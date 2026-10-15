#!/usr/bin/env python3
"""devfeed — one page for what shipped and what's waiting, across all your GitHub repos.

Two commands, standard library only, GitHub access through the gh CLI:

    python3 devfeed.py collect   # fetch once and write public/feed.json
    python3 devfeed.py serve     # serve the page and re-collect every few minutes
    python3 devfeed.py demo      # serve made-up data, no GitHub needed

Which repos to watch is discovered, not configured: every non-archived,
non-fork repo of each owner pushed within `active_days`, plus any `pin`ned in
config.json (which otherwise only holds display names, colour slots and
`hide`s). Per repo it fetches recent commits and merged PRs, open PRs (with
check and review state) and branches with recent commits but no PR yet, and
optionally one GitHub Projects board (which column each card is in, and since
when).

feed.json is regenerated wholesale each run, so there is no state to corrupt.
If any fetch fails the previous feed.json is left untouched rather than
overwritten with a partial view; the page shows "updated Xm ago" and turns it
red when it goes stale, so a broken collector is visible on the page itself.
"""

import argparse
import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
OUT_PATH = os.path.join(PUBLIC, "feed.json")

DEFAULTS = {
    "bind": "127.0.0.1",
    "port": 8300,
    "interval_minutes": 5,
    "days": 30,
    "spark_days": 14,
    "active_days": 14,
    "hide": [],
    "ignore_branches": [],
    "board": None,
    "repos": [],
}

MAX_ITEMS_PER_REPO = 30
SLOTS = 8                 # colour slots in index.html
BRANCH_DAYS = 7           # a branch with no commit for this long is not "in progress"
BOARD_DONE_DAYS = 7       # Done cards older than this are history, not context


def log(msg):
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{ts}] devfeed: {msg}", file=sys.stderr, flush=True)


def load_config(path):
    with open(path) as f:
        cfg = {**DEFAULTS, **json.load(f)}
    owners = cfg.get("owners") or cfg.get("owner")
    if not owners:
        sys.exit(f"{path}: set \"owner\" (a GitHub user or org) or \"owners\" (a list)")
    cfg["owners"] = [owners] if isinstance(owners, str) else list(owners)
    return cfg


def gh_bin():
    found = shutil.which("gh") or next(
        (p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(p)), None)
    if not found:
        sys.exit("devfeed needs the GitHub CLI: https://cli.github.com, then `gh auth login`")
    return found


def gh(*args):
    r = subprocess.run([gh_bin(), *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def utc_cutoff(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_repo(repo, since):
    commits = gh("api", f"repos/{repo}/commits?per_page=60")
    pulls = gh("api", f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=20")

    merges = []
    merged_prs = set()
    merged_titles = set()
    for pr in pulls:
        if not pr.get("merged_at"):
            continue
        ts = pr["merged_at"]
        if ts < since:
            continue
        merged_prs.add(pr["number"])
        merged_titles.add(pr["title"].strip())
        merges.append({
            "type": "merge",
            "title": pr["title"],
            "ref": f"#{pr['number']}",
            "ts": ts,
            "url": pr["html_url"],
        })

    pushes = []
    commit_ts = []
    for c in commits:
        ts = (c.get("commit", {}).get("committer") or {}).get("date", "")
        if not ts or ts < since:
            continue
        commit_ts.append(ts)
        msg = c["commit"]["message"].split("\n")[0]
        # Three shapes duplicate a PR's own merge entry: the merge commit
        # ("Merge pull request #N ..."), a squash commit ("... (#N)"), and —
        # for merge-commit PRs whose title is the branch commit's subject —
        # the branch commit itself. Skip all three from the item list; they
        # still count toward the sparkline via commit_ts.
        if msg.strip() in merged_titles:
            continue
        n = None
        if msg.startswith("Merge pull request #"):
            try:
                n = int(msg.split("#")[1].split()[0])
            except (IndexError, ValueError):
                pass
        elif msg.endswith(")") and "(#" in msg:
            try:
                n = int(msg.rsplit("(#", 1)[1][:-1])
            except ValueError:
                pass
        if n in merged_prs:
            continue
        pushes.append({
            "type": "push",
            "title": msg,
            "ref": c["sha"][:7],
            "ts": ts,
            "url": c["html_url"],
        })

    items = sorted(merges + pushes, key=lambda i: i["ts"], reverse=True)
    return items[:MAX_ITEMS_PER_REPO], commit_ts


OPEN_WORK_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequests(states: OPEN, first: 20, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title url isDraft createdAt updatedAt headRefName reviewDecision
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      }
    }
    refs(refPrefix: "refs/heads/", first: 20,
         orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
      nodes {
        name
        associatedPullRequests(first: 1) { totalCount }
        target { ... on Commit { committedDate messageHeadline url } }
      }
    }
  }
}
"""


def fetch_open_work(repo, ignored_branches):
    """Open PRs (what's waiting for review) and branches with recent commits
    but no PR yet (what's being built). Merged work is fetch_repo's job."""
    owner, name = repo.split("/")
    data = gh("api", "graphql", "-f", f"query={OPEN_WORK_QUERY}",
              "-F", f"owner={owner}", "-F", f"name={name}")["data"]["repository"]

    prs = []
    for pr in data["pullRequests"]["nodes"]:
        last = pr["commits"]["nodes"]
        rollup = last[0]["commit"]["statusCheckRollup"] if last else None
        prs.append({
            "title": pr["title"],
            "ref": f"#{pr['number']}",
            "url": pr["url"],
            "branch": pr["headRefName"],
            "draft": pr["isDraft"],
            "opened": pr["createdAt"],
            "ts": pr["updatedAt"],
            "review": pr["reviewDecision"],          # APPROVED / CHANGES_REQUESTED / None
            "checks": rollup["state"] if rollup else None,  # SUCCESS / FAILURE / PENDING / None
        })

    default = (data["defaultBranchRef"] or {}).get("name")
    cutoff = utc_cutoff(BRANCH_DAYS)
    # A branch with any PR is covered elsewhere: open ones are in prs, and
    # merged/closed ones are done even if nobody deleted the branch.
    branches = []
    for ref in data["refs"]["nodes"]:
        c = ref["target"] or {}
        ts = c.get("committedDate")
        if (ref["name"] == default or ref["name"] in ignored_branches
                or ref["associatedPullRequests"]["totalCount"] or not ts or ts < cutoff):
            continue
        branches.append({
            "title": c["messageHeadline"],
            "branch": ref["name"],
            "ts": ts,
            "url": f"https://github.com/{repo}/tree/{ref['name']}",
        })
    return prs, branches


BOARD_QUERY = """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        title url
        field(name: "Status") {
          ... on ProjectV2SingleSelectField { options { name } }
        }
        items(first: 100) {
          nodes {
            status: fieldValueByName(name: "Status") {
              ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt }
            }
            content {
              ... on Issue {
                number title url state
                repository { nameWithOwner }
                labels(first: 20) { nodes { name } }
              }
            }
          }
        }
      }
    }
  }
}
"""


def fetch_board(board):
    """A GitHub Projects board: every card not Done, plus cards Done in the last
    week. `since` is when the card moved into its current column, which is what
    "moved since you last looked" and "stuck in progress" are measured from."""
    data = gh("api", "graphql", "-f", f"query={BOARD_QUERY}",
              "-F", f"owner={board['owner']}", "-F", f"number={board['number']}")
    project = (data["data"]["repositoryOwner"] or {}).get("projectV2")
    if not project:
        raise RuntimeError(f"board {board['owner']}#{board['number']} not found "
                           "(gh needs the read:project scope: gh auth refresh -s read:project)")
    done = board.get("done_column", "Done")
    attention = board.get("attention_label", "needs-human")
    columns = [o["name"] for o in (project.get("field") or {}).get("options", [])
               if o["name"] != done]
    cutoff = utc_cutoff(BOARD_DONE_DAYS)
    cards = []
    for item in project["items"]["nodes"]:
        c, st = item["content"], item["status"]
        if not c or not c.get("number"):      # draft notes and PR cards: not tracked here
            continue
        status = st["name"] if st else "No status"
        since = st["updatedAt"] if st else None
        if status == done and (since or "") < cutoff:
            continue
        labels = [l["name"] for l in c["labels"]["nodes"]]
        cards.append({
            "title": c["title"].strip(),
            "url": c["url"],
            "repo": c["repository"]["nameWithOwner"],
            "ref": f"#{c['number']}",
            "status": "Done" if status == done else status,
            "since": since,
            "closed": c["state"] != "OPEN",
            "needs_human": attention in labels,
            "size": next((l[5:] for l in labels if l.startswith("size:")), None),
        })
    return {
        "title": board.get("title") or project["title"],
        "url": project["url"],
        "columns": columns,
        "active_columns": board.get("active_columns", columns[-2:]),
        "cards": cards,
    }


def discover(cfg):
    """Repos to show: recently pushed ones plus pins, minus hides, most recent first."""
    overrides = {r["repo"]: r for r in cfg["repos"]}
    hidden = set(cfg["hide"])
    cutoff = utc_cutoff(cfg["active_days"])
    pushed = {}
    for owner in cfg["owners"]:
        for r in gh("repo", "list", owner, "--limit", "200", "--no-archived", "--source",
                    "--json", "nameWithOwner,pushedAt"):
            pushed[r["nameWithOwner"]] = r["pushedAt"]
    names = {n for n, ts in pushed.items() if ts and ts >= cutoff}
    names |= {n for n, o in overrides.items() if o.get("pin")}
    names -= hidden
    return [
        {
            "repo": n,
            "display": overrides.get(n, {}).get("display") or n.split("/")[1],
            # Pinned slot if configured, else derived from the name, so a repo
            # keeps its colour however the list reorders.
            "slot": overrides.get(n, {}).get("slot") or zlib.crc32(n.encode()) % SLOTS + 1,
        }
        for n in sorted(names, key=lambda n: pushed.get(n) or "", reverse=True)
    ]


def daily_counts(commit_ts, days):
    """Commit counts per local day for the sparkline, oldest first."""
    today = datetime.now().astimezone().date()
    buckets = {today - timedelta(days=n): 0 for n in range(days)}
    for ts in commit_ts:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
        if d in buckets:
            buckets[d] += 1
    return [buckets[d] for d in sorted(buckets)]


def collect(cfg):
    since = utc_cutoff(cfg["days"])
    ignored = set(cfg["ignore_branches"])

    repos_out = []
    for r in discover(cfg):
        items, commit_ts = fetch_repo(r["repo"], since)
        open_prs, branches = fetch_open_work(r["repo"], ignored)
        repos_out.append({
            "repo": r["repo"],
            "display": r["display"],
            "slot": r["slot"],
            "last_activity": max([i["ts"] for i in items[:1] + open_prs + branches],
                                 default=None),
            "spark": daily_counts(commit_ts, cfg["spark_days"]),
            "items": items,
            "open_prs": open_prs,
            "branches": branches,
        })

    feed = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval_minutes": cfg["interval_minutes"],
        "spark_days": cfg["spark_days"],
        "days": cfg["days"],
        "repos": repos_out,
        "board": fetch_board(cfg["board"]) if cfg.get("board") else None,
    }
    os.makedirs(PUBLIC, exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(feed, f)
    os.replace(tmp, OUT_PATH)

    total = sum(len(r["items"]) for r in repos_out)
    prs = sum(len(r["open_prs"]) for r in repos_out)
    log(f"feed regenerated ({len(repos_out)} repos, {total} items, {prs} open PRs)")


def collect_safely(cfg):
    try:
        collect(cfg)
        return True
    except Exception as e:
        log(f"ERROR: {e}")
        return False


def demo_feed():
    """A made-up feed with timestamps relative to now, for trying the page
    (and taking screenshots) without a GitHub account."""
    now = datetime.now(timezone.utc)

    def ago(hours):
        return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def item(kind, title, ref, hours, repo):
        url = f"https://github.com/{repo}/" + ("pull/" + ref[1:] if kind == "merge" else "commit/" + ref)
        return {"type": kind, "title": title, "ref": ref, "ts": ago(hours), "url": url}

    projects = [
        ("acme/storefront", "Storefront", 1, [
            ("merge", "Checkout: remember the last shipping address", "#142", 2),
            ("push", "Tighten the cart drawer on small screens", "a1c9e02", 5),
            ("merge", "Stripe webhooks retry with backoff", "#139", 26),
            ("push", "Seed data for the holiday collection", "77de1b4", 50),
        ], [("Product reviews with photos", "#145", 30, "PENDING", None)],
           [("Wishlist sharing links", "feat/wishlist-share", 8)]),
        ("acme/mobile", "Mobile app", 3, [
            ("merge", "Offline mode for saved lists", "#58", 4),
            ("push", "Bump Expo SDK", "3f0a9c1", 28),
            ("merge", "Push notification opt-in screen", "#55", 74),
        ], [("Dark mode for settings", "#60", 200, "FAILURE", None)], []),
        ("acme/api", "API", 4, [
            ("merge", "Rate limit the search endpoint", "#311", 20),
            ("push", "Log slow queries over 200 ms", "c45e7d0", 30),
            ("push", "Drop the legacy v1 auth route", "9b1d3aa", 98),
        ], [("Postgres 17 upgrade", "#314", 6, "SUCCESS", "APPROVED")], []),
        ("acme/docs", "Docs", 5, [
            ("push", "Quickstart rewrite", "e0d2b77", 120),
        ], [], []),
    ]
    repos = []
    for repo, display, slot, items, prs, branches in projects:
        its = [item(k, t, r, h, repo) for k, t, r, h in items]
        open_prs = [{"title": t, "ref": r, "url": f"https://github.com/{repo}/pull/{r[1:]}",
                     "branch": "feat/" + r[1:], "draft": False, "opened": ago(h), "ts": ago(h / 2),
                     "review": rv, "checks": ck} for t, r, h, ck, rv in prs]
        brs = [{"title": t, "branch": b, "ts": ago(h),
                "url": f"https://github.com/{repo}/tree/{b}"} for t, b, h in branches]
        spark = [zlib.crc32(f"{repo}{d}".encode()) % 5 for d in range(14)]
        repos.append({"repo": repo, "display": display, "slot": slot,
                      "last_activity": its[0]["ts"], "spark": spark, "items": its,
                      "open_prs": open_prs, "branches": brs})
    cards = [
        ("Gift cards at checkout", "acme/storefront", "#150", "In Progress", 90, False, "m"),
        ("Product reviews with photos", "acme/storefront", "#144", "Review", 30, False, "l"),
        ("Decide on a search provider", "acme/api", "#309", "Ready", 40, True, "s"),
        ("Widget for the home screen", "acme/mobile", "#61", "Inbox", 200, False, None),
        ("Rate limit the search endpoint", "acme/api", "#305", "Done", 20, False, "s"),
    ]
    board = {"title": "Roadmap", "url": "https://github.com/orgs/acme/projects/1",
             "columns": ["Inbox", "Ready", "In Progress", "Review"],
             "active_columns": ["In Progress", "Review"],
             "cards": [{"title": t, "url": f"https://github.com/{r}/issues/{n[1:]}", "repo": r,
                        "ref": n, "status": st, "since": ago(h), "closed": st == "Done",
                        "needs_human": nh, "size": sz} for t, r, n, st, h, nh, sz in cards]}
    return {"generated_at": ago(0), "interval_minutes": 5, "spark_days": 14, "days": 30,
            "repos": repos, "board": board}


class Server(http.server.ThreadingHTTPServer):
    """HTTPServer.server_bind does a reverse-DNS lookup of the bind address,
    which can hang for a long time when DNS is slow. Nothing here needs it."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Static files, but never cached, so a changed page shows up on the next load."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


def serve(cfg, collecting=True):
    def loop():
        while True:
            collect_safely(cfg)
            time.sleep(cfg["interval_minutes"] * 60)

    if collecting:
        threading.Thread(target=loop, daemon=True).start()
    handler = functools.partial(NoCacheHandler, directory=PUBLIC)
    server = Server((cfg["bind"], cfg["port"]), handler)
    log(f"serving http://{cfg['bind']}:{cfg['port']} " + (
        f"(collecting every {cfg['interval_minutes']} min)" if collecting else "(demo data)"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["collect", "serve", "demo"])
    p.add_argument("--config", default=os.environ.get("DEVFEED_CONFIG")
                   or os.path.join(ROOT, "config.json"),
                   help="path to config.json (default: next to this script)")
    p.add_argument("--port", type=int, help="override the port from config.json")
    args = p.parse_args()
    if args.command == "demo":
        os.makedirs(PUBLIC, exist_ok=True)
        with open(OUT_PATH, "w") as f:
            json.dump(demo_feed(), f)
        serve({**DEFAULTS, "port": args.port or DEFAULTS["port"]}, collecting=False)
        return
    if not os.path.exists(args.config):
        sys.exit(f"No config at {args.config}. Start from config.example.json.")
    cfg = load_config(args.config)
    if args.port:
        cfg["port"] = args.port
    if args.command == "collect":
        sys.exit(0 if collect_safely(cfg) else 1)
    serve(cfg)


if __name__ == "__main__":
    main()

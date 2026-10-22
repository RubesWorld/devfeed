#!/usr/bin/env python3
"""devfeed — one page for what shipped and what's waiting, across all your GitHub repos.

A few commands, standard library only, GitHub access through the gh CLI:

    python3 devfeed.py collect   # fetch once and write public/feed.json
    python3 devfeed.py serve     # serve the page and re-collect every few minutes
    python3 devfeed.py demo      # serve made-up data, no GitHub needed
    python3 devfeed.py digest    # collect once and email a morning digest
                                 # (--dry-run prints it, --demo renders made-up data)

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
import re
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
MAX_BOARD_CARDS = 500     # pages of 100 fetched until this many cards
SIZE_ORDER = ["xs", "s", "m", "l", "xl"]


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
query($owner: String!, $number: Int!, $after: String) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        title url
        field(name: "Status") {
          ... on ProjectV2SingleSelectField { options { name } }
        }
        items(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            status: fieldValueByName(name: "Status") {
              ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt }
            }
            content {
              ... on Issue {
                number title url state body
                repository { nameWithOwner }
                labels(first: 20) { nodes { name color } }
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
    nodes, after = [], None
    while True:
        args = ["api", "graphql", "-f", f"query={BOARD_QUERY}",
                "-F", f"owner={board['owner']}", "-F", f"number={board['number']}"]
        if after:
            args += ["-f", f"after={after}"]
        data = gh(*args)
        project = (data["data"]["repositoryOwner"] or {}).get("projectV2")
        if not project:
            raise RuntimeError(f"board {board['owner']}#{board['number']} not found "
                               "(gh needs the read:project scope: gh auth refresh -s read:project)")
        page = project["items"]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"] or len(nodes) >= MAX_BOARD_CARDS:
            break
        after = page["pageInfo"]["endCursor"]
    nu = board.get("next_up") or {}
    done = board.get("done_column", "Done")
    attention = board.get("attention_label", "needs-human")
    hidden_labels = set(board.get("hide_labels", []))
    columns = [o["name"] for o in (project.get("field") or {}).get("options", [])
               if o["name"] != done]
    cutoff = utc_cutoff(BOARD_DONE_DAYS)
    cards = []
    for item in nodes:
        c, st = item["content"], item["status"]
        if not c or not c.get("number"):      # draft notes and PR cards: not tracked here
            continue
        status = st["name"] if st else "No status"
        since = st["updatedAt"] if st else None
        if status == done and (since or "") < cutoff:
            continue
        labels = c["labels"]["nodes"]
        repo = c["repository"]["nameWithOwner"]
        body = c.get("body") or ""
        touches = parse_paths(section(body, nu.get("touches_heading", "Touches")))
        listed = bool(touches)
        touches = touches or mentioned_paths(body)
        cards.append({
            "title": c["title"].strip(),
            "url": c["url"],
            "repo": c["repository"]["nameWithOwner"],
            "ref": f"#{c['number']}",
            "status": "Done" if status == done else status,
            "since": since,
            "closed": c["state"] != "OPEN",
            "needs_human": any(l["name"] == attention for l in labels),
            # The card's own labels, in GitHub's colours. The attention label
            # is shown as "needs you" instead, so it isn't repeated here.
            "labels": [{"name": l["name"], "color": l["color"]} for l in labels
                       if l["name"] != attention and l["name"] not in hidden_labels],
            # From the issue body, for next_up(): which cards this one builds on,
            # and which files it changes.
            "depends_on": parse_refs(section(body, nu.get("depends_heading", "Depends on")), repo),
            "touches": touches,
            "touches_listed": listed,
            "held": any(l["name"] == attention or held_label(l["name"], nu) for l in labels),
            "size": next((l["name"][len(nu.get("size_prefix", "size:")):] for l in labels
                          if l["name"].startswith(nu.get("size_prefix", "size:"))), None),
        })
    return {
        "title": board.get("title") or project["title"],
        "url": project["url"],
        "columns": columns,
        "active_columns": board.get("active_columns", columns[-2:]),
        "count_only_columns": board.get("count_only_columns", []),
        "cards": cards,
    }


# ------------------------------------------------------------------ next up
# Feature priority is the owner's call (the order of the board's columns says
# it). What the board doesn't show is structure: which card others stand on,
# and which cards will collide. next_up() answers "what should go next so the
# rest gets easier", per repo, from two optional sections in each issue body:
#
#   ## Depends on            ## Touches
#   - #12: needs its table   - `src/db/schema.ts`
#                            - `src/app/connect/`

def section(body, heading):
    """The lines under `## <heading>` in a markdown body, up to the next heading."""
    m = re.search(r"^#{2,3}\s*" + re.escape(heading) + r"\s*$(.*?)(?=^#{1,3}\s|\Z)",
                  body, re.M | re.S | re.I)
    return m.group(1) if m else ""


def parse_refs(text, repo):
    """`#12`, `owner/name#12` -> ["owner/name#12", ...], in order, no duplicates."""
    refs = []
    for owner_repo, n in re.findall(r"(?:([\w.-]+/[\w.-]+))?#(\d+)", text):
        ref = f"{owner_repo or repo}#{n}"
        if ref not in refs:
            refs.append(ref)
    return refs


def parse_paths(text):
    """One path per bullet: the first `backticked` span, else the first word."""
    paths = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        line = line[1:].strip()
        m = re.search(r"`([^`]+)`", line)
        p = (m.group(1) if m else (line.split() or [""])[0]).strip().lstrip("./")
        if p and p.lower() != "none" and ("/" in p or "." in p) and p not in paths:
            paths.append(p)
    return paths


def mentioned_paths(body):
    """Fallback for issues without a Touches section: `backticked` spans that
    look like repo paths (have a slash), minus URLs and :line suffixes."""
    paths = []
    for span in re.findall(r"`([^`\s]+)`", body):
        p = re.sub(r":\d+(-\d+)?$", "", span).lstrip("./")
        if ("/" in p and "://" not in span and re.fullmatch(r"[\w.\-/\[\]]+", p)
                and p not in paths):
            paths.append(p)
    return paths


def held_label(name, nu):
    """hold_labels entries ending in ':' match as a prefix (`risk:` holds `risk:auth`)."""
    return any(name.startswith(h) if h.endswith(":") else name == h
               for h in nu.get("hold_labels", []))


def overlaps(a, b, nested):
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or (nested and (a.startswith(b + "/") or b.startswith(a + "/")))


def shared_paths(x, y):
    """Paths both cards change. A directory covers the files under it only when
    both cards list it under Touches; paths guessed from the body (a passing
    mention of `src/app/`) only count when they're the same file."""
    nested = x.get("touches_listed") and y.get("touches_listed")
    return [p for p in x["touches"] if any(overlaps(p, q, nested) for q in y["touches"])]


def next_up(board, cfg_board):
    """Per repo: the card to build next and why, cards waiting on unfinished
    work, held cards that others wait on, and pairs that shouldn't be built at
    the same time because they change the same files."""
    nu = cfg_board.get("next_up") or {}
    columns, active = board["columns"], board["active_columns"]
    first_active = min((columns.index(c) for c in active if c in columns), default=len(columns))
    candidates_cols = nu.get("columns") or columns[max(first_active - 1, 0):first_active]
    key = lambda c: c["repo"] + c["ref"]
    open_cards = {key(c): c for c in board["cards"] if c["status"] != "Done" and not c["closed"]}

    dependents = {}
    for c in open_cards.values():
        for d in c["depends_on"]:
            dependents.setdefault(d, []).append(key(c))

    def unblocks(k):
        seen, todo = [], list(dependents.get(k, []))
        while todo:
            n = todo.pop(0)
            if n not in seen and n != k:
                seen.append(n)
                todo += dependents.get(n, [])
        return seen

    def short(k, repo):
        return k[len(repo):] if k.startswith(repo + "#") else k

    def brief(c):
        return {"ref": c["ref"], "title": c["title"], "url": c["url"], "status": c["status"]}

    out = []
    for repo in sorted({c["repo"] for c in open_cards.values()}):
        cards = [c for c in open_cards.values() if c["repo"] == repo]
        cands = [c for c in cards if c["status"] in candidates_cols]
        ready, waiting, held = [], [], []
        for c in cands:
            blockers = [d for d in c["depends_on"] if d in open_cards]
            row = dict(brief(c), unblocks=[short(k, repo) for k in unblocks(key(c))],
                       shares=[], blocked_by=[short(d, repo) for d in blockers])
            for other in cards:
                if other is not c and shared_paths(c, other):
                    row["shares"].append({"ref": short(key(other), repo),
                                          "paths": shared_paths(c, other)})
            size = c.get("size")
            row["_rank"] = (-len(row["unblocks"]), -len(row["shares"]),
                            SIZE_ORDER.index(size) if size in SIZE_ORDER else 2,
                            -columns.index(c["status"]), c.get("since") or "")
            (waiting if blockers else held if c["held"] else ready).append(row)
        for rows in (ready, held):
            rows.sort(key=lambda r: r["_rank"])

        # Pairs in candidate or active columns changing the same files, unless
        # one already builds on the other (then the order is decided).
        busy = [c for c in cards if c["status"] in candidates_cols or c["status"] in active]
        conflicts = []
        for i, x in enumerate(busy):
            for y in busy[i + 1:]:
                if key(y) in x["depends_on"] or key(x) in y["depends_on"]:
                    continue
                paths = shared_paths(x, y)
                if paths:
                    conflicts.append({"a": brief(x), "b": brief(y), "paths": paths})

        top = ready[0]["ref"] if ready else None
        conflicts.sort(key=lambda p: top not in (p["a"]["ref"], p["b"]["ref"]))
        for r in ready + held + waiting:
            r.pop("_rank", None)
        if ready or waiting or conflicts or any(h["unblocks"] for h in held):
            out.append({
                "repo": repo,
                "build_next": ready[0] if ready else None,
                "then": [{"ref": r["ref"], "title": r["title"]} for r in ready[1:3]],
                "needs_you": [h for h in held if h["unblocks"]][:2],
                "waiting": waiting[:3],
                "conflicts": conflicts[:3],
            })
    return out


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
    if feed["board"]:
        feed["next_up"] = next_up(feed["board"], cfg["board"])
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
            ("push", "Quickstart rewrite", "e0d2b77", 240),
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
    feature, bug, grey = ("feature", "a2eeef"), ("bug", "d73a4a"), "ededed"
    # (depends on, touches) per card, for Next up.
    structure = {
        "#150": ([], ["src/checkout/"]),
        "#151": ([], ["src/checkout/", "src/db/schema.ts"]),
        "#152": (["acme/storefront#151"], ["src/orders/"]),
        "#153": (["acme/storefront#151"], ["src/account/credit.tsx"]),
        "#62": ([], ["src/lists/"]),
        "#61": (["acme/mobile#62"], ["src/widgets/"]),
    }
    cards = [
        ("Gift cards at checkout", "acme/storefront", "#150", "In Progress", 90, False,
         [feature, ("size:m", grey), ("payments", "fbca04")]),
        ("Saved payment methods", "acme/storefront", "#151", "Ready", 20, False,
         [feature, ("size:s", grey), ("payments", "fbca04")]),
        ("One-click reorder", "acme/storefront", "#152", "Inbox", 60, False, [feature]),
        ("Pay with store credit", "acme/storefront", "#153", "Inbox", 70, False, [feature]),
        ("Product reviews with photos", "acme/storefront", "#144", "Review", 30, False,
         [feature, ("size:l", grey)]),
        ("Decide on a search provider", "acme/api", "#309", "Ready", 40, True,
         [("question", "d876e3")]),
        ("Crash when a saved list is empty", "acme/mobile", "#62", "Ready", 10, False,
         [bug, ("priority:high", "b60205")]),
        ("Widget for the home screen", "acme/mobile", "#61", "Inbox", 200, False,
         [feature, ("design", "0e8a16"), ("ios", "5319e7"), ("android", "1d76db")]),
        ("Rate limit the search endpoint", "acme/api", "#305", "Done", 20, False, [feature]),
    ]
    board = {"title": "Roadmap", "url": "https://github.com/orgs/acme/projects/1",
             "columns": ["Inbox", "Ready", "In Progress", "Review"],
             "active_columns": ["In Progress", "Review"],
             "cards": [{"title": t, "url": f"https://github.com/{r}/issues/{n[1:]}", "repo": r,
                        "ref": n, "status": st, "since": ago(h), "closed": st == "Done",
                        "needs_human": nh,
                        "labels": [{"name": ln, "color": lc} for ln, lc in labels],
                        "depends_on": structure.get(n, ([], []))[0],
                        "touches": structure.get(n, ([], []))[1],
                        "touches_listed": n in structure, "held": nh,
                        "size": next((ln[5:] for ln, _ in labels if ln.startswith("size:")), None)}
                       for t, r, n, st, h, nh, labels in cards]}
    return {"generated_at": ago(0), "interval_minutes": 5, "spark_days": 14, "days": 30,
            "repos": repos, "board": board, "next_up": next_up(board, {}), "demo": True}


DIGEST_TITLE = 80         # characters before a title is cut with …
DIGEST_WAITING = 5        # waiting items shown per repo
DIGEST_AWAY = 10          # "while you were away" items shown per repo
DIGEST_REPOS = 15


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_time(dt):
    return dt.astimezone().strftime("%a %d %b %H:%M")


def pr_verb(p):
    if p.get("review") == "CHANGES_REQUESTED" or p.get("checks") == "FAILURE":
        return "fix"
    if p.get("review") == "APPROVED" and p.get("checks") == "SUCCESS":
        return "merge"
    return "review"


def render_digest(feed, since, now, active_days=7, stale_hours=None):
    """The digest email as (subject, body), from a feed and the window start.
    Pure: no I/O, so the caller decides the window, the clock and staleness."""
    end = parse_ts(feed["generated_at"])
    active_cutoff = now - timedelta(days=active_days)
    board = feed.get("board") or {}
    build_next = {n["repo"]: n["build_next"] for n in feed.get("next_up") or [] if n.get("build_next")}

    def cut(title):
        return title if len(title) <= DIGEST_TITLE else title[:DIGEST_TITLE - 1] + "…"

    def entry(text, url):
        return [f"  - {text}", f"    {url}"]

    def in_window(s):
        return bool(s) and since < parse_ts(s) <= end

    # Cards can name repos the feed doesn't list (no recent pushes), so
    # everything is keyed by repo name, with the feed's entry when there is one.
    repos = {r["repo"]: r for r in feed["repos"]}
    blocks = {}

    def block(name):
        if name not in blocks:
            r = repos.get(name) or {"repo": name, "display": name.split("/")[-1],
                                    "last_activity": None, "items": [], "open_prs": [],
                                    "branches": []}
            blocks[name] = {"repo": r, "waiting": [], "away": []}
        return blocks[name]

    counts = {"merge": 0, "pr": 0, "move": 0}
    for r in feed["repos"]:
        b = block(r["repo"])
        for p in r["open_prs"]:
            if not p["draft"]:
                b["waiting"].append((f"{pr_verb(p)} {p['ref']} {cut(p['title'])}", p["url"]))
        for i in r["items"]:
            if i["type"] == "merge" and in_window(i["ts"]):
                b["away"].append((i["ts"], f"merged {i['ref']} {cut(i['title'])}", i["url"]))
                counts["merge"] += 1
        for p in r["open_prs"]:
            if in_window(p.get("opened")):
                b["away"].append((p["opened"], f"new PR {p['ref']} {cut(p['title'])}", p["url"]))
                counts["pr"] += 1
    for c in board.get("cards") or []:
        if c.get("needs_human") and c["status"] != "Done" and not c["closed"]:
            block(c["repo"])["waiting"].append((f"{c['ref']} {cut(c['title'])}", c["url"]))
        if in_window(c.get("since")):
            block(c["repo"])["away"].append((c["since"], f"{c['ref']} → {c['status']}", c["url"]))
            counts["move"] += 1

    waiting_total = sum(len(b["waiting"]) for b in blocks.values())
    day = now.astimezone().strftime("%a %d %b")
    subject = f"devfeed: {waiting_total} need you · {day}" if waiting_total \
        else f"devfeed: all clear · {day}"
    lines = [f"Couldn't refresh from GitHub; this is {stale_hours}h old."] \
        if stale_hours is not None else []
    since_s = local_time(since)

    if not waiting_total:
        if any(counts.values()):
            parts = [f"{counts['merge']} merge{'s' * (counts['merge'] != 1)}",
                     f"{counts['pr']} new PR{'s' * (counts['pr'] != 1)}",
                     f"{counts['move']} board move{'s' * (counts['move'] != 1)}"]
            lines.append(f"Nothing needs you. Since {since_s}: {', '.join(parts)}.")
        else:
            lines.append(f"Nothing needs you, and nothing changed since {since_s}.")
        return subject, "\n".join(lines) + "\n"

    def recent(b):
        return b["repo"]["last_activity"] or ""

    active = [b for b in blocks.values() if b["waiting"]
              or (b["repo"]["last_activity"] and parse_ts(b["repo"]["last_activity"]) >= active_cutoff)]
    active.sort(key=recent, reverse=True)
    active.sort(key=lambda b: not b["waiting"])
    if lines:
        lines.append("")
    for b in active[:DIGEST_REPOS]:
        r = b["repo"]
        lines.append(f"{r['display']} ({r['repo']})")
        last = (r["branches"][:1] and r["branches"][0]) or (r["items"][:1] and r["items"][0])
        if last:
            where = last.get("branch") or last["ref"]
            lines += [f"  Left off: {cut(last['title'])} ({where})", f"    {last['url']}"]
        nxt = next((p for p in r["open_prs"] if not p["draft"]), None)
        if nxt:
            lines += [f"  Next: {pr_verb(nxt)} {nxt['ref']} {cut(nxt['title'])}",
                      f"    {nxt['url']}"]
        elif r["repo"] in build_next:
            bn = build_next[r["repo"]]
            lines += [f"  Next: build {bn['ref']} {cut(bn['title'])}", f"    {bn['url']}"]
        if b["waiting"]:
            lines.append("  Waiting on you:")
            for text, url in b["waiting"][:DIGEST_WAITING]:
                lines += entry(text, url)
            if len(b["waiting"]) > DIGEST_WAITING:
                lines.append(f"  +{len(b['waiting']) - DIGEST_WAITING} more")
        lines.append("")
    if len(active) > DIGEST_REPOS:
        lines += [f"+{len(active) - DIGEST_REPOS} more repos", ""]

    lines.append(f"While you were away (since {since_s})")
    away = [b for b in blocks.values() if b["away"]]
    away.sort(key=lambda b: max(a[0] for a in b["away"]), reverse=True)
    for b in away:
        lines.append(b["repo"]["display"])
        events = sorted(b["away"], reverse=True)
        for _, text, url in events[:DIGEST_AWAY]:
            lines += entry(text, url)
        if len(events) > DIGEST_AWAY:
            lines.append(f"  +{len(events) - DIGEST_AWAY} more")
    if not away:
        lines.append("  Nothing changed.")
    return subject, "\n".join(lines) + "\n"


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
    p.add_argument("command", choices=["collect", "serve", "demo", "digest"])
    p.add_argument("--config", default=os.environ.get("DEVFEED_CONFIG")
                   or os.path.join(ROOT, "config.json"),
                   help="path to config.json (default: next to this script)")
    p.add_argument("--port", type=int, help="override the port from config.json")
    p.add_argument("--dry-run", action="store_true", help="digest: print the email, don't send it")
    p.add_argument("--demo", action="store_true", help="digest: render made-up data, no GitHub")
    args = p.parse_args()
    if args.command == "digest" and args.demo:
        f = demo_feed()
        end = parse_ts(f["generated_at"])
        subject, body = render_digest(f, end - timedelta(hours=24), end)
        print(f"Subject: {subject}\n\n{body}", end="")
        return
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

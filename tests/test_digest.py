"""Morning digest: rendering, window, state and sending. Run: python3 -m unittest discover tests"""
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402

# Display times are local; pin the zone so assertions don't depend on the machine.
os.environ["TZ"] = "UTC"
time.tzset()

R = "me/app"
NOW = datetime(2026, 10, 20, 7, 30, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=24)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(n, hours=10, review=None, checks=None, draft=False, title=None):
    return {"title": title or f"pr {n}", "ref": f"#{n}", "url": f"https://github.com/{R}/pull/{n}",
            "branch": f"b{n}", "draft": draft, "opened": ts(hours), "ts": ts(hours),
            "review": review, "checks": checks}


def item(kind, ref, hours, title=None):
    path = "pull/" + ref[1:] if kind == "merge" else "commit/" + ref
    return {"type": kind, "title": title or f"{kind} {ref}", "ref": ref, "ts": ts(hours),
            "url": f"https://github.com/{R}/{path}"}


def repo(name=R, display="App", hours=5, items=None, prs=(), branches=()):
    items = [item("push", "abc1234", hours)] if items is None else items
    return {"repo": name, "display": display, "slot": 1,
            "last_activity": ts(hours) if hours is not None else None,
            "spark": [], "items": items, "open_prs": list(prs), "branches": list(branches)}


def card(n, status="Ready", hours=100, needs_human=False, repo_name=R):
    return {"title": f"card {n}", "url": f"https://github.com/{repo_name}/issues/{n}",
            "repo": repo_name, "ref": f"#{n}", "status": status, "since": ts(hours),
            "closed": status == "Done", "needs_human": needs_human}


def feed(repos, cards=None, next_up=None):
    f = {"generated_at": ts(0), "repos": repos,
         "board": {"cards": cards} if cards is not None else None}
    if next_up is not None:
        f["next_up"] = next_up
    return f


def render(f, since=SINCE, **kw):
    return devfeed.render_digest(f, since, NOW, **kw)


class Demo(unittest.TestCase):
    def setUp(self):
        f = devfeed.demo_feed()
        end = devfeed.parse_ts(f["generated_at"])
        self.subject, self.body = devfeed.render_digest(f, end - timedelta(hours=24), end)

    def test_verify_step_one(self):
        self.assertTrue(self.subject.startswith("devfeed: 4 need you · "))
        self.assertIn("Left off: Wishlist sharing links (feat/wishlist-share)", self.body)
        self.assertIn("Next: review #145", self.body)
        self.assertIn("Next: merge #314", self.body)
        self.assertIn("Next: fix #60", self.body)
        self.assertIn("#309 Decide on a search provider", self.body)
        self.assertNotIn("Docs", self.body)
        self.assertIn("While you were away (since ", self.body)

    def test_waiting_repos_first(self):
        api = self.body.index("API (acme/api)")
        self.assertLess(self.body.index("Storefront (acme/storefront)"), self.body.index("While you"))
        self.assertLess(api, self.body.index("While you"))


class Render(unittest.TestCase):
    def test_verbs(self):
        cases = [(dict(review="CHANGES_REQUESTED", checks="SUCCESS"), "fix"),
                 (dict(checks="FAILURE"), "fix"),
                 (dict(review="APPROVED", checks="SUCCESS"), "merge"),
                 (dict(review="APPROVED", checks="PENDING"), "review"),
                 (dict(review="APPROVED"), "review"),
                 (dict(), "review")]
        for kw, verb in cases:
            with self.subTest(kw=kw):
                _, body = render(feed([repo(prs=[pr(7, **kw)])]))
                self.assertIn(f"Next: {verb} #7", body)

    def test_left_off_prefers_branch_then_item(self):
        br = {"title": "wip thing", "branch": "feat/x", "ts": ts(1),
              "url": f"https://github.com/{R}/tree/feat/x"}
        _, body = render(feed([repo(prs=[pr(1)], branches=[br])]))
        self.assertIn("Left off: wip thing (feat/x)\n    https://github.com/me/app/tree/feat/x", body)
        _, body = render(feed([repo(prs=[pr(1)], items=[item("merge", "#9", 3, "shipped it")])]))
        self.assertIn("Left off: shipped it (#9)", body)

    def test_next_falls_back_to_build_next(self):
        nu = [{"repo": R, "build_next": {"ref": "#40", "title": "Build me",
                                         "url": f"https://github.com/{R}/issues/40"}}]
        f = feed([repo(prs=[pr(1, draft=True)])], cards=[card(2, needs_human=True)], next_up=nu)
        _, body = render(f)
        self.assertIn("Next: build #40 Build me\n    https://github.com/me/app/issues/40", body)

    def test_next_omitted_when_nothing(self):
        f = feed([repo()], cards=[card(2, needs_human=True)])
        _, body = render(f)
        self.assertNotIn("Next:", body)

    def test_active_filter(self):
        quiet = repo("me/old", "Old", hours=24 * 8)
        never = repo("me/never", "Never", hours=None, items=[])
        _, body = render(feed([repo(prs=[pr(1)]), quiet, never]))
        self.assertNotIn("Old", body)
        self.assertNotIn("Never", body)
        _, body = render(feed([repo(prs=[pr(1)]), quiet]), active_days=10)
        self.assertIn("Old (me/old)", body)

    def test_ordering(self):
        a = repo("me/a", "Alpha", hours=1)
        b = repo("me/b", "Beta", hours=50, prs=[pr(3, hours=50)])
        c = repo("me/c", "Gamma", hours=20)
        _, body = render(feed([a, b, c]))
        self.assertLess(body.index("Beta"), body.index("Alpha"))
        self.assertLess(body.index("Alpha"), body.index("Gamma"))

    def test_attention_card_lists_quiet_repo(self):
        quiet = repo("me/old", "Old", hours=24 * 20)
        f = feed([repo(prs=[pr(1)]), quiet], cards=[card(5, needs_human=True, repo_name="me/old")])
        _, body = render(f)
        self.assertIn("Old (me/old)", body)
        self.assertIn("#5 card 5\n    https://github.com/me/old/issues/5", body)

    def test_done_or_closed_attention_cards_dont_count(self):
        c1, c2 = card(5, "Done", needs_human=True), card(6, needs_human=True)
        c2["closed"] = True
        subject, _ = render(feed([repo()], cards=[c1, c2]))
        self.assertIn("all clear", subject)

    def test_draft_not_waiting(self):
        subject, body = render(feed([repo(prs=[pr(1, draft=True)])]))
        self.assertIn("all clear", subject)
        self.assertTrue(body.startswith("Nothing needs you."))

    def test_window_filter(self):
        items = [item("merge", "#1", 2), item("merge", "#2", 30), item("push", "abc", 3)]
        prs = [pr(3, hours=5), pr(4, hours=48)]
        cards = [card(8, "Review", hours=1), card(9, "Review", hours=40)]
        _, body = render(feed([repo(items=items, prs=prs)], cards=cards))
        away = body[body.index("While you were away"):]
        self.assertIn("merged #1", away)
        self.assertNotIn("#2", away)
        self.assertNotIn("abc", away)
        self.assertIn("new PR #3", away)
        self.assertNotIn("#4", away)
        self.assertIn("#8 → Review", away)
        self.assertNotIn("#9", away)

    def test_board_move_shown_once(self):
        _, body = render(feed([repo(prs=[pr(1, hours=40)])], cards=[card(8, "Done", hours=1)]))
        self.assertEqual(body.count("#8 → Done"), 1)

    def test_long_window_heading_uses_real_since(self):
        since = NOW - timedelta(days=14)
        _, body = render(feed([repo(prs=[pr(1, hours=24 * 10)])]), since=since)
        self.assertIn("While you were away (since Tue 06 Oct 07:30)", body)
        self.assertIn("new PR #1", body)

    def test_one_line_counts(self):
        items = [item("merge", "#1", 2), item("merge", "#2", 3)]
        f = feed([repo(items=items, prs=[pr(3, draft=True)])], cards=[card(8, hours=1)])
        subject, body = render(f)
        self.assertEqual(body, "Nothing needs you. Since Mon 19 Oct 07:30: "
                               "2 merges, 1 new PR, 1 board move.\n")
        self.assertEqual(subject, "devfeed: all clear · Tue 20 Oct")

    def test_one_line_nothing_changed(self):
        _, body = render(feed([repo(hours=48, items=[item("push", "abc", 48)])]))
        self.assertEqual(body, "Nothing needs you, and nothing changed since Mon 19 Oct 07:30.\n")

    def test_empty_feed_one_line(self):
        subject, body = render(feed([]))
        self.assertIn("all clear", subject)
        self.assertEqual(body.count("\n"), 1)

    def test_no_board(self):
        f = feed([repo(prs=[pr(1)])])
        f.pop("board")
        subject, body = render(f)
        self.assertIn("1 need you", subject)
        self.assertIn("Next: review #1", body)

    def test_unicode(self):
        f = feed([repo(display="Café ☕", prs=[pr(1, title="Ünïcödé — émoji 🚀")])])
        _, body = render(f)
        self.assertIn("Café ☕ (me/app)", body)
        self.assertIn("Ünïcödé — émoji 🚀", body)

    def test_truncation(self):
        long = "x" * 120
        _, body = render(feed([repo(prs=[pr(1, title=long)])]))
        self.assertIn("x" * 79 + "…", body)
        self.assertNotIn("x" * 80, body)
        self.assertIn("https://github.com/me/app/pull/1\n", body)

    def test_caps(self):
        prs = [pr(n, hours=2) for n in range(1, 9)]
        items = [item("merge", f"#{n}", 1) for n in range(100, 113)]
        _, body = render(feed([repo(items=items, prs=prs)]))
        self.assertIn("+3 more", body)          # 8 waiting, 5 shown
        self.assertIn("+11 more", body)         # 13 merges + 8 new PRs, 10 shown
        many = [repo(f"me/r{n}", f"R{n}", prs=[pr(1)]) for n in range(18)]
        _, body = render(feed(many))
        self.assertIn("+3 more repos", body)

    def test_stale_first_line(self):
        subject, body = render(feed([repo(prs=[pr(1)])]), stale_hours=9)
        self.assertTrue(body.startswith("Couldn't refresh from GitHub; this is 9h old.\n"))
        _, body = render(feed([]), stale_hours=9)
        self.assertTrue(body.startswith("Couldn't refresh from GitHub; this is 9h old.\n"))
        self.assertIn("Nothing needs you", body)


if __name__ == "__main__":
    unittest.main()

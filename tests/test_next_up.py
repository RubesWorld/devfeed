"""Next up: section parsing and ranking. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402

R = "me/app"


def card(n, status, depends=(), touches=(), listed=True, held=False, size=None, closed=False):
    return {"title": f"card {n}", "url": f"https://github.com/{R}/issues/{n}", "repo": R,
            "ref": f"#{n}", "status": status, "since": "2026-01-01T00:00:00Z",
            "closed": closed, "needs_human": held, "labels": [],
            "depends_on": [f"{R}#{d}" for d in depends], "touches": list(touches),
            "touches_listed": listed, "held": held, "size": size}


def board(*cards):
    return {"columns": ["Backlog", "Inbox", "Ready", "In Progress", "Review"],
            "active_columns": ["In Progress", "Review"], "cards": list(cards)}


class Parsing(unittest.TestCase):
    BODY = ("## Why\nbecause\n\n## Touches\n- `src/db/schema.ts`: the table\n"
            "- src/app/connect/ page\n- none\n\n## Depends on\n- #12: its table\n"
            "- other/repo#3\n- #12 again\n\n## Verify\n1. see `src/lib/x.ts:40`\n")

    def test_sections(self):
        self.assertEqual(devfeed.parse_paths(devfeed.section(self.BODY, "Touches")),
                         ["src/db/schema.ts", "src/app/connect/"])
        self.assertEqual(devfeed.parse_refs(devfeed.section(self.BODY, "Depends on"), R),
                         [f"{R}#12", "other/repo#3"])
        self.assertEqual(devfeed.section(self.BODY, "Open questions"), "")

    def test_mentioned_paths_fallback(self):
        body = "Fix `src/lib/x.ts:40-52`, see `https://a.b/c`, call `router.replace('/')` and `npm test`."
        self.assertEqual(devfeed.mentioned_paths(body), ["src/lib/x.ts"])


class Ranking(unittest.TestCase):
    def nu(self, *cards, **cfg):
        return {r["repo"]: r for r in devfeed.next_up(board(*cards), {"next_up": cfg})}[R]

    def test_unblocking_beats_standalone_and_counts_chains(self):
        r = self.nu(card(1, "Ready"), card(2, "Ready"), card(3, "Inbox", depends=[2]),
                    card(4, "Backlog", depends=[3]))
        self.assertEqual(r["build_next"]["ref"], "#2")
        self.assertEqual(r["build_next"]["unblocks"], ["#3", "#4"])
        self.assertEqual(r["then"][0]["ref"], "#1")

    def test_blocked_cards_wait_and_merged_dependencies_dont_block(self):
        r = self.nu(card(1, "Ready", depends=[2]), card(2, "In Progress"),
                    card(3, "Ready", depends=[9]), card(9, "Done", closed=True))
        self.assertEqual(r["build_next"]["ref"], "#3")
        self.assertEqual([(w["ref"], w["blocked_by"]) for w in r["waiting"]], [("#1", ["#2"])])

    def test_held_cards_are_never_recommended(self):
        r = self.nu(card(1, "Ready", held=True), card(2, "Inbox", depends=[1]), card(3, "Ready"))
        self.assertEqual(r["build_next"]["ref"], "#3")
        self.assertEqual(r["needs_you"][0]["ref"], "#1")

    def test_shared_paths_rank_and_conflict(self):
        r = self.nu(card(1, "Ready", touches=["src/a.ts"]), card(2, "Ready", touches=["src/db/"]),
                    card(3, "In Progress", touches=["src/db/schema.ts"]))
        self.assertEqual(r["build_next"]["ref"], "#2")
        self.assertEqual([(c["a"]["ref"], c["b"]["ref"]) for c in r["conflicts"]], [("#2", "#3")])

    def test_guessed_paths_only_match_exact_files(self):
        r = self.nu(card(1, "Ready", touches=["src/app/"], listed=False),
                    card(2, "In Progress", touches=["src/app/index.tsx"], listed=False))
        self.assertEqual(r["conflicts"], [])

    def test_dependent_pairs_are_not_conflicts(self):
        r = self.nu(card(1, "Ready", touches=["src/x.ts"]),
                    card(2, "Ready", depends=[1], touches=["src/x.ts"]))
        self.assertEqual(r["conflicts"], [])

    def test_smaller_first_on_ties_and_configured_columns(self):
        r = self.nu(card(1, "Ready", size="m"), card(2, "Ready", size="s"))
        self.assertEqual(r["build_next"]["ref"], "#2")
        r = self.nu(card(1, "Inbox"), card(2, "Ready", held=True), columns=["Inbox"])
        self.assertEqual(r["build_next"]["ref"], "#1")


if __name__ == "__main__":
    unittest.main()

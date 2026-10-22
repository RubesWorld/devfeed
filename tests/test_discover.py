"""Repo discovery: an empty `gh repo list` is a failed fetch, not an empty
account, so the previous feed stays. Run: python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cfg(**extra):
    return {**devfeed.DEFAULTS, "owners": ["me"], "board": None,
            "repos": [{"repo": "me/pinned", "pin": True}], **extra}


class Discover(unittest.TestCase):
    def test_lists_recent_and_pinned(self):
        with mock.patch.object(devfeed, "gh", return_value=[
                {"nameWithOwner": "me/app", "pushedAt": NOW},
                {"nameWithOwner": "me/old", "pushedAt": "2020-01-01T00:00:00Z"}]):
            names = [r["repo"] for r in devfeed.discover(cfg())]
        self.assertEqual(sorted(names), ["me/app", "me/pinned"])

    def test_empty_listing_raises(self):
        # GitHub sometimes answers with an empty list instead of an error. Pins
        # would then be all that's left, and a good feed would be replaced.
        with mock.patch.object(devfeed, "gh", return_value=[]):
            with self.assertRaises(RuntimeError) as e:
                devfeed.discover(cfg())
        self.assertIn("me", str(e.exception))

    def test_empty_listing_for_one_owner_raises(self):
        listings = {"me": [{"nameWithOwner": "me/app", "pushedAt": NOW}], "org": []}
        with mock.patch.object(devfeed, "gh", side_effect=lambda *a: listings[a[2]]):
            with self.assertRaises(RuntimeError) as e:
                devfeed.discover(cfg(owners=["me", "org"]))
        self.assertIn("org", str(e.exception))

    def test_empty_listing_keeps_previous_feed(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        out = os.path.join(d.name, "feed.json")
        with open(out, "w") as f:
            json.dump({"generated_at": NOW, "repos": [{"repo": "me/app"}]}, f)
        with mock.patch.object(devfeed, "OUT_PATH", out), \
                mock.patch.object(devfeed, "PUBLIC", d.name), \
                mock.patch.object(devfeed, "gh", return_value=[]), \
                mock.patch.object(devfeed, "fetch_repo", return_value=([], [])), \
                mock.patch.object(devfeed, "fetch_open_work", return_value=([], [])), \
                mock.patch.object(devfeed, "log") as log:
            self.assertFalse(devfeed.collect_safely(cfg()))
        with open(out) as f:
            self.assertEqual(json.load(f)["repos"], [{"repo": "me/app"}])
        self.assertIn("ERROR", str(log.call_args))


if __name__ == "__main__":
    unittest.main()

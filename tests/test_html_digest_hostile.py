"""Hostile review of the HTML digest change: inputs that would crash the
email. Run: python3 -m unittest discover tests"""
import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402
from test_digest import NOW, R, feed, pr, repo  # noqa: E402
from test_digest_hostile import CommandBase, a_repo, iso  # noqa: E402
from test_digest_hostile import pr as hpr  # noqa: E402

os.environ["TZ"] = "UTC"
time.tzset()


def html(f, **kw):
    return devfeed.render_digest_html(f, NOW - timedelta(hours=24), NOW, **kw)


class ConfigSlot(unittest.TestCase):
    """`slot` comes straight from config.json's repo overrides (discover():
    `overrides.get(n, {}).get("slot") or ...`), unvalidated. The page's JS
    coerces "3" fine, so the page works; slot_color() does int arithmetic and
    dict lookup, so the HTML renderer raises."""

    def test_slot_written_as_string_in_config_renders(self):
        f = feed([dict(repo(prs=[pr(1)]), slot="3")])
        try:
            h = html(f)
        except TypeError as e:
            self.fail(f"render_digest_html crashed on slot \"3\" from config: {e}")
        self.assertIn(devfeed.SLOT_DARK[3], h)

    def test_slot_not_a_whole_number_renders(self):
        f = feed([dict(repo(prs=[pr(1)]), slot=2.5)])
        try:
            html(f)
        except KeyError as e:
            self.fail(f"render_digest_html crashed on slot 2.5 from config: KeyError {e}")

    def test_config_slot_reaches_feed_unvalidated(self):
        # The path from config.json to the renderer: discover() passes it through.
        cfg = {**devfeed.DEFAULTS, "owners": ["me"], "board": None,
               "repos": [{"repo": R, "slot": "3"}]}
        now = iso(datetime.now(timezone.utc))
        with mock.patch.object(devfeed, "gh", return_value=[{"nameWithOwner": R, "pushedAt": now}]):
            slot = devfeed.discover(cfg)[0]["slot"]
        try:
            devfeed.slot_color(slot)
        except TypeError as e:
            self.fail(f"discover() let config slot {slot!r} through and slot_color() crashed: {e}")


class HtmlFailureKillsSend(CommandBase):
    """A bug in the HTML renderer must not cost the whole email: the text
    part still goes out, and a text dry-run never renders HTML at all."""

    def setUp(self):
        super().setUp()
        gen = iso(datetime.now(timezone.utc))
        with open(self.feed_path, "w") as f:
            json.dump({"generated_at": gen, "repos": [a_repo([hpr(1, gen)])], "board": None}, f)
        p = mock.patch.object(devfeed, "render_digest_html", side_effect=TypeError("boom"))
        self.render = p.start()
        self.addCleanup(p.stop)

    def test_send_survives_html_render_failure(self):
        self.assertEqual(self.run_digest(), 0)
        msg = self.smtp.return_value.send_message.call_args[0][0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertIn("HTML part failed", self.logged())

    def test_text_dry_run_does_not_need_html(self):
        out = io.StringIO()
        with mock.patch.object(devfeed, "collect_safely", return_value=True), redirect_stdout(out):
            code = devfeed.digest({**devfeed.DEFAULTS, "owners": ["me"]}, self.config, True)
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("Subject: "))
        self.render.assert_not_called()

    def test_html_preview_failure_exits_1(self):
        with mock.patch.object(devfeed, "collect_safely", return_value=True), redirect_stdout(io.StringIO()):
            code = devfeed.digest({**devfeed.DEFAULTS, "owners": ["me"]}, self.config, True, html=True)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

"""Digest regressions from hostile review: TLS verification, overlapping runs,
state writes after a send, password scrubbing and bad config values."""
import io
import json
import os
import smtplib
import ssl
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402

os.environ["TZ"] = "UTC"
time.tzset()

R = "me/app"
PW = "s3cret-app-pw"
SETTINGS = {"to": ["me@example.com"], "from": "bot@example.com", "host": "smtp.example.com",
            "port": 587, "user": "bot@example.com", "password": PW, "active_days": 7}
GOOD = {"to": "me@example.com", "smtp": {"host": "smtp.example.com", "user": "bot@example.com"}}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cfg_with(digest):
    return {**devfeed.DEFAULTS, "owners": ["me"], "digest": digest}


def pr(n, opened):
    return {"title": f"pr {n}", "ref": f"#{n}", "url": f"https://github.com/{R}/pull/{n}",
            "branch": f"b{n}", "draft": False, "opened": opened, "ts": opened,
            "review": None, "checks": None}


def a_repo(prs=()):
    now = iso(datetime.now(timezone.utc))
    return {"repo": R, "display": "App", "slot": 1, "last_activity": now, "spark": [],
            "items": [{"type": "push", "title": "x", "ref": "abc1234", "ts": now,
                       "url": f"https://github.com/{R}/commit/abc1234"}],
            "open_prs": list(prs), "branches": []}


class TLS(unittest.TestCase):
    """The SMTP password goes over TLS that never checks the server certificate:
    smtplib's default context is ssl._create_unverified_context, so anyone on
    the path (cafe Wi-Fi, a hostile DNS answer) can read the app password."""

    def test_starttls_verifies_server_certificate(self):
        with mock.patch.object(devfeed.smtplib, "SMTP") as smtp:
            conn = smtp.return_value.__enter__.return_value
            devfeed.send_digest(devfeed.build_message("s", "b\n", SETTINGS,
                                                      datetime.now(timezone.utc)), SETTINGS)
        ctx = conn.starttls.call_args.kwargs.get("context") if conn.starttls.call_args else None
        self.assertIsNotNone(ctx, "starttls() called without a context: certificate not verified")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_smtp_ssl_verifies_server_certificate(self):
        settings = {**SETTINGS, "port": 465}
        with mock.patch.object(devfeed.smtplib, "SMTP_SSL") as ssl_cls:
            devfeed.send_digest(devfeed.build_message("s", "b\n", settings,
                                                      datetime.now(timezone.utc)), settings)
        ctx = ssl_cls.call_args.kwargs.get("context")
        self.assertIsNotNone(ctx, "SMTP_SSL() without a context: certificate not verified")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


class CommandBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config = os.path.join(self.dir.name, "config.json")
        self.state = os.path.join(self.dir.name, "digest-state.json")
        self.feed_path = os.path.join(self.dir.name, "feed.json")
        for name, value in (("OUT_PATH", self.feed_path), ("PUBLIC", self.dir.name)):
            p = mock.patch.object(devfeed, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"DEVFEED_SMTP_PASSWORD": PW})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(devfeed.smtplib, "SMTP")
        self.smtp = p.start()
        self.addCleanup(p.stop)
        self.smtp.return_value.__enter__.return_value = self.smtp.return_value
        self.smtp.return_value.__exit__.return_value = False
        p = mock.patch.object(devfeed, "log")
        self.log = p.start()
        self.addCleanup(p.stop)

    def write_feed(self, generated_at=None, **extra):
        gen = generated_at or iso(datetime.now(timezone.utc))
        with open(self.feed_path, "w") as f:
            json.dump({"generated_at": gen, "repos": [a_repo([pr(1, gen)])], "board": None,
                       **extra}, f)
        return gen

    def run_digest(self, collected=True, digest_cfg=None):
        out = io.StringIO()
        with mock.patch.object(devfeed, "collect_safely", return_value=collected), \
                redirect_stdout(out):
            return devfeed.digest(cfg_with(digest_cfg or GOOD), self.config, False)

    def logged(self):
        return " ".join(str(c) for c in self.log.call_args_list)


class PasswordLeak(CommandBase):
    def test_password_with_backslash_echoed_in_bytes_response_is_hidden(self):
        # SMTPResponseException's str() is repr((code, bytes)), which doubles a
        # backslash, so str.replace(password, '***') no longer matches.
        pw = "ab\\cd-app-pw"
        self.write_feed()
        self.smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(
            535, f"5.7.8 rejected {pw}".encode())
        with mock.patch.dict(os.environ, {"DEVFEED_SMTP_PASSWORD": pw}):
            code = self.run_digest()
        self.assertEqual(code, 1)
        logged = self.logged()
        self.assertNotIn("ab\\\\cd-app-pw", logged)
        self.assertNotIn("cd-app-pw", logged)


class StateFile(CommandBase):
    def test_state_written_when_quit_fails_after_server_accepted_mail(self):
        # send_message returned, so the mail is delivered; a non-221 reply to
        # QUIT makes smtplib's __exit__ raise. The state must still move on,
        # or the next digest repeats the whole window.
        gen = self.write_feed()
        self.smtp.return_value.__exit__.side_effect = smtplib.SMTPResponseException(
            451, b"closing")
        code = self.run_digest()
        self.smtp.return_value.send_message.assert_called_once()
        self.assertTrue(os.path.exists(self.state), "mail delivered but state not written")
        with open(self.state) as f:
            self.assertEqual(json.load(f)["last_window_end"], gen)
        self.assertEqual(code, 0)

    def test_unwritable_state_dir_after_send_does_not_crash(self):
        # Config dir read-only (root-owned /etc/devfeed, a locked-down home):
        # the mail goes out, then write_state raises PermissionError straight
        # out of digest() with a traceback.
        self.write_feed()
        os.chmod(self.dir.name, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, self.dir.name, stat.S_IRWXU)
        try:
            code = self.run_digest()
        except OSError as e:
            self.fail(f"digest() raised {type(e).__name__} after sending: {e}")
        self.assertIsInstance(code, int)

    def test_overlapping_runs_do_not_crash_on_shared_tmp(self):
        # Two digests overlap (cron + manual, which the spec allows). Both use
        # the same digest-state.json.tmp, so the second os.replace finds its
        # tmp already moved away and raises FileNotFoundError after sending.
        real_replace = os.replace
        calls = []

        def racing_replace(src, dst):
            if not calls:
                calls.append(1)
                devfeed.write_state(self.state, "2026-10-20T07:30:05Z",
                                    datetime.now(timezone.utc))
            return real_replace(src, dst)

        with mock.patch.object(devfeed.os, "replace", side_effect=racing_replace):
            try:
                devfeed.write_state(self.state, "2026-10-20T07:30:00Z",
                                    datetime.now(timezone.utc))
            except FileNotFoundError as e:
                self.fail(f"overlapping write_state crashed: {e}")
        with open(self.state) as f:
            json.load(f)

    def test_older_run_does_not_move_window_backwards(self):
        # Overlap again: run B (newer feed, T2) finished first and stored T2.
        # Run A, holding an older feed T1, then sees T2 > T1, treats the state
        # as a clock change, re-sends the last 24h and stores T1: the window
        # goes backwards and the next digest repeats T1..T2.
        t1 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        t2 = t1 + timedelta(minutes=3)
        self.write_feed(iso(t1))
        devfeed.write_state(self.state, iso(t2), datetime.now(timezone.utc))
        self.run_digest()
        with open(self.state) as f:
            self.assertEqual(json.load(f)["last_window_end"], iso(t2))

    def test_stale_demo_feed_is_not_mailed_as_real(self):
        # `devfeed.py demo` overwrites public/feed.json with made-up acme data.
        # If the next digest's collect fails, that fake feed is mailed and its
        # generated_at is stored as the window end.
        self.write_feed(demo=True)
        code = self.run_digest(collected=False)
        self.smtp.return_value.send_message.assert_not_called()
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(code, 1)


class StaleFallback(CommandBase):
    def test_missing_gh_still_sends_stale_email(self):
        # Spec Verify 7: with gh unfindable, the email is built from the old
        # feed.json. gh_bin() calls sys.exit, which collect_safely's
        # `except Exception` does not catch, so digest aborts and sends nothing.
        self.write_feed(iso(datetime.now(timezone.utc) - timedelta(hours=9)))
        with mock.patch.object(devfeed, "gh_bin", side_effect=SystemExit("no gh")):
            try:
                code = devfeed.digest(cfg_with(GOOD), self.config, False)
            except SystemExit as e:
                self.fail(f"digest exited ({e}) instead of mailing the stale feed")
        self.assertEqual(code, 0)
        body = self.smtp.return_value.send_message.call_args[0][0].get_content()
        self.assertTrue(body.startswith("Couldn't refresh from GitHub; this is 9h old."))


class Window(unittest.TestCase):
    def test_window_end_is_not_after_collection_started(self):
        # generated_at is stamped after every repo has been fetched. A merge in
        # repo 1 that lands while repos 2..N are still being fetched has
        # ts < generated_at but is not in this feed; the next window starts at
        # generated_at, so it is never reported.
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        out = os.path.join(d.name, "feed.json")
        first_fetch = []

        def fetch_repo(repo, since):
            if not first_fetch:
                first_fetch.append(datetime.now(timezone.utc).replace(microsecond=0))
            time.sleep(1.1)
            return [], []

        repos = [{"repo": f"me/r{i}", "display": f"r{i}", "slot": 1} for i in range(2)]
        with mock.patch.object(devfeed, "OUT_PATH", out), \
                mock.patch.object(devfeed, "PUBLIC", d.name), \
                mock.patch.object(devfeed, "discover", return_value=repos), \
                mock.patch.object(devfeed, "fetch_repo", side_effect=fetch_repo), \
                mock.patch.object(devfeed, "fetch_open_work", return_value=([], [])), \
                mock.patch.object(devfeed, "log"):
            devfeed.collect({**devfeed.DEFAULTS, "owners": ["me"], "board": None})
        with open(out) as f:
            end = devfeed.parse_ts(json.load(f)["generated_at"])
        self.assertLessEqual(end, first_fetch[0],
                             "window end is later than the first fetch: events in between are lost")


class Inputs(unittest.TestCase):
    def test_active_days_as_string_does_not_crash(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        feed_path = os.path.join(d.name, "feed.json")
        gen = iso(datetime.now(timezone.utc))
        with open(feed_path, "w") as f:
            json.dump({"generated_at": gen, "repos": [a_repo([pr(1, gen)])], "board": None}, f)
        out = io.StringIO()
        with mock.patch.object(devfeed, "OUT_PATH", feed_path), \
                mock.patch.object(devfeed, "collect_safely", return_value=True), \
                mock.patch.object(devfeed, "log"), redirect_stdout(out):
            try:
                devfeed.digest(cfg_with({**GOOD, "active_days": "7"}),
                               os.path.join(d.name, "config.json"), True)
            except TypeError as e:
                self.fail(f"\"active_days\": \"7\" crashed with a traceback: {e}")


if __name__ == "__main__":
    unittest.main()

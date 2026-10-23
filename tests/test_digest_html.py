"""HTML digest: layout, escaping, links, size, and the --html preview.
Run: python3 -m unittest discover tests"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402
from test_digest import (GOOD, NOW, PW, SETTINGS, R, card, cfg_with, feed, pr,  # noqa: E402
                         repo)

os.environ["TZ"] = "UTC"
time.tzset()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def html(f, since=None, **kw):
    return devfeed.render_digest_html(f, since or NOW - timedelta(hours=24), NOW, **kw)


class Html(unittest.TestCase):
    def test_document_is_email_safe(self):
        h = html(feed([repo(prs=[pr(1)])]))
        self.assertTrue(h.startswith("<!doctype html>"))
        self.assertIn('<meta name="color-scheme" content="dark">', h)
        self.assertIn("max-width:600px", h)
        for banned in ("<link", "<img", "<script", "url(", "@import"):
            self.assertNotIn(banned, h)

    def test_matches_feed_look(self):
        h = html(feed([repo(prs=[pr(1, review="APPROVED", checks="SUCCESS"), pr(2, checks="FAILURE"),
                                 pr(3)])], cards=[card(9, needs_human=True)]))
        for token in ("#0d0d0d", "#1a1a19", "#3987e5", "Space Grotesk", "IBM Plex Mono", "Dev Feed"):
            self.assertIn(token, h)
        self.assertIn("border-left:3px solid #3987e5", h)   # slot 1
        for tag, color in (("merge", "#0ca30c"), ("fix", "#d03b3b"), ("review", "#3987e5"),
                           ("needs&nbsp;you", "#c98500")):
            self.assertIn(f"color:{color}\">{tag}</span>", h)
        self.assertIn("4 waiting", h)
        self.assertIn("4 need you", h)

    def test_titles_are_links_no_raw_urls(self):
        h = html(feed([repo(prs=[pr(1, title="Ship it")])]))
        self.assertIn(f'href="https://github.com/{R}/pull/1"', h)
        self.assertIn(">Ship it</a>", h)
        self.assertNotIn(f">https://github.com/{R}/pull/1<", h)

    def test_escapes_titles_and_attrs(self):
        p = pr(1, title='<script>alert(1)</script> & "q"')
        p["url"] = 'https://github.com/me/app/pull/1?"onmouseover=alert(1)'
        f = feed([repo(display="<b>App</b>", prs=[p])])
        h = html(f)
        self.assertNotIn("<script>", h)
        self.assertNotIn("<b>App</b>", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot;", h)
        self.assertNotIn('?"onmouseover', h)
        self.assertIn("?&quot;onmouseover", h)

    def test_only_https_links(self):
        bad = [pr(1, title="js"), pr(2, title="plain http")]
        bad[0]["url"], bad[1]["url"] = "javascript:alert(1)", "http://example.com/x"
        h = html(feed([repo(prs=bad)]))
        self.assertNotIn("javascript:", h)
        self.assertNotIn('href="http://', h)
        self.assertIn(">js<", h)
        self.assertIn(">plain http<", h)

    def test_one_line_and_stale(self):
        h = html(feed([]), stale_hours=9)
        stale = h.index("Couldn&#x27;t refresh from GitHub; this is 9h old.")
        self.assertLess(stale, h.index("Nothing needs you"))
        self.assertIn("all clear", h)
        self.assertNotIn("border-left:3px", h)

    def test_next_not_repeated(self):
        nu = [{"repo": "me/b", "build_next": {"ref": "#40", "title": "Build me",
                                             "url": "https://github.com/me/b/issues/40"}}]
        h = html(feed([repo(prs=[pr(1, title="Only once")]), repo("me/b", "Beta")], next_up=nu))
        self.assertEqual(h.count(">Only once</a>"), 1)   # the away line says "#1 Only once"
        self.assertIn(">build&nbsp;next</span>", h)
        self.assertIn(">Build me</a>", h)

    def test_also_active(self):
        h = html(feed([repo(prs=[pr(1)]), repo("me/q", "Quiet", hours=2)]))
        self.assertIn("Also active", h)
        self.assertIn('href="https://github.com/me/q"', h)
        self.assertIn(">Quiet</a>", h)

    def test_left_off_and_away(self):
        items = [{"type": "merge", "title": "shipped", "ref": "#9", "ts": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "url": f"https://github.com/{R}/pull/9"}]
        h = html(feed([repo(items=items, prs=[pr(1)])], cards=[card(8, "Review", hours=1)]))
        self.assertIn("Left off", h)
        self.assertIn("While you were away", h)
        self.assertIn(">merged</span>", h)
        self.assertIn("#8 → Review", h)

    def test_caps(self):
        prs = [pr(n, hours=2) for n in range(1, 9)]
        h = html(feed([repo(prs=prs)]))
        self.assertIn("+3 more", h)
        many = [repo(f"me/r{n}", f"R{n}", prs=[pr(1)]) for n in range(18)]
        self.assertIn("+3 more repos", html(feed(many)))

    def test_size_under_gmail_clip(self):
        # Gmail clips messages over 102KB behind a "view entire message" link.
        long = "A long pull request title that keeps going " * 4
        prs = [pr(n, hours=2, title=long) for n in range(1, 9)]
        many = [repo(f"me/r{n}", f"Repository {n}", prs=prs) for n in range(20)]
        quiet = [repo(f"me/q{n}", f"Quiet {n}", hours=3) for n in range(20)]
        size = len(html(feed(many + quiet)).encode())
        self.assertLess(size, 95_000, size)  # Gmail clips at 102KB

    def test_unicode(self):
        h = html(feed([repo(display="Café ☕", prs=[pr(1, title="Ünïcödé — émoji 🚀")])]))
        self.assertIn("Café ☕", h)
        self.assertIn("Ünïcödé — émoji 🚀", h)

    def test_no_board(self):
        f = feed([repo(prs=[pr(1)])])
        f.pop("board")
        self.assertIn("1 need you", html(f))

    def test_page_url(self):
        f = feed([repo(prs=[pr(1)])])
        self.assertNotIn("open devfeed", html(f))
        h = html(f, page_url='http://host.example:8300/?a="b"')
        self.assertIn('href="http://host.example:8300/?a=&quot;b&quot;"', h)
        self.assertIn("open devfeed", h)
        self.assertNotIn("open devfeed", html(f, page_url="javascript:alert(1)"))


class Message(unittest.TestCase):
    def test_multipart_alternative(self):
        msg = devfeed.build_message("devfeed: 1 need you · Café", "héllo 🚀\n", SETTINGS, NOW,
                                    html="<!doctype html><p>héllo 🚀</p>")
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        plain, rich = msg.iter_parts()
        self.assertEqual((plain.get_content_type(), rich.get_content_type()), ("text/plain", "text/html"))
        self.assertEqual((plain.get_content_charset(), rich.get_content_charset()), ("utf-8", "utf-8"))
        self.assertEqual(plain.get_content(), "héllo 🚀\n")
        self.assertIn("héllo 🚀", rich.get_content())
        for h in ("From", "To", "Subject", "Date", "Message-ID"):
            self.assertTrue(msg[h], h)
        msg.as_bytes()


class Command(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config = os.path.join(self.dir.name, "config.json")
        self.feed_path = os.path.join(self.dir.name, "feed.json")
        gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(self.feed_path, "w") as f:
            json.dump({"generated_at": gen, "repos": [repo(prs=[pr(1, title="Hello")])], "board": None}, f)
        for target, value in ((devfeed, "OUT_PATH"), (devfeed, "log")):
            p = mock.patch.object(target, value, self.feed_path if value == "OUT_PATH" else mock.Mock())
            p.start()
            self.addCleanup(p.stop)

    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "devfeed.py"), *args],
                              capture_output=True, text=True, timeout=30)

    def test_demo_html(self):
        r = self.cli("digest", "--demo", "--html", "--config", self.config)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("<!doctype html>"))
        self.assertIn("Storefront", r.stdout)

    def test_html_needs_preview(self):
        r = self.cli("digest", "--html", "--config", self.config)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--html", r.stderr)
        self.assertEqual(r.stderr.strip().count("\n"), 0)

    def test_dry_run_html(self):
        out = io.StringIO()
        with mock.patch.object(devfeed, "collect_safely", return_value=True), redirect_stdout(out):
            code = devfeed.digest(cfg_with({**GOOD, "page_url": "http://h:8300/"}), self.config, True,
                                  html=True)
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("<!doctype html>"))
        self.assertIn(">Hello</a>", out.getvalue())
        self.assertIn('href="http://h:8300/"', out.getvalue())

    def test_send_includes_html(self):
        with mock.patch.dict(os.environ, {"DEVFEED_SMTP_PASSWORD": PW}), \
                mock.patch.object(devfeed.smtplib, "SMTP") as smtp, \
                mock.patch.object(devfeed, "collect_safely", return_value=True):
            smtp.return_value.__enter__.return_value = smtp.return_value
            self.assertEqual(devfeed.digest(cfg_with(GOOD), self.config, False), 0)
        msg = smtp.return_value.send_message.call_args[0][0]
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertIn(">Hello</a>", msg.get_body(("html",)).get_content())
        self.assertIn("review #1 Hello", msg.get_body(("plain",)).get_content())

    def test_bad_page_url_exits(self):
        with self.assertRaises(SystemExit) as e:
            devfeed.digest_page_url(cfg_with({**GOOD, "page_url": "ftp://x"}))
        self.assertIn("digest.page_url", str(e.exception.code))
        self.assertIsNone(devfeed.digest_page_url(cfg_with(GOOD)))


if __name__ == "__main__":
    unittest.main()

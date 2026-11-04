"""Swipe-to-advance: the allowlist, the server-side re-checks, and the request guards.
Run: python3 -m unittest discover tests"""
import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import devfeed  # noqa: E402

BOARD = {"owner": "Me", "number": 1,
         "advance": {"Spec'd": "Ready",
                     "Backlog": {"to": "Inbox", "add_labels": ["inbox"], "remove_labels": ["backlog"]},
                     "Review": "Review", "Bad": 5},
         "next_up": {"hold_labels": ["risk:", "spec:needs-answers"]}}


def node(status="Spec'd", labels=(), state="OPEN", owner="me", number=1, issue=True):
    content = {"number": 7, "state": state, "repository": {"nameWithOwner": "me/app"},
               "labels": {"nodes": [{"name": n} for n in labels]}} if issue else {}
    return {"data": {"node": {
        "project": {"id": "P1", "number": number, "owner": {"login": owner},
                    "field": {"id": "F1", "options": [{"id": n[0], "name": n} for n in
                                                      ("Backlog", "Inbox", "Spec'd", "Ready", "Review")]}},
        "status": {"name": status} if status else None,
        "content": content}}}


class FakeGh:
    def __init__(self, item_node):
        self.node, self.calls = item_node, []

    def __call__(self, *args, parse=True):
        self.calls.append(args)
        return self.node if "query=" + devfeed.ITEM_QUERY in args else {}

    def mutations(self):
        return [a for a in self.calls if "query=" + devfeed.SET_STATUS in a]


class Rules(unittest.TestCase):
    def test_normalised_and_bad_entries_dropped(self):
        rules = devfeed.advance_rules(BOARD)
        self.assertEqual(sorted(rules), ["Backlog", "Spec'd"])
        self.assertEqual(rules["Spec'd"], {"to": "Ready", "add_labels": [], "remove_labels": []})
        self.assertEqual(devfeed.advance_rules(None), {})


class Advance(unittest.TestCase):
    def go(self, item_node, frm="Spec'd", item="PVTI_x"):
        gh = FakeGh(item_node)
        return gh, devfeed.advance(BOARD, item, frm, run=gh)

    def refused(self, code, item_node, frm="Spec'd", item="PVTI_x"):
        gh = FakeGh(item_node)
        with self.assertRaises(devfeed.Refused) as e:
            devfeed.advance(BOARD, item, frm, run=gh)
        self.assertEqual(e.exception.code, code)
        self.assertEqual(gh.mutations(), [])
        return str(e.exception)

    def test_moves_along_the_allowlist(self):
        gh, out = self.go(node())
        self.assertEqual(out, {"ref": "me/app#7", "status": "Ready"})
        (m,) = gh.mutations()
        self.assertIn("option=R", m)
        self.assertIn("item=PVTI_x", m)

    def test_labels_change_before_the_column(self):
        gh, _ = self.go(node("Backlog"), frm="Backlog")
        edit = next(i for i, a in enumerate(gh.calls) if a[:2] == ("issue", "edit"))
        self.assertEqual(gh.calls[edit][4:], ("me/app", "--add-label", "inbox", "--remove-label", "backlog"))
        self.assertLess(edit, gh.calls.index(gh.mutations()[0]))

    def test_columns_not_in_the_allowlist_are_refused(self):
        self.refused(403, node("Review"), frm="Review")
        self.refused(403, node("Ready"), frm="Ready")
        self.refused(403, node(), frm=["Spec'd"])

    def test_stale_page_is_refused(self):
        self.assertIn("in Ready now", self.refused(409, node("Ready")))

    def test_held_closed_and_other_boards_are_refused(self):
        self.assertIn("risk:auth", self.refused(409, node(labels=["risk:auth"])))
        self.refused(409, node(labels=["needs-human"]))
        self.refused(409, node(labels=["spec:needs-answers"]))
        self.refused(409, node(state="CLOSED"))
        self.refused(403, node(owner="someone-else"))
        self.refused(403, node(number=2))
        self.refused(409, node(issue=False))

    def test_bad_item_ids_never_reach_gh(self):
        gh = FakeGh(node())
        for bad in ("", "a b", "x" * 101, "id=evil&x", None, 5):
            with self.assertRaises(devfeed.Refused):
                devfeed.advance(BOARD, bad, "Spec'd", run=gh)
        self.assertEqual(gh.calls, [])


class Guards(unittest.TestCase):
    """The real handler on a loopback port, with advance() stubbed out."""

    @classmethod
    def setUpClass(cls):
        cfg = {**devfeed.DEFAULTS, "bind": "127.0.0.1", "port": 0, "board": BOARD,
               "hosts": ["mini.tailnet.ts.net"]}
        handler = type("H", (devfeed.Handler,), {"cfg": cfg})
        cls.server = devfeed.Server(("127.0.0.1", 0), handler)
        cfg["port"] = cls.server.server_address[1]
        cls.port = cfg["port"]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.patch = mock.patch.object(devfeed, "advance",
                                      side_effect=lambda b, i, f: {"ref": i, "status": "Ready"})
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, body=b'{"item": "PVTI_x", "from": "Spec\'d"}', path="/api/advance", **headers):
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-Devfeed": "1"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"))
        c.close()
        return out

    def test_the_page_can_move_a_card(self):
        self.assertEqual(self.post(), (200, {"ref": "PVTI_x", "status": "Ready"}))
        self.assertEqual(self.post(Origin=f"http://127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.post(Host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(self.post(Host=f"mini.tailnet.ts.net:{self.port}")[0], 200)

    def test_cross_site_and_rebinding_requests_are_refused(self):
        self.assertEqual(self.post(Origin="https://evil.example")[0], 403)
        self.assertEqual(self.post(Host=f"evil.example:{self.port}")[0], 403)
        self.assertEqual(self.post(X_Devfeed=None)[0], 403)
        self.assertEqual(self.post(Content_Type="text/plain")[0], 403)

    def test_malformed_requests(self):
        self.assertEqual(self.post(path="/api/other")[0], 404)
        self.assertEqual(self.post(body=b"not json")[0], 400)
        self.assertEqual(self.post(body=b"[1]")[0], 400)
        self.assertEqual(self.post(body=b"x" * 3000)[0], 400)

    def test_preflight_is_never_answered(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("OPTIONS", "/api/advance", headers={"Origin": "https://evil.example"})
        r = c.getresponse()
        self.assertNotIn("Access-Control-Allow-Origin", dict(r.getheaders()))
        c.close()


if __name__ == "__main__":
    unittest.main()

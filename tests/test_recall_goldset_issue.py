"""A due recall gold set opens ONE GitHub issue, edited in place, closed on recovery.

From 2026-09 to 2026-11 `recall`, `recall_us` and `recall_eu` filed DEGRADED
every week because their reference sets aged out, and the only signal was a
line in the weekly digest email. Nothing landed where the steward sweep and
the AI sessions look (open GitHub issues), so nobody built the new set.
recall_goldset_issue.py closes that gap with the same one-issue discipline as
reference_freshness.py and host-watch: open once, edit silently, close itself.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import recall_goldset_issue as rgi  # noqa: E402


class Resp:
    def __init__(self, data=None):
        self._data = data if data is not None else {}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, open_issues):
        self.open_issues = open_issues
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, None))
        return Resp(self.open_issues)

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url, json))
        return Resp({"number": 77})

    def patch(self, url, json=None, **kw):
        self.calls.append(("PATCH", url, json))
        return Resp({})


def write_worklists(tmp, due_by_family):
    paths = {}
    for fam, due in due_by_family.items():
        p = Path(tmp) / f"{fam}.json"
        p.write_text(json.dumps({"next_goldset": {
            "due": due, "reason": "window closed 104 days ago",
            "instruction": "Build a new recall gold set and seal it."}}))
        paths[fam] = str(p)
    return paths


class DueReading(unittest.TestCase):
    def test_reads_every_family_worklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_worklists(tmp, {"world": True, "us": False, "eu": True})
            rows = rgi.check(paths)
        self.assertEqual({r["family"]: r["due"] for r in rows},
                         {"world": True, "us": False, "eu": True})

    def test_missing_worklist_is_not_silently_fresh(self):
        rows = rgi.check({"world": "/nonexistent/recall_worklist.json"})
        self.assertTrue(rows[0]["due"])

    def test_default_paths_cover_all_three_families(self):
        self.assertEqual(set(rgi.default_paths()), {"world", "us", "eu"})


class IssueSync(unittest.TestCase):
    DUE = [{"family": "world", "due": True, "reason": "r", "instruction": "i"}]
    OK = [{"family": "world", "due": False, "reason": "", "instruction": ""}]

    def test_opens_one_issue_when_due(self):
        s = FakeSession([])
        self.assertEqual(rgi.sync_issue(self.DUE, repo="o/r", token="t", session=s),
                         "opened #77")
        posts = [c for c in s.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["title"], rgi.ISSUE_TITLE)
        self.assertIn("world", posts[0][2]["body"])

    def test_edits_existing_issue_instead_of_opening_another(self):
        s = FakeSession([{"number": 5, "title": rgi.ISSUE_TITLE}])
        self.assertEqual(rgi.sync_issue(self.DUE, repo="o/r", token="t", session=s),
                         "edited #5")
        self.assertFalse([c for c in s.calls if c[0] == "POST"])

    def test_ignores_a_pull_request_with_the_same_title(self):
        s = FakeSession([{"number": 9, "title": rgi.ISSUE_TITLE, "pull_request": {}}])
        self.assertEqual(rgi.sync_issue(self.DUE, repo="o/r", token="t", session=s),
                         "opened #77")

    def test_closes_on_recovery(self):
        s = FakeSession([{"number": 5, "title": rgi.ISSUE_TITLE}])
        self.assertEqual(rgi.sync_issue(self.OK, repo="o/r", token="t", session=s),
                         "closed #5")
        self.assertIn(("PATCH", "https://api.github.com/repos/o/r/issues/5",
                       {"state": "closed", "state_reason": "completed"}), s.calls)

    def test_nothing_due_and_nothing_open_is_quiet(self):
        s = FakeSession([])
        self.assertEqual(rgi.sync_issue(self.OK, repo="o/r", token="t", session=s),
                         "no issue")


class Wiring(unittest.TestCase):
    def test_recall_workflow_syncs_the_issue(self):
        wf = (Path(__file__).resolve().parent.parent
              / ".github/workflows/recall.yml").read_text()
        self.assertIn("recall_goldset_issue.py --issue", wf)
        self.assertIn("issues: write", wf)


if __name__ == "__main__":
    unittest.main()

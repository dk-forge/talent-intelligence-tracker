"""main_green answers "what is main's ACTUAL state?", so its judgement is proved.

No test here opens a connection: every read goes through a fake `api`, or
through a patched `subprocess.run`, so `gh` is never started.

Mutation-proved on 2026-09-21: in `judge`, `if not runs: return UNKNOWN` was
changed to return PASS, and `test_no_run_at_all_is_unknown_not_green`,
`test_unknown_never_exits_zero` and `test_a_path_filtered_workflow_is_not_aged`
went red; the guard was put back. A test that
has only ever passed has not tested anything.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main_green as mg  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
REPO = "o/r"
WF = mg.Workflow("tests.yml", "suite", max_age_days=3.0)


def run(conclusion="success", age_hours=1.0, sha=HEAD, status="completed"):
    at = (NOW - timedelta(hours=age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"status": status, "conclusion": conclusion, "head_sha": sha,
            "updated_at": at, "created_at": at, "html_url": "https://x/run"}


def listing(*runs):
    return {"total_count": len(runs), "workflow_runs": list(runs)}


def judge(payload, head_age_hours=10.0, wf=WF):
    return mg.judge(wf, payload, HEAD, NOW - timedelta(hours=head_age_hours), NOW)


class FakeApi:
    """Records every call. `runs` maps workflow file -> payload or Exception."""

    def __init__(self, runs, issues=None, head_age_hours=10.0):
        self.runs, self.issues, self.calls = runs, list(issues or []), []
        self.head_at = NOW - timedelta(hours=head_age_hours)

    def __call__(self, path, *, method="GET", payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path.endswith("/commits/main"):
            return {"sha": HEAD, "commit": {"committer": {
                "date": self.head_at.strftime("%Y-%m-%dT%H:%M:%SZ")}}}
        if method == "GET" and "/actions/workflows/" in path:
            got = self.runs[path.split("/actions/workflows/")[1].split("/")[0]]
            if isinstance(got, Exception):
                raise got
            return got
        if method == "GET" and "/issues?" in path:
            return [i for i in self.issues if i.get("state", "open") == "open"]
        if method == "POST" and path.endswith("/issues"):
            issue = {"number": 100 + len(self.issues), "state": "open", **payload}
            self.issues.append(issue)
            return issue
        if method == "PATCH":
            n = int(path.rsplit("/", 1)[1])
            issue = next(i for i in self.issues if i["number"] == n)
            issue.update(payload)
            return issue
        if method == "POST" and path.endswith("/comments"):
            return {"id": 1}
        raise AssertionError(f"unexpected call {method} {path}")

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


class JudgeTests(unittest.TestCase):
    def test_a_recent_green_run_is_pass(self):
        self.assertEqual(judge(listing(run())).state, mg.PASS)

    def test_a_red_newest_run_is_fail(self):
        v = judge(listing(run("failure"), run("success", 5)))
        self.assertEqual(v.state, mg.FAIL)

    def test_a_timed_out_run_is_fail_not_pass(self):
        self.assertEqual(judge(listing(run("timed_out"))).state, mg.FAIL)

    def test_a_cancelled_run_is_looked_past(self):
        self.assertEqual(judge(listing(run("cancelled"), run("failure", 5))).state, mg.FAIL)
        self.assertEqual(judge(listing(run("cancelled"), run("success", 5))).state, mg.PASS)

    def test_the_listing_order_is_not_trusted(self):
        self.assertEqual(judge(listing(run("success", 50), run("failure", 1))).state, mg.FAIL)

    def test_no_run_at_all_is_unknown_not_green(self):
        self.assertEqual(judge(listing()).state, mg.UNKNOWN)

    def test_only_in_flight_or_cancelled_runs_is_unknown(self):
        v = judge(listing(run(None, status="in_progress"), run("cancelled")))
        self.assertEqual(v.state, mg.UNKNOWN)

    def test_a_listing_without_workflow_runs_is_unknown(self):
        for payload in ({}, None, [], {"message": "Not Found"}):
            self.assertEqual(judge(payload).state, mg.UNKNOWN, payload)

    def test_an_old_green_run_while_main_moved_is_unknown(self):
        v = judge(listing(run(age_hours=24 * 4, sha="b" * 40)))
        self.assertEqual(v.state, mg.UNKNOWN)
        self.assertIn("no run", v.detail)

    def test_an_old_green_run_is_pass_when_main_has_not_moved(self):
        self.assertEqual(judge(listing(run(age_hours=24 * 4, sha=HEAD))).state, mg.PASS)

    def test_a_fresh_head_is_inside_the_grace(self):
        v = judge(listing(run(age_hours=24 * 4, sha="b" * 40)), head_age_hours=1.0)
        self.assertEqual(v.state, mg.PASS)

    def test_an_in_flight_run_on_the_head_counts_as_main_being_covered(self):
        v = judge(listing(run(None, 0.1, HEAD, "in_progress"),
                          run(age_hours=24 * 4, sha="b" * 40)))
        self.assertEqual(v.state, mg.PASS)

    def test_a_path_filtered_workflow_is_not_aged(self):
        wf = mg.Workflow("deploy-plugin.yml", "deploy", max_age_days=None)
        self.assertEqual(judge(listing(run(age_hours=24 * 30, sha="b" * 40)), wf=wf).state,
                         mg.PASS)
        self.assertEqual(judge(listing(), wf=wf).state, mg.UNKNOWN)

    def test_an_unreadable_date_is_unknown(self):
        bad = run()
        bad["updated_at"] = bad["created_at"] = "yesterday"
        self.assertEqual(judge(listing(bad)).state, mg.UNKNOWN)


class CheckTests(unittest.TestCase):
    def test_a_failed_read_is_unknown_for_that_workflow_only(self):
        api = FakeApi({"tests.yml": mg.ReadError("HTTP 502"), "b.yml": listing(run())})
        got = mg.check(REPO, api, NOW, [WF, mg.Workflow("b.yml", "b")])
        self.assertEqual([v.state for v in got], [mg.UNKNOWN, mg.PASS])

    def test_an_unreadable_head_makes_everything_unknown(self):
        def api(path, **_):
            raise mg.ReadError("no network")
        got = mg.check(REPO, api, NOW, [WF])
        self.assertEqual([v.state for v in got], [mg.UNKNOWN])

    def test_unknown_never_exits_zero(self):
        api = FakeApi({"tests.yml": listing()})
        self.assertEqual(mg.exit_code(mg.check(REPO, api, NOW, [WF])), 3)

    def test_exit_codes(self):
        V = mg.Verdict
        self.assertEqual(mg.exit_code([V("a", mg.PASS, "")]), 0)
        self.assertEqual(mg.exit_code([V("a", mg.PASS, ""), V("b", mg.FAIL, "")]), 1)
        self.assertEqual(mg.exit_code([V("a", mg.UNKNOWN, ""), V("b", mg.FAIL, "")]), 1)
        self.assertEqual(mg.exit_code([V("a", mg.PASS, ""), V("b", mg.UNKNOWN, "")]), 3)
        self.assertEqual(mg.exit_code([]), 3, "nothing judged is not green")
        self.assertEqual(mg.exit_code([V("a", "GREENISH", "")]), 3)

    def test_the_configured_list_is_real(self):
        root = Path(mg.__file__).resolve()
        while not (root / ".github").is_dir():
            root = root.parent
        for wf in mg.WORKFLOWS:
            self.assertTrue((root / ".github" / "workflows" / wf.file).is_file(), wf.file)
        self.assertGreaterEqual(len(mg.WORKFLOWS), 2)


class IssueTests(unittest.TestCase):
    RED = [mg.Verdict("tests.yml", mg.FAIL, "red on 1234"), mg.Verdict("b.yml", mg.PASS, "")]
    GREEN = [mg.Verdict("tests.yml", mg.PASS, ""), mg.Verdict("b.yml", mg.PASS, "")]

    def test_the_first_red_run_opens_exactly_one_issue(self):
        api = FakeApi({})
        self.assertIn("opened", mg.sync_issue(REPO, self.RED, api, NOW))
        self.assertEqual(len(api.issues), 1)
        self.assertEqual(api.issues[0]["title"], mg.ISSUE_TITLE)
        self.assertIn(mg.MARKER, api.issues[0]["body"])

    def test_the_same_trouble_next_day_writes_nothing(self):
        api = FakeApi({})
        mg.sync_issue(REPO, self.RED, api, NOW)
        api.calls.clear()
        same_set = [mg.Verdict("tests.yml", mg.FAIL, "red on 9999, another sha"), self.RED[1]]
        msg = mg.sync_issue(REPO, same_set, api, NOW + timedelta(days=1))
        self.assertIn("left alone", msg)
        self.assertEqual(api.writes(), [])
        self.assertEqual(len(api.issues), 1)

    def test_a_changed_set_updates_the_same_issue_and_opens_no_second(self):
        api = FakeApi({})
        mg.sync_issue(REPO, self.RED, api, NOW)
        worse = [self.RED[0], mg.Verdict("b.yml", mg.UNKNOWN, "no run")]
        self.assertIn("updated", mg.sync_issue(REPO, worse, api, NOW))
        self.assertEqual(len(api.issues), 1)
        self.assertIn("b.yml` | UNKNOWN", api.issues[0]["body"])

    def test_fail_and_unknown_are_different_sets(self):
        a = mg.problem_set([mg.Verdict("t", mg.FAIL, "")])
        b = mg.problem_set([mg.Verdict("t", mg.UNKNOWN, "")])
        self.assertNotEqual(a, b)
        self.assertEqual(mg.problem_set(self.GREEN), "")

    def test_all_pass_closes_the_issue(self):
        api = FakeApi({})
        mg.sync_issue(REPO, self.RED, api, NOW)
        self.assertIn("closed", mg.sync_issue(REPO, self.GREEN, api, NOW))
        self.assertEqual(api.issues[0]["state"], "closed")

    def test_all_pass_with_no_issue_writes_nothing(self):
        api = FakeApi({})
        mg.sync_issue(REPO, self.GREEN, api, NOW)
        self.assertEqual(api.writes(), [])

    def test_someone_elses_issue_and_pull_requests_are_never_touched(self):
        api = FakeApi({}, issues=[
            {"number": 1, "state": "open", "body": "Main is not green, says a person"},
            {"number": 2, "state": "open", "body": mg.MARKER, "pull_request": {}}])
        mg.sync_issue(REPO, self.GREEN, api, NOW)
        self.assertEqual(api.writes(), [])

    def test_an_unreadable_issue_list_raises_rather_than_opening_a_duplicate(self):
        def api(path, **_):
            return {"message": "rate limited"}
        with self.assertRaises(mg.ReadError):
            mg.sync_issue(REPO, self.RED, api, NOW)


class OfflineTests(unittest.TestCase):
    def test_gh_json_turns_every_bad_read_into_a_read_error(self):
        for rc, out in ((1, ""), (0, ""), (0, "   "), (0, "<html>")):
            proc = mock.Mock(returncode=rc, stdout=out, stderr="boom")
            with mock.patch.object(mg.subprocess, "run", return_value=proc):
                with self.assertRaises(mg.ReadError):
                    mg.gh_json("repos/o/r")
        with mock.patch.object(mg.subprocess, "run", side_effect=OSError("no gh")):
            with self.assertRaises(mg.ReadError):
                mg.gh_json("repos/o/r")

    def test_main_is_red_when_gh_answers_nothing(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(mg.subprocess, "run", return_value=proc), \
                mock.patch("builtins.print"):
            self.assertEqual(mg.main([]), 3)


if __name__ == "__main__":
    unittest.main()

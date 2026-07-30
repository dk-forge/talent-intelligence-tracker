"""The tool that says whether Actions is red must never say it wrongly.

Two failure shapes are worth more than the happy path here, and both are ones
this project has already shipped:

1. **"I could not check" rendering as "everything is fine."** No gh, no
   credential, no network — every one of those must exit 3 and say so, never
   exit 0 over an empty list. The whole reason ci_status exists is that a
   session read an all-clear beside a red repo.

2. **An alarm nobody reads.** Twenty-four historical evictions in the ACTION
   NEEDED list is the same as no alarm at all. An eviction counts when it hit a
   DATABASE WRITER, inside the window, and the writer queue has not already
   booked it — `drain-writers` losing its own pending slot costs one cycle and
   no data, and its own concurrency comment says so.

Everything below is offline. `assess()` is pure by design; the CLI is exercised
by replacing `subprocess.run` on the real module and putting it back, never by
installing a fake module into sys.modules.
"""

from __future__ import annotations

import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import ci_status
import writer_queue_runs

NOW = datetime(2026, 7, 30, 22, 0, tzinfo=timezone.utc)


def run(workflow="collect", *, conclusion="failure", hours_ago=1, run_id=1,
        branch="main", jobs=None, status="completed"):
    return {
        "databaseId": run_id,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "createdAt": (NOW - timedelta(hours=hours_ago)).isoformat().replace(
            "+00:00", "Z"),
        "headBranch": branch,
        "url": f"https://github.com/x/y/actions/runs/{run_id}",
        **({} if jobs is None else {"job_count": jobs}),
    }


class RedTests(unittest.TestCase):
    def test_the_three_red_conclusions(self):
        for conclusion in ("failure", "timed_out", "startup_failure"):
            self.assertTrue(ci_status.is_red(run(conclusion=conclusion)))

    def test_success_and_cancelled_are_not_red(self):
        # A cancelled run is reported through the eviction path instead, where
        # the job count decides. Calling it red here would flag every backfill
        # anyone ever stopped by hand.
        for conclusion in ("success", "cancelled", "skipped", None):
            self.assertFalse(ci_status.is_red(run(conclusion=conclusion)))


class EvictionSignatureTests(unittest.TestCase):
    def test_cancelled_with_zero_jobs_is_the_eviction(self):
        self.assertTrue(ci_status.was_evicted(
            run(conclusion="cancelled", jobs=0)))

    def test_cancelled_after_doing_work_is_a_human_pressing_stop(self):
        self.assertFalse(ci_status.was_evicted(
            run(conclusion="cancelled", jobs=3)))

    def test_an_unreadable_job_count_is_not_an_eviction(self):
        # Unknown is not zero. Claiming an eviction on a failed API call would
        # invent an incident, and the run list already carries None for it.
        self.assertFalse(ci_status.was_evicted(
            run(conclusion="cancelled", jobs=None)))
        self.assertFalse(ci_status.was_evicted({"conclusion": "cancelled"}))

    def test_a_failure_is_not_an_eviction(self):
        self.assertFalse(ci_status.was_evicted(run(conclusion="failure")))


class StillRedTests(unittest.TestCase):
    """RED NOW is about the newest run, not about the failure."""

    def _assess(self, **kwargs):
        base = dict(failures=[], cancelled=[], latest={}, default_branch="main",
                    lock_group=set(), already_recorded=set(), now=NOW)
        base.update(kwargs)
        return ci_status.assess("owner/repo", **base)

    def test_a_failure_with_nothing_green_after_it_is_red_now(self):
        failed = run("tests", run_id=7, hours_ago=3)
        report = self._assess(failures=[failed], latest={"tests": failed})
        self.assertEqual([r["databaseId"] for r in report["red_now"]], [7])
        self.assertTrue(any("tests is RED on main" in p
                            for p in report["problems"]))

    def test_a_failure_somebody_already_fixed_is_not_an_alarm(self):
        report = self._assess(
            failures=[run("tests", run_id=7, hours_ago=3)],
            latest={"tests": run("tests", conclusion="success", run_id=9,
                                 hours_ago=1)})
        self.assertEqual(report["red_now"], [])
        self.assertEqual(report["recovered"], ["tests"])
        self.assertEqual(report["problems"], [])

    def test_a_state_that_could_not_be_read_is_not_a_recovery(self):
        report = self._assess(failures=[run("tests")], latest={"tests": None})
        self.assertEqual(report["unknown"], ["tests"])
        self.assertTrue(any("COULD NOT BE READ" in p
                            for p in report["problems"]))

    def test_a_red_run_stays_red_however_old_it_is(self):
        # The window bounds the "and these also failed" list. It must NOT bound
        # this: a dispatch-only workflow that failed a week ago and has not run
        # since is exactly the thing nobody notices.
        old = run("recall", run_id=4, hours_ago=200)
        report = self._assess(failures=[old], latest={"recall": old})
        self.assertEqual(len(report["red_now"]), 1)
        self.assertEqual(report["recent"], [])

    def test_a_failure_on_a_side_branch_is_listed_but_not_red(self):
        report = self._assess(
            failures=[run("tests", branch="claude/experiment", run_id=5)],
            latest={})
        self.assertEqual(report["red_now"], [])
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["off_branch"], ["tests"])
        self.assertEqual(len(report["recent"]), 1)

    def test_the_default_branch_is_whatever_the_repo_says(self):
        failed = run("tests", branch="trunk", run_id=5)
        report = self._assess(failures=[failed], latest={"tests": failed},
                              default_branch="trunk")
        self.assertEqual(len(report["red_now"]), 1)


class EvictionTriageTests(unittest.TestCase):
    """Which evictions are worth waking somebody for."""

    WRITERS = {"collect", "correct-form-d", "backfill-gdelt-2026"}

    def _assess(self, cancelled, **kwargs):
        base = dict(failures=[], latest={}, default_branch="main",
                    lock_group=self.WRITERS, already_recorded=set(), now=NOW)
        base.update(kwargs)
        return ci_status.assess("owner/repo", cancelled=cancelled, **base)

    def test_an_evicted_writer_is_an_alarm(self):
        report = self._assess([run("correct-form-d", conclusion="cancelled",
                                   jobs=0, run_id=11, hours_ago=2)])
        self.assertEqual([r["databaseId"] for r in report["lost"]], [11])
        self.assertTrue(any("ZERO jobs" in p for p in report["problems"]))
        self.assertTrue(any("re-dispatching with defaults" in p
                            for p in report["problems"]))

    def test_a_drainer_losing_its_own_slot_is_not(self):
        # drain-writers is deliberately NOT in the writers' lock group, and its
        # own concurrency comment says a lost tick costs a cycle and no data.
        report = self._assess([run("drain-writers", conclusion="cancelled",
                                   jobs=0, run_id=12, hours_ago=2)])
        self.assertEqual(report["lost"], [])
        self.assertEqual(len(report["benign"]), 1)
        self.assertEqual(report["problems"], [])

    def test_an_eviction_the_queue_already_booked_is_left_to_ops_status(self):
        report = self._assess(
            [run("collect", conclusion="cancelled", jobs=0, run_id=13)],
            already_recorded={"13"})
        self.assertEqual(report["lost"], [])
        self.assertEqual(len(report["recorded"]), 1)
        self.assertEqual(report["problems"], [])

    def test_an_eviction_outside_the_window_is_counted_not_shouted(self):
        report = self._assess([run("collect", conclusion="cancelled", jobs=0,
                                   run_id=14, hours_ago=48)])
        self.assertEqual(report["lost"], [])
        self.assertEqual(report["older_cancelled"], 1)

    def test_an_unknown_repo_treats_every_eviction_as_real(self):
        # The sibling's lock groups are not knowable from here — the two repos
        # share no code — so silence there would be a guess in the wrong
        # direction.
        report = self._assess([run("EDGAR sweep", conclusion="cancelled",
                                   jobs=0, run_id=15, hours_ago=2)],
                              lock_group=None)
        self.assertEqual(len(report["lost"]), 1)


class _FakeGh:
    """subprocess.run, answering by argv. Installed on the real module and
    removed again; nothing is put into sys.modules."""

    def __init__(self, *, failures=(), cancelled=(), latest=None, jobs=0,
                 missing=False, stderr=None):
        self.failures = list(failures)
        self.cancelled = list(cancelled)
        self.latest = latest or {}
        self.jobs = jobs
        self.missing = missing
        self.stderr = stderr

    def __call__(self, argv, capture_output, text):
        if self.missing:
            raise FileNotFoundError(2, "No such file or directory: 'gh'")
        if self.stderr:
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr=self.stderr)
        out = "[]"
        if argv[1] == "repo":
            out = "main\n"
        elif argv[1] == "api":
            out = str(self.jobs)
        elif "--status" in argv:
            status = argv[argv.index("--status") + 1]
            if status == "failure":
                out = json.dumps(self.failures)
            elif status == "cancelled":
                out = json.dumps(self.cancelled)
        elif "-w" in argv:
            out = json.dumps([r for r in [self.latest.get(argv[argv.index("-w") + 1])]
                              if r])
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._real = writer_queue_runs.subprocess.run
        self._real_sleep = writer_queue_runs.time.sleep
        writer_queue_runs.time.sleep = lambda _s: None

    def tearDown(self):
        writer_queue_runs.subprocess.run = self._real
        writer_queue_runs.time.sleep = self._real_sleep

    def _main(self, fake, *args):
        writer_queue_runs.subprocess.run = fake
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = ci_status.main(["--repo", "owner/repo", "--no-queue", *args])
        return code, buffer.getvalue()

    def test_a_quiet_repo_is_green(self):
        code, out = self._main(_FakeGh())
        self.assertEqual(code, 0)
        self.assertIn("All green", out)

    def test_a_red_workflow_exits_two_like_ops_status(self):
        failed = run("tests", run_id=7, hours_ago=2)
        code, out = self._main(_FakeGh(failures=[failed],
                                       latest={"tests": failed}))
        self.assertEqual(code, 2)
        self.assertIn("ACTION NEEDED", out)
        self.assertIn("tests", out)

    def test_no_gh_exits_three_and_never_says_green(self):
        code, out = self._main(_FakeGh(missing=True))
        self.assertEqual(code, 3)
        self.assertIn("COULD NOT CHECK", out)
        self.assertNotIn("All green", out)

    def test_no_credential_exits_three_and_never_says_green(self):
        code, out = self._main(_FakeGh(stderr="gh auth login required"))
        self.assertEqual(code, 3)
        self.assertIn("COULD NOT CHECK", out)
        self.assertNotIn("All green", out)

    def test_no_network_exits_three_and_never_says_green(self):
        code, out = self._main(
            _FakeGh(stderr="dial tcp: lookup api.github.com: no such host"))
        self.assertEqual(code, 3)
        self.assertIn("COULD NOT CHECK", out)
        self.assertNotIn("All green", out)

    def test_the_three_exit_codes_are_distinct(self):
        self.assertEqual(len({0, 2, 3}), 3)


class GhUnavailableTests(unittest.TestCase):
    """The distinction ci_status's exit 3 rests on, at its source."""

    def setUp(self):
        self._real = writer_queue_runs.subprocess.run
        self._real_sleep = writer_queue_runs.time.sleep
        writer_queue_runs.time.sleep = lambda _s: None

    def tearDown(self):
        writer_queue_runs.subprocess.run = self._real
        writer_queue_runs.time.sleep = self._real_sleep

    def _install(self, stderr=None, missing=False):
        def fake(argv, capture_output, text):
            if missing:
                raise FileNotFoundError(2, "no gh")
            return types.SimpleNamespace(returncode=1, stdout="", stderr=stderr)
        writer_queue_runs.subprocess.run = fake

    def test_a_missing_gh_is_a_message_and_not_a_traceback(self):
        self._install(missing=True)
        with self.assertRaises(writer_queue_runs.GhUnavailable) as caught:
            writer_queue_runs._gh(["run", "list"])
        self.assertIn("not installed", str(caught.exception))

    def test_an_unauthenticated_gh_is_unavailable_not_a_bad_call(self):
        self._install("HTTP 401: Bad credentials")
        with self.assertRaises(writer_queue_runs.GhUnavailable):
            writer_queue_runs._gh(["run", "list"])

    def test_a_real_server_error_stays_an_ordinary_failure(self):
        # A 502 that never clears is GitHub being unwell on a call we made
        # correctly. It is a RuntimeError, not "gh is unavailable", so the
        # drainer's existing handling is untouched.
        self._install("HTTP 502: Server Error")
        with self.assertRaises(RuntimeError) as caught:
            writer_queue_runs._gh(["run", "list"])
        self.assertNotIsInstance(caught.exception, writer_queue_runs.GhUnavailable)

    def test_gh_unavailable_is_still_a_runtime_error(self):
        # Every existing caller catches RuntimeError. The new class must not
        # slip past them.
        self.assertTrue(issubclass(writer_queue_runs.GhUnavailable, RuntimeError))


class TheRitualTests(unittest.TestCase):
    """A check nobody is told to run is a check nobody runs."""

    def _root(self):
        from pathlib import Path
        return Path(ci_status.__file__).resolve().parent

    def test_claude_md_tells_every_session_to_run_it(self):
        block = (self._root() / "CLAUDE.md").read_text().split(
            "## Start here, every session", 1)[1].split("\n## ", 1)[0]
        self.assertIn("ci_status.py", block)
        self.assertIn("ops_status.py", block)

    def test_it_needs_no_venv_either(self):
        # ops_status is stdlib-only because it must run before any venv exists.
        # The command standing beside it in the same ritual has to clear the
        # same bar, or half the ritual fails on a fresh checkout.
        import ast

        tree = ast.parse((self._root() / "ci_status.py").read_text())
        imported = {
            name.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for name in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        self.assertEqual(imported - set(sys.stdlib_module_names)
                         - {"writer_queue", "writer_queue_runs"}, set())


if __name__ == "__main__":
    unittest.main()

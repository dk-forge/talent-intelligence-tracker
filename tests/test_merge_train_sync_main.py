"""The train starts on main what a token merge cannot start.

The train merges with the default Actions token, and GitHub starts no
`on: push` workflow for a push made with that token. So after a train merge
tests.yml, card-contract.yml and style-standard.yml never ran on main and
main's colour was UNKNOWN. `sync_main` reconciles that on every tick.

It ports the TESTS half of the sibling tracker's fix only. The plugin deploy
is never dispatched from here, and these tests hold that.
"""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import merge_train as mt  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
HEAD = "c" * 40
WORKFLOWS = ("tests.yml", "card-contract.yml", "style-standard.yml")


def cfg(**over):
    base = {"repo": "o/r", "min_checks": 3, "deploy_workflow": "deploy-plugin.yml",
            "plugin_paths": ["wordpress-plugin/"],
            "post_merge_workflows": list(WORKFLOWS)}
    base.update(over)
    return mt.Config.from_dict(base)


class FakeMain:
    def __init__(self, head_age_minutes=60, has_run=(), head_error=None,
                 dispatch_fails=(), read_fails=()):
        self.head_at = NOW - timedelta(minutes=head_age_minutes)
        self.has_run = set(has_run)
        self.head_error = head_error
        self.dispatch_fails = set(dispatch_fails)
        self.read_fails = set(read_fails)
        self.dispatched = []
        self.writes = []

    def main_head(self, branch):
        if self.head_error:
            raise mt.MergeTrainError(self.head_error)
        return HEAD, self.head_at

    def workflow_has_run_for(self, wf, sha):
        assert sha == HEAD
        if wf in self.read_fails:
            raise mt.MergeTrainError(f"{wf} runs response had no total_count (NOREAD)")
        return wf in self.has_run

    def dispatch(self, wf, ref):
        if wf in self.dispatch_fails:
            raise mt.MergeTrainError("HTTP 403: Resource not accessible by integration")
        self.dispatched.append((wf, ref))

    # Anything that would undo a merge or deploy must never be reached.
    def __getattr__(self, name):
        raise AssertionError(f"sync_main reached for client.{name}")


def sync(client, *, dry_run=False, merged=(), **over):
    rep = mt.Report(dry_run=dry_run)
    rep.merged = list(merged)
    with redirect_stdout(io.StringIO()):
        problems = mt.sync_main(client, cfg(**over), rep, dry_run=dry_run, now=NOW)
    return rep, problems


class TheReconcile(unittest.TestCase):

    def test_a_head_with_no_runs_gets_every_listed_workflow_dispatched_on_main(self):
        c = FakeMain()
        rep, problems = sync(c)
        self.assertEqual(c.dispatched, [(w, "main") for w in WORKFLOWS])
        self.assertEqual(rep.dispatched, list(WORKFLOWS))
        self.assertEqual(problems, [])

    def test_a_workflow_that_already_ran_on_the_head_is_left_alone(self):
        c = FakeMain(has_run={"tests.yml", "style-standard.yml"})
        rep, problems = sync(c)
        self.assertEqual(c.dispatched, [("card-contract.yml", "main")])
        self.assertEqual(problems, [])

    def test_a_head_with_every_run_dispatches_nothing(self):
        c = FakeMain(has_run=set(WORKFLOWS))
        rep, problems = sync(c)
        self.assertEqual((c.dispatched, rep.dispatched, problems), ([], [], []))

    def test_a_fresh_head_the_train_did_not_make_is_left_to_start_its_own_runs(self):
        c = FakeMain(head_age_minutes=9)
        rep, problems = sync(c)
        self.assertEqual(c.dispatched, [])
        self.assertEqual(problems, [])
        self.assertTrue(any("waiting" in ln for ln in rep.lines))

    def test_a_head_exactly_at_the_grace_is_settled(self):
        c = FakeMain(head_age_minutes=10)
        sync(c)
        self.assertEqual(len(c.dispatched), 3)

    def test_a_fresh_head_the_train_made_itself_is_dispatched_at_once(self):
        c = FakeMain(head_age_minutes=0)
        rep, problems = sync(c, merged=[41])
        self.assertEqual(c.dispatched, [(w, "main") for w in WORKFLOWS])
        self.assertEqual(problems, [])

    def test_a_dry_run_says_would_dispatch_and_dispatches_nothing(self):
        c = FakeMain()
        rep, problems = sync(c, dry_run=True)
        self.assertEqual(c.dispatched, [])
        self.assertEqual(rep.dispatched, [])
        self.assertEqual(problems, [])
        for w in WORKFLOWS:
            self.assertTrue(any(f"WOULD DISPATCH {w}" in ln for ln in rep.lines), w)

    def test_no_listed_workflows_means_no_read_and_no_dispatch(self):
        c = FakeMain(head_error="must not be asked")
        rep, problems = sync(c, post_merge_workflows=[])
        self.assertEqual((c.dispatched, problems), ([], []))


class TheFailuresAreLoud(unittest.TestCase):

    def test_a_failed_dispatch_is_a_problem_and_the_others_still_go(self):
        c = FakeMain(dispatch_fails={"tests.yml"})
        rep, problems = sync(c)
        self.assertEqual(len(problems), 1)
        self.assertIn("tests.yml", problems[0])
        self.assertIn("403", problems[0])
        self.assertEqual([w for w, _ in c.dispatched],
                         ["card-contract.yml", "style-standard.yml"])
        self.assertNotIn("tests.yml", rep.dispatched)

    def test_an_unreadable_run_list_is_unknown_never_zero_runs(self):
        c = FakeMain(read_fails={"card-contract.yml"})
        rep, problems = sync(c)
        self.assertEqual(len(problems), 1)
        self.assertIn("UNKNOWN", problems[0])
        self.assertNotIn("card-contract.yml", [w for w, _ in c.dispatched])

    def test_an_unreadable_main_is_a_problem_and_dispatches_nothing(self):
        c = FakeMain(head_error="could not read the head of main (NOREAD)")
        rep, problems = sync(c)
        self.assertEqual(c.dispatched, [])
        self.assertEqual(len(problems), 1)

    def test_sync_main_never_raises_and_never_touches_a_merge(self):
        # FakeMain raises AssertionError on any other client method, so a
        # revert, a re-run or a deploy read would fail this test.
        c = FakeMain(dispatch_fails=set(WORKFLOWS))
        rep, problems = sync(c, merged=[7])
        self.assertEqual(len(problems), 3)
        self.assertEqual(rep.merged, [7])


class TheExitCode(unittest.TestCase):
    """main() goes red with ::error:: when a dispatch fails, after the merge."""

    def _main(self, client, rep, dry="false"):
        out = io.StringIO()
        with mock.patch.object(mt, "GitHubClient", return_value=client), \
             mock.patch.object(mt, "run", return_value=rep), \
             mock.patch.dict("os.environ", {"MERGE_TRAIN_DRY_RUN": dry,
                                            "GITHUB_WORKSPACE": str(ROOT),
                                            "GITHUB_STEP_SUMMARY": ""}), \
             mock.patch.object(mt, "_write_summary"), \
             redirect_stdout(out):
            code = mt.main([])
        return code, out.getvalue()

    def test_a_failed_dispatch_exits_non_zero_with_an_error_annotation(self):
        rep = mt.Report(dry_run=False)
        rep.merged = [12]
        rep.action = "merged #12"
        c = FakeMain(dispatch_fails={"tests.yml"})
        code, out = self._main(c, rep)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"::error::merge train sync main: tests\.yml")
        self.assertEqual(rep.merged, [12])

    def test_a_clean_sync_exits_zero(self):
        rep = mt.Report(dry_run=False)
        rep.merged = [12]
        c = FakeMain()
        code, out = self._main(c, rep)
        self.assertEqual(code, 0)
        self.assertNotIn("::error::", out)
        self.assertEqual(len(c.dispatched), 3)

    def test_a_dry_run_exits_zero_and_dispatches_nothing(self):
        rep = mt.Report(dry_run=True)
        c = FakeMain()
        code, out = self._main(c, rep, dry="true")
        self.assertEqual(code, 0)
        self.assertEqual(c.dispatched, [])
        self.assertIn("WOULD DISPATCH tests.yml", out)


class TheGhCalls(unittest.TestCase):
    """The real client, with gh mocked."""

    def test_dispatch_runs_gh_workflow_run_on_the_ref(self):
        client = mt.GitHubClient("o/r", ROOT)
        with mock.patch.object(mt, "_run") as run:
            client.dispatch("tests.yml", "main")
        run.assert_called_once_with(
            ["gh", "workflow", "run", "tests.yml", "-R", "o/r", "--ref", "main"])

    def test_a_gh_failure_surfaces_as_a_merge_train_error(self):
        client = mt.GitHubClient("o/r", ROOT)
        failed = mock.Mock(returncode=1, stdout="", stderr="HTTP 403")
        with mock.patch.object(mt.subprocess, "run", return_value=failed):
            with self.assertRaises(mt.MergeTrainError):
                client.dispatch("tests.yml", "main")

    def test_has_run_reads_the_head_sha_and_counts(self):
        client = mt.GitHubClient("o/r", ROOT)
        with mock.patch.object(client, "_api", return_value={"total_count": 0}) as api:
            self.assertFalse(client.workflow_has_run_for("tests.yml", HEAD))
        self.assertIn(f"head_sha={HEAD}", api.call_args[0][0])
        with mock.patch.object(client, "_api", return_value={"total_count": 2}):
            self.assertTrue(client.workflow_has_run_for("tests.yml", HEAD))

    def test_an_empty_read_is_not_zero_runs(self):
        client = mt.GitHubClient("o/r", ROOT)
        for empty in ({}, [], None):
            with mock.patch.object(client, "_api", return_value=empty):
                with self.assertRaises(mt.MergeTrainError):
                    client.workflow_has_run_for("tests.yml", HEAD)

    def test_an_unreadable_head_is_noread(self):
        client = mt.GitHubClient("o/r", ROOT)
        with mock.patch.object(client, "_api", return_value={"sha": "", "commit": {}}):
            with self.assertRaises(mt.MergeTrainError):
                client.main_head("main")


class TheShippedWiring(unittest.TestCase):

    def setUp(self):
        self.raw = json.loads((ROOT / ".github" / "merge-train.json").read_text())
        self.cfg = mt.Config.from_dict(self.raw)

    def test_the_three_main_workflows_are_listed(self):
        self.assertEqual(set(self.cfg.post_merge_workflows), set(WORKFLOWS))

    def test_every_listed_workflow_exists_and_declares_workflow_dispatch(self):
        for wf in self.cfg.post_merge_workflows:
            text = (ROOT / ".github" / "workflows" / wf).read_text()
            self.assertRegex(text, r"(?m)^  workflow_dispatch:", wf)

    def test_the_deploy_is_never_a_post_merge_workflow(self):
        self.assertNotIn(self.cfg.deploy_workflow, self.cfg.post_merge_workflows)
        with self.assertRaises(mt.MergeTrainError):
            mt.Config.from_dict({**self.raw, "post_merge_workflows":
                                 ["tests.yml", self.raw["deploy_workflow"]]})

    def test_the_train_never_names_a_deploy_dispatch(self):
        src = (ROOT / "merge_train.py").read_text()
        self.assertNotIn("dispatch(cfg.deploy_workflow", src)

    def test_the_train_may_dispatch(self):
        text = (ROOT / ".github" / "workflows" / "merge-train.yml").read_text()
        self.assertTrue(re.search(r"(?m)^  actions: write", text))


if __name__ == "__main__":
    unittest.main()

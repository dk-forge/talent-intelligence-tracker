"""A run dropped from the concurrency queue must be told apart from a cancel.

Both directions are load-bearing. Reporting every `cancelled` run would flag
the deliberate ones too, and a watchdog that cries wolf gets filtered within a
month. Missing the silent ones is the incident of 2026-07-29, where thirteen
dispatched writers evaporated while a backfill held the lock and nothing
anywhere said so.

Written with unittest so `python3 -m unittest discover -s tests` runs it with
no third-party packages installed. pytest collects it just the same.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dropped_runs  # noqa: E402

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=timezone.utc)


def iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def run(**kwargs):
    base = {
        "id": 1,
        "name": "correct-sec-pillar",
        "path": ".github/workflows/correct-sec-pillar.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "cancelled",
        "created_at": iso(1),
        "run_started_at": iso(1),
        "updated_at": iso(0.8),
        "html_url": "https://example.invalid/1",
    }
    base.update(kwargs)
    return base


class TestExecutedAnything(unittest.TestCase):
    """The discriminator, checked against the shapes the real API returned."""

    def test_a_queue_eviction_has_no_jobs_at_all(self):
        # Every one of the 2026-07-29 drops reported total_count 0.
        self.assertFalse(dropped_runs.executed_anything([]))

    def test_a_job_that_never_started_is_not_execution(self):
        self.assertFalse(dropped_runs.executed_anything(
            [{"name": "correct", "started_at": None, "steps": []}]))

    def test_a_started_job_with_steps_is_execution(self):
        # The backfill holding the lock: one job, ten steps.
        self.assertTrue(dropped_runs.executed_anything(
            [{"name": "backfill", "started_at": iso(2),
              "steps": [{"name": "Set up job"}] * 10}]))

    def test_a_human_cancelling_a_running_job_is_not_reported(self):
        """It gets as far as "Set up job", so it is a decision, not a drop."""
        self.assertTrue(dropped_runs.executed_anything(
            [{"name": "collect", "started_at": iso(2),
              "steps": [{"name": "Set up job", "conclusion": "success"}]}]))


class TestLockHolder(unittest.TestCase):
    def test_the_run_spanning_the_drop_is_named(self):
        dropped = run(id=30428223266, created_at=iso(1), updated_at=iso(0.8))
        holder = run(id=30423752001, name="backfill-gdelt-2026",
                     path=".github/workflows/backfill-gdelt-2026.yml",
                     status="in_progress", conclusion=None,
                     run_started_at=iso(2), updated_at=iso(2))
        found = dropped_runs.overlapping_holder(dropped, [dropped, holder], NOW)
        self.assertEqual(found["id"], 30423752001)

    def test_an_in_progress_run_is_still_holding_it_now(self):
        """Its updated_at stops moving; only `status` says it is still going."""
        dropped = run(created_at=iso(0.5), updated_at=iso(0.4))
        holder = run(id=99, status="in_progress", conclusion=None,
                     run_started_at=iso(6), updated_at=iso(6))
        self.assertIsNotNone(
            dropped_runs.overlapping_holder(dropped, [dropped, holder], NOW))

    def test_a_run_that_finished_first_is_not_the_holder(self):
        dropped = run(created_at=iso(1), updated_at=iso(0.8))
        earlier = run(id=99, conclusion="success",
                      run_started_at=iso(9), updated_at=iso(8))
        self.assertIsNone(
            dropped_runs.overlapping_holder(dropped, [dropped, earlier], NOW))

    def test_the_longest_overlap_wins(self):
        dropped = run(created_at=iso(1), updated_at=iso(0.9))
        brief = run(id=2, conclusion="success",
                    run_started_at=iso(1.1), updated_at=iso(0.5))
        long = run(id=3, name="backfill-gdelt-2026", conclusion="success",
                   run_started_at=iso(5), updated_at=iso(0.5))
        found = dropped_runs.overlapping_holder(dropped, [dropped, brief, long], NOW)
        self.assertEqual(found["id"], 3)

    def test_no_holder_is_not_an_error(self):
        """A drop with nothing to blame is still a drop."""
        self.assertIsNone(dropped_runs.overlapping_holder(run(), [], NOW))


class TestWriterDiscovery(unittest.TestCase):
    """Derived from the workflow files, so a new writer is watched on day one."""

    def setUp(self):
        self.writers = dropped_runs.writer_workflows()

    def test_it_finds_the_writers(self):
        self.assertGreaterEqual(len(self.writers), 5, self.writers)

    def test_every_writer_it_found_is_in_one_group(self):
        groups = set(self.writers.values())
        self.assertEqual(len(groups), 1, self.writers)
        self.assertNotIn(None, groups)

    def test_the_workflows_that_actually_dropped_are_watched(self):
        for name in ("correct-sec-pillar", "enrich", "recall",
                     "collect-press", "correct-form-d", "backfill-gdelt-2026"):
            self.assertIn(f".github/workflows/{name}.yml", self.writers)

    def test_the_watchdog_does_not_watch_itself(self):
        """It writes nothing, so it must not be in the group or the list."""
        self.assertNotIn(".github/workflows/dropped-runs.yml", self.writers)

    def test_a_concurrency_group_is_read_off_a_file(self):
        text = "name: x\n\nconcurrency:\n  group: talent-collect\n  cancel-in-progress: false\n"
        self.assertEqual(dropped_runs._concurrency_group(text), "talent-collect")

    def test_no_concurrency_block_reads_as_none(self):
        self.assertIsNone(dropped_runs._concurrency_group("name: x\njobs: {}\n"))


class TestEmail(unittest.TestCase):
    def setUp(self):
        holder = run(id=30423752001, name="backfill-gdelt-2026",
                     path=".github/workflows/backfill-gdelt-2026.yml",
                     status="in_progress", conclusion=None, run_started_at=iso(3))
        self.drops = [{
            "id": 30428223266,
            "name": "correct-sec-pillar",
            "path": ".github/workflows/correct-sec-pillar.yml",
            "event": "workflow_dispatch",
            "created_at": iso(1),
            "url": "https://example.invalid/30428223266",
            "group": "talent-collect",
            "holder": holder,
        }]
        self.subject, self.body = dropped_runs.build_email(
            self.drops, "dk-forge/talent-intelligence-tracker", 26)

    def test_the_subject_names_the_workflow(self):
        self.assertIn("correct-sec-pillar", self.subject)

    def test_the_body_names_the_run_that_held_the_lock(self):
        self.assertIn("backfill-gdelt-2026", self.body)
        self.assertIn("30423752001", self.body)

    def test_it_gives_the_redispatch_command(self):
        self.assertIn("gh workflow run correct-sec-pillar.yml", self.body)

    def test_it_warns_that_the_inputs_are_not_recoverable(self):
        """A blind re-dispatch of correct-sec-pillar runs a dry run and lies."""
        self.assertIn("dry_run: true", self.body)

    def test_it_forbids_the_wrong_fix(self):
        """Whoever reads this at 2am must not delete the lock."""
        self.assertIn("Do NOT fix this by removing the shared lock", self.body)

    def test_owner_facing_copy_has_no_em_dashes(self):
        self.assertNotIn("—", self.body)
        self.assertNotIn("—", self.subject)


if __name__ == "__main__":
    unittest.main()

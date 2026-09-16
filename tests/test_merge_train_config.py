"""This repo's own merge-train wiring, pinned.

`test_merge_train.py` proves the JUDGEMENT. This proves the CONFIGURATION it
is handed here, which is the other half: a correct judge reading a wrong floor
merges on nothing.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import merge_train as mt  # noqa: E402
import self_heal  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "merge-train.json"
WORKFLOW = ROOT / ".github" / "workflows" / "merge-train.yml"


class TheWiring(unittest.TestCase):

    def setUp(self):
        self.raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.cfg = mt.Config.load(CONFIG)

    def test_the_config_loads(self):
        self.assertEqual(self.cfg.repo, "dk-forge/talent-intelligence-tracker")

    def test_the_floor_is_derived_and_is_not_one(self):
        """MEASURED 2026-09-16: 7 check runs on every one of the 30 most
        recently merged pull requests. The floor sits under that so a
        path-filtered absence cannot wedge the queue, and far over the 0 to 2
        a pull request shows while CI is still starting."""
        self.assertGreaterEqual(self.cfg.min_checks, 3)
        self.assertLessEqual(self.cfg.min_checks, 7)

    def test_the_deploy_window_is_armed(self):
        """A plugin pull request merged on top of a fresh deploy is how the
        shared host has been taken down."""
        self.assertTrue(self.cfg.plugin_paths)
        self.assertEqual(self.cfg.deploy_workflow, "deploy-plugin.yml")
        self.assertGreaterEqual(self.cfg.deploy_window_minutes, 60)
        self.assertTrue((ROOT / ".github" / "workflows" /
                         self.cfg.deploy_workflow).exists())

    def test_every_self_heal_forbidden_path_is_forbidden_here_too(self):
        """ONE DEFINITION OF WHAT MAY NOT BE TOUCHED. The healer's list is the
        older one; if it grows an entry, this fails until the train has it."""
        missing = [p for p in self_heal.FORBIDDEN
                   if p not in self.cfg.forbidden_paths]
        self.assertEqual(missing, [],
                         "self_heal.FORBIDDEN has paths the merge train would "
                         "not refuse")

    def test_the_mechanical_set_holds_no_program_logic(self):
        """The train's whole licence is bookkeeping. A .py, .php or .js file
        in the mechanical set would make it a bot that edits code."""
        for path in self.cfg.mechanical_paths:
            self.assertFalse(path.endswith((".py", ".php", ".js", ".ts", ".tsx")),
                             f"{path} is program logic")

    def test_the_invariants_are_never_healed(self):
        for name in ("tripwire", "money-basis-check", "recall", "landmarks"):
            with self.subTest(name):
                self.assertTrue(mt.is_human_only(name, self.cfg), name)

    def test_the_workflow_never_uses_auto_merge(self):
        """Auto-merge landed PR #954 untested in the sandbox when no ruleset
        existed. It is banned here by inspection, not by memory."""
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--auto", text)
        self.assertNotIn("--auto", (ROOT / "merge_train.py")
                         .read_text(encoding="utf-8"))

    def test_the_workflow_runs_on_the_vps_and_is_dry_until_armed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("[self-hosted, linux, contabo]", text)
        self.assertIn("MERGE_TRAIN_ARMED", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_the_order_file_is_readable_if_it_exists(self):
        order = ROOT / ".github" / "merge-train-order.txt"
        if order.exists():
            mt.read_order_file(order.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

"""The rejection audit has to be produced, committed and PRINTED.

`analysis/recall/rejection_audit.py` has been writing `data/recall_rejection_audit.json`
since 2026-07-29 and surfaced nowhere: nothing ran it on a schedule, nothing
committed it, and `ops_status.py` -- the file CLAUDE.md tells every session to
run first -- did not mention it. A diagnosis nobody reads is not a diagnosis.

Three links in that chain, and each one is asserted here, because any one of
them breaking leaves the other two looking fine:

  1. recall.yml RUNS it, or the committed file is whatever the last human run
     produced and ages silently against a moving corpus.
  2. recall.yml COMMITS it, or it is recomputed every week and thrown away.
  3. ops_status.py PRINTS it, or the next session never learns it exists.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECALL_YML = ROOT / ".github" / "workflows" / "recall.yml"
AUDIT_JSON = ROOT / "data" / "recall_rejection_audit.json"
OPS = ROOT / "ops_status.py"


class TheWorkflowProducesAndKeepsIt(unittest.TestCase):
    def setUp(self):
        self.yml = RECALL_YML.read_text()

    def test_recall_runs_the_audit(self):
        self.assertIn("analysis.recall.rejection_audit", self.yml,
                      "recall.yml must RUN the audit, or the committed file is "
                      "whatever a human last produced by hand")
        self.assertIn("--write", self.yml,
                      "and with --write, or it prints and keeps nothing")

    def test_the_audit_is_in_the_committed_paths(self):
        line = next(l for l in self.yml.splitlines() if l.strip().startswith("paths="))
        self.assertIn("data/recall_rejection_audit.json", line,
                      "the audit has to be committed beside the measurement it "
                      "explains, or it is recomputed weekly and discarded")

    def test_the_audit_cannot_fail_the_measurement(self):
        # A lost diagnosis is much smaller than a lost recall measurement, and
        # the measurement is the point of the job.
        block = self.yml[self.yml.index("Audit why the misses"):]
        block = block[:block.index("- name:", 10)] if "- name:" in block[10:] else block
        self.assertIn("continue-on-error: true", block)

    def test_the_audit_runs_after_the_measurement(self):
        # It audits the corpus the measurement just scored. Before it, it would
        # be describing a different database than the number beside it.
        self.assertLess(self.yml.index("name: Measure"),
                        self.yml.index("Audit why the misses"))


class OpsStatusPrintsIt(unittest.TestCase):
    def test_ops_status_has_a_section_for_it(self):
        src = OPS.read_text()
        self.assertIn("recall_rejection_audit.json", src)
        self.assertIn("_report_rejection_audit", src)
        # Wired into main(), not merely defined. A defined-and-uncalled reporter
        # is the exact shape of this bug one level up.
        self.assertRegex(src, r"\n    _report_rejection_audit\(\)")

    def test_it_runs_and_names_the_four_causes(self):
        out = subprocess.run([sys.executable, str(OPS)], cwd=ROOT,
                             capture_output=True, text=True).stdout
        self.assertIn("[3c]", out)
        for cause in ("fetched_then_dropped", "outside_our_history",
                      "publisher_not_wired", "publisher_unknown",
                      "feed_read_item_missed"):
            self.assertIn(cause, out, cause)

    def test_the_zero_is_read_aloud(self):
        # The single most useful figure in the audit is a zero, and a zero in a
        # column is the easiest thing on a status page to skim past. It is only
        # printed while it IS zero, so this test moves with the data.
        if not AUDIT_JSON.exists():
            self.skipTest("no audit file")
        stages = json.loads(AUDIT_JSON.read_text()).get("stages") or {}
        out = subprocess.run([sys.executable, str(OPS)], cwd=ROOT,
                             capture_output=True, text=True).stdout
        if int(stages.get("fetched_then_dropped") or 0) == 0:
            # The sentence used to end "the corpus is young, not leaky". That
            # was the 2026-07-29 diagnosis and it stopped being true when the
            # audit learned to read the walkers' cursors: most of what looked
            # like a young corpus is days a rationed walker has since FINISHED.
            # The zero itself has not moved and is still the point, so what is
            # read aloud is the zero rather than a diagnosis that has.
            self.assertIn("no filter rejected a gold event in this set", out)
            self.assertIn("it is not the filters", out)

    def test_every_percentage_is_computed_from_the_file(self):
        if not AUDIT_JSON.exists():
            self.skipTest("no audit file")
        data = json.loads(AUDIT_JSON.read_text())
        stages, misses = data.get("stages") or {}, int(data.get("misses") or 0)
        out = subprocess.run([sys.executable, str(OPS)], cwd=ROOT,
                             capture_output=True, text=True).stdout
        block = out[out.index("[3c]"):]
        block = block[:block.index("\n[")] if "\n[" in block[4:] else block
        for key, n in stages.items():
            if key not in ("stored_unmatched", "stored_not_current"):
                want = f"{100.0 * int(n) / misses:.0f}%" if misses else "n/a"
                row = re.search(rf"^\s*{int(n)}\s+\S+\s+{re.escape(key)}\b",
                                block, re.M)
                self.assertIsNotNone(row, f"{key} is not printed")
                self.assertIn(want, row.group(0),
                              f"{key}'s share must be computed, not typed")

    def test_it_does_not_make_a_young_corpus_an_action_item(self):
        # A permanent red on a number only time can move trains the next session
        # to ignore the exit code, which is the one thing this file must not do.
        src = OPS.read_text()
        body = src[src.index("def _report_rejection_audit"):]
        body = body[:body.index("\ndef ")]
        self.assertNotIn("problems.append", body)
        self.assertIn("-> None", body.splitlines()[0])


if __name__ == "__main__":
    unittest.main()

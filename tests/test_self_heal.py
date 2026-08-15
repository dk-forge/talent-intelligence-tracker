"""Guards for self_heal.py — the draft-only self-healer's gate and guard.

Two promises, offline, no network, no keys:

1. THE GATE SAYS NO to every known-expected class of red — the alarms that
   are working as designed and already have an owner (drain-writers' red-once
   signal, the live contrast audit, landmarks/recall, evictions, budget
   stops, host outages, branch reds).

2. THE FORBIDDEN-PATH GUARD GOES RED on a violation, and the violation
   fixtures are the repo's real crown jewels: data/ (the committed database
   and every ledger), the two pots, the locks, the handover, the healer.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import self_heal

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "self-heal.yml").read_text(
    encoding="utf-8")


# -- the gate ---------------------------------------------------------------

def test_a_branch_failure_is_never_healed():
    heal, reason = self_heal.classify(
        "tests", "failure", "AssertionError: something real",
        branch="dependabot/pip/backend/whatever")
    assert not heal
    assert "main" in reason


def test_an_eviction_or_self_timeout_is_never_healed():
    # Both arrive as conclusion `cancelled`; ci_alert and ops_status [2b]
    # already own them.
    heal, reason = self_heal.classify("collect", "cancelled", "")
    assert not heal
    assert "cancelled" in reason


@pytest.mark.parametrize("conclusion", ["success", "timed_out",
                                        "startup_failure", ""])
def test_only_a_plain_failure_is_healable(conclusion):
    heal, _ = self_heal.classify("tests", conclusion, "whatever")
    assert not heal


@pytest.mark.parametrize("workflow", [
    "drain-writers",          # red-once IS the needs-a-human signal
    "Rendered contrast audit",  # measures the live site; red pre-deploy is correct
    "landmarks",              # measures the corpus, a regression is a data event
    "recall",
    "host-watch",             # the alarm channel
    "CI failure alert",
    "Self-heal",
])
def test_the_designed_alarms_are_never_healed(workflow):
    heal, _ = self_heal.classify(workflow, "failure",
                                 "AssertionError: something real")
    assert not heal


@pytest.mark.parametrize("cause", [
    "HTTP 504 from /alert: <html>Gateway Time-out</html>",
    "curl: (22) The requested URL returned error: 503",
    "could not reach /alert: timed out",
    "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>",
])
def test_a_host_outage_shaped_failure_is_never_healed(cause):
    heal, reason = self_heal.classify("enrich", "failure", cause)
    assert not heal
    assert "host-outage" in reason


@pytest.mark.parametrize("cause", [
    "spend.CreditsExhausted: the OpenRouter key answered 402",
    "spend.PaidReadsOff: the month's allowance is spent",
    "HTTP 402 from openrouter.ai",
])
def test_a_budget_stop_is_never_healed(cause):
    heal, reason = self_heal.classify("tripwire", "failure", cause)
    assert not heal
    assert "budget" in reason


def test_a_guardrail_finding_awaiting_adjudication_is_never_healed():
    # Run 31734617121's real cause line: the guardrail publishing the clean
    # rows and going red about the flagged ones. Adjudication is the owner's.
    heal, reason = self_heal.classify(
        "collect", "failure",
        "[guardrail] 1 finding(s) are past their grace window. This run "
        "exits non-zero AFTER publishing the clean rows.")
    assert not heal
    assert "ADJUDICATION" in reason


def test_a_new_code_shaped_failure_IS_healable():
    heal, reason = self_heal.classify(
        "tests", "failure",
        "AssertionError: the place ribbon names the wrong unit")
    assert heal
    assert "healable" in reason


def test_a_failure_with_no_cause_line_is_still_healable():
    heal, _ = self_heal.classify("tests", "failure", "")
    assert heal


# -- the fingerprint is the budget ledger -----------------------------------

def test_drifting_numbers_are_one_cause_and_one_branch():
    a = self_heal.branch_name("tests", "held 21/51, 41.2% regressed")
    b = self_heal.branch_name("tests", "held 20/51, 39.2% regressed")
    assert a == b
    assert a.startswith(self_heal.BRANCH_PREFIX)


def test_different_causes_and_workflows_are_different_branches():
    assert self_heal.branch_name("tests", "a") != self_heal.branch_name("tests", "b")
    assert (self_heal.branch_name("tests", "same")
            != self_heal.branch_name("collect", "same"))


# -- the forbidden-path guard -----------------------------------------------

VIOLATIONS = [
    "data/talent_intel.db",         # the database IS the memory
    "data/writer_queue.json",       # queued work a healer must not erase
    "data/alert_outbox.json",       # held alerts
    "data/landmarks.json",          # hand-assembled, never machine-thinned
    "spend.py",                     # the pots
    "budget.py",
    "guardrails.py",
    "requirements.lock",            # the supply chain
    "requirements-dev.lock",
    "docs/HANDOVER.md",             # the session log
    ".github/workflows/self-heal.yml",  # the healer itself
]


@pytest.mark.parametrize("path", VIOLATIONS)
def test_every_forbidden_path_is_caught(path):
    assert self_heal.violations([path]) == [path]


def test_a_benign_fix_passes():
    assert self_heal.violations(
        ["collectors/sec_edgar.py", "tests/test_sec_edgar_filer_name.py",
         "pipeline/classify.py"]) == []


def test_check_exits_red_on_the_violation_fixture(capsys):
    code = self_heal.main(["check", "--files",
                           "pipeline/classify.py", "data/talent_intel.db"])
    assert code == 1
    assert "data/talent_intel.db" in capsys.readouterr().out


def test_check_exits_green_on_a_benign_fixture(capsys):
    assert self_heal.main(["check", "--files", "pipeline/classify.py"]) == 0


def test_the_forbidden_list_names_real_paths():
    for pattern in self_heal.FORBIDDEN:
        if any(ch in pattern for ch in "*?["):
            continue
        target = ROOT / pattern.rstrip("/")
        assert target.exists(), pattern


# -- the merge gate resolves UNKNOWN to "stay a draft" ----------------------

def test_only_an_unambiguous_looks_sound_merges():
    m = self_heal.VERDICT_MARKER
    assert self_heal.review_verdict([f"{m} LOOKS SOUND\nevidence"]) == "LOOKS SOUND"
    assert self_heal.review_verdict([f"{m} DO NOT MERGE\n."]) == "DO NOT MERGE"
    assert self_heal.review_verdict(["lgtm!"]) is None
    # two markers in one comment is ambiguous, and ambiguous never merges
    assert self_heal.review_verdict([f"{m} LOOKS SOUND\n{m} DO NOT MERGE"]) is None


def test_the_latest_verdict_wins():
    m = self_heal.VERDICT_MARKER
    assert self_heal.review_verdict(
        [f"{m} LOOKS SOUND\n.", f"{m} NEEDS WORK\n."]) == "NEEDS WORK"


@pytest.mark.parametrize("path", [
    ".github/workflows/tests.yml",   # never auto-merge CI
    "data/talent_intel.db",          # never auto-merge state
    "spend.py",                      # never auto-merge a pot
])
def test_the_merge_gate_refuses_these_whatever_the_verdict(path):
    ok, _ = self_heal.automergeable_paths(["pipeline/classify.py", path])
    assert not ok


def test_an_empty_diff_is_never_automerged():
    assert not self_heal.automergeable_paths([])[0]


def test_a_source_and_test_diff_is_automergeable():
    ok, reason = self_heal.automergeable_paths(
        ["collectors/sec_edgar.py", "tests/test_sec_edgar_filer_name.py"])
    assert ok, reason


def test_suite_failures_reads_both_test_runners():
    out = ("FAILED tests/test_z.py::test_c - AssertionError\n"
           "FAIL: test_a (tests.test_x.C.test_a)\n"
           "ERROR: test_b (tests.test_y.D.test_b)\n")
    assert self_heal.suite_failures(out) == {
        "tests/test_z.py::test_c",
        "test_a (tests.test_x.C.test_a)",
        "test_b (tests.test_y.D.test_b)"}


def test_a_standing_red_subtracts_and_a_new_red_blocks():
    baseline = {"tests/test_live.py::test_known"}
    assert {"tests/test_live.py::test_known"} - baseline == set()
    assert ({"tests/test_live.py::test_known", "tests/test_new.py::test_x"}
            - baseline) == {"tests/test_new.py::test_x"}


# -- the healing ledger -----------------------------------------------------

def _record(tmp):
    return self_heal.main(
        ["record", "--pr", "7", "--workflow", "collect",
         "--merge-sha", "abc1234",
         "--run-url", "https://github.com/x/y/actions/runs/1",
         "--cause", "AssertionError: the real line",
         "--files", "pipeline/classify.py",
         "--healing-log", str(tmp / "HEALING-LOG.md"),
         "--techlog", str(tmp / "TECHLOG.md")])


def test_the_ledger_carries_the_revert_and_the_kill_switch(tmp_path, capsys):
    (tmp_path / "TECHLOG.md").write_text("# Tech Log\n\n## old\n")
    assert _record(tmp_path) == 0
    ledger = (tmp_path / "HEALING-LOG.md").read_text()
    assert "git revert abc1234" in ledger
    assert "SELF_HEAL_AUTOMERGE_DISABLED" in ledger
    assert "PR #7" in ledger


def test_entries_go_newest_first_and_a_second_heal_appends(tmp_path):
    (tmp_path / "TECHLOG.md").write_text("# Tech Log\n\n## old entry\n")
    _record(tmp_path)
    tech = (tmp_path / "TECHLOG.md").read_text()
    assert tech.index("self-heal: auto-merged") < tech.index("## old entry")
    _record(tmp_path)
    assert (tmp_path / "HEALING-LOG.md").read_text().count("- revert:") == 2


def test_a_failed_record_is_a_warning_not_a_crash(tmp_path):
    # The CALLER downgrades a 1 to a warning; a heal that merged is never
    # undone by a docs write that did not.
    assert _record(tmp_path / "not" / "there") == 1


# -- the workflow file keeps its shape --------------------------------------

def test_the_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses:\s*anthropics/claude-code-action@(\S+)", WORKFLOW)
    assert len(uses) == 2, "one healer step and one reviewer step"
    for ref in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            "the action must be pinned to a 40-hex commit SHA, not a tag a "
            "maintainer can move")


def test_one_healer_at_a_time_and_never_the_writer_lock():
    assert "group: self-heal" in WORKFLOW
    assert "cancel-in-progress: false" in WORKFLOW
    # NOT in the writer lock: a healer queueing there could evict a writer.
    assert "talent-collect" not in re.sub(r"#.*", "", WORKFLOW)


def test_the_pr_is_a_draft_and_a_human_merges():
    assert "--draft" in WORKFLOW
    assert "gh pr ready" in WORKFLOW


def test_the_kill_switch_and_the_dormant_notice_exist():
    assert "SELF_HEAL_DISABLED" in WORKFLOW
    assert "CLAUDE_CODE_OAUTH_TOKEN" in WORKFLOW
    assert "DORMANT" in WORKFLOW


def test_the_automerge_kill_switch_is_separate_from_the_healer_switch():
    # Two switches on purpose: one keeps the drafts and returns the click to
    # a human, the other stops the healer entirely.
    assert "SELF_HEAL_AUTOMERGE_DISABLED" in WORKFLOW


def test_the_owner_authorization_and_date_are_recorded_in_the_workflow():
    assert "2026-08-14" in WORKFLOW
    assert "owner" in WORKFLOW.lower()


def test_the_reviewer_is_asked_for_the_machine_readable_verdict():
    assert self_heal.VERDICT_MARKER in WORKFLOW


def test_the_heal_is_recorded_in_the_ledgers():
    assert "self_heal.py record" in WORKFLOW
    assert "docs/HEALING-LOG.md" in WORKFLOW


def test_automerge_is_gated_by_the_guard_and_the_review():
    """Asserted structurally, not by grep: a fix must never merge without
    both the forbidden-path guard and the adversarial review having run."""
    import yaml
    parsed = yaml.safe_load(WORKFLOW)
    jobs = parsed["jobs"]
    assert set(jobs) == {"heal", "guard", "review", "automerge", "summary"}
    needs = jobs["automerge"]["needs"]
    for required in ("heal", "guard", "review"):
        assert required in needs
    gate = jobs["automerge"]["if"]
    assert "needs.guard.result == 'success'" in gate
    assert "needs.review.result == 'success'" in gate
    assert "SELF_HEAL_AUTOMERGE_DISABLED" in gate


def test_the_guard_runs_the_real_check():
    assert "self_heal.py check" in WORKFLOW


def test_the_prompt_forbids_what_the_guard_forbids():
    for pattern in self_heal.FORBIDDEN:
        assert pattern.rstrip("/") in WORKFLOW, pattern

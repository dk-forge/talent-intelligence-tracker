"""Forward work is funded before historical backfill, and a pause stays visible.

The owner's policy, 2026-08-10:

  * Paid model and discovery spend is capped at $10 per UTC CALENDAR month.
  * Paid processing prioritizes 2026-01-01 forward.
  * Paid pre-2026 extraction and discovery are deferred until opted in.
  * Correctness still applies to every record already published, at any date:
    corrections, retractions and guardrail work are NOT deferred.
  * Free structured historical work continues.
  * Free forward collectors continue after the paid ceiling is reached.

Every assertion here fails on the pre-policy tree.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import spend

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def yaml_code(path: Path) -> str:
    """Workflow text with comments stripped.

    Every one of these files documents at length what it does NOT do, so a
    plain substring match reads prose as configuration: `backfill-funding-bulk`
    says "there is no OPENROUTER_API_KEY here" and a naive `in` call scores
    that as a key. Match the code.
    """
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)

#: The paid walkers. Each holds an OPENROUTER_API_KEY and can spend.
PAID_WALKERS = (
    "backfill-2026",
    "backfill-funding-2026",
    "backfill-gdelt-2026",
    "backfill-gnews-2026",
    "backfill-press-2026",
)

#: The free ones. No key, no spend, and the policy explicitly leaves them alone.
FREE_WALKERS = ("backfill-funding-bulk", "backfill-structured-2026")


# --- forward work cannot be starved -----------------------------------------

def test_forward_collection_is_never_deferred_by_this_policy():
    """The whole point. If only one test survives, this one."""
    # A live collector declares no window at all.
    defers, why = spend.forward_first_defers({})
    assert defers is False
    assert "forward work" in why


def test_a_pre_2026_window_defers_its_paid_reads():
    for start in ("2015-01-01", "2024-05-01", "2025-12-31"):
        defers, why = spend.forward_first_defers({spend.BACKFILL_START_ENV: start})
        assert defers is True, start
        assert spend.BACKFILL_OPT_IN_ENV in why


def test_a_2026_forward_window_keeps_the_allowance():
    for start in ("2026-01-01", "2026-07-26", "2027-03-01"):
        defers, _ = spend.forward_first_defers({spend.BACKFILL_START_ENV: start})
        assert defers is False, start


def test_it_defers_regardless_of_how_much_is_left():
    """Ordering, not arithmetic.

    A history walk does not get to spend the first dollar of a fresh month
    just because the month is fresh. `forward_first_defers` takes no balance
    argument at all, which is the structural version of that claim.
    """
    import inspect

    params = inspect.signature(spend.forward_first_defers).parameters
    assert set(params) == {"env"}


def test_an_unreadable_window_does_not_defer_anything():
    """UNKNOWN is not a licence to switch off live collection."""
    defers, why = spend.forward_first_defers({spend.BACKFILL_START_ENV: "not-a-date"})
    assert defers is False
    assert "made no decision" in why


def test_the_guard_switches_paid_reads_off_and_leaves_forward_alone(monkeypatch):
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    monkeypatch.delenv(spend.BACKFILL_OPT_IN_ENV, raising=False)
    # Register the flag with monkeypatch BEFORE apply_forward_first writes it
    # straight into os.environ, so teardown puts it back. Without this the
    # "off" leaks into every later test in the session and quietly switches
    # the paid paths off across the whole suite -- which is exactly what it
    # did the first time this test was written.
    monkeypatch.setenv(spend.PAID_READS_ENV, "on")

    monkeypatch.setenv(spend.BACKFILL_START_ENV, "2024-01-01")
    spend.apply_forward_first()
    assert os.environ[spend.PAID_READS_ENV] == "off"

    monkeypatch.setenv(spend.PAID_READS_ENV, "on")
    monkeypatch.setenv(spend.BACKFILL_START_ENV, "2026-02-01")
    spend.apply_forward_first()
    assert os.environ[spend.PAID_READS_ENV] == "on"


# --- correctness is never deferred, at any date -----------------------------

def test_no_correction_or_retraction_workflow_declares_a_backfill_window():
    """Corrections to already-published rows must be unreachable by this gate.

    The gate can only fire on a run that sets TIT_BACKFILL_START. If no
    correction or retraction workflow sets it, no correction can be deferred
    by the forward-first policy, whatever the row's date.
    """
    correction_workflows = sorted(WORKFLOWS.glob("correct-*.yml")) + [
        WORKFLOWS / "retract.yml"]
    assert len(correction_workflows) >= 8, "expected the correct-*/retract set"
    for wf in correction_workflows:
        assert spend.BACKFILL_START_ENV not in yaml_code(wf), wf.name


def test_the_policy_names_correctness_as_out_of_scope():
    src = (ROOT / "spend.py").read_text()
    block = src[src.index("Forward-first: who gets the allowance"):
                src.index("FORWARD_FROM = ")]
    assert "correct-*.yml" in block
    assert "retract.yml" in block


# --- free work continues -----------------------------------------------------

def test_the_free_walkers_were_not_gated_by_this_policy():
    """Free structured historical work may continue. It costs nothing, so
    there is nothing for a budget policy to say about it."""
    for name in FREE_WALKERS:
        text = yaml_code(WORKFLOWS / f"{name}.yml")
        assert "OPENROUTER_API_KEY" not in text, name
        assert spend.BACKFILL_START_ENV not in text, name


def test_degrade_still_only_switches_paid_reads():
    """Free forward collection continues after the paid ceiling is reached."""
    src = (ROOT / "spend.py").read_text()
    body = src[src.index("def degrade("):src.index("def gate(")]
    # It switches ONE flag and never stops the job. Free collection is
    # everything upstream of pipeline/classify.py, and that flag is the only
    # thing classify reads.
    assert 'f"{PAID_READS_ENV}=off' in body
    assert "sys.exit" not in body
    assert "raise" not in body


# --- it is a pause, not a teardown ------------------------------------------

def test_the_owner_can_opt_back_in():
    env = {spend.BACKFILL_START_ENV: "2024-01-01"}
    for value in ("on", "1", "yes", "true"):
        assert spend.forward_first_defers({**env, spend.BACKFILL_OPT_IN_ENV: value})[0] is False
    for value in ("", "off", "0", "no", "false", "deferred"):
        assert spend.forward_first_defers({**env, spend.BACKFILL_OPT_IN_ENV: value})[0] is True


def test_every_paid_walker_declares_its_window_and_offers_the_opt_in():
    """A policy no workflow feeds is a comment.

    The gate is blind unless the walker says which window it is walking, and
    "how do I turn it back on" must be one tick in the Actions tab.
    """
    for name in PAID_WALKERS:
        text = yaml_code(WORKFLOWS / f"{name}.yml")
        assert f"{spend.BACKFILL_START_ENV}: " in text, name
        assert "historical_backfill:" in text, name
        assert spend.BACKFILL_OPT_IN_ENV in text, name


def test_every_paid_walker_still_runs_the_degrade_step():
    """Pre-existing and load-bearing: the $10 cap must keep reaching them."""
    for name in PAID_WALKERS:
        assert "spend.py --degrade" in yaml_code(WORKFLOWS / f"{name}.yml"), name


def test_no_walker_was_deleted_or_retired():
    for name in PAID_WALKERS + FREE_WALKERS:
        assert (WORKFLOWS / f"{name}.yml").exists(), name


#: Workflows that hold the key but buy nothing, and why. An exemption has to
#: be written down with a reason, so the next one is a decision rather than an
#: omission.
KEY_HOLDERS_THAT_BUY_NOTHING = {
    "health-digest.yml": "reads the balance for the digest's spend line; "
                         "makes no model call",
}


def test_every_paid_workflow_asks_before_it_spends():
    """ab-models held the collectors' key with no spend step at either end.

    Forward work is funded first, and it cannot be if a discretionary job can
    quietly take the month's headroom.
    """
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = yaml_code(wf)
        if "OPENROUTER_API_KEY: ${{ secrets" not in text:
            continue
        if wf.name in KEY_HOLDERS_THAT_BUY_NOTHING:
            continue
        assert "spend.py" in text, f"{wf.name} can spend and never asks"


# --- the deferral is on a clock ---------------------------------------------

def test_review_falls_after_the_next_allowance_month_opens():
    due = datetime.date.fromisoformat(spend.deferral_review_due())
    adopted = datetime.date.fromisoformat(spend.POLICY_ADOPTED)
    assert due > adopted
    assert (due.year, due.month) != (adopted.year, adopted.month)


def test_december_rolls_into_january():
    assert spend.deferral_review_due("2026-12-20").startswith("2027-01-")


def test_it_is_not_overdue_the_day_it_is_taken():
    assert spend.deferral_overdue(spend.POLICY_ADOPTED) is False


def test_it_goes_overdue_and_stays_overdue():
    due = spend.deferral_review_due()
    assert spend.deferral_overdue(due) is True
    later = (datetime.date.fromisoformat(due) + datetime.timedelta(days=90)).isoformat()
    assert spend.deferral_overdue(later) is True


def test_ops_status_prints_three_states_and_escalates():
    src = (ROOT / "ops_status.py").read_text()
    block = src[src.index("def _report_spend"):src.index("def _report_surfaces")]
    assert "FUNDED FIRST" in block
    assert "DEFERRED BY POLICY" in block
    assert "Not broken and not finished" in block
    assert "review date" in block
    # It must be able to RETURN a problem, not merely print prose.
    assert "-> list[str]" in block
    assert "return [f" in block
    assert "problems += _report_spend()" in src


# --- nothing underneath the policy moved ------------------------------------

def test_the_cap_is_unchanged_and_is_a_utc_calendar_month():
    """This file's subject is that the cap is a UTC CALENDAR MONTH, not a
    rolling window. The value itself is pinned by
    test_spend_degrades.test_the_allowance_is_the_number_the_owner_set; here it
    only has to be a real ceiling under the $20 provider cap (raised 10.0 ->
    18.0 by the owner on 2026-08-12)."""
    assert 0 < spend.MONTHLY_ALLOWANCE_USD < 20.0
    assert spend.STOP_AT_FRACTION == 0.9
    src = (ROOT / "spend.py").read_text()
    body = src[src.index("def month_delta("):src.index("def degrade(")]
    assert 'datetime.timezone.utc).strftime("%Y-%m")' in body


def test_degrade_still_exits_zero_with_the_policy_on_the_same_path():
    # Anchored on `if args.degrade:` alone since 2026-08-18. It used to pin
    # the next line too (`degrade(over)`), which made this a test of statement
    # ORDER inside the branch rather than of what the branch does, and it went
    # red the moment `publish_month_total()` was added ahead of it. What has
    # to hold is that this path degrades, applies the policy, and exits ZERO.
    src = (ROOT / "spend.py").read_text()
    block = src[src.index("    if args.degrade:\n"):]
    block = block[:block.index("if args.gate:")]
    assert "degrade(over)" in block
    assert "apply_forward_first()" in block
    assert "return 0" in block

"""The two pots: catch-up work must never be able to starve staying current.

August 2026 is the whole reason this file exists. Hand-dispatched backfill
walkers spent 88% of the month in two and a half days, the single 90% stop line
closed over everything, and the collectors that keep the tracker CURRENT ran
degraded from 08-03 to 08-12. One pot cannot express "pay the recurring work
first", so there are two.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from pathlib import Path

import pytest

import budget
import spend

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

AUG = datetime.date(2026, 8, 1)


def day(n: int) -> datetime.date:
    return datetime.date(2026, 8, n)


# --- the numbers, and that they were derived rather than picked -------------

def test_the_allowance_is_this_repos_measured_share_of_the_combined_target():
    """$6.04, and every factor in it is a measurement in the repo.

    $8.00 combined (the owner's own "steady state" figure, and the only number
    in the $5-$8 band that does not require cutting the sibling below the
    steady state he asked to keep) x 75.52%, this repo's share of measured
    combined demand: $0.8020/day here against $0.26/day there.
    """
    assert budget.MONTHLY_TARGET_COMBINED_USD == 8.00
    assert budget.DERIVED_ALLOWANCE_USD == 6.04
    assert spend.MONTHLY_ALLOWANCE_USD == budget.DERIVED_ALLOWANCE_USD, (
        "spend.py owns the policy literal and budget.py owns its derivation; "
        "when they disagree the ceiling nobody derived is the one that binds")
    # 0.8020 / (0.8020 + 0.26)
    assert budget.THIS_REPO_SHARE == pytest.approx(0.8020 / 1.0620, abs=5e-4)
    assert spend.MONTHLY_ALLOWANCE_USD < 20.0, (
        "the policy allowance must stay strictly under the provider cap on the "
        "key; at parity the provider hard-stops a run mid-call instead of "
        "spend.py degrading it cleanly")


def test_the_split_is_derived_from_the_walkers_own_declared_budgets():
    """A walker that changes its own budget cannot leave the split behind."""
    import backfill_gdelt_2026
    import backfill_gnews_2026
    import backfill_press_2026

    declared = (backfill_gnews_2026.MONTHLY_WALKER_BUDGET_USD
                + backfill_gdelt_2026.MONTHLY_WALKER_BUDGET_USD
                + backfill_press_2026.MONTHLY_WALKER_BUDGET_USD)
    assert declared == budget.WALKER_POT_TOTAL_USD, (
        f"the three walkers declare ${declared:.2f}/month between them but "
        f"budget.WALKER_POT_TOTAL_USD says ${budget.WALKER_POT_TOTAL_USD:.2f}; "
        f"the discretionary pot is sized on that sum")

    #: committed demand MEASURED 2026-08-13, the first full un-degraded day
    #: since the guard tripped on 08-03: $0.8020/day over 8 priced runs.
    committed_demand = 0.8020 * 30
    expected = committed_demand / (committed_demand + declared)
    assert budget.COMMITTED_SHARE == pytest.approx(expected, abs=5e-4)


def test_the_two_pots_add_up_to_the_allowance():
    pot = budget.pots(6.04)
    assert round(pot[budget.COMMITTED] + pot[budget.DISCRETIONARY], 6) == 6.04
    assert pot[budget.COMMITTED] > pot[budget.DISCRETIONARY]


# --- THE LOAD-BEARING ONE --------------------------------------------------

def test_a_backfill_on_day_2_leaves_the_collectors_funded_on_day_20():
    """The defect, in one test.

    A catch-up walker helps itself to the WHOLE month's allowance on the 2nd.
    Eighteen days later the scheduled collectors must still be inside their
    ceiling, because the two pots are separate and only one of them was
    raided. Under the single-pot design this is exactly what happened in
    August and the collectors were degraded for nine days.
    """
    allowance = spend.MONTHLY_ALLOWANCE_USD
    raided = {budget.COMMITTED: 0.0, budget.DISCRETIONARY: allowance}

    on_day_2 = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                             charged=raided, today=day(2))
    assert on_day_2.skip, (
        "a walker that has already spent the whole allowance must stop "
        f"spending, and this one was handed ${on_day_2.ceiling:.4f} more")

    on_day_20 = budget.decide(kind=budget.COMMITTED, allowance=allowance,
                              charged=raided, today=day(20))
    pot = budget.pots(allowance)[budget.COMMITTED]
    assert on_day_20.remaining == pytest.approx(pot), (
        f"the collectors have ${on_day_20.remaining:.4f} of their "
        f"${pot:.2f} pot left on day 20 after a backfill spent the entire "
        f"allowance on day 2; a catch-up job must not be able to move this "
        f"number at all")
    assert not on_day_20.over, (
        "the committed pot must not read as degraded because somebody else "
        f"spent the month: {on_day_20.reason}")
    assert "PAST THE LINE" not in on_day_20.reason
    assert "committed pot alone" in on_day_20.reason


def test_committed_work_degrades_only_on_its_own_overspend():
    allowance = spend.MONTHLY_ALLOWANCE_USD
    pot = budget.pots(allowance)[budget.COMMITTED]
    charged = {budget.COMMITTED: pot * 0.95, budget.DISCRETIONARY: 0.0}
    d = budget.decide(kind=budget.COMMITTED, allowance=allowance,
                      charged=charged, today=day(20))
    assert d.over, (
        "a caller must be able to ask 'is my pot spent' as a boolean; matching "
        "on the reason prose means a reworded sentence silently starts spending")
    assert "PAST THE LINE" in d.reason
    assert "free collectors" in d.reason and "UNMARKED" in d.reason
    spend_src = (ROOT / "spend.py").read_text()
    assert "decision.over" in spend_src and "PAST THE LINE" not in spend_src, (
        "spend.py must read the flag rather than grep the reason string")


# --- slowing down, rather than switching off -------------------------------

def test_a_discretionary_run_slows_as_the_pot_empties():
    allowance = spend.MONTHLY_ALLOWANCE_USD
    pot = budget.pots(allowance)[budget.DISCRETIONARY]
    fresh = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                          charged={budget.COMMITTED: 0.0,
                                   budget.DISCRETIONARY: 0.0},
                          today=day(20))
    lean = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                         charged={budget.COMMITTED: 0.0,
                                  budget.DISCRETIONARY: pot * 0.8},
                         today=day(20))
    assert 0 < lean.ceiling < fresh.ceiling, (
        f"a leaner month must buy a SMALLER run, not the same one: "
        f"${lean.ceiling:.5f} against ${fresh.ceiling:.5f}")
    assert not lean.skip, "80% spent is not a reason to stop entirely"


def test_the_ceiling_is_the_days_actually_left_and_not_a_fixed_thirtieth():
    allowance = spend.MONTHLY_ALLOWANCE_USD
    charged = {budget.COMMITTED: 0.0, budget.DISCRETIONARY: 0.0}
    early = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                          charged=charged, today=day(2))
    late = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                         charged=charged, today=day(29))
    assert late.ceiling > early.ceiling, (
        "an unspent pot with three days left buys a BIGGER run than the same "
        "pot with thirty days left; a fixed 1/30th would leave it unspent")
    assert early.days_left == 30 and late.days_left == 3


def test_a_spent_pot_skips_loudly_and_exits_zero(monkeypatch, capsys):
    allowance = spend.MONTHLY_ALLOWANCE_USD
    d = budget.decide(kind=budget.DISCRETIONARY, allowance=allowance,
                      charged={budget.COMMITTED: 0.0,
                               budget.DISCRETIONARY: allowance},
                      today=day(20))
    assert d.skip and d.ceiling == 0.0
    for phrase in ("SKIPS", "not broken and not finished", "Exiting ZERO"):
        assert phrase in d.reason, (
            f"a run skipped for zero headroom must say {phrase!r}; it said: "
            f"{d.reason}")

    monkeypatch.setenv(budget.KIND_ENV, budget.DISCRETIONARY)
    assert budget.main(["--gate"]) == 0, (
        "a skip is the budget working; a non-zero exit here manufactures a "
        "red run, which manufactures an alert")
    capsys.readouterr()


# --- a truncated run says what it dropped ----------------------------------

def test_a_truncated_walker_run_says_what_the_ceiling_dropped():
    """No silent caps. The ration and its basis are both in the disclosure."""
    import backfill_gnews_2026 as walker

    units, disclosure = budget.walker_ration(
        monthly_walker_budget_usd=walker.MONTHLY_WALKER_BUDGET_USD,
        usd_per_unit=walker.USD_PER_GATED_CANDIDATE,
        per_slice_days=walker.SLICE_DAYS,
        allowance=spend.MONTHLY_ALLOWANCE_USD,
        charged={budget.COMMITTED: 0.0, budget.DISCRETIONARY: 0.0},
        today=day(2))
    assert units >= 1
    assert str(units) in disclosure
    for phrase in ("DERIVED from the discretionary pot", "day(s) ="):
        assert phrase in disclosure, (
            f"the ration must name where it came from; it said: {disclosure}")
    assert units < walker.DAILY_GATE_RATION_STATIC, (
        f"at a ${spend.MONTHLY_ALLOWANCE_USD:.2f} allowance the live ration "
        f"({units}) should be below the old static one "
        f"({walker.DAILY_GATE_RATION_STATIC}); if it is not, the budget is "
        f"not the thing deciding")


def test_a_walker_whose_share_buys_nothing_returns_zero_units_and_says_so():
    import backfill_gnews_2026 as walker

    units, disclosure = budget.walker_ration(
        monthly_walker_budget_usd=walker.MONTHLY_WALKER_BUDGET_USD,
        usd_per_unit=walker.USD_PER_GATED_CANDIDATE,
        per_slice_days=walker.SLICE_DAYS,
        allowance=spend.MONTHLY_ALLOWANCE_USD,
        charged={budget.COMMITTED: 0.0,
                 budget.DISCRETIONARY: spend.MONTHLY_ALLOWANCE_USD},
        today=day(20))
    assert units == 0
    assert "SKIPS" in disclosure and "Exiting ZERO" in disclosure


def test_every_walker_names_the_drop_in_its_own_summary():
    """The disclosure has to reach the RUN LOG, not just a returned string.

    A ceiling that only exists in a helper's docstring is a silent cap by the
    time anybody reads the job output.
    """
    import backfill_gdelt_2026
    import backfill_gnews_2026
    import backfill_press_2026

    for module in (backfill_gnews_2026, backfill_gdelt_2026, backfill_press_2026):
        source = Path(module.__file__).read_text()
        name = Path(module.__file__).name
        assert "DROPPED FOR BUDGET, NOT FOR A VERDICT" in source, (
            f"{name} truncates for money and does not say so in its summary")
        assert "ration_basis" in source, (
            f"{name} does not print WHERE its ceiling came from, so a short "
            f"run is indistinguishable from a finished one")
        assert "NOTHING BOUGHT" in source, (
            f"{name} has no zero-headroom message; a run that buys nothing "
            f"must say it is not broken and not finished")


def test_every_walker_derives_its_ration_from_the_budget_not_a_constant():
    """All three, not just the one that was convenient to wire."""
    import backfill_gdelt_2026
    import backfill_gnews_2026
    import backfill_press_2026

    for module in (backfill_gnews_2026, backfill_gdelt_2026, backfill_press_2026):
        source = Path(module.__file__).read_text()
        assert "budget.walker_ration" in source, (
            f"{Path(module.__file__).name} still sizes its ration from a "
            f"static monthly constant, so it cannot slow down in a lean month")


# --- which pot a run is in -------------------------------------------------

def test_a_run_that_declares_nothing_is_committed():
    assert budget.run_kind({}) == budget.COMMITTED
    assert budget.run_kind({budget.KIND_ENV: ""}) == budget.COMMITTED
    assert budget.run_kind({budget.KIND_ENV: "backfill"}) == budget.COMMITTED, (
        "an unrecognised value must fail toward the PROTECTED pot: the live "
        "collectors declare nothing and rationing them on a typo is the "
        "failure this whole module exists to prevent")
    assert budget.run_kind({budget.KIND_ENV: "discretionary"}) == budget.DISCRETIONARY


def _paid_workflows() -> dict[str, str]:
    uses_key = re.compile(r"OPENROUTER_API_KEY:\s*\$\{\{\s*secrets\.")
    return {p.name: p.read_text() for p in sorted(WORKFLOWS.glob("*.yml"))
            if uses_key.search(p.read_text())}


def test_a_dispatch_only_paid_workflow_declares_itself_discretionary():
    """The classification is structural: no live cron means catch-up.

    A hand-maintained list of job names goes stale the first time somebody
    adds a walker, and a walker missing from the list spends out of the pot it
    was supposed to be kept out of.
    """
    scheduled = budget.scheduled_workflows()
    missing = []
    for name, text in _paid_workflows().items():
        declares = f"{budget.KIND_ENV}: {budget.DISCRETIONARY}" in text
        if name not in scheduled and not declares:
            missing.append(name)
        if name in scheduled:
            assert not declares, (
                f"{name} runs on a schedule, so it is stay-current work and "
                f"must not declare itself discretionary")
    assert not missing, (
        "these paid workflows have no schedule, so they are catch-up work, "
        f"and they must export {budget.KIND_ENV}: {budget.DISCRETIONARY} or "
        f"they spend out of the collectors' pot: {sorted(missing)}")


# --- the ledger ------------------------------------------------------------

def _ledger_db(tmp_path, rows):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE source_health (collector TEXT, run_at TEXT, "
                 "cost_usd REAL, run_kind TEXT)")
    conn.executemany("INSERT INTO source_health VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def test_an_enqueued_cron_still_counts_as_scheduled():
    """`tripwire.yml` and `benchmark-diff.yml` carry no cron of their own.

    Their crons live in `schedule-link-hygiene.yml`, deliberately, because a
    `schedule:` in a database writer's own file enters the `talent-collect`
    lock uncoordinated. They are ARMED and they are stay-current work, and a
    rule that only reads each file in isolation would have filed the twice-
    weekly tripwire as catch-up and slowly starved it.
    """
    scheduled = budget.scheduled_workflows()
    assert "tripwire.yml" in scheduled
    assert "benchmark-diff.yml" in scheduled
    assert "collect.yml" in scheduled
    for name in ("backfill-gnews-2026.yml", "backfill-gdelt-2026.yml",
                 "backfill-press-2026.yml", "ab-models.yml"):
        assert name not in scheduled, (
            f"{name} is dispatch-only catch-up work; if it has grown a "
            f"schedule that is a spend decision and belongs to the owner")


def test_the_ledger_tells_the_two_pots_apart(tmp_path):
    path = _ledger_db(tmp_path, [
        ("google_news", "2026-08-13T08:07:16+00:00", 0.1878, "committed"),
        ("google_news", "2026-08-02T08:07:16+00:00", 1.5000, "discretionary"),
        ("google_news", "2026-07-30T08:07:16+00:00", 9.9999, "committed"),
    ])
    got = budget.ledger_spend(db_path=path, month="2026-08")
    assert got[budget.COMMITTED] == pytest.approx(0.1878)
    assert got[budget.DISCRETIONARY] == pytest.approx(1.5)


def test_a_row_with_no_kind_counts_as_committed(tmp_path):
    path = _ledger_db(tmp_path, [
        ("google_news", "2026-08-13T08:07:16+00:00", 0.5, None)])
    got = budget.ledger_spend(db_path=path, month="2026-08")
    assert got[budget.COMMITTED] == pytest.approx(0.5)


def test_the_health_row_can_record_which_pot_paid_for_it():
    from pipeline import schema

    assert ("source_health", "run_kind", "TEXT") in schema.MIGRATIONS, (
        "without a run_kind on the health row the ledger has a total and no "
        "split, which is the state that let a backfill degrade the collectors")


def test_unattributed_spend_is_charged_to_catch_up_first():
    """The tripwire and ab_models call models without filing a priced row.

    Charging the remainder to the catch-up pot slows a backfill. Charging it
    the other way would degrade the collectors, which is the failure being
    prevented, so the bias is chosen rather than accidental.
    """
    ledger = {budget.COMMITTED: 1.0, budget.DISCRETIONARY: 0.2}
    got = budget.charge(ledger, month_total=1.5)
    assert got[budget.COMMITTED] == pytest.approx(1.0)
    assert got[budget.DISCRETIONARY] == pytest.approx(0.5)

    # And it never spills, however large it gets: an unattributed dollar must
    # not be able to degrade the scheduled collectors.
    huge = budget.charge(ledger, month_total=50.0)
    assert huge[budget.COMMITTED] == pytest.approx(1.0)
    assert huge[budget.DISCRETIONARY] == pytest.approx(49.0)


def test_an_unmeasured_total_leaves_the_ledger_alone():
    ledger = {budget.COMMITTED: 1.0, budget.DISCRETIONARY: 0.2}
    assert budget.charge(ledger, month_total=None) == ledger
    assert budget.charge(ledger, month_total=0.5) == ledger


# --- the one line the owner reads ------------------------------------------

def test_the_status_line_names_spend_allowance_days_left_and_projection():
    line = budget.status_line(
        allowance=6.04,
        charged={budget.COMMITTED: 2.0, budget.DISCRETIONARY: 0.5},
        today=day(10))
    for phrase in ("$2.50", "$6.04", "22 day(s) left", "projected"):
        assert phrase in line, f"the status line is missing {phrase!r}: {line}"
    assert "current" in line and "catch-up" in line, (
        "'spent $2.50 of $6.04' hides the only thing that matters here — "
        f"whether it came out of staying current or catching up: {line}")


def test_the_status_line_says_the_ledger_is_a_floor():
    line = budget.status_line(allowance=6.04,
                              charged={budget.COMMITTED: 1.0,
                                       budget.DISCRETIONARY: 0.0},
                              today=day(10))
    assert "FLOOR" in line, (
        "jobs that call a model without filing a priced health row are not in "
        "the ledger, so an unqualified total would read as a measurement it "
        f"is not: {line}")
    measured = budget.status_line(allowance=6.04,
                                  charged={budget.COMMITTED: 1.0,
                                           budget.DISCRETIONARY: 0.0},
                                  today=day(10), measured_total=True)
    assert "FLOOR" not in measured


def test_an_unreadable_allowance_is_unknown_and_never_a_pass(tmp_path, monkeypatch):
    empty = tmp_path / "spend.py"
    empty.write_text("X = 1\n")
    assert budget.monthly_allowance(str(empty)) is None
    monkeypatch.setattr(budget, "monthly_allowance", lambda *a, **k: None)
    d = budget.decide(kind=budget.COMMITTED,
                      charged={budget.COMMITTED: 0.0,
                               budget.DISCRETIONARY: 0.0})
    assert "UNKNOWN" in d.reason and "not a pass" in d.reason


def test_ops_status_shows_the_budget_line():
    source = (ROOT / "ops_status.py").read_text()
    assert "budget.status_line" in source, (
        "the owner is not a developer and reads ops_status; a budget that is "
        "only visible by importing a module is not surfaced")


def test_ops_status_does_not_parse_the_allowance_a_second_time():
    import ops_status

    assert "budget.monthly_allowance" in (ROOT / "ops_status.py").read_text(), (
        "two parsers for one policy number is how they come to disagree")
    assert ops_status._monthly_allowance() == spend.MONTHLY_ALLOWANCE_USD

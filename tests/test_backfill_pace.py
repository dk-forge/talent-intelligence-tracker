"""A historical walker's cadence must map one-to-one onto its progress.

The failure this file exists to make impossible was measured on the sibling
tracker, not imagined here. Its `edgar-history-sweep` keyed its cursor on
`now.toordinal()` — a DATE ordinal — and ran HOURLY. Every run in a day computed
the identical window, re-fetched the identical filings and re-extracted them at
full prompt: roughly $3.80 a day of pure waste for six days, out of runs that
were all green, because a run redoing yesterday's work is indistinguishable from
outside from a run doing work.

The bug is the PAIRING, and that is what is asserted here rather than the
symptom. A date-keyed cursor with a daily cron is fine. A run-keyed cursor with
an hourly cron is fine. A date-keyed cursor with a sub-daily cron multiplies spend
by the runs per day, silently. So:

  * the cursor must advance on the RUN — a second `record` in the same clock
    second still moves it;
  * no sliced-backfill workflow may carry a cron faster than daily;
  * and a run that finished nothing must not requeue itself, because a chain that
    cannot advance is the same waste with no window to blame.

Offline. No network, no model, no clock dependence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backfill_slices

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

FIXED = datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)


def _job(state, *, size=4):
    return backfill_slices.open_job(
        state, workflow="backfill-gdelt-2026.yml", unit="days",
        start="2026-01-01", end="2026-12-31", slice_size=size)


def _finish(state, job, lo, hi, *, now=FIXED, totals=None):
    ticket = backfill_slices.slice_ticket(job, lo, hi, totals=totals or {"stored": 1})
    return backfill_slices.record(state, ticket, now=now)


# --- the property: progress is anchored to the run -------------------------

def test_two_runs_in_the_same_clock_second_advance_the_cursor_twice():
    """THE property. A date-keyed cursor would return the same window twice."""
    state = backfill_slices.empty_state()
    job = _job(state)

    first = backfill_slices.next_slice(job["cursor"], job["end"], "days", 4)
    assert first == ("2026-01-01", "2026-01-04")
    _finish(state, job, *first, now=FIXED)

    second = backfill_slices.next_slice(job["cursor"], job["end"], "days", 4)
    assert second == ("2026-01-05", "2026-01-08"), (
        "the second window of the same second repeated the first — the cursor is "
        "keyed on something other than the run")
    # And the identical wall clock is genuinely identical, so the test is not
    # passing because a millisecond elapsed.
    _finish(state, job, *second, now=FIXED)
    assert job["cursor"] == "2026-01-09"
    assert job["slices"] == 2


def test_the_cursor_is_monotonic_across_a_whole_chain():
    state = backfill_slices.empty_state()
    job = _job(state)
    seen: list[str] = []
    for _ in range(30):
        window = backfill_slices.next_slice(job["cursor"], job["end"], "days", 4)
        if window is None:
            break
        seen.append(window[0])
        _finish(state, job, *window)
    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen), "a window was walked twice"


def test_a_run_that_finished_nothing_stalls_instead_of_requeueing():
    """A chain that cannot advance is the same waste with no window to blame."""
    state = backfill_slices.empty_state()
    job = _job(state)
    ticket = backfill_slices.slice_ticket(job, "2026-01-01", "2026-01-04",
                                          next_cursor=job["cursor"])
    result = backfill_slices.record(state, ticket, now=FIXED)
    assert not result["advanced"]
    assert result["job"]["state"] == "stalled"
    assert result["problem"] and "no progress" in result["problem"]
    assert backfill_slices.next_inputs(result["job"]) is None, (
        "a stalled job must not produce inputs for another run")


def test_a_budget_stop_resumes_on_the_first_window_it_did_not_do():
    """Cadence maps to progress even when a slice stops half way.

    The run passes the day after the last window it FINISHED, so nothing is
    collected twice (which is spend) and nothing is skipped (which is a hole).
    """
    state = backfill_slices.empty_state()
    job = _job(state, size=4)
    # Nominal slice 01-01..01-04; the budget expired after finishing 01-02.
    _finish(state, job, "2026-01-01", "2026-01-04", totals={"stored": 3})
    assert job["cursor"] == "2026-01-05"

    state = backfill_slices.empty_state()
    job = _job(state, size=4)
    ticket = backfill_slices.slice_ticket(
        job, "2026-01-01", "2026-01-04", next_cursor="2026-01-03",
        stopped_early="slice budget reached")
    backfill_slices.record(state, ticket, now=FIXED)
    assert job["cursor"] == "2026-01-03"
    assert backfill_slices.next_slice(job["cursor"], job["end"], "days", 4) \
        == ("2026-01-03", "2026-01-06")


# --- the pairing: cadence against cursor shape -----------------------------

def _crons(path: Path) -> list[str]:
    """Uncommented cron expressions only. A `#   - cron:` line is prose."""
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- cron:"):
            out.append(stripped.split(":", 1)[1].split("#")[0].strip().strip("'\""))
    return out


def _runs_per_day(expression: str) -> float:
    """How many times a day this cron fires, from its minute and hour fields.

    Only the two fields that can make a schedule sub-daily are read. A weekly or
    monthly cron fires at most once on the days it fires at all, which is what
    matters here — the question is never "how often per month" but "can two runs
    land inside one date".
    """
    fields = expression.split()
    if len(fields) < 2:
        return 0.0
    minute, hour = fields[0], fields[1]

    def slots(field: str, span: int) -> int:
        if field == "*":
            return span
        if field.startswith("*/"):
            step = int(field[2:])
            return max(1, span // max(step, 1))
        return len([p for p in field.split(",") if p])

    return slots(minute, 60) * slots(hour, 24)


SLICED_WORKFLOWS = ("backfill-2026.yml", "backfill-funding-2026.yml",
                    "backfill-funding-bulk.yml", "backfill-gdelt-2026.yml",
                    "backfill-structured-2026.yml")


@pytest.mark.parametrize("name", SLICED_WORKFLOWS)
def test_a_sliced_backfill_never_carries_a_sub_daily_cron(name):
    """The sibling's exact configuration, refused by a test.

    These four all resume from the committed cursor, so a sub-daily cadence is
    safe for THEM in a way it was not for the sibling — but the guard is kept at
    the workflow level anyway, because the next walker to be written may derive a
    window from a date and this is where somebody will look.
    """
    path = WORKFLOWS / name
    if not path.exists():
        pytest.skip(f"{name} does not exist")
    for expression in _crons(path):
        assert _runs_per_day(expression) <= \
            backfill_slices.DATE_CURSOR_SAFE_RUNS_PER_DAY, (
            f"{name} fires {_runs_per_day(expression)}x a day on "
            f"'{expression}'. If its cursor is ever keyed on a date rather than "
            f"on the run, every run after the first repeats the window at full "
            f"prompt cost. The sibling burned ~$3.80/day for six days that way "
            f"out of runs that were all green.")


def test_the_historical_walker_is_not_armed():
    """Dispatch-only until the owner has chosen a pace.

    Cost scales with slices, so the cron IS the budget. Arming it is a spend
    decision and belongs to the owner; the numbers to decide it with are printed
    by `backfill_gdelt_2026.py --plan-cost`.
    """
    path = WORKFLOWS / "backfill-gdelt-2026.yml"
    assert path.exists()
    assert _crons(path) == [], (
        "the historical walker has grown a schedule. Its per-slice cost is real "
        "money, so this is the owner's decision and it must be recorded in "
        "TECHLOG with the chosen pace and the projected month.")


def test_the_structured_walker_is_not_armed():
    """Same refusal, a different reason, and the reason has to be written down.

    The GDELT walker is unarmed because a slice costs money. This one costs
    $0.00 — every source it walks exposes `as_classified` and no model is ever
    called — so a reader looking for the cost argument will not find one and
    might conclude a cron is harmless. It is not. Every source here WRITES the
    database, so a scheduled run enters the single `talent-collect` group
    uncoordinated and either evicts the pending run (which ends `cancelled`
    with no jobs, no logs and inputs GitHub will not disclose) or is itself
    evicted and becomes an unreplayable orphan. Fifteen of those are on file
    from 2026-07-29. Queue it through drain-writers.yml instead.
    """
    path = WORKFLOWS / "backfill-structured-2026.yml"
    assert path.exists()
    assert _crons(path) == [], (
        "the structured walker has grown a schedule. It is free, so this is "
        "NOT a spend decision — it is the writer lock. A cron in a "
        "talent-collect workflow evicts a pending run or becomes an orphan.")


# --- the roster cursor: same property, a population instead of a calendar ----

def _roster_job(state, *, size=1, end="7"):
    return backfill_slices.open_job(
        state, workflow="backfill-structured-2026.yml", unit="slices",
        start="0", end=end, slice_size=size, label="companies_house")


def test_a_roster_cursor_also_advances_per_run_not_per_date():
    """`companies_house` costs one request per COMPANY and nothing per day.

    Its cursor therefore walks the roster rather than the calendar — but the
    property that made the sibling's sweep expensive is the same one, so it is
    asserted the same way: two runs in one clock second must advance twice.
    """
    state = backfill_slices.empty_state()
    job = _roster_job(state)

    first = backfill_slices.next_slice(job["cursor"], job["end"], "slices", 1)
    assert first == ("0", "0")
    _finish(state, job, *first, now=FIXED)

    second = backfill_slices.next_slice(job["cursor"], job["end"], "slices", 1)
    assert second == ("1", "1"), (
        "the second run of the same second repeated roster slice 0 — a whole "
        "eighth of the 9,230-employer roster would go unvisited while the run "
        "count looked perfect")
    _finish(state, job, *second, now=FIXED)
    assert job["cursor"] == "2"
    assert job["slices"] == 2


def test_a_roster_walk_visits_every_slice_exactly_once_and_completes():
    state = backfill_slices.empty_state()
    job = _roster_job(state)
    seen = []
    for _ in range(20):
        window = backfill_slices.next_slice(job["cursor"], job["end"], "slices", 1)
        if window is None:
            break
        seen.append(window[0])
        _finish(state, job, *window)
    assert seen == [str(i) for i in range(8)]
    assert job["state"] == "done"


def test_one_workflow_walking_three_sources_keeps_three_cursors():
    """Without the label they share a key, and each resumes where another
    stopped: a hole in one source and a re-collection in the other."""
    state = backfill_slices.empty_state()
    india = backfill_slices.open_job(
        state, workflow="backfill-structured-2026.yml", unit="days",
        start="2026-01-01", end="2026-07-30", slice_size=28, label="bse_india")
    korea = backfill_slices.open_job(
        state, workflow="backfill-structured-2026.yml", unit="days",
        start="2026-01-01", end="2026-07-30", slice_size=60,
        label="opendart_korea")
    assert india is not korea
    _finish(state, india, "2026-01-01", "2026-01-28")
    assert india["cursor"] == "2026-01-29"
    assert korea["cursor"] == "2026-01-01", (
        "India's slice moved Korea's cursor — the two share a job id")


def test_a_job_written_before_labels_existed_keeps_its_id():
    """The label defaults to empty, so every committed cursor still resolves."""
    assert backfill_slices.job_id("backfill-gdelt-2026.yml", "2026-01-01",
                                  "2026-12-31") == \
        "backfill-gdelt-2026:2026-01-01..2026-12-31"


def test_the_cron_parser_reads_the_shapes_that_actually_appear():
    assert _runs_per_day("0 6 * * *") == 1
    assert _runs_per_day("0 6,18 * * *") == 2      # collect.yml
    assert _runs_per_day("0 * * * *") == 24        # the sibling's hourly sweep
    assert _runs_per_day("*/30 * * * *") == 48
    assert _runs_per_day("30 5 * * 1") == 1        # weekly, once on its day
    assert _runs_per_day("30 9 5 * *") == 1        # monthly, once on its day


# --- the cost model --------------------------------------------------------

def test_the_cost_of_a_window_is_derived_from_measured_prices():
    """A pace decision needs a number, and the number must come from the ledger's
    own measured per-item prices rather than from a guess in a comment."""
    import backfill_gdelt_2026 as walker

    cheap = walker.window_cost(candidates=100, reads=10)
    dear = walker.window_cost(candidates=100, reads=60)
    assert dear["usd"] > cheap["usd"] > 0
    # Reads dominate: a read is about forty times a gate call.
    assert cheap["gate_usd"] < cheap["read_usd"]
    year = walker.year_projection(windows_per_run=walker.SLICE_DAYS)
    assert year["slices"] == pytest.approx(366 / walker.SLICE_DAYS, rel=0.05)
    assert year["days_at_one_slice_per_day"] == year["slices"]


def test_the_walker_ceiling_is_the_owners_budget_not_a_round_number():
    """The default per-run read ceiling must be affordable at one slice a day.

    Sized against the ~$5/month product budget rather than against what a runner
    could get through: a ceiling that only spend.py can stop is a ceiling that
    reads as a plan.
    """
    import backfill_gdelt_2026 as walker

    per_slice = walker.window_cost(
        candidates=walker.DEFAULT_MAX_READTHROUGHS * 4,
        reads=walker.DEFAULT_MAX_READTHROUGHS)["usd"]
    assert per_slice * 30 <= walker.MONTHLY_WALKER_BUDGET_USD * 1.05, (
        f"one slice a day at the default ceiling projects ${per_slice * 30:.2f} a "
        f"month against a ${walker.MONTHLY_WALKER_BUDGET_USD:.2f} allowance")

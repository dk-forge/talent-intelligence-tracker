"""Hitting the ceiling costs depth, not everything.

On 2026-07-30 `spend.py --enforce` took the collect jobs red at $9.47 of a $10
allowance, and NOTHING was collected for the rest of the month — including the
SEC, UK pay-gap, ATS, BSE, EDINET and DART collectors, which derive every field
from a column and call no model, and `pipeline/cheap_extract.py`, which closes
records from stated text for $0. Halting all of that to protect a budget none
of it spends is a self-inflicted outage.

These pin the replacement: `--degrade` never fails the step, it switches the
PAID stages off, and a candidate it refuses defers UNMARKED so a later run
reads it. Nothing here stubs a real module into sys.modules (CLAUDE.md, "Test
gotcha"); the environment is patched and put back.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import run_collect
import spend
from pipeline import classify

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


@pytest.fixture
def stats():
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


# --- the allowance -----------------------------------------------------------

def test_the_allowance_is_the_number_the_owner_set():
    """$10. Policy, in a diff, not a secret.

    $10 -> $25 on 2026-07-30, $25 -> $5 on 2026-07-31, both by the owner. This
    test exists because the file kept $25 for a day after the owner had gone
    back to $5, and every cost decision taken in that window was measured
    against a ceiling five times too high.
    """
    assert spend.MONTHLY_ALLOWANCE_USD == 10.0
    assert spend.STOP_AT_FRACTION == 0.9


def test_the_allowance_is_stated_once():
    """A budget written twice is a budget that disagrees with itself. Only
    spend.py may hold the number; everything else imports or prints it."""
    src = inspect.getsource(run_collect)
    assert "25.0" not in src and "$25" not in src


# --- the flag ----------------------------------------------------------------

def test_degrade_writes_the_flag_into_the_job_environment(tmp_path, monkeypatch):
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    spend.degrade(True)

    assert env_file.read_text().strip() == "TIT_PAID_READS=off"


def test_degrade_writes_nothing_when_inside_the_allowance(tmp_path, monkeypatch):
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    spend.degrade(False)

    assert env_file.read_text() == ""


def test_degrade_outside_actions_only_reports(monkeypatch, capsys):
    """No $GITHUB_ENV means no job to configure. It must not crash, and it must
    not pretend it changed anything."""
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    spend.degrade(True)
    assert "DEGRADED" in capsys.readouterr().out


def test_an_unwritable_env_file_is_loud_and_still_exits_zero(monkeypatch, capsys):
    """Failing OPEN is the safe direction: the key's own hard cap is underneath
    this, and a job that goes red over a filesystem error collects nothing."""
    monkeypatch.setenv("GITHUB_ENV", "/proc/definitely/not/writable")
    spend.degrade(True)
    out = capsys.readouterr().out
    assert "COULD NOT SET TIT_PAID_READS" in out


def test_paid_reads_default_to_on(monkeypatch):
    monkeypatch.delenv("TIT_PAID_READS", raising=False)
    assert classify.paid_reads_enabled()


@pytest.mark.parametrize("value", ["off", "OFF", "0", "no", "false", " off "])
def test_every_spelling_of_off_is_off(monkeypatch, value):
    monkeypatch.setenv("TIT_PAID_READS", value)
    assert not classify.paid_reads_enabled()


def test_an_unrecognised_value_means_on(monkeypatch):
    """A typo must not silently stop collection."""
    monkeypatch.setenv("TIT_PAID_READS", "maybe")
    assert classify.paid_reads_enabled()


# --- what a degraded run does ------------------------------------------------

def test_classify_refuses_before_the_cheapest_paid_call(monkeypatch, stats):
    """Not one token is spent, gate included."""
    monkeypatch.setenv("TIT_PAID_READS", "off")

    def never(*args, **kwargs):  # pragma: no cover - the point is it is not hit
        raise AssertionError("a degraded run reached the wire")

    monkeypatch.setattr(classify, "_call", never)

    with pytest.raises(classify.BudgetExhausted):
        classify.classify({"raw_text": "Acme raises $10M", "headline": "Acme"})
    assert stats["gate_calls"] == 0 and stats["full_calls"] == 0


def test_the_refusal_lands_in_the_retry_next_run_path():
    """A BudgetDeferred, so run_collect prints DEFER and does NOT mark the URL
    seen. A candidate refused for budget is read later, never dropped."""
    assert issubclass(classify.BudgetExhausted, classify.BudgetDeferred)
    assert issubclass(classify.BudgetExhausted, classify.Throttled)


def test_the_month_arm_is_caught_before_the_per_run_arm():
    """BudgetExhausted IS a BudgetDeferred, so the general arm would shadow it
    and a degraded month would read as ordinary rationing."""
    src = inspect.getsource(run_collect.run)
    assert (src.index("except classify.BudgetExhausted")
            < src.index("except classify.BudgetDeferred"))


def test_a_degraded_run_is_not_reported_as_every_candidate_rejected():
    """No guard rejected anything. Calling it that would send a human hunting a
    broken classifier over a budget decision the owner made."""
    src = inspect.getsource(run_collect.run)
    assert "and not running_degraded" in src
    assert run_collect.run_outcome(observed=5, everything_rejected=False,
                               mostly_throttled=False,
                               running_degraded=True)[0] is True


# --- degrading is a SUCCESS, and the exit code has to say so ------------------
#
# Measured 2026-08-03: collect runs 30793331965 and 30842395879 and both
# collect-press runs concluded FAILURE with every collector printing its
# designed degradation and nothing broken. `broken` drove the health status and
# the exit code at once, so "the page is shallower than usual" and "a human
# must fix something" were the same bit. They are two questions now.

def test_a_purely_degraded_run_is_green():
    """The whole point. Four red runs a day for the rest of the month over a
    budget decision is how the owner learns to filter this sender, and the next
    genuine breakage arrives in a folder nobody opens."""
    degraded, failed = run_collect.run_outcome(
        observed=253, everything_rejected=False, mostly_throttled=False,
        running_degraded=True)
    assert failed is False, "a rationed run is a success"
    assert degraded is True, "but it is NOT 'ok': the page is shallower"


def test_a_healthy_run_is_green_and_not_degraded():
    assert run_collect.run_outcome(observed=40, everything_rejected=False,
                               mostly_throttled=False,
                               running_degraded=False) == (False, False)


@pytest.mark.parametrize("breakage", ["observed", "everything_rejected",
                                      "mostly_throttled"])
@pytest.mark.parametrize("degraded_month", [False, True])
def test_a_genuine_collector_failure_stays_red(breakage, degraded_month):
    """Including during a degraded month. A month whose allowance is spent is
    exactly when a real breakage is easiest to wave through, so each genuine
    condition is tested on its own rather than under the degradation."""
    kwargs = dict(observed=10, everything_rejected=False,
                  mostly_throttled=False, running_degraded=degraded_month)
    kwargs[breakage] = 0 if breakage == "observed" else True
    degraded, failed = run_collect.run_outcome(**kwargs)
    assert failed is True, breakage
    assert degraded is True


def test_the_exit_code_is_driven_by_failed_and_the_health_row_by_broken():
    """A regression here is invisible in behaviour tests of `verdict` alone:
    `run` could compute the split correctly and then still `return 1 if broken`.
    """
    src = inspect.getsource(run_collect.run)
    assert "return 1 if failed else 0" in src
    assert "return 1 if broken else 0" not in src
    assert 'status="degraded" if broken else "ok"' in src


def test_a_degraded_run_says_so_in_the_ledger_and_the_log():
    src = inspect.getsource(run_collect.run)
    assert "DEGRADED: monthly allowance spent" in src
    assert "deferred unread and unmarked" in src


def test_the_free_half_of_the_pipeline_is_upstream_of_the_refusal():
    """The refusal lives in classify(), which is the only function that can
    spend. Everything free — the prefilter, precheck, the deterministic
    closers, the known-round match, story clustering, both dedup layers —
    happens before it in run_collect and is untouched by the flag."""
    src = inspect.getsource(run_collect.run)
    body = src.split("for item in kept:", 1)[1]
    refusal = body.index("classify.classify(item, interpret_now=False)")
    for free in ("validate.precheck(item)",
                 "dedupe.funding_event_duplicate(",
                 "cheap_extract.extract(item)",
                 "store.already_seen(conn, url)"):
        assert body.index(free) < refusal, free


# --- the workflows -----------------------------------------------------------

def test_the_collect_jobs_degrade_rather_than_halt():
    for name in ("collect.yml", "collect-press.yml"):
        text = (WORKFLOWS / name).read_text()
        assert "python spend.py --degrade" in text, name
        assert "python spend.py --enforce" not in text, name


def test_the_tripwire_gates_rather_than_hard_stopping():
    """There is still no DEGRADED mode for a job whose only action is a paid
    query — the run genuinely does nothing when the allowance is gone. What
    changed on 2026-08-06 is the exit code, not the ceiling.

    `--enforce` here exited 1 at $10.08 of $10, so the run went red, so the
    writer ticket that dispatched it was filed `failed`, so drain-writers went
    red as well: two failure emails for one correct budget stop. `--gate` exits
    0, names the numbers in a `::notice::`, and answers `over` so the paid step
    skips itself. The money saved is identical.

    The full pinning — including that a genuine tripwire fault stays red, and
    that no OTHER workflow reintroduces the shape — lives in
    tests/test_budget_stop_is_not_a_failure.py.
    """
    text = (WORKFLOWS / "tripwire.yml").read_text()
    body = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "python spend.py --gate" in body
    assert "--enforce" not in body
    assert "python spend.py" in body, (
        "the guard must still be asked before a query is bought; dropping the "
        "step entirely would be the one thing worse than going red for it")


def test_the_structured_collectors_were_never_gated_by_spend():
    """They call no model. A spend guard there would only ever be a way to
    lose free rows."""
    text = (WORKFLOWS / "collect-structured.yml").read_text()
    assert "spend.py" not in text


def test_the_degraded_marker_survives_the_health_line_truncation():
    """ops_status prints `detail[:70]` per collector, so a marker appended at
    the end of the detail is exactly the marker nobody sees. The one thing a
    reader must not have to scroll for is 'this run was rationed'."""
    src = inspect.getsource(run_collect.run)
    detail = src.split("detail=(", 1)[1][:400]
    assert detail.index("DEGRADED") < detail.index("dup, ")

    ops = (Path(__file__).resolve().parent.parent / "ops_status.py").read_text()
    assert "detail'][:70]" in ops, (
        "the truncation this ordering is defending against has moved; "
        "re-check that DEGRADED still fits inside it")


# ---------------------------------------------------------------------------
# The discretionary jobs must not be able to switch the scheduled ones off
# ---------------------------------------------------------------------------
#
# Measured 2026-08-03/04. The 2026 backfills ran `python spend.py || true` --
# report-only, by a 2026-07-28 note that approved them as an estimated $7-12 of
# owner-approved spend. 91 gnews-backfill dispatches on 2026-08-03 instead spent
# ~$21.5 (balance delta), took the key past its $20 credit cap, and every paid
# call has 402'd since 08-03 08:16Z. Because collect.yml DOES degrade, the
# discretionary job switched the production job off: 351 google_news candidates
# deferred unread on 08-04.
#
# `--degrade` cannot halt a backfill. It exits 0 and only turns PAID reads off
# once the allowance is spent; the free fetch, the prefilter and every record
# `cheap_extract` can close for $0 keep running, and a deferred candidate is
# left UNMARKED with the cursor unmoved, so a later run reads it. The backfill
# loses depth for the rest of the month. It does not lose coverage.

BACKFILL_WORKFLOWS = sorted(
    p.name for p in WORKFLOWS.glob("backfill-*.yml")
    if "python spend.py" in p.read_text()
)


def test_there_are_backfill_workflows_to_check():
    """Guard the guard: a glob that matches nothing passes every test below."""
    assert BACKFILL_WORKFLOWS, "no backfill workflow invokes spend.py at all"


@pytest.mark.parametrize("name", BACKFILL_WORKFLOWS)
def test_a_backfill_runs_under_the_guard_before_it_spends(name):
    text = (WORKFLOWS / name).read_text()
    assert "python spend.py --degrade" in text, (
        f"{name} spends against the shared allowance without asking the guard, "
        f"so a discretionary run can exhaust the budget the scheduled "
        f"collectors depend on"
    )


@pytest.mark.parametrize("name", BACKFILL_WORKFLOWS)
def test_a_backfill_is_never_hard_stopped(name):
    """--enforce here would be the 2026-07-30 outage again, on a job the owner
    approved. Degrade, never halt."""
    assert "python spend.py --enforce" not in (WORKFLOWS / name).read_text(), name


@pytest.mark.parametrize("name", BACKFILL_WORKFLOWS)
def test_the_guard_runs_before_the_backfill_step_not_after(name):
    """A report printed after the money is gone is a receipt, not a guard.

    Compared against the script INVOCATION, not the cap: every one of these
    workflows explains the raised read cap in a header comment, so matching the
    name alone finds the comment and the ordering check passes vacuously."""
    text = (WORKFLOWS / name).read_text()
    invocation = text.index("\n          python backfill")
    assert text.index("python spend.py --degrade") < invocation, name

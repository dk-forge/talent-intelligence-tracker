"""The quality gates: what makes a recall measurement able to FAIL.

The point of every test here is the same one: before 2026-07-30 this script
exited 0 on every input, so a 9% week and a 95% week were the same event to
anything downstream. These pin the bars, pin where they come from, and pin the
cases where there deliberately is no bar.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.recall import thresholds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def result(measured_on, *, digest="d1", total=10, found=0, partial=0,
           defects=None, items=None, candidates=100, groups=None):
    held = found + partial
    missed = total - held
    summary = {
        "overall": {"total": total, "found": found, "found_partial": partial,
                    "missed": missed, "held": held,
                    "held_pct": round(100.0 * held / total, 1) if total else None,
                    "clean_pct": round(100.0 * found / total, 1) if total else None},
        "defects": defects or {},
    }
    summary.update(groups or {})
    return {
        "measured_on": measured_on,
        "goldset": {"version": "v", "digest": digest},
        "summary": summary,
        "candidates_seen_total": candidates,
        "items": items or [],
    }


def item(gold_id, verdict, company="Acme", country="XX"):
    return {"id": gold_id, "verdict": verdict, "company": company,
            "country": country}


# --- the bound itself -------------------------------------------------------

def test_wilson_is_used_because_the_naive_interval_goes_below_zero():
    """At the rates this project measures, p +/- z*sqrt(p(1-p)/n) is negative,
    and a floor below zero is not a floor."""
    low, high = thresholds.wilson(8, 89)
    assert 0 < low < 0.089 < high < 1
    assert round(low, 4) == 0.0463          # the 2026-07-28 measurement's floor


def test_the_bound_widens_as_the_denominator_shrinks():
    tight_low, _ = thresholds.wilson(50, 500)
    loose_low, _ = thresholds.wilson(5, 50)
    assert loose_low < tight_low


def test_an_empty_denominator_is_the_whole_interval_not_a_divide_by_zero():
    assert thresholds.wilson(0, 0) == (0.0, 1.0)


# --- the first run against a set has no bar, and says so --------------------

def test_a_first_measurement_is_baseline_not_pass():
    verdict = thresholds.evaluate(result("2026-07-28", found=1), history=[])
    assert verdict["verdict"] == thresholds.BASELINE
    assert verdict["exit_code"] == 0


def test_a_new_reference_set_resets_the_rate_gates_but_not_the_item_gate():
    """A widened set is meant to be harder, so a lower rate against a NEW set is
    the set working. The events themselves still have to still be held."""
    old = result("2026-07-28", digest="old", total=89, partial=8,
                 items=[item(f"g{i}", "FOUND") for i in range(8)])
    new = result("2026-08-04", digest="new", total=170, partial=8,
                 items=[item(f"g{i}", "FOUND") for i in range(8)])
    verdict = thresholds.evaluate(new, history=[old])
    assert verdict["exit_code"] == 0
    by_name = {g["gate"]: g for g in verdict["gates"]}
    assert by_name["held_floor"]["status"] == thresholds.BASELINE
    assert by_name["retraction"]["status"] == thresholds.PASS


# --- the gates that fail ----------------------------------------------------

def test_a_collapse_below_the_derived_floor_fails():
    prior = result("2026-07-28", total=89, partial=8)
    now = result("2026-08-04", total=89, partial=2)      # 2.2%, floor is 4.6%
    verdict = thresholds.evaluate(now, history=[prior])
    assert verdict["verdict"] == thresholds.FAIL
    assert verdict["exit_code"] == thresholds.EXIT_REGRESSION


def test_a_drop_inside_sampling_noise_does_not_fail():
    """7/89 against a best of 8/89 is not evidence of anything, and a gate that
    fires on it is a gate people learn to ignore."""
    prior = result("2026-07-28", total=89, partial=8)
    now = result("2026-08-04", total=89, partial=7)
    assert thresholds.evaluate(now, history=[prior])["exit_code"] == 0


def test_losing_events_we_were_measured_as_holding_fails():
    prior = result("2026-07-28", total=89, partial=3,
                   items=[item("a", "FOUND"), item("b", "FOUND"),
                          item("c", "FOUND_PARTIAL")])
    now = result("2026-08-04", total=89, partial=1,
                 items=[item("a", "FOUND"), item("b", "MISSED"),
                        item("c", "MISSED")])
    verdict = thresholds.evaluate(now, history=[prior])
    gate = next(g for g in verdict["gates"] if g["gate"] == "retraction")
    assert gate["status"] == thresholds.FAIL
    assert gate["observed"] == 2 and gate["bar"] == 1


def test_one_retraction_is_a_note_and_not_a_failure():
    """One event can leave for a reason that is not a regression — a dedupe
    merge changing a company key, a revision superseding a row."""
    prior = result("2026-07-28", total=89, partial=3,
                   items=[item("a", "FOUND"), item("b", "FOUND"),
                          item("c", "FOUND")])
    now = result("2026-08-04", total=89, partial=2,
                 items=[item("a", "FOUND"), item("b", "FOUND"),
                        item("c", "MISSED")])
    assert thresholds.evaluate(now, history=[prior])["exit_code"] == 0


def test_the_retraction_allowance_scales_with_the_held_corpus():
    """A fixed count would be a hair trigger once coverage is real."""
    prior = result("2026-07-28", total=400, partial=200,
                   items=[item(f"g{i}", "FOUND") for i in range(200)])
    now = result("2026-08-04", total=400, partial=185,
                 items=[item(f"g{i}", "FOUND") for i in range(185)]
                       + [item(f"g{i}", "MISSED") for i in range(185, 200)])
    gate = next(g for g in thresholds.evaluate(now, history=[prior])["gates"]
                if g["gate"] == "retraction")
    assert gate["bar"] == 20 and gate["status"] == thresholds.PASS


def test_field_rot_fails_even_while_recall_climbs():
    prior = result("2026-07-28", total=89, partial=8,
                   defects={"country_missing": 2})
    now = result("2026-08-04", total=89, partial=20,
                 defects={"country_missing": 18, "amount_missing": 2})
    verdict = thresholds.evaluate(now, history=[prior])
    gate = next(g for g in verdict["gates"] if g["gate"] == "defect_ceiling")
    assert gate["status"] == thresholds.FAIL
    assert verdict["exit_code"] == thresholds.EXIT_REGRESSION


def test_a_cell_that_dies_fails_even_when_the_overall_number_improves():
    groups_before = {"by_source_type": {
        "filing": {"total": 5, "held": 4, "held_pct": 80.0},
        "trade_press": {"total": 50, "held": 4, "held_pct": 8.0}}}
    groups_after = {"by_source_type": {
        "filing": {"total": 5, "held": 0, "held_pct": 0.0},
        "trade_press": {"total": 50, "held": 30, "held_pct": 60.0}}}
    prior = result("2026-07-28", total=89, partial=8, groups=groups_before)
    now = result("2026-08-04", total=89, partial=30, groups=groups_after)
    gate = next(g for g in thresholds.evaluate(now, history=[prior])["gates"]
                if g["gate"] == "cell_collapse")
    assert gate["status"] == thresholds.FAIL
    assert "filing" in gate["detail"]


# --- the instrument, which is a different failure from a regression ---------

def test_an_api_that_answers_nothing_is_an_instrument_fault_not_a_zero():
    prior = result("2026-07-28", total=89, partial=8, candidates=1200)
    now = result("2026-08-04", total=89, partial=0, candidates=0,
                 items=[item("a", "MISSED")])
    verdict = thresholds.evaluate(now, history=[prior])
    assert verdict["exit_code"] == thresholds.EXIT_INSTRUMENT
    assert verdict["exit_code"] != thresholds.EXIT_REGRESSION


def test_zero_candidates_with_no_history_is_not_yet_a_fault():
    now = result("2026-07-28", total=89, partial=0, candidates=0)
    assert thresholds.evaluate(now, history=[])["exit_code"] == 0


def test_a_run_is_never_its_own_baseline():
    """The result file is on disk before the gates run, so the gate has to
    exclude it or every run would compare itself with itself and always pass."""
    now = result("2026-08-04", total=89, partial=2)
    verdict = thresholds.evaluate(now, history=[result("2026-07-28", total=89,
                                                       partial=8), now])
    assert verdict["history_runs"] == 1
    assert verdict["verdict"] == thresholds.FAIL


def test_history_is_read_off_disk_when_none_is_passed(tmp_path):
    for name, res in (("recall-2026-07-28.json", result("2026-07-28", total=89,
                                                        partial=8)),
                      ("not-a-result.json", {"junk": True})):
        (tmp_path / name).write_text(json.dumps(res))
    (tmp_path / "recall-2026-08-01.json").write_text("{ broken")
    verdict = thresholds.evaluate(result("2026-08-04", total=89, partial=2),
                                  results_dir=str(tmp_path))
    assert verdict["history_runs"] == 1          # corrupt file skipped, not fatal
    assert verdict["exit_code"] == thresholds.EXIT_REGRESSION


# --- the script honours the verdict ----------------------------------------

def test_the_script_documents_its_exit_codes():
    """The codes are a contract with the workflow, so they live in --help."""
    out = subprocess.run([sys.executable, "measure_recall.py", "--help"],
                         cwd=ROOT, capture_output=True, text=True)
    assert "EXIT CODES" in out.stdout
    for code in ("0", "2", "3", "4"):
        assert f"\n    {code}  " in out.stdout


@pytest.mark.parametrize("flag", ["--no-gate"])
def test_there_is_an_explicit_escape_hatch_and_it_is_not_the_default(flag):
    out = subprocess.run([sys.executable, "measure_recall.py", "--help"],
                         cwd=ROOT, capture_output=True, text=True)
    assert flag in out.stdout
    assert "never for the scheduled run" in out.stdout


def test_the_scheduled_workflow_does_not_pass_the_escape_hatch():
    with open(os.path.join(ROOT, ".github", "workflows", "recall.yml"),
              encoding="utf-8") as handle:
        workflow = handle.read()
    assert "--no-gate" not in workflow
    assert "continue-on-error" not in workflow.split("Audit why the misses")[0]

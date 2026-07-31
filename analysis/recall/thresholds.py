"""Turn a recall measurement into a PASS or a FAIL, with every bar derived from
the measured history rather than picked.

WHY THIS EXISTS
---------------
`measure_recall.py` computed the one number whose entire purpose is to say
whether the product is any good, and then exited 0 whatever it found. A 9% week
and a 95% week produced identical exit codes, so no scheduler, no CI alert and
no health check could ever tell them apart. A measurement that cannot fail is a
report, and this project's standing rule is that a claim nobody can contradict
is not a measurement.

WHERE THE BARS COME FROM
------------------------
Round numbers are the temptation here and they are the wrong answer twice over:
90% would be red forever and 5% would be green forever, and neither would have
anything to do with what this tracker has actually been observed to do. So every
bar below is computed from `analysis/recall/results/` at run time:

  * the floors are the WILSON 95% lower bound on the best rate this tracker has
    ever recorded against the same reference set. Wilson rather than a plain
    proportion because these denominators are small (89 events, 8 held) and the
    normal approximation is badly behaved out at the tails, which is exactly
    where a recall figure lives. The bound answers the only question worth
    asking of a drop: is it larger than sampling noise on this many events?
  * the ceilings are the same bound taken upward, on the WORST rate recorded.
  * the retraction allowance scales with the size of the held corpus, so it does
    not become a hair trigger as coverage grows.

Consequence, stated plainly: the FIRST run against a new reference set has no
bar, because there is nothing to compare it with, and it says BASELINE rather
than PASS. Anything else would be a number invented to look like a threshold.

WHAT IS COMPARABLE ACROSS REFERENCE SETS, AND WHAT IS NOT
---------------------------------------------------------
A rate measured against one gold set cannot be compared with a rate measured
against another: the denominators are different populations, and a widened set
deliberately samples harder countries, so recall FALLING after a widening is the
set doing its job and not the tracker getting worse. Gating on that would train
everyone to ignore the gate the first time it was right.

Exactly one quantity survives a change of set: an INDIVIDUAL gold event, by id.
An event this tracker was measured as holding, and now does not hold, is a
regression under any reference set, and this repository has twice destroyed rows
without a single red run. So the retraction gate is the one that is always on;
the rate gates wait for a second run against the same digest.
"""

from __future__ import annotations

import json
import math
import os

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 95%, two-sided. Not tunable per-run on purpose: a threshold whose confidence
# level is an argument is a threshold somebody widens on the day it fires.
Z = 1.959963984540054

# A cell that held this many events and now holds none is a dead collector
# rather than noise. Derived from the reference set's own arithmetic: the
# smallest cell REQUIRED_SHAPE will admit carries 4 events (min_per_source_type),
# so 3 is the largest count that cannot be produced by a single admissible cell
# losing all but one of its events by chance.
CELL_COLLAPSE_MIN_HELD = 3

# Share of the held corpus that may drop out between runs without failing.
# Not a tolerance for being wrong: it is the width of the band in which a
# retraction has an innocent explanation (a dedupe merge changing a company key,
# a revision superseding a row). Below `max(1, ...)` this is under one event at
# today's corpus size, so today the effective bar is "two or more"; at 200 held
# events it is 20, which is the point of scaling it rather than fixing it.
RETRACTION_SHARE = 0.10

PASS = "PASS"
FAIL = "FAIL"
BASELINE = "BASELINE"

# Exit codes. Distinct on purpose: "the tracker got worse" and "the instrument
# stopped working" want different humans and different fixes, and collapsing
# them into one non-zero code is how an outage gets triaged as a regression.
EXIT_OK = 0
EXIT_REGRESSION = 3
EXIT_INSTRUMENT = 4


def wilson(successes: int, total: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for a proportion, as (low, high).

    Used instead of `p +/- z*sqrt(p(1-p)/n)` because that interval is nonsense
    at the rates this project actually measures: at 8/89 it reaches below zero,
    and a floor below zero is not a floor.
    """
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centre = p + z2 / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    return max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom)


def load_results(results_dir: str = RESULTS_DIR) -> list:
    """Every stored measurement, oldest first, WHOLE — not the reduced points
    `series.py` produces, because the gates need per-item verdicts.

    A corrupt file is skipped rather than fatal, for the same reason it is in
    series.py: one unreadable historical file must not be able to stop the
    instrument.
    """
    if not os.path.isdir(results_dir):
        return []
    out = []
    for name in sorted(os.listdir(results_dir)):
        if not (name.startswith("recall-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(results_dir, name), encoding="utf-8") as handle:
                out.append(json.load(handle))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: str(r.get("measured_on") or ""))
    return out


def _digest(result: dict) -> str:
    return str((result.get("goldset") or {}).get("digest") or "")


def _overall(result: dict) -> dict:
    return (result.get("summary") or {}).get("overall") or {}


def ever_held(history: list) -> dict:
    """Every gold id this tracker has EVER been measured as holding, with the
    date it was last seen. Keyed by id, so it survives a change of reference set
    as long as the item survives.
    """
    seen = {}
    for result in history:
        for item in result.get("items") or []:
            if item.get("verdict") in ("FOUND", "FOUND_PARTIAL"):
                seen[item.get("id")] = {
                    "company": item.get("company"),
                    "country": item.get("country"),
                    "last_held_on": result.get("measured_on"),
                }
    return seen


def _gate(name, status, detail, bar=None, observed=None, exit_code=EXIT_REGRESSION):
    return {"gate": name, "status": status, "detail": detail, "bar": bar,
            "observed": observed, "exit_code": exit_code if status == FAIL else EXIT_OK}


def _instrument_gate(result: dict, history: list) -> dict:
    """Did we measure the tracker, or did we measure a broken connection?

    An API answering 200 with an empty row list for every query is indexed as a
    total miss and looks exactly like a tracker that holds nothing. The history
    is what separates the two: if we have previously seen candidate rows and now
    see none anywhere, the instrument is what changed.
    """
    seen = result.get("candidates_seen_total")
    if seen is None:
        return _gate("instrument", PASS,
                     "this run did not record candidates_seen_total, so the "
                     "instrument check was not applied")
    before = [r.get("candidates_seen_total") for r in history
              if r.get("candidates_seen_total")]
    if seen == 0 and before:
        return _gate("instrument", FAIL,
                     f"not one candidate row came back for any of "
                     f"{_overall(result).get('total')} gold events, and previous "
                     f"runs saw up to {max(before)}. This is the API, not the "
                     f"tracker, and recording it as 0% recall would be a lie "
                     f"about the wrong system",
                     bar=1, observed=0, exit_code=EXIT_INSTRUMENT)
    return _gate("instrument", PASS,
                 f"{seen} candidate rows seen across the set")


def _retraction_gate(result: dict, history: list) -> dict:
    """Events we were measured as holding and no longer hold.

    The one gate that survives a change of reference set, and therefore the one
    that is always on.
    """
    held_before = ever_held(history)
    if not held_before:
        return _gate("retraction", BASELINE,
                     "no earlier run held anything, so nothing can have been "
                     "retracted")

    now_missed = {i.get("id") for i in (result.get("items") or [])
                  if i.get("verdict") == "MISSED"}
    lost = [held_before[i] | {"id": i} for i in sorted(now_missed & set(held_before))]

    allowance = max(1, math.ceil(RETRACTION_SHARE * len(held_before)))
    detail = (f"{len(lost)} of {len(held_before)} previously-held events are "
              f"missed now (allowance {allowance})")
    if lost:
        detail += ": " + ", ".join(f"{e['company']} ({e['country']})" for e in lost[:10])
    status = FAIL if len(lost) > allowance else PASS
    out = _gate("retraction", status, detail, bar=allowance, observed=len(lost))
    out["lost"] = lost
    return out


def _same_set(history: list, digest: str) -> list:
    return [r for r in history if _digest(r) == digest]


def _floor_gate(result: dict, history: list) -> dict:
    """Overall held rate against the best ever recorded on THIS reference set.

    Same digest only. Comparing a rate across reference sets is comparing two
    different populations, and a widened set is meant to be harder.
    """
    digest = _digest(result)
    prior = _same_set(history, digest)
    if not prior:
        return _gate("held_floor", BASELINE,
                     f"reference set {digest} has no earlier measurement, so "
                     f"there is no bar this run could fall below. The next run "
                     f"against it is gated")

    best = max(prior, key=lambda r: (_overall(r).get("held") or 0) /
                                    max(1, _overall(r).get("total") or 1))
    best_cell = _overall(best)
    floor, _ = wilson(best_cell.get("held") or 0, best_cell.get("total") or 0)

    cell = _overall(result)
    got = (cell.get("held") or 0) / max(1, cell.get("total") or 1)
    status = FAIL if got < floor else PASS
    return _gate(
        "held_floor", status,
        f"held {cell.get('held')}/{cell.get('total')} ({got:.1%}) against a floor "
        f"of {floor:.1%} — the Wilson 95% lower bound on the best this set has "
        f"recorded, {best_cell.get('held')}/{best_cell.get('total')} on "
        f"{best.get('measured_on')}",
        bar=round(floor, 4), observed=round(got, 4))


def _defect_gate(result: dict, history: list) -> dict:
    """Field defects per held event, against the worst ever recorded on this set.

    Recall can climb while the data rots: holding twice as many events with
    three times as many wrong countries is not an improvement, and a single
    held-percentage cannot see it.
    """
    digest = _digest(result)
    prior = _same_set(history, digest)
    if not prior:
        return _gate("defect_ceiling", BASELINE,
                     f"reference set {digest} has no earlier measurement, so "
                     f"there is no defect rate to compare with")

    def rate(res):
        held = _overall(res).get("held") or 0
        defects = sum((res.get("summary") or {}).get("defects", {}).values())
        return defects, held

    worst = max(prior, key=lambda r: rate(r)[0] / max(1, rate(r)[1]))
    w_defects, w_held = rate(worst)
    _, ceiling = wilson(min(w_defects, w_held), w_held)

    defects, held = rate(result)
    got = defects / max(1, held)
    status = FAIL if held and got > ceiling else PASS
    return _gate(
        "defect_ceiling", status,
        f"{defects} field defects across {held} held events ({got:.1%}) against "
        f"a ceiling of {ceiling:.1%} — the Wilson 95% upper bound on the worst "
        f"this set has recorded, {w_defects}/{w_held} on {worst.get('measured_on')}",
        bar=round(ceiling, 4), observed=round(got, 4))


CELL_GROUPS = ("by_source_type", "by_signal_type", "by_geography", "by_segment")


def _collapse_gate(result: dict, history: list) -> dict:
    """A cell that used to hold events and now holds none.

    This is the shape a dead collector makes. It is invisible in the overall
    figure whenever another cell improves in the same week, which is precisely
    when it is most likely to be missed.
    """
    digest = _digest(result)
    prior = _same_set(history, digest)
    if not prior:
        return _gate("cell_collapse", BASELINE,
                     f"reference set {digest} has no earlier measurement, so no "
                     f"cell has a history to collapse from")

    summary = result.get("summary") or {}
    collapsed = []
    for group in CELL_GROUPS:
        best_before = {}
        for res in prior:
            for key, cell in ((res.get("summary") or {}).get(group) or {}).items():
                best_before[key] = max(best_before.get(key, 0), cell.get("held") or 0)
        for key, before in best_before.items():
            if before < CELL_COLLAPSE_MIN_HELD:
                continue
            now = ((summary.get(group) or {}).get(key) or {}).get("held")
            if now == 0:
                collapsed.append(f"{group.replace('by_', '')}:{key} "
                                 f"{before} -> 0")

    status = FAIL if collapsed else PASS
    return _gate("cell_collapse", status,
                 ("; ".join(collapsed) if collapsed
                  else f"no cell that once held {CELL_COLLAPSE_MIN_HELD}+ events "
                       f"has fallen to zero"),
                 bar=CELL_COLLAPSE_MIN_HELD, observed=len(collapsed))


def evaluate(result: dict, history: list | None = None,
             results_dir: str = RESULTS_DIR) -> dict:
    """Run every gate. Returns the verdict and the exit code to use.

    `history` excludes the run being judged: a measurement cannot be its own
    baseline. Callers that have already written this run's file get it filtered
    out here by measured_on + digest rather than having to remember.
    """
    if history is None:
        history = load_results(results_dir)
    history = [r for r in history
               if not (r.get("measured_on") == result.get("measured_on")
                       and _digest(r) == _digest(result))]

    gates = [
        _instrument_gate(result, history),
        _retraction_gate(result, history),
        _floor_gate(result, history),
        _defect_gate(result, history),
        _collapse_gate(result, history),
    ]
    failed = [g for g in gates if g["status"] == FAIL]
    exit_code = EXIT_OK
    for gate in failed:
        # Instrument fault outranks regression: if we did not measure the
        # tracker, nothing we say about the tracker is a finding.
        if gate["exit_code"] == EXIT_INSTRUMENT:
            exit_code = EXIT_INSTRUMENT
            break
        exit_code = EXIT_REGRESSION

    if failed:
        verdict = FAIL
    elif all(g["status"] == BASELINE for g in gates[1:]):
        verdict = BASELINE
    else:
        verdict = PASS

    return {
        "verdict": verdict,
        "exit_code": exit_code,
        "gates": gates,
        "history_runs": len(history),
        "history_runs_same_set": len(_same_set(history, _digest(result))),
    }


def report(verdict: dict) -> None:
    """Print the gates. Every line carries its bar and where the bar came from,
    because a threshold nobody can re-derive is a threshold somebody edits."""
    print("\n" + "=" * 72)
    print(f"QUALITY GATES — {verdict['verdict']} "
          f"(exit {verdict['exit_code']}), "
          f"{verdict['history_runs']} earlier measurement(s), "
          f"{verdict['history_runs_same_set']} against this reference set")
    print("=" * 72)
    for gate in verdict["gates"]:
        print(f"  {gate['status']:<8} {gate['gate']:<16} {gate['detail']}")
    if verdict["verdict"] == BASELINE:
        print("\n  BASELINE: this reference set has never been measured before, so "
              "no rate\n  gate could apply. This run becomes the bar the next one "
              "is held to.")

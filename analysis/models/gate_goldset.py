#!/usr/bin/env python3
"""A hand-labelled GROUND TRUTH set for the paid LLM gate, and its scorer.

    python3 -m analysis.models.gate_goldset          # describe the set, no key
    python3 ab_models.py --gate-gold                 # score models against it

WHY THIS EXISTS, AND WHAT WAS MISSING BEFORE IT
-----------------------------------------------
`ab_models.py` could already compare two models. What it could not do is say
which of them was RIGHT. Every mode in it scores a challenger's AGREEMENT with
the incumbent, and its own output says so in capitals ("READ THIS AS A FLOOR,
NOT A SCORE"). Agreement is blind in exactly the direction that matters here:
when the incumbent and the challenger are wrong together the metric reads
100%, and when the challenger CORRECTS the incumbent the metric reads as a
regression — which is not hypothetical, it is what happened in the 2026-07-28
gate A/B, where every disagreement was the challenger being right.

So a model swap taken on `ab_models.py` alone is a swap taken on similarity to
a model nobody measured. This file is the missing baseline: 80 real captured
items with a verdict a human wrote after reading each one against
`classify.GATE_SYSTEM`, so accuracy has a denominator.

WHY THE GATE AND NOT EXTRACTION
-------------------------------
Two reasons, one of money and one of honesty.

* MONEY. `cost_projection.py [4]` puts the gate at **$3.68/month of the $6.30
  floor** — the largest single line left once extraction is on the cheapest
  model and leadership is offloaded. `docs/PLAN-gate-to-five-dollars.md` exists
  to remove it. Nothing should be removed before it is measured.
* HONESTY. The gate reads headline + teaser, truncated at `GATE_CHARS`. Every
  item below IS a headline and a teaser, so this set's inputs are the
  production inputs byte for byte. Extraction reads `FULL_READ_CHARS` of
  article body, and this repo does not persist `raw_text` anywhere — no
  column on `signals`, nothing in the gate ledger. An "extraction gold set"
  built from headlines would score a different and easier task while carrying
  extraction's name, and that is the kind of number this project has been
  burned by. Extraction's set has to start with capturing raw text; see
  KNOWN_LIMITS.

WHAT THIS SET CAN AND CANNOT DECIDE
-----------------------------------
It can REJECT a model. It cannot CERTIFY one at 98%. At n=75 scoreable items a
perfect score has a Wilson 95% lower bound near 95%, so "98% or better" is not
a claim 75 items can support in either direction — `docs/PLAN-gate-to-five-
dollars.md` step 0 asks for >=200 for that reason. Everything here reports its
interval so the ceiling is visible rather than remembered. Read KNOWN_LIMITS
before quoting any number out of it.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.recall.stats import wilson

ROOT = Path(__file__).resolve().parent
SET_PATH = ROOT / "goldset-gate-2026-08.json"

#: Read this before quoting a figure from this set. Each entry is a fact about
#: the set that a number computed from it cannot express, and each one is an
#: instruction for whoever assembles the next version.
KNOWN_LIMITS = [
    "ENGLISH ONLY. The live gate answers in 43 languages and 75% of its "
    "traffic is not English (data/gate_labels: 3,634 English of 14,565). A "
    "labeller who cannot read Vietnamese cannot label a Vietnamese item, and "
    "guessing would have been worse than the gap. This set therefore says "
    "NOTHING about the multilingual behaviour that most of the bill pays for.",

    "POSITIVE-HEAVY. 56 YES against 19 NO, where live traffic is roughly 47% "
    "YES (4,561 of 9,716 non-error decisions). Half the set is "
    "tests/fixtures/ab_headlines.txt, which was assembled to exercise "
    "extraction and is mostly real events. So this set measures RECALL (does "
    "the gate keep what it should) far better than it measures PRECISION (does "
    "it drop what it should). Precision is where the money is, because a "
    "false YES buys an extraction call. The next version must draw its "
    "negatives first and its positives second.",

    "SMALL. 75 scoreable items. It can reject a model that misses one in ten. "
    "It cannot separate 98% from 94%. Extend it; do not re-derive it.",

    "NOT AN EXTRACTION SET. The gate's input is headline plus teaser. "
    "Extraction reads the article body, which this repo does not store. Do not "
    "reuse these labels to justify an extraction model swap.",
]

#: Shape the set must keep, in the spirit of `analysis/recall/goldset.py`'s
#: REQUIRED_SHAPE: a set that drifts toward the easy items measures memory.
REQUIRED_SHAPE = {
    "min_items": 60,
    "min_negatives": 15,
    "min_hard": 12,
    #: Two independent provenances, so the set cannot become one fixture.
    "min_provenances": 2,
}


def load(path: Path | None = None) -> dict:
    return json.loads((path or SET_PATH).read_text())


def scoreable(doc: dict) -> list[dict]:
    """Items with a verdict. AMBIGUOUS IS A THIRD STATE AND NOT A PASS.

    Five items carry `gold_is_talent_signal: null` because a careful reader
    could defend either answer against GATE_SYSTEM — a strike ballot, a planned
    sukuk, a CEO appointment whose employer is not named in the text the gate
    is given. Scoring those would charge a model for the rubric's silence. They
    are excluded from the denominator and REPORTED, because five items the
    rubric cannot decide is itself a finding about the rubric.
    """
    return [i for i in doc["items"] if i["gold_is_talent_signal"] is not None]


def score(doc: dict, answers: dict[str, bool]) -> dict:
    """Grade {item_id: YES?} against the hand labels.

    An item the model did not answer is a MISS, never a skip: a gate that
    errors on an item drops it in production, so silence is a NO with extra
    steps.
    """
    items = scoreable(doc)
    tp = fp = tn = fn = 0
    wrong = []
    for item in items:
        gold = item["gold_is_talent_signal"]
        got = bool(answers.get(item["id"], False))
        if gold and got:
            tp += 1
        elif gold and not got:
            fn += 1
            wrong.append((item, "MISSED a real signal"))
        elif not gold and got:
            fp += 1
            wrong.append((item, "kept an item it should have dropped"))
        else:
            tn += 1
    total = len(items)
    correct = tp + tn
    lo, hi = wilson(correct, total)
    return {
        "total": total, "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "accuracy_lo": lo, "accuracy_hi": hi,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        "precision": tp / (tp + fp) if (tp + fp) else 0.0,
        "wrong": wrong,
        "unanswered": [i["id"] for i in items if i["id"] not in answers],
    }


def production_baseline(doc: dict) -> dict:
    """What the LIVE gate scored, from the verdicts the ledger already holds.

    FREE, and it is the first accuracy figure this repo has ever had for the
    gate: `data/gate_labels/labels-2026-08.jsonl` recorded the incumbent's own
    YES/NO on 40 of these items at the time it saw them, so grading it against
    the hand labels costs nothing and calls no model. It covers only the
    ledger half of the set; the ab_headlines half was never gated.
    """
    answers = {i["id"]: i["production_gate"] == "YES"
               for i in doc["items"] if i["production_gate"] in ("YES", "NO")}
    covered = set(answers)
    subset = dict(doc, items=[i for i in doc["items"] if i["id"] in covered])
    out = score(subset, answers)
    out["note"] = ("the LIVE gate's own recorded verdicts, graded — no model "
                   "was called and nothing was spent")
    return out


def describe() -> int:
    doc = load()
    items = doc["items"]
    print(f"gate gold set {doc['version']}: {len(items)} items, "
          f"{len(scoreable(doc))} scoreable")
    yes = sum(1 for i in items if i["gold_is_talent_signal"] is True)
    no = sum(1 for i in items if i["gold_is_talent_signal"] is False)
    print(f"  {yes} YES, {no} NO, {len(items) - yes - no} ambiguous "
          f"(excluded from the denominator, not counted as passes)")
    print(f"  {sum(1 for i in items if i['hard'])} marked hard")
    print("\nKNOWN LIMITS — read before quoting any figure from this set:")
    for line in KNOWN_LIMITS:
        print(f"  * {line}")

    base = production_baseline(doc)
    print(f"\nTHE INCUMBENT GATE, GRADED FOR FREE ({base['note']}):")
    print(f"  {base['correct']}/{base['total']} = {base['accuracy']:.1%} "
          f"(Wilson 95%: {base['accuracy_lo']:.1%}-{base['accuracy_hi']:.1%})")
    print(f"  recall {base['recall']:.1%}, precision {base['precision']:.1%}, "
          f"{base['fn']} missed signal(s), {base['fp']} kept in error")
    for item, why in base["wrong"]:
        print(f"    {why}: {item['text'].splitlines()[0][:66]}")
    print("\n  A challenger has to beat THAT, not agree with it.")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT.parent.parent))
    raise SystemExit(describe())

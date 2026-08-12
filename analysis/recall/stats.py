"""The interval, in one place.

`wilson` lived in `thresholds.py`, where it was used to derive a floor and
never shown to anybody. That was half a measurement: a recall figure published
as a bare percentage invites a reader to compare 38.2% with 34.1% when the two
are the same number on this many events.

It is a leaf module with nothing but stdlib behind it because three different
importers now need it and none of them may end up importing each other:
`goldset.py` sizes a reference set by the interval it can support,
`thresholds.py` derives the gates from it, and `measure_recall.py` publishes it.
`thresholds.wilson` is kept as a re-export so that every caller and test written
against the old home still resolves to this one implementation.
"""

from __future__ import annotations

import math

# 95%, two-sided. Not tunable per-run on purpose: a confidence level that is an
# argument is one somebody widens on the day it fires.
Z = 1.959963984540054


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


def interval(successes: int, total: int) -> dict:
    """The published form: the rate, both bounds and the width, all in points.

    Rounded once, here, so the page, the health digest and the session check
    cannot round the same interval three ways and appear to disagree.
    """
    low, high = wilson(successes, total)
    return {
        "pct": round(100.0 * successes / total, 1) if total else None,
        "low_pct": round(100.0 * low, 1),
        "high_pct": round(100.0 * high, 1),
        "width_pct": round(100.0 * (high - low), 1),
        "successes": successes,
        "total": total,
    }


def widest_possible_width(total: int) -> float:
    """How wide the interval would be at its worst case, p = 0.5.

    What a reference set can be SIZED against before anything has been
    measured. A set assembled to answer a question has to be big enough to
    answer it whatever the answer turns out to be, so the bar cannot be the
    interval the result happened to produce.
    """
    if total <= 0:
        return 1.0
    low, high = wilson(round(total / 2), total)
    return high - low

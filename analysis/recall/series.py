"""The trend, and the work list the measurement emits.

Two jobs that both read the same thing: every dated result in `results/`.

A single recall figure is a verdict. The same figure with a visible history is a
system that measures itself, and the only reason to automate the measurement is
to produce that history. So this reduces every stored result to a comparable
point, and it is careful to keep the gold set identity attached to each point,
because points measured against DIFFERENT reference sets are not strictly
comparable and the page has to be able to say so.

The work list is the other half. A category scoring zero is not a fact to
display, it is an instruction: repair or add feeds there. Writing it to a stable
path is what turns a report into a loop.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# A window this old has stopped teaching us anything. Collection for a closed
# window converges within a few weeks (late write-ups, backfills), after which
# re-measuring the same events measures memory rather than reach.
GOLDSET_STALE_DAYS = 35

# Three consecutive measurements with the same held count against the same set
# means that set has converged, whatever the calendar says.
PLATEAU_RUNS = 3


def _point(result: dict) -> dict:
    """One stored measurement, reduced to what a trend needs."""
    summary = result.get("summary", {})
    gold = result.get("goldset", {})
    return {
        "measured_on": result.get("measured_on"),
        "goldset_version": gold.get("version"),
        "goldset_digest": gold.get("digest"),
        "window": gold.get("window"),
        "overall": summary.get("overall"),
        "by_segment": summary.get("by_segment", {}),
        "by_signal_type": summary.get("by_signal_type", {}),
        "by_geography": summary.get("by_geography", {}),
        "by_source_type": summary.get("by_source_type", {}),
        "by_country": summary.get("by_country", {}),
        # Present only on the US family's results, and absent rather than empty
        # elsewhere, so a trend line for a metro cannot be drawn through the
        # months before metros existed.
        **({"by_metro": summary["by_metro"]} if "by_metro" in summary else {}),
        "defects": summary.get("defects", {}),
    }


def load_series(results_dir: str = RESULTS_DIR) -> list:
    """Every measurement ever recorded, oldest first.

    Historical results are never rewritten. A published figure that cannot be
    re-derived from the file that produced it is not a measurement, it is a
    memory of one.
    """
    if not os.path.isdir(results_dir):
        return []
    points = []
    for name in sorted(os.listdir(results_dir)):
        if not (name.startswith("recall-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(results_dir, name), encoding="utf-8") as handle:
                points.append(_point(json.load(handle)))
        except (OSError, ValueError, KeyError):
            # A corrupt historical file must not take the current run down with
            # it. It is visible by its absence from the series.
            continue
    points.sort(key=lambda p: (p["measured_on"] or "", p["goldset_version"] or ""))
    return points


def _parse(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def goldset_is_due(series: list, current: dict, today: date | None = None) -> dict:
    """Is it time for a NEW reference set?

    Re-running one fixed set forever measures memorisation. Once those specific
    events are held the number walks to 100% and stops meaning anything, which
    is the most flattering way a benchmark can fail. Two independent triggers,
    either of which is enough:

    - the window it covers has aged out, so it no longer describes current reach
    - the number against it has stopped moving, so it has converged early
    """
    today = today or date.today()
    window_end = _parse((current.get("window") or {}).get("end"))
    version = current.get("version")

    if window_end:
        age = (today - window_end).days
        if age > GOLDSET_STALE_DAYS:
            return {
                "due": True,
                "reason": (f"the current set covers a window that closed {age} days ago, "
                           f"so it no longer describes what we reach today"),
            }

    same_set = [p for p in series if p["goldset_version"] == version]
    held = [(p["overall"] or {}).get("held") for p in same_set[-PLATEAU_RUNS:]]
    if len(held) >= PLATEAU_RUNS and len(set(held)) == 1 and held[0] is not None:
        return {
            "due": True,
            "reason": (f"the last {PLATEAU_RUNS} measurements against this set all held "
                       f"{held[0]} events, so it has converged and further runs "
                       f"measure memory rather than reach"),
        }

    return {"due": False, "reason": "the current set is still current and still moving"}


def next_window(current: dict, today: date | None = None) -> dict:
    """The window a fresh set should cover: the calendar month before this one."""
    today = today or date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return {"start": f"{year:04d}-{month:02d}-01", "end": f"{year:04d}-{month:02d}-{last_day:02d}"}


def build_worklist(result: dict, series: list, goldset: dict,
                   today: date | None = None, family=None) -> dict:
    """What this measurement says somebody should go and fix.

    Ordered by how much of the gold set a cell accounts for, because a country
    with one event scoring zero is noise and a source type with fifty is a
    priority. Every entry carries its counts for the same reason every
    percentage on the page does.
    """
    summary = result.get("summary", {})

    # Which group carries this family's geography. Worldwide it is the country;
    # inside one country it is the metro, and reading `by_country` there would
    # produce a work list of one cell called US that can never be a gap and can
    # never be an instruction. The keys keep their names so every consumer of
    # this file, including the health digest, reads one schema.
    spread_group = "by_metro" if "by_metro" in summary else "by_country"

    def cells(group, predicate):
        out = []
        for key, cell in (summary.get(group) or {}).items():
            if predicate(cell):
                out.append({"key": key, "total": cell["total"], "held": cell["held"],
                            "held_pct": cell["held_pct"]})
        return sorted(out, key=lambda c: (-c["total"], c["key"]))

    missed = [i for i in result.get("items", []) if i["verdict"] == "MISSED"]

    due = goldset_is_due(series, goldset, today=today)
    window = next_window(goldset, today=today)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "measured_on": result.get("measured_on"),
        "goldset_version": (result.get("goldset") or {}).get("version"),
        "overall": summary.get("overall"),

        # Countries where we found nothing at all. This is the feed roadmap:
        # a zero here means we have no working route into that country's press.
        "spread_group": spread_group,
        "zero_countries": cells(spread_group, lambda c: c["held"] == 0),
        "weak_countries": cells(spread_group,
                                lambda c: c["held"] and (c["held_pct"] or 0) < 50),

        # Which kind of document we are failing to read. A filing we miss is a
        # broken collector; a trade-press story we miss is a missing feed.
        "weak_source_types": cells("by_source_type", lambda c: (c["held_pct"] or 0) < 50),
        "weak_segments": cells("by_segment", lambda c: (c["held_pct"] or 0) < 50),

        # Field defects need an extractor fix, not a feed. Kept separate so the
        # two never compete for the same attention.
        "field_defects": summary.get("defects", {}),

        # A sample of what was missed, so whoever picks this up can see the
        # shape of the gap without opening the full result file.
        "missed_examples": [
            {"company": m["company"], "country": m["country"],
             "signal_type": m["signal_type"], "source_name": m["source_name"],
             "source_url": m["source_url"], "event_date": m["event_date"]}
            for m in missed[:40]
        ],
        "missed_total": len(missed),

        "next_goldset": {
            "due": due["due"],
            "reason": due["reason"],
            "suggested_window": window,
            # Paste-ready, because the loop this project actually runs is "get
            # the alert, paste one line, do the one thing". An instruction that
            # needs to be reconstructed from memory is a step that gets skipped,
            # and a skipped step here means the number quietly stops meaning
            # anything while continuing to be published.
            "instruction": (US_INSTRUCTION if spread_group == "by_metro" else
                WORLD_INSTRUCTION).format(
                    start=window["start"], end=window["end"],
                    month=window["start"][:7]),
        },
    }


# The paste-ready instruction, one per family. Separated from the code that
# chooses between them because the choice is one line and the text is the part
# somebody actually has to follow: a US set assembled to the worldwide recipe
# would be judged by the US bars and fail on every one of them.
WORLD_INSTRUCTION = (
    "Build a new recall gold set for "
    "{start} to {end} and seal it. Run several "
    "INDEPENDENT research passes, one per segment (US large funding, "
    "US small funding, Europe, Asia-Pacific, Israel, rest of world, "
    "US leadership, non-US leadership), each forbidden from "
    "consulting asktherecruiter.com or any of our own data: a set "
    "drawn from what we already hold measures nothing. Every item "
    "needs a source URL that a stranger can open, and quotas must be "
    "filled per segment rather than taking whatever was easy to "
    "find. Check every URL resolves, drop only the unreachable ones, "
    "and drop nothing at all once matching has begun. Write it to "
    "analysis/recall/goldset-{month}.json with "
    "sealed=true, family=\"world\" and an assembled_on date, matching the shape "
    "of the previous set. `python3 measure_recall.py --check` must pass "
    "before it is used; analysis/recall/goldset.py REQUIRED_SHAPE is "
    "the guard that stops a set being quietly rebuilt out of easy US "
    "filings. The newest set on disk is the one measured, so nothing "
    "else needs changing."
)

US_INSTRUCTION = (
    "Build a new UNITED STATES recall gold set for "
    "{start} to {end} and seal it. Run eight INDEPENDENT research passes, one "
    "per metro and event type (San Francisco Bay Area, New York City, Austin, "
    "rest of the United States, each for funding and for leadership), every "
    "pass forbidden from consulting asktherecruiter.com, this repository, our "
    "database or our source registry: a set drawn from the feeds we already "
    "watch measures memory rather than reach. Enumerate candidates "
    "CHRONOLOGICALLY within the window and take them in that order rather than "
    "picking the interesting ones. A commercial deal database or people-data "
    "service may be used to FIND a candidate and must never be stored, named "
    "or linked: every item cites the original publisher, which is the "
    "company's own announcement, the filing, or a named news outlet's own "
    "article, and no commercial service's name may appear anywhere in the "
    "repository. Fetch every source URL and confirm the company, the date, the "
    "metro and the amount on the page itself before writing the row down; drop "
    "what will not verify and record why. Write it to "
    "analysis/recall/us/goldset-us-{month}.json with sealed=true, "
    "family=\"us\", a metro on every item and an assembled_on date. "
    "`python3 measure_recall.py --family us --check` must pass before it is "
    "used; analysis/recall/goldset.py US_REQUIRED_SHAPE is the guard that "
    "stops the set being quietly rebuilt out of large SEC filers, which is the "
    "one part of the US this tracker already reads well."
)

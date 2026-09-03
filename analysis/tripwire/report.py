"""Turn a run into the two things it owes: a dated record and a work list.

Shapes mirror analysis/recall deliberately. `data/recall_worklist.json` already
taught the health machinery to read "here is a stable path holding what somebody
should go and fix", and a second instrument inventing a second shape for the
same idea would be two things to learn instead of one.

The cost figures are the point of the file. Discovery is the only part of this
product that spends money to look rather than to read, so a tripwire that costs
more than it finds has to be visible AS THAT, in the same file that reports what
it found — not inferable from a billing dashboard three weeks later.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime

from pipeline import provider_names

from .diff import HELD, MISSING, UNUSABLE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RESULTS_DIR = os.path.join(HERE, "results")
WORKLIST_PATH = os.path.join(ROOT, "data", "tripwire_worklist.json")


def _redact_for_write(obj):
    """Recursively redact every string leaf before it can reach a written
    file.

    Two different things land in a tripwire result: the MODEL's own claims
    (`ask.py`'s `claimed_outlet` / `claimed_url`, free text nothing here
    controls) and rows pulled from OUR OWN `signals` table (`diff.py`'s
    `matched.source_url`, which legitimately holds a provider's domain
    because the database itself is exempt from the no-provider-names rule —
    it is the system's memory, not a published artifact). Both are fine to
    hold; neither is fine to publish verbatim. Rather than name every field
    that could carry either kind of text — and miss the next one a future
    query or a future schema column adds — this walks the whole structure
    and redacts every string, so a field nobody thought to allowlist cannot
    become the next tripwire-YYYY-MM-DD.json leak the way `source_url` just
    did. See `pipeline/provider_names.py` for the redaction itself.
    """
    if isinstance(obj, str):
        return provider_names.redact(obj)
    if isinstance(obj, dict):
        return {key: _redact_for_write(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_redact_for_write(value) for value in obj]
    return obj

# What the chase collector reports as, so a confirmed miss can be counted.
CHASE_COLLECTOR = "tripwire_chase"


def _rate(numerator: float, denominator: float):
    """A per-unit cost that always travels with its counts, or None when there
    is nothing to divide by. A bare figure with no denominator is not a result.
    """
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def tally(results: list[dict]) -> dict:
    counts = {"leads": len(results), "held": 0, "missing": 0, "unusable": 0}
    for entry in results:
        if entry["verdict"] == HELD:
            counts["held"] += 1
        elif entry["verdict"] == MISSING:
            counts["missing"] += 1
        elif entry["verdict"] == UNUSABLE:
            counts["unusable"] += 1
    counts["usable"] = counts["leads"] - counts["unusable"]
    return counts


def _group(results: list[dict], key_fn) -> dict:
    out: dict[str, dict] = {}
    for entry in results:
        key = key_fn(entry)
        if not key:
            continue
        cell = out.setdefault(key, {"leads": 0, "held": 0, "missing": 0, "unusable": 0})
        cell["leads"] += 1
        cell[{HELD: "held", MISSING: "missing", UNUSABLE: "unusable"}[entry["verdict"]]] += 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["missing"], kv[0])))


def by_country(results: list[dict]) -> dict:
    """Misses per country, which is the feed roadmap.

    Keyed on the country the lead is ABOUT, not the query that found it: an
    industry sweep that keeps surfacing missing Japanese companies is saying
    something about Japan's feeds, and grouping it under 'technology' would
    hide exactly that.
    """
    return _group(results, lambda e: e.get("claimed_country") or
                  (e.get("dimension_key") if e.get("dimension") == "country" else ""))


def by_industry(results: list[dict]) -> dict:
    return _group(results, lambda e: e.get("claimed_industry") or
                  (e.get("dimension_key") if e.get("dimension") == "industry" else ""))


def lifetime_cost(results_dir: str = RESULTS_DIR) -> float:
    """Everything the tripwire has ever spent, from its own dated files."""
    total = 0.0
    if not os.path.isdir(results_dir):
        return total
    for name in sorted(os.listdir(results_dir)):
        if not (name.startswith("tripwire-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(results_dir, name), encoding="utf-8") as handle:
                total += float(((json.load(handle) or {}).get("cost") or {})
                               .get("run_usd") or 0)
        except (OSError, ValueError, TypeError):
            continue
    return round(total, 4)


def confirmed_misses(conn: sqlite3.Connection | None) -> int | None:
    """How many leads became STORED RECORDS.

    This is the only honest denominator for "what did discovery cost?". A lead
    is a claim; a confirmed miss is a row we now hold, sourced to the
    publisher's own article, that we did not hold before the tripwire pointed
    at it. Returns None when the chase has never run, so the caller prints
    'not yet measurable' rather than a division by zero dressed up as a number.
    """
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE is_current = 1 AND collector = ?",
            (CHASE_COLLECTOR,),
        ).fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else 0


def cost_block(run_usd: float, queries: int, counts: dict, *,
               results_dir: str = RESULTS_DIR,
               conn: sqlite3.Connection | None = None) -> dict:
    confirmed = confirmed_misses(conn)
    lifetime = round(lifetime_cost(results_dir) + run_usd, 4)
    return {
        "run_usd": round(run_usd, 4),
        "queries": queries,
        "usd_per_query": _rate(run_usd, queries),
        "usd_per_lead": _rate(run_usd, counts["usable"]),
        "usd_per_candidate_miss": _rate(run_usd, counts["missing"]),
        # Lifetime, because one run's confirmations mostly land on the next
        # chase. Dividing a single run's spend by a single run's confirmations
        # would swing between zero and absurd and mean nothing either way.
        "lifetime_usd": lifetime,
        "confirmed_misses_lifetime": confirmed,
        "usd_per_confirmed_miss": (_rate(lifetime, confirmed)
                                   if confirmed else None),
        "confirmed_miss_note": (
            "a confirmed miss is a stored record the chase found from a lead — "
            "not a lead, and never the model's own claim"
        ),
    }


def build_result(plan: dict, queries: list[dict], results: list[dict],
                 cost: dict, diagnostics: list[dict]) -> dict:
    counts = tally(results)
    return {
        "ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ran_on": date.today().isoformat(),
        "plan": plan,
        "queries_asked": [
            {"dimension": q["dimension"], "key": q["key"], "tier": q["tier"],
             "measured": q.get("measured")}
            for q in queries
        ],
        "counts": counts,
        "by_country": by_country(results),
        "by_industry": by_industry(results),
        "cost": cost,
        "diagnostics": diagnostics,
        # Full leads, verdicts included, so a later session can re-derive the
        # work list without re-asking anything.
        "leads": results,
    }


def build_worklist(result: dict) -> dict:
    """What somebody — or the chase collector — should do next.

    Only MISSING leads travel here. HELD ones have done their job by proving
    the query was answerable and we answered it.
    """
    missing = [entry for entry in result["leads"] if entry["verdict"] == MISSING]
    return {
        "generated_at": result["ran_at"],
        "ran_on": result["ran_on"],
        "basis": result["plan"]["basis"],
        "counts": result["counts"],
        "cost": result["cost"],
        # The two numbers the health machinery consumes. A country whose miss
        # count stays high run after run is not a country we are unlucky in; it
        # is a country whose feeds need repair or addition.
        "country_misses": {k: v["missing"] for k, v in result["by_country"].items()
                           if v["missing"]},
        "industry_misses": {k: v["missing"] for k, v in result["by_industry"].items()
                            if v["missing"]},
        "missing_total": len(missing),
        "leads": missing,
        "instruction": (
            "Every entry is a LEAD, never a record. Nothing here may be stored "
            "as written: the claimed_* fields are one model's assertion and are "
            "wrong often enough that treating them as data would put invented "
            "figures on the site. To act on them run "
            "`python run_collect.py --source tripwire_chase --dry-run`, which "
            "searches for each employer's own publisher coverage and sends "
            "whatever it finds through classify -> validate -> store like any "
            "other candidate. A lead nothing can be found for is a lead that "
            "was wrong, and it costs nothing further."
        ),
    }


def write(result: dict, worklist: dict, *, results_dir: str = RESULTS_DIR,
          worklist_path: str = WORKLIST_PATH) -> tuple[str, str]:
    # Redact at the write, not after it — `pipeline/provider_names.py`'s own
    # rule, applied here because this is the choke point both committed files
    # pass through. Whatever slipped past `usable()`, a query, or a schema
    # change is caught here rather than by the CI guard three commits later.
    result = _redact_for_write(result)
    worklist = _redact_for_write(worklist)

    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, f"tripwire-{result['ran_on']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
        handle.write("\n")

    os.makedirs(os.path.dirname(worklist_path), exist_ok=True)
    with open(worklist_path, "w", encoding="utf-8") as handle:
        json.dump(worklist, handle, indent=1)
        handle.write("\n")
    return path, worklist_path

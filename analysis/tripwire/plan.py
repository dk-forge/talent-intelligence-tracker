"""What the tripwire asks about on a given run, and what that may cost.

Two dimensions, decided deliberately:

  * COUNTRIES, rotating, prioritised by MEASURED RECALL. The recall run already
    records which countries held nothing (analysis/recall/results). Those are
    where a query is most likely to find something we do not have, so they take
    most of the budget. A country we already cover earns a rare check.
  * INDUSTRIES, a full sweep of all 18, on a monthly cadence. Geography and
    sector fail differently: a country gap is a missing feed, a sector gap is a
    vocabulary we never learned. Sweeping all of them costs one run a month.

Cities are deliberately NOT a dimension. You find a Tel Aviv round by asking
about Israel and reading the city off the article; adding cities multiplies the
query count for almost no new discovery.

Everything here is a pure function of a recall result and a date, so the plan a
run used is reproducible from the result file it wrote.
"""

from __future__ import annotations

import json
import os
from datetime import date

from pipeline.vocab import COUNTRY_NAMES, INDUSTRIES

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RECALL_RESULTS = os.path.join(ROOT, "analysis", "recall", "results")
RESULTS_DIR = os.path.join(HERE, "results")


# --- the budget, which is the constraint everything else derives from ------
#
# The owner's ceiling is ~$5/month of LLM spend for the WHOLE product, and the
# daily classification pipeline already measures ~$3/month of that (see the
# arithmetic in run_collect.py and spend.py). Search-backed queries are the
# expensive kind — one is worth tens of ordinary classifications — so discovery
# gets the smallest slice that can still say something, and the query count is
# DERIVED from the money rather than the money being estimated from a query
# count somebody picked.
TRIPWIRE_MONTHLY_USD = 1.00

# Deliberately pessimistic: a search-backed request bills tokens plus a
# per-search fee, and the exact figure depends on which model the run used. The
# run measures what it actually spent and records it in the result file, so
# this estimate only ever sizes the plan — it never reports the cost.
USD_PER_QUERY_ESTIMATE = 0.02

# What a query ACTUALLY cost, the first time this instrument issued live
# queries: run 30506967802 on 2026-07-30, 17 search-backed queries against
# `perplexity/sonar`, $0.0977 billed, from OpenRouter's own `usage.cost` and not
# from arithmetic on a price list. The spread across those 17 was $0.0054 to
# $0.0060, so the figure is stable rather than an average hiding a tail; the
# Israel query, the one country a human could check by eye, cost $0.0059 and
# returned 8 leads.
#
# It is recorded and NOT substituted for the estimate above. Feeding the
# measured price back into the sizing arithmetic would take COUNTRIES_PER_RUN
# from 4 to 19 and quadruple the bill — the derivation would still be correct
# and the run would still be inside its cap, but the cap would stop being the
# thing that constrains the design. The estimate sizes the plan; this says what
# the plan costs. Two numbers doing two jobs, and the gap between them is the
# safety margin.
USD_PER_QUERY_MEASURED = 0.0057
USD_PER_QUERY_MEASURED_SOURCE = (
    "run 30506967802, 2026-07-30: 17 queries, $0.0977 billed, perplexity/sonar")

# Twice a week. Weekly makes a country come round too rarely to be a tripwire;
# daily spends the month's budget in a week.
RUNS_PER_MONTH = 8

# Every industry, every sweep. Eighteen is the whole vocabulary, so a partial
# sweep would only ever be a partial answer to "which sector are we blind to?".
INDUSTRY_SWEEP_QUERIES = len(INDUSTRIES)

QUERIES_PER_MONTH = int(TRIPWIRE_MONTHLY_USD / USD_PER_QUERY_ESTIMATE)
COUNTRIES_PER_RUN = max(
    1, (QUERIES_PER_MONTH - INDUSTRY_SWEEP_QUERIES) // RUNS_PER_MONTH)

# The hard per-run guard. A run that would issue more than this stops instead of
# spending: the sweep run is the biggest one there is, and nothing legitimate
# exceeds it.
MAX_QUERIES_PER_RUN = COUNTRIES_PER_RUN + INDUSTRY_SWEEP_QUERIES

# Share of each run's country slots that go to countries measured at zero
# recall. They are where the misses are; the remainder keeps weakly-covered and
# well-covered countries in the rotation so a regression somewhere we thought
# was fine still surfaces.
ZERO_TIER_SHARE = 0.75

# How far back a query asks. A country comes round roughly monthly at this
# cadence, so a window shorter than the cycle would leave a blind gap between
# visits — the one failure a tripwire must not have.
LOOKBACK_DAYS = 45

# Items requested per query. Past this the model pads with older or vaguer
# items, which costs tokens and produces leads the diff throws away.
LEADS_PER_QUERY = 8

TIER_ZERO = "zero"            # measured, held nothing
TIER_WEAK = "weak"            # measured, held under half
TIER_COVERED = "covered"      # measured, held half or more
TIER_UNMEASURED = "unmeasured"  # never in a gold set, so we know nothing

TIER_ORDER = (TIER_ZERO, TIER_WEAK, TIER_COVERED, TIER_UNMEASURED)

# Used only when no recall result exists yet. It is a guess, and the plan says
# so in its `basis` field rather than presenting it as measurement: these are
# simply the largest funding and hiring markets outside the US, which is where
# an unmeasured tracker is most likely to be missing volume.
DEFAULT_COUNTRY_ORDER = (
    "IL", "GB", "IN", "CA", "DE", "FR", "SG", "AU", "JP", "SE",
    "NL", "ES", "BR", "AE", "CH", "IE", "KR", "MX", "ZA", "NO",
)


def _weak_threshold(cell: dict) -> str:
    held = cell.get("held") or 0
    pct = cell.get("held_pct") or 0
    if not held:
        return TIER_ZERO
    return TIER_WEAK if pct < 50 else TIER_COVERED


def latest_recall(results_dir: str = RECALL_RESULTS) -> dict | None:
    """The newest recall measurement, or None if none has ever run.

    Corrupt files are skipped rather than fatal, exactly as series.py does: a
    tripwire that cannot run because a historical file is unreadable is worse
    than one that falls back to the default order and says so.
    """
    if not os.path.isdir(results_dir):
        return None
    for name in sorted(os.listdir(results_dir), reverse=True):
        if not (name.startswith("recall-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(results_dir, name), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            continue
    return None


def country_tiers(recall: dict | None) -> list[dict]:
    """Every country we know about, tiered by what recall measured.

    Ordered so the most informative query comes first: zero-recall countries
    before weak ones, and within a tier the country the gold set had most to say
    about, because a country with four measured events scoring zero is a real
    hole and one with a single event is noise.
    """
    tiers: list[dict] = []
    seen: set[str] = set()

    by_country = ((recall or {}).get("summary") or {}).get("by_country") or {}
    for iso2, cell in by_country.items():
        tier = _weak_threshold(cell)
        tiers.append({
            "iso2": iso2,
            "name": COUNTRY_NAMES.get(iso2, iso2),
            "tier": tier,
            "measured": {"total": cell.get("total"), "held": cell.get("held"),
                         "held_pct": cell.get("held_pct")},
        })
        seen.add(iso2)

    for iso2 in DEFAULT_COUNTRY_ORDER:
        if iso2 in seen:
            continue
        tiers.append({
            "iso2": iso2,
            "name": COUNTRY_NAMES.get(iso2, iso2),
            "tier": TIER_UNMEASURED,
            "measured": None,
        })
        seen.add(iso2)

    def sort_key(entry):
        measured = entry["measured"] or {}
        return (
            TIER_ORDER.index(entry["tier"]),
            -(measured.get("total") or 0),
            DEFAULT_COUNTRY_ORDER.index(entry["iso2"])
            if entry["iso2"] in DEFAULT_COUNTRY_ORDER else 99,
            entry["iso2"],
        )

    return sorted(tiers, key=sort_key)


def _slice(pool: list[dict], cycle: int, count: int) -> list[dict]:
    """A deterministic rotating slice, so consecutive runs walk the pool rather
    than re-asking the same head of it (the trap the sibling's chase fell into).
    """
    if not pool or count <= 0:
        return []
    count = min(count, len(pool))
    start = (cycle * count) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(count)]


def countries_for_run(tiers: list[dict], cycle: int,
                      per_run: int = COUNTRIES_PER_RUN) -> list[dict]:
    """This run's countries: mostly zero-recall, with the rest kept in view.

    `cycle` is a run counter (day of year x runs per day + run index), so the
    slice is reproducible from the date alone and the pool walks forward.
    """
    zeros = [t for t in tiers if t["tier"] == TIER_ZERO]
    rest = [t for t in tiers if t["tier"] != TIER_ZERO]

    zero_slots = min(len(zeros), round(per_run * ZERO_TIER_SHARE))
    if not rest:
        zero_slots = min(len(zeros), per_run)
    rest_slots = per_run - zero_slots

    chosen = _slice(zeros, cycle, zero_slots) + _slice(rest, cycle, rest_slots)

    # Deduplicate defensively: a tiny pool can hand back the same country twice
    # and paying for the same query twice is exactly what the budget forbids.
    out, seen = [], set()
    for entry in chosen:
        if entry["iso2"] in seen:
            continue
        seen.add(entry["iso2"])
        out.append(entry)
    return out


def industries_due(results_dir: str = RESULTS_DIR, today: date | None = None) -> bool:
    """Has the full industry sweep already run this calendar month?

    Derived from the dated result files rather than from a stored counter, so a
    missed run self-corrects and a replayed one cannot double-charge.
    """
    today = today or date.today()
    month = today.strftime("%Y-%m")
    if not os.path.isdir(results_dir):
        return True
    for name in sorted(os.listdir(results_dir)):
        if not (name.startswith("tripwire-") and name.endswith(".json")):
            continue
        if not name[len("tripwire-"):].startswith(month):
            continue
        try:
            with open(os.path.join(results_dir, name), encoding="utf-8") as handle:
                plan = (json.load(handle) or {}).get("plan") or {}
        except (OSError, ValueError):
            continue
        if plan.get("industries"):
            return False
    return True


def build_plan(*, cycle: int, recall: dict | None = None,
               per_run: int = COUNTRIES_PER_RUN,
               sweep_industries: bool | None = None,
               results_dir: str = RESULTS_DIR,
               today: date | None = None,
               limit: int | None = None) -> dict:
    """The whole plan for one run, with the reasoning attached.

    Carries its own provenance because a work list that says "we asked about
    Japan" is much less useful than one that says "we asked about Japan because
    the last recall measurement held 0 of 4 Japanese events".
    """
    today = today or date.today()
    tiers = country_tiers(recall)
    countries = countries_for_run(tiers, cycle, per_run)

    if sweep_industries is None:
        sweep_industries = industries_due(results_dir, today)
    industries = list(INDUSTRIES) if sweep_industries else []

    plan = {
        "cycle": cycle,
        "planned_on": today.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "basis": (
            f"recall measured {recall.get('measured_on')} against gold set "
            f"{(recall.get('goldset') or {}).get('version')}"
            if recall else
            "DEFAULT ORDER — no recall measurement on file, so the country "
            "priority below is a stated guess, not a measurement"
        ),
        "countries": countries,
        "industries": industries,
        "industry_sweep_reason": (
            "monthly full sweep, not yet run this month" if industries
            else "the full sweep already ran this month"
        ),
    }

    if limit is not None:
        # An explicit cheap run. Countries are trimmed before industries: the
        # sweep is the part that answers a question a partial sweep cannot.
        budget = max(0, limit)
        plan["countries"] = plan["countries"][:budget]
        remaining = max(0, budget - len(plan["countries"]))
        plan["industries"] = plan["industries"][:remaining]
        plan["limited_to"] = limit

    plan["query_count"] = len(plan["countries"]) + len(plan["industries"])
    plan["estimated_usd"] = round(plan["query_count"] * USD_PER_QUERY_ESTIMATE, 4)
    return plan


def monthly_projection() -> dict:
    """What this design costs a month, at the pessimistic per-query price.

    Printed by every run so the ceiling is never something anyone has to
    re-derive from constants scattered across a file.
    """
    country_queries = COUNTRIES_PER_RUN * RUNS_PER_MONTH
    total = country_queries + INDUSTRY_SWEEP_QUERIES
    return {
        "runs_per_month": RUNS_PER_MONTH,
        "countries_per_run": COUNTRIES_PER_RUN,
        "country_queries": country_queries,
        "industry_queries": INDUSTRY_SWEEP_QUERIES,
        "queries_per_month": total,
        "usd_per_query_estimate": USD_PER_QUERY_ESTIMATE,
        "projected_usd_per_month": round(total * USD_PER_QUERY_ESTIMATE, 2),
        "cap_usd_per_month": TRIPWIRE_MONTHLY_USD,
        # Both prices, always. A projection quoted only at the pessimistic
        # estimate reads as the bill and is 3.5x it; one quoted only at the
        # measured price hides the margin that makes the cap safe.
        "usd_per_query_measured": USD_PER_QUERY_MEASURED,
        "usd_per_query_measured_source": USD_PER_QUERY_MEASURED_SOURCE,
        "measured_usd_per_month": round(total * USD_PER_QUERY_MEASURED, 2),
        "estimate_over_measured": round(
            USD_PER_QUERY_ESTIMATE / USD_PER_QUERY_MEASURED, 1),
    }

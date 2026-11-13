#!/usr/bin/env python3
"""Read this repo's actual state. Run it first, every session.

No dependencies, no network, no keys. It answers the questions a new session
would otherwise waste half an hour re-deriving: what is live, what is stale,
what is broken, and what the honest coverage claim is right now.

Exit codes: 0 healthy | 2 something needs a human
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import source_registry as registry
# The staleness leashes live in health_digest and are imported, never restated.
# They were duplicated here as one global 36 hours, which was right while every
# collector was daily or dispatch-only and became wrong the moment monthly ones
# shipped: this script would have reported the pay-gap collector as stale for
# three weeks in four, and exited 2 on a healthy repo. health_digest imports
# nothing outside the standard library at module scope, so this keeps the
# no-dependencies promise.
from health_digest import DEFAULT_MAX_AGE_HOURS, MAX_AGE_HOURS

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "talent_intel.db"

LIVE_URL = "https://asktherecruiter.com/blog/talent-intelligence-tracker/"
SIBLING_URL = "https://asktherecruiter.com/blog/ai-layoff-tracker/"


def main() -> int:
    problems: list[str] = []

    print("=" * 64)
    print("TALENT INTELLIGENCE TRACKER — OPS STATUS")
    print("=" * 64)

    if not DB.exists():
        print("\n[!] No database yet. Nothing has been collected.")
        print("    Next: python run_collect.py --dry-run --offline")
        return 2

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    problems += _report_collection_armed()
    problems += _report_data(conn)
    problems += _report_health(conn)
    problems += _report_coverage(conn)
    _report_surfaces()
    _report_spend()

    print("\n" + "-" * 64)
    if problems:
        print(f"ACTION NEEDED: {len(problems)} item(s)")
        for p in problems:
            print(f"  -> {p}")
        return 2

    print("All clear.")
    return 0


def _report_collection_armed() -> list[str]:
    """Is anything actually collecting? A session that assumes yes when the
    answer is no will misread every number below."""
    workflow = ROOT / ".github" / "workflows" / "collect.yml"
    if not workflow.exists():
        return ["no collect.yml — nothing collects at all"]

    armed = any(
        line.strip().startswith("- cron:")
        for line in workflow.read_text().splitlines()
    )

    print("\n[0] COLLECTION  " + ("ARMED — runs on schedule" if armed
                                  else "DORMANT — schedule commented out"))
    if not armed:
        print("    Nothing is being collected. Arm it by uncommenting the")
        print("    schedule in .github/workflows/collect.yml (needs")
        print("    OPENROUTER_API_KEY in repo secrets first).")
    return []


def _report_data(conn) -> list[str]:
    problems = []
    total = conn.execute("SELECT COUNT(*) FROM signals WHERE is_current = 1").fetchone()[0]
    revisions = conn.execute("SELECT COUNT(*) FROM signals WHERE is_current = 0").fetchone()[0]

    print(f"\n[1] DATA  {total} current signals ({revisions} superseded revisions)")

    if total == 0:
        print("    Empty. No collector has stored anything yet.")
        return problems

    by_pillar = conn.execute(
        "SELECT pillar, COUNT(*) n FROM signals WHERE is_current = 1 GROUP BY pillar ORDER BY n DESC"
    ).fetchall()
    for row in by_pillar:
        print(f"      {row['pillar']:<22} {row['n']}")

    by_conf = conn.execute(
        "SELECT confidence, COUNT(*) n FROM signals WHERE is_current = 1 GROUP BY confidence"
    ).fetchall()
    print("    confidence: " + ", ".join(f"{r['confidence']}={r['n']}" for r in by_conf))

    # Every record must have a source. This is the product's core promise, so
    # it is checked rather than assumed.
    unsourced = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE is_current = 1 AND (source_url IS NULL OR source_url = '')"
    ).fetchone()[0]
    if unsourced:
        problems.append(f"{unsourced} signal(s) have no source_url — the one thing that must never happen")

    newest = conn.execute(
        "SELECT MAX(captured_at) FROM signals WHERE is_current = 1"
    ).fetchone()[0]
    print(f"    newest capture: {newest}")

    return problems


def _report_health(conn) -> list[str]:
    problems = []
    print("\n[2] COLLECTORS")

    rows = conn.execute(
        """
        SELECT collector, status, items_found, items_stored, detail, MAX(run_at) run_at
          FROM source_health GROUP BY collector ORDER BY collector
        """
    ).fetchall()

    if not rows:
        print("    No collector has reported yet.")
        return problems

    now = datetime.now(timezone.utc)
    for row in rows:
        age = ""
        limit = MAX_AGE_HOURS.get(row["collector"], DEFAULT_MAX_AGE_HOURS)
        try:
            last = datetime.fromisoformat(row["run_at"])
            hours = (now - last).total_seconds() / 3600
            age = f"{hours:.0f}h ago"
            if hours > limit:
                problems.append(
                    f"{row['collector']} last ran {hours:.0f}h ago — stale "
                    f"(its leash is {limit}h)"
                )
        except (TypeError, ValueError):
            pass

        flag = "OK      " if row["status"] == "ok" else row["status"].upper().ljust(8)
        print(f"    {flag} {row['collector']:<16} found={row['items_found']} "
              f"stored={row['items_stored']}  {age}")
        if row["detail"]:
            print(f"             {row['detail'][:70]}")

        if row["status"] != "ok":
            problems.append(f"{row['collector']} is {row['status']} — {row['detail'] or 'no detail'}")

    # A collector that has never reported at all shows up nowhere in the query
    # above, so without this it is invisible: the ledger looks healthy because
    # the silent one is not in it. Named from the leash table rather than by
    # importing run_collect, which would drag in `requests` and break the
    # no-dependencies promise this script makes.
    never = sorted(set(MAX_AGE_HOURS) - {r["collector"] for r in rows})
    for name in never:
        print(f"    ------   {name:<16} has never reported in this database")

    return problems


def _report_coverage(conn) -> list[str]:
    """The honest claim, straight from the registry. A market is covered when
    it has a working connector, a health check and a passing test — never
    because it appears in a list.

    The registry says what we are ALLOWED to claim; the database says what we
    actually hold. Those are two different facts and this prints both, because
    a promoted market with no rows behind it is the exact lie the tier system
    exists to prevent.
    """
    problems = []
    print("\n[3] COVERAGE  (claim allowed, per tier)")
    by_tier: dict[str, list[str]] = {}
    for entry in registry.coverage_manifest():
        by_tier.setdefault(entry["status"], []).append(entry["iso2"])

    for tier in (registry.RECONCILED, registry.STRUCTURED_OFFICIAL, registry.DISCOVERY_ONLY):
        codes = by_tier.get(tier, [])
        if codes:
            print(f'    {tier:<22} {len(codes):>2}  "{registry.TIER_PUBLIC_CLAIM[tier]}"'
                  f'  [{", ".join(sorted(codes))}]')

    rows = {
        (r["country"], r["collector"]): r["n"]
        for r in conn.execute(
            "SELECT country, collector, COUNT(*) n FROM signals "
            "WHERE is_current = 1 AND country IS NOT NULL AND country != '' "
            "GROUP BY country, collector"
        )
    }
    reported = {
        r[0] for r in conn.execute("SELECT DISTINCT collector FROM source_health")
    }
    for market in registry.MARKETS:
        if market.status == registry.DISCOVERY_ONLY:
            continue
        # google_news is excluded on purpose: news discovery is what every
        # market already has, so counting it here would let a promoted market
        # look backed by structured data it does not hold.
        official = [s for s in market.live_sources if s != "google_news"]
        held = {name: rows.get((market.iso2, name), 0) for name in official}
        detail = ", ".join(
            f"{name} {n}" if name in reported else f"{name} not yet run here"
            for name, n in held.items()
        )
        print(f"    {market.iso2}  {sum(held.values())} structured signal(s)  ({detail})")

        # A collector that has RUN and stored nothing for the market it was
        # promoted on is the silent zero this project refuses to tolerate. One
        # that has never run in this checkout is a different thing entirely —
        # usually a database behind main — and section [2] already says so, so
        # it is printed and not escalated.
        ran_and_empty = [n for n in official if n in reported and not held[n]]
        if ran_and_empty and not sum(held.values()):
            problems.append(
                f"{market.iso2} claims structured coverage but "
                f"{' and '.join(ran_and_empty)} has run and stored nothing for it "
                "— fix the collector, or demote the market"
            )

    candidates = sum(len(m.candidate_official_sources) for m in registry.MARKETS)
    print(f"    {candidates} researched official source(s) not yet built (roadmap, not coverage)")
    return problems


def _report_spend() -> None:
    """Spend needs a key, which ops_status deliberately does not require, so
    this only points at where to look."""
    print("\n[5] SPEND")
    print("    python spend.py            (needs OPENROUTER_API_KEY)")
    print("    Enforced before every collection run via spend.py --enforce")


def _report_surfaces() -> None:
    print("\n[4] SURFACES")
    print(f"    dashboard  {LIVE_URL}")
    print(f"    sibling    {SIBLING_URL}  (layoffs are READ from its API, never collected here)")
    print(f"    repo       https://github.com/dk-forge/talent-intelligence-tracker")


if __name__ == "__main__":
    sys.exit(main())

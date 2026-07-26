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

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "talent_intel.db"
STALE_AFTER_HOURS = 36  # two missed runs on a 2x/day cadence

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

    problems += _report_data(conn)
    problems += _report_health(conn)
    _report_coverage()
    _report_surfaces()

    print("\n" + "-" * 64)
    if problems:
        print(f"ACTION NEEDED: {len(problems)} item(s)")
        for p in problems:
            print(f"  -> {p}")
        return 2

    print("All clear.")
    return 0


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
        try:
            last = datetime.fromisoformat(row["run_at"])
            hours = (now - last).total_seconds() / 3600
            age = f"{hours:.0f}h ago"
            if hours > STALE_AFTER_HOURS:
                problems.append(f"{row['collector']} last ran {hours:.0f}h ago — stale")
        except (TypeError, ValueError):
            pass

        flag = "OK      " if row["status"] == "ok" else row["status"].upper().ljust(8)
        print(f"    {flag} {row['collector']:<16} found={row['items_found']} "
              f"stored={row['items_stored']}  {age}")
        if row["detail"]:
            print(f"             {row['detail'][:70]}")

        if row["status"] != "ok":
            problems.append(f"{row['collector']} is {row['status']} — {row['detail'] or 'no detail'}")

    return problems


def _report_coverage() -> None:
    """The honest claim, straight from the registry. A market is covered when
    it has a working connector, a health check and a passing test — never
    because it appears in a list."""
    print("\n[3] COVERAGE  (claim allowed, per tier)")
    by_tier: dict[str, list[str]] = {}
    for entry in registry.coverage_manifest():
        by_tier.setdefault(entry["status"], []).append(entry["iso2"])

    for tier in (registry.RECONCILED, registry.STRUCTURED_OFFICIAL, registry.DISCOVERY_ONLY):
        codes = by_tier.get(tier, [])
        if codes:
            print(f'    {tier:<22} {len(codes):>2}  "{registry.TIER_PUBLIC_CLAIM[tier]}"'
                  f'  [{", ".join(sorted(codes))}]')

    candidates = sum(len(m.candidate_official_sources) for m in registry.MARKETS)
    print(f"    {candidates} researched official source(s) not yet built (roadmap, not coverage)")


def _report_surfaces() -> None:
    print("\n[4] SURFACES")
    print(f"    dashboard  {LIVE_URL}")
    print(f"    sibling    {SIBLING_URL}  (layoffs are READ from its API, never collected here)")
    print(f"    repo       https://github.com/dk-forge/talent-intelligence-tracker")


if __name__ == "__main__":
    sys.exit(main())

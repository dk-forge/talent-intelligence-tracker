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

    problems += _report_collection_armed()
    problems += _report_data(conn)
    problems += _report_health(conn)
    problems += _report_writer_queue()
    _report_coverage()
    _report_discovery()
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


def _report_writer_queue() -> list[str]:
    """What is waiting for the one writer slot, and what fell out of it.

    Every workflow that writes the database shares the `talent-collect` lock,
    and GitHub keeps only ONE pending run per lock. Dispatching past a waiting
    run evicts it: it ends `cancelled` having created no jobs, with no error and
    no annotation anywhere. Seven writer runs were lost that way on 2026-07-29
    while a GDELT backfill held the lock, and every one of them was reported as
    "queued".

    So work is queued HERE, in a committed file, and drain-writers.yml
    dispatches one ticket at a time into an empty group — which is the one
    condition under which nothing can be evicted. This section is where that
    queue becomes visible to a session, because an invisible queue would be the
    same bug wearing a different hat.
    """
    import writer_queue

    print("\n[2b] WRITER QUEUE  (the single database-writer slot)")

    queue_file = ROOT / "data" / "writer_queue.json"
    if not queue_file.exists():
        print("    Nothing queued, nothing lost.")
        print("    Queue a writer:  gh workflow run drain-writers.yml \\")
        print("                       -f enqueue=<workflow>.yml -f inputs_json='{...}'")
        return []

    state = writer_queue.summary(writer_queue.load(queue_file))
    counts = state["counts"]
    if counts:
        print("    " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    for ticket in state["waiting"]:
        print(f"    {ticket['state']:<11} {ticket['workflow']:<26} "
              f"since {ticket['requested_at']}  attempts={ticket['attempts']}")
        if ticket.get("inputs"):
            print(f"                inputs {ticket['inputs']}")

    for orphan in state["orphans"]:
        print(f"    ORPHAN      {orphan['workflow']} run {orphan['run_id']} "
              f"(created {orphan.get('created_at')})")

    if state["last_tick"]:
        print(f"    last drain tick: {state['last_tick']}")
        last = None
        try:
            last = datetime.fromisoformat(state["last_tick"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass
        if last and state["waiting"]:
            idle = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if idle > 2:
                return state["problems"] + [
                    f"work is queued but drain-writers.yml has not ticked in "
                    f"{idle:.0f}h — the drainer itself is down"]

    return state["problems"]


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


def _report_discovery() -> None:
    """The tripwire: what the outside view says we are missing.

    Read straight off the committed work list, because a session that does not
    know a work list exists will never chase it. Says DORMANT loudly while
    nothing schedules it, for the same reason section [0] does: a session that
    assumes this runs would misread a stale work list as current.
    """
    workflow = ROOT / ".github" / "workflows" / "tripwire.yml"
    armed = workflow.exists() and any(
        line.strip().startswith("- cron:")
        for line in workflow.read_text().splitlines())

    print("\n[3b] DISCOVERY TRIPWIRE  "
          + ("ARMED — runs on schedule" if armed else "DORMANT — dispatch only"))

    worklist = ROOT / "data" / "tripwire_worklist.json"
    if not worklist.exists():
        print("    No work list yet. Prove the plumbing with:")
        print("      python run_tripwire.py --offline")
        return

    import json

    try:
        data = json.loads(worklist.read_text())
    except ValueError:
        print("    Work list is unreadable. Re-run run_tripwire.py.")
        return

    counts, cost = data.get("counts") or {}, data.get("cost") or {}
    print(f"    last run {data.get('ran_on')}: {counts.get('leads', 0)} leads, "
          f"{data.get('missing_total', 0)} missing, "
          f"${float(cost.get('run_usd') or 0):.4f} spent")
    misses = data.get("country_misses") or {}
    if misses:
        top = ", ".join(f"{k}={v}" for k, v in list(misses.items())[:8])
        print(f"    misses by country: {top}")
    if data.get("missing_total"):
        print("    Chase them (a lead is never a record):")
        print("      python run_collect.py --source tripwire_chase --dry-run")


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

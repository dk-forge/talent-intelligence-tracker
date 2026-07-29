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
    problems += _report_link_rot(conn)
    problems += _report_guardrails(conn)
    problems += _report_backfills()
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


def _commits_behind_origin() -> int:
    """How many commits this checkout is behind origin/main, 0 if unknown.

    Deliberately does NOT fetch: ops_status is read-only and must work offline
    and inside an egress-blocked session. It compares against whatever
    origin/main this checkout last saw, which is enough to catch the common
    case of a session reading a queue file written after its own checkout.
    Any failure (no git, no remote ref, not a repo) returns 0 and stays quiet
    rather than inventing a warning.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except Exception:
        return 0


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

    behind = _commits_behind_origin()

    queue_file = ROOT / "data" / "writer_queue.json"
    if not queue_file.exists():
        # An absent file means "nothing lost" ONLY if this checkout is current.
        # The drainer commits the queue to main, so a checkout even one commit
        # behind reads a file that predates every eviction and prints a
        # confident all-clear. That happened on 2026-07-29: this section said
        # "Nothing queued, nothing lost" while main recorded 15 orphans and a
        # waiting ticket. CLAUDE.md tells every session to run ops_status
        # FIRST, so a false all-clear here is the most expensive lie the tool
        # can tell — it is the eviction bug wearing the reporting tool as a hat.
        if behind:
            print(f"    UNKNOWN — this checkout is {behind} commit(s) behind origin/main,")
            print("    and the queue lives in a committed file, so what you see here is")
            print("    older than what actually happened. Run: git pull --ff-only")
            return ["writer queue state is unknown: checkout is behind origin/main"]
        print("    Nothing queued, nothing lost.")
        print("    Queue a writer:  gh workflow run drain-writers.yml \\")
        print("                       -f enqueue=<workflow>.yml -f inputs_json='{...}'")
        return []

    if behind:
        print(f"    (stale: {behind} commit(s) behind origin/main — `git pull --ff-only`)")

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


def _report_backfills() -> list[str]:
    """How far each long backfill has actually got.

    A backfill is a chain of short runs now (see backfill_slices.py), which
    means "is it still going?" stopped being answerable by looking for a
    running job — between slices there is nothing running at all. The committed
    cursor is the only place the answer lives, so this is where a session
    reads it.
    """
    import backfill_slices

    state_file = ROOT / "data" / "backfill_state.json"
    if not state_file.exists():
        return []

    print("\n[2e] BACKFILLS  (bounded slices; each run requeues the next)")
    behind = _commits_behind_origin()
    if behind:
        print(f"    (stale: {behind} commit(s) behind origin/main — `git pull --ff-only`)")

    state = backfill_slices.summary(backfill_slices.load(state_file))
    if not state["jobs"]:
        print("    Nothing in flight.")
        return []

    for job in state["jobs"]:
        where = job["cursor"] or "finished"
        print(f"    {job['state'].upper():<8} {job['id']}")
        print(f"             at {where} of {job['end']}, {job['slices']} slice(s) done"
              + (f", updated {job['updated_at']}" if job.get("updated_at") else ""))
        if job["totals"]:
            print("             " + ", ".join(
                f"{k}={v}" for k, v in sorted(job["totals"].items())))
    return state["problems"]


def _report_link_rot(conn) -> list[str]:
    """Are the documents we cite still there, and still themselves?

    The promise is that every update links to the filing or report behind it. A
    source link that dies converts a sourced claim into an unsourced one and
    nothing on the page changes, so the only way anyone finds out is by looking.
    This is where a session looks.

    A DRIFTED link is escalated on its own, separately from the rot rate. It is
    not decay: it is a URL we cite now resolving to somebody else's domain, and
    it answers 200 while doing it.
    """
    from pipeline import source_links

    print("\n[2c] SOURCE LINKS  (every figure has to still link to its document)")

    try:
        summary = source_links.rot_summary(conn)
    except sqlite3.OperationalError:
        print("    No link ledger yet. Prove the checker with:")
        print("      python3 link_check.py --dry-run --limit 40")
        return []

    total = summary["distinct_source_urls"]
    if not summary["checked"]:
        print(f"    {total} distinct source URLs, NONE checked yet.")
        print("    The checker ships DORMANT. Measure a sample with:")
        print("      python3 link_check.py --random --limit 200")
        print("      python3 archive_sources.py --dry-run --limit 200")
        return []

    print(f"    checked   {summary['checked']}/{total} distinct source URLs, "
          f"{summary['rot']} rotted ({summary['rot_pct']}%)")
    print("              " + ", ".join(f"{s}={n}" for s, n in
                                       sorted(summary["states"].items())))
    print(f"    archived  {summary['archived']}/{total} "
          f"({summary['archive_pct']}%) have a Wayback fallback, "
          f"{summary['archive_pending']} pending, "
          f"{summary['archive_unavailable']} unavailable")

    problems = []
    drifted = summary["states"].get("drifted", 0)
    if drifted:
        rows = conn.execute(
            "SELECT source_url, final_domain FROM source_links "
            " WHERE state = 'drifted' ORDER BY checked_at DESC LIMIT 5").fetchall()
        for row in rows:
            print(f"    DRIFTED   {row['source_url'][:60]} -> {row['final_domain']}")
        problems.append(
            f"{drifted} cited URL(s) now resolve to a different domain. A 200 "
            f"from a domain that changed hands is worse than a 404: check each "
            f"one and retract or re-source it by hand.")

    worst = source_links.rot_by_publisher(conn)
    if worst:
        print("    worst publishers (a rising rate here means a changed URL scheme):")
        for row in worst[:5]:
            print(f"      {row['rot_pct']:>5}%  {row['rot']}/{row['checked']}  {row['host']}")
    return problems


def _report_guardrails(conn) -> list[str]:
    """What the pre-publish guardrails caught, and what is blocking a publish.

    The $86bn Form D overstatement stood in public for weeks because nothing in
    the pipeline asked whether a single row was implausible, whether the period
    totals reconciled, or whether the printed date span matched the data. The
    checks now run on the write path (pipeline/guardrails.py). This is where a
    session SEES them, which is the half that makes flag-not-drop work: a
    finding nobody looks at is a silent drop with extra steps.
    """
    from pipeline import guardrails

    print("\n[2d] PUBLISH GUARDRAILS  (no figure goes out over an unanswered flag)")

    try:
        stats = guardrails.derive_amount_threshold(guardrails.stored_amounts(conn))
    except sqlite3.OperationalError:
        print("    No signals table yet.")
        return []

    print(f"    single-row amount ceiling  ${stats['threshold']:,}"
          + ("" if stats["derived"] else "   (FALLBACK, not derived)"))
    print(f"      {stats['reason']}")

    try:
        counts = dict(conn.execute(
            "SELECT state, COUNT(*) FROM publish_guardrails GROUP BY state").fetchall())
    except sqlite3.OperationalError:
        counts = {}

    if counts:
        print("    ledger: " + ", ".join(f"{s}={n}" for s, n in sorted(counts.items())))
        rows = guardrails.open_findings(conn)
        pending = ("open guardrail finding(s) are blocking every publish")
    else:
        # No run has written the ledger yet. Evaluating here rather than saying
        # "nothing flagged" is the difference between an ops tool and a
        # reassuring one: an empty ledger means nobody has looked, and the tool
        # that tells every session to trust it must not confuse the two. Still
        # read-only, which is this file's whole contract.
        findings = guardrails.evaluate(conn)["findings"]
        rows = [{"check_name": f.check, "subject": f.subject, "label": f.label,
                 "detail": f.detail, "value": f.value} for f in findings]
        print(f"    ledger empty (no run has recorded yet). Evaluated live: "
              f"{len(rows)} finding(s).")
        if not guardrails.collector_patterns_available():
            print("    NOTE: this interpreter cannot import the Form D "
                  "collector's own name")
            print("    patterns, so the vehicle check here is NARROWER than the "
                  "pipeline's.")
            print("    For the full set:  .venv/bin/python guardrails.py --check")
        pending = ("finding(s) will block the next publish the moment a run "
                   "records them")

    if not rows:
        return []

    for row in rows[:6]:
        value = row.get("value")
        amount = f"${float(value):,.0f}" if value else ""
        print(f"    OPEN  {row['check_name']:<14} {(row.get('label') or '')[:46]:<46} {amount}")
    if len(rows) > 6:
        print(f"          ... and {len(rows) - 6} more")

    return [f"{len(rows)} {pending}. Read them with `python3 guardrails.py`, "
            f"then accept the real ones and retract the rest. Nothing is dropped "
            f"automatically, on purpose."]


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

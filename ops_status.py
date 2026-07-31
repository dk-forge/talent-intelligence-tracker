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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import source_registry as registry
# Per-collector, derived from each one's actual schedule, and shared with
# health_digest.py so the two tools cannot disagree about what "stale" means.
# The global 36h this replaced called a five-day-old monthly source stale
# while the digest called the same row healthy. staleness.py is stdlib-only,
# which is the property this file's "no deps, no keys" promise rests on.
from staleness import max_age_hours

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
    problems += _report_employer_keys(conn)
    problems += _report_health(conn)
    problems += _report_run_cost(conn)
    problems += _report_writer_queue()
    problems += _report_host()
    problems += _report_link_rot(conn)
    problems += _report_guardrails(conn)
    problems += _report_backfills()
    _report_coverage()
    _report_discovery()
    _report_rejection_audit()
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


def _report_employer_keys(conn) -> list[str]:
    """Is every stored employer key still the key we would compute today?

    `company_key` is a normalised name, computed at ingestion and then stored,
    so every change to `vocab.company_key` leaves the rows behind it spelled the
    old way. That is invisible from the outside and it breaks two things
    quietly: the key is the first input to `content_hash`, so a stale row cannot
    dedupe against its own history and the next signal about that employer lands
    as a second record; and the profile URL is derived from the key, so the
    employer's page moves.

    The second check is the other half. Two keys that differ only in punctuation
    claim ONE profile URL, because the slug transliterates accents, turns "&"
    into "and" and collapses every run of punctuation. includes/company.php
    refuses to serve or publish either side of that, which is the right call and
    is also completely silent — a page that simply never appears. So an
    unmerged pair is named here instead, and the merge goes in
    `vocab.EMPLOYER_KEY_ALIASES`.
    """
    from pipeline import vocab

    print("\n[1c] EMPLOYER KEYS  (the only employer identity there is)")
    problems = []

    rows = conn.execute(
        "SELECT company, company_key FROM signals WHERE is_current = 1 "
        "  AND company_key IS NOT NULL AND company_key <> ''"
    ).fetchall()

    stale: dict[str, tuple[str, int]] = {}
    keys = set()
    for row in rows:
        keys.add(row["company_key"])
        fresh = vocab.company_key(row["company"])
        if fresh != row["company_key"]:
            old, count = stale.get(row["company_key"], (fresh, 0))
            stale[row["company_key"]] = (old, count + 1)

    if stale:
        n = sum(count for _, count in stale.values())
        print(f"    {n} row(s) across {len(stale)} employer(s) carry a key this "
              f"name no longer normalises to")
        for old, (new, count) in sorted(stale.items())[:6]:
            print(f"      {count:>3}  {old!r} -> {new!r}")
        if len(stale) > 6:
            print(f"      ... and {len(stale) - 6} more")
        problems.append(
            f"{n} row(s) carry a stale company_key, so they cannot dedupe "
            f"against their own history: queue correct-company-key.yml "
            f"(dry run first)")
    else:
        print(f"    {len(keys)} keys, all current with pipeline/vocab.py")

    # A collision is two keys claiming one URL. Computed the way the slug is,
    # which is a deliberate duplicate of six lines of PHP: the alternative is
    # not checking at all from here, and the pair it finds is checked by hand
    # before anything acts on it.
    claims: dict[str, list[str]] = {}
    for key in keys:
        claims.setdefault(_profile_slug(key), []).append(key)
    collisions = {slug: sorted(owners) for slug, owners in claims.items()
                  if slug and len(owners) > 1}

    # A pair the alias map already merges is not waiting on a decision, it is
    # waiting on the correction that moves its rows. Saying so is the difference
    # between "somebody must choose which spelling wins" and "the job is
    # queued", and only the first is work.
    undecided = {slug: owners for slug, owners in collisions.items()
                 if not any(vocab.EMPLOYER_KEY_ALIASES.get(o) in owners for o in owners)}

    if collisions:
        print(f"    {len(collisions)} slug(s) claimed by two keys, so neither "
              f"employer is published:")
        for slug, owners in sorted(collisions.items()):
            merged = "" if slug in undecided else "   (merged; the rows have not moved yet)"
            print(f"      /company/{slug}/{merged}")
            for owner in owners:
                print(f"          {owner!r}")
    if undecided:
        problems.append(
            f"{len(undecided)} employer(s) recorded under two keys that claim "
            f"one URL and are not merged: decide which spelling wins, add it to "
            f"vocab.EMPLOYER_KEY_ALIASES, then queue correct-company-key.yml")

    return problems


def _profile_slug(key: str) -> str:
    """tit_company_slug() from includes/company.php: accents folded, "&" to
    "and", every other run of non-alphanumerics to one hyphen."""
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", key.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded.replace("&", " and ")).strip("-")


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
            limit = max_age_hours(row["collector"])
            if hours > limit:
                problems.append(f"{row['collector']} last ran {hours:.0f}h ago "
                                f"— stale (its schedule expects a run within "
                                f"{limit}h)")
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


#: How much of the health ledger the cost window looks at. Collection runs
#: twice a day, so seven days is fourteen scheduled runs plus whatever was
#: dispatched — enough that one expensive backfill does not decide the number,
#: short enough that a change made this week is visible this week.
COST_WINDOW_DAYS = 7


def _monthly_allowance() -> float | None:
    """The monthly budget, read out of spend.py without importing it.

    spend.py owns that number — it is a policy, deliberately in a diffable file
    rather than a secret — but it imports `requests` at module scope, and this
    file's whole promise is stdlib only, no deps, no network, no keys. So the
    assignment is parsed instead of executed. Copying the figure here was the
    alternative, and a duplicated budget is a budget that goes stale silently.

    None when it cannot be read, which prints as "no policy figure" rather than
    quietly comparing against a default nobody set.
    """
    import ast

    try:
        tree = ast.parse((ROOT / "spend.py").read_text())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "MONTHLY_ALLOWANCE_USD":
                try:
                    return float(ast.literal_eval(node.value))
                except (ValueError, TypeError):
                    return None
    return None


def _report_run_cost(conn) -> list[str]:
    """What the model charged, per run, from the health ledger.

    classify.STATS has counted tokens and the provider's own cost figure since
    the gate was added, printed them at the end of every run, and then lost them
    when the process exited. The consequence was specific: spend drift could
    only be seen in a month-end total, and cost per stored row — the number that
    says whether a prompt change, a cap change or a model switch paid for itself
    — could not be plotted at all, because nothing had ever written it down.

    Cost is recorded on the health row the run already files, so this reads the
    same rows section [2] does. Rows that predate the columns are NULL and are
    skipped rather than counted as free.
    """
    from pipeline import store

    print("\n[2a] RUN COST  (what the model charged, per run)")

    if not store.health_has_cost_columns(conn):
        print("    This database predates per-run cost accounting. The next")
        print("    collect run adds the columns and starts recording.")
        return []

    rows = conn.execute(
        f"""SELECT collector, run_at, status, items_stored, model, gate_model,
                   prompt_tokens, cached_tokens, completion_tokens, cost_usd,
                   reads_bought, rows_from_reads
              FROM source_health
             WHERE cost_usd IS NOT NULL
             ORDER BY run_at DESC LIMIT {2 * COST_WINDOW_DAYS + 12}"""
    ).fetchall()

    if not rows:
        print("    No run has recorded a cost yet. Every run that calls a model")
        print("    records one from now on; a structured source records none")
        print("    because it spends nothing.")
        return []

    for row in rows[:5]:
        ratio = store.reads_to_rows_pct(row["reads_bought"], row["rows_from_reads"])
        rows_bought = row["rows_from_reads"] or 0
        share = "" if ratio is None else (
            f"  {row['reads_bought']} reads -> {rows_bought} "
            f"row{'' if rows_bought == 1 else 's'} ({ratio}%)")
        print(f"    {row['run_at'][:16]}  {row['collector']:<16} "
              f"${row['cost_usd']:.4f}{share}")
        prompt = row["prompt_tokens"] or 0
        if prompt:
            cached = row["cached_tokens"] or 0
            print(f"                      {prompt:,} prompt tokens "
                  f"({cached * 100 // prompt}% served from cache), "
                  f"{row['completion_tokens'] or 0:,} completion")

    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=COST_WINDOW_DAYS)).isoformat(timespec="seconds")
    window = [r for r in rows if (r["run_at"] or "") >= cutoff]

    problems: list[str] = []
    if not window:
        print(f"    Nothing in the last {COST_WINDOW_DAYS} days. The newest cost "
              f"row is {rows[0]['run_at'][:16]}.")
        return problems

    spend = sum(float(r["cost_usd"] or 0) for r in window)
    reads = sum(int(r["reads_bought"] or 0) for r in window)
    from_reads = sum(int(r["rows_from_reads"] or 0) for r in window)
    stored = sum(int(r["items_stored"] or 0) for r in window)

    print(f"    last {COST_WINDOW_DAYS}d: ${spend:.4f} over {len(window)} run(s), "
          f"{reads} reads -> {from_reads} rows"
          + ("" if store.reads_to_rows_pct(reads, from_reads) is None
             else f" ({store.reads_to_rows_pct(reads, from_reads)}%)"))
    if stored:
        print(f"             ${spend / stored:.5f} per stored row")

    models = {(r["model"], r["gate_model"]) for r in window if r["model"]}
    for model, gate_model in sorted(models):
        print(f"             {model} read-through, "
              + (f"{gate_model} gate" if gate_model else "no gate (single-stage)"))
    if len(models) > 1:
        print("             (two model configurations in one window, so the "
              "cost per row above mixes them)")

    # The whole point of persisting this: drift is visible in a day rather than
    # at a month end. Projected, not extrapolated from one run — a window of
    # fewer than three runs is too easy for a single dispatched backfill to
    # dominate, and a false alarm here costs the tool its authority.
    allowance = _monthly_allowance()
    projected = spend / COST_WINDOW_DAYS * 30
    if allowance is None:
        print("             (spend.py's monthly allowance could not be read, "
              "so nothing is compared against it)")
    else:
        print(f"             projects to ${projected:.2f}/30d against a "
              f"${allowance:.2f} allowance (spend.py)")
        if len(window) >= 3 and projected > allowance:
            problems.append(
                f"the last {COST_WINDOW_DAYS} days of model spend project to "
                f"${projected:.2f} over 30 days, past the ${allowance:.2f} "
                f"allowance in spend.py: check the read-through cap and the "
                f"reads-to-rows ratio above before the key's own cap stops "
                f"collection with a 402")
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
        if ticket.get("unbound_count"):
            print(f"                dispatched {ticket['unbound_count']}x with NO "
                  f"RUN produced — the dispatch is failing, not the work")

    for orphan in state["orphans"]:
        print(f"    ORPHAN      {orphan['workflow']} run {orphan['run_id']} "
              f"(created {orphan.get('created_at')})")

    # A LIVE tick is not a MOVING queue, and the difference is the whole of the
    # 2026-07-30 stall: the drainer ran eleven times in seven hours, every run
    # green, and the queue did not move once. So both facts are printed, and
    # each has its own alarm — the heartbeat catches a drainer that has stopped,
    # `idle_since` catches a drainer that is running and achieving nothing.
    if state.get("last_dispatch"):
        print(f"    last dispatch:   {state['last_dispatch']}")
    if state.get("idle_since"):
        print(f"    STALLED SINCE:   {state['idle_since']}  (work waiting, lock "
              f"group empty, nothing sent)")

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


def _report_host() -> list[str]:
    """Is the host that serves all of this answering, and did anything we tried
    to say about it get through?

    Read from a committed ledger rather than probed here, and that is the same
    property this whole file rests on: no network, no keys, no hang. The probe
    lives in host-watch.yml, which runs every 15 minutes and writes
    data/host_status.json only when the answer changes or a heartbeat is due.

    This section exists because on 2026-07-31 nothing did. Two Bluehost 504
    windows in one day, both found by the owner in a browser, while every ops
    tool in both repos went on reporting confidently about a site none of them
    had checked was up.
    """
    import json as _json

    print("\n[2f] HOST  (the site that serves every number above)")

    armed = _crons("host-watch.yml")
    ledger_path = ROOT / "data" / "host_status.json"
    outbox_path = ROOT / "data" / "alert_outbox.json"
    problems: list[str] = []

    if not armed:
        print("    watchdog  DORMANT — nothing probes the host on a timer, so an")
        print("              outage is found by a person in a browser, which is")
        print("              exactly how both 2026-07-31 outages were found.")
        return ["host-watch.yml has no cron: nothing is watching whether the "
                "site is reachable"]

    try:
        ledger = _json.loads(ledger_path.read_text())
    except (OSError, ValueError):
        print(f"    watchdog  ARMED ({', '.join(armed)}) but has never recorded")
        print("              an answer. Prove it: gh workflow run host-watch.yml")
        return []

    state = ledger.get("state", "unknown")
    last_probe = _parse_iso(ledger.get("last_probe_at"))
    age_h = (_utcnow() - last_probe).total_seconds() / 3600 if last_probe else None

    if state == "up":
        print(f"    reachable UP since {ledger.get('since', '?')} "
              f"— {ledger.get('last_detail', '')}")
    else:
        fails = ledger.get("consecutive_failures", 0)
        print(f"    reachable {state.upper()} since {ledger.get('since', '?')} "
              f"— {fails} consecutive failed probe(s)")
        print(f"              {ledger.get('last_detail', '')}")
        problems.append(
            f"the WordPress host has been unreachable since "
            f"{ledger.get('since')} ({fails} failed probes). Nothing in this "
            f"repo can fix it; alerts raised meanwhile are held, not lost.")

    if age_h is not None and age_h > 24:
        print(f"    watchdog  STALE — last probe recorded {age_h:.0f}h ago")
        problems.append(
            "host-watch has not recorded a probe in over 24h, so 'the host is "
            "up' is a memory rather than a measurement. Check the workflow.")

    # A host that wobbles four times a week is worth seeing even when no single
    # wobble was long enough to email about. This is that record.
    recent = [h for h in ledger.get("history", [])
              if h.get("state") == "down"
              and (_parse_iso(h.get("at")) or _utcnow()) > _utcnow() - timedelta(days=14)]
    if recent:
        print(f"    wobbles   {len(recent)} outage(s) recorded in the last 14 days")
        for h in recent[-3:]:
            print(f"              {h.get('at')}  {h.get('detail', '')[:56]}")

    try:
        outbox = _json.loads(outbox_path.read_text())
    except (OSError, ValueError):
        outbox = {"entries": []}
    held = [e for e in outbox.get("entries", []) if e.get("state") == "pending"]
    if held:
        worst = max(e.get("attempts", 0) for e in held)
        print(f"    alerts    {len(held)} HELD — raised but not yet delivered "
              f"(most-tried: x{worst})")
        for e in held[:3]:
            print(f"              {e.get('raised_at')}  "
                  f"{(e.get('payload') or {}).get('subject', e.get('key', ''))[:60]}")
        if worst >= 12:  # alert_outbox.FAIL_LOUD_ATTEMPTS
            problems.append(
                f"{len(held)} alert(s) have failed delivery 12+ times. With the "
                f"host up that is a settled refusal, not an outage: check "
                f"WP_API_KEY and that the plugin carrying /alert is deployed.")
    else:
        print("    alerts    nothing held — every alert raised has reached the owner")

    return problems


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


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

    # The one decision this section exists to prompt. 51 of the 81 gold-set
    # misses are `outside_our_history` — the news collectors first ran on
    # 2026-07-27 and national_press on 2026-07-29, against a gold window of
    # 2026-07-01..28 — so the recall number is substantially a measurement of a
    # tracker younger than the window judging it. The walker that fixes it is
    # built and has never run. It stays dispatch-only because its cost scales
    # with slices, which makes the cron the budget and the pace the owner's call.
    walker = "backfill-gdelt-2026:2026-01-01..2026-07-26"
    walked = any(job["id"].startswith("backfill-gdelt-2026")
                 for job in state["jobs"])
    if not walked and not _crons("backfill-gdelt-2026.yml"):
        print("    HISTORY  the 2026 news walker has never run, and 51 of the 81 "
              "recall misses")
        print("             are simply from before we existed. It is "
              "dispatch-only on purpose:")
        print("             python3 backfill_gdelt_2026.py --plan-cost   "
              "# what each pace costs")
        print(f"             then queue slice one (the cursor, not the input, "
              f"decides where it resumes):")
        print(f"             gh workflow run drain-writers.yml "
              f"-f enqueue=backfill-gdelt-2026.yml \\")
        print(f"                  -f inputs_json='{{\"start\":\"2026-01-01\","
              f"\"end\":\"2026-07-26\",\"slice\":\"true\"}}' \\")
        print(f"                  -f reason='{walker}, slice 1'")

    if not state["jobs"]:
        print("    Nothing else in flight.")
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


def _crons(workflow: str) -> list[str]:
    """The UNCOMMENTED cron expressions in one workflow file.

    A commented `#   - cron:` line starts with `#` once stripped, so prose about
    a schedule can never be mistaken for one. Same test `_report_collection_armed`
    uses, and the reason both read the file instead of a constant: CLAUDE.md
    makes this script the authority on arming state, and a script that reports
    its own comments is not an authority.
    """
    path = ROOT / ".github" / "workflows" / workflow
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- cron:"):
            # Trailing comment off FIRST, then the quotes: the other order
            # leaves the closing quote glued to the expression.
            value = stripped.split(":", 1)[1].split("#")[0].strip()
            out.append(value.strip("'\""))
    return out


#: The two link-hygiene writers, and the workflow that is allowed to schedule
#: them. Both write the database, so both sit in the single `talent-collect`
#: lock, so NEITHER may carry a cron of its own: a scheduled run enters that
#: group as an uncoordinated third body and either evicts the pending run or is
#: evicted itself, ending cancelled with zero jobs and unreplayable inputs.
#: The schedule lives one level out, in a workflow that writes a ticket.
LINK_JOBS = {"archive-sources.yml": "three-hourly Wayback pass",
             "link-check.yml": "daily rot sweep"}
LINK_SCHEDULER = "schedule-link-hygiene.yml"


def _report_link_schedule() -> list[str]:
    """Whether the link-hygiene jobs actually run, and by which route."""
    problems = []
    scheduler = _crons(LINK_SCHEDULER)
    self_scheduled = {name: _crons(name) for name in LINK_JOBS}
    offenders = {n: c for n, c in self_scheduled.items() if c}

    if offenders:
        print("    schedule  MISWIRED — see below")
        for name, crons in offenders.items():
            problems.append(
                f"{name} carries its own cron ({', '.join(crons)}). It is a "
                f"database writer, so a scheduled run enters the talent-collect "
                f"lock uncoordinated: it either evicts the pending run or is "
                f"evicted itself, and an evicted run cannot be replayed because "
                f"GitHub does not expose a dispatched run's inputs. Move the "
                f"slot to {LINK_SCHEDULER}, which writes a ticket instead.")
    elif scheduler:
        print(f"    schedule  ARMED via the writer queue — {LINK_SCHEDULER}")
        print(f"              writes a ticket ({'; '.join(sorted(scheduler))}) and")
        print("              drain-writers dispatches it into an empty lock, so a")
        print("              slot that fires during a backfill waits instead of")
        print("              being evicted. Neither job is on its own cron.")
    else:
        print("    schedule  DORMANT — dispatch only. Nothing runs these on a")
        print("              timer, so silence here is expected, not an incident.")
        print(f"              Arm by restoring the crons in {LINK_SCHEDULER};")
        print("              never by adding one to the two writers themselves.")
    return problems


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

    problems = _report_link_schedule()

    try:
        summary = source_links.rot_summary(conn)
    except sqlite3.OperationalError:
        print("    No link ledger yet. Prove the checker with:")
        print("      python3 link_check.py --dry-run --limit 40")
        return problems

    # Read BEFORE the "nothing checked yet" branch below, because the archiving
    # half of this ledger is not downstream of the rot checker: a URL wrongly
    # retired from the capture queue is just as retired on a database where
    # link_check has never run, and gating the escalation on an unrelated job
    # having run is exactly the coupling that hides things here.
    try:
        split = source_links.archive_gap(conn)
    except sqlite3.OperationalError:
        split = None
    if split and split["terminal_blind"]:
        # This must always be zero. classify_archive_outcome refuses to record
        # terminal without a definitive negative, so a non-zero here is either
        # pre-fix history or a new route into the same bug.
        problems.append(
            f"{split['terminal_blind']} URL(s) sit at the TERMINAL "
            f"'unavailable' state without archive.org ever having said it holds "
            f"no snapshot of them. They have dropped out of the capture queue "
            f"for good on the strength of a throttle. Put them back: "
            f"python3 archive_sources.py --recheck-terminal --dry-run  (then "
            f"without --dry-run, queued as a writer).")

    total = summary["distinct_source_urls"]
    if not summary["checked"]:
        print(f"    {total} distinct source URLs, NONE checked yet.")
        print("    Measure a sample before trusting any rate here:")
        print("      python3 link_check.py --random --limit 200")
        print("      python3 archive_sources.py --dry-run --limit 200")
        return problems

    print(f"    checked   {summary['checked']}/{total} distinct source URLs, "
          f"{summary['rot']} rotted ({summary['rot_pct']}%)")
    print("              " + ", ".join(f"{s}={n}" for s, n in
                                       sorted(summary["states"].items())))
    print(f"    archived  {summary['archived']}/{total} "
          f"({summary['archive_pct']}%) have a Wayback fallback, "
          f"{summary['archive_pending']} pending, "
          f"{summary['archive_unavailable']} unavailable")

    # The percentage cannot answer the question that matters about it. A slow
    # climb is the design — Save Page Now is rate-limited and a backfill takes
    # about a week — and a climb stalled because archive.org will not answer
    # looks exactly the same from outside. So the un-archived population is
    # printed split, and the split is the difference between a queue and a wall.
    if split:
        print(f"    the gap   {split['never_probed']:,} never answered about, "
              f"{split['probed_absent']:,} confirmed absent from Wayback "
              f"(the real capture queue)")
        if split["blind_recently"]:
            print(f"              {split['blind_recently']:,} carry a blind "
                  f"round: archive.org would not answer, and no state, attempt "
                  f"or verdict was recorded on the strength of that.")

    # What the capture cap costs, said where the percentage is printed. The
    # percentage above is over the WHOLE corpus, so it has a ceiling near 4%:
    # ~96% of that corpus is SEC and GOV.UK filings the schedule deliberately
    # skips. This is the same measure over the population the schedule can
    # actually reach, which is the one that moves when the job runs. The weekly
    # digest calls the identical function, so the dashboard and the email can
    # never quote two different coverage figures for the same day.
    cover = source_links.archive_coverage(conn)
    if cover["in_scope"] and total:
        share = round(100.0 * cover["in_scope"] / total, 1)
        print(f"    in scope  {cover['archived']:,}/{cover['in_scope']:,} "
              f"({cover['pct']}%) archived across the "
              f"{len(cover['collectors'])} collector(s) the schedule covers")
        print(f"              {cover['capture_queue']:,} waiting on a capture, "
              f"{cover['never_probed']:,} never answered about")
        print(f"              newest snapshot: {cover['newest_snapshot'] or 'never'}")
        print(f"              That scope is {cover['in_scope']:,} of {total:,} "
              f"URLs ({share}%). The rest are SEC and")
        print("              GOV.UK filings whose publishers keep them "
              "indefinitely, so the")
        print("              corpus percentage above has its ceiling there "
              "rather than a stall.")
        print("              Widen it by editing the collector default in")
        print("              .github/workflows/archive-sources.yml.")

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
    """What the pre-publish guardrails are holding back, and how urgent it is.

    The $86bn Form D overstatement stood in public for weeks because nothing in
    the pipeline asked whether a single row was implausible, whether the period
    totals reconciled, or whether the printed date span matched the data. The
    checks now run on the write path (pipeline/guardrails.py). This is where a
    session SEES them, which is the half that makes flag-not-drop work: a
    finding nobody looks at is a silent drop with extra steps.

    Reports the two categories separately because they are different problems.
    A HELD row never reached the site, so the guardrail worked and nothing is
    wrong in public. A LIVE one is a figure that is wrong on the page right now,
    which quarantine cannot fix - only a human retraction can.
    """
    from pipeline import guardrails

    print("\n[2d] PUBLISH GUARDRAILS  (a flagged row is held back; the rest publish)")

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

    # Evaluate read-only whether or not a run has recorded. An empty ledger
    # means nobody has looked, and the tool every session is told to trust must
    # not confuse that with "nothing flagged".
    report = guardrails.quarantine(conn, write=False)

    if counts:
        print("    ledger: " + ", ".join(f"{s}={n}" for s, n in sorted(counts.items())))
    else:
        print("    ledger empty (no run has recorded yet); evaluated live.")
    if not guardrails.collector_patterns_available():
        print("    NOTE: this interpreter cannot import the Form D collector's "
              "own name")
        print("    patterns, so the vehicle check here is NARROWER than the "
              "pipeline's.")
        print("    For the full set:  .venv/bin/python guardrails.py --check")

    held, live = report["held"], report["live"]
    overdue, aggregate = report["overdue"], report["aggregate"]
    if not (held or live or aggregate):
        print("    Nothing quarantined. Every row publishes.")
        return []

    print(f"    quarantined {len(held) + len(live)} row(s): {len(held)} held "
          f"back, {len(live)} already live")

    for row in sorted(held + live, key=lambda r: -(r.get("value") or 0))[:6]:
        age, grace = row.get("age_hours"), row.get("grace_hours")
        left = "" if age is None else f"  red in {max(0.0, grace - age):.0f}h"
        tag = "LIVE" if row["already_live"] else "HELD"
        print(f"    {tag}  {row['check_name']:<13} "
              f"{(row.get('label') or '')[:44]:<44}{left}")
    if len(held) + len(live) > 6:
        print(f"          ... and {len(held) + len(live) - 6} more")

    problems = []
    if aggregate:
        for row in aggregate:
            print(f"    HALT  {row['check_name']:<13} {(row.get('label') or '')[:44]}")
        problems.append(
            f"{len(aggregate)} aggregate guardrail finding(s): the published set "
            f"does not add up, so NOTHING publishes until this is answered. "
            f"There is no clean subset of a wrong total.")
    if live:
        problems.append(
            f"{len(live)} flagged row(s) are ALREADY on the live site. Quarantine "
            f"cannot pull a published row back: read the filing, then "
            f"`python3 guardrails.py --accept/--reject` and retract the bad ones.")
    if overdue:
        problems.append(
            f"{len(overdue)} finding(s) are past their grace window, so every "
            f"publish run is now exiting non-zero AFTER sending its clean rows. "
            f"Answer them with `python3 guardrails.py`.")
    if held and not problems:
        print(f"    {len(held)} held row(s) are inside their grace window; runs "
              f"stay green until then.")
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


def _report_rejection_audit() -> None:
    """WHY we miss what we miss — the roadmap, not a scoreboard.

    `analysis/recall/rejection_audit.py` takes every gold-set event we did not
    hold and asks which of four things went wrong. It has been produced since
    2026-07-29 and surfaced NOWHERE: the file sat in data/ and no session that
    did not already know its filename would ever have opened it.

    IT IS PRINTED AS A DIAGNOSIS AND NOT AS A NUMBER, because the split is the
    whole finding and the headline count is the least useful part of it:

        fetched_then_dropped = 0

    Not one gold event has ever been fetched and then rejected by a filter. The
    prefilter, the gate, the vocabularies and the guards are not what is losing
    coverage — and "our filters are too aggressive" is the intuitive diagnosis
    that this measurement refutes. Meanwhile the largest bucket by a distance is
    `outside_our_history`: events that predate the collector that would have
    caught them. That is a YOUNG CORPUS, not a leaky one, and it is fixed by
    backfilling rather than by loosening anything.

    So this section prints the four causes with what each one means you should
    DO. It is deliberately not an ACTION NEEDED item: a young corpus is not a
    fault, and a permanent red on a number that only time can move would train
    the next session to ignore the exit code.
    """
    import json

    path = ROOT / "data" / "recall_rejection_audit.json"
    print("\n[3c] WHY WE MISS WHAT WE MISS  (the feed roadmap, from the gold set)")

    if not path.exists():
        print("    No audit yet. It is produced beside the recall measurement:")
        print("      python -m analysis.recall.rejection_audit")
        return
    try:
        data = json.loads(path.read_text())
    except ValueError:
        print("    Audit file is unreadable. Re-run analysis/recall/rejection_audit.py.")
        return

    stages = data.get("stages") or {}
    misses = int(data.get("misses") or 0)
    gold = int(data.get("gold_events") or 0)
    held = gold - misses
    print(f"    measured {data.get('measured_on')} on gold set "
          f"{data.get('goldset_version')}: held {held} of {gold}, missed {misses}")

    # Order is the reading order of the finding, not the file's order and not
    # descending size: the zero comes first because it is what the reader is
    # most likely to have assumed otherwise.
    meaning = [
        ("fetched_then_dropped",
         "a filter rejected it", "LOOSEN something — this is the only bucket that means that"),
        ("outside_our_history",
         "older than the collector", "BACKFILL. Not filters, not sources"),
        ("publisher_not_wired",
         "researched, not connected", "wire the feed that is already in the catalogue"),
        ("publisher_unknown",
         "not researched", "find the publisher"),
        ("feed_read_item_missed",
         "feed depth or run cadence", "plumbing: read deeper, or read more often"),
    ]
    for key, means, todo in meaning:
        n = int(stages.get(key) or 0)
        share = f"{100.0 * n / misses:.0f}%" if misses else "n/a"
        print(f"    {n:>3} {share:>4}  {key:<22} {means}")
        if n:
            print(f"                                       -> {todo}")

    dropped = int(stages.get("fetched_then_dropped") or 0)
    if dropped == 0:
        print("    READ THE ZERO: no filter has ever rejected a gold event. The "
              "corpus is young, not leaky.")

    split = data.get("split") or {}
    if split:
        print("    by cause: " + ", ".join(f"{k}={v}" for k, v in split.items()))


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

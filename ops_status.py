#!/usr/bin/env python3
"""Read this repo's actual state. Run it first, every session.

No dependencies, no network, no keys. It answers the questions a new session
would otherwise waste half an hour re-deriving: what is live, what is stale,
what is broken, and what the honest coverage claim is right now.

Exit codes: 0 healthy | 2 something needs a human
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
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
    problems += _report_read_rations()
    problems += _report_writer_queue()
    problems += _report_host()
    problems += _report_link_rot(conn)
    problems += _report_guardrails(conn)
    problems += _report_backfills()
    _report_coverage()
    _report_discovery()
    _report_rejection_audit()
    problems += _report_recall(conn)
    problems += _report_landmarks(conn)
    problems += _report_published_figures()
    _report_surfaces()
    problems += _report_spend()

    print("\n" + "-" * 64)
    if problems:
        print(f"ACTION NEEDED: {len(problems)} item(s)")
        for p in problems:
            print(f"  -> {p}")
        return 2

    print("All clear.")
    return 0


def _report_recall(conn) -> list[str]:
    """What each measured population's latest recall figure actually says.

    Read from the committed result files, offline. Two families now, so the one
    thing this section must never do is print one number: a worldwide figure and
    a United States figure are measurements of different populations against
    different reference sets, and a session that read one as the other would
    draw the wrong conclusion in both directions.

    Every line carries the INTERVAL. The US set is 51 events wide, so its
    headline resolves to about 26 points, and a session comparing this week's
    41% with next week's 35% needs to see that those are the same number before
    it goes looking for a regression that is not there.

    PASS / FAIL / UNKNOWN are three states. A family whose results directory is
    empty, or whose newest result cannot be read, is UNKNOWN and is an action
    item: it means nothing has measured that population, which is exactly the
    state this whole loop exists to make visible. It is never a pass.
    """
    print("\n[3e] MEASURED RECALL  (what we hold, against held-out reference sets)")
    problems: list[str] = []
    try:
        from analysis.recall import family as families
        from analysis.recall import stats, thresholds
    except Exception as e:                                  # noqa: BLE001
        print(f"    UNKNOWN — could not load the measurement ({e}). NOT a pass.")
        return ["RECALL: the measurement could not be loaded (UNKNOWN, not a pass)"]

    for fam in families.ALL:
        results = thresholds.load_results(fam.results_dir)
        if not results:
            print(f"    UNKNOWN {fam.label}: no measurement has ever been recorded")
            print(f"            python3 measure_recall.py --family {fam.id}")
            problems.append(
                f"RECALL {fam.label}: never measured (UNKNOWN, not a pass)")
            continue

        latest = results[-1]
        overall = (latest.get("summary") or {}).get("overall") or {}
        span = overall.get("held_interval")
        if not span:
            # A result from before the interval was published. Recomputed here
            # from its own counts rather than left blank, using the same one
            # function, so an old file reads like a new one.
            span = stats.interval(overall.get("held") or 0, overall.get("total") or 0)

        verdict = thresholds.evaluate(latest, history=results[:-1])
        mark = {"PASS": "PASS   ", "FAIL": "FAIL   ",
                "BASELINE": "BASELINE"}.get(verdict["verdict"], "UNKNOWN")
        print(f"    {mark} {fam.label}: held {overall.get('held')}/"
              f"{overall.get('total')} ({span['pct']}%), 95% interval "
              f"{span['low_pct']} to {span['high_pct']}, "
              f"measured {latest.get('measured_on')} against "
              f"{(latest.get('goldset') or {}).get('version')}")

        for gate in verdict["gates"]:
            if gate["status"] == thresholds.FAIL:
                print(f"            {gate['gate']}: {gate['detail']}")
                problems.append(f"RECALL {fam.label}: {gate['gate']} FAILED")

        # The cell breakdown is the work list, so the worst cell is named here
        # rather than left in a JSON file somebody has to open.
        group = "by_metro" if "by_metro" in (latest.get("summary") or {}) \
            else "by_source_type"
        cells = (latest.get("summary") or {}).get(group) or {}
        ranked = sorted((c for c in cells.items() if c[1]["total"] >= 4),
                        key=lambda kv: kv[1]["held_pct"] or 0)
        if ranked:
            key, cell = ranked[0]
            print(f"            weakest {group.replace('by_', '')}: {key}, "
                  f"held {cell['held']}/{cell['total']} ({cell['held_pct']}%)")

        # A set that has aged out or converged is still being measured, and a
        # measurement against a converged set measures memory. The run itself
        # says so; this repeats it where a session actually looks.
        worklist = ROOT / "data" / (
            "recall_worklist.json" if fam.is_default
            else f"recall_{fam.id}_worklist.json")
        try:
            due = json.loads(worklist.read_text())["next_goldset"]
        except Exception:                                   # noqa: BLE001
            continue
        if due.get("due"):
            print(f"            NEW REFERENCE SET DUE: {due['reason']}")
            problems.append(
                f"RECALL {fam.label}: a fresh reference set is due, and until "
                f"one lands the figure measures memory rather than reach")

    return problems


def _report_published_figures() -> list[str]:
    """Are the numbers a reader can quote actually right?

    Source health asks "did the collector run". The guardrails ask "is the row
    plausible". Neither asks the question a journalist's editor asks, which is
    whether the number on the screen is the number the data supports. That is a
    separate question and until 2026-08-04 nothing here asked it: a region tab
    badged one figure and returned another for weeks, and every check was green
    the whole time.

    The verdicts come from published_figures.check_all(), the single definition,
    so this dashboard and anything else that reports these can never disagree.
    """
    print("\n[PUBLISHED FIGURES]  numbers a reader or a journalist can quote")
    try:
        import published_figures
    except Exception as e:                                  # noqa: BLE001
        print(f"    UNKNOWN — could not load the checks ({e}). NOT a pass.")
        return ["PUBLISHED FIGURES: checks could not be loaded (UNKNOWN, not a pass)"]

    report = published_figures.check_all()
    problems: list[str] = []
    for r in report.results:
        if r.state == published_figures.FAIL:
            print(f"    FAIL    {r.key}")
            for line in r.detail.split("; "):
                print(f"            {line}")
            problems.append(f"PUBLISHED FIGURE: {r.key}")
        elif r.state == published_figures.UNKNOWN:
            print(f"    UNKNOWN {r.key} — NOT checked, NOT passing")
            for line in r.detail.split("; "):
                print(f"            {line}")
            # An environment that cannot reach the site is not a defect in the
            # site, but it is never a clean bill of health either, so it is
            # ALWAYS printed above as UNKNOWN. What it is not is an action item,
            # in exactly two cases: a transport fault (an egress-blocked or
            # offline session, which says nothing about the data) and the
            # deploy's own 503 maintenance window, which is self-resolving and
            # which every deploy would otherwise turn into five action items.
            # This mirrors _excusable() in the sibling repo rather than inventing
            # a second opinion about the same question.
            import urllib.error
            deploying = (isinstance(r.error, urllib.error.HTTPError)
                         and r.error.code == 503)
            if not r.transport and not deploying:
                problems.append(f"PUBLISHED FIGURE UNVERIFIED: {r.key}")
        else:
            print(f"    ok      {r.key}")
    if report.verdict == published_figures.FAIL:
        print("    *** A PUBLISHED NUMBER IS WRONG ON A LIVE PUBLIC SURFACE.")
        print("    *** Journalists quote these. Fix before any staleness item.")
    return problems


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
    # A pair of DIFFERENT employers that collide is not waiting on a spelling
    # decision either, and telling someone to make one invites the merge that
    # destroys an employer. The slug deletes every non-[a-z0-9] character, so
    # any two names written in a non-Latin script collapse together — '오픈ai'
    # and '페르소나ai' are both 'ai'. Reviewed pairs live in vocab; they still
    # block publication, and the fix is the plugin's slug, not the key.
    distinct = {slug: owners for slug, owners in collisions.items()
                if tuple(owners) == tuple(sorted(
                    vocab.DISTINCT_EMPLOYER_SLUG_COLLISIONS.get(slug, ())))}
    # Same employer, but no spelling of it is SQL-findable, so the alias map
    # cannot name a survivor. Also blocked on the slug, not on a decision.
    unnameable = {slug: owners for slug, owners in collisions.items()
                  if tuple(owners) == tuple(sorted(
                      vocab.SAME_EMPLOYER_NO_ASCII_KEY.get(slug, ())))}

    undecided = {slug: owners for slug, owners in collisions.items()
                 if slug not in distinct and slug not in unnameable
                 and not any(vocab.EMPLOYER_KEY_ALIASES.get(o) in owners for o in owners)}

    if collisions:
        print(f"    {len(collisions)} slug(s) claimed by two keys, so neither "
              f"employer is published:")
        for slug, owners in sorted(collisions.items()):
            if slug in distinct:
                note = "   (two DIFFERENT employers; blocked on the slug, do not merge)"
            elif slug in unnameable:
                note = "   (one employer, but no ASCII spelling to survive; blocked on the slug)"
            elif slug in undecided:
                note = ""
            else:
                note = "   (merged; the rows have not moved yet)"
            print(f"      /company/{slug}/{note}")
            for owner in owners:
                print(f"          {owner!r}")
    if undecided:
        problems.append(
            f"{len(undecided)} employer(s) recorded under two keys that claim "
            f"one URL and are not merged: decide which spelling wins, add it to "
            f"vocab.EMPLOYER_KEY_ALIASES, then queue correct-company-key.yml")
    if distinct or unnameable:
        problems.append(
            f"{len(distinct) + len(unnameable)} profile URL(s) are unpublishable "
            f"because the slug drops non-Latin characters: {len(distinct)} held by "
            f"two DIFFERENT employers, {len(unnameable)} by one employer with no "
            f"ASCII spelling to survive. Both reviewed in vocab; the fix is "
            f"tit_company_slug, NOT an alias")

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

    The parser itself now lives in `budget.py`, which is stdlib-only for the
    same reason this file is and needs the same number. Two parsers for one
    policy figure is how they come to disagree.
    """
    import budget

    return budget.monthly_allowance()


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


def _report_read_rations() -> list[str]:
    """Does the schedule hand out the rations the measured rule derives?

    The rule and its arithmetic live in `pipeline/classify.py` (READ_CONVERSION,
    BINDING_READ_BUDGET, read_cap): a collector's share of the read budget is
    its share of measured conversion. But `TIT_READTHROUGH_CAP` in a workflow
    beats the derived value — it has to, because the backfills set 5000 and a
    derived daily ration silently overriding an explicitly requested one is the
    kind of surprise this repo keeps paying for.

    So the two can disagree, and a disagreement is invisible from either side:
    the code looks right and the run buys something else. This is where it
    becomes visible. It is a WARNING and not an exit-2 problem, because a
    workflow deliberately overriding the rule is legitimate and the owner may
    have chosen it.
    """
    from pipeline import classify

    print("\n[2g] READ RATIONS  (reads follow measured conversion)")
    for name, cap in sorted(classify.COLLECTOR_READ_CAPS.items()):
        conv = classify.READ_CONVERSION[name]
        print(f"    {name:16} {cap:4} reads/run   measured conversion {conv:.1%}")
    print(f"    {'':16} {sum(classify.COLLECTOR_READ_CAPS.values()):4} total, held "
          f"constant at classify.BINDING_READ_BUDGET")

    scheduled = _scheduled_read_caps()
    disagree = {n: v for n, v in scheduled.items()
                if n in classify.COLLECTOR_READ_CAPS
                and v != classify.COLLECTOR_READ_CAPS[n]}
    if not disagree:
        print("    the schedule hands out exactly these.")
        return []

    for name, value in sorted(disagree.items()):
        print(f"    ::warning:: collect.yml sets TIT_READTHROUGH_CAP={value} for "
              f"{name}, where the")
        print(f"                measured rule derives "
              f"{classify.COLLECTOR_READ_CAPS[name]}. The workflow wins, so the "
              f"rule is")
        print("                inert for this collector until that number is "
              "changed.")
    return []


def _scheduled_read_caps() -> dict[str, int]:
    """The per-source TIT_READTHROUGH_CAP values the collect sweep exports.

    Read out of the workflow's shell `case` rather than kept as a constant
    here, for the same reason `_crons` reads the cron: a constant describing
    another file is a comment, and this script is supposed to be an authority.
    """
    path = ROOT / ".github" / "workflows" / "collect.yml"
    if not path.exists():
        return {}
    found: dict[str, int] = {}
    pattern = re.compile(
        r"^\s*([a-z_|]+)\)\s*export TIT_READTHROUGH_CAP=(\d+)")
    for line in path.read_text().splitlines():
        hit = pattern.match(line)
        if hit:
            for name in hit.group(1).split("|"):
                found[name] = int(hit.group(2))
    return found


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

    queue = writer_queue.load(queue_file)
    state = writer_queue.summary(queue)
    counts = state["counts"]
    if counts:
        print("    " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # Printed in the order they will actually be dispatched, not the order they
    # were asked for. Those stopped being the same thing when the dispatch key
    # learned what a slice costs, and a list that still read as FIFO would quietly
    # mislead about which chain moves next.
    for ticket in sorted(state["waiting"],
                         key=lambda t: writer_queue.dispatch_key(queue, t)):
        print(f"    {ticket['state']:<11} {ticket['workflow']:<26} "
              f"since {ticket['requested_at']}  attempts={ticket['attempts']}")
        if ticket.get("inputs"):
            print(f"                inputs {ticket['inputs']}")
        if ticket["state"] == "queued":
            # Only for work still in the line. A dispatched ticket is already
            # holding the lock, and printing where it WOULD have sorted reads
            # like a prediction about a decision that has been made.
            print(f"                {writer_queue.dispatch_reason(queue, ticket)}")
        if ticket.get("unbound_count"):
            print(f"                dispatched {ticket['unbound_count']}x with NO "
                  f"RUN produced — the dispatch is failing, not the work")

    # Deferred work is listed even though it is green. A budget stop does no
    # work and breaks nothing, so it must not redden the drainer — but it is
    # still work that has not happened, and a state visible only as a number in
    # `counts` is the shape of a queue nobody drains. Each one names its own
    # deadline, so a session can see whether it is still waiting or overdue.
    for held in state.get("deferred", []):
        if held.get("acknowledged"):
            continue
        print(f"    DEFERRED    {held['workflow']:<26} {held['reason'][:60]}")
        print(f"                needs a human after {held['needs_a_human_after']}")

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
        # A chain between slices has NOTHING running, so "is it still going?"
        # is answered by what is queued behind it and by nothing else. A
        # cancelled slice skips its commit step, records no progress and queues
        # no successor, and the chain then sits at a frozen cursor looking
        # exactly like one that is merely waiting its turn — for two days, in
        # the 2026-07-31 case, with `problems: []` the whole time.
        if job["state"] == "running" and "waiting_on" in job:
            idle = job.get("idle_hours")
            age = f", idle {idle:.0f}h" if isinstance(idle, (int, float)) else ""
            if job["waiting_on"] == "unknown":
                print(f"             next slice: UNKNOWN — no writer queue file "
                      f"to read{age}")
            elif job["waiting_on"]:
                print(f"             next slice queued as {job['waiting_on']}{age}")
            else:
                print(f"             next slice: NOTHING QUEUED{age} — the chain "
                      f"has stopped")
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
LINK_JOBS = {"archive-sources.yml": "eight-hourly Wayback pass",
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

    # THE READER-FACING RE-CHECK PROMISE. Every in-scope row without a snapshot
    # renders "No archive snapshot yet. We re-check weekly; next check by
    # <date>" (shipped as wordpress-plugin/.../data/archive_promise.json,
    # generated by build_archive_promise.py from the real schedule). This is
    # the check that keeps the sentence true: an in-scope unarchived URL that
    # has not been re-attempted inside the window is a live page lying about
    # its own upkeep, and that is a wrong statement already published, not a
    # stale source.
    try:
        overdue = source_links.archive_recheck_overdue(conn)
        print(f"    promise   every unarchived in-scope URL re-attempted within "
              f"{source_links.RECHECK_PROMISE_DAYS} days: "
              + ("KEPT" if not overdue else f"BROKEN for {len(overdue)} URL(s)"))
        if overdue:
            for row in overdue[:5]:
                print(f"      overdue  {row['source_url'][:70]} "
                      f"(last attempt {row['last_attempt'][:10] or 'never'})")
            problems.append(
                f"{len(overdue)} in-scope URL(s) without a snapshot have not "
                f"been re-attempted within the {source_links.RECHECK_PROMISE_DAYS}"
                f"-day promise the listing pages print. The pages are promising "
                f"a re-check nothing is making. Check that the archive slot in "
                f"schedule-link-hygiene.yml still fires and drain-writers is "
                f"moving, then queue a pass: gh workflow run drain-writers.yml "
                f"-f enqueue=archive-sources.yml "
                f"-f inputs_json='{{\"dry_run\":\"false\"}}' -f reason='promise'")
    except sqlite3.OperationalError:
        pass

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

    # Rows that went out over the ceiling because independent outlets agreed on
    # the figure. Printed even when nothing is quarantined: an exemption nobody
    # sees is the same kind of invisible as a queue nobody reads, and this one
    # publishes numbers rather than withholding them.
    corroborated = (report.get("amount") or {}).get("corroborated") or []
    if corroborated:
        print(f"    {len(corroborated)} figure(s) above the ceiling published "
              f"themselves on independent corroboration:")
        for row in corroborated[:6]:
            print(f"      OK    {row['label'][:44]:<44} "
                  f"{', '.join(row['outlets'][:3])}")

    held, live = report["held"], report["live"]
    overdue, aggregate = report["overdue"], report["aggregate"]

    # The decided ones. Not a queue and never part of the exit code - but a row
    # that will never publish has to be visible somewhere a session actually
    # looks, or a rejection is a silent delete with a nicer name.
    withheld = report.get("withheld") or []
    withheld_line = ""
    if withheld:
        total = sum(r.get("value") or 0 for r in withheld)
        withheld_line = (
            f"    withheld {len(withheld)} row(s) (${total / 1e9:,.2f}bn) by a "
            f"rejection: decided, never publishing.  guardrails.py --withheld")

    if not (held or live or aggregate):
        print("    Nothing quarantined. Every row publishes.")
        if withheld_line:
            print(withheld_line)
        return []

    print(f"    quarantined {len(held) + len(live)} row(s): {len(held)} held "
          f"back, {len(live)} already live")
    if withheld_line:
        print(withheld_line)

    for row in sorted(held + live, key=lambda r: -(r.get("value") or 0))[:6]:
        age, grace = row.get("age_hours"), row.get("grace_hours")
        left = "" if age is None else f"  red in {max(0.0, grace - age):.0f}h"
        # RJCT is the one that cannot be fixed by waiting: rejected AND on the
        # site, so only a retraction removes it.
        tag = ("RJCT" if row.get("rejected")
               else "LIVE" if row["already_live"] else "HELD")
        print(f"    {tag}  {row['check_name']:<13} "
              f"{(row.get('label') or '')[:44]:<44}{left}")
    if len(held) + len(live) > 6:
        print(f"          ... and {len(held) + len(live) - 6} more")

    problems = []

    # THE MONEY QUEUE, NAMED IN FULL AND NEVER TRUNCATED.
    #
    # The six-row summary above is a glance. This is not: it is the list of
    # figures a person has now been shown for two days and not answered, and it
    # prints every one of them with its dollars, because the state it exists to
    # prevent is the one found on 2026-08-04 - fifteen open `amount` findings
    # worth $874.2bn, none ever reviewed, one re-seen 229 times. A truncated
    # list is how a queue becomes decorative: "and 9 more" is not a fact
    # anybody acts on.
    #
    # This goes red at 48h while the publish runs stay green until their own
    # grace window, and the split is deliberate. Red CI trains people to skim.
    # The tool the session ritual runs FIRST is the right place to be insistent,
    # and it is read by a person who is already sitting down to work.
    unreviewed = guardrails.unreviewed_amounts(held + live)
    if unreviewed:
        total = sum(r.get("value") or 0 for r in unreviewed)
        print(f"\n    UNREVIEWED FUNDING FIGURES, all of them "
              f"({len(unreviewed)} row(s), ${total / 1e9:,.1f}bn held out of "
              f"every published figure):")
        for row in unreviewed:
            where = "on the live site" if row.get("already_live") else "held back"
            print(f"      ${(row.get('value') or 0) / 1e9:>8,.2f}bn  "
                  f"{(row.get('label') or '')[:46]:<46} "
                  f"{row['age_hours'] / 24:.0f}d unanswered, {where}")
            print(f"                  {row['check_name']}/{row['subject']}")
        problems.append(
            f"{len(unreviewed)} funding figure(s) worth ${total / 1e9:,.1f}bn "
            f"have been in the amount queue longer than "
            f"{guardrails.AMOUNT_REVIEW_DEADLINE_HOURS}h with nobody's answer on "
            f"them. Every one is out of the money charts, the totals and the "
            f"table until it is answered. Read each source, then "
            f"`python3 guardrails.py --accept amount/<hash> '<why>'` for the "
            f"real ones and `--reject` plus `python3 retract.py` for the rest.")

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


def _report_landmarks(conn) -> list[str]:
    """The events nobody could defend missing. RECOMPUTED here, not read.

    This section exists because on 2026-08-04 the owner found by hand that the
    three largest private funding rounds ever recorded were absent from the
    site, and every automated check was green while they were. Source health
    answers "did the collector run". Data integrity answers "do the numbers add
    up". Recall answers "do we hold a representative sample of the world". Not
    one of those can notice a specific enormous event going missing.

    The STORED lens is recomputed from the committed database on every run,
    because a weekly report is up to seven days stale and a session that
    breaks collection today should see it today. The LIVE lens needs the
    network, which this file will not touch, so it is read from the committed
    report and always printed with its date.

    Only a REGRESSION is an ACTION NEEDED item. A landmark that has never been
    held is a standing gap: real, listed, and not this run's fault. A permanent
    red on a number only backfilling can move teaches the next session to
    ignore the exit code, which is the failure this whole section is about.
    """
    import json

    print("\n[3d] LANDMARKS  (the events nobody could defend missing)")

    try:
        from analysis.landmarks import check as lcheck
        from analysis.landmarks import landmarks as lset
    except Exception as exc:               # pragma: no cover - import guard
        print(f"    Could not load the landmark check: {exc}")
        return ["the landmark check will not import, so nothing is watching "
                "the largest rounds"]

    try:
        data = lset.load()
    except lset.InvalidLandmarkSet as exc:
        print(f"    {exc}")
        return ["the landmark set is not usable, so the guard is INERT"]

    report_path = ROOT / "data" / "landmarks_report.json"
    previous = None
    if report_path.exists():
        try:
            previous = json.loads(report_path.read_text())
        except ValueError:
            previous = None

    rows = lcheck.stored_rows(conn)
    body = lcheck.evaluate(
        lset.entries(data), rows, None,
        today=_utcnow().date(),
        history=lcheck.previous_history(previous),
        tolerance=float(data.get("amount_tolerance") or lcheck.AMOUNT_TOLERANCE),
        window_days=int(data.get("window_days") or lcheck.WINDOW_DAYS),
    )
    summary = body["summary"]
    print("    stored, recomputed now: " + summary["one_line"])

    problems = []
    for item in body["entries"]:
        if item["regression"]:
            problems.append(
                "landmark REGRESSION: %s %s ($%.3gbn) was held and is not any "
                "more (%s)" % (item["quarter"], item["company"],
                               item["amount_usd"] / 1e9,
                               "; ".join(item["regression"])))

    gaps = [i for i in body["entries"]
            if i["status"] != "held" and not i["regression"]]
    if gaps:
        print("    standing gaps, largest first:")
        for item in sorted(gaps, key=lambda i: -float(i["amount_usd"]))[:8]:
            print("      %-8s %-16s $%-8s %s"
                  % (item["quarter"], item["company"][:16],
                     _landmark_money(item["amount_usd"]), item["status"]))
        if len(gaps) > 8:
            print("      ... and %d more" % (len(gaps) - 8))
        print("      Full list, with the primary document for each:")
        print("        python3 check_landmarks.py")

    if previous:
        live = (previous.get("summary") or {})
        checked = previous.get("checked_on")
        stale = lcheck.report_is_stale(previous, _utcnow().date())
        print("    live (what a reader sees), from the weekly report of %s%s:"
              % (checked, "  STALE" if stale else ""))
        print("      %s" % live.get("one_line", "no summary recorded"))
        if live.get("held_not_live"):
            # The exact 2026-08 defect: correct rows, quarantined, invisible.
            print("      %d landmark(s) are STORED and NOT LIVE. Those are "
                  "rows we hold and no reader can see;" % live["held_not_live"])
            print("      check the publish guardrails: python3 guardrails.py")
        if stale:
            problems.append(
                "the landmark report is stale (last checked %s). The weekly "
                "landmarks workflow has probably stopped."
                % (checked or "never"))
    else:
        print("    live lens: UNKNOWN, no report has ever been written.")
        print("      python3 check_landmarks.py --live --write")
        problems.append("no landmark report has ever been written, so the "
                        "reader-facing lens has never been checked")

    return problems


def _landmark_money(value) -> str:
    value = float(value or 0)
    return "%.4gbn" % (value / 1e9) if value >= 1e9 else "%.4gm" % (value / 1e6)


def _report_rejection_audit() -> None:
    """WHY we miss what we miss — the roadmap, not a scoreboard.

    `analysis/recall/rejection_audit.py` takes every gold-set event we did not
    hold and asks which of four things went wrong. It has been produced since
    2026-07-29 and surfaced NOWHERE: the file sat in data/ and no session that
    did not already know its filename would ever have opened it.

    IT IS PRINTED AS A DIAGNOSIS AND NOT AS A NUMBER, because the split is the
    whole finding and the headline count is the least useful part of it:

        fetched_then_dropped = 0

    Almost no gold event has ever been fetched and then rejected by a filter.
    The prefilter, the gate, the vocabularies and the guards are not what is
    losing coverage — and "our filters are too aggressive" is the intuitive
    diagnosis that this measurement refutes.

    WHAT THE LARGEST BUCKET IS HAS CHANGED ONCE, and the change is the reason
    to keep reading this section rather than remembering it. Until the
    historical walkers ran it was `outside_our_history`: events that predate
    the collector that would have caught them, a YOUNG CORPUS rather than a
    leaky one. Since 2026-08-12 the audit reads the walkers' committed cursors
    too, and most of those events turn out to fall on days a walker has since
    FINISHED. That is `walked_never_read`, and it is a different bill: the day
    was swept at whatever depth its ration bought, so dispatching more slices
    walks past the same events again and only depth closes them.

    So this section prints the causes with what each one means you should DO,
    for every reference set that has an audit. It is deliberately not an ACTION
    NEEDED item: a rationed walk is the designed behaviour at this ceiling, and
    a permanent red on the budget would train the next session to ignore the
    exit code.
    """
    import json

    print("\n[3c] WHY WE MISS WHAT WE MISS  (the feed roadmap, from the gold set)")

    try:
        from analysis.recall import family as families
        from analysis.recall.rejection_audit import out_path_for
        paths = [(f.label, out_path_for(f)) for f in families.ALL]
    except Exception:                                # pragma: no cover
        paths = [("Worldwide", ROOT / "data" / "recall_rejection_audit.json")]

    printed = False
    for label, path in paths:
        # A family with no audit file is skipped rather than reported empty.
        # The worldwide one is the historical file every earlier session read;
        # a second family that has never been audited must not make this
        # section look like the first one has gone missing.
        if not path.exists():
            continue
        printed = True
        if len(paths) > 1:
            print(f"    -- {label} --")
        try:
            _print_one_rejection_audit(json.loads(path.read_text()))
        except ValueError:
            print("    Audit file is unreadable. Re-run "
                  "analysis/recall/rejection_audit.py.")
    if not printed:
        print("    No audit yet. It is produced beside the recall measurement:")
        print("      python -m analysis.recall.rejection_audit [--family us]")


def _print_one_rejection_audit(data: dict) -> None:
    """One family's block, so the section can carry more than one."""
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
        ("walked_never_read",
         "a walker finished this day", "DEPTH, which is money. Dispatching more "
         "slices walks past these again"),
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
        print("    READ THE ZERO: no filter rejected a gold event in this set. "
              "Whatever is losing coverage, it is not the filters.")

    split = data.get("split") or {}
    if split:
        print("    by cause: " + ", ".join(f"{k}={v}" for k, v in split.items()))


def _report_spend() -> list[str]:
    """[5] What the allowance is for, and who is waiting on it.

    Measuring month-to-date needs a key, which ops_status deliberately does
    not require, so the figure is not printed here. The POLICY is a committed
    file and can be read with no key at all, which is the part that was
    missing: a session could see what had been spent and never see what the
    money was for next.

    Three states, printed as three states:

      FUNDED FIRST        forward work, 2026-01-01 onward.
      DEFERRED BY POLICY  paid extraction and discovery over earlier windows.
                          Not broken, not finished.
      NEEDS A DECISION    a deferral that has outlived the allowance month it
                          was taken in, plus grace. Returned as a problem, so
                          a pause cannot quietly become permanent.
    """
    print("\n[5] SPEND")

    try:
        import ast

        src = (ROOT / "spend.py").read_text()
        tree = ast.parse(src)
        const: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    try:
                        const[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass
        allowance = float(const["MONTHLY_ALLOWANCE_USD"])
        stop = float(const["STOP_AT_FRACTION"])
        forward_from = str(const["FORWARD_FROM"])
        adopted = str(const["POLICY_ADOPTED"])
        grace = int(const["DEFERRAL_GRACE_DAYS"])
        opt_in_env = str(const["BACKFILL_OPT_IN_ENV"])
    except (OSError, SyntaxError, KeyError, ValueError, TypeError) as exc:
        # UNKNOWN, and said so. Absence of a reading is not a pass, but it is
        # also not evidence of a fault, so it does not manufacture an issue.
        print(f"    UNKNOWN: could not read the policy out of spend.py ({exc}).")
        print("    This run did not establish what is funded. Not a pass.")
        return []

    print(f"    allowance           ${allowance:,.2f} per UTC calendar month, stop at "
          f"{int(stop * 100)}% (policy, spend.py)")

    # THE ONE LINE. `source_health.cost_usd` is a committed per-run ledger, so
    # month-to-date spend IS readable from here — offline, keyless, and split
    # by pot. This used to say "run spend.py with the key", which meant the
    # section a session reads first could not answer the question the owner
    # asks most.
    import budget

    ledger = budget.ledger_spend()
    print("    " + budget.status_line(allowance=allowance, charged=ledger))
    pot = budget.pots(allowance)
    print(f"    TWO POTS            ${pot[budget.COMMITTED]:,.2f} committed "
          f"(the scheduled collectors, paid first) and "
          f"${pot[budget.DISCRETIONARY]:,.2f} discretionary (the backfill "
          f"walkers, ab-models,")
    print("                        benchmark-diff). A catch-up job spends only "
          "the second, and its per-run ceiling is what remains divided by the "
          "days left, so it slows rather than stopping. No backfill can "
          "degrade the collectors.")
    print("    authoritative total run `python spend.py` with OPENROUTER_API_KEY: "
          "the ledger above misses jobs that call a model without filing a "
          "priced health row, so it is a floor.")
    print(f"    FUNDED FIRST        paid extraction and discovery for {forward_from} "
          f"onward, and every correction, retraction and guardrail check on rows "
          f"already published, at ANY date")
    print(f"    DEFERRED BY POLICY  paid extraction and discovery for windows before "
          f"{forward_from}")
    print("                        Not broken and not finished. The free paths keep "
          "running: fetch, registries, deterministic parsing, validation and dedup "
          "cost nothing and are unaffected, and free forward collection continues "
          "after the paid ceiling is reached.")
    print(f"                        Deferred since {adopted}. Cursors and the "
          f"self-requeue chain are intact, so a funded run resumes on the first "
          f"window it did not do.")

    # The escalation clock. Start of the next UTC allowance month after
    # adoption, plus the grace, because a new month is new money and that is
    # the honest moment to re-decide.
    d = date.fromisoformat(adopted)
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    due = nxt + timedelta(days=grace)
    print(f"    review due          {due.isoformat()}")
    print(f"    to opt back in      dispatch the walker with historical_backfill "
          f"ticked, or set {opt_in_env}=on")

    if datetime.now(timezone.utc).date() >= due:
        return [f"historical backfill has been deferred since {adopted} and the "
                f"review date {due.isoformat()} has passed: the owner needs to "
                f"either opt it back in or restate the deferral (docs/HANDOVER.md)"]
    return []


def _report_surfaces() -> None:
    print("\n[4] SURFACES")
    print(f"    dashboard  {LIVE_URL}")
    print(f"    sibling    {SIBLING_URL}  (layoffs are READ from its API, never collected here)")
    print(f"    repo       https://github.com/dk-forge/talent-intelligence-tracker")


if __name__ == "__main__":
    sys.exit(main())

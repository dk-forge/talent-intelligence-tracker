"""The write path. Every collector funnels through here, so every guard
applies exactly once (spec 6 rule 1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from . import dedupe


def already_seen(conn: sqlite3.Connection, url: str) -> bool:
    """Spec 4 rule 2: dedupe BEFORE the LLM, never after."""
    row = conn.execute("SELECT 1 FROM seen_urls WHERE url = ? LIMIT 1", (url,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, url: str, collector: str, outcome: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_urls (url, first_seen, collector, outcome) VALUES (?, ?, ?, ?)",
        (url, datetime.now(timezone.utc).isoformat(timespec="seconds"), collector, outcome),
    )


def record_corroboration(conn: sqlite3.Connection, signal_id: str, *,
                         source_url: str, source_name: str = "",
                         amount_usd: int | None = None,
                         collector: str = "") -> bool:
    """Write down that a SECOND outlet reported a round we already hold.

    Called from the one place the fact is still available: the moment dedup
    decides an arriving article is a round we already stored and drops it. By
    design that article never becomes a row, so if this is not written here the
    only trace left is a url in seen_urls marked `duplicate` - which carries no
    employer, no amount, and no pointer to what it duplicated.

    That loss had a price. `pipeline/guardrails.check_amounts` holds back any
    single figure the corpus's own distribution cannot explain, and in 2026 the
    derived ceiling (~$6.5bn) sits BELOW every real AI mega-round, so it flags
    correct answers at the same rate as wrong ones. The one thing that tells a
    real $30bn round from a misread $539bn of assets under management is that
    several independent outlets state the same figure - and that was exactly
    what was being thrown away. See guardrails.CORROBORATION_MIN_OUTLETS.

    Returns True when this is a new outlet for that round. Never raises: a
    missing table on an old database, or a locked one, must not take down a
    collect run over a note about a row that was going to be skipped anyway.
    `INSERT OR IGNORE` keyed on (signal_id, host) makes it idempotent, so an
    outlet that republishes the same story eight times counts once.
    """
    host = registrable_host(source_url)
    if not signal_id or not host:
        return False
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO funding_corroborations "
            "  (signal_id, host, source_url, source_name, amount_usd, "
            "   collector, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (signal_id, host, source_url, source_name, amount_usd, collector,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return bool(cur.rowcount)
    except sqlite3.Error:
        return False


def registrable_host(url: str) -> str:
    """The registrable domain of a url, or "" when it cannot be read.

    One import site for the whole write path, and it is deliberately a
    re-export rather than a second implementation: `collectors.national_press`
    already owns the multi-label suffix table that stops `guardian.co.tt` and
    `mirror.co.tt` comparing equal, and two outlets that compare equal when
    they are not is precisely the way a corroboration count lies upward.

    Imported lazily because `collectors` imports `pipeline`, and because that
    module imports `requests`, which the test machine does not have. The
    fallback takes the last two labels, which OVER-merges hosts under a
    two-label public suffix - so it can only ever return FEWER distinct
    outlets, never more. That is the safe direction for a rule whose whole job
    is to be hard to satisfy.
    """
    try:
        from collectors.national_press import registrable_domain
        return registrable_domain(url)
    except Exception:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower().strip(".")
        return ".".join(host.split(".")[-2:]) if host else ""


def duplicate_verdict(conn: sqlite3.Connection, signal) -> str | None:
    """'duplicate', 'retracted', or None when this signal would be inserted.

    Both dedup layers and nothing else: no write, no model, no network, two
    indexed reads. Split out of `store()` so a caller can ask "will this
    store?" BEFORE buying the read-through, which is the most expensive call
    in the pipeline. Measured over the nine runs in the ledger to 2026-07-30,
    477 interpretations were bought against 320 rows stored — a third of them
    went to records that met one of these two layers, or a validate rejection,
    a moment later.

    `store()` still asks the same question through this same function, so a
    caller that does not call it first is unaffected and the two answers
    cannot drift apart.
    """
    known = dedupe.exact_duplicate(conn, signal.content_hash)
    if known:
        return known
    if dedupe.fuzzy_duplicate(conn, signal):
        return "duplicate"
    return None


def store(conn: sqlite3.Connection, signal) -> str:
    """Insert a signal. Returns 'stored', 'duplicate' or 'retracted'.

    'retracted' is reported separately so a withdrawn record resurfacing is
    visible in the run output rather than looking like ordinary dedup.
    """
    verdict = duplicate_verdict(conn, signal)
    if verdict:
        return verdict

    data = asdict(signal)
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    conn.execute(
        f"INSERT INTO signals ({columns}) VALUES ({placeholders})",
        tuple(data.values()),
    )
    return "stored"


def revise(conn: sqlite3.Connection, signal_id: str, new_signal, note: str) -> None:
    """Correct a record by appending a revision. Never an UPDATE of the facts.

    Spec 18: you must be able to reconstruct what we knew on any past date, so
    the old row survives with is_current = 0.
    """
    current = conn.execute(
        "SELECT row_id, revision FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,),
    ).fetchone()
    if current is None:
        raise ValueError(f"no current revision for signal_id {signal_id}")

    conn.execute("UPDATE signals SET is_current = 0 WHERE row_id = ?", (current["row_id"],))

    data = asdict(new_signal)
    data["signal_id"] = signal_id
    data["revision"] = current["revision"] + 1
    data["is_current"] = 1
    data["supersedes_row_id"] = current["row_id"]
    data["as_of"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["notes"] = note

    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    conn.execute(
        f"INSERT INTO signals ({columns}) VALUES ({placeholders})",
        tuple(data.values()),
    )


# What a run's model accounting is stored under, in the order a reader wants
# it. Named here rather than spelled out in the INSERT because report_health
# writes only the ones the database in front of it actually has: this file also
# runs against checkouts and read-only copies that predate the columns, and a
# health row is the last thing that should fail to land over a cost figure.
USAGE_COLUMNS = (
    "model", "gate_model", "prompt_tokens", "cached_tokens",
    "completion_tokens", "cost_usd", "reads_bought", "rows_from_reads",
    # The funnel. What was screened, what the screen threw away, and what it
    # KEPT and the budget would not pay to read — the last of which is the
    # coverage gap, and used to exist only in a step log.
    "candidates", "gate_calls", "gate_rejects", "budget_deferred",
)


def health_has_cost_columns(conn: sqlite3.Connection) -> bool:
    """Whether this database can hold per-run cost yet.

    Read by the tools that REPORT cost. They open the database directly rather
    than through schema.connect(), so they can be looking at a file the
    migration has not reached.
    """
    present = {row[1] for row in conn.execute("PRAGMA table_info(source_health)")}
    return set(USAGE_COLUMNS) <= present


def reads_to_rows_pct(reads_bought: int | None, rows_from_reads: int | None) -> int | None:
    """The waste ratio, computed in ONE place.

    A full read-through that stores nothing is money spent on a row the page
    never got — a model NO after the gate said yes, a validate rejection the
    precheck could not see, a post-read duplicate. The run log prints this and
    so does ops_status, and they must not be able to disagree, which is the
    whole reason it is a function rather than two format strings.
    """
    if not reads_bought:
        return None
    return int(rows_from_reads or 0) * 100 // int(reads_bought)


def report_health(
    conn: sqlite3.Connection,
    collector: str,
    *,
    status: str,
    items_found: int = 0,
    items_stored: int = 0,
    detail: str = "",
    usage: dict | None = None,
) -> None:
    """Spec 6 rule 4: a collector returning zero is degraded, never ok.

    `usage` is what the model charged this run (classify.usage_snapshot()).
    Omitted — or None, which is what a run that called no model reports — the
    cost columns stay NULL, so a free run reads as unmeasured rather than as a
    measured zero.
    """
    if items_found == 0 and status == "ok":
        status = "degraded"
        detail = (detail + " | zero items found").strip(" |")

    row: dict = {
        "collector": collector,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "items_found": items_found,
        "items_stored": items_stored,
        "detail": detail,
    }

    present = {r[1] for r in conn.execute("PRAGMA table_info(source_health)")}

    # Which pot paid for this run. Recorded on EVERY row, not only priced ones,
    # so the ledger can say "a discretionary run stored these and bought
    # nothing" as well as "it cost this much". One place, so no collector has
    # to remember; budget.run_kind() reads the workflow's own declaration and
    # defaults to committed, which is the direction that protects the
    # scheduled collectors.
    if "run_kind" in present:
        import budget

        row["run_kind"] = budget.run_kind()

    if usage:
        for name in USAGE_COLUMNS:
            if name in present and name in usage:
                row[name] = usage[name]

    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR REPLACE INTO source_health ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )

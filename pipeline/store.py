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

    if usage:
        present = {r[1] for r in conn.execute("PRAGMA table_info(source_health)")}
        for name in USAGE_COLUMNS:
            if name in present and name in usage:
                row[name] = usage[name]

    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR REPLACE INTO source_health ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )

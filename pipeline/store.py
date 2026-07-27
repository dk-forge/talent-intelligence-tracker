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


def store(conn: sqlite3.Connection, signal) -> str:
    """Insert a signal. Returns 'stored', 'duplicate' or 'retracted'.

    'retracted' is reported separately so a withdrawn record resurfacing is
    visible in the run output rather than looking like ordinary dedup.
    """
    known = dedupe.exact_duplicate(conn, signal.content_hash)
    if known:
        return known
    if dedupe.fuzzy_duplicate(conn, signal):
        return "duplicate"

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


def report_health(
    conn: sqlite3.Connection,
    collector: str,
    *,
    status: str,
    items_found: int = 0,
    items_stored: int = 0,
    detail: str = "",
) -> None:
    """Spec 6 rule 4: a collector returning zero is degraded, never ok."""
    if items_found == 0 and status == "ok":
        status = "degraded"
        detail = (detail + " | zero items found").strip(" |")

    conn.execute(
        """
        INSERT OR REPLACE INTO source_health
            (collector, run_at, status, items_found, items_stored, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            collector,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            status,
            items_found,
            items_stored,
            detail,
        ),
    )

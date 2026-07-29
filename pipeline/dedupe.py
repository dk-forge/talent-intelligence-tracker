"""Dedup layers 1 and 2 (spec 7).

One acquisition reported by forty outlets must become one record. Layer 3 (a
bounded, rotating LLM deep scan for the pairs these miss) is a scheduled job,
not part of the write path.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

# Same company + same pillar inside this window is the same development.
SAME_EVENT_DAYS = 14

# Spec 7 lesson (a): a flat window misses a re-report of the same event months
# later, so a near-identical headline gets a much wider window.
NEAR_IDENTICAL_DAYS = 400


def exact_duplicate(conn: sqlite3.Connection, content_hash: str) -> str | None:
    """Return why this hash is already known, or None.

    Deliberately ignores is_current. A retracted record is still a record we
    have judged and withdrawn, and re-storing it would silently undo the
    retraction — which is how the WWT homepage-sourced row came back. The
    unique index spans all revisions, so checking only current rows also
    crashed the run with an IntegrityError instead of skipping.
    """
    row = conn.execute(
        "SELECT is_current, notes FROM signals WHERE content_hash = ? LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    if row["is_current"]:
        return "duplicate"
    return "retracted"


def fuzzy_duplicate(conn: sqlite3.Connection, signal) -> str | None:
    """Return the signal_id of an existing record this duplicates, or None."""
    if not signal.published_date:
        return None

    pub = date.fromisoformat(signal.published_date)
    window_start = (pub - timedelta(days=SAME_EVENT_DAYS)).isoformat()
    window_end = (pub + timedelta(days=SAME_EVENT_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT signal_id, headline, published_date
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = ?
           AND published_date BETWEEN ? AND ?
        """,
        (signal.company_key, signal.pillar, window_start, window_end),
    ).fetchall()
    if rows:
        return rows[0]["signal_id"]

    wide_start = (pub - timedelta(days=NEAR_IDENTICAL_DAYS)).isoformat()
    wide_end = (pub + timedelta(days=NEAR_IDENTICAL_DAYS)).isoformat()
    candidates = conn.execute(
        """
        SELECT signal_id, headline
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = ?
           AND published_date BETWEEN ? AND ?
        """,
        (signal.company_key, signal.pillar, wide_start, wide_end),
    ).fetchall()

    for row in candidates:
        if _token_overlap(row["headline"], signal.headline) >= 0.85:
            return row["signal_id"]
    return None


def funding_event_duplicate(conn: sqlite3.Connection, company_key: str,
                            amount_usd: int | None, amount_canon: str,
                            days: int = 21) -> str | None:
    """A funding round we already hold, matched BEFORE any model is paid.

    fuzzy_duplicate above catches the same round after classification, which
    means the read-through was already bought. This runs on the deterministic
    parse of the headline (pipeline/cheap_extract.py), so the seventh outlet
    to rewrite a round we stored on Monday costs nothing at all.

    Matched on employer + amount, inside a recency window. The amount match
    uses the USD integer when both sides parse, else the canonical text form
    (currency kept, so €71M never matches $71M). Returns the existing
    signal_id, or None — and None on any doubt, because the cost of a miss
    here is one paid read, while a false match silently drops a real story.
    """
    if not company_key or not amount_canon:
        return None
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT signal_id, funding_amount, funding_amount_usd
          FROM signals
         WHERE is_current = 1
           AND company_key = ?
           AND pillar = 'company_development'
           AND funding_amount IS NOT NULL
           AND published_date >= ?
        """,
        (company_key, since),
    ).fetchall()

    from . import cheap_extract  # local import; cheap_extract imports nothing from here

    for row in rows:
        if amount_usd is not None and row["funding_amount_usd"] == amount_usd:
            return row["signal_id"]
        stored_canon = cheap_extract._canon_amount(row["funding_amount"] or "")
        if stored_canon and stored_canon == amount_canon:
            return row["signal_id"]
    return None


def _token_overlap(a: str, b: str) -> float:
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

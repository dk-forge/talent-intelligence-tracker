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


def exact_duplicate(conn: sqlite3.Connection, content_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM signals WHERE content_hash = ? AND is_current = 1 LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row is not None


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


def _token_overlap(a: str, b: str) -> float:
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

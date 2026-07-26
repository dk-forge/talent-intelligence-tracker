"""SQLite schema — immutable and event-sourced from day one (spec 18).

Two rules drive every decision here, and both are impossible to retrofit:

1. Rows are never updated in place. A correction appends a new revision of the
   same `signal_id`. `is_current` marks the newest revision, so you can still
   reconstruct exactly what we knew on any past date.
2. Outcome fields exist now even though nothing resolves them for months.
   Without them there is no lead-lag measurement later, and therefore no
   accuracy scorecard.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "talent_intel.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    row_id            INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Stable across revisions. An outcome points back at this.
    signal_id         TEXT    NOT NULL,
    revision          INTEGER NOT NULL DEFAULT 1,
    is_current        INTEGER NOT NULL DEFAULT 1,
    supersedes_row_id INTEGER,

    -- What happened
    headline          TEXT    NOT NULL,
    summary           TEXT    NOT NULL,
    talent_readthrough TEXT   NOT NULL,

    company           TEXT    NOT NULL,
    company_key       TEXT    NOT NULL,
    pillar            TEXT    NOT NULL,
    signal_direction  TEXT    NOT NULL,

    city              TEXT,
    region            TEXT,
    country           TEXT,

    confidence        TEXT    NOT NULL,

    -- Provenance. No source URL, no row (spec 2 rule 1).
    source_url        TEXT    NOT NULL,
    source_name       TEXT    NOT NULL,
    discovery_url     TEXT,
    archive_url       TEXT,
    published_date    TEXT,

    -- Time. as_of is when WE believed this; published_date is the source's.
    captured_at       TEXT    NOT NULL,
    as_of             TEXT    NOT NULL,

    -- Dedup
    content_hash      TEXT    NOT NULL,

    -- Outcome verification (spec 18 loop 7). Unused for months by design.
    predicted_outcome   TEXT,
    check_after_date    TEXT,
    outcome_observed    TEXT,
    outcome_source_url  TEXT,
    outcome_checked_at  TEXT,

    collector         TEXT    NOT NULL,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_current  ON signals(is_current);
CREATE INDEX IF NOT EXISTS idx_signals_geo      ON signals(country, city);
CREATE INDEX IF NOT EXISTS idx_signals_pillar   ON signals(pillar);
CREATE INDEX IF NOT EXISTS idx_signals_pub      ON signals(published_date);
CREATE INDEX IF NOT EXISTS idx_signals_company  ON signals(company_key);
CREATE INDEX IF NOT EXISTS idx_signals_sigid    ON signals(signal_id, revision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_hash_rev ON signals(content_hash, revision);

-- Every URL we have ever looked at, so we never pay an LLM for it twice.
-- Spec 4 rule 2: this removed ~60% of daily extraction volume on the sibling.
CREATE TABLE IF NOT EXISTS seen_urls (
    url         TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    collector   TEXT NOT NULL,
    outcome     TEXT NOT NULL   -- stored | rejected | duplicate
);

-- Collector status ledger (spec 16 loop 1). A collector that returns zero
-- writes 'degraded', never 'ok'.
CREATE TABLE IF NOT EXISTS source_health (
    collector    TEXT NOT NULL,
    run_at       TEXT NOT NULL,
    status       TEXT NOT NULL,  -- ok | degraded | running | error
    items_found  INTEGER NOT NULL DEFAULT 0,
    items_stored INTEGER NOT NULL DEFAULT 0,
    detail       TEXT,
    PRIMARY KEY (collector, run_at)
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

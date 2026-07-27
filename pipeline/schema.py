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

TABLES = """
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

    -- Where the ROLES are, taken from the source text only.
    city              TEXT,
    region            TEXT,
    country           TEXT,

    -- Where the EMPLOYER is headquartered. Distinct provenance: this is not a
    -- claim the source made, so it is never presented as the event location.
    -- It exists so "Revolut CEO steps down" is findable under London, the same
    -- union the sibling tracker exposes as country_basis=any.
    hq_city           TEXT,
    hq_country        TEXT,
    -- US state, for the state filter. Only set when the country is US.
    state             TEXT,

    -- What the signal is ABOUT, for the filters a recruiter actually uses.
    -- functions is a JSON array of closed-vocabulary values.
    functions         TEXT,
    industry          TEXT,

    -- Figures. Both must appear verbatim in the source text or they are not
    -- stored: same rule as every other number on a record.
    headcount         INTEGER,
    funding_amount    TEXT,

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
    notes             TEXT,

    -- Set once WordPress has accepted the row. SQLite is the system of record;
    -- WordPress is a rendering surface, so publishing is resumable and a row
    -- that failed stays unpublished and is retried rather than lost.
    published_at      TEXT
);


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

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_signals_current  ON signals(is_current);
CREATE INDEX IF NOT EXISTS idx_signals_geo      ON signals(country, city);
CREATE INDEX IF NOT EXISTS idx_signals_hq       ON signals(hq_country, hq_city);
CREATE INDEX IF NOT EXISTS idx_signals_pub_at  ON signals(published_at);
CREATE INDEX IF NOT EXISTS idx_signals_pillar   ON signals(pillar);
CREATE INDEX IF NOT EXISTS idx_signals_industry ON signals(industry);
CREATE INDEX IF NOT EXISTS idx_signals_state    ON signals(state);
CREATE INDEX IF NOT EXISTS idx_signals_pub      ON signals(published_date);
CREATE INDEX IF NOT EXISTS idx_signals_company  ON signals(company_key);
CREATE INDEX IF NOT EXISTS idx_signals_sigid    ON signals(signal_id, revision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_hash_rev ON signals(content_hash, revision);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to an existing table, and the database is committed to the repo and
# long-lived, so every new column needs an entry here or an old checkout breaks
# the moment an index references it.
#
# Append only. Never remove or reorder — rows must stay reconstructable.
MIGRATIONS = (
    ("signals", "hq_city", "TEXT"),
    ("signals", "hq_country", "TEXT"),
    ("signals", "published_at", "TEXT"),
    ("signals", "state", "TEXT"),
    ("signals", "functions", "TEXT"),
    ("signals", "industry", "TEXT"),
    ("signals", "headcount", "INTEGER"),
    ("signals", "funding_amount", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any missing columns. Returns what it added, for logging."""
    applied = []
    for table, column, decl in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table not created yet; the CREATE covers it
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    return applied


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Order matters: create tables, then add missing columns, then indexes —
    # an index on a not-yet-added column is what broke this the first time.
    conn.executescript(TABLES)
    _migrate(conn)
    conn.executescript(INDEXES)
    conn.commit()
    return conn

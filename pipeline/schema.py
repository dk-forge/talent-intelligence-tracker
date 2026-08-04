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

from . import vocab

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

    -- Employer identity keys, and the join to the sibling layoff tracker.
    -- company_key is a normalised NAME, so it collapses the moment two
    -- employers share one. cik is free and exact: the SEC collectors already
    -- parse it out of every EFTS hit. ticker is stored only when the text
    -- states it, never looked up.
    ticker            TEXT,
    cik               TEXT,

    -- What kind of organisation the employer is (public/private/startup/...).
    -- Same provenance as hq_country: the model's own knowledge of the company,
    -- not a claim the article made.
    employer_type     TEXT,

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

    -- What the headcount COUNTS: new roles, the whole workforce, one site, or
    -- roles affected. 4,000 means three different things without this, and a
    -- reader sorting by headcount would be comparing unlike quantities.
    headcount_scope   TEXT,

    -- The same funding figure as a plain integer of US dollars, parsed
    -- deterministically in Python from funding_amount. The string column stays
    -- exactly as the source wrote it: it is the quotable form, this is the
    -- arithmetic one. NULL for non-USD currencies (we will not invent an
    -- exchange rate) and for anything that will not parse.
    funding_amount_usd INTEGER,

    -- The round's name. A $30M seed and a $30M Series D are different talent
    -- events; without the stage the only sortable thing about funding is size.
    funding_stage     TEXT,

    -- Where the work happens, when the source says so.
    work_mode         TEXT,

    -- The corporate event, when the source names one, from the perspective of
    -- `company`: 'acquisition' is this employer buying, 'acquired' is this
    -- employer being bought. Same event, opposite meaning to a recruiter.
    deal_type         TEXT,

    -- What the employer did with a PLACE of work: opened | closed | expanded |
    -- relocated | announced. The earliest geographic hiring signal there is,
    -- because a site decision lands months before the job adverts do. It says
    -- nothing at all about headcount: 'opened' is not 'hiring' and 'closed' is
    -- not 'displacement' unless the source states the roles. city/country
    -- carry the where, as they do for every other row.
    site_event        TEXT,

    -- How much this row is worth a recruiter's attention: high | medium |
    -- routine. Computed deterministically in Python (validate.compute_
    -- materiality) from values already on the row, so it costs nothing and can
    -- be recomputed over the whole table without refetching anything.
    materiality       TEXT,

    confidence        TEXT    NOT NULL,

    -- Provenance. No source URL, no row (spec 2 rule 1).
    source_url        TEXT    NOT NULL,
    source_name       TEXT    NOT NULL,
    discovery_url     TEXT,
    archive_url       TEXT,
    published_date    TEXT,

    -- When the change TAKES EFFECT, when the source states it. Distinct from
    -- published_date: "Tim Cook steps down as CEO in September" is a July
    -- article about a September event, and filing it under July is the wrong
    -- answer to "who is leaving next quarter". Stored only when the source
    -- states it, never derived from published_date.
    effective_date    TEXT,

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

    -- WHAT THE RUN COST. classify.STATS has accumulated the provider's own
    -- usage accounting since the day the gate was added, and printed it, and
    -- then thrown it away when the process exited. So spend drift was only
    -- visible in a month-end total, and "cost per stored row" — the one number
    -- that says whether a change to the prompt, the cap or the model paid for
    -- itself — could not be plotted at all.
    --
    -- It belongs HERE and not in a new table: a run already files exactly one
    -- health row, the ledger is already append-only on (collector, run_at),
    -- already merges cleanly (merge_db.py unions it), and already reaches the
    -- weekly digest and ops_status. A parallel cost table would need its own
    -- merge rule and its own join to answer any question worth asking.
    --
    -- NULL means "no model accounting was recorded" and 0 means "measured
    -- zero", and the difference matters: a structured collector and a
    -- retraction sweep call no model at all, and writing zeros for them would
    -- make a genuinely free run indistinguishable from a run whose accounting
    -- went missing.
    model             TEXT,     -- the read-through model, as configured
    gate_model        TEXT,     -- the one-word gate, '' when single-stage
    prompt_tokens     INTEGER,
    cached_tokens     INTEGER,  -- of prompt_tokens; the prefix cache's receipt
    completion_tokens INTEGER,
    cost_usd          REAL,     -- the PROVIDER's own figure, never arithmetic
    reads_bought      INTEGER,  -- full read-throughs paid for this run
    -- Rows that those read-throughs actually bought. Beside reads_bought this
    -- IS the reads-vs-rows ratio, which is deliberately not stored as a third
    -- number: a percentage rounded at write time can disagree with the two
    -- integers it came from, and then nobody knows which to believe.
    -- store.reads_to_rows_pct() is the one place it is computed.
    rows_from_reads   INTEGER,

    -- THE FUNNEL. reads_bought says what was bought; these say what was
    -- SCREENED and what was refused for budget, which is the coverage
    -- question rather than the cost one. See MIGRATIONS for why.
    candidates        INTEGER,  -- reached the classifier
    gate_calls        INTEGER,  -- one-word screens paid for
    gate_rejects      INTEGER,  -- dropped there, at ~1/40th of a read
    budget_deferred   INTEGER,  -- kept by the gate and NOT read: the gap
    PRIMARY KEY (collector, run_at)
);

-- Link rot, per SOURCE URL rather than per row (link_check.py, archive_sources.py).
--
-- WHY A SEPARATE TABLE. The promise is that every figure links to the document
-- that states it, so a source link that dies converts a sourced claim into an
-- unsourced one WITHOUT anything looking broken. That has to be recorded
-- somewhere, and it must not be recorded on the signal: a dead link is not a
-- correction, nothing about what we knew has changed, and appending a revision
-- for it would put HTTP weather into the record of what a source said.
--
-- Keyed on the URL because 15,631 current rows share 13,893 distinct source
-- URLs (and thousands of SEC rows share a handful of filing index pages), so
-- one check and one snapshot serve every row that cites the same document.
--
-- Nothing here ever deletes or edits a signal. A dead link is recorded and
-- surfaced; deciding what to do about it is a human step, on purpose.
CREATE TABLE IF NOT EXISTS source_links (
    source_url    TEXT PRIMARY KEY,

    -- Reachability, from link_check.py.
    http_status   INTEGER,        -- 0 means the request never completed
    final_url     TEXT,           -- where it landed after redirects
    final_domain  TEXT,           -- registrable domain of final_url
    state         TEXT,           -- live | walled | dead | drifted | unreachable | error | robots
    checked_at    TEXT,
    check_detail  TEXT,
    checks        INTEGER NOT NULL DEFAULT 0,

    -- Permanence, from archive_sources.py. archive_url is a Wayback permalink:
    -- a neutral third-party copy, so a reader can still reach the evidence when
    -- the publisher's own copy is gone.
    archive_url      TEXT,
    archive_state    TEXT,        -- archived | pending | unavailable
    archive_attempts INTEGER NOT NULL DEFAULT 0,
    archived_at      TEXT,
    -- Probe accounting. `archive_attempts` counts CAPTURES tried; these two
    -- count what we LEARNED, and the difference is what stops a throttled
    -- fortnight from walking a capturable document to the terminal state:
    --   archive_probes       definitive answers from the availability API
    --                        (a hit, or an explicit "no snapshot"). 0 means we
    --                        have never once been told anything about this URL.
    --   archive_blind_rounds rounds that learned nothing at all — a 429, a
    --                        timeout, a Save Page Now refusal. Never rot, never
    --                        evidence, and never grounds for going terminal.
    archive_probes       INTEGER NOT NULL DEFAULT 0,
    archive_blind_rounds INTEGER NOT NULL DEFAULT 0,
    archive_detail       TEXT,

    -- Reporting only. A rot rate that rises for ONE publisher means that
    -- publisher changed its URL scheme, which is actionable in a way that an
    -- overall percentage is not.
    source_name   TEXT,
    host          TEXT,

    -- Merge key. Both jobs write this row, so merge_db.py resolves a collision
    -- by keeping the later write wholesale. Both jobs are resumable and
    -- idempotent, so the worst a lost update costs is one cycle.
    updated_at    TEXT NOT NULL
);

-- Employer identity resolutions (pipeline/identity.py). Keyed per EMPLOYER,
-- not per signal: employers repeat and the SEC filers among them recur every
-- quarter, so this is the difference between one lookup and forty.
-- `resolved = 0` rows are negative results and are kept on purpose — a name
-- Wikidata does not know must be asked about once, not on every run.
-- Pre-publish guardrail findings (pipeline/guardrails.py).
--
-- WHY A LEDGER AND NOT A DROP. The $86bn Form D overstatement stood in public
-- for weeks because nothing asked whether a single row was implausible. The
-- answer to that is not "bin anything large": a genuine $8.6bn raise has to
-- survive, and a guardrail that silently corrects is just a different invisible
-- defect. So a finding is RECORDED, surfaced by ops_status.py [2d] and the
-- weekly digest, and blocks publishing until a person accepts or rejects it.
--
-- The state column is the whole point: an accepted finding stays accepted when
-- it fires again, so a real mega-round is reviewed once and never again, while
-- an unreviewed one keeps the job red.
CREATE TABLE IF NOT EXISTS publish_guardrails (
    check_name  TEXT NOT NULL,   -- amount | period_totals | date_span | vehicle_name
    subject     TEXT NOT NULL,   -- content_hash for a row check, a scope key otherwise
    label       TEXT,
    detail      TEXT,
    value       REAL,            -- the dollars or the size of the discrepancy
    state       TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected | resolved
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    seen        INTEGER NOT NULL DEFAULT 1,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    PRIMARY KEY (check_name, subject)
);

-- WHO ELSE SAID IT. One row per outlet that reported a funding figure we
-- already hold, written at the moment dedup throws that outlet's article away.
--
-- WHY THIS TABLE EXISTS. Corroboration was arriving and being discarded. On
-- 2026-08-01 the Anthropic $30bn round stored from one outlet at 14:25:39; at
-- 14:26:21 reuters.com and at 16:53:45 w.media arrived reporting the same round
-- and were marked `duplicate` in seen_urls, and on 2026-08-04 Anthropic's own
-- press release for that exact round arrived and was marked `duplicate` too.
-- Four independent reports of one figure, and the only thing the system kept
-- was a url and the word "duplicate" - no employer, no amount, no way to ask
-- afterwards how many outlets agreed. Meanwhile the amount guardrail was
-- holding that same row out of the product for a fifth day because a single
-- source could not distinguish it from a parse error.
--
-- Dedup is the ONLY place this can be captured. By design the second outlet's
-- article never becomes a row (that is the whole point of dedup: one event,
-- one record), so the fact that it existed is destroyed unless it is written
-- down here as it goes past.
--
-- `amount_usd` is the figure the ARRIVING outlet stated, kept separately from
-- the stored row's, so a later reader can see that the two agreed rather than
-- having to trust that they did. `host` is the registrable domain, which is
-- what makes two reports independent; the UNIQUE key is on (signal_id, host)
-- so an outlet that republishes the same round eight times counts once.
CREATE TABLE IF NOT EXISTS funding_corroborations (
    signal_id   TEXT NOT NULL,   -- the row this outlet corroborates
    host        TEXT NOT NULL,   -- registrable domain of the arriving article
    source_url  TEXT,
    source_name TEXT,
    amount_usd  INTEGER,         -- what THIS outlet stated, not what we hold
    collector   TEXT,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (signal_id, host)
);

CREATE TABLE IF NOT EXISTS employer_identity (
    company_key   TEXT PRIMARY KEY,
    company       TEXT,
    qid           TEXT,           -- Wikidata item, for auditing a bad match
    ticker        TEXT,
    cik           TEXT,
    hq_city       TEXT,
    hq_country    TEXT,
    employer_type TEXT,
    resolved      INTEGER NOT NULL DEFAULT 0,
    detail        TEXT,
    resolved_at   TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_signals_fund_usd ON signals(funding_amount_usd);
CREATE INDEX IF NOT EXISTS idx_signals_effective ON signals(effective_date);
CREATE INDEX IF NOT EXISTS idx_signals_cik      ON signals(cik);
CREATE INDEX IF NOT EXISTS idx_signals_material ON signals(materiality);
CREATE INDEX IF NOT EXISTS idx_signals_site_evt ON signals(site_event);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_hash_rev ON signals(content_hash, revision);
CREATE INDEX IF NOT EXISTS idx_links_state    ON source_links(state);
CREATE INDEX IF NOT EXISTS idx_links_checked  ON source_links(checked_at);
CREATE INDEX IF NOT EXISTS idx_links_archive  ON source_links(archive_state);
CREATE INDEX IF NOT EXISTS idx_links_host     ON source_links(host);
CREATE INDEX IF NOT EXISTS idx_guardrails_state ON publish_guardrails(state);
CREATE INDEX IF NOT EXISTS idx_corrob_signal ON funding_corroborations(signal_id);
-- Deliberately NO index on signals(source_url). It would help the GROUP BY in
-- source_links.distinct_source_urls by a millisecond or two on 15k rows, and it
-- added 1.7 MB to a database that is committed to the repo on every collect run
-- and therefore stored again in full by git each time. The wrong trade.
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
    # Added while the 2026 backfill was mid-flight. These could not wait: a
    # column added after tens of thousands of rows have landed is NULL on all
    # of them forever, because nothing re-reads an article we already paid to
    # classify. funding_amount_usd is the exception and is re-derived below
    # from the string we already hold.
    ("signals", "funding_amount_usd", "INTEGER"),
    ("signals", "funding_stage", "TEXT"),
    ("signals", "effective_date", "TEXT"),
    ("signals", "ticker", "TEXT"),
    ("signals", "cik", "TEXT"),
    ("signals", "work_mode", "TEXT"),
    ("signals", "employer_type", "TEXT"),
    ("signals", "headcount_scope", "TEXT"),
    ("signals", "materiality", "TEXT"),
    ("signals", "deal_type", "TEXT"),
    ("signals", "site_event", "TEXT"),
    # Per-run cost accounting on the health ledger. The committed database has
    # thousands of health rows already, and they stay NULL here forever: no
    # model is going to re-read a run that finished in July. That is the honest
    # shape of a column added after the fact, and the reason this went in the
    # same week the accounting was being printed rather than a month later.
    ("source_health", "model", "TEXT"),
    ("source_health", "gate_model", "TEXT"),
    ("source_health", "prompt_tokens", "INTEGER"),
    ("source_health", "cached_tokens", "INTEGER"),
    ("source_health", "completion_tokens", "INTEGER"),
    ("source_health", "cost_usd", "REAL"),
    ("source_health", "reads_bought", "INTEGER"),
    ("source_health", "rows_from_reads", "INTEGER"),
    # Archive PROBE accounting, added 2026-07-29. `archive_state` alone cannot
    # answer the question that decides whether a URL may ever go terminal:
    # "have we ever had a real answer about this document?" A `pending` row with
    # no definitive probe and a `pending` row Wayback has explicitly said it does
    # not hold look identical, and only the second one is a real gap. Without the
    # distinction a stretch of 429s reads as a growing backlog and the capture
    # budget is spent on documents archive.org already has. NULL on every row
    # stored before this column existed, which is why the reset below reads NULL
    # as "never probed" rather than as zero probes.
    ("source_links", "archive_probes", "INTEGER"),
    ("source_links", "archive_blind_rounds", "INTEGER"),
    ("source_links", "archive_detail", "TEXT"),

    # THE FUNNEL, so the cost of coverage stays measured instead of being
    # re-derived from a workflow log somebody happened to still have.
    #
    # `reads_bought` answers "what did we buy" and not "what did we DECLINE to
    # buy", and the second number is the whole coverage question. A press run
    # on 2026-07-30 gated 627 candidates, kept 249 and could read only 200, so
    # 49 stories — Hebrew, German, Serbian, Vietnamese, Korean — went unread
    # for the budget rather than for a verdict. That fact lived only in the
    # step log, and step logs expire. cost_projection.py reads these four.
    ("source_health", "candidates", "INTEGER"),       # reached the classifier
    ("source_health", "gate_calls", "INTEGER"),       # one-word screens paid for
    ("source_health", "gate_rejects", "INTEGER"),     # dropped there, cheap
    ("source_health", "budget_deferred", "INTEGER"),  # kept and NOT read: the gap
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


def backfill_funding_usd(conn: sqlite3.Connection) -> int:
    """Fill funding_amount_usd from the funding_amount string already stored.

    The only new column that gets backfilled, and only because it invents
    nothing: it re-parses text we collected, with the same deterministic parser
    new rows use. No model call, no refetch, no network.

    Idempotent: it touches only rows where the number is missing and the string
    is present, so a second run is a no-op. Rows whose string will not parse
    (non-USD currencies, 'undisclosed') stay NULL and are re-examined each run,
    which is cheap and means a parser improvement picks them up automatically.
    """
    rows = conn.execute(
        """SELECT row_id, funding_amount FROM signals
            WHERE funding_amount IS NOT NULL AND funding_amount != ''
              AND funding_amount_usd IS NULL"""
    ).fetchall()

    updates = []
    for row in rows:
        usd = vocab.parse_funding_usd(row["funding_amount"])
        if usd is not None:
            updates.append((usd, row["row_id"]))
    if updates:
        conn.executemany(
            "UPDATE signals SET funding_amount_usd = ? WHERE row_id = ?", updates
        )
    return len(updates)


def backfill_materiality(conn: sqlite3.Connection) -> int:
    """Fill materiality on rows that predate the column.

    Same contract as backfill_funding_usd, and the same reason it is safe: the
    rule is a pure function of columns already on the row, so this recomputes
    rather than invents. No model call, no refetch, no network — which is the
    only reason a column added after a 2,000-row backfill is not NULL forever.

    Idempotent: only rows where it is missing are touched.
    """
    from . import validate  # imported here: validate imports vocab, not schema

    rows = conn.execute(
        """SELECT row_id, headcount, funding_amount_usd, ticker, cik, pillar,
                  headline, city
             FROM signals WHERE materiality IS NULL OR materiality = ''"""
    ).fetchall()

    updates = [
        (
            validate.compute_materiality(
                headcount=row["headcount"],
                funding_usd=row["funding_amount_usd"],
                ticker=row["ticker"],
                cik=row["cik"],
                pillar=row["pillar"] or "",
                headline=row["headline"] or "",
                city=row["city"],
            ),
            row["row_id"],
        )
        for row in rows
    ]
    if updates:
        conn.executemany(
            "UPDATE signals SET materiality = ? WHERE row_id = ?", updates
        )
    return len(updates)


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

    # Run here rather than from a script someone has to remember. A one-off
    # migration script is a migration that stops running: the collectors, the
    # backfill and every ops tool open the database through connect(), so
    # wiring it in is the only version that cannot be skipped. After the first
    # pass it is one indexless read of a handful of rows.
    #
    # Never fatal: ops_status.py and other read-only tools also call connect(),
    # and a locked or read-only database must not take them down over a column
    # that is allowed to be NULL.
    try:
        backfill_funding_usd(conn)
        backfill_materiality(conn)
    except sqlite3.OperationalError:
        pass

    conn.commit()
    return conn

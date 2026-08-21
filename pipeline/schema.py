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

import re
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

    -- WHY THE FIGURE ON THIS ROW IS, OR IS NOT, MONEY THE EMPLOYER RAISED.
    -- 'company_raise' is the only value any public money total may sum, and
    -- every sum asks for it BY NAME. Any other value is one of the excluding
    -- deal_types and says why the row is out. NULL is a third state and means
    -- the row was never examined, which is not a pass: summing NULL as though
    -- it meant 'company_raise' is the defect this column exists to close.
    -- pipeline/money_raised.py is the one definition.
    money_basis       TEXT,

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




-- Collector status ledger (spec 16 loop 1). A collector that returns zero
-- writes 'degraded', never 'ok'.
CREATE TABLE IF NOT EXISTS source_health (
    collector    TEXT NOT NULL,
    run_at       TEXT NOT NULL,
    -- ok | degraded | running | error | skipped
    --
    -- `skipped` means the collector RAN and correctly declined to do the paid
    -- part of its work. Nothing broke, so it is not `degraded` or `error`;
    -- nothing was collected, so it is not `ok`. It exists because a run that
    -- files NO row is indistinguishable from a run that died, and the
    -- staleness leash in staleness.py cannot tell a binding budget from a
    -- dead job without a row to read.
    status       TEXT NOT NULL,
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

    -- WHOSE SPEND WAS IT. cost_usd made the month's total measurable; this
    -- makes it SPLITTABLE. 'committed' is scheduled work that keeps the
    -- tracker current; 'discretionary' is hand-dispatched catch-up (the
    -- backfill walkers, ab_models). In August 2026 those were indistinguishable
    -- here, the catch-up family spent 88% of the month in 2.5 days, and the
    -- collectors ran degraded for the nine days after with no query able to say
    -- why. NULL predates the column and counts as committed. budget.py owns the
    -- rule; store.report_health writes it on every row, priced or not.
    run_kind          TEXT,
    PRIMARY KEY (collector, run_at)
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
CREATE INDEX IF NOT EXISTS idx_guardrails_state ON publish_guardrails(state);
CREATE INDEX IF NOT EXISTS idx_corrob_signal ON funding_corroborations(signal_id);
-- Deliberately NO index on signals(source_url). It would help the GROUP BY in
-- source_links.distinct_source_urls by a millisecond or two on 15k rows, and it
-- added 1.7 MB to a database that is committed to the repo on every collect run
-- and therefore stored again in full by git each time. The wrong trade.
"""


# --- the second committed file ---------------------------------------------
#
# WHY THERE ARE TWO FILES. GitHub refuses any single file over 100 MiB in a
# push, and the limit is PER FILE, not per repository or per push. On
# 2026-08-20 `data/talent_intel.db` was 78.8 MiB and growing 676 KB/day
# (measured over the fortnight to 08-20, not estimated), which is 32 days from
# a repository that stops accepting commits. Everything about this system
# assumes the database is committed: `git push` is the compare-and-swap that
# makes merge_db.py safe, `git show <sha>:data/talent_intel.db` is the restore
# path, and backup_check.py opens the committed blob. Moving the database out
# of git to a release asset or to LFS would buy space by giving all three of
# those up, so instead the file is SPLIT and both halves stay committed.
#
# What moved is everything that is a CACHE or a LEDGER rather than the product:
#
#   seen_urls          the never-pay-twice URL cache. 15.2 MiB with its
#                      autoindex, 31 percent of daily growth, and the biggest
#                      single thing here that is not a signal.
#   source_links       the link-rot / archive ledger. 3.7 MiB, 14 percent of
#                      growth, and re-derivable by re-running link_check.
#   employer_identity  the Wikidata resolution cache, re-derivable by paying
#                      for the lookups again.
#
# `signals` stays in the product file with source_health and publish_guardrails,
# which are small and are what every reader means by "the data".
#
# The two files are ONE database at runtime: connect() ATTACHes the cache as
# `cache`, and SQLite resolves an unqualified table name across attached
# schemas, so `SELECT ... FROM seen_urls` needs no change anywhere. A commit
# spanning both files is atomic (SQLite writes a master journal), so a run
# cannot store a signal without recording its URL as seen.
#
# THE ONE RULE THIS ADDS: every workflow that commits the database must commit
# BOTH files. A run that pushes only the product file throws away the URL cache
# and re-pays the LLM for stories it already read.
# tests/test_workflows.py enforces it; do not weaken that test.

CACHE_SCHEMA = "cache"

#: Tables that live in the cache file rather than the product file.
CACHE_TABLE_NAMES = ("seen_urls", "source_links", "employer_identity")

CACHE_TABLES = """
-- Every URL we have ever looked at, so we never pay an LLM for it twice.
-- Spec 4 rule 2: this removed ~60% of daily extraction volume on the sibling.
CREATE TABLE IF NOT EXISTS cache.seen_urls (
    url         TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    collector   TEXT NOT NULL,
    outcome     TEXT NOT NULL   -- stored | rejected | duplicate
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
CREATE TABLE IF NOT EXISTS cache.source_links (
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


CREATE TABLE IF NOT EXISTS cache.employer_identity (
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

CACHE_INDEXES = """
CREATE INDEX IF NOT EXISTS cache.idx_links_state    ON source_links(state);
CREATE INDEX IF NOT EXISTS cache.idx_links_checked  ON source_links(checked_at);
CREATE INDEX IF NOT EXISTS cache.idx_links_archive  ON source_links(archive_state);
CREATE INDEX IF NOT EXISTS cache.idx_links_host     ON source_links(host);
"""


def cache_path_for(db_path: Path | str) -> Path:
    """The cache file that belongs to `db_path`.

    Derived rather than configured, so a test database, a backfill's scratch
    copy and the committed file all carry their own cache and nothing can point
    two databases at one cache by accident.
    """
    p = Path(db_path)
    return p.with_name(p.stem + "_cache" + p.suffix)


CACHE_DB_PATH = cache_path_for(DB_PATH)



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
    # NULL on every row stored before this column existed, and that is the
    # honest reading of it: never examined. correct_money_basis.py is the pass
    # that judges them, and it judges them by calling the same classifier the
    # write path calls rather than from a list somebody typed.
    ("signals", "money_basis", "TEXT"),
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

    # WHOSE SPEND WAS IT. cost_usd made the month's total measurable; this makes
    # it SPLITTABLE, which is the question August actually posed. Backfill
    # walkers spent 88% of that month in 2.5 days and the scheduled collectors
    # ran degraded for the nine days after, and no query over this table could
    # have said so, because a walker's row and a collector's row are identical.
    # `committed` (scheduled, keeps the tracker current) or `discretionary`
    # (dispatch-only catch-up). NULL predates the column and counts as
    # committed — see budget.ledger_spend on why not a third bucket.
    ("source_health", "run_kind", "TEXT"),
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


def attach_cache(conn: sqlite3.Connection, db_path: Path | str) -> Path:
    """ATTACH the cache file belonging to `db_path` as `cache`. Returns its path.

    Idempotent: attaching twice raises, so an already-attached `cache` is left
    alone. That matters because read-only callers build their own connection
    and hand it here.
    """
    cache = cache_path_for(db_path)
    already = {row[1] for row in conn.execute("PRAGMA database_list")}
    if CACHE_SCHEMA not in already:
        conn.execute("ATTACH DATABASE ? AS %s" % CACHE_SCHEMA, (str(cache),))
    return cache


def connect_ro(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Read-only connection with the cache file attached read-only.

    For the tools that deliberately open `mode=ro` rather than going through
    connect(): ops_status, the digest, the analysis walkers. They must not
    create a table, must not migrate and must not be able to lock a database a
    collect run is writing — but they DO read `source_links` and `seen_urls`,
    which now live in the other file.

    A missing cache file is NOT silently tolerated. `mode=ro` refuses to create
    it, so the ATTACH raises and the caller fails loudly, which is the point:
    reading zero link-rot rows because the file was never fetched looks exactly
    like a clean link-rot report.
    """
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cache = cache_path_for(path)
    conn.execute(
        "ATTACH DATABASE ? AS %s" % CACHE_SCHEMA,
        (f"file:{cache}?mode=ro",),
    )
    return conn


class CacheMoveFailed(RuntimeError):
    """A row was about to be lost moving a table between the two files."""


def _move_legacy_cache_tables(conn: sqlite3.Connection) -> list[str]:
    """Move any cache table still sitting in the product file into the cache.

    THIS IS NOT ONE-OFF CLEANUP, it is the thing that makes the split safe to
    land on a repository with two dozen live branches. SQLite resolves an
    unqualified table name against `main` FIRST, so a `seen_urls` in the
    product file SHADOWS the real one in the cache file — silently, and reading
    an empty cache is indistinguishable from a cache that legitimately holds
    nothing. That is exactly how a run re-pays the LLM for every story it
    already read.

    Two ways it happens, and they need different handling:

    THE MIGRATION. A database written before the split holds the real rows in
    main and has no cache file at all. Those rows are moved by REBUILDING the
    table in the cache file from the legacy table's OWN `CREATE` statement and
    copying every row across unchanged.

    Rebuilding from the legacy DDL rather than from CACHE_TABLES is not
    fastidiousness, it is the fix for a measured row loss. The two shapes are
    not the same: `ALTER TABLE ADD COLUMN archive_probes INTEGER` (MIGRATIONS)
    is nullable, `CREATE TABLE ... archive_probes INTEGER NOT NULL DEFAULT 0`
    (CACHE_TABLES) is not, and 1,232 of 6,496 `source_links` rows on
    2026-08-20 held NULL there. Copied into the stricter table with
    `INSERT OR IGNORE`, every one of them was skipped in silence and the move
    reported success. Coercing them to 0 would have been just as wrong in a
    quieter way: NULL means "never probed" and 0 means "probed and told
    nothing", and archive_sources.py reads the difference to decide whether a
    document may go terminal.

    THE SHADOW. A branch still running the pre-split schema.py puts an EMPTY
    `seen_urls` back into main. Here the cache is authoritative — it was
    written by a checkout that knew about it — so rows are folded in with
    `INSERT OR IGNORE` and a key the cache already holds is kept.

    Either way the row count is CHECKED before `main` is dropped, and a
    shortfall raises rather than logs. A move that loses rows is the failure
    this whole file is arranged around, and the last time it happened here it
    took 9,572 signals and no run went red.

    It does NOT vacuum. Dropping a 15 MiB table returns its pages to the
    freelist and leaves the file the same size on disk, so the space is only
    reclaimed by split_cache_db.py, which is run once by a human and commits
    the result. A VACUUM here would rewrite an 80 MiB file on every connect().
    """
    moved = []
    in_main = {row[0] for row in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}
    for table in CACHE_TABLE_NAMES:
        if table not in in_main:
            continue

        source_rows = conn.execute(
            f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        exists_in_cache = conn.execute(
            f"SELECT COUNT(*) FROM {CACHE_SCHEMA}.sqlite_master "
            f" WHERE type='table' AND name=?", (table,)).fetchone()[0]
        held = conn.execute(
            f"SELECT COUNT(*) FROM {CACHE_SCHEMA}.{table}").fetchone()[0] \
            if exists_in_cache else 0

        if held == 0:
            # The migration. Reproduce the legacy table verbatim in the cache
            # file, so nothing can be coerced, widened or refused on the way.
            legacy_sql = conn.execute(
                "SELECT sql FROM main.sqlite_master "
                " WHERE type='table' AND name=?", (table,)).fetchone()[0]
            if exists_in_cache:
                conn.execute(f"DROP TABLE {CACHE_SCHEMA}.{table}")
            conn.execute(_qualify_create(legacy_sql, table))
            conn.execute(
                f"INSERT INTO {CACHE_SCHEMA}.{table} SELECT * FROM main.{table}")
            # The legacy shape is authoritative about the rows, but it can be
            # missing a column the current CREATE declares — a database old
            # enough to predate one. Reconciled by ALTER, which keeps the
            # copied rows and gives the new column its declared default, so
            # CACHE_INDEXES below cannot fail on a column that is not there.
            _add_missing_cache_columns(conn, table)
            expected = source_rows
        else:
            # The shadow. The cache wins every key it already has; everything
            # else is folded in.
            columns = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            cache_columns = {r[1] for r in conn.execute(
                f"PRAGMA {CACHE_SCHEMA}.table_info({table})")}
            shared = [c for c in columns if c in cache_columns]
            names = ", ".join(f'"{c}"' for c in shared)
            key = [r[1] for r in sorted(
                (r for r in conn.execute(f"PRAGMA {CACHE_SCHEMA}.table_info({table})")
                 if r[5]), key=lambda r: r[5])]
            on = " AND ".join(f"c.{k} = m.{k}" for k in key)
            collisions = conn.execute(
                f"SELECT COUNT(*) FROM main.{table} m "
                f" JOIN {CACHE_SCHEMA}.{table} c ON {on}").fetchone()[0] if key else 0
            conn.execute(
                f"INSERT OR IGNORE INTO {CACHE_SCHEMA}.{table} ({names}) "
                f"SELECT {names} FROM main.{table}")
            expected = held + source_rows - collisions

        landed = conn.execute(
            f"SELECT COUNT(*) FROM {CACHE_SCHEMA}.{table}").fetchone()[0]
        if landed != expected:
            conn.rollback()
            raise CacheMoveFailed(
                f"moving {table} into the cache file would have lost "
                f"{expected - landed} row(s): {source_rows} in the product "
                f"file plus {held} already held should be {expected}, but "
                f"{landed} landed. Nothing was dropped; the product file is "
                f"unchanged.")

        conn.execute(f"DROP TABLE main.{table}")
        moved.append(table)
    if moved:
        conn.commit()
    return moved


def _add_missing_cache_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Bring a table rebuilt from a legacy CREATE up to the current declaration.

    The reference is CACHE_TABLES itself, built in a throwaway attached schema
    and read back, rather than a hand-kept list of columns. A list would be a
    second declaration of the same thing and would drift the first time a
    column was added — which is precisely the class of bug MIGRATIONS exists to
    document.
    """
    conn.execute("ATTACH DATABASE ':memory:' AS _ref")
    try:
        conn.executescript(CACHE_TABLES.replace(f"{CACHE_SCHEMA}.", "_ref."))
        want = [(r[1], r[2], r[4]) for r in
                conn.execute(f"PRAGMA _ref.table_info({table})")]
        have = {r[1] for r in conn.execute(f"PRAGMA {CACHE_SCHEMA}.table_info({table})")}
        added = []
        for name, decl, default in want:
            if name in have:
                continue
            # Always nullable, never NOT NULL, even where the CREATE says so.
            # SQLite cannot ALTER a NOT NULL column onto a populated table
            # without a default, and adopting one would hand every existing row
            # a value nothing measured — which is the same mistake as coercing
            # a NULL `archive_probes` to 0. This mirrors MIGRATIONS, which adds
            # every column nullable for exactly that reason.
            clause = f"{name} {decl}"
            if default is not None:
                clause += f" DEFAULT {default}"
            conn.execute(f"ALTER TABLE {CACHE_SCHEMA}.{table} ADD COLUMN {clause}")
            added.append(name)
        return added
    finally:
        conn.execute("DETACH DATABASE _ref")


def _qualify_create(sql: str, table: str) -> str:
    """Point a `CREATE TABLE` statement at the cache schema.

    sqlite_master stores the statement exactly as it was written, so the name
    may or may not be quoted and may or may not carry IF NOT EXISTS. Only the
    table name is touched; every column declaration is left byte for byte,
    which is the entire point of rebuilding from this rather than from
    CACHE_TABLES.
    """
    pattern = re.compile(
        r'(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)(["\'`\[]?)'
        + re.escape(table) + r'(["\'`\]]?)',
        re.IGNORECASE)
    qualified, count = pattern.subn(
        lambda m: f"{m.group(1)}{CACHE_SCHEMA}.{m.group(2)}{table}{m.group(3)}",
        sql, count=1)
    if count != 1:
        raise CacheMoveFailed(
            f"could not read the stored CREATE statement for {table}: {sql!r}")
    return qualified


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # The cache file is created on demand exactly like the product file is: a
    # fresh checkout, a test tmp_path and a backfill's scratch copy all have to
    # work without a migration step somebody remembers to run.
    attach_cache(conn, path)

    # Order matters, and the first two steps are new:
    #   1. create the product tables, then the cache tables, so the destination
    #      of the move below exists before the move runs;
    #   2. move any shadowing copy out of main BEFORE _migrate and INDEXES,
    #      both of which name their tables unqualified and would otherwise
    #      migrate and index the shadow instead of the real table;
    #   3. add missing columns, then indexes — an index on a not-yet-added
    #      column is what broke this the first time.
    conn.executescript(TABLES)
    conn.executescript(CACHE_TABLES)
    _move_legacy_cache_tables(conn)
    _migrate(conn)
    conn.executescript(INDEXES)
    conn.executescript(CACHE_INDEXES)

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

"""The source-link ledger: one row per distinct source URL, never per signal.

Why this exists
---------------
The product's promise is that every update links to the filing or report behind
it, and that no figure appears unless the source states it. A source link that
dies does not merely inconvenience a reader: it silently converts a sourced
claim into an unsourced one, and nothing on the page looks different. With 575
publisher feeds across 139 countries in the catalogue, many of them small
national outlets, that is a certainty rather than a risk.

So reachability and permanence are recorded here, keyed on the URL:

  link_check.py       records HTTP status, the final URL after redirects, and
                      the check date. It never touches a signal.
  archive_sources.py  records a Wayback permalink so the evidence survives the
                      publisher's copy.

Two rules this module exists to enforce
---------------------------------------
1. **A dead link never deletes or alters a row.** Nothing here writes to
   `signals` except `project_archive_urls()`, which fills the `archive_url`
   provenance column and touches no claim, no figure and no source. Deciding
   what to do about a dead link is a separate, human-visible step: `state` is
   recorded, `ops_status.py` surfaces it, the weekly digest mails it.
2. **Keyed on the URL, not the row.** 15,631 current signals share 13,893
   distinct source URLs, and the SEC collectors put thousands of rows behind a
   handful of filing index pages. One check and one snapshot serve all of them.

Deliberately NOT a WordPress broken-link-checker plugin
-------------------------------------------------------
Those plugins crawl POST CONTENT. Our source links live in the custom
`wp_tit_signals` table and in this repo's SQLite, never in a post body, so such
a plugin would scan a handful of prose links, find them all healthy, and report
a green badge over an entirely unchecked corpus. That is precisely the
false-healthy failure this project keeps finding, and it would be worse here
than no checker at all, because it comes with a reassuring number attached.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

# States link_check.py may record. Only two of them are rot:
#
#   dead      404/410. The document is gone. Visibly broken to a reader.
#   drifted   the final domain is not the one we stored. The single most
#             dangerous case and the reason this is not just a status check:
#             botswanaguardian.co.bw now redirects to a betting site whose feed
#             verified perfectly green. A cited article that quietly becomes a
#             casino is worse than a 404, because a 404 announces itself.
#
# The rest are not rot and must never be counted as it:
#
#   live         2xx.
#   walled       401/403/405/429. A bot wall, not a dead document: a human with
#                a browser still reaches it. Counting these as rot would report
#                every paywalled publisher as broken.
#   unreachable  the request never completed (DNS, TLS, timeout). Could be the
#                publisher, could be the runner's network, so it is reported
#                and retried, never called rot on one observation.
#   error        5xx. Shared hosting has bad afternoons.
#   robots       the publisher's robots.txt disallows this path. We do not
#                fetch it, and we do not get to call it broken either.
ROT_STATES = frozenset({"dead", "drifted"})
REACHABLE_STATES = frozenset({"live", "walled"})
ALL_STATES = ROT_STATES | REACHABLE_STATES | {"unreachable", "error", "robots"}

ARCHIVE_STATES = frozenset({"archived", "pending", "unavailable"})

# After this many failed archive rounds a URL is recorded 'unavailable' rather
# than retried forever. Wayback genuinely cannot capture some pages (hard bot
# walls, login gates), and a queue that never drains hides the ones it could.
MAX_ARCHIVE_ATTEMPTS = 5

# 'unavailable' is TERMINAL: archive_candidates drops the URL and only a
# hand-written UPDATE puts it back. So it may only ever be reached from
# EVIDENCE, and this is the whole of that rule: at least one round in which
# archive.org actually answered us and said it holds no snapshot.
#
# The bug this constant exists to make impossible has now shipped twice in this
# family, in two different guises, and both times the run stayed green:
#
#   1. A 429 from the availability API was read as "no snapshot exists", so a
#      throttled afternoon manufactured a gap and then spent the capture budget
#      on it. Fixed in check_availability on 2026-07-30.
#   2. A 429 from Save Page Now still SPENT one of the five attempts, so five
#      throttled nights walked a perfectly capturable document to terminal
#      'unavailable' having never once been told it was uncapturable. Fixed
#      2026-07-29 (this file) by counting blind rounds apart from attempts.
#
# Both are the same error: treating "we did not learn anything" as a finding.
# One probe is a low bar deliberately — it is a floor against blindness, not a
# confidence threshold. The attempts ceiling above is what bounds the retries.
MIN_PROBES_BEFORE_TERMINAL = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- reading ---------------------------------------------------------------

def distinct_source_urls(conn: sqlite3.Connection, *, collector: str | None = None,
                         limit: int | None = None) -> list[dict]:
    """Every distinct source URL on a current signal, newest capture first.

    Newest first on purpose, and it is the same ordering that makes the archiver
    behave like an ingest-time job: a URL stored an hour ago is at the head of
    the queue, so arming the workflow after a collect run archives what that run
    just stored, while everything older backfills from behind it. No change to
    the ingest write path is needed to get that, which is the point — the write
    path is where this project's expensive bugs live.
    """
    sql = """SELECT source_url,
                    MAX(source_name) AS source_name,
                    MAX(captured_at) AS newest_capture,
                    COUNT(*)         AS rows_citing
               FROM signals
              WHERE is_current = 1
                AND source_url IS NOT NULL AND source_url != ''"""
    params: list = []
    # A comma-separated list, because the interesting population is usually
    # "the publisher collectors" rather than any single one. Measured
    # 2026-07-29: 29% of publisher URLs were already in Wayback against 3% of
    # SEC and GOV.UK ones, and it is the publisher long tail that actually rots
    # while EDGAR keeps its filings forever. Being able to point a run at that
    # tail is the difference between a useful nightly job and one that spends
    # its whole capture budget preserving documents a government already
    # preserves.
    names = [c.strip() for c in str(collector).split(",") if c.strip()] if collector else []
    if names:
        sql += f" AND collector IN ({', '.join('?' for _ in names)})"
        params.extend(names)
    sql += " GROUP BY source_url ORDER BY newest_capture DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, params)]


def check_candidates(conn: sqlite3.Connection, *, limit: int,
                     collector: str | None = None,
                     recheck_after_days: int = 30,
                     shuffle: bool = False) -> list[dict]:
    """URLs due a reachability check: never checked first, then oldest check.

    A URL already known dead is still re-checked on the normal rotation. Outlets
    restore articles, and a checker that stops looking at everything it once
    called dead can only ever report a rot rate that climbs.

    `shuffle` is for MEASUREMENT rather than for working the queue. The default
    order is newest-captured first, which is right for keeping up but wrong for
    answering "what fraction of what we cite is dead": it would sample whatever
    the last collector happened to store. A shuffled sample of the whole corpus
    is the honest answer to that question, and it is the number the owner asked
    for.
    """
    # 0 means "everything is due", which is how a full re-sweep is forced. It is
    # a distinct case rather than an arithmetic accident: timestamps are stored
    # to the second, so a cutoff of `now` would exclude a URL checked in the
    # current second and the knob would look broken exactly when it is used.
    cutoff = None if recheck_after_days <= 0 else (
        datetime.now(timezone.utc)
        - timedelta(days=recheck_after_days)).isoformat(timespec="seconds")
    rows = distinct_source_urls(conn, collector=collector)
    known = {r["source_url"]: dict(r) for r in conn.execute(
        "SELECT source_url, checked_at, state FROM source_links")}

    never, due = [], []
    for row in rows:
        seen = known.get(row["source_url"])
        if not seen or not seen.get("checked_at"):
            never.append(row)
        elif cutoff is None or seen["checked_at"] < cutoff:
            row["last_state"] = seen.get("state")
            due.append(row)
    # Oldest check first among the due ones, so the rotation is fair.
    due.sort(key=lambda r: known[r["source_url"]]["checked_at"])
    ordered = never + due
    if shuffle:
        import random
        random.shuffle(ordered)
    return ordered[:limit]


def archive_candidates(conn: sqlite3.Connection, *, limit: int,
                       collector: str | None = None) -> list[dict]:
    """URLs with no usable snapshot yet, NEVER-ANSWERED first, then newest.

    Resumable by construction: a URL drops out of this list the moment it is
    'archived' or 'unavailable', and a brand new row's URL appears in it
    automatically. Running the job daily therefore guarantees forward coverage
    with no bookkeeping of its own.

    THE ORDERING, which is the part that decides whether coverage can climb
    -----------------------------------------------------------------------
    Two tiers, and the tiering is what keeps `pending` from becoming terminal by
    accident. `pending` has always been re-examined — the sibling's defect, where
    a pending URL never re-entered its candidate list and 3,965 of them sat
    unreachable, does not exist in this function. But re-examined is not the same
    as REACHED: `limit` truncates this list, and a strict newest-first order
    means a URL that has never had an answer from archive.org sinks a little
    further every time a collect run stores something newer. At 12,970 distinct
    URLs and a 600-row window that is the same outcome by a slower route.

    So a URL with no definitive probe sorts ahead of one that has had a real
    answer and is merely awaiting a capture. Every brand-new URL has zero
    probes, so the ingest-time property the module docstring defends is
    preserved exactly: within tier 1 the order is still newest-capture first.
    What changes is that the never-answered tail rides in the same tier as the
    new rows instead of behind every one of them.

    `archive_probes` is NULL for every row written before the column existed,
    and NULL reads as 0 here on purpose: those rows genuinely predate any record
    of what we were told, so treating them as never-answered is the honest
    reading and costs one free availability call each.

    TIER 2 IS LEAST-RECENTLY-EXAMINED FIRST, and until 2026-09-02 it was not.
    A probed URL has already had the ingest-time pass that newest-first exists
    for, and every examined miss is written back `pending` with a fresh
    `updated_at`, so newest-capture order inside tier 2 made the same 600
    newest misses the head of the list on every run: examined three times a
    day, re-stamped three times a day, and the 1,844 older in-scope URLs behind
    them never re-entered the window. Measured on the committed ledger that
    day: 21 runs x 600 = 12,600 examinations in the 7-day promise window
    against a 2,653-URL in-scope queue, capacity 4.7x the queue, and the
    promise still broken for 1,844 of them. The capacity arithmetic in
    build_archive_promise.py was right and could not see it, because it counts
    examinations and assumed they were spread. Ordering tier 2 by the ledger's
    own `updated_at` (the same clock archive_recheck_overdue judges by) is what
    makes the window a rotation rather than a fixed head. A URL with no ledger
    row at all still sorts into tier 1.
    """
    rows = distinct_source_urls(conn, collector=collector)
    known = {r["source_url"]: dict(r) for r in conn.execute(
        "SELECT source_url, archive_state, archive_attempts, archive_probes, "
        "       archive_blind_rounds, updated_at FROM source_links")}
    tier_unprobed, tier_probed = [], []
    for row in rows:
        seen = known.get(row["source_url"]) or {}
        state = seen.get("archive_state")
        if state == "archived" or state == "unavailable":
            continue
        row["archive_attempts"] = int(seen.get("archive_attempts") or 0)
        row["archive_probes"] = int(seen.get("archive_probes") or 0)
        row["archive_blind_rounds"] = int(seen.get("archive_blind_rounds") or 0)
        row["last_examined"] = seen.get("updated_at") or ""
        (tier_unprobed if row["archive_probes"] == 0 else tier_probed).append(row)
    # Stable sort: two URLs examined in the same second keep newest-capture
    # order between them, which is the tier 1 property and harmless here.
    tier_probed.sort(key=lambda r: r["last_examined"])
    return (tier_unprobed + tier_probed)[:limit]


def archive_gap(conn: sqlite3.Connection) -> dict:
    """How the un-archived population splits, which the percentage cannot say.

    `archive_pct` climbing slowly is the design (Save Page Now is rate-limited
    and a backfill takes about a week). `archive_pct` climbing slowly because
    nothing can get an answer out of archive.org looks identical from outside,
    and that is what these counts separate:

      never_probed   we have never once been told anything about this URL. Not
                     a gap in Wayback: a gap in what we know.
      probed_absent  archive.org answered and said it holds no snapshot. THIS is
                     the real capture queue and the only population a capture
                     budget should be sized against.
      blind_recently at least one round on this URL learned nothing.
      terminal_blind recorded 'unavailable' without a single definitive probe —
                     a document dropped from the queue forever on the strength
                     of a throttle. Must always be zero; reset_blinded_terminal
                     is how it gets there.
    """
    def count(where: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM source_links WHERE {where}").fetchone()[0]

    unarchived = ("IFNULL(archive_state, 'pending') != 'archived' "
                  "AND IFNULL(archive_state, 'pending') != 'unavailable'")
    total = conn.execute(
        "SELECT COUNT(DISTINCT source_url) FROM signals WHERE is_current = 1"
    ).fetchone()[0]
    ledger = count("1=1")
    return {
        # URLs on a current signal that have no ledger row at all have never
        # been probed either, so they belong in the same bucket as a pending row
        # with no probe. Counting only the ledger would understate it by 12,700.
        "never_probed": (total - ledger) + count(
            f"{unarchived} AND IFNULL(archive_probes, 0) = 0"),
        "probed_absent": count(f"{unarchived} AND IFNULL(archive_probes, 0) > 0"),
        "blind_recently": count(
            f"{unarchived} AND IFNULL(archive_blind_rounds, 0) > 0"),
        "terminal_blind": count(
            "archive_state = 'unavailable' AND IFNULL(archive_probes, 0) < ?"
            .replace("?", str(MIN_PROBES_BEFORE_TERMINAL))),
    }


def reset_blinded_terminal(conn: sqlite3.Connection, *,
                           dry_run: bool = False) -> list[str]:
    """Put back every URL that reached 'unavailable' without a real negative.

    A one-time repair by intent and an idempotent one by construction: after the
    guard in `classify_archive_outcome` no new row can qualify, so a later run
    finds nothing and says so. It is kept rather than deleted because the two
    ways into this state (the availability 429, the Save Page Now 429) both
    shipped as green runs, and a third route would need exactly this again.

    Returns the URLs it moved (or would move). Never touches `signals`, never
    touches a snapshot we already hold, and cannot reach a claim.
    """
    urls = [r[0] for r in conn.execute(
        "SELECT source_url FROM source_links "
        " WHERE archive_state = 'unavailable' "
        "   AND IFNULL(archive_probes, 0) < ? "
        "   AND IFNULL(archive_url, '') = ''",
        (MIN_PROBES_BEFORE_TERMINAL,))]
    if urls and not dry_run:
        conn.executemany(
            "UPDATE source_links "
            "   SET archive_state = 'pending', archive_attempts = 0, "
            "       archive_detail = 'reset: went terminal with no definitive "
            "probe', updated_at = ? "
            " WHERE source_url = ?",
            [(_now(), url) for url in urls])
    return urls


# --- writing ---------------------------------------------------------------

_CHECK_COLUMNS = ("http_status", "final_url", "final_domain", "state",
                  "checked_at", "check_detail")
_ARCHIVE_COLUMNS = ("archive_url", "archive_state", "archive_attempts", "archived_at")


def _upsert(conn: sqlite3.Connection, url: str, values: dict) -> None:
    """Insert or update one URL's ledger row. Never raises on a repeat."""
    payload = dict(values)
    payload["source_url"] = url
    payload["updated_at"] = _now()
    columns = list(payload)
    assignments = ", ".join(f"{c} = excluded.{c}" for c in columns
                            if c != "source_url")
    conn.execute(
        f"INSERT INTO source_links ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(source_url) DO UPDATE SET {assignments}",
        tuple(payload.values()),
    )


def record_check(conn: sqlite3.Connection, url: str, *, state: str,
                 http_status: int | None = None, final_url: str = "",
                 final_domain: str = "", detail: str = "",
                 source_name: str = "", host: str = "") -> None:
    """Record what a reachability check observed. Writes nothing else, ever."""
    if state not in ALL_STATES:
        raise ValueError(f"unknown link state {state!r}")
    previous = conn.execute(
        "SELECT checks FROM source_links WHERE source_url = ?", (url,)).fetchone()
    _upsert(conn, url, {
        "http_status": http_status,
        "final_url": final_url or "",
        "final_domain": final_domain or "",
        "state": state,
        "checked_at": _now(),
        "check_detail": detail[:400],
        "checks": int((previous["checks"] if previous else 0) or 0) + 1,
        "source_name": source_name or None,
        "host": host or None,
    })


def record_archive(conn: sqlite3.Connection, url: str, *, state: str,
                   archive_url: str = "", attempts: int = 0,
                   source_name: str = "", host: str = "",
                   probes: int | None = None, blind_rounds: int | None = None,
                   detail: str = "") -> None:
    """Record the outcome of one archiving round for one URL.

    `probes` and `blind_rounds` are the round's running totals for this URL, not
    increments, so a caller that recomputes them from the row it read stays
    correct under the merge (merge_db keeps the later write wholesale) and a
    caller that does not pass them leaves the counters alone.
    """
    if state not in ARCHIVE_STATES:
        raise ValueError(f"unknown archive state {state!r}")
    values = {
        "archive_url": archive_url or None,
        "archive_state": state,
        "archive_attempts": attempts,
        "archived_at": _now() if state == "archived" else None,
        "source_name": source_name or None,
        "host": host or None,
    }
    if probes is not None:
        values["archive_probes"] = int(probes)
    if blind_rounds is not None:
        values["archive_blind_rounds"] = int(blind_rounds)
    if detail:
        values["archive_detail"] = detail[:400]
    if state != "archived":
        # Never blank a permalink we already hold because a later round failed.
        values.pop("archive_url")
        values.pop("archived_at")
    _upsert(conn, url, values)


def classify_archive_outcome(availability_url: str | None, save_url: str | None,
                             attempts: int, probes: int = 1) -> tuple[str, str]:
    """The status to record for one URL from one round's results.

    Pure, so the decision is tested without a network. 'archived' when either
    pass found a snapshot; 'unavailable' once the attempts are spent AND
    archive.org has actually told us at least once that it holds nothing;
    'pending' otherwise, which is a retry on a later run.

    `probes` defaults to 1 so an existing caller keeps the old behaviour rather
    than silently never going terminal — a queue that can never drain is the
    other failure this function sits between. Every caller in this repo passes
    it; the default exists for the reader, and the test table pins both ends.
    """
    for candidate in (availability_url, save_url):
        if candidate and str(candidate).lower().startswith("http"):
            return "archived", str(candidate)
    if attempts >= MAX_ARCHIVE_ATTEMPTS and probes >= MIN_PROBES_BEFORE_TERMINAL:
        return "unavailable", ""
    return "pending", ""


def project_archive_urls(conn: sqlite3.Connection) -> int:
    """Copy snapshot permalinks from the ledger onto the signals that cite them.

    The ONE write this module makes to `signals`, and it is deliberately narrow:
    `archive_url` is provenance we looked up, in the same class as ticker and
    cik, not a claim any source made. No headline, figure, date, country or
    source_url can be reached from here, so a bug in archiving can leave a row
    without a fallback link and can never alter what a source said.

    It is an UPDATE rather than a revision for the same reason
    backfill_funding_usd is: nothing about what we knew has changed, so
    appending a revision would put HTTP weather into the record.

    Idempotent, and cheap after the first pass: only rows whose archive_url
    differs are touched. If a push race sends the run through merge_db.py the
    projection can be dropped (merge_db keys signals on content_hash+revision
    and skips rows it already has); the ledger itself merges, so the next run
    re-applies it. That is why this is re-derived every run rather than once.
    """
    return conn.execute(
        """UPDATE signals
              SET archive_url = (SELECT l.archive_url FROM source_links l
                                  WHERE l.source_url = signals.source_url)
            WHERE EXISTS (SELECT 1 FROM source_links l
                           WHERE l.source_url = signals.source_url
                             AND l.archive_url IS NOT NULL AND l.archive_url != ''
                             AND IFNULL(signals.archive_url, '') != l.archive_url)"""
    ).rowcount


# --- reporting -------------------------------------------------------------

def rot_summary(conn: sqlite3.Connection) -> dict:
    """The numbers ops_status.py and the health digest read.

    `rot_pct` is over CHECKED urls only. Reporting it over the whole corpus
    would let the number fall simply because the checker got slower, which is
    the sort of metric that improves while the thing it measures does not.
    """
    states = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) n FROM source_links WHERE state IS NOT NULL GROUP BY state")}
    checked = sum(states.values())
    rot = sum(n for s, n in states.items() if s in ROT_STATES)
    archive = {r["archive_state"]: r["n"] for r in conn.execute(
        "SELECT archive_state, COUNT(*) n FROM source_links "
        " WHERE archive_state IS NOT NULL GROUP BY archive_state")}
    total = conn.execute(
        "SELECT COUNT(DISTINCT source_url) FROM signals WHERE is_current = 1"
    ).fetchone()[0]
    return {
        "distinct_source_urls": total,
        "checked": checked,
        "states": states,
        "rot": rot,
        "rot_pct": round(100.0 * rot / checked, 1) if checked else 0.0,
        "archived": archive.get("archived", 0),
        "archive_pending": archive.get("pending", 0),
        "archive_unavailable": archive.get("unavailable", 0),
        "archive_pct": (round(100.0 * archive.get("archived", 0) / total, 1)
                        if total else 0.0),
    }


#: Fallback if the workflow cannot be read. Kept in step with the shell default
#: in .github/workflows/archive-sources.yml by scheduled_archive_scope's test.
DEFAULT_ARCHIVE_SCOPE = ("national_press", "google_news", "gdelt", "ats_boards")

#: THE PUBLIC RE-CHECK PROMISE, IN DAYS. The listing surfaces print, on every
#: in-scope row that has no snapshot yet, "No archive snapshot yet. We re-check
#: weekly; next check by <date>". That sentence is a commitment, and this
#: constant is the single definition of it: the plugin's shipped
#: data/archive_promise.json is generated from it (build_archive_promise.py),
#: ops_status.py [2c] goes RED when any in-scope unarchived URL has not been
#: re-attempted within it, and the test suite refuses a schedule that cannot
#: meet it. Change it HERE or nowhere; a second copy is how the page ends up
#: promising a cadence nothing keeps.
#:
#: Seven days is a deliberate UNDER-promise. The real schedule (the
#: '20 */8 * * *' slot in schedule-link-hygiene.yml) runs the archiver three
#: times a day over a 600-URL window, so today's whole in-scope queue is
#: re-examined roughly daily. Weekly is what survives a bad stretch: Wayback
#: throttling every pass for days, the writer lock held by a long backfill, a
#: host outage. Promising the median instead of the floor is how a true
#: sentence becomes a false one without any code changing.
RECHECK_PROMISE_DAYS = 7


def scheduled_archive_cadence_hours(root=None) -> int | None:
    """Hours between scheduled archive passes, read from the REAL schedule.

    The cron lives in schedule-link-hygiene.yml (the writers may not carry
    their own; see that file's header), on the slot that maps to
    archive-sources.yml. Parsed rather than typed for the same reason
    scheduled_archive_scope() is: a promise derived from a schedule that
    changed is a promise nobody is keeping. Returns None when the slot cannot
    be found or is not armed, and None must never render as a cadence.
    """
    from pathlib import Path
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    path = root / ".github" / "workflows" / "schedule-link-hygiene.yml"
    if not path.exists():
        return None
    text = path.read_text()
    # The slot-to-workflow mapping is the `case "$SLOT"` block; the schedule
    # list holds the same cron strings. The archive slot is the one the case
    # maps to archive-sources.yml.
    import re
    for match in re.finditer(r"'([^']+)'\)\s*WANT='archive-sources\.yml'", text):
        cron = match.group(1)
        hour_field = cron.split()[1] if len(cron.split()) == 5 else ""
        if hour_field.startswith("*/"):
            try:
                return int(hour_field[2:])
            except ValueError:
                return None
        return 24  # a fixed hour = one pass a day
    return None


def scheduled_archive_limit(root=None) -> int:
    """Candidate URLs a scheduled archive run examines, from the shell fallback.

    Read from `${LIMIT:-...}` in archive-sources.yml for the same reason
    scheduled_archive_scope() reads `${COLLECTOR:-...}`: a queued ticket carries
    only dry_run, so the fallback is what a scheduled run actually gets, and the
    capacity arithmetic behind the re-check promise must be sized against that
    rather than against a constant that can drift from it.
    """
    from pathlib import Path
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    path = root / ".github" / "workflows" / "archive-sources.yml"
    if path.exists():
        for line in path.read_text().splitlines():
            if "LIMIT:-" in line:
                raw = line.split("LIMIT:-", 1)[1].split("}", 1)[0]
                try:
                    return int(raw)
                except ValueError:
                    break
    return 600


def archive_promise(root=None) -> dict:
    """The one statement of the reader-facing re-check promise.

    Everything the plugin needs to render the pending state, and everything the
    integrity check needs to enforce it, derived from the same two files the
    schedule actually runs from. build_archive_promise.py writes this verbatim
    into the plugin's data directory; tests/test_archive_promise.py fails when
    the shipped copy no longer matches this derivation, which is what makes a
    cron edit that breaks the promise a red test rather than a quiet lie.
    """
    cadence = scheduled_archive_cadence_hours(root)
    return {
        "recheck_days": RECHECK_PROMISE_DAYS,
        "cadence_hours": cadence,
        "collectors": scheduled_archive_scope(root),
        "derived_from": ".github/workflows/schedule-link-hygiene.yml + "
                        "archive-sources.yml; see pipeline/source_links.py",
    }


def archive_recheck_overdue(conn: sqlite3.Connection,
                            collectors: Sequence[str] | None = None,
                            days: int = RECHECK_PROMISE_DAYS) -> list[dict]:
    """Every in-scope unarchived URL the promise has been broken for.

    The listing surfaces tell a reader "we re-check weekly". This is the check
    that keeps that sentence true: an in-scope URL with no snapshot whose last
    archiving round (ledger `updated_at`) is older than the promise window —
    or that has NO ledger row at all despite being stored longer ago than the
    window — is a row the page is lying about. ops_status.py [2c] goes red on
    any result here; a healthy schedule keeps this list empty with days to
    spare, because the 8-hourly pass re-touches the whole queue roughly daily.

    Brand-new URLs are not violations: a URL stored an hour ago has simply not
    had its first pass yet, and the promise it renders under ("next check by"
    seven days out) is still ahead of it.
    """
    names = list(collectors) if collectors is not None else scheduled_archive_scope()
    if not names:
        return []
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=days)).isoformat(timespec="seconds")
    placeholders = ", ".join("?" for _ in names)
    rows = conn.execute(
        f"""SELECT s.source_url,
                   MAX(s.captured_at) AS newest_capture,
                   l.archive_state, l.updated_at
              FROM signals s
              LEFT JOIN source_links l ON l.source_url = s.source_url
             WHERE s.is_current = 1
               AND s.source_url IS NOT NULL AND s.source_url != ''
               AND s.collector IN ({placeholders})
               AND IFNULL(l.archive_state, 'pending') NOT IN
                   ('archived', 'unavailable')
             GROUP BY s.source_url""", names).fetchall()
    overdue = []
    for r in rows:
        last = r["updated_at"] or r["newest_capture"] or ""
        if last and last[:19] < cutoff[:19]:
            overdue.append({"source_url": r["source_url"],
                            "last_attempt": last,
                            "state": r["archive_state"] or "never probed"})
    return overdue


def scheduled_archive_scope(root=None) -> list[str]:
    """The collectors a SCHEDULED archive run actually covers.

    Read from the SHELL FALLBACK in the workflow rather than from the input
    default, because a queued ticket carries only `dry_run` and the fallback is
    what applies on that path.

    This lives here rather than in ops_status.py because two tools now report
    archive coverage and both must scope it the same way. The same reasoning
    that moved the staleness leashes into one module applies exactly: a session
    reading "0.5% archived" from the dashboard and "11% archived" from the
    weekly email would have no way to tell which one was lying.
    """
    from pathlib import Path
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    path = root / ".github" / "workflows" / "archive-sources.yml"
    if not path.exists():
        return list(DEFAULT_ARCHIVE_SCOPE)
    for line in path.read_text().splitlines():
        if "COLLECTOR:-" in line:
            names = line.split("COLLECTOR:-", 1)[1].split("}", 1)[0]
            return [n.strip() for n in names.split(",") if n.strip()]
    return list(DEFAULT_ARCHIVE_SCOPE)


def archive_coverage(conn: sqlite3.Connection,
                     collectors: Sequence[str] | None = None) -> dict:
    """Archive coverage over the population the schedule can actually reach.

    `rot_summary()['archive_pct']` is over the WHOLE corpus, which is the right
    number for "how much of what we cite has a fallback" and the wrong one for
    "is the archiver working". Roughly 96% of that corpus is SEC and GOV.UK
    filings the schedule deliberately does not touch, so the corpus percentage
    has a ceiling near 4% and a healthy archiver reads as a stalled one. The
    ratio here has a ceiling of 100% and moves when the job does.

    `capture_queue` is the count archive.org has answered about and declined to
    hold: the only population a capture budget should ever be sized against.
    `never_probed` is not a gap in Wayback, it is a gap in what we know.
    """
    names = list(collectors) if collectors is not None else scheduled_archive_scope()
    if not names:
        names = list(DEFAULT_ARCHIVE_SCOPE)
    placeholders = ", ".join("?" for _ in names)
    urls = {r[0] for r in conn.execute(
        f"""SELECT source_url FROM signals
             WHERE is_current = 1 AND source_url IS NOT NULL AND source_url != ''
               AND collector IN ({placeholders})
             GROUP BY source_url""", names)}
    known = {r["source_url"]: dict(r) for r in conn.execute(
        "SELECT source_url, archive_state, archive_probes, archived_at "
        "  FROM source_links")}

    archived = unavailable = capture_queue = never = 0
    newest = None
    for url in urls:
        row = known.get(url) or {}
        state = row.get("archive_state")
        if state == "archived":
            archived += 1
            when = row.get("archived_at")
            if when and (newest is None or when > newest):
                newest = when
        elif state == "unavailable":
            unavailable += 1
        elif int(row.get("archive_probes") or 0) > 0:
            capture_queue += 1
        else:
            never += 1
    total = len(urls)
    return {
        "collectors": names,
        "in_scope": total,
        "archived": archived,
        "unavailable": unavailable,
        "capture_queue": capture_queue,
        "never_probed": never,
        "pct": round(100.0 * archived / total, 1) if total else 0.0,
        "newest_snapshot": newest,
    }


def rot_by_publisher(conn: sqlite3.Connection, *, minimum: int = 3) -> list[dict]:
    """Rot rate per host, worst first.

    This is the actionable half. An overall rate creeping up says nothing you
    can act on; one publisher going from 0% to 60% says it changed its URL
    scheme, and that is a fix rather than a lament. `minimum` keeps a single
    dead link at a one-article host out of the top of the list at 100%.
    """
    rows = conn.execute(
        """SELECT host,
                  COUNT(*) AS checked,
                  SUM(CASE WHEN state IN ('dead','drifted') THEN 1 ELSE 0 END) AS rot,
                  SUM(CASE WHEN state = 'drifted' THEN 1 ELSE 0 END) AS drifted
             FROM source_links
            WHERE state IS NOT NULL AND host IS NOT NULL
            GROUP BY host HAVING checked >= ?
            ORDER BY (1.0 * rot / checked) DESC, checked DESC""",
        (minimum,)).fetchall()
    return [dict(r, rot_pct=round(100.0 * r["rot"] / r["checked"], 1))
            for r in rows if r["rot"]]

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
    """
    rows = distinct_source_urls(conn, collector=collector)
    known = {r["source_url"]: dict(r) for r in conn.execute(
        "SELECT source_url, archive_state, archive_attempts, archive_probes, "
        "       archive_blind_rounds FROM source_links")}
    tier_unprobed, tier_probed = [], []
    for row in rows:
        seen = known.get(row["source_url"]) or {}
        state = seen.get("archive_state")
        if state == "archived" or state == "unavailable":
            continue
        row["archive_attempts"] = int(seen.get("archive_attempts") or 0)
        row["archive_probes"] = int(seen.get("archive_probes") or 0)
        row["archive_blind_rounds"] = int(seen.get("archive_blind_rounds") or 0)
        (tier_unprobed if row["archive_probes"] == 0 else tier_probed).append(row)
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

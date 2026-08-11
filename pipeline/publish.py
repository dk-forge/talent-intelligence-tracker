"""Push stored signals to the WordPress plugin.

SQLite in the repo is the system of record; WordPress is the rendering surface.
Publishing is therefore idempotent and resumable: rows carry a `published_at`
marker, only unpublished ones are sent, and a duplicate on the server side is a
success rather than an error.
"""

from __future__ import annotations

import os
import time

import requests

from . import guardrails

# ModSecurity on this host blocks python-requests outright. Anything talking to
# the WP host must look like a browser or every call returns an inexplicable
# 403.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
TIMEOUT = 30

BATCH_SIZE = 25
RETRY_STATUSES = (500, 502, 503, 504)


class PublishError(RuntimeError):
    pass


def _guard(conn, *, dry_run: bool) -> dict:
    """Run the pre-publish guardrails and decide what may be sent.

    HERE and not in a separate audit script, because the $86bn Form D
    overstatement was not a thing nobody could have checked - it was a thing
    nobody was going to remember to check. Every route that can move a headline
    figure goes through this module: run_collect, every backfill, the
    corrections, and the enrich path that once took the money charts from $3.2M
    to $20.79bn on its own.

    It QUARANTINES rather than halting. The flagged rows are dropped from the
    batch and everything else goes out. The first build halted the whole run,
    and the first two production runs showed what that costs: eight findings
    stopped a collect and a backfill that were carrying dozens of good records,
    and one of the eight (X.AI, $16.6bn) is a real raise. A guard that stops the
    product every time a genuine mega-round lands does not survive contact with
    a product that has to run unattended for days.

    What does not soften: a quarantined row is not sent, so it cannot reach a
    headline figure. That was the whole point and it is unchanged.
    """
    report = guardrails.quarantine(conn, write=not dry_run)
    _announce(report, dry_run=dry_run)
    if report["aggregate"]:
        findings = guardrails.as_findings(report["aggregate"])
        error = PublishError(str(guardrails.AggregateBroken(findings)))
        error.findings = findings
        raise error
    return report


def _announce(report: dict, *, dry_run: bool = False) -> None:
    """Say what was held back, in a way that cannot be scrolled past.

    Printed from HERE rather than from run_collect so every route gets it for
    free: six backfill scripts, both corrections and the enrich job all publish
    through this module and not one of them would have grown its own version.

    `::warning::` and `::error::` are GitHub Actions annotations, so a
    quarantine lands on the run summary page and not only in a log nobody opens.
    Same mechanism health_digest.py already uses.
    """
    held, live, overdue = report["held"], report["live"], report["overdue"]
    if not (held or live or report["aggregate"]):
        return

    prefix = "would quarantine" if dry_run else "QUARANTINED"
    print(f"\n[guardrails] {prefix} {len(held) + len(live)} row(s). "
          f"Everything else in this batch publishes normally.")

    for row in sorted(held + live, key=lambda r: -(r.get("value") or 0)):
        age, grace = row.get("age_hours"), row.get("grace_hours")
        left = "" if age is None else f", red in {max(0.0, grace - age):.0f}h"
        where = ("ALREADY LIVE on the site, needs a retraction decision"
                 if row["already_live"] else "held back, never published")
        print(f"::warning::[guardrail] {row['check_name']}/{row['subject']} "
              f"{row.get('label') or ''} - {where}{left}")

    if live:
        print(f"::warning::[guardrail] {len(live)} of these are ALREADY on the "
              f"live site. Quarantine cannot pull a published row back; only "
              f"`python3 retract.py <signal_id> '<why>'` can.")

    for row in report["aggregate"]:
        print(f"::error::[guardrail] {row['check_name']}/{row['subject']} "
              f"{row.get('label') or ''} - the published set does not add up, "
              f"so nothing was sent.")

    if overdue:
        print(f"::error::[guardrail] {len(overdue)} finding(s) are past their "
              f"grace window. This run exits non-zero AFTER publishing the "
              f"clean rows.")

    print("[guardrails] Answer them:  python3 guardrails.py\n")


def _escalate(report: dict, published: int) -> None:
    """Go red once a human has neglected a finding, never before.

    Called AFTER the send, on purpose. The escalation must not cost a clean row:
    "one suspect row does not take the batch down with it" has to hold on the
    day the run goes red as well as on the days it does not. The clean rows are
    already sent, marked published and committed by the time this raises, so red
    here means "nobody answered", never "work was lost".
    """
    overdue = report["overdue"]
    if not overdue:
        return
    oldest = max((row.get("age_hours") or 0) for row in overdue)
    findings = guardrails.as_findings(overdue)
    error = PublishError(str(guardrails.QuarantineOverdue(
        findings, published=published, oldest_hours=oldest)))
    error.findings = findings
    raise error


def _config() -> tuple[str, str]:
    site = (os.environ.get("WP_SITE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("WP_API_KEY") or "").strip()
    if not site:
        raise PublishError("WP_SITE_URL is not set")
    if not site.endswith("/blog"):
        # The bare domain is a different application entirely.
        raise PublishError(f"WP_SITE_URL must end with /blog, got {site!r}")
    if not key:
        raise PublishError("WP_API_KEY is not set")
    return site, key


# The allowlist of columns that travel to WordPress. A column missing here is
# a column the site can never show, however well it is populated locally, so
# adding a field to the schema means adding it here in the same change.
FIELDS = (
    "signal_id", "headline", "summary", "talent_readthrough", "company",
    "company_key", "ticker", "cik", "employer_type", "pillar",
    "signal_direction", "city", "region", "country",
    "hq_city", "hq_country", "state", "functions", "industry", "headcount",
    "headcount_scope", "funding_amount", "funding_amount_usd", "funding_stage",
    "work_mode", "deal_type", "site_event",
    "materiality", "confidence", "source_url", "source_name",
    # `archive_url` is deliberately NOT here. It is not a field of Signal and
    # never could be: a row is built at classification time and its Wayback
    # snapshot is captured afterwards, so at publish time it is always empty.
    # It travels through ENRICHABLE below instead, which is the path that exists
    # precisely for values learned after a row was published.
    "discovery_url", "published_date", "effective_date", "captured_at",
    "as_of", "content_hash", "predicted_outcome", "check_after_date",
    "collector",
)


# Derived columns /enrich accepts. Must match tit_enrichable_columns() in
# includes/api.php: nothing a source STATED, only what we computed or looked up.
ENRICHABLE = (
    "funding_amount_usd", "funding_stage", "effective_date",
    "ticker", "cik", "work_mode", "employer_type", "headcount_scope",
    "materiality",
    # The employer's headquarters, looked up rather than claimed by a source.
    # Exactly the same class as ticker and cik, and left out of this list by
    # oversight rather than by decision: the identity backfill fills them
    # locally and they had no route to the site, so published rows stayed
    # invisible to every geographic filter while we already held the answer.
    # The recall measurement is what surfaced it.
    #
    # `country` is NOT here and must not be. That is the JOB location, taken
    # only from the source text, and pushing a looked-up value into it would
    # turn "where the source says this happened" into "where the company is
    # from". The site already unions the two at query time (country_basis=any),
    # which is the right place for that to happen and the only place it is
    # reversible.
    "hq_city", "hq_country",
    # A neutral third-party snapshot of the document this row already cites.
    # Looked up, never claimed by a source, and it can only ADD a fallback: it
    # is not source_url and can never become it, so a bug in archiving leaves a
    # row exactly as sourced as it already was.
    "archive_url",
)


def enrich_published(conn, *, dry_run: bool = False, limit: int | None = None) -> dict:
    """Push derived fields onto rows the site already holds.

    publish() only ever sends rows with published_at IS NULL, and the server
    treats a re-sent content_hash as a duplicate, so a column added AFTER a row
    was published could never reach the live table. Measured 2026-07-28: the
    local database held $20.79bn of parsed funding across 53 rows while the
    site's money charts showed one row and $3.2M.

    Safe to run repeatedly: only keys that are present are sent, the server
    ignores empty values so a blank can never erase a known one, and rows it
    cannot find are reported rather than treated as failures.
    """
    import sqlite3 as _sqlite3

    # funding_amount_usd is in ENRICHABLE, so this path alone can move the money
    # total on a row the site already holds. It is guarded like any other write.
    guard = _guard(conn, dry_run=dry_run)

    cols = ", ".join(ENRICHABLE)
    sql = (
        f"SELECT content_hash, {cols} FROM signals "
        "WHERE is_current = 1 AND published_at IS NOT NULL AND ("
        + " OR ".join(f"{c} IS NOT NULL" for c in ENRICHABLE) + ") "
        "ORDER BY row_id ASC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    previous_factory = conn.row_factory
    conn.row_factory = _sqlite3.Row
    try:
        raw = [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.row_factory = previous_factory
    rows = [{k: v for k, v in r.items() if v is not None} for r in raw]

    # A quarantined row must not have its derived columns pushed either. This is
    # the path that carries funding_amount_usd, so leaving it unfiltered would
    # mean a flagged amount reaching the money total by the back door while
    # publish() was carefully not sending it by the front.
    quarantined = guard["quarantined"]
    held = [r for r in rows if r["content_hash"] in quarantined]
    rows = [r for r in rows if r["content_hash"] not in quarantined]

    if not rows:
        _escalate(guard, 0)
        return {"sent": 0, "updated": 0, "errors": [],
                "quarantined": len(held), "guardrails": guard}
    if dry_run:
        return {"sent": 0, "updated": 0, "errors": [], "would_send": len(rows),
                "quarantined": len(held), "guardrails": guard}

    site, key = _config()
    session = requests.Session()
    updated = sent = 0
    errors: list[dict] = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        # Same retry as _post_batch above, and for the same reason. That
        # function's comment -- "shared hosting 500s at random under load, a
        # single bad response must not abort the run" -- was written about the
        # publish path and never applied here, so enrich stayed a bare post
        # with a 30s timeout where publish uses 60. It died on
        # `ReadTimeoutError ... read timeout=30` against asktherecruiter.com on
        # 2026-07-30 while carrying archive snapshots, losing the whole run to
        # one slow minute on somebody else's server.
        #
        # A retry is safe here specifically because /enrich is idempotent: it
        # writes derived values onto rows matched by content hash, so sending
        # the same batch twice sets the same values twice. The publish path
        # could not be retried this casually and is not.
        last_error = ""
        resp = None
        for attempt in range(4):
            try:
                resp = session.post(
                    f"{site}/wp-json/talent/v1/enrich",
                    json={"rows": batch},
                    headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT,
                             "Content-Type": "application/json"},
                    timeout=60,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                resp = None
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in RETRY_STATUSES:
                last_error = f"{resp.status_code}: {resp.text[:200]}"
                time.sleep(2 ** attempt)
                continue
            break
        if resp is None or resp.status_code in RETRY_STATUSES:
            raise PublishError(
                f"/enrich did not answer after 4 attempts: {last_error}")
        resp.raise_for_status()
        result = resp.json() or {}
        updated += int(result.get("updated", 0))
        errors.extend(result.get("errors") or [])
        sent += len(batch)
    _escalate(guard, sent)
    return {"sent": sent, "updated": updated, "errors": errors,
            "quarantined": len(held), "guardrails": guard}


def unpublished(conn, limit: int | None = None) -> list[dict]:
    sql = (
        f"SELECT row_id, {', '.join(FIELDS)} FROM signals "
        "WHERE is_current = 1 AND published_at IS NULL "
        "ORDER BY row_id ASC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def _post_batch(session: requests.Session, site: str, key: str, rows: list[dict]) -> dict:
    payload = {"rows": [{f: row.get(f) for f in FIELDS} for row in rows]}

    # Shared hosting 500s at random under load. A single bad response must not
    # abort the run and lose the whole batch.
    last_error = ""
    for attempt in range(4):
        try:
            resp = session.post(
                f"{site}/wp-json/talent/v1/bulk",
                json=payload,
                headers={
                    "X-Talent-API-Key": key,
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in RETRY_STATUSES:
            last_error = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 403:
            raise PublishError("WordPress rejected the API key (403). WP_API_KEY "
                               "must match TALENT_API_KEY in wp-config.php.")
        if resp.status_code == 503:
            raise PublishError("WordPress has no API key configured (503). Set "
                               "TALENT_API_KEY in wp-config.php.")
        if resp.status_code >= 400:
            raise PublishError(f"{resp.status_code}: {resp.text[:300]}")

        # 207 means some rows failed; the body names which.
        return resp.json()

    raise PublishError(f"gave up after retries: {last_error}")


def publish_health(conn, *, dry_run: bool = False) -> int:
    """Send every collector's last run to the site.

    Health has always been recorded locally and never sent, so /source-health
    returned nothing and the sources page could not say when a source last ran.
    "Running now" was a status with no evidence behind it.

    Sent separately from the records, and failure here never blocks them: a
    stale timestamp is a much smaller problem than a lost signal.
    """
    rows = [dict(r) for r in conn.execute(
        """SELECT collector, run_at, status, items_found, items_stored, detail
             FROM source_health ORDER BY collector"""
    ).fetchall()]
    if not rows or dry_run:
        return 0

    site, key = _config()
    resp = requests.post(
        f"{site}/wp-json/talent/v1/health",
        json={"collectors": rows},
        headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return len(rows)


def publish(conn, *, dry_run: bool = False, limit: int | None = None) -> dict:
    """Send unpublished signals. Returns a summary.

    The guardrails run FIRST, before a single row is sent and before the
    "nothing to send" short circuit. Two of the four are checks on the whole
    published set rather than on the batch, so a run with no new rows is exactly
    when a period total or a date span can be wrong and nobody would look.
    """
    guard = _guard(conn, dry_run=dry_run)

    rows = unpublished(conn, limit)

    # The quarantine. A flagged row is dropped from the batch and stays
    # unpublished, so it reaches no headline figure; every other row in the same
    # batch goes out. It is NOT marked published, so it is re-offered every run
    # and publishes itself the moment somebody accepts the finding - no requeue,
    # no separate replay path to remember.
    quarantined = guard["quarantined"]
    held = [r for r in rows if r["content_hash"] in quarantined]
    rows = [r for r in rows if r["content_hash"] not in quarantined]

    if not rows:
        _escalate(guard, 0)
        return {"sent": 0, "stored": 0, "duplicate": 0, "errors": [],
                "quarantined": len(held), "guardrails": guard}

    if dry_run:
        return {"sent": 0, "stored": 0, "duplicate": 0, "errors": [],
                "would_send": len(rows), "quarantined": len(held),
                "guardrails": guard}

    site, key = _config()
    session = requests.Session()

    sent = stored = duplicate = 0
    errors: list[dict] = []

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        result = _post_batch(session, site, key, batch)

        stored += int(result.get("stored", 0))
        duplicate += int(result.get("duplicate", 0))
        batch_errors = result.get("errors") or []
        errors.extend(batch_errors)

        # Mark only what the server accepted. A row that errored stays
        # unpublished and is retried next run rather than being lost silently.
        failed_indexes = {e.get("index") for e in batch_errors}
        accepted = [r["row_id"] for i, r in enumerate(batch) if i not in failed_indexes]
        if accepted:
            conn.executemany(
                "UPDATE signals SET published_at = datetime('now') WHERE row_id = ?",
                [(row_id,) for row_id in accepted],
            )
            conn.commit()
        sent += len(batch)

    # After the send, never before: the escalation must not cost a clean row.
    _escalate(guard, sent)

    return {"sent": sent, "stored": stored, "duplicate": duplicate,
            "errors": errors, "quarantined": len(held), "guardrails": guard}

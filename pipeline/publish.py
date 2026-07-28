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

# ModSecurity on this host blocks python-requests outright. Anything talking to
# the WP host must look like a browser or every call returns an inexplicable
# 403.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
TIMEOUT = 30

BATCH_SIZE = 25
RETRY_STATUSES = (500, 502, 503, 504)


class PublishError(RuntimeError):
    pass


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
    "work_mode", "deal_type", "materiality", "confidence", "source_url", "source_name",
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
    if not rows:
        return {"sent": 0, "updated": 0, "errors": []}
    if dry_run:
        return {"sent": 0, "updated": 0, "errors": [], "would_send": len(rows)}

    site, key = _config()
    session = requests.Session()
    updated = sent = 0
    errors: list[dict] = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        resp = session.post(
            f"{site}/wp-json/talent/v1/enrich",
            json={"rows": batch},
            headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT,
                     "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json() or {}
        updated += int(result.get("updated", 0))
        errors.extend(result.get("errors") or [])
        sent += len(batch)
    return {"sent": sent, "updated": updated, "errors": errors}


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
    """Send unpublished signals. Returns a summary."""
    rows = unpublished(conn, limit)
    if not rows:
        return {"sent": 0, "stored": 0, "duplicate": 0, "errors": []}

    if dry_run:
        return {"sent": 0, "stored": 0, "duplicate": 0, "errors": [],
                "would_send": len(rows)}

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

    return {"sent": sent, "stored": stored, "duplicate": duplicate, "errors": errors}

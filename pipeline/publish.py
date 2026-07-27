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


FIELDS = (
    "signal_id", "headline", "summary", "talent_readthrough", "company",
    "company_key", "pillar", "signal_direction", "city", "region", "country",
    "hq_city", "hq_country", "state", "functions", "industry", "headcount",
    "funding_amount", "confidence", "source_url", "source_name",
    "discovery_url", "published_date", "captured_at", "as_of", "content_hash",
    "predicted_outcome", "check_after_date", "collector",
)


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

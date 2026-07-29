#!/usr/bin/env python3
"""Withdraw a published record, locally and on WordPress.

    python retract.py <signal_id> "why it was withdrawn"
    python retract.py --bare-domains "source link was an outlet homepage"

Nothing is deleted. The row is marked not-current with the reason, so the
corrections log can show what was published and when it was withdrawn.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlparse

import requests

from pipeline import publish, schema


def find_bare_domain_rows(conn) -> list[dict]:
    """Rows whose source link is an outlet homepage rather than an article.

    These cannot support the claim they are attached to, which is the one
    failure this product cannot carry.
    """
    rows = []
    for row in conn.execute(
        "SELECT signal_id, company, source_url FROM signals WHERE is_current = 1"
    ):
        if not urlparse(row["source_url"]).path.strip("/"):
            rows.append(dict(row))
    return rows


#: Retries for a TRANSIENT host failure, and the pauses between them.
#:
#: Shared hosting 500s and 504s under load — it is gotcha 8 in CLAUDE.md and it
#: was walked into anyway: on 2026-07-29 a scope correction withdrew three rows
#: and lost four to `504` from the gateway, one request at a time, with nothing
#: wrong with the requests. A withdrawal that fails leaves a record live on a
#: page that promises it is not there, so this is the one place where "the host
#: was busy" must not be a final answer.
#:
#: Only 5xx and a dropped connection are retried. A 4xx is our fault — a bad
#: key, a signal_id that does not exist — and repeating it just asks the same
#: wrong question five times.
RETRY_PAUSES = (2, 5, 12, 30)


def retract_remote(signal_id: str, reason: str) -> dict:
    site, key = publish._config()
    last = ""
    for attempt in range(len(RETRY_PAUSES) + 1):
        try:
            resp = requests.post(
                f"{site}/wp-json/talent/v1/retract",
                json={"signal_id": signal_id, "reason": reason},
                headers={
                    "X-Talent-API-Key": key,
                    "User-Agent": publish.USER_AGENT,
                    "Content-Type": "application/json",
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"[:200]
        else:
            if resp.status_code < 400:
                return resp.json()
            last = f"{resp.status_code}: {resp.text[:200]}"
            if resp.status_code < 500:
                raise publish.PublishError(last)
        if attempt < len(RETRY_PAUSES):
            time.sleep(RETRY_PAUSES[attempt])
    raise publish.PublishError(f"{last} (after {len(RETRY_PAUSES) + 1} attempts)")


def retract_local(conn, signal_id: str, reason: str) -> int:
    cur = conn.execute(
        "UPDATE signals SET is_current = 0, notes = ? WHERE signal_id = ? AND is_current = 1",
        (f"retracted: {reason}", signal_id),
    )
    conn.commit()
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Withdraw a published record.")
    parser.add_argument("signal_id", nargs="?")
    parser.add_argument("reason", nargs="?")
    parser.add_argument("--bare-domains", metavar="REASON",
                        help="retract every row whose source link is a homepage")
    args = parser.parse_args()

    conn = schema.connect()

    if args.bare_domains:
        targets = [(r["signal_id"], args.bare_domains, r["company"], r["source_url"])
                   for r in find_bare_domain_rows(conn)]
        if not targets:
            print("No bare-domain rows. Nothing to retract.")
            return 0
    elif args.signal_id and args.reason:
        targets = [(args.signal_id, args.reason, "", "")]
    else:
        parser.error("give a signal_id and reason, or use --bare-domains REASON")

    failures = 0
    for signal_id, reason, company, url in targets:
        label = f"{company or signal_id}" + (f" ({url})" if url else "")
        try:
            result = retract_remote(signal_id, reason)
            local = retract_local(conn, signal_id, reason)
            print(f"retracted {label}: wordpress={result.get('retracted')} local={local}")
        except (publish.PublishError, requests.RequestException) as exc:
            failures += 1
            print(f"FAILED {label}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

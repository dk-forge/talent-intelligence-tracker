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


def retract_remote(signal_id: str, reason: str) -> dict:
    site, key = publish._config()
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
    if resp.status_code >= 400:
        raise publish.PublishError(f"{resp.status_code}: {resp.text[:200]}")
    return resp.json()


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

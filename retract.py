#!/usr/bin/env python3
"""Withdraw a published record, locally and on WordPress.

    python retract.py <signal_id> "why it was withdrawn"
    python retract.py --bare-domains "source link was an outlet homepage"
    python retract.py --out-of-scope "the sibling tracker owns workforce reduction"

Nothing is deleted. The row is marked not-current with the reason, so the
corrections log can show what was published and when it was withdrawn.
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

import requests

from pipeline import publish, schema, validate


class Unsafe(RuntimeError):
    """The worklist is so large that the likeliest explanation is a broken
    rule, not a broken table."""


# A rule-derived worklist withdraws PUBLISHED rows, so it gets the same
# treatment correct_sec_pillar.py gives its own: past this share, the
# explanation is far more likely to be a vocabulary change that started
# matching everything than a table that was wrong all along. Measured
# 2026-07-29: 5 of 15,631 live rows, 0.03%.
#
# Below MIN_ROWS a share says nothing — one row in two is 50% and no evidence
# of anything — so the guard waits until there is a population to judge rather
# than blocking a fresh table or a test fixture.
MAX_SHARE = 0.01
MIN_ROWS = 50


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


def find_out_of_scope_rows(conn, *, force: bool = False) -> list[dict]:
    """Live rows the scope boundary refuses today: a workforce reduction is the
    sibling tracker's record, not ours.

    The worklist is derived by CALLING validate.out_of_scope_term, exactly as
    correct_sec_pillar.py derives its own by calling validate.forced_pillar,
    so what this withdraws and what the gate refuses can never drift. A row
    this returns is a row that would not be stored if it were collected now.

    Every value it reads is a stored column, so nothing is refetched, no model
    is called, and the answer is the same one build_signal computed — or would
    have computed, for the rows that predate the arm that catches them.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT signal_id, company, collector, headline, summary, "
        "       talent_readthrough, signal_direction, source_url "
        "FROM signals WHERE is_current = 1 ORDER BY row_id")]

    out = []
    for row in rows:
        hit = validate.out_of_scope_term(
            collector=row["collector"],
            headline=row["headline"] or "",
            summary=row["summary"] or "",
            talent_readthrough=row["talent_readthrough"] or "",
            signal_direction=row["signal_direction"] or "",
        )
        if hit:
            row["term"], row["arm"] = hit
            out.append(row)

    share = len(out) / len(rows) if rows else 0
    if len(rows) >= MIN_ROWS and share > MAX_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} live rows ({share:.1%}) would be withdrawn, "
            f"above the {MAX_SHARE:.0%} ceiling. Check the reduction vocabulary in "
            f"pipeline/prefilter.py before re-running, and pass --force only if the "
            f"number is genuinely right.")
    return out


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
    parser.add_argument("--out-of-scope", metavar="REASON",
                        help="retract every live row the scope boundary refuses: "
                             "a workforce reduction belongs to the sibling tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be withdrawn and write nothing")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows would go")
    args = parser.parse_args()

    conn = schema.connect()

    if args.bare_domains:
        targets = [(r["signal_id"], args.bare_domains, r["company"], r["source_url"])
                   for r in find_bare_domain_rows(conn)]
        if not targets:
            print("No bare-domain rows. Nothing to retract.")
            return 0
    elif args.out_of_scope:
        try:
            rows = find_out_of_scope_rows(conn, force=args.force)
        except Unsafe as exc:
            print(f"REFUSING: {exc}", file=sys.stderr)
            return 2
        # The arm and the matched phrase go in the reason, so the corrections
        # log says WHY this row was out of scope and not just that it was.
        targets = [(r["signal_id"],
                    f"{args.out_of_scope} ({r['arm']}: {r['term']!r})",
                    r["company"], r["source_url"])
                   for r in rows]
        for row in rows:
            print(f"  [{row['collector']}] {row['company']}\n"
                  f"      {row['headline'][:96]}\n"
                  f"      {row['arm']}: {row['term']!r}")
        if not targets:
            print("No out-of-scope rows. Nothing to retract.")
            return 0
    elif args.signal_id and args.reason:
        targets = [(args.signal_id, args.reason, "", "")]
    else:
        parser.error("give a signal_id and reason, or use --bare-domains REASON "
                     "or --out-of-scope REASON")

    if args.dry_run:
        print(f"\ndry run: {len(targets)} row(s) would be withdrawn, nothing written")
        return 0

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

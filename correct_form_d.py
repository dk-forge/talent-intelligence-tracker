#!/usr/bin/env python3
"""Correct the Form D rows that are already published.

    python3 correct_form_d.py --dry-run      # counts only, writes nothing
    python3 correct_form_d.py                # apply

Three things were published that the filings do not say, all fixed in the
collector (see collectors/sec_form_d_bulk.py). The collector fix only governs
rows collected AFTER it. This governs the rows already on the site.

    1. signal_direction "hiring" on a filing that states money and not
       headcount. Corrected in place to "neutral".
    2. A read-through asserting that capital is spent on headcount "within the
       following two to six quarters". Replaced with what the filing records.
    3. Rows that are not employers, or not capital raises: single-purpose
       property vehicles, non-traded credit vehicles, insurance product
       offerings. Those are RETRACTED, not corrected.

**Why a correction and not a purge-and-reimport.** content_hash is
md5(company_key|pillar|published_date|normalised_headline) — pipeline/validate.py,
content_hash(). Neither signal_direction nor talent_readthrough is an input, so
rewriting them cannot move a row's hash and cannot orphan the dedup. A
purge-and-reimport would churn thousands of rows and take the source reports
attached to them with it, for no gain.

**Nothing here refetches a record or calls a model.** The corrected fields are
recomputed from the columns already stored, through the collector's own
`as_classified`, so a corrected row and a freshly collected one say the same
thing. The quarter archives ARE downloaded, but only to re-run the exclusion
rules — the archive decides which rows to retract, never what a row says.

Idempotent, and safe to interrupt: every row is an independent operation, a row
already holding the corrected values is skipped, and a retraction only ever
touches is_current = 1.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import requests

import retract
from collectors import sec_form_d, sec_form_d_bulk as bulk
from pipeline import publish, schema, vocab

COLLECTOR = bulk.COLLECTOR
BATCH_SIZE = 25
# data/.cache/ is already gitignored: a quarter archive is somebody else's file.
CACHE = Path(os.environ.get("FORM_D_CACHE", "data/.cache/form-d"))

# The read-through every affected row carries. Matched on a phrase specific
# enough that a row corrected by hand is not clobbered.
INVENTED_READTHROUGH = "standard precursor to hiring"


def _quarter(published_date: str) -> str:
    year, month = published_date[:4], int(published_date[5:7])
    return f"{year}q{(month + 2) // 3}"


def live_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT signal_id, content_hash, company, headline, city, state, country, "
        "       published_date, funding_amount_usd, signal_direction, talent_readthrough, "
        "       source_url "
        "FROM signals WHERE collector = ? AND is_current = 1 ORDER BY row_id",
        (COLLECTOR,))]


def as_item(row: dict) -> dict:
    """Rebuild the collector's raw item from what is stored.

    Only the keys `as_classified` reads. The country is stored as a code, so it
    is expanded back to the name the collector used, or a fresh collection and
    a correction would word the same row differently.
    """
    country = (row.get("country") or "").strip()
    place = "United States" if country == "US" else (vocab.COUNTRY_NAMES.get(country) or "")
    return {
        "headline": row["headline"],
        "money": sec_form_d._humanise(int(row["funding_amount_usd"] or 0)),
        "city": row.get("city") or "",
        "state": row.get("state") or "",
        "country": place,
        "published_date": row.get("published_date") or "",
    }


def corrections(rows: list[dict]) -> list[dict]:
    """Rows whose stored direction or read-through differs from the fix."""
    out = []
    for row in rows:
        if not row.get("funding_amount_usd"):
            # Without the figure the read-through cannot be rebuilt truthfully.
            continue
        fixed = bulk.as_classified(as_item(row))
        data = {}
        if row.get("signal_direction") != fixed["signal_direction"]:
            data["signal_direction"] = fixed["signal_direction"]
        if INVENTED_READTHROUGH in (row.get("talent_readthrough") or ""):
            data["talent_readthrough"] = fixed["talent_readthrough"]
        if data:
            out.append({"content_hash": row["content_hash"], "signal_id": row["signal_id"],
                        "company": row["company"], **data})
    return out


def _archives(quarter: str, *, timeout: int = 300) -> list[bytes]:
    """One quarter's archives, cached, so a re-run or an interrupted run does
    not download SEC's servers again. Some early quarters are split into
    numbered parts, so a quarter is a LIST."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = sorted(CACHE.glob(f"{quarter}.*.zip"))
    if cached:
        return [p.read_bytes() for p in cached]
    urls = bulk.dataset_urls().get(quarter)
    if not urls:
        raise bulk.DatasetError(f"{quarter} is not published on {bulk.INDEX_URL}")
    blobs = []
    for n, url in enumerate(sorted(urls)):
        resp = requests.get(url, headers={"User-Agent": bulk.USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        (CACHE / f"{quarter}.{n}.zip").write_bytes(resp.content)
        blobs.append(resp.content)
    return blobs


def still_qualifying(quarters: set[str]) -> set[str]:
    """Every source_url the collector's CURRENT rules would still produce."""
    keep: set[str] = set()
    for quarter in sorted(quarters):
        print(f"  re-running the exclusion rules over {quarter} ...", flush=True)
        for blob in _archives(quarter):
            for item in bulk.parse_archive(blob):
                keep.add(item["source_url"])
    return keep


# A correction pass that retracts most of a quarter is not a correction pass,
# it is a broken download reading as "nothing qualifies any more". The measured
# exclusion rate is ~25%, so anything past this is a fault, not a result.
MAX_RETRACTION_SHARE = 0.45


class Unsafe(RuntimeError):
    """The re-parse disagrees with the published data so violently that the
    likeliest explanation is a bad archive, not a bad row."""


def retractions(rows: list[dict], *, force: bool = False) -> list[dict]:
    """Published rows the current rules would no longer collect.

    Decided by re-running parse_archive rather than by re-implementing the
    filters here, so this cannot drift from the collector.
    """
    quarters = {_quarter(r["published_date"]) for r in rows if r.get("published_date")}
    keep = still_qualifying(quarters)
    out = [r for r in rows if r["source_url"] not in keep]
    share = len(out) / len(rows) if rows else 0
    if share > MAX_RETRACTION_SHARE and not force:
        raise Unsafe(
            f"{len(out)} of {len(rows)} rows ({share:.0%}) look excluded. Expected "
            f"~25%. A truncated or wrong-quarter archive looks exactly like this. "
            f"Check {CACHE}/ before re-running, and pass --force only if the "
            f"number is genuinely right.")
    return out


REASON = ("not an employer or not a capital raise: single-purpose property and "
          "investment vehicles, and insurance product offerings, are excluded "
          "from this source")


def push_corrections(rows: list[dict]) -> dict:
    site, key = publish._config()
    session = requests.Session()
    sent = corrected = 0
    errors: list[dict] = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        payload = [{k: v for k, v in r.items()
                    if k in ("content_hash", "signal_direction", "talent_readthrough")}
                   for r in batch]
        resp = session.post(
            f"{site}/wp-json/talent/v1/correct",
            json={"collector": COLLECTOR, "rows": payload},
            headers={"X-Talent-API-Key": key, "User-Agent": publish.USER_AGENT,
                     "Content-Type": "application/json"},
            timeout=publish.TIMEOUT,
        )
        if resp.status_code >= 400:
            raise publish.PublishError(f"{resp.status_code}: {resp.text[:300]}")
        result = resp.json() or {}
        corrected += int(result.get("corrected", 0))
        errors.extend(result.get("errors") or [])
        sent += len(batch)
        print(f"    corrected {corrected}/{sent} sent", flush=True)
    return {"sent": sent, "corrected": corrected, "errors": errors}


def apply_locally(conn, rows: list[dict]) -> int:
    for row in rows:
        sets, values = [], []
        for col in ("signal_direction", "talent_readthrough"):
            if col in row:
                sets.append(f"{col} = ?")
                values.append(row[col])
        values.append(row["content_hash"])
        conn.execute(
            f"UPDATE signals SET {', '.join(sets)} WHERE content_hash = ? AND is_current = 1",
            values)
    conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--skip-retractions", action="store_true",
                        help="correct the wording only; leave the vehicles published")
    parser.add_argument("--limit", type=int, help="stop after N rows (for a first pass)")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows looks excluded")
    args = parser.parse_args()

    conn = schema.connect()
    rows = live_rows(conn)
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} live {COLLECTOR} rows\n")

    try:
        to_retract = [] if args.skip_retractions else retractions(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    retract_hashes = {r["content_hash"] for r in to_retract}
    to_correct = [c for c in corrections(rows) if c["content_hash"] not in retract_hashes]

    direction = sum(1 for c in to_correct if "signal_direction" in c)
    wording = sum(1 for c in to_correct if "talent_readthrough" in c)
    money = sum(int(r["funding_amount_usd"] or 0) for r in to_retract)
    print(f"\n  to retract          {len(to_retract):>5}   (${money / 1e9:.1f}bn of stated raises)")
    print(f"  to correct          {len(to_correct):>5}")
    print(f"    direction         {direction:>5}   -> neutral")
    print(f"    read-through      {wording:>5}   -> what the filing records")
    print(f"  already correct     {len(rows) - len(to_retract) - len(to_correct):>5}")

    for row in to_retract[:8]:
        print(f"  [retract] ${int(row['funding_amount_usd'] or 0):>15,}  {row['company']}")
    for row in to_correct[:3]:
        print(f"\n  [correct] {row['company']}\n            {row.get('talent_readthrough', '')}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    failures = 0
    if to_retract:
        print(f"\nretracting {len(to_retract)} ...")
        for row in to_retract:
            try:
                retract.retract_remote(row["signal_id"], REASON)
                retract.retract_local(conn, row["signal_id"], REASON)
            except (publish.PublishError, requests.RequestException) as exc:
                failures += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    if to_correct:
        print(f"\ncorrecting {len(to_correct)} ...")
        result = push_corrections(to_correct)
        apply_locally(conn, to_correct)
        print(f"  server corrected {result['corrected']} of {result['sent']} sent")
        failures += len(result["errors"])
        for err in result["errors"][:10]:
            print(f"  ERROR {err}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

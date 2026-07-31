#!/usr/bin/env python3
"""Withdraw the published Form D rows that are not money raised.

    python3 correct_form_d_overcount.py --dry-run     # counts only, writes nothing
    python3 correct_form_d_overcount.py               # apply

A Form D reports an "amount sold". Three kinds of filing report an amount sold
that is not a company raising money to spend, and all three were published as
funding:

  1. **A business combination.** The issuer answered YES to "is this offering
     being made in connection with a business combination transaction", and the
     amount is the value of shares handed to the target's owners rather than
     cash coming in. Danaher acquiring Masimo at $180/share was published as
     Masimo raising $9.90bn; a merger of W.D. Company into Dillard's, priced off
     a closing share price with no cash anywhere in the filing, as $2.39bn.
     `ISBUSINESSCOMBINATIONTRANS` is in the dataset and the collector never read
     it.
  2. **A continuous offering with no cap.** `TOTALOFFERINGAMOUNT` is the word
     "Indefinite", the issuer says the offering has run more than a year, and
     the filing is an annual amendment restating everything sold since the first
     sale — OPTCAPITAL LLC's fourteenth amendment, first sale 2012, published as
     a $1.77bn round.
  3. **A restatement of an offering already published.** A Form D amendment
     carries the running total for the SAME offering, so an offering published
     twice is the same money counted twice: Fluidstack's January D at $450M and
     its May D/A at $842M are one raise, and the D/A is the whole of it.

The rules are written narrow, and what each one costs is measured rather than
assumed — see docs/TECHLOG.md. The one that matters: a cash placement that
happens to FUND an acquisition is a real raise and must survive, so rule 1
spares any filing whose own clarification says the proceeds were used, and seven
rows totalling $0.75bn are kept on exactly that basis.

**Withdrawn, not corrected.** The stored figure is what the filing says; what is
wrong is that the figure is money raised at all. There is no smaller true number
to revise it to, and inventing one is the thing this tracker exists not to do.
Nothing is deleted: `retract.py` marks the row not-current with the reason, so
the corrections log can still count it.

**Rule 3 keeps the LATEST filing, not the first and not the sum.** An amendment
restates the offering's running total, so the last filing for an offering is the
whole raise and every earlier one is that same money over again. Measured: in 65
of the 66 offerings this touches the latest figure is also the largest, and the
one exception is an issuer revising its own total down.

Idempotent and safe to interrupt: every withdrawal is an independent operation
against `is_current = 1`, and a row already withdrawn is skipped on a re-run.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import io
import os
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import requests

import retract
from collectors import sec_form_d_bulk as bulk
from pipeline import publish, schema

COLLECTORS = ("sec_form_d", "sec_form_d_bulk")
CACHE = Path(os.environ.get("FORM_D_CACHE", "data/.cache/form-d"))

#: How a published row is joined back to its filing. Both Form D collectors
#: store the filing's own primary_doc.xml, and the 18-digit path segment is the
#: accession number without its dashes.
ACCESSION = re.compile(r"/(\d{18})/primary_doc\.xml")

#: The clarification wording that RESCUES a business-combination filing: the
#: issuer is saying cash came in and was then spent on a deal. Written from the
#: filings themselves rather than invented — every phrase here appears in one.
CASH_RAISE = re.compile(
    r"proceeds\s+(?:of|from)?[^.]{0,80}?\b(?:used|being\s+used|applied)"
    r"|\b(?:funds|proceeds)\s+(?:are|is|was|were)\s+(?:being\s+)?used\s+(?:to|as|for)"
    r"|\bmade\s+to\s+(?:partially\s+|help\s+)?fund\b"
    r"|\bcash\s+purchase\s+price\b"
    r"|\bfinancing\s+closed\b|\(pipe\)\s+financing", re.I)

MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}

#: A pass that withdraws more than this is a broken download reading as "none of
#: it qualifies", not a result. Measured at ~11% of the published Form D rows.
MAX_WITHDRAWAL_SHARE = 0.30

#: The host fell over twice on 2026-07-30. Withdrawals go one at a time with a
#: pause, and a run of consecutive failures stops the pass instead of hammering
#: a host that is already down. retract.retract_remote retries 5xx on its own.
PAUSE_SECONDS = float(os.environ.get("RETRACT_PAUSE", "0.6"))
CONSECUTIVE_FAILURE_LIMIT = 5

REASONS = {
    "bizcomb": "the filing states this offering was made in connection with a "
               "business combination, so the amount is securities issued as "
               "consideration rather than money raised",
    "continuous": "an uncapped continuous offering: the amount is everything "
                  "sold since the first sale years earlier, not a round",
    "superseded": "a later amendment to the same offering carries the whole "
                  "running total, so this row is that money counted twice",
}


class Unsafe(RuntimeError):
    """The archives disagree with the published data so violently that a bad
    download is likelier than a bad corpus."""


# --------------------------------------------------------------------- facts


def _quarter(published_date: str) -> str:
    year, month = published_date[:4], int(published_date[5:7])
    return f"{year}q{(month + 2) // 3}"


def _archives(quarter: str, *, timeout: int = 300) -> list[bytes]:
    """One quarter's Form D archives, cached. Same cache correct_form_d.py uses."""
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


def offering_facts(quarters: set[str]) -> dict[str, dict]:
    """The raw Form D fields, per accession, for the quarters given.

    Read through the collector's own `_rows`, so this and the collector cannot
    disagree about what a column is called or how the archive is laid out.
    """
    facts: dict[str, dict] = {}
    for quarter in sorted(quarters):
        print(f"  reading {quarter} ...", flush=True)
        try:
            blobs = _archives(quarter)
        except bulk.DatasetError as exc:
            # The CURRENT quarter has no data set until it ends, and rows filed
            # inside it are always the newest ones. Skipping is right and must
            # not be a failure; every row in that quarter simply stays live, and
            # main() prints how many and how much money that is.
            print(f"    no data set yet ({exc}); those rows stay published", flush=True)
            continue
        for blob in blobs:
            archive = zipfile.ZipFile(io.BytesIO(blob))
            subs = {r["ACCESSIONNUMBER"]: r for r in bulk._rows(archive, "FORMDSUBMISSION.TSV")}
            offs = {r["ACCESSIONNUMBER"]: r for r in bulk._rows(archive, "OFFERING.TSV")}
            issuers: dict[str, dict] = {}
            for r in bulk._rows(archive, "ISSUERS.TSV"):
                if r.get("IS_PRIMARYISSUER_FLAG") == "YES" or r["ACCESSIONNUMBER"] not in issuers:
                    issuers[r["ACCESSIONNUMBER"]] = r
            for accession, sub in subs.items():
                off = offs.get(accession) or {}
                iss = issuers.get(accession) or {}
                facts[accession.replace("-", "")] = {
                    "filing_date": (sub.get("FILING_DATE") or "").strip(),
                    "file_num": (sub.get("FILE_NUM") or "").strip(),
                    "cik": (iss.get("CIK") or "").strip(),
                    "is_bizcomb": (off.get("ISBUSINESSCOMBINATIONTRANS") or "").strip(),
                    "bizcomb_note": (off.get("BUSCOMBCLARIFICATIONOFRESP") or "").strip(),
                    "total_offering": (off.get("TOTALOFFERINGAMOUNT") or "").strip(),
                    "more_than_one_year": (off.get("MORETHANONEYEAR") or "").strip(),
                    "sale_date": (off.get("SALE_DATE") or "").strip(),
                }
    return facts


def filed_on(fact: dict) -> datetime.date | None:
    """DD-MMM-YYYY, which is how the bulk submission file dates a filing."""
    value = fact.get("filing_date") or ""
    try:
        return datetime.date(int(value[-4:]), MONTHS[value[3:6].upper()], int(value[:2]))
    except (ValueError, KeyError, IndexError):
        return None


def first_sale_on(fact: dict) -> datetime.date | None:
    value = fact.get("sale_date") or ""
    try:
        return datetime.date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------- rules


def is_business_combination(fact: dict) -> bool:
    """Rule 1. YES to the business-combination question, and the filing does not
    itself say the proceeds were cash that was then spent."""
    if fact.get("is_bizcomb", "").strip().lower() != "true":
        return False
    return not CASH_RAISE.search(fact.get("bizcomb_note") or "")


#: A year, because "Indefinite" plus "this offering will last more than a year"
#: is the shape of a fresh evergreen fund as well as of a decade-old one. The
#: gap between the first sale and THIS filing is what separates them, and 138
#: rows are spared by it - Harvey AI's $200m among them.
CONTINUOUS_MIN_AGE_DAYS = 365


def is_continuous_offering(fact: dict) -> bool:
    """Rule 2. An uncapped offering that has been selling for over a year, so
    the amount sold is a cumulative total rather than a round."""
    if (fact.get("total_offering") or "").strip().lower() != "indefinite":
        return False
    if (fact.get("more_than_one_year") or "").strip().lower() != "true":
        return False
    filed, first_sale = filed_on(fact), first_sale_on(fact)
    return bool(filed and first_sale and (filed - first_sale).days >= CONTINUOUS_MIN_AGE_DAYS)


def offering_key(fact: dict) -> tuple[str, str]:
    """One offering. The SEC file number is the offering's own identifier and it
    survives every amendment, so it — and not the issuer, and not the company
    name — is what makes two filings the same money.

    Grouping on the ISSUER instead would have merged Fluidstack's January
    offering with the entirely separate one it opened in June, and deleted a
    real $730m raise.
    """
    return (fact.get("cik", "").strip(), fact.get("file_num", "").strip())


def superseded(rows: list[dict], facts: dict[str, dict]) -> list[dict]:
    """Rule 3. Every published row for an offering except the last one filed."""
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        groups[offering_key(facts[row["accession"]])].append(row)
    out = []
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda r: (filed_on(facts[r["accession"]])
                                               or datetime.date(1900, 1, 1), r["accession"]))
        out.extend(ordered[:-1])
    return out


# ----------------------------------------------------------------- the corpus


def live_rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = []
    for row in conn.execute(
        "SELECT signal_id, content_hash, company, published_date, funding_amount_usd, "
        "       source_url, collector "
        "FROM signals WHERE collector IN (?, ?) AND is_current = 1 "
        "  AND funding_amount_usd IS NOT NULL ORDER BY row_id",
        COLLECTORS,
    ):
        row = dict(row)
        match = ACCESSION.search(row["source_url"] or "")
        row["accession"] = match.group(1) if match else None
        rows.append(row)
    return rows


def to_withdraw(rows: list[dict], facts: dict[str, dict]) -> dict[str, list[dict]]:
    """The three buckets, in rule order. A row can only be in one: a row already
    withdrawn for being a merger is not also counted as a duplicate."""
    joined = [r for r in rows if r["accession"] in facts]
    bizcomb = [r for r in joined if is_business_combination(facts[r["accession"]])]
    continuous = [r for r in joined if r not in bizcomb
                  and is_continuous_offering(facts[r["accession"]])]
    taken = {id(r) for r in bizcomb} | {id(r) for r in continuous}
    survivors = [r for r in joined if id(r) not in taken]
    return {"bizcomb": bizcomb, "continuous": continuous,
            "superseded": superseded(survivors, facts)}


def money(rows) -> int:
    return sum(int(r["funding_amount_usd"] or 0) for r in rows)


# ------------------------------------------------------------------ applying


def withdraw(conn, buckets: dict[str, list[dict]]) -> int:
    """One request each, paced, and a stop rather than a hammer.

    A withdrawal that fails leaves a record live on a page that says it is not
    there, so failures are counted and reported; but a HOST that has fallen over
    answers every request the same way, and going on is how a 90-minute run
    turns into 300 failed retractions.
    """
    failures = consecutive = 0
    for name, rows in buckets.items():
        if not rows:
            continue
        print(f"\nwithdrawing {len(rows)} ({name}) ...", flush=True)
        for n, row in enumerate(rows, 1):
            try:
                retract.retract_remote(row["signal_id"], REASONS[name])
                retract.retract_local(conn, row["signal_id"], REASONS[name])
                consecutive = 0
            except (publish.PublishError, requests.RequestException) as exc:
                failures += 1
                consecutive += 1
                print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)
                if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                    print(f"  STOPPING: {consecutive} consecutive failures. The host is "
                          f"down, not the requests. Re-queue this once it is back; "
                          f"already-withdrawn rows are skipped.", file=sys.stderr)
                    return failures
            if n % 25 == 0:
                print(f"    {n}/{len(rows)}", flush=True)
            time.sleep(PAUSE_SECONDS)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows looks wrong")
    parser.add_argument("--only", choices=sorted(REASONS),
                        help="apply one rule only, for a first pass")
    args = parser.parse_args()

    conn = schema.connect()
    rows = live_rows(conn)
    quarters = {_quarter(r["published_date"]) for r in rows if r.get("published_date")}
    print(f"{len(rows)} live Form D funding rows, ${money(rows) / 1e9:.3f}bn, "
          f"across {len(quarters)} quarter(s)\n")

    facts = offering_facts(quarters)
    joined = [r for r in rows if r["accession"] in facts]
    unjoined = [r for r in rows if r["accession"] not in facts]
    print(f"\n  joined to a filing   {len(joined):>5}   ${money(joined) / 1e9:.3f}bn")
    print(f"  no archive yet       {len(unjoined):>5}   ${money(unjoined) / 1e9:.3f}bn"
          f"   (the current quarter is not published as a data set; untouched)")

    buckets = to_withdraw(rows, facts)
    if args.only:
        buckets = {args.only: buckets[args.only]}
    total = [r for bucket in buckets.values() for r in bucket]
    share = len(total) / len(rows) if rows else 0
    print()
    for name, bucket in buckets.items():
        print(f"  {name:<12} {len(bucket):>5} rows   ${money(bucket) / 1e9:>7.3f}bn")
    print(f"  {'TOTAL':<12} {len(total):>5} rows   ${money(total) / 1e9:>7.3f}bn"
          f"   ({share:.0%} of the published Form D rows)")

    if share > MAX_WITHDRAWAL_SHARE and not args.force:
        print(f"\nREFUSING: {share:.0%} of rows look wrong and the measured figure is 11%. "
              f"A truncated or wrong-quarter archive looks exactly like this. Check "
              f"{CACHE}/ before re-running, and pass --force only if it is genuinely right.",
              file=sys.stderr)
        return 2

    for name, bucket in buckets.items():
        for row in sorted(bucket, key=lambda r: -int(r["funding_amount_usd"] or 0))[:5]:
            print(f"  [{name}] ${int(row['funding_amount_usd'] or 0):>15,}  {row['company']}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    failures = withdraw(conn, buckets)
    if failures:
        print(f"\n{failures} withdrawal(s) failed. Re-queue: applied rows are skipped.",
              file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Withdraw records that are workforce reductions, which this product does not hold.

THE BREACH
----------
"Layoffs are NOT collected here. They are read from the sibling's public API at
render time. One source of truth per fact." That rule is in CLAUDE.md, it is on
the page's own footer, and SEVEN records were live on the dashboard anyway.
Four were known:

    Atlassian        a restructuring eliminating roles, ~10% of its workforce
    Groupon          a restructuring plan, up to 400 positions globally
    IO Biotech       a restructuring and workforce reduction plan
    Lyra Therapeutics  a workforce reduction affecting substantially all
                     remaining employees

and three were not, which is why this re-reads the whole corpus rather than
the list somebody already had:

    Elastic N.V.     "expects to reduce its workforce by approximately 7%",
                     $22-25m of severance and termination benefits
    Commerce.com     (BigCommerce) a plan "to realign the Company's current
                     workforce", $13.9m primarily severance
    Verizon          "despedirá a 3,000 empleados" — from google_news, not
                     SEC, and the very row the scope guard was WRITTEN for in
                     the first place. The guard landed; nobody withdrew the
                     row it was written about.

Elastic and Commerce.com are the interesting ones. Both filings carry Item
2.05 AND a real Item 5.02 event (a Chief Product Officer leaving, a CFO taking
on COO duties), and the model read only the 5.02 — both rows say nothing more
than "reported a change in its officer or director". So the leadership event
is lost along with the reduction. That is a real cost and it is the right
trade at this size: 6 of 3,784 live sec_edgar filings announce a reduction at
all, and 2 of those carry a leadership event, so the boundary costs 0.05% of
the leadership pillar to keep a promise the page makes in writing.

ROOT CAUSE
----------
The scope guard in `validate.build_signal` read the HEADLINE. `sec_edgar` does
not have a headline: it stamps the identical synthetic string

    "<Company> 8-K filing (Item 5.02): officer or director change"

onto every document it fetches, so the guard spent every run matching a layoff
vocabulary against the collector's own boilerplate. The reduction language was
in `raw_text` — the filing body — which no arm of the guard read. The second
arm (summary + read-through, when the model chose `displacement`) was the only
thing that could ever have fired here, and it post-dates these rows.

The forward fix is a third arm, `prefilter.filing_reduction_plan`, which reads
the document. This is the backward half: the rows already stored and published.

WHY IT RE-FETCHES
-----------------
`raw_text` is a collector-only field and is never stored, so the evidence that
convicts these rows does not exist in the database. Judging them on their
stored summary would reproduce the original defect one level up — Atlassian's
summary says "elimination of certain roles", which the reduction vocabulary
does not match, and it is still a filing announcing that 10% of a workforce is
going. So the sweep goes back to the filing and asks the same question the
forward guard now asks.

It therefore runs TWO passes, and the second is the one that matters:

  1. free, every collector: the stored headline / summary / read-through,
     which is what a guard could see without the network. Cheap, and it is how
     a breach in a collector that does NOT fetch documents would surface.
  2. paid in seconds, `sec_edgar` only: re-fetch each filing and apply the
     document rule. This is the definitive test and the only one that would
     have caught Atlassian.

Nothing is deleted and nothing is edited in place. A withdrawal goes through
`retract.retract_remote` + `retract.retract_local`, exactly like every other
correction here, so the row survives with `is_current = 0` and its reason, and
the corrections log can still say what was published and when it came down.

    python correct_layoff_scope.py --dry-run
    python correct_layoff_scope.py --dry-run --stored-only   # no network
    python correct_layoff_scope.py                           # writes

Dispatch it through the queue, never directly, and pass dry_run explicitly:

    gh workflow run drain-writers.yml -f enqueue=correct-layoff-scope.yml \
      -f inputs_json='{"dry_run":"false"}' -f reason='withdraw layoff filings'
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import requests

import retract
from collectors import sec_edgar
from pipeline import prefilter, publish, schema

#: The collector whose headline is synthetic, so the only collector whose rows
#: can be judged solely by re-reading the document. Others put the publisher's
#: own headline in the row, which pass 1 already reads.
DOCUMENT_COLLECTOR = sec_edgar.COLLECTOR

REASON = ("workforce reduction: the source document announces a reduction of "
          "its own workforce, which the sibling AI Layoff Tracker holds and "
          "this product does not collect")


class Unsafe(Exception):
    """The rule matched an implausible share of the corpus."""


#: A withdrawal takes a row off the live site, so a rule that suddenly matches
#: hundreds is a broken rule rather than a discovery. MEASURED over the whole
#: corpus on 2026-07-29: 3,784 filings re-read, 0 unreadable, 6 announcing a
#: reduction — 0.16%. One percent is six times that and still two orders of
#: magnitude below a rule that has started matching the boilerplate every 8-K
#: carries.
MAX_SHARE = 0.01
MIN_ROWS = 50


def current_rows(conn, collector: str | None = None) -> list[dict]:
    sql = "SELECT * FROM signals WHERE is_current = 1"
    args: tuple = ()
    if collector:
        sql += " AND collector = ?"
        args = (collector,)
    return [dict(r) for r in conn.execute(sql, args)]


def stored_verdict(row: dict) -> str | None:
    """What the guard would say about this row WITHOUT the network.

    Calls the production predicates rather than restating them, so this cannot
    drift from what the pipeline actually enforces.
    """
    cut = prefilter.workforce_reduction_term(row.get("headline") or "")
    if cut:
        return cut
    blob = " ".join(filter(None, (row.get("summary"),
                                  row.get("talent_readthrough"))))
    return prefilter.filing_reduction_plan(blob)


def fetch_body(url: str, *, fetch=None) -> tuple[str | None, str | None]:
    """(document text, fetch error) — exactly one of the two is None.

    A fetch failure returns an ERROR rather than an empty string. A document we
    could not read is unknown, and reporting unknown as clean is the shape of
    every defect in this repo's incident log.
    """
    # Resolved at call time, not bound as a default: a default argument
    # captures the function object at import and no test can replace it.
    fetch = fetch or sec_edgar.fetch_text
    try:
        body = fetch(url)
    except Exception as exc:  # noqa: BLE001 - one filing must not kill the sweep
        return None, f"{type(exc).__name__}: {exc}"[:160]
    if not body:
        return None, "empty document"
    return body, None


def sweep(conn, *, stored_only: bool = False, limit: int | None = None,
          cache_path: Path | None = None, fetch=None, progress=None) -> dict:
    """Both passes. Returns the report; writes nothing."""
    everything = current_rows(conn)
    report: dict = {
        "scanned": len(everything),
        "stored_hits": [],
        "document_hits": [],
        "unreadable": [],
        "fetched": 0,
    }

    for row in everything:
        cut = stored_verdict(row)
        if cut:
            report["stored_hits"].append((row, cut))

    if stored_only:
        return report

    # The cache holds the DOCUMENT, never the verdict. Caching the verdict
    # would mean that tuning the rule requires re-reading four thousand filings
    # off SEC, which is the kind of friction that stops a rule being tuned at
    # all. Bodies are capped at 3,000 characters by the collector, so the whole
    # corpus is a few megabytes.
    cache: dict[str, dict] = {}
    if cache_path and cache_path.exists():
        cache = json.loads(cache_path.read_text())

    documents = current_rows(conn, DOCUMENT_COLLECTOR)
    if limit:
        documents = documents[:limit]
    for n, row in enumerate(documents, 1):
        url = row["source_url"]
        entry = cache.get(url)
        if entry is None:
            body, error = fetch_body(url, fetch=fetch)
            report["fetched"] += 1
            entry = cache[url] = {"body": body, "error": error}
        body, error = entry.get("body"), entry.get("error")
        if error:
            report["unreadable"].append((row, error))
        else:
            verdict = prefilter.filing_reduction_plan(body or "")
            if verdict:
                report["document_hits"].append((row, verdict))
        if progress and n % 250 == 0:
            progress(n, len(documents))

    if cache_path:
        cache_path.write_text(json.dumps(cache, sort_keys=True))
    return report


def to_withdraw(report: dict, *, force: bool = False) -> list[tuple[dict, str]]:
    """The union of both passes, deduplicated on signal_id, blast-radius checked."""
    merged: dict[str, tuple[dict, str]] = {}
    for row, cut in report["document_hits"] + report["stored_hits"]:
        merged.setdefault(row["signal_id"], (row, cut))
    hits = list(merged.values())

    scanned = report["scanned"]
    if not force and scanned >= MIN_ROWS and len(hits) > scanned * MAX_SHARE:
        raise Unsafe(
            f"{len(hits)} of {scanned} live rows ({len(hits) / scanned:.1%}) match "
            f"the reduction rule, over the {MAX_SHARE:.0%} ceiling. That is a "
            "broken rule, not a discovery. Read the samples, then --force if it "
            "really is right.")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be withdrawn and write nothing")
    parser.add_argument("--stored-only", action="store_true",
                        help="skip the document pass — no network, no re-fetch")
    parser.add_argument("--limit", type=int,
                        help="only re-read the first N documents (for a first pass)")
    parser.add_argument("--cache", help="reuse document verdicts from this file")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share of rows match")
    args = parser.parse_args(argv)

    conn = schema.connect()

    def progress(n, total):
        print(f"  read {n}/{total} filings", flush=True)

    report = sweep(conn, stored_only=args.stored_only, limit=args.limit,
                   cache_path=Path(args.cache) if args.cache else None,
                   progress=progress)

    print(f"\nscanned {report['scanned']} live rows "
          f"({report['fetched']} filings re-read)")
    print(f"  stored text says reduction    {len(report['stored_hits']):>4}")
    print(f"  the DOCUMENT says reduction   {len(report['document_hits']):>4}")
    print(f"  documents that would not read {len(report['unreadable']):>4}")

    if report["unreadable"]:
        print("\n  UNREADABLE — these are unknown, not clean:")
        for row, error in report["unreadable"][:20]:
            print(f"    {row['company'][:40]:<40} {error}")
            print(f"      {row['source_url']}")
        if len(report["unreadable"]) > 20:
            print(f"    ... and {len(report['unreadable']) - 20} more")

    try:
        hits = to_withdraw(report, force=args.force)
    except Unsafe as exc:
        print(f"\nREFUSING: {exc}", file=sys.stderr)
        return 2

    by_collector = Counter(row["collector"] for row, _ in hits)
    published = sum(1 for row, _ in hits if row["published_at"])
    print(f"\nto withdraw: {len(hits)}  ({published} of them published, so they "
          "are live on the site right now)")
    for collector, n in by_collector.most_common():
        print(f"    {collector:<20} {n}")
    for row, cut in hits:
        print(f"\n  [{row['collector']}/{row['pillar']}] {row['company']}  ({cut!r})")
        print(f"      {row['headline'][:96]}")
        print(f"      {(row['summary'] or '')[:150]}")
        print(f"      {row['source_url']}")

    # Fail loud on a sweep that read nothing. A document pass where every fetch
    # failed looks exactly like a clean corpus, and "it went green" is how this
    # repo has lost work before.
    if not args.stored_only and report["fetched"] and not report["document_hits"] \
            and len(report["unreadable"]) == report["fetched"]:
        print("\nSTOPPING: every document fetch failed, so the document pass "
              "proved nothing.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    if not hits:
        print("\nNothing to withdraw.")
        return 0

    failures = 0
    print(f"\nwithdrawing {len(hits)} rows ...")
    for row, _cut in hits:
        try:
            # Remote first, then local: a row that came off the site but not out
            # of the database is re-found by the next run, while the reverse is
            # a row live on the site that nothing here knows to look at again.
            result = retract.retract_remote(row["signal_id"], REASON)
            local = retract.retract_local(conn, row["signal_id"], REASON)
            print(f"  withdrawn {row['company'][:40]:<40} "
                  f"wordpress={result.get('retracted')} local={local}")
        except (publish.PublishError, requests.RequestException) as exc:
            failures += 1
            print(f"  FAILED {row['company']}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{failures} withdrawal(s) failed — they are still live.",
              file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

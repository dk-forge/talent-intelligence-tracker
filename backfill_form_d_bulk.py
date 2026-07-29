#!/usr/bin/env python3
"""Backfill the funding pillar from SEC's quarterly Form D DATA SETS.

The search-based sweep (`backfill_form_d_2026.py`) works, but it pays a model
to read filings whose fields SEC already publishes as columns, and its
full-text query only matched about a sixth of the quarter's filings. This
walks the same filings from the bulk data set instead:

    zero LLM spend        the fields are columns, so the record is derived
    ~2,000 rows/quarter   against ~30 a month from the search path
    exact dollar amounts  an integer off the filing, not a rounded phrase
    back to 2008Q1        if the owner ever wants the history

Records still go through `validate.build_signal` -> `store` -> `publish`, so
every credibility guard applies unchanged: source_url is the filing's own
EDGAR page, the figure must appear in the source text, and confidence is
capped by the source rather than asserted.

Usage:
    python backfill_form_d_bulk.py --quarters 2026q1
    python backfill_form_d_bulk.py --quarters 2026q1,2026q2 --dry-run
    python backfill_form_d_bulk.py --list          # what SEC publishes today
"""

from __future__ import annotations

import argparse
import sys

import backfill_slices
from collectors import sec_form_d_bulk as bulk
from pipeline import publish, schema, store, validate

WORKFLOW = "backfill-funding-bulk.yml"

#: Quarters per slice. MEASURED: run 30413051586 did 2026q1,2026q2 in 6.8
#: minutes of job time — about 3.4 minutes a quarter, because the data set
#: publishes the fields as columns and no model is called.
#:
#: One quarter is therefore minutes, not hours, and this workflow has never run
#: long. It is sliced anyway because SEC publishes back to 2008q1 and `--
#: quarters` takes a comma-separated list: a single dispatch asking for the
#: full history is 73 quarters and four hours of held lock, and the only thing
#: currently preventing that is that nobody has typed it.
SLICE_QUARTERS = 1


def run_quarter(conn, quarter: str, *, dry_run: bool) -> dict:
    print(f"\n=== {quarter} ===")
    items = bulk.collect(quarter)
    counts = {"found": len(items), "stored": 0, "duplicate": 0,
              "rejected": 0, "skipped": 0}
    print(f"{len(items)} qualifying issuer raises in the data set")

    for item in items:
        url = item["source_url"]
        if store.already_seen(conn, url):
            counts["skipped"] += 1
            continue
        try:
            # conn: without it identity.enrich() inside build_signal is a no-op,
            # so the row lands with no ticker, type or HQ. See the note at the
            # same call in run_collect.py.
            signal = validate.build_signal(bulk.as_classified(item), item,
                                           bulk.COLLECTOR, conn=conn)
        except validate.Rejected as exc:
            counts["rejected"] += 1
            print(f"  REJECT  {item['headline'][:66]}\n          {exc}")
            if not dry_run:
                store.mark_seen(conn, url, bulk.COLLECTOR, "rejected")
            continue
        if dry_run:
            counts["stored"] += 1
            continue
        outcome = store.store(conn, signal)
        store.mark_seen(conn, url, bulk.COLLECTOR, outcome)
        if outcome == "stored":
            counts["stored"] += 1
        else:
            counts["duplicate"] += 1
        if counts["stored"] and counts["stored"] % 250 == 0:
            conn.commit()
            print(f"  ... {counts['stored']} stored")

    if not dry_run:
        conn.commit()
    print(f"{quarter}: stored={counts['stored']} duplicate={counts['duplicate']} "
          f"rejected={counts['rejected']} already-seen={counts['skipped']} "
          f"found={counts['found']}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quarters", help="comma-separated, e.g. 2026q1,2026q2")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print the quarters SEC currently publishes and exit")
    ap.add_argument("--slice", action="store_true",
                    help="do ONE bounded slice of --quarters, resuming from the "
                         "committed cursor, then stop. --quarters takes a RANGE "
                         "here (2008q1..2026q2), not a list.")
    ap.add_argument("--slice-quarters", type=int, default=SLICE_QUARTERS,
                    help=f"quarters per slice (default {SLICE_QUARTERS})")
    ap.add_argument("--emit-next", help="write the slice ticket here, for "
                                        "backfill_slices.py record")
    ap.add_argument("--state", help="slice state file (default data/backfill_state.json)")
    args = ap.parse_args()

    if args.list:
        for label in sorted(bulk.dataset_urls(), reverse=True):
            print(label)
        return 0
    if not args.quarters:
        ap.error("--quarters is required (or --list)")

    job = None
    if args.slice:
        # A range, so the job has a fixed identity across every slice of it. A
        # comma list would give each dispatch a different job id and the chain
        # would never find its own cursor.
        lo_q, _, hi_q = args.quarters.strip().lower().partition("..")
        hi_q = hi_q or lo_q
        job, window = backfill_slices.open_slice(
            workflow=WORKFLOW, unit="quarters", start=lo_q, end=hi_q,
            slice_size=args.slice_quarters, state_path=args.state,
            inputs={"dry_run": "false"})
        if window is None:
            print(f"{backfill_slices.job_id(WORKFLOW, lo_q, hi_q)} is already "
                  "complete — nothing to do.")
            return 0
        quarters = backfill_slices.slice_members(window[0], window[1], "quarters")
        print(f"SLICE {', '.join(quarters)} of {lo_q}..{hi_q} "
              f"(slice {job['slices'] + 1})")
    else:
        quarters = [q.strip().lower() for q in args.quarters.split(",") if q.strip()]

    conn = schema.connect()
    totals = {"found": 0, "stored": 0, "duplicate": 0, "rejected": 0, "skipped": 0}
    empty: list[str] = []

    # Publishing is a SEPARATE gate from collecting, and a slice must survive
    # it failing. This is not hypothetical: the first live sliced run
    # (30481065108) collected its quarter and then died inside
    # `publish.publish` because the publish guardrails held eight open
    # findings — so the ticket was never emitted, the cursor never moved, and
    # the chain stopped with nothing recorded. The rows are real either way.
    blocked = ""
    done_through = None
    dataset_error = ""
    for quarter in quarters:
        try:
            counts = run_quarter(conn, quarter, dry_run=args.dry_run)
        except bulk.DatasetError as exc:
            # Under --slice this must not abandon the quarters already done:
            # the ticket below is what records them, so the error is carried
            # to the end rather than returned from the middle.
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            dataset_error = str(exc)
            if not args.slice:
                return 1
            break
        for key, value in counts.items():
            totals[key] += value
        if counts["found"] == 0:
            empty.append(quarter)
        done_through = quarter

    print(f"\nFORM D BULK BACKFILL {','.join(quarters)}: "
          f"found={totals['found']} stored={totals['stored']} "
          f"duplicate={totals['duplicate']} rejected={totals['rejected']} "
          f"already-seen={totals['skipped']}")

    if not args.dry_run:
        store.report_health(
            conn, bulk.COLLECTOR, status="ok",
            items_found=totals["found"], items_stored=totals["stored"],
            detail=f"bulk data set backfill: {','.join(quarters)}")
        conn.commit()
        try:
            result = publish.publish(conn)
            print(f"published: {result}")
            publish.publish_health(conn)
            conn.commit()
        except publish.PublishError as exc:
            blocked = f"publish refused: {exc}"
            print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)

    if args.slice and args.emit_next and not args.dry_run:
        cursor = (backfill_slices.advance(done_through, "quarters")
                  if done_through else job["cursor"])
        backfill_slices.emit(args.emit_next, backfill_slices.slice_ticket(
            job, quarters[0], quarters[-1], next_cursor=cursor,
            totals={k: v for k, v in totals.items()},
            stopped_early=dataset_error, halt=dataset_error or blocked))
        print(f"  next cursor {cursor}")
    if dataset_error or blocked:
        return 1

    # FAIL LOUD. A quarter of Form D holds ~15,700 submissions and ~2,000
    # qualifying raises, so zero is never the market being quiet — it is the
    # archive not downloading, the columns being renamed, or SEC throttling the
    # request. The search backfill's ancestor exited 0 on five silent 403s and
    # looked exactly like a successful run (2026-07-28); this is the same trap
    # one layer up.
    if empty:
        print(f"\nSTOPPING: no qualifying filings in {', '.join(empty)}. A "
              f"quarter of Form D cannot be empty, so the data set itself did "
              f"not parse (check the archive layout and the User-Agent).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

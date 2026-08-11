#!/usr/bin/env python3
"""Benchmark-diff entry point: diff an external reference list against what we
hold, and chase what we lack to its own primary sources.

    python run_benchmark_diff.py --dry-run    # diff + chase, store nothing
    python run_benchmark_diff.py              # diff + chase + store

Ported from the sibling layoff tracker's tracker-diff loop. The reference list
arrives ONLY through two secrets:

  BENCHMARK_FEED_URLS    comma-separated URLs returning JSON [{company,...}]
                         or CSV with a company column
  BENCHMARK_COMPANIES    the list pasted inline (comma / newline separated)

DORMANT BY DEFAULT. With neither secret set this prints one line and exits 0,
so the weekly slot costs nothing, writes nothing, and the repo carries zero
benchmark data. The owner arming a secret is the only activation. Do not ask
the owner to add the secrets; dormant is a designed state, exactly as it is in
the sibling repo.

WHAT AN ARMED RUN DOES
----------------------
1. RECALL: every listed employer is checked against our stored employer keys
   (pipeline.vocab.company_key, the same normaliser the store uses). The log
   carries the percentage and the counts, never a name. When recall drops
   below BENCHMARK_RECALL_ALERT_PCT (default 90) the missing names are emailed
   to the owner through the keyed /alert route: names go to the inbox and
   nowhere else.
2. CHASE: a rotating slice of the missing list (BENCHMARK_DIFF_MAX per run,
   default 40, cursored on the calendar date so the whole backlog is walked
   across weeks) goes to collectors/benchmark_chase.py, which finds each
   employer's OWN press coverage and OWN filings and sends them through the
   ordinary run_collect path: prefilter, gate, classify, validate, both dedup
   layers, store. The reference site is never cited; the stored source is the
   publisher or the registry, as always.

WHY THE CHASE'S LOG IS REDACTED
-------------------------------
run_collect narrates candidates by headline ("REJECT <headline>", "STORE
<company> ..."). For every other collector that is the right behaviour; here a
chased headline names a list member, and the standing rule (same as the
sibling's) is that logs carry only counts and slice indices. So the chase runs
with stdout captured, and only the lines that are counts by construction are
re-emitted. tests/test_benchmark_diff.py pins that no injected name survives
into this script's output.

The gate-label ledger is switched off for the run (TIT_GATE_LEDGER=off) for
the same reason: labels are committed to the repo and carry headlines, and a
headline of a chased-but-unverified employer is list membership in a public
place. The forgone training data is a few dozen labels a week.

COST
----
$0 while dormant (one print). Armed: the diff itself is free (feeds + one
read-only SQL query), the press and filing searches are free, and paid gate /
read-through calls happen only inside the ordinary classify path, bounded by
spend.py --degrade (run first, in the workflow), TIT_PAID_READS=off,
classify.READTHROUGH_CAP and the per-run lead cap. Weekly cadence: this is a
tripwire, not a collector.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys

DORMANT_LINE = ("benchmark-diff: neither BENCHMARK_FEED_URLS nor "
                "BENCHMARK_COMPANIES is set; dormant, nothing to diff, "
                "nothing spent. Arming either secret activates it; the list "
                "itself never enters the repo.")

# Email the owner when our coverage of the reference list drops below this.
RECALL_ALERT_PCT = float(os.environ.get("BENCHMARK_RECALL_ALERT_PCT", "90") or "90")
RECALL_ALERT_MAX_NAMES = max(5, int(
    os.environ.get("BENCHMARK_RECALL_MAX_NAMES", "60") or "60"))

USER_AGENT = "TalentIntel/1.0 (info@asktherecruiter.com)"

# Lines of run_collect's narration that are safe to re-emit: they are counts,
# caps and stage summaries by construction. Everything else (candidate detail,
# REJECT/STORE/DEFER lines) is withheld because it can carry a headline, and a
# headline can carry a list member's name.
_SAFE_LINE = re.compile(
    r"^\s*$"
    r"|^\[benchmark_chase\] "
    r"|^  lead \d+/\d+: "
    r"|^DRY RUN"
    r"|^STOPPING"
)


def email_recall_gap(missing: list[str], recall_pct: float, n_total: int,
                     *, post=None) -> bool:
    """Mail the owner the employers we lack versus the reference list, so a
    coverage gap surfaces by itself. PRIVATE: names go only to the inbox via
    the keyed /alert route, never to the repo or the Actions log. Fires only
    below the threshold; a healthy week is silent. Never raises."""
    if recall_pct >= RECALL_ALERT_PCT or not missing:
        return False
    site = (os.environ.get("WP_SITE_URL") or "").rstrip("/")
    key = os.environ.get("WP_API_KEY") or ""
    if not (site and key):
        print("benchmark-diff: recall below threshold but WP_SITE_URL / "
              "WP_API_KEY are not set, so no alert can be sent")
        return False
    shown = sorted(missing, key=str.lower)[:RECALL_ALERT_MAX_NAMES]
    more = (f" (first {len(shown)} of {len(missing)})"
            if len(missing) > len(shown) else "")
    subject = (f"Benchmark recall {recall_pct}%: missing {len(missing)} of "
               f"{n_total} listed employers")
    body = "\n".join([
        "The weekly benchmark diff compared our data against the reference "
        "employer list.",
        f"We carry {n_total - len(missing)} of {n_total} ({recall_pct}%). "
        f"Missing {len(missing)}.",
        "",
        f"Employers on the list with no current row of ours{more}:",
        "  " + ", ".join(shown),
        "",
        "The chase walks these in weekly slices already. To chase them all "
        "now, open a Claude Code session in the talent-intelligence-tracker "
        "repo and paste:",
        '  "Run run_benchmark_diff.py and widen BENCHMARK_DIFF_MAX for this '
        'run; report what stored and what found no primary source."',
    ])
    if post is None:
        import requests
        post = requests.post
    try:
        resp = post(f"{site}/wp-json/talent/v1/alert",
                    json={"subject": subject, "body": body},
                    headers={"X-Talent-API-Key": key,
                             "User-Agent": USER_AGENT},
                    timeout=30)
        status = getattr(resp, "status_code", 0)
        if status == 200:
            print(f"benchmark-diff: recall gap alert sent "
                  f"({recall_pct}%, {len(missing)} missing)")
            return True
        print(f"benchmark-diff: recall alert not sent (HTTP {status})")
    except Exception as exc:  # noqa: BLE001 - an alert must not fail the run
        print(f"benchmark-diff: recall alert failed ({type(exc).__name__})")
    return False


def _run_chase(leads: list[dict], *, dry_run: bool, limit: int | None) -> int:
    """The chase, through run_collect's ordinary path, with its narration
    captured and only the count-shaped lines re-emitted (see the module
    docstring). Returns run_collect's exit code."""
    from collectors import benchmark_chase
    import run_collect

    # Labels are committed; a chased headline is list membership. See above.
    os.environ["TIT_GATE_LEDGER"] = "off"

    benchmark_chase.prepare(leads)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = run_collect.run(dry_run=dry_run, offline=False,
                                   run_index=0, limit=limit,
                                   source="benchmark_chase")
    finally:
        benchmark_chase.prepare(None)
        withheld = 0
        for line in buffer.getvalue().splitlines():
            if _SAFE_LINE.match(line):
                print(line)
            else:
                withheld += 1
        if withheld:
            print(f"benchmark-diff: {withheld} narration line(s) withheld "
                  "from this log; candidate detail can name list members, so "
                  "this log carries counts only")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff the benchmark list against our employers and chase "
                    "the gap to primary sources.")
    parser.add_argument("--dry-run", action="store_true",
                        help="diff, chase and classify, but store nothing "
                             "and send no alert")
    parser.add_argument("--limit", type=int,
                        help="cap candidates sent to the classifier")
    parser.add_argument("--max-chase", type=int,
                        help="leads chased this run (default BENCHMARK_DIFF_MAX)")
    args = parser.parse_args(argv)

    from collectors import benchmark_chase

    if not benchmark_chase.armed():
        print(DORMANT_LINE)
        return 0

    names = benchmark_chase.benchmark_names()
    if not names:
        print("benchmark-diff: the armed secrets resolved to 0 names; "
              "nothing to diff")
        return 0

    keys = benchmark_chase.our_company_keys()
    missing = benchmark_chase.missing_names(names, keys)
    recall_pct = round(100.0 * (len(names) - len(missing)) / len(names), 1)
    print(f"benchmark-diff: RECALL {len(names) - len(missing)}/{len(names)} "
          f"listed employers held ({recall_pct}%); missing {len(missing)}")

    if not args.dry_run:
        email_recall_gap(missing, recall_pct, len(names))

    if not missing:
        print("benchmark-diff: nothing to chase")
        return 0

    per_run = args.max_chase or benchmark_chase.MAX_LEADS
    sliced, idx, n_slices = benchmark_chase.todays_slice(missing, per_run)
    print(f"benchmark-diff: chasing slice {idx}/{n_slices} of "
          f"{len(missing)} missing ({len(sliced)} lead(s) this run)")

    return _run_chase([{"company": n} for n in sliced],
                      dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())

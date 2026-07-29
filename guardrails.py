#!/usr/bin/env python3
"""Read and answer the pre-publish guardrails.

    python3 guardrails.py                        # what is open, and why
    python3 guardrails.py --all                  # every finding ever, with its verdict
    python3 guardrails.py --check                # evaluate without writing (safe anywhere)
    python3 guardrails.py --live                 # also reconcile against the live /aggregate
    python3 guardrails.py --accept amount/<hash> --note "real raise, read the filing"
    python3 guardrails.py --reject vehicle_name/<hash> --note "SPV, retracting"

The guardrails themselves run on the write path (pipeline/publish.py), not here.
This is the surface a person uses to answer them, and answering is the point:
the whole design is flag-and-review rather than silent correction, because the
one thing worse than a wrong number in public is a wrong number that something
quietly fixed in a way nobody can see.

An open finding does NOT stop the pipeline. The flagged row is quarantined - held
out of the batch and out of every figure - and everything else publishes. Runs
stay green until a finding has been open past its grace window, at which point
they publish the clean rows and THEN exit non-zero. So this queue is not
urgent-by-default; it becomes urgent on a clock you can see below.

Accepting a finding is a decision that is REMEMBERED, and it releases the row:
it is still unpublished, so the next run sends it. ChangXin Memory's genuine
$8.6bn raise is accepted once and never blocks a run again. Rejecting one does
not delete anything: retract the row with `python3 retract.py <signal_id>
"why"`, which is the path that keeps the correction visible on the site.

No model is called. No network, unless you pass --live.
"""

from __future__ import annotations

import argparse
import sys

from pipeline import guardrails, schema

LIVE_AGGREGATE = ("https://asktherecruiter.com/blog/wp-json/talent/v1/aggregate")
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"


def _money(value) -> str:
    if value is None:
        return ""
    value = float(value)
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f}bn"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.1f}M"
    return f"{value:,.0f}"


def _live_span(timeout: int = 40) -> dict | None:
    """The span the live page is actually printing.

    Deliberately optional and deliberately not cache-busted: hammering the
    origin with a random query string is what shared hosting throttles.
    """
    try:
        import requests
        resp = requests.get(LIVE_AGGREGATE, headers={"User-Agent": USER_AGENT},
                            timeout=timeout)
        resp.raise_for_status()
        return (resp.json() or {}).get("span")
    except Exception as exc:
        print(f"  live check unavailable: {exc}", file=sys.stderr)
        return None


def _where(row: dict) -> str:
    """The one distinction that decides how urgent a finding is."""
    if "already_live" not in row:
        return ""
    age, grace = row.get("age_hours"), row.get("grace_hours")
    clock = "" if age is None else (
        f", RED NOW ({age:.0f}h open, window {grace}h)" if age > grace
        else f", red in {max(0.0, grace - age):.0f}h")
    if row["already_live"]:
        return f"    ALREADY LIVE on the site: quarantine cannot pull it back{clock}"
    return f"    held back, never published{clock}"


def _print_findings(rows: list[dict]) -> None:
    for row in rows:
        key = f"{row['check_name']}/{row['subject']}"
        state = row.get("state", "open")
        print(f"\n  [{state}] {key}")
        print(f"    {row.get('label') or ''}   {_money(row.get('value'))}")
        placing = _where(row)
        if placing:
            print(placing)
        detail = row.get("detail") or ""
        for line in (detail[i:i + 88] for i in range(0, len(detail), 88)):
            print(f"    {line}")
        if row.get("review_note"):
            print(f"    reviewed {row.get('reviewed_at')}: {row['review_note']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true",
                        help="every finding ever recorded, not only the open ones")
    parser.add_argument("--check", action="store_true",
                        help="evaluate the guardrails now and print, writing nothing")
    parser.add_argument("--live", action="store_true",
                        help="also read the live /aggregate and reconcile its "
                             "printed date span against the stored rows")
    parser.add_argument("--accept", action="append", default=[], metavar="KEY",
                        help="mark a finding reviewed and correct (repeatable)")
    parser.add_argument("--reject", action="append", default=[], metavar="KEY",
                        help="mark a finding reviewed and wrong (repeatable)")
    parser.add_argument("--note", default="",
                        help="why. Required with --accept or --reject.")
    parser.add_argument("--who", default="", help="who decided")
    args = parser.parse_args(argv)

    conn = schema.connect()

    if args.accept or args.reject:
        if not args.note:
            parser.error("--accept and --reject need a --note saying why")
        changed = 0
        for key in args.accept:
            n = guardrails.review(conn, key, "accepted", args.note, args.who)
            print(f"accepted {key}" if n else f"NOT FOUND {key}")
            changed += n
        for key in args.reject:
            n = guardrails.review(conn, key, "rejected", args.note, args.who)
            print(f"rejected {key}" if n else f"NOT FOUND {key}")
            changed += n
        if not changed:
            print("\nNothing matched. Keys look like 'amount/<content_hash>'; "
                  "run with no arguments to list them.", file=sys.stderr)
            return 1
        remaining = guardrails.open_findings(conn)
        print(f"\n{len(remaining)} finding(s) still open.")
        return 0

    stats = guardrails.derive_amount_threshold(guardrails.stored_amounts(conn))
    print("PRE-PUBLISH GUARDRAILS")
    print(f"  amount threshold  ${stats['threshold']:,}"
          + ("" if stats["derived"] else "   (FALLBACK, not derived)"))
    print(f"                    {stats['reason']}")
    if not guardrails.collector_patterns_available():
        print("  NOTE: the Form D collector's name patterns could not be "
              "imported, so the")
        print("        vehicle check is running narrower than the pipeline's. "
              "Use the venv.")

    live = _live_span() if args.live else None
    report = guardrails.quarantine(conn, live_span=live, write=False)

    if args.check:
        rows = report["open"]
        print(f"\n  evaluated now: {len(rows)} finding(s), nothing written")
        print(f"  would quarantine {len(report['quarantined'])} row(s), "
              f"{len(report['live'])} of them already on the site")
        if report["aggregate"]:
            print(f"  {len(report['aggregate'])} aggregate finding(s) would HALT "
                  f"publishing outright: a wrong total has no clean subset")
        _print_findings([dict(r, state="would open") for r in rows])
        return 1 if rows else 0

    if args.all:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM publish_guardrails "
            " ORDER BY state, COALESCE(value, 0) DESC")]
        print(f"\n  {len(rows)} finding(s) recorded")
        _print_findings(rows)
        return 0

    rows = report["held"] + report["live"] + report["aggregate"]
    if not rows:
        print("\n  Nothing open. Every row publishes.")
        return 0

    by_check: dict[str, int] = {}
    for row in rows:
        by_check[row["check_name"]] = by_check.get(row["check_name"], 0) + 1
    print("\n  OPEN: " + ", ".join(f"{k}={v}" for k, v in sorted(by_check.items())))
    print(f"  QUARANTINED {len(report['held']) + len(report['live'])} row(s) "
          f"({len(report['held'])} held back, {len(report['live'])} already live). "
          f"Every other row publishes normally.")
    if report["aggregate"]:
        print(f"  {len(report['aggregate'])} aggregate finding(s) are HALTING "
              f"every publish: the set does not add up, so there is no clean "
              f"subset to send.")
    if report["overdue"]:
        print(f"  {len(report['overdue'])} finding(s) are PAST their grace "
              f"window, so runs now exit non-zero after publishing clean rows.")
    else:
        print("  Nothing is overdue yet, so runs are still green.")
    _print_findings(rows)
    print("\n  Accept one:  python3 guardrails.py --accept <key> --note 'why'")
    print("               (accepting releases the row: it publishes next run)")
    print("  Reject one:  python3 guardrails.py --reject <key> --note 'why'")
    print("               then  python3 retract.py <signal_id> 'why'")
    return 1


if __name__ == "__main__":
    sys.exit(main())

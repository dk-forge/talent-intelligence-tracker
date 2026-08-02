#!/usr/bin/env python3
"""Report what we have actually spent, and enforce a ceiling.

Every cost figure in this project has been arithmetic from published prices.
OpenRouter's key endpoint reports real usage against the real limit, so this
turns the budget from a forecast into a measurement — and, with --enforce, into
something the pipeline cannot exceed.

    python spend.py             # report
    python spend.py --degrade   # exit 0, but switch PAID reads off when over
    python spend.py --enforce   # exit 1 if over the monthly allowance

THE CEILING DEGRADES, IT DOES NOT HALT (2026-07-30)
---------------------------------------------------
`--enforce` stopped the WHOLE collect job at 90% of the allowance, which is how
2026-07 ended: $9.47 of $10 and every job red from 21:47 onward. But most of
what this product collects costs nothing at all — the SEC, UK pay-gap, ATS,
BSE, EDINET and DART collectors derive every field from a column and call no
model; `pipeline/cheap_extract.py` closes records from stated text for $0; and
the free prefilter, the story clustering and both dedup layers keep working
whatever the balance says. Halting all of that to protect a budget none of it
spends is a self-inflicted outage.

So `--degrade` is what the collect jobs run. It never fails the job. When the
month's spend is past the ceiling it sets `TIT_PAID_READS=off` in the job's
environment, `pipeline/classify.py` refuses the gate and the read-through, and
every candidate that would have cost money defers UNMARKED — so it is read next
month rather than lost. The run keeps its free rows and says plainly, in the
step log and in the health ledger, that it is running degraded.

`--enforce` is kept for a human asking "should I be spending right now" and for
`tripwire.yml`, whose entire purpose is a paid query: there is no degraded mode
for a job that does nothing else.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

KEY_URL = "https://openrouter.ai/api/v1/auth/key"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# The budget the owner set. Kept here rather than in a secret so it is
# reviewable in a diff — it is a policy, not a credential.
#
# 10.0 -> 25.0 on 2026-07-30, then -> 5.0 on 2026-07-31. Both by the owner, and
# the second is the one that stands: "it's supposed to be $5 a month for open
# credit remember?", "well close to $5 a month". The $25 was said while scoping
# worldwide coverage and this file kept it for a day after the owner had gone
# back to $5 — so every cost decision in that window was measured against a
# ceiling five times too high, and a "two weeks of runway left" warning was
# raised on the strength of it that was never true.
#
# RAISED 5.0 -> 10.0 on 2026-08-01, by the owner: "Fine go to $10 for now and
# we'll figure this out later - I just want both running at 100% asap". This is
# an interim number, not the target: the owner's standing goal is still ~$5, and
# docs/PLAN-gate-to-five-dollars.md is still the route there.
#
# What $10 does and does not buy, from cost_projection.py rather than opinion:
# the LLM gate costs $4.41/month whatever else happens, so $10 leaves ~$5.59 for
# read-throughs against a full-coverage cost of $49.14. That is far better than
# $0.59 and it is still rationing. Do not read $10 as "coverage is now funded".
#
# The $5 that was here is a HARDER constraint than the coverage currently costs, and that is the
# point: it is the target the architecture is being rebuilt to meet, not a
# description of today. What $5 does and does not buy is arithmetic, not
# opinion: run `python cost_projection.py`. Do not raise this to make a red
# run green — degrading is the designed response, and the free collectors,
# the free prefilter and both dedup layers do not spend a cent.
MONTHLY_ALLOWANCE_USD = 10.0

# Stop collecting with headroom left, so a long run cannot overshoot mid-batch.
STOP_AT_FRACTION = 0.9

# The environment variable a degraded run sets. Read by pipeline/classify.py,
# which is the only place that can spend.
PAID_READS_ENV = "TIT_PAID_READS"


def fetch() -> dict:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    resp = requests.get(
        KEY_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("data") or {}


SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "data", "spend_month.json")


def month_delta(lifetime_used: float) -> tuple[float, str]:
    """This calendar month's spend, measured as a delta from a committed
    month-start snapshot of the key's LIFETIME usage.

    The /auth/key `usage` figure never resets: it is cumulative for the key's
    lifetime. Enforcing the monthly allowance directly against it meant the
    guard tripped permanently once lifetime spend crossed 90% of one month's
    budget — at ~$3/month, autonomous collection would have died forever in
    month three, surfacing only as red runs (audit 2026-07-28, finding 5).

    The snapshot file rides the same commit-the-database step the workflow
    already has, so it persists across runners. First run of a new month
    rewrites it; a missing or corrupt file is treated as "month starts now",
    which under-counts one partial month rather than halting collection.
    """
    import datetime
    import json as _json

    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    snap = {}
    try:
        with open(SNAPSHOT_PATH) as fh:
            snap = _json.load(fh) or {}
    except (OSError, ValueError):
        snap = {}

    if snap.get("month") != month or not isinstance(snap.get("usage_at_start"), (int, float)):
        snap = {"month": month, "usage_at_start": lifetime_used}
        try:
            os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
            with open(SNAPSHOT_PATH, "w") as fh:
                _json.dump(snap, fh)
        except OSError:
            pass  # read-only checkout: enforce on the in-memory value anyway

    return max(0.0, lifetime_used - float(snap["usage_at_start"])), month


def degrade(over: bool) -> None:
    """Switch PAID reads off for the rest of the job, and say so.

    Writes to `$GITHUB_ENV` so the setting reaches every later step of the same
    job, which is how a workflow passes a decision along. Outside Actions there
    is no such file and this only prints — a local `--degrade` run reports the
    verdict rather than silently changing a shell it does not own.
    """
    if not over:
        print("\n  Paid reads: ON. Within the allowance.")
        return

    print("\n  DEGRADED: paid reads are OFF for the rest of this job.")
    print("  The free collectors, the free prefilter, deterministic extraction")
    print("  and both dedup layers keep running. Every candidate that would")
    print("  have cost money defers UNMARKED and is read on a later run, so")
    print("  this costs depth for the rest of the month, never coverage.")

    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        print(f"  (no $GITHUB_ENV here — set {PAID_READS_ENV}=off yourself to "
              "reproduce this locally)")
        return
    try:
        with open(github_env, "a") as fh:
            fh.write(f"{PAID_READS_ENV}=off\n")
    except OSError as exc:
        # Loud, and still exit 0. A job that could not write the flag will
        # spend, which is the old behaviour and the safe direction to fail:
        # the key's own hard cap is still underneath it.
        print(f"  COULD NOT SET {PAID_READS_ENV}: {exc} — this job will spend "
              "as normal, and the key's hard cap is the remaining backstop")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report and enforce LLM spend.")
    parser.add_argument("--enforce", action="store_true",
                        help="exit non-zero when the allowance is exhausted")
    parser.add_argument("--degrade", action="store_true",
                        help="always exit 0; switch paid reads off when over the "
                             "allowance, leaving the free collectors running")
    args = parser.parse_args()

    d = fetch()
    used = float(d.get("usage") or 0)
    limit = d.get("limit")
    remaining = (float(limit) - used) if limit is not None else None
    spent_this_month, month = month_delta(used)

    print("=" * 56)
    print("LLM SPEND")
    print("=" * 56)
    print(f"  spent on this key   ${used:,.4f} (lifetime)")
    print(f"  spent in {month}    ${spent_this_month:,.4f}")
    if limit is None:
        print("  key limit           none set  <- a runaway run has no backstop")
    else:
        pct = 100 * used / float(limit) if float(limit) else 0
        print(f"  key limit           ${float(limit):,.2f}  ({pct:.1f}% used)")
        print(f"  remaining on key    ${remaining:,.4f}")
    print(f"  monthly allowance   ${MONTHLY_ALLOWANCE_USD:,.2f} (policy, in spend.py)")
    if d.get("is_free_tier"):
        print("  tier                free")

    problems = []
    if limit is None:
        problems.append("no hard cap on the key — set one in the OpenRouter dashboard")
    elif remaining is not None and remaining <= 0:
        problems.append("key limit reached: collection will fail with 402")
    elif remaining is not None and remaining < 1:
        problems.append(f"under $1 left on the key (${remaining:.2f})")

    over = spent_this_month >= MONTHLY_ALLOWANCE_USD * STOP_AT_FRACTION
    if over:
        problems.append(
            f"this month's spend ${spent_this_month:.2f} is at or past "
            f"{int(STOP_AT_FRACTION*100)}% of the ${MONTHLY_ALLOWANCE_USD:.0f} allowance"
        )

    print()
    if problems:
        for p in problems:
            print(f"  ACTION NEEDED: {p}")
    else:
        print("  Within budget.")

    # Degradation first: when both flags are given, the softer one wins, so a
    # workflow that gains --degrade without losing --enforce cannot go red by
    # accident.
    if args.degrade:
        degrade(over)
        return 0

    # Enforcement is deliberately a hard stop, not a warning. A budget that only
    # warns is a forecast; this makes it a fact. It is now used only where there
    # IS nothing else to run — see the module docstring.
    if args.enforce and over:
        print("\nSTOPPING: spend ceiling reached. Collection will not run.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

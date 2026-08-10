#!/usr/bin/env python3
"""Report what we have actually spent, and enforce a ceiling.

Every cost figure in this project has been arithmetic from published prices.
OpenRouter's key endpoint reports real usage against the real limit, so this
turns the budget from a forecast into a measurement — and, with --enforce, into
something the pipeline cannot exceed.

    python spend.py             # report
    python spend.py --degrade   # exit 0, but switch PAID reads off when over
    python spend.py --gate      # exit 0, and tell the workflow whether it is over
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

`--enforce` is kept for a human asking "should I be spending right now" from a
shell, where a non-zero exit is the answer and nothing downstream reads it.

AND A CEILING THAT BINDS IS NOT A DEFECT (2026-08-06)
----------------------------------------------------
`tripwire.yml` ran `--enforce`, because a job whose only action is a paid query
has no degraded mode. True, and beside the point: the QUESTION had no degraded
answer, but the EXIT CODE still turned a correct, expected, recurring budget
stop into a red workflow. On 2026-08-06 the month stood at $10.08 of $10, the
tripwire went red, its queue ticket was filed as `failed`, drain-writers then
reported "NEW items that need a human" — and the owner got two failure emails
for one event that was the budget working.

So the tripwire asks with `--gate`: exit 0 either way, print the same report,
emit a `::notice::` naming the spend and the allowance when the ceiling binds,
and answer the workflow through `$GITHUB_OUTPUT` as `over=true|false` so the
steps that would spend can skip themselves. Nothing about the ceiling moves:
the allowance is the same $10, STOP_AT_FRACTION is the same 0.9, and a gated
run spends exactly $0. What changes is that stopping is reported as stopping
rather than as breakage. A real tripwire fault — an unreachable model, a crash,
a bad key — is still loudly red, because that is `run_tripwire.py`'s own exit
code and this flag never touches it.
"""

from __future__ import annotations

import argparse
import datetime
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


# ---------------------------------------------------------------------------
# Forward-first: who gets the allowance BEFORE it runs out
# ---------------------------------------------------------------------------
#
# The owner's policy, 2026-08-10. `--degrade` answers "is the month spent",
# which is a question about a total. It cannot answer "who should get the
# money first", and until now nothing did: a walk over 2024 and the live
# collectors drew on one key at the same 90% line, so whoever ran earlier in
# the month won. That ordering was an accident of dispatch times.
#
#   Paid processing prioritizes FORWARD_FROM onward. Paid extraction and
#   discovery for windows BEFORE that date are deferred until the owner opts
#   in. Forward accuracy is what a reader uses; history is the expensive tail.
#
# THREE THINGS THIS DOES NOT DO, each of them load-bearing:
#
#  1. It does not touch CORRECTNESS. Retractions, corrections and guardrail
#     work on rows ALREADY PUBLISHED are not deferred, at any date. Those run
#     from correct-*.yml, retract.yml and the guardrail path, none of which
#     walks a window or reads TIT_BACKFILL_START, so none of them can be
#     switched off here. `tests/test_forward_first.py` pins that.
#  2. It does not stop FREE historical work. Fetching, registries,
#     deterministic parsing, validation and dedup cost nothing and keep
#     running: this switches PAID reads only.
#  3. It does not stop FREE FORWARD collection when the cap is reached. That
#     is `--degrade`'s existing behaviour and it is unchanged.
#
# It is a pause, not a teardown. No collector is retired, no cursor is reset.
# A walker that defers records `stopped_early` and a `next_cursor`, so the
# next funded run resumes on the first window it did not do -- machinery that
# already exists and is already tested (tests/test_backfill_pace.py).
FORWARD_FROM = "2026-01-01"

# The date the policy was adopted, and the clock a pause is measured against.
POLICY_ADOPTED = "2026-08-10"

# The window start a walker was dispatched with. Workflows pass their `start`
# input through as this, which is the only thing this module needs to know to
# tell forward work from historical work.
BACKFILL_START_ENV = "TIT_BACKFILL_START"

# How the owner opts historical paid work back in.
BACKFILL_OPT_IN_ENV = "TIT_HISTORICAL_BACKFILL"
_OPT_IN_OFF = frozenset({"", "0", "off", "no", "false", "deferred"})

# Grace after the next UTC allowance month opens before a still-deferred
# historical walk is raised as needing the owner's decision. One health-digest
# cycle: the digest is weekly, so a pause survives at most one digest after the
# month turns over before ops_status starts asking. It is not a money number.
DEFERRAL_GRACE_DAYS = 7


def historical_backfill_opted_in(env: dict | None = None) -> bool:
    """True when the owner has explicitly funded paid pre-FORWARD_FROM work."""
    src = os.environ if env is None else env
    return (src.get(BACKFILL_OPT_IN_ENV, "") or "").strip().lower() not in _OPT_IN_OFF


def forward_first_defers(env: dict | None = None) -> tuple[bool, str]:
    """(defers, why) -- must this run's PAID reads yield to forward work?

    True only for a run whose declared window starts before FORWARD_FROM and
    which has not been opted in. A run that declares no window is forward work
    by default: the live collectors declare nothing, and failing closed here
    would switch off the very thing the policy exists to protect.
    """
    src = os.environ if env is None else env
    start = (src.get(BACKFILL_START_ENV, "") or "").strip()
    if not start:
        return False, ("this run declares no backfill window, so it is forward "
                       "work and keeps the allowance")
    try:
        window_start = datetime.date.fromisoformat(start[:10])
    except ValueError:
        # Unparseable is UNKNOWN, and UNKNOWN is not a licence to defer live
        # collection. Say so and leave the allowance alone.
        return False, (f"{BACKFILL_START_ENV}={start!r} is not a date this module "
                       f"can read, so the forward-first policy made no decision "
                       f"about this run; the monthly ceiling still applies")
    if window_start >= datetime.date.fromisoformat(FORWARD_FROM):
        return False, (f"this run walks {start}, which is {FORWARD_FROM} or later, "
                       f"so it is forward work and keeps the allowance")
    if historical_backfill_opted_in(env):
        return False, (f"this run walks {start}, before {FORWARD_FROM}, and the "
                       f"owner has opted in ({BACKFILL_OPT_IN_ENV} is set), so it "
                       f"may spend")
    return True, (f"this run walks {start}, before {FORWARD_FROM}. Paid extraction "
                  f"and discovery for pre-{FORWARD_FROM} windows are DEFERRED so "
                  f"forward collection is funded first. The walk still fetches, "
                  f"parses, validates and dedups, all of which cost nothing, and "
                  f"its cursor is recorded so a funded run resumes on the first "
                  f"window it did not do. Set {BACKFILL_OPT_IN_ENV}=on to fund it.")


def deferral_review_due(adopted: str | None = None) -> str:
    """The date a still-deferred historical walk becomes a question.

    The start of the next UTC allowance month after adoption, plus the grace. A
    new month is new money, so that is the honest moment to re-decide rather
    than let a pause quietly become permanent.
    """
    d = datetime.date.fromisoformat(adopted or POLICY_ADOPTED)
    nxt = datetime.date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return (nxt + datetime.timedelta(days=DEFERRAL_GRACE_DAYS)).isoformat()


def deferral_overdue(today: str | None = None, adopted: str | None = None) -> bool:
    """True once the pause has outlived the month it was taken in, plus grace."""
    day = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return datetime.date.fromisoformat(day) >= datetime.date.fromisoformat(
        deferral_review_due(adopted))


def apply_forward_first() -> None:
    """Switch PAID reads off for a pre-FORWARD_FROM walk, and say why.

    Called on the `--degrade` path, which every paid walker already runs. It
    needs no balance reading: this is a decision about ORDER, not about a
    total, and it holds on the first day of a fresh month exactly as it holds
    on the last.
    """
    defers, why = forward_first_defers()
    print(f"\n  Forward-first: {why}")
    if not defers:
        return
    os.environ[PAID_READS_ENV] = "off"
    print(f"  DEFERRED BY POLICY. Not broken and not finished: this is a third "
          f"state. Review due {deferral_review_due()}.")
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        return
    try:
        with open(github_env, "a") as fh:
            fh.write(f"{PAID_READS_ENV}=off\n")
    except OSError as exc:
        # Loud, and still exit 0, in the same safe direction `degrade` fails:
        # the step spends as before and the key's hard cap is underneath.
        print(f"  COULD NOT SET {PAID_READS_ENV} for later steps: {exc} - they "
              f"will spend as normal; the key's hard cap is the backstop")


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


def gate(over: bool, spent: float) -> None:
    """Answer the WORKFLOW, without failing it.

    Writes `over=true|false` to `$GITHUB_OUTPUT` so a later step can skip the
    part that would spend, and emits the `::notice::` that names the numbers. A
    notice and not a warning or an error: nothing here is wrong, and this repo
    has already learned what happens to a channel that shouts about correct
    behaviour — it gets filtered, and the next real breakage goes unread.
    """
    if over:
        print(f"\n::notice::Spend gate CLOSED: ${spent:,.2f} spent this month is "
              f"at or past {int(STOP_AT_FRACTION * 100)}% of the "
              f"${MONTHLY_ALLOWANCE_USD:,.2f} monthly allowance, so this run "
              "will not buy anything. This is the ceiling working, not a "
              "failure. The allowance resets at the start of next month.")
    else:
        print(f"\n  Spend gate OPEN: ${spent:,.2f} of "
              f"${MONTHLY_ALLOWANCE_USD:,.2f} used this month.")

    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    try:
        with open(output, "a") as fh:
            fh.write(f"over={'true' if over else 'false'}\n")
    except OSError as exc:
        # Loud, and still exit 0 — but say plainly which way this fails. With
        # no output the caller's `if:` sees an empty string, which is not
        # 'true', so the paid step RUNS. That is the same safe direction
        # `degrade` fails in: the key's own hard cap is still underneath.
        print(f"  COULD NOT WRITE THE GATE OUTPUT: {exc} — the caller will "
              "read no answer and proceed as if inside the allowance; the "
              "key's hard cap is the remaining backstop")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report and enforce LLM spend.")
    parser.add_argument("--enforce", action="store_true",
                        help="exit non-zero when the allowance is exhausted")
    parser.add_argument("--degrade", action="store_true",
                        help="always exit 0; switch paid reads off when over the "
                             "allowance, leaving the free collectors running")
    parser.add_argument("--gate", action="store_true",
                        help="always exit 0; report whether the allowance is "
                             "exhausted as the step output `over`, for a job "
                             "that has no degraded mode and must not go red "
                             "for stopping")
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
    # accident. --gate is softer still, and sits alongside for the same reason.
    if args.degrade:
        degrade(over)
        apply_forward_first()
        return 0

    if args.gate:
        gate(over, spent_this_month)
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

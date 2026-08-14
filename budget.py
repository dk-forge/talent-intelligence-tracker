#!/usr/bin/env python3
"""Who gets the allowance: two pots, one of which can never be raided.

`spend.py` answers "is the month spent". That is a question about a TOTAL, and
a total cannot answer the question August actually posed, which is **whose
spend was it**. The answer, measured and written down in
docs/TECHLOG.md 2026-08-12:

    ONE-OFF (backfill walkers, tripwire, ab-models, benchmark-diff)  ~$8.87  88%
    RECURRING (collect + collect-press)                             ~$1.21  12%

Hand-dispatched catch-up work spent 88% of the month in two and a half days,
the single 90% line closed over everything, and the collectors that keep the
tracker CURRENT ran degraded from 2026-08-03 to 2026-08-12. Nine days of
staying current were paid for by three days of catching up, and nothing in the
repo could have stopped it, because there was one pot.

So there are two:

  * **COMMITTED** — the scheduled jobs that keep the tracker current. Paid
    first. Their ceiling is their OWN pot, so no amount of catch-up spending
    can degrade them. This is the whole point of the file.
  * **DISCRETIONARY** — the catch-up family: every `backfill_*` walker,
    `ab_models`, `benchmark_diff`. They spend the headroom that is left, and
    their per-run ceiling falls out of what REMAINS and how many days are left
    in the month, so late in a lean month a walker still runs, just smaller.

WHICH POT A JOB IS IN IS STRUCTURAL, NOT A LIST
-----------------------------------------------
A workflow with a live `schedule:` is committed; a `workflow_dispatch:`-only
workflow is discretionary. That rule is checkable from the workflow files
themselves (`tests/test_budget_allocator.py` checks it), which is the only kind
of classification that cannot drift: a hand-maintained list of job names goes
stale the first time somebody adds a walker, and the failure mode of a stale
list is that a new backfill lands in the pot it was supposed to be kept out of.

The declaration a RUN makes is `TIT_RUN_KIND`, exported by the workflow. A run
that declares nothing is COMMITTED, deliberately and in the same direction
`spend.forward_first_defers` fails open: the live collectors declare nothing,
and treating silence as discretionary would ration the very thing this file
exists to protect.

WHERE THE SPLIT IS MEASURED
---------------------------
`source_health.cost_usd` is a per-run cost ledger and it is already committed
to the repo, so month-to-date spend is readable OFFLINE, with no key. This
module adds `run_kind` to that row, which is what turns a total into a split.

The ledger is a FLOOR, not the total. `run_tripwire`, `ab_models` and
`run_benchmark_diff` call models without filing a priced health row, so what
the ledger attributes is always less than or equal to what OpenRouter charged.
`spend.py` has the authoritative total (the key's own usage delta) and passes
it in; the difference is UNATTRIBUTED and is charged entirely to
DISCRETIONARY. It never spills into committed, however large it gets.

That charging rule has a bias and it is deliberate: it errs toward protecting
the recurring collectors. The known unattributed jobs are the tripwire
(~$0.29/month, scheduled, so genuinely committed) and two dispatch-only jobs.
Mis-charging the tripwire's three tenths of a dollar to the catch-up pot slows
a backfill; charging it the other way would degrade the collectors, which is
the failure this module exists to prevent. When no authoritative total is
available the split is reported as a FLOOR and says so — absence of a reading
is never a pass.

THE NUMBERS, AND HOW THEY WERE DERIVED
--------------------------------------
See `MONTHLY_TARGET_COMBINED_USD` and `COMMITTED_SHARE` below. Every figure in
this file is arithmetic over a measurement that is in the repo; none of it is
a round number somebody liked.

    python3 budget.py            # the report, offline, no key
    python3 budget.py --gate     # answer a workflow: this run's ceiling
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "data", "talent_intel.db")

COMMITTED = "committed"
DISCRETIONARY = "discretionary"
KINDS = (COMMITTED, DISCRETIONARY)

#: The environment variable a workflow uses to declare which pot it spends
#: from. Unset means COMMITTED — see the module docstring on why silence must
#: fail toward the protected pot.
KIND_ENV = "TIT_RUN_KIND"


# ---------------------------------------------------------------------------
# THE TARGET, AND THIS REPO'S SHARE OF IT
# ---------------------------------------------------------------------------
#
# The owner, 2026-08-13, having read the OpenRouter bill: "keep both at steady
# state", and separately "close to $5 a month as possible" across BOTH
# trackers. The two trackers hold SEPARATE OpenRouter keys on one account, so
# each key's usage is separable, but neither repo can read the other's.
#
# WHAT WAS MEASURED, AND WHERE
#
#  * THIS repo's recurring steady state is **$0.8020/day**, read out of
#    `source_health.cost_usd` for 2026-08-13 — the first FULL day of
#    un-degraded running since the guard tripped on 08-03. Eight priced runs:
#    collect at 08:07 ($0.2219) and 19:31 ($0.2070), collect-press at 12:26
#    ($0.2011) and 22:15 ($0.1720). No backfill ran that day, so it is the
#    committed set alone. = $24.06 per 30 days.
#  * The SIBLING's steady state is **$0.26/day** (~$7.80/30d). That figure is
#    the owner's own reading of the shared account over 2026-08-07..08-10, and
#    it is usable as a per-tracker number for exactly one reason: across that
#    window THIS key's lifetime usage did not move at all (unchanged at
#    $26.9480 from 08-03 to 08-12, TECHLOG 2026-08-12). A window in which this
#    tracker spent nothing measures the other one cleanly.
#  * Combined natural demand is therefore **$1.062/day, $31.86/30d**, of which
#    this repo is **0.8020 / 1.062 = 75.52%**.
#
# THERE IS NO OBSERVED COMBINED STEADY STATE, and that is worth saying before
# the number is used. Every window the owner measured had one tracker at zero
# or the other spiking: $0.26/day was this tracker switched off, and the
# 08-12/08-13 spikes were it switching back on. So the combined target is a
# POLICY CHOICE, not a measurement to return to.
#
# THE SHARE DERIVATION IS RETIRED, AND WHY IT FAILED IS THE POINT
# ---------------------------------------------------------------
# Until 2026-08-13 this file derived its own ceiling as $8.00 combined x
# 75.52% = $6.04, where 75.52% was the measurement above. The arithmetic was
# sound and the answer was still wrong, because the OTHER half of it was an
# assumption about a repository this one cannot read: $6.04 here IMPLIED
# $1.96 for the AI Layoff Tracker, while that repo's own
# `railway/spend.py` said $7.00. The two ceilings summed to $13.04 against a
# target of $8.00, and no session on either side could see it, because each
# repo held a share and neither held the whole.
#
# A share is only a budget if somebody enforces the denominator. Nobody does.
# So both halves are STATED here now, and the sum is written down where the
# next session reads it instead of re-deriving a fraction from a stale guess
# about the other tracker.
#
# THE OWNER'S DECISION, 2026-08-13: **$22.00 combined per month across both
# trackers — $14.00 for the AI Layoff Tracker, $8.00 for this one.** It is a
# raise on both sides and it was taken with the measurement in hand: this
# repo's committed collectors MEASURE $24.06/month at today's read caps
# (cost_projection.py [4]), so $8.00 still does not buy today's depth and the
# route under it is docs/PLAN-gate-to-five-dollars.md rather than a bigger
# number here.
MONTHLY_TARGET_COMBINED_USD = 22.00

#: The AI Layoff Tracker's half, STATED and not derived. This repo can neither
#: read nor set `railway/spend.py` over there, so this constant is a written
#: assumption rather than a reading — but it is written down, which is exactly
#: what the retired share derivation was not. If the sibling's own number ever
#: stops being 14.00, the combined target is not met however well this file
#: behaves, and the discrepancy is at least visible in one place.
SIBLING_ALLOWANCE_USD = 14.00

#: This repo's half of that stated total: $22.00 - $14.00 = **$8.00**. Written
#: as the subtraction so the whole and the other half are both in the reader's
#: eye-line, and so a future edit cannot move one side without the sum moving
#: with it.
#:
#: The literal lives in `spend.py` (one file owns the policy number, and
#: ops_status parses it there); this is the derivation of it.
DERIVED_ALLOWANCE_USD = round(MONTHLY_TARGET_COMBINED_USD - SIBLING_ALLOWANCE_USD, 2)

#: KEPT AS A MEASUREMENT, NO LONGER USED AS A LEVER. This repo's share of
#: measured combined demand on 2026-08-13, 0.8020 / 1.0620. It still describes
#: where the money goes; it no longer decides where the ceiling sits, for the
#: reason in the block comment above.
THIS_REPO_SHARE = 0.7552


# ---------------------------------------------------------------------------
# THE SPLIT BETWEEN THE TWO POTS
# ---------------------------------------------------------------------------
#
# Derived from demand each side has already WRITTEN DOWN, so it is checkable:
#
#   committed      $24.06/month   measured, 2026-08-13, the ledger above
#   discretionary   $3.00/month   the three walkers' OWN declared budgets:
#                                 backfill_gnews_2026.MONTHLY_WALKER_BUDGET_USD  1.00
#                                 backfill_gdelt_2026.MONTHLY_WALKER_BUDGET_USD  1.50
#                                 backfill_press_2026.MONTHLY_WALKER_BUDGET_USD  0.50
#
#   committed share = 24.06 / 27.06 = 88.91%
#
# `tests/test_budget_allocator.py` re-derives this from the walker modules, so
# a walker that changes its own budget cannot leave the split behind.
#
# NOTE WHAT THIS SPLIT DOES NOT CLAIM. $5.37/month against a MEASURED committed
# demand of $24.06/month funds 22% of today's read depth. The committed pot
# stops the collectors being starved by a BACKFILL; it does not make $6.04 buy
# what $24.06 buys. Bringing the committed set inside its pot is a read-cap
# decision (`pipeline/classify.BINDING_READ_BUDGET`, currently 217 reads/run)
# and it is the owner's, not this file's — `report()` prints the arithmetic and
# changes nothing.
WALKER_POT_TOTAL_USD = 3.00
COMMITTED_SHARE = 0.8891

#: Below this a discretionary run has no meaningful ceiling left and skips.
#: Sized at roughly one gate-plus-read of a walker's cheapest unit, so "too
#: small to bother" means literally "cannot buy one item", not a taste.
MIN_DISCRETIONARY_RUN_USD = 0.002


# ---------------------------------------------------------------------------
# Reading the policy number without importing spend.py
# ---------------------------------------------------------------------------

def monthly_allowance(path: str | None = None) -> float | None:
    """`spend.MONTHLY_ALLOWANCE_USD`, parsed rather than imported.

    spend.py imports `requests` at module scope; this module and ops_status.py
    both promise stdlib only, no network, no keys. Copying the figure here was
    the alternative and a duplicated budget is a budget that goes stale
    silently. ops_status delegates to this so there is ONE parser.

    None when it cannot be read, which prints as "no policy figure" rather
    than quietly comparing against a default nobody set.
    """
    import ast

    try:
        with open(path or os.path.join(ROOT, "spend.py")) as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "MONTHLY_ALLOWANCE_USD":
                try:
                    return float(ast.literal_eval(node.value))
                except (ValueError, TypeError):
                    return None
    return None


def pots(allowance: float) -> dict[str, float]:
    """The allowance, split. Adds up to the allowance, by construction."""
    committed = round(allowance * COMMITTED_SHARE, 4)
    return {COMMITTED: committed, DISCRETIONARY: round(allowance - committed, 4)}


# ---------------------------------------------------------------------------
# Which pot is this run spending from
# ---------------------------------------------------------------------------

def scheduled_workflows(workflows_dir: str | None = None) -> set[str]:
    """Every workflow that runs on a timer, INCLUDING the enqueued ones.

    "Has a `schedule:` in its own file" is the obvious rule and it is wrong
    here. Several database writers deliberately carry no cron of their own —
    a `schedule:` in their file would enter the `talent-collect` lock
    uncoordinated and either evict the pending run or become an unreplayable
    orphan — so `schedule-link-hygiene.yml` holds their crons and enqueues a
    ticket instead. `tripwire.yml` and `benchmark-diff.yml` are both in that
    position: dispatch-only files that nonetheless run twice a week and once a
    week respectively, which makes them stay-current work.

    So a workflow is scheduled if it has a cron, or if a workflow that HAS a
    cron names it. Getting this wrong in the safe-looking direction would have
    put the armed tripwire in the catch-up pot and slowly starved a scheduled
    job, which is the same class of bug from the other end.
    """
    import re

    directory = workflows_dir or os.path.join(ROOT, ".github", "workflows")
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".yml"))
    except OSError:
        return set()

    texts = {}
    for name in names:
        try:
            with open(os.path.join(directory, name)) as fh:
                texts[name] = fh.read()
        except OSError:
            continue

    cron = re.compile(r"^\s*-\s*cron:", re.M)
    scheduled = {n for n, t in texts.items() if cron.search(t)}

    # COMMENTS DO NOT SCHEDULE ANYTHING. `drain-writers.yml` has a cron and
    # mentions `backfill-gdelt-2026.yml` in a note about an incident; reading
    # that as a schedule would move a hand-dispatched walker into the
    # collectors' pot, which is the exact failure this file exists to stop.
    # Only a name a scheduled file actually names in its own YAML counts.
    for name in sorted(scheduled):
        code = "\n".join(line for line in texts[name].splitlines()
                         if not line.lstrip().startswith("#"))
        for other in names:
            if other != name and other in code:
                scheduled.add(other)
    return scheduled


def run_kind(env: dict | None = None) -> str:
    """This run's pot, from `TIT_RUN_KIND`.

    Anything unrecognised — unset, empty, a typo — is COMMITTED. See the
    module docstring: silence must fail toward the pot that is protected, not
    away from it.
    """
    src = os.environ if env is None else env
    value = (src.get(KIND_ENV, "") or "").strip().lower()
    return DISCRETIONARY if value == DISCRETIONARY else COMMITTED


# ---------------------------------------------------------------------------
# What has been spent, per pot
# ---------------------------------------------------------------------------

def month_bounds(today: datetime.date | None = None) -> tuple[str, int, int]:
    """(YYYY-MM, days in the month, days remaining INCLUDING today)."""
    day = today or datetime.datetime.now(datetime.timezone.utc).date()
    span = calendar.monthrange(day.year, day.month)[1]
    return day.strftime("%Y-%m"), span, span - day.day + 1


def ledger_spend(db_path: str | None = None,
                 month: str | None = None) -> dict[str, float]:
    """Month-to-date spend per pot, out of the committed cost ledger.

    Offline and keyless. Rows whose `run_kind` is NULL predate the column and
    are counted as COMMITTED — every one of them was written before any
    discretionary run declared itself, and the alternative (a third bucket
    nobody enforces against) would silently exempt them from both ceilings.
    """
    month = month or month_bounds()[0]
    out = {COMMITTED: 0.0, DISCRETIONARY: 0.0}
    try:
        conn = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        has_kind = any(r[1] == "run_kind"
                       for r in conn.execute("PRAGMA table_info(source_health)"))
        column = "run_kind" if has_kind else "NULL"
        rows = conn.execute(
            f"SELECT {column}, COALESCE(SUM(cost_usd), 0) FROM source_health "
            "WHERE cost_usd IS NOT NULL AND substr(run_at, 1, 7) = ? "
            f"GROUP BY {column}", (month,)).fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    for kind, total in rows:
        out[DISCRETIONARY if kind == DISCRETIONARY else COMMITTED] += float(total or 0)
    return {k: round(v, 6) for k, v in out.items()}


def charge(ledger: dict[str, float],
           month_total: float | None = None) -> dict[str, float]:
    """The ledger split, reconciled against an authoritative month total.

    `month_total` is the OpenRouter key's own usage delta, which `spend.py`
    has and this module does not. The excess over what the ledger attributes
    is real spend by a job that filed no priced health row (the tripwire,
    ab_models, benchmark_diff), and ALL of it is charged to DISCRETIONARY. It
    never spills into COMMITTED, even when it takes the catch-up pot deep
    negative: an unattributed dollar must not be able to degrade the scheduled
    collectors, which is the whole thesis of this module. The bias is
    deliberate and the docstring at the top of this file explains it.

    With `month_total` None the ledger stands alone and is a FLOOR. Callers
    must report it as one; `report()` does.
    """
    out = dict(ledger)
    if month_total is None:
        return out
    unattributed = round(float(month_total) - sum(ledger.values()), 6)
    if unattributed <= 0:
        return out
    out[DISCRETIONARY] = round(out[DISCRETIONARY] + unattributed, 6)
    return out


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

class Decision:
    """What one run may spend, and the sentence that says why.

    `skip` and `truncated` are different states and neither is a failure.
    A skipped run exits ZERO and says so: a non-zero exit here would
    manufacture a red run, which manufactures an alert, for a budget behaving
    exactly as designed. Only a genuine data fault is allowed to be red.
    """

    def __init__(self, *, kind, pot, spent, remaining, days_left,
                 ceiling, skip, reason, over=False):
        #: Is this run's OWN pot exhausted? A boolean, not a phrase somebody
        #: greps the reason string for — a caller matching on prose is a
        #: caller that silently starts spending when the wording is improved.
        self.over = over
        self.kind = kind
        self.pot = pot
        self.spent = spent
        self.remaining = remaining
        self.days_left = days_left
        self.ceiling = ceiling
        self.skip = skip
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (f"Decision(kind={self.kind!r}, ceiling={self.ceiling:.4f}, "
                f"skip={self.skip}, remaining={self.remaining:.4f})")


def decide(*, kind: str | None = None, allowance: float | None = None,
           charged: dict[str, float] | None = None,
           today: datetime.date | None = None,
           stop_at_fraction: float = 0.9) -> Decision:
    """This run's ceiling, from what is left and how long is left.

    COMMITTED work is measured against the COMMITTED pot alone. That single
    sentence is the fix: on 2026-08-01..03 a backfill campaign spent 88% of
    the month and the collectors were degraded for the following nine days,
    because both were measured against one total.

    DISCRETIONARY work gets `remaining / days left`, so it SLOWS as the month
    tightens rather than stopping — and reaches zero only when the pot is
    genuinely gone, which is a skip, not a failure.
    """
    kind = kind or run_kind()
    allowance = monthly_allowance() if allowance is None else allowance
    if allowance is None:
        return Decision(
            kind=kind, pot=0.0, spent=0.0, remaining=0.0, days_left=0,
            ceiling=0.0, skip=False,
            reason="UNKNOWN: the monthly allowance could not be read out of "
                   "spend.py, so no ceiling was derived and this run is not "
                   "restricted by it. That is not a pass; it is an unread "
                   "policy, and the key's own hard cap is the only backstop "
                   "left.")

    charged = ledger_spend() if charged is None else charged
    _, _, days_left = month_bounds(today)
    pot = pots(allowance)[kind]
    spent = float(charged.get(kind, 0.0))
    remaining = round(pot - spent, 6)

    if kind == COMMITTED:
        line = round(pot * stop_at_fraction, 6)
        over = spent >= line
        return Decision(
            kind=kind, pot=pot, spent=spent, remaining=remaining,
            days_left=days_left, ceiling=max(0.0, remaining), skip=False,
            over=over,
            reason=(
                f"COMMITTED work is measured against the committed pot alone: "
                f"${spent:,.4f} of ${pot:,.2f} spent, stop line ${line:,.2f}. "
                + ("PAST THE LINE, so paid reads degrade; the free collectors, "
                   "the free prefilter and both dedup layers keep running and "
                   "every deferred candidate stays UNMARKED for a later run. "
                   if over else
                   "Inside the line, so paid reads stay on. ")
                + "No amount of discretionary spending can move this number: "
                  "that is the point of the two pots."))

    per_day = remaining / max(1, days_left)
    ceiling = round(max(0.0, per_day), 6)
    skip = ceiling < MIN_DISCRETIONARY_RUN_USD
    return Decision(
        kind=kind, pot=pot, spent=spent, remaining=remaining,
        days_left=days_left, ceiling=0.0 if skip else ceiling, skip=skip,
        over=skip,
        reason=(
            f"DISCRETIONARY catch-up work spends the headroom that is left: "
            f"${spent:,.4f} of ${pot:,.2f} spent, ${remaining:,.4f} remaining "
            f"over {days_left} day(s) left in the month = ${ceiling:,.4f} for "
            f"this run. "
            + (f"That is below the ${MIN_DISCRETIONARY_RUN_USD:,.3f} floor, so "
               f"this run buys NOTHING and SKIPS. It is not broken and not "
               f"finished: the pot refills on the 1st, no cursor is reset and "
               f"nothing is marked, so the next funded run resumes on the "
               f"first window this one did not do. Exiting ZERO, because a "
               f"budget working is not a red run."
               if skip else
               "The run goes ahead SMALLER rather than not at all, and must "
               "report what the ceiling made it drop.")))


def walker_ration(*, monthly_walker_budget_usd: float, usd_per_unit: float,
                  per_slice_days: int = 1,
                  decision: Decision | None = None,
                  **kwargs) -> tuple[int, str]:
    """A backfill walker's per-slice ration, from the LIVE remaining pot.

    Each walker's own `MONTHLY_WALKER_BUDGET_USD` stops being a static monthly
    constant and becomes its SHARE of the discretionary pot
    (`WALKER_POT_TOTAL_USD` is the three of them added up). The share is then
    spread over the days actually left, which is what makes a walker slow down
    in a lean month instead of racing to the ceiling and stopping.

    Returns `(units, disclosure)`. `units` may be 0 — that is the skip, and the
    disclosure says so. Never raises on a spent pot.
    """
    decision = decision or decide(kind=DISCRETIONARY, **kwargs)
    if decision.skip or decision.ceiling <= 0:
        return 0, decision.reason
    share = monthly_walker_budget_usd / WALKER_POT_TOTAL_USD
    per_run = decision.ceiling * share
    units = int(per_run / (usd_per_unit * max(1, per_slice_days)))
    if units < 1:
        return 0, (
            f"{decision.reason} This walker's share of that is "
            f"${per_run:,.5f}, which at ${usd_per_unit:,.5f} per item over "
            f"{per_slice_days} day-window(s) does not buy one item, so this "
            f"run buys NOTHING and SKIPS. Exiting ZERO.")
    return units, (
        f"ration {units}/day-window, DERIVED from the discretionary pot as it "
        f"stands: ${decision.remaining:,.4f} left over {decision.days_left} "
        f"day(s) = ${decision.ceiling:,.4f} this run, of which this walker's "
        f"share (${monthly_walker_budget_usd:,.2f} of "
        f"${WALKER_POT_TOTAL_USD:,.2f}) is ${per_run:,.5f} at "
        f"${usd_per_unit:,.5f} per item.")


# ---------------------------------------------------------------------------
# The one line the owner reads
# ---------------------------------------------------------------------------

def status_line(*, allowance: float | None = None,
                charged: dict[str, float] | None = None,
                today: datetime.date | None = None,
                measured_total: bool = False) -> str:
    """spent / allowance / days left / projected, in one sentence.

    The owner is not a developer and this is the line he reads. It names both
    pots separately, because "spent $3 of $6" hides the only thing that
    matters here — whether the $3 came out of staying current or out of
    catching up.
    """
    allowance = monthly_allowance() if allowance is None else allowance
    if allowance is None:
        return ("BUDGET UNKNOWN: the allowance could not be read out of "
                "spend.py. This run did not establish what is funded.")
    charged = ledger_spend() if charged is None else charged
    month, span, days_left = month_bounds(today)
    pot = pots(allowance)
    spent = sum(charged.values())
    elapsed = span - days_left + 1
    projected = spent / elapsed * span if elapsed else 0.0
    return (
        f"BUDGET {month}: ${spent:,.2f} of ${allowance:,.2f} spent "
        f"(current ${charged.get(COMMITTED, 0.0):,.2f}/${pot[COMMITTED]:,.2f}, "
        f"catch-up ${charged.get(DISCRETIONARY, 0.0):,.2f}/"
        f"${pot[DISCRETIONARY]:,.2f}), {days_left} day(s) left, "
        f"projected ${projected:,.2f} for the month"
        + ("" if measured_total else
           " — from the committed cost ledger, which is a FLOOR: jobs that "
           "call a model without filing a priced health row are not in it"))


def report(allowance: float | None = None,
           charged: dict[str, float] | None = None,
           today: datetime.date | None = None,
           measured_total: bool = False) -> None:
    allowance = monthly_allowance() if allowance is None else allowance
    charged = ledger_spend() if charged is None else charged
    print("=" * 66)
    print("BUDGET — two pots, and only one of them can be raided")
    print("=" * 66)
    print("  " + status_line(allowance=allowance, charged=charged, today=today,
                             measured_total=measured_total))
    if allowance is None:
        return
    print()
    for kind in KINDS:
        d = decide(kind=kind, allowance=allowance, charged=charged, today=today)
        print(f"  [{kind}]")
        for line in _wrap(d.reason):
            print(f"      {line}")
    print()
    for line in _wrap(
            f"${MONTHLY_TARGET_COMBINED_USD:,.2f}/month across BOTH trackers, "
            f"stated by the owner and split as ${DERIVED_ALLOWANCE_USD:,.2f} "
            f"here + ${SIBLING_ALLOWANCE_USD:,.2f} for the AI Layoff Tracker. "
            f"Both halves are written down; this repo can set only its own. A "
            f"SHARE is what the two repos used to hold, and it summed to "
            f"$13.04 against a stated $8.00.", width=58):
        print(f"  {'target':13} {line}" if line.startswith("$22")
              else f"  {'':13} {line}")


def _wrap(text: str, width: int = 72) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", action="store_true",
                    help="answer the workflow: write this run's ceiling and "
                         "skip verdict to $GITHUB_OUTPUT, always exit 0")
    ap.add_argument("--kind", choices=KINDS, default=None,
                    help=f"override {KIND_ENV} for this invocation")
    args = ap.parse_args(argv)

    report()
    if not args.gate:
        return 0

    d = decide(kind=args.kind or run_kind())
    print()
    for line in _wrap(f"GATE [{d.kind}]: {d.reason}"):
        print(f"  {line}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        try:
            with open(out, "a") as fh:
                fh.write(f"kind={d.kind}\n")
                fh.write(f"skip={'true' if d.skip else 'false'}\n")
                fh.write(f"ceiling_usd={d.ceiling:.6f}\n")
                fh.write(f"remaining_usd={d.remaining:.6f}\n")
        except OSError as exc:
            # Loud, and still exit 0, in the same safe direction spend.py fails:
            # with no answer the caller's `if:` sees an empty string, the step
            # runs, and the key's own hard cap is the remaining backstop.
            print(f"  COULD NOT WRITE THE GATE OUTPUT: {exc} — the caller will "
                  "read no answer and proceed as if funded; the key's hard cap "
                  "is the remaining backstop")
    # ZERO either way. A skip is the budget working; only a data fault is red.
    return 0


if __name__ == "__main__":
    sys.exit(main())

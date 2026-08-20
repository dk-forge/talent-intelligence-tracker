#!/usr/bin/env python3
"""Weekly health digest: the alert that reaches a human.

Health has been RECORDED since day one (the source_health table, pushed to
/source-health, rendered on the sources page) and never ALERTED. A dead
collector showed up as a red Actions run and a degraded badge on a page nobody
opens. Worse failure modes produced no signal at all: a workflow disabled by
hand, or GitHub auto-disabling the cron after 60 days of no repository
activity, means no run, no red X, and a tracker that quietly stops.

So this reads the ledger, classifies every collector, and emails the owner
ONLY when something needs a human. Silence is the healthy state.

    python3 health_digest.py                # read live ledger, alert if needed
    python3 health_digest.py --dry-run      # classify and print, send nothing
    python3 health_digest.py --local        # read the committed SQLite ledger
    python3 health_digest.py --send-test    # prove the mail path end to end

Exit codes: 0 = the digest ran (whether or not it found problems)
            1 = the digest itself could not run

That is deliberately unlike the sibling tracker, which fails red on findings.
The email IS the alert here. A red weekly run on top of it only trains the
owner to ignore a red weekly run.

Env: WP_SITE_URL, WP_API_KEY (both needed to send), OPENROUTER_API_KEY
(optional, adds the spend line), HEALTH_DIGEST_TO is NOT read here: the
recipient is configured server-side so a leaked key cannot redirect mail.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Imported under an alias because `build_email` takes a parameter called
# `guardrails` (the ledger rows), and the module and the rows are both needed
# in the same scope. The alias is the honest fix; renaming the parameter would
# touch every caller and every test for a shadowing that is one line deep.
from pipeline import guardrails as guardrails_mod
from pipeline import schema

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "talent_intel.db"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# This script's own name in the ledger. It must NEVER classify itself: the
# sibling's digest marked itself degraded because another collector was
# degraded, which showed one problem as two amber lights and read as "the
# digest is broken" when it had just done its job. A digest's status describes
# whether the DIGEST ran, not what it found.
DIGEST_NAME = "health_digest"

# Reporters that are not collectors. They file into the same ledger so their
# state is visible on the health page, but they must never count towards "is
# the pipeline alive", because a weekly measurement running happily would make
# `newest_run_hours` look fresh while every real collector was dead - which is
# precisely the blind spot pipeline_stopped() exists to close. They are still
# classified for staleness and degradation like anything else.
#
# The recall entries are DERIVED from the family definitions rather than typed,
# so adding a measured population cannot leave its health entry classified as a
# collector by omission. A US recall run counted as a collector would make
# `newest_run_hours` look fresh on a week when every real collector was dead,
# which is precisely the blind spot named above.
MEASUREMENT_ONLY = {"tripwire", "link_check", "archive_sources"}
try:
    from analysis.recall import family as _recall_families
    MEASUREMENT_ONLY |= {f.health_source for f in _recall_families.ALL}
except Exception:                              # pragma: no cover - import guard
    MEASUREMENT_ONLY |= {"recall", "recall_us"}

# Statuses that are not an incident. "retired"/"disabled" are deliberate stops,
# so their old timestamp is expected and must not read as staleness either.
#
# "skipped" is NOT one of those, and the difference is the point: it means the
# collector ran and correctly declined to buy the paid part of its work, so it
# is benign — a budget stop is UNDECIDED, never a verdict — but its timestamp
# is FRESH and must keep ticking the staleness clock. Putting it in
# DELIBERATELY_STOPPED would exempt a genuinely dead job from the age check for
# ever, which is the failure this whole status exists to close.
BENIGN_STATUSES = {"ok", "retired", "disabled", "skipped"}
DELIBERATELY_STOPPED = {"retired", "disabled"}

# How long a collector may stay quiet before it counts as stale. The map
# lives in staleness.py because TWO tools judge this — ops_status.py and this
# digest — and when each carried its own numbers they disagreed about every
# collector that was not on the 2x/day cron. Re-exported here so existing
# callers of health_digest.MAX_AGE_HOURS keep reading the shared truth.
from staleness import DEFAULT_MAX_AGE_HOURS, MAX_AGE_HOURS  # noqa: E402

# The loudest check. If the NEWEST successful collect anywhere is older than
# this, the pipeline itself has stopped, which is the failure mode that
# produces no red runs to notice.
PIPELINE_STOPPED_HOURS = 36


# --------------------------------------------------------------------------
# Reading the ledger
# --------------------------------------------------------------------------

def read_live(site: str, timeout: int = 40) -> dict:
    """Collectors from the live /source-health endpoint."""
    import requests

    resp = requests.get(
        f"{site.rstrip('/')}/wp-json/talent/v1/source-health",
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    collectors = payload.get("collectors") if isinstance(payload, dict) else None
    if not isinstance(collectors, dict):
        raise ValueError("source-health returned no collectors object")
    return collectors


def read_local(db_path: Path = DB) -> dict:
    """Collectors from the committed SQLite ledger, newest run per collector."""
    if not db_path.exists():
        raise FileNotFoundError(f"no database at {db_path}")
    conn = schema.connect_ro(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT collector, status, items_found, items_stored, detail,
                      MAX(run_at) AS run_at
                 FROM source_health GROUP BY collector"""
        ).fetchall()
    finally:
        conn.close()
    return {r["collector"]: dict(r) for r in rows}


#: A snapshot older than this, with URLs still waiting, means archiving has
#: STOPPED PRODUCING rather than merely slowed. The staleness leash catches an
#: archiver that stopped RUNNING; it cannot catch one that runs green every
#: three hours and records nothing, which is exactly what happened on
#: 2026-07-30 (run 30507215991 went out with the dry_run default and nobody
#: noticed for a day). Seven days is several hundred capture attempts: a queue
#: that has not moved in that time is not being throttled, it is broken.
ARCHIVE_STALL_DAYS = 7


def read_link_health(db_path: Path = DB) -> dict | None:
    """Archive coverage and rot rate, over the scope the schedule can reach.

    Read LOCALLY even when the collector ledger comes from the site: the link
    ledger is a repo artifact, and the site never sees the un-archived tail at
    all. Never fatal — a digest that cannot read this still has collectors to
    report.

    The scoping is the whole point. `rot_summary()['archive_pct']` is over the
    whole corpus, ~96% of which is SEC and GOV.UK filings the schedule
    deliberately skips, so it reads about 0.5% on a perfectly healthy archiver.
    An email that quotes that number every week teaches its reader to ignore it.
    """
    if not db_path.exists():
        return None
    try:
        from pipeline import source_links
        conn = schema.connect_ro(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cover = source_links.archive_coverage(conn)
            cover["rot"] = source_links.rot_summary(conn)
        finally:
            conn.close()
        return cover
    except Exception:
        return None


def read_landmarks(path: Path | None = None) -> dict | None:
    """The weekly landmark result, as one summary. Never fatal.

    Read from the committed report rather than recomputed, on purpose: the
    report is the only place the LIVE lens exists, and the live lens is the one
    that answers the owner's actual question, which is whether a reader can see
    these events. Recomputing the stored lens here would produce a second
    number in the same email that disagrees with the first for a good reason
    nobody would remember.
    """
    import json

    path = path or (ROOT / "data" / "landmarks_report.json")
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text())
    except ValueError:
        return None
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    return {
        "checked_on": report.get("checked_on"),
        "version": report.get("landmarks_version"),
        "one_line": summary.get("one_line", ""),
        "total": summary.get("total", 0),
        "held": summary.get("held", 0),
        "standing_gaps": summary.get("standing_gaps", 0),
        "regressions": summary.get("regressions", 0),
        "held_not_live": summary.get("held_not_live", 0),
        "live_lens": summary.get("live_lens"),
        "regressed": [
            {"company": e.get("company"), "quarter": e.get("quarter"),
             "amount_usd": e.get("amount_usd"), "why": "; ".join(e.get("regression") or []),
             "source_url": e.get("source_url")}
            for e in (report.get("entries") or []) if e.get("regression")
        ],
        "biggest_gaps": sorted(
            [{"company": e.get("company"), "quarter": e.get("quarter"),
              "amount_usd": e.get("amount_usd") or 0, "status": e.get("status")}
             for e in (report.get("entries") or [])
             if e.get("status") != "held" and not e.get("regression")],
            key=lambda g: -float(g["amount_usd"]))[:5],
    }


def archiving_stalled(link_health: dict | None, now: datetime) -> bool:
    """Is there work outstanding that nothing has moved in a week?

    Both halves are required. No outstanding work means a quiet archiver is a
    finished one, and saying otherwise would train the owner to ignore this.
    """
    if not link_health:
        return False
    outstanding = link_health["capture_queue"] + link_health["never_probed"]
    if not outstanding:
        return False
    newest = link_health.get("newest_snapshot")
    if not newest:
        return True
    try:
        when = datetime.fromisoformat(newest)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).days >= ARCHIVE_STALL_DAYS


def read_guardrails(db_path: Path = DB) -> list[dict]:
    """Quarantined rows still waiting on a human, worst money first.

    Always read LOCALLY, even when the collector ledger comes from the live
    site: the guardrails run before publishing, so a held-back row is by
    definition one the site has never been told about. Reading it from the site
    would be asking the patient to diagnose itself.

    Each row carries `already_live` and its age against its grace window,
    because the email has to separate two very different asks. A held row is the
    guardrail WORKING and nothing is wrong in public. A live one is a wrong
    figure on the page that only a human retraction can remove.

    Never fatal. A digest that cannot read this still has collectors to report.
    """
    if not db_path.exists():
        return []
    try:
        from pipeline import guardrails
        conn = schema.connect_ro(db_path)
        conn.row_factory = sqlite3.Row
        try:
            report = guardrails.quarantine(conn, write=False)
        finally:
            conn.close()
        rows = report["held"] + report["live"] + report["aggregate"]
        return sorted(rows, key=lambda r: -(r.get("value") or 0))
    except Exception:
        return []


# --------------------------------------------------------------------------
# Classification (pure, so it is testable without a network or a database)
# --------------------------------------------------------------------------

def age_hours(run_at, now: datetime):
    """Hours since an ISO timestamp, or None if it cannot be read."""
    if not run_at:
        return None
    try:
        stamp = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds() / 3600.0


def classify(collectors: dict, now: datetime) -> dict:
    """Sort collectors into ok / degraded / stale.

    Staleness outranks status: a collector whose last run said "ok" three weeks
    ago is a stopped collector, and reporting it as fine is exactly the blind
    spot this script exists to close.
    """
    result = {"ok": [], "degraded": [], "stale": [], "unknown_age": []}

    for name in sorted(collectors):
        info = collectors[name]
        if not isinstance(info, dict):
            continue
        if name == DIGEST_NAME:
            continue  # never classify itself

        status = str(info.get("status") or "").strip().lower()
        limit = MAX_AGE_HOURS.get(name, DEFAULT_MAX_AGE_HOURS)
        age = age_hours(info.get("run_at") or info.get("checked_at"), now)

        if status in DELIBERATELY_STOPPED:
            result["ok"].append(name)
        elif age is None:
            result["unknown_age"].append((name, status))
        elif age > limit:
            result["stale"].append((name, round(age, 1), limit))
        elif status not in BENIGN_STATUSES:
            result["degraded"].append((name, status, str(info.get("detail") or "")))
        else:
            result["ok"].append(name)

    return result


def newest_run_hours(collectors: dict, now: datetime):
    """Age in hours of the most recent run by ANY real collector.

    None when nothing has ever reported, which is itself a stopped pipeline.
    """
    ages = [
        age_hours(info.get("run_at") or info.get("checked_at"), now)
        for name, info in collectors.items()
        if isinstance(info, dict)
        and name != DIGEST_NAME
        and name not in MEASUREMENT_ONLY
        and str(info.get("status") or "").lower() not in DELIBERATELY_STOPPED
    ]
    ages = [a for a in ages if a is not None]
    return min(ages) if ages else None


def pipeline_stopped(collectors: dict, now: datetime,
                     limit_hours: float = PIPELINE_STOPPED_HOURS) -> bool:
    newest = newest_run_hours(collectors, now)
    return newest is None or newest > limit_hours


# --------------------------------------------------------------------------
# Spend
# --------------------------------------------------------------------------

def spend_line():
    """This month's spend, PER POT, or None when it cannot be read.

    spend.py imports cleanly (module scope is constants and an import), so this
    borrows its month-delta logic rather than restating the arithmetic. Any
    failure here is reported as absent, never as zero: a digest that says
    "$0.00 spent" when the key lookup failed is worse than one that says
    nothing.

    PER POT SINCE 2026-08-18, and the reason is an outage that never happened.
    This function used to ask one question -- is the month's TOTAL past 90% of
    the allowance -- and call the answer `at_ceiling`. Since budget.py split
    the allowance on 2026-08-13, that total has not been the thing that stops
    collection: the scheduled collectors are measured against the COMMITTED
    pot alone, precisely so a backfill campaign cannot degrade them.

    On 2026-08-17 the difference was the whole story. The month read $12.18 of
    $8.00, well past the old line, so the digest mailed "collection will not
    run until the month rolls over". The committed pot at that moment held
    $3.20 of $7.11 against a $6.40 stop line; the collectors had filed priced
    health rows on each of the four preceding days and went on filing them.
    What had actually stopped was the catch-up walkers, which is the two pots
    working as designed.

    So `at_ceiling` now means the one thing every reader took it to mean: THE
    SCHEDULED COLLECTORS ARE DEGRADED. `total_over` carries the old question
    for anyone who wants the whole-allowance number.
    """
    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return None
    try:
        import budget as budget_mod
        import spend as spend_module

        data = spend_module.fetch()
        used = float(data.get("usage") or 0)
        month_spend, month = spend_module.month_delta(used)
        allowance = spend_module.MONTHLY_ALLOWANCE_USD
        ceiling = allowance * spend_module.STOP_AT_FRACTION

        charged = budget_mod.charge(budget_mod.ledger_spend(),
                                    month_total=month_spend)
        pots = budget_mod.pots(allowance)
        committed = budget_mod.decide(
            kind=budget_mod.COMMITTED, allowance=allowance, charged=charged,
            stop_at_fraction=spend_module.STOP_AT_FRACTION)
        catchup = budget_mod.decide(
            kind=budget_mod.DISCRETIONARY, allowance=allowance,
            charged=charged, stop_at_fraction=spend_module.STOP_AT_FRACTION)
        return {
            "month": month,
            "spent": month_spend,
            "allowance": allowance,
            "committed_spent": charged[budget_mod.COMMITTED],
            "committed_pot": pots[budget_mod.COMMITTED],
            "committed_over": committed.over,
            "discretionary_spent": charged[budget_mod.DISCRETIONARY],
            "discretionary_pot": pots[budget_mod.DISCRETIONARY],
            "discretionary_over": catchup.over,
            # The collectors, and nothing else. See the docstring.
            "at_ceiling": committed.over,
            "total_over": month_spend >= ceiling,
            "lifetime": used,
            "limit": data.get("limit"),
        }
    except (Exception, SystemExit) as exc:
        # SystemExit is caught on purpose: spend.fetch() raises it on a bad key
        # or an OpenRouter error, and an unreadable balance must never take the
        # collector digest down with it.
        return {"error": str(exc)[:200]}


# --------------------------------------------------------------------------
# The email
# --------------------------------------------------------------------------

PASTE_LEAD = (
    "What to do: open a Claude Code session in the talent-intelligence-tracker "
    "repo and paste the line below. That is the whole job."
)


def build_email(buckets: dict, stopped: bool, newest_hours, spend: dict | None,
                source_label: str, guardrails: list[dict] | None = None,
                link_health: dict | None = None,
                archive_stalled: bool = False,
                landmarks: dict | None = None) -> tuple[str, str]:
    """Subject and plain-text body. No em-dashes: this is owner-facing copy."""
    guardrails = guardrails or []
    landmark_regressions = (landmarks or {}).get("regressions") or 0
    stale = buckets["stale"]
    degraded = buckets["degraded"]
    unknown = buckets["unknown_age"]
    names = ([n for n, _, _ in stale] + [n for n, _, _ in degraded]
             + [n for n, _ in unknown])

    if stopped:
        subject = "Pipeline may have stopped: no collect in %s" % (
            "any recorded run" if newest_hours is None
            else "%.0f hours" % newest_hours)
    elif landmark_regressions:
        # Ranked this high because of what it means. A landmark is an event
        # with the company's own announcement behind it, and a regression says
        # one that WAS on the site is not any more. Nothing else in this email
        # can tell you that.
        subject = ("%d landmark round(s) we used to hold have gone missing"
                   % landmark_regressions)
    elif guardrails:
        # Ranked by what the finding actually costs, not by count. A row that is
        # already LIVE is a wrong figure on the page right now, which is the
        # $86bn failure in miniature; an OVERDUE one is already turning every
        # run red. A merely held row is the guardrail working, and says so.
        live = [r for r in guardrails if r.get("already_live")]
        overdue = [r for r in guardrails
                   if (r.get("age_hours") or 0) > (r.get("grace_hours") or 1e9)]
        if live:
            subject = ("%d quarantined row(s) are already live on the site"
                       % len(live))
        elif overdue:
            subject = ("%d quarantined row(s) unanswered past the grace window"
                       % len(overdue))
        elif guardrails_mod.unreviewed_amounts(
                [r for r in guardrails if "already_live" in r]):
            # Named in the SUBJECT because this is the one the ledger showed
            # nobody ever opening: fifteen rows, $874.2bn, `reviewed_at` NULL
            # on every one. "Waiting on you" was true and got ignored; the
            # dollars are what makes it read as a cost rather than a chore.
            unreviewed = guardrails_mod.unreviewed_amounts(
                [r for r in guardrails if "already_live" in r])
            total = sum(r.get("value") or 0 for r in unreviewed)
            subject = ("$%.1fbn of funding is held back, unanswered for over "
                       "%dh" % (total / 1e9,
                                guardrails_mod.AMOUNT_REVIEW_DEADLINE_HOURS))
        else:
            subject = "%d row(s) quarantined, waiting on you" % len(guardrails)
    elif names:
        subject = "%d collector(s) need attention: %s" % (
            len(names), ", ".join(names[:4]))
    elif spend and spend.get("at_ceiling"):
        subject = "LLM spend has reached the monthly ceiling"
    elif archive_stalled:
        subject = "Source archiving has produced nothing in %d days" % (
            ARCHIVE_STALL_DAYS)
    else:
        subject = "Health digest"

    lines = ["The weekly health check found something that needs a human.", ""]

    if landmark_regressions:
        lines += ["LANDMARK REGRESSION: %d round(s) this tracker used to hold "
                  "are gone." % landmark_regressions, ""]
        for item in (landmarks or {}).get("regressed", [])[:6]:
            lines.append("  %s  %s  $%.3gbn"
                         % (item.get("quarter"), item.get("company"),
                            float(item.get("amount_usd") or 0) / 1e9))
            lines.append("    %s" % (item.get("why") or ""))
            lines.append("    %s" % (item.get("source_url") or ""))
        lines += [
            "  A landmark is an event with the company's OWN announcement "
            "behind it,",
            "  so this is not a judgement call about coverage. Something we "
            "published",
            "  is not published any more.",
            "",
        ]

    if stopped:
        lines += [
            "PIPELINE STOPPED",
            ("  No collector has reported at all." if newest_hours is None else
             "  The newest collect is %.0f hours old (expected within %d)."
             % (newest_hours, PIPELINE_STOPPED_HOURS)),
            "  This is the failure that leaves no red run to notice: a workflow",
            "  switched off by hand, or GitHub auto-disabling the cron after 60",
            "  days without repository activity. Check the Actions tab first,",
            "  and look for a 'this workflow was disabled' banner on collect.",
            "",
        ]

    if guardrails:
        live = [r for r in guardrails if r.get("already_live")]
        held = [r for r in guardrails
                if "already_live" in r and not r.get("already_live")]
        aggregate = [r for r in guardrails if "already_live" not in r]
        overdue = [r for r in guardrails
                   if (r.get("age_hours") or 0) > (r.get("grace_hours") or 1e9)]

        lines += [
            "PUBLISH GUARDRAILS: %d row(s) quarantined." % len(guardrails),
            "  Collection is NOT stopped. Every other row publishes normally.",
            "  Nothing was dropped either: each flagged row is still there, held "
            "out of",
            "  every figure, waiting for a yes or a no.",
        ]
        if held:
            lines.append("  %d never reached the site, so nothing is wrong in "
                         "public. That is the guard working." % len(held))
        if live:
            lines.append("  %d are ALREADY LIVE. Quarantine cannot pull a "
                         "published row back, so these need a retraction "
                         "decision from you." % len(live))
        if aggregate:
            lines.append("  %d are aggregate findings: the published set does "
                         "not add up, so NOTHING is publishing until they are "
                         "answered." % len(aggregate))
        if overdue:
            lines.append("  %d are past the grace window, so every run is now "
                         "exiting non-zero after it publishes its clean rows."
                         % len(overdue))
        else:
            lines.append("  None are overdue yet, so the runs are still green.")

        for row in guardrails[:6]:
            value = row.get("value") or 0
            age, grace = row.get("age_hours"), row.get("grace_hours")
            when = ("" if age is None
                    else "  [red in %.0fh]" % max(0.0, grace - age)
                    if age <= grace else "  [OVERDUE by %.0fh]" % (age - grace))
            lines.append("  %-14s %-8s %s%s%s" % (
                row.get("check_name", ""),
                "live" if row.get("already_live") else
                ("held" if "already_live" in row else "halt"),
                (row.get("label") or "")[:52],
                "  ($%.2fbn)" % (value / 1e9) if value >= 1e9 else "", when))
        if len(guardrails) > 6:
            lines.append("  ... and %d more" % (len(guardrails) - 6))

        # THE MONEY QUEUE, IN FULL, WITH NO "AND N MORE".
        #
        # The six-row extract above is a summary and it is allowed to truncate.
        # This is not, and the reason is what the ledger looked like on
        # 2026-08-04: fifteen `amount` findings worth $874.2bn, every one
        # `state='open'` with `reviewed_at` NULL, one of them re-seen 229 times
        # over five days, while the site published $212.5bn. Four fifths of the
        # money we hold had never reached a reader and no email had ever named
        # the rows. A queue summarised as a count is a queue that stays a count.
        #
        # Mailed at 48h rather than at the grace window because this email is
        # the moment of telling, and the thing being told is that real figures
        # are being withheld - not that anything failed.
        unreviewed = guardrails_mod.unreviewed_amounts(
            [r for r in guardrails if "already_live" in r])
        if unreviewed:
            total = sum(r.get("value") or 0 for r in unreviewed)
            lines += [
                "",
                "  UNREVIEWED FUNDING FIGURES (%d, $%.1fbn), every one, oldest "
                "first:" % (len(unreviewed), total / 1e9),
                "  These are out of the money charts, the totals and the table "
                "until answered.",
            ]
            for row in sorted(unreviewed, key=lambda r: -(r.get("age_hours") or 0)):
                lines.append("    $%8.2fbn  %-46s %.0fd unanswered  %s" % (
                    (row.get("value") or 0) / 1e9,
                    (row.get("label") or "")[:46],
                    (row.get("age_hours") or 0) / 24,
                    "ON THE LIVE SITE" if row.get("already_live") else "held back"))
                lines.append("                %s/%s" % (
                    row.get("check_name", ""), row.get("subject", "")))
        lines.append("")

    for name, hours, limit in stale:
        lines.append("STALE: %s last ran %.0f hours ago, expected within %d. "
                     "It has probably stopped." % (name, hours, limit))
    for name, status, detail in degraded:
        lines.append("DEGRADED: %s is %s. %s" % (name, status, detail[:160]))
    for name, status in unknown:
        lines.append("NO TIMESTAMP: %s reports status %s with no readable "
                     "run time." % (name, status or "unknown"))

    if spend:
        if spend.get("error"):
            lines += ["", "Spend could not be read: %s" % spend["error"]]
        else:
            lines += ["", "Spend in %s: $%.2f of the $%.2f monthly allowance."
                      % (spend["month"], spend["spent"], spend["allowance"])]
            # The allowance is TWO POTS and only one of them can stop
            # collection. Saying which is the difference between a real
            # outage and a backfill campaign finishing its money, and until
            # 2026-08-18 this block could not tell them apart.
            if "committed_pot" in spend:
                lines.append(
                    "  Collectors (committed pot): $%.2f of $%.2f. "
                    "Catch-up walkers: $%.2f of $%.2f."
                    % (spend.get("committed_spent") or 0.0,
                       spend.get("committed_pot") or 0.0,
                       spend.get("discretionary_spent") or 0.0,
                       spend.get("discretionary_pot") or 0.0))
            if spend.get("at_ceiling"):
                lines.append(
                    "  AT THE COLLECTION CEILING. The committed pot is past "
                    "its stop line, so spend.py --degrade has switched PAID "
                    "reads off for the scheduled collectors. The job does not "
                    "fail and does not stop: the free collectors, the free "
                    "prefilter, deterministic extraction and both dedup "
                    "layers keep running, and every candidate that would have "
                    "cost money defers UNMARKED for a later run. This costs "
                    "depth for the rest of the month, never coverage.")
            elif spend.get("total_over"):
                lines.append(
                    "  The month TOTAL is past the line, and collection is "
                    "UNAFFECTED. The overspend is in the catch-up pot, which "
                    "the backfill walkers draw on and which cannot degrade a "
                    "scheduled collector: that separation is what budget.py "
                    "exists for. The collectors are inside their own pot and "
                    "still buying paid reads. Nothing here needs a human "
                    "unless you want the walkers funded again, which is a "
                    "decision and not a fault.")

    lines += ["", PASTE_LEAD, ""]

    if stopped:
        lines.append(
            '  "The health digest says the pipeline has stopped: no collect in '
            '%s. Check .github/workflows/collect.yml is still enabled and '
            'scheduled, check the last few collect runs in Actions, and confirm '
            'the spend guard is not exiting 1. Then run ops_status.py and tell '
            'me what actually broke."'
            % ("any recorded run" if newest_hours is None
               else "%.0f hours" % newest_hours))
    elif landmark_regressions:
        lines.append(
            '  "The health digest says a landmark round has gone missing. Run '
            'python3 check_landmarks.py --live and read the REGRESSION block: '
            'it names the company, the amount and the primary document. Find '
            'out whether the row was retracted, superseded by a revision, or '
            'quarantined by a publish guardrail, and tell me which before '
            'changing anything. Do not add the row by hand: if it needs '
            'recollecting it goes through the collector like everything else."')
    elif guardrails:
        lines.append(
            '  "The health digest says rows are quarantined by the publish '
            'guardrails. Run python3 guardrails.py, read every one, and for '
            'each tell me whether the row is a real employer raising real money '
            'or a vehicle that employs nobody. Start with any marked ALREADY '
            'LIVE: those are wrong figures on the page right now and only a '
            'retraction removes them. Do not accept anything to clear the '
            'queue: an accepted finding never blocks again. Retract the bad '
            'ones with retract.py so the correction stays visible on the site."')
    elif "link_check" in names:
        # A drifted link is the one finding here that is neither a parser bug
        # nor decay, so the generic "fix the collector" instruction below would
        # send the owner to the wrong file entirely. It is a URL we cite being
        # served by somebody else, and it needs a person to look at the page.
        lines.append(
            '  "The health digest flagged link_check. Run python3 ops_status.py '
            'and read section [2c], then open each DRIFTED url and tell me what '
            'is actually being served there now. Do not delete any row: propose '
            'a retraction or a re-source for each one and let me decide. Then '
            'check whether one publisher accounts for most of the rot, which '
            'would mean it changed its URL scheme."')
    elif names:
        lines.append(
            '  "The health digest flagged these collectors: %s. For each, open '
            'its file in collectors/, check whether the upstream site or feed '
            'changed shape, and fix the parser. A collector returning zero '
            'almost always means the page layout moved. Then dry-run it with '
            'python run_collect.py --dry-run --source <name> and confirm."'
            % ", ".join(names))
    elif spend and spend.get("at_ceiling"):
        lines.append(
            '  "The health digest says the COMMITTED pot has hit its stop '
            'line, so the scheduled collectors are running with paid reads '
            'off. Run python3 budget.py and python3 cost_projection.py, '
            'confirm the month-delta snapshot in data/spend_month.json is '
            'correct, and tell me what the collectors actually cost per day '
            'before we discuss the allowance. Do not raise the number to make '
            'the message stop."')
    elif archive_stalled:
        # Deliberately points at the runs rather than at the script. The script
        # is fine every time this fires: what breaks is the path to it, and the
        # first question is always whether the runs went out in dry_run mode.
        lines.append(
            '  "The health digest says source archiving has produced nothing '
            'for a week. Run python3 ops_status.py and read section [2c], then '
            'check the last few archive-sources runs with gh: confirm each one '
            'said RECORDING rather than DRY RUN, and that the scheduled tickets '
            'in data/writer_queue.json carry dry_run=false. If the runs are '
            'recording, read what Wayback actually answered before changing any '
            'budget: a throttle is not a refusal and raising spn_max makes it '
            'worse."')

    # LANDMARKS is reported EVERY time, for the same reason SOURCE LINKS is:
    # it is supposed to move, and a number that only appears once it is already
    # bad cannot show a slow slide. It is also the one line in this email that
    # answers "are the events nobody could defend missing actually here", which
    # is the question a human had to ask by hand on 2026-08-04.
    if landmarks:
        lines += ["", "LANDMARKS  (largest disclosed round per quarter, "
                  "primary sources)", "  " + landmarks["one_line"]]
        if landmarks.get("held_not_live"):
            lines.append(
                "  %d are STORED and NOT LIVE: rows we hold that no reader can "
                "see. Check the" % landmarks["held_not_live"])
            lines.append("  publish guardrails before anything else.")
        if landmarks.get("biggest_gaps"):
            lines.append("  Largest standing gaps:")
            for gap in landmarks["biggest_gaps"]:
                lines.append("    %-8s %-18s $%.3gbn  %s"
                             % (gap["quarter"], gap["company"][:18],
                                float(gap["amount_usd"]) / 1e9, gap["status"]))
        lines.append(
            "  A standing gap has never been held, so it is a work list and "
            "not a fault.")
        lines.append("  Checked %s against set %s."
                     % (landmarks.get("checked_on") or "never",
                        landmarks.get("version") or "unknown"))
    else:
        lines += ["", "LANDMARKS",
                  "  No landmark report exists, so nothing is watching the "
                  "largest rounds.",
                  "  Run: python3 check_landmarks.py --live --write"]

    # SOURCE LINKS is reported EVERY time, findings or none. It is the one
    # number in this email that is supposed to move week on week, and a metric
    # that only appears when it is already bad cannot show a slow slide. The
    # archiver went a whole day producing nothing on 2026-07-30 and what hid it
    # was not a wrong number, it was no number anywhere the owner reads.
    if link_health:
        rot = link_health.get("rot") or {}
        lines += [
            "",
            "SOURCE LINKS",
            "  %d of %d cited documents in the archived scope have a Wayback "
            "fallback (%s%%)." % (link_health["archived"], link_health["in_scope"],
                                  link_health["pct"]),
            "  %d are waiting on a capture, %d have never been asked about."
            % (link_health["capture_queue"], link_health["never_probed"]),
            "  Newest snapshot: %s." % (link_health.get("newest_snapshot")
                                        or "none ever recorded"),
        ]
        if rot.get("checked"):
            lines.append(
                "  Rot: %d of %d checked links are dead or drifted (%s%%)."
                % (rot["rot"], rot["checked"], rot["rot_pct"]))
        lines.append(
            "  The scope is the publisher collectors on purpose. The rest of "
            "what we")
        lines.append(
            "  cite is SEC and GOV.UK filings, which those governments keep "
            "indefinitely.")
        if archive_stalled:
            lines += [
                "",
                "  STALLED. There is outstanding work and no snapshot has been "
                "recorded",
                "  in %d days. This is the failure that leaves no red run: the "
                "job can" % ARCHIVE_STALL_DAYS,
                "  exit green having captured nothing, and every source link "
                "keeps working",
                "  right up until the day it does not.",
            ]

    lines += ["", "Ledger read from: %s" % source_label,
              "Healthy collectors: %d" % len(buckets["ok"])]

    return subject, "\n".join(lines)


def send_alert(subject: str, body: str, *, site: str, key: str) -> tuple[bool, str]:
    """POST the alert to the site's keyed /alert endpoint.

    Mail goes through WordPress (wp_mail) rather than an SMTP service so there
    is one fewer credential to hold, and the same de-duplication the sibling
    uses applies server-side. Returns (sent, human-readable note). The key is
    never printed.
    """
    if not site or not key:
        missing = " and ".join(
            [n for n, v in (("WP_SITE_URL", site), ("WP_API_KEY", key)) if not v])
        return False, "not sent: %s is not configured" % missing

    import requests

    try:
        resp = requests.post(
            f"{site.rstrip('/')}/wp-json/talent/v1/alert",
            json={"subject": subject, "body": body},
            headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT},
            timeout=30,
        )
    except Exception as exc:
        return False, "not sent: request failed (%s)" % str(exc)[:160]

    if resp.status_code == 404:
        return False, ("not sent: the site has no /alert route yet. The plugin "
                       "carrying it has not been deployed.")
    if resp.status_code != 200:
        return False, "not sent: endpoint returned HTTP %d" % resp.status_code

    try:
        payload = resp.json()
    except ValueError:
        return False, "not sent: endpoint returned a non-JSON body"

    if payload.get("sent"):
        return True, "sent"
    return False, "not sent: %s" % (payload.get("reason") or "wp_mail declined")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Alert a human when a collector dies.")
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and print, send no email")
    parser.add_argument("--local", action="store_true",
                        help="read the committed SQLite ledger instead of the site")
    parser.add_argument("--send-test", action="store_true",
                        help="send a test alert whatever the findings, to prove "
                             "the mail path works")
    args = parser.parse_args(argv)

    site = (os.environ.get("WP_SITE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("WP_API_KEY") or "").strip()

    if args.local or not site:
        try:
            collectors = read_local()
        except Exception as exc:
            print("FATAL: could not read the local ledger: %s" % exc, file=sys.stderr)
            return 1
        source_label = "local SQLite ledger (%s)" % DB.name
    else:
        try:
            collectors = read_live(site)
        except Exception as exc:
            print("FATAL: could not read /source-health: %s" % exc, file=sys.stderr)
            return 1
        source_label = "live /source-health"

    if not collectors:
        print("FATAL: the ledger is empty. Nothing has ever reported.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    buckets = classify(collectors, now)
    newest = newest_run_hours(collectors, now)
    stopped = pipeline_stopped(collectors, now)
    spend = spend_line()
    guardrail_rows = read_guardrails()
    link_health = read_link_health()
    archive_stalled = archiving_stalled(link_health, now)
    landmarks = read_landmarks()

    print("HEALTH DIGEST  (%s)" % source_label)
    print("  %d ok, %d degraded, %d stale, %d without a timestamp"
          % (len(buckets["ok"]), len(buckets["degraded"]),
             len(buckets["stale"]), len(buckets["unknown_age"])))
    print("  newest collect: %s"
          % ("never" if newest is None else "%.1f hours ago" % newest))
    for name, hours, limit in buckets["stale"]:
        print("  ::warning:: STALE %s: %.1fh since its last run (limit %dh)"
              % (name, hours, limit))
    for name, status, detail in buckets["degraded"]:
        print("  ::warning:: %s %s: %s" % (status.upper(), name, detail[:120]))
    for name, status in buckets["unknown_age"]:
        print("  ::warning:: NO TIMESTAMP %s (status %s)" % (name, status))
    if stopped:
        print("  ::warning:: PIPELINE STOPPED: no collect within %dh"
              % PIPELINE_STOPPED_HOURS)
    if spend and not spend.get("error"):
        print("  spend in %s: $%.2f of $%.2f%s"
              % (spend["month"], spend["spent"], spend["allowance"],
                 "  AT CEILING" if spend.get("at_ceiling") else ""))
    elif spend:
        print("  spend unavailable: %s" % spend["error"])

    for row in guardrail_rows:
        print("  ::warning:: GUARDRAIL %s: %s"
              % (row.get("check_name", ""), (row.get("label") or "")[:100]))

    if link_health:
        print("  source links: %d/%d in scope archived (%s%%), %d waiting on a "
              "capture, %d never asked about, newest %s"
              % (link_health["archived"], link_health["in_scope"],
                 link_health["pct"], link_health["capture_queue"],
                 link_health["never_probed"],
                 link_health.get("newest_snapshot") or "never"))
    if archive_stalled:
        print("  ::warning:: ARCHIVING STALLED: work outstanding and no snapshot "
              "recorded in %d days" % ARCHIVE_STALL_DAYS)

    if landmarks:
        print("  " + landmarks["one_line"] + "  (checked %s)"
              % landmarks.get("checked_on"))
        if landmarks["regressions"]:
            print("  ::warning:: LANDMARK REGRESSION: %d round(s) we used to "
                  "hold are gone" % landmarks["regressions"])
    else:
        print("  ::warning:: no landmark report: nothing is watching the "
              "largest rounds")

    needs_human = bool(
        stopped or buckets["stale"] or buckets["degraded"] or buckets["unknown_age"]
        or guardrail_rows or (spend and spend.get("at_ceiling"))
        # An archiver that runs green every three hours and records nothing is
        # invisible to every other check here: it is not stale, not degraded,
        # and costs nothing. It is also the exact failure that turns a sourced
        # claim into an unsourced one the day a publisher deletes a page.
        or archive_stalled
        # A landmark that WAS held and is not any more. Standing gaps are
        # deliberately not here: they are a backlog, and a weekly email about a
        # backlog that only backfilling can move is an email that gets filtered.
        or bool(landmarks and landmarks["regressions"])
    )

    if not needs_human and not args.send_test:
        print("  Nothing needs a human. No email sent.")
        return 0

    subject, body = build_email(buckets, stopped, newest, spend, source_label,
                                guardrail_rows, link_health, archive_stalled,
                                landmarks)
    if args.send_test and not needs_human:
        subject = "Test alert: everything is healthy"
        body = ("This is a test of the alert path, sent on request.\n\n"
                "Nothing is wrong. %d collectors are healthy and the newest "
                "collect is %s.\n"
                % (len(buckets["ok"]),
                   "unknown" if newest is None else "%.1f hours old" % newest))

    print("\n--- email ---\nSubject: %s\n%s\n--- end ---\n" % (subject, body))

    if args.dry_run:
        print("dry run: no email sent")
        return 0

    sent, note = send_alert(subject, body, site=site, key=key)
    print("alert delivery: %s" % note)
    if not sent and needs_human:
        # A digest that cannot deliver is still a digest that ran, and the
        # findings above are in the log. Say so loudly and stay green: the
        # workflow failing here would be a second alarm for the same problem.
        print("::warning:: the digest found problems but could not email them")
    elif not sent:
        print("::warning:: the test alert could not be delivered")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "talent_intel.db"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# This script's own name in the ledger. It must NEVER classify itself: the
# sibling's digest marked itself degraded because another collector was
# degraded, which showed one problem as two amber lights and read as "the
# digest is broken" when it had just done its job. A digest's status describes
# whether the DIGEST ran, not what it found.
DIGEST_NAME = "health_digest"

# Statuses that are not an incident. "retired"/"disabled" are deliberate stops,
# so their old timestamp is expected and must not read as staleness either.
BENIGN_STATUSES = {"ok", "retired", "disabled"}
DELIBERATELY_STOPPED = {"retired", "disabled"}

# How long a collector may stay quiet before it counts as stale.
#
# Only google_news is on the cron: collect.yml runs at 06:00 and 18:00 UTC and
# passes --source google_news when no input is given. 36 hours is two missed
# runs, the same threshold ops_status.py uses. The other three collectors are
# dispatch-only today, so a short leash on them would cry wolf every week; they
# get a long one and move to 36 the day they join the schedule.
MAX_AGE_HOURS = {
    "google_news": 36,
    "gdelt": 336,
    "sec_edgar": 336,
    "sec_form_d": 336,
}
DEFAULT_MAX_AGE_HOURS = 336  # 14 days

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
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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
    """This month's spend, or None when it cannot be read.

    spend.py imports cleanly (module scope is constants and an import), so this
    borrows its month-delta logic rather than restating the arithmetic. Any
    failure here is reported as absent, never as zero: a digest that says
    "$0.00 spent" when the key lookup failed is worse than one that says
    nothing.
    """
    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return None
    try:
        import spend as spend_module

        data = spend_module.fetch()
        used = float(data.get("usage") or 0)
        month_spend, month = spend_module.month_delta(used)
        allowance = spend_module.MONTHLY_ALLOWANCE_USD
        ceiling = allowance * spend_module.STOP_AT_FRACTION
        return {
            "month": month,
            "spent": month_spend,
            "allowance": allowance,
            "at_ceiling": month_spend >= ceiling,
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
                source_label: str) -> tuple[str, str]:
    """Subject and plain-text body. No em-dashes: this is owner-facing copy."""
    stale = buckets["stale"]
    degraded = buckets["degraded"]
    unknown = buckets["unknown_age"]
    names = ([n for n, _, _ in stale] + [n for n, _, _ in degraded]
             + [n for n, _ in unknown])

    if stopped:
        subject = "Pipeline may have stopped: no collect in %s" % (
            "any recorded run" if newest_hours is None
            else "%.0f hours" % newest_hours)
    elif names:
        subject = "%d collector(s) need attention: %s" % (
            len(names), ", ".join(names[:4]))
    elif spend and spend.get("at_ceiling"):
        subject = "LLM spend has reached the monthly ceiling"
    else:
        subject = "Health digest"

    lines = ["The weekly health check found something that needs a human.", ""]

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
            if spend.get("at_ceiling"):
                lines.append(
                    "  AT THE CEILING. spend.py --enforce now exits 1, so "
                    "collection will not run until the month rolls over or the "
                    "allowance in spend.py changes.")

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
            '  "The health digest says LLM spend has hit the monthly ceiling '
            'and collection is blocked. Read spend.py, confirm the month-delta '
            'snapshot in data/spend_month.json is correct, and tell me whether '
            'to wait for the month to roll over or raise the allowance."')

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

    needs_human = bool(
        stopped or buckets["stale"] or buckets["degraded"] or buckets["unknown_age"]
        or (spend and spend.get("at_ceiling"))
    )

    if not needs_human and not args.send_test:
        print("  Nothing needs a human. No email sent.")
        return 0

    subject, body = build_email(buckets, stopped, newest, spend, source_label)
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
    if not sent:
        # A digest that cannot deliver is still a digest that ran, and the
        # findings above are in the log. Say so loudly and stay green: the
        # workflow failing here would be a second alarm for the same problem.
        print("::warning:: the digest found problems but could not email them")
    return 0


if __name__ == "__main__":
    sys.exit(main())

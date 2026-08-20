#!/usr/bin/env python3
"""Is the site that serves all of this actually answering? And drain what waited.

WHY THIS EXISTS
---------------
On 2026-07-31 Bluehost answered 504 for everything under /blog/ for about seven
minutes, and again for about six that afternoon. Between the two trackers there
are ops tools that report collector health, link rot, spend, writer-queue state
and data integrity — and not one of them knew whether the host serving every one
of those things was reachable. **The outage was found by the owner in a
browser.** Everything downstream (enrich failing, drain-writers going red, the
alerter failing four times) was a symptom being reported without its cause.

So this is the missing measurement, and it is deliberately the CHEAPEST one that
could be true: one GET of a public REST route, cache-busted so Cloudflare cannot
answer on behalf of a dead origin. It is not a synthetic browser check and it
does not crawl the dashboard — a shared host that has hit its ceiling twice in a
day should not be monitored by adding load to it.

THREE JOBS, ONE RUN, AND WHY THEY BELONG TOGETHER
--------------------------------------------------
1. PROBE the host and record the answer in `data/host_status.json` (committed,
   because ops_status.py must stay offline: a status tool that reaches the
   network is a status tool that can hang, and this repo's ops_status promises
   "no deps, no network, no keys").
2. DRAIN `data/alert_outbox.json` when the host answers. An alert held during an
   outage reaches the owner here, and this is the natural place for it: the only
   moment worth retrying delivery is the moment we have just proven the host is
   up, and that is exactly what step 1 establishes.
3. ESCALATE a SUSTAINED outage on a channel that is not on the host — one
   GitHub issue, opened once and closed once. See gh_fallback.py.

SUSTAINED, NOT FLAPPING
-----------------------
A single failed probe is a bad packet. `SUSTAINED_FAILURES` consecutive failed
RUNS (each of which already retried within itself) is an outage. At a 15-minute
cadence that is roughly 45 minutes of continuous unreachability before anybody
is emailed, which means neither of 2026-07-31's outages would have opened an
issue — and that is correct. Both self-healed, the held alerts were delivered
minutes later, and an alarm that fires on every seven-minute wobble on shared
hosting is one the owner learns to ignore. The blips are still RECORDED, and
ops_status.py shows them, because a host wobbling four times a week is a fact
worth seeing even when no single wobble is worth an email.

WHY A SUSTAINED OUTAGE DOES NOT MAKE THIS RUN RED
-------------------------------------------------
Two reasons, and the second is the important one.

* A red run here would fire the CI failure alert, which would try to post to the
  down host, which would hold the alert — the amplification loop this whole
  change exists to break, re-entered through the front door.
* A red run means "something in this repository needs fixing". Bluehost being
  down is not that. It is reported, loudly, in the ledger and on the issue and
  in ops_status; it is not pretended to be a defect in the code.

This run goes red only when its OWN machinery fails: it cannot write the ledger,
or the host is up and a held alert still will not deliver for a settled reason
like a wrong key. Those are things a human can fix here.

Stdlib only. Runs with no `pip install`, for the same reason ci_alert.py does.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import alert_outbox
import ci_alert
import opsmail
import gh_fallback

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "data" / "host_status.json"

#: A public GET that exercises WordPress, the plugin and the database, and
#: returns a few hundred bytes. Not the dashboard page: that is heavy, and
#: Cloudflare caches it, so it can answer 200 from the edge while the origin is
#: dead — a monitor that reads a cached copy of a corpse.
PROBE_PATH = "/wp-json/talent/v1/source-health"

USER_AGENT = "TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)"

#: Attempts within one run, and the gaps between them. One bad response is not
#: an outage; this is the difference between measuring the host and measuring
#: a single TCP connection.
PROBE_ATTEMPTS = 3
PROBE_GAP_SECONDS = 20
PROBE_TIMEOUT = 25

#: Consecutive FAILED RUNS before the outage is announced on the fallback
#: channel. See "SUSTAINED, NOT FLAPPING" above for why this is not 1.
SUSTAINED_FAILURES = 3

#: Even when nothing changes, write the ledger this often so ops_status can tell
#: "the host has been fine all day" from "this watchdog stopped running a week
#: ago and its last word was fine". Four commits a day of a small JSON file, in
#: a repo that already commits a drain tick every few minutes.
HEARTBEAT_HOURS = 6

#: How stale the ledger may be before ops_status calls the WATCHDOG broken.
#: Three missed heartbeats.
LEDGER_STALE_HOURS = 24

HISTORY_KEPT = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- the probe -------------------------------------------------------------

def probe_once(site: str, *, timeout: int = PROBE_TIMEOUT) -> tuple[bool, str]:
    """One cache-busted GET. Returns (ok, detail)."""
    url = (f"{site.rstrip('/')}{PROBE_PATH}"
           f"?cb={random.randint(1, 10**9)}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(4096)
            ms = int((time.monotonic() - started) * 1000)
            return True, f"HTTP {resp.status} in {ms}ms"
    except urllib.error.HTTPError as exc:
        # A 4xx here still proves WordPress is alive and routing. Only the 5xx
        # family and a refusal to answer at all mean "the host is not serving".
        if exc.code < 500:
            return True, f"HTTP {exc.code} (the site answered)"
        return False, f"HTTP {exc.code} from {PROBE_PATH}"
    except urllib.error.URLError as exc:
        return False, f"no answer from {PROBE_PATH}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — a watchdog must not raise
        return False, f"no answer from {PROBE_PATH}: {exc}"


def probe(site: str, *, sleep=time.sleep) -> tuple[bool, str]:
    """Up to PROBE_ATTEMPTS, spaced. Any success means the host is up."""
    detail = ""
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        ok, detail = probe_once(site)
        print(f"  probe {attempt}/{PROBE_ATTEMPTS}: {detail}")
        if ok:
            return True, detail
        if attempt < PROBE_ATTEMPTS:
            sleep(PROBE_GAP_SECONDS)
    return False, detail


# --- the ledger ------------------------------------------------------------

def load_ledger(path: Path | str = LEDGER) -> dict:
    p = Path(path)
    if not p.exists():
        return {"version": 1, "state": "unknown", "consecutive_failures": 0,
                "history": []}
    try:
        doc = json.loads(p.read_text() or "{}")
    except (OSError, ValueError):
        return {"version": 1, "state": "unknown", "consecutive_failures": 0,
                "history": []}
    doc.setdefault("history", [])
    doc.setdefault("consecutive_failures", 0)
    doc.setdefault("state", "unknown")
    return doc


def save_ledger(doc: dict, path: Path | str = LEDGER) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["history"] = doc.get("history", [])[-HISTORY_KEPT:]
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def apply_probe(doc: dict, ok: bool, detail: str, *, now: datetime) -> dict:
    """Fold one probe result into the ledger. Returns a decision dict.

    Pure, so the whole state machine is testable without a network: what a
    watchdog does at the boundaries (first failure, third failure, recovery) is
    exactly what nobody can test by waiting for an outage.
    """
    was = doc.get("state", "unknown")
    changed = False

    if ok:
        doc["consecutive_failures"] = 0
        doc["last_ok_at"] = _iso(now)
        if was != "up":
            doc["state"] = "up"
            doc["since"] = _iso(now)
            doc["history"] = doc.get("history", []) + [
                {"at": _iso(now), "state": "up", "detail": detail}]
            changed = True
        doc["announced"] = False
    else:
        doc["consecutive_failures"] = doc.get("consecutive_failures", 0) + 1
        if was != "down":
            doc["state"] = "down"
            doc["since"] = _iso(now)
            doc["history"] = doc.get("history", []) + [
                {"at": _iso(now), "state": "down", "detail": detail}]
            changed = True

    doc["last_probe_at"] = _iso(now)
    doc["last_detail"] = detail

    sustained = (not ok
                 and doc["consecutive_failures"] >= SUSTAINED_FAILURES)
    return {"state_changed": changed, "recovered": ok and was == "down",
            "sustained": sustained,
            "newly_sustained": sustained and not doc.get("announced")}


def needs_commit(doc: dict, decision: dict, *, now: datetime,
                 outbox_changed: bool) -> bool:
    """Write the ledger only when it SAYS something new.

    A probe every 15 minutes that committed every time would be 96 commits a day
    of one unchanged line. The heartbeat is what keeps 'quiet' from becoming
    indistinguishable from 'stopped'.
    """
    if decision["state_changed"] or decision["newly_sustained"] or outbox_changed:
        return True
    last = _parse(doc.get("_committed_at"))
    return last is None or now - last >= timedelta(hours=HEARTBEAT_HOURS)


# --- draining what waited --------------------------------------------------

def drain(outbox: dict, site: str = "", key: str = "") -> tuple[int, int, str]:
    """Deliver held alerts, oldest first. Returns (delivered, remaining, note).

    IT CALLS `ci_alert.deliver`, NEVER `post_alert`, and that is not a detail.
    A held alert has ALREADY been ruled on by the ledger and already claimed, so
    re-running it through `post_alert` would either find its own cause open and
    swallow the alert as a duplicate of itself, or clear a scope a second time.
    The ruling travelled with the payload; the drain's job is to send it.

    The idempotency key travelled with it too, so a re-drain after a failed
    outbox commit collapses at Resend instead of mailing the owner twice.

    Stops at the first TRANSIENT failure: if the relay has gone away again
    mid-drain there is nothing to learn from hammering it, and the entries keep
    their place. A SETTLED refusal (bad key, unverified sender) does not stop
    the drain — every entry will hit it, and the count of how many is the size
    of the problem.

    `site` and `key` are still accepted and ignored: several callers pass them
    and the held payloads predate the move off the host.
    """
    delivered = 0
    blocked = ""
    for entry in alert_outbox.pending(outbox):
        payload = entry.get("payload") or {}
        ok, note, transient = ci_alert.deliver(payload)
        if ok:
            alert_outbox.mark_delivered(entry, f"delivered late: {note}")
            delivered += 1
            print(f"  delivered {entry.get('key')}: {note}")
            continue
        alert_outbox.record_attempt(entry, note)
        print(f"  still undeliverable {entry.get('key')}: {note}")
        blocked = note
        if transient:
            break
    return delivered, len(alert_outbox.pending(outbox)), blocked


def outage_summary(doc: dict, *, now: datetime) -> dict:
    """The email the owner gets about the outage itself, once the host is back.

    Held rather than sent, on purpose: it cannot be sent while the thing it
    describes is happening. The GitHub issue is what speaks during the outage;
    this is the record that lands in the inbox afterwards, so the outage is not
    something you had to be watching to know about.
    """
    since = doc.get("since", "unknown")
    return {
        "subject": f"HOST DOWN: asktherecruiter.com/blog stopped answering at {since}",
        # SLUGGED, not interpolated. `since` is an ISO timestamp, so the
        # obvious `f"host-unreachable:{since}"` mints
        # `host-unreachable:2026-08-03T21:55:00+00:00` — an uppercase T and a
        # `+`, both outside the endpoint's `^[a-z0-9][a-z0-9:._-]{0,159}$`.
        # That is a settled 400: the record of the outage would be held,
        # retried, and stuck, so the ONE email whose whole job is to arrive
        # after the host comes back could never arrive at all. Same defect as
        # the `%G-W%V` week token, in the worse place. Still one key per
        # outage, because `since` is still what varies.
        "dedupe_key": f"host-unreachable:{ci_alert.slug(since)}",
        "body": "\n".join([
            "The WordPress host that serves both trackers stopped answering.",
            "",
            f"  first failed probe: {since}",
            f"  consecutive failed probes: {doc.get('consecutive_failures')}",
            f"  last detail: {doc.get('last_detail')}",
            f"  probe: GET {PROBE_PATH} (public, cache-busted)",
            "",
            "WHAT THIS MEANS FOR YOUR ALERTS. Nothing. Operational mail leaves",
            "through Resend, not through this host, so a CI failure raised",
            "during the window was emailed as normal. That was not true before",
            "2026-08-20: alerts used to go out through a route on this host, so",
            "an outage silenced the alarm about itself.",
            "",
            "WHAT IT MEANS FOR READERS. The tracker pages were unreachable for",
            "the window above.",
            "",
            "WHAT TO DO. Usually nothing: this host has produced several short",
            "504 windows and healed itself each time. If these become frequent,",
            "the ledger in data/host_status.json holds every transition, and",
            "ops_status.py prints the recent ones — that history is the case you",
            "would take to the host, and it is why the blips are recorded even",
            "when they are too short to email about.",
        ]),
    }


# --- the run ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="watch the host, drain what waited")
    ap.add_argument("--site", default=os.environ.get("WP_SITE_URL", ""))
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--outbox", default=str(alert_outbox.OUTBOX))
    ap.add_argument("--no-fallback", action="store_true",
                    help="skip the GitHub issue channel (for local runs)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    site = (args.site or "").rstrip("/")
    if not site:
        print("::error::WP_SITE_URL is not set, so the host cannot be probed at "
              "all. This watchdog is the only thing that knows the site is up; "
              "unconfigured, it is a green run that measures nothing.")
        return 1

    now = _now()
    print(f"probing {site}{PROBE_PATH}")
    ok, detail = probe(site)

    ledger = load_ledger(args.ledger)
    decision = apply_probe(ledger, ok, detail, now=now)
    outbox = alert_outbox.load(args.outbox)
    held_before = len(alert_outbox.pending(outbox))
    repo = gh_fallback.repo_from_env()
    outbox_changed = False
    problems: list[str] = []

    if decision["newly_sustained"]:
        # STILL QUEUED RATHER THAN SENT, and the reason has changed. It used to
        # be that the email COULD NOT be sent while the host was down, because
        # it went out through that host. It can be sent now. It is still queued
        # because the drain below is the one place a held alert is delivered and
        # recorded, and routing this one message around it would be a second
        # delivery path to keep in step. The drain runs in this same tick, so
        # the email leaves immediately rather than waiting for the recovery.
        ledger["announced"] = True
        _outcome, _entry = alert_outbox.enqueue(
            outbox, key=f"host-unreachable:{ledger.get('since')}", kind="alert",
            scope="host:asktherecruiter", payload=outage_summary(ledger, now=now),
            reason="raised while the host was unreachable, so it could not be sent")
        outbox_changed = True
        if not args.no_fallback:
            good, note = gh_fallback.open_or_update(
                repo,
                line=(f"- **{_iso(now)}** — the host has failed "
                      f"{ledger['consecutive_failures']} consecutive probes "
                      f"(`{detail}`). {held_before + 1} alert(s) held."))
            print(f"fallback channel: {note}")
            if not good:
                # The primary channel is down and the fallback did not work.
                # This is the one thing here worth a human's attention tonight.
                print("::error::the host is unreachable AND the host-independent "
                      f"fallback could not be used: {note}. Nothing is currently "
                      "able to tell the owner about this outage.")
                problems.append("fallback channel unusable")

    # THE DRAIN IS NO LONGER GATED ON THE HOST ANSWERING, and it must not be.
    # It was, because delivery went through `/alert` on that host, so "the host
    # just answered" was the only moment worth retrying. Mail leaves through
    # Resend now: the held alerts have nothing to do with whether Bluehost is
    # up, and waiting for a host probe before draining would make the relay's
    # availability depend on the very thing the move was meant to decouple from.
    # So this tick drains whenever anything is held. An EMPTY outbox makes no
    # request at all, which is why the fifteen-minute tick stays free.
    if held_before and not opsmail.configured():
        print(f"::error::{held_before} alert(s) are held, but RESEND_API_KEY is "
              "not set so none of them can be delivered.")
        problems.append("no RESEND_API_KEY to drain with")
    elif held_before and not args.dry_run:
        print(f"draining {held_before} held alert(s)")
        delivered, remaining, blocked = drain(outbox)
        outbox_changed = outbox_changed or delivered > 0 or bool(blocked)
        print(f"delivered {delivered}, {remaining} still held")
        if remaining and alert_outbox.stuck(outbox):
            print(f"::error::{len(alert_outbox.stuck(outbox))} alert(s) have "
                  f"now failed delivery {alert_outbox.FAIL_LOUD_ATTEMPTS}+ "
                  f"times ({blocked}). These are not going to arrive on their "
                  "own - check RESEND_API_KEY, and check that OPS_MAIL_FROM "
                  "uses a domain this Resend account has verified.")
            problems.append("alerts are stuck")
    elif held_before:
        print(f"[dry-run] would deliver {held_before} held alert(s)")

    if ok:
        if not args.no_fallback and not alert_outbox.pending(outbox):
            good, note = gh_fallback.close(
                repo, note=(f"The host answered again at {_iso(now)} "
                            f"(`{detail}`) and every held alert has been "
                            "delivered. Closing; this is the second and last "
                            "email about this outage."))
            if note != "no fallback issue was open":
                print(f"fallback channel: {note}")

    if args.dry_run:
        print(f"[dry-run] state={ledger['state']} "
              f"consecutive_failures={ledger['consecutive_failures']} "
              f"commit={needs_commit(ledger, decision, now=now, outbox_changed=outbox_changed)}")
        return 2 if problems else 0

    if outbox_changed:
        alert_outbox.save(outbox, args.outbox)

    commit = needs_commit(ledger, decision, now=now, outbox_changed=outbox_changed)
    if commit:
        ledger["_committed_at"] = _iso(now)
        try:
            save_ledger(ledger, args.ledger)
        except OSError as exc:
            print(f"::error::could not write the host ledger ({exc}), so nothing "
                  "records whether the site is up.")
            return 1

    _emit_output("commit", "yes" if commit or outbox_changed else "no")
    _emit_output("state", ledger["state"])

    print(f"\nhost is {ledger['state'].upper()} — {detail}")
    if not ok:
        print(f"consecutive failed probes: {ledger['consecutive_failures']} "
              f"(announced at {SUSTAINED_FAILURES})")
        print("This run is deliberately GREEN. The host being down is not a "
              "defect in this repository, and a red run here would fire the CI "
              "alert, which would post to the down host, which is the loop this "
              "design exists to break.")

    return 2 if problems else 0


def _emit_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())

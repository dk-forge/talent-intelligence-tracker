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

# --- the other origins ------------------------------------------------------
#
# THE SANDBOX IS A SEPARATE PRODUCT ON A SEPARATE RAILWAY PROJECT. The only
# thing it shares with this tracker is the owner, and until 2026-09-05 the only
# thing watching it was whichever laptop session happened to be open. This
# watch already runs every 15 minutes on a public repo at $0, already counts
# failures in RUNS rather than minutes, and already knows how to speak on a
# channel that is not the thing being watched. So the sandbox origins ride the
# same tick, the same state machine and the same outbox, and each one gets its
# OWN issue marker, its OWN outbox scope and its OWN sub-ledger under
# `origins` in data/host_status.json. The WordPress host keeps the top level of
# that file untouched, because ops_status.py reads it there.
#
# WHAT "UP" MEANS FOR A /healthz. Stricter than the host probe, on purpose. The
# host probe accepts a 4xx as "WordPress is routing"; a health endpoint that
# answers anything but `200 {"status":"ok","version":"<non-empty>"}` within
# HEALTHZ_TIMEOUT seconds is DOWN. `degraded` is DOWN. HTML is DOWN. A version
# of "" is DOWN, because a build that does not know its own version is not a
# build anyone deployed on purpose.

#: Seconds. Both origins answer in under 100ms when healthy; ten seconds is
#: not a latency budget, it is the line past which a reader has given up.
HEALTHZ_TIMEOUT = 10

#: Each origin: a stable id (the sub-ledger key, the outbox scope and the
#: issue marker all derive from it), a plain-language label for the issue title
#: and the mail subject, and the env var the workflow carries its URL in. The
#: URL is NOT hardcoded here: the workflow is the one place it lives, so the
#: test that reads "the workflow lists all three origins" is reading the truth.
ORIGINS = (
    {"id": "sandbox-backend", "label": "sandbox backend",
     "env": "SANDBOX_BACKEND_URL"},
    {"id": "sandbox-frontend", "label": "sandbox frontend",
     "env": "SANDBOX_FRONTEND_URL"},
)


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


def judge_healthz(status: int | None, body: bytes | str) -> tuple[bool, str]:
    """Is this the answer of a healthy origin? Pure, so every DOWN shape is a
    test rather than an outage nobody can schedule.

    UP is exactly: HTTP 200, a JSON object, `status == "ok"`, and `version` a
    non-empty string. Everything else is DOWN with a detail that says which
    gate refused it, so an issue body reads "status=degraded" rather than
    "unhealthy".
    """
    if status != 200:
        return False, f"HTTP {status} from /healthz"
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    try:
        doc = json.loads(body)
    except ValueError:
        return False, "non-JSON body from /healthz"
    if not isinstance(doc, dict):
        return False, "non-object JSON from /healthz"
    st = doc.get("status")
    if st != "ok":
        return False, f"status={st!r} from /healthz"
    version = doc.get("version")
    if not isinstance(version, str) or not version.strip():
        return False, "empty version from /healthz"
    return True, f"HTTP 200 status=ok version={version}"


def probe_healthz_once(url: str, *, timeout: int = HEALTHZ_TIMEOUT) -> tuple[bool, str]:
    """One cache-busted GET of a /healthz. Returns (ok, detail).

    Same User-Agent as the host probe. A timeout, a refused connection, any
    non-200 and any body judge_healthz refuses are all DOWN; nothing here
    raises, because a watchdog that raises has measured nothing.
    """
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}cb={random.randint(1, 10**9)}",
                                 headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096)
            ok, detail = judge_healthz(resp.status, body)
    except urllib.error.HTTPError as exc:
        return judge_healthz(exc.code, b"")
    except urllib.error.URLError as exc:
        return False, f"no answer from /healthz: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - a watchdog must not raise
        return False, f"no answer from /healthz: {exc}"
    if ok:
        detail += f" in {int((time.monotonic() - started) * 1000)}ms"
    return ok, detail


def probe_healthz(url: str, *, sleep=time.sleep) -> tuple[bool, str]:
    """Up to PROBE_ATTEMPTS, spaced like the host probe. Any success is UP."""
    detail = ""
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        ok, detail = probe_healthz_once(url)
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
    doc.setdefault("origins", {})
    return doc


def origin_ledger(doc: dict, origin_id: str) -> dict:
    """The sub-ledger for one non-host origin, created empty on first sight.

    Same shape as the top level, so `apply_probe` runs on it unchanged and the
    boundary tests written for the host hold for every origin by construction.
    """
    sub = doc.setdefault("origins", {}).setdefault(origin_id, {})
    sub.setdefault("history", [])
    sub.setdefault("consecutive_failures", 0)
    sub.setdefault("state", "unknown")
    return sub


def save_ledger(doc: dict, path: Path | str = LEDGER) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["history"] = doc.get("history", [])[-HISTORY_KEPT:]
    for sub in (doc.get("origins") or {}).values():
        sub["history"] = sub.get("history", [])[-HISTORY_KEPT:]
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


def origin_scope(origin: dict) -> str:
    """The outbox scope AND the ledger key prefix for one origin's outages.

    One string for both so a recovery enqueued under it cancels an outage
    notice that was never delivered (alert_outbox's `cancelled` outcome), which
    is what keeps a sustained-then-healed blip from mailing twice about nothing.
    """
    return f"{origin['id']}-unreachable"


def origin_marker(origin: dict) -> str:
    return f"<!-- alert-fallback:{origin['id']}-unreachable -->"


def origin_title(origin: dict, url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return f"{origin['label']} down ({host} stopped answering /healthz)"


def origin_outage_summary(origin: dict, doc: dict, url: str, *, now: datetime) -> dict:
    """The mail the owner gets when a sandbox origin has been down for
    SUSTAINED_FAILURES consecutive runs. Queued into the outbox and drained in
    this same tick, so it leaves immediately; the outbox is the one delivery
    path, and a second one would be a second thing to keep in step."""
    since = doc.get("since", "unknown")
    key = f"{origin_scope(origin)}:{ci_alert.slug(since)}"
    return {
        "subject": f"{origin['label'].upper()} DOWN: {url} stopped answering at {since}",
        "dedupe_key": key,
        "idempotency_key": f"tit-watch-{key}",
        "body": "\n".join([
            f"The {origin['label']} ({url}) is not answering its health check.",
            "",
            f"  first failed probe: {since}",
            f"  consecutive failed probes: {doc.get('consecutive_failures')}",
            f"  last detail: {doc.get('last_detail')}",
            f"  probe: GET /healthz, {HEALTHZ_TIMEOUT}s timeout, expects",
            "         HTTP 200 and JSON with status \"ok\" and a non-empty version",
            "",
            "THIS IS THE SANDBOX, NOT THE TRACKER. It is a separate product on a",
            "separate Railway project; the only thing it shares with this repo",
            "is the owner and this fifteen-minute tick. Nothing in this",
            "repository can fix it: check the Railway service, its deploy log",
            "and its domain, in that order.",
            "",
            "One GitHub issue is open for this outage and is edited silently as",
            "it continues. It closes itself, and you get one RECOVERED mail,",
            "when the origin answers again.",
        ]),
    }


def origin_recovery_summary(origin: dict, doc: dict, url: str, *, now: datetime) -> dict:
    """The one RECOVERED mail, sent only for an outage that was announced.

    A blip that never reached SUSTAINED_FAILURES announced nothing, so it
    recovers silently; that is the same rule the host follows.
    """
    since = doc.get("outage_since", "unknown")
    key = f"{origin_scope(origin)}:{ci_alert.slug(since)}"
    return {
        "subject": f"RECOVERED: {origin['label']} is answering again ({url})",
        "idempotency_key": f"tit-watch-recovered-{key}",
        "body": "\n".join([
            f"The {origin['label']} ({url}) answered its health check again.",
            "",
            f"  outage began: {since}",
            f"  answered again: {_iso(now)}",
            f"  detail: {doc.get('last_detail')}",
            "",
            "The GitHub issue for this outage is closed. This is the last mail",
            "about it; the transitions are kept under `origins` in",
            "data/host_status.json if the pattern is worth taking to Railway.",
        ]),
    }


def watch_origin(origin: dict, url: str, ledger: dict, outbox: dict, *,
                 now: datetime, repo: str, fallback: bool,
                 probe=probe_healthz) -> dict:
    """Probe one non-host origin and fold the answer into ledger, outbox and
    the issue channel. Returns the apply_probe decision plus `outbox_changed`
    and `problems`, so main() can merge it with the host's.

    The order matters and mirrors the host: state machine first, then the
    channels, and every channel failure is a note rather than an exception.
    """
    sub = origin_ledger(ledger, origin["id"])
    was_announced = bool(sub.get("announced"))
    print(f"probing {url}")
    ok, detail = probe(url)
    decision = apply_probe(sub, ok, detail, now=now)
    decision["outbox_changed"] = False
    decision["problems"] = []
    marker = origin_marker(origin)

    if decision["newly_sustained"]:
        sub["announced"] = True
        # Remembered separately because apply_probe rewrites `since` on the
        # recovery, and the RECOVERED mail has to name the outage it closes.
        sub["outage_since"] = sub.get("since")
        summary = origin_outage_summary(origin, sub, url, now=now)
        alert_outbox.enqueue(outbox, key=summary["dedupe_key"], kind="alert",
                             scope=origin_scope(origin), payload=summary,
                             reason=f"the {origin['label']} stopped answering")
        decision["outbox_changed"] = True
        if fallback:
            good, note = gh_fallback.open_or_update(
                repo, marker=marker, title=origin_title(origin, url),
                what=f"the {origin['label']}",
                preamble=(f"The {origin['label']} ({url}) is not answering its "
                          f"health check. It is a separate product on a separate "
                          f"Railway project; nothing in this repository serves it "
                          f"and nothing here can fix it.\n\n**Nothing is required "
                          f"of you here.** Check the Railway service, its deploy "
                          f"log and its domain. This issue is edited in place while "
                          f"the outage lasts and closes itself when the origin "
                          f"answers again."),
                line=(f"- **{_iso(now)}** the {origin['label']} has failed "
                      f"{sub['consecutive_failures']} consecutive probes "
                      f"(`{detail}`)."))
            print(f"fallback channel ({origin['id']}): {note}")
            if not good:
                print(f"::error::the {origin['label']} is unreachable AND the "
                      f"fallback issue could not be used: {note}.")
                decision["problems"].append(
                    f"fallback channel unusable for {origin['id']}")

    if ok and was_announced:
        recovery = origin_recovery_summary(origin, sub, url, now=now)
        alert_outbox.enqueue(outbox, key=recovery["idempotency_key"],
                             kind="resolve", scope=origin_scope(origin),
                             payload=recovery, reason="")
        decision["outbox_changed"] = True
        sub.pop("outage_since", None)

    if ok and fallback:
        good, note = gh_fallback.close(
            repo, marker=marker,
            note=(f"The {origin['label']} answered again at {_iso(now)} "
                  f"(`{detail}`). Closing; this is the last notice about this "
                  "outage."))
        if note != "no fallback issue was open":
            print(f"fallback channel ({origin['id']}): {note}")

    print(f"{origin['label']} is {sub['state'].upper()}: {detail}")
    return decision


def origin_urls(args, environ=os.environ) -> dict[str, str]:
    """origin id -> URL, from the CLI or the env the workflow carries."""
    urls = {}
    for origin in ORIGINS:
        flag = origin["id"].replace("-", "_")
        urls[origin["id"]] = (getattr(args, flag, "") or environ.get(origin["env"], "")).rstrip("/")
    return urls


# --- the run ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="watch the host, drain what waited")
    ap.add_argument("--site", default=os.environ.get("WP_SITE_URL", ""))
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--outbox", default=str(alert_outbox.OUTBOX))
    ap.add_argument("--no-fallback", action="store_true",
                    help="skip the GitHub issue channel (for local runs)")
    ap.add_argument("--dry-run", action="store_true")
    for origin in ORIGINS:
        ap.add_argument(f"--{origin['id']}", default="",
                        help=f"{origin['label']} /healthz URL (env {origin['env']})")
    args = ap.parse_args(argv)

    site = (args.site or "").rstrip("/")
    if not site:
        print("::error::WP_SITE_URL is not set, so the host cannot be probed at "
              "all. This watchdog is the only thing that knows the site is up; "
              "unconfigured, it is a green run that measures nothing.")
        return 1
    urls = origin_urls(args)
    for origin in ORIGINS:
        if not urls[origin["id"]]:
            # Same rule as the host, for the same reason: an origin this
            # watch is declared to cover and silently skips is a green run
            # that measures nothing, which is the state the sandbox was in.
            print(f"::error::{origin['env']} is not set, so the "
                  f"{origin['label']} cannot be probed. Unconfigured, this is "
                  "a green run that measures nothing.")
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

    # THE OTHER ORIGINS, after the host's own bookkeeping and BEFORE the drain,
    # so an outage notice or a RECOVERED they enqueue leaves in this same tick
    # rather than the next one. Each origin's decision is merged into the
    # host's for the commit question only; the printed verdict, the outputs and
    # the top of the ledger stay the host's, because ops_status reads them there.
    for origin in ORIGINS:
        od = watch_origin(origin, urls[origin["id"]], ledger, outbox, now=now,
                          repo=repo, fallback=not args.no_fallback)
        outbox_changed = outbox_changed or od["outbox_changed"]
        problems.extend(od["problems"])
        decision["state_changed"] = decision["state_changed"] or od["state_changed"]
        decision["newly_sustained"] = decision["newly_sustained"] or od["newly_sustained"]
    held_before = len(alert_outbox.pending(outbox))

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

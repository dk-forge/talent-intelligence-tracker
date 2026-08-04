#!/usr/bin/env python3
"""An alert that could not be delivered is HELD, not lost.

THE DEFECT THIS CLOSES
----------------------
On 2026-07-31 between 00:48 and 00:55 UTC Bluehost answered 504 for everything
under /blog/. Four things happened in order, and the fourth is the design flaw:

    enrich failed (it could not reach the WP host)
      -> drain-writers went red (it correctly refuses to auto-retry a writer)
      -> the CI failure alert fired for both
      -> and it POSTed to /alert, which is ON THE HOST THAT WAS DOWN.
         "HTTP 504 from /alert" x4. "CI alert could not be delivered" x4.

**The alerting system depended on the host it was alerting about.** The one
moment the alarm was most needed was exactly the moment it could not ring — the
same failure class this project keeps finding (a monitor silent precisely when
it matters), one layer up. The outage was found by the owner in a browser.

WHY A COMMITTED FILE AND NOT A LONGER RETRY
-------------------------------------------
Retrying inside the run is necessary and not sufficient. Tonight's outage lasted
seven minutes; a shared host that has hit its ceiling twice in one day can be
down for hours, and no in-run backoff outlives a ten-minute job. So the alert
goes where `data/writer_queue.json` put evicted writers: into a file in the
repository, which survives the runner, the outage and the day. A later run
drains it. This repo's own rule — **the repo IS the memory** — already says
where durable state lives, and it is not in a runner's /tmp.

The alternative durable stores were all worse for this specific job: GitHub
Actions cache is evicted after 7 days and silently, an artifact needs an API
call to the same GitHub that may be the thing failing, and any third-party
queue is a paid dependency the budget does not have.

WHY A QUEUED ALERT MUST NOT REDDEN ITS OWN RUN
----------------------------------------------
The old code exited 1 when the POST failed. So an outage that reddened four
workflows produced four MORE red runs, and a session reading ops_status would
have been told the ALERTER was broken when the alerter was working perfectly and
the host was down. That is amplification: an outage manufacturing red runs which
manufacture alerts which also fail. `ci_alert.py` now exits 0 when the alert is
safely on this queue, because a held alert is a delivered promise, not a
failure. It exits non-zero only when it can neither deliver NOR hold — the one
state where nobody will ever hear about the original failure.

WHAT STOPS THIS QUEUE BECOMING A LANDFILL
-----------------------------------------
* Entries are keyed and idempotent, so eight runs failing against a down host
  during one outage leave eight distinct alerts and never eight copies of one.
* A recovery cancels its own pending alert. If 'collect' failed while the host
  was down and passed before the queue drained, the RED was never sent, so
  there is nothing to clear: both entries are dropped and the owner is not
  mailed twice about something that fixed itself. The endpoint's open/resolved
  state is server-side, so a never-delivered alert left no trace to reconcile.
* Delivered entries become bounded history (HISTORY_KEPT), the same way
  writer_queue.json keeps landed tickets rather than forgetting them.
* An entry that has failed FAIL_LOUD_ATTEMPTS times is `stuck`, and
  ops_status.py reports it as ACTION NEEDED. A queue that quietly never drains
  is the original silence with extra steps.

Stdlib only, on purpose: this runs before any `pip install` in the alerting
path, so a dependency resolution failure can never take the notifier down.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTBOX = ROOT / "data" / "alert_outbox.json"

#: What the endpoint accepts as a dedupe_key, mirrored from `tit_api_alert()` in
#: wordpress-plugin/.../includes/api.php
#: (`$safe = '/^[a-z0-9][a-z0-9:._-]{0,159}$/'`). `ci_alert.KEY_SAFE` IS this
#: object, so there is one definition, and tests/test_ci_noise_report.py pins it
#: against the PHP.
#:
#: IT LIVES IN THE QUEUE BECAUSE THE QUEUE IS WHAT MUST NOT ACCEPT A BAD ONE.
#: A key the endpoint rejects fails with a SETTLED 400, and no number of retries
#: turns a settled 400 into a delivery. On 2026-08-03 ci_noise_report.py composed
#: its ISO week with `%G-W%V`, minted `ci-noise:2026-W32` (uppercase W is not in
#: that character class), and the outbox held it and retried it sixteen times. At
#: twelve it went `stuck`, and host-watch — which reports "alerts are stuck with
#: the host UP" — then failed EVERY fifteen-minute tick from 21:55Z onward while
#: the host answered HTTP 200 in under a second. One malformed string took the
#: outage alarm offline for five hours. Retrying the unretryable is the bug.
KEY_SAFE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,159}$")


def deliverable_key(key: str) -> str:
    """The nearest key the endpoint will actually accept.

    Deliberately a REPAIR rather than a rejection. The alternative is to refuse
    the envelope, and refusing means the owner never hears about the failure that
    prompted it — trading a stuck queue for silence, which is worse. The repair
    is deterministic and case-folding only in practice, so two runs of the same
    cause still collide on one key and the idempotence the queue relies on holds.

    A key that survives this unchanged is one the endpoint will take. That is the
    property `enqueue` depends on: nothing unsendable-by-construction can enter
    the retry loop, so the 2026-08-03 shape cannot recur with a different string.
    """
    safe = re.sub(r"[^a-z0-9:._-]", "-", (key or "").lower()).lstrip(":._-")[:160]
    return safe or "alert"


#: Delivered/cancelled entries kept for forensics. Enough to reconstruct an
#: outage after the fact, small enough that the committed file stays reviewable.
HISTORY_KEPT = 100

#: After this many failed delivery attempts an entry stops being "the host is
#: having a bad night" and starts being "nobody is ever going to read this".
#: ops_status.py escalates at this line. Sized off the drainer's cadence: at a
#: probe every 15 minutes, 12 attempts is roughly three hours of continuous
#: failure, which is far longer than any outage this host has produced and
#: short enough that a wrong API key does not sit undiscovered for a week.
FAIL_LOUD_ATTEMPTS = 12

VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty() -> dict:
    return {"version": VERSION, "updated_at": _now(), "entries": []}


def load(path: Path | str = OUTBOX) -> dict:
    """Read the outbox. A missing or unreadable file is an EMPTY outbox, never
    an exception: this is called from the failure path of the alerter, and a
    notifier that crashes while handling a failure has told nobody anything."""
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        doc = json.loads(p.read_text() or "{}")
    except (OSError, ValueError) as exc:
        print(f"alert_outbox: {p} is unreadable ({exc}) — starting a fresh outbox")
        return empty()
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        print(f"alert_outbox: {p} has an unexpected shape — starting a fresh outbox")
        return empty()
    doc.setdefault("version", VERSION)
    return doc


def save(doc: dict, path: Path | str = OUTBOX) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = _now()
    doc["entries"] = _trim(doc.get("entries", []))
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def _trim(entries: list[dict]) -> list[dict]:
    """Every pending entry, plus the most recent HISTORY_KEPT settled ones."""
    pending = [e for e in entries if e.get("state") == "pending"]
    settled = [e for e in entries if e.get("state") != "pending"]
    settled.sort(key=lambda e: e.get("settled_at") or e.get("raised_at") or "")
    return pending + settled[-HISTORY_KEPT:]


def pending(doc: dict) -> list[dict]:
    """Undelivered entries, oldest first — which is also delivery order. An
    alert and its later recovery must never reach the inbox back to front."""
    out = [e for e in doc.get("entries", []) if e.get("state") == "pending"]
    out.sort(key=lambda e: e.get("raised_at") or "")
    return out


def stuck(doc: dict) -> list[dict]:
    return [e for e in pending(doc) if e.get("attempts", 0) >= FAIL_LOUD_ATTEMPTS]


def enqueue(doc: dict, *, key: str, kind: str, scope: str, payload: dict,
            reason: str = "", run_url: str = "") -> tuple[str, dict | None]:
    """Hold one undelivered alert. Idempotent in `key`.

    Returns (outcome, entry). Outcome is one of:
      'queued'      a new entry is now held
      'already'     an identical undelivered entry was already held; only its
                    attempt count and last error moved
      'cancelled'   a recovery arrived for a scope whose RED was never sent, so
                    both were dropped. Nothing was ever mailed, so there is
                    nothing to clear and the owner hears about none of it.

    `cancelled` is the entire reason this function knows about `scope`. Without
    it, a host outage that reddened four workflows and then healed would deliver
    four "CI RED" mails followed by four "RECOVERED" mails describing problems
    that no longer existed — eight emails about nothing, on a channel whose
    whole value is that the owner still reads it.
    """
    entries = doc.setdefault("entries", [])

    if kind == "resolve":
        superseded = [e for e in entries
                      if e.get("state") == "pending" and e.get("kind") == "alert"
                      and e.get("scope") == scope]
        if superseded:
            for e in superseded:
                e["state"] = "cancelled"
                e["settled_at"] = _now()
                e["settled_because"] = (
                    "recovered before this was ever delivered, so there was "
                    "nothing to tell the owner and nothing to clear")
            return "cancelled", None

    for e in entries:
        if e.get("state") == "pending" and e.get("key") == key:
            e["attempts"] = e.get("attempts", 0) + 1
            e["last_attempt_at"] = _now()
            if reason:
                e["last_error"] = reason
            return "already", e

    entry = {
        "key": key,
        "kind": kind,
        "scope": scope,
        "state": "pending",
        "raised_at": _now(),
        "attempts": 1,
        "last_attempt_at": _now(),
        "last_error": reason,
        "run_url": run_url,
        "payload": payload,
    }
    entries.append(entry)
    return "queued", entry


def record_attempt(entry: dict, error: str) -> None:
    entry["attempts"] = entry.get("attempts", 0) + 1
    entry["last_attempt_at"] = _now()
    entry["last_error"] = error


def mark_delivered(entry: dict, note: str = "") -> None:
    entry["state"] = "delivered"
    entry["settled_at"] = _now()
    entry["settled_because"] = note or "delivered"
    entry.pop("payload", None)  # the body is large and its job is done


def describe(doc: dict) -> list[str]:
    """Lines for ops_status.py. Kept here so the dashboard and the queue can
    never describe the same backlog two different ways."""
    held = pending(doc)
    if not held:
        settled = [e for e in doc.get("entries", []) if e.get("state") != "pending"]
        if not settled:
            return ["empty — every alert raised so far reached the owner directly"]
        last = max(settled, key=lambda e: e.get("settled_at") or "")
        return [f"empty — nothing held; last settled {last.get('settled_at')} "
                f"({last.get('settled_because', '')[:60]})"]

    lines = [f"{len(held)} alert(s) HELD — raised but not yet delivered"]
    for e in held[:5]:
        subject = (e.get("payload") or {}).get("subject", e.get("key", ""))
        lines.append(f"  {e.get('raised_at')}  x{e.get('attempts', 0)}  "
                     f"{subject[:72]}")
    if len(held) > 5:
        lines.append(f"  ... and {len(held) - 5} more")
    return lines


def enqueue_envelope(envelope_path: Path | str,
                     outbox_path: Path | str = OUTBOX) -> str:
    """Fold an envelope written by ci_alert.py into the committed outbox.

    Separate from writing the envelope ON PURPOSE. A rejected push is answered
    by fetching main, resetting onto it and re-deriving — the lesson merge_db.py
    and schedule-link-hygiene.yml both already learned — and re-deriving means
    running this again against the freshly fetched file. `enqueue` is idempotent
    in `key`, so the loop is conflict-free by construction and a race costs a
    retry rather than a duplicate email.
    """
    env = json.loads(Path(envelope_path).read_text())
    doc = load(outbox_path)

    # NOTHING THE ENDPOINT WILL CERTAINLY REJECT GETS INTO THE RETRY LOOP.
    # This is the only door into the committed outbox, so it is the only place
    # this has to hold. A key that fails KEY_SAFE earns a settled 400 on every
    # attempt forever, and the queue's escalation is built for "the host is
    # having a bad night" — twelve failures and it declares the entry stuck,
    # which reddens host-watch on every tick from then on. Repairing the key
    # here costs nothing when it was already valid and, when it was not, turns a
    # permanently undeliverable alert into a delivered one.
    key = env["key"]
    safe = deliverable_key(key)
    original = None
    if safe != key:
        original = key
        print(f"::warning::alert key {key!r} is not one /alert will accept; "
              f"queued as {safe!r}. A key it rejects is a settled 400, not an "
              f"outage, and retrying it is what took host-watch down on "
              f"2026-08-03.")

    outcome, entry = enqueue(
        doc,
        key=safe, kind=env.get("kind", "alert"), scope=env.get("scope", ""),
        payload=env["payload"], reason=env.get("reason", ""),
        run_url=env.get("run_url", ""))
    if original and entry.get("original_key") != original:
        entry["original_key"] = original      # so the repair is auditable
    save(doc, outbox_path)
    return outcome


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="inspect the durable alert outbox")
    ap.add_argument("command", choices=["status", "list", "enqueue"],
                    nargs="?", default="status")
    ap.add_argument("--path", default=str(OUTBOX))
    ap.add_argument("--envelope", help="an envelope file written by ci_alert.py")
    args = ap.parse_args(argv)

    if args.command == "enqueue":
        if not args.envelope:
            print("::error::alert_outbox.py enqueue needs --envelope")
            return 2
        print(f"outbox: {enqueue_envelope(args.envelope, args.path)}")
        return 0

    doc = load(args.path)
    for line in describe(doc):
        print(line)
    if args.command == "list":
        print(json.dumps(doc, indent=2, sort_keys=True))

    blocked = stuck(doc)
    if blocked:
        print(f"::error::{len(blocked)} alert(s) have failed delivery "
              f"{FAIL_LOUD_ATTEMPTS}+ times and are not reaching the owner.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

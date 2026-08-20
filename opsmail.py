#!/usr/bin/env python3
"""Operational mail, sent by Resend, off the host it reports about.

WHY THIS FILE EXISTS AT ALL
---------------------------
Until now every operational email in this repository went out through
`/wp-json/talent/v1/alert`, which is a route on the WordPress site the alerts
are ABOUT. On 2026-07-31 Bluehost answered 504 for everything under `/blog/`
for about seven minutes: enrich failed, drain-writers correctly went red, and
the alerter then failed four times saying "HTTP 504 from /alert". The alarm was
mute at the exact moment it was needed, and it manufactured four extra red runs
while it was. The host went down twice again on 2026-08-19.

The held outbox was the right fix for DELIVERY, and it stays. It is not a fix
for the COUPLING. An alert that has to reach the failing host in order to
describe the failure is one round trip away from silence, however good the
queue behind it is. Sending through Resend removes the coupling: the alarm no
longer depends on the thing it monitors.

WHY RESEND AND NOT THE SUBSCRIBER RELAY
---------------------------------------
`/alert` calls bare `wp_mail()`. On this install the Brevo plugin intercepts
`wp_mail` and replaces the whole From line with the SUBSCRIBER relay identity,
so operational alarms arrived wearing the reader newsletter's face, in the same
inbox, beside mail the owner had subscribed to. Mail that looks like a
newsletter gets filed with the newsletter, and after that the alarm is
decoration. That is the same failure as eight identical emails in an afternoon,
with a longer fuse.

It is also a budget question. Brevo's free tier is a few hundred emails a day
and it is where readers live. Operations moves to Resend, whose free tier is
about 100 a day and 3,000 a month: ample for alarms and useless for a mailing
list, which is exactly why this is the right way round. A bad afternoon of red
CI must never be able to eat an allowance readers depend on.

SENDER IDENTITY
---------------
`OPS_MAIL_FROM` names the operations sender and is READ IN THIS FILE AND
NOWHERE ELSE. A second reader is a second default, and a second default is how
two alarms end up in two folders. The display name follows the sibling
tracker's convention exactly, so one mail rule catches both:

    From:    Talent Intelligence Tracker Ops <ops@asktherecruiter.com>
    Subject: [Talent Intelligence Tracker] ...

The prefix is what `/alert` stamped, preserved byte for byte including the
trailing space, because the owner's filters key off it.

STDLIB ONLY, on purpose. This is the notification path. It runs in workflows
that do no `pip install` at all, so no dependency resolver can take the alarm
down. Same reasoning as `ci_alert.py` and `alert_outbox.py`.

THE KEY IS NEVER PRINTED. It is read from the environment, sent in a header,
and never echoed, logged or included in any error string. `_redact` scrubs it
out of anything the API hands back before that text reaches a log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.resend.com"

#: EVERY request needs a real-looking User-Agent, and this was learned the same
#: way twice. The WP host's ModSecurity blocks `python-requests` outright. In
#: the sibling tracker Resend's own API, which sits behind Cloudflare, answered
#: the very first selftest with `403 Error 1010: Access denied, the site owner
#: has banned your browser signature` to urllib's default `Python-urllib/3.12`.
#: Every alert would have read as a SETTLED refusal, been held, and never
#: arrived.
UA = "TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)"

#: Who operational mail comes from. Deliberately not the reader relay: an alarm
#: that arrives looking like a newsletter is one the owner filters with the
#: newsletter. Overridable so the sending domain can change without a code
#: change, because the verified domain is a property of the Resend account.
DEFAULT_FROM = "Talent Intelligence Tracker Ops <ops@asktherecruiter.com>"
DEFAULT_TO = "info@asktherecruiter.com"

#: Prefix on every operational subject. `tit_api_alert()` stamped
#: `[Talent Intelligence Tracker] ` and the owner's filters key off it, so it is
#: preserved byte for byte, trailing space included.
SUBJECT_PREFIX = "[Talent Intelligence Tracker] "

#: Worth asking again in a few seconds. 429 is Resend's rate limit, 5xx is
#: Resend having a bad moment. 401 and 403 are a bad key and 422 is a bad
#: sender, and retrying a settled no only makes the run longer.
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

#: Seconds between in-run retries, matching ci_alert's shape. Three attempts
#: over about fifteen seconds catches a wobble. It does not try to outlast an
#: outage, because outlasting one is the outbox's job.
BACKOFF = (3, 12)


def sender() -> str:
    return os.environ.get("OPS_MAIL_FROM", "").strip() or DEFAULT_FROM


def recipient() -> str:
    return os.environ.get("OPS_MAIL_TO", "").strip() or DEFAULT_TO


def _key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def _redact(text: str) -> str:
    """Never let the key reach a log, even by way of an echoed error body."""
    key = _key()
    out = str(text or "")
    if key and key in out:
        out = out.replace(key, "<redacted>")
    return out


def configured() -> bool:
    return bool(_key())


def _request(method: str, path: str, body=None, extra_headers=None):
    """One request. Returns (status, parsed_body_or_text)."""
    headers = {"Authorization": f"Bearer {_key()}",
               "Content-Type": "application/json",
               "Accept": "application/json",
               "User-Agent": UA}
    headers.update(extra_headers or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw or "{}")
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:400] if exc.fp else ""
        return exc.code, raw
    except urllib.error.URLError as exc:
        return 0, f"could not reach Resend: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - a notifier must not raise
        return 0, f"could not reach Resend: {exc}"


def send_once(subject: str, body: str, idempotency_key: str = ""):
    """One send. Returns (ok, note, transient).

    `idempotency_key` is a belt to the ledger's braces. Resend honours it for
    24 hours and returns the original result rather than sending twice, so two
    runners that raced past the committed ledger still produce one email. The
    ledger is what makes RECOVERED work and what holds the fourteen day window;
    this only closes the seconds-wide gap between reading the ledger and
    writing it.
    """
    if not _key():
        return False, "RESEND_API_KEY is not set", False
    payload = {"from": sender(), "to": [recipient()],
               "subject": (SUBJECT_PREFIX + (subject or "")).strip()[:200],
               "text": body or ""}
    extra = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    status, answer = _request("POST", "/emails", payload, extra)
    if status in (200, 201) and isinstance(answer, dict) and answer.get("id"):
        return True, "emailed the owner", False
    if status == 0:
        return False, _redact(str(answer)), True
    return (False,
            f"HTTP {status} from Resend: {_redact(str(answer))[:300]}",
            status in TRANSIENT_STATUS)


def send(subject: str, body: str, idempotency_key: str = "", sleep=time.sleep):
    """Send, retrying transient failures in-run. Returns (ok, note, transient)."""
    ok, note, transient = send_once(subject, body, idempotency_key)
    for delay in BACKOFF:
        if ok or not transient:
            break
        print(f"  Resend did not answer ({note}): retrying in {delay}s")
        sleep(delay)
        ok, note, transient = send_once(subject, body, idempotency_key)
    return ok, note, transient


def verified_domains():
    """-> (list of "name (status)", note). Never raises, never prints the key.

    Read-only, and it names no secret. It exists so a session can find out what
    this account is actually allowed to send as, instead of guessing at a From
    address and reading a 422 in a failed workflow.
    """
    if not _key():
        return [], "RESEND_API_KEY is not set"
    status, answer = _request("GET", "/domains")
    if status != 200 or not isinstance(answer, dict):
        return [], f"HTTP {status}: {_redact(str(answer))[:200]}"
    rows = answer.get("data") or []
    return ([f"{d.get('name')} ({d.get('status')})"
             for d in rows if isinstance(d, dict)], "ok")


def render(subject: str, body: str, idempotency_key: str = "") -> dict:
    """The exact message that WOULD be sent, without sending it.

    This is how the sender and the prefix are verified without spending a
    delivery or putting mail in the owner's inbox. `--render` prints it.
    """
    return {"from": sender(), "to": [recipient()],
            "subject": (SUBJECT_PREFIX + (subject or "")).strip()[:200],
            "text": body or "",
            "idempotency_key": idempotency_key or ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="operational mail through Resend")
    ap.add_argument("--render", action="store_true",
                    help="print the message that would be sent, and send nothing")
    ap.add_argument("--domains", action="store_true",
                    help="list the sending domains this account has verified")
    ap.add_argument("--send", action="store_true", help="send one test email")
    ap.add_argument("--subject", default="OPS MAIL SELFTEST")
    ap.add_argument("--body", default="")
    args = ap.parse_args(argv)

    if args.render:
        # Deliberately before the key check: proving the identity is right must
        # not require holding the credential.
        print(json.dumps(render(args.subject, args.body), indent=2))
        return 0

    if not configured():
        print("::error::RESEND_API_KEY is not set, so no operational mail can "
              "be sent. Nothing was attempted.")
        return 1

    print(f"from: {sender()}")
    print(f"to:   {recipient()}")

    if args.domains or not args.send:
        names, note = verified_domains()
        print(f"verified sending domains: {', '.join(names) or '(none)'} [{note}]")
        if not args.send:
            return 0

    body = args.body or (
        "This is the operational mail selftest.\n\n"
        "It proves that CI alerts now leave through Resend rather than through "
        "the WordPress host they report about. If you are reading it, the alarm "
        "no longer depends on the thing it monitors.\n\n"
        "Nothing is wrong. Nothing needs doing.\n")
    ok, note, transient = send(args.subject, body)
    print(f"send: {note} (transient={transient})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

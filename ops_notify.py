#!/usr/bin/env python3
"""The one door operational mail leaves by.

WHY THIS FILE EXISTS
--------------------
`opsmail.py` records at length why operational mail left the WordPress host.
This file exists because of how that kind of change goes wrong: in the sibling
tracker three callers were converted and nine were not, and NOTHING NOTICED,
because a wrong From line produces no error anywhere. The mail arrives. It just
arrives wearing the wrong face.

The unconverted callers went on POSTing to `/wp-json/talent/v1/alert`, which
calls bare `wp_mail()`. On this install `wp_mail` is intercepted by the Brevo
plugin, which replaces the whole From line with the SUBSCRIBER relay identity.
So an operational alarm arrives from the newsletter address, under the reader
newsletter's display name, in the same inbox as a digest the owner subscribed
to.

That is not a cosmetic complaint. Mail that looks like a newsletter gets filed
with the newsletter, and after that the alarm is decoration. It is the same
failure mode as eight identical emails in one afternoon, with a longer fuse.

WHAT THIS GUARANTEES
--------------------
One From, one subject prefix, one transport, for everything only the operator
receives.

  From:    opsmail.sender()          "Talent Intelligence Tracker Ops <ops@...>"
  Subject: opsmail.SUBJECT_PREFIX    "[Talent Intelligence Tracker] "

Both are applied by `opsmail.send_once`, not here and not by any caller, so
there is exactly one place either can be wrong. `tests/test_ops_sender.py`
fails if a new module grows its own.

WHAT THIS DOES NOT CHANGE
-------------------------
Dedup semantics. `ci_alert.post_alert` rules on the message against the
committed ledger in `data/alert_state.json`, and `alert_state.decide()` mirrors
the endpoint's three shapes exactly:

    {subject, body}                  send every time, no dedup
    {subject, body, dedupe_key}      raise once per cause, remind at 14 days
    {subject, body, resolve_scope}   clear, mailing once if anything was open

Callers are ported with the shape they already had. A caller that was undeduped
stays undeduped, because changing an alarm's cadence while changing its From
line would make a later "why did this stop mailing?" unanswerable.

NEVER PRINTS A SUBJECT OR A BODY. The only thing that reaches stdout is a fixed
phrase and a delivery note, so a reporter that carries company names cannot leak
them into a public run log by way of this file.

BEST EFFORT, NEVER RAISES. Every caller is a reporting tail on a job that has
already done its real work. A notifier that raises while handling somebody
else's failure has told nobody anything, and it turns a delivery problem into a
red run, which is the amplification loop CLAUDE.md warns about.
"""

from __future__ import annotations

import opsmail


def configured() -> bool:
    """Can operational mail be sent at all right now?

    Callers gate on this instead of on `WP_SITE_URL`/`WP_API_KEY`, which is what
    they used to read and which has had nothing to do with sending mail since
    the Resend move.
    """
    return opsmail.configured()


def notify(subject: str, body: str, *, dedupe_key: str = "",
           resolve_scope: str = "", what: str = "operational alert") -> bool:
    """Send one operational email. Returns True if it went out.

    `what` is a fixed, name-free description used in the one line this prints,
    so a run log says which reporter spoke without quoting anything it said.
    """
    if not configured():
        print(f"ops mail: RESEND_API_KEY is not set, so the {what} was not "
              f"sent. Nothing was lost that the next run will not re-derive.")
        return False

    payload = {"subject": subject or "", "body": body or ""}
    if dedupe_key:
        payload["dedupe_key"] = dedupe_key
    if resolve_scope:
        payload["resolve_scope"] = resolve_scope

    try:
        # Imported here rather than at module scope: `ci_alert` imports
        # `opsmail`, and a top-level import in both directions is a cycle the
        # notification path does not need to own.
        import ci_alert
        ok, note, _transient = ci_alert.post_alert("", "", payload)
    except Exception as exc:  # noqa: BLE001 - defensive
        # The exception text, not the payload. See the module docstring.
        print(f"ops mail: the {what} could not be sent ({type(exc).__name__}: "
              f"{exc}). This is non-fatal.")
        return False

    print(f"ops mail: {what}: {note}")
    return bool(ok)


def resolve(scope: str, subject: str, body: str,
            what: str = "recovery notice") -> bool:
    """Clear an open cause. Silent when nothing was open, which is what makes it
    safe to call after every healthy run."""
    return notify(subject, body, resolve_scope=scope, what=what)

#!/usr/bin/env python3
"""The channel that does not touch the host: exactly ONE GitHub issue.

WHY THIS EXISTS
---------------
The email path runs through `/alert` on the WordPress site. When the site is
down, that path is down, and a queued alert reaches the owner only when the host
comes back — which is right for the CONTENT of the alert and useless as a way of
learning, tonight, that the site is down. Something has to be able to speak
while the host cannot.

WHY GITHUB'S OWN NOTIFICATIONS, AND WHY AN ISSUE RATHER THAN THE RED RUN
------------------------------------------------------------------------
GitHub is the only notification channel this project already has that is not on
Bluehost, and it costs nothing. But the owner's objection to it is exact and
correct: "undeduped and noisy — about fifteen emails for one defect tonight."
That is what a red RUN does. Thirty workflows each mailing on each failure is
the alarm-fatigue problem this repo has spent a lot of effort not creating.

An ISSUE has the property a run notification lacks: it can be UPDATED. Watching
your own repository mails you when an issue is opened and when it is closed, and
**editing an issue's body mails nobody**. So one outage is:

    open   -> 1 email  ("the site is down, alerts are being held")
    update -> 0 emails (the body accumulates what else broke, and when)
    close  -> 1 email  ("the site is back, N held alerts were delivered")

Two emails for an outage that produced fifteen. The deduplication is structural
rather than a suppression rule that can drift out of step with what was sent —
there is one issue because there is one MARKER, and this module refuses to open
a second while one is open.

WHAT THIS IS NOT
----------------
Not a replacement for the email. The issue says "you are not being told things";
the queued alerts say what those things were, and they still arrive by mail when
the host returns. Not a paid service, and deliberately not a second webhook: a
channel nobody configured is a channel that is silently broken.

DEGRADING: every function here returns a plain (ok, note) and never raises. It
is called from failure paths. If `gh` is missing or the token cannot write
issues, the caller prints the note loudly and carries on to the queue — a
fallback that takes down the primary is worse than no fallback.
"""

from __future__ import annotations

import json
import os
import subprocess

#: The invisible tie between an outage and its issue. Titles get edited and
#: labels have to exist in the repo before they can be applied; an HTML comment
#: in the body needs neither and cannot be lost by a rename.
MARKER = "<!-- alert-fallback:host-unreachable -->"

TITLE = "Alerts are not reaching the owner (the /alert host is unreachable)"


def _gh(args: list[str], repo: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(["gh", *args, "-R", repo],
                              capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, ("gh is not installed on this runner, so the "
                       "host-independent fallback channel is unavailable")
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"gh failed to run ({exc})"
    if proc.returncode != 0:
        return False, f"gh {args[0]} {args[1] if len(args) > 1 else ''} " \
                      f"exited {proc.returncode}: {proc.stderr.strip()[:300]}"
    return True, proc.stdout


def find_open(repo: str) -> tuple[int | None, str, str]:
    """(number, body, note) for the one open fallback issue, if any."""
    ok, out = _gh(["issue", "list", "--state", "open", "--limit", "50",
                   "--json", "number,body,title"], repo)
    if not ok:
        return None, "", out
    try:
        for issue in json.loads(out or "[]"):
            if MARKER in (issue.get("body") or ""):
                return issue["number"], issue.get("body") or "", ""
    except ValueError as exc:
        return None, "", f"could not parse gh issue list output ({exc})"
    return None, "", ""


def open_or_update(repo: str, *, line: str, preamble: str = "") -> tuple[bool, str]:
    """Ensure the single fallback issue exists and carries `line`.

    Opening mails the owner once. Updating does not mail at all, which is the
    whole reason the accumulating detail goes in the BODY and not in a comment.
    """
    number, body, note = find_open(repo)
    if note:
        return False, note

    if number is None:
        text = "\n".join([
            MARKER,
            preamble or _default_preamble(),
            "",
            "## What has been held so far",
            "",
            line,
            "",
            "_This issue is edited in place as more is held; editing does not "
            "email. It closes itself when the host answers again and the queue "
            "drains, and that close is the second and last email._",
        ])
        ok, out = _gh(["issue", "create", "--title", TITLE, "--body", text], repo)
        return (ok, "opened the fallback issue — this is the ONE email the "
                    "outage sends" if ok else out)

    if line.strip() and line.strip() in body:
        return True, f"issue #{number} already records this"

    marker = "## What has been held so far"
    if marker in body:
        head, tail = body.split(marker, 1)
        body = f"{head}{marker}\n{tail.rstrip()}\n{line}\n"
    else:
        body = f"{body.rstrip()}\n\n{line}\n"
    ok, out = _gh(["issue", "edit", str(number), "--body", body], repo)
    return (ok, f"updated issue #{number} silently (no email)" if ok else out)


def close(repo: str, *, note: str) -> tuple[bool, str]:
    """Close the fallback issue. This is the second and final email."""
    number, _body, problem = find_open(repo)
    if problem:
        return False, problem
    if number is None:
        return True, "no fallback issue was open"
    ok, out = _gh(["issue", "close", str(number), "--comment", note], repo)
    return (ok, f"closed issue #{number}" if ok else out)


def _default_preamble() -> str:
    return (
        "The `/alert` endpoint that turns a red CI run into an email lives on "
        "the same WordPress host it reports about, so while that host is "
        "unreachable **no alert email can be sent**.\n\n"
        "Alerts raised during the outage are not lost. They are held in "
        "`data/alert_outbox.json` (committed, so it outlives the runner) and "
        "delivered by the next `host-watch` run that finds the host answering.\n\n"
        "**Nothing is required of you.** This issue exists because silence "
        "during an outage is the one thing the alerting design must never do.")


def repo_from_env(default: str = "dk-forge/talent-intelligence-tracker") -> str:
    return os.environ.get("GITHUB_REPOSITORY") or default

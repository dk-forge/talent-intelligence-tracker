#!/usr/bin/env python3
"""Which alarms are open, held in the repository instead of on the host.

WHY THIS MOVED, AND WHY THAT IS NOT A DOWNGRADE
-----------------------------------------------
Dedup by CAUSE is the whole design of this repo's alerting, not a refinement.
An alarm is RAISED once per cause and RECOVERED exactly once on the next green
run, because an alarm that mails eight times in an afternoon is one you learn to
filter, and a filtered alarm is the original silence wearing a new hat.

That open/resolved state used to live server side, in the `tit_ci_alert_state`
option next to `wp_mail`. It lived there for a good reason: a "sent" record that
can disagree with what was actually sent is worth less than no record.

Sending moved to Resend so the alarm would stop depending on the host it
monitors, and a fire-and-forget send keeps no record of its own. So the ledger
came here, to a committed file, which this repo already trusts for
`data/alert_outbox.json` and `data/host_status.json`. The repo IS the memory.

THE RACE THIS FILE EXISTS TO CLOSE
----------------------------------
A committed file read at checkout and written at push has a window tens of
seconds wide. Two runners that both read "nothing is open" both send, and the
dedup guarantee is gone. That would be a real weakening, and a quota fix that
costs the dedup guarantee is a bad trade.

So the claim is committed BEFORE the email is sent, and the commit is the
compare-and-swap. `git push` to main is atomic: exactly one racing runner wins
it. The loser re-derives on the new main, finds the cause already open, and goes
quiet. The window is closed by the same mechanism that stores the state.

DO NOT REORDER THAT. Writing the ledger after the send is the unlocked version
with extra steps.

Two consequences worth naming rather than discovering:

* An alarm is recorded open before it is known to be delivered. The endpoint
  recorded only on a successful send. The promise is kept a different way: an
  undeliverable alert is HELD in `data/alert_outbox.json` and delivered by the
  drain, so a claimed alarm is a queued one. The only gap left is "could neither
  send NOR hold", which is already the one state that reddens the run.
* Losing the push five times running does NOT silence the alert. Five failed
  pushes with the cause still absent means five DIFFERENT concurrent alarms, not
  a duplicate of ours. Silence is the worse failure, so it sends and says loudly
  that the claim went unrecorded.

Resend's own `Idempotency-Key` is a second, independent guard on the same 24
hours. This ledger is what makes RECOVERED work and what holds the fourteen day
reminder window; the header only closes a gap of seconds, and it names a
TRANSITION rather than a cause. See `Decision.idempotency_key()`.

Stdlib only. This is the notification path and no dependency resolver may take
it down.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: Beside the outbox, in `data/`, which is this repo's committed memory and
#: already has the fetch/reset/enqueue/push machinery a racing commit needs.
STATE = ROOT / "data" / "alert_state.json"

VERSION = 1

#: One reminder a fortnight, no more. Total silence until a green run would mean
#: a breakage the owner missed once is never mentioned again. Twice a month is a
#: reminder, not alarm fatigue. Mirrored from `tit_api_alert()`, which it
#: replaces.
REMIND_AFTER_SECONDS = 14 * 24 * 3600

#: A caller looping on a mutating cause key could otherwise grow this file
#: without bound. Keep the newest entries and drop the oldest by first-seen.
MAX_OPEN = 200

#: Set by ci-alert.yml and host-watch.yml. Anywhere else, including every test
#: and every local run, the ledger is read and written in place with no git.
COMMIT_ENV = "ALERT_STATE_COMMIT"


def _now() -> int:
    return int(time.time())


def _stamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _ago(seconds: int) -> str:
    if seconds < 90 * 60:
        return f"{max(1, seconds // 60)} minutes"
    if seconds < 36 * 3600:
        return f"{seconds // 3600} hours"
    return f"{seconds // 86400} days"


def empty() -> dict:
    return {"version": VERSION, "updated_at": _stamp(_now()), "open": {}}


def load(path: Path | str = STATE) -> dict:
    """Read the ledger. A missing or unreadable file is an EMPTY ledger and
    never an exception: this runs on the failure path, and a notifier that
    crashes while handling a failure has told nobody anything."""
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        doc = json.loads(p.read_text() or "{}")
    except (OSError, ValueError) as exc:
        print(f"alert_state: {p} is unreadable ({exc}), starting a fresh ledger")
        return empty()
    if not isinstance(doc, dict):
        return empty()
    doc.setdefault("version", VERSION)
    doc.setdefault("open", {})
    if not isinstance(doc["open"], dict):
        doc["open"] = {}
    return doc


def save(doc: dict, path: Path | str = STATE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entries = doc.get("open") or {}
    if len(entries) > MAX_OPEN:
        newest = sorted(entries.items(), key=lambda kv: int(kv[1].get("first", 0)))
        entries = dict(newest[-MAX_OPEN:])
    doc["open"] = dict(sorted(entries.items()))
    doc["updated_at"] = _stamp(_now())
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


class Decision:
    """What the ledger says to do about one message.

    `kind` is 'raise', 'resolve' or 'silent'. A silent decision is the common
    case on a green run and is what makes it safe to post a resolve after EVERY
    success without the clear becoming noise itself.
    """

    def __init__(self, kind, *, subject="", body="", note="", key="",
                 scope="", cleared=(), transition=0):
        self.kind = kind
        self.subject = subject
        self.body = body
        self.note = note
        self.key = key
        self.scope = scope
        self.cleared = list(cleared)
        #: The instant this particular alarm TRANSITION happened, taken from the
        #: ledger rather than from the clock. For a raise it is when the cause
        #: opened; for a resolve it is the last sighting of the newest thing
        #: being cleared. See idempotency_key() for why it has to be here.
        self.transition = int(transition or 0)

    @property
    def sends(self) -> bool:
        return self.kind in ("raise", "resolve")

    def idempotency_key(self) -> str:
        """Stable for one alarm TRANSITION, not for one cause.

        A cause can legitimately open, close and open again inside Resend's
        24-hour idempotency window. A key that names the CAUSE then collides:
        the second, genuine alarm comes back HTTP 409
        `invalid_idempotent_request` and is refused. That happened twice in the
        sibling tracker on 2026-08-19.

        Both obvious fixes are worse. Folding the body into the key makes every
        run a distinct key, so Resend dedupes nothing and the second guard is
        gone. Making the body byte-identical per cause makes Resend answer 200
        with the ORIGINAL email's id, having sent nothing, so the genuine second
        alarm is swallowed in silence instead of failing loudly. A silent pass is
        the expensive mistake; a loud refusal is the cheap one.

        So the key names the transition. A re-raise after a clear is a different
        transition and gets a different key; a retry or a re-drain of the SAME
        transition gets the same one and collapses, which is the property this
        guard was always supposed to have.
        """
        if self.kind == "raise":
            # No cause key is the legacy "send it every time" shape. There is
            # nothing to dedup, so there must be no idempotency key: a shared
            # `tit-raise-` would make every undeduped alert collide with every
            # other one inside the same 24 hours.
            if not self.key:
                return ""
            return f"tit-raise-{self.key}-{self.transition}"
        if self.kind == "resolve":
            return f"tit-resolve-{self.scope}-{self.transition}"
        return ""


def decide(state: dict, payload: dict, now: int | None = None) -> Decision:
    """Pure. Given the ledger and a message, say whether it is sent and how.

    Mirrors `tit_api_alert()` exactly, because two definitions of "have we told
    them yet" is how one becomes wrong quietly:

      {subject, body}                 send it, no dedup (the legacy shape)
      {subject, body, dedupe_key}     raise once per cause, remind at 14 days
      {subject, body, resolve_scope}  clear, mailing once if anything was open

    The one thing genuinely lost is the endpoint's legacy three-day suppression
    by subject hash, which this never implemented. The callers that relied on it
    had subjects carrying a count, so it almost never fired for them.
    """
    now = _now() if now is None else now
    subject = str(payload.get("subject") or "")
    body = str(payload.get("body") or "")
    resolve = str(payload.get("resolve_scope") or "")
    dedupe = str(payload.get("dedupe_key") or "")
    entries = state.get("open") or {}

    if resolve:
        open_keys = sorted(k for k in entries if k.startswith(resolve + ":"))
        if not open_keys:
            return Decision("silent", note="nothing was open for this scope",
                            scope=resolve)
        extra = f"\n\nThis clears {len(open_keys)} open alert(s):\n"
        for k in open_keys:
            extra += "  - " + str(entries[k].get("subject") or k) + "\n"
        # The transition is the newest sighting among the things being cleared.
        # Ledger-derived, so two runners racing on the SAME clear agree on it,
        # and clearing this scope again tomorrow is a different number.
        newest = max((int(entries[k].get("last") or entries[k].get("first") or 0)
                      for k in open_keys), default=0)
        return Decision("resolve", subject=subject, body=body + extra,
                        scope=resolve, cleared=open_keys, transition=newest)

    if dedupe:
        prior = entries.get(dedupe)
        # The instant this cause OPENED, which is what apply() will store as
        # `first`. A cause already open keeps it, so the fortnightly STILL
        # FAILING reminder is the same transition as the raise it repeats. A
        # cause that was cleared and has come back has no prior, so it opens now
        # and is a new transition.
        opened = now
        if prior:
            first = int(prior.get("first", now))
            last = int(prior.get("last", first))
            opened = first
            if (now - last) < REMIND_AFTER_SECONDS:
                return Decision(
                    "silent", key=dedupe,
                    note=("suppressed: this exact cause is already open "
                          f"(raised {_ago(now - first)} ago)"))
            subject = "STILL FAILING: " + subject
        return Decision("raise", subject=subject, body=body, key=dedupe,
                        transition=opened)

    return Decision("raise", subject=subject, body=body)


def apply(state: dict, decision: Decision, now: int | None = None) -> None:
    """Fold a decision into the ledger. Called before the send, so the commit
    that records it is the claim that stops a racing runner sending too."""
    now = _now() if now is None else now
    entries = state.setdefault("open", {})
    if decision.kind == "resolve":
        # Cleared whether or not the mail lands. The flag answers "is there an
        # unresolved failure", and the answer is now no. Leaving it open would
        # suppress the NEXT genuine alert for this cause, which is the more
        # expensive of the two mistakes.
        for k in decision.cleared:
            entries.pop(k, None)
        return
    if decision.kind == "raise" and decision.key:
        prior = entries.get(decision.key) or {}
        entries[decision.key] = {"first": int(prior.get("first", now)),
                                 "last": now, "subject": decision.subject}


# ---------------------------------------------------------------------------
# The claim, which is a git push
# ---------------------------------------------------------------------------

def _git(*args, cwd=None):
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=120, cwd=cwd or str(ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"git {args[0]} could not run ({exc})"
    return proc.returncode, (proc.stdout + proc.stderr).strip()[:300]


def commit_enabled() -> bool:
    return os.environ.get(COMMIT_ENV, "").lower() in {"1", "true", "yes"}


def _re_derive(path, payload, now):
    state = load(path)
    return state, decide(state, payload, now)


# Deliberately NOT a config knob. Five attempts is the same ceiling the outbox
# hold loop uses, and a sixth would not change the verdict: if five racing
# pushes all landed and the cause is still absent, they were five different
# alarms and ours is real.
CLAIM_ATTEMPTS = 5


def state_path(path: Path | str | None = None) -> Path:
    """The ledger this process reads. `ALERT_STATE_PATH` exists so the offline
    test suite can drive the real code against a temporary file rather than
    writing the committed one."""
    return Path(path or os.environ.get("ALERT_STATE_PATH") or STATE)


def claim(payload: dict, *, path: Path | str | None = None,
          now: int | None = None, sleep=time.sleep, commit: bool | None = None):
    """Decide, and record the decision durably BEFORE anything is sent.

    Returns (decision, recorded). `recorded` is False only when the ledger could
    not be written at all, which is loud and still sends.
    """
    now = _now() if now is None else now
    commit = commit_enabled() if commit is None else commit
    path = state_path(path)

    state, decision = _re_derive(path, payload, now)
    if not decision.sends:
        # The overwhelmingly common case on a green run. Nothing to write, no
        # git, no network, nothing to race with.
        return decision, True

    if decision.kind == "raise" and not decision.key:
        # The legacy shape: no cause key, so there is nothing to dedup and
        # nothing to record. Writing an unchanged ledger would be a commit that
        # says nothing, on the path where noise is the enemy.
        return decision, True

    if not commit:
        apply(state, decision, now)
        save(state, path)
        return decision, True

    rel = os.path.relpath(str(Path(path).resolve()), str(ROOT))
    for attempt in range(1, CLAIM_ATTEMPTS + 1):
        code, note = _git("fetch", "origin", "main")
        if code:
            print(f"::warning::could not fetch main to claim the alarm: {note}")
        else:
            _git("reset", "--hard", "origin/main")
        state, decision = _re_derive(path, payload, now)
        if not decision.sends:
            print(f"another run claimed this first: {decision.note}")
            return decision, True
        apply(state, decision, now)
        save(state, path)
        _git("add", rel)
        code, _ = _git("diff", "--staged", "--quiet")
        if code == 0:
            return decision, True
        _git("commit", "-q", "-m",
             f"alert: claim {decision.kind} {decision.key or decision.scope}")
        code, note = _git("push", "origin", "HEAD:main")
        if code == 0:
            return decision, True
        print(f"claim push rejected (attempt {attempt}), re-deriving: {note}")
        sleep(attempt * 3)

    print("::warning::the alarm could not be recorded in data/alert_state.json "
          "after five attempts, so this email is not deduped against a "
          "simultaneous one. It is being sent anyway. Silence is the worse "
          "failure, and five lost races with this cause still absent means five "
          "different alarms rather than a duplicate of this one.")
    return decision, False


#: The endpoint accepted these and the outbox still carries them, so the shape
#: is pinned here too. Lowercase only.
KEY_SAFE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,159}$")


def open_alarms(path: Path | str | None = None):
    """-> [(key, first_iso, last_iso, subject)], oldest first. For ops_status.

    This is what could never be read while the state lived in a WordPress
    option: a session can now see what is currently being SUPPRESSED.
    """
    entries = (load(state_path(path)).get("open") or {})
    rows = sorted(entries.items(), key=lambda kv: int(kv[1].get("first", 0)))
    return [(k, _stamp(int(v.get("first", 0))), _stamp(int(v.get("last", 0))),
             str(v.get("subject") or "")) for k, v in rows]


def main(argv=None) -> int:
    rows = open_alarms()
    if not rows:
        print("no alarm is open: nothing is being suppressed")
        return 0
    print(f"{len(rows)} alarm(s) open, so a repeat of each is being suppressed:")
    for key, first, last, subject in rows:
        print(f"  {key}\n    raised {first}, last seen {last}\n    {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

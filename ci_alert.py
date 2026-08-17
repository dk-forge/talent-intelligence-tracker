#!/usr/bin/env python3
"""A red CI run becomes an email, deduped by CAUSE rather than by run.

THE GAP THIS CLOSES
-------------------
Health has always been recorded here and rarely announced: a dead collector was
a badge on a page nobody opens, and a broken workflow was nothing at all. The
owner: "I don't get notified of workflow failures. I only see them when I check,
and I've been checking sporadically."

This repo has an expensive record of failures that were loud in CI and silent
everywhere a person would look. Thirteen writer runs were evicted on 2026-07-28/29
and reported as "queued". A `cp` over the database destroyed 9,572 signal rows
across five commits without a single red run. Eleven consecutive drain ticks
dispatched nothing and exited 0. The lesson each time was the same: a signal that
does not reach a person is not a signal.

WHY DEDUPE BY CAUSE IS THE WHOLE DESIGN, NOT A REFINEMENT
---------------------------------------------------------
The sibling repo had one assertion redden CI eight consecutive times in an
afternoon. Eight identical emails would train the owner to filter this sender,
and a filtered alert channel is the ORIGINAL problem wearing a new hat — the same
shape as a staleness alarm pinned permanently red by a ceiling its job could
never meet. **An alarm that cannot be cleared is an alarm nobody reads.**

So the numbers are normalised OUT of the failure message before it is
fingerprinted: a count that drifts while the same thing stays broken is ONE
cause and mails once, while a genuinely different assertion mails immediately.
The open/resolved state lives server-side in the `/alert` endpoint, next to the
mailer, because a "we already told them" record that can disagree with what was
actually sent is worth less than no record at all.

AND IT CLEARS. On the next green run of the same workflow+branch this posts a
resolve for that scope and the endpoint mails "RECOVERED" exactly once (and
nothing at all when nothing was open). A fixed alarm that never says so is one
the owner still has to go and check.

Usage (the workflow passes these from the `workflow_run` event payload):

    python3 ci_alert.py --run-id 123 --workflow collect --conclusion failure \\
        --branch main --event schedule --run-url https://github.com/...

WHAT HAPPENS WHEN THE HOST IS DOWN (added 2026-07-31, after it was)
-------------------------------------------------------------------
`/alert` is a route on the WordPress site. Bluehost answered 504 for everything
under /blog/ between 00:48 and 00:55 UTC, so enrich failed, drain-writers
correctly went red, and this alerter then failed FOUR times trying to report
them — because it was posting to the host it was reporting about. The alarm was
mute at exactly the moment it was needed, and the outage was found by the owner
in a browser instead.

Two things changed, and they are separate fixes to two separate defects:

1. DELIVERY IS NOW DURABLE. A POST that fails is retried inside the run
   (transient failures only) and, if it still fails, the alert is HELD in
   `data/alert_outbox.json` — committed, so it outlives the runner and the
   outage. `host-watch.yml` delivers it when the host answers again. See
   alert_outbox.py for why a committed file rather than a longer backoff.

2. A HELD ALERT IS NOT A FAILURE. This used to exit 1 whenever the POST failed,
   so one outage reddened four workflows and then reddened four ALERT runs on
   top — an outage manufacturing red runs which manufacture alerts which also
   fail. Worse, it told a session that the ALERTER was broken when the alerter
   was doing its job and the host was down. Holding an alert is a kept promise,
   so it exits 0. The only non-zero left is "could neither deliver NOR hold",
   which is the one state where the owner will never hear about the failure.

Exit codes: 0 = handled (mailed, suppressed, held for later, or nothing to do)
            1 = could neither deliver the alert NOR hold it. Nobody is going to
                be told about the original failure, so this run goes RED and
                ops_status.py surfaces it at the next session start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Bluehost's ModSecurity blocks python-requests outright; every request to the
# WP host must look like a real client. (Gotcha #1 in CLAUDE.md.)
USER_AGENT = "TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)"

# Conclusions worth an email. `cancelled` is deliberately absent, and here that
# is not a style choice: this repo CANCELS runs by design — the `talent-collect`
# lock evicts a pending run whenever a second one is dispatched past it. Mailing
# on cancellation would fire constantly and drown the failures that matter.
# Evicted writer runs have their own detection in ops_status.py [2b], which
# knows the difference between an eviction and a failure. This does not.
ALERTABLE = {"failure", "timed_out", "startup_failure"}

# ...BUT A JOB THAT KILLS ITSELF ON `timeout-minutes` ALSO REPORTS `cancelled`,
# and that is a different animal entirely. GitHub reserves the `timed_out`
# conclusion for a handful of cases; a step that simply runs past its job's own
# `timeout-minutes` ends the run `cancelled`, indistinguishable at the
# conclusion level from an evicted writer run. So the blanket "cancelled is
# noise" rule made a whole class of permanent failure silent in BOTH channels:
# ci_status.py only reports a cancelled run that created ZERO jobs (the eviction
# signature), and a self-timeout creates jobs, so it is not there either.
#
# The sibling repo paid for this. "Archive WARN sources to Wayback" (weekly,
# timeout-minutes: 20) was killed at ~20m on EVERY run it ever had, 2026-07-27
# and 2026-08-03, never once completing, and no email ever fired — while the
# archive re-check invariant drifted to 8.6 days against its 10-day bound.
#
# It matters here now because PR #32 gave collect, collect-press, deploy-plugin,
# retract and tests a `timeout-minutes` they never had. Those ceilings are
# generous precisely because hitting one was quiet, and a generous ceiling that
# reports nothing when it binds is a wall with no alarm on it.
#
# A self-timeout is never routine: nothing outside the job cancelled it, it ran
# into a wall this repository set. So `cancelled` is still not alertable BY
# CONCLUSION. It is alertable by EVIDENCE, and the evidence is NOT in the log: a
# self-killed job's log ends on a bare "##[error]The operation was canceled.",
# character-for-character what an evicted or externally cancelled job prints,
# and `--log-failed` returns nothing at all because a cancelled run has no
# failed STEP. The distinguishing line lives in the job's CHECK-RUN ANNOTATIONS:
#
#   failure  The job has exceeded the maximum execution time of 20m0s
#   failure  The operation was canceled.
#
# Only a self-timeout produces the first line, so that is what is matched, and
# everything else — every eviction — returns 0 and says why.
_SELF_TIMEOUT = re.compile(
    r"has exceeded the maximum (?:execution|operation) time of\s*(.+?)\.?$",
    re.IGNORECASE)

# Actions log lines arrive as "<job>\t<step>\t<ISO timestamp> <content>".
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

# The generic tail every failed step prints. True, and useless: "a job failed"
# is precisely the alert that gets ignored.
_GENERIC_ERROR = re.compile(r"^Process completed with exit code \d+\.?$")

_ANNOTATION = re.compile(r"^##\[error\](.*)$")
_PY_EXCEPTION = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Failure)\b.*$")
_PYTEST_DETAIL = re.compile(r"^E\s{2,}(\S.*)$")
_PYTEST_SUMMARY = re.compile(r"^FAILED\s+\S+::\S+.*$")
_UNITTEST_HEAD = re.compile(r"^(?:FAIL|ERROR):\s+\w+\s+\(.*\)\s*$")
_LOOSE_ERROR = re.compile(r"(?i)(?:^|\s)(?:error|fatal|failed)[: ]")

# THE PHP RENDER HARNESSES, which are half of what `tests` runs and which mailed
# NOTHING USEFUL until 2026-08-17. Every file in tests/php/ fails in one shape:
#
#   dashboard FAILED:
#     - the markup must stay inside 184,600 bytes and was 185,146 ...
#
# The header matched _LOOSE_ERROR and became the cause; the bullet under it
# matched no pattern here at all and was dropped. So run 32059349793 mailed
# "dashboard FAILED:" with a trailing colon and an empty tail. The measured
# value, the bound and the name of the thing that exceeded it were all in the
# log, and none of the three was in the email. A cause line carrying no number
# cannot be triaged from a phone, and being triaged from a phone is the only
# reason this module exists.
#
# The bullet is recognised ONLY inside a block the header opened. A build log is
# full of indented list items and not one of them is a diagnosis.
_HARNESS_HEAD = re.compile(r"^\S.*\bFAILED\b.*:$")
_HARNESS_BULLET = re.compile(r"^-\s+(\S.*)$")

# Applied IN ORDER to reduce a message to its cause. Anything that can change
# run-to-run while the underlying defect stays the same must die here, or the
# same broken thing mails twice.
_NORMALISE = (
    (re.compile(r"/home/runner/work/[^/\s]+/[^/\s]+/"), ""),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T[\d:.]+Z?\b"), "<TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<HEX>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    (re.compile(r"\d+(?:[.,]\d+)*"), "<N>"),
    (re.compile(r"\s+"), " "),
)


#: The EXACT shape `talent/v1/alert` accepts for `dedupe_key` and
#: `resolve_scope`, mirrored from `tit_api_alert()` in
#: wordpress-plugin/.../includes/api.php (`$safe = '/^[a-z0-9][a-z0-9:._-]{0,159}$/'`).
#: Lowercase only, and the endpoint answers a settled 400 for anything else.
#:
#: This lives here because a key the endpoint rejects is not a bad email, it is
#: NO email. `ci_noise_report.py` composed its week with `%G-W%V`, minted
#: `ci-noise:2026-W32`, took a 400 sixteen times, went `stuck` in the outbox,
#: and host-watch then failed EVERY tick from 2026-08-03T21:55Z on "alerts are
#: stuck with the host up". A permanently red watchdog cannot report an outage.
#: Any caller composing a key by hand rather than through `slug()` must assert
#: against this.
#:
#: ONE DEFINITION, and it is the queue's. A second copy of this pattern is a
#: second thing to keep in step with the PHP, and the failure mode of a copy
#: that drifts is exactly the one above: a key that passes here and 400s there.
#: `alert_outbox.enqueue_envelope` is what ENFORCES it, because the queue is
#: what must never accept a key it can do nothing with but retry forever.
from alert_outbox import KEY_SAFE  # noqa: E402,F401  stdlib-only, no cycle

#: The module itself, for `deliverable_key` on the notice path below. Same
#: import, same no-cycle guarantee; named separately so the `#:` above stays
#: attached to the constant it documents.
import alert_outbox  # noqa: E402


def slug(text: str, limit: int = 48) -> str:
    """A stable, key-safe scope fragment. Two different workflows must never
    collide here — a collision would silence a real, separate breakage."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit] or "unknown"


def strip_prefix(line: str) -> str:
    """Drop the job/step/timestamp columns `gh run view --log` prepends."""
    parts = line.split("\t")
    if len(parts) >= 3:
        line = parts[-1]
    return _TS.sub("", line).rstrip()


def normalise(message: str) -> str:
    """Reduce a failure message to its CAUSE — what stays the same while a
    still-broken thing keeps failing with different numbers."""
    out = (message or "").strip()
    for pattern, replacement in _NORMALISE:
        out = pattern.sub(replacement, out)
    return out.strip()


def extract_cause(raw_log: str) -> tuple[str, list[str]]:
    """Pull the actual failing assertion out of a failed run's log.

    Returns (cause, context). `cause` is the single most specific line, and it
    is what gets fingerprinted and what leads the subject line — because a
    message that carries its own diagnosis is the entire reason the email is
    worth opening, and "a job failed" is not one.
    """
    lines = [strip_prefix(ln) for ln in (raw_log or "").splitlines()]

    # Everything after the first generic "Process completed with exit code N" is
    # runner teardown (git config, orphan cleanup) and never diagnostic.
    cut = len(lines)
    for i, ln in enumerate(lines):
        body = _ANNOTATION.sub(r"\1", ln).strip()
        if _GENERIC_ERROR.match(body):
            cut = i
            break
    body_lines = [ln for ln in lines[:cut] if ln.strip()]

    annotations: list[str] = []
    exceptions: list[str] = []
    pytest_detail: list[str] = []
    test_heads: list[str] = []
    harness_heads: list[str] = []
    harness_detail: list[str] = []
    loose: list[str] = []
    in_harness = False
    for ln in body_lines:
        stripped = ln.strip()
        # pytest's own summary line starts with FAILED too, and a parametrised
        # id can end in a colon. It has a bucket of its own; never steal it.
        if _HARNESS_HEAD.match(stripped) and not _PYTEST_SUMMARY.match(stripped):
            harness_heads.append(stripped)
            in_harness = True
            continue
        if in_harness:
            bullet = _HARNESS_BULLET.match(stripped)
            if bullet:
                harness_detail.append(bullet.group(1).strip())
                continue
            # The block ends at the first line that is not one of its bullets.
            in_harness = False
        m = _ANNOTATION.match(stripped)
        if m and m.group(1).strip() and not _GENERIC_ERROR.match(m.group(1).strip()):
            annotations.append(m.group(1).strip())
            continue
        detail = _PYTEST_DETAIL.match(stripped)
        if detail:
            pytest_detail.append(detail.group(1).strip())
            continue
        if _PYTEST_SUMMARY.match(stripped) or _UNITTEST_HEAD.match(stripped):
            test_heads.append(stripped)
            continue
        if _PY_EXCEPTION.match(stripped):
            exceptions.append(stripped)
            continue
        if _LOOSE_ERROR.search(stripped):
            loose.append(stripped)

    # Most specific wins, and WHICH END of a bucket is the specific one differs
    # by bucket. A traceback's LAST exception line is the one that actually
    # stopped the run; earlier ones are usually chained or captured. A php
    # harness prints its bullets in the order the assertions failed and keeps
    # going, so the FIRST bullet is the thing that went wrong and the rest are
    # usually knock-on from it.
    for bucket, lead_with_first in ((exceptions, False), (pytest_detail, False),
                                    (harness_detail, True), (annotations, False),
                                    (test_heads, False), (harness_heads, False),
                                    (loose, False)):
        if bucket:
            cause = bucket[0] if lead_with_first else bucket[-1]
            break
    else:
        # No recognisable error shape. The last real output line still beats
        # "a job failed", and saying so honestly beats inventing a diagnosis.
        cause = body_lines[-1].strip() if body_lines else ""

    # The header names which harness broke, which the bullet on its own does
    # not, so it leads the context whenever there is one.
    context: list[str] = []
    for bucket in (harness_heads, test_heads, exceptions, pytest_detail,
                   harness_detail, annotations):
        for ln in bucket[-3:]:
            if ln != cause and ln not in context:
                context.append(ln)
    return cause[:400], context[:5]


def fetch_failed_log(repo: str, run_id: str) -> str:
    """`gh run view --log-failed` — only the failed steps, so it stays small.

    Returns "" (never raises) when gh is missing, unauthenticated, or the logs
    have expired. A missing log must degrade the email's DETAIL, never suppress
    the email: knowing a workflow is red is already more than the owner had.
    """
    try:
        proc = subprocess.run(
            ["gh", "run", "view", str(run_id), "-R", repo, "--log-failed"],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"could not read the failed log ({exc}), alerting without the cause line")
        return ""
    if proc.returncode != 0:
        print(f"gh run view exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout or ""


def fetch_annotations(repo: str, run_id: str) -> str:
    """Every job annotation for a run, one message per line. "" on any problem.

    Two `gh api` calls, and neither may raise: this runs on the failure path,
    and a notifier that dies while handling a failure has told nobody anything.
    Same contract as `fetch_failed_log` — a missing annotation must degrade the
    verdict to "routine cancellation", never crash the alerter.
    """
    def _api(path: str, jq: str) -> str:
        try:
            proc = subprocess.run(["gh", "api", path, "-q", jq],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"could not read {path} ({exc})")
            return ""
        if proc.returncode != 0:
            print(f"gh api {path} exited {proc.returncode}: "
                  f"{proc.stderr.strip()[:200]}")
        return proc.stdout or ""

    jobs = _api(f"repos/{repo}/actions/runs/{run_id}/jobs", ".jobs[].id")
    return "\n".join(_api(f"repos/{repo}/check-runs/{job_id}/annotations",
                          ".[].message")
                     for job_id in jobs.split())


def self_timeout_cause(text: str) -> str | None:
    """-> the runner's own timeout line, or None if this run was cancelled by
    something OUTSIDE itself — an eviction from the `talent-collect` lock, a
    superseded push, a human.

    Returning None is the common case here and it MUST stay silent. This repo
    evicts runs by design, ops_status.py [2b] is what tells an eviction from a
    failure, and mailing on every cancellation is the alarm fatigue the whole
    module exists to prevent — not a bar it may trade away for this class.
    """
    for raw in (text or "").splitlines():
        found = _SELF_TIMEOUT.search(strip_prefix(raw))
        if found:
            return ("the job cancelled ITSELF on timeout-minutes: it exceeded "
                    f"the maximum execution time of {found.group(1).strip()}")
    return None


def build_alert(*, repo: str, workflow: str, branch: str, event: str,
                run_url: str, cause: str, context: list[str],
                label: str = "CI RED") -> tuple[str, str, str]:
    """Compose the email and the cause key it is deduped on.

    `label` names the CLASS of red in the subject. The scope is deliberately
    unchanged across classes: a self-timeout and a failed assertion in the same
    workflow both clear on that workflow's next green run, so the resolve path
    needs no new vocabulary. The cause fingerprint is what keeps them distinct
    emails.
    """
    scope = f"{slug(workflow)}:{slug(branch, 32)}"
    fingerprint = hashlib.md5(
        f"{scope}\n{normalise(cause)}".encode("utf-8")).hexdigest()[:16]
    dedupe_key = f"{scope}:{fingerprint}"

    headline = cause or "no error line could be extracted from the log"
    subject = f"{label}: {workflow}: {headline}"[:180]

    lines = [
        f"The workflow '{workflow}' failed on GitHub Actions and nothing else would "
        "have told you.\n",
        f"  repo:     {repo}",
        f"  workflow: {workflow}",
        f"  branch:   {branch}",
        f"  trigger:  {event}",
        f"  run:      {run_url}",
        "",
        "WHAT FAILED:",
        f"  {headline}",
    ]
    if context:
        lines.append("")
        lines.append("Context from the failed step:")
        lines.extend(f"  {c}" for c in context)
    if not cause:
        lines.append("")
        lines.append(
            "No assertion or error line could be read out of this run's log (the log may "
            "have expired, or the job died before producing one). Open the run URL. This "
            "email is telling you the truth it has, not guessing at one.")
    lines.append(
        "\nWhat to do: open a Claude Code session in the talent-intelligence-tracker repo "
        "and paste this line:\n"
        f'  "The GitHub Actions workflow \'{workflow}\' is failing on {branch} with: '
        f'{headline}. The run is {run_url}. Reproduce it locally with '
        '`.venv/bin/pytest -q`, find the root cause, and fix it."\n')
    lines.append(
        "You will get ONE more email about this workflow: a RECOVERED notice on its next "
        "green run. We suppress repeats of this same failure deliberately. An alarm "
        "that mails eight times in an afternoon is one you learn to filter, and a filtered "
        "alarm is how a wrong number stays live for hours.")
    return subject, "\n".join(lines), dedupe_key


#: A shared host under load answers 5xx, and a proxy in front of a dead origin
#: answers 502/503/504. Those are worth asking again about in a few seconds.
#: 401/403 (wrong key) and 404 (route not deployed) are not: retrying a settled
#: "no" only makes the run longer, and both are held for a human either way.
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

#: Seconds between in-run retries. Three attempts over ~20 seconds catches the
#: single bad response and the brief wobble, which is most of what a shared host
#: produces. It deliberately does NOT try to outlast an outage: tonight's lasted
#: seven minutes and a job has ten, so anything longer is a race the runner
#: cannot win. Outlasting is the outbox's job, not this loop's.
_BACKOFF = (3, 12)


def _post_once(site: str, key: str, payload: dict) -> tuple[bool, str, bool]:
    """One POST. Returns (ok, description, transient)."""
    req = urllib.request.Request(
        f"{site.rstrip('/')}/wp-json/talent/v1/alert",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Talent-API-Key": key,
                 "User-Agent": USER_AGENT},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, ("the site has no /alert route yet: the plugin carrying it "
                           "has not been deployed (deploy-plugin.yml is manual here)"), False
        detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        return False, f"HTTP {exc.code} from /alert: {detail}", exc.code in _TRANSIENT_STATUS
    except urllib.error.URLError as exc:
        # DNS, TCP, TLS, timeout: the host is not answering at all. Always
        # transient — a name that does not resolve now may resolve in a minute,
        # and there is nothing here for a human to fix in this repo.
        return False, f"could not reach /alert: {exc.reason}", True
    except Exception as exc:  # noqa: BLE001 — a notifier must not raise
        return False, f"could not reach /alert: {exc}", True
    if body.get("sent"):
        return True, "emailed the owner", False
    return True, f"not emailed: {body.get('reason', 'the endpoint reported no send')}", False


def post_alert(site: str, key: str, payload: dict,
               *, sleep=time.sleep) -> tuple[bool, str, bool]:
    """POST to the plugin's keyed /alert, retrying transient failures.

    Returns (ok, description, transient) — `transient` is what tells the caller
    whether this looked like a host outage (hold it, say so quietly, do not go
    red) or a settled refusal like a bad key (hold it, and be loud about it).

    urllib rather than requests on purpose: this runs before any `pip install`,
    so the alerting path cannot be broken by a dependency resolution failure —
    which would be a notifier that dies exactly when the repo is unhealthy.
    """
    ok, note, transient = _post_once(site, key, payload)
    for delay in _BACKOFF:
        if ok or not transient:
            break
        print(f"  /alert did not answer ({note}), retrying in {delay}s")
        sleep(delay)
        ok, note, transient = _post_once(site, key, payload)
    return ok, note, transient


def write_envelope(path: str, *, key: str, kind: str, scope: str,
                   payload: dict, reason: str, run_url: str) -> bool:
    """Park an undeliverable alert where the workflow can commit it.

    Writing the envelope and folding it into `data/alert_outbox.json` are two
    steps because the commit has to survive a racing push, and the answer to a
    rejected push in this repo is always to reset onto main and RE-DERIVE rather
    than to replay a diff. The workflow loops fetch -> reset -> `alert_outbox.py
    enqueue --envelope` -> commit -> push, and `enqueue` is idempotent in `key`.
    """
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "kind": kind, "scope": scope,
                       "payload": payload, "reason": reason,
                       "run_url": run_url}, fh, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"::error::could not write the alert envelope to {path}: {exc}")
        return False
    return True


def hold(*, envelope: str, key: str, kind: str, scope: str, payload: dict,
         note: str, transient: bool, run_url: str) -> int:
    """The undeliverable path, in one place so both kinds behave identically.

    Returns the process exit code. It is 0 when the alert is safely held — and
    that is the fix for the amplification loop, not an oversight. See the module
    docstring: exiting 1 here is what turned one seven-minute outage into four
    extra red runs and a false "the alerter is broken" reading in ops_status.
    """
    if not envelope:
        print("::error::the alert could not be delivered and there is nowhere to "
              "hold it (no ALERT_ENVELOPE path was given), so nobody will be told "
              f"about this failure at all. Delivery said: {note}")
        return 1

    if not write_envelope(envelope, key=key, kind=kind, scope=scope,
                          payload=payload, reason=note, run_url=run_url):
        print("::error::the alert could not be delivered AND could not be held. "
              f"Nobody will be told about this failure. Delivery said: {note}")
        return 1

    # Loud, but not red. The distinction is the point: a session reading this
    # log must be able to tell "the host was down and we kept the alert" from
    # "the alerter is broken", because the first needs nothing from anybody.
    if transient:
        print(f"::warning::/alert is unreachable ({note}). The alert is HELD in "
              "data/alert_outbox.json and will be delivered by the next host-watch "
              "run that finds the host answering. This run is NOT failing: an "
              "outage must not manufacture red runs on top of the ones it caused.")
    else:
        print(f"::error::/alert refused this alert and it is not a transient "
              f"failure: {note}. It is HELD in data/alert_outbox.json, but a "
              "settled refusal will not fix itself — check WP_API_KEY and that "
              "the plugin carrying /alert is deployed. ops_status.py escalates "
              "a held alert that keeps failing.")
    return 0


def notice(*, subject: str, body: str, dedupe_key: str, run_url: str,
           envelope: str, dry_run: bool) -> int:
    """Mail one thing that is TRUE but not a failure, on the same rails.

    The tripwire's budget stop is the case this exists for. Once the ceiling
    binding stopped being a red run (see spend.py `--gate`), the run-completed
    alert path stopped firing for it — and "the owner separately needs to know
    spend is AT the cap" is a real requirement, not a side effect of the red. So
    the signal survives, as ONE email, through the same endpoint, the same
    server-side dedupe and the same held-not-lost outbox as every other alert.

    Deduped on the caller's key rather than on an extracted log line, because
    the caller knows what one event is. `spend-ceiling:2026-08` is one email per
    allowance month however many runs meet the closed gate.
    """
    key = alert_outbox.deliverable_key(dedupe_key)
    site = os.environ.get("WP_SITE_URL", "").rstrip("/")
    api_key = os.environ.get("WP_API_KEY", "")
    payload = {"subject": subject, "body": body, "dedupe_key": key}

    print(f"dedupe_key: {key}")
    if dry_run:
        print("--- subject ---")
        print(subject)
        print("--- body ---")
        print(body)
        return 0
    if not (site and api_key):
        print("::error::WP_SITE_URL / WP_API_KEY are not set. The notice was NOT sent.")
        return 1

    ok, note, transient = post_alert(site, api_key, payload)
    print(f"notice {key}: {note}")
    if not ok:
        return hold(envelope=envelope, key=key, kind="alert", scope=key,
                    payload=payload, note=note, transient=transient,
                    run_url=run_url)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CI failure -> the owner's inbox")
    ap.add_argument("--run-id")
    ap.add_argument("--workflow")
    ap.add_argument("--conclusion")
    # The notice path: not a run conclusion at all, so it takes none of the
    # three above. Kept in this file rather than a new one because it must use
    # THIS module's post/retry/hold, and a second copy of that logic is a second
    # thing to forget to fix.
    ap.add_argument("--notice-key",
                    help="send one deduped non-failure alert with this key")
    ap.add_argument("--notice-subject", default="")
    ap.add_argument("--notice-body", default="")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--event", default="unknown")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--repo", default=os.environ.get(
        "GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the alert instead of posting it")
    ap.add_argument("--envelope", default=os.environ.get("ALERT_ENVELOPE", ""),
                    help="where to park an undeliverable alert for the workflow "
                         "to commit into data/alert_outbox.json")
    args = ap.parse_args(argv)

    if args.notice_key:
        return notice(subject=args.notice_subject, body=args.notice_body,
                      dedupe_key=args.notice_key, run_url=args.run_url,
                      envelope=args.envelope, dry_run=args.dry_run)

    missing = [name for name, value in (("--run-id", args.run_id),
                                        ("--workflow", args.workflow),
                                        ("--conclusion", args.conclusion))
               if not value]
    if missing:
        ap.error(f"{', '.join(missing)} are required unless --notice-key is given")

    conclusion = (args.conclusion or "").lower()
    scope = f"{slug(args.workflow)}:{slug(args.branch, 32)}"

    site = os.environ.get("WP_SITE_URL", "").rstrip("/")
    key = os.environ.get("WP_API_KEY", "")

    if conclusion == "success":
        # A green drain-writers tick is NOT proof the queue's problems are
        # fixed. Since writer_queue.select_red (2026-08-02), a needs-human item
        # reddens ONE tick and the next tick is deliberately green with the
        # item still waiting — so resolving this scope here would mail
        # "RECOVERED ... Nothing to do" about a failure a human has not
        # touched. The queue file is the authority: while it still reports
        # problems, a green tick resolves nothing and mails nothing. Every
        # other workflow's green really does mean the failure stopped.
        if slug(args.workflow) == "drain-writers":
            try:
                import writer_queue
                open_problems = writer_queue.summary(writer_queue.load())["problems"]
            except Exception as exc:  # a broken import must not eat real recoveries
                print(f"could not read the writer queue ({exc}), "
                      "treating the green run as a real recovery")
                open_problems = []
            if open_problems:
                print(f"skip resolve for {scope}: the tick is green because its "
                      f"items were already reported once (red-once), but "
                      f"{len(open_problems)} item(s) still wait on a human. "
                      "No RECOVERED mail until the queue itself is clear.")
                return 0
        # Recovery. The endpoint mails exactly once IF something was open for
        # this scope and stays silent otherwise, so this is cheap to post on
        # every green run and cannot itself become noise.
        payload = {"resolve_scope": scope,
                   "subject": f"RECOVERED: {args.workflow} is green again",
                   "body": (f"'{args.workflow}' on {args.branch} passed again.\n\n"
                            f"  run: {args.run_url}\n\n"
                            "Whatever was failing is no longer failing. Nothing to do.")}
        if args.dry_run or not (site and key):
            print(f"[dry-run] resolve scope={scope}")
            return 0
        ok, note, transient = post_alert(site, key, payload)
        print(f"resolve {scope}: {note}")
        if not ok:
            # Held like any other alert. Holding a RESOLVE is what lets the
            # outbox cancel a RED for the same scope that never went out: if
            # both were raised during one outage, the owner hears about
            # neither, because neither was ever true by the time anyone could
            # have read it. See alert_outbox.enqueue.
            return hold(envelope=args.envelope, key=f"resolve:{scope}",
                        kind="resolve", scope=scope, payload=payload,
                        note=note, transient=transient, run_url=args.run_url)
        return 0

    label = "CI RED"
    if conclusion == "cancelled":
        # See _SELF_TIMEOUT. A cancelled run stays silent UNLESS it killed
        # itself, in which case nothing outside the job cancelled it: it ran
        # past a wall this repository set, and on a schedule that is permanent
        # and was, until now, invisible in both channels.
        timeout_cause = self_timeout_cause(
            fetch_annotations(args.repo, args.run_id))
        if not timeout_cause:
            print("cancelled by something outside the job (an eviction from the "
                  "talent-collect lock, a superseded push, or a human): "
                  "deliberately not alertable. ops_status.py [2b] is what tells "
                  "an eviction from a failure")
            return 0
        label = "CI SELF-TIMEOUT"
        cause, context = timeout_cause, [
            "The job was not evicted, superseded or cancelled by a human. It ran "
            "past its own `timeout-minutes` and the runner killed it.",
            "GitHub reports this as `cancelled`, not `timed_out`, which is why it "
            "produced no email and no ci_status.py line before now.",
            "Raise the ceiling with the measured reason written down, or make the "
            "job fit inside it. Do not simply retry.",
        ]
    elif conclusion not in ALERTABLE:
        print(f"conclusion '{conclusion}' is not alertable, nothing to do")
        return 0
    else:
        cause, context = extract_cause(fetch_failed_log(args.repo, args.run_id))

    subject, body, dedupe_key = build_alert(
        repo=args.repo, workflow=args.workflow, branch=args.branch, event=args.event,
        run_url=args.run_url, cause=cause, context=context, label=label)

    print(f"cause:      {cause or '(none extracted)'}")
    print(f"normalised: {normalise(cause)}")
    print(f"dedupe_key: {dedupe_key}")

    if args.dry_run:
        print("--- subject ---")
        print(subject)
        print("--- body ---")
        print(body)
        return 0

    if not (site and key):
        # Loud, and non-zero. A silent "no credentials so I did nothing" is the
        # same class of lie as a green drain tick that dispatched nothing.
        print("::error::WP_SITE_URL / WP_API_KEY are not set. The CI alert was NOT sent.")
        return 1

    payload = {"subject": subject, "body": body, "dedupe_key": dedupe_key}
    ok, note, transient = post_alert(site, key, payload)
    print(f"alert {dedupe_key}: {note}")
    if not ok:
        return hold(envelope=args.envelope, key=dedupe_key, kind="alert",
                    scope=scope, payload=payload, note=note,
                    transient=transient, run_url=args.run_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())

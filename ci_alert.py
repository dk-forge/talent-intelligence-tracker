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

Exit codes: 0 = handled (mailed, suppressed, or nothing to do)
            1 = the alert POST itself failed. The run goes RED so the failure of
                the alerter is itself visible — including in ops_status.py [2f].
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
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
    loose: list[str] = []
    for ln in body_lines:
        stripped = ln.strip()
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

    # Most specific wins. A traceback's LAST exception line is the one that
    # actually stopped the run; earlier ones are usually chained or captured.
    for bucket in (exceptions, pytest_detail, annotations, test_heads, loose):
        if bucket:
            cause = bucket[-1]
            break
    else:
        # No recognisable error shape. The last real output line still beats
        # "a job failed", and saying so honestly beats inventing a diagnosis.
        cause = body_lines[-1].strip() if body_lines else ""

    context: list[str] = []
    for bucket in (test_heads, exceptions, pytest_detail, annotations):
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
        print(f"could not read the failed log ({exc}) — alerting without the cause line")
        return ""
    if proc.returncode != 0:
        print(f"gh run view exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout or ""


def build_alert(*, repo: str, workflow: str, branch: str, event: str,
                run_url: str, cause: str, context: list[str]) -> tuple[str, str, str]:
    """Compose the email and the cause key it is deduped on."""
    scope = f"{slug(workflow)}:{slug(branch, 32)}"
    fingerprint = hashlib.md5(
        f"{scope}\n{normalise(cause)}".encode("utf-8")).hexdigest()[:16]
    dedupe_key = f"{scope}:{fingerprint}"

    headline = cause or "no error line could be extracted from the log"
    subject = f"CI RED: {workflow} — {headline}"[:180]

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
            "have expired, or the job died before producing one). Open the run URL — this "
            "email is telling you the truth it has, not guessing at one.")
    lines.append(
        "\nWhat to do: open a Claude Code session in the talent-intelligence-tracker repo "
        "and paste this line:\n"
        f'  "The GitHub Actions workflow \'{workflow}\' is failing on {branch} with: '
        f'{headline}. The run is {run_url}. Reproduce it locally with '
        '`.venv/bin/pytest -q`, find the root cause, and fix it."\n')
    lines.append(
        "You will get ONE more email about this workflow: a RECOVERED notice on its next "
        "green run. Repeats of this same failure are suppressed deliberately — an alarm "
        "that mails eight times in an afternoon is one you learn to filter, and a filtered "
        "alarm is how a wrong number stays live for hours.")
    return subject, "\n".join(lines), dedupe_key


def post_alert(site: str, key: str, payload: dict) -> tuple[bool, str]:
    """POST to the plugin's keyed /alert. Returns (ok, description).

    urllib rather than requests on purpose: this runs before any `pip install`,
    so the alerting path cannot be broken by a dependency resolution failure —
    which would be a notifier that dies exactly when the repo is unhealthy.
    """
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
            return False, ("the site has no /alert route yet — the plugin carrying it "
                           "has not been deployed (deploy-plugin.yml is manual here)")
        detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        return False, f"HTTP {exc.code} from /alert: {detail}"
    except Exception as exc:
        return False, f"could not reach /alert: {exc}"
    if body.get("sent"):
        return True, "emailed the owner"
    return True, f"not emailed: {body.get('reason', 'the endpoint reported no send')}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CI failure -> the owner's inbox")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--conclusion", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--event", default="unknown")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--repo", default=os.environ.get(
        "GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker"))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the alert instead of posting it")
    args = ap.parse_args(argv)

    conclusion = (args.conclusion or "").lower()
    scope = f"{slug(args.workflow)}:{slug(args.branch, 32)}"

    site = os.environ.get("WP_SITE_URL", "").rstrip("/")
    key = os.environ.get("WP_API_KEY", "")

    if conclusion == "success":
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
        ok, note = post_alert(site, key, payload)
        print(f"resolve {scope}: {note}")
        if not ok:
            print(f"::error::CI recovery notice could not be delivered — {note}")
            return 1
        return 0

    if conclusion not in ALERTABLE:
        print(f"conclusion '{conclusion}' is not alertable — nothing to do")
        return 0

    cause, context = extract_cause(fetch_failed_log(args.repo, args.run_id))
    subject, body, dedupe_key = build_alert(
        repo=args.repo, workflow=args.workflow, branch=args.branch, event=args.event,
        run_url=args.run_url, cause=cause, context=context)

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
        print("::error::WP_SITE_URL / WP_API_KEY are not set — the CI alert was NOT sent.")
        return 1

    ok, note = post_alert(site, key, {"subject": subject, "body": body,
                                      "dedupe_key": dedupe_key})
    print(f"alert {dedupe_key}: {note}")
    if not ok:
        # This run going red is the point: it is a separate workflow from the one
        # that failed, so it can never mask the original failure, and
        # ops_status.py [2f] surfaces the alerter's own breakage at session start.
        print(f"::error::CI alert could not be delivered — {note}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

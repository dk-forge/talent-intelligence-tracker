#!/usr/bin/env python3
"""The gate and the guard for the DRAFT-ONLY self-healer (self-heal.yml).

WHAT THE HEALER IS, AND POINTEDLY IS NOT
----------------------------------------
When a workflow goes red on MAIN with a NEW, code-shaped failure, the
self-heal workflow asks Claude (via the pinned anthropics/claude-code-action)
to reproduce it from the failing run's log, diagnose it, and open a **draft
pull request** with a test-verified fix. A second, adversarial pass reviews
the draft and posts its findings as a PR comment. A human merges — always.
The healer never merges, never pushes to main, never dispatches or re-runs a
workflow (so it can never evict a queued writer from the talent-collect
lock), never touches `data/` (the repo IS the memory — the database, the
writer queue, the outbox, the landmarks all live there), and never edits
spend or guardrail constants (FORBIDDEN below, enforced twice: named in the
prompt, and re-checked by `check` on the branch's real diff).

THIS MODULE IS THE PART THAT SAYS NO
------------------------------------
Most red runs here are alarms working as designed, with an owner already:

  * anything not conclusion `failure` — evictions and self-timeouts arrive as
    `cancelled`; ci_alert.py already tells them apart and mails the one that
    matters, and ops_status [2b] owns evictions.
  * a red on any branch but main — that branch has an author (dependabot or
    a session), and the fix belongs on the branch, not in a healer draft.
  * `drain-writers` — its red-once design means a red IS the deliberate
    "an orphaned run needs a human decision" signal. Never guess the inputs
    of a lost run; that rule is exactly why no machine may answer this red.
  * `Rendered contrast audit` — it measures the LIVE site, and between
    merging a CSS fix and the operator's manual deploy its red is CORRECT.
    A healer cannot verify a fix against prod and must never run the deploy.
  * `landmarks` / `recall` — they measure the corpus and the public endpoint;
    a regression there is a data event for a human, not a code patch.
  * `host-watch` and `CI failure alert` — the alarm channel itself.
  * host-outage-shaped causes (5xx / unreachable): nothing in the checkout
    is wrong; host-watch owns the outage.
  * budget-shaped causes (CreditsExhausted / 402 / PaidReadsOff): the spend
    ceiling working. A budget stop is UNDECIDED, never a defect.

The cause line comes from `ci_alert.extract_cause` on the same `--log-failed`
text the email quotes — one classification, reused, so a failure cannot be
healed and mailed as needs-a-human at the same time.

BUDGET IS STRUCTURAL: one healer at a time (concurrency group), one open PR
per cause fingerprint (branch name = fingerprint, so `gh pr list` is the
ledger), and a hard ceiling on simultaneous open healer PRs.

NOTE ON SPEND: the healer runs on the operator's Claude subscription token
(CLAUDE_CODE_OAUTH_TOKEN), not on OPENROUTER_API_KEY, so it does not draw on
either tracker pot in spend.py/budget.py.

Exit codes: `gate` always exits 0 — a decision not to heal is a decision.
`check` exits 1 when the branch touched a forbidden path.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_alert  # noqa: E402  the ONE cause extraction, reused not re-derived

#: Workflow display names (lowercased) the healer must never touch, each for a
#: reason written in the module docstring.
NEVER_HEAL = {
    "ci failure alert",
    "host-watch",
    "self-heal",
    "drain-writers",
    "rendered contrast audit",
    "landmarks",
    "recall",
}

#: A failure whose cause line looks like the HOST failing, not the code.
_HOST_OUTAGE = re.compile(
    r"HTTP 5\d\d\b"
    r"|returned error: 5\d\d\b"
    r"|could not reach"
    r"|Connection (?:refused|reset|timed out)"
    r"|Gateway Time-?out"
    r"|Service Temporarily Unavailable",
    re.IGNORECASE)

#: A failure whose cause line is the BUDGET working. Hitting a ceiling
#: degrades by design; a rare hard stop (tripwire's --enforce) is still the
#: ceiling doing its job, and the fix is the owner's allowance decision.
_BUDGET_STOP = re.compile(
    r"CreditsExhausted|PaidReadsOff|HTTP 402\b|spend ceiling|allowance",
    re.IGNORECASE)

#: A failure whose cause is a GUARDRAIL FINDING waiting on a human. Run
#: 31734617121: collect went red with "[guardrail] 1 finding(s) are past
#: their grace window" — the guardrail working exactly as designed, publishing
#: the clean rows and refusing to stay quiet about the flagged ones.
#: Adjudication is the owner's, always (standing rule: arming, spending and
#: adjudication stay gated), so no code "fix" is ever the answer here.
_ADJUDICATION = re.compile(
    r"\[guardrail\]|past (?:their|its) grace window|needs adjudication",
    re.IGNORECASE)

#: Ceiling on simultaneously open healer PRs.
MAX_OPEN_PRS = 3

BRANCH_PREFIX = "self-heal/"

#: Paths the healer's branch may never change. `data/` is the widest entry on
#: purpose: the committed database, the writer queue, the alert outbox, the
#: landmarks and every ledger live there, and a healer that edits state is a
#: healer that erases evidence. spend/budget/guardrails are the constants a
#: "fix" must never widen; the locks are the supply chain; HANDOVER is the
#: session log; the healer may not edit itself.
FORBIDDEN = (
    "data/",
    "spend.py",
    "budget.py",
    "guardrails.py",
    "requirements.lock",
    "requirements-dev.lock",
    "docs/HANDOVER.md",
    ".github/workflows/self-heal.yml",
)


def fingerprint(workflow: str, cause: str) -> str:
    """A stable id for one cause of one workflow's red. Numbers normalised
    out exactly as ci_alert's dedupe key does, so a drifting count is still
    one fingerprint and one draft."""
    text = f"{ci_alert.slug(workflow)}\n{ci_alert.normalise(cause or '')}"
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def branch_name(workflow: str, cause: str) -> str:
    return f"{BRANCH_PREFIX}{ci_alert.slug(workflow, 32)}-{fingerprint(workflow, cause)}"


def classify(workflow: str, conclusion: str, cause: str,
             branch: str = "main") -> tuple[bool, str]:
    """-> (heal, reason). The reason ships as a step output, so a skipped run
    says exactly which known-expected class it skipped as."""
    if branch and branch != "main":
        return False, (f"the failure is on branch '{branch}', not main. A "
                       "branch red has an author (a session or dependabot) "
                       "and the fix belongs there — the healer only heals "
                       "unattended breakage on main.")
    if (conclusion or "").lower() != "failure":
        return False, (f"conclusion '{conclusion}' is not healable: only a "
                       "plain failure is. Evictions and self-timeouts arrive "
                       "as 'cancelled' and ci_alert/ops_status already own "
                       "them; success needs nothing.")
    if (workflow or "").strip().lower() in NEVER_HEAL:
        return False, (f"'{workflow}' is never healed: it is the alarm "
                       "channel, a live-site measurement, or a red that IS "
                       "the designed needs-a-human signal (see self_heal.py "
                       "NEVER_HEAL).")
    if cause and _ADJUDICATION.search(cause):
        return False, ("the cause line is a guardrail finding awaiting "
                       "ADJUDICATION, which is the owner's and only the "
                       "owner's. The guardrail is working; a code 'fix' "
                       "could only silence it.")
    if cause and _BUDGET_STOP.search(cause):
        return False, ("the cause line is budget-shaped: the spend ceiling "
                       "working as designed. The fix is the owner's "
                       "allowance decision, never code.")
    if cause and _HOST_OUTAGE.search(cause):
        return False, ("the cause line is host-outage-shaped (5xx / "
                       "unreachable): nothing in this checkout is wrong. "
                       "host-watch owns outages.")
    return True, "a code-shaped failure on main with no standing owner: healable."


# --------------------------------------------------------------------------
# gh plumbing — never raises: the gate runs on the failure path.
# --------------------------------------------------------------------------

def _gh(args_list: list[str], timeout: int = 60) -> str | None:
    try:
        proc = subprocess.run(["gh"] + args_list, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"gh {' '.join(args_list[:2])} failed ({exc})")
        return None
    if proc.returncode != 0:
        print(f"gh {' '.join(args_list[:2])} exited {proc.returncode}: "
              f"{proc.stderr.strip()[:200]}")
        return None
    return proc.stdout


def run_metadata(repo: str, run_id: str):
    """(workflow, conclusion, branch, url) for a run id — the
    workflow_dispatch path, which is how the gate is verified against any
    PAST run without waiting for a fresh failure."""
    out = _gh(["api", f"repos/{repo}/actions/runs/{run_id}",
               "-q", "[.name, .conclusion, .head_branch, .html_url] | @tsv"])
    if not out:
        return None, None, None, None
    parts = out.strip().split("\t")
    return tuple(parts + [None] * (4 - len(parts)))[:4]


def open_healer_prs(repo: str) -> list[dict]:
    out = _gh(["pr", "list", "-R", repo, "--state", "open",
               "--json", "number,headRefName"])
    if not out:
        return []
    try:
        prs = json.loads(out)
    except ValueError:
        return []
    return [p for p in prs
            if (p.get("headRefName") or "").startswith(BRANCH_PREFIX)]


# --------------------------------------------------------------------------
# The forbidden-path guard. A prompt is a request; this is the check.
# --------------------------------------------------------------------------

def violations(changed_paths: list[str]) -> list[str]:
    bad = []
    for path in changed_paths:
        path = path.strip()
        if not path:
            continue
        for pattern in FORBIDDEN:
            if path == pattern or fnmatch.fnmatch(path, pattern) \
                    or (pattern.endswith("/") and path.startswith(pattern)):
                bad.append(path)
                break
    return bad


def changed_between(base: str, head: str) -> list[str]:
    proc = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise SystemExit(f"git diff failed: {proc.stderr.strip()[:300]}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _emit(outputs: dict) -> None:
    for k, v in outputs.items():
        print(f"{k}: {v}")
    path = os.environ.get("GITHUB_OUTPUT", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in outputs.items():
            fh.write(f"{k}={str(v).replace(chr(10), ' ')}\n")


def gate(args) -> int:
    repo = args.repo
    workflow, conclusion, branch = args.workflow, args.conclusion, args.branch
    if not (workflow and conclusion):
        workflow, conclusion, fetched, _url = run_metadata(repo, args.run_id)
        branch = branch or fetched
        if not workflow:
            _emit({"heal": "no",
                   "reason": f"could not read run {args.run_id} from the "
                             "API, and a gate that cannot see the failure "
                             "heals nothing."})
            return 0

    cause = ""
    if (conclusion or "").lower() == "failure":
        cause, _context = ci_alert.extract_cause(
            ci_alert.fetch_failed_log(repo, args.run_id))

    heal, reason = classify(workflow, conclusion, cause, branch or "main")
    if not heal:
        _emit({"heal": "no", "reason": reason})
        return 0

    branch_ref = branch_name(workflow, cause)
    open_prs = open_healer_prs(repo)
    same = [p["number"] for p in open_prs if p.get("headRefName") == branch_ref]
    if same:
        _emit({"heal": "no",
               "reason": f"a healer PR for this exact cause is already open "
                         f"(#{same[0]}, branch {branch_ref})."})
        return 0
    if len(open_prs) >= MAX_OPEN_PRS:
        _emit({"heal": "no",
               "reason": f"{len(open_prs)} healer PRs are already open "
                         f"(ceiling {MAX_OPEN_PRS})."})
        return 0

    _emit({"heal": "yes", "reason": reason, "branch": branch_ref,
           "fingerprint": fingerprint(workflow, cause),
           "cause": (cause or "(no cause line could be extracted)")[:400]})
    return 0


def check(args) -> int:
    if args.files is not None:
        changed = [p for p in args.files if p.strip()]
    else:
        changed = changed_between(args.base, args.head)
    bad = violations(changed)
    if bad:
        print("::error::the healer's branch touched FORBIDDEN paths:")
        for path in bad:
            print(f"::error::  {path}")
        print("::error::data/, spend/guardrail constants, the locks, the "
              "handover, or the healer itself. This PR must not be merged as "
              "it stands; the guard fails the healer run so the red is on "
              "the healer, not hidden in the draft.")
        return 1
    print(f"checked {len(changed)} changed path(s): none are forbidden.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="decide whether a failed run is healable")
    g.add_argument("--run-id", required=True)
    g.add_argument("--workflow", default="")
    g.add_argument("--conclusion", default="")
    g.add_argument("--branch", default="")
    g.add_argument("--repo", default=os.environ.get(
        "GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker"))

    c = sub.add_parser("check", help="fail if a branch touched forbidden paths")
    c.add_argument("--base", default="origin/main")
    c.add_argument("--head", default="HEAD")
    c.add_argument("--files", nargs="*", default=None)

    args = ap.parse_args(argv)
    return gate(args) if args.cmd == "gate" else check(args)


if __name__ == "__main__":
    sys.exit(main())

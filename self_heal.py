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

  * a cancellation from OUTSIDE the job — an eviction from the talent-collect
    lock, a superseded push, a human. Routine here by design; ops_status [2b]
    owns evictions. But a job that ran past its own `timeout-minutes` ALSO
    arrives as `cancelled`, and that one IS healed: it is a real, repeating,
    permanent failure that this gate refused wholesale until 2026-08-18, while
    ci_alert.py mailed the same event as CI SELF-TIMEOUT. The two are told
    apart by `ci_alert.is_self_timeout_cause()`, CALLED here and never
    re-implemented. The healer's hard limit against widening a ceiling is what
    keeps that fix honest: make the job fit, do not move the wall.
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
    conc = (conclusion or "").lower()
    if conc == "cancelled":
        # `cancelled` is TWO events wearing one word, and in this repo it is
        # three: an eviction from the talent-collect lock, a superseded push,
        # or a job that ran past its own `timeout-minutes`. Only the last is a
        # failure, and GitHub reports it as `cancelled` rather than
        # `timed_out`. ci_alert.py has always known that; this gate did not,
        # and skipped the whole class until 2026-08-18.
        if not ci_alert.is_self_timeout_cause(cause):
            return False, ("cancelled by something OUTSIDE the job (an "
                           "eviction from the talent-collect lock, a "
                           "superseded push, a human). Routine, and not a "
                           "failure; ops_status [2b] owns evictions. Only a "
                           "run that killed itself on its own timeout-minutes "
                           "is healable here.")
        # fall through: a self-timeout IS a failure, and a code-shaped one.
    elif conc != "failure":
        return False, (f"conclusion '{conclusion}' is not healable: only a "
                       "plain failure, or a `cancelled` that is really a "
                       "self-timeout, is. Success needs nothing.")
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
    conc = (conclusion or "").lower()
    if conc == "failure":
        cause, _context = ci_alert.extract_cause(
            ci_alert.fetch_failed_log(repo, args.run_id))
    elif conc == "cancelled":
        # A self-killed job has NO failed step, so --log-failed returns nothing;
        # the distinguishing line lives in the job's check-run annotations. The
        # same call the alerter makes, so the two cannot disagree about one run.
        cause = ci_alert.self_timeout_of_run(repo, args.run_id) or ""

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


# --------------------------------------------------------------------------
# The MERGE GATE — owner-authorized auto-merge, 2026-08-14 ("a human clicks
# merge — I want you to click merge, I'm okay with that"). The click is
# delegated; the CONDITIONS are not, and every one resolves UNKNOWN to "stay
# a draft", never to a pass:
#
#   1. the adversarial reviewer's LATEST machine-readable verdict is exactly
#      LOOKS SOUND (absent, ambiguous, or anything else = no merge);
#   2. the forbidden-path guard passed (wired in the workflow: the automerge
#      job `needs` the guard job's success);
#   3. the branch's diff is source/test files only — never workflows, and
#      never anything in FORBIDDEN (so data/, the pots and the locks cannot
#      reach the merge step whatever the verdict says);
#   4. the merged preview runs the offline suite and produces NO failure that
#      main does not already have. A standing red fails BOTH runs and is
#      subtracted; anything new blocks. This also closes the gap where a
#      branch pushed with GITHUB_TOKEN triggers no checks at all.
#
# Kill switch: repository variable SELF_HEAL_AUTOMERGE_DISABLED=true turns
# only the merge off; the healer keeps drafting for a human.
# --------------------------------------------------------------------------

#: The first line of the adversarial reviewer's PR comment. Anything that does
#: not match, or matches with a different verdict, keeps the draft.
VERDICT_MARKER = "SELF-HEAL-REVIEW-VERDICT:"
_VERDICT = re.compile(
    rf"^{re.escape(VERDICT_MARKER)}\s*(LOOKS SOUND|NEEDS WORK|DO NOT MERGE)\s*$",
    re.MULTILINE)


def review_verdict(comment_bodies) -> str | None:
    """The LATEST verdict across a PR's comments, or None. A comment carrying
    several markers is ambiguous and counts as no verdict at all."""
    verdict = None
    for body in comment_bodies:
        found = _VERDICT.findall(body or "")
        if len(found) == 1:
            verdict = found[0]
        elif len(found) > 1:
            verdict = None
    return verdict


def fetch_review_verdict(repo: str, pr: str) -> str | None:
    out = _gh(["api", f"repos/{repo}/issues/{pr}/comments",
               "--paginate", "-q", "[.[].body]"])
    if not out:
        return None
    bodies: list = []
    try:
        for chunk in out.strip().splitlines():
            bodies.extend(json.loads(chunk))
    except ValueError:
        return None
    return review_verdict(bodies)


def automergeable_paths(changed: list[str]) -> tuple[bool, str]:
    """Stricter than the guard: auto-merge additionally refuses ANY workflow
    or CI change — a human can merge those from the draft."""
    bad = violations(changed)
    if bad:
        return False, f"forbidden paths changed: {', '.join(bad)}"
    ci = [p for p in changed if p.strip().startswith(".github/")]
    if ci:
        return False, ("the fix edits CI/workflows "
                       f"({', '.join(ci)}); auto-merge never ships those")
    if not changed:
        return False, "the branch changes nothing"
    return True, "source/test files only"


#: pytest -q / unittest failure headers, one per failing test.
_SUITE_FAIL = re.compile(
    r"^FAILED\s+(\S+::\S+)"
    r"|^(?:FAIL|ERROR):\s+(\S+(?:\s+\([^)]*\))?)\s*$",
    re.MULTILINE)


def suite_failures(output: str) -> set:
    """The set of failing test identities in a suite run's output."""
    return {a or b for a, b in _SUITE_FAIL.findall(output or "")}


def run_suite(test_cmd: str, cwd: str):
    proc = subprocess.run(["bash", "-c", test_cmd], capture_output=True,
                          text=True, timeout=3600, cwd=cwd)
    return suite_failures(proc.stdout + "\n" + proc.stderr), proc.returncode


def merge_gate(args) -> int:
    """Exit 0 = every condition holds and the PR may be merged. Anything else
    prints why and exits 1, which the workflow treats as 'stays a draft' — a
    decision, not a red run."""
    repo = args.repo

    verdict = fetch_review_verdict(repo, args.pr)
    if verdict != "LOOKS SOUND":
        print(f"no merge: the reviewer's verdict is "
              f"{verdict or 'absent/ambiguous'}, and only LOOKS SOUND merges. "
              "UNKNOWN is never a pass.")
        return 1
    print("reviewer verdict: LOOKS SOUND")

    changed = changed_between("origin/main", f"origin/{args.branch}")
    ok, reason = automergeable_paths(changed)
    if not ok:
        print(f"no merge: {reason}")
        return 1
    print(f"paths: {reason} ({len(changed)} changed)")

    base_fail, _ = run_suite(args.test_cmd, args.test_cwd)
    print(f"main baseline: {len(base_fail)} failing test(s)")
    merge = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", f"origin/{args.branch}"],
        capture_output=True, text=True, timeout=120)
    if merge.returncode != 0:
        print(f"no merge: the branch does not merge cleanly onto main: "
              f"{merge.stderr.strip()[:200]}")
        return 1
    head_fail, _ = run_suite(args.test_cmd, args.test_cwd)
    print(f"merged preview: {len(head_fail)} failing test(s)")
    new = sorted(head_fail - base_fail)
    if new:
        print("no merge: the fix introduces failures main does not have:")
        for name in new[:10]:
            print(f"  {name}")
        return 1
    fixed = sorted(base_fail - head_fail)
    print(f"no new failures; {len(fixed)} baseline failure(s) fixed"
          + (f": {', '.join(fixed[:5])}" if fixed else ""))
    print("merge-gate: ALL CONDITIONS HOLD")
    return 0


# --------------------------------------------------------------------------
# THE HEALING LEDGER — "if things break it's easy to backtrack and fix fast".
# Every auto-merge appends a terse revert-index entry to docs/HEALING-LOG.md
# and a narrative entry to docs/TECHLOG.md. BEST-EFFORT: the workflow step
# warns loudly on failure and stays green, because a heal that merged is not
# undone by a docs write that did not. Both files are append-only newest-
# first; each run inserts only its own entry, so a racing merge keeps BOTH.
# --------------------------------------------------------------------------

HEALING_LOG = "docs/HEALING-LOG.md"
TECHLOG = "docs/TECHLOG.md"

_HEALING_HEADER = """# Healing log — auto-merged fixes

The terse revert index for everything the self-healer merged on its own
(owner authorization 2026-08-14). **Every heal is ONE squash commit: the
revert is `git revert <merge sha>`.** Draft-only mode is one line: set the
repository variable `SELF_HEAL_AUTOMERGE_DISABLED=true`. Newest first; if two
merges race, keep BOTH entries. The narrative for each heal lives in
docs/TECHLOG.md under the same date.
"""


def _insert_entry(path: str, header: str, entry: str) -> None:
    """Prepend `entry` before the first '## ' heading (newest-first),
    creating the file with `header` when absent."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = header
    lines = text.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(lines) if ln.startswith("## ")),
              len(lines))
    lines[at:at] = [entry if entry.endswith("\n") else entry + "\n"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))


def record(args) -> int:
    """Append the two ledger entries for one auto-merged heal. 0 on success,
    1 on any problem — and the CALLER treats 1 as a warning, never as a
    failed heal."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    day = now.strftime("%Y-%m-%d")
    files = [f for f in (args.files or []) if f.strip()]
    filelist = ", ".join(files) if files else "(unrecorded)"

    ledger_entry = (
        f"## {stamp} — {args.workflow} — PR #{args.pr} — merge {args.merge_sha}\n"
        f"- run:      {args.run_url or '(unrecorded)'}\n"
        f"- cause:    {args.cause or '(no cause line extracted)'}\n"
        f"- files:    {filelist}\n"
        f"- reviewer: {VERDICT_MARKER} {args.verdict}\n"
        f"- revert:   `git revert {args.merge_sha}`\n\n")

    techlog_entry = (
        f"## {day} - self-heal: auto-merged fix for '{args.workflow}' (PR #{args.pr})\n\n"
        f"**What failed:** {args.cause or 'no cause line could be extracted'} "
        f"({args.run_url or 'run url unrecorded'}).\n\n"
        f"**The fix:** {filelist}. PR #{args.pr} carries the diff and the "
        f"red-before/green-after evidence; the squash merge is "
        f"{args.merge_sha}.\n\n"
        f"**Adversarial review:** {args.verdict} — the reviewer's PR comment "
        f"records what it tried in order to break the fix.\n\n"
        f"**Revert:** `git revert {args.merge_sha}`. Auto-merged under the "
        f"owner's 2026-08-14 authorization; the kill switch is the repository "
        f"variable `SELF_HEAL_AUTOMERGE_DISABLED=true` (draft-only mode).\n\n")

    ok = True
    for path, header, entry in ((args.healing_log, _HEALING_HEADER, ledger_entry),
                                (args.techlog, "# Tech Log\n\n", techlog_entry)):
        try:
            _insert_entry(path, header, entry)
            print(f"recorded in {path}")
        except OSError as exc:
            print(f"::warning::could not record the heal in {path}: {exc}. "
                  "The merge itself is unaffected; add the entry by hand.")
            ok = False
    return 0 if ok else 1


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

    m = sub.add_parser("merge-gate",
                       help="exit 0 only if every owner-authorized "
                            "auto-merge condition holds")
    m.add_argument("--pr", required=True)
    m.add_argument("--branch", required=True)
    m.add_argument("--test-cmd", default=".venv/bin/pytest -q")
    m.add_argument("--test-cwd", default=".")
    m.add_argument("--repo", default=os.environ.get(
        "GITHUB_REPOSITORY", "dk-forge/talent-intelligence-tracker"))

    r = sub.add_parser("record",
                       help="append one auto-merged heal to the ledgers "
                            "(best-effort; never fails the heal)")
    r.add_argument("--pr", required=True)
    r.add_argument("--workflow", required=True)
    r.add_argument("--merge-sha", required=True)
    r.add_argument("--run-url", default="")
    r.add_argument("--cause", default="")
    r.add_argument("--verdict", default="LOOKS SOUND")
    r.add_argument("--files", nargs="*", default=None)
    r.add_argument("--healing-log", default=HEALING_LOG)
    r.add_argument("--techlog", default=TECHLOG)

    args = ap.parse_args(argv)
    if args.cmd == "gate":
        return gate(args)
    if args.cmd == "merge-gate":
        return merge_gate(args)
    if args.cmd == "record":
        return record(args)
    return check(args)


if __name__ == "__main__":
    sys.exit(main())

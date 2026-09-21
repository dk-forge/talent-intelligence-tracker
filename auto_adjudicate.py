#!/usr/bin/env python3
"""Put guardrail findings to the two referees BEFORE they redden the data jobs.

    python3 auto_adjudicate.py                  # dry run: WOULD ASK, no call, no fetch, no write
    python3 auto_adjudicate.py --live           # ask, write specs + state + a plan
    python3 auto_adjudicate.py --act PLAN.json  # enqueue the agreed specs, sync the issue

WHY (2026-09-17 to 2026-09-21). Four findings sat past their grace window and
every collect / enrich run was red until a session resolved them by hand (PRs
#169 to #175). The owner's standing rule is "two independent referees first,
act on agreement, only disagreements reach me", and it was being applied by
hand. This is the same rule on a clock, with every laptop off.

WHAT IT REUSES, AND WHAT IT REFUSES TO OWN.

  which findings   `guardrails.quarantine`, the function `pipeline/publish.py`
                   calls to decide "overdue". Imported, never re-derived, and
                   pinned by identity in the tests. "Due soon" is read off the
                   `age_hours` and `grace_hours` that function itself attaches.
  the referees     `adjudicate_guardrail.adjudicate`, unchanged. Same prompt,
                   same evidence floor, same agreement rule, same spec files.
                   There is no second referee in this file.
  the write        NONE here. This job opens the database read-only and holds
                   no lock. An agreement is a spec with status `agree-dry-run`;
                   it is applied by queueing `adjudicate-rows.yml from_spec=`
                   through `drain-writers.yml`, the one door every database
                   write in this repository goes through.

FIVE OUTCOMES, AND ONLY ONE OF THEM APPLIES ANYTHING.

  AGREE            both referees, same action (and the same correction for an
                   edit). Queued once.
  DISAGREE         two usable verdicts that differ. Nothing applied. Issue.
  UNKNOWN          no evidence over the 800 character floor, or no current row.
                   Nothing applied. Issue.
  UNDECIDED        a budget stop, a referee that did not answer, or an answer
                   that did not parse. NOT a verdict and above all NOT a "no":
                   a truncated, code-fenced answer was once stored as a
                   rejection in the sibling tracker (33 of 90). Counted apart,
                   retried on a later tick (three asks at most), never applied.
  NOT ASKABLE      vehicle_name, period_totals, date_span: the referee prompt
                   has no vocabulary for them (HANDOVER 2026-09-21, Digital
                   Realty). Never asked, never auto-accepted. Issue. The same
                   goes for a REJECTED finding whose row is live: only
                   `retract.py` finishes that, and a person runs it.

SPEND. Every request goes through `classify._call` behind
`adjudicate_guardrail._gate` (the month's switch, then the ceiling), one gate
read per attempt. On top of that this job keeps its own two ceilings: one run
(default $0.25) and one calendar month (default $3.00), the month read from
the committed `data/auto_adjudicate_state.json`. A binding ceiling is
UNDECIDED, exits 0, and is printed in the run summary with the real spend.

EXIT. 0 whenever the job ran, including "could not decide". 1 only for a real
fault: the database or the ledger table cannot be read, the queue would not
take a ticket, the issue could not be written.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import adjudicate_guardrail as adj  # noqa: E402
from pipeline import classify, guardrails, schema  # noqa: E402

#: THE overdue function. The same object `pipeline.publish` calls; a test pins
#: `auto_adjudicate.quarantine is guardrails.quarantine`.
quarantine = guardrails.quarantine

RUN_CAP_USD = 0.25
MONTH_CAP_USD = 3.00
#: A finding this close to its deadline is put to the referees now, so the
#: answer lands before the data jobs go red rather than after.
DUE_WITHIN_HOURS = 24
#: Paid asks per key before an UNDECIDED becomes "a human decides".
MAX_ASKS = 3
#: A queued apply that has not closed the finding is queued again after this.
REQUEUE_AFTER_HOURS = 24

#: What the referee prompt can actually be asked about.
ASKABLE = (guardrails.AMOUNT, adj.PLACE)

STATE_PATH = REPO / "data" / "auto_adjudicate_state.json"
ISSUE_TITLE = "Guardrail findings that need a ruling"
ISSUE_MARKER = "<!-- auto-adjudicate-guardrails:ruling-queue -->"
ISSUE_LABEL = "guardrail-ruling"

AGREE, DISAGREE, UNKNOWN, UNDECIDED, NOT_ASKABLE, QUEUED = (
    "AGREE", "DISAGREE", "UNKNOWN", "UNDECIDED", "NOT ASKABLE", "QUEUED")

# What one referee did, kept apart on purpose.
R_VERDICT, R_BUDGET, R_NO_ANSWER, R_PARSE, R_LOW_CONF = (
    "verdict", "budget_stop", "no_answer", "parse_failure", "low_confidence")


class Fault(RuntimeError):
    """A real fault: exit 1. Never raised for "could not decide"."""


# --------------------------------------------------------------------------
# State: the month's spend, and what each key has already cost us
# --------------------------------------------------------------------------

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    except ValueError as exc:
        # An unreadable spend ledger is not "nothing spent".
        raise Fault(f"{path}: state file is not JSON ({exc})") from exc
    state.setdefault("version", 1)
    state.setdefault("months", {})
    state.setdefault("keys", {})
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def month_key(now: _dt.datetime) -> str:
    return now.strftime("%Y-%m")


def month_spent(state: dict, now: _dt.datetime) -> float:
    return float(state["months"].get(month_key(now), 0.0))


# --------------------------------------------------------------------------
# Which findings
# --------------------------------------------------------------------------

def _key(row: dict) -> str:
    return f"{row['check_name']}/{row['subject']}"


def list_findings(conn, *, due_within: float = DUE_WITHIN_HOURS) -> list[dict]:
    """Everything this job has an opinion about, each tagged with its clock.

    The clock comes from `quarantine` and nowhere else. On top of it, every
    OPEN `amount` or `place` ledger row is listed with `clock: none`: a finding
    a session raised by hand fires no check, so no clock ever starts for it
    and it would otherwise sit open for good with nobody told.
    """
    try:
        conn.execute("SELECT 1 FROM publish_guardrails LIMIT 1").fetchall()
    except Exception as exc:  # noqa: BLE001
        raise Fault(f"publish_guardrails cannot be read: {exc}") from exc

    report = quarantine(conn, write=False)
    overdue_ids = {id(r) for r in report["overdue"]}
    overdue_keys = {_key(r) for r in report["overdue"]}
    out: dict[str, dict] = {}

    for row in list(report["held"]) + list(report["live"]):
        age, grace = row.get("age_hours"), row.get("grace_hours")
        if id(row) in overdue_ids or _key(row) in overdue_keys:
            clock = "overdue"
        elif age is not None and grace is not None and age > grace - due_within:
            clock = "due"
        else:
            continue
        out[_key(row)] = {**row, "key": _key(row), "clock": clock,
                          "state": "rejected" if row.get("rejected") else "open"}

    for row in report["aggregate"]:
        out[_key(row)] = {**row, "key": _key(row), "clock": "halts", "state": "open"}

    placeholders = ", ".join("?" for _ in ASKABLE)
    for raw in conn.execute(
            f"SELECT * FROM publish_guardrails WHERE state = 'open' "
            f"AND check_name IN ({placeholders}) "
            f"ORDER BY COALESCE(value, 0) DESC, check_name, subject", ASKABLE):
        row = dict(raw)
        if _key(row) not in out:
            out[_key(row)] = {**row, "key": _key(row), "clock": "none",
                              "age_hours": guardrails._hours_since(row.get("first_seen")),
                              "grace_hours": None}
    return list(out.values())


# --------------------------------------------------------------------------
# What the referees have already said
# --------------------------------------------------------------------------

def latest_spec(key: str, spec_dir: Path | None = None) -> tuple[Path, dict] | None:
    spec_dir = Path(spec_dir or adj.SPEC_DIR)
    paths = sorted(spec_dir.glob(f"*-{key.replace('/', '-')}.json"))
    for path in reversed(paths):
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return None


def _reason(verdict: dict | None) -> str:
    if not verdict:
        return ""
    text = str(verdict.get("reasoning") or verdict.get("reason") or "")
    return re.sub(r"\s+", " ", text).strip()[:220]


def _verdict_text(model: str, verdict: dict | None, how: str = "") -> str:
    if not verdict:
        return f"{model}: no usable verdict" + (f" ({how})" if how else "")
    bits = [str(verdict.get("recommended"))]
    for field in ("corrected_amount", "corrected_basis", "corrected_city", "corrected_country"):
        if verdict.get(field) is not None:
            bits.append(f"{field}={verdict[field]}")
    if verdict.get("confidence") is not None:
        bits.append(f"confidence {verdict['confidence']}")
    reason = _reason(verdict)
    return f"{model}: " + ", ".join(bits) + (f". {reason}" if reason else "")


def spec_outcome(spec: dict) -> str | None:
    """A terminal outcome an existing spec already carries, or None when the
    spec settles nothing (a budget stop or a parse failure leaves one too)."""
    status = spec.get("status")
    if status in ("agree-dry-run", "applied"):
        # "applied" on a finding that is STILL open means the write did not
        # reach main (the merge_db revert of 2026-09-20). Re-applying is free.
        return AGREE
    if status == "disagree":
        return DISAGREE
    if status == "unknown" and spec.get("why"):
        return UNKNOWN
    return None


# --------------------------------------------------------------------------
# One referee's answer, classified. A parse failure is never a "no".
# --------------------------------------------------------------------------

def classify_answer(raw: str | None, parse) -> str:
    """How one raw referee answer should be COUNTED. `parse` is the adjudicator's
    own parser; this only explains a None it returned."""
    if raw is None:
        return R_NO_ANSWER
    if parse(raw) is not None:
        return R_VERDICT
    text = classify._strip_fences(raw) if hasattr(classify, "_strip_fences") else raw
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("recommended") in adj.ACTIONS:
        return R_LOW_CONF
    return R_PARSE


class Recorder:
    """Wraps the ONE paid door so each raw answer can be counted afterwards.
    Performs exactly one inner request per call; it never retries."""

    def __init__(self, inner):
        self.inner = inner
        self.attempts: dict[str, int] = {}
        self.raw: dict[str, str] = {}

    def __call__(self, model, system, user, **kw):
        self.attempts[model] = self.attempts.get(model, 0) + 1
        content = self.inner(model, system, user, **kw)
        self.raw[model] = content
        return content


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------

def run(conn, *, live: bool, state: dict, now: _dt.datetime | None = None,
        run_cap: float = RUN_CAP_USD, month_cap: float = MONTH_CAP_USD,
        call=None, fetch=None, wayback=None, spec_dir: Path | None = None,
        out=print) -> dict:
    """Decide what to do with every listed finding. Writes specs (through the
    adjudicator) and mutates `state` only when `live`. Returns the plan."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    spec_dir = Path(spec_dir or adj.SPEC_DIR)
    findings = list_findings(conn)
    start_usd = float(classify.STATS.get("usd", 0.0))
    spent_before = month_spent(state, now)
    counts = {R_VERDICT: 0, R_BUDGET: 0, R_NO_ANSWER: 0, R_PARSE: 0, R_LOW_CONF: 0}
    items: list[dict] = []

    out(f"auto-adjudicate: {len(findings)} finding(s) in scope; "
        f"{'LIVE' if live else 'DRY RUN (no call, no fetch, no write)'}; "
        f"run cap ${run_cap:.2f}, month cap ${month_cap:.2f} "
        f"(${spent_before:.4f} spent in {month_key(now)})")

    for f in findings:
        key, check = f["key"], f["check_name"]
        item = {"key": key, "label": f.get("label") or "", "clock": f["clock"],
                "age_hours": f.get("age_hours"), "grace_hours": f.get("grace_hours"),
                "outcome": None, "lines": [], "spec": None, "enqueue": False}
        items.append(item)
        kstate = state["keys"].get(key, {})

        if f.get("state") == "rejected":
            item["outcome"] = NOT_ASKABLE
            item["lines"] = ["rejected and the row is LIVE: only `retract.py` takes the "
                             "figure down, and a person runs it"]
        elif check not in ASKABLE:
            item["outcome"] = NOT_ASKABLE
            item["lines"] = [f"the referee prompt has no vocabulary for `{check}`; never "
                             "auto-accepted. Answer through adjudicate-rows.yml "
                             "accept_keys / reject_keys with a note"]
        else:
            found = latest_spec(key, spec_dir)
            settled = spec_outcome(found[1]) if found else None
            if settled == AGREE:
                _plan_agreed(item, found[0], found[1], kstate, now, live, state)
            elif settled in (DISAGREE, UNKNOWN):
                item["outcome"] = settled
                item["spec"] = _rel(found[0])
                item["lines"] = _spec_lines(found[1])
            elif int(kstate.get("asks", 0)) >= MAX_ASKS:
                item["outcome"] = UNDECIDED
                item["lines"] = [f"asked {kstate['asks']} times without two usable verdicts "
                                 f"(last: {kstate.get('last', 'unknown')}); not asked again"]
                item["exhausted"] = True
            elif not live:
                item["outcome"] = "WOULD ASK"
                item["lines"] = [f"WOULD ASK {adj.REFEREES[0]} + {adj.REFEREES[1]} "
                                 "(skipped: a dry run calls no paid model)"]
            else:
                _ask(conn, item, state, now, start_usd=start_usd, run_cap=run_cap,
                     month_cap=month_cap, spent_before=spent_before, counts=counts,
                     call=call, fetch=fetch, wayback=wayback, spec_dir=spec_dir, out=out)

        out(f"  {item['outcome']:<11} {key}  {item['label']}  [clock: {item['clock']}]")
        for line in item["lines"]:
            out(f"              {line}")

    spent = float(classify.STATS.get("usd", 0.0)) - start_usd
    if live:
        months = state["months"]
        months[month_key(now)] = round(spent_before + spent, 6)
        state["last_run"] = {"at": now.isoformat(timespec="seconds"),
                             "spent_usd": round(spent, 6), "referee_answers": counts}
    return {"items": items, "spent_usd": round(spent, 6), "counts": counts,
            "month_spent_usd": round(spent_before + spent, 6),
            "run_cap": run_cap, "month_cap": month_cap, "live": live,
            "referees": list(adj.REFEREES)}


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _spec_lines(spec: dict) -> list[str]:
    lines = []
    if spec.get("why"):
        lines.append(f"why: {spec['why']}")
    for model, verdict in (spec.get("verdicts") or {}).items():
        lines.append(_verdict_text(model, verdict))
    return lines or ["no verdict on file"]


def _plan_agreed(item, path, spec, kstate, now, live, state) -> None:
    """An agreement on file and the finding still open: queue its apply ONCE."""
    item["spec"] = _rel(path)
    item["lines"] = [f"agreed: {spec.get('action')}"] + _spec_lines(spec)
    queued_at = kstate.get("enqueued_at")
    if queued_at:
        try:
            age = (now - _dt.datetime.fromisoformat(queued_at)).total_seconds() / 3600
        except ValueError:
            age = None
        if age is not None and age < REQUEUE_AFTER_HOURS:
            item["outcome"] = QUEUED
            item["lines"].insert(0, f"apply already queued {queued_at}; not queued twice")
            return
    item["outcome"] = AGREE
    item["enqueue"] = True
    if live:
        state["keys"].setdefault(item["key"], {})["enqueued_at"] = now.isoformat(timespec="seconds")


def _ask(conn, item, state, now, *, start_usd, run_cap, month_cap, spent_before,
         counts, call, fetch, wayback, spec_dir, out) -> None:
    key = item["key"]
    spent = float(classify.STATS.get("usd", 0.0)) - start_usd
    ceiling = min(run_cap, max(0.0, month_cap - spent_before))
    if spent >= ceiling or not classify.paid_reads_enabled():
        item["outcome"] = UNDECIDED
        which = ("the month's switch (TIT_PAID_READS) is off" if not classify.paid_reads_enabled()
                 else f"ceiling ${ceiling:.2f} reached (${spent:.4f} this run)")
        item["lines"] = [f"budget stop: {which}. Not a verdict; asked again on a later tick"]
        item["budget_stop"] = True
        counts[R_BUDGET] += len(adj.REFEREES)
        return

    recorder = Recorder(call or classify._call)
    kwargs = {"apply": False, "start_usd": start_usd, "call": recorder,
              "spec_dir": spec_dir, "ceiling": ceiling}
    if fetch is not None:
        kwargs["fetch"] = fetch
    if wayback is not None:
        kwargs["wayback"] = wayback
    # apply=False, ALWAYS. This process never writes the database; an agreement
    # leaves an `agree-dry-run` spec and the queue applies it.
    rc = adj.adjudicate(conn, key, **kwargs)
    if rc == 1:
        raise Fault(f"{key}: the adjudicator could not load the finding")

    found = latest_spec(key, spec_dir)
    spec = found[1] if found else {}
    parse = adj.parse_place_verdict if key.startswith(adj.PLACE + "/") else adj.parse_verdict
    verdicts = spec.get("verdicts") or {}
    how: dict[str, str] = {}
    for model in adj.REFEREES:
        if recorder.attempts.get(model, 0) == 0:
            # Never asked: a budget stop, unless the adjudicator stopped before
            # the referees for a reason it wrote down (no evidence, no row).
            how[model] = "" if spec.get("why") else R_BUDGET
        else:
            how[model] = classify_answer(recorder.raw.get(model), parse)
        if how[model]:
            counts[how[model]] += 1
    asked = bool(recorder.attempts)
    kstate = state["keys"].setdefault(key, {})
    if asked:
        kstate["asks"] = int(kstate.get("asks", 0)) + 1

    settled = spec_outcome(spec)
    item["spec"] = _rel(found[0]) if found else None
    if settled == AGREE and rc == 0:
        kstate["last"] = AGREE
        _plan_agreed(item, found[0], spec, kstate, now, True, state)
    elif settled in (DISAGREE, UNKNOWN):
        kstate["last"] = settled
        item["outcome"] = settled
        item["lines"] = _spec_lines(spec)
    else:
        # Fewer than two usable verdicts. Whatever the reason, it is not a "no".
        item["outcome"] = UNDECIDED
        kstate["last"] = ", ".join(f"{m}: {how[m] or 'not asked'}" for m in adj.REFEREES)
        item["lines"] = [_verdict_text(m, verdicts.get(m), how[m]) for m in adj.REFEREES]
        item["lines"].append("not a verdict and never applied; asked again on a later tick "
                             f"({kstate.get('asks', 0)} of {MAX_ASKS} asks used)")
        if any(h == R_BUDGET for h in how.values()):
            item["budget_stop"] = True


# --------------------------------------------------------------------------
# Acting on the plan: the queue, then the issue
# --------------------------------------------------------------------------

def run_gh(args: list[str], *, stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Fault(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def enqueue_agreed(plan: dict, *, gh=run_gh, out=print) -> int:
    """Queue ONE adjudicate-rows ticket re-applying every agreed spec. It calls
    no referee (`from_spec`) and is the only thing here that leads to a write."""
    specs = [i["spec"] for i in plan["items"]
             if i.get("enqueue") and i.get("outcome") == AGREE and i.get("spec")]
    if not specs:
        return 0
    reason = f"auto-adjudicate: two referees agreed on {len(specs)} guardrail finding(s)"
    inputs = {"from_spec": ",".join(specs), "dry_run": "false", "reason": reason}
    gh(["workflow", "run", "drain-writers.yml", "-f", "enqueue=adjudicate-rows.yml",
        "-f", f"inputs_json={json.dumps(inputs)}", "-f", f"reason={reason}"])
    out(f"queued adjudicate-rows.yml from_spec for {len(specs)} agreed spec(s)")
    return len(specs)


def issue_lines(plan: dict) -> list[str]:
    """One line per finding a human has to look at. An AGREE or a plain
    budget stop is not one; an exhausted UNDECIDED is."""
    lines = []
    for i in plan["items"]:
        outcome = i.get("outcome")
        if outcome in (AGREE, QUEUED, "WOULD ASK"):
            continue
        if outcome == UNDECIDED and not i.get("exhausted"):
            continue
        detail = " | ".join(i.get("lines") or [])
        spec = f" | spec `{i['spec']}`" if i.get("spec") else ""
        lines.append(f"- **{outcome}** `{i['key']}` {i.get('label', '')} "
                     f"(clock: {i.get('clock')}) | {detail}{spec}")
    return lines


def issue_body(lines: list[str]) -> str:
    head = (f"{ISSUE_MARKER}\n"
            "Findings the two referees could not settle, or that they cannot be asked about. "
            "Nothing below was applied. Written by `auto-adjudicate-guardrails.yml`, updated "
            "in place, closed when empty.\n\n")
    if not lines:
        return head + "Nothing is waiting on a ruling.\n"
    how = ("\n\nTo rule: an owner-ruled spec through `adjudicate-rows.yml from_spec=`, or "
           "`accept_keys` / `reject_keys` with a note for a check the referees cannot be "
           "asked about. A rejected LIVE row needs `retract.py`.\n")
    return head + "\n".join(lines) + how


def sync_issue(lines: list[str], *, gh=run_gh, out=print) -> str:
    """Create, update in place, reopen or close the ONE issue. Returns what it did."""
    gh(["label", "create", ISSUE_LABEL, "--force", "--color", "B60205",
        "--description", "Guardrail findings waiting on a human ruling"])
    listed = json.loads(gh(["issue", "list", "--state", "all", "--label", ISSUE_LABEL,
                            "--limit", "50", "--json", "number,state,body"]) or "[]")
    ours = [i for i in listed if ISSUE_MARKER in (i.get("body") or "")]
    ours.sort(key=lambda i: (i.get("state") != "OPEN", -int(i["number"])))
    body = issue_body(lines)
    if not ours:
        if not lines:
            return "none-needed"
        gh(["issue", "create", "--title", ISSUE_TITLE, "--label", ISSUE_LABEL,
            "--body-file", "-"], stdin=body)
        return "created"
    issue = ours[0]
    number = str(issue["number"])
    is_open = issue.get("state") == "OPEN"
    if not lines:
        if not is_open:
            return "already-closed"
        gh(["issue", "edit", number, "--body-file", "-"], stdin=body)
        gh(["issue", "close", number, "--comment", "Nothing is waiting on a ruling."])
        return "closed"
    if not is_open:
        gh(["issue", "reopen", number])
    if (issue.get("body") or "").strip() == body.strip() and is_open:
        return "unchanged"
    gh(["issue", "edit", number, "--body-file", "-"], stdin=body)
    return "updated" if is_open else "reopened"


def summary(plan: dict) -> str:
    c = plan["counts"]
    tally: dict[str, int] = {}
    for i in plan["items"]:
        tally[i["outcome"]] = tally.get(i["outcome"], 0) + 1
    rows = "\n".join(f"| {i['outcome']} | `{i['key']}` | {i.get('label', '')} | {i.get('clock')} |"
                     for i in plan["items"]) or "| (nothing in scope) | | | |"
    return (
        "## auto-adjudicate-guardrails\n\n"
        f"- mode: {'LIVE' if plan['live'] else 'DRY RUN'}\n"
        f"- referees: {' + '.join(plan['referees'])}\n"
        f"- spend this run: ${plan['spent_usd']:.4f} of a ${plan['run_cap']:.2f} run cap\n"
        f"- spend this month: ${plan['month_spent_usd']:.4f} of a ${plan['month_cap']:.2f} month cap\n"
        f"- outcomes: {json.dumps(tally, sort_keys=True)}\n"
        f"- referee answers: {c[R_VERDICT]} verdict, {c[R_PARSE]} parse failure, "
        f"{c[R_LOW_CONF]} low confidence, {c[R_NO_ANSWER]} no answer, {c[R_BUDGET]} budget stop "
        "(a parse failure is never counted as a no)\n\n"
        "| outcome | key | label | clock |\n|---|---|---|---|\n" + rows + "\n")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="ask the referees and write specs and state (default: dry run)")
    ap.add_argument("--act", metavar="PLAN", help="enqueue the agreed specs in PLAN and sync the issue")
    ap.add_argument("--plan-out", metavar="PATH", help="write the plan JSON here")
    ap.add_argument("--run-cap", type=float, default=RUN_CAP_USD)
    ap.add_argument("--month-cap", type=float, default=MONTH_CAP_USD)
    args = ap.parse_args(argv)

    try:
        if args.act:
            plan = json.loads(Path(args.act).read_text(encoding="utf-8"))
            if not plan.get("live"):
                print("dry run plan: nothing queued, the issue is not touched. WOULD write:")
                print(issue_body(issue_lines(plan)))
                return 0
            enqueue_agreed(plan)
            print(f"issue: {sync_issue(issue_lines(plan))}")
            return 0

        try:
            conn = schema.connect_ro()
        except Exception as exc:  # noqa: BLE001
            raise Fault(f"the database cannot be opened: {exc}") from exc
        state = load_state()
        plan = run(conn, live=args.live, state=state,
                   run_cap=args.run_cap, month_cap=args.month_cap)
        if args.live:
            save_state(state)
        text = summary(plan)
        print("\n" + text)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text)
        if args.plan_out:
            Path(args.plan_out).write_text(json.dumps(plan, indent=1, default=str), encoding="utf-8")
        if not args.live:
            print("WOULD write this issue body:\n" + issue_body(issue_lines(plan)))
        return 0
    except Fault as exc:
        print(f"::error::auto-adjudicate FAULT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""The two-referee rule on a clock: agreement queues once, nothing else applies.

Offline throughout: the model is a stub through `call`, the source through
`fetch`, GitHub through `gh`, the database a tmp_path. No test here opens a
connection, and the dry-run test proves the job itself opens none either.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

import adjudicate_guardrail as adj
import auto_adjudicate as auto
from pipeline import classify, guardrails, publish, schema, validate

A, B = adj.REFEREES
NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc)
HEADLINE = "Acme's $59 million investment in its new logistics centre"
HASH = validate.content_hash("acme", "company_development", "2026-09-11", HEADLINE, "Gestion")
ARTICLE = ("Acme invests $59 million in its own logistics facility in Callao, the company said. ") * 14
assert len(ARTICLE) >= adj.EVIDENCE_MIN_CHARS
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-adjudicate-guardrails.yml"


def _row(conn, content_hash=HASH, company="Acme", published_at="2026-09-12 01:00:00"):
    fields = {
        "signal_id": content_hash, "headline": HEADLINE, "summary": "s", "talent_readthrough": "t",
        "company": company, "company_key": company.lower(), "pillar": "company_development",
        "signal_direction": "neutral", "confidence": "reported",
        "source_url": "https://example.com/x", "source_name": "Gestion",
        "captured_at": "2026-09-11", "as_of": "2026-09-11", "content_hash": content_hash,
        "collector": "national_press", "published_date": "2026-09-11", "country": "PE",
        "funding_amount": "US$59 millones", "funding_amount_usd": 59_000_000,
        "money_basis": "company_raise", "published_at": published_at, "is_current": 1,
    }
    conn.execute(f"INSERT INTO signals ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                 tuple(fields.values()))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "rows.db")
    _row(c)
    adj.open_session_finding(c, guardrails.AMOUNT, HASH, "a capex figure shown as a raise")
    yield c
    c.close()


@pytest.fixture
def stats():
    saved = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(saved)


@pytest.fixture(autouse=True)
def _paid_reads_on(monkeypatch):
    monkeypatch.setenv("TIT_PAID_READS", "on")


def _money(recommended="edit", basis="outbound_investment", amount=59_000_000, confidence=90):
    return {"amount_stated_for_this_event": True, "money_basis": basis, "recommended": recommended,
            "corrected_amount": amount, "corrected_basis": basis, "confidence": confidence,
            "reasoning": "'Acme invests $59 million in its own facility'"}


def _referee(answers, stats, cost=0.004):
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append(model)
        stats["usd"] = float(stats.get("usd", 0.0)) + cost
        answer = answers[model]
        return answer if isinstance(answer, str) else json.dumps(answer)

    call.calls = calls
    return call


class Gh:
    """GitHub, as a list of issues and a log of every call."""

    def __init__(self, issues=None):
        self.issues = issues or []
        self.calls = []

    def __call__(self, args, *, stdin=None):
        self.calls.append((list(args), stdin))
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        if args[:2] == ["issue", "create"]:
            self.issues.append({"number": len(self.issues) + 1, "state": "OPEN", "body": stdin})
        if args[:2] == ["issue", "edit"]:
            self._get(args[2])["body"] = stdin
        if args[:2] == ["issue", "close"]:
            self._get(args[2])["state"] = "CLOSED"
        if args[:2] == ["issue", "reopen"]:
            self._get(args[2])["state"] = "OPEN"
        return ""

    def _get(self, number):
        return next(i for i in self.issues if str(i["number"]) == str(number))

    def dispatches(self):
        return [a for a, _ in self.calls if a[:2] == ["workflow", "run"]]


def _run(conn, tmp_path, stats, answers=None, *, live=True, state=None, **kw):
    state = state if state is not None else auto.load_state(tmp_path / "state.json")
    call = _referee(answers or {}, stats)
    plan = auto.run(conn, live=live, state=state, now=NOW, call=call,
                    fetch=lambda url: ARTICLE, wayback=lambda url: None,
                    spec_dir=tmp_path / "specs", out=lambda *_: None, **kw)
    return plan, state, call


def _ledger(conn):
    return conn.execute("SELECT state FROM publish_guardrails WHERE subject = ?", (HASH,)).fetchone()[0]


# --- the overdue function is the imported one ---------------------------------

def test_the_overdue_function_is_the_one_the_data_jobs_call():
    assert auto.quarantine is guardrails.quarantine
    assert publish.guardrails.quarantine is auto.quarantine


def test_the_clock_is_read_from_quarantine_and_not_re_derived(conn, monkeypatch):
    other = "9" * 32
    row = {"check_name": "vehicle_name", "subject": other, "label": "Some Realty $80M",
           "age_hours": 1.0, "grace_hours": 192}
    report = {"held": [row], "live": [], "aggregate": [], "overdue": [row]}
    monkeypatch.setattr(auto, "quarantine", lambda c, write=True: report)
    found = {f["key"]: f for f in auto.list_findings(conn)}
    # One hour old is nowhere near 192: only quarantine's own verdict says overdue.
    assert found[f"vehicle_name/{other}"]["clock"] == "overdue"


def test_a_finding_within_a_day_of_its_deadline_is_due_and_a_fresh_one_is_not(conn, monkeypatch):
    rows = [{"check_name": "amount", "subject": "a" * 32, "label": "due", "age_hours": 50.0, "grace_hours": 72},
            {"check_name": "amount", "subject": "b" * 32, "label": "fresh", "age_hours": 5.0, "grace_hours": 72}]
    monkeypatch.setattr(auto, "quarantine", lambda c, write=True: {
        "held": [], "live": rows, "aggregate": [], "overdue": []})
    found = {f["key"]: f["clock"] for f in auto.list_findings(conn)}
    assert found["amount/" + "a" * 32] == "due"
    assert "amount/" + "b" * 32 not in found


def test_listing_never_writes_the_ledger(conn, monkeypatch):
    seen = {}
    real = guardrails.quarantine
    monkeypatch.setattr(auto, "quarantine", lambda c, write=True: seen.update(write=write) or real(c, write=write))
    auto.list_findings(conn)
    assert seen == {"write": False}


# --- agreement ----------------------------------------------------------------

def test_agreement_queues_one_apply_and_writes_nothing_itself(conn, tmp_path, stats):
    plan, state, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()})
    item = plan["items"][0]
    assert item["outcome"] == auto.AGREE and item["enqueue"] is True
    assert _ledger(conn) == "open", "this job never writes the ledger; the queue does"
    spec = json.loads(Path(tmp_path / "specs").glob("*.json").__next__().read_text())
    assert spec["status"] == "agree-dry-run"

    gh = Gh()
    assert auto.enqueue_agreed(plan, gh=gh, out=lambda *_: None) == 1
    (dispatch,) = gh.dispatches()
    assert dispatch[:4] == ["workflow", "run", "drain-writers.yml", "-f"]
    assert "enqueue=adjudicate-rows.yml" in dispatch
    inputs = json.loads(next(a for a in dispatch if a.startswith("inputs_json=")).split("=", 1)[1])
    assert inputs["dry_run"] == "false" and inputs["from_spec"].endswith(f"amount-{HASH}.json")
    assert set(inputs) == {"from_spec", "dry_run", "reason"}, "from_spec asks no referee"


def test_agreement_applies_once(conn, tmp_path, stats):
    plan, state, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()})
    assert len(call.calls) == 2
    # Six hours later the apply is still in the queue and the finding still open.
    later = NOW + dt.timedelta(hours=6)
    again = auto.run(conn, live=True, state=state, now=later, call=call,
                     fetch=lambda u: ARTICLE, wayback=lambda u: None,
                     spec_dir=tmp_path / "specs", out=lambda *_: None)
    assert again["items"][0]["outcome"] == auto.QUEUED
    assert len(call.calls) == 2, "an agreement on file is never paid for twice"
    gh = Gh()
    assert auto.enqueue_agreed(again, gh=gh, out=lambda *_: None) == 0
    assert gh.dispatches() == []


# --- disagreement: THE guard --------------------------------------------------

def test_disagreement_applies_nothing_and_lands_in_the_issue(conn, tmp_path, stats):
    plan, state, _ = _run(conn, tmp_path, stats,
                          {A: _money(amount=59_000_000), B: _money(amount=11_000_000)})
    item = plan["items"][0]
    assert item["outcome"] == auto.DISAGREE and item["enqueue"] is False
    gh = Gh()
    assert auto.enqueue_agreed(plan, gh=gh, out=lambda *_: None) == 0
    assert gh.dispatches() == [], "a disagreement queues nothing"
    assert _ledger(conn) == "open"

    assert auto.sync_issue(auto.issue_lines(plan), gh=gh, out=lambda *_: None) == "created"
    body = gh.issues[0]["body"]
    assert auto.ISSUE_MARKER in body and f"amount/{HASH}" in body
    assert "corrected_amount=59000000" in body and "corrected_amount=11000000" in body
    assert A in body and B in body


def test_an_item_that_is_not_an_agreement_is_never_enqueued_even_if_flagged(conn, tmp_path, stats):
    """Belt and braces: the enqueue reads the OUTCOME, not only the flag."""
    plan, _, _ = _run(conn, tmp_path, stats, {A: _money("accept"), B: _money("reject")})
    plan["items"][0]["enqueue"] = True
    gh = Gh()
    assert auto.enqueue_agreed(plan, gh=gh, out=lambda *_: None) == 0
    assert gh.dispatches() == []


def test_a_disagreement_on_file_is_not_paid_for_again(conn, tmp_path, stats):
    _, state, call = _run(conn, tmp_path, stats, {A: _money("accept"), B: _money("reject")})
    plan, _, call2 = _run(conn, tmp_path, stats, {A: _money(), B: _money()}, state=state)
    assert call2.calls == [] and plan["items"][0]["outcome"] == auto.DISAGREE


# --- a parse failure is not a "no" --------------------------------------------

TRUNCATED = '```json\n{"amount_stated_for_this_event": true, "recommended": "rej'


def test_a_truncated_fenced_answer_is_a_parse_failure_and_never_a_rejection(conn, tmp_path, stats):
    plan, state, _ = _run(conn, tmp_path, stats, {A: _money("reject"), B: TRUNCATED})
    item = plan["items"][0]
    assert item["outcome"] == auto.UNDECIDED and item["enqueue"] is False
    assert plan["counts"][auto.R_PARSE] == 1 and plan["counts"][auto.R_VERDICT] == 1
    gh = Gh()
    assert auto.enqueue_agreed(plan, gh=gh, out=lambda *_: None) == 0 and gh.dispatches() == []
    assert _ledger(conn) == "open"
    # Not a human's problem yet either: it is asked again, up to MAX_ASKS.
    assert auto.issue_lines(plan) == []
    assert state["keys"][f"amount/{HASH}"]["asks"] == 1


def test_two_parse_failures_are_not_an_agreement_to_reject(conn, tmp_path, stats):
    plan, _, _ = _run(conn, tmp_path, stats, {A: TRUNCATED, B: "I cannot answer in JSON."})
    assert plan["items"][0]["outcome"] == auto.UNDECIDED
    assert plan["counts"][auto.R_PARSE] == 2 and plan["counts"][auto.R_VERDICT] == 0


def test_answers_are_counted_apart():
    assert auto.classify_answer(None, adj.parse_verdict) == auto.R_NO_ANSWER
    assert auto.classify_answer(TRUNCATED, adj.parse_verdict) == auto.R_PARSE
    assert auto.classify_answer(json.dumps(_money(confidence=10)), adj.parse_verdict) == auto.R_LOW_CONF
    fenced = "```json\n" + json.dumps(_money("reject")) + "\n```"
    assert auto.classify_answer(fenced, adj.parse_verdict) == auto.R_VERDICT


def test_an_undecided_key_reaches_the_issue_after_max_asks(conn, tmp_path, stats):
    state = auto.load_state(tmp_path / "state.json")
    for _ in range(auto.MAX_ASKS):
        plan, state, _ = _run(conn, tmp_path, stats, {A: TRUNCATED, B: TRUNCATED}, state=state)
    plan, state, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()}, state=state)
    assert call.calls == [], "not asked a fourth time"
    assert plan["items"][0]["outcome"] == auto.UNDECIDED
    assert len(auto.issue_lines(plan)) == 1


# --- budget -------------------------------------------------------------------

def test_a_run_cap_already_spent_is_undecided_and_calls_nothing(conn, tmp_path, stats):
    plan, state, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()}, run_cap=0.0)
    item = plan["items"][0]
    assert call.calls == [] and item["outcome"] == auto.UNDECIDED and item.get("budget_stop")
    assert plan["spent_usd"] == 0
    assert "asks" not in state["keys"].get(f"amount/{HASH}", {}), "a budget stop is not an ask"
    assert auto.issue_lines(plan) == []


def test_the_month_cap_binds_across_runs(conn, tmp_path, stats):
    state = auto.load_state(tmp_path / "state.json")
    state["months"]["2026-09"] = 3.00
    plan, _, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()}, state=state)
    assert call.calls == [] and plan["items"][0]["outcome"] == auto.UNDECIDED
    # A new month is a new allowance.
    state["months"] = {"2026-08": 3.00}
    plan, _, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()}, state=state)
    assert len(call.calls) == 2 and plan["items"][0]["outcome"] == auto.AGREE


def test_the_months_switch_off_is_undecided(conn, tmp_path, stats, monkeypatch):
    monkeypatch.setenv("TIT_PAID_READS", "off")
    plan, _, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()})
    assert call.calls == [] and plan["items"][0]["outcome"] == auto.UNDECIDED


def test_a_stop_between_the_two_referees_is_undecided_not_a_verdict(conn, tmp_path, stats):
    # The first referee spends the whole ceiling; the second is never asked.
    plan, state, call = _run(conn, tmp_path, stats, {A: _money("reject"), B: _money("reject")},
                             run_cap=0.004)
    assert call.calls == [A]
    assert plan["items"][0]["outcome"] == auto.UNDECIDED
    assert plan["counts"][auto.R_BUDGET] == 1
    assert _ledger(conn) == "open"


def test_real_spend_is_recorded_against_the_month(conn, tmp_path, stats):
    plan, state, _ = _run(conn, tmp_path, stats, {A: _money(), B: _money()})
    assert plan["spent_usd"] == pytest.approx(0.008)
    assert state["months"]["2026-09"] == pytest.approx(0.008)
    assert "$0.0080 of a $0.25 run cap" in auto.summary(plan)


def test_an_unreadable_state_file_is_a_fault_not_a_zero(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(auto.Fault):
        auto.load_state(path)


# --- checks the referees cannot be asked about --------------------------------

@pytest.mark.parametrize("check", ["vehicle_name", "period_totals", "date_span"])
def test_a_check_without_referee_vocabulary_is_never_asked_or_accepted(conn, tmp_path, stats,
                                                                     monkeypatch, check):
    row = {"check_name": check, "subject": "c" * 32, "label": "Digital Realty $80M",
           "age_hours": 225.0, "grace_hours": 192}
    report = {"held": [row] if check == "vehicle_name" else [], "live": [],
              "aggregate": [] if check == "vehicle_name" else [row],
              "overdue": [row] if check == "vehicle_name" else []}
    monkeypatch.setattr(auto, "quarantine", lambda c, write=True: report)
    conn.execute("DELETE FROM publish_guardrails")
    plan, _, call = _run(conn, tmp_path, stats, {A: _money("accept"), B: _money("accept")})
    (item,) = plan["items"]
    assert call.calls == [] and item["outcome"] == auto.NOT_ASKABLE and not item["enqueue"]
    gh = Gh()
    assert auto.enqueue_agreed(plan, gh=gh, out=lambda *_: None) == 0 and gh.dispatches() == []
    assert len(auto.issue_lines(plan)) == 1 and check in auto.issue_lines(plan)[0]


def test_a_rejected_live_row_is_listed_for_retraction_and_never_asked(conn, tmp_path, stats):
    guardrails.review(conn, f"amount/{HASH}", "rejected", "a valuation, not a raise", "owner")
    conn.execute("UPDATE publish_guardrails SET first_seen = '2026-09-01T00:00:00+00:00'")
    conn.commit()
    plan, _, call = _run(conn, tmp_path, stats, {A: _money(), B: _money()})
    (item,) = plan["items"]
    assert call.calls == [] and item["outcome"] == auto.NOT_ASKABLE and item["clock"] == "overdue"
    assert "retract.py" in auto.issue_lines(plan)[0]


# --- dry run ------------------------------------------------------------------

def test_a_dry_run_calls_no_model_fetches_nothing_and_writes_nothing(conn, tmp_path, stats):
    def boom(*a, **k):
        raise AssertionError("a dry run must not reach the network")

    state = auto.load_state(tmp_path / "state.json")
    lines = []
    plan = auto.run(conn, live=False, state=state, now=NOW, call=boom, fetch=boom, wayback=boom,
                    spec_dir=tmp_path / "specs", out=lines.append)
    assert plan["items"][0]["outcome"] == "WOULD ASK"
    assert any("WOULD ASK" in line and A in line for line in lines)
    assert not (tmp_path / "specs").exists() and state["months"] == {} and state["keys"] == {}
    assert auto.issue_lines(plan) == []


def test_acting_on_a_dry_plan_touches_nothing(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"live": False, "items": []}))
    monkeypatch.setattr(auto, "run_gh", lambda *a, **k: pytest.fail("gh was called on a dry plan"))
    assert auto.main(["--act", str(plan)]) == 0


# --- the one issue ------------------------------------------------------------

def test_the_issue_is_created_once_updated_in_place_and_closed_when_empty():
    gh = Gh()
    assert auto.sync_issue([], gh=gh) == "none-needed" and gh.issues == []
    assert auto.sync_issue(["- one"], gh=gh) == "created"
    assert auto.sync_issue(["- one"], gh=gh) == "unchanged"
    assert auto.sync_issue(["- one", "- two"], gh=gh) == "updated"
    assert len(gh.issues) == 1 and "- two" in gh.issues[0]["body"]
    assert auto.sync_issue([], gh=gh) == "closed" and gh.issues[0]["state"] == "CLOSED"
    assert auto.sync_issue([], gh=gh) == "already-closed"
    assert auto.sync_issue(["- three"], gh=gh) == "reopened"
    assert len(gh.issues) == 1 and gh.issues[0]["state"] == "OPEN"


def test_an_issue_without_the_marker_is_not_ours():
    gh = Gh([{"number": 7, "state": "OPEN", "body": "someone else's issue with the same label"}])
    assert auto.sync_issue(["- one"], gh=gh) == "created"
    assert gh.issues[0]["body"].startswith("someone else")


def test_a_gh_failure_is_a_fault_and_exits_non_zero(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"live": True, "items": [
        {"key": "amount/x", "outcome": auto.AGREE, "enqueue": True, "spec": "analysis/adjudications/s.json"}]}))

    def refuse(args, *, stdin=None):
        raise auto.Fault("HTTP 403")

    monkeypatch.setattr(auto, "run_gh", refuse)
    monkeypatch.setattr(auto, "enqueue_agreed", lambda p, **k: refuse([]))
    assert auto.main(["--act", str(plan)]) == 1


def test_an_unreadable_ledger_is_a_fault(tmp_path):
    import sqlite3
    empty = sqlite3.connect(tmp_path / "empty.db")
    with pytest.raises(auto.Fault):
        auto.list_findings(empty)


# --- the workflow -------------------------------------------------------------

def _wf():
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_schedule_is_six_hourly_at_an_odd_minute_and_a_dispatch_defaults_to_dry():
    wf = _wf()
    on = wf.get("on") or wf.get(True)
    (cron,) = [s["cron"] for s in on["schedule"]]
    minute, hour = cron.split()[:2]
    assert hour == "*/6" and int(minute) % 2 == 1
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert on["workflow_dispatch"]["inputs"]["run_cap"]["default"] == f"{auto.RUN_CAP_USD:.2f}"
    assert on["workflow_dispatch"]["inputs"]["month_cap"]["default"] == f"{auto.MONTH_CAP_USD:.2f}"


def test_the_workflow_is_not_a_database_writer():
    text = WORKFLOW.read_text()
    wf = _wf()
    assert wf["concurrency"]["group"] != "talent-collect"
    runs = "\n".join(s.get("run", "") for s in wf["jobs"]["adjudicate"]["steps"])
    assert "talent_intel.db" not in runs and "merge_db" not in runs
    assert "--apply" not in runs
    assert "\u2014" not in text and "\u2013" not in text


def test_only_the_live_path_holds_the_model_key():
    for step in _wf()["jobs"]["adjudicate"]["steps"]:
        if "OPENROUTER_API_KEY" in (step.get("env") or {}):
            assert "dry_run == false" in step["if"] and "schedule" in step["if"]


def test_no_deepseek_referee():
    assert not [m for m in adj.REFEREES if "deepseek" in m.lower()]

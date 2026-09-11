"""Two referees between the collector's dry run and the writer queue.

Every test is offline: the collector is a stub handed in through `run`, the
referees through `call`, the document through `fetch`/`wayback`/`reader`, and
the queue and the specs live under tmp_path. Nothing stubs a real module into
sys.modules (CLAUDE.md, "Test gotcha").
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import fill_landmarks as fl
import run_collect
from collectors import primary_chase
from pipeline import classify

A, B = fl.REFEREES
DATA = {"amount_tolerance": 0.08, "window_days": 45}
ENTRY = {
    "id": "2025q1-acme-3b", "quarter": "2025Q1", "company": "Acme",
    "event_date": "2025-03-03", "amount_usd": 3_000_000_000,
    "amount_text": "$3 billion Series E", "currency": "USD", "country": "US",
    "source_url": "https://acme.example/news/series-e",
    "source_kind": "company_announcement", "added_on": "2026-08-04",
}
ROW = {
    "company": "Acme", "company_key": "acme", "pillar": "company_development",
    "headline": "Acme raises $3 billion Series E", "summary": "s",
    "funding_amount": "$3 billion", "funding_amount_usd": 3_000_000_000,
    "money_basis": "company_raise", "published_date": "2025-03-03",
    "source_url": ENTRY["source_url"], "source_name": "acme.example",
    "collector": "primary_chase", "country": "US",
}
# Long enough to clear the evidence floor: a document, not a stub.
DOCUMENT = ("Acme raises $3 billion Series E at a $61.5 billion post-money "
            "valuation, the company said today. ") * 12
assert len(DOCUMENT) >= fl.adj.EVIDENCE_MIN_CHARS


@pytest.fixture
def stats():
    saved = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(saved)


def _verdict(recommended="accept", matches=True, basis="company_raise", confidence=95):
    return {"matches_landmark": matches, "money_basis": basis,
            "recommended": recommended, "confidence": confidence,
            "reasoning": "'Acme raises $3 billion Series E'"}


def _referee(answers: dict, cost=0.004, stats=None):
    """A `call` that answers per model and meters like the real door does."""
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append(model)
        if stats is not None:
            stats["usd"] = float(stats.get("usd", 0.0)) + cost
        answer = answers[model]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    call.calls = calls
    return call


def _run(tmp_path, call, *, row=ROW, entry=ENTRY, stats=None, fetch=None,
         reader=None):
    return fl.adjudicate(
        entry, row, DATA,
        start_usd=float((stats or classify.STATS).get("usd", 0.0)),
        call=call, fetch=fetch or (lambda url: DOCUMENT),
        wayback=lambda url: None, reader=reader or (lambda url: None),
        spec_dir=tmp_path / "specs")


def _spec(tmp_path):
    return json.loads(next((tmp_path / "specs").glob("*.json")).read_text())


# --- the decision rule ---------------------------------------------------------

def test_both_accepting_and_matching_queues():
    assert fl.decide({A: _verdict(), B: _verdict()}) == fl.QUEUE


def test_one_reject_is_a_disagreement_not_a_queue():
    assert fl.decide({A: _verdict(), B: _verdict("reject", matches=False)}) == fl.DISAGREE
    assert fl.decide({A: _verdict("reject"), B: _verdict()}) == fl.DISAGREE


def test_both_rejecting_is_a_reject():
    assert fl.decide({A: _verdict("reject", matches=False),
                      B: _verdict("reject", matches=False)}) == fl.REJECT


def test_an_accept_that_does_not_match_the_landmark_does_not_queue():
    # A correct row for a DIFFERENT round is still not this landmark.
    assert fl.decide({A: _verdict(matches=False), B: _verdict()}) == fl.DISAGREE
    assert fl.decide({A: _verdict(matches=False), B: _verdict(matches=False)}) == fl.REJECT


def test_a_missing_verdict_is_unknown_never_a_tie_break():
    assert fl.decide({A: _verdict(), B: None}) == fl.UNKNOWN
    assert fl.decide({A: None, B: None}) == fl.UNKNOWN


def test_an_edit_answer_is_a_reject_and_says_so():
    parsed = fl.parse_verdict(json.dumps({**_verdict("edit"), "corrected_amount": 1}))
    assert parsed["recommended"] == "reject"
    assert parsed["recommended_raw"] == "edit"


def test_a_verdict_without_the_match_field_is_not_a_verdict():
    bare = {k: v for k, v in _verdict().items() if k != "matches_landmark"}
    assert fl.parse_verdict(json.dumps(bare)) is None
    assert fl.parse_verdict(json.dumps({**_verdict(), "matches_landmark": "yes"})) is None
    assert fl.parse_verdict("not json at all") is None


# --- one landmark end to end ---------------------------------------------------

def test_two_accepts_queue_and_write_both_verdicts(tmp_path, stats):
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    status, path, cost = _run(tmp_path, call, stats=stats)
    assert status == fl.QUEUE
    assert call.calls == [A, B]
    assert cost == pytest.approx(0.008)
    spec = _spec(tmp_path)
    assert spec["status"] == fl.QUEUE
    assert set(spec["verdicts"]) == {A, B}
    assert spec["landmark"]["id"] == ENTRY["id"]
    assert spec["row"]["funding_amount_usd"] == 3_000_000_000
    assert spec["check_verdict"] == "HELD"
    assert spec["cost_usd"] == pytest.approx(0.008)
    assert path.name == f"{path.name[:10]}-landmark-{ENTRY['id']}.json"


def test_any_reject_leaves_the_spec_and_queues_nothing(tmp_path, stats):
    call = _referee({A: _verdict(), B: _verdict("reject", matches=False)}, stats=stats)
    status, _path, _cost = _run(tmp_path, call, stats=stats)
    assert status == fl.DISAGREE
    spec = _spec(tmp_path)
    assert spec["status"] == fl.DISAGREE
    assert spec["verdicts"][B]["recommended"] == "reject"


def test_an_unreadable_document_is_unknown_and_asks_no_referee(tmp_path, stats):
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    status, _path, cost = _run(tmp_path, call, stats=stats, fetch=lambda url: "")
    assert status == fl.UNKNOWN
    assert call.calls == [], "no evidence, no question, no spend"
    assert cost == 0.0
    spec = _spec(tmp_path)
    assert spec["status"] == fl.UNKNOWN
    assert "readable" in spec["why"]


def test_a_thin_page_falls_back_to_the_collectors_own_read(tmp_path, stats):
    # openai.com serves a shell to a page-stripper and the announcement to the
    # collector's reader; the row came out of the latter.
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    reader = lambda url: {"raw_text": DOCUMENT, "discovery_url": "https://mirror/" + url}  # noqa: E731
    status, _path, _cost = _run(tmp_path, call, stats=stats, fetch=lambda url: "x" * 100,
                                reader=reader)
    assert status == fl.QUEUE
    assert _spec(tmp_path)["evidence_url"].startswith("https://mirror/")


def test_a_thin_collector_read_is_still_unknown(tmp_path, stats):
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    reader = lambda url: {"raw_text": "Acme announces $5B Series H raise", "discovery_url": url}  # noqa: E731
    status, _path, _cost = _run(tmp_path, call, stats=stats, fetch=lambda url: "",
                                reader=reader)
    assert status == fl.UNKNOWN
    assert call.calls == []


def test_no_row_from_the_collector_is_unknown(tmp_path, stats):
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    status, _path, _cost = _run(tmp_path, call, row=None, stats=stats)
    assert status == fl.UNKNOWN
    assert call.calls == []
    assert _spec(tmp_path)["row"] is None


def test_two_accepts_on_a_row_the_check_would_not_hold_do_not_queue(tmp_path, stats):
    # Both referees say yes to a row 20% off the landmark. The weekly check
    # would still read WRONG_AMOUNT, so queueing it fills nothing.
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    off = {**ROW, "funding_amount_usd": 3_600_000_000}
    status, _path, _cost = _run(tmp_path, call, row=off, stats=stats)
    assert status == fl.DISAGREE
    assert _spec(tmp_path)["check_verdict"] == "WRONG_AMOUNT"


def test_the_run_ceiling_stops_the_second_referee_and_decides_nothing(tmp_path, stats, monkeypatch):
    monkeypatch.setattr(fl, "RUN_CEILING_USD", 0.004)
    call = _referee({A: _verdict(), B: _verdict()}, cost=0.004, stats=stats)
    status, _path, _cost = _run(tmp_path, call, stats=stats)
    assert status == fl.UNKNOWN
    assert call.calls == [A], "the ceiling is read before every request"


def test_the_months_switch_is_read_before_any_request(tmp_path, stats, monkeypatch):
    monkeypatch.setenv("TIT_PAID_READS", "off")
    call = _referee({A: _verdict(), B: _verdict()}, stats=stats)
    status, _path, _cost = _run(tmp_path, call, stats=stats)
    assert status == fl.UNKNOWN
    assert call.calls == []


def test_a_throttled_first_attempt_is_retried_outside_the_call(tmp_path, stats):
    answers = {A: [classify.Throttled("busy"), _verdict()], B: _verdict()}
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append(model)
        stats["usd"] = float(stats.get("usd", 0.0)) + 0.004
        answer = answers[model]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)

    status, _path, _cost = _run(tmp_path, call, stats=stats)
    assert status == fl.QUEUE
    assert calls == [A, A, B]


def test_the_retry_never_lives_inside_the_paid_callable():
    import inspect
    src = inspect.getsource(fl.ask_referee)
    assert "for _attempt in range(attempts)" in src
    assert "_gate(start_usd)" in src
    assert src.index("_gate(start_usd)") < src.index("call(model")


# --- the collector, in dry run, with its rows captured -------------------------

@dataclass
class _Signal:
    company: str
    funding_amount_usd: int
    source_url: str
    pillar: str = "company_development"
    published_date: str = "2025-03-03"
    headline: str = "Acme raises $3 billion Series E"


def test_extract_rows_points_the_collector_at_a_temporary_work_list():
    seen = {}

    def run(*, dry_run, offline, run_index, limit, source):
        assert dry_run is True and offline is False and source == "primary_chase"
        leads = primary_chase.load_worklist()
        seen["leads"] = leads
        run_collect._print_signal(_Signal("Acme", 3_000_000_000, leads[0]["url"]))
        return 0

    before_default = primary_chase.load_worklist.__defaults__
    before_print = run_collect._print_signal
    rows = fl.extract_rows([ENTRY], run=run)
    assert seen["leads"] == [{"url": ENTRY["source_url"], "archive_fallback": True}]
    assert rows == [{"company": "Acme", "funding_amount_usd": 3_000_000_000,
                     "source_url": ENTRY["source_url"], "pillar": "company_development",
                     "published_date": "2025-03-03",
                     "headline": "Acme raises $3 billion Series E"}]
    assert primary_chase.load_worklist.__defaults__ == before_default
    assert run_collect._print_signal is before_print


def test_match_rows_uses_the_checks_own_matcher():
    rows = [ROW, {**ROW, "company": "Other Co", "company_key": "other co"}]
    assert fl.match_rows([ENTRY], rows, DATA) == {ENTRY["id"]: ROW}
    far = {**ROW, "published_date": "2025-08-01"}
    assert fl.match_rows([ENTRY], [far], DATA) == {ENTRY["id"]: None}


def test_standing_gaps_are_the_unheld_entries_largest_first():
    small = {**ENTRY, "id": "2025q1-tiny-100m", "company": "Tiny", "amount_usd": 100_000_000}
    held = {**ENTRY, "id": "2025q1-held-3b"}
    data = {**DATA, "entries": [small, held]}
    gaps = fl.standing_gaps(data, [ROW])
    assert [g["id"] for g in gaps] == ["2025q1-tiny-100m"]
    gaps = fl.standing_gaps(data, [])
    assert [g["id"] for g in gaps] == ["2025q1-held-3b", "2025q1-tiny-100m"]


# --- queueing, through the work list and the writer queue ----------------------

def test_the_work_list_carries_urls_and_nothing_that_reaches_the_database(tmp_path):
    path = tmp_path / "worklist.json"
    fl.write_worklist([ENTRY], path)
    body = json.loads(path.read_text())
    assert [lead["url"] for lead in body["leads"]] == [ENTRY["source_url"]]
    for lead in body["leads"]:
        assert set(lead) == {"url", "archive_fallback", "note"}
        assert "3" not in lead["note"].replace(ENTRY["id"], "")
    assert primary_chase.load_worklist(str(path)) == [
        {"url": ENTRY["source_url"], "archive_fallback": True}]


def test_one_ticket_is_enqueued_and_reused(tmp_path):
    queue_path = tmp_path / "queue.json"
    first = fl.enqueue_ticket("why", "test", queue_path)
    assert first["workflow"] == "collect.yml"
    assert first["inputs"] == {"source": "primary_chase"}
    assert first["state"] == "queued"
    second = fl.enqueue_ticket("again", "test", queue_path)
    assert second["id"] == first["id"], "one pass reads the whole list; one ticket"
    tickets = json.loads(queue_path.read_text())["tickets"]
    assert [t["id"] for t in tickets] == [first["id"]]


def test_queued_specs_are_marked_with_their_ticket(tmp_path):
    spec_dir = tmp_path / "specs"
    fl.write_spec(ENTRY, fl.QUEUE, {}, ROW, {"cost_usd": 0.0}, spec_dir)
    assert fl.accepted_from_specs([ENTRY], spec_dir) == [ENTRY]
    fl.mark_queued([ENTRY], {"id": "t1", "workflow": "collect.yml",
                             "inputs": {"source": "primary_chase"}}, spec_dir)
    spec = json.loads(fl.spec_path(ENTRY["id"], spec_dir).read_text())
    assert spec["status"] == "queued" and spec["ticket"]["id"] == "t1"
    assert fl.accepted_from_specs([ENTRY], spec_dir) == [ENTRY]
    fl.write_spec(ENTRY, fl.DISAGREE, {}, ROW, {"cost_usd": 0.0}, spec_dir)
    assert fl.accepted_from_specs([ENTRY], spec_dir) == []


def test_the_cli_refuses_to_run_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert fl.main([]) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_a_spec_never_carries_an_em_or_en_dash(tmp_path):
    spec_dir = tmp_path / "specs"
    verdicts = {A: {**_verdict(), "reasoning": "'new funding\u2014$40B at a $300B valuation'"},
                B: {**_verdict(), "reasoning": "2025\u20132026"}}
    fl.write_spec(ENTRY, fl.QUEUE, verdicts, ROW, {"cost_usd": 0.0}, spec_dir)
    text = fl.spec_path(ENTRY["id"], spec_dir).read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text
    assert "new funding-$40B" in text

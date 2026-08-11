"""The batch read-through path: half price, 24 hours late, and off by default.

OpenRouter's batch API is asynchronous with a 24-hour completion window, so
turning it on breaks same-run publishing: a record's interpretation is submitted
by one run and collected by a later one. These tests pin the three things that
make that safe to ship as an option rather than a default — the flag really is
off, a queued record is deferred rather than half-stored, and a rehearsal
neither queues nor spends.
"""

from __future__ import annotations

import json

import pytest

import run_collect
from pipeline import classify, prompts

from tests.test_readthrough_split import EXTRACTED, GOOD, RAW, answer


@pytest.fixture(autouse=True)
def isolate_stats():
    """classify.STATS is module state. Autouse, because one test forgetting to
    restore it makes the next test's counters lie — which is exactly how these
    two were caught."""
    before = dict(classify.STATS)
    yield
    classify.STATS.clear()
    classify.STATS.update(before)


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """A batch spool in a temp directory, with the flag on."""
    monkeypatch.setattr(classify, "READ_BATCH_DIR", str(tmp_path / "read_batch"))
    monkeypatch.setattr(classify, "DRY_RUN", False)
    monkeypatch.setenv("TIT_READ_BATCH", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "0" * 48)
    return tmp_path / "read_batch"


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.headers: dict = {}

    def json(self):
        return self._payload


def batch_reply(monkeypatch, *, get=None, post=None, seen=None):
    class _Requests:
        RequestException = classify.requests.RequestException

        @staticmethod
        def get(url, headers=None, timeout=None):
            if seen is not None:
                seen.append(("GET", url))
            return _Resp(get or {})

        @staticmethod
        def post(url, headers=None, data=None, json=None, timeout=None):
            if seen is not None:
                seen.append(("POST", url, data))
            return _Resp(post or {})

    monkeypatch.setattr(classify, "requests", _Requests)


# --- the flag ----------------------------------------------------------------

def test_the_batch_path_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TIT_READ_BATCH", raising=False)
    assert not classify.read_batch_enabled()


def test_the_flag_takes_the_usual_truthy_spellings(monkeypatch):
    for value in ("1", "true", "yes", "on", "ON"):
        monkeypatch.setenv("TIT_READ_BATCH", value)
        assert classify.read_batch_enabled(), value
    for value in ("0", "off", "no", ""):
        monkeypatch.setenv("TIT_READ_BATCH", value)
        assert not classify.read_batch_enabled(), value


def test_the_synchronous_path_spends_nothing_on_the_spool(monkeypatch):
    """With the flag off, the batch code is not consulted at all."""
    monkeypatch.delenv("TIT_READ_BATCH", raising=False)

    def fake(model, system, user, **kwargs):
        return answer(GOOD)

    monkeypatch.setattr(classify, "_call", fake)
    monkeypatch.setattr(classify, "batch_take",
                        lambda p: pytest.fail("spool touched on the sync path"))
    assert classify.interpret(EXTRACTED, RAW) == GOOD


# --- queueing ----------------------------------------------------------------

def test_a_queued_interpretation_defers_the_record_and_stores_nothing(
        spool, monkeypatch):
    batch_reply(monkeypatch, seen=(seen := []))
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "queued for the batch API" in str(caught.value)
    assert classify.STATS["read_queued"] == 1
    assert classify.STATS["read_calls"] == 0, "queueing must not buy a sync read"
    assert seen == [], "queueing is a file write, not a request"
    assert json.loads((spool / "pending.json").read_text())


def test_the_same_candidate_is_not_queued_twice(spool, monkeypatch):
    batch_reply(monkeypatch)
    for _ in range(3):
        with pytest.raises(classify.ReadThroughUnavailable):
            classify.interpret(EXTRACTED, RAW)
    assert len(json.loads((spool / "pending.json").read_text())) == 1
    assert classify.STATS["read_queued"] == 1


def test_the_key_belongs_to_the_exact_question():
    """A changed fact must not be answered by the old sentence."""
    one = classify.batch_key(prompts.build(EXTRACTED, RAW))
    two = classify.batch_key(prompts.build({**EXTRACTED, "city": "Dublin"}, RAW))
    assert one != two
    assert one == classify.batch_key(prompts.build(EXTRACTED, RAW))


# --- harvesting --------------------------------------------------------------

def completed(key: str, *, cost: float = 0.0011) -> dict:
    return {
        "id": "batch_123", "status": "completed",
        "request_counts": {"total": 1, "completed": 1, "failed": 0},
        "usage": {"prompt_tokens": 450, "completion_tokens": 60, "cost": cost},
        "results": [{
            "custom_id": key,
            "response": {"status_code": 200, "body": {"choices": [
                {"message": {"role": "assistant", "content": answer(GOOD)}}]}},
            "error": None,
        }],
    }


def test_a_harvested_answer_publishes_on_the_later_run(spool, monkeypatch):
    key = classify.batch_key(prompts.build(EXTRACTED, RAW))
    classify._batch_save("submitted.json", ["batch_123"])
    batch_reply(monkeypatch, get=completed(key))

    harvested, notes = classify.harvest_batches()
    assert harvested == 1 and any("completed" in n for n in notes)

    # The candidate comes round again (its URL was never marked seen) and this
    # time the answer is waiting.
    assert classify.interpret(EXTRACTED, RAW) == GOOD
    assert classify.STATS["read_served"] == 1
    assert classify.STATS["read_written"] == 1
    # Taken once. A second read re-queues rather than re-serving a stale answer.
    with pytest.raises(classify.ReadThroughUnavailable):
        classify.interpret(EXTRACTED, RAW)


def test_a_harvested_batch_reports_what_it_cost(spool, monkeypatch):
    """A batched month must still be measured. The cost lands on the run that
    HARVESTED it, which is asynchrony's own doing and worth knowing."""
    key = classify.batch_key(prompts.build(EXTRACTED, RAW))
    classify._batch_save("submitted.json", ["batch_123"])
    batch_reply(monkeypatch, get=completed(key, cost=0.0044))
    classify.harvest_batches()
    assert classify.STATS["usd"] == pytest.approx(0.0044)
    assert classify.STATS["prompt_tokens"] == 450


def test_a_batch_still_running_is_left_alone(spool, monkeypatch):
    classify._batch_save("submitted.json", ["batch_123"])
    batch_reply(monkeypatch, get={"id": "batch_123", "status": "in_progress"})
    harvested, notes = classify.harvest_batches()
    assert harvested == 0
    assert json.loads((spool / "submitted.json").read_text()) == ["batch_123"]
    assert any("nothing to collect yet" in n for n in notes)


def test_an_expired_batch_is_dropped_and_says_so(spool, monkeypatch):
    """Its candidates were never marked seen, so they come round and re-queue.
    Silently keeping a dead batch id forever is the other option, and it would
    make a broken batch look like a slow one."""
    classify._batch_save("submitted.json", ["batch_123"])
    batch_reply(monkeypatch, get={"id": "batch_123", "status": "expired"})
    harvested, notes = classify.harvest_batches()
    assert harvested == 0
    assert json.loads((spool / "submitted.json").read_text()) == []
    assert any("re-queued" in n for n in notes)


def test_an_unreachable_batch_is_retried_not_dropped(spool, monkeypatch):
    classify._batch_save("submitted.json", ["batch_123"])
    batch_reply(monkeypatch, get={}, seen=None)
    monkeypatch.setattr(classify.requests, "get",
                        staticmethod(lambda *a, **k: _Resp({}, status_code=503)))
    harvested, notes = classify.harvest_batches()
    assert harvested == 0
    assert json.loads((spool / "submitted.json").read_text()) == ["batch_123"]
    assert any("will retry" in n for n in notes)


# --- submitting --------------------------------------------------------------

def test_one_run_submits_one_batch(spool, monkeypatch):
    batch_reply(monkeypatch, post={"id": "batch_999", "status": "validating"},
                seen=(seen := []))
    for extracted in (EXTRACTED, {**EXTRACTED, "company": "Holobiome"}):
        with pytest.raises(classify.ReadThroughUnavailable):
            classify.interpret(extracted, RAW)

    sent, note = classify.submit_pending()
    assert sent == 2 and "batch_999" in note
    assert len([s for s in seen if s[0] == "POST"]) == 1
    assert json.loads((spool / "submitted.json").read_text()) == ["batch_999"]
    assert json.loads((spool / "pending.json").read_text()) == {}


def test_the_body_serialises_endpoint_and_model_before_requests(spool, monkeypatch):
    """OpenRouter stream-parses the body so it can accept very large arrays, and
    returns 400 if `requests` comes first. Key order is the contract."""
    batch_reply(monkeypatch, post={"id": "batch_999"}, seen=(seen := []))
    with pytest.raises(classify.ReadThroughUnavailable):
        classify.interpret(EXTRACTED, RAW)
    classify.submit_pending()

    body = next(s[2] for s in seen if s[0] == "POST")
    assert body.index('"endpoint"') < body.index('"requests"')
    assert body.index('"model"') < body.index('"requests"')
    parsed = json.loads(body)
    assert parsed["endpoint"] == "/v1/chat/completions"
    assert parsed["model"] == classify.READ_MODEL
    request = parsed["requests"][0]
    assert request["custom_id"].startswith("read-")
    assert request["body"]["max_tokens"] == classify.READ_MAX_TOKENS
    assert classify.SCHEMA_HINT not in request["body"]["messages"][1]["content"]


def test_a_refused_submission_keeps_the_queue(spool, monkeypatch):
    batch_reply(monkeypatch)
    monkeypatch.setattr(classify.requests, "post",
                        staticmethod(lambda *a, **k: _Resp({"error": "nope"}, 400)))
    with pytest.raises(classify.ReadThroughUnavailable):
        classify.interpret(EXTRACTED, RAW)
    sent, note = classify.submit_pending()
    assert sent == 0 and "still queued" in note
    assert len(json.loads((spool / "pending.json").read_text())) == 1


def test_nothing_is_submitted_when_nothing_was_queued(spool, monkeypatch):
    batch_reply(monkeypatch, seen=(seen := []))
    assert classify.submit_pending() == (0, "")
    assert seen == []


# --- rehearsals --------------------------------------------------------------

def test_a_dry_run_neither_queues_nor_submits_nor_harvests(spool, monkeypatch):
    monkeypatch.setattr(classify, "DRY_RUN", True)
    batch_reply(monkeypatch, seen=(seen := []))

    with pytest.raises(classify.ReadThroughUnavailable):
        classify.interpret(EXTRACTED, RAW)
    assert classify.harvest_batches() == (0, ["batch harvest skipped on a dry run"])
    assert classify.submit_pending() == (0, "batch submit skipped on a dry run")

    assert seen == []
    assert not spool.exists(), "a rehearsal must leave no queue behind"


# --- the wiring --------------------------------------------------------------

def test_run_collect_touches_the_batch_only_outside_the_candidate_loop():
    """The flag was allowed to add two calls, not to restructure the run."""
    import inspect

    src = inspect.getsource(run_collect.run)
    loop = src.index("for item in kept:")
    assert src.index("classify.harvest_batches()") < loop
    assert src.index("classify.submit_pending()") > loop
    assert src.count("classify.read_batch_enabled()") == 2


def test_the_latency_consequence_is_printed_not_buried():
    import inspect

    src = inspect.getsource(run_collect.run)
    assert "publish on a LATER run" in src
    assert "24h" in src


def test_a_dry_run_tells_the_batch_code_it_is_a_rehearsal():
    import inspect

    assert "classify.set_dry_run(dry_run)" in inspect.getsource(run_collect.run)

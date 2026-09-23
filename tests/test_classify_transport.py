"""A dropped connection to OpenRouter must defer the candidate, not kill the run.

On 2026-09-22 a `RemoteDisconnected('Remote end closed connection without
response')` inside `classify._call`'s `requests.post` propagated straight out
of `run_collect.py`, taking down the whole `google_news` collect step (exit 1)
mid-run, well before every other candidate in that batch was even looked at.
The retry loop right there already knows how to ride out a transient HTTP
status (429/500/502/503/504) — it does not know how to ride out getting no
response at all, which is exactly as transient and arguably more common on
shared hosting. This is the same lesson `collectors/http_retry.py` documents
for 429s, applied to the case where there is no status code to read.
"""
from __future__ import annotations

import pytest
import requests as real_requests

from pipeline import classify


@pytest.fixture
def stats():
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


class _Resp:
    status_code = 200
    text = ""
    headers: dict = {}

    def json(self):
        return {"choices": [{"message": {"content": "{}"},
                             "finish_reason": "stop"}],
                "provider": "Google AI Studio",
                "usage": {}}


def _setup(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "0" * 48)
    monkeypatch.setattr(classify.time, "sleep", lambda _s: None)


def test_a_dropped_connection_is_retried_not_fatal(monkeypatch, stats):
    """A RemoteDisconnected on the first attempt must not escape _call raw —
    it must be retried like any other transient failure."""
    _setup(monkeypatch)
    calls = []

    class _Requests:
        exceptions = real_requests.exceptions

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise real_requests.exceptions.ConnectionError(
                    "Remote end closed connection without response")
            return _Resp()

    monkeypatch.setattr(classify, "requests", _Requests)
    content = classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)
    assert content == "{}"
    assert len(calls) == 2, "it must retry the dropped connection, not give up"


def test_a_connection_that_never_recovers_defers_the_candidate(monkeypatch, stats):
    """Exhausting every retry on a dead connection must raise the SAME
    Throttled run_collect already knows how to defer — never the raw
    requests exception, which run_collect does not catch and which crashes
    the whole collect step."""
    _setup(monkeypatch)

    class _Requests:
        exceptions = real_requests.exceptions

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            raise real_requests.exceptions.ConnectionError(
                "Remote end closed connection without response")

    monkeypatch.setattr(classify, "requests", _Requests)
    with pytest.raises(classify.Throttled):
        classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

"""EFTS returns a transient 500 sometimes, and it is EDGAR's server, not ours.

Measured live twice: 2026-08-19 (collect run 32307688627, cited in
collectors/sec_form_d.py's collect()) and 2026-09-03 (run 33696039069). Both
times the EXACT SAME query, replayed unmodified seconds to minutes later,
answered 200 with real hits (222, then 182). Before this fix,
collectors/sec_form_d.search() raised on the first non-2xx response and never
retried, so one EDGAR blip on page 0 was fatal to the whole call and
collect()'s page loop broke out on the first RequestException -- zeroing out
found/stored/dup/rejected/deferred for the entire day. That reads identically
to a genuinely quiet window in the health ledger and in ops_status, until
someone reads the log for the one line collect() already prints.

search() now retries a transient 5xx a bounded number of times before giving
up, the same split collectors/czechia_ares.py's _request() already makes: a
4xx is a real answer about OUR request (a malformed query, a revoked
credential) and is never retried; a 5xx is EDGAR's own server and gets a few
seconds and another try. These tests prove the retry actually fires, actually
gives up eventually, and never fires for a 4xx -- by mutation: deleting the
retry loop (collapsing it back to one bare `requests.get()` call) fails all
three.
"""
from __future__ import annotations

import requests
import pytest

from collectors import sec_form_d


class _Resp:
    """Enough of a requests.Response for search() to run against, offline."""

    def __init__(self, status_code: int, hits: list | None = None):
        self.status_code = status_code
        self._hits = hits or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return {"hits": {"hits": self._hits}}


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry ladder really does call time.sleep(); don't make the suite
    wait on it. REQUEST_DELAY and EFTS_RETRY_WAIT are both faked to 0."""
    monkeypatch.setattr(sec_form_d.time, "sleep", lambda seconds: None)


def test_a_transient_500_is_retried_and_recovers(monkeypatch):
    calls: list[int] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _Resp(500)
        return _Resp(200, hits=[{"_id": "0000000000-26-000001:primary_doc.xml"}])

    monkeypatch.setattr(sec_form_d.requests, "get", fake_get)

    hits = sec_form_d.search(days_back=5, page=0)

    assert hits == [{"_id": "0000000000-26-000001:primary_doc.xml"}]
    assert len(calls) == 2, (
        "a transient 500 must be retried once and then succeed, not be "
        f"treated as fatal on the first response; got {len(calls)} call(s)")


def test_a_persistent_500_gives_up_after_the_bounded_retry_count(monkeypatch):
    calls: list[int] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        return _Resp(500)

    monkeypatch.setattr(sec_form_d.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        sec_form_d.search(days_back=5, page=0)

    assert len(calls) == sec_form_d.EFTS_RETRIES, (
        f"must give up after exactly EFTS_RETRIES={sec_form_d.EFTS_RETRIES} "
        f"attempts -- not retry forever and not bail after one; got "
        f"{len(calls)} call(s)")


def test_a_4xx_is_a_real_answer_and_is_never_retried(monkeypatch):
    calls: list[int] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        return _Resp(404)

    monkeypatch.setattr(sec_form_d.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        sec_form_d.search(days_back=5, page=0)

    assert len(calls) == 1, (
        "a 4xx is a real answer about OUR request (a malformed query, a "
        "revoked credential) and retrying it the way a 5xx is retried would "
        f"hide that; got {len(calls)} call(s)")

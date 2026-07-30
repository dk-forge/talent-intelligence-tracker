"""What these two jobs do when the other end will not answer.

Both are armed on a timer now, which changes the cost of getting this wrong: a
hand-dispatched run that misreads a 429 is one bad afternoon, and a nightly one
is a policy. No network in this file — every response is injected.

The property under test is the same one in both halves: **a non-answer is not an
answer.** archive.org refusing to talk is not evidence that a document is
unarchived, and a publisher's 500 is not evidence that its article is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import archive_sources
import link_check
from pipeline import schema, source_links, store, validate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def stocked(tmp_path):
    conn = schema.connect(tmp_path / "links.db")
    for i, url in enumerate([
        "https://www.irishtimes.com/business/one/",
        "https://www.ctech.co.il/two/",
        "https://www.technode.com/three/",
    ]):
        store.store(conn, validate.build_signal(
            {"company": f"Company{i}", "pillar": "company_development",
             "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
             "confidence": "reported",
             "headline": f"Company{i} to create 300 new jobs in Dublin",
             "summary": f"Company{i} will add 300 roles in Dublin.",
             "talent_readthrough": "300 engineering roles in Dublin."},
            {"raw_text": f"Company{i} to create 300 new jobs in Dublin",
             "source_url": url, "source_name": "The Irish Times",
             "published_date": "2026-07-20"},
            "national_press"))
    conn.commit()
    yield conn
    conn.close()


# --- archive_sources: a throttled availability call is not a miss ----------

class _Resp:
    def __init__(self, status, payload=None, headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def close(self):
        """link_check streams and closes. Without this the probe raises,
        `_probe_once` swallows it, and every fixture reads as 'unreachable' —
        which is a fake that quietly tests nothing."""


class _AvailabilitySession:
    """Answers the availability API from a queue of statuses; SPN always saves."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.availability_calls = 0
        self.save_calls = 0

    def get(self, url, **kwargs):
        if url.startswith(archive_sources.AVAILABILITY):
            self.availability_calls += 1
            status = self.statuses.pop(0) if self.statuses else 200
            if status == 200:
                return _Resp(200, {"archived_snapshots": {}})
            return _Resp(status)
        self.save_calls += 1
        return _Resp(200, headers={"Content-Location": "/web/2026/x"})


@pytest.mark.parametrize("code", sorted(archive_sources.AVAILABILITY_UNKNOWN_CODES))
def test_a_throttled_availability_call_is_unknown_not_absent(code):
    """Measured 2026-07-30: archive.org/wayback/available answered 429 to the
    FIRST request of a measurement, and was still answering 429 twenty seconds
    later. Read as 'no snapshot', that invents a gap, spends the capture budget
    re-archiving documents Wayback already holds, and walks the attempt counter
    toward the terminal 'unavailable' state."""
    session = _AvailabilitySession([code])
    assert archive_sources.check_availability(
        "https://x.example/a", session) is archive_sources.RATE_LIMITED


def test_a_transport_failure_is_also_unknown():
    class _Broken:
        def get(self, *a, **k):
            raise OSError("connection reset")

    assert archive_sources.check_availability(
        "https://x.example/a", _Broken()) is archive_sources.RATE_LIMITED


def test_a_genuine_miss_is_still_a_miss():
    session = _AvailabilitySession([200])
    assert archive_sources.check_availability("https://x.example/a", session) is None


def test_an_unanswered_url_never_spends_a_capture_or_an_attempt(stocked):
    """The consequence that cannot be undone.

    `archive_candidates` drops an 'unavailable' URL forever, so five throttled
    nights would silently retire three capturable documents and only a hand
    written UPDATE could bring them back.
    """
    session = _AvailabilitySession([429, 429, 429])
    result = archive_sources.run(
        stocked, limit=10, collector=None, dry_run=False, spn_max=40,
        spn_gap=0, avail_gap=0, deadline=1e6, session=session,
        sleep=lambda _s: None, clock=lambda: 0.0)
    stocked.commit()

    assert result["unknown"] == 3
    assert result["archived"] == 0
    assert session.save_calls == 0, (
        "a capture was spent on a URL we learned nothing about")
    # Nothing recorded means nothing to un-record, and the gap is unchanged: the
    # next run simply asks again.
    assert stocked.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 0
    assert len(source_links.archive_candidates(stocked, limit=10)) == 3


def test_five_throttled_runs_cannot_retire_a_capturable_document(stocked):
    for _ in range(source_links.MAX_ARCHIVE_ATTEMPTS + 1):
        archive_sources.run(
            stocked, limit=10, collector=None, dry_run=False, spn_max=40,
            spn_gap=0, avail_gap=0, deadline=1e6,
            session=_AvailabilitySession([503, 503, 503]),
            sleep=lambda _s: None, clock=lambda: 0.0)
    stocked.commit()
    states = {r[0] for r in stocked.execute(
        "SELECT archive_state FROM source_links")}
    assert "unavailable" not in states
    assert len(source_links.archive_candidates(stocked, limit=10)) == 3


def test_a_throttled_run_is_reported_degraded_not_ok(stocked, capsys):
    """Before this, `throttled_out` only fired when Save Page Now was throttled
    too, so a run blinded in pass 1 reported `ok` with a healthy capture count
    beside it. That is the false-healthy shape this project keeps finding."""
    archive_sources.run(
        stocked, limit=10, collector=None, dry_run=False, spn_max=40,
        spn_gap=0, avail_gap=0, deadline=1e6,
        session=_AvailabilitySession([429, 429, 429]),
        sleep=lambda _s: None, clock=lambda: 0.0)
    stocked.commit()

    # main() is what writes the health row, so drive the decision the same way.
    result = {"archived": 0, "free_hits": 0, "pending": 0, "saves": 0,
              "throttled": 0, "unavailable": 0, "unknown": 3, "checked": 3}
    unknown, answered = result["unknown"], result["free_hits"] + result["pending"] + result["unknown"]
    assert bool(unknown) and unknown * 2 >= (answered or 1), (
        "a wholly unanswered free pass must not evaluate as healthy")


def test_the_free_pass_is_paced(stocked):
    """The free pass costs no money, which is not the same as being welcome at
    any rate we like."""
    slept = []
    archive_sources.run(
        stocked, limit=10, collector=None, dry_run=True, spn_max=0,
        spn_gap=0, avail_gap=0.5, deadline=1e6,
        session=_AvailabilitySession([200, 200, 200]),
        sleep=slept.append, clock=lambda: 0.0)
    # Three URLs means two waits: the first call needs no pause before it.
    assert slept == [0.5, 0.5]


def test_the_paced_free_pass_leaves_room_for_the_captures():
    """Pacing pass 1 must not quietly starve pass 2.

    The deadline stops the run cleanly between URLs, so an over-long free pass
    does not fail — it just means no capture is ever attempted, every miss is
    recorded 'pending', and the coverage line stops moving while every run stays
    green. Measured latency on the availability API is 0.2-1.0s per call, so a
    full-limit pass is budgeted at one second each plus the gap.
    """
    free_pass = archive_sources.DEFAULT_LIMIT * (
        archive_sources.DEFAULT_AVAIL_GAP + 1.0)
    captures = archive_sources.DEFAULT_SPN_MAX * archive_sources.DEFAULT_SPN_GAP
    assert free_pass + captures < archive_sources.DEFAULT_DEADLINE, (
        f"a full free pass ({free_pass:.0f}s) plus the capture budget "
        f"({captures:.0f}s) exceeds the {archive_sources.DEFAULT_DEADLINE}s "
        f"deadline, so pass 2 would never run and coverage would flatline "
        f"without a single red run")


# --- link_check: a 5xx is retried once, and is never rot -------------------

class _FlakySession:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get(self, url, **kwargs):
        if url.endswith("/robots.txt"):
            return _Resp(404, url=url)
        self.asked.append(url)
        status, final = self.answers[url].pop(0)
        if status == 0:
            raise OSError("connection reset")
        return _Resp(status, url=final)


def test_a_transient_500_is_retried_once_and_then_believed():
    """Shared hosting has bad afternoons; so does a runner's DNS. A single
    observation costs the URL its whole 30-day recheck window, so one retry buys
    a month of not-knowing back for one extra request."""
    url = "https://www.irishtimes.com/business/one/"
    session = _FlakySession({url: [(503, url), (200, url)]})
    status, final = link_check.probe(url, session, sleep=lambda _s: None)
    assert (status, final) == (200, url)
    assert session.asked == [url, url]


def test_a_dropped_connection_is_retried_once():
    url = "https://www.ctech.co.il/two/"
    session = _FlakySession({url: [(0, ""), (200, url)]})
    assert link_check.probe(url, session, sleep=lambda _s: None)[0] == 200


def test_a_404_is_an_answer_and_is_not_retried():
    url = "https://www.ctech.co.il/two/"
    session = _FlakySession({url: [(404, url)]})
    assert link_check.probe(url, session, sleep=lambda _s: None)[0] == 404
    assert session.asked == [url]


def test_a_429_is_not_retried_because_that_would_be_answering_slow_down_with_no():
    url = "https://www.technode.com/three/"
    session = _FlakySession({url: [(429, url)]})
    assert link_check.probe(url, session, sleep=lambda _s: None)[0] == 429
    assert session.asked == [url]
    assert link_check.classify(429, url, url)[0] == "walled"


def test_retrying_stops_after_one_extra_attempt():
    """Two retries would double this job's request count against every flaky
    host in the catalogue to refine a state that is not rot either way."""
    url = "https://www.irishtimes.com/business/one/"
    session = _FlakySession({url: [(503, url), (503, url)]})
    assert link_check.probe(url, session, sleep=lambda _s: None)[0] == 503
    assert session.asked == [url, url]


def test_a_persistent_5xx_is_recorded_as_error_and_never_as_rot(stocked, monkeypatch):
    monkeypatch.setattr(link_check, "robots_allows", lambda url, session=None: True)
    answers = {r["source_url"]: [(500, r["source_url"])] * 2
               for r in source_links.distinct_source_urls(stocked)}
    result = link_check.run(stocked, limit=10, collector=None, dry_run=False,
                            recheck_days=30, shuffle=False, pause=0,
                            session=_FlakySession(answers), sleep=lambda _s: None)
    stocked.commit()
    assert result["states"] == {"error": 3}
    assert result["rot"] == 0
    assert "error" not in source_links.ROT_STATES
    # And no signal was touched, which is the rule that outranks all of this.
    assert stocked.execute(
        "SELECT COUNT(*) FROM signals WHERE is_current = 0").fetchone()[0] == 0


# --- and it still costs nothing -------------------------------------------

def test_neither_fix_reached_for_a_model():
    for path in (ROOT / "link_check.py", ROOT / "archive_sources.py"):
        body = path.read_text()
        for forbidden in ("openrouter", "OPENROUTER_API_KEY", "classify_signal"):
            assert forbidden not in body, f"{path.name} reaches for a model"

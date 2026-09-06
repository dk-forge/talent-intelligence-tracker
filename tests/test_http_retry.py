"""A 429 keeps its slot, and running out of retries is REPORTED.

WHAT WAS WRONG. A sweep of every collector on 2026-09-06 found five answering
HTTP 429 by dropping the query, the page or the document with no counter, no
print and no health row:

  * `google_news` - `except requests.RequestException: continue`, once per
    edition, across ~40 editions. A throttled edition and an edition that
    genuinely had no news produced the same observation, and
    `store.report_health` graded the run on it.
  * `sec_edgar` - the same shape twice, per phrase and per document.
  * `sec_form_d` - the EFTS retry ladder was `resp.status_code < 500`, so a
    429 broke out on the FIRST attempt, and the per-filing XML fetch dropped
    silently.
  * `benchmark_chase` - the SEC leg returned `[]` with nothing printed.
  * `press_archive` - its CDX path says in as many words that a throttle "is
    NOT 'nothing archived'"; the sitemap, robots and head paths in the same
    file still read any non-200 as nothing there.

Every test here fails on the pre-`http_retry` tree.

THE TWO PROPERTIES. A 429-then-200 must KEEP the slot (retry, do not drop),
and an exhausted one must be REPORTED (recorded, never silent). And the wait
must be CAPPED: a server may legitimately answer `Retry-After: 3600`, and a
job with `timeout-minutes: 120` that sleeps an hour is killed with nothing
committed, which loses far more than the one slot.
"""
from __future__ import annotations

import pytest
import requests

from collectors import http_retry


class Resp:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
        self.text = "body"

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_ledger():
    http_retry.reset()
    yield
    http_retry.reset()


class TestTheSlotIsKept:
    def test_a_429_then_a_200_returns_the_200(self):
        answers = [Resp(429, {"Retry-After": "2"}), Resp(200)]
        slept = []
        got = http_retry.fetch("edition es-MX", lambda: answers.pop(0),
                               sleep=slept.append)
        assert got.status_code == 200
        assert slept == [2.0], "it must WAIT what it was told, then retry"
        assert http_retry.EXHAUSTED == [], "a slot that was read is not a loss"

    def test_a_503_is_treated_the_same_as_a_429(self):
        """A shared host answers 503 to the overload a limiter answers 429 to,
        and both clear by waiting."""
        answers = [Resp(503), Resp(200)]
        got = http_retry.fetch("page 0", lambda: answers.pop(0),
                               sleep=lambda _s: None)
        assert got.status_code == 200

    def test_a_tuple_returning_fetcher_is_understood(self):
        """Half this codebase fetches through `capped_fetch.capped_get`, which
        returns `(response, body)`."""
        answers = [(Resp(429), b""), (Resp(200), b"<rss/>")]
        resp, body = http_retry.fetch("feed", lambda: answers.pop(0),
                                      sleep=lambda _s: None)
        assert (resp.status_code, body) == (200, b"<rss/>")

    def test_a_404_is_not_a_rate_limit_and_is_handed_straight_back(self):
        calls = []

        def once():
            calls.append(1)
            return Resp(404)

        assert http_retry.fetch("x", once, sleep=lambda _s: None).status_code == 404
        assert len(calls) == 1, "a 404 must not be retried; it is an answer"

    def test_every_retried_response_is_closed(self):
        """A streamed response left open leaks a connection per attempt, and
        the retry path is where a run makes the most of them."""
        first, second = Resp(429), Resp(200)
        answers = [first, second]
        http_retry.fetch("x", lambda: answers.pop(0), sleep=lambda _s: None)
        assert first.closed


class TestExhaustionIsReportedNotSilent:
    def test_running_out_of_attempts_records_the_slot(self):
        with pytest.raises(http_retry.RateLimited):
            http_retry.fetch("google_news es-MX layoffs",
                             lambda: Resp(429), attempts=3,
                             sleep=lambda _s: None)
        assert [slot for slot, _d in http_retry.EXHAUSTED] == \
            ["google_news es-MX layoffs"]
        assert "429" in http_retry.EXHAUSTED[0][1]

    def test_the_exception_is_a_requests_exception_ON_PURPOSE(self):
        """The per-slot `except requests.RequestException: continue` handlers
        are CORRECT -- one throttled edition must not lose the other forty.
        Making this escape them would trade a silent drop for a dead run,
        which is a worse deal, not a stricter one. The drop is in the ledger
        before it is raised; that is what changed."""
        assert issubclass(http_retry.RateLimited, requests.RequestException)
        try:
            http_retry.fetch("slot", lambda: Resp(429), attempts=1,
                             sleep=lambda _s: None)
        except requests.RequestException:
            caught = True
        assert caught
        assert http_retry.EXHAUSTED, "caught by the old handler, NOT silent"

    def test_the_detail_line_names_the_slots_and_not_only_a_count(self):
        for n in range(3):
            with pytest.raises(http_retry.RateLimited):
                http_retry.fetch(f"edition-{n}", lambda: Resp(429),
                                 attempts=1, sleep=lambda _s: None)
        line = http_retry.exhausted_detail()
        assert "3 slice(s) NOT read" in line
        assert "edition-0" in line and "edition-2" in line

    def test_the_detail_line_is_bounded(self):
        """A badly throttled run must not write a health `detail` longer than
        the column."""
        for n in range(40):
            with pytest.raises(http_retry.RateLimited):
                http_retry.fetch(f"edition-{n}", lambda: Resp(429),
                                 attempts=1, sleep=lambda _s: None)
        line = http_retry.exhausted_detail()
        assert "+34 more" in line and len(line) < 240

    def test_nothing_throttled_says_nothing(self):
        assert http_retry.exhausted_detail() == ""

    def test_a_caller_that_handles_its_own_429_can_still_record_it(self):
        """`link_check` is explicit that retrying a 429 answers "slow down"
        with "no". Such a caller still must not report the slot as read."""
        http_retry.record_throttled("press_archive sitemap Publisher", 429)
        assert http_retry.exhausted_detail().startswith("1 slice(s) NOT read")


class TestTheWaitIsCapped:
    def test_an_hour_long_retry_after_is_capped(self):
        """`Retry-After: 3600` inside a job with `timeout-minutes: 120` is how
        a run gets killed with nothing committed. Come back next run instead."""
        assert http_retry.retry_after_seconds({"Retry-After": "3600"}, 0) == \
            http_retry.RETRY_AFTER_CAP_SECONDS

    def test_an_unparseable_retry_after_falls_back_to_backoff_not_to_zero(self):
        """An HTTP-date form is not parsed (it would need a trusted clock).
        Falling back to zero would answer "slow down" by hammering."""
        wait = http_retry.retry_after_seconds(
            {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 0)
        assert wait == http_retry.BACKOFF_SECONDS

    def test_no_header_at_all_backs_off_exponentially(self):
        assert http_retry.retry_after_seconds({}, 0) == 2.0
        assert http_retry.retry_after_seconds({}, 1) == 4.0
        assert http_retry.retry_after_seconds(None, 2) == 8.0

    def test_the_cap_is_below_the_tightest_workflow_ceiling(self):
        """Sized against `collect-press.yml`'s 120 minutes for ~600
        publishers. A cap that could be slept several times over inside one
        run is not a cap."""
        assert http_retry.RETRY_AFTER_CAP_SECONDS <= 60


class TestTheCollectorsRouteThroughIt:
    """A shared helper half the collectors ignore is not a fix."""

    @pytest.mark.parametrize("module", [
        "google_news", "sec_edgar", "sec_form_d", "press_archive",
        "benchmark_chase",
    ])
    def test_the_silent_drop_collectors_use_it(self, module):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "collectors"
        src = (root / f"{module}.py").read_text()
        assert "http_retry" in src, \
            f"{module} still drops a throttled slot with nothing recorded"

    def test_sec_form_d_no_longer_breaks_out_of_its_ladder_on_a_429(self):
        """`status_code < 500` was the whole defect: it broke out on the first
        attempt for the one status that means "ask again in a moment"."""
        from collectors import sec_form_d
        import inspect
        src = inspect.getsource(sec_form_d.search)
        assert "http_retry.fetch" in src

    def test_a_throttled_google_news_edition_is_recorded(self, monkeypatch):
        """The highest-volume call site, end to end."""
        from collectors import google_news

        monkeypatch.setattr(google_news.capped_fetch, "capped_get",
                            lambda *a, **kw: (Resp(429), b""))
        monkeypatch.setattr(http_retry.time, "sleep", lambda _s: None)
        with pytest.raises(requests.RequestException):
            google_news.fetch("layoffs", lang="es", country="MX")
        assert http_retry.EXHAUSTED, \
            "a throttled edition read as an empty one; that is the defect"
        assert "es" in http_retry.EXHAUSTED[0][0]

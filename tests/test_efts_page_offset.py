"""EFTS takes `from` as a RECORD offset, and both SEC collectors got the unit
wrong for the whole life of the project.

`collectors/sec_form_d.search(page=N)` sent `from = N * 10` and
`collectors/sec_edgar.search(page=N)` sent `from = N * PAGE_SIZE` with
`PAGE_SIZE = 10`. Different spellings, one bug: the endpoint answers with 100
hits per response, so consecutive "pages" overlapped by 90%. Pages 0, 1 and 2
were three requests, 300 hits and 120 distinct filings — 1.2 pages of reach
bought at three times the rate limit. Found on collect run 32307688627
(2026-08-19).

The overlap is MEASURED, not assumed. `fixtures/efts_page_size.json` is a live
capture: `from=0` and `from=10` share 90 of 100 ids, while `from=0` and
`from=100` share 0 or 1 (ordering jitter between two requests, not a page
boundary). Both the Form D and the 8-K query agree, so this is the endpoint's
behaviour and not one query's.

These tests pin the OFFSET each page number asks for, because that is the thing
that was wrong and the thing no other test looked at. They assert against
PAGE_SIZE rather than against 100 so that a future change to a measured
constant moves both collectors together — but `test_page_size_matches_the_
measured_capture` holds the constant itself to the recorded evidence, so
PAGE_SIZE cannot be quietly edited back to a number EFTS does not serve.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from collectors import sec_edgar, sec_form_d

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "efts_page_size.json"
CAPTURE = json.loads(FIXTURE.read_text())


class _Resp:
    """Enough of a requests.Response for search() to return an empty page."""

    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"hits": {"hits": []}}


@pytest.fixture
def record(monkeypatch):
    """Capture the query params each collector sends, without a network call."""
    calls: list[dict] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        return _Resp()

    for mod in (sec_edgar, sec_form_d):
        monkeypatch.setattr(mod.requests, "get", fake_get)
        monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)
    return calls


# --- the constant is held to the measurement --------------------------------

def test_page_size_matches_the_measured_capture():
    """PAGE_SIZE is what EFTS serves, not a number somebody picked.

    Every query in the capture returns exactly PAGE_SIZE hits while results
    remain (the short final page is excluded — it is the end of the result set,
    not the page size).
    """
    full_pages = [
        n
        for q in CAPTURE["queries"].values()
        for n in q["hits_per_response"].values()
        if n > 0 and n == max(q["hits_per_response"].values())
    ]
    assert full_pages, "capture has no full page to measure"
    assert set(full_pages) == {sec_edgar.PAGE_SIZE}


def test_the_capture_shows_a_ten_stride_overlapping_by_ninety_percent():
    """The evidence for the fix, kept next to the fix.

    If this ever stops holding, the bug this module exists for was not real and
    the constant needs re-measuring rather than defending.
    """
    for label, q in CAPTURE["queries"].items():
        assert q["overlap_with_from_0"]["10"] == 90, label
        assert q["overlap_with_from_0"]["100"] <= 1, label


# --- the offset each page number asks for -----------------------------------

# Pages 0/1/2 as ABSOLUTE offsets. Deliberately not written as
# `page * sec_edgar.PAGE_SIZE`: that form passes for any self-consistent pair,
# including the 0/10/20 the bug produced, so it would pin the shape and not the
# number. 100 here is the measured page size, asserted against the capture in
# test_page_size_matches_the_measured_capture.
PINNED_OFFSETS = [(0, 0), (1, 100), (2, 200)]


@pytest.mark.parametrize("page,expected", PINNED_OFFSETS)
def test_form_d_offset_is_one_whole_page_per_page(page, expected, record):
    sec_form_d.search(days_back=5, page=page)
    assert record[-1]["from"] == expected


@pytest.mark.parametrize("page,expected", PINNED_OFFSETS)
def test_sec_edgar_offset_is_one_whole_page_per_page(page, expected, record):
    sec_edgar.search("item 5.02", days_back=7, page=page)
    assert record[-1]["from"] == expected


def test_pages_0_1_2_do_not_overlap(record):
    """The regression itself, stated as the property that was violated.

    The old arithmetic gave 0, 10, 20 — three windows of 100 covering 120
    records. Correct paging asks for three disjoint windows.
    """
    for page in (0, 1, 2):
        sec_form_d.search(days_back=5, page=page)
        sec_edgar.search("item 5.02", days_back=7, page=page)

    for collector_offsets in (record[0::2], record[1::2]):
        offsets = [c["from"] for c in collector_offsets]
        assert offsets == [0, sec_edgar.PAGE_SIZE, 2 * sec_edgar.PAGE_SIZE]
        windows = [range(o, o + sec_edgar.PAGE_SIZE) for o in offsets]
        for a, b in ((0, 1), (1, 2), (0, 2)):
            assert not set(windows[a]) & set(windows[b])


def test_form_d_derives_its_offset_from_the_one_constant(monkeypatch, record):
    """sec_form_d must not carry a second copy of the page size.

    It had its own `page * 10` literal, which is how the two collectors were
    able to be wrong in the same way without either fix reaching the other.
    """
    monkeypatch.setattr(sec_edgar, "PAGE_SIZE", 37)
    sec_form_d.search(days_back=5, page=3)
    assert record[-1]["from"] == 111


# --- the caller that compensated for the bug --------------------------------

def test_form_d_backfill_walks_one_page_at_a_time():
    """`backfill_form_d_2026` used to advance `max(1, len(hits) // 10)` pages,
    which was a workaround for the offset bug rather than a stride. Against the
    fixed offset that expression steps 10 real pages and skips 900 filings a
    hop, so it had to come out with the bug. Pinned because a plausible-looking
    "derive the stride from what came back" is exactly what somebody would
    reintroduce.
    """
    source = pathlib.Path(__file__).parent.parent / "backfill_form_d_2026.py"
    # Code only: the comment above the constant quotes the old expression on
    # purpose, and a test that reads prose would fail on its own explanation.
    code = [ln.split("#", 1)[0] for ln in source.read_text().splitlines()]
    advances = [ln.strip() for ln in code if "page +=" in ln]
    assert advances == ["page += 1"]

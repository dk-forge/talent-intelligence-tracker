"""sec_edgar takes the NEWEST filings per phrase, not EFTS's most relevant.

#247 (2026-10-13): sec_edgar stored 0 rows on three runs in a row, every run
reading `found=11 ... already-seen=11`. EFTS orders hits by relevance, not by
date (measured live 2026-10-13: the first hits of a 7-day "item 5.02" window
ran 10-07, 10-07, 10-07, 10-09, 10-06, ...), and the collector kept the first
four per phrase. The same four highly-ranked filings came back day after day,
all already seen, while that day's new filings sat further down the page.
"""
from collectors import sec_edgar


def _hit(n, date):
    return {"_id": f"0000000000-26-{n:06d}:doc{n}.htm",
            "_source": {"display_names": [f"Co {n} Inc  (CIK 000000{n:04d})"],
                        "file_date": date}}


def test_each_phrase_keeps_its_newest_filings(monkeypatch):
    hits = [_hit(1, "2026-10-07"), _hit(2, "2026-10-06"), _hit(3, "2026-10-07"),
            _hit(4, "2026-10-08"), _hit(5, "2026-10-12"), _hit(6, "2026-10-12"),
            _hit(7, "2026-10-09"), _hit(8, "2026-10-11")]
    monkeypatch.setattr(sec_edgar, "PHRASES", ("item 5.02",))
    monkeypatch.setattr(sec_edgar, "search", lambda phrase, **kw: list(hits))
    monkeypatch.setattr(sec_edgar, "fetch_text", lambda url, **kw: "body")

    out = sec_edgar.collect(max_per_phrase=4)

    assert sorted(r["published_date"] for r in out) == \
        ["2026-10-09", "2026-10-11", "2026-10-12", "2026-10-12"]


def test_a_hit_without_a_date_sorts_last_and_does_not_crash(monkeypatch):
    hits = [_hit(1, None), _hit(2, "2026-10-12")]
    monkeypatch.setattr(sec_edgar, "PHRASES", ("item 5.02",))
    monkeypatch.setattr(sec_edgar, "search", lambda phrase, **kw: list(hits))
    monkeypatch.setattr(sec_edgar, "fetch_text", lambda url, **kw: "body")

    out = sec_edgar.collect(max_per_phrase=1)

    assert [r["published_date"] for r in out] == ["2026-10-12"]

"""The correction, and the door it waited on.

37 rows carry an `hq_country` with no headquarters city behind it, written by
one cancelled run of `place-unplaced.yml` before its bar was tightened, and
carried to the live site by `/enrich`. Synthesia, the UK company that raised
GBP 146m from GV, was filed under Czechia on the public page as a result.

For a while the correction was written and REFUSED to run, because `/enrich`
could not blank `hq_country`: `tit_clearable_columns()` returned
`funding_amount_usd` and `funding_stage` only. A corrected database in front of
an uncorrected page is a divergence nobody would notice, which is the rule
`correct_city_country.py` already states.

Plugin 1.77.0 widened that allowlist, so the door is open and the pass can run.
`test_the_refusal_is_still_correct` was the alarm for the door OPENING; it now
guards the door against being shut again, which is the same divergence seen
from the other side.
"""

import json
from pathlib import Path

import pytest

import reverse_cityless_hq as rev
from pipeline import schema

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_the_refusal_is_still_correct():
    """The door is OPEN as of plugin 1.77.0, and this test now guards it from
    being closed again.

    Until 1.77.0 this asserted `not rev.site_can_clear()`: the correction was
    written, the plugin could not accept it, and the red this test would throw
    was the signal that somebody had widened the allowlist. That happened on
    2026-08-12, deliberately, so the assertion is inverted rather than deleted.

    It still earns its place, because the failure it now catches is the same
    failure in the other direction. `tit_clearable_columns()` is read out of
    the plugin source in this checkout, so a later edit that trims the list back
    to the funding columns would leave `reverse_cityless_hq.py` refusing again
    with nothing else in the diff saying a live correction route had been
    removed. Keep it until every row in `data/cityless_hq_to_reverse.json` is
    reversed on the site AND the file is retired, not before.
    """
    assert rev.site_can_clear(), (
        "tit_clearable_columns() no longer admits hq_city/hq_country, so "
        "reverse_cityless_hq.py cannot blank a wrong headquarters country on "
        "the live site. If that removal was deliberate, the 37 rows in "
        "data/cityless_hq_to_reverse.json need another route first.")


def test_applying_with_no_credentials_exits_two_and_writes_nothing(
        tmp_path, capsys, monkeypatch):
    """The site is written FIRST, so a missing key must stop the run before the
    local database moves. Otherwise a run with no credentials leaves a corrected
    database in front of an uncorrected page, which is the exact divergence this
    whole pass exists to avoid.

    This test used to assert the REFUSED path instead -- `--apply` exiting 2
    because `tit_clearable_columns()` would not admit these columns. Plugin
    1.77.0 opened that door, so the refusal is gone and this is the guard that
    remains on the same failure.
    """
    from pipeline import cheap_extract, store, validate

    db = tmp_path / "test.db"
    conn = schema.connect(db)
    row = json.loads(rev.ROWS_PATH.read_text(encoding="utf-8"))[0]
    raw = {"headline": "Acme Raises $12M in Seed Funding",
           "raw_text": "Acme Raises $12M in Seed Funding",
           "source_url": "https://o.example/x", "discovery_url": "https://o.example/x",
           "source_name": "Example Wire", "published_date": "2026-07-01"}
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news")
    signal.content_hash = row["content_hash"]
    signal.company = row["company"]
    signal.hq_country = row["hq_country"]
    store.store(conn, signal)
    conn.commit()
    conn.close()

    monkeypatch.delenv("WP_SITE_URL", raising=False)
    monkeypatch.delenv("WP_API_KEY", raising=False)
    code = rev.main(["--apply", "--db", str(db)])
    assert code == 2
    assert "WP_SITE_URL and WP_API_KEY are required" in capsys.readouterr().err

    conn = schema.connect(db)
    still = conn.execute(
        "SELECT hq_country FROM signals WHERE is_current = 1 "
        "AND content_hash = ?", (row["content_hash"],)).fetchone()
    conn.close()
    assert still[0] == row["hq_country"], "nothing local moved"


def test_the_list_is_a_file_and_not_a_query():
    """A derived worklist would also sweep up cityless values that were there
    before and are nobody's mistake. This names exactly what one run wrote."""
    rows = json.loads(rev.ROWS_PATH.read_text(encoding="utf-8"))
    assert len(rows) == 37
    assert {"content_hash", "company", "hq_country"} <= set(rows[0])
    assert len({r["content_hash"] for r in rows}) == 37
    # The one that made the case, by name.
    assert any(r["company"] == "Synthesia" and r["hq_country"] == "CZ"
               for r in rows)


def test_a_row_that_has_a_city_now_is_not_a_target(conn):
    """Idempotent, and it also declines to undo somebody else's better answer:
    if a headquarters city has arrived since, the country is no longer the
    weak kind this pass exists to take back."""
    from pipeline import cheap_extract, store, validate

    row = json.loads(rev.ROWS_PATH.read_text(encoding="utf-8"))[0]
    raw = {"headline": "Acme Raises $12M in Seed Funding",
           "raw_text": "Acme Raises $12M in Seed Funding",
           "source_url": "https://o.example/x", "discovery_url": "https://o.example/x",
           "source_name": "Example Wire", "published_date": "2026-07-01"}
    signal = validate.build_signal(cheap_extract.extract(raw), raw, "google_news")
    signal.content_hash = row["content_hash"]
    signal.company = row["company"]
    signal.hq_country = row["hq_country"]
    store.store(conn, signal)
    conn.commit()
    assert len(rev.targets(conn)) == 1

    conn.execute("UPDATE signals SET hq_city = 'Prague' WHERE content_hash = ?",
                 (row["content_hash"],))
    conn.commit()
    assert rev.targets(conn) == []

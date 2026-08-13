"""The correction that cannot run yet, and the guard that keeps it that way.

37 rows carry an `hq_country` with no headquarters city behind it, written by
one cancelled run of `place-unplaced.yml` before its bar was tightened, and
carried to the live site by `/enrich`. Synthesia, the UK company that raised
GBP 146m from GV, is filed under Czechia on the public page as a result.

The correction is written and it REFUSES to run, because `/enrich` cannot blank
`hq_country`: `tit_clearable_columns()` returns `funding_amount_usd` and
`funding_stage` only. A corrected database in front of an uncorrected page is a
divergence nobody would notice, which is the rule `correct_city_country.py`
already states.

So the tests here are mostly about the refusal. When somebody widens that
allowlist and deploys, `test_the_refusal_is_still_correct` goes red, and that
red is the signal to queue the pass.
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
    """RED when the plugin allowlist is widened, which is exactly when the
    correction becomes runnable. Read the docstring in reverse_cityless_hq.py,
    deploy, then queue it."""
    assert not rev.site_can_clear(), (
        "tit_clearable_columns() now admits hq_city/hq_country, so the "
        "reversal has a door: queue reverse-cityless-hq.yml with "
        "dry_run=false, then delete this test and the workflow")


def test_applying_without_the_door_exits_two_and_writes_nothing(capsys):
    code = rev.main(["--apply"])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().err


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

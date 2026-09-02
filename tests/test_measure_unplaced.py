"""The unplaced census sorts blanks by cause, and the causes cannot bleed.

Each fixture row is one class from measure_unplaced's docstring, and the
assertions are the counts. The two that matter for a reader are pinned by
mutation below: a declined resolution (cityless or ambiguous) is never counted
as placeable, and a placed row from the declined class is counted in the
mirror rather than disappearing into "has a country".
"""
import pytest

import measure_unplaced
from pipeline import identity, schema


def _row(conn, company, **cols):
    base = {"signal_id": "s", "headline": "h", "summary": "s",
            "talent_readthrough": "t", "company": company,
            "company_key": identity.vocab.company_key(company),
            "pillar": "leadership", "signal_direction": "neutral",
            "confidence": "reported", "source_url": "https://e.com/a",
            "source_name": "E", "captured_at": "2026-09-01",
            "as_of": "2026-09-01", "content_hash": f"hash-{company}",
            "collector": "google_news"}
    base.update(cols)
    conn.execute(
        f"INSERT INTO signals ({', '.join(base)}) VALUES ({', '.join('?' * len(base))})",
        tuple(base.values()))


def _cache(conn, company, resolved=True, **fields):
    identity.cache_put(conn, identity.Identity(
        company_key=identity.vocab.company_key(company), company=company,
        resolved=resolved, **fields))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "talent_intel.db"
    conn = schema.connect(path)
    # (a) three ways of not knowing
    _row(conn, "Never Looked Up")
    _row(conn, "Unknown To Wikidata"); _cache(conn, "Unknown To Wikidata", resolved=False)
    _row(conn, "Known No Seat"); _cache(conn, "Known No Seat", ticker="KNS")
    # declined, two ways
    _row(conn, "Cityless"); _cache(conn, "Cityless", hq_country="CA")
    _row(conn, "Two Of Them"); _cache(conn, "Two Of Them", hq_city="Prague", hq_country="CZ",
                                      detail=f"wikidata Q1, 2 {identity.AMBIGUOUS_MARKER}")
    # (b) placeable now
    _row(conn, "Clean"); _cache(conn, "Clean", hq_city="Mountain View", hq_country="US")
    # already placed, one each way, plus one findable by HQ only
    _row(conn, "Placed", country="US")
    _row(conn, "HQ Only", hq_country="SE", hq_city="Stockholm")
    # the mirror: a cityless country already on a row, and an ambiguous one
    _row(conn, "Stamped Cityless", hq_country="CA"); _cache(conn, "Stamped Cityless", hq_country="CA")
    _row(conn, "Stamped Ambiguous", hq_country="CZ", hq_city="Prague")
    _cache(conn, "Stamped Ambiguous", hq_city="Prague", hq_country="CZ",
           detail=f"wikidata Q1, 2 {identity.AMBIGUOUS_MARKER}")
    # a superseded row must not count anywhere
    _row(conn, "Old Revision", is_current=0)
    conn.commit()
    conn.close()
    # `cache_put` creates an unqualified `employer_identity` when it finds none,
    # which lands in main and shadows the attached cache. Production never
    # sees the shadow because every next `schema.connect` moves it across;
    # the census reads through `connect_ro`, so give it that same next open.
    schema.connect(path).close()
    return path


def test_the_census_sorts_every_blank_into_exactly_one_cause(db):
    conn = schema.connect_ro(db)
    try:
        c = measure_unplaced.census(conn)
    finally:
        conn.close()
    assert c["current"] == 10
    assert c["blank_country"] == 9
    assert c["hq_only"] == 3                    # HQ Only + the two mirror rows
    assert c["no_place"] == 6
    assert c["cause"] == {
        "a_no_cache_row": 1, "a_unresolved": 1, "a_resolved_no_seat": 1,
        "declined_cityless": 1, "declined_ambiguous": 1, "b_placeable_now": 1,
    }
    assert sum(c["cause"].values()) == c["no_place"]
    assert c["mirror"] == {"cityless": 1, "ambiguous": 1}
    assert dict(c["by_collector"]) == {"google_news": 6}


def test_placeable_agrees_with_the_bar_every_writer_now_uses(db):
    """The census's (b) and `identity.is_placeable` are the same question. If
    one loosens without the other, a run of --apply-cache places a different
    set from the one the census promised."""
    conn = schema.connect_ro(db)
    try:
        c = measure_unplaced.census(conn)
        cached = [identity.cache_get(conn, k) for (k,) in conn.execute(
            f"SELECT company_key FROM {schema.CACHE_SCHEMA}.employer_identity")]
    finally:
        conn.close()
    placeable = [i for i in cached if identity.is_placeable(i)]
    # Clean, plus Stamped Ambiguous's cache row is NOT placeable, Stamped
    # Cityless's is not either: exactly one clean resolution in the cache.
    assert len(placeable) == 1
    assert c["cause"]["b_placeable_now"] == 1


def test_the_report_prints_the_numbers_and_the_refusals(db):
    conn = schema.connect_ro(db)
    try:
        text = measure_unplaced.report(measure_unplaced.census(conn))
    finally:
        conn.close()
    assert "NO place in either column" in text and "     6" in text
    assert "placeable now from the cache" in text
    assert "country with no HQ city" in text
    assert "mirror" in text
    assert "—" not in text                 # no em-dashes, anywhere it prints

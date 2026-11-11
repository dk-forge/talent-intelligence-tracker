"""Country for an unplaced signal from fields we already hold, never a guess.

Two gaps, both measured on data/talent_intel.db 2026-11-11:

1. A classifier country the vocabulary cannot read ("Unknown", "Global")
   shadowed the collector's own country (a filing's stated address), because
   `classified or raw` takes the first TRUTHY string, not the first usable one.
2. The employer HQ registry (cache.employer_identity, Wikidata + SEC) was
   applied AFTER place_fallback.decide, so a placeable registry HQ could never
   corroborate the publisher's country. 77 current rows carry no country in
   either column while a placeable registry HQ exists; most of those (INE ->
   Madrid, Eximbank -> Washington on a Vietnamese outlet) are wrong as a
   country on their own, so the registry is used ONLY as the corroborating
   second signal decide() already accepts, and a disagreeing one vetoes.
"""
import sqlite3

import pytest

from pipeline import identity, validate
from tests.test_validate import classified, raw


@pytest.mark.parametrize("junk", ["Unknown", "Global", "N/A", "not stated"])
def test_unusable_classifier_country_falls_back_to_the_source_country(junk):
    s = validate.build_signal(classified(city="", country=junk),
                              raw(country="Canada"), "sec_form_d")
    assert s.country == "CA"


def test_usable_classifier_country_still_wins():
    s = validate.build_signal(classified(city="", country="Ireland"),
                              raw(country="Canada"), "sec_form_d")
    assert s.country == "IE"


def test_no_country_anywhere_stays_blank():
    s = validate.build_signal(classified(city="", country="Unknown"), raw(), "google_news")
    assert s.country is None


def _conn_with_registry(company_key, hq_city, hq_country, detail="wikidata Q1"):
    conn = sqlite3.connect(":memory:")
    identity.ensure_cache(conn)
    identity.cache_put(conn, identity.Identity(
        company_key=company_key, company=company_key, hq_city=hq_city,
        hq_country=hq_country, resolved=True, detail=detail))
    return conn


HEAD = "Firma otwiera nowe biuro i zatrudni 200 osob"


def _unplaced(conn, url):
    return validate.build_signal(
        classified(company="Acme Widgets", city="", country="", headline=HEAD,
                   summary="Acme Widgets hires 200.", talent_readthrough="200 roles."),
        raw(headline=HEAD, raw_text=HEAD, source_url=url), "national_press", conn=conn)


def test_registry_hq_corroborates_the_publisher_country():
    conn = _conn_with_registry("acme widgets", "Warsaw", "PL")
    assert _unplaced(conn, "https://www.example.pl/biznes/acme").country == "PL"


def test_registry_hq_alone_never_places_a_row():
    conn = _conn_with_registry("acme widgets", "Warsaw", "PL")
    assert _unplaced(conn, "https://www.example.com/acme").country is None


def test_registry_hq_that_disagrees_vetoes():
    conn = _conn_with_registry("acme widgets", "Madrid", "ES")
    assert _unplaced(conn, "https://www.example.pl/biznes/acme").country is None


def test_ambiguous_or_cityless_registry_hq_is_not_used():
    conn = _conn_with_registry("acme widgets", None, "PL")
    assert _unplaced(conn, "https://www.example.pl/biznes/acme").country is None
    conn = _conn_with_registry("acme widgets", "Warsaw", "PL",
                               detail=identity.AMBIGUOUS_MARKER)
    assert _unplaced(conn, "https://www.example.pl/biznes/acme").country is None

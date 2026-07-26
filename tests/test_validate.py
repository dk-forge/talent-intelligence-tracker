"""The credibility gate is the most important thing in this repo, so it gets
the most tests. Every one of these is a rule from spec 2.
"""

import pytest

from pipeline import validate, vocab


def raw(**overrides):
    base = {
        "raw_text": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "headline": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "source_url": "https://www.irishtimes.com/business/2026/07/20/stripe-dublin/",
        "source_name": "The Irish Times",
        "published_date": "Mon, 20 Jul 2026 08:14:00 GMT",
    }
    base.update(overrides)
    return base


def classified(**overrides):
    base = {
        "company": "Stripe",
        "pillar": "company_development",
        "signal_direction": "hiring",
        "city": "Dublin",
        "country": "Ireland",
        "confidence": "reported",
        "headline": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "summary": "Stripe will add 300 roles at its Dublin engineering hub.",
        "talent_readthrough": "300 engineering roles entering the Dublin market.",
    }
    base.update(overrides)
    return base


def test_happy_path_builds_a_signal():
    signal = validate.build_signal(classified(), raw(), "google_news")

    assert signal.company == "Stripe"
    assert signal.company_key == "stripe"
    assert signal.city == "Dublin"
    assert signal.region == "Europe"
    assert signal.country == "IE"
    assert signal.published_date == "2026-07-20"
    assert signal.signal_id == signal.content_hash


def test_no_source_url_is_rejected():
    with pytest.raises(validate.Rejected, match="no source_url"):
        validate.build_signal(classified(), raw(source_url=""), "google_news")


def test_aggregator_is_never_stored_as_a_source():
    with pytest.raises(validate.Rejected, match="aggregator"):
        validate.build_signal(
            classified(),
            raw(source_url="https://news.google.com/rss/articles/CBMi"),
            "google_news",
        )


def test_empty_raw_text_is_rejected():
    """The sibling posted zero records for weeks because a collector set every
    field except this one."""
    with pytest.raises(validate.Rejected, match="raw_text"):
        validate.build_signal(classified(), raw(raw_text=""), "google_news")


def test_invented_figure_kills_the_record():
    """Spec 2 rule 2. The source says 300; the model says 3,000."""
    with pytest.raises(validate.Rejected, match="not present in source"):
        validate.build_signal(
            classified(summary="Stripe will add 3,000 roles in Dublin."),
            raw(),
            "google_news",
        )


def test_figure_present_in_source_passes_even_when_formatted_differently():
    signal = validate.build_signal(
        classified(summary="Stripe will add 300 roles."),
        raw(raw_text="Stripe to create 300 new jobs in Dublin"),
        "google_news",
    )
    assert "300" in signal.summary


def test_a_year_is_not_treated_as_an_invented_figure():
    signal = validate.build_signal(
        classified(summary="Stripe will add 300 roles during 2026."),
        raw(raw_text="Stripe to create 300 new jobs in Dublin"),
        "google_news",
    )
    assert signal.summary.endswith("2026.")


def test_news_source_cannot_be_promoted_to_verified():
    """Spec 2 rule 3: reported is never silently promoted."""
    signal = validate.build_signal(
        classified(confidence="verified"),
        raw(),
        "google_news",
    )
    assert signal.confidence == "reported"


def test_primary_source_may_be_verified():
    signal = validate.build_signal(
        classified(confidence="verified"),
        raw(source_url="https://www.sec.gov/Archives/edgar/data/000/8k.htm"),
        "google_news",
    )
    assert signal.confidence == "verified"


def test_rumored_is_not_upgraded_by_a_primary_domain():
    signal = validate.build_signal(
        classified(confidence="rumored"),
        raw(source_url="https://www.sec.gov/Archives/edgar/data/000/8k.htm"),
        "google_news",
    )
    assert signal.confidence == "rumored"


def test_pillar_outside_the_vocabulary_is_rejected():
    with pytest.raises(validate.Rejected, match="pillar"):
        validate.build_signal(classified(pillar="office_gossip"), raw(), "google_news")


def test_direction_outside_the_vocabulary_is_rejected():
    with pytest.raises(validate.Rejected, match="signal_direction"):
        validate.build_signal(classified(signal_direction="vibes"), raw(), "google_news")


def test_no_geography_is_rejected():
    with pytest.raises(validate.Rejected, match="no geography"):
        validate.build_signal(classified(city="", country=""), raw(), "google_news")


def test_missing_readthrough_is_rejected():
    with pytest.raises(validate.Rejected, match="required"):
        validate.build_signal(classified(talent_readthrough=""), raw(), "google_news")


@pytest.mark.parametrize("alias", ["SF", "Bay Area", "san francisco", "Silicon Valley"])
def test_city_aliases_collapse_to_one_city(alias):
    assert vocab.normalize_city(alias) == ("San Francisco", "North America", "US")


def test_unknown_city_is_not_invented():
    assert vocab.normalize_city("Atlantis") is None


@pytest.mark.parametrize("name,expected", [
    ("Acme Inc.", "acme"),
    ("Acme, Inc", "acme"),
    ("Acme GmbH", "acme"),
    ("ACME LIMITED", "acme"),
])
def test_company_key_collapses_legal_suffixes(name, expected):
    assert vocab.company_key(name) == expected


def test_same_event_from_two_outlets_hashes_identically():
    a = validate.build_signal(classified(), raw(source_name="The Irish Times"), "google_news")
    b = validate.build_signal(
        classified(),
        raw(source_url="https://www.rte.ie/news/2026/0720/stripe/", source_name="RTE"),
        "google_news",
    )
    assert a.content_hash == b.content_hash

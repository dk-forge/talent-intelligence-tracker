"""The fields that make the filters real, and the rule that keeps them honest.

Headcount and funding are the two numbers a recruiter would act on, which makes
them the two most damaging to get wrong. Both go through the same gate as every
other figure: present in the source text, or not stored.
"""

import json

import pytest

from pipeline import validate, vocab


def build(raw_text, **overrides):
    classified = {
        "company": "Acme", "pillar": "company_development",
        "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
        "confidence": "reported", "headline": "Acme expands in Dublin",
        "summary": "Acme is expanding in Dublin.",
        "talent_readthrough": "Roles entering the Dublin market.",
    }
    classified.update(overrides)
    return validate.build_signal(
        classified,
        {"raw_text": raw_text, "source_url": "https://example.com/acme-dublin",
         "source_name": "Example", "published_date": "2026-07-20"},
        "google_news",
    )


# --- headcount -------------------------------------------------------------

def test_headcount_present_in_source_is_stored():
    s = build("Acme to create 300 jobs in Dublin", headcount=300)
    assert s.headcount == 300


def test_invented_headcount_is_dropped_not_stored():
    """The model says 500; the source says 300. Store neither rather than a
    plausible-but-wrong number."""
    s = build("Acme to create 300 jobs in Dublin", headcount=500)
    assert s.headcount is None


def test_headcount_absent_from_source_is_dropped():
    s = build("Acme expands its Dublin office", headcount=250)
    assert s.headcount is None


def test_zero_headcount_means_not_stated():
    s = build("Acme expands its Dublin office", headcount=0)
    assert s.headcount is None


# --- funding ---------------------------------------------------------------

def test_funding_present_in_source_is_stored_verbatim():
    s = build("Organic Traditions secures $10.5M to accelerate expansion",
              funding_amount="$10.5M")
    assert s.funding_amount == "$10.5M"


def test_invented_funding_is_dropped():
    s = build("Organic Traditions secures funding to accelerate expansion",
              funding_amount="$10.5M")
    assert s.funding_amount is None


def test_funding_figure_that_disagrees_with_the_source_is_dropped():
    s = build("Acme raises $5M", funding_amount="$50M")
    assert s.funding_amount is None


# --- functions -------------------------------------------------------------

def test_functions_are_stored_as_a_normalised_list():
    s = build("Acme hiring in Dublin",
              functions=["finance", "IT", "HR", "supply chain"])
    assert json.loads(s.functions) == ["finance", "it_infrastructure",
                                       "hr_people", "supply_chain"]


def test_unrecognised_functions_are_dropped_not_invented():
    s = build("Acme hiring in Dublin", functions=["finance", "vibes", "wizardry"])
    assert json.loads(s.functions) == ["finance"]


def test_no_functions_stores_null_rather_than_an_empty_string():
    s = build("Acme hiring in Dublin", functions=[])
    assert s.functions is None


# --- industry and state ----------------------------------------------------

def test_industry_normalises_through_the_closed_list():
    s = build("Acme hiring in Dublin", industry="Pharmaceuticals")
    assert s.industry == "pharma_biotech"


def test_unknown_industry_is_dropped():
    s = build("Acme hiring in Dublin", industry="interpretive dance")
    assert s.industry is None


def test_state_is_only_set_for_us_signals():
    """An Irish signal must never carry a US state, however the model answers."""
    s = build("Acme hiring in Dublin", state="Ohio")
    assert s.state is None


def test_state_is_captured_for_us_signals():
    s = build("Acme to create jobs in Ohio", city="", country="United States",
              state="Ohio")
    assert s.state == "OH"


def test_us_city_implies_its_state():
    s = build("Acme to create jobs in Austin", city="Austin",
              country="United States")
    assert s.state == "TX"


@pytest.mark.parametrize("value,expected", [
    ("Ohio", "OH"), ("CA", "CA"), ("Washington DC", "DC"), ("Ontario", None),
])
def test_state_normalisation(value, expected):
    assert vocab.normalize_state(value) == expected


def test_sort_is_a_closed_list_not_request_text():
    """The ORDER BY string goes straight into the SQL, where $wpdb->prepare
    cannot help. It must never be built from the request."""
    from pathlib import Path

    php = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "includes" / "api.php").read_text()
    block = php[php.index("$orders = array("):php.index("$per_page =")]
    for key in ("newest", "oldest", "largest", "employer"):
        assert f"'{key}'" in block, key
    # Lookup with a fallback, never interpolation of the parameter itself.
    assert "$orders[sanitize_text_field($req->get_param('sort') ?? '')] ?? $orders['newest']" in php
    assert "ORDER BY {$order}" in php


def test_the_date_window_uses_the_source_date():
    """Filtering on capture date would move a story between periods depending
    on when a collector happened to run."""
    from pathlib import Path

    php = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "includes" / "api.php").read_text()
    assert "COALESCE(published_date, DATE(captured_at)) {$op} %s" in php
    # A malformed date must be ignored, not passed through.
    assert r"preg_match('/^\d{4}-\d{2}-\d{2}$/', $value)" in php

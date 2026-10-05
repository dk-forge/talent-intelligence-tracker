"""Offline tests for the test-only depth source probe (no network)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import depth_source_probe as p


def test_naics_sector_keywords_and_codes():
    assert p.naics_sector("Technology") == "51"
    assert p.naics_sector("Financial Services") == "52"
    assert p.naics_sector("541511") == "54"
    assert p.naics_sector("") is None
    assert p.naics_sector("Unclassifiable widgets") is None


def test_country_code():
    assert p.country_code("United States") == "US"
    assert p.country_code("uk") == "GB"
    assert p.country_code("de") == "DE"
    assert p.country_code(None) is None


def test_norm_company_strips_suffixes():
    assert p.norm_company("Google LLC") == p.norm_company("GOOGLE, Inc.") == "google"


def test_match_rate_counts_industry_region_company():
    evs = [
        {"industry": "Technology", "country": "US", "state": "CA", "company": "Acme Inc"},
        {"industry": "Retail", "country": "US", "state": "", "company": "Beta"},
        {"industry": "", "country": "DE", "company": "Gamma GmbH"},
        {"industry": "Banking", "country": "United States", "state": "NY", "company": "Delta"},
    ]
    m = p.match_rate(evs, p.us_state_ok, companies={"acme", "gamma"})
    assert m["events"] == 4
    assert m["industry_pct"] == 75.0
    assert m["region_pct"] == 50.0
    assert m["industry_and_region_pct"] == 50.0
    assert m["company_pct"] == 50.0


def test_match_rate_empty():
    assert p.match_rate([], p.us_ok)["industry_pct"] is None


def test_staleness_parses_periods():
    assert p.staleness_days("2000Q4") == (p.TODAY - p.dt.date(2000, 12, 31)).days
    assert p.staleness_days("2000-02") == (p.TODAY - p.dt.date(2000, 2, 29)).days
    assert p.staleness_days("2000-M01") == (p.TODAY - p.dt.date(2000, 1, 31)).days
    assert p.staleness_days("2000-03-05") == (p.TODAY - p.dt.date(2000, 3, 5)).days
    assert p.staleness_days("junk") is None

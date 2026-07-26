"""The free filter that runs before any paid classification.

Every rejection case here is a real headline from the first live run, which
fetched 25 candidates and contained zero talent signals.
"""

import pytest

from pipeline import prefilter

# Verbatim from the first live dry run.
REAL_NOISE = [
    "MLB expansion franchise to pour $72bn into local area after building new stadium",
    "WoW Factor: World of Warcraft's The War Within - great expansion or grind?",
    "New Medicaid Expansion Changes Hurt People with Disabilities",
    "U.S. Cattle Inventory -- Herd Expansion or Continued Herd Contraction?",
    "How NBA expansion to Seattle, Vegas would have a seismic impact",
    "Growing international concerns about the expansion of US nuclear weapons",
    "War expansion or imminent ceasefire? Conflicting reports as Israel escalates",
    "Injuries have slowed the Tempo, but the WNBA expansion team has shown promise",
    "The Daily Grind: Where is the line between an expansion and a DLC?",
]

# Verbatim from the SECOND live run, where the first version of this filter
# dropped every one of them. A capability centre opening is a hiring event even
# when the headline never says "jobs", and these are exactly what the
# standalone euphemism queries were written to surface.
SITE_OPENINGS = [
    "US telecom giant T-Mobile sets up global tech centre in Hyderabad",
    "Heineken launches global capability centre in Hyderabad",
    "US Biotech Regeneron to launch Global Capability Centre in Hyderabad",
    "TruBridge Launches Chennai Global Capability Center (GCC)",
    "Lonza To Establish Global Capability Centre In Hyderabad",
    "BMS opens Mumbai capability centre",
    "GI Outsourcing opens Global Capability Centre in Hyderabad",
]

REAL_SIGNALS = [
    "Stripe to create 300 new jobs at expanded Dublin engineering hub",
    "Workday appoints new chief people officer as it expands in London",
    "SAP raises minimum salary across German sites in retention push",
    "Intel opens new facility in Leixlip, hiring 500 staff",
    "Revolut CEO steps down after six years",
    "Shopify scraps its return to office policy for engineering employees",
]


@pytest.mark.parametrize("headline", REAL_NOISE)
def test_real_noise_is_filtered_before_the_llm(headline):
    keep, reason = prefilter.passes(headline)
    assert not keep, f"would have paid to classify: {headline}"
    assert reason


@pytest.mark.parametrize("headline", REAL_SIGNALS)
def test_real_signals_survive(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a genuine signal ({reason}): {headline}"


@pytest.mark.parametrize("headline", SITE_OPENINGS)
def test_site_openings_survive_without_the_word_jobs(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a site-opening signal ({reason}): {headline}"


def test_gulf_cooperation_council_is_not_a_capability_centre():
    """'GCC' is deliberately not a site term — it is also a trade bloc."""
    keep, _ = prefilter.passes("GCC leaders meet in Riyadh to discuss trade")
    assert not keep


def test_empty_text_is_filtered():
    assert prefilter.passes("") == (False, "empty text")


def test_employment_words_match_on_boundaries_not_substrings():
    """The sibling's equivalent loop went inert for a day because 'RIF' matched
    inside 'tariff'."""
    keep, _ = prefilter.passes("New tariffs on imported steel announced")
    assert not keep


def test_a_company_expansion_with_no_people_is_filtered():
    """'Expansion' alone is never enough — that was the whole problem."""
    keep, _ = prefilter.passes("Acme announces expansion of its Dublin facility")
    assert not keep


def test_the_same_expansion_with_a_headcount_survives():
    keep, _ = prefilter.passes("Acme announces expansion of its Dublin facility, 200 jobs")
    assert keep

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


def test_a_record_we_cannot_place_is_kept_not_discarded():
    """Geography is how this product segments; it is not what makes a record
    true. A live dry run threw away six of twelve classified candidates for
    this, all real leadership changes at real employers, all after the model
    had already been paid for. They are stored unplaced and excluded from the
    country filters instead."""
    signal = validate.build_signal(
        classified(city="", country="", headquarters_city="", headquarters_country=""),
        raw(), "google_news")
    assert signal.country is None
    assert signal.hq_country is None
    # The credibility rules are untouched: it still had to have a real source.
    assert signal.source_url.startswith("http")


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


# --- the pillar the document already decided -------------------------------
#
# An 8-K Item 5.02 IS an officer or director change; collectors/sec_edgar.py
# builds that headline itself. The model was asked anyway and put 573 of them
# in another pillar, mostly rewards_comp, because a 5.02(e) filing is mostly
# about the incoming officer's pay. Correct records, published, and unfindable
# by anyone browsing leadership changes.

def filing(**overrides):
    body = ("Item 5.02 Departure of Directors or Certain Officers. On June 29, "
            "2026 the Board appointed a new Chief Financial Officer. Her annual "
            "base salary will be 750000 dollars plus an equity award.")
    base = {
        "raw_text": body,
        "headline": "ACME CORP 8-K filing (Item 5.02): officer or director change",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/2/d8k.htm",
        "source_name": "SEC EDGAR",
        "published_date": "2026-06-29",
        "country": "United States",
    }
    base.update(overrides)
    return base


def read_as(pillar, **overrides):
    base = {
        "company": "Acme Corp",
        "pillar": pillar,
        "signal_direction": "comp_shift",
        "confidence": "verified",
        "headline": "ACME CORP 8-K filing (Item 5.02): officer or director change",
        "summary": "Acme Corp appointed a new Chief Financial Officer.",
        "talent_readthrough": "A finance leadership seat has changed hands.",
    }
    base.update(overrides)
    return base


def test_an_item_502_filing_is_a_leadership_change_whatever_the_model_says():
    signal = validate.build_signal(read_as("rewards_comp"), filing(), "sec_edgar")
    assert signal.pillar == "leadership_change"
    # Only the pillar. The model's reading of everything else survives.
    assert signal.signal_direction == "comp_shift"
    assert signal.summary.startswith("Acme Corp appointed")


def test_the_forced_pillar_is_the_one_the_hash_is_built_from():
    """Otherwise the row would be findable under leadership changes and still
    dedup as a rewards_comp record, and the next collection would publish it
    twice."""
    signal = validate.build_signal(read_as("rewards_comp"), filing(), "sec_edgar")
    assert signal.content_hash == validate.content_hash(
        "acme", "leadership_change", "2026-06-29", signal.headline, "SEC EDGAR")


def test_a_filing_the_model_gave_its_own_headline_keeps_its_pillar():
    """The narrowness is the point: where the model replaced the collector's
    boilerplate it read past the item and found something specific in the
    document, and that judgement is the one we want. A rule on the collector
    name alone would file an acquisition under leadership changes."""
    signal = validate.build_signal(
        read_as("company_development",
                headline="Masimo to be Acquired by Danaher",
                summary="Masimo agreed to be acquired by Danaher.",
                signal_direction="neutral"),
        filing(headline="Masimo to be Acquired by Danaher",
               raw_text="Masimo agreed to be acquired by Danaher."),
        "sec_edgar",
    )
    assert signal.pillar == "company_development"


def test_a_genuine_comp_filing_from_the_same_source_is_untouched():
    signal = validate.build_signal(
        read_as("rewards_comp",
                headline="Littelfuse Inc. Announces Equity Grants to Named Executive Officers",
                summary="Littelfuse granted equity awards to named executive officers.",
                company="Littelfuse Inc."),
        filing(headline="Littelfuse Inc. Announces Equity Grants to Named Executive Officers",
               raw_text="Littelfuse granted equity awards to named executive officers."),
        "sec_edgar",
    )
    assert signal.pillar == "rewards_comp"


def test_no_other_source_has_its_pillar_taken_away():
    """A news story about an officer change is still the model's call: nothing
    about a news headline makes the pillar a fact the way a filing item does."""
    assert validate.forced_pillar(
        "google_news", "Acme 8-K filing (Item 5.02): officer or director change") is None
    signal = validate.build_signal(
        classified(pillar="rewards_comp",
                   headline="Acme names new CFO in leadership change"),
        raw(headline="Acme names new CFO in leadership change"),
        "google_news")
    assert signal.pillar == "rewards_comp"


def test_same_event_from_two_outlets_hashes_identically():
    a = validate.build_signal(classified(), raw(source_name="The Irish Times"), "google_news")
    b = validate.build_signal(
        classified(),
        raw(source_url="https://www.rte.ie/news/2026/0720/stripe/", source_name="RTE"),
        "google_news",
    )
    assert a.content_hash == b.content_hash


# --- precheck: the free rejections, moved ahead of the money -----------------
#
# Every case in this table is a candidate build_signal would reject WITHOUT
# reading the model's output. Until precheck existed, each one still cost a
# full read-through first: the pipeline paid to classify an item whose
# rejection was already sitting in the collector's dict. The invariant under
# test is agreement — precheck must raise exactly where build_signal raises
# for raw-only reasons, with the same messages, so hoisting the check moved
# WHEN the money is spent and nothing else.

PRECHECK_REJECTS = (
    ("no source url", raw(source_url=""), "no source_url"),
    ("bare domain", raw(source_url="https://www.ft.com/"), "bare domain"),
    ("aggregator", raw(source_url="https://news.google.com/rss/articles/x"), "aggregator"),
    ("job board", raw(source_url="https://www.indeed.com/viewjob?jk=1"), "job board"),
    ("single advert", raw(source_url="https://insurancejournal.com/jobs/12345/"), "job advert"),
    ("empty body", raw(raw_text=""), "raw_text is empty"),
    # A filing that ANNOUNCES a reduction is the sibling's record whatever the
    # model would say about it — the Atlassian shape, caught before the most
    # expensive read the pipeline makes instead of after it.
    ("reduction filing",
     raw(source_url="https://www.sec.gov/Archives/edgar/data/1/x-8k.htm",
         raw_text="Acme 8-K filing. The Board approved a restructuring plan "
                  "that includes a reduction of approximately 10% of the "
                  "Company's workforce."),
     "the source document announces it"),
)


@pytest.mark.parametrize("label,item,message",
                         PRECHECK_REJECTS, ids=[c[0] for c in PRECHECK_REJECTS])
def test_precheck_rejects_before_any_model_is_paid(label, item, message):
    with pytest.raises(validate.Rejected, match=message):
        validate.precheck(item)


@pytest.mark.parametrize("label,item,message",
                         PRECHECK_REJECTS, ids=[c[0] for c in PRECHECK_REJECTS])
def test_build_signal_agrees_with_precheck(label, item, message):
    """The two ends of the hoist can never drift: what precheck rejects,
    build_signal rejects, for the same stated reason."""
    with pytest.raises(validate.Rejected, match=message):
        validate.build_signal(classified(), item, "google_news")


def test_precheck_passes_a_storable_candidate():
    """precheck must never reject on anything the model could still change:
    the happy-path item sails through untouched."""
    validate.precheck(raw())


def test_precheck_needs_nothing_from_the_model():
    """The whole point: it reads the raw dict alone, so run_collect can call
    it before a cent is spent. A signature that grew a `classified` parameter
    would quietly turn a pre-spend check into a post-spend one."""
    import inspect
    params = list(inspect.signature(validate.precheck).parameters)
    assert params == ["raw"]

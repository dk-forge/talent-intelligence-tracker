"""A source link must prove the claim, not merely name the outlet.

"Every record links to a primary source" is only true if the link goes to the
article that makes the claim. An outlet front page proves nothing and is stale
within hours.

This shipped broken. Google News RSS puts the outlet homepage in its <source>
element, its redirect no longer resolves by following it, and the real URL is
not recoverable from the encoded token. Two records went live linking to
crn.com and ft.com front pages before this guard existed.
"""

import pytest

from pipeline import validate


def classified():
    return {
        "company": "Acme", "pillar": "company_development",
        "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
        "confidence": "reported", "headline": "Acme to create 100 jobs in Dublin",
        "summary": "Acme will add 100 roles.",
        "talent_readthrough": "100 roles entering the Dublin market.",
    }


def raw(url):
    return {
        "raw_text": "Acme to create 100 jobs in Dublin",
        "source_url": url,
        "source_name": "Example",
        "published_date": "2026-07-20",
    }


@pytest.mark.parametrize("url", [
    "https://www.crn.com",          # exactly what went live
    "https://www.ft.com",           # exactly what went live
    "https://www.irishtimes.com/",
    "https://example.com",
])
def test_a_homepage_is_not_a_receipt(url):
    with pytest.raises(validate.Rejected, match="bare domain"):
        validate.build_signal(classified(), raw(url), "google_news")


@pytest.mark.parametrize("url", [
    "https://www.irishtimes.com/business/2026/07/20/acme-dublin/",
    "https://www.ft.com/content/abc-123",
    "https://example.com/a",
])
def test_an_article_url_passes(url):
    signal = validate.build_signal(classified(), raw(url), "google_news")
    assert signal.source_url == url


def test_google_news_itself_is_still_blocked_first():
    """The aggregator check must fire before the bare-domain one, so the error
    names the real problem."""
    with pytest.raises(validate.Rejected, match="aggregator"):
        validate.build_signal(
            classified(), raw("https://news.google.com/rss/articles/CBMi"), "google_news"
        )


# --- job adverts are not market intelligence -------------------------------

@pytest.mark.parametrize("url", [
    # Verbatim from the first live GDELT run, which stored it.
    "https://www.insurancejournal.com/jobs/878898-claims-strategy-manager",
    "https://example.com/careers/senior-engineer",
    "https://example.com/vacancies/12345",
])
def test_a_single_job_advert_is_not_a_signal(url):
    """We track that an employer is hiring at scale, not each vacancy.
    Storing adverts would make this a bad job board."""
    with pytest.raises(validate.Rejected, match="job advert"):
        validate.build_signal(classified(), raw(url), "gdelt")


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/123",
    "https://www.indeed.com/viewjob?jk=abc",
])
def test_job_boards_are_rejected_by_host(url):
    with pytest.raises(validate.Rejected, match="job board"):
        validate.build_signal(classified(), raw(url), "gdelt")


def test_a_newsroom_article_about_hiring_still_passes():
    """The guard must not eat genuine coverage that merely mentions jobs."""
    signal = validate.build_signal(
        classified(),
        raw("https://www.irishtimes.com/business/2026/07/20/acme-dublin-jobs/"),
        "gdelt",
    )
    assert signal.source_url.endswith("acme-dublin-jobs/")

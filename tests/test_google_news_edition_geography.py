# -*- coding: utf-8 -*-
"""google_news passes the edition's country the way the other two collectors
pass theirs, and refuses to when the edition is not a place.

THE MEASUREMENT THAT MOTIVATED THIS (2026-08-01, every current row)

    collector        pillar                rows   no country
    google_news      company_development    382   81.4%
    national_press   company_development    118   35.6%
    gdelt            company_development    130    5.4%
    google_news      ALL                    999   70.6%

`national_press` folds the publisher's own country into raw_text as a dateline
and `gdelt` does the same with `sourcecountry`. This collector passed no
geography at all, while being the one that had it for free: we do not read
"Google News", we read the `gl=BR` edition of it and we choose the country at
fetch time. One of the 311 unplaced funding rows is "Enigma Raises $71M", the
headline national_press.py's docstring was written about.

THE MEASUREMENT THAT LIMITED IT, same day, live, leadership query:

    en-GB   47 items   100.0% also in the en-US result set
    en-IE   47 items   100.0%
    en-SG   47 items   100.0%
    en-IN   47 items    97.9%
    en-ZA   47 items    97.9%
    de-DE  100 items     0.0%
    pt-BR   47 items     0.0%
    es-MX   64 items     0.0%
    fr-FR   50 items     0.0%
    ja-JP  100 items     0.0%

`gl=` selects a LANGUAGE for the English editions and a PLACE for the rest. A
hand-read of the 14 items en-GB returned found one British employer (Restore
plc) among Cracker Barrel, Hormel, Conagra, Toro, Iowa 80, Chemquest, Apple,
BBVA Mexico and Banijay; pt-BR scored 12 of 14. So a dateline on an English
edition would tag one identical story as British, Indian, Irish, Singaporean
and South African across five runs, and it would do it hardest on the rows
carrying no other geography — precisely the ones this change exists to fix.
A wrong country is worse than an absent one: nothing downstream can tell it
from a right one.
"""

from __future__ import annotations

import pytest

from collectors import google_news as gnews

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Enigma Raises $71M in Seed Funding</title>
    <link>https://news.google.com/rss/articles/CBMiABC</link>
    <description>The round was led by Greenfield.</description>
    <pubDate>Fri, 01 Aug 2026 09:00:00 GMT</pubDate>
    <source url="https://www.calcalist.co.il">Calcalist</source>
  </item>
</channel></rss>"""


# --- the hint reaches the classifier, and only as a hint -------------------

def test_a_place_edition_reaches_the_classifier_as_a_dateline():
    item = gnews.parse(RSS, "q", country="IL", lang="he")[0]
    assert "Israel" in item["raw_text"]
    assert "a hint, not a stated fact" in item["raw_text"]


def test_the_edition_country_is_never_a_sourced_field():
    """Same boundary tests/test_national_press.py pins one file along.

    validate.py reads raw["country"] as sourced, so writing the edition there
    would file a story under whichever edition happened to surface it.
    """
    item = gnews.parse(RSS, "q", country="IL", lang="he")[0]
    assert "country" not in item, "the edition must not be a sourced field"
    assert item["edition_country"] == "IL"   # carried for reporting only


def test_the_headline_still_leads():
    """The gate reads the first tokens hardest and the country is context, so
    the dateline is appended rather than prepended."""
    text = gnews.parse(RSS, "q", country="IL", lang="he")[0]["raw_text"]
    assert text.startswith("Enigma Raises $71M")
    assert text.rstrip().endswith("a hint, not a stated fact.)")


# --- and is refused where it would be a lie --------------------------------

@pytest.mark.parametrize("country", ["GB", "IN", "IE", "SG", "ZA", "US"])
def test_an_english_edition_gets_no_dateline(country):
    """See the module docstring: every English edition returns the en-US result
    set, so `gl=` names a language and not a place."""
    assert gnews.edition_dateline(country, "en") == ""
    item = gnews.parse(RSS, "q", country=country, lang="en")[0]
    assert "edition" not in item["raw_text"]
    assert item["raw_text"].rstrip().endswith("led by Greenfield.")


@pytest.mark.parametrize("code", ["ZZ", "", "   "])
def test_a_country_the_vocabulary_does_not_hold_gets_no_dateline(code):
    """A hint the model cannot act on is a hint the token budget pays for and
    nothing acts on, and it would also be the one place a typo in
    GOOGLE_NEWS_LOCALES could reach the prompt."""
    assert gnews.edition_dateline(code, "pt") == ""


def test_a_caller_that_forgets_the_edition_labels_nothing():
    """The defaults are empty rather than US/en on purpose. A collector that
    silently called every item American would be the same class of bug as
    writing raw["country"], arriving through a default argument."""
    item = gnews.parse(RSS, "q")[0]
    assert "edition" not in item["raw_text"]
    assert item["edition_country"] == ""


# --- the plumbing actually carries it --------------------------------------

def test_fetch_threads_both_halves_of_the_edition_through(monkeypatch):
    """parse() cannot infer the edition, so a fetch that drops either half
    would leave every non-English item unlabelled and nothing would say so."""
    seen = {}

    class _Resp:
        content = RSS
        status_code = 200

        def raise_for_status(self):
            pass

    def _get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(gnews.requests, "get", _get)
    items = gnews.fetch("q", lang="pt", country="BR")
    assert "gl=BR" in seen["url"] and "hl=pt" in seen["url"]
    assert "Brazil" in items[0]["raw_text"]


def test_every_wired_locale_resolves_or_deliberately_does_not():
    """A locale added to the registry with a code the vocabulary lacks would
    lose its dateline silently. This is where that shows up instead."""
    import source_registry as registry
    from pipeline import vocab

    unknown = sorted({
        country for lang, country in registry.GOOGLE_NEWS_LOCALES
        if lang not in gnews._LANGUAGE_ONLY_EDITIONS
        and country.upper() not in vocab.COUNTRY_NAMES
    })
    assert not unknown, (
        f"these wired editions select a place but the country vocabulary does "
        f"not hold them, so they pass no geography at all: {unknown}")

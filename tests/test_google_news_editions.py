"""The seventeen English non-US editions, and why they are not coming back.

Measured live 2026-08-01: with an English query `gl=` selects a LANGUAGE and not
a place. Every English non-US edition returned between zero and five in-scope
items per visit from a publisher in its own country that `national_press` does
not already read twice a day; the non-English controls returned 53 to 163. Two
of them, en-BD and en-HK, returned nothing at all that was not already in the
anchor's result set.

These tests do not re-run that measurement — it needs a network and the whole
point of the offline suite is that it does not. They pin the DECISION and, more
importantly, the thing that makes the decision safe: every withdrawn market
still has its own publishers' feeds in the catalogue. Withdrawing an edition
from a market that had nothing else would be a coverage hole, and this is what
notices.
"""

from __future__ import annotations

import csv

import source_registry as registry


def _wired_feeds_per_country() -> dict[str, int]:
    with registry.CATALOGUE_CSV.open(newline="") as fh:
        counts: dict[str, int] = {}
        for row in csv.DictReader(fh):
            if (row.get("rss") or "").startswith("http"):
                country = (row.get("country") or "").strip()
                counts[country] = counts.get(country, 0) + 1
    return counts


#: The catalogue names countries; the rotation names ISO2 codes.
_COUNTRY_NAMES = {
    "GB": "United Kingdom", "CA": "Canada", "AU": "Australia", "IN": "India",
    "IE": "Ireland", "SG": "Singapore", "NZ": "New Zealand",
    "ZA": "South Africa", "PH": "Philippines", "NG": "Nigeria",
    "KE": "Kenya", "GH": "Ghana", "PK": "Pakistan", "BD": "Bangladesh",
    "MY": "Malaysia", "HK": "Hong Kong", "IL": "Israel",
}


def test_the_anchor_is_the_only_english_edition_queried():
    """The finding, as an invariant.

    An English edition added back here is re-fetching the anchor under another
    name and paying gate spend for the privilege. If a future measurement says
    otherwise, this test is the place to record it — with the numbers, the way
    WITHDRAWN_ENGLISH_EDITIONS carries them.
    """
    english = [(lang, cc) for lang, cc in registry.GOOGLE_NEWS_LOCALES
               if lang == "en"]
    assert not english, (
        f"English non-US editions are back in the rotation: {english}. "
        f"Re-measure before restoring one: python3 -m analysis.editions.measure")
    assert registry.GOOGLE_NEWS_ANCHOR == ("en", "US")


def test_every_withdrawn_market_kept_a_local_path():
    """The replacement, and the reason withdrawing was safe.

    None of these markets lost coverage on 2026-08-01; they stopped being read
    once per rotation through an edition that returned the US wire, and carried
    on being read every run through their own publishers. Two feeds, because
    one feed for a whole country is the single point of failure the catalogue
    refuses everywhere else.
    """
    wired = _wired_feeds_per_country()
    thin = {cc: wired.get(_COUNTRY_NAMES[cc], 0)
            for cc in registry.WITHDRAWN_ENGLISH_EDITIONS
            if wired.get(_COUNTRY_NAMES[cc], 0) < 2}
    assert not thin, (
        f"these markets lost their Google News edition and no longer have two "
        f"publisher feeds to replace it, which makes the withdrawal a coverage "
        f"hole rather than a swap: {thin}")


def test_the_withdrawal_is_recorded_with_its_measurement():
    """A list of codes with no numbers beside it invites a silent restore.

    Asserted against the source text because that is where the evidence lives
    and where the next session will look.
    """
    text = registry.__file__ and open(registry.__file__).read()
    block = text.split("WITHDRAWN_ENGLISH_EDITIONS = (", 1)[0]
    preamble = block.rsplit("#: The seventeen English non-US editions", 1)[-1]
    assert "2026-08-01" in preamble, "the withdrawal has no date on it"
    assert "analysis.editions.measure" in preamble, (
        "the withdrawal does not say how to re-measure it")
    for code in ("en-BD", "en-HK", "pt-BR"):
        assert code in preamble, f"{code} is missing from the recorded table"


def test_the_withdrawn_codes_are_real_and_not_still_rotating():
    rotating = {cc for _, cc in registry.GOOGLE_NEWS_LOCALES}
    for code in registry.WITHDRAWN_ENGLISH_EDITIONS:
        assert len(code) == 2 and code.isupper(), code
        assert code in _COUNTRY_NAMES, f"{code} has no catalogue country name"
    # MY and IL keep an edition, in another language. Withdrawing en-MY did not
    # withdraw id-MY, and that is the point: the finding is about `gl=` under an
    # ENGLISH query, not about the market.
    assert ("id", "MY") in registry.GOOGLE_NEWS_LOCALES
    assert ("he", "IL") in registry.GOOGLE_NEWS_LOCALES
    assert "US" not in registry.WITHDRAWN_ENGLISH_EDITIONS
    assert rotating, "the rotation is empty"

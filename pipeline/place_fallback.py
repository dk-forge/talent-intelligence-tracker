"""A rule-based country for a row the classifier left unplaced, or None.

WHY (2026-11-09). The 9 Nov talent daily printed 'Austro Panel d.o.o.' (a
factory opening "in BiH", from the Bosnian national outlet biznisinfo.ba) and
'PhinCon' (an Indonesian-language job post on liputan6.com) with no country,
and so with no flag and outside every country filter. In both cases the place
was in what we already hold; the classifier just did not write it down.

THE REFUSAL THIS DOES NOT OVERTURN. measure_unplaced.py and TECHLOG
2026-09-02 refused the publisher's ccTLD and a headline place name AS SIGNALS
ON THEIR OWN, with measured counterexamples: "HPE Aruba" placed in Aruba,
"Latin America expansion" in the US, "Sofia Fridell" in Bulgaria; and on the
2026-11 corpus a Brazilian outlet covering Qualcomm, a Taiwanese one covering
General Atomics. Every one of those was ONE weak signal. So here no single
signal places a row. A country is written only when two independent pieces of
evidence AGREE and nothing on the row disagrees:

  1. The publisher's country: a curated NATIONAL outlet in data/feeds.csv
     (coverage 'National' only; a regional outlet's base does not place its
     story), else the host's country-code TLD (vanity TLDs such as .io, .ai,
     .co, .tv, .me never count).
  2. The headline's language, only where that language is spoken as a
     national language in ONE country (Indonesian, Korean, Japanese, ...).
     English, Spanish, French, German, Portuguese, Arabic, Chinese, Dutch,
     Swedish and the like never place anything.
  3. A country named in the headline, exactly one, by full name or a short
     alias this module lists explicitly ('BiH').

Placed when: publisher P is corroborated by the language (2 == P), by the
headline (3 == P), or by the employer HQ the classifier already gave (== P).
With no publisher country: only language AND headline agreeing.

Vetoed (stays blank, 'not recorded') when: the headline names more than one
country, or names one that is not the candidate; or the row's hq_country
names a different country. A blank is honest; a wrong country on a filter is
worse than a blank one.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pipeline import vocab

FEEDS_CSV = Path(__file__).resolve().parent.parent / "data" / "feeds.csv"

#: Two-letter TLDs sold as generic brands. A host under one says nothing
#: about where its publisher is.
VANITY_TLDS = frozenset({
    "ai", "am", "cc", "co", "fm", "gg", "io", "la", "ly", "me", "nu", "so",
    "sh", "to", "tv", "vc", "ws", "su", "eu",
})

#: ccTLDs that are not the ISO2 code of their country.
_TLD_ALIASES = {"uk": "GB"}

#: Headline languages spoken as a national language in ONE country only.
#: Deliberately short. Every code here must be one pipeline/language.py emits.
EXCLUSIVE_LANGUAGE = {
    "id": "ID", "ko": "KR", "ja": "JP", "pl": "PL", "cs": "CZ", "da": "DK",
    "th": "TH", "he": "IL", "ka": "GE", "hy": "AM",
}

#: Short or local names vocab does not carry, read only by this module.
_EXTRA_ALIASES = {"bih": "BA", "bosnia": "BA"}

_WORD = r"(?<!\w){}(?!\w)"


@lru_cache(maxsize=1)
def national_outlets() -> dict[str, str]:
    """Publisher host (no 'www.') to ISO2, for National outlets only."""
    out: dict[str, str] = {}
    if not FEEDS_CSV.exists():
        return out
    with FEEDS_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("coverage") or "").strip() != "National":
                continue
            iso2 = vocab.normalize_country(row.get("country") or "")
            if not iso2:
                continue
            for url in (row.get("site_url"), row.get("feed_url")):
                host = _host(url or "")
                if host:
                    out.setdefault(host, iso2)
    return out


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _base_host(host: str) -> str:
    """'feed.liputan6.com' and 'liputan6.com' are one publisher."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def publisher_country(source_url: str) -> str | None:
    host = _host(source_url or "")
    if not host:
        return None
    outlets = national_outlets()
    if host in outlets:
        return outlets[host]
    base = _base_host(host)
    by_base = {_base_host(h): c for h, c in outlets.items()}
    # Two outlets of one base in different countries would be ambiguous.
    clash = {c for h, c in outlets.items() if _base_host(h) == base}
    if base in by_base and len(clash) == 1:
        return by_base[base]
    tld = host.rsplit(".", 1)[-1]
    if len(tld) != 2 or tld in VANITY_TLDS:
        return None
    iso2 = _TLD_ALIASES.get(tld, tld.upper())
    return iso2 if iso2 in vocab.COUNTRY_NAMES else None


def headline_countries(headline: str) -> set[str]:
    low = (headline or "").lower()
    found = set()
    for name, code in list(vocab._COUNTRY_ALIASES.items()) + list(_EXTRA_ALIASES.items()):
        if len(name) < 4 and name not in _EXTRA_ALIASES:
            continue
        if re.search(_WORD.format(re.escape(name)), low):
            found.add(code)
    return found


def language_country(language: str | None) -> str | None:
    return EXCLUSIVE_LANGUAGE.get((language or "").strip().lower())


def decide(*, source_url: str, headline: str, language: str | None,
           hq_country: str | None = None) -> str | None:
    """ISO2 when the evidence agrees and nothing contradicts it, else None."""
    pub = publisher_country(source_url)
    lang = language_country(language)
    named = headline_countries(headline)
    hq = (hq_country or "").strip().upper() or None
    if len(named) > 1:
        return None
    title = next(iter(named), None)
    if pub:
        candidate = pub
        corroborated = lang == pub or title == pub or hq == pub
    else:
        candidate = lang
        corroborated = bool(lang) and title == lang
    if not (candidate and corroborated):
        return None
    if title and title != candidate:
        return None
    if lang and lang != candidate:
        return None
    if hq and hq != candidate:
        return None
    return candidate

"""Legal Entity Identifier matching against the GLEIF golden copy. PURE.

No network, no database: gleif_lei.py does the I/O and calls this.

THE RULE, and it is deliberately strict. An employer gets an LEI only when
  1. one of its stored names normalises to the same string as a GLEIF legal
     name or other name, AND
  2. the GLEIF legal-address country equals the employer's ONE known country,
     AND
  3. exactly one LEI answers. Two entities sharing a name in a country, or our
     spellings pointing at two different LEIs, is ambiguous and gets nothing.

Why country is required: the read-only probe (2026-10-13) matched "Hook Media,
Inc." (US) to "HÖÖK media AB" (Sweden). Both normalise to "hook media". A
wrong identifier on a public page is worse than none, so a missing country or
an employer seen in two countries also gets nothing.
"""
from __future__ import annotations

import re
import unicodedata

# Trailing legal-form tokens, stripped repeatedly ("& Co. KG", "Co., Ltd").
# Only forms, never descriptive words such as "holdings" or "group": those are
# part of how two different entities are told apart.
LEGAL_SUFFIXES = frozenset("""
inc incorporated corp corporation co company ltd limited llc llp lp plc pllc
gmbh ag kg mbh ug se sa sas sarl sa de cv nv bv ab as asa oy oyj aps spa srl
sl sll kk pte pty bhd sdn tbk pt jsc pjsc ojsc oao zao ooo sro spol kft zrt
lda ltda eirl cia sociedad anonima aktiengesellschaft
""".split())

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalise_name(name: str | None) -> str:
    """Case, accents, punctuation and trailing legal forms removed."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = s.replace("&", " and ").replace(".", "")   # "S.A." -> "sa"
    s = _PUNCT.sub(" ", s.replace("_", " "))
    words = _SPACE.sub(" ", s).strip().split(" ")
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    while words and words[-1] == "and":   # "smith and sons gmbh and co kg"
        words.pop()
        while words and words[-1] in LEGAL_SUFFIXES:
            words.pop()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(w for w in words if w)


class LeiIndex:
    """(normalised name, country) -> set of LEIs, for the keys we asked about.

    Built by streaming 3.4M GLEIF records and keeping only names one of our
    employers could match, so memory is bounded by our employers, not GLEIF.
    """

    def __init__(self, wanted: set[tuple[str, str]]):
        self._wanted = {(n, c.upper()) for n, c in wanted if n}
        self._hits: dict[tuple[str, str], set[str]] = {}

    def add(self, name: str | None, country: str | None, code: str) -> None:
        key = (normalise_name(name), (country or "").upper())
        if key in self._wanted:
            self._hits.setdefault(key, set()).add(code)

    def lookup(self, norm: str, country: str) -> set[str]:
        return self._hits.get((norm, country.upper()), set())

    def __len__(self) -> int:
        return len(self._hits)


def match_company(index: LeiIndex, names, countries: set[str]) -> str | None:
    """The single LEI for an employer, or None when the match is not unique."""
    if len(countries) != 1:
        return None
    (country,) = countries
    found: set[str] = set()
    for name in names:
        norm = normalise_name(name)
        if norm:
            found |= index.lookup(norm, country)
    return next(iter(found)) if len(found) == 1 else None

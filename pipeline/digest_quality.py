"""Checks every digest item must pass before it goes out (owner, 2026-11-04).

Pure and offline: each function reads one /query-shaped row and returns a
verdict. daily_digest.py decides what to print; this decides what is true.

  - check_attribution: is the credited employer the one the source names?
  - link: the careers page when stored, else the source.
  - is_stale / is_republished: an old plan is not news.
  - place / industry: a country and an industry for every item, read from the
    stored fields or, failing that, the article text. Never guessed.
  - applications_open / stated_roles: only what the source states.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pipeline import vocab

OK = "ok"
REWRITTEN = "rewritten"
HELD = "held"

#: How old an announcement may be and still lead a daily edition.
STALE_DAYS = 30

LOCATION_NOT_STATED = "location not stated"
INDUSTRY_NOT_STATED = "industry not stated"

#: The Ask The Recruiter link printed with every edition's hiring items.
TAILOR_CV_URL = "https://asktherecruiter.com"


def _get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def source_text(row) -> str:
    return " ".join(str(_get(row, k) or "") for k in
                    ("headline", "summary", "talent_readthrough"))


# --- 1. Employer attribution -----------------------------------------------

@dataclass(frozen=True)
class Attribution:
    status: str
    employer: str
    reason: str = ""


#: "<Actor> is reopening <Company> facilities": the roles belong to the actor,
#: and <Company> names the site. This is the Cedra case.
_SITE_WORDS = r"(?:facilities|facility|plant|plants|site|sites|factory|factories|mill|works)"
_TAKEOVER_VERBS = (r"(?:is |are |will be |has been |have been )?"
                   r"(reopening|reopen|reopens|reopened|restarting|restart|"
                   r"restarts|reviving|revive|revives|taking over|takes over|"
                   r"took over|buying|buys|bought|acquiring|acquires|acquired)")


def _named_in(name: str, text: str) -> bool:
    return bool(name) and re.search(
        rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I) is not None


def check_attribution(row) -> Attribution:
    company = (_get(row, "company") or "").strip()
    text = source_text(row)
    if not company:
        return Attribution(HELD, company, "no employer credited")
    site = re.search(
        rf"((?:[A-Z][\w&'.-]*\s){{0,3}}[A-Z][\w&'.-]*)\s+{_TAKEOVER_VERBS}\s+"
        rf"(?:the\s+)?(?:former\s+)?{re.escape(company)}\s+({_SITE_WORDS})",
        text)
    if site:
        actor, place_word = site.group(1).strip(), site.group(3)
        if actor.lower() != company.lower():
            return Attribution(
                REWRITTEN,
                f"{actor} ({_gerund(site.group(2))} {company} {place_word})",
                f"{company} names the site; {actor} is the employer")
    if not _named_in(company, text):
        return Attribution(HELD, company, "the source text never names the "
                           "credited employer")
    return Attribution(OK, company)


def _gerund(verb: str) -> str:
    v = verb.lower()
    for stem, form in (("reopen", "reopening"), ("restart", "restarting"),
                       ("reviv", "reviving"), ("tak", "taking over"),
                       ("took", "taking over"), ("buy", "buying"),
                       ("bought", "buying"), ("acquir", "acquiring")):
        if v.startswith(stem):
            return form
    return v


# --- 2. Links ----------------------------------------------------------------

def link(row) -> str:
    return ((_get(row, "careers_url") or "").strip()
            or (_get(row, "source_url") or "").strip())


# --- 3. Dates ----------------------------------------------------------------

def announced(row) -> str | None:
    d = (_get(row, "published_date") or "").strip()
    return d[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", d) else None


def is_stale(row, until: datetime) -> bool:
    d = announced(row)
    if not d:
        return False
    day = datetime.strptime(d, "%Y-%m-%d").date()
    return (until.date() - day).days > STALE_DAYS


_REPUBLISHED = re.compile(
    r"\b(?:first |originally |previously )?(?:announced|unveiled|revealed)\s+"
    r"(?:in|back in|last)\s+(?:[A-Z][a-z]+\s+)?(20\d\d|year)\b", re.I)
_REPUBLISHED_PHRASES = re.compile(
    r"\b(?:previously announced|reiterat\w+|re-?announc\w+|"
    r"announced earlier this year)\b", re.I)


def is_republished(row, until: datetime) -> bool:
    text = source_text(row)
    if _REPUBLISHED_PHRASES.search(text):
        return True
    for m in _REPUBLISHED.finditer(text):
        token = m.group(1).lower()
        if token == "year" or int(token) < until.year:
            return True
    return False


# --- 4. Country and industry ---------------------------------------------------

def _scan_country(text: str) -> str | None:
    found = set()
    low = text.lower()
    for name, code in vocab._COUNTRY_ALIASES.items():
        if len(name) < 4:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", low):
            found.add(code)
    if len(found) == 1:
        return found.pop()
    return None


def place(row) -> str | None:
    """ISO2 of where the roles are: stored, else from the city, else read
    from the text when exactly one country is named. None when unplaceable."""
    stored = vocab.normalize_country(_get(row, "country") or "")
    if stored:
        return stored
    city = vocab.normalize_city(_get(row, "city") or "")
    if city:
        return city[2]
    return _scan_country(source_text(row))


def country_name(iso2: str | None) -> str:
    if not iso2:
        return LOCATION_NOT_STATED
    return vocab.COUNTRY_NAMES.get(iso2, iso2)


def industry(row) -> str:
    stored = vocab.normalize_industry(_get(row, "industry") or "")
    if stored:
        return stored
    low = source_text(row).lower()
    found = {code for alias, code in vocab._INDUSTRY_ALIASES.items()
             if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", low)}
    return found.pop() if len(found) == 1 else INDUSTRY_NOT_STATED


# --- 6. What the source states ---------------------------------------------------

_APPLICATIONS = re.compile(
    r"\b(?:applications?\s+(?:are\s+|is\s+)?(?:now\s+)?open|"
    r"accepting applications|apply now|now accepting)\b", re.I)
_ROLES = re.compile(
    r"\broles?\s+(?:include|including|such as|are|for)\s+([^.;]{3,120})", re.I)


def applications_open(row) -> bool:
    return bool(_APPLICATIONS.search(source_text(row)))


def stated_roles(row) -> str | None:
    m = _ROLES.search(source_text(row))
    return m.group(1).strip() if m else None

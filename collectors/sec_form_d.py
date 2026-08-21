"""SEC Form D collector — the funding signal, free and primary-sourced.

Every US private placement files a Form D, and the filing is structured XML
rather than prose: issuer name, industry, city, state, and the amount actually
sold. That means the money figure is a fact read off a legal filing, not a
number a model produced — which is the only kind of figure this product stores.

A Form D states money and nothing else. It does not say the issuer is hiring,
and this collector never claims it does: the row carries the raise as a fact
and leaves the headcount question open. That is why `signal_direction` on these
rows is "neutral" — the rule in `pipeline/classify.py` is that the direction is
what the SOURCE STATES about headcount, never what the event usually implies.

**The noise problem**: most Form D filers are not employers at all. Two thirds
are investment funds, and much of the rest are single-purpose vehicles that
raise money for one building and employ nobody. `industryGroupType` names both
classes, so it is checked first; the name patterns are the second pass, for the
vehicles that file under a generic group.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import requests

from . import sec_edgar

EFTS_URL = sec_edgar.EFTS_URL
ARCHIVES = sec_edgar.ARCHIVES
USER_AGENT = sec_edgar.USER_AGENT
COLLECTOR = "sec_form_d"
REQUEST_DELAY = 0.15

# A fund raising a fund is not a talent signal. This is ~70% of Form D volume.
POOLED_FUND_INDUSTRIES = {
    "pooled investment fund",
    "other investment fund",
    "hedge fund",
    "private equity fund",
    "venture capital fund",
}

# Neither is a building. Form D's REAL ESTATE group ("Commercial",
# "Residential", "Construction", "REITS and Finance", "Other Real Estate") is
# filed almost entirely by single-purpose vehicles: one LLC per property, which
# raises the money, buys the asset and employs nobody. A recruiter or a job
# seeker can do nothing with them, and because each raise is large they
# dominated the money views — 874 of the first 4,003 published rows, and every
# dollar of the "Real estate & construction $12.2B" total.
#
# The cost of this rule is stated rather than hidden: a genuine operating
# employer that files under the same group (a brokerage, a general contractor)
# is dropped with them. On a sample of the live rows that was ~2%, and the
# dataset gives no column that separates the two.
REAL_ESTATE_INDUSTRIES = {
    "commercial",
    "residential",
    "construction",
    "reits and finance",
    "other real estate",
}

EXCLUDED_INDUSTRIES = POOLED_FUND_INDUSTRIES | REAL_ESTATE_INDUSTRIES

# industryGroupType alone is not enough: plenty of vehicles file under a
# generic group ("Other", "Investing", "Business Services") and are still
# vehicles, not employers. The name gives them away — a tenant-in-common
# interest, a qualified opportunity fund, an SPV, a numbered series in a
# ladder, a conglomerate holding private equity or private credit. These
# patterns are deliberately narrow: an operating company must never be dropped,
# so each one is a phrase no real employer puts in its name.
EXCLUDED_NAME_PATTERNS = re.compile(
    r"\b("
    r"fund\s*(?:i{1,3}|iv|v|vi{0,3}|\d+)?(?:-[a-z])?\b"
    r"|dst\b|reit\b|\btrust\b"
    r"|net[- ]leased?|opportunity zone|statutory trust"
    r"|\bl\.?p\.?$|\bllp$"
    r"|series\s+[a-z0-9]+\s+(?:dst|lp|llc)$"
    r"|holdings?\s+(?:i{1,3}|\d+)$"
    # Single-purpose property and investment vehicles.
    r"|tic\b|qof\b|spv\b|investco\b|conglomerate\b"
    r"|private\s+equity\b|private\s+credit\b"
    r"|co-?invest(?:ors?|ment)?\b"
    r"|apartments?\b|condominiums?\b|villas?\b|townhomes?\b"
    # Property vehicles that file under a generic industry group. Note what is
    # NOT here: "estates" matches "Real Estate Business Analytics, Inc.", a
    # software company, and "development" matches "Strobe Development, Inc.".
    r"|properties\b|realty\b|land\s?co\b|(?:golf|country)\s+club\b"
    # Insurance product wrappers: "DELAWARE LIFE VARIABLE ACCOUNT H",
    # "NATIONWIDE PPVUL SEPARATE ACCOUNT 6". A separate account is a ring-fenced
    # pool backing policies; it has no staff. The bulk path also catches these on
    # the filing itself (sec_form_d_bulk.NOT_A_CAPITAL_RAISE), which is the
    # stronger check — this is the fallback for a blank description.
    r"|(?:variable|separate)\s+account\b|ppvu?l\b|vul\b"
    # Non-traded credit and infrastructure vehicles: "Apollo Asset Backed Credit
    # Co LLC", "Apollo Infrastructure Co LLC" — the same class as the
    # "Conglomerate" entities above, wearing a different name. The strategy words
    # are listed rather than generalised, because a bare "Credit Co" or "Equity
    # Co" would eventually match a real lender.
    r"|(?:asset[- ]?backed|infrastructure|private\s+markets?|diversified\s+credit"
    r"|opportunistic\s+credit|multi[- ]?strateg(?:y|ies))"
    r"\s+(?:credit\s+|income\s+)?co\.?\s*(?:llc|lp|l\.p\.)?\.?$"
    # "MIMG CCLXV Rapid City 6 Master, LLC" — the master entity of a syndication.
    r"|master,?\s*(?:llc|lp)\.?$"
    # A roman numeral immediately before the entity suffix is a series vehicle:
    # "Northfield V74 I, LLC", "CRA Funding VIII, LLC", "HMA III, Inc.".
    r"|(?:i{1,3}|iv|vi{0,3}|ix|xi{0,3})[\s,]*(?:llc|lp|inc)\.?$"
    r")", re.I,
)

# Below this the raise is too small to imply meaningful hiring, and the noise
# floor (shell companies, single-property real estate LPs) is high.
MIN_RAISED = 1_000_000


# --- Is the amount sold money raised, on the date we are dating it to? ------
#
# Everything above asks WHO filed. These three ask WHAT THE FIGURE IS, and a
# real operating company clears every issuer filter and still reports a number
# that is not a dated capital raise. All three shipped, and the first two were
# withdrawn by hand on 2026-07-29 before this function existed: Dillard's
# $2.39bn (shares issued as merger consideration) and OPTCAPITAL $1.77bn (the
# fourteenth annual amendment to an offering whose first sale was 2012).
#
# Measured on 2026-07-29 against the published corpus (2,998 Form D bulk rows,
# $87.09bn), each rule counting only what the ones above it left:
#
#     business combination    176 rows   $ 8.53bn
#     amendment               539 rows   $14.75bn
#     continuous offering      29 rows   $ 0.27bn
#                             ---------------------
#                             744 rows   $23.55bn   (24.8% of rows, 27% of $)

#: 1. The issuer has ticked "business combination transaction" itself: the
#:    securities are merger, acquisition or exchange consideration, so the
#:    "amount sold" is the value of stock handed to the target's holders and no
#:    cash reached the company. Snowflake/Observe, Marvell/XConn, Roblox/
#:    Morpheus, AeroVironment/Empirical, Radian/Inigo, Tencent Music/Ximalaya
#:    were all live on the site as raises.
#:
#:    Deliberately NOT gated on the clarification text, even though the text is
#:    where the reader can see it. 115 of the 176 published rows leave that box
#:    EMPTY, so a text rule decides a third of the class and readmits whichever
#:    filers happened to write prose we could match. Of those that do write
#:    something, the largest are MIXED — Onebrief $359M and CesiumAstro $271M
#:    both say part-cash-part-consideration in one sentence, HawkEye 360 says
#:    "$25M of the shares", ChartSpan says "includes shares issued pursuant to a
#:    merger as well as shares sold to investors" — and no column splits one
#:    figure into its two halves. Keeping those publishes an overstated raise,
#:    which is the exact failure this rule exists for.
#:
#:    The cost is known and paid on purpose: about fifteen rows, ~$0.6bn, where
#:    the offering really was cash and the acquisition was what the cash bought
#:    (Infinity Natural Resources $350M, Legence $100M, FONAR, Saint Raphael
#:    Health). Those are recall lost to precision, and they are named here so
#:    the loss is documented rather than discovered.
BUSINESS_COMBINATION = (
    "not a capital raise: the filing answers yes to business combination "
    "transaction, so the securities are consideration in a merger, acquisition "
    "or exchange rather than stock sold for cash")

#: 2. An amendment (D/A) restates the CUMULATIVE amount sold since the
#:    offering's first sale. It is not new money on the day it was filed, and
#:    the original Form D is already stored with the same raise at its own date
#:    — under a different headline, so a different content_hash, so dedup never
#:    saw them as one. Fluidstack is the shape: D 2026-01-23 $450M, D/A
#:    2026-05-12 $842M (same first sale, the $450M inside it), D 2026-06-30
#:    $730M against a new $1.5bn offering. Dropping the amendment leaves the two
#:    genuine offerings, each dated to its own filing.
#:
#:    The increment ($392M here) is deliberately not derived. Subtracting one
#:    filing from another produces a figure that appears in neither, and a
#:    figure that appears in no source is the one thing this project does not
#:    store. An amendment can also revise the total DOWN, so the subtraction is
#:    not even reliably a raise.
AMENDMENT = (
    "not new money on the filing date: this is an amendment (D/A), and the "
    "amount sold on an amendment is the cumulative total since the offering's "
    "first sale. The original Form D already carries that raise at its own "
    "date, so counting both states it twice")

#: 3. A continuous offering: no stated size AND intended to run more than a
#:    year. The amount sold is then a running total over a window with no
#:    beginning in view, re-reported larger at every annual amendment —
#:    OPTCAPITAL's $1.77bn had been accumulating since 2012-07-22.
#:
#:    Both halves are required. "Indefinite" ALONE is kept, because it usually
#:    means only that the filer declined to state a ceiling: 88 published rows /
#:    $1.21bn are Indefinite on a one-year offering with a recent first sale,
#:    and they are ordinary raises (Harvey AI's $200M, first sold twelve days
#:    before the filing). Excluding on the word alone would have taken them.
CONTINUOUS_OFFERING = (
    "not a dated capital raise: the offering states no size ('Indefinite') and "
    "is intended to last more than one year, so the amount sold is a running "
    "total over an open window rather than money raised on the filing date")


def is_true(value: str | None) -> bool:
    """A Form D boolean. Written 'true'/'false' in both the XML and the TSV."""
    return (value or "").strip().lower() == "true"


def money_raised_exclusion(*, business_combination: bool, amendment: bool,
                           offering_amount: str, more_than_one_year: bool) -> str | None:
    """Why this offering's amount sold is not money raised, or None if it is.

    Takes the four answers rather than a filing, because the two routes read
    them out of different shapes — TSV columns on the bulk path, XML tags on
    the search path — and the RULE has to have one home, for the same reason
    EXCLUDED_INDUSTRIES and US_STATE_CODES do. The returned string is published
    as the retraction reason, so it says why rather than that.
    """
    if business_combination:
        return BUSINESS_COMBINATION
    if amendment:
        return AMENDMENT
    if (offering_amount or "").strip().lower() == "indefinite" and more_than_one_year:
        return CONTINUOUS_OFFERING
    return None


# Where a Form D issuer is, decided from the address the filing states. Both of
# these live HERE, in the module the bulk path already imports, so the two
# routes to one filing cannot disagree about which issuers are American — the
# same reason EXCLUDED_INDUSTRIES and EXCLUDED_NAME_PATTERNS have one home.
#
# `stateOrCountry` is a two-character code, and EDGAR uses one namespace for
# both kinds of place: a US state is its postal code ("CA") and anywhere else
# is an EDGAR code that is not one ("A1" is British Columbia, "K7" is Israel).
# Membership in this set, and only that, decides US-versus-foreign.
US_STATE_CODES = frozenset("""
AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
""".split())


def _country_name(description: str) -> str:
    """The COUNTRY out of a stateOrCountryDescription, which holds two things.

    The field is written narrowest-first, and only a US filer's fits in one
    segment. A US issuer gets a bare state ("CALIFORNIA"); a foreign one gets
    the sub-national unit and THEN the country: "BRITISH COLUMBIA, CANADA",
    "ONTARIO, CANADA", "NEW SOUTH WALES, AUSTRALIA", "ENGLAND, UNITED KINGDOM".

    Passing that whole string on as the country is what shipped on the bulk
    path: it reached `vocab.normalize_country` as "British Columbia, Canada",
    matched nothing, and stored NULL. 100 Canadian issuers therefore landed
    with no country in EITHER column — invisible to every geographic filter on
    the site, with the country printed in the filing we had already fetched and
    parsed. Plain one-segment foreign names ("ISRAEL", "UNITED KINGDOM") always
    worked, which is why the gap read as a few odd rows rather than as a bug.

    The country is the LAST segment and only the last. Nothing is guessed: a
    tail the vocabulary does not recognise still normalises to None upstream
    and still stores NULL, exactly as before. The full string is kept verbatim
    in the record's body text, so the filing's own wording is not lost.
    """
    return (description or "").rsplit(",", 1)[-1].strip()


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return (m.group(1) or "").strip() if m else ""


def _money(value: str) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _humanise(amount: int) -> str:
    """Render the figure the way a source would write it, so the
    figures-are-sourced check has something to match against."""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B".replace(".0B", "B")
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M".replace(".0M", "M")
    return f"${amount:,}"


def search(days_back: int = 5, page: int = 0, *,
           startdt: str | None = None, enddt: str | None = None) -> list[dict]:
    """One EFTS page of Form D filings. Returns raw hits.

    Explicit startdt/enddt (YYYY-MM-DD) override days_back, the same shape
    sec_edgar.search already has: the backfill walks historical windows this
    way while the daily run keeps its rolling few days.
    """
    if not (startdt and enddt):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        startdt = start.strftime("%Y-%m-%d")
        enddt = end.strftime("%Y-%m-%d")
    params = {
        "q": '"equity"',           # EFTS requires a term; every Form D carries it
        "forms": "D",
        "dateRange": "custom",
        "startdt": startdt,
        "enddt": enddt,
        # sec_edgar.PAGE_SIZE, never a literal: this was `page * 10` against an
        # endpoint that answers with 100, so pages 0/1/2 overlapped by 90%.
        "from": page * sec_edgar.PAGE_SIZE,
    }
    time.sleep(REQUEST_DELAY)
    resp = requests.get(EFTS_URL, params=params,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        timeout=30)
    resp.raise_for_status()
    return (resp.json().get("hits") or {}).get("hits") or []


def collect(queries=None, *, days_back: int = 5, pages: int = 3,
            max_items: int = 25) -> list[dict]:
    """Return raw candidate dicts. `queries` is accepted and ignored so this is
    interchangeable with the other collectors."""
    out: list[dict] = []
    seen: set[str] = set()

    for page in range(pages):
        try:
            hits = search(days_back=days_back, page=page)
        except requests.RequestException as exc:
            # SAY SO. This `break` used to be silent, and a silent break is
            # indistinguishable from a genuinely quiet window: both leave
            # found=0, which run_outcome() reads as a failure, and the workflow
            # goes red with nothing in the log to explain it.
            #
            # That is not hypothetical. On 2026-08-19 `collect` run 32307688627
            # reddened main on exactly this path -- EFTS refused the first page,
            # the group ran 3.0s against a normal 32s, and the step printed
            # nothing at all. Replaying the same query afterwards returned 222
            # filings, so the window was never empty. Diagnosing one transient
            # took a full log read and a manual replay of the query.
            #
            # This line does NOT change what the run concludes; whether an
            # errored zero should be fatal at all is a live question about
            # failure semantics and is the owner's call. It changes only whether
            # the failure can be read. An unattributable red is the expensive
            # part.
            print(f"[sec_form_d] EDGAR refused page {page}: "
                  f"{type(exc).__name__}: {exc}")
            break
        if not hits:
            break

        for hit in hits:
            if len(out) >= max_items:
                return out

            url = sec_edgar.document_url(hit)
            if not url or url in seen:
                continue
            seen.add(url)

            try:
                time.sleep(REQUEST_DELAY)
                xml = requests.get(url, headers={"User-Agent": USER_AGENT},
                                   timeout=30).text
            except requests.RequestException:
                continue

            industry = _tag(xml, "industryGroupType")
            if industry.lower() in EXCLUDED_INDUSTRIES:
                continue

            # Is the figure money raised, on the date we would date it to? The
            # three answers are in the XML the whole time; this path read past
            # them for as long as it existed. Both amendment signals are read
            # because the form carries both, and a filing that says D/A at the
            # top must not depend on a nested flag being present to be caught.
            if money_raised_exclusion(
                business_combination=is_true(_tag(xml, "isBusinessCombinationTransaction")),
                amendment=(_tag(xml, "submissionType").upper() == "D/A"
                           or is_true(_tag(xml, "isAmendment"))),
                offering_amount=_tag(xml, "totalOfferingAmount"),
                more_than_one_year=is_true(_tag(xml, "moreThanOneYear")),
            ):
                continue

            raised = _money(_tag(xml, "totalAmountSold"))
            if not raised or raised < MIN_RAISED:
                continue

            company = _tag(xml, "entityName")
            if not company:
                continue
            if EXCLUDED_NAME_PATTERNS.search(company):
                # An investment vehicle raising capital employs nobody; only an
                # operating company's raise implies hiring.
                continue

            city = _tag(xml, "city").title()
            # The issuer address, read the same way the bulk path reads it. The
            # code alone is not a country: "A1" is British Columbia, and this
            # path used to store it in `state` (a US state column) while
            # asserting country "United States" on every record. The XML has
            # carried the readable place next to the code the whole time.
            state_code = _tag(xml, "stateOrCountry").upper()
            place = _tag(xml, "stateOrCountryDescription").title()
            in_us = state_code in US_STATE_CODES
            money = _humanise(raised)

            # The classifier reads only raw_text, so every fact it may use has
            # to be stated here — in the words a source would use, because the
            # figures-are-sourced check compares against exactly this string.
            # The place is the filing's own wording, so a foreign issuer's
            # country is in the text the classifier sees, not just in a column.
            headline = f"{company} raised {money} in a private placement"
            body = (
                f"{company} filed a Form D with the SEC reporting {money} "
                f"({raised:,} dollars) sold in a private securities offering. "
                f"Industry: {industry}. Location: {city}, {place or state_code}. "
                f"Form D filings are required for exempt offerings and are the "
                f"public record of private fundraising."
            )

            out.append({
                "raw_text": f"{headline}\n\n{body}",
                "headline": headline,
                "source_url": url,
                "source_name": "SEC EDGAR (Form D)",
                "discovery_url": url,
                "published_date": (hit.get("_source") or {}).get("file_date"),
                "country": "United States" if in_us else _country_name(place),
                "state": state_code if in_us else "",
                "city": city,
                "funding_amount": money,
                "collector": COLLECTOR,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    return out

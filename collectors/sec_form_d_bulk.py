"""SEC Form D BULK data sets — the same filings, already structured, free.

`sec_form_d.py` finds one filing at a time through full-text search and then
fetches and regexes its XML. SEC also publishes every Form D as a quarterly
tab-separated data set, with the fields already parsed: issuer name, CIK,
city, state, industry, and the amount actually sold. Coverage runs 2008Q1 to
the current quarter.

Three things follow, and they are the whole reason this module exists:

- **No model is involved.** The fields are columns, so a record can be built
  deterministically. The search path pays a gate call plus a read-through per
  filing to extract what the TSV hands over for nothing.
- **The figure is a real number**, not a phrase a model copied out of prose.
- **Recall is an order of magnitude better.** EFTS matched ~850 filings a
  month for the collector's query; a quarter of the data set holds ~15,700
  submissions.

**Expected yield, so a low count is not misread as a broken parse:** about
**1,100-1,300 records per quarter**, not the 2,000-3,000 an earlier note
estimated. The difference is the exclusions below doing their job — funds,
single-purpose property vehicles, insurance product offerings and employee
benefit plans are all filtered out, and then so is every filing whose "amount
sold" is not money raised on the day it was filed (merger consideration, an
amendment's cumulative total, a continuous offering's running total; see
`sec_form_d.money_raised_exclusion`). Measured across the three cached
quarters on 2026-07-29:

    2025Q4   1,649 -> 1,257   ($44.42bn -> $33.15bn)
    2026Q1   1,545 -> 1,129   ($56.53bn -> $35.49bn)
    2026Q2   1,690 -> 1,277   ($55.41bn -> $40.39bn)

the left column being the yield before the offering-shape rules and the right
the yield now. A quarter that yields ~1,200 is healthy. A quarter that yields
~50 is an archive or layout failure. **If you are comparing against an older
run, ~1,600 was the healthy number until 2026-07-29** — the drop is these
rules, not a broken parse.

The rows still go through `validate.build_signal` -> `store` -> `publish`
exactly like every other source, so the credibility guards (source URL is a
receipt, figures appear in the source text, confidence capped by the source)
all still apply. Nothing is written directly.

Two SEC facts this module is built around, both learned the hard way:

1. **The dataset path is not stable.** SEC moved it from
   `/files/structureddata/data/form-d-data-sets/` to
   `/files/datastandardsinnovation/data/form-d-data-sets/` partway through
   2026, so 2026Q2 lives at a different prefix than 2026Q1. URLs are therefore
   SCRAPED from the index page, never constructed by pattern.
2. **The User-Agent must carry a contact address.** A browser-shaped UA gets
   "Request Rate Threshold Exceeded"; SEC wants to know who is calling. The
   repo default in `sec_edgar.USER_AGENT` is the right shape, and is reused
   here rather than restated.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone

import requests

from . import sec_edgar, sec_form_d

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
BASE = "https://www.sec.gov"
USER_AGENT = sec_edgar.USER_AGENT
COLLECTOR = "sec_form_d_bulk"

# The issuer filters are the search collector's own constants, imported rather
# than restated, so the two routes to the same filings cannot drift on what
# counts as an employer raising money.
EXCLUDED_INDUSTRIES = sec_form_d.EXCLUDED_INDUSTRIES
EXCLUDED_NAME_PATTERNS = sec_form_d.EXCLUDED_NAME_PATTERNS
MIN_RAISED = sec_form_d.MIN_RAISED

_QUARTER = re.compile(r"(\d{4})q([1-4])", re.I)

# Form D's own industry list, mapped onto the site's vocabulary. The Form D
# taxonomy groups by parent: "Commercial" and "Residential" are REAL ESTATE
# categories, not generic ones, which is why they are not left to a fuzzy
# match. Anything unmapped stores NULL rather than a guess — "Other" is 22% of
# qualifying filings and means exactly nothing.
#
# The real-estate rows are kept here but no longer reachable: that whole group
# is now dropped upstream as single-purpose property vehicles
# (sec_form_d.REAL_ESTATE_INDUSTRIES). They stay so the mapping is still
# correct if that filter is ever narrowed.
INDUSTRY_MAP = {
    "other technology": "technology",
    "computers": "technology",
    "telecommunications": "telecom",
    "biotechnology": "pharma_biotech",
    "pharmaceuticals": "pharma_biotech",
    "other health care": "healthcare",
    "hospitals and physicians": "healthcare",
    "health insurance": "healthcare",
    "commercial": "real_estate_construction",
    "residential": "real_estate_construction",
    "construction": "real_estate_construction",
    "reits and finance": "real_estate_construction",
    "other real estate": "real_estate_construction",
    "manufacturing": "manufacturing",
    "retailing": "retail_ecommerce",
    "restaurants": "hospitality_travel",
    "lodging and conventions": "hospitality_travel",
    "tourism and travel services": "hospitality_travel",
    "other travel": "hospitality_travel",
    "airlines and airports": "transport_logistics",
    "insurance": "financial_services",
    "commercial banking": "financial_services",
    "investment banking": "financial_services",
    "investing": "financial_services",
    "other banking and financial services": "financial_services",
    "oil and gas": "energy_utilities",
    "coal mining": "energy_utilities",
    "electric utilities": "energy_utilities",
    "energy conservation": "energy_utilities",
    "environmental services": "energy_utilities",
    "other energy": "energy_utilities",
    "business services": "professional_services",
}

# Where the issuer is, from the same two definitions the search path uses. The
# dataset's STATEORCOUNTRY / STATEORCOUNTRYDESCRIPTION columns hold the same two
# fields as the XML's stateOrCountry / stateOrCountryDescription, so the rule
# for reading them lives once, in sec_form_d, and is bound here rather than
# restated — the two routes reach the same filings and must not drift.
US_STATE_CODES = sec_form_d.US_STATE_CODES
_country_name = sec_form_d._country_name

# EDGAR's own bookkeeping, glued onto the company name: a backslash and a state
# ("Maverick Bancshares, Inc.\TX"), or a slash-wrapped marker ("BAE SYSTEMS PLC
# /FI/" for a foreign issuer). Both were rendering in the company column.
_EDGAR_NAME_SUFFIX = re.compile(r"\s*(?:\\[A-Za-z]{2,3}|/[A-Za-z]{2,3}/)\s*$")



# A Form D reports a SECURITIES OFFERING, and not every offering is a company
# raising capital. When the security is "Other", the filer describes it, and
# that description is the only place the dataset says what the filing is
# actually for. Three kinds are not capital raises at all:
#
#   1. Insurance and annuity products. A life insurer files a Form D for each
#      variable-life or annuity product it sells, and the "amount sold" is
#      premium collected from policyholders. This is the case a name filter
#      CANNOT fix: Metropolitan Life Insurance Co is a real employer, and its
#      product filings sit beside real corporate raises under the same name and
#      the same CIK. The discriminator has to be on the FILING, and this is it.
#      Measured on 2025Q4: 60 rows, $30.6bn — 41.6% of the quarter's dollars
#      after every other filter, from 3.5% of its rows.
#   2. Employee benefit plans. "Interests in a Share Incentive Plan",
#      "Participant interests in Issuers Deferred Compensation Program". The
#      money is employees', and the company raised none of it.
#   3. Club memberships. "Non-Equity Golf Memberships" is a green fee.
#
# What is deliberately NOT here: "Membership Interests", "LLC Membership units".
# Those are how an LLC describes its ordinary equity, so they ARE the raise —
# which is why this matches "golf memberships" and never "memberships" alone.
NOT_A_CAPITAL_RAISE = re.compile(
    # Insurance and annuity products.
    r"variable\s+(?:life|annuit|univ|insur)|annuit|\bvul\b|\bppvu?l\b"
    r"|insurance\s+(?:polic|contract|product)|life\s+insurance|separate\s+account"
    r"|funding\s+agreement|529\s+(?:program|plan)|health\s+savings"
    r"|guaranteed\s+(?:investment|interest)"
    # The same products in the trade's abbreviations, which is how the biggest
    # survivors of the first pass were worded: "Synthetic GICs issued to
    # insurance carriers of BOLI/COLI policies" ($4.2bn), "AGL Institutional
    # Life" ($0.6bn). Spelling out "guaranteed investment contract" caught
    # neither, and both sat at the top of the money list afterwards.
    r"|\bgics?\b|\bboli\b|\bcoli\b|institutional\s+life\b"
    # A customer buying allocated bullion is not funding a payroll:
    # "Allocated Units of Precious Metals" ($2.5bn).
    r"|allocated\s+units?\b"
    # Employee benefit plans.
    r"|(?:share|stock|equity|unit)\s+incentive\s+plan|deferred\s+compensation"
    # Club memberships, never bare "membership".
    r"|(?:golf|club|resort|social)\s+memberships?|non-?equity\s+membership",
    re.I,
)


class DatasetError(RuntimeError):
    """The index page or a quarter's archive could not be read."""


def _headers() -> dict:
    return {"User-Agent": USER_AGENT}


def dataset_urls(*, timeout: int = 60) -> dict[str, list[str]]:
    """Scrape the index page: {'2026q1': [url, ...], ...}, newest first.

    Scraped, never constructed: SEC silently moved the directory between
    2026Q1 and 2026Q2, and a pattern-built URL would have 404'd on exactly the
    quarter we most wanted. Some early quarters are split into numbered parts,
    so a quarter maps to a LIST of archives.
    """
    resp = requests.get(INDEX_URL, headers=_headers(), timeout=timeout)
    resp.raise_for_status()
    found: dict[str, list[str]] = {}
    for href in re.findall(r'href="([^"]+\.zip)"', resp.text, re.I):
        name = href.rsplit("/", 1)[-1]
        m = _QUARTER.search(name)
        if not m:
            continue
        label = f"{m.group(1)}q{m.group(2)}"
        url = href if href.startswith("http") else BASE + href
        found.setdefault(label, []).append(url)
    if not found:
        raise DatasetError(
            f"no Form D archives found on {INDEX_URL} — the page layout "
            f"changed, or the request was blocked (User-Agent: {USER_AGENT})")
    return found


def _rows(archive: zipfile.ZipFile, filename: str):
    """One TSV inside the archive, as dicts. The directory inside the zip is
    named for the quarter ('2026Q1_d/'), so members are matched by suffix."""
    name = next((n for n in archive.namelist()
                 if n.upper().endswith("/" + filename)
                 or n.upper() == filename), None)
    if not name:
        raise DatasetError(f"{filename} missing from the archive: {archive.namelist()}")
    with archive.open(name) as handle:
        text = io.TextIOWrapper(handle, "utf-8", errors="replace")
        yield from csv.DictReader(text, delimiter="\t")


def _filing_date(value: str) -> str | None:
    """'31-MAR-2026' -> '2026-03-31'. The data set uses Oracle's default
    format, which nothing downstream understands."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _amount(value: str) -> int | None:
    try:
        amount = int(float((value or "0").strip() or 0))
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _source_url(cik: str, accession: str) -> str | None:
    """The filing's own EDGAR page, never the data set archive.

    Byte-identical to the URL `sec_form_d` stores for the same filing, so a
    row this collector has already seen through the search path is skipped by
    `already_seen` instead of stored twice.
    """
    cik = (cik or "").strip().lstrip("0")
    accession = (accession or "").strip().replace("-", "")
    if not cik or not accession:
        return None
    return f"{sec_edgar.ARCHIVES}/{cik}/{accession}/primary_doc.xml"


def _tables(blob: bytes) -> tuple[dict, dict, dict]:
    """(submissions, offerings, issuers) out of one quarter's archive, each
    keyed on accession number."""
    archive = zipfile.ZipFile(io.BytesIO(blob))

    submissions = {r["ACCESSIONNUMBER"]: r for r in _rows(archive, "FORMDSUBMISSION.TSV")}
    offerings = {r["ACCESSIONNUMBER"]: r for r in _rows(archive, "OFFERING.TSV")}
    issuers: dict[str, dict] = {}
    for row in _rows(archive, "ISSUERS.TSV"):
        # A filing can name several issuers; the primary one is the employer.
        if row.get("IS_PRIMARYISSUER_FLAG") == "YES" or row["ACCESSIONNUMBER"] not in issuers:
            issuers[row["ACCESSIONNUMBER"]] = row
    return submissions, offerings, issuers


def _money_raised_exclusion(submission: dict, offering: dict) -> str | None:
    """The offering-shape rules, read off this quarter's columns.

    The rule itself lives in `sec_form_d.money_raised_exclusion`, which the
    search path calls with the same four answers read out of the XML. Only the
    column names are local — the two routes reach the same filings and must not
    disagree about whether one of them is a raise.

    SUBMISSIONTYPE and ISAMENDMENT are both read. They agreed on all 2,998
    published rows, and a rule this consequential should not rest on one column
    of a data set whose layout SEC has already moved once.
    """
    return sec_form_d.money_raised_exclusion(
        business_combination=sec_form_d.is_true(offering.get("ISBUSINESSCOMBINATIONTRANS")),
        amendment=((submission.get("SUBMISSIONTYPE") or "").strip().upper() == "D/A"
                   or sec_form_d.is_true(offering.get("ISAMENDMENT"))),
        offering_amount=offering.get("TOTALOFFERINGAMOUNT") or "",
        more_than_one_year=sec_form_d.is_true(offering.get("MORETHANONEYEAR")),
    )


def money_raised_exclusions(blob: bytes) -> dict[str, str]:
    """{filing URL: why its amount is not money raised} for one quarter.

    For the correction path, which has to tell a reader why a published row is
    being withdrawn. "The current rules no longer produce this URL" is true and
    says nothing; these three reasons name the box on the form.

    Only the offering-shape rules are reported. A row dropped as a vehicle or as
    an insurance product is not in here, and the caller falls back to its own
    wording for those — which is the wording those retractions already carry.
    """
    submissions, offerings, issuers = _tables(blob)
    out: dict[str, str] = {}
    for accession, offering in offerings.items():
        reason = _money_raised_exclusion(submissions.get(accession, {}), offering)
        if not reason:
            continue
        url = _source_url((issuers.get(accession) or {}).get("CIK", ""), accession)
        if url:
            out[url] = reason
    return out


def parse_archive(blob: bytes) -> list[dict]:
    """Every qualifying operating-company raise in one quarter's archive."""
    submissions, offerings, issuers = _tables(blob)

    out: list[dict] = []
    for accession, submission in submissions.items():
        offering = offerings.get(accession)
        issuer = issuers.get(accession)
        if not offering or not issuer:
            continue
        if (submission.get("TESTORLIVE") or "").upper() != "LIVE":
            continue

        # Vehicles, not employers. A fund raising a fund is two thirds of Form D
        # volume; a single-purpose property LLC is most of the rest of the noise.
        # The data set states the fund case three different ways and all three
        # are checked, because each one catches vehicles the others miss. The
        # industry group carries both classes (see sec_form_d.EXCLUDED_INDUSTRIES),
        # and the name patterns are the second pass for whatever files under a
        # generic group.
        if (offering.get("ISPOOLEDINVESTMENTFUNDTYPE") or "").lower() == "true":
            continue
        industry_raw = (offering.get("INDUSTRYGROUPTYPE") or "").strip()
        if industry_raw.lower() in EXCLUDED_INDUSTRIES:
            continue
        if (offering.get("INVESTMENTFUNDTYPE") or "").strip():
            continue
        # The dataset's own word for a property syndication: the security being
        # sold is an undivided interest in one building.
        if (offering.get("ISTENANTINCOMMONTYPE") or "").lower() == "true":
            continue

        # Is this filing a capital raise at all? See NOT_A_CAPITAL_RAISE. This
        # is the only check here that reads the filing rather than the issuer,
        # and it is the only one that can separate an insurer's annuity product
        # from the same insurer's actual corporate raise.
        if NOT_A_CAPITAL_RAISE.search(offering.get("DESCRIPTIONOFOTHERTYPE") or ""):
            continue

        # And is the figure money raised ON THE DATE WE DATE IT TO? Three more
        # columns, none of them about the issuer: merger consideration, an
        # amendment's cumulative total, and a continuous offering's running
        # total are all real numbers off a real filing that are not a raise
        # that happened on the filing date. See sec_form_d for each rule and
        # what it deliberately does not catch.
        if _money_raised_exclusion(submission, offering):
            continue

        raised = _amount(offering.get("TOTALAMOUNTSOLD"))
        if not raised or raised < MIN_RAISED:
            continue

        company = _EDGAR_NAME_SUFFIX.sub("", (issuer.get("ENTITYNAME") or "").strip())
        if not company or EXCLUDED_NAME_PATTERNS.search(company):
            continue

        url = _source_url(issuer.get("CIK", ""), accession)
        if not url:
            continue

        state_code = (issuer.get("STATEORCOUNTRY") or "").strip().upper()
        place = (issuer.get("STATEORCOUNTRYDESCRIPTION") or "").strip().title()
        in_us = state_code in US_STATE_CODES
        city = (issuer.get("CITY") or "").strip().title()
        money = sec_form_d._humanise(raised)
        # The exact figure, kept as its own string so the integer column is the
        # real number off the filing rather than a rounding of "$8.6M".
        exact = f"${raised:,}"
        filed = _filing_date(submission.get("FILING_DATE", ""))

        # Wording deliberately identical to sec_form_d's: the dedup hash is
        # built from the headline, so the two routes to one filing collapse to
        # a single record instead of publishing it twice.
        headline = f"{company} raised {money} in a private placement"
        body = (
            f"{company} filed a Form D with the SEC reporting {money} "
            f"({raised:,} dollars) sold in a private securities offering. "
            f"Industry: {industry_raw or 'not stated'}. "
            f"Location: {city}, {place}. "
            f"Form D filings are required for exempt offerings and are the "
            f"public record of private fundraising."
        )

        out.append({
            "raw_text": f"{headline}\n\n{body}",
            "headline": headline,
            "source_url": url,
            "source_name": "SEC EDGAR (Form D)",
            "discovery_url": url,
            "published_date": filed,
            "cik": (issuer.get("CIK") or "").strip(),
            "country": "United States" if in_us else _country_name(place),
            "state": state_code if in_us else "",
            "city": city,
            "funding_amount": exact,
            "industry_raw": industry_raw,
            "amount_usd": raised,
            "money": money,
            "accession": accession,
            "submission_type": (submission.get("SUBMISSIONTYPE") or "").strip(),
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every field here is read off the filing. No model is called, so nothing in
    a record can be something a model believed — and nothing here may be
    something WE believed either. Two rules follow, both of them fixes for
    things this source shipped:

    - `signal_direction` is "neutral", not "hiring". The rule in
      `pipeline/classify.py` is that the direction is what the SOURCE STATES
      about headcount, never what the event usually implies. A Form D states
      money. It says nothing about roles, so "Hiring up" on these rows was a
      claim the filing does not make. The pillar is still company_development
      and the figure still carries the value; only the false claim is gone.
    - The read-through states the filing and names the gap. It used to assert
      that "capital raised is spent on headcount within the following two to
      six quarters" — a generalisation that appears in no filing, printed
      identically on thousands of rows as if it had been sourced.
    """
    company = item["headline"].split(" raised ")[0]
    money = item["money"]
    where = ", ".join(p for p in (item.get("city") or "",
                                  item.get("state") or item.get("country") or "") if p)
    filed = item.get("published_date") or ""
    reported = f"{company} reported a {money} private placement to the SEC"
    if filed:
        reported += f" on {filed}"
    if where:
        reported += f", listing an address in {where}"
    return {
        "company": company,
        "pillar": "company_development",
        # Not "hiring". See the docstring: the filing states money, not roles.
        "signal_direction": "neutral",
        "headline": item["headline"],
        "summary": (
            f"{company} reported {money} sold in a private placement in a "
            f"Form D filing with the SEC."
        ),
        "talent_readthrough": (
            f"{reported}. The filing records the money only; it names no roles "
            f"and no hiring plan."
        ),
        "country": item.get("country") or "",
        "state": item.get("state") or "",
        "city": item.get("city") or "",
        "industry": INDUSTRY_MAP.get((item.get("industry_raw") or "").lower(), ""),
        "funding_amount": item.get("funding_amount") or "",
        # Earned by the source, not asserted by us: infer_confidence caps this
        # at what sec.gov is worth, which for a filing is 'verified'.
        "confidence": "verified",
    }


def collect(quarter: str, *, timeout: int = 300) -> list[dict]:
    """Every qualifying raise in one quarter, e.g. '2026q1'."""
    urls = dataset_urls().get(quarter.lower())
    if not urls:
        raise DatasetError(f"{quarter} is not published on {INDEX_URL}")
    out: list[dict] = []
    for url in sorted(urls):
        resp = requests.get(url, headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        out.extend(parse_archive(resp.content))
    return out

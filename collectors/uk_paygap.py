"""UK gender pay gap reporting — the pay pillar outside the United States.

Every UK employer with 250 or more employees must publish six pay-gap figures
each year, on a fixed snapshot date, on a government service. That makes it the
closest thing to a national, comparable, primary-source pay disclosure that
exists anywhere, and it is downloadable as one CSV per reporting year:

    https://gender-pay-gap.service.gov.uk/viewing/download-data/2025

Verified live on 2026-07-28: 11,153 employers in the 2025 file, of which 613
employ 5,000 or more. Files exist for every reporting year from 2017.

Why it earns its place here:

- **No model is involved.** The figures are columns. There is no LLM cost.
- **It is genuinely useful to a job seeker.** "Is this employer's pay gap
  better or worse than the last three years" is a question nobody can answer
  from a careers page, and this answers it for every large UK employer.
- **It fixes the geography.** The tracker's coverage read "1 country" because
  every structured source it had was American.

LICENCE / ATTRIBUTION (a condition of use, not a courtesy)

  Contains public sector information licensed under the Open Government
  Licence v3.0. The attribution statement is carried in the summary of every
  stored row, so it travels with the data to WordPress rather than living only
  in this docstring.

Two deliberate choices:

1. **The registered-office town goes in `hq_city`, never `city`.** The CSV
   carries the employer's registered address, which is not where its workforce
   sits: Tesco's registered office is in Welwyn Garden City and its employees
   are everywhere. `country` IS sourced — the duty covers an employer's GB
   employees — so these rows are GB with an unstated job location, which is
   exactly the split hq_* columns exist for.
2. **`source_url` is the employer's own report page for that year**, not the
   CSV. The CSV is a dataset; the report page is the document that makes the
   claim, and it is the page a reader can check.
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime, timezone

import requests

DOWNLOAD_URL = "https://gender-pay-gap.service.gov.uk/viewing/download-data/{year}"
REPORT_URL = ("https://gender-pay-gap.service.gov.uk/employers/{employer_id}"
              "/reporting-year-{year}/gender-pay-gap-report")
COLLECTOR = "uk_paygap"

# The service is a public one and does not block a descriptive agent, but it
# gets a real name and a contact address for the same reason SEC demands one.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

# The first reporting year the duty existed.
FIRST_YEAR = 2017

# A national CSV cannot be small. Below this the download was truncated, the
# service returned an error page, or the column names moved — none of which is
# "a quiet year", so it exits rather than storing nothing quietly.
MIN_ROWS_PER_YEAR = 1000

# The service publishes size as a band, not a number, so the filter is a band
# too. Default is the largest two: 613 employers in 2025, which is thousands of
# rows across the full backfill without burying every other source under a
# single country. Widen with TIT_PAYGAP_MIN_SIZE=1000 (or 250 for everything).
SIZE_BANDS = (
    ("250", "250 to 499"),
    ("500", "500 to 999"),
    ("1000", "1000 to 4999"),
    ("5000", "5000 to 19,999"),
    ("20000", "20,000 or more"),
)
DEFAULT_MIN_SIZE = "5000"

# UK postcode area -> a city already in the site's curated vocabulary. Only
# areas that are unambiguous are listed; everything else stores no city at all
# rather than a guess. These fill hq_city, never the job-location city.
POSTCODE_AREA_CITY = {
    "EC": "London", "WC": "London", "E": "London", "N": "London",
    "NW": "London", "SE": "London", "SW": "London", "W": "London",
    "M": "Manchester", "EH": "Edinburgh", "BT": "Belfast",
}

# SIC 2007 division (the first two digits) -> the site's industry vocabulary.
# Unmapped divisions store NULL. "Other" is not a category.
SIC_DIVISION_INDUSTRY = {
    "10": "food_beverage", "11": "food_beverage", "12": "food_beverage",
    "13": "manufacturing", "14": "manufacturing", "15": "manufacturing",
    "16": "manufacturing", "17": "manufacturing", "18": "manufacturing",
    "19": "manufacturing", "20": "manufacturing", "22": "manufacturing",
    "23": "manufacturing", "24": "manufacturing", "25": "manufacturing",
    "27": "manufacturing", "28": "manufacturing", "31": "manufacturing",
    "32": "manufacturing", "33": "manufacturing",
    "21": "pharma_biotech",
    "26": "technology", "62": "technology", "63": "technology",
    "29": "automotive", "45": "automotive",
    "30": "aerospace_defence",
    "35": "energy_utilities", "36": "energy_utilities", "37": "energy_utilities",
    "38": "energy_utilities", "39": "energy_utilities", "06": "energy_utilities",
    "41": "real_estate_construction", "42": "real_estate_construction",
    "43": "real_estate_construction", "68": "real_estate_construction",
    "46": "retail_ecommerce", "47": "retail_ecommerce",
    "49": "transport_logistics", "50": "transport_logistics",
    "51": "transport_logistics", "52": "transport_logistics",
    "53": "transport_logistics",
    "55": "hospitality_travel", "56": "hospitality_travel",
    "79": "hospitality_travel",
    "58": "media_entertainment", "59": "media_entertainment",
    "60": "media_entertainment", "90": "media_entertainment",
    "91": "media_entertainment", "92": "media_entertainment",
    "93": "media_entertainment",
    "61": "telecom",
    "64": "financial_services", "65": "financial_services",
    "66": "financial_services",
    "69": "professional_services", "70": "professional_services",
    "71": "professional_services", "72": "professional_services",
    "73": "professional_services", "74": "professional_services",
    "77": "professional_services", "78": "professional_services",
    "80": "professional_services", "81": "professional_services",
    "82": "professional_services",
    "84": "public_sector",
    "85": "education",
    "86": "healthcare", "87": "healthcare", "88": "healthcare",
}

# The two divisions that also say what KIND of employer this is. Read off the
# filed SIC code, not inferred from the name.
SIC_DIVISION_EMPLOYER_TYPE = {"84": "government", "85": "education"}

_POSTCODE_AREA = re.compile(r"^([A-Z]{1,2})\d")


class PayGapError(RuntimeError):
    """A reporting year could not be read, or came back implausibly empty."""


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"}


def latest_complete_year(today: datetime | None = None) -> int:
    """The most recent reporting year whose deadline has passed.

    A reporting year's snapshot is taken in April and employers have until the
    following April to publish, so the file for year Y is only complete from
    May of Y+1. Collecting an open year would store a third of the country and
    call it the country.
    """
    now = today or datetime.now(timezone.utc)
    return now.year - 1 if now.month >= 5 else now.year - 2


def years_from_env(default_years: list[int] | None = None) -> list[int]:
    """Which reporting years to collect. Set by the workflow, so a backfill is
    a longer list through the same path rather than a script of its own."""
    raw = (os.environ.get("TIT_PAYGAP_YEARS") or "").strip()
    if not raw:
        return list(default_years) if default_years else [latest_complete_year()]
    years: list[int] = []
    for token in re.split(r"[,\s]+", raw):
        if not token:
            continue
        digits = re.sub(r"\D", "", token)
        if len(digits) != 4:
            raise PayGapError(f"TIT_PAYGAP_YEARS holds {token!r}, which is not a year")
        year = int(digits)
        if year < FIRST_YEAR:
            raise PayGapError(
                f"{year} predates gender pay gap reporting; the first year is {FIRST_YEAR}")
        years.append(year)
    return years


def allowed_sizes(min_size: str | None = None) -> set[str]:
    """The size bands at or above the floor, as the CSV spells them."""
    floor = (min_size or os.environ.get("TIT_PAYGAP_MIN_SIZE") or DEFAULT_MIN_SIZE).strip()
    labels = [label for key, label in SIZE_BANDS]
    keys = [key for key, _label in SIZE_BANDS]
    if floor not in keys:
        raise PayGapError(
            f"TIT_PAYGAP_MIN_SIZE={floor!r} is not one of {', '.join(keys)}")
    return set(labels[keys.index(floor):])


def fetch_csv(year: int, *, timeout: int = 120) -> str:
    resp = requests.get(DOWNLOAD_URL.format(year=year), headers=_headers(), timeout=timeout)
    if resp.status_code != 200:
        raise PayGapError(
            f"{DOWNLOAD_URL.format(year=year)} returned {resp.status_code}")
    text = resp.content.decode("utf-8-sig", errors="replace")
    if "EmployerName" not in text.split("\n", 1)[0]:
        raise PayGapError(
            f"the {year} download is not the CSV it used to be — the first "
            f"line reads {text.split(chr(10), 1)[0][:120]!r}")
    return text


def _number(value: str):
    """A pay-gap percentage as the CSV prints it, or None. Never rounded: the
    string stored is the string filed."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        float(text)
    except ValueError:
        return None
    return text


def _date(value: str) -> str | None:
    """'2026/03/27 13:34:41' -> '2026-03-27'."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _first_sic_division(codes: str) -> str | None:
    for token in re.split(r"[,;\s]+", (codes or "").strip()):
        digits = re.sub(r"\D", "", token)
        if len(digits) >= 4:
            return digits[:2]
    return None


def _hq_city(postcode: str) -> str:
    m = _POSTCODE_AREA.match((postcode or "").upper().strip())
    return POSTCODE_AREA_CITY.get(m.group(1), "") if m else ""


def _gap_phrase(value: str, what: str) -> str:
    """State the direction in words. A bare '-2.91% pay gap' is read backwards
    by most people, and this data is only useful if it reads correctly."""
    number = float(value)
    if number > 0:
        return f"women's {what} is {value}% lower than men's"
    if number < 0:
        return f"women's {what} is {abs(number):g}% higher than men's"
    return f"there is no {what} gap"


def parse_csv(text: str, year: int, *, sizes: set[str] | None = None) -> list[dict]:
    """Every qualifying employer in one reporting year's file."""
    sizes = sizes if sizes is not None else allowed_sizes()
    period = f"{year}/{str(year + 1)[-2:]}"
    out: list[dict] = []
    total = 0

    for row in csv.DictReader(io.StringIO(text)):
        total += 1
        if (row.get("EmployerSize") or "").strip() not in sizes:
            continue

        name = (row.get("CurrentName") or row.get("EmployerName") or "").strip()
        employer_id = re.sub(r"\D", "", (row.get("EmployerId") or "").strip())
        median = _number(row.get("DiffMedianHourlyPercent", ""))
        mean = _number(row.get("DiffMeanHourlyPercent", ""))
        if not (name and employer_id and median is not None):
            continue

        url = REPORT_URL.format(employer_id=employer_id, year=year)
        division = _first_sic_division(row.get("SicCodes", ""))
        submitted = _date(row.get("DateSubmitted", "")) or _date(row.get("DueDate", ""))
        top_quartile_women = _number(row.get("FemaleTopQuartile", ""))
        bonus_median = _number(row.get("DiffMedianBonusPercent", ""))
        size = (row.get("EmployerSize") or "").strip()

        headline = f"{name}: {_gap_phrase(median, 'median hourly pay')} ({period})"

        parts = [
            f"{name} reported its gender pay gap for the {period} reporting "
            f"year to the UK government's gender pay gap service, as every "
            f"employer with 250 or more employees is required by law to do. "
            f"On the median measure, {_gap_phrase(median, 'hourly pay')} "
            f"({median}%)."
        ]
        if mean is not None:
            parts.append(f"On the mean measure the gap is {mean}%.")
        if top_quartile_women is not None:
            parts.append(
                f"Women are {top_quartile_women}% of the employer's "
                f"highest-paid quartile.")
        if bonus_median is not None:
            parts.append(f"The median bonus gap is {bonus_median}%.")
        parts.append(f"Reported employer size band: {size} employees.")
        parts.append(
            "Contains public sector information licensed under the Open "
            "Government Licence v3.0.")
        body = " ".join(parts)

        out.append({
            "raw_text": f"{headline}\n\n{body}",
            "headline": headline,
            "source_url": url,
            "source_name": "GOV.UK gender pay gap service",
            "discovery_url": url,
            "published_date": submitted,
            "company": name,
            "country": "United Kingdom",
            "hq_city": _hq_city(row.get("PostCode", "")),
            "industry": SIC_DIVISION_INDUSTRY.get(division or "", ""),
            "employer_type": SIC_DIVISION_EMPLOYER_TYPE.get(division or "", ""),
            "median": median,
            "mean": mean,
            "bonus_median": bonus_median,
            "top_quartile_women": top_quartile_women,
            "size_band": size,
            "period": period,
            "reporting_year": year,
            "employer_id": employer_id,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    if total < MIN_ROWS_PER_YEAR:
        raise PayGapError(
            f"the {year} file held {total} employers. A national return cannot "
            f"be that small, so the download was truncated or the columns "
            f"moved — this is a breakage, not a quiet year.")
    return out


def collect(queries=None, *, years: list[int] | None = None,
            sizes: set[str] | None = None) -> list[dict]:
    """Every qualifying employer for each requested reporting year.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect.
    """
    wanted = years if years is not None else years_from_env()
    bands = sizes if sizes is not None else allowed_sizes()
    out: list[dict] = []
    for year in wanted:
        rows = parse_csv(fetch_csv(year), year, sizes=bands)
        print(f"[{COLLECTOR}] {year}: {len(rows)} employers at or above the size floor")
        if not rows:
            raise PayGapError(
                f"{year} produced no qualifying employers. The size bands are "
                f"spelled differently, or the filter is wrong.")
        out.extend(rows)
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated."""
    name = item["company"]
    summary_parts = [
        f"{name} reported a median hourly gender pay gap of {item['median']}% "
        f"for the {item['period']} reporting year under UK gender pay gap "
        f"regulations."
    ]
    if item.get("mean") is not None:
        summary_parts.append(f"The mean gap is {item['mean']}%.")
    if item.get("top_quartile_women") is not None:
        summary_parts.append(
            f"Women are {item['top_quartile_women']}% of its highest-paid quartile.")
    summary_parts.append(
        "Contains public sector information licensed under the Open "
        "Government Licence v3.0.")

    return {
        "company": name,
        "pillar": "rewards_comp",
        "signal_direction": "comp_shift",
        "headline": item["headline"],
        "summary": " ".join(summary_parts),
        "talent_readthrough": (
            "A pay gap is a distribution fact, not a pay level: it says how "
            "the employer's men and women are spread across its pay grades, "
            "so a large gap usually means few women in senior roles rather "
            "than unequal pay for the same job. The quartile split is the "
            "part worth reading, and the year-on-year direction says more "
            "than any single year's number."
        ),
        "country": item.get("country") or "",
        # The registered office, kept apart from job location on purpose: this
        # is the employer's address, not where the reported workforce sits.
        "headquarters_city": item.get("hq_city") or "",
        "headquarters_country": "United Kingdom",
        "industry": item.get("industry") or "",
        "employer_type": item.get("employer_type") or "",
        # A statutory return published by the government service that collects
        # it. infer_confidence caps this at what the host is worth, and until
        # the service is listed in vocab.PRIMARY_SOURCE_DOMAINS that cap lands
        # it at 'reported' — lower than it deserves, never higher.
        "confidence": "verified",
    }

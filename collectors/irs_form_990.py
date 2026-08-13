"""IRS Form 990 executive compensation. The pay pillar's tax exempt half.

`sec_execcomp` reaches every US domestic registrant and structurally reaches
nothing else, so the pay pillar can see Apple and cannot see a teaching
hospital, a university or a research institute. Those employers file a Form
990, Congress made the officer pay on it public precisely so it would be read,
and the IRS publishes every return as e-file XML in monthly batches:

    https://www.irs.gov/charities-non-profits/form-990-series-downloads

SHIPPED DORMANT. Nothing schedules it. Arming it is a separate decision with
its own cost, and the download volume in `batch_urls` is most of that cost.

THE RECEIPT, WHICH IS WHY THIS TOOK A SCOPING PASS AND A BUILD PASS
-------------------------------------------------------------------
`docs/SCOPE-us-pay-filings.md` ranked this source first and blocked it on one
thing: no citable per filing URL. That verdict was right about the two routes
it tried and wrong about the source. Measured 2026-08-13:

- `https://apps.irs.gov/pub/epostcard/990/xml/{year}/{OBJECT_ID}_public.xml`
  404s. Confirmed on object IDs taken from a batch zip that demonstrably
  holds the file.
- The AWS S3 bucket `irs-form-990` still exists and is publicly listable and
  is EMPTY: `?max-keys=20` returns `<IsTruncated>false</IsTruncated>` with no
  keys, and a known legacy object 404s. That route is gone, not moved.
- `https://apps.irs.gov/app/eos/` 403s to any automated client, and its
  details page has no per organisation URL at all: the whole search lands on
  `https://apps.irs.gov/app/eos/details/` with the state held server side. It
  is unlinkable even from a browser.

What DOES exist is the Tax Exempt Organization Search "Copies of Returns"
database, which serves the scanned return itself from a static path:

    GET  https://apps.irs.gov/teos/details/returnsSearch/{EIN}
    ->   {"items":[{"TAX_PERIOD":"202407", "RETURN_TYPE":"990",
                    "STATICFILEPATH":"/pub/epostcard/cor/310707369_202407_990_2025081423655359.pdf"}]}

That JSON answers this collector's own descriptive User-Agent with HTTP 200,
and the PDF it names answers 200 as `application/pdf`. Neither is behind the
bot wall that stops `/app/eos`. The filename cannot be composed: its 16 digit
tail is an IRS posting date plus the return id, and the posting date appears
in no published file. So the URL is LOOKED UP, once per organisation, and a
filing whose lookup finds nothing is DROPPED rather than cited to the zip.

Measured receipt rate, 100 randomly sampled form 990 filings per index year:

    index_2025   100/100     index_2024   55/60     index_2026   19/100

The 2026 figure is not a defect, it is the reason for `latest_complete_year`.
TEOS posts the return copy months after the XML batch, so the current year is
a year of rows that cannot be sourced. Collecting it would be collecting
nothing, loudly.

WHAT A ROW CLAIMS, WHICH IS NARROWER THAN "PAY AT A HOSPITAL"
------------------------------------------------------------
One row is: the largest single compensation figure any officer, director,
trustee or key employee of this organisation was reported to receive FROM the
organisation, with the title as filed, for the calendar year ending with or
within the tax year that ended on the stated date.

Four things that is not, all of them mistakes that were available here:

1. **Not the chief executive's pay.** The highest paid person on Part VII is
   frequently not the chief executive: in the batch this was measured on it
   is `HEAD COACH, BASKETBALL` at Pepperdine, `MUSIC DIRECTOR` at the
   Metropolitan Opera and `CHAIR/PHYSICIAN` at Greater Baltimore Medical
   Center. Picking "the CEO" would mean matching titles, which is inventing.
   The row says highest paid, because that is what was computed.
2. **Not current pay.** Part VII states compensation for the calendar year
   ending with or within the tax year, so a return for the tax year ended
   2024-07-31 carries calendar 2023 pay, and returns arrive up to about 18
   months after that. `published_date` is the tax period end, the way
   `sec_execcomp` dates by period end, so the figure sits on the year it
   describes rather than the day we fetched it.
3. **Not an average, and never divided by anything.**
   `CYSalariesCompEmpBnftPaidAmt / TotalEmployeeCnt` is arithmetic and not a
   salary: the numerator bundles benefits and pension, the denominator counts
   part time and seasonal staff. Both figures are stored as filed and neither
   is combined with the other.
4. **Not a named person.** The return names the individual and this record
   does not, which is `sec_execcomp`'s posture exactly: that collector stores
   "the principal executive officer" and never the officer. The scoping pass
   refused state payroll portals largely because they are person level data,
   and a source that is right about hospitals does not get a different rule.
   The title is filed and is stored; the name is filed and is dropped.

THE POPULATION, AND WHY THERE IS A FLOOR
----------------------------------------
376,920 long form 990s were filed in index year 2025. Unfiltered this source
is thirteen times the whole database and all of it American, which is the
takeover `uk_paygap.DEFAULT_MIN_SIZE` exists to prevent.

At the default floor, measured by running this parser over two whole batches,
2025_TEOS_XML_01A and 06A, which hold 31,706 long form returns between them
(8.4% of the year): 147 storable rows, 0.464%. Across the year's 376,920 that
is about **1,750 rows a year**. Against 29,329 current rows of which 8,832 are
rewards_comp, one year moves the pay pillar from 30.1% of the database to
34.0%, which is depth. Three years of backfill takes it to 40.7%, which is a
decision somebody should make on purpose rather than discover.

Scaled from the 06A batch alone, the other floors are roughly:

    CYTotalRevenueAmt >= $25M    ~8,800 filings/yr
    CYTotalRevenueAmt >= $50M    ~4,300 filings/yr
    CYTotalRevenueAmt >= $100M   ~1,750 filings/yr   <- default, two-batch
    CYTotalRevenueAmt >= $250M     ~770 filings/yr

Revenue rather than `TotalEmployeeCnt`, and that choice is measured too. At a
1,000 employee floor the 06A batch is 96 filings of which roughly 40% are
YMCAs and Goodwills, because that field counts every seasonal and part time
W-2. At the $100M revenue floor the same batch is 20.4% hospitals and health
systems, 16.8% universities, colleges and schools, 2.7% research institutes
and 4.4% YMCA-shaped, which is the coverage this source was chosen for.

THE EMPLOYER JOIN, WHICH IS ZERO AND IS FINE
--------------------------------------------
`pipeline.vocab.company_key` matched 0 of the 96 filers at 1,000 employees,
0 of 227 at 500, and 1 of 526 at 250 against every employer the tracker
holds. The one match is `Midwest Energy Inc`, a Kansas electric cooperative,
colliding with the `Midwest Energy Ltd` we already store. So the measured
join is 0 correct matches in 526 and one wrong one, and adding an EIN column
to `employer_identity` would buy nothing that exists today: there is no
second EIN carrying source built, and these are new employers rather than
better keys for old ones. The EIN is not thrown away either. It is the first
field of the receipt URL, so `310707369` is recoverable from any stored row
without a schema change on the day a second EIN source lands.

No model is called anywhere on this path. Every field is a tagged XML element
or a fixed editorial line, so this collector exposes `as_classified` and costs
nothing per row.

ROBOTS. `www.irs.gov/robots.txt` carries no disallow for `/pub/` or for the
downloads page; it does disallow `/charities-non-profits/tax-exempt-
organization-search`, which is the www landing page and is not a path this
collector fetches. `apps.irs.gov/robots.txt` answers 503 through Akamai, so
`national_press.robots_allows` reads it as no restriction, which is the
standard reading and the same one every other collector here uses. Nothing is
fetched from `/app/eos`, the one path that actually refuses automated
clients.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

DOWNLOADS_PAGE = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"
HOST = "https://apps.irs.gov"
TEOS_DETAILS = HOST + "/teos/details/returnsSearch/{ein}"
COLLECTOR = "irs_form_990"
SOURCE_NAME = "IRS Form 990 (Tax Exempt Organization Search)"

# A real name and a contact address, for the same reason SEC demands one.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

NS = "{http://www.irs.gov/efile}"

# The first index year the downloads page enumerates completely. 2023, 2024
# and 2025 list 12, 12 and 16 batch zips; 2021 and 2022 list one each, which is
# the page collapsing an older section rather than a year with one batch. A
# year this collector cannot enumerate is refused rather than half collected.
FIRST_YEAR = 2023
MIN_BATCHES_PER_YEAR = 10

# The two return types on the copies-of-returns database that ARE a Form 990.
# `990` is the 501(c)(3) return and `990O` is the same form filed by another
# 501(c) class; both are the document this collector reads.
#
# `990T` is NOT. It is the unrelated business income tax return, a different
# form with no Part VII, and a great many health systems file one for the same
# tax period as their 990. A prefix match on "990" cited it: the first dry run
# put nine McLaren hospital rows and two others on a 990-T URL, which links a
# reader to a real IRS document that does not contain the figure on the row.
FORM_990_RETURN_TYPES = frozenset({"990", "990O"})

# A national filing year cannot be small. Below this the page changed shape,
# the download truncated, or the revenue floor is wrong -- none of which is
# "a quiet year", so it raises rather than storing nothing quietly.
MIN_ROWS_PER_YEAR = 50

# See the docstring. Override with TIT_FORM990_MIN_REVENUE.
DEFAULT_MIN_REVENUE = 100_000_000

# Politeness on the receipt lookup. One organisation, one request, one second.
RECEIPT_PAUSE = 1.0

_BATCH_HREF = re.compile(
    r'href="(https://apps\.irs\.gov/pub/epostcard/990/xml/(\d{4})/[^"]+\.zip)"')


class Form990Error(RuntimeError):
    """A year could not be read, or came back implausibly empty."""


def _headers(accept: str = "*/*") -> dict:
    return {"User-Agent": USER_AGENT, "Accept": accept}


def _text(el, tag: str) -> str:
    found = el.find(NS + tag) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _int(value: str):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --- which years, and which files inside them ------------------------------

def latest_complete_year(today: datetime | None = None) -> int:
    """The most recent index year whose returns have a citable copy.

    The IRS posts the e-file XML for a return months before Tax Exempt
    Organization Search posts the copy a reader can open, so the current year
    is a year of rows this collector would have to drop. Measured on 2026-08-13:
    100 of 100 sampled 2025 filings had a receipt, 19 of 100 sampled 2026
    filings did. Collecting the open year is not a fuller year, it is a longer
    log of NO RECEIPT.
    """
    now = today or datetime.now(timezone.utc)
    return now.year - 1


def years_from_env(default_years: list[int] | None = None) -> list[int]:
    """Which index years to collect. Set by the workflow, so a backfill is a
    longer list through the same path rather than a script of its own."""
    raw = (os.environ.get("TIT_FORM990_YEARS") or "").strip()
    if not raw:
        return list(default_years) if default_years else [latest_complete_year()]
    years: list[int] = []
    for token in re.split(r"[,\s]+", raw):
        if not token:
            continue
        digits = re.sub(r"\D", "", token)
        if len(digits) != 4:
            raise Form990Error(f"TIT_FORM990_YEARS holds {token!r}, which is not a year")
        year = int(digits)
        if year < FIRST_YEAR:
            raise Form990Error(
                f"{year} is not enumerable from the downloads page; the first "
                f"year this collector will read is {FIRST_YEAR}")
        years.append(year)
    return years


def min_revenue(value=None) -> int:
    """The revenue floor, in whole dollars. See the docstring for the
    measurement behind the default."""
    raw = value if value is not None else os.environ.get("TIT_FORM990_MIN_REVENUE")
    if raw in (None, ""):
        return DEFAULT_MIN_REVENUE
    floor = _int(str(raw).replace(",", "").replace("$", ""))
    if floor is None or floor < 0:
        raise Form990Error(
            f"TIT_FORM990_MIN_REVENUE={raw!r} is not a whole number of dollars")
    return floor


def parse_batch_urls(html: str, year: int) -> list[str]:
    """Every batch zip the downloads page lists for one index year.

    Read off the page rather than composed, because the file naming has already
    changed once (`download990xml_2019_4.zip` became `2019_TEOS_XML_04A.zip`)
    and the batch letters are not a sequence: 2025 has 11A, 11B, 11C and 11D.
    """
    urls = sorted({url for url, found in _BATCH_HREF.findall(html)
                   if int(found) == year})
    if len(urls) < MIN_BATCHES_PER_YEAR:
        raise Form990Error(
            f"the downloads page lists {len(urls)} batch file(s) for {year}. A "
            f"complete year is twelve or more, so this is the page changing "
            f"shape or the year still being published, not a quiet year.")
    return urls


def batch_urls(year: int, *, session=None, timeout: int = 60) -> list[str]:
    resp = (session or requests).get(DOWNLOADS_PAGE, headers=_headers("text/html"),
                                     timeout=timeout)
    if resp.status_code != 200:
        raise Form990Error(f"{DOWNLOADS_PAGE} returned {resp.status_code}")
    return parse_batch_urls(resp.text, year)


def _cache_dir():
    raw = (os.environ.get("TIT_FORM990_CACHE") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_batch(url: str, *, session=None, timeout: int = 900) -> Path:
    """One batch zip on local disk. Roughly 200MB each, fifteen or so a year.

    `TIT_FORM990_CACHE=<dir>` keeps them, which is what stops a second year, a
    re-run or a dry run re-downloading gigabytes from a government host for
    files that never change once published.
    """
    name = url.rsplit("/", 1)[-1]
    cache = _cache_dir()
    if cache is not None and (cache / name).exists():
        return cache / name
    target = (cache or Path(os.environ.get("TMPDIR") or "/tmp")) / name
    with (session or requests).get(url, headers=_headers(), timeout=timeout,
                                   stream=True) as resp:
        if resp.status_code != 200:
            raise Form990Error(f"{url} returned {resp.status_code}")
        with open(target, "wb") as handle:
            for chunk in resp.iter_content(1 << 20):
                handle.write(chunk)
    return target


# --- one filing ------------------------------------------------------------

def _address(filer) -> tuple[str, str]:
    """(country, us_state) as the return states it, never inferred.

    Foreign filers are real here and are not rare: Oxford University Press, the
    University of Calgary and the Hebrew University of Jerusalem all file a US
    Form 990. Assuming the United States would put three foreign employers on
    the American map on the first batch.
    """
    us = filer.find(NS + "USAddress")
    if us is not None:
        return "United States", _text(us, "StateAbbreviationCd")
    foreign = filer.find(NS + "ForeignAddress")
    if foreign is not None:
        return _text(foreign, "CountryCd"), ""
    return "", ""


def highest_paid(f990) -> tuple[int, str]:
    """(largest reported compensation from the organisation, title as filed).

    Part VII Section A lists officers, directors, trustees, key employees and
    the five highest paid employees over $100,000. This reads
    `ReportableCompFromOrgAmt`, which is that person's W-2 box 1 pay from this
    organisation, and nothing else: related organisation pay and other
    compensation are separate columns describing separate money, and adding
    them would be computing a total the return does not state.

    The NAME on the row is deliberately not returned. See the module docstring.

    A Part VII row is NOT always a person, and the largest figure on the sheet
    is exactly where that bites. The Bank of America Charitable Gift Fund's
    2023 return carries $20,052,864 against `<BusinessName>BANK OF AMERICA` with
    `InstitutionalTrusteeInd` set: a corporate trustee's fee, filed in the same
    column as an officer's salary, and forty times the largest real pay figure
    in the same batch. The return itself makes the distinction, so this reads
    it rather than guessing at the title: a group with no `PersonNm` is skipped.
    """
    best, title = 0, ""
    for group in f990.iter(NS + "Form990PartVIISectionAGrp"):
        if not _text(group, "PersonNm"):
            continue
        amount = _int(_text(group, "ReportableCompFromOrgAmt"))
        if amount is not None and amount > best:
            best, title = amount, _text(group, "TitleTxt")
    return best, title


def parse_filing(xml_bytes: bytes, *, floor: int) -> dict | None:
    """One return as a raw dict, or None when it is not one of ours.

    Returns None rather than raising for everything ordinary: a 990-EZ, a
    990-PF, a return under the revenue floor, a return whose Part VII carries
    no compensation at all. Those are not breakages and a batch is full of them.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    header = root.find(NS + "ReturnHeader")
    if header is None or _text(header, "ReturnTypeCd") != "990":
        return None
    data = root.find(NS + "ReturnData")
    f990 = data.find(NS + "IRS990") if data is not None else None
    filer = header.find(NS + "Filer")
    if f990 is None or filer is None:
        return None

    revenue = _int(_text(f990, "CYTotalRevenueAmt"))
    if revenue is None or revenue < floor:
        return None

    name_el = filer.find(NS + "BusinessName")
    name = " ".join(part for part in (_text(name_el, "BusinessNameLine1Txt"),
                                      _text(name_el, "BusinessNameLine2Txt")) if part)
    ein = re.sub(r"\D", "", _text(filer, "EIN"))
    period_end = _text(header, "TaxPeriodEndDt")
    if not (name and len(ein) == 9 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_end)):
        return None

    amount, title = highest_paid(f990)
    if amount <= 0:
        # A return with no Part VII compensation figure has nothing to say to
        # the pay pillar. 14 of the 113 filings above the floor in the measured
        # batch are like this, mostly benefit trusts with no paid officers.
        return None

    country, state = _address(filer)
    employees = _int(_text(f990, "TotalEmployeeCnt")) or 0
    payroll = _int(_text(f990, "CYSalariesCompEmpBnftPaidAmt"))

    exact = f"${amount:,}"
    revenue_exact = f"${revenue:,}"
    # A title is filed free text and is occasionally blank or a job description.
    role = title.strip() or "an officer, director, trustee or key employee"

    headline = (f"{name}: {exact} reported for its highest paid officer or key "
                f"employee, tax year ended {period_end}")
    body_parts = [
        f"{name} reported {exact} in compensation from the organisation to the "
        f"highest paid individual on Part VII of its Form 990 for the tax year "
        f"ended {period_end}. The title stated for that person is {role}. "
        f"Part VII reports compensation for the calendar year ending with or "
        f"within the tax year, so the figure is prior calendar year pay rather "
        f"than pay today, and it covers officers, directors, trustees, key "
        f"employees and the five highest paid employees over $100,000 and "
        f"nobody else at the organisation.",
        f"The organisation reported {revenue_exact} in total revenue for the "
        f"same year.",
    ]
    if employees > 0:
        body_parts.append(
            f"It reported {employees:,} employees, which counts everyone it "
            f"issued a W-2 to, including part time and seasonal staff.")
    if payroll is not None:
        body_parts.append(
            f"Its total salaries, other compensation and employee benefits for "
            f"the year were ${payroll:,}. That figure bundles benefits and "
            f"pension, so dividing it by the employee count is arithmetic and "
            f"not an average salary.")
    body_parts.append(
        "The return names the individual paid this amount. This record does "
        "not, for the same reason the SEC pay source stores the principal "
        "executive officer and never the officer.")

    return {
        "raw_text": headline + "\n\n" + " ".join(body_parts),
        "headline": headline,
        "source_name": SOURCE_NAME,
        "company": name,
        "ein": ein,
        "country": country,
        "state": state,
        "published_date": period_end,
        "period_end": period_end,
        "tax_period": period_end.replace("-", "")[:6],
        "amount_usd": amount,
        "amount_exact": exact,
        "title": role,
        "revenue_usd": revenue,
        "revenue_exact": revenue_exact,
        "employees": employees,
        "payroll_usd": payroll,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the receipt -----------------------------------------------------------

def receipt_url(ein: str, tax_period: str, *, session=None, timeout: int = 45,
                cache: dict | None = None) -> str | None:
    """The URL of the return itself, or None.

    One request per ORGANISATION rather than per filing, because an EIN's whole
    filing history comes back in one answer and a backfill of several years
    asks for the same EIN once. None means this filing has no copy posted yet,
    which is a reason to drop the row and never a reason to cite the batch zip.
    """
    ein = re.sub(r"\D", "", ein or "")
    if len(ein) != 9:
        return None
    if cache is not None and ein in cache:
        items = cache[ein]
    else:
        resp = (session or requests).get(TEOS_DETAILS.format(ein=ein),
                                         headers=_headers("application/json"),
                                         timeout=timeout)
        if resp.status_code != 200:
            raise Form990Error(
                f"{TEOS_DETAILS.format(ein=ein)} returned {resp.status_code}. "
                f"The copy of returns lookup is the only citable URL this "
                f"source has, so this is a breakage and not a missing filing.")
        try:
            items = resp.json().get("items") or []
        except (ValueError, json.JSONDecodeError):
            raise Form990Error(
                f"the copy of returns lookup for EIN {ein} did not return JSON")
        if cache is not None:
            cache[ein] = items

    paths = []
    for item in items:
        if str(item.get("TAX_PERIOD") or "") != tax_period:
            continue
        if str(item.get("RETURN_TYPE") or "") not in FORM_990_RETURN_TYPES:
            continue
        path = str(item.get("STATICFILEPATH") or "")
        if not path.startswith("/pub/epostcard/cor/"):
            # A path anywhere else is the API changing under us, and a guessed
            # host is exactly how a receipt stops being one.
            continue
        paths.append(path)
    if not paths:
        return None
    # An organisation can have TWO copies posted for one tax period, an original
    # and a later one, and the API returns them in no useful order. The tail of
    # the filename is the IRS posting date followed by the return id, so the
    # largest string is the most recently posted copy of that period's return.
    # Citing the earlier one would link a reader to a superseded document.
    return HOST + max(paths)


# --- the run ---------------------------------------------------------------

def collect(queries=None, *, years: list[int] | None = None, floor: int | None = None,
            session=None, max_batches: int | None = None) -> list[dict]:
    """Every qualifying return for each requested index year.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the year's
    batches are the whole population.

    `TIT_FORM990_MAX_BATCHES=1` reads one batch instead of the year, which is
    what a dry run wants: a batch is a month of filings and about 200MB, a year
    is fifteen of them.
    """
    session = session or requests.Session()
    wanted = years if years is not None else years_from_env()
    limit = max_batches
    if limit is None:
        limit = _int(os.environ.get("TIT_FORM990_MAX_BATCHES") or "") or None
    bar = min_revenue(floor)

    out: list[dict] = []
    receipts: dict[str, list] = {}
    for year in wanted:
        urls = batch_urls(year, session=session)
        chosen = urls[:limit] if limit else urls
        print(f"[{COLLECTOR}] {year}: {len(urls)} batch file(s), reading "
              f"{len(chosen)}, revenue floor ${bar:,}")
        above = dropped = 0
        for url in chosen:
            path = fetch_batch(url, session=session)
            with zipfile.ZipFile(path) as archive:
                members = [n for n in archive.namelist() if n.endswith("_public.xml")]
                for member in members:
                    item = parse_filing(archive.read(member), floor=bar)
                    if item is None:
                        continue
                    above += 1
                    time.sleep(RECEIPT_PAUSE if item["ein"] not in receipts else 0)
                    url_for_row = receipt_url(item["ein"], item["tax_period"],
                                              session=session, cache=receipts)
                    if not url_for_row:
                        dropped += 1
                        print(f"  NO RECEIPT  {item['company'][:52]} "
                              f"({item['period_end']}): no copy of this return "
                              f"is posted, so it cannot be cited and is dropped")
                        continue
                    item["source_url"] = url_for_row
                    item["discovery_url"] = url_for_row
                    out.append(item)
            print(f"[{COLLECTOR}]   {path.name}: {len(members):,} returns, "
                  f"{above} above the floor so far, {dropped} dropped for no receipt")
        if not limit and above and len(out) < MIN_ROWS_PER_YEAR:
            raise Form990Error(
                f"{year} produced {len(out)} storable returns from {above} above "
                f"the ${bar:,} floor. A complete year holds thousands, so the "
                f"receipt lookup is failing rather than the year being quiet.")
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is read off the return or is a fixed editorial line, so nothing
    on the record can be something a model believed.
    """
    name = item["company"]
    parts = [
        f"{name} reported {item['amount_exact']} in compensation from the "
        f"organisation to the highest paid individual on Part VII of the Form "
        f"990 it filed with the IRS for the tax year ended {item['period_end']}, "
        f"under the title {item['title']}.",
        f"Part VII states pay for the calendar year ending with or within the "
        f"tax year, so this is prior calendar year pay and not pay today.",
        f"The organisation reported {item['revenue_exact']} in total revenue.",
    ]
    if item.get("employees"):
        parts.append(f"It reported {item['employees']:,} employees.")
    parts.append("The return names the individual; this record does not.")

    return {
        "company": name,
        "pillar": "rewards_comp",
        # Not "hiring": a pay disclosure moves the compensation picture, it
        # does not say anyone is being hired.
        "signal_direction": "comp_shift",
        "headline": item["headline"],
        "summary": " ".join(parts),
        "talent_readthrough": (
            "This is the only comparable filed pay figure for the employers "
            "the SEC route cannot see, which is most hospitals, universities, "
            "research institutes and large charities. Read it as a ceiling "
            "rather than a salary band: it is the largest single figure on the "
            "return, it is frequently not the chief executive, and everyone at "
            "the organisation outside the officers and the five highest paid "
            "employees is invisible on it. The employee count beside it counts "
            "part time and seasonal staff, so the two figures do not divide."
        ),
        "country": item.get("country") or "",
        "state": item.get("state") or "",
        # A tax exempt organisation, filed as one. Nothing narrower is stated on
        # the return: it carries no industry code, so `industry` stays empty
        # rather than being guessed from the organisation's name.
        "employer_type": "nonprofit",
        "headcount": item.get("employees") or None,
        "headcount_scope": "total_workforce",
        # Earned by the source, not asserted by us: infer_confidence caps this
        # at what the host is worth, and until apps.irs.gov is listed in
        # vocab.PRIMARY_SOURCE_DOMAINS that cap lands it at 'reported'.
        "confidence": "verified",
    }

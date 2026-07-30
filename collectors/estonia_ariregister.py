"""Estonian business register — appointments only, and the file says so itself.

The Centre of Registers and Information Systems publishes the whole business
register as a daily open-data file, and every person on a company's registry
card carries `algus_kpv`, the date that person's office BEGAN. That is a
source-stated appointment date for the entire country, keyless, in one
download.

    https://avaandmed.ariregister.rik.ee/sites/default/files/avaandmed/
        ettevotja_rekvisiidid__kaardile_kantud_isikud.json.zip

THE LOAD-BEARING NEGATIVE, MEASURED ON THE WHOLE FILE 2026-07-30
================================================================

**`lopp_kpv` is null in 520,895 of 520,895 person rows. Zero end dates, ever.**

The file holds CURRENT holders only, which the download page states and the
data confirms: when somebody leaves, their row leaves with them. So Estonia
yields **appointments and never departures**, and no amount of care changes
that. It is written into the summary and the read-through of every stored row,
into the sources-page note and into this docstring, because a leadership feed
that silently reports only arrivals reads as a country where nobody ever
leaves.

The one route to departures is `arireg.ettevotjaMuudatusedTasuline_v1`, the
SOAP change-list service. It needs an account and it is *tasuline* — chargeable.
Refused: this repository's whole cost discipline is that structured sources are
free, and a paid feed for one small country is not the place to break it.

**Do not try to recover departures by diffing yesterday's file against
today's.** A row that vanished may be a departure, a correction, a merger or a
company leaving the register, the file states no date for any of it, and a
stamped date the source never stated is exactly what "the model never invents a
number" forbids. Korea's roster endpoints were refused for the same reason.

VOLUME, AND WHY THERE HAS TO BE A THRESHOLD
--------------------------------------------

Measured on the 2026-07-30 file: **520,895 person rows across 375,305
companies**, and **18,155 appointments in the 90 days to 2026-07-30 — 202 a
day, about 74,000 a year.** From a country of 1.3 million people. That is four
times India's whole leadership feed, and it is dominated by one-person `OÜ`
micro-companies: `JUHL`, board member, is 446,636 of the 520,895 rows, and the
median Estonian company on this file has one of them and nothing else.

Collecting it unfiltered is the failure this tracker has already refused twice
(the UK register, 5.9 million companies; Spain). So the population is not the
register.

**Estonia publishes employee counts, and the threshold is drawn on somebody
else's definition rather than ours.** The annual-report open data carries
`AverageNumberOfEmployeesInFullTimeEquivalentUnits` per report, joined to a
company through `report_id`:

    4.<year>_aruannete_elemendid_kuni_<date>.zip   element -> value
    1.aruannete_yldandmed_kuni_<date>.zip          report_id -> registrikood

Measured on the 2025 reports: 3,006,385 element rows hold **194,930** employee
figures, joining to **194,851** companies. At each candidate floor:

| full-time equivalent employees | companies | appointments in 365 days |
|---|---|---|
| 10 or more | 5,449 | 808 |
| 25 or more | 1,878 | 384 |
| **50 or more** | **825** | **235** |
| 100 or more | 368 | 119 |
| 250 or more | 107 | **38** |

**The floor is 50**, and it is EU Recommendation 2003/361's own boundary: micro
under 10, small under 50, medium 50 to 249, large 250 and above. At 50 an
Estonian business has stopped being a small enterprise by the definition the
Commission publishes and Estonia applies.

**250 was tried first and refused with its number.** It is the line
`companies_house` and `czechia_ares` draw, and in a country this size it yields
38 appointments a year — under one a week, so most weekly runs would store
nothing and a collector that returns zero is `degraded` by this repository's own
rule. The threshold that matches the UK's *letter* would produce a connector
that is broken most weeks. 50 matches its intent: an employer large enough that
who runs it is a fact about a labour market rather than about one person's
company.

Measured at the chosen floor, over 2026-05-01..07-30 (91 days): **66
appointments**, all of them `JUHL` or `PROK`, at employers from Bondora (54)
to BAUHOF GROUP (492). About **5 a week**, ~265 a year.

WHAT THE THRESHOLD COSTS, SAID PLAINLY
---------------------------------------

* A company with **no 2025 annual report** has no employee figure and is
  excluded. That is every company incorporated since the last reporting cycle,
  so a fast-growing new employer is invisible here until it files.
* The report files are **frozen at "kuni 30.06.2026"**. They are a snapshot, not
  a feed, so the employee figure ages while the appointments do not.
  `discover_report_files` reads the download page for the current filenames
  rather than hard-coding the date, because that date is the publisher's own
  versioning and a hard-coded URL would 404 into a silent zero on the day it
  moves.

WHAT IS NOT COLLECTED, AND WHY EACH IS A DECISION
--------------------------------------------------

* **A legal person is not an employee.** `isiku_tyyp` `J` is a company entered
  on another company's card — Nasdaq CSD SE appears as `ORP` at Enefit
  Industry. 26,442 of 520,895 rows. Same judgement as `companies_house`'s
  `corporate-*` roles.
* **Insolvency offices.** `LIKV`, `LIKVJ`, `PANKR`, `AJUTPH`, `AJPH`, `JPNKR`
  and `ERIH` are liquidators and bankruptcy trustees. A court appointing
  somebody to wind a company up is not a hire, and the workforce consequence of
  it is the sibling tracker's scope.
* **Owners.** `TOSAN`, `UOSAN`, `YHL`, `YHLLV`, `EUSOS`, `EUSOS2` are partners
  and members. Holding a stake is not holding an office.
* **Institutional and administrative entries.** `ORP`/`ARP` (share-register
  keepers), `FV` (fund manager), `KISIK` (contact person), `MDKPI` (agent for
  service of documents), `DOKH`, `ASES`, `KOAS`, `SJESI`, `ESIS`, `ESIS2`,
  `VALIT`, `ETTEV`, `FIE` (a sole trader IS the business).
* **A direction of `hiring`.** The register records that an office began and
  nothing about where the person came from. Every row is `neutral`, the same
  rule `companies_house` and `czechia_ares` apply.
* **A city.** The persons file carries no company address at all, and the file
  that does is another 30MB download for a country whose employers are almost
  all in one city. Guessing Tallinn would be inventing a place, so nothing here
  states one.

PERSONAL DATA: TAKEN AT THE BOUNDARY, NEVER PERSISTED
-------------------------------------------------------

The file carries a residential address on 60,930 person rows
(`aadress_tanav_maja_korter`, `aadress_postiindeks`,
`aadress_ads__ads_normaliseeritud_taisaadress`), a birth date (`synniaeg`) on
16,099, an email on 14,360 and a pseudonymous national-ID hash
(`isikukood_hash`) on 485,719. **`scrub_person` is the only way a person reaches
a row from here**, and it returns given name, surname and the role. Everything
else is dropped inside the collector before a dict is built, so no later stage
can leak what it never received. Asserted by `tests/test_estonia_ariregister.py`.

ACCESS AND LICENCE
------------------

`avaandmed.ariregister.rik.ee/robots.txt` is Drupal's default and does not
disallow `/sites/default/files/`. The open data is licensed **CC BY 4.0**,
stated on the terms-of-service page, and the attribution travels in the summary
of every stored row.

`source_url` is `ariregister.rik.ee/eng/company/{registrikood}`, the register's
own English page for that company, and it is distinguishable: a real code
answers HTTP 200 with the company's page and an invented one answers HTTP 303,
verified live on 2026-07-30. `ariregister.rik.ee/robots.txt` permits that path
(what it disallows is query strings, `/api/`, `/cart/`, `/tab/documents` and the
document-file routes) and sets `Crawl-delay: 10`, which is a fact about
`link_check.py` rather than about this collector: nothing here fetches that host
at all. One company has one such URL and can appoint many people, so
`REVISITS_ITS_SOURCE_URL` is set and dedup happens on `content_hash`.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

SITE = "https://avaandmed.ariregister.rik.ee"
DOWNLOAD_PAGE = SITE + "/en/downloading-open-data"
PERSONS_URL = (SITE + "/sites/default/files/avaandmed/"
                      "ettevotja_rekvisiidid__kaardile_kantud_isikud.json.zip")
REGISTER = "https://ariregister.rik.ee/eng/company/{code}"

COLLECTOR = "estonia_ariregister"
SOURCE_NAME = "Estonian business register (Ariregister open data)"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

ATTRIBUTION = ("Source: the Estonian business register open data published by "
               "the Centre of Registers and Information Systems (RIK), "
               "licensed CC BY 4.0.")

# The one sentence that has to survive to every surface. Estonia's file holds
# current office-holders only, so there is no departure in it and never will be.
APPOINTMENTS_ONLY = ("The Estonian open-data file lists current office-holders "
                     "only, so it reports appointments and never departures.")

# The element the annual report states an employee count under. Exactly as the
# publisher spells it; a value that will not match is a missing figure, not a
# near-miss to be repaired.
EMPLOYEE_ELEMENT = "AverageNumberOfEmployeesInFullTimeEquivalentUnits"

# EU Recommendation 2003/361: micro < 10, small < 50, medium 50-249, large 250+.
# 50 is where an Estonian business stops being a small enterprise. See the
# module docstring for the four floors that were measured and why 250 — the
# line the UK and Czech connectors draw — was refused here at 38 appointments a
# year.
DEFAULT_MIN_EMPLOYEES = 50

# Weekly cron, so one cadence plus a fortnight of slack: a missed run is picked
# up by the next one rather than becoming a permanent hole. Overlap is free —
# a re-seen appointment is an exact content_hash duplicate.
DEFAULT_DAYS = 21

# The roles that are a natural person holding an office. Keyed on the register's
# own `isiku_roll` codes, verbatim, with the register's own Estonian label
# beside each. A code not on this list is a declined row, never a new category.
ROLES = {
    "JUHL":  ("Juhatuse liige", "a member of the management board"),
    "JUHA":  ("juhatuse ainuliige", "the sole member of the management board"),
    "JUHE":  ("juhatuse esimees", "chairman of the management board"),
    "JUHJ":  ("juhatuse liige (juhataja)", "a member of the management board"),
    "PROK":  ("Prokurist", "a procurist"),
    "VFILJ": ("Filiaali juhataja", "manager of the branch"),
    "HNKL":  ("Haldusnõukogu liige", "a member of the supervisory council"),
}

# Named rather than merely omitted. See the module docstring for what each
# family is and why it is not a talent signal.
EXCLUDED_ROLES = (
    # insolvency and liquidation
    "LIKV", "LIKVJ", "PANKR", "AJUTPH", "AJPH", "JPNKR", "ERIH",
    # owners rather than officers
    "TOSAN", "UOSAN", "YHL", "YHLLV", "EUSOS", "EUSOS2",
    # institutions and administrative contacts
    "ORP", "ARP", "FV", "KISIK", "MDKPI", "DOKH", "ASES", "KOAS", "SJESI",
    "ESIS", "ESIS2", "VALIT", "ETTEV", "FIE",
)

# A natural person. `J` is a legal person and never stores.
PERSON_TYPE = "F"

REVISITS_ITS_SOURCE_URL = True

# Sanity floors on the three inputs. Each is roughly half of what was measured,
# so an ordinary month cannot fail one and a truncated download cannot pass.
MIN_COMPANIES = 200_000          # measured 375,305
MIN_EMPLOYEE_FIGURES = 100_000   # measured 194,851
MIN_ROSTER = 200                 # measured 825 at 50 FTE

# Below this roster size the emptiness floor does not apply: a hand-narrowed
# dispatch (TIT_EE_MIN_EMPLOYEES=1000) is a population too small to expect
# anything from. Above it the measured rate is 0.725 appointments a day, so one
# per week of window is a floor with five times the margin.
FLOOR_APPLIES_ABOVE = 200
FLOOR_DAYS_PER_ROW = 7

_CODE = re.compile(r"^\d{7,9}$")
_EE_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_GENERAL_FILE = re.compile(r"/sites/default/files/1\.aruannete_yldandmed[^\"']*\.zip")
_ELEMENTS_FILE = re.compile(
    r"/sites/default/files/4\.(\d{4})_aruannete_elemendid[^\"']*\.zip")


class AriregisterError(RuntimeError):
    """A file could not be read, or came back implausibly empty."""


# --- configuration ---------------------------------------------------------

def days_from_env(default_days: int | None = None) -> int:
    raw = (os.environ.get("TIT_EE_DAYS") or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw) or int(raw) < 1:
        raise AriregisterError(
            f"TIT_EE_DAYS holds {raw!r}, which is not a number of days")
    return int(raw)


def min_employees(value: int | None = None) -> int:
    """The full-time-equivalent floor. See the module docstring for the four
    candidates that were measured and why this one."""
    if value is not None:
        return value
    raw = (os.environ.get("TIT_EE_MIN_EMPLOYEES") or "").strip()
    if not raw:
        return DEFAULT_MIN_EMPLOYEES
    if not re.fullmatch(r"\d{1,6}", raw) or int(raw) < 1:
        raise AriregisterError(
            f"TIT_EE_MIN_EMPLOYEES holds {raw!r}, which is not a headcount")
    return int(raw)


# --- fetching --------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT}


def fetch_zip(url: str, *, session=None, timeout: int = 900) -> zipfile.ZipFile:
    """One published zip, held in memory as a seekable archive.

    The three files this collector reads are 45MB, 20MB and 18MB compressed.
    They are read as streams once open — the persons file expands to about 1GB
    of JSON and the two report files to about 250MB of CSV each — so nothing
    here holds a decompressed file.
    """
    get = (session or requests).get
    resp = get(url, headers=_headers(), timeout=timeout)
    if resp.status_code != 200:
        raise AriregisterError(f"{url} returned HTTP {resp.status_code}")
    try:
        return zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile as exc:
        raise AriregisterError(
            f"{url} did not return a zip archive "
            f"({len(resp.content)} bytes, starts {resp.content[:16]!r})") from exc


def discover_report_files(*, session=None, page: str | None = None) -> tuple[str, str]:
    """The current annual-report file URLs, read off the download page.

    The publisher versions these in the FILENAME — `..._kuni_30062026_0.zip` —
    so a hard-coded URL 404s on the day the next cut lands, and a 404 in a
    materiality filter is a silent zero: every company would fail the threshold
    and the run would look like a quiet fortnight. Reading the page is what
    makes that a loud failure instead.

    Returns (general_info_url, latest_year_elements_url).
    """
    if page is None:
        get = (session or requests).get
        resp = get(DOWNLOAD_PAGE, headers=_headers(), timeout=120)
        if resp.status_code != 200:
            raise AriregisterError(
                f"the open-data download page returned HTTP {resp.status_code}")
        page = resp.text

    general = _GENERAL_FILE.search(page)
    if not general:
        raise AriregisterError(
            "the open-data download page no longer links a "
            "'1.aruannete_yldandmed' file. Without it a report_id cannot be "
            "joined to a company, so the employee threshold cannot be applied "
            "and every appointment would be stored or none would.")
    years = {int(m.group(1)): m.group(0) for m in _ELEMENTS_FILE.finditer(page)}
    if not years:
        raise AriregisterError(
            "the open-data download page no longer links any "
            "'4.<year>_aruannete_elemendid' file, which is where the employee "
            "count lives.")
    latest = max(years)
    return SITE + general.group(0), SITE + years[latest]


# --- the employee threshold ------------------------------------------------

def _csv_rows(archive: zipfile.ZipFile):
    """Stream the single CSV inside one of the report archives.

    Semicolon-delimited and quoted, which is how RIK publishes them; read as a
    stream because each expands to about 250MB.
    """
    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise AriregisterError(
            f"archive holds no CSV (members: {archive.namelist()[:4]})")
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        yield from csv.DictReader(text, delimiter=";")


def employee_counts(elements: zipfile.ZipFile, general: zipfile.ZipFile) -> dict:
    """`registrikood -> full-time-equivalent employees`, from the annual reports.

    Two passes because the figure and the company are in different files and
    only `report_id` joins them. A company with several reports keeps the
    largest figure it has stated, which is the least flattering direction to be
    wrong in for a MINIMUM threshold: it can only ever admit a company, never
    exclude one that qualifies.
    """
    by_report: dict[str, float] = {}
    element_rows = 0
    for row in _csv_rows(elements):
        element_rows += 1
        if (row.get("elemendi_nimetus") or "").strip() != EMPLOYEE_ELEMENT:
            continue
        try:
            by_report[(row.get("report_id") or "").strip()] = float(
                (row.get("vaartus") or "").strip())
        except (TypeError, ValueError):
            continue
    if not by_report:
        raise AriregisterError(
            f"no report element was named {EMPLOYEE_ELEMENT!r} in "
            f"{element_rows} rows. That element is the whole materiality "
            f"filter, so this is the column or the element name having moved, "
            f"not a year in which nobody reported a headcount.")

    out: dict[str, float] = {}
    for row in _csv_rows(general):
        value = by_report.get((row.get("report_id") or "").strip())
        if value is None:
            continue
        code = (row.get("registrikood") or "").strip()
        if code and value > out.get(code, -1.0):
            out[code] = value
    if len(out) < MIN_EMPLOYEE_FIGURES:
        raise AriregisterError(
            f"joined {len(out)} companies to an employee figure, against a "
            f"measured 194,851. A national return cannot be that small, so the "
            f"join column moved or a download was truncated.")
    return out


# --- the persons file ------------------------------------------------------

def iter_companies(archive: zipfile.ZipFile):
    """Every company object in the persons file, one at a time.

    The file is a single ~1GB JSON array, so it is decoded incrementally with
    `raw_decode` over a sliding buffer rather than loaded. Stdlib only: this
    repository installs `requests` and `PyYAML` and nothing else, and adding a
    streaming-JSON dependency to read one file is not a trade worth making.
    """
    names = [n for n in archive.namelist() if n.lower().endswith(".json")]
    if not names:
        raise AriregisterError(
            f"the persons archive holds no JSON (members: {archive.namelist()[:4]})")
    decoder = json.JSONDecoder()
    with archive.open(names[0]) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8")
        buffer = ""
        opened = False
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            buffer += chunk
            if not opened:
                start = buffer.find("[")
                if start < 0:
                    continue
                buffer = buffer[start + 1:]
                opened = True
            while True:
                trimmed = buffer.lstrip(" \n\r\t,")
                if not trimmed or trimmed[0] == "]":
                    buffer = trimmed
                    break
                try:
                    obj, end = decoder.raw_decode(trimmed)
                except ValueError:
                    buffer = trimmed
                    break
                yield obj
                buffer = trimmed[end:]


def parse_date(value: str) -> str | None:
    """'05.06.2023' -> '2023-06-05'. The register's own format, and the only
    one accepted: a date that will not parse is a declined row."""
    match = _EE_DATE.match((value or "").strip())
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def scrub_person(entry: dict) -> dict | None:
    """Name and nothing else. THE ONLY WAY A PERSON REACHES A ROW FROM HERE.

    The file carries a home address, a birth date, an email and a national-ID
    hash. The owner's ruling is that name, role, employer and date are taken at
    this boundary and everything else is dropped before a dict exists. Names are
    returned exactly as published — `Kõrve`, `Rieksts-Riekstinš`,
    `Suislep-Peets` — never re-cased and never transliterated.
    """
    if not isinstance(entry, dict):
        return None
    given = re.sub(r"\s+", " ", str(entry.get("eesnimi") or "")).strip()
    family = re.sub(r"\s+", " ", str(entry.get("nimi_arinimi") or "")).strip()
    if not family:
        return None
    return {"given_name": given, "family_name": family,
            "name": f"{given} {family}".strip()}


def is_person(entry: dict) -> bool:
    """Whether this card entry is a natural person in an allowlisted office.

    `isiku_tyyp` is the answer to the first half and the only one: a legal
    person entered as a share-register keeper carries a company name in exactly
    the field a human's surname uses.
    """
    return ((entry.get("isiku_tyyp") or "").strip() == PERSON_TYPE
            and (entry.get("isiku_roll") or "").strip() in ROLES)


def register_url(code: str) -> str | None:
    """The register's own English page for a company. A real code answers 200
    and an invented one answers 303 — see the module docstring."""
    clean = str(code or "").strip()
    if not _CODE.match(clean):
        return None
    return REGISTER.format(code=clean)


# --- one row ---------------------------------------------------------------

def _pretty(iso: str) -> str:
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def _employees(count: float) -> str:
    """The reported figure, as the report states it. Never rounded up."""
    return str(int(count)) if float(count).is_integer() else f"{count:g}"


def _row(company: dict, entry: dict, when: str, staff: float,
         *, floor: int) -> dict | None:
    code = str(company.get("ariregistri_kood") or "").strip()
    url = register_url(code)
    name = re.sub(r"\s+", " ", str(company.get("nimi") or "")).strip()
    person = scrub_person(entry)
    role_code = (entry.get("isiku_roll") or "").strip()
    if not (url and name and person and role_code in ROLES):
        return None

    estonian, english = ROLES[role_code]
    who = person["name"]
    headline = f"{name}: {who} appointed {english} on {_pretty(when)}"

    # The summary is built HERE and `as_classified` returns it unchanged, so it
    # is a literal prefix of `raw_text` and every figure in it is verbatim in
    # the source text by construction rather than by care.
    #
    # That is not belt and braces. `validate._NUMBER` matches a number, an
    # optional trailing period and then a word beginning b, m or k as a
    # magnitude — a documented defect it names and deliberately leaves alone —
    # so "on 9 June 2026. BAUHOF GROUP AS reported" reads as the figure
    # `2026b`. Twelve of the first 66 rows built here were discarded for
    # inventing a number they had not invented, every one of them an employer
    # whose name begins B, M or K. Composing the summary separately from the
    # body is what let the two sentences differ in the word AFTER the year.
    summary = (
        f"The Estonian business register records that {who} was appointed "
        f"{english} of {name} (registry code {code}) on {_pretty(when)}. "
        f"{name} reported {_employees(staff)} employees in full-time "
        f"equivalent units in its annual report. {APPOINTMENTS_ONLY} "
        f"{ATTRIBUTION}"
    )
    body = (
        f"{summary} The register names the role {estonian}. The employee "
        f"figure is why this employer is read at all: the register covers "
        f"every Estonian company and this connector reads only those reporting "
        f"{floor} full-time equivalent employees or more, the point at which "
        f"an enterprise stops being small under the European Commission's own "
        f"definition."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "summary": summary,
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": PERSONS_URL,
        "published_date": when,
        "company": name,
        "country": "Estonia",
        # Personal data stops here. `person_name` is a name and nothing else,
        # because scrub_person returned nothing else.
        "person_name": who,
        "role_code": role_code,
        "role_et": estonian,
        "role_en": english,
        "appointed_on": when,
        "registry_code": code,
        "employees": staff,
        "employee_floor": floor,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def emptiness_floor(roster: int, window: int) -> int:
    """How few appointments is too few to be a quiet fortnight."""
    if roster < FLOOR_APPLIES_ABOVE:
        return 0
    return max(1, window // FLOOR_DAYS_PER_ROW)


LAST_RUN: dict = {}


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            floor: int | None = None, session=None,
            persons=None, elements=None, general=None) -> list[dict]:
    """Every appointment at a material Estonian employer inside the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the register IS
    the population.

    `persons`, `elements` and `general` accept already-open archives so a test
    can drive the whole path without a network call.
    """
    window = days if days is not None else days_from_env()
    threshold = min_employees(floor)
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()

    if elements is None or general is None:
        general_url, elements_url = discover_report_files(session=session)
        print(f"[{COLLECTOR}] employee figures from {elements_url.rsplit('/', 1)[-1]}")
        elements = elements or fetch_zip(elements_url, session=session)
        general = general or fetch_zip(general_url, session=session)
    staff = employee_counts(elements, general)
    roster = {code for code, count in staff.items() if count >= threshold}
    print(f"[{COLLECTOR}] {len(staff)} companies with a reported headcount, "
          f"{len(roster)} at {threshold} full-time equivalents or more")
    if len(roster) < MIN_ROSTER and threshold <= DEFAULT_MIN_EMPLOYEES:
        raise AriregisterError(
            f"{len(roster)} companies clear {threshold} full-time equivalents, "
            f"against a measured 825. That is the employee element or the "
            f"report year having moved, not a change in Estonian employment.")

    if persons is None:
        persons = fetch_zip(PERSONS_URL, session=session)

    out: list[dict] = []
    seen: set[tuple] = set()
    companies = rows = declined_role = declined_person = 0
    for company in iter_companies(persons):
        companies += 1
        code = str(company.get("ariregistri_kood") or "").strip()
        count = staff.get(code)
        for entry in company.get("kaardile_kantud_isikud") or []:
            rows += 1
            when = parse_date(entry.get("algus_kpv") or "")
            if not when or not (start <= when <= end):
                continue
            if (entry.get("isiku_tyyp") or "").strip() != PERSON_TYPE:
                declined_person += 1
                continue
            if (entry.get("isiku_roll") or "").strip() not in ROLES:
                declined_role += 1
                continue
            if count is None or count < threshold:
                continue
            row = _row(company, entry, when, count, floor=threshold)
            if row is None:
                continue
            # The file holds 25 exact (role, name, date) repeats inside a single
            # company, measured 2026-07-30. They are one appointment written
            # twice, not two appointments.
            fingerprint = (code, row["person_name"], row["role_code"], when)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(row)

    if companies < MIN_COMPANIES:
        raise AriregisterError(
            f"the persons file held {companies} companies, against a measured "
            f"375,305. That is a truncated download, not a smaller Estonia.")

    print(f"[{COLLECTOR}] window {start}..{end} ({window}d): {companies} "
          f"companies, {rows} card entries, {declined_person} legal persons "
          f"declined, {declined_role} declined for role, {len(out)} appointments")
    print(f"[{COLLECTOR}] this source reports appointments only: "
          f"lopp_kpv is null on every row of the file, so no departure exists "
          f"in it to collect")

    LAST_RUN.clear()
    LAST_RUN.update({"read": rows, "companies": companies,
                     "roster": len(roster), "appointments": len(out)})

    floor_rows = emptiness_floor(len(roster), window)
    if len(out) < floor_rows:
        raise AriregisterError(
            f"{start}..{end} produced {len(out)} appointments from "
            f"{len(roster)} employers of {threshold} full-time equivalents or "
            f"more, against a measured 66 over 91 days. That is the date "
            f"format, the role codes or the employee join having moved, not a "
            f"quiet fortnight.")
    return out


# --- the derived record ----------------------------------------------------

def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a field of the register's own file, a field of the annual
    report, or a fixed editorial line. Nothing on the record is something a
    model believed, and there is no LLM cost at all.
    """
    name = item["company"]
    return {
        "company": name,
        "pillar": "leadership_change",
        # Never `hiring`. The register records that an office began, not whether
        # the person came from outside the business — the same rule
        # companies_house and czechia_ares apply.
        "signal_direction": "neutral",
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text`. See the note there for the rejection that made this
        # structural rather than a matter of writing the two carefully.
        "summary": item["summary"],
        "talent_readthrough": (
            "An Estonian company must tell the register who sits on its "
            "management board, so this is a complete record of board-level "
            "arrivals at the country's larger employers rather than a "
            "selective one. Read the gap in it as carefully as the rows: the "
            "published file lists only the people holding an office today, so "
            "a departure leaves no trace in it and this source can never "
            "report one. An arrival here is therefore evidence of a change, "
            "not evidence of growth, and a board with more arrivals than usual "
            "is worth reading beside something that does report exits. The "
            "register also does not say whether the person came from inside or "
            "outside the business."
        ),
        "country": "Estonia",
        # No city: the persons file carries no company address, and Estonia's
        # employers are concentrated enough that guessing would be inventing a
        # place rather than reading one.
        "headquarters_country": "Estonia",
        # A statutory register published by the state body that maintains it.
        # infer_confidence caps this at what the host is worth, and
        # ariregister.rik.ee is in vocab.PRIMARY_SOURCE_DOMAINS, so it lands at
        # 'verified'.
        "confidence": "verified",
    }

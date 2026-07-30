"""Companies House officer appointments — the UK's leadership spine, filtered
to the employers the state already says are large.

Every appointment to the board of a UK company is filed with the registrar and
published on the register, with the person's name, the role and the date. That
is the same kind of mandated, machine-readable leadership record that Item 5.02
of a US Form 8-K and SEBI's Regulation 30 category give, and it is the reason
this connector exists rather than another news feed for Britain.

    GET https://api.company-information.service.gov.uk/company/{number}/officers
        ?items_per_page=100&start_index=N            (HTTP Basic: key as user)

WHY THERE IS A FILTER AT ALL, AND WHY IT IS THIS ONE
=====================================================

The register holds **about 5.7 million live companies** — part 1 of 7 of the
free Company Data Product for 2026-07-01 holds 849,999 rows, and the seven
parts are not equal (part 7 is 52Mb against 69-70Mb each for the rest), so read
this as 5.7 to 5.9 million rather than as a count. They appoint officers
constantly. Measured on a random sample of 120 live companies drawn from that
snapshot, reading each one's public officers page: **0.246 appointments per
company per year**, which is **~1.4 million a year, ~27,000 a week, ~3,900 a
day**. The database this connector writes into holds 15,711 signals in total.
Collecting the register unfiltered would therefore bury every other source in
the tracker inside four days, and it is not even mechanically possible: 5.7M
requests a week is 33 days of continuous polling at the API's
600-requests-per-5-minutes ceiling.

It would also be worthless. The sample's median company has **2 officers ever
recorded** and a mean of 4; the names it returned are `AD ASTRA BARS LTD`,
`B-LEAF HEALTHCARE LTD`, `AVENIR WORKS 6 LTD`, `5374 LTD`. A director
appointment at a dormant single-member company is not a talent-market signal.

**The filter is the GOV.UK gender pay gap roster**, and it is chosen because it
is the only free, primary, machine-readable list of UK employers that is keyed
on EMPLOYEES rather than on anything else. Every employer with 250 or more
employees in Great Britain must report a gender pay gap by law, the published
CSV carries a `CompanyNumber` column, and this repository already reads that
file (`collectors/uk_paygap.py`). So the population is:

    the 2025 reporting year's file, 11,154 employers
      -> 9,634 carry a well-formed Companies House number (86.4%)
      -> 9,230 of those are in a size band of 250 employees or more

**9,230 companies, 0.16% of the register, every one of them an employer the
state requires to report because it has at least 250 employees.** Verified by
running it: the live pay-gap file yields exactly 9,230, and the four rotation
slices come out 2,344 / 2,295 / 2,321 / 2,270. There is no inference in any of
that and no model anywhere near it.

Measured yield, from a random sample of 150 of those companies read the same
way (145 parseable; the five that were not are `IP*` registered societies and
`RC*` Royal Charter bodies, which keep no officer list of this shape):

| | random register | GPG 250+ roster |
|---|---|---|
| appointments per company per year | 0.246 | **0.867** |
| active officers, median | 1 | 4 |
| officers ever recorded, median | 2 | 26 |
| projected appointments a year | ~1,400,000 | **~7,354** |
| projected stored rows a week | ~27,000 | **~110** |

The stored figure is lower than the raw one for two measured reasons, both
already in the pipeline: `dedupe.fuzzy_duplicate` treats one employer's
leadership changes inside 14 days as one development, which collapses the count
by a factor of **0.81** (simulated over 105 weekly windows on the sample), and
the role allowlist below drops another ~3%.

WHAT WAS REJECTED AS A FILTER, WITH THE NUMBERS
-----------------------------------------------

*Accounts category* (`FULL` / `GROUP` / `MEDIUM`), which is the obvious filter
because it is in the free bulk product and needs no second source. It fails on
precision, measured by joining the roster to the same snapshot: only **1,104 of
17,378** companies filing full, group or medium accounts in that slice are
250+ employee employers — **6.35%**. Scaled up it is ~120,000 companies, 13x
the roster, to poll for a population that is 94% not what we are looking for,
and it would miss 14% of the roster anyway (1,104 of the 1,284 roster companies
in the slice file FULL, GROUP or MEDIUM; the other 180 file as audit-exempt
subsidiaries, small, or nothing at all). The reason is structural: the
accounts category records how a company chose to file, not how many people it
employs, so a two-employee property vehicle with a large balance sheet files
FULL and a 400-person business can file as a subsidiary. **Employees are the
materiality this tracker needs, and only the pay-gap roster states them.**

*SIC code.* A topic filter, not a size filter. It cannot separate a 3,000-
person software company from a dormant one with the same code.

*Everything the register itself exposes as a search.* `advanced-search/companies`
filters on name, status, type, incorporation date, location and SIC — nothing
about size, accounts or employees.

WHY NOT THE STREAMING API
-------------------------

The brief that asked for this connector said the streaming API at
`stream.company-information.service.gov.uk/officers` was "almost certainly the
right primitive". It is not, for two reasons that were checked rather than
assumed:

1. **A REST key cannot open it.** The streaming guide is explicit: "Applications
   that are to use the streaming API must be registered as such, the REST API
   and streaming API keys are not interchangable." `COMPANIES_HOUSE_API_KEY_UK`
   is documented as a REST key with the REST rate limit, so it will 401 there.
2. **It is the wrong shape for this repository even with the right key.** The
   stream is a long-lived HTTP connection resumed by a committed `timepoint`
   (too old a timepoint returns 416), limited to two concurrent connections per
   account. Every database writer here shares one `talent-collect` lock and runs
   as a bounded GitHub Actions job that commits and exits; a process that has to
   stay connected to avoid losing its place is the opposite of that, and a
   missed window would be unrecoverable rather than back-fillable.

Polling is not a compromise here, it is the property that makes the source
safe: `appointed_on` is a field on every officer, so a window is just a filter
on data the endpoint always returns. **This collector keeps no state at all.**
A missed run loses nothing; a wider window is a longer date filter.

THE ROTATION, AND WHY THE WINDOW IS DERIVED FROM IT
---------------------------------------------------

10,568 requests would sweep the whole roster (1.145 requests per company at 100
officers a page, measured), which is 97 minutes of wall clock at REQUEST_DELAY
— long enough to matter, because this job holds the single writer lock and
`writer_queue.LONG_HOLD_MINUTES` is 120.

So the roster is split into `SLICES` deterministic slices and one slice runs
each week, exactly as `google_news` rotates locales. The slice is a stable
digest of the company number, so there is no state file and no committed
cursor, and the window is DERIVED from the rotation the way
`registry.recency_window_days` derives Google News's: `SLICES * 7 + 14` days,
so one entirely missed run is still covered by the next visit rather than
becoming a permanent hole. Overlap costs nothing — a re-seen appointment is an
exact `content_hash` duplicate and is skipped before anything is written.

    4 slices, weekly     2,270-2,344 companies a run, 42-day window, ~25 minutes

Each visit therefore covers 42 days of which 28 are new and 14 were already seen
last time. That is deliberate: the 14 re-seen days cost nothing (exact
`content_hash` duplicates, skipped before any write) and they are what makes a
missed run recoverable instead of a hole.

WHAT IS NOT COLLECTED, AND WHY EACH IS A DECISION
-------------------------------------------------

- **A body corporate is not an employee.** `corporate-director`,
  `corporate-secretary` and the rest of the `corporate-*` roles are a company
  being appointed to another company's board, and `nominee-director` /
  `nominee-secretary` are formation-agent artefacts. This is the same judgement
  `bse_india` makes about auditors, and the register proves it is needed:
  `LEGAL & GENERAL CO SEC LIMITED` is the sitting secretary of Legal & General
  Resources Limited. Measured cost of excluding them: **2 of 231 appointments
  (0.9%) were a body corporate and 63 of 3,151 officers (2.0%) were nominees.**
  The API's `officer_role` is the ONLY place this distinction survives — the
  public web page renders a `corporate-secretary` as plain "Secretary" — which
  is why the allowlist is keyed on the API field and never on a rendered label.
- **Resignations.** `resigned_on` is on the same records and would add 80% more
  rows (184 resignations against 231 appointments in the sampled two years),
  and the register never says why somebody left. An unexplained departure is
  the weakest row this source could produce, so v1 collects arrivals only.
- **A direction of `hiring`.** The register records the legal fact of an
  appointment and nothing about where the person came from. A group finance
  manager added to a subsidiary board looks identical to an external chief
  executive hire, so every row here is `neutral`. Precision over recall, the
  same rule `bse_india` applies to a re-appointment.
- **`city`.** The register gives a registered office, which is not where the
  workforce sits. It goes in `hq_city` through `uk_paygap`'s own postcode map,
  imported rather than reimplemented, and only for the postcode areas that map
  unambiguously. Nothing here splits an address on a comma.

ACCESS AND LICENCE
------------------

`api.company-information.service.gov.uk` answers 401 to every path including
`/robots.txt`, so there is no directive to honour on the API host and the
default applies. `find-and-update.company-information.service.gov.uk/robots.txt`
404s (no directives), and `download.companieshouse.gov.uk/robots.txt` is
`User-agent: *` / `Disallow:` — explicitly everything. The register's data is
public sector information; the attribution statement travels in the summary of
every stored row, the same way `uk_paygap` carries its own.

`source_url` is the register's own page for that officer's appointments,
`/officers/{officer_id}/appointments`, which names the company, the role and
the appointment date and is keyed on a permanent officer id. It is NOT the
company's officers page (that is one URL for every appointment the company ever
makes) and NOT `links.self`, which has no public page behind it. Because one
person can be appointed twice, that URL is a page this collector revisits on
purpose — see `REVISITS_ITS_SOURCE_URL`.

WHAT IS STILL UNPROVEN, AND WILL BE UNTIL THE FIRST REAL RUN
------------------------------------------------------------

No authenticated call has ever been made from here: the key exists only as a
GitHub secret. Everything above was measured against the PUBLIC register web
pages, the free bulk product and the published specification, so what remains
unverified is the authenticated envelope — that `items_per_page=100` is
accepted, that `total_results` counts what the docs say, that HTTP Basic with
an empty password is the accepted form, and the exact `officer_role` strings on
live rows. All four are asserted against a fixture built from real register
facts in the documented shape, and all four fail loudly rather than quietly if
the live API disagrees. The first run should be
`python run_collect.py --source companies_house --dry-run`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from . import uk_paygap

API_URL = "https://api.company-information.service.gov.uk/company/{number}/officers"
REGISTER = "https://find-and-update.company-information.service.gov.uk"
APPOINTMENTS_URL = REGISTER + "/officers/{officer_id}/appointments"
COMPANY_OFFICERS_URL = REGISTER + "/company/{number}/officers"
COLLECTOR = "companies_house"
SOURCE_NAME = "Companies House officer appointments"

# The secret's name in this repository. Read here and nowhere else, and never
# printed: every error message below describes the key without quoting it.
API_KEY_ENV = "COMPANIES_HOUSE_API_KEY_UK"

# The register is a public service and gets a real name and a contact address
# for the same reason SEC demands one.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

# Attribution. Carried in the summary of every row so it reaches WordPress with
# the data rather than living only in this docstring, exactly as uk_paygap does.
ATTRIBUTION = ("Contains public sector information from the Companies House "
               "register, licensed under the Open Government Licence v3.0.")

# 600 requests per 5 minutes is 2 a second. 0.55s leaves ~9% of the allowance
# unspent, which is the margin for a retry after a 429 without breaching it.
REQUEST_DELAY = 0.55
RATE_LIMIT_WAIT = 65          # a 429 resets at the end of its 5-minute window
RATE_LIMIT_RETRIES = 5

# The endpoint's maximum page size. Measured on 145 roster companies: officers
# ever recorded run median 26, mean 44.4, p90 66, max 1,992, so 100 a page is
# 1.145 requests per company and 98% of companies need exactly one.
PAGE_SIZE = 100
MAX_PAGES = 25                # 2,500 officers; the sampled maximum was 1,992

# The rotation. One slice a week, so the whole roster is read every SLICES weeks
# and no run holds the writer lock for long. The window covers the rotation plus
# two weeks, so a single missed run is caught by the next visit rather than
# leaving a permanent hole; overlap is free because a re-seen appointment is an
# exact content_hash duplicate.
SLICES = 4
WINDOW_SLACK_DAYS = 14

# The materiality floor, as the pay-gap service spells its bands. Anything below
# "250 to 499" — the voluntary reporters it also publishes, and rows with no band
# — is not in the statutory population and is not the guarantee this connector
# rests on, so it is excluded by construction rather than by a size guess.
DEFAULT_MIN_SIZE = "250"

# A national statutory roster cannot be small. Below this the CSV was truncated,
# the column moved, or the join is wrong — none of which is a quiet week.
MIN_ROSTER = 5000

# The plausible ceiling for roster numbers the register does not recognise.
# Companies dissolve and are removed, and the pay-gap file is up to a year old,
# so some 404s are expected; a fifth of the roster 404ing is a broken join.
MAX_MISSING_FRACTION = 0.20

# Below this many companies, a run is a hand-narrowed dispatch (say
# TIT_CH_MIN_SIZE=20000, 51 companies) and an emptiness floor would fire on a
# population too small to expect anything from. At or above it, the floor
# applies: the measured rate is ~88 appointments per 1,000 companies over a
# 42-day window, so one per 200 is a floor no working run can fail.
FLOOR_APPLIES_ABOVE = 500
FLOOR_PER_COMPANIES = 200
FLOOR_MINIMUM = 5

# The officer roles that are a PERSON taking a leadership role. Keyed on the
# API's own `officer_role` enum, verbatim. A value not on this list is a
# declined row, never a new category.
ROLES = {
    "director": "a director",
    "secretary": "company secretary",
    "llp-member": "a member",
    "llp-designated-member": "a designated member",
}

# Roles that look like leadership and are not, named rather than merely omitted
# so a later reader can see the exclusion was a decision. Every `corporate-*`
# role is a COMPANY appointed to a board, and a nominee is a formation agent's
# placeholder. See the module docstring for the measured cost of excluding them.
EXCLUDED_ROLES = (
    "corporate-director", "corporate-secretary",
    "corporate-nominee-director", "corporate-nominee-secretary",
    "corporate-llp-member", "corporate-llp-designated-member",
    "nominee-director", "nominee-secretary",
    "general-partner-in-a-limited-partnership",
    "limited-partner-in-a-limited-partnership",
    "judicial-factor", "receiver-and-manager",
)

# The source URL is the register's page for one PERSON's appointments, and a
# person can be appointed again. Marking it seen would make the first
# appointment the last one this collector ever reported for them, which is the
# ats_boards lesson. Dedup happens on content_hash and the fuzzy window instead.
REVISITS_ITS_SOURCE_URL = True

_COMPANY_NUMBER = re.compile(r"^[A-Z0-9]{8}$")
_APPOINTMENTS_PATH = re.compile(r"^/officers/([A-Za-z0-9_-]{8,64})/appointments/?$")


class CompaniesHouseError(RuntimeError):
    """A run could not be read, or came back implausibly empty."""


class Employer:
    """One company on the roster: what the pay-gap file says about it, plus the
    register key. Nothing here is inferred."""

    __slots__ = ("number", "name", "size_band", "postcode", "sic")

    def __init__(self, number: str, name: str, size_band: str,
                 postcode: str, sic: str):
        self.number = number
        self.name = name
        self.size_band = size_band
        self.postcode = postcode
        self.sic = sic


# --- configuration ---------------------------------------------------------

def api_key() -> str:
    """The REST key, from the environment. Never printed, never logged."""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise CompaniesHouseError(
            f"{API_KEY_ENV} is not set. Every route into the Companies House "
            f"API needs one (the REST API, the streaming API and even "
            f"/robots.txt answer 401 unauthenticated), and it must be a REST "
            f"key: streaming keys are a separate registration and are not "
            f"interchangeable with these.")
    return key


def allowed_sizes(min_size: str | None = None) -> set[str]:
    """The pay-gap size bands at or above the floor, as the CSV spells them.

    Built from uk_paygap.SIZE_BANDS rather than a second copy of the labels, so
    the two cannot drift. Deliberately never includes the service's
    "Less than 250" or "Not Provided" rows: the 250-employee duty IS the
    materiality guarantee, and a voluntary reporter below it does not carry it.
    """
    floor = (min_size or os.environ.get("TIT_CH_MIN_SIZE") or DEFAULT_MIN_SIZE).strip()
    keys = [k for k, _label in uk_paygap.SIZE_BANDS]
    labels = [label for _k, label in uk_paygap.SIZE_BANDS]
    if floor not in keys:
        raise CompaniesHouseError(
            f"TIT_CH_MIN_SIZE={floor!r} is not one of {', '.join(keys)}")
    return set(labels[keys.index(floor):])


def slices_from_env(default: int = SLICES) -> int:
    raw = (os.environ.get("TIT_CH_SLICES") or "").strip()
    if not raw:
        return default
    if not re.fullmatch(r"\d{1,2}", raw) or int(raw) < 1:
        raise CompaniesHouseError(
            f"TIT_CH_SLICES holds {raw!r}, which is not a slice count")
    return int(raw)


def window_days(slices: int | None = None) -> int:
    """Derived from the rotation, never typed twice.

    A slice is visited every `slices` weeks, so the window has to cover that
    gap. The slack is a whole extra visit's worth of margin: one missed run is
    then still inside the next run's window instead of being a hole nothing
    ever looks at again.
    """
    raw = (os.environ.get("TIT_CH_DAYS") or "").strip()
    if raw:
        if not re.fullmatch(r"\d{1,4}", raw) or int(raw) < 1:
            raise CompaniesHouseError(
                f"TIT_CH_DAYS holds {raw!r}, which is not a number of days")
        return int(raw)
    return (slices if slices is not None else slices_from_env()) * 7 + WINDOW_SLACK_DAYS


def slice_of(number: str, slices: int) -> int:
    """Which slice a company belongs to. A stable digest, not Python's hash():
    that is salted per process, so the rotation would reshuffle every run and
    some companies would go months without a visit.
    """
    digest = hashlib.blake2b(number.encode("ascii", "ignore"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % max(1, slices)


def current_slice(slices: int, *, today: date | None = None) -> int:
    """This week's slice. From the ISO week number, so it advances by itself and
    nothing has to be committed for the rotation to move on."""
    raw = (os.environ.get("TIT_CH_SLICE") or "").strip()
    if raw:
        if not re.fullmatch(r"\d{1,2}", raw):
            raise CompaniesHouseError(
                f"TIT_CH_SLICE holds {raw!r}, which is not a slice index")
        if int(raw) >= slices:
            raise CompaniesHouseError(
                f"TIT_CH_SLICE={raw} is out of range for {slices} slice(s)")
        return int(raw)
    day = today or datetime.now(timezone.utc).date()
    return day.isocalendar()[1] % max(1, slices)


# --- the roster ------------------------------------------------------------

def parse_roster(text: str, *, sizes: set[str] | None = None) -> list[Employer]:
    """Every 250+ employee employer in one pay-gap file that carries a
    Companies House number.

    The name taken is `CurrentName` falling back to `EmployerName` — the same
    expression uk_paygap.parse_csv uses, deliberately, so `vocab.company_key`
    lands on the SAME employer as the pay-gap rows already stored and a company
    profile page shows one employer's pay and leadership together instead of
    two near-identical employers.
    """
    bands = sizes if sizes is not None else allowed_sizes()
    out: list[Employer] = []
    seen: set[str] = set()
    total = 0
    for row in csv.DictReader(io.StringIO(text)):
        total += 1
        if (row.get("EmployerSize") or "").strip() not in bands:
            continue
        number = (row.get("CompanyNumber") or "").strip().upper()
        name = (row.get("CurrentName") or row.get("EmployerName") or "").strip()
        if not (name and _COMPANY_NUMBER.match(number)) or number in seen:
            continue
        seen.add(number)
        out.append(Employer(number, name, (row.get("EmployerSize") or "").strip(),
                            (row.get("PostCode") or "").strip(),
                            (row.get("SicCodes") or "").strip()))
    if total < uk_paygap.MIN_ROWS_PER_YEAR:
        raise CompaniesHouseError(
            f"the pay-gap file held {total} employers. A national return cannot "
            f"be that small, so the download was truncated or the columns "
            f"moved — this is a breakage, not a quiet year.")
    return out


def roster(*, year: int | None = None, sizes: set[str] | None = None) -> list[Employer]:
    """The population, fetched fresh from the pay-gap service each run.

    uk_paygap.fetch_csv is imported rather than reimplemented: it already
    carries the User-Agent the service expects and already refuses a response
    whose header is not the CSV it used to be.
    """
    reporting_year = year if year is not None else uk_paygap.latest_complete_year()
    employers = parse_roster(uk_paygap.fetch_csv(reporting_year), sizes=sizes)
    if len(employers) < MIN_ROSTER:
        raise CompaniesHouseError(
            f"the {reporting_year} pay-gap file yielded {len(employers)} "
            f"employers with a Companies House number at this size floor. "
            f"About 9,200 is the measured figure, so this is the CompanyNumber "
            f"column having moved or the size bands being spelled differently, "
            f"not a change in British employment.")
    return employers


# --- the API ---------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def fetch_officers(number: str, *, key: str, session=None,
                   timeout: int = 45) -> list[dict] | None:
    """Every officer record for one company, or None if the register has no
    such company.

    None and [] are different answers and are counted separately: a 404 is a
    roster number the register does not recognise, while an empty list is a
    company with no officers on file. Conflating them is how a broken join
    reads as a quiet week.
    """
    get = (session or requests).get
    items: list[dict] = []
    start = 0
    for _page in range(MAX_PAGES):
        params = {"items_per_page": PAGE_SIZE, "start_index": start}
        resp = None
        for attempt in range(RATE_LIMIT_RETRIES):
            resp = get(API_URL.format(number=number), params=params,
                       headers=_headers(), auth=(key, ""), timeout=timeout)
            if resp.status_code != 429:
                break
            # Not a failure. The allowance resets at the end of its window.
            time.sleep(RATE_LIMIT_WAIT)
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            raise CompaniesHouseError(
                f"{API_KEY_ENV} was refused (401). Either the key is wrong, or "
                f"it is a STREAMING key: Companies House registers the two "
                f"kinds separately and states that they are not "
                f"interchangeable. This collector needs the REST key.")
        if resp.status_code == 429:
            raise CompaniesHouseError(
                f"still rate limited after {RATE_LIMIT_RETRIES} waits of "
                f"{RATE_LIMIT_WAIT}s. The allowance is 600 requests per 5 "
                f"minutes and REQUEST_DELAY is {REQUEST_DELAY}s, so something "
                f"else is sharing this key.")
        if resp.status_code != 200:
            raise CompaniesHouseError(
                f"the officers endpoint returned {resp.status_code} for "
                f"company {number}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CompaniesHouseError(
                f"company {number} did not return JSON: {resp.text[:160]!r}"
            ) from exc
        if "items" not in payload:
            raise CompaniesHouseError(
                f"company {number} returned a payload with no 'items' key "
                f"(keys: {sorted(payload)[:8]}). The response shape has changed.")
        page = payload.get("items") or []
        items.extend(page)
        total = payload.get("total_results")
        start += PAGE_SIZE
        if len(page) < PAGE_SIZE or (isinstance(total, int) and start >= total):
            break
    return items


# --- one appointment ------------------------------------------------------

def officer_page(appointments_link: str) -> str | None:
    """The register's own page for this officer's appointments, from the API's
    `links.officer.appointments`.

    Derived, never composed from a guessed identifier. BSE's AttachLive /
    AttachHis rot was the lesson: an identifier we invent is a link that breaks
    on somebody else's schedule.
    """
    match = _APPOINTMENTS_PATH.match((appointments_link or "").strip())
    if not match:
        return None
    return APPOINTMENTS_URL.format(officer_id=match.group(1))


def person(name: str) -> str:
    """The register writes a person as 'SURNAME, Forenames'. Companies House's
    own officer page titles the same person 'Forenames SURNAME', so that is the
    form used here — reordered, never re-cased. Re-casing is what turns
    O'BRIEN into O'brien and McDONALD into Mcdonald, and a person's name is
    exactly the field that must not be improved.
    """
    text = re.sub(r"\s+", " ", (name or "")).strip()
    if "," not in text:
        return text
    surname, forenames = text.split(",", 1)
    surname, forenames = surname.strip(), forenames.strip()
    if not (surname and forenames):
        return text
    return f"{forenames} {surname}"


def is_person(officer: dict) -> bool:
    """Whether this officer is a human being.

    `officer_role` is the answer and the only one: the public web page renders a
    `corporate-secretary` as plain "Secretary", so a label is not evidence.

    Matched against the API's enum EXACTLY, with no case folding. Folding case
    would make the string "Secretary" — which is precisely what the web page
    prints for a body corporate — pass this check, so leniency here reopens the
    hole the function exists to close. An unrecognised value is a declined row
    and is counted, never quietly accepted as a near-miss.
    """
    return (officer.get("officer_role") or "").strip() in ROLES


def appointed_in_window(officer: dict, start: str, end: str) -> bool:
    """`appointed_on` inside [start, end], as ISO dates. A record without one
    (a pre-1992 appointment carries `appointed_before` instead) is not an
    appointment we can date, so it is not collected.
    """
    when = (officer.get("appointed_on") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        return False
    return start <= when <= end


def _pretty(iso: str) -> str:
    """'2026-07-01' -> '1 July 2026', which is how the register prints it."""
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def _row(employer: Employer, officer: dict) -> dict | None:
    role = (officer.get("officer_role") or "").strip()
    if role not in ROLES:
        return None
    url = officer_page(((officer.get("links") or {}).get("officer") or {})
                       .get("appointments") or "")
    who = person(officer.get("name") or "")
    when = (officer.get("appointed_on") or "").strip()
    if not (url and who and re.fullmatch(r"\d{4}-\d{2}-\d{2}", when)):
        return None

    role_phrase = ROLES[role]
    headline = (f"{employer.name}: {who} appointed {role_phrase} "
                f"on {_pretty(when)}")

    # Everything the summary quotes is here, in the same words. The employer's
    # size band and the company number are the two figures on the record, and
    # both are read off a file rather than said by anybody.
    body = (
        f"Companies House records that {who} was appointed {role_phrase} of "
        f"{employer.name} (company number {employer.number}) on "
        f"{_pretty(when)}. The appointment is on the public register of "
        f"companies, which every UK company must keep up to date with the "
        f"registrar. {employer.name} employs {employer.size_band} people "
        f"according to its own gender pay gap return, which is why it is read "
        f"here: the register covers all 5.9 million UK companies and this "
        f"connector reads only the employers the law requires to report a pay "
        f"gap, meaning 250 employees or more. {ATTRIBUTION}"
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": COMPANY_OFFICERS_URL.format(number=employer.number),
        "published_date": when,
        "company": employer.name,
        "country": "United Kingdom",
        # The registered office, never the job location. uk_paygap's map is
        # imported, so the two UK sources place an employer identically.
        "hq_city": uk_paygap._hq_city(employer.postcode),
        "industry": uk_paygap.SIC_DIVISION_INDUSTRY.get(
            uk_paygap._first_sic_division(employer.sic) or "", ""),
        "employer_type": uk_paygap.SIC_DIVISION_EMPLOYER_TYPE.get(
            uk_paygap._first_sic_division(employer.sic) or "", ""),
        "officer_name": officer.get("name") or "",
        "officer_role": role,
        "appointed_on": when,
        "company_number": employer.number,
        "size_band": employer.size_band,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def emptiness_floor(polled: int) -> int:
    """How few appointments is too few to be a quiet fortnight.

    Scaled to what was actually polled rather than typed as one number, because
    the size floor and the slice count are both inputs: 51 companies at the
    20,000-employee band genuinely may produce nothing, while 2,300 companies
    producing nothing over six weeks is the roster join, the role enum or the
    date field having moved.
    """
    if polled < FLOOR_APPLIES_ABOVE:
        return 0
    return max(FLOOR_MINIMUM, polled // FLOOR_PER_COMPANIES)


def collect(queries=None, *, days: int | None = None, slices: int | None = None,
            slice_index: int | None = None, year: int | None = None,
            sizes: set[str] | None = None, employers: list | None = None,
            today: date | None = None, session=None, key: str | None = None,
            ) -> list[dict]:
    """Every qualifying appointment in this week's slice of the roster.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the statutory
    roster IS the population.
    """
    slice_count = slices if slices is not None else slices_from_env()
    window = days if days is not None else window_days(slice_count)
    index = (slice_index if slice_index is not None
             else current_slice(slice_count, today=today))
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()
    credential = key if key is not None else api_key()

    population = employers if employers is not None else roster(year=year, sizes=sizes)
    mine = [e for e in population if slice_of(e.number, slice_count) == index]
    print(f"[{COLLECTOR}] roster {len(population)} employers at 250+ staff; "
          f"slice {index + 1}/{slice_count} is {len(mine)} of them")
    print(f"[{COLLECTOR}] window {start}..{end} ({window}d, derived from the "
          f"{slice_count}-week rotation)")

    out: list[dict] = []
    seen: set[tuple] = set()
    polled = missing = officers_read = declined_role = 0

    for employer in mine:
        if session is None:
            time.sleep(REQUEST_DELAY)
        items = fetch_officers(employer.number, key=credential, session=session)
        polled += 1
        if items is None:
            missing += 1
            continue
        officers_read += len(items)
        for officer in items:
            if not appointed_in_window(officer, start, end):
                continue
            if not is_person(officer):
                declined_role += 1
                continue
            row = _row(employer, officer)
            if row is None:
                continue
            fingerprint = (row["source_url"], row["company_number"],
                           row["appointed_on"], row["officer_role"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(row)

    print(f"[{COLLECTOR}] {polled} companies polled, {missing} not on the "
          f"register, {officers_read} officer records read, "
          f"{declined_role} declined for role, {len(out)} appointments")

    if polled and missing / polled > MAX_MISSING_FRACTION:
        raise CompaniesHouseError(
            f"{missing} of {polled} roster company numbers are not on the "
            f"register ({100 * missing / polled:.0f}%). Dissolved companies "
            f"explain a few percent; this is the CompanyNumber column or the "
            f"join having moved.")

    floor = emptiness_floor(polled)
    if len(out) < floor:
        raise CompaniesHouseError(
            f"{start}..{end} produced {len(out)} appointments from {polled} "
            f"employers of 250 staff or more, against a measured rate of about "
            f"88 per 1,000 companies over a window this long. That is the role "
            f"enum, the date field or the roster join having moved, not a quiet "
            f"fortnight.")
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a field of the response, a field of the pay-gap file, or a
    fixed editorial line. Nothing on the record is something a model believed,
    and there is no LLM cost at all.
    """
    who = person(item["officer_name"])
    return {
        "company": item["company"],
        "pillar": "leadership_change",
        # Never `hiring`. See the module docstring: the register records that an
        # appointment happened, not whether the person came from outside the
        # employer, and a group manager joining a subsidiary board is filed
        # identically to an external chief executive hire.
        "signal_direction": "neutral",
        "headline": item["headline"],
        "summary": (
            f"Companies House records that {who} was appointed "
            f"{ROLES[item['officer_role']]} of {item['company']} (company "
            f"number {item['company_number']}) on "
            f"{_pretty(item['appointed_on'])}. {item['company']} employs "
            f"{item['size_band']} people according to its own gender pay gap "
            f"return. {ATTRIBUTION}"
        ),
        "talent_readthrough": (
            "A UK company must tell the registrar who its directors and "
            "secretaries are, so this is a complete record of board-level "
            "change at large British employers rather than a selective one: it "
            "covers the employers no outlet writes about on the same basis as "
            "the ones it does. Read it as the legal fact and not as a hire. "
            "The register does not say whether the person came from inside or "
            "outside the business, or what they will do, so a run of "
            "appointments at one employer is worth reading as a board being "
            "rebuilt and a single one is worth reading as housekeeping until "
            "something else says otherwise."
        ),
        "country": item.get("country") or "",
        # The registered office, kept apart from job location on purpose.
        "headquarters_city": item.get("hq_city") or "",
        "headquarters_country": "United Kingdom",
        "industry": item.get("industry") or "",
        "employer_type": item.get("employer_type") or "",
        # A statutory register maintained by the registrar, published by the
        # registrar. infer_confidence caps this at what the host is worth, and
        # find-and-update.company-information.service.gov.uk is already in
        # vocab.PRIMARY_SOURCE_DOMAINS, so it lands at 'verified'.
        "confidence": "verified",
    }

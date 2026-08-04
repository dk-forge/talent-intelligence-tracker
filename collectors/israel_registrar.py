"""Israeli Registrar of Companies changes file — share allotments and capital
increases, which are the registrar's own record that a company raised money.

Israel's Registrar of Companies (רשם החברות), part of the Ministry of Justice,
publishes a DAILY changes file to the national open data portal. It is not a
company list. It is an event stream: one row per act filed against a company,
carrying the company number, the company name, the registrar's name for the act
and the date the registrar updated the status.

    GET https://data.gov.il/api/3/action/datastore_search
        ?resource_id=28780ab5-3ef1-44c7-8377-da82c0aa6781
        &filters={"קוד סוג בקשה": <code>}&limit=&offset=      (no key, no auth)

WHY THIS SOURCE AND NOT THE COMPANY LIST
========================================

The portal publishes six datasets from the registrar. Five are registers: the
company list (`ica_companies`), the partnership list, companies in expedited
voluntary liquidation, the pledges register, and a data-cleansing artefact.
A register is a list of names with a status, and a list of names is not a
signal — it says nothing happened, only that something exists.

`ica-changes` is the sixth and the only one with dated events on it. Measured
on 2026-08-03 it held **558,617 rows across 96 distinct act types**, rolling:
the registrar states the file carries every change made to a corporation up to
one year back. The newest rows on that date were dated 12/07/2026, so it is
current rather than an archive.

WHAT IS COLLECTED, AND WHY IT IS FOUR ACT TYPES OUT OF NINETY-SIX
-----------------------------------------------------------------

The brief for this connector asked for a Companies House equivalent, and the
thing that makes Companies House unusually rich is that an SH01 share
allotment is effectively a funding disclosure. Israel files the same act, under
its own name, with its own code:

| code | act (registrar's own wording) | rows in the year |
|---|---|---|
| 2 | הקצאת מניות — share allotment | 4,051 |
| 22030 | דו"ח שנתי-הקצאת מניות — annual return, share allotment | 1,344 |
| 21 | הגדלה של הון רשום — increase of registered capital | 924 |
| 22157 | דו"ח שנתי-הגדלת הון — annual return, capital increase | 375 |

**6,694 acts a year.** A live 14-day dry run on 2026-08-03 returned **343 rows
across 311 distinct companies** (228 share allotments, 64 through the annual
return, 35 capital increases, 16 through the annual return), so the real rate
is about 24.5 a day rather than the 18.3 a flat division of the year suggests:
these acts are not spread evenly. That is the same order as the UK connector's
measured ~110 stored rows a week, and it is why no size filter is needed to
keep this from swamping the database.

A share allotment is the act of issuing NEW shares. It is what a company does
when it takes investment, and a dormant shell does not do it — which is the
property that makes this list self-filtering where a company register is not.
That matters here more than it does in Britain, because **Israel publishes no
employee-count filter of any kind**. There is no equivalent of the gender pay
gap roster that gives `companies_house` its 250-employee floor, and nothing in
any of the six registrar datasets states a headcount, a size band or a
turnover. So the act type IS the filter, and it is the only one available.
That is stated plainly on the sources page rather than left for a reader to
discover.

WHAT IS DELIBERATELY NOT COLLECTED
-----------------------------------

- **Director changes**, though the feed carries them and they are real:
  `עדכון דירקטורים-הוספת דירקטור` (code 4032) is 11,501 rows a year and
  `הסרת דירקטור` (4031) is 11,974, with another 4,041 through the annual
  return. That is **~451 a week against this database's 15,711 signals**, and
  with no size filter available there is no way to tell a board change at a
  real employer from one at a one-person company. `companies_house` solves the
  identical problem with the pay-gap roster; Israel offers no such list, so v1
  does not collect them rather than collecting them badly. This is a deferral
  with a named blocker, not an oversight.
- **Share transfers** (code 3, 15,301 rows). A transfer moves existing shares
  between holders. No new money enters the company, so it is not the funding
  act this connector exists to read.
- **Capital reductions, liquidations, receiverships and insolvency orders.**
  All present, all dated, none of them a talent-market signal of the kind this
  tracker stores.

THE LIMIT THAT MATTERS MOST
----------------------------

**The file carries no amount.** The columns are the company number, the company
name, the act, the date, a pledge identifier and the act code — that is all of
them. There is no share count, no price per share, no sum raised and no
investor. So a row here is a dated funding EVENT and never a funding FIGURE,
and nothing in this module invents one. Where `sec_form_d` can state a sum
because the filing states a sum, this states that an allotment was registered
and links to the registrar's own record of it. Read it as the fact that money
was raised, not as how much.

The date is the registrar's `תאריך עדכון סטטוס`, the day the registrar updated
the corporation's status for that act. It is the date of the REGISTRATION of
the act, which can trail the corporate decision behind it. It is not
represented as the date of the raise.

ACCESS, LICENCE AND ROBOTS
---------------------------

Keyless. Every call above was made anonymously and answered 200; there is no
registration, no token and no quota to apply for, so nothing here reads an
environment variable for a credential and the owner is never asked for one.

`https://data.gov.il/robots.txt` answers 200 with `Disallow: /` scoped to five
named crawlers — `laboraybot`, `YandexBot`, `SemrushBot`, `Barkrowler` and
`PetalBot`. There is no `User-agent: *` group, so this connector is not
addressed by any directive and the default applies.

The dataset's own licence, from `package_show?id=ica-changes`, is **cc-by**
(Creative Commons Attribution), `isopen: true`. Redistribution on a public
dashboard is permitted with attribution, and the attribution travels in the
summary of every stored row rather than living only in this docstring, the same
way `companies_house` and `uk_paygap` carry theirs.

No rate limit is published. The portal is CKAN, one act code is one paged
query, and a run makes single-figure requests, so REQUEST_DELAY is a courtesy
rather than a constraint.

`source_url` is the portal's own datastore query filtered to that company's
number, which returns exactly that company's change records and was verified
against a live company before being adopted. The registrar's public lookup at
`ica.justice.gov.il` is a search FORM with no per-company permalink, so there
is no stabler public page to cite; a URL composed from a guessed identifier is
the rot `companies_house` refuses, and this one is derived from the company
number the row itself carries.

DORMANT
-------

Nothing schedules this. It is registered in `run_collect` so it can be
rehearsed and reviewed, and no workflow calls it, which is this repository's
standing shape for a new collector. The first run should be

    python run_collect.py --source israel_registrar --dry-run

Its one-year rolling window means a missed run is recoverable for a year by
widening TIT_IL_DAYS, unlike a feed that only holds a fortnight.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

API_URL = "https://data.gov.il/api/3/action/datastore_search"
DATASET_PAGE = "https://data.gov.il/dataset/ica-changes"
RESOURCE_ID = "28780ab5-3ef1-44c7-8377-da82c0aa6781"
COLLECTOR = "israel_registrar"
SOURCE_NAME = "Israeli Registrar of Companies (changes file)"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

ATTRIBUTION = ("Contains information from the Israeli Registrar of Companies "
               "changes file, published on data.gov.il under the Creative "
               "Commons Attribution licence.")

# The registrar's own column names, in Hebrew, verbatim. Never transliterated:
# these are the API's keys and a rewritten one is a KeyError at 2am.
COL_NUMBER = "מספר תאגיד"
COL_NAME = "שם תאגיד"
COL_ACT = "סוג בקשה"
COL_DATE = "תאריך עדכון סטטוס"
COL_CODE = "קוד סוג בקשה"

# The acts that mean new shares were issued or registered capital was raised,
# keyed on the registrar's own numeric code rather than on its Hebrew label.
# The code is the stable key; the label is prose and could be re-worded.
FUNDING_ACTS = {
    2: "registered a share allotment",
    22030: "reported a share allotment in its annual return",
    21: "registered an increase in its registered capital",
    22157: "reported an increase in capital in its annual return",
}

# The English gloss for each act, for a summary that does not assume Hebrew.
ACT_ENGLISH = {
    2: "share allotment",
    22030: "share allotment (annual return)",
    21: "increase of registered capital",
    22157: "capital increase (annual return)",
}

# CKAN's datastore pages. 1,000 a page was exercised live; the largest act code
# here is 4,051 rows, so a code is five pages at most.
PAGE_SIZE = 1000
MAX_PAGES = 40
REQUEST_DELAY = 0.4

DEFAULT_DAYS = 14

# The file is a rolling one-year window, so a request wider than that is asking
# for rows the registrar does not publish and would read as a quiet year.
MAX_DAYS = 365

# A national funding stream cannot be empty over a fortnight. Measured on a
# live 14-day run: 343 rows, so 24.5 acts a day. The floor is a TWENTIETH of
# that, which is a bar no working run can fail and which still catches the
# failure it exists for: an act code renumbered, or the date column moved, and
# either of those returns zero rather than a few.
MEASURED_ACTS_PER_DAY = 24.5
FLOOR_FRACTION = 20

# One company can register several allotments in a year, so its citation URL is
# a page this collector revisits on purpose.
REVISITS_ITS_SOURCE_URL = True

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class IsraelRegistrarError(RuntimeError):
    """A run could not be read, or came back implausibly empty."""


# --- parsing ---------------------------------------------------------------

def parse_date(raw: str) -> str | None:
    """The registrar writes `DD/MM/YYYY`. Returns an ISO date, or None.

    Day-first is not a guess: the live file carries `22/12/2025` and
    `21/09/2025`, and a value above twelve in the first position is only
    readable as a day. A row whose date will not parse is dropped rather than
    stored with today's date, because a funding event on the wrong date is
    worse than a funding event we did not store.
    """
    match = _DMY.match((raw or "").strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def company_name(raw: str) -> str:
    """The registrar's name for the company, with its quote encoding undone.

    The file writes the Hebrew gershayim as a tilde, so `בע~מ` is `בע"מ`, the
    Hebrew for Ltd, and it appears in nearly every company name in the feed.
    Left alone it reaches a public page as a visible tilde in the middle of
    every Israeli company's name.

    This restores ONE character and changes nothing else: no re-casing, no
    transliteration, no whitespace-stripping beyond collapsing runs. A company
    name is a name, and the same rule applies to it as to a person's.
    """
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    return text.replace("~", '"')


def citation_url(number) -> str:
    """The portal's own datastore query for one company's change records.

    Derived from the company number on the row, never composed from a guessed
    identifier. Verified live against company 513612515, which returned exactly
    its own single allotment record.
    """
    filters = json.dumps({COL_NUMBER: int(number)}, ensure_ascii=False)
    return (f"{API_URL}?resource_id={RESOURCE_ID}"
            f"&filters={requests.utils.quote(filters)}")


# --- the API ---------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def fetch_act(code: int, *, session=None, timeout: int = 60) -> list[dict]:
    """Every row in the changes file for one act code.

    The act code is filtered SERVER side, which is what keeps this cheap: the
    file is 558,617 rows and the four codes read here are 6,694 of them, so
    nothing downloads the whole thing to throw 99% of it away.
    """
    get = (session or requests).get
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "resource_id": RESOURCE_ID,
            "filters": json.dumps({COL_CODE: code}, ensure_ascii=False),
            "limit": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
        }
        if session is None and page:
            time.sleep(REQUEST_DELAY)
        resp = get(API_URL, params=params, headers=_headers(), timeout=timeout)
        if resp.status_code != 200:
            raise IsraelRegistrarError(
                f"the changes file returned {resp.status_code} for act code "
                f"{code}. The portal is keyless and answers 200 anonymously, "
                f"so this is the resource id having moved, not a credential.")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise IsraelRegistrarError(
                f"act code {code} did not return JSON: {resp.text[:160]!r}"
            ) from exc
        if not payload.get("success"):
            raise IsraelRegistrarError(
                f"the portal refused the query for act code {code}: "
                f"{str(payload.get('error'))[:200]}")
        result = payload.get("result") or {}
        if "records" not in result:
            raise IsraelRegistrarError(
                f"act code {code} returned a result with no 'records' key "
                f"(keys: {sorted(result)[:8]}). The response shape has changed.")
        page_rows = result.get("records") or []
        rows.extend(page_rows)
        if len(page_rows) < PAGE_SIZE:
            break
    return rows


# --- one act ---------------------------------------------------------------

def _row(record: dict) -> dict | None:
    code = record.get(COL_CODE)
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    if code not in FUNDING_ACTS:
        return None

    name = company_name(record.get(COL_NAME) or "")
    number = record.get(COL_NUMBER)
    when = parse_date(record.get(COL_DATE) or "")
    if not (name and number and when):
        return None

    english = ACT_ENGLISH[code]
    hebrew = (record.get(COL_ACT) or "").strip().replace("~", '"')
    headline = f"{name}: {english} registered on {when}"

    body = (
        f"The Israeli Registrar of Companies recorded that {name} (company "
        f"number {number}) {FUNDING_ACTS[code]} on {when}. The registrar "
        f"publishes this act in its daily changes file under its own heading "
        f"{hebrew}. A share allotment is the issue of new shares, which is the "
        f"act a company files when it takes in investment, and it is the "
        f"reason this record is read here rather than the registrar's company "
        f"list. The changes file states the act and the date and carries no "
        f"amount: there is no share count, no price and no investor on the "
        f"record, so this is the fact that capital was raised and not a "
        f"figure for how much. The date is the day the registrar updated the "
        f"company's status for this act, which can trail the decision behind "
        f"it. {ATTRIBUTION}"
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "summary": body,
        "source_url": citation_url(number),
        "source_name": SOURCE_NAME,
        "discovery_url": DATASET_PAGE,
        "published_date": when,
        "company": name,
        "country": "Israel",
        # The changes file carries no address of any kind, so nothing here
        # places a company in a city. Israeli rows sit at country level.
        "hq_city": "",
        "industry": "",
        "company_number": str(number),
        "act_code": code,
        "act_hebrew": hebrew,
        "act_english": english,
        "registered_on": when,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def window_days() -> int:
    raw = (os.environ.get("TIT_IL_DAYS") or "").strip()
    if not raw:
        return DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw) or not 1 <= int(raw) <= MAX_DAYS:
        raise IsraelRegistrarError(
            f"TIT_IL_DAYS holds {raw!r}. The changes file is a rolling "
            f"one-year window, so a window must be between 1 and {MAX_DAYS} "
            f"days; anything wider asks for rows the registrar does not "
            f"publish and would read as a quiet year.")
    return int(raw)


def emptiness_floor(days: int) -> int:
    """How few acts is too few to be a quiet fortnight.

    Scaled to the window rather than typed as one number, because the window is
    an input: a one-day rehearsal and a 90-day catch-up cannot share a floor.
    A live fortnight returned 343, and this asks for 17 of them.
    """
    return max(1, int(days * MEASURED_ACTS_PER_DAY / FLOOR_FRACTION))


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            session=None) -> list[dict]:
    """Every share allotment and capital increase the registrar registered
    inside the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the registrar's
    own act codes ARE the population.
    """
    window = days if days is not None else window_days()
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()

    print(f"[{COLLECTOR}] window {start}..{end} ({window}d) over "
          f"{len(FUNDING_ACTS)} act codes; keyless, no model")

    out: list[dict] = []
    seen: set[tuple] = set()
    read = 0

    for code in sorted(FUNDING_ACTS):
        records = fetch_act(code, session=session)
        read += len(records)
        kept = 0
        for record in records:
            row = _row(record)
            if row is None:
                continue
            if not start <= row["registered_on"] <= end:
                continue
            fingerprint = (row["company_number"], row["registered_on"],
                           row["act_code"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(row)
            kept += 1
        print(f"[{COLLECTOR}]   code {code} ({ACT_ENGLISH[code]}): "
              f"{len(records)} rows in the file, {kept} inside the window")

    print(f"[{COLLECTOR}] {read} act rows read, {len(out)} inside the window")

    floor = emptiness_floor(window)
    if len(out) < floor:
        raise IsraelRegistrarError(
            f"{start}..{end} produced {len(out)} funding acts against a "
            f"measured rate of about {MEASURED_ACTS_PER_DAY} a day across "
            f"these four codes, and a floor of {floor}. That is the act codes "
            f"having been renumbered or the date column having moved, not a "
            f"quiet fortnight.")
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a field of the registrar's own file or a fixed editorial
    line. Nothing on the record is something a model believed, and there is no
    LLM cost at all.
    """
    return {
        "company": item["company"],
        # A raise is a company development, the same pillar sec_form_d's
        # funding rows land on. There is no funding pillar in the vocabulary.
        "pillar": "company_development",
        # Never `positive`. The registrar states that shares were issued and
        # nothing about the sum, the valuation or the investor, so a small
        # top-up and a large round are filed identically. Precision over
        # recall, the same rule companies_house applies to an appointment.
        "signal_direction": "neutral",
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text` rather than a second telling of it.
        "summary": item["summary"],
        "talent_readthrough": (
            "An Israeli company must register a share allotment with the "
            "registrar, so this is a complete record of capital being raised "
            "across the register rather than a selective one: it covers the "
            "companies no outlet writes about on the same basis as the ones it "
            "does, which is the point of reading a registry instead of a news "
            "feed. Read it as the legal fact and not as a figure. The file "
            "carries no amount, no investor and no valuation, so a seed "
            "top-up and a growth round look identical here, and a single "
            "allotment is worth reading as a company taking money in while a "
            "run of them at one company is worth reading as a company raising "
            "repeatedly. Israel publishes no employee count anywhere in the "
            "registrar's data, so unlike the UK rows these are not filtered to "
            "large employers and a company's size is unknown until something "
            "else says otherwise."
        ),
        "country": "Israel",
        # The changes file carries no address, so nothing is placed in a city.
        "headquarters_city": "",
        "headquarters_country": "Israel",
        "industry": "",
        "employer_type": "",
        # A statutory register maintained by the registrar and published by the
        # Ministry of Justice on the national portal.
        "confidence": "verified",
    }

"""Singapore's ACRA register of corporate entities — dated INCORPORATIONS,
filtered to software and IT by the register's own industry code.

The Accounting and Corporate Regulatory Authority is the body a Singapore
company is incorporated with, and it publishes its whole register of corporate
entities as open data on data.gov.sg. Collection 2, "ACRA Information on
Corporate Entities", is 27 CSV files — one per letter of the alphabet plus an
"others" file — refreshed MONTHLY. Measured on 2026-08-03 the collection's own
metadata reported `frequency: monthly` and `lastUpdatedAt: 2026-07-17`.

    GET https://api-production.data.gov.sg/v2/public/api/collections/2/metadata
        -> 200; childDatasets (27 dataset ids) and lastUpdatedAt
    GET https://api-open.data.gov.sg/v1/public/api/datasets/<id>/poll-download
        -> 201; {"code":0,"data":{"status":"DOWNLOAD_SUCCESS","url":<signed>}}
    GET <that signed url>                     -> 200; the CSV itself

All three answered ANONYMOUSLY. There is no key, none exists for this
collection, and the owner is never asked for one. The signed S3 url EXPIRES, so
it is requested fresh on every run and never written down anywhere in this
repository.

Note the **201** on the middle hop, verified live on 2026-08-04. The portal
models a download request as creating an export job, so it answers 201 Created
even though the file is already prepared and the signed URL is in the body,
while the two metadata routes answer a plain 200. The first draft of this
collector accepted only 200 and its first live run died there, blaming the
endpoint for having moved. `_json` accepts both, and a test pins it.

WHAT THE SIGNAL IS
==================

**An incorporation, and only an incorporation.** `registration_incorporation_date`
is a stated, dated fact — ISO `YYYY-MM-DD` — and `primary_ssic_code` is the
register's own industry classification, which is what stops this being a bare
list of company names. Measured on `d_af2042c77ffaf0db5d75561ce9ef5688`, the
letter "W" file, fetched 2026-08-03: HTTP 200, 18,628,748 bytes, **57,533 rows,
53 columns**. In that one file **2,578 entities carry a primary SSIC beginning
"62"** (computer programming, consultancy and related activities) and **138 of
those were incorporated in the 12 months to 2026-08-03**. Real rows from it:
WORLDAI PTE. LTD. (2025-08-03, 62011, 1 officer, "Live Company"), WISAGENT PTE.
LTD. (2025-08-06, 62021, 4 officers), WENYA LABS PTE. LTD. (2025-11-24, 62011,
3 officers), WHIZHACK TECHNOLOGIES PTE. LTD. (2025-08-06, 62013, 6 officers).

`no_of_officers` is a company-size proxy and is read as one: it is a COUNT of
the people currently on the entity's record, nothing more.

WHAT THE SIGNAL IS NOT, SAID BEFORE THE ROWS ARE READ
=====================================================

**This is not a filing-event feed, and it is narrower than every other registry
in this tracker.** The 53 columns were read; the absences below are absences in
the published file, not gaps in this connector.

- **No dated officer CHANGE.** The file carries `no_of_officers`, a current
  count, and no appointment date, no departure date and no officer name. So
  there is nothing here of the kind `companies_house` (an `appointed_on` per
  person) or `estonia_ariregister` (`algus_kpv` per person) reads, and this
  source can never produce a leadership row.
- **No funding of any kind.** No share allotment, no share capital, no
  allotment date, no investor. `israel_registrar` exists because Israel files
  the allotment as a dated act; Singapore publishes no equivalent here, so the
  funding read that connector gives is simply unavailable.
- **A monthly SNAPSHOT, not a stream.** Latency is therefore up to about a
  month: a company incorporated the day after a refresh is invisible until the
  next one. And a CHANGE is only visible as a difference between two readings,
  which **this collector deliberately does not compute**. It reads the
  incorporation date, which the file STATES, rather than diffing two snapshots
  and stamping a date the register never stated. That is the same refusal
  `estonia_ariregister` records against diffing its own daily file: a row that
  moved between two readings may have moved for half a dozen reasons, and none
  of them is dated.
- **The gap cannot be closed by widening a window.** An incorporation date is
  read off whatever snapshot is current, so a company incorporated and struck
  off between two readings never appears in any file, and no window reaches it.

So the honest description is: a complete, dated, industry-filtered record of new
software and IT companies being formed in Singapore, up to a month behind, and
nothing else at all.

WHY THE INDUSTRY CODE AND NOT THE DESCRIPTION
---------------------------------------------

`primary_ssic_description` was the literal string **"na"** on every one of the
sampled 2025-2026 rows, including all four named above. The DESCRIPTION is
therefore not a field this connector may render or filter on; `SSIC_PREFIXES`
matches the CODE, which is populated. `ssic_description` exists only to refuse
"na", and it is tested for, because "WORLDAI PTE. LTD. operates in na" is
exactly the kind of sentence that reaches a public page once and is never
lived down.

WHAT IS FILTERED OUT, AND WHY EACH IS A DECISION
-------------------------------------------------

- **Every SSIC outside the prefixes.** Singapore incorporates companies in
  every sector; without the code filter this would be a company-formation
  firehose with no talent content, which is the failure the UK and Spanish
  registers were refused for. The default is `62`, one division, the one that
  is software and IT services.
- **Entities that are no longer live.** `entity_status_description` is kept only
  when it begins "Live". A company incorporated and already struck off, wound
  up or amalgamated is not a new employer. **This one is NOT measured**: the
  sampled rows were all "Live Company", so how many recent incorporations are
  already dead is unknown here, and the run prints the count it dropped rather
  than claiming a rate it has not counted.
- **Former names.** `former_entity_name1..15` record renames and are read as
  context only. A rename is not dated in the file, so it is never a row.
- **Audit firms.** `uen_of_audit_firm1..5` / `name_of_audit_firm1..5` name a
  supplier, not an employer event. Same judgement `bse_india` makes.
- **A direction of `hiring`.** An incorporation says a company now exists. It
  says nothing about anyone being employed by it, so every row is `neutral`.
  Precision over recall, the same rule every registry connector here applies.

ADDRESS, AND WHY A CITY IS ALLOWED HERE
----------------------------------------

Unlike Israel's changes file, ACRA publishes an address (block, street, level,
unit, building, postal code). Singapore is a city-state, so `hq_city` is
"Singapore" for every row and that is a fact rather than a guess. Nothing here
parses the street address into anything; the address columns are read and not
stored, because a registered office is not where a workforce sits and the city
is already known from the country.

LICENCE, ROBOTS AND RATE
------------------------

The **Singapore Open Data Licence** (https://data.gov.sg/open-data-licence,
HTTP 200) grants a "worldwide, perpetual, royalty-free, non-exclusive licence"
to "use, access, download, copy, distribute, transmit, modify and adapt the
datasets ... whether commercially or non-commercially". Redistribution on a
public dashboard is permitted, and the attribution to ACRA and data.gov.sg
travels in the summary of every stored row rather than living only here, the
same way `companies_house` and `israel_registrar` carry theirs.

`https://data.gov.sg/robots.txt` answers 200 with exactly `User-agent: *` /
`Allow: /`. Nothing is disallowed.

No rate limit is published for anonymous use, but data.gov.sg states that
signing up grants HIGHER limits, which is only meaningful if an anonymous limit
exists. So this is polite by construction: `REQUEST_DELAY` between the 27 files,
one pass, and a monthly cadence because the data is monthly. A run downloads
about 27 files of the order of the measured 18MB; each is read and released
rather than all held, and nothing caches a signed url.

`source_url` is the portal's own page for the DATASET the row was read from,
`https://data.gov.sg/datasets/<id>/view`, and the id is taken from the
collection's `childDatasets` list rather than composed. There is no per-company
permalink to cite: ACRA's own lookup is a search form, and an invented
per-company url is the rot `companies_house` refuses. One dataset page is
therefore shared by every company whose name begins with that letter, which is
why `REVISITS_ITS_SOURCE_URL` is True — not because a company is incorporated
twice (it is not; an incorporation is a one-time event per company), but because
without it run_collect would mark the letter file seen after the first company
and every other company in that file would be dropped for the life of the
collector. Dedup happens on `content_hash` and on the UEN instead.

DORMANT
-------

Nothing schedules this. It is registered in `run_collect` so it can be rehearsed
and reviewed, and no workflow calls it, which is this repository's standing shape
for a new collector. The first run should be

    python run_collect.py --source singapore_acra --dry-run
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

PORTAL = "https://data.gov.sg"
API = "https://api-production.data.gov.sg/v2/public/api"
DOWNLOAD_API = "https://api-open.data.gov.sg/v1/public/api"

COLLECTION_ID = "2"
COLLECTION_METADATA_URL = f"{API}/collections/{COLLECTION_ID}/metadata"
DATASET_METADATA_URL = API + "/datasets/{dataset_id}/metadata"
POLL_DOWNLOAD_URL = DOWNLOAD_API + "/datasets/{dataset_id}/poll-download"
COLLECTION_PAGE = f"{PORTAL}/collections/{COLLECTION_ID}/view"
DATASET_PAGE = PORTAL + "/datasets/{dataset_id}/view"
LICENCE_URL = f"{PORTAL}/open-data-licence"

COLLECTOR = "singapore_acra"
SOURCE_NAME = "ACRA register of corporate entities (Singapore)"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

ATTRIBUTION = ("Contains information from the ACRA register of corporate "
               "entities, published by the Accounting and Corporate Regulatory "
               "Authority on data.gov.sg under the Singapore Open Data Licence.")

# The register's own column names, verbatim as the published CSV spells them.
# Never renamed: these are the file's keys and a rewritten one is a KeyError at
# 2am, or worse, a silent empty column.
COL_UEN = "uen"
COL_NAME = "entity_name"
COL_TYPE = "entity_type_description"
COL_STATUS = "entity_status_description"
COL_INCORPORATED = "registration_incorporation_date"
COL_SSIC = "primary_ssic_code"
COL_SSIC_TEXT = "primary_ssic_description"
COL_ACTIVITY = "primary_user_described_activity"
COL_OFFICERS = "no_of_officers"

# Absent any one of these, the file is not the file this was written against.
# A missing column must be a loud failure: SSIC or the date column moving would
# otherwise read as a month in which Singapore incorporated no software company.
REQUIRED_COLUMNS = (COL_UEN, COL_NAME, COL_STATUS, COL_INCORPORATED, COL_SSIC)

# SSIC division 62 is computer programming, consultancy and related activities.
# The CODE, never the description: primary_ssic_description was the string "na"
# on every sampled row. See the module docstring.
DEFAULT_SSIC_PREFIXES = ("62",)

# The industry vocabulary value each prefix maps to. A prefix with no entry
# stores no industry rather than guessing one, so widening TIT_SG_SSIC by hand
# cannot silently label a construction company "technology".
INDUSTRY_BY_PREFIX = {"62": "technology"}

# What "62" means, in the register's own division wording, so the summary can
# say what the code is without leaning on the "na" description field.
SSIC_DIVISION = {
    "62": "computer programming, consultancy and related activities",
}

# An entity still on the register. ACRA also publishes struck off, dissolved,
# amalgamated and in-liquidation entities in the same file, and a company that
# is already gone is not a new employer.
LIVE_STATUS_PREFIX = "LIVE"

# The collection is 27 files. Below this the collection metadata has changed
# shape or been truncated, and a short list of files is a short run that would
# look like a quiet month.
MIN_DATASETS = 20
MEASURED_DATASETS = 27

# One refresh cycle plus a fortnight, because the file is monthly: a run that
# lands just after a refresh must still cover everything the previous one could
# not see.
DEFAULT_DAYS = 45

# The register states incorporation dates back to the nineteenth century, so a
# window is not bounded by the source the way Israel's rolling year is. It is
# bounded here on purpose: past a year this stops being a window and becomes a
# backfill of the whole register, which is a different job with a different
# review.
MAX_DAYS = 365

# The one measured file, kept as three numbers rather than one derived constant
# so a later reader can see exactly what was counted and re-do the arithmetic:
# 138 qualifying incorporations, in 57,533 rows, over 365 days.
MEASURED_QUALIFYING = 138
MEASURED_ROWS = 57_533
MEASURED_WINDOW_DAYS = 365

# How much less than the measured rate a run may produce before it is treated as
# a breakage. Ten times' margin, because the rate above was measured on ONE of
# the 27 files and the letters are not identical; a floor that assumed every
# file behaves like "W" would be a false alarm waiting for a quiet letter.
FLOOR_MARGIN = 10

# 27 files of the order of 18MB each. The delay is a courtesy: data.gov.sg
# publishes no anonymous rate limit but states that signing up raises the limit,
# which only means anything if an anonymous one exists.
REQUEST_DELAY = 2.0

# One dataset page is shared by every company whose name begins with that
# letter, so marking it seen would drop every company after the first. NOT a
# claim that a company is incorporated twice: it is incorporated once, which is
# why the fingerprint below is the UEN. See the module docstring.
REVISITS_ITS_SOURCE_URL = True

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SSIC_PREFIX = re.compile(r"^\d{2,5}$")
_DIGITS = re.compile(r"^\d+$")

# Values the register writes where it has nothing to say. Matched case
# insensitively and never rendered.
_EMPTY_MARKERS = {"", "na", "n.a.", "n/a", "-", "nil", "none", "not available"}


class SingaporeAcraError(RuntimeError):
    """A run could not be read, or came back implausibly empty."""


# --- configuration ---------------------------------------------------------

def window_days(days: int | None = None) -> int:
    """The incorporation window, in days.

    Validated the way `israel_registrar.window_days` validates TIT_IL_DAYS: a
    value that is not a plain number of days inside the permitted range is
    refused loudly rather than falling back to the default, because a silent
    fallback turns a typo into a run that quietly looked at the wrong month.
    """
    if days is not None:
        return days
    raw = (os.environ.get("TIT_SG_DAYS") or "").strip()
    if not raw:
        return DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw) or not 1 <= int(raw) <= MAX_DAYS:
        raise SingaporeAcraError(
            f"TIT_SG_DAYS holds {raw!r}. The register is a monthly snapshot "
            f"and this window must be between 1 and {MAX_DAYS} days; past a "
            f"year this stops being a window and becomes a backfill of the "
            f"whole register.")
    return int(raw)


def ssic_prefixes(prefixes=None) -> tuple:
    """The primary SSIC prefixes a row must match.

    A prefix must be at least two digits. A single digit would widen a division
    filter into a whole SSIC section — "6" is every financial and IT activity
    there is — and that is the difference between an industry filter and no
    filter at all, which is the failure this connector exists to avoid.
    """
    if prefixes is not None:
        values = tuple(prefixes)
    else:
        raw = (os.environ.get("TIT_SG_SSIC") or "").strip()
        if not raw:
            return DEFAULT_SSIC_PREFIXES
        values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values:
        raise SingaporeAcraError(
            "TIT_SG_SSIC is empty. Without an SSIC prefix this would collect "
            "every company incorporated in Singapore, which is a formation "
            "firehose with no talent content in it.")
    for value in values:
        if not _SSIC_PREFIX.match(value):
            raise SingaporeAcraError(
                f"TIT_SG_SSIC holds {value!r}, which is not an SSIC prefix of "
                f"two to five digits. One digit is a whole SSIC section and "
                f"would widen the filter to most of the register.")
    return values


# --- the API ---------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _json(url: str, *, session=None, timeout: int = 60) -> dict:
    """One keyless JSON document from the portal, or a loud failure.

    ACCEPTS 200 AND 201, and the 201 is not laxity. The portal does not answer
    these routes with the same code: the collection and dataset metadata
    endpoints return 200, while `poll-download` returns **201 Created**,
    because asking for a download is modelled as creating an export job even
    when the file is already prepared and the signed URL comes straight back in
    the body. Verified live on 2026-08-04:

        collections/2/metadata        -> HTTP 200
        datasets/{id}/poll-download   -> HTTP 201

    Insisting on 200 was a real defect rather than a hypothetical one: the
    first live run of this collector died on that 201 with a message blaming
    the endpoint for having moved. Both codes carry the same JSON envelope, and
    the envelope is what is actually checked below.

    Anything else IS the portal having moved rather than a credential problem,
    and the message says so, because nobody should go looking for a key that
    does not exist.
    """
    get = (session or requests).get
    resp = get(url, headers=_headers(), timeout=timeout)
    if resp.status_code not in (200, 201):
        raise SingaporeAcraError(
            f"{url} returned HTTP {resp.status_code}. This collection is "
            f"keyless and answers 200 (metadata) or 201 (poll-download) "
            f"anonymously, so this is the endpoint having moved and not a "
            f"missing credential.")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SingaporeAcraError(
            f"{url} did not return JSON: {resp.text[:160]!r}") from exc
    if not isinstance(payload, dict):
        raise SingaporeAcraError(f"{url} returned {type(payload).__name__}, not an object")
    return payload


def collection_metadata(*, session=None) -> dict:
    """The collection's own metadata: the child dataset ids and the refresh date."""
    payload = _json(COLLECTION_METADATA_URL, session=session)
    data = payload.get("data") or {}
    meta = data.get("collectionMetadata") or {}
    if "childDatasets" not in meta:
        raise SingaporeAcraError(
            f"collection {COLLECTION_ID} returned metadata with no "
            f"'childDatasets' key (keys: {sorted(meta)[:8]}). The response "
            f"shape has changed.")
    return meta


def dataset_ids(meta: dict) -> list:
    """The 27 per-letter dataset ids, read off the collection rather than typed.

    Typing them here would be 27 identifiers this repository has to keep in step
    with somebody else's publishing, and a stale one 404s into a letter of the
    alphabet quietly going missing.
    """
    ids = [str(value).strip() for value in (meta.get("childDatasets") or [])
           if str(value).strip()]
    if len(ids) < MIN_DATASETS:
        raise SingaporeAcraError(
            f"collection {COLLECTION_ID} lists {len(ids)} datasets, against a "
            f"measured {MEASURED_DATASETS} (one per letter plus an 'others' "
            f"file). A short list is a short run, which would read as a quiet "
            f"month rather than as the breakage it is.")
    return ids


def download_url(dataset_id: str, *, session=None) -> str:
    """The signed url for one dataset's CSV, requested fresh.

    The url expires, so it is never cached in this repository and never written
    to disk. A poll-download that is anything other than DOWNLOAD_SUCCESS is a
    failure, not an empty file: the alternative is treating a refused download
    as a letter of the alphabet with no companies in it.
    """
    payload = _json(POLL_DOWNLOAD_URL.format(dataset_id=dataset_id), session=session)
    if payload.get("code") != 0:
        raise SingaporeAcraError(
            f"poll-download for {dataset_id} answered code "
            f"{payload.get('code')!r}: {str(payload.get('errorMsg'))[:160]}")
    data = payload.get("data") or {}
    status = str(data.get("status") or "").strip()
    if status != "DOWNLOAD_SUCCESS":
        raise SingaporeAcraError(
            f"poll-download for {dataset_id} answered status {status!r} rather "
            f"than DOWNLOAD_SUCCESS. That is a download that did not happen, "
            f"not a file with nothing in it.")
    url = str(data.get("url") or "").strip()
    if not url:
        raise SingaporeAcraError(
            f"poll-download for {dataset_id} reported success and gave no url")
    return url


def fetch_dataset_csv(dataset_id: str, *, session=None, timeout: int = 300) -> str:
    """One per-letter CSV as text.

    Held one file at a time and released, never all 27: the measured file is
    18,628,748 bytes and the collection is 27 of that order.
    """
    get = (session or requests).get
    url = download_url(dataset_id, session=session)
    resp = get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if resp.status_code != 200:
        raise SingaporeAcraError(
            f"the signed download for {dataset_id} returned HTTP "
            f"{resp.status_code}. These urls expire, so a stale one is a bug "
            f"in this collector rather than an outage.")
    return resp.text


# --- parsing ---------------------------------------------------------------

def parse_rows(text: str, *, dataset_id: str = "") -> list:
    """Every record in one published CSV, with the expected columns asserted.

    The column check is the whole guard against a silent zero: if
    `primary_ssic_code` or `registration_incorporation_date` is renamed, every
    row stops qualifying and the run looks like a month in which Singapore
    incorporated no software company.
    """
    reader = csv.DictReader(io.StringIO(text))
    columns = set(reader.fieldnames or [])
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise SingaporeAcraError(
            f"dataset {dataset_id or '?'} is missing the column(s) "
            f"{', '.join(missing)}. The measured file carries 53 columns "
            f"including all of them, so this is the publisher renaming a "
            f"column and not an empty file. Every row would silently stop "
            f"qualifying.")
    rows = list(reader)
    if not rows:
        raise SingaporeAcraError(
            f"dataset {dataset_id or '?'} has a header and no rows. A "
            f"published letter of the register cannot be empty; the measured "
            f"file held {MEASURED_ROWS:,} rows.")
    return rows


def parse_date(raw: str) -> str | None:
    """The register writes ISO `YYYY-MM-DD`. Returns it back, or None.

    Only ISO is accepted. A row whose incorporation date will not parse is
    DROPPED rather than stored against today, because an incorporation on the
    wrong date is worse than one we did not store, and a snapshot has no second
    field to fall back to.
    """
    text = (raw or "").strip()
    if not _ISO.match(text):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def is_live(record: dict) -> bool:
    """Whether the register still carries this entity as live.

    A company incorporated last month and already struck off is not a new
    employer. Matched on the leading word so "Live Company" and a bare "Live"
    both pass and "Struck Off" does not.
    """
    return (record.get(COL_STATUS) or "").strip().upper().startswith(LIVE_STATUS_PREFIX)


def ssic_code(record: dict) -> str:
    return (record.get(COL_SSIC) or "").strip()


def matches_ssic(record: dict, prefixes) -> bool:
    """Whether the entity's PRIMARY SSIC code is in scope.

    The primary code only. A secondary code is a second activity a company may
    or may not do, and treating it as the industry would put every business with
    an incidental IT line into a software feed.
    """
    code = ssic_code(record)
    if not code:
        return False
    return any(code.startswith(prefix) for prefix in prefixes)


def ssic_description(record: dict) -> str:
    """The register's own description of the primary SSIC, or an empty string.

    `primary_ssic_description` was literally "na" on every sampled 2025-2026
    row, so this field is a trap rather than a source: rendered unchecked it
    puts "operates in na" on a public page. Anything in `_EMPTY_MARKERS` is
    nothing, and nothing is what gets stored.
    """
    text = re.sub(r"\s+", " ", (record.get(COL_SSIC_TEXT) or "")).strip()
    return "" if text.lower() in _EMPTY_MARKERS else text


def officer_count(record: dict) -> int | None:
    """`no_of_officers` as an integer, or None. Never a zero standing in for
    "not stated": the two are different facts and only one of them is a size."""
    text = (record.get(COL_OFFICERS) or "").strip()
    if not _DIGITS.match(text):
        return None
    return int(text)


def entity_name(record: dict) -> str:
    """The register's own name for the entity, whitespace collapsed and nothing
    else. Never re-cased: ACRA publishes "WORLDAI PTE. LTD." and a company name
    is exactly the field that must not be improved."""
    return re.sub(r"\s+", " ", (record.get(COL_NAME) or "")).strip()


def industry_for(code: str, prefixes) -> str:
    """The industry vocabulary value for a code, or an empty string.

    Keyed on the prefix that matched, so a hand-widened TIT_SG_SSIC cannot label
    an unrelated division "technology".
    """
    for prefix in sorted(prefixes, key=len, reverse=True):
        if code.startswith(prefix) and prefix in INDUSTRY_BY_PREFIX:
            return INDUSTRY_BY_PREFIX[prefix]
    return ""


def _division(code: str, prefixes) -> str:
    for prefix in sorted(prefixes, key=len, reverse=True):
        if code.startswith(prefix) and prefix in SSIC_DIVISION:
            return SSIC_DIVISION[prefix]
    return ""


def _pretty(iso: str) -> str:
    """'2025-08-03' -> '3 August 2025'."""
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


# --- one incorporation -----------------------------------------------------

def _row(record: dict, *, dataset_id: str = "", prefixes=None) -> dict | None:
    """The raw dict for one incorporation, or None if the row cannot carry one.

    `raw_text` is set here and is load-bearing: the classifier reads ONLY
    raw_text, and a collector that leaves it empty stores nothing while
    reporting success. That has already happened on this codebase, so it is
    asserted in the tests rather than trusted.
    """
    codes = tuple(prefixes) if prefixes is not None else ssic_prefixes()
    name = entity_name(record)
    uen = (record.get(COL_UEN) or "").strip()
    when = parse_date(record.get(COL_INCORPORATED) or "")
    code = ssic_code(record)
    if not (name and uen and when and code):
        return None
    if not matches_ssic(record, codes):
        return None
    if not is_live(record):
        return None

    officers = officer_count(record)
    division = _division(code, codes)
    described = ssic_description(record)
    kind = re.sub(r"\s+", " ", (record.get(COL_TYPE) or "")).strip()

    headline = f"{name}: incorporated in Singapore on {_pretty(when)}"

    # The summary is built HERE and `as_classified` returns it unchanged, so it
    # is a literal prefix of `raw_text` and every figure in it is verbatim in
    # the source text by construction rather than by care. Composing the two
    # separately is what cost estonia_ariregister twelve of its first 66 rows.
    officers_sentence = (
        f"The register records {officers} officer(s) on the entity. "
        if officers is not None else "")
    summary = (
        f"ACRA, the registrar Singapore companies are incorporated with, "
        f"records that {name} (UEN {uen}) was incorporated on {_pretty(when)} "
        f"with primary SSIC code {code}"
        + (f", which is {division}" if division else "")
        + f". {officers_sentence}{ATTRIBUTION}"
    )
    body = (
        f"{summary} The register is published as open data and refreshed "
        f"monthly, so this is the legal fact that a company now exists and "
        f"nothing more: the file states no funding, no share capital and no "
        f"dated officer change, only a current count of officers. It does not "
        f"say whether the company has hired anyone or begun trading."
        + (f" The register describes the entity as {kind}." if kind else "")
        + (f" It describes the activity as {described}." if described else "")
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "summary": summary,
        "source_url": DATASET_PAGE.format(dataset_id=dataset_id),
        "source_name": SOURCE_NAME,
        "discovery_url": COLLECTION_PAGE,
        "published_date": when,
        "company": name,
        "country": "Singapore",
        # A city-state. Reading this off the country rather than off the
        # register's street address on purpose: a registered office is not
        # where a workforce sits, and nothing here parses an address.
        "hq_city": "Singapore",
        "industry": industry_for(code, codes),
        "uen": uen,
        "ssic_code": code,
        "ssic_description": described,
        "entity_type": kind,
        "entity_status": (record.get(COL_STATUS) or "").strip(),
        "no_of_officers": officers,
        "incorporated_on": when,
        "dataset_id": dataset_id,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def emptiness_floor(rows_read: int, days: int) -> int:
    """How few incorporations is too few to be a quiet month.

    Scaled to what was actually read rather than typed as one number, because
    both the number of files and the window are inputs. The rate is the one
    measured file and nothing else: 138 qualifying incorporations in 57,533
    rows over 365 days, divided by a margin of ten because the other 26 letters
    were not counted and are not assumed to behave the same way.
    """
    rate = MEASURED_QUALIFYING / (MEASURED_ROWS * MEASURED_WINDOW_DAYS)
    return max(1, int(rows_read * days * rate / FLOOR_MARGIN))


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            session=None, datasets=None, prefixes=None) -> list[dict]:
    """Every software or IT company incorporated in Singapore inside the window.

    `queries` is accepted and IGNORED so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the register IS
    the population.

    `datasets` accepts an explicit list of dataset ids so a rehearsal can read
    one letter instead of 27.
    """
    window = window_days(days)
    codes = ssic_prefixes(prefixes)
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()

    if datasets is None:
        meta = collection_metadata(session=session)
        ids = dataset_ids(meta)
        print(f"[{COLLECTOR}] collection {COLLECTION_ID} last updated "
              f"{meta.get('lastUpdatedAt')} ({meta.get('frequency')})")
    else:
        ids = list(datasets)

    print(f"[{COLLECTOR}] window {start}..{end} ({window}d) over {len(ids)} "
          f"files, primary SSIC {'/'.join(codes)}; keyless, no model")

    out: list[dict] = []
    seen: set[str] = set()
    rows_read = in_scope = not_live = undated = 0

    for position, dataset_id in enumerate(ids):
        if session is None and position:
            time.sleep(REQUEST_DELAY)
        records = parse_rows(fetch_dataset_csv(dataset_id, session=session),
                             dataset_id=dataset_id)
        rows_read += len(records)
        kept = 0
        for record in records:
            if not matches_ssic(record, codes):
                continue
            in_scope += 1
            when = parse_date(record.get(COL_INCORPORATED) or "")
            if not when:
                undated += 1
                continue
            if not start <= when <= end:
                continue
            if not is_live(record):
                not_live += 1
                continue
            row = _row(record, dataset_id=dataset_id, prefixes=codes)
            if row is None:
                continue
            # A UEN is one entity, and an entity is incorporated once. Two rows
            # with the same UEN are the same company read twice, never two
            # incorporations.
            if row["uen"] in seen:
                continue
            seen.add(row["uen"])
            out.append(row)
            kept += 1
        print(f"[{COLLECTOR}]   {dataset_id}: {len(records)} rows, "
              f"{kept} inside the window")

    print(f"[{COLLECTOR}] {rows_read} rows read, {in_scope} in the SSIC scope, "
          f"{undated} with no usable incorporation date, {not_live} no longer "
          f"live, {len(out)} incorporations stored")

    floor = emptiness_floor(rows_read, window)
    if len(out) < floor:
        raise SingaporeAcraError(
            f"{start}..{end} produced {len(out)} incorporations from "
            f"{rows_read} register rows, against a measured rate of "
            f"{MEASURED_QUALIFYING} in {MEASURED_ROWS} rows over "
            f"{MEASURED_WINDOW_DAYS} days. That is the column names or the "
            f"SSIC filter having moved, not a quiet month: Singapore does not "
            f"stop incorporating software companies.")
    return out


# --- the derived record ----------------------------------------------------

def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a column of the register or a fixed editorial line. Nothing
    on the record is something a model believed, and there is no LLM cost at
    all, which is what makes this source free.
    """
    return {
        "company": item["company"],
        # An incorporation is a company development. There is no funding pillar
        # in the vocabulary, and this source states no funding anyway.
        "pillar": "company_development",
        # Never `hiring`. The register says a company now exists and nothing
        # about anybody being employed by it.
        "signal_direction": "neutral",
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text` rather than a second telling of it.
        "summary": item["summary"],
        "talent_readthrough": (
            "Every Singapore company is incorporated with ACRA, so this is a "
            "complete record of new software and IT companies being formed "
            "there rather than a selective one: it covers the companies no "
            "outlet writes about on the same basis as the ones it does. Read "
            "the shape of it as carefully as the rows. It is an incorporation "
            "and nothing else, so it says a company now exists and not that it "
            "has raised money, hired anyone or begun trading, and the officer "
            "count is a size proxy rather than a headcount. The published file "
            "is a monthly snapshot rather than a stream of filings, so a "
            "company can be up to about a month old before it appears here, "
            "and the register carries no dated officer change and no funding "
            "of any kind, which makes this narrower than the UK and Israeli "
            "registries read alongside it. A single incorporation is worth "
            "reading as a company being formed; a run of them in one part of "
            "the industry code is worth reading as where new formation is "
            "happening."
        ),
        "country": "Singapore",
        # A city-state, so the city is a fact rather than a guess.
        "headquarters_city": "Singapore",
        "headquarters_country": "Singapore",
        "industry": item.get("industry") or "",
        "employer_type": "",
        # A statutory register, published by the registrar companies are
        # incorporated with. infer_confidence caps this at what the host is
        # worth, and data.gov.sg is in vocab.PRIMARY_SOURCE_DOMAINS, so it lands
        # at 'verified'.
        "confidence": "verified",
    }

"""Czech company register (ARES) — per-person office dates, both directions,
filtered to the employers the state's own statistics call large.

The Czech Republic publishes what almost nobody else does: for every entry in
the veřejný rejstřík it states, per person, the date the office ACTUALLY BEGAN
and the date it ENDED, separately from the date the court registered either.
Companies House gives `appointed_on` and no reason for a departure; SEBI gives
a filed category; EDINET gives a clause number and no person at all. ARES gives
a named person, a named role, a start date, an end date, and the registration
date of each, in JSON, keyless.

    POST /ekonomicke-subjekty-notifikace/vyhledat  {"datovyZdroj":"vr"}
    GET  /ekonomicke-subjekty-notifikace/datovy-zdroj/vr/cislo-davky/{n}
    POST /ekonomicke-subjekty-res/vyhledat         {"ico":[... up to 100 ...]}
    GET  /ekonomicke-subjekty-vr/{ico}

all under https://ares.gov.cz/ekonomicke-subjekty-v-be/rest.

THE THREE-STAGE FUNNEL, MEASURED LIVE ON A REAL RUN 2026-07-30
==============================================================

One dry run of the shipped collector over the default 14-day window
(2026-07-16..07-30):

| stage | what it costs | what survives |
|---|---|---|
| notification feed, 13 batches | 14 requests | 10,483 notifications, 10,190 distinct companies |
| RES employee band, 100 ICOs a request | 102 requests | **92** companies at 250+ employees (0.9%) |
| VR record, one request each | 92 requests | **108** office events, all 108 stored |

**108 rows a fortnight, so roughly 2,800 a year** — the largest non-US
structured source in the tracker after India's, and the only one that reports
departures. The whole run is 208 requests and about two minutes.

Over a longer arc, 2026-07-02..07-29: the feed carried **24,651 notifications
across 23 batches, 22,492 distinct companies, about 880 a day**, of which
**226 (1.0%)** are employers of 250 people or more.

That 1.0% is the reason there is a filter at all: 12,474 of those 22,492 are
`s.r.o.` limited companies whose employee band is `Bez zaměstnanců` or unstated.
Collecting the feed unfiltered would put hundreds of thousands of rows a year of
one-person holding companies into a database that holds 15,711 signals in total.

THE MATERIALITY FILTER, AND THE HOLE IN IT THAT MUST BE SAID OUT LOUD
---------------------------------------------------------------------

`GET /ekonomicke-subjekty-res/{ico}` returns
`statistickeUdaje.kategoriePoctuPracovniku`, a banded employee count from the
statistical register. The codebook is published at
`/ciselniky-nazevniky/vyhledat` (`kodCiselniku: KategoriePoctuPracovniku`) and
was read rather than guessed: **`330` is `250 - 499 zaměstnanců`**, so
`code >= "330"` is exactly the 250-employee line the UK gender pay gap duty
draws, and `BAND_FLOOR` is that code.

Three caveats, each measured on the same 22,492 companies, and each of them a
RECALL hole rather than a precision one:

1. **`000 Neuvedeno` is 12,624 of the 19,285 RES records (65%).** The band is
   not stated for two thirds of Czech companies, and it is unstated for **567
   of the 1,362 joint-stock companies (41.6%)** in the sample. A large employer
   whose statistical band was never populated is silently invisible here.
2. **3,207 of the 22,492 (14.3%) have no RES record at all.** VR and RES are
   different registers with different populations.
3. **The band goes stale.** ČEZ's RES record carries
   `datumAktualizace: 2023-06-29`. Among the 226 material companies the
   distribution is 2023: 75, 2024: 23, 2025: 45, 2026: 83, so a third of them
   are working from a three-year-old count.

There is **no search-by-band**: `EkonomickeSubjektyRegistraceFiltr` accepts an
`ico` array and nothing else (read from the OpenAPI document at
`/ekonomicke-subjekty-v-be/rest/v3/api-docs`), so a roster cannot be built the
way `companies_house` builds one from the pay-gap CSV. The change feed is what
makes the per-ICO lookup affordable instead.

**Legal form was considered as a proxy and refused with the number.** Filtering
to `a.s.` joint-stock companies (`pravniForma` 121) would poll 1,362 companies
in that window to find 117 material ones — **8.6% precision**, six times the
volume for a population that is 91% not what we are looking for. That is the
same shape as the UK accounts-category filter (6.35%) and it fails for the same
structural reason: legal form records how a business is owned, not how many
people it employs.

WHAT `datumVymazu` IS NOT, AND THIS IS THE TRAP THAT WOULD HAVE SHIPPED
-----------------------------------------------------------------------

The VR record is a full VERSION HISTORY. One person's one membership appears
once per amendment, each version carrying `datumZapisu` (registered) and
`datumVymazu` (that VERSION superseded). On ČEZ's record, **353 of 543 member
versions carry a `datumVymazu` and no `zanikClenstvi`** — they are corrections,
not departures. Martin Novák's board membership beginning 2026-05-25 appears
twice, the first version deleted on 2026-06-27, purely because his academic
titles were added; he is still on the board today.

So `datumVymazu` is never a departure and never a date on a record here. A
departure is `clenstvi.clenstvi.zanikClenstvi` and nothing else.

**And the obvious repair for that — read only the live version — is wrong in
the other direction, which cost a second pass to find.** Jean-Charles Chen's
seat at ICO 17774713 has two versions: the live one (`datumZapisu 2026-07-22`,
no `datumVymazu`) says `Člen správní rady` with no dates at all, and the
superseded one says `zanikFunkce: 2026-07-10` for `Předseda správní rady`. He
stopped being chairman on 10 July and stayed on the board, and **the only place
that fact exists is the version the register has already deleted.** Reading the
live version alone loses it; reading every version loses nothing and reports his
arrival four times.

So `memberships()` GROUPS the versions on (organ, person, membership start) and
`_events` reads them all, deduplicating a membership event on (kind, date) and a
role event on (kind, date, role) — because one person can be promoted and
demoted inside one unbroken membership and those are genuinely two facts, while
the same arrival restated by five amendments is one.

The registration date is kept as well as the office date, and it is read from
the right field per event kind: an arrival is registered when its version was
WRITTEN (`datumZapisu`), a departure when the sitting version was flagged
DELETED (`datumVymazu`) — that is the only date the register gives for one.
Neither date is inferred and neither is diffed out of two snapshots.

WHY THE WINDOW IS ON THE REGISTRATION DATE AND THE EVENT DATE IS THE OFFICE ONE
-------------------------------------------------------------------------------

This was got wrong first and the live dry run said so, which is the only way it
could have been found. Filtering on the OFFICE date — the obvious reading of
"use the office dates as the event date" — asks the feed which companies changed
this week and then throws away every change whose effective date was earlier
than the window, which is most of them. **A real seven-day run
(2026-07-23..07-30, 76 material companies) produced ZERO events that way** and
tripped the emptiness floor, correctly.

The register writes a change down after it happens, and the feed announces the
writing. So the window belongs on the registration date. Same 76 companies, same
week, selecting on registration instead: **41 events**, of which 18 arrivals,
13 departures, 8 promotions and 2 role endings. The office-to-registration lag
on those runs a median of 25 days, which is exactly why a 7-day office-date
window found nothing.

Both dates are still source-stated and both are still on the record. What
changed is only which of them decides that an event is new.

`MAX_BACKLOG_DAYS` is the other half of that decision. Seven of those 41 events
had office dates one to **ten years** before their registration: a court finally
writing down a 2016 board change. There is no honest date to publish those
under — the true one puts a decade-old change on a dashboard of this week's
market, and today's is a figure nobody stated — so they are declined and
counted.

PERSONAL DATA: TAKEN AT THE BOUNDARY, NEVER PERSISTED
------------------------------------------------------

The Czech national open data catalogue states this dataset's conditions of use
as `neobsahuje autorská díla`, `není autorskoprávně chráněnou databází`,
`není chráněna zvláštním právem pořizovatele databáze` (`narrowMatch` CC0) —
**and `obsahuje osobní údaje`**, contains personal data. The publisher says so
itself, and the record bears it out: `fyzickaOsoba` carries `datumNarozeni` on
13,834 of 15,645 person rows in the material sample and a full residential
address (`adresa.textovaAdresa`, street, house number, postcode) on 15,619 of
them.

**`scrub_person` is the only way a person reaches a row from here**, and it
returns four things: given name, surname, the role, nothing else. Birth date,
address, `bydliste`, citizenship and the free-text `textOsoba` are dropped
inside the collector, before a dict is built, so no later stage can leak what it
never received. Asserted by `tests/test_czechia_ares.py`.

ACCESS
------

`ares.gov.cz/robots.txt` is `User-agent: *` / `Disallow: /cms/` and every path
used here is outside it. The Ministry of Finance publishes the operating limit
as more than 500 queries a minute and reserves the right to cut off anyone who
"automatizovaně propátrává databázi náhodnými údaji" — automatically probes the
database with random values. This connector never guesses an ICO: every lookup
comes from the register's own change feed, and `REQUEST_DELAY` puts a full run
(~475 requests for a 14-day window) at roughly two minutes and a quarter of the
stated ceiling.

`source_url` IS THE API DOCUMENT, AND THE TWO ALTERNATIVES WERE CHECKED
-----------------------------------------------------------------------

* `ares.gov.cz/ekonomicke-subjekty/{ico}` is a Vue application. It answers
  **HTTP 200 with the same 912-byte shell** for ČEZ and for the invented ICO
  `00000001`. That is the EDINET viewer trap and Korea's "Reject" body: a URL
  that cannot fail is not a receipt.
* `or.justice.cz/ias/ui/rejstrik-$firma?ico=` is the Ministry of Justice's own
  register and is the nicest page for a human — but `or.justice.cz/robots.txt`
  carries `Disallow: /ias/`, which is the whole application, so citing it would
  make `link_check.py` record every Czech row as `robots` and check none of
  them.

So the citation is `ekonomicke-subjekty-vr/{ico}`, the JSON this record is
derived from. It is robots-permitted, it is stable on a permanent identifier,
and a bogus ICO is an unambiguous **HTTP 404** with
`{"kod":"NENALEZENO", ...}` rather than a page that looks the same as a real
one. One company has one such URL and can appoint many people, so
`REVISITS_ITS_SOURCE_URL` is set: dedup happens on `content_hash`.

WHAT IS NOT COLLECTED
---------------------

* **A body corporate is not an employee.** A member with no `fyzickaOsoba` is a
  company sitting on another company's board. 49 of 15,694 member versions in
  the material sample. Same judgement as `companies_house`'s `corporate-*`
  roles and `bse_india`'s auditors.
* **`KONTROLNI_KOMISE_CLEN`**, a cooperative's internal control commission. It
  audits the body it sits in, which is the auditor judgement again.
* **A direction of `hiring` or `displacement`.** The register records that an
  office began or ended and never why. A group manager added to a subsidiary
  board is written identically to an external chief executive hire, and one
  director leaving is not a workforce reduction — that is the sibling tracker's
  scope. Every row here is `neutral`.
* **A city.** `sidlo` is a registered office, not where the workforce sits, so
  it goes to `headquarters_city` through the shared gazetteer and only when it
  normalises. Nothing here splits an address on a comma.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from pipeline import vocab

BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
FEED_URL = BASE + "/ekonomicke-subjekty-notifikace/vyhledat"
BATCH_URL = BASE + "/ekonomicke-subjekty-notifikace/datovy-zdroj/{zdroj}/cislo-davky/{cislo}"
RES_SEARCH_URL = BASE + "/ekonomicke-subjekty-res/vyhledat"
VR_URL = BASE + "/ekonomicke-subjekty-vr/{ico}"

COLLECTOR = "czechia_ares"
SOURCE_NAME = "ARES Czech company register (veřejný rejstřík)"

# The register is a public service and gets a real name and a contact address,
# the same as SEC and Companies House.
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

# Attribution travels in the summary of every stored row rather than living only
# in this docstring, exactly as uk_paygap and companies_house do.
ATTRIBUTION = ("Source: ARES, the Czech Ministry of Finance's register of "
               "economic subjects, which republishes the public register "
               "(veřejný rejstřík) maintained by the courts.")

# The data source inside the notification service. `vr` is the public register;
# the others (`res`, `rzp`, ...) notify about different registers entirely.
FEED_SOURCE = "vr"

# The Ministry states the operating limit as more than 500 queries a minute.
# 0.25s is 240 a minute, under half of it, and leaves room for a retry.
REQUEST_DELAY = 0.25
RETRY_WAIT = 20
RETRIES = 4

# The RES search refuses more than 100 ICOs in one filter, with HTTP 400 and
# `VSTUP_PRILIS_MNOHO_HODNOT`. Measured 2026-07-30: 50 and 100 accepted, 200
# refused. This is the endpoint's number, not a choice of ours.
RES_BATCH = 100

# How far back a run reads. Weekly cron, so one cadence plus a week of overlap:
# a skipped run loses nothing because a re-seen event is an exact content_hash
# duplicate and costs nothing to see twice.
DEFAULT_DAYS = 14

# The feed's own horizon, MEASURED 2026-07-30: `vyhledat` returned 23 batches
# running 2026-07-02 to 2026-07-29, so about four weeks of history and no more.
# Unlike BSE's window cap this refusal is silent — asking for 90 days simply
# returns the same 23 batches — so the ceiling is enforced here rather than
# discovered as a suspiciously quiet quarter. There is no backfill route past
# it: the feed is the only index of what changed, and ARES publishes no archive
# of retired batches.
FEED_HORIZON_DAYS = 28

# How far an office date may sit BEFORE the registration that reports it and
# still be this week's news. Measured on the 2026-07-23..07-30 feed: of the 41
# events registered in that window, 29 happened within 30 days and 34 within
# 60 — and the other seven happened between one and TEN years earlier. Those
# are backlog corrections, and there is no honest date to publish them under:
# their true office date puts a 2016 board change on a dashboard of this week's
# market, and today's date is a figure nobody stated. Declined, and counted.
MAX_BACKLOG_DAYS = 365

# The employee band that means 250 or more, read from the register's own
# codebook (`KategoriePoctuPracovniku`) on 2026-07-30 rather than guessed:
#
#   310  100 - 199      320  200 - 249      330  250 - 499      340  500 - 999
#   410  1000 - 1499    ...                 470  5000 - 9999    510  10 000+
#
# Codes are three-digit strings and compare correctly as strings. `000` is
# `Neuvedeno`, not zero, and never passes.
BAND_FLOOR = "330"

BAND_LABELS = {
    "330": "250 to 499", "340": "500 to 999", "410": "1,000 to 1,499",
    "420": "1,500 to 1,999", "430": "2,000 to 2,499", "440": "2,500 to 2,999",
    "450": "3,000 to 3,999", "460": "4,000 to 4,999", "470": "5,000 to 9,999",
    "510": "10,000 or more",
}

# The engagement types that are a PERSON holding an office. Keyed on the API's
# own `typAngazma` enum, verbatim, the way companies_house keys on
# `officer_role`. A value not on this list is a declined row and is counted,
# never quietly accepted as a near-miss.
ENGAGEMENTS = {
    "STATUTARNI_ORGAN_CLEN": "a member of the statutory body",
    "SPRAVNI_RADA_CLEN": "a member of the administrative board",
    "DOZORCI_RADA_CLEN": "a member of the supervisory board",
    "PROKURA_OSOBA": "a procurist",
}

# Named rather than merely omitted, so a later reader can see the exclusion was
# a decision. A control commission audits the body it sits in.
EXCLUDED_ENGAGEMENTS = ("KONTROLNI_KOMISE_CLEN",)

# The four events one membership can produce, and the phrase each earns. A role
# change is only an event when the role date differs from the membership date —
# otherwise it is the same appointment said twice, which is how ČEZ's one board
# arrival became two rows in the first measurement.
EVENT_TOOK_OFFICE = "took_office"
EVENT_LEFT_OFFICE = "left_office"
EVENT_TOOK_ROLE = "took_role"
EVENT_LEFT_ROLE = "left_role"

# The source URL is one page per COMPANY and a company appoints many people, so
# marking it seen would make the first event the last one this collector ever
# reported for that employer. Same reasoning as companies_house and ats_boards.
REVISITS_ITS_SOURCE_URL = True

# Below this many material companies a run is a hand-narrowed dispatch and an
# emptiness floor would fire on a population too small to expect anything from.
# At or above it the floor applies: the measured rate is 108 events from 92
# material companies over 14 days (1.17 each), so one per 25 is a floor no
# working run can fail, with nearly thirty times the margin. It is set that
# loose on purpose — the first design of this collector produced ZERO on a real
# window, and a floor tight enough to be interesting is a floor that goes red on
# a genuinely quiet fortnight and teaches people to ignore it.
FLOOR_APPLIES_ABOVE = 50
FLOOR_PER_COMPANIES = 25
FLOOR_MINIMUM = 1

# The feed carried 24,651 notifications over 28 days, about 880 a day. A tenth
# of that is a floor a working feed cannot fail, and a feed that has gone quiet
# or changed shape will.
MIN_NOTIFICATIONS_PER_DAY = 80

_ICO = re.compile(r"^\d{8}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AresError(RuntimeError):
    """A run could not be read, or came back implausibly empty."""


# --- configuration ---------------------------------------------------------

def days_from_env(default_days: int | None = None) -> int:
    """How many days back to read. Set by the workflow.

    Refused above FEED_HORIZON_DAYS, because the notification service simply
    returns the batches it has and a wider request looks identical to a
    narrower one. A silent ceiling is the BSE lesson with the alarm removed.
    """
    raw = (os.environ.get("TIT_ARES_DAYS") or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw):
        raise AresError(f"TIT_ARES_DAYS holds {raw!r}, which is not a number of days")
    days = int(raw)
    if days < 1:
        raise AresError("TIT_ARES_DAYS must be at least 1 day")
    if days > FEED_HORIZON_DAYS:
        raise AresError(
            f"TIT_ARES_DAYS is {days}. The ARES notification feed held only "
            f"{FEED_HORIZON_DAYS} days of batches when this was measured "
            f"(2026-07-30), and it answers a wider request with the same list "
            f"rather than an error, so this run would report a quiet month it "
            f"never actually read. There is no backfill past the feed: ARES "
            f"publishes no archive of retired batches.")
    return days


def band_floor(value: str | None = None) -> str:
    """The employee band code at or above which a company is collected."""
    floor = (value or os.environ.get("TIT_ARES_MIN_BAND") or BAND_FLOOR).strip()
    if floor not in BAND_LABELS:
        raise AresError(
            f"TIT_ARES_MIN_BAND={floor!r} is not one of "
            f"{', '.join(sorted(BAND_LABELS))}. Those are the codes at or above "
            f"250 employees; anything lower is not the materiality this "
            f"connector rests on.")
    return floor


def is_material(band: str | None, floor: str = BAND_FLOOR) -> bool:
    """Whether a RES employee band clears the floor.

    `None` and `000` (Neuvedeno) are both "the band is not stated" and neither
    passes. That is a recall hole and it is stated on the record rather than
    guessed around: see the module docstring.
    """
    code = (band or "").strip()
    if not re.fullmatch(r"\d{3}", code):
        return False
    return code >= floor


# --- HTTP ------------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/json"}


def _request(method: str, url: str, *, body=None, session=None, timeout: int = 90):
    """One call, retried on a transient 5xx and never on a 404."""
    call = getattr(session or requests, method)
    resp = None
    for attempt in range(RETRIES):
        kwargs = {"headers": _headers(), "timeout": timeout}
        if body is not None:
            kwargs["data"] = json.dumps(body)
        resp = call(url, **kwargs)
        if resp.status_code < 500:
            break
        if session is None:
            time.sleep(RETRY_WAIT)
    return resp


def _json(resp, what: str) -> dict:
    if resp.status_code != 200:
        raise AresError(f"{what} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AresError(f"{what} did not return JSON: {resp.text[:160]!r}") from exc


def fetch_batches(*, session=None) -> list[dict]:
    """Every notification batch the feed is currently holding."""
    payload = _json(_request("post", FEED_URL, body={"datovyZdroj": FEED_SOURCE},
                             session=session), "the notification feed")
    if "notifikacniDavky" not in payload:
        raise AresError(
            f"the notification feed returned a payload with no "
            f"'notifikacniDavky' key (keys: {sorted(payload)[:8]}). The "
            f"response shape has changed.")
    return payload.get("notifikacniDavky") or []


def fetch_batch(cislo: int, *, session=None) -> list[dict]:
    """The companies one batch says changed."""
    url = BATCH_URL.format(zdroj=FEED_SOURCE, cislo=cislo)
    payload = _json(_request("get", url, session=session), f"batch {cislo}")
    if "seznamNotifikaci" not in payload:
        raise AresError(
            f"batch {cislo} returned a payload with no 'seznamNotifikaci' key "
            f"(keys: {sorted(payload)[:8]}). The response shape has changed.")
    return payload.get("seznamNotifikaci") or []


def fetch_bands(icos: list[str], *, session=None) -> dict:
    """`ico -> employee band code` for up to RES_BATCH companies at a time.

    A company absent from the answer has no RES record at all, which is 14.3%
    of the register's changed companies and is not the same thing as an
    unstated band. Both fail `is_material`; only one of them means the join is
    broken, so they are counted separately by the caller.
    """
    out: dict[str, dict] = {}
    for start in range(0, len(icos), RES_BATCH):
        chunk = icos[start:start + RES_BATCH]
        if session is None:
            time.sleep(REQUEST_DELAY)
        payload = _json(_request("post", RES_SEARCH_URL,
                                 body={"ico": chunk, "pocet": RES_BATCH},
                                 session=session), "the RES search")
        if "ekonomickeSubjekty" not in payload:
            raise AresError(
                f"the RES search returned a payload with no "
                f"'ekonomickeSubjekty' key (keys: {sorted(payload)[:8]}). The "
                f"response shape has changed.")
        for entry in payload.get("ekonomickeSubjekty") or []:
            record = (entry.get("zaznamy") or [{}])[0]
            stats = record.get("statistickeUdaje") or {}
            out[str(entry.get("icoId") or record.get("ico") or "")] = {
                "band": (stats.get("kategoriePoctuPracovniku") or "").strip(),
                "name": (record.get("obchodniJmeno") or "").strip(),
                "town": ((record.get("sidlo") or {}).get("nazevObce") or "").strip(),
                "updated": (record.get("datumAktualizace") or "").strip(),
            }
    return out


def fetch_vr(ico: str, *, session=None) -> dict | None:
    """One company's public-register record, or None if there is no such ICO.

    A bogus ICO is HTTP 404 with `{"kod":"NENALEZENO"}`, verified live on
    2026-07-30 against `00000001` and `99999999`. None and an empty record are
    different answers and are counted separately by the caller.
    """
    resp = _request("get", VR_URL.format(ico=ico), session=session)
    if resp.status_code == 404:
        return None
    return _json(resp, f"the VR record for {ico}")


def vr_url(ico: str) -> str | None:
    """The citation. See the module docstring for why it is the API document
    and not either of the two register pages."""
    clean = (ico or "").strip()
    if not _ICO.match(clean):
        return None
    return VR_URL.format(ico=clean)


# --- reading one record ----------------------------------------------------

def scrub_person(fyzicka: dict) -> dict | None:
    """Name and nothing else. THE ONLY WAY A PERSON REACHES A ROW FROM HERE.

    ARES publishes a birth date and a full residential address for most people
    on the register, and the national catalogue's own conditions of use say the
    dataset `obsahuje osobní údaje`. The owner's ruling is that name, role,
    employer and date are taken at this boundary and everything else is dropped
    before a dict exists — not filtered at render, not stored and hidden.

    Names are returned exactly as published. The register writes some people in
    capitals and some in title case, and re-casing is what turns O'BRIEN into
    O'brien; a person's name is the field that must not be improved.
    """
    if not isinstance(fyzicka, dict):
        return None
    given = re.sub(r"\s+", " ", str(fyzicka.get("jmeno") or "")).strip()
    family = re.sub(r"\s+", " ", str(fyzicka.get("prijmeni") or "")).strip()
    if not family:
        return None
    return {"given_name": given, "family_name": family,
            "name": f"{given} {family}".strip()}


def _organs(record: dict):
    """Every (organ, member version) pair on a VR record."""
    for entry in record.get("zaznamy") or []:
        organs = ((entry.get("statutarniOrgany") or [])
                  + (entry.get("ostatniOrgany") or []))
        for organ in organs:
            for member in organ.get("clenoveOrganu") or []:
                yield organ, member


def _rank(member: dict) -> tuple:
    """How authoritative one version of a membership is.

    A version with no `datumVymazu` is the live one and wins outright. When
    every version has been superseded, the one deleted LAST is the final state —
    which is how a real departure is written, because the register flags the
    sitting record deleted and that record is the one carrying `zanikClenstvi`.
    """
    return (member.get("datumVymazu") is None,
            member.get("datumVymazu") or "",
            member.get("datumZapisu") or "")


def memberships(record: dict) -> dict:
    """Group a VR record's version history into one list per MEMBERSHIP.

    Keyed on (organ, person, membership start), because that is what identifies
    one spell in office. Each value is every version of that membership, sorted
    with the authoritative one first: ČEZ's record holds 543 member versions
    describing far fewer memberships, and without the grouping one appointment
    stores eight times over with a different role phrase on each.

    The versions are KEPT rather than collapsed away, and the reason is a real
    record. Jean-Charles Chen's seat at ICO 17774713 has two versions: the live
    one says `Člen správní rady` with no dates at all, and the superseded one —
    the only place the fact exists — says `zanikFunkce: 2026-07-10` for
    `Předseda správní rady`. He stopped being chairman that day and stayed on
    the board. Reading only the live version loses the change entirely; reading
    every version without grouping reports his arrival twice. So `_events`
    reads all of them and deduplicates per event kind.
    """
    grouped: dict = {}
    for organ, member in _organs(record):
        person = scrub_person(member.get("fyzickaOsoba") or {})
        if person is None:
            continue
        membership = (member.get("clenstvi") or {}).get("clenstvi") or {}
        key = (organ.get("nazevOrganu") or "",
               person["given_name"], person["family_name"],
               membership.get("vznikClenstvi") or "")
        grouped.setdefault(key, []).append((organ, member, person))
    for versions in grouped.values():
        versions.sort(key=lambda v: _rank(v[1]), reverse=True)
    return grouped


def _in(when, start: str, end: str) -> bool:
    text = (when or "").strip()
    return bool(_ISO_DATE.match(text)) and start <= text <= end


def _lag(office: str, registered: str) -> int:
    """How long the court took to write down a change that had already
    happened. Negative when the register recorded it in advance, which it does:
    an appointment taking effect on a stated future date is registered first."""
    if not (_ISO_DATE.match(office or "") and _ISO_DATE.match(registered or "")):
        return 0
    return (date.fromisoformat(registered) - date.fromisoformat(office)).days


def _events(record: dict, start: str, end: str, stats: dict | None = None) -> list[dict]:
    """The office events on one record REGISTERED inside the window.

    The window is on the registration date and the event date reported is the
    office date, and that pairing is the whole point — see WHY THE WINDOW IS ON
    THE REGISTRATION DATE in the module docstring for the measurement that
    forced it. Both dates are source-stated; neither is inferred.

    Never a diff of two snapshots and never `datumVymazu`: see the module
    docstring for what that field actually means and what reading it as a
    departure would have produced.

    A MEMBERSHIP event (took/left office) is identified by its kind and its
    date, so the several versions restating one arrival collapse to one row and
    the role shown is the authoritative version's. A ROLE event (took/left a
    named role) is identified by its kind, its date AND the role, because one
    person can be promoted and demoted inside one unbroken membership and those
    are different facts.
    """
    out = []
    grouped = memberships(record)
    for key in sorted(grouped):
        versions = grouped[key]
        head_organ, head_member, person = versions[0]
        engagement = (head_member.get("typAngazma") or "").strip()
        if engagement not in ENGAGEMENTS:
            continue
        body = re.sub(r"\s+", " ", str(head_organ.get("nazevOrganu") or "")).strip()
        head_funkce = ((head_member.get("clenstvi") or {}).get("funkce") or {})
        head_role = re.sub(r"\s+", " ", str(head_funkce.get("nazev") or "")).strip()

        seen: set = set()
        for _organ, member, _person in versions:
            clenstvi = member.get("clenstvi") or {}
            membership = clenstvi.get("clenstvi") or {}
            funkce = clenstvi.get("funkce") or {}
            began = (membership.get("vznikClenstvi") or "").strip()
            ended = (membership.get("zanikClenstvi") or "").strip()
            role_began = (funkce.get("vznikFunkce") or "").strip()
            role_ended = (funkce.get("zanikFunkce") or "").strip()
            role = re.sub(r"\s+", " ", str(funkce.get("nazev") or "")).strip()
            # An arrival is registered when the version was WRITTEN; a departure
            # is registered when the sitting version was flagged DELETED, which
            # is the only date the register gives for it.
            written = (member.get("datumZapisu") or "").strip()
            deleted = (member.get("datumVymazu") or "").strip()

            candidates = []
            if began and _in(written, start, end):
                candidates.append((EVENT_TOOK_OFFICE, began, head_role, written))
            if ended and _in(deleted or written, start, end):
                candidates.append((EVENT_LEFT_OFFICE, ended, head_role,
                                   deleted or written))
            # A role date equal to the membership date is the same fact twice.
            if role_began and role_began != began and _in(written, start, end):
                candidates.append((EVENT_TOOK_ROLE, role_began, role, written))
            if role_ended and role_ended != ended and _in(deleted or written,
                                                          start, end):
                candidates.append((EVENT_LEFT_ROLE, role_ended, role,
                                   deleted or written))

            for kind, when, shown, registered in candidates:
                if not _ISO_DATE.match(when):
                    continue
                # A registration that lands years after the office date is a
                # backlog correction, not this week's news. Measured on the
                # 2026-07-23..07-30 feed: 34 of 41 events registered in that
                # window happened within 60 days, and the other 7 within one
                # to ten YEARS. Storing those with their true office date
                # would put a 2016 board change on a dashboard of this week's
                # market, and storing them with today's date would be a figure
                # nobody stated. Declined, and counted.
                if _lag(when, registered) > MAX_BACKLOG_DAYS:
                    if stats is not None:
                        stats["backlog"] = stats.get("backlog", 0) + 1
                    continue
                identity = ((kind, when) if kind in (EVENT_TOOK_OFFICE,
                                                     EVENT_LEFT_OFFICE)
                            else (kind, when, shown))
                if identity in seen:
                    continue
                seen.add(identity)
                out.append({"person": person, "engagement": engagement,
                            "role": shown, "body": body, "event": kind,
                            "date": when, "registered": registered})
    return out


# --- one row ---------------------------------------------------------------

def _pretty(iso: str) -> str:
    """'2026-07-01' -> '1 July 2026'."""
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def _phrase(event: dict) -> str:
    """What the headline says the person did. Derived from the event kind and
    the role the register itself names; never a sentence anybody composed."""
    role = event["role"] or ENGAGEMENTS[event["engagement"]]
    if event["event"] == EVENT_TOOK_OFFICE:
        return f"took office as {role}"
    if event["event"] == EVENT_LEFT_OFFICE:
        return f"left office as {role}"
    if event["event"] == EVENT_TOOK_ROLE:
        return f"took the role of {role}"
    return f"left the role of {role}"


def _row(ico: str, company: dict, event: dict, *, floor: str) -> dict | None:
    url = vr_url(ico)
    name = company.get("name") or ""
    if not (url and name and event["person"]["family_name"] and event["date"]):
        return None
    band = company.get("band") or ""
    size = BAND_LABELS.get(band, "")
    who = event["person"]["name"]
    when = _pretty(event["date"])
    phrase = _phrase(event)

    headline = f"{name}: {who} {phrase} on {when}"

    registered = event["registered"]
    registered_line = (
        f" The court registered the change on {_pretty(registered)}."
        if _ISO_DATE.match(registered or "") else "")

    # The summary is built HERE and `as_classified` returns it unchanged, so it
    # is a literal prefix of `raw_text` and every figure in it is verbatim in
    # the source text by construction rather than by care. `validate._NUMBER`
    # reads a year, a trailing full stop and a following word beginning b, m or
    # k as a magnitude — a defect it names and deliberately leaves alone — so
    # two sentences that differ only in the word AFTER the date are enough to
    # make a sourced figure look invented. It cost the Estonian connector
    # twelve of its first 66 rows before both were built this way.
    summary = (
        f"The Czech public register (veřejný rejstřík) records that {who} "
        f"{phrase} of {name} (IČO {ico}) on {when}, in the body the register "
        f"names {event['body']}. {name} is in the {size} employee band of the "
        f"Czech statistical register. {ATTRIBUTION}"
    )
    body = (
        f"{summary} The date above is the one the register states for the "
        f"office itself, not a difference between two "
        f"readings.{registered_line} The employee band is why this employer is "
        f"read at all: the register covers every Czech company and this "
        f"connector reads only those the state's own statistics put at 250 "
        f"employees or more."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "summary": summary,
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": FEED_URL,
        "published_date": event["date"],
        "company": name,
        "country": "Czechia",
        # The registered office, never the job location, and only when it
        # normalises through the shared gazetteer.
        "hq_town": company.get("town") or "",
        # Personal data stops here. `person_name` is a name and nothing else,
        # because scrub_person returned nothing else.
        "person_name": who,
        # The phrase the headline and the summary both use, derived once so the
        # two can never drift and a figure in one is a figure in the other.
        "phrase": phrase,
        "role": event["role"],
        "body": event["body"],
        "engagement": event["engagement"],
        "event": event["event"],
        "event_date": event["date"],
        "registered_on": event["registered"],
        "ico": ico,
        "size_band": band,
        "size_label": size,
        "band_floor": floor,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def emptiness_floor(material: int) -> int:
    """How few events is too few to be a quiet fortnight.

    Scaled to the material population actually polled rather than typed as one
    number, because the window and the band floor are both inputs: 30 companies
    at the 5,000-employee band genuinely may produce nothing, while 226 of them
    producing nothing over four weeks is the feed, the band field or the date
    fields having moved.
    """
    if material < FLOOR_APPLIES_ABOVE:
        return 0
    return max(FLOOR_MINIMUM, material // FLOOR_PER_COMPANIES)


LAST_RUN: dict = {}


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            min_band: str | None = None, session=None) -> list[dict]:
    """Every office event at a material Czech employer inside the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the register's
    own change feed IS the population.
    """
    window = days if days is not None else days_from_env()
    floor = band_floor(min_band)
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()

    batches = [b for b in fetch_batches(session=session)
               if start <= (b.get("datumUvolneniDavky") or "") <= end]
    print(f"[{COLLECTOR}] window {start}..{end} ({window}d): "
          f"{len(batches)} notification batch(es)")

    changed: set[str] = set()
    notifications = 0
    for batch in batches:
        if session is None:
            time.sleep(REQUEST_DELAY)
        for note in fetch_batch(batch.get("cisloDavky"), session=session):
            notifications += 1
            ico = str(note.get("icoId") or "").strip()
            if _ICO.match(ico):
                changed.add(ico)

    expected = MIN_NOTIFICATIONS_PER_DAY * window
    if notifications < expected:
        raise AresError(
            f"the change feed carried {notifications} notifications over "
            f"{window} days, against a measured rate of about 880 a day "
            f"(24,651 over 2026-07-02..07-29). Below {expected} this is the "
            f"feed having changed shape or stopped, not a quiet fortnight.")

    icos = sorted(changed)
    bands = fetch_bands(icos, session=session)
    material = [i for i in icos if is_material(bands.get(i, {}).get("band"), floor)]
    no_res = sum(1 for i in icos if i not in bands)
    unstated = sum(1 for i in icos
                   if i in bands and not (bands[i].get("band") or "").strip().strip("0"))
    print(f"[{COLLECTOR}] {notifications} notifications, {len(icos)} distinct "
          f"companies, {no_res} with no RES record, {unstated} with no stated "
          f"employee band, {len(material)} at band {floor} or above")

    out: list[dict] = []
    seen: set[tuple] = set()
    stats: dict = {}
    polled = missing = 0
    for ico in material:
        if session is None:
            time.sleep(REQUEST_DELAY)
        record = fetch_vr(ico, session=session)
        polled += 1
        if record is None:
            missing += 1
            continue
        for event in _events(record, start, end, stats):
            row = _row(ico, bands.get(ico, {}), event, floor=floor)
            if row is None:
                continue
            fingerprint = (ico, row["person_name"], row["event"],
                           row["event_date"], row["role"], row["body"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(row)

    backlog = stats.get("backlog", 0)
    print(f"[{COLLECTOR}] {polled} register records read, {missing} not found, "
          f"{backlog} declined as backlog (office date more than "
          f"{MAX_BACKLOG_DAYS} days before the registration), "
          f"{len(out)} office events")

    # What the run READ, so a window in which nothing moved reports as a working
    # collector rather than as a degraded one. run_collect reads this.
    LAST_RUN.clear()
    LAST_RUN.update({"read": polled, "notifications": notifications,
                     "changed": len(icos), "material": len(material),
                     "backlog": backlog, "events": len(out)})

    floor_rows = emptiness_floor(len(material))
    if len(out) < floor_rows:
        raise AresError(
            f"{start}..{end} produced {len(out)} office events from "
            f"{len(material)} employers of 250 staff or more, against a "
            f"measured rate of 108 events from 92 such employers over 14 days. "
            f"That is the band field, the office-date fields or the feed having "
            f"moved, not a quiet fortnight.")
    return out


# --- the derived record ----------------------------------------------------

def _direction(item: dict) -> str:
    """Always `neutral`, and the docstring says why at length.

    Never `hiring`: the register records that an office began, not whether the
    person came from outside the business. Never `displacement`: one director
    leaving is a change of leadership, not a workforce reduction, and workforce
    reductions are the sibling tracker's scope rather than ours.
    """
    return "neutral"


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a field of the register's own JSON or a fixed editorial
    line, so nothing on the record can be something a model believed, and there
    is no LLM cost at all. The figures in the summary — the ICO, the dates, the
    employee band — are all present verbatim in `raw_text` by construction.
    """
    name = item["company"]
    hit = vocab.normalize_city(item.get("hq_town") or "")
    return {
        "company": name,
        "pillar": "leadership_change",
        "signal_direction": _direction(item),
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text`. See the note there for why that is structural.
        "summary": item["summary"],
        "talent_readthrough": (
            "A Czech company must tell the register who holds its offices, and "
            "the register states the date the office itself began or ended "
            "rather than only the date a court wrote it down. So this is a "
            "complete record of board-level change at large Czech employers "
            "rather than a selective one, and it is one of the few sources "
            "anywhere that reports departures on the same footing as arrivals. "
            "Read it as the legal fact and not as a hire: the register never "
            "says whether the person came from inside or outside the business, "
            "and it never says why somebody left. A run of changes at one "
            "employer is worth reading as a board being rebuilt; a single one "
            "is worth reading as housekeeping until something else says "
            "otherwise. The employee band is the statistical register's own "
            "and is not always current."
        ),
        "country": "Czechia",
        # The registered office, kept apart from job location on purpose, and
        # only when the shared gazetteer recognises the town.
        "headquarters_city": hit[0] if hit else "",
        "headquarters_country": "Czechia",
        # A public register maintained by the courts and republished by the
        # Ministry of Finance. infer_confidence caps this at what the host is
        # worth, and ares.gov.cz is in vocab.PRIMARY_SOURCE_DOMAINS, so it
        # lands at 'verified'.
        "confidence": "verified",
    }

"""Denmark's CVR - the one register that states a start date AND an end date.

`source_registry.py`'s 2026-07-31 registry sweep called this "the single
highest-value ask on this page", and the reason is narrow and checkable: of the
fourteen national registers that sweep fetched live, CVR is the only one that
publishes, per participant, BOTH the date an office began and the date it
ended, AND an employee band to draw a materiality line with. Estonia reports
arrivals and can never report a departure. Norway reports that a board changed
and not who. Denmark reports both directions, per person, with a size filter.

    http://distribution.virk.dk/cvr-permanent/virksomhed/_search

IT SHIPS DORMANT, AND THERE ARE TWO LOCKS
=========================================

Exactly the pattern `us_exec_wire` and `spain_borme` ship under.

  * No workflow schedules it. Registration in `run_collect.SOURCES` makes it
    runnable by hand (`--source denmark_cvr`), nothing more.
  * `TIT_DK_CVR` defaults OFF. A disarmed `collect()` returns nothing, makes no
    request and sends no credential, so even a workflow that named it would
    stay dormant until the owner sets the flag.

The dry-run diagnostic is exempt from the arming lock, because rehearsing must
work before the owner decides: `python3 denmark_cvr_probe.py` prints the
credential state, the live liveness of the cluster, the field check against the
public mapping, and the exact query body a run would send. It stores nothing.

THE CREDENTIALS ARE NOT IN THIS REPOSITORY, AND THEY ARE FREE
--------------------------------------------------------------

`DENMARK_DATA_USER` and `DENMARK_DATA_PASSWORD`. They exist today as GitHub
secrets on the SIBLING repository (`dk-forge/ai-layoff-tracker`) and must be
copied here before this can run; nothing in this checkout can read their values.
Erhvervsstyrelsen issues them free on request (cvrselvbetjening@erst.dk) against
a signed agreement covering the handling of advertising-protected
(`reklamebeskyttet`) entities.

**READ THAT AGREEMENT BEFORE ARMING.** This module cannot, so it takes the
conservative side of the one question the agreement governs: a company carrying
`reklamebeskyttet: true` is DECLINED and counted, never stored. If the signed
terms turn out to permit it, that is a deliberate one-line change somebody makes
knowing what they signed, not a default.

THE CREDENTIALS TRAVEL IN PLAINTEXT, AND THAT IS NOT A CHOICE WE MADE
======================================================================

**The service listens on port 80 only.** Measured 2026-09-09 against all three
ELB addresses behind `distribution.virk.dk` (16.170.8.208, 13.62.170.52,
13.63.232.241): port 80 accepts, port 443 times out on every one. So HTTP Basic
authentication over cleartext is the only shape the service offers, which is
what Erhvervsstyrelsen's own guide documents and what every open-source client
of this endpoint does. The owner has accepted it. It is stated here, in
`docs/HANDOVER.md` and in the probe's own output rather than buried, because a
credential in cleartext is a fact an operator has to keep knowing.

Two consequences are enforced in code rather than left to care:

  * **Redirects are refused** (`allow_redirects=False`). A 3xx to another host
    is the one way a Basic credential walks off this host, and a plaintext
    credential is exactly the one you must not let walk.
  * **Every request asserts its own hostname** before it is sent. `_request`
    raises rather than send a credential anywhere but `distribution.virk.dk`.

Both are proved by mutation in `tests/test_denmark_cvr.py`.

WHAT WAS VERIFIED LIVE, AND WHAT WAS NOT
=========================================

This module was written WITHOUT credentials, which the repository's rule about
honest ceilings makes worth stating precisely rather than in general.

VERIFIED, live and unauthenticated, 2026-09-09:

  * `GET /` answers 200, `cluster_name Erst.Distribution.AWS.Prod.Cluster`,
    Elasticsearch 6.8.23.
  * `POST /cvr-permanent/virksomhed/_search` answers **401** from nginx with no
    credential. The service is healthy and the wall is authentication.
  * **`GET /cvr-permanent/_mapping` is PUBLIC.** That is the whole reason this
    collector could be written blind: every field path below was read out of the
    production mapping rather than guessed. The alias resolves to four indices
    (`cvr-v-20220630` is the company one) and every field this module reads -
    `Vrvirksomhed.cvrNummer` (keyword), `.sidstIndlaest` (date),
    `.reklamebeskyttet` (boolean), `.virksomhedMetadata.nyesteNavn.navn`,
    `.virksomhedMetadata.nyesteAarsbeskaeftigelse.intervalKodeAntalAnsatte`
    (keyword), `.virksomhedMetadata.nyesteBeliggenhedsadresse.postdistrikt`,
    `.deltagerRelation[].deltager.enhedstype`, `.deltager.navne[].navn`,
    `.organisationer[].hovedtype`, `.organisationer[].organisationsNavn[].navn`,
    `.organisationer[].medlemsData[].attributter[].type`, `.vaerdier[].vaerdi`
    and `.vaerdier[].periode.gyldigFra` / `.gyldigTil` - exists in it with the
    type stated. `mapping_check()` re-reads it on every run and refuses rather
    than run against a shape that moved.
  * `datacvr.virk.dk/robots.txt` permits `/enhed/virksomhed/`, with
    `Crawl-delay: 10`. That is a fact about `link_check.py`; **nothing here
    fetches that host at all.**

NOT VERIFIED, and named so nobody reads a fixture as a measurement:

  * **No response body from `_search` has ever been seen by this code.** The
    fixture in `tests/fixtures/denmark_cvr_search.json` is assembled from the
    production mapping plus a real captured document published by a third party
    (see its `_provenance`). It proves the parser, the guards and the scrubbing.
    It does not prove that Denmark's live data looks like it.
  * **The band-code vocabulary is second-hand.** `ANTAL_0_0` ... 
    `ANTAL_1000_999999` come from two independent open-source clients of this
    same endpoint, not from a response. The mapping confirms the FIELD is a
    keyword and says nothing about its values. A renamed code would return an
    empty result set rather than an error, which is why the canary below exists.
  * **The mapping type in the path is UNKNOWN.** The mapping reports type
    `_doc`, while the documented and universally used path segment is
    `/virksomhed/`. Under Elasticsearch 6.8 a `_search` against a type that does
    not exist answers **200 with zero hits**, which is the silent zero this
    repository refuses. So the run does not assume: `canary()` asks for one
    known-live CVR number first and requires a hit. If the documented path
    answers zero for it, the typeless path is tried, and if both answer zero the
    run refuses and says so. A scan whose clean zero has never caught one known
    instance is worth nothing.
  * **There is no published rate limit.** None was found in any official or
    third-party source, so it is UNKNOWN rather than generous, and the pacing
    below is deliberately slower than anything measured need would justify.
  * **The yield is UNMEASURED.** See THE EMPTINESS FLOOR IS UNCALIBRATED below.

THE POPULATION
==============

Not the register: CVR holds roughly 1.9 million units, and collecting a national
register unfiltered is the failure this tracker has already refused three times
(the UK's 5.9M companies, Spain, Estonia's 375,305). The population is the
intersection of two things the index can filter on cheaply:

  * **Changed**, by a range query on `Vrvirksomhed.sidstIndlaest`. Two cursor
    fields exist. `sidstOpdateret` is when the BUSINESS fact changed and can be
    backdated by a correction; `sidstIndlaest` is when the record was loaded
    into the index and only moves forward. A poll wants the monotonic one, or a
    backdated correction lands before the cursor and is never seen.
  * **Material**, by a `terms` filter on
    `nyesteAarsbeskaeftigelse.intervalKodeAntalAnsatte`.

Both fields are indexed (`date` and `keyword`), so both are filters and neither
is a pass over the register. `hovedtype` and `enhedstype` are `text` and
therefore analysed, so they are NEVER queried on: a `term` against an analysed
field is a well-known way to match nothing quietly. They are filtered in Python.

THE MATERIALITY FLOOR IS A BAND EDGE, AND ITS LOWER EDGE IS 200, NOT 250
-------------------------------------------------------------------------

`companies_house` and `czechia_ares` both draw the line at 250 employees, which
is the European Commission's own large-enterprise boundary. **Denmark does not
publish a 250 boundary.** The bands run ... `ANTAL_100_199`, `ANTAL_200_499`,
`ANTAL_500_999`, `ANTAL_1000_999999`, so the two candidate floors are:

  * `ANTAL_200_499` - admits every employer of 250 or more, and also employers
    of 200 to 249, which the UK and Czech connectors would exclude.
  * `ANTAL_500_999` - excludes nothing that is small, and excludes every genuine
    large employer between 250 and 499.

The floor is `ANTAL_200_499`, and it is stated on the record rather than tidied
away: a stored row says the employer is in the "200 to 499" band, never that it
has 250 staff. Being one band wider than the sibling connectors is the honest
direction to be wrong in - the alternative silently drops half of Denmark's
large employers to make a number in a docstring match.

THE EMPTINESS FLOOR IS UNCALIBRATED, ON PURPOSE
================================================

`czechia_ares` refuses a run producing fewer than one event per 25 material
employers, and `estonia_ariregister` one per week of window. Both numbers came
from a measurement against real data. **This connector has no measurement, so it
ships with no floor**, and `MEASURED_YIELD` is `None` rather than a plausible
guess. A guard calibrated on an invented number is worse than no guard: it
either never fires or fires on an ordinary week, and either way somebody learns
to ignore it.

A zero run is still not a pass. `run_collect` reads `LAST_RUN['read']` and marks
a collector that read nothing `degraded`, which is this repository's standing
rule, and the shape guards above (the canary, the mapping check, the credential
states) fail loudly on every cause of a zero that is not simply a quiet
fortnight. **THE FIRST ARMED RUN'S JOB IS TO PRODUCE THE NUMBER** - how many
companies changed, how many were material, how many office events came out of
them - and to write it here beside a floor derived from it. Until then this
paragraph is the guard.

WHAT IS NOT COLLECTED, AND WHY EACH IS A DECISION
==================================================

* **Every `hovedtype` except `LEDELSESORGAN`.** Named rather than merely
  omitted, in `EXCLUDED_HOVEDTYPER`. `REGISTER` is the beneficial-owners
  register: ownership, not office, and the most sensitive personal data in the
  file. `REVISION` and `BÆREDYGTIGHEDSREVISION` are auditors - the same
  judgement `bse_india` applies to auditors and `czechia_ares` to the control
  commission. `STIFTERE` is who founded the company, a historical fact rather
  than a current office. `FULDT_ANSVARLIG_DELTAGERE` and
  `SÆRLIGE_FINANSIELLE_DELTAGERE` are partners, which is holding a stake.
  `TEGNINGSBERETTIGEDE` is authority to sign for the company, which follows an
  office rather than being one, and reading it would double every arrival.
* **A legal person on a board.** `deltager.enhedstype` `VIRKSOMHED` is a company
  sitting on another company's board, in exactly the field a human's name uses.
  Same judgement as `companies_house`'s `corporate-*` roles and Estonia's
  `isiku_tyyp` `J`.
* **A function this module does not know.** An unrecognised `FUNKTION` value is
  DECLINED, COUNTED, and printed with its literal value so the vocabulary can be
  widened from evidence. Never accepted as a near-miss.
* **A direction of `hiring` or `displacement`.** The register records that an
  office began or ended and never why. Every row is `neutral`, the rule
  `companies_house`, `czechia_ares` and `estonia_ariregister` all apply.
* **`organisationsNavn`'s period as a person's dates.** In the one real document
  available, the organisation period ends 2015-11-03 and the same membership's
  `medlemsData` period ends 2015-03-31. They are different facts. Dates come
  from `medlemsData` and the organ name is used only to say WHICH body.
* **A job location.** `nyesteBeliggenhedsadresse` is a registered office, so it
  goes to `headquarters_city` through the shared gazetteer and only when it
  normalises. Nothing here splits an address.

PERSONAL DATA: TAKEN AT THE BOUNDARY, NEVER PERSISTED
======================================================

The participant object carries a residential address (`beliggenhedsadresse`,
`postadresse`), an address-protection flag (`adresseHemmelig`), a persistent
national participant id (`enhedsNummer`) and a `forretningsnoegle`.
**`scrub_person` is the only way a person reaches a row from here**, and it
returns a name and nothing else. Everything else is dropped inside this module
before a dict exists, so no later stage can leak what it never received.
Asserted by `tests/test_denmark_cvr.py`, which keeps every one of those fields
in its fixture precisely so the tests can require that none of them arrives.

CITATION
========

`source_url` is `datacvr.virk.dk/enhed/virksomhed/{cvr}`, the register's own
public page for that company. Two things about it are stated rather than
assumed:

  * **It could not be verified as distinguishable.** Estonia's connector proved
    a real code answers 200 and an invented one 303. Here both a real CVR number
    (24256790) and an invented one (99999999) answered **403** to an automated
    client on 2026-09-09: datacvr.virk.dk refuses non-browser traffic. So the
    URL is guarded a better way instead, offline and free - **`valid_cvr`
    applies the register's own modulus-11 checksum**, which an invented number
    fails. 24256790, 22756214, 61126228 and 10403782 pass it; 99999999 does not.
  * `link_check.py` reads a 403 as WALLED rather than as rot, which is the
    correct reading of a host that fronts a bot wall.

One company has one such URL and appoints many people, so
`REVISITS_ITS_SOURCE_URL` is set and dedup happens on `content_hash`.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from pipeline import vocab

HOST = "distribution.virk.dk"
BASE = f"http://{HOST}"

# The documented path first, the typeless one second. See THE MAPPING TYPE IN
# THE PATH IS UNKNOWN in the module docstring: the mapping reports type `_doc`
# while every client and every guide uses `/virksomhed/`, and under 6.8 the
# wrong one answers 200 with zero hits rather than an error.
SEARCH_PATHS = ("/cvr-permanent/virksomhed/_search", "/cvr-permanent/_search")
SCROLL_URL = BASE + "/_search/scroll"
MAPPING_URL = BASE + "/cvr-permanent/_mapping"
REGISTER = "https://datacvr.virk.dk/enhed/virksomhed/{cvr}"

COLLECTOR = "denmark_cvr"
SOURCE_NAME = "CVR, the Danish Central Business Register (Erhvervsstyrelsen)"

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

ATTRIBUTION = ("Source: CVR, Det Centrale Virksomhedsregister, published by "
               "Erhvervsstyrelsen (the Danish Business Authority).")

USER_ENV = "DENMARK_DATA_USER"
PASSWORD_ENV = "DENMARK_DATA_PASSWORD"
ARM_ENV = "TIT_DK_CVR"

# The four credential states, and they are FOUR rather than two for the reason
# the digest mailer learned the hard way: absence of a signal is not a pass.
CRED_ABSENT = "ABSENT"        # nothing configured. Dormant, and green.
CRED_PRESENT = "PRESENT"      # configured, not yet exercised this run.
CRED_REJECTED = "REJECTED"    # the service answered 401. A human rotates a key.
CRED_UNKNOWN = "UNKNOWN"      # unreachable or 5xx. Never a pass, never a fault.

# No published rate limit exists for this service, so this is set from caution
# rather than from a documented ceiling: one request a second against a
# government host, and the whole run is a handful of scroll pages.
REQUEST_DELAY = 1.0
RETRY_WAIT = 20
RETRIES = 3
TIMEOUT = 120

# Elasticsearch 6.8 caps `from + size` at 3,000 on this cluster, so bulk reads
# go through the scroll API. 100 a page keeps a page small enough to parse
# without holding a national register in memory.
PAGE_SIZE = 100
SCROLL_TTL = "2m"
# A ceiling on one run rather than a limit on the data: 200 pages is 20,000
# companies, far more than a fortnight of material change can plausibly be, and
# a run that hits it says so rather than truncating quietly.
MAX_PAGES = 200

DEFAULT_DAYS = 14

# The employee bands, as two independent open-source clients of this endpoint
# spell them. NOT verified against a live response - see the docstring. The top
# band is `ANTAL_1000_999999` and not `ANTAL_1000_9999`, which is the single
# most likely thing to be typed wrong.
BAND_LABELS = {
    "ANTAL_0_0": "0",
    "ANTAL_1_1": "1",
    "ANTAL_2_4": "2 to 4",
    "ANTAL_5_9": "5 to 9",
    "ANTAL_10_19": "10 to 19",
    "ANTAL_20_49": "20 to 49",
    "ANTAL_50_99": "50 to 99",
    "ANTAL_100_199": "100 to 199",
    "ANTAL_200_499": "200 to 499",
    "ANTAL_500_999": "500 to 999",
    "ANTAL_1000_999999": "1,000 or more",
}

# The floor is a BAND EDGE and its lower edge is 200, not 250. See THE
# MATERIALITY FLOOR IS A BAND EDGE in the module docstring for the two
# candidates and why the wider one is the honest direction to be wrong in.
MATERIAL_BANDS = ("ANTAL_200_499", "ANTAL_500_999", "ANTAL_1000_999999")

# A known-live employer, used ONLY as a canary: a search that cannot find this
# is a search that is looking in the wrong place, not a quiet fortnight. Novo
# Nordisk A/S, CVR 24256790, which passes the modulus-11 check below.
CANARY_CVR = "24256790"

# The only organ whose members hold an office. Everything else is named in
# EXCLUDED_HOVEDTYPER with the reason it is not a talent signal.
LEADERSHIP_HOVEDTYPE = "LEDELSESORGAN"

EXCLUDED_HOVEDTYPER = (
    # ownership and the beneficial-owners register
    "REGISTER", "FULDT_ANSVARLIG_DELTAGERE", "SÆRLIGE_FINANSIELLE_DELTAGERE",
    "HOVEDSELSKAB",
    # auditors
    "REVISION", "BÆREDYGTIGHEDSREVISION",
    # historical and administrative
    "STIFTERE", "TEGNINGSBERETTIGEDE", "REPRÆSENTANTER", "FORSIKRINGER",
    "HVIDVASK", "FUSION", "SPALTNING",
)

# The attribute that names what a member of an organ actually is.
FUNCTION_ATTRIBUTE = "FUNKTION"

# The function values that are a person holding an office, keyed on the
# register's own words folded to lower case. The case is NOT consistent in this
# data - the same real document spells one organisation attribute `Direktion`
# and the matching member attribute `DIREKTION` - so nothing here compares a
# literal. A value not on this list is declined and COUNTED, never accepted as
# a near-miss.
FUNCTIONS = {
    "adm. dir.": "the chief executive (administrerende direktør)",
    "adm. direktør": "the chief executive (administrerende direktør)",
    "administrerende direktør": "the chief executive (administrerende direktør)",
    "direktion": "a member of the executive board (direktionen)",
    "direktør": "a director (direktør)",
    "bestyrelse": "a member of the board (bestyrelsen)",
    "bestyrelsesmedlem": "a member of the board (bestyrelsen)",
    "formand": "chair of the board (formand)",
    "næstformand": "deputy chair of the board (næstformand)",
    "tilsynsråd": "a member of the supervisory board (tilsynsrådet)",
    "tilsynsrådsmedlem": "a member of the supervisory board (tilsynsrådet)",
}

# Named rather than merely omitted, and these arrive under LEDELSESORGAN so the
# hovedtype filter does not catch them. A court appointing somebody to wind a
# company up is not a hire, and the workforce consequence of it is the sibling
# tracker's scope.
EXCLUDED_FUNCTIONS = ("likvidator", "kurator", "midlertidig ledelse",
                      "rekonstruktør", "granskningsmand")

# A natural person. `VIRKSOMHED` is a company on another company's board.
PERSON_TYPE = "PERSON"

EVENT_TOOK_OFFICE = "took_office"
EVENT_LEFT_OFFICE = "left_office"

REVISITS_ITS_SOURCE_URL = True

# NOT A NUMBER, and that is the point. See THE EMPTINESS FLOOR IS UNCALIBRATED
# in the module docstring: this connector has never seen a real response, so it
# ships with no yield guard rather than with an invented one. The first armed
# run's job is to replace this with a measurement and derive a floor from it.
MEASURED_YIELD = None

_CVR = re.compile(r"^\d{8}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The register's own modulus-11 weights, most significant digit first.
_MOD11_WEIGHTS = (2, 7, 6, 5, 4, 3, 2, 1)

# Every field path this module reads, as `Vrvirksomhed`-relative dotted paths.
# `mapping_check` requires each to exist in the PUBLIC mapping, which is why a
# renamed field is a loud refusal here rather than an empty result set.
REQUIRED_FIELDS = (
    "cvrNummer",
    "sidstIndlaest",
    "reklamebeskyttet",
    "virksomhedMetadata.nyesteNavn.navn",
    "virksomhedMetadata.nyesteAarsbeskaeftigelse.intervalKodeAntalAnsatte",
    "virksomhedMetadata.nyesteBeliggenhedsadresse.postdistrikt",
    "deltagerRelation.deltager.enhedstype",
    "deltagerRelation.deltager.navne.navn",
    "deltagerRelation.organisationer.hovedtype",
    "deltagerRelation.organisationer.organisationsNavn.navn",
    "deltagerRelation.organisationer.medlemsData.attributter.type",
    "deltagerRelation.organisationer.medlemsData.attributter.vaerdier.vaerdi",
    "deltagerRelation.organisationer.medlemsData.attributter.vaerdier"
    ".periode.gyldigFra",
    "deltagerRelation.organisationer.medlemsData.attributter.vaerdier"
    ".periode.gyldigTil",
)


class CvrError(RuntimeError):
    """A run could not be read, or came back in a shape it cannot trust."""


class CvrCredentialsMissing(CvrError):
    """No credential is configured. A distinct type because it is a distinct
    state: dormant and green, not broken and not a zero."""


class CvrCredentialsRejected(CvrError):
    """The service answered 401 to a credential we sent. A human rotates a
    secret; nothing here can retry its way out of it."""


# --- configuration ---------------------------------------------------------

def armed() -> bool:
    """Whether a LIVE run may proceed. Defaults OFF.

    A disarmed run makes no request and sends no credential. The probe ignores
    this, because rehearsing coverage must work before the owner decides.
    """
    return (os.environ.get(ARM_ENV) or "off").strip().lower() in (
        "1", "on", "true", "yes", "arm", "armed")


def days_from_env(default_days: int | None = None) -> int:
    raw = (os.environ.get("TIT_DK_DAYS") or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw) or int(raw) < 1:
        raise CvrError(
            f"TIT_DK_DAYS holds {raw!r}, which is not a number of days")
    return int(raw)


def credential_state() -> str:
    """ABSENT or PRESENT, read from the environment and nothing more.

    REJECTED and UNKNOWN are states only a request can discover, so they are
    returned by the caller that made one. Four states, never two: a missing key
    must stay a state of its own, or a dormant collector and a broken credential
    report the same thing.
    """
    user = (os.environ.get(USER_ENV) or "").strip()
    secret = (os.environ.get(PASSWORD_ENV) or "").strip()
    return CRED_PRESENT if (user and secret) else CRED_ABSENT


def credentials() -> tuple[str, str]:
    user = (os.environ.get(USER_ENV) or "").strip()
    secret = (os.environ.get(PASSWORD_ENV) or "").strip()
    if not (user and secret):
        raise CvrCredentialsMissing(
            f"{USER_ENV} and {PASSWORD_ENV} are not both set. The CVR "
            f"distribution service answers 401 to every search path without "
            f"them. They exist as GitHub secrets on the sibling repository "
            f"dk-forge/ai-layoff-tracker and must be copied to this one; "
            f"Erhvervsstyrelsen issues them free on request. This is a "
            f"DORMANT collector reporting that it is dormant, not a failure.")
    return user, secret


# --- the modulus-11 check --------------------------------------------------

def valid_cvr(number) -> bool:
    """Whether an eight-digit string is a well-formed CVR number.

    The register's own modulus-11 checksum. This is what stands in for the
    real-versus-invented URL check Estonia's connector could run live and this
    one could not: datacvr.virk.dk answers 403 to an automated client for a real
    company and for an invented one alike, so distinguishability was proved
    offline instead. 24256790, 22756214, 61126228 and 10403782 pass; 99999999
    does not.
    """
    text = str(number or "").strip()
    if not _CVR.match(text):
        return False
    total = sum(int(d) * w for d, w in zip(text, _MOD11_WEIGHTS))
    return total % 11 == 0


def register_url(number) -> str | None:
    text = str(number or "").strip()
    return REGISTER.format(cvr=text) if valid_cvr(text) else None


# --- HTTP ------------------------------------------------------------------

def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/json"}


def assert_same_host(url: str) -> None:
    """Refuse to send a credential anywhere but the CVR distribution host.

    The credential travels in CLEARTEXT because the service offers port 80 and
    nothing else, so the ordinary consequences of a redirect are not ordinary
    here. Redirects are refused at the request too; this is the check that
    cannot be turned off by a library setting.
    """
    host = (urlparse(url).hostname or "").lower()
    if host != HOST:
        raise CvrError(
            f"refusing to send CVR credentials to {host or '(no host)'}: this "
            f"module talks to {HOST} and to nothing else, and the credential "
            f"is sent in cleartext.")


def _request(method: str, url: str, *, body=None, session=None,
             auth=None, timeout: int = TIMEOUT):
    """One call, retried on a transient 5xx and never on a 401 or a 404.

    `allow_redirects=False` is load-bearing rather than tidy: a 3xx to another
    host is how a plaintext Basic credential leaves the host it was issued for.
    """
    assert_same_host(url)
    call = getattr(session or requests, method)
    resp = None
    for _attempt in range(RETRIES):
        kwargs = {"headers": _headers(), "timeout": timeout,
                  "allow_redirects": False}
        if auth is not None:
            kwargs["auth"] = auth
        if body is not None:
            kwargs["data"] = json.dumps(body)
        resp = call(url, **kwargs)
        if resp.status_code < 500:
            break
        if session is None:
            time.sleep(RETRY_WAIT)
    return resp


def _json(resp, what: str) -> dict:
    """The response body, with 401 and 3xx raised as themselves."""
    if resp.status_code == 401:
        raise CvrCredentialsRejected(
            f"{what} answered 401. Either {USER_ENV}/{PASSWORD_ENV} are wrong, "
            f"or the agreement behind them has lapsed. Nothing here can retry "
            f"its way out of that; a human rotates the secret.")
    if 300 <= resp.status_code < 400:
        raise CvrError(
            f"{what} answered a {resp.status_code} redirect, which this module "
            f"does not follow: the credential is sent in cleartext and a "
            f"redirect is how it would leave {HOST}.")
    if resp.status_code != 200:
        raise CvrError(f"{what} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise CvrError(
            f"{what} did not return JSON: {resp.text[:160]!r}") from exc


# --- the public mapping check ----------------------------------------------

def _mapping_paths(mapping: dict) -> set:
    """Every `Vrvirksomhed`-relative dotted field path in the mapping."""
    out: set = set()

    def walk(props: dict, prefix: str) -> None:
        for name, node in (props or {}).items():
            path = f"{prefix}.{name}" if prefix else name
            out.add(path)
            walk((node or {}).get("properties") or {}, path)

    for index in (mapping or {}).values():
        for kind in ((index or {}).get("mappings") or {}).values():
            company = ((kind or {}).get("properties") or {}).get("Vrvirksomhed")
            if company:
                walk(company.get("properties") or {}, "")
    return out


def mapping_check(*, session=None, mapping: dict | None = None) -> set:
    """Refuse to run against a shape that moved.

    `/cvr-permanent/_mapping` is PUBLIC - no credential, and verified live on
    2026-09-09 - which is what lets a collector written without credentials have
    a real guard at all. A renamed field would otherwise produce an empty result
    set, which is the silent zero this repository refuses.

    Returns the field paths found, so a caller can report them.
    """
    if mapping is None:
        mapping = _json(_request("get", MAPPING_URL, session=session),
                        "the public CVR mapping")
    found = _mapping_paths(mapping)
    if not found:
        raise CvrError(
            "the public mapping carries no Vrvirksomhed properties at all. "
            "That is the index or the alias having moved, not a field rename.")
    missing = [f for f in REQUIRED_FIELDS if f not in found]
    if missing:
        raise CvrError(
            f"the CVR mapping no longer carries {len(missing)} field(s) this "
            f"collector reads: {', '.join(missing)}. Every one of them was "
            f"present on 2026-09-09. Read the live mapping at {MAPPING_URL} "
            f"(it needs no credential) before changing anything here.")
    return found


# --- the query -------------------------------------------------------------

def search_body(start: str, end: str, *, bands=MATERIAL_BANDS,
                size: int = PAGE_SIZE) -> dict:
    """The one query a run sends, built here so the probe can print it.

    Only indexed, unanalysed fields are filtered on: a `date` range on
    `sidstIndlaest` and a `terms` filter on the `keyword` band code.
    `hovedtype` and `enhedstype` are `text` and are therefore filtered in
    Python, because a `term` query against an analysed field matches nothing and
    says nothing about why.
    """
    return {
        "size": size,
        # Scroll wants a stable, cheap order and `_doc` is that order. Without
        # it the scroll is ordered by a relevance score no filter-only query
        # produces, which is slower and says nothing.
        "sort": ["_doc"],
        "_source": ["Vrvirksomhed.cvrNummer",
                    "Vrvirksomhed.reklamebeskyttet",
                    "Vrvirksomhed.virksomhedMetadata",
                    "Vrvirksomhed.deltagerRelation"],
        "query": {"bool": {"filter": [
            {"range": {"Vrvirksomhed.sidstIndlaest": {
                "gte": start, "lte": f"{end}T23:59:59", "time_zone": "+01:00"}}},
            {"terms": {
                "Vrvirksomhed.virksomhedMetadata.nyesteAarsbeskaeftigelse"
                ".intervalKodeAntalAnsatte": list(bands)}},
        ]}},
    }


def canary_body(cvr: str = CANARY_CVR) -> dict:
    """A search for one known-live company, counting only.

    Under Elasticsearch 6.8 a search against a mapping type that does not exist
    answers 200 with zero hits, and the mapping says this index's type is `_doc`
    while every client and guide uses `/virksomhed/`. So the run asks a question
    it knows the answer to before it trusts a zero to anything else.
    """
    return {"size": 0,
            "query": {"term": {"Vrvirksomhed.cvrNummer": str(cvr)}}}


def _total(payload: dict) -> int:
    """`hits.total`, which is an int on 6.x and an object on 7.x. Read both,
    because the cluster is 6.8.23 today and nobody will remember this file on
    the day it is upgraded."""
    hits = (payload or {}).get("hits") or {}
    total = hits.get("total")
    if isinstance(total, dict):
        return int(total.get("value") or 0)
    return int(total or 0)


def resolve_search_url(*, session=None, auth=None) -> str:
    """Which of the two search paths actually answers, proved by the canary."""
    tried = []
    for path in SEARCH_PATHS:
        url = BASE + path
        payload = _json(_request("post", url, body=canary_body(),
                                 session=session, auth=auth),
                        f"the canary search at {path}")
        found = _total(payload)
        tried.append(f"{path} -> {found}")
        if found > 0:
            return url
        if session is None:
            time.sleep(REQUEST_DELAY)
    raise CvrError(
        f"no search path can find CVR {CANARY_CVR}, a company that certainly "
        f"exists ({'; '.join(tried)}). Elasticsearch answers a search against a "
        f"missing mapping type with 200 and zero hits, so this would otherwise "
        f"have been a quiet run rather than an error. Read the live mapping at "
        f"{MAPPING_URL}, which needs no credential.")


# --- reading one company ---------------------------------------------------

def scrub_person(deltager: dict) -> dict | None:
    """A name, and nothing else. THE ONLY WAY A PERSON REACHES A ROW FROM HERE.

    The participant object carries a residential address, an address-protection
    flag, a persistent national participant id and a business key. None of them
    is read. Names are returned exactly as the register publishes them, never
    re-cased and never transliterated.
    """
    if not isinstance(deltager, dict):
        return None
    if (deltager.get("enhedstype") or "").strip().upper() != PERSON_TYPE:
        return None
    names = [n for n in (deltager.get("navne") or []) if isinstance(n, dict)]
    if not names:
        return None
    # The current name is the one whose period is open; failing that, the last
    # one published. Never a name chosen by sort order.
    current = [n for n in names
               if not ((n.get("periode") or {}).get("gyldigTil") or "").strip()]
    chosen = (current or names)[-1]
    name = re.sub(r"\s+", " ", str(chosen.get("navn") or "")).strip()
    return {"name": name} if name else None


def company_name(record: dict) -> str:
    meta = record.get("virksomhedMetadata") or {}
    return re.sub(r"\s+", " ",
                  str(((meta.get("nyesteNavn") or {}).get("navn")) or "")).strip()


def company_band(record: dict) -> str:
    meta = record.get("virksomhedMetadata") or {}
    staffing = meta.get("nyesteAarsbeskaeftigelse") or {}
    return (staffing.get("intervalKodeAntalAnsatte") or "").strip()


def company_town(record: dict) -> str:
    meta = record.get("virksomhedMetadata") or {}
    address = meta.get("nyesteBeliggenhedsadresse") or {}
    return re.sub(r"\s+", " ",
                  str(address.get("postdistrikt") or "")).strip()


def _function(vaerdi) -> tuple[str, str] | None:
    """`(the register's own word, the English gloss)`, or None if unknown.

    Case-insensitive because the case is NOT consistent in this data: the same
    real document spells one attribute `Direktion` and the matching member
    attribute `DIREKTION`. A `vaerdi` may also be null.
    """
    text = re.sub(r"\s+", " ", str(vaerdi or "")).strip()
    if not text:
        return None
    gloss = FUNCTIONS.get(text.lower())
    return (text, gloss) if gloss else None


def _in(when, start: str, end: str) -> bool:
    text = (when or "").strip()[:10]
    return bool(_ISO_DATE.match(text)) and start <= text <= end


def events(record: dict, start: str, end: str,
           stats: dict | None = None) -> list[dict]:
    """The office events on one company record inside the window.

    Both directions, both source-stated, neither inferred: `periode.gyldigFra`
    is the date the office began and `periode.gyldigTil` the date it ended, on
    the member's own `FUNKTION` attribute. Never a diff of two snapshots, and
    never the ORGANISATION's period - in the one real document available, the
    organisation period and the membership period end eight months apart and
    they are different facts.
    """
    out: list[dict] = []
    seen: set = set()
    for relation in record.get("deltagerRelation") or []:
        if not isinstance(relation, dict):
            continue
        person = scrub_person(relation.get("deltager") or {})
        if person is None:
            if stats is not None:
                stats["not_a_person"] = stats.get("not_a_person", 0) + 1
            continue
        for organ in relation.get("organisationer") or []:
            if not isinstance(organ, dict):
                continue
            hovedtype = (organ.get("hovedtype") or "").strip().upper()
            if hovedtype != LEADERSHIP_HOVEDTYPE:
                if stats is not None:
                    stats["other_organ"] = stats.get("other_organ", 0) + 1
                continue
            body = ""
            for named in organ.get("organisationsNavn") or []:
                if isinstance(named, dict):
                    body = re.sub(r"\s+", " ",
                                  str(named.get("navn") or "")).strip() or body
            for member in organ.get("medlemsData") or []:
                if not isinstance(member, dict):
                    continue
                for attribute in member.get("attributter") or []:
                    if not isinstance(attribute, dict):
                        continue
                    if (attribute.get("type") or "").strip().upper() \
                            != FUNCTION_ATTRIBUTE:
                        continue
                    for value in attribute.get("vaerdier") or []:
                        if not isinstance(value, dict):
                            continue
                        raw_value = value.get("vaerdi")
                        known = _function(raw_value)
                        if known is None:
                            if stats is not None:
                                stats["unknown_function"] = (
                                    stats.get("unknown_function", 0) + 1)
                                unseen = stats.setdefault("unknown_values", set())
                                text = re.sub(r"\s+", " ",
                                              str(raw_value or "")).strip()
                                if text:
                                    unseen.add(text)
                            continue
                        role, gloss = known
                        period = value.get("periode") or {}
                        began = (period.get("gyldigFra") or "")[:10]
                        ended = (period.get("gyldigTil") or "")[:10]
                        for kind, when in ((EVENT_TOOK_OFFICE, began),
                                           (EVENT_LEFT_OFFICE, ended)):
                            if not _in(when, start, end):
                                continue
                            identity = (person["name"], role, kind, when)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            out.append({"person": person, "role": role,
                                        "gloss": gloss, "body": body,
                                        "event": kind, "date": when})
    return out


# --- one row ---------------------------------------------------------------

def _pretty(iso: str) -> str:
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def _phrase(event: dict) -> str:
    if event["event"] == EVENT_TOOK_OFFICE:
        return f"took office as {event['gloss']}"
    return f"left office as {event['gloss']}"


def _row(record: dict, event: dict, *, floor: str) -> dict | None:
    cvr = str((record.get("cvrNummer") or "")).strip()
    url = register_url(cvr)
    name = company_name(record)
    band = company_band(record)
    if not (url and name and event["person"]["name"] and event["date"]):
        return None

    size = BAND_LABELS.get(band, "")
    if not size:
        return None
    who = event["person"]["name"]
    when = _pretty(event["date"])
    phrase = _phrase(event)
    headline = f"{name}: {who} {phrase} on {when}"
    organ = f", in the body the register names {event['body']}" if event["body"] else ""

    # The summary is built HERE and `as_classified` returns it unchanged, so it
    # is a literal prefix of `raw_text` and every figure in it is verbatim in
    # the source text by construction rather than by care. `validate._NUMBER`
    # reads a year, a trailing full stop and a following word beginning b, m or
    # k as a magnitude - a defect it names and deliberately leaves alone - so
    # two sentences differing only in the word AFTER a date are enough to make a
    # sourced figure look invented. It cost the Estonian connector twelve of its
    # first 66 rows before both were built this way.
    summary = (
        f"CVR, the Danish Central Business Register, records that {who} "
        f"{phrase} of {name} (CVR number {cvr}) on {when}{organ}. The register "
        f"places {name} in the {size} employee band of its most recent annual "
        f"employment figures. {ATTRIBUTION}"
    )
    body = (
        f"{summary} Both dates on a Danish participant record are stated by the "
        f"register itself, so a departure is read here rather than inferred "
        f"from the absence of a row. The employee band is why this employer is "
        f"read at all: the register covers every Danish company and this "
        f"connector reads only those in the {BAND_LABELS[floor]} band or above. "
        f"That band's lower edge is 200 rather than 250, because the register "
        f"publishes no boundary at 250."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "summary": summary,
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": BASE + SEARCH_PATHS[0],
        "published_date": event["date"],
        "company": name,
        "country": "Denmark",
        # The registered office, never a job location, and only if the shared
        # gazetteer recognises it.
        "hq_town": company_town(record),
        # Personal data stops here. `person_name` is a name and nothing else,
        # because scrub_person returned nothing else.
        "person_name": who,
        "phrase": phrase,
        "role": event["role"],
        "role_en": event["gloss"],
        "body": event["body"],
        "event": event["event"],
        "event_date": event["date"],
        "cvr": cvr,
        "size_band": band,
        "size_label": size,
        "band_floor": floor,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def emptiness_floor(material: int) -> int:
    """Zero, and the docstring is the guard.

    `czechia_ares` and `estonia_ariregister` both refuse a suspiciously empty
    run against a floor derived from a MEASUREMENT. This connector has no
    measurement - see THE EMPTINESS FLOOR IS UNCALIBRATED - so it ships with no
    floor rather than with a plausible-looking guess. A floor calibrated on an
    invented number either never fires or fires on an ordinary week, and either
    way somebody learns to ignore it.

    Do not answer a quiet run by inventing one here. Measure the first armed
    run, write the numbers into the docstring, and derive it.
    """
    return 0


def rows_from_hits(hits, start: str, end: str, *, floor: str,
                   stats: dict | None = None) -> list[dict]:
    """Every storable row in one page of hits. No network, so a test can drive
    the whole derivation from a recorded payload."""
    out: list[dict] = []
    for hit in hits or []:
        record = ((hit or {}).get("_source") or {}).get("Vrvirksomhed") or {}
        if record.get("reklamebeskyttet") is True:
            # See READ THAT AGREEMENT BEFORE ARMING. The conservative side of
            # the one question the signed terms govern.
            if stats is not None:
                stats["reklamebeskyttet"] = stats.get("reklamebeskyttet", 0) + 1
            continue
        if company_band(record) not in MATERIAL_BANDS:
            if stats is not None:
                stats["below_floor"] = stats.get("below_floor", 0) + 1
            continue
        for event in events(record, start, end, stats):
            row = _row(record, event, floor=floor)
            if row is not None:
                out.append(row)
    return out


LAST_RUN: dict = {}


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            session=None) -> list[dict]:
    """Every office event at a material Danish employer inside the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the register's
    own change cursor IS the population.

    DORMANT by default. Two locks, and this is the second one: with `TIT_DK_CVR`
    unset the run makes no request, sends no credential and returns nothing.
    """
    window = days if days is not None else days_from_env()
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()
    floor = MATERIAL_BANDS[0]

    if not armed():
        print(f"[{COLLECTOR}] DORMANT ({ARM_ENV} is off) - no request made, no "
              f"credential sent, nothing stored. Credentials are "
              f"{credential_state()}. Run `python3 denmark_cvr_probe.py` to "
              f"rehearse what an armed run would do.")
        LAST_RUN.clear()
        LAST_RUN.update({"read": 0, "armed": False,
                         "credentials": credential_state()})
        return []

    auth = credentials()          # raises CvrCredentialsMissing, loudly
    mapping_check(session=session)
    search_url = resolve_search_url(session=session, auth=auth)
    print(f"[{COLLECTOR}] window {start}..{end} ({window}d), searching "
          f"{search_url}")

    out: list[dict] = []
    stats: dict = {}
    companies = pages = 0
    scroll_id = None
    payload = _json(_request("post", f"{search_url}?scroll={SCROLL_TTL}",
                             body=search_body(start, end), session=session,
                             auth=auth), "the CVR search")
    while True:
        pages += 1
        hits = ((payload.get("hits") or {}).get("hits")) or []
        if not hits:
            break
        companies += len(hits)
        out.extend(rows_from_hits(hits, start, end, floor=floor, stats=stats))
        scroll_id = payload.get("_scroll_id")
        if not scroll_id or pages >= MAX_PAGES:
            break
        if session is None:
            time.sleep(REQUEST_DELAY)
        payload = _json(_request("post", SCROLL_URL,
                                 body={"scroll": SCROLL_TTL,
                                       "scroll_id": scroll_id},
                                 session=session, auth=auth),
                        "the CVR scroll")

    if pages >= MAX_PAGES:
        raise CvrError(
            f"the run reached its {MAX_PAGES}-page ceiling ({companies} "
            f"companies) and would have truncated. Narrow the window with "
            f"TIT_DK_DAYS rather than raising the ceiling, or the run stops "
            f"fitting inside the writer lock.")

    unknown = sorted(stats.get("unknown_values") or ())
    print(f"[{COLLECTOR}] {companies} material companies changed in the window "
          f"across {pages} page(s), {stats.get('below_floor', 0)} dropped below "
          f"the band floor, {stats.get('reklamebeskyttet', 0)} advertising "
          f"protected, {stats.get('not_a_person', 0)} participants that are not "
          f"natural persons, {stats.get('other_organ', 0)} organs that are not "
          f"{LEADERSHIP_HOVEDTYPE}, {stats.get('unknown_function', 0)} "
          f"declined for an unknown function, {len(out)} office events")
    if unknown:
        print(f"[{COLLECTOR}] functions this collector does not know, verbatim: "
              + ", ".join(repr(v) for v in unknown[:20]))

    LAST_RUN.clear()
    LAST_RUN.update({"read": companies, "pages": pages,
                     "events": len(out), "armed": True,
                     "credentials": CRED_PRESENT,
                     "unknown_functions": stats.get("unknown_function", 0)})
    return out


# --- the derived record ----------------------------------------------------

def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a field of the register's own JSON or a fixed editorial
    line, so nothing on the record can be something a model believed, and there
    is no LLM cost at all.
    """
    name = item["company"]
    hit = vocab.normalize_city(item.get("hq_town") or "")
    return {
        "company": name,
        "pillar": "leadership_change",
        # Never `hiring`: the register records that an office began, not where
        # the person came from. Never `displacement`: one director leaving is a
        # change of leadership, and workforce reductions are the sibling
        # tracker's scope.
        "signal_direction": "neutral",
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text`. See the note there for why that is structural.
        "summary": item["summary"],
        "talent_readthrough": (
            "A Danish company must tell CVR who sits in its management organs, "
            "and the register states the date each office began AND the date it "
            "ended. So this is one of the very few sources anywhere that "
            "reports departures on the same footing as arrivals, and a "
            "complete record of them at large Danish employers rather than a "
            "selective one. Read it as the legal fact and not as a hire: the "
            "register never says whether somebody came from inside or outside "
            "the business, and it never says why anybody left. A run of changes "
            "at one employer is worth reading as a management team being "
            "rebuilt; a single one is housekeeping until something else says "
            "otherwise. The employee band is the register's own most recent "
            "annual figure and is not always current."
        ),
        "country": "Denmark",
        "headquarters_city": hit[0] if hit else "",
        "headquarters_country": "Denmark",
        # The statutory register itself, published by the authority that
        # maintains it. infer_confidence caps this at what the host is worth,
        # and datacvr.virk.dk is in vocab.PRIMARY_SOURCE_DOMAINS.
        "confidence": "verified",
    }

"""New Zealand's INCORPORATION signal, read from MBIE's NZBN register.

SHIPS DORMANT behind two locks, exactly as denmark_cvr does: no workflow
schedules it, and `TIT_NZ_NZBN` defaults off, so a run makes no request and
sends no credential until the owner arms it. The key is the GitHub secret
`NZBN_API_KEY_NZ` (approved by MBIE 2026-11-11), read from the environment only.

THE ROUTES BELOW HAVE NOT BEEN EXERCISED AGAINST THE LIVE API. They are written
from MBIE's NZBN API v5 documentation and the fixtures in the tests are
CONSTRUCTED in that shape, not captured. Arming order is therefore: run the
probe with the key, read the response shapes, correct this file, only then arm.
`ROUTES_VERIFIED_LIVE` is False until somebody does that, and `collect()`
refuses to run armed while it is.

The terms (docs/SOURCE-nz-nzbn.md, MBIE case 00094961, 2026-11-12) shape four
things in this file and each is a test:

  * ATTRIBUTION. Every row states the source and the date and time the data was
    retrieved, inside `summary` so it travels with the row to every surface.
  * SOLE TRADERS. Their name and every field but the NZBN can be made private at
    any time, so the entity types allowed through are an ALLOWLIST of company
    forms, and anything unrecognised is dropped. The default is to exclude.
  * REFRESH. `public_view` rebuilds a record from the CURRENT response only, so
    a field that has become private is dropped rather than carried forward.
  * LIMITED CALLS. The population comes from Change Event Search over a date
    range, and a per-entity read is a conditional GET (`If-Match` with the
    stored ETag, 304 when unchanged). Nothing walks the whole register.

No model is called on any path: every value is a field of the register or a
fixed editorial line. `REVISITS_ITS_SOURCE_URL` is True for the reason it is on
the other registry collectors: dedup is on content, not on the page.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

COLLECTOR = "nz_nzbn"
SOURCE_NAME = "NZBN register (Ministry of Business, Innovation and Employment)"
API = "https://api.business.govt.nz/gateway/nzbn/v5"
ENTITY_PAGE = "https://www.nzbn.govt.nz/mynzbn/nzbndetails/{nzbn}/"
DISCOVERY_PAGE = "https://www.nzbn.govt.nz/"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"

KEY_ENV = "NZBN_API_KEY_NZ"
ARM_ENV = "TIT_NZ_NZBN"
DAYS_ENV = "TIT_NZ_DAYS"
KEY_HEADER = "Ocp-Apim-Subscription-Key"

# Flip only after the routes were read against the live API with the key.
ROUTES_VERIFIED_LIVE = False

DEFAULT_DAYS = 8
MAX_DAYS = 90
REQUEST_DELAY = 1.0
TIMEOUT = 60
PAGE_SIZE = 100
MAX_PAGES = 50

REVISITS_ITS_SOURCE_URL = True

# ANZSIC06 classes: software publishing and computer system design. Prefixes,
# because the register states the code with a letter division in front.
DEFAULT_ANZSIC_PREFIXES = ("J542", "M700")

# Change events that mean "this entity now exists". The exact vocabulary is one
# of the unverified routes: confirm it from the probe before arming.
NEW_REGISTRATION_EVENT = "NEW_REGISTRATION"

# The only entity forms allowed through. Everything else, sole traders and
# partnerships first among them (natural persons), is excluded by default.
_COMPANY_TYPE_CODES = {"LTD", "ULTD", "COMP", "COMPANY", "NZCOMPANY",
                       "OVERSEAS", "OVERSEASCOMPANY"}
_PRIVATE_FORM_WORDS = ("sole", "trader", "partnership", "individual",
                       "natural person", "trust")

LAST_RUN: dict = {}

ATTRIBUTION_FMT = ("Data sourced from the Ministry of Business, Innovation and "
                   "Employment's NZBN register. Last refreshed {stamp}.")


class NzbnError(RuntimeError):
    """The register moved, refused us, or was read in a shape we do not know."""


class NzbnKeyMissing(NzbnError):
    pass


# --- the locks ---------------------------------------------------------------

def armed() -> bool:
    return (os.environ.get(ARM_ENV) or "off").strip().lower() in (
        "1", "on", "true", "yes", "arm", "armed")


def api_key() -> str:
    key = (os.environ.get(KEY_ENV) or "").strip()
    if not key:
        raise NzbnKeyMissing(f"{KEY_ENV} is not set; the NZBN API needs a key.")
    return key


def days_from_env(default_days: int | None = None) -> int:
    raw = (os.environ.get(DAYS_ENV) or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,3}", raw) or not 1 <= int(raw) <= MAX_DAYS:
        raise NzbnError(f"{DAYS_ENV} holds {raw!r}; use 1..{MAX_DAYS} days")
    return int(raw)


# --- the terms: attribution and privacy --------------------------------------

def attribution(retrieved_at: datetime | None = None) -> str:
    """The attribution MBIE's agreement (schedule 2) requires: source and time."""
    when = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ATTRIBUTION_FMT.format(stamp=when.strftime("%Y-%m-%d %H:%M UTC"))


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(value or "").lower()).strip()


def is_company_form(entity: dict) -> bool:
    """True only for a recognised company form. Fails CLOSED.

    A sole trader can hide every field but the NZBN at any time, name included,
    so the safe default for a form we cannot place is to drop the entity.
    """
    code = _norm(entity.get("entityTypeCode")).replace(" ", "").upper()
    desc = _norm(entity.get("entityTypeDescription"))
    if any(word in desc for word in _PRIVATE_FORM_WORDS):
        return False
    if any(word in _norm(entity.get("entityTypeCode")) for word in _PRIVATE_FORM_WORDS):
        return False
    if code in _COMPANY_TYPE_CODES:
        return True
    return "company" in desc or "limited" in desc


_PUBLIC_FIELDS = ("nzbn", "entityName", "entityStatusCode",
                  "entityStatusDescription", "entityTypeCode",
                  "entityTypeDescription", "registrationDate",
                  "industryClassifications", "addresses")


def public_view(entity: dict) -> dict | None:
    """The record as the register shows it NOW, or None if it may not be shown.

    Built from the current response alone, never merged over an earlier read, so
    a field that has since become private is absent here and stays dropped.
    """
    if not isinstance(entity, dict) or not is_company_form(entity):
        return None
    view = {k: entity[k] for k in _PUBLIC_FIELDS if entity.get(k) not in (None, "", [], {})}
    if not view.get("nzbn") or not view.get("entityName"):
        return None
    return view


def refresh(previous: dict | None, current: dict) -> dict | None:
    """Replace, never merge: `previous` is ignored on purpose."""
    return public_view(current)


# --- fields ------------------------------------------------------------------

def parse_date(raw) -> str | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(raw or ""))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def anzsic_codes(view: dict) -> list:
    out = []
    for item in view.get("industryClassifications") or []:
        code = str((item or {}).get("classificationCode") or "").strip().upper()
        if code:
            out.append((code, str((item or {}).get("classificationDescription") or "").strip()))
    return out


def matches_industry(view: dict, prefixes=DEFAULT_ANZSIC_PREFIXES) -> tuple | None:
    for code, desc in anzsic_codes(view):
        if any(code.startswith(p) for p in prefixes):
            return code, desc
    return None


def is_registered(view: dict) -> bool:
    status = _norm(view.get("entityStatusDescription") or view.get("entityStatusCode"))
    return status in ("registered", "reg", "active", "") and "removed" not in status


def _city(view: dict) -> str:
    for addr in ((view.get("addresses") or {}).get("addressList") or []):
        if _norm(addr.get("addressType")) in ("registered", "physical", "service"):
            town = str(addr.get("address3") or addr.get("city") or "").strip()
            if town:
                return town
    return ""


def _pretty(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


# --- one row -----------------------------------------------------------------

def _row(view: dict, *, retrieved_at: datetime, prefixes=DEFAULT_ANZSIC_PREFIXES) -> dict | None:
    if view is None:
        return None
    industry = matches_industry(view, prefixes)
    when = parse_date(view.get("registrationDate"))
    if not (industry and when and is_registered(view)):
        return None
    name = re.sub(r"\s+", " ", str(view["entityName"])).strip()
    nzbn = str(view["nzbn"]).strip()
    code, described = industry
    stamp = attribution(retrieved_at)
    headline = f"{name}: registered in New Zealand on {_pretty(when)}"
    summary = (
        f"The NZBN register records that {name} (NZBN {nzbn}) was registered on "
        f"{_pretty(when)} with industry classification {code}"
        + (f" ({described})" if described else "") + f". {stamp}")
    body = (f"{summary} This is the legal fact that a company now exists and "
            f"nothing more: it does not say whether the company has raised money, "
            f"hired anyone or begun trading.")
    city = _city(view)
    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "summary": summary,
        "source_url": ENTITY_PAGE.format(nzbn=nzbn),
        "source_name": SOURCE_NAME,
        "discovery_url": DISCOVERY_PAGE,
        "published_date": when,
        "company": name,
        "country": "New Zealand",
        "hq_city": city,
        "industry": "technology",
        "nzbn": nzbn,
        "anzsic_code": code,
        "registered_on": when,
        "attribution": stamp,
        "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "collector": COLLECTOR,
        "fetched_at": retrieved_at.isoformat(timespec="seconds"),
    }


# --- the API -----------------------------------------------------------------

def _headers(key: str, etag: str | None = None) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json", KEY_HEADER: key}
    if etag:
        h["If-Match"] = etag
    return h


def change_events(start: str, end: str, *, key: str, session=None) -> list:
    """NZBNs with a new-registration change event in [start, end]. Paged."""
    get = (session or requests).get
    nzbns, page = [], 0
    while page < MAX_PAGES:
        resp = get(f"{API}/entities/changes", headers=_headers(key), timeout=TIMEOUT,
                   params={"change-event-type": NEW_REGISTRATION_EVENT,
                           "start-date": start, "end-date": end,
                           "page": page, "page-size": PAGE_SIZE})
        if resp.status_code in (401, 403):
            raise NzbnError(f"Change Event Search answered {resp.status_code}: the key was refused")
        if resp.status_code != 200:
            raise NzbnError(f"Change Event Search answered HTTP {resp.status_code}")
        items = (resp.json() or {}).get("items") or []
        nzbns.extend(str(i.get("nzbn")) for i in items if i.get("nzbn"))
        if len(items) < PAGE_SIZE:
            return nzbns
        page += 1
        if session is None:
            time.sleep(REQUEST_DELAY)
    raise NzbnError(f"change search reached its {MAX_PAGES}-page ceiling; narrow {DAYS_ENV}")


def get_entity(nzbn: str, *, key: str, etag: str | None = None, session=None):
    """(entity_or_None, etag, status). 304 when `etag` still matches."""
    get = (session or requests).get
    resp = get(f"{API}/entities/{nzbn}", headers=_headers(key, etag), timeout=TIMEOUT)
    if resp.status_code == 304:
        return None, etag, 304
    if resp.status_code == 404:
        return None, None, 404
    if resp.status_code != 200:
        raise NzbnError(f"entity {nzbn} answered HTTP {resp.status_code}")
    return resp.json(), resp.headers.get("ETag"), 200


# --- the run -----------------------------------------------------------------

def collect(queries=None, *, days: int | None = None, today: date | None = None,
            session=None, etags: dict | None = None, prefixes=DEFAULT_ANZSIC_PREFIXES) -> list[dict]:
    """New company registrations in software and IT inside the window.

    DORMANT by default. `queries` is accepted and ignored. `etags` maps NZBN to
    the ETag from an earlier read; it is a parameter, not state, because a
    collector never writes.
    """
    if not armed():
        print(f"[{COLLECTOR}] DORMANT ({ARM_ENV} is off) - no request made, no key "
              f"sent, nothing stored.")
        LAST_RUN.clear()
        LAST_RUN.update({"armed": False, "read": 0})
        return []
    if not ROUTES_VERIFIED_LIVE:
        raise NzbnError("armed, but the NZBN routes were never read against the live "
                        "API (ROUTES_VERIFIED_LIVE is False). Run the probe first.")
    key = api_key()
    window = days if days is not None else days_from_env()
    end_day = today or datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=window)).isoformat()
    end = end_day.isoformat()
    etags = etags if etags is not None else {}

    nzbns = list(dict.fromkeys(change_events(start, end, key=key, session=session)))
    out, unchanged, private, off_scope = [], 0, 0, 0
    for nzbn in nzbns:
        entity, etag, status = get_entity(nzbn, key=key, etag=etags.get(nzbn), session=session)
        if status == 304:
            unchanged += 1
            continue
        if entity is None:
            continue
        if etag:
            etags[nzbn] = etag
        view = refresh(None, entity)
        if view is None:
            private += 1
            continue
        row = _row(view, retrieved_at=datetime.now(timezone.utc), prefixes=prefixes)
        if row is None:
            off_scope += 1
            continue
        out.append(row)
        if session is None:
            time.sleep(REQUEST_DELAY)
    print(f"[{COLLECTOR}] {start}..{end}: {len(nzbns)} new registrations, {unchanged} "
          f"unchanged (304), {private} excluded as non-company, {off_scope} out of "
          f"industry scope, {len(out)} stored")
    LAST_RUN.clear()
    LAST_RUN.update({"armed": True, "read": len(nzbns), "stored": len(out)})
    return out


def as_classified(item: dict) -> dict:
    return {
        "company": item["company"],
        "pillar": "company_development",
        "signal_direction": "neutral",
        "headline": item["headline"],
        "summary": item["summary"],
        "talent_readthrough": (
            "A registration is the fact that a company now exists in New Zealand, "
            "not that it has raised money, hired anyone or begun trading. Sole "
            "traders and other entities that may hide their details are excluded "
            "by design, so this reads as new company formation in software and IT "
            "and never as a count of all new businesses."),
        "country": "New Zealand",
        "headquarters_city": item.get("hq_city") or "",
        "headquarters_country": "New Zealand",
        "industry": item.get("industry") or "technology",
        "employer_type": "",
        "confidence": "verified",
    }

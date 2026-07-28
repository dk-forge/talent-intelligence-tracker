"""SEC executive compensation, from the XBRL frames API. The pay pillar's spine.

Item 402(v) of Regulation S-K makes every US domestic registrant tag its
principal executive officer's total compensation in the pay-versus-performance
table of its proxy statement. SEC then republishes every registrant's value for
a period as a single JSON document:

    https://data.sec.gov/api/xbrl/frames/ecd/PeoTotalCompAmt/USD/CY2025.json

One request returns one calendar year for every filer that reported it — 1,141
companies for CY2025, verified live on 2026-07-28. Frames exist for CY2022
onward, which is the whole life of the disclosure.

Why this collector exists, in the order the reasons matter:

- **No model is involved.** `cik`, `entityName`, `val`, `accn` and the period
  are columns, so the record is derived. There is no LLM cost at all.
- **It carries backfill.** Four years of it, from one request per year.
- **It joins.** The frame hands over the CIK, which is the same key the SEC
  filing collectors and the sibling layoff tracker use, so a pay figure lands
  on the employer we already know rather than on a new name string.

Rows still go through `validate.build_signal` -> `store` -> `publish` like
everything else. Nothing is written directly.

Two things this module is deliberate about:

1. **The source URL is the filing, never the frame.** `accn` + `cik` build the
   proxy statement's own EDGAR index page, which is the document that makes
   the claim. The frames JSON is a dataset, and a dataset is not a receipt.
2. **`published_date` is the period end**, because the frame does not carry a
   filing date and no honest one can be derived from an accession number. The
   figure describes the fiscal year that ended on that date, the headline and
   the summary both say so, and dating it that way is what puts a CY2022
   figure on the 2022 timeline instead of on the day we happened to fetch it.

SEC gotcha, learned by the funding backfill and not re-learnable cheaply: the
User-Agent must carry a contact address at no more than 10 requests/second. A
browser-shaped UA gets "Request Rate Threshold Exceeded". `sec_edgar.USER_AGENT`
is the right shape and is reused here rather than restated, so the two cannot
drift.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

import requests

from . import sec_edgar

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/ecd/{tag}/USD/CY{year}.json"
TAG = "PeoTotalCompAmt"
COLLECTOR = "sec_execcomp"
USER_AGENT = sec_edgar.USER_AGENT
REQUEST_DELAY = sec_edgar.REQUEST_DELAY

# The disclosure began with proxy statements filed in 2023, so CY2022 is the
# first frame that exists. Asking for anything earlier is a mistake, not an
# empty year, and it is refused rather than silently returning nothing.
FIRST_YEAR = 2022

# A calendar year of this frame holds a four-figure number of registrants.
# Anything much smaller means the request was throttled, the tag was renamed or
# the taxonomy moved — none of which is "a quiet year".
MIN_ROWS_PER_YEAR = 100

# A filer's own tagging error is still a wrong number on our page.
#
# The CY2025 frame holds a $3.78bn PEO total for a company with a $1.5bn market
# capitalisation, and a $1.02bn total for an employer whose chief executive is
# on public record taking about a million dollars. Both are scale mistakes in
# the XBRL tag, not pay packets. The genuine mega-grants sit below this line —
# Welltower at $821m and Opendoor at $741m were both reported as real — so a
# billion dollars is where "extraordinary" stops and "mis-tagged" starts.
#
# Dropped, never corrected: repairing someone else's filing would be inventing
# a figure, which is the one thing this pipeline may not do.
MAX_PLAUSIBLE = 1_000_000_000

_US_STATE_LOC = re.compile(r"^US-([A-Z]{2})$")


class FrameError(RuntimeError):
    """A year's frame could not be read, or came back implausibly empty."""


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def years_from_env(default_years: list[int] | None = None) -> list[int]:
    """Which calendar years to collect.

    Set by the workflow rather than by a backfill script of its own: the whole
    point of this source is that a year costs one request, so a backfill is
    just a longer list of years through the same path.
    """
    raw = (os.environ.get("TIT_EXECCOMP_YEARS") or "").strip()
    if not raw:
        if default_years:
            return list(default_years)
        # The most recent COMPLETE calendar year. The current year's frame does
        # not exist until proxies for it are filed, which is a year away.
        return [datetime.now(timezone.utc).year - 1]
    years: list[int] = []
    for token in re.split(r"[,\s]+", raw):
        if not token:
            continue
        digits = re.sub(r"\D", "", token)
        if len(digits) != 4:
            raise FrameError(f"TIT_EXECCOMP_YEARS holds {token!r}, which is not a year")
        years.append(int(digits))
    return years


def fetch_frame(year: int, *, timeout: int = 60) -> list[dict]:
    """One calendar year of PEO total compensation, as the API returns it."""
    if year < FIRST_YEAR:
        raise FrameError(
            f"CY{year} predates the pay-versus-performance disclosure; the "
            f"first frame SEC publishes is CY{FIRST_YEAR}")
    url = FRAMES_URL.format(tag=TAG, year=year)
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers=_headers(), timeout=timeout)
    if resp.status_code == 404:
        raise FrameError(f"CY{year} is not published yet: {url}")
    if resp.status_code != 200:
        raise FrameError(
            f"{url} returned {resp.status_code} (User-Agent: {USER_AGENT}). "
            f"SEC rejects a request with no contact address in the UA.")
    data = resp.json().get("data") or []
    if len(data) < MIN_ROWS_PER_YEAR:
        raise FrameError(
            f"CY{year} returned {len(data)} registrants from {url}. A calendar "
            f"year of this frame holds four figures, so this is the request "
            f"being throttled or the tag being renamed, not a quiet year.")
    return data


def filing_url(cik, accession: str) -> str | None:
    """The proxy statement's own EDGAR index page.

    Built from the frame's `accn` and `cik`, which is why no second request is
    needed to get a real document URL. Byte-shape matches what the other SEC
    collectors store, under the same /Archives/edgar/data root.
    """
    digits = re.sub(r"\D", "", str(cik or "")).lstrip("0")
    accession = (accession or "").strip()
    if not digits or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        return None
    return f"{sec_edgar.ARCHIVES}/{digits}/{accession.replace('-', '')}/{accession}-index.htm"


def humanise(amount: int) -> str:
    """$25,647,769 -> '$25.6M'. Rounded for reading, never for storing."""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def _row(entry: dict, year: int) -> dict | None:
    company = (entry.get("entityName") or "").strip()
    cik = entry.get("cik")
    accession = (entry.get("accn") or "").strip()
    end = (entry.get("end") or "").strip()
    try:
        value = int(entry.get("val"))
    except (TypeError, ValueError):
        return None
    # A negative or zero PEO total is a restatement artefact, not a pay packet.
    if value <= 0 or not company or not end:
        return None
    if value >= MAX_PLAUSIBLE:
        print(f"[{COLLECTOR}] SKIP {company}: ${value:,} is a tagging error, not pay")
        return None

    url = filing_url(cik, accession)
    if not url:
        return None

    money = humanise(value)
    exact = f"${value:,}"
    state = ""
    loc = _US_STATE_LOC.match((entry.get("loc") or "").strip().upper())
    if loc:
        state = loc.group(1)

    # Both the exact figure and the rounded one appear here, because the
    # summary quotes both and every figure in a summary must be present in the
    # source text or the record is discarded.
    headline = (
        f"{company}: {exact} total compensation for the principal executive "
        f"officer, fiscal year ended {end}"
    )
    body = (
        f"{company} (CIK {cik}) reported {exact} ({money}) as the total "
        f"compensation of its principal executive officer for the fiscal year "
        f"beginning {entry.get('start') or 'not stated'} and ended {end}. "
        f"The figure is the {TAG} value tagged in the pay-versus-performance "
        f"table required by Item 402(v) of Regulation S-K, filed with the SEC "
        f"under accession {accession}. Every US domestic registrant must tag "
        f"it, which is what makes the figure comparable across employers "
        f"rather than a number an outlet chose to print."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": "SEC EDGAR (pay-versus-performance)",
        "discovery_url": url,
        "published_date": end,
        "cik": str(cik or ""),
        "country": "United States",
        "state": state,
        "company": company,
        "amount_usd": value,
        "amount_exact": exact,
        "money": money,
        "period_end": end,
        "calendar_year": year,
        "accession": accession,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def collect(queries=None, *, years: list[int] | None = None) -> list[dict]:
    """Every registrant's PEO total compensation for each requested year.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the frame is the
    whole population.
    """
    wanted = years if years is not None else years_from_env()
    out: list[dict] = []
    for year in wanted:
        entries = fetch_frame(year)
        kept = [row for row in (_row(e, year) for e in entries) if row]
        print(f"[{COLLECTOR}] CY{year}: {len(entries)} registrants, {len(kept)} usable")
        if not kept:
            raise FrameError(
                f"CY{year} produced no usable rows from {len(entries)} "
                f"registrants — the frame's field names have changed.")
        out.extend(kept)
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is read off the frame or is a fixed editorial line, so nothing
    on the record can be something a model believed. The read-through is the
    same sentence on every row of this source, which is honest about being a
    general inference rather than a specific insight.
    """
    company = item["company"]
    return {
        "company": company,
        "pillar": "rewards_comp",
        # Not "hiring": a pay disclosure moves the compensation picture, it
        # does not say anyone is being hired.
        "signal_direction": "comp_shift",
        "headline": item["headline"],
        "summary": (
            f"{company} disclosed {item['amount_exact']} ({item['money']}) in "
            f"total compensation for its principal executive officer for the "
            f"fiscal year ended {item['period_end']}, in the "
            f"pay-versus-performance table of its SEC proxy statement."
        ),
        "talent_readthrough": (
            "Top-of-house pay is the anchor the rest of an employer's pay "
            "structure is set against, and it is the one compensation figure "
            "every US-listed company must publish on the same basis. Read it "
            "as a comparable, not as a salary band: it is a single officer's "
            "total, including equity awards valued under SEC rules, and it "
            "says nothing directly about what a given role pays."
        ),
        "country": item.get("country") or "",
        "state": item.get("state") or "",
        # Earned by the source, not asserted by us: infer_confidence caps this
        # at what sec.gov is worth, which for a filing is 'verified'.
        "confidence": "verified",
    }

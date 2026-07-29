"""SEC Form D collector — the funding signal, free and primary-sourced.

Every US private placement files a Form D, and the filing is structured XML
rather than prose: issuer name, industry, city, state, and the amount actually
sold. That means the money figure is a fact read off a legal filing, not a
number a model produced — which is the only kind of figure this product stores.

A Form D states money and nothing else. It does not say the issuer is hiring,
and this collector never claims it does: the row carries the raise as a fact
and leaves the headcount question open. That is why `signal_direction` on these
rows is "neutral" — the rule in `pipeline/classify.py` is that the direction is
what the SOURCE STATES about headcount, never what the event usually implies.

**The noise problem**: most Form D filers are not employers at all. Two thirds
are investment funds, and much of the rest are single-purpose vehicles that
raise money for one building and employ nobody. `industryGroupType` names both
classes, so it is checked first; the name patterns are the second pass, for the
vehicles that file under a generic group.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import requests

from . import sec_edgar

EFTS_URL = sec_edgar.EFTS_URL
ARCHIVES = sec_edgar.ARCHIVES
USER_AGENT = sec_edgar.USER_AGENT
COLLECTOR = "sec_form_d"
REQUEST_DELAY = 0.15

# A fund raising a fund is not a talent signal. This is ~70% of Form D volume.
POOLED_FUND_INDUSTRIES = {
    "pooled investment fund",
    "other investment fund",
    "hedge fund",
    "private equity fund",
    "venture capital fund",
}

# Neither is a building. Form D's REAL ESTATE group ("Commercial",
# "Residential", "Construction", "REITS and Finance", "Other Real Estate") is
# filed almost entirely by single-purpose vehicles: one LLC per property, which
# raises the money, buys the asset and employs nobody. A recruiter or a job
# seeker can do nothing with them, and because each raise is large they
# dominated the money views — 874 of the first 4,003 published rows, and every
# dollar of the "Real estate & construction $12.2B" total.
#
# The cost of this rule is stated rather than hidden: a genuine operating
# employer that files under the same group (a brokerage, a general contractor)
# is dropped with them. On a sample of the live rows that was ~2%, and the
# dataset gives no column that separates the two.
REAL_ESTATE_INDUSTRIES = {
    "commercial",
    "residential",
    "construction",
    "reits and finance",
    "other real estate",
}

EXCLUDED_INDUSTRIES = POOLED_FUND_INDUSTRIES | REAL_ESTATE_INDUSTRIES

# industryGroupType alone is not enough: plenty of vehicles file under a
# generic group ("Other", "Investing", "Business Services") and are still
# vehicles, not employers. The name gives them away — a tenant-in-common
# interest, a qualified opportunity fund, an SPV, a numbered series in a
# ladder, a conglomerate holding private equity or private credit. These
# patterns are deliberately narrow: an operating company must never be dropped,
# so each one is a phrase no real employer puts in its name.
EXCLUDED_NAME_PATTERNS = re.compile(
    r"\b("
    r"fund\s*(?:i{1,3}|iv|v|vi{0,3}|\d+)?(?:-[a-z])?\b"
    r"|dst\b|reit\b|\btrust\b"
    r"|net[- ]leased?|opportunity zone|statutory trust"
    r"|\bl\.?p\.?$|\bllp$"
    r"|series\s+[a-z0-9]+\s+(?:dst|lp|llc)$"
    r"|holdings?\s+(?:i{1,3}|\d+)$"
    # Single-purpose property and investment vehicles.
    r"|tic\b|qof\b|spv\b|investco\b|conglomerate\b"
    r"|private\s+equity\b|private\s+credit\b"
    r"|co-?invest(?:ors?|ment)?\b"
    r"|apartments?\b|condominiums?\b|villas?\b|townhomes?\b"
    # Property vehicles that file under a generic industry group. Note what is
    # NOT here: "estates" matches "Real Estate Business Analytics, Inc.", a
    # software company, and "development" matches "Strobe Development, Inc.".
    r"|properties\b|realty\b|land\s?co\b|(?:golf|country)\s+club\b"
    # "MIMG CCLXV Rapid City 6 Master, LLC" — the master entity of a syndication.
    r"|master,?\s*(?:llc|lp)\.?$"
    # A roman numeral immediately before the entity suffix is a series vehicle:
    # "Northfield V74 I, LLC", "CRA Funding VIII, LLC", "HMA III, Inc.".
    r"|(?:i{1,3}|iv|vi{0,3}|ix|xi{0,3})[\s,]*(?:llc|lp|inc)\.?$"
    r")", re.I,
)

# Below this the raise is too small to imply meaningful hiring, and the noise
# floor (shell companies, single-property real estate LPs) is high.
MIN_RAISED = 1_000_000


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return (m.group(1) or "").strip() if m else ""


def _money(value: str) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _humanise(amount: int) -> str:
    """Render the figure the way a source would write it, so the
    figures-are-sourced check has something to match against."""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B".replace(".0B", "B")
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M".replace(".0M", "M")
    return f"${amount:,}"


def search(days_back: int = 5, page: int = 0, *,
           startdt: str | None = None, enddt: str | None = None) -> list[dict]:
    """One EFTS page of Form D filings. Returns raw hits.

    Explicit startdt/enddt (YYYY-MM-DD) override days_back, the same shape
    sec_edgar.search already has: the backfill walks historical windows this
    way while the daily run keeps its rolling few days.
    """
    if not (startdt and enddt):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        startdt = start.strftime("%Y-%m-%d")
        enddt = end.strftime("%Y-%m-%d")
    params = {
        "q": '"equity"',           # EFTS requires a term; every Form D carries it
        "forms": "D",
        "dateRange": "custom",
        "startdt": startdt,
        "enddt": enddt,
        "from": page * 10,
    }
    time.sleep(REQUEST_DELAY)
    resp = requests.get(EFTS_URL, params=params,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        timeout=30)
    resp.raise_for_status()
    return (resp.json().get("hits") or {}).get("hits") or []


def collect(queries=None, *, days_back: int = 5, pages: int = 3,
            max_items: int = 25) -> list[dict]:
    """Return raw candidate dicts. `queries` is accepted and ignored so this is
    interchangeable with the other collectors."""
    out: list[dict] = []
    seen: set[str] = set()

    for page in range(pages):
        try:
            hits = search(days_back=days_back, page=page)
        except requests.RequestException:
            break
        if not hits:
            break

        for hit in hits:
            if len(out) >= max_items:
                return out

            url = sec_edgar.document_url(hit)
            if not url or url in seen:
                continue
            seen.add(url)

            try:
                time.sleep(REQUEST_DELAY)
                xml = requests.get(url, headers={"User-Agent": USER_AGENT},
                                   timeout=30).text
            except requests.RequestException:
                continue

            industry = _tag(xml, "industryGroupType")
            if industry.lower() in EXCLUDED_INDUSTRIES:
                continue

            raised = _money(_tag(xml, "totalAmountSold"))
            if not raised or raised < MIN_RAISED:
                continue

            company = _tag(xml, "entityName")
            if not company:
                continue
            if EXCLUDED_NAME_PATTERNS.search(company):
                # An investment vehicle raising capital employs nobody; only an
                # operating company's raise implies hiring.
                continue

            city = _tag(xml, "city").title()
            state = _tag(xml, "stateOrCountry")
            money = _humanise(raised)

            # The classifier reads only raw_text, so every fact it may use has
            # to be stated here — in the words a source would use, because the
            # figures-are-sourced check compares against exactly this string.
            headline = f"{company} raised {money} in a private placement"
            body = (
                f"{company} filed a Form D with the SEC reporting {money} "
                f"({raised:,} dollars) sold in a private securities offering. "
                f"Industry: {industry}. Location: {city}, {state}. "
                f"Form D filings are required for exempt offerings and are the "
                f"public record of private fundraising."
            )

            out.append({
                "raw_text": f"{headline}\n\n{body}",
                "headline": headline,
                "source_url": url,
                "source_name": "SEC EDGAR (Form D)",
                "discovery_url": url,
                "published_date": (hit.get("_source") or {}).get("file_date"),
                "country": "United States",
                "state": state,
                "city": city,
                "funding_amount": money,
                "collector": COLLECTOR,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    return out

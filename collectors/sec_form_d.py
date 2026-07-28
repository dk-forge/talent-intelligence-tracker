"""SEC Form D collector — the funding signal, free and primary-sourced.

Every US private placement files a Form D, and the filing is structured XML
rather than prose: issuer name, industry, city, state, and the amount actually
sold. That means the money figure is a fact read off a legal filing, not a
number a model produced — which is the only kind of figure this product stores.

Funding is a leading indicator for hiring: a company that closed a round is
staffing up two to six months later. That is the whole reason it belongs here.

**The noise problem**: most Form D filers are investment funds, not companies
raising money. `industryGroupType` says which, so pooled investment funds are
dropped before anything else happens.
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
EXCLUDED_INDUSTRIES = {
    "pooled investment fund",
    "other investment fund",
    "hedge fund",
    "private equity fund",
    "venture capital fund",
}

# industryGroupType alone is not enough: real-estate syndications and Delaware
# statutory trusts file under "Other Real Estate" and are still investment
# vehicles, not employers. The name gives them away.
EXCLUDED_NAME_PATTERNS = re.compile(
    r"\b("
    r"fund\s*(?:i{1,3}|iv|v|vi{0,3}|\d+)?(?:-[a-z])?\b"
    r"|dst\b|reit\b|\btrust\b"
    r"|net[- ]leased?|opportunity zone|statutory trust"
    r"|\bl\.?p\.?$|\bllp$"
    r"|series\s+[a-z0-9]+\s+(?:dst|lp|llc)$"
    r"|holdings?\s+(?:i{1,3}|\d+)$"
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

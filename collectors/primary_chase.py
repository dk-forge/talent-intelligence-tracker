"""Chase a named event to the EMPLOYER'S OWN announcement.

WHY THIS EXISTS
---------------
`analysis/` measured the recall gap on the three largest private funding rounds
of 2026 and found the same shape three times: the round WAS discovered, in
several languages, and then died downstream — host-blocked, gate-errored, or
deferred unread when the month's allowance ran out. Every copy we ever saw was
a rewrite. Nothing in the pipeline ever read the document the rewrites were
rewriting, because a company's own newsroom is not an RSS feed anybody wired.

`collectors/tripwire_chase.py` chases a lead to *a publisher's* article. This
chases a lead to *the primary document*: the employer's own announcement post,
or the regulator's own filing. It is the same discipline one step earlier.

THE ONE RULE
------------
The work list carries a URL and NOTHING ELSE that reaches the database. No
amount, no date, no company name, no headline. Every field is read out of the
document that URL serves, exactly as `national_press` reads a feed, and the
item then goes through the identical `prefilter -> precheck -> extract ->
validate -> dedupe -> store` path as every other candidate, with every guard
that implies. A work list that names a round the document does not state
stores nothing, and that is the point: the list is a place to LOOK, never a
fact.

Consequences worth being explicit about, the same three the tripwire chase
states, one rung up:

  * A wrong URL yields no candidate. It cost one HTTP GET and no money.
  * A URL whose document says a different number stores the DOCUMENT's number,
    because the number comes from the page and never from the list.
  * The stored source is the primary document. There is no aggregator in the
    chain at all, so there is nothing to canonicalise past.

COST
----
Free at fetch. The candidate then meets the ordinary economics: if the headline
states every field, `pipeline/cheap_extract.py` closes it deterministically for
$0 and no model reads it; if it does not, it queues for the paid path like
anything else and defers unread when the allowance is spent. A newsroom
headline is unusually likely to close for free precisely because the company
wrote it to state the round — "Anthropic raises $65B in Series H funding at
$965B post-money valuation" is the record.

DORMANT. Nothing schedules this, and it should stay that way: a standing list
of URLs re-fetched twice a day is a list of documents that have already been
read. Run it by hand after a recall measurement names a miss:

    python run_collect.py --source primary_chase --dry-run
    python run_collect.py --source primary_chase

With an empty work list it fetches nothing and says so, which run_collect
reports as degraded — a chase with nothing to chase should look exactly as
quiet as it is.
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timezone

import requests

COLLECTOR = "primary_chase"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKLIST_PATH = os.path.join(ROOT, "data", "primary_chase_worklist.json")

# A corporate newsroom is served by a CDN that expects a browser, exactly as
# Google News' resolution endpoint does. The descriptive UA rule is about the
# WordPress host and does not apply here.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Politeness, and a ceiling on one hand-run pass. The money is bounded
# downstream by classify.read_cap, so this is not a spend control.
MAX_LEADS = 25

# How much of the document the classifier gets. Long enough that every figure
# the headline states is also present in the body (validate rejects a figure
# that is not in raw_text), short enough that a paid read of it is cheap.
BODY_CHARS = 2000

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_META = re.compile(
    r"""<meta[^>]+?(?:property|name)\s*=\s*["'](?P<key>[^"']+)["'][^>]*?"""
    r"""content\s*=\s*["'](?P<val>[^"']*)["'][^>]*>""", re.I)
_META_REV = re.compile(
    r"""<meta[^>]+?content\s*=\s*["'](?P<val>[^"']*)["'][^>]*?"""
    r"""(?:property|name)\s*=\s*["'](?P<key>[^"']+)["'][^>]*>""", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# A masthead a corporate site glues onto its own <title>: " \ Anthropic",
# " | OpenAI". Only ever stripped from <title>, never from og:title, and only
# when og:title was missing.
_MASTHEAD = re.compile(r"\s*[\\|·–—]\s*[^\\|·–—]{2,30}$")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# The visible dateline, in the two shapes a newsroom writes it. Read from the
# rendered text, not from the URL and never from the work list.
_DATE_TEXT = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>20\d{2})\b")
_DATE_TEXT_DMY = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|"
    r"Nov|Dec)[a-z]*\.?\s+(?P<year>20\d{2})\b")


def load_worklist(path: str = WORKLIST_PATH) -> list[dict]:
    """The URLs to chase, or nothing.

    A missing or malformed list is an empty list, never an exception: a chase
    with nothing to chase is a quiet run, not a broken collector.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    leads = data.get("leads") if isinstance(data, dict) else data
    if not isinstance(leads, list):
        return []
    out = []
    for lead in leads:
        if isinstance(lead, str):
            lead = {"url": lead}
        if not isinstance(lead, dict):
            continue
        url = (lead.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            out.append({"url": url,
                        "archive_fallback": bool(lead.get("archive_fallback"))})
    return out[:MAX_LEADS]


# Some publishers refuse every non-interactive client outright — openai.com
# answers 403 to any request that is not a browser session, on every path but
# the sitemap. That is a fact about their edge, not a fact about the document,
# and the document is public: the Internet Archive holds it. So a lead may say
# `archive_fallback` and the collector reads the archived copy of THE SAME URL.
#
# What is stored is unchanged: `source_url` stays the publisher's own permalink,
# because that is the document this record is about, and `discovery_url` records
# the archived copy that was actually read. This is the same relationship
# `archive_sources.py` already maintains from the other direction — the archive
# is the evidence's backup, never a source in its own right. NOT a way around
# bot detection: it is a public mirror of a public page, fetched from the
# mirror's own public API.
_WAYBACK_AVAILABLE = "https://archive.org/wayback/available?url={url}"
_WAYBACK_RAW = "https://web.archive.org/web/{stamp}id_/{url}"


def _archived(url: str, *, timeout: int, session) -> str | None:
    """The newest archived snapshot of `url`, or None."""
    try:
        resp = session.get(_WAYBACK_AVAILABLE.format(url=url), timeout=timeout,
                           headers={"User-Agent": BROWSER_UA})
        snap = ((resp.json() or {}).get("archived_snapshots") or {}).get("closest") or {}
    except (requests.RequestException, ValueError):
        return None
    if not snap.get("available") or not snap.get("timestamp"):
        return None
    return _WAYBACK_RAW.format(stamp=snap["timestamp"], url=url)


def _metas(page: str) -> dict:
    found = {}
    for pattern in (_META, _META_REV):
        for m in pattern.finditer(page):
            key = m.group("key").strip().lower()
            found.setdefault(key, html.unescape(m.group("val")).strip())
    return found


def _visible_text(page: str) -> str:
    body = _SCRIPT.sub(" ", page)
    body = _TAG.sub(" ", body)
    return re.sub(r"\s+", " ", html.unescape(body)).strip()


def _stated_date(page: str, text: str) -> str:
    """YYYY-MM-DD the DOCUMENT states, or "".

    Structured metadata first; then the visible dateline, but only when the
    page states exactly one date in its first screenful. Two different dates
    is an ambiguity, and choosing one of them is the guess this collector
    exists not to make.
    """
    metas = _metas(page)
    for key in ("article:published_time", "article:published",
                "publishdate", "date", "pubdate", "dc.date", "og:published_time"):
        raw = metas.get(key) or ""
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1)

    head = text[:4000]
    hits = set()
    for pattern in (_DATE_TEXT, _DATE_TEXT_DMY):
        for m in pattern.finditer(head):
            mon = _MONTHS.get(m.group("mon").lower())
            if not mon:
                continue
            try:
                hits.add(datetime(int(m.group("year")), mon,
                                  int(m.group("day"))).strftime("%Y-%m-%d"))
            except ValueError:
                continue
    return hits.pop() if len(hits) == 1 else ""


def _headline(page: str, metas: dict) -> str:
    for key in ("og:title", "twitter:title"):
        value = (metas.get(key) or "").strip()
        if value:
            return value
    m = _TITLE.search(page)
    if not m:
        return ""
    return _MASTHEAD.sub("", html.unescape(_TAG.sub("", m.group(1))).strip()).strip()


def _get(url: str, *, timeout: int, session):
    try:
        return session.get(
            url, timeout=timeout,
            headers={"User-Agent": BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml"},
        )
    except requests.RequestException:
        return None


def _fetch(url: str, *, timeout: int, session,
           archive_fallback: bool = False) -> dict | None:
    read_from = url
    resp = _get(url, timeout=timeout, session=session)
    ok = (resp is not None and resp.status_code < 400
          and "html" in (resp.headers.get("content-type") or "").lower())
    if not ok and archive_fallback:
        mirror = _archived(url, timeout=timeout, session=session)
        if mirror:
            print(f"[{COLLECTOR}] {url} refused the fetch; reading the "
                  f"archived copy of the same page")
            read_from = mirror
            resp = _get(mirror, timeout=timeout, session=session)
            ok = (resp is not None and resp.status_code < 400)
    if not ok:
        return None

    page = resp.text
    metas = _metas(page)
    headline = _headline(page, metas)
    text = _visible_text(page)
    if not headline or not text:
        return None

    # raw_text is what every later stage reads. The headline leads, the
    # document's own standfirst follows, then the opening of the body — so the
    # figures the headline states are also present verbatim further down,
    # which is what validate's figure guard checks against.
    description = (metas.get("og:description")
                   or metas.get("description") or "").strip()
    # Start at the LAST time the headline appears, not the first: the first is
    # the <title>, and everything between it and the <h1> is site furniture —
    # nav, skip links, product menus. The <h1> is where the article starts.
    body = text
    at = body.rfind(headline)
    if at >= 0:
        body = body[at + len(headline):]
    parts = [headline]
    if description and description not in body[:BODY_CHARS]:
        parts.append(description)
    parts.append(body[:BODY_CHARS].strip())

    # The final URL, so a redirect to the canonical permalink is what gets
    # cited rather than whatever the work list happened to be typed with. When
    # the body came from the archive the CITED url is still the publisher's:
    # the archive served the document, it did not publish it.
    final = url if read_from is not url else str(resp.url or url)
    return {
        "raw_text": "\n\n".join(p for p in parts if p).strip(),
        "headline": headline,
        "source_url": final,
        "discovery_url": read_from,
        # The document is its own publisher. Kept as the host so nothing here
        # asserts a brand name the page did not state.
        "source_name": re.sub(r"^www\.", "", (
            re.sub(r"^https?://", "", final).split("/")[0])),
        "published_date": _stated_date(page, text),
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def collect(queries=None, *, timeout: int = 25, session=None) -> list[dict]:
    """One raw dict per reachable lead. `queries` is ignored: the work list IS
    the population, the same way a registry frame is for a derived source."""
    leads = load_worklist()
    if not leads:
        print(f"[{COLLECTOR}] no work list at {WORKLIST_PATH} — nothing to chase")
        return []

    session = session or requests.Session()
    items = []
    for lead in leads:
        item = _fetch(lead["url"], timeout=timeout, session=session,
                      archive_fallback=lead.get("archive_fallback", False))
        if item is None:
            print(f"[{COLLECTOR}] unreachable, skipped: {lead['url']}")
            continue
        items.append(item)
    print(f"[{COLLECTOR}] {len(items)} of {len(leads)} lead(s) fetched")
    return items

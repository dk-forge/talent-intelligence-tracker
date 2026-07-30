"""BSE corporate announcements — the leadership pillar outside the United States.

Regulation 30 of the SEBI (Listing Obligations and Disclosure Requirements)
Regulations, 2015 makes every company listed in India disclose a change in its
directors or key managerial personnel to the exchange, and it makes them file it
under a FIXED CATEGORY the company picks from SEBI's list. BSE republishes those
filings as JSON, filterable by that category:

    https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w
        ?strCat=Company+Update&subcategory=Change+in+Directorate&strType=C
        &strPrevDate=YYYYMMDD&strToDate=YYYYMMDD&pageno=N

Verified live on 2026-07-30, for 1 to 29 July 2026:

    Change in Management        657
    Change in Directorate       239
    Resignation of Director     117
    Cessation                    67
                              -----
                              1,080 filings in 29 days, ~13,000 a year

Why this source and not the nine others that were researched alongside it: the
mandated CATEGORY is the whole point. The reason the United States has 7,620
documents in this tracker and Israel has 24 is not that America is busier. It is
that Item 5.02 of Form 8-K gives every US officer change a machine-readable
label, and almost nothing else in the world does. Foreign private issuers file
Form 6-K, which has no item taxonomy at all, so an EDGAR full-text search for
"appointed as" against Israeli filers returns resellers, distributors and
Companies Law boilerplate at about one useful hit in eight. SEBI's Regulation 30
category list is the same kind of mandated taxonomy as Item 5.02, which is what
makes this the one non-US jurisdiction that can be read at volume without
guessing.

Three consequences worth stating plainly:

- **No model is involved.** The company, the scrip code, the category, the date
  and the filed description are all fields in the response, so the record is
  derived and `as_classified` closes it. There is no LLM cost at all. The
  headline stored is the description the COMPANY filed, quoted rather than
  paraphrased: a model would read better and could be wrong, and on a source
  this structured that trade is not worth making.
- **Statutory auditors are excluded on purpose.** `Appointment of Statutory
  Auditor/s` and `Resignation of Statutory Auditors` are Regulation 30
  categories too, and they sit right beside the ones below in the same
  response. An auditor is an appointed FIRM, not an employee, so those rows are
  not talent signals and are not collected. Nine such filings appeared in the
  sampled month and none of them are here.
- **`country` is India by construction**, not by inference: every filer on this
  API is a company listed on an Indian exchange. No city is available, and none
  is guessed.

THE LINK-ROT TRAP, found the only way it can be found (by fetching an old one).

BSE serves each filing's PDF from `/xml-data/corpfiling/AttachLive/{name}` while
it is recent and moves it to `AttachHis/{name}` later. A January 2024 attachment
404s on AttachLive and 200s on AttachHis, so a stored AttachLive URL is a link
that will rot on its own. `source_url` is therefore the announcement's own page,
keyed by the filing's `NEWSID`, which is stable and which BSE keeps pointed at
the PDF wherever it has moved it:

    https://www.bseindia.com/corporates/anndet_new?newsid={NEWSID}

ACCESS. bseindia.com serves no robots.txt (the path returns the site's app
shell), so there is no directive to honour and the default applies. The API does
require a browser-shaped User-Agent and a bseindia.com `Referer`; without the
Referer it answers 403. That is the same class of gotcha as ModSecurity blocking
`python-requests` on the WordPress host, and it is why both headers are set on
every request here rather than left to a caller.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ANNOUNCEMENT_URL = "https://www.bseindia.com/corporates/anndet_new?newsid={newsid}"
COLLECTOR = "bse_india"

# The API answers 403 to anything that does not look like the site's own
# front-end. Both headers are required, not decorative.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://www.bseindia.com/"

# The Regulation 30 category every one of these filings sits under.
CATEGORY = "Company Update"

# The sub-categories that are a change of PERSON in a leadership role, exactly
# as SEBI spells them. Anything not on this list is not collected: a value that
# will not normalise is a rejected record, not a new category.
#
# Ordered by measured volume over 1 to 29 July 2026, so the cost of a page walk
# is spent on the productive ones first.
SUBCATEGORIES = (
    "Change in Management",                                  # 657
    "Change in Directorate",                                 # 239
    "Resignation of Director",                               # 117
    "Cessation",                                             #  67
    "Appointment of Company Secretary / Compliance Officer",
    "Resignation of Company Secretary / Compliance Officer",
    "Resignation of Chief Financial Officer (CFO)",
    "Resignation of Chairman",
)

# The sub-categories that look like leadership and are not. Named here rather
# than merely omitted, so that a later reader can see the exclusion was a
# decision and not an oversight.
EXCLUDED_SUBCATEGORIES = (
    "Appointment of Statutory Auditor/s",
    "Resignation of Statutory Auditors",
)

PAGE_SIZE = 50          # what the API returns per page, not a choice of ours
MAX_PAGES = 40          # 2,000 filings per sub-category, well past a week's worth
REQUEST_DELAY = 0.25    # a public API on shared infrastructure; do not hammer it

# How far back a run looks. Seven days on a weekly cron is one cadence plus six
# days of overlap, so a skipped run loses nothing: already-seen URLs are skipped
# before anything is stored, which makes overlap free.
DEFAULT_DAYS = 7

# Across roughly 5,000 listed companies, a week with fewer than this many
# leadership filings has not happened and cannot: the measured rate is about 250
# a week. Below the floor, the category names have moved, the Referer check has
# tightened or the window is wrong. None of those is a quiet week, so the run
# fails loudly instead of reporting a healthy zero.
MIN_ROWS_PER_WINDOW = 10

# A re-appointment of a sitting director is not someone joining. Precision over
# recall: anything this pair cannot separate stays `neutral`.
_APPOINTED = re.compile(r"\bappoint(?:ed|ment|ments|s)?\b", re.I)
_RE_APPOINTED = re.compile(r"\bre[-\s]?appoint", re.I)

# The sub-categories that are a departure whatever the wording says.
_DEPARTURES = ("resignation", "cessation")

# An auditor is an appointed FIRM, not an employee, so appointing one is not a
# talent signal. The two auditor SUB-CATEGORIES are excluded above, but auditor
# appointments also arrive filed under `Change in Management`, where only the
# company's own wording gives them away: 26 of 380 filings in the live window of
# 23 to 30 July 2026 were auditor-only. A filing that mentions an auditor AND a
# human role ("appointment of a director and of the secretarial auditor") is
# kept, because the part we are here for is really in it.
_AUDITOR = re.compile(
    r"\b(?:statutory|secretarial|internal|cost|branch|joint|tax)\s+auditors?\b", re.I)
_HUMAN_ROLE = re.compile(
    r"\b(?:director|chief|officer|manager|managing|chairman|chairperson|"
    r"secretary|president|ceo|cfo|cto|coo|whole[-\s]?time|kmp|"
    r"key managerial|senior management)\b", re.I)

# Some companies file the regulation's own name, or a covering note, where the
# description belongs. "Disclosure under Regulation 30of SEBI (LODR), 2015" and
# "Please see enclosed annexure" say nothing a reader cannot already see, and 62
# of 354 filings in the live window of 23 to 30 July 2026 read like that, so
# those rows carry the mandated CATEGORY as their description instead. The
# category is the part that was never freeform.
#
# The prefix alone is not enough to judge it, which is why `describe` also asks
# whether a human role is named: "Outcome of Board Meeting - Changes in Senior
# Management Personnel (SMP)" opens like a covering note and is genuinely more
# specific than its category, so it is kept as filed.
_BOILERPLATE = re.compile(
    r"^(?:disclosure|intimation|announcement|submission|pursuant to|"
    r"please (?:find|see|refer|note)|regulation|reg\.|letter|compliance|"
    r"as attached|enclosed|outcome of|sub\s*:|subject\s*:|ref\s*:)", re.I)
BOILERPLATE_LIMIT = 90

# The longest filed description that is still a headline. Some companies file a
# paragraph in this field; the full text always reaches `raw_text`, so nothing
# is lost by keeping the displayed line readable.
HEADLINE_LIMIT = 220

# The same string is quoted into raw_text, the headline and the summary, so a
# figure in the summary is always a figure present in the source text.
FILED_TEXT_LIMIT = 600


class BseError(RuntimeError):
    """A window could not be read, or came back implausibly empty."""


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Referer": REFERER, "Accept": "application/json"}


def days_from_env(default_days: int | None = None) -> int:
    """How many days back to read. Set by the workflow, so a backfill is a
    longer window through the same path rather than a script of its own."""
    raw = (os.environ.get("TIT_BSE_DAYS") or "").strip()
    if not raw:
        return default_days if default_days is not None else DEFAULT_DAYS
    if not re.fullmatch(r"\d{1,4}", raw):
        raise BseError(f"TIT_BSE_DAYS holds {raw!r}, which is not a number of days")
    days = int(raw)
    if days < 1:
        raise BseError("TIT_BSE_DAYS must be at least 1 day")
    return days


def window(days: int, *, today: datetime | None = None) -> tuple[str, str]:
    """The API's date window, in the YYYYMMDD form it insists on."""
    end = today or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def announcement_url(newsid: str) -> str | None:
    """The filing's own page on BSE. See the link-rot note in the docstring for
    why this is not the PDF."""
    clean = (newsid or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{16,64}", clean):
        return None
    return ANNOUNCEMENT_URL.format(newsid=clean)


def fetch_page(subcategory: str, start: str, end: str, page: int,
               *, timeout: int = 45, session=None) -> list[dict]:
    """One page of one sub-category. Returns the raw rows as BSE sends them."""
    params = {
        "pageno": page,
        "strCat": CATEGORY,
        "strPrevDate": start,
        "strScrip": "",
        "strSearch": "P",
        "strToDate": end,
        "strType": "C",
        "subcategory": subcategory,
    }
    get = (session or requests).get
    resp = get(API_URL, params=params, headers=_headers(), timeout=timeout)
    if resp.status_code != 200:
        raise BseError(
            f"{API_URL} returned {resp.status_code} for {subcategory!r}. BSE "
            f"answers 403 without a bseindia.com Referer and a browser "
            f"User-Agent; both are set in _headers().")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise BseError(
            f"{subcategory!r} did not return JSON: {resp.text[:160]!r}") from exc
    if "Table" not in payload:
        raise BseError(
            f"{subcategory!r} returned a payload with no 'Table' key "
            f"(keys: {sorted(payload)[:8]}). The response shape has changed.")
    return payload.get("Table") or []


def _squeeze(text: str) -> str:
    """Collapse the whitespace a filed description arrives with. BSE also
    doubles apostrophes on the way out of its database ('Shareholder''s')."""
    return re.sub(r"\s+", " ", (text or "").replace("''", "'")).strip()


def _date(value: str) -> str | None:
    """'2026-07-29T23:51:57.91' -> '2026-07-29'."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.split(".")[0]).date().isoformat()
    except ValueError:
        return None


def direction_for(subcategory: str, filed_text: str) -> str:
    """`hiring` only when the filing states an appointment that is not a
    re-appointment. Everything else, including every departure, is `neutral`.

    Never `displacement`: one director leaving is a change of leadership, not a
    workforce reduction, and workforce reductions are the sibling tracker's
    scope rather than ours.
    """
    if any(word in subcategory.lower() for word in _DEPARTURES):
        return "neutral"
    if _APPOINTED.search(filed_text) and not _RE_APPOINTED.search(filed_text):
        return "hiring"
    return "neutral"


def is_auditor_only(filed_text: str) -> bool:
    """Whether this filing appoints or replaces an audit FIRM and nothing else.
    See the `_AUDITOR` note: an auditor is not an employee."""
    return bool(_AUDITOR.search(filed_text)) and not _HUMAN_ROLE.search(filed_text)


def describe(filed_text: str, subcategory: str) -> str:
    """What the headline says after the employer's name.

    The company's own description, unless the company filed a covering note or
    the regulation's own name instead of a description, in which case the
    mandated category is both more informative and equally sourced. A note that
    names a role is still a description and is left alone.
    """
    if (len(filed_text) < BOILERPLATE_LIMIT
            and _BOILERPLATE.match(filed_text)
            and not _HUMAN_ROLE.search(filed_text)):
        return subcategory
    return filed_text[:HEADLINE_LIMIT]


def _row(entry: dict, subcategory: str) -> dict | None:
    company = _squeeze(entry.get("SLONGNAME") or "")
    filed = _squeeze(entry.get("HEADLINE") or "")[:FILED_TEXT_LIMIT]
    scrip = re.sub(r"\D", "", str(entry.get("SCRIP_CD") or ""))
    url = announcement_url(entry.get("NEWSID") or "")
    published = _date(entry.get("NEWS_DT") or "") or _date(entry.get("DT_TM") or "")

    # A filing with no description is a row we could only describe by its
    # category, which is not a signal about anybody. Declined rather than
    # padded out.
    if not (company and filed and scrip and url):
        return None
    if is_auditor_only(filed):
        return None

    headline = f"{company}: {describe(filed, subcategory)}"
    body = (
        f"{company} (BSE scrip code {scrip}) filed this disclosure with BSE "
        f"Ltd under Regulation 30 of the SEBI (Listing Obligations and "
        f"Disclosure Requirements) Regulations, 2015, in the category "
        f"\"{subcategory}\". The quoted text above is the description the "
        f"company itself filed with the announcement. Regulation 30 requires "
        f"every company listed in India to disclose a change in its directors "
        f"or key managerial personnel to the exchange, which is what makes "
        f"these comparable across employers rather than the ones an outlet "
        f"chose to write about."
    )

    return {
        # The filed description is QUOTED here, and the quotes are load-bearing.
        # `validate._NUMBER` ends with an optional magnitude suffix behind `\s*`,
        # and that `\s*` matches newlines: a date at the end of this line
        # followed by a blank line and a word starting with K was read as
        # "28.07.2026\n\nK" -> '28072026k', so the same date in the summary
        # looked invented and a good record was discarded. Closing the quote
        # puts a non-space character after the figure and the collision cannot
        # happen. The underlying regex still needs fixing in pipeline/validate.py.
        "raw_text": f"\"{filed}\"\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": "BSE India (SEBI Regulation 30 filing)",
        "discovery_url": url,
        "published_date": published,
        "company": company,
        "country": "India",
        # Listed on an Indian exchange, which is what the source IS. Not a guess.
        "employer_type": "public",
        "filed_text": filed,
        "subcategory": subcategory,
        "scrip_code": scrip,
        "newsid": (entry.get("NEWSID") or "").strip(),
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def collect(queries=None, *, days: int | None = None,
            subcategories: tuple = (), today: datetime | None = None,
            session=None) -> list[dict]:
    """Every leadership filing in the window, across the sub-category allowlist.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: there is nothing to search for, the mandated
    category IS the population.
    """
    wanted = tuple(subcategories) or SUBCATEGORIES
    start, end = window(days if days is not None else days_from_env(), today=today)
    out: list[dict] = []
    seen: set[str] = set()
    total = 0

    for subcategory in wanted:
        kept = 0
        for page in range(1, MAX_PAGES + 1):
            if session is None:
                import time
                time.sleep(REQUEST_DELAY)
            rows = fetch_page(subcategory, start, end, page, session=session)
            if not rows:
                break
            total += len(rows)
            for entry in rows:
                # BSE echoes the filter back on every row. If it ever stops
                # matching, the server-side filter has silently become a
                # no-op and we would be storing the whole announcement feed.
                echoed = _squeeze(entry.get("SUBCATNAME") or "")
                if echoed and echoed != subcategory:
                    raise BseError(
                        f"asked for subcategory {subcategory!r} and got "
                        f"{echoed!r}. The server-side filter is no longer "
                        f"filtering, so this run would store the whole feed.")
                item = _row(entry, subcategory)
                if not item or item["source_url"] in seen:
                    continue
                seen.add(item["source_url"])
                out.append(item)
                kept += 1
            if len(rows) < PAGE_SIZE:
                break
        print(f"[{COLLECTOR}] {subcategory}: {kept} filings")

    print(f"[{COLLECTOR}] {start}..{end}: {total} rows read, {len(out)} usable")
    if len(out) < MIN_ROWS_PER_WINDOW:
        raise BseError(
            f"{start}..{end} produced {len(out)} filings from {total} rows. "
            f"India files about 250 leadership disclosures a week, so this is "
            f"the category names having moved or the request being refused, "
            f"not a quiet week.")
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is read off the response or is a fixed editorial line, so
    nothing on the record can be something a model believed. The filed
    description is quoted, never paraphrased, which is also why every figure in
    the summary is present in `raw_text` by construction.
    """
    company = item["company"]
    filed = item["filed_text"]
    return {
        "company": company,
        "pillar": "leadership_change",
        "signal_direction": direction_for(item["subcategory"], filed),
        "headline": item["headline"],
        "summary": (
            f"{company} disclosed a leadership change to BSE under Regulation "
            f"30 of the SEBI (Listing Obligations and Disclosure "
            f"Requirements) Regulations, 2015, filed in the category "
            f"\"{item['subcategory']}\". The company's own description of the "
            f"filing reads: \"{filed}\""
        ),
        "talent_readthrough": (
            "An Indian listed company must tell the exchange when its "
            "directors or key managerial personnel change, so this is the one "
            "leadership feed for India that is complete rather than "
            "selective: it covers the small and mid-cap employers no outlet "
            "writes about, on the same basis as the large ones. Read the "
            "category as the fact and the description as the detail. A "
            "re-appointment is a board keeping someone, not a market hiring "
            "them, and this source distinguishes the two only as far as the "
            "filed wording allows."
        ),
        "country": "India",
        "employer_type": "public",
        # A statutory disclosure made to the exchange that collects it.
        # infer_confidence caps this at what the host is worth, so it lands at
        # 'verified' only while bseindia.com is a listed primary source domain.
        "confidence": "verified",
    }

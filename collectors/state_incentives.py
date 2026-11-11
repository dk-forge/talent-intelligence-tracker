"""State job-creation incentive awards: a PLANNED hiring signal.

A state grants money or tax credits to an employer in return for a promise to
create jobs. The promise is a planned-hiring signal. It is not a hire and it is
never a job posting, so every row says "planned hiring signal (state incentive
award)" and is stored with:

    site_event       'planned_hiring'   (pipeline/planned_hiring.py)
    headcount        the promised count, ONLY when the source states it exactly
    headcount_scope  'promised'
    money_basis      'state_funding'    (never summed as money raised)
    source_url       the release (TX) or the state's own dataset row (NY)

Rows are HIDDEN from every public output until PLANNED_HIRING_ENABLED is on.

SOURCES
=======

TX  Texas Enterprise Fund. The Governor announces each award in a press release
    on gov.texas.gov ("A Texas Enterprise Fund (TEF) grant of $3.5 million ...
    has been extended to LITEON"). The press-release listing pages at
    /news/category/press-release/P<offset> (8 posts a page) are walked, and a
    post counts only when BOTH hold: its title is "Governor Abbott Announces
    <Company> Expansion|Relocation|Headquarters ... In <Place>", and its body
    carries the award sentence. The title test is what keeps a later speech
    ("Delivers Remarks At Lilly Groundbreaking") from re-storing an old award.

NY  Empire State Development, Excelsior Jobs Program, from the state's
    Database of Economic Incentives (data.ny.gov dataset 26ei-n4eb). It states
    `job_creation_commitments_ftes` as a column and a `start_date` per award,
    refreshed quarterly. The older "Businesses Admitted" dataset (rerf-sf77)
    also has a commitment column but no date and stopped updating in 2023, so
    it is not used.

THE JOB COUNT IS EXPLICIT OR ABSENT
===================================

Only exact wording counts: "will create 500 jobs", "create 2,000 new jobs",
and the TEF-tied "for 448 of the new jobs to be created". Hedged wording
("more than 2,000", "nearly 500", "up to 3,000", "approximately", "hundreds
of") is NOT a count, and the award is stored with no headcount. The search is
confined to the paragraph carrying the award sentence, so a politician's quote
further down cannot supply a number the award paragraph hedged. For NY a zero
or blank commitment is no headcount.

EXCLUDED
========

Clawbacks and recaptures: a TX release that mentions recapture, clawback or
repayment, and every NY row that is Terminated or Closed, carries a termination
rationale, or has penalties applied.

Dormant: nothing schedules this collector. Run it by hand:

    python run_collect.py --source state_incentives --dry-run
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from pipeline import planned_hiring

COLLECTOR = "state_incentives"
USER_AGENT = ("Mozilla/5.0 (compatible; TalentIntel/1.0; "
              "+https://asktherecruiter.com)")
TIMEOUT = 30

TX_LISTING = "https://gov.texas.gov/news/category/press-release/P{offset}"
TX_PAGE_SIZE = 8
TX_SOURCE_NAME = "Office of the Texas Governor (Texas Enterprise Fund)"

NY_DATASET = "https://data.ny.gov/resource/26ei-n4eb.json"
NY_PROGRAM = "Excelsior Jobs Program"
NY_SOURCE_NAME = "Empire State Development (Database of Economic Incentives)"

DEFAULT_SINCE = "2026-01-01"
DEFAULT_TX_PAGES = 60

STATS: dict = {}

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
# "create 500 jobs", "create 2,000 new jobs", "create 500 high-quality jobs".
# The words between the number and "jobs" may not be a hedge or a number.
_CREATE = re.compile(
    r"\bcreat(?:e|es|ing)\s+" + _NUM + r"\s+(?:(?!jobs\b)[a-z][a-z-]*\s+){0,3}?jobs\b",
    re.I)
# "... has been extended to IEM for 448 of the new jobs to be created"
_TEF_FOR = re.compile(r"\bfor\s+" + _NUM + r"\s+of\s+the\s+new\s+jobs\b", re.I)
_HEDGE = re.compile(
    r"(?:more\s+than|over|nearly|almost|approximately|about|around|roughly|"
    r"up\s+to|at\s+least|as\s+many\s+as|upwards\s+of)\s*$", re.I)

_AWARD = re.compile(
    r"(?:Texas\s+Enterprise\s+Fund\s+\(TEF\)\s+grant|TEF\s+grant)\s+of\s+"
    r"(\$[\d.,]+(?:\s+(?:million|billion))?)", re.I)
_TITLE = re.compile(
    r"^Governor Abbott Announces\s+(?P<company>.+?)\s+"
    r"(?:Expansion|Relocation|Headquarters|Investment|Facility|Campus)"
    r"(?:\s+In\s+(?P<place>.+))?$")
_CLAWBACK = re.compile(r"\b(?:recaptur\w*|claw\s*-?\s*back\w*|repay\w*)\b", re.I)
_META_DATE = re.compile(r'<p class="meta">\s*([A-Z][a-z]+ \d{1,2}, \d{4})')


class StateIncentivesError(RuntimeError):
    pass


def explicit_jobs(text: str) -> int | None:
    """The job count the text states exactly, or None. Never a guess."""
    for rx in (_TEF_FOR, _CREATE):
        for m in rx.finditer(text or ""):
            if _HEDGE.search(text[:m.start(1)]):
                continue
            n = int(m.group(1).replace(",", ""))
            if n > 0:
                return n
    return None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def tx_listing_urls(page: str) -> list[str]:
    seen, out = set(), []
    for u in re.findall(r'href="(https://gov\.texas\.gov/news/post/[^"#?]+)"', page):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_tx_release(page: str, url: str) -> dict | None:
    """One TEF award off one release, or None when the post is not one."""
    h1 = re.search(r"<h1>(.*?)</h1>", page, re.S)
    if not h1:
        return None
    title = _text(h1.group(1))
    tm = _TITLE.match(title)
    if not tm:
        return None
    start = h1.end()
    end = page.find("</section>", start)
    body = page[start:end if end > 0 else None]
    paragraphs = [_text(p) for p in re.findall(r"<p>(.*?)</p>", body, re.S)]
    paragraphs = [p for p in paragraphs if p]
    award_para = next((p for p in paragraphs if _AWARD.search(p)), None)
    if award_para is None:
        return None
    full = " ".join(paragraphs)
    if _CLAWBACK.search(full):
        STATS["excluded_clawback"] = STATS.get("excluded_clawback", 0) + 1
        return None
    dm = _META_DATE.search(page)
    if not dm:
        return None
    published = datetime.strptime(dm.group(1), "%B %d, %Y").date().isoformat()
    place = (tm.group("place") or "").strip()
    city = "" if re.search(r"\bCounty$", place) else place
    company = tm.group("company").strip()
    jobs = explicit_jobs(award_para)
    grant = _AWARD.search(award_para).group(1)
    return {
        "raw_text": f"{title}\n\n" + "\n\n".join(paragraphs),
        "headline": title,
        "source_url": url,
        "discovery_url": url,
        "source_name": TX_SOURCE_NAME,
        "published_date": published,
        "company": company,
        "program": "Texas Enterprise Fund",
        "grant": grant,
        "jobs": jobs,
        "award_text": award_para,
        "city": city,
        "place": place,
        "state": "TX",
        "country": "US",
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _ny_excluded(row: dict) -> bool:
    status = (row.get("project_status") or "").strip().lower()
    if status != "active":
        return True
    if (row.get("rationale_for_termination") or "N/A").strip() not in ("", "N/A"):
        return True
    if (row.get("penalties_applied") or "N/A").strip().lower() in ("yes", "y"):
        return True
    try:
        return float(row.get("assistance_amount") or 0) < 0
    except ValueError:
        return False


def parse_ny_rows(rows: list[dict], *, since: str = DEFAULT_SINCE) -> list[dict]:
    out = []
    for row in rows:
        if (row.get("program_through_which_the_funding_was_awarded") or "") != NY_PROGRAM:
            continue
        published = (row.get("start_date") or "")[:10]
        if not published or published < since:
            continue
        if _ny_excluded(row):
            STATS["excluded_clawback"] = STATS.get("excluded_clawback", 0) + 1
            continue
        name = (row.get("recipient_name") or "").strip()
        pid = (row.get("project_id_number") or "").strip()
        if not name or not pid:
            continue
        try:
            jobs = int(float((row.get("job_creation_commitments_ftes") or "0")
                             .replace(",", "")))
        except ValueError:
            jobs = 0
        jobs = jobs if jobs > 0 else None
        amount = row.get("assistance_amount") or row.get("total_lead_agency_benefits_awarded")
        try:
            amount_txt = f"${int(float(amount)):,}"
        except (TypeError, ValueError):
            amount_txt = ""
        project = (row.get("name_of_project") or "").strip()
        region = (row.get("economic_development_region") or "").strip()
        headline = f"{name} awarded Excelsior Jobs Program tax credits" + (
            f" for {project}" if project else "")
        parts = [f"Empire State Development awarded {name}"
                 + (f" up to {amount_txt} in" if amount_txt else "")
                 + " Excelsior Jobs Program tax credits"
                 + (f" for {project}" if project else "")
                 + (f" ({region})" if region else "") + "."]
        if jobs is not None:
            parts.append(f"Job creation commitment: {jobs} FTE "
                         f"(job_creation_commitments_ftes = {jobs}).")
        else:
            parts.append("The award states no job creation commitment.")
        url = f"{NY_DATASET}?project_id_number={quote(pid, safe='')}"
        out.append({
            "raw_text": f"{headline}\n\n" + " ".join(parts),
            "headline": headline,
            "source_url": url,
            "discovery_url": url,
            "source_name": NY_SOURCE_NAME,
            "published_date": published,
            "company": name,
            "program": NY_PROGRAM,
            "grant": amount_txt,
            "jobs": jobs,
            "award_text": " ".join(parts),
            "city": "",
            "place": region,
            "state": "NY",
            "country": "US",
            "industry_hint": row.get("industry") or "",
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def _get(session, url, **kw):
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **kw)
    resp.raise_for_status()
    return resp


def collect_tx(session, *, pages: int = DEFAULT_TX_PAGES,
               since: str = DEFAULT_SINCE) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for i in range(pages):
        urls = tx_listing_urls(_get(session, TX_LISTING.format(offset=i * TX_PAGE_SIZE)).text)
        fresh = [u for u in urls if u not in seen]
        if not fresh:
            break
        for url in fresh:
            seen.add(url)
            try:
                item = parse_tx_release(_get(session, url).text, url)
            except requests.RequestException as exc:
                print(f"[{COLLECTOR}] TX fetch failed {url}: {exc}")
                continue
            if item and item["published_date"] >= since:
                out.append(item)
    STATS["tx_posts_read"] = len(seen)
    return out


def collect_ny(session, *, since: str = DEFAULT_SINCE) -> list[dict]:
    resp = _get(session, NY_DATASET, params={
        "$where": (f"program_through_which_the_funding_was_awarded='{NY_PROGRAM}' "
                   f"AND start_date >= '{since}'"),
        "$limit": "5000",
    })
    return parse_ny_rows(resp.json(), since=since)


def collect(queries=None, *, since: str = DEFAULT_SINCE,
            tx_pages: int = DEFAULT_TX_PAGES) -> list[dict]:
    """Every TX and NY award since `since`. `queries` is accepted and ignored."""
    STATS.clear()
    session = requests.Session()
    tx = collect_tx(session, pages=tx_pages, since=since)
    ny = collect_ny(session, since=since)
    STATS.update(tx_awards=len(tx), ny_awards=len(ny))
    print(f"[{COLLECTOR}] TX {len(tx)} award(s) from {STATS.get('tx_posts_read', 0)} "
          f"post(s); NY {len(ny)} award(s); "
          f"{STATS.get('excluded_clawback', 0)} clawback/terminated excluded")
    return tx + ny


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated."""
    jobs = item.get("jobs")
    company = item["company"]
    promise = (f"promised {jobs} new jobs" if jobs is not None
               else "no exact job count stated")
    summary = (f"{planned_hiring.LABEL[0].upper()}{planned_hiring.LABEL[1:]}: "
               f"{item['program']} award to {company}"
               + (f" of {item['grant']}" if item.get("grant") else "")
               + f", {promise}. {item['award_text']}")
    return {
        "company": company,
        "pillar": "company_development",
        "signal_direction": "hiring",
        "headline": item["headline"],
        "summary": summary[:1000],
        "talent_readthrough": (
            f"This is a {planned_hiring.LABEL}, a commitment made in return "
            "for public money, not a hire and not an opening anyone can apply "
            "to. Awards are paid against jobs actually created over several "
            "years, so the promised count is a ceiling on near-term hiring "
            "rather than a forecast of it."),
        "city": item.get("city") or "",
        "state": item.get("state") or "",
        "country": item.get("country") or "US",
        "headcount": jobs,
        "funding_amount": item.get("grant") or "",
        "confidence": "verified",
    }

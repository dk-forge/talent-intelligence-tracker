"""Applicant-tracking system job boards — hiring momentum, measured daily.

Greenhouse, Lever, Ashby and Workable all publish an employer's open roles as
keyless JSON. Three facts about that make this collector worth having and shape
every decision in it:

1. **There is no history anywhere.** The Wayback Machine holds no snapshots of
   these API endpoints, and none of the three exposes a "closed on" date. A day
   that nobody records is gone permanently. That is why this ships even though
   it contributes nothing to a backfill: the archive starts the day it runs.
2. **The signal is the DIFF, not the listing.** One vacancy is a job advert,
   and `validate._JOB_POSTING_PATH` exists because storing adverts would make
   this a bad job board instead of a signal tracker. What is market
   intelligence is "this employer opened 40 more roles in Dublin this
   fortnight", which is a fact about the employer, derived from counting.
3. **No model is involved.** Titles and locations are fields. There is no LLM
   cost at all.

Two files hold the state this needs:

    collectors/ats_watchlist.json   which employers, and on which ATS
    data/ats_board_state.json       the daily count series, committed to git

The state file is the archive. It records every run's counts per employer, per
place and per function, whether or not a signal is emitted, so the series
survives even when nothing was material enough to publish. The signal
threshold and the recording are deliberately separate: recording is free and
irreversible if skipped, publishing is not.

Emission is throttled on purpose. The pipeline's own fuzzy dedup treats the
same employer and pillar inside fourteen days as one event, so a daily "opened
3 roles" row could not be stored anyway. A signal is emitted when the board has
moved materially since the LAST EMITTED baseline, which makes each row a real
fortnightly-or-slower movement rather than sampling noise.

Growth only. A shrinking board is not a layoff — roles come down when they are
filled as often as when they are cancelled — and layoffs are the sibling
tracker's job, never collected here.

Confidence is `reported`, chosen honestly and not by accident. The board is the
employer's own publication, but the COUNT is our measurement of it on two dates,
not a number the employer published. A derived measurement does not earn the
tier that a filed document does.

**robots.txt decides which ATS we may read at all**, checked in code with the
press collector's function rather than audited once and forgotten:

    greenhouse       boards-api.greenhouse.io  Disallow: /embed/ only  -> allowed
    lever            api.lever.co              Allow: / , Crawl-delay: 1 -> allowed,
                                               and that delay is honoured below
    workable         apply.workable.com        Disallow: (empty)       -> allowed
                                               by robots, but it rate-limits
                                               hard (Cloudflare 1015) and no
                                               payload with postings in it has
                                               been captured yet, so the parser
                                               below ships with NO board on the
                                               watchlist. See its `withdrawn`
                                               entry for what would change that.
    ashby            api.ashbyhq.com           robots.txt answers 401; a 4xx is
                                               "no robots.txt" under RFC 9309, so
                                               the posting API Ashby documents as
                                               public is read. Fails open, and
                                               says so rather than pretending it
                                               was an explicit yes.
    smartrecruiters  api.smartrecruiters.com   Disallow: / for every agent but
                                               LinkedInBot -> NOT READ. The five
                                               employers we had on it are
                                               withdrawn in the watchlist, with
                                               the reason recorded there. The
                                               parsing stays because the terms
                                               may change; the request does not
                                               happen while they say no.

A board the gate refuses is reported as `robots`, and is NOT counted toward the
breakage tolerance below: a publisher's terms are a decision, not an outage.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from pipeline import vocab
# ONE robots.txt implementation in this repo. The press collector's is the
# original and is tested there; a second one would drift and the drift would be
# invisible until a publisher's terms were quietly ignored. Imported rather than
# copied for exactly that reason.
from collectors.national_press import robots_allows

COLLECTOR = "ats_boards"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"
REQUEST_DELAY = 0.2
# Per-ATS politeness. Lever's is not a preference of ours: its robots.txt says
# `Crawl-delay: 1`, so the number belongs to Lever and the comment says whose it
# is, because a bare 1.1 would be edited away by the next person tidying up.
# Workable's is ours, and it is measured: apply.workable.com starts answering
# 429 after a few dozen quick requests, so a fast sweep of many accounts breaks
# itself. One request a second is well under where it complained.
ATS_DELAY = {"lever": 1.1, "workable": 1.0}
TIMEOUT = 45

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = Path(os.environ.get("TIT_ATS_WATCHLIST")
                      or REPO_ROOT / "collectors" / "ats_watchlist.json")
STATE_PATH = Path(os.environ.get("TIT_ATS_STATE")
                  or REPO_ROOT / "data" / "ats_board_state.json")

# The source_url is the employer's board page and is the same on every run, so
# run_collect must not mark it seen — one hiring signal would otherwise be the
# last this collector ever produced. Dedup is content_hash and fuzzy matching,
# which is where it belongs.
REVISITS_ITS_SOURCE_URL = True

# This collector keeps state between runs, so a dry run that wrote it would
# advance the baseline without storing the row and lose the movement for good.
# run_collect passes the flag through only to collectors that declare this.
ACCEPTS_DRY_RUN = True

# What the last collect() actually did. This exists because health is measured
# in `items_found`, and for a DIFF-shaped source the number of emitted rows is
# the wrong quantity: a day on which sixty boards were read and none of them
# moved materially is a healthy day, not a dead collector. run_collect reads
# `read` — boards successfully counted — and reports that instead. A run that
# reads nothing is still, correctly, degraded.
LAST_RUN = {"boards": 0, "read": 0, "robots_blocked": 0, "failed": 0,
            "movements": 0}

BOARD_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
}
API_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
}
SOURCE_NAMES = {
    "greenhouse": "Greenhouse job board",
    "ashby": "Ashby job board",
    "lever": "Lever job board",
    "workable": "Workable job board",
    "smartrecruiters": "SmartRecruiters careers site",
}

# SmartRecruiters pages at 100. Bosch alone runs to nearly fifty pages, so the
# cap is generous but finite: an unbounded loop against someone else's API is
# how a daily job becomes a daily incident.
SR_PAGE_SIZE = 100
SR_MAX_PAGES = 60

# What counts as movement worth publishing.
MIN_DELTA = 10              # net new postings since the last emitted baseline
MIN_RELATIVE_DELTA = 0.15   # or this much growth, if at least MIN_RELATIVE_ABS
MIN_RELATIVE_ABS = 5
MIN_PLACE_DELTA = 3         # a place is named only if it moved by itself

# A posted salary band is only re-published when the midpoint moves this much,
# so the pay row says "the range changed" rather than "we looked again".
MIN_BAND_MOVE = 0.02

# How many daily observations to keep per board. Roughly two years.
HISTORY_LIMIT = 800

# More than this share of the watchlist failing means the run is broken, not
# that a few employers closed their boards.
MAX_FAILURE_RATE = 0.34


class BoardError(RuntimeError):
    """The watchlist could not be read, or too much of it failed at once."""


# --- Title -> function -----------------------------------------------------
#
# Ordered longest phrase first so "data engineer" resolves to data_ai rather
# than to engineering. A title that matches nothing counts toward the total and
# toward its place, and simply carries no function: an unlabelled role is
# better than an invented label.
_TITLE_FUNCTION = (
    ("machine learning", "data_ai"), ("data scientist", "data_ai"),
    ("data engineer", "data_ai"), ("data analyst", "data_ai"),
    ("research scientist", "research"), ("research engineer", "data_ai"),
    ("applied ai", "data_ai"), ("analytics", "data_ai"),
    ("site reliability", "it_infrastructure"), ("devops", "it_infrastructure"),
    ("infrastructure", "it_infrastructure"), ("security", "it_infrastructure"),
    ("platform engineer", "it_infrastructure"), ("network", "it_infrastructure"),
    ("it support", "it_infrastructure"),
    ("product manager", "product"), ("product management", "product"),
    ("product designer", "design"), ("designer", "design"), ("ux", "design"),
    ("software engineer", "engineering"), ("engineer", "engineering"),
    ("developer", "engineering"), ("engineering manager", "engineering"),
    ("account executive", "sales"), ("account manager", "sales"),
    ("sales", "sales"), ("business development", "sales"),
    ("partnerships", "sales"), ("solutions consultant", "sales"),
    ("marketing", "marketing"), ("brand", "marketing"),
    ("communications", "marketing"), ("content", "marketing"),
    ("customer success", "customer_support"), ("customer support", "customer_support"),
    ("support specialist", "customer_support"), ("technical support", "customer_support"),
    ("recruiter", "hr_people"), ("recruiting", "hr_people"),
    ("talent acquisition", "hr_people"), ("people operations", "hr_people"),
    ("human resources", "hr_people"), ("compensation", "hr_people"),
    ("accountant", "finance"), ("accounting", "finance"), ("finance", "finance"),
    ("controller", "finance"), ("treasury", "finance"), ("fp&a", "finance"),
    ("counsel", "legal_compliance"), ("legal", "legal_compliance"),
    ("compliance", "legal_compliance"), ("risk", "legal_compliance"),
    ("privacy", "legal_compliance"),
    ("supply chain", "supply_chain"), ("procurement", "supply_chain"),
    ("logistics", "supply_chain"), ("warehouse", "supply_chain"),
    ("manufacturing", "manufacturing"), ("production", "manufacturing"),
    ("clinical", "clinical_healthcare"), ("nurse", "clinical_healthcare"),
    ("physician", "clinical_healthcare"), ("medical", "clinical_healthcare"),
    ("chief ", "executive"), ("vice president", "executive"),
    ("head of", "executive"),
    ("operations", "operations"), ("program manager", "operations"),
    ("project manager", "operations"),
)


def function_for_title(title: str) -> str | None:
    text = (title or "").lower()
    for phrase, function in _TITLE_FUNCTION:
        if phrase in text:
            return function
    return None


# --- Location -> a place key ----------------------------------------------
#
# Keys are prefixed so the label and the classified fields can be rebuilt from
# the key alone, and so a city and a country of the same name cannot collide.

_SPLIT = re.compile(r"\s*(?:[,;|/()]|\s-\s|\bor\b)\s*", re.I)


def place_key(*candidates: str) -> str:
    """The most specific place these strings agree on: a curated city, else a
    country, else remote, else nothing. Never invents a location.

    Parentheses are separators, not decoration: "Remote (Sweden)" is a Swedish
    role and filing it under a generic "remote" bucket would throw away the one
    piece of geography the posting actually stated.
    """
    parts: list[str] = []
    for candidate in candidates:
        for token in _SPLIT.split(str(candidate or "")):
            token = token.strip()
            if token:
                parts.append(token)

    for token in parts:
        if vocab.normalize_city(token):
            return f"city:{vocab.normalize_city(token)[0]}"
    for token in parts:
        code = vocab.normalize_country(token)
        if code:
            return f"country:{code}"
    if any(re.search(r"\bremote\b", token, re.I) for token in parts):
        return "remote:"
    return ""


def place_label(key: str) -> str:
    kind, _, value = (key or "").partition(":")
    if kind == "city":
        return value
    if kind == "country":
        return vocab.COUNTRY_NAMES.get(value, value)
    if kind == "remote":
        return "remote roles"
    return ""


# --- Fetching --------------------------------------------------------------


def _get(url: str, *, timeout: int = TIMEOUT, delay: float = REQUEST_DELAY):
    time.sleep(delay)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT,
                                      "Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def board_allowed(entry: dict) -> bool:
    """Whether this employer's ATS lets us read the board endpoint.

    One robots.txt implementation in the repo (see the import at the top), one
    lookup per ATS host per run — the parser is cached inside it.
    """
    ats = entry.get("ats")
    if ats not in API_URLS:
        return True   # an unknown ATS is a watchlist error, raised in fetch
    return robots_allows(API_URLS[ats].format(slug=entry.get("slug", "example")))


def _salary(job: dict) -> tuple[int, int] | None:
    """An Ashby posted annual base-salary band in USD, as (min, max).

    Only USD annual salary components are read. Mixing currencies into one
    median would produce a number that describes nothing, and converting them
    would be a guessed exchange rate on a pay figure.
    """
    comp = job.get("compensation") or {}
    for component in comp.get("summaryComponents") or []:
        if (component.get("compensationType") != "Salary"
                or component.get("interval") != "1 YEAR"
                or component.get("currencyCode") != "USD"):
            continue
        low, high = component.get("minValue"), component.get("maxValue")
        try:
            low, high = int(low), int(high)
        except (TypeError, ValueError):
            continue
        if 0 < low <= high:
            return low, high
    return None


def _lever_salary(job: dict) -> tuple[int, int] | None:
    """A Lever posted annual base-salary band in USD, as (min, max).

    Same rule as Ashby's: annual, USD, nothing converted. Lever spells the
    interval `per-year-salary`.
    """
    band = job.get("salaryRange") or {}
    if band.get("currency") != "USD" or band.get("interval") != "per-year-salary":
        return None
    try:
        low, high = int(band.get("min")), int(band.get("max"))
    except (TypeError, ValueError):
        return None
    return (low, high) if 0 < low <= high else None


def fetch_postings(entry: dict) -> list[dict]:
    """One employer's open roles, normalised to {place, function, salary}."""
    ats, slug = entry["ats"], entry["slug"]
    delay = ATS_DELAY.get(ats, REQUEST_DELAY)
    out: list[dict] = []

    if ats == "greenhouse":
        for job in _get(API_URLS[ats].format(slug=slug), delay=delay).get("jobs") or []:
            location = ((job.get("location") or {}).get("name") or "")
            out.append({
                "place": place_key(location),
                "function": function_for_title(job.get("title", "")),
                "salary": None,
            })

    elif ats == "lever":
        # Lever is the one API here that tells a missing board apart from an
        # empty one: an unknown slug answers {"ok": false}, not [].
        payload = _get(API_URLS[ats].format(slug=slug), delay=delay)
        if isinstance(payload, dict):
            raise BoardError(f"lever:{slug} is not a board: "
                             f"{str(payload.get('error') or payload)[:80]}")
        for job in payload or []:
            categories = job.get("categories") or {}
            locations = categories.get("allLocations") or [categories.get("location") or ""]
            out.append({
                "place": place_key(*locations, job.get("country") or ""),
                "function": (vocab.normalize_function(categories.get("team", ""))
                             or vocab.normalize_function(categories.get("department", ""))
                             or function_for_title(job.get("text", ""))),
                "salary": _lever_salary(job),
            })

    elif ats == "workable":
        payload = _get(API_URLS[ats].format(slug=slug), delay=delay)
        for job in (payload or {}).get("jobs") or []:
            location = job.get("location") or {}
            out.append({
                "place": place_key(location.get("city", ""),
                                   location.get("region", ""),
                                   location.get("country", ""),
                                   location.get("location_str", "")),
                "function": (vocab.normalize_function(job.get("department", ""))
                             or function_for_title(job.get("title", ""))),
                # Workable's public widget publishes no pay band at all, so
                # there is nothing to read rather than something to guess.
                "salary": None,
            })

    elif ats == "ashby":
        for job in _get(API_URLS[ats].format(slug=slug), delay=delay).get("jobs") or []:
            if job.get("isListed") is False:
                continue
            postal = ((job.get("address") or {}).get("postalAddress") or {})
            out.append({
                "place": place_key(postal.get("addressLocality", ""),
                                   postal.get("addressRegion", ""),
                                   job.get("location", ""),
                                   postal.get("addressCountry", "")),
                "function": (vocab.normalize_function(job.get("department", ""))
                             or function_for_title(job.get("title", ""))),
                "salary": _salary(job),
            })

    elif ats == "smartrecruiters":
        for page in range(SR_MAX_PAGES):
            url = (f"{API_URLS[ats].format(slug=slug)}"
                   f"?limit={SR_PAGE_SIZE}&offset={page * SR_PAGE_SIZE}")
            payload = _get(url, delay=delay)
            content = payload.get("content") or []
            for job in content:
                location = job.get("location") or {}
                out.append({
                    "place": place_key(location.get("city", ""),
                                       location.get("country", "")),
                    "function": (vocab.normalize_function(
                        (job.get("function") or {}).get("label", ""))
                        or function_for_title(job.get("name", ""))),
                    "salary": None,
                })
            if len(content) < SR_PAGE_SIZE:
                break
    else:
        raise BoardError(f"unknown ATS {ats!r} for {slug!r} in {WATCHLIST_PATH}")

    return out


def snapshot(postings: list[dict]) -> dict:
    """Counts only. The individual adverts are deliberately not kept."""
    places: dict[str, int] = {}
    functions: dict[str, int] = {}
    lows: list[int] = []
    highs: list[int] = []
    paid_places: dict[str, int] = {}
    for job in postings:
        if job.get("place"):
            places[job["place"]] = places.get(job["place"], 0) + 1
        if job.get("function"):
            functions[job["function"]] = functions.get(job["function"], 0) + 1
        if job.get("salary"):
            lows.append(job["salary"][0])
            highs.append(job["salary"][1])
            if job.get("place"):
                paid_places[job["place"]] = paid_places.get(job["place"], 0) + 1

    snap = {"total": len(postings), "places": places, "functions": functions}
    if lows:
        snap["salary"] = {
            "listed": len(lows),
            "median_min": _median(lows),
            "median_max": _median(highs),
            # Where the roles carrying that band actually are, and only when
            # they mostly agree. A median band across three continents belongs
            # to no country, and saying otherwise would put a US salary on a
            # city because most of the employer's OTHER roles are there.
            "place": _dominant(paid_places, len(lows)),
        }
    return snap


# A place has to carry most of the priced roles before the band is attributed
# to it. Below this the band is stored with no location at all.
DOMINANT_SHARE = 0.6


def _dominant(counts: dict[str, int], total: int) -> str:
    if not counts or total <= 0:
        return ""
    key, count = max(counts.items(), key=lambda kv: kv[1])
    return key if count / total >= DOMINANT_SHARE else ""


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


# --- State -----------------------------------------------------------------


def load_watchlist(path: Path | None = None) -> list[dict]:
    target = Path(path or WATCHLIST_PATH)
    try:
        payload = json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        raise BoardError(f"{target} could not be read: {exc}") from exc
    boards = [b for b in payload.get("boards") or [] if b.get("slug") and b.get("ats")]
    if not boards:
        raise BoardError(f"{target} lists no boards")
    return boards


def load_state(path: Path | None = None) -> dict:
    target = Path(path or STATE_PATH)
    if not target.exists():
        return {"version": 1, "boards": {}}
    try:
        state = json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        # Refusing is right: an unreadable state file means every board looks
        # brand new, every baseline resets, and the series is silently lost.
        raise BoardError(f"{target} is unreadable: {exc}") from exc
    state.setdefault("boards", {})
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    target = Path(path or STATE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


# --- Trajectory ------------------------------------------------------------
#
# The rule, written down so it can be argued with, and so the page can state it
# rather than imply it. Given the recorded daily counts for one board:
#
#   unknown   fewer than MIN_OBSERVATIONS readings in the window, or the first
#             and last of them less than MIN_SPAN_DAYS apart. This is the
#             DEFAULT, and it is a real answer: two readings a day apart say
#             nothing about an employer's hiring.
#   rising    the latest count is above the earliest in the window by at least
#             MIN_DELTA roles AND at least MIN_RELATIVE of it. Evidence of
#             hiring: an employer does not advertise roles it does not intend
#             to fill.
#   falling   the same test downward. NOT evidence of cuts, and never rendered
#             as any. A role leaves a board when it is filled, when it is
#             withdrawn, when it is reposted under a new id, and when somebody
#             tidies up a board that had gone stale. All four look identical
#             from outside, which is exactly why this tracker will not name one.
#   flat      moved by less than that. A board that is holding its level.
#
# Both a floor and a percentage, because either alone lies at one end of the
# range: 5 roles is noise at Bosch and a third of a startup's board.
TRAJECTORY_WINDOW_DAYS = 30
TRAJECTORY_MIN_OBSERVATIONS = 4
TRAJECTORY_MIN_SPAN_DAYS = 14
TRAJECTORY_MIN_DELTA = 5
TRAJECTORY_MIN_RELATIVE = 0.10

FALLING_CAVEAT = ("A board that shrinks is not evidence of job cuts: roles "
                  "leave a board when they are filled, withdrawn or reposted.")


def trajectory(history: list[dict], *, today: str | None = None,
               window_days: int = TRAJECTORY_WINDOW_DAYS) -> dict:
    """Direction of a board's volume over the window, or 'unknown'.

    Pure: same history in, same verdict out, no clock and no network unless
    `today` is left to default. `basis` carries the numbers the verdict was
    reached on, so nothing downstream has to restate the rule from memory.
    """
    end = today or datetime.now(timezone.utc).date().isoformat()
    try:
        cutoff = (datetime.strptime(end, "%Y-%m-%d")
                  - timedelta(days=window_days)).date().isoformat()
    except ValueError:
        return {"direction": "unknown", "basis": "the window could not be read",
                "window_days": window_days, "observations": 0}

    points = sorted((h for h in history or []
                     if h.get("date") and h["date"] >= cutoff and h["date"] <= end),
                    key=lambda h: h["date"])
    observations = len(points)
    if observations < TRAJECTORY_MIN_OBSERVATIONS:
        return {"direction": "unknown", "window_days": window_days,
                "observations": observations,
                "basis": (f"{observations} "
                          f"{'reading' if observations == 1 else 'readings'} in "
                          f"{window_days} days: too few to say anything (we "
                          f"need {TRAJECTORY_MIN_OBSERVATIONS})")}

    first, last = points[0], points[-1]
    span = ((datetime.strptime(last["date"], "%Y-%m-%d")
             - datetime.strptime(first["date"], "%Y-%m-%d")).days)
    if span < TRAJECTORY_MIN_SPAN_DAYS:
        return {"direction": "unknown", "window_days": window_days,
                "observations": observations,
                "basis": (f"{observations} readings spanning {span} days: too "
                          f"short a period to call a direction (we need "
                          f"{TRAJECTORY_MIN_SPAN_DAYS} days)")}

    was, now = int(first.get("total") or 0), int(last.get("total") or 0)
    delta = now - was
    relative = abs(delta) / was if was > 0 else 0.0
    material = abs(delta) >= TRAJECTORY_MIN_DELTA and relative >= TRAJECTORY_MIN_RELATIVE

    if not material:
        direction = "flat"
        basis = (f"{now} open roles on {last['date']} against {was} on "
                 f"{first['date']}: a move of {delta:+d} across {observations} "
                 f"readings, below the {TRAJECTORY_MIN_DELTA}-role and "
                 f"{TRAJECTORY_MIN_RELATIVE:.0%} floors this tracker calls a change")
    else:
        direction = "rising" if delta > 0 else "falling"
        basis = (f"{now} open roles on {last['date']} against {was} on "
                 f"{first['date']}: {delta:+d} ({relative:.0%}) across "
                 f"{observations} readings over {span} days")
        if direction == "falling":
            basis += ". " + FALLING_CAVEAT

    return {"direction": direction, "basis": basis, "window_days": window_days,
            "observations": observations, "from": dict(first), "to": dict(last),
            "delta": delta}


def _material(delta: int, base_total: int) -> bool:
    if delta >= MIN_DELTA:
        return True
    return (delta >= MIN_RELATIVE_ABS and base_total > 0
            and delta / base_total >= MIN_RELATIVE_DELTA)


def _top_place(current: dict, baseline: dict) -> tuple[str, int]:
    best_key, best_delta = "", 0
    for key, count in (current.get("places") or {}).items():
        delta = count - (baseline.get("places") or {}).get(key, 0)
        if delta > best_delta:
            best_key, best_delta = key, delta
    return best_key, best_delta


def _grown_functions(current: dict, baseline: dict, limit: int = 3) -> list[str]:
    grown = []
    for key, count in (current.get("functions") or {}).items():
        delta = count - (baseline.get("functions") or {}).get(key, 0)
        if delta > 0:
            grown.append((delta, key))
    grown.sort(reverse=True)
    return [key for _delta, key in grown[:limit]]


# --- Signals ---------------------------------------------------------------


def _hiring_item(entry: dict, current: dict, baseline: dict, today: str) -> dict | None:
    delta = current["total"] - baseline["total"]
    if delta <= 0 or not _material(delta, baseline["total"]):
        return None

    company = entry["company"]
    url = BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
    since = baseline.get("date") or "the previous observation"
    key, place_delta = _top_place(current, baseline)
    label = place_label(key) if place_delta >= MIN_PLACE_DELTA else ""
    functions = _grown_functions(current, baseline)

    where = f" in {label}" if label else ""
    headline = (f"{company} opened {delta} more roles{where} "
                f"(job board: {baseline['total']} to {current['total']})")

    body = (
        f"{company}'s own job board listed {current['total']} open roles on "
        f"{today}, against {baseline['total']} when it was last recorded on "
        f"{since}: a net increase of {delta}. "
    )
    if label:
        body += (f"The largest single movement was {label}, up {place_delta}. ")
    if functions:
        body += ("Roles grew in " + ", ".join(
            vocab.FUNCTION_LABELS.get(f, f) for f in functions) + ". ")
    body += (
        "The count is a measurement of the employer's published board on two "
        "dates, not a hiring plan the employer announced, and a role leaving "
        "the board may have been filled or withdrawn."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAMES[entry["ats"]],
        "discovery_url": url,
        "published_date": today,
        "company": company,
        "kind": "hiring",
        "delta": delta,
        "place_key": key if label else "",
        "functions": functions,
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _pay_item(entry: dict, current: dict, previous: dict | None, today: str) -> dict | None:
    """Ashby publishes posted pay ranges through the same free endpoint. A
    median of what an employer is ADVERTISING is a pay-pillar fact a job seeker
    cannot get anywhere else, and it is not the same thing as what it pays."""
    salary = current.get("salary")
    if not salary or salary["listed"] < 5:
        return None

    old = (previous or {}).get("salary")
    if old:
        was = (old["median_min"] + old["median_max"]) / 2
        now = (salary["median_min"] + salary["median_max"]) / 2
        if was > 0 and abs(now - was) / was < MIN_BAND_MOVE:
            return None

    company = entry["company"]
    url = BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
    low = f"${salary['median_min']:,}"
    high = f"${salary['median_max']:,}"
    listed = salary["listed"]

    where = place_label(salary.get("place") or "")
    headline = (f"{company} advertises a median posted salary band of "
                f"{low} to {high} across {listed} open roles")
    body = (
        f"Of the roles {company} had open on {today}, {listed} published an "
        f"annual base salary range in US dollars. The median of those ranges "
        f"runs from {low} to {high}. "
    )
    if where:
        body += f"Most of the priced roles are in {where}. "
    body += (
        "The figure is the midpoint of what the employer is advertising, not "
        "what it pays: an advertised band is a recruiting position, and an "
        "individual offer can sit anywhere in it or outside it."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAMES[entry["ats"]],
        "discovery_url": url,
        "published_date": today,
        "company": company,
        "kind": "pay",
        "listed": listed,
        "band_low": low,
        "band_high": high,
        "place_key": salary.get("place") or "",
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def collect(queries=None, *, dry_run: bool = False,
            watchlist: list[dict] | None = None,
            state: dict | None = None, today: str | None = None,
            persist: bool | None = None) -> list[dict]:
    """Fetch every board, record the day, and emit the movements.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect. `dry_run` leaves the state file untouched, so a
    rehearsal cannot consume the movement it is rehearsing.
    """
    boards = watchlist if watchlist is not None else load_watchlist()
    store = state if state is not None else load_state()
    day = today or datetime.now(timezone.utc).date().isoformat()

    out: list[dict] = []
    failures: list[str] = []
    blocked: list[str] = []
    read = 0

    for entry in boards:
        board_id = f"{entry['ats']}:{entry['slug']}"
        record = store["boards"].setdefault(board_id, {})
        record["company"] = entry.get("company") or entry["slug"]
        # Written on every run so an old state file gains them without a
        # migration. company_key is the join to a company profile page, and the
        # url is the source that makes the claim: a series with neither is a
        # number nobody can attribute or place.
        record["ats"] = entry["ats"]
        record["slug"] = entry["slug"]
        record["company_key"] = vocab.company_key(record["company"])
        record["url"] = BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
        record["source_name"] = SOURCE_NAMES[entry["ats"]]

        if not board_allowed(entry):
            # Their terms, not our outage. Deliberately outside `failures`: a
            # robots-blocked ATS must never look like a broken scraper, and a
            # broken scraper must never be excused as a robots block.
            blocked.append(board_id)
            record["status"] = "robots"
            continue
        record["status"] = "ok"

        try:
            postings = fetch_postings(entry)
        except (requests.RequestException, ValueError, BoardError) as exc:
            # BoardError included on purpose: Lever answers a missing slug with
            # an error object, and one dead slug is one dead board, not a dead
            # run. The tolerance below still catches it if it spreads.
            failures.append(f"{board_id}: {type(exc).__name__} {exc}")
            continue

        current = snapshot(postings)
        # Greenhouse, Ashby, Workable and SmartRecruiters all answer 200 with an
        # empty list for a slug that does not exist, so an employer that HAD
        # roles and now has none is a renamed slug far more often than an
        # employer that stopped hiring entirely. (Lever is the exception and is
        # caught above, with its own error.)
        if current["total"] == 0:
            failures.append(f"{board_id}: returned zero postings")
            continue
        read += 1

        previous = record.get("last")
        baseline = record.get("baseline")

        item = _hiring_item(entry, current, baseline, day) if baseline else None
        if item:
            out.append(item)
            record["baseline"] = dict(current, date=day)
        elif not baseline:
            # The first sighting is the baseline, not a signal. There is no
            # diff yet, and publishing the level would be publishing a listing.
            record["baseline"] = dict(current, date=day)

        pay = _pay_item(entry, current, previous, day)
        if pay:
            out.append(pay)

        record["last"] = dict(current, date=day)
        history = record.setdefault("history", [])
        if not history or history[-1].get("date") != day:
            history.append({"date": day, "total": current["total"]})
        else:
            history[-1] = {"date": day, "total": current["total"]}
        del history[:-HISTORY_LIMIT]
        # Recomputed from the series every run rather than stored once, so a
        # rule change reaches every board on the next run and never leaves two
        # generations of verdict in one file.
        record["trajectory"] = trajectory(history, today=day)

    LAST_RUN.update(boards=len(boards), read=read, robots_blocked=len(blocked),
                    failed=len(failures), movements=len(out))

    print(f"[{COLLECTOR}] {len(boards)} boards, {read} read, "
          f"{len(blocked)} robots-blocked, {len(failures)} failed, "
          f"{len(out)} movements")
    for board_id in blocked:
        print(f"  ROBOTS        {board_id}: the ATS disallows this endpoint, "
              f"so it was not requested")
    for failure in failures:
        print(f"  BOARD FAILED  {failure}")

    # FAIL LOUD. A handful of employers closing a board is normal; a third of
    # the watchlist failing at once is an API change, a blocked agent or no
    # network, and none of those may look like a quiet hiring day.
    #
    # Robots-blocked boards leave the denominator: they were never attempted,
    # so counting them would either mask a real breakage (as successes) or
    # invent one (as failures).
    attempted = len(boards) - len(blocked)
    if attempted and len(failures) / attempted > MAX_FAILURE_RATE:
        raise BoardError(
            f"{len(failures)} of {attempted} boards failed, which is past "
            f"the {MAX_FAILURE_RATE:.0%} tolerance. This is a breakage, not a "
            f"quiet day. First: {failures[0] if failures else 'none'}")

    if persist if persist is not None else not dry_run:
        save_state(store)
    else:
        print(f"[{COLLECTOR}] state NOT written (dry run) — {STATE_PATH}")
    return out


def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated."""
    company = item["company"]
    kind, _, value = (item.get("place_key") or "").partition(":")
    city = value if kind == "city" else ""
    country = vocab.COUNTRY_NAMES.get(value, "") if kind == "country" else ""

    if item["kind"] == "pay":
        return {
            "company": company,
            "pillar": "rewards_comp",
            "signal_direction": "comp_shift",
            "headline": item["headline"],
            "summary": (
                f"{company} published an annual base salary range on "
                f"{item['listed']} of its open roles. The median advertised "
                f"band runs {item['band_low']} to {item['band_high']}."
            ),
            "talent_readthrough": (
                "Advertised bands move before published pay data does, because "
                "they are set by what an employer thinks it must offer to fill "
                "a role today. Read the direction across postings rather than "
                "any one band, and remember this is the ask, not the settlement."
            ),
            "city": city,
            "country": country,
            # Our measurement of the employer's board, not a figure the
            # employer stated about its pay. Deliberately not 'verified'.
            "confidence": "reported",
        }

    return {
        "company": company,
        "pillar": "company_development",
        "signal_direction": "hiring",
        "headline": item["headline"],
        "summary": (
            f"{company}'s published job board grew by {item['delta']} open "
            f"roles since it was last recorded."
        ),
        "talent_readthrough": (
            "A board that grows week on week is the earliest public evidence "
            "of hiring intent an employer produces: it moves before any "
            "announcement and before any filing. Treat the direction as the "
            "signal and the exact count as approximate, since roles are "
            "reposted, split across locations and withdrawn without notice."
        ),
        "city": city,
        "country": country,
        "functions": item.get("functions") or [],
        "headcount": item["delta"],
        "headcount_scope": "new_roles",
        "confidence": "reported",
    }

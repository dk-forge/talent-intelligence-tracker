"""Applicant-tracking system job boards — hiring momentum, measured daily.

Greenhouse, Ashby and SmartRecruiters all publish an employer's open roles as
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
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline import vocab

COLLECTOR = "ats_boards"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)"
REQUEST_DELAY = 0.2
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

BOARD_URLS = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
}
API_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
}
SOURCE_NAMES = {
    "greenhouse": "Greenhouse job board",
    "ashby": "Ashby job board",
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


def _get(url: str, *, timeout: int = TIMEOUT) -> dict:
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT,
                                      "Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


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


def fetch_postings(entry: dict) -> list[dict]:
    """One employer's open roles, normalised to {place, function, salary}."""
    ats, slug = entry["ats"], entry["slug"]
    out: list[dict] = []

    if ats == "greenhouse":
        for job in _get(API_URLS[ats].format(slug=slug)).get("jobs") or []:
            location = ((job.get("location") or {}).get("name") or "")
            out.append({
                "place": place_key(location),
                "function": function_for_title(job.get("title", "")),
                "salary": None,
            })

    elif ats == "ashby":
        for job in _get(API_URLS[ats].format(slug=slug)).get("jobs") or []:
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
            payload = _get(url)
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

    for entry in boards:
        board_id = f"{entry['ats']}:{entry['slug']}"
        record = store["boards"].setdefault(board_id, {})
        record["company"] = entry.get("company") or entry["slug"]

        try:
            postings = fetch_postings(entry)
        except (requests.RequestException, ValueError) as exc:
            failures.append(f"{board_id}: {type(exc).__name__} {exc}")
            continue

        current = snapshot(postings)
        # All three APIs answer 200 with an empty list for a slug that does not
        # exist, so an employer that HAD roles and now has none is a renamed
        # slug far more often than an employer that stopped hiring entirely.
        if current["total"] == 0:
            failures.append(f"{board_id}: returned zero postings")
            continue

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

    print(f"[{COLLECTOR}] {len(boards)} boards, {len(failures)} failed, "
          f"{len(out)} movements")
    for failure in failures:
        print(f"  BOARD FAILED  {failure}")

    # FAIL LOUD. A handful of employers closing a board is normal; a third of
    # the watchlist failing at once is an API change, a blocked agent or no
    # network, and none of those may look like a quiet hiring day.
    if boards and len(failures) / len(boards) > MAX_FAILURE_RATE:
        raise BoardError(
            f"{len(failures)} of {len(boards)} boards failed, which is past "
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

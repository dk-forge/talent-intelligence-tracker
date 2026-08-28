"""Applicant-tracking system job boards — hiring, pay and work mode, daily.

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
   intelligence is "this employer's board listed 40 more active postings in
   Dublin this fortnight than our previous scan", an observed fact about the
   employer, derived from counting -- NOT a claim it opened that many new roles
   (old postings expire as new ones appear).
3. **No model is involved.** Titles, locations, pay bands and work mode are
   all fields. There is no LLM cost at all, on any of the three row kinds.

**EVERY POSTING STATES THREE THINGS AND THIS READS ALL THREE.** For a long time
it read one — how many roles are open — and threw the other two away:

    volume     how many roles, by place and by function. company_development.
    pay        the annual base salary range the posting advertises, which US
               state, UK and EU pay-transparency law puts on the page.
               rewards_comp.
    work mode  remote, hybrid or onsite. how_we_work, which is the thinnest
               pillar in the corpus and the one this source is best placed to
               answer, because a job posting is the only document an employer
               publishes that states where the work happens on the record.

All three are counted, never copied. A row is always about an EMPLOYER —
a proportion or a median across a board — and never about a vacancy.

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

**The ATSs that are NOT here, and why**, checked 2026-07-30 so the next session
does not spend an evening rediscovering it. Two of these answer 200 to us; the
publisher's stated terms decide, not whether a request succeeds.

    recruitee        {slug}.recruitee.com      `User-Agent: * / Disallow: /` on
                                               the CAREER hosts, which are the
                                               hosts we would read. The
                                               marketing site allows; that is
                                               not the same host, and checking
                                               the wrong one is how a "yes" gets
                                               invented. The API is otherwise
                                               ideal — it 404s an unknown slug
                                               and publishes `company_name`.
    pinpoint         {slug}.pinpointhq.com     `User-Agent: * / Disallow: /`.
    softgarden       {slug}.softgarden.io      `Disallow: /api/` and `/rest/`,
                                               which is the whole of it.
    zoho recruit     {slug}.zohorecruit.com    robots refuses the path.
    comeet           www.comeet.co             not keyless: answers
                                               `{"message": "Token is missing"}`.
    teamtailor       {slug}.teamtailor.com     no public JSON found; the
                                               documented API needs a token.
    bamboohr         {slug}.bamboohr.com       robots allows (`/jobs/embed.php`
                                               is the only refusal), and a real
                                               tenant is distinguishable — it
                                               answers JSON where a non-tenant
                                               serves the marketing page. But no
                                               payload with postings in it has
                                               been captured, which is exactly
                                               where Workable sat, so it is not
                                               wired on a guess.
    workday          {tenant}.wdN.myworkdayjobs.com
                                               the jobs endpoint is a POST, the
                                               robots.txt path answers a Workday
                                               error object rather than a robots
                                               file, and every tenant is its own
                                               host with its own terms. A real
                                               project, not an afternoon.
    personio         {slug}.jobs.personio.de   the /xml feed works and robots
                                               404s (fails open, like Ashby).
    breezy           {slug}.breezy.hr          clean JSON, 403 on an unknown
                                               slug, robots allows.
    rippling         api.rippling.com          clean JSON, 404 on an unknown
                                               slug, robots allows.

The last three are permitted and parseable and are still not wired, because
being allowed to read something is not a reason to. Probed against the 250
European employers we hold signals for — the pool they exist to serve —
Personio returned two boards and neither cleared the ten-role bar, Breezy
returned none and Rippling returned none. The constraint is the employer pool,
not the platform list: continental Europe is 145 employers in the whole
database. Wire one of these the day a collector brings European employers in.
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
    # `pay_transparency=true` is what puts `pay_input_ranges` on each posting —
    # the band the US state pay-transparency statutes require on the page.
    # Deliberately NOT `content=true`, which is the other way to get the same
    # flag: content carries the full job description and takes Stripe's board
    # from 374KB to 4.4MB for one employer. Measured on the whole watchlist,
    # the flag costs 3% more bytes than the plain call and buys 6,281 priced
    # postings that were previously discarded.
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?pay_transparency=true",
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

# --- What a work-mode row has to clear ------------------------------------
#
# A mix is a fact about an EMPLOYER, so it has to be measured on enough of that
# employer's board to be one. Both floors, not either:
#
#   MIN_MODE_POSTINGS   how many postings actually stated a mode. At ten, one
#                       posting is ten points of the mix and the row would be
#                       reporting its own noise.
#   MIN_MODE_COVERAGE   what share of the whole board those are. A board of 300
#                       roles where 25 mention remote is not 100% remote; it is
#                       275 roles that did not say, and publishing the 25 as
#                       the employer's mix would be the single most misleading
#                       row this collector could produce.
#
# Measured against the live watchlist on 2026-08-14, 20 and 50% qualify 80 of
# 284 boards on 7,169 postings. Dropping to 10 and 40% adds 39 boards worth 978
# postings — an eighth more evidence carried by boards where the mix is thin,
# which is the wrong side of the trade for a pillar whose whole problem is that
# nobody believes it yet.
MIN_MODE_POSTINGS = 20
MIN_MODE_COVERAGE = 0.5

# And what counts as the mix having MOVED. One rule, on the largest share
# change across the three modes since the last row we published: fifteen points
# of a board is a policy that changed, not a fortnight of ordinary churn.
MIN_MODE_SHIFT = 0.15

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
        whole = str(candidate or "").strip()
        # The WHOLE string first, before any splitting. A board writes
        # "London, Ontario" and "Cambridge, MA" as one field, and the
        # gazetteer holds both of those spellings precisely because the bare
        # name belongs to two places. Splitting first threw the source's own
        # disambiguation away and filed every London, Ontario role in England.
        if whole:
            hit = vocab.normalize_city(whole)
            if hit:
                return f"city:{hit[0]}"
            parts.append(whole)
        for token in _SPLIT.split(whole):
            token = token.strip()
            if token:
                parts.append(token)

    for token in parts:
        hit = vocab.normalize_city(token)
        if not hit:
            continue
        # A qualifier beside the city that names a DIFFERENT country means this
        # is not that city. "Paris, TX" is not Paris and "Melbourne, FL" is not
        # Melbourne; both fall through to their country, which is the honest
        # answer for a town we do not curate.
        elsewhere = {code for code in
                     (vocab.place_qualifier_country(other)
                      for other in parts if other != token) if code}
        if elsewhere and hit[2] not in elsewhere:
            continue
        return f"city:{hit[0]}"
    for token in parts:
        # A US STATE CODE IS NOT A COUNTRY CODE, and half of them collide:
        # "Peoria, IL" resolved to Israel, "San Jose, CA" to Canada,
        # "Cambridge, MA" to Morocco, "Boise, ID" to Indonesia. Any two-letter
        # token that is a US state is read as one, because a board writing two
        # letters after a comma means the state every time.
        if len(token) == 2 and vocab.normalize_state(token):
            return "country:US"
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


# --- Posted pay -------------------------------------------------------------
#
# A band is read as ANNUAL BASE SALARY, IN THE CURRENCY THE POSTING STATES, or
# it is not read. Three rules hold every reader below, and each one exists
# because breaking it produces a wrong number rather than a missing one:
#
# 1. **Nothing is converted.** A band in GBP stays in GBP and is counted into a
#    GBP median. Converting would put a guessed exchange rate inside a pay
#    figure, and mixing currencies into one median produces a number that
#    describes nothing. This is what makes UK and EU boards readable at all:
#    the answer to "what about sterling" is a second median, not a conversion.
# 2. **Base salary only.** On-target earnings, commission, bonus and equity are
#    a different quantity, and a sales OTE counted as salary inflates the
#    employer's band by whatever the commission plan is worth. Ashby and Lever
#    type the component; Greenhouse does not, so its band is read off a
#    free-text title and REFUSED when that title says anything but base pay.
# 3. **An absent, vague or non-annual figure stores nothing.** "Competitive
#    salary" is not a range. An hourly rate is not an annual band. Where the
#    payload does not carry an interval (Greenhouse), a magnitude that cannot
#    be an annual salary is UNKNOWN and is dropped, never scaled up to look
#    like one.

# A stated annual salary sits inside this, in any currency this reads. The
# floor's real job is Greenhouse, whose `pay_input_ranges` carry NO interval
# field at all: a $28-$45 hourly band and a $28,000-$45,000 annual band are the
# same two numbers to a parser, and the only honest way to tell them apart is
# that one of them cannot be a year's pay. Anything below the floor is dropped
# as UNKNOWN rather than multiplied by 2,080 to make it fit.
MIN_ANNUAL_PAY = 12_000
MAX_ANNUAL_PAY = 10_000_000

# Greenhouse's range `title` is free text the employer typed, and it is the
# only thing separating a base band from a sales OTE. Read in two steps,
# because one keyword list gets this WRONG in both directions — measured
# against all 164 distinct titles live on the watchlist, 2026-08-14:
#
#   `_IS_BASE_PAY` wins first. A title that says "base salary" or "base pay"
#   IS a base band, whatever else it mentions. 161 postings carry titles like
#   "Annual base salary range (excluding equity and bonus)" and "Base Salary is
#   one part of our competitive total compensation" — every one of them a base
#   range, and every one of them refused by a naive bonus/equity/total-comp
#   keyword list. The employer naming what the figure EXCLUDES is the employer
#   confirming what it is.
#
#   `_NOT_BASE_PAY` then refuses the rest: on-target earnings, commission,
#   total cash, total compensation, and any interval that is not a year. 429
#   postings, led by "Total Targeted Cash" (164) and "Annual OTE Salary" (73).
#
#   Anything else is accepted, because `pay_input_ranges` is the field the US
#   state pay-transparency laws are answered in and its default content is the
#   base range — "Pay Range", "Salary Range", "Zone 1 Pay Range". The magnitude
#   floor above still has to pass, which is what catches an hourly band whose
#   title never says so.
_IS_BASE_PAY = re.compile(r"\bbase\s+(?:salary|pay|compensation|rate)\b", re.I)
_STILL_NOT_BASE_PAY = re.compile(r"on[- ]target|\bote\b|commission", re.I)
_NOT_BASE_PAY = re.compile(
    r"on[- ]target|\bote\b|commission|total\s+(?:targeted\s+)?cash|"
    r"total\s+(?:target(?:ed)?\s+)?compensation|"
    r"per[- ]hour|hourly|/\s*hr\b|per[- ]diem|"
    r"weekly|monthly|per[- ]month|stipend|\bequity\b|\bbonus\b", re.I)


def _is_base_pay_title(title: str) -> bool:
    """Whether a Greenhouse range title describes annual BASE pay."""
    text = title or ""
    if _IS_BASE_PAY.search(text):
        return not _STILL_NOT_BASE_PAY.search(text)
    return not _NOT_BASE_PAY.search(text)


def _band(low, high, currency: str) -> tuple[int, int, str] | None:
    """A band, or None. The one place the magnitude and ordering rules live."""
    try:
        low, high = int(low), int(high)
    except (TypeError, ValueError):
        return None
    currency = (currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        # A range with no currency is not a range. It is two numbers.
        return None
    if not (MIN_ANNUAL_PAY <= low <= high <= MAX_ANNUAL_PAY):
        return None
    return low, high, currency


def _salary(job: dict) -> tuple[int, int, str] | None:
    """An Ashby posted annual base-salary band, as (min, max, currency)."""
    comp = job.get("compensation") or {}
    for component in comp.get("summaryComponents") or []:
        if (component.get("compensationType") != "Salary"
                or component.get("interval") != "1 YEAR"):
            continue
        band = _band(component.get("minValue"), component.get("maxValue"),
                     component.get("currencyCode"))
        if band:
            return band
    return None


def _lever_salary(job: dict) -> tuple[int, int, str] | None:
    """A Lever posted annual base-salary band, as (min, max, currency).

    Same rule as Ashby's. Lever spells the interval `per-year-salary`.
    """
    band = job.get("salaryRange") or {}
    if band.get("interval") != "per-year-salary":
        return None
    return _band(band.get("min"), band.get("max"), band.get("currency"))


def _greenhouse_salary(job: dict) -> tuple[int, int, str] | None:
    """A Greenhouse posted annual base-salary band, as (min, max, currency).

    `pay_input_ranges` is what the US state pay-transparency laws put on the
    page, and it is served by the ordinary board endpoint under
    `?pay_transparency=true` — three percent more bytes than the plain call and
    no job description at all, which is why this collector asks for the flag
    and not for `content=true`.

    It is also the weakest-typed of the three: cents, a currency, and a
    free-text `title` the employer wrote. There is no interval and no
    compensation type, so both of those are decided by refusing anything that
    reads as an hourly rate, an on-target figure or a bonus — see
    `_NOT_BASE_PAY` and `MIN_ANNUAL_PAY`. An employer publishing several ranges
    for several work locations is normal; the FIRST acceptable one is taken,
    because picking the largest would bias every band upward.
    """
    for entry in job.get("pay_input_ranges") or []:
        if not _is_base_pay_title(entry.get("title") or ""):
            continue
        low, high = entry.get("min_cents"), entry.get("max_cents")
        if not isinstance(low, int) or not isinstance(high, int):
            continue
        band = _band(low // 100, high // 100, entry.get("currency_type"))
        if band:
            return band
    return None


# --- Work mode --------------------------------------------------------------
#
# Remote, hybrid or onsite: the second thing every posting states and the one
# this tracker's thinnest pillar is short of. It is read STRUCTURED where the
# ATS types it and from prose only where it does not, and the difference is
# recorded per board so nothing downstream has to guess which it was.
#
# THE RULE THAT MATTERS IS WHAT COUNTS AS UNKNOWN. A posting that does not say
# is unknown; it is never read as onsite. "Onsite by default" would be a true
# statement about the world and a false statement about the document, and it
# would label the great majority of the corpus off an absence — which is
# exactly the invented number this repo refuses everywhere else. Boards where
# too few postings state a mode publish nothing at all.

WORK_MODES = ("remote", "hybrid", "onsite")

WORK_MODE_LABELS = {
    "remote": "fully remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}

# Ashby and Lever both spell it `workplaceType`, in different cases and with
# different words for the same third state.
_STRUCTURED_WORK_MODE = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on-site": "onsite",
    "on site": "onsite",
    "inperson": "onsite",
    "in-person": "onsite",
    "office": "onsite",
}

# Prose, for the one live ATS that types nothing: Greenhouse states the mode,
# when it states it, inside the location name ("Remote - US", "London
# (Hybrid)"). Hybrid is tested first because "Hybrid - Remote 2 days" says
# hybrid, and a remote-first test would call it remote.
_PROSE_WORK_MODE = (
    (re.compile(r"\bhybrid\b", re.I), "hybrid"),
    (re.compile(r"\b(?:fully[- ])?remote\b|\bwork from home\b|\bwfh\b|"
                r"\bdistributed\b|\banywhere\b", re.I), "remote"),
    (re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b", re.I),
     "onsite"),
)


def work_mode_from_text(*candidates: str) -> str | None:
    """The work mode these strings state, or None if they do not state one.

    Silence is None. Nothing here infers onsite from the absence of the word
    remote, which is the one reading that would fill the pillar with a number
    the postings never made.
    """
    text = " ".join(str(c or "") for c in candidates)
    if not text.strip():
        return None
    for pattern, mode in _PROSE_WORK_MODE:
        if pattern.search(text):
            return mode
    return None


def structured_work_mode(value) -> str | None:
    """An ATS's own typed work-mode field, normalised, or None."""
    key = str(value or "").strip().lower().replace("_", "-")
    return _STRUCTURED_WORK_MODE.get(key) or _STRUCTURED_WORK_MODE.get(
        key.replace("-", ""))


def fetch_postings(entry: dict) -> list[dict]:
    """One employer's open roles, normalised to
    {place, function, salary, mode, mode_source}.

    `mode_source` is "structured" when the ATS typed the field itself and
    "prose" when it was read out of a location string. It is recorded rather
    than discarded so a board's mix can say which it is, and so a provider
    that starts typing the field is visible as a change in the data instead of
    a silent improvement nobody can point at.
    """
    ats, slug = entry["ats"], entry["slug"]
    delay = ATS_DELAY.get(ats, REQUEST_DELAY)
    out: list[dict] = []

    if ats == "greenhouse":
        for job in _get(API_URLS[ats].format(slug=slug), delay=delay).get("jobs") or []:
            location = ((job.get("location") or {}).get("name") or "")
            # Greenhouse types NOTHING about work mode. The location name is
            # the only place an employer can say it, so this is the one live
            # ATS read from prose, and most of its postings say nothing —
            # which is recorded as nothing.
            out.append({
                "place": place_key(location),
                "function": function_for_title(job.get("title", "")),
                "salary": _greenhouse_salary(job),
                "mode": work_mode_from_text(location),
                "mode_source": "prose",
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
            mode = structured_work_mode(job.get("workplaceType"))
            out.append({
                "place": place_key(*locations, job.get("country") or ""),
                "function": (vocab.normalize_function(categories.get("team", ""))
                             or vocab.normalize_function(categories.get("department", ""))
                             or function_for_title(job.get("text", ""))),
                "salary": _lever_salary(job),
                "mode": mode or work_mode_from_text(*locations),
                "mode_source": "structured" if mode else "prose",
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
                # Its `telecommuting` flag is a boolean with two states for a
                # three-state fact: false is "not remote", which is onsite and
                # hybrid together, so it cannot answer the question this asks.
                # The location string is read instead, and says nothing far
                # more often than it says something.
                "mode": work_mode_from_text(location.get("location_str", ""),
                                            location.get("city", "")),
                "mode_source": "prose",
            })

    elif ats == "ashby":
        for job in _get(API_URLS[ats].format(slug=slug), delay=delay).get("jobs") or []:
            if job.get("isListed") is False:
                continue
            postal = ((job.get("address") or {}).get("postalAddress") or {})
            # `workplaceType`, NOT `isRemote`. Ashby publishes both, and on a
            # role typed Hybrid `isRemote` is still true — it means
            # remote-ELIGIBLE, which is a different question. Reading the
            # boolean would have filed Ramp's hybrid engineering roles as
            # fully remote, so the three-state field is the only one read.
            mode = structured_work_mode(job.get("workplaceType"))
            out.append({
                "place": place_key(postal.get("addressLocality", ""),
                                   postal.get("addressRegion", ""),
                                   job.get("location", ""),
                                   postal.get("addressCountry", "")),
                "function": (vocab.normalize_function(job.get("department", ""))
                             or function_for_title(job.get("title", ""))),
                "salary": _salary(job),
                "mode": mode or work_mode_from_text(job.get("location", "")),
                "mode_source": "structured" if mode else "prose",
            })

    elif ats == "smartrecruiters":
        for page in range(SR_MAX_PAGES):
            url = (f"{API_URLS[ats].format(slug=slug)}"
                   f"?limit={SR_PAGE_SIZE}&offset={page * SR_PAGE_SIZE}")
            payload = _get(url, delay=delay)
            content = payload.get("content") or []
            for job in content:
                location = job.get("location") or {}
                # SmartRecruiters is the ONLY provider that types all three
                # states as data AND states the days-per-week, in
                # `hybridDescription`. It is also the one provider whose
                # robots.txt refuses us, so none of that is read today. The
                # parser stays because the terms may change; the request does
                # not happen while they say no.
                if location.get("hybrid"):
                    mode = "hybrid"
                elif location.get("remote"):
                    mode = "remote"
                elif location.get("remote") is False:
                    mode = "onsite"
                else:
                    mode = None
                out.append({
                    "place": place_key(location.get("city", ""),
                                       location.get("country", "")),
                    "function": (vocab.normalize_function(
                        (job.get("function") or {}).get("label", ""))
                        or function_for_title(job.get("name", ""))),
                    "salary": None,
                    "mode": mode,
                    "mode_source": "structured" if mode else "prose",
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
    # Pay is bucketed BY CURRENCY and never pooled. Two boards in one bucket
    # would be an unstated exchange rate inside a published pay figure; a
    # sterling board simply gets a sterling median, which is what makes the UK
    # and the EU readable here without converting anything.
    by_currency: dict[str, dict] = {}
    modes: dict[str, int] = {}
    mode_places: dict[str, int] = {}
    structured = 0
    for job in postings:
        if job.get("place"):
            places[job["place"]] = places.get(job["place"], 0) + 1
        if job.get("function"):
            functions[job["function"]] = functions.get(job["function"], 0) + 1
        if job.get("salary"):
            low, high, currency = job["salary"]
            bucket = by_currency.setdefault(
                currency, {"lows": [], "highs": [], "places": {}})
            bucket["lows"].append(low)
            bucket["highs"].append(high)
            if job.get("place"):
                bucket["places"][job["place"]] = bucket["places"].get(job["place"], 0) + 1
        mode = job.get("mode")
        if mode in WORK_MODES:
            modes[mode] = modes.get(mode, 0) + 1
            if job.get("mode_source") == "structured":
                structured += 1
            if job.get("place"):
                mode_places[job["place"]] = mode_places.get(job["place"], 0) + 1

    snap = {"total": len(postings), "places": places, "functions": functions}

    if by_currency:
        # One band per board: the currency most of the priced roles are in.
        # A board that prices in three currencies has no single band, and
        # inventing one by picking the biggest pile would attach a US number
        # to a European employer.
        currency = max(by_currency, key=lambda c: len(by_currency[c]["lows"]))
        bucket = by_currency[currency]
        snap["salary"] = {
            "listed": len(bucket["lows"]),
            "median_min": _median(bucket["lows"]),
            "median_max": _median(bucket["highs"]),
            "currency": currency,
            # Where the roles carrying that band actually are, and only when
            # they mostly agree. A median band across three continents belongs
            # to no country, and saying otherwise would put a US salary on a
            # city because most of the employer's OTHER roles are there.
            "place": _dominant(bucket["places"], len(bucket["lows"])),
            # How many other currencies this board also prices in, so a
            # reader of the state file can see the band is a slice and not
            # the whole of what the employer advertises.
            "other_currencies": sorted(c for c in by_currency if c != currency),
        }

    known = sum(modes.values())
    if known:
        snap["modes"] = dict(modes)
        snap["mode_known"] = known
        # The share of the counted postings whose mode came from a field the
        # ATS types, rather than from words in a location string. It is
        # recorded rather than asserted because it differs by an order of
        # magnitude across providers, and a row derived mostly from prose
        # deserves to be recognisable as one.
        snap["mode_structured"] = structured
        snap["mode_place"] = _dominant(mode_places, known)
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


# A symbol where a reader would expect one, and the ISO code otherwise. Never
# a converted figure, and never a bare number: "95,000" with no currency on it
# is the "range with no currency" this collector refuses at the parser.
_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€",
                     "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "SGD": "S$",
                     "INR": "₹", "JPY": "¥", "BRL": "R$"}

_CURRENCY_NAMES = {"USD": "US dollars", "GBP": "pounds sterling",
                   "EUR": "euros", "CAD": "Canadian dollars",
                   "AUD": "Australian dollars", "NZD": "New Zealand dollars",
                   "SGD": "Singapore dollars", "INR": "Indian rupees",
                   "JPY": "Japanese yen", "BRL": "Brazilian reais"}


def _money(amount: int, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount:,}" if symbol else f"{amount:,} {currency}"


def _mode_shares(snap: dict) -> dict[str, float]:
    known = snap.get("mode_known") or 0
    if not known:
        return {}
    modes = snap.get("modes") or {}
    return {mode: modes.get(mode, 0) / known for mode in WORK_MODES}


def _mode_qualifies(snap: dict) -> bool:
    """Whether this board stated a mode on enough of itself to have a mix."""
    known = snap.get("mode_known") or 0
    total = snap.get("total") or 0
    return (known >= MIN_MODE_POSTINGS and total > 0
            and known / total >= MIN_MODE_COVERAGE)


def _mode_phrase(shares: dict[str, float]) -> tuple[str, float]:
    """The mode with the largest share, and that share."""
    mode = max(WORK_MODES, key=lambda m: shares.get(m, 0.0))
    return mode, shares.get(mode, 0.0)


def _work_mode_item(entry: dict, current: dict, baseline: dict | None,
                    today: str) -> dict | None:
    """A ways-of-working row, or None.

    TWO ROWS EXIST AND ONLY TWO, AND THE ARGUMENT FOR EACH IS DIFFERENT.

    The FIRST qualifying observation publishes the mix once: "this employer
    advertises 61% of its 214 open roles as hybrid" is a fact about an employer
    with the employer's own board behind it, and it is precisely the fact the
    how_we_work pillar exists to hold. It is not a job advert — no title, no
    vacancy, no link to one — and it is not a listing, because it is a
    proportion of a population rather than a copy of it.

    EVERY ROW AFTER THAT IS A CHANGE, and nothing else is published ever again
    for that employer. A monthly restatement of the same mix would be the
    thousands of near-identical rows this collector exists not to produce: 284
    boards on a monthly cadence is 3,408 rows a year saying almost exactly what
    the row before them said. `dedupe.fuzzy_duplicate` would in any case
    collapse most of them (same employer, same pillar, 0.85 headline overlap
    across 400 days) and the ones that survived would be the accidents, which
    is the worst of both. So the baseline moves only when a row is emitted, and
    the next row has to clear MIN_MODE_SHIFT against THAT — which makes every
    row after the first a genuine fortnightly-or-slower movement in how an
    employer says the work happens.
    """
    if not _mode_qualifies(current):
        return None

    shares = _mode_shares(current)
    company = entry["company"]
    url = BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
    known = current["mode_known"]
    total = current["total"]
    structured = current.get("mode_structured") or 0
    # `remote:` is deliberately not spoken here. It is a real place key and
    # `place_label` renders it "remote roles", which inside a sentence about
    # remote, hybrid and onsite reads as "most of them in remote roles, 100%
    # are advertised as fully remote" — a tautology dressed as a location. A
    # city or a country is a place; remote is the answer, not the place.
    key = current.get("mode_place") or ""
    place = place_label(key) if key.partition(":")[0] in ("city", "country") else ""
    where = f", most of them in {place}," if place else ","

    # How the mode was read, stated rather than implied. Greenhouse types no
    # work-mode field at all, so its share is read out of location strings, and
    # a row should not be able to hide which of the two it is.
    provenance = (
        "The mode is read from the field the ATS types on each posting."
        if structured == known else
        "The mode is read from the wording of each posting's location, which "
        "is the only place this ATS states it."
        if structured == 0 else
        f"{structured} of the {known} state the mode in a typed field; the "
        f"rest state it in the wording of their location."
    )
    limit = (
        f"{total - known} of the {total} open roles say nothing about where "
        f"the work happens and are counted in neither direction: a posting "
        f"that does not say is unknown here and is never read as onsite."
    ) if total > known else (
        "Every open role on the board states its mode."
    )

    if baseline is None:
        mode, share = _mode_phrase(shares)
        headline = (f"{company} advertises {share:.0%} of its open roles as "
                    f"{WORK_MODE_LABELS[mode]}")
        body = (
            f"Across the {known} of {company}'s {total} open roles that state "
            f"how the work happens{where} {shares['remote']:.0%} are advertised "
            f"as fully remote, {shares['hybrid']:.0%} as hybrid and "
            f"{shares['onsite']:.0%} as onsite, measured on {today}. "
            f"{provenance} {limit} "
            "This is the employer's own advertising rather than a policy it "
            "announced, and it describes the roles it is hiring for now, not "
            "the arrangement of the people already there."
        )
        kind_detail = {"baseline": True, "mode": mode, "share": round(share, 4)}
    else:
        was = _mode_shares(baseline)
        moved = max(WORK_MODES, key=lambda m: abs(shares.get(m, 0) - was.get(m, 0)))
        delta = shares[moved] - was.get(moved, 0.0)
        if abs(delta) < MIN_MODE_SHIFT:
            return None
        since = baseline.get("date") or "the previous observation"
        direction = "up" if delta > 0 else "down"
        headline = (f"{company} moved from {was.get(moved, 0.0):.0%} to "
                    f"{shares[moved]:.0%} of open roles advertised as "
                    f"{WORK_MODE_LABELS[moved]}")
        body = (
            f"On {since}, {was.get(moved, 0.0):.0%} of the {company} open roles "
            f"stating a work mode were advertised as {WORK_MODE_LABELS[moved]}. "
            f"On {today} it is {shares[moved]:.0%} of {known} such roles"
            f"{where} a move of {abs(delta):.0%} {direction}. The rest of the "
            f"board reads {shares['remote']:.0%} fully remote, "
            f"{shares['hybrid']:.0%} hybrid and {shares['onsite']:.0%} onsite. "
            f"{provenance} {limit} "
            "A change in what an employer advertises is not the same as a "
            "policy change it announced, and roles turn over, so read the "
            "direction rather than the exact percentage."
        )
        kind_detail = {"baseline": False, "mode": moved,
                       "share": round(shares[moved], 4),
                       "was": round(was.get(moved, 0.0), 4)}

    return dict({
        "raw_text": f"{headline}\n\n{body}",
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAMES[entry["ats"]],
        "discovery_url": url,
        "published_date": today,
        "company": company,
        "kind": "work_mode",
        "counted": known,
        "board_total": total,
        "shares": {m: round(shares[m], 4) for m in WORK_MODES},
        "place_key": current.get("mode_place") or "",
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, **kind_detail)


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
    # HONESTY: a board-scan delta is an OBSERVATION, not a hiring act. The count
    # rose because the employer's board LISTED more active postings than our
    # previous scan -- old postings expire while new ones appear, so this never
    # proves the employer "opened" that many new roles. Describe the measurement,
    # not an intent we did not witness. (The body below already says as much.)
    headline = (f"{company}'s job board listed {delta} more active postings{where} "
                f"than our previous scan (job board: {baseline['total']} to {current['total']})")

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
    # Only comparable in the SAME currency. A board that priced mostly in USD
    # last week and mostly in GBP this week has not moved its bands by the
    # ratio between two currencies; it is advertising somewhere else, and that
    # is a new band rather than a suppressed one. State files written before
    # the currency field existed were USD-only by construction, so a missing
    # key reads as USD rather than as a mismatch.
    if old and (old.get("currency") or "USD") == (salary.get("currency") or "USD"):
        was = (old["median_min"] + old["median_max"]) / 2
        now = (salary["median_min"] + salary["median_max"]) / 2
        if was > 0 and abs(now - was) / was < MIN_BAND_MOVE:
            return None

    company = entry["company"]
    url = BOARD_URLS[entry["ats"]].format(slug=entry["slug"])
    currency = salary.get("currency") or "USD"
    low = _money(salary["median_min"], currency)
    high = _money(salary["median_max"], currency)
    listed = salary["listed"]

    where = place_label(salary.get("place") or "")
    headline = (f"{company} advertises a median posted salary band of "
                f"{low} to {high} across {listed} open roles")
    body = (
        f"Of the roles {company} had open on {today}, {listed} published an "
        f"annual base salary range in {_CURRENCY_NAMES.get(currency, currency)}. "
        f"The median of those ranges runs from {low} to {high}. "
    )
    if where:
        body += f"Most of the priced roles are in {where}. "
    others = salary.get("other_currencies") or []
    if others:
        body += (
            f"The employer also advertises bands in {', '.join(others)}, which "
            f"are counted separately and never averaged into this one: "
            f"converting them would put a guessed exchange rate inside a pay "
            f"figure. "
        )
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

        # Work mode carries its OWN baseline, not the hiring one. The two
        # measure different things and move on different clocks: a board can
        # double in size without its remote/hybrid/onsite mix shifting at all,
        # and a board that never grows can go from remote-first to
        # onsite-required in a quarter. Sharing a baseline would let either
        # movement consume the other's.
        mode_baseline = record.get("mode_baseline")
        mode_item = _work_mode_item(entry, current, mode_baseline, day)
        if mode_item:
            out.append(mode_item)
            record["mode_baseline"] = dict(current, date=day)
        elif mode_baseline is None and _mode_qualifies(current):
            # Unreachable in practice — a qualifying board with no baseline
            # always emits — but recorded rather than assumed, so a future
            # tightening of the emit rule cannot silently leave a board with
            # no baseline for ever.
            record["mode_baseline"] = dict(current, date=day)

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

    if item["kind"] == "work_mode":
        shares = item["shares"]
        if item["baseline"]:
            summary = (
                f"{company} advertises {item['share']:.0%} of the "
                f"{item['counted']} open roles that state a work mode as "
                f"{WORK_MODE_LABELS[item['mode']]}: {shares['remote']:.0%} "
                f"fully remote, {shares['hybrid']:.0%} hybrid and "
                f"{shares['onsite']:.0%} onsite."
            )
        else:
            summary = (
                f"{company} moved from {item['was']:.0%} to "
                f"{item['share']:.0%} of open roles advertised as "
                f"{WORK_MODE_LABELS[item['mode']]}, across {item['counted']} "
                f"roles that state a work mode."
            )
        return {
            "company": company,
            "pillar": "how_we_work",
            # Neither hiring nor displacement: how the work happens says
            # nothing about how many people do it. `comp_shift` would be
            # borrowing the pay pillar's word for a fact about location.
            "signal_direction": "neutral",
            "headline": item["headline"],
            "summary": summary,
            "talent_readthrough": (
                "What an employer advertises is the offer a candidate can "
                "accept today. It moves before any return-to-office "
                "announcement, because a policy reaches the job postings "
                "before it reaches a press release. Read it as the shape of "
                "the roles being hired for, not of the whole workforce, and "
                "read the direction rather than the exact share."
            ),
            "city": city,
            "country": country,
            # Our count of the employer's own postings on one date. The board
            # is the employer's publication; the proportion is our measurement
            # of it, and a derived measurement never earns 'verified'.
            "confidence": "reported",
        }

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
            "reliable part and the exact count as approximate, since roles are "
            "reposted, split across locations and withdrawn without notice."
        ),
        "city": city,
        "country": country,
        "functions": item.get("functions") or [],
        "headcount": item["delta"],
        "headcount_scope": "new_roles",
        "confidence": "reported",
    }

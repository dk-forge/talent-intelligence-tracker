#!/usr/bin/env python3
"""Find which employers we ALREADY TRACK publish an open ATS job board.

    python3 resolve_ats_boards.py --limit 400
    python3 resolve_ats_boards.py --names "Okta, Inc." "Roblox Corporation"

Run by hand, never on a schedule. It is a discovery tool: it proposes watchlist
entries, and a human merges the ones that look right into
`collectors/ats_watchlist.json`. Nothing here writes the database.

Why it exists, and why it verifies so hard:

* Greenhouse, Ashby, SmartRecruiters and Workable all answer **200 with an empty
  list** for a slug that does not exist, so a mistyped slug is indistinguishable
  from an employer with nothing open. A watchlist built by guessing slugs is the
  "looks wired, delivers nothing" failure in its purest form.
* A slug that exists may belong to someone else entirely. `sierra` is a board;
  it is not necessarily the Sierra in our database. So a candidate is only
  reported with the NAME EVIDENCE that backs it:

      board_name   the ATS itself published a name and it matches ours
                   (Greenhouse `/v1/boards/{slug}.name`, Workable account
                   `name`, Lever board page `<title>`)
      slug_only    no name is published anywhere; the slug is exactly the
                   employer's own normalised name and nothing more

  `slug_only` candidates are printed separately and are a human decision.
* A matching name is only evidence when two companies are unlikely to share it.
  `ashby:ditto` published exactly "DITTO", matched our DITTO exactly, and is a
  different company — ours raised $6m for menstrual-health supplements and the
  board is hiring Bluetooth and database engineers. So a short one-word name
  goes to `--review`, which prints what we hold on the employer beside what the
  board is advertising, and the merge refuses it until somebody has looked.
* A board is only worth proposing at `--min-count` open roles or more (10 by
  default, the bar `collectors/ats_watchlist.json` states). A five-role board
  empties in a normal quiet week, and the collector cannot tell an empty board
  from a dead slug, so a tiny board buys one employer at the price of a
  recurring false breakage. Under-size hits are reported as `too_small` rather
  than dropped silently, because "this employer HAS a board, it is just small"
  is worth knowing next time.
* robots.txt is checked before the first request to a host, with the same
  function the press collector uses. SmartRecruiters is absent from this tool on
  purpose: `https://api.smartrecruiters.com/robots.txt` is `Disallow: /` for
  every agent except LinkedInBot.

Scale, and what limits it. `--workers` probes several employers at once, but
each ATS host keeps its OWN serialised pace (`DELAY`), so concurrency never
raises the rate any one publisher sees — it only stops a slow host idling the
fast ones. Lever's `Crawl-delay: 1` therefore caps a Lever pass at roughly a
thousand employers an hour however many workers are asked for, which is why a
wide sweep runs `--ats greenhouse ashby` and Lever is spent on the employers
that most need it.

`--ledger` remembers every employer already probed, so a second pass over a
bigger pool costs only the difference. It is the difference between "probe the
next 3,000" and "probe 9,000 again to reach the next 3,000".
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from collectors import ats_boards
from collectors.ats_boards import (USER_AGENT, WATCHLIST_PATH, load_watchlist,
                                   robots_allows)

REPO_ROOT = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / "data" / "talent_intel.db"
TIMEOUT = 30

# Per-ATS pause between requests. Lever's robots.txt states `Crawl-delay: 1`,
# so its number is theirs and not a preference of ours. Workable's is measured
# rather than chosen, and it is the collector's number, not a second opinion:
# `apply.workable.com` is behind Cloudflare and starts answering 429 above
# roughly one request a second. At 0.5 it answered 429 to a hand check made
# while a sweep was running, and a 429 is indistinguishable from "no board"
# to the probe — so half a second was quietly turning real boards into
# nothing found.
DELAY = {"greenhouse": 0.3, "lever": 1.1, "ashby": 0.3,
         "workable": ats_boards.ATS_DELAY["workable"]}

# Legal-form and holding-company noise. Stripped only from the END of a name,
# so "Group 1 Automotive" keeps its Group.
_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co", "plc",
    "limited", "ltd", "llc", "lp", "llp", "nv", "n v", "sa", "ag", "gmbh",
    "holdings", "holding", "group", "the", "class a", "common stock",
)
_PUNCT = re.compile(r"[^a-z0-9]+")


def base_name(name: str) -> str:
    """'Cloudflare, Inc.' -> 'cloudflare'. Lower, de-punctuated, de-suffixed."""
    text = (name or "").lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(" " + suffix):
                text = text[: -(len(suffix) + 1)].strip()
                changed = True
    return text


MAX_SLUGS = 3


def slug_candidates(name: str) -> list[str]:
    """Slugs worth trying, most likely first. Deliberately few: every extra
    variant is a request to somebody else's API for a guess we cannot verify.

    Both the suffix-stripped and the whole name are tried, because stripping is
    a guess in both directions: 'Match Group, Inc.' is `matchgroup` on Lever,
    and 'Cloudflare, Inc.' is `cloudflare` on Greenhouse.
    """
    stripped = base_name(name)
    whole = _PUNCT.sub(" ", (name or "").lower().replace("&", " and ")).strip()
    out: list[str] = []
    for text in (stripped, whole):
        if not text or len(text) < 3:
            continue
        for slug in (text.replace(" ", ""), text.replace(" ", "-")):
            if slug not in out and len(slug) >= 3:
                out.append(slug)
    return out[:MAX_SLUGS]


class Tally:
    """Thread-safe count of what went wrong, by ATS and by cause.

    A probe swallows its errors on purpose — one unreachable host must not end
    a sweep of nine thousand employers — but a swallowed 429 is indistinguish-
    able from "this employer has no board", so a rate-limited pass and a
    genuinely empty pool produce the same silence. Counting them is what tells
    a low yield from a throttled one.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def add(self, key: str) -> None:
        with self._lock:
            self.counts[key] = self.counts.get(key, 0) + 1

    def __bool__(self) -> bool:
        return bool(self.counts)

    def report(self) -> str:
        return ", ".join(f"{count} x {key}" for key, count
                         in sorted(self.counts.items(), key=lambda kv: -kv[1]))


class Pace:
    """One serialised request stream per ATS host, whatever the worker count.

    Concurrency here is about not letting Lever's crawl delay idle Greenhouse,
    never about asking any publisher for more. Each host keeps its own lock, so
    N workers still produce at most one request per `DELAY[ats]` seconds to that
    host — the number a publisher stated stays the number it gets.
    """

    def __init__(self, delays: dict[str, float]):
        self._delays = delays
        self._locks = {ats: threading.Lock() for ats in delays}
        self._last = {ats: 0.0 for ats in delays}

    def wait(self, ats: str) -> None:
        lock = self._locks.get(ats)
        if lock is None:
            return
        with lock:
            gap = time.monotonic() - self._last[ats]
            if gap < self._delays[ats]:
                time.sleep(self._delays[ats] - gap)
            self._last[ats] = time.monotonic()


# Words that can stand between an employer's name and the name on its board
# without making it a different employer: the arm of the group that runs the
# board. "BYD North America" is BYD's board. "Uniti AI" is not Uniti Group's.
_QUALIFIERS = {
    "north", "south", "america", "americas", "usa", "us", "uk", "emea", "apac",
    "europe", "european", "asia", "pacific", "international", "global",
    "worldwide", "canada", "japan", "india", "china", "korea", "australia",
    "france", "germany", "deutschland", "iberia", "benelux", "nordics",
    "group", "holdings", "holding", "technologies", "technology", "labs",
    "systems", "services", "solutions", "careers", "jobs", "team", "hq",
    "and", "of", "the", "for", "at", "de", "do", "brasil", "brazil", "mexico",
}


def names_agree(ours: str, theirs: str, *, exact: bool = False) -> bool:
    """Whether the name the ATS published is the employer we asked for.

    Greenhouse and Workable publish the ACCOUNT name, which is one entity under
    a legal or a brand spelling — so "Recursion" and "Recursion
    Pharmaceuticals" are the same employer and plain containment is right in
    that direction.

    The other direction is where boards get mis-attributed, and both known
    cases came from it. When the board's name is LONGER than ours, every extra
    word has to be a qualifier: `greenhouse:byd` publishes "BYD North America",
    which is BYD's own board run by its American arm, while `ashby:uniti`
    publishes "Uniti AI", which is a different company that merely starts the
    same way. Containment called both a match.

    `exact` is for Ashby, where the only name available is a board-page title —
    a short brand string with no legal form to strip, so nothing but equality
    is evidence.
    """
    a, b = base_name(ours), base_name(theirs)
    if not a or not b:
        return False
    a_joined, b_joined = a.replace(" ", ""), b.replace(" ", "")
    if a_joined == b_joined:
        return True
    if exact:
        return False
    if b_joined in a_joined:
        # Ours is the longer spelling: the board is using the brand name.
        return True
    if a_joined not in b_joined:
        return False
    residual = [token for token in b.split() if token not in a.split()]
    return bool(residual) and all(token in _QUALIFIERS for token in residual)


# Only Ashby's name comes from a page title rather than an account record.
EXACT_NAME_MATCH = {"ashby"}

# What a board page appends to the employer's name. Stripped before comparing,
# so "Deliveroo Jobs" is read as "Deliveroo" and not as a company we have never
# heard of.
_TITLE_NOISE = re.compile(
    r"^\s*(?:jobs?|careers?|open roles|vacancies)\s+at\s+|"
    r"\s*[-—|:]?\s*(?:jobs?|careers?|open roles|vacancies|job board)\s*$",
    re.I)


def clean_board_title(title: str) -> str:
    """'Deliveroo Jobs' -> 'Deliveroo'. 'Jobs at Ramp' -> 'Ramp'."""
    text = (title or "").strip()
    for _ in range(3):
        stripped = _TITLE_NOISE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    return text


# --- the probes ------------------------------------------------------------
#
# Each returns (job_count, published_name_or_None) or None when the board does
# not exist. Zero jobs is NOT a board: an employer with a real board and nothing
# open is indistinguishable from a wrong slug, and adding it would hand the
# collector a board it will report as broken on day one.


def _get(url: str):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT,
                                      "Accept": "application/json"},
                        timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def probe_greenhouse(slug: str):
    board = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    if not board:
        return None
    jobs = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs") or {}
    count = len(jobs.get("jobs") or [])
    return (count, board.get("name")) if count else None


def board_page_title(url: str) -> str | None:
    """The employer name a board page publishes in its <title>, or None.

    The board page is a different host from the posting API, so it gets its own
    robots check rather than riding on the API's. `robots_allows` caches per
    origin, so this costs one request for the whole run.
    """
    if not robots_allows(url):
        return None
    try:
        page = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if page.status_code != 200:
        return None
    match = re.search(r"<title>([^<]{1,160})</title>", page.text, re.I)
    return clean_board_title(match.group(1)) if match else None


def probe_lever(slug: str):
    payload = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(payload, list) or not payload:
        return None
    return len(payload), board_page_title(f"https://jobs.lever.co/{slug}")


def probe_ashby(slug: str):
    payload = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(payload, dict):
        return None
    jobs = [j for j in (payload.get("jobs") or []) if j.get("isListed") is not False]
    if not jobs:
        return None
    # The posting API publishes no organisation name, but the board PAGE does,
    # in its title — and `https://jobs.ashbyhq.com/robots.txt` allows it
    # (`/meeting/`, `/b/` and its own `/api/` are the only disallowed paths;
    # the posting API lives on a different host). That title is the only thing
    # standing between an Ashby slug and the wrong company, so it is worth the
    # extra request: without it every Ashby candidate is a human judgement.
    return len(jobs), board_page_title(f"https://jobs.ashbyhq.com/{slug}")


def probe_workable(slug: str):
    payload = _get(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs") or []
    return (len(jobs), payload.get("name")) if jobs else None


PROBES = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "workable": probe_workable,
}
ROBOTS_PROBE = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/example/jobs",
    "lever": "https://api.lever.co/v0/postings/example?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/example",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/example",
}


def companies_from_db(limit: int, min_signals: int,
                      collectors: list[str] | None = None,
                      industries: list[str] | None = None,
                      countries: list[str] | None = None,
                      exclude_countries: list[str] | None = None) -> list[tuple[str, int]]:
    """Employers we already hold signals for, most-signalled first.

    `collectors` narrows the pool. It matters: the pay-gap import alone
    contributes several thousand UK public bodies, and probing an NHS trust for
    a Greenhouse board is a request nobody benefits from.

    `countries` narrows it by where the employer is, which is how a pass is
    aimed at the thin part of the map. The country read is the employer's HQ
    first and the signal's location second, because an ATS board belongs to the
    employer and not to the place one of its filings happened to mention.
    """
    # READ-ONLY, and not as a matter of taste. This file is committed, and
    # opening it read-write rewrites its header even when nothing is queried
    # into it — so a tool whose docstring says "nothing here writes the
    # database" was leaving a 38MB tracked file dirty, one `git add -A` away
    # from a spurious commit that looks exactly like a data change.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    where = ["is_current = 1", "company IS NOT NULL", "company != ''"]
    params: list = []
    if collectors:
        where.append("collector IN (%s)" % ",".join("?" * len(collectors)))
        params += collectors
    if countries:
        where.append("COALESCE(NULLIF(hq_country,''), NULLIF(country,'')) IN (%s)"
                     % ",".join("?" * len(countries)))
        params += countries
    if exclude_countries:
        where.append("COALESCE(NULLIF(hq_country,''), NULLIF(country,''), '?') "
                     "NOT IN (%s)" % ",".join("?" * len(exclude_countries)))
        params += exclude_countries
    if industries:
        # A far better prior than size for these four ATSs: they are what
        # software employers use, while most large US filers are on systems
        # with no public board endpoint at all.
        where.append("industry IN (%s)" % ",".join("?" * len(industries)))
        params += industries
    params.append(min_signals)
    rows = conn.execute(
        f"""SELECT company, COUNT(*) AS n FROM signals
             WHERE {' AND '.join(where)}
             GROUP BY company_key HAVING n >= ?
             ORDER BY n DESC, company ASC""", params).fetchall()
    conn.close()

    seen: set[str] = set()
    out: list[tuple[str, int]] = []
    for row in rows:
        key = base_name(row["company"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((row["company"], row["n"]))
        if len(out) >= limit:
            break
    return out


MIN_COUNT = 10   # the bar collectors/ats_watchlist.json states, in one place

# A matching name is only evidence if two companies are unlikely to share it.
# 'Cloudflare' and 'Fluidstack' are coined and effectively unique; 'Ditto',
# 'Assured', 'Corgi' and 'Uniti' are words, and words are held by more than one
# company. `ashby:ditto` published exactly "DITTO", matched our DITTO exactly,
# and is a different company: ours raised $6m for menstrual-health supplements
# and the board is hiring Bluetooth and database engineers. No name rule can
# separate those, so a short one-word name is sent to a human instead of being
# accepted or thrown away. Eight characters is where a sweep of 3,021 employers
# put it: every wrong board that pass produced — Ditto, Eve, Assured, Twenty,
# Weave and Symphony — is eight characters or fewer, and Symphony is why it is
# not seven. (Ours is the Indian air-cooler manufacturer; greenhouse:symphony
# is the encrypted-messaging company.) The price is reviewing Humanoid,
# Freehand, AirTrunk, Babylist and Supabase, all of which were right. It is a
# cut through a spectrum and not a fact about language, so it errs toward
# asking.
AMBIGUOUS_NAME_CHARS = 8


def needs_review(name: str) -> bool:
    """Whether a matching name is too common a word to stand on its own."""
    base = base_name(name)
    return bool(base) and len(base.split()) == 1 and len(base) <= AMBIGUOUS_NAME_CHARS


def dump_watchlist(payload: dict) -> str:
    """The watchlist as the file is actually written: one board per line.

    `json.dumps(indent=2)` would spread every entry over six lines, which at
    six hundred boards is a 3,600-line file whose diffs nobody reads. A board
    is one line, so adding forty of them is forty lines of review.
    """
    lines = ["{"]
    keys = list(payload)
    for position, key in enumerate(keys):
        value = payload[key]
        tail = "" if position == len(keys) - 1 else ","
        if isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f"  {json.dumps(key)}: [")
            group = None
            for index, entry in enumerate(value):
                # A blank line where the ATS changes. The file has always been
                # grouped that way and it is the only thing that makes six
                # hundred one-line entries navigable by eye.
                if group is not None and entry.get("ats") != group:
                    lines.append("")
                group = entry.get("ats")
                comma = "" if index == len(value) - 1 else ","
                lines.append("    " + json.dumps(entry, ensure_ascii=False) + comma)
            lines.append("  ]" + tail)
        else:
            body = json.dumps(value, indent=2, ensure_ascii=False)
            body = "\n".join(("  " + line) if line else line
                             for line in body.splitlines()).lstrip()
            lines.append(f"  {json.dumps(key)}: {body}{tail}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def merge_candidates(candidates: list[dict], watchlist: dict, *,
                     min_count: int = MIN_COUNT,
                     allow_slug_only: bool = False) -> tuple[list[dict], list[dict]]:
    """Fold verified candidates into a watchlist payload. Returns (added, refused).

    Four rules, all of them the file's own, applied here rather than by hand
    because a hundred entries is past where a human merge stays honest:

    * the bar. Under `min_count` open roles a quiet week empties the board and
      the collector cannot tell that from a dead slug.
    * name evidence. `slug_only` means nothing published a name anywhere, which
      is a human decision and not a merge.
    * one board per employer. A second board under a different slug would count
      the same employer twice in every aggregate.
    * the employer's name is OURS unless the board's own spelling normalises to
      the same `company_key`. The Recursion Pharmaceuticals board is titled
      'Recursion', which keys as a different employer, so taking the board's
      word for it would hang the volume panel on an empty second profile.
      Between two spellings that key the same, both of them the employer's own,
      the one that is not shouting wins: EDGAR files 'AGILYSYS, INC.' and the
      board says 'Agilysys', and only one of those belongs on a page.
    """
    from pipeline import vocab

    boards = list(watchlist.get("boards") or [])
    slugs = {f"{b['ats']}:{b['slug'].lower()}" for b in boards}
    keys = {vocab.company_key(b.get("company") or b["slug"]) for b in boards}

    added: list[dict] = []
    refused: list[dict] = []
    for hit in sorted(candidates, key=lambda h: -int(h.get("verified_count") or 0)):
        ours = hit["company"]
        published = hit.get("published_name")
        # Re-derived here rather than read off `evidence`. A candidates file is
        # a record of what the board published, and the rule that reads it can
        # tighten between the probe and the merge — as it did the day
        # `greenhouse:byd` turned out to say "BYD North America".
        agrees = bool(published) and names_agree(
            ours, published, exact=hit["ats"] in EXACT_NAME_MATCH)
        reason = None
        if int(hit.get("verified_count") or 0) < min_count:
            reason = f"under {min_count} open roles"
        elif published and not agrees:
            reason = f"the board publishes {published!r}"
        elif not published and not allow_slug_only:
            reason = "no published name to check the slug against"
        elif needs_review(ours) and not hit.get("reviewed"):
            reason = "one short word for a name; a human has to look"
        elif f"{hit['ats']}:{hit['slug'].lower()}" in slugs:
            reason = "already watched"
        elif vocab.company_key(ours) in keys:
            reason = "this employer already has a board"
        if reason:
            refused.append(dict(hit, refused_because=reason))
            continue

        same_employer = (published and vocab.company_key(published)
                         == vocab.company_key(ours))
        name = ours
        if same_employer and not (published.isupper() and not ours.isupper()):
            name = published
        entry = {"ats": hit["ats"], "slug": hit["slug"], "company": name,
                 "verified_count": int(hit["verified_count"])}
        if published and published != name:
            # The evidence travels with the entry. Without it the file says a
            # slug is this employer's and nothing records what the board
            # actually called itself when somebody checked.
            entry["published_name"] = published
        boards.append(entry)
        slugs.add(f"{entry['ats']}:{entry['slug'].lower()}")
        keys.add(vocab.company_key(name))
        added.append(entry)

    watchlist["boards"] = sorted(boards, key=lambda b: (b["ats"], b["slug"].lower()))
    return added, refused


SAMPLE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
}


def board_sample(ats: str, slug: str, limit: int = 8) -> list[tuple[str, str]]:
    """A few of the roles a board is advertising, as (title, where).

    What a company is HIRING FOR is the second signal a name cannot give. Two
    companies can both be called Ditto; only one of them is advertising
    Bluetooth and database engineers.
    """
    try:
        payload = _get(SAMPLE_URLS[ats].format(slug=slug))
    except (requests.RequestException, ValueError, KeyError):
        return []
    jobs = payload if isinstance(payload, list) else (payload.get("jobs") or [])
    out = []
    for job in jobs[:limit]:
        title = job.get("title") or job.get("text") or job.get("name") or ""
        where = (job.get("location")
                 or (job.get("categories") or {}).get("location") or "")
        if isinstance(where, dict):
            where = where.get("name") or where.get("city") or ""
        out.append((str(title)[:64], str(where)[:40]))
    return out


def employer_evidence(company: str, limit: int = 3) -> list[str]:
    """What we already hold on this employer, in one line each."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """select headline, industry, collector,
                  coalesce(nullif(hq_country,''), nullif(country,''), '?') place
             from signals where is_current = 1 and company = ? limit ?""",
        (company, limit)).fetchall()
    conn.close()
    return [f"[{r['collector']}] {r['industry'] or 'no industry'} "
            f"{r['place']} :: {r['headline'][:96]}" for r in rows]


def review(candidates: list[dict], *, pace: Pace, roles: int = 5) -> list[dict]:
    """Print every candidate whose name is too common a word to trust, beside
    what we hold on that employer and what the board is advertising.

    Deliberately terse, and biggest board first. Half of a wide sweep's hits can
    land here, and a review nobody finishes is a gate nobody applies: one line
    of ours, a handful of role titles, and the mismatch is usually plain at a
    glance — a menstrual-health supplements company does not advertise for
    Bluetooth engineers.
    """
    flagged = [c for c in candidates if needs_review(c["company"])]
    flagged.sort(key=lambda c: -int(c.get("verified_count") or 0))
    print(f"{len(flagged)} of {len(candidates)} candidates rest on one short "
          f"word for a name, biggest board first. Each needs an eye:\n")
    for hit in flagged:
        print(f"--- {hit['ats']}:{hit['slug']}  ours: {hit['company']!r}  "
              f"board says: {hit.get('published_name')!r}  "
              f"({hit['verified_count']} roles)")
        for line in (employer_evidence(hit["company"], 1)
                     or ["(nothing on file to compare)"]):
            print(f"    OURS   {line}")
        pace.wait(hit["ats"])
        sample = board_sample(hit["ats"], hit["slug"], roles)
        print("    BOARD  " + "; ".join(f"{title} ({where})" if where else title
                                        for title, where in sample)[:400])
    return flagged


def unconnected(boards: list[dict]) -> list[tuple[str, str]]:
    """Watched boards whose employer key holds no signals: (company, key).

    The point of watching an employer's board is that it makes a profile we
    already have richer. A board keyed to an employer with no signals renders
    on a page nobody arrives at — and the near miss is worse than the clean
    miss, because `greenhouse:coinbase` filed as "Coinbase" keys as `coinbase`
    while our filings are under `coinbase global`, so the board and the filings
    end up on two different pages for one company and neither page is complete.
    """
    from pipeline import vocab
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    held = {row[0] for row in conn.execute(
        "select distinct company_key from signals where is_current = 1")}
    conn.close()
    out = []
    for board in boards:
        name = board.get("company") or board["slug"]
        key = vocab.company_key(name)
        if key not in held:
            out.append((name, key))
    return out


def verify_board(entry: dict, *, pace: Pace, min_count: int) -> dict:
    """Re-check one WATCHED board: does it still fetch, still carry enough
    roles, and does it still publish this employer's name?

    The watchlist records the reading on the day an entry was added, and that
    is evidence about that day and no other. A slug can be renamed, a board can
    be taken down, and an account can be sold — none of which the collector can
    tell from a quiet week, because these APIs answer 200 with an empty list.
    """
    ats, slug = entry["ats"], entry["slug"]
    if ats not in PROBES:
        return dict(entry, state="unsupported",
                    detail=f"{ats} has no probe here")
    pace.wait(ats)
    try:
        result = PROBES[ats](slug)
    except (requests.RequestException, ValueError) as exc:
        return dict(entry, state="error", detail=f"{type(exc).__name__} {exc}")
    if not result:
        return dict(entry, state="gone", detail="no board, or nothing open")
    count, published = result
    ours = entry.get("company") or slug
    if published and not names_agree(ours, published,
                                     exact=ats in EXACT_NAME_MATCH):
        return dict(entry, state="wrong_company", published_name=published,
                    verified_count=count,
                    detail=f"publishes {published!r}, we file it as {ours!r}")
    if count < min_count:
        return dict(entry, state="small", verified_count=count,
                    detail=f"{count} open roles, bar is {min_count}")
    return dict(entry, state="ok", verified_count=count,
                published_name=published)


def probe_company(company: str, signals: int, *, allowed: list[str],
                  known: set[str], pace: Pace, min_count: int,
                  tally: Tally | None = None) -> dict | None:
    """Probe one employer across the allowed ATSs. First real board wins.

    Returns a hit, a rejection (`too_small` / `mismatch`), or None for "no board
    anywhere". Pure of the watchlist and of argparse so it can be tested, and
    thread-safe: everything it touches is either an argument or `pace`, which
    holds its own locks.
    """
    reject: dict | None = None
    for ats in allowed:
        for slug in slug_candidates(company):
            if f"{ats}:{slug.lower()}" in known:
                continue
            pace.wait(ats)
            try:
                result = PROBES[ats](slug)
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", "?")
                if tally is not None:
                    tally.add(f"{ats} HTTP {status}")
                continue
            except (requests.RequestException, ValueError) as exc:
                if tally is not None:
                    tally.add(f"{ats} {type(exc).__name__}")
                continue
            if not result:
                continue
            count, published = result
            exact = ats in EXACT_NAME_MATCH
            if published and not names_agree(company, published, exact=exact):
                # The board exists and belongs to somebody else. This is the
                # mis-attribution the tool is for: `uniti` on Ashby publishes
                # "Uniti AI", not the Uniti Group REIT whose filings we hold.
                reject = reject or {"outcome": "mismatch", "ats": ats,
                                    "slug": slug, "company": company,
                                    "published_name": published,
                                    "verified_count": count}
                continue
            evidence = "board_name" if published else "slug_only"
            record = {"ats": ats, "slug": slug, "company": company,
                      "verified_count": count, "evidence": evidence,
                      "published_name": published, "signals": signals}
            if count < min_count:
                # A real board, too small to watch. Recorded rather than
                # dropped: "has a board, it is just small" is worth knowing.
                record["outcome"] = "too_small"
                reject = record
                continue
            record["outcome"] = "hit"
            return record
    return reject


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=300,
                        help="how many tracked employers to try")
    parser.add_argument("--min-signals", type=int, default=1)
    parser.add_argument("--names", nargs="*", default=None,
                        help="probe these names instead of reading the database")
    parser.add_argument("--names-file", default=None,
                        help="a JSON list of names to probe. What --names is "
                             "for a handful, this is for a thousand: a second "
                             "pass on a slower ATS wants the employers the "
                             "fast ones found nothing for, and that list is a "
                             "file, not an argv")
    parser.add_argument("--ats", nargs="*", default=list(PROBES),
                        choices=list(PROBES))
    parser.add_argument("--collectors", nargs="*", default=None,
                        help="only employers whose signals came from these")
    parser.add_argument("--industries", nargs="*", default=None,
                        help="only employers classified into these industries")
    parser.add_argument("--countries", nargs="*", default=None,
                        help="only employers in these ISO country codes")
    parser.add_argument("--exclude-countries", nargs="*", default=None,
                        help="every employer EXCEPT these country codes")
    parser.add_argument("--min-count", type=int, default=MIN_COUNT,
                        help="open roles a board needs before it is proposed")
    parser.add_argument("--workers", type=int, default=4,
                        help="employers probed at once; each ATS host keeps its "
                             "own serialised pace regardless")
    parser.add_argument("--ledger", default=None,
                        help="JSON file of employers already probed; they are "
                             "skipped and the new ones appended")
    parser.add_argument("--out", default="scratchpad/ats_candidates.json")
    parser.add_argument("--review", nargs="*", default=None,
                        help="candidate files to print for human review: the "
                             "ones whose name is one common word")
    parser.add_argument("--verify", action="store_true",
                        help="re-check every board already on the watchlist "
                             "instead of probing for new ones")
    parser.add_argument("--merge", nargs="*", default=None,
                        help="candidate files to fold into the watchlist "
                             "instead of probing")
    parser.add_argument("--allow-slug-only", action="store_true",
                        help="merge candidates with no published name. A human "
                             "decision, one employer at a time, never a sweep")
    args = parser.parse_args()

    if args.review:
        candidates: list[dict] = []
        for path in args.review:
            candidates += json.loads(Path(path).read_text())
        flagged = review(candidates, pace=Pace(DELAY))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(flagged, indent=1) + "\n")
        print(f"{len(flagged)} written to {args.out}. Set \"reviewed\": true on "
              f"the ones that are the same company, then --merge that file; "
              f"the rest are simply not merged.")
        return 0

    if args.verify:
        boards = load_watchlist()
        pace = Pace(DELAY)
        states: dict[str, int] = {}
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for result in pool.map(
                    lambda b: verify_board(b, pace=pace,
                                           min_count=args.min_count), boards):
                results.append(result)
                states[result["state"]] = states.get(result["state"], 0) + 1
                if result["state"] != "ok":
                    print(f"  {result['state'].upper():14} "
                          f"{result['ats']}:{result['slug']} "
                          f"{result.get('company')} — {result['detail']}")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, indent=1) + "\n")
        roles = sum(int(r.get("verified_count") or 0) for r in results
                    if r["state"] == "ok")
        print(f"\n{len(boards)} watched boards: "
              + ", ".join(f"{n} {state}" for state, n
                          in sorted(states.items(), key=lambda kv: -kv[1]))
              + f"; {roles:,} open roles behind the ok ones -> {args.out}")
        orphans = unconnected(boards)
        if orphans:
            print(f"\n{len(orphans)} of them key to an employer we hold no "
                  f"signals for, so their volume renders on a page with "
                  f"nothing else on it:")
            for name, key in orphans:
                print(f"    {name}  ->  company_key {key!r}")
        return 0 if states.get("ok") == len(boards) else 2

    if args.merge is not None:
        payload = json.loads(WATCHLIST_PATH.read_text())
        before = len(payload.get("boards") or [])
        candidates: list[dict] = []
        for path in args.merge:
            candidates += json.loads(Path(path).read_text())
        added, refused = merge_candidates(
            candidates, payload, min_count=args.min_count,
            allow_slug_only=args.allow_slug_only)
        WATCHLIST_PATH.write_text(dump_watchlist(payload))
        counts: dict[str, int] = {}
        for item in refused:
            counts[item["refused_because"]] = counts.get(item["refused_because"], 0) + 1
        print(f"{len(candidates)} candidates -> {len(added)} added, "
              f"{len(refused)} refused; watchlist {before} -> "
              f"{len(payload['boards'])} boards")
        for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4d} {reason}")
        return 0

    allowed = []
    for ats in args.ats:
        if robots_allows(ROBOTS_PROBE[ats]):
            allowed.append(ats)
        else:
            print(f"SKIP {ats}: robots.txt disallows the board endpoint")
    if not allowed:
        print("Every requested ATS is disallowed by robots.txt.")
        return 1

    if args.names_file:
        companies = [(str(name), 0)
                     for name in json.loads(Path(args.names_file).read_text())]
    elif args.names:
        companies = [(name, 0) for name in args.names]
    else:
        companies = companies_from_db(args.limit, args.min_signals,
                                      args.collectors, args.industries,
                                      args.countries, args.exclude_countries)

    known = {f"{b['ats']}:{b['slug'].lower()}" for b in load_watchlist()}
    known_names = {base_name(b.get("company") or "") for b in load_watchlist()}

    ledger_path = Path(args.ledger) if args.ledger else None
    probed: set[str] = set()
    if ledger_path and ledger_path.exists():
        try:
            probed = {str(name) for name in json.loads(ledger_path.read_text())}
        except (OSError, ValueError):
            # A ledger that cannot be read means re-probing, which is wasteful
            # but never wrong. Refusing the run would be the worse failure.
            print(f"NOTE {ledger_path} unreadable; probing the whole pool")

    queue = [(name, n) for name, n in companies
             if base_name(name) not in known_names and name not in probed]
    print(f"{len(companies)} employers ({len(companies) - len(queue)} already "
          f"watched or already probed), {len(queue)} to probe, "
          f"ATS: {', '.join(allowed)}, {args.workers} workers, "
          f"bar {args.min_count}+ open roles, "
          f"{len(known)} boards on the watchlist ({WATCHLIST_PATH.name})")

    pace = Pace(DELAY)
    tally = Tally()
    hits: list[dict] = []
    rejects: list[dict] = []
    lock = threading.Lock()
    done = 0
    started = time.monotonic()

    def work(item: tuple[str, int]) -> None:
        nonlocal done
        company, signals = item
        result = probe_company(company, signals, allowed=allowed, known=known,
                               pace=pace, min_count=args.min_count, tally=tally)
        with lock:
            done += 1
            if result and result["outcome"] == "hit":
                hits.append(result)
                print(f"[{done}/{len(queue)}] HIT {result['ats']}:{result['slug']} "
                      f"{company} — {result['verified_count']} postings "
                      f"({result['evidence']})")
            elif result:
                rejects.append(result)
                if result["outcome"] == "mismatch":
                    print(f"  MISMATCH {result['ats']}:{result['slug']} publishes "
                          f"{result['published_name']!r}, we asked for {company!r}")
                else:
                    print(f"  TOO SMALL {result['ats']}:{result['slug']} {company} "
                          f"— {result['verified_count']} postings "
                          f"(bar is {args.min_count})")
            if done % 250 == 0:
                rate = done / max(time.monotonic() - started, 1e-6) * 3600
                print(f"  ... {done}/{len(queue)} probed, {len(hits)} boards, "
                      f"{rate:,.0f}/hour")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, queue))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(hits, indent=1) + "\n")
    if rejects:
        reject_path = Path(args.out).with_name(Path(args.out).stem + "_rejected.json")
        reject_path.write_text(json.dumps(rejects, indent=1) + "\n")
        print(f"{len(rejects)} rejected -> {reject_path}")
    if ledger_path:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(
            sorted(probed | {name for name, _ in queue}), indent=0) + "\n")

    strong = [h for h in hits if h["evidence"] == "board_name"]
    small = [r for r in rejects if r["outcome"] == "too_small"]
    wrong = [r for r in rejects if r["outcome"] == "mismatch"]
    print(f"\n{len(hits)} boards found ({len(strong)} with published-name "
          f"evidence, {len(hits) - len(strong)} slug-only) -> {args.out}")
    print(f"rejected: {len(small)} under {args.min_count} roles, "
          f"{len(wrong)} publishing somebody else's name")
    # A pass that was throttled and a pool that has no boards look identical in
    # the hit count. This is the line that tells them apart.
    print("errors swallowed: " + (tally.report() if tally else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

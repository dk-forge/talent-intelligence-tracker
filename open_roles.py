#!/usr/bin/env python3
"""Open roles now: role titles that link STRAIGHT to the employer's apply page.

    python3 open_roles.py --stdout                 # look at the payload
    python3 open_roles.py --publish                # POST it to the live site
    python3 open_roles.py --publish --no-link-check

Owner rulings this module exists to keep:

* 2026-11-12: NO PUBLIC JOB LISTINGS. Nothing here creates a job page on our
  site, and nothing emits schema.org JobPosting markup. A role is one line:
  company, title, city, and an outbound link to the employer's OWN posting on
  its Greenhouse, Ashby or Lever board. Our site never becomes the destination.
* 2026-11-13 ("yes, employer links"): show "Open roles now: N" plus up to five
  titles on each company page, and an "Open roles now" section in the daily and
  weekly emails, refreshed daily, closed roles gone within 24 hours.

WHERE THE DATA COMES FROM. Not a second scraper. `collectors/ats_boards.py`
already reads every board on the watchlist once a day to count postings; it now
keeps four more fields per posting (title, url, location text, posted) and
hands them here. Only three providers qualify, decided by their terms and
recorded in docs/SOURCE-TERMS-open-roles.md: greenhouse, lever, ashby. Workable
and SmartRecruiters never reach a reader through this path, and Jooble stays
off until its written OK.

HOW A CLOSED ROLE DISAPPEARS WITHIN 24 HOURS. Two mechanisms, either sufficient:

1. Only boards read successfully TODAY contribute rows, and the board API is
   the employer's system of record: a filled or withdrawn role is gone from it.
   The whole set is replaced on every publish, never merged.
2. Every link we would actually SHOW (five per company, the email pool) is
   re-requested here before publishing, and a 404 or 410 drops the role. A
   timeout or a 5xx keeps it: the board listed it minutes ago, and a flaky
   employer careers site is not evidence the role closed.

And the plugin hides the whole block when the snapshot is older than
TIT_OPEN_ROLES_MAX_AGE_H (30h), so a failed collection shows nothing rather
than yesterday's roles.

This module never writes the database and never calls a model. The role-level
file (data/open_roles_today.json) is gitignored on purpose: the repo is public
and an archive of individual adverts is a listings archive. Only daily
aggregate counts are kept, inside data/ats_board_state.json, for the trends.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from pipeline import vocab

REPO_ROOT = Path(__file__).resolve().parent

# The providers whose terms allow showing titles and outbound links. Decided in
# docs/SOURCE-TERMS-open-roles.md; adding one here without a row there is the
# defect tests/test_open_roles.py exists to catch.
ALLOWED_ATS = ("greenhouse", "lever", "ashby")

COMPANY_ROLES = 5          # titles per company page
POOL_PER_CELL = 10         # roles per (country, family) cell for the emails
POOL_PER_COMPANY = 2       # one employer may not fill an email section
TREND_DAYS = 120           # daily aggregates kept in the state file
TITLE_MAX = 110

# Our own host must never be the link target: a role links to the employer.
OWN_HOSTS = ("asktherecruiter.com",)


def today_path() -> Path:
    from collectors import ats_boards
    return Path(os.environ.get("TIT_OPEN_ROLES")
                or ats_boards.STATE_PATH.with_name("open_roles_today.json"))


# --- One role -------------------------------------------------------------

_WS = re.compile(r"\s+")


def clean_title(title: str) -> str:
    t = _WS.sub(" ", str(title or "")).strip()
    if len(t) > TITLE_MAX:
        t = t[:TITLE_MAX - 1].rstrip() + "…"
    return t


def link_ok(url: str) -> bool:
    """An https link to somebody else's host. Anything else is not shown."""
    try:
        parts = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    return not any(host == h or host.endswith("." + h) for h in OWN_HOSTS)


def place_of(posting: dict) -> tuple[str, str, str, bool]:
    """(city, state, country, remote) from what the collector already parsed.

    The city and country come from the same `place_key` the counts use, so an
    email's per-city number and a city page's number are one reading. The US
    state is read from the curated city first, then from the location text.
    """
    from collectors import ats_boards

    key = posting.get("place") or ""
    kind, _, value = key.partition(":")
    city = country = state = ""
    if kind == "city":
        city = value
        hit = vocab.normalize_city(value)
        country = hit[2] if hit else ""
    elif kind == "country":
        country = value
    remote = kind == "remote" or posting.get("mode") == "remote"
    if country == "US":
        state = vocab.state_for_city(city) or ""
        if not state:
            for text in (posting.get("state_text") or "", posting.get("location_text") or ""):
                for token in ats_boards._SPLIT.split(text):
                    code = vocab.normalize_state(ats_boards._ZIP.sub("", token.strip()))
                    if code:
                        state = code
                        break
                if state:
                    break
    return city, state, country, remote


def roles_from_board(entry: dict, record: dict, postings: list[dict]) -> list[dict]:
    """The showable roles from one board read today. Nothing else."""
    ats = entry.get("ats")
    if ats not in ALLOWED_ATS:
        return []
    out = []
    seen = set()
    for p in postings:
        title = clean_title(p.get("title", ""))
        url = str(p.get("url") or "").strip()
        if not title or not link_ok(url) or url in seen:
            continue
        seen.add(url)
        city, state, country, remote = place_of(p)
        out.append({
            "company": record.get("company") or entry.get("company") or entry.get("slug"),
            "company_key": record.get("company_key") or vocab.company_key(entry.get("company") or ""),
            "ats": ats,
            "title": title,
            "url": url,
            "city": city,
            "state": state,
            "country": country,
            "remote": remote,
            "family": p.get("function") or "",
            "posted": str(p.get("posted") or "")[:10],
        })
    return out


# --- Counts and trend -----------------------------------------------------

def _bump(d: dict, k: str) -> None:
    if k:
        d[k] = d.get(k, 0) + 1


def aggregate(roles: list[dict]) -> dict:
    agg = {"total": len(roles), "employers": len({r["company_key"] for r in roles}),
           "country": {}, "state": {}, "city": {}, "family": {}, "country_family": {},
           "country_city": {}}
    for r in roles:
        _bump(agg["country"], r["country"])
        if r["country"] == "US":
            _bump(agg["state"], r["state"])
        _bump(agg["city"], r["city"])
        _bump(agg["family"], r["family"])
        if r["country"] and r["family"]:
            _bump(agg["country_family"].setdefault(r["country"], {}), r["family"])
        if r["country"] and r["city"]:
            _bump(agg["country_city"].setdefault(r["country"], {}), r["city"])
    return agg


def record_trend(store: dict, roles: list[dict], day: str) -> None:
    """Keep the day's COUNTS (never the roles) for the weekly trend lines."""
    agg = aggregate(roles)
    agg.pop("country_family", None)
    agg.pop("country_city", None)
    agg.pop("city", None)       # cities are the long tail; country and family carry the trend
    trend = store.setdefault("open_roles_trend", {})
    trend[day] = agg
    for old in sorted(trend)[:-TREND_DAYS]:
        del trend[old]


def save_today(roles: list[dict], day: str, *, read: int = 0, failed: int = 0) -> Path:
    path = today_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "checked_on": day,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "boards_read": read, "boards_failed": failed, "roles": roles,
    }, sort_keys=True) + "\n")
    print(f"[open_roles] {len(roles)} showable roles from {read} boards -> {path}")
    return path


def trend_vs(store: dict, day: str, days_back: int = 7) -> dict | None:
    """The aggregate recorded `days_back` days before `day` (or the nearest
    earlier one within two days), or None. Never interpolated."""
    trend = store.get("open_roles_trend") or {}
    target = date.fromisoformat(day) - timedelta(days=days_back)
    for slack in range(0, 3):
        k = (target - timedelta(days=slack)).isoformat()
        if k in trend:
            return dict(trend[k], date=k)
    return None


# --- Choosing what is shown -----------------------------------------------

def _rank_key(r: dict):
    # Newest posting first; a role with no date sorts last, then by title so
    # the choice is stable between two runs on the same data.
    return (r.get("posted") or "0000", r["title"])


def company_blocks(roles: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in roles:
        by.setdefault(r["company_key"], []).append(r)
    out = {}
    for key, rs in by.items():
        rs.sort(key=_rank_key, reverse=True)
        out[key] = {"company": rs[0]["company"], "count": len(rs),
                    "roles": [_slim(r) for r in rs[:COMPANY_ROLES]]}
    return out


def _slim(r: dict) -> dict:
    return {k: r[k] for k in ("company", "company_key", "title", "url", "city",
                              "state", "country", "remote", "family", "posted")}


def email_pool(roles: list[dict]) -> list[dict]:
    """Enough roles that any (country, family) choice a reader makes can fill
    ten lines, with no employer taking more than two of them."""
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in sorted(roles, key=_rank_key, reverse=True):
        for cell in ((r["country"], r["family"]), (r["country"], ""),
                     ("", r["family"]), ("", "")):
            bucket = cells.setdefault(cell, [])
            if len(bucket) >= POOL_PER_CELL:
                continue
            if sum(1 for x in bucket if x["company_key"] == r["company_key"]) >= POOL_PER_COMPANY:
                continue
            bucket.append(r)
    seen = {}
    for bucket in cells.values():
        for r in bucket:
            seen[r["url"]] = r
    return sorted((_slim(r) for r in seen.values()), key=_rank_key, reverse=True)


# --- Link re-check --------------------------------------------------------

DEAD = (404, 410)


# Each job-board host gets ONE serial queue and its own delay. Lever publishes
# Crawl-delay: 1 (the collector honours it on api.lever.co, ats_boards.ATS_DELAY),
# and the first live run broke it here by firing 8 requests at once at the same
# hosts, then ran ~10 minutes before the self-hosted runner lost its
# connection (2026-11-13, run 43468894602).
HOST_DELAY = {"jobs.lever.co": 1.1}
DEFAULT_HOST_DELAY = 0.25
LINK_TIMEOUT = (4, 6)       # connect, read: bounds every hop of every request
MAX_REDIRECTS = 5


def _status(url: str, timeout) -> int | None:
    """HEAD, redirects followed (at most MAX_REDIRECTS). No GET fallback: a
    body is never needed to tell a live page from a 404/410, and a streamed GET
    is how a slow server holds a worker open."""
    import requests
    from collectors import ats_boards
    try:
        with requests.Session() as s:
            s.max_redirects = MAX_REDIRECTS
            resp = s.head(url, headers={"User-Agent": ats_boards.USER_AGENT},
                          timeout=timeout, allow_redirects=True)
            return resp.status_code
    except Exception:  # noqa: BLE001 - any failure is "could not tell"
        return None


def check_links(urls: list[str], *, budget_s: float = 300, timeout=LINK_TIMEOUT,
                delays: dict | None = None, status=_status) -> dict:
    """{url: status, None (could not tell) or "skipped" (deadline)}.

    One thread per host, requests in series with the host's delay between
    them, so no host ever sees two at once. After `budget_s` every remaining
    url is "skipped": kept, because each role was on the employer's own board
    API minutes earlier, and a re-check that ran out of time has found nothing.
    """
    delays = HOST_DELAY if delays is None else delays
    deadline = time.monotonic() + budget_s
    by_host: dict[str, list[str]] = {}
    for u in urls:
        by_host.setdefault(urlparse(u).hostname or "", []).append(u)
    results: dict[str, int | None | str] = {u: "skipped" for u in urls}

    def walk(host: str, queue: list[str]) -> None:
        gap = delays.get(host, DEFAULT_HOST_DELAY)
        for n, u in enumerate(queue):
            if n and gap:
                time.sleep(gap)
            if time.monotonic() >= deadline:
                return
            results[u] = status(u, timeout)

    threads = [threading.Thread(target=walk, args=(h, q), daemon=True)
               for h, q in by_host.items()]
    for t in threads:
        t.start()
    for t in threads:
        # A request in flight at the deadline gets at most one more bounded
        # request's worth of time; a daemon thread never holds the process.
        t.join(max(0.0, deadline - time.monotonic()) + 1.0)
    return dict(results)


# --- The payload ----------------------------------------------------------

def build(snapshot: dict, store: dict, *, links: dict | None = None) -> dict:
    roles = list(snapshot.get("roles") or [])
    dropped = 0
    if links:
        dead = {u for u, s in links.items() if s in DEAD}
        dropped = sum(1 for r in roles if r["url"] in dead)
        roles = [r for r in roles if r["url"] not in dead]
    day = snapshot["checked_on"]
    agg = aggregate(roles)
    week_ago = trend_vs(store, day, 7)
    return {
        "as_of": snapshot["as_of"],
        "checked_on": day,
        "sources": list(ALLOWED_ATS),
        "totals": {"roles": agg["total"], "employers": agg["employers"]},
        "by_country": agg["country"],
        "by_state": agg["state"],
        "by_city": agg["city"],
        "by_family": agg["family"],
        "by_country_family": agg["country_family"],
        "by_country_city": agg["country_city"],
        "week_ago": week_ago,
        "family_labels": dict(vocab.FUNCTION_LABELS),
        "companies": company_blocks(roles),
        "pool": email_pool(roles),
        "link_check": {
            "checked": sum(1 for s in (links or {}).values() if isinstance(s, int)),
            "unchecked": sum(1 for s in (links or {}).values() if not isinstance(s, int)),
            "dropped": dropped,
        },
        "boards_read": snapshot.get("boards_read", 0),
        "boards_failed": snapshot.get("boards_failed", 0),
    }


def shown_urls(snapshot: dict) -> list[str]:
    """Only the links a reader could actually click: the five per company and
    the email pool. Checking all ~30,000 would be a crawl, not a check."""
    roles = snapshot.get("roles") or []
    urls = {r["url"] for b in company_blocks(roles).values() for r in b["roles"]}
    urls |= {r["url"] for r in email_pool(roles)}
    return sorted(urls)


def publish(payload: dict) -> dict:
    import requests

    from pipeline.publish import TIMEOUT, USER_AGENT, PublishError, _config

    site, key = _config()
    resp = requests.post(f"{site}/wp-json/talent/v1/open-roles", json=payload,
                         headers={"X-Talent-API-Key": key, "User-Agent": USER_AGENT},
                         timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise PublishError(f"{resp.status_code}: {resp.text[:300]}")
    return resp.json()


def main(argv=None) -> int:
    from collectors import ats_boards

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot", default=None, help="role file the collector wrote")
    ap.add_argument("--state", default=None)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--out", default=None, help="write the payload here too")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--no-link-check", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.snapshot) if args.snapshot else today_path()
    if not path.exists():
        print(f"no role snapshot at {path}: ats_boards has not run on this checkout",
              file=sys.stderr)
        return 1
    snapshot = json.loads(path.read_text())
    store = ats_boards.load_state(Path(args.state) if args.state else None)

    links = None
    if not args.no_link_check:
        urls = shown_urls(snapshot)
        links = check_links(urls)
        dead = sum(1 for s in links.values() if s in DEAD)
        print(f"[open_roles] re-checked {len(urls)} shown links: {dead} closed (404/410)")
    payload = build(snapshot, store, links=links)
    # No summary of the payload is printed here. Its counts derive from the
    # postings' address fields, which CodeQL classes as private data
    # (py/clear-text-logging, alert #53 on PR #332); the site's own reply,
    # printed after --publish, carries the same counts.
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    if args.stdout:
        json.dump(payload, sys.stdout, indent=1)
        print()
    if args.publish:
        if not os.environ.get("WP_API_KEY"):
            print("WP_API_KEY is not set: nothing was published.", file=sys.stderr)
            return 1
        print(f"[open_roles] published: {publish(payload)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

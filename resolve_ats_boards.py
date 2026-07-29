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
* robots.txt is checked before the first request to a host, with the same
  function the press collector uses. SmartRecruiters is absent from this tool on
  purpose: `https://api.smartrecruiters.com/robots.txt` is `Disallow: /` for
  every agent except LinkedInBot.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

from collectors.ats_boards import (USER_AGENT, WATCHLIST_PATH, load_watchlist,
                                   robots_allows)

REPO_ROOT = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / "data" / "talent_intel.db"
TIMEOUT = 30

# Per-ATS pause between requests. Lever's robots.txt states `Crawl-delay: 1`,
# so its number is theirs and not a preference of ours.
DELAY = {"greenhouse": 0.3, "lever": 1.1, "ashby": 0.3, "workable": 0.5}

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


def names_agree(ours: str, theirs: str) -> bool:
    a, b = base_name(ours), base_name(theirs)
    if not a or not b:
        return False
    a_joined, b_joined = a.replace(" ", ""), b.replace(" ", "")
    return a_joined == b_joined or a_joined in b_joined or b_joined in a_joined


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


def probe_lever(slug: str):
    payload = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(payload, list) or not payload:
        return None
    name = None
    try:
        page = requests.get(f"https://jobs.lever.co/{slug}",
                            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if page.status_code == 200:
            match = re.search(r"<title>([^<]{1,120})</title>", page.text, re.I)
            if match:
                name = match.group(1).strip()
    except requests.RequestException:
        name = None
    return len(payload), name


def probe_ashby(slug: str):
    payload = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(payload, dict):
        return None
    jobs = [j for j in (payload.get("jobs") or []) if j.get("isListed") is not False]
    # Ashby publishes no organisation name on this endpoint, so there is no
    # name evidence to be had. That is what `slug_only` means downstream.
    return (len(jobs), None) if jobs else None


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
                      industries: list[str] | None = None) -> list[tuple[str, int]]:
    """Employers we already hold signals for, most-signalled first.

    `collectors` narrows the pool. It matters: the pay-gap import alone
    contributes several thousand UK public bodies, and probing an NHS trust for
    a Greenhouse board is a request nobody benefits from.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = ["is_current = 1", "company IS NOT NULL", "company != ''"]
    params: list = []
    if collectors:
        where.append("collector IN (%s)" % ",".join("?" * len(collectors)))
        params += collectors
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=300,
                        help="how many tracked employers to try")
    parser.add_argument("--min-signals", type=int, default=1)
    parser.add_argument("--names", nargs="*", default=None,
                        help="probe these names instead of reading the database")
    parser.add_argument("--ats", nargs="*", default=list(PROBES),
                        choices=list(PROBES))
    parser.add_argument("--collectors", nargs="*", default=None,
                        help="only employers whose signals came from these")
    parser.add_argument("--industries", nargs="*", default=None,
                        help="only employers classified into these industries")
    parser.add_argument("--out", default="scratchpad/ats_candidates.json")
    args = parser.parse_args()

    allowed = []
    for ats in args.ats:
        if robots_allows(ROBOTS_PROBE[ats]):
            allowed.append(ats)
        else:
            print(f"SKIP {ats}: robots.txt disallows the board endpoint")
    if not allowed:
        print("Every requested ATS is disallowed by robots.txt.")
        return 1

    if args.names:
        companies = [(name, 0) for name in args.names]
    else:
        companies = companies_from_db(args.limit, args.min_signals,
                                      args.collectors, args.industries)

    known = {f"{b['ats']}:{b['slug'].lower()}" for b in load_watchlist()}
    known_names = {base_name(b.get("company") or "") for b in load_watchlist()}

    print(f"{len(companies)} employers, ATS: {', '.join(allowed)}, "
          f"{len(known)} boards already on the watchlist ({WATCHLIST_PATH.name})")

    hits: list[dict] = []
    last: dict[str, float] = {}
    for index, (company, signals) in enumerate(companies, 1):
        if base_name(company) in known_names:
            continue
        slugs = slug_candidates(company)
        found = False
        for ats in allowed:
            if found:
                break
            for slug in slugs:
                if f"{ats}:{slug.lower()}" in known:
                    continue
                elapsed = time.monotonic() - last.get(ats, 0.0)
                if ats in last and elapsed < DELAY[ats]:
                    time.sleep(DELAY[ats] - elapsed)
                try:
                    result = PROBES[ats](slug)
                except (requests.RequestException, ValueError):
                    result = None
                finally:
                    last[ats] = time.monotonic()
                if not result:
                    continue
                count, published = result
                evidence = ("board_name" if published and names_agree(company, published)
                            else "slug_only")
                if published and not names_agree(company, published):
                    # The board exists and belongs to somebody else. Say so and
                    # move on; this is the mis-attribution this tool is for.
                    print(f"  MISMATCH {ats}:{slug} publishes {published!r}, "
                          f"we asked for {company!r}")
                    continue
                hits.append({"ats": ats, "slug": slug, "company": company,
                             "verified_count": count, "evidence": evidence,
                             "published_name": published, "signals": signals})
                print(f"[{index}/{len(companies)}] HIT {ats}:{slug} "
                      f"{company} — {count} postings ({evidence})")
                found = True
                break

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(hits, indent=1) + "\n")
    strong = [h for h in hits if h["evidence"] == "board_name"]
    print(f"\n{len(hits)} boards found ({len(strong)} with published-name "
          f"evidence, {len(hits) - len(strong)} slug-only) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

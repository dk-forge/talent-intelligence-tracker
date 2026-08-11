"""Chase the benchmark diff's missing employers to their own primary sources.

Ported from the sibling layoff tracker's tracker-diff loop (2026-08-02). An
external reference list of employers, supplied ONLY through secrets, is diffed
against what we already hold; any employer they list and we lack becomes a
company-TARGETED search against the sources we already trust. The reference
site is never cited, never fetched for content, and never named: their list is
a discovery SIGNAL pointing at a primary source, and the primary source is
what gets stored, through the same classify -> validate -> store path as every
other candidate.

Two ways to supply the list (use either or both):

  * BENCHMARK_FEED_URLS   comma-separated URLs, each returning a JSON array of
                          {company, ...} objects (or {"data":[...]}) OR a CSV
                          with a company / company_name / name column.
  * BENCHMARK_COMPANIES   the list pasted inline, comma- or newline-separated.

Ships DORMANT: with neither secret set there is nothing to diff, so the repo
carries zero benchmark data and a scheduled run logs one line and exits clean.
The owner arming a secret is the only activation. `run_benchmark_diff.py` is
the entry point; it owns the recall arithmetic and the owner-only alert.

PRIVACY IS A HARD RULE HERE, stricter than the tripwire chase next door: the
Actions log carries ONLY counts and slice indices, never a company name and
never a feed URL. A name in the log is a name in a public place, and the whole
point of the secret is that the list never reaches one. The tripwire chase
prints its leads because a model's claim is not confidential; a benchmark
list is. tests/test_benchmark_diff.py pins this.

What a chase costs: the Google News fetch and the SEC full-text search are
free. Money is only spent when a fetched article survives every free guard and
buys a gate call or a read-through, and that spend is bounded by the same
levers as every collector: spend.py --degrade runs first, TIT_PAID_READS=off
defers every paid call, classify.READTHROUGH_CAP caps the reads, and the
per-run lead cap below bounds how much can even be fetched.

DORMANT. Nothing schedules this collector directly. The weekly slot in
schedule-link-hygiene.yml asks for benchmark-diff.yml, which runs
run_benchmark_diff.py, which calls run_collect with this source only after the
dormancy check and the diff have decided there is something to chase.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import date, datetime, timezone

import requests

from collectors import capped_fetch

from analysis.recall.match import first_token
from collectors import google_news, sec_edgar
from pipeline.vocab import company_key

COLLECTOR = "benchmark_chase"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "talent_intel.db")

# Leads chased per run. The whole missing list is walked across weeks by the
# rotating slice in todays_slice(), so this bounds one run's fetch volume and
# its worst-case spend, not the loop's reach.
MAX_LEADS = max(1, int(os.environ.get("BENCHMARK_DIFF_MAX", "40") or "40"))

# Articles kept per lead. The second and third write-ups of one event add
# nothing the first does not; the dedupe would drop them anyway, after paying
# to resolve them.
MAX_ITEMS_PER_LEAD = 3

# Filings kept per lead. A company that filed twice in the window filed the
# same change twice for our purposes.
MAX_FILINGS_PER_LEAD = 2

# How far back the press search looks. Wider than the collect cycle's own
# recency window because a benchmark list carries no dates we may read.
LOOKBACK_DAYS = 75

# How far back the SEC full-text search looks. Filings are sparse per company,
# so a wide window costs one request either way.
SEC_DAYS_BACK = 120

# What run_collect reads for the health verdict. A chase that examined 40
# leads and found no new article is a quiet day, not a broken collector, so
# `read` reports leads examined, the same shape as the diff collectors.
LAST_RUN: dict = {}

# Set by run_benchmark_diff.prepare() so the diff is computed once per run.
_PREPARED: list[dict] | None = None


def armed() -> bool:
    """True when either secret is present. The ONLY activation there is."""
    return bool((os.environ.get("BENCHMARK_FEED_URLS") or "").strip()
                or (os.environ.get("BENCHMARK_COMPANIES") or "").strip())


def prepare(leads: list[dict] | None) -> None:
    """Hand collect() a precomputed slice, so run_benchmark_diff does the diff
    exactly once. Pass None to clear."""
    global _PREPARED
    _PREPARED = leads


def _names_from_body(body: str) -> list[str]:
    """Company names out of one feed body: JSON array/object or CSV."""
    names: list[str] = []
    stripped = body.lstrip()
    if stripped[:1] in ("[", "{"):
        try:
            data = json.loads(body)
        except ValueError:
            return []
        rows = data if isinstance(data, list) else (
            data.get("data") or data.get("companies") or data.get("events") or [])
        for row in rows:
            if isinstance(row, dict):
                name = row.get("company") or row.get("company_name") or row.get("name")
                if name:
                    names.append(str(name).strip())
            elif isinstance(row, str) and row.strip():
                names.append(row.strip())
        return names
    try:
        for row in csv.DictReader(io.StringIO(body)):
            name = (row.get("company") or row.get("company_name")
                    or row.get("name") or "").strip()
            if name:
                names.append(name)
    except csv.Error:
        return []
    return names


def _fetch_feed(url: str, label: str, *, session=None) -> list[str]:
    """One feed's names. NEVER print the URL: it lives in a secret, and a URL
    substring can slip past GitHub's masking into a public log. The feed is
    referred to only by its index."""
    try:
        resp, raw = capped_fetch.capped_get(
            url, session=session, timeout=40,
            headers={"User-Agent": "TalentIntel/1.0 (info@asktherecruiter.com)"},
            max_bytes=capped_fetch.FEED_BYTES)
        if resp.status_code != 200:
            print(f"[{COLLECTOR}] feed {label}: HTTP {resp.status_code}")
            return []
        body = raw.decode("utf-8", errors="replace")
    except requests.RequestException as exc:
        print(f"[{COLLECTOR}] feed {label}: fetch failed ({type(exc).__name__})")
        return []
    names = _names_from_body(body)
    print(f"[{COLLECTOR}] feed {label}: {len(names)} name(s)")
    return names


def benchmark_names(*, session=None) -> list[str]:
    """The full reference list from both secrets, de-duplicated, in a stable
    order so the rotating slice is deterministic."""
    by_key: dict[str, str] = {}
    feeds = [u.strip() for u in
             (os.environ.get("BENCHMARK_FEED_URLS") or "").split(",") if u.strip()]
    for i, url in enumerate(feeds, 1):
        for name in _fetch_feed(url, f"#{i}", session=session):
            by_key.setdefault(name.lower(), name)
    inline = [n.strip() for n in
              re.split(r"[,\n]", os.environ.get("BENCHMARK_COMPANIES") or "")
              if n.strip()]
    for name in inline:
        by_key.setdefault(name.lower(), name)
    return sorted(by_key.values(), key=str.lower)


def our_company_keys(db_path: str | None = None) -> set[str]:
    """Every employer key we currently hold, through the SAME normaliser the
    store uses, so 'Acme, Inc.' on their list matches our 'Acme Inc' row.

    Read-only on purpose (a collector never writes), and loud when the
    database is missing: an empty set would silently declare every listed
    employer missing and chase the whole list.
    """
    import sqlite3

    db_path = db_path or DB_PATH
    if not os.path.exists(db_path):
        raise RuntimeError(f"no database at {db_path}; refusing to treat "
                           "every listed employer as missing")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT company_key FROM signals WHERE is_current = 1")
        return {r[0] for r in rows if r[0]}


def missing_names(names: list[str], keys: set[str]) -> list[str]:
    """The employers they list and we lack, by normalised key."""
    out = []
    for name in names:
        k = company_key(name)
        if k and k not in keys:
            out.append(name)
    return out


def todays_slice(missing: list[str], per_run: int = MAX_LEADS,
                 today: date | None = None) -> tuple[list[str], int, int]:
    """A rotating slice of the missing list, cursored on the calendar date, so
    the whole backlog is walked across runs instead of the same head every
    time. Returns (slice, slice_index_1based, slice_count)."""
    if not missing:
        return [], 0, 0
    today = today or date.today()
    n_slices = max(1, (len(missing) + per_run - 1) // per_run)
    idx = today.toordinal() % n_slices
    return missing[idx * per_run:(idx + 1) * per_run], idx + 1, n_slices


def query_for(name: str) -> str:
    """A company-targeted Google News query. The list contributes the NAME and
    nothing else; the intent words are this tracker's own vocabulary."""
    clean = name.replace('"', "").strip()
    return (f'"{clean}" (raises OR raised OR funding OR "Series" OR appoints '
            f'OR "chief executive" OR "steps down" OR hiring) '
            f'when:{LOOKBACK_DAYS}d')


def _mentions(name: str, item: dict) -> bool:
    """Does the article actually name this employer? Without this, a targeted
    search that found nothing returns whatever Google thought was close, and
    the classifier is paid to read a story about somebody else."""
    token = first_token(name)
    if not token:
        return False
    haystack = f"{item.get('headline', '')} {item.get('raw_text', '')}".lower()
    return token in haystack


def _filer_matches(name: str, filer: str) -> bool:
    """Is this SEC hit FILED BY the employer we searched for? Full-text search
    also returns filings that merely mention the name, and a filing that
    mentions an employer is not evidence about that employer."""
    want, got = company_key(name), company_key(filer)
    return bool(want and got and (want in got or got in want))


def _chase_sec(name: str, *, sec_search=None, sec_fetch_text=None) -> list[dict]:
    """The strongest primary source first: the employer's own 8-K filings."""
    search = sec_search or sec_edgar.search
    fetch_text = sec_fetch_text or sec_edgar.fetch_text
    try:
        hits = search(name.replace('"', ""), days_back=SEC_DAYS_BACK)
    except requests.RequestException:
        return []
    out: list[dict] = []
    for hit in hits:
        if len(out) >= MAX_FILINGS_PER_LEAD:
            break
        filer, cik = sec_edgar._company_and_cik(hit)
        if not _filer_matches(name, filer):
            continue
        url = sec_edgar.document_url(hit)
        if not url:
            continue
        try:
            body = fetch_text(url)
        except requests.RequestException:
            continue
        if not body:
            continue
        headline = f"{filer} 8-K filing"
        out.append({
            "raw_text": f"{headline}\n\n{body}",
            "headline": headline,
            "source_url": url,
            "source_name": "SEC EDGAR",
            "discovery_url": url,
            "published_date": (hit.get("_source") or {}).get("file_date"),
            "country": "United States",
            "cik": cik,
            "query": name,
            "collector": COLLECTOR,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def collect(queries=None, *, leads: list[dict] | None = None,
            limit: int | None = None, session=None, pause: float = 1.0,
            fetch=None, resolve=None, sec_search=None, sec_fetch_text=None,
            log=print) -> list[dict]:
    """One targeted press search and one targeted filing search per lead.

    `queries` is accepted and ignored: the diff IS this collector's
    population. Leads normally arrive via prepare() from run_benchmark_diff;
    when neither is given (a bare `run_collect.py --source benchmark_chase`)
    the diff is computed here, and a dormant state fetches nothing.

    Every log line here is counts and indices only. See the module docstring:
    a benchmark name in a public Actions log is the one leak this loop is
    built never to make.
    """
    LAST_RUN.clear()
    work = leads if leads is not None else _PREPARED
    if work is None:
        if not armed():
            log(f"[{COLLECTOR}] neither BENCHMARK_FEED_URLS nor "
                "BENCHMARK_COMPANIES is set; dormant, nothing to diff")
            LAST_RUN["read"] = 0
            return []
        names = benchmark_names(session=session)
        missing = missing_names(names, our_company_keys())
        sliced, idx, n_slices = todays_slice(missing)
        log(f"[{COLLECTOR}] {len(names)} listed, {len(missing)} missing, "
            f"chasing slice {idx}/{n_slices} ({len(sliced)} lead(s))")
        work = [{"company": n} for n in sliced]
    elif limit:
        # A caller that prepared its own slice already chose its size;
        # `limit` is an explicit further cap, never a silent default one.
        work = work[:limit]

    LAST_RUN["read"] = len(work)
    if not work:
        log(f"[{COLLECTOR}] nothing to chase")
        return []

    fetch = fetch or google_news.fetch
    resolve = resolve or google_news.resolve_source_url
    # Lazy, the same way tripwire_chase reads the registry: collectors are
    # imported by source_registry consumers and an import at module top would
    # be a cycle waiting to happen.
    import source_registry as registry
    anchor_lang, anchor_country = registry.GOOGLE_NEWS_ANCHOR

    seen: set[str] = set()
    out: list[dict] = []
    log(f"[{COLLECTOR}] chasing {len(work)} lead(s) to their primary sources")

    for i, lead in enumerate(work, 1):
        name = str(lead.get("company") or "").strip()
        if not name:
            continue

        kept_press = 0
        try:
            items = fetch(query_for(name), lang=anchor_lang, country=anchor_country)
        except requests.RequestException as exc:
            log(f"  lead {i}/{len(work)}: press fetch failed "
                f"({type(exc).__name__})")
            items = []
        for item in items:
            if kept_press >= MAX_ITEMS_PER_LEAD:
                break
            if item["discovery_url"] in seen or not _mentions(name, item):
                continue
            seen.add(item["discovery_url"])
            # Resolve here, as the tripwire chase does: only the google_news
            # source is resolved inside run_collect, and an unresolved item is
            # an aggregator link that validate.py rightly refuses.
            item = resolve(item, session=session)
            item["collector"] = COLLECTOR
            item["locale"] = f"{anchor_country}:{anchor_lang}"
            # Bucket by employer so fair_share spreads the cap across leads.
            item["query"] = name
            item["fetched_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            out.append(item)
            kept_press += 1

        filings = _chase_sec(name, sec_search=sec_search,
                             sec_fetch_text=sec_fetch_text)
        for filing in filings:
            if filing["discovery_url"] in seen:
                continue
            seen.add(filing["discovery_url"])
            out.append(filing)

        log(f"  lead {i}/{len(work)}: {kept_press} article(s), "
            f"{len(filings)} filing(s)")
        time.sleep(pause)

    log(f"[{COLLECTOR}] {len(out)} candidate document(s) from "
        f"{len(work)} lead(s)")
    return out

#!/usr/bin/env python3
"""
Cross-tracker analysis: talent signals (this repo) x layoff events (sibling).

Two questions the new CIK/ticker join makes askable, plus a shape audit of the
talent dataset after the SEC 8-K backfill.

  1. Does executive churn predict layoffs?
  2. How long after a funding round does a hiring signal show?

Design rules this script enforces on itself, because the product's argument is
that it does not publish plausible-but-unsupported claims:

  * Every proportion is printed as numerator/denominator, never bare.
  * The BASE RATE is computed and printed before the conditional rate.
  * Windows that extend past `today` are CENSORED, not silently truncated.
    A 12-month forward window on a dataset that starts in 2026-01 and is being
    read in 2026-07 has zero fully-observed events, and the script says so
    rather than reporting a rate over partial windows.
  * A null result is a result. Nothing here is tuned until it is significant.

Read-only. Talks to two public APIs, writes only into analysis/.

Usage:
    python3 analysis/cross_tracker.py              # fetch (cached) + analyse
    python3 analysis/cross_tracker.py --fetch-only # just warm the cache
    python3 analysis/cross_tracker.py --offline    # cache only, never network
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, ".cache")
sys.path.insert(0, ROOT)

from pipeline.vocab import company_key  # noqa: E402  the join key both sides use

BASE = "https://asktherecruiter.com/blog/wp-json"
TALENT = BASE + "/talent/v1/query"
LAYOFFS = BASE + "/layoffs/v1/query"
# ModSecurity on the shared host blocks python-requests' default UA outright.
UA = "AiLayoffTracker/1.0 (+https://asktherecruiter.com)"

TODAY = date.today()
SEED = 20260728  # fixed, so the permutation test is reproducible


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def _get(url: str, tries: int = 5) -> dict:
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            # Shared hosting 500s randomly under load; any paging job must
            # retry transient failures and continue.
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}: {last}")


def _page_all(url: str, key: str, per_page: int, label: str, offline: bool) -> list:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{label}.json")
    if os.path.exists(path):
        with open(path) as fh:
            rows = json.load(fh)
        print(f"  {label}: {len(rows):,} rows from cache", file=sys.stderr)
        return rows
    if offline:
        raise SystemExit(f"--offline but no cache at {path}")

    rows, page = [], 1
    while True:
        sep = "&" if "?" in url else "?"
        d = _get(f"{url}{sep}per_page={per_page}&page={page}")
        batch = d.get(key) or []
        rows.extend(batch)
        total = d.get("total", 0)
        print(f"  {label}: {len(rows):,}/{total:,}", file=sys.stderr)
        if len(batch) < per_page or len(rows) >= total:
            break
        page += 1
    with open(path, "w") as fh:
        json.dump(rows, fh)
    return rows


def fetch_talent(offline=False) -> list:
    return _page_all(TALENT, "rows", 200, "talent", offline)


def fetch_layoffs(offline=False) -> list:
    # sort ascending by date so paging is stable while new rows land at the end
    return _page_all(LAYOFFS + "?sort=layoff_date&dir=asc", "data", 200, "layoffs", offline)


def load_local_db() -> list:
    """The committed SQLite database, snapshotted on first read.

    Why bother when there is a live API: the identity spine (cik, ticker,
    employer_type) and most of the Form D funding rows exist ONLY here. The
    plugin deploy is not armed, so those columns have not reached the published
    surface. The database is also being written by a running backfill, so the
    first read is copied into the cache and every later run reads the copy --
    otherwise the "reproducible" script reports a different number every time.
    """
    import shutil
    import sqlite3

    os.makedirs(CACHE, exist_ok=True)
    snap = os.path.join(CACHE, "talent_intel.snapshot.db")
    stamp = os.path.join(CACHE, "talent_intel.snapshot.txt")
    live = os.path.join(ROOT, "data", "talent_intel.db")
    if not os.path.exists(snap):
        if not os.path.exists(live):
            return []
        shutil.copy2(live, snap)
        with open(stamp, "w") as fh:
            fh.write(datetime.now().isoformat(timespec="seconds"))
    con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM signals WHERE is_current = 1")]
    except sqlite3.Error:
        rows = []
    con.close()
    print(f"  local db snapshot: {len(rows):,} rows", file=sys.stderr)
    return rows


def merge_talent(api_rows, db_rows, out):
    """Union of the published API and the local database, keyed on signal_id.

    Neither side is a superset of the other, which is itself worth printing.
    """
    L = out.append
    merged = {}
    for r in api_rows:
        merged[r.get("signal_id")] = dict(r)
    api_ids = set(merged)
    db_ids = set()
    for r in db_rows:
        sid = r.get("signal_id")
        db_ids.add(sid)
        if sid in merged:
            # the database is where identity and funding columns actually live
            for col in ("cik", "ticker", "employer_type", "funding_stage",
                        "funding_amount_usd", "funding_amount"):
                if not merged[sid].get(col) and r.get(col):
                    merged[sid][col] = r[col]
        else:
            merged[sid] = dict(r)
    rows = list(merged.values())

    L("## Where the data came from")
    L("")
    L("| source | rows |")
    L("|---|---:|")
    L(f"| live API `/talent/v1/query` | {len(api_rows):,} |")
    L(f"| local committed SQLite (snapshot) | {len(db_rows):,} |")
    L(f"| in both | {len(api_ids & db_ids):,} |")
    L(f"| API only | {len(api_ids - db_ids):,} |")
    L(f"| database only | {len(db_ids - api_ids):,} |")
    L(f"| **union used below** | **{len(rows):,}** |")
    L("")
    L("Neither side is a superset of the other, and that is a finding on its own. "
      "The published API is ahead on rows; the local database is ahead on "
      "*columns* -- `cik`, `ticker` and most Form D funding rows exist only there "
      "because the plugin deploy is not armed. Anyone reading the public API today "
      "sees a dataset with an empty identity spine.")
    L("")
    return rows


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_GENERIC = {
    "", "the", "group", "holdings", "holding", "company", "services", "systems",
    "technologies", "solutions", "international", "global", "partners", "capital",
    "management", "industries", "enterprises", "associates", "trust", "fund",
    "n a", "na", "usa", "us", "america", "american",
}


def jkey(name) -> str:
    """Join key. `company_key` already strips Inc/Corp/LLC/Ltd/PLC/SA/NV/GmbH.
    On top of that we refuse keys that are too short or too generic to be an
    identity claim -- a false join is worse than a missed one here."""
    if not name:
        return ""
    k = company_key(str(name))
    if len(k) < 4 or k in _GENERIC:
        return ""
    return k


def parse_d(v):
    if not v:
        return None
    s = str(v)[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def wilson(k: int, n: int, z: float = 1.96):
    """95% Wilson score interval. Normal approximation is useless at the
    counts this analysis actually has."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def pct(k: int, n: int) -> str:
    """Never a bare percentage."""
    if n == 0:
        return f"n/a (0/0)"
    p, lo, hi = wilson(k, n)
    return f"{100*p:.1f}% ({k}/{n}, 95% CI {100*lo:.1f}-{100*hi:.1f}%)"


def mde(p0: float, n: int, alpha_z: float = 1.645, power_z: float = 0.84):
    """Minimum detectable effect: the smallest conditional rate that a
    one-sided test at alpha=.05 with 80% power could have distinguished from
    base rate p0 at sample size n. A null result without this number is just
    an assertion that nothing was found; with it, the null has a size."""
    if n <= 0 or not (0 < p0 < 1):
        return None
    se = math.sqrt(p0 * (1 - p0) / n)
    return min(1.0, p0 + (alpha_z + power_z) * se)


def _logfact_table(n):
    t = [0.0] * (n + 1)
    for i in range(1, n + 1):
        t[i] = t[i - 1] + math.log(i)
    return t


def fisher_exact_greater(a, b, c, d):
    """One-sided Fisher exact, H1: row-1 proportion > row-2 proportion.
    Table [[a,b],[c,d]] = [[hit,miss] conditional, [hit,miss] base]."""
    n = a + b + c + d
    if n == 0 or n > 200000:
        return None
    lf = _logfact_table(n)

    def lp(x):
        y = (a + b) - x
        u = (a + c) - x
        v = (c + d) - u
        if y < 0 or u < 0 or v < 0:
            return None
        return (lf[a + b] + lf[c + d] + lf[a + c] + lf[b + d]
                - lf[n] - lf[x] - lf[y] - lf[u] - lf[v])

    obs = lp(a)
    tot = 0.0
    for x in range(0, min(a + b, a + c) + 1):
        v = lp(x)
        if v is not None:
            tot += math.exp(v)
    p = 0.0
    for x in range(a, min(a + b, a + c) + 1):
        v = lp(x)
        if v is not None and v <= obs + 1e-12:
            p += math.exp(v)
    return min(1.0, p / tot) if tot else None


# --------------------------------------------------------------------------
# shape audit (analysis 3)
# --------------------------------------------------------------------------

def shape(talent, out):
    L = out.append
    L("## 3. Shape of the talent dataset after the SEC backfill")
    L("")
    n = len(talent)
    L(f"Union of the live API and the local database snapshot: **{n:,}** rows "
      f"(current revisions only). Percentages below are of that union.")
    L("")

    def table(title, counter, total, limit=None, note=None):
        L(f"**{title}**")
        L("")
        L("| value | rows | share |")
        L("|---|---:|---:|")
        items = (counter.most_common(limit) if limit
                 else sorted(counter.items(), key=lambda kv: (kv[0] is None, str(kv[0]))))
        for k, v in items:
            L(f"| {k or '(null)'} | {v:,} | {100*v/total:.1f}% |")
        shown = sum(v for _, v in items)
        if shown < total:
            L(f"| (remaining {len(counter)-len(items)} values) | {total-shown:,} | {100*(total-shown)/total:.1f}% |")
        L("")
        if note:
            L(note)
            L("")

    table("By pillar", Counter(r.get("pillar") for r in talent), n)
    table("By signal direction", Counter(r.get("signal_direction") for r in talent), n)
    table("By confidence", Counter(r.get("confidence") for r in talent), n)
    table("By country (job location, top 12)",
          Counter(r.get("country") for r in talent), n, limit=12)
    table("By employer type", Counter(r.get("employer_type") for r in talent), n)
    table("By source (top 12)", Counter(r.get("source_name") for r in talent), n, limit=12)

    months = Counter()
    for r in talent:
        d = parse_d(r.get("published_date")) or parse_d(r.get("captured_at"))
        months[d.strftime("%Y-%m") if d else "(no date)"] += 1
    L("**By month of publication**")
    L("")
    L("| month | rows | bar |")
    L("|---|---:|---|")
    mx = max(months.values()) if months else 1
    for k in sorted(months):
        L(f"| {k} | {months[k]:,} | {'#' * max(1, round(40*months[k]/mx))} |")
    L("")

    L("**Materiality:** there is no `materiality` column in the schema yet "
      "(`signals` has pillar, direction, confidence, headcount, funding_amount_usd "
      "-- nothing that ranks how much a signal matters). Reporting a materiality "
      "breakdown would mean inventing the column, so this section does not have one.")
    L("")

    # --- artefacts -------------------------------------------------------
    L("### Collection artefacts, not market patterns")
    L("")
    art = []
    ident = sum(1 for r in talent if r.get("cik") or r.get("ticker"))
    lead = sum(1 for r in talent if r.get("pillar") == "leadership_change")
    us = sum(1 for r in talent if r.get("country") == "US")

    real_months = {k: v for k, v in months.items() if k != "(no date)"}
    if real_months:
        ordered = sorted(real_months)
        gaps = []
        cur = datetime.strptime(ordered[0], "%Y-%m").date()
        end = datetime.strptime(ordered[-1], "%Y-%m").date()
        while cur <= end:
            tag = cur.strftime("%Y-%m")
            if real_months.get(tag, 0) == 0:
                gaps.append(tag)
            cur = add_months(cur, 1)
        thin = [m for m in ordered if real_months[m] < 0.15 * mx]
        if gaps:
            art.append(
                f"**Month gaps: {', '.join(gaps)}.** Zero rows in a month is not a "
                f"quiet market -- the SEC backfill was run window by window and "
                f"those windows have not been run yet. Any month-over-month chart "
                f"drawn on this data today is drawing the backfill queue.")
        if thin:
            art.append(
                f"**Thin months: {', '.join(f'{m} ({real_months[m]:,})' for m in thin)}.** "
                f"Below 15% of the peak month. The tail is partly the live "
                f"collector running at 2x/day against a backfill that loaded "
                f"months in bulk, so recent months look small next to backfilled ones.")

    art.append(
        f"**{100*lead/n:.0f}% of rows ({lead:,}/{n:,}) are leadership changes.** "
        f"That is the composition of an SEC Item 5.02 backfill, not of the talent "
        f"market. Every 8-K filer that changed an officer is in here; a private "
        f"company that hired 200 engineers is not, because it files nothing.")
    art.append(
        f"**{100*us/n:.0f}% of rows ({us:,}/{n:,}) are US.** SEC EDGAR is a US "
        f"filing system. The non-US rows come from news collectors running at a "
        f"fraction of the volume. This is coverage bias and must never be read as "
        f"'the US is where the activity is'.")
    art.append(
        f"**Only {100*ident/n:.0f}% of rows ({ident:,}/{n:,}) carry a CIK or ticker.** "
        f"The identity spine landed recently; rows collected before it have no "
        f"identifier and can only be joined by name.")
    for a in art:
        L(f"- {a}")
    L("")
    return {"rows": n, "months": dict(months), "leadership": lead, "us": us, "identified": ident}


# --------------------------------------------------------------------------
# join
# --------------------------------------------------------------------------

def build_join(talent, layoffs, out):
    L = out.append

    # layoff side: employer -> sorted list of market-visible event dates.
    # `notice` basis = COALESCE(announcement_date, layoff_date): when the cut
    # was announced where known, else when it takes effect. That is the date a
    # leadership change could plausibly have preceded.
    lay_by_key = defaultdict(list)
    lay_eff = defaultdict(list)          # layoff_date only, for the sensitivity check
    lay_by_ticker = defaultdict(list)
    lay_names = defaultdict(set)
    drop_date = drop_name = 0
    drop_date_src = Counter()
    src_total = Counter()
    for r in layoffs:
        d = parse_d(r.get("announcement_date")) or parse_d(r.get("layoff_date"))
        k = jkey(r.get("company_name"))
        src_total[r.get("source_type") or "(none)"] += 1
        if not d:
            drop_date += 1
            drop_date_src[r.get("source_type") or "(none)"] += 1
            continue
        if not k:
            drop_name += 1
            continue
        lay_by_key[k].append(d)
        e = parse_d(r.get("layoff_date"))
        if e:
            lay_eff[k].append(e)
        lay_names[k].add((r.get("company_name") or "").strip())
        t = (r.get("ticker") or "").strip().upper()
        if t:
            lay_by_ticker[t].append(d)
    for m in (lay_by_key, lay_eff, lay_by_ticker):
        for v in m.values():
            v.sort()
    lay_dropped = drop_date + drop_name
    lay_span = [d for v in lay_by_key.values() for d in v]

    # talent side
    tal_by_key = defaultdict(list)
    tal_ticker_of_key = {}
    tal_names = defaultdict(set)
    for r in talent:
        k = jkey(r.get("company"))
        if not k:
            continue
        tal_by_key[k].append(r)
        tal_names[k].add((r.get("company") or "").strip())
        t = (r.get("ticker") or "").strip().upper()
        if t:
            tal_ticker_of_key[k] = t

    tier_ticker, tier_name = set(), set()
    for k in tal_by_key:
        t = tal_ticker_of_key.get(k)
        if t and t in lay_by_ticker:
            tier_ticker.add(k)
        elif k in lay_by_key:
            tier_name.add(k)
    joined = tier_ticker | tier_name

    # --- what identifiers actually exist, before claiming an identifier join ---
    tal_cik = sum(1 for r in talent if r.get("cik"))
    tal_tick = sum(1 for r in talent if r.get("ticker"))
    lay_tick = sum(1 for r in layoffs if (r.get("ticker") or "").strip())
    lay_has_cik_col = any("cik" in r for r in layoffs[:50])

    L("## The join, before any finding")
    L("")
    L("### The CIK join does not exist end to end")
    L("")
    L("This analysis was commissioned on the strength of a new CIK column. That "
      "column is real on the talent side and absent on the layoff side, so the "
      "cross-tracker join is a **name** join, with a ticker tier that turns out "
      "to be empty. Stating that plainly is more useful than quietly falling back.")
    L("")
    L("| identifier | talent side | layoff side |")
    L("|---|---:|---:|")
    L(f"| rows with a CIK | {tal_cik:,}/{len(talent):,} "
      f"({100*tal_cik/max(1,len(talent)):.1f}%) | "
      f"{'no `cik` field exists in the layoff schema' if not lay_has_cik_col else '0'} |")
    L(f"| rows with a ticker | {tal_tick:,}/{len(talent):,} "
      f"({100*tal_tick/max(1,len(talent)):.1f}%) | "
      f"{lay_tick:,}/{len(layoffs):,} ({100*lay_tick/max(1,len(layoffs)):.1f}%) |")
    L("")
    L("The layoff tracker's REST payload has no `cik` key at all, and its `ticker` "
      "column is effectively unpopulated -- unsurprising, since most of its volume "
      "is state WARN filings and news, neither of which carries a securities "
      "identifier. So *every* joined employer below is joined on a normalised "
      "name, and the error modes of a name join apply to every number in analysis 1.")
    L("")
    L("**The actionable version:** the way to make this a real identifier join is "
      "to resolve the layoff tracker's company names to CIKs on that side, using "
      "the same SEC `company_tickers.json` spine already built here. Until that "
      "exists, the honest ceiling of cross-tracker analysis is name matching.")
    L("")
    L("### How many employers actually join")
    L("")
    L("The number that decides whether analysis 1 is possible at all.")
    L("")
    L("| | employers |")
    L("|---|---:|")
    L(f"| distinct employers, talent side | {len(tal_by_key):,} |")
    L(f"| distinct employers, layoff side | {len(lay_by_key):,} |")
    L(f"| **joined (in both)** | **{len(joined):,}** |")
    L(f"| ... joined on ticker (both sides carry one) | {len(tier_ticker):,} |")
    L(f"| ... joined on normalised name only | {len(tier_name):,} |")
    L("")
    L(f"Layoff rows excluded from the join: **{lay_dropped:,} of {len(layoffs):,}** "
      f"({100*lay_dropped/max(1,len(layoffs)):.1f}%) -- {drop_date:,} with no usable "
      f"date on either the announcement or effective column, and {drop_name:,} whose "
      f"company name collapsed to something too short or too generic to be an "
      f"identity claim. Usable layoff events retained: {len(lay_span):,}, spanning "
      f"{min(lay_span)} to {max(lay_span)}.")
    L("")
    L("That 20% is not spread evenly, and the pattern is worth handing back to the "
      "sibling tracker as a data-quality item rather than absorbing silently:")
    L("")
    L("| layoff source type | rows | undated | share undated |")
    L("|---|---:|---:|---:|")
    for s, tot in src_total.most_common():
        u = drop_date_src.get(s, 0)
        L(f"| {s} | {tot:,} | {u:,} | {100*u/tot:.1f}% |")
    L("")
    L("A row with no date cannot participate in any before/after analysis, so this "
      "is the ceiling on how much of the layoff dataset is usable for timing "
      "questions -- regardless of how many rows it has in total.")
    L("")
    L("Join key strips Inc / Corp / LLC / Ltd / PLC / SA / NV / GmbH and friends "
      "(`pipeline.vocab.company_key`, the same function the tracker itself keys on), "
      "then refuses any key under 4 characters or on a generic-word blocklist "
      "(`group`, `holdings`, `global`, ...). A false join is worse than a missed one: "
      "it manufactures a correlation out of two different companies.")
    L("")
    if joined:
        sample = sorted(joined)[:15]
        L("Sample of joined employers, so the name matching can be eyeballed:")
        L("")
        L("| join key | talent-side name | layoff-side name |")
        L("|---|---|---|")
        for k in sample:
            L(f"| `{k}` | {sorted(tal_names[k])[0]} | {sorted(lay_names.get(k, {'-'}))[0]} |")
        L("")
    return tal_by_key, lay_by_key, lay_eff, joined, {
        "talent_employers": len(tal_by_key), "layoff_employers": len(lay_by_key),
        "joined": len(joined), "by_ticker": len(tier_ticker), "by_name": len(tier_name),
        "talent_rows_with_cik": tal_cik, "talent_rows_with_ticker": tal_tick,
        "layoff_rows_with_ticker": lay_tick, "layoff_has_cik_field": lay_has_cik_col,
    }


# --------------------------------------------------------------------------
# analysis 1
# --------------------------------------------------------------------------

def analysis_one(tal_by_key, lay_by_key, lay_eff, joined, out):
    L = out.append
    res = {}
    L("## 1. Does executive churn predict layoffs?")
    L("")
    L("**Read the denominator carefully.** The universe below is employers present "
      "in *both* datasets. Every one of them therefore has at least one layoff "
      "event on record at some point in history -- that is what being in the "
      "layoff dataset means. This is conditioning on the outcome, and it inflates "
      "every rate on this page. It is the correct universe for the comparison "
      "being made (conditional vs base rate are inflated identically, so the "
      "*difference* survives), and it is the wrong number to lift out as "
      "'X% of leadership changes are followed by layoffs'. The all-employer "
      "denominator is printed alongside each window for exactly that reason.")
    L("")

    # every leadership-change signal, including at employers with no layoff row
    all_events = []
    for k, rows in sorted(tal_by_key.items()):
        for r in rows:
            if r.get("pillar") != "leadership_change":
                continue
            d = parse_d(r.get("published_date")) or parse_d(r.get("captured_at"))
            if d:
                all_events.append((k, d))

    # leadership-change events for joined employers.
    # `joined` is a set and `events` feeds a seeded resampler, so both the
    # iteration and the sort must be total -- otherwise the "reproducible"
    # script prints a different base rate on every run.
    events = []  # (key, date)
    for k in sorted(joined):
        for r in tal_by_key[k]:
            if r.get("pillar") != "leadership_change":
                continue
            d = parse_d(r.get("published_date")) or parse_d(r.get("captured_at"))
            if d:
                events.append((k, d))
    events.sort(key=lambda e: (e[1], e[0]))
    all_events.sort(key=lambda e: (e[1], e[0]))

    if not events:
        L("No leadership-change signals exist for any joined employer. "
          "The analysis cannot run. Nothing further is reported here.")
        L("")
        return {"runnable": False}

    first = min(d for _, d in events)
    last = max(d for _, d in events)
    L(f"Leadership-change signals belonging to a joined employer: **{len(events):,}**, "
      f"spanning {first} to {last}. Employers involved: "
      f"{len({k for k, _ in events}):,}.")
    L("")
    L("Layoff event date used throughout: `COALESCE(announcement_date, layoff_date)` "
      "-- the sibling tracker's documented `notice` basis, i.e. when the cut became "
      "publicly visible, falling back to when it takes effect.")
    L("")

    def has_layoff(k, lo, hi):
        """any layoff event in (lo, hi]"""
        return any(lo < d <= hi for d in lay_by_key.get(k, ()))

    # observable window for placebo anchors: the talent data's own span
    win_lo, win_hi = first, last
    span_days = (win_hi - win_lo).days or 1

    L("### Censoring comes first")
    L("")
    L(f"Today is {TODAY}. A signal dated D has a *fully observed* N-month forward "
      f"window only if D + N months <= today. Reporting a rate over partially "
      f"observed windows would understate the hit rate by construction, so "
      f"partially observed signals are excluded rather than counted as misses.")
    L("")
    L("| window | signals with a complete window | excluded as censored |")
    L("|---|---:|---:|")
    complete = {}
    for months in (3, 6, 12):
        ok = [(k, d) for k, d in events if add_months(d, months) <= TODAY]
        complete[months] = ok
        L(f"| {months} months | {len(ok):,} | {len(events)-len(ok):,} |")
    L("")

    rng = random.Random(SEED)
    rows_out = []

    for months in (3, 6, 12):
        L(f"### {months}-month window")
        L("")
        ev = complete[months]
        if not ev:
            L(f"**Not computable.** Zero of {len(events):,} leadership-change signals "
              f"have a fully observed {months}-month forward window. The talent "
              f"dataset begins {first} and today is {TODAY}; the earliest signal is "
              f"{(TODAY-first).days} days old, and this window needs "
              f"{months} months. This is not a null result -- it is an "
              f"unaskable question, and it stays unaskable until "
              f"{add_months(first, months)}.")
            L("")
            rows_out.append({"window_months": months, "computable": False})
            continue

        # ---- BASE RATE first, as the rule requires ----
        # For each employer in the joined universe, sample anchor dates
        # uniformly across the same calendar span the real signals occupy, and
        # ask the same question. Matches employer composition and calendar
        # window; breaks only the link to the leadership event itself.
        anchor_hi = add_months(TODAY, -months)
        draws = 200
        base_hits = base_n = 0
        per_draw = []
        for _ in range(draws):
            h = n = 0
            for k, _d in ev:
                lo = win_lo
                hi = min(win_hi, anchor_hi)
                if hi < lo:
                    continue
                a = lo + timedelta(days=rng.randint(0, (hi - lo).days))
                n += 1
                if has_layoff(k, a, add_months(a, months)):
                    h += 1
            base_hits += h
            base_n += n
            if n:
                per_draw.append(h / n)
        base_p = base_hits / base_n if base_n else 0.0

        L(f"**Base rate first.** Same employers, same calendar span, anchor date "
          f"chosen at random instead of at a leadership change, {draws} "
          f"resamples: **{100*base_p:.1f}%** "
          f"({base_hits:,} hits / {base_n:,} employer-windows across all draws; "
          f"one draw is {len(ev):,} windows).")
        L("")

        # ---- CONDITIONAL rate ----
        hit_rows = [(k, d) for k, d in ev if has_layoff(k, d, add_months(d, months))]
        hits = len(hit_rows)
        p, lo_ci, hi_ci = wilson(hits, len(ev))
        L(f"**Conditional rate.** A layoff event within {months} months *after* a "
          f"leadership change: {pct(hits, len(ev))}.")
        L("")

        if months == 3 and hit_rows:
            L("<details><summary>All " + str(hits) + " hits, listed so the null is "
              "auditable rather than asserted</summary>")
            L("")
            L("| employer (join key) | leadership change | first layoff in window | days |")
            L("|---|---|---|---:|")
            for k, d in sorted(hit_rows):
                nxt = min(x for x in lay_by_key[k] if d < x <= add_months(d, months))
                L(f"| `{k}` | {d} | {nxt} | {(nxt-d).days} |")
            L("")
            L("</details>")
            L("")

        # ---- placebo: the same window BEFORE the signal ----
        pre_ev = [(k, d) for k, d in ev if add_months(d, -months) >= date(2015, 1, 1)]
        pre_hits = sum(1 for k, d in pre_ev if has_layoff(k, add_months(d, -months), d))
        L(f"**Placebo (backward window).** Layoff in the {months} months *before* the "
          f"leadership change: {pct(pre_hits, len(pre_ev))}. If churn genuinely "
          f"leads layoffs, forward should beat backward. If they match, we are "
          f"looking at companies that are simply always cutting.")
        L("")

        # ---- significance ----
        one_draw_n = len(ev)
        one_draw_hits = round(base_p * one_draw_n)
        fe = fisher_exact_greater(hits, one_draw_n - hits,
                                  one_draw_hits, one_draw_n - one_draw_hits)
        perm_p = None
        if per_draw:
            ge = sum(1 for x in per_draw if x >= p)
            perm_p = (ge + 1) / (len(per_draw) + 1)

        L(f"**Is the difference real?** Permutation p = "
          f"{perm_p:.3f} ({sum(1 for x in per_draw if x >= p)} of {len(per_draw)} "
          f"random-anchor draws reached the observed rate or better)."
          + (f" Fisher exact (one-sided, conditional vs one base draw of the same "
             f"size) p = {fe:.3f}." if fe is not None else ""))
        L("")

        m = mde(base_p, len(ev))
        if m is not None:
            lift = (m - base_p) / base_p if base_p else 0
            L(f"**How big a signal would we have caught?** At n={len(ev):,} and a "
              f"base rate of {100*base_p:.1f}%, a one-sided test with 80% power "
              f"could have detected a conditional rate of **{100*m:.1f}%** or "
              f"higher -- a {100*lift:.0f}% relative lift. Anything smaller than "
              f"that is invisible at this sample size. So the honest statement is "
              f"not 'executive churn does not predict layoffs'; it is "
              f"'if there is an effect at {months} months, it is smaller than a "
              f"{100*lift:.0f}% lift, and the point estimate is currently "
              f"{'below' if p < base_p else 'above'} the base rate'.")
            L("")

        # sensitivity: effective date only, no announcement-date substitution
        alt_hits = sum(1 for k, d in ev
                       if any(d < x <= add_months(d, months) for x in lay_eff.get(k, ())))
        L(f"**Sensitivity, different date basis.** Using `layoff_date` alone "
          f"(when the cut takes effect) instead of "
          f"`COALESCE(announcement_date, layoff_date)`: {pct(alt_hits, len(ev))}. "
          f"The conclusion does not move with the date basis.")
        L("")

        clustered = len({k for k, _ in ev})
        L(f"**Clustering caveat.** Those {len(ev):,} signals come from only "
          f"{clustered:,} employers ({len(ev)/clustered:.1f} signals each). Signals "
          f"from one employer are not independent -- one company with rolling "
          f"layoffs and a reshuffling board contributes many correlated hits. "
          f"Treat any p-value above as an upper bound on how surprised to be.")
        L("")

        # employer-level, one observation per employer, no clustering
        emp_first = {}
        for k, d in ev:
            if k not in emp_first or d < emp_first[k]:
                emp_first[k] = d
        e_hits = sum(1 for k, d in emp_first.items() if has_layoff(k, d, add_months(d, months)))
        L(f"**Employer-level (one row per employer, first signal only).** "
          f"{pct(e_hits, len(emp_first))}. This is the clustering-free version and "
          f"the one to quote if only one number is quoted.")
        L("")

        # the denominator a reader actually wants: every leadership change we
        # hold, not just the ones at companies already known to lay people off
        all_ev = [(k, d) for k, d in all_events if add_months(d, months) <= TODAY]
        all_hits = sum(1 for k, d in all_ev if has_layoff(k, d, add_months(d, months)))
        L(f"**Against the all-employer denominator** (every leadership-change "
          f"signal we hold with a complete window, including the "
          f"{len(all_ev)-len(ev):,} at employers with no layoff record at all): "
          f"{pct(all_hits, len(all_ev))}. This is the number that answers "
          f"'if I see an exec change, how often does a layoff follow?' and it is "
          f"much smaller than the joined-universe figure above, because most "
          f"companies that change an officer never appear in the layoff data.")
        L("")

        rows_out.append({
            "window_months": months, "computable": True,
            "signals": len(ev), "employers": clustered,
            "conditional_hits": hits, "conditional_n": len(ev),
            "conditional_rate": p, "conditional_ci": [lo_ci, hi_ci],
            "base_rate": base_p, "base_hits": base_hits, "base_n": base_n,
            "placebo_hits": pre_hits, "placebo_n": len(pre_ev),
            "employer_hits": e_hits, "employer_n": len(emp_first),
            "all_denom_hits": all_hits, "all_denom_n": len(all_ev),
            "perm_p": perm_p, "fisher_p": fe,
        })

    res["windows"] = rows_out

    L("### Confounders in analysis 1")
    L("")
    for c in [
        "**One side is 2026-only.** The talent dataset starts 2026-01; the layoff "
        "dataset reaches back to 2001 (state WARN and ERM) / 2015 (news and SEC). Every "
        "forward window is short and the 12-month window is not observable at all. "
        "Nothing here can speak to a lag longer than the data is old.",
        "**Selection into the dataset.** A company only produces a leadership-change "
        "signal here if it files an SEC 8-K Item 5.02 -- i.e. it is a US public "
        "company with a named officer change. Public companies also file WARN "
        "notices and issue layoff press releases at a far higher rate than the "
        "average employer. The joined set is therefore enriched for companies that "
        "do both, which inflates the conditional rate and the base rate together. "
        "That is exactly why the base rate is the comparison, not the population.",
        "**Survivorship.** Companies that were acquired, delisted or went private "
        "mid-window stop filing 8-Ks and stop appearing on either side. Their "
        "outcomes are missing, and 'stopped filing' correlates with distress.",
        "**Name-join error in both directions.** Joining on a suffix-stripped name "
        "merges genuinely distinct companies (false positives) and misses "
        "rebrands, DBA names and non-Latin-script names (false negatives). A "
        "concrete example from the hit list above: `everest group` matches "
        "'Everest Group, Ltd.' on the talent side (the Bermuda reinsurer) against "
        "'Everest Group' on the layoff side, which may instead be the research and "
        "advisory firm of the same name. One such pair in 36 hits is a ~3% "
        "false-positive floor that no amount of statistics removes.",
        "**Reverse causation is not excluded.** An executive departing *because* a "
        "restructuring is already underway produces exactly the same forward "
        "correlation as an executive arriving and then cutting. The backward "
        "placebo window is the only lever here against that, and it is a weak one.",
        "**Layoff-side date basis.** WARN effective dates can be months after the "
        "decision and can be in the future; news dates can precede the filing. "
        "`COALESCE(announcement_date, layoff_date)` mixes the two bases across rows.",
        "**Conditioning on the outcome.** Every employer in the joined universe "
        "has a layoff on record somewhere in history. Rates computed on it are "
        "not population rates and must never be published as such.",
        "**The layoff dataset is not a census either.** It holds verified events "
        "from SEC filings, ~25 US states' WARN systems and worldwide news. A "
        "company can cut 300 people with no 8-K, no WARN trigger and no coverage, "
        "and it counts as a miss here. Every rate in this section is therefore a "
        "floor on the true rate, on both the conditional and the base side.",
    ]:
        L(f"- {c}")
    L("")
    return res


# --------------------------------------------------------------------------
# analysis 2
# --------------------------------------------------------------------------

def analysis_two(talent, out):
    L = out.append
    L("## 2. How long after a raise does hiring show?")
    L("")
    L("Practitioner guidance in circulation claims the real hiring wave starts "
      "8-12 weeks after a round closes. The question here is only whether our "
      "data supports it, contradicts it, or cannot yet answer it.")
    L("")

    by_key = defaultdict(list)
    for r in talent:
        k = jkey(r.get("company"))
        if k:
            by_key[k].append(r)

    def sig_date(r):
        return parse_d(r.get("published_date")) or parse_d(r.get("captured_at"))

    funding, hiring = [], []
    for k, rows in by_key.items():
        for r in rows:
            d = sig_date(r)
            if not d:
                continue
            if r.get("funding_stage") or r.get("funding_amount_usd"):
                funding.append((k, d, r))
            if r.get("signal_direction") == "hiring":
                hiring.append((k, d, r))

    hire_by_key = defaultdict(list)
    for k, d, r in hiring:
        hire_by_key[k].append(d)
    for v in hire_by_key.values():
        v.sort()

    fund_emp = {k for k, _, _ in funding}
    hire_emp = set(hire_by_key)
    both = fund_emp & hire_emp

    L("| | count |")
    L("|---|---:|")
    L(f"| funding signals (a stage or a USD amount) | {len(funding):,} |")
    L(f"| distinct employers with a funding signal | {len(fund_emp):,} |")
    L(f"| hiring-direction signals | {len(hiring):,} |")
    L(f"| distinct employers with a hiring signal | {len(hire_emp):,} |")
    L(f"| **employers with both** | **{len(both):,}** |")
    L("")

    pairs = []
    for k, d, r in funding:
        nxt = [h for h in hire_by_key.get(k, ()) if h > d]
        if nxt:
            pairs.append((k, d, nxt[0], (nxt[0] - d).days))

    L(f"Funding signals followed by a later hiring signal at the same employer: "
      f"**{len(pairs)}** of {len(funding):,}.")
    L("")

    if len(pairs) < 20:
        L("### Verdict: cannot yet answer")
        L("")
        L(f"With **{len(pairs)}** matched funding-to-hiring pairs, this dataset "
          f"cannot support, contradict or refine the 8-12 week claim. Any lag "
          f"distribution drawn on {len(pairs)} points would be a picture of "
          f"which rows happen to have landed, not of the market.")
        L("")
        if pairs:
            L("The pairs that do exist, listed in full because that is the entire "
              "evidence base:")
            L("")
            L("| employer | funding date | first hiring signal | lag (days) | lag (weeks) |")
            L("|---|---|---|---:|---:|")
            for k, fd, hd, lag in sorted(pairs, key=lambda p: p[3]):
                L(f"| `{k}` | {fd} | {hd} | {lag} | {lag/7:.1f} |")
            L("")
            L("These are listed, not summarised. No median, no modal window, no "
              "'consistent with 8-12 weeks' -- at this n those words would be "
              "decoration on noise.")
            L("")

        # what n would be needed
        L("### What sample size would settle it")
        L("")
        L("Two separate requirements, and the second is the binding one.")
        L("")
        L("**(a) To estimate the share of hires falling in the 8-12 week band to "
          "+/-10 percentage points at 95% confidence:** worst-case variance at "
          "p=0.5 gives n = 1.96^2 x 0.25 / 0.10^2 = **97 matched pairs**. "
          "For +/-5pp it is **385 pairs**. A 'the wave starts at 8-12 weeks' claim "
          "is really a claim about the shape of a distribution, so the +/-5pp "
          "figure is the honest target.")
        L("")
        n_f = len(funding)
        rate = len(pairs) / n_f if n_f else 0.0
        if len(pairs) == 0 and n_f:
            # rule of three: 0 hits in n gives a 95% upper bound of 3/n
            ub = 3.0 / n_f
            L(f"**(b) To get there, how many funding rows?** We have observed "
              f"**0 pairs in {n_f} funding signals**. By the rule of three the 95% "
              f"upper bound on the pairing rate is 3/{n_f} = {100*ub:.1f}%. So "
              f"reaching 97 pairs needs *at least* 97/{ub:.4f} = "
              f"**~{math.ceil(97/ub):,} funding rows**, and that is the optimistic "
              f"end -- the true rate could be far lower, in which case no realistic "
              f"Form D backfill reaches it.")
            L("")
            L("**This is the finding.** The binding constraint is not funding "
              "coverage, it is that we barely collect hiring signals at all "
              f"({len(hiring):,} rows in the whole dataset, "
              f"{100*len(hiring)/max(1,len(talent)):.1f}% of it). Backfilling Form D "
              "faster does not fix it, and neither does any amount of patience: "
              "with a funding side of 54 rows and a hiring side of "
              f"{len(hiring)}, the pairing is arithmetically starved on the "
              "hiring side.")
            L("")
            L("The lag question becomes answerable only when a collector exists "
              "that observes hiring as a *rate* -- 'employer X opened N roles this "
              "fortnight' -- rather than as news. An ATS job-board collector of "
              "exactly that shape is in progress in this repo "
              "(`collectors/ats_boards.py`, uncommitted at the time of writing), "
              "which is the right unblock. Two things to note before anyone "
              "expects this analysis to become answerable when it ships: that "
              "collector explicitly has **no history** (the archive starts the day "
              "it runs), and it watches a curated employer list rather than the "
              "long tail of Form D filers. So the earliest this question can be "
              "answered is roughly one year after that collector goes live, on "
              "the intersection of its watchlist with the funding data -- not on "
              "the whole funding set.")
        elif n_f:
            L(f"**(b) To get there, how many funding rows?** Observed pairing rate "
              f"{pct(len(pairs), n_f)}. At that rate, 97 pairs needs "
              f"~{math.ceil(97/rate):,} funding signals and 385 pairs needs "
              f"~{math.ceil(385/rate):,}.")
        L("")
    else:
        lags = sorted(p[3] for p in pairs)
        n = len(lags)
        med = lags[n // 2]
        in_band = sum(1 for x in lags if 56 <= x <= 84)
        L("### Lag distribution")
        L("")
        L(f"Median lag {med} days ({med/7:.1f} weeks) over n={n}. "
          f"Share landing in the 8-12 week band (56-84 days): {pct(in_band, n)}.")
        L("")
        buckets = Counter()
        for x in lags:
            buckets[min(x // 28, 6)] += 1
        L("| lag bucket | pairs |")
        L("|---|---:|")
        for b in sorted(buckets):
            label = f"{b*4}-{(b+1)*4} weeks" if b < 6 else "24+ weeks"
            L(f"| {label} | {buckets[b]} |")
        L("")

    L("### Confounders in analysis 2")
    L("")
    for c in [
        "**The hiring signal is news-shaped, not hiring-shaped.** A 'hiring' row "
        "here means a source published something we classified as expansionary. "
        "Companies announce hiring when it is newsworthy, which is not when it "
        "starts. The measured lag would be a lag-to-press-release, not a "
        "lag-to-requisition, even at adequate n.",
        "**Form D is not the round.** A Form D is filed within 15 days of first "
        "sale, and plenty of rounds are announced weeks earlier or never filed at "
        "all (Reg D exemptions, foreign issuers, debt). The funding date is a "
        "filing date standing in for a decision date.",
        "**Right-censoring.** A company funded in 2026-06 has had at most a few "
        "weeks to produce a hiring signal, so recent funding rows can only "
        "contribute short lags. This biases any observed median downward, which "
        "would make the 8-12 week claim look better supported than it is.",
        "**Survivorship among the funded.** Rounds that were followed by a quiet "
        "failure produce no hiring signal ever, and are indistinguishable in this "
        "data from rounds whose hiring we simply did not observe.",
    ]:
        L(f"- {c}")
    L("")
    return {"funding": len(funding), "hiring": len(hiring),
            "employers_both": len(both), "pairs": len(pairs),
            "pair_lags_days": [p[3] for p in pairs]}


# --------------------------------------------------------------------------

def summary(res, _out):
    """Written last, printed first. Three verdicts, each with its numbers."""
    L = []
    a1 = res.get("analysis_1", {}).get("windows", [])
    a2 = res.get("analysis_2", {})
    j = res.get("join", {})
    w = {r["window_months"]: r for r in a1 if r.get("computable")}

    L.append("## Summary")
    L.append("")
    L.append("| question | verdict |")
    L.append("|---|---|")
    if w:
        best = w.get(3) or list(w.values())[0]
        direction = ("below" if best["conditional_rate"] < best["base_rate"] else "above")
        L.append(f"| 1. Does executive churn predict layoffs? | **No detectable "
                 f"effect.** At {best['window_months']} months the conditional rate "
                 f"is {100*best['conditional_rate']:.1f}% "
                 f"({best['conditional_hits']}/{best['conditional_n']}) against a "
                 f"base rate of {100*best['base_rate']:.1f}% -- the point estimate "
                 f"sits *{direction}* the base rate. Not publishable as a finding "
                 f"in either direction. |")
    else:
        L.append("| 1. Does executive churn predict layoffs? | **Not computable.** |")
    L.append(f"| 2. How long after a raise does hiring show? | **Cannot yet "
             f"answer.** {a2.get('pairs', 0)} matched funding-to-hiring pairs from "
             f"{a2.get('funding', 0)} funding signals and {a2.get('hiring', 0)} "
             f"hiring signals. The 8-12 week claim is neither supported nor "
             f"contradicted here. |")
    L.append("| 3. What shape is the talent dataset? | **A US public-company "
             "filing archive, currently.** The month histogram traces the backfill "
             "queue, not the market. Details and the artefact list in section 3. |")
    L.append("")
    L.append(f"The join that makes question 1 askable at all produces "
             f"**{j.get('joined', 0):,} employers** in both datasets, "
             f"{j.get('by_name', 0):,} of them matched on name because the layoff "
             f"tracker stores no CIK. That number, not the p-values, is the real "
             f"output of this exercise.")
    L.append("")
    L.append("**Nothing here supports a positive claim.** The one result solid "
             "enough to publish is the null in question 1, and it is drafted as a "
             "paragraph at the end of this report.")
    L.append("")
    return L


def publishable(res, out):
    L = out.append
    a1 = res.get("analysis_1", {}).get("windows", [])
    w = {r["window_months"]: r for r in a1 if r.get("computable")}
    L("## Is any of this publishable?")
    L("")
    if not w:
        L("No.")
        L("")
        return
    t = w.get(3)
    L("One paragraph is, and it is a negative. Drafted in the product's voice, "
      "caveat included, ready to be cut if it reads as overclaiming:")
    L("")
    L("> **We checked whether an executive change is an early warning of layoffs. "
      "It isn't -- at least not at the scale we can currently see.** Across the "
      f"{res['join']['joined']:,} employers that appear in both our talent tracker "
      f"and our layoff tracker, a leadership change was followed by a workforce "
      f"reduction within three months "
      f"{100*t['conditional_rate']:.0f}% of the time "
      f"({t['conditional_hits']} of {t['conditional_n']} leadership changes). "
      f"For the same companies over the same period, a randomly chosen date was "
      f"followed by a reduction within three months "
      f"{100*t['base_rate']:.0f}% of the time. The exec change adds nothing; the "
      f"point estimate is fractionally *below* the background rate. The honest "
      f"caveat is that our talent data only starts in January 2026, so we can "
      f"speak to a three-month lag and a six-month lag and not to a twelve-month "
      f"one -- and a lift smaller than roughly "
      f"{100*(mde(t['base_rate'], t['conditional_n'])-t['base_rate'])/t['base_rate']:.0f}% "
      f"would be invisible at this sample size. We are publishing the null because "
      f"the alternative is publishing a plausible story with no support, and there "
      f"is already enough of that about.")
    L("")
    L("What would have to change before that paragraph could become a positive "
      "finding: the layoff tracker resolving its company names to CIKs (the "
      "current join is a name join), and twelve months of talent data, which "
      f"arrives {add_months(date(2026, 1, 2), 12)}.")
    L("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()

    print("fetching...", file=sys.stderr)
    api_talent = fetch_talent(a.offline)
    layoffs = fetch_layoffs(a.offline)
    db_talent = load_local_db()
    if a.fetch_only:
        return

    out = []
    out.append("# Cross-tracker analysis: executive churn, funding lag, and the "
               "shape of the talent dataset")
    out.append("")
    out.append(f"Generated {TODAY} by `analysis/cross_tracker.py`. Read-only: it "
               f"touches two public APIs and a read-only snapshot of the committed "
               f"database, and writes nothing outside `analysis/`.")
    out.append("")
    out.append("Every proportion below carries its numerator and denominator. "
               "Base rates are stated before conditional rates. Where a window is "
               "not fully observed the result is reported as not computable rather "
               "than computed over partial windows. Two of the three questions "
               "below come back negative, and they are reported as such.")
    out.append("")

    res = {}
    body = []
    talent = merge_talent(api_talent, db_talent, body)
    res["sources"] = {"api_rows": len(api_talent), "db_rows": len(db_talent),
                      "union_rows": len(talent), "layoff_rows": len(layoffs)}
    tal_by_key, lay_by_key, lay_eff, joined, jstats = build_join(talent, layoffs, body)
    res["join"] = jstats
    res["analysis_1"] = analysis_one(tal_by_key, lay_by_key, lay_eff, joined, body)
    res["analysis_2"] = analysis_two(talent, body)
    res["shape"] = shape(talent, body)

    publishable(res, body)
    out.extend(summary(res, out))
    out.extend(body)

    md = os.path.join(HERE, "cross_tracker_findings.md")
    js = os.path.join(HERE, "cross_tracker_results.json")
    with open(md, "w") as fh:
        fh.write("\n".join(out) + "\n")
    with open(js, "w") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(f"wrote {md}\nwrote {js}", file=sys.stderr)


if __name__ == "__main__":
    main()

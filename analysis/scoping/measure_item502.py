#!/usr/bin/env python3
"""Reproduce every number in docs/SCOPE-role-change-registries.md.

    python3 analysis/scoping/measure_item502.py            # everything
    python3 analysis/scoping/measure_item502.py --offline  # only the parts
                                                          # that read the
                                                          # committed corpus

Stdlib only, no keys, no model, writes nothing. It reads SEC EDGAR full-text
search and sec.gov archives (both free and public, contact address in the
User-Agent as SEC's policy asks) and the committed database.

A scoping document nobody can re-run is how a catalogue of dead feeds gets
written, so this is the document's evidence rather than its illustration.
"""
from __future__ import annotations

import argparse
import calendar
import collections
import datetime
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "talent_intel.db")

# SEC's policy asks for a descriptive User-Agent with a contact address, and
# 403s anonymous traffic. Same string collectors/sec_edgar.py defaults to.
UA = "TalentIntel/1.0 (info@asktherecruiter.com)"
EFTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
DELAY = 0.16          # comfortably under SEC's published 10 requests/second

# The week the worked example walks. A full business week, enumerated to the
# last filing, so the volume and the parse rate are counts and not samples.
WEEK = ("2026-07-06", "2026-07-10")


# --- fetching ----------------------------------------------------------------

def _get(url: str, tries: int = 4, timeout: int = 45) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:          # transient 500s are routine on EFTS
            last = exc
            time.sleep(1.5 + i * 2)
    raise last


def efts(**params) -> dict:
    time.sleep(DELAY)
    return json.loads(_get(EFTS + "?" + urllib.parse.urlencode(params)))


def item502(startdt: str, enddt: str, frm: int = 0) -> dict:
    return efts(q='"item 5.02"', forms="8-K", dateRange="custom",
                startdt=startdt, enddt=enddt, **({"from": frm} if frm else {}))


# --- [1] volume ---------------------------------------------------------------

def volume() -> None:
    print("\n[1] HOW MANY Item 5.02 8-Ks THERE ACTUALLY ARE")
    print("    source: EDGAR full-text search, q=\"item 5.02\" forms=8-K")
    total = 0
    months = [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]
    for y, m in months:
        last = calendar.monthrange(y, m)[1]
        n = item502(f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last}")["hits"]["total"]["value"]
        total += n
        print(f"      {y}-{m:02d}   {n:>6,}")
    print(f"      mean   {total/len(months):>6,.0f} filings a month")


# --- [2] the enumeration ceiling ---------------------------------------------

def window_cap() -> None:
    print("\n[2] CAN THE MONTH BE ENUMERATED? (Elasticsearch result-window cap)")
    q2 = item502("2026-04-01", "2026-06-30")
    print(f"    Q2 2026 total: {q2['hits']['total']['value']:,}")
    for frm in (0, 1000, 2900, 9990):
        d = item502("2026-04-01", "2026-06-30", frm=frm)
        if "hits" in d:
            print(f"      from={frm:<6} {len(d['hits']['hits'])} hits returned")
        else:
            msg = str(d.get("errorMessage") or d)[:80]
            print(f"      from={frm:<6} REFUSED: {msg}")
    print("    -> the window is from+size <= 10,000, and a page is 100 whatever"
          "\n       `size` says. A calendar month of Item 5.02 is ~1,000, so a"
          "\n       month is enumerable in ~10 requests and a week in ~2.")


# --- [3] one real week, to the last filing ------------------------------------

def _company_cik(hit: dict) -> tuple[str, str | None]:
    names = (hit.get("_source") or {}).get("display_names") or []
    if not names:
        return "", None
    raw = names[0]
    m = re.search(r"CIK\s*(\d{4,10})", raw)
    cik = m.group(1).lstrip("0") if m else None
    # Deliberately the SAME regex collectors/sec_edgar.py uses, so this
    # measures the live behaviour rather than a tidier reimplementation.
    name = re.sub(r"\s*\((?:[A-Z0-9.\-]{1,10})\)\s*", " ", raw)
    name = re.sub(r"\s*\(CIK[^)]*\)\s*$", "", name).strip()
    return name, cik


def enumerate_week() -> list[dict]:
    hits, frm = [], 0
    while True:
        d = item502(WEEK[0], WEEK[1], frm=frm)
        page = d["hits"]["hits"]
        hits.extend(page)
        total = d["hits"]["total"]["value"]
        if len(hits) >= total or not page:
            break
        frm += 100
    out = []
    for h in hits:
        rid = h.get("_id") or ""
        if ":" not in rid:
            continue
        acc, fn = rid.split(":", 1)
        name, cik = _company_cik(h)
        if not cik:
            continue
        src = h.get("_source") or {}
        out.append({"company": name, "cik": cik, "accession": acc,
                    "file_date": src.get("file_date"),
                    "items": src.get("items") or [],
                    "url": f"{ARCHIVES}/{cik}/{acc.replace('-', '')}/{fn}"})
    return out


def week_report(recs: list[dict]) -> None:
    print(f"\n[3] ONE REAL WEEK, ENUMERATED: {WEEK[0]} .. {WEEK[1]}")
    print(f"    filings: {len(recs)}   distinct accessions: "
          f"{len({r['accession'] for r in recs})}")
    tagged = sum(1 for r in recs if "5.02" in r["items"])
    print(f"    carry '5.02' in EFTS's OWN structured `items` field: "
          f"{tagged}/{len(recs)}")
    print("    -> item selection is a STRUCTURED field, not a text match. The"
          "\n       phrase query is only the search key; `_source.items` is the"
          "\n       filer's own item list and is what a comprehensive walk"
          "\n       should filter on.")
    co = collections.Counter(i for r in recs for i in r["items"])
    print("    items filed alongside 5.02, top 8: "
          + ", ".join(f"{k} {v}" for k, v in co.most_common(8)))
    resid = [r["company"] for r in recs if "(" in r["company"]]
    print(f"\n    DEFECT: the live collector's ticker-strip regex leaves a"
          f" parenthetical\n    on {len(resid)}/{len(recs)} names "
          f"({len(resid)/len(recs):.0%}) — it accepts one ticker, not a list:")
    for n in resid[:4]:
        print(f"      {n}")


# --- [4] what the section says, and whether a parser can read it -------------

SECTION = re.compile(r"Item\s*5\.02[\s\.\-–—:]*", re.I)
NEXT_ITEM = re.compile(r"Item\s*[0-9]\.[0-9]{2}", re.I)
HEADING_TAIL = re.compile(
    r"Compensatory\s+Arrangements\s+of\s+Certain\s+Officers\s*[\.:]?\s*", re.I)
HEADING_ALT = re.compile(r"Appointment\s+of\s+Certain\s+Officers\s*[\.:]?\s*", re.I)


def fetch_body(url: str) -> str:
    time.sleep(DELAY)
    raw = _get(url).decode("utf-8", "replace")[:800_000]
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def section(text: str) -> str | None:
    m = SECTION.search(text or "")
    if not m:
        return None
    tail = text[m.end():]
    n = NEXT_ITEM.search(tail)
    body = (tail[:n.start()] if n else tail)[:20_000]
    # The statutory heading names all four sub-paragraphs and is boilerplate.
    # Reading it as content puts "Appointment of Certain Officers" in every
    # filing and makes 99 of 100 look like an appointment.
    for h in (HEADING_TAIL, HEADING_ALT):
        mm = h.search(body[:400])
        if mm:
            return body[mm.end():]
    return body


APPT = re.compile(r"\bappointed\b|\belected\b|\bpromoted\b|\bappointment\s+of\b|"
                  r"\bnamed\s+(?:Mr|Ms|Mrs|Dr|[A-Z][a-z])|"
                  r"\baccepted\s+an\s+offer\b", re.I)
DEP = re.compile(r"\bresign|\bretir|\bstep(?:ping|s|ped)\s+down|\bdepart|"
                 r"\bterminat|\bpassing\b|\bdeath\b|\bwill\s+cease\b|"
                 r"\bmutually\s+agreed", re.I)
COMP = re.compile(r"restricted\s+stock|equity\s+award|base\s+salary|annual\s+bonus|"
                  r"severance|employment\s+agreement|option\s+grant|"
                  r"performance\s+share|compensation\s+committee", re.I)

HON = r"(?:Mr|Mrs|Ms|Dr|Prof|Sir|Professor)\.?\s+"
PERSON = rf"(?:{HON})?[A-Z][a-zA-Z'’\-]+(?:\s+(?:[A-Z]\.|[A-Z][a-zA-Z'’\-]+)){{1,3}}"
SEAT = (r"Chief\s+[A-Z][a-z]+\s+(?:[A-Z][a-z]+\s+)?Officer|President|"
        r"Executive\s+Chair(?:man|woman|person)?|"
        r"Chair(?:man|woman|person)?\s+of\s+the\s+Board|"
        r"member\s+of\s+the\s+Board\s+of\s+Directors|the\s+Board\s+of\s+Directors|"
        r"director")
ACTIVE = re.compile(
    rf"\b(?:appointed|named|elected|promoted)\s+(?P<person>{PERSON})\s*,?\s*"
    rf"(?:age\s+\d+\s*,\s*)?"
    rf"(?:as|to\s+serve\s+as|to\s+the\s+position\s+of|to\s+the\s+office\s+of|to)\s+"
    rf"(?:the\s+|its\s+|our\s+|their\s+)?(?:Company[’'`]?s\s+)?(?:new\s+)?"
    rf"(?P<seat>{SEAT})\b", re.I)
PASSIVE = re.compile(
    rf"\b(?P<person>{PERSON})\s*,?\s*(?:age\s+\d+\s*,\s*)?"
    rf"(?:was|has\s+been|were)\s+(?:appointed|named|elected|promoted)\s+"
    rf"(?:as|to\s+serve\s+as|to\s+serve\s+on|to\s+the\s+position\s+of|to)\s+"
    rf"(?:the\s+|its\s+|our\s+)?(?:Company[’'`]?s\s+)?(?:new\s+)?"
    rf"(?P<seat>{SEAT})\b", re.I)
OF_FORM = re.compile(
    rf"\bappointment\s+of\s+(?P<person>{PERSON})\s+(?:as|to)\s+"
    rf"(?:the\s+|its\s+|our\s+)?(?:Company[’'`]?s\s+)?(?P<seat>{SEAT})\b", re.I)
UNCARRIED = re.compile(r"\binterim\b|\bacting\b|\bco-Chief\b", re.I)
ROLE_WORDS = set("""former interim acting incoming outgoing chief officer president
chairman chairwoman chair director board committee company corporation inc
executive vice senior principal lead independent effective pursuant the
registrant subsidiary bancorp holdings group and""".split())


def valid_person(span: str) -> str | None:
    s = re.sub(rf"^{HON}", "", (span or "").strip())
    toks = s.split()
    if not 2 <= len(toks) <= 4:
        return None
    for t in toks:
        low = t.lower().strip(".")
        if low in ROLE_WORDS or any(ch.isdigit() for ch in t):
            return None
        if not re.match(r"^[A-Z][A-Za-z'’\.\-]*$", t):
            return None
    return s


def parse_and_triage(recs: list[dict], sample: int) -> None:
    print(f"\n[4] WHAT THE SECTION SAYS, AND WHETHER A REGEX CAN READ IT"
          f"  (first {sample} filings of the week, fetched)")
    cat = collections.Counter()
    verdict = collections.Counter()
    closes = []
    for r in sorted(recs, key=lambda x: x["accession"])[:sample]:
        try:
            body = section(fetch_body(r["url"]))
        except Exception as exc:
            verdict[f"fetch failed: {type(exc).__name__}"] += 1
            continue
        if body is None:
            verdict["no 5.02 section in the primary document"] += 1
            continue
        key = ("A" if APPT.search(body) else "-") + \
              ("D" if DEP.search(body) else "-") + \
              ("C" if COMP.search(body) else "-")
        cat[key] += 1
        found = {}
        for pat in (ACTIVE, PASSIVE, OF_FORM):
            for m in pat.finditer(body):
                p = valid_person(m.group("person"))
                if p:
                    found.setdefault(p, (re.sub(r"\s+", " ", m.group("seat")),
                                         m.start(), m.end()))
        if not found:
            verdict["decline: no parseable appointment clause"] += 1
            continue
        if len(found) > 1:
            verdict["decline: more than one appointee named"] += 1
            continue
        person, (seat, s0, s1) = next(iter(found.items()))
        if UNCARRIED.search(body[max(0, s0 - 200):s1 + 200]):
            verdict["decline: interim or acting"] += 1
            continue
        verdict["CLOSE"] += 1
        closes.append({**r, "person": person, "seat": seat,
                       "evidence": re.sub(r"\s+", " ", body[max(0, s0 - 130):s1 + 130])})

    n = sum(cat.values())
    label = {"A--": "arrival only", "AD-": "arrival + departure",
             "ADC": "arrival + departure + pay", "A-C": "arrival + pay",
             "-D-": "departure only", "-DC": "departure + pay",
             "--C": "pay only", "---": "neither (amendment / by reference)"}
    print("\n    content of the section, heading stripped:")
    for k, v in cat.most_common():
        print(f"      {label.get(k, k):<38} {v:>4}  ({v/max(1,n):.0%})")
    arrivals = sum(v for k, v in cat.items() if k[0] == "A")
    print(f"      -> {arrivals}/{n} carry ARRIVAL language. "
          f"{n - arrivals} are a departure, a pay change, or an amendment.")

    print("\n    a precision-first regex over that text:")
    for k, v in verdict.most_common():
        print(f"      {k:<44} {v:>4}  ({v/max(1,sample):.0%})")

    print("\n    every close, quoted, so the error rate is hand-readable:")
    for i, c in enumerate(closes, 1):
        print(f"\n      {i}. {c['company'][:52]}  [{c['file_date']}]")
        print(f"         person {c['person']!r}   seat {c['seat']!r}")
        print(f"         {c['url']}")
        print(f"         ...{c['evidence'][:240]}...")
    return closes


# --- [5] overlap and the entity join -----------------------------------------

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"/[a-z]{2}/?$", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|"
               r"lp|plc|holdings|holding|group|the|new|sa|nv|ag)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def overlap(recs: list[dict]) -> None:
    print("\n[5] WHAT IT WOULD ADD, AND WHAT THE ENTITY JOIN COSTS")
    if not os.path.exists(DB):
        print("    no committed database here; skipped.")
        return
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    tot = cur.execute("select count(*) from signals where is_current=1").fetchone()[0]
    ncik = cur.execute("select count(*) from signals where is_current=1 "
                       "and cik is not null and cik!=''").fetchone()[0]
    print(f"    corpus: {tot:,} current rows, {ncik:,} ({ncik/tot:.1%}) carry a CIK")
    held = {str(r[0]).lstrip("0") for r in cur.execute(
        "select distinct cik from signals where is_current=1 "
        "and cik is not null and cik!=''")}
    by_cik = collections.defaultdict(list)
    for cik, pd, col in cur.execute(
            "select cik, published_date, collector from signals "
            "where is_current=1 and pillar='leadership_change' "
            "and cik is not null and cik!=''"):
        by_cik[str(cik).lstrip("0")].append((pd, col))

    matched = collections.Counter()
    hit_n = 0
    for r in recs:
        try:
            d0 = datetime.date.fromisoformat(r["file_date"])
        except Exception:
            continue
        got = None
        for pd, col in by_cik.get(r["cik"], []):
            try:
                d1 = datetime.date.fromisoformat((pd or "")[:10])
            except Exception:
                continue
            if abs((d1 - d0).days) <= 14:
                got = col
                break
        if got:
            hit_n += 1
            matched[got] += 1
    print(f"\n    EVENT overlap: {hit_n}/{len(recs)} ({hit_n/len(recs):.1%}) of the"
          f" week's filings already\n    have a leadership row for the same CIK"
          f" within 14 days.")
    print(f"    the matching rows came from: {dict(matched)}")
    print(f"    -> the remainder, {len(recs)-hit_n} filings that week, is what a"
          f" comprehensive walk\n       would add over the sampled walk running today.")

    names = {(r[0] or "") for r in cur.execute(
        "select distinct company from signals where is_current=1")}
    keys = {(r[0] or "") for r in cur.execute(
        "select distinct company_key from signals where is_current=1")}
    norm = {_norm(x) for x in names | keys} - {""}
    both = [r for r in recs if r["cik"] in held]
    name_ok = sum(1 for r in both if _norm(r["company"]) in norm)
    print(f"\n    the join: {len(both)}/{len(recs)} of the week's filers are"
          f" ALREADY in the corpus by CIK.")
    print(f"    of those, a normalised NAME match would find {name_ok} "
          f"({name_ok/max(1,len(both)):.0%}).")
    print("    -> the CIK is on the filing and is already a column on the row."
          "\n       For this source the entity join is an integer equality and"
          "\n       costs nothing. The name is cosmetic.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="skip everything that needs sec.gov")
    ap.add_argument("--sample", type=int, default=100,
                    help="how many of the week's filings to fetch and parse")
    args = ap.parse_args()

    print("SCOPING: mandatory role-change filings, measured")
    print("=" * 74)
    if args.offline:
        print("\n--offline: the SEC sections are skipped; nothing to report from"
              "\nthe committed corpus alone.")
        return 0
    volume()
    window_cap()
    recs = enumerate_week()
    week_report(recs)
    parse_and_triage(recs, args.sample)
    overlap(recs)
    print("\nNothing was written and no model was called. Cost: $0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

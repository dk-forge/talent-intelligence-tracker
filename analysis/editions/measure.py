"""Does a Google News edition return that country's press, or the US wire again?

    python3 -m analysis.editions.measure                  # every rotated edition
    python3 -m analysis.editions.measure en-GB pt-BR      # named editions only
    python3 -m analysis.editions.measure --json out.json  # keep the raw items

Stdlib only, keyless, free. It fetches RSS and counts; it stores nothing, calls
no model and does not resolve a single redirect.

THREE NUMBERS, AND THE ORDER MATTERS
------------------------------------
1. **The churn floor.** en-US is pulled at the start and again at the end, and
   the self-overlap is printed first. Without it, "edition X differs from en-US
   by 40%" is unreadable: the index moves under a long run and that difference
   might be nothing but time. It was 99.2% on 2026-08-01, so it was not.

2. **Overlap with the anchor.** How much of this edition is already in en-US.
   Useful, and on its own MISLEADING — the first measurement of these editions
   used one query, found 100%, and concluded they were identical. On the full
   production pack they overlap 52-69%, which reads like real difference and is
   not: the part that differs is the same global English wire re-ranked.

3. **Local-publisher share, which is the one that decides.** How many of the
   edition's items come from a publisher IN that country, and of those, how many
   are IN SCOPE (they survive the same free prefilter production runs them
   through) and come from a publisher `national_press` does not already read
   twice a day. That last column is an edition's marginal value, because a
   market whose press we already read gains nothing from meeting it again by
   chance, and a local story about a football transfer is not a talent signal.
   On 2026-08-01 every English non-US edition scored 0-5 and every non-English
   one scored 53-163.

"In that country" is decided by `data/sources_catalogue.csv` first — the
repo's own country-to-publisher map, which knows irishtimes.com is Irish — and
by ccTLD as a fallback. Both are conservative: a local publisher on a .com the
catalogue has never heard of counts as foreign, so the local share is a LOWER
bound and the argument for withdrawing an edition is stronger than it reads.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import source_registry as registry  # noqa: E402
from pipeline import prefilter  # noqa: E402  (regex only, no third-party import)

RSS_ENDPOINT = "https://news.google.com/rss/search"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
CATALOGUE = REPO_ROOT / "data" / "sources_catalogue.csv"
PAUSE = 1.0

#: ISO2 -> the catalogue's country name. Only the editions we query, plus the
#: withdrawn ones, so a re-measurement can ask whether a withdrawal still holds.
COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada",
    "AU": "Australia", "IN": "India", "IE": "Ireland", "SG": "Singapore",
    "NZ": "New Zealand", "ZA": "South Africa", "PH": "Philippines",
    "NG": "Nigeria", "KE": "Kenya", "GH": "Ghana", "PK": "Pakistan",
    "BD": "Bangladesh", "MY": "Malaysia", "HK": "Hong Kong", "IL": "Israel",
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "FR": "France",
    "BE": "Belgium", "ES": "Spain", "MX": "Mexico", "AR": "Argentina",
    "CL": "Chile", "CO": "Colombia", "BR": "Brazil", "PT": "Portugal",
    "IT": "Italy", "NL": "Netherlands", "PL": "Poland", "SE": "Sweden",
    "TR": "Turkey", "ID": "Indonesia", "VN": "Vietnam", "JP": "Japan",
    "KR": "South Korea", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
    "EG": "Egypt", "QA": "Qatar", "MA": "Morocco", "PE": "Peru",
    "EC": "Ecuador", "UY": "Uruguay", "SN": "Senegal",
}

#: Fallback when the catalogue has no row for a domain. Deliberately short:
#: a wrong entry here would credit an edition with locality it does not have.
CCTLD = {
    "GB": ".uk", "CA": ".ca", "AU": ".au", "IN": ".in", "IE": ".ie",
    "SG": ".sg", "NZ": ".nz", "ZA": ".za", "PH": ".ph", "NG": ".ng",
    "KE": ".ke", "GH": ".gh", "PK": ".pk", "BD": ".bd", "MY": ".my",
    "HK": ".hk", "IL": ".il", "DE": ".de", "AT": ".at", "CH": ".ch",
    "FR": ".fr", "BE": ".be", "ES": ".es", "MX": ".mx", "AR": ".ar",
    "CL": ".cl", "CO": ".co", "BR": ".br", "PT": ".pt", "IT": ".it",
    "NL": ".nl", "PL": ".pl", "SE": ".se", "TR": ".tr", "ID": ".id",
    "VN": ".vn", "JP": ".jp", "KR": ".kr", "AE": ".ae", "SA": ".sa",
    "EG": ".eg", "QA": ".qa", "MA": ".ma", "PE": ".pe", "EC": ".ec",
    "UY": ".uy", "SN": ".sn",
}


def domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def catalogue_domains() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(every catalogued publisher per country, those we actually fetch)."""
    listed: dict[str, set[str]] = {}
    fetched: dict[str, set[str]] = {}
    if not CATALOGUE.exists():
        return listed, fetched
    with CATALOGUE.open(newline="") as fh:
        for row in csv.DictReader(fh):
            country = (row.get("country") or "").strip()
            site = domain(row.get("url") or "")
            if site:
                listed.setdefault(country, set()).add(site)
            rss = (row.get("rss") or "").strip()
            if rss.startswith("http"):
                fetched.setdefault(country, set()).add(domain(rss))
    return listed, fetched


def fetch(query: str, lang: str, country: str, *, timeout: int = 45) -> list[dict]:
    params = {"q": query, "hl": lang, "gl": country, "ceid": f"{country}:{lang}"}
    req = urllib.request.Request(
        f"{RSS_ENDPOINT}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()

    items = []
    for node in ET.fromstring(body).findall(".//item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not (title and link):
            continue
        source = node.find("source")
        items.append({
            "title": title,
            # Google's own article id. Stable across editions, which is what
            # makes "the same item" mean the same item.
            "aid": link.rstrip("/").rsplit("/", 1)[-1].split("?")[0],
            "outlet": (source.text or "").strip() if source is not None else "",
            "outlet_url": (source.get("url") or "").strip() if source is not None else "",
        })
    return items


def edition(lang: str, country: str, *, window: int, pause: float = PAUSE) -> list[dict]:
    """One edition, asked exactly what production asks it."""
    seen, out = set(), []
    for query in registry.google_news_queries(lang, window_days=window):
        try:
            got = fetch(query, lang, country)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            print(f"  {country}:{lang} query failed ({type(exc).__name__}), "
                  f"counted as zero for this query", file=sys.stderr)
            got = []
        for item in got:
            if item["aid"] not in seen:
                seen.add(item["aid"])
                out.append(item)
        time.sleep(pause)
    return out


def is_local(item: dict, iso2: str, listed: set[str], fetched: set[str]) -> tuple[bool, bool]:
    """(published in that country, and already read by national_press)."""
    host = domain(item["outlet_url"])
    if not host:
        return False, False
    already = any(host == d or host.endswith("." + d) or d.endswith("." + host)
                  for d in fetched)
    known = already or any(host == d or host.endswith("." + d) for d in listed)
    tld = CCTLD.get(iso2, "")
    return (known or (bool(tld) and host.endswith(tld))), already


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("editions", nargs="*",
                        help="lang-COUNTRY pairs, e.g. en-GB. Default: the "
                             "whole rotation plus the withdrawn English ones.")
    parser.add_argument("--window", type=int, default=7,
                        help="the `when:Nd` recency window to ask for")
    parser.add_argument("--pause", type=float, default=PAUSE)
    parser.add_argument("--json", metavar="PATH",
                        help="write every item, so the counts can be re-derived")
    args = parser.parse_args(argv)

    if args.editions:
        targets = []
        for spec in args.editions:
            lang, _, country = spec.partition("-")
            targets.append((lang, country.upper()))
    else:
        targets = ([("en", cc) for cc in registry.WITHDRAWN_ENGLISH_EDITIONS]
                   + list(registry.GOOGLE_NEWS_LOCALES))

    listed, fetched = catalogue_domains()
    anchor_lang, anchor_cc = registry.GOOGLE_NEWS_ANCHOR

    print(f"anchor {anchor_cc}:{anchor_lang} ...", file=sys.stderr)
    anchor = edition(anchor_lang, anchor_cc, window=args.window, pause=args.pause)
    anchor_ids = {i["aid"] for i in anchor}
    raw = {f"{anchor_cc}:{anchor_lang}": anchor}

    rows = []
    for lang, cc in targets:
        if (lang, cc) == registry.GOOGLE_NEWS_ANCHOR:
            continue
        print(f"{cc}:{lang} ...", file=sys.stderr)
        items = edition(lang, cc, window=args.window, pause=args.pause)
        raw[f"{cc}:{lang}"] = items
        if not items:
            rows.append((f"{lang}-{cc}", 0, None, None, None, ""))
            continue

        country_name = COUNTRY_NAMES.get(cc, "")
        same = sum(1 for i in items if i["aid"] in anchor_ids)
        local = new_local = 0
        publishers: Counter = Counter()
        for item in items:
            here, already = is_local(item, cc, listed.get(country_name, set()),
                                     fetched.get(country_name, set()))
            if not here:
                continue
            local += 1
            # The same free filter production applies, so this column counts
            # what an edition would actually have CONTRIBUTED and not what it
            # merely returned. Titles only: the measurement does not resolve
            # redirects, so there is no body text to filter on, which makes
            # this a lower bound too.
            if not already and prefilter.passes(item["title"])[0]:
                new_local += 1
                publishers[item["outlet"] or domain(item["outlet_url"])] += 1
        rows.append((f"{lang}-{cc}", len(items), 100.0 * same / len(items),
                     100.0 * local / len(items), new_local,
                     ", ".join(n for n, _ in publishers.most_common(4))))

    print(f"re-checking the anchor for churn ...", file=sys.stderr)
    recheck = edition(anchor_lang, anchor_cc, window=args.window, pause=args.pause)
    floor = (100.0 * sum(1 for i in recheck if i["aid"] in anchor_ids) / len(recheck)
             if recheck else 0.0)
    raw[f"{anchor_cc}:{anchor_lang}:recheck"] = recheck

    print(f"\nCHURN FLOOR: the anchor re-fetched at the end of this run repeats "
          f"{floor:.1f}% of its own first pull.")
    print("A difference smaller than that floor is the index moving, not an "
          "edition differing.\n")
    print(f"{'edition':10}{'items':>7}{'same as anchor':>16}{'local':>8}"
          f"{'NEW local':>11}   publishers national_press does not read")
    for name, n, same, local, new_local, publishers in rows:
        if not n:
            print(f"{name:10}{n:>7}   (returned nothing)")
            continue
        print(f"{name:10}{n:>7}{same:>15.1f}%{local:>7.1f}%{new_local:>11}"
              f"   {publishers}")

    if args.json:
        Path(args.json).write_text(json.dumps(raw, indent=1, ensure_ascii=False))
        print(f"\nraw items written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

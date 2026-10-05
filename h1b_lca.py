#!/usr/bin/env python3
"""US DOL OFLC H-1B / LCA disclosure file -> employer-level aggregate (reference store).

    python3 h1b_lca.py --discover                  # print the latest listed file
    python3 h1b_lca.py --xlsx FILE --store         # reduce a local file
    python3 h1b_lca.py --download DIR --store      # fetch the latest, reduce, store

SOURCE. The Office of Foreign Labor Certification publishes one cumulative
Labor Condition Application disclosure file per fiscal year, refreshed each
quarter (`LCA_Disclosure_Data_FY<YYYY>_Q<n>.xlsx`, ~250 MB, ~1M rows) at
https://www.dol.gov/agencies/eta/foreign-labor/performance. US Government work,
public domain; we cite DOL OFLC anyway.

PERSONAL DATA IS NEVER READ, LET ALONE STORED. The file carries natural persons
(employer points of contact, attorneys and agents: names, emails, phones) and
case numbers. The reader below extracts ONLY the columns in READ_COLUMNS --
employer, NAICS, worksite state/county, SOC code, visa class, status, positions,
wage -- by header name; every other cell is skipped unparsed. The output is an
aggregate: one row per (employer, NAICS, worksite state, worksite county, SOC
major group) with counts and wage quartiles. No case number, no person, no
street address, no individual wage row.

RAW FILE -> GitHub Release asset only (h1b-lca.yml), never git.

REDUCTION. VISA_CLASS == "H-1B" only (the file also carries H-1B1 and E-3).
`cases` counts every H-1B application; `certified` counts CASE_STATUS
"Certified" (and "Certified - Withdrawn"); wage quartiles are over certified
cases with a usable WAGE_RATE_OF_PAY_FROM, annualised by WAGE_UNIT_OF_PAY
(hour x2080, week x52, bi-weekly x26, month x12) and rounded to $100.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import statistics
import sys
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import reference_store
from h1b_join import normalise_employer

SOURCE = "h1b_lca"
PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
LICENCE = "Public domain (US Government work)"
ATTRIBUTION = (
    "H-1B Labor Condition Application disclosure data: U.S. Department of Labor, "
    "Office of Foreign Labor Certification (dol.gov/agencies/eta/foreign-labor/"
    "performance). Public domain. Aggregated by employer, worksite and SOC major "
    "group by us; no personal data is kept.")
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
TABLE = "employers.csv.gz"
MAX_DOWNLOAD = 900_000_000
MAX_TABLE_BYTES = 45_000_000      # GitHub warns at 50 MB; refuse before that

READ_COLUMNS = (
    "CASE_STATUS", "VISA_CLASS", "SOC_CODE", "TOTAL_WORKER_POSITIONS",
    "EMPLOYER_NAME", "NAICS_CODE", "WORKSITE_COUNTY", "WORKSITE_STATE",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
)
REQUIRED = ("CASE_STATUS", "VISA_CLASS", "SOC_CODE", "EMPLOYER_NAME",
            "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY")

SOC_MAJOR = {
    "11": "Management", "13": "Business and Financial Operations",
    "15": "Computer and Mathematical", "17": "Architecture and Engineering",
    "19": "Life, Physical, and Social Science", "21": "Community and Social Service",
    "23": "Legal", "25": "Educational Instruction and Library",
    "27": "Arts, Design, Entertainment, Sports, and Media",
    "29": "Healthcare Practitioners and Technical", "31": "Healthcare Support",
    "33": "Protective Service", "35": "Food Preparation and Serving Related",
    "37": "Building and Grounds Cleaning and Maintenance",
    "39": "Personal Care and Service", "41": "Sales and Related",
    "43": "Office and Administrative Support", "45": "Farming, Fishing, and Forestry",
    "47": "Construction and Extraction", "49": "Installation, Maintenance, and Repair",
    "51": "Production", "53": "Transportation and Material Moving",
    "55": "Military Specific",
}
ANNUAL = {"year": 1, "month": 12, "bi-weekly": 26, "week": 52, "hour": 2080}
FIELDS = ("employer_key", "employer", "naics", "worksite_state", "worksite_county",
          "soc_major", "job_family", "cases", "certified", "positions",
          "wage_n", "wage_p25", "wage_median", "wage_p75")

_FILE_RE = re.compile(r'href="([^"]*LCA_Disclosure_Data_FY(\d{4})_Q(\d)[^"]*\.xlsx)"', re.I)
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class SourceError(RuntimeError):
    pass


# --- discovery -----------------------------------------------------------------

def discover(html: str) -> dict:
    """The newest FY/quarter file listed on the performance page."""
    found = [(int(fy), int(q), href) for href, fy, q in _FILE_RE.findall(html)]
    if not found:
        raise SourceError("no LCA_Disclosure_Data_FY*_Q*.xlsx link on the page")
    fy, q, href = max(found)
    return {"url": urllib.parse.urljoin("https://www.dol.gov", href),
            "file": href.rsplit("/", 1)[-1], "fiscal_year": fy, "quarter": q,
            "as_of": quarter_end(fy, q)}


def quarter_end(fy: int, q: int) -> str:
    """US federal FY: Q1 = Oct-Dec of the prior calendar year."""
    return {1: f"{fy - 1}-12-31", 2: f"{fy}-03-31", 3: f"{fy}-06-30", 4: f"{fy}-09-30"}[q]


# --- streaming xlsx reader (stdlib) -------------------------------------------

def _col(ref: str) -> str:
    return re.match(r"[A-Z]+", ref)[0]


def iter_rows(path, columns=READ_COLUMNS):
    """Yield {column: text} for the named header columns only. Other cells skipped."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as f:
                for _, el in ET.iterparse(f):
                    if el.tag == _NS + "si":
                        strings.append("".join(t.text or "" for t in el.iter(_NS + "t")))
                        el.clear()
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        letters: dict[str, str] = {}
        first = True
        with z.open(sheet) as f:
            for _, el in ET.iterparse(f):
                if el.tag != _NS + "row":
                    continue
                cells = {}
                for c in el.iter(_NS + "c"):
                    letter = _col(c.get("r", ""))
                    if not first and letter not in letters:
                        continue
                    t = c.get("t")
                    v = c.find(_NS + "v")
                    if t == "inlineStr":
                        txt = "".join(x.text or "" for x in c.iter(_NS + "t"))
                    elif v is None:
                        txt = ""
                    elif t == "s":
                        txt = strings[int(v.text)]
                    else:
                        txt = v.text or ""
                    cells[letter] = txt
                el.clear()
                if first:
                    letters = {L: name.strip().upper() for L, name in cells.items()
                               if name.strip().upper() in columns}
                    missing = set(REQUIRED) - set(letters.values())
                    if missing:
                        raise SourceError(f"header lacks {sorted(missing)}; schema changed")
                    first = False
                    continue
                yield {letters[L]: v for L, v in cells.items()}


# --- reduction -----------------------------------------------------------------

def annual_wage(value: str, unit: str) -> float | None:
    try:
        v = float(str(value).replace("$", "").replace(",", ""))
    except ValueError:
        return None
    mult = ANNUAL.get((unit or "").strip().lower())
    if not mult or v <= 0:
        return None
    w = v * mult
    # Under full-time federal minimum wage or over $2M is a unit-entry error.
    return w if 15_000 <= w <= 2_000_000 else None


def _q(sorted_vals, p):
    if not sorted_vals:
        return ""
    if len(sorted_vals) == 1:
        return int(round(sorted_vals[0], -2))
    return int(round(statistics.quantiles(sorted_vals, n=4, method="inclusive")[p], -2))


def reduce(rows) -> tuple[list[dict], dict]:
    groups: dict[tuple, dict] = {}
    names: dict[str, Counter] = defaultdict(Counter)
    seen = kept = 0
    for r in rows:
        seen += 1
        if (r.get("VISA_CLASS") or "").strip().upper() != "H-1B":
            continue
        raw = (r.get("EMPLOYER_NAME") or "").strip()
        key = normalise_employer(raw)
        if not key:
            continue
        kept += 1
        soc = re.sub(r"\D", "", r.get("SOC_CODE") or "")[:2]
        naics = re.sub(r"\D", "", str(r.get("NAICS_CODE") or "").split(".")[0])
        state = (r.get("WORKSITE_STATE") or "").strip().upper()[:2]
        county = (r.get("WORKSITE_COUNTY") or "").strip().upper()
        g = groups.setdefault((key, naics, state, county, soc),
                              {"cases": 0, "certified": 0, "positions": 0, "wages": []})
        names[key][raw] += 1
        g["cases"] += 1
        status = (r.get("CASE_STATUS") or "").strip().lower()
        if status.startswith("certified"):
            g["certified"] += 1
            try:
                g["positions"] += int(float(r.get("TOTAL_WORKER_POSITIONS") or 0))
            except ValueError:
                pass
            w = annual_wage(r.get("WAGE_RATE_OF_PAY_FROM"), r.get("WAGE_UNIT_OF_PAY"))
            if w:
                g["wages"].append(w)
    display = {k: c.most_common(1)[0][0] for k, c in names.items()}
    table = []
    for (key, naics, state, county, soc), g in sorted(groups.items()):
        w = sorted(g["wages"])
        table.append({
            "employer_key": key, "employer": display[key], "naics": naics,
            "worksite_state": state, "worksite_county": county, "soc_major": soc,
            "job_family": SOC_MAJOR.get(soc, ""), "cases": g["cases"],
            "certified": g["certified"], "positions": g["positions"], "wage_n": len(w),
            "wage_p25": _q(w, 0), "wage_median": _q(w, 1), "wage_p75": _q(w, 2),
        })
    stats = {"rows_read": seen, "h1b_cases": kept, "employers": len(display),
             "table_rows": len(table)}
    return table, stats


def to_csv_gz(table: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(table)
    return gzip.compress(buf.getvalue().encode(), compresslevel=9, mtime=0)


def top_employers(table: list[dict], n: int = 100) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in table:
        a = agg.setdefault(r["employer_key"], {"employer": r["employer"], "cases": 0,
                                               "certified": 0, "positions": 0})
        for k in ("cases", "certified", "positions"):
            a[k] += r[k]
    return sorted(agg.values(), key=lambda a: (-a["cases"], a["employer"]))[:n]


def store(table, stats, meta, match=None, store_dir=reference_store.STORE) -> dict:
    body = to_csv_gz(table)
    if len(body) > MAX_TABLE_BYTES:
        raise SourceError(f"aggregate is {len(body)}B gzipped, over {MAX_TABLE_BYTES}B")
    extra = {"file": meta["file"], "fiscal_year": meta["fiscal_year"],
             "quarter": meta["quarter"], "stats": stats}
    if match:
        extra["tracker_match"] = {k: match[k] for k in
                                  ("tracker_companies", "matched", "match_pct")}
    return reference_store.write(
        SOURCE, {TABLE: body}, rows=len(table), as_of=meta["as_of"], licence=LICENCE,
        attribution=ATTRIBUTION, source_url=PAGE, store=store_dir, extra=extra)


def stored_employer_keys(store_dir=reference_store.STORE) -> set[str]:
    path = reference_store.source_dir(SOURCE, store_dir) / TABLE
    if not path.is_file():
        return set()
    with gzip.open(path, "rt") as f:
        return {r["employer_key"] for r in csv.DictReader(f)}


def compact(table, stats, meta, match) -> dict:
    return {"source": SOURCE, "as_of": meta["as_of"], "file": meta["file"],
            "licence": LICENCE, "attribution": ATTRIBUTION, "source_url": PAGE,
            "stats": stats, "tracker_match": {k: match[k] for k in
                                              ("tracker_companies", "matched", "match_pct")},
            "top_employers": top_employers(table)}


# --- network -------------------------------------------------------------------

def _session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch_page() -> str:
    r = _session().get(PAGE, timeout=60)
    r.raise_for_status()
    return r.text


def download(url: str, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / url.rsplit("/", 1)[-1]
    n = 0
    with _session().get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                n += len(chunk)
                if n > MAX_DOWNLOAD:
                    raise SourceError(f"{url} exceeded {MAX_DOWNLOAD} bytes")
                f.write(chunk)
    return out


def _output(name: str, value: str) -> None:
    import os
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


def main(argv=None) -> int:
    try:
        return _main(argv)
    except Exception as exc:  # one readable annotation; Actions logs are not always reachable
        print(f"::error::h1b_lca failed: {type(exc).__name__}: {exc}", flush=True)
        raise


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--discover", action="store_true")
    p.add_argument("--xlsx")
    p.add_argument("--download", metavar="DIR")
    p.add_argument("--store", action="store_true")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--force", action="store_true", help="re-pull even if this file is stored")
    a = p.parse_args(argv)

    if a.xlsx:
        m = re.search(r"FY(\d{4})_Q(\d)", a.xlsx)
        fy, q = (int(m[1]), int(m[2])) if m else (0, 1)
        meta = {"file": Path(a.xlsx).name, "fiscal_year": fy, "quarter": q,
                "as_of": quarter_end(fy, q) if m else datetime.now(timezone.utc).date().isoformat()}
        path = Path(a.xlsx)
    else:
        meta = discover(fetch_page())
        reference_store.notice(f"h1b_lca latest listed: {meta['file']} (as_of {meta['as_of']})")
        _output("file", meta["file"])
        stored = reference_store.read_manifest(SOURCE)
        if stored and stored.get("file") == meta["file"] and not a.force:
            reference_store.touch(SOURCE)
            reference_store.notice(f"h1b_lca {meta['file']} already stored; checked, nothing to pull")
            _output("changed", "false")
            return 0
        if a.discover:
            return 0
        path = download(meta["url"], Path(a.download or "."))
        _output("path", str(path))

    table, stats = reduce(iter_rows(path))
    import h1b_join
    match = h1b_join.match(h1b_join.tracker_companies(
        reference_store.REPO_ROOT / "data" / "talent_intel.db"),
        {r["employer_key"] for r in table})
    summary = (f"h1b_lca {meta['file']} rows_read={stats['rows_read']} "
               f"h1b_cases={stats['h1b_cases']} employers={stats['employers']} "
               f"table_rows={stats['table_rows']} tracker_match={match['matched']}/"
               f"{match['tracker_companies']} ({match['match_pct']}%)")
    if a.store:
        m = store(table, stats, meta, match)
        summary += f" stored={m['files'][TABLE]['bytes']}B"
        _output("changed", "true")
    if a.publish:
        summary += f" site={reference_store.publish(SOURCE, compact(table, stats, meta, match))}"
    reference_store.notice(summary)
    print(json.dumps({"unmatched_sample": match["unmatched_sample"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

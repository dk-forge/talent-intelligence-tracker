#!/usr/bin/env python3
"""TEST-ONLY probe of free official depth/demographics sources.

    python depth_source_probe.py --source qwi|bls|eu|fred|indeed|oflc|events
    python depth_source_probe.py --source qwi --out out/

NOT PRODUCTION. Nothing here is imported by any collector, page or ingest job.
It answers one question per source -- reachable? licence? freshness?
demographic fields? coverage? -- and a MATCH TEST: of the tracker's own public
events from the last ~90 days, what share could be linked to the source at
industry (NAICS sector) + region (state/country) level, and company level where
the source carries employer names (only OFLC LCA does).

Every source prints exactly one `::notice` line (GitHub caps notices per step)
whose body is compact JSON, so results are readable through check-run
annotations without log access. A small sample of what was fetched is written
under --out/archive/<source>/ so a snapshot can be kept if the licence allows.

Stdlib only. Network-touching functions are thin; the matching logic is pure and
unit-tested offline.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

UA = "depth-source-probe/0.1 (test-only; contact via github.com/dk-forge)"
SITE = "https://asktherecruiter.com/blog/wp-json"
TODAY = dt.date.today()

# --------------------------------------------------------------------------
# Pure helpers (unit tested)
# --------------------------------------------------------------------------

# NAICS 2-digit sectors, keyword -> sector code. Order matters: first hit wins.
NAICS_KEYWORDS = [
    ("51", ("tech", "software", "internet", "saas", "media", "telecom", "information",
            "publishing", "semiconductor", "ai", "cloud", "gaming", "entertainment")),
    ("52", ("financ", "bank", "insur", "fintech", "crypto", "invest", "payment", "lending")),
    ("62", ("health", "hospital", "pharma", "biotech", "medical", "life science", "care")),
    ("44", ("retail", "e-commerce", "ecommerce", "consumer", "grocery", "apparel")),
    ("31", ("manufactur", "automotive", "auto", "industrial", "hardware", "aerospace",
            "chemical", "electronics", "food", "steel")),
    ("48", ("transport", "logistic", "shipping", "airline", "delivery", "freight", "mobility")),
    ("54", ("consult", "professional", "legal", "advertis", "marketing", "research", "staffing", "hr")),
    ("61", ("education", "edtech", "university", "school")),
    ("21", ("energy", "oil", "gas", "mining", "utilit")),
    ("23", ("construction", "real estate", "proptech")),
    ("92", ("government", "federal", "public sector", "defense", "nonprofit")),
    ("72", ("hospitality", "restaurant", "travel", "hotel")),
]

US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
                "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
EU_EEA = set("AT BE BG HR CY CZ DK EE FI FR DE GR EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
             "IS LI NO CH".split())
OECD = set("AU AT BE CA CL CO CR CZ DK EE FI FR DE GR HU IS IE IL IT JP KR LV LT LU MX NL NZ NO PL "
           "PT SK SI ES SE CH TR GB US".split())
COUNTRY_NAMES = {"united states": "US", "usa": "US", "us": "US", "united kingdom": "GB", "uk": "GB",
                 "germany": "DE", "france": "FR", "india": "IN", "canada": "CA", "netherlands": "NL",
                 "ireland": "IE", "spain": "ES", "italy": "IT", "sweden": "SE", "japan": "JP",
                 "australia": "AU", "singapore": "SG", "israel": "IL", "china": "CN", "brazil": "BR",
                 "poland": "PL", "switzerland": "CH", "denmark": "DK", "norway": "NO", "finland": "FI"}


def naics_sector(industry: str | None) -> str | None:
    s = (industry or "").strip().lower()
    if not s:
        return None
    if re.fullmatch(r"\d{2,6}", s):
        return s[:2]
    for code, words in NAICS_KEYWORDS:
        for w in words:
            if re.search(r"\b" + re.escape(w), s):
                return code
    return None


def country_code(v: str | None) -> str | None:
    s = (v or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z]{2}", s):
        return s.upper().replace("UK", "GB")
    return COUNTRY_NAMES.get(s.lower())


def norm_company(name: str | None) -> str:
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|plc|gmbh|lp|llp|the|company|holdings?)\b", " ", s)
    return " ".join(s.split())


def event_fields(ev: dict) -> dict:
    """Pull the linkable keys out of a tracker row whatever its exact shape."""
    def first(*keys):
        for k in keys:
            v = ev.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
        return None
    return {
        "industry": first("industry", "sector", "naics"),
        "country": country_code(first("country", "employer_country", "country_code", "hq_country")),
        "state": (first("state", "us_state", "region") or "").upper() or None,
        "company": first("company", "employer", "company_name", "name"),
    }


def match_rate(events: list[dict], region_ok, need_industry: bool = True,
               companies: set[str] | None = None) -> dict:
    """Share of events linkable to a source: industry sector + region, company."""
    n = len(events)
    ind = reg = both = comp = 0
    for ev in events:
        f = event_fields(ev)
        i = naics_sector(f["industry"]) is not None
        r = bool(region_ok(f))
        ind += i
        reg += r
        both += (i or not need_industry) and r
        if companies is not None and norm_company(f["company"]) in companies:
            comp += 1
    pct = lambda x: round(100.0 * x / n, 1) if n else None
    out = {"events": n, "industry_pct": pct(ind), "region_pct": pct(reg), "industry_and_region_pct": pct(both)}
    if companies is not None:
        out["company_pct"] = pct(comp)
    return out


# region predicates per source
def us_state_ok(f):
    return f["country"] in (None, "US") and f["state"] in US_STATES


def us_ok(f):
    return f["country"] == "US" or f["state"] in US_STATES


def staleness_days(latest: str | None) -> int | None:
    """'2026-08', '2026Q2', '2026-08-15', '2026-M08' -> days since period END."""
    if not latest:
        return None
    s = latest.strip()
    m = re.match(r"^(\d{4})-?Q([1-4])$", s)
    if m:
        y, q = int(m[1]), int(m[2])
        end = dt.date(y + (q == 4), (q * 3) % 12 + 1, 1) - dt.timedelta(days=1)
        return (TODAY - end).days
    m = re.match(r"^(\d{4})-M?(\d{2})(?:-(\d{2}))?", s)
    if m:
        y, mo = int(m[1]), int(m[2])
        if m[3]:
            return (TODAY - dt.date(y, mo, int(m[3]))).days
        end = dt.date(y + (mo == 12), mo % 12 + 1, 1) - dt.timedelta(days=1)
        return (TODAY - end).days
    m = re.match(r"^(\d{4})$", s)
    if m:
        return (TODAY - dt.date(int(m[1]), 12, 31)).days
    return None


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def get(url: str, data: bytes | None = None, headers: dict | None = None, timeout=60, limit=None) -> tuple[int, bytes]:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (r.read(limit) if limit else r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500]
    except Exception as e:  # noqa: BLE001 -- a probe reports, it does not crash
        return 0, repr(e).encode()[:300]


def head_size(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            v = r.headers.get("Content-Length")
            return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def save(out: str, source: str, name: str, body: bytes) -> None:
    d = os.path.join(out, "archive", source)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "wb") as f:
        f.write(body)


def load_events(tracker: str, days: int = 90) -> list[dict]:
    since = (TODAY - dt.timedelta(days=days)).isoformat()
    if tracker == "alt":
        base, params, cap = f"{SITE}/layoffs/v1/query", {"from": since, "per_page": 200}, 200
    else:
        base, params, cap = f"{SITE}/talent/v1/query", {"since": since, "per_page": 200}, 200
    rows: list[dict] = []
    for page in range(1, 40):
        params["page"] = page
        st, body = get(base + "?" + urllib.parse.urlencode(params), timeout=60)
        if st != 200:
            break
        try:
            js = json.loads(body)
        except ValueError:
            break
        items = js if isinstance(js, list) else next(
            (js[k] for k in ("items", "rows", "data", "results", "events", "signals") if isinstance(js.get(k), list)), [])
        rows.extend(i for i in items if isinstance(i, dict))
        if len(items) < cap:
            break
    return rows


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def probe_qwi(out, events):
    r = {"source": "census_qwi", "licence": "US Gov public domain (cite Census LEHD)", "frequency": "quarterly"}
    found = None
    for y in range(TODAY.year, TODAY.year - 3, -1):
        for q in (4, 3, 2, 1):
            u = ("https://api.census.gov/data/timeseries/qwi/sa?get=HirA,Sep,Emp,sex,agegrp"
                 f"&for=state:06&industry=51&year={y}&quarter={q}")
            st, body = get(u)
            if st == 200 and body.startswith(b"["):
                found = (y, q, body)
                break
            r.setdefault("http_tries", []).append(st)
        if found:
            break
    r["reachable"] = bool(found)
    if found:
        y, q, body = found
        save(out, "census_qwi", f"qwi_sa_CA_naics51_{y}Q{q}.json", body)
        r["latest"] = f"{y}Q{q}"
        r["stale_days"] = staleness_days(r["latest"])
        r["rows_sample"] = len(json.loads(body)) - 1
        for ep in ("se", "rh"):
            st, _ = get(f"https://api.census.gov/data/timeseries/qwi/{ep}?get=HirA&for=state:06&year={y}&quarter={q}")
            r[f"endpoint_{ep}"] = st
    r["demographics"] = "sex, age group (sa); sex x education (se); race x ethnicity (rh); firm age/size"
    r["geo"] = "US national/state/county/metro/WIB; NAICS 2-4 digit"
    r["match"] = match_rate(events, us_state_ok)
    r["full_pull_estimate"] = "state x NAICS3 x demog, all years ~1-3 GB via LEHD bulk CSV; ~5-20 MB/quarter"
    return r


def probe_bls(out, events):
    r = {"source": "bls_jolts_cps", "licence": "US Gov public domain (cite BLS)", "frequency": "monthly"}
    series = ["JTS000000000000000LDL", "JTS510000000000000LDL", "JTS540099000000000LDL",
              "LNS14000000", "LNS14000001", "LNS14000002", "LNS14000003", "LNS14000006", "LNS14027662"]
    st, body = get("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                   data=json.dumps({"seriesid": series}).encode(),
                   headers={"Content-Type": "application/json"})
    r["http"] = st
    ok = False
    try:
        js = json.loads(body)
        r["api_status"] = js.get("status")
        r["api_msg"] = (js.get("message") or [])[:2]
        latest = {}
        for s in js.get("Results", {}).get("series", []):
            d = s.get("data") or []
            if d:
                latest[s["seriesID"]] = f'{d[0]["year"]}-{d[0]["period"].lstrip("M")}'
        ok = bool(latest)
        r["latest"] = latest
        if latest:
            r["stale_days"] = min(staleness_days(v) or 9999 for v in latest.values())
        save(out, "bls_jolts_cps", "bls_v1_sample.json", body)
    except ValueError:
        pass
    r["reachable"] = ok
    r["demographics"] = "JOLTS: none (industry/size/region only). CPS: sex, age, race, ethnicity, education"
    r["geo"] = "JOLTS: US + 4 regions + 50 states (totals) + NAICS supersector; CPS national (LAUS for states)"
    r["match"] = match_rate(events, us_ok)
    r["full_pull_estimate"] = "JOLTS+CPS flat files (download.bls.gov/pub/time.series/jt, ln) ~150 MB; <1 MB/month"
    r["note"] = "v1 keyless: 25 req/day, 10 yrs, 25 series per call; v2 key free"
    return r


def probe_eu(out, events):
    r = {"source": "eurostat_ons_oecd", "frequency": "monthly/quarterly"}
    res = {}
    # Eurostat unemployment by sex & age (JSON-stat)
    u = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m"
         "?geo=DE&geo=FR&sex=F&sex=M&age=Y_LT25&age=TOTAL&unit=PC_ACT&s_adj=SA&lastTimePeriod=3")
    st, body = get(u)
    e = {"http": st}
    try:
        js = json.loads(body)
        tp = list(js["dimension"]["time"]["category"]["index"].keys())
        e["latest"] = max(tp)
        e["stale_days"] = staleness_days(e["latest"])
        save(out, "eurostat", "une_rt_m_sample.json", body)
    except Exception:  # noqa: BLE001
        pass
    # Eurostat employment flows by NACE? lfsi_emp_q is sex/age; check jvs (vacancy) too
    st2, b2 = get("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/lfsq_egan2"
                  "?geo=DE&sex=T&age=Y15-64&nace_r2=J&unit=THS_PER&lastTimePeriod=1")
    e["lfs_by_nace_http"] = st2
    e["licence"] = "CC BY 4.0 (Eurostat reuse policy)"
    res["eurostat"] = e
    # ONS redundancies level BEAO (thousands, LFS)
    st, body = get("https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/redundancies/timeseries/beao/lms/data")
    o = {"http": st}
    try:
        js = json.loads(body)
        months = js.get("months") or []
        if months:
            last = months[-1]
            o["latest"] = last.get("date")
            mm = re.match(r"(\d{4}) (\w{3})", last.get("date", ""))
            if mm:
                mo = dt.datetime.strptime(mm[2], "%b").month
                o["stale_days"] = staleness_days(f"{mm[1]}-{mo:02d}")
            o["latest_value_thousands"] = last.get("value")
        save(out, "ons", "beao_redundancies.json", body)
    except Exception:  # noqa: BLE001
        pass
    st3, _ = get("https://api.beta.ons.gov.uk/v1/datasets?limit=1")
    o["dataset_api_http"] = st3
    o["licence"] = "Open Government Licence v3 (attribution)"
    res["ons"] = o
    # OECD SDMX: monthly unemployment rate by sex
    u = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0/"
         "USA+DEU+GBR+JPN..._Z.Y._T.Y_GE15..M?lastNObservations=1&format=csvfilewithlabels")
    st, body = get(u, timeout=90)
    oc = {"http": st}
    if st == 200:
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8", "replace"))))
        per = [x.get("TIME_PERIOD") for x in rows if x.get("TIME_PERIOD")]
        if per:
            oc["latest"] = max(per)
            oc["stale_days"] = staleness_days(oc["latest"])
        oc["rows"] = len(rows)
        save(out, "oecd", "ialfs_une_m_sample.csv", body)
    else:
        oc["err"] = body[:160].decode("utf-8", "replace")
    oc["licence"] = "CC BY 4.0 (OECD, since 2024)"
    res["oecd"] = oc
    r["parts"] = res
    r["reachable"] = {k: v.get("http") == 200 for k, v in res.items()}
    r["demographics"] = "Eurostat/OECD: sex, age (+education in LFS); ONS: redundancies by sex/age/industry (LFS)"
    r["geo"] = "Eurostat EU27+EEA (NUTS regions, NACE); ONS UK (+regions); OECD 38 members"
    r["match"] = {
        "eurostat": match_rate(events, lambda f: f["country"] in EU_EEA),
        "ons": match_rate(events, lambda f: f["country"] == "GB"),
        "oecd": match_rate(events, lambda f: f["country"] in OECD),
    }
    r["full_pull_estimate"] = "selected tables: ~10-50 MB total; <1 MB/month"
    return r


def probe_fred(out, events):
    r = {"source": "fred", "licence": "FRED terms: cite; underlying BLS/DOL series public domain, some third-party series copyrighted",
         "frequency": "weekly/monthly", "key_secret": bool(os.environ.get("FRED_API_KEY"))}
    st, body = get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=JTSLDL,ICSA,UNRATE,LNS14000002")
    r["http"] = st
    r["reachable"] = st == 200 and b"observation_date" in body[:200] or (st == 200 and b"DATE" in body[:200])
    if st == 200:
        rows = list(csv.reader(io.StringIO(body.decode())))
        hdr = rows[0]
        latest = {}
        for i, col in enumerate(hdr[1:], 1):
            for row in reversed(rows[1:]):
                if i < len(row) and row[i] not in ("", "."):
                    latest[col] = row[0]
                    break
        r["latest"] = latest
        r["stale_days"] = min((staleness_days(v) or 9999) for v in latest.values()) if latest else None
        save(out, "fred", "fredgraph_sample.csv", body)
    r["demographics"] = "only via mirrored CPS series (sex/age/race/education unemployment)"
    r["geo"] = "US national/state/county/MSA mirrors; few international"
    r["match"] = match_rate(events, us_ok)
    r["note"] = "keyless fredgraph CSV works for small pulls; production should use api.stlouisfed.org with a free FRED_API_KEY"
    r["full_pull_estimate"] = "per-series CSVs, <5 MB for ~200 series; negligible growth"
    return r


def probe_indeed(out, events):
    r = {"source": "indeed_hiring_lab", "licence": "CC BY 4.0 (attribution)", "frequency": "weekly (daily rows)"}
    st, body = get("https://api.github.com/repos/hiring-lab/job_postings_tracker/contents",
                   headers={"Accept": "application/vnd.github+json"})
    countries = []
    if st == 200:
        countries = [x["name"] for x in json.loads(body) if x.get("type") == "dir" and re.fullmatch(r"[A-Z]{2}", x["name"])]
    r["countries"] = countries
    st2, b2 = get("https://api.github.com/repos/hiring-lab/job_postings_tracker/contents/US",
                  headers={"Accept": "application/vnd.github+json"})
    files = [x["name"] for x in json.loads(b2)] if st2 == 200 else []
    r["us_files"] = files
    r["repo_size_kb"] = None
    st4, b4 = get("https://api.github.com/repos/hiring-lab/job_postings_tracker")
    if st4 == 200:
        r["repo_size_kb"] = json.loads(b4).get("size")
    sector = next((f for f in files if "sector" in f.lower()), None)
    if sector:
        st3, b3 = get(f"https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master/US/{sector}")
        if st3 == 200:
            rows = list(csv.DictReader(io.StringIO(b3.decode())))
            dates = [x.get("date") for x in rows if x.get("date")]
            r["latest"] = max(dates) if dates else None
            r["stale_days"] = staleness_days(r["latest"])
            r["occupational_categories"] = len({x.get("display_name") for x in rows})
            save(out, "indeed_hiring_lab", sector, b3)
    r["reachable"] = bool(countries)
    r["demographics"] = "none (postings by occupation category, country, some metros/states for US)"
    r["geo"] = f"{len(countries)} countries"
    cs = set(countries) | ({"GB"} if "UK" in countries else set())
    r["match"] = match_rate(events, lambda f: f["country"] in cs, need_industry=False)
    r["full_pull_estimate"] = "whole repo ~= repo_size_kb; grows a few MB/month"
    return r


def probe_oflc(out, events, budget_s=600):
    r = {"source": "dol_oflc_lca", "licence": "US Gov public domain (cite DOL OFLC)", "frequency": "quarterly (cumulative FY files)"}
    st, body = get("https://www.dol.gov/agencies/eta/foreign-labor/performance")
    r["page_http"] = st
    links = sorted(set(re.findall(rb'href="([^"]*LCA_Disclosure_Data_FY\d{4}_Q\d[^"]*\.xlsx)"', body)))
    links = [l.decode() for l in links]
    def key(l):
        m = re.search(r"FY(\d{4})_Q(\d)", l)
        return (int(m[1]), int(m[2])) if m else (0, 0)
    links.sort(key=key)
    r["files_listed"] = len(links)
    if not links:
        r["reachable"] = False
        r["err"] = body[:120].decode("utf-8", "replace")
        return r
    url = urllib.parse.urljoin("https://www.dol.gov", links[-1])
    fy, q = key(links[-1])
    r["latest_file"] = url.rsplit("/", 1)[-1]
    r["latest"] = f"{fy - (q <= 1)}Q{(q + 2) % 4 + 1}"  # FY Q1 = Oct-Dec of prior CY
    r["stale_days"] = staleness_days(r["latest"])
    r["file_bytes"] = head_size(url)
    t0 = time.time()
    st, data = get(url, timeout=300)
    r["download_http"] = st
    r["download_s"] = round(time.time() - t0)
    r["reachable"] = st == 200
    employers: set[str] = set()
    if st == 200:
        employers, info = xlsx_column(data, "EMPLOYER_NAME", budget_s=budget_s)
        r.update(info)
        sample = "\n".join(sorted(employers)[:200]).encode()
        save(out, "dol_oflc_lca", "employer_names_sample.txt", sample)
    r["demographics"] = "none of workers; job title, SOC occupation, NAICS, wage, worksite state/county, employer"
    r["geo"] = "US worksite state/county/city"
    r["match"] = match_rate(events, us_ok, companies=employers or None)
    r["full_pull_estimate"] = "~100-250 MB xlsx per FY (cumulative); ~1 GB for 5 FYs"
    return r


def xlsx_column(data: bytes, col: str, budget_s=600):
    """Stream one column out of a big xlsx with stdlib only (shared strings)."""
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    t0 = time.time()
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        with z.open("xl/sharedStrings.xml") as f:
            for _, el in ET.iterparse(f):
                if el.tag == ns + "si":
                    strings.append("".join(t.text or "" for t in el.iter(ns + "t")))
                    el.clear()
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    letter = None
    names: set[str] = set()
    rows = 0
    timed_out = False
    with z.open(sheet) as f:
        for _, el in ET.iterparse(f):
            if el.tag != ns + "row":
                continue
            rows += 1
            for c in el.iter(ns + "c"):
                ref = re.match(r"[A-Z]+", c.get("r", ""))[0]
                v = c.find(ns + "v")
                txt = None
                if v is not None:
                    txt = strings[int(v.text)] if c.get("t") == "s" else v.text
                elif c.get("t") == "inlineStr":
                    txt = "".join(t.text or "" for t in c.iter(ns + "t"))
                if rows == 1 and txt == col:
                    letter = ref
                elif letter and ref == letter and txt:
                    names.add(norm_company(txt))
            el.clear()
            if time.time() - t0 > budget_s:
                timed_out = True
                break
    return names, {"rows_parsed": rows, "distinct_employers": len(names), "parse_timed_out": timed_out}


PROBES = {"qwi": probe_qwi, "bls": probe_bls, "eu": probe_eu, "fred": probe_fred,
          "indeed": probe_indeed, "oflc": probe_oflc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=sorted(PROBES) + ["events"])
    ap.add_argument("--tracker", default="alt", choices=["alt", "tit"])
    ap.add_argument("--out", default="probe-out")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    events = load_events(a.tracker)
    if a.source == "events":
        keys = sorted({k for e in events[:50] for k in e})
        res = {"tracker": a.tracker, "events_90d": len(events), "keys": keys[:40],
               "sample_fields": [event_fields(e) | {"company": None} for e in events[:3]]}
    else:
        try:
            res = PROBES[a.source](a.out, events)
        except Exception as e:  # noqa: BLE001
            res = {"source": a.source, "reachable": False, "crash": repr(e)[:300]}
        res["tracker"] = a.tracker
    arch = os.path.join(a.out, "archive")
    if os.path.isdir(arch):
        res["snapshot_bytes"] = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(arch) for f in fs)
    with open(os.path.join(a.out, f"result_{a.source}_{a.tracker}.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    msg = json.dumps(res, separators=(",", ":"), default=str).replace("%", "%25").replace("\n", " ")
    print(f"::notice title=probe {a.source}/{a.tracker}::{msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

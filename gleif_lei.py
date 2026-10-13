#!/usr/bin/env python3
"""Attach GLEIF Legal Entity Identifiers to employers. Offline-safe, free, idempotent.

    python gleif_lei.py                     # dry run: download, match, print counts
    python gleif_lei.py --write             # also rewrite the plugin's company_lei.json
    python gleif_lei.py --golden gc.zip     # use a local golden-copy zip (no download)

SOURCE. The GLEIF golden copy (LEI-CDF 3.1, CSV), published three times a day
at goldencopy.gleif.org and released under CC0 1.0 (licence confirmed on
gleif.org 2026-10-13). No key, no account, no paid API, no model.

THE MATCH RULE lives in pipeline/lei.py: unique normalised name AND the
employer's one known country equal to the LEI's legal-address country.
Ambiguous gets nothing.

WHERE IT GOES. Not into the database. The LEI is a fact about an employer, not
an event, so it does not belong on a `signals` row (which would need a revision
per row) and the plugin renders company pages from the published rows alone.
It is written to `wordpress-plugin/talent-intelligence-tracker/data/company_lei.json`,
the same committed-data-file path sources.json takes to the page. That keeps
this job OUT of the talent-collect writer lock: it opens the database
read-only (schema.connect_ro) and commits one generated file.

Only ACTIVE entities with a live registration (ISSUED, LAPSED, PENDING_*) are
considered; a retired, merged, annulled or duplicate LEI is never attached.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline import lei, provider_names

LATEST_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
OUT = (Path(__file__).parent / "wordpress-plugin" / "talent-intelligence-tracker"
       / "data" / "company_lei.json")
UA = "talent-intelligence-tracker gleif_lei (+https://github.com/dk-forge/talent-intelligence-tracker)"

LIVE_REGISTRATION = {"ISSUED", "LAPSED", "PENDING_TRANSFER", "PENDING_ARCHIVAL"}
_NAME_COLUMNS = (["Entity.LegalName"]
                 + [f"Entity.OtherEntityNames.OtherEntityName.{i}" for i in range(1, 6)]
                 + [f"Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.{i}"
                    for i in range(1, 6)])


def load_companies(conn) -> dict[str, tuple[set[str], set[str]]]:
    """company_key -> (names we hold for it, its countries).

    Country is the headquarters country where any current row states one, else
    the event country. More than one country means no LEI (pipeline/lei.py).
    """
    rows = conn.execute(
        "SELECT company_key, company, country, hq_country FROM signals"
        " WHERE is_current = 1 AND company_key IS NOT NULL AND company_key <> ''")
    names: dict[str, set[str]] = {}
    hq: dict[str, set[str]] = {}
    event: dict[str, set[str]] = {}
    for key, company, country, hq_country in rows:
        names.setdefault(key, {key}).update([company] if company else [])
        if hq_country:
            hq.setdefault(key, set()).add(hq_country.upper())
        if country:
            event.setdefault(key, set()).add(country.upper())
    return {k: (n, hq.get(k) or event.get(k) or set()) for k, n in names.items()}


def iter_golden_copy(zip_path):
    """Yield (lei, [names], country) for every live, ACTIVE record."""
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            for r in reader:
                if r.get("Entity.EntityStatus") != "ACTIVE":
                    continue
                if r.get("Registration.RegistrationStatus") not in LIVE_REGISTRATION:
                    continue
                names = [r[c] for c in _NAME_COLUMNS if r.get(c)]
                yield r["LEI"], names, r.get("Entity.LegalAddress.Country") or ""


def match_all(companies, records) -> dict[str, str]:
    wanted = {(lei.normalise_name(n), c)
              for names, countries in companies.values() if len(countries) == 1
              for n in names for c in countries}
    index = lei.LeiIndex(wanted)
    for code, names, country in records:
        for name in names:
            index.add(name, country, code)
    out = {}
    for key, (names, countries) in companies.items():
        code = lei.match_company(index, names, countries)
        if code:
            out[key] = code
    return out


def write_output(path: Path, matches: dict[str, str], golden_date: str) -> None:
    doc = {
        "source": {
            "name": "GLEIF Golden Copy (LEI-CDF)",
            "url": "https://www.gleif.org/en/lei-data/gleif-golden-copy",
            "licence": "CC0 1.0",
            "golden_copy": golden_date,
            "rule": "unique normalised name and matching legal-address country",
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        # Never publish a banned data-provider name, even as an employer's
        # own name (tests/test_no_provider_names.py, 2026-10-13).
        "companies": dict(sorted((k, v) for k, v in matches.items()
                                 if not provider_names.contains(k))),
    }
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def download_latest(dest_dir: Path) -> tuple[Path, str]:
    meta = json.loads(_get(LATEST_API))["data"]
    url = meta["lei2"]["full_file"]["csv"]["url"]
    dest = dest_dir / "golden-copy.csv.zip"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest, meta["publish_date"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rewrite company_lei.json")
    ap.add_argument("--golden", help="local golden-copy CSV zip (skips the download)")
    ap.add_argument("--golden-date", default="local file")
    ap.add_argument("--db", help="database path (default: the committed one)")
    args = ap.parse_args(argv)

    from pipeline import schema
    conn = schema.connect_ro(args.db)
    companies = load_companies(conn)
    conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        if args.golden:
            golden, date = Path(args.golden), args.golden_date
        else:
            golden, date = download_latest(Path(tmp))
        matches = match_all(companies, iter_golden_copy(golden))

    with_country = sum(1 for _, c in companies.values() if len(c) == 1)
    print(f"golden copy: {date}")
    print(f"employers: {len(companies)}  with one country: {with_country}  "
          f"LEI attached: {len(matches)} ({100 * len(matches) / max(len(companies), 1):.1f}%)")
    if args.write:
        write_output(OUT, matches, date)
        print(f"wrote {OUT}")
    else:
        print("dry run: nothing written (pass --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

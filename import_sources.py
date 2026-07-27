#!/usr/bin/env python3
"""Import the owner's source spreadsheets into the committed catalogue.

    python import_sources.py ~/Downloads/talent_intelligence_sources_master_batch_*.xlsx

The batches are cumulative, so importing several is safe: rows are keyed by
name and the last one wins. Output is `data/sources_catalogue.csv`, committed so
the catalogue is reviewable in a diff rather than living in a binary.

**Everything imported is a candidate.** A source becomes "live" only in
source_registry.SOURCES, where a collector actually reads it. A spreadsheet row
is research, not coverage, and the sources page says so.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    raise SystemExit("pip install openpyxl")

OUT = Path(__file__).parent / "data" / "sources_catalogue.csv"

# Only the columns the public page can honestly use. Authority and confidence
# scores are deliberately dropped: they are judgements we have not verified, and
# publishing an unearned score is the same failure as publishing unearned
# coverage.
FIELDS = (
    "name", "url", "rss", "api", "country", "state", "city",
    "coverage", "category", "industry", "source_type", "signals",
    "language", "free", "notes",
)

COLUMN_MAP = {
    "name": "Source Name",
    "url": "Official Website",
    "rss": "RSS Feed",
    "api": "API",
    "country": "Country",
    "state": "State / Province",
    "city": "City",
    "coverage": "Geographic Coverage",
    "category": "Category",
    "industry": "Industry",
    "source_type": "Source Type",
    "signals": "Signal Types",
    "language": "Language",
    "free": "Free/Paid",
    "notes": "Notes",
}


def read(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        ws = wb["Sources"]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return []
    header = [str(h).strip() if h else "" for h in rows[0]]

    out = []
    for raw in rows[1:]:
        record = dict(zip(header, raw))
        name = (record.get("Source Name") or "").strip()
        url = (record.get("Official Website") or "").strip()
        # A source without a name or a real URL cannot be published or checked.
        if not name or not url.startswith("http"):
            continue
        out.append({k: str(record.get(col) or "").strip()
                    for k, col in COLUMN_MAP.items()})
    return out


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]]
    if not paths:
        raise SystemExit(__doc__)

    merged: dict[str, dict] = {}
    for p in paths:
        rows = read(p)
        for r in rows:
            merged[r["name"].lower()] = r
        print(f"{p.name}: {len(rows)} usable rows")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for name in sorted(merged):
            writer.writerow(merged[name])

    countries = {r["country"] for r in merged.values() if r["country"]}
    categories = {r["category"] for r in merged.values() if r["category"]}
    print(f"\n{len(merged)} unique sources -> {OUT}")
    print(f"  {len(countries)} countries, {len(categories)} categories")
    print("  all imported as CANDIDATES; live status lives in source_registry.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

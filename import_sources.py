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
import re
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


# ---------------------------------------------------------------------------
# COMPETITOR DENYLIST (hashed).
#
# Standing rule: competitor names never appear in this repo, in CI logs, or on
# public pages. This repo is PUBLIC, so a vendor row committed to
# data/sources_catalogue.csv publishes it to anyone -- and so would a denylist
# written in plain text. The entries below are therefore SHA-256 prefixes of the
# normalised name and of the URL host, so the guard works without the repo ever
# containing what it blocks.
#
# Two vendor rows arrived via an .xlsx batch and were committed before anyone
# noticed (removed 2026-07-28). Matching covers the name and the host, so a
# re-export under a slightly different label or a subdomain is still caught.
#
# FAIL LOUD, never silently drop: a silent filter hides that the INPUT still
# contains them, so the next person re-adds them by hand. The import aborts and
# prints the row index, not the name.
#
# To add an entry:  python3 -c "import hashlib;print(hashlib.sha256(b'name').hexdigest()[:16])"
COMPETITOR_DENY_NAME_HASHES = frozenset({
    "0616a267820d2c78",
    "567877b2e3d7aac3",
})
COMPETITOR_DENY_HOST_HASHES = frozenset({
    "67b0747067cdad0f",
    "8bb09747e66dc6c7",
})


def _hash_key(value: str) -> str:
    import hashlib as _hl
    return _hl.sha256(str(value or "").strip().lower().encode()).hexdigest()[:16]


def assert_no_competitors(rows: list[dict]) -> None:
    """Abort the import if a denylisted vendor is present. Never writes.

    Reports the row INDEX rather than the name so the offending vendor is not
    echoed into a public CI log."""
    offenders = []
    for i, row in enumerate(rows, 1):
        name = re.sub(r"[^a-z0-9 ]+", "", str(row.get("name", "")).lower()).strip()
        # A vendor re-exported as "<Name> Inc." / "<Name> Ltd" must still match,
        # so test the full name, the name minus legal suffixes, and the leading
        # token. Hashing means we cannot substring-match, hence explicit variants.
        bare = re.sub(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited"
                      r"|llc|plc|group|holdings|technologies|labs|io)\b", " ", name)
        bare = re.sub(r"\s+", " ", bare).strip()
        candidates = {name, bare, name.split(" ")[0] if name else "", bare.split(" ")[0] if bare else ""}
        host = str(row.get("url", "") or "").lower().split("//")[-1].split("/")[0]
        host = host[4:] if host.startswith("www.") else host
        if any(_hash_key(c) in COMPETITOR_DENY_NAME_HASHES for c in candidates if c) \
           or _hash_key(host) in COMPETITOR_DENY_HOST_HASHES:
            offenders.append(str(i))
    if offenders:
        raise SystemExit(
            "REFUSING TO WRITE: denylisted competitor row(s) present in the input at "
            f"row(s) {', '.join(offenders)}.\nThis repo is public and competitor names "
            "must never be committed. Remove them from the source spreadsheet and re-run."
        )


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

    # Standing rule check BEFORE any write: a competitor row must never reach
    # the committed CSV in this public repo.
    assert_no_competitors(list(merged.values()))

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

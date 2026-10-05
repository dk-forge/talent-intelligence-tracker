#!/usr/bin/env python3
"""Join tracker companies to H-1B (DOL OFLC LCA) employers by normalised name.

    python3 h1b_join.py            # match rate of current US tracker companies

One normaliser, used on both sides, so the join is symmetric: lower-case, drop
a "d/b/a ..." clause, '&' -> 'and', punctuation to spaces, drop legal-form and
holding suffixes and a leading "the". A name that is ALL suffix ("The Company,
Inc.") keeps its un-stripped form, because an empty key would match every
other empty key.

Exact-key match only, on purpose: a fuzzy join would put a stranger's visa
filings on a tracker company's page. The match rate is reported, not hidden;
a low rate is the honest number for an exact join.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

SUFFIXES = (
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "gmbh", "ag", "sa", "lp", "llp", "pc", "pllc", "pa",
    "holdings", "holding", "group", "the",
)
_SUFFIX_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in SUFFIXES) + r")\b")
_DBA_RE = re.compile(r"\b(d\s*/?\s*b\s*/?\s*a|doing business as)\b.*$")


def normalise_employer(name: str | None) -> str:
    s = (name or "").lower().strip()
    if not s:
        return ""
    s = _DBA_RE.sub(" ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", s)        # l.l.c. -> llc.
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    base = " ".join(s.split())
    stripped = " ".join(_SUFFIX_RE.sub(" ", base).split())
    return stripped or base


def match(tracker_names, employer_keys: set[str], sample: int = 25) -> dict:
    keys = {}
    spellings: dict[str, list[str]] = {}
    for name in tracker_names:
        k = normalise_employer(name)
        if k:
            keys.setdefault(k, name)
            spellings.setdefault(k, []).append(name)
    pairs = {k: k for k in keys if k in employer_keys}
    total = len(keys)
    return {
        "tracker_companies": total,
        "matched": len(pairs),
        "match_pct": round(100 * len(pairs) / total, 1) if total else 0.0,
        "pairs": pairs,
        # Every tracker spelling of a matched employer -> its key, so the site
        # can look a company page up by its EXACT stored name and never needs a
        # second copy of the normaliser.
        "names": {n: k for k in pairs for n in spellings[k]},
        "unmatched_sample": sorted(k for k in keys if k not in pairs)[:sample],
    }


def tracker_companies(db_path) -> list[str]:
    """Distinct current US company names, read-only (never writes the DB)."""
    uri = f"file:{Path(db_path)}?mode=ro"
    with sqlite3.connect(uri, uri=True) as c:
        rows = c.execute(
            "SELECT DISTINCT company FROM signals WHERE country = 'US' "
            "AND is_current = 1 AND company IS NOT NULL AND company != '' "
            "ORDER BY company").fetchall()
    return [r[0] for r in rows]


def main(argv=None) -> int:
    import h1b_lca
    keys = h1b_lca.stored_employer_keys()
    r = match(tracker_companies(Path(__file__).resolve().parent / "data" / "talent_intel.db"), keys)
    print(f"::notice::h1b join: {r['matched']}/{r['tracker_companies']} tracker US "
          f"companies matched ({r['match_pct']}%) against {len(keys)} H-1B employers")
    return 0


if __name__ == "__main__":
    sys.exit(main())

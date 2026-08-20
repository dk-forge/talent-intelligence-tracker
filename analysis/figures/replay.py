#!/usr/bin/env python3
"""What the figure guard's glue bug cost, replayed over what we actually hold.

    python3 -m analysis.figures.replay
    python3 -m analysis.figures.replay --examples 20 --json out.json

READ-ONLY, always: the connection is opened `mode=ro` and there is no write path
in this file. Measurement, not collection — no network call, no model, no cent.

Every number in the comment above `validate._NUMBER` comes from here. If you
change that comment, change it because this said so.

WHAT THIS CANNOT MEASURE, and read it before quoting any number below.

**`raw_text` is not persisted** — the same limitation `measure_city_placement.py`
documents. The guard compares the model's summary against the collector's
`raw_text`; what survives in `signals` is `headline`, `summary` and
`talent_readthrough`. So the exact comparison the pipeline made cannot be
re-made, and in particular:

  * **A rejected candidate leaves no text at all.** `seen_urls` records the URL
    and `outcome='rejected'`, and nothing else — no reason, no figures, no
    summary. None of those rejections can be attributed to a rule, by this
    script or any other. Anyone who tells you what fraction of them was this bug
    is extrapolating, and the honest count is printed as zero below.
  * The four measurements that ARE possible are each labelled with what they
    stand for. None of them is "records lost".
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from pipeline import schema, validate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "talent_intel.db"

# --- the guards being compared ----------------------------------------------
#
# A guard is a (pattern, normaliser) pair, because both halves decide what a
# figure is and replaying one with the other's partner credits code with
# behaviour it did not have.

# As it shipped before the fix: `\s*` before the suffix, so a line break glued.
OLD_NUMBER = re.compile(r"\d[\d,.]*\s*(?:bn|b|m|k|billion|million|thousand)?", re.I)

# The variant that looks like the right fix and is not. See the comment on
# validate._NUMBER: the missing boundary is folding magnitudes in 43 languages by
# accident, and this is what taking that away costs.
BOUNDARY_NUMBER = re.compile(
    r"\d[\d,.]*" + rf"(?:{validate._H_SPACE}*(?:bn|b|m|k|billion|million|thousand)\b)?",
    re.I)

_MAGNITUDE = {"bn": "b", "b": "b", "billion": "b", "m": "m", "million": "m",
              "k": "k", "thousand": "k"}
_MAGNITUDE_TAIL = re.compile(r"(bn|billion|million|thousand|[bmk])$", re.I)


def plain_normalize(token: str) -> str:
    """The shipped normaliser, restated here so the replay owns both halves."""
    return re.sub(r"[,\s.]", "", token.lower())


def folded_normalize(token: str) -> str:
    """plain_normalize plus an ENGLISH magnitude fold, for the variant below."""
    text = plain_normalize(token)
    return _MAGNITUDE_TAIL.sub(lambda m: _MAGNITUDE[m.group(0).lower()], text)


OLD = (OLD_NUMBER, plain_normalize)
NEW = (validate._NUMBER, validate._normalize_number)
VARIANTS = {
    # what shipped: the newline can no longer glue, nothing else moves
    "shipped (horizontal space only)": NEW,
    # rejected: a word boundary after the suffix
    "+ word boundary": (BOUNDARY_NUMBER, plain_normalize),
    # rejected: a word boundary plus an English-only magnitude fold
    "+ word boundary + English fold": (BOUNDARY_NUMBER, folded_normalize),
}

# A glue site: a token the old pattern ended with a magnitude the new one does
# not take, because the letters belong to the next word or the next line. Found
# by differencing the two patterns rather than by pattern-matching prose, so it
# cannot disagree with the code under test.
_GLUED_SUFFIX = re.compile(r"(?:bn|b|m|k|billion|million|thousand)$", re.I)

# Where a raw_text's FIRST newline junction can be rebuilt from stored columns.
# Every collector joins `headline\n\nbody`, and for these the body's opening
# characters are a template over a column we still have — so whether the glue
# fired is a fact, not an estimate. The other collectors' bodies are a teaser or
# a filing that was never stored, and their junctions are unknowable.
JUNCTION_BODY_OPENS_WITH = {
    # collectors/sec_execcomp.py: body = f"{company} (CIK {cik}) reported ..."
    "sec_execcomp": lambda r: r["company"] or "",
    # collectors/sec_form_d.py and _bulk: body = f"{company} filed a Form D ..."
    "sec_form_d": lambda r: r["company"] or "",
    "sec_form_d_bulk": lambda r: r["company"] or "",
    # collectors/uk_paygap.py: body = f"{company} reported its gender pay gap ..."
    "uk_paygap": lambda r: r["company"] or "",
}


def tokens(guard, text: str) -> set[str]:
    pattern, normalize = guard
    return {normalize(m.group(0)) for m in pattern.finditer(text or "")}


def invented(guard, claim: str, source: str) -> set[str]:
    """`assert_figures_are_sourced`, returned instead of raised, on one guard."""
    out = tokens(guard, claim) - tokens(guard, source)
    return {n for n in out if not re.fullmatch(r"(19|20)\d\d", n)}


def glue_sites(text: str, reference: re.Pattern) -> list[tuple[str, bool]]:
    """Every place the old pattern glued and `reference` does not.

    `reference` is the pattern being credited with the fix — validate._NUMBER for
    what shipped (newline glue only), BOUNDARY_NUMBER for the in-line defect that
    did not ship. Returns (token, crossed_a_newline).

    Call it on ONE stored field at a time. Joining two fields invents a junction
    the pipeline never had, and that artefact reported 189 hits of "31 B" — a
    sec_execcomp headline ending in a filing date beside a summary starting with
    the company name, two strings never adjacent in any raw_text.
    """
    sites = []
    for match in OLD_NUMBER.finditer(text or ""):
        old = match.group(0)
        if not _GLUED_SUFFIX.search(old):
            continue
        new = reference.match(text, match.start())
        if new and new.group(0) == old:
            continue          # a real magnitude: both patterns agree
        sites.append((old, bool(re.search(r"[\r\n]", old))))
    return sites


def rows(db: Path):
    conn = schema.connect_ro(db)
    conn.row_factory = sqlite3.Row
    try:
        signals = conn.execute(
            "SELECT row_id, collector, company, headline, summary, "
            "       talent_readthrough, headcount, funding_amount, source_url "
            "FROM signals WHERE is_current = 1"
        ).fetchall()
        seen = conn.execute(
            "SELECT collector, outcome, count(*) AS n FROM seen_urls GROUP BY 1, 2"
        ).fetchall()
        return signals, seen
    finally:
        conn.close()


def measure(db: Path) -> dict:
    signals, seen = rows(db)
    result = {
        "db": str(db),
        "current_rows": len(signals),
        "rejections_on_record": sum(
            r["n"] for r in seen if r["outcome"] == "rejected"),
        "rejections_attributable_to_this_rule": 0,
        "inline_glue": {"rows": 0, "sites": 0, "across_newline": 0,
                        "by_field": Counter(), "by_collector": Counter(),
                        "tokens": Counter()},
        "newline_junction": {"rows_checkable": 0, "glue_fired": 0,
                             "by_collector": Counter(), "examples": []},
        "variants": {},
        "field_channel": {"headcount_rows": 0, "headcount_freed": 0,
                          "funding_rows": 0, "funding_freed": 0},
    }
    for name in VARIANTS:
        result["variants"][name] = {
            "old_rejects_variant_accepts": 0,
            "variant_rejects_old_accepts": 0,
            "by_collector": Counter(),
            "examples": [],
            "newly_rejected_examples": [],
        }
    ig, nj = result["inline_glue"], result["newline_junction"]

    for r in signals:
        # --- 1. the in-line glue, in the text we still hold ------------------
        sites = []
        for field in ("headline", "summary", "talent_readthrough"):
            found = glue_sites(r[field] or "", BOUNDARY_NUMBER)
            ig["by_field"][field] += len(found)
            sites.extend(found)
        if sites:
            ig["rows"] += 1
            ig["sites"] += len(sites)
            ig["across_newline"] += sum(1 for _t, nl in sites if nl)
            ig["by_collector"][r["collector"]] += 1
            for token, _nl in sites:
                ig["tokens"][" ".join(token.split())] += 1

        # --- 2. the newline junction, where it can be rebuilt exactly --------
        opener = JUNCTION_BODY_OPENS_WITH.get(r["collector"])
        if opener:
            nj["rows_checkable"] += 1
            junction = f"{r['headline'] or ''}\n\n{opener(r)}"
            if glue_sites(junction, validate._NUMBER):
                nj["glue_fired"] += 1
                nj["by_collector"][r["collector"]] += 1
                if len(nj["examples"]) < 5:
                    nj["examples"].append(junction.replace("\n", "\\n")[:160])

        # --- 3. every candidate guard against the old one ---------------------
        claim, source = r["summary"] or "", r["headline"] or ""
        if not (claim and source):
            continue
        old = invented(OLD, claim, source)
        for name, guard in VARIANTS.items():
            new = invented(guard, claim, source)
            v = result["variants"][name]
            if old and not new:
                v["old_rejects_variant_accepts"] += 1
                v["by_collector"][r["collector"]] += 1
                if len(v["examples"]) < 12:
                    v["examples"].append(
                        {"collector": r["collector"], "old_called_invented": sorted(old),
                         "headline": r["headline"], "summary": (r["summary"] or "")[:200]})
            if new and not old:
                v["variant_rejects_old_accepts"] += 1
                if len(v["newly_rejected_examples"]) < 12:
                    v["newly_rejected_examples"].append(
                        {"collector": r["collector"], "newly_invented": sorted(new),
                         "headline": r["headline"], "summary": (r["summary"] or "")[:200]})

        # --- 4. the quieter channel: a field dropped off a stored record ------
        if r["headcount"] is not None:
            result["field_channel"]["headcount_rows"] += 1
            if (validate._normalize_number(str(r["headcount"])) in tokens(NEW, source)
                    and plain_normalize(str(r["headcount"])) not in tokens(OLD, source)):
                result["field_channel"]["headcount_freed"] += 1
        if r["funding_amount"]:
            result["field_channel"]["funding_rows"] += 1
            d_old, d_new = tokens(OLD, r["funding_amount"]), tokens(NEW, r["funding_amount"])
            old_ok = bool(d_old) and d_old <= tokens(OLD, source)
            new_ok = bool(d_new) and d_new <= tokens(NEW, source)
            if new_ok and not old_ok:
                result["field_channel"]["funding_freed"] += 1
    return result


def _counters_to_dicts(obj):
    if isinstance(obj, Counter):
        return dict(obj)
    if isinstance(obj, dict):
        return {k: _counters_to_dicts(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_counters_to_dicts(v) for v in obj]
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--examples", type=int, default=6)
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the full measurement here")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2
    m = measure(args.db)

    print(f"current rows                {m['current_rows']:>7}")
    print(f"rejected URLs on record     {m['rejections_on_record']:>7}"
          "   (no reason stored for any of them)")
    print(f"attributable to THIS rule   "
          f"{m['rejections_attributable_to_this_rule']:>7}"
          "   <- raw_text is not persisted. This is the honest answer.")
    print()

    nj = m["newline_junction"]
    print("[the reported bug: a magnitude taken from the NEXT LINE]")
    print(f"  rows whose junction can be rebuilt exactly  {nj['rows_checkable']:>7}")
    print(f"  junctions where the glue FIRED              {nj['glue_fired']:>7}")
    if nj["by_collector"]:
        print("  by collector: " + ", ".join(
            f"{c} ({n})" for c, n in nj["by_collector"].most_common()))
    for ex in nj["examples"][:3]:
        print(f"    {ex}")
    print("  A fired junction is not a lost record: the token is only missing if"
          "\n  it appears nowhere else in raw_text, and these bodies repeat the"
          "\n  date. The junctions that cannot be rebuilt — a news teaser, a"
          "\n  filing body — are where the unmeasurable loss lives.")
    print()

    ig = m["inline_glue"]
    print("[the same defect INSIDE one line, commoner, and NOT fixed — measured")
    print(" against the word-boundary variant, the fix that would close it]")
    print(f"  rows with a glue site   {ig['rows']:>7}"
          f"   ({100 * ig['rows'] / max(m['current_rows'], 1):.2f}% of current rows)")
    print(f"  sites                   {ig['sites']:>7}")
    print(f"  of those, cross a line  {ig['across_newline']:>7}"
          "   (necessarily 0: no stored field contains a newline)")
    if ig["by_field"]:
        print("  by field: " + ", ".join(
            f"{c} ({n})" for c, n in ig["by_field"].most_common()))
    if ig["by_collector"]:
        print("  by collector: " + ", ".join(
            f"{c} ({n})" for c, n in ig["by_collector"].most_common()))
    if ig["tokens"]:
        print("  commonest: " + ", ".join(
            f"{t!r} ({n})" for t, n in ig["tokens"].most_common(10)))
    print()

    print("[each candidate guard against the one that shipped, on 'summary' vs")
    print(" 'headline' — the only pair of real texts every stored row still has.")
    print(" headline is the FIRST LINE of raw_text and the rest is gone, so read")
    print(" these as the shape's fingerprint on stored rows, never as a count of")
    print(" records lost.]")
    for name, v in m["variants"].items():
        print(f"\n  {name}")
        print(f"    frees  {v['old_rejects_variant_accepts']:>5} rows the old guard called invented")
        print(f"    breaks {v['variant_rejects_old_accepts']:>5} rows the old guard accepted"
              "   <- non-zero means it narrows the rule")
        if v["by_collector"]:
            print("    freed by collector: " + ", ".join(
                f"{c} ({n})" for c, n in v["by_collector"].most_common()))
        for ex in v["newly_rejected_examples"][: args.examples]:
            print(f"      BREAKS {ex['newly_invented']}: {ex['headline'][:96]}")
    print()

    fc = m["field_channel"]
    print("[the quieter channel: a stated field dropped off a record that stored]")
    print(f"  rows with a headcount      {fc['headcount_rows']:>6}, "
          f"the fix frees {fc['headcount_freed']}")
    print(f"  rows with a funding amount {fc['funding_rows']:>6}, "
          f"the fix frees {fc['funding_freed']}")

    if args.examples:
        shipped = m["variants"]["shipped (horizontal space only)"]["examples"]
        if shipped:
            print(f"\n--- {min(args.examples, len(shipped))} of {len(shipped)} rows the "
                  "shipped fix frees ---")
            for e in shipped[: args.examples]:
                print(f"\n  [{e['collector']}] old called invented: "
                      f"{e['old_called_invented']}")
                print(f"  headline: {e['headline']}")
                print(f"  summary : {e['summary']}")
    if args.json:
        args.json.write_text(json.dumps(_counters_to_dicts(m), indent=1,
                                        sort_keys=True))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

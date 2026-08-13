#!/usr/bin/env python3
"""Whose reads did the budget actually buy, and what would another rule buy?

    python3 -m analysis.ranking.read_share              # both lenses
    python3 -m analysis.ranking.read_share --ledger     # what was bought
    python3 -m analysis.ranking.read_share --model      # what a rule would buy
    python3 -m analysis.ranking.read_share --country GB

MEASUREMENT, NEVER COLLECTION, and never a policy. Nothing here writes a row,
marks a URL seen, calls a model, or touches a cent. It reads two files this repo
already commits and prints tables. `analysis/ranking/measure.py` answers "does
ranking change the MIX"; this answers "what share of the budget does ONE country
get, and what would it get under a different rule" — which is the question the
2026-08-12 US recall gap map raised and could not settle from the source alone.

TWO LENSES, AND THEY MEASURE DIFFERENT THINGS
---------------------------------------------
`--ledger` is what actually happened. `data/gate_labels/labels-*.jsonl` carries
one line per gate decision with the candidate's country hint and its terminal
outcome, so the split of real reads and real deferrals by country is a count and
not a model. Its limits, which decide how to read it:

  * The country is the ledger's own `_country`: `source_country`, else
    `country`, else the Google News edition. That is the SAME hint
    `candidate_rank.candidate_country` buckets on, so the two agree by
    construction — but it is the PUBLISHER's country, never the story's. A US
    round written up in Sao Paulo counts as BR here and in the ranker.
  * `deferred` covers the per-run read cap, a throttled provider and a failed
    read-through together. The run log distinguishes them; the ledger does not.
  * `bootstrap-weak.jsonl` is excluded: it was back-filled from `seen_urls` and
    carries no gate verdict of its own.

`--model` is what a different ordering would have bought, replayed over the
stored news population `measure.py` already builds (real text, real publishers,
real arrival order). Its limit is the one stated there and it is severe: these
are the rows that STORED, so it measures which eventual winners a capped run
reaches, not the yield on candidates it rejected. Read the ORDERING it produces,
which is exact, and not the conversion, which is not measured here at all.

WHY A COUNTRY AND NOT A COLLECTOR
---------------------------------
`classify.read_cap` already splits the budget between COLLECTORS by measured
conversion, and `tests/test_read_budget.py` pins that. Nothing pins, prints or
measures the split between COUNTRIES, which is decided further down by
`candidate_rank.rank` — a score that pays for country need plus a round robin
that gives every country's best story a place before any country's second. Both
are deliberate and documented. Neither has ever been reported as a share.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import candidate_rank, schema  # noqa: E402

LEDGER_DIR = ROOT / "data" / "gate_labels"

#: Ledger outcomes that could only be reached by spending a place under the
#: per-run read cap. The cap counts `STATS["full_calls"]`, incremented inside
#: `classify.classify()` the moment stage 2 is entered — which is BEFORE
#: `run_collect` asks the dedup layers and before the conditional second pass.
#: So a candidate later found `duplicate` still consumed a place, and is
#: counted here as one (reported separately as `dup` too, because it did not
#: buy the second pass). `gate_reject` and `error` never reach stage 2;
#: `deferred` is precisely the candidate the cap pushed past.
READ_OUTCOMES = frozenset({"stored", "model_reject", "validate_reject",
                           "would_store", "retracted", "duplicate"})
GATE_SURVIVED = frozenset({"YES", "CLF_YES", "OFF"})

#: The ledger records a country verbatim, on purpose (see gate_ledger._country),
#: so one country arrives under several spellings from different collectors.
ALIASES = {
    "US": {"US", "USA", "United States", "United States of America"},
    "GB": {"GB", "UK", "United Kingdom"},
    "IN": {"IN", "India"},
    "DE": {"DE", "Germany"},
}


def _names(iso2: str) -> frozenset[str]:
    return frozenset(ALIASES.get(iso2.upper(), {iso2.upper()}))


def ledger_lines() -> list[dict]:
    """Every real gate decision, newest shard last. Bootstrap excluded."""
    out: list[dict] = []
    for path in sorted(LEDGER_DIR.glob("labels-*.jsonl")):
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue          # a torn last line is not a finding
    return out


def report_ledger(iso2: str) -> None:
    rows = ledger_lines()
    if not rows:
        print("no ledger shards under data/gate_labels — nothing to report, "
              "which is UNKNOWN and not zero")
        return
    names = _names(iso2)
    days = sorted({r.get("ts", "")[:10] for r in rows if r.get("ts")})
    print(f"\nLEDGER: {len(rows):,} real gate decisions, {days[0]} to {days[-1]}")
    print(f"the country in question is {iso2} (ledger spellings: "
          f"{', '.join(sorted(names))})\n")

    head = (f"{'collector':16}{'cand':>7}{iso2:>6}{'share':>7}"
            f"{'gateOK':>8}{iso2:>6}{'share':>7}"
            f"{'reads':>7}{iso2:>6}{'share':>7}{'dup':>6}"
            f"{'defer':>7}{iso2:>6}{'share':>7}")
    print(head)
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row.get("collector") or "?"].append(row)
    groups["ALL"] = rows

    for collector in sorted(groups, key=lambda c: (c == "ALL", c)):
        sub = groups[collector]
        def split(pred):
            got = [r for r in sub if pred(r)]
            mine = [r for r in got if (r.get("country") or "") in names]
            share = 100.0 * len(mine) / len(got) if got else 0.0
            return len(got), len(mine), share
        c_n, c_m, c_s = split(lambda r: True)
        g_n, g_m, g_s = split(lambda r: r.get("gate") in GATE_SURVIVED)
        r_n, r_m, r_s = split(lambda r: r.get("outcome") in READ_OUTCOMES)
        d_n, _, _ = split(lambda r: r.get("outcome") == "duplicate")
        f_n, f_m, f_s = split(lambda r: r.get("outcome") == "deferred")
        print(f"{collector:16}{c_n:>7}{c_m:>6}{c_s:>6.1f}%"
              f"{g_n:>8}{g_m:>6}{g_s:>6.1f}%"
              f"{r_n:>7}{r_m:>6}{r_s:>6.1f}%{d_n:>6}"
              f"{f_n:>7}{f_m:>6}{f_s:>6.1f}%")

    print("\nthe asymmetry, which is the finding rather than any single share:")
    for collector in sorted(groups):
        sub = groups[collector]
        survived = [r for r in sub if r.get("gate") in GATE_SURVIVED]
        if not survived:
            continue
        mine = [r for r in survived if (r.get("country") or "") in names]
        other = [r for r in survived if (r.get("country") or "") not in names]
        def deferred_share(rows_):
            if not rows_:
                return None
            return 100.0 * sum(1 for r in rows_
                               if r.get("outcome") == "deferred") / len(rows_)
        a, b = deferred_share(mine), deferred_share(other)
        if a is None or b is None:
            continue
        print(f"  {collector:16} of its gate survivors, {a:5.1f}% of {iso2} "
              f"deferred against {b:5.1f}% of everywhere else "
              f"(n={len(mine)} vs {len(other)})")


# --- lens B: what another ordering would have bought ----------------------

def _country_of(item: dict) -> str:
    return candidate_rank.candidate_country(item) or ""


def policy_arrival(items, context, buckets):
    return list(items)


def policy_current(items, context, buckets):
    return candidate_rank.rank(items, context)


def policy_score_only(items, context, buckets):
    """The score with the round robin taken off — the 2026-07-29 first cut."""
    return sorted(items, key=lambda i: -candidate_rank.score(i, context))


def policy_floor(share: float):
    """The current order, with one country guaranteed `share` of every cut.

    A floor and not a quota: it reserves places in the ORDER, so a run where
    that country has nothing to say loses nothing to it.
    """
    def run(items, context, buckets):
        ranked = candidate_rank.rank(items, context)
        mine = [i for i in ranked if _country_of(i) == run.iso2]
        rest = [i for i in ranked if _country_of(i) != run.iso2]
        gap = max(1, int(round(1.0 / share)) - 1)
        out, mi, ri = [], 0, 0
        while mi < len(mine) or ri < len(rest):
            for _ in range(gap):
                if ri < len(rest):
                    out.append(rest[ri]); ri += 1
            if mi < len(mine):
                out.append(mine[mi]); mi += 1
        return out
    run.iso2 = "US"
    return run


def policy_volume(items, context, buckets):
    """Each country's share of the cut equals its share of the candidates.

    The obvious alternative to need weighting, included because it is the one
    most people reach for and its cost is not obvious until it is counted: at a
    cut smaller than the corpus it spends everything on the largest suppliers.
    """
    import heapq
    heap = []
    for key, bucket in buckets.items():
        step = 1.0 / len(bucket)
        heapq.heappush(heap, (step, step, key, 0))
    out = []
    while heap:
        priority, step, key, index = heapq.heappop(heap)
        out.append(buckets[key][index])
        if index + 1 < len(buckets[key]):
            heapq.heappush(heap, (priority + step, step, key, index + 1))
    return out


def report_model(iso2: str, cuts: list[int]) -> None:
    from analysis.ranking.measure import stored_population

    conn = schema.connect()
    try:
        context = candidate_rank.Context.for_conn(conn)
        items = stored_population(conn)
    finally:
        conn.close()
    if not items:
        print("no stored news rows — UNKNOWN, not zero")
        return

    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(_country_of(item), []).append(item)

    held = context.country_rows(iso2)
    best = {key: max(candidate_rank.score(i, context) for i in bucket)
            for key, bucket in buckets.items()}
    order = [key for key, _ in sorted(best.items(), key=lambda kv: -kv[1])]
    print(f"\nMODEL: {len(items):,} stored news candidates, "
          f"{len(buckets)} country buckets "
          f"({len(buckets.get('', []))} carry no country hint at all)")
    print(f"{iso2} holds {held:,} rows, so its country score is "
          f"{'0 (over COUNTRY_THIN_ROWS)' if held and held >= candidate_rank.COUNTRY_THIN_ROWS else 'not zero'};"
          f" its best candidate scores {best.get(iso2, 0.0)}, which places its "
          f"bucket {order.index(iso2) + 1 if iso2 in order else '-'} of "
          f"{len(order)} in the round robin's visiting order")
    print("A cut smaller than the number of buckets never finishes pass one, so "
          "a bucket at position P gets nothing until the cut reaches P.\n")

    floor10 = policy_floor(0.10); floor10.iso2 = iso2
    floor20 = policy_floor(0.20); floor20.iso2 = iso2
    policies = [
        ("current (rank)", policy_current),
        ("arrival", policy_arrival),
        ("score, no robin", policy_score_only),
        (f"floor {iso2} 10%", floor10),
        (f"floor {iso2} 20%", floor20),
        ("volume-weighted", policy_volume),
    ]

    def thin(item):
        rows = context.country_rows(_country_of(item) or None)
        return rows is not None and 0 < rows < candidate_rank.COUNTRY_THIN_ROWS

    print(f"{'policy':18}{'cut':>6}{iso2:>6}{'share':>8}{'countries':>11}"
          f"{'thin-country':>14}")
    for label, policy in policies:
        ordered = policy(items, context, buckets)
        assert sorted(map(id, ordered)) == sorted(map(id, items)), \
            f"{label} must be a permutation"
        for cut in cuts:
            head = ordered[:cut]
            mine = sum(1 for i in head if _country_of(i) == iso2)
            print(f"{label:18}{cut:>6}{mine:>6}{100.0 * mine / cut:>7.1f}%"
                  f"{len({_country_of(i) for i in head}):>11}"
                  f"{sum(1 for i in head if thin(i)):>14}")
        print()
    print("`countries` and `thin-country` are the price. A place given to a "
          "country that already reads well is a place taken from one that does "
          "not, and at these cuts it is taken from a country holding under "
          f"{candidate_rank.COUNTRY_THIN_ROWS} rows.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", action="store_true")
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--country", default="US")
    ap.add_argument("--cuts", default="37,99,118,217,395",
                    help="read cuts to report at. Defaults are the walker's "
                         "DAILY_GATE_RATION, the two live per-collector caps, "
                         "their sum, and the walker's measured full day.")
    args = ap.parse_args(argv)
    if not (args.ledger or args.model):
        args.ledger = args.model = True
    iso2 = args.country.upper()
    if args.ledger:
        report_ledger(iso2)
    if args.model:
        report_model(iso2, [int(c) for c in args.cuts.split(",") if c.strip()])
    return 0


if __name__ == "__main__":
    sys.exit(main())

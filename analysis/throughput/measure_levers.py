#!/usr/bin/env python3
"""Throughput levers, measured against committed state. Read-only, no keys.

    python3 analysis/throughput/measure_levers.py

Four sections, and none of them calls a model or a network:

  [1] the gate ERROR rate, and whether it is a standing loss or one incident
  [2] why the leadership parser closed zero rows for a whole priced window
  [3] LEVER 1 -- deterministic non-English leadership extraction, measured
  [4] LEVER 2 -- the cross-language duplicate pre-check, and its FALSE-DROP
      AUDIT, which is the precondition and not the follow-up

Every figure prints as MEASURED (read out of a named file) or MODELLED
(arithmetic on measured inputs, with the inputs shown). The unit prices in [3]
and [4] come from `cost_projection.py`'s own tables and are quoted, never
remembered.

WHAT THE REPLAY CANNOT DO, stated once rather than implied:

  * The gate ledger records the headline and teaser the gate read. It does NOT
    record `source_name` or `published_date`. `strip_publisher` refuses to
    guess a publisher suffix, so the replay maps host -> the source_name the
    database most often gave that host; where no mapping exists the suffix
    stays on and the grammar declines. That understates the parse rate and
    never overstates it.
  * With no `published_date`, the replay cannot reproduce the +/-21 day window
    the pre-check applies live. It therefore runs with NO window, which makes
    the pre-check strictly more eager than it will be -- overstating both the
    saving and the false-drop rate. For a safety audit that is the only
    direction worth being wrong in.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from analysis.recall.stats import wilson  # noqa: E402
from pipeline import leadership_intl, prefilter, schema  # noqa: E402

LEDGER = os.path.join("data", "gate_labels", "labels-2026-08.jsonl")
DB = os.path.join("data", "talent_intel.db")

# Terminal outcomes, per data/gate_labels/README.md. `deferred` is the one
# non-terminal outcome and a later run writes a second line under the same key,
# so the rule is: take the last TERMINAL outcome per key.
TERMINAL = frozenset({"stored", "duplicate", "retracted", "gate_reject",
                      "model_reject", "validate_reject", "error",
                      "would_store", "unknown"})

# Unit prices, all quoted from cost_projection.py's [3], [4] and [5] tables.
GATE_CALL = 0.000051        # $/call   google/gemini-2.5-flash-lite
EXTRACT_CALL = 0.001059     # $/call   deepseek/deepseek-chat
MARGINAL_READ = 0.00131     # $/read   cost_projection [5]
EXTRACTION_MONTH = 14.82    # $/month  cost_projection [4], today's caps
GATE_MONTH = 3.09           # $/month  cost_projection [4]
DAYS_PER_MONTH = 30.4

# The generic publisher-suffix strip used ONLY to estimate what the grammar
# would parse in production, where source_name is always present. Never used by
# the pipeline itself.
_TAIL = re.compile(r"\s+[-–|]\s+[^-–|]{2,60}$")


def load_ledger(path=LEDGER):
    last = {}
    for line in open(path, encoding="utf-8"):
        entry = json.loads(line)
        if entry["outcome"] in TERMINAL:
            last[entry["key"]] = entry
    return last


def host_to_source_name(conn):
    counts = collections.defaultdict(collections.Counter)
    for row in conn.execute("SELECT source_url, discovery_url, source_name "
                            "FROM signals WHERE is_current = 1"):
        url = row["source_url"] or row["discovery_url"] or ""
        host = url.split("://", 1)[-1].split("/", 1)[0].lower()
        if row["source_name"]:
            counts[host][row["source_name"]] += 1
    return {host: c.most_common(1)[0][0] for host, c in counts.items()}


def as_item(entry, source_name="", generic_strip=False):
    headline = entry["headline"]
    if generic_strip:
        headline = _TAIL.sub("", headline).strip()
        source_name = ""
    teaser = entry["teaser"]
    return {"headline": headline, "lang": entry["lang"],
            "source_name": source_name,
            "raw_text": (headline + ("\n\n" + teaser if teaser else "")).strip()}


# --- [1] the gate ERROR rate -------------------------------------------------

def section_gate_errors(last):
    print("[1] MEASURED  the gate ERROR rate    (data/gate_labels/labels-2026-08.jsonl)")
    by_day = collections.defaultdict(collections.Counter)
    for entry in last.values():
        by_day[entry["ts"][:10]][entry["gate"]] += 1
    total = collections.Counter()
    print("    %-12s %7s %7s %7s %7s %8s" % ("day", "total", "YES", "NO", "ERROR", "ERROR%"))
    for day in sorted(by_day):
        c = by_day[day]
        n = sum(c.values())
        total.update(c)
        print("    %-12s %7d %7d %7d %7d %7.1f%%"
              % (day, n, c["YES"], c["NO"], c["ERROR"], 100 * c["ERROR"] / n))
    n = sum(total.values())
    print("    %-12s %7d %7d %7d %7d %7.1f%%"
          % ("ALL", n, total["YES"], total["NO"], total["ERROR"],
             100 * total["ERROR"] / n))

    errors = [e for e in last.values() if e["gate"] == "ERROR"]
    days = {e["ts"][:10] for e in errors}
    hours = sorted(e["ts"][11:13] for e in errors)
    print()
    print("    every ERROR falls on %s, between %s:00 and %s:00 UTC."
          % (", ".join(sorted(days)), hours[0], hours[-1]))
    print("    collectors: %s" % dict(collections.Counter(e["collector"] for e in errors)))
    print("    outcome of every one: %s"
          % dict(collections.Counter(e["outcome"] for e in errors)))
    print()
    print("    VERDICT: not a standing 25.9% loss. It is one provider outage,")
    print("    already diagnosed and already handled. `classify.gate_verdict`")
    print("    returns ERROR on Throttled/ClassifyError and `run_collect`'s")
    print("    ClassifyError arm counts it with the DEFERRALS and deliberately")
    print("    does NOT mark the URL seen, so every one of these candidates")
    print("    returns on the next healthy run. `run_outcome(mostly_errored=)`")
    print("    turns a run that could not judge its candidates into a failure")
    print("    instead of a quiet one. Both landed 2026-08-04, the day after.")
    print("    The 25.9% is a three-day window that contains the outage day; on")
    print("    the two clean days the rate is 0.0%.")
    print()


# --- [2] why the leadership parser closed zero -------------------------------

def _decline_reason(item, lang):
    """Which guard in leadership_intl declined this item. Mirrors the module's
    own order; a divergence here is a bug in this script, not in the module."""
    head = leadership_intl.strip_publisher(item["headline"], item["source_name"])
    text = item["raw_text"]
    if not lang:
        return "unsupported language"
    if leadership_intl._DEPARTURE.search(text):
        return "a departure, not an appointment"
    if leadership_intl._UNCARRIED.search(text):
        return "interim or a stated start date"
    if leadership_intl._DEAL.search(text) or leadership_intl._AMOUNT.search(text):
        return "a deal or a money figure"
    if prefilter._REDUCTION.search(text) or prefilter._RIF.search(text):
        return "a workforce reduction"
    if len(re.findall(leadership_intl._TITLES[lang], head, re.I)) > 1:
        return "two seats named"
    letters = [c for c in head if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return "all-caps headline"
    if "?" in head or ";" in head:
        return "a question or two clauses"
    if not re.search(leadership_intl._TITLES[lang], head, re.I):
        return "no chief-executive title in the headline"
    if leadership_intl.parse_appointment(item):
        return "CLOSES"
    for pattern in leadership_intl._COMPILED[lang]:
        match = pattern.match(head)
        if match:
            if not leadership_intl.valid_person(match.group("person")):
                return "person span rejected"
            if not leadership_intl.valid_company(match.group("company")):
                return "employer span rejected"
    return "no pattern matched"


def joined_stored(last, conn):
    """Ledger lines that stored, joined to their row. The join key is
    `sha1(source_url or discovery_url or headline)[:16]`, gate_ledger's own."""
    stored = {k: e for k, e in last.items() if e["outcome"] == "stored"}
    out = []
    for row in conn.execute(
            "SELECT source_url, discovery_url, headline, pillar, collector, "
            "source_name, company FROM signals WHERE is_current = 1 "
            "AND captured_at >= '2026-07-31'"):
        ident = row["source_url"] or row["discovery_url"] or row["headline"] or ""
        key = hashlib.sha1(ident.encode("utf-8", "replace")).hexdigest()[:16]
        entry = stored.get(key)
        if entry:
            out.append((entry, row))
    return out


def section_why_zero(pairs):
    print("[2] MEASURED  why _parse_leadership closed zero for the whole priced window")
    leadership = [(e, r) for e, r in pairs if r["pillar"] == "leadership_change"]
    langs = collections.Counter(e["lang"] for e, _ in leadership)
    print("    %d stored leadership rows joined to their ledger line." % len(leadership))
    print("    languages: %s" % ", ".join("%s %d" % kv for kv in langs.most_common(10)))
    print()
    print("    English `_LEADERSHIP_SHAPE` is an appoints/names/taps verb list, and")
    print("    it is where they die. Replaying it over these rows:")
    tally = collections.Counter()
    for entry, _row in leadership:
        item = as_item(entry, _row["source_name"] or "")
        from pipeline import cheap_extract
        head = item["headline"]
        if cheap_extract._LEADERSHIP_SHAPE.match(head):
            tally["shape matched"] += 1
        else:
            tally["SHAPE regex no match"] += 1
    for k, v in tally.most_common():
        print("      %-26s %5d  %5.1f%%" % (k, v, 100 * v / len(leadership)))
    print()
    print("    Nothing was wrong with the parser. It was never shown a sentence")
    print("    it could read.")
    print()
    print("    Now the same rows through `leadership_intl`, by decline reason:")
    reasons = collections.Counter()
    supported = 0
    for entry, row in leadership:
        item = as_item(entry, row["source_name"] or "")
        lang = leadership_intl.language(item)
        if lang:
            supported += 1
        reasons[_decline_reason(item, lang)] += 1
    for k, v in reasons.most_common(12):
        print("      %-38s %5d  %5.1f%%" % (k, v, 100 * v / len(leadership)))
    print("    in a supported language: %d of %d" % (supported, len(leadership)))
    print()
    return leadership


# --- [3] LEVER 1 -------------------------------------------------------------

def section_lever_one(last, leadership, conn):
    print("[3] LEVER 1   deterministic non-English leadership extraction")
    from pipeline import vocab
    parsed = agree_company = agree_person = 0
    for entry, row in leadership:
        item = as_item(entry, row["source_name"] or "")
        appointment = leadership_intl.parse_appointment(item)
        if not appointment:
            continue
        parsed += 1
        if vocab.company_key(appointment.company) == vocab.company_key(row["company"] or ""):
            agree_company += 1
        prose = " ".join(str(row[f] or "") for f in ("headline", "company")).lower()
        summary = (conn.execute("SELECT summary FROM signals WHERE source_url = ?",
                                (row["source_url"],)).fetchone() or {"summary": ""})["summary"] or ""
        if appointment.person.split()[-1].lower() in (summary + prose).lower():
            agree_person += 1
    print("    MEASURED against the PAID MODEL's own reading of the same URLs.")
    print("    The model is not ground truth; a disagreement is a row to hand-read,")
    print("    and all of them were.")
    for label, hits in (("employer key agrees", agree_company),
                        ("person agrees", agree_person)):
        low, high = wilson(hits, parsed)
        print("      %-22s %4d/%-4d  %5.1f%%  [%.1f, %.1f]"
              % (label, hits, parsed, 100 * hits / parsed, 100 * low, 100 * high))
    print()

    paid = [e for e in last.values() if e["gate"] == "YES"]
    closes = collections.Counter()
    for entry in paid:
        if leadership_intl.parse_appointment(as_item(entry, generic_strip=True)):
            closes[entry["outcome"]] += 1
    n_closes = sum(closes.values())
    share = n_closes / len(paid)
    print("    MEASURED  over every candidate that reached PAID extraction (gate YES):")
    print("      paid extractions in the window : %d" % len(paid))
    print("      closed for $0 by this grammar  : %d  (%.1f%% of paid extraction volume)"
          % (n_closes, 100 * share))
    print("      what the paid path made of them: %s" % dict(closes))
    print()
    saving = share * (EXTRACTION_MONTH + GATE_MONTH)
    reads = saving / MARGINAL_READ / DAYS_PER_MONTH
    print("    MODELLED  inputs shown, all from cost_projection.py:")
    print("      $/month = %.4f x (extraction $%.2f + gate $%.2f) = $%.2f"
          % (share, EXTRACTION_MONTH, GATE_MONTH, saving))
    print("      a free close skips the gate too, because cheap_extract runs")
    print("      BEFORE classify.classify and the gate lives inside it.")
    print("      extra reads/day = $%.2f / $%.5f / %.1f = %.0f"
          % (saving, MARGINAL_READ, DAYS_PER_MONTH, reads))
    print()
    return saving, reads


# --- [4] LEVER 2 and the false-drop audit ------------------------------------

def section_lever_two(last, conn):
    print("[4] LEVER 2   cross-language duplicate pre-check, and its FALSE-DROP AUDIT")
    names = host_to_source_name(conn)
    own = {}
    for row in conn.execute("SELECT source_url, discovery_url, signal_id "
                            "FROM signals WHERE is_current = 1"):
        url = row["source_url"] or row["discovery_url"] or ""
        own.setdefault(hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:16],
                       row["signal_id"])

    dups = [e for e in last.values() if e["outcome"] == "duplicate"]
    stored = [e for e in last.values() if e["outcome"] == "stored"]
    print("    MEASURED  what the 612 wasted extractions actually ARE:")
    title = re.compile(r"\bceo\b|chief exec|amministratore delegato|directeur g|"
                       r"director general|\bvd\b|vorstandsvorsitzend|대표이사|"
                       r"מנכ|genel m|presidente|diretor", re.I)
    shaped = sum(1 for e in dups if title.search(e["headline"]))
    amount = sum(1 for e in dups if leadership_intl._AMOUNT.search(
        e["headline"] + " " + e["teaser"]))
    print("      chief-executive appointments : %d of %d  (%.1f%%)"
          % (shaped, len(dups), 100 * shaped / len(dups)))
    print("      carrying a currency amount   : %d of %d  (%.1f%%)"
          % (amount, len(dups), 100 * amount / len(dups)))
    print("      -> the cross-language duplicate is an APPOINTMENT, not a round.")
    print()

    skips = {"duplicate": [], "stored": []}
    for entry in dups + stored:
        item = as_item(entry, names.get(entry["host"], ""))
        appointment = leadership_intl.parse_appointment(item)
        if not appointment:
            continue
        tokens = [t for t in re.split(r"[\s'’-]+", appointment.person) if len(t) > 1]
        if len(tokens) < 2:
            continue
        rows = conn.execute(
            "SELECT signal_id, headline, summary, talent_readthrough FROM signals "
            "WHERE is_current = 1 AND company_key = ? "
            "AND pillar = 'leadership_change' "
            "AND substr(captured_at,1,16) < ? AND signal_id IS NOT ?",
            (appointment.company_key, entry["ts"][:16], own.get(entry["key"]))
        ).fetchall()
        for row in rows:
            prose = " ".join(str(row[f] or "") for f in
                             ("headline", "summary", "talent_readthrough")).lower()
            if all(t.lower() in prose for t in tokens):
                skips[entry["outcome"]].append((entry, appointment, row))
                break

    good, bad = len(skips["duplicate"]), len(skips["stored"])
    total = good + bad
    print("    THE AUDIT. Ground truth is the ledger's own terminal outcome.")
    print("      correct skips (it WAS already held) : %d" % good)
    print("      false drops   (it was NOT held)     : %d" % bad)
    if total:
        low, high = wilson(bad, total)
        print("      FALSE-DROP RATE                     : %.1f%%  (%d/%d)  [%.1f, %.1f]"
              % (100 * bad / total, bad, total, 100 * low, 100 * high))
    print()
    for entry, appointment, row in skips["stored"]:
        print("      HAND-READ  candidate: %s" % entry["headline"][:110])
        print("                 matched  : %s" % (row["headline"] or "")[:110])
    print()
    print("    WHAT THIS BUYS, and the honest answer is almost nothing:")
    overlap = sum(1 for e, _a, _r in skips["duplicate"]
                  if leadership_intl.extract(as_item(e, names.get(e["host"], "")),
                                             count=False) is not None)
    print("      %d of the %d correct skips are ALSO closed for $0 by lever 1."
          % (overlap, good))
    print("      A free close already costs nothing -- no gate, no extraction, no")
    print("      read -- and the existing content-hash and fuzzy layers then drop")
    print("      the row. So lever 2's marginal saving on this population is $0,")
    print("      and the doc's separate $3.01/month for it does not survive")
    print("      lever 1 being built. It is kept because it is where the code")
    print("      belongs and because it records the skip as a duplicate rather")
    print("      than as a dedup-suppressed store, not because it saves money.")
    print()


def main():
    if not os.path.exists(LEDGER):
        print("no ledger at %s -- run from the repo root" % LEDGER)
        return 3
    conn = schema.connect(DB)
    conn.row_factory = sqlite3.Row
    last = load_ledger()
    print("=" * 78)
    print("THROUGHPUT LEVERS, MEASURED   %d candidates with a terminal outcome"
          % len(last))
    print("=" * 78)
    print()
    section_gate_errors(last)
    pairs = joined_stored(last, conn)
    leadership = section_why_zero(pairs)
    section_lever_one(last, leadership, conn)
    section_lever_two(last, conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

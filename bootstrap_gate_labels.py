#!/usr/bin/env python3
"""A WEAK historical label set, built from what the database already remembers.

WHY IT EXISTS
-------------
`pipeline/gate_ledger.py` starts collecting real labels today, and the plan
(docs/PLAN-gate-to-five-dollars.md, step 1) says two to four weeks of them is
enough to train the classifier gate. This script exists so the four weeks can be
spent PROTOTYPING rather than waiting: `seen_urls` has held an outcome for every
candidate since the beginning, so a rough label set can be reconstructed now.

WHY IT IS WEAK, IN FOUR WAYS, AND WHY THAT IS NOT FIXABLE HERE
--------------------------------------------------------------
1. **IT CONTAINS NO TRUE GATE REJECTS.** A gate NO was never written down; the
   candidate was marked `rejected` in `seen_urls` exactly like a candidate the
   full extraction refused, and the two are indistinguishable in the database.
   The negative class here is therefore "everything the pipeline declined", of
   which the gate's own rejects are an unknown fraction.
2. **THE FEATURES ARE URL SLUGS, NOT THE GATE'S TEXT.** The source headline and
   teaser were never stored for a rejected candidate — only its URL — so the
   only feature available for BOTH classes is the URL. Using the real headline
   for positives (which `signals` does hold) and a slug for negatives would let
   a classifier separate them on field shape alone and score beautifully while
   learning nothing, so this script deliberately uses the slug for both.
3. **A SLUG IS LOSSY.** Case is gone, punctuation is gone, many publishers
   truncate, and a few use numeric slugs (those rows are dropped, symmetrically,
   by the token-count floor below).
4. **THE POSITIVE CLASS IS ALSO NOT THE STORED-ROW POPULATION.** `duplicate`
   candidates were real signals we already held; as "should the gate keep this"
   they are yes, as "did this become a stored row" they are no. Ambiguous, so
   they are excluded rather than guessed at.

SO: FINE FOR PROTOTYPING, NOT FINE TO SHIP A CLASSIFIER AGAINST. The plan says
so and every line written here says so too — `"weak": true` and
`"basis": "url_slug"` are on every record, and the file is named for it. The
replay test that decides whether the classifier ships (>=99.5% of eventually
stored candidates routed relevant-or-uncertain) must be run on REAL ledger
labels, which is what the ledger is now accumulating.

    python3 bootstrap_gate_labels.py            # write data/gate_labels/
    python3 bootstrap_gate_labels.py --dry-run  # counts only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter

from pipeline import gate_ledger, provider_names

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "data", "talent_intel.db")
OUT_NAME = "bootstrap-weak.jsonl"

# The collectors whose candidates actually reach the LLM gate. A collector with
# `as_classified` derives its record from structured fields and never calls a
# model at all (run_collect: "there is no spend to cap"), so its rows are not
# part of the population the classifier replaces — and its text is nothing like
# news prose. Including uk_paygap's 4,761 stored rows would have made the
# positive class mostly gender-pay-gap filings and taught the classifier to
# recognise a government form.
GATE_COLLECTORS = ("google_news", "gdelt", "national_press", "sec_edgar",
                   "sec_form_d", "news_backstop", "press_archive",
                   "tripwire_chase")

# Outcomes that are unambiguous about the classifier's real target — did this
# candidate end up a stored row.
POSITIVE, NEGATIVE = "stored", "rejected"

# A slug needs this many word-shaped tokens to be worth a line. Set at 3 to drop
# SEC accession URLs (ck0002032019-20260109.htm) and numeric-id slugs, and it
# drops them from BOTH classes on the same rule, which is the only thing that
# matters here.
MIN_SLUG_TOKENS = 3

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_SPLIT = re.compile(r"[-_+.]+")


def slug_text(url: str) -> str:
    """The most word-like path segment of a URL, as plain text.

    Picks a segment rather than the whole path because a path is mostly
    taxonomy ("/economia/20260729/11603767/") and one segment is the headline.
    The most word-shaped one is the headline in every publisher layout this
    repo has met.
    """
    try:
        rest = url.split("://", 1)[-1]
        path = rest.split("/", 1)[1] if "/" in rest else ""
    except Exception:
        return ""
    path = path.split("?", 1)[0].split("#", 1)[0]

    best, best_score = "", 0
    for segment in path.split("/"):
        if not segment:
            continue
        segment = re.sub(r"\.(html?|php|aspx?|xml|json|shtml)$", "", segment,
                         flags=re.I)
        words = _WORD.findall(_SPLIT.sub(" ", segment))
        if len(words) > best_score:
            best, best_score = " ".join(words), len(words)
    if best_score < MIN_SLUG_TOKENS:
        return ""
    # A slug is a headline with the punctuation taken out, so it carries the
    # same provider names a headline does. Redacted at the point the string is
    # built, for the reason `pipeline/gate_ledger._clean` gives at length.
    return provider_names.redact(best)


def host_of(url: str) -> str:
    rest = (url or "").split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("@")[-1].split(":")[0].lower()[:80]
    return provider_names.redact(host)


def rows(conn: sqlite3.Connection):
    placeholders = ",".join("?" for _ in GATE_COLLECTORS)
    return conn.execute(
        f"""SELECT url, collector, outcome, first_seen
              FROM seen_urls
             WHERE collector IN ({placeholders})
               AND outcome IN (?, ?)
          ORDER BY first_seen, url""",
        (*GATE_COLLECTORS, POSITIVE, NEGATIVE),
    )


def build(conn: sqlite3.Connection) -> tuple[list[dict], Counter]:
    seen_keys: set[str] = set()
    labels: list[dict] = []
    tally: Counter = Counter()

    for url, collector, outcome, first_seen in rows(conn):
        tally["candidates"] += 1
        headline = slug_text(url or "")
        if not headline:
            tally[f"dropped_no_slug:{collector}"] += 1
            tally["dropped_no_slug"] += 1
            continue
        key = gate_ledger.key({"source_url": url})
        if key in seen_keys:
            tally["dropped_duplicate_key"] += 1
            continue
        seen_keys.add(key)

        labels.append({
            "key": key,
            "ts": (first_seen or "")[:16].replace(":00", "") or "",
            "collector": collector or "",
            "host": host_of(url),
            # Never populated: `seen_urls` holds neither, and taking them from
            # `signals` would give the positive class two fields the negative
            # class cannot have. That asymmetry is the exact thing that makes a
            # bootstrap set score well and teach nothing.
            "lang": "",
            "country": "",
            "headline": headline[:gate_ledger.HEADLINE_CHARS],
            "teaser": "",
            # The gate's own verdict is NOT recoverable: a gate NO and an
            # extraction NO were both written to seen_urls as 'rejected'.
            "gate": "UNKNOWN",
            "outcome": outcome,
            "basis": gate_ledger.BASIS_URL_SLUG,
            # On every line, not only in the filename and the docs, so a
            # training script that globs the directory cannot mix these with
            # real gate labels by accident.
            "weak": True,
        })
        tally[outcome] += 1

    return labels, tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--out-dir", default=gate_ledger.LEDGER_DIR)
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would be written, write nothing")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"no database at {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        labels, tally = build(conn)
    finally:
        conn.close()

    positives = tally[POSITIVE]
    negatives = tally[NEGATIVE]
    print(f"candidates considered : {tally['candidates']}")
    print(f"  positives (stored)  : {positives}")
    print(f"  weak negatives      : {negatives}")
    print(f"  dropped, no usable slug: {tally['dropped_no_slug']} "
          f"(numeric or accession-number URLs, dropped from both classes)")
    if tally["dropped_duplicate_key"]:
        print(f"  dropped, duplicate URL : {tally['dropped_duplicate_key']}")
    for name, count in sorted(tally.items()):
        if name.startswith("dropped_no_slug:"):
            print(f"      {name.split(':', 1)[1]:<16} {count}")

    if not labels:
        print("nothing to write", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, OUT_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        for label in labels:
            fh.write(json.dumps(label, ensure_ascii=False,
                                separators=(",", ":")) + "\n")

    size = os.path.getsize(path)
    print(f"\nwrote {len(labels)} weak labels to {path} ({size // 1024} KB)")
    print("\nWEAK. It holds NO true gate rejects — a gate NO and an extraction "
          "NO were both\nrecorded as 'rejected' — and its features are URL "
          "slugs, not the headline and\nteaser the gate reads. Prototype "
          "against it; do NOT measure the shipping bar\nagainst it. That "
          "measurement needs the real ledger in " + gate_ledger.LEDGER_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

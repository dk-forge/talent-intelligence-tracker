#!/usr/bin/env python3
"""Re-point rows whose cited outlet MOVED, to the same document at its new address.

QUEUE IT, NEVER DISPATCH IT (CLAUDE.md):

    gh workflow run drain-writers.yml -f enqueue=correct-drifted-sources.yml \
         -f inputs_json='{"dry_run":"false"}' -f reason='why'

link_check.py records a cited URL whose redirect lands on a different domain as
`drifted`. Most of that is a betting site squatting a dead masthead, which is
why nothing acts on it automatically. Some of it is the outlet itself moving
(m.economictimes.com -> economictimes.indiatimes.com, the same article id) or
an employer pointing its Greenhouse board at its own careers site.

Owner ruling 2026-10-19: "yes, update them to their new addresses", for the
ones where the new address IS the same document. That judgement is made by a
person, per URL, and committed in `data/drifted_repoint.json` (old -> new, with
why). A drifted URL that is not in that file is never touched, so a redirect to
a homepage, a generic careers landing page or somebody else's site stays
exactly as link_check reported it, for a human to retract or re-source.

THE PATH, which is the existing one: the proposal is written into the committed
re-source ledger (`pipeline/re_source.py`) by `--record`, read, committed, and
only then `--apply` pushes it through the site's /re-source door (headline
unchanged, citation moves host, collector bound) and appends a revision with
`store.revise()`, so the original citation survives at is_current = 0. The row
is never edited in place locally. No model is called; no page is fetched.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from pipeline import re_source, schema, store, validate

SPEC = Path(__file__).resolve().parent / "data" / "drifted_repoint.json"
NOTE = ("citation re-pointed to the same document at its new address after the "
        "outlet moved domain (owner ruling 2026-10-19; data/drifted_repoint.json)")
_FIELDS = tuple(f.name for f in dataclasses.fields(validate.Signal))


def load_spec(path: Path = SPEC) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    for old, entry in spec.items():
        new = entry.get("new_url") or ""
        if not new.startswith("https://") or not entry.get("why"):
            raise ValueError(f"{old}: needs an https new_url and a why")
        if (urlparse(new).hostname or "").lower() == (urlparse(old).hostname or "").lower():
            raise ValueError(f"{old}: the new address must be on a different host")
    return spec


def targets(conn, spec: dict) -> list:
    if not spec:
        return []
    marks = ",".join("?" * len(spec))
    return conn.execute(
        f"SELECT * FROM signals WHERE is_current = 1 AND source_url IN ({marks}) "
        "ORDER BY row_id", tuple(spec)).fetchall()


def repointed(row, new_url: str) -> validate.Signal:
    """The row with only its citation moved. The headline and the outlet name
    are kept, so the fingerprint cannot move, and that is asserted."""
    signal = validate.Signal(**{f: row[f] for f in _FIELDS})
    signal.source_url = new_url
    rehashed = validate.content_hash(signal.company_key, signal.pillar,
                                     signal.published_date, signal.headline,
                                     signal.source_name)
    if rehashed != row["content_hash"]:
        raise RuntimeError(f"{row['content_hash']}: re-pointing would move the content_hash")
    return signal


def run(conn, spec: dict, *, mode: str, ledger_path=re_source.DEFAULT_LEDGER,
        push=re_source.push) -> int:
    ledger = re_source.load_ledger(ledger_path)
    rows = targets(conn, spec)
    print(f"{len(rows)} current row(s) cite one of {len(spec)} re-pointable URL(s)")
    done = 0
    for row in rows:
        new = spec[row["source_url"]]["new_url"]
        signal = repointed(row, new)
        print(f"  {row['content_hash'][:12]} {row['company']}: {row['source_url']} -> {new}")
        if mode == "record":
            re_source.record(ledger, row["content_hash"], source_url=new,
                             source_name=row["source_name"], headline=row["headline"],
                             route="canonical")
        elif mode == "apply":
            if row["published_at"]:
                push(row, source_url=new, source_name=row["source_name"],
                     headline=row["headline"], ledger=ledger)
            else:
                re_source.entry_for(ledger, row["content_hash"], source_url=new,
                                    source_name=row["source_name"], headline=row["headline"])
            store.revise(conn, row["signal_id"], signal, NOTE)
            if row["published_at"]:
                conn.execute("UPDATE signals SET published_at = ? WHERE signal_id = ? "
                             "AND is_current = 1", (row["published_at"], row["signal_id"]))
            conn.commit()
        done += 1
    if mode == "record":
        re_source.save_ledger(ledger, ledger_path)
    print(f"{done} row(s) {dict(record='recorded in the ledger', apply='re-pointed').get(mode, 'would be re-pointed (dry run)')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--record", action="store_true", help="write the proposals into the re-source ledger")
    g.add_argument("--apply", action="store_true", help="push the recorded proposals and revise the rows")
    args = ap.parse_args(argv)
    conn = schema.connect()
    mode = "record" if args.record else "apply" if args.apply else "dry"
    return run(conn, load_spec(), mode=mode)


if __name__ == "__main__":
    sys.exit(main())

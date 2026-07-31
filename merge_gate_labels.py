#!/usr/bin/env python3
"""Fold one run's gate labels into the ledger on main, without losing either.

WHY THIS IS NEEDED AT ALL
-------------------------
The commit step in collect.yml does `git reset --hard origin/main` before it
commits, on every attempt, because attempt 1 is already working from a possibly
stale checkout. That reset would throw away the labels this run just wrote, in
exactly the way `merge_db.py` exists to stop it throwing away the database. So
the labels are copied aside before the reset and folded back in afterwards —
the same shape as the database merge, on purpose, because a second pattern for
the same hazard is a second pattern somebody has to learn.

A plain `cp` back would be wrong even though collect jobs are serialised by the
`talent-collect` concurrency group: the copy is a SUPERSET of origin only if
nothing else appended in the meantime, and "only if nothing else happened" is
the assumption this repo has been burned by most. So it appends what origin does
not already have, line for line, and touches nothing else in the directory.

    python3 merge_gate_labels.py <saved-copy-dir> <ledger-dir>
"""

from __future__ import annotations

import gzip
import os
import sys

from pipeline import gate_ledger


def _open(path: str, mode: str):
    opener = gzip.open if path.endswith(".gz") else open
    return opener(path, mode, encoding="utf-8")


def _read(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with _open(path, "rt") as fh:
        return [line.rstrip("\n") for line in fh if line.strip()]


def _shards(directory: str) -> dict[str, str]:
    """Month stem -> filename, for the ledger's own shards only.

    bootstrap-weak.jsonl and README.md live in this directory too and are NOT
    per-run output: `git reset --hard` restores them from origin already, and
    merging them would be a way to duplicate 4,328 lines every run.
    """
    found: dict[str, str] = {}
    if not os.path.isdir(directory):
        return found
    for name in sorted(os.listdir(directory)):
        if not name.startswith(gate_ledger.SHARD_PREFIX):
            continue
        if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
            continue
        stem = name[len(gate_ledger.SHARD_PREFIX):].split(".", 1)[0]
        if len(stem) == 7 and stem[4] == "-":
            found[stem] = name
    return found


def merge(src_dir: str, dst_dir: str) -> tuple[int, list[str]]:
    """Append src's new lines to dst. Returns (lines added, notes)."""
    notes: list[str] = []
    added = 0
    src_shards, dst_shards = _shards(src_dir), _shards(dst_dir)
    if not src_shards:
        return 0, ["no gate-label shards to merge"]

    os.makedirs(dst_dir, exist_ok=True)
    for stem, src_name in sorted(src_shards.items()):
        src_lines = _read(os.path.join(src_dir, src_name))
        # Prefer whatever form the destination already has: a month that closed
        # mid-run is compressed on one side and plain on the other, and the
        # compressed form is the one that should survive.
        dst_name = dst_shards.get(stem, src_name)
        dst_path = os.path.join(dst_dir, dst_name)
        dst_lines = _read(dst_path)

        held = set(dst_lines)
        fresh = [line for line in src_lines if line not in held]
        if not fresh:
            continue
        # Rewritten whole rather than appended: a gzip member appended to a
        # gzip file is legal but a plain append to a compressed shard is not,
        # and one code path is worth more here than one syscall.
        with _open(dst_path, "wt") as fh:
            for line in dst_lines + fresh:
                fh.write(line + "\n")
        added += len(fresh)
        notes.append(f"{dst_name}: +{len(fresh)} label(s) "
                     f"({len(dst_lines)} already held)")
    return added, notes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    src_dir, dst_dir = sys.argv[1], sys.argv[2]
    try:
        added, notes = merge(src_dir, dst_dir)
    except Exception as exc:
        # Same rule as the ledger itself: bookkeeping may not fail a collect
        # run that has already stored and published rows.
        print(f"::warning::gate labels could not be merged ({exc}) — this "
              "run's labels are lost, the data it collected is not",
              file=sys.stderr)
        return 0
    for note in notes:
        print(f"gate labels: {note}")
    print(f"gate labels: {added} line(s) merged into {dst_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

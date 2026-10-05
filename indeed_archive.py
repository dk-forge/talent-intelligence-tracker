#!/usr/bin/env python3
"""Monthly archive of the Indeed Hiring Lab reference table.

    python3 indeed_archive.py --snapshot [--month YYYY-MM]

Two halves, one per size class (indeed-archive.yml runs both):

* SMALL, IN GIT -- this script: the current reduced table
  (data/reference/indeed_occupations/latest.json) gzipped to
  data/archive/indeed_hiring_lab/monthly/<YYYY-MM>.json.gz with a fixed gzip
  mtime, so a re-run in the same month with the same data is byte-identical and
  commits nothing. Refuses a snapshot over MAX_SNAPSHOT_BYTES.
* LARGE, NEVER IN GIT -- the workflow: a `git bundle` of the whole
  hiring-lab/job_postings_tracker history (~1 GB) uploaded as the GitHub
  Release asset `indeed-hiring-lab-<YYYY-MM>`. CC BY 4.0 permits the copy; the
  release notes carry the attribution.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from datetime import datetime, timezone
from pathlib import Path

import reference_store

ARCHIVE = reference_store.REPO_ROOT / "data" / "archive" / "indeed_hiring_lab" / "monthly"
MAX_SNAPSHOT_BYTES = 5_000_000


def snapshot(month: str, store=reference_store.STORE, archive: Path = ARCHIVE) -> Path:
    src = Path(store) / "indeed_occupations" / "latest.json"
    if not src.is_file():
        raise SystemExit(f"{src} missing: nothing stored to archive yet")
    body = gzip.compress(src.read_bytes(), compresslevel=9, mtime=0)
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise SystemExit(f"snapshot {len(body)}B exceeds {MAX_SNAPSHOT_BYTES}B; "
                         "this belongs in a Release asset, not git")
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / f"{month}.json.gz"
    out.write_bytes(body)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--snapshot", action="store_true")
    p.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    a = p.parse_args(argv)
    if a.snapshot:
        out = snapshot(a.month)
        reference_store.notice(f"indeed archive snapshot {out.name} {out.stat().st_size}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The reference-data store: external context series, committed, never mixed in.

WHAT LIVES HERE. Free official series that frame the tracker's own signals --
Indeed Hiring Lab postings by occupation and country, the US DOL OFLC H-1B/LCA
employer aggregate -- each under `data/reference/<source>/`:

    latest.json (or a named data file)   the current reduced table
    MANIFEST.json                         source, licence, attribution, as_of,
                                          fetched_at, row count, sha256 per file

The repo IS the memory here, as it is for alert_state.json and the gate labels:
a weekly/quarterly collector on a GitHub-hosted runner writes the store and
commits it, `reference_freshness.py` reads the manifests offline and opens ONE
issue when a source goes stale. The site gets a compact copy through the keyed
`POST /talent/v1/reference-ingest/<source>` route (includes/reference_data.php),
same contract as /indeed-index: option first, nothing summed into a signal.

NEVER MIXED IN. Nothing in this module or its callers opens the signals
database, writes a row, or calls a model. These are someone else's counts with
someone else's licence, and every manifest carries the attribution line the
licence requires.

FAIL-SOFT ON STORE. A collector that could not build a complete table raises
before `write()` is reached, so a bad pull never overwrites a good one; and
`write()` itself refuses a table that shrank by more than SHRINK_LIMIT, which is
what a silently-truncated source looks like from here.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
STORE = REPO_ROOT / "data" / "reference"

#: A new table with fewer than this share of the stored table's rows is refused.
SHRINK_LIMIT = 0.5

#: Sources the site route accepts. Mirrors the PHP allowlist.
SOURCES = ("indeed_occupations",)


class StoreError(RuntimeError):
    """The store refused a write (shrink, empty, unknown source)."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_dir(source: str, store: Path = STORE) -> Path:
    if source not in SOURCES:
        raise StoreError(f"unknown reference source {source!r}; add it to SOURCES")
    return Path(store) / source


def read_manifest(source: str, store: Path = STORE) -> dict | None:
    path = source_dir(source, store) / "MANIFEST.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write(source: str, files: dict[str, bytes], *, rows: int, as_of: str,
          licence: str, attribution: str, source_url: str,
          store: Path = STORE, now: datetime | None = None,
          extra: dict | None = None) -> dict:
    """Write the table file(s) and the manifest. Returns the manifest.

    `files` maps a file name inside the source directory to its bytes. The
    previous manifest's row count is the shrink baseline.
    """
    if rows <= 0 or not files:
        raise StoreError(f"{source}: refusing to store an empty table")
    previous = read_manifest(source, store)
    if previous and previous.get("rows") and rows < previous["rows"] * SHRINK_LIMIT:
        raise StoreError(
            f"{source}: {rows} rows is under {SHRINK_LIMIT:.0%} of the stored "
            f"{previous['rows']}; a source that shrank this far is truncated, "
            "not smaller. Nothing was overwritten.")

    directory = source_dir(source, store)
    directory.mkdir(parents=True, exist_ok=True)
    listed = {}
    for name, body in sorted(files.items()):
        path = directory / name
        path.write_bytes(body)
        listed[name] = {"bytes": len(body), "sha256": _sha256(path)}

    manifest = {
        "source": source,
        "as_of": as_of,
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "rows": rows,
        "licence": licence,
        "attribution": attribution,
        "source_url": source_url,
        "files": listed,
    }
    if extra:
        manifest.update(extra)
    (directory / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def notice(message: str) -> None:
    """One Actions annotation line (readable via check-run annotations)."""
    print(f"::notice::{message}", flush=True)


def publish(source: str, payload: dict) -> str:
    """POST a compact copy to the keyed site route. Best effort.

    Returns "stored", "not-deployed" (the plugin on the host predates the
    route: a 404 is a deploy lag, not a data failure) or "skipped" with no key.
    Any other HTTP error raises, so a broken route is loud.
    """
    if not os.environ.get("WP_API_KEY"):
        return "skipped"
    import requests

    from pipeline.publish import TIMEOUT, USER_AGENT as WP_UA, PublishError, _config

    site, key = _config()
    resp = requests.post(
        f"{site}/wp-json/talent/v1/reference-ingest/{source}",
        json=payload,
        headers={"X-Talent-API-Key": key, "User-Agent": WP_UA},
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        return "not-deployed"
    if resp.status_code >= 400:
        raise PublishError(f"{resp.status_code}: {resp.text[:300]}")
    return "stored"

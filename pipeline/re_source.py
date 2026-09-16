"""Re-point a published row's citation, and only a citation that was recorded first.

The live site has a door for this, ``/re-source`` (plugin 1.88.7), and the
door holds its own invariants: the headline may only lose the current
masthead, the citation must move to a different host, both halves of the new
citation are required, the collector is named and the UPDATE is bound to it.
This module holds the OTHER half of the rule, the one a server cannot hold:

    NOTHING IS PUSHED THAT WAS NOT FIRST WRITTEN INTO A COMMITTED LEDGER.

A re-chase (correct_aggregator_sources.py) decides a row's new source by
matching an employer, a figure and a date in the news index. That decision is
deterministic but the INDEX is not: the same query on 2026-09-16 returned a
publisher for one row in the morning and nothing at all in the afternoon. So
the decision is recorded in a dry run, committed, read by a human, and only
then applied, and ``push`` refuses a row whose proposed (url, name, headline)
is not exactly what the ledger holds. A dry run that prints outlet names but
never the URL is not a ledger; ``record`` writes the shape ``push`` reads.

The ledger is JSON keyed by content_hash::

    {"<content_hash>": {"source_url": ..., "source_name": ..., "headline": ...,
                        "route": "canonical" | "index", "recorded_at": "<UTC>"}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline import publish

#: Where the re-chase records its proposals. Committed; the repo is the memory.
DEFAULT_LEDGER = Path(__file__).resolve().parent.parent / "data" / "re_source_ledger.json"

#: The three fields the door accepts, and the three a ledger entry must hold.
FIELDS = ("source_url", "source_name", "headline")


class NotInLedger(RuntimeError):
    """A push for a row the committed ledger does not hold, or holds differently."""


def load_ledger(path: str | os.PathLike = DEFAULT_LEDGER) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text() or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{p} is not a JSON object keyed by content_hash")
    return data


def record(ledger: dict, content_hash: str, *, source_url: str, source_name: str,
           headline: str, route: str) -> dict:
    """Write one proposal into the ledger (in memory; the caller saves it)."""
    if route not in ("canonical", "index"):
        raise ValueError(f"route must be canonical or index, not {route!r}")
    for name, value in (("source_url", source_url), ("source_name", source_name),
                        ("headline", headline)):
        if not (value or "").strip():
            raise ValueError(f"{name} is required in a ledger entry")
    ledger[content_hash] = {
        "source_url": source_url.strip(), "source_name": source_name.strip(),
        "headline": headline.strip(), "route": route,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return ledger[content_hash]


def save_ledger(ledger: dict, path: str | os.PathLike = DEFAULT_LEDGER) -> None:
    Path(path).write_text(json.dumps(ledger, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def entry_for(ledger: dict, content_hash: str, *, source_url: str, source_name: str,
              headline: str) -> dict:
    """The ledger entry for this row, or NotInLedger when absent or different."""
    entry = ledger.get(content_hash)
    if not entry:
        raise NotInLedger(f"no committed re-chase record for {content_hash}")
    proposed = {"source_url": source_url.strip(), "source_name": source_name.strip(),
                "headline": headline.strip()}
    held = {k: (entry.get(k) or "").strip() for k in FIELDS}
    if proposed != held:
        differ = sorted(k for k in FIELDS if proposed[k] != held[k])
        raise NotInLedger(
            f"the proposal for {content_hash} differs from the committed record "
            f"on {', '.join(differ)}; re-run the dry run and commit before applying")
    return entry


def push(row, *, source_url: str, source_name: str, headline: str, ledger: dict,
         session=None) -> dict:
    """POST one re-source to the site, only if the ledger holds exactly it.

    ``row`` needs ``content_hash`` and ``collector``. Raises NotInLedger before
    any request is built when the ledger disagrees; raises publish.PublishError
    on an HTTP error or a per-row refusal, so a data job goes red rather than
    counting a refused row as done.
    """
    entry_for(ledger, row["content_hash"], source_url=source_url,
              source_name=source_name, headline=headline)
    site, key = publish._config()
    payload = {"content_hash": row["content_hash"], "source_url": source_url.strip(),
               "source_name": source_name.strip(), "headline": headline.strip()}
    poster = session or requests
    resp = poster.post(
        f"{site}/wp-json/talent/v1/re-source",
        json={"collector": row["collector"], "rows": [payload]},
        headers={"X-Talent-API-Key": key, "User-Agent": publish.USER_AGENT,
                 "Content-Type": "application/json"},
        timeout=publish.TIMEOUT,
    )
    if resp.status_code >= 400:
        raise publish.PublishError(f"{resp.status_code}: {resp.text[:300]}")
    result = resp.json() or {}
    if result.get("errors"):
        raise publish.PublishError(f"/re-source refused: {result['errors']}")
    return result

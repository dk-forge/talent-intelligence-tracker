"""Load and validate the sealed gold set.

The gold set is a data file, not code, and it is versioned so that a published
recall figure can be re-derived later from exactly the reference set it was
measured against. Changing an item changes the file's digest, which changes the
digest recorded alongside every published measurement. That is what stops a
number being quietly improved after the fact.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(DIR, "goldset-2026-07.json")

REQUIRED_ITEM_FIELDS = (
    "id", "company", "signal_type", "event_date", "country", "size_band",
    "detail", "source_url", "source_type", "source_name",
)
VALID_SIGNAL_TYPES = {"funding", "leadership"}
VALID_SIZE_BANDS = {"large", "small"}
VALID_SOURCE_TYPES = {
    "filing", "press_release", "trade_press", "national_news",
}


def load(path: str = DEFAULT_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    # Digest the ITEMS, not the whole file. The reference set is what a
    # published figure was measured against; correcting a typo in the method
    # note must not look like the reference set changed, and adding an item
    # must.
    canonical = json.dumps(data.get("items", []), sort_keys=True, ensure_ascii=False)
    data["_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    data["_path"] = path
    return data


def validate(data: dict) -> list:
    """Every rule the gold set must satisfy. Returns a list of problems.

    Empty list means the file is fit to measure against. This runs in the test
    suite, so a gold set edited into an invalid state fails CI rather than
    silently producing a wrong denominator.
    """
    problems = []
    items = data.get("items", [])

    if not items:
        return ["gold set is empty"]

    window_start = datetime.strptime(data["window"]["start"], "%Y-%m-%d").date()
    window_end = datetime.strptime(data["window"]["end"], "%Y-%m-%d").date()

    seen_ids, seen_events = set(), set()
    for item in items:
        label = item.get("id") or item.get("company") or "<unnamed>"

        for field in REQUIRED_ITEM_FIELDS:
            if not item.get(field):
                problems.append(f"{label}: missing {field}")

        if item.get("signal_type") not in VALID_SIGNAL_TYPES:
            problems.append(f"{label}: bad signal_type {item.get('signal_type')!r}")
        if item.get("size_band") not in VALID_SIZE_BANDS:
            problems.append(f"{label}: bad size_band {item.get('size_band')!r}")
        if item.get("source_type") not in VALID_SOURCE_TYPES:
            problems.append(f"{label}: bad source_type {item.get('source_type')!r}")

        country = item.get("country") or ""
        if len(country) != 2 or not country.isupper():
            problems.append(f"{label}: country must be an ISO alpha-2 code, got {country!r}")

        url = item.get("source_url") or ""
        if not url.startswith("http"):
            problems.append(f"{label}: source_url must be a URL, got {url!r}")
        if "asktherecruiter.com" in url:
            problems.append(f"{label}: source_url points at our own tracker, which breaks independence")

        try:
            when = datetime.strptime(item.get("event_date", ""), "%Y-%m-%d").date()
        except ValueError:
            problems.append(f"{label}: event_date must be YYYY-MM-DD")
        else:
            if not window_start <= when <= window_end:
                problems.append(f"{label}: event_date {when} is outside the declared window")

        if item.get("signal_type") == "funding" and not item.get("amount_usd"):
            problems.append(f"{label}: a funding item needs amount_usd")

        if item.get("id") in seen_ids:
            problems.append(f"{label}: duplicate id")
        seen_ids.add(item.get("id"))

        event = (item.get("company", "").lower().strip(),
                 item.get("signal_type"), item.get("event_date"))
        if event in seen_events:
            problems.append(f"{label}: duplicate event")
        seen_events.add(event)

    return problems


def counts(data: dict) -> dict:
    """Shape of the gold set, for the method section of the public page."""
    items = data.get("items", [])
    out = {"total": len(items), "signal_type": {}, "geography": {},
           "country": {}, "source_type": {}, "size_band": {}}
    for item in items:
        for key, value in (
            ("signal_type", item["signal_type"]),
            ("geography", "US" if item["country"] == "US" else "non-US"),
            ("country", item["country"]),
            ("source_type", item["source_type"]),
            ("size_band", item["size_band"]),
        ):
            out[key][value] = out[key].get(value, 0) + 1
    return out

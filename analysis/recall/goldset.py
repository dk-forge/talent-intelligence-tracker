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


def all_paths(directory: str = DIR) -> list:
    """Every gold set ever used, oldest first.

    Nothing here is ever deleted. A published figure that cannot be re-derived
    from the exact list it was measured against is not a measurement, and sets
    get retired precisely because reusing one forever measures memory.
    """
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.startswith("goldset-") and name.endswith(".json")
        and ".draft" not in name
    )


def latest_path(directory: str = DIR) -> str:
    """The set a fresh measurement runs against: the newest one on disk.

    Sets are named goldset-YYYY-MM.json, so newest is last alphabetically. A
    draft is skipped by name until somebody seals it, which is the one step in
    this loop that is deliberately not automated.
    """
    paths = all_paths(directory)
    if not paths:
        raise FileNotFoundError(f"no gold set in {directory}")
    return paths[-1]


DEFAULT_PATH = latest_path()

# The shape any reference set must have, fixed in code so that it constrains
# sets which do not exist yet.
#
# This is the anti-flattery guard and it matters more than any per-item rule.
# The cheapest way to make this number look good would be to quietly rebuild the
# next set out of large US filings, which we read well, and leave out the small
# non-US press events, which we do not. Nobody would have to intend it: "use
# what was easy to find" produces exactly that set on its own. So a thin spread
# is a validation failure, not a review note.
REQUIRED_SHAPE = {
    "min_items": 40,
    "min_countries": 8,
    "min_share": {
        ("geography", "non-US"): 0.30,
        ("signal_type", "funding"): 0.25,
        ("signal_type", "leadership"): 0.20,
        ("size_band", "small"): 0.30,
    },
    "min_source_types": 3,
    "min_per_source_type": 4,
}

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

    if data.get("sealed") is not True:
        problems.append(
            "the set is not sealed: measuring against a set that is still being "
            "edited is how a benchmark ends up shaped by its own result")
    if not data.get("assembled_on"):
        problems.append("no assembled_on date, so independence cannot be dated")

    problems.extend(_shape_problems(items))

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


def _shape_problems(items: list) -> list:
    """Enforce REQUIRED_SHAPE. See the comment on it for why this is a failure
    and not a warning."""
    shape = counts({"items": items})
    total = shape["total"]
    problems = []

    if total < REQUIRED_SHAPE["min_items"]:
        problems.append(
            f"only {total} events: below {REQUIRED_SHAPE['min_items']}, too few to "
            "break down by cell")
    if len(shape["country"]) < REQUIRED_SHAPE["min_countries"]:
        problems.append(
            f"only {len(shape['country'])} countries: below "
            f"{REQUIRED_SHAPE['min_countries']}, so geography cannot be measured")

    for (group, key), share in REQUIRED_SHAPE["min_share"].items():
        got = shape[group].get(key, 0)
        if total and got / total < share:
            problems.append(
                f"{key} is {got}/{total} ({got / total:.0%}) of the set: below the "
                f"required {share:.0%}, which would flatter the result")

    kinds = {k: n for k, n in shape["source_type"].items()
             if n >= REQUIRED_SHAPE["min_per_source_type"]}
    if len(kinds) < REQUIRED_SHAPE["min_source_types"]:
        problems.append(
            f"only {len(kinds)} kinds of document carry at least "
            f"{REQUIRED_SHAPE['min_per_source_type']} events "
            f"({shape['source_type']}): a set of one document type measures one "
            "collector, not the tracker")
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

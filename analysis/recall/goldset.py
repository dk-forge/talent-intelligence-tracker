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
#
# The GEOGRAPHIC bars were added on 2026-07-30, after the first measurement made
# the original ones look generous. A set can satisfy `min_countries: 8` and
# still be the United States, western Europe and nothing else — which describes
# the feeds we already had rather than the world the tracker now claims to
# reach, so recall against it answers only "how well do we read the places we
# already read".
#
# Each geographic bar here is set at the shape of the NARROWEST set ever
# actually used (2026-07-v1: 29 countries, 12 of them carrying more than one
# event, the largest country 38% of the set, six of the project's seven regions
# carrying at least two events), so that no set already on disk is retroactively
# invalidated and every published figure stays re-derivable.
#
# The RATCHET is separate and automatic: `_ratchet_problems` measures the widest
# set already on disk and requires a new one to be within RATCHET_FLOOR of it.
# Nobody has to remember to raise a constant after assembling a wider set, and
# nothing here has to be edited when they do.
REQUIRED_SHAPE = {
    "min_items": 40,
    "min_countries": 20,
    "min_share": {
        ("geography", "non-US"): 0.30,
        ("signal_type", "funding"): 0.25,
        ("signal_type", "leadership"): 0.20,
        ("size_band", "small"): 0.30,
    },
    "min_source_types": 3,
    "min_per_source_type": 4,

    # A country carrying a single event is "an indication and not a rate" — the
    # set's own caveat. Thirty such countries look broad and measure nothing
    # about any of them, so a floor on countries carrying more than one event is
    # a floor on how much of the breadth is real.
    "min_countries_with_repeats": 8,
    "repeat_country_events": 2,

    # No single country may dominate. Without this, "widen the set" is satisfied
    # by keeping forty US events and adding one each from forty countries, and
    # the headline figure goes on being a US figure wearing a world map.
    "max_country_share": 0.45,

    # Region coverage, over the project's OWN region vocabulary
    # (pipeline.validate._region_for_country, seven regions) rather than a
    # second geography invented here. Six of seven, each carrying at least two
    # events: a set may miss one region, not two, and may not represent a region
    # with a single token event.
    "min_regions": 6,
    "min_per_region": 2,

    # `amount_disclosed: false` is a declared escape hatch, not a general one.
    # Past this share the set stops being checkable on amounts at all, and
    # "undisclosed" becomes the easy way to admit an event nobody pinned down.
    "max_undisclosed_amount_share": 0.15,
}

# How much narrower than the widest set already on disk a new one may be.
# Not 1.0: a fresh window genuinely yields a different number of reachable
# events, and a bar that demanded a strict improvement every month would be met
# by padding rather than by research. Not 0.5 either, or the ratchet ratchets
# nothing. Eight tenths lets a lean month through and refuses a retreat.
RATCHET_FLOOR = 0.8

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


def validate(data: dict, peers: list | None = None) -> list:
    """Every rule the gold set must satisfy. Returns a list of problems.

    Empty list means the file is fit to measure against. This runs in the test
    suite, so a gold set edited into an invalid state fails CI rather than
    silently producing a wrong denominator.

    `peers` is the breadth of the other sets on disk, for the ratchet. It is
    found automatically for a set that was LOADED from a file and left empty for
    one built in memory, so a unit test constructing a set by hand is judged
    against the fixed bars only and never against whatever happens to be in the
    repository that week.
    """
    problems = []
    items = data.get("items", [])

    if peers is None and data.get("_path"):
        peers = peer_breadths(data["_path"],
                              assembled_on=data.get("assembled_on"))
    if items and peers:
        problems.extend(_ratchet_problems(data, peers))

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
            # An undisclosed round is a real event, and a set that cannot admit
            # one measures only the events that came with a number. That bias
            # points straight at the markets this benchmark exists to cover: a
            # seed round in Ghana or Kazakhstan is routinely reported with no
            # figure at all. So the omission has to be DECLARED rather than
            # silently allowed — `amount_disclosed: false` is the assembler
            # saying the publisher did not state one, which is a different fact
            # from having forgotten to write it down.
            if item.get("amount_disclosed") is not False:
                problems.append(
                    f"{label}: a funding item needs amount_usd, or "
                    f"amount_disclosed=false if the publisher stated none")

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

    funding = [i for i in items if i.get("signal_type") == "funding"]
    undisclosed = [i for i in funding if not i.get("amount_usd")]
    ceiling = REQUIRED_SHAPE["max_undisclosed_amount_share"]
    if funding and len(undisclosed) / len(funding) > ceiling:
        problems.append(
            f"{len(undisclosed)}/{len(funding)} funding events have no amount "
            f"({len(undisclosed) / len(funding):.0%}): above the {ceiling:.0%} "
            "ceiling, so most of the set cannot be checked on the number")

    problems.extend(_geography_problems(shape, total))
    return problems


def breadth(shape: dict) -> dict:
    """The four numbers the ratchet compares, in one place so that the guard and
    the message can never describe a set two ways."""
    by_country = shape["country"]
    total = shape["total"] or 1
    return {
        "events": shape["total"],
        "countries": len(by_country),
        "countries_with_repeats": sum(
            1 for n in by_country.values()
            if n >= REQUIRED_SHAPE["repeat_country_events"]),
        "regions": sum(1 for key, n in regions(shape).items()
                       if key and n >= REQUIRED_SHAPE["min_per_region"]),
        "largest_country_share": (max(by_country.values()) / total
                                  if by_country else 0.0),
    }


def _ratchet_problems(data: dict, peers: list) -> list:
    """A new set may not be narrower than the widest one already on disk.

    This is what makes the geographic guard a ratchet rather than a note asking
    somebody to raise a constant. The failure it prevents is not malice: it is
    an ordinary month where the easy countries are the ones that answer, the
    next set quietly comes back at 30 countries, and the published figure rises
    because the world got smaller. Compared against the widest PEER rather than
    the previous one, so a single lean month cannot lower the bar for good.
    """
    if not peers:
        return []
    mine = breadth(counts(data))
    best = {key: max(p[key] for p in peers)
            for key in ("events", "countries", "countries_with_repeats", "regions")}
    tightest = min(p["largest_country_share"] for p in peers)

    problems = []
    labels = {
        "events": "events",
        "countries": "countries",
        "countries_with_repeats": f"countries carrying "
                                  f"{REQUIRED_SHAPE['repeat_country_events']}+ events",
        "regions": "regions",
    }
    for key, was in best.items():
        floor = RATCHET_FLOOR * was
        if mine[key] < floor:
            problems.append(
                f"{mine[key]} {labels[key]}: a set already on disk reached {was}, "
                f"and a new set may not fall below {RATCHET_FLOOR:.0%} of the "
                f"widest one ({floor:.0f}). Widening is not supposed to be "
                f"reversible")
    if mine["largest_country_share"] > tightest + (1 - RATCHET_FLOOR):
        problems.append(
            f"the largest country is {mine['largest_country_share']:.0%} of this "
            f"set against {tightest:.0%} in the most evenly spread set on disk: "
            f"a concentration the ratchet does not allow back")
    return problems


def peer_breadths(path: str, directory: str | None = None,
                  assembled_on: str | None = None) -> list:
    """The breadth of every set on disk assembled BEFORE this one.

    Strictly earlier, because a ratchet that looked at later sets would reach
    backwards and invalidate the very history it exists to protect: the day the
    widened 169-event set landed, the 89-event set it superseded would have
    stopped validating and its published 9.0% would have become underivable.

    Separated from `validate` so a caller with no filesystem — every unit test
    that builds a set in memory — is unaffected.
    """
    directory = directory or os.path.dirname(os.path.abspath(path))
    out = []
    for other in all_paths(directory):
        if os.path.abspath(other) == os.path.abspath(path):
            continue
        try:
            with open(other, encoding="utf-8") as handle:
                peer = json.load(handle)
        except (OSError, ValueError):
            continue
        when = str(peer.get("assembled_on") or "")
        if assembled_on and not (when and when < assembled_on):
            continue
        try:
            out.append(breadth(counts(peer)))
        except KeyError:
            continue
    return out


def regions(shape: dict) -> dict:
    """Events per region, over the project's own region vocabulary.

    Imported lazily and defensively. This module is the one thing that has to
    keep working when the pipeline does not: `measure_recall.py --check` is the
    step that runs before the API is touched, and a validator that cannot run
    because an unrelated import broke would take the whole measurement with it.
    A country the vocabulary cannot place lands under `None`, which is worth
    seeing — it means our own geography cannot admit a market the benchmark
    covers — but it is a finding rather than an invalid set.
    """
    try:
        from pipeline.validate import _region_for_country
    except Exception:                              # pragma: no cover - import guard
        return {}
    out = {}
    for iso2, count in shape["country"].items():
        key = _region_for_country(iso2)
        out[key] = out.get(key, 0) + count
    return out


def _geography_problems(shape: dict, total: int) -> list:
    """Is the set actually global, or global-looking?

    See REQUIRED_SHAPE for where each bar comes from. All three of these can be
    satisfied on paper by a set that is really one market, which is why they are
    three rules and not one.
    """
    problems = []
    by_country = shape["country"]

    repeats = [c for c, n in by_country.items()
               if n >= REQUIRED_SHAPE["repeat_country_events"]]
    if len(repeats) < REQUIRED_SHAPE["min_countries_with_repeats"]:
        problems.append(
            f"only {len(repeats)} countries carry "
            f"{REQUIRED_SHAPE['repeat_country_events']}+ events: below "
            f"{REQUIRED_SHAPE['min_countries_with_repeats']}, so most of the "
            "breadth is single events that cannot measure a country")

    if total and by_country:
        biggest, count = max(by_country.items(), key=lambda kv: (kv[1], kv[0]))
        share = count / total
        if share > REQUIRED_SHAPE["max_country_share"]:
            problems.append(
                f"{biggest} is {count}/{total} ({share:.0%}) of the set: above "
                f"the {REQUIRED_SHAPE['max_country_share']:.0%} ceiling, so the "
                "headline figure would be that one country's figure")

    per_region = {k: n for k, n in regions(shape).items()
                  if k and n >= REQUIRED_SHAPE["min_per_region"]}
    if per_region and len(per_region) < REQUIRED_SHAPE["min_regions"]:
        problems.append(
            f"only {len(per_region)} regions carry "
            f"{REQUIRED_SHAPE['min_per_region']}+ events ({per_region}): below "
            f"{REQUIRED_SHAPE['min_regions']}, so whole regions the tracker "
            "claims to reach go unmeasured")
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

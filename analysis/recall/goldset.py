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

from analysis.recall.stats import widest_possible_width

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

    # Which dimension the spread guards above are measured over, and whether
    # the project's seven-region vocabulary applies. Named here rather than
    # assumed, because a second family measures spread over metros inside one
    # country and every rule below has to know which it is looking at.
    "spread_key": "country",
    "spread_label": "countries",
    "spread_singular": "country",
    "regional": True,
    "single_country": None,
}

# The same anti-flattery job, for a reference set that is the United States on
# purpose.
#
# WHY A SECOND SHAPE AND NOT A `country == US` FILTER ON THE FIRST. Every
# geographic bar above exists to stop a set being quietly rebuilt out of large
# US filings. Point those bars at a US set and they all fail by construction:
# 20 countries, six regions, no country over 45%. Deleting them for the US
# family would delete the guard, so the guard is REPLACED with the same idea
# measured over the dimension that actually varies inside one country, which is
# the hiring market.
#
# The failure being guarded against is specific, and it is the easy one to fall
# into here. This tracker's US spine is SEC EDGAR: 8-K officer changes,
# pay-versus-performance tables and Form D. Every one of those is a large or
# listed employer filing a mandatory document. A US reference set assembled
# from "what is easy to enumerate in the United States" is a set of SEC
# filings, we would score well against it, and the number would say nothing
# about the startup and hiring markets it is supposed to describe.
#
# That is not a hypothetical. Assembling the first US set, three of the eight
# research passes independently reached for EDGAR full-text search, because it
# is the only chronologically enumerable index of US corporate events that is
# free and not an aggregator. All three came back over 90% exchange-listed
# filings and all three were discarded. `max_source_type_share` is the bar that
# makes that discard mechanical instead of a judgement somebody has to remember
# to make.
US_REQUIRED_SHAPE = {
    # ANCHORED TO A NUMBER THE OWNER ALREADY READS, not to a round figure. The
    # sibling layoff tracker publishes recall against 57 held-out SEC filings,
    # which resolves to a Wilson interval about 25 points wide. A US set that
    # resolved much worse than that would be a second number in the same house
    # that cannot be read the same way. A proportion on n events has a
    # worst-case width of roughly 2 * 0.98 / sqrt(n), so 45 events is where 28
    # points lands, and that is the floor.
    #
    # Stated plainly because it is the kind of thing that gets quietly forgotten:
    # the first set assembled under this bar came in at 51 events and 26.5
    # points, so there is real but not generous headroom. A future set that
    # scrapes in at 45 is admissible and noticeably blunter, and the page prints
    # the interval so a reader can see which they are looking at.
    "min_items": 45,
    "max_interval_width": 0.28,

    "min_countries": 1,
    "min_share": {
        # The same floor the worldwide set carries, for the same reason and not
        # a higher one. A higher bar was drafted here and then withdrawn: the
        # research passes showed the small-employer share is capped by publisher
        # behaviour rather than by effort, since small rounds are routinely
        # written up in 300 words that never state where the company sits, and
        # a row with no verifiable metro cannot enter a set whose cells ARE
        # metros. A bar nothing honest can clear is a bar that gets edited on
        # the day it fires. The under-representation is real and is declared in
        # the set's own caveats instead of being papered over by a number.
        ("size_band", "small"): 0.30,
    },

    # TWO document types, eight events each, and no type over half the set.
    #
    # The worldwide bar is three types at four events. It is relaxed to two and
    # tightened to eight here on purpose. Inside the United States the reachable
    # original publishers for a privately held employer's event are the company's
    # own announcement and the trade press; a general-audience daily covers the
    # large ones and a filing exists only for the listed ones. Demanding four
    # kinds would be satisfied most easily by going back to EDGAR, which is the
    # failure this whole shape exists to refuse. `max_source_type_share` is what
    # carries the real weight: no single kind of document may be a majority, so
    # a set cannot be one collector wearing four labels.
    "min_source_types": 2,
    "min_per_source_type": 8,
    "max_source_type_share": 0.50,

    # Metro is this family's spread dimension. Four cells, each carrying at
    # least eight events. Eight is not enough to call a metro cell a rate and it
    # is not meant to be: it is the floor at which a cell is worth printing
    # beside its interval, which at eight events is about fifty points wide and
    # says so.
    "min_countries_with_repeats": 4,
    "repeat_country_events": 8,
    "max_country_share": 0.45,

    "min_regions": 0,
    "min_per_region": 0,

    "max_undisclosed_amount_share": 0.15,

    "spread_key": "metro",
    "spread_label": "metros",
    "spread_singular": "metro",
    "regional": False,
    # Every item must be this country. A US reference set that quietly grew a
    # Toronto row is measuring something else under a US heading.
    "single_country": "US",
    # And every item must be a kind of signal the set DECLARES it covers. The
    # first US set covers funding alone, because US leadership events at private
    # employers could not be enumerated from original sources without either a
    # commercial people-data service or EDGAR, and both are refused. Scope that
    # narrow has to be declared in the file and enforced against the items, or
    # a later set grows a half-measured second signal type and the headline
    # silently changes population.
    "declared_signal_types": True,
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


def shape_for(data: dict) -> dict:
    """Which set of bars this reference set is judged against.

    Declared BY THE FILE, in its own `family` key, and never inferred from what
    is inside it. Inference would mean a worldwide set that happened to come
    back all-US one month quietly got judged by the US bars and passed, which is
    precisely the retreat REQUIRED_SHAPE exists to refuse.
    """
    return SHAPES.get(str(data.get("family") or "world"), REQUIRED_SHAPE)


def validate(data: dict, peers: list | None = None, shape: dict | None = None) -> list:
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
    shape = shape_for(data) if shape is None else shape

    if peers is None and data.get("_path"):
        peers = peer_breadths(data["_path"],
                              assembled_on=data.get("assembled_on"),
                              shape=shape)
    if items and peers:
        problems.extend(_ratchet_problems(data, peers, shape))

    if not items:
        return ["gold set is empty"]

    if data.get("sealed") is not True:
        problems.append(
            "the set is not sealed: measuring against a set that is still being "
            "edited is how a benchmark ends up shaped by its own result")
    if not data.get("assembled_on"):
        problems.append("no assembled_on date, so independence cannot be dated")

    # What this set claims to cover, enforced against what is in it. A set that
    # measures one signal type has to say so, and then may not quietly acquire
    # a second: a half-covered signal type dilutes the headline while looking
    # like added breadth.
    declared = data.get("signal_types")
    if shape.get("declared_signal_types"):
        if not declared:
            problems.append(
                "no signal_types declared: a set that covers some of what the "
                "tracker collects must name which, or the headline is a claim "
                "about a population nobody wrote down")
        else:
            stray = sorted({i.get("signal_type") for i in items} - set(declared))
            if stray:
                problems.append(
                    f"items carry signal types the set does not declare: "
                    f"{', '.join(stray)}. Declared: {', '.join(declared)}")

    problems.extend(_shape_problems(items, shape))

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
        only = shape.get("single_country")
        if only and country != only:
            problems.append(
                f"{label}: country is {country!r} in a set declared as {only} only, "
                f"so it is measuring a different population under this heading")
        if shape.get("spread_key") == "metro" and not item.get("metro"):
            problems.append(
                f"{label}: missing metro, which is this set's whole cell structure")

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


def _shape_problems(items: list, bars: dict = REQUIRED_SHAPE) -> list:
    """Enforce the family's shape. See the comment on REQUIRED_SHAPE for why
    this is a failure and not a warning."""
    shape = counts({"items": items})
    total = shape["total"]
    spread_key = bars.get("spread_key", "country")
    spread_label = bars.get("spread_label", "countries")
    by_spread = shape[spread_key]
    problems = []

    if total < bars["min_items"]:
        problems.append(
            f"only {total} events: below {bars['min_items']}, too few to "
            "break down by cell")

    # The count bar and the interval bar say the same thing two ways on
    # purpose: the count is what an assembler works to, and the width is what
    # the number has to survive being read as. A family that does not declare a
    # width is unchanged.
    ceiling = bars.get("max_interval_width")
    if ceiling is not None and total:
        width = widest_possible_width(total)
        if width > ceiling:
            problems.append(
                f"{total} events give a worst-case 95% interval "
                f"{width * 100:.1f} points wide, above the "
                f"{ceiling * 100:.0f}-point ceiling: a rate this uncertain "
                f"cannot be broken down by cell and should not be published as "
                f"a figure")

    if len(by_spread) < bars["min_countries"]:
        problems.append(
            f"only {len(by_spread)} {spread_label}: below "
            f"{bars['min_countries']}, so spread cannot be measured")

    for (group, key), share in bars["min_share"].items():
        got = shape[group].get(key, 0)
        if total and got / total < share:
            problems.append(
                f"{key} is {got}/{total} ({got / total:.0%}) of the set: below the "
                f"required {share:.0%}, which would flatter the result")

    kinds = {k: n for k, n in shape["source_type"].items()
             if n >= bars["min_per_source_type"]}
    if len(kinds) < bars["min_source_types"]:
        problems.append(
            f"only {len(kinds)} kinds of document carry at least "
            f"{bars['min_per_source_type']} events "
            f"({shape['source_type']}): a set of one document type measures one "
            "collector, not the tracker")

    # The sharp instrument. A single document type past half the set means one
    # collector's own supply is most of the denominator, whatever the type
    # count says.
    type_ceiling = bars.get("max_source_type_share")
    if type_ceiling is not None and total and shape["source_type"]:
        kind, count = max(shape["source_type"].items(),
                          key=lambda kv: (kv[1], kv[0]))
        if count / total > type_ceiling:
            problems.append(
                f"{kind} is {count}/{total} ({count / total:.0%}) of the set: "
                f"above the {type_ceiling:.0%} ceiling, so most of the "
                f"denominator is one kind of document and the figure is that "
                f"collector's figure")

    funding = [i for i in items if i.get("signal_type") == "funding"]
    undisclosed = [i for i in funding if not i.get("amount_usd")]
    undisclosed_ceiling = bars["max_undisclosed_amount_share"]
    if funding and len(undisclosed) / len(funding) > undisclosed_ceiling:
        problems.append(
            f"{len(undisclosed)}/{len(funding)} funding events have no amount "
            f"({len(undisclosed) / len(funding):.0%}): above the "
            f"{undisclosed_ceiling:.0%} ceiling, so most of the set cannot be "
            "checked on the number")

    problems.extend(_geography_problems(shape, total, bars))
    return problems


def breadth(shape: dict, bars: dict = REQUIRED_SHAPE) -> dict:
    """The four numbers the ratchet compares, in one place so that the guard and
    the message can never describe a set two ways.

    The keys keep their worldwide names across both families. They are the
    ratchet's own vocabulary rather than a claim about geography, and renaming
    them per family would mean a stored breadth could not be compared with the
    one beside it.
    """
    by_spread = shape[bars.get("spread_key", "country")]
    total = shape["total"] or 1
    return {
        "events": shape["total"],
        "countries": len(by_spread),
        "countries_with_repeats": sum(
            1 for n in by_spread.values()
            if n >= bars["repeat_country_events"]),
        "regions": (sum(1 for key, n in regions(shape).items()
                        if key and n >= bars["min_per_region"])
                    if bars.get("regional") else 0),
        "largest_country_share": (max(by_spread.values()) / total
                                  if by_spread else 0.0),
    }


def _ratchet_problems(data: dict, peers: list, bars: dict = REQUIRED_SHAPE) -> list:
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
    mine = breadth(counts(data), bars)
    best = {key: max(p[key] for p in peers)
            for key in ("events", "countries", "countries_with_repeats", "regions")}
    tightest = min(p["largest_country_share"] for p in peers)

    spread_label = bars.get("spread_label", "countries")
    problems = []
    labels = {
        "events": "events",
        "countries": spread_label,
        "countries_with_repeats": f"{spread_label} carrying "
                                  f"{bars['repeat_country_events']}+ events",
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
            f"the largest {bars.get('spread_singular', 'country')} is "
            f"{mine['largest_country_share']:.0%} of this "
            f"set against {tightest:.0%} in the most evenly spread set on disk: "
            f"a concentration the ratchet does not allow back")
    return problems


def peer_breadths(path: str, directory: str | None = None,
                  assembled_on: str | None = None,
                  shape: dict = REQUIRED_SHAPE) -> list:
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
            out.append(breadth(counts(peer), shape))
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


def _geography_problems(shape: dict, total: int,
                        bars: dict = REQUIRED_SHAPE) -> list:
    """Is the set actually spread, or spread-looking?

    See REQUIRED_SHAPE for where each bar comes from. All three of these can be
    satisfied on paper by a set that is really one market, which is why they are
    three rules and not one.

    Worldwide the spread dimension is the country and the third rule is the
    project's region vocabulary. Inside one country it is the metro and there is
    no third rule, so the region bar is set to zero rather than reinterpreted:
    a guard that quietly changes meaning between families is a guard nobody can
    read off the constant.
    """
    problems = []
    spread_key = bars.get("spread_key", "country")
    spread_label = bars.get("spread_label", "countries")
    singular = bars.get("spread_singular", "country")
    by_spread = shape[spread_key]

    repeats = [c for c, n in by_spread.items()
               if n >= bars["repeat_country_events"]]
    if len(repeats) < bars["min_countries_with_repeats"]:
        problems.append(
            f"only {len(repeats)} {spread_label} carry "
            f"{bars['repeat_country_events']}+ events: below "
            f"{bars['min_countries_with_repeats']}, so most of the "
            f"breadth is thin cells that cannot measure a {singular}")

    if total and by_spread:
        biggest, count = max(by_spread.items(), key=lambda kv: (kv[1], kv[0]))
        share = count / total
        if share > bars["max_country_share"]:
            problems.append(
                f"{biggest} is {count}/{total} ({share:.0%}) of the set: above "
                f"the {bars['max_country_share']:.0%} ceiling, so the "
                f"headline figure would be that one {singular}'s figure")

    if bars.get("regional"):
        per_region = {k: n for k, n in regions(shape).items()
                      if k and n >= bars["min_per_region"]}
        if per_region and len(per_region) < bars["min_regions"]:
            problems.append(
                f"only {len(per_region)} regions carry "
                f"{bars['min_per_region']}+ events ({per_region}): below "
                f"{bars['min_regions']}, so whole regions the tracker "
                "claims to reach go unmeasured")
    return problems


def counts(data: dict) -> dict:
    """Shape of the gold set, for the method section of the public page.

    `metro` is counted only where an item declares one, so a worldwide set is
    unaffected and its `metro` bucket stays empty rather than growing a column
    of nulls.
    """
    items = data.get("items", [])
    out = {"total": len(items), "signal_type": {}, "geography": {},
           "country": {}, "source_type": {}, "size_band": {}, "metro": {}}
    for item in items:
        for key, value in (
            ("signal_type", item["signal_type"]),
            ("geography", "US" if item["country"] == "US" else "non-US"),
            ("country", item["country"]),
            ("source_type", item["source_type"]),
            ("size_band", item["size_band"]),
            ("metro", item.get("metro")),
        ):
            if value is None:
                continue
            out[key][value] = out[key].get(value, 0) + 1
    return out


# Declared after both shapes exist, so `shape_for` can route a file to its bars
# by the family it names. Anything unrecognised falls back to the worldwide
# bars, which are the stricter ones: an unknown family must not be a way to be
# judged by nothing.
SHAPES = {"world": REQUIRED_SHAPE, "us": US_REQUIRED_SHAPE}

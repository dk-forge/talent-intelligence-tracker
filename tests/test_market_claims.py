"""What `source_registry.MARKETS` actually controls, pinned rather than believed.

Two false beliefs about this tuple have already shaped decisions in this repo:
that it drives the Google News locale rotation, and that it widens the
prefilter's geography gate. It does neither. It controls the PUBLIC COVERAGE
CLAIM, and it feeds the segment matrix — which is what makes the sweep budget the
real cost of adding a market, and $0 the real model cost.

These tests exist so the next widening is sized against the constraint that binds
instead of against the one that sounds like it should.
"""

from __future__ import annotations

import csv
import math

import source_registry as registry
from pipeline import prefilter


def test_a_market_is_a_public_claim_and_that_is_what_it_costs():
    """Every market renders on the sources page with a claim beside it."""
    manifest = registry.coverage_manifest()
    assert len(manifest) == len(registry.MARKETS)
    for entry in manifest:
        assert entry["public_claim"] in registry.TIER_PUBLIC_CLAIM.values()
        assert entry["iso2"] and entry["country"]


def test_markets_does_not_drive_the_locale_rotation():
    """The rotation is an independent tuple, so adding a market fetches nothing.

    This is the whole reason a widening of MARKETS costs no gate time and no model
    spend: the editions were already being swept. If this coupling is ever
    introduced, the cost model in the MARKETS comment stops being true and the
    comment has to change in the same commit.
    """
    import run_collect

    source = registry.__file__
    with open(source) as fh:
        text = fh.read()
    locales_block = text.split("GOOGLE_NEWS_LOCALES = (", 1)[1].split(")\n", 1)[0]
    assert "MARKETS" not in locales_block

    before = list(run_collect.build_locales(0))
    assert before, "the rotation produced nothing"
    # Every locale comes from GOOGLE_NEWS_LOCALES or the anchor, never from a
    # market's iso2.
    allowed = set(registry.GOOGLE_NEWS_LOCALES) | {registry.GOOGLE_NEWS_ANCHOR}
    assert set(before) <= allowed


def test_markets_does_not_widen_the_prefilters_geography_gate():
    """A market's name is not what makes its stories survive the free filter.

    `_geography_terms` is built from the country and city VOCABULARY. A headline
    naming a country nobody has listed as a market still passes; a market added
    without its country in the vocabulary would still be dropped.
    """
    assert prefilter.has_covered_geography(
        "Employer to create 300 new jobs in Ulaanbaatar, Mongolia")
    listed = {m.name for m in registry.MARKETS}
    assert "Mongolia" not in listed, (
        "pick a country that is NOT a market for this test to mean anything")


def test_the_segment_budget_is_the_real_ceiling_on_this_tuple():
    """Adding a market spends a sweep slot, and the slots are finite.

    Recomputed here rather than asserted as a number, so the arithmetic in the
    MARKETS comment cannot drift away from the code. The margin is deliberately
    allowed to be zero: the point is that the next addition must be a decision
    about what to remove or about raising the budget, not an accident.

    The ceiling came off `recency_window_days(...)` until 2026-08-01, which tied
    it to the LOCALE rotation and meant shortening that rotation silently cut
    twelve markets. It is `SEGMENT_SWEEP_BUDGET_DAYS` now, same number, chosen
    rather than inherited; see the note beside it.
    """
    from run_collect import RUNS_PER_DAY, SEGMENTS_PER_RUN

    segments = registry.build_segments()
    budget = registry.SEGMENT_SWEEP_BUDGET_DAYS
    ceiling = budget * SEGMENTS_PER_RUN * RUNS_PER_DAY
    assert len(segments) <= ceiling, (
        f"{len(segments)} segments against a ceiling of {ceiling}. Give a market "
        f"local `terms` only by removing others, or raise the segment budget "
        f"deliberately.")
    # And the market count is exactly the segment cost, so the comment's
    # "name plus one per term" accounting is real.
    expected = sum(1 + len(m.terms) for m in registry.MARKETS)
    assert len(segments) == expected
    assert math.ceil(len(segments) / SEGMENTS_PER_RUN / RUNS_PER_DAY) <= budget


def test_every_claimed_market_has_the_evidence_its_tier_needs():
    """Two conditions, and both were load-bearing in choosing the 2026-07-29 twelve.

    A `discovery_only` market claims `live_sources=("google_news",)`, so it needs
    an edition in the rotation for that claim to be true — otherwise the claim is
    that we monitor news somewhere no query asks about. And a country read through
    one feed is a single point of failure, which is why Saudi Arabia's ar:SA
    edition keeps sweeping without SA being claimed.
    """
    rotated = ({country for _, country in registry.GOOGLE_NEWS_LOCALES}
               | {registry.GOOGLE_NEWS_ANCHOR[1]})
    with registry.CATALOGUE_CSV.open(newline="") as fh:
        wired: dict[str, int] = {}
        for row in csv.DictReader(fh):
            if (row.get("rss") or "").startswith("http"):
                country = (row.get("country") or "").strip()
                wired[country] = wired.get(country, 0) + 1

    for market in registry.MARKETS:
        if market.live_sources != ("google_news",):
            continue      # a structured market earns its listing another way
        assert market.iso2 in rotated or wired.get(market.name, 0) >= 2, (
            f"{market.iso2} claims google_news discovery with no edition in the "
            f"rotation and fewer than two wired feeds")


def test_the_countries_the_goldset_scored_zero_on_are_claimed_or_excused():
    """A country the sealed gold set scored us ZERO on is an instruction.

    Any zero-country left off MARKETS must be off it for one of the two stated
    reasons — no language pack for its edition, or fewer than two wired feeds —
    and not merely by having been forgotten. `data/recall_worklist.json` is the
    authority; a run that has not produced one skips this.
    """
    import json
    from pathlib import Path

    path = Path(registry.__file__).parent / "data" / "recall_worklist.json"
    if not path.exists():
        return
    zero = [e["key"] for e in json.loads(path.read_text()).get("zero_countries", [])]
    if not zero:
        return

    claimed = {m.iso2 for m in registry.MARKETS}
    rotated = ({country for _, country in registry.GOOGLE_NEWS_LOCALES}
               | {registry.GOOGLE_NEWS_ANCHOR[1]})
    with registry.CATALOGUE_CSV.open(newline="") as fh:
        wired: dict[str, int] = {}
        for row in csv.DictReader(fh):
            if (row.get("rss") or "").startswith("http"):
                country = (row.get("country") or "").strip()
                wired[country] = wired.get(country, 0) + 1
    from pipeline import vocab

    unexplained = []
    for iso2 in zero:
        if iso2 in claimed or iso2 in BUDGET_DEFERRED:
            continue
        name = vocab.COUNTRY_NAMES.get(iso2, "")
        no_edition = iso2 not in rotated
        too_thin = wired.get(name, 0) < 2
        if not (no_edition or too_thin):
            unexplained.append(iso2)
    assert not unexplained, (
        f"{unexplained} scored zero on the gold set, have a swept edition and at "
        f"least two wired feeds, and are still not claimed. Claim them, or — if "
        f"the segment budget is full, which is a legitimate reason — name them in "
        f"BUDGET_DEFERRED above with the reason. Forgetting is not a reason.")


#: Zero-scoring countries with an edition and wired feeds that are NOT claimed
#: because `build_segments()` is at its ceiling. It was empty as of 2026-07-29:
#: the twelve added that day spent the budget exactly, and every remaining
#: zero-country was excluded by a language pack that does not exist (CN, NO, FI)
#: or by having one wired feed (SA). An entry here is a decision with a reason
#: beside it; the test above exists so it cannot be an omission instead.
#:
#: It stopped being empty on 2026-07-30, and that is the loop working rather
#: than failing. The gold set went from 29 countries to 79, the zero list from
#: 27 to 66, and seventeen of the new zeros have BOTH a swept edition and two or
#: more wired feeds — so the guard correctly reports that reach is no longer
#: their excuse. They are still not CLAIMED, because a market claim here is
#: earned and not asserted: it needs a working connector, a health check and a
#: passing test, and `build_segments()` has no room for seventeen more without
#: the segment budget being raised, which is the owner's call and not a test
#: file's.
#:
#: The right state to be in today and the wrong one to stay in. The instruction
#: attached to it is data/recall_worklist.json, which names all 66 and orders
#: them by how much of the set each accounts for.
_WIDENED = ("deferred 2026-07-30: newly measured at zero by the widened gold set "
            "(79 countries, up from 29). Has an edition and 2+ wired feeds, so "
            "reach is not the excuse — build_segments() is at its ceiling and "
            "raising the segment budget is the owner's decision")
BUDGET_DEFERRED: dict[str, str] = {
    iso2: _WIDENED for iso2 in (
        # Africa: the region that went from 1 gold event to 20 and holds none.
        "KE", "GH", "NG", "MA", "SN",
        # South-east Asia and greater China: 12 new events, no holds.
        "VN", "ID", "MY", "PH", "BD", "HK",
        # Latin America: 13 events, one hold, and it is Brazil.
        "CL", "UY", "PE",
        # Europe and the near abroad.
        "PL", "AT", "TR",
    )
}

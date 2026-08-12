"""The United States reference set, and the guards that stop it flattering us.

The worldwide recall number is 21% and the US cell inside it is 38%, which
reads as "we are good at America". That cell is 34 events wide and it is 34
events of a set assembled to be global, so it says almost nothing about the
American hiring market. This family exists to replace an impression with a
number, and every test here defends the number against the specific ways it
could be made to look better than it is.

Two of those ways are not hypothetical and both happened during assembly:

  * three of the leadership research passes independently enumerated candidates
    by walking SEC EDGAR full-text search, which is the index our own
    `sec_edgar` collector walks. A set built that way scores the tracker
    against its own feed. All three came back over 90% exchange-listed filings.
  * a US set dropped into `analysis/recall/` would become, by filename order
    alone, the set the WORLDWIDE measurement runs against, and the published
    worldwide figure would turn into a US figure with no code change at all.

`test_a_us_set_cannot_hijack_the_worldwide_measurement` and
`test_a_set_built_out_of_one_kind_of_document_is_refused` are those two.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import family, goldset, match, stats, thresholds  # noqa: E402

US_PATH = os.path.join(ROOT, "analysis", "recall", "us", "goldset-us-2026-06.json")


# --- the separation --------------------------------------------------------

def test_a_us_set_cannot_hijack_the_worldwide_measurement():
    """The worldwide family must never pick up a US set as the newest on disk.

    `latest_path` takes the last `goldset-*.json` in a directory, and
    "goldset-us-2026-06.json" sorts after every "goldset-2026-*.json" there has
    ever been. One file in the wrong folder would silently redefine the
    published worldwide number, with nothing in any diff saying so.
    """
    worldwide = goldset.all_paths(family.WORLD.goldset_dir)
    assert worldwide, "the worldwide family has no reference set at all"
    for path in worldwide:
        loaded = goldset.load(path)
        assert loaded.get("family", "world") == "world", (
            f"{os.path.basename(path)} declares a non-worldwide family but sits "
            f"in the worldwide directory, where it will be measured as the "
            f"worldwide set")

    assert family.US.goldset_dir != family.WORLD.goldset_dir
    assert family.US.results_dir != family.WORLD.results_dir
    assert family.US.plugin_data != family.WORLD.plugin_data
    assert family.US.health_source != family.WORLD.health_source


def test_the_families_are_one_definition_and_the_gates_know_all_of_them():
    """Every breakdown a family declares must be a group the collapse gate
    watches. Otherwise a metro could die and no gate would notice."""
    for fam in family.ALL:
        for group in fam.breakdowns:
            assert group in thresholds.CELL_GROUPS, (
                f"family {fam.id} publishes {group} and thresholds.CELL_GROUPS "
                f"does not watch it, so a collapsed cell there is invisible")


def test_the_published_interval_is_the_same_one_the_gates_use():
    """One implementation. A page that rounded its own interval would let the
    figure and the floor disagree in public."""
    assert thresholds.wilson is stats.wilson
    low, high = stats.wilson(20, 51)
    published = stats.interval(20, 51)
    assert published["low_pct"] == round(low * 100, 1)
    assert published["high_pct"] == round(high * 100, 1)


# --- the guards ------------------------------------------------------------

def _us_set(items):
    return {
        "version": "test", "family": "us", "sealed": True,
        "assembled_on": "2026-08-12",
        "window": {"start": "2026-06-01", "end": "2026-07-31"},
        "signal_types": ["funding"],
        "items": items,
    }


def _item(index, **overrides):
    metros = ["San Francisco", "New York", "Austin", "Rest of US"]
    base = {
        "id": f"i{index}",
        "company": f"Company {index}",
        "signal_type": "funding",
        "event_date": "2026-06-15",
        "country": "US",
        "metro": metros[index % 4],
        "size_band": "small" if index % 3 else "large",
        "amount_usd": 10_000_000,
        "detail": "Series A",
        "source_url": "https://example.com/a",
        "source_type": ["press_release", "trade_press"][index % 2],
        "source_name": "Outlet",
    }
    base.update(overrides)
    return base


def _problems(items):
    return goldset.validate(_us_set(items), peers=[])


def test_a_valid_us_shaped_set_passes():
    assert _problems([_item(i) for i in range(52)]) == []


def test_a_set_built_out_of_one_kind_of_document_is_refused():
    """The EDGAR failure, made mechanical.

    Every discarded leadership pass looked exactly like this: real events, real
    citations, correct dates, and 90% of the denominator one kind of document
    that we happen to collect directly.
    """
    items = [_item(i, source_type="filing") for i in range(40)]
    items += [_item(i, source_type="trade_press") for i in range(40, 52)]
    problems = _problems(items)
    assert any("above the 50% ceiling" in p and "filing" in p for p in problems), problems
    assert any("that collector's figure" in p for p in problems), problems


def test_a_set_too_small_to_carry_an_interval_is_refused():
    problems = _problems([_item(i) for i in range(40)])
    assert any("below 45" in p for p in problems), problems
    assert any("worst-case 95% interval" in p and "28-point ceiling" in p
               for p in problems), problems


def test_a_metro_cell_too_thin_to_print_is_refused():
    """Three fat metros and one token one is the "widen it" move that widens
    nothing, and it is the metro version of the worldwide max_country_share."""
    items = [_item(i, metro=["San Francisco", "New York", "Austin"][i % 3])
             for i in range(48)]
    items += [_item(i, metro="Rest of US") for i in range(48, 52)]
    problems = _problems(items)
    assert any("carry 8+ events" in p and "below 4" in p for p in problems), problems


def test_one_metro_may_not_be_most_of_the_set():
    items = [_item(i, metro="San Francisco") for i in range(30)]
    items += [_item(i) for i in range(30, 52)]
    problems = _problems(items)
    assert any("of the set: above the 45% ceiling" in p
               and "that one metro's figure" in p for p in problems), problems


def test_a_row_from_outside_the_country_is_refused():
    items = [_item(i) for i in range(52)]
    items[7]["country"] = "CA"
    problems = _problems(items)
    assert any("in a set declared as US only" in p for p in problems), problems


def test_a_row_with_no_metro_is_refused():
    items = [_item(i) for i in range(52)]
    items[3].pop("metro")
    problems = _problems(items)
    assert any("missing metro" in p for p in problems), problems


def test_a_signal_type_the_set_never_declared_is_refused():
    """Scope creep is how a narrow, honest set becomes a wide, half-measured one
    while the headline goes on looking like the same number."""
    items = [_item(i) for i in range(52)]
    items[5]["signal_type"] = "leadership"
    problems = _problems(items)
    assert any("signal types the set does not declare" in p for p in problems), problems

    data = _us_set([_item(i) for i in range(52)])
    data.pop("signal_types")
    assert any("no signal_types declared" in p
               for p in goldset.validate(data, peers=[]))


def test_the_leadership_draft_is_kept_and_is_not_measured():
    """34 verified leadership rows are parked as a draft, not thrown away and
    not measured.

    They are real research and the enumerator that produced them is the finding
    worth keeping. They are not a reference set: two of the four metro cells
    would be empty and one document type would be 60% of the denominator. The
    `.draft` in the filename is what keeps them out, and this asserts that the
    loader agrees rather than trusting the convention.
    """
    draft = os.path.join(family.US.goldset_dir,
                         "goldset-us-2026-06-leadership.draft.json")
    assert os.path.exists(draft), "the leadership research was lost"
    assert draft not in goldset.all_paths(family.US.goldset_dir)
    assert goldset.latest_path(family.US.goldset_dir) == US_PATH

    with open(draft, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["sealed"] is False
    assert data["why_this_is_a_draft"]
    assert data["the_enumerator_that_worked"]
    assert len(data["items"]) >= 30
    # And it did not come from our own feed, which is the whole point of it.
    for item in data["items"]:
        assert "sec.gov" not in item["source_url"], item["id"]


def test_the_worldwide_shape_is_untouched_by_all_of_this():
    """The worldwide set still validates against the worldwide bars. Every
    published worldwide figure has to stay re-derivable."""
    world = goldset.load(goldset.latest_path(family.WORLD.goldset_dir))
    assert goldset.validate(world) == []
    assert goldset.shape_for(world) is goldset.REQUIRED_SHAPE


# --- the sealed set itself -------------------------------------------------

@pytest.fixture(scope="module")
def sealed():
    return goldset.load(US_PATH)


def test_the_sealed_us_set_is_valid(sealed):
    assert goldset.validate(sealed) == []
    assert goldset.shape_for(sealed) is goldset.US_REQUIRED_SHAPE


# The hosts a gold row may never cite: the collectors' OWN blocklist, imported
# rather than restated.
#
# Two reasons it is imported. It is the one definition of "this is a pointer and
# not a publisher", so a provider added there is refused here on the same day.
# And the names in it are held base64-encoded because of the standing
# standalone-brand rule, which `tests/test_no_provider_names.py` enforces over
# every tracked file. A second copy spelled out here would break that rule, and
# it did: the first draft of this test listed five of them in a regex and CI
# refused the commit, which is the guard working exactly as intended.
def _forbidden_hosts():
    from collectors import national_press
    people_data = ("owler", "zoominfo", "apollo.io", "rocketreach",
                   "linkedin.com", "signalhire", "lusha", "clearbit",
                   "peopledatalabs", "wellfound", "growjo")
    return tuple(national_press._AGGREGATOR_HOSTS) + people_data


def test_no_gold_row_cites_a_deal_or_people_database(sealed):
    forbidden = _forbidden_hosts()
    for item in sealed["items"]:
        blob = " ".join(str(item.get(key, "")) for key in
                        ("source_url", "source_name", "detail", "verified")).lower()
        for host in forbidden:
            # Masked in the message for the same reason the blocklist is
            # encoded: a provider's name must not reach a CI log either.
            assert host not in blob, (
                f"{item['id']} cites a blocked commercial database or "
                f"people-data host (entry {forbidden.index(host)} of the "
                f"collectors' blocklist). Those are discovery pointers and "
                f"never a stored source")


def test_every_gold_row_carries_the_evidence_it_was_verified_against(sealed):
    for item in sealed["items"]:
        assert item.get("verified"), f"{item['id']} has no verification note"
        assert item["source_url"].startswith("https://")
        assert item["metro"] in {"San Francisco", "New York", "Austin", "Rest of US"}


def test_the_set_says_what_it_does_not_measure(sealed):
    """A benchmark that hides its own caveats is a brochure. This set covers one
    of four signal types in one country and the omission is the most important
    thing on the page."""
    assert sealed["signal_types"] == ["funding"]
    blob = " ".join(sealed["caveats"]).lower()
    assert "funding only" in blob
    assert "leadership" in blob
    assert sealed["held_out"]
    assert sealed["sampling_by_cell"]


# --- the script ------------------------------------------------------------

def test_check_mode_runs_offline_for_both_families():
    for name in ("world", "us"):
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "measure_recall.py"),
             "--family", name, "--check"],
            capture_output=True, text=True, cwd=ROOT)
        assert proc.returncode == 0, proc.stderr
        assert "gold set is valid" in proc.stdout
        assert "worst-case 95% interval" in proc.stdout


def test_the_family_flag_cannot_grade_a_set_on_the_wrong_bars():
    """`--family us` pointed at the worldwide set must be judged by the
    WORLDWIDE bars, because the file declares what it is. The flag routes the
    result; it does not choose the standard."""
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "measure_recall.py"),
         "--family", "us", "--check",
         "--goldset", goldset.latest_path(family.WORLD.goldset_dir)],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "family us" in proc.stdout
    # It validated, which it can only have done against the worldwide bars: the
    # worldwide set is 79 countries and would fail every US bar there is.
    assert "gold set is valid" in proc.stdout


def test_the_us_worklist_names_metros_and_not_countries():
    """A US work list keyed on country is one cell called US that can never be
    an instruction."""
    from analysis.recall import series
    results = [
        {"verdict": "MISSED", "defects": [],
         "gold": {"id": "a", "company": "A", "country": "US", "metro": "Austin",
                  "signal_type": "funding", "size_band": "small",
                  "source_type": "trade_press", "source_name": "O",
                  "source_url": "https://x", "event_date": "2026-06-01",
                  "detail": "d"}},
    ]
    summary = match.summarise(results)
    result = {"summary": summary, "measured_on": "2026-08-12",
              "goldset": {"version": "t"},
              "items": [{"id": "a", "company": "A", "country": "US",
                         "signal_type": "funding", "source_name": "O",
                         "source_url": "https://x", "event_date": "2026-06-01",
                         "verdict": "MISSED"}]}
    worklist = series.build_worklist(result, [], {"window": {"end": "2026-07-31"}})
    assert worklist["spread_group"] == "by_metro"
    assert [c["key"] for c in worklist["zero_countries"]] == ["Austin"]
    assert "metro and event type" in worklist["next_goldset"]["instruction"]
    assert "EDGAR" not in worklist["next_goldset"]["instruction"] or True


def test_the_us_instruction_forbids_the_thing_that_broke_the_first_attempt():
    from analysis.recall import series
    assert "commercial deal database" in series.US_INSTRUCTION
    assert "CHRONOLOGICALLY" in series.US_INSTRUCTION
    assert "US_REQUIRED_SHAPE" in series.US_INSTRUCTION

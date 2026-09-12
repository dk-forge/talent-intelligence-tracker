"""The European reference-set family, and the guards that keep it European.

Europe is the owner's second stated market and until 2026-09-12 had no
reference set of its own. What is protected here: the family is separate from
the other two (its own directory, results, page data and health entry), its
population is an explicit list of thirty countries and nothing else, and it
cannot be quietly rebuilt out of the London press.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import family, goldset, series, thresholds  # noqa: E402


# --- the separation --------------------------------------------------------

def test_the_eu_family_shares_nothing_with_the_other_two():
    for other in (family.WORLD, family.US):
        assert family.EU.goldset_dir != other.goldset_dir
        assert family.EU.results_dir != other.results_dir
        assert family.EU.plugin_data != other.plugin_data
        assert family.EU.health_source != other.health_source
    assert family.EU in family.ALL
    assert family.by_id("eu") is family.EU


def test_an_eu_set_cannot_hijack_the_worldwide_measurement():
    """Same guard as the US family: nothing in the worldwide directory may
    declare another family, because the newest file there IS the worldwide
    set."""
    for path in goldset.all_paths(family.WORLD.goldset_dir):
        assert goldset.load(path).get("family", "world") == "world", path
    for path in goldset.all_paths(family.EU.goldset_dir):
        assert goldset.load(path).get("family") == "eu", path


def test_every_family_is_known_to_the_gates_and_the_shapes():
    for fam in family.ALL:
        assert fam.id in goldset.SHAPES
        for group in fam.breakdowns:
            assert group in thresholds.CELL_GROUPS


# --- the guards ------------------------------------------------------------

COUNTRIES = ["GB", "DE", "FR", "NL", "SE", "ES", "IT", "PL", "DK", "CH",
             "NO", "IE", "FI", "BE", "AT", "PT", "CZ", "EE"]


def _eu_set(items):
    return {
        "version": "test", "family": "eu", "sealed": True,
        "assembled_on": "2026-09-12",
        "window": {"start": "2026-08-01", "end": "2026-08-31"},
        "items": items,
    }


def _item(index, **overrides):
    base = {
        "id": f"e{index}",
        "company": f"Company {index}",
        "signal_type": "leadership" if index % 3 == 0 else "funding",
        "event_date": "2026-08-15",
        "country": COUNTRIES[index % len(COUNTRIES)],
        "size_band": "small" if index % 3 else "large",
        "amount_usd": None if index % 3 == 0 else 10_000_000,
        "detail": "Series A" if index % 3 else "New chief executive",
        "source_url": "https://example.com/a",
        "source_type": ["press_release", "trade_press", "national_news"][index % 3],
        "source_name": "Outlet",
    }
    base.update(overrides)
    return base


def _problems(items):
    return goldset.validate(_eu_set(items), peers=[])


def test_a_valid_eu_shaped_set_passes():
    assert _problems([_item(i) for i in range(54)]) == []


def test_the_population_is_the_list_and_nothing_else():
    """Turkey, Israel, Ukraine and the United States are all real markets and
    none of them is this one. A row from outside the list is measuring a
    different population under a European heading."""
    for outside in ("US", "IL", "TR", "UA", "RS"):
        items = [_item(i) for i in range(54)]
        items[5]["country"] = outside
        assert any("outside the population" in p for p in _problems(items)), outside
    assert "TR" not in goldset.EU_COUNTRIES
    assert len(goldset.EU_COUNTRIES) == 30
    assert {"GB", "CH", "NO"} <= goldset.EU_COUNTRIES


def test_a_european_set_that_is_really_the_united_kingdom_is_refused():
    items = [_item(i) for i in range(54)]
    for item in items[:20]:
        item["country"] = "GB"
    assert any("above the 30% ceiling" in p for p in _problems(items))


def test_thin_cells_do_not_count_as_breadth():
    """Twelve countries carrying one event each look broad and measure none of
    them. Six countries must carry three or more."""
    items = [_item(i, country=COUNTRIES[i % 18]) for i in range(54)]
    for item in items:
        item["country"] = COUNTRIES[int(item["id"][1:]) % 18]
    # Now collapse: put everything except one token per country into GB and DE.
    spread = [_item(i, country=("GB" if i % 2 else "DE")) for i in range(40)]
    spread += [_item(40 + n, country=c) for n, c in enumerate(COUNTRIES[2:16])]
    assert any("carry 3+ events" in p for p in _problems(spread))


def test_one_kind_of_document_may_not_be_most_of_the_set():
    items = [_item(i, source_type="press_release") for i in range(54)]
    assert any("above the 50% ceiling" in p for p in _problems(items))


def test_leadership_and_small_employers_cannot_be_left_out():
    items = [_item(i, signal_type="funding", detail="Series A",
                   amount_usd=60_000_000, size_band="large") for i in range(54)]
    problems = _problems(items)
    assert any("leadership is 0/54" in p for p in problems)
    assert any("small is 0/54" in p for p in problems)


def test_the_worldwide_and_us_shapes_are_untouched_by_all_of_this():
    assert "allowed_countries" not in goldset.REQUIRED_SHAPE
    assert "allowed_countries" not in goldset.US_REQUIRED_SHAPE
    assert goldset.REQUIRED_SHAPE["max_country_share"] == 0.45
    assert goldset.US_REQUIRED_SHAPE["single_country"] == "US"


# --- the script and the work list ------------------------------------------

def test_check_mode_runs_offline_for_every_family():
    for fam in family.ALL:
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "measure_recall.py"),
             "--family", fam.id, "--check"],
            capture_output=True, text=True, cwd=ROOT)
        assert proc.returncode == 0, proc.stderr
        assert "gold set is valid" in proc.stdout


def test_the_eu_work_list_hands_out_the_eu_recipe():
    results = [
        {"verdict": "MISSED", "defects": [],
         "gold": {"id": "a", "company": "A", "country": "DE",
                  "signal_type": "funding", "size_band": "small",
                  "source_type": "trade_press", "source_name": "O",
                  "source_url": "https://x", "event_date": "2026-08-01",
                  "detail": "d"}},
    ]
    from analysis.recall import match
    result = {"measured_on": "2026-09-12", "goldset": {"version": "v"},
              "summary": match.summarise(results),
              "items": [{"verdict": "MISSED", "company": "A", "country": "DE",
                         "signal_type": "funding", "source_name": "O",
                         "source_url": "https://x", "event_date": "2026-08-01"}]}
    work = series.build_worklist(result, [], _eu_set([_item(0)]))
    assert "EUROPE" in work["next_goldset"]["instruction"]
    assert "EU_REQUIRED_SHAPE" in work["next_goldset"]["instruction"]
    assert "goldset-eu-" in work["next_goldset"]["instruction"]
    assert work["spread_group"] == "by_country"
    # And the other two recipes are still handed to their own families.
    us_work = series.build_worklist(
        result | {"summary": result["summary"] | {"by_metro": {}}}, [],
        {"family": "us", "window": {"start": "2026-06-01", "end": "2026-07-31"}})
    assert "UNITED STATES" in us_work["next_goldset"]["instruction"]
    world_work = series.build_worklist(
        result, [], {"window": {"start": "2026-07-01", "end": "2026-07-28"}})
    assert "family=\"world\"" in world_work["next_goldset"]["instruction"]


def test_the_scheduled_run_measures_europe_too():
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "recall.yml"),
              encoding="utf-8") as handle:
        text = handle.read()
    workflow = yaml.safe_load(text)
    runs = " ".join(step.get("run", "") for step in workflow["jobs"]["recall"]["steps"])
    assert "--family eu --check" in runs
    assert "--family eu --quiet --push" in runs
    assert "rejection_audit --family eu" in runs
    assert "analysis/recall/eu/results" in runs
    assert "recall-eu.json" in runs


def test_the_eu_measurement_is_never_a_collector():
    import health_digest
    import staleness
    assert "recall_eu" in health_digest.MEASUREMENT_ONLY
    assert staleness.MAX_AGE_HOURS["recall_eu"] == staleness.MAX_AGE_HOURS["recall"]

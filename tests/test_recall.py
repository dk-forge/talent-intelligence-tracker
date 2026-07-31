"""The recall measurement, tested offline.

Two things are worth protecting here. The gold set must stay valid and stay
independent, because an invalid reference set produces a wrong denominator that
nobody notices. And the matching rule must stay exactly as strict as it was
when a number was published, because loosening it silently is how a recall
figure improves without coverage improving.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.recall import goldset, match, series  # noqa: E402


# --- the gold set itself ---------------------------------------------------

@pytest.fixture(scope="module")
def gold():
    return goldset.load()


def test_goldset_is_valid(gold):
    assert goldset.validate(gold) == []


def test_goldset_was_sealed_before_it_was_measured(gold):
    """Independence is a date, not a promise. The assembly date must not be
    later than the first measurement, and the file has to say when it was
    fixed."""
    assert gold["assembled_on"], "the gold set must record when it was assembled"
    assert gold["sealed"] is True, "an unsealed gold set must not be measured against"


def test_goldset_never_cites_our_own_tracker(gold):
    """The one failure that would make the whole exercise meaningless: a
    reference set assembled by reading our own database."""
    for item in gold["items"]:
        assert "asktherecruiter.com" not in item["source_url"]


def test_goldset_has_a_deliberate_spread(gold):
    shape = goldset.counts(gold)
    assert shape["total"] >= 40, "too small to break down by cell"
    assert shape["geography"].get("US", 0) >= 10
    assert shape["geography"].get("non-US", 0) >= 15
    assert shape["signal_type"].get("funding", 0) >= 10
    assert shape["signal_type"].get("leadership", 0) >= 10
    assert shape["size_band"].get("small", 0) >= 10, "large events only would flatter the result"
    assert len(shape["country"]) >= 8


def test_the_four_known_misses_are_seeded(gold):
    """These four were known before the gold set existed. If a later edit drops
    them, the gold set has been tuned to flatter the result."""
    names = {item["company"].lower() for item in gold["items"]}
    for seed in ("glow", "plantopia", "harmony", "enigma"):
        assert any(seed in name for name in names), f"seed {seed} is missing"


# --- name matching ---------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Glow", "Glow Security"),
    ("Enigma Technologies", "Enigma"),
    ("Pitney Bowes Inc.", "Pitney Bowes"),
    ("Acme, Inc", "Acme Inc."),
])
def test_names_that_are_the_same_employer(a, b):
    assert match.names_match(a, b)


@pytest.mark.parametrize("a,b", [
    ("Glow", "Glowforge"),
    ("Harmony", "Harmonic"),
    ("Enigma", "Enigmatic Labs"),
    ("Ramp", "Rampart Systems"),
])
def test_names_that_are_not(a, b):
    """Word-boundary matching, not substring. A substring rule would count
    Glowforge as Glow and inflate recall."""
    assert not match.names_match(a, b)


# --- classification --------------------------------------------------------

GOLD_FUNDING = {
    "id": "x1", "company": "Glow", "signal_type": "funding",
    "event_date": "2026-07-14", "country": "IL", "amount_usd": 180000000,
    "size_band": "large", "source_type": "trade_press",
}


def row(**over):
    base = {
        "signal_id": "r1", "company": "Glow", "pillar": "company_development",
        "country": "IL", "hq_country": "IL", "funding_amount_usd": "180000000",
        "published_date": "2026-07-14", "source_url": "https://example.com/a",
        "headline": "Glow raises $180M",
    }
    base.update(over)
    return base


def test_a_clean_hit_is_found():
    assert match.classify(GOLD_FUNDING, [row()])["verdict"] == "FOUND"


def test_nothing_stored_is_missed():
    assert match.classify(GOLD_FUNDING, [])["verdict"] == "MISSED"


def test_missing_country_is_partial_not_a_hit():
    """The Enigma case: we hold the round but with no country, so it is
    invisible to every geographic filter on the site."""
    out = match.classify(GOLD_FUNDING, [row(country=None, hq_country=None)])
    assert out["verdict"] == "FOUND_PARTIAL"
    assert "country_missing" in out["defects"]


def test_wrong_country_is_partial():
    out = match.classify(GOLD_FUNDING, [row(country="US", hq_country="US")])
    assert "country_wrong" in out["defects"]


def test_missing_amount_is_partial():
    out = match.classify(GOLD_FUNDING, [row(funding_amount_usd=None)])
    assert "amount_missing" in out["defects"]


def test_a_rounded_amount_is_still_the_same_number():
    """Sources round. $178M reported against a $180M round is not a defect."""
    assert match.classify(GOLD_FUNDING, [row(funding_amount_usd="178000000")])["verdict"] == "FOUND"


def test_a_different_amount_is_a_defect():
    out = match.classify(GOLD_FUNDING, [row(funding_amount_usd="18000000")])
    assert "amount_mismatch" in out["defects"]


def test_the_wrong_kind_of_event_is_not_a_match():
    """We may hold a leadership change for the same company in the same week.
    That is not the funding round, and it earns no credit for one."""
    out = match.classify(GOLD_FUNDING, [row(
        pillar="leadership_change", funding_amount_usd=None,
        headline="Glow appoints a chief revenue officer")])
    assert out["verdict"] == "MISSED"


GOLD_LEADERSHIP = {
    "id": "x9", "company": "American Eagle Outfitters", "signal_type": "leadership",
    "event_date": "2026-07-01", "country": "US", "amount_usd": None,
    "size_band": "large", "source_type": "press_release",
}


def test_a_filing_shelved_under_compensation_counts_as_held_but_wrong():
    """An 8-K Item 5.02 covers both the officer change and what they are paid,
    so it can be classified as compensation. We have the event; it is just
    invisible to anyone browsing leadership changes. That is a defect, not a
    miss, and calling it a miss would overstate the gap."""
    out = match.classify(GOLD_LEADERSHIP, [row(
        company="AMERICAN EAGLE OUTFITTERS, INC.", pillar="rewards_comp",
        headline="AMERICAN EAGLE OUTFITTERS INC 8-K filing (Item 5.02): officer change",
        published_date="2026-06-29", country="US", hq_country="US",
        funding_amount_usd=None)])
    assert out["verdict"] == "FOUND_PARTIAL"
    assert "wrong_category" in out["defects"]


def test_an_unrelated_row_in_the_wrong_pillar_is_still_a_miss():
    """Partial credit is for the same event under the wrong heading, not for
    any row at all about the same employer that month."""
    out = match.classify(GOLD_LEADERSHIP, [row(
        company="American Eagle Outfitters", pillar="rewards_comp",
        headline="American Eagle Outfitters: median hourly pay gap is 4.1%",
        published_date="2026-07-02", funding_amount_usd=None)])
    assert out["verdict"] == "MISSED"


def test_an_event_far_outside_the_window_is_not_a_match():
    out = match.classify(GOLD_FUNDING, [row(published_date="2026-02-01")])
    assert out["verdict"] == "MISSED"


def test_a_late_writeup_inside_the_window_still_counts():
    assert match.classify(GOLD_FUNDING, [row(published_date="2026-07-28")])["verdict"] == "FOUND"


def test_the_cleanest_duplicate_decides_the_verdict():
    """Holding an event twice, once well and once badly, is a deduplication
    problem, not a recall failure."""
    out = match.classify(GOLD_FUNDING, [row(signal_id="bad", country=None, hq_country=None),
                                        row(signal_id="good")])
    assert out["verdict"] == "FOUND"
    assert out["matched_row"]["signal_id"] == "good"


# --- the summary -----------------------------------------------------------

def test_every_percentage_carries_its_counts():
    results = [
        {"verdict": "FOUND", "defects": [], "gold": dict(GOLD_FUNDING)},
        {"verdict": "MISSED", "defects": [], "gold": dict(GOLD_FUNDING, id="x2", country="US")},
    ]
    summary = match.summarise(results)
    assert summary["overall"] == {
        "total": 2, "found": 1, "found_partial": 0, "missed": 1,
        "held": 1, "held_pct": 50.0, "clean_pct": 50.0,
    }
    for group in ("by_geography", "by_country", "by_signal_type", "by_source_type"):
        for cell in summary[group].values():
            assert cell["total"] > 0
            assert set(cell) >= {"total", "found", "missed", "held_pct", "clean_pct"}


def test_a_rate_with_no_denominator_is_none_not_zero():
    assert match.rate(0, 0) is None
    assert match.rate(0, 5) == 0.0


# --- the script ------------------------------------------------------------

def test_check_mode_runs_offline_and_passes():
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "measure_recall.py"), "--check"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "gold set is valid" in proc.stdout


def test_offline_replay_produces_a_dated_measurement(tmp_path, gold):
    """Repeatability: the same script re-runs with no network and records the
    date it ran. A recall figure with no date is worthless."""
    fixture = tmp_path / "rows.json"
    fixture.write_text(json.dumps({gold["items"][0]["id"]: []}))
    out = match.summarise([])
    assert out["overall"]["held_pct"] is None

    from measure_recall import measure  # noqa: WPS433
    result = measure(gold, offline_rows={}, verbose=False)
    assert result["measured_on"]
    assert result["goldset"]["digest"] == gold["_digest"]
    assert result["summary"]["overall"]["total"] == len(gold["items"])


# --- the shape guard on future sets ----------------------------------------

def _shaped(**over):
    """A minimally valid set, deliberately spread, as any future set must be."""
    # Twenty-one countries across all seven of the project's regions, none of
    # them dominant. "Minimally valid" got much harder on 2026-07-30: the first
    # measurement found 27 of 29 countries scoring zero, which is what made a
    # set that is really the US and western Europe worth refusing outright.
    plan = [("US", "funding", "large", "filing"), ("US", "leadership", "small", "press_release"),
            ("CA", "funding", "small", "filing"),
            ("GB", "funding", "small", "trade_press"), ("DE", "leadership", "small", "national_news"),
            ("FR", "funding", "large", "national_news"), ("PL", "leadership", "small", "trade_press"),
            ("IN", "funding", "small", "trade_press"), ("JP", "leadership", "small", "trade_press"),
            ("SG", "funding", "large", "press_release"), ("ID", "funding", "small", "national_news"),
            ("BR", "funding", "large", "press_release"), ("MX", "leadership", "small", "national_news"),
            ("CL", "funding", "small", "trade_press"),
            ("ZA", "leadership", "large", "filing"), ("NG", "funding", "small", "trade_press"),
            ("KE", "funding", "small", "national_news"),
            ("IL", "funding", "large", "trade_press"), ("AE", "leadership", "small", "press_release"),
            ("AU", "leadership", "large", "press_release"), ("NZ", "funding", "small", "trade_press")]
    items = []
    for i in range(63):
        country, signal, band, source = plan[i % len(plan)]
        items.append({
            "id": f"g{i}", "company": f"Company {i}", "signal_type": signal,
            "event_date": "2026-08-10", "country": country, "size_band": band,
            "detail": "Series A", "source_url": f"https://example.com/{i}",
            "source_type": source, "source_name": "Example", "amount_usd": 1000000,
        })
    data = {"version": "test", "assembled_on": "2026-09-01", "sealed": True,
            "window": {"start": "2026-08-01", "end": "2026-08-31"}, "items": items}
    data.update(over)
    return data


def test_a_well_shaped_future_set_validates():
    assert goldset.validate(_shaped()) == []


def test_an_unsealed_set_is_refused():
    assert any("not sealed" in p for p in goldset.validate(_shaped(sealed=False)))


def test_a_set_rebuilt_out_of_easy_us_filings_is_refused():
    """The failure this guard exists for. Nobody has to intend it: "use what was
    easy to find" produces an all-US, all-filing set on its own, and then the
    number climbs for the worst possible reason."""
    easy = _shaped()
    for item in easy["items"]:
        item["country"] = "US"
        item["size_band"] = "large"
        item["source_type"] = "filing"
    problems = goldset.validate(easy)
    assert any("non-US" in p for p in problems)
    assert any("small" in p for p in problems)
    assert any("kinds of document" in p for p in problems)


def test_a_set_that_is_broad_on_paper_and_one_country_in_practice_is_refused():
    """The way "widen the gold set" gets satisfied without widening anything:
    keep the US events, bolt one event each onto forty countries. Every country
    count goes up, the map fills in, and the headline number is still a US
    number."""
    fake = _shaped()
    filler = ["PE", "UY", "EC", "CR", "PA", "GT", "DO", "PY", "BO", "JM",
              "TT", "SV", "HN", "IS", "LU", "MT", "CY", "AL", "MD", "MK"]
    for index, item in enumerate(fake["items"]):
        item["country"] = filler[index] if index < len(filler) else "US"
    problems = goldset.validate(fake)
    assert any("of the set: above the" in p for p in problems)
    assert any("cannot measure a country" in p for p in problems)


def test_a_set_confined_to_two_regions_is_refused():
    """Twenty-one countries, all of them in Europe and North America. This is
    what "use what was easy to find" produces once somebody has been told to
    add countries, and it measures the feeds we already had."""
    fake = _shaped()
    western = ["US", "CA", "GB", "DE", "FR", "PL", "ES", "IT", "NL", "SE",
               "NO", "FI", "DK", "IE", "PT", "AT", "BE", "CZ", "RO", "GR", "HU"]
    for index, item in enumerate(fake["items"]):
        item["country"] = western[index % len(western)]
    problems = goldset.validate(fake)
    assert any("regions carry" in p for p in problems), problems


def test_the_regions_come_from_the_projects_own_vocabulary():
    """Not a second geography invented in the benchmark. A country the pipeline
    cannot place lands under None, which is a finding rather than an error."""
    shape = goldset.counts({"items": [
        {"signal_type": "funding", "country": c, "source_type": "filing",
         "size_band": "small"}
        for c in ("US", "NG", "JP", "BR", "IL", "AU", "PL")]})
    assert set(goldset.regions(shape)) == {
        "North America", "Africa", "Asia", "Latin America", "Middle East",
        "Oceania", "Europe"}


def test_a_new_set_may_not_be_narrower_than_the_widest_one_already_on_disk():
    """The ratchet. The failure it prevents is not malice: an ordinary month
    where only the easy countries answer, the next set quietly comes back at 30
    of them, and the published figure rises because the world got smaller."""
    wide = goldset.breadth(goldset.counts(goldset.load(goldset.latest_path())))
    narrow = _shaped()
    problems = goldset._ratchet_problems(narrow, [wide])
    assert any("not supposed to be reversible" in p for p in problems), problems


def test_the_ratchet_never_reaches_backwards():
    """A ratchet comparing against LATER sets would invalidate the history it
    exists to protect: the day the 169-event set landed, the 89-event set it
    superseded would have stopped validating and its published 9.0% would have
    become underivable."""
    paths = goldset.all_paths()
    if len(paths) < 2:
        pytest.skip("needs two sets on disk")
    oldest = goldset.load(paths[0])
    peers = goldset.peer_breadths(paths[0], assembled_on=oldest["assembled_on"])
    assert peers == []
    assert goldset.validate(oldest) == []


def test_a_set_built_in_memory_is_judged_on_the_fixed_bars_only():
    """Unit tests must not be graded against whatever happens to be in the
    repository that week."""
    assert goldset.validate(_shaped()) == []


def test_an_undisclosed_round_must_say_so_rather_than_just_omit_the_number():
    """A set that cannot admit an undisclosed round measures only the events
    that came with a number, and that bias points straight at the markets this
    benchmark exists to cover."""
    silent = _shaped()
    for item in silent["items"]:
        if item["signal_type"] == "funding":
            item["amount_usd"] = None
    assert any("amount_disclosed=false" in p for p in goldset.validate(silent))

    declared = _shaped()
    funding = [i for i in declared["items"] if i["signal_type"] == "funding"]
    for item in funding[:2]:
        item["amount_usd"] = None
        item["amount_disclosed"] = False
    assert goldset.validate(declared) == []


def test_undisclosed_cannot_become_the_easy_way_in():
    declared = _shaped()
    for item in declared["items"]:
        if item["signal_type"] == "funding":
            item["amount_usd"] = None
            item["amount_disclosed"] = False
    assert any("cannot be checked on the number" in p
               for p in goldset.validate(declared))


def test_every_gold_set_on_disk_is_valid():
    """Historical sets are kept so any past figure can be re-derived. A kept set
    that no longer validates would make its figure unreproducible."""
    paths = goldset.all_paths()
    assert paths, "no gold set on disk"
    for path in paths:
        assert goldset.validate(goldset.load(path)) == [], path


def test_the_newest_set_is_the_one_measured():
    assert goldset.DEFAULT_PATH == goldset.latest_path()


# --- the series and the work list ------------------------------------------

def _result(measured_on, held, total=10, version="v1", country_cells=None):
    return {
        "measured_on": measured_on,
        "goldset": {"version": version, "digest": "abc", "assembled_on": "2026-07-28",
                    "window": {"start": "2026-07-01", "end": "2026-07-28"}},
        "summary": {
            "overall": {"total": total, "found": held, "found_partial": 0,
                        "missed": total - held, "held": held,
                        "held_pct": round(100 * held / total, 1),
                        "clean_pct": round(100 * held / total, 1)},
            "by_country": country_cells or {},
            "by_source_type": {}, "by_segment": {}, "by_signal_type": {},
            "by_geography": {}, "defects": {},
        },
        "items": [],
    }


def test_the_series_is_ordered_and_keeps_each_points_set(tmp_path):
    for name, res in (("recall-2026-08-03.json", _result("2026-08-03", 3)),
                      ("recall-2026-07-28.json", _result("2026-07-28", 1))):
        (tmp_path / name).write_text(json.dumps(res))
    points = series.load_series(str(tmp_path))
    assert [p["measured_on"] for p in points] == ["2026-07-28", "2026-08-03"]
    assert all(p["goldset_version"] == "v1" for p in points)


def test_a_corrupt_historical_file_does_not_take_the_run_down(tmp_path):
    (tmp_path / "recall-2026-07-28.json").write_text(json.dumps(_result("2026-07-28", 1)))
    (tmp_path / "recall-2026-08-03.json").write_text("{ this is not json")
    assert len(series.load_series(str(tmp_path))) == 1


def test_a_converged_set_is_declared_due():
    """Re-running one set forever measures memorisation, not recall. Three
    identical measurements mean it has converged, whatever the calendar says."""
    points = [{"goldset_version": "v1", "overall": {"held": 8}} for _ in range(3)]
    current = {"version": "v1", "window": {"start": "2026-07-01", "end": "2026-07-28"}}
    verdict = series.goldset_is_due(points, current, today=date(2026, 8, 1))
    assert verdict["due"] and "converged" in verdict["reason"]


def test_an_aged_window_is_declared_due():
    current = {"version": "v1", "window": {"start": "2026-07-01", "end": "2026-07-28"}}
    verdict = series.goldset_is_due([], current, today=date(2026, 10, 1))
    assert verdict["due"] and "closed" in verdict["reason"]


def test_a_moving_current_set_is_not_due():
    points = [{"goldset_version": "v1", "overall": {"held": h}} for h in (6, 7, 8)]
    current = {"version": "v1", "window": {"start": "2026-07-01", "end": "2026-07-28"}}
    assert series.goldset_is_due(points, current, today=date(2026, 8, 1))["due"] is False


def test_the_work_list_names_the_countries_that_held_nothing():
    """The point of automating this: a zero is not a fact to display, it is an
    instruction to go and find a route into that country's press."""
    cells = {
        "NG": {"total": 3, "found": 0, "found_partial": 0, "missed": 3, "held": 0,
               "held_pct": 0.0, "clean_pct": 0.0},
        "US": {"total": 7, "found": 7, "found_partial": 0, "missed": 0, "held": 7,
               "held_pct": 100.0, "clean_pct": 100.0},
    }
    work = series.build_worklist(
        _result("2026-07-28", 7, country_cells=cells), [],
        {"version": "v1", "window": {"start": "2026-07-01", "end": "2026-07-28"}},
        today=date(2026, 7, 28))
    assert [c["key"] for c in work["zero_countries"]] == ["NG"]
    assert work["zero_countries"][0]["total"] == 3
    assert work["next_goldset"]["suggested_window"]["start"] == "2026-06-01"
    # The instruction has to be paste-ready and carry the one rule that makes
    # the whole exercise mean anything, because it is read by whoever picks the
    # alert up months from now with no memory of this.
    instruction = work["next_goldset"]["instruction"].lower()
    assert "independent research passes" in instruction
    assert "asktherecruiter.com" in instruction, "must forbid consulting our own data"
    assert "source url" in instruction
    assert "sealed=true" in instruction
    assert "2026-06" in instruction, "must name the file to write"


def test_the_committed_work_list_matches_the_committed_measurement():
    """Other tooling reads the work list, so it must not drift from the result
    it claims to describe."""
    work_path = os.path.join(ROOT, "data", "recall_worklist.json")
    if not os.path.exists(work_path):
        pytest.skip("no measurement run yet")
    with open(work_path, encoding="utf-8") as handle:
        work = json.load(handle)
    result_path = os.path.join(
        ROOT, "analysis", "recall", "results", f"recall-{work['measured_on']}.json")
    assert os.path.exists(result_path), "the work list names a measurement not on disk"
    with open(result_path, encoding="utf-8") as handle:
        result = json.load(handle)
    assert work["overall"] == result["summary"]["overall"]


# --- the schedule ----------------------------------------------------------

def test_the_measurement_is_scheduled_and_shares_the_writers_lock():
    """It files a source_health row, which makes it a database writer, and a
    writer outside the group is a writer with no lock."""
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "recall.yml"),
              encoding="utf-8") as handle:
        workflow = yaml.safe_load(handle)
    # PyYAML reads a bare `on:` key as the boolean True.
    triggers = workflow.get("on") or workflow.get(True)
    assert "schedule" in triggers, "an unscheduled measurement is a manual one"
    assert workflow["concurrency"]["group"] == "talent-collect"
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_a_measurement_never_counts_as_the_pipeline_being_alive():
    """`recall` reports into the same ledger as the collectors so it shows on
    the health page. If it also counted towards "is anything still running", a
    weekly measurement would mask every collector being dead for a day a week."""
    import health_digest
    assert "recall" in health_digest.MEASUREMENT_ONLY
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    collectors = {
        "recall": {"status": "ok", "run_at": "2026-08-01T08:00:00+00:00"},
        "google_news": {"status": "ok", "run_at": "2026-07-01T06:00:00+00:00"},
    }
    assert health_digest.pipeline_stopped(collectors, now) is True


def test_published_page_data_matches_a_recorded_measurement():
    """What the page renders must be a measurement this repo can produce, not a
    hand-written figure."""
    published = os.path.join(
        ROOT, "wordpress-plugin", "talent-intelligence-tracker", "data", "recall.json")
    if not os.path.exists(published):
        pytest.skip("no measurement published yet")
    with open(published, encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["measured_on"], "a published recall figure must carry its date"
    assert isinstance(data.get("series"), list) and data["series"], (
        "the page must render the trend, not one measurement")
    assert data["series"][-1]["measured_on"] == data["measured_on"], (
        "the series must end at the measurement the page is showing")
    assert data["goldset"]["digest"], "and the digest of the gold set it was measured against"
    summary = data["summary"]["overall"]
    assert summary["total"] == len(data["items"])
    assert summary["held"] + summary["missed"] == summary["total"]

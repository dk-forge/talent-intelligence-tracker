"""The landmark guard, and the ways it could quietly stop guarding.

Most of these tests are not about whether the checker computes the right
verdict. They are about the four ways a guard like this dies without a red
run, each of which is a real thing that has happened to a check in this
repository or its sibling:

  1. the reference file is emptied, and "0 of 0 held" passes for ever;
  2. an entry that is genuinely missing is scored as held, because the matcher
     is generous and nobody measured it;
  3. the report is written but never read, so a regression has nothing to
     regress against;
  4. the number is computed and never wired to anything a human sees.

There is no network and no model in any of this.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from analysis.landmarks import check, landmarks

ROOT = Path(__file__).resolve().parent.parent


# --- the committed set ------------------------------------------------------

def test_the_shipped_landmark_set_loads_and_validates():
    data = landmarks.load()
    assert landmarks.entries(data), "the shipped set has no entries"


def test_the_shipped_set_is_big_enough_to_mean_something():
    data = landmarks.load()
    items = landmarks.entries(data)
    assert len(items) >= landmarks.MIN_ENTRIES
    assert len({e["quarter"] for e in items}) >= landmarks.MIN_QUARTERS
    assert len({e["company"].lower() for e in items}) >= landmarks.MIN_COMPANIES


def test_every_landmark_carries_a_primary_document():
    """No source URL, no record. The same rule the pipeline enforces on rows,
    applied to the events we grade ourselves against."""
    for entry in landmarks.entries(landmarks.load()):
        assert entry["source_url"].startswith("https://"), entry["id"]
        assert entry["source_kind"] in landmarks.SOURCE_KINDS, entry["id"]


def test_every_company_announcement_really_is_on_the_company_s_own_domain():
    """Provenance rule, asserted as a POSITIVE.

    A landmark sourced to somebody else's commercial dataset is a licensing
    problem and a circular measurement at once, and the temptation is real
    because those sites are the easiest place to find the list. The check is
    written the other way round on purpose: rather than a denylist of vendors
    (which this repository may not spell out anyway, see
    tests/test_no_provider_names.py), it asserts that a source claiming to be
    the company's own announcement is served from the company's own domain.

    A denylist only catches the vendors somebody thought of. This catches
    everything that is not the employer.
    """
    import re

    for entry in landmarks.entries(landmarks.load()):
        if entry["source_kind"] != "company_announcement":
            continue
        host = entry["source_url"].split("/")[2].lower()
        flattened = re.sub(r"[^a-z0-9]", "", host.removeprefix("www."))
        tokens = []
        for name in landmarks.names(entry):
            tokens += [re.sub(r"[^a-z0-9]", "", t)
                       for t in name.lower().split() if t not in {"ai", "inc"}]
        assert any(t and t in flattened for t in tokens), (
            "%s: %s is not %s's own domain"
            % (entry["id"], host, entry["company"]))


def test_every_quarter_label_agrees_with_its_own_date():
    for entry in landmarks.entries(landmarks.load()):
        assert entry["quarter"] == landmarks.quarter_of(entry["event_date"]), \
            entry["id"]


def test_an_empty_set_is_a_hard_failure_and_not_a_perfect_score():
    """THE test this file exists for.

    An emptied landmark file makes every downstream number read "0 of 0 held,
    0 regressions" and exit 0 for ever. That is indistinguishable from a
    healthy week to every scheduler, email and exit code downstream, so it has
    to be refused at the door.
    """
    problems = landmarks.validate({"entries": []})
    assert problems
    assert any("EMPTY" in p for p in problems)

    with pytest.raises(landmarks.InvalidLandmarkSet):
        landmarks.load(str(ROOT / "does-not-exist.json"))


def test_a_thinned_set_is_refused():
    data = landmarks.load()
    thinned = dict(data, entries=landmarks.entries(data)[:3])
    problems = landmarks.validate(thinned)
    assert any("floor" in p for p in problems), problems


def test_a_landmark_without_a_source_url_is_refused():
    data = landmarks.load()
    items = [dict(e) for e in landmarks.entries(data)]
    items[0]["source_url"] = ""
    assert any("source_url" in p for p in landmarks.validate(dict(data, entries=items)))


def test_a_duplicated_event_is_refused():
    data = landmarks.load()
    items = [dict(e) for e in landmarks.entries(data)]
    twin = dict(items[0], id=items[0]["id"] + "-again")
    assert any("duplicate event" in p
               for p in landmarks.validate(dict(data, entries=items + [twin])))


# --- the checker ------------------------------------------------------------

ENTRY = {
    "id": "2026q2-example-65b",
    "quarter": "2026Q2",
    "company": "Anthropic",
    "event_date": "2026-05-28",
    "amount_usd": 65000000000,
    "amount_text": "$65 billion Series H",
    "source_url": "https://www.anthropic.com/news/series-h",
    "source_kind": "company_announcement",
}


def row(**kw):
    base = {
        "signal_id": "sig",
        "company": "Anthropic",
        "pillar": "company_development",
        "headline": "Anthropic raises $65B in Series H funding",
        "summary": "",
        "funding_amount_usd": 65000000000,
        "published_date": "2026-05-28",
        "source_url": "https://www.anthropic.com/news/series-h",
    }
    base.update(kw)
    return base


def test_a_matching_row_is_held():
    assert check.verdict(ENTRY, [row()])["verdict"] == check.HELD


def test_the_checker_cannot_silently_pass_on_a_missing_entry():
    """Requirement stated as a test.

    An empty corpus must produce MISSING for every landmark and held == 0.
    A checker that reports a pass here is worse than no checker: it is a green
    light over an empty database.
    """
    body = check.evaluate([ENTRY], [], None, today=date(2026, 8, 4))
    assert body["entries"][0]["stored_verdict"] == check.MISSING
    assert body["summary"]["held"] == 0
    assert body["summary"]["standing_gaps"] == 1
    assert "0 of 1 held" in body["summary"]["one_line"]


def test_an_unrelated_row_for_the_same_employer_is_not_the_event():
    """A leadership change in the window must never answer for the round."""
    unrelated = row(pillar="leadership_change", funding_amount_usd=None,
                    headline="Anthropic appoints a chief financial officer")
    assert check.verdict(ENTRY, [unrelated])["verdict"] == check.MISSING


def test_a_different_round_by_the_same_employer_is_not_this_one():
    """Anthropic's Series G and Series H are 105 days apart. A window wide
    enough to let them touch would score one round twice and never notice the
    other going missing."""
    series_g = row(published_date="2026-02-12", funding_amount_usd=30000000000,
                   headline="Anthropic raises $30bn Series G")
    assert check.verdict(ENTRY, [series_g])["verdict"] == check.MISSING


def test_a_row_with_the_wrong_amount_is_wrong_amount_not_held():
    off = row(funding_amount_usd=6500000000)
    result = check.verdict(ENTRY, [off])
    assert result["verdict"] == check.WRONG_AMOUNT
    assert "6.5bn" in result["detail"]


def test_a_row_with_no_parsed_amount_is_wrong_amount_not_held():
    """The live site spent months showing 'OpenAI capta 93.175 millones' as its
    only OpenAI funding row: an event we held, under a figure no reader can
    read. Holding the story is not holding the number."""
    unquantified = row(funding_amount_usd=None)
    result = check.verdict(ENTRY, [unquantified])
    assert result["verdict"] == check.WRONG_AMOUNT
    assert "no USD amount" in result["detail"]


def test_a_near_name_row_never_answers_for_the_round():
    """'MI XAI Investment, LLC' raising $1.84m is not xAI's $20bn Series E."""
    entry = dict(ENTRY, company="xAI", amount_usd=20000000000,
                 event_date="2026-01-06", quarter="2026Q1")
    feeder = row(company="MI XAI Investment, LLC", funding_amount_usd=1840000,
                 published_date="2026-01-27",
                 headline="MI XAI Investment, LLC raised $1.8M in a private placement")
    assert check.verdict(entry, [feeder])["verdict"] == check.MISSING


def test_an_alias_finds_the_employer_under_its_other_name():
    entry = dict(ENTRY, company="Anysphere", aliases=["Cursor"],
                 amount_usd=2300000000, event_date="2025-11-13",
                 quarter="2025Q4")
    stored = row(company="Cursor", funding_amount_usd=2300000000,
                 published_date="2025-11-14",
                 headline="Cursor raises $2.3B Series D")
    assert check.verdict(entry, [stored])["verdict"] == check.HELD


def test_a_more_than_figure_accepts_a_larger_stored_amount():
    entry = dict(ENTRY, amount_usd=1000000000,
                 amount_text="more than $1 billion, Series C")
    assert check.verdict(entry, [row(funding_amount_usd=1050000000)])["verdict"] \
        == check.HELD


def test_an_approximate_amount_is_not_amount_checked():
    """A round announced in euros is not graded against a conversion the
    publisher never wrote."""
    entry = dict(ENTRY, amount_usd=1990000000, amount_is_approximate=True,
                 currency="EUR")
    result = check.verdict(entry, [row(funding_amount_usd=1700000000)])
    assert result["verdict"] == check.HELD
    assert result["amount_checked"] is False


# --- regression versus standing gap ----------------------------------------

def test_a_never_held_landmark_is_a_standing_gap_and_not_a_regression():
    body = check.evaluate([ENTRY], [], None, today=date(2026, 8, 4), history={})
    assert body["summary"]["regressions"] == 0
    assert body["summary"]["standing_gaps"] == 1
    assert body["entries"][0]["regression"] == []


def test_a_previously_held_landmark_going_missing_is_a_regression():
    history = {ENTRY["id"]: {"ever_stored": True, "ever_live": False,
                             "first_held_on": "2026-07-01",
                             "last_held_on": "2026-07-28"}}
    body = check.evaluate([ENTRY], [], None, today=date(2026, 8, 4),
                          history=history)
    assert body["summary"]["regressions"] == 1
    assert "was held" in body["entries"][0]["regression"][0]


def test_a_regression_is_still_a_regression_when_the_amount_goes_wrong():
    history = {ENTRY["id"]: {"ever_stored": True, "ever_live": False}}
    body = check.evaluate([ENTRY], [row(funding_amount_usd=1)], None,
                          today=date(2026, 8, 4), history=history)
    assert body["summary"]["regressions"] == 1


def test_history_is_carried_forward_so_next_week_can_still_detect_it():
    body = check.evaluate([ENTRY], [row()], None, today=date(2026, 8, 4))
    assert body["history"][ENTRY["id"]]["ever_stored"] is True
    assert body["history"][ENTRY["id"]]["first_held_on"] == "2026-08-04"

    # ... and a week later, with the row gone, that memory is what reds it.
    later = check.evaluate([ENTRY], [], None, today=date(2026, 8, 11),
                           history=body["history"])
    assert later["summary"]["regressions"] == 1


def test_history_survives_a_report_written_by_an_older_shape():
    previous = {"entries": [{"id": ENTRY["id"], "ever_stored": True,
                             "ever_live": True}]}
    history = check.previous_history(previous)
    assert history[ENTRY["id"]]["ever_stored"] is True


# --- the two lenses ---------------------------------------------------------

def test_stored_and_live_can_disagree_and_the_disagreement_is_reported():
    """The 2026-08 defect in miniature: a correct row, quarantined before
    publication, invisible to every reader. A guard that only asked the
    database would have called this held."""
    body = check.evaluate([ENTRY], [row()], {"Anthropic": []},
                          today=date(2026, 8, 4))
    item = body["entries"][0]
    assert item["stored_verdict"] == check.HELD
    assert item["live_verdict"] == check.MISSING
    assert item["status"] == "held_not_live"
    assert body["summary"]["held_not_live"] == 1
    assert "stored but not live" in body["summary"]["one_line"]


def test_a_live_lens_that_did_not_run_is_unknown_and_never_a_regression():
    """A host outage must not manufacture twenty regressions and an email."""
    history = {ENTRY["id"]: {"ever_stored": True, "ever_live": True}}
    body = check.evaluate([ENTRY], [row()], None, today=date(2026, 8, 4),
                          history=history)
    assert body["entries"][0]["live_verdict"] == check.UNKNOWN
    assert body["summary"]["regressions"] == 0
    # and the memory that it was once live is NOT erased by not looking
    assert body["history"][ENTRY["id"]]["ever_live"] is True


def test_the_live_lens_asks_once_per_employer_not_once_per_landmark():
    asked = []

    def fetch(name):
        asked.append(name)
        return []

    entries = [ENTRY, dict(ENTRY, id="another", event_date="2026-02-12",
                           quarter="2026Q1")]
    check.live_rows(fetch, entries)
    assert asked == ["Anthropic"]


# --- the summary line the owner reads --------------------------------------

def test_the_one_line_reads_the_way_the_owner_asked_for_it():
    assert check.one_line(20, 17, 3, 0) == \
        "landmarks: 17 of 20 held, 3 standing gaps, 0 regressions"


def test_the_one_line_carries_counts_and_never_a_bare_percentage():
    line = check.one_line(20, 4, 16, 0, 2)
    assert "4 of 20" in line and "%" not in line


# --- the report is read, not just written ----------------------------------

def test_a_missing_or_undated_report_is_stale_rather_than_fine():
    assert check.report_is_stale(None, date(2026, 8, 4))
    assert check.report_is_stale({"checked_on": None}, date(2026, 8, 4))
    assert check.report_is_stale({"checked_on": "2026-07-01"}, date(2026, 8, 4))
    assert not check.report_is_stale({"checked_on": "2026-08-01"},
                                     date(2026, 8, 4))


def test_the_committed_report_exists_and_matches_the_committed_set():
    report = json.loads((ROOT / "data" / "landmarks_report.json").read_text())
    assert report["summary"]["total"] == len(landmarks.entries(landmarks.load()))
    assert report["landmarks_version"] == landmarks.load()["version"]
    assert set(report["history"]) == {
        e["id"] for e in landmarks.entries(landmarks.load())}


def test_ops_status_prints_the_landmark_section():
    """Wiring test. A number computed weekly and surfaced nowhere is the fourth
    way this guard dies."""
    source = (ROOT / "ops_status.py").read_text()
    assert "_report_landmarks" in source
    assert "problems += _report_landmarks(conn)" in source
    assert "[3d] LANDMARKS" in source


def test_the_weekly_digest_carries_the_landmark_line():
    source = (ROOT / "health_digest.py").read_text()
    assert "read_landmarks" in source
    assert "landmarks = read_landmarks()" in source
    # reported every week, not only when it is already bad
    assert "LANDMARKS  (largest disclosed round per quarter" in source
    # and a regression is what makes the email go out
    assert 'or bool(landmarks and landmarks["regressions"])' in source


def test_the_digest_reports_landmarks_even_on_a_clean_week():
    import health_digest

    buckets = {"ok": ["x"], "stale": [], "degraded": [], "unknown_age": []}
    landmark_summary = {
        "checked_on": "2026-08-04", "version": "2026-08-v1",
        "one_line": "landmarks: 17 of 20 held, 3 standing gaps, 0 regressions",
        "total": 20, "held": 17, "standing_gaps": 3, "regressions": 0,
        "held_not_live": 0, "live_lens": "read", "regressed": [],
        "biggest_gaps": [],
    }
    _, body = health_digest.build_email(
        buckets, False, 2.0, None, "test", [], None, False, landmark_summary)
    assert "landmarks: 17 of 20 held" in body


def test_a_landmark_regression_sets_the_subject():
    import health_digest

    buckets = {"ok": [], "stale": [], "degraded": [], "unknown_age": []}
    landmark_summary = {
        "checked_on": "2026-08-04", "version": "2026-08-v1",
        "one_line": "landmarks: 16 of 20 held, 3 standing gaps, 1 regression",
        "total": 20, "held": 16, "standing_gaps": 3, "regressions": 1,
        "held_not_live": 0, "live_lens": "read",
        "regressed": [{"company": "Anthropic", "quarter": "2026Q2",
                       "amount_usd": 65000000000, "why": "stored: was held",
                       "source_url": "https://www.anthropic.com/news/series-h"}],
        "biggest_gaps": [],
    }
    subject, body = health_digest.build_email(
        buckets, False, 2.0, None, "test", [], None, False, landmark_summary)
    assert "landmark" in subject.lower()
    assert "LANDMARK REGRESSION" in body


def test_the_landmark_package_imports_nothing_beyond_the_standard_library():
    """ops_status runs before any venv exists and promises no dependencies.

    It now recomputes the landmark check at session start, so this package is
    on that path and inherits the promise. tests/test_health_digest.py allows
    `analysis` in ops_status's import set on the strength of this assertion.
    """
    import ast

    allowed = {
        "__future__", "annotations", "hashlib", "json", "os", "re",
        "datetime", "sqlite3", "typing",
        # the recall matcher, itself stdlib-only, so employer names normalise
        # exactly once in this repository
        "analysis",
    }
    for name in ("__init__.py", "landmarks.py", "check.py"):
        tree = ast.parse((ROOT / "analysis" / "landmarks" / name).read_text())
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        assert not (imports - allowed), (name, imports - allowed)


# --- the workflow -----------------------------------------------------------

def _workflow():
    import yaml
    return yaml.safe_load((ROOT / ".github/workflows/landmarks.yml").read_text())


def test_the_weekly_workflow_exists_and_is_weekly():
    data = _workflow()
    # PyYAML parses the bare key `on` as the boolean True.
    triggers = data.get("on") or data.get(True)
    crons = [s["cron"] for s in triggers["schedule"]]
    assert crons == ["0 9 * * 1"], crons


def test_the_workflow_validates_the_set_before_measuring_against_it():
    text = (ROOT / ".github/workflows/landmarks.yml").read_text()
    assert "check_landmarks.py --check" in text
    assert text.index("--check") < text.index("--write")


def test_the_workflow_commits_the_report_and_not_the_database():
    """It reads the corpus read-only and writes one snapshot file. If it ever
    grows into a database writer it must join the shared lock, and this is
    where that decision gets caught."""
    text = (ROOT / ".github/workflows/landmarks.yml").read_text()
    assert "data/landmarks_report.json" in text
    assert "talent_intel.db" not in text
    assert "merge_db" not in text


def test_the_workflow_commits_the_report_even_when_the_check_reds():
    """A regression that reds the run must still leave its evidence committed,
    or next week has no history to compare against and the regression quietly
    becomes the new normal."""
    text = (ROOT / ".github/workflows/landmarks.yml").read_text()
    assert "continue-on-error: true" in text
    assert "if: ${{ !cancelled() }}" in text


def test_the_workflow_spends_nothing():
    text = (ROOT / ".github/workflows/landmarks.yml").read_text()
    assert "OPENROUTER_API_KEY" not in text

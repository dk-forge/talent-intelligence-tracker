"""A day a historical walker has finished is not a day we could not reach.

Until the walkers existed, "no live route reached this date" and "nothing has
been here" were the same statement. They stopped being the same on 2026-07-30,
and the difference is the whole answer: an unreached day is closed by
dispatching slices, and a day a rationed walker already finished is closed by
buying depth. The first fix walks straight past the second's events.

Everything here is synthetic and offline, like test_rejection_audit.py, and the
two share the same catalogue so the buckets stay comparable.
"""

import json
from datetime import date

from analysis.recall import rejection_audit as audit

CATALOGUE = {
    "swept": {"swept-outlet.com": "Swept Outlet"},
    "known": {"swept-outlet.com": "Swept Outlet",
              "researched.co.il": "Researched Daily"},
    "backstop_countries": {"FJ"},
    "rows": 2,
}
FIRST_RUN = {"google_news": date(2026, 7, 27), "national_press": date(2026, 7, 29)}


def place(url, event_date, *, walked=None, seen=None, cited=None):
    return audit.classify_miss(
        {"id": "x", "country": "US", "signal_type": "funding",
         "event_date": event_date, "source_name": "Outlet", "source_url": url},
        seen=seen or {}, cited=cited or {}, catalogue=CATALOGUE,
        by_domain={}, first_run=FIRST_RUN, feed_backlog_days=3, walked=walked)


# --- the new bucket ----------------------------------------------------------

def test_a_day_no_walker_has_reached_is_still_a_history_problem():
    got = place("https://swept-outlet.com/a", "2026-07-01",
                walked={"google_news": date(2026, 6, 20)})

    assert got["stage"] == "outside_our_history"
    assert got["walkers_past_this_date"] == []


def test_a_day_a_walker_has_finished_is_a_budget_problem_instead():
    """Same event, same date, same live routes. The only thing that moved is a
    cursor on disk, and that is enough to change which work closes it."""
    got = place("https://swept-outlet.com/a", "2026-07-01",
                walked={"google_news": date(2026, 7, 12)})

    assert got["stage"] == "walked_never_read"
    assert got["walkers_past_this_date"] == ["google_news"]
    assert audit.ANSWER[got["stage"]].startswith("budget")


def test_the_roster_walker_cannot_reach_a_publisher_the_catalogue_lacks():
    """press_archive reads the sitemaps of catalogue publishers and nobody
    else's, so its cursor says nothing about a publisher we have never heard
    of. Google News is a search and carries no such restriction."""
    unknown = "https://nobody-knows-this.example/a"
    walked = {"press_archive": date(2026, 7, 30)}

    assert place(unknown, "2026-07-01", walked=walked)["stage"] == \
        "outside_our_history"
    assert place("https://researched.co.il/a", "2026-07-01",
                 walked=walked)["stage"] == "walked_never_read"
    assert place(unknown, "2026-07-01",
                 walked={"google_news": date(2026, 7, 30)})["stage"] == \
        "walked_never_read"


def test_a_fetched_document_still_beats_any_cursor():
    """Ordering is pinned for the same reason it already was: a URL we resolved
    is an observation, and a cursor is an inference about a day."""
    got = place("https://swept-outlet.com/a", "2026-07-01",
                walked={"google_news": date(2026, 7, 12)},
                seen={"https://swept-outlet.com/a": ("google_news", "rejected")})

    assert got["stage"] == "fetched_then_dropped"


def test_a_live_route_still_beats_a_cursor():
    """Inside the live window the finding is about the live route, not about a
    walk that also happens to cover the date."""
    got = place("https://swept-outlet.com/a", "2026-07-27",
                walked={"google_news": date(2026, 7, 30)})

    assert got["stage"] == "feed_read_item_missed"


# --- reading the cursors -----------------------------------------------------

def test_a_day_walker_is_finished_through_the_day_before_its_cursor(tmp_path):
    """The cursor is the NEXT day to walk. Reading it as the last day done
    would credit the walker with a day it has not started."""
    path = tmp_path / "backfill_state.json"
    path.write_text(json.dumps({"jobs": {
        "backfill-gnews-2026:2026-01-01..2026-07-26": {
            "unit": "days", "state": "running", "cursor": "2026-07-13",
            "start": "2026-01-01", "end": "2026-07-26"},
    }}))

    assert audit.load_walked(path) == {"google_news": date(2026, 7, 12)}


def test_a_finished_day_walker_is_credited_to_its_declared_end(tmp_path):
    path = tmp_path / "backfill_state.json"
    path.write_text(json.dumps({"jobs": {
        "backfill-gdelt-2026:2026-01-01..2026-06-30": {
            "unit": "days", "state": "done", "cursor": None,
            "start": "2026-01-01", "end": "2026-06-30"},
    }}))

    assert audit.load_walked(path) == {"gdelt": date(2026, 6, 30)}


def test_a_roster_walker_counts_only_once_the_whole_roster_is_done(tmp_path):
    """backfill_press_2026 walks publishers and takes the date range as a fixed
    input, because a sitemap costs the same fetch for one day as for six
    months. A half-walked roster has covered the window for nobody."""
    running = {"unit": "slices", "state": "running", "cursor": "4",
               "inputs": {"start": "2026-01-01", "end": "2026-07-30"}}
    path = tmp_path / "backfill_state.json"
    path.write_text(json.dumps({"jobs": {
        "backfill-press-2026:2026-01-01..2026-07-30:0..16": running}}))
    assert audit.load_walked(path) == {}

    path.write_text(json.dumps({"jobs": {
        "backfill-press-2026:2026-01-01..2026-07-30:0..16":
            dict(running, state="done")}}))
    assert audit.load_walked(path) == {"press_archive": date(2026, 7, 30)}


def test_the_furthest_walk_wins_when_a_route_has_several_jobs(tmp_path):
    path = tmp_path / "backfill_state.json"
    path.write_text(json.dumps({"jobs": {
        "backfill-gnews-2026:a": {"unit": "days", "state": "done",
                                  "end": "2026-01-24"},
        "backfill-gnews-2026:b": {"unit": "days", "state": "running",
                                  "cursor": "2026-07-13"},
    }}))

    assert audit.load_walked(path) == {"google_news": date(2026, 7, 12)}


def test_a_missing_or_broken_state_file_credits_nothing(tmp_path):
    """Absence of a walker record is not evidence that a day was walked. It
    must fall back to the live-route answer, never to a free pass."""
    assert audit.load_walked(tmp_path / "nope.json") == {}
    broken = tmp_path / "backfill_state.json"
    broken.write_text("{not json")
    assert audit.load_walked(broken) == {}
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"jobs": {
        "backfill-gnews-2026:x": {"unit": "days", "state": "running",
                                  "cursor": None}}}))
    assert audit.load_walked(empty) == {}


def test_a_walker_this_module_does_not_know_is_not_credited(tmp_path):
    path = tmp_path / "backfill_state.json"
    path.write_text(json.dumps({"jobs": {
        "backfill-something-else:x": {"unit": "days", "state": "done",
                                      "end": "2026-07-30"}}}))

    assert audit.load_walked(path) == {}


# --- naming the rule that refused it -----------------------------------------

def drop(url, *, gate_outcomes=None):
    return audit.classify_miss(
        {"id": "x", "country": "US", "signal_type": "funding",
         "event_date": "2026-07-25", "source_name": "Outlet",
         "source_url": url},
        seen={url: ("google_news", "rejected")}, cited={}, catalogue=CATALOGUE,
        by_domain={}, first_run=FIRST_RUN, feed_backlog_days=3,
        gate_outcomes=gate_outcomes)


def test_a_dropped_item_the_gate_ledger_saw_names_the_stage_and_the_rule():
    """`gate_ledger.key()` is a sha1 of the same URL `seen_urls` keys on, so
    the two join with nothing new on either side. Until they were joined, the
    one bucket that means "loosen something" could not say what to loosen."""
    url = "https://swept-outlet.com/a"

    bare = drop(url)
    assert bare["stage"] == "fetched_then_dropped"
    assert "dropped_at" not in bare

    got = drop(url, gate_outcomes={
        audit.url_key(url): ("validate_reject", "no country in the text")})
    assert got["dropped_at"] == "validate_reject"
    assert got["dropped_because"] == "no country in the text"


def test_a_ledger_line_with_no_reason_still_names_the_stage():
    """Most stored lines carry no reason, and half an answer beats none."""
    url = "https://swept-outlet.com/a"
    got = drop(url, gate_outcomes={audit.url_key(url): ("model_reject", "")})

    assert got["dropped_at"] == "model_reject"
    assert "dropped_because" not in got


def test_a_ledger_line_that_only_echoes_seen_urls_is_not_an_attribution():
    """`bootstrap_gate_labels.py` back-filled the ledger FROM seen_urls to give
    the classifier a weak training set, so those lines say `rejected` and
    nothing more. Echoing one as `dropped_at: rejected` would dress this
    module's oldest limit up as an answer, and it is how the one bucket that
    means "loosen something" would stop being read."""
    url = "https://swept-outlet.com/a"
    got = drop(url, gate_outcomes={audit.url_key(url): ("rejected", "")})

    assert "dropped_at" not in got


def test_the_last_line_for_a_key_wins(tmp_path):
    """A deferred candidate is gated again on a later run and writes a second
    line under the same key. The last terminal outcome is the real one."""
    (tmp_path / "labels-2026-08.jsonl").write_text(
        json.dumps({"key": "abc", "outcome": "deferred"}) + "\n"
        + json.dumps({"key": "abc", "outcome": "stored", "reason": "r"}) + "\n")

    assert audit.load_gate_outcomes(tmp_path) == {"abc": ("stored", "r")}


def test_a_missing_or_broken_ledger_attributes_nothing(tmp_path):
    assert audit.load_gate_outcomes(tmp_path / "nope") == {}
    (tmp_path / "labels.jsonl").write_text(
        "{not json\n" + json.dumps({"key": "k"}) + "\n")
    assert audit.load_gate_outcomes(tmp_path) == {}


def test_the_url_key_matches_the_gate_ledger_that_writes_it():
    """Duplicated for the same reason match.py duplicates the company key, so
    it is asserted against the real one rather than assumed."""
    from pipeline import gate_ledger

    url = "https://swept-outlet.com/a"
    assert audit.url_key(url) == gate_ledger.key({"source_url": url})

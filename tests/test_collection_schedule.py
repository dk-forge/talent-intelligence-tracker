"""Every configured source is known, a missed slot is caught up, a never-run
source is visible, and a source storing nothing gets one issue (coverage audit
2026-09-24, self-heal slice).

The schedule is read from the REAL workflow files, so these tests also pin
that the parser understands them: a source added to collect-structured.yml's
case map is enumerated with no second list to update.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import collection_schedule as cs

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE source_health (collector TEXT, status TEXT, "
                 "items_found INT, items_stored INT, detail TEXT, run_at TEXT)")
    conn.executemany("INSERT INTO source_health VALUES (?,?,?,?,?,?)", rows)
    return conn


def _ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


def test_every_configured_source_is_enumerated_from_the_workflows():
    srcs = cs.configured_sources()
    for name in ("google_news", "gdelt", "sec_edgar", "sec_form_d", "national_press",
                 "ats_boards", "bse_india", "edinet_japan", "opendart_korea",
                 "companies_house", "czechia_ares", "estonia_ariregister",
                 "spain_borme", "sec_execcomp", "uk_paygap", "singapore_acra"):
        assert name in srcs, name
    assert srcs["edinet_japan"].workflow == "collect-structured.yml"
    assert srcs["edinet_japan"].cadence_hours == 168
    assert srcs["singapore_acra"].cadence_hours >= 24 * 28
    assert srcs["ats_boards"].cadence_hours == 24
    assert srcs["google_news"].workflow == "collect.yml"
    # Dormant by design: no cron, so not configured, so never a false alarm.
    assert "denmark_cvr" not in srcs and "israel_registrar" not in srcs


def test_cadence_is_read_from_the_cron_shape():
    assert cs.cadence_hours("0 4 * * 2") == 168
    assert cs.cadence_hours("30 10 7 * *") == 24 * 31
    assert cs.cadence_hours("0 22 * * *") == 24


def test_a_missed_weekly_slot_is_planned_for_catch_up():
    conn = _db([("edinet_japan", "ok", 5, 2, "", _ago(9 * 24)),
                ("bse_india", "ok", 5, 2, "", _ago(3 * 24)),
                ("companies_house", "ok", 5, 2, "", _ago(7 * 24 + 23))])
    plan = cs.catch_up_plan(conn, now=NOW, limit=10)
    assert "edinet_japan" in plan
    assert "bse_india" not in plan
    # Inside cadence + one day of slack: not yet a miss.
    assert "companies_house" not in plan


def test_a_structured_source_that_never_ran_is_caught_up_and_reported():
    conn = _db([("bse_india", "ok", 5, 2, "", _ago(24))])
    assert "singapore_acra" in cs.catch_up_plan(conn, now=NOW, limit=20)
    assert "singapore_acra" in cs.never_ran(conn)
    assert "bse_india" not in cs.never_ran(conn)


def test_catch_up_is_bounded_and_skips_the_daily_news_sweep():
    conn = _db([])
    plan = cs.catch_up_plan(conn, now=NOW, limit=2)
    assert len(plan) == 2
    full = cs.catch_up_plan(conn, now=NOW, limit=50)
    # Daily sources re-run tomorrow anyway, and collect.yml is not a
    # single-source dispatch target for them; the catch-up is for slots that
    # otherwise wait a week or a month.
    assert "google_news" not in full and "ats_boards" not in full
    # Longest-overdue first is not knowable for never-run sources, so the
    # order is stable: weekly before monthly.
    assert full.index("edinet_japan") < full.index("singapore_acra")


def test_zero_store_run_streak_counts_runs_not_days():
    conn = _db([("companies_house", "ok", 10, 0, "", _ago(24 * k)) for k in (1, 8, 15)]
               + [("bse_india", "ok", 10, 0, "", _ago(24)),
                  ("bse_india", "ok", 10, 4, "", _ago(24 * 8))])
    streaks = cs.zero_store_sources(conn, runs=3)
    assert "companies_house" in streaks and streaks["companies_house"] == 3
    assert "bse_india" not in streaks


def test_issue_sync_opens_one_per_source_and_closes_on_recovery():
    calls = []

    class FakeGh:
        def open_or_update(self, repo, **kw):
            calls.append(("open", kw["marker"]))
            return True, "ok"

        def close(self, repo, **kw):
            calls.append(("close", kw["marker"]))
            return True, "ok"

        def find_open(self, repo, *, marker):
            return (7 if "recovered_src" in marker else None), "", ""

    cs.sync_zero_store_issues({"companies_house": 3}, ["companies_house", "recovered_src"],
                              repo="o/r", gh=FakeGh())
    assert ("open", cs.issue_marker("companies_house")) in calls
    assert ("close", cs.issue_marker("recovered_src")) in calls
    assert all(m != cs.issue_marker("companies_house") for k, m in calls if k == "close")


def test_ops_status_names_a_configured_source_that_never_ran():
    import ops_status
    conn = _db([("bse_india", "ok", 5, 2, "", NOW.isoformat())])
    conn.row_factory = sqlite3.Row
    problems = ops_status._report_health(conn)
    assert any("singapore_acra" in p and "never" in p for p in problems), problems


def test_catch_up_goes_through_the_writer_queue_never_a_direct_dispatch():
    """A direct dispatch into the talent-collect lock can displace the one
    pending run (writer_queue.py). The catch-up writes tickets instead, one
    per missed source, and never a second ticket for a source already waiting.
    """
    import writer_queue
    queue = writer_queue.empty_queue()
    added = cs.enqueue_catch_up(queue, ["edinet_japan", "opendart_korea"], now=NOW)
    assert added == ["edinet_japan", "opendart_korea"]
    tickets = queue["tickets"]
    assert {t["workflow"] for t in tickets} == {"collect-structured.yml"}
    assert {t["inputs"]["source"] for t in tickets} == {"edinet_japan", "opendart_korea"}
    assert len({t["id"] for t in tickets}) == 2
    assert cs.enqueue_catch_up(queue, ["edinet_japan"], now=NOW) == []
    tickets[0]["state"] = "landed"
    assert cs.enqueue_catch_up(queue, ["edinet_japan"], now=NOW) == ["edinet_japan"]

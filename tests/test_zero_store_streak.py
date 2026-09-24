"""A primary collector that stores nothing for days is an outage, not a quiet week.

google_news stored 0 rows on every run from 2026-09-17 to 2026-09-21 (it had
stored 29-81 a day before), each run marked `degraded` with "every candidate
rejected". ops_status only ever looks at the LATEST row per collector, so a run
of five dead days read exactly like one bad night. This pins the streak check:
three consecutive UTC days with runs and zero rows stored names the collector.
"""

from pipeline import schema, store

import staleness


def _runs(*pairs):
    return [(f"2026-09-{d:02d}T23:00:00+00:00", s) for d, s in pairs]


def test_three_dead_days_is_a_streak():
    assert staleness.zero_store_streak_days(_runs((17, 0), (18, 0), (19, 0))) == 3


def test_a_single_stored_row_breaks_the_streak():
    assert staleness.zero_store_streak_days(_runs((17, 0), (18, 4), (19, 0))) == 1


def test_a_day_with_any_storing_run_is_not_dead():
    runs = _runs((18, 0), (19, 0)) + [("2026-09-19T08:00:00+00:00", 2)]
    assert staleness.zero_store_streak_days(runs) == 0


def test_no_runs_is_no_streak():
    assert staleness.zero_store_streak_days([]) == 0


def test_primary_collectors_named():
    assert {"google_news", "gdelt", "national_press"} <= set(staleness.PRIMARY_COLLECTORS)
    # The SEC pair legitimately stores nothing on a weekend; it is not primary.
    assert "sec_form_d" not in staleness.PRIMARY_COLLECTORS


def test_ops_status_names_the_streak(tmp_path):
    import ops_status

    conn = schema.connect(tmp_path / "t.db")
    for day in (17, 18, 19):
        store.report_health(conn, "google_news", status="degraded",
                            items_found=900, items_stored=0,
                            detail="every candidate rejected")
        conn.execute("UPDATE source_health SET run_at=? WHERE rowid=last_insert_rowid()",
                     (f"2026-09-{day:02d}T23:00:00+00:00",))
    conn.commit()
    problems = ops_status._report_health(conn)
    assert any("ZERO-STORE STREAK" in p and "google_news" in p for p in problems)


def test_ops_status_quiet_when_rows_stored(tmp_path):
    import ops_status

    conn = schema.connect(tmp_path / "t.db")
    for day, n in ((17, 0), (18, 0), (19, 5)):
        store.report_health(conn, "google_news", status="ok",
                            items_found=900, items_stored=n, detail="x")
        conn.execute("UPDATE source_health SET run_at=? WHERE rowid=last_insert_rowid()",
                     (f"2026-09-{day:02d}T23:00:00+00:00",))
    conn.commit()
    problems = ops_status._report_health(conn)
    assert not any("ZERO-STORE STREAK" in p for p in problems)

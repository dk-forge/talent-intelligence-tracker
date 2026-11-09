"""Backfilling a country onto rows written before pipeline/place_fallback.py.

PR #311 made build_signal place an unplaced row only when two independent
signals agree and nothing on the row disagrees. Rows written before it stay
blank. This pass gives those rows the SAME decision, by calling the same
function, and nothing looser:

- one signal alone never places a row (the 2026-09-02 refusal stands);
- any disagreement leaves the row blank, and the dry run says why;
- the revision is appended, never written over, and content_hash does not move;
- the site is corrected in place first, then the revision records it.
"""

from __future__ import annotations

import sqlite3

import pytest

import correct_unplaced_country as backfill
from pipeline import place_fallback, schema, store, validate

INDONESIAN = "PhinCon membuka lowongan kerja untuk lulusan baru tahun ini"
BOSNIAN = "Austro Panel otvara novu fabriku u BiH"


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def wp_config(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "test-key")


def stored_in(conn, *, headline, source_url, hq_country=None,
              published_date="2026-11-05", published=True, country=None,
              company="PhinCon"):
    """A real row via build_signal, then the blank history left on it."""
    read = {
        "company": company, "pillar": "leadership_change",
        "signal_direction": "neutral", "confidence": "reported",
        "headline": headline, "summary": "Hiring.", "talent_readthrough": "Hiring.",
    }
    raw = {"raw_text": f"{company}. {headline}", "headline": headline, "source_url": source_url,
           "source_name": "Outlet", "published_date": published_date}
    signal = validate.build_signal(read, raw, "press")
    assert store.store(conn, signal) == "stored"
    conn.execute(
        "UPDATE signals SET country = ?, region = NULL, hq_country = ?, "
        "published_at = ? WHERE signal_id = ?",
        (country, hq_country, "2026-11-05 10:00:00" if published else None,
         signal.signal_id))
    conn.commit()
    return signal


def live(conn, signal_id) -> dict:
    conn.row_factory = sqlite3.Row
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


class Recorder:
    """Stands in for the site's /correct."""

    def __init__(self):
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})

        class Resp:
            status_code = 200
            text = "recorded"

            def json(self_inner):
                return {"corrected": 1, "skipped_no_fields": 0, "errors": []}

        return Resp()


# --- the decision ------------------------------------------------------------

def test_publisher_and_language_agreeing_place_the_row(conn):
    stored_in(conn, headline=INDONESIAN, source_url="https://www.liputan6.com/a")
    placed, left = backfill.worklist(backfill.current_rows(conn))
    assert len(placed) == 1 and left == []
    _row, fixed, ev = placed[0]
    assert fixed["country"] == "ID"
    assert fixed["region"]
    assert ev["publisher"] == "ID" and ev["language"] == "ID"


def test_publisher_and_headline_agreeing_place_the_row(conn):
    stored_in(conn, headline=BOSNIAN, source_url="https://biznisinfo.ba/a")
    placed, _left = backfill.worklist(backfill.current_rows(conn))
    assert placed[0][1]["country"] == "BA"
    assert placed[0][2]["headline"] == "BA"


def test_one_signal_alone_never_places_a_row(conn):
    """An English headline on a .de host: the ccTLD is one weak signal."""
    stored_in(conn, headline="Acme hires 200 engineers in new office",
              source_url="https://example.de/a")
    placed, left = backfill.worklist(backfill.current_rows(conn))
    assert placed == []
    assert left[0][1] == "one signal only, not corroborated"


def test_a_disagreeing_hq_leaves_it_blank_and_says_why(conn):
    stored_in(conn, headline=INDONESIAN, source_url="https://www.liputan6.com/a",
              hq_country="US")
    placed, left = backfill.worklist(backfill.current_rows(conn))
    assert placed == []
    assert left[0][1] == "signals disagree"


def test_two_countries_in_the_headline_leave_it_blank(conn):
    stored_in(conn, headline="Acme moves jobs from Germany to Poland",
              source_url="https://example.pl/a")
    _placed, left = backfill.worklist(backfill.current_rows(conn))
    assert left[0][1] == "headline names more than one country"


def test_no_signal_at_all_is_reported(conn):
    stored_in(conn, headline="Acme hires 200 engineers",
              source_url="https://example.com/a")
    _placed, left = backfill.worklist(backfill.current_rows(conn))
    assert left[0][1] == "no signal"


def test_the_decision_is_exactly_place_fallback_decide(conn):
    """No second opinion: the backfill must place what build_signal would."""
    for name, url, head, hq in [
            ("PhinCon", "https://www.liputan6.com/a", INDONESIAN, None),
            ("Telkom", "https://www.liputan6.com/b", INDONESIAN, "US"),
            ("Acme", "https://example.de/c", "Acme hires", None)]:
        stored_in(conn, headline=head, source_url=url, hq_country=hq, company=name)
    rows = backfill.current_rows(conn)
    placed, _ = backfill.worklist(rows)
    got = {r["signal_id"]: f["country"] for r, f, _e in placed}
    for row in rows:
        want = place_fallback.decide(
            source_url=row["source_url"], headline=row["headline"],
            language=backfill.headline_language(row["headline"]),
            hq_country=row["hq_country"])
        assert got.get(row["signal_id"]) == want


def test_placed_rows_and_rows_outside_the_window_are_not_targets(conn):
    stored_in(conn, headline=INDONESIAN, source_url="https://www.liputan6.com/a",
              country="ID")
    stored_in(conn, headline=INDONESIAN, source_url="https://www.liputan6.com/b",
              published_date="2026-10-01", company="Telkom")
    assert backfill.current_rows(conn) == []


def test_an_implausible_worklist_stops_the_run(conn, monkeypatch):
    monkeypatch.setattr(backfill, "MAX_ROWS", 0)
    stored_in(conn, headline=INDONESIAN, source_url="https://www.liputan6.com/a")
    with pytest.raises(backfill.Unsafe):
        backfill.worklist(backfill.current_rows(conn))


# --- the write ---------------------------------------------------------------

def test_apply_corrects_the_site_then_appends_a_revision(conn):
    signal = stored_in(conn, headline=INDONESIAN,
                       source_url="https://www.liputan6.com/a")
    before = live(conn, signal.signal_id)
    (row, fixed, _ev), = backfill.worklist(backfill.current_rows(conn))[0]
    site = Recorder()
    backfill.reissue(conn, row, fixed,
                     push=lambda r, f: backfill.push_place(r, f, session=site))

    after = live(conn, signal.signal_id)
    assert after["country"] == "ID"
    assert after["revision"] == before["revision"] + 1
    assert after["content_hash"] == before["content_hash"]
    assert after["published_at"] == before["published_at"]
    assert after["notes"] == backfill.NOTE
    old = conn.execute("SELECT is_current FROM signals WHERE row_id = ?",
                       (before["row_id"],)).fetchone()[0]
    assert old == 0
    assert site.calls[0]["url"].endswith("/wp-json/talent/v1/correct")
    sent = site.calls[0]["json"]["rows"][0]
    assert sent["country"] == "ID" and sent["content_hash"] == before["content_hash"]
    # A second pass finds nothing: idempotent.
    assert backfill.worklist(backfill.current_rows(conn))[0] == []


def test_dry_run_writes_nothing(conn, monkeypatch, capsys):
    signal = stored_in(conn, headline=INDONESIAN,
                       source_url="https://www.liputan6.com/a")
    monkeypatch.setattr(backfill.schema, "connect", lambda *a, **k: conn)
    assert backfill.main([]) == 0
    out = capsys.readouterr().out
    assert "'ID'" in out and "publisher=ID" in out and "language=ID" in out
    assert live(conn, signal.signal_id)["country"] is None


def test_the_workflow_passes_a_since_until_window_through_the_environment():
    """Batched runs need --since/--until from the dispatch, never interpolated
    into the shell, and an empty input must keep the script's own default."""
    yaml = pytest.importorskip("yaml")
    from pathlib import Path

    wf = yaml.safe_load((Path(__file__).parent.parent
                         / ".github/workflows/correct-unplaced-country.yml").read_text())
    triggers = wf.get("on") or wf.get(True)
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    for name in ("since", "until"):
        assert inputs[name]["required"] is False
    step = next(s for job in wf["jobs"].values() for s in job.get("steps", [])
                if "correct_unplaced_country.py" in (s.get("run") or ""))
    assert step["env"]["SINCE"] == "${{ inputs.since }}"
    assert step["env"]["UNTIL"] == "${{ inputs.until }}"
    assert "inputs.since" not in step["run"] and "inputs.until" not in step["run"]
    assert '--since "$SINCE"' in step["run"] and '--until "$UNTIL"' in step["run"]

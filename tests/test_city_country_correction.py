"""Re-filing the rows the city gazetteer contradicts.

Toronto was mapped to the United States when these rows were written, and
`build_signal` takes the country FROM the city table on purpose, so the rows
went onto the live site under the US country filter. Five properties matter, and
each one is a way this pass could do damage rather than a correction.

The worklist is DERIVED from the vocabulary and then checked against a shape, so
it finds rows nobody listed and refuses rows nobody decided about.

The revision is appended, never written over: `is_current = 0` survives, because
"what did we publish on 2026-07-28" has to stay answerable.

The content_hash must NOT move. It is the reason the site correction can be an
in-place UPDATE, and includes/db.php refuses any hash it has already seen at any
revision — so a pass that moved the hash would delete two live records and call
it a duplicate.

The site is corrected BEFORE the database, because the local revision is the
only record that the site was corrected.

And a deployed plugin that cannot accept the fields makes the whole pass refuse
rather than leaving a corrected database behind a wrong page.
"""

from __future__ import annotations

import pytest

import correct_city_country as correct
from pipeline import publish, schema, store, validate

FILING = (
    "Celestica Inc., headquartered in Toronto, reported that Michael M. Wilson, "
    "Chair of the Board of Directors, will not stand for re-election at the 2026 "
    "annual meeting of shareholders."
)


def read(**over):
    base = {
        "company": "Celestica Inc.",
        "pillar": "leadership_change",
        "signal_direction": "neutral",
        "confidence": "verified",
        "city": "Toronto",
        "headquarters_city": "Toronto",
        "headline": "CELESTICA INC 8-K filing (Item 5.02): officer or director change",
        "summary": "The chair of the board will not stand for re-election.",
        "talent_readthrough": "Board leadership changes in Toronto.",
    }
    base.update(over)
    return base


def raw(**over):
    base = {
        "raw_text": FILING,
        "headline": read()["headline"],
        "source_url": "https://www.sec.gov/Archives/edgar/data/1030894/x/tm269620d1_8k.htm",
        "source_name": "SEC EDGAR",
        "published_date": "2026-03-24",
    }
    base.update(over)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def stored_in(conn, *, country, hq_country="US", published=True,
             state=None, **over):
    """A row as history left it: correct when it was written, wrong now.

    Built through build_signal so it is a real row, then the geography the OLD
    gazetteer produced is written back. That is what these two rows are — the
    table changed underneath them, not the collector.
    """
    signal = validate.build_signal(read(**over), raw(**over), "sec_edgar")
    assert store.store(conn, signal) == "stored"
    conn.execute(
        "UPDATE signals SET country = ?, hq_country = ?, state = ?, "
        "       published_at = ? WHERE signal_id = ?",
        (country, hq_country, state,
         "2026-07-28 15:00:51" if published else None, signal.signal_id))
    conn.commit()
    return signal


def live(conn, signal_id) -> dict:
    conn.row_factory = __import__("sqlite3").Row
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


class Recorder:
    """Stands in for the site. Never a stubbed module — see CLAUDE.md."""

    def __init__(self, response=None, status=200):
        self.calls: list[dict] = []
        self._response = response if response is not None else {
            "corrected": 1, "unchanged_or_missing": 0,
            "skipped_no_fields": 0, "errors": []}
        self._status = status

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        recorder = self

        class Resp:
            status_code = recorder._status
            text = "recorded"

            def json(self_inner):
                return recorder._response

        return Resp()


@pytest.fixture(autouse=True)
def wp_config(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "test-key")


# --- the worklist ------------------------------------------------------------

def test_it_finds_the_row_nobody_listed(conn):
    stored_in(conn, country="US")
    found = correct.targets(correct.current_rows(conn))
    assert len(found) == 1
    _row, fixed = found[0]
    assert fixed["country"] == "CA"


def test_a_row_already_filed_correctly_is_not_a_target(conn):
    """Which is what makes a second run a no-op."""
    stored_in(conn, country="CA", hq_country="CA")
    assert correct.targets(correct.current_rows(conn)) == []


def test_the_hq_half_moves_with_it(conn):
    stored_in(conn, country="US", hq_country="US")
    _row, fixed = correct.targets(correct.current_rows(conn))[0]
    assert fixed["hq_country"] == "CA"


def test_a_state_facet_cannot_survive_leaving_the_us(conn):
    """`state` is only meaningful inside the US, and build_signal only ever sets
    it when the country is US. A row keeping one would sit in the state filter's
    denominator forever."""
    stored_in(conn, country="US", state="NY")
    _row, fixed = correct.targets(correct.current_rows(conn))[0]
    assert fixed["state"] is None


def test_a_shape_nobody_decided_about_stops_the_run(conn):
    """A vocabulary edit that contradicts a third city is a decision for a
    person. Silently re-filing it is how a correction pass becomes a rewrite."""
    stored_in(conn, country="US", city="Dublin", headquarters_city="Dublin")
    with pytest.raises(correct.Unsafe) as exc:
        correct.targets(correct.current_rows(conn))
    assert "'Dublin' 'US'" in str(exc.value)


def test_the_accepted_shape_is_exactly_the_measured_one():
    assert correct.ACCEPTED_SHAPES == {("Toronto", "US"): "CA"}


def test_an_implausible_worklist_stops_the_run(conn, monkeypatch):
    monkeypatch.setattr(correct, "MAX_ROWS", 0)
    stored_in(conn, country="US")
    with pytest.raises(correct.Unsafe) as exc:
        correct.targets(correct.current_rows(conn))
    assert "ceiling" in str(exc.value)


def test_an_hq_only_row_is_reported_and_never_touched(conn):
    """Its job location is right, so it is not this pass's row. Saying nothing
    about it would read as "the defect is gone"."""
    stored_in(conn, country="CA", hq_country="US")
    rows = correct.current_rows(conn)
    assert correct.targets(rows) == []
    assert len(correct.hq_only_rows(rows)) == 1


# --- the corrected row -------------------------------------------------------

def test_only_the_geography_moves(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    fixed = correct.place_correction(row)
    corrected = correct.corrected_signal(row, fixed)

    assert corrected.country == "CA"
    assert corrected.city == "Toronto"
    assert corrected.headline == row["headline"]
    assert corrected.summary == row["summary"]
    assert corrected.talent_readthrough == row["talent_readthrough"]
    assert corrected.source_url == row["source_url"]
    assert corrected.confidence == row["confidence"]


def test_the_fingerprint_must_not_move(conn):
    """The whole reason the site correction can be an in-place UPDATE."""
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    corrected = correct.corrected_signal(row, correct.place_correction(row))
    assert corrected.content_hash == row["content_hash"]


def test_a_field_that_moved_the_hash_would_refuse(conn):
    """If content_hash ever starts reading one of these columns, this pass is the
    wrong shape and has to say so rather than corrupt a live row."""
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    row["content_hash"] = "0" * 32
    with pytest.raises(correct.Unsafe) as exc:
        correct.corrected_signal(row, correct.place_correction(row))
    assert "content_hash" in str(exc.value)


# --- the revision ------------------------------------------------------------

def test_the_original_survives_the_correction(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    site = Recorder()

    correct.reissue(conn, row, correct.place_correction(row),
                    push=lambda r, f: correct.push_place(r, f, session=site))

    history = conn.execute(
        "SELECT revision, is_current, country FROM signals "
        " WHERE signal_id = ? ORDER BY revision", (signal.signal_id,)).fetchall()
    assert [tuple(r) for r in history] == [(1, 0, "US"), (2, 1, "CA")]


def test_the_revision_says_why(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    correct.reissue(conn, row, correct.place_correction(row),
                    push=lambda r, f: None)
    assert "gazetteer" in live(conn, signal.signal_id)["notes"]


def test_the_revision_is_not_offered_to_publish_again(conn):
    """The site holds this revision's geography after an in-place correction, so
    a NULL published_at would send it, get 'duplicate' back on a hash the site
    already has, and read like a lost row in the log."""
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    correct.reissue(conn, row, correct.place_correction(row),
                    push=lambda r, f: None)
    assert live(conn, signal.signal_id)["published_at"] == "2026-07-28 15:00:51"
    assert publish.unpublished(conn) == []


def test_an_unpublished_row_needs_no_site_call(conn):
    signal = stored_in(conn, country="US", published=False)
    row = live(conn, signal.signal_id)
    site = Recorder()

    correct.reissue(conn, row, correct.place_correction(row),
                    push=lambda r, f: correct.push_place(r, f, session=site))

    assert site.calls == []
    assert live(conn, signal.signal_id)["country"] == "CA"


def test_running_it_twice_changes_nothing_the_second_time(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    correct.reissue(conn, row, correct.place_correction(row), push=lambda r, f: None)

    assert correct.targets(correct.current_rows(conn)) == []
    assert conn.execute(
        "SELECT COUNT(*) FROM signals WHERE signal_id = ?",
        (signal.signal_id,)).fetchone()[0] == 2


# --- the site ----------------------------------------------------------------

def test_the_site_is_corrected_in_place_not_withdrawn(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    site = Recorder()

    correct.push_place(row, correct.place_correction(row), session=site)

    assert len(site.calls) == 1
    call = site.calls[0]
    assert call["url"].endswith("/wp-json/talent/v1/correct")
    sent = call["json"]["rows"][0]
    assert sent["content_hash"] == row["content_hash"]
    assert sent["country"] == "CA"
    assert sent["city"] == "Toronto"
    # The employer's HQ is looked up rather than sourced, so it does not travel
    # this route: it is already enrichable and enrich.yml carries it.
    assert "hq_country" not in sent


def test_a_plugin_that_drops_the_fields_stops_everything(conn):
    """tit_correctable_columns() allows two columns today. A corrected database
    behind an uncorrected page is a divergence nobody would notice."""
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    site = Recorder({"corrected": 0, "unchanged_or_missing": 0,
                     "skipped_no_fields": 1, "errors": []})

    with pytest.raises(correct.PluginTooOld) as exc:
        correct.push_place(row, correct.place_correction(row), session=site)
    assert "includes/api.php" in str(exc.value)


def test_nothing_is_written_locally_when_the_site_refuses(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    site = Recorder({"corrected": 0, "skipped_no_fields": 1, "errors": []})

    with pytest.raises(correct.PluginTooOld):
        correct.reissue(conn, row, correct.place_correction(row),
                        push=lambda r, f: correct.push_place(r, f, session=site))

    assert live(conn, signal.signal_id)["country"] == "US"
    assert conn.execute(
        "SELECT COUNT(*) FROM signals WHERE signal_id = ?",
        (signal.signal_id,)).fetchone()[0] == 1


def test_the_site_is_corrected_before_the_database(conn):
    """The local revision is the only record that the site was corrected, so a
    run killed between the two steps must retry both."""
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)

    def die(_row, _fixed):
        raise publish.PublishError("host was busy")

    with pytest.raises(publish.PublishError):
        correct.reissue(conn, row, correct.place_correction(row), push=die)
    assert live(conn, signal.signal_id)["country"] == "US"
    assert correct.targets(correct.current_rows(conn))


def test_a_server_error_is_not_a_correction(conn):
    signal = stored_in(conn, country="US")
    row = live(conn, signal.signal_id)
    site = Recorder({"corrected": 0, "errors": [{"index": 0, "error": "no"}]})
    with pytest.raises(publish.PublishError):
        correct.push_place(row, correct.place_correction(row), session=site)


# --- the command line --------------------------------------------------------

def test_the_default_is_a_dry_run(conn, capsys, monkeypatch):
    """This one is dry by default, unlike the older correction scripts: it edits
    the geography of rows that are on the live site."""
    signal = stored_in(conn, country="US")
    monkeypatch.setattr(correct.schema, "connect", lambda *a, **k: conn)

    assert correct.main([]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out
    assert "'US' -> 'CA'" in out
    assert live(conn, signal.signal_id)["country"] == "US"


def test_the_dry_run_names_the_rows_and_the_site_constraint(conn, capsys, monkeypatch):
    stored_in(conn, country="US")
    monkeypatch.setattr(correct.schema, "connect", lambda *a, **k: conn)
    correct.main([])
    out = capsys.readouterr().out
    assert "Celestica Inc." in out
    assert "on the live site" in out
    assert correct.SITE_ALLOWLIST in out


def test_a_refused_shape_exits_two(conn, capsys, monkeypatch):
    stored_in(conn, country="US", city="Dublin", headquarters_city="Dublin")
    monkeypatch.setattr(correct.schema, "connect", lambda *a, **k: conn)
    assert correct.main([]) == 2
    assert "REFUSING" in capsys.readouterr().err


def test_nothing_to_do_is_a_clean_exit(conn, capsys, monkeypatch):
    stored_in(conn, country="CA", hq_country="CA")
    monkeypatch.setattr(correct.schema, "connect", lambda *a, **k: conn)
    assert correct.main([]) == 0
    assert "Nothing to correct" in capsys.readouterr().out


def test_dry_run_and_apply_cannot_both_be_asked_for(conn, monkeypatch):
    monkeypatch.setattr(correct.schema, "connect", lambda *a, **k: conn)
    with pytest.raises(SystemExit):
        correct.main(["--dry-run", "--apply"])


# --- the workflow ------------------------------------------------------------

def test_the_workflow_defaults_to_a_dry_run():
    """A writer whose default writes is one mis-click from a live edit."""
    yaml = pytest.importorskip("yaml")
    from pathlib import Path

    wf = yaml.safe_load((Path(__file__).parent.parent
                         / ".github/workflows/correct-city-country.yml").read_text())
    triggers = wf.get("on") or wf.get(True)
    assert triggers["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    # The script writes only with --apply, and the workflow may only pass it when
    # the input was explicitly turned off.
    step = next(s for job in wf["jobs"].values() for s in job.get("steps", [])
                if "correct_city_country.py" in (s.get("run") or ""))
    assert "--apply" in step["run"]
    assert 'inputs.dry_run }}" = "false"' in step["run"]

"""Re-deriving funding_amount_usd from the string the publisher wrote.

The column is the output of a pure function over a string the row already
carries, and that function keeps improving. Every improvement leaves the rows
written before it holding a figure the parser would no longer produce — twelve
of them on 2026-07-30, each a two- or three-digit dollar amount standing for a
round of millions, every one of them summed into the headline money total.

These pin the BEHAVIOUR, not those twelve rows, because a test naming the twelve
would go green the day they were corrected and say nothing the next time the
vocabulary widens:

  * a stored value the parser disagrees with is corrected;
  * a row whose amount now REFUSES ends with NULL, not with its old number,
    because the page promises an unreadable amount is left out rather than
    guessed at;
  * a row the parser agrees with is not touched at all — no revision, no
    request, nothing;
  * the original survives at is_current = 0, so what the money chart said
    yesterday stays answerable;
  * and running it twice changes nothing the second time.

The last one is what makes it safe to queue after every vocabulary fix.
"""

from __future__ import annotations

import sqlite3

import pytest

import correct_funding_amount as correct
from pipeline import publish, schema, store, validate, vocab

ARTICLE = (
    "Ábaco Technologies anunció una ronda de financiación de USD 53 millones "
    "liderada por inversores internacionales, y planea contratar ingenieros en "
    "Madrid durante el próximo año."
)


def read(**over):
    base = {
        "company": "Abaco Technologies",
        "pillar": "company_development",
        "signal_direction": "hiring",
        "confidence": "reported",
        "city": "Madrid",
        "headline": "Abaco Technologies raises USD 53 millones",
        "summary": "The company raised USD 53 millones and plans to hire.",
        "talent_readthrough": "Engineering hiring in Madrid.",
        "funding_amount": "USD 53 millones",
    }
    base.update(over)
    return base


def raw(**over):
    base = {
        "raw_text": ARTICLE,
        "headline": "Abaco Technologies raises USD 53 millones",
        "source_url": "https://www.infobae.com/example/abaco-ronda/",
        "source_name": "Infobae",
        "published_date": "2026-05-11",
    }
    base.update(over)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def stored_as(conn, *, usd, amount="USD 53 millones", published=True, **over):
    """A row as history left it: the string is what the publisher wrote, and the
    dollar figure is what the parser of the day made of it.

    Built through build_signal so it is a real row, then the OLD parser's answer
    is written back over the current one. That is exactly what these rows are —
    the function changed underneath them, not the source.
    """
    signal = validate.build_signal(
        read(funding_amount=amount, **over), raw(**over), "google_news")
    assert store.store(conn, signal) == "stored"
    conn.execute(
        "UPDATE signals SET funding_amount = ?, funding_amount_usd = ?, "
        "       published_at = ? WHERE signal_id = ?",
        (amount, usd, "2026-07-28 15:00:51" if published else None,
         signal.signal_id))
    conn.commit()
    return signal


def live(conn, signal_id) -> dict:
    conn.row_factory = sqlite3.Row
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


def history(conn, signal_id) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? ORDER BY revision",
        (signal_id,))]


class Recorder:
    """Stands in for the site. Never a stubbed module — see CLAUDE.md."""

    def __init__(self, response=None, status=200):
        self.calls: list[dict] = []
        self._response = response if response is not None else {
            "updated": 1, "missing": 0, "skipped": 0, "errors": []}
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


def apply(conn, found, recorder):
    for row, change in found:
        correct.reissue(conn, row, change,
                        push=lambda r, p: correct.push_amount(r, p, session=recorder))


# --- the worklist -----------------------------------------------------------

def test_a_stale_figure_is_found_without_anybody_listing_it(conn):
    """The whole column is re-derived. A hand-typed list of row ids would be
    stale the next time the multiplier vocabulary widens, which is the exact
    event this pass exists to follow."""
    stored_as(conn, usd=53)
    found = correct.targets(correct.current_rows(conn))
    assert len(found) == 1
    _row, (stale, fresh) = found[0]
    assert (stale, fresh) == (53, 53_000_000)


def test_a_row_the_parser_agrees_with_is_not_a_target(conn):
    """Which is what makes a second run a no-op, and what stops this pass
    writing a revision that says nothing."""
    stored_as(conn, usd=53_000_000)
    assert correct.targets(correct.current_rows(conn)) == []


def test_a_row_whose_amount_now_refuses_is_a_target(conn):
    """25 millioner kroner is not 25 dollars, and it is not 25 million dollars
    either. The only true answer is no answer."""
    stored_as(conn, usd=25, amount="25 millioner kroner")
    _row, (stale, fresh) = correct.targets(correct.current_rows(conn))[0]
    assert stale == 25 and fresh is None


def test_a_row_with_no_string_at_all_is_outside_the_population(conn):
    """There is nothing to re-derive FROM, so inventing an answer for it is the
    opposite of what this pass does."""
    signal = stored_as(conn, usd=53)
    conn.execute("UPDATE signals SET funding_amount = '' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    assert correct.current_rows(conn) == []


def test_an_empty_string_figure_is_read_as_no_figure(conn):
    """SQLite is happy to hand back '' where an int was expected, and '' is not
    a number. Comparing it to None naively would make every such row a target
    forever."""
    signal = stored_as(conn, usd=53)
    conn.execute("UPDATE signals SET funding_amount_usd = '', "
                 "funding_amount = '25 millioner kroner' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    assert correct.targets(correct.current_rows(conn)) == []


# --- the ceilings -----------------------------------------------------------

def test_a_parser_that_moved_most_of_the_column_stops_the_run(conn, monkeypatch):
    """Re-deriving a column from a function means a bug in the function is a bug
    in every row. 0.37% was measured; a run that finds far more than that has
    found a change nobody described."""
    monkeypatch.setattr(correct, "MIN_ROWS", 1)
    monkeypatch.setattr(correct, "MAX_SHARE", 0.0)
    stored_as(conn, usd=53)
    with pytest.raises(correct.Unsafe) as exc:
        correct.targets(correct.current_rows(conn))
    assert "ceiling" in str(exc.value)
    # And a person who has read the printed table may proceed.
    assert len(correct.targets(correct.current_rows(conn), force=True)) == 1


def test_the_clearing_ceiling_is_tighter_than_the_moving_one():
    """A wrong figure is corrected by the next run. A cleared one is not:
    /enrich ignores absent values on purpose, so nothing puts it back."""
    assert correct.MAX_CLEAR_SHARE < correct.MAX_SHARE


def test_a_parser_that_started_refusing_everything_stops_the_run(conn, monkeypatch):
    monkeypatch.setattr(correct, "MIN_ROWS", 1)
    # The moving ceiling is lifted out of the way on purpose, so what stops the
    # run is the CLEARING one and not the one it shares a message with.
    monkeypatch.setattr(correct, "MAX_SHARE", 1.0)
    monkeypatch.setattr(correct, "MAX_CLEAR_SHARE", 0.0)
    stored_as(conn, usd=25, amount="25 millioner kroner")
    with pytest.raises(correct.Unsafe) as exc:
        correct.targets(correct.current_rows(conn))
    assert "CLEARED" in str(exc.value)


def test_a_handful_of_rows_is_never_refused_for_a_percentage(conn):
    """A share of three rows is noise. MIN_ROWS is what stops a small database
    refusing itself."""
    stored_as(conn, usd=53)
    assert len(correct.targets(correct.current_rows(conn))) == 1


# --- the corrected row ------------------------------------------------------

def test_only_the_dollar_figure_moves(conn):
    signal = stored_as(conn, usd=53)
    row = live(conn, signal.signal_id)
    corrected = correct.corrected_signal(row, 53_000_000)

    assert corrected.funding_amount_usd == 53_000_000
    # The publisher's own wording is untouched. It is the evidence.
    assert corrected.funding_amount == "USD 53 millones"
    assert corrected.headline == row["headline"]
    assert corrected.source_url == row["source_url"]
    assert corrected.company_key == row["company_key"]


def test_the_content_hash_must_not_move(conn):
    """It is the reason the site update can be in place. includes/db.php refuses
    a hash it has already seen at ANY revision, so a pass that moved the hash
    would delete the live records and call it a duplicate."""
    signal = stored_as(conn, usd=53)
    row = live(conn, signal.signal_id)
    assert correct.corrected_signal(row, 53_000_000).content_hash == row["content_hash"]


def test_a_hash_that_did_move_stops_the_row(conn, monkeypatch):
    """If funding_amount_usd ever becomes an input to content_hash, this whole
    pass is the wrong shape and must not quietly keep running."""
    signal = stored_as(conn, usd=53)
    row = live(conn, signal.signal_id)
    monkeypatch.setattr(validate, "content_hash", lambda *a, **k: "moved")
    with pytest.raises(correct.Unsafe) as exc:
        correct.corrected_signal(row, 53_000_000)
    assert "withdraw-and-republish" in str(exc.value)


# --- the revision -----------------------------------------------------------

def test_the_correction_is_appended_and_the_original_survives(conn):
    """Never an overwrite. "What did the money chart say on 2026-07-29" has to
    stay answerable, and that cannot be retrofitted."""
    signal = stored_as(conn, usd=53)
    apply(conn, correct.targets(correct.current_rows(conn)), Recorder())

    revisions = history(conn, signal.signal_id)
    assert len(revisions) == 2
    old, new = revisions
    assert old["is_current"] == 0 and old["funding_amount_usd"] == 53
    assert new["is_current"] == 1 and new["funding_amount_usd"] == 53_000_000
    assert new["revision"] == old["revision"] + 1
    assert new["supersedes_row_id"] == old["row_id"]
    assert "re-derived" in new["notes"]


def test_a_refused_amount_ends_as_null_and_not_as_its_old_number(conn):
    """The promise the page makes. A figure we cannot read is left out rather
    than converted at a rate nobody published, and a stale wrong number sitting
    where the parser now says "I will not guess" is exactly the falsehood that
    promise exists to prevent."""
    signal = stored_as(conn, usd=105, amount="10,5 mio. kr.")
    apply(conn, correct.targets(correct.current_rows(conn)), Recorder())

    row = live(conn, signal.signal_id)
    assert row["funding_amount_usd"] is None
    # And the publisher's own words are still on the row for a reader.
    assert row["funding_amount"] == "10,5 mio. kr."
    assert "left out rather than guessed" in row["notes"]


def test_a_row_the_parser_agrees_with_gets_no_revision_at_all(conn):
    """Not a revision that changes nothing — no revision. A history full of
    no-op revisions is a history nobody can read."""
    signal = stored_as(conn, usd=53_000_000)
    found = correct.targets(correct.current_rows(conn))
    recorder = Recorder()
    apply(conn, found, recorder)

    assert found == []
    assert len(history(conn, signal.signal_id)) == 1
    assert recorder.calls == []


def test_the_published_marker_travels_to_the_new_revision(conn):
    """Left NULL, the revision would be offered to publish() every run, come
    back 'duplicate' on a hash the site has already seen, and be marked
    published anyway — a pointless round trip that reads like a lost row."""
    signal = stored_as(conn, usd=53)
    apply(conn, correct.targets(correct.current_rows(conn)), Recorder())
    assert live(conn, signal.signal_id)["published_at"] == "2026-07-28 15:00:51"


def test_an_unpublished_row_is_revised_without_touching_the_site(conn):
    signal = stored_as(conn, usd=53, published=False)
    recorder = Recorder()
    apply(conn, correct.targets(correct.current_rows(conn)), recorder)

    assert live(conn, signal.signal_id)["funding_amount_usd"] == 53_000_000
    assert recorder.calls == []


# --- idempotence ------------------------------------------------------------

def test_running_it_twice_changes_nothing_the_second_time(conn):
    """The property that makes it safe to queue after every vocabulary fix.

    Both shapes are covered: the row that gains a figure and the row that loses
    one. The second is the one that could loop forever — a clear that did not
    stick would be re-derived, re-cleared and re-revised on every run.
    """
    gained = stored_as(conn, usd=53)
    lost = stored_as(conn, usd=25, amount="25 millioner kroner",
                     company="Visibuilt", headline="Visibuilt raises 25 millioner kroner",
                     source_url="https://www.techsavvy.media/example/visibuilt/")

    apply(conn, correct.targets(correct.current_rows(conn)), Recorder())
    after_first = {s: history(conn, s) for s in (gained.signal_id, lost.signal_id)}
    assert [len(v) for v in after_first.values()] == [2, 2]

    recorder = Recorder()
    second = correct.targets(correct.current_rows(conn))
    assert second == [], "the second run found work to do, so it is not idempotent"
    apply(conn, second, recorder)

    assert recorder.calls == []
    assert {s: history(conn, s) for s in after_first} == after_first


def test_a_cleared_row_is_not_refilled_by_the_connect_time_backfill(conn, tmp_path):
    """The one thing that could quietly undo a clear.

    schema.connect() runs backfill_funding_usd() on every open, which fills the
    column WHERE funding_amount_usd IS NULL. A cleared row is exactly that shape.
    It stays cleared because the backfill asks the SAME function that refused the
    string in the first place — which is a property of the two agreeing, not an
    accident, so it is asserted rather than assumed.
    """
    signal = stored_as(conn, usd=25, amount="25 millioner kroner")
    apply(conn, correct.targets(correct.current_rows(conn)), Recorder())
    assert live(conn, signal.signal_id)["funding_amount_usd"] is None

    conn.commit()
    filled = schema.backfill_funding_usd(conn)
    conn.commit()
    assert filled == 0
    assert live(conn, signal.signal_id)["funding_amount_usd"] is None


def test_the_backfill_cannot_do_this_job_which_is_why_this_exists(conn):
    """It touches only rows where the figure is MISSING, so every one of the
    twelve — each holding a wrong figure rather than none — is invisible to it.
    A change that widened it to overwrite stored values would be an in-place
    edit of a published record with no revision behind it."""
    signal = stored_as(conn, usd=53)
    assert schema.backfill_funding_usd(conn) == 0
    assert live(conn, signal.signal_id)["funding_amount_usd"] == 53


# --- what is sent to the site -----------------------------------------------

def test_a_new_figure_is_sent_as_a_value(conn):
    signal = stored_as(conn, usd=53)
    row = live(conn, signal.signal_id)
    recorder = Recorder()
    correct.push_amount(row, 53_000_000, session=recorder)

    (call,) = recorder.calls
    assert call["url"].endswith("/wp-json/talent/v1/enrich")
    (sent,) = call["json"]["rows"]
    assert sent["funding_amount_usd"] == 53_000_000
    assert sent["content_hash"] == row["content_hash"]
    assert "clear" not in sent


def test_a_removal_has_to_be_asked_for_by_name(conn):
    """/enrich ignores an absent or empty field on purpose, so sending nothing
    would leave the wrong figure on the live page while the local database said
    it had been corrected. `clear` is the only door, and it exists for exactly
    these rows."""
    signal = stored_as(conn, usd=25, amount="25 millioner kroner")
    row = live(conn, signal.signal_id)
    recorder = Recorder()
    correct.push_amount(row, None, session=recorder)

    (sent,) = recorder.calls[0]["json"]["rows"]
    assert sent["clear"] == ["funding_amount_usd"]
    assert "funding_amount_usd" not in sent


def test_a_plugin_that_cannot_clear_the_column_fails_the_run(conn):
    """Not one row skipped — every row that must lose its figure would fail the
    same way, and a counted-and-continued failure leaves wrong money on the page
    while the run goes green."""
    signal = stored_as(conn, usd=25, amount="25 millioner kroner")
    row = live(conn, signal.signal_id)
    recorder = Recorder(response={
        "updated": 0, "errors": [{"index": 0,
                                  "error": "not clearable: funding_amount_usd"}]})
    with pytest.raises(publish.PublishError) as exc:
        correct.push_amount(row, None, session=recorder)
    assert "not clearable" in str(exc.value)


def test_the_site_is_corrected_before_the_database(conn, monkeypatch):
    """A row is a target while its LIVE revision holds the stale figure, so the
    local revision is the only record that the site was corrected. Written
    first, a run killed between the two steps leaves the page wrong with nothing
    left to find it. Written second, the worst a kill costs is one repeated
    UPDATE of a value the site already holds.

    Wrapping the function on the module object, never stubbing the module into
    sys.modules (CLAUDE.md, "Test gotcha").
    """
    signal = stored_as(conn, usd=53)
    order: list[str] = []
    real_revise = store.revise

    def revise(*args, **kwargs):
        order.append("database")
        return real_revise(*args, **kwargs)

    monkeypatch.setattr(store, "revise", revise)

    def push(row, parsed):
        order.append("site")
        return {"updated": 1}

    correct.reissue(conn, live(conn, signal.signal_id), (53, 53_000_000), push=push)
    assert order == ["site", "database"]


def test_a_failed_request_leaves_the_row_a_target(conn):
    """So the next run finds it again, rather than a revision claiming a
    correction the site never received."""
    signal = stored_as(conn, usd=53)
    row = live(conn, signal.signal_id)
    recorder = Recorder(status=500)
    with pytest.raises(publish.PublishError):
        correct.reissue(conn, row, (53, 53_000_000),
                        push=lambda r, p: correct.push_amount(r, p, session=recorder))

    assert len(history(conn, signal.signal_id)) == 1
    assert live(conn, signal.signal_id)["funding_amount_usd"] == 53
    assert correct.targets(correct.current_rows(conn))


# --- the column this pass is about ------------------------------------------

def test_the_column_is_one_the_site_can_both_set_and_clear():
    """The two allowlists in includes/api.php. Read as text rather than trusted:
    a column removed from either one turns this pass into a run that corrects
    the database and leaves the page wrong."""
    from pathlib import Path

    api = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker/includes/api.php").read_text()
    enrichable = api.split("function tit_enrichable_columns()")[1].split("}")[0]
    clearable = api.split("function tit_clearable_columns()")[1].split("}")[0]
    assert correct.COLUMN in enrichable
    assert correct.COLUMN in clearable


def test_the_pass_and_the_parser_cannot_disagree_about_a_string(conn):
    """rederivation() is the single place the comparison is made, so the
    printed table, the payload and the revision cannot hold three opinions."""
    for amount, stale in (("USD 53 millones", 53), ("$20-million USD", 20),
                          ("25 millioner kroner", 25), ("$150.000", 150),
                          ("$12 milyon dolarlık", 12), ("US$ 544 mi", 544)):
        row = {"funding_amount": amount, correct.COLUMN: stale}
        assert correct.rederivation(row) == (stale, vocab.parse_funding_usd(amount))

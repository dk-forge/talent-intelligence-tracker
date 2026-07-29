"""Re-issuing the Item 5.02 filings that were published under another pillar.

Three properties matter here. A corrected row must say exactly what a freshly
collected one would say. It must move only the pillar and the two values
DERIVED from the pillar, because anything else would be a republication wearing
a correction's name. And every phase must be resumable, because this withdraws
573 published rows one request at a time and it will be interrupted.
"""

from __future__ import annotations

import pytest

import correct_sec_pillar as correct
from pipeline import publish, schema, store, validate

FILING_BODY = (
    "Item 5.02 Departure of Directors or Certain Officers; Election of Directors. "
    "On June 29, 2026 the Board appointed a new Chief Financial Officer, whose "
    "annual base salary will be 750000 dollars plus an equity award."
)
HEADLINE = "ACME CORP 8-K filing (Item 5.02): officer or director change"


def raw(**over):
    base = {
        "raw_text": FILING_BODY,
        "headline": HEADLINE,
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/2/d8k.htm",
        "source_name": "SEC EDGAR",
        "published_date": "2026-06-29",
        "country": "United States",
        "cik": "919012",
    }
    base.update(over)
    return base


def read_as(pillar, **over):
    base = {
        "company": "Acme Corp",
        "pillar": pillar,
        "signal_direction": "comp_shift",
        "confidence": "verified",
        "headline": HEADLINE,
        "summary": "Acme Corp appointed a new Chief Financial Officer.",
        "talent_readthrough": "A finance leadership seat has changed hands.",
    }
    base.update(over)
    return base


def as_stored(pillar="rewards_comp", **over):
    """A row exactly as it went onto the site BEFORE forced_pillar existed.

    Built through build_signal like everything else, but under a collector name
    that still lets the model choose the pillar, then labelled sec_edgar. That
    is what those rows are: the model's pillar, on a filing from this source.
    """
    signal = validate.build_signal(read_as(pillar, **over), raw(**over), "google_news")
    signal.collector = correct.COLLECTOR
    return signal


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def published(conn):
    """One mis-filed row, live on the site."""
    signal = as_stored()
    assert store.store(conn, signal) == "stored"
    conn.execute("UPDATE signals SET published_at = '2026-07-01' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    return signal


def row_for(conn, signal_id):
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


# --- what a corrected row says ---------------------------------------------

def test_a_corrected_row_says_what_a_fresh_one_would_say(conn, published):
    """The whole reason this recomputes through validate rather than writing
    the pillar and leaving the derived columns where they were."""
    fixed = correct.corrected_signal(row_for(conn, published.signal_id))
    fresh = validate.build_signal(read_as("rewards_comp"), raw(), correct.COLLECTOR)

    assert fixed.pillar == fresh.pillar == "leadership_change"
    assert fixed.content_hash == fresh.content_hash
    assert fixed.materiality == fresh.materiality


def test_only_the_pillar_and_what_the_pillar_derives_can_move(conn, published):
    """A correction that could rewrite a headline, a figure or a read-through
    would be a republication wearing a correction's name."""
    before = row_for(conn, published.signal_id)
    fixed = correct.corrected_signal(before)

    moved = {name for name in correct._FIELDS
             if getattr(fixed, name) != before[name]}
    assert moved <= {"pillar", "content_hash", "materiality"}
    assert "pillar" in moved


def test_the_hash_moves_which_is_why_this_is_a_revision(conn, published):
    """If pillar were not a hash input this would be a /correct in place. It
    is one, so the fingerprint changes and the row has to be re-issued: leaving
    the old hash on a row whose pillar changed means the next collection of the
    same filing matches nothing and publishes it twice."""
    fixed = correct.corrected_signal(row_for(conn, published.signal_id))
    assert fixed.content_hash != published.content_hash
    assert fixed.signal_id == published.signal_id


def test_a_bare_officer_change_lands_in_the_tier_its_peers_are_in(conn, published):
    """It was 'high' only because a mis-filed pillar exempted it from the bare
    officer change rule. Its 2,480 correctly filed peers are routine."""
    assert row_for(conn, published.signal_id)["materiality"] != "routine"
    assert correct.corrected_signal(row_for(conn, published.signal_id)).materiality \
        == "routine"


# --- which rows are picked up ----------------------------------------------

def test_a_genuine_comp_filing_from_the_same_source_is_left_alone(conn):
    """The 19 that are really about pay: an incentive plan, a salary increase,
    an equity grant. The model wrote its own headline for those, which is
    exactly the reading forced_pillar defers to."""
    signal = as_stored(
        headline="Littelfuse Inc. Announces Equity Grants to Named Executive Officers",
        summary="Littelfuse granted equity awards to named executive officers.",
        raw_text="Littelfuse granted equity awards to named executive officers.")
    store.store(conn, signal)
    assert correct.targets(correct.current_rows(conn)) == []


def test_an_implausible_worklist_is_a_refusal_not_a_mass_reissue():
    """573 of 3,496 is 16%. A rule that suddenly matches everything is a broken
    rule, and it must not be able to withdraw the source from the site."""
    rows = [{"headline": HEADLINE, "pillar": "rewards_comp"} for _ in range(100)]
    with pytest.raises(correct.Unsafe):
        correct.targets(rows)
    assert len(correct.targets(rows, force=True)) == 100


def test_a_plausible_share_passes_the_guard():
    rows = [{"headline": HEADLINE, "pillar": "rewards_comp"} for _ in range(16)]
    rows += [{"headline": HEADLINE, "pillar": "leadership_change"} for _ in range(84)]
    assert len(correct.targets(rows)) == 16


def test_the_guard_does_not_fire_on_a_table_too_small_to_judge():
    """Three rows out of four is 75% and evidence of nothing."""
    rows = [{"headline": HEADLINE, "pillar": "rewards_comp"} for _ in range(3)]
    rows += [{"headline": HEADLINE, "pillar": "leadership_change"}]
    assert len(correct.targets(rows)) == 3


def test_a_row_the_correction_turns_into_a_duplicate_is_withdrawn(conn, published):
    """Two 8-K accessions filed the same day by one company both carry the
    collector's identical boilerplate headline, so under the corrected pillar
    they are one record. Publishing the second is not an option even in
    principle: the site's unique key is (content_hash, revision) and every
    insert it makes is revision 1, so it would not error, it would silently
    never land. Measured once in the live table (Cadence Design Systems)."""
    already = validate.build_signal(
        read_as("leadership_change"),
        raw(source_url="https://www.sec.gov/Archives/edgar/data/1/3/d8k.htm"),
        correct.COLLECTOR)
    assert store.store(conn, already) == "stored"

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [published.signal_id]


def test_a_row_the_site_would_refuse_as_near_duplicate_is_withdrawn(conn, published):
    """Different filing, different headline, different hash — and the site
    still refuses it, because tit_insert_signal() also matches on employer,
    pillar, direction and a 14-day window. publish() would count that as a
    duplicate without naming it and mark the row published, leaving the site
    showing neither revision. So the rule is mirrored and the row withdrawn."""
    other = validate.build_signal(
        read_as("leadership_change",
                headline="Acme Corp names Dale Reid to the board",
                summary="Acme Corp appointed Dale Reid as a director."),
        raw(headline="Acme Corp names Dale Reid to the board",
            published_date="2026-07-06",
            source_url="https://www.sec.gov/Archives/edgar/data/1/9/d8k.htm"),
        correct.COLLECTOR)
    assert store.store(conn, other) == "stored"

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [published.signal_id]


def test_the_same_employer_outside_the_window_is_not_a_duplicate(conn, published):
    """The window is 14 days. A second officer change three months later is a
    second officer change."""
    other = validate.build_signal(
        read_as("leadership_change",
                headline="Acme Corp names Dale Reid to the board",
                summary="Acme Corp appointed Dale Reid as a director."),
        raw(headline="Acme Corp names Dale Reid to the board",
            published_date="2026-10-06",
            source_url="https://www.sec.gov/Archives/edgar/data/1/9/d8k.htm"),
        correct.COLLECTOR)
    store.store(conn, other)

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert [r["signal_id"] for r in to_move] == [published.signal_id]
    assert dupes == []


def test_two_targets_that_correct_onto_one_hash_leave_one_survivor(conn):
    """The same collision between two rows this pass is moving, where neither
    fingerprint exists yet. Exactly one is re-issued and the rest withdrawn:
    dropping both would take the filing off the site entirely."""
    # Two live rows for one company and date exist only because the pillar
    # differed: dedupe.fuzzy_duplicate keys on it, which is how the pair got in.
    for n, pillar in ((2, "rewards_comp"), (3, "company_development")):
        assert store.store(conn, as_stored(
            pillar, source_url=f"https://www.sec.gov/Archives/edgar/data/1/{n}/d8k.htm",
        )) == "stored"
    conn.commit()

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert len(to_move) == 1 and len(dupes) == 1


# --- resumability ----------------------------------------------------------

@pytest.fixture
def withdrawn():
    """Records what the site was asked to retract, in place of the network."""
    calls = []
    return calls, lambda signal_id, reason: calls.append(signal_id)


def reissue_all(conn, withdraw):
    rows = correct.targets(correct.current_rows(conn))
    for row in rows:
        correct.reissue(conn, row, withdraw=withdraw)
    return rows


def test_the_original_survives_and_the_replacement_is_unpublished(conn, published,
                                                                  withdrawn):
    calls, withdraw = withdrawn
    reissue_all(conn, withdraw)

    rows = conn.execute(
        "SELECT revision, is_current, pillar, published_at FROM signals "
        "WHERE signal_id = ? ORDER BY revision", (published.signal_id,)).fetchall()
    assert [(r["revision"], r["is_current"]) for r in rows] == [(1, 0), (2, 1)]
    assert rows[0]["pillar"] == "rewards_comp" and rows[0]["published_at"]
    assert rows[1]["pillar"] == "leadership_change" and rows[1]["published_at"] is None
    assert calls == [published.signal_id]
    # publish() picks it up with no further arrangement: that is the whole path.
    assert [r["signal_id"] for r in publish.unpublished(conn)] == [published.signal_id]


def test_the_site_is_asked_to_withdraw_before_anything_is_re_issued(conn, published):
    """A replacement published while its predecessor is still live puts the
    same filing on the page twice, under two pillars."""
    order = []

    def withdraw(signal_id, reason):
        order.append(("withdraw", bool(conn.execute(
            "SELECT 1 FROM signals WHERE signal_id = ? AND revision = 2",
            (signal_id,)).fetchone())))

    reissue_all(conn, withdraw)
    assert order == [("withdraw", False)]


def test_running_it_twice_re_issues_nothing(conn, published, withdrawn):
    """It will be interrupted. A second pass must find its own work done, and
    must not withdraw the REPLACEMENT: /retract works on signal_id and both
    revisions share one, so a row offered twice would take itself off the
    site."""
    calls, withdraw = withdrawn
    reissue_all(conn, withdraw)
    assert reissue_all(conn, withdraw) == []
    assert calls == [published.signal_id]


def test_a_withdrawal_that_failed_leaves_the_row_exactly_as_it_was(conn, published):
    """One row's failure is one row. It keeps its pillar, stays live, stays a
    target, and above all does not get a revision published while the site is
    still showing the old one."""
    def withdraw(signal_id, reason):
        raise publish.PublishError("503")

    with pytest.raises(publish.PublishError):
        reissue_all(conn, withdraw)

    assert publish.unpublished(conn) == []
    assert row_for(conn, published.signal_id)["pillar"] == "rewards_comp"
    assert len(correct.targets(correct.current_rows(conn))) == 1


def test_a_row_that_was_never_published_is_not_withdrawn(conn, withdrawn):
    """Only what the site is actually showing is taken off it."""
    store.store(conn, as_stored())
    conn.commit()
    calls, withdraw = withdrawn
    reissue_all(conn, withdraw)
    assert calls == []
    assert len(publish.unpublished(conn)) == 1

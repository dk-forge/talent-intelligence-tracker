"""Re-filing the office openings that were published under another pillar.

Three properties matter here, and they are the SEC pass's three because it is
the same shape of correction. A corrected row must say exactly what a freshly
collected one would say. It must move only the pillar, the site event and the
two values DERIVED from the pillar, because anything else would be a
republication wearing a correction's name. And every phase must be resumable,
because it withdraws published rows one request at a time.

A fourth is specific to this pass: the rule that decides which rows move reads
a HEADLINE rather than a document type, so most of what follows is about what
it must refuse to decide.
"""

from __future__ import annotations

import pytest

import correct_site_pillar as correct
from pipeline import prefilter, publish, schema, store, validate

BODY = ("4Life has opened a new office in Mexico City, its third in the "
        "country. The company did not say how many people will work there.")
HEADLINE = "4Life Opens New Office in Mexico"


def raw(**over):
    base = {
        "raw_text": BODY,
        "headline": HEADLINE,
        "source_url": "https://www.directsellingnews.com/2026/07/27/4life-office/",
        "source_name": "Direct Selling News",
        "published_date": "2026-07-27",
        "country": "Mexico",
    }
    base.update(over)
    return base


def read_as(pillar, **over):
    base = {
        "company": "4Life",
        "pillar": pillar,
        "signal_direction": "neutral",
        "confidence": "reported",
        "headline": HEADLINE,
        "summary": "4Life opened a new office in Mexico City.",
        "talent_readthrough": "A new site is a place the employer will staff.",
    }
    base.update(over)
    return base


def as_stored(pillar="company_development", collector="google_news", **over):
    """A row exactly as it went onto the site BEFORE the pillar rule existed.

    Built through build_signal like everything else, but under a collector the
    headline rule does not govern, then labelled with the news collector. That
    is what those rows are: the model's unguided pillar, on a news story.
    """
    signal = validate.build_signal(read_as(pillar, **over), raw(**over), "sec_edgar")
    signal.collector = collector
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
    conn.execute("UPDATE signals SET published_at = '2026-07-28' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    return signal


def row_for(conn, signal_id):
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


# --- what the headline is allowed to decide --------------------------------
#
# The narrow half of prefilter's site vocabulary. Everything it refuses stays
# the model's to read, which is the status quo rather than a guess.

@pytest.mark.parametrize("headline", [
    "4Life Opens New Office in Mexico",
    "Bespoke Partners Opens New Office in Boston",
    "Elders Real Estate opens new office in Murray Bridge",
    "Growing with Purpose: JHA Companies opens new office at DuBois Airport",
    "Trustee and fiduciary services expand in GIFT City as Axis Trustee opens new office",
    "Infiligence Opens Richmond Engineering Hub to Bring Enterprise AI Into Production",
    "La app de stablecoins Tuyo abre oficina en Madrid e inicia un proceso de contratación",
    "Siemens opens electrification, automation factory in Egypt",
])
def test_a_headline_that_plainly_says_a_site_opened(headline):
    assert prefilter.site_opening_term(headline)
    assert validate.forced_pillar("google_news", headline) == "how_we_work"
    assert validate.forced_site_event("google_news", headline) == "opened"


@pytest.mark.parametrize("headline, why", [
    ("Acme to open a new office in Cork next year", "a decision, not an event"),
    ("Acme will open a new office in Cork", "a decision, not an event"),
    ("Acme plans to open a new office in Cork", "a decision, not an event"),
    ("Acme's new Cork office is under construction", "not open yet"),
    # The gerund is the ambiguous form: "the opening of" reads identically
    # before and after the ribbon is cut, so the announcing verb and the diary
    # date are what settle it. Each of these matched 'opened' until they did.
    ("Acme announces the opening of its new Cork office next year", "announced"),
    ("Acme is planning the opening of a new office in Cork", "still a plan"),
    ("Acme unveils plans for the opening of a Cork office", "still a plan"),
    ("Acme opens new Cork office in 2028", "a dated opening is a diary entry"),
    ("$7B firm expands Cary office space, plans to hire", "expanded, and office space"),
    ("OpenAI expands AI workforce in Dublin", "a workforce, not a site"),
    ("Acme invests €50m in its Cork plant", "invests decides nothing"),
    ("Acme closes its Cork plant", "a closure"),
    ("Acme relocates its Cork office to Dublin", "a move"),
    ("Acme closes Cork plant and opens Dublin office", "two events in one line"),
    ("Acme opens the door to a Cork investment", "opens the door"),
    ("Office space rents fall as Acme opens hybrid review", "a false friend"),
])
def test_a_headline_that_does_not_settle_it_is_left_to_the_model(headline, why):
    assert prefilter.site_opening_term(headline) is None, why
    assert validate.forced_pillar("google_news", headline) is None, why
    assert validate.forced_site_event("google_news", headline) is None, why


@pytest.mark.parametrize("headline", [
    # Every one of these is a live employer in the table. Under a bare "open"
    # verb the first would read as a site opening: it is a funding row.
    "Open Office Ltd raised $3M in a private placement",
    "OPEN DOORS PARTNERS, LLC raised $13.7M in a private placement",
    "Open Text Corp 8-K filing (Item 5.02): officer or director change",
    "The Open University: women's median hourly pay is 14.90% lower than men's",
    "Opendoor Technologies Inc.: $741,137,105 total compensation",
])
def test_an_employer_whose_name_contains_open_is_not_a_site_opening(headline):
    assert prefilter.site_opening_term(headline) is None


def test_the_rule_only_governs_collectors_whose_headline_is_a_sentence():
    """A templated headline's pillar is settled by its document, and a phrase
    match there could only be an accident of an employer's name."""
    assert validate.forced_site_event("sec_form_d_bulk", HEADLINE) is None
    assert validate.forced_pillar("sec_form_d_bulk", HEADLINE) is None
    assert validate.forced_site_event("google_news", HEADLINE) == "opened"


def test_the_officer_rule_still_wins_on_its_own_collector():
    """Extending forced_pillar must not disturb what it already decided."""
    officer = "ACME CORP 8-K filing (Item 5.02): officer or director change"
    assert validate.forced_pillar("sec_edgar", officer) == "leadership_change"


# --- what a corrected row says ---------------------------------------------

def test_a_corrected_row_says_what_a_fresh_one_would_say(conn, published):
    """The whole reason this recomputes through validate rather than writing
    the pillar and leaving the derived columns where they were."""
    fixed = correct.corrected_signal(row_for(conn, published.signal_id))
    fresh = validate.build_signal(read_as("company_development"), raw(), "google_news")

    assert fixed.pillar == fresh.pillar == "how_we_work"
    assert fixed.site_event == fresh.site_event == "opened"
    assert fixed.content_hash == fresh.content_hash
    assert fixed.materiality == fresh.materiality


def test_only_the_pillar_the_site_event_and_what_they_derive_can_move(conn, published):
    """A correction that could rewrite a headline, a figure or a read-through
    would be a republication wearing a correction's name."""
    before = row_for(conn, published.signal_id)
    fixed = correct.corrected_signal(before)

    moved = {name for name in correct._FIELDS
             if getattr(fixed, name) != before[name]}
    assert moved <= {"pillar", "site_event", "content_hash", "materiality"}
    assert "pillar" in moved and "site_event" in moved


def test_the_hash_moves_which_is_why_this_is_a_revision(conn, published):
    """If pillar were not a hash input this would be a /correct in place. It is
    one, so the fingerprint changes and the row has to be re-issued."""
    fixed = correct.corrected_signal(row_for(conn, published.signal_id))
    assert fixed.content_hash != published.content_hash
    assert fixed.signal_id == published.signal_id


def test_a_row_already_in_the_right_pillar_still_gets_its_site_event(conn):
    """Five of the nine. They were filed correctly and collected before the
    column existed, so the pillar does not move and the hash does not either."""
    signal = as_stored("how_we_work")
    store.store(conn, signal)
    conn.commit()

    [row] = correct.targets(correct.current_rows(conn))
    fixed = correct.corrected_signal(row)
    assert fixed.pillar == "how_we_work"
    assert fixed.site_event == "opened"
    assert fixed.content_hash == signal.content_hash


def test_a_site_event_the_model_already_set_is_never_overwritten(conn):
    """The model read the body; this reads one sentence. On "expanded or
    opened" the body wins, so the forced value only ever fills a blank."""
    signal = as_stored("how_we_work")
    signal.site_event = "expanded"
    store.store(conn, signal)
    conn.commit()

    assert correct.targets(correct.current_rows(conn)) == []


def test_a_row_that_is_already_right_is_not_a_target(conn):
    signal = as_stored("how_we_work")
    signal.site_event = "opened"
    store.store(conn, signal)
    conn.commit()
    assert correct.targets(correct.current_rows(conn)) == []


# --- which rows are picked up ----------------------------------------------

def test_a_genuine_funding_story_from_the_same_collector_is_left_alone(conn):
    """The pillar rule reads the headline, so a story whose headline says
    nothing about a place of work keeps the model's reading."""
    signal = as_stored(
        "company_development",
        headline="Cashea raises $100M Series B",
        summary="Cashea raised $100M in a Series B round.",
        raw_text="Cashea raised $100M in a Series B round led by an investor.")
    store.store(conn, signal)
    assert correct.targets(correct.current_rows(conn)) == []


def test_an_implausible_worklist_is_a_refusal_not_a_mass_reissue():
    """9 of 132 is 7%. A rule that suddenly matches everything is a broken
    rule, and it must not be able to withdraw the news collectors from the
    site."""
    rows = [{"collector": "google_news", "headline": HEADLINE,
             "pillar": "company_development", "site_event": None} for _ in range(100)]
    with pytest.raises(correct.Unsafe):
        correct.targets(rows)
    assert len(correct.targets(rows, force=True)) == 100


def test_a_plausible_share_passes_the_guard():
    rows = [{"collector": "google_news", "headline": HEADLINE,
             "pillar": "company_development", "site_event": None} for _ in range(9)]
    rows += [{"collector": "google_news", "headline": "Cashea raises $100M Series B",
              "pillar": "company_development", "site_event": None} for _ in range(123)]
    assert len(correct.targets(rows)) == 9


def test_the_guard_does_not_fire_on_a_table_too_small_to_judge():
    """Three rows out of four is 75% and evidence of nothing."""
    rows = [{"collector": "google_news", "headline": HEADLINE,
             "pillar": "company_development", "site_event": None} for _ in range(3)]
    rows += [{"collector": "google_news", "headline": "Cashea raises $100M Series B",
              "pillar": "company_development", "site_event": None}]
    assert len(correct.targets(rows)) == 3


# --- the duplicate ---------------------------------------------------------

def test_the_two_copies_of_one_story_collapse_onto_one_record(conn, published):
    """The 4Life pair, which is why this pass resolves a duplicate at all.

    One article, one source_url, one seen_urls entry — and two rows, because
    pillar is an input to content_hash AND the key fuzzy_duplicate groups on,
    so one story read under two pillars walks through both dedup layers.
    Correcting the pillar is what makes them collide, and the second is
    withdrawn rather than published: the site's unique key is (content_hash,
    revision) and every insert it makes is revision 1, so it would not error,
    it would silently never land.
    """
    other = as_stored("how_we_work")
    other.site_event = "opened"
    assert store.store(conn, other) == "stored"
    conn.commit()

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [published.signal_id]


def test_two_targets_that_correct_onto_one_hash_leave_one_survivor(conn):
    """Both copies still needing correction, which is the state the live table
    is actually in. Exactly one is re-issued and the rest withdrawn: dropping
    both would take the story off the site entirely."""
    for pillar in ("how_we_work", "company_development"):
        assert store.store(conn, as_stored(pillar)) == "stored"
    conn.commit()

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert len(to_move) == 1 and len(dupes) == 1
    # The copy that has been live longest is the one kept.
    assert to_move[0]["row_id"] < dupes[0]["row_id"]


def test_a_row_the_site_would_refuse_as_near_duplicate_is_withdrawn(conn, published):
    """Different story, different headline, different hash — and the site still
    refuses it, because tit_insert_signal() also matches on employer, pillar,
    direction and a 14-day window."""
    other = validate.build_signal(
        read_as("how_we_work",
                headline="4Life opens a second office in Guadalajara",
                summary="4Life opened a second office in Guadalajara."),
        raw(headline="4Life opens a second office in Guadalajara",
            published_date="2026-07-30",
            raw_text="4Life opened a second office in Guadalajara.",
            source_url="https://www.directsellingnews.com/2026/07/30/4life-two/"),
        "google_news")
    assert store.store(conn, other) == "stored"

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [published.signal_id]


def test_the_same_employer_outside_the_window_is_not_a_duplicate(conn, published):
    """The window is 14 days. An office opened three months later is a second
    office."""
    other = validate.build_signal(
        read_as("how_we_work",
                headline="4Life opens a second office in Guadalajara",
                summary="4Life opened a second office in Guadalajara."),
        raw(headline="4Life opens a second office in Guadalajara",
            published_date="2026-10-30",
            raw_text="4Life opened a second office in Guadalajara.",
            source_url="https://www.directsellingnews.com/2026/10/30/4life-two/"),
        "google_news")
    store.store(conn, other)

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert [r["signal_id"] for r in to_move] == [published.signal_id]
    assert dupes == []


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
        "SELECT revision, is_current, pillar, site_event, published_at FROM signals "
        "WHERE signal_id = ? ORDER BY revision", (published.signal_id,)).fetchall()
    assert [(r["revision"], r["is_current"]) for r in rows] == [(1, 0), (2, 1)]
    assert rows[0]["pillar"] == "company_development" and rows[0]["published_at"]
    assert rows[1]["pillar"] == "how_we_work" and rows[1]["published_at"] is None
    assert rows[0]["site_event"] is None and rows[1]["site_event"] == "opened"
    assert calls == [published.signal_id]
    # publish() picks it up with no further arrangement: that is the whole path.
    assert [r["signal_id"] for r in publish.unpublished(conn)] == [published.signal_id]


def test_the_site_is_asked_to_withdraw_before_anything_is_re_issued(conn, published):
    """A replacement published while its predecessor is still live puts the
    same story on the page twice under two pillars — which is the exact state
    the 4Life pair is already in."""
    order = []

    def withdraw(signal_id, reason):
        order.append(("withdraw", bool(conn.execute(
            "SELECT 1 FROM signals WHERE signal_id = ? AND revision = 2",
            (signal_id,)).fetchone())))

    reissue_all(conn, withdraw)
    assert order == [("withdraw", False)]


def test_running_it_twice_re_issues_nothing(conn, published, withdrawn):
    """It can be interrupted. A second pass must find its own work done, and
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
    assert row_for(conn, published.signal_id)["pillar"] == "company_development"
    assert len(correct.targets(correct.current_rows(conn))) == 1


def test_a_row_that_was_never_published_is_not_withdrawn(conn, withdrawn):
    """Only what the site is actually showing is taken off it."""
    store.store(conn, as_stored())
    conn.commit()
    calls, withdraw = withdrawn
    reissue_all(conn, withdraw)
    assert calls == []
    assert len(publish.unpublished(conn)) == 1

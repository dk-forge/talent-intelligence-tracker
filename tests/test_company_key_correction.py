"""Re-issuing the rows whose stored company_key is not the key we compute today.

Four properties matter. The worklist must be DERIVED from vocab.company_key
rather than typed, because the list somebody had was missing two of the eleven
employers. A corrected row must move the key and the hash and nothing else. The
merge of two spellings must actually collapse to one employer. And the whole
pass must be resumable, because it withdraws published rows one request at a
time against a host that 504s under load.
"""

from __future__ import annotations

import pytest

import correct_company_key as correct
from pipeline import publish, schema, store, validate, vocab

ARTICLE = (
    "CO-OPERATIVE GROUP LIMITED published its gender pay gap report for the "
    "reporting year, covering 2000 employees across its retail estate."
)


def raw(**over):
    base = {
        "raw_text": ARTICLE,
        "headline": "CO-OPERATIVE GROUP LIMITED gender pay gap report",
        "source_url": "https://gender-pay-gap.service.gov.uk/employers/1/2024",
        "source_name": "GOV.UK gender pay gap service",
        "published_date": "2024-04-05",
        "country": "United Kingdom",
    }
    base.update(over)
    return base


def read(**over):
    base = {
        "company": "CO-OPERATIVE GROUP LIMITED",
        "pillar": "rewards_comp",
        "signal_direction": "comp_shift",
        "confidence": "verified",
        "headline": "CO-OPERATIVE GROUP LIMITED gender pay gap report",
        "summary": "The employer published its gender pay gap figures.",
        "talent_readthrough": "Pay transparency for a large retail workforce.",
    }
    base.update(over)
    return base


def stored_under(conn, key, **over):
    """A row as it went onto the site under the OLD key.

    Built through build_signal like every other row, then the key it was stored
    with is written back — which is exactly what history looks like: the row was
    correct when a different vocab.company_key produced it.
    """
    signal = validate.build_signal(read(**over), raw(**over), "uk_paygap")
    fresh_id = signal.signal_id
    assert store.store(conn, signal) == "stored"

    # signal_id IS the content_hash of the first revision, so a row stored under
    # the old key carries the old key's hash in both columns. Rewriting only one
    # of them would make a test row that could never exist.
    signal.company_key = key
    signal.content_hash = validate.content_hash(
        key, signal.pillar, signal.published_date, signal.headline, signal.source_name)
    signal.signal_id = signal.content_hash
    conn.execute(
        "UPDATE signals SET company_key = ?, content_hash = ?, signal_id = ?, "
        "       published_at = '2026-07-01' WHERE signal_id = ?",
        (key, signal.content_hash, signal.signal_id, fresh_id))
    conn.commit()
    return signal


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def mangled(conn):
    """One row live on the site under the key the `\\b` bug produced."""
    return stored_under(conn, "-operative group")


def row_for(conn, signal_id):
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


# --- which rows are picked up ----------------------------------------------

def test_the_worklist_is_derived_from_vocab_and_not_from_a_list(conn, mangled):
    """The list of employers somebody had named six. Deriving it found eleven:
    the three EMPLOYER_KEY_ALIASES merges, and two rows still carrying an `lp`
    and a `pbc` from before those joined the suffix vocabulary. A script that
    hard-coded the six would have left five behind and reported success."""
    stale = stored_under(conn, "crossamerica partners lp",
                         company="CrossAmerica Partners LP",
                         headline="CrossAmerica Partners LP reports",
                         source_url="https://gender-pay-gap.service.gov.uk/employers/2/2024")
    picked = {r["signal_id"] for r in correct.targets(correct.current_rows(conn))}
    assert picked == {mangled.signal_id, stale.signal_id}


def test_a_row_whose_key_is_already_right_is_left_alone(conn):
    signal = validate.build_signal(read(), raw(), "uk_paygap")
    assert store.store(conn, signal) == "stored"
    assert correct.targets(correct.current_rows(conn)) == []


def test_the_guard_refuses_a_worklist_big_enough_to_mean_a_broken_normaliser():
    """Re-keying a tenth of the corpus is not a correction, it is somebody
    having broken company_key — and it would withdraw and republish every one
    of those rows before anyone noticed."""
    rows = [{"company": "Acme Inc.", "company_key": "wrong"} for _ in range(60)]
    with pytest.raises(correct.Unsafe):
        correct.targets(rows)
    assert len(correct.targets(rows, force=True)) == 60


def test_the_guard_does_not_fire_on_a_table_too_small_to_judge():
    """Three rows out of four is 75% and evidence of nothing."""
    rows = [{"company": "Acme Inc.", "company_key": "wrong"} for _ in range(3)]
    rows += [{"company": "Acme Inc.", "company_key": "acme"}]
    assert len(correct.targets(rows)) == 3


# --- what a corrected row says ---------------------------------------------

def test_a_corrected_row_says_what_a_fresh_one_would_say(conn, mangled):
    fixed = correct.corrected_signal(row_for(conn, mangled.signal_id))
    fresh = validate.build_signal(read(), raw(), "uk_paygap")

    assert fixed.company_key == fresh.company_key == "co-operative group"
    assert fixed.content_hash == fresh.content_hash


def test_only_the_key_and_the_hash_it_feeds_can_move(conn, mangled):
    """A correction that could rewrite a headline, a figure or a read-through
    would be a republication wearing a correction's name. materiality is
    deliberately absent: compute_materiality does not read the key, so
    recomputing it here could only introduce a difference."""
    before = row_for(conn, mangled.signal_id)
    fixed = correct.corrected_signal(before)

    moved = {name for name in correct._FIELDS if getattr(fixed, name) != before[name]}
    assert moved == {"company_key", "content_hash"}


def test_the_hash_moves_which_is_why_this_is_a_revision(conn, mangled):
    """company_key is the first input to content_hash. Leaving the old hash on
    a row whose key changed means the next collection of the same document
    matches nothing and publishes it a second time — which is the defect this
    pass exists to end, not one to reintroduce one field along."""
    fixed = correct.corrected_signal(row_for(conn, mangled.signal_id))
    assert fixed.content_hash != mangled.content_hash
    assert fixed.signal_id == mangled.signal_id


# --- the merge --------------------------------------------------------------

def test_two_spellings_of_one_employer_end_up_under_one_key(conn):
    """The whole point of EMPLOYER_KEY_ALIASES. Both halves are live, they claim
    one profile URL, and includes/company.php refuses to serve either until they
    are one employer."""
    edgar = stored_under(
        conn, "perma-fix environmental services",
        company="Perma-Fix Environmental Services, Inc.",
        headline="Perma-Fix Environmental Services, Inc. officer change",
        source_url="https://www.sec.gov/Archives/edgar/data/891532/d8k.htm")
    execcomp = validate.build_signal(
        read(company="PERMA FIX ENVIRONMENTAL SERVICES INC",
             headline="PERMA FIX ENVIRONMENTAL SERVICES INC pay versus performance"),
        raw(headline="PERMA FIX ENVIRONMENTAL SERVICES INC pay versus performance",
            source_url="https://www.sec.gov/Archives/edgar/data/891532/pvp.htm"),
        "sec_execcomp")
    assert store.store(conn, execcomp) == "stored"

    picked = correct.targets(correct.current_rows(conn))
    assert [r["signal_id"] for r in picked] == [edgar.signal_id], (
        "only the spelling that loses moves; the survivor is already right")
    assert correct.corrected_signal(picked[0]).company_key == execcomp.company_key


def test_the_surviving_spelling_is_the_one_sql_can_find_without_the_index():
    """tit_company_rows()'s fast path is REPLACE(company_key,' ','-') = slug, in
    SQL. A survivor carrying an accent, an ampersand or a hyphen would only be
    reachable through the precomputed slug index, for no reason: the pair
    already contains a spelling that does not need it."""
    import re
    import unicodedata

    for variant, survivor in vocab.EMPLOYER_KEY_ALIASES.items():
        legacy = survivor.replace(" ", "-")
        folded = unicodedata.normalize("NFKD", survivor.lower())
        folded = "".join(c for c in folded if not unicodedata.combining(c))
        canonical = re.sub(r"[^a-z0-9]+", "-", folded.replace("&", " and ")).strip("-")
        assert legacy == canonical, (
            f"{survivor!r} survives {variant!r} but needs the slug index to be "
            f"found; the other spelling would not")


def test_an_alias_never_points_at_another_alias():
    """A chain would make the result depend on how many times you applied the
    map, and company_key applies it exactly once."""
    for variant, survivor in vocab.EMPLOYER_KEY_ALIASES.items():
        assert survivor not in vocab.EMPLOYER_KEY_ALIASES, variant
        assert vocab.company_key(survivor) == survivor


# --- duplicates the correction creates -------------------------------------

def test_a_row_the_correction_turns_into_a_duplicate_is_withdrawn(conn, mangled):
    """Merging two spellings makes this likelier here than in the pillar pass:
    the two halves are by definition the same employer. The site's unique key is
    (content_hash, revision) and every insert it makes is revision 1, so a
    second row carrying a live row's hash would not error, it would silently
    never land."""
    # A second document that fingerprints the same: same employer, pillar, date
    # and headline. That is what content_hash says "one record" means.
    twin = validate.build_signal(
        read(), raw(source_url="https://gender-pay-gap.service.gov.uk/employers/9/2024"),
        "uk_paygap")
    assert store.store(conn, twin) == "stored"
    assert twin.signal_id != mangled.signal_id

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [mangled.signal_id]


def test_a_row_the_site_would_refuse_as_near_duplicate_is_withdrawn(conn, mangled):
    """Different document, different headline, different hash, and the site
    still refuses it: tit_insert_signal() also matches employer, pillar,
    direction and a 14-day window. publish() would count that as a duplicate
    without naming it and mark the row published, leaving the site showing
    neither revision."""
    other = validate.build_signal(
        read(headline="Co-operative Group Limited pay gap figures for 2024"),
        raw(headline="Co-operative Group Limited pay gap figures for 2024",
            published_date="2024-04-11",
            source_url="https://gender-pay-gap.service.gov.uk/employers/1/2024b"),
        "uk_paygap")
    assert store.store(conn, other) == "stored"

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert to_move == []
    assert [r["signal_id"] for r in dupes] == [mangled.signal_id]


def test_the_same_employer_outside_the_window_is_not_a_duplicate(conn, mangled):
    other = validate.build_signal(
        read(headline="Co-operative Group Limited pay gap figures for 2025"),
        raw(headline="Co-operative Group Limited pay gap figures for 2025",
            published_date="2025-04-03",
            source_url="https://gender-pay-gap.service.gov.uk/employers/1/2025"),
        "uk_paygap")
    store.store(conn, other)

    to_move, dupes = correct.split_duplicates(
        conn, correct.targets(correct.current_rows(conn)))
    assert [r["signal_id"] for r in to_move] == [mangled.signal_id]
    assert dupes == []


# --- the identity cache -----------------------------------------------------

def test_the_resolved_identity_follows_the_employer_to_its_new_key(conn, mangled):
    """employer_identity is keyed on company_key and holds what Wikidata and
    SEC answered, negative results included. Re-keying without this orphans the
    entry and the next enrichment pass pays for the lookups again."""
    conn.execute(
        "INSERT INTO employer_identity (company_key, company, resolved, resolved_at) "
        "VALUES (?, ?, 1, '2026-07-01T00:00:00+00:00')",
        ("-operative group", "CO-OPERATIVE GROUP LIMITED"))
    conn.commit()

    moves = correct.key_moves(correct.targets(correct.current_rows(conn)))
    assert correct.carry_identity_cache(conn, moves) == 1

    carried = conn.execute(
        "SELECT company FROM employer_identity WHERE company_key = 'co-operative group'"
    ).fetchone()
    assert carried["company"] == "CO-OPERATIVE GROUP LIMITED"


def test_carrying_the_cache_never_deletes_and_never_overwrites(conn, mangled):
    """Nothing in this pass is irreversible, and a cache is the last place to
    make an exception. An entry already resolved for the surviving key wins,
    because it was resolved for the key that survives."""
    conn.execute(
        "INSERT INTO employer_identity (company_key, company, resolved, resolved_at) "
        "VALUES (?, ?, 1, '2026-07-01T00:00:00+00:00')",
        ("-operative group", "old name"))
    conn.execute(
        "INSERT INTO employer_identity (company_key, company, resolved, resolved_at) "
        "VALUES (?, ?, 1, '2026-07-02T00:00:00+00:00')",
        ("co-operative group", "the survivor's own resolution"))
    conn.commit()

    moves = correct.key_moves(correct.targets(correct.current_rows(conn)))
    assert correct.carry_identity_cache(conn, moves) == 0

    kept = conn.execute(
        "SELECT company_key, company FROM employer_identity ORDER BY company_key"
    ).fetchall()
    assert [r["company"] for r in kept] == ["old name", "the survivor's own resolution"]


# --- resumability -----------------------------------------------------------

@pytest.fixture
def withdrawn():
    """Records what the site was asked to retract, in place of the network."""
    calls = []
    return calls, lambda signal_id, reason: calls.append(signal_id)


def test_the_original_survives_and_the_replacement_is_unpublished(conn, mangled, withdrawn):
    calls, withdraw = withdrawn
    correct.reissue(conn, row_for(conn, mangled.signal_id), withdraw=withdraw)

    assert calls == [mangled.signal_id], "a published row is taken off the site first"

    old = conn.execute(
        "SELECT company_key, is_current FROM signals WHERE signal_id = ? AND revision = 1",
        (mangled.signal_id,)).fetchone()
    assert old["is_current"] == 0
    assert old["company_key"] == "-operative group", (
        "the record of what was published must not be rewritten")

    new = row_for(conn, mangled.signal_id)
    assert new["revision"] == 2
    assert new["company_key"] == "co-operative group"
    assert new["published_at"] is None
    assert [r["signal_id"] for r in publish.unpublished(conn)] == [mangled.signal_id]


def test_a_row_already_re_issued_is_not_a_target_again(conn, mangled, withdrawn):
    """The whole pass is resumable because the worklist is derived from what is
    stored, so an interrupted run simply picks up where the rows are."""
    _, withdraw = withdrawn
    correct.reissue(conn, row_for(conn, mangled.signal_id), withdraw=withdraw)
    assert correct.targets(correct.current_rows(conn)) == []


def test_an_unpublished_row_is_not_retracted(conn, withdrawn):
    """A row the site has never seen has nothing to withdraw, and asking would
    be a request that can only fail."""
    calls, withdraw = withdrawn
    signal = validate.build_signal(read(), raw(), "uk_paygap")
    store.store(conn, signal)
    conn.execute("UPDATE signals SET company_key = '-operative group' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()

    correct.reissue(conn, row_for(conn, signal.signal_id), withdraw=withdraw)
    assert calls == []

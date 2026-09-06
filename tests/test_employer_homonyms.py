"""Two different employers sharing one name, and the registries that say so.

`EMPLOYER_KEY_ALIASES` answers "which spelling of this name wins".
`HOMONYM_EMPLOYER_KEYS` answers a question that map cannot express: WHICH
EMPLOYER IS THIS, when two of them are called the same thing.

The case that forced it. /company/indigo/ was live, titled "IndiGo: 4 tracked
updates on hiring, funding and leadership", with a meta description reading
"$50M disclosed funding, 2 leadership changes" — an American medical-malpractice
insurtech's Series B attributed to the Indian airline, in the line search
engines index. One key held both companies, so the fusion was in the database
and not merely in the URL, and no slug fix could have reached it.

Everything here is written to fail if the fix is taken back out, and the two
that matter most are the ones that fail on a REPAIR that looks reasonable:
a default branch (test_an_industry_the_registry_does_not_name_stays_ambiguous)
and a registry key that has quietly stopped matching anything
(test_every_reviewed_collision_key_is_claimed_by_a_live_employer).
"""

import os
import re
import sqlite3
import unicodedata

import pytest

import correct_company_key as correct
import ops_status
from pipeline import vocab


# --- the split itself ------------------------------------------------------

def test_the_indigo_conflation_is_split():
    """The four rows that shared one key land on two, by employer."""
    airline = [
        ("IndiGo", "transport_logistics"),      # Istanbul route + hiring drive
        ("Indigo", "hospitality_travel"),       # Elbers resigns (airliners.de)
        ("IndiGo", "transport_logistics"),      # Walsh appointed (intereconomia)
        ("인디고", "hospitality_travel"),         # the same resignation, in Korean
    ]
    insurtech = [("Indigo", "financial_services")]

    keys = {vocab.company_key(n, industry=i) for n, i in airline}
    assert keys == {"indigo airline"}, keys
    assert {vocab.company_key(n, industry=i) for n, i in insurtech} == {"indigo insurance"}
    # The whole point: they are no longer the same employer.
    assert vocab.company_key("Indigo", industry="financial_services") != \
        vocab.company_key("IndiGo", industry="transport_logistics")


def test_the_korean_spelling_is_a_base_key_and_never_an_alias():
    """'인디고' resolves by industry in its own right.

    The first attempt aliased it onto 'indigo' and let the homonym map finish
    the job. test_identity::test_an_alias_may_only_merge_two_spellings_of_one
    _name refused that, correctly: the alias map's whole safety property is
    that both sides slug the same, and a wholly-Hangul name slugs to the empty
    string. Pinned here so nobody restores the alias to save three lines.
    """
    assert "인디고" not in vocab.EMPLOYER_KEY_ALIASES
    assert "인디고" in vocab.HOMONYM_EMPLOYER_KEYS
    assert vocab.company_key("인디고", industry="hospitality_travel") == "indigo airline"
    # One table, so the two spellings cannot drift into different verdicts.
    assert vocab.HOMONYM_EMPLOYER_KEYS["인디고"] is vocab.HOMONYM_EMPLOYER_KEYS["indigo"]
    assert vocab.company_key("인디고", industry="technology") == "인디고"


# --- the third state, and the repair that must never be made ---------------

def test_an_industry_the_registry_does_not_name_stays_ambiguous():
    """NO DEFAULT BRANCH. This is the assertion to read before "improving" the
    resolver.

    Industry is model-assigned, and the airline's own rows already disagreed
    about it (transport_logistics on two, hospitality_travel on a third). Any
    fallback — first branch, most common branch, longest match — eventually
    files an insurtech row under the airline or the reverse, silently and
    permanently, which is the defect this registry exists to end rather than
    to relocate. Unresolved is a state.
    """
    for industry in ("technology", "retail_ecommerce", "", None, "AIRLINE"):
        assert vocab.company_key("Indigo", industry=industry) == "indigo", industry


def test_an_unresolved_homonym_row_is_reported_and_not_treated_as_current():
    """The staleness check cannot see this row, so something else must.

    A row left on the base key returns that key from company_key, so
    `company_key(name) != stored_key` is False and [1c]'s staleness pass calls
    it current. It is not current; it is sitting on a key that means two
    companies. ops_status reports it separately, and this is that contract.
    """
    stored, fresh = "indigo", vocab.company_key("Indigo", industry="technology")
    assert fresh == stored, "precondition: the staleness check sees nothing wrong"
    assert fresh in vocab.HOMONYM_EMPLOYER_KEYS, "so the ambiguity report must catch it"


def test_ops_status_reports_an_unresolved_homonym_row(tmp_path, capsys):
    """End to end through the real reporting function, on a corpus of one row."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE signals (is_current INT, company TEXT, "
                 "company_key TEXT, industry TEXT)")
    conn.execute("INSERT INTO signals VALUES (1, 'Indigo', 'indigo', 'technology')")
    conn.commit()
    conn.row_factory = sqlite3.Row

    problems = ops_status._report_employer_keys(conn)
    out = capsys.readouterr().out
    assert "means TWO" in out and "'indigo'" in out
    assert any("HOMONYM_EMPLOYER_KEYS" in p for p in problems)
    assert any("Do NOT add a default branch" in p for p in problems)


# --- the branch keys have to behave like every other key -------------------

def test_every_branch_key_is_one_sql_can_find_without_the_index():
    """The rule EMPLOYER_KEY_ALIASES survivors follow, applied to branches.

    tit_company_rows()'s fast path is REPLACE(company_key,' ','-') = slug, in
    SQL. A branch key carrying an accent or a hyphen would be reachable only
    through the precomputed slug index, and a key we invent has no excuse for
    needing it.
    """
    for base, branches in vocab.HOMONYM_EMPLOYER_KEYS.items():
        for industry, branch in branches.items():
            legacy = branch.replace(" ", "-")
            folded = unicodedata.normalize("NFKD", branch.lower())
            folded = "".join(c for c in folded if not unicodedata.combining(c))
            canonical = re.sub(r"[^a-z0-9]+", "-",
                               folded.replace("&", " and ")).strip("-")
            assert legacy == canonical, (
                f"branch {branch!r} for {base!r}/{industry} needs the slug index")


def test_a_branch_key_is_never_itself_a_base_key_or_an_alias():
    """No chains. company_key applies each map once, so a branch that were also
    a base key would make the answer depend on how many times you asked."""
    for base, branches in vocab.HOMONYM_EMPLOYER_KEYS.items():
        assert base not in vocab.EMPLOYER_KEY_ALIASES, (
            f"{base!r} is both an alias variant and a homonym base")
        for branch in branches.values():
            assert branch not in vocab.HOMONYM_EMPLOYER_KEYS
            assert branch not in vocab.EMPLOYER_KEY_ALIASES
            assert vocab.company_key(branch) == branch


def test_industry_changes_no_key_outside_the_registry():
    """The kwarg is inert everywhere else, proved over the stored corpus.

    company_key feeds content_hash, so a kwarg that moved any other employer
    would silently re-key rows nobody asked about. 19,000-odd keys is the only
    honest way to say "nothing else changed".
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "talent_intel.db")
    if not os.path.exists(path):
        pytest.skip("committed corpus not present")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    names = conn.execute(
        "SELECT DISTINCT company, industry FROM signals WHERE is_current = 1 "
        "  AND company IS NOT NULL AND company <> ''").fetchall()
    conn.close()
    assert len(names) > 1000, "corpus too small for this to mean anything"

    moved = set()
    for company, industry in names:
        bare = vocab.company_key(company)
        with_industry = vocab.company_key(company, industry=industry)
        if bare != with_industry:
            moved.add(bare)
    assert moved <= set(vocab.HOMONYM_EMPLOYER_KEYS), (
        f"industry moved keys outside the registry: "
        f"{sorted(moved - set(vocab.HOMONYM_EMPLOYER_KEYS))}")


# --- the guard for the defect that put a reviewed pair back in the queue ----

def test_every_reviewed_collision_key_is_claimed_by_a_live_employer():
    """A registry keyed on a DERIVED value goes stale when the value moves, and
    nothing goes red when it does.

    SAME_EMPLOYER_NO_ASCII_KEY and DISTINCT_EMPLOYER_SLUG_COLLISIONS are keyed
    by the published slug. On 2026-09-06 one of those keys was
    'giay-thuong-inh', written when tit_company_slug deleted the đ outright.
    U+0111 later joined _ATOMIC_FOLDS and folds to 'd' like every other stroked
    letter, so the pair claims 'giay-thuong-dinh' now: the lookup in ops_status
    missed, the pair dropped out of `unnameable` into `undecided`, and the
    report went back to telling a human to "decide which spelling wins" about a
    pair an earlier session had correctly refused to alias. Reviewing a
    collision twice is cheap; MERGING one because the note explaining why not
    had gone invisible is not.

    So: every key in either registry must be a slug the employers it names
    actually produce today.

    THIS DID NOT START LIFE AS A CLEAN ZERO. On the day it was written it
    failed five times in a row and each failure was real: the đ pair above, the
    NH pair, and all four DISTINCT_EMPLOYER_SLUG_COLLISIONS entries, every one
    of them recording a collision the Hangul romaniser had already resolved.
    """
    for registry_name in ("SAME_EMPLOYER_NO_ASCII_KEY",
                          "DISTINCT_EMPLOYER_SLUG_COLLISIONS"):
        registry = getattr(vocab, registry_name)
        for slug, owners in registry.items():
            produced = {ops_status._profile_slug(o) for o in owners}
            assert produced == {slug}, (
                f"{registry_name}[{slug!r}] names owners that now slug to "
                f"{sorted(produced)}. The slugifier moved and this key did "
                f"not follow, so ops_status no longer finds this entry and "
                f"is asking for a decision that was already made.")


def test_the_reviewed_pairs_are_reachable_from_the_report():
    """The end the previous test protects: each reviewed pair is classified,
    not offered up as undecided."""
    for slug in vocab.SAME_EMPLOYER_NO_ASCII_KEY:
        owners = sorted(vocab.SAME_EMPLOYER_NO_ASCII_KEY[slug])
        assert tuple(owners) == tuple(sorted(
            vocab.SAME_EMPLOYER_NO_ASCII_KEY.get(slug, ()))), slug


# --- what a split must NOT carry -------------------------------------------

def test_a_split_key_carries_no_identity_cache():
    """key_moves omits a key that split, because there is nowhere to carry to.

    `employer_identity` holds pipeline.identity's verdict, and for a fused key
    that verdict is a fact ABOUT the ambiguity: 'indigo' is recorded as "no
    organisation among 2 candidates" precisely because two companies answered
    to it. Copying that onto a branch would tell the next enrichment pass that
    a now-unambiguous employer is unresolvable.
    """
    rows = [
        {"company_key": "indigo", "company": "IndiGo",
         "industry": "transport_logistics"},
        {"company_key": "indigo", "company": "Indigo",
         "industry": "financial_services"},
        {"company_key": "félix", "company": "Félix", "industry": None},
    ]
    moves = correct.key_moves(rows)
    assert "indigo" not in moves, "a split key has two destinations, so it has none"
    assert moves == {"félix": "felix"}, moves


def test_a_merge_still_carries_its_identity_cache():
    """The other side of the boundary: ordinary merges are untouched."""
    rows = [{"company_key": "félix", "company": "Félix", "industry": None},
            {"company_key": "félix", "company": "Félix",
             "industry": "financial_services"}]
    assert correct.key_moves(rows) == {"félix": "felix"}

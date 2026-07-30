"""Ranking may change the ORDER of the read budget and never the SET.

The whole safety argument for `pipeline/candidate_rank.py` is one property: it is
a permutation. If it can drop, filter or promote a candidate then it has become a
second, unreviewed eligibility rule sitting in front of `precheck`, the gate and
`validate` — and unlike those it was never designed to make that decision.

Nothing here touches a network or a model, because nothing in the module can.
"""

from __future__ import annotations

import pytest

from pipeline import candidate_rank, schema, store, validate


def item(headline, *, locale=None, source_country=None, collector="google_news",
         body=""):
    raw = f"{headline}\n\n{body}".strip()
    out = {"raw_text": raw, "headline": headline, "collector": collector,
           "source_url": "https://example.com/a/", "source_name": "Example"}
    if locale:
        out["locale"] = locale
    if source_country:
        out["source_country"] = source_country
    return out


CONTEXT = candidate_rank.Context(
    rows_by_country={"US": 10339, "GB": 4801, "IE": 10, "KE": 0},
    known_employers=frozenset({"acme corp"}))


# --- the property that makes it safe ---------------------------------------

def test_rank_is_a_permutation_and_nothing_else():
    items = [item(f"Employer{i} to create {i}00 new jobs in Nairobi",
                  locale=("KE:en" if i % 2 else "US:en")) for i in range(40)]
    out = candidate_rank.rank(items, CONTEXT)
    assert len(out) == len(items)
    # Identity, not equality: the same dicts come back, so nothing was rebuilt,
    # normalised, copied or quietly edited on the way through.
    assert sorted(map(id, out)) == sorted(map(id, items))


def test_ranking_is_stable_so_an_unscored_run_is_unchanged():
    """A collector whose items carry no signal at all must behave exactly as it
    did before this module existed, or the change is not free after all."""
    items = [item(f"Something vague number {i}") for i in range(25)]
    assert candidate_rank.rank(items, CONTEXT) == items


def test_ranking_is_deterministic():
    items = [item("Acme Corp raises $20 million Series B", locale="KE:en"),
             item("Beta Ltd names new CEO", locale="US:en"),
             item("Gamma plc to create 400 new jobs", locale="GB:en")]
    assert (candidate_rank.rank(items, CONTEXT)
            == candidate_rank.rank(list(items), CONTEXT))


def test_an_empty_context_still_scores_and_never_raises():
    """A dry run with no database ranks on keyword force alone."""
    empty = candidate_rank.Context()
    items = [item("Acme Corp raises $20 million Series B"),
             item("Something vague")]
    assert candidate_rank.rank(items, empty)[0] is items[0]


def test_context_from_a_missing_table_is_empty_rather_than_fatal(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "bare.db")
    ctx = candidate_rank.Context.for_conn(conn)
    assert ctx.rows_by_country == {} and ctx.known_employers == frozenset()
    conn.close()


# --- the signals ------------------------------------------------------------

def test_a_country_holding_nothing_outranks_the_two_that_hold_everything():
    """The measured defect: 15,140 of 15,711 rows are US or GB."""
    us = item("Employer A to create 300 new jobs", locale="US:en")
    kenya = item("Employer B to create 300 new jobs", locale="KE:en")
    assert candidate_rank.rank([us, kenya], CONTEXT) == [kenya, us]


def test_a_thin_country_sits_between_an_empty_one_and_a_saturated_one():
    empty = item("Employer B to create 300 new jobs", locale="KE:en")
    thin = item("Employer C to create 300 new jobs", locale="IE:en")
    full = item("Employer A to create 300 new jobs", locale="US:en")
    assert candidate_rank.rank([full, thin, empty], CONTEXT) == [empty, thin, full]


def test_the_country_hint_is_read_from_either_shape_a_collector_uses():
    assert candidate_rank.candidate_country(item("x", locale="KE:en")) == "KE"
    assert candidate_rank.candidate_country(
        item("x", source_country="Kenya")) == "KE"
    assert candidate_rank.candidate_country(
        item("x", source_country="KE")) == "KE"
    assert candidate_rank.candidate_country(item("x")) is None


def test_the_locale_wins_over_the_publishers_country():
    """`locale` is the edition asked; `source_country` is the outlet's home. When
    a collector supplies both, the edition is the closer hint to the story."""
    both = item("x", locale="KE:en", source_country="United States")
    assert candidate_rank.candidate_country(both) == "KE"


def test_a_stated_amount_or_title_outranks_a_vague_mention():
    stated = item("Acme Corp raises $20 million Series B")
    vague = item("Report says the jobs market is changing")
    assert candidate_rank.keyword_force(stated) > candidate_rank.keyword_force(vague)
    assert candidate_rank.rank([vague, stated], CONTEXT) == [stated, vague]


def test_a_filing_outranks_a_news_item_all_else_equal():
    news = item("Employer A to create 300 new jobs", locale="US:en",
                collector="google_news")
    filing = item("Employer A to create 300 new jobs", locale="US:en",
                  collector="sec_edgar")
    assert candidate_rank.rank([news, filing], CONTEXT) == [filing, news]


def test_country_need_outweighs_every_other_signal_together():
    """Deliberate: concentration is the measured defect, so it leads. If this
    ever stops being true it is a decision, not a drifting weight."""
    others = (candidate_rank.W_SOURCE_TIER + candidate_rank.W_EMPLOYER_NEW
              + 3 * candidate_rank.W_KEYWORD_FORCE)
    assert candidate_rank.W_COUNTRY_EMPTY > others - candidate_rank.W_COUNTRY_THIN


# --- the report -------------------------------------------------------------

def test_explain_prints_what_moved_rather_than_that_something_moved():
    items = [item("Employer A to create 300 new jobs", locale="US:en")] * 5
    items += [item("Employer B to create 300 new jobs", locale="KE:en")]
    note = candidate_rank.explain(items, CONTEXT, top=3)
    assert "ranked 6 candidate(s)" in note
    assert "1 from countries with no stored rows (was 0)" in note


def test_explain_on_an_empty_run_says_nothing_rather_than_zero_of_zero():
    assert candidate_rank.explain([], CONTEXT) == ""


# --- integration: the run still stores the same rows ------------------------

@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "rank.db")
    yield c
    c.close()


def test_the_context_reads_the_corpus_it_claims_to(conn):
    store.store(conn, validate.build_signal(
        {"company": "Acme Corp", "pillar": "company_development",
         "signal_direction": "hiring", "city": "Nairobi", "country": "Kenya",
         "confidence": "reported",
         "headline": "Acme Corp to create 300 new jobs in Nairobi",
         "summary": "Acme Corp will add 300 roles in Nairobi.",
         "talent_readthrough": "300 roles in Nairobi."},
        {"raw_text": "Acme Corp to create 300 new jobs in Nairobi",
         "source_url": "https://www.nation.africa/a/", "source_name": "Nation",
         "published_date": "2026-07-20"}, "national_press"))
    conn.commit()
    ctx = candidate_rank.Context.for_conn(conn)
    assert ctx.country_rows("KE") == 1
    assert ctx.country_rows("US") == 0
    # `company_key` strips the corporate suffix, so the key is "acme".
    assert "acme" in ctx.known_employers


def test_run_collect_ranks_without_changing_what_it_stores(monkeypatch):
    """The offline pipeline must store exactly what it stored before.

    Ranking sits between the prefilter and the loop, so the guard against it
    having become a filter is the end-to-end row count, not only the unit test
    above.
    """
    import run_collect

    seen: list[int] = []
    real_rank = run_collect.candidate_rank.rank

    def spy(items, context):
        seen.append(len(items))
        out = real_rank(items, context)
        assert sorted(map(id, out)) == sorted(map(id, items))
        return out

    monkeypatch.setattr(run_collect.candidate_rank, "rank", spy)
    assert run_collect.run(dry_run=True, offline=True, run_index=0,
                           limit=None) == 0
    assert seen and seen[0] > 0

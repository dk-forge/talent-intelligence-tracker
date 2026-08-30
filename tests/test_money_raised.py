"""What may be added into a public "money raised" total, and what may not.

WHY THESE EXIST. On 2026-08-20 the live tracker printed $564.79bn "raised" over
4,238 published rows. The top of that set held five different kinds of thing
that are not a company raising money -- a VC closing its own fund, a
government award, an investment pledge, an outbound spend and a divestiture
price -- and the total added every one of them up.

The divestiture is the one that decides the shape of this file. Nothing failed
to READ it: the classifier labelled that row `divestiture`, correctly, and the
sum included it anyway, because no money query had ever looked at the column.
Three guards over the funding columns were green the whole time. So these
tests are deliberately split in two:

  * the CLASSIFIER tests, using the real headlines as fixtures, and
  * the QUERY tests, which read the plugin source and assert that no surface
    sums funding_amount_usd without the basis clause.

The second half is the half that was missing. A classifier nobody consults is
worth exactly nothing, and that is not a hypothetical: it is what shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipeline import money_raised, validate, vocab

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"


# --- The definition, on the rows that produced it ---------------------------
#
# Every fixture below is a real published row, quoted from the live corpus on
# 2026-08-20, with the deal_type the pipeline actually stored on it.

#: (deal_type, company, headline, summary, expected basis)
REAL_ROWS = [
    # A DIVESTITURE. The label was there and correct; the sum ignored it.
    ("divestiture", "Alibaba",
     "Alibaba said to be selling gaming arm for US$1.5 billion in boost to AI pivot",
     "Alibaba is reportedly selling its gaming arm for US$1.5 billion.",
     "divestiture"),

    # AN OUTBOUND INVESTMENT. Nvidia is paying, and $1.5bn of Nvidia's spending
    # was published as $1.5bn Nvidia raised.
    (None, "Nvidia",
     "Nvidia to invest $1.5b in SB Energy",
     "Nvidia will invest $1.5 billion in SoftBank-backed SB Energy and secure "
     "up to 8 gigawatts of AI computing capacity at an Ohio campus.",
     money_raised.OUTBOUND_INVESTMENT),

    # A STATE SUBSIDY. A public award is not an investment round.
    (None, "NextEra",
     "NextEra secures $3.3bn in state funding for 10GW of new natural gas "
     "generation to meet growing data centre demand",
     "NextEra has raised $3.3bn.",
     money_raised.STATE_FUNDING),

    # A FUND-LEVEL RAISE. $3.5bn that will be DEPLOYED into companies; counting
    # it and then counting the rounds it funds is the same dollar twice.
    (None, "Accel",
     "Accel raises $3.5 billion to invest in emerging global AI startups",
     "Accel has raised $3.5 billion.",
     money_raised.FUND_RAISE),

    # The same kind again, written the way the asset-management press writes
    # it: a "strategy" rather than a "fund".
    (None, "Pantheon",
     "Exclusive | Pantheon Raises $3.2 Billion for Co-Investment Strategy",
     "Pantheon has raised $3.2 billion for its co-investment strategy.",
     money_raised.FUND_RAISE),

    # A PLEDGE. Commitments from a state visit are not capital received.
    (None, "Marcos",
     "Marcos secures US$2.5B in investment commitments from Canada visit",
     "Marcos has raised US$2.5B.",
     money_raised.PLEDGE),
]


@pytest.mark.parametrize("deal_type,company,headline,summary,expected", REAL_ROWS)
def test_the_real_published_rows_are_excluded(deal_type, company, headline,
                                              summary, expected):
    assert money_raised.basis(deal_type, company, headline, summary) == expected


#: Rows that must stay IN, because a definition that only ever says no is a
#: definition that empties a page. Every one of these is a real corpus row too.
REAL_RAISES = [
    # The biggest raise on the site. Nothing about it is ambiguous and the
    # rules above must not touch it.
    (None, "Anthropic",
     "Anthropic raises $65B in Series H funding at $965B post-money valuation",
     "Anthropic has raised $65B in Series H funding."),

    # THE FUND IS THE INVESTOR, not the raiser. capital_event.py names this
    # trap and this module inherits it: a rule reading `\\bfund\\b` would refuse
    # a real seed round.
    (None, "Emergent",
     "Emergent raises $70M from Khosla Ventures and SoftBank Vision Fund 2",
     "Emergent has raised $70M."),

    # THE DEBT RULING, in the direction that keeps rows. Venture debt is how a
    # private round is papered; only market instruments are out, and that line
    # is capital_event's, unchanged.
    (None, "Kids2", "Kids2 Raises $225M in Debt Funding",
     "Kids2 has raised $225M in debt funding."),

    # A ROUND ANNOUNCED BESIDE AN ACQUISITION. deal_type is `acquisition` and
    # correct -- that IS the corporate event -- but the $17 million is the
    # Series B, not the price of Trustana.
    ("acquisition", "Graas",
     "Singapore's Graas raises $17 million Series B, acquires product-data "
     "startup Trustana",
     "Graas has raised $17 million in a Series B round and acquired Trustana."),

    # THE ROW'S EMPLOYER IS THE INVESTEE, not the spender. One sentence, two
    # opposite verdicts, decided by whose row it is.
    (None, "Gnani.ai",
     "Aavishkaar Capital invests $10 mn in Gnani.ai Series B round",
     "Aavishkaar Capital has invested $10 million in Gnani.ai's Series B "
     "funding round."),

    # THE VERB IS PART OF THE EMPLOYER'S OWN NAME. Two Form D filers supplied
    # both halves of the outbound test out of their own names.
    (None, "Powerhouse Investing Group Holding Company, LLC",
     "Powerhouse Investing Group Holding Company, LLC raised $1M in a private "
     "placement",
     "Powerhouse Investing Group Holding Company, LLC raised $1M."),

    # A PRODUCT, not a fund. "Vehicle" was in the fund pattern until this row.
    (None, "Vince Gaydarzhiev",
     "Bulgarian Founder Vince Gaydarzhiev Raises $10M for AI Vehicle "
     "Inspection Startup",
     "The startup has raised $10M."),
]


@pytest.mark.parametrize("deal_type,company,headline,summary", REAL_RAISES)
def test_a_real_round_is_still_a_real_round(deal_type, company, headline, summary):
    assert money_raised.basis(deal_type, company, headline, summary) \
        == money_raised.COMPANY_RAISE


def test_a_seam_between_two_texts_cannot_manufacture_a_subject():
    """The look-back window must not read the headline as the summary's clause.

    "Qatar's Rasmal Ventures backs Yuno's $45 million Series B" plus a summary
    beginning "Rasmal Ventures has invested in Yuno's $45 million round" put
    `Yuno` forty characters behind `invested` ACROSS THE JOIN, and Yuno's own
    round read as Yuno spending money. Three real rounds were excluded that way.
    """
    assert money_raised.basis(
        None, "Yuno",
        "Qatar's Rasmal Ventures backs Yuno's $45 million Series B",
        "Rasmal Ventures has invested in Yuno's $45 million Series B funding "
        "round, with participation from Andreessen Horowitz and Tiger Global.",
    ) == money_raised.COMPANY_RAISE


def test_a_shared_generic_word_is_not_evidence_of_the_subject():
    """"Balerion Space Ventures Invests in Northwood Space $100M Series B" is
    Northwood's round. Matching ANY token of "Northwood Space" found `Space`
    inside the INVESTOR's name."""
    assert money_raised.basis(
        None, "Northwood Space",
        "Balerion Space Ventures Invests in Northwood Space $100M Series B Round",
        "Balerion Space Ventures has invested $100M in Northwood Space's "
        "Series B round.",
    ) == money_raised.COMPANY_RAISE


def test_the_verdict_is_never_none():
    """The third state exists in the COLUMN, never in the function.

    A row the classifier has seen always says which of the two it is. NULL in
    the database means "never examined", and it can only get there by a row
    never reaching this function -- not by this function declining to answer.
    """
    for deal_type, company, headline, summary, _ in REAL_ROWS:
        assert money_raised.basis(deal_type, company, headline, summary)
    assert money_raised.basis(None, None) == money_raised.COMPANY_RAISE


def test_every_verdict_is_a_real_deal_type():
    """The verdict's home is the deal_type column, following capital_event.

    A value this module invents that vocab does not know would be written to a
    row, refused by the site's closed vocabulary, and become an uncountable
    silent drop -- the exact shape capital_event.py exists to avoid.
    """
    for kind in money_raised.MONEY_KINDS:
        assert vocab.normalize_deal_type(kind) == kind
    for kind in money_raised.EXCLUDING_DEAL_TYPES:
        assert kind in vocab.DEAL_TYPES


# --- The write path actually asks ------------------------------------------

def _build(headline, summary, raw_text, **classified):
    base = {
        "company": "Acme", "pillar": "company_development",
        "signal_direction": "neutral", "confidence": "reported",
        "headline": headline, "summary": summary,
        "talent_readthrough": "Capital changes the hiring picture.",
    }
    base.update(classified)
    return validate.build_signal(
        base,
        {"raw_text": raw_text, "source_url": "https://example.com/story",
         "source_name": "Example", "published_date": "2026-07-20"},
        "google_news",
    )


def test_build_signal_writes_the_verdict_on_every_figure():
    signal = _build(
        "Acme raises $70M in Series B funding",
        "Acme has raised $70M in Series B funding.",
        "Acme raises $70M in Series B funding, led by Example Ventures.",
        funding_amount="$70M",
    )
    assert signal.funding_amount_usd == 70_000_000
    assert signal.money_basis == money_raised.COMPANY_RAISE


def test_build_signal_excludes_an_outbound_spend_but_keeps_the_row():
    """The figure survives; only its membership of a total does not.

    That is the difference from capital_event, which NULLS the columns. A bond
    is not a funding round in any sense. A divestiture price and a fund close
    are correctly extracted figures that belong on the row and in its detail
    panel, so the row keeps them and money_basis is what keeps them out of the
    sum.
    """
    signal = _build(
        "Acme to invest $1.5b in Beta Energy",
        "Acme will invest $1.5 billion in Beta Energy.",
        "Acme to invest $1.5b in Beta Energy, securing capacity at an Ohio campus.",
        funding_amount="$1.5b",
    )
    assert signal.money_basis == money_raised.OUTBOUND_INVESTMENT
    assert signal.funding_amount_usd == 1_500_000_000
    assert signal.deal_type == money_raised.OUTBOUND_INVESTMENT


def test_a_row_with_no_figure_is_not_given_a_verdict():
    """NULL where there is nothing to judge. Four rows in five are this."""
    signal = _build(
        "Acme names a new chief executive",
        "Acme has appointed a new chief executive.",
        "Acme names a new chief executive.",
    )
    assert signal.funding_amount_usd is None
    assert signal.money_basis is None


# --- No surface may sum without the clause ---------------------------------

#: Every plugin file that adds up money. Named rather than globbed, because a
#: NEW file that sums without the clause should make somebody add it here and
#: think about it, and a glob would let it pass on the day it was written.
SUMMING_FILES = (
    "includes/shortcodes.php",
    "includes/press.php",
    "includes/places.php",
)

#: How a query says it has heard of the money basis. The clause is sometimes
#: inline and sometimes held in a local built from tit_money_where(), and both
#: are the same claim.
_MONEY_MARKERS = ("money_basis", "$money", "$matrix_money", "$money_sum",
                  "$money_where")

#: The window around a sum that counts as "the same query". A basis clause in
#: the WHERE governs a SUM in the SELECT list, so requiring it INSIDE the SUM
#: would fail correct code; requiring it nowhere near would pass broken code.
_QUERY_SPAN = 700


def _sums_of_the_money_column(php: str):
    """Every SUM(...) whose body names funding_amount_usd, with its span.

    Bracket-matched rather than sliced to the next ')', for the reason
    tests/phpsource.py exists: a SUM body here contains tit_country_expr()
    calls and interpolated locals, and a naive slice cuts them in half and
    then reports the half it kept as unguarded.
    """
    out = []
    for match in re.finditer(r"\bSUM\s*\(", php, re.I):
        depth, i = 0, match.end() - 1
        while i < len(php):
            if php[i] == "(":
                depth += 1
            elif php[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = php[match.end():i]
        if "funding_amount_usd" in body:
            out.append((match.start(), body))
    return out


def test_the_summable_verdict_is_asked_for_by_name():
    """`money_basis = 'company_raise'`, never `NOT IN (...)`.

    Written as an exclusion list, every future value would land on the summable
    side by default AND NULL WOULD BE SUMMED -- and NULL is the state that
    means "this row was never examined". A row nothing has judged is not a row
    judged to be a company raise. That equivalence is the entire defect.
    """
    php = (PLUGIN / "includes/api.php").read_text()
    assert "function tit_money_where()" in php
    where = php.split("function tit_money_where()", 1)[1].split("}", 1)[0]
    assert "money_basis = '" in where
    assert "NOT IN" not in where.upper()
    assert "IS NOT NULL" not in where.upper()


def test_an_unexamined_row_cannot_reach_a_total():
    """The predicate must reject NULL, which is what equality does.

    Asserted on the SQL rather than on a comment, because a comment cannot be
    wrong in a way that changes a published number.
    """
    php = (PLUGIN / "includes/api.php").read_text()
    where = php.split("function tit_money_where()", 1)[1].split("}", 1)[0]
    # An equality against a non-empty literal is never true for NULL in SQL.
    assert re.search(r"money_basis\s*=\s*'?\s*\"?\s*\.?\s*tit_money_basis_summable",
                     where) or "money_basis = '" in where


@pytest.mark.parametrize("relative", SUMMING_FILES)
def test_no_surface_sums_the_money_column_without_the_basis_clause(relative):
    """THE GUARD THAT WAS MISSING.

    The sibling tracker logs this exact failure as "the surface that forgot the
    filter": a hand-written rollup omitted the dedup clause and listed a total
    and the rows it absorbed, both summed. Here there were six money sums
    across three files and NONE of them had ever asked what the figures were
    of.

    So every `SUM(... funding_amount_usd ...)` in the plugin must carry the
    basis test inside it. The check reads the SQL text, which is the only thing
    the database sees.
    """
    php = (PLUGIN / relative).read_text()
    naked = []
    for start, body in _sums_of_the_money_column(php):
        near = php[max(0, start - _QUERY_SPAN):start + _QUERY_SPAN]
        if not any(m in body or m in near for m in _MONEY_MARKERS):
            naked.append(" ".join(body.split())[:110])
    assert not naked, (
        f"{relative} sums funding_amount_usd without the money-basis clause:\n  "
        + "\n  ".join(naked))


def test_the_coverage_sentence_admits_what_it_has_not_judged():
    """A public figure that leaves rows out has to say so.

    The old sentence said "Totals cover the 4,204 of 4,616 funding updates that
    state a US dollar amount", which was true about currencies and silent about
    everything else -- and silence is how an unexamined figure gets added up.
    """
    php = (PLUGIN / "includes/shortcodes.php").read_text()
    body = php.split("function tit_money_coverage_sentence(", 1)[1]
    body = body.split("\nfunction ", 1)[0]
    assert "unjudged" in body
    assert "money the employer raised" in body


def test_the_column_travels_to_the_site():
    """A verdict the site never receives is a verdict the site cannot apply.

    publish.FIELDS is the allowlist for new rows and ENRICHABLE is the only
    route onto rows the site already holds. money_basis needs both: the second
    is how the 4,238 rows that were already published get judged without being
    re-sent, which the near-duplicate guard would refuse anyway.
    """
    from pipeline import publish
    assert "money_basis" in publish.FIELDS
    assert "money_basis" in publish.ENRICHABLE

    api = (PLUGIN / "includes/api.php").read_text()
    enrichable = api.split("function tit_enrichable_columns()", 1)[1]
    enrichable = enrichable.split("\n}", 1)[0]
    assert "'money_basis'" in enrichable


# --- The figure and the rows behind it --------------------------------------
#
# A public number whose own link lands on a different set of rows is the same
# defect as a wrong number, arriving one click later. These hold the three
# pieces that make the "Total Raised" link honest, and they are three pieces
# because any one of them alone is silent.


def _dashboard_js() -> str:
    return (PLUGIN / "assets/dashboard.js").read_text()


def test_the_money_figure_links_to_the_rows_it_summed():
    """`funding=1` alone is the WIDER population.

    It includes every row the figure leaves out -- divestiture prices, fund
    closes, outbound spends, state subsidies, pledges -- so a reader who
    clicked the total landed on a table that does not add up to it. The link
    has to name the basis, and has to name it from the one function that
    decides what is summable rather than by retyping the value.
    """
    php = (PLUGIN / "includes/shortcodes.php").read_text()
    defs = php.split("function tit_signal_defs()", 1)[1].split("\n}", 1)[0]
    money = [ln for ln in defs.splitlines() if "'money'" in ln and "array(" in ln]
    assert money, "the money row is gone from tit_signal_defs()"
    row = defs[defs.index(money[0]):]
    row = row[:row.index("'money')") + len("'money')")]
    assert "money_basis=" in row, (
        "the Total Raised link does not carry money_basis, so it points at the "
        "wider funding view and the rows under the figure do not add up to it"
    )
    assert "tit_money_basis_summable" in row, (
        "the link retypes the summable value instead of asking the function "
        "that decides it, so a rename would leave the link pointing at a value "
        "no row carries -- zero rows, reading as a quiet week"
    )


def test_the_browser_carries_the_parameter_that_link_depends_on():
    """THE HALF THAT MAKES THE LINK REAL, and the half that fails silently.

    dashboard.js forwards only the parameters it has a control for. Without
    the control, `money_basis=company_raise` is dropped in the browser and the
    link behaves exactly as the wider view did while looking precise -- which
    is worse than the bug it replaced, because it also looks fixed.
    """
    js = _dashboard_js()
    php = (PLUGIN / "includes/shortcodes.php").read_text()
    assert "id=\"tit-f-money_basis\"" in php, "the control is not rendered"
    assert "money_basis: document.getElementById('tit-f-money_basis')" in js, (
        "money_basis is not in the `inputs` map, so refresh() never puts it "
        "in the querystring"
    )
    multi = js.split("var MULTI = {", 1)[1].split("};", 1)[0]
    assert "money_basis" in multi, (
        "money_basis is not a MULTI filter, so the page reads .value off a "
        "<select multiple> and sends one basis where the reader chose several"
    )
    assert "data.money_bases" in js, "the control is never filled from /facets"

    api = (PLUGIN / "includes/api.php").read_text()
    facets = api.split("function tit_api_facets()", 1)[1].split("\nfunction ", 1)[0]
    assert "'money_bases'" in facets, (
        "/facets does not offer money_bases, so the control renders empty and "
        "hides itself, and the link it exists for is dropped again"
    )


def test_every_basis_a_reader_can_pick_has_words():
    """A control that offers `outbound_investment` is showing its schema.

    The vocabulary is what the API accepts; the map is what the page prints.
    A new money kind added to money_raised.py reaches the control by itself,
    through /facets, and would arrive wearing its stored name.
    """
    js = _dashboard_js()
    labels = js.split("var MONEY_BASIS_LABEL = {", 1)[1].split("};", 1)[0]
    api = (PLUGIN / "includes/api.php").read_text()
    allowed = api.split("function tit_allowed_money_bases()", 1)[1].split("}", 1)[0]
    allowed += api.split("function tit_allowed_deal_types()", 1)[1].split("}", 1)[0]
    values = set(re.findall(r"'([a-z_]+)'", allowed))
    missing = sorted(v for v in values if f"{v}:" not in labels)
    assert not missing, f"no reader-facing label for: {missing}"

    # And the words come from the shared vocabulary, not from a second opinion
    # about what a fund close is called.
    for value in ("fund_raise", "outbound_investment", "state_funding", "pledge"):
        for word in vocab.DEAL_TYPE_LABELS[value].split():
            assert word.capitalize() in labels or word in labels, (
                f"{value} is labelled with words vocab.DEAL_TYPE_LABELS does "
                f"not use ({vocab.DEAL_TYPE_LABELS[value]!r})"
            )


# --- One event, one row -----------------------------------------------------

def test_a_backer_qualifier_is_not_part_of_the_employers_name():
    """Two outlets, one $2bn round, two rows, $2bn counted twice.

        "Thrive Holdings Raises $2B To Expand AI-Powered Business Roll-Ups"
        "OpenAI-backed Thrive Holdings raises $2B to bring AI to the enterprise"

    Both dedup layers require company_key EQUALITY, so the two never met. The
    qualifier says who the investors are, which is a fact about the round and
    never a fact about the employer's identity.
    """
    assert vocab.company_key("OpenAI-backed Thrive Holdings") \
        == vocab.company_key("Thrive Holdings")
    assert vocab.company_key("Nvidia-backed Nscale") == vocab.company_key("Nscale")
    assert vocab.company_key("Google-backed Isomorphic") \
        == vocab.company_key("Isomorphic")


def test_the_qualifier_strip_cannot_eat_a_real_name():
    """The sibling tracker's documented false-merge, avoided by construction.

    It refuses to strip revision words from an employer key because
    "Revision Optics, Inc." would key as `optics` and could then merge with an
    unrelated employer. Only a HYPHENATED participle is stripped here, only
    from the front, and only when a name survives it.
    """
    assert vocab.company_key("Asset Backed Securities Corp") == "asset backed securities"
    assert vocab.company_key("Revision Optics, Inc.") == "revision optics"
    assert vocab.company_key("CO-OPERATIVE GROUP LIMITED") == "co-operative group"
    # Nothing but the qualifier: stripping would leave an empty key that every
    # other nameless row would then collide with.
    assert vocab.company_key("SoftBank-backed") != ""


def test_two_outlets_reporting_one_round_now_dedupe(tmp_path):
    """End to end through the layer that was missing them.

    funding_event_duplicate matches employer + amount inside a window, and it
    matched neither before, because the employer keys differed.
    """
    from pipeline import dedupe, schema

    # A real file under tmp_path, not ":memory:". schema.connect ATTACHes a
    # second database derived from the path it is given, so an in-memory
    # connection writes a literal ":memory:_cache" file into the checkout.
    conn = schema.connect(str(tmp_path / "t.db"))
    signal = _build(
        "Thrive Holdings Raises $2B To Expand AI-Powered Business Roll-Ups",
        "Thrive Holdings has raised $2B.",
        "Thrive Holdings Raises $2B To Expand AI-Powered Business Roll-Ups.",
        company="Thrive Holdings", funding_amount="$2B",
    )
    from pipeline import store
    assert store.store(conn, signal) == "stored"

    assert dedupe.funding_event_duplicate(
        conn,
        vocab.company_key("OpenAI-backed Thrive Holdings"),
        2_000_000_000, "$2000.0m",
        published_date="2026-07-20",
    ) == signal.signal_id


# --- The instrument is the last question, not an assumed round ---------------
#
# `docs/RULING-public-equity-proceeds.md`: equity sold into public markets is
# excluded. The two phrasings that missed it were added to capital_event on
# 2026-08-29, and that fix governed NEW WRITES ONLY. The rows already stored
# kept deal_type NULL and money_basis company_raise, and correct_money_basis.py
# could not reach them, because it derives its verdict by calling basis() and
# basis() never asked the instrument. The correction the ruling prescribes was
# a no-op on the exact rows the ruling was about.

@pytest.mark.parametrize("headline", [
    "Alibaba to issue US$10 billion in new shares for huge AI push",
    "Alibaba raises US$10 billion in record Hong Kong share sale",
])
def test_stored_public_equity_proceeds_are_excluded_on_rejudgement(headline):
    """Both Alibaba rows, as they are actually stored: no deal_type at all."""
    from pipeline import capital_event
    assert capital_event.classify(headline) == "public_offering", (
        "the capital_event vocabulary fix has regressed; this test is then "
        "passing for the wrong reason")
    assert money_raised.basis(None, "Alibaba", headline, "") == "public_offering"


def test_basis_is_a_no_op_on_the_write_path():
    """THE PROPERTY THAT MAKES THIS SAFE.

    build_signal calls capital_event FIRST and nulls the figure when it answers,
    so basis() is only ever reached on a write with text capital_event has
    already declined. The new branch re-asks the same function with the same
    inputs, so it declines again and nothing about a new write changes. Asserted
    rather than reasoned: if capital_event ever declines here, basis() must not
    manufacture an exclusion from it.
    """
    from pipeline import capital_event
    for headline, summary in [
        ("Acme raises $70M in Series B funding", "Acme has raised $70M."),
        ("Beta closes $12M seed round", "Beta closed a $12M seed round."),
        ("Gamma raises $400m led by Menlo Ventures", "A Series C."),
    ]:
        assert capital_event.classify(headline, summary) is None
        assert money_raised.basis(None, "Acme", headline, summary) == \
            money_raised.COMPANY_RAISE


def test_every_capital_event_kind_is_one_a_total_acts_on():
    """The two vocabularies must stay in step.

    `basis()` only lets an EXCLUDING kind through, so a capital event that is
    not in `EXCLUDING_DEAL_TYPES` would be silently dropped back to
    `company_raise` -- an instrument we correctly identified and then summed
    anyway, which is the original defect wearing a new hat. Today every kind is
    excluding, so this asserts the state rather than a hypothetical; the day
    somebody adds one, this fails and names it.
    """
    from pipeline import capital_event
    stray = set(capital_event.STATS) - money_raised.EXCLUDING_DEAL_TYPES
    assert not stray, (
        f"capital_event can return {sorted(stray)}, which money_raised does not "
        f"exclude. Add them to EXCLUDING_DEAL_TYPES and to "
        f"tit_allowed_deal_types() in includes/api.php, or basis() will sum "
        f"the figure it identified.")


def test_the_site_can_filter_every_verdict_basis_can_write():
    """A money_basis the site's closed vocabulary does not hold is a value a
    reader can see on a row and never select."""
    php = (PLUGIN / "includes/api.php").read_text()
    allowed = php.split("function tit_allowed_deal_types()", 1)[1].split("}", 1)[0]
    from pipeline import capital_event
    for kind in sorted(capital_event.STATS):
        assert f"'{kind}'" in allowed, (
            f"{kind} can be written as a money_basis but is not in "
            f"tit_allowed_deal_types()")


def test_a_named_private_round_still_beats_the_instrument():
    """The transaction-price exception is unchanged: a real round beside a
    listing plan is still a round."""
    signal = money_raised.basis(
        None, "Delta",
        "Delta raises $30M Series B ahead of a possible IPO next year",
        "Delta has raised $30M in a Series B.")
    assert signal == money_raised.COMPANY_RAISE

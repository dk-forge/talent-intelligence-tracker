"""A price is not a raise.

"Masimo to be Acquired by Danaher for $180.00 Per Share" was published with
funding_amount "$9.9 billion" and funding_amount_usd 9,900,000,000 — the sum
Danaher is paying FOR Masimo, taken from Masimo's own 8-K exhibit 99.1, counted
as money Masimo raised. It sat at the top of the money list until a human read
the filing (retracted 2026-07-29).

Every existing guard passed, and that is the point of this file. The figure IS
in the source, verbatim, so `assert_figures_are_sourced` and `_sourced_figure`
were both satisfied; the employer is real and named; the source is sec.gov, so
the row earned `verified`. Nothing in the pipeline asked what the number WAS.

Three things are asserted here, and each of them is a way the fix could rot:

  * the four shapes named in the incident are refused — an 8-K reporting that
    the company is being acquired, a merger price, a per-share cash price, a
    shelf registration
  * a genuine raise still stores its figure, from every route that produces one
  * the correction pass and the pipeline judge a row by the SAME function, so
    the rows already on the site and the rows collected tomorrow cannot be
    graded by two different rules
"""

from __future__ import annotations

import pytest

import correct_not_capital_raised as correct
from pipeline import schema, store, validate

SEC_URL = "https://www.sec.gov/Archives/edgar/data/937556/000110465926015904/tm266533d1_ex99-1.htm"

# The exhibit's own words, as far as they matter here.
MASIMO_BODY = (
    "Masimo Corporation (NASDAQ: MASI) and Danaher Corporation today announced "
    "that they have entered into a definitive merger agreement under which "
    "Danaher will acquire Masimo for $180.00 per share in cash, representing "
    "total consideration of approximately $9.9 billion. The transaction is "
    "expected to close in the second half of 2026, subject to customary "
    "closing conditions and regulatory approvals."
)
MASIMO_HEADLINE = "Masimo to be Acquired by Danaher for $180.00 Per Share"
MASIMO_SUMMARY = (
    "Masimo Corporation announced it has entered into a definitive agreement "
    "to be acquired by Danaher Corporation for $180.00 per share in cash, "
    "representing a total consideration of $9.9 billion."
)


def raw(**over):
    base = {
        "raw_text": f"{MASIMO_HEADLINE}\n\n{MASIMO_BODY}",
        "headline": MASIMO_HEADLINE,
        "source_url": SEC_URL,
        "source_name": "SEC EDGAR",
        "published_date": "2026-02-17",
        "country": "United States",
        "cik": "937556",
    }
    base.update(over)
    return base


def read(**over):
    base = {
        "company": "Masimo Corporation",
        "pillar": "company_development",
        "signal_direction": "neutral",
        "confidence": "verified",
        "headline": MASIMO_HEADLINE,
        "summary": MASIMO_SUMMARY,
        "talent_readthrough": (
            "Masimo's 8,500 staff pass to Danaher on closing; the filing names "
            "no role changes."),
        "funding_amount": "$9.9 billion",
    }
    base.update(over)
    return base


def build(collector="sec_edgar", **over):
    return validate.build_signal(read(**over), raw(**over), collector)


# --- the incident ----------------------------------------------------------

def test_an_acquisition_price_is_not_stored_as_funding():
    """The row this whole change exists for."""
    signal = build()
    assert signal.funding_amount is None
    assert signal.funding_amount_usd is None


def test_the_deal_is_still_published_as_a_company_development_row():
    """Only the money field was wrong. Withdrawing the M&A signal as well would
    trade one wrong answer for a missing one — the acquisition of an 8,500-person
    employer is exactly what a recruiter came here to see."""
    signal = build()
    assert signal.pillar == "company_development"
    assert signal.headline == MASIMO_HEADLINE
    assert signal.summary == MASIMO_SUMMARY
    assert signal.confidence == "verified"
    assert signal.source_url == SEC_URL


def test_the_per_share_price_is_refused_too():
    """$180.00 is the same mistake one order of magnitude down, and it is the
    figure a model is most likely to reach for: it is the one in the headline."""
    signal = build(funding_amount="$180.00")
    assert signal.funding_amount is None


def test_a_merger_price_is_refused():
    text = ("Beta Systems, Inc. announced a definitive merger agreement under "
            "which Gamma Holdings will pay $2.4 billion in cash for all "
            "outstanding shares.")
    signal = build(
        collector="google_news",
        headline="Beta Systems to merge with Gamma Holdings in $2.4 billion deal",
        summary=text, funding_amount="$2.4 billion",
        raw_text=text, source_url="https://www.reuters.com/business/beta-gamma/",
        source_name="Reuters", company="Beta Systems, Inc.")
    assert signal.funding_amount is None


def test_a_shelf_registration_ceiling_is_refused():
    """A shelf is permission to sell, not money received. On the day it is
    filed the company has raised nothing, and "up to" is the whole difference."""
    text = ("Acme Corp filed a registration statement permitting it to offer "
            "up to $500 million of securities from time to time.")
    signal = build(
        collector="google_news", company="Acme Corp",
        headline="Acme Corp files $500 million shelf registration",
        summary=text, funding_amount="$500 million", raw_text=text,
        source_url="https://www.acme.com/news/shelf-registration",
        source_name="Acme Corp")
    assert signal.funding_amount is None


def test_money_in_a_filing_about_an_officer_change_is_refused():
    """Compass, Inc. carried $1,000.0 million on an Item 5.02 filing. The money
    is REAL — a convertible note issuance — and it is not what this record
    reports. If we want that raise it has to come from the document that
    announces it, not from a leadership row that mentions it in passing."""
    body = ("Item 5.02 Departure of Directors or Certain Officers. On January 9, "
            "2026 the Company appointed a new Chief Operating Officer. The "
            "Company also completed its previously announced acquisition of "
            "Anywhere Real Estate Inc. and issued $1,000.0 million aggregate "
            "principal amount of 0.25% Convertible Senior Notes due 2031.")
    signal = build(
        company="Compass, Inc.",
        headline="Compass, Inc. 8-K filing (Item 5.02): officer or director change",
        summary=("Compass, Inc. completed its acquisition of Anywhere Real "
                 "Estate Inc. and issued $1,000.0 million in aggregate "
                 "principal amount of Convertible Senior Notes."),
        funding_amount="$1,000.0 million", raw_text=body, pillar="leadership_change",
        source_url="https://www.sec.gov/Archives/edgar/data/1563190/x/comp.htm")
    assert signal.funding_amount is None
    assert signal.pillar == "leadership_change"


def test_a_blank_check_trust_is_refused_on_the_same_filing_shape():
    """Irenic Acquisition Corp., $220,000,000. A SPAC's trust is not capital
    that funds a payroll: the entity employs nobody."""
    body = ("Item 5.02. The Company appointed a director. $220,000,000 of the "
            "proceeds of the initial public offering was placed in trust.")
    signal = build(
        company="Irenic Acquisition Corp.",
        headline="Irenic Acquisition Corp. 8-K filing (Item 5.02): officer or director change",
        summary="Irenic Acquisition Corp. reported a change in its officers.",
        funding_amount="$220,000,000", raw_text=body, pillar="leadership_change",
        source_url="https://www.sec.gov/Archives/edgar/data/2122505/x/8k.htm")
    assert signal.funding_amount is None


def test_the_stage_goes_with_the_figure():
    """The page's "Funding raised" tile counts a row carrying a figure OR a
    stage, so a stage left behind keeps the row in the funding count with
    nothing to show for it."""
    signal = build(funding_stage="other")
    assert signal.funding_amount is None
    assert signal.funding_stage is None


def test_the_two_arms_cover_each_other():
    """They are complementary by construction and neither is redundant.

    Where the model keeps the collector's boilerplate headline, the document
    arm fires and the price arm has nothing to read. Where the model REPLACED
    that headline it found something specific in the filing — which is the
    Masimo case exactly — so the document arm stands down and the price arm is
    the only thing between the figure and the money total.
    """
    officer = "Acme Corp 8-K filing (Item 5.02): officer or director change"
    assert validate.forced_pillar("sec_edgar", officer)
    assert validate.deal_price_term("$50M", headline=officer, summary="", raw_text="") is None
    assert validate.not_capital_raised("$50M", collector="sec_edgar", headline=officer)

    assert validate.forced_pillar("sec_edgar", MASIMO_HEADLINE) is None
    assert validate.deal_price_term(
        "$9.9 billion", headline=MASIMO_HEADLINE, summary=MASIMO_SUMMARY)


def test_the_headline_arm_reads_a_price_it_is_not_holding():
    """Masimo's headline prints $180.00 per share while the stored figure was
    $9.9 billion. Anything that looked only at the stored figure's own
    surroundings in the headline would have found nothing there."""
    assert "9.9" not in MASIMO_HEADLINE
    assert validate.deal_price_term("$9.9 billion", headline=MASIMO_HEADLINE)


# --- what must still be stored ---------------------------------------------
#
# The failure mode of this fix is over-reach, so these are the regression half.
# A rule that suppressed real raises would take the money charts to zero and
# every one of these is a shape the live table holds today.

REAL_RAISES = [
    ("Enigma raises $71M seed round for physical-AI robotics",
     "Enigma raised $71M in a seed round led by Sequoia.", "$71M"),
    ("ChangXin Memory raises $8.6bn from state investors",
     "ChangXin Memory raised $8.6bn from Chinese state investors.", "$8.6bn"),
    ("Acme raises $50M in debt financing to fund acquisitions",
     "Acme raised $50M in debt financing. The company said the money would "
     "fund acquisitions of smaller rivals.", "$50M"),
    ("Holobiome closes $10M Series A",
     "Holobiome closed a $10M Series A for microbiome therapeutics.", "$10M"),
]


@pytest.mark.parametrize("headline,summary,figure", REAL_RAISES,
                         ids=lambda v: v[:28] if isinstance(v, str) else v)
def test_a_genuine_raise_keeps_its_figure(headline, summary, figure):
    signal = build(
        collector="google_news", company=headline.split(" raise")[0].split(" closes")[0],
        headline=headline, summary=summary, funding_amount=figure,
        raw_text=f"{headline}\n\n{summary}", pillar="company_development",
        source_url="https://techcrunch.com/2026/07/20/funding-round/",
        source_name="TechCrunch")
    assert signal.funding_amount == figure


def test_a_form_d_private_placement_keeps_its_figure():
    """The Form D path builds its `classified` dict in Python with no model at
    all, and it is where most of the corpus's money comes from. Its wording —
    "private placement", "offering", "sold" — must not read as a deal."""
    from collectors import sec_form_d_bulk as bulk

    item = {
        "headline": "Compass, Inc. raised $2.8M in a private placement",
        "money": "$2.8M", "city": "New York", "state": "NY",
        "country": "United States", "published_date": "2026-01-14",
    }
    body = (
        "Compass, Inc. filed a Form D with the SEC reporting $2.8M "
        "(2,800,000 dollars) sold in a private securities offering. "
        "Form D filings are required for exempt offerings and are the public "
        "record of private fundraising.")
    classified = bulk.as_classified(item)
    classified["funding_amount"] = "$2,800,000"
    signal = validate.build_signal(
        classified,
        {"raw_text": f"{item['headline']}\n\n{body}", "headline": item["headline"],
         "source_url": "https://www.sec.gov/Archives/edgar/data/1563190/x/primary_doc.xml",
         "source_name": "SEC EDGAR (Form D)", "published_date": "2026-01-14",
         "country": "United States"},
        bulk.COLLECTOR)
    assert signal.funding_amount == "$2,800,000"
    assert signal.funding_amount_usd == 2_800_000


def test_an_officer_change_from_another_source_keeps_its_money():
    """The document arm is scoped to the source whose headline is written by
    the collector. A news story about a raise that also mentions a CFO
    appointment is not that, and must not be swept up."""
    text = ("Matternet raised $1.0 million in a bridge round and appointed a "
            "new chief financial officer.")
    signal = build(
        collector="google_news", company="Matternet, Inc.",
        headline="Matternet raises $1.0 million and names new CFO",
        summary=text, raw_text=text, funding_amount="$1.0 million",
        pillar="company_development",
        source_url="https://www.axios.com/2026/07/20/matternet-bridge-round",
        source_name="Axios")
    assert signal.funding_amount == "$1.0 million"


# --- the correction pass ---------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _published(conn, signal):
    assert store.store(conn, signal) == "stored"
    conn.execute("UPDATE signals SET published_at = '2026-07-28' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    return signal


def as_published_before_the_fix():
    """The Masimo row as it actually went onto the site, figure and all.

    The funding columns are put back by hand, because a pre-fix row is by
    definition one this pipeline will no longer build — there is no collector
    name that lets that figure through any more, which is the point. Everything
    else comes from build_signal, and materiality is recomputed from the money
    so the row is internally consistent the way the stored one was ('high').
    """
    signal = validate.build_signal(read(), raw(), "sec_edgar")
    signal.funding_amount = "$9.9 billion"
    signal.funding_amount_usd = 9_900_000_000
    signal.materiality = validate.compute_materiality(
        headcount=signal.headcount, funding_usd=signal.funding_amount_usd,
        ticker=signal.ticker, cik=signal.cik, pillar=signal.pillar,
        headline=signal.headline, city=signal.city)
    assert signal.materiality == "high"
    return signal


@pytest.fixture
def live(conn):
    """That row, published."""
    return _published(conn, as_published_before_the_fix())


def row_for(conn, signal_id):
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


def test_the_correction_and_the_pipeline_use_one_rule(conn, live):
    """Not "they agree today" — that they call the same function. A correction
    pass that re-implemented the test would drift from the pipeline the first
    time either changed, and the rows already on the site would then be graded
    by a rule nothing else uses."""
    rows = correct.funded_rows(conn)
    assert [r["signal_id"] for r, _ in correct.targets(rows)] == [live.signal_id]

    fresh = build()
    assert fresh.funding_amount is None
    assert correct.corrected_signal(row_for(conn, live.signal_id)).funding_amount is None


def test_a_corrected_row_says_what_a_fresh_one_would_say(conn, live):
    fixed = correct.corrected_signal(row_for(conn, live.signal_id))
    fresh = build()
    assert fixed.funding_amount == fresh.funding_amount is None
    assert fixed.funding_amount_usd == fresh.funding_amount_usd is None
    assert fixed.funding_stage == fresh.funding_stage is None
    assert fixed.materiality == fresh.materiality


def test_only_the_money_and_what_the_money_derives_can_move(conn, live):
    """A correction that could rewrite a headline, a company or a read-through
    would be a republication wearing a correction's name."""
    before = row_for(conn, live.signal_id)
    fixed = correct.corrected_signal(before)
    moved = {name for name in correct._FIELDS if getattr(fixed, name) != before[name]}
    assert moved <= {"funding_amount", "funding_amount_usd", "funding_stage",
                     "materiality"}
    assert "funding_amount" in moved


def test_the_hash_does_not_move_which_is_why_the_row_is_withdrawn(conn, live):
    """The whole withdraw-and-do-not-republish shape rests on this. No funding
    column is an input to content_hash, so the corrected revision hashes to the
    row the site already holds and comes back 'retracted' if it is re-sent.

    If this ever fails, the pass must re-issue rows the way correct_sec_pillar
    does instead — and it checks the same thing at runtime and refuses.
    """
    row = row_for(conn, live.signal_id)
    fixed = correct.corrected_signal(row)
    assert fixed.content_hash == live.content_hash
    assert correct.hash_is_stable(row, fixed)


def test_the_correction_is_a_revision_and_the_old_figure_survives(conn, live):
    """"What did we publish on date D" has to stay answerable, so the wrong
    figure is superseded rather than erased."""
    withdrawn = []
    correct.correct(conn, row_for(conn, live.signal_id),
                    "deal consideration", withdraw=lambda sid, why: withdrawn.append(sid))

    assert withdrawn == [live.signal_id]
    current = row_for(conn, live.signal_id)
    assert current["funding_amount"] is None
    assert current["revision"] == 2
    assert correct.NOTE in current["notes"]

    old = dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 0",
        (live.signal_id,)).fetchone())
    assert old["funding_amount"] == "$9.9 billion"
    assert old["funding_amount_usd"] == 9_900_000_000


def test_a_withdrawn_row_is_never_offered_for_publication_again(conn, live):
    """It cannot land — the site dedups on content_hash across every revision —
    so offering it would have it sent once, answered 'retracted', and marked
    published, leaving the local table claiming a publication the site does not
    have."""
    from pipeline import publish

    correct.correct(conn, row_for(conn, live.signal_id), "why", withdraw=lambda *a: None)
    assert live.content_hash not in {r["content_hash"] for r in publish.unpublished(conn)}


def test_a_row_the_site_never_held_still_publishes(conn):
    """The inherited publication state has to work in both directions: a row
    that was never sent is corrected locally and then published normally."""
    from pipeline import publish

    signal = as_published_before_the_fix()
    assert store.store(conn, signal) == "stored"
    conn.commit()

    correct.correct(conn, row_for(conn, signal.signal_id), "why",
                    withdraw=lambda *a: pytest.fail("nothing to withdraw"))
    waiting = {r["content_hash"] for r in publish.unpublished(conn)}
    assert signal.content_hash in waiting


def test_the_pass_is_idempotent(conn, live):
    correct.correct(conn, row_for(conn, live.signal_id), "why", withdraw=lambda *a: None)
    assert correct.targets(correct.funded_rows(conn)) == []


def test_it_refuses_to_run_when_it_would_condemn_the_corpus(conn):
    """8 of 3,064 rows is the measured share. A rule that suddenly matches a
    twentieth of every funding row we hold is broken, and the answer to that is
    to stop rather than to withdraw a twentieth of the site."""
    rows = [{"signal_id": str(i), "collector": "sec_edgar",
             "headline": "Acme Corp 8-K filing (Item 5.02): officer or director change",
             "summary": "", "funding_amount": "$5M"}
            for i in range(correct.MIN_ROWS)]
    with pytest.raises(correct.Unsafe):
        correct.targets(rows)
    assert len(correct.targets(rows, force=True)) == len(rows)


# --- the guardrail half ----------------------------------------------------

def test_a_blank_check_name_is_flagged_at_publish_time():
    """Belt and braces for the class rather than the row: a SPAC's money can
    arrive by another route (BHAV Acquisition Corp is on the Form D path), and
    the vehicle check runs on every funding row whatever collected it."""
    from pipeline import guardrails

    assert guardrails.vehicle_match("Irenic Acquisition Corp.")
    assert guardrails.vehicle_match("Kensington Capital Acquisition Corp. VI")
    assert guardrails.vehicle_match("Four Leaf Acquisition Corporation")


@pytest.mark.parametrize("name", [
    # Real employers whose names read the same way. Every one of these is in
    # the live table, and flagging them would be the cost that makes a name
    # check not worth running.
    "SuRo Capital Corp.", "Rithm Capital Corp.", "Ladder Capital Corp",
    "UWHARRIE CAPITAL CORP", "Great Elm Capital Corp.",
    "Compass, Inc.", "Masimo Corporation", "Danaher Corporation",
])
def test_an_operating_employer_is_not_mistaken_for_a_vehicle(name):
    from pipeline import guardrails

    assert guardrails.vehicle_match(name) is None

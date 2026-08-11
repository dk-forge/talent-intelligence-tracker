"""The figure guard's tokeniser: what counts as one figure in the text.

`assert_figures_are_sourced` is the rule that "the model never invents a number",
and it works by comparing two SETS of numeric tokens. So the tokeniser decides
whether the rule fires, and every case below is one the tokeniser used to get
wrong or one that must keep working. Read the comment above `validate._NUMBER`
first; these tests are that comment, executable.

A rejection here is a success. A FALSE rejection is silent record loss — the
record is discarded, not repaired — so the tests come in pairs: what must pass,
and what must still be refused.
"""

import pytest

from pipeline import validate


# --- the bug: a magnitude taken from the next line --------------------------
#
# Collectors join their fields with a blank line ("{headline}\n\n{body}"), so a
# figure at the end of one field sat next to the first letter of the next.

def test_a_line_break_ends_a_figure():
    """The reported case, from collectors/bse_india.py's own comment."""
    raw_text = ('28.07.2026\n\nK M Sugar Mills Ltd has informed the exchange of '
                'a change in directorate.')

    assert validate._numbers_in(raw_text) == {"28072026"}
    # ...and NOT '28072026k', which is what made the same date in a summary look
    # invented and threw the whole record away.
    validate.assert_figures_are_sourced(
        "K M Sugar Mills reported a directorate change on 28.07.2026.", raw_text)


def test_a_line_break_does_not_confirm_a_magnitude_either():
    """The glue cut both ways: it could also CONFIRM a figure nobody wrote."""
    raw_text = "The round totalled 5\n\nBillion-dollar valuations are rare."

    with pytest.raises(validate.Rejected, match="not present in source text"):
        validate.assert_figures_are_sourced("The company raised $5B.", raw_text)


def test_the_junction_that_fires_this_in_production():
    """sec_execcomp: headline ends in a filing date, body opens with the company.

    465 stored rows have this exact shape (analysis/figures/replay.py). It cost
    nothing there only because the body repeats the date; strip the repeat and
    the record is gone.
    """
    headline = ("MASTEC, INC.: $9,640,917 total compensation for the principal "
                "executive officer, fiscal year ended 2022-12-31")
    raw_text = f"{headline}\n\nMASTEC, INC. (CIK 15615) reported $9,640,917."

    validate.assert_figures_are_sourced(
        "MasTec paid its principal executive officer $9,640,917 for the fiscal "
        "year ended 2022-12-31.", raw_text)


def test_a_headcount_at_the_end_of_a_line_is_still_sourced():
    """The quieter channel. The record stores either way; the FIELD is what the
    glue dropped, and a headcount is what makes a row material."""
    raw_text = "Acme to hire 300\n\nBengaluru will take most of the roles."

    assert validate._sourced_int(300, raw_text) == 300
    # The magnitude is still checked: the text says 300, not 300 million. (The
    # CURRENCY is not checked and never was — `_sourced_figure` compares digits.)
    assert validate._sourced_figure("$300M", raw_text) is None


# --- adjacency: a magnitude written against its digits must still count ------

@pytest.mark.parametrize("text, expected", [
    ("raised $1.2bn", {"12bn"}),
    ("a €5B valuation", {"5b"}),
    ("$71M seed round", {"71m"}),
    ("1,200 roles", {"1200"}),
    ("500k of debt", {"500k"}),
    ("5 million users", {"5m"}),
    # A non-breaking space is horizontal whitespace: scraped prose is full of
    # them and this is still five million.
    ("5 million users", {"5m"}),
])
def test_a_magnitude_beside_its_digits_is_part_of_the_figure(text, expected):
    assert validate._numbers_in(text) == expected


def test_a_magnitude_is_part_of_the_claim_and_not_decoration():
    """'$5B' is not sourced by a text that only says '5'. If this ever passes,
    the guard has stopped reading magnitudes and every amount is unchecked."""
    with pytest.raises(validate.Rejected, match=r"\['5b'\]"):
        validate.assert_figures_are_sourced(
            "Acme raised $5B.", "Acme raised 5 something or other.")


# --- the accident that must not be 'fixed' without a decision ---------------

@pytest.mark.parametrize("source_headline, summary_figure", [
    # Spanish, French, German, Bosnian, Danish — all real stored rows.
    ("capta 500 millones de euros", "500 million euros"),
    ("une levée de fonds de 3 millions d'euros", "3 million euros"),
    ("5U AI erhält 3,2 Millionen US-Dollar Pre-Seed", "$3.2 million"),
    ("Investicija od 150 miliona dolara", "$150 million"),
    ("rejser 25 millioner kroner", "25 million kroner"),
    # and the English case, where the publisher writes the short form
    ("Pilot Protocol Raises $4.5M in Seed Funding", "$4.5 million"),
])
def test_a_magnitude_in_another_language_still_matches_the_english_summary(
        source_headline, summary_figure):
    """This works by ACCIDENT — the suffix has no word boundary after it, so
    'millones', 'millioner' and 'Millionen' all truncate to 'm' and compare equal
    to 'million'. It is load-bearing anyway: the feed set spans 43 languages, and
    14 stored rows are sourced only through it. Adding `\\b` breaks every case
    here, which is why it did not ship. Anyone who wants the boundary has to
    bring a magnitude vocabulary first, and this test is where they find out.
    """
    validate.assert_figures_are_sourced(
        f"The company raised {summary_figure}.", source_headline)


# --- a genuine invention is still a rejection --------------------------------

def test_an_invented_amount_is_rejected():
    with pytest.raises(validate.Rejected, match="not present in source text"):
        validate.assert_figures_are_sourced(
            "Acme raised €5B in a round led by nobody in particular.",
            "Acme opened an office in Dublin and did not say what it cost.")


def test_an_invented_headcount_is_rejected():
    with pytest.raises(validate.Rejected, match=r"\['1200'\]"):
        validate.assert_figures_are_sourced(
            "Acme will hire 1,200 engineers.",
            "Acme is expanding its Dublin engineering hub.")


def test_an_invented_headcount_is_not_stored_as_a_field_either():
    assert validate._sourced_int(1200, "Acme is expanding in Dublin.") is None
    assert validate._sourced_figure("$5B", "Acme is expanding in Dublin.") is None


def test_a_year_is_the_one_exception_and_still_is():
    validate.assert_figures_are_sourced(
        "Acme opened the site in 2026.", "Acme has opened a site in Dublin.")


def test_the_whole_record_is_discarded_and_not_repaired():
    """build_signal raises rather than dropping the offending sentence."""
    from tests.test_validate import classified, raw

    with pytest.raises(validate.Rejected, match="not present in source text"):
        validate.build_signal(
            classified(summary="Stripe will add 900 roles at its Dublin hub."),
            raw(), "google_news")

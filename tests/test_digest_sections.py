"""A digest section may not promise a number its rows do not carry, and no row
counted in a headline may be printed nowhere.

THE TWO DEFECTS THIS PINS, both read in a delivered reader digest
(2026-09-14) and both reproduced here from fixtures.

  1. A LIST HEADED FOR HIRING HELD ROWS WITH NO JOB FIGURE. The delivered
     edition's "Biggest hiring signals" ranked five rows, of which the fourth
     and fifth were "Creative Investments Holding secures $20 million first
     close" and "PayTabs strikes $100 million+ deal" -- funding rows, each
     printed with no job count because neither states one. A funding round is
     not a hiring signal by this tracker's own definitions
     (pipeline/count_meaning), and a heading that ranks "the biggest" over
     rows carrying no size is a promise the rows cannot keep.

     That edition is composed in the SIBLING repository and is not fixable
     from here. What IS here is the same question asked of this repo's own
     renderer, and the answer has to keep being right: a row reaches
     SIGNALS NAMING THE MOST ROLES only when count_meaning says its headcount
     is a current opening.

  2. AN EXPLANATORY SENTENCE PRINTED TWICE IN ONE EDITION. The same delivered
     message carried the counting-basis sentence ("counted by the date the
     source published, or, for a job-board reading or a source that carries no
     date, the day we captured it") under the window figure AND again under
     the year-to-date figure, about eighty lines apart. Both copies were true.
     The second is noise, and noise is what a reader learns to skip on the way
     to the figure. The guard here is general rather than aimed at that one
     string: EVERY explanatory sentence this renderer emits must appear
     exactly once.

AND THE ONE THIS REPO ACTUALLY HAD, which the other two turned up on the way.
count_meaning declares SIX types and daily_digest.build_edition had FIVE
branches, so every `other` row -- no headcount, neither funding nor a
leadership move -- fell out of the edition: counted in "adds N signals",
named in no breakdown, printed in no section. Measured on the committed
database, 1,545 of 35,789 current rows classify as `other`. A row counted in
a headline and shown nowhere is a number the reader cannot check.

Offline and read-only: fixtures only, no database, no network, no model.
"""
import datetime as dt
import os
import re
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_digest as D  # noqa: E402
from pipeline import count_meaning as cm  # noqa: E402

SINCE = dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc)
UNTIL = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)


def row(**kw):
    """One /query-shaped row. The keys are the stored column names."""
    base = dict(headline="", summary="", talent_readthrough="", company="",
                headcount=None, headcount_scope=None, signal_direction=None,
                pillar=None, confidence="reported", collector="google_news",
                source_name="Example Wire", source_url="https://example.com/a",
                country="AE", published_date="2026-09-13", effective_date=None,
                funding_amount=None, funding_amount_usd=None,
                funding_stage=None, deal_type=None, money_basis=None)
    base.update(kw)
    return base


#: The three rows of the delivered edition that DID name jobs.
HIRING = row(company="Elnusa Petrofin",
             headline="Elnusa Petrofin adds fleet and personnel",
             summary="adding 500 staff for fuel distribution",
             headcount=500, headcount_scope="new_roles",
             signal_direction="hiring", country="ID")

#: The two that did not, and which the delivered edition ranked beside them.
#: The identity is carried by the NORMALISED amount and the basis verdict,
#: which is exactly the shape the classifier used to miss.
FUNDING = row(company="Creative Investments Holding",
              headline="Creative Investments Holding secures $20 million "
                       "first close",
              summary="closed a $20m first close, targets $50m",
              pillar="company_development", signal_direction="neutral",
              funding_amount_usd=20000000, money_basis="company_raise")

DEAL = row(company="PayTabs",
           headline="PayTabs strikes $100 million+ deal for Amazon Payment "
                    "Services MENA",
           summary="acquires Amazon Payment Services MENA",
           pillar="company_development", signal_direction="neutral",
           deal_type="acquisition")

#: No headcount, no money, no leadership move: the genuine sixth class.
BARE = row(company="Northwind", headline="Northwind opens a Dublin office",
           summary="opened a new office in Dublin", country="IE")


def edition(rows, ytd_total=26888):
    return D.build_edition(rows, SINCE, UNTIL, UNTIL, ytd_total)


def rendered(rows, **kw):
    return D.render(edition(rows, **kw))


def section_of(text, heading):
    """The body of one section, up to the next ALL-CAPS heading or the end."""
    if heading not in text:
        return ""
    rest = text.split(heading, 1)[1]
    # A heading starts at column 0 in SHOUTING CAPS; it may carry a lowercase
    # parenthetical ("FUNDING & LEADERSHIP (no hiring count)"), so only the
    # opening run is matched. Every row this renderer prints is indented.
    nxt = re.search(r"^[A-Z][A-Z &/]{5,}", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


class ARowWithNoJobFigureNeverStandsUnderTheHiringHeading(unittest.TestCase):
    """Defect 1, asked of this repo's renderer."""

    def test_a_funding_round_is_not_listed_as_a_signal_naming_roles(self):
        text = rendered([HIRING, FUNDING, DEAL])
        roles = section_of(text, "SIGNALS NAMING THE MOST ROLES")
        self.assertIn("Elnusa Petrofin", roles)
        self.assertNotIn("Creative Investments Holding", roles)
        self.assertNotIn("PayTabs", roles)

    def test_the_funding_rows_are_shown_under_a_heading_that_fits_them(self):
        """Not dropped, and not relabelled as hiring. Excluding them from the
        roles list is only half the job: a reader was shown them, and the
        honest fix shows them under a heading that describes them."""
        text = rendered([HIRING, FUNDING, DEAL])
        funding = section_of(text, "FUNDING & LEADERSHIP (no hiring count)")
        self.assertIn("Creative Investments Holding", funding)
        self.assertIn("PayTabs", funding)

    def test_no_headcount_reaches_the_roles_total_from_a_funding_row(self):
        """The total under the list sums `roles` only, so a funding row must
        contribute nothing to it whatever else changes."""
        self.assertEqual(edition([HIRING, FUNDING, DEAL]).current_roles_total,
                         500)

    def test_a_window_with_no_hiring_row_prints_no_hiring_list(self):
        """A heading over 'None this window.' is honest; a heading over
        funding rows is not. This is the case the delivered edition got
        wrong, in its purest form."""
        roles = section_of(rendered([FUNDING, DEAL]),
                           "SIGNALS NAMING THE MOST ROLES")
        self.assertIn("None this window.", roles)
        self.assertNotIn("PayTabs", roles)


class EveryExplanatorySentenceAppearsExactlyOnce(unittest.TestCase):
    """Defect 2, as a general property rather than one banned string."""

    #: Long enough to be prose rather than a label or a row. A section heading
    #: is short and a row is data; an explanation is a sentence.
    MIN_WORDS = 8

    def _sentences(self, text):
        """Every explanatory unit the renderer emits: each prose LINE, and
        each sentence inside it.

        THE LINE IS CHECKED AND NOT ONLY THE SENTENCE, because the first
        version of this guard split on terminal punctuation and the
        renderer's parenthetical line ("(current openings only; ...)") ends
        in a bracket. Duplicating that line therefore merged it with whatever
        followed, produced two different strings, and the guard passed a
        duplication it was written to catch. Proved by mutation, both ways.
        """
        out = []
        for line in text.splitlines():
            flat = re.sub(r"\s+", " ", line).strip()
            if not flat:
                continue
            # An indented line is a row; a SHOUTING line is a heading; a line
            # with a URL or a bracketed tag is data. None of them is prose.
            if line.startswith(" ") or "http" in flat or "[" in flat:
                continue
            if re.match(r"^[A-Z][A-Z &/]{5,}", flat):
                continue
            # The line AND its sentences, deduplicated WITHIN the line: a line
            # holding a single sentence yields the same string twice, which
            # would report every one-sentence paragraph as its own duplicate.
            units = dict.fromkeys(
                [flat] + [p.strip() for p in re.split(r"(?<=[.!?]) ", flat)])
            for unit in units:
                if len(unit.split()) >= self.MIN_WORDS:
                    out.append(unit)
        return out

    def test_a_full_edition_repeats_no_explanatory_sentence(self):
        text = rendered([HIRING, FUNDING, DEAL, BARE])
        dupes = {s: n for s, n in Counter(self._sentences(text)).items()
                 if n > 1}
        self.assertEqual({}, dupes,
                         f"an explanatory sentence is printed more than "
                         f"once: {dupes}")

    def test_it_holds_on_an_edition_that_fires_every_section(self):
        """The duplication a reader met was between two sections that are not
        always both present, so the guard is worth nothing unless it runs on
        an edition carrying all of them."""
        planned = row(company="Vicuna", headline="Vicuna plans 300 jobs",
                      summary="will create 300 jobs by 2029", headcount=300,
                      headcount_scope="new_roles", signal_direction="hiring")
        workforce = row(company="ONCE", headline="ONCE closes the year",
                        summary="with 80,000 existing professionals",
                        headcount=80000, headcount_scope="total_workforce",
                        signal_direction="neutral")
        ed = edition([HIRING, planned, workforce, FUNDING, BARE])
        self.assertTrue(ed.naming_roles and ed.planned and ed.workforce
                        and ed.funding_leadership and ed.other,
                        "this fixture is meant to fire all five sections")
        dupes = {s: n for s, n in Counter(self._sentences(D.render(ed))).items()
                 if n > 1}
        self.assertEqual({}, dupes, f"repeated: {dupes}")


class NoRowIsCountedInAHeadlineAndPrintedNowhere(unittest.TestCase):
    """The defect this repo actually had."""

    def test_every_featured_row_lands_in_exactly_one_section(self):
        ed = edition([HIRING, FUNDING, DEAL, BARE])
        self.assertEqual(len(ed.featured), len(ed.sectioned))
        self.assertEqual({id(f) for f in ed.featured},
                         {id(f) for f in ed.sectioned})

    def test_a_bare_signal_is_shown_rather_than_dropped(self):
        text = rendered([HIRING, BARE])
        self.assertIn("OTHER SIGNALS (no hiring count)", text)
        self.assertIn("Northwind", section_of(
            text, "OTHER SIGNALS (no hiring count)"))

    def test_the_breakdown_accounts_for_every_signal_it_counts(self):
        """"adds N signals (...)" must reconcile: the parts summed to less
        than N for any window holding an `other` row, and said nothing."""
        ed = edition([HIRING, FUNDING, DEAL, BARE])
        line = D.what_changed(ed)
        total = int(re.search(r"adds (\d+) signal", line).group(1))
        self.assertEqual(total, len(ed.featured))
        counted = sum(int(n) for n in re.findall(r"(\d+) (?:naming|projected|"
                                                 r"workforce|funding/|other)",
                                                 line))
        self.assertEqual(total, counted,
                         f"the breakdown does not account for every signal "
                         f"it counts: {line!r}")


class AMoneyIdentityIsReadFromEveryFieldThatCarriesIt(unittest.TestCase):
    """The classifier asked ONE raw text column whether a row states money,
    and the store has five fields that carry it."""

    def test_a_round_known_only_by_its_normalised_amount_is_funding(self):
        self.assertEqual(cm.classify(FUNDING).type, cm.FUNDING_OR_LEADERSHIP)

    def test_a_deal_known_only_by_its_type_is_funding_or_leadership(self):
        self.assertEqual(cm.classify(DEAL).type, cm.FUNDING_OR_LEADERSHIP)

    def test_a_row_with_no_money_field_at_all_stays_other(self):
        """THE OVER-CORRECTION GUARD. Widening the question until every row
        answers yes would empty the `other` class and make the fix
        unfalsifiable."""
        self.assertEqual(cm.classify(BARE).type, cm.OTHER)

    def test_a_zero_amount_is_not_money_stated(self):
        """Absence of a figure and a measured zero are different things
        everywhere else in this tracker, and they are here too."""
        self.assertEqual(
            cm.classify(row(headline="x", funding_amount_usd=0)).type,
            cm.OTHER)

    def test_a_numeric_amount_does_not_crash_the_classifier(self):
        """`(g("funding_amount") or "").strip()` raised AttributeError on a
        number, and funding_amount_usd IS a number. No stored row triggers it
        today, so this is a guard and not a repair."""
        self.assertEqual(
            cm.classify(row(headline="x", funding_amount=20000000)).type,
            cm.FUNDING_OR_LEADERSHIP)

    def test_a_headcount_still_outranks_a_money_field(self):
        """A funded employer that also states roles is a hiring row. The money
        branch is reached only when there is no headcount, and widening the
        money question must not have moved that boundary."""
        funded_and_hiring = dict(HIRING)
        funded_and_hiring["funding_amount_usd"] = 20000000
        self.assertEqual(cm.classify(funded_and_hiring).type,
                         cm.CONFIRMED_HIRES)


if __name__ == "__main__":
    unittest.main()

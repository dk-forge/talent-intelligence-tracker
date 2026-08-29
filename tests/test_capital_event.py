"""A bond, an IPO, a share offering and a project financing are not rounds.

THE DEFECT. Four large-company capital events in one month were stored as
funding rounds, and every one of them was caught by `guardrails.check_amounts`,
which is a MAGNITUDE check:

    ChangXin Memory   $8.6bn    a STAR Market IPO, retracted after publication
    Oracle            $25bn     a corporate bond issue
    Intel             $20bn     a public stock sale by a listed company
    Nvidia            $709bn    an infrastructure financing arrangement

They were caught for the wrong reason and the reason does not generalise
downward. Zions Bancorporation's "US$ 500 million in a senior notes issuance"
is the same class of event, sits four orders of magnitude below the derived
ceiling, and is on the live page as a funding round with nothing having asked.

Every fixture below is a REAL headline and summary out of the corpus, verbatim.
Invented ones would only prove that the regex matches the regex.

unittest rather than pytest, matching the rest of this suite: pytest is not
installed on the machine that runs these.
"""

import re
import unittest

# No `sys.modules` stub for `requests` here, deliberately, and the reason is the
# suite gotcha in CLAUDE.md: a fake module persists and shadows the real one for
# everything loaded afterwards. `build_signal` reaches `collectors.national_press`
# for `registrable_domain`, which subclasses `requests.RequestException`, so a
# stub turns every build in this file into an AttributeError. The dev lock
# installs the real thing; nothing needs faking.
from pipeline import capital_event, cheap_extract, guardrails, validate, vocab  # noqa: E402


# --- the four that cost a human decision ------------------------------------
#
# (label, headline, summary, expected verdict). Stored text, unedited.
THE_FOUR = [
    ("ChangXin Memory $8.6bn",
     "CXMT becomes China’s most valuable A-share company after $8.6 billion IPO",
     "ChangXin Memory Technologies raised $8.6 billion in its Shanghai STAR "
     "Market IPO.",
     capital_event.IPO),
    ("Intel $20bn",
     "Intel Raises $20 Billion From New Stock Sale",
     "Intel has raised $20 Billion.",
     capital_event.PUBLIC_OFFERING),
    ("Nvidia $709bn",
     "Nvidia taps Wall Street for $709b ‘AI factory’ funding deal",
     "Nvidia is working with Apollo, Blackstone, and Goldman Sachs to raise "
     "capital for the data centre boom.",
     capital_event.PROJECT_FINANCE),
]

# The one the rule deliberately does NOT catch, kept as a fixture so the gap is
# asserted rather than remembered. See DeliberateLetThroughs below.
ORACLE = ("Oracle raises $25 billion and reassures skeptical investors",
          "Oracle has raised $25 billion.")


# --- real rounds the rule must never touch ----------------------------------
#
# All stored, all current, all real. Two of them are DEBT, which is the trap:
# a debt round is an ordinary venture instrument for a private company, so
# `\bdebt\b` can never be disqualifying on its own.
REAL_ROUNDS = [
    ("debt funding is a venture instrument",
     "Kids2 Raises $225M in Debt Funding - FinSMEs", "Kids2 has raised $225M."),
    ("debt and equity together",
     "Karta Raises $140M in Debt and Equity Funding - FinSMEs",
     "Karta has raised $140M."),
    ("venture debt from a bank is still venture debt",
     "Wonder Raises USD 12 Million Venture Debt from HSBC Innovation Banking "
     "to Drive Growth and Expansion",
     "Wonder has secured USD 12 million in venture debt from HSBC Innovation "
     "Banking to fuel its growth and expansion efforts."),
    ("a company on its way to an IPO is not an IPO",
     "IPO - bound AI startup Amity raises $100m in EDBI - led Series D round",
     "Amity, an AI startup preparing for an IPO, has raised $100 million in a "
     "Series D funding round led by EDBI."),
    ("eyeing an IPO is not raising one",
     "Saudi vacation rental platform Gathern raises $72 million at a valuation "
     "of over $266 million, eyes IPO",
     "Gathern has raised $72 million in Series B round led by Sanabil "
     "Investments."),
    ("a pre-IPO financing is private money",
     "China-based CAR-T company Oricell raises $40M more as it looks to go "
     "public",
     "Oricell Therapeutics has raised more than $110 million in what it calls "
     "a pre-IPO financing."),
    ("an employee share SALE beside a round is not a share OFFERING",
     "9fin completes first employee share sale after $170m raise",
     "9fin has completed its first employee share sale following a $170m "
     "funding round."),
    ("a follow-on ROUND is not a follow-on OFFERING",
     "Gruve: $50 Million Follow-On Series A Raised For AI Infrastructure "
     "Platform",
     "Gruve has raised $50 million in a follow-on Series A funding round for "
     "its AI infrastructure platform."),
    ("a credit line as USE OF PROCEEDS is not the instrument",
     "Danish Entravel Group raises €6.5 million to secure larger supplier "
     "credit facilities",
     "Entravel Group, a TravelTech company, has raised €6.5 million to enhance "
     "supplier credit facilities, process higher booking volumes, and expand "
     "its white-label model into traditional travel sectors."),
    ("an investor named Fund is not a fund close",
     "Emergent raises $70M from Khosla Ventures and SoftBank Vision Fund 2",
     "Emergent has secured $70 million in funding from Khosla Ventures and "
     "SoftBank Vision Fund 2."),
    ("BOND in an employer's NAME is not a bond issue",
     "Bond Aviation Holdings, LLC raised $60.6M in a private placement",
     "Bond Aviation Holdings, LLC reported $60.6M sold in a private placement "
     "in a Form D filing with the SEC."),
    ("a private placement is in scope, all 2,600 of them",
     "Prime Intellect, Inc. raised $49.9M in a private placement",
     "Prime Intellect, Inc. reported $49.9M sold in a private placement in a "
     "Form D filing with the SEC."),
]


class TheFourThatCostADecision(unittest.TestCase):

    def test_each_one_is_classified_by_its_kind(self):
        for label, headline, summary, expected in THE_FOUR:
            with self.subTest(label):
                self.assertEqual(
                    capital_event.classify(headline, summary), expected,
                    f"{label}: the text says what this is and the classifier "
                    f"must read it, because the magnitude check that caught it "
                    f"cannot see anything below its own threshold")

    def test_the_figure_does_not_reach_the_funding_columns(self):
        """The point of the verdict: the money stops being a round."""
        for label, headline, summary, _kind in THE_FOUR:
            with self.subTest(label):
                signal = _signal(headline, summary)
                self.assertIsNone(signal.funding_amount, label)
                self.assertIsNone(signal.funding_amount_usd, label)
                self.assertIsNone(signal.funding_stage, label)

    def test_the_row_survives_saying_what_it_was(self):
        """A silent drop is uncountable. deal_type IS the count."""
        for label, headline, summary, kind in THE_FOUR:
            with self.subTest(label):
                self.assertEqual(_signal(headline, summary).deal_type, kind)

    def test_the_deterministic_extractor_stops_minting_them(self):
        """Intel and Oracle were both minted by cheap_extract, which read only
        the headline for the class question while the teaser sat unread."""
        item = {"headline": THE_FOUR[1][1],
                "raw_text": f"{THE_FOUR[1][1]}\n\n{THE_FOUR[1][2]}"}
        self.assertIsNone(cheap_extract.parse_funding(item))


class TheZionsCase(unittest.TestCase):
    """The whole reason a magnitude threshold was never going to be enough.

    $500m is an utterly ordinary growth round by size. It is a bank selling
    senior notes, it is live on the site as funding, and no threshold that
    lets Anthropic's $30bn through could ever have reached down to it.
    """

    HEADLINE = "Zions Bancorporation capta US$ 500 milhões em emissão de notas sênior"
    SUMMARY = "Zions Bancorporation has raised US$ 500 million in a senior notes issuance."

    def test_a_500m_bond_is_refused_although_no_threshold_sees_it(self):
        self.assertEqual(capital_event.classify(self.HEADLINE, self.SUMMARY),
                         capital_event.BOND_ISSUE)

    def test_it_is_far_below_any_threshold_the_corpus_could_derive(self):
        derived = guardrails.derive_amount_threshold([10 ** 6] * 300)["threshold"]
        self.assertGreater(derived, 500_000_000,
                           "if a derived threshold ever reached $500m it would "
                           "be quarantining most of the real corpus")


class DeliberateLetThroughs(unittest.TestCase):
    """What the rule refuses to guess at, asserted so it stays a decision.

    Precision beats recall here. A rule that refuses a real round loses
    coverage silently and for ever; a rule that lets a bond through costs one
    guardrail decision by somebody already reading that queue.
    """

    def test_oracle_is_let_through_because_the_text_never_says(self):
        """'Oracle raises $25 billion and reassures skeptical investors' names
        no instrument anywhere. The honest verdict is None, not a guess from
        the fact that Oracle is large and listed — a company can raise venture
        money in the same week it issues a bond, so identity cannot decide it.
        It stays the amount guardrail's problem, and that is the honest state.
        """
        self.assertIsNone(capital_event.classify(*ORACLE))

    def test_no_real_round_in_the_corpus_is_refused(self):
        for label, headline, summary in REAL_ROUNDS:
            with self.subTest(label):
                verdict = capital_event.explain(headline, summary)
                self.assertIsNone(
                    verdict,
                    f"{label}: refusing this loses a real round silently and "
                    f"for ever, which is the failure this tracker cannot see")

    def test_fund_closes_and_aum_stay_with_the_veto_that_already_holds_them(self):
        """Not widened into this rule on purpose: a REFUSAL on `\\bfunds?\\b`
        would refuse 'raises $70M from Khosla Ventures and SoftBank Vision
        Fund 2'. `guardrails.NOT_A_COMPANY_ROUND` can afford that word because
        it only withholds an auto-accept."""
        for headline in ("A16z Raises $15B In New Funds",
                         "Arch Surpasses $539 Billion In Private Market Assets",
                         "ASE lifts 2026 capex to record US$10.5 billion"):
            with self.subTest(headline):
                self.assertIsNone(capital_event.classify(headline))
                self.assertIsNotNone(guardrails.not_a_company_round(headline))


class TheVerdictHasAHome(unittest.TestCase):

    def test_every_kind_is_a_real_deal_type(self):
        for kind in capital_event.CAPITAL_EVENT_TYPES:
            with self.subTest(kind):
                self.assertIn(kind, vocab.DEAL_TYPES)
                self.assertEqual(vocab.normalize_deal_type(kind), kind)

    def test_a_model_read_deal_type_is_never_displaced(self):
        """Compass's 8-K: 'completed its acquisition of Anywhere Real Estate
        Inc. and issued $1,000.0 million ... Convertible Senior Notes due
        2031'. The acquisition is the better answer to 'what kind of
        transaction'; the notes are still not a funding round. Both hold."""
        signal = _signal(
            "Compass, Inc. completes Anywhere acquisition",
            "Compass, Inc. completed its acquisition of Anywhere Real Estate "
            "Inc. and issued $1,000.0 million in aggregate principal amount of "
            "the Company’s 0.25% Convertible Senior Notes due 2031.",
            deal_type="acquisition", funding_amount="$1,000.0 million")
        self.assertEqual(signal.deal_type, "acquisition")
        self.assertIsNone(signal.funding_amount_usd)

    def test_a_row_with_no_figure_is_never_relabelled(self):
        """The rule is gated on a stored figure. A leadership story that
        mentions a bond somewhere is not a bond row."""
        signal = _signal(
            "Acme Systems names Dana Brooks chief financial officer",
            "Acme Systems named Dana Brooks CFO, months after its senior notes "
            "issuance.",
            pillar="leadership_change", funding_amount="")
        self.assertIsNone(signal.deal_type)

    def test_a_refusal_is_counted(self):
        before = dict(capital_event.STATS)
        _signal(*THE_FOUR[1][1:3])
        self.assertEqual(capital_event.STATS[capital_event.PUBLIC_OFFERING],
                         before[capital_event.PUBLIC_OFFERING] + 1,
                         "an uncounted refusal is a silent drop, which is how a "
                         "source posts zero while reporting healthy")


def _signal(headline, summary, *, pillar="company_development",
            deal_type="", funding_amount=None):
    """A signal built the way a real record arrives: the model's reading over
    the collector's text, with the figure the source states."""
    raw_text = f"{headline}\n\n{summary}"
    if funding_amount is None:
        m = re.search(r"[$€£]\s?[\d,.]+(?:\s?(?:billion|million|bn|b|m))?",
                      f"{headline} {summary}", re.I)
        funding_amount = m.group(0) if m else ""
    return validate.build_signal(
        {
            "company": "Testco Holdings",
            "pillar": pillar,
            "signal_direction": "neutral",
            "confidence": "reported",
            "headline": headline,
            "summary": summary,
            "talent_readthrough": "Watch the careers page for new roles.",
            "funding_amount": funding_amount,
            "deal_type": deal_type,
        },
        {
            "raw_text": raw_text,
            "headline": headline,
            "source_url": "https://www.reuters.com/business/2026/08/11/testco/",
            "source_name": "Reuters",
            "published_date": "Tue, 11 Aug 2026 08:14:00 GMT",
        },
        "national_press")


if __name__ == "__main__":
    unittest.main()


# --- Alibaba: one announcement landing on both sides of an existing ruling ---
#
# Equity sold into public markets is already excluded, and the owner rejected
# Intel's $20bn stock sale twice on that ground. Alibaba's HK$80bn issue still
# reached the amount queue as `company_raise` twice, because neither of these
# two phrasings was in _PUBLIC_EQUITY while a THIRD headline for the same event
# ("...; third-largest follow-on offering") was excluded by it.
#
# PROVEN BY MUTATION: take either new alternative back out of _PUBLIC_EQUITY
# and the matching case below fails with classify() == None.

def test_a_qualified_share_sale_by_a_listed_issuer_is_a_public_offering():
    assert capital_event.classify(
        "Alibaba raises US$10 billion in record Hong Kong share sale"
    ) == capital_event.PUBLIC_OFFERING


def test_issuing_new_shares_is_a_public_offering():
    assert capital_event.classify(
        "Alibaba to issue US$10 billion in new shares for huge AI push amid "
        "strong investor demand") == capital_event.PUBLIC_OFFERING


def test_an_employee_share_sale_is_still_not_a_public_offering():
    """The reason the bare phrase was refused, and it stays refused.

    The amount belongs to the ROUND; the share sale belongs to the employees.
    Matching it would null a real Series row's figure.
    """
    assert capital_event.classify(
        "9fin completes first employee share sale after $170m raise") is None


def test_a_staff_share_sale_is_not_a_public_offering():
    assert capital_event.classify(
        "Acme completes staff share sale after $40m Series B") is None

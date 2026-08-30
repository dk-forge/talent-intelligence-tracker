"""WHAT "MONEY RAISED" MEANS. One definition, in code, for every surface.

THE DEFINITION

    Money raised is COMPANY-INBOUND CAPITAL: new capital that arrived at the
    employer named on the row, raised by that employer, for that employer to
    operate on.

Three words in that sentence are doing all the work, so each one is written
out below with the ruling that follows from it.

    INBOUND.  The money came IN. A company spending money is not a company
      raising it, however large the figure and however often a headline uses
      the word "funding" for both.

    THE EMPLOYER NAMED ON THE ROW.  Direction is judged from `company`, the
      same perspective `deal_type` already uses. "Aavishkaar Capital invests
      $10 mn in Gnani.ai" is an INBOUND row for Gnani.ai and would be an
      outbound row for Aavishkaar. One sentence, two opposite verdicts,
      decided entirely by whose row it is.

    RAISED.  Capital the company went out and got. Not a price paid for it,
      not a price it paid for something else, and not a promise.

WHAT IS EXCLUDED, AND WHY EACH ONE

  1. TRANSACTION PRICES — acquisition, acquired, merger, divestiture,
     joint_venture.
     A purchase price is money moving between OWNERS. "Alibaba said to be
     selling gaming arm for US$1.5 billion" is Alibaba receiving $1.5bn and it
     is still not a raise: nobody invested in Alibaba, an asset changed hands.
     `acquisition` is the same fact pointed the other way and is money leaving.
     These were already being labelled correctly by the classifier and summed
     anyway, which is the narrower half of the defect this module answers.

  2. PUBLIC AND LENDER MARKETS — ipo, bond_issue, public_offering,
     project_finance.
     Already ruled, already written down, and NOT REOPENED HERE:
     `pipeline/capital_event.py` decides these from the source's own words and
     nulls the figure at write time. This module names them in
     EXCLUDING_DEAL_TYPES only so that rows stored BEFORE that shipped — which
     still carry both a `deal_type` and a figure — cannot be summed either.

     THE DEBT RULING, stated because the brief asked which way it went.
     Debt is IN when it is a private instrument and OUT when it is a market
     one, and that line is capital_event's, unchanged. Venture debt, "debt
     funding", "debt and equity funding" and convertible notes are how real
     private rounds are papered and they stay in the total. Bonds, sukuk,
     debentures, senior notes, syndicated facilities and project finance are
     paper sold to lenders at large and stay out. The reason is not the
     instrument's legal form but who the counterparty is: a venture lender is
     backing the company, a bond market is buying an obligation.

  3. FUND-LEVEL RAISES — `fund_raise`, new here.
     An investor closing its own fund or vehicle. "Accel raises $3.5 billion
     to invest in emerging global AI startups" is $3.5bn that will be
     DEPLOYED into companies over the next several years; counting it, and
     then counting the rounds it funds when they are announced, is the same
     dollar twice. A fund close is a real event and a genuine hiring signal,
     so the row is kept and labelled. It is not money any company raised.

  4. OUTBOUND INVESTMENT — `outbound_investment`, new here.
     The named employer is the one paying. "Nvidia to invest $1.5b in SB
     Energy" arrived in the money total as $1.5bn Nvidia raised. So did
     "OpenAI injects $7 billion into employee stock buyback", which is OpenAI
     buying its own shares back — the exact opposite of a raise.

  5. GOVERNMENT MONEY — `state_funding`, new here.
     A subsidy, grant, incentive or state/federal appropriation. "NextEra
     secures $3.3bn in state funding for 10GW of new natural gas generation"
     is a public award, not an investment round, and the corpus also holds a
     long tail of municipal ones ("City of Monroe secures $17.7 million in
     federal funding for infrastructure projects") that are not company
     finance at all.

  6. PLEDGES — `pledge`, new here.
     Money announced as intended rather than received. "Marcos secures US$2.5B
     in investment commitments from Canada visit" is a commitment total from a
     state visit. A commitment is not capital, and a raise that has not closed
     is not a raise.

WHAT IS DELIBERATELY LEFT IN, named so nobody has to rediscover it

  * A fund raise the text does not disclose as one. "G Squared raises $2.3B as
    companies stay private longer than ever" and "Base10 raises $850 million
    for 'real economy' AI" are both fund closes and neither sentence says so.
    Deciding them would mean reading the RAISER'S IDENTITY — treating a name
    ending in Capital, Ventures or Partners as an allocator — and the corpus
    is full of operating companies with exactly those names. capital_event.py
    reached the same conclusion for the same reason and this module holds the
    same line: only the text of THIS story decides.

  * Non-English outbound. The patterns below read English verbs. "La Banque
    mondiale investit 25 millions USD" is correctly kept, but only because the
    row's own employer is Jumia and the money really did arrive there; a row
    where the named employer is the foreign-language SPENDER would be missed.

THE PRECISION ARGUMENT, which decides every pattern below

It is the mirror image of capital_event's, because the cost is the mirror
image. There, a wrong refusal loses a row for ever and a wrong acceptance costs
one human decision. Here nothing is dropped: an excluded row is STORED, keeps
its figure in `funding_amount`, and says on its face why it is not in the
total. So the cost of a wrong exclusion is one visible, correctable row, while
the cost of a wrong inclusion is a wrong number on a public page that nobody
notices for months — which is precisely what happened. The bias therefore runs
the other way, toward excluding what can be defended from the words on the
page. It still has to be defended from the words on the page.

THE THIRD STATE IS THE POINT

`basis()` never returns None. Every row carrying a figure gets a positive
verdict at write time, so the stored `money_basis` column has three states and
they mean three different things:

    'company_raise'    examined, and it is a raise            -> SUMMED
    an exclusion kind  examined, and it is not                -> NOT summed
    NULL               NEVER EXAMINED                         -> NOT summed

NULL is not a synonym for `company_raise` and must never be summed as one.
That equivalence is the whole defect: `deal_type` was empty on 5,584 of the
5,602 rows carrying a dollar figure, every one of them was added up, and the
empty value meant "the model was not asked" rather than "we checked". The SQL
that sums this column asks for `company_raise` by NAME, so a row this module
has not seen cannot leak into a public figure by default.

No model is called. Same input, same answer, for ever, and it costs nothing.
"""

from __future__ import annotations

import re

from . import capital_event

#: The verdict that is summed. Exactly one value, asked for by name.
COMPANY_RAISE = "company_raise"

#: "up to N characters, not crossing a sentence boundary" -- and a DECIMAL
#: POINT IS NOT A SENTENCE BOUNDARY. Written as a bare `[^.]` first, which put
#: a full stop between every raise verb and its purpose clause the moment the
#: amount had a decimal in it: "Accel raises $3.5 billion to invest in emerging
#: global AI startups" and "Pantheon Raises $3.2 Billion for Co-Investment
#: Strategy" both failed, and those are the two headlines this module was
#: written for. A period followed by a digit is part of a number.
_GAP = r"(?:[^.]|\.(?=\d)){0,70}?"

#: Verdicts this module decides itself. Every one is also a `vocab.DEAL_TYPES`
#: value, following capital_event's rule: the verdict's home is the deal_type
#: column, so the row keeps saying what it actually was and a refusal stays
#: countable with `SELECT deal_type, COUNT(*)`.
FUND_RAISE = "fund_raise"
OUTBOUND_INVESTMENT = "outbound_investment"
STATE_FUNDING = "state_funding"
PLEDGE = "pledge"

MONEY_KINDS = (FUND_RAISE, OUTBOUND_INVESTMENT, STATE_FUNDING, PLEDGE)

#: Every `deal_type` that means the figure on the row is not money the employer
#: raised. Kept as one frozen set because the aggregate, the correction script
#: and the tests must not be able to hold three different opinions about it.
#: `acquired` is here for the same reason `acquisition` is: a purchase price is
#: not a raise from either side of the table.
EXCLUDING_DEAL_TYPES = frozenset({
    "acquisition", "acquired", "merger", "divestiture", "joint_venture",
    "ipo", "bond_issue", "public_offering", "project_finance",
    FUND_RAISE, OUTBOUND_INVESTMENT, STATE_FUNDING, PLEDGE,
})

# Per-run counter, so an exclusion is countable rather than a silent drop.
# Same shape and the same reason as capital_event.STATS.
STATS: dict[str, int] = {k: 0 for k in MONEY_KINDS}


# --- 1. Fund-level raises ----------------------------------------------------
#
# The money is going INTO a vehicle so it can go out again. Two shapes carry
# that in the corpus, and both need a raise verb in front of them, because the
# bare words are everywhere.
#
# NOT a bare `\bfund\b`, and NOT `\bFund\s+[IVX]+\b` either. "Emergent raises
# $70M from Khosla Ventures and SoftBank Vision Fund 2" is a real seed round
# whose INVESTOR is a numbered fund, and a rule that reads the fund's name
# would refuse it. The fund has to be what the money is FOR.
#
# `strategy` and `vehicle` sit beside `fund` because the asset-management press
# uses all three interchangeably: Pantheon's is written "for its co-investment
# strategy" and means a fund.
#
# NOT `\bvehicle\b`, a measured retraction: it refused "Bulgarian Founder
# Vince Gaydarzhiev Raises $10M for AI Vehicle Inspection Startup", a real
# seed round whose PRODUCT is vehicles. And `strategy` only with the
# allocator's own adjective in front of it, because "raises $40M for its
# growth strategy" is an ordinary company saying what the money is for.
_FUND_VEHICLE = re.compile(
    r"\b(?:raise[sd]?|raising|clos(?:e|es|ed|ing)|secure[sd]?|land(?:s|ed)?)\b"
    + _GAP + r"\bfor\s+(?:its\s+|a\s+|the\s+|their\s+)?"
    r"(?:(?:\w+[\s,'’-]+){0,5}funds?\b"
    r"|(?:co[-\s]?investment|investment|credit|buyout|secondar\w+|"
    r"opportunit\w+|growth\s+equity)\s+strateg(?:y|ies)\b)",
    re.I,
)

# "raises $X to invest in / to back / to deploy into" — the purpose clause says
# the money is somebody else's capital to allocate. Accel's and Bruin Capital's
# are the worked examples.
#
# NOT `\bto\s+invest\b` bare: "raises $40M to invest in its own factory" is a
# use-of-proceeds sentence about capex, so the object has to be an investee.
_FUND_PURPOSE = re.compile(
    r"\b(?:raise[sd]?|raising|clos(?:e|es|ed|ing)|secure[sd]?)\b"
    + _GAP + r"\bto\s+(?:invest\s+in|back|deploy\s+(?:into|in))\s+"
    r"(?!its\b|our\b|their\b|the\s+company\b)",
    re.I,
)

# The idioms that only ever describe a fundraise by a fund manager.
#
# NOT `(?:final|first|interim)\s+clos(?:e|ing)`, and that is a retraction too:
# a venture round has a first close as readily as a fund does, and the pattern
# refused "SLEEK EV Secures First Closing of US$8.5 Million in Series A funding
# led by KYD". A fund that announces a first close almost always names the
# fund as well, which _FUND_VEHICLE above already reads.
_FUND_CLOSE = re.compile(
    r"\b(?:fund|funds)\s+clos(?:e|es|ed|ing)\b"
    r"|\blimited\s+partners?\b|\bLPs\b"
    r"|\bdry\s+powder\b",
    re.I,
)


# --- 2. Outbound investment --------------------------------------------------
#
# THE COMPANY-ANCHORED ONE, and it cannot be decided without knowing whose row
# this is. Both of these sentences contain "invests $X":
#
#   "Nvidia to invest $1.5b in SB Energy"                  company Nvidia   OUT
#   "Aavishkaar Capital invests $10 mn in Gnani.ai"        company Gnani.ai IN
#
# So the test is not "does a spending verb appear" but "is the row's OWN
# employer the one doing the spending". Two conditions, both required:
#
#   the employer's name appears in the run-up to the verb, and
#   the FIGURE hangs off the verb rather than off something else in the
#     sentence.
#
# The second condition is what keeps "Foo raises $100M and will invest in R&D"
# a funding round: "invest" is there, the employer is there, and no amount
# follows the verb, so nothing is excluded.
#
# NOT `\bspends?\b` or `\bspending\b`, removed after they fired on
# "Freehand Raises $75M Series B To Automate Fortune 500 Supply Chain Spend"
# and on the employer "Spend Life Wisely Company, Inc." The word is a noun as
# often as a verb and it appears in company names, which is exactly the trap
# `\bbond\b` is in capital_event.py.
_OUTBOUND_VERB = re.compile(
    r"\b(?:to\s+invest|invests?|investing|invested"
    r"|inject(?:s|ing|ed)?|pour(?:s|ing|ed)?"
    r"|plough(?:s|ing|ed)?|plow(?:s|ing|ed)?"
    r"|commit(?:s|ting|ted)?|allocat(?:es?|ing|ed))\b",
    re.I,
)

#: A currency amount in the shapes this corpus writes them. Same list as
#: capital_event._AMOUNT_NEAR, kept separate because the two modules answer
#: different questions and should be free to diverge.
_AMOUNT = re.compile(
    r"(?:US\$|USD|[$€£₹¥]|\bmilioni\b|\bmilhões\b|\bmillions?\b|\bmilliards?\b"
    r"|\bmn\b|\bbn\b|\bcrore\b|\blakh\b)",
    re.I,
)

#: How far the money may sit from the spending verb and still be its object.
#: A clause. "commits US$721M" is 3 characters; the slack is for "to invest
#: almost $150b" and "injects more than $700 million".
_OUTBOUND_WINDOW = 40

#: And how far BACK the employer's name may sit and still be the subject of the
#: verb. One clause again: "Semiconductor Major Marvell To Invest $250 Mn" is
#: 20 characters between the name and the verb.
_SUBJECT_WINDOW = 70

#: Words in the run-up that make the employer the OBJECT rather than the
#: subject, so its presence proves nothing. "Aavishkaar Capital invests $10 mn
#: in Gnani.ai" already fails the subject test on distance, but "Gnani.ai, in
#: which Aavishkaar invests $10 mn" would not.
_NOT_THE_SUBJECT = re.compile(r"\b(?:in|into|for|to|at)\s+which\b|,\s*where\b", re.I)


# --- 3. Government money -----------------------------------------------------
#
# NOT `\bgrants?\b` bare (a company grants share options), NOT `\bincentives?\b`
# bare (incentive plans are a rewards_comp staple), and NOT `\bpublic\b` bare
# for obvious reasons. Every pattern names the payer or the instrument.
_STATE_MONEY = re.compile(
    r"\b(?:state|federal|government|governmental|public|provincial|municipal|"
    r"EU|European\s+Union|congressional)\s+"
    r"(?:funding|funds|grants?|money|monies|financing|aid|appropriations?|"
    r"support\s+package|loan\s+guarantees?)\b"
    r"|\bin\s+(?:state|federal|provincial|EU)\s+(?:funding|grants?|money)\b"
    r"|\bsubsid(?:y|ies|ised|ized)\b"
    r"|\btax\s+(?:credits?|incentives?|breaks?|rebates?)\b"
    r"|\bgrant\s+from\s+the\s+"
    r"(?:U\.?S\.?\s+)?(?:Department|Ministry|Government|State|Commission|"
    r"Agency|Administration|Council)\b"
    r"|\b(?:CHIPS|IRA|Inflation\s+Reduction)\s+Act\s+(?:funding|award|grant)\b"
    r"|\bstimulus\s+(?:package|funding|grant)\b",
    re.I,
)


# --- 4. Pledges --------------------------------------------------------------
#
# Announced, not received. NOT `\bcommitments?\b` bare: "raises $200M with
# commitments from Sequoia" is a closed round described by who committed to it.
# The phrase has to name the money itself as the commitment.
_PLEDGE = re.compile(
    r"\binvestment\s+(?:commitments?|pledges?|intentions?)\b"
    r"|\b(?:pledges?|pledged)\s+(?:to\s+invest|investment)\b"
    r"|\bmemorand(?:um|a)\s+of\s+understanding\b|\bMoUs?\b"
    r"|\bletters?\s+of\s+intent\b",
    re.I,
)


def _employer_head(company: str | None) -> str:
    """The ONE word of the employer's name that identifies it.

    THE FIRST significant word, not any of them, and that is the fix for a real
    false exclusion. "Balerion Space Ventures Invests in Northwood Space $100M
    Series B Round" is a row about Northwood Space, and matching ANY token of
    "Northwood Space" against the run-up found `Space` inside the INVESTOR's
    name and called the round an outbound spend. A shared generic word is not
    evidence of who the subject is; the head of the name is the part that is.

    Legal suffixes and the corporate filler words go first, so "Acme Holdings
    Inc" heads on "Acme". Two characters is the floor, which keeps "AUO" and
    "GM".
    """
    if not company:
        return ""
    generic = {"the", "inc", "llc", "ltd", "corp", "plc", "group", "holdings",
               "company", "co", "and", "sa", "ag", "nv", "gmbh", "limited"}
    for word in re.findall(r"[A-Za-z0-9&.]{2,}", company):
        if word.lower() not in generic:
            return word
    return ""


def _is_outbound(company: str | None, text: str) -> bool:
    """Whether the row's OWN employer is the one spending the stated figure.

    ONE TEXT AT A TIME, never the concatenation, and the caller enforces that.
    Joining headline and summary with a newline put a 70-character look-back
    window across the seam, so the employer's name in the headline became the
    subject of a verb in the summary:

        "Qatar's Rasmal Ventures backs Yuno's $45 million Series B"
        "Rasmal Ventures has invested in Yuno's $45 million round..."

    Yuno's own round read as Yuno spending money, because `Yuno` was 40
    characters back from `invested` across the join. Three real rounds were
    excluded that way, plus "Lovable raises $400m ... - Investing.com", where
    the outlet's name in the headline supplied the verb and the summary
    supplied the amount.
    """
    head = _employer_head(company)
    if not head:
        return False
    pattern = re.compile(r"(?<![A-Za-z])" + re.escape(head) + r"(?![A-Za-z])", re.I)
    # A verb that is also a word of the employer's OWN NAME proves nothing, and
    # two Form D filers demonstrated it: "Harvest Invest-071 LLC ... raised
    # $1.9M in a private placement" and "Powerhouse Investing Group Holding
    # Company, LLC raised $1M in a private placement". Both are real inbound
    # raises whose names supplied both halves of the test - the head word and
    # the verb, adjacent, exactly as a subject and its verb would be. Same
    # trap as capital_event's "Bond Biosciences, Inc.".
    own_name = (company or "").lower()
    for m in _OUTBOUND_VERB.finditer(text):
        if m.group(0).lower() in own_name:
            continue
        after = text[m.end():m.end() + _OUTBOUND_WINDOW]
        if not _AMOUNT.search(after):
            continue
        before = text[max(0, m.start() - _SUBJECT_WINDOW):m.start()]
        if _NOT_THE_SUBJECT.search(before):
            continue
        if pattern.search(before):
            return True
    return False


def classify(company: str | None, *texts: str | None) -> str | None:
    """The reason this figure is not money the employer raised, or None.

    Give it every text the record carries — headline, summary, raw_text. None
    means "nothing in the words says otherwise", which is what a plain company
    round looks like.

    Ordered, and the order is about the LABEL rather than the outcome, since
    every branch excludes. A fund close that also says "to invest in startups"
    is a fund raise first and an outbound spend second, and `fund_raise` is the
    truer thing to write on the row.
    """
    parts = [t for t in texts if t and t.strip()]
    if not parts:
        return None
    joined = " \n ".join(parts)
    if _FUND_VEHICLE.search(joined) or _FUND_PURPOSE.search(joined) \
            or _FUND_CLOSE.search(joined):
        return FUND_RAISE
    # PER TEXT, never the join: see _is_outbound. The other three rules read a
    # phrase, which a seam cannot manufacture; this one reads a subject and a
    # verb, which a seam can.
    if any(_is_outbound(company, part) for part in parts):
        return OUTBOUND_INVESTMENT
    if _STATE_MONEY.search(joined):
        return STATE_FUNDING
    if _PLEDGE.search(joined):
        return PLEDGE
    return None


#: The deal types that are a PRICE rather than an instrument. A company can
#: raise a round in the same breath as it buys something, and when it does, the
#: figure usually belongs to the round.
_TRANSACTION_PRICES = frozenset({
    "acquisition", "acquired", "merger", "divestiture", "joint_venture",
})

#: The private market's own name for a round. Same list and same job as
#: capital_event._PRIVATE_ROUND_MARKER: a round the private market can name is
#: a round, whatever else the story mentions.
_PRIVATE_ROUND_MARKER = re.compile(
    r"\bseries\s+[a-k]\b|\bpre[- ]?seed\b"
    r"|\bseed\s+(?:round|funding|financing)\b"
    r"|\bventure\s+round\b|\bprivate\s+placement\b",
    re.I,
)


def basis(deal_type: str | None, company: str | None,
          *texts: str | None) -> str:
    """The three-state verdict for `money_basis`. NEVER None.

    Three sources, asked in order of what each knows: the stored `deal_type`,
    then this module's own patterns, then `capital_event` for the instrument.

    A `deal_type` already on the row normally wins, because it was decided by a
    model reading the whole document (or by capital_event reading the
    instrument), and both of those know more than the patterns above. When the
    row carries NO label and the patterns find nothing, the instrument is the
    last question rather than an assumed `company_raise` -- see the comment on
    that branch for why it is a no-op on the write path and matters only when
    re-judging a stored row.

    THE ONE EXCEPTION IS A TRANSACTION PRICE BESIDE A NAMED ROUND. "Singapore's
    Graas raises $17 million Series B, acquires product-data startup Trustana"
    is stored with deal_type `acquisition`, correctly — that IS the corporate
    event on the row — and the $17 million is the Series B, not the price of
    Trustana. Letting the label decide alone would drop a real round out of the
    total, which is the same class of error in the other direction. So when the
    text names a private round, a transaction label does not by itself exclude
    the figure, and the patterns above get their say instead.

    Deliberately NOT extended to the capital events or to the money kinds. A
    bond issue mentioned alongside a Series B is still a bond issue, and a fund
    close that says "Series A" somewhere is still a fund close: those verdicts
    were reached by reading the instrument, and a round name elsewhere in the
    story is not evidence against them.
    """
    if deal_type and deal_type in EXCLUDING_DEAL_TYPES:
        priced = deal_type in _TRANSACTION_PRICES
        joined = " \n ".join(t for t in texts if t)
        if not (priced and _PRIVATE_ROUND_MARKER.search(joined)):
            return deal_type
    kind = classify(company, *texts)
    if kind:
        return kind

    # AND LAST, THE INSTRUMENT, asked of the module that owns it.
    #
    # This branch is unreachable on the write path and exists entirely for
    # re-judging a STORED row. validate.build_signal calls capital_event first
    # and NULLS the figure when it answers, so `basis()` is only ever reached
    # there with text capital_event has already declined -- the same call with
    # the same inputs, so it declines again. Nothing about a new write changes.
    #
    # A row already in the database is the other case, and it is why this is
    # here. `docs/RULING-public-equity-proceeds.md` settled that equity sold
    # into public markets is excluded, and the two phrasings that missed it
    # ("issue US$10 billion in new shares", "record Hong Kong share sale") were
    # added to capital_event on 2026-08-29. That fix governed new writes only.
    # The rows stored BEFORE it kept `deal_type` NULL and `money_basis =
    # company_raise`, and correct_money_basis.py could not reach them: it
    # derives its verdict by calling this function, and this function asked the
    # stored label and its own patterns and never the instrument. So the
    # correction pass the ruling prescribes was a no-op on the exact rows the
    # ruling was about -- two classifiers disagreeing, with the corpus stuck on
    # the side of whichever one nobody asked.
    #
    # Only an EXCLUDING kind counts. capital_event may name an instrument that
    # is not one of ours, and a kind no total acts on must not become a verdict.
    event = capital_event.classify(*texts)
    if event and event in EXCLUDING_DEAL_TYPES:
        return event
    return COMPANY_RAISE


def note(kind: str) -> None:
    """Count one exclusion. Called by whatever acted on the verdict."""
    if kind in STATS:
        STATS[kind] += 1

"""What KIND of capital event a stated figure belongs to, decided by the text.

WHY THIS EXISTS. Four large-company capital events in one month were stored as
funding rounds and each one cost the owner a manual guardrail decision:

    ChangXin Memory   $8.6bn    a STAR Market IPO, retracted after publication
    Oracle            $25bn     a corporate bond issue
    Intel             $20bn     a public stock sale by a listed company
    Nvidia            $709bn    an infrastructure financing arrangement

Every one of them was caught by `guardrails.check_amounts`, which is a
MAGNITUDE check: it asks whether the corpus's own distribution can explain a
number. That reason does not generalise downward, and the corpus proves it.
Zions Bancorporation's "US$ 500 million in a senior notes issuance" is the same
class of event, is on the live site as a funding round today, and is four
orders of magnitude below the derived ceiling, so nothing has ever asked about
it. The magnitude check was never going to; a $500m bond looks exactly like a
$500m growth round to a threshold.

So this module asks the other question — not "is the number too big" but "is
this a company raising a round at all" — and it asks it at extraction, where
the answer is written down in the source's own words.

WHAT IT IS NOT. It is not a bigger `NOT_A_COMPANY_ROUND`. That regex
(pipeline/guardrails.py) withholds an AUTO-ACCEPT and never refuses anything,
which is why it can afford `\bfunds?\b` and `\bassets\b` bare and be wrong
about a sovereign-wealth-fund investor at no cost. This one changes what gets
STORED, so every pattern in it has to survive a much harder question, and the
two are deliberately separate objects with separate tolerances.

THE PRECISION ARGUMENT, because it decides every pattern below.

A rule that refuses a real venture round loses coverage silently and for ever:
the row is never stored, nothing counts it, and `measure_recall.py` reads the
loss as a market we do not reach. A rule that lets a bond through costs ONE
guardrail decision by a human who is already reading that queue. Those are not
symmetric, so the bias is heavily toward refusing only what can be defended
from the words on the page.

Three traps this codebase has already hit are wired into the shape of it:

  "raises" is meaningless.  It is in all four headlines above and in every
    real round. No pattern here reads a verb.

  Debt is a legitimate venture instrument.  "Kids2 Raises $225M in Debt
    Funding", "Karta Raises $140M in Debt and Equity Funding" and "Wonder
    Raises USD 12 Million Venture Debt from HSBC Innovation Banking" are all
    real private rounds in the corpus right now. So `\bdebt\b` is NOT
    disqualifying and neither is `\bconvertible note\b` — a convertible note
    is how a seed round is papered. What IS disqualifying is the instrument
    that only exists in public and lender markets: a bond, a sukuk, a
    debenture, SENIOR notes, a syndicated facility.

  Employer identity cannot decide it.  A company can raise venture money in
    the same week it issues a bond, so no ticker, CIK or employer_type is read
    here. Only the text of THIS story.

WHAT IS DELIBERATELY LET THROUGH, named so nobody has to rediscover it:

  * A story whose text simply does not say. Oracle's is the worked example:
    "Oracle raises $25 billion and reassures skeptical investors", summary
    "Oracle has raised $25 billion." Nothing in either sentence names an
    instrument. There is no honest deterministic verdict there and this module
    returns None rather than guess from the fact that Oracle is large and
    listed. It stays the magnitude check's problem.
  * Fund closes, AUM restatements and capex. Already handled, over-eagerly and
    harmlessly, by `guardrails.NOT_A_COMPANY_ROUND`. Widening a REFUSAL to
    `\bfunds?\b` would refuse "raises $70M from Khosla Ventures and SoftBank
    Vision Fund 2", which is a real round, so that family stays where its
    tolerance already fits it.
  * Any private placement. Two thousand six hundred Form D rows say "private
    placement" and every one of them is in scope.

No model is called. Same input, same answer, for ever, and it costs nothing.
"""

from __future__ import annotations

import re

# The verdicts. Every one is a `vocab.DEAL_TYPES` value, because the verdict's
# home is the `deal_type` column and not a fifth parallel refusal path: the
# row keeps saying what it actually was, which is the only form of "refused"
# that anybody can count later.
IPO = "ipo"
BOND_ISSUE = "bond_issue"
PUBLIC_OFFERING = "public_offering"
PROJECT_FINANCE = "project_finance"

#: The kinds this module can decide. `ipo` predates it.
CAPITAL_EVENT_TYPES = (IPO, BOND_ISSUE, PUBLIC_OFFERING, PROJECT_FINANCE)

# Per-run counter, so a refusal is countable rather than a silent drop. A
# source that posts zero while reporting healthy is a failure this project has
# already shipped once; the whole point of writing the verdict into deal_type
# is that the row survives to be counted, and this is the same fact at run
# time.
STATS: dict[str, int] = {k: 0 for k in CAPITAL_EVENT_TYPES}


# --- 1. Debt securities ------------------------------------------------------
#
# The instruments that only exist because an issuer is selling paper to
# investors at large. NOT `\bbond\b` bare: the corpus holds "Bond Aviation
# Holdings, LLC", "TB Bond, LLC" and "Bond Biosciences, Inc.", three real
# issuers whose NAME carries the word, and a bare match would refuse all of
# them. The word has to be doing the work of an instrument.
#
# `senior notes` and `notes due 20xx` and not `notes`: a convertible note is
# an ordinary seed instrument, and Compass's "0.25% Convertible SENIOR Notes
# due 2031" is a registered bond. The qualifier is the whole difference.
_DEBT_SECURITIES = re.compile(
    r"\bbond\s+(?:sale|issue|issuance|offering|placement|programme|program)\b"
    r"|\b(?:issuance|issue|sale|offering)\s+of\s+(?:\w+[\s,-]+){0,4}bonds?\b"
    r"|\bvia\s+(?:\w+[\s,-]+){0,3}bonds?\b"
    r"|\bbonds?\s+(?:worth|totall?ing)\b"
    r"|\b(?:eurobonds?|sukuk|debentures?|commercial\s+paper)\b"
    r"|\bsenior\s+(?:secured\s+|unsecured\s+)?notes?\b"
    r"|\bnotes?\s+(?:offerings?|issuance)\b"
    # "debt OFFERING" and never "debt funding". A private company's debt round
    # is written "venture debt", "debt funding", "debt and equity funding" —
    # three real corpus rows, all kept. An offering is sold to the market.
    r"|\bdebt\s+offerings?\b"
    r"|\bnotes?\s+due\s+20\d\d\b"
    r"|\bhigh[- ]yield\s+(?:notes?|bonds?|debt)\b",
    re.I,
)

# --- 2. Equity sold into public markets --------------------------------------
#
# An already-listed issuer selling shares. `private placement` never reaches
# here; every phrase below names a REGISTERED sale.
#
# NOT a bare `\bshare sale\b`: the corpus's "9fin completes first employee share
# sale after $170m raise" attaches the amount to the ROUND and the share sale to
# the employees, so the bare phrase would refuse a real Series row. `stock sale`
# stays — Intel's is the worked example and no corpus row uses it otherwise.
#
# QUALIFIED `share sale` IS IN, and the qualifier is the four words that made
# the bare form unsafe. Alibaba's HK$80bn issue reached the amount queue TWICE
# under two headlines and neither was classified:
#
#     "Alibaba raises US$10 billion in record Hong Kong share sale"
#     "Alibaba to issue US$10 billion in new shares for huge AI push"
#
# so both were stored `money_basis = company_raise` and both were summable. A
# third row for the same event — "Alibaba launches Rs95,000 crore share sale
# ...; third-largest follow-on offering" — WAS excluded, by `follow-on
# offering`. One announcement was landing on both sides of the ruling depending
# on which outlet's wording arrived.
#
# This is not a new policy. Equity sold into public markets is already excluded
# (rule 2 in the module docstring, and the owner rejected Intel's $20bn stock
# sale twice on exactly this ground). These two patterns apply the existing
# ruling to two phrasings it was missing. Measured over the whole corpus before
# shipping: 2 rows of 32,451 change classification, and they are these two.
# "employee/employees/staff/insider share sale" still does not match, which is
# the 9fin case and is pinned by a test.
_PUBLIC_EQUITY = re.compile(
    r"\b(?:registered\s+direct|follow[- ]?on|secondary|public|rights|equity|"
    r"stock|share|at[- ]the[- ]market|overnight)\s+offerings?\b"
    r"|\bdirect\s+offerings?\b"
    r"|\bofferta\s+(?:pubblica|diretta)\b|\boferta\s+(?:p[úu]blica|direta)\b"
    r"|\boffre\s+directe\b"
    r"|\bstock\s+sale\b"
    r"|\bissu\w*\s+(?:\S+\s+){0,6}new\s+shares\b"
    r"|\bnew\s+share\s+issue\b"
    r"|(?<!employee\s)(?<!employees\s)(?<!staff\s)(?<!insider\s)"
    r"\bshare\s+sale\b"
    r"|\brights\s+issue\b"
    r"|\bat[- ]the[- ]market\s+program(?:me)?\b"
    r"|\bshelf\s+(?:offering|registration)\b",
    re.I,
)

# --- 3. Loans, facilities and project financing ------------------------------
#
# A lender advancing money against an asset. NOT `\bloan\b` bare: plenty of
# real employers ARE lenders ("digital lending startup raises $X"), so the word
# has to carry a qualifier that makes it the instrument of THIS transaction.
#
# NOT a bare `credit facilit(y|ies)` either, and that is a measured retraction
# rather than caution: it refused "Danish Entravel Group raises €6.5 million to
# secure larger supplier credit facilities", a real venture round whose USE OF
# PROCEEDS is a credit line. A purpose clause is not an instrument. The
# qualified forms above lose nothing — no row in the corpus needs the bare one.
#
# `taps ... markets` / `taps Wall Street` is here because that idiom describes
# institutional issuance and nothing else — it is the only handle Nvidia's
# "Nvidia taps Wall Street for $709b 'AI factory' funding deal" ever offers.
_LENDER_FINANCE = re.compile(
    r"\b(?:syndicated|green|social|bridge|term|construction|project|"
    r"revolving|senior\s+secured|asset[- ]backed|warehouse)\s+"
    r"(?:loans?|facilit(?:y|ies)|financing)\b"
    r"|\brevolving\s+credit\b|\bdebt\s+facilit(?:y|ies)\b"
    r"|\b(?:project|infrastructure|acquisition)\s+financ(?:e|ing)\b"
    r"|\bsecuriti[sz]ation\b"
    r"|\btaps?\s+(?:into\s+)?(?:the\s+)?"
    r"(?:wall\s+street|debt|bond|credit|capital)\s*(?:markets?)?\b",
    re.I,
)

# --- 4. The listing itself ---------------------------------------------------
#
# THE HARD ONE, because "IPO" appears in real private rounds constantly and
# means the opposite thing there. Four in the corpus right now:
#
#   "IPO-bound AI startup Amity raises $100m in EDBI-led Series D round"
#   "Gathern raises $72 million at a valuation of over $266 million, eyes IPO"
#   "Oricell raises $40M more as it looks to go public"  (a pre-IPO financing)
#   "Odyssey CEO talks about $279M IPO"                  (this one IS the IPO)
#
# So the term alone decides nothing. Two conditions have to hold: the listing
# has to be near the money, and nothing may say the listing is still ahead.
_IPO_TERM = re.compile(
    r"\b(?:i\.?p\.?o\.?)\b|initial\s+public\s+offering"
    r"|\bgo(?:es|ing)?\s+public\b|\bdirect\s+listing\b|\bflotation\b"
    r"|\bstock\s+market\s+debut\b|\bnell'ipo\b",
    re.I,
)

# Words that put the listing in the FUTURE. Read in the 24 characters before
# the term, which is where a qualifier lives, plus "pre-IPO" as its own token.
_IPO_AHEAD = re.compile(
    r"(?:pre[-\s]?|ahead\s+of|before|toward?s?|eyes?|eyeing|bound|plan\w*|"
    r"prepar\w*|consider\w*|weigh\w*|file[sd]?\s+for|looks?\s+to|hopes?\s+to|"
    r"aims?\s+to|set\s+to|expect\w*|targets?|seeks?|to\s+)\s*$",
    re.I,
)

# A round the private market can name is a round, whatever else the story
# mentions about a future listing.
_PRIVATE_ROUND_MARKER = re.compile(
    r"\bseries\s+[a-k]\b|\bpre[- ]?seed\b|\bseed\s+(?:round|funding|financing)\b"
    r"|\bventure\s+round\b|\bprivate\s+placement\b|\bpre[-\s]?ipo\b",
    re.I,
)

# A currency amount, in the shapes the corpus writes them. Only used to ask
# whether the listing is what the figure is attached to.
_AMOUNT_NEAR = re.compile(
    r"(?:US\$|USD|[$€£₹¥]|\bmilioni\b|\bmilhões\b|\bmillions?\b|\bmilliards?\b)",
    re.I,
)

#: How far apart the money and the listing may sit and still be one claim.
#: 45 characters is a clause. Measured on the corpus: it holds every one of the
#: seven rows whose figure IS the IPO proceeds, and the three IPO-adjacent
#: private rounds are excluded by the two guards above rather than by distance.
_IPO_WINDOW = 45


def _ipo_is_the_raise(text: str) -> bool:
    """Whether the listing is what produced the figure, not a plan beside it."""
    if _PRIVATE_ROUND_MARKER.search(text):
        return False
    for m in _IPO_TERM.finditer(text):
        before = text[max(0, m.start() - 24):m.start()]
        if _IPO_AHEAD.search(before):
            continue
        window = text[max(0, m.start() - _IPO_WINDOW):m.end() + _IPO_WINDOW]
        if _AMOUNT_NEAR.search(window):
            return True
    return False


#: Ordered, because one story can name two instruments and the first match must
#: be the one that describes THIS figure. Debt securities before public equity
#: before lender finance: "priced its stock and debt offering at $2 billion" is
#: a registered offering either way, and a syndicated loan that a bond refinances
#: is still a bond issue for the money in the headline.
_RULES = (
    (BOND_ISSUE, _DEBT_SECURITIES),
    (PUBLIC_OFFERING, _PUBLIC_EQUITY),
    (PROJECT_FINANCE, _LENDER_FINANCE),
)


def classify(*texts: str | None) -> str | None:
    """The capital event this text describes, or None to leave it a round.

    Give it every text the record carries — headline, summary, raw_text. None
    is the ordinary answer and means "the source did not say", which is not
    the same as "it is a round" and is never treated as one anywhere else.
    """
    joined = " \n ".join(t for t in texts if t)
    if not joined.strip():
        return None
    for kind, pattern in _RULES:
        if pattern.search(joined):
            return kind
    if _ipo_is_the_raise(joined):
        return IPO
    return None


def explain(*texts: str | None) -> tuple[str, str] | None:
    """(kind, the phrase that decided it), for a log line or a test message."""
    joined = " \n ".join(t for t in texts if t)
    if not joined.strip():
        return None
    for kind, pattern in _RULES:
        hit = pattern.search(joined)
        if hit:
            return kind, hit.group(0).strip()
    if _ipo_is_the_raise(joined):
        hit = _IPO_TERM.search(joined)
        return IPO, (hit.group(0).strip() if hit else "IPO")
    return None


def note(kind: str) -> None:
    """Count one refusal. Called by whatever acted on the verdict."""
    if kind in STATS:
        STATS[kind] += 1

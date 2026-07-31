"""The read-through prompt, and nothing else.

One model call used to do two jobs. EXTRACTION is pattern-matching — the
employer, the amount, the stage, the place, the role are all IN the text, and
`deepseek/deepseek-chat` lifts them well for $0.00128 a call. The READ-THROUGH
is judgement: what this signal means for hiring in a named place is NOT in the
text, and the repo's own A/B (`ab_models.py --readthrough`) showed deepseek
RESTATING the headline where a Claude model produced something a recruiter
could act on.

Upgrading the fused call would have paid a frontier price on ~2,476 tokens of
`classify.SCHEMA_HINT` that the interpretation never needed. So the call is
split, and this file is the small half: **the headline, the teaser, and the
facts extraction already returned.** That is the whole input.

WHAT IS DELIBERATELY ABSENT, each for a reason that cost somebody a bug:

* `SCHEMA_HINT`. Interpretation does not need the storage vocabulary, and
  carrying it is the entire cost of the naive upgrade.
* The `headquarters_*` fields. Those come from the model's own knowledge of the
  company, are kept in separate columns, and are never merged into `city`. Hand
  them to a writer and the sentence places an unplaced record — the exact
  inference this product may not make.
* The publisher. `classify` prefixes "Published by: <outlet>" to the extraction
  text as a geography hint. A writer given the outlet writes the outlet's home
  town into the sentence; `national_press` and the city scanner both paid for
  that lesson already.

CACHING: the byte-stable prefix here (system + rules) is ~1,100 characters,
roughly 250 tokens. Sonnet 5's minimum cacheable prefix is 1,024 tokens and
Haiku 4.5's is 4,096, so **this prompt does not cache and no saving is claimed
for it.** A prefix under the floor does not error, it silently does not cache,
which is how a saving gets claimed that was never possible. The item-specific
text still goes last, so the shape is right if the prompt ever grows past the
floor.
"""

from __future__ import annotations

# The teaser is here for colour, not for facts: what the company does, what
# kind of round it was, who the buyer is. Extraction already lifted every field
# we store, so a longer window buys tokens rather than judgement. 500 chars is
# a news teaser in full (national_press caps its own at 400) and truncates only
# SEC filing bodies, where page two is exhibits.
TEASER_CHARS = 500

READ_SYSTEM = (
    "You write one line of talent-market intelligence for recruiters. Use only "
    "the facts you are given. Never state a number, place, employer or claim "
    "that is not in them. Reply with JSON only."
)

# Kept tight on purpose — this is the cost lever. Every rule here survived
# because a live record broke without it; the funding example is the one that
# put "Hiring up" on the page beside a sentence saying no hiring was disclosed.
READ_RULES = """Write the talent read-through: what this signal MEANS for hiring. One sentence, two at most.
- Use only the headline, the teaser and the facts below. Introduce no figure, place or employer that is not in them.
- No hedging: not potential, possibly, may, could, indicates, suggests, likely.
- When hiring is not stated, name the fact and name the gap. Money gets reported; hiring plans usually do not.
- Say what the company does and where, so the line is worth reading beside a hundred similar rows.
- Call it "the filing" only for an SEC filing, otherwise "the announcement" or "the report".
- Never write a storage code: "pharma and biotech", not "pharma_biotech". A sentence with an underscore in it is wrong.
WEAK: Enigma has $71M of new capital. The announcement does not disclose hiring plans.
STRONG: Enigma raised $71M in seed for physical-AI robotics, a stage where headcount goes into research and engineering. The announcement names no roles.
Reply with JSON only: {"talent_readthrough": "..."}"""


# The facts interpretation is allowed to see, with the label it is shown under.
# Ordered so the sentence's own shape falls out of the reading order: who, what
# kind of event, where, then the specifics.
#
# `headquarters_city` and `headquarters_country` are absent BY DESIGN — see the
# module docstring. A test pins their absence.
FACT_FIELDS = (
    ("company", "employer"),
    ("pillar", "kind of signal"),
    ("signal_direction", "what the source says about headcount"),
    ("city", "city stated"),
    ("state", "state stated"),
    ("country", "country stated"),
    ("industry", "industry"),
    ("funding_amount", "amount raised"),
    ("funding_stage", "round"),
    ("headcount", "roles stated"),
    ("headcount_scope", "what that count covers"),
    ("functions", "functions named"),
    ("work_mode", "work mode"),
    ("site_event", "site event"),
    ("deal_type", "deal"),
    ("effective_date", "takes effect"),
)


def _readable(value) -> str:
    """A storage code is not English.

    Values reach the writer with underscores turned to spaces, so the only way
    an underscore can appear in the returned sentence is the model inventing
    one. That is what makes the underscore check in `classify` a real check
    rather than a coin flip on whether the prompt leaked a code.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(_readable(v) for v in value if str(v).strip())
    text = str(value).strip()
    return text.replace("_", " ")


def facts_block(classified: dict) -> str:
    """The extracted fields, one per line, empties dropped.

    Dropping empties is not cosmetic: "round: (not stated)" invites the writer
    to talk about what is missing field by field, and the prompt already says
    how to handle a gap in one place.
    """
    lines = []
    for key, label in FACT_FIELDS:
        value = classified.get(key)
        if value in (None, "", 0, [], ()):
            continue
        rendered = _readable(value)
        if rendered:
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)


def build(classified: dict, raw: dict, *, teaser_chars: int = TEASER_CHARS) -> str:
    """The user half of the read-through call.

    Rules first (byte-stable), item last — the same shape as the extraction
    call, so the prompt is cache-ready even though it is currently under every
    candidate model's minimum cacheable prefix.
    """
    headline = (classified.get("headline") or raw.get("headline") or "").strip()
    teaser = (raw.get("raw_text") or "").strip()[:teaser_chars]
    facts = facts_block(classified)
    return (
        f"{READ_RULES}\n\n---\n"
        f"Headline: {headline}\n"
        f"Source text: {teaser}\n\n"
        f"Facts already extracted from that text:\n{facts}"
    )


def stable_prefix() -> str:
    """Everything identical on every call, for the caching arithmetic.

    Exposed so the claim about the cache floor is checkable rather than
    remembered: a test measures this and the comment in the docstring above
    cannot drift away from it silently.
    """
    return READ_SYSTEM + READ_RULES


# --- Is the second pass worth buying for THIS story? -------------------------
#
# THE QUESTION NOBODY HAD ASKED. Extraction and the read-through were priced at
# $31.69 and $31.29 a month at full worldwide coverage, which is 83% of the
# whole bill for reading every story twice. So: what does the second pass
# actually buy?
#
# EXACTLY ONE FIELD. `interpret()` returns `{"talent_readthrough": "..."}` and
# `interpret_late()` writes that single attribute. It is never asked for the
# employer, the country, the pillar, the amount or the direction, and `_accept`
# refuses any sentence carrying a figure or a place that is not already in the
# extracted facts. It also sees LESS text than extraction does — 500 characters
# of teaser against extraction's 4,000. **It cannot change a stored fact, and
# it cannot know anything extraction did not.** It is a prose upgrade on one
# field, and `pipeline/schema.py` already stores extraction's own version of
# that field for every record.
#
# WHAT IT IS WORTH, MEASURED. 4,171 rows carry the sentence the fused deepseek
# call wrote before the split, and 452 carry claude-sonnet-5's. Against five
# deterministic defect tests:
#
#     deepseek, fused        4,171 rows    9.6% defective
#       hedged  6.4%   short  2.5%   adds-no-fact  1.8%   restates  0.4%
#     claude-sonnet-5          452 rows    0.9% (and all four are this
#                                          module's own blind spot below)
#
# Mean headline overlap is 0.150 against 0.158 — statistically the same, so
# "deepseek restates the headline" is not a general property of the corpus,
# whatever one sample suggested. What it IS: thinner (127 characters against
# 194) and hedging one time in fifteen.
#
# So the frontier model is bought for every record to fix roughly one in ten.
# This decides which ten.
#
# WHAT THIS DOES NOT MEASURE, said plainly. These tests find DEFECTS, not dull
# prose. A sentence can pass all five and still be less useful than the one
# Sonnet would have written — Sonnet's extra 67 characters are usually context
# about what the company does, and no regex scores that. So the honest claim is
# "this catches the defects", not "this catches the quality gap".
#
# IT FAILS TOWARD QUALITY. Anything it cannot judge is sent to the model.

import re as _re

# Same vocabulary as `classify._HEDGE`, which is what the interpretation call
# is already scored against — a second, differently-worded hedge list would
# eventually disagree with the guard it exists to anticipate.
_WEAK_HEDGE = _re.compile(
    r"\b(suggests?|may|might|could|possibly|potentially|indicates?|likely)\b",
    _re.I)

# A storage code that leaked into English. The prompt bans it and the guard
# catches it after the fact; here it is a reason to buy a better sentence.
_WEAK_CODE = _re.compile(r"\b[a-z]+_[a-z]+\b")

#: Under this many characters a sentence is a label, not a read-through. The
#: fused corpus averages 127 and Sonnet 194; 80 is where "Brussels Airlines
#: appoints a new CEO; executive leadership changes." sits, which is the shape
#: this is for.
MIN_USEFUL_CHARS = 80

#: Jaccard overlap with the headline above which the sentence is the headline
#: again. 0.55 flags 0.4% of the fused corpus, which is the honest rate.
RESTATEMENT_OVERLAP = 0.55

#: Content words the sentence adds beyond the headline. Below this it is a
#: rewording rather than a read-through.
MIN_ADDED_WORDS = 6

#: Below this many word-tokens on either side, the two word-count tests are not
#: applied at all — see `_comparable`. Three, because a headline is short:
#: "Enigma Raises $71M in Seed Funding" is four scoreable words and is a
#: perfectly ordinary headline to compare against.
MIN_TOKENS_TO_COMPARE = 3

#: A "word" longer than this is not a word, it is an unsegmented script. Chinese
#: and Japanese put no spaces between words, so a word split returns one
#: enormous token; this is what tells the two apart without a language tag.
MAX_WORD_CHARS = 20


def _tokens(text: str) -> set[str]:
    return {w for w in _re.findall(r"[^\W\d_]{3,}", text or "", _re.UNICODE)}


def _comparable(a: set[str], b: set[str]) -> bool:
    """Whether the word-overlap tests mean anything for this pair.

    Chinese, Japanese and Thai do not put spaces between words, so a word split
    returns one enormous token or none at all, and both overlap tests then
    report whatever that accident produces. Measured: all four sentences this
    module flagged in the Sonnet corpus were Chinese, Arabic and Hebrew, and
    all four were fine. So below a floor the tests are SKIPPED, which sends the
    record to the model — the safe direction, and the one that spends the
    budget on exactly the languages the coverage gap is made of.
    """
    if len(a) < MIN_TOKENS_TO_COMPARE or len(b) < MIN_TOKENS_TO_COMPARE:
        return False
    return max(len(w) for w in a | b) <= MAX_WORD_CHARS


def weak_reasons(sentence: str, headline: str) -> tuple[str, ...]:
    """Why extraction's own read-through is not good enough, or ().

    Free: no model, no network, five regex-and-set operations. An empty tuple
    means the sentence stands on its own and the second pass is not worth
    buying for this record.
    """
    text = (sentence or "").strip()
    if not text:
        return ("empty",)

    why = []
    if len(text) < MIN_USEFUL_CHARS:
        why.append("short")
    if _WEAK_HEDGE.search(text):
        why.append("hedged")
    if _WEAK_CODE.search(text):
        why.append("storage-code")

    said, asked = _tokens(text), _tokens(headline)
    if _comparable(said, asked):
        union = said | asked
        if union and len(said & asked) / len(union) >= RESTATEMENT_OVERLAP:
            why.append("restates-headline")
        if len(said - asked) < MIN_ADDED_WORDS:
            why.append("adds-no-fact")
    else:
        # Cannot judge. Buy the good sentence.
        why.append("not-scoreable")
    return tuple(why)

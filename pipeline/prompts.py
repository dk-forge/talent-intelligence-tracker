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

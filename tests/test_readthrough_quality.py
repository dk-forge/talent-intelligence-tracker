"""The read-through is the product. Hedged copy is worthless to a recruiter.

Three of the first nine live records broke this rule ("suggests upcoming
hiring", "may lead to new hires") because the prompt banned hedging without
saying what to write instead when a source genuinely does not state hiring
plans. The fix is in the instruction, so the instruction is what gets tested.
"""

import re

from pipeline import classify

HEDGES = re.compile(
    r"\b(suggests?|may|might|could|possibly|potentially|indicates?|likely)\b", re.I
)


def test_the_prompt_bans_hedging():
    assert "No hedging words" in classify.SCHEMA_HINT


def test_the_prompt_says_what_to_write_instead_for_funding():
    """Banning a word without offering the alternative is why this failed."""
    # Normalise whitespace: the prompt wraps, and a test that breaks on
    # rewrapping teaches you to stop editing the prompt.
    hint = " ".join(classify.SCHEMA_HINT.split())
    assert "does not disclose hiring plans" in hint
    assert "name the fact and the gap" in hint


def test_the_funding_examples_model_the_behaviour():
    """A worked good/bad pair beats an abstract rule."""
    hint = classify.SCHEMA_HINT
    assert "BAD:" in hint and "GOOD:" in hint
    bad_block = hint[hint.find("BAD:  \"Holobiome"):]
    assert HEDGES.search(bad_block.split("GOOD:")[0]), "the BAD example should hedge"


def test_the_good_examples_do_not_hedge():
    """If our own examples hedge, the rule teaches the opposite."""
    hint = classify.SCHEMA_HINT
    for line in hint.splitlines():
        if line.strip().startswith("GOOD:"):
            assert not HEDGES.search(line), f"GOOD example hedges: {line.strip()}"

"""The second model pass is bought for the records that need it.

THE QUESTION THAT HAD NOT BEEN ASKED. Extraction and the read-through were
$31.69 and $31.29 a month at full worldwide coverage — 83% of the bill, for
reading every story twice. So what does the second pass buy?

**One field, and no facts.** These tests establish that structurally rather
than by sampling: the interpretation call is asked for exactly one key, writes
exactly one attribute, and sees LESS of the source text than extraction does.
It cannot change the employer, the country, the pillar, the amount or the
direction, because it is never given the chance to.

What is left is prose quality on a field extraction already filled in for free.
Measured over the real corpus — 4,171 rows carrying the fused deepseek sentence
against 452 carrying claude-sonnet-5's — the free triage flags 8.7% of
deepseek's Latin-script prose and 1.0% of Sonnet's. That nine-to-one gap is the
evidence that the triage measures the thing it claims to, and it is what makes
buying the frontier model for ~9% of records instead of 100% a cost decision
rather than a quality one.
"""

from __future__ import annotations

import inspect
import json

import pytest

import run_collect
from pipeline import classify, prompts, validate

RAW = {
    "headline": "Enigma Raises $71M in Seed Funding",
    "raw_text": ("Enigma Raises $71M in Seed Funding. The physical-AI robotics "
                 "company, based in Boston, will use the round to scale."),
    "source_name": "The Robot Report",
    "source_url": "https://www.therobotreport.com/enigma-seed/",
}

EXTRACTED = {
    "is_talent_signal": True, "company": "Enigma",
    "pillar": "company_development", "signal_direction": "neutral",
    "city": "Boston", "country": "United States", "confidence": "reported",
    "funding_amount": "$71M", "funding_stage": "seed", "headcount": 0,
    "headline": "Enigma Raises $71M in Seed Funding",
    "summary": "Enigma has raised $71M in seed funding.",
    "talent_readthrough": "PLACEHOLDER",
}

STRONG = ("Enigma raised $71M in seed for physical-AI robotics in Boston, a "
          "stage where headcount goes into research and engineering. The "
          "announcement names no roles.")
BETTER = ("Enigma raised $71M in Boston to build physical-AI robotics, and a "
          "seed round of that size normally goes into research headcount "
          "first. No roles are named.")


@pytest.fixture
def stats():
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


def signal_with(sentence: str):
    return validate.build_signal({**EXTRACTED, "talent_readthrough": sentence},
                                 RAW, "google_news")


def reply(monkeypatch, sentence: str, calls: list | None = None):
    def fake(model, system, user, **kwargs):
        if calls is not None:
            calls.append(model)
        return json.dumps({"talent_readthrough": sentence})
    monkeypatch.setattr(classify, "_call", fake)


# --- what the second pass CAN change, which is one field -------------------

def test_the_interpretation_is_asked_for_exactly_one_key():
    assert prompts.READ_RULES.rstrip().endswith(
        'Reply with JSON only: {"talent_readthrough": "..."}')
    for field in ("company", "country", "pillar", "funding_amount",
                  "signal_direction", "confidence"):
        assert f'"{field}"' not in prompts.READ_RULES, field


def test_it_writes_exactly_one_attribute():
    """So a field-by-field comparison of the six that decide a record would
    return 100% agreement by construction. There is nothing to A/B."""
    src = inspect.getsource(classify.interpret_late)
    assigned = [line.strip() for line in src.splitlines()
                if line.strip().startswith("signal.")]
    assert assigned == ["signal.talent_readthrough = interpret(classified, raw, timeout=timeout)"]


def test_it_sees_less_of_the_source_than_extraction_does():
    """The second pass is not 'the model finally reads the article'. It reads
    500 characters where extraction read 4,000, so it cannot know more."""
    assert prompts.TEASER_CHARS < classify.FULL_READ_CHARS
    assert prompts.TEASER_CHARS == 500 and classify.FULL_READ_CHARS == 4000


def test_extraction_already_produces_the_same_field_for_free():
    assert '"talent_readthrough"' in classify.SCHEMA_HINT


# --- the triage -------------------------------------------------------------

def test_a_sentence_that_stands_on_its_own_buys_nothing(monkeypatch, stats):
    calls = []
    reply(monkeypatch, BETTER, calls)
    signal = signal_with(STRONG)

    classify.interpret_late(signal, dict(EXTRACTED), dict(RAW))

    assert calls == [], "a frontier call was bought for a sentence that was fine"
    assert signal.talent_readthrough == STRONG
    assert stats["read_skipped_strong"] == 1


@pytest.mark.parametrize("weak,reason", [
    ("Enigma's $71M in Boston suggests upcoming hiring in robotics roles.", "hedged"),
    ("Enigma raised money.", "short"),
    ("Enigma raised $71M in Boston for data_ai and research roles this year.", "storage-code"),
])
def test_a_weak_sentence_buys_the_frontier_one(monkeypatch, stats, weak, reason):
    calls = []
    reply(monkeypatch, BETTER, calls)
    signal = signal_with(weak)

    assert reason in prompts.weak_reasons(weak, RAW["headline"])
    classify.interpret_late(signal, dict(EXTRACTED), dict(RAW))

    assert calls == [classify.READ_MODEL]
    assert signal.talent_readthrough == BETTER
    assert stats["read_bought_weak"] == 1


def test_an_ungrounded_free_sentence_is_replaced_even_when_it_reads_well(
        monkeypatch, stats):
    """The hole this would otherwise reopen. `ungrounded_reason` used to run
    only on the PAID sentence, because the free one was always overwritten.
    Keeping the free one without that check would let an invented figure
    through on a sentence with no other defect."""
    invented = ("Enigma raised $71M in Boston and will add 400 engineers "
                "across its research group over the coming year.")
    assert prompts.weak_reasons(invented, RAW["headline"]) == ()
    assert classify.ungrounded_reason(invented, EXTRACTED, RAW["raw_text"])

    calls = []
    reply(monkeypatch, BETTER, calls)
    signal = signal_with(invented)
    classify.interpret_late(signal, dict(EXTRACTED), dict(RAW))

    assert calls == [classify.READ_MODEL]
    assert signal.talent_readthrough == BETTER


def test_what_cannot_be_scored_is_sent_to_the_model(monkeypatch, stats):
    """Chinese, Japanese and Thai put no spaces between words, so the two
    overlap tests measure an accident. Those go to the model — the safe
    direction, and the one that spends the budget on exactly the languages the
    coverage gap is made of."""
    chinese = "蓝色涌现完成数千万人民币天使轮融资，资金将投入技术研发迭代与渠道搭建，支出通常流向研发团队。"
    assert prompts.weak_reasons(chinese, "本末科技前合伙人创业做电助力渔轮，获高瓴联合投资")

    calls = []
    reply(monkeypatch, BETTER, calls)
    signal = signal_with(chinese)
    classify.interpret_late(signal, dict(EXTRACTED), dict(RAW))
    assert calls == [classify.READ_MODEL]


def test_an_empty_sentence_always_buys_one():
    assert prompts.weak_reasons("", "Anything at all") == ("empty",)


def test_the_triage_costs_nothing():
    """No model, no network, no database. If this ever needed a fetch it would
    cost more than the call it is trying to avoid."""
    src = inspect.getsource(prompts.weak_reasons) + inspect.getsource(prompts._comparable)
    for forbidden in ("requests", "_call", "urlopen", "conn", "execute", "open("):
        assert forbidden not in src, forbidden


def test_the_unconditional_behaviour_is_one_variable(monkeypatch, stats):
    monkeypatch.setenv("TIT_READ_ALWAYS", "1")
    calls = []
    reply(monkeypatch, BETTER, calls)
    signal = signal_with(STRONG)
    classify.interpret_late(signal, dict(EXTRACTED), dict(RAW))
    assert calls == [classify.READ_MODEL]
    assert signal.talent_readthrough == BETTER


def test_the_run_log_prints_the_ratio_that_is_the_saving():
    """A triage that silently stopped flagging anything would look exactly like
    a cheaper month."""
    src = inspect.getsource(run_collect.run)
    assert "read_skipped_strong" in src and "read_bought_weak" in src
    assert "TIT_READ_ALWAYS=1" in src


# --- the measurement that decided it ----------------------------------------

def test_the_triage_separates_the_two_models_on_real_prose():
    """The evidence, reduced to the smallest sample that carries it.

    Three real fused-deepseek sentences that the live corpus flagged, and three
    real claude-sonnet-5 ones it did not. The full measurement is 4,171 against
    452 rows and lives in TECHLOG; this is what keeps the property from drifting
    the next time somebody edits a threshold.
    """
    deepseek_flagged = [
        ("LOOPTWORKS, INC's $3.6M funding in Portland, OR, suggests upcoming "
         "hiring to support growth.",
         "LOOPTWORKS, INC raised $3.6M in a private placement"),
        ("Holobiome's $10M funding in Boston, MA, suggests upcoming hiring in "
         "biotech roles.",
         "Holobiome, Inc. raised $10M in a private placement"),
        ("Brussels Airlines appoints a new CEO; executive leadership changes.",
         "Lorenza Maggio appointed CEO of Brussels Airlines"),
    ]
    sonnet_clean = [
        ("Andrew Taylor steps into the Vice President and Chief Accounting "
         "Officer role at Kontoor Brands, the Greensboro apparel group behind "
         "Wrangler and Lee.",
         "Kontoor Brands, Inc. announces leadership change"),
        ("Nir Naor joins Neuronetics as CFO, bringing over 20 years of finance "
         "and life sciences leadership to the Malvern device maker.",
         "Neuronetics Appoints Nir Naor Chief Financial Officer"),
        ("HotDoc, the Australian digital doctors' booking platform backed by "
         "Potentia Capital, loses the founder who ran it for fourteen years.",
         "HotDoc founder Ben Hurst steps away after 14 years"),
    ]
    for sentence, headline in deepseek_flagged:
        assert prompts.weak_reasons(sentence, headline), sentence[:50]
    for sentence, headline in sonnet_clean:
        assert prompts.weak_reasons(sentence, headline) == (), sentence[:50]

"""One call did two jobs. These pin the seam between them.

EXTRACTION lifts facts that are in the text and stays on
`deepseek/deepseek-chat` with SCHEMA_HINT untouched. The READ-THROUGH is an
interpretation that is NOT in the text, so it moved to its own model on its own
small prompt. Three properties make that split safe rather than merely cheaper,
and each has a section below:

  the small prompt carries ONLY what judgement needs (and refuses the rest);
  a failed interpretation defers the whole record and never stores a blank;
  the no-invented-figures rule binds on the new call as hard as on the old one.

Nothing here is stubbed into sys.modules — attributes are patched on the real
modules, so a fake cannot outlive its test (CLAUDE.md, "Test gotcha").
"""

from __future__ import annotations

import json

import pytest

import run_collect
from pipeline import classify, prompts, validate

# A real candidate shape: what a collector hands the pipeline.
RAW = {
    "headline": "Enigma Raises $71M in Seed Funding",
    "raw_text": ("Enigma Raises $71M in Seed Funding. The physical-AI robotics "
                 "company, based in Boston, will use the round to scale."),
    "source_name": "The Robot Report",
    "source_url": "https://www.therobotreport.com/enigma-seed/",
}

# What extraction returns for it. hq_* are present on purpose: the point of
# several tests below is that they are NOT forwarded to the writer.
EXTRACTED = {
    "is_talent_signal": True,
    "company": "Enigma",
    "pillar": "company_development",
    "signal_direction": "neutral",
    "city": "Boston",
    "country": "United States",
    "headquarters_city": "San Francisco",
    "headquarters_country": "United States",
    "confidence": "reported",
    "functions": ["research", "engineering"],
    "industry": "technology",
    "funding_amount": "$71M",
    "funding_stage": "seed",
    "headcount": 0,
    "headline": "Enigma Raises $71M in Seed Funding",
    "summary": "Enigma has raised $71M in seed funding.",
    "talent_readthrough": "Enigma has $71M of new capital.",
}

GOOD = ("Enigma raised $71M in seed for physical-AI robotics in Boston, a stage "
        "where headcount goes into research and engineering. The announcement "
        "names no roles.")


@pytest.fixture
def stats():
    """classify.STATS is module state, so it is saved and put back."""
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


def answer(sentence: str) -> str:
    return json.dumps({"talent_readthrough": sentence})


def reply(monkeypatch, content: str, seen: list | None = None):
    """Make the read-through call return `content` without touching the wire."""
    def fake(model, system, user, **kwargs):
        if seen is not None:
            seen.append({"model": model, "system": system, "user": user, **kwargs})
        return content
    monkeypatch.setattr(classify, "_call", fake)


# --- the split ---------------------------------------------------------------

def test_extraction_keeps_its_model_and_its_prompt():
    """The half that works is not what changed."""
    assert classify.MODEL == "deepseek/deepseek-chat"
    assert classify.GATE_MODEL == "google/gemini-2.5-flash-lite"
    assert "Return JSON with exactly these keys" in classify.SCHEMA_HINT


def test_the_read_through_has_its_own_model_and_env_var():
    assert classify.READ_MODEL == "anthropic/claude-sonnet-5"
    assert classify.read_enabled()


def test_the_read_through_can_be_switched_off(monkeypatch):
    """The one-line revert to the fused behaviour, and it must actually revert."""
    monkeypatch.setattr(classify, "READ_MODEL", "off")
    assert not classify.read_enabled()


def test_the_gate_switch_still_means_what_it_meant(monkeypatch):
    monkeypatch.setattr(classify, "GATE_MODEL", "off")
    assert not classify.gate_enabled()


def test_the_small_prompt_does_not_carry_the_extraction_schema():
    """This is the whole saving. SCHEMA_HINT is ~2,476 tokens of storage
    vocabulary, and paying a frontier rate for it is what the split avoids."""
    prompt = prompts.build(EXTRACTED, RAW)
    assert classify.SCHEMA_HINT not in prompt
    assert "is_talent_signal" not in prompt
    assert "headquarters_country" not in prompt


def test_the_small_prompt_carries_the_headline_the_teaser_and_the_facts():
    prompt = prompts.build(EXTRACTED, RAW)
    assert "Enigma Raises $71M in Seed Funding" in prompt      # headline
    assert "physical-AI robotics" in prompt                     # teaser
    assert "amount raised: $71M" in prompt                      # extracted fact
    assert "round: seed" in prompt


def test_the_writer_is_never_told_where_the_company_is_headquartered():
    """hq_* is the model's own knowledge, kept in separate columns and never
    merged into `city`. A writer given it places an unplaced record."""
    prompt = prompts.build(EXTRACTED, RAW)
    assert "San Francisco" not in prompt


def test_the_writer_is_never_told_who_the_publisher_is():
    """classify prefixes 'Published by:' to the EXTRACTION text as a geography
    hint. Handing it to a writer files every story in the outlet's home town."""
    prompt = prompts.build(EXTRACTED, RAW)
    assert "The Robot Report" not in prompt
    assert "Published by" not in prompt


def test_the_facts_reach_the_writer_as_english_not_as_storage_codes():
    """prompts._readable strips underscores from every value shown, which is
    what makes the underscore check in ungrounded_reason a real check."""
    prompt = prompts.build(EXTRACTED, RAW)
    assert "company_development" not in prompt
    assert "company development" in prompt
    assert "data_ai" not in prompts.build(
        {**EXTRACTED, "functions": ["data_ai"]}, RAW)


def test_the_prompt_stays_small():
    """A ceiling, not a target. If a future edit doubles this prompt, the
    per-read cost doubles with it and this test is where that gets noticed."""
    prompt = prompts.build(EXTRACTED, RAW)
    assert len(prompts.READ_SYSTEM) + len(prompt) < 2400


def test_the_teaser_is_capped():
    long_raw = {**RAW, "raw_text": "x" * 9000}
    assert len(prompts.build(EXTRACTED, long_raw)) < 2400


def test_nothing_is_claimed_for_prompt_caching():
    """Sonnet 5's minimum cacheable prefix is 1,024 tokens and Haiku 4.5's is
    4,096. This prefix is far under both, so it silently does not cache — and
    the module says so rather than claiming a saving that cannot happen."""
    prefix = prompts.stable_prefix()
    # Even at a generous 3 chars/token this cannot reach 1,024 tokens.
    assert len(prefix) < 1024 * 3
    assert "does not cache and no saving is claimed" in prompts.__doc__


# --- the seam ----------------------------------------------------------------

def test_classify_replaces_the_extracted_read_through(monkeypatch, stats):
    reply(monkeypatch, answer(GOOD))
    monkeypatch.setattr(classify, "gate_enabled", lambda: False)
    monkeypatch.setattr(classify, "READTHROUGH_CAP", 10)
    monkeypatch.setattr(classify, "_strip_fences", classify._strip_fences)

    calls = []

    def fake_call(model, system, user, **kwargs):
        calls.append(model)
        if model == classify.MODEL:
            return json.dumps(EXTRACTED)
        return answer(GOOD)

    monkeypatch.setattr(classify, "_call", fake_call)
    out = classify.classify(dict(RAW))

    assert calls == [classify.MODEL, classify.READ_MODEL]
    assert out["talent_readthrough"] == GOOD
    # Everything extraction decided is left exactly as it was.
    for key in ("company", "pillar", "signal_direction", "city", "country",
                "confidence", "funding_amount", "summary"):
        assert out[key] == EXTRACTED[key], key


def test_the_interpretation_call_cannot_promote_confidence(monkeypatch, stats):
    """It returns exactly one key. There is no tier for it to raise."""
    reply(monkeypatch, answer(GOOD) )
    sentence = classify.interpret(EXTRACTED, RAW)
    assert isinstance(sentence, str)
    assert "talent_readthrough" in prompts.READ_RULES
    assert "confidence" not in prompts.READ_RULES
    # And the source still sets the ceiling, whatever any model says.
    assert validate.infer_confidence(RAW["source_url"], "verified") == "reported"


def test_the_wire_request_is_the_small_one(monkeypatch, stats):
    """What actually leaves the process: the read model, the small prompt, a
    bounded max_tokens, and no response_format for an Anthropic endpoint."""
    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": answer(GOOD)},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 431, "completion_tokens": 58,
                              "cost": 0.00123}}

        text = ""
        headers: dict = {}

    class _Requests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            sent.update(json)
            return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "0" * 48)
    monkeypatch.setattr(classify, "requests", _Requests)

    assert classify.interpret(EXTRACTED, RAW) == GOOD
    assert sent["model"] == "anthropic/claude-sonnet-5"
    assert sent["max_tokens"] == classify.READ_MAX_TOKENS
    assert "response_format" not in sent   # Anthropic on OpenRouter 404s with it
    body = sent["messages"][1]["content"]
    assert classify.SCHEMA_HINT not in body
    # The provider's own accounting is still summed, so the split does not lose
    # the one cost figure in this repo that is not arithmetic.
    assert stats["prompt_tokens"] == 431 and stats["usd"] == pytest.approx(0.00123)


# --- the failure path --------------------------------------------------------

def test_a_failed_interpretation_defers_the_whole_record(monkeypatch, stats):
    def boom(*a, **k):
        raise classify.Throttled("provider busy")

    monkeypatch.setattr(classify, "_call", boom)
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "deferring the whole record" in str(caught.value)
    assert stats["read_unavailable"] == 1
    assert stats["read_written"] == 0


def test_an_unreadable_interpretation_defers_rather_than_storing_a_blank(
        monkeypatch, stats):
    reply(monkeypatch, "I'm afraid I can't help with that.")
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "blank differentiator" in str(caught.value)
    assert stats["read_unavailable"] == 1


def test_an_empty_interpretation_defers_too(monkeypatch, stats):
    reply(monkeypatch, answer("   "))
    with pytest.raises(classify.ReadThroughUnavailable):
        classify.interpret(EXTRACTED, RAW)
    assert stats["read_unavailable"] == 1


def test_the_deferral_lands_in_the_retry_next_run_path_not_the_budget_one():
    """run_collect already has three arms. This must be the one that prints
    DEFER, does not mark the URL seen, and DOES count toward the
    mostly-throttled breakage alarm — interpretation failing for a whole run is
    breakage, unlike a deliberate budget deferral."""
    assert issubclass(classify.ReadThroughUnavailable, classify.Throttled)
    assert not issubclass(classify.ReadThroughUnavailable, classify.BudgetDeferred)


def test_run_collect_still_defers_without_marking_seen():
    import inspect

    src = inspect.getsource(run_collect.run)
    throttled = src.index("except classify.Throttled")
    budget = src.index("except classify.BudgetDeferred")
    assert budget < throttled, "BudgetDeferred must be caught first or it is shadowed"
    arm = src[throttled:throttled + 700]
    assert "throttled += 1" in arm
    assert "mark_seen" not in arm, "a deferred candidate must stay unseen"


def test_the_run_log_names_the_deferrals_and_the_model():
    """A partial failure that only shows up as a low row count is invisible."""
    import inspect

    src = inspect.getsource(run_collect.run)
    assert "classify.READ_MODEL" in src
    assert "read_unavailable" in src and "read_ungrounded" in src
    assert "deferred whole" in src


def test_storing_a_blank_read_through_is_not_even_possible():
    """Why 'store with an empty read-through and retry later' was refused: the
    guard that would have to be weakened is the one keeping blank
    differentiators off the page."""
    with pytest.raises(validate.Rejected):
        validate.build_signal({**EXTRACTED, "talent_readthrough": ""}, RAW,
                              "google_news")


def test_a_run_that_wrote_nothing_still_reports_what_it_spent(stats):
    """Extraction was paid for. A cost that is only recorded on the happy path
    disappears exactly when someone is looking for it."""
    stats.update({"gate_calls": 12, "full_calls": 4, "read_calls": 4,
                  "read_unavailable": 4, "usd": 0.00512})
    snapshot = classify.usage_snapshot()
    assert snapshot["reads_bought"] == 4
    assert snapshot["rows_from_reads"] == 0
    assert snapshot["cost_usd"] == 0.00512


# --- the no-invented-figures rule, on the new call ---------------------------

def test_an_invented_figure_is_refused(monkeypatch, stats):
    reply(monkeypatch, answer(
        "Enigma raised $71M and will add 300 engineering roles in Boston."))
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "300" in str(caught.value)
    assert stats["read_ungrounded"] == 1
    assert stats["read_written"] == 0


def test_a_figure_written_in_words_is_the_same_figure(monkeypatch, stats):
    """'$71M' in the source and '71 million' in the sentence must not read as
    an invention: a false refusal defers a real story."""
    reply(monkeypatch, answer(
        "Enigma raised 71 million dollars in seed for robotics in Boston. The "
        "announcement names no roles."))
    assert classify.interpret(EXTRACTED, RAW)
    assert stats["read_ungrounded"] == 0
    assert stats["read_written"] == 1


def test_a_figure_from_the_extracted_fields_counts_as_sourced(stats):
    """The rule is 'in the source text OR in the extracted fields' — and the
    extracted fields already passed validate's own verbatim check."""
    extracted = {**EXTRACTED, "headcount": 300}
    assert classify.ungrounded_reason(
        "Adds 300 engineering roles in Boston.", extracted,
        "Enigma is hiring 300 engineers in Boston.") == ""


def test_a_year_is_not_an_invented_figure():
    assert classify.ungrounded_reason(
        "Enigma opens the Boston site in 2027.", EXTRACTED,
        RAW["raw_text"]) == ""


def test_an_invented_place_is_refused(monkeypatch, stats):
    """The writer knows things. It may not use them: a place must be stated."""
    reply(monkeypatch, answer(
        "Enigma raised $71M and is hiring in Dublin, based in Dublin."))
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "Dublin" in str(caught.value)
    assert stats["read_ungrounded"] == 1


def test_the_stated_place_is_allowed(monkeypatch, stats):
    reply(monkeypatch, answer(
        "Enigma raised $71M in seed for robotics based in Boston. The "
        "announcement names no roles."))
    assert classify.interpret(EXTRACTED, RAW)
    assert stats["read_written"] == 1


def test_a_place_the_work_is_in_counts_even_without_a_seat_frame(stats):
    """A read-through says where the WORK is, not where the company sits, so
    "hiring in Dublin" has to be caught as surely as "Dublin-based"."""
    assert "Dublin" in classify.ungrounded_reason(
        "Adds engineering roles in Dublin.", EXTRACTED, RAW["raw_text"])
    assert "Dublin" in classify.ungrounded_reason(
        "Adds roles to the Dublin market.", EXTRACTED, RAW["raw_text"])
    # And the stated one still passes through the same frame.
    assert classify.ungrounded_reason(
        "Adds engineering roles in Boston.", EXTRACTED, RAW["raw_text"]) == ""


def test_a_person_whose_name_is_also_a_city_is_not_a_place_claim():
    """'reports to Charlotte Jones' is a leadership sentence, not a North
    Carolina claim. A following capital is a surname."""
    assert classify.ungrounded_reason(
        "The new CFO reports to Charlotte Jones.", EXTRACTED,
        RAW["raw_text"]) == ""


def test_an_ordinary_word_that_is_also_a_city_is_not_a_place_claim():
    """The scanner reads frames, not names. 'Reading the announcement' must not
    defer a record, or precision costs coverage."""
    assert classify.ungrounded_reason(
        "Reading the announcement, Enigma names no roles.", EXTRACTED,
        RAW["raw_text"]) == ""


def test_a_storage_code_in_the_prose_is_refused(monkeypatch, stats):
    reply(monkeypatch, answer("Enigma is hiring in data_ai roles in Boston."))
    with pytest.raises(classify.ReadThroughUnavailable) as caught:
        classify.interpret(EXTRACTED, RAW)
    assert "storage code" in str(caught.value)


def test_hedging_is_counted_and_not_rejected(monkeypatch, stats):
    """A hedge is a quality flaw, not an invented claim. Deferring records over
    an adverb would be worse than the hedge; the count keeps the A/B honest."""
    reply(monkeypatch, answer(
        "Enigma's $71M may lead to hiring in Boston."))
    assert classify.interpret(EXTRACTED, RAW)
    assert stats["read_hedged"] == 1 and stats["read_written"] == 1


def test_the_record_level_rule_is_untouched():
    """validate still discards a record whose figures are not verbatim in
    raw_text. The split added a guard; it removed none."""
    with pytest.raises(validate.Rejected):
        validate.build_signal(
            {**EXTRACTED, "summary": "Enigma has raised $250M in seed funding."},
            RAW, "google_news")
    assert validate.build_signal(dict(EXTRACTED), RAW, "google_news")

"""The provider block that leaves the process, asserted offline.

There is no `OPENROUTER_API_KEY` in this environment, so nothing here makes a
live call and nothing here proves a cache hit. What it proves is the REQUEST
SHAPE, against the field names OpenRouter documents
(openrouter.ai/docs/features/provider-routing, read 2026-07-29): `order`,
`allow_fallbacks`, `only`, `ignore`, `require_parameters`, `sort`,
`data_collection`, `max_price`. A misspelled key inside `provider` is accepted
and silently ignored by the API, which is the failure this file exists to catch.

The availability tradeoff is a test, not a comment: `allow_fallbacks` must be
true on every request the pipeline can make. Pinning may cost the cache; it may
never cost the run.
"""

import pytest

from pipeline import classify, prompts

# The fields OpenRouter documents inside `provider`. Anything else we send is a
# typo that the API will accept and ignore.
DOCUMENTED = {
    "order", "allow_fallbacks", "require_parameters", "only", "ignore",
    "quantizations", "sort", "data_collection", "zdr", "max_price",
    "preferred_min_throughput", "preferred_max_latency",
    "enforce_distillable_text",
}

RAW = {
    "raw_text": "Enigma Raises $71M in Seed Funding for physical-AI robotics.",
    "headline": "Enigma Raises $71M in Seed Funding",
    "source_url": "https://www.finsmes.com/2026/07/enigma-raises-71m.html",
    "source_name": "FinSMEs",
}


@pytest.fixture
def stats():
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


def capture(monkeypatch, content: str) -> dict:
    """Run one _call and hand back the JSON body that went to the wire."""
    sent: dict = {}

    class _Resp:
        status_code = 200
        text = ""
        headers: dict = {}

        def json(self):
            return {"choices": [{"message": {"content": content},
                                 "finish_reason": "stop"}],
                    "provider": "StreamLake",
                    "usage": {"prompt_tokens": 3100, "completion_tokens": 400,
                              "prompt_tokens_details": {"cached_tokens": 2476},
                              "cost": 0.00128}}

    class _Requests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            sent.update(json)
            return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "0" * 48)
    monkeypatch.delenv("TIT_PROVIDER_ORDER", raising=False)
    monkeypatch.setattr(classify, "requests", _Requests)
    return sent


# --- the shape ---------------------------------------------------------------

def test_extraction_pins_the_provider_order(monkeypatch, stats):
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    provider = sent["provider"]
    assert provider["order"] == ["deepseek", "streamlake", "novita", "deepinfra"]
    assert provider["allow_fallbacks"] is True
    # The reason the block existed before this change is still true.
    assert provider["require_parameters"] is True


def test_every_key_we_send_is_one_openrouter_documents(monkeypatch, stats):
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    assert set(sent["provider"]) <= DOCUMENTED
    assert isinstance(sent["provider"]["order"], list)
    assert all(isinstance(slug, str) for slug in sent["provider"]["order"])


def test_the_order_is_never_sent_as_only_or_ignore(monkeypatch, stats):
    """`only` and `ignore` are the fields that turn one provider's outage into a
    failed collect job. A preference is the whole design."""
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    assert "only" not in sent["provider"]
    assert "ignore" not in sent["provider"]
    assert sent["provider"]["allow_fallbacks"] is True


def test_fallbacks_are_on_for_every_call_the_pipeline_makes(monkeypatch, stats):
    for model, json_mode in ((classify.MODEL, True),
                             (classify.GATE_MODEL, False),
                             (classify.READ_MODEL, False)):
        sent = capture(monkeypatch, "YES")
        classify._call(model, "sys", "user", timeout=5, json_mode=json_mode)
        assert sent.get("provider", {}).get("allow_fallbacks", True) is True


# --- who it applies to -------------------------------------------------------

def test_a_provider_slug_is_only_sent_to_the_author_that_has_one(monkeypatch, stats):
    """'streamlake' in front of an anthropic/ model is noise, so it is not sent.
    The read-through's Anthropic call keeps the body it already had."""
    sent = capture(monkeypatch, '{"talent_readthrough": "x"}')
    classify._call(classify.READ_MODEL, prompts.READ_SYSTEM, "user",
                   timeout=5, json_mode=False)

    assert classify.provider_order(classify.READ_MODEL) == ()
    assert "provider" not in sent
    assert "response_format" not in sent   # Anthropic endpoints 404 with it


def test_the_gate_model_is_not_pinned_either():
    assert classify.provider_order(classify.GATE_MODEL) == ()
    assert classify.provider_order(classify.MODEL)[0] == "deepseek"


# --- the escape hatch --------------------------------------------------------

def test_the_order_can_be_overridden(monkeypatch, stats):
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    monkeypatch.setenv("TIT_PROVIDER_ORDER", "novita, deepinfra")
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    assert sent["provider"]["order"] == ["novita", "deepinfra"]


def test_pinning_can_be_switched_off_in_one_line(monkeypatch, stats):
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    monkeypatch.setenv("TIT_PROVIDER_ORDER", "off")
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    assert "order" not in sent["provider"]
    assert sent["provider"]["require_parameters"] is True


# --- the prefix the pin exists to protect ------------------------------------

def test_the_cacheable_prefix_still_leads_the_user_message(monkeypatch, stats):
    """Pinning is worthless if something is inserted before SCHEMA_HINT: the
    shared prefix breaks and the cache silently forfeits."""
    sent = capture(monkeypatch, '{"is_talent_signal": false}')
    # Single-stage, so `sent` holds the extraction body and not the gate's.
    monkeypatch.setattr(classify, "GATE_MODEL", "off")
    classify.classify(RAW, timeout=5)

    assert sent["messages"][0]["content"] == classify.MINI_SYSTEM
    assert sent["messages"][1]["content"].startswith(classify.SCHEMA_HINT)


def test_the_run_records_which_endpoint_actually_served_it(monkeypatch, stats):
    """Without this the cache rate is uninterpretable — 60% across three
    providers is not the same measurement as 60% on one."""
    capture(monkeypatch, '{"is_talent_signal": false}')
    classify._call(classify.MODEL, classify.MINI_SYSTEM, "x", timeout=5)

    assert classify.STATS["providers"] == "StreamLake"
    assert classify.STATS["cached_tokens"] == 2476

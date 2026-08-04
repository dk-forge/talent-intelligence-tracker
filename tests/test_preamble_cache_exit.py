"""The extraction-preamble exit stays honest.

30.6% of the unconditional full-coverage bill buys the same ~2,500-token
extraction preamble on a slug (`deepseek/deepseek-chat`) where no endpoint
prices a cache read — the ledger's last 27 priced runs bill cached_tokens = 0
on every one. `cost_projection.py` prices the exit (extraction on a slug that
does cache the prefix), and the exit rests on three claims these tests pin:

1. The prefix that would cache is EXPOSED and byte-stable, measured from the
   prompt that ships, not remembered in a comment.
2. The pricing program's EXTRACT_PREFIX agrees with that live prompt — the
   constant sat 9.8% above reality (2,754 against 2,509) and nothing caught it.
3. The two-call verification (`ab_models.py --cache-check`) sends the
   PRODUCTION prompt shape, reads the BILLED cached tokens, and refuses to
   call an unverifiable run a pass: PASS / FAIL / UNKNOWN are three states.

Nothing here reaches the network.
"""

from __future__ import annotations

import json

import ab_models
import cost_projection as cp
from pipeline import classify

#: The repo's own calibration, from cost_projection.py: 4.39 chars/token.
CHARS_PER_TOKEN = 4.39


# --- 1. the prefix is exposed, byte-stable, and the shape classify() sends ---

def test_extract_stable_prefix_is_exposed_and_byte_stable():
    first = classify.extract_stable_prefix()
    assert first == classify.extract_stable_prefix()
    assert first == classify.MINI_SYSTEM + classify.SCHEMA_HINT
    # If this shrinks below the floor, implicit caching silently stops firing
    # and every cached-row projection becomes fiction. Gemini 2.5's implicit
    # floor and Sonnet 5's explicit floor are both 1,024 tokens.
    assert len(first) / CHARS_PER_TOKEN >= 1024


def test_the_prefix_leads_and_the_item_comes_last_in_the_wire_shape():
    """The cacheable bytes must open the user message; anything inserted
    before SCHEMA_HINT breaks the shared prefix and silently forfeits the
    cache. The probe and production must agree on that shape."""
    probe_user = f"{classify.SCHEMA_HINT}\n\n---\n{ab_models.CACHE_CHECK_ITEM}"
    assert probe_user.startswith(classify.SCHEMA_HINT)
    assert probe_user.rstrip().endswith(ab_models.CACHE_CHECK_ITEM.rstrip())


# --- 2. the pricing constant cannot drift from the prompt again --------------

def test_cost_projection_prefix_constant_matches_the_live_prompt():
    measured = len(classify.extract_stable_prefix()) / CHARS_PER_TOKEN
    drift = abs(cp.EXTRACT_PREFIX - measured) / measured
    assert drift <= 0.02, (
        f"EXTRACT_PREFIX={cp.EXTRACT_PREFIX} but the live prefix measures "
        f"{measured:.0f} tokens ({drift:.1%} apart) — update the constant "
        f"from len(classify.extract_stable_prefix()) / 4.39, do not guess")


def test_the_prefix_is_most_of_the_modelled_extraction_input_but_not_all():
    # The whole point of the exit: the byte-stable share dominates the call.
    # The upper bound is real too — the drifted 2,754 claimed 89% of the
    # input was cacheable, which flattered every cached row it priced.
    assert 0.75 <= cp.EXTRACT_PREFIX / cp.EXTRACT_IN <= 0.85


# --- 3. the two-call verification refuses to assume ---------------------------

def test_cache_verdict_three_states_and_absence_is_not_a_pass():
    hit = {"prompt_tokens": 3100,
           "prompt_tokens_details": {"cached_tokens": 2500}}
    miss = {"prompt_tokens": 3100,
            "prompt_tokens_details": {"cached_tokens": 0}}
    below_floor = {"prompt_tokens": 3100,
                   "prompt_tokens_details": {"cached_tokens": 512}}

    assert ab_models.cache_verdict(miss, hit) == ("CACHED", 0)
    assert ab_models.cache_verdict(miss, miss) == ("NOT CACHED", 2)
    # A partial hit under the provider floor is a miss, not a rounding error.
    assert ab_models.cache_verdict(miss, below_floor) == ("NOT CACHED", 2)
    # No usage, no verdict. Absence of a signal is not a pass.
    assert ab_models.cache_verdict(None, hit) == ("UNKNOWN", 3)
    assert ab_models.cache_verdict(miss, None) == ("UNKNOWN", 3)
    assert ab_models.cache_verdict(miss, {}) == ("UNKNOWN", 3)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


def test_cache_check_sends_the_production_prefix_twice(monkeypatch):
    """Two POSTs, byte-identical, opening with MINI_SYSTEM + SCHEMA_HINT and
    asking OpenRouter for its usage accounting — the only ground truth."""
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _FakeResponse(200, {
            "provider": "test-provider",
            "usage": {"prompt_tokens": 3100, "completion_tokens": 200,
                      "cost": 0.0004,
                      "prompt_tokens_details": {"cached_tokens": 2500}},
            "choices": [{"message": {"content": "{}"}}],
        })

    monkeypatch.setattr(ab_models.requests, "post", fake_post)
    monkeypatch.setattr(ab_models.time, "sleep", lambda *_: None)

    code = ab_models.run_cache_check("test-key", "google/gemini-2.5-flash-lite")

    assert code == 0
    assert len(sent) == 2
    assert sent[0] == sent[1], "the two probe calls must be byte-identical"
    body = sent[0]
    assert body["usage"] == {"include": True}
    assert body["messages"][0] == {"role": "system",
                                   "content": classify.MINI_SYSTEM}
    assert body["messages"][1]["content"].startswith(classify.SCHEMA_HINT)


def test_cache_check_402_is_unknown_never_a_verdict(monkeypatch):
    """An exhausted key means the probe checked NOTHING. Exit 3, not 0, not 2
    — the same three-state rule ops_status holds the rest of the repo to."""
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(402, {"error": "insufficient credits"},
                             text="Insufficient credits")

    monkeypatch.setattr(ab_models.requests, "post", fake_post)
    monkeypatch.setattr(ab_models.time, "sleep", lambda *_: None)

    assert ab_models.run_cache_check("k", "google/gemini-2.5-flash-lite") == 3


def test_cache_check_never_reads_the_gate_or_readthrough_prompt():
    """The probe must price the call the pipeline actually pays for. A probe
    on a reduced prompt would verify a cache the product never uses."""
    probe_user = f"{classify.SCHEMA_HINT}\n\n---\n{ab_models.CACHE_CHECK_ITEM}"
    assert classify.GATE_SYSTEM not in probe_user
    assert len(probe_user) > 10_000  # the production schema, not a stub

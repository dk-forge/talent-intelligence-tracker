#!/usr/bin/env python3
"""A/B candidate models against the current one, on real captured headlines.

Answers two questions with measurements rather than estimates:

1. **Agreement.** Does a cheaper model reach the same verdict as the incumbent?
   The sibling project A/B'd a cheaper model, measured 74% agreement, and
   rejected it. Same bar applies here.
2. **Actual cost.** Every per-item figure quoted so far has been arithmetic from
   published prices. OpenRouter returns real token counts per call, so this
   reports what a run genuinely costs.

    python ab_models.py                 # gate comparison across candidates
    python ab_models.py --gate-gold     # gate ACCURACY against hand labels
    python ab_models.py --readthrough   # quality comparison on survivors only
    python ab_models.py --extraction    # the REAL schema, field by field
    python ab_models.py --cache-check   # does the extraction prefix REALLY cache?

EVERY MODE EXCEPT --gate-gold SCORES AGREEMENT, NOT ACCURACY (2026-08-14)
------------------------------------------------------------------------
That is a real limit and it took until 2026-08-14 to write down. Agreement
with the incumbent cannot see the two models being wrong together, and it
scores a CORRECTION as a regression — which is not a hypothetical, it is what
the 2026-07-28 gate A/B found, and it is why "reject below 90% agreement"
would have picked the wrong model. `--gate-gold` is the mode with a human in
the denominator; see `analysis/models/gate_goldset.py`. Do not take a model
swap on the other modes alone.

WHY --extraction EXISTS (2026-07-30)
------------------------------------
`cost_projection.py` measures extraction at **$31.69 of a $75.99 monthly bill**
at full worldwide coverage — the largest single line, larger than the frontier
read-through. Two swaps would move it, and both are quality decisions nobody
can take on arithmetic alone:

    deepseek/deepseek-chat        $31.69/month   the incumbent
    deepseek/deepseek-chat-v3.1   $20.52/month   its prefix cache reads at 0.5x
    google/gemini-2.5-flash-lite   $4.90/month   already trusted as our GATE

The gate comparison above cannot decide this. It asks a one-word question on a
deliberately reduced prompt, and extraction is twenty structured fields off the
real ~2,500-token extraction prefix (`classify.extract_stable_prefix()`;
11,016 measured characters, 2,509 tokens at the repo's 4.39 chars/token
calibration). A model can be an excellent gate and a
poor extractor, so this mode sends the PRODUCTION prompt and scores field by
field against the incumbent — because a cheaper model that quietly loses the
country on a fifth of records would show up as a saving and read as a coverage
regression months later.

The bar is the one the repo already holds elsewhere: agreement on the fields
that decide a record, not an average across twenty of them. `company`,
`country` and `pillar` are what a row IS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
HEADLINES = Path(__file__).parent / "tests" / "fixtures" / "ab_headlines.txt"

# The incumbent is listed first and is the baseline everything is scored against.
GATE_MODELS = [
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-5-nano",
    "openai/gpt-oss-120b",
    "meta-llama/llama-3.3-70b-instruct",
]

READTHROUGH_MODELS = [
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-5-mini",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-5",
]

# Deliberately smaller than the production prompt: the gate only needs a verdict,
# not the full record. The production schema is ~250 tokens against a ~40-token
# headline, which is most of what we currently pay for.
GATE_SYSTEM = "Classify a talent-market headline. JSON only."
GATE_SCHEMA = """Reply with JSON only:
{"is_talent_signal": true|false,
 "company": "employer named, or empty",
 "pillar": "company_development|leadership_change|rewards_comp|how_we_work",
 "signal_direction": "hiring|displacement|neutral|comp_shift"}

is_talent_signal is false unless this is a hiring, leadership, compensation,
FUNDING or location-strategy development at a NAMED EMPLOYER. A raise counts:
it is company_development with signal_direction hiring. Government funding,
political announcements and economic-development programmes are false."""

# The omission above is the bug the production prompt shipped with: funding was
# not listed, so the model silently discarded every raise. Benchmarking against
# a schema that still has it would compare models on the wrong question.

READTHROUGH_SYSTEM = "You write talent-market intelligence for recruiters. JSON only."
READTHROUGH_SCHEMA = """Reply with JSON only:
{"talent_readthrough": "one sentence a recruiter can act on: WHO is affected, WHERE, and WHAT CHANGES for them. No hedging words (potential, possibly, may, could, indicates, suggests)."}"""


def load_headlines() -> list[str]:
    return [
        line.strip()
        for line in HEADLINES.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def call(model: str, system: str, schema: str, headline: str, key: str) -> tuple[dict | None, dict, str]:
    """Returns (parsed_json_or_None, usage, error)."""
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{schema}\n\n---\n{headline}"},
        ],
    }
    # Anthropic endpoints on OpenRouter do not advertise response_format, so
    # require_parameters filters every provider out and the request 404s with
    # "No endpoints found". Claude follows a JSON-only instruction reliably, and
    # the brace extraction below handles the response either way.
    if not model.startswith("anthropic/"):
        body["response_format"] = {"type": "json_object"}
        body["provider"] = {"require_parameters": True}
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": USER_AGENT},
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        return None, {}, f"network: {exc}"

    if resp.status_code >= 400:
        return None, {}, f"HTTP {resp.status_code}: {resp.text[:120]}"

    payload = resp.json()
    usage = payload.get("usage") or {}
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None, usage, f"no JSON in response: {content[:100]!r}"
    try:
        return json.loads(content[start:end + 1]), usage, ""
    except ValueError as exc:
        return None, usage, f"unparseable: {exc}"


def run_gate(key: str, headlines: list[str]) -> int:
    results: dict[str, list] = {}
    costs: dict[str, dict] = {}

    for model in GATE_MODELS:
        print(f"\n=== {model} ===", flush=True)
        verdicts, tin, tout, errors = [], 0, 0, 0
        for headline in headlines:
            parsed, usage, err = call(model, GATE_SYSTEM, GATE_SCHEMA, headline, key)
            tin += usage.get("prompt_tokens", 0)
            tout += usage.get("completion_tokens", 0)
            if err:
                errors += 1
                verdicts.append(None)
                print(f"  ERR  {headline[:56]}  ({err[:60]})")
                continue
            verdicts.append(parsed)
            flag = "SIGNAL " if parsed.get("is_talent_signal") else "reject "
            print(f"  {flag} {headline[:56]}"
                  + (f"  [{parsed.get('company','')[:18]}|{parsed.get('pillar','')[:18]}]"
                     if parsed.get("is_talent_signal") else ""))
            time.sleep(0.4)
        results[model] = verdicts
        costs[model] = {"in": tin, "out": tout, "errors": errors}

    baseline = results[GATE_MODELS[0]]
    print("\n" + "=" * 74)
    print("AGREEMENT WITH INCUMBENT (deepseek/deepseek-chat)")
    print("=" * 74)
    print(f"{'model':<38} {'signal':>8} {'pillar':>8} {'errors':>7}")
    print("-" * 74)
    for model in GATE_MODELS:
        v = results[model]
        comparable = [(a, b) for a, b in zip(baseline, v) if a and b]
        if not comparable:
            print(f"{model:<38} {'n/a':>8} {'n/a':>8} {costs[model]['errors']:>7}")
            continue
        sig = sum(a.get("is_talent_signal") == b.get("is_talent_signal") for a, b in comparable)
        both = [(a, b) for a, b in comparable if a.get("is_talent_signal") and b.get("is_talent_signal")]
        pil = sum(a.get("pillar") == b.get("pillar") for a, b in both)
        print(f"{model:<38} {100*sig/len(comparable):>7.0f}% "
              f"{(f'{100*pil/len(both):.0f}%' if both else 'n/a'):>8} {costs[model]['errors']:>7}")

    print("\n" + "=" * 74)
    print(f"MEASURED COST  (per item, and per month at 660 items/day)")
    print("=" * 74)
    print(f"{'model':<38} {'tok in':>7} {'tok out':>8} {'$/item':>10} {'$/month':>9}")
    print("-" * 74)
    prices = _prices()
    for model in GATE_MODELS:
        c, n = costs[model], len(headlines)
        pi, po = prices.get(model, (0.0, 0.0))
        per_item = (c["in"] / n) * pi + (c["out"] / n) * po
        print(f"{model:<38} {c['in']//n:>7} {c['out']//n:>8} "
              f"{per_item:>10.6f} {per_item*660*30:>9.2f}")

    print("\nDISAGREEMENTS vs incumbent (where the choice actually matters):")
    for i, headline in enumerate(headlines):
        base = baseline[i]
        if not base:
            continue
        differing = [m for m in GATE_MODELS[1:]
                     if results[m][i] and results[m][i].get("is_talent_signal") != base.get("is_talent_signal")]
        if differing:
            print(f"  {'SIGNAL' if base.get('is_talent_signal') else 'reject'} (incumbent): {headline[:60]}")
            for m in differing:
                print(f"      {m} said {'SIGNAL' if results[m][i].get('is_talent_signal') else 'reject'}")
    return 0


def run_readthrough(key: str, headlines: list[str]) -> int:
    """Quality comparison on items that are genuinely talent signals."""
    sample = headlines[2:6]
    for headline in sample:
        print(f"\n--- {headline[:72]}")
        for model in READTHROUGH_MODELS:
            parsed, usage, err = call(model, READTHROUGH_SYSTEM, READTHROUGH_SCHEMA, headline, key)
            if err:
                print(f"  {model:<30} ERROR {err[:60]}")
                continue
            print(f"  {model:<30} {parsed.get('talent_readthrough','')[:150]}")
            time.sleep(0.4)
    return 0


def _prices() -> dict[str, tuple[float, float]]:
    """Live per-token prices, so the cost column is not another estimate."""
    try:
        data = requests.get("https://openrouter.ai/api/v1/models",
                            headers={"User-Agent": USER_AGENT}, timeout=30).json()["data"]
    except (requests.RequestException, KeyError, ValueError):
        return {}
    out = {}
    for m in data:
        p = m.get("pricing") or {}
        try:
            out[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
        except (TypeError, ValueError):
            continue
    return out


# Extraction candidates, incumbent first. Everything here is scored against
# `pipeline.classify.MODEL` on `pipeline.classify.SCHEMA_HINT` — the production
# prompt, byte for byte, because a reduced one measures a different model.
EXTRACTION_MODELS = [
    "deepseek/deepseek-chat",
    "deepseek/deepseek-chat-v3.1",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "openai/gpt-5-mini",
]

# The fields that decide what a record IS. An average over twenty fields hides
# a model that gets `funding_stage` right and `company` wrong, and `company` is
# the difference between a row and a rejection.
DECIDING_FIELDS = ("is_talent_signal", "company", "pillar", "country",
                   "signal_direction", "funding_amount")


def run_extraction(key: str, headlines: list[str]) -> int:
    """Field-by-field agreement on the PRODUCTION extraction prompt."""
    from pipeline import classify

    incumbent = EXTRACTION_MODELS[0]
    prices = _prices()
    answers: dict[str, list] = {}
    spend: dict[str, float] = {}

    for model in EXTRACTION_MODELS:
        print(f"\n=== {model} ===", flush=True)
        rows, cost = [], 0.0
        for headline in headlines:
            parsed, usage, err = call(model, classify.MINI_SYSTEM,
                                      classify.SCHEMA_HINT, headline, key)
            if model in prices and usage:
                pin, pout = prices[model]
                cost += (usage.get("prompt_tokens", 0) * pin
                         + usage.get("completion_tokens", 0) * pout)
            rows.append(parsed if not err else None)
            if err:
                print(f"  ERROR {err[:80]}")
            time.sleep(0.3)
        answers[model] = rows
        spend[model] = cost
        print(f"  {sum(r is not None for r in rows)}/{len(rows)} parsed, "
              f"${cost:.5f} for the set")

    print("\n" + "=" * 72)
    print("AGREEMENT WITH THE INCUMBENT, ON THE FIELDS THAT DECIDE A RECORD")
    print("=" * 72)
    print(f"{'model':32} " + " ".join(f"{f[:9]:>10}" for f in DECIDING_FIELDS)
          + f" {'$/item':>9}")
    for model in EXTRACTION_MODELS:
        cells = []
        for field in DECIDING_FIELDS:
            same = total = 0
            for mine, theirs in zip(answers[model], answers[incumbent]):
                if mine is None or theirs is None:
                    continue
                total += 1
                same += _same_value(mine.get(field), theirs.get(field))
            cells.append(f"{100 * same // total if total else 0:>9}%")
        per_item = spend[model] / max(len(headlines), 1)
        print(f"{model:32} " + " ".join(cells) + f" {per_item:9.6f}")

    print("\nREAD THIS AS A FLOOR, NOT A SCORE. Disagreement with the incumbent")
    print("is not error: the gate A/B of 2026-07-28 found the challenger")
    print("CORRECTING the incumbent, which is why 'reject below 90% agreement'")
    print("would have picked the wrong model there. Below, every disagreement")
    print("on `company` or `country`, to be read rather than counted.")
    for model in EXTRACTION_MODELS[1:]:
        shown = 0
        for i, headline in enumerate(headlines):
            mine, theirs = answers[model][i], answers[incumbent][i]
            if mine is None or theirs is None or shown >= 8:
                continue
            for field in ("company", "country"):
                if not _same_value(mine.get(field), theirs.get(field)):
                    print(f"\n  {headline[:70]}")
                    print(f"    {incumbent:30} {field}={theirs.get(field)!r}")
                    print(f"    {model:30} {field}={mine.get(field)!r}")
                    shown += 1
                    break
    return 0


# --- ACCURACY, not agreement -------------------------------------------------
#
# Every mode above scores a challenger against the INCUMBENT. This one scores
# both against a human, using `analysis/models/gate_goldset.py`. Read that
# module's docstring before reading a number out of here: agreement cannot see
# two models being wrong together, and it reads a correction as a regression,
# which is exactly what happened in the 2026-07-28 gate A/B.
#
# THE SET IS THE PRODUCTION QUESTION AND THE PRODUCTION INPUT. `classify.
# GATE_SYSTEM` byte for byte, on headline+teaser, so a score here is a score on
# the surface that costs $3.68/month rather than on a reduced stand-in.

#: Gate candidates, incumbent first. Same slugs as GATE_MODELS; kept separate
#: so a change to the exploratory list cannot silently change what the accuracy
#: measurement was taken on.
GATE_GOLD_MODELS = [
    "google/gemini-2.5-flash-lite",   # the incumbent gate
    "deepseek/deepseek-chat",
    "openai/gpt-5-nano",
    "openai/gpt-oss-120b",
    "meta-llama/llama-3.3-70b-instruct",
]


def _gate_answer(text: str) -> bool | None:
    """YES/NO out of a one-word reply. None when the model said neither.

    None is NOT False. A model that answers something else has failed to
    answer, and `score()` counts an unanswered item as a miss; folding it into
    NO here would hide a broken model as a conservative one.
    """
    head = (text or "").strip().upper()
    if head.startswith("YES"):
        return True
    if head.startswith("NO"):
        return False
    return None


def _gate_call(model: str, item_text: str, key: str) -> tuple[str, dict, str]:
    """One production-shaped gate call: GATE_SYSTEM, one word back."""
    from pipeline import classify

    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 4,
        "messages": [
            {"role": "system", "content": classify.GATE_SYSTEM},
            {"role": "user", "content": item_text[:classify.GATE_CHARS]},
        ],
    }
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": USER_AGENT},
            json=body, timeout=90)
    except requests.RequestException as exc:
        return "", {}, f"network: {exc}"
    if resp.status_code >= 400:
        return "", {}, f"HTTP {resp.status_code}: {resp.text[:120]}"
    payload = resp.json()
    content = ((payload.get("choices") or [{}])[0]
               .get("message") or {}).get("content") or ""
    return content, payload.get("usage") or {}, ""


def run_gate_gold(key: str) -> int:
    """Accuracy against the hand labels, beside cost per item, per model."""
    from analysis.models import gate_goldset

    doc = gate_goldset.load()
    items = gate_goldset.scoreable(doc)
    prices = _prices()

    base = gate_goldset.production_baseline(doc)
    print("=" * 78)
    print("GATE ACCURACY AGAINST HAND LABELS")
    print("=" * 78)
    print(f"{len(items)} scoreable items of {len(doc['items'])}; "
          f"{len(doc['items']) - len(items)} ambiguous and excluded.")
    print(f"\nFREE BASELINE — the live gate's OWN recorded verdicts on the "
          f"{base['total']} ledger items:")
    print(f"  {base['correct']}/{base['total']} = {base['accuracy']:.1%} "
          f"(Wilson 95% {base['accuracy_lo']:.1%}-{base['accuracy_hi']:.1%}), "
          f"recall {base['recall']:.1%}, precision {base['precision']:.1%}")

    results = {}
    for model in GATE_GOLD_MODELS:
        print(f"\n=== {model} ===", flush=True)
        answers, cost, unparsed = {}, 0.0, 0
        for item in items:
            content, usage, err = _gate_call(model, item["text"], key)
            if err:
                print(f"  ERROR {err[:90]}")
                continue
            if model in prices and usage:
                pin, pout = prices[model]
                cost += (usage.get("prompt_tokens", 0) * pin
                         + usage.get("completion_tokens", 0) * pout)
            verdict = _gate_answer(content)
            if verdict is None:
                unparsed += 1
                continue
            answers[item["id"]] = verdict
            time.sleep(0.2)
        s = gate_goldset.score(doc, answers)
        s["cost"] = cost
        s["unparsed"] = unparsed
        results[model] = s
        print(f"  {s['correct']}/{s['total']} = {s['accuracy']:.1%}, "
              f"${cost:.5f} for the set")

    print("\n" + "=" * 78)
    print(f"{'model':34} {'acc':>7} {'95% interval':>15} {'recall':>7} "
          f"{'prec':>7} {'$/item':>10}")
    for model, s in results.items():
        per_item = s["cost"] / max(s["total"], 1)
        print(f"{model:34} {s['accuracy']:6.1%} "
              f"{s['accuracy_lo']:6.1%}-{s['accuracy_hi']:<6.1%} "
              f"{s['recall']:6.1%} {s['precision']:6.1%} {per_item:10.6f}")

    print("\nWHAT A CHEAPER MODEL HAS TO CLEAR. The gate's whole job is to stop "
          "\npaying for items that will not store, so its two errors cost "
          "different things:")
    print("  a FALSE POSITIVE buys an extraction call and then gets rejected "
          "downstream")
    print("  a FALSE NEGATIVE loses the event, and coverage is the product")
    print("So read RECALL first and only then the price. Every disagreement is "
          "\nprinted below to be read rather than counted:")
    for model, s in results.items():
        for item, why in s["wrong"]:
            print(f"\n  {model}: {why}")
            print(f"    {item['text'].splitlines()[0][:72]}")
            print(f"    gold: {item['why'][:150]}")
        if s["unanswered"]:
            print(f"\n  {model}: {len(s['unanswered'])} item(s) unanswered, "
                  f"counted as misses")

    print("\nAND THE CEILING ON ALL OF IT:")
    for limit in gate_goldset.KNOWN_LIMITS:
        print(f"  * {limit}")
    return 0


def _same_value(a, b) -> bool:
    """Compared the way the pipeline compares them: case and surrounding space
    are normalised away by `vocab` before anything is stored, so counting them
    as disagreements would manufacture a difference that never reaches a row."""
    if isinstance(a, list) or isinstance(b, list):
        return sorted(map(str, a or [])) == sorted(map(str, b or []))
    return str(a or "").strip().lower() == str(b or "").strip().lower()


# --- Does the extraction prefix ACTUALLY cache on this slug? ------------------
#
# The two-call procedure, executable rather than remembered. A cache_control
# flag or a provider-order tweak that "should" cache is worth exactly nothing
# until the provider's own usage accounting says tokens were served from cache
# — the ledger holds 27 priced runs on `deepseek/deepseek-chat` with
# cached_tokens = 0 on every one, which is what "no endpoint prices a cache
# read" looks like from the billing side.
#
# So: send the PRODUCTION extraction prompt (the byte-stable prefix, then one
# fixed item) twice, a few seconds apart, and read back what OpenRouter says it
# billed. Three verdicts, and absence of a signal is never a pass:
#
#   CACHED      exit 0   call 2 reports cached_tokens >= the 1,024-token floor
#   NOT CACHED  exit 2   usage came back and says no tokens were cached
#   UNKNOWN     exit 3   the probe could not check (402, network, no usage) —
#                        top the key up and re-run; do not treat this as a pass

#: Gemini 2.5 models cache implicitly from a 1,024-token prefix; Sonnet 5's
#: explicit floor is the same figure. The extraction prefix is ~2,509 modelled
#: tokens, so a verdict below this floor is a miss, not a rounding error.
CACHE_FLOOR_TOKENS = 1024

#: Seconds between the two probe calls. Implicit caches populate on the first
#: request; the gap only needs to outlast request pipelining.
PROBE_GAP_SECONDS = 5

#: Byte-stable on purpose: a probe item that changed between sessions would
#: make two sessions' billed numbers incomparable. Synthetic, so no real
#: company's row is ever created from a probe.
CACHE_CHECK_ITEM = (
    "Published by: Example Wire\n\n"
    "Example Robotics raises $25M Series B led by Example Capital. "
    "The Munich-based warehouse-automation firm said the round will fund its "
    "expansion into France and the Netherlands. The company employs 140 "
    "people and plans to open a Paris engineering office in 2027."
)


def _cached_tokens(usage: dict | None) -> int:
    details = (usage or {}).get("prompt_tokens_details") or {}
    try:
        return int(details.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def cache_verdict(first: dict | None, second: dict | None) -> tuple[str, int]:
    """(verdict, exit code) from the two calls' billed usage.

    PASS / FAIL / UNKNOWN are three distinct states. A probe that could not
    read billed tokens has checked nothing, and nothing is not a pass.
    """
    if not first or not second or not second.get("prompt_tokens"):
        return "UNKNOWN", 3
    if _cached_tokens(second) >= CACHE_FLOOR_TOKENS:
        return "CACHED", 0
    return "NOT CACHED", 2


def _cache_probe(model: str, key: str) -> tuple[dict, str, str]:
    """One production-shaped extraction call. Returns (usage, provider, error).

    Its own request rather than `call()` because the verdict needs OpenRouter's
    usage accounting (`usage: {include: true}`) and the serving provider — a
    prefix cache is per provider, so two calls that scattered across providers
    explain their own miss.
    """
    from pipeline import classify

    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": classify.MINI_SYSTEM},
            {"role": "user",
             "content": f"{classify.SCHEMA_HINT}\n\n---\n{CACHE_CHECK_ITEM}"},
        ],
        "usage": {"include": True},
    }
    if not model.startswith("anthropic/"):
        body["response_format"] = {"type": "json_object"}
        body["provider"] = {"require_parameters": True}
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": USER_AGENT},
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        return {}, "", f"network: {exc}"
    if resp.status_code >= 400:
        return {}, "", f"HTTP {resp.status_code}: {resp.text[:160]}"
    try:
        payload = resp.json()
    except ValueError as exc:
        return {}, "", f"unparseable response: {exc}"
    provider = payload.get("provider") or ""
    return payload.get("usage") or {}, str(provider), ""


def run_cache_check(key: str, model: str) -> int:
    print(f"Two identical PRODUCTION extraction calls to {model}, "
          f"{PROBE_GAP_SECONDS}s apart.")
    print("The verdict is read from OpenRouter's billed usage, never assumed.\n")

    results = []
    for n in (1, 2):
        usage, provider, err = _cache_probe(model, key)
        if err:
            print(f"  call {n}: FAILED — {err}")
            print("\nVERDICT: UNKNOWN — this probe could not check. A run that")
            print("could not check is not a pass. If the failure is HTTP 402,")
            print("top up the OpenRouter key and re-run this exact command.")
            return 3
        results.append(usage)
        print(f"  call {n}: provider={provider or '?':16} "
              f"prompt_tokens={usage.get('prompt_tokens', '?'):>6} "
              f"cached_tokens={_cached_tokens(usage):>6} "
              f"completion={usage.get('completion_tokens', '?'):>5} "
              f"cost=${float(usage.get('cost') or 0):.6f}")
        if n == 1:
            time.sleep(PROBE_GAP_SECONDS)

    verdict, code = cache_verdict(results[0], results[1])
    print(f"\nVERDICT: {verdict} (floor {CACHE_FLOOR_TOKENS} cached tokens on "
          f"call 2; the prefix is ~2,509 modelled tokens)")
    if verdict == "NOT CACHED":
        print("If the two providers above differ, the miss may be routing")
        print("scatter rather than a slug that cannot cache — re-run once")
        print("before concluding. Record BOTH calls' numbers either way.")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B candidate models on real headlines.")
    parser.add_argument("--readthrough", action="store_true",
                        help="compare read-through quality instead of gate verdicts")
    parser.add_argument("--extraction", action="store_true",
                        help="compare the REAL extraction schema, field by field")
    parser.add_argument("--gate-gold", action="store_true",
                        help="score gate models against the HAND LABELS "
                             "(analysis/models/goldset-gate-2026-08.json) "
                             "rather than against each other")
    parser.add_argument("--cache-check", nargs="?", const="google/gemini-2.5-flash-lite",
                        default=None, metavar="MODEL",
                        help="send the production extraction prompt twice and report "
                             "the BILLED cached tokens (default probe: "
                             "google/gemini-2.5-flash-lite)")
    args = parser.parse_args()

    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    if args.cache_check:
        return run_cache_check(key, args.cache_check)
    if args.gate_gold:
        return run_gate_gold(key)

    headlines = load_headlines()
    print(f"{len(headlines)} real headlines from live runs")
    if args.extraction:
        return run_extraction(key, headlines)
    return run_readthrough(key, headlines) if args.readthrough else run_gate(key, headlines)


if __name__ == "__main__":
    sys.exit(main())

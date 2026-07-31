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
    python ab_models.py --readthrough   # quality comparison on survivors only
    python ab_models.py --extraction    # the REAL schema, field by field

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
real 2,754-token `classify.SCHEMA_HINT`. A model can be an excellent gate and a
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


def _same_value(a, b) -> bool:
    """Compared the way the pipeline compares them: case and surrounding space
    are normalised away by `vocab` before anything is stored, so counting them
    as disagreements would manufacture a difference that never reaches a row."""
    if isinstance(a, list) or isinstance(b, list):
        return sorted(map(str, a or [])) == sorted(map(str, b or []))
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B candidate models on real headlines.")
    parser.add_argument("--readthrough", action="store_true",
                        help="compare read-through quality instead of gate verdicts")
    parser.add_argument("--extraction", action="store_true",
                        help="compare the REAL extraction schema, field by field")
    args = parser.parse_args()

    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    headlines = load_headlines()
    print(f"{len(headlines)} real headlines from live runs")
    if args.extraction:
        return run_extraction(key, headlines)
    return run_readthrough(key, headlines) if args.readthrough else run_gate(key, headlines)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Gate accuracy on the hand-labelled gold set via GitHub Models ($0).

Owner-approved one-off (2026-10-08): could the paid LLM gate run on GitHub
Models' free, rate-limited inference instead of OpenRouter? Same production
prompt (`classify.GATE_SYSTEM`, headline+teaser at `GATE_CHARS`), same scorer
(`analysis.models.gate_goldset.score`) as `ab_models.py --gate-gold`, so the
numbers sit beside that mode's flash-lite row. Reads the LIVE catalog (never a
guessed id) and prints the rate-limit headers the endpoint returns.

    GITHUB_TOKEN=... python ab_github_models.py [--models a,b] [--limit N]

Needs a token with `models: read`. Spends nothing; writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

CATALOG = "https://models.github.ai/catalog/models"
ENDPOINT = "https://models.github.ai/inference/chat/completions"


def gate_answer(text: str) -> bool | None:
    """YES/NO from a one-word reply; None = did not answer (counted as a miss)."""
    head = (text or "").strip().strip('"\'`*').upper()
    if head.startswith("YES"):
        return True
    if head.startswith("NO"):
        return False
    return None


def pick_models(catalog: list[dict]) -> list[str]:
    """Every listed xAI model plus the small OpenAI/Meta/Mistral chat models."""
    ids = [m["id"] for m in catalog]
    grok = [i for i in ids if i.lower().startswith("xai/")]
    small = [i for i in ids if any(k in i.lower() for k in
             ("gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini", "ministral",
              "mistral-small", "llama-3.3-70b", "llama-4-scout"))]
    return grok + small


def call(model: str, text: str, token: str) -> tuple[str, dict, str]:
    from pipeline import classify
    body = {"model": model, "temperature": 0, "max_tokens": 8, "messages": [
        {"role": "system", "content": classify.GATE_SYSTEM},
        {"role": "user", "content": text[:classify.GATE_CHARS]}]}
    for attempt in range(4):
        try:
            r = requests.post(ENDPOINT, json=body, timeout=90, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json"})
        except requests.RequestException as exc:
            return "", {}, f"network: {exc}"
        limits = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}
        if r.status_code == 429 and attempt < 3:
            time.sleep(min(int(r.headers.get("retry-after", "20") or 20), 65))
            continue
        if r.status_code >= 400:
            return "", limits, f"HTTP {r.status_code}: {r.text[:160]}"
        msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
        return msg.get("content") or "", limits, ""
    return "", {}, "rate-limited"


def main() -> int:
    from analysis.models import gate_goldset
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("::error::GITHUB_TOKEN missing"); return 1
    r = requests.get(CATALOG, timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        cat = r.json()
    except ValueError:
        print(f"::error::catalog HTTP {r.status_code}: {r.text[:300]!r}")
        return 1
    if isinstance(cat, dict):  # some deployments wrap the list
        cat = cat.get("models") or cat.get("data") or []
    print("::notice::GitHub Models catalog: " + ", ".join(m["id"] for m in cat))
    for m in cat:
        if m["id"].lower().startswith("xai/") or m["id"] in pick_models(cat):
            print(f"  {m['id']}: rate_limit_tier={m.get('rate_limit_tier')}")
    models = [s.strip() for s in args.models.split(",") if s.strip()] or pick_models(cat)
    doc = gate_goldset.load()
    items = gate_goldset.scoreable(doc)
    if args.limit:
        items = items[:args.limit]
    for model in models:
        answers, unparsed, errors, last_limits, t0 = {}, 0, [], {}, time.monotonic()
        for it in items:
            content, limits, err = call(model, it["text"], token)
            last_limits = limits or last_limits
            if err:
                errors.append(err); continue
            v = gate_answer(content)
            if v is None:
                unparsed += 1; continue
            answers[it["id"]] = v
            time.sleep(4)  # stay under the low tier's ~15 req/min
        s = gate_goldset.score(doc, answers)
        lat = (time.monotonic() - t0) / max(len(items), 1)
        print(f"::notice::GHM {model} acc={s['correct']}/{s['total']}={s['accuracy']:.1%} "
              f"recall={s['recall']:.1%} precision={s['precision']:.1%} "
              f"unparsed={unparsed} errors={len(errors)} s_per_item={lat:.1f}")
        if errors:
            print(f"::warning::GHM {model} first error: {errors[0]}")
        print(f"::notice::GHM {model} ratelimit headers: {last_limits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

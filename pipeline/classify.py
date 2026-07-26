"""LLM classification via OpenRouter.

Cost discipline (spec 4): the system prompt is deliberately tiny, candidates
are keyword-gated before they ever reach here, and a 402 stops the run rather
than burning a batch of failures.
"""

from __future__ import annotations

import json
import os

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("TIT_MODEL", "deepseek/deepseek-chat")
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# Spec 4 rule 1: a narrow classification does not need a 1,400-token prompt.
MINI_SYSTEM = (
    "Classify a talent-market news item. Use only facts present in the text. "
    "Never state a number that is not in the text. Reply with JSON only."
)

SCHEMA_HINT = """Return JSON with exactly these keys:
{"is_talent_signal": true|false,
 "company": "the employer named, or empty",
 "pillar": "company_development|leadership_change|rewards_comp|how_we_work",
 "signal_direction": "hiring|displacement|neutral|comp_shift",
 "city": "city named in the text, or empty",
 "country": "country named in the text, or empty",
 "confidence": "verified|reported|rumored",
 "headline": "the factual headline, unembellished",
 "summary": "1-2 sentences restating ONLY what the source says",
 "talent_readthrough": "1 sentence: what this means for hiring, displacement or comp in that place. This is your interpretation.",
 "predicted_outcome": "a checkable consequence, or empty",
 "check_after_date": "YYYY-MM-DD when that could be checked, or empty"}

Set is_talent_signal false for anything that is not a hiring, leadership, comp
or location-strategy development at a named employer."""


class CreditsExhausted(RuntimeError):
    """Raised on a 402 so the caller stops cleanly (spec 4 rule 4)."""


class AuthFailed(RuntimeError):
    """Raised on a 401. A bad key is permanent for the run, so retrying it 25
    times just prints the same error 25 times — the first live run did exactly
    that."""


class ClassifyError(RuntimeError):
    pass


def classify(raw: dict, *, timeout: int = 45) -> dict | None:
    """Classify one candidate. Returns None if it is not a talent signal."""
    # Strip: a key pasted into a secrets box often carries a trailing newline,
    # which makes the Authorization header malformed and the failure look like
    # a missing key rather than a whitespace problem.
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise AuthFailed("OPENROUTER_API_KEY is not set")
    if "..." in api_key or len(api_key) < 40:
        raise AuthFailed(
            "OPENROUTER_API_KEY looks truncated — it may be the abbreviated "
            "value shown in the dashboard rather than the full key"
        )

    text = (raw.get("raw_text") or "").strip()
    if not text:
        raise ClassifyError("raw_text is empty")

    body = {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        # OpenRouter routes a model across several providers, and not all of
        # them honour response_format. One that ignores it returns empty
        # content, which looks like a parse bug rather than a routing one —
        # 8 of the first 10 live classifications failed this way. This pins
        # routing to providers that actually support the parameters we send.
        "provider": {"require_parameters": True},
        "messages": [
            {"role": "system", "content": MINI_SYSTEM},
            {"role": "user", "content": f"{SCHEMA_HINT}\n\n---\n{text[:4000]}"},
        ],
    }

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        json=body,
        timeout=timeout,
    )

    if resp.status_code == 402:
        raise CreditsExhausted("OpenRouter returned 402 — stopping the run")
    if resp.status_code == 401:
        raise AuthFailed(f"OpenRouter rejected the API key (401): {resp.text[:200]}")
    if resp.status_code >= 400:
        raise ClassifyError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")

    try:
        choice = resp.json()["choices"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise ClassifyError(f"unexpected response shape: {exc}") from exc

    content = (choice.get("message", {}).get("content") or "").strip()
    if not content:
        # Say WHY it was empty. "Expecting value: line 1 column 1" tells you
        # nothing; finish_reason usually tells you everything.
        raise ClassifyError(
            f"model returned empty content (finish_reason="
            f"{choice.get('finish_reason')!r}, provider={resp.json().get('provider')!r})"
        )

    try:
        parsed = json.loads(_strip_fences(content))
    except ValueError as exc:
        raise ClassifyError(f"unparseable model response: {exc} — got {content[:200]!r}") from exc

    if not parsed.get("is_talent_signal"):
        return None
    return parsed


def _strip_fences(content: str) -> str:
    """Models wrap JSON in ```json fences often enough to be worth handling."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text

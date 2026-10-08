#!/usr/bin/env python3
"""Would anthropic/claude-haiku-5.5 do each paid job as well as today's model?

    python eval_haiku55.py --dry-run     # build every prompt, spend nothing

Owner question, 2026-10-08. DISPATCH-ONLY (eval-haiku55.yml). Writes nothing
to the database, posts nothing, reads no WordPress key. One request per call,
no retries, and a hard $1.00 run cap (EVAL_CAP_USD) checked before every call
against OpenRouter's own billed `usage.cost`. Results leave as ::notice:: lines.

FOUR CALL TYPES, scored with the repo's own validators:

  gate        classify.GATE_SYSTEM (TIT_GATE_MODEL) on the hand-labelled gate
              gold set, scored by analysis.models.gate_goldset.score (ACCURACY).
  extraction  classify.MINI_SYSTEM + SCHEMA_HINT (TIT_MODEL) on the same items:
              is_talent_signal vs the gold label, JSON validity, and agreement
              with the incumbent on the deciding fields.
  read        prompts.READ_SYSTEM + prompts.build (TIT_READ_MODEL) on the gold
              positives, held to classify's production acceptance rule
              (non-empty, ungrounded_reason, _HEDGE). No gold sentence exists,
              so this is the production guard pass rate, not accuracy.
  referee     adjudicate_guardrail.PROMPT (ADJ_REFEREE_A/B) on settled money
              findings (both referees agreed and it was applied, or the owner
              ruled), evidence = the row's stored text (the production
              fallback), scored on `recommended` vs the settled action.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"
CANDIDATE = "anthropic/claude-haiku-5.5"
GATE_MODELS = ("google/gemini-2.5-flash-lite", CANDIDATE)
EXTRACTION_MODELS = ("google/gemini-2.5-flash-lite", CANDIDATE)
READ_MODELS = ("anthropic/claude-sonnet-5", CANDIDATE)
REFEREE_MODELS = ("anthropic/claude-sonnet-4.5", "openai/gpt-4o",
                  "google/gemini-2.5-flash-lite", CANDIDATE)
CAP_USD = float(os.environ.get("EVAL_CAP_USD", "1.00"))
DECIDING = ("is_talent_signal", "company", "pillar", "country")

_spent = {"usd": 0.0}


def notice(title, msg):
    print(f"::notice title={title}::{msg}", flush=True)


def call(model, system, user, *, max_tokens, json_mode=True, key=""):
    """(content | None, cost_usd, error). ONE request, never retried."""
    if _spent["usd"] >= CAP_USD:
        return None, 0.0, "cap"
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "usage": {"include": True},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    if json_mode and not model.startswith("anthropic/"):
        body["response_format"] = {"type": "json_object"}
        body["provider"] = {"require_parameters": True}
    try:
        resp = requests.post(ENDPOINT, json=body, timeout=90, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return None, 0.0, f"network {type(exc).__name__}"
    if resp.status_code >= 400:
        return None, 0.0, f"HTTP {resp.status_code}"
    payload = resp.json()
    cost = float((payload.get("usage") or {}).get("cost") or 0)
    _spent["usd"] += cost
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return content, cost, None


def parse_json(content):
    if content is None:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        out = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


class Tally:
    def __init__(self):
        self.n = self.ok = self.err = self.correct = self.judged = 0
        self.calls = 0
        self.cost = 0.0
        self.tp = self.fp = self.fn = 0

    def add_call(self, cost, err):
        self.n += 1
        if err not in ("cap",):
            self.calls += 1
        self.cost += cost
        if err:
            self.err += 1

    def line(self, extra=""):
        per_k = 1000 * self.cost / self.calls if self.calls else 0
        acc = f"{self.correct / self.judged:.1%} ({self.correct}/{self.judged})" if self.judged else "n/a"
        pr = ""
        if self.tp + self.fp + self.fn:
            p = self.tp / (self.tp + self.fp) if self.tp + self.fp else 0
            r = self.tp / (self.tp + self.fn) if self.tp + self.fn else 0
            pr = f" precision={p:.1%} recall={r:.1%}"
        return (f"acc={acc}{pr} valid={self.ok}/{self.n} errors={self.err} "
                f"spend=${self.cost:.4f} cost_per_1k_calls=${per_k:.3f}{extra}")


def run_gate(items, key, dry):
    from analysis.models import gate_goldset
    from pipeline import classify
    doc = gate_goldset.load()
    for m in GATE_MODELS:
        t, answers = Tally(), {}
        for it in items:
            if dry:
                continue
            content, cost, err = call(m, classify.GATE_SYSTEM, it["text"][:classify.GATE_CHARS],
                                      max_tokens=8, json_mode=False, key=key)
            t.add_call(cost, err)
            head = (content or "").strip().upper()
            v = True if head.startswith("YES") else False if head.startswith("NO") else None
            if v is not None:
                t.ok += 1
                answers[it["id"]] = v
            time.sleep(0.1)
        s = gate_goldset.score(doc, answers)
        t.correct, t.judged = s["correct"], s["total"]
        notice(f"gate {m}", t.line(f" recall={s['recall']:.1%} precision={s['precision']:.1%} "
                                   f"wilson95={s['accuracy_lo']:.1%}-{s['accuracy_hi']:.1%}"))


def run_extraction(items, key, dry):
    from pipeline import classify
    got = {}
    for m in EXTRACTION_MODELS:
        t, got[m] = Tally(), {}
        for it in items:
            if dry:
                continue
            content, cost, err = call(m, classify.MINI_SYSTEM,
                                      f"{classify.SCHEMA_HINT}\n\n---\n{it['text']}",
                                      max_tokens=1000, key=key)
            t.add_call(cost, err)
            parsed = parse_json(content)
            if parsed is None or not isinstance(parsed.get("is_talent_signal"), bool):
                continue
            t.ok += 1
            got[m][it["id"]] = parsed
            gold = str(it["gold_is_talent_signal"]) == "True"
            pred = parsed["is_talent_signal"]
            t.judged += 1
            t.correct += pred == gold
            t.tp += pred and gold
            t.fp += pred and not gold
            t.fn += (not pred) and gold
            time.sleep(0.1)
        agree = ""
        if m != EXTRACTION_MODELS[0]:
            base = got[EXTRACTION_MODELS[0]]
            both = [i for i in got[m] if i in base]
            for f in DECIDING:
                same = sum(str(got[m][i].get(f)).strip().lower() ==
                           str(base[i].get(f)).strip().lower() for i in both)
                agree += f" agree_{f}={same}/{len(both)}"
        notice(f"extraction {m}", t.line(agree))
    return got


def run_read(items, extracted, key, dry):
    from pipeline import classify, prompts
    base = extracted.get(EXTRACTION_MODELS[0], {})
    pos = [it for it in items if str(it["gold_is_talent_signal"]) == "True"
           and (it["id"] in base or dry)]
    for m in READ_MODELS:
        t, hedged, ungrounded = Tally(), 0, 0
        for it in pos:
            classified = base.get(it["id"], {})
            raw = {"headline": it["text"].splitlines()[0], "raw_text": it["text"]}
            prompt = prompts.build(classified, raw)
            if dry:
                continue
            content, cost, err = call(m, prompts.READ_SYSTEM, prompt,
                                      max_tokens=classify.READ_MAX_TOKENS,
                                      json_mode=not m.startswith("anthropic/"), key=key)
            t.add_call(cost, err)
            parsed = parse_json(content)
            sentence = ((parsed or {}).get("talent_readthrough") or "").strip()
            if not sentence:
                continue
            t.ok += 1
            if classify.ungrounded_reason(sentence, classified, raw["raw_text"]):
                ungrounded += 1
                continue
            t.judged += 1
            t.correct += 1          # passed the production acceptance rule
            hedged += bool(classify._HEDGE.search(sentence))
            time.sleep(0.1)
        notice(f"read {m}", t.line(f" (acc = production guard pass rate) ungrounded={ungrounded} hedged={hedged}"))


def referee_items():
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "analysis", "adjudications", "*.json"))):
        d = json.load(open(path, encoding="utf-8"))
        if not str(d.get("key", "")).startswith("amount/") or not d.get("row"):
            continue
        if d.get("status") not in ("applied", "agree-dry-run", "owner-ruled"):
            continue
        if d.get("action") in ("accept", "reject", "edit"):
            out.append(d)
    return out


def run_referee(key, dry, limit):
    import adjudicate_guardrail as ag
    specs = referee_items()[:limit]
    for m in REFEREE_MODELS:
        t = Tally()
        for d in specs:
            evidence = ag.stored_evidence(d["row"])
            if not evidence:
                continue
            prompt = ag.PROMPT.format(
                rules=ag.RULES,
                finding=json.dumps({"key": d["key"], "label": d.get("label")}, ensure_ascii=False),
                row=json.dumps(ag.row_view(d["row"]), indent=1, ensure_ascii=False, default=str),
                evidence=evidence)
            if dry:
                continue
            content, cost, err = call(m, "You are a precise, literal referee. JSON only.",
                                      prompt, max_tokens=500, key=key)
            t.add_call(cost, err)
            parsed = parse_json(content)
            if not parsed or parsed.get("recommended") not in ("accept", "reject", "edit"):
                continue
            t.ok += 1
            t.judged += 1
            t.correct += parsed["recommended"] == d["action"]
            time.sleep(0.1)
        notice(f"referee {m}", t.line(f" items={len(specs)}"))


def main(argv=None):
    from analysis.models import gate_goldset
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--referee", type=int, default=int(os.environ.get("EVAL_REFEREE_N", 30)))
    args = ap.parse_args(argv)
    dry = args.dry_run or bool(os.environ.get("EVAL_DRY_RUN"))
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key and not dry:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    items = gate_goldset.scoreable(gate_goldset.load())
    notice("eval-haiku55", f"gate gold items={len(items)} referee specs={len(referee_items())} "
           f"cap=${CAP_USD:.2f} dry={dry}")
    run_gate(items, key, dry)
    extracted = run_extraction(items, key, dry)
    run_referee(key, dry, args.referee)
    run_read(items, extracted, key, dry)
    notice("eval-haiku55 total", f"billed spend this run ${_spent['usd']:.4f} (cap ${CAP_USD:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

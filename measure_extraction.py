#!/usr/bin/env python3
"""Measure what production extraction actually got right, against the BODIES.

    python measure_extraction.py --draw                 # offline, free
    python measure_extraction.py --estimate             # price it, free
    OPENROUTER_API_KEY=... python measure_extraction.py --grade --ceiling 2.00
    python measure_extraction.py --report               # offline, free

WHAT THIS IS, AND WHAT EVERY OTHER MEASUREMENT HERE IS NOT
----------------------------------------------------------
`measure_recall.py` asks what we MISSED. `ab_models.py` asks whether a
challenger AGREES with the incumbent, which is blind when both are wrong and
reads a correction as a regression. `analysis/models/gate_goldset.py` grades
the GATE — one yes/no question, on 75 English items.

None of them asks the question this does: **of the records we published, how
many of the fields are right?** Extraction reads a headline and a teaser and
writes twenty structured fields; the body of the article is the only thing that
can say whether `company`, the amount, the headcount, the country, the
direction and the event type are what the source actually states.

WHY THE BODY HAS TO BE FETCHED
------------------------------
`signals` stores no `raw_text` — by design, for size and for copyright. So the
evidence is fetched at grading time through `adjudicate_guardrail.fetch_evidence`,
the SAME door the two-referee adjudicator reads a cited source through:
archived copy first, then the publisher, then Wayback, and a read under
`EVIDENCE_MIN_CHARS` is refused rather than guessed from. A row whose body
cannot be read is UNKNOWN for every field and costs nothing.

HOW A FIELD IS GRADED
---------------------
By the rule the owner set on 2026-09-11 for data decisions: **two referees
from different vendors, and only their agreement counts.** Each reads the body
and the six stored values under production's OWN definitions (imported from
`classify.SCHEMA_HINT`, never retyped), and answers `correct`, `wrong` or
`not_stated` per field.

    both correct                -> CORRECT
    both wrong                  -> WRONG
    one correct, one wrong      -> UNKNOWN (disagreement)
    either says not_stated      -> UNKNOWN (the body does not settle it)
    a referee gave no verdict   -> UNKNOWN (and counted as a parse failure)

**UNKNOWN is never a pass and never a failure.** It is reported as its own
column with its own reason, and the accuracy denominator is CORRECT + WRONG
alone. Every rate carries its Wilson interval (`analysis/recall/stats.wilson`,
the one implementation in this repo).

PARSE FAILURES ARE COUNTED APART FROM WRONG ANSWERS. The sibling tracker stored
33 of 90 truncated, code-fenced answers as rejections and measured a model on
them. So: fences are stripped, `MAX_TOKENS` carries headroom, an answer that
does not parse is a referee failure with its own counter, and it can never
reach the accuracy denominator.

WHAT IT COSTS, AND WHO PAYS
---------------------------
DISCRETIONARY (`TIT_RUN_KIND`), so it cannot touch the collectors' pot. The
gate — `adjudicate_guardrail._gate`, reused rather than re-implemented — reads
`TIT_PAID_READS` and this run's ceiling immediately BEFORE every request, and
the cost is metered immediately after from OpenRouter's own usage accounting.
`--estimate` prices the run from the live model list before a cent is spent and
refuses to start a run it cannot afford. `CEILING_MAX_USD` is a hard bar this
program will not take an argument past.

THIS MEASURES. IT MOVES NOTHING. No model swap, no row edited, no publish. The
only writes are the sample, the result file, and one priced health row.

Exit codes: 0 the run completed (whatever it found); 2 a stratum came back with
nothing judged, so the measurement is incomplete and a human should read it;
1 a hard failure (no sample, no key, a ceiling out of bounds).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import requests  # noqa: E402

import adjudicate_guardrail as adj  # noqa: E402
import budget  # noqa: E402
from analysis.extraction import frame  # noqa: E402
from analysis.recall.stats import wilson  # noqa: E402
from pipeline import classify, schema, store  # noqa: E402

OUT_DIR = REPO / "analysis" / "extraction"
HEALTH_NAME = "extraction_benchmark"

#: Two vendors, as the adjudication rule requires, and the cheapest pair that
#: can read an article in any language and answer a structured question about
#: it. They are NOT the adjudicator's pair (sonnet-4.5 + gpt-4o): that pair
#: decides a published figure one row at a time, this one reads 200 bodies, and
#: at 200 rows the adjudicator's pair prices at about $4 against this one's
#: $1.25. Overridable so a later run can re-measure with a stronger pair and say
#: which it used; the pair is written into the result file either way.
#:
#: NOT A GPT-5 FAMILY MODEL. The first paid run (2026-09-16, $0.85) paired
#: haiku with gpt-5-mini and gpt-5-mini answered 0 of 164: every request was
#: rejected before generation (zero billed tokens on 164 rows, so a 4xx and
#: not a truncation), and `classify._call` sends `temperature: 0`, which that
#: family refuses. It is the third time this repo has sent a GPT-5 model
#: through that door and the third time it answered nothing (gpt-5-nano:
#: "40 errors, unusable" 2026-07-28; 0 of 75 on 2026-08-14). gpt-4o-mini is
#: the same vendor and the same non-reasoning request shape the adjudicator's
#: gpt-4o answered 16 rows with on the same day. gpt-4o itself prices this run
#: at $3.62, past the $2.00 ceiling the run is approved at.
REFEREES = (
    os.environ.get("BENCH_REFEREE_A", "anthropic/claude-haiku-4.5"),
    os.environ.get("BENCH_REFEREE_B", "openai/gpt-4o-mini"),
)

#: The six fields the benchmark grades, as (stored column, reader-facing label).
#: `company`, `country`, the direction and the event type are what a row IS;
#: the amount and the headcount are the two numbers a reader acts on.
FIELDS = (
    ("company", "company"),
    ("funding_amount", "amount"),
    ("headcount", "headcount"),
    ("country", "country"),
    ("signal_direction", "direction"),
    ("pillar", "event type"),
)
FIELD_KEYS = tuple(key for key, _ in FIELDS)
VERDICTS = ("correct", "wrong", "not_stated")

TARGET = 200
MIN_CELL = 5
#: How much of the body each referee reads. Below the adjudicator's 12,000
#: because this prompt asks six questions rather than one and pays for the text
#: twice, and because the fields under measurement are stated near the top of a
#: news story. Stated as a limit, not hidden: a figure that appears only in the
#: last paragraph of a very long article is a read this benchmark can miss.
EVIDENCE_CAP = 8_000
#: Headroom. Six fields with a short reason each is ~350 tokens; a referee cut
#: off mid-JSON is a parse failure that measures the cap, not the model.
MAX_TOKENS = 1_200
CALL_TIMEOUT = 90
#: Per referee, per row. The retry lives HERE and never inside the paid call.
ATTEMPTS = 2
#: The hard bar. The owner's one-time grant is $20.00 and this run was approved
#: at no more than $6.00 of it; a `--ceiling` past this is refused rather than
#: clamped, because a clamped ceiling is one nobody reads.
CEILING_MAX_USD = 6.00
DEFAULT_CEILING_USD = 2.00
#: This repo's own calibration, the same one `cost_projection.py` prices with.
CHARS_PER_TOKEN = 4.39
MODELS_URL = "https://openrouter.ai/api/v1/models"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"


# --------------------------------------------------------------------------
# the definitions, taken from production rather than retyped
# --------------------------------------------------------------------------

def field_rules(schema_hint: str | None = None) -> dict[str, str]:
    """Production's own wording for each graded field, out of `SCHEMA_HINT`.

    Imported, never copied. A benchmark that carries its own description of
    `pillar` grades the model against a definition nothing in production uses,
    and the divergence is invisible: both sides read fine.
    """
    text = classify.SCHEMA_HINT if schema_hint is None else schema_hint
    rules: dict[str, str] = {}
    for key in FIELD_KEYS:
        needle = f'"{key}": "'
        start = text.find(needle)
        if start == -1:
            continue
        start += len(needle)
        end = start
        while True:
            end = text.find('",', end)
            if end == -1:
                end = text.rfind('"')
                break
            if text[end - 1] != "\\":
                break
            end += 2
        rules[key] = text[start:end].replace('\\"', '"').strip()
    return rules


def direction_rules(schema_hint: str | None = None) -> str:
    """The prose block under the schema that defines `signal_direction`.

    `"signal_direction": "hiring|displacement|neutral|comp_shift"` is a
    vocabulary and not a definition: the definition is the paragraph below the
    JSON, and a referee that never sees it will mark every funding round
    "hiring", which is the exact mistake the paragraph was written to stop.
    """
    text = classify.SCHEMA_HINT if schema_hint is None else schema_hint
    start = text.find("signal_direction is what the SOURCE STATES")
    return text[start:].strip() if start != -1 else ""


PROMPT = """You are one of two independent referees auditing a talent-market tracker's
automated extraction. The other referee is a model from a different vendor; you
will not see its answer. You are NOT re-extracting the record. You are deciding,
for each field below, whether the value the tracker stored is what THIS SOURCE
TEXT states. Be strict and literal, and decide only from the source text.

The tracker's own definitions, which are the rules you apply:
{rules}

{direction}

EVERY VERDICT IS EXACTLY ONE OF THESE THREE WORDS, spelled exactly:
"correct", "wrong", "not_stated". There is no fourth word. Do not write
"partially_correct", "unclear", "n/a", "neutral", or any other value, and do
not leave a field out: an answer carrying any other word is discarded unread
and the row is lost from the measurement.

WHAT EACH ANSWER MEANS:
- "correct": the source text states this, and the stored value is it. An empty
  stored value is CORRECT when the source states nothing for that field.
- "wrong": the source states something different, or the source states a value
  and the stored value is empty, or the stored value is empty of meaning.
- "not_stated": this source text does not settle the field either way (it is
  truncated before the relevant part, it is about something else, or the field
  is one the definition says comes from outside the text). Use it whenever you
  would have to guess. "not_stated" is not a criticism; it is honesty, and the
  tracker records it as UNKNOWN rather than as a pass or a failure.

"not_stated" IS THE ONLY WAY TO DECLINE. If you cannot judge a field for any
reason at all, answer "not_stated" for that field and say why in "why". Never
express doubt by inventing a verdict word, by omitting a field, or by writing
prose outside the JSON object.

EVERY STORED VALUE IS GRADEABLE, INCLUDING "neutral". "neutral" is an ordinary
value of signal_direction with a definition above, exactly like "hiring",
"displacement" and "comp_shift". Judge it the same way: "correct" when the
source describes an event that the definition calls neutral, "wrong" when the
source describes one of the other three. That the stored value is "neutral",
or is empty, or looks like a default, is NEVER a reason to decline a verdict
or to reach for a word outside the three. The same holds for every other
field.

THE STORED RECORD:
{row}

THE SOURCE TEXT (may be truncated):
<<<
{evidence}
>>>

Answer with ONE JSON object and nothing else, no markdown fence:
{{"source_language": "the ISO 639-1 code of the language this source text is written in",
  "fields": {{
    {fields}
  }}}}
Each field object is
{{"verdict": "correct"|"wrong"|"not_stated", "source_value": "what the source states, or empty", "why": "at most 160 characters"}}
"""


def build_prompt(row: dict, evidence: str, rules: dict[str, str] | None = None) -> str:
    rules = field_rules() if rules is None else rules
    rule_lines = "\n".join(f"- {key}: {rules.get(key, '')}" for key in FIELD_KEYS)
    field_lines = ",\n    ".join(f'"{key}": {{...}}' for key in FIELD_KEYS)
    return PROMPT.format(
        rules=rule_lines,
        direction=direction_rules(),
        row=json.dumps(stored_view(row), ensure_ascii=False, indent=2),
        evidence=evidence[:EVIDENCE_CAP],
        fields=field_lines,
    )


def stored_view(row: dict) -> dict:
    """Exactly the six graded values, plus the headline for context.

    Nothing else: a referee shown `summary` and `talent_readthrough` grades the
    prose it just read rather than the field it was asked about.
    """
    view = {"headline": row.get("headline") or ""}
    for key in FIELD_KEYS:
        value = row.get(key)
        view[key] = "" if value in (None, "") else value
    return view


# --------------------------------------------------------------------------
# the draw
# --------------------------------------------------------------------------

def frame_rows(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 AND collector IN "
        f"({','.join('?' * len(frame.NEWS_COLLECTORS))})", frame.NEWS_COLLECTORS)]
    return [r for r in rows if frame.eligible(r)]


def _order_key(seed: str, row: dict) -> str:
    return hashlib.sha256(f"{seed}:{row['content_hash']}".encode()).hexdigest()


def draw(rows: list[dict], target: int = TARGET, seed: str = "",
         min_cell: int = MIN_CELL) -> list[dict]:
    """A stratified sample: region x event type, with the language mix held.

    Deterministic for a given (frame, seed): the order inside a cell is a hash
    of the seed and the row's content_hash, so the draw can be reproduced and
    audited without storing a random state, and adding a row to the corpus
    cannot silently reshuffle a sample that was already graded.

    THE LANGUAGE MIX IS HELD PER CELL, not globally. A global floor is satisfied
    by taking every non-English row from whichever cell has most of them, which
    is how a "multilingual" sample ends up being one cell's worth of Portuguese.
    """
    cells: dict[tuple[str, str], list[dict]] = {}
    for region in frame.REGIONS:
        for event in frame.EVENT_TYPES:
            cells[(region, event)] = []
    for row in rows:
        cells.setdefault(frame.cell_of(row), []).append(row)

    quota = frame.allocate({cell: len(members) for cell, members in cells.items()},
                           target, min_cell=min_cell)
    drawn: list[dict] = []
    for cell in sorted(cells):
        want = quota.get(cell, 0)
        members = sorted(cells[cell], key=lambda r: _order_key(seed, r))
        if not want or not members:
            continue
        foreign = [r for r in members if frame.likely_non_english(r)]
        english = [r for r in members if not frame.likely_non_english(r)]
        want_foreign = min(len(foreign), round(want * len(foreign) / len(members)))
        want_english = min(len(english), want - want_foreign)
        want_foreign = min(len(foreign), want - want_english)
        picked = foreign[:want_foreign] + english[:want_english]
        # Whichever pool ran short, top up from the other rather than returning
        # a short cell: the quota is the measurement's resolution.
        if len(picked) < want:
            rest = [r for r in members if r not in picked]
            picked += rest[:want - len(picked)]
        drawn.extend(sorted(picked, key=lambda r: _order_key(seed, r)))
    return drawn


def sample_record(rows: list[dict], seed: str, frame_size: int,
                  cell_sizes: dict[tuple[str, str], int]) -> dict:
    return {
        "drawn_on": _dt.date.today().isoformat(),
        "seed": seed,
        "target": len(rows),
        "frame": {
            "definition": "current signals from the LLM extraction path with a fetchable source",
            "collectors": list(frame.NEWS_COLLECTORS),
            "size": frame_size,
            "cells": {f"{r}/{e}": n for (r, e), n in sorted(cell_sizes.items())},
        },
        "strata": {
            "region": list(frame.REGIONS),
            "event_type": list(frame.EVENT_TYPES),
            "language": "proxy at draw time (non-Latin script, or a non-anglophone publisher); "
                        "the measured language comes back from the referees",
        },
        "drawn_cells": _counts(rows, lambda r: f"{frame.region_of(r)}/{frame.event_type_of(r)}"),
        "drawn_language_proxy": {
            "likely_non_english": sum(1 for r in rows if frame.likely_non_english(r)),
            "non_latin_script": sum(1 for r in rows if frame.non_latin_headline(r)),
        },
        "items": [
            {
                "content_hash": r["content_hash"],
                "signal_id": r["signal_id"],
                "region": frame.region_of(r),
                "event_type": frame.event_type_of(r),
                "likely_non_english": frame.likely_non_english(r),
                "collector": r["collector"],
                "source_url": r["source_url"],
                "archive_url": r.get("archive_url") or "",
                "stored": stored_view(r),
            }
            for r in rows
        ],
    }


def _counts(rows, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[key(row)] = out.get(key(row), 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# the price, before anything is spent
# --------------------------------------------------------------------------

def prices(fetch=None) -> dict[str, tuple[float, float]]:
    """(prompt, completion) USD per token, live. {} when the list is unreadable."""
    try:
        data = (fetch or _fetch_models)()
    except Exception:  # noqa: BLE001 - an unreadable price list is not a verdict
        return {}
    out: dict[str, tuple[float, float]] = {}
    for model in data:
        pricing = model.get("pricing") or {}
        try:
            out[model["id"]] = (float(pricing.get("prompt", 0)),
                                float(pricing.get("completion", 0)))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _fetch_models() -> list[dict]:
    resp = requests.get(MODELS_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def estimate(items: int, prompt_chars: int, price_table: dict, referees=REFEREES,
             out_tokens: int = 420) -> tuple[float | None, list[str]]:
    """(USD for the whole run, per-referee lines). None when a price is unknown.

    UNKNOWN, not zero: an unpriceable run is one nobody can approve, and a
    missing price that reads as free is how a ceiling stops meaning anything.
    """
    in_tokens = prompt_chars / CHARS_PER_TOKEN
    total = 0.0
    lines = []
    for model in referees:
        price = price_table.get(model)
        if not price:
            lines.append(f"  {model}: UNKNOWN, not priced by the live model list")
            return None, lines
        each = in_tokens * price[0] + out_tokens * price[1]
        lines.append(f"  {model}: ${each:.5f} x {items} = ${each * items:.2f}")
        total += each * items
    return total, lines


# --------------------------------------------------------------------------
# the referees
# --------------------------------------------------------------------------

def parse_answer(content: str) -> tuple[dict | None, str]:
    """(verdict, reason). A reason is only present when the verdict is None.

    Fences are stripped before parsing, and a truncated answer is a PARSE
    FAILURE with its own reason string — never a "wrong" and never a silent
    drop. That distinction is the one the sibling tracker got wrong, storing 33
    truncated code-fenced answers as rejections.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None, "no JSON object in the answer"
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError as exc:
        return None, f"unparseable JSON ({exc})"
    if not isinstance(parsed, dict):
        return None, "the answer is not an object"
    fields = parsed.get("fields")
    if not isinstance(fields, dict):
        return None, "no fields object"
    clean: dict[str, dict] = {}
    for key in FIELD_KEYS:
        entry = fields.get(key)
        if not isinstance(entry, dict) or entry.get("verdict") not in VERDICTS:
            return None, f"field {key} carries no usable verdict"
        clean[key] = {
            "verdict": entry["verdict"],
            "source_value": str(entry.get("source_value") or "")[:200],
            "why": str(entry.get("why") or "")[:200],
        }
    language = str(parsed.get("source_language") or "").strip().lower()[:8]
    return {"source_language": language, "fields": clean}, ""


def ask(model: str, prompt: str, *, start_usd: float, ceiling: float,
        call=None, attempts: int = ATTEMPTS) -> tuple[dict | None, str, float]:
    """One referee's answer: (verdict or None, reason, cost).

    The gate is `adjudicate_guardrail._gate` — reused, not re-implemented, so
    the month's switch and this run's ceiling are read by the same code that
    reads them for an adjudication. It is read immediately before every request
    and the cost is metered immediately after, and `call` performs exactly one
    request: the retry is this loop, never inside the callable.
    """
    call = call or classify._call
    before = float(classify.STATS.get("usd", 0.0))
    content, reason = None, ""
    for _attempt in range(attempts):
        adj._gate(start_usd, ceiling)
        try:
            content = call(model, classify.MINI_SYSTEM, prompt,
                           timeout=CALL_TIMEOUT, max_tokens=MAX_TOKENS, json_mode=True)
            break
        except (classify.Throttled, classify.ClassifyError) as exc:
            reason = no_answer(exc)
            content = None
    cost = float(classify.STATS.get("usd", 0.0)) - before
    if content is None:
        return None, reason or "no answer", cost
    verdict, why = parse_answer(content)
    if verdict is None:
        # Keep WHAT was said, not only that it did not parse. The 2026-09-16
        # re-run lost 99 of 167 rows to `field signal_direction carries no
        # usable verdict` and the word the referee actually wrote was gone
        # with the run, so the cause was a correlation rather than a reading.
        why = f"{why}; answer: {' '.join(content.split())[:ANSWER_CHARS]}"
    return verdict, why, cost


#: How much of an unparseable answer is kept beside its reason.
ANSWER_CHARS = 600


#: How much of a refusal is kept. `classify._call` already clips the upstream
#: body to 300 characters; this keeps the whole of that.
REASON_CHARS = 320


def no_answer(exc: BaseException) -> str:
    """The reason recorded for a referee that raised instead of answering.

    The exception's MESSAGE, not only its class. The first paid run recorded
    164 rows of `no answer (ClassifyError)` and the run log printed nothing
    more, so the cause (an HTTP 4xx from the second referee's endpoint) had to
    be reconstructed from the bill. A result file must be diagnosable alone.
    """
    text = " ".join(str(exc).split())
    return f"no answer ({type(exc).__name__}: {text[:REASON_CHARS]})"


#: The mid-run brake. The preflight catches a referee that cannot answer AT
#: ALL; it cannot catch one that answers a one-line question and then fails on
#: the real prompt, which is what happened on 2026-09-16 (gpt-4o-mini, 99 of
#: 167 rows, a word outside the verdict vocabulary). After this many rows with
#: a readable body, a referee failing more than `ABORT_RATE` of them stops the
#: run instead of paying out the remaining sample.
ABORT_AFTER_ROWS = 20
ABORT_RATE = 0.5


class RefereeStop(RuntimeError):
    """A referee is failing most rows, so the rest of the sample is not bought."""


def referee_to_stop(records: list[dict]) -> tuple[str, int, int] | None:
    """(model, failures, readable rows) for a referee past the brake, else None.

    Judged only on rows with a readable body: an unreadable one asks nobody and
    must not count against a referee. UNKNOWN is not a verdict here either -
    this decides whether to keep SPENDING, and the rows already graded stand.
    """
    readable = [r for r in records if r.get("evidence_chars")]
    if len(readable) < ABORT_AFTER_ROWS:
        return None
    for model in REFEREES:
        failed = sum(1 for r in readable
                     if not (r.get("referees", {}).get(model) or {}).get("answered"))
        if failed > ABORT_RATE * len(readable):
            return model, failed, len(readable)
    return None


#: What the preflight asks. Tiny, so a referee that cannot answer at all costs
#: a fraction of a cent rather than the sample.
PREFLIGHT_PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true}'
PREFLIGHT_MAX_TOKENS = 20


def preflight(*, start_usd: float, ceiling: float, referees=None,
              call=None, attempts: int = ATTEMPTS) -> dict[str, str]:
    """{model: reason} for every referee that gives NO answer to a one-line question.

    Runs before the first body is fetched. On 2026-09-16 the second referee
    rejected every one of its 164 requests and the run still paid the first
    referee $0.85 to grade 164 rows nobody could agree with. Same discipline as
    `ask`: the gate is read before every attempt and each attempt is exactly one
    request. Only whether a reply came back is judged, never its content: a
    referee that answers is then graded on the sample like any other. An empty
    dict means every referee answered.
    """
    call = call or classify._call
    dead: dict[str, str] = {}
    for model in referees or REFEREES:
        reason = ""
        for _attempt in range(attempts):
            adj._gate(start_usd, ceiling)
            try:
                call(model, classify.MINI_SYSTEM, PREFLIGHT_PROMPT,
                     timeout=CALL_TIMEOUT, max_tokens=PREFLIGHT_MAX_TOKENS, json_mode=True)
                reason = ""
                break
            except (classify.Throttled, classify.ClassifyError) as exc:
                reason = no_answer(exc)
        if reason:
            dead[model] = reason
    return dead


# --------------------------------------------------------------------------
# the agreement rule
# --------------------------------------------------------------------------

def combine(a: dict | None, b: dict | None, key: str) -> tuple[str, str]:
    """(outcome, reason) for one field from two referees.

    correct / wrong / unknown, and unknown always says which kind it is.
    """
    if a is None or b is None:
        return "unknown", "no_verdict"
    first = a["fields"][key]["verdict"]
    second = b["fields"][key]["verdict"]
    if first == "not_stated" or second == "not_stated":
        return "unknown", "not_stated_in_source"
    if first == second:
        return first, ""
    return "unknown", "referees_disagree"


def grade_row(item: dict, row: dict, *, start_usd: float, ceiling: float,
              fetch=None, wayback=None, call=None) -> dict:
    """One sampled row: fetch the body, ask both referees, combine.

    An unreadable body is UNKNOWN for all six fields and spends NOTHING — the
    fetch happens first and the referees are never asked about an empty page.
    That ordering is deliberate: the adjudicator learned it the expensive way,
    when two referees "agreed" to reject a figure neither had seen because the
    evidence was 185 characters of a Wayback loading screen.
    """
    evidence, used = adj.fetch_evidence(
        {"archive_url": item.get("archive_url") or "", "row": {"source_url": item["source_url"]}},
        fetch=fetch or adj.fetch_page, wayback=wayback or adj.wayback_copy)
    record = {
        "content_hash": item["content_hash"],
        "region": item["region"],
        "event_type": item["event_type"],
        "likely_non_english": item["likely_non_english"],
        "evidence_url": used,
        "evidence_chars": len(evidence),
        "cost_usd": 0.0,
        "referees": {},
        "parse_failures": [],
        "fields": {},
    }
    if not evidence:
        record["skipped"] = "no readable body"
        record["fields"] = {key: {"outcome": "unknown", "reason": "no_evidence"}
                            for key in FIELD_KEYS}
        return record

    prompt = build_prompt(row, evidence)
    answers: list[dict | None] = []
    for model in REFEREES:
        verdict, reason, cost = ask(model, prompt, start_usd=start_usd,
                                    ceiling=ceiling, call=call)
        record["cost_usd"] = round(record["cost_usd"] + cost, 6)
        record["referees"][model] = {
            "answered": verdict is not None,
            "reason": reason,
            "language": (verdict or {}).get("source_language", ""),
            "fields": (verdict or {}).get("fields", {}),
        }
        if verdict is None:
            record["parse_failures"].append({"model": model, "reason": reason})
        answers.append(verdict)

    languages = [(v or {}).get("source_language", "") for v in answers]
    record["language"] = languages[0] if languages[0] and languages[0] == languages[1] else (
        "disagree" if all(languages) else "unknown")
    for key in FIELD_KEYS:
        outcome, reason = combine(answers[0], answers[1], key)
        entry = {"outcome": outcome, "reason": reason, "stored": stored_view(row).get(key)}
        if answers[0] and answers[1]:
            entry["source_values"] = [answers[i]["fields"][key]["source_value"] for i in (0, 1)]
            entry["why"] = answers[0]["fields"][key]["why"]
        record["fields"][key] = entry
    return record


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def tally(records: list[dict], predicate=None) -> dict[str, dict]:
    """Per field: correct, wrong, unknown (by reason), rate and interval."""
    chosen = [r for r in records if predicate is None or predicate(r)]
    out: dict[str, dict] = {}
    for key, label in FIELDS:
        correct = wrong = 0
        reasons: dict[str, int] = {}
        for record in chosen:
            entry = record["fields"].get(key) or {}
            outcome = entry.get("outcome")
            if outcome == "correct":
                correct += 1
            elif outcome == "wrong":
                wrong += 1
            else:
                reason = entry.get("reason") or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
        judged = correct + wrong
        low, high = wilson(correct, judged) if judged else (None, None)
        out[key] = {
            "label": label,
            "correct": correct,
            "wrong": wrong,
            "judged": judged,
            "unknown": sum(reasons.values()),
            "unknown_by_reason": dict(sorted(reasons.items())),
            "accuracy": (correct / judged) if judged else None,
            "interval": [low, high] if judged else None,
        }
    return out


def summarise(sample: dict, records: list[dict], spend: float,
              ceiling: float, estimated: float | None) -> dict:
    by_region = {region: tally(records, lambda r, region=region: r["region"] == region)
                 for region in frame.REGIONS}
    by_event = {event: tally(records, lambda r, event=event: r["event_type"] == event)
                for event in frame.EVENT_TYPES}
    languages = _counts(records, lambda r: r.get("language") or "unknown")
    return {
        "measured_on": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sample": {"drawn_on": sample.get("drawn_on"), "seed": sample.get("seed"),
                   "items": len(sample.get("items") or []), "graded": len(records)},
        "referees": list(REFEREES),
        "extraction_model_at_measurement": classify.MODEL,
        "evidence": {
            "read": sum(1 for r in records if r.get("evidence_chars")),
            "unreadable": sum(1 for r in records if not r.get("evidence_chars")),
            "median_chars": _median([r.get("evidence_chars", 0) for r in records]),
        },
        "parse_failures": sum(len(r.get("parse_failures") or []) for r in records),
        "parse_failures_by_model": _counts(
            [pf for r in records for pf in (r.get("parse_failures") or [])],
            lambda pf: pf["model"]),
        "language_measured": languages,
        "overall": tally(records),
        "by_region": by_region,
        "by_event_type": by_event,
        "spend": {"usd": round(spend, 6), "ceiling_usd": ceiling,
                  "estimated_usd": estimated, "pot": budget.DISCRETIONARY},
    }


def _median(values: list[int]) -> int:
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) // 2


def render(summary: dict) -> str:
    lines = []
    sample = summary["sample"]
    lines.append("=" * 72)
    lines.append("EXTRACTION ACCURACY, AGAINST THE BODIES")
    lines.append("=" * 72)
    lines.append(f"  sample     {sample['graded']} of {sample['items']} drawn "
                 f"{sample['drawn_on']} (seed {sample['seed'] or 'none'})")
    lines.append(f"  referees   {' + '.join(summary['referees'])}")
    lines.append(f"  extraction {summary['extraction_model_at_measurement']} "
                 "(the model that wrote these rows is whatever was live when each was stored)")
    evidence = summary["evidence"]
    lines.append(f"  bodies     {evidence['read']} read, {evidence['unreadable']} unreadable "
                 f"(median {evidence['median_chars']} characters)")
    lines.append(f"  parse      {summary['parse_failures']} referee answer(s) did not parse; "
                 "counted apart from wrong answers and never in a denominator")
    lines.append(f"  languages  {summary['language_measured']}")
    lines.append("")
    lines.append(f"  {'field':<12}{'correct':>9}{'wrong':>7}{'judged':>8}"
                 f"{'unknown':>9}  accuracy (95% Wilson)")
    for key, _label in FIELDS:
        row = summary["overall"][key]
        if row["judged"]:
            rate = (f"{row['accuracy'] * 100:5.1f}%  "
                    f"({row['interval'][0] * 100:.1f} - {row['interval'][1] * 100:.1f})")
        else:
            rate = "UNKNOWN: nothing judged"
        lines.append(f"  {row['label']:<12}{row['correct']:>9}{row['wrong']:>7}"
                     f"{row['judged']:>8}{row['unknown']:>9}  {rate}")
    lines.append("")
    for heading, block in (("by region", summary["by_region"]),
                           ("by event type", summary["by_event_type"])):
        lines.append(f"  {heading}")
        for stratum, fields in block.items():
            parts = []
            for key, _label in FIELDS:
                row = fields[key]
                parts.append(f"{row['label']} "
                             + (f"{row['accuracy'] * 100:.0f}% ({row['judged']})"
                                if row["judged"] else "UNKNOWN(0)"))
            lines.append(f"    {stratum:<18} " + ", ".join(parts))
    lines.append("")
    spend = summary["spend"]
    estimated = ("UNKNOWN" if spend["estimated_usd"] is None
                 else f"${spend['estimated_usd']:.2f}")
    lines.append(f"  spend      ${spend['usd']:.4f} of a ${spend['ceiling_usd']:.2f} ceiling "
                 f"(estimated {estimated}), charged to {spend['pot']}")
    lines.append("")
    lines.append("  UNKNOWN is not a pass and not a failure. The denominator is")
    lines.append("  correct + wrong; every unknown is listed by reason in the result file.")
    return "\n".join(lines)


def incomplete(summary: dict) -> list[str]:
    """Strata where nothing could be judged. Exit 2, so a session reads it."""
    gaps = []
    for heading, block in (("region", summary["by_region"]),
                           ("event type", summary["by_event_type"])):
        for stratum, fields in block.items():
            for key, label in FIELDS:
                if not fields[key]["judged"]:
                    gaps.append(f"{heading} {stratum}: {label} judged nothing")
    for key, label in FIELDS:
        if not summary["overall"][key]["judged"]:
            gaps.append(f"overall: {label} judged nothing")
    return gaps


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def latest(pattern: str, directory: Path | None = None) -> Path | None:
    found = sorted((directory or OUT_DIR).glob(pattern))
    return found[-1] if found else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draw", action="store_true", help="draw a sample and write it (offline, free)")
    ap.add_argument("--estimate", action="store_true", help="price the run and stop (free)")
    ap.add_argument("--grade", action="store_true", help="fetch the bodies and ask the referees (paid)")
    ap.add_argument("--report", action="store_true", help="re-render the latest result (offline, free)")
    ap.add_argument("--sample", default="", help="sample file (default: the newest)")
    ap.add_argument("--result", default="", help="result file (default: the newest)")
    ap.add_argument("--target", type=int, default=TARGET)
    ap.add_argument("--seed", default="", help="draw seed, recorded in the sample")
    ap.add_argument("--limit", type=int, default=0, help="grade only the first N (a smoke run)")
    ap.add_argument("--ceiling", type=float, default=DEFAULT_CEILING_USD,
                    help=f"this run's spend ceiling, USD (max {CEILING_MAX_USD:.2f})")
    ap.add_argument("--health", action="store_true",
                    help="file the run's spend as a priced source_health row")
    args = ap.parse_args(argv)
    if not (args.draw or args.estimate or args.grade or args.report):
        ap.error("give --draw, --estimate, --grade or --report")
    if args.ceiling <= 0 or args.ceiling > CEILING_MAX_USD:
        print(f"--ceiling must be between 0 and {CEILING_MAX_USD:.2f}: the owner "
              f"approved this measurement at no more than ${CEILING_MAX_USD:.2f} of the "
              "one-time discretionary grant", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.draw:
        conn = schema.connect()
        rows = frame_rows(conn)
        cell_sizes = {}
        for row in rows:
            cell_sizes[frame.cell_of(row)] = cell_sizes.get(frame.cell_of(row), 0) + 1
        drawn = draw(rows, target=args.target, seed=args.seed)
        record = sample_record(drawn, args.seed, len(rows), cell_sizes)
        path = Path(args.sample) if args.sample else (
            OUT_DIR / f"sample-{record['drawn_on']}.json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(f"frame {len(rows)} rows; drew {len(drawn)} into {path}")
        print(f"  cells    {record['drawn_cells']}")
        print(f"  language {record['drawn_language_proxy']} (proxy at draw time)")
        if not args.estimate and not args.grade:
            return 0

    sample_path = Path(args.sample) if args.sample else latest("sample-*.json")
    if not sample_path or not sample_path.exists():
        if args.report:
            pass
        else:
            print("no sample to work from; run --draw first", file=sys.stderr)
            return 1
    sample = json.loads(sample_path.read_text()) if sample_path and sample_path.exists() else {}
    items = (sample.get("items") or [])[: args.limit or None]

    estimated = None
    if args.estimate or args.grade:
        rules = field_rules()
        probe = build_prompt({"headline": "x"}, "x" * EVIDENCE_CAP, rules)
        estimated, lines = estimate(len(items), len(probe), prices())
        print(f"PRICE BEFORE THE RUN: {len(items)} item(s), "
              f"{len(probe)} prompt characters each")
        for line in lines:
            print(line)
        if estimated is None:
            print("  total: UNKNOWN. A run nobody can price is one nobody can approve.")
        else:
            print(f"  total: ${estimated:.2f} against a ${args.ceiling:.2f} ceiling "
                  f"and a ${CEILING_MAX_USD:.2f} hard bar")
        print("  " + budget.status_line())
        if args.estimate and not args.grade:
            return 0
        if estimated is not None and estimated > args.ceiling:
            print(f"REFUSING: the estimate ${estimated:.2f} is past this run's "
                  f"${args.ceiling:.2f} ceiling. Raise the ceiling deliberately "
                  "(up to the hard bar) or draw a smaller sample.", file=sys.stderr)
            return 1

    if args.grade:
        if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
            print("OPENROUTER_API_KEY is not set; the referees cannot be asked.\n"
                  "This repo's key lives in Actions. Queue the run:\n"
                  "  gh workflow run drain-writers.yml -f enqueue=extraction-benchmark.yml \\\n"
                  "     -f inputs_json='{\"ceiling\":\"2.00\"}' -f reason='...'",
                  file=sys.stderr)
            return 1
        start_usd = float(classify.STATS.get("usd", 0.0))
        dead = preflight(start_usd=start_usd, ceiling=args.ceiling)
        if dead:
            print("REFUSING: a referee gave no answer to a one-line question, so the "
                  "sample is not graded and nothing more is spent. Fix the referee "
                  "(or name another with BENCH_REFEREE_A/B) and queue the run again:",
                  file=sys.stderr)
            for model, reason in dead.items():
                print(f"  {model}: {reason}", file=sys.stderr)
            return 1
        conn = schema.connect()
        by_hash = {}
        for item in items:
            row = conn.execute(
                "SELECT * FROM signals WHERE content_hash = ? AND is_current = 1",
                (item["content_hash"],)).fetchone()
            by_hash[item["content_hash"]] = dict(row) if row else None

        records: list[dict] = []
        stopped, budget_stopped = "", False
        for index, item in enumerate(items, 1):
            row = by_hash.get(item["content_hash"])
            if row is None:
                records.append({
                    "content_hash": item["content_hash"], "region": item["region"],
                    "event_type": item["event_type"],
                    "likely_non_english": item["likely_non_english"],
                    "evidence_chars": 0, "cost_usd": 0.0, "referees": {},
                    "parse_failures": [], "skipped": "row is no longer current",
                    "fields": {key: {"outcome": "unknown", "reason": "row_not_current"}
                               for key in FIELD_KEYS}})
                continue
            try:
                record = grade_row(item, row, start_usd=start_usd, ceiling=args.ceiling)
            except adj.BudgetStop as stop:
                stopped, budget_stopped = str(stop), True
                print(f"\nBUDGET STOP after {index - 1} row(s): {stopped}")
                print("UNDECIDED, not a verdict: the rows not reached are not "
                      "graded and are not counted against anything.")
                break
            records.append(record)
            brake = referee_to_stop(records)
            if brake:
                model, failed, readable = brake
                stopped = (f"{model} failed {failed} of {readable} readable row(s); "
                           "the rest of the sample was not bought")
                print(f"\n  [{index}/{len(items)}] {item['region']}/{item['event_type']} "
                      f"{record['evidence_chars']}ch")
                print(f"\nREFEREE STOP: {stopped}")
                print("The rows already graded stand; what was not reached is not "
                      "a finding. Read the parse failures in the result file: they "
                      "carry the answer the referee gave.")
                break
            print(f"  [{index}/{len(items)}] {item['region']}/{item['event_type']} "
                  f"{record['evidence_chars']}ch "
                  + ", ".join(f"{label}:{record['fields'][key]['outcome'][:4]}"
                              for key, label in FIELDS), flush=True)

        spend = float(classify.STATS.get("usd", 0.0)) - start_usd
        summary = summarise(sample, records, spend, args.ceiling, estimated)
        if stopped and budget_stopped:
            summary["budget_stop"] = stopped
        elif stopped:
            summary["referee_stop"] = stopped
        summary["records"] = records
        result_path = Path(args.result) if args.result else (
            OUT_DIR / f"result-{_dt.date.today().isoformat()}.json")
        result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print()
        print(render(summary))
        print(f"\nwritten to {result_path}")
        if args.health:
            store.report_health(
                conn, HEALTH_NAME, status="ok", items_found=len(items),
                items_stored=len(records),
                detail=f"body-based extraction benchmark, {len(records)} row(s) graded",
                usage={"model": " + ".join(REFEREES), "cost_usd": round(spend, 6),
                       "reads_bought": 2 * len(records)})
            conn.commit()
        if stopped and budget_stopped:
            # A budget stop is UNDECIDED, never a verdict, and never a red run.
            # Every field it left ungraded would otherwise read as "judged
            # nothing" and redden the workflow for the budget working.
            print("\nUNDECIDED: the run stopped on its ceiling, so what is not "
                  "graded is not a finding. Re-queue with a higher ceiling "
                  f"(hard bar ${CEILING_MAX_USD:.2f}) or a smaller --limit.")
            return 0
        gaps = incomplete(summary)
        if gaps:
            print("\nINCOMPLETE, so a human reads it:")
            for gap in gaps:
                print(f"  {gap}")
            return 2
        return 0

    if args.report:
        path = Path(args.result) if args.result else latest("result-*.json")
        if not path or not path.exists():
            print("no result to report; run --grade first", file=sys.stderr)
            return 1
        summary = json.loads(path.read_text())
        print(render(summary))
        return 2 if incomplete(summary) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

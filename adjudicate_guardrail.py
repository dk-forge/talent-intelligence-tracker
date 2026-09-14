#!/usr/bin/env python3
"""Answer a held publish-guardrail finding with two independent AI referees.

THE RULE THIS IMPLEMENTS (owner, 2026-09-11). A data decision that used to wait
for the owner is put to two referees from different vendors, each reading the
row and its own cited source under the tracker's written money rules. It is
applied only when both return the same verdict. A disagreement is written down
and left for a human; nothing is changed. Unreadable evidence is UNKNOWN, and
UNKNOWN never applies anything, because absence of evidence is not evidence.
The owner hears only about the disagreements.

WHY TWO VENDORS. One model reading a row is one confident opinion. Two models
that share no weights and still agree is the evidence bar the owner accepts
for a published figure, and it is cheap: two short calls, a few cents, metered
by OpenRouter's own usage accounting like every other paid read here.

WHAT IT NEVER DOES. It never edits the ledger by hand: accept and reject go
through `guardrails.review()`, the same function `guardrails.py --accept` and
`--reject` call, so the note, the reviewer and the shared-note guard all apply.
An `edit` verdict (the source states a DIFFERENT amount or basis for this
event) appends a revision through `store.revise()`, the path
`correct_funding_amount.py` uses, pushes it through `/enrich` only if the row
is already live, and then accepts the finding so the corrected figure is what
publishes. It never prints a key. It never retries inside a paid call: the
retry is the outer `ATTEMPTS` loop, and the run ceiling is read before every
attempt.

USAGE
    OPENROUTER_API_KEY=... .venv/bin/python adjudicate_guardrail.py \\
        --key amount/<content_hash> [--key ...]            # dry run: verdicts only
    ... --apply                                            # apply on agreement
    .venv/bin/python adjudicate_guardrail.py --from-spec analysis/adjudications/<file>.json --apply
                                                           # re-apply an agreed spec, no model
    ... --row <content_hash> --reason 'why'                # a live row no check caught (money)
    ... --place-row <content_hash> --reason 'why'          # a live row whose location looks wrong

A ROW NO CHECK CAUGHT is put to the same two referees. `--row` and
`--place-row` open a finding in the guardrails ledger first (`amount/<hash>`
or `place/<hash>`, detail "raised by a session: <reason>"), through
`guardrails.record` with an empty check list so no other open finding is
resolved by the write, and then adjudicate it exactly as a check-raised one.
Nothing is ever changed without two agreeing verdicts. A `place` finding is
inert for publishing (it is not a ROW_CHECK, so it never holds a row back and
never goes overdue); its only consequence is the correction the referees
agree on, applied through `correct_city_country.reissue`, the door the
gazetteer correction already uses. In a dry run the findings opened by the run
are removed again at the end, so a dry run leaves the ledger as it found it.

Every run writes analysis/adjudications/<date>-<check>-<subject>.json with
both verdicts verbatim, the spend, and the outcome.

Exit codes: 0 every key agreed (applied, or dry run); 3 at least one key is
UNKNOWN or the referees disagreed (spec written, nothing applied for it);
1 a hard failure (no such finding, no key, bad arguments).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import requests  # noqa: E402

import archive_sources  # noqa: E402
import correct_city_country  # noqa: E402
import correct_funding_amount  # noqa: E402
from pipeline import classify, guardrails, money_raised, schema, store, validate, vocab  # noqa: E402

REFEREES = (
    os.environ.get("ADJ_REFEREE_A", "anthropic/claude-sonnet-4.5"),
    os.environ.get("ADJ_REFEREE_B", "openai/gpt-4o"),
)
#: What one run may spend across every key and every attempt, in USD, read
#: from OpenRouter's own cost figure (classify.STATS["usd"]) before each call.
RUN_CEILING_USD = 0.10
#: Attempts per referee. The retry lives HERE, never inside the paid call.
ATTEMPTS = 2
EVIDENCE_CAP = 12_000
#: Below this many characters a read is a stub, a bot wall or an interstitial,
#: not the article: the Mistral row's first pass read 185 characters of a
#: Wayback loading page and two referees "agreed" to reject a figure neither
#: had seen. A thin read tries the next copy; no copy clearing it is UNKNOWN.
EVIDENCE_MIN_CHARS = 800
#: A referee that answers below this is saying it could not see enough. Two
#: such answers are not an agreement; they are two reports of blindness.
CONFIDENCE_FLOOR = 50
#: How much of the raw page is stripped. CNBC carries ~600k of CSS before the
#: body, so the cap is on the RAW after stripping, never before it.
RAW_CAP = 3_000_000
FETCH_TIMEOUT = 15
MAX_TOKENS = 700
CALL_TIMEOUT = 90
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
              "TalentIntel/1.0 (+https://asktherecruiter.com)")
SPEC_DIR = REPO / "analysis" / "adjudications"
ACTIONS = ("accept", "reject", "edit")
BASIS_VOCAB = (money_raised.COMPANY_RAISE,) + tuple(sorted(money_raised.EXCLUDING_DEAL_TYPES))
WHO = f"two-model adjudication ({REFEREES[0]} + {REFEREES[1]})"

#: The second question the referees can be asked: where the row is. Not one of
#: guardrails.CHECKS, on purpose: a `place` finding must never hold a row back
#: or go overdue, because nothing automated raises one.
PLACE = "place"
#: The detail prefix on a finding a session opened by hand, so `--withheld`
#: and the digest can tell it from one a check raised.
SESSION_LABEL = "raised by a session"
#: The name the spend row is filed under in source_health. Discretionary.
HEALTH_NAME = "adjudicate_rows"


class BudgetStop(RuntimeError):
    """The run ceiling or the month's allowance binds. UNDECIDED, never a verdict."""


RULES = f"""The tracker's written money rules, which you must apply literally:
1. The public "money raised" total sums ONLY rows whose money_basis is
   "company_raise": new capital that ARRIVED at the named employer from private
   investors or venture lenders (a priced equity round, venture debt, a
   convertible note).
2. Every other basis is stored but NOT summed. The vocabulary is exactly:
   {", ".join(BASIS_VOCAB)}.
   ipo and public_offering: equity sold into public markets.
   bond_issue: bonds, notes, debentures. project_finance: a loan or bank
   facility advanced against an asset or a project (a syndicated bank loan is
   here, a venture lender is not). fund_raise: an investor closing its own fund
   or vehicle. outbound_investment: THIS employer is the one paying.
   state_funding: a subsidy, grant or appropriation. pledge: announced, in talks,
   or sought, but not received. acquisition, acquired, merger, divestiture,
   joint_venture: a transaction price, not a raise.
3. A VALUATION IS NOT A RAISE. "valued at $X after new funding" states what the
   company is worth; the raise is the amount actually invested, if the source
   states it at all. A round that is being sought or discussed is a pledge.
4. The stored amount must be a figure the source states for THIS company and
   THIS event, in the currency the source uses. A figure the source does not
   state, a figure that belongs to another company or an earlier event, or a
   misread currency or multiplier, is wrong.
5. "recommended" means: accept = the stored amount is stated for this event and
   its basis really is company_raise; reject = the source states no usable
   funding figure for this company and event, so the row must be withheld;
   edit = the source states a DIFFERENT amount (give corrected_amount as an
   integer in USD) or a different basis (give corrected_basis from the
   vocabulary) for this same event. For edit, give both fields; repeat the
   stored value for the one that does not change.
"""

PROMPT = """You are one of two independent referees adjudicating a funding figure a public
talent-market tracker has quarantined before publication. The other referee is a
model from a different vendor; you will not see its answer. Be strict and
literal. Decide only from the row and the source text below. If the source text
is empty, unrelated, or does not mention this company, say so and answer
"reject" with confidence 0.

{rules}

THE GUARDRAIL FINDING:
{finding}

THE STORED ROW:
{row}

THE CITED SOURCE (text only, may be truncated):
<<<
{evidence}
>>>

Answer with ONE JSON object and nothing else:
{{"amount_stated_for_this_event": true|false,
  "money_basis": "<one of the vocabulary>|unknown",
  "recommended": "accept"|"reject"|"edit",
  "corrected_amount": <integer USD>|null,
  "corrected_basis": "<one of the vocabulary>"|null,
  "confidence": 0-100,
  "reasoning": "at most 600 characters, quoting the sentence in the source that decides it"}}
"""

PLACE_RULES = """The tracker's written location rules, which you must apply literally:
1. `city` and `country` on a row are WHERE THE ROLES OR THE ACTIVITY IN THE
   STORY ARE LOCATED, as the cited source states it. Not where the outlet is
   published, not the reporter's dateline, and not the employer's headquarters
   unless the story places the activity there.
2. `country` is an ISO 3166-1 alpha-2 code (US, AR, GB, VN ...). Puerto Rico
   is PR, not US. Hong Kong is HK. A city that exists in several countries
   (San Juan, Cordoba, Cambridge, London) takes the country the SOURCE places
   it in; a same-named city in another country is wrong, however well known.
3. `city` is the city named by the source for the activity, spelled as the
   source spells it in Latin script, or null when the source names a country
   or a province but no city.
4. A source that states no location for this employer's activity leaves the
   row's location unverifiable: answer "reject". Do not infer a location from
   the employer's name or from the outlet.
5. "recommended" means: accept = the stored city and country are what the
   source states; edit = the source places the activity somewhere else (give
   corrected_city and corrected_country, repeating the stored value for the
   one that does not change); reject = the source states no usable location
   for this row.
"""

PLACE_PROMPT = """You are one of two independent referees adjudicating WHERE a public
talent-market tracker has filed one record. The other referee is a model from a
different vendor; you will not see its answer. Be strict and literal. Decide
only from the row and the source text below. If the source text is empty,
unrelated, or does not mention this employer, say so and answer "reject" with
confidence 0.

{rules}

THE FINDING:
{finding}

THE STORED ROW:
{row}

THE CITED SOURCE (text only, may be truncated):
<<<
{evidence}
>>>

Answer with ONE JSON object and nothing else:
{{"location_stated": true|false,
  "recommended": "accept"|"reject"|"edit",
  "corrected_city": "<city>"|null,
  "corrected_country": "<ISO 3166-1 alpha-2>"|null,
  "confidence": 0-100,
  "reasoning": "at most 600 characters, quoting the sentence in the source that decides it"}}
"""


# --------------------------------------------------------------------------
# The finding, its row, and its evidence
# --------------------------------------------------------------------------

def load_finding(conn, key: str) -> dict | None:
    """The ledger row, the signal it is about, and any recorded archive copy.

    Read the way guardrails.py reads them: the ledger by (check_name, subject),
    the row by content_hash at its current revision, the Wayback permalink from
    source_links. None when the key names nothing.
    """
    check, _, subject = key.partition("/")
    if not check or not subject:
        return None
    finding = conn.execute(
        "SELECT * FROM publish_guardrails WHERE check_name = ? AND subject = ?",
        (check, subject)).fetchone()
    if finding is None:
        return None
    row = conn.execute(
        "SELECT * FROM signals WHERE content_hash = ? AND is_current = 1",
        (subject,)).fetchone()
    archive_url = None
    if row is not None and row["source_url"]:
        try:
            link = conn.execute(
                "SELECT archive_url FROM source_links WHERE source_url = ?",
                (row["source_url"],)).fetchone()
            archive_url = link["archive_url"] if link else None
        except Exception:  # noqa: BLE001 - a checkout without the cache file
            archive_url = None
    return {"key": key, "finding": dict(finding),
            "row": dict(row) if row is not None else None,
            "archive_url": archive_url}


def row_view(row: dict | None) -> dict:
    keep = ("company", "headline", "summary", "funding_amount", "funding_amount_usd",
            "funding_stage", "deal_type", "money_basis", "source_url", "source_name",
            "published_date", "collector", "city", "region", "state", "country",
            "hq_city", "hq_country", "published_at")
    return {k: row.get(k) for k in keep} if row else {}


def finding_view(finding: dict) -> dict:
    return {k: finding.get(k) for k in ("check_name", "label", "detail", "value",
                                        "first_seen", "seen")}


def open_session_finding(conn, check: str, content_hash: str, reason: str) -> dict:
    """Open `<check>/<content_hash>` for a live row no automated check caught.

    Through `guardrails.record` with an EMPTY check list, so the write can
    resolve nothing else: `record()` marks every open finding of each listed
    check that did not fire this pass as resolved, and a session raising one
    row must not close the amount queue behind it. Returns {"key", "opened",
    "state"}; an existing finding is left in whatever state it is in.
    """
    if check not in (guardrails.AMOUNT, PLACE):
        raise ValueError(f"a session may raise {guardrails.AMOUNT!r} or {PLACE!r}, not {check!r}")
    key = f"{check}/{content_hash}"
    row = conn.execute(
        "SELECT company, funding_amount_usd, city, country FROM signals "
        " WHERE content_hash = ? AND is_current = 1", (content_hash,)).fetchone()
    if row is None:
        raise ValueError(f"{key}: no current row carries this content_hash")
    existing = conn.execute(
        "SELECT state FROM publish_guardrails WHERE check_name = ? AND subject = ?",
        (check, content_hash)).fetchone()
    if existing is not None:
        return {"key": key, "opened": False, "state": existing["state"]}
    if check == guardrails.AMOUNT:
        label = f"{row['company']} ${int(row['funding_amount_usd'] or 0):,}"
        value = float(row["funding_amount_usd"] or 0) or None
    else:
        label = f"{row['company']} filed under {row['city'] or '?'}, {row['country'] or '?'}"
        value = None
    finding = guardrails.Finding(check, content_hash, label,
                                 f"{SESSION_LABEL}: {reason}", value)
    guardrails.record(conn, [finding], checks=())
    return {"key": key, "opened": True, "state": "open"}


def drop_session_finding(conn, key: str) -> int:
    """Remove a finding THIS RUN opened and did not decide (a dry run's
    cleanup). Refuses anything a person or a check wrote: only an open finding
    whose detail carries SESSION_LABEL, and only under a key the caller opened."""
    check, _, subject = key.partition("/")
    cur = conn.execute(
        "DELETE FROM publish_guardrails WHERE check_name = ? AND subject = ? "
        "  AND state = 'open' AND detail LIKE ?",
        (check, subject, f"{SESSION_LABEL}: %"))
    conn.commit()
    return cur.rowcount


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    return _html.unescape(re.sub(r"\s+", " ", text)).strip()


def fetch_page(url: str, timeout: int = FETCH_TIMEOUT) -> str:
    """Text of one page with a browser UA, or '' when unreadable. Never raises."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=timeout)
        if resp.status_code != 200:
            print(f"  evidence: HTTP {resp.status_code} from {url[:80]}")
            return ""
        return strip_html(resp.text[:RAW_CAP])[:EVIDENCE_CAP]
    except Exception as exc:  # noqa: BLE001 - any failure is "unreadable", reported not raised
        print(f"  evidence: could not read {url[:80]} ({type(exc).__name__})")
        return ""


def wayback_copy(url: str) -> str | None:
    """A Wayback permalink from the availability API, or None. Never raises."""
    if not url:
        return None
    found = archive_sources.check_availability(url, requests.Session())
    if not found or found == archive_sources.RATE_LIMITED:
        return None
    return found


def fetch_evidence(item: dict, *, fetch=fetch_page, wayback=wayback_copy) -> tuple[str, str]:
    """(text, url_used). Archived copy first, then the live page, then Wayback.

    A bot wall on the publisher (invezz.com answers 403 to anything that is not
    a browser session) is why the third step exists; a 429 from archive.org is
    "we do not know", and the result is then honestly empty. A read shorter
    than EVIDENCE_MIN_CHARS is treated as no read: the next copy is tried, and
    when none clears the floor the answer is "" and nothing is spent.
    """
    row = item.get("row") or {}
    source_url = row.get("source_url") or ""
    candidates = [u for u in (item.get("archive_url"), source_url) if u]
    snapshot = None
    for url in candidates:
        text = fetch(url)
        if len(text) >= EVIDENCE_MIN_CHARS:
            return text, url
        if text:
            print(f"  evidence: only {len(text)} characters from {url[:80]}; trying the next copy")
    snapshot = wayback(source_url)
    if snapshot:
        text = fetch(snapshot)
        if len(text) >= EVIDENCE_MIN_CHARS:
            return text, snapshot
        if text:
            print(f"  evidence: only {len(text)} characters from the Wayback copy")
    return "", ""


# --------------------------------------------------------------------------
# The referees, metered
# --------------------------------------------------------------------------

def _spent_so_far(start_usd: float) -> float:
    return float(classify.STATS.get("usd", 0.0)) - start_usd


def _gate(start_usd: float, ceiling: float | None = None) -> None:
    """Read before EVERY paid request: the month's switch, then this run's ceiling."""
    if not classify.paid_reads_enabled():
        raise BudgetStop("TIT_PAID_READS is off: the month's allowance is spent")
    limit = RUN_CEILING_USD if ceiling is None else float(ceiling)
    spent = _spent_so_far(start_usd)
    if spent >= limit:
        raise BudgetStop(f"run ceiling ${limit:.2f} reached (${spent:.4f} spent)")


def parse_verdict(content: str) -> dict | None:
    """A verdict dict, or None when the answer is not the JSON that was asked for."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", content or "", re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
    if not isinstance(parsed, dict) or parsed.get("recommended") not in ACTIONS:
        return None
    try:
        confidence = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < CONFIDENCE_FLOOR:
        # The prompt tells a referee that cannot see the source to answer
        # "reject" at confidence 0. That is a report, not a verdict.
        return None
    basis = parsed.get("corrected_basis")
    if basis is not None and basis not in BASIS_VOCAB:
        parsed["corrected_basis"] = None
    amount = parsed.get("corrected_amount")
    if amount is not None:
        try:
            parsed["corrected_amount"] = int(round(float(amount)))
        except (TypeError, ValueError):
            parsed["corrected_amount"] = None
    return parsed


_ISO2 = re.compile(r"^[A-Z]{2}$")


def parse_place_verdict(content: str) -> dict | None:
    """A place verdict, or None. Same floor as parse_verdict: a low-confidence
    answer is a report of blindness, not a verdict. The country is upper-cased
    and must be two letters; anything else is dropped to null so a free-text
    country can never be written to a row."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", content or "", re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
    if not isinstance(parsed, dict) or parsed.get("recommended") not in ACTIONS:
        return None
    try:
        confidence = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < CONFIDENCE_FLOOR:
        return None
    country = parsed.get("corrected_country")
    country = country.strip().upper() if isinstance(country, str) else None
    parsed["corrected_country"] = country if country and _ISO2.match(country) else None
    city = parsed.get("corrected_city")
    parsed["corrected_city"] = city.strip() if isinstance(city, str) and city.strip() else None
    return parsed


def ask_referee(model: str, prompt: str, *, start_usd: float,
                call=None, attempts: int = ATTEMPTS, parse=parse_verdict,
                ceiling: float | None = None) -> tuple[dict | None, float]:
    """One verdict from one model: (verdict or None, this referee's cost).

    `call` performs exactly ONE request (classify._call, the repo's one door to
    OpenRouter). The gate is read immediately before each attempt and the cost
    is metered immediately after, so a caller cannot spend without checking.
    A non-JSON answer is a referee failure (None), not a verdict.
    """
    call = call or classify._call
    before = float(classify.STATS.get("usd", 0.0))
    content = None
    last_error: Exception | None = None
    for _attempt in range(attempts):
        _gate(start_usd, ceiling)
        try:
            content = call(model, classify.MINI_SYSTEM, prompt,
                           timeout=CALL_TIMEOUT, max_tokens=MAX_TOKENS, json_mode=True)
            break
        except (classify.Throttled, classify.ClassifyError) as exc:
            last_error = exc
            content = None
    cost = float(classify.STATS.get("usd", 0.0)) - before
    if content is None:
        print(f"  {model}: no answer ({type(last_error).__name__ if last_error else 'empty'})")
        return None, cost
    verdict = parse(content)
    if verdict is None:
        print(f"  {model}: answer is not a usable verdict (not the JSON asked for, "
              f"or confidence below {CONFIDENCE_FLOOR}): {content[:200]!r}")
    return verdict, cost


# --------------------------------------------------------------------------
# The agreement rule
# --------------------------------------------------------------------------

def decide(verdicts: dict[str, dict | None]) -> tuple[str, str, dict | None]:
    """(status, action, correction). status is 'agree', 'disagree' or 'unknown'.

    Two verdicts are needed; a missing one is UNKNOWN, not a tie-break. An
    `edit` agrees only when both name the same corrected amount and basis.
    """
    answered = {m: v for m, v in verdicts.items() if v}
    if len(answered) < 2:
        return "unknown", "keep", None
    actions = {v["recommended"] for v in answered.values()}
    if len(actions) != 1:
        return "disagree", "keep", None
    action = actions.pop()
    if action != "edit":
        return "agree", action, None
    fixes = {(v.get("corrected_amount"), v.get("corrected_basis")) for v in answered.values()}
    if len(fixes) != 1:
        return "disagree", "keep", None
    amount, basis = fixes.pop()
    if amount is None and basis is None:
        return "disagree", "keep", None
    return "agree", "edit", {"corrected_amount": amount, "corrected_basis": basis}


def decide_place(verdicts: dict[str, dict | None]) -> tuple[str, str, dict | None]:
    """The agreement rule for a place finding. An `edit` agrees only when both
    referees name the same country and the same city (case-insensitive)."""
    answered = {m: v for m, v in verdicts.items() if v}
    if len(answered) < 2:
        return "unknown", "keep", None
    actions = {v["recommended"] for v in answered.values()}
    if len(actions) != 1:
        return "disagree", "keep", None
    action = actions.pop()
    if action != "edit":
        return "agree", action, None
    fixes = {((v.get("corrected_city") or "").casefold(), v.get("corrected_country"))
             for v in answered.values()}
    if len(fixes) != 1:
        return "disagree", "keep", None
    city_key, country = fixes.pop()
    if not country:
        return "disagree", "keep", None
    # Spelled the way the first referee spelled it; the two agree on the key.
    city = next((v.get("corrected_city") for v in answered.values()
                 if (v.get("corrected_city") or "").casefold() == city_key), None)
    return "agree", "edit", {"corrected_city": city or None, "corrected_country": country}


def deciding_note(key: str, action: str, verdicts: dict, correction: dict | None) -> str:
    """The ledger note: the verdict and each referee's deciding sentence.

    Unique per finding by construction (it carries the key), which is what
    `guardrails.review()`'s shared-note guard is there to check.
    """
    quotes = "; ".join(f"{m}: {(v.get('reasoning') or '')[:220]}"
                       for m, v in verdicts.items() if v)
    fix = ""
    if correction and "corrected_country" in correction:
        fix = (f" corrected_city={correction.get('corrected_city')} "
               f"corrected_country={correction.get('corrected_country')}.")
    elif correction:
        fix = (f" corrected_amount={correction.get('corrected_amount')} "
               f"corrected_basis={correction.get('corrected_basis')}.")
    return f"two-model adjudication of {key}, both referees say {action}.{fix} {quotes}"


# --------------------------------------------------------------------------
# Applying, through the machinery that already exists
# --------------------------------------------------------------------------

def _edited_signal(row: dict, correction: dict):
    amount = correction.get("corrected_amount")
    if amount is None:
        amount = row.get("funding_amount_usd")
    signal = correct_funding_amount.corrected_signal(row, amount)
    basis = correction.get("corrected_basis")
    if basis:
        signal.money_basis = basis
        if basis in money_raised.EXCLUDING_DEAL_TYPES:
            signal.deal_type = signal.deal_type or basis
    return signal


def apply_decision(conn, item: dict, action: str, correction: dict | None,
                   note: str, *, who: str = WHO, apply: bool = False,
                   push=None) -> int:
    """Apply one agreed verdict. Returns the number of ledger rows changed.

    accept / reject: `guardrails.review()`, exactly what the CLI calls.
    edit: a revision through `store.revise()` carrying the corrected figure and
    basis (pushed through /enrich first only when the row is already live, the
    order `correct_funding_amount.reissue` uses and for the same reason), then
    the finding is accepted so the corrected row is what publishes.
    """
    key = item["key"]
    if not apply:
        print(f"  dry run: would {action} {key}")
        return 0
    if action == "edit":
        row = item.get("row")
        if not row:
            raise RuntimeError(f"{key}: no current row to revise")
        signal = _edited_signal(row, correction or {})
        if row.get("published_at"):
            push = push or correct_funding_amount.push_amount
            push(row, signal.funding_amount_usd)
        store.revise(conn, row["signal_id"], signal,
                     f"funding figure adjudicated by {who}: {note[:400]}")
        if row.get("published_at"):
            conn.execute(
                "UPDATE signals SET published_at = ? WHERE signal_id = ? AND is_current = 1",
                (row["published_at"], row["signal_id"]))
        conn.commit()
        state = "accepted"
    else:
        state = "accepted" if action == "accept" else "rejected"
    return guardrails.review(conn, key, state, note, who)


def place_fix(row: dict, correction: dict) -> dict:
    """The geography fields an agreed place edit moves, mirroring what
    validate.build_signal would write for that city and country: the region
    from the country, the state facet only inside the US, hq untouched.

    The gazetteer is NOT consulted for the country: it is what filed the row
    wrongly in the first place (it holds one country per city name), and two
    referees reading the source outrank a table that cannot see the source.
    Where it disagrees the caller prints it, because that is a vocabulary
    decision for a person."""
    country = correction["corrected_country"]
    city = correction.get("corrected_city")
    fixed: dict = {}
    if country != row.get("country"):
        fixed["country"] = country
    if (city or None) != (row.get("city") or None):
        fixed["city"] = city
    region = validate._region_for_country(country)
    if region and region != row.get("region"):
        fixed["region"] = region
    state = vocab.state_for_city(city) if (country == "US" and city) else None
    if state != row.get("state"):
        fixed["state"] = state
    return fixed


def apply_place(conn, item: dict, action: str, correction: dict | None,
                note: str, *, who: str = WHO, apply: bool = False, push=None) -> int:
    """Apply one agreed place verdict. accept and reject close the finding
    through guardrails.review; edit goes through correct_city_country.reissue
    (the site first, then a revision) and then accepts the finding."""
    key = item["key"]
    if not apply:
        print(f"  dry run: would {action} {key}")
        return 0
    if action == "edit":
        row = item.get("row")
        if not row:
            raise RuntimeError(f"{key}: no current row to revise")
        fixed = place_fix(row, correction or {})
        if fixed:
            hit = vocab.normalize_city(fixed.get("city", row.get("city")) or "")
            if hit and hit[2] != (correction or {}).get("corrected_country"):
                print(f"  NOTE: the city gazetteer files {hit[0]!r} under {hit[2]!r}; "
                      f"the referees place this row in {correction['corrected_country']!r}. "
                      f"The row is corrected from the source; the table is a "
                      f"vocabulary decision for a person.")
            # Looked up at call time, never bound as a default, so a caller
            # that replaces the site door is honoured.
            correct_city_country.reissue(
                conn, row, fixed, push=push or correct_city_country.push_place,
                note=f"location adjudicated by {who}: {note[:400]}")
        state = "accepted"
    else:
        state = "accepted" if action == "accept" else "rejected"
    return guardrails.review(conn, key, state, note, who)


# --------------------------------------------------------------------------
# The spec file: both verdicts, the spend, the outcome
# --------------------------------------------------------------------------

def spec_path(key: str, spec_dir: Path = SPEC_DIR, today: _dt.date | None = None) -> Path:
    stamp = (today or _dt.date.today()).isoformat()
    return spec_dir / f"{stamp}-{key.replace('/', '-')}.json"


def write_spec(item: dict, status: str, verdicts: dict, extra: dict,
               spec_dir: Path = SPEC_DIR) -> Path:
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_path(item["key"], spec_dir)
    body = {
        "key": item["key"],
        "label": (item.get("finding") or {}).get("label"),
        "status": status,
        "referees": list(REFEREES),
        "verdicts": verdicts,
        "row": row_view(item.get("row")),
        **extra,
        "written_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


# --------------------------------------------------------------------------
# One key, end to end
# --------------------------------------------------------------------------

def adjudicate(conn, key: str, *, apply: bool, start_usd: float,
               call=None, fetch=fetch_page, wayback=wayback_copy,
               spec_dir: Path = SPEC_DIR, push=None, ceiling: float | None = None) -> int:
    """Returns 0 on agreement (applied or dry), 3 on UNKNOWN or disagreement, 1 on a hard failure."""
    item = load_finding(conn, key)
    if item is None:
        print(f"{key}: no such finding. Keys look like amount/<content_hash>.")
        return 1
    is_place = key.startswith(PLACE + "/")
    template, rules, parse, judge, act = (
        (PLACE_PROMPT, PLACE_RULES, parse_place_verdict, decide_place, apply_place)
        if is_place else (PROMPT, RULES, parse_verdict, decide, apply_decision))
    finding = item["finding"]
    print(f"\n{key}  [{finding.get('state')}]  {finding.get('label')}")
    if finding.get("state") != "open":
        print(f"  already {finding.get('state')}; nothing to adjudicate")
        return 0
    if not item["row"]:
        write_spec(item, "unknown", {}, {"why": "no current row carries this content_hash",
                                         "cost_usd": 0.0, "action": "keep"}, spec_dir)
        print("  UNKNOWN: no current row. Nothing applied.")
        return 3

    evidence, used = fetch_evidence(item, fetch=fetch, wayback=wayback)
    if not evidence:
        path = write_spec(item, "unknown", {}, {"why": "no evidence page could be read",
                                                "evidence_url": None, "cost_usd": 0.0,
                                                "action": "keep"}, spec_dir)
        print(f"  UNKNOWN: no evidence could be read. Nothing applied. Spec: {path}")
        return 3
    print(f"  evidence: {len(evidence)} characters from {used[:90]}")

    prompt = template.format(rules=rules,
                             finding=json.dumps(finding_view(finding), indent=1, ensure_ascii=False),
                             row=json.dumps(row_view(item["row"]), indent=1, ensure_ascii=False),
                             evidence=evidence)
    verdicts: dict[str, dict | None] = {}
    costs: dict[str, float] = {}
    for model in REFEREES:
        try:
            verdicts[model], costs[model] = ask_referee(
                model, prompt, start_usd=start_usd, call=call, parse=parse, ceiling=ceiling)
        except BudgetStop as exc:
            print(f"  {model}: budget stop ({exc}); UNDECIDED")
            verdicts[model], costs[model] = None, 0.0
        v = verdicts[model]
        print(f"  {model}: " + (json.dumps(v, ensure_ascii=False) if v else "no usable verdict"))

    status, action, correction = judge(verdicts)
    cost = round(sum(costs.values()), 6)
    extra = {"cost_usd": cost, "cost_by_referee": costs, "evidence_url": used,
             "evidence_chars": len(evidence), "action": action, "correction": correction}
    if action == "reject" and item["row"].get("published_at") and not is_place:
        # A rejection withholds a row that has not been sent. This one HAS: the
        # figure stays on the site until a person retracts it, and the spec
        # says so rather than letting "rejected" read as "gone".
        extra["live_row_needs_retraction"] = True
        print("  NOTE: this row is LIVE. Rejecting it withholds nothing; the figure "
              "stays published until retract.py takes it down. Listed for the owner.")
    if status != "agree":
        path = write_spec(item, status, verdicts, extra, spec_dir)
        print(f"  {status.upper()}: the referees did not agree. Nothing applied. "
              f"A human decides. Spec: {path}  (spend ${cost:.4f})")
        return 3

    note = deciding_note(key, action, verdicts, correction)
    changed = act(conn, item, action, correction, note, apply=apply, push=push)
    outcome = "applied" if apply and changed else "agree-dry-run"
    path = write_spec(item, outcome, verdicts, {**extra, "note": note, "who": WHO,
                                                "ledger_rows_changed": changed}, spec_dir)
    print(f"  AGREE: {action}. {'APPLIED' if apply else 'dry run'} (spend ${cost:.4f}) Spec: {path}")
    return 0


def apply_from_spec(conn, path: Path, *, apply: bool, push=None) -> int:
    """Re-apply an agreed spec without calling a model. For a checkout that
    holds the spec but not the ledger write (a merge, a second machine)."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("status") == "owner-ruled":
        # The one path that is not two referees agreeing: the referees
        # DISAGREED, the owner read both reasonings and ruled. The ruling is
        # recorded in the spec by name, with its reason, and is applied under
        # that name rather than under the two-model WHO, so the ledger never
        # says two models agreed when they did not.
        missing = [k for k in ("ruled_by", "ruling", "action") if not spec.get(k)]
        if missing:
            print(f"{path}: owner-ruled spec lacks {', '.join(missing)}; nothing to apply")
            return 3
        who = f"owner ruling ({spec['ruled_by']}) after referee disagreement"
    elif spec.get("status") in ("agree-dry-run", "applied"):
        who = spec.get("who", WHO)
    else:
        print(f"{path}: status {spec.get('status')!r} is not an agreement; nothing to apply")
        return 3
    item = load_finding(conn, spec["key"])
    if item is None:
        print(f"{spec['key']}: no such finding")
        return 1
    if item["finding"].get("state") != "open":
        print(f"{spec['key']}: already {item['finding'].get('state')}")
        return 0
    note = spec.get("note") or spec.get("ruling") or ""
    act = apply_place if spec["key"].startswith(PLACE + "/") else apply_decision
    changed = act(conn, item, spec["action"], spec.get("correction"),
                  note, who=who, apply=apply, push=push)
    print(f"{spec['key']}: {spec['action']} {'APPLIED' if apply else 'dry run'} "
          f"({changed} ledger row(s))")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", action="append", default=[], metavar="KEY",
                    help="a finding key, check/subject (repeatable)")
    ap.add_argument("--from-spec", action="append", default=[], metavar="PATH",
                    help="apply an already-agreed spec file, calling no model")
    ap.add_argument("--apply", action="store_true",
                    help="write on agreement (otherwise a dry run)")
    ap.add_argument("--row", action="append", default=[], metavar="CONTENT_HASH",
                    help="a live row no check caught: open amount/<hash> and adjudicate its figure")
    ap.add_argument("--place-row", action="append", default=[], metavar="CONTENT_HASH",
                    help="a live row whose location looks wrong: open place/<hash> and adjudicate it")
    ap.add_argument("--reason", default="",
                    help="why the session raised it (required with --row or --place-row)")
    ap.add_argument("--ceiling", type=float, default=None,
                    help=f"this run's spend ceiling in USD (default {RUN_CEILING_USD:.2f})")
    ap.add_argument("--health", action="store_true",
                    help="file the run's spend as a priced source_health row (the committed cost ledger)")
    args = ap.parse_args(argv)
    if not (args.key or args.from_spec or args.row or args.place_row):
        ap.error("give at least one --key, --row, --place-row or --from-spec")
    if (args.row or args.place_row) and not args.reason.strip():
        ap.error("--row and --place-row need --reason: the ledger records why a session raised it")

    conn = schema.connect()
    worst = 0
    for path in args.from_spec:
        worst = max(worst, apply_from_spec(conn, Path(path), apply=args.apply))

    keys = list(args.key)
    opened: list[str] = []
    for check, hashes in ((guardrails.AMOUNT, args.row), (PLACE, args.place_row)):
        for content_hash in hashes:
            try:
                state = open_session_finding(conn, check, content_hash, args.reason.strip())
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"{state['key']}: {'opened' if state['opened'] else 'already ' + state['state']}")
            if state["opened"]:
                opened.append(state["key"])
            keys.append(state["key"])
    if not keys:
        return worst

    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        print("OPENROUTER_API_KEY is not set; a referee cannot be asked", file=sys.stderr)
        for key in opened:
            drop_session_finding(conn, key)
        return 1
    start_usd = float(classify.STATS.get("usd", 0.0))
    try:
        for key in keys:
            # Module state read at call time, so a test (or a caller) that
            # replaces the reader or the spec directory is honoured.
            rc = adjudicate(conn, key, apply=args.apply, start_usd=start_usd,
                            fetch=fetch_page, wayback=wayback_copy, spec_dir=SPEC_DIR,
                            ceiling=args.ceiling)
            worst = max(worst, rc)
    finally:
        if not args.apply:
            # A dry run leaves the ledger as it found it: only the findings THIS
            # run opened, and only while still open (an applied one is not).
            for key in opened:
                if drop_session_finding(conn, key):
                    print(f"{key}: dry run, session finding removed again")
        spent = _spent_so_far(start_usd)
        if args.health:
            # The run's OWN delta of the provider's cost figure, not
            # classify.usage_snapshot(): that returns None unless the gate or
            # read counters moved, and this path meters through _call without
            # touching them, so it would file a free run for a paid one.
            store.report_health(conn, HEALTH_NAME, status="ok", items_found=len(keys),
                                items_stored=len(keys),
                                detail=f"two-referee adjudication of {len(keys)} key(s), exit {worst}",
                                usage={"model": " + ".join(REFEREES),
                                       "cost_usd": round(float(spent), 6),
                                       "reads_bought": 2 * len(keys)})
            conn.commit()
    limit = RUN_CEILING_USD if args.ceiling is None else args.ceiling
    print(f"\nrun spend ${spent:.4f} of a ${limit:.2f} ceiling; "
          f"{len(keys)} key(s); exit {worst}")
    return worst


if __name__ == "__main__":
    sys.exit(main())

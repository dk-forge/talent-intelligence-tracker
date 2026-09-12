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
import correct_funding_amount  # noqa: E402
from pipeline import classify, guardrails, money_raised, schema, store  # noqa: E402

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
            "published_date", "collector", "country", "published_at")
    return {k: row.get(k) for k in keep} if row else {}


def finding_view(finding: dict) -> dict:
    return {k: finding.get(k) for k in ("check_name", "label", "detail", "value",
                                        "first_seen", "seen")}


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


def _gate(start_usd: float) -> None:
    """Read before EVERY paid request: the month's switch, then this run's ceiling."""
    if not classify.paid_reads_enabled():
        raise BudgetStop("TIT_PAID_READS is off: the month's allowance is spent")
    spent = _spent_so_far(start_usd)
    if spent >= RUN_CEILING_USD:
        raise BudgetStop(f"run ceiling ${RUN_CEILING_USD:.2f} reached (${spent:.4f} spent)")


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


def ask_referee(model: str, prompt: str, *, start_usd: float,
                call=None, attempts: int = ATTEMPTS) -> tuple[dict | None, float]:
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
        _gate(start_usd)
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
    verdict = parse_verdict(content)
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


def deciding_note(key: str, action: str, verdicts: dict, correction: dict | None) -> str:
    """The ledger note: the verdict and each referee's deciding sentence.

    Unique per finding by construction (it carries the key), which is what
    `guardrails.review()`'s shared-note guard is there to check.
    """
    quotes = "; ".join(f"{m}: {(v.get('reasoning') or '')[:220]}"
                       for m, v in verdicts.items() if v)
    fix = ""
    if correction:
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
               spec_dir: Path = SPEC_DIR, push=None) -> int:
    """Returns 0 on agreement (applied or dry), 3 on UNKNOWN or disagreement, 1 on a hard failure."""
    item = load_finding(conn, key)
    if item is None:
        print(f"{key}: no such finding. Keys look like amount/<content_hash>.")
        return 1
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

    prompt = PROMPT.format(rules=RULES,
                           finding=json.dumps(finding_view(finding), indent=1, ensure_ascii=False),
                           row=json.dumps(row_view(item["row"]), indent=1, ensure_ascii=False),
                           evidence=evidence)
    verdicts: dict[str, dict | None] = {}
    costs: dict[str, float] = {}
    for model in REFEREES:
        try:
            verdicts[model], costs[model] = ask_referee(model, prompt, start_usd=start_usd, call=call)
        except BudgetStop as exc:
            print(f"  {model}: budget stop ({exc}); UNDECIDED")
            verdicts[model], costs[model] = None, 0.0
        v = verdicts[model]
        print(f"  {model}: " + (json.dumps(v, ensure_ascii=False) if v else "no usable verdict"))

    status, action, correction = decide(verdicts)
    cost = round(sum(costs.values()), 6)
    extra = {"cost_usd": cost, "cost_by_referee": costs, "evidence_url": used,
             "evidence_chars": len(evidence), "action": action, "correction": correction}
    if status != "agree":
        path = write_spec(item, status, verdicts, extra, spec_dir)
        print(f"  {status.upper()}: the referees did not agree. Nothing applied. "
              f"A human decides. Spec: {path}  (spend ${cost:.4f})")
        return 3

    note = deciding_note(key, action, verdicts, correction)
    changed = apply_decision(conn, item, action, correction, note, apply=apply, push=push)
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
    changed = apply_decision(conn, item, spec["action"], spec.get("correction"),
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
    args = ap.parse_args(argv)
    if not args.key and not args.from_spec:
        ap.error("give at least one --key or --from-spec")

    conn = schema.connect()
    worst = 0
    for path in args.from_spec:
        worst = max(worst, apply_from_spec(conn, Path(path), apply=args.apply))
    if not args.key:
        return worst

    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        print("OPENROUTER_API_KEY is not set; a referee cannot be asked", file=sys.stderr)
        return 1
    start_usd = float(classify.STATS.get("usd", 0.0))
    for key in args.key:
        rc = adjudicate(conn, key, apply=args.apply, start_usd=start_usd)
        worst = max(worst, rc)
    spent = _spent_so_far(start_usd)
    print(f"\nrun spend ${spent:.4f} of a ${RUN_CEILING_USD:.2f} ceiling; "
          f"{len(args.key)} key(s); exit {worst}")
    return worst


if __name__ == "__main__":
    sys.exit(main())

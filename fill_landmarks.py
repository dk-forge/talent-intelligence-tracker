#!/usr/bin/env python3
"""Fill the standing landmark gaps from their primary documents, through the
machinery, with two referees between the extraction and the write.

WHAT A STANDING GAP IS. `check_landmarks.py` names twenty events nobody could
defend missing, each with the company's own announcement behind it, and on
2026-09-12 twelve of them had never been held. Every one has a primary
document. Nothing in the pipeline had read it, because a corporate newsroom is
not a feed anybody wired; that is exactly the situation
`collectors/primary_chase.py` exists for.

WHAT THIS DOES, in order, and what it never does:

1. Reads the landmark set and the committed database and lists the entries
   the stored lens does not hold (`analysis.landmarks.check.verdict`, the same
   function the weekly check uses, so "gap" has one definition).
2. Puts each gap's primary URL on a primary_chase work list and runs the
   ordinary collector in DRY RUN: `run_collect.run(dry_run=True,
   source="primary_chase")`, the identical `prefilter -> precheck -> extract
   -> validate -> dedupe` path every candidate takes. The rows it WOULD store
   are captured off the same hook that prints them. Nothing here builds a row
   by hand and nothing here writes one.
3. Matches each captured row back to its landmark with `check.candidates`, and
   puts the row, the document text and the tracker's written money rules to
   two referees from different vendors (`adjudicate_guardrail.REFEREES`,
   through `classify._call`, the repo's one door to OpenRouter). Each answers
   strict JSON: does the row match the landmark within the check's own
   tolerance, what is the money basis, accept or reject.
4. Writes both verdicts verbatim to
   `analysis/adjudications/<date>-landmark-<id>.json`, one file per landmark,
   including the ones that were UNKNOWN (no row, or no readable document) so
   the absence is written down and not inferred.
5. With `--queue`, and ONLY for landmarks BOTH referees accepted AND matched:
   rewrites `data/primary_chase_worklist.json` to carry exactly those URLs and
   enqueues ONE `collect.yml` ticket with `source=primary_chase` through
   `writer_queue.enqueue`, the same ticket a human types with
   `gh workflow run drain-writers.yml -f enqueue=collect.yml ...`. The drainer
   dispatches it into an empty lock group, the runner re-reads each document
   and stores what the document states. The work list carries URLs and nothing
   else, so a referee's acceptance cannot reach the database either: it only
   decides whether the collector is pointed at the page.

Disagreements and UNKNOWNs are left with their spec files and reported; no
URL for them reaches the work list. A stored row that then trips the publish
guardrail's amount ceiling is `held_not_live`, which is a separate, existing
queue answered by `adjudicate_guardrail.py`.

COST. The dry run pays the ordinary extraction price for candidates
`cheap_extract` cannot close (metered inside `classify`, same as any collect
run); the referees are two short calls per row. Every request goes through
`classify._call`; the run ceiling is read from `classify.STATS["usd"]` before
each referee attempt and the retry is the outer loop, never inside the call.

USAGE
    OPENROUTER_API_KEY=... .venv/bin/python fill_landmarks.py            # dry: verdicts + specs
    ... --queue                                                          # also: work list + ticket
    .venv/bin/python fill_landmarks.py --from-specs --queue              # no model: rebuild from specs

Exit codes: 0 every gap was accepted (or queued); 3 at least one gap is
UNKNOWN, rejected or disagreed (spec written, nothing queued for it); 1 a hard
failure (no key, no gaps to work on, invalid landmark set).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import adjudicate_guardrail as adj  # noqa: E402
import writer_queue  # noqa: E402
from analysis.landmarks import check, landmarks  # noqa: E402
from collectors import primary_chase  # noqa: E402
from pipeline import classify, schema  # noqa: E402

REFEREES = adj.REFEREES
#: The whole run, extraction and referees together, read from OpenRouter's own
#: cost figure. Twelve rows at the guardrail adjudicator's measured $0.029 a
#: pair is $0.35; the rest is the extraction the dry run buys.
RUN_CEILING_USD = 0.50
ATTEMPTS = adj.ATTEMPTS
SPEC_DIR = adj.SPEC_DIR
WORKLIST_PATH = Path(primary_chase.WORKLIST_PATH)
WORKFLOW = "collect.yml"
WORKFLOW_INPUTS = {"source": "primary_chase"}
WHO = f"two-model landmark adjudication ({REFEREES[0]} + {REFEREES[1]})"

QUEUE = "queue"
REJECT = "reject"
DISAGREE = "disagree"
UNKNOWN = "unknown"


class BudgetStop(RuntimeError):
    """The run ceiling or the month's allowance binds. UNDECIDED, never a verdict."""


PROMPT = """You are one of two independent referees checking a funding row a public
talent-market tracker extracted from the EMPLOYER'S OWN announcement, before it
is written. The other referee is a model from a different vendor; you will not
see its answer. Be strict and literal. Decide only from the row, the landmark
entry and the document text below. If the document text is empty, unrelated,
or does not mention this company, answer "reject" with confidence 0.

{rules}

THE LANDMARK ENTRY (assembled by hand from public sources; the event the row
is supposed to be):
{landmark}
The row MATCHES the landmark when its USD amount is within {tolerance:.0%} of
the landmark's amount_usd (or, when amount_text begins "more than", "over" or
"at least", is not below it) AND its date is within {window} days of
event_date. A row stating the round in another currency matches when the
stated figure is the one the document states for this round.

THE EXTRACTED ROW (what the collector would store):
{row}

THE PRIMARY DOCUMENT (text only, may be truncated):
<<<
{evidence}
>>>

Answer with ONE JSON object and nothing else:
{{"matches_landmark": true|false,
  "money_basis": "<one of the vocabulary>|unknown",
  "recommended": "accept"|"reject",
  "confidence": 0-100,
  "reasoning": "at most 600 characters, quoting the sentence in the document that decides it"}}

"accept" means: the row's amount is a figure the document states for this
company and this event, its money_basis really is company_raise, and it
matches the landmark. Anything else is "reject".
"""


# --------------------------------------------------------------------------
# 1. The gaps
# --------------------------------------------------------------------------

def standing_gaps(data: dict, rows: list) -> list[dict]:
    """Landmark entries the stored lens does not hold, largest first."""
    tolerance = float(data.get("amount_tolerance") or check.AMOUNT_TOLERANCE)
    window = int(data.get("window_days") or check.WINDOW_DAYS)
    gaps = [e for e in landmarks.entries(data)
            if check.verdict(e, rows, tolerance, window)["verdict"] != check.HELD]
    return sorted(gaps, key=lambda e: -(e.get("amount_usd") or 0))


# --------------------------------------------------------------------------
# 2. The collector, in dry run, with its rows captured
# --------------------------------------------------------------------------

def leads_for(gaps: list[dict]) -> list[dict]:
    """A primary_chase work list: URLs and nothing else. `archive_fallback` is
    on for every lead, which only matters when the publisher refuses the
    fetch (openai.com answers 403 to any non-interactive client)."""
    return [{"url": e["source_url"], "archive_fallback": True} for e in gaps]


def extract_rows(gaps: list[dict], *, run=None) -> list[dict]:
    """The rows the ordinary collector WOULD store for these leads.

    `run_collect.run(dry_run=True, source="primary_chase")` is the machinery;
    the work list it reads is a temporary file holding the gap URLs, and the
    rows are captured off `_print_signal`, the hook the dry run already calls
    for every row it would store. Nothing is written: dry run marks nothing
    seen and stores nothing.
    """
    import run_collect
    run = run or run_collect.run
    captured: list[dict] = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tmp:
        json.dump({"leads": leads_for(gaps)}, tmp)
        worklist = tmp.name
    saved_default = primary_chase.load_worklist.__defaults__
    saved_print = run_collect._print_signal
    primary_chase.load_worklist.__defaults__ = (worklist,)
    run_collect._print_signal = lambda s: captured.append(asdict(s))
    try:
        rc = run(dry_run=True, offline=False, run_index=0, limit=None,
                 source="primary_chase")
    finally:
        primary_chase.load_worklist.__defaults__ = saved_default
        run_collect._print_signal = saved_print
        try:
            os.unlink(worklist)
        except OSError:
            pass
    if rc != 0:
        print(f"collector dry run exited {rc}; its rows are still judged")
    return captured


def match_rows(gaps: list[dict], rows: list[dict], data: dict) -> dict[str, dict | None]:
    """{landmark id: the captured row that could be it, or None}.

    `check.candidates` is the weekly check's own matcher (employer name,
    funding shape, date window), so a row that would not answer for the
    landmark there does not answer for it here either.
    """
    window = int(data.get("window_days") or check.WINDOW_DAYS)
    out: dict[str, dict | None] = {}
    for entry in gaps:
        found = check.candidates(entry, rows, window)
        out[entry["id"]] = found[0] if found else None
    return out


def would_hold(entry: dict, row: dict, data: dict) -> dict:
    """The weekly check's verdict on this one row, so a queued row is one the
    check will call HELD and not a second near miss."""
    tolerance = float(data.get("amount_tolerance") or check.AMOUNT_TOLERANCE)
    window = int(data.get("window_days") or check.WINDOW_DAYS)
    return check.verdict(entry, [row], tolerance, window)


# --------------------------------------------------------------------------
# 3. The document and the referees
# --------------------------------------------------------------------------

def collector_read(url: str, *, reader=None) -> tuple[str, str]:
    """(raw_text, url the collector read it from): the same document as the
    collector's own reader sees it, `primary_chase._fetch` with the archive
    fallback. openai.com serves 769 characters of shell to a page-stripper and
    the whole announcement to the collector's reader; the row came out of the
    latter, so that is the text a referee should judge it against."""
    reader = reader or (lambda u: primary_chase._fetch(
        u, timeout=adj.FETCH_TIMEOUT, session=__import__("requests").Session(),
        archive_fallback=True))
    try:
        item = reader(url)
    except Exception as exc:  # noqa: BLE001 - unreadable is "", reported not raised
        print(f"  evidence: the collector's reader failed on {url[:80]} ({type(exc).__name__})")
        return "", ""
    if not item:
        return "", ""
    return (item.get("raw_text") or "")[:adj.EVIDENCE_CAP], item.get("discovery_url") or url


def fetch_document(entry: dict, row: dict, *, fetch=adj.fetch_page,
                   wayback=adj.wayback_copy, reader=None) -> tuple[str, str]:
    """(text, url_used): the landmark's recorded archive copy first, then the
    primary URL, then a Wayback snapshot, then the collector's own read of the
    page. Browser UA, 15 s, stripped, capped at
    adjudicate_guardrail.EVIDENCE_CAP, and every copy must clear
    adjudicate_guardrail.EVIDENCE_MIN_CHARS. Empty when nothing does, and then
    nothing is spent."""
    url = entry.get("source_url") or row.get("source_url")
    item = {"row": {"source_url": url}, "archive_url": entry.get("archive_url")}
    text, used = adj.fetch_evidence(item, fetch=fetch, wayback=wayback)
    if text:
        return text, used
    text, used = collector_read(url, reader=reader)
    if len(text) >= adj.EVIDENCE_MIN_CHARS:
        print(f"  evidence: the page-stripper read too little; using the collector's own read")
        return text, used
    if text:
        print(f"  evidence: the collector's own read is only {len(text)} characters")
    return "", ""


def row_view(row: dict) -> dict:
    keep = ("company", "headline", "summary", "pillar", "funding_amount",
            "funding_amount_usd", "funding_stage", "deal_type", "money_basis",
            "source_url", "source_name", "published_date", "collector",
            "city", "country", "hq_city", "hq_country", "confidence", "notes")
    return {k: row.get(k) for k in keep}


def landmark_view(entry: dict) -> dict:
    keep = ("id", "quarter", "company", "event_date", "amount_usd", "amount_text",
            "currency", "valuation_usd", "country", "source_url", "note")
    return {k: entry.get(k) for k in keep if entry.get(k) is not None}


def build_prompt(entry: dict, row: dict, evidence: str, data: dict) -> str:
    return PROMPT.format(
        rules=adj.RULES,
        landmark=json.dumps(landmark_view(entry), indent=1, ensure_ascii=False),
        tolerance=float(data.get("amount_tolerance") or check.AMOUNT_TOLERANCE),
        window=int(data.get("window_days") or check.WINDOW_DAYS),
        row=json.dumps(row_view(row), indent=1, ensure_ascii=False),
        evidence=evidence)


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
    """A verdict dict or None. `adjudicate_guardrail.parse_verdict` does the
    JSON, vocabulary and confidence-floor work; this adds the one field the
    landmark question needs and narrows the actions to accept|reject."""
    parsed = adj.parse_verdict(content)
    if parsed is None:
        return None
    if parsed.get("recommended") == "edit":
        # Not offered. The prompt says anything but accept is reject, and a
        # referee wanting the row CHANGED is a referee not accepting it as it
        # stands. The original answer is kept on the verdict.
        parsed["recommended_raw"] = "edit"
        parsed["recommended"] = "reject"
    if parsed.get("recommended") not in ("accept", "reject"):
        return None
    if not isinstance(parsed.get("matches_landmark"), bool):
        return None
    return parsed


def ask_referee(model: str, prompt: str, *, start_usd: float,
                call=None, attempts: int = ATTEMPTS) -> tuple[dict | None, float]:
    """One verdict from one model: (verdict or None, this referee's cost).
    `call` performs exactly ONE request; the gate is read before each attempt
    and the cost is metered after. The shape of adjudicate_guardrail.ask_referee."""
    call = call or classify._call
    before = float(classify.STATS.get("usd", 0.0))
    content = None
    last_error: Exception | None = None
    for _attempt in range(attempts):
        _gate(start_usd)
        try:
            content = call(model, classify.MINI_SYSTEM, prompt,
                           timeout=adj.CALL_TIMEOUT, max_tokens=adj.MAX_TOKENS,
                           json_mode=True)
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
        print(f"  {model}: answer is not a usable verdict: {content[:200]!r}")
    return verdict, cost


# --------------------------------------------------------------------------
# 4. The decision rule
# --------------------------------------------------------------------------

def decide(verdicts: dict[str, dict | None]) -> str:
    """QUEUE only when BOTH referees answered, BOTH accept and BOTH say the row
    matches the landmark. Any reject is REJECT (when they agree) or DISAGREE
    (when they do not). A missing verdict is UNKNOWN, never a tie-break."""
    answered = {m: v for m, v in verdicts.items() if v}
    if len(answered) < 2:
        return UNKNOWN
    accepts = [v["recommended"] == "accept" and v["matches_landmark"] is True
               for v in answered.values()]
    if all(accepts):
        return QUEUE
    if not any(accepts):
        return REJECT
    return DISAGREE


# --------------------------------------------------------------------------
# 5. The spec file
# --------------------------------------------------------------------------

def spec_path(entry_id: str, spec_dir: Path = SPEC_DIR,
              today: _dt.date | None = None) -> Path:
    stamp = (today or _dt.date.today()).isoformat()
    return spec_dir / f"{stamp}-landmark-{entry_id}.json"


def plain_dashes(value):
    """Em and en dashes become hyphens, recursively, in every string written to
    a spec. A referee quoting a document verbatim carries the document's
    typography with it, and this repository's dash ban covers every committed
    file, including a quotation. Every other character is left alone."""
    if isinstance(value, str):
        return value.replace("\u2014", "-").replace("\u2013", "-")
    if isinstance(value, dict):
        return {k: plain_dashes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain_dashes(v) for v in value]
    return value


def write_spec(entry: dict, status: str, verdicts: dict, row: dict | None,
               extra: dict, spec_dir: Path = SPEC_DIR) -> Path:
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_path(entry["id"], spec_dir)
    body = plain_dashes({
        "key": f"landmark/{entry['id']}",
        "landmark": landmark_view(entry),
        "status": status,
        "referees": list(REFEREES),
        "verdicts": verdicts,
        "row": row_view(row) if row else None,
        **extra,
        "who": WHO,
        "written_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


# --------------------------------------------------------------------------
# 6. Queueing, through the work list and the writer queue
# --------------------------------------------------------------------------

def write_worklist(accepted: list[dict], path: Path = WORKLIST_PATH,
                   today: _dt.date | None = None) -> None:
    """The work list carries URLs and nothing else that reaches the database."""
    stamp = (today or _dt.date.today()).isoformat()
    body = {
        "_comment": [
            "Where to LOOK, never what to store. Every field of every record comes",
            "out of the document the URL serves; nothing in this file reaches the",
            "database, including the notes. See collectors/primary_chase.py.",
            "",
            f"Rewritten {stamp} by fill_landmarks.py: the primary documents of the",
            "standing landmark gaps whose extracted rows BOTH referees accepted.",
            "The spec files are analysis/adjudications/<date>-landmark-*.json.",
            "",
            "'archive_fallback': the publisher's host may refuse automated requests",
            "(openai.com answers 403 to every non-interactive client). The archived",
            "copy of the SAME url is read instead; the cited source_url is still the",
            "publisher's own permalink, because that is the document.",
        ],
        "leads": [{"url": e["source_url"], "archive_fallback": True,
                   "note": f"landmark {e['id']}"} for e in accepted],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def enqueue_ticket(reason: str, by: str, queue_path: Path | None = None,
                   workflow_dir: Path | None = None) -> dict:
    """ONE collect.yml ticket, source=primary_chase, through writer_queue.
    The drainer dispatches it into an empty lock group; nothing here
    dispatches a writer directly."""
    queue = writer_queue.load(queue_path)
    for waiting in queue.get("tickets", []):
        if (waiting.get("workflow") == WORKFLOW
                and waiting.get("state") == "queued"
                and waiting.get("inputs") == {k: str(v) for k, v in WORKFLOW_INPUTS.items()}):
            # One run reads the whole work list, so a second ticket for the
            # same pass is a second fetch of documents already read.
            return waiting
    ticket = writer_queue.enqueue(queue, WORKFLOW, dict(WORKFLOW_INPUTS), reason, by,
                                  workflow_dir=workflow_dir)
    writer_queue.prune(queue)
    writer_queue.save(queue, queue_path)
    return ticket


# --------------------------------------------------------------------------
# One landmark, end to end
# --------------------------------------------------------------------------

def adjudicate(entry: dict, row: dict | None, data: dict, *, start_usd: float,
               call=None, fetch=adj.fetch_page, wayback=adj.wayback_copy,
               reader=None, spec_dir: Path = SPEC_DIR) -> tuple[str, Path, float]:
    """(status, spec path, cost) for one landmark."""
    label = f"{entry['quarter']} {entry['company']} {check._money(entry.get('amount_usd'))}"
    print(f"\n{entry['id']}  {label}")
    if row is None:
        path = write_spec(entry, UNKNOWN, {}, None,
                          {"why": "the collector dry run produced no row for this document",
                           "evidence_url": None, "cost_usd": 0.0}, spec_dir)
        print(f"  UNKNOWN: no row from the collector. Spec: {path}")
        return UNKNOWN, path, 0.0

    evidence, used = fetch_document(entry, row, fetch=fetch, wayback=wayback, reader=reader)
    if not evidence:
        path = write_spec(entry, UNKNOWN, {}, row,
                          {"why": "no readable copy of the primary document",
                           "evidence_url": None, "cost_usd": 0.0}, spec_dir)
        print(f"  UNKNOWN: the document could not be read. Nothing asked. Spec: {path}")
        return UNKNOWN, path, 0.0
    print(f"  evidence: {len(evidence)} characters from {used[:90]}")

    prompt = build_prompt(entry, row, evidence, data)
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

    status = decide(verdicts)
    local = would_hold(entry, row, data)
    if status == QUEUE and local["verdict"] != check.HELD:
        # Both referees accepted a row the weekly check would still not call
        # HELD (a currency the check cannot compare, a date outside its
        # window). Queueing it fills nothing, so it is written down as such.
        status = DISAGREE
        print(f"  the check itself would read this row {local['verdict']}: "
              f"{local['detail']}; not queued")
    cost = round(sum(costs.values()), 6)
    extra = {"cost_usd": cost, "cost_by_referee": costs, "evidence_url": used,
             "evidence_chars": len(evidence), "check_verdict": local["verdict"],
             "check_detail": local.get("detail", "")}
    path = write_spec(entry, status, verdicts, row, extra, spec_dir)
    print(f"  {status.upper()}  (spend ${cost:.4f})  Spec: {path}")
    return status, path, cost


def accepted_from_specs(gaps: list[dict], spec_dir: Path = SPEC_DIR,
                        today: _dt.date | None = None) -> list[dict]:
    """The gaps whose spec on disk (today's) says QUEUE. For a checkout that
    holds the specs and not the run."""
    out = []
    for entry in gaps:
        path = spec_path(entry["id"], spec_dir, today)
        if not path.exists():
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("status") in (QUEUE, "queued"):
            out.append(entry)
    return out


def mark_queued(entries: list[dict], ticket: dict, spec_dir: Path = SPEC_DIR,
                today: _dt.date | None = None) -> None:
    for entry in entries:
        path = spec_path(entry["id"], spec_dir, today)
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["status"] = "queued"
        spec["ticket"] = {"id": ticket["id"], "workflow": ticket["workflow"],
                          "inputs": ticket["inputs"]}
        path.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", action="store_true",
                    help="rewrite the work list and enqueue the collect ticket for accepted rows")
    ap.add_argument("--from-specs", action="store_true",
                    help="call no model: read today's spec files for the accepted set")
    ap.add_argument("--by", default=os.environ.get("USER") or "fill_landmarks",
                    help="requested_by on the ticket")
    ap.add_argument("--only", action="append", default=[], metavar="LANDMARK_ID",
                    help="work only these landmark ids (repeatable); others keep their specs")
    args = ap.parse_args(argv)

    try:
        data = landmarks.load()
    except landmarks.InvalidLandmarkSet as exc:
        print(f"landmark set invalid: {exc}", file=sys.stderr)
        return 1
    conn = schema.connect()
    gaps = standing_gaps(data, check.stored_rows(conn))
    conn.close()
    if not gaps:
        print("no standing gaps: every landmark is held in the stored lens")
        return 0
    if args.only:
        unknown_ids = sorted(set(args.only) - {e["id"] for e in gaps})
        if unknown_ids:
            print(f"not a standing gap: {', '.join(unknown_ids)}", file=sys.stderr)
            return 1
        gaps = [e for e in gaps if e["id"] in set(args.only)]
    print(f"{len(gaps)} standing gap(s):")
    for e in gaps:
        print(f"  {e['quarter']}  {e['company']:<16} {check._money(e.get('amount_usd')):>8}  {e['source_url']}")

    if args.from_specs:
        accepted = accepted_from_specs(gaps)
        statuses = {e["id"]: (QUEUE if e in accepted else "not accepted") for e in gaps}
        spent = 0.0
    else:
        if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
            print("OPENROUTER_API_KEY is not set; neither the extractor nor a referee can be asked",
                  file=sys.stderr)
            return 1
        start_usd = float(classify.STATS.get("usd", 0.0))
        rows = extract_rows(gaps)
        extraction_usd = _spent_so_far(start_usd)
        print(f"\ncollector dry run: {len(rows)} row(s) would store, extraction spend ${extraction_usd:.4f}")
        matched = match_rows(gaps, rows, data)
        statuses = {}
        accepted = []
        for entry in gaps:
            status, _path, _cost = adjudicate(entry, matched[entry["id"]], data,
                                              start_usd=start_usd)
            statuses[entry["id"]] = status
            if status == QUEUE:
                accepted.append(entry)
        spent = _spent_so_far(start_usd)

    print("\nOUTCOME")
    for e in gaps:
        print(f"  {statuses[e['id']]:<13} {e['quarter']}  {e['company']:<16} "
              f"{check._money(e.get('amount_usd')):>8}")
    print(f"run spend ${spent:.4f} of a ${RUN_CEILING_USD:.2f} ceiling")

    if args.queue and accepted:
        # The work list is the union of everything today's specs accepted, so
        # a narrowed re-run adds to the pass rather than replacing it.
        conn = schema.connect()
        every_gap = standing_gaps(data, check.stored_rows(conn))
        conn.close()
        held_ids = {e["id"] for e in accepted}
        accepted = accepted + [e for e in accepted_from_specs(every_gap)
                               if e["id"] not in held_ids]
        write_worklist(accepted)
        ticket = enqueue_ticket(
            reason=f"landmarks: {len(accepted)} standing gap(s) accepted by two referees",
            by=args.by)
        mark_queued(accepted, ticket)
        print(f"work list: {len(accepted)} lead(s) -> {WORKLIST_PATH.name}; "
              f"queued {ticket['id']} inputs={ticket['inputs']}")
    elif args.queue:
        print("nothing accepted; the work list and the queue are untouched")
    else:
        print(f"dry: {len(accepted)} accepted, nothing queued (pass --queue)")

    return 0 if all(s in (QUEUE,) for s in statuses.values()) else 3


if __name__ == "__main__":
    sys.exit(main())

"""One JSONL line per gate decision: the training set the classifier gate needs.

WHY THIS EXISTS
---------------
docs/PLAN-gate-to-five-dollars.md, step 1. The paid gate is $5.70/month on its
own — the cost of LOOKING at ~3,150 candidates a day to find the ~1,280 worth
reading — which is more than the owner's entire $5 target, so no model swap can
reach it. The only route under $5 is a local classifier that answers most of
those looks for free, and a classifier needs labels.

VERIFIED 2026-07-31: `pipeline/classify.py` kept ONLY aggregate counters
(`STATS["gate_calls"]`, `STATS["gate_rejects"]`). Every per-candidate verdict
this pipeline has ever formed was thrown away the moment it was acted on. Each
day without this module is a day of training data lost, which is why step 1
comes first and why it costs nothing: no model is called, no candidate is
treated differently, and the gate's control flow is untouched.

WHAT IS RECORDED
----------------
Exactly the feature set the real gate has, and never more:

  headline   the candidate's own headline
  teaser     up to 300 characters of the candidate's text, markup stripped

`classify.gate()` sends `raw_text[:1500]` with the publisher line prepended, so
what lands here is a strict SUBSET of what the gate saw. That direction matters
and only that direction: a classifier trained on more than the gate gets would
score well here and fail in production, so nothing downstream of the gate — no
extracted company, no model summary, no read-through — is ever written to a
label line.

WHAT IS NOT RECORDED
--------------------
No PII and no full article text. Headline plus a 300-character teaser, both
already public on the publisher's own page, plus the host, the collector, the
language and the country. Nothing about a person, nothing behind a paywall,
nothing that could not be read from the RSS item this pipeline was handed.
BORME taught this repo what an unexercised scrubber is worth; the way to not
need one is to never carry the field.

ONE BOUNDED EXCEPTION (2026-08-14): THE EXTRACTION-INPUT CAPTURE
----------------------------------------------------------------
An extraction gold set needs the text extraction actually read, and this repo
persisted it nowhere — which is the one thing that kept the biggest cost lever
(the extraction A/B) unmeasurable (docs/PLAN-gate-to-five-dollars.md,
CORRECTION 2026-08-14). So `capture_extract_input()` writes, onto the SAME
line the gate verdict and outcome already live on, the first
`EXTRACT_EXCERPT_CHARS` characters of the exact text sent to the extraction
model — for a deterministic 1-in-`SAMPLE_1_IN` sample of the candidates that
REACH extraction, capped at `EXTRACT_CAPTURE_PER_RUN` per run. Everything the
module promises above still holds for the other fields; this one is:

  * still only public text off the publisher's own page;
  * provider-name redacted like every other free-text field here (the one
    deliberate divergence from the production bytes — a future gold-set replay
    must note it);
  * bounded: at the caps below it is ~60 excerpts/day, ~4 KB each, ~7 MB in an
    open month, gzipped ~4x when the month closes and deleted with its shard
    after KEEP_MONTHS. Compare the text it samples FROM: ~1.5 MB/day.
  * temporary in purpose: once the extraction gold set exists, turn it off
    with TIT_EXTRACT_CAPTURE=off rather than letting it run forever.

THE JOIN
--------
The classifier's real target is "did this candidate end up a STORED row", not
"did the LLM gate like it" — the plan's shipping bar is measured against stored
rows. The gate verdict is formed inside `classify.classify()`; the outcome is
decided several guards later, in `run_collect`. Both are keyed by `key(item)`,
a hash of the same URL `run_collect` deduplicates on, and both happen inside one
run, so the join is closed IN MEMORY and one complete line is written at flush.
Nothing is left for a later pass to reconcile.

The one outcome a run cannot close is `deferred`: a throttled provider or the
read-through cap means the candidate is deliberately not marked seen and comes
back on a later run. That later run gates it again and writes a second line
under the SAME key, so a reader takes the last terminal outcome per key. This is
honest rather than convenient: a deferred candidate genuinely has no outcome
yet, and recording one would be inventing it.

IT MUST NEVER FAIL A RUN
------------------------
The gate is on the hot path. Every public function here swallows every
exception, says so once, loudly, and disables itself for the rest of the run.
A bookkeeping file is worth nothing next to a day of collection.

SIZE, MEASURED
--------------
Line size was measured on real candidates on 2026-07-31, not estimated:

    google_news     504 items, 5 locales   mean 310 B/line   (p90 354, max 437)
    national_press  253 items, 25 feeds    mean 519 B/line   (p90 634, max 733)

google_news is the cheaper half only because of the teaser rule below: its
`raw_text` is the headline followed by an <a href> around a 300-character base64
aggregator URL, and stripping markup collapses the teaser to nothing.

Against the plan's full-coverage rate of ~3,150 gate calls a day that is
**1.0-1.6 MB/day, i.e. 30-50 MB a month** depending on the collector mix; at the
rate actually measured on 2026-07-31 (1,894 gate calls that day, 98% of them
google_news) it is **~0.6 MB/day, ~18 MB a month**. Big enough that rotation is
built now rather than discovered later, which is the lesson `data/ats_board_
state.json` taught this repo at 13 MB.

So: sharded by month, a closed month gzipped on the first run of the next one
(~4x), and shards older than KEEP_MONTHS deleted. The OPEN month deliberately
stays plain text — git delta-compresses an append-only text file almost
perfectly, and a gzip blob rewritten twice a day would not delta at all.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

from . import provider_names

LEDGER_DIR = os.environ.get("TIT_GATE_LEDGER_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "gate_labels")

# Shards are named so that compaction can never mistake the hand-built weak
# bootstrap set (bootstrap-weak.jsonl) for a month it should gzip or delete.
SHARD_PREFIX = "labels-"

# The plan's own number: "headline, teaser (first ~300 chars)". Long enough that
# a funding figure in the first sentence of a national_press teaser survives,
# short enough that the ledger is a fifth of the size the raw text would be.
TEASER_CHARS = 300
# Observed maximum over 402 live google_news items on 2026-07-31 was 208. The
# cap exists so one pathological item cannot write a kilobyte line, not because
# anything is expected to reach it.
HEADLINE_CHARS = 300

# Six months: enough for the plan's 30-day replay test, a held-out month, and a
# monthly retrain with history behind it. Beyond that the labels describe a
# vocabulary the collectors no longer use.
KEEP_MONTHS = int(os.environ.get("TIT_GATE_LEDGER_KEEP_MONTHS", "6") or "6")

# Verdicts. Three values and not two, because the gate FAILS OPEN: a throttled
# or erroring gate returns True without ever having judged the candidate
# (`classify.gate`). Recording that as a YES would teach the classifier that
# provider outages are talent signals.
YES, NO, ERROR, OFF = "YES", "NO", "ERROR", "OFF"

# Two more since the classifier gate (plan step 2): the local classifier's own
# confident verdicts, recorded so the funnel stays complete once most
# candidates never reach the LLM gate. Kept DISTINCT from YES/NO on purpose —
# the trainer must never feed the classifier its own homework as ground truth
# (`train_gate_classifier.SELF_LABELLED`), and the drift alarm reads the
# CLF-vs-LLM split to know the uncertain share.
CLF_YES, CLF_NO = "CLF_YES", "CLF_NO"

# Feature bases. A training script must never mix them: `gate_text` lines carry
# the real headline and teaser the gate read, `url_slug` lines are the weak
# historical bootstrap, reconstructed from a URL because the source text was
# never kept. See bootstrap_gate_labels.py.
BASIS_GATE_TEXT = "gate_text"
BASIS_URL_SLUG = "url_slug"

DRY_RUN = False

# key -> line dict, plus the order they were gated in. A dict because the
# outcome arrives later and has to find its line; a list because a ledger that
# reordered a run's candidates would make the run log and the ledger disagree.
_BUFFER: dict[str, dict] = {}
_ORDER: list[str] = []
_DISABLED = False

STATS = {"recorded": 0, "outcomes": 0, "written": 0, "failures": 0}


def enabled() -> bool:
    """Off only if somebody turns it off. Default ON: the cost of collecting a
    label is a few hundred bytes, and the cost of not collecting it is a day."""
    if _DISABLED:
        return False
    return (os.environ.get("TIT_GATE_LEDGER") or "on").strip().lower() not in (
        "off", "0", "no", "false")


def set_dry_run(value: bool) -> None:
    """A rehearsal buffers and counts but writes nothing, exactly as the batch
    spool does. A dry run is usually a local experiment; it must not leave an
    uncommitted data file behind for a real run to push."""
    global DRY_RUN
    DRY_RUN = bool(value)


def reset() -> None:
    """Drop the buffer. For tests and for a caller that runs several sources in
    one process — the ledger is per-run state, like classify.STATS."""
    global _DISABLED
    _BUFFER.clear()
    _ORDER.clear()
    _DISABLED = False
    for name in STATS:
        STATS[name] = 0


def _fail(what: str, exc: Exception) -> None:
    """Say it once, loudly, then stop trying for the rest of the run.

    Loudly because this project's signature failure is the thing that looks
    healthy while broken; once because the alternative is one line per candidate
    for the rest of a run, which hides everything else in the step log.
    """
    global _DISABLED
    STATS["failures"] += 1
    if not _DISABLED:
        _DISABLED = True
        print(f"[gate-ledger] DISABLED for this run — {what} failed ({exc}). "
              "Collection is unaffected; this run's gate labels are lost.",
              file=sys.stderr)


def key(item: dict) -> str:
    """The stable join key for one candidate.

    Hashed from the same URL `run_collect` deduplicates on, with the same
    precedence, so a line here and a `seen_urls` row describe the same thing.
    Falls back to the headline for the sources that carry no URL at all.
    """
    ident = (item.get("source_url") or item.get("discovery_url")
             or item.get("headline") or "")
    return hashlib.sha1(ident.encode("utf-8", "replace")).hexdigest()[:16]


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_FOLD = re.compile(r"[^0-9a-zÀ-￿]+")


def _clean(text: str) -> str:
    """Markup out, whitespace collapsed, provider names redacted.

    Strictly reduces what the gate saw, which is the invariant this whole
    module rests on, and the redaction is one more reduction of the same kind.

    It lives HERE, in the one function every free-text field on a label line
    passes through, rather than in a check before the write: this file is
    appended to twice a day by a bot, and on 2026-08-13 three provider names
    reached `origin/main` inside real headlines and publisher hosts because
    nothing between the RSS item and the commit could refuse them. A filter is
    only as good as somebody remembering to call it; a chokepoint is not
    optional. See `pipeline/provider_names.py` for why this redacts rather
    than drops the text the way `analysis/ranking/gold_bucket.py` does.
    """
    cleaned = _WS.sub(" ", _TAG.sub(" ", html.unescape(text or ""))).strip()
    return provider_names.redact(cleaned)


def _fold(text: str) -> str:
    return _FOLD.sub("", (text or "").lower())


def teaser(raw_text: str, headline: str) -> str:
    """The candidate's text after its own headline, cleaned and truncated.

    Two reductions, both of which only ever REMOVE information the gate had:

    1. Markup is stripped. A google_news item's `raw_text` is the headline
       followed by an <a href> wrapping a 300-character base64 aggregator URL —
       measured over 402 live items on 2026-07-31, that boilerplate is most of
       the median 579-character body. No classifier can learn from it and every
       byte of it would be committed twice a day.
    2. A teaser that merely repeats the headline is dropped to "". After
       cleaning, that is exactly what a google_news body is, and google_news is
       roughly nine tenths of the gate's traffic. This one line is the
       difference between ~470 and ~200 bytes on most of the ledger.
    """
    body = _clean(raw_text)
    head = _clean(headline)
    if head and body.startswith(head):
        body = body[len(head):].strip()
    if not body:
        return ""
    folded_body, folded_head = _fold(body), _fold(head)
    if folded_head and (folded_body in folded_head or folded_head in folded_body):
        return ""
    return body[:TEASER_CHARS]


def _host(item: dict) -> str:
    """The publisher's host. The gate is told the outlet name in prose
    ("Published by: ..."), so this is the same signal in a stable form."""
    url = item.get("source_url") or item.get("discovery_url") or ""
    try:
        rest = url.split("://", 1)[-1]
        host = rest.split("/", 1)[0].split("@")[-1].split(":")[0].lower()[:80]
        # A provider's own domain is a provider name in plaintext, and 7 of the
        # 15 hits on 2026-08-13 were exactly that. Redacted for the same reason
        # and by the same function as the headline.
        return provider_names.redact(host)
    except Exception:
        return ""


def _lang(item: dict) -> str:
    """Language, however this collector spells it. gdelt and national_press set
    `language`; google_news carries a `locale` of "COUNTRY:lang"."""
    for candidate in (item.get("language"), item.get("lang")):
        if candidate:
            return str(candidate)[:12]
    locale = item.get("locale") or ""
    return locale.split(":", 1)[1][:12] if ":" in locale else ""


def _country(item: dict) -> str:
    """Country, where known, verbatim.

    NOT normalised on purpose. `source_country` is ISO2 from gdelt and
    national_press, `country` is a full name from sec_edgar, and folding them
    here would put a vocabulary decision inside a bookkeeping module — where it
    would drift away from `validate`'s. A training script normalises; a ledger
    records.
    """
    for candidate in (item.get("source_country"), item.get("country")):
        if candidate:
            return str(candidate)[:60]
    locale = item.get("locale") or ""
    return locale.split(":", 1)[0][:60] if ":" in locale else ""


def record(item: dict, collector: str, verdict: str) -> None:
    """Buffer one gate decision. Never raises.

    Called from `classify.classify()` the moment the gate answers, so the line
    carries the verdict for the exact text that produced it. The outcome is
    filled in later by `outcome()`; a candidate whose run ends before that keeps
    the provisional outcome set here.
    """
    if not enabled():
        return
    try:
        candidate_key = key(item)
        headline = _clean(item.get("headline") or "")[:HEADLINE_CHARS]
        line = {
            "key": candidate_key,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "collector": collector or (item.get("collector") or ""),
            "host": _host(item),
            "lang": _lang(item),
            "country": _country(item),
            "headline": headline,
            "teaser": teaser(item.get("raw_text") or "", headline),
            "gate": verdict,
            # A gate NO is terminal here and nowhere else: `classify` returns
            # None and no later stage ever sees the candidate, so this is the
            # only place that outcome can be written. A classifier drop is
            # terminal the same way, but keeps its own outcome value so the
            # trainer can exclude it (it is the model's opinion, not evidence).
            # Everything else starts as "unknown" and is closed by run_collect.
            "outcome": ("gate_reject" if verdict == NO
                        else "clf_reject" if verdict == CLF_NO else "unknown"),
            "basis": BASIS_GATE_TEXT,
        }
        if candidate_key not in _BUFFER:
            _ORDER.append(candidate_key)
        _BUFFER[candidate_key] = line
        STATS["recorded"] += 1
    except Exception as exc:            # never fail a run over bookkeeping
        _fail("recording a gate label", exc)


# --- The extraction-input capture (see the module docstring's exception) -----

#: Byte-equal to `classify.FULL_READ_CHARS` — the excerpt IS the extraction
#: input, because a shorter one would let a gold set score an easier task under
#: extraction's name. Not imported from classify (classify imports this
#: module); tests/test_extract_capture.py holds the two equal.
EXTRACT_EXCERPT_CHARS = 4000

#: Deterministic sample: capture when int(key, 16) % SAMPLE_1_IN == 0. By key,
#: not by coin flip, so a deferred candidate's second run agrees with its
#: first and two collectors seeing the same URL agree with each other.
SAMPLE_1_IN = 2

#: Hard per-run ceiling. At ~4 collect runs/day this is ~60 excerpts/day —
#: a few hundred per week, which is the gold set's appetite, at ~7 MB/month.
EXTRACT_CAPTURE_PER_RUN = 15


def capture_enabled() -> bool:
    return enabled() and (os.environ.get("TIT_EXTRACT_CAPTURE") or "on") \
        .strip().lower() not in ("off", "0", "no", "false")


def capture_extract_input(item: dict, text: str) -> None:
    """Attach the extraction input to this candidate's line. Never raises.

    Called from `classify` immediately before the one real extraction call, so
    what lands here is what the model was sent — up to provider-name
    redaction, which the committed repo requires and a replay must note.
    Silently a no-op for a candidate that was never gated, an unsampled key,
    a full per-run quota, or a disabled capture.
    """
    if not capture_enabled():
        return
    try:
        candidate_key = key(item)
        line = _BUFFER.get(candidate_key)
        if line is None or "extract_excerpt" in line:
            return
        if int(candidate_key, 16) % SAMPLE_1_IN != 0:
            return
        if STATS.get("captures", 0) >= EXTRACT_CAPTURE_PER_RUN:
            return
        line["extract_excerpt"] = provider_names.redact(
            str(text or "")[:EXTRACT_EXCERPT_CHARS])
        STATS["captures"] = STATS.get("captures", 0) + 1
    except Exception as exc:            # never fail a run over bookkeeping
        _fail("capturing an extraction input", exc)


#: How much of a rejection message the ledger keeps. Long enough to hold the
#: rule and its subject, short enough that a month of them is still a file.
REASON_MAX = 240


def outcome(item: dict, value: str, reason: str = "") -> None:
    """Close the join: what this candidate finally became. Never raises.

    Silently ignores a candidate that was never gated (a deterministic close, a
    derived row, an offline stub) — those never cost a gate call and are not
    part of the population the classifier replaces.

    `reason` is WHY, and it exists because twice in one investigation a story
    was lost and the record of the loss did not say why. 269 candidates carry
    `validate_reject` in the August 2026 shard and not one of them names the
    rule that refused it, so the only way to triage any of them is to re-fetch
    and re-run - which for the Spanish copy of OpenAI's $122bn close, the only
    copy of that round we ever saw, meant the biggest round of the year was
    unattributable from stored state. validate.Rejected has always carried the
    message; nothing kept it. The cost of keeping it is one string.
    """
    if not enabled():
        return
    try:
        line = _BUFFER.get(key(item))
        if line is None:
            return
        if line["gate"] in (NO, CLF_NO):
            # A gate NO is terminal: `classify` returned None and no later
            # stage ever saw this candidate. run_collect cannot tell that
            # rejection apart from an extraction NO — both arrive as
            # `classified is None` — so the rule lives here, where the verdict
            # is known. Without it every gate reject would be relabelled by the
            # generic reject branch and the ledger would hold no gate rejects
            # at all, which is the one class the classifier most needs.
            return
        line["outcome"] = value
        # Redacted like every other free text here, and not as a formality:
        # `validate` refuses aggregator hosts BY NAME, so the rejection message
        # for the one class of candidate most likely to mention a provider is
        # the one most likely to spell its domain.
        text = provider_names.redact(" ".join(str(reason or "").split()))
        if text:
            line["reason"] = text[:REASON_MAX]
        STATS["outcomes"] += 1
    except Exception as exc:
        _fail("recording a gate outcome", exc)


def month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def shard_path(month_key: str, *, compressed: bool = False) -> str:
    return os.path.join(LEDGER_DIR,
                        f"{SHARD_PREFIX}{month_key}.jsonl"
                        + (".gz" if compressed else ""))


def flush() -> int:
    """Append this run's labels and clear the buffer. Returns lines written.

    One open/write/close for the whole run rather than per candidate: the gate
    is a hot loop, and a file handle held open across a run of unknown length is
    a file handle held open across a crash.
    """
    if not _BUFFER:
        return 0
    if not enabled():
        _BUFFER.clear()
        _ORDER.clear()
        return 0
    lines = [_BUFFER[k] for k in _ORDER if k in _BUFFER]
    _BUFFER.clear()
    _ORDER.clear()
    if DRY_RUN:
        print(f"[gate-ledger] dry run — {len(lines)} gate label(s) not written")
        return 0
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        path = shard_path(month())
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
        STATS["written"] += len(lines)
        compact()
        return len(lines)
    except Exception as exc:
        _fail("writing the gate label shard", exc)
        return 0


def compact(now: datetime | None = None) -> list[str]:
    """Gzip closed months, delete shards past KEEP_MONTHS. Returns what changed.

    Runs on every flush rather than on a schedule, because a scheduled job is
    one more thing that can be evicted from the writer queue, and this is a few
    milliseconds of directory listing on all but one run a month.

    The OPEN month stays plain text on purpose: git delta-compresses an
    append-only text file almost perfectly, and a gzip member appended twice a
    day would store a fresh multi-megabyte blob in every commit.
    """
    notes: list[str] = []
    try:
        current = month(now)
        shards: dict[str, str] = {}
        for name in sorted(os.listdir(LEDGER_DIR)):
            if not name.startswith(SHARD_PREFIX):
                continue
            stem = name[len(SHARD_PREFIX):].split(".", 1)[0]
            if len(stem) != 7 or stem[4] != "-":
                continue
            path = os.path.join(LEDGER_DIR, name)
            if name.endswith(".jsonl") and stem != current:
                packed = path + ".gz"
                with open(path, "rb") as src, gzip.open(packed, "wb", 9) as dst:
                    shutil.copyfileobj(src, dst)
                os.remove(path)
                notes.append(f"compacted {name} -> {os.path.basename(packed)}")
                path = packed
            shards[stem] = path

        for stem in sorted(shards)[:-KEEP_MONTHS] if len(shards) > KEEP_MONTHS else []:
            os.remove(shards[stem])
            notes.append(f"dropped {os.path.basename(shards[stem])} "
                         f"(older than {KEEP_MONTHS} months)")
        for note in notes:
            print(f"[gate-ledger] {note}")
        return notes
    except Exception as exc:
        _fail("compacting the gate label shards", exc)
        return notes


def around_run(label="collect"):
    """Reset before an entry point, flush after it, whatever it returns.

    `record()` only BUFFERS. A caller that classifies and never flushes fills
    the buffer for a whole run and drops every line at process exit, in silence
    — the module cannot warn about it, because a run that gates nothing looks
    exactly the same from in here. That is not hypothetical: `classify()` has
    always recorded, but `backfill_sec_2026.py` and `backfill_form_d_2026.py`
    never flushed, so months of paid gate verdicts went to the buffer and
    nowhere else while the daily run's labels landed fine.

    So the pairing lives in ONE place and every entry point wears it, rather
    than each remembering two calls. `finally`, because the backfills return
    early on exhausted credits and on a refused auth, and the verdicts bought
    before that point are as real as any others.

    `label` may be a callable taking the wrapped function's kwargs, for a caller
    whose name is only known per invocation (run_collect logs its source).
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            reset()
            # A rehearsal buffers and counts but writes nothing. A caller that
            # only learns its own dry-run flag later (the backfills parse it
            # from argv inside the function) calls set_dry_run again itself;
            # this is the default, not the last word.
            set_dry_run(bool(kwargs.get("dry_run")))
            try:
                return fn(*args, **kwargs)
            finally:
                written = flush()
                if written:
                    name = label(kwargs) if callable(label) else label
                    print(f"[{name}] gate labels: {written} decision(s) "
                          "recorded for the classifier training set "
                          "(data/gate_labels/)")
        return wrapper
    return decorate


def read_all(directory: str | None = None):
    """Every label line in the ledger, oldest shard first, plain and gzipped.

    The reader every consumer shares — the merge tool, the tests, and whatever
    trains the classifier in step 2 — so none of them has to know that a closed
    month is compressed.
    """
    root = directory or LEDGER_DIR
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
            continue
        path = os.path.join(root, name)
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue

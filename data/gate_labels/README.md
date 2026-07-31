# Gate labels — the training set for the classifier gate

Step 1 of [docs/PLAN-gate-to-five-dollars.md](../../docs/PLAN-gate-to-five-dollars.md).
The paid LLM gate costs **$5.70/month on its own** — the cost of LOOKING at
~3,150 candidates a day to find the ~1,280 worth reading — which is more than
the owner's entire $5 target, so no model swap reaches it. The route under $5 is
a local classifier that answers most of those looks for free, and a classifier
needs labels. Until 2026-07-31 this pipeline kept only aggregate counters and
threw every per-candidate verdict away.

## Two files, and they must never be mixed

| | written by | features | contains gate rejects? | safe to ship against |
|---|---|---|---|---|
| `labels-YYYY-MM.jsonl[.gz]` | `pipeline/gate_ledger.py`, on every collect run | the headline and teaser the gate itself read | **yes** | **yes** |
| `bootstrap-weak.jsonl` | `bootstrap_gate_labels.py`, once, from `seen_urls` | a URL slug | **no** | **no — prototyping only** |

Every line carries a `basis` field (`gate_text` or `url_slug`) and the weak set
also carries `"weak": true`, so a training script that globs this directory can
separate them without knowing the filenames. **It must.** A model trained on the
two together learns to tell "has a real teaser" from "does not", which is a fact
about the file, not about the news.

## Line shape

```json
{"key":"a348a07d5cf6353f","ts":"2026-07-31T20:03Z","collector":"google_news",
 "host":"inc.com","lang":"en","country":"US",
 "headline":"Enigma Raises $71M in Seed Funding - inc.com","teaser":"",
 "gate":"YES","outcome":"stored","basis":"gate_text"}
```

- `gate` — the one-word verdict: `YES`, `NO`, `ERROR` (the gate failed open and
  never judged this candidate — **not** a YES), or `OFF` (single-stage run).
- `outcome` — what the candidate finally became: `stored`, `duplicate`,
  `retracted`, `gate_reject`, `model_reject`, `validate_reject`, `deferred`,
  `error`, `would_store` (a dry run), `unknown`.
- `key` — `sha1(source_url or discovery_url or headline)[:16]`, the same URL
  `run_collect` deduplicates on, so a line and a `seen_urls` row describe the
  same candidate.

**The classifier's target is `outcome == "stored"`**, not `gate == "YES"`. The
plan's shipping bar is measured against stored rows: *a replay over >=30 days in
which >=99.5% of candidates that ultimately produced a stored row are routed
relevant-or-uncertain.*

`deferred` is the one non-terminal outcome — a busy provider or the read-through
cap, where the candidate is deliberately not marked seen and returns on a later
run. That later run writes a second line under the same `key`. **Take the last
terminal outcome per key.**

## What is deliberately not here

No PII and no full article text: headline plus at most 300 characters of teaser,
markup stripped, both already public on the publisher's own page. Nothing
downstream of the gate — no extracted company, no model summary, no read-through
— is ever written, because a classifier trained on more than the gate gets would
score well here and fail in production.

## Size and rotation

Measured on 2026-07-31: 310 B/line over 504 live google_news items, 519 B over
253 national_press ones. That is ~0.6 MB/day at the rate actually observed
(1,894 gate calls) and 1.0–1.6 MB/day at the plan's full-coverage 3,150.

So the ledger shards by month, gzips a closed month on the first run of the next
one (~4x), and drops shards older than `TIT_GATE_LEDGER_KEEP_MONTHS` (6). The
**open** month stays plain text on purpose: git delta-compresses an append-only
text file almost perfectly, and a gzip blob rewritten twice a day would not
delta at all.

## Operating it

```bash
python3 bootstrap_gate_labels.py --dry-run   # what the weak set would contain
python3 bootstrap_gate_labels.py             # rebuild bootstrap-weak.jsonl
TIT_GATE_LEDGER=off python3 run_collect.py   # collect without recording labels
```

The ledger can never fail a collect run: every entry point swallows its own
exceptions, prints one loud line on stderr and disables itself for the rest of
the run. A dry run buffers and counts but writes nothing.

# Five throughput levers, measured and ranked

Read-only measurement pass, 2026-08-12. No behaviour changed, no model called,
no workflow run, no key used. Every figure below is either **MEASURED** (read
out of committed state, with the file named) or **MODELLED** (arithmetic on
measured inputs, with the inputs shown). Nothing is estimated from memory.

The question this answers: talent's coverage is capped by read throughput, not
by discovery — the US gap map says 26 of 30 US recall misses were *walked but
never read* and zero had no source. That scarcity is what forces
`pipeline/candidate_rank.py` to weigh country need, which is what makes lifting
the US come out of the other 104 countries' slots. **If throughput can be raised
by engineering, that fight dissolves.** So: what does each lever actually buy?

---

## The measured base

Two ledgers carry everything.

**`data/talent_intel.db`, table `source_health`** — 27 priced runs,
2026-07-30 to 2026-08-02 (this is every costed run that has ever happened):

| | |
|---|---|
| charged | **$3.8611** for **1,710 reads** producing **794 rows** |
| unit | $0.00226 per read, $0.00486 per stored row |
| prompt tokens | **7,918,361**, of which **0 served from cache (0%)** |
| gate calls | 4,347, of which 2,214 rejected |
| interpretations | 693 bought for 794 rows |

**`data/gate_labels/labels-2026-08.jsonl`** — 11,824 lines, 2026-08-01 to
2026-08-03, resolving to **9,089 candidates with a terminal outcome** (last
terminal outcome per key, as the ledger's README requires):

| gate verdict | n | share |
|---|---:|---:|
| `NO` — dropped by the paid gate | 3,713 | 40.9% |
| `YES` — **reached paid extraction** | 3,020 | 33.2% |
| `ERROR` — never judged | 2,356 | 25.9% |

And what became of the 3,020 that were paid for:

| outcome | n | share of paid extractions |
|---|---:|---:|
| `stored` | 1,834 | 60.7% |
| **`duplicate`** — an event we already held | **612** | **20.3%** |
| `model_reject` | 304 | 10.1% |
| `validate_reject` | 269 | 8.9% |

Language of those 3,020 paid candidates: **English 1,078 (35.7%), non-English
1,942 (64.3%)**. Hold that number; it turns out to be the root cause of two
separate levers.

Monthly projections quoted below come from `cost_projection.py` run today,
which labels its own figures. Its `[4]` table at today's caps: gate **$3.09**,
extraction **$14.82**, read-through **$13.00** — $30.90/month against an
allowance of $17.71 after discovery. Its `[5]` marginal read price is
**$0.00131**. Full coverage is 768 reads/day; today's caps buy 373/day.

---

## LEVER 1 — dedup before the model

**Talent already has three free dedup layers ahead of spend**, all in
`run_collect.py`: `store.already_seen(url)` (line 614), story clustering on
stated employer+amount (line 542), and `dedupe.funding_event_duplicate` fed by
`cheap_extract.parse_funding` (line 642) — the last one is the direct
equivalent of the sibling's seen-URL pre-check, and it is strictly better,
because it catches the *same round from a different outlet* rather than only the
same URL.

*(For comparison: the sibling's `railway/seen_urls.py` keys on the exact
`source_url` and its own docstring claims "~60% of daily extraction volume", but
that is a prose figure from four late-July runs — the ledger records `items`
**after** the filter, so the denominator is structurally absent and the hit rate
is not measurable from committed state. Talent's equivalent is measurable.)*

**The waste that survives all three: 612 of 3,020 paid extractions, 20.3%,
were spent on events already held.** They only became visible to the
content-hash and fuzzy layers *after* the extraction was bought.

Their languages: es 135, en+English 137, pt 63, it 51, tr 39, fr 38, sv 28,
others. **78% are non-English** — precisely the candidates
`cheap_extract.parse_funding` cannot read, because the module is English-only by
design (its rule 4).

| | |
|---|---|
| **Waste, MEASURED** | 20.3% of extraction spend |
| **$/month** | **$3.01** at today's caps (0.203 × $14.82); $5.53 at full coverage. MODELLED from the measured share × cost_projection's extraction line |
| **Extra reads/day** | **76** (MODELLED: $3.01 ÷ $0.00131 ÷ 30.4) |
| **Effort** | Medium. Not a new layer — a wider grammar for the existing one: match `(company_key, amount_canon)` in the Romance languages, Turkish and Swedish |
| **Coverage cost** | Small but **real and must be measured**: a looser cross-language match can collapse two genuinely distinct rounds. Not a free win; needs a hand-checked sample at the existing 31/31 bar |

---

## LEVER 2 — a cheap gate before the expensive read

**Both trackers already run one, and on the same model.** The sibling's gate is
not free either: `railway/extractor.py` calls
`google/gemini-2.5-flash-lite` per candidate at ~$0.000049/call. Its
google_news 990/219 and gdelt 57/683 are a 2026-08-10 snapshot of
`railway/spend_jobs.json`; the current file reads google_news 1,376/325 (19.1%
dropped) and gdelt 77/1,046 (**93.1% dropped**).

Talent's equivalent is two-stage and better instrumented:

| stage | mechanism | drop | cost |
|---|---|---|---|
| `pipeline/prefilter.py` | free keyword vocabulary | 3,898 → 1,800/day (53.8%) | **$0** |
| `classify.gate_verdict` | gemini-2.5-flash-lite, one word | 1,800 → 768/day (57.3%) | $3.09–5.70/mo |

So there is no missing gate here. The remaining lever is **replacing the paid
gate with the trained classifier** — `docs/PLAN-gate-to-five-dollars.md` step 2,
runtime already written (`pipeline/gate_classifier.py`), trainer already written
(`train_gate_classifier.py`), weekly workflow already scheduled
(`gate-classifier.yml`, Tuesdays). **`data/gate_classifier/` does not exist**,
so it has never armed, and `STATS["clf_relevant"]`/`clf_irrelevant` are zero.

| | |
|---|---|
| **Saving, MODELLED** | gate $3.09 → ~$1.20 (the plan's own 80%-coverage figure, itself unmeasured) = **$1.89/month** |
| **Extra reads/day** | **47** |
| **Effort** | **High.** The last mile of an ML step: train, hold out, pass the ≥99.5% replay bar, arm |
| **Coverage cost** | Bounded by design — only the confident-IRRELEVANT band can cost recall, and the module refuses to route on an artifact whose replay report is missing, stale or under the bar |

### False drops — the part that matters, and it is thin on both sides

A gate that discards real events buys throughput by losing coverage, so this
was checked hard.

- **Sibling, MEASURED once and only once:** `docs/TECHLOG.md` 2026-08-06 —
  **103 shadow `NO` verdicts, 0 false drops**, from two metered runs over about
  thirteen hours. No entry in `railway/spend_jobs.json` carries a
  `gate_false_drops` key. There is no gold set, no labelled sample, no audit
  script, and no re-audit of the ~1,481 drops accumulated since.
- **The unaudited number is the big one.** gdelt's drop rate was 84% in shadow
  and is **93.1% in live**, and in live a false drop is invisible by
  construction: extraction never runs, so nothing can contradict the `NO`.
  Mitigation that is real: gate rejects are never marked seen, so a wrong `NO`
  is re-pulled and re-judged.
- **Talent has no false-drop audit of its LLM gate either** — but unlike the
  sibling it *can* have one cheaply, because `gate_ledger.py` already records
  every `NO` with the headline the gate read. **3,713 drops are sitting in
  `labels-2026-08.jsonl` waiting to be sampled.** That audit is an afternoon and
  it is a precondition for lever 2, not a follow-up.

### The 25.9% nobody has costed

**2,356 of 9,089 candidates hit gate `ERROR`** — all of them google_news. Per
the ledger's own README an ERROR is "the gate failed open and never judged this
candidate — **not** a YES". Those candidates were not judged, not read and not
marked seen. That is a throughput loss of the same order as lever 1, it costs
nothing to fix, and it appears in no cost model. **It should be triaged before
any of the five levers is built.**

---

## LEVER 3 — deterministic extraction where the format is structured

**The talent case is completely different from the sibling's, and the
difference is a factor of about sixty.**

The sibling measured a no-model SEC Item 2.05 parser at **75.4% recall / 100%
precision** (43/57, Wilson [62.9, 84.8] and [91.8, 100.0]) and it saved about
six cents — because Item 2.05 filings are **15 of 271 documents a sweep reads,
5.5% of volume, on a one-dollar bill**. Its own conclusion was to keep the
parser as a precision instrument and not as a saving. That is the right call
*there*.

Here, `pipeline/cheap_extract.py` is already live and already productive:
**950 stored rows carry `EVIDENCE_NOTE`** — no model ever read them. In the
priced window it closed **53.8% of google_news funding rows** for $0.

But look at where it does not fire:

| collector | pillar | rows stored (priced window) | closed free | % |
|---|---|---:|---:|---:|
| google_news | **leadership_change** | **1,153** | **0** | **0.0%** |
| google_news | company_development | 645 | 347 | 53.8% |
| google_news | how_we_work | 288 | 0 | 0.0% |
| gdelt | leadership_change | 150 | 0 | 0.0% |
| national_press | leadership_change | 103 | 1 | 1.0% |

**Leadership is 1,406 of the 3,047 news rows stored in the priced window — 46%
of paid volume — and free extraction closes none of it.**

This is not a missing parser. `_parse_leadership` exists, was committed
2026-07-29 (`9c2353e`, "leadership joins the deterministic extractor"), and ran
in production for the whole priced window. It closed **zero**.

**Root cause, and it is the same one as lever 1:** `cheap_extract` is English
only, deliberately (rule 4 — the name-span validation leans on English
capitalisation). The leadership rows are not English. Country mix of
google_news leadership rows: FR 131, SE 105, ES 88, BR 87, IT 82, DE 63,
KR 62, IL 57, TR 55, NL 34, JP 33. And 64.3% of everything that reaches paid
extraction is non-English.

| | |
|---|---|
| **Opportunity, MEASURED share** | 46% of paid news volume, at 0% free closure today |
| **$/month, MODELLED** | if leadership closed at funding's own measured 53.8%: 0.46 × 0.538 = 24.7% of extraction → **$3.66/month** at today's caps, $6.73 at full coverage. A free close also skips the gate call and the read-through, so this is a floor |
| **Extra reads/day** | **92** |
| **Effort** | Medium-high. Per-language appointment grammar for ~8 languages, at the existing hand-check bar |
| **Coverage cost** | **None.** The module declines on ambiguity and the candidate takes the paid path unchanged; output goes through the same `validate → store` path. The risk is a wrong $0 extraction, which the 31/31 sampling bar exists to catch |

**This is the largest lever that costs no coverage.** The sibling's 6-cent
result does not transfer and should not be used to argue against it.

---

## LEVER 4 — prompt caching

**Plainly: it is worth exactly $0 today, the 0% is correct behaviour rather
than a defect, and there is a specific reason for each of the three prompts.**

Measured prefix sizes (from the modules' own `stable_prefix()` accessors, which
exist so this claim stays checkable):

| stage | model | stable prefix | cacheable? |
|---|---|---:|---|
| gate | gemini-2.5-flash-lite | 869 chars ≈ **217 tok** | **No** — under Gemini's 1,024-token implicit-cache floor |
| **extraction** | **deepseek/deepseek-chat** | **11,016 chars ≈ 2,754 tok** | **Above every floor — and there is no cache to hit** |
| read-through | claude-sonnet-5 | 1,193 chars ≈ **298 tok** | **No** — under Sonnet's 1,024-token minimum |

The one prompt with a big enough prefix is on the one model that prices no
cache read. `data/model_prices.json` (committed):

```
deepseek/deepseek-chat        prompt 2.574e-7   cache_read: null
deepseek/deepseek-chat-v3.1   prompt 2.5e-7     cache_read 1.3e-7   (0.52x)
google/gemini-2.5-flash-lite  prompt 1.0e-7     cache_read 1.0e-8   (0.10x)
anthropic/claude-sonnet-5     prompt 2.0e-6     cache_read 2.0e-7   (0.10x)
```

`pipeline/classify.py` records the check behind that null: OpenRouter's
endpoints API for the slug returned three endpoints — streamlake, deepinfra/fp4,
novita/fp8 — and **not one publishes an `input_cache_read` price**. There is no
cache to hit. `PROVIDER_ORDER` is already pinned so the prefix stops scattering
the day a caching endpoint appears, and the prompt is already cache-shaped
(system + `SCHEMA_HINT` first, item text last), with a test pinning the shape.

**So the engineering effort for this lever is zero and always was. It arrives
free the day the extraction model changes.**

What it would then be worth. MODELLED, inputs shown: 1,710 measured extraction
calls × 2,754 measured prefix tokens = **4,709,340 prefix tokens = 59.5% of all
7,918,361 prompt tokens ever sent by this project.**

| if extraction moved to | saving per call | as % of the extraction call |
|---|---|---|
| deepseek-chat-v3.1 | 2,754 × 1.2e-7 = $0.000330 | ~32% |
| gemini-2.5-flash-lite | 2,754 × 9.0e-8 = $0.000248 | ~68% |

Against `cost_projection [4]`: extraction at full coverage is $27.23 on
deepseek-chat and **$4.78 on flash-lite**. Caching then takes that ~$4.78 to
roughly **$1.5–2**.

| | |
|---|---|
| **$/month today** | **$0.00** |
| **$/month after the extraction switch** | ~$3 at full coverage, as a rider |
| **Extra reads/day today** | **0** |
| **Effort** | **Zero** — already built |
| **Coverage cost** | None |

**The suspicion that this was the big one is understandable and it is wrong,
but it points at the right place.** The switch that *unlocks* caching is worth
about six times the caching: $27.23 → $4.78/month at full coverage. That switch
is gated on `ab_models.py --extraction`, which **has never been run**. It needs
`OPENROUTER_API_KEY`, so its result is **UNKNOWN from this pass** — but it costs
cents and it is the single largest unmeasured item on the board.

One caveat kept honest: a prefix above the floor is a *necessary* condition for
a cache hit, not a sufficient one. Whether flash-lite's implicit cache actually
warms at this call rate is a provider behaviour that would have to be observed
in `cached_tokens` after the switch, not assumed. `STATS["cached_tokens"]` and
`ops_status [2a]` already report it per run, so the check is free.

---

## LEVER 5 — earned cadence

**Measured, and the answer is no. This one should not be built.**

There is no persistent zero-yield paid source. Rows bought per read, all time:

| collector | reads | rows | rows/read |
|---|---:|---:|---:|
| google_news | 1,098 | 495 | 0.451 |
| national_press | 524 | 260 | 0.496 |
| gdelt | 70 | 30 | 0.429 |
| sec_edgar | 12 | 5 | 0.417 |
| sec_form_d | 6 | 4 | 0.667 |

That is a flat distribution. There is nothing to demote.

At the publisher level, over the 9,089-candidate ledger window covering **4,136
distinct hosts**:

| demotion rule | hosts | paid reads reclaimed | share of all paid reads |
|---|---:|---:|---:|
| ≥5 candidates, zero stored | 131 | 118 | **3.9%** |
| ≥10 candidates, zero stored | 35 | 50 | **1.7%** |
| ≥20 candidates, zero stored | 9 | 28 | 0.9% |

And the reason the tail is thin is the reason it must not be cut: **1,834
stored rows came from 1,188 distinct hosts, and the top 400 hosts account for
only 57% of them.** The long tail *is* the international coverage this product
is measured on and short of. Demoting a low-volume publisher in a country
holding one row is exactly the coverage `data/recall_worklist.json` is asking
for.

| | |
|---|---|
| **$/month** | $0.25–$0.58 (1.7–3.9% of $14.82) |
| **Extra reads/day** | **6–15** |
| **Effort** | Medium |
| **Coverage cost** | ⚠️ **REAL, and disproportionate to the gain. This lever raises throughput by dropping real events and must be marked as such.** |

The sibling reached the same conclusion independently and encoded it:
`railway/spend.py` `earned_skip()` slows only five *queue-draining* jobs and
**refuses to slow any discovery job whatever the ledger says**, on the stated
reasoning that a queue is still there next run but a short-lived page is not.
Its own zero-yield flagship, `company-watchlist` (16 runs, one row ever), turned
out on investigation to be measuring **a bug, not a collector** — a `queries`
argument that was accepted and ignored, so the company-targeted query was never
sent. That is the failure mode of yield-based demotion in one sentence: it
punishes broken plumbing by reading it less.

---

## The ranking, by measured value per pound

| # | lever | $/mo | +reads/day | effort | coverage cost |
|---|---|---:|---:|---|---|
| **1** | **Deterministic extraction, non-English leadership** | **$3.66** | **92** | Med-high | **None** |
| **2** | **Cross-language duplicate pre-check** | **$3.01** | **76** | Medium | Small, measurable |
| **3** | **Prompt caching** | **$0.00** | **0** | **Zero** | None |
| 4 | Classifier gate | $1.89 | 47 | **High** | Bounded by the replay bar |
| 5 | Earned cadence | $0.25–0.58 | 6–15 | Medium | ⚠️ **Drops real events** |

**Build 1 and 2 together.** They are one piece of work: both are blocked by the
same fact — `cheap_extract` is English-only and 64.3% of paid candidates are
not English — and both are unblocked by the same non-English company/amount/
title grammar. Together: **$6.67/month, ~168 extra reads/day, no coverage lost.**

**Lever 3 needs no build at all.** It is already shipped and dormant.

**Do not build lever 5.**

**But do all of that only after two things that are cheaper than any of it:**

1. **Run `ab_models.py --extraction`.** Costs cents. Has never run. Worth
   $22.45/month at full coverage on its own ($27.23 → $4.78), and it is what
   turns lever 3's zero into ~$3. Larger than all five levers combined.
2. **Triage the 25.9% gate ERROR rate.** 2,356 candidates in three days that
   were never judged and never read. Free.

---

## The headline answer

**With the best levers built, how many candidates a day can talent read on $18?
Enough — and the honest finding is that money was never the binding
constraint.**

`cost_projection.py [4]`, run today, at full worldwide coverage of **768
reads/day**:

| configuration | $/month | fits $17.71? |
|---|---:|---|
| today | 54.20 | no |
| second pass conditional (shipped) | 33.18 | no |
| **+ extraction on gemini-2.5-flash-lite** | **10.73** | **yes** |
| + both cheapest models | 8.58 | yes |
| + all of it, cheapest models | 5.24 | yes |

**Full coverage already fits inside $18 — without any of the five levers — if
the extraction A/B passes.** Today's $18 buys 373 reads/day against demand of
768. The flash-lite switch alone buys all 768.

**And at full coverage the product fight dissolves completely.**
`candidate_rank` only matters when `READTHROUGH_CAP` binds; when every gate
survivor is read, the permutation it produces is irrelevant. The US is ~7% of
candidates (653 of 9,089 over three days; 280 paid reads), so lifting it takes
nothing from the other 104 countries when everybody is read. **The 26 US misses
that were "walked but never read" get read.**

### Four things that must be said plainly

1. **The A/B might fail.** Its bar is ≥98% agreement on the six deciding fields
   with every disagreement hand-read, and accuracy is the moat. If flash-lite
   fails it, full coverage on deepseek is $33.18 against $17.71, and levers
   1+2+4 together are worth ~$8.5/month — closing about half of the remaining
   $15.5 gap. **Then it is genuinely a product decision again, and the owner
   needs to know that the decision hangs on one measurement nobody has taken.**

2. **768/day is TODAY's demand.** `data/recall_worklist.json` is an instruction
   to add feeds, and every country added raises the denominator. A configuration
   that fits at 768 is not proof it fits at 1,500.

3. **The allowance is not currently reaching daily collection at all, and this
   is worth more than levers 2, 4 and 5 combined this month.** `source_health`
   accounts for only **$0.88 of August's $18**. Paid reads have been off since
   2026-08-03 — the ledger shows `DEGRADED: monthly allowance spent` on every
   collect run for **10 of the month's 12 days**, deferring 1,100–1,200
   candidates per national_press run — because roughly twenty google_news
   backfill slices ran on 2026-08-03 and took the month. **No engineering lever
   changes anything while the month's money goes to backfill first.** That is a
   scheduling decision, not an engineering one.

4. **Two numbers in the framing could not be reproduced from committed state.**
   "37 candidates a day out of about 395 available" does not appear in any
   ledger, and I did not find its basis. The measured equivalents are: read
   ration **217/run** held at `classify.BINDING_READ_BUDGET` (google_news 99 +
   national_press 118), affordable **373/day** at $18, demand **768/day**, and
   **actual reads over the last ten days: zero**. If 37/395 came from a
   different basis it is worth reconciling before it is planned against.

---

## What was not measured, and why

- `ab_models.py --extraction` and `--cache-check` both need
  `OPENROUTER_API_KEY`. **UNKNOWN from this pass.** Their results decide the
  largest item on the board.
- Whether flash-lite's implicit cache actually warms at this call rate is a
  provider behaviour, observable only after a switch. `cached_tokens` already
  reports it.
- The sibling's per-source health ledger is not committed — it is POSTed to the
  live host — so its per-collector staleness is UNKNOWN from committed state.
  Its spend ledger `railway/spend_jobs.json` is committed and was used instead.
- Attribution of the sibling's August OpenRouter burn: its committed job ledger
  accounts for $1.94 against an $18.44 balance decline, because `railway-cron`
  runs under a second key and `edgar-history-sweep` writes no ledger entry.
  Flagged there, not this repo's problem.
- No competitor or commercial data service is named anywhere in this document,
  in either repo, or in any figure above.

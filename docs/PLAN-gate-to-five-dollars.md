# The road to $5/month: replace the paid gate, in that order

Written 2026-07-31 by a Fable session at the owner's request, as standing
instructions for the next session (any model). The owner's target is
**total LLM spend ~$5/month at full worldwide coverage**. This plan is the
only honest route there, and it is deliberately sequenced so that every step
is MEASURED before the next is built. The context that justifies each number
is in docs/TECHLOG.md (2026-07-29/30/31) and cost_projection.py.

## Where the money is, measured

The ladder as of 2026-07-31, from cost_projection.py (labels MEASURED /
COUNTED / MODELLED are in that file):

    full coverage, read-late                      $100.99 / mo
    second pass CONDITIONAL      (SHIPPED)          $59.29
    + extraction on gemini-2.5-flash-lite           $18.79   <- blocked on one A/B
    + leadership free + funding cheap_extract        $9.31   <- the current floor

**The $5 blocker is the gate: $5.70/month on its own** - the cost of LOOKING
at ~3,150 candidates/day with an LLM to find the ~1,280 worth reading. No
model swap fixes that; the gate must mostly stop being an LLM.

Discipline note, learned expensively this week: 40% free extraction turned
out to be 2.5%; prompt caching turned out to be $0; the batched gate turned
out to be $1.66. Unmeasured levers disappoint. Every acceptance bar below is
therefore a measurement, not a judgement call.

## Step 0 - Run the extraction A/B. Biggest money, zero architecture.

`ab_models.py --extraction` already exists and has never run. It compares
deepseek-chat vs gemini-2.5-flash-lite field-by-field on the six fields that
decide a record (company, amount, currency, country, pillar, direction),
using the production SCHEMA_HINT.

- Run it over >=200 stored rows' raw text. Costs cents. Needs
  OPENROUTER_API_KEY (present in Actions; not local).
- ACCEPTANCE: >=98% agreement on the six fields, with every disagreement
  hand-read - and score a disagreement FOR flash-lite when it is right and
  deepseek was wrong (that happened in the sonnet/deepseek comparison).
- If it passes, switch extraction. $59.29 -> $18.79. If it fails, STOP and
  report; do not take the swap on vibes. Accuracy is the moat; $40/mo is not.

## Step 1 - Start persisting gate labels TODAY. The classifier needs food.

VERIFIED 2026-07-31: pipeline/classify.py keeps only aggregate counters
(STATS["gate_calls"], STATS["gate_rejects"]). Per-candidate verdicts are
NOT stored anywhere. The training set does not exist yet, so:

- Append one JSONL line per gate decision to a committed ledger
  (data/gate_labels.jsonl or sharded by month): headline, teaser (first
  ~300 chars), language, source collector, country, gate verdict, and -
  when the candidate goes further - the extraction outcome (stored /
  extract-rejected) keyed so the two can be joined later.
- NO PII, no full article text. Headline+teaser is what the gate itself
  sees, so it is exactly the feature set the classifier will get.
- This rides the same commit-the-database step the writers already have.
  It is a writer: queue it via drain-writers rules like everything else.
- ALSO bootstrap a weak historical set now, without waiting: stored rows
  (positives, we have ~18k) vs prefilter-passed-never-stored candidates
  from seen_urls (weak negatives). Good enough to prototype; NOT good
  enough to ship against - it lacks true gate-rejects.

Two to four weeks of real labels is enough. The step costs nothing.

STATUS 2026-07-31: shipped, then found half-wired and completed. The ledger is
pipeline/gate_ledger.py and the daily collectors (collect.yml,
collect-press.yml, collect-structured.yml) had all of it. The five backfills
had none of it: they classified, so they BUFFERED labels, but none imported
gate_ledger to flush them and none merged the directory back after their
commit step's `git reset --hard`, so every verdict they paid for was lost
twice over. Both halves are fixed and two invariant tests now derive the list
of affected scripts and workflows from the code rather than from a list kept
by hand. Practical effect on this step: backfill slices now contribute labels
too, so the "two to four weeks" is a ceiling rather than an estimate.

## Step 2 - The local classifier gate, fail-open, replay-gated.

The architecture change that gets under $5:

- A small text classifier - scikit-learn logistic regression over character
  3-5-grams plus word unigrams (char n-grams because 43 languages, no
  tokenizer fights; Hebrew/CJK lessons in TECHLOG apply). Artifact committed
  to the repo (<5MB), runs on the free CI runner. NO new paid dependency,
  NO fastText unless it is genuinely easier - argue it if so.
- THREE-way routing, and this is the safety design:
    confident-RELEVANT   -> skip the LLM gate, straight to extraction
    UNCERTAIN            -> LLM gate exactly as today
    confident-IRRELEVANT -> drop (the only risky class)
- Thresholds chosen on a held-out month so the confident-IRRELEVANT class
  contains as close to ZERO eventually-stored stories as the data allows.
- **SHIPPING BAR, non-negotiable: a replay test over >=30 days of real
  labels in which >=99.5% of all candidates that ultimately produced a
  STORED row are routed relevant-or-uncertain.** If the classifier costs
  even a fraction of a recall point, it does not ship. Recall is the moat;
  the gap this whole plan closes is ~$4/month. The owner would rather pay
  $9 than dent the recall number - he has said the number is the product.
- Fail-open everywhere: model file missing, load error, timeout, new
  language it has never seen -> route to the LLM gate. A classifier failure
  must never become a silent drop. (This project's signature failure is
  the thing that looks healthy while broken - see TECHLOG 2026-07-29.)
- Monthly retrain from the rolling ledger, with the SAME replay test as a
  CI gate on the new artifact. Drift alarm: if the UNCERTAIN share rises
  past ~35%, alert - that is vocabulary drift or a new language, and it
  quietly re-inflates the LLM gate bill.

Expected effect if the confident bands cover ~80% of candidates (to be
MEASURED, not assumed): gate $5.70 -> ~$1.20/mo.

## Step 3 - Free extraction takes funding's easy half.

cheap_extract already closes 33.2% of stored funding rows from the headline
alone (measured; 15x its all-pillar rate). The declines are precision guards
firing - a comma inside a name span, title-case headlines. Extend it
carefully toward 40-50% of funding stories, at the existing hand-check bar
(31/31 correct; keep that standard, sample every change). Every free close
skips gate AND extraction AND read.

## Step 4 - Route filed pillars away from paid reads, per country.

Leadership and hiring are moving to registries (7 live; Spain landed
2026-07-30, ~12,700 events/yr free; DK CVR is one owner email away) and job
boards (286 verified, 29,869 roles). The routing rule: skip the paid read
for a leadership story ONLY where the employer's country has a live registry
collector - elsewhere the news path is the only source. Do not blanket-skip:
that is how Boeing-shaped suppression happens (TECHLOG, superset incident).

## Step 5 - Batch the residual LLM gate. Only if trivial.

After step 2 the LLM gate handles only the uncertain band. Prefix share is
43%, so batching 10-20 headlines/call saves about a third of what remains -
pennies at that point. Do it only if it is an hour's work.

## The honest arithmetic, with the disappointment discount applied

    gate        $5.70 -> ~$1.20   (IF classifier covers 80%; measure)
    extraction $47.90 -> ~$7.40   (flash-lite, IF the A/B passes)
                       -> ~$4-5   (after free extraction + routing)
    read-through        ~$1-2     (conditional, smaller population)
    ------------------------------------------------
    likely landing      $7-9/month
    stretch case        ~$5       (classifier >=85% coverage AND free
                                   extraction >=50% of funding)

State it to the owner this way. Do NOT promise $5. The week's record says
unmeasured levers land short; $7-9 is the number to plan against, $5 is the
stretch. If a step's measurement kills it, say so and stop - a truthful $8
beats an optimistic $5 that costs recall.

## Process rules (all learned the hard way this week; do not relearn)

- Isolated git worktree at origin/main for ALL work; a reset --hard in the
  shared checkout destroyed nine commits on 2026-07-30.
- Small commits, pushed often; three agents died mid-task on API 500/529s.
- Writers queue via drain-writers.yml, never direct dispatch (15 silent
  evictions). deploy-plugin.yml and correct-*.yml DEFAULT to dry_run=true -
  a plain dispatch is a green run that ships zero bytes.
- Verify live by curl, never by a green tick. Sample checks miss things
  (the 22 sitemap 301s survived a 20-URL sample); fetch exhaustively where
  it is cheap.
- Pace anything that hits the WordPress host; it fell over twice on
  2026-07-30. Stop on 504s.
- Every cost claim goes into cost_projection.py labelled MEASURED / COUNTED
  / MODELLED, and spend.py's comment ladder gets updated with the reasoning.

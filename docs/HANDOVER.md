# Handover — Talent Intelligence Tracker

---

## 2026-08-12: the door for the wrong-country correction is open in the code (1.77.0). PUSHED, NOT DEPLOYED. The 37 are still wrong on the live page.

Branch `main`. Plugin change plus version bump plus one TECHLOG entry. **No
deploy was run and no reversal was run**, because the deploy is the owner's
call and not an agent's (CLAUDE.md says so by name, and the 2026-08-01 incident
is why). Nothing was spent; this correction removes values and never looks one
up.

**Three commands, in this order, and nothing else is outstanding.**

```bash
gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker \
  --ref main -f dry_run=false
# then bare URL, browser UA, NO cache buster; assets stamp TIT_VERSION.mtime,
# so match the "1.77.0." prefix

gh workflow run drain-writers.yml -f enqueue=reverse-cityless-hq.yml \
  -f inputs_json='{"dry_run":"false"}' -f reason='take back the cityless hq'
# then check the live page: Synthesia must no longer read CZ

gh workflow run drain-writers.yml -f enqueue=enrich.yml \
  -f inputs_json='{"dry_run":"false"}' -f reason='carry the city-backed places'
# LAST. Running it before the reversal pushes MORE of the 37 to readers.
```

**What changed in the code.** `tit_clearable_columns()` in `includes/api.php`
now returns `hq_city` and `hq_country` as well as the two funding columns, so
`/enrich` has a route to blank a headquarters that was never really looked up.
The reasoning is in the function's docblock: these 37 came off the entity-P17
fallback with no headquarters city behind them, there is no right value to send
instead, and the only correction available was a clear. A clear still has to be
named explicitly, so an absent or empty field still erases nothing, and
`archive_url` stays outside the allowlist.

**The measured numbers, read off the LIVE endpoint and not from the database.**

| | events |
|---|---:|
| a US-filtered reader sees today | **7 of 51** |
| after the reversal | **6 of 51** |

AlphaSense, Ramp, Ollin Biosciences, Databento, RapidPulse, Singularity,
Crystalys Therapeutics. **Databento is one of the 37** and reads
`hq_city=None, hq_country=US` live, so the reversal costs a visible row on
purpose: 6 honest beats 7 where one is accidentally right. It returns at 7
city-backed (Boston) once `enrich.yml` carries the 33 waiting placements.
AlphaSense reads `hq_city=New York` and is not affected.

Synthesia still reads `hq_country=CZ` on the live page as of this handover, on
page 2 of a Czechia filter.

**36 of the 37 are live; the 37th never published.** Checked row by row against
`talent/v1/query` on `signal_id` (which is the `content_hash`). The exception is
the second Synthesia row, `89aae556`, from `press_archive`. So expect the run to
report 37 reversed locally and 36 changed on the site, and read that as the
expected shape rather than a discrepancy. Nothing in the list has drifted since
it was written: no row has since gained a city or a different country.

**The refusal test was inverted, not deleted.**
`test_the_refusal_is_still_correct` asserted `not rev.site_can_clear()`; it now
asserts `rev.site_can_clear()` and guards the door against being shut again.
Keep it until every row in `data/cityless_hq_to_reverse.json` is reversed on
the site and the file is retired.

**The lesson from the incident, which is the part worth carrying forward:** a
cancelled GitHub job still completes the step it is already running. The
placement run was cancelled within minutes and its commit step had already
started, so the bad rows landed on main anyway. Cancel is a promise about the
NEXT step.

`is_placeable` was not widened and the placement backfill was not re-run. A
better placement pass is separate work and needs the owner's sign-off on the
bar.

---

## 2026-08-12: a US reader sees 5 of the 21 events we hold. The ingest cause is fixed; the backfill is queued, not run.

Branch `fix/place-the-unplaced`. **No plugin change, no version bump, no
deploy.** No model was called and no money was spent. Full reasoning and every
number is in TECHLOG under this date.

**The headline: 5 of 51, now 7 on the live site, and 6 once the reversal
below runs.** Free bought one event that survives its own bar: AlphaSense. Applying the plugin's own clause — `country IN
('US') OR (country IS NULL AND hq_country IN ('US'))` — to the 21 US funding
events the sealed recall set says we hold: 5 visible, 13 carrying no place at
all, 3 filed under the publisher's country (BR, ZA, ZA). Site-wide the
placeless state is 1,666 rows held by 1,633 employers.

Four things a next session needs:

1. **The ingest cause was a cache nothing fills.** `build_signal` consulted the
   employer identity cache and never filled it; no workflow has ever run
   `python -m pipeline.identity --backfill`, so 12,881 of 16,597 employer keys
   have no cache row and the lookup was a guaranteed miss for every new
   employer. Fixed by `identity.place_if_unplaced` — one free network
   resolution, but only for a row that would otherwise carry no country in
   either column. Red-then-green in
   `tests/test_unplaced_rows_get_placed.py`.
2. **Do not let the identity spine loose on history without `is_placeable`.**
   Two bars, both bought by measurement. Two organisations of the same name is
   a notability coin flip (Synthesia resolves to the Czech chemical works, BKV
   Corporation to a Hungarian political party). And a country with no curated
   headquarters city behind it is a hint rather than a fact: that half of the
   resolutions contains **Premier Lacrosse League as Canada**, which is one of
   the 13 US events a reader cannot see. The general `--backfill` is unchanged;
   only the placement paths refuse.
3. **The backfill HAS been run, twice, and it is finished with the free
   route.** 1,666 placeless rows became 1,573; 93 rows placed; 71 carried to
   the live site by `/enrich`; $0.00 spent. Re-run it any time — the worklist
   shrinks now — but 1,545 employers remain and Wikidata does not know them.

   ```bash
   gh workflow run drain-writers.yml -f enqueue=place-unplaced.yml \
     -f inputs_json='{"dry_run":"false","limit":"700"}' \
     -f reason='why'
   ```

   Never dispatch it directly, and keep the limit inside the 90 minute lock
   window: resolution is about 7 seconds an employer. It fills
   `hq_city`/`hq_country`, both already in `tit_enrichable_columns()`, so the
   values reach readers through `/enrich` with **no deploy**.
4. **The $2.14 the owner authorised was NOT spent, and the honest next step is
   $0.13.** No `OPENROUTER_API_KEY` exists in a subagent session, so a paid
   re-read must run on Actions. Before buying 1,666 of them, buy 100: a free
   probe of the 16 US rows found 6 of them **robots-disallowed** and 5 whose
   page states no place, so the fetchable yield is the thing to measure rather
   than assume. And never ask a model where a company is headquartered from its
   name alone — it will answer for all 1,666 and sound certain.

**THE ONE THING THAT NEEDS THE OWNER, and it needs a deploy.** 37 rows are
live carrying an `hq_country` with no headquarters city behind it, written by
the first run of the pass before its bar was tightened and cancelled too late
to stop the commit. Synthesia, the UK company that raised GBP 146m from GV, is
filed under Czechia on the public page. The correction exists
(`reverse_cityless_hq.py`, `reverse-cityless-hq.yml`, all 37 named in
`data/cityless_hq_to_reverse.json`) and it REFUSES to run, because `/enrich`
cannot blank `hq_country`:

```
1. tit_clearable_columns() in includes/api.php must return 'hq_city', 'hq_country'
2. bump Version: and TIT_VERSION, deploy, verify the page
3. gh workflow run drain-writers.yml -f enqueue=reverse-cityless-hq.yml \
     -f inputs_json='{"dry_run":"false"}' -f reason='take back the cityless hq'
```

`tests/test_reverse_cityless_hq.py::test_the_refusal_is_still_correct` goes red
the moment step 1 lands, which is the signal to do step 3.

**And 33 employers are placed locally and NOT on the site.** The second
placement run's own `/enrich` hit a WordPress 503, and the retry was cancelled
on purpose rather than re-run: `/enrich` sends every enrichable column for
every row, so re-running it before the reversal would push MORE of the 37
cityless values to readers. Those 34 rows are all city-backed and correct;
they are waiting on the same deploy. Once the reversal has run:

```bash
gh workflow run drain-writers.yml -f enqueue=enrich.yml \
  -f inputs_json='{"dry_run":"false"}' -f reason='carry the city-backed places'
```

**The 3 misfiled rows need a plugin change and this session did not make one.**
`country_basis=any` in `includes/api.php` is a FALLBACK; making it a real union
of job location OR employer HQ, as the sibling's already is, would recover
them. Stated, not made: a plugin change is a deploy, and the deploy is the
owner's call.

---

## 2026-08-12: the 30 US misses are placed. It is the budget. ON A BRANCH, NOT MERGED, NOT DEPLOYED.

Branch `triage/us-recall-misses`, stacked on `measure/us-recall` (PR #15), which
is itself unmerged. No plugin change, no version bump, no deploy.

**Read this before planning any coverage work.** Full reasoning and every number
is in TECHLOG under this date. The four things a next session needs:

1. **26 of the 30 misses are the read ration, not a missing source.** Zero of
   the 30 are a publisher no route can reach. `rejection_audit.py` now reads
   `data/backfill_state.json`, so a day a walker has FINISHED no longer reads
   as a day we could not reach — which moved the US set from 28
   `outside_our_history` to 2, and the WORLDWIDE set from 87 to 9. Dispatching
   more slices walks past these events again; only depth reaches them, and
   depth is money.
2. **The missing-country defect does not understate the 41.2%.** Checked two
   ways, both in TECHLOG. What it does is worse: the number a reader sees under
   a US filter is **5 of 51**, not 21 of 51. Closing that gap needs no new
   event and buys more than any walk.
3. **There is no free fix for the missing place.** Measured, not assumed:
   `pipeline/identity.py` resolves 2 of the 16 defective employers and gets one
   of those two wrong. Wikidata does not know seed-stage private companies.
4. **90% is not substantiable.** `candidate_rank` deliberately ranks the US
   last, since it scores by country need and the US is the least needy country,
   so US recall is low partly by design. The bound nobody can narrow from here
   is 41.2% to 96%; plan against 65 to 70% and read it as the assumption it is.
   The defensible next number is whatever a full-depth re-walk of the 61-day
   window measures, and that walk costs $5.35 at the walker's own published
   prices.

---

## 2026-08-12: US recall measured, 21/51 with a range (1.76.0). ON A BRANCH, NOT MERGED, NOT DEPLOYED.

Branch `measure/us-recall`, PR open. Nothing here is on `main` or on the site.

**The headline: we hold 21 of 51 independently listed US funding events from
2026-06-01 to 2026-07-31. That is 41.2%, and the 95% interval is 28.8 to 54.8.**
By hiring market: Austin 5/8, New York 8/16, rest of US 5/14, San Francisco
3/13. Those cells carry 8 to 16 events each, so their ranges overlap almost
entirely and they are a work list rather than a league table.

Two findings to read before anything else:

1. **US leadership coverage could not be measured, and that is the result
   rather than unfinished work.** Executive appointments at privately held US
   employers cannot be enumerated from original sources. Open web search returns
   commercial people databases, which we never cite, and the only free
   chronological index left is SEC EDGAR full-text search, which is exactly what
   our own collector walks. Four independent research passes reached for EDGAR
   unprompted, all four came back over 90% exchange-listed filings, and all four
   were discarded rather than measured against.
2. **There is a way through, and 34 rows of it are already banked.** Searching a
   press-release wire for the literal dateline text (`DENVER, June`) enumerates
   private employers chronologically without touching any of our own feeds.
   Three re-run passes produced 34 verified rows that way. They are parked as a
   draft the measurement ignores, at
   `analysis/recall/us/goldset-us-2026-06-leadership.draft.json`. They need a San
   Francisco pass and a New York pass, which stopped only because the session's
   web-search budget ran out.

**The one thing that is a defect in the tracker rather than in the
measurement:** of the 21 events we do hold, 13 carry no country at all. That
makes them invisible to every geographic filter on the site even though we have
them. It needs an extractor fix, not a new source.

### To publish this

Review and merge the PR, then run the weekly measurement or wait for Monday. The
deploy is separate and is still the owner's call:

```bash
gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker --ref main -f dry_run=false
```

The site is now behind by three versions: 1.74.6, 1.75.0 and this 1.76.0.

---

## 2026-08-12: filter controls standardised, panel ships open, phone pass (1.75.0). MERGED, NOT DEPLOYED.

**The site is behind by two versions now.** `main` was already carrying 1.74.6
undeployed; this adds 1.75.0. Everything below is on `main` and none of it is
on the live site.

**To publish it — this is the owner's call, and a subagent does not make it:**

```bash
gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker --ref main -f dry_run=false
```

Then verify the live page renders the new bar, and match the **commit SHA**,
not "the latest run".

**`contrast-audit.yml` is RED against the live site and that red is correct** —
it measures production, and production is two versions old. CLAUDE.md says not
to disarm it for a green board. It should go green after the deploy.

### What changed

Full reasoning, numbers and the two handover claims that turned out to be wrong
are in TECHLOG under 1.75.0. In short:

1. **Every control has a visible edge in all three theme states.** Worst
   boundary went 1.00:1 -> 4.48:1 (light) and 1.00:1 -> 6.40:1 (dark, and auto
   on a dark OS). The cause was `--tit-line`, a row-divider hairline, doing a
   control edge's job; a new `--tit-ctl-line` reuses the two values the theme
   control was already proven on at 1.74.2. One height, one radius, one type
   size (was four), one edge, width tokens instead of five pixel guesses.
2. **The filter panel ships open**, per the owner. Collapsing is still there,
   remembered for the session only, and a deep-linked filtered view forces it
   open. Cost at 375px: the first data row moves 348.8px, which is less than
   the 360.6px the old panel cost when opened by hand, and it buys 44px targets.
   At 1280px it costs nothing; the bar was already open there.
3. **The phone.** Tap targets under 44px went 345 -> 226 (the remainder is 93
   inline text links, which WCAG 2.5.8 exempts, plus ranking rows, matrix cells
   and per-chart controls — named, not silently done). Dropdown popovers no
   longer run off the bottom: two of them opened 590px and 624px tall on an
   812px screen, and `openDrop_()` now measures vertical room at open the same
   way it already measured horizontal. Usable width 347 of 375px (92.5%).

### The guard, and the trap in it

`tests/test_control_boundaries.py` — four tests, proved red against the pre-fix
tree before they were green. It drives the REAL shortcode markup (via
`render_dashboard.php`'s `TIT_DUMP_HTML` hook) in headless Chrome, so it cannot
drift from what ships. No php or no Chrome **skips loudly**.

**If you write another test like this, do not assert on `innerText` of the
element you are hiding.** For a non-rendered subtree `innerText` falls back to
`textContent`, so the collapsed panel reported 5,239 readable characters. Read
it off the RENDERED ancestor instead: 42 collapsed, 377 open.

### Budget

Markup is **184,578 of 184,600 bytes** and went DOWN by one. The ceiling was
not raised and must not be: almost all of this is CSS, which the budget does
not count.

---

## 2026-08-10: FORWARD-FIRST budget policy. Read this before dispatching a backfill.

**The owner's decision, arrived at with a second AI advisor.** It is a policy
about ORDER, not a new budget system. The $10 cap, the degrade-not-halt
behaviour, the per-run read rations and the approval gates are all unchanged
and all still binding.

### The policy

1. Paid model and discovery spend is capped at **$10 per UTC calendar month**.
   Verified UTC-calendar, not rolling: `spend.month_delta` keys its snapshot on
   `datetime.now(timezone.utc).strftime("%Y-%m")` and measures a delta from a
   committed month-start reading of the key's lifetime usage.
2. **Paid processing prioritizes 2026-01-01 forward.** Forward accuracy is what
   readers use. History is the expensive tail and it is not going anywhere.
3. **Paid pre-2026 extraction and discovery are deferred** until the owner opts
   in.
4. **Correctness still applies to every record already published, at any date.**
   Retractions, corrections and guardrail work are NOT deferred. The gate can
   only fire on a run that declares a window through `TIT_BACKFILL_START`; no
   `correct-*.yml` and not `retract.yml` sets it, so no correction to a
   published row can be blocked by this policy. Test-pinned.
5. **Free structured historical work continues.** Fetching, registries,
   deterministic parsing, validation and dedup cost nothing and are untouched.
   `backfill-funding-bulk` and `backfill-structured-2026` hold no key and were
   deliberately left alone.
6. **Free forward collectors continue after the paid ceiling is reached.** That
   is `--degrade`'s existing behaviour and nothing about it moved.

### The mechanism, in one paragraph

A walker workflow now declares the window it is walking as `TIT_BACKFILL_START`.
`spend.py --degrade`, which every paid walker already ran, additionally calls
`apply_forward_first()`: if that window starts before `FORWARD_FROM` and the
owner has not opted in, it sets `TIT_PAID_READS=off` for the rest of the job.
`pipeline/classify.py` then raises `BudgetExhausted` on the first candidate, the
candidate defers UNMARKED, and the walker records `stopped_early` with a
`next_cursor`. **It takes no balance argument**, deliberately: this is ordering,
so it holds on day one of a fresh month exactly as it holds on day thirty. That
is what stops a history walk taking the headroom forward collection needs.

A run that declares no window is forward work and keeps the allowance. An
unparseable window is UNKNOWN and defers nothing, because UNKNOWN is not a
licence to switch off live collection.

### How to opt back into historical backfill

Either one:

* Dispatch the walker from the Actions tab with **`historical_backfill` ticked**.
* Set **`TIT_HISTORICAL_BACKFILL=on`** in the environment of the run.

Available on `backfill-2026`, `backfill-funding-2026`, `backfill-gdelt-2026`,
`backfill-gnews-2026` and `backfill-press-2026`.

**It is a pause, not a teardown.** No collector or backfill was deleted or
retired. Cursors and the writer-queue self-requeue chain are intact, so a funded
run resumes on the first window it did not do (`tests/test_backfill_pace.py`
already pinned that, and it still passes).

### It escalates if the pause outlives its reason

`ops_status.py [5] SPEND` prints three states: **FUNDED FIRST**, **DEFERRED BY
POLICY** and, once the clock runs out, **an ACTION NEEDED item**. Deferred is a
third state and must never be read as broken or as done.

The clock is the start of the next UTC allowance month after adoption, plus one
health-digest cycle of grace: **review due 2026-09-08**. A new month is new
money, so that is the honest moment to re-decide. Past that date `_report_spend`
returns a problem and ops_status exits 2 until the owner opts back in or
restates the deferral here.

### What this does NOT claim about cost

The read-through model swap has **not** shipped in this repo. `pipeline/classify.py`
still has `MODEL = deepseek/deepseek-chat` for the read-through; only
`GATE_MODEL` is `google/gemini-2.5-flash-lite`. The measured cost ratio of
**0.389** (recorded in the 2026-08-07 entry below, measured twice on independent
token mixes) implies the same $10 would buy roughly 2.6x as many read-throughs
after the swap, but that is an ESTIMATE conditional on a swap that has not
happened, not a description of today. Do not budget against it until it ships.

Last measured actuals, unchanged by this session: $1.4524 over 16 runs, 948
reads to 391 rows, $0.00343 per stored row.

**This session bought nothing.** It changed no model, no cap and no ration, so
it has no measured cost effect of its own. The intended effect is distributional:
paid pre-2026 walks now spend $0 until opted in, and that headroom stays with
forward collection.

### One thing left for the owner to decide

`ab-models.yml` held `OPENROUTER_API_KEY` with no spend step at either end, so a
discretionary model comparison could take the month's headroom from forward
collection. It now runs `spend.py --degrade` like every other paid job. If the
owner wants comparisons to outrank forward collection, say so and it comes back
out; the reverse was assumed here because that is what forward-first means.

---

## 2026-08-07: budget stop. Read this before spending anything.

**No code changed in this repo this session.** The session's work was in the
sibling layoff tracker; this entry exists so the state here is readable cold,
because the owner is out of budget and this was the last dispatch.

### The one thing that will stop you

**The OpenRouter key is exhausted.** Verified in the `collect.yml` run of
2026-08-07T00:09Z, not assumed:

```
ACTION NEEDED: key limit reached: collection will fail with 402
ACTION NEEDED: this month's spend $10.08 is at or past 90% of the $10 allowance
DEGRADED: paid reads are OFF for the rest of this job.
```

**This is the guard working, not a breakage.** `spend.py --degrade` set
`TIT_PAID_READS=off`, the job exited 0, and free collection kept running: the
deterministic extractors, the structured SEC and registry collectors, dedup and
the gate all continue. Paid candidates defer UNMARKED and return on a later
run, so this costs depth, never coverage. **Do not raise the allowance, do not
disarm the guard, and do not "fix" the degraded lines on the dashboard.** The
only real fix is owner-side: top up the key or wait for the month to roll.

Until then, treat any paid measurement as unavailable. A number you cannot buy
is UNKNOWN, and UNKNOWN is not a pass.

### The measured cost floor, both trackers

From a cost ratio of **0.389**, measured twice on independent token mixes
(`google/gemini-2.5-flash-lite` against the incumbent `deepseek/deepseek-chat`):

| scenario | cost floor per tracker at FULL coverage |
|---|---|
| today | (the incumbent, above the $10 allowance in bursts) |
| with the model swap | **$7.78 to $13.62 / month** |
| with batch on top | **$3.89 to $6.81 / month** |

**Batch is a SECOND halving that only unlocks AFTER the swap.** OpenRouter's
batch pricing is confirmed real at 50% off, but the `:batch` slugs exist **only
for Gemini, not for DeepSeek**. So while the incumbent is a DeepSeek model,
batch is not purchasable at any price. Flipping to flash-lite is what makes the
second halving available. **Do not build the batch path yet** — it was
deliberately left unbuilt, and it is worth nothing until the swap ships.

Note this repo already runs `google/gemini-2.5-flash-lite` as the GATE and
`deepseek/deepseek-chat` as the read-through, so the swap here is about the
read-through only. Last 7 days measured: $1.4524 over 16 runs, 948 reads to 391
rows (41%), **$0.00343 per stored row**, projecting $6.22/30d against the $10
allowance.

### Still open

1. **The money classification sweep over ALREADY-PUBLISHED rows.** Unchanged
   and still the top item. The quarantine drain only covered rows above the
   ~$6.5bn outlier ceiling, and none of the four error classes (currency
   mis-scale, AUM, fund closes, IPOs) are size-dependent, so the same mistakes
   almost certainly sit below that line in volume. `guardrails.NOT_A_COMPANY_ROUND`
   vetoes them going FORWARD; nothing has swept BACKWARD.
2. **Do not quote $493.3bn or $214.9bn.** Still unreproduced. The live API
   returns $457.1B. Stamp the query the publish run uses and read the total from
   the endpoint before publishing it. An agent's reported total is a claim, not
   a measurement.
3. **The 11 slugs claimed by two employers** (`ops_status.py` section 1c).
   Blocked on the slug by design; several are two genuinely different employers
   and must NOT be merged.

### Owner-only (a session cannot do these)

- **The ChangXin Memory $8.6bn IPO retract.** Needs a credentialed `retract.py`.
  It must NOT be done as a local-only retraction: that removes the row from our
  copy while leaving it live on the site, and kills every surface that would
  otherwise keep nagging about it. Unchanged from the 2026-08-04 entry.
- **Topping up the OpenRouter key** (above).

### The lesson to carry, in one line

Roughly a dozen defects across both trackers this week were one species: a
mechanism reporting health while doing nothing, so the search is **"what would
never tell us if it broke"** — and the answer comes from running the thing
live, never from a green fixture.

---
---

## 2026-08-05: recall copy fixed, dashboard trend is now a market claim (1.72.0, pushed, NOT deployed)

Three owner-driven changes, full detail and guards in the TECHLOG entry of the
same date. The short version for the next session:

- The recall page's direction sentence names the metric as COVERAGE and says
  "Higher is better". Do not shorten it back to a bare "Held".
- "Updates Collected a Day" renders on the SOURCES page now. The dashboard's
  trend slot is tit_market_trend(): weekly counts from the fixed panel of
  collectors live the whole 12-week window, split by stated headcount
  direction. With no full-window collector yet (collection began 2026-07-26)
  it renders the composition variant and says so on the card; the counts
  variant arms itself once the fleet has the history. The standalone
  direction card is gone; by_direction stays on /aggregate.
- Budgets: query budget held at 15 (itemised at the constant), byte budget
  181,600 -> 184,600 (itemised in the harness). New harness
  tests/php/market_trend.php proves the panel excludes a mid-window flood.
- Recall country table: full country names, headers "Event captured" /
  "Captured with every detail correct", a generated source line under every
  country (data/country_sources.json, built with sources.json), and a
  separate whole-market table with external denominators and a Read column
  (South Korea prints "Not comparable", never 0.1% as a score). The market
  figures are a dated snapshot recorded 2026-08-05; update them there when
  the private benchmark refreshes.
- Version 1.72.0 pushed to main. NOT deployed; deploy-plugin.yml not run, per
  standing instruction.

---

## READ THIS FIRST (2026-08-04, later): the amount queue is empty and the guard now escalates

The section below stands as the diagnosis and is still the ranking. What
changed since it was written:

- **The publish quarantine is empty.** All 15 held rows ($874.2bn) were
  adjudicated individually. 4 accepted (xAI $20bn, Waymo $16bn, DeepSeek
  $7.4bn, Databricks $5bn), 8 rejected and retracted locally (Arch $539bn AUM,
  Turkish Airlines $100bn lira capex, A16z $15bn fund close, ASE $10.5bn capex,
  Blackstone $6.3bn fund close, Corgi $4bn valuation-not-raise, Kingswood $4bn
  twice), 3 already answered earlier the same day. Published funding total goes
  from $214.9bn to a **projected $493.3bn** once the next publish run sends the
  eight released rows. **Nothing has been deployed and no publish run has been
  made**, so the site still shows the old total.
- **Defects 1 to 4 in the list below now have machine guards.** Currency
  mis-scale, AUM, fund closes and IPOs are all vetoed by
  `guardrails.NOT_A_COMPANY_ROUND`, which is what withholds the new
  corroboration auto-accept. It never quarantines on its own.
- **The queue cannot go unread again.** `ops_status.py` exits 2 on any `amount`
  finding older than 48h and names every row and its dollars, and the weekly
  digest mails the same full list. See the TECHLOG entry of the same date.
- **Row 25799 is fixed.** "93.175 millones" is no longer rendered as a figure.

**DONE 2026-08-04:** ChangXin Memory $8.6bn is withdrawn. It was an IPO, not
private funding, and had been ACCEPTED into the ledger on 2026-07-29 by
mistake, so no ops surface ever reported it. Retracted via `retract.yml`
(queued through `drain-writers.yml` — it is a writer, and it is the only path
holding `WP_SITE_URL`/`WP_API_KEY`, which is what the previous sessions were
missing): `wordpress=1 local=1`, run 30875221356, commit `4a6c7dc`. The ledger
verdict is now `rejected`. **Error class 4 is closed.**

That withdrawal also found a defect in `retract.yml` itself — the reason was
interpolated into bash, so `$8.6bn` was recorded as `.6bn` on both sides. Fixed
(inputs now travel via `env:`); the local note is repaired, the WordPress one
cannot be and is not rendered. Full account in the TECHLOG entry of this date.

**Still owed:** the audit below says only 6 of the top 25 rows are a
correctly-scaled private round. The amount queue only ever contained rows ABOVE
the derived ceiling of ~$6.5bn, so everything in the top 25 below that line was
reviewed by nobody. **That gap is now read but not fixed:** of the 12 top-25
rows with no `amount` ledger entry, **7 are wrong** — Marcos $2.5bn (a head of
state, not a company), GSK $2.2bn (an acquisition), Bradesco $2.0bn (a listed
bank's capital increase), Revolution Medicines $2.0bn (a public offering),
Cursor $2.0bn (not closed), Nscale $2.0bn (a duplicate), Ominimo $1.6bn (a
valuation, not a raise). The other 5 are sound. Two of those are error classes
the list of four below does not name: **an acquisition counted as a raise** and
**a person counted as a company**. The table is in the TECHLOG entry of this
date. **None of them are retracted yet** — that is the next session's job, and
the ceiling that hid them is the thing to fix, not the seven rows.

---

## READ THIS FIRST (2026-08-04): the published money total is wrong

A 40-agent audit measured it. Reports: `audit2/00-SYNTHESIS.md` and
`landmark/blast-radius.md` in the 2026-08-04 session scratchpad.

**Of the top 25 rows by `funding_amount_usd`, only 6 are a private company's
disclosed funding round stored at the right scale.** Four distinct failures,
all of which inflate the public money total:

1. **Currency mis-scale.** `100 billion lira ($2.3 billion)` stored as
   **$100.00B**: the parser took the foreign-currency figure and ignored the
   USD conversion the source itself supplied. About 43x wrong.
2. **Assets under management counted as a raise** ($539B of private-market
   assets is not money anyone raised).
3. **Investor FUND raises counted as company rounds** (a $15B fund close is not
   a startup round; one $4B fund appears twice under two spellings).
4. **An IPO counted as private funding** ($8.6B).

Recall side: **of the 20 largest disclosed private rounds of 2026 we hold 9**,
missing the two largest private rounds ever recorded. Not for lack of
collection: in the six days around one of them we stored 1,093 signals, 133
carrying a funding amount, and none was that round.

**This outranks every feature on the backlog.** The product's promise is that a
figure appears only if its source states it, and the biggest number on the page
does not currently meet that bar. Fix the classification (raise vs fund vs AUM
vs IPO), fix the currency parse, re-derive the total, then return to coverage.

Meanwhile keep the honest claim already written into the private benchmark:
this tracker does not lead on coverage (3.2 percent median against commercial
databases across 29 audited markets). It leads on per-row auditability and on
cost. No surface may claim otherwise.

### Other audit-confirmed defects, ranked in the synthesis
- `/aggregate`, `/query`, `/facets`, `/feed` all return `cf-cache-status:
  DYNAMIC` in production, so every filter click lands 2-3 uncached PHP hits on a
  shared host. `api.php`'s comment claiming the edge caches for five minutes is
  FALSE, and a design constant was reasoned from that false premise. Fix is a
  Cloudflare rule (owner action) plus adding `feed` to the htaccess alternation.
- `tit_flush_caches()` deletes `_transient_tit_%`, sweeping the export throttle,
  the feed throttle and the alert suppression with it, 4-plus times daily. Use
  options, not renamed transients.
- Both 2026-08-03 collect runs concluded FAILURE while every collector reported
  the DESIGNED degradation. Degrading is success; two red runs a day is how an
  alert channel gets filtered right before real breakage.
- `%G-W%V` mints an uppercase W that `/alert` rejects with a settled 400, so
  host-watch reddens every ~30 minutes and MASKS a real outage.
- 648 publisher feeds are fetched with whole-body buffering, so one compromised
  or expired-domain publisher can OOM the twice-daily collect run.
- $9.94 of a $10 August allowance went in three days with only $0.88
  attributable, because the backfill workflow runs `spend.py || true` so the
  guard never binds. Meter every backfill before promising a monthly number.
- ~1,000 company and place URLs are in no sitemap index; the sibling repo
  already solved this and the code is a copy.
- `data/talent_intel.db` is 68.86 MB, committed and re-pushed every data tick.
  GitHub hard-rejects at 100 MB, at which point every data job fails at the push
  step. A future outage with a known shape and no alarm.

### Fixed and live this session, do not redo
- Stored XSS: all three in-script JSON-LD blocks emitted stored publisher
  headlines with `JSON_UNESCAPED_SLASHES`, so a `</script>` in a headline could
  break out on the company, place and dashboard pages. Hardened with
  `JSON_HEX_TAG|JSON_HEX_AMP`; guard test proven to fail on the old tree. Live
  at 1.67.2.
- "Where the Money Went" renamed "Money Raised by City": the only card in the
  money trio whose title did not name its dimension.

---


> **benchmark-diff is DORMANT BY THE OWNER'S DECISION (2026-08-03). Do NOT ask
> the owner to set BENCHMARK_COMPANIES or BENCHMARK_FEED_URLS.** He was
> reminded ten times and asked for it to stop. His named benchmarks need no
> list: the aggregate publisher's monthly figure is auto-checked weekly into
> the private benchmark file, the commercial database's public claims
> likewise, and the tripwire already does discovery. The loop exits green at
> zero cost while dormant. Same rule as the sibling repo's tracker-diff.

**2026-08-04, and it is the largest recall defect this tracker has had.** The
three biggest private AI rounds of 2026 were all missing from the page: OpenAI's
March close (~$122bn at ~$852bn), Anthropic's February round ($30bn at $380bn)
and Anthropic's May Series H ($65bn at $965bn). None of it was discovery - the
query pack matched every one of them and the gate ledger holds the proof - so
the fixes are four downstream defects, all in [TECHLOG.md](TECHLOG.md) under
2026-08-04: the amount parser could not read a dollar written as a word
("122.000 millones de dolares" and five more returned NULL), the free gate could
not read the billion-scale register (Series stopped at E while cheap_extract
read [a-k]; no French "leve", no German separable "sammelt ... ein", no
valuation shape at all), dedup collapsed two different rounds in one fortnight
and kept whichever arrived first, and a gate that ERRORED was counted as a
rejection so an eight-hour 85.7% outage looked like a quiet news day. Before and
after on the same corpus: prefilter 13/22 -> 22/22, the amount parser 3/14 ->
14/14, 19 live rows newly quantified with 0 disagreements, 3,069 -> 3,140 tests.
Nothing was relaxed: all seventeen must-refuse amount strings still refuse, and
"93.175 millones" is still NULL because it names no currency.

**AND THE NEXT THING TO FIX IS PUBLICATION, NOT RECALL.** 15 rows worth $874.2bn
sit in `publish_guardrails` with `state='open'` and `reviewed_at NULL`, and
`publish.py` withholds every one of them - Anthropic $30bn is IN the database,
correctly extracted, re-seen 169 times, and has never reached a reader. That was
deliberately left alone: the guard is not wrong (Arch $539bn really is a misread
of "private market ASSETS"), so raising the threshold publishes the wrong ones
with the right ones, and auto-accepting is weakening a number guard. It needs a
corroboration rule plus a human draining the queue. The 2026-08-04 fixes push
MORE correctly-parsed mega-rounds into that queue, not fewer.

**Read this first if you are a new session.** It is the current state of the
build, what is proven, what is broken, and what to do next. Keep it updated as
you go: it is the only thing that survives a crashed session.

Last updated: **2026-08-02**. Plugin **1.64.0 on main and NOT DEPLOYED**
(1.64.0 adds the archive pending state — every publisher-sourced listing row
without a Wayback snapshot now says "No archive snapshot yet. We re-check
weekly; next check by <date>", derived end to end from the real schedule and
enforced by ops_status [2c]; plus the strip's week-date span, the largest
raise's own country, and the repaint's missing label space. 1.63.0 and 1.62.3
are still waiting for the same deploy. TECHLOG 2026-08-02.) Before that:
**18,250** current signals stored, company profiles shipped, cron firing on
schedule but not reliably green, **2,941 offline tests passing** (counted
2026-08-02, not carried forward; one pre-existing failure in
test_funding_amount_parsing, see TECHLOG) plus seven PHP render harnesses.

**1.63.0 is a dashboard reorder waiting for a deploy, and 1.62.3 is waiting with
it.** The chart grid moved from below the update cards to between the filters
and them, the "Narrow It Down" heading is gone (its sentence survives,
shortened), eight lines of directional copy now name the thing rather than the
position, and the quick-view count carries a real space rather than a margin.
Written by an agent, so it is pushed and not published: a session has to run
`deploy-plugin.yml`, then look at the live page at 375px and at desktop rather
than trust the green run. Full entry in [TECHLOG.md](TECHLOG.md), 2026-08-01.

**Read the two sections directly below before anything else.** The budget is
**$10/month** since 2026-08-01 (the section below still argues from $5 — the
arithmetic holds, the ceiling moved, and `cost_projection.py` is the authority
on both); and the gate label ledger, which is the only route to making the gate
free, was silently discarding its own output until 2026-08-01.

**2026-08-02, and both are premise corrections rather than features.** The
writer queue's dispatch order now knows what a slice costs — a chain measured at
3 minutes was waiting 123 for its turn, against 56/92 for the chain doing the
most work. Within the hour bse_india took its last two slices six minutes apart
instead of four hours and the chain finished, delaying the chain it overtook by
six minutes in total. And the discovery tripwire turned out to be armed, priced
and documented as none of those things; its first WRITING run landed 93 leads in
`data/tripwire_worklist.json`. Both in [TECHLOG.md](TECHLOG.md) under
2026-08-02. Spain
joined on 2026-07-31 as the fifteenth live collector and the second that
reports a departure; it is DORMANT, and the section below says how to arm it. Figures
below dated 2026-07-29 are left as they were measured that day; where one has
moved it says so beside it.

Also 2026-07-30: the sources page's collector map derived rather than typed (it
named five of nine live collectors), the dashboard's Top Cities strip counted
under the clause its own pills filter by (London read 18 and returned 1,339),
five funding amounts off by a factor of a million corrected in the parser, meta
and og descriptions added to the dashboard and the three trust pages, and the
cross-tracker pairing built and shipped disabled with the measurement that says
why. Detail in [TECHLOG.md](TECHLOG.md).

**Also 2026-08-02: the benchmark-diff loop, ported from the sibling and
DORMANT.** `run_benchmark_diff.py` + `collectors/benchmark_chase.py` diff an
external reference employer list against our data and chase the gap to each
employer's own press and filings through the ordinary pipeline. The list
arrives ONLY via the `BENCHMARK_FEED_URLS` / `BENCHMARK_COMPANIES` secrets;
with neither set (the current state) the weekly Tuesday slot prints one line
and exits 0 at zero cost. Do not ask the owner to add the secrets. Logs carry
counts and slice indices, never a name; the recall gap emails the owner via
`/alert` only below 90%. TECHLOG 2026-08-02 has the full design.

**Chronological detail lives in [TECHLOG.md](TECHLOG.md)** — that file is what
happened and why; this one is current state and next actions. Both are for the
TALENT tracker only. The sibling AI Layoff Tracker has its own `docs/HANDOFF.md`
(a gated baton) and `docs/TECHLOG.md`; never cross-write them.

---

## How to work on this repo without publishing a wrong number

Written 2026-08-01 from the mistakes that actually happened, not from
principle. The sibling repo carries the same section; keep them in step.

**1. Read facts from `origin/main`, never from the local working tree.** This
checkout runs behind and holds other sessions' uncommitted files. On 2026-08-01
it was **180 commits stale** and two wrong numbers reached the owner from it: a
collector reported as never having run when it had run and stored 48 rows, and
a row count off by ~700. `git show origin/main:path` costs one command. A stale
tree does not announce itself; it answers confidently.

**2. Measure the premise before you build on it.** Both coverage premises above
were briefed as fact and measurement inverted both. An agent that measures and
contradicts its brief is doing the job; build the part that survives and write
down the part that did not.

**3. A green run can do nothing at all.** Backfills and `deploy-plugin.yml`
default to `dry_run=true`: green, zero work. `collect-structured.yml` succeeded
six times while collectors inside it had never produced a health row. Ask what
the run *did*, not whether it passed.

**4. Absence of a signal is not a pass, and a small sample proves little.**
PASS / FAIL / **UNKNOWN** are three states. A session once declared the archiver
broken on "0 of 40 rows have `archive_url`" when the true rate was 72/17,533 —
at 0.4% a 40-row sample finds nothing about 86% of the time.

**5. Look at the rendered result when there is one.** Tests, sitemap counts and
a matched deploy SHA cannot see two links that say the same thing, or a footnote
printed 316 times. Both shipped in the sibling repo on 2026-08-01 with 400 tests
green, and both were found in under a minute by opening the page.

**6. Cite the file when briefing an agent.** An agent briefed against the Form D
and M&A overstatements correctly reported they are in THIS repo's HANDOVER and
not the sibling's TECHLOG, and designed against the right incidents instead.
Memory across two similar projects is exactly where this fails.

**7. Check the escape hatch itself.** `drain-writers`' documented remedy
reported success and fixed nothing for hours. When a documented remedy does not
work, read it before running it again.

**8. State what you did NOT verify, beside what you did.** It is the only thing
that tells the next session where to look first.

---

## The budget is $5/month, and $5 does not fit the architecture (2026-08-01)

`spend.MONTHLY_ALLOWANCE_USD` went $10 -> $25 on 2026-07-30 and **back to $5 on
2026-07-31**, both by the owner. The file kept $25 for a day after the owner had
returned to $5, so every cost decision in that window was measured against a
ceiling five times too high — including a "two weeks of runway left" warning
raised to the owner that was never true.

**What $5 buys, from `cost_projection.py [5]` rather than from opinion:**

| | |
|---|---|
| LLM gate, not optional | **$4.41/month** |
| left for read-throughs | **$0.59** |
| reads that buys | 14/day against demand of **1,102/day** |
| share of full coverage | **1%** |
| full coverage would cost | **$49.14** |

Per-source caps derived at that ceiling are literally `1` for both google_news
and national_press. **They were deliberately NOT applied.** Rationing reads
cannot close a gap the gate has already spent, and a cap of 1 would gut
coverage today in service of a target the architecture cannot yet meet.

**So the road to $5 is making the GATE free, not rationing reads** — replace the
paid LLM gate with a trained classifier
(`docs/PLAN-gate-to-five-dollars.md`, steps 2-5). Until then `spend.py
--degrade` is what keeps the promise: paid reads switch off partway through the
month, every free collector, the free prefilter and both dedup layers keep
running, and deferred candidates return UNMARKED on a later run. **Degraded is
the DESIGNED state at this ceiling, not an incident. Do not raise the allowance
to make it stop.**

Note for anyone reading a cost comment elsewhere: `collect.yml`'s cap block
explained itself in terms of $25 until 2026-08-01. If you find another, it is
stale, not a second opinion.

## The gate ledger was losing the training data it exists to collect (2026-08-01)

**This blocked the only route to $5 and produced no error.** `pipeline/gate_ledger.py`
records one JSONL line per gate decision, and it records **four** verdicts, not
two, because the gate fails open: logging an outage as YES would teach the
classifier that a busy provider is a talent signal.

It was correctly wired at the gate call site and in the three collect
workflows. But `record()` only **buffers**, and five backfill scripts
(`backfill_sec_2026`, `_form_d_`, `_gdelt_`, `_gnews_`, `_press_`) call
`classify.classify()` **without importing `gate_ledger`** — so they filled the
buffer and it died at process exit. Even a flushed shard would not have
survived: none of those five workflows ran `merge_gate_labels.py` after their
`git reset --hard`. **Lost twice, after the money was already spent.** The
module cannot detect this: a run that gated nothing and a run that lost
everything look identical from inside it.

Fixed 2026-08-01: `gate_ledger.around_run(label)` resets before and flushes in
`finally`; all five scripts decorated; all five workflows save `data/gate_labels`
before the reset and merge after it. `run_collect._with_gate_labels` is now
that same function rather than a second copy. Two of the new tests **derive
their subject list from the code** rather than a hand-maintained list, which is
precisely what let five backfills slip.

**Still unproven:** no real backfill has run since. The first genuine
confirmation is a `data/gate_labels/labels-2026-08.jsonl` line appearing on
main. Until then, treat step 1 of the plan as wired but not demonstrated.

---

## Two coverage premises that measurement inverted (2026-08-01)

Both were briefed as fact and both were largely wrong. Recorded because the
measurements cost real time and the wrong versions are intuitive enough to be
believed again.

**1. The "24 missing prefilter languages" were mostly not a language problem.**
The 7x gap is real and reproduces: 117 wired feeds in 23 uncovered languages
returned **1.3%** in-scope against a 95-feed control's **9.7%**. But a
**language-neutral control** settles it — untranslated Latin-script tokens every
newsroom writes anyway (CEO/CFO/CTO, startup, "Series A", seed, VC, unicorn,
IPO) appear in **6.5%** of the control corpus and **1.2%** of the uncovered one,
and no regex touches that ratio. The uncovered feeds are national general
dailies; the covered sample is disproportionately tech and business press.
**About five sixths of the gap is which feeds are wired, not which languages are
read.** Wiring general dailies in COVERED languages would buy the same 1.3%.
Shipped anyway for the part that is real: **1.3% -> 3.3%**, ~24 genuinely in
scope per 2,119 items, at **~$0.08/month** (not free). Four live collisions are
pinned by tests, including Latvian `algas` (wages) being Estonian `algas`
(began) under one regex covering all 23 languages.

**2. The google_news edition dateline would have made English editions WORSE.**
`company_development` rows were 81.4% no-country from google_news against 35.6%
from national_press, so the edition should place the story. It does now — but
**only for non-English editions**.

**CORRECTED 2026-08-01: the "100% identical to en-US" figure was wrong.** It
came from a single query returning 47 items. Re-measured across the full
five-query production pack, the English non-US editions repeat **62-70%** of
the anchor, not 100% (only `en-BD` and `en-HK` are true duplicates at 99.7%;
the anchor re-fetched against itself is also 99.7%, which is the churn floor).

**The conclusion held, but overlap was the wrong instrument.** The ~35% that
differs is the same global English wire re-ranked. What decides it is the
publisher test: items from a newsroom in that edition's own country, in scope,
from a publisher `national_press` does not already read twice a day. On that
measure English non-US editions are **0.0-11.5% local (0-7 new items a visit)**
against **49.0-67.7% (53-163 a visit)** for pt-BR, de-DE and ja-JP. The stored
rows agree: google_news placed BR 48, FR 45, ES 40, JP 39, IT 39 against GB 7,
IE 5, NG 5, SG 4, and ZA / PH / BD / HK at zero. A hand-read of en-GB's 14 items found **one** British employer
(Restore plc) among Cracker Barrel, Hormel, Conagra, Toro, Apple and BBVA
Mexico. Stamping those GB would put a wrong country on exactly the rows the fix
targets, **and a wrong country is worse than an absent one**. pt-BR scores 12/14
and every non-English edition overlaps en-US at 0%.

**Separate finding, FIXED the same day, and the premise moved again on the way.**
Those English non-US editions were re-fetching the US anchor under another name.
Re-measured on the full five-query production pack rather than one query: the
overlap is **62-70%, not 100%** — only en-BD and en-HK sit at the churn floor
(99.7%) — so "identical" was wrong. It did not matter, because overlap was the
wrong instrument. The publisher test settles it: an English non-US edition
returns **0-7** in-scope items per visit from a newsroom in its own country that
`national_press` does not already read, while pt-BR, de-DE and ja-JP return
**53-163**.

All seventeen are withdrawn (`source_registry.WITHDRAWN_ENGLISH_EDITIONS`, with
the per-edition table). Nothing needed building to replace them: every one of
those markets already has publisher feeds read on EVERY run, and the thinnest —
Bangladesh, Ghana, Malaysia — return 30-45 items a run against the 0-7 the
edition gave every four days. `LOCALES_PER_RUN` went 5 -> 4 so the swap does not
raise spend: the withdrawn editions were cheap precisely because they returned
the anchor again, and 5 all-non-English editions a run would have been a 26%
rise in daily candidate load. At 4 the load is flat within 1% and there are 8
productive edition-visits a day instead of 6.7. Read-throughs are capped and
saturated, so no read money moved — only which candidates compete for it.
`python3 -m analysis.editions.measure` re-runs the whole thing, free.

Two consequences worth knowing: the segment budget no longer derives from the
locale rotation (it is `SEGMENT_SWEEP_BUDGET_DAYS`, same 56-segment ceiling —
otherwise shortening the sweep would have silently cut twelve markets off the
coverage page); and ZA and NZ now hold their `discovery_only` listing on wired
feeds alone, so do not read `live_sources=("google_news",)` there as "we query
their edition". Detail in [TECHLOG.md](TECHLOG.md).

**Open, from that pass:** the sources page still says Google News RSS is "38
country editions, 15 languages". It was already wrong (51 plus the anchor) and
the true string is now "35 country editions, 16 languages"; correcting it means
regenerating `wordpress-plugin/.../sources.json` and deploying.

**Read rations now actually bind.** `classify.read_cap` splits the budget by
measured conversion (google_news 761 reads -> 354 rows = 46.5%; national_press
288 -> 160 = 55.6%), but **a `TIT_READTHROUGH_CAP` set in a workflow WINS over
the rule** (deliberate; backfills need 5000). So the rule was live for
national_press and inert for google_news, which kept buying 129 from a bash
`case` statement. Now 99 / 118 with the 217 total pinned in code and asserted by
a test. `ops_status.py [2g]` reads that case statement and warns when the two
disagree, so the code can never look right while the run buys something else.

## Operational fixes worth not relearning (2026-08-01)

- **`drain-writers`' own escape hatch was keeping it red.** `-f resolve=all`
  reported success and cleared nothing, because `"all"` iterated only the
  `orphans` list while failed tickets required an exact ticket ID — and the help
  text said "an orphan run, or a failed ticket". Five failed tickets reddened
  every tick from 00:30 to 01:36. Fixed with a test; landed tickets are still
  untouched because they were never a problem.
- **Evictions produce orphans, and each orphan reddens `drain-writers` on every
  tick until a human clears it.** That is why the failures arrive in bursts. The
  evictor was identified: **`collect-structured`'s own cron**, which fired at
  09:56:32Z and cancelled a pending collect-press one second later. Of
  collect-press's 8 lifetime runs, 3 were cancelled with `total_count: 0`, and
  two of six SCHEDULED slots were evicted.
- **The sources page had two live em-dashes**, from
  `data/sources_catalogue.csv` — a file that is an engineering log AND public
  copy with nothing marking which field is which. The guard went into
  `build_sources_json.py`, the render boundary, and it **refuses to build**
  rather than substituting: of the two offences one wanted a full stop and the
  other a comma, and a silent swap puts words on a public page nobody wrote.
  Notes that never render keep their dashes.
- **`sec_form_d_bulk` stores 2,682 current rows and is not on the sources
  page.** Its `source_name` is "SEC EDGAR (Form D)" while
  `COLLECTOR_BY_SOURCE_NAME` maps only "SEC EDGAR Form D" (no parentheses), so
  it resolves to nothing. The usual failure is a page claiming coverage we lack;
  this is the inverse, real rows with an undisclosed ingest path. **Open.**

---

## The Form D overcount correction (2026-07-31) — APPLIED, and measured

**318 published funding records, $14.25bn, withdrawn on 2026-07-31 (run 30605363355, zero failures).** A Form D
reports an *amount sold*, and three kinds of filing report an amount that is not
money any company raised: a takeover paid for in shares
(`ISBUSINESSCOMBINATIONTRANS` = true, a field in the data set that no code path
had ever read), an uncapped continuous offering whose total is years of
cumulative sales, and an amendment restating an offering already published.
Derivation, every measurement and both rejected rules are in
[TECHLOG.md](TECHLOG.md).

| | before | projected | measured |
|---|---|---|---|
| funding records | 3,344 | 3,026 | **3,026** |
| money raised | $122.0bn | $107.7bn | **$107.7bn** |
| business combinations published as raises | 177 / $8.5bn | 7 / $0.7bn | **7 / $0.7bn** |
| employers with a funding record | 3,127 | 2,906 | **2,937** |

**State: done, deployed, verified.** The corrections-log entry is `applied` with
its projected column kept beside the measured one, live on plugin **1.61.0**.
Every planned signal id was re-fetched from the live API afterwards and none of
them is still published.

**The one row of the projection that missed, and why it is left visible.** We
said 2,906 employers would keep a funding record; the answer is 2,937. The 31 are
employers that ARRIVED between the projection and the run — 32 funding records
worth $3.55bn from a backfill slice and a night of collection, while the ticket
waited behind two other writers. Same cause as the 998-row correction's $10bn gap
on 29 July.

**Do not re-litigate three settled findings.** Sales commission as a cash-raise
rescue (rescues 8, wrongly keeps 5), `ISSECURITYTOBEACQUIREDTYPE` (mis-ticked on
400 rows), and industry group "Investing" (91 rows, $1.56bn, zero of them matched
by the collector's vehicle-name patterns and several of them real employers) were
all measured and rejected. "Investing" is a NAME-vocabulary gap for whoever owns
the collector — the missing shapes are `... Funding LLC`, a `YYYY-N` serial and
`Blocker Corp` — and not an industry to exclude.

**The honest ceiling.** 115 of the 177 takeover filings answer yes and explain
nothing, so on the rate of the 62 that do explain, roughly a dozen real raises
are withdrawn with them. And the current quarter has no bulk data set until it
ends, so 9 July filings worth $0.09bn are untouched and get checked when 2026q3
publishes. Both are on the corrections page in words.

---

## Where things stand (2026-07-29)

**Verified by curl, not by a green tick:** plugin **1.53.0**; dashboard,
`/recall/`, `/corrections/`, `/sources/` all 200; **money raised $101.4B** (was
$124B, then $200.3bn, both before the stale-`company_key` correction); sources
page lists all **9** live collectors and reports a last run for every one of
them; writer queue holds one failed ticket and zero unresolved orphans;
**715 of 715** sitemap URLs clean.

**Twelve registered collectors:** `google_news`, `gdelt`, `national_press`,
`sec_edgar`, `sec_form_d`, `sec_execcomp`, `uk_paygap`, `ats_boards`,
`bse_india`, `edinet_japan`, `opendart_korea`, and `tripwire_chase` (dormant,
correctly absent from the sources page). The last two were built on 2026-07-30
and **neither has made an authenticated call yet**, so Japan and Korea both stay
`discovery_only`; see the TECHLOG entries for what each one measured and what it
refused. The sources page joins a collector to a source name through the
`collector` field on each row of `data/sources.json`, written by
`build_sources_json.py` from `source_registry.COLLECTOR_BY_SOURCE_NAME`. **Do
not re-type that map in PHP.** It was typed there with five of nine entries, so
`national_press`, `sec_execcomp` and `uk_paygap` all read "not yet reported"
while running twice a day.

### Spain (2026-07-31) — built, measured, DORMANT, and it reports departures

`collectors/spain_borme.py`. Keyless, no model, **$0**, and the **second source
in this tracker that states a departure at all** (the other is `czechia_ares`).
It reads BORME Section A, the bulletin every Spanish commercial register
publishes its inscribed acts in.

| | |
|---|---|
| discovery | `boe.es/datosabiertos/api/borme/sumario/{YYYYMMDD}` — Section A, ~30 province files a day |
| document | `boe.es/diario_borme/txt.php?id=…` — **NOT `xml.php`, which robots.txt disallows** |
| what it keeps | the **consejero delegado** alone, under 8 spellings, across 3 act headings |
| measured | 7 real publication days: 213 province files, 15,642 entries, **340 events (141 arrivals, 199 departures)** at 209 employers |
| projected | ~49 a publication day, **~12,700 a year** |
| validate | **340 of 340 build a Signal, 0 rejected, all `verified`**; 155 carry a city |
| tier | **ES stays `discovery_only`** — no run has gone through `run_collect` yet, and the segment budget is still full at 56 of 56 |

**Read three things before you change it.**

1. **The cancel-and-re-inscribe pair is 21% of the raw feed.** A Spanish board
   renewal is inscribed as a total cancellation followed by a total
   re-appointment, so the same person appears at the same office in both
   directions on one date and nobody left — 92 of 432 candidate rows. Both
   halves are declined. A pair at two DIFFERENT offices survives, because SPLA
   SA really did move one man from a sole delegation to a joint one, and
   collapsing on the person alone would delete that.
2. **The office IS the materiality filter, because Spain publishes no
   headcount.** Everything board-grade is 494 acts a day (123,455 a year); the
   consejero delegado is 49. Widening is one entry in `OFFICES` and eight times
   the volume.
3. **A Spanish row is a week old by construction.** The date on it is when the
   registrar inscribed the act; BORME publishes about seven days later (p90 8,
   p99 11). The two-digit year pivots on the publication date, so `(03.02.97)`
   is 1997 and not 2097.

**To arm it:** uncomment the single Sunday cron in `collect-structured.yml`.
The gate is the standing one — a human reads a REAL dry run first:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
  -f inputs_json='{"source":"spain_borme","dry_run":"true"}' \
  -f reason='first real BORME run'
```

A run is ~210 requests and 10-25 minutes, inside the writer lock's 120-minute
hold. Promotion to `structured_official` is one commit after the first real run
lands **and** the segment budget has room.

### The registry sweep behind it (2026-07-31)

Fourteen more national registries were asked one question — *does the source
STATE a director change as a typed dated event, or would we have to infer it by
diffing two snapshots?* — and every one was fetched live. The ranking, the
numbers and the refusals are the `THE 2026-07-31 REGISTRY SWEEP` block in
`source_registry.py`. **Read it before researching any European registry.**
The two that matter for a next session:

* **Denmark's CVR is the best source on the whole list and it answers 401.** It
  is the only registry found that states BOTH a start and an end date per
  participant AND publishes employee bands. Access is free; the credentials
  come from Erhvervsstyrelsen on request. **This is the single highest-value
  owner ask on the page** — higher than ASX, because ASX needs a licence
  negotiation and this needs an email.
* **Norway is the best buildable one, and it is the EDINET shape.** A real
  role-level change feed (`/oppdateringer/roller?afterTime=`, 1,338 updates a
  day) plus a current-roster endpoint with no per-person dates, so it can say
  *this employer's board changed on this date* and never who or which way.
  `antallAnsatte` is the free materiality filter and it is **unstated on 86% of
  a 147-company sample**, which is a recall hole of the Czech `Neuvedeno` kind.

### Korea (2026-07-30) — built, measured, deliberately not promoted

`collectors/opendart_korea.py`. Zero cost, no model. **Read the scope before you
describe it to anybody**: DART's typed detail codes stop one level coarser than
SEBI's, so what selects a row is the Korea Exchange's own report TITLE, and only
two kinds of change have one.

| | |
|---|---|
| what it reads | `opendart.fss.or.kr/api/list.json`, detail types `E005` and `I001` |
| what it keeps | 4 exchange report titles: a change of representative director (3 spellings) and the appointment/dismissal/early retirement of an independent director |
| measured | 261 of 8,363 rows over 2026-05-01..07-29 (3.1%), ~1,060/year, 12 to 49 a week |
| direction | always `neutral`. The title never says which way the change went |
| `source_url` | `dart.fss.or.kr/dsaf001/main.do?rcpNo=` — and robots.txt disallows that path, so `link_check.py` records it as `robots`. Nothing fetches it |
| tier | KR added to `MARKETS` at `discovery_only`. It was **not in MARKETS at all** before this |

**Three things not to redo.** The English viewer
(`englishdart.fss.or.kr/dsbh001/main.do`) answers HTTP 200 with a body of the
single word "Reject" for 4 of 20 real filings, Kia and Korea Gas Corporation
among them — do not cite it. The periodic-report endpoints (`exctvSttus.json`,
`empSttus.json`) are point-in-time rosters with no appointment date and are
refused rather than diffed. And a missing `crtfc_key` is an HTTP **302** to an
HTML page while a bad key is an HTTP **200** with `{"status":"010"}`, so neither
the status code nor "it parsed as JSON" means success.

**To promote it:** run it once for real
(`gh workflow run collect-structured.yml -f source=opendart_korea -f dry_run=true`
first), read what `corp_name_eng` coverage actually is — a blank declines the row,
and that is the number that decides real yield — then add `opendart_korea` to
`KR.live_sources` and move the status in one commit.

### Discovery widened (2026-07-29, late) — schedule, staleness, markets, feeds

Four changes shipped together, aimed at the 9% recall measurement:

1. **collect.yml's cron now SWEEPS google_news, gdelt, sec_edgar and
   sec_form_d** (one `run_collect.py` invocation each; the loop is in the
   Collect step). Before this the schedule only ever passed `google_news`, so
   the other three ran twice each in their whole lives, always by hand, while
   their last manual run sat in the ledger saying "ok". Each invocation
   carries its own `TIT_READTHROUGH_CAP` (gdelt 100, the SEC pair 40,
   google_news the default 60); the caps are ceilings, not spend — the SEC
   pair found 6 and 3 items on their last real runs. Dispatch keeps its
   single-source input. Pinned by
   `test_the_schedule_sweeps_every_collector_this_workflow_owns`.
2. **Staleness has one authority: `staleness.py`** (stdlib-only, because
   ops_status must run before any venv exists). ops_status used to apply a
   global 36h while health_digest carried a per-collector map, and the two
   disagreed about every source off the 2x/day cron. Leashes are derived from
   each schedule: 14h for the 2x/day sweeps, 48h for the perishable daily
   boards, ~35 days for the monthly structured pair (the old 14-day default
   flagged them mid-cycle, monthly), a quarter-plus for bulk and dormant.
3. **Six markets joined MARKETS, Israel first**: IL (Hebrew and English
   terms), IN, CA, AU, SG, JP, all discovery_only. The part that adds actual
   discovery: a Hebrew `GOOGLE_NEWS_VOCAB` pack, live-verified (leadership 21
   items, funding 26; the מנכ"ל gershayim must be U+05F4 — an ASCII quote
   inside a quoted phrase silently matches nothing), and `("he","IL")` in the
   rotation. 51 editions now sweep in 5.1 days; the derived window widened
   6d -> 7d by itself. A third daily cron slot would cut the sweep to 3.4d at
   +50% spend — the owner's call, documented at `LOCALES_PER_RUN`.
4. **Eighteen publisher feeds joined the catalogue (575 -> 593), every one
   fetched through the collector's own path first.** Notables: HR Dive,
   TechCrunch, GeekWire, The San Francisco Standard, NYT Business, and the
   Apple/Google/NVIDIA newsrooms. Refused with reasons in `feed_checked`:
   Axios/CNBC/Business Wire (robots.txt), Microsoft News (newest item 448d
   old behind a 200), and CTech — the outlet that broke the four missed
   Israeli rounds publishes NO feed, so Israel's English coverage is Globes,
   Geektime, the Innovation Authority and the he:IL edition.

**CORRECTED 2026-08-02: the tripwire is ARMED and its cost is MEASURED.**
This paragraph said it was dormant and unpriced for three days after it was
neither. It was armed on 2026-07-30 (77becc5), Mon+Thu 07:00 UTC with
`dry_run=false`, from `schedule-link-hygiene.yml` — arming it meant DELETING
the cron from `tripwire.yml`, not uncommenting it, because a lock member may
not carry its own schedule. Live queries went out the same day (run
30506967802): 17 queries, $0.0977, **$0.0057 a query**, 3.5x under the $0.02
the plan is sized on. The `staleness.py` half of the instruction below was
never done, because its stated trigger was a line arming removes — see the
2026-08-02 TECHLOG entry.

### Company profiles (built 2026-07-29, live on 1.47.0)

`/talent-intelligence-tracker/company/{slug}/`, computed on render. **714
indexable pages of 7,318 employers**, gated on **3 source documents and either
2 kinds of evidence or 5 documents**. The reasoning is in `includes/company.php`
and the measurement in TECHLOG; the short version is that rows are the wrong
unit (one pay-versus-performance table becomes four rows) and three documents
from one templated feed is one thing said three times.

`tit_company_meets_threshold()` decides the page, `tit_company_gate_having()`
builds the sitemap's SQL from the same constants, and the tests fail on a
threshold typed twice. **Do not add a second implementation of "is this employer
worth a URL".** Below the bar renders and stays linked, but is `noindex, follow`
and out of the sitemap.

**Verify it with `python3 check_sitemap_urls.py`, and do not substitute a
sample.** It fetches EVERY URL with redirects disabled and asserts 200, no hop,
no noindex, and no decoder-dependent character in the raw `<loc>`. It exists
because a twenty-URL hand sample passed while 22 of 712 URLs were broken: the
sample resolved the XML entity and the bug only appears when you do not, so the
sample and the bug were the same shape. Last run: **714 fetched, 714 clean**,
after the key correction below moved three of the URLs in it.

**Five things a future session will otherwise rediscover:**

1. **The slug transliterates and must keep doing so.** No encoding of "&" is
   safe: `%26` 404s, the entity `&#038;` 301s into a 404 for a consumer that
   does not resolve it, and only the resolved literal works. Accents 404 both
   ways. So the slug is `remove_accents`, `&` -> `and`, `[^a-z0-9]` -> `-`.
   **The pre-1.46 slug still resolves and 301s to the canonical one; keep step 1
   of `tit_company_rows()` exactly as it is** or every URL ever published here
   breaks at once.
2. **A collision is refused, not resolved, and the refusal stays.** Two keys
   claiming one slug is one employer stored twice, and serving either would
   show half a history. The three that existed are merged (below), but the
   branch in `tit_company_servable_slug()` is what makes the NEXT unmerged pair
   harmless rather than wrong. **The fix for a collision is always a merge in
   employer identity, never a routing rule.** `ops_status.py [1c]` names any
   pair that appears.
3. **A corrected `company_key` keeps its old URL, and that is a property of
   revisions.** The slug is derived from the key, so a fix to
   `vocab.company_key` moves it. `tit_company_moved_slugs()` joins each
   superseded revision to the current revision of the same signal, and step 3
   of `tit_company_rows()` resolves the old slug to the key that signal holds
   now, so the ordinary canonical comparison 301s it. **Do not add a redirect
   list beside it** — this already covers every correction there will ever be.
   Both slug forms of the old key are indexed, a live key always wins, and an
   ambiguous move is dropped rather than guessed. Proved by running it:
   `php tests/php/route_company_slugs.php`.
4. **The site's SEO plugin is SEOPress, not Yoast.** It prints its own robots
   tag on our routes. The head is buffered and every robots tag replaced with
   one of ours, so nothing here names a plugin. Do not "fix" this by calling a
   plugin filter.
5. **The `robots_txt` filter is inert.** `/blog/robots.txt` is a physical file
   Apache serves from disk, and the robots.txt a crawler reads for this host is
   the root app's. **Manual step, not done:** submit
   `https://asktherecruiter.com/blog/talent-intelligence-tracker/company-sitemap.xml`
   in Search Console, or add it to the root robots.txt. Until then, discovery is
   the internal links from the dashboard table.

### Employer keys (corrected 2026-07-29, run 30490704433)

**DONE, and it was eleven employers rather than the six that were written
down.** `correct_company_key.py` re-issued **38 rows across 11 employers**, 0
duplicates, 0 failures. Six were the `\b` suffix-strip mangling
(`-operative group`), three were the collision merges, and two —
`crossamerica partners lp`, `peace coffee pbc` — were nobody's list at all:
`lp` and `pbc` joined the suffix vocabulary after those rows were stored.

**That is why the worklist is derived and not typed.** The script's targets are
every live row where `vocab.company_key(row.company)` differs from the stored
key, so it covers whatever the last edit to that function moved, and the next
edit needs no new script. `ops_status.py [1c]` reports the same question
continuously, so a stale key is a line in the status output rather than a fact
somebody has to remember.

**The merge is a curated list, on purpose.** `vocab.EMPLOYER_KEY_ALIASES` holds
three entries: one employer spelled two ways by the filer (EDGAR's index vs an
8-K cover page; two GOV.UK pay-gap employer ids for one NHS trust). The
rule-shaped alternative — fold whatever the slug folds — was measured at **274
keys and 624 rows to merge three employers**, and it re-breaks CO-OPERATIVE
GROUP by feeding "co" back to the suffix strip. **An alias may only ever
collapse punctuation**, asserted in `tests/test_identity.py`; anything else is
a rename hiding in a lookup table.

Verified live: the three moved sitemap URLs 301 to their new form (both the
canonical and the pre-1.46 shape of each), the merged employers show their
whole history (Perma-Fix 4 updates, Daré Bioscience 4, the NHS trust 6), and
`check_sitemap_urls.py` fetched **714 of 714 clean**. It is 714 rather than 713
because merging the trust's two GOV.UK ids puts 6 documents behind one
employer, which crosses the gate.

**Not verified:** how any of it looks. Checked by status code and markup, not
by eye.

### Publish guardrails (built 2026-07-29) — quarantine, not halt

Four arithmetic checks run inside `pipeline/publish.py` before anything is sent:
an implausible single funding amount, period totals that do not reconcile, a
printed date span that does not match the data, and a vehicle/SPV name on a
funding row. They cost nothing (no model, no network). Full derivation,
measurements and rejected candidates are in [TECHLOG.md](TECHLOG.md).

**They QUARANTINE, they do not halt.** A flagged row is held out of the batch
and out of every figure; every other row in the same batch publishes. The first
build halted the run instead and both of the first two production runs failed on
the same eight findings while carrying dozens of good records, so that was
changed the same day. A quarantined row is never marked published, so accepting
its finding releases it on the next run with nothing to replay.

**Runs stay green while a quarantine is inside its grace window.** They publish
the clean rows and THEN exit non-zero once a finding has gone unanswered past
it: **192h** for a row that never reached the site (one weekly digest cycle plus
a day) and **72h** for one already on the site, because that is a wrong figure in
public that only a retraction can remove. An aggregate finding (period totals,
date span) still halts immediately: there is no clean subset of a wrong total.

Where to look, without writing anything:

```bash
.venv/bin/python guardrails.py                  # what is quarantined, and the countdown
.venv/bin/python guardrails.py --check --live   # also reconcile the live date span
python3 ops_status.py                           # section [2d]
```

Then answer each one:

```bash
python3 guardrails.py --accept amount/<content_hash> --note 'read the filing, real'
python3 guardrails.py --reject vehicle_name/<content_hash> --note 'SPV'
python3 retract.py <signal_id> 'why'            # rejecting records the judgement;
                                                # retract.py removes the row
```

**Do not accept anything to clear the queue.** An accepted finding never blocks
again.

### Three traps that will bite you today

1. **`deploy-plugin.yml` defaults to `dry_run=true`.** A plain dispatch is a
   green run that uploads **zero bytes** — every step passes, the FTPS upload is
   skipped. Always `-f dry_run=false`, then **curl the live `ver=`**. This was
   walked into an hour after being documented; reading about it does not prevent
   it, only the verification step does.
2. **Never dispatch a database writer directly.** GitHub keeps one pending run
   per concurrency group and silently evicts the waiter. Queue it:
   `gh workflow run drain-writers.yml -f enqueue=<wf>.yml -f inputs_json='{"dry_run":"false"}' -f reason='why'`
3. **`correct-*.yml` also default to `dry_run=true`.** A replay with guessed
   defaults is a green run that changes nothing.
4. **A backfill is a CHAIN now.** Dispatch the whole window you want; the
   committed cursor in `data/backfill_state.json` decides where a run actually
   begins, and each run queues the next. A chain that stops says so — `halted`
   or `stalled` in `ops_status.py [2e]` — and never requeues itself out of it.
   Fix the cause, then re-queue the backfill; it resumes at the cursor.

### Cost and coverage (2026-07-30) — worldwide costs $100.99/month, and $25 is the allowance

**Run the program, do not trust this paragraph:**

```bash
python3 cost_projection.py          # live prices; --offline uses the snapshot
```

It reads the health ledger and OpenRouter's price list and prints what
worldwide coverage costs, labelling every number MEASURED (what the provider
charged), COUNTED (the funnel) or MODELLED (a price list times a token count).
It exits **2** when full coverage does not fit the allowance, which today it
does not.

**The headline: full coverage is $100.99/month against a $25 allowance, and
$59.29 with the conditional second pass shipped today.** $5 is not reachable
and $9.31 is the floor with every lever stacked, two of which are unverified
model swaps. **The gate alone is $5.70 and is not optional** — it is how we
know which 1,282 of 3,156 daily candidates are worth reading — so any target at
or below $6 is a target below the cost of looking.

| configuration | total |
|---|---|
| full coverage, read-late | $100.99 |
| second pass CONDITIONAL **(shipped)** | **$59.29** |
| + extraction on `gemini-2.5-flash-lite` | $18.79 |
| + leadership free, free funding extraction, cheapest models | **$9.31** |

Where the money is, and the surprises in it:

| stage | model | $/month at full coverage |
|---|---|---|
| gate | gemini-2.5-flash-lite | $4.15 |
| extraction | deepseek/deepseek-chat | **$31.69** |
| read-through | claude-sonnet-5 | $40.14 |

- **The second pass buys ONE FIELD and no facts**, and it is now conditional.
  `interpret()` is asked for one key, writes one attribute, and sees 500
  characters of teaser against extraction's 4,000 — it cannot change a stored
  fact and cannot know more than extraction did. Extraction already writes that
  field for free. Measured on 4,171 fused-deepseek sentences against 452
  Sonnet ones, `prompts.weak_reasons` flags **8.7% of deepseek's Latin-script
  prose and 1.0% of Sonnet's** — nine to one, which is the evidence the triage
  measures what it claims to. So the frontier call is bought for the ~9% that
  need it. **−$41.70/month.** `TIT_READ_ALWAYS=1` reverts it.
  Caveat in the code: the tests find DEFECTS, not dull prose.
- **"deepseek restates the headline" is not true of the corpus.** Mean headline
  overlap 0.150 against Sonnet's 0.158. What is true: thinner (127 characters
  against 194) and hedging one time in fifteen. The earlier A/B generalised from
  one sample and this file repeated it.
- **Free extraction is 15x better on FUNDING than overall**: 33.2% of the 289
  stored funding rows close from the headline alone, against 2.2% across the
  paid path. Funding headlines state every field. 88% are Latin-script, so
  English-first is not the ceiling anyone assumed.
- **The gate is 5% of the bill.** Batching it saves ~$1.66/month, not the
  order of magnitude it looks like: `GATE_SYSTEM` is 217 tokens against ~287 of
  item text, so the shared prefix is 43% of a gate call, not the 86% it is for
  extraction. Not built; it needs the candidate loop split into a free pass and
  a paid pass, and $1.66 does not buy that risk.
- **Prompt caching is worth exactly $0** on `deepseek/deepseek-chat`: no
  endpoint serving that slug publishes an `input_cache_read` price. Re-checked
  2026-07-30. `deepseek-chat-v3.1` does, at ~0.5x, which is a **model** decision.

**What shipped.**

1. **The read-through is bought LAST.** 477 interpretations were bought against
   320 rows stored, so a third went to records a `validate` rejection or one of
   the two dedup layers settled a moment later — all free.
   `classify(interpret_now=False)` + `store.duplicate_verdict()` + 
   `classify.interpret_late()`. Safe because `content_hash` never reads the
   read-through and `build_signal` checks it only for emptiness; both asserted.
2. **The ceiling degrades, it does not halt.** `spend.py --degrade` on both
   collect jobs. Past 90% of the allowance it sets `TIT_PAID_READS=off`,
   `classify()` refuses before the gate, and the candidate defers UNMARKED.
   Free collectors, the free prefilter, deterministic extraction and both dedup
   layers keep running. The health row says `DEGRADED: monthly allowance spent`.
   `MONTHLY_ALLOWANCE_USD` 10 -> 25.
3. **`READTHROUGH_CAP` 200 -> 75, and the direction is deliberate.** The
   binding ceiling moved from the run to the MONTH: a cap of 200 does not spend
   $75, it lets demand (862 reads/day, measured) spend $75, so the allowance
   would be gone in ten days and paid reads off for twenty. Ten good days and
   twenty thin ones is worse coverage than thirty even ones. `collect.yml` sets
   google_news 45, gdelt 8, the SEC pair 40 (headroom on a demand of two).
4. **A country's second story never outranks another country's first.**
   `candidate_rank.interleave_by_country`. Scoring alone could not do it —
   forty candidates from one thin country all score `W_COUNTRY_EMPTY` and eat
   the run in arrival order. A quota was refused (most countries have nothing
   most days, so a quota spends the budget on absence). **This is what makes a
   cap of 75 acceptable: a capped run is not a random 75 of 249, it is the 75
   that buy the most countries.**
5. **The funnel is in the ledger**: `source_health.candidates / gate_calls /
   gate_rejects / budget_deferred`. `budget_deferred` is the coverage gap and
   used to exist only in a step log.
6. **`ab_models.py --extraction`** sends the production `SCHEMA_HINT` and scores
   agreement field by field on the six that decide what a record IS. Built
   because extraction is the largest line and the two swaps that would move it
   ($20.52 or $4.90) are quality decisions nobody should take on arithmetic.

**Where Germany's twelve rows come from.** Not a missing feed and not a filter.
The press run on 2026-07-30 gated 627 candidates, kept 249 and could read 200;
the 49 it refused were in Chinese, Hebrew, Serbian, German, Vietnamese and
Korean. And the "10.8% non-US" figure is about the FREE collectors — `uk_paygap`,
`sec_execcomp`, `sec_edgar`, `companies_house` are US/UK filing regimes by
construction. The paid news path is already 90%+ not-US/GB (google_news 362 of
388, national_press 180 of 205). More worldwide coverage means more paid reads
spread across more countries, which is exactly what 3 and 4 above trade off.

**Not attempted: 43-language free extraction.** `cheap_extract`'s English
restriction is not one gate but six vocabularies plus a capitalisation
heuristic, and **there is no non-English corpus here to hand-check against** —
`signals` stores headlines, not the `raw_text` a parser reads. The existing bar
is 31/31 correct. Worth ~$0.0032 per record closed (both stages, since a free
close skips the read-through too), so it stays on the list behind a captured
corpus.

Full derivation, including what was refused, in TECHLOG 2026-07-30 "worldwide
coverage priced honestly".

### Open, in priority order

| # | What | Why |
|---|---|---|
| 1 | ~~Bounded backfill slices~~ **BUILT 2026-07-29** | `backfill_slices.py`. All four backfills take one measured slice, commit it, and queue the next in the same commit; `timeout-minutes` 350 -> 90, below `LONG_HOLD_MINUTES`. Progress in a committed `data/backfill_state.json`, shown at `ops_status.py [2e]`. Proven live by run 30481065108, which also found the publish-failure gap now fixed. |
| 2 | ~~Scope breach: layoff 8-Ks stored here~~ **FIXED 2026-07-29** | It was **seven** rows, not four: + Elastic (7% of its workforce), Commerce.com, and Verizon — the row the guard was originally written for. Forward fix is a third arm, `prefilter.filing_reduction_plan`, reading the filing BODY. Backward fix is `correct_layoff_scope.py`. Measured: 3,784 filings re-read, 0 unreadable, 6 announcing a reduction (0.16%). |
| 3 | ~~Link checker + Wayback~~ **BUILT 2026-07-29, ARMED 2026-07-30, PROVEN END TO END 2026-07-30** | `link_check.py` + `archive_sources.py` + the `source_links` ledger. Scheduled from `schedule-link-hygiene.yml`, which writes a queue ticket — never from a cron in the two writers themselves, which would be evictable. Archiving every 3h, rot sweep daily. A reader filtering the live dashboard to `Marvell` sees the `Archived` link. What is left is coverage: 71 of 656 in-scope URLs, reported scoped by `ops_status.py [2c]` and mailed weekly. |
| 4 | **Re-file 12 split office rows** | They sit across two pillars, plus a 4Life duplicate filed both ways. Needs a queued `store.revise()` pass. |
| 5 | ~~Company profile pages~~ **BUILT 2026-07-29, live on 1.47.0** | `/company/{slug}`, measured threshold gate, **714 indexable pages**, every URL verified. Employer keys corrected and the three collisions merged the same day; a moved key's old URL 301s. See the sections below and TECHLOG. Next step is Search Console, not code. |
| 6 | **Country/city/industry SEO pages** | Needs a **per-cell threshold**. Thin programmatic sets get filtered at the *set* level, dragging strong pages down with them. |
| 7 | ~~Publish guardrails~~ **BUILT 2026-07-29** | `pipeline/guardrails.py`, on the write path, quarantining rather than halting. Next step is to ANSWER what it holds, not to build anything. See below. |
| 8 | ~~First live tripwire run~~ **DONE**: queries 2026-07-30, first WRITING run 2026-08-02. **Second recall measurement still open.** | 39 live queries across two runs, $0.0057 each MEASURED and reproduced, now charged in `cost_projection.py` at $0.29/month. `data/tripwire_worklist.json` holds 93 leads. What remains is the CHASE (nothing has stored a row against a lead, so cost per confirmed miss is unmeasurable) and a second recall point, without which the trend chart cannot draw. |
| 9 | **Take the preamble exit** (instrumented 2026-08-04, blocked on the exhausted key) | Extraction re-sends a 2,509-token byte-stable preamble uncached on every call — `deepseek/deepseek-chat` has no endpoint pricing cache reads, and the ledger's last 27 runs bill `cached_tokens = 0`. The priced exit is extraction on `gemini-2.5-flash-lite`: extract $27.23 -> $4.78/month at full coverage. Two proofs owed before flipping `TIT_MODEL`, both needing a topped-up key, both one command: `ab_models.py --extraction` (quality, field by field) and `ab_models.py --cache-check` (billed cached tokens, three-state verdict). Full procedure in TECHLOG 2026-08-04 "the preamble exit". |

### Non-negotiable

- **Never store an aggregator as a source** (commercial funding databases,
  startup-intelligence platforms and ecosystem directories; the domain
  blocklist in `collectors/national_press.py` is the authoritative list):
  discovery pointers only; cite the original publisher.
- **Never bypass a paywall. Never scrape LinkedIn** (`validate.py` blocks it).
- **Never write a row directly.** `extract → validate → store → publish`, and the
  raw dict **must** set `raw_text` or the extractor returns `None` silently.
- **An LLM claim is a lead, never a record.** The tripwire prefixes model-asserted
  fields with `claimed_`; the chase takes the employer name and nothing else.
- **No em-dashes in UI copy. No superlatives** on page, meta or structured data.
- **Cost ceiling $10/month** (`spend.MONTHLY_ALLOWANCE_USD`; $10 -> $25 on
  2026-07-30 -> $5 on 2026-07-31 -> $10 on 2026-08-01, all by the owner. This
  line said $25 until 2026-08-02; `cost_projection.py` is the authority, not
  this list). It holds by rationing, not by luck: dedup before the LLM, gate on
  headline+teaser only, per-language prefilters, earned cadence, deterministic
  closes, and a per-run read cap sized to the MONTH rather than the run. Feeds
  are free; only stories cost. Full worldwide coverage would be $100.99/month, so
  the cap is a real trade and `pipeline/candidate_rank.py` is what decides which
  stories fit inside it. `python3 cost_projection.py` re-derives all of it.

---

## Coordinates

| | |
|---|---|
| Repo | `dk-forge/talent-intelligence-tracker` (public — keeps Actions free) |
| Live page | https://asktherecruiter.com/blog/talent-intelligence-tracker/ |
| REST API | `https://asktherecruiter.com/blog/wp-json/talent/v1/` |
| Sibling (do not touch) | `dk-forge/ai-layoff-tracker` — live product on the same host |
| Local checkout | `/Users/dakotta/Projects/AI Talent Intelligence Dashboard` |

Run `python3 ops_status.py` first, every session. No deps, no keys.

### Verifying a deploy actually landed

A green Actions run is not proof. The run listed right after a push is usually
the *previous* commit's, so match the **commit SHA**:

```bash
SHA=$(git rev-parse HEAD)
gh run list --repo dk-forge/talent-intelligence-tracker \
  --workflow=deploy-plugin.yml -L 6 \
  --json headSha,status,conclusion \
  -q ".[] | select(.headSha==\"$SHA\")"
```

Then check the live page, with the UA the host will accept:

```bash
UA="TalentIntel/1.0 (+https://asktherecruiter.com)"
curl -s -A "$UA" "https://asktherecruiter.com/blog/talent-intelligence-tracker/?cb=$RANDOM" \
  | grep -o "css?ver=[0-9.]*"
```

That version must be the `TIT_VERSION` you just shipped, with an mtime suffix.
Grepping for `dashboard.css` will find nothing even when everything is fine —
see gotcha 0.

**There IS a `php` binary on this machine** (8.5.8 via Homebrew), and this file
said there was not. That matters, because it means the five harnesses under
`tests/php/` can be run locally before a deploy rather than only in CI:

```bash
for f in render_recall route_company_slugs render_place_pages \
         render_dashboard enrich_and_correct; do php tests/php/$f.php || break; done
```

The deploy workflow still lints every PHP file with `php -l` before it uploads,
so a syntax error fails the deploy rather than the site. Do not skip the
workflow to "save time".

---

## Job-posting volume (2026-07-29)

Job ads are the most direct evidence of hiring there is, and the tracker's
promise is to know before the ad appears — so the ads themselves have to be
counted too. `collectors/ats_boards.py` does that, and this session made the
volume a first-class thing rather than a side effect.

**What changed.**

- **Lever and Workable** join Greenhouse and Ashby. Lever is the only one of the
  four that distinguishes a missing board from an empty one (it answers
  `{"ok": false}` rather than `[]`), and it publishes posted salary bands, so
  the Ashby pay row now has a second source.
- **SmartRecruiters is withdrawn.** `https://api.smartrecruiters.com/robots.txt`
  is `Disallow: /` for every agent except LinkedInBot. The endpoint answers us
  200 anyway, which is exactly why this is enforced in code rather than by
  whether a request works. The five employers on it (Bosch, Ubisoft, Wise,
  Kiabi, Sodexo — 5,800 postings between them) moved to a `withdrawn` list in
  `collectors/ats_watchlist.json` with the reason; the parser and its fixture
  stay in place in case the terms change.
- **robots.txt is checked per ATS host** using `national_press.robots_allows` —
  imported, not reimplemented. A blocked board is reported as `robots` and left
  OUT of the failure tolerance: their terms are a decision, not an outage.
- **The archive gained the fields that make it renderable.**
  `data/ats_board_state.json` now records `company_key`, the board URL, the ATS
  and a recomputed `trajectory` per board alongside the daily counts.
- **`build_board_series.py`** (DORMANT, nothing schedules it) turns that archive
  into `wordpress-plugin/.../data/board_series.json` and can POST it to a new
  keyed `talent/v1/board-series`. `includes/board_series.php` renders it on the
  company profile as an inline-SVG sparkline with the rule printed beside it.
  The endpoint refuses any board without the URL it was counted from.
- **`resolve_ats_boards.py`** (run by hand) finds boards for employers we
  ALREADY hold signals for, and reports the name evidence behind each hit.
  Greenhouse, Workable and the Lever board page publish the employer's own name,
  so a slug belonging to somebody else is caught; Ashby publishes none, so an
  Ashby slug is a human judgement and is labelled `slug_only`.
- **Health counts what it READ, not what it emitted.** `run_collect` now reads
  `module.LAST_RUN["read"]` when a collector exposes it. Without that, a
  diff-shaped source is `degraded` every day nothing moved — which is most days,
  until nobody reads the health page. `health_digest.py` also gained
  `"ats_boards": 48`; it had been on the 14-day default while running daily.

**The direction rule, which is the part to argue with.** A board is `rising` or
`falling` only if it moved by at least 5 roles AND at least 10% over 30 days,
across at least 4 readings spanning at least 14 days. Otherwise `flat`, and with
less evidence than that, `unknown` — "we cannot tell" is a real answer and
renders as one. A rising board is evidence of hiring. **A falling board is not
evidence of cuts** and is never rendered as any: roles leave a board when they
are filled, withdrawn or reposted. Only growth is published as a signal row.

**What cannot be back-filled, ever.** These APIs publish no history and no
closed-on date, and no archive holds snapshots of them. Every series starts the
day we began counting, so a day the daily run misses is gone permanently. That
is why `collect-structured.yml` commits `data/ats_board_state.json` on
`!cancelled()` rather than on success.

---

## Link rot and archiving (built 2026-07-29, armed 2026-07-30)

`link_check.py`, `archive_sources.py`, and the `source_links` table they share.
Zero cost: no model is called by either, ever.

**Why it is load-bearing here and not merely nice.** The promise is that every
update links to the filing or report behind it. A source link that dies does not
inconvenience a reader, it silently converts a sourced claim into an unsourced
one, and the page looks identical afterwards. With 575 publisher feeds across
139 countries, many of them small national outlets, that is a certainty.

**The ledger is keyed on the URL, never on the row.** 15,631 current signals
share 12,890 distinct source URLs and the SEC collectors put thousands of rows
behind a handful of index pages, so one check and one snapshot serve all of them.
Nothing here deletes, retracts or revises a signal: the single write to `signals`
is `archive_url`, a provenance column that can reach no claim, figure, date or
source URL. Deciding what to do about a dead link is a human step, on purpose.
An automatic reaction to an HTTP code would let a publisher's bad afternoon
delete evidence.

**Do not replace this with a WordPress broken-link-checker plugin.** They crawl
POST CONTENT. Our source links live in `wp_tit_signals`, so such a plugin would
check a handful of prose links, find them healthy, and paint a green badge over
an entirely unchecked corpus, which is worse than no checker because it arrives
with a reassuring number attached. Said again in a comment at the top of both
`link_check.py` and `pipeline/source_links.py`.

### Measured on real stored URLs, 2026-07-29 (dry runs, nothing recorded)

| Population | Checked | Rotted | Notes |
|---|---|---|---|
| `national_press` | 27 | 0 | one consent-gate bounce, one HTTP 454 |
| `google_news` | 101 | 0 | 89 live, 10 bot-walled, 2 robots-disallowed |
| `gdelt` | 3 | 0 | |
| `ats_boards` | 10 | 0 | |
| **publisher subtotal** | **141** | **0 (0.0%)** | |
| random sample of the whole corpus | 150 | 0 | all live; ~90% SEC, ~8% GOV.UK |
| **total distinct URLs checked** | **291** | **0 (0.0%)** | |

**Read that number with its age and its mix.** These rows are days to weeks old
and rot is a function of time, and the corpus is ~99% SEC EDGAR and the GOV.UK
pay-gap service, both of which keep their documents indefinitely. 0% today is a
baseline, not a result. The value of the ledger is the SECOND measurement, and
the per-publisher breakdown after it: a publisher going from 0% to 60% has
changed its URL scheme, which is a fix rather than a lament. It also means the
rot to watch is entirely in the 141-URL publisher tail, which is growing fast
and is where the 139-country catalogue lands.

**Wayback coverage already held, before we capture anything:**
publisher URLs **38/131 (29%)**, SEC and GOV.UK URLs **4/150 (3%)**.

That gap decided the collector default. EDGAR and the GOV.UK pay-gap service keep
their own documents indefinitely; the small-outlet tail does not. Spending a
40-capture per-run budget on 12,700 SEC index pages would take most of a year to
preserve documents a government already preserves, so `archive-sources.yml`
defaults to `--collector national_press,google_news,gdelt,ats_boards`. Blank it
once the tail is covered.

### The finding that justifies the drift guard

The sweep's one loud hit was `hln.be`, which answers 200 and lands on
`myprivacy.dpgmedia.be` — a different registrable domain. That is a consent
gate, not a takeover, and the checker now says so, because the gate carries the
article URL back with it in its callback and a squatter has no reason to name the
document it replaced. The distinction is the whole point: `drifted` has to keep
meaning "somebody else is serving this now" rather than degrading into a list of
European cookie banners. The case it is really for is
`botswanaguardian.co.bw`, which became a betting site whose feed verified
perfectly green. A cited article that quietly becomes a casino is worse than a
404, because a 404 announces itself.

### They are armed — from outside their own files (2026-07-30)

Both write the database, so both hold the single `talent-collect` lock, so
**neither may carry a cron of its own.** A scheduled run enters that group as an
uncoordinated third body and either evicts the pending run or is itself evicted,
ending `cancelled` with zero jobs, no logs, no annotation, and inputs GitHub
will not disclose — an orphan a human has to close by hand. There are 15 of
those in `data/writer_queue.json` from 2026-07-29.

So the schedule lives in **`.github/workflows/schedule-link-hygiene.yml`**, which
is not a writer and holds no lock. It writes a *ticket*:

| slot | ticket | run |
|---|---|---|
| `20 */8 * * *` | `archive-sources.yml`, `dry_run=false` | Wayback pass, three times a day |
| `30 5 * * *` | `link-check.yml`, `dry_run=false` | daily rot sweep, before the 13:00 Monday digest |

**The cadence was retuned twice and the arithmetic is the argument both times:
nightly -> every 3h on 2026-07-30, then 3h -> every 8h on 2026-07-31.** The
scheduled scope holds 716 distinct source URLs, 227 archived, and the real
capture queue is 444 confirmed absent from Wayback. A run resolves 15-30% of
what it examines from the free availability API and captures at most 40 more,
roughly half of which land first try, so a run is worth about twenty snapshots.
Eight runs a day clears that queue in about three days; three runs a day clears
it in about eight.

**Eight days is the right trade, and the lock is why.** Every run holds the
single `talent-collect` slot for up to 25 minutes (`DEFAULT_DEADLINE`). Eight
runs is 200 minutes a day — a seventh of the day with collect, enrich and every
backfill slice queued behind an archiver, then landing on the WordPress host in
a burst when it clears. Three runs is 75 minutes. Bluehost 504'd for everything
under `/blog/` twice on 2026-07-30/31; smoothing that bunching is worth more
than five days of archive latency.

And it was never hourly. The sibling tracker's own hourly archive sprint was
audited and REVERTED on 2026-07-30 after three consecutive runs were handed 0, 2
and 7 candidates: rate does not buy yield once the queue is short. Our 444 is a
real backlog today and will not be one next week, at which point every extra
slot is a 25-minute lock window spent on a no-op. If the queue is still long in
two weeks the lever is `spn_max` or the `collector` scope, not this cron.

The rot sweep went weekly to daily for a plainer reason: 150 URLs a week against
14,796 cited documents revisits a given link about twice a decade, which is not
a check.

`drain-writers.yml` dispatches each ticket only into an EMPTY group, so it cannot
be evicted; if one somehow is, its inputs are on file and it is re-dispatched
automatically. The enqueue is `--if-absent`, so a slot firing while a six-hour
backfill holds the lock waits rather than stacking a ticket per night.

To run one by hand, queue it — never dispatch it:

```bash
gh workflow run drain-writers.yml -f enqueue=link-check.yml \
     -f inputs_json='{"dry_run":"false","random":"true","limit":"200"}' \
     -f reason='ad-hoc rot measurement'
```

The leashes in `staleness.py` move with the cadence (2400h -> 54/180 when they
were armed -> **`archive_sources` 26, `link_check` 36** at the current one), or a
checker that stopped running would look exactly like a checker with nothing to
report. The test now DERIVES the bound from the cron rather than pinning a
number, so the next cadence change cannot leave a dead job looking healthy for a
fortnight. `ops_status.py [2c]` prints the arming state, derived from the
workflow files, and goes red if either writer ever grows a cron.

**A dry run cannot refresh that clock**, which is the other half of the guard: a
hand dispatch carrying the `dry_run=true` default records nothing to
`source_health`, so a schedule that quietly went dry still ages into STALE.

**Coverage the schedule can actually reach.** The pass is pointed at the
publisher tail (`national_press,google_news,gdelt,ats_boards`), which is 656 of
14,796 distinct source URLs — 4.4%. The other 95.6% are SEC and GOV.UK filings
whose publishers keep them indefinitely, so that share is this schedule's ceiling
and not a stall; `[2c]` says so next to the percentage. Widen it by editing the
collector default in `archive-sources.yml`.

Which is why coverage is now reported SCOPED as well.
`source_links.archive_coverage()` measures archived-over-in-scope (71/656,
10.8%), a ratio with a ceiling of 100% that moves when the job runs, rather than
archived-over-corpus (0.5%), which reads a healthy archiver as a stalled one.
`ops_status.py [2c]` and the weekly digest call the SAME function, for the reason
the staleness leashes live in one module: a dashboard and an email disagreeing
about this number would leave a session no way to tell which was lying.

**And the silence now has an alarm of its own.** `health_digest.archiving_stalled()`
fires when work is outstanding AND no snapshot has landed in seven days, and it
is in `needs_human`, so it mails. Nothing else here can see that failure: a job
that runs green every three hours and records nothing is not stale, not degraded
and costs nothing. It happened on 2026-07-30 — run **30507215991** went out by
direct dispatch, took the `dry_run=true` default, examined 164 URLs, found 24
already in Wayback and recorded NONE of them — and went unnoticed for a day
because no number anybody reads described it. The digest reports SOURCE LINKS
every week now, findings or none: a metric that appears only once it is bad
cannot show a slow slide.

**Verified:** the recording path. Runs 30473757174 (link-check, 17:05Z) and
30474293718 (archive-sources, 17:12Z) on 2026-07-29 both recorded, merged and
pushed — commits `f56164e` and `c18288e`. 72 rows now carry an `archive_url`.

**Verified 2026-07-30:** the reader-facing fallback link, which this document
previously recorded as unverified. Plugin **1.58.0 is live**, and filtering the
dashboard to Employer = `Marvell` renders

```html
<span class="tit-archived"><a href="https://web.archive.org/web/20260729172104/https://inc42.com/buzz/semiconductor-major-marvell-to-invest-250-mn-in-india-double-headcount/"
   rel="nofollow noopener" target="_blank"
   title="Archived copy at the Internet Archive">Archived</a></span>
```

beside the publisher's own link. The whole path holds: `archive_sources.py` ->
`source_links` -> `project_archive_urls()` -> `/enrich` -> `wp_tit_signals` ->
`dashboard.js`. Note that the default `detail=notable` filter hides most rows, so
a row with a snapshot is easiest to find by employer.

**Still unverified:** nothing on this path. What remains is coverage, and
coverage is now a number rather than a hope.

---

## The discovery tripwire (built 2026-07-28, DORMANT)

`run_tripwire.py` + `analysis/tripwire/`. It asks a search-backed model what
happened, diffs the answer against what we hold, and emits the difference as a
WORK LIST. It exists because the owner found four Israeli rounds we had missed
by asking Gemini: an outside view does not share our feeds' blind spots, and no
amount of care inside the pipeline can find a story no feed carries.

| | |
|---|---|
| Dimensions | countries (rotating, prioritised by measured recall) + industries (all 18, once a month). NOT cities: you find a Tel Aviv round by asking Israel |
| Priority | straight off `analysis/recall/results/` — a country that held nothing gets 3 of every 4 slots; falls back to a stated guess if no measurement exists |
| Budget | `plan.TRIPWIRE_MONTHLY_USD = $1.00`. The query count is DERIVED from it: 8 runs x 4 countries + 18 industries = 50 queries/month |
| Enforcement | `spend.py`, not a second mechanism. The run reads the month against the product allowance and declines to spend rather than failing red |
| Output | `data/tripwire_worklist.json` (stable path) + `analysis/tripwire/results/tripwire-DATE.json` (dated trend), with per-country and per-industry miss counts |
| Proof | `python run_tripwire.py --offline` — whole path, no network, no key, no spend |

**The rule that makes it safe: a tripwire hit is a LEAD, never a record.** Every
model-asserted field carries a `claimed_` prefix and dies in the work list.
`collectors/tripwire_chase.py` takes the employer's NAME and nothing else,
searches Google News for the publisher's own article, and sends THAT through
`classify -> validate -> store` like any other candidate. A hallucinated company
finds no articles and stores nothing. A real company whose round the model
mis-sized still stores the right size, because the size comes from the article.

**ARMED 2026-07-30, and priced by measurement rather than by estimate.** Live
queries went out that day (run 30506967802): 17 search-backed queries against
`perplexity/sonar`, $0.0977 billed, **$0.0057 a query**, spread $0.0054-$0.0060,
so the $0.02 estimate is 3.5x conservative in the right direction. The Israel
query — the one a human can check by eye — cost $0.0059 and returned 8 leads.
`analysis/tripwire/plan.py` holds the figure and its source string;
`cost_projection.py` charges it against the allowance as **$0.29/month** for 50
queries. The estimate still SIZES the plan and the measurement REPORTS it: two
numbers doing two jobs, and the gap between them is the safety margin.

**The first WRITING run landed 2026-08-02** (run 30731489198, commit 02a8df3),
queued through `drain-writers` and never dispatched directly. 22 queries,
**$0.1248**, $0.0057 each, 108 usable leads against 25,152 stored signals, 15
already held, **93 missing**; $0.0012 per usable lead. `data/tripwire_worklist.json`,
`analysis/tripwire/results/tripwire-2026-08-02.json` and one `source_health` row
are on main, so the chase collector finally has something to read and the trend
has its first point. The price reproduced exactly across two runs three days
apart asking entirely different sets (39 queries, $0.2225), which is stronger
evidence than one larger sample.

**Still unmeasurable: cost per CONFIRMED miss.** That needs
`collectors/tripwire_chase.py` to store a row against a lead, and it has not
run. 93 leads are sitting in the work list waiting for it — that is the next
step, and it is a decision about spend, not a missing piece of code.

---

## The 2026-07-28 render lessons (numbers re-checked 2026-07-29)

Written the day every control on the dashboard was found inert in production.
The findings below are permanent properties of this host and this theme, so they
are worth reading before you touch the page. **The figures the section shipped
with are gone, because they were wrong within a day:** it said plugin v1.24.0,
44 records and 219 tests; live is **1.47.0**, the database holds **15,650**
current signals with **15,649** published, and **1,168 of 1,174** offline tests
pass. Do not treat anything here as a description of today's page. The current
page is described at the top of this file.

### Autoptimize aggregates inline scripts, and the exclude filter only matches paths

`dashboard.js` opened with `if (!root || typeof TIT === 'undefined') return;`
and `TIT` was undefined on the live page, so the file returned on its first
statement. No filter, no region tab, no quick view, no sort, no facet
population, ever. Nothing errored and the page looked completely normal.

Cause: **Autoptimize aggregates INLINE scripts, while
`autoptimize_filter_js_exclude` only matches assets by path.** Excluding
`plugin/assets` kept `dashboard.js` where it was and swept the inline object
`wp_localize_script` prints into a bundle that loaded *after* it. A path-based
exclude cannot name an inline object, so it separates a script from the data it
depends on while looking like it protected both.

Fixed at both ends: config also rides on `#tit-dashboard` as `data-` attributes,
and the exclude list names `var TIT` too. **If you add a localized script, do
both.** This is how the optimiser works, not a bug that was fixed once.

### The same exclude changed CSS source order, and the theme capped the page

Twenty Twenty-Five caps children of `.is-layout-constrained` at the global
content size. Our `.tit-wrap` max-width has the *same specificity*, so which one
wins comes down to source order, and source order changed the moment our CSS
stopped being inlined by Autoptimize and became a `<link>`. The dashboard went
1160px to 645px with no CSS change of its own. Now pinned to
`var(--wp--style--global--wide-size)` (1340px), which is what a full-width block
gets and what makes the sibling as wide as it is. **Changing what the optimiser
touches can move layout without touching a stylesheet.**

### A green deploy proves an upload, not a render

Both findings above shipped green. Check the deployed page for behaviour, not
just for the version string.

### Card rules keyed to one table's columns are a coincidence, not a component

`.tit-sources .tit-table` set `min-width:720px`, which beat the mobile
`min-width:0` and kept the sources table 720px wide inside a phone. Default
every cell to full width and let cells opt in.

### Do not cache-bust the live host in a loop

Appending `?cb=<random>` to every request bypasses Cloudflare and hits the
origin directly, and doing that dozens of times in a session is what shared
hosting throttles. Use it once to read the origin's truth, never to poll.

### A partial vocabulary fails silently and looks like sparse data

`tit_country_names()` held 52 of ~200 codes, so LV and NA printed as raw codes
inside a chart of country names. Region code lists were shortlists in the same
way: Namibia was missing from Africa, so that record counted under World only.
Both carry the world now. Any new fixed vocabulary needs the whole set on day
one, because the failure mode is a plausible-looking number, not an error.

### Where that section's four open items landed

1. **The OpenRouter key was raised.** It was ~$1.90 into a $5 lifetime cap and
   only the owner could lift it. Read-throughs now run in production and hit
   their own 60-per-run cap, which is spend the old ceiling could not have
   covered, so the raise is real. The exact headroom is not readable from here:
   `spend.py` needs the key. The $5-lifetime versus $10-monthly contradiction in
   Secrets is still unresolved.
2. **The cron fires. It is not reliably green.** Of the last 30 `collect.yml`
   runs, 4 are `schedule`: 2026-07-28 07:02Z and 18:36Z both succeeded,
   2026-07-29 07:05Z was cancelled, and 2026-07-29 18:27Z failed at the publish
   step with `PUBLISH FAILED: 8 open guardrail finding(s) across amount,
   vehicle_name` after collecting normally. So the thing to watch is now the
   conclusion of each run, not whether the schedule exists. The first firing
   landed ~1h after its 06:00Z slot, which is GitHub's queue rather than a bug.
3. **Top nav is done.** The site header carries "Talent Intelligence Tracker"
   beside "AI Layoff Tracker". It is still a `wp-block-navigation` block in the
   database, so it still cannot be changed from this repo: Appearance → Editor →
   Navigation.
4. **Read-through quality** was gated on the key and no longer is. Whether it
   still restates the headline on leadership records is an unmeasured question
   now, not a known-bad one.

### The page has moved on from this section

The at-a-glance tiles described above are gone: `.tit-glance` survives as a
period matrix table (`tit-matrix`, periods as columns) and there is no
`tit-tile` anywhere in the live markup. The `.tit-span` note the session added
does survive, now reading "Covering 28 Jun 2017 to 29 Jul 2026." Region tabs are
World / Americas / Europe / Middle East / Africa / Asia / Oceania, not the
USA / Canada / UK / India set the section describes.

### Deliberately NOT built, each justification re-checked 2026-07-29

Four things were left unbuilt because the data could not support them. None has
since been built, but **two of the four justifications have expired**, so those
two are now unbuilt for no stated reason. They are listed rather than deleted so
nobody rebuilds them blind or leaves them out forever by inheritance.

- **Years/quarters/months cascade.** Justified by "8 days of data".
  **EXPIRED.** `published_date` on current rows runs 2017-06-28 to 2026-07-29,
  and the page says so itself in the span note. Nine years of history is a
  cascade's use case, not the argument against it. Decide this one on merit.
- **Sources dropdown.** Justified by "32 distinct sources across 44 records",
  which was close to one source per record, so the control would have listed
  near-unique values. **EXPIRED.** There are now 146 distinct `source_name`
  values across 15,650 current rows, roughly 107 rows per source. Note the cost
  is more than a `<select>`: there is no `source_name` filter in
  `tit_build_where` and `/facets` returns no source list, so this is API work.
- **Minimum-headcount filter.** Justified by "`headcount` on 1 of 44 records".
  **STILL HOLDS, and more strongly than before.** `headcount` is non-null on 8
  of 15,650 current rows, 0.05% against the original 2.3%. `min_headcount`
  already exists in the API as `headcount >= %d`; exposing it in the UI would
  cut the page to 8 rows. What shipped instead is the inverse control, an "Only
  show updates that state a headcount" checkbox, and **that checkbox is keyed on
  `signal_direction IN ('hiring', 'displacement')`, not on the `headcount`
  column at all.** Its count (52 at the API, 50 on the cached page) is a count
  of hiring and displacement rows. Do not read it as headcount coverage.
- **Chart embed.** Justified by "needs an embed route this plugin lacks".
  **STILL HOLDS.** The registered routes are `/add`, `/aggregate`, `/alert`,
  `/bulk`, `/correct`, `/enrich`, `/facets`, `/health`, `/query`, `/retract` and
  `/source-health`. There is still no embed route.

---

## Current state (2026-07-27, late)

**Live:** plugin **v1.13.0**, 13 records, **209 tests** green.

Three page templates, all rendering:
`/talent-intelligence-tracker/`, `.../sources/`, `.../company/{slug}/`.

**The page:**
- Hero on the off-white ground: title and live pill on one line, then four
  at-a-glance cells (today / this week / this month / year), then one line of
  fine print carrying the totals. A period with nothing in it still prints and
  says so.
- **No stat tiles on the dashboard.** They repeated the numbers already in the
  hero's fine print. The sources page and company profiles keep theirs, where
  nothing repeats them.
- Region strip using the sibling's `.alt-tab` pill pattern, a hue per region.
  Regions with nothing in them are dropped; World always survives.
- Three chart cards in one row, plain HTML and CSS, no chart library.
- Eight filters in one panel, then the table.
- **Below 860px the table becomes cards**, each cell labelled from `data-label`.
  Below 700px the wrap goes full bleed to cancel the theme's two nested padded
  containers, which otherwise left it 219px wide on a 375px screen.
- Whole page is capped at 1160px; the theme's container runs to ~1500px.

**Design tokens are the sibling's**, read out of its live `layoffs.css` rather
than approximated, so the two products are one family: blue `#2a78d6`,
secondary `#D55E00`, ink `#16181d`, border `#e2e3e8`, surface `#f7f8fa`,
ground `#fafaf8`, system-ui type. One divergence: the sibling's `--alt-muted`
(`#868a93`) is ~3.4:1 on white and fails AA, so it is decorative-only here and
readable text uses its `--alt-ink-2` (`#4a4d55`, 8.6:1).

**Sources, after the audit:** 160 listed, **4 running**. The imported catalogue
was cut from 383 rows to 111 by one rule — a row survives only if it carries an
RSS feed, an API, or is an official filing system. 272 rows were names with
nothing to connect to. Pinned by `tests/test_sources_page.py`.

**What has actually produced a stored record:**

| collector | stored |
|---|---|
| `sec_edgar` (8-K Item 5.02) | 6 |
| `google_news` | 4 |
| `sec_form_d` | 3 |
| `gdelt` | **0** |

GDELT throttles erratically (~50% success even at 12s spacing) and has never
produced a record. It is the next thing to either fix or retire.

**Google News is multilingual (v1.12.0).** 25 national editions, 7 languages,
three rotating per run plus a fixed US anchor.

> **The trap, if you touch this:** rotating `hl`/`gl`/`ceid` alone does nothing.
> Measured 2026-07-27, the same English phrases returned US:en 23 items, DE:de
> **2**, BR:pt **0** — and German phrasing returned **20** from that same German
> edition. Each edition must ask in its own language (`GOOGLE_NEWS_VOCAB` in
> `source_registry.py`), and `prefilter.py` needs the matching non-English terms
> or every candidate is dropped for free before the model ever sees it. A locale
> without a phrase set is a silent zero dressed up as coverage;
> `tests/test_locale_rotation.py` refuses to let one exist.

Going multilingual took a run from ~25 candidates to ~215, so
`DEFAULT_CANDIDATE_CAP = 40` lives in `run_collect.py`. The cap is a **fair
share** (one item per query in turn), not a head slice: the sibling's flat
`MAX_ITEMS` meant a broad sweep filled the cap and the targeted queries never
fired.

**Still not done:**
- ~~Collection is DORMANT~~ **ARMED 2026-07-27**, 06:00 and 18:00 UTC, after six
  live dry runs. To disarm, comment the two schedule lines out again; nothing
  else changes. Spend is capped independently (`spend.py` exits 1 at 90% of the
  monthly allowance, `DEFAULT_CANDIDATE_CAP` bounds each run at 40 candidates).
- **Filters change the table but not the charts or the hero figures.** The
  stated goal is "every number, chart and row below updates to match"; the
  charts are server-rendered from the unfiltered set and do not re-fetch. This
  is the largest remaining gap in the UI.
- No date-range control, no sort, no quick views
- Model switch (Gemini Flash-Lite gate + Haiku read-through) designed, not applied
- 13 records is thin enough that any layout looks sparse. "Europe 1" is a real
  tab with one row behind it. The template holds up; it needs volume.

---

## The one thing that took six hours to learn

**Every record needs a URL that is a receipt for the claim, and a homepage is
not one.** That is the whole difficulty of this product. Two live records once
linked to `crn.com` and `ft.com` front pages; both were retracted, and
`validate.py` now rejects a URL whose path is empty.

**Correction, and read this before you touch `google_news.py`.** An earlier
version of this document said Google News article URLs were unrecoverable and
that the encoded token had been tested and did not contain them. **That was
wrong.** Google exposes its own resolution endpoint: the article page carries a
signature (`data-n-a-sg`) and a timestamp (`data-n-a-ts`), and posting those to
`news.google.com/_/DotsSplashUi/data/batchexecute` returns the publisher URL.
`resolve_source_url()` does this and it works.

Two ways that hunt goes wrong, both survived here:
- Decoding the base64 token and finding no URL proves only that it is not *in*
  the token. It does not prove Google will not resolve it for you.
- The URL comes back inside an **escaped** JSON string, so the natural
  `"(https?://[^"]+)"` stops at the backslash and matches nothing — which reads
  exactly like "not in the response". The live regex allows for the escaping and
  is pinned by `tests/test_google_news_resolution.py`.

Resolution is best-effort. On failure the item keeps the outlet homepage from
the RSS `<source>` element, and `validate.py` rejects it as a bare domain rather
than crediting the aggregator.

**GDELT** returns real article URLs but its throttling is erratic and its yield
collapsed to zero on a live publishing run. It has still produced no record.

**SEC EDGAR is the source that works.** 8-K Item 5.02 filings are legally
required within four business days, always have a real `sec.gov` document URL,
are primary sources (so records earn `verified`), and SEC allows 10 req/s.
`collectors/sec_edgar.py`.

---

## What the first REAL runs found (the dry runs could not)

**The commit-back raced and lost 28 records.** A run checks out main, collects
for several minutes, then pushes. A plugin deploy landed in that window, the
push was rejected as non-fast-forward, and the commit existed only on the
runner: collection happened, money was spent, nothing reached the repo. Six dry
runs could never have found this, because a dry run writes nothing. The step now
resets to the current main, puts our database back and retries five times,
failing loudly if it still cannot push. It hit the retry path on its very first
real run ("Pushed on attempt 2").

**GDELT was retired and un-retired the same day.** It had produced zero records
in its whole life, so it was retired on the "coverage is earned" rule. That was
the wrong attribution: the cause was the six pipeline bugs below, not GDELT.
Its first run with those fixed stored three, including a Namibian recruitment
drive and a V&A strike ballot that no Google News edition we query carries.
**A source that yields nothing is either a dead source or a broken pipeline, and
from outside the two look identical. Retire nothing until the pipeline is proven
on a source that does work.**

**A funding floor is not needed yet.** The worry was that admitting news funding
would fill the page with micro-rounds. Every raise actually stored is $1.45M or
more; the sub-$1M rounds were already being dropped further down the pipeline.
Revisit only if a real run stores one.

---

## What six dry runs found

Each of these was invisible until a real run was read line by line. Every one
looked like a quiet news day from the outside.

| # | Bug | How it showed up |
|---|---|---|
| 1 | The free filter killed **every funding story** | 78 of 96 filtered candidates were raises. A funding headline has no employment word in it, so a pillar named in the page's own headline had never produced a news record |
| 2 | "No geography" **discarded paid candidates** | 6 of 12 classified records thrown away after the model had been paid, all real leadership changes. Geography is how we segment; it is not what makes a record true |
| 3 | The prompt was **too timid to record a country** | One read-through said "in Egypt" while the country field was empty. "Named IN THE TEXT" was read so literally that datelines and nationalities did not count |
| 4 | **OpenRouter 429s counted as rejections** | 5 candidates lost in one run, OpenAI tripling its Dublin headcount among them. A busy provider read as the model declining the story |
| 5 | The country vocabulary knew **23 countries** while we queried 25 editions | Philippines and Egypt both normalised to None. The model was right and the vocabulary threw the answer away |
| 6 | The **publisher never reached the classifier** | "USTA SC names new CEO" places nowhere alone; from the Post and Courier it is South Carolina. `source_name` sat in the item, unused |

Measured across the six runs: **4/12 stored → 11/14 stored, 2/11 placed → 6/11
placed, 0 deferred.** The remaining unplaced records are funding wire stories
where the outlet genuinely implies no country, and those now say "Location not
stated" rather than being discarded.

---

## Guards that exist, and the live incident behind each

Do not weaken these without understanding what they caught.

| Guard | What it caught |
|---|---|
| Bare-domain source rejected | Two live records linking to `crn.com` / `ft.com` homepages |
| Job adverts rejected (`/jobs/`, `/careers/`, job-board hosts) | Stored "Claims Strategy Manager - Remote at Allstate" |
| Civil-service exam notices filtered | Stored UPPSC PCS and Indian Navy SSC recruitment notices |
| Figures must appear in source text | A model-invented headcount is the fatal failure mode |
| Confidence capped by source | News can never be promoted to `verified` |
| Aggregators never stored as source | Google News redirect as a citation |
| `"expansion"` removed from vocabulary | MLB, World of Warcraft, Medicaid, cattle herds |
| Retraction survives re-collection (`dedupe.exact_duplicate` ignores `is_current`) | A retracted homepage-sourced record came back; checking only current rows also crashed the run with an IntegrityError, because the unique index spans all revisions |
| Non-English prefilter terms | Without them the multilingual queries fetch correctly and every candidate is dropped for free — zero records, looking like it works |
| Catalogue rows must be connectable | 272 spreadsheet names rendered as "researched" read as coverage we do not have |

---

## Gotchas that cost real time

**0. Grepping the page for `dashboard.css` finds nothing, and the CSS is still
loading.** This site runs Autoptimize. It rewrites our enqueue to
`wp-content/cache/autoptimize/css/autoptimize_single_<hash>.css`, carrying our
version string through as `?ver=`. So `grep dashboard.css` on the served HTML
returns zero while every rule is present and applying. To check whether the
stylesheet is really live, grep for `?ver=<TIT_VERSION>` instead, then curl that
file and grep it for a selector you shipped.

The real hazard underneath: Autoptimize keys its rewritten copy on the version
string we hand it. `TIT_VERSION` alone was not enough, because an FTP deploy can
ship a CSS-only fix without the constant moving, and visitors then keep the old
rewritten copy. Assets are versioned `TIT_VERSION . '.' . filemtime()` for that
reason (`tit_asset_version()`), which is also what the sibling does. Pinned by
`tests/test_plugin_separation.py`.


1. **OpenRouter + `require_parameters` + `response_format` excludes all Claude
   endpoints** — 404 "No endpoints found". Skip both for `anthropic/*`.
2. **OpenRouter routes across providers; some ignore `response_format`** and
   return empty content. `provider: {require_parameters: true}` pins routing.
3. **Bare `pytest` does not put the repo root on `sys.path`** — `pytest.ini` has
   `pythonpath = .`. `python -m pytest` masks this.
4. **A shell ternary in a plain YAML scalar** (`? 1 : 0`) reads as a mapping key
   and GitHub rejects the whole workflow with no logs. Use block scalars.
5. **`2>/dev/null` inside an lftp script** is parsed as a path, not a redirect.
6. **`workflow_dispatch` inputs come from the default branch** — a new input
   needs a push before dispatch works, and a failed string-replace silently
   leaves it out (this happened; verify with grep).
7. **The parent WP theme washes out our text** — colours need enough
   specificity to win, or stat tiles render near-invisible grey.
8. **FTP account is chrooted to the WordPress root.** Path is
   `/wp-content/plugins/talent-intelligence-tracker`, no `public_html` prefix.
9. **ModSecurity blocks curl's default UA and a browser UA alike.** Use
   `-A "TalentIntel/1.0 (+https://asktherecruiter.com)"` or the host returns a
   "Not Acceptable!" HTML page where you expected JSON.
10. **Easy Table of Contents injects itself into the middle of the hero.** Any
    post with headings gets one. Suppressed on our routes only, in `page.php`
    plus a CSS fallback; ordinary blog posts keep theirs.
11. **Our own routed pages have no theme container.** `sources.php` and
    `company.php` call `get_header()` and render straight into the body, so
    they ran edge to edge with no gutter until `.tit-sources` / `.tit-company`
    got their own 1160px container. The shortcode page must be excluded from
    that or it double-pads.
12. **Block themes set `margin-inline:auto` on every direct child of
    `.entry-content`,** which beats a single-class selector — the phone
    full-bleed rule silently moved nothing until the selector out-specified it.
    And `max-width:100%` resolves against the *padded* container, so it pinned
    the width back even once the negative margins applied.
13. **`pytest -q | tail -2 && git commit` ships a red suite.** `tail` exits 0
    whatever pytest did, so the `&&` chain continues and CI catches it a minute
    later. Run pytest on its own line and read the exit code.
14. **Tabular figures pad a narrow `1` to a full advance width.** Right in a
    stacked column, wrong for a single inline number: `13` rendered as `1 3` on
    every tile. Use proportional numerals for standalone figures.

---

## Secrets (all set, in GitHub repo secrets)

`OPENROUTER_API_KEY` — the key carries its own hard cap set in the OpenRouter
dashboard, which is the real guarantee. **This was recorded here as a $5
lifetime cap while `spend.py` enforces a $10 monthly allowance; the two
disagree and only the owner can say which is current.** If the key cap is
genuinely $5 lifetime it binds first and `MONTHLY_ALLOWANCE_USD` never fires.
Check the dashboard before sizing anything.
`WP_API_KEY` (must match the key set in WP admin → Talent Intel),
`WP_SITE_URL` (must end `/blog`), `FTP_HOST`, `FTP_USERNAME`, `FTP_PASSWORD`,
`FTP_PORT`, `WP_PLUGIN_REMOTE_DIR`.

**No Railway.** A leftover service exists; it plays no part. Collection runs on
Actions because the SQLite DB must be committed back to the repo.

---

## Cost, measured not estimated

**The ceiling is enforced in code, not hoped for.** `spend.py` runs as the first
step of every collect job and exits 1 at `STOP_AT_FRACTION` (0.9) of
`MONTHLY_ALLOWANCE_USD` (10.0). The OpenRouter key also has its own hard cap,
which is what makes it a guarantee rather than a policy.

- Gate call: 141 tokens in / 35 out (measured)
- `deepseek/deepseek-chat` (current): ~$1.15/month at 660 items/day
- `google/gemini-2.5-flash-lite`: 90% agreement with incumbent, ~$0.56/month
- `anthropic/claude-haiku-4.5` matches Sonnet 5 on read-through quality at half price
- Spent to date: **~$1.86**, last measured 2026-07-27, mostly on the model A/B

`spend.py` needs `OPENROUTER_API_KEY` to report; without it, it says so and
exits rather than guessing. Model A/B is reproducible: `ab_models.py`, workflow
`ab-models.yml`.

**Sizing anything new:** cost scales with candidates reaching the model, not
with source count. Candidates are gated by `prefilter.passes()` (free), then
already-seen URLs are skipped (free), then `DEFAULT_CANDIDATE_CAP` caps what is
left. 40 per run, twice a day, is ~2,400 classifications a month.

---

## Next steps, in order

Done: the page overhaul, the spend ceiling in code, Form D, company profiles,
multilingual Google News, the source audit, the six pipeline bugs, and arming
collection. What is actually left:

1. **Watch the first three armed runs** (06:00 and 18:00 UTC from 2026-07-28).
   Read them the way the dry runs were read. Check specifically: how many are
   deferred rather than rejected, whether spend tracks the projection, and
   whether `source_health` goes degraded on any of them. The dry runs never
   exercised dedup or the commit-back, because nothing was written.
2. ~~Make the filters drive the charts and the hero figures~~ **Done
   2026-07-28**, including the at-a-glance tiles, which were the last part of
   the hero still contradicting its own filters. See the 2026-07-28 section.
3. **Fix or retire GDELT.** Still zero records. Retiring it is a legitimate
   outcome, and the sources page must then say 3 running, not 4.
4. **A funding floor, or a decision not to have one.** The news path now admits
   raises of any size, so a pre-seed of a few hundred thousand lands beside a
   $130M Series B. `sec_form_d` uses MIN_RAISED = $1M. Watch a few real runs
   before choosing a number; it is easier to judge with rows on the page.
5. **Date range, sort, quick views** on the table.
6. **Model switch — measured 2026-07-28, NOT applied, and the reason matters.**

   | model | agrees on signal | agrees on pillar | $/item | $/month @660/day |
   |---|---|---|---|---|
   | `deepseek/deepseek-chat` (incumbent) | 100% | 100% | 0.000064 | $1.27 |
   | `google/gemini-2.5-flash-lite` | 72% | 100% | **0.000033** | **$0.65** |
   | `openai/gpt-oss-120b` | 75% | 100% | 0.000048 | $0.96 |
   | `meta-llama/llama-3.3-70b` | 79% | 100% | 0.000363 | $7.19 |
   | `openai/gpt-5-nano` | n/a | n/a | — | 40 errors, unusable |

   **Read the disagreements before reading the percentages.** Every one went the
   same way: the incumbent said reject, the challengers said SIGNAL, and the
   headlines were *"Enigma Raises $71M in Seed Funding"*, *"Peace Coffee PBC
   raised $1.9M"*, *"Holobiome raised $10M"*. Gemini is not disagreeing with the
   incumbent, it is **correcting** it. The sibling's "reject below 90%
   agreement" rule would throw out the better model, because that rule assumes
   the incumbent is the reference truth and here it is not.

   So the case for gemini-2.5-flash-lite is strong: half the cost, and it reads
   funding correctly on the short gate prompt where deepseek needs the long
   production one with worked examples.

   **Why it is still not applied.** The read-through is this product's
   differentiator and this A/B only tested the gate. `ab_models.py
   --readthrough` exists to test it and was not run, because the key had $1.90
   left and burning it on benchmarking would stop collection. Run that first,
   then switch. Switching halves ongoing spend, so it is worth doing early once
   the key limit is raised.

   **Status 2026-07-30: the GATE half is applied.** `classify.GATE_MODEL`
   defaults to `google/gemini-2.5-flash-lite` (overridable via
   `TIT_GATE_MODEL`), citing this A/B in its comment. The read-through stays
   on `deepseek/deepseek-chat`, gated behind the quality A/B above — that
   half of this item is what remains open.
7. **More languages for Google News.** Adding a language is what adds countries:
   a phrase set in `GOOGLE_NEWS_VOCAB`, the matching terms in
   `prefilter._EMPLOYMENT_TERMS_INTL`, then its locales in
   `GOOGLE_NEWS_LOCALES`. Never add a locale without the phrase set.
8. **Reconcile the spend cap contradiction** (see Secrets): $5 lifetime on the
   key versus $10 monthly in `spend.py`. Now that collection actually runs,
   this stops being theoretical.

---

## Rules that are not negotiable

- No source URL, no record. A homepage is not a source.
- The model never invents a number: figures must appear verbatim in `raw_text`.
- Confidence is earned by the source and never promoted.
- Never overwrite a record — append a revision (`store.revise`).
- Layoffs are NOT collected here; read the sibling's public API.
- Coverage is earned: a market in the registry is not a covered market.
- Never publish fabricated records to the live site, for any reason.

## 2026-08-04: main is GREEN, and the world got wider

`tests/test_audit_publishers.py` had reddened `main` for six commits. It was never
a broken test. It named 13 publishers that each cost a gold-set event and for which
the catalogue held neither a feed nor a written reason. All 13 are now closed
honestly, test byte-identical, nothing skipped or xfailed, full suite 0 failed /
3193 passed.

**Wired, 8 publishers / 10 feeds**, item counts observed at probe time:
aviacionline.com (Argentina), bursa.ro (Romania, two feeds, one carries the ESPI
current reports the gold miss came through), ecosistemastartup.com, liputan6.com
(Indonesia, two feeds, declared only in the homepage head on a separate host),
miningweekly.com (South Africa), parkiet.com (Poland), startupslatam.com,
youngster.id (Indonesia).

**Refused with evidence, 5.** Each note carries the paths probed and the status
codes seen, so the next session does not repeat the work. Note the one that is a
DIFFERENT KIND of answer: renewable-carbon.eu returned 500 "Error establishing a
database connection" on every path INCLUDING the homepage. That is an outage
verdict, not a no-feed verdict, and the note says to recheck. commersant.ge and
sharesansar.com are marked NEEDS HTML PARSING (no feed exists, content does).
ctee.com.tw blocks a named AI-crawler roster. muscatdaily.com is WordPress with
feeds switched off.

**Security batch 1.68.1 is LIVE**, deployed after this went green so one clean
state shipped rather than a half one. Verified: live page reports ver=1.68.1 and
all eight new publishers render on the sources page.

**Still the top job, unchanged:** the published money total is inflated. The
landmark fix restores rounds lost to word-shaped dollars, but confirm it also
covers classification (a raise vs an investor fund close vs assets under
management vs an IPO) and the 43x lira mis-scale before trusting the headline.

## 2026-08-04: do NOT quote $493.3bn, and the badge that certifies itself

**Two funding totals were circulated in session that nobody could reproduce.**
A quarantine-drain agent reported the published total moving $214.9bn to a
projected $493.3bn after 8 wrong-class rows were retracted and 7 real rounds
released. A later agent tried to stamp those into the benchmark and could not
get either number out of the live API under ANY parameter combination. What the
API actually returns: **$457.1B** bare, $457.0B with `since=2025-01-01`, and
$442.9B on the 2026-YTD tile.

The retraction work itself is sound and independently evidenced, row by row.
What was never verified is the arithmetic quoted around it. So: **do not quote
$214.9bn or $493.3bn.** Before publishing any funding total, stamp the exact
query the publish run uses and read it from the live endpoint. The benchmark now
carries the measured $457.1B with its query recorded, plus an explicit UNKNOWN
block, which is the right shape.

This is worth remembering as a pattern rather than a one-off: the number was
produced by an agent, relayed by a second, and would have been published by a
third. Nobody lied. It simply never touched the live endpoint on the way through.
An agent's reported total is a claim, not a measurement.

**A badge that cannot fail.** `scratchpad/bm-live.html` on the talent side shows
an "auto-refresh OK" badge and carries `data-bench-*` hooks, but the file
contains NO JavaScript at all. Every number in it was hand-typed while the badge
asserted freshness. Same species as the archiver that never once completed, the
alert key the endpoint could never accept, and the review queue nobody drained:
a mechanism that reports health while doing nothing. Either wire the refresh the
badge claims, or delete the badge. Do not leave it self-certifying.

**Also fixed in that file, in case the reasoning is needed again:** the tech cell
divided a US-filtered numerator by global denominators and scored 73 and 53
percent. On one basis it is 131 and 94 percent by jobs. A second cell had the
same defect against a WARN-only floor, and behind it a third bug: the file used
`source=` where the API expects `sources=`, so the filter silently did nothing
and returned unfiltered data. A parameter that is ignored rather than rejected
is its own hazard, and worth checking elsewhere.

## Final state 2026-08-04: live 1.71.1, CI green

Verified against the live site and live API, not inferred from green runs.

**Shipped:** security hardening (JSON-LD XSS, SSRF class, hash-pinned deps);
13 publishers wired or refused with evidence; the funding quarantine drained,
8 wrong-class rows retracted and 7 real rounds released; the star made
self-explanatory with a confirmation that says it is saved in this browser only;
nine chart titles renamed to name their unit; the World tab badge reconciled;
Israel and Singapore registries added DORMANT; and the published-figure invariant.

**Registries, so nobody re-litigates them.** Israel: `data.gov.il` `ica-changes`,
CC-BY, four act codes that are the Israeli SH01 analogue, 343 rows / 311 companies
in a 14-day dry run. Its honest limit is on the sources page: the file carries no
amount, so a row means capital was raised, never how much. Singapore: ACRA,
incorporations only, monthly latency, open licence. **Canada is REFUSED with
evidence** and should not be re-opened without new facts: 642,984 active rows, no
industry column, no directors, no share data, no event stream, and the
per-corporation API is lookup-by-id only, so harvesting events means polling
642,984 ids. Federal CBCA only; the provinces are separate systems.

**Both are DORMANT.** Neither has a cron. All seven daily slots are taken; Israel
wants a weekly slot, Singapore a monthly one. That is a deliberate ship-dormant,
not an oversight.

**Do not quote $493.3bn or $214.9bn.** Still true, still unreproduced. The live
API returns $457.1B. Stamp the query the publish run uses and read it from the
endpoint before publishing any funding total. An agent's reported total is a
claim, not a measurement, and that number passed through three agents unverified.

**The verifier's own false positives are the lesson.** The new invariant flagged
two things that were NOT defects: the ribbon's 103 against an API 104 (the ribbon
counts notable rows; `?detail=notable` answers 103 exactly), and a news card
quoting a source's own words. Both were fixed IN THE CHECKER, not the page,
because a false positive is a wrong check: it trains the reader to dismiss the
alert. One was caused by a figure stamped with an EMPTY query, and the module's
own warning about that mistake sits two lines below the figure that made it.

**THE PATTERN, the most useful thing to carry forward.** About ten defects across
both trackers today were one species: a mechanism that reports health while doing
nothing. Here specifically: a benchmark badge reading "auto-refresh OK" in a file
containing no JavaScript at all; a test fixture answering 200 where ACRA answers
201, so 42 tests passed against a collector that dies on first real use; an
import-promise check walking the whole AST so it saw the very imports deferred
because of the promise it enforces, its allowlist quietly become an appeasement
list; and `host-watch` red 7 times in 57 runs with the host UP and answering in
under a second every time.

So the question to ask is not "what is broken" but **"what would never tell us if
it broke."** Run the thing live rather than trusting a fixture: the ACRA 201 was
found by calling the real endpoint, and no amount of green suite would have shown
it.

**Still open:** the money classification sweep over ALREADY-PUBLISHED rows (the
quarantine drain covered only rows above the ~$6.5bn outlier ceiling, and the four
error classes are not size-dependent, so the same mistakes almost certainly sit
below that line in volume). Owner-only: the ChangXin IPO retract, which needs a
credentialed `retract.py` and must NOT be done as a local-only retraction, since
that removes it from our copy while leaving it live and kills every surface that
would otherwise nag.

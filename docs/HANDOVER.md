# Handover — Talent Intelligence Tracker

**Read this first if you are a new session.** It is the current state of the
build, what is proven, what is broken, and what to do next. Keep it updated as
you go: it is the only thing that survives a crashed session.

Last updated: **2026-07-31**. Plugin **1.58.0**, **17,539** current signals
stored, company profiles shipped, cron firing on schedule but not reliably
green, **2,576 offline tests passing** plus five PHP render harnesses. Spain
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

**Chronological detail lives in [TECHLOG.md](TECHLOG.md)** — that file is what
happened and why; this one is current state and next actions. Both are for the
TALENT tracker only. The sibling AI Layoff Tracker has its own `docs/HANDOFF.md`
(a gated baton) and `docs/TECHLOG.md`; never cross-write them.

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

**The tripwire stays dormant, deliberately.** Arming is mechanically two
commented lines in `tripwire.yml`, but its own header states the gate: armed
only after a human has read a REAL run and agreed. No live query has ever
been issued (cost per query is still an estimate). The first live run is
`python run_tripwire.py --dry-run --countries IL --no-industries` — one
query, about two cents — and arming afterwards means uncommenting the two
schedule lines AND tightening `tripwire` to 336 in `staleness.py` in the same
commit.

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

### Cost and coverage (2026-07-30) — worldwide costs $75.99/month, and $25 is the allowance

**Run the program, do not trust this paragraph:**

```bash
python3 cost_projection.py          # live prices; --offline uses the snapshot
```

It reads the health ledger and OpenRouter's price list and prints what
worldwide coverage costs, labelling every number MEASURED (what the provider
charged), COUNTED (the funnel) or MODELLED (a price list times a token count).
It exits **2** when full coverage does not fit the allowance, which today it
does not.

**The headline: full coverage is $75.99/month against a $25 allowance.** Where
the money is, and the two surprises in it:

| stage | model | $/month at full coverage |
|---|---|---|
| gate | gemini-2.5-flash-lite | $4.15 |
| extraction | deepseek/deepseek-chat | **$31.69** |
| read-through | claude-sonnet-5 | $40.14 |

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
| 8 | **First live tripwire run + second recall measurement** | The tripwire has never issued a live query (cost is an estimate). The trend chart cannot draw until a second measurement exists. |

### Non-negotiable

- **Never store an aggregator as a source** (Crunchbase, Dealroom, Tracxn,
  Harmonic, StartupBlink, Startup Nation Central, TechIreland, Fundup) —
  discovery pointers only; cite the original publisher.
- **Never bypass a paywall. Never scrape LinkedIn** (`validate.py` blocks it).
- **Never write a row directly.** `extract → validate → store → publish`, and the
  raw dict **must** set `raw_text` or the extractor returns `None` silently.
- **An LLM claim is a lead, never a record.** The tripwire prefixes model-asserted
  fields with `claimed_`; the chase takes the employer name and nothing else.
- **No em-dashes in UI copy. No superlatives** on page, meta or structured data.
- **Cost ceiling $25/month** (`spend.MONTHLY_ALLOWANCE_USD`, raised from $10 on
  2026-07-30). It holds by rationing, not by luck: dedup before the LLM, gate on
  headline+teaser only, per-language prefilters, earned cadence, deterministic
  closes, and a per-run read cap sized to the MONTH rather than the run. Feeds
  are free; only stories cost. Full worldwide coverage would be $75.99/month, so
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
| `20 */3 * * *` | `archive-sources.yml`, `dry_run=false` | Wayback pass, eight times a day |
| `30 5 * * *` | `link-check.yml`, `dry_run=false` | daily rot sweep, before the 13:00 Monday digest |

**The cadence was retuned on 2026-07-30, and the arithmetic is the argument.**
The scheduled scope holds 656 distinct source URLs, 71 archived and 585 never
once answered about. A run resolves 15-30% of what it examines from the free
availability API and captures at most 40 more, roughly half of which land first
try, so a run is worth about twenty snapshots. Nightly, that backlog is three
and a half WEEKS; every three hours it is three days, after which each run finds
only what the last collect stored and exits in seconds.

Not hourly. The sibling tracker's own hourly archive sprint was audited and
REVERTED on 2026-07-30 after three consecutive runs were handed 0, 2 and 7
candidates: rate does not buy yield once the queue is short. And every run here
holds the `talent-collect` write lock for up to 25 minutes
(`DEFAULT_DEADLINE`), so hourly would spend half the day holding the lock away
from collection. The rot sweep went weekly to daily for a plainer reason: 150
URLs a week against 14,796 cited documents revisits a given link about twice a
decade, which is not a check.

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

Still unproven, and the reason it is dormant: nothing has run a LIVE query, so
the real cost per query is an estimate ($0.02, deliberately pessimistic) and the
lead quality of the actual model is unmeasured. First live run should be
`python run_tripwire.py --dry-run --countries IL --no-industries` — one query,
about two cents, against the country we know we are weak in and can check by eye.

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

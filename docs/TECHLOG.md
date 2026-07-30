# Tech Log — Talent Intelligence Tracker

Chronological record of what was built, why, what broke, and how it was fixed.
Newest first. **Keep this updated:** every deploy gets a line; every incident
gets an entry with root cause and the guard added, so the next session inherits
the reasoning and not just the diff.

This file is for the **Talent Intelligence Tracker only**. The sibling AI Layoff
Tracker has its own at `/Users/dakotta/Projects/atr-layoff-tracker/docs/TECHLOG.md`.
They share a WordPress install and nothing else — different repo, different
plugin constant (`TIT_VERSION` vs `ALT_VERSION`), different tables, different
REST namespace. Never write one repo's state into the other's docs.

---

## 2026-07-30 — the registry backfill: two of the four were already reachable, and India's ceiling is 32 days

Brief: build the 2026 historical backfill for the structured registry
collectors, on the premise that they all expose `as_classified`, so their spend
is $0 and back-filling them is the cheapest coverage win available. **The
premise is exactly right and the model spend for this session was $0.00.** What
the brief was wrong about is which of them needed anything built.

### What is actually held, measured first

`data/talent_intel.db`, 2026 rows by collector, current revisions only:

| collector | rows, all time | rows in 2026 | verdict |
|---|---|---|---|
| `sec_edgar` | 3,797 | **3,797**, every week of 2026-W01..W30 | **complete, no-op** |
| `sec_form_d_bulk` | 2,998 | 2,998, Jan..Jun | complete to the last published quarter |
| `uk_paygap` | 4,761 | 537 | **complete** — 2017..2025 run 403 to 595 a year |
| `sec_execcomp` | 3,910 | 133 | **complete for its shape.** `published_date` is the fiscal PERIOD END, so a CY2026 row needs a fiscal year that has ended in 2026. 2022..2025 hold 574 / 1,010 / 1,091 / 1,102 and 2026 fills as proxies land |
| `bse_india` | **0** | **0** | never run |
| `companies_house` | **0** | **0** | never run |
| `edinet_japan` | **0** | **0** | never run |
| `opendart_korea` | **0** | **0** | never run |

So "is 2026 already held" is **yes for all three SEC/UK sources and zero for
every registry collector**. The brief's guess that "some of this may be a no-op"
was right about which sources and right about why: the ~7,700 rows the dashboard
shows for 2026 are SEC plus the pay gap, and `backfill_sec_2026.py` already
walked them.

### Then: can each API even express a historical window? Two of four could

This is the question that decided what got built, and the answer is not the same
for any two of them.

| source | window it can express | reachable through `collect-structured.yml` today | built |
|---|---|---|---|
| `edinet_japan` | a LIST of calendar days; `MAX_DAYS` 366 | **yes.** `days=211` is one run of 211 calls at 0.5s — about two minutes | **nothing** |
| `companies_house` | `appointed_on` filter, any width, no state at all | partly: `days=211` + `ch_slice=0..3`, four dispatches, no cursor | walker |
| `bse_india` | **32 days.** Server-enforced, undocumented | **no** | walker |
| `opendart_korea` | 90 days, AND anchored on today | **no** — Jan..Apr unreachable | walker |

**`edinet_japan` needed nothing and gets nothing.** Its collector docstring
already says "a backfill widens the window; it does not become a script", its
own cap is a year, and one dispatch closes 2026:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
     -f inputs_json='{"source":"edinet_japan","days":"211","dry_run":"false"}' \
     -f reason='Japan 2026 catch-up'
```

A walker for that would be a second implementation of a cursor for 211 requests.
`test_edinet_is_absent_and_the_refusal_says_why` asserts the omission AND
asserts `edinet_japan.MAX_DAYS >= 366`, so if Japan's window ever shrinks the
omission stops being silently stale.

### THE FINDING: BSE refuses a window wider than 32 days, inside an HTTP 200

`collectors/bse_india.py` said a backfill is "a longer window through the same
path", and `collect-structured.yml`'s `days` input said "a gap is back-filled by
widening this". Measured live against `api.bseindia.com` on 2026-07-30, that is
**false above 32 days**:

```
strPrevDate=20260101, strToDate=20260131 (30d)  ->  200 {"Table": [50 rows]}
                                20260201 (31d)  ->  200 {"Table": [50 rows]}
                                20260202 (32d)  ->  200 {"Table": [50 rows]}
                                20260203 (33d)  ->  200 {"Status":"False",
                                                        "Message":"Date range
                                                         exceeded threshold."}
```

Binary-searched: 30/31/32 accepted; 33, 34, 35, 36, 40, 45, 90, 151 and 211 all
refused. The threshold is published nowhere. **The refusal is HTTP 200 with no
`Table` key**, so it landed in the collector's "the response shape has changed"
branch — a message that sends a reader looking for a redesigned API instead of
at a number in a workflow input. So India's history was not merely slow to
reach, it was unreachable through the documented route, and the error blamed the
wrong thing.

Three changes, all additive:

* `bse_india.WINDOW_CAP_DAYS = 32`, with the measurement beside it.
* `fetch_page` names the width refusal before the generic branch: *"BSE refused
  20260101..20260730 ... The undocumented ceiling ... is 32 days. This is a
  window that is too wide, not a changed API."*
* `days_from_env` refuses `TIT_BSE_DAYS > 32` rather than spending a run on a
  request that cannot succeed, and points at the walker.

Korea's ceiling is the quieter kind and was already documented: OpenDART limits
a `corp_code`-less search to three months and returns a **shorter window**
rather than an error, so a walker asking for 120 days would collect 90 and
record 120 as done. `window()` is also anchored on `datetime.now()`, which on
2026-07-30 put the earliest reachable day at 2026-05-01. January to April was
not a wide window away; it was unreachable. An explicit `--start` is the whole
fix.

### What was built

`backfill_structured_2026.py` + `.github/workflows/backfill-structured-2026.yml`.
One walker, three sources, `backfill_gdelt_2026.py`'s shape — monotonic
committed cursor, one slice a run, seen-URL skipping before any work, a `--plan`
summary, `--fetch-only`, `--dry-run`, and a `halt` path that records the slice
and declines to requeue into a wall.

**It is deliberately NOT a second priced walker.** GDELT walks news, so its
constraint is money and `--plan-cost` prices a pace. Every source here derives
its record from typed fields, so the constraints are the API ceiling and the
writer lock, and `--plan` prints **requests, wall clock and rate-limit
headroom** instead of dollars. There is no `--max-readthroughs`, no spend guard,
no gate — and `tests/test_backfill_structured.py` walks the module's AST to
assert `classify` is never imported, because a cap can be raised and an absent
import cannot.

Slice sizes, each derived from the API's own ceiling rather than picked:

| source | unit | slice | why that size |
|---|---|---|---|
| `bse_india` | days | **28** | four weeks, four days inside the measured 32-day ceiling, and it keeps the busiest sub-category at ~13 pages against the collector's `MAX_PAGES` of 40 — so a slice can neither be refused for width nor silently truncated for depth |
| `opendart_korea` | days | **60** | inside the documented 90, and ~56 list pages plus one `company.json` per filer |
| `companies_house` | **slices** | 1 of 8 | its cost is per COMPANY and nothing per day, so the ROSTER is what is walked |

**The roster cursor is a new unit in `backfill_slices.py`,** and it exists
because a date cursor for Companies House would be a lie: widening its window
from 42 days to 211 costs nothing (`appointed_on` is a filter over data the
endpoint returns anyway), while sweeping the 9,230-employer roster is 10,568
requests. So the job's `start`/`end` are slice indices `0..7` and the date
window rides on the job's committed `inputs`. `next_inputs` has an explicit
branch refusing to overwrite them — without it the next run would read a
one-day window and store nothing, silently, for seven of the eight slices.

**Eight backfill slices, not the rotation's four**, because the weekly job's
only work is the fetch while a backfill slice then puts ~590 rows through
validate/store/publish. `slice_of` is a blake2b digest, so any count partitions
the roster exactly once and the two do not have to agree; asserted over 4,000
numbers for both counts.

`backfill_slices.job_id` also gained an optional `label`. Three sources walking
the same 2026 window through one workflow would otherwise share one key and each
would resume where another stopped — a hole in one and a re-collection in the
other. It defaults to empty, so every cursor already committed keeps its id.

### Measured: two real slices, live, into a scratch database

`bse_india`, through the walker, 2026-07-30. Nothing was written to the
committed database at any point: `schema.DB_PATH` was pointed at a copy.

| slice | rows read | usable | stored | duplicate | wall |
|---|---|---|---|---|---|
| 2026-01-01..01-28 | — | **898** | **616** | 282 | **52s** |
| 2026-01-29..02-25 | 1,427 | **1,368** | **866** | 502 | **108s** |

The ~35% duplicate rate is `dedupe.fuzzy_duplicate` collapsing one employer's
leadership filings inside 14 days into one development, which is the intended
behaviour and the same factor `companies_house` was sized with. The chain was
driven end to end: slice 1 emitted a ticket with `next_cursor 2026-01-29`,
`record` advanced, slice 2 opened at exactly that day, `next_inputs` carried the
date window forward.

**A full 2026 walk, at a rate-limit-respecting pace** (`--plan`, which fetches
nothing):

| source | slices | req/slice | min/slice | rows/slice | rows total | req total |
|---|---|---|---|---|---|---|
| `bse_india` | 8 | 37 | **1.8** (measured) | 1,130 | ~9,000 fetched, ~6,000 stored | 296 |
| `companies_house` | 8 | 1,320 | 12.1 (paced) | 590 | ~4,700 | 10,560 |
| `opendart_korea` | 4 | 190 | 0.6 (paced) | 175 | ~700 | 760 |

So the whole 2026 registry catch-up is **20 queued runs, under two hours of
compute in total, ~11,600 requests and $0.00 of model spend**, for roughly
**11,000 rows** against a database that holds 15,711. India alone is more rows
than the tracker currently has from anywhere outside SEC and the UK pay gap.

Wall clock is printed **measured where a slice has actually been run and marked
`*` where it is arithmetic**, because the paced projection is only the time
spent waiting on the API: for `companies_house` that is almost the whole run,
for `bse_india` it is a twentieth of it (37 requests carrying 1,368 rows), and
projecting BSE from its pacing alone understates it by 20x. Two of the three are
unmeasured because `OPENDART_API_KEY_KR` and `COMPANIES_HOUSE_API_KEY_UK` are
GitHub secrets and are not set locally; every such figure says so in its own
`evidence` line, and a test fails if a projection is ever printed unmarked.

### Not armed, and the reason is different from the GDELT walker's

No cron, and `test_the_structured_walker_is_not_armed` refuses one. The reason
is written down because it is NOT the usual one: this walker is free, so a
reader looking for the cost argument will not find one and might conclude a cron
is harmless. It is not. Every source here writes the database and therefore
holds the single `talent-collect` lock, in which GitHub keeps exactly one
pending run, so a scheduled run enters that group uncoordinated and either
evicts the waiting run or becomes an unreplayable orphan.

The queue is currently blocked (`WRITER_QUEUE_TOKEN` unset, so a dispatch
produces no run and the ticket requeues), which makes **a slice being re-run the
ordinary case rather than the exception**. That is why the seen-URL skip is
before everything else and is measured: a repeated `bse_india` slice costs one
fetch and stores nothing, asserted by
`test_a_slice_stores_and_the_second_run_of_it_stores_nothing`. `companies_house`
is exempt from the skip and must be — its `source_url` is one PERSON's
appointments page and a person can be appointed twice, so skipping it on sight
would make the first appointment the last one that source ever reported. The
flag is read off `companies_house.REVISITS_ITS_SOURCE_URL` rather than restated.

### Figures round-trip, proved through the walker and not at the regex

Four silent data-loss bugs in three days came from the verbatim-figure guard
meeting non-Latin scripts and typographic separators, so both non-Latin sources
here are driven end to end rather than unit-tested:

* **India**: a filed description ending in `28.07.2026` at a company whose name
  begins with K — the exact newline-spanning `\s*` collision that read
  `28.07.2026\n\nK` as `28072026k` — stores, and
  `validate.assert_figures_are_sourced` agrees on the stored strings.
* **Korea**: full-width digits in the filer's Korean and English names survive
  the whole path, fold to ASCII, and the summary's figures are all present in
  `raw_text`. A companion test asserts that **NFKC is still not used**, because
  it rewrites the U+318D in `독립이사의선임ㆍ해임또는중도퇴임에관한신고` to
  U+119E and the report-name allowlist stops matching — the obvious blanket fix
  that would break the source.

### `staleness.py`: nothing changed, and that is the decision

The walker deliberately writes **no `source_health` row**, asserted by
`test_the_walker_writes_no_health_row`. Each of these collectors is leashed to
its WEEKLY cron (180h). If a backfill reported health it would reset that leash,
and a broken weekly run would be masked by a backfill that happened to succeed —
the leash measures whether the COLLECTOR ran, and a backfill is not that. The
backfill's own failure is a red run.

### Numbers

- Suite **2,044 -> 2,082, +38**, measured by running HEAD and the staged tree
  side by side rather than by counting the diff. 36 are written here — 30 in
  `tests/test_backfill_structured.py` and 6 in `tests/test_backfill_pace.py`
  (the not-armed test, the roster cursor's per-run property, a whole roster
  walk, the three-cursor property, the backward-compatible job id, and one more
  parametrized workflow) — and 2 are `tests/test_workflows.py` parametrizing
  over the new workflow file by itself.
- **$0.00** model spend, in the walker and in measuring it.
- 2 live slices, 898 + 1,368 rows fetched, 616 + 866 stored, 160s total.
- 1 undocumented API ceiling found, binary-searched and named in code.
- 0 rows written to `data/talent_intel.db`.

### What was refused

* **A walker for `edinet_japan`.** It is one dispatch of an existing workflow.
* **Arming anything.** No cron was added anywhere.
* **Re-fetching SEC or the UK pay gap.** 2026 is complete for all three; the
  counts are in the first table rather than an assurance.
* **A second cursor implementation.** `backfill_slices` gained a unit and an
  optional label, both additive and both defaulted so every committed cursor
  still resolves.
* **`run_collect.py` and `source_registry.py`.** Untouched — other lanes.

## 2026-07-30 — the page is dated now, the font question is answered with numbers, and the press page's links are checked against the code that reads them

Plugin **1.54.0 -> 1.55.0**. Second design pass, taking the four items the
first pass explicitly HELD. Every figure below is measured.

**NOT DEPLOYED.** The brief asked for a deploy and a live check, and also said
do not push. `deploy-plugin.yml` uploads from a checked-out git ref, so shipping
this needs the branch pushed first. The prohibition won. What was verified
instead is in the "Measured" table below, all of it against the real render in a
real browser rather than against the source. The live page was left on 1.54.0
and confirmed unharmed (HTTP 200, TTFB 2.72s, `dashboard.css?ver=1.54.0`).

### Measured, before -> after

| | before | after |
|---|---|---|
| cold render queries | 12 | **12** (constant untouched) |
| warm render queries | 0 | **0** |
| N+1 tripwire (+5,000 rows) | same count | **same count** |
| markup bytes (synthetic corpus, fixture prefixes excluded) | 153,670 | **166,802** |
| body sideways scroll at 390px | none | **none** (`scrollWidth` 390 = `innerWidth` 390) |
| elements overflowing the viewport at 390px | 0 | **0** |
| containers needing a horizontal gesture at 390px | 0 | **0** |
| offline tests | 2,040 | **2,044** |
| PHP harnesses | 5 pass | **6 pass** (`render_press.php` is new) |
| press page cold / warm queries | n/a | **5 / 0** |
| webfont bytes added | 0 | **0** |

`ops_status.py` exits 2 both before and after, and not because of anything here:
five collectors are stale on wall-clock time. It reads neither
`wordpress-plugin/` nor `tests/php/` (grep: zero references), so nothing in this
pass can move it.

### 1. The dated glance panel, and the four buckets that cost nothing

The hero opened with one undated lump — "12,566 updates · 5,542 employers ·
51 countries · $101B raised · 7,573 from official filings" — which answers "how
big is this dataset" in the position where a reader is asking "what has moved".
Every figure in it is as true in March as today, so nothing on the first screen
said whether the thing was still running.

It is now a ladder: **Today / This week / This month / 2026 so far**, each with
updates, employers, dollars raised, updates from official filings, and the
largest single raise named. The old line survives as the bottom rung, labelled
**Everything We Hold**, because it answers a real question and the meta
description is built from the same three figures.

- **Translated, not ported.** The sibling's row reads "1,864 workers · 3
  verified layoffs · largest: Damen Mangalia (1,000)". Layoffs are not collected
  here, so "workers" and "layoffs" have no meaning on this page. The equivalents
  are what this tracker holds.
- **Zero extra queries, and that is a correctness decision first.** The panel
  rides on `tit_glance_matrix()`'s existing single scan. The two describe the
  same windows over the same rows, so computing them separately could have put
  "this week, 1,204 updates" above a matrix cell reading 1,198 — invisible until
  a reader adds them up. Sharing one statement makes disagreement impossible
  rather than unlikely, and it is why the budget is still 12. Verified on screen:
  the panel's "This week 638" and the matrix's "Everything in This View / This
  week 638" are the same number because they are the same expression.
- **Largest raise: two scalar subqueries per bucket, not an argmax.** An
  aggregate returns the largest AMOUNT; it cannot return who raised it, and SQL
  has no portable argmax. The tricks that fake one are engine-specific — SQLite
  defines bare columns beside `MAX()`, MySQL does not; the string-packing form
  needs a different concat operator in each — and the harness is SQLite while
  production is MySQL, so anything that differs between them is a bug that ships
  green. Scalar subqueries are standard in both and stay inside one statement,
  the same shape the top-cities strip already uses. `row_id ASC` breaks ties, or
  two equal rounds resolve to whichever row the engine reached first, which is
  the defect the city flags had.
- **Today is computed and usually absent.** This repo already measured that
  "today" reads zero for most of most days (source dates, not capture dates;
  collection twice daily) and removed the column from the matrix for exactly
  that reason. Reintroducing it as a permanent zero would repeat a mistake that
  is written down. It is computed every render and the row is printed only when
  it holds something.

**The week-over-week comparison is suppressed, and the rule is about history
rather than size.** The sibling can say "down 25% vs the week before" because it
holds years. Here the news collectors first ran 2026-07-27 and `national_press`
on 07-29, so the prior week is not a quiet week, it is a week that mostly
predates the collector; dividing by it prints something like "up 4,000%", which
would be the most quotable number on the page and is an artefact of the corpus
start date. The comparison prints only when the view holds data from on or
before the start of the period being compared against, measured **per view** so
it also holds under a filter that narrows to a young collector. When it is
absent the panel says why in a few words, because a reader who sees nothing
cannot tell "flat" from "we cannot say yet".

`render_dashboard.php` pins **both directions**, which matters: a rule that only
ever suppresses is indistinguishable from a feature that never worked. The
40-day fixture prints the comparison; the same corpus with everything older than
9 days deleted must not, must emit no percentage of any kind, and must state the
reason.

**Copy as Post is honest or it is not shipped.** The sibling's version is scoped
only by its region tab and ignores the rest of its filter bar, so a reader
looking at one country can copy a worldwide total. This one reads the RENDERED
rows out of the DOM at click time and appends the active filters read from the
chips bar the page already maintains, and says "unfiltered" explicitly when
there are none. The panel repaints from `/aggregate` under those filters, so the
two halves cannot drift. The button is rendered `hidden` and revealed by script,
because its whole function is `navigator.clipboard` and a control that visibly
does nothing is worse than an absent one.

### 2. The font decision, settled with a measurement

Held last pass because the mock loads three Google webfonts on a page with a
2.5-4.0s cold TTFB against a deliberate no-CDN rule, and substituting by guess
was refused. Measured 2026-07-30 rather than argued:

| | bytes |
|---|---|
| stylesheet, fonts.googleapis.com | 17,959 |
| Source Serif 4, latin woff2 | 122,168 |
| IBM Plex Mono, latin woff2 (three static faces) | 30,232 |
| Public Sans, latin woff2 | 26,636 |
| **total added to first paint** | **~196,995** |

Against a live origin TTFB measured the same day at **2.72s** and a whole-markup
budget of 156KB. The fonts weigh more than the page they set, and Source Serif
alone is 68% of it for headings only. They also arrive on a **serialised
two-origin path**: the gstatic requests cannot start until the googleapis
stylesheet has been fetched and parsed, so it is DNS + TCP + TLS to one new host
and then to a second before a glyph is asked for, which a byte count does not
show.

And the site runs **Complianz** (`cmplz-manage-consent` is in the live markup,
confirmed by curl). Google Fonts is a named blockable third-party service in a
consent layer, so the design's character would reach some readers and not
others, decided by a cookie banner. Turning that off is a privacy decision that
belongs to the owner.

**Decision: no webfonts. Ship the mock's typographic STRUCTURE at zero bytes** —
a serif for display, a grotesque for body, a mono for labels and figures. That
contrast is what carries the character. What it does not get is Source Serif's
personality at 54px, which is a real loss and is stated rather than papered
over. Worth writing down: **the body face needed no change at all.** The stack
was already `system-ui, -apple-system, "Segoe UI", Roboto`, and Public Sans is a
neo-grotesque close enough to system-ui on both platforms that the two are hard
to tell apart.

**Self-hosting from the plugin is the right long-term answer and is NOT done
here.** It removes the CDN objection, both extra origins and the consent problem
outright, and the one thing that would have made it unsafe is already handled:
this plugin's assets are excluded from Autoptimize's CSS aggregation, so
relative `url()` paths in an `@font-face` resolve rather than break. What it
needs is 179KB of third-party font binaries plus their OFL licence downloaded
and committed into a public repository that deploys to production, which is the
owner's call and not an agent's. **The five latin woff2 files, so it is a
one-line yes:**

```
122,168  fonts.gstatic.com/s/sourceserif4/v14/vEFI2_tTDB4M7-auWDN0ahZJW1gb8te1Xb7G.woff2
 26,636  fonts.gstatic.com/s/publicsans/v21/ijwRs572Xtc6ZYQws9YVwnNGfJ7QwOk1.woff2
 10,052  fonts.gstatic.com/s/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q131nj-o.woff2       (400)
 10,060  fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgsAXHNk.woff2   (500)
 10,120  fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3vAOwlBFgsAXHNk.woff2   (600)
```

Subsetting to the glyphs this product actually uses would cut Source Serif hard,
since 122KB is a variable font carrying the full 8..60 optical-size axis and
200..900 weight range for a page that needs three weights.

### 3. "Why you can trust this", with the FAQ as its second tab

Did not exist anywhere: not in this repo, not in the sibling, not live. Built
from the mock now that the mock is on disk.

**Two fixes to the mock.** Its four numbered items sit in
`repeat(auto-fit, minmax(210px, 1fr))`, which resolves to three columns at most
desktop widths and strands the fourth alone on a second row. Explicit counts
instead — 1 / 2 / 4, all divisors of four — so there is no width at which one
item sits by itself. Verified in a browser: 4-across at 1280px, 2+2 at 900px,
stacked at 390px. And the mock has no FAQ; there was none anywhere in this
product to move, checked before writing, because two FAQs that drift apart is
worse than one.

**Every panel is in the initial HTML and nothing is fetched.** A tab that loads
on click hides its content from a crawler, and an FAQ is among the most
SEO-valuable blocks on a page. Both panels are rendered server-side in full;
JavaScript's entire job is to add `is-tabbed`, and the stylesheet does the hiding
only once that class is present. Verified in a real DOM with the script removed:
both panels `display:block` with 909 and 2,762 characters of text, all eight
questions visible, both panel headings visible, the tab strip `display:none` and
the copy button hidden — so nobody is offered a control that cannot work. With
the script, arrow keys move selection and focus, roving tabindex makes the strip
one stop, and `aria-selected` follows.

**Every number in the copy is computed**, checked by recomputing it from the
database in the harness. corrections.php here once shipped a typed "$124.0bn"
captioned "Measured now" against a live figure of $101B, and the sibling's press
page still carries a hardcoded "51 ... we currently carry every one of them"
with no query behind it. A panel whose subject is trustworthiness is the last
place on the site that can afford either.

FAQPage structured data is emitted, and it is the one line worth arguing about:
3,450 bytes duplicating visible prose. It earns them only because the answers
ARE visible — company.php and places.php both record that the sibling earned a
manual-action risk emitting identical FAQPage markup across ~1,830 URLs where
the answers appeared nowhere in the document. The harness asserts the two
together: every question the schema names must also be rendered as text, so if a
future session moves an answer behind a fetch the schema fails with it.

### 4. The press page, and a test that closes the sibling's silent-link bug

`/talent-intelligence-tracker/press/`. The owner assumed one existed. Sections:
numbers you can use right now (four windows, each with a preset view), context
for this year, the archive, **what this tracker does not do**, how to cite, press
contact. The sibling's page was read for shape only; nothing imported, nothing
copied.

**The archive is a live query and not a snapshot, deliberately.** Corrections
here append a revision rather than overwriting, so the current rows are what we
now believe; a frozen copy would preserve a figure we have since corrected and
present it as though it still stood. What makes an older number answerable is
the corrections log, and the page says so. Months with nothing in them are
skipped rather than rendered as zeroes, because every month before July 2026
would read as "nothing happened" rather than "we were not there".

**The link check is the point of the whole file.** The sibling shipped press-page
evidence links built on `ai_primary=1` — a parameter its REST API accepts and its
dashboard JavaScript ignores — so every "see the rows behind this number" link
advertised a filtered view and served the entire corpus, silently, in a way no
reader could detect. Its own ARCHITECTURE.md now cites it as the canonical
example: a bad parameter NAME over-reports, a bad VALUE under-reports, neither
raises.

A hand-maintained whitelist does not fix that, because the defect IS the
whitelist drifting from the front end. So `render_press.php` **parses the
`inputs` map out of `assets/dashboard.js`** and requires every parameter this
page emits to be in it, plus proves by string match that `applyUrlState()` still
reads `funding` and `stated_headcount` by name. Values are checked too: a
`country` must be an ISO code this product recognises, a `since`/`until` must be
a date the control accepts. Proved to work by temporarily emitting
`ai_primary=1` — the harness failed with the exact sentence describing the
sibling's bug — then reverted.

Also asserted: no superlatives (eight phrases), no em-dashes, Title Case
headings, no withdrawn record reaching any figure, the year label derived from
the clock, and a 5-query cold / 0-query warm budget so a per-row lookup inside
the archive loop fails here rather than under a crawl.

### Where the brief proved wrong about the code

- **"Query budget: `TIT_DASH_QUERY_BUDGET` is 12 cold / 0 warm ... Do not raise
  the constant to pass."** Correct, and the panel cost zero — but the reason it
  could is that the buckets it wanted were **not** the matrix's. The matrix runs
  week / month / quarter / YTD and the panel needed today / week / month / year.
  Three boundaries are shared and two are not, so this is a genuine extension of
  the scan rather than a re-use of existing columns.
- **The brief's model includes a "Today" row unconditionally.** This repo's own
  TECHLOG had already measured Today as structurally near-empty and removed it
  from the matrix. Shipping it as a permanent zero would have re-introduced a
  documented mistake; it self-suppresses instead.
- **"Self-hosting subset woff2 from the plugin is probably the right answer."**
  Right about the destination and wrong about who can take the step: it means
  downloading third-party binaries into a public repo that deploys to
  production. The Autoptimize question the brief asked about turned out already
  answered — our assets are excluded from CSS aggregation, so relative
  `@font-face` URLs would resolve. The blocker the brief did not anticipate is
  **Complianz**, which is installed and would gate a CDN font behind consent.
- **"Check whether FAQ content already exists somewhere before writing new."**
  Checked; none exists. The only FAQ-shaped thing in the codebase is the warning
  in company.php and places.php about the sibling's FAQPage manual-action risk,
  which shaped the design rather than supplying content.
- **A CSS miss worth recording.** `.tit-wrap .tit-press h2` matched nothing: the
  press page's root carries **both** classes, so it needed `.tit-wrap.tit-press`.
  The selector read as correct and the headings quietly kept the body stack. Only
  caught by reading `getComputedStyle().fontFamily` in a browser, which is the
  same lesson as gotcha 11 at a smaller scale.

---

## 2026-07-29 — four coverage levers at $0 and one priced walker, and three of the four briefs were wrong about the code

Five items, briefed as "close the coverage gap as cheaply as possible". Four had
to cost nothing in model spend and the fifth had to be paced rather than funded.
All five landed. **Model spend this session: $0.00.** No model call was made by
any code written here and none was made while measuring it.

Tests **1,996 -> 2,040** (+44, four new files). `ops_status.py` exits 2 before and
after with the *identical* five items — five collectors reading stale against a
checkout six commits behind origin. Verified by running `git show
HEAD:ops_status.py` against the same database: same exit, same list. Nothing
written here adds a problem.

**Three of the five briefs described code that is not there.** Each is recorded
below beside what is, because the wrong belief is the reusable part.

### 1. The archive queue: the sibling's bug is absent, and the mirror of it was not

**Brief:** 3,965 URLs sit `pending` on the sibling and never re-enter its
candidate list; this repo has the mirror problem, and records already pushed to a
terminal state by a blinded 429 probe need resetting. Count them.

**Count: ZERO, and neither premise held.**

| measured, `data/talent_intel.db`, 263 ledger rows | |
|---|---|
| `archive_state = 'unavailable'` | **0** |
| max `archive_attempts` on any row | **1** (of `MAX_ARCHIVE_ATTEMPTS` = 5) |
| archived / pending / no archive row yet | 72 / 69 / 122 |
| coverage | 72 of 12,970 distinct source URLs (0.6%) |

Nothing has ever reached the terminal state, so there was nothing to reset.
`archive_sources.py --recheck-terminal` says so and exits 0; it is kept because
both routes into that state shipped as green runs and a third would need it
again.

And **`pending` already re-entered the candidate list.**
`source_links.archive_candidates` excluded only `archived` and `unavailable`. The
sibling's defect is not in this function and never was.

**What WAS real, and it is the same bug in the second of the two places it can
happen.** The availability-API 429 was fixed on 2026-07-30. Save Page Now's 429
was not: `archive_attempts` was incremented unconditionally after a capture
attempt, so a *refused* capture spent one of the five. Five throttled nights —
which for an anonymous Save Page Now caller is an ordinary fortnight, not an
outlier — would have retired a perfectly capturable document to the terminal
state having never once been told it was uncapturable, out of five green runs.
`archive_candidates` drops it forever and only a hand-written UPDATE brings it
back.

**Second real defect: `pending` was re-examined but could not be REACHED.** The
candidate list was a strict newest-capture-first head slice under `limit`. At
12,970 distinct URLs and a 600-URL window, a URL nobody has ever had an answer
about sinks further every time a collect run stores something newer. That is the
sibling's outcome by a slower route, and it is invisible because the percentage
still climbs.

Both fixed structurally rather than by patching the symptom:

* **Terminal requires EVIDENCE.** `classify_archive_outcome` now takes `probes`
  and will not record `unavailable` until archive.org has answered at least once
  and said it holds nothing (`MIN_PROBES_BEFORE_TERMINAL`). A throttle can no
  longer retire a document, by construction, whatever the next caller does.
* **Blind rounds are counted apart from attempts.** Three new columns
  (`archive_probes`, `archive_blind_rounds`, `archive_detail`), appended to
  `MIGRATIONS`. NULL reads as "never probed", which is the honest reading of every
  row written before they existed.
* **The gap is reported SPLIT.** `source_links.archive_gap()` and
  `ops_status [2c]`: **12,898 never answered about, 0 confirmed absent from
  Wayback.** A percentage climbing slowly because Save Page Now is rate-limited
  (the design) and one climbing slowly because nothing can get an answer (a
  fault) are indistinguishable until those two numbers are printed apart. Today
  every un-archived URL is in the first bucket, which is a statement about what we
  know rather than about Wayback.
* **Two tiers in the candidate order**: never-probed first, then probed-and-absent.
  Every brand-new URL has zero probes, so the ingest-time property the module
  docstring defends is preserved exactly — within tier 1 the order is still
  newest-first. What changes is that the never-answered tail rides *with* the new
  rows instead of behind every one of them.
* **Real pacing.** Consecutive non-answers back the availability gap off
  geometrically to 30s, one answer resets it, and 12 unbroken non-answers end the
  free pass with the remainder unexamined and a `::warning::`. The old behaviour
  walked all 600 candidates at 2/s learning nothing and spent the deadline
  proving archive.org was still refusing.
* `ops_status [2c]` goes RED on any terminal-while-blind row and names the repair
  command. It must always be zero.

Cost: **$0**. No model is called by `archive_sources.py` or `link_check.py`, ever.

One existing assertion changed and it is worth naming.
`test_an_unanswered_url_never_spends_a_capture_or_an_attempt` asserted
`COUNT(*) == 0` on the ledger after a blind round. That proxy stopped being the
property: a blind round is now written down, because "nothing has answered about
this URL for six nights" is otherwise unknowable. The test now asserts the
substance — state `pending`, attempts 0, probes 0, blind_rounds 1 — and says why
the proxy was replaced.

### 2. Ranking the read budget: measured on a real candidate set, and it moves

**The brief's figure was stale.** `READTHROUGH_CAP` is already 200; the
95-deferral measurement was taken at 60, before the owner's 2026-07-30 raise.
The lever is still real, because a full `national_press` sweep produces ~1,018
gate survivors and 200 binds hard on that.

`pipeline/candidate_rank.py`. Ranks `kept` immediately before the classify loop,
which is where `BudgetDeferred` is thrown. Four free signals: country need (from
our own `signals.country` GROUP BY, not from a stale worklist file), employer
novelty, keyword force (reusing `cheap_extract`'s own reading), source tier.

**The property that makes it safe is that it is a permutation.** `rank()` returns
the same objects, asserted by identity rather than equality, so nothing was
rebuilt, normalised or quietly edited on the way through. It cannot reject,
filter or promote; `precheck`, the gate, `validate` and `store` are untouched and
unaware of it. A deferred candidate is still left unmarked and still returns next
run, so the ordering decides *when* a story is read and never *whether*.

**Measured, live, on a real candidate set** — 90 catalogue feeds one per country
in turn, 1,514 items, 162 past the free prefilter, which is exactly the population
a run hands the gate. `python3 -m analysis.ranking.measure --live --feeds 90`:

| cap 60 | US/GB | countries reached | from countries holding ZERO rows | no country hint |
|---|---|---|---|---|
| arrival order | 0 | 20 | **19** | 4 |
| ranked | 0 | **29** | **60** | 0 |

**3.2x the zero-row candidates read, +45% country breadth, at identical spend.**
At cap 200 the 162-candidate sample does not bind and the two orders are
identical — correct, and the honest shape of the result: ordering only matters
when the cap binds.

On the 226 stored news rows (`--stored`), at cap 60: 2 countries -> 23.

Three limits printed with the result rather than left to be discovered:

* **No real candidate set was ever captured, so none can be replayed.**
  `raw_text` is not persisted and a rejected candidate leaves a bare URL in
  `seen_urls` with no text and no reason — the same wall the rejection audit hit,
  and it printed a zero rather than an estimate for the same reason.
* The stored population is rows that *stored*, so the "holds zero" signal is
  circular on it by construction. That column is omitted there, not fudged.
* The live sample was breadth-first, one feed per country, which **flatters**
  arrival order — a real run reads 43 US feeds among 575. The true effect is
  likely larger, not smaller.

Cost: **$0**. One GROUP BY, one DISTINCT scan, and regexes already compiled. A
ranking signal that needed a fetch would cost more than the read it was trying to
prioritise.

### 3. MARKETS: 15 not 14, Korea already in it, and it drives neither of the two things it is believed to

**Brief:** MARKETS has 14 entries; Korea is in the Google News rotation without
being in MARKETS; more editions cost gate time; more candidates into a saturated
read cap produce more deferrals.

Actual: **15 entries, and KR was added on 2026-07-29** with the OpenDART work.
And the caution does not apply, because of what MARKETS actually controls —
traced through the code rather than assumed:

* It does **NOT** drive the Google News locale rotation. `GOOGLE_NEWS_LOCALES` is
  an independent tuple and `build_locales` reads only it. Every country added
  below has been swept twice a day for days while the coverage manifest said
  nothing about it — the same gap Korea had.
* It does **NOT** widen the prefilter's geography gate. The comment above
  `_geography_terms` claimed it "grows automatically as source_registry.MARKETS
  grows"; the function reads `vocab.COUNTRY_NAMES`, `vocab._CITY_ALIASES`,
  `vocab._COUNTRY_ALIASES` and a hardcoded short-code list, and has never
  referenced MARKETS. **Corrected in place**, because that belief is exactly what
  would make someone add a market expecting its stories to start surviving the
  free filter.
* `build_segments()` **does** read it, and `build_queries()` puts the result in
  the query list for every source that is not gdelt, google_news or
  tripwire_chase — which is every structured source, and **every one of them
  accepts `queries` and ignores it** (`national_press` says so in its docstring;
  the SEC pair search by form and item; a derived source has no search vocabulary
  at all). So a segment added here reaches no fetch today.

**Therefore expanding MARKETS costs $0 AND adds zero candidates AND zero gate
time.** It is a correction to a public claim, not a widening of collection. The
brief's caution (a) is true of widening `GOOGLE_NEWS_LOCALES`, which is a
different edit and was not made.

**The binding constraint is the segment sweep budget, and it is 56.**
`test_the_segment_matrix_still_sweeps_inside_the_recency_window` requires
`ceil(segments / 4 / 2) <= 7`. The 15 existing markets spend 44 (name + one per
`terms` entry). Twelve name-only markets spend the remaining twelve exactly.
**That is why none of the twelve carries `terms`** — one three-phrase pack costs
four slots and buys one market instead of four.

Added, **MARKETS 15 -> 27**: BR, ES, IT, MX, AR, CO, PT, CH, SE, AE, ZA, NZ.
Every gold-set zero-country that already has a swept Google News edition and at
least two wired publisher feeds. Both conditions were load-bearing:

* **No edition** -> a `discovery_only` market cannot honestly claim
  `live_sources=("google_news",)`, and adding an edition means adding a
  live-verified LANGUAGE PACK, not a translation. That excludes **CN** (7 feeds,
  no `zh` pack), **NO** (5, no pack) and **FI** (4, no pack).
* **One wired feed** is the single point of failure the catalogue refuses
  elsewhere. That excludes **SA**; its ar:SA edition keeps sweeping, simply
  unclaimed.

`tests/test_market_claims.py` pins all of it, including a test that fails if a
zero-scoring country with an edition and feeds is left unclaimed without being
named in `BUDGET_DEFERRED` with a reason. That dict is empty today: the twelve
spent the budget exactly, and every remaining zero-country is excluded for one of
the two reasons above.

### 4. The historical walker already existed. What did not exist was a price on it

**Brief:** build a cursor-based walker equivalent to the sibling's; read the
sibling read-only for the pattern.

**It has been here since 2026-07-29.** `backfill_gdelt_2026.py` +
`backfill_slices.py`: monotonic cursor committed to `data/backfill_state.json`,
one slice per run, server-side windows (GDELT DOC 2.0 takes explicit
`startdatetime`/`enddatetime`; Google News RSS has no archive, which is why GDELT
is the route), seen-URL skipping before any spend, `--fetch-only` for a free
rehearsal, `MAX_SLICES_PER_JOB`, and a `halt` path that records the slice and
declines to requeue into a wall. **The sibling was not read: there was nothing to
pattern-match, the pattern was already here.**

**The sibling's date-ordinal trap is structurally absent.** `record()` moves the
cursor from the ticket the run emitted and reads no clock, so two runs in one hour
advance twice and a run that finished nothing advances not at all — which it
catches, marks `stalled`, and refuses to requeue.

**It has never run.** `data/backfill_state.json` holds one job and it is
`backfill-funding-bulk`.

**What was NOT cheap by construction was the read ceiling — and the number that
actually applied was in the workflow, not the script.** Script default 1200; the
`max_readthroughs` workflow input default **also '1200'**, which is what a
dispatch uses. At the measured $0.00128 a read that is ~$1.54 a slice, and a year
of 2026 history is 92 slices: **the input default alone authorised ~$142 against a
~$5/month product budget.** A ceiling only `spend.py` can stop is not a ceiling,
it is a plan to be interrupted.

Now derived rather than typed:

```
MONTHLY_WALKER_BUDGET_USD = 1.50
USD_PER_READ_ALL_IN       = 0.00128 + 4 x 0.00003   # the read AND the gates that found it
DEFAULT_MAX_READTHROUGHS  = 1.50 / 30 / 0.0014 = 35
```

Deriving it from the read price alone overshot by 9% — small, and exactly the
arithmetic that makes a stated ceiling quietly untrue. The workflow default is now
blank, meaning "use the derived value", so the budget and the ceiling cannot
disagree.

`python3 backfill_gdelt_2026.py --plan-cost` (fetches nothing, calls nothing):

| pace | wall clock | $/month | $ total |
|---|---|---|---|
| 1 slice/day | 92 days | **1.47** | 4.51 |
| 2 slices/day | 46 days | 2.94 | 4.51 |
| 4 slices/day | 23 days | 5.88 | 4.51 |

**A year of 2026 history costs $4.51 at any pace.** The pace only decides how long
it takes and how much lands inside one month — and 4/day exceeds the whole product
budget on its own. **Not armed**: there is no cron, and arming one is the owner's
spend decision. `ops_status [2e]` now says so with the queue command beside it,
because the walker addresses **51 of the 81 recall misses** (`outside_our_history`
— the news collectors first ran 2026-07-27 and `national_press` on 2026-07-29,
against a gold window of 2026-07-01..28; the 9% is a two-day-old tracker measured
against a four-week window).

`tests/test_backfill_pace.py` asserts **the property and not the symptom**: two
`record` calls at the identical clock second advance the cursor twice; the cursor
is monotonic across a 30-slice chain; a budget stop resumes on the first window it
did not do; a stalled job yields no inputs; no sliced backfill workflow may carry
a cron faster than daily (with a cron-expression parser tested against the shapes
that actually appear here, including the sibling's `0 * * * *`); and the walker
carries no cron at all.

**Not measured, and it does not change the projection:** candidate volume per
day-window. Two `--fetch-only` probes were started and neither finished — GDELT
paces at 12s a query and 9 queries a window — and the session ended before they
did. It is not load-bearing: the gate term is a fortieth of the read term, so the
slice cost is a read-count projection with rounding, and the read ceiling is what
binds. Anyone wanting the number can have it for free:
`python3 backfill_gdelt_2026.py --start 2026-03-10 --end 2026-03-10 --fetch-only`.

### What was refused

* **Rebuilding the walker.** It exists; rebuilding it would have been a second
  implementation of a cursor, which is how two sources of truth start.
* **Arming any cron.** None was added. The walker, the tripwire and the plugin
  deploy all stay as they were.
* **`spend.py`.** Untouched. The $10 monthly allowance and the OpenRouter key cap
  are the enforcement; everything above is sizing.
* **Raising `SEGMENTS_PER_RUN`** to fit a thirteenth market. It would have relaxed
  a guard that exists because queries once asked `when:3d` while the matrix took
  6.2 days, and it would have bought a market by weakening the thing that keeps
  markets honest.
* **Mapping the catalogue's `source_type` column into a ranking signal.** The
  recall worklist's under-delivering types are `trade_press` (4% held),
  `press_release` (16%), `national_news` (0%), `filing` (40%); the catalogue's
  column is 66 freeform values from "News Organization" (888 rows) to "Patent
  Office". Mapping one onto the other invents a vocabulary to rank by, and a wrong
  mapping would be invisible — it would simply rank the wrong things first.
* **A registry connector**, `collectors/companies_house.py`,
  `data/sources_catalogue.csv` (read only) and everything under
  `wordpress-plugin/`. Other lanes.

---

## 2026-07-29 — the filter panel is a column of scrolling checkboxes, and the page has one vocabulary

Plugin **1.53.0 -> 1.54.0**. Owner-driven pass on the dashboard. Everything
below is measured; the numbers are from `data/talent_intel.db` at 15,711 current
signals and from `tests/php/render_dashboard.php` before and after.

### Measurements, before -> after

| | before | after |
|---|---|---|
| cold render queries | 12 | **12** (budget unchanged, constant untouched) |
| warm render queries | 0 | **0** |
| markup bytes (synthetic corpus, fixture prefixes excluded) | 151,801 | **153,670** |
| body sideways scroll at 390px | none | **none** (`scrollWidth` 390 = `innerWidth` 390) |
| containers needing a horizontal gesture at 390px | **3** (matrix, country strip, city strip) | **0** |
| offline tests | 1,924 | **2,006** |
| PHP harnesses | 5 pass | 5 pass |

Verified in a real DOM at 390x844 and 1280x860, not by reading the CSS:
`position: sticky` computed on `#tit-panel` and held at `top: 16px` after a
2,000px scroll; `.tit-matrix` computed `display: block` with `min-width: 0px`
and its scroller `overflow-x: visible`; every matrix cell still carrying
`data-filter` and `data-since`.

### The filter panel

The owner's words were "Fix the sapce all this" and "it's still not designed
well", and the diagnosis was that **there was no visual object called "a
group"**. Seven option groups sat in a three-column grid with 8px row gaps and
no boundary of any kind, so each group's options ran into the next group's
heading at the same weight and colour. A gap only reads as separation when it
exceeds the gap *inside* a group, and 8px never did.

- **Each group is now a bounded box**: heading, then its options inside a box
  with its own border, background and capped height. One column, 18px between
  groups, a hairline rule at each boundary.
- **Options are real checkboxes, one per line, and the box scrolls** — the owner
  asked for exactly that ("I like scrolling and check boxes"). This is the third
  shape this control has had: a native `select multiple size="5"` (keyboard-free
  for us, but a five-row window hiding fifteen of Industry's eighteen options,
  needing ctrl-click most readers do not know about), then a pill row (fixed
  discoverability, lost the list, and seven wrapping pill rows *were* the wall
  the owner complained about), now checkboxes. Measured: Industry renders 18 rows
  in a 162px box over 612px of scroll height.
- **The panel is a column beside the rows and sticks** at 1000px and up
  ("filters dont move with the page a like the layoff one"). It had to become a
  column first: a full-width block is taller than the viewport, so there is
  nothing to pin. Below 1000px it wraps to a normal stacked block, and
  `prefers-reduced-motion` forces `position: static`.
- Reset moved to the top of the panel, same `id`, so the same handler binds it.

**The state architecture did not move.** Each `<select multiple>` is still the
state and still what the querystring, chips bar, exports, quick views,
click-to-filter and share links read. `pillify()` in dashboard.js re-renders the
checkboxes *from* the select after every change. It also still hides the select
with a class it applies **at runtime**, which is what leaves a JavaScript-off
visitor a working native control; that is why the hiding must never move to the
server.

Two numbers that had to be kept in step by hand are gone: the list box was
pinned to 96px and the pill row to 96px because the swap happened after paint
and any difference was a layout shift. The select is `display:none` the moment
the script runs, so there is no swap and no pair.

### The three defects the owner named

1. **"remove exact locaiton only doens't make nses?"** — read `api.php` first.
   `country_basis=location` is real: it changes the country clause from
   `(country IN (..) OR (country IS NULL AND hq_country IN (..)))` to
   `country IN (..)`, dropping rows placed only by a substituted head office.
   So it was kept and renamed **"Only Countries A Source Named"**, which is the
   sentence the (i) panel was already using while the control called itself
   something else. **Stated limit rather than papered over:** it narrows the
   country clause only. The city clause in `tit_build_where()` is
   unconditionally the union form, so a city pick still admits a head-office
   match. Closing that is an `api.php` change and was out of this pass's lane,
   so the label says country and does not claim the city.

2. **"Only Updates That Move Headcount (54)" — "What does this mean?"** It
   filters `signal_direction IN ('hiring','displacement')` and reads nothing
   from the `headcount` column. Measured: `headcount` non-null on **11 of
   15,711** rows (0.07%); the direction test true on **53** (0.34%) — 51 hiring,
   2 displacement. So the label promised a column it does not touch, and the set
   is a third of one percent. **Decision: kept, relocated, relabelled** as the
   quick view **"Moves Headcount"** with its computed count printed on it.
   Removing it would have broken `/query` links already in the wild; leaving it
   in the panel gave a 0.34% control the same weight as Industry. A quick view is
   explicitly a narrow named cut, and the count means a reader sees the size
   *before* clicking. The checkbox survives in `.tit-state` as the state the
   button drives.

3. **The UK concentration note and the hidden-rows disclosure.** Both facts kept
   in full, both re-ordered so the reader meets the point before the arithmetic.
   The caveat now opens "Read United Kingdom as filing volume rather than as how
   much is happening there:" and the evidence follows. The detail note opens
   "You are seeing 12,568 of 15,711 updates. 3,143 routine filings are hidden."
   and defines "routine" in a trailing clause. The control itself was three
   stacked labels ("Officer and director filings" / "Hide the routine ones" /
   prose) and is now one setting and its value: **Routine Filings: Hidden /
   Shown**. Every figure still computed and still moves with the filters.

### Where I was told something that turned out to be wrong

- **"Funding Stage stops at Series B", "Work Setup has no Hybrid", "Site Change
  has no closure" — all three are neither render bugs nor vocabulary gaps.**
  `pipeline/vocab.py` already holds `series_c`, `series_d_plus`, `hybrid`,
  `closed` and `relocated`. `/facets` is deliberately **data-driven**: it lists
  only values actually present, because a control returning nothing reads as
  broken rather than as thin coverage. The real finding is coverage, and it is
  worse than the labels suggested. Across 15,711 current rows: `work_mode` is
  set on **4** (onsite 3, remote 1), `site_event` on **19**, `deal_type` on
  **23**, `funding_stage` on **33**. Five facet controls between them describe
  about **80 rows**. They hide themselves when a column is *empty*; they do not
  hide themselves when it is nearly empty, which is the same defect class as the
  headcount control. **Owner decision, not taken here:** raise a minimum-rows
  threshold before a facet control appears at all.
- **"Remove Where The Money Went entirely."** There is exactly one money surface,
  it is the one the owner pasted, and the owner separately said they loved the
  card format. Confirmed against the live page by curl (1.53.0): "Where The Money
  Went" appears once, and the three-card panel the endorsement described does not
  exist in this codebase at all. **So only the section HEADING went**, which is
  what the owner actually pasted and which repeated "Click a row to narrow the
  page" eight lines under "Click any row to narrow the whole page to it". The
  cards stay; the city card takes the wording "Where the Money Went".
- **"Manufacturing / Education / IPO appear in two groups each" — all three
  true.** Fixed as wording, never as vocabulary: `Production & Manufacturing`
  for the function (Industry keeps `Manufacturing`), `Educational Institution`
  for the employer type (Industry keeps `Education`), `Initial Public Offering`
  for the deal type (Funding Stage keeps `IPO`). Stored values untouched.
- **A "Why you can trust this" panel with numbered SOURCED / UNCONVERTED /
  UNGUESSED / CORRECTABLE items does not exist** in this repo, in the sibling, or
  on the live page. Not built: authoring it from a description of a screenshot
  would have meant inventing both a design and its copy.

### Title Case and one vocabulary

The owner asked for Title Case three times, so it is now **a test** rather than
a habit: `render_dashboard.php` reads the matrix row labels and the card
headings out of the rendered markup and asserts conventional Title Case (short
conjunctions and prepositions lowercase inside a label, first word always
capitalised, all-caps acronyms allowed). It regressed twice because a convention
nobody can check makes a wrong label look exactly as correct as a right one.

The deeper problem was **two vocabularies for one set of facts**. The charts said
`Pay and benefits` and `Growing and expanding`; the matrix beside them said
`Pay news` and `Funding raised` for the same rows. One list now, and the retired
phrases are asserted absent so a second vocabulary cannot creep back:

| was | is | why |
|---|---|---|
| Hiring up | **Adding Roles** | "up" was doing the work of "the source says headcount is rising" |
| Cutting back | **Cutting Roles** | "back" could have meant costs, hours or investment |
| Pay news | **Pay and Benefits** | the charts' phrase, which was already the better one |
| Funding raised | **Funding Rounds** | it counts updates |
| Money raised | **Total Raised** | it sums dollars, and "Total" says so |
| All updates | **Everything in This View** | a reader could not tell whether the 3,143 hidden filings were in it. They are not |

**Checked before renaming, because the sibling was bitten here:** on the layoff
tracker this same edit was a two-file data join, because an aggregate keyed its
rows *by label* and a cached response spanning the deploy window would have
silently killed click-to-filter. **That coupling does not exist here** — every
chart row carries its key on `data-k`, every matrix row on `data-signal`, the
filter a click applies is a separate `filter` field, and `tit_glance_matrix()`
keys its cells `c_{di}_{pi}` by index. Nothing reads a label. The test now pins
that it stays that way.

Renaming `Money raised` to `Total Raised` also **shortened a paragraph instead of
hiding it**: the block needed a sentence beginning "Money raised is the
exception" only because one row was lying about its unit.

### Mobile

Three separate containers required a horizontal gesture at 390px. All three are
gone.

- **The matrix stacks.** Five columns cannot fit 390px, so it had
  `min-width:560px` inside `overflow-x:auto` — which does stop the *body*
  scrolling, and was still wrong: the header rendered "THIS WEEK | THIS M..."
  under a scrollbar, on the first thing on the page, whose own copy says "Tap any
  number to filter the page". Below 860px each row is a card. **The period label
  is real markup** (`.tit-cell-p`, rendered by both `shortcodes.php` and
  `matrixHtml()`), not a CSS `::after` on a data attribute: `display:block` drops
  the implicit table roles and generated content is not reliably in the
  accessibility tree, is not selectable and is not findable. Nothing is keyed to
  `nth-child`. Every cell keeps its `data-filter` and `data-since`.
- **The geo strips wrap.** A previous pass had deliberately set
  `flex-wrap:nowrap; overflow-x:auto` on them below 560px, reasoning that a
  container scrolling beats the body scrolling. Both halves true, conclusion
  wrong: it put two stacked horizontal scrollbars on the first phone screen with
  "Glasgo" cut mid-word. The sibling reached the same verdict about its own pill
  strips — hiding options behind a swipe is the failure pills exist to fix.
- **The three explanations under the matrix are one disclosure**, collapsed on a
  phone, open on desktop, **not one word cut**. `open` is in the markup, so a
  crawler, a desktop reader, and a reader with no CSS or no JavaScript all get
  every word in the initial HTML with nothing fetched; a four-line function is
  the only thing that closes it, once, on a narrow viewport. It has to be script
  because `open` is an attribute and CSS cannot remove one. Re-collapsed after a
  repaint, or every filter change would undo it. The two paragraphs also became
  six single-idea lines ("this make s not sentds").
- **Dark scheme.** The stylesheet's existing note explains why there is no
  `prefers-color-scheme` block (the theme paints white regardless, so honouring
  the preference produced light text on white) and that reasoning stands. What was
  missing is that we never *told* the browser: without `color-scheme`, a UA in
  dark mode repaints controls, scrollbars and any background we did not set, which
  is exactly the mixed result in the owner's screenshot. `color-scheme: only
  light` plus explicit backgrounds and ink on our own headings. **Supported
  schemes are now stated: light.**

### Page order

Geo strips moved above the matrix ("Should we move this ... Aboe"): picking a
place is how most readers start. That invalidated a **pointer** — the quick-views
hint said "click a number in the matrix at the top" and the matrix is no longer
at the top. Grepped for others; that was the only one. A stale direction is worse
than none, because a reader follows it.

The chart cards also gained one bar pattern instead of two. "What Is Moving"
stacked its label above a full-width bar while the two cards beside it were
inline; the fix is `display:contents` on `.tit-pillar-head` so the button's own
grid takes over, **in CSS and not in markup**, because `.tit-pillar` is the
click-to-filter handler's selector and restructuring it would have risked a
working control to fix a visual inconsistency. Cards size to content
(`align-items:start`) rather than stretching a four-category card to match a
51-country one, the scroll edge fades rather than bisecting a row, and the
"Click a row to filter" that all six subtitles ended with is gone — the panel
header says it once.

### The harness now announces itself

The owner twice read a screenshot of `tests/php/render_dashboard.php`'s output as
the live site and concluded the data had broken. It renders the **real** dashboard
against a synthetic corpus, so it is byte-for-byte the shape of production with
different numbers, and the only tell was that its UK count outranks its US count.
Every fixture employer is now prefixed `TEST FIXTURE` and the placeholder headline
says so. The byte budget subtracts the prefix (~2.1KB of test-only content) before
measuring, or a legitimate change would eventually fail the budget for a reason
nobody could find.

### Held for a second pass, deliberately

- **The full Claude Design re-skin.** The mock is a 965-line React preview styled
  entirely with inline `style` attributes and **zero `@media` rules**, so it is a
  desktop specification only. Porting it means extracting every inline style into
  classes and authoring all responsive behaviour, and its character depends on
  three Google webfonts (`Source Serif 4`, `Public Sans`, `IBM Plex Mono`) on a
  page whose cold TTFB is already 2.5-4.0s and whose assets are deliberately
  CDN-free. **No font was substituted and none was added**; this pass changed
  layout and wording inside the existing token set. The mock's own decisions that
  cost nothing were adopted: the sidebar filter column, the checkbox rows, the
  place-basis wording, the headcount cut as a quick view, and the city money card
  as "Where the Money Went".
- **The dated four-bucket glance panel** replacing the hero figure line, with the
  week-over-week comparison suppressed until real history exists. Not started.
  The suppression rule is the load-bearing part: news collectors first ran on
  27 July, `national_press` on 29 July, so "this week vs last week" would compare
  a populated week against an empty one and print something like "up 4,000%".
- **The FAQ tab and the trust panel** (does not exist to move; see above).
- **The sibling port.** Not touched, and not only for budget: `CLAUDE.md` names
  the sibling "do not touch", it is outside the lane I was given, and that repo
  auto-deploys on push. It needs its own session in its own repo.
- **A minimum-rows threshold for facet controls** (the ~80-row finding above).
  That is an owner decision about what a nearly-empty control should do.

---

## 2026-07-30 — the UK register is not the source; the 250-employee roster is

Build the Companies House connector, now that the key exists. It ships. The
interesting half of the work is not the connector, it is the **refusal to point
it at the register**, and every figure below is measured rather than argued.

No authenticated call has been made from this repository — the key exists only
as a GitHub secret — so everything here was measured against the PUBLIC register
web pages (which need no key), the free bulk Company Data Product, the GOV.UK
gender pay gap download, and the published API specification. What that leaves
unproven is listed at the end, and it is a short list.

### The register is 190x too big, and the excess is dormant micro-companies

Part 1 of 7 of the free Company Data Product for 2026-07-01 holds **849,999
live companies**; the seven parts are not equal sizes (part 7 is 52Mb against
69-70Mb), so the register is **~5.7 to 5.9 million** rather than a round figure.
A random sample of 120 of those companies, read one officers page each:

| | random live register | GPG 250+ roster |
|---|---|---|
| companies | ~5.7M | **9,230** |
| appointments per company per year | 0.246 | **0.867** |
| active officers, median | 1 | 4 |
| officers ever recorded, median / mean | 2 / 4.0 | 26 / 44.4 |
| projected appointments a year | ~1.4M | **~7,354** |
| projected stored rows a week | ~27,000 | **~110** |

**~27,000 appointments a week against a database of 15,711 signals.** Four days
of unfiltered collection and the tracker is a list of UK director changes with
some other content attached. It is also mechanically impossible: 5.7M requests a
week is 33 days of continuous polling at 600 requests per 5 minutes.

And the excess is not merely large, it is empty. The random sample's median
company has **two officers in its entire history**; the names it returned are
`AD ASTRA BARS LTD`, `B-LEAF HEALTHCARE LTD`, `AVENIR WORKS 6 LTD`, `5374 LTD`.

### The filter is a statutory employee count, and the obvious alternative fails

The chosen population is the **GOV.UK gender pay gap roster**: every employer
with 250 or more employees in Great Britain must report, the CSV carries a
`CompanyNumber` column, and this repo already reads that file.

    2025 reporting year          11,154 employers
      well-formed CH number       9,634  (86.4%)
      in a 250+ size band         9,230  <- the population

Coverage of the biggest employers is *worse* than average and it is worth
knowing why: 301 of 546 in the 5,000-19,999 band and 51 of 67 in the 20,000+
band carry a company number, because the largest UK employers include NHS
trusts, councils and government departments that are not companies at all.

**The accounts-category filter the brief suggested was built as a measurement
and refused.** `FULL` / `GROUP` / `MEDIUM` is 2.05% of the register (~120,000
companies), and joining the roster to the same snapshot gives its precision
directly: **1,104 of 17,378** such companies in that slice are 250+ employee
employers — **6.35%**. So it is 13x the roster to poll, 94% of it is not what we
are looking for, and it still misses 14% of the roster (180 of 1,284 roster
companies in the slice file as audit-exempt subsidiaries, small, or nothing).
The reason is structural and worth keeping: **accounts category records how a
company chose to file, not how many people it employs.** A two-employee
property vehicle with a large balance sheet files FULL; a 400-person business
can file as a subsidiary. SIC code was refused for the same class of reason — it
is a topic filter, and it cannot tell a 3,000-person software company from a
dormant one. Nothing the register exposes as a search helps either:
`advanced-search/companies` filters name, status, type, incorporation date,
location and SIC, and nothing about size.

Full accounts-category distribution on that 849,999-row slice, since it took a
73MB download to get and should not need a second one: MICRO ENTITY 32.63%, NO
ACCOUNTS FILED 25.38%, TOTAL EXEMPTION FULL 22.61%, DORMANT 12.54%, UNAUDITED
ABRIDGED 2.82%, FULL 1.48%, SMALL 1.15%, AUDIT EXEMPTION SUBSIDIARY 0.57%,
GROUP 0.46%, TOTAL EXEMPTION SMALL 0.16%, MEDIUM 0.11%.

### Where the brief was wrong, and it was the load-bearing part

**"There is a streaming API ... that is almost certainly the right primitive
rather than polling companies one by one."** It is not, on two independent
grounds, both checked rather than assumed.

1. A REST key cannot open it. The streaming authentication guide says
   "Applications that are to use the streaming API must be registered as such,
   the REST API and streaming API keys are not interchangable."
   `COMPANIES_HOUSE_API_KEY_UK` is documented as a REST key with the REST rate
   limit, so it will 401 on the stream.
2. Even with the right key it is the wrong shape for this repo. The stream is a
   long-lived connection resumed by a stored `timepoint` (too old a timepoint
   returns 416), capped at two concurrent connections per account. Every
   database writer here shares one `talent-collect` lock and runs as a bounded
   Actions job that commits and exits. A process that must stay connected to
   keep its place is the opposite of that, and its missed windows would be
   unrecoverable rather than back-fillable.

Polling turns out to be the property that makes this safe rather than a
compromise: `appointed_on` is a field on every officer record, so a window is a
filter over data the endpoint always returns, and **this collector stores no
state whatsoever.** A missed run loses nothing and a wider window is one
integer.

Two smaller corrections. The brief said the free bulk product has no officers
data — true, and it is still the right thing to download, because it is the only
free way to count the denominator and test the accounts-category filter. And
`find-and-update.company-information.service.gov.uk` was **already** in
`vocab.PRIMARY_SOURCE_DOMAINS` before this session, so rows reach `verified`
with no vocabulary change.

### The rotation, because a whole sweep holds the lock too long

10,568 requests sweep the roster (1.145 requests per company at 100 officers a
page — officers-ever runs median 26, mean 44.4, p90 66, max 1,992, so 98% of
companies need exactly one page). At `REQUEST_DELAY = 0.55s` that is **97
minutes**, and `writer_queue.LONG_HOLD_MINUTES` is 120.

So the roster is sliced four ways by a **blake2b digest of the company number**
— not `hash()`, which is salted per process and would reshuffle the rotation
every run, leaving some companies unvisited for months while the run count
looked perfect — and the ISO week number picks the slice. Nothing is committed
and there is no cursor to corrupt. Measured slice sizes: **2,344 / 2,295 /
2,321 / 2,270**, so **~2,600 requests and ~25 minutes** a run.

The window is **derived** from the rotation the way `recency_window_days`
derives Google News's: `SLICES * 7 + 14` = 42 days. Each visit therefore covers
28 new days and 14 already seen. The overlap is the point: it costs nothing
(exact `content_hash` duplicates, skipped before any write) and it makes a
single missed run recoverable on the slice's next visit instead of a permanent
hole.

### Four judgements that are not obvious from the code

**A body corporate is not an employee** — the `bse_india` auditor rule again,
and the register proves it is needed: `LEGAL & GENERAL CO SEC LIMITED` is the
sitting secretary of Legal & General Resources Limited. So the role allowlist is
`director`, `secretary`, `llp-member`, `llp-designated-member` and nothing else;
every `corporate-*` and `nominee-*` role is named in `EXCLUDED_ROLES` rather
than merely absent. Measured cost: 2 of 231 appointments (0.9%) were a body
corporate, 63 of 3,151 officers (2.0%) were nominees.

**The allowlist reads `officer_role` verbatim, with no case folding, and that is
a deliberate strictness.** The public web page renders a `corporate-secretary`
as plain "Secretary" — which is also why the 150-company measurement that sized
this source could not see corporate officers at all, and why its yield figure is
about 1% high. Folding case would let the string the web page prints through the
one check that exists to catch it. The first version did `.lower()`; a test
caught it.

**Every row is `neutral`, never `hiring`.** The register records the legal fact
of an appointment and says nothing about where the person came from: a group
finance manager added to a subsidiary board is filed identically to an external
chief executive hire. Precision over recall, the same rule `bse_india` applies
to a re-appointment.

**Resignations are refused in v1**, with the number: `resigned_on` is on the
same records and would add **80% more rows** (184 resignations against 231
appointments in the sampled two years) that say the least of anything this
source could produce, because the register never says why somebody left.

### Identity, geography and the concentration this was meant to fix

The employer name is the pay-gap file's `CurrentName` falling back to
`EmployerName` — the **same expression `uk_paygap.parse_csv` uses**, on purpose,
so `vocab.company_key` lands on the same employer and a company profile shows
one employer's pay and its board rather than two near-identical employers.
Verified against the live database: `LEGAL & GENERAL RESOURCES LIMITED` keys to
`legal & general resources`, which already has `uk_paygap` rows; 493 of the
9,228 distinct roster keys do (the rest because `uk_paygap` defaults to a 5,000
employee floor).

Geography follows `uk_paygap` exactly, by importing its map rather than copying
it: the registered office postcode area fills `hq_city` and only for
unambiguous areas, `city` is never set at all, `industry` and `employer_type`
come from the filed SIC division. **Nothing here splits an address on a comma**,
and a test asserts the source text does not either — `ats_boards` turning
"Cambridge, MA" into Morocco is the reason.

GB rows today are **4,801, of which `uk_paygap` is 4,761 (99.2%)**. At ~110
rows a week the concentration falls below 90% inside five weeks.

`source_url` is `/officers/{officer_id}/appointments` — the register's own page
for that person, which names the company, the role and the appointment date, and
which is keyed on a permanent officer id **read out of the API's
`links.officer.appointments`, never composed** (BSE's AttachLive → AttachHis rot
is what an invented identifier does). It is not the company officers page, which
would be one URL for every appointment the company ever makes. Because one
person can be appointed twice, `REVISITS_ITS_SOURCE_URL = True`, so dedup runs
on `content_hash` and the fuzzy window rather than on URL-seen — the
`ats_boards` lesson.

### GB is promoted, and it should have been promoted before this

`GB` moves `discovery_only` → `structured_official`. Two things about that.
`uk_paygap` has been a working GB structured connector with a health check and a
passing test since 2026-07-28 and was **never listed in the market's
`live_sources`**, so the tier understated the country while the country chart
was dominated by that very source. And "Companies House appointments" sat in
`candidate_official_sources` — the roadmap — while being the thing this entry
builds; it is removed from there.

### Numbers

- 9,230 companies in the population, from 11,154 pay-gap employers.
- 4 slices, 2,344 / 2,295 / 2,321 / 2,270, ~2,600 requests and ~25 min each.
- 42-day window, derived. ~200 candidates a run, ~133 of them new.
- ~110 stored rows a week, ~5,600 a year, all at 250+ employee employers.
- **$0.** `as_classified` closes the record; no model is called on this path.
- 72 tests, offline, against a fixture of real register values.
- Suite 1,823 → 1,996 (the two concurrent Japan and Korea connectors are in
  that number too). `ops_status.py` exit 0, `structured_official` now `[GB, IN]`.

### Access and licence, checked first

- `api.company-information.service.gov.uk/robots.txt` → **401**
  (`{"error":"Empty Authorization header"}`). Every path on the API host needs
  auth, so there is no directive to honour and the default applies.
- `stream.company-information.service.gov.uk/robots.txt` → **401**, same.
- `find-and-update.company-information.service.gov.uk/robots.txt` → **404**
  with an HTML page. No directives. This is the host the measurements read.
- `download.companieshouse.gov.uk/robots.txt` → **200**, `User-agent: *` /
  `Disallow:` — explicitly everything.
- Public sector information; the OGL attribution rides in the summary of every
  stored row, exactly as `uk_paygap` carries its own.

### Unproven until the first real run, and it is a short list

Everything authenticated. Specifically: that `items_per_page=100` is accepted,
that `total_results` counts what the docs say, that HTTP Basic with an empty
password is the accepted credential form, and the exact `officer_role` strings
on live rows. All four are pinned by tests against the documented shape and all
four fail loudly rather than quietly. First run:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
     -f inputs_json='{"source":"companies_house","dry_run":"true"}' \
     -f reason='first authenticated Companies House run'
```

What was verified without the key: the roster (`ch.roster()` returns 9,230 from
the live pay-gap download), the rotation, the window arithmetic, the whole
`collect → as_classified → build_signal` path against a stubbed session — one
row out the far end, `verified`, `GB`, `hq_city=London`,
`industry=professional_services`, `published_date=2026-07-01`, direction
`neutral` — and that a keyless run fails with the message that names the
streaming-key trap rather than storing zero quietly. The emptiness floor fired
on that stub run before it was lifted for the demonstration, which is the guard
working.

---

## 2026-07-30 — Korea's spine is the report TITLE, because its typed codes stop one level too coarse

Build the Korean equivalent of the India connector. It ships, it costs nothing,
and Korea stays `discovery_only` because the SOURCE is measured and the
CONNECTOR has never made an authenticated call. Every number below came from a
command in this repo; no OpenDART credential was used at any point.

### The endpoint list was walked before a line was written

All six published API groups, 84 endpoints, read from
`https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001..DS006` on 2026-07-29.
There is no Item 5.02 equivalent to ask for, and the reason is precise:
**`pblntf_detail_ty` has about 60 values and every Korea Exchange timely
disclosure shares ONE of them, `I001`** — supply contracts, dividends, buybacks,
CEO changes and litigation all arrive under the same code. That is the Form 6-K
problem again: a filing type with no item taxonomy inside it.

**Two things rescue it, and one of them is a measurement rather than a document.**

`E005` (독립사외이사에관한신고) is a detail code of its own, and every row it
returns carries one report name. Inside `I001`, the exchange's own report TITLE
turns out to be a fixed vocabulary: **8,211 `I001` filings over 2026-05-01 to
2026-07-29 collapse into 360 DISTINCT titles**, and the leadership ones recur
character-for-character. Those are KRX's form titles, generated by the filing
system, not sentences a company composed — the same class of value as BSE's
`SUBCATNAME`, and the only thing that makes `I001` usable at all.

Measured unauthenticated through DART's own public search
(`dart.fss.or.kr/dsab007/detailSearch.ax`, which robots.txt permits — it
disallows six paths and that is not one of them), and then re-counted by running
the shipped collector's own `is_wanted` / `strip_amendment` / `REFUSED_REPORT_NAMES`
over the captured rows:

| window | rows read | stored-eligible | refused by name | amendments skipped | not leadership |
|---|---|---|---|---|---|
| 2026-05-01..07-29 (90d) | 8,363 | **261 (3.1%)** | 4 | 4 | 8,094 |
| 2026-07-01..07-29 (29d) | 2,561 | **88 (3.4%)** | 4 | 0 | 2,469 |

The 261, by title:

| report title | FSS's own English | 90d |
|---|---|---|
| 독립이사의선임ㆍ해임또는중도퇴임에관한신고 | Report on the Appointment, Dismissal or Early Retirement of Independent Directors | 150 |
| 대표이사변경 | Change of CEO | 79 |
| 대표이사(대표집행임원)변경(안내공시) | Notice on Change of CEO | 28 |
| 대표집행임원변경 | Change of Representative Executive Director | 4 |

Per ISO week over twelve full weeks: **12 to 49, median 19**, across KOSPI (29),
KOSDAQ (74) and KONEX (6) on the CEO items alone. `MIN_ROWS_PER_WINDOW = 5` sits
below the observed floor, so a run that returns fewer has broken rather than gone
quiet. March is Korea's shareholder-meeting season and runs higher, so a summer
measurement is the conservative one.

**~1,060 a year, which is about 8% of India's ~13,000, and the gap is scope not
diligence.** SEBI Regulation 30 covers every director and every key managerial
person. Korea's mandated item covers the representative director, and separately
independent directors. Ordinary inside directors are elected at a shareholder
meeting whose result is untyped prose, so they cannot be reached from here.

### What was refused, with the numbers

**The periodic-report endpoints are snapshots, exactly as the brief feared, and
diffing them was declined.** `exctvSttus.json` (임원현황) returns every sitting
officer as of `stlm_dt` — name, position, `hffc_pd` tenure as free text
("3년 6개월"), term expiry. `empSttus.json` (직원현황) returns headcount by
division and gender. Neither states that anybody was appointed and neither
carries an appointment DATE, so an event out of them means diffing year N against
N-1 and stamping the difference with a date the source never stated. Both are
also **per-`corp_code` only**: there is no date-ranged form, so even a snapshot
sweep is one request per filer per report code.

**The 주요사항보고서 family has no officer item at all.** All 36 endpoints in
group DS005 were read: insolvency, capital raises, buybacks, mergers, divisions,
asset transfers, business suspension. Not one is an appointment or a departure.
**The brief that commissioned this named that family as a candidate; it is a dead
end**, and that is now written into `source_registry.py` so nobody researches it
twice.

**`독립(사외)이사 및 그 변동현황` is the one endpoint with change FIELDS and they
are still not events.** `apnt` (선임), `rlsofc` (해임) and `mdstrm_resig`
(중도퇴임) are period COUNTS with no person and no date. An aggregate is not a
record about anybody.

**`elestock.json` (임원ㆍ주요주주 소유보고) was the near miss.** It is
event-driven, it carries a real `rcept_dt`, and it names the officer and their
position. But the API exposes **no 보고사유 field**, so an appointment cannot be
told from a share purchase, and reading one as the other would invent the event
type rather than the number.

**`대표이사변경 (자회사의 주요경영사항)` — the chaebol trap, 2 of 261.** A listed
PARENT reports a change at a subsidiary it does not name in the title, and
`corp_name` is the parent. Miwon Holdings and MAEIL HOLDINGS each filed one.
Refused by name. `기업인수목적회사의임원선임결정` (2 more) is a SPAC appointing
its own formation officers, which is a company being incorporated rather than
anybody being hired.

**Amendments are not second events.** `[기재정정]`-prefixed rows are skipped:
this tracker corrects a record with `store.revise()`, never with a second row.
That costs the amendments whose original fell outside the window — 4 of 265 over
90 days — and the price is written down rather than hidden.

### Four traps, each found by fetching something

**1. The English viewer answers 200 with the single word "Reject".**
`englishdart.fss.or.kr/dsbh001/main.do?rcpNo=` looked like the ideal
`source_url`: the same document with FSS's own English labels, on a host that
serves no robots.txt. Sampled on 20 real filings from the allowlist, **16
rendered and 4 returned a page whose entire body is the word "Reject"** — among
them Kia and Korea Gas Corporation, so it is not an obscurity effect. A citation
that answers 200 with one word is worse than a 404, because a link checker calls
it live. `source_url` is therefore `dart.fss.or.kr/dsaf001/main.do?rcpNo=`, the
form OpenDART's own field documentation gives for every `rcept_no`.

**Said plainly, because it is a real cost: `dart.fss.or.kr/robots.txt` disallows
`/dsaf001/main.do`.** Nothing here fetches it — the collector talks only to
`opendart.fss.or.kr/api/`, which serves no robots.txt at all — so `link_check.py`
will record these URLs as `robots` rather than checking them. That is the correct
outcome rather than a defect to route around, and Wayback already holds
`dsaf001/main.do` snapshots going back to 2009, so archiving is not blocked.

**2. A missing key is a 302; a bad key is a 200.** Verified live and keyless on
2026-07-29: `list.json` with no `crtfc_key` returns **HTTP 302** and an HTML
error page, while a syntactically valid but unregistered key returns
**HTTP 200 `{"status":"010"}`**. So neither the status code nor "the body parsed
as JSON" means success — `status` is the only authority. This matters more than
usual here because CLAUDE.md already records that mapping a MISSING GitHub secret
sets the variable to empty string, which is how a leadership dispatch once went
green having stored nothing. `api_key()` refuses an empty or non-40-character key
before a request is spent, and names the 302 in the message.

**3. Full-width digits break the verbatim-figure guard.** `validate._numbers_in`
tokenises with `\d`, which matches U+FF10..FF19, and `_normalize_number` does not
fold them, so `１２３` in a summary and `123` in `raw_text` compare unequal and a
correct record is discarded silently. `_squeeze` folds them to ASCII on the way
in. **NFKC would be the obvious fix and is WRONG here**: it rewrites U+318D — the
ㆍ inside 독립이사의선임ㆍ해임또는중도퇴임에관한신고 — to U+119E, so the
allowlist would stop matching the report name the API sends. Both halves are
pinned by tests. Belt and braces on top: every figure in the summary is a
substring of the same string quoted into `raw_text`, following `bse_india`.

**4. A Korean company_key produces a URL that 404s.** `vocab.company_key` passes
Hangul straight through (verified: `company_key("한울앤제주") == "한울앤제주"`),
and `tit_company_slug()` is `[^a-z0-9]+ -> '-'` with a `rawurlencode` fallback
when the result is empty — and HANDOVER.md records that percent-encoded slugs 404
on this host. So the stored employer is **`corp_name_eng` from `company.json`,
the company's OWN registered English name**, fetched once per company per run and
cached in memory. A filer whose `corp_name_eng` is blank is DECLINED and counted:
this file invents no transliteration. The Korean `corp_name` is still quoted into
`raw_text`, because it is the filer's own name and the record should carry it.

### Two things deliberately not stored

`stock_code` is not written to `ticker`. That column is SEC-authoritative
everywhere else in this tracker (`pipeline/identity.py` resolves it from
`company_tickers.json`), and a 6-digit KRX code beside `AAPL` is two vocabularies
in one filter. `adres` from `company.json` is not read either: a registered legal
seat is not where an appointment happened, and `identity.py` is already the single
authority for `hq_city`. No city is guessed, so Korean rows place at country level
only, as Indian ones do.

### Direction is never inferred, and that is the honest weakness

`대표이사변경` says a change happened. `독립이사의선임ㆍ해임또는중도퇴임에관한신고`
names all three possibilities in one title. Neither separates a joiner from a
leaver, so **every row here is `neutral`**, as India's departures already are.
`displacement` is never used: one officer leaving is a change of leadership, not a
workforce reduction, and workforce reductions are the sibling's scope. Recovering
the direction means downloading and parsing the filing body, which is document
parsing at best and an LLM call at worst, and zero cost is the premise.

One consequence worth stating: **`prefilter.filing_reduction_plan` returns None
on Korean text** (checked: `구조조정 인원 감축` -> None). The scope guard is
English-only, so on this source the report-title allowlist IS the scope guard —
and a CEO change cannot be a workforce reduction, which is why that is sufficient
here and would not be for a prose source.

### The sibling's OpenDART retirement does not transfer

`/Users/dakotta/Projects/atr-layoff-tracker` holds `railway/sources/opendart.py`,
retired on 2026-07-24 in commit `aead15e` with the reason "**0 layoff rows ever**
came from EDINET(JP)/OpenDART(KR)/CVM(BR)". Read read-only; nothing imported,
nothing copied. **That is outcome 2 of the three the coordinator named, and it is
outcome 2 for a reason that is now proved rather than assumed.** The sibling read
the disclosure list for discovery and then scanned document BODIES for Korean
layoff vocabulary. Korean statutory disclosure has no workforce-reduction item —
the 36 major-report endpoints above are the proof — so its zero was guaranteed by
the taxonomy, not by the source's quality. Read it as a fact about layoffs, not
about appointments. Two things from that codebase were genuinely useful as
RESEARCH and are re-derived here rather than borrowed: the `status` code
semantics, which are on FSS's own message table, and the fact that somebody
already tried the English viewer as a citation, which is what prompted measuring
it and finding the "Reject" page.

### Where this brief was wrong about the repo

1. **"Promote KR from `discovery_only`" — KR was not in `MARKETS` at all.**
   `("ko", "KR")` has been in `GOOGLE_NEWS_LOCALES` with its own Korean query
   pack the whole time, and `data/sources_catalogue.csv` carries five Korean
   publisher feeds, so the country was being swept while the coverage manifest
   said nothing about it. KR is added now, at `discovery_only`.
2. **"the major-report or 사업보고서 family"** carries no officer change. All 36
   endpoints checked; see above.
3. **`data/sources_catalogue.csv` needed no edit.** That file is the publisher
   FEED catalogue; a structured connector is registered in
   `source_registry.SOURCES` and `COLLECTOR_BY_SOURCE_NAME`, and
   `build_sources_json.py` derives the page from those.

### Korea stays discovery_only, on purpose

The rule is that coverage is earned by a working connector, a health check and a
passing test. There are 48 passing offline tests and the whole
`_row -> as_classified -> build_signal -> store` path runs against a throwaway
database (4 stored, 0 rejected, 3 declined for the three stated reasons). What
does not exist is a single authenticated call. **What the source holds is
measured; what the connector does is not**, and a tier is a public claim about
the connector. Promotion is one commit after the first real run: add
`opendart_korea` to `KR.live_sources` and move the status, recording what the run
returned.

Unproven until then, listed so the first run knows what to look at: the exact
`list.json` row shape (taken from FSS's published response spec rather than from
a response), **whether `corp_name_eng` is populated for every listed filer** —
which is the one that decides real yield, because a blank declines the row — and
the real request cost of one window (estimated at ~8 list pages plus one
`company.json` per distinct employer, so roughly 30 requests against a documented
20,000/day quota).

---

## 2026-07-30 — Japan has a typed CEO clause; the sibling's EDINET zero was the ordinance, not the source

Build the Japanese equivalent of the India connector. It ships, it costs nothing,
and it is **much narrower than the brief assumed** — narrow enough that Japan
stays `discovery_only`. Every number below is reproducible from a command; the
one thing that is NOT measured is the only thing that matters for promotion, and
it says so.

### The sibling had already built and retired this. That result does not transfer

`/Users/dakotta/Projects/atr-layoff-tracker/railway/sources/edinet.py`, wired to
`foreign-filings.yml`, retired in commit `aead15e` on 2026-07-24: *"0 layoff rows
ever came from EDINET(JP)/OpenDART(KR)/CVM(BR). Those regulatory filings
essentially never announce layoffs"*. Read read-only; nothing imported, nothing
copied.

**That zero was guaranteed by the ordinance, not earned by the source.** Read the
law and count:

```
python3 -c  # against e-gov lawdata 348M50000040005, parsed with ElementTree
  Article 19(2) has 44 items.
  Items containing ANY workforce-reduction word (解雇/人員/削減/希望退職/
    早期退職/整理解雇/リストラ/雇用/従業員数/退職): NONE
  Items mentioning 代表取締役: ['9']
```

An extraordinary report **cannot** announce a layoff, because no clause requires
one: the 44 triggers are disasters, lawsuits, mergers, divestitures, subsidiary
and shareholder changes, bankruptcy, debt covenants, auditor changes and one
officer clause. A layoff tracker pointed at this was structurally certain to
return zero on day one. So the retirement is a fact about layoffs and says
nothing about appointments.

Two further things the sibling's code shows, both load-bearing here:

* **It never read `currentReportReason`.** `grep` for it in that file returns
  nothing, as does `臨時`, `180` and `reason`. It fetched every document type,
  then downloaded ZIP archives and scanned bodies for layoff vocabulary — the
  expensive path, and it skipped the typed field entirely.
* **Its `source_url` does not resolve.** `viewer_url()` returns
  `disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID=<id>`. Measured 2026-07-29:
  that URL returns the **same 82,145 bytes** for a real id (`S100VV88`) and a
  nonsense one (`S100ZZZZ`), and `docID` appears nowhere in the HTML. It is the
  search screen. See the source-URL section below.

### The clause: verified, typed, and only one of them

`currentReportReason` (臨報提出事由) is a document-list **metadata** field, and the
EDINET API specification (Version 2, 2026-06, page 47 item 29 + footnote *4)
defines it as a clause number, comma-joined for multiple reasons:

> 「臨報提出事由は、『第19条第2項第1号』、『第29条第2項第1号』のように記載され…」

So the reason is a closed machine-readable label of the same class as Item 5.02
and a SEBI Regulation 30 category. **The brief's STOP condition — "if it is only
free prose, stop" — does not fire.** No document is downloaded and no model is
called; `as_classified` closes the record and spend is zero.

`docTypeCode` 180 = 臨時報告書, 190 = 訂正臨時報告書 (spec page 88).

**The scope is the representative director alone.** Article 19(2)(ix) is the only
officer clause in 44, and it reads 提出会社の代表取締役…の異動 — the chief
executive and co-representatives, not the wider board and not senior management.
India's Regulation 30 covers every director and every key managerial person;
Item 5.02 covers directors and principal officers. **Do not describe this as
"officer changes".** It is a CEO-change feed.

### Four traps, each of which would have shipped silently

1. **A substring match files audit firms as leadership changes.**
   `第19条第2項第9号の2`, `の3` and `の4` all have the accepted clause as a
   string PREFIX, and they are shareholder-meeting resolutions, a rejected AGM
   resolution, and **a change of accounting auditor**. That last is the
   `bse_india` auditor exclusion arriving in a different disguise: an audit firm
   is an appointed firm, not an employee. Worse, `第29条第2項第9号` belongs to a
   DIFFERENT ordinance (405M50000040022, specified securities) where item 9 is
   ファンドの併合 — a **fund merger**. Read from that ordinance, Article 29(2)
   has no officer clause at all, so REITs are excluded by law rather than by
   taste. Matching is therefore whole-element equality, never `in`.

2. **HTTP 200 on every error, in two different body shapes.** Verified live
   against the real host on 2026-07-29, and documented at spec pages 82-84:

   | condition | HTTP | body |
   |---|---|---|
   | no key / bad key | **200** | `{"StatusCode": 401, "message": "Access denied due to invalid subscription key…"}` |
   | throttled | **200** | `{"StatusCode": 429, …}` |
   | bad parameter / not found / server error | **200** | `{"metadata": {"status": "404", "message": "Not Found"}}` |

   A `resp.status_code != 200` check sees success, finds no `results`, and
   reports a healthy empty day — so an expired key and a throttled run would
   both look like "Japan filed nothing", forever. `_status_of` reads both shapes
   and anything but 200 raises. The sibling's client checked `status_code` only.

3. **Full-width digits eat correct records.** `currentReportReason` is typed
   全半角 in the spec, so the clause can arrive as `第１９条第２項第９号`.
   Python's `\d` matches full-width digits, so `validate._NUMBER` tokenises a
   half-width summary as `{19,2,9}` against a full-width `raw_text` as
   `{19,２,９}`, and `assert_figures_are_sourced` discards the whole record for
   "inventing" 2 and 9. Demonstrated before the fix was written:

   ```
   assert_figures_are_sourced("filed under 第19条第2項第9号",
                              "…内閣府令第19条第２項第９号の規定に基づき…")
   -> Rejected: figure(s) not present in source text: ['2', '9']
   ```

   The collector normalises the clause once and writes that SAME string into
   both the summary and `raw_text`, so the two cannot diverge. This is the third
   instance of this bug class in three days (the `sec_execcomp` newline glue and
   the missing thousands separator were the first two), and the pattern is
   always the same: two renderings of one figure that were never compared.
   Pinned by `test_a_full_width_clause_still_round_trips` and by a test that
   asserts the un-normalised pairing really is rejected.

4. **A Japanese company name produces an EMPTY slug.** `vocab.company_key`
   passes non-ASCII through untouched, so `株式会社オプトラン` becomes
   `株式会社オプトラン` and the company-profile slug
   (`[^a-z0-9]+ -> -`) is `""`. Every Japanese employer would collide on the
   empty slug and the profile route would break. **The fix is not a
   transliteration rule of ours.** The official EDINET code list publishes each
   filer's own English name, and a filer without one is DECLINED and counted.
   Measured on the real list, 2026-07-30: **3,428 of 3,829 listed filers carry
   one (89.5%)**, so ~10% of Japanese filings are refused by design.

### The source URL is the document, because the viewer is not

| candidate | real id | bogus id | verdict |
|---|---|---|---|
| `disclosure2dl…/searchdocument/pdf/{docID}.pdf` | 200 `application/pdf` | **404** | stored |
| `disclosure2…/WEEK0010.aspx?docID=` | 200, 82,145 B | 200, **82,145 B** | refused |

The BSE lesson was link ROT (AttachLive → AttachHis). Japan's trap is the
opposite and worse: a URL that can never rot **because it never resolves**, so
`link_check.py` would report it healthy forever while every Japanese row cited a
search box. The PDF permalink needs no API key, so a reader can open it.

### Licence: a green light, and it constrains the design

EDINET's terms (`WZEK0030.html`) put the content under the Japanese **Public Data
License 1.0** — commercial reuse and redistribution permitted — and require
attribution (carried in `source_name`). Unlike ASX, nothing here forbids
aggregating and republishing. But they prohibit scraping the website while
explicitly exempting the API:

> 「スクレイピング等を利用して本ウェブサイトからコンテンツを機械的に取得すること
> は禁止します。ただし、API機能を利用する場合はこの限りではありません。」

That is why every FACT comes from the API. The one non-API fetch is the code
list, which the spec itself publishes as a 固定リンク for API users (page 86), so
it is the sanctioned path rather than a scrape. It also closed off measuring
volume by crawling the viewer: a refusal to measure by a prohibited method.

### What it refuses to claim

* **Every row is `neutral`, never `hiring`.** Item 9 covers a person becoming a
  representative director and ceasing to be one under ONE clause, so the typed
  metadata cannot tell an arrival from a departure. Guessing would make half the
  rows wrong. Recovering the direction means reading the body — an LLM call per
  document — and that trade was declined, because zero-cost is the premise.
* **No person is named**, for the same reason. The filing is linked and says so.
* **No city, ever.** The code list's address is ward-level with full-width digits
  and, for the Tokyo wards holding most large filers,
  `新宿区西新宿六丁目５番１号` never says Tokyo. A city would need a ~1,900-entry
  municipality vocabulary, and guessing is how `ats_boards` turned
  "Cambridge, MA" into Morocco. `country` is Japan by construction.
* **No figure at all.** The metadata carries no amount and no headcount, so the
  only numerals reaching a summary are the clause and the filing date.
* **Corrections (190) are skipped, not stored.** Storing one would double-count
  an event, and this repo appends revisions rather than overwriting. The hook a
  future session needs is `parentDocID`, and it is on the row.

### THE RECALL HOLE, which is large and invisible

Item 9 exempts a change occurring between the annual shareholders' meeting and
the filing of the annual report when the annual report already describes it.
Japanese AGMs cluster in late June and 有価証券報告書 are filed in the same
weeks, so **the commonest timing of a Japanese presidential succession can
produce no extraordinary report at all.** This source is a floor on Japanese
leadership change, not a count of it. Said in the read-through, the registry note
and the sources page, and asserted by a test.

### Measured, and the one thing that is not

Offline, whole `run_collect` path, stubbed transport, nothing written:

| | |
|---|---|
| list API calls | 7 (one per calendar day; the endpoint takes one date) |
| code-list downloads | 1 |
| documents read | 12 |
| extraordinary reports (180) | 10 |
| reporting `第19条第2項第9号` | 6 |
| stored | 3 |
| declined (no English name / withdrawn / viewing expired) | 3 |
| corrections skipped | 1 |
| **rejected by validate** | **0** |
| **deferred** | **0** |
| cost | **$0.00** — no model, no document fetch |

Tests **1,823 → 1,876** (+53), all green. `ops_status.py` exits 2 before and
after, on the same three pre-existing stale collectors (gdelt 54h, sec_edgar 52h,
sec_form_d 60h); nothing here added an item. Two `source_health` error rows
written by keyless local dry runs were deleted afterwards, so the committed
database carries no false alarm — the database itself is NOT staged by this work.

**VOLUME IS UNMEASURED, and that is the whole reason Japan stays
`discovery_only`.** No authenticated call has ever been made from this repo: the
key exists as a GitHub secret and was deliberately not available locally, so
unlike India's 354-in-7-days and Australia's 192-in-30 there is no live count
here. The bound, stated as an estimate and not a measurement: **3,829 listed
filers** on the official code list against a published Japanese president-turnover
rate of **3.84% for 2025** (Teikoku Databank) puts the order of magnitude at a
**few hundred a year, roughly 1-3% of India's ~13,000** — before the AGM
exemption above removes more. Thin, but a CEO change is the highest-value
leadership row there is.

**Also unproven until the first real run**, and listed so nobody mistakes the
green suite for verification: the fixture's `currentReportReason` VALUES are
constructed to the published spec rather than captured, so the exact string form
(half-width vs full-width, spacing, and whether multi-reason joining uses `,`
without a space) is spec-derived; and the real ratio of 180s to item-9s is
unknown.

### Promotion gate, so it is one commit and not a judgement call

Japan becomes `structured_official` when a real run has measured it. Exactly:
dispatch `collect-structured.yml` with `source=edinet_japan`, `dry_run=true`;
read the printed line `N documents read, M extraordinary reports, K reporting
第19条第2項第9号, S usable`; then in ONE commit flip `MARKETS`'s JP entry to
`STRUCTURED_OFFICIAL`, add `edinet_japan` to its `live_sources`, and update
`test_japan_stays_discovery_only_until_a_real_run_measures_it`. If K is
implausibly zero over 7 days, the clause strings differ from the spec and the
matcher is what to fix — not the floor.

**Scheduled, on Tuesday.** `collect-structured.yml` gains `0 4 * * 2`, and the
day is deliberate: Monday already carries BSE at 04:00, the link-hygiene ticket
at 05:30 and the digest at 13:00, and every writer shares the one
`talent-collect` lock in which GitHub keeps a single pending run that a second
scheduled writer can evict. There is deliberately **no minimum-rows floor** of
the kind `bse_india` carries: India's 250-a-week makes a zero provably a
breakage, whereas one clause covering one role across 3,829 filers can genuinely
be quiet, so health is judged on `LAST_RUN["read"]` instead. The honest floor
cannot be set until the first real run measures the rate.

### Where the brief was wrong

* **"Documents are Japanese, often Shift-JIS or in XBRL."** The API's JSON
  metadata is UTF-8, and this collector never touches a document body, so the
  encoding trap does not arise on the stored path at all. Where encoding DOES
  bite is the code list, and there the specific claim is wrong in a way that
  matters: both lists are **cp932, not `shift_jis`** — `shift_jis` raises on
  byte `0xfb` at offset 35,244 of the Japanese list, because cp932 carries the
  NEC/IBM extended characters Japanese company names actually use. Naming the
  narrower codec would crash the run on such a filer. (The sibling decoded
  bodies as `utf-8` with `errors="replace"`, which would have mojibaked them
  silently; it never mattered because it found nothing.)
* **"万/億 magnitude characters."** Real, but not reachable here: no figure is
  stored, so there is nothing for a magnitude character to corrupt. The
  full-width DIGIT problem was the live one, and it was in the clause reference
  rather than in any amount.
* **"Extraordinary reports are the likely home for officer changes — confirm
  it."** Confirmed, but the brief's framing implied a category comparable to
  SEBI's. It is one clause covering one role, with an exemption that removes the
  commonest timing. The honest headline is "Japan types the CEO change", not
  "Japan types officer changes".
* **"MEASURE and report honestly: documents seen in a real recent window."** Not
  possible: the key is a GitHub secret and no authenticated call could be made,
  and the alternative — crawling the viewer — is prohibited by the terms. Stated
  as unmeasured rather than estimated into looking measured.

---

## 2026-07-30 — the page stops disagreeing with itself: sources, city pills, five amounts

Launch-blocker pass over `wordpress-plugin/`. The theme running through all of
it is a page stating a number that the same page contradicts one click later.
Every figure below is reproducible from a command in this repo or a curl against
the live site, and where the brief that started this work was wrong about the
code, it says so.

### The sources page named five of its nine live collectors

`/sources/` printed "not yet reported" for `national_press`, `sec_execcomp` and
`uk_paygap` (confirmed live before the fix: `grep -c "not yet reported"` on the
served page returned 3). Between them those three are most of the database:
`national_press` found 9,305 items on its last run, and `uk_paygap` supplies
4,761 of the United Kingdom's 4,793 rows. The cause was a five-entry
`$by_collector` map typed by hand in `includes/sources.php` beside a nine-entry
`COLLECTOR_BY_SOURCE_NAME` in `source_registry.py`.

Fixed by deriving it. `sources_manifest()` writes a `collector` key onto every
row of `data/sources.json`; `tit_sources_collector_map()` builds the join from
that. A source added to the registry now arrives on the page with its collector
attached.

The four collectors that report health and are NOT sources stay absent, with the
reason recorded in `_NOT_SOURCES` rather than implied by omission:
`archive_sources` and `link_check` maintain the ledger behind the links, `recall`
measures what we miss, `sec_form_d_bulk` backfills a source already listed. That
set is asserted disjoint from the manifest, so it cannot become a hiding place
for a real source nobody wants to write a row for.

### Every city pill returned a different number from the one printed on it

`SELECT city k, COALESCE(country, hq_country) cc ...` in `shortcodes.php` had
three defects at once. Measured against the committed database:

| city | pill printed | click returned | after |
|---|---|---|---|
| London | 19 | 1,339 | 1,339 |
| Manchester | absent | 106 | 106 |
| Edinburgh | absent | 49 | 49 |
| Toronto | 25, US flag | 27 | 27, CA flag |

1. **It grouped by bare `city`** while the pill writes `city=<name>`, which
   `api.php` resolves as `city = %s OR (city IS NULL AND hq_city = %s)`. Almost
   every London row is placed by its employer's head office, and this count
   could not see one of them. Manchester and Edinburgh were missing from a strip
   carrying Seattle (43) and Toronto (27).
2. **It was counted under a bare `is_current = 1`** rather than `{$base}`, the
   only strip on the page that was, so it included the 3,143 routine officer
   filings the table sets aside.
3. **`cc` was non-aggregated under `GROUP BY city`**, so the flag was whichever
   row the engine reached first and MySQL and SQLite need not agree. Toronto
   holds 24 Canadian rows, 2 American and 1 from Hong Kong, and flew a US flag.
   It is the modal country now, ties broken alphabetically.

`tit_city_expr()` and `tit_country_expr()` join the other shared predicates in
`api.php` so the grouping rule has one authority; `/aggregate`'s own `by_city`
had defect 1 and got the same fix. The index-friendly `OR` form stays in the
WHERE clauses for the reason `tit_place_kinds()` already documents.

Still ONE query and still **12 cold, 0 warm**. `tests/php/render_dashboard.php`
now parses every pill out of the rendered markup and asserts its printed count
against the clause `tit_place_kinds()` declares, with two new fixtures for the
shapes that caused the bugs.

### Five funding amounts off by a factor of a million, and the rule behind them

| employer | stored | was | now |
|---|---|---|---|
| Terminal | `$20-million USD` | 20 | 20,000,000 |
| Abaco Technologies | `USD 53 millones` | 53 | 53,000,000 |
| Visibuilt | `25 millioner kroner` | 25 | NULL |
| Serpier | `10,5 mio. kr.` | 105 | NULL |
| Multiverse | `500 millones` | 500 | NULL |

The multiplier vocabulary was English-only, and `\s*` does not match the hyphen
in `$20-million`. But the deeper rule was the denylist: `parse_funding_usd`
refused a currency only when `_NON_USD` recognised the word, so "no foreign
currency I know" read as "US dollars". `kron[ao]r?` does not match "kroner" and
"kr." was in no list at all, so two Danish rounds sat in a column the page
promises holds only amounts a source stated in dollars.

**The test is positive now**: no `$`, `US$` or `USD` in the string, no number. It
costs nothing to be strict. Of 3,097 current rows carrying an amount, **3,094**
name one of the three outright, and the only three that did not were exactly
these three. Verified across the whole corpus: those five rows change and
nothing else moves.

Widening the vocabulary opened a trap that is closed in the same commit.
`USD 1,5 millones` would strip the comma and store fifteen million for one and a
half, because every comma-decimal string used to be refused as foreign before
its number was read. `_read_number()` decides which comma is which by the
ordinary rule. And `mil` now REFUSES rather than falling through to no
multiplier: it is a million in Singapore English (`US$22 mil`, in the 2026-07-29
sweep) and a thousand in Spanish, and twenty-two dollars was wrong under both.

**The five stored rows are NOT corrected.** Three of them need their live
`funding_amount_usd` set to NULL, and until this session no route on the plugin
could write that: `/enrich` ignores an empty field by design, `/correct` cannot
blank a value, and a withdraw-and-republish would remove both rows because a
revision carries the same `content_hash`. `/enrich` now takes an explicit
`clear` array restricted to `tit_clearable_columns()`. Applying it is a queued
writer run and belongs to the owner.

### Four pages had no description, and og:description existed nowhere

The dashboard, `/sources/`, `/recall/` and `/corrections/` shipped with no
`meta[name=description]` (confirmed live: `grep -c` returned 0 on all four). The
brief said the mechanism existed and had merely not been applied; half true. The
`description` mechanism existed on the company and place pages.
**`og:description` existed on none of the six**, so no link to any page of this
product had share-card text. `tit_head_description()` prints both from one
string, truncating at a sentence rather than mid-figure.

### Three more places the copy contradicted the data

- **`tracked since` on all 715 indexable profiles said July 2026**, because it
  was `MIN(captured_at)` and every row was captured when the backfills ran,
  while the same page said "last update 3 months ago". Now
  `MIN(COALESCE(published_date, DATE(captured_at)))`, matching the span note.
- **`/corrections/` captioned a table with `date_i18n('j F Y')`** — today's
  date, whatever today was — over figures measured on 29 July, while a later
  correction had taken the money total from **$124.0bn to $101.4bn** (live
  `/aggregate`, 2026-07-30). The caption prints the date measured; the fall is a
  note with its own dates rather than an overwritten cell.
- **One filter had three names.** Checkbox "Only Updates That Move Headcount",
  chip "Only with a stated headcount", SQL `signal_direction IN (...)`. Only the
  checkbox was right: `headcount` is non-null on **11 of 15,711** rows (0.07%)
  and the control does not read that column. The comment claiming "about 87%"
  said 99.93%. Chip and comment fixed.
- **`/places/` counted 15,711 while the dashboard counted 12,568**, and only the
  dashboard explained itself. One sentence each side.
- **`tit-f-state` rendered 51 bare postal codes.** `tit_state_names()` carries
  all 50 states, DC and the five territories on day one, for the reason
  `tit_country_names()` once failed with 52 of ~200 codes. It rides on a `data-`
  attribute as well as `wp_localize_script` per gotcha 10, costs **2,096 bytes**
  of markup, and that is why the harness byte budget moved 152,000 to 156,000
  with the note saying what bought it.

### The cross-tracker pairing: built, measured, switched off

An employer cutting in one place while hiring in another is the signal only
somebody holding both halves can produce. `includes/cross_tracker.php` reads the
sibling's PUBLIC HTTP API at render time, caches it in a `tit_` transient keyed
on `TIT_VERSION`, retries once on a 5xx, times out at four seconds and caches a
miss short so a sibling that is down cannot make every render wait. No file
imported, no database joined.

It ships DISABLED, and that is a count:

```
our employers                                    7,377
sibling names on /layoffs/v1/companies          20,000 -> 18,648 keys
keys present in both                               559
of those, with a hiring-direction row here           6
```

Reading the six is what settles it. The sibling's own `?company=US Bank` answers
with **Piraeus Bank** for three of its four most recent rows, so a loose rule
publishes a Greek redundancy against a named American bank. Tesla matches
"TRIGO (Tesla)", a contractor. Saint-Gobain matches two subsidiaries. Infosys
and SouthState pair 2024-2025 cuts against July 2026 hires, and the claim is
concurrency. Exactly one pair is near defensible — HSBC, 20,000 cut in the UK on
2026-03-19 against 200 hired in wealth management in July — and that hiring
row's own geography is wrong here (`city=London, country=SG`).

**Zero pairs defensible, one fabricated claim available.** What would change it,
in order: a shared ticker or CIK instead of a name match; a decided subsidiary
rule; a recency window binding both sides; and more than 49 hiring rows in
15,711.

### Where the brief was wrong

- **`finance.yahoo.com` is NOT already blocked.** The brief was corrected
  mid-session to say `_AGGREGATOR_DOMAINS` blocks it by registrable domain.
  There is no `_AGGREGATOR_DOMAINS` in `pipeline/validate.py`. The guard is
  `host in _BLOCKED_SOURCE_HOSTS`, an exact-host `frozenset`, at line 466.
  Proved by running `validate.build_signal` on all three hosts:
  `news.yahoo.com` is rejected by name, `finance.yahoo.com` and
  `sg.finance.yahoo.com` pass the host check.
- **It is three rows, not two**, and blocking the domain would be WRONG for one
  of them. Fetching each URL's `rel=canonical` settles which is which:
  `finance.yahoo.com/small-business/articles/7-eleven-...` canonicalises to
  `cstoredive.com` and `...warsteiner-owner-haus-cramer...` to `just-drinks.com`
  — syndication, and we already read cstoredive.com directly for two other rows.
  But `sg.finance.yahoo.com/news/hsbc-plans-hire-100-ai-...` canonicalises to
  ITSELF: Yahoo Finance Singapore is the publisher of record. A registrable-domain
  block would drop it, which is the `news.crunchbase.com` over-block again. Also
  `finance.yahoo.com` is a registered candidate source in `source_registry.py`
  and appears in the recall gold set. The right rule is the canonical host, not
  the requested one. NOT IMPLEMENTED: `validate.py` was held by another agent.
- **There IS a `php` binary on this machine** (8.5.8). `docs/HANDOVER.md` said
  there was not, so the five harnesses under `tests/php/` had been treated as
  CI-only. They run locally in under two seconds.
- The audit's counts drifted with the data by a row or two throughout (12,566 vs
  12,568 notable; Manchester 108 vs 106). Its diagnoses were otherwise accurate.

### Deliberately not done

- **Not deployed.** The session was told not to push, and `deploy-plugin.yml`
  checks out a ref on GitHub, so a deploy of local commits is impossible without
  one. Version bumped to **1.53.0** in both places; live still serves 1.52.0.
- **The five funding rows are not corrected**, per above.
- **The Toronto city/region/country correction is not run.** `/correct` accepts
  those columns now, which was the blocker; the run itself is a writer and must
  be queued through `drain-writers.yml`, never dispatched.
- **Nothing was armed.** No cron uncommented.
- **Nothing submitted to Search Console.** Neither tracker's sitemap is in
  `robots.txt` or `sitemap_index.xml`, so 748 indexable pages are reachable only
  by internal links. Owner action.

---

## 2026-07-30 — a figure guard that ate records, a cache that does not exist, and the 81 misses

Three jobs, and two of the three briefs turned out to be wrong about the code.
Every number below is reproducible from a command in this repo.

### 1. `validate._NUMBER` glued a magnitude across a line break

`\s` matches a newline, and the magnitude suffix sat behind a bare `\s*` with
nothing after it, so `"28.07.2026\n\nK M Sugar Mills"` tokenised as
`28072026k`. Since `assert_figures_are_sourced` compares two SETS, and every
collector joins its fields with a blank line, the glue lands on the SOURCE side
and a figure that IS verbatim in the source reads as invented — the whole record
discarded, silently. Fixed: the suffix now sits behind horizontal whitespace
only (`_H_SPACE`, every character `\s` matches except the ones that end a line,
so NBSP still counts and CR/LF/FF/VT/U+2028/U+2029 do not).

Measured, `python3 -m analysis.figures.replay`, 15,711 current rows:

| | |
|---|---|
| newline junctions rebuildable exactly | 11,678 |
| junctions where the glue FIRED | **465**, all `sec_execcomp` |
| records those 465 cost | 0 — that body repeats the filing date, so the clean token survives |
| rejections on record, attributable to this rule | **0 of 1,368**, and that is the honest answer |

`raw_text` is not persisted (`measure_city_placement.py` documents the same
limitation) and a rejected candidate leaves a URL in `seen_urls` with no text
and no reason. So the cost on the sources whose bodies we no longer hold is not
knowable, by this script or any other, and the script prints that as a zero
rather than an estimate.

**The brief said this affects `sec_edgar` and `national_press`. It affects
neither.** `sec_edgar.fetch_text` ends with `re.sub(r"\s+", " ", text)` and its
synthetic headline ends in the word "change"; `national_press._plain` collapses
whitespace too and its dateline opens with "(". The only collector whose
`headline\n\nbody` junction can put a digit next to a B, M or K is
`sec_execcomp` — headline ending in a filing date, body opening with the company
name — which is the 465 above. `bse_india` hit the bug first and worked around
it by quoting its filed description; that comment asked for this fix.

### What did NOT ship, because it was built and then measured

The same glue happens INSIDE a line — `"hire 300 by 2027"` -> `300b` — and it is
commoner: 261 sites over 163 stored rows. The obvious fix is `\b` after the
suffix. **It is a regression, and the measurement is why we know.**

| variant | frees | BREAKS |
|---|---|---|
| horizontal space only (shipped) | 0 | **0** |
| + word boundary | 5 | 23 |
| + word boundary + English magnitude fold | 5 | 14 |

The missing boundary is doing multilingual magnitude folding by accident:
`millones`, `millions`, `Millionen`, `miliona`, `millioner` and `millions` all
truncate to `m`, which is exactly what makes them compare equal to the model's
English "million". The 14 rows it breaks are every one a foreign-language
funding round — Multiverse's 500 millones, Proxima Fusion's 411 millions, 5U
AI's 3,2 Millionen. The feed set spans **43 languages** (`data/feeds.csv`), so
doing this on purpose means a magnitude vocabulary in 43 languages, and a
partial vocabulary fails silently and looks like sparse data. Left alone,
pinned by `tests/test_figure_guard.py` so the next person meets the reason
instead of the trap. (Adjacent, unfixed, same class: `£1bn` in a headline does
not match `$1 billion` in a summary, because `bn` matches whole and `billion`
truncates to `b`.)

### 2. The DeepSeek cache the routing was going to hit does not exist

TECHLOG's own "smallest further lever" priced pinned routing at **-$2.84/month
at zero cost to coverage**. Checked against OpenRouter's endpoints API on
2026-07-29:

* `deepseek/deepseek-chat`, the configured `TIT_MODEL`, has three endpoints —
  streamlake, deepinfra/fp4, novita/fp8 — and **not one publishes an
  `input_cache_read` price**. There is no cache on this slug to route to.
* `deepseek/deepseek-chat-v3.1` has four that do, at **~0.5x** (deepinfra
  0.00000013 against 0.00000025 prompt), not the 0.1x DeepSeek's own API
  charges. DeepSeek's first-party endpoint serves neither slug through
  OpenRouter.
* So the saving is a model switch away and worth about half the advertised
  figure. That is a decision about extraction quality, not a routing tweak.
* **The 60% cache rate (131k of 216k) that motivated the lever is not
  reproducible here.** `source_health` holds zero rows with a non-null
  `prompt_tokens`, and `ops_status [2a]` agrees: "No run has recorded a cost
  yet". Wherever it came from, it was not this ledger — and it would have mixed
  both stages anyway, since Gemini's implicit cache on the gate lands in the
  same counter.

Pinned anyway, because it costs nothing and buys three things: the prefix stops
scattering the day a caching endpoint appears, `cached_tokens` becomes
interpretable, and extraction stops being a lottery between an fp4 and an fp8
host. `provider.order = ["deepseek", "streamlake", "novita", "deepinfra"]`,
keyed by model author so no slug is sent to a model that provider does not
serve. **`allow_fallbacks` is true on every request and no code path sets it
false; `only` and `ignore` are never sent** — a pinned provider's outage must
cost the cache, never the run. Field names read from the docs, not guessed; a
misspelled key inside `provider` is accepted and silently ignored, so
`tests/test_provider_routing.py` asserts every key we send is one the schema
documents. No live call was possible (no `OPENROUTER_API_KEY` here), so
`STATS["providers"]` now records which endpoint OpenRouter says served each
call and the first real run settles it. `TIT_PROVIDER_ORDER=off` reverts.

### 3. The rejection audit: none of the 81 misses was ever fetched

`python3 -m analysis.recall.rejection_audit --write`, read-only, writes
`data/recall_rejection_audit.json` beside `data/recall_worklist.json`.

| stage | n | what it means |
|---|---|---|
| `outside_our_history` | **51** | backfill |
| `publisher_not_wired` | 12 | source, researched but not connected |
| `publisher_unknown` | 11 | source, not researched |
| `feed_read_item_missed` | 7 | filter/plumbing |
| `fetched_then_dropped` | **0** | filter |
| `stored_unmatched` | 0 | matcher defect |

**Zero.** An exact-URL lookup against `seen_urls`: no filter in this pipeline has
ever rejected a gold event, so tuning filters would have moved nothing. The
dominant bucket is a third answer nobody asked for — the gold window is
2026-07-01..28, the earliest run of any collector is 2026-07-27, and
`national_press` first ran on **2026-07-29, the day after the measurement it is
being judged by**. The furthest any route reached backwards on the 28th was
2026-07-20 (Google News, `when:7d`). The 9% is a two-day-old tracker measured
against a four-week window.

The actionable part is the 23 sourcing misses, and the sharpest is that **CTech
is still unreadable**: `national_press` exists because CTech broke four Israeli
rounds we missed, and its catalogue row's `rss` column is still empty. Four of
the 81 are CTech articles. Twelve sit on catalogued publishers with no feed
(calcalistech 4, businesswire 2, globenewswire 2, tech.eu, prnewswire, yahoo
finance — three of those are wire services, one connector each for a lot of
coverage); eleven on publishers not in the catalogue (latamlist 2, finsmes,
european-biotechnology, techla.pro, pv-magazine); seven are inside a live route
and a swept publisher, and four of those seven domains have already delivered us
other articles (betakit 6, entrackr 5, wamda 3, exame 2).

VERDICT: a HISTORY problem, not a filter problem and not yet mainly a source
problem. HIGH confidence on the zero (exact-URL lookup), MEDIUM on the split
between the rest, which rests on publication dates and route reach rather than
on a record of what each run saw. Limits printed with the result: no rejection
reason is persisted anywhere, and nothing records the items a feed carried that
a run did not reach — both can only UNDERSTATE the filter side. The one
judgement call (days of RSS backlog) is a parameter, and the report prints 1, 3,
7 and 14 days: at 14 the counts move (history 34, feed-read 14), the ordering
never does, and the zero never does.

Not done here, both in the owner's lane: adding
`data/recall_rejection_audit.json` to `recall.yml`'s committed `paths`, and a
block in `ops_status.py` to surface it.

### What was tried and thrown away

* The first glue measurement joined `headline + summary` with a space and
  reported 189 hits of `"31 B"` — an artefact of a junction the pipeline never
  builds. Glue is now measured one stored field at a time, and the docstring
  says why.
* Counting glue sites "across a newline" over stored fields returns 0 and always
  will: no stored field contains a newline. The number is printed with that
  caveat rather than quietly dropped, and the real newline exposure is measured
  by REBUILDING the `headline\n\nbody` junction for the collectors whose body
  opening is a template over a stored column.
* The backstop route's countries are catalogue country NAMES and the gold set
  uses ISO-2, so the first version never matched. Fixed, and it changes nothing:
  none of the gold set's 29 countries is one of the 21 backstop countries.

---

## 2026-07-30 — Australia has the spine and not the licence; sixty publishers instead

Two jobs. Build the Australian equivalent of the India connector, and widen the
publisher net from research the house already owns. The first ends in a
**refusal**, and the refusal is the more useful result.

### ASX: the taxonomy is there, the permission is not

The India unlock was a jurisdiction's MANDATED disclosure category. Australia
has one, and it is as good as SEBI's. Measured live over the whole window the
API exposes — 2026-06-30 to 2026-07-30, **10,000 announcements**, 400 pages of
25 from
`asx.api.markitdigital.com/asx-research/1.0/markets/announcements?count=25&page=N`
— ASX types every announcement, **142 distinct types**, and the board and
officer ones are:

| type | 30 days |
|---|---|
| `Director Appointment/Resignation` | 105 |
| `Company Secretary Appointment/Resignation` | 48 |
| `CEO/Managing Director - Appointment Resignation` | 46 |
| `Chair Appointment/Resignation` | 33 |
| **distinct announcements across those four** | **192** |

That is ~45 a week, ~2,300 a year, from a market of roughly 2,200 listed
entities. Not thin. Nothing about it is technically hard: the company name, the
ticker, the sector, the type and the date are all fields in the response, so it
would have been an `as_classified` collector spending nothing, exactly like
`bse_india`.

**www.asx.com.au/robots.txt permits it.** The entire file is `User-agent: *` /
`Disallow: /search*` plus a sitemap line, and neither
`asx.api.markitdigital.com` nor `announcements.asx.com.au` serves a robots.txt
at all. That was checked first, as instructed, and it is a green light.

**The terms of use are a red one, twice.** `www.asx.com.au/legals/terms-of-use`:

> Market Announcements are freely available for investors' private and personal
> use only, and cannot be used for any commercial purpose without the express
> written authority of ASX. A commercial purpose is any use other than accessing
> and using the content for your own personal and private decision making.

and, under Prohibited uses, "use any spider, screen scraper, robot, other
similar software or device, or other similar process, to use or access the Site
in any way whatsoever, including monitoring, downloading or copying any content
on the Site (except ... with ASX's prior written consent)". The legacy
interstitial at `/asx/v2/statistics/displayAnnouncement.do` makes a human click
it: "I confirm that any content I access will not be used for any commercial
purpose in the context as explained above". ASX sells this use as ComNews and
ComNews Direct.

This tracker aggregates the information and republishes it on a public
dashboard. That is the licensed use, and we do not hold the licence. **This is
the SmartRecruiters decision again** (`collectors/ats_watchlist.json`): every
endpoint answers 200, and the terms still say no, which is precisely why it is
recorded in code and in the registry rather than being settled by whether a
request works. **NEEDS-OWNER**: one email to ASX Information Services turns
2,300 sourced Australian leadership rows a year into a day's work, with the
measurement above already done.

**The trap the next attempt would otherwise re-find twice.**

1. *There is no announcement page and no `asx.com.au` document URL.* The API's
   `url` field is empty on all 10,000 rows. The PDF is reached from
   `documentKey` at `asx.api.markitdigital.com/asx-research/1.0/file/{key}` — the
   vendor's host, not the exchange's. The legacy `todayAnns.do` page does carry
   an `idsId` per announcement, but only for the current day, and its `idsId` is
   NOT the middle segment of `documentKey` (TERRACOM's Final Director's Interest
   Notice: `idsId=03119949`, `documentKey=2924-03115930-2A1686673`). Today those
   two counters happen to sit 4,019 apart. Deriving one from the other would be
   a guessed identifier inside a stored source URL, which is the AttachLive /
   AttachHis mistake with extra steps.
2. *`Change of Director's Interest Notice` is not an appointment.* It is
   Appendix 3Y under Listing Rule 3.19A — a SITTING director's shareholding
   moving — and at **589 in the same 30 days** it is the largest
   leadership-looking type by a factor of three. The brief named 3X/3Y/3Z as the
   likely spine; 3Y in particular would have trebled the volume with rows that
   are not talent signals at all. The appointments themselves sit under Listing
   Rule 3.16.1, which is what the four types above report.
3. *Appendix 3X and 3Z are duplicates as often as not.* `Initial Director's
   Interest Notice` (120) and `Final Director's Interest Notice` (81) are filed
   BECAUSE of an appointment or a cessation, so on 35 of 152 same-day
   (ticker, date) groups they sit beside the change announcement for the same
   person — TERRACOM filed "Final Director's Interest Notice (M Chadwick)" and
   "Director Resignation (M Chadwick)" 30 minutes apart. Including them would
   have taken the headline count from 385 to look like coverage while storing
   one event twice. 82 groups in 30 days ARE notice-only with no change
   announcement within four days, so excluding them costs real recall; that is
   the honest price of the cleaner unit, and it is written down rather than
   hidden. Moot while the licence stands.

`source_registry.py`'s triage block now carries all of this, and
`tests/test_source_widening.py` asserts the paragraph keeps BOTH halves. A
refusal that keeps the measurement and loses the licence reads to the next
session as a rich source nobody got round to.

Australia stays `discovery_only`. Nothing was added to `collect-structured.yml`,
because there is nothing to schedule.

### Sixty publishers, from research rather than from code

The sibling AI Layoff Tracker's `TRUSTED_DOMAINS` holds **705 distinct domains
(698 registrable)**. It was read READ-ONLY as research: no import, no file
copied, no database touched. An outlet list is a fact about the world; the
no-shared-code ruling is untouched.

**372 of those registrable domains are not swept here.** 116 were taken forward
and probed through `collectors/national_press.py`'s OWN `robots_allows` ->
`fetch` -> `parse` path, so nothing was admitted that the live run cannot read:
robots must permit, >=3 items must parse, the newest must be <=45 days old, and
the drift guard must land on the recorded registrable domain. **63 verified.
60 added. 3 refused, measured.**

| | before | after |
|---|---|---|
| feeds in `data/sources_catalogue.csv` | 593 | **653** |
| country buckets with at least one feed | 139 | **164** |

Twenty-three of the twenty-five new buckets are countries this catalogue could
not reach at all: DR Congo, Republic of the Congo, Gabon, Chad, Burundi,
Central African Republic, South Sudan, Sudan, Kosovo, Lesotho, Eswatini,
Malawi, Madagascar, Cape Verde, Sierra Leone, Guinea, Mali, Benin, Afghanistan,
Tonga, Cook Islands, New Caledonia, Bermuda. The other two are
`Pacific (regional)` (Islands Business) and `East Africa (regional)` (The
EastAfrican), filed at coverage `Regional` on purpose so `dateline()` tells the
model the outlet's base does not place the story rather than filing a Fiji round
under Tonga.

Depth went where the 2026-07-28 recall measured zero: **United Kingdom 3 -> 10**
(it had three feeds for the whole country), Canada 7 -> 15, Germany 8 -> 12,
France 10 -> 12, India 9 -> 11, Ireland 4 -> 7, Spain 4 -> 6, Australia 6 -> 8,
Singapore 5 -> 6, Switzerland 4 -> 5.

### Four things that are less good than the headline number

1. **Not one recall zero-coverage country was newly REACHED.** All 27 of them
   already had at least one feed before today. The recall zeros are not a
   feed-existence problem, so this widening is depth against them and nothing
   more; whether depth is what was missing is unmeasured until the next gold
   set runs.
2. **A feed is not coverage.** None of the 23 new countries is covered in the
   sense `CLAUDE.md` means. They have a connector that fetches and a health
   row; they have produced nothing, and several of them realistically never
   will. They are on the sources page as catalogue CANDIDATES, which is the tier
   that says exactly that, and none of them touched `MARKETS`.
3. **This widens a funnel that is already saturated.** The last real run bought
   all its read-throughs and still deferred 95 gate survivors, so the immediate
   effect of 60 more feeds is more deferrals, not more spend and not
   immediately more rows. The value lands when the read cap or the free
   deterministic close rate rises, not today.
4. **The four countries with no feed at all are still Aruba, Curacao, Kuwait and
   Saint Kitts and Nevis.** The sibling's list reaches none of them either.

### What was refused, with the measurement

- **theage.com.au, brisbanetimes.com.au, watoday.com.au.** All three verified
  green. All three serve the SAME Nine business feed as smh.com.au: measured
  2026-07-30, The Age and Brisbane Times share **20 of 20** headlines with the
  Herald and WAtoday shares **15 of 20**. `national_press` de-duplicates on
  `title_key`, so they would have added nothing to the corpus and three lines to
  the public sources page. Only the Herald is listed, plus The Canberra Times,
  which shares **0 of 20** because ACM is a different owner. Pinned by
  `test_no_syndicated_nine_masthead_was_listed_beside_the_herald`. FAZ, Spiegel,
  Sueddeutsche and Welt were checked the same way and share 0 with each other,
  so all four are listed.
- **53 of the 116 candidates found no readable feed** at all under the paths
  tried (Georgia, Armenia, Belarus, Somalia, Liberia, Seychelles, Comoros,
  Angola, Togo, Burkina Faso, Niger, Gambia, Vanuatu, Solomon Islands, Samoa,
  Guam, Northern Mariana Islands and French Polynesia among them, plus
  news24.com, businesslive.co.za, uol.com.br, corriere.it, publico.pt,
  caixin.com, zawya.com, aleqt.com, swissinfo.ch and interest.co.nz). Those are
  "not found by this pass", not "no feed exists" — a hand-found feed URL for any
  of them is a one-line catalogue addition.

### Two items in the brief that were already done

- **`finance.yahoo.com` needs no blocklist entry.** `_AGGREGATOR_DOMAINS` is
  DERIVED from `_AGGREGATOR_HOSTS` by registrable domain, so `news.yahoo.com`
  already blocks `finance.yahoo.com`, `uk.finance.yahoo.com` and every other
  Yahoo host. Adding it by name would imply the domain rule does not work.
  Checked first: the sibling allows no Yahoo host as editorial either. Asserted
  now rather than re-argued.
- **`news.crunchbase.com` is still in `_EDITORIAL_EXCEPTIONS`** and stays there.
  Asserted.

### One place the brief was wrong about this repo

Feeds are not added to `data/feeds.csv`. That file is a GENERATED export, built
by `build_feeds_export.py` from `data/sources_catalogue.csv`, and its intended
consumer is the sibling tracker — a test fails if it is hand-edited. So the 60
rows went into the catalogue and `feeds.csv` was regenerated, which is also why
the reciprocity here is neat: the sibling's outlet research came in as research,
and 60 more verified feeds go back out to it through a file that already
existed for that purpose.

---

## 2026-07-30 — link hygiene is armed, and the cron is not where it looks like it goes

The ask was to uncomment two crons: `40 3 * * *` in `archive-sources.yml` and
`30 5 * * 1` in `link-check.yml`. **Both jobs are now scheduled on exactly those
times, and neither of those crons exists.** The schedule lives in a new
`schedule-link-hygiene.yml`, which is not a database writer, and it writes a
queue *ticket* instead of starting a run.

### Why a cron in those two files is a job that skips nights silently

Both write `data/talent_intel.db`, so both sit in `talent-collect`, and GitHub
keeps exactly ONE pending run per group. A `schedule:` in a lock-group workflow
is a direct dispatch with a timer on it, and it has two outcomes:

* it evicts whatever was pending — recoverable if that was a ticket, because
  `writer_queue.tick` re-dispatches a displaced ticket with its inputs intact;
* it IS evicted, and then it is not recoverable. It ends `cancelled` with zero
  jobs — no steps, no logs, no annotation — and the dispatch API does not expose
  a run's inputs, so nothing can replay it. `data/writer_queue.json` still holds
  **15 orphans from 2026-07-29**, all closed by one hand-written triage note.

Both workflow headers already said "NEVER DISPATCH THIS DIRECTLY". A cron is a
direct dispatch that fires 365 times a year. So the commented crons were not
uncommented, they were **deleted**, and the headers now explain the refusal —
a `# schedule:` block left in place is an invitation to uncomment it, which is
the wrong fix arrived at by the most natural route available.
`tests/test_link_hygiene_schedule.py` fails if either file grows a cron, or a
commented-out one.

### What the description got wrong, on reading the code

1. **"Both were hand-dispatched and SUCCEEDED under supervision at 02:00Z
   today."** Green, yes — as **dry runs that recorded nothing**. Run
   30507215991: `DRY RUN: 24 of 164 already in Wayback, 140 would need a
   capture. Nothing recorded, nothing captured.` Run 30507217495:
   `##[warning]DRY RUN... dry run: nothing recorded`. Both workflows default
   `dry_run` to true, which is the trap `link-check.yml`'s own header warns
   about, and it caught the owner. The runs that actually prove the write path
   are the **17:0x pair on 2026-07-29** — 30473757174 and 30474293718 — which
   recorded, merged and pushed as `f56164e` and `c18288e`. That matters: it
   means the merge-and-push step is exercised, so arming is not a first
   unattended execution of untested code. It just isn't the pair cited.
2. **The cron-collision list omitted `0 4 * * 1`** — `collect-structured.yml`
   grew a Monday 04:00 BSE India slot in 95e6df1. A 03:40 archive run with
   `timeout-minutes: 60` can still hold the lock at 04:00 on a Monday. Under the
   queue this is latency, not loss, which is the point of moving it there.
3. **"Confirm both workflows follow the merge path, not a copy."** They do, and
   the `cp` in each is the *safe* direction: `cp data/…db "$RUNNER_TEMP/x.db"`
   saves the run's work before the reset, and `merge_db.py` brings it back
   after. `tests/test_workflows.py` already distinguishes these by destination.
   No launch blocker here.
4. **"Send a browser-ish User-Agent to the WP host."** Neither job touches the
   WP host at all — no `wp-json`, no POST, nothing. They talk to publishers and
   to archive.org, both with `national_press.USER_AGENT`, which is browser-ish
   and names us. The ModSecurity/`no-store`/Cloudflare rules do not apply.

### The launch blocker that was real: a 429 read as "not archived"

`check_availability` returned `None` for anything that was not a 200. Measured
2026-07-30 from this machine: `archive.org/wayback/available` answered **429 to
the first request**, and again 20 seconds later. Every consequence points the
same way:

* pass 1 invents a gap that does not exist;
* the phantom misses go to pass 2, spending a bounded capture budget
  re-archiving documents Wayback already holds;
* each attempt increments `archive_attempts`, and at `MAX_ARCHIVE_ATTEMPTS` (5)
  the URL is recorded `unavailable` — which `archive_candidates` treats as
  **terminal**. Five throttled nights would retire capturable documents forever,
  recoverable only by a hand-written UPDATE;
* and it is invisible: `throttled_out` only fired when Save Page Now was
  throttled *too*, so a run blinded in pass 1 reported `ok` next to a healthy
  capture count. The false-healthy shape again.

Fixed: 429/5xx/timeout now return `RATE_LIMITED`, which is neither a hit nor a
miss. Such a URL is skipped for the night, spends no capture, touches no attempt
counter, and stays in the gap. A run whose free pass went mostly unanswered is
`degraded` with a named warning. The free pass is also paced at
`DEFAULT_AVAIL_GAP = 0.5s` — it costs no money, which is not the same as being
welcome at any rate we like — and a test pins that 600 × (0.5 + 1.0s latency)
plus the 40 × 6s capture budget still fits inside the 1500s deadline, because an
over-long pass 1 would starve pass 2 of every capture while staying green.

`link_check.probe` now retries **once** on a transport failure or a 5xx. Not for
the rot rate — neither state is rot — but for the recheck window: one
observation costs that URL its whole 30-day rotation, so a publisher's bad
afternoon buys a month of not knowing. Never for a 4xx: a 429 is in
`WALLED_CODES` and retrying it would be answering "slow down" with "no".

### Numbers

| | |
|---|---|
| distinct source URLs | 12,970 |
| in the nightly pass's scope (4 publisher collectors) | 235 — **1.8%** |
| the other 98.2% | SEC (3,797 + 2,998 + 1,170 + 9) and GOV.UK (4,761), kept by their own publishers |
| archived now | 72 (48 free + 24 captured), 69 pending, 0 unavailable |
| free-pass hit rate, publisher tail | 48/141 = **34%** (17:12Z), 24/164 = **15%** (02:02Z) |
| checked now | 150/12,970, 0 rotted, 1 `error` (a 454 from techsavvy.media) |
| publisher-tail growth | 34 -> 78 -> 123 distinct URLs/day |
| model spend added | **$0.00** — asserted in two test files |

**What the cap costs, stated where the number is printed.** `1.8%` is the
ceiling this schedule can reach, so the `[2c]` coverage percentage will climb to
roughly there and stop. That is not a stall, and `ops_status.py` now says so on
the line below the percentage, with the scope read out of the workflow rather
than hard-coded. Separately: at 40 captures/night against ~123 new tail URLs/day
the nightly budget does **not** keep up with ingest, and raising `spn_max` makes
it worse, not better. Widening the collector default is the lever; the budget
is not.

`link_check` at 150 URLs/week is a **sample, not a sweep** — 7,800 checks/year
against a corpus that grew 9,347 URLs on 2026-07-28 alone. Left as measured
rather than retuned; `[2c]` prints `checked N/12,970` so the honesty is on the
page.

### Also

* `writer_queue.py enqueue --if-absent` (opt-in, so two retractions of two rows
  never collapse into one). Without it a nightly slot behind a long backfill
  leaves a ticket per night, each aging past `STUCK_AFTER_HOURS` and reporting
  the same single fact as "the lock is starved" once a night.
* The scheduler re-derives its ticket on top of `origin/main` after a rejected
  push rather than rebasing a JSON diff — the `merge_db` lesson one file along.
  `--if-absent` re-evaluated against the fresh queue makes that idempotent.
* It must never contain the string `talent_intel.db`: `test_every_database_writer_shares_one_lock`
  finds writers by raw-text search and would then demand this workflow join the
  very group it has to stay out of. Asserted.
* `staleness.py` leashes: `archive_sources` 2400 -> **54** (two nights plus the
  queue's worst-case wait), `link_check` 2400 -> **180** (the weekly shape
  `bse_india` already uses). The 200 both files suggested is eight missed nights
  for a daily job and only one missed Monday for a weekly one — one number could
  not be right for both.
* `ops_status.py [2c]` now derives and prints the arming state, and goes **red**
  if either writer ever grows a cron.

Suite 1,714 -> 1,782 (+68). One unrelated failure,
`test_form_d_correction.py::test_the_correction_route_writes_those_two_columns_and_nothing_else`,
is another agent's uncommitted edit to `wordpress-plugin/.../includes/api.php`:
a new doc comment containing `normalised_headline` trips that test's substring
allowlist check on the bare word `headline`. Passes against the committed file;
left alone, as that file is not this change's.

---

## 2026-07-30 — the read-through gets its own model, and $5 does not cover it

The owner asked for a frontier model on every read-through inside ~$5/month.
The plumbing is now built for it. **The budget is not met, and the number is
below** — say $13.61/month, not "about five".

### The diagnosis, which is the whole design

One model call was doing two jobs on ~3,100 input / ~400 output tokens.
EXTRACTION is pattern-matching: the employer, the amount, the stage, the place
and the role are all IN the text, and `deepseek/deepseek-chat` lifts them at the
measured $0.00128 a call. The READ-THROUGH is judgement: what a signal means for
hiring in a named place is NOT in the text. The quality A/B that `classify.py`
said had not been run has now been run (`ab_models.py --readthrough`, workflow
run 30506952969) and deepseek RESTATED the headline where the Claude models
wrote something a recruiter could act on.

Upgrading the fused call was the obvious move and the expensive one: ~2,476 of
its ~3,100 input tokens are `SCHEMA_HINT`, so a frontier rate gets paid on the
storage vocabulary the judgement never reads — **$0.0102 a record, $36.72/month
at 3,600 records.** So the call is split. Extraction keeps its model and its
prompt byte for byte; the read-through moves to `TIT_READ_MODEL` (default
`anthropic/claude-sonnet-5`) with its own small prompt in `pipeline/prompts.py`.
Per record that is **5.2x cheaper than the naive upgrade**.

`TIT_MODEL` and `TIT_GATE_MODEL` mean exactly what they meant.
`TIT_READ_MODEL=off` restores the fused behaviour in one line.

### The small prompt, and what it refuses to carry

Measured over all 4,023 current rows from model-path collectors: **median 1,739
characters, p90 1,819, max 2,028**, of which 1,193 is the stable prefix. The
teaser is capped at 500 characters because extraction already lifted every field
we store, so a longer window buys tokens rather than judgement.

Absent on purpose, each for a reason somebody already paid for: `SCHEMA_HINT`
(the whole saving); `headquarters_city`/`headquarters_country`, which are the
model's own knowledge of where a company sits and would place an unplaced
record; and the publisher line, because a writer handed the outlet files every
story in the outlet's home town.

### The rules still bind, three ways

STRUCTURALLY — the writer sees the headline, a teaser and the extracted facts, so
it has nothing to invent a place from but its own memory. DETERMINISTICALLY —
every figure and every gazetteer place in the returned sentence is checked
against the source text and the extracted fields, with word multipliers folded
so "$71M" and "71 million" are one figure rather than a false refusal, and with
place frames read rather than bare names so "Reading the announcement" and
"reports to Charlotte Jones" are not place claims. BY PROMPT for claim-level
grounding, which no regex can check and which is labelled as prompt-enforced
rather than claimed as verified. Confidence needs no new guard: the call returns
exactly one key, so there is no tier for it to promote, and `infer_confidence`
still caps on the source host. `validate` is untouched — it still discards any
record whose figures are not verbatim in `raw_text`.

### Failure handling: the whole record defers

Extraction succeeding while interpretation fails **defers the whole record**
(`ReadThroughUnavailable`, a `Throttled`). Storing a blank was refused because
the guard that would have to be weakened — `validate` requiring a non-empty
`talent_readthrough` — is precisely the one keeping blank differentiators off
the page.

A deferred record is not lost (its URL is deliberately not marked seen, so the
next run retries it inside a recency window measured in days), not silent (the
DEFER line names the reason, `STATS` counts `read_unavailable` and
`read_ungrounded` apart, the run log prints both beside the model that wrote the
prose, and the health row's `detail` carries them), and not free — the extraction
call was already paid for, and `read_unavailable` beside `full_calls` is where
that waste shows up. Because these deferrals feed `mostly_throttled`, a run
where interpretation is broken throughout reports `degraded` and `ops_status`
exits 2 for a human.

### The batch API: half price, a day late, flag off

OpenRouter runs an asynchronous batch API (`POST /api/beta/batches`) and prices
the batch variant at exactly half the sync rate — read off its own `/models`
endpoint, not assumed: `anthropic/claude-sonnet-5` is $2.00/$10.00 per M today
and `anthropic/claude-sonnet-5:batch` is $1.00/$5.00. Going through OpenRouter
rather than Anthropic directly is what makes it maintainable: same key, same 402
handling, same usage accounting, so `spend.py` still sees every cent.

The completion window is 24h, so **batching breaks same-run publishing**: one run
submits, a later run collects, and at twice-daily collection a story reaches the
page 12-24h after it was read. Nothing is lost; freshness is the price, and
freshness is what this product sells. Hence `TIT_READ_BATCH` defaults to off.
The flag adds two calls outside the candidate loop and changes nothing inside it.
One asymmetry worth knowing: a batch's cost lands on the health row of the run
that HARVESTED it, not the one that submitted it.

### Caching: nothing is claimed

The stable prefix is 1,193 characters, ~272 tokens. Sonnet 5's minimum cacheable
prefix is 1,024 tokens and Haiku 4.5's is 4,096, so **this prompt does not cache
and no saving is claimed for it.** A prefix under the floor does not error, it
silently does not cache — which is exactly how a saving gets claimed that was
never possible. The item text still goes last so the shape is right if the
prompt ever grows past the floor.

### The measurement table

Prices are live from OpenRouter's `/models` endpoint (2026-07-30). Token counts
are **derived, not provider-reported**: there is no `OPENROUTER_API_KEY` in the
session that built this, so no call was made and no `usage` block was read. The
character counts are exact; tokens come from this repo's own calibration
(`SCHEMA_HINT` = 10,877 chars = 2,476 tokens = 4.393 chars/token) with a 1.3x
pessimistic multiplier for Claude's heavier tokenizer. **538 in / 90 out** is
therefore a conservative projection of a p90 prompt, and the conclusion below
does not change at the un-multiplied 414 tokens either.

| read-through | $/read | $/month @3,600 | all-in @1,800 | all-in @3,600 |
|---|---|---|---|---|
| `deepseek/deepseek-chat` (fused, today) | — | — | $4.19 | $6.50 |
| `claude-sonnet-5` sync **(shipped default)** | $0.001976 | $7.11 | $7.75 | **$13.61** |
| `claude-sonnet-5:batch` | $0.000988 | $3.56 | $5.97 | $10.05 |
| `claude-haiku-4.5` sync | $0.000988 | $3.56 | $5.97 | $10.05 |
| `claude-haiku-4.5:batch` | $0.000494 | $1.78 | $5.08 | $8.28 |

All-in = gate + extraction + read-through, on the repo's own measured per-item
figures (gate $0.00003 x 1,050 screened/run x 60 runs = $1.89/month; extraction
$0.00128 x reads). Sonnet 5 is on introductory pricing until 2026-08-31; at the
standard $3/$15 the shipped default becomes $0.002964/read, $10.67/month at
3,600.

### $5 is not reached, and the honest number

**At 3,600 reads/month nothing lands under $5 — not even the read-through we
already had.** Gate plus extraction alone are $6.50 before a single
interpretation is bought. The frontier read-through is not what breaks the
budget; the budget was already broken at that read volume.

What $5 all-in actually buys, holding gate and extraction at their measured
prices:

| read-through | reads/month within $5 | per run |
|---|---|---|
| `deepseek` (fused, today) | 2,430 | 40 |
| `claude-sonnet-5` sync | 955 | 16 |
| `claude-sonnet-5:batch` | 1,371 | 23 |
| `claude-haiku-4.5:batch` | 1,753 | 29 |

Measured steady demand is 30-60 reads/run. So the shipped default fits $5 at
roughly half the low end of demand.

**The smallest further lever, and it is not the model.** Extraction is the
largest single line ($4.61/month at 3,600) and 2,476 of its 3,100 input tokens
are a byte-stable prefix that DeepSeek bills at 0.1x on a cache hit. The last
real run measured only 60% of prompt tokens served from cache (131k of 216k),
because OpenRouter routes a model across providers and a prefix scattered across
providers does not hit. Pinning that routing takes extraction from $0.00128 to
~$0.00049 a call — **-$2.84/month at 3,600 reads, at zero cost to coverage or
quality.** It still does not reach $5 with a frontier read-through; it is simply
the cheapest $2.84 available, and it should be spent before read volume is cut.

### `spend.py`: the allowance the owner would need

`MONTHLY_ALLOWANCE_USD` is left at 10.0 and `STOP_AT_FRACTION` at 0.9 — the
budget is policy and belongs to the owner. What the number would need to be:

| configuration | projected | allowance to set |
|---|---|---|
| shipped default, 30 reads/run | $7.75 | **$9** |
| shipped default, 60 reads/run | $13.61 | **$16** |
| `TIT_READ_BATCH=1`, 60 reads/run | $10.05 | **$12** |
| Haiku 4.5 batched, 60 reads/run | $8.28 | **$10** |

The allowance has to exceed the projection by 1/0.9, because the guard stops
collection at 90% of it. At today's $10 the shipped default would hard-stop
mid-month at 60 reads/run — which is the guard working, not failing.

### What was refused

The extraction prompt was not touched. The read-through was not allowed to see
the employer's headquarters or the publisher. No saving was claimed for prompt
caching. The batch path was not made the default, and its 24-hour latency is
printed by the run rather than buried in a comment. And $5 was not reported as
met by rounding a $13.61 projection down to a target.

---

## 2026-07-30 — the city gap: 93.8% of rows had no place, and the vocabulary was why

Measured, read-only, before anything was written: 969 of 15,711 current rows
carried a city, in 25 distinct cities. The assumption going in was that the
extractor was not lifting the city out of the text. The measurement says the
ceiling was one layer lower.

### What was actually broken

`normalize_city` knew 45 aliases across 26 markets. The house rule is
"normalise through a fixed vocabulary or be dropped", so Tel Aviv, Dubai, Sao
Paulo, Seoul, Lagos, Nairobi and Jakarta were places the product could not
report **even when a source stated them plainly**. Nothing errored; the column
came out NULL and the page said "location not stated". The gazetteer now holds
418 aliases -> 338 cities across 105 countries, with three invariants pinned by
tests: one region per country (`validate._region_for_country` scans the table,
so a disagreement is a dictionary-order accident), every country code nameable,
and no city name in two countries.

That last rule is the interesting one. Cambridge, Birmingham, Newcastle and San
Jose are **deliberately absent bare** and reachable only as "Cambridge, MA" /
"Cambridge, UK", because a bare "Cambridge-based" cannot be placed without
inventing a country. Same-country collisions (Portland OR/ME, Columbus OH/GA)
are in — the country is right either way — and stay out of `_CITY_STATE`, where
guessing between them would be visibly wrong. `vocab.place_qualifier_country`
reads the source's own qualifier, which is what makes "London, Ontario" stop
being London.

### The scanner, and the rule it had to be taught first

`cheap_extract.stated_city()` reads six phrasings that name a place outright:
`<City>-based`, `based in <City>`, `headquartered in <City>`,
`<City>-headquartered`, `opens a <City> office`, `its <City> office`. It fills a
NULL city on the funding, hiring and leadership closers, never overrides one,
and never overrides a country the prefix already sourced.

`national_press.dateline()` folds the PUBLISHER's seat into `raw_text` on
purpose, in the exact shape `(Outlet: The Recursive, based in Sofia, Bulgaria —
a hint, not a stated fact.)`. A scanner reading that would file every story a
Sofia outlet carried in Sofia and turn a sourced claim into an invented one.
Hint spans are blanked before anything reads them, offsets preserved so the
story's own "based in" still lines up. Same for `classify`'s "Published by:"
line. Both pinned.

The funding sweep's four tightenings, translated: a place INSIDE a name
declines ("Berlin Packaging-based" resolves to nothing because "Packaging" is
what touches the hyphen); `-based` is not a place frame (AI-based, cloud-based,
faith-based, US-based, Israeli-based); a contradicted qualifier declines
(Dublin/Ohio, Melbourne/Florida, Athens/Georgia, Manchester/New Hampshire,
Perth/Scotland are all real and all would have been wrong); and a city
belonging to someone else is skipped, not stored ("led by London-based Index"
states London about the INVESTOR), while two different cities decline outright.

### The read-through prompt

The `city` field now states the no-inference rule explicitly and names
`headquarters_city` as the place for anything the model merely knows.
SCHEMA_HINT goes 2,436 -> 2,476 tokens, **+40 (1.6%)**, prefix shape untouched
(byte-stable prefix, item text last — lever 4 below). At the measured
$0.00128 / 3,100-token read that is +$0.0000165 per read: +$0.06/month at 60
reads a run, +$0.20/month at the 200 cap.

### The backfill number, and why it is small

`measure_city_placement.py` runs the scanner over the committed database
read-only. **7 rows**, adding Munich, Palo Alto, Rome, Sao Paulo and Vilnius.
Not 3,000.

The reason is worth writing down because it changes what a backfill can be:
**`raw_text` is not persisted.** The pipeline reads headline + teaser,
classifies, and stores the RESULT. What survives is `headline`, `summary` and
`talent_readthrough`, so the sentence that carried the place is usually gone.
And the 14,742 unplaced rows are not news: 4,761 uk_paygap, 3,910
sec_execcomp, 3,476 sec_edgar, 2,363 sec_form_d_bulk, against 226 from every
news collector combined. Those filings never contained an English "X-based"
sentence to lift.

A third pass including `talent_readthrough` finds 17 rows and is **printed with
a refusal beside it**. Its matches read "the Houston-based food and beverage
giant" and "a real estate firm based in San Francisco" — the model's own
knowledge of where Sysco and Prologis are, not anything the 8-K said. Storing
those would be exactly the inference this product may not make, so the script
labels the pass NOT SOURCED and excludes it from the total.

Precision check against the 969 rows a model already placed: 1 agreement, 1
disagreement, 967 declines. The disagreement is instructive — "Ramp fully
launches in Canada alongside new Toronto office" stored Toronto (where the
roles are) while the scanner read "New York-based Ramp" (where the company is).
`extract()` already declines any item `prefilter.site_event_term` fires on, so
that class cannot reach storage through the cheap path; the standalone helper
says so in its docstring.

### The bug the measurement found on the way

`ats_boards.place_key` split a location on commas and tried every part as a
country, so a two-letter US state code resolved to whichever country shares it:
"Peoria, IL" -> Israel, "San Jose, CA" -> Canada, "Cambridge, MA" -> Morocco,
"Boise, ID" -> Indonesia, "Wilmington, DE" -> Germany. Fixed by trying the
WHOLE string before splitting (so the board's own "London, Ontario" survives),
by reading a two-letter state as a state, and by falling back to the country
when a qualifier contradicts the city ("Paris, TX" is not Paris). 10,357 of the
17,956 postings in the committed board state currently carry a country key
rather than a city; the next boards run is where that becomes visible.

### What was refused

Nothing infers a place. The outlet's base is never written to a record; a
country never implies a city; a company's known headquarters stays in the
separate `hq_*` columns and is never merged into `city`. The read-through pass
above was measured and left unstored for that reason, and the two legacy
Toronto/US rows were left alone — a correction is `store.revise()` work the
owner queues, not something a vocabulary change should do silently.

---

## 2026-07-30 — cost levers, second pass: every qualified candidate gets read

The first pass (below, "the cost levers") made looking cheap; this one makes
reading complete. The owner authorized raising the read cap on 2026-07-30, and
the levers around it exist so that raise buys coverage rather than a bill.
Measured facts these changes stand on: gate ≈ $0.00003/item, read-through
≈ $0.00128/item (3,100 in / 400 out), and the last real run bought all 60 of
its reads, stored 34 rows, and budget-deferred 95 gate survivors.

**Read only what can store (`validate.precheck`).** Every rejection
build_signal can reach from the raw item alone — no source URL, an aggregator
or job-board link, a bare domain, an empty body, a filing that ANNOUNCES a
workforce reduction — now fires in run_collect before the gate, with the same
messages. build_signal still calls it first, so backfills and corrections
cannot route around it, and a test table pins the two ends to identical
verdicts. Same rows stored; only WHEN the money is spent moved. The waste
that remains is now printed every run: `reads bought vs rows stored` beside
the token accounting, fed by `classify.STATS["read_stored"]` at store time —
the 60-bought/34-stored gap was invisible until it had a number.

**Leadership joins the deterministic extractor.** "<Employer> Appoints
<Person> as <C-title>" closes for $0 under the funding design: precision over
recall, DECLINE on any ambiguity, `reported` confidence, EVIDENCE_NOTE on the
row, zero exemptions from validate -> store. The funding sweep's four
tightenings translate one for one: a country/city employer span declines
(government stories), a role word in the person span declines ("Former Google
Executive Jane Doe" — where the description ends is a model's job), Title
Case trusts only a one-token employer and a two-token person, and the
stolen-detail lesson becomes stated start dates and interim arrangements —
facts the record cannot carry, so they decline the item. Any mention of a cut
declines outright: the subject-race heuristic keeps such stories FOR THE
MODEL, and a $0 close gets no benefit of the doubt.

**The gate default is `google/gemini-2.5-flash-lite`** (env
`TIT_GATE_MODEL`), citing the repo's own A/B: about half the incumbent's gate
price, and every disagreement was the challenger correcting the incumbent's
false rejection of a real funding round. The read-through model is explicitly
untouched — prompt changes and model switches there stay gated behind
`ab_models.py --readthrough`, which has not been run.

**READTHROUGH_CAP default 60 -> 200** (env `TIT_READTHROUGH_CAP`), the
owner's 2026-07-30 call recorded in the comment. 200 bounds one run at ~$0.26
of reads; it was never the monthly guarantee and still is not — spend.py runs
first on every collect job and hard-stops at 90% of the allowance, and the
OpenRouter key's own cap sits behind it. Projected month at the new defaults,
from the measured per-item figures: gates ~1,050 screened x 2/day x 30 x
$0.00003 ≈ $1.9; reads at the measured steady demand (~60-155 gate survivors
a run, less the deterministic closes) ≈ $2.3-4.6 at 30-60 reads/run bought,
with the theoretical at-cap ceiling $15.4 that spend.py exists to make
unreachable. Budget-deferred logging is unchanged, so the day the cap binds
again is a printed number, not a guess.

---

## 2026-07-29 — the stale employer keys, and the merge that could not be a rule

Plugin 1.47.0 and `correct_company_key.py`. Closes the correction the sitemap
entry below left owed, and the three slug collisions `includes/company.php`
refuses to serve.

### Deriving the worklist found two employers nobody had named

The paragraph left in HANDOVER named six employers, mangled by the `\b`
suffix strip. The three collision pairs made nine. The script takes its
worklist by asking a different question — **every live row whose stored
`company_key` differs from `vocab.company_key(row.company)`** — and that
returns **eleven employers and 38 rows**:

| stored key | corrected to | rows | why |
|---|---|---|---|
| `-operative group` | `co-operative group` | 9 | `\bco\b` ate the "co" |
| `the midcounties -operative` | `the midcounties co-operative` | 9 | same |
| `central england -operative` | `central england co-operative` | 8 | same |
| `-diagnostics` | `co-diagnostics` | 2 | same |
| `associated banc-` | `associated banc-corp` | 1 | same |
| `overlay alpha -gp` | `overlay alpha co-gp` | 1 | same |
| `barking havering & redbridge…` | `barking havering and redbridge…` | 4 | merge |
| `perma-fix environmental services` | `perma fix environmental services` | 1 | merge |
| `daré bioscience` | `dare bioscience` | 1 | merge |
| `crossamerica partners lp` | `crossamerica partners` | 1 | **`lp` joined the suffix list later** |
| `peace coffee pbc` | `peace coffee` | 1 | **`pbc` joined the suffix list later** |

The last two were not in anyone's list. They are the same defect from a
different direction: `company_key` is computed once and stored, so *every*
change to it leaves the rows behind it spelled the old way, and the ones nobody
wrote down are exactly the ones a hand-written script misses. Deriving the
worklist also means the next change to that function needs no new script.

### Why the merge is a list of three and not a rule

Three employers were recorded twice under keys differing only in punctuation,
because the filer spells them two ways: EDGAR's company index writes
`PERMA FIX` where the 8-K cover page writes `Perma-Fix`, and the GOV.UK pay-gap
service holds one NHS trust under two employer ids (15028 to 2022, 22115 from
2023), once with `&` and once with `and`. Both spellings claim one profile URL,
so neither was published.

The rule-shaped fix is obvious and was measured before it was rejected: make
`company_key` fold exactly what the slug folds — accents, `&` to `and`,
punctuation to a separator — so two names that produce one URL cannot produce
two keys. Over the 7,788 distinct stored names:

| folding | keys changed | employers merged |
|---|---|---|
| accents | 10 | 1 |
| `&` to `and` | 141 | 1 |
| hyphen to space | 124 | 1 |
| **all three** | **274 (624 rows)** | **3** |

274 keys re-spelled and 624 rows withdrawn and republished, to merge three
employers. And it contradicts the fix directly above it: folding hyphens to
spaces feeds "co" back to the suffix strip and mangles CO-OPERATIVE GROUP a
second way. So `vocab.EMPLOYER_KEY_ALIASES` states the three merges, one line
each, with the filer id that justifies it. The surviving spelling in each pair
is the one whose space-for-hyphen form is already the canonical slug, so the
fast path in `tit_company_rows()` finds it in SQL without touching the index.

**A list has to be added to, and that is what `ops_status.py [1c]` is for.** It
names any stored key that is no longer current with `vocab.py`, and any two keys
claiming one URL that are not merged, distinguishing "waiting on a human to
choose" from "merged, waiting on the correction to run". Before it, an unmerged
pair was a page that silently never appeared.

### The three URLs that moved, and why they still resolve

Correcting a key moves the profile slug, and three of these employers are over
the publishing threshold, so three URLs in the live sitemap changed:

    /company/operative-group/            -> /company/co-operative-group/
    /company/the-midcounties-operative/  -> /company/the-midcounties-co-operative/
    /company/central-england-operative/  -> /company/central-england-co-operative/

The old three had to 301 rather than 404. **The old URL is not lost
information: it is stored.** A correction appends a revision and the old row
survives at `is_current = 0` still carrying the old key, so
`tit_company_moved_slugs()` joins each superseded revision to the current
revision of the same signal, and step 3 of `tit_company_rows()` resolves the old
slug to the key that signal holds now. The canonical comparison already in
`tit_company_template()` then issues the 301, so there is no second redirect
rule to keep in step with the first — and it is a property of revisions rather
than a redirect list, so it covers every key correction there will ever be.

Both slug forms of the old key are indexed, because both were live URLs: the key
`-operative group` canonicalises to `operative-group` (the leading hyphen is
trimmed) and legacy-slugs to `-operative-group`, and the sitemap published the
first. Two refusals, matching the collision map beside it: a slug a **current**
key holds is never redirected away from (a merge leaves both spellings on one
slug and the survivor still serves it), and a slug two corrections both claim is
dropped rather than guessed.

### Proved by running it, because reading it would not have settled it

`tests/php/route_company_slugs.php` stubs WordPress, backs `$wpdb` with SQLite
so the JOIN executes instead of being matched as text, and asserts the routing
in three phases in three processes (the index memoises in a static): before the
correction, after it, and under an ambiguous move. Deleting the step-3 lookup
fails six assertions. This is the same lesson as the sitemap entry below — a
twenty-URL hand sample passed while 22 of 712 URLs were broken — one level up:
whether a URL 301s or 404s is a behaviour across a state change, and no reading
of the source settles it.

One thing the harness cannot catch, so it is written into the code: the SQL
aliases are `prev` and `live`, not `old` and `new`. SQLite accepts either;
MySQL has reserved both at one version or another for row aliases, and an
unquoted reserved word there is a parse error that takes out every company page
at once.

### What the correction does and does not touch

Shape follows `correct_sec_pillar.py`: dry run by default, `store.revise()` so
the original survives, retract before republish, one row at a time and committed
per row, both of the site's duplicate guards mirrored so a row it would refuse
is withdrawn with a reason instead of vanishing. **Two values move and no
others** — `company_key` and the `content_hash` it feeds. `materiality` is
deliberately *not* recomputed the way the pillar pass recomputes it, because
`compute_materiality` does not read the key, so recomputing could only introduce
a difference. Nothing is deleted, including the orphaned `employer_identity`
entry, which is copied onto the new key rather than moved.

The `--force` guard refuses a worklist above 5% of live rows. Measured here:
38 of 15,650, 0.24%. The one legitimate way to exceed it is a real edit to the
suffix vocabulary, and that deserves a human saying so out loud before hundreds
of rows are withdrawn from the site.

---

## 2026-07-29 — the sitemap was a list of promises and 22 of them were false

Plugin 1.45.5 and 1.46.0, both a consequence of the same review note.

### What a twenty-URL sample could not find

The company sitemap shipped 712 URLs. Twenty were fetched by hand and all
twenty passed. **Twenty-two were broken**, and a reviewer hit one on their fifth
random pick.

An employer key containing "&" was written into `<loc>` as the XML entity
`&#038;`. That is correct XML. The problem is that consumers disagree about it:

| form in the URL | result, measured |
|---|---|
| `%26` percent-encoded | 404, does not survive the WordPress rewrite |
| `&#038;` XML entity, unresolved | 301 to `/company/b-&/` then 404 |
| `&` literal, entity resolved | 200 |

The sample resolved the entity, so it only ever exercised the row that works.
**The sample and the bug were the same shape**, which is the only reason twenty
passes meant nothing. That pair of outcomes is exactly the "Page with redirect"
plus "Not found (404)" report the owner has already had to forward once from
Search Console.

`check_sitemap_urls.py` now fetches EVERY URL in the file with redirects
disabled and asserts 200, no redirect hop, no noindex, and no decoder-dependent
character in the RAW `<loc>` text. It reads the raw text rather than the parsed
tree, because the parsed tree is what hid this. 713 requests take about a
minute. **A sitemap is a list of promises and the only check that verifies a
list of promises is checking all of them.**

It also retries a 5xx three times with a long backoff. A first version retried
after 1.5s and 3s, reported one URL as a hard 504, and that URL answered 200 in
2.4s a minute later: all three attempts had landed inside one bad window, so the
checker was measuring its own impatience. Shared hosting 5xxes at random
(gotcha 8) and a checker that cries wolf teaches its reader to skim.

### 1.45.5, immediately: stop advertising them

The 22 were withheld from the sitemap and made noindex, because their URL was
about to change and asking a crawler to index a URL you are replacing is the
same defect from the other side. Pages stayed reachable and stayed linked.
Sitemap 712 -> 690, and 690/690 verified clean.

### 1.46.0: the slug transliterates

An ampersand has no safe encoding, so it stops being encoded. The slug is now
`remove_accents`, `&` -> `and`, everything outside `[a-z0-9]` -> `-`:
`b-and-m-retail`, `atkinsrealis-uk`. 167 of 7,301 keys change and **all 162 that
had no publishable URL get one**. Sitemap 690 -> **713, all verified clean**.

- **No live link breaks.** The lookup is two steps: the pre-1.46 comparison
  exactly as it was (which resolves every URL that has ever worked here), then a
  small index for the canonical forms SQL cannot express. The old URL 301s to
  the canonical one, so no employer is indexable at two addresses.
- **The index holds only the 167 keys whose two forms differ**, so the common
  path is one indexed query touching no map. A 7,301-entry array behind every
  request would have been a quarter of a megabyte.
- **Collisions are refused, not resolved.** Three canonical slugs are claimed by
  two keys each, and all three pairs are one employer recorded twice
  ("perma-fix"/"perma fix", "daré bioscience"/"dare bioscience", one NHS trust
  filed with "&" and with "and"). Neither side is served under the shared URL
  and neither is published. The fix is a merge in employer identity. None is
  over the publishing threshold.
- Two of those pairs also SHADOW: one key's canonical slug is another key's
  legacy slug. Checked explicitly; they are the same two duplicate pairs, so the
  collision rule already covers them and no third employer is affected.

### The truncated key, and its cause

`company_key` used `\b(inc|llc|ltd|...|co|...)\b` to strip legal suffixes, and a
hyphen is a word boundary, so `\bco\b` matched the "co" inside "co-operative".
**CO-OPERATIVE GROUP LIMITED was stored as `-operative group`.** Six real
employers were mangled: also ASSOCIATED BANC-CORP (`associated banc-`),
CO-DIAGNOSTICS (`-diagnostics`), two more co-operatives, and Overlay Alpha
Co-GP. A lookaround now requires a whole space-delimited token; measured across
7,770 distinct stored names, the key changes for those six and nothing else.

**Rows already stored keep the mangled key.** `company_key` feeds
`content_hash`, so a new signal for one of those six will not dedupe against the
old rows until a correction pass rewrites them through `store.revise()`. That is
a queued writer job and was deliberately not done in the same commit.
## 2026-07-29 — backfills in bounded slices, and a scope guard that reads the filing

Two fixes for two things that were true all day: a backfill could hold the only
writer lock for six hours, and a page promising it publishes no layoffs was
publishing seven.

### The 350-minute lock hold, fixed by finishing

`backfill-gdelt-2026` took the `talent-collect` lock at 04:59 UTC, ran 350
minutes, hit its own `timeout-minutes: 350` and was **cancelled** — so its
commit step, guarded by `if: !cancelled()`, was **skipped**. Six hours of
collection existed only on a runner that was then deleted, and every correction
queued behind it waited the whole time.

Priority ordering and starvation reporting both landed earlier the same day and
neither could have helped. **Priority decides who goes next and cannot preempt
a running job**; saying the lock has been held for two hours does not hand it
back. The only thing that bounds a lock hold is a job that finishes.

So a backfill is now a **chain of short runs**. A run takes one slice, commits
it, and appends a ticket for the next slice to `data/writer_queue.json` **in
the same commit**; `drain-writers.yml` dispatches it when the group empties,
behind whatever short corrections arrived meanwhile (a `backfill-*` ticket
still carries `BACKFILL_PRIORITY`). Progress lives in a committed
`data/backfill_state.json`, so a run that dies loses at most its own slice.

**The cursor is the authority, not the dispatch inputs.** A ticket can wait
hours behind other work, so an input saying where to start would be a second,
staler source of truth. Dispatch the whole window; the cursor decides where a
run begins.

Slice sizes are measured and the measurement is written beside each constant:

| Workflow | Slice | Measured basis |
|---|---|---|
| `backfill-gdelt-2026` | 4 days | the 350-min run had not finished a month; 9 queries at 12s pacing plus the retry ladder is ~11 min/day |
| `backfill-2026` | 7 days | seven month-long runs took 137, 145, 159, 184, 185, 188, 215 min |
| `backfill-funding-2026` | 28 days | 12.7 min for a whole month (run 30377226199) |
| `backfill-funding-bulk` | 1 quarter | 6.8 min for two quarters (run 30413051586) |

A size from measurement is an estimate, so the promise is elsewhere: a
**50-minute wall clock** stops the run at the next window boundary. And
`timeout-minutes` drops 350 → 90 on all four, below `LONG_HOLD_MINUTES` (120),
so a sliced backfill can no longer reach the condition the drainer reports as
starvation. The 40-minute gap between budget and timeout is what makes the run
end *cleanly*, which is the whole difference: a cancelled run's commit step is
skipped.

**The lock is untouched.** Same group, same `cancel-in-progress: false`, all
four. Slicing changes how LONG the lock is held, never how MANY writers hold it.

Three guards, because a self-requeuing job is a loop:

- a slice whose cursor did not move is never requeued and the run goes red;
- the cursor is **monotonic**. `actions/checkout` pins a run's SHA at DISPATCH,
  so a run that waited behind the lock read a state file as old as its wait, and
  recording it unconditionally would rewind the chain. Same shape as the stale
  checkout that destroyed 311 rows, one file along;
- a chain past 200 slices stops itself, and a dry or fetch-only run advances
  nothing.

`backfill_slices.py record` runs **after** `git reset --hard origin/main` and
merges into whatever main holds, for the same reason the database is merged
rather than copied. Its exit code is carried past the push, so a bookkeeping
failure never costs the collected rows.

#### What running one taught us

The first live sliced run (30481065108, `backfill-funding-bulk 2026q1`) took its
slice correctly, walked the quarter, and then **died inside `publish.publish`**
because the publish guardrails were holding open findings. The ticket was
emitted after the publish call, so nothing was emitted: the cursor never moved,
the state file was never written, and the chain stopped having recorded nothing.
Only the database commit survived — precisely the asymmetry slicing exists to
remove.

Collecting and publishing are **separate gates**. Each script now catches
`PublishError`, emits its ticket anyway, and then goes red. The ticket carries
`halt`, which is deliberately not the same as failing: the slice's cursor and
totals are applied in full so the work is never redone, and only the **requeue**
is withheld — whatever blocked this slice blocks the next one, and a chain
requeueing into a wall produces one red run per slice and buries the first, real
one. `ops_status.py [2e]` shows it, because between slices there is nothing
running and "is it still going?" stopped being answerable by looking for a job.

### Seven layoff records on a page that says it collects none

Layoffs are read from the sibling tracker's API and never collected here. The
footer says so. Seven records were live anyway.

**The guard read the HEADLINE, and `sec_edgar` has no headline.** It stamps the
identical string `"<Company> 8-K filing (Item 5.02): officer or director
change"` onto every document it fetches, so the first arm spent every run
matching a fourteen-language layoff vocabulary against the collector's own
boilerplate. The second arm only fires when the model chose `displacement`. The
reduction language sat untouched in `raw_text`, which nothing read. Nothing
errored, nothing went red, and a guard **with tests** reported healthy
throughout — this day's theme again.

**Running the existing predicate over the body does not fix it**, and that is
worth knowing before someone tries. `workforce_reduction_term` lets an in-scope
subject appearing EARLIER win the race, and every Item 5.02 filing opens with
"appointed" or "resigned", so a reduction announced three paragraphs later is
suppressed every time.

So `prefilter.filing_reduction_plan` is body-shaped, and the question it answers
is **not "does this mention a cut" but "does this ANNOUNCE one"** — because
getting it wrong the other way rejects the pillar this product is largest in
(3,777 live sec_edgar leadership rows). It fires on:

- **Item 2.05** — "Costs Associated with Exit or Disposal Activities", the SEC's
  own code for this event. Decisive alone: a registrant does not file one for
  somebody else's layoff.
- or **any two of {a reduction term, a plan, a stated scale}** within a
  paragraph. Two, because each alone is ambiguous: a reduction term alone is the
  passing mention ("she led finance through the 2024 layoffs at her former
  employer"), a plan alone is usually a compensation plan, a scale alone is a
  share count.

**Severance, termination benefits and one-time charges are deliberately not
corroborators.** They are the standard furniture of an Item 5.02 officer
departure, and admitting them would turn this into a rule that rejects
leadership changes.

**Measured over the whole corpus rather than asserted:** 3,784 filings re-read,
0 unreadable, 6 announcing a reduction — **0.16%**.

`correct_layoff_scope.py` is the backward half, and it **re-fetches**, because
`raw_text` is never stored: Atlassian's stored summary says "elimination of
certain roles", which the reduction vocabulary does not match, so judging these
rows on the database would reproduce the original defect one level up.
Withdrawal goes through `retract_remote` + `retract_local` like every other
correction here — nothing deleted, nothing edited in place.

It found **three the open list did not have**:

| | |
|---|---|
| Elastic N.V. | "expects to reduce its workforce by approximately 7%", $22-25m of severance and termination benefits |
| Commerce.com (BigCommerce) | a plan "to realign the Company's current workforce", $13.9m primarily severance |
| Verizon | "despedirá a 3,000 empleados" — from google_news, and **the very row the scope guard was written for**. The guard landed; nobody withdrew the row it was written about. |

Elastic and Commerce.com are the judgement call worth inheriting. Both filings
carry Item 2.05 **and** a real Item 5.02 event (a Chief Product Officer leaving,
a CFO taking on COO duties), and the model read only the 5.02 — both rows said
nothing more than "reported a change in its officer or director". Withdrawing
them loses the leadership event too. That is the right trade at this size: 6 of
3,784 live filings announce a reduction and 2 of those carry a leadership event,
so the boundary costs **0.05% of the leadership pillar** to keep a promise the
page makes in writing.
## 2026-07-29 — the cost levers: reading everything relevant on the same budget

The candidate cap raise (150 → 1500) made every prefilter survivor visible to
the pipeline; this session built the levers that keep that affordable. Naively
read-through-ing ~1,000 survivors/run is ~$77/month; the ceiling stays where
it was because most of what survives the free filter no longer needs a model.

**Lever 1 — deterministic teaser extraction (`pipeline/cheap_extract.py`).**
A funding or hiring headline that states every field IS the record. Regexes
close it: employer before a completed-raise verb, amount with its currency
verbatim (non-USD keeps its currency and a NULL USD integer, per the existing
no-FX rule), stage only where the text ties it to THIS round, place only from
a `-based`/possessive prefix that normalises. Everything else DECLINES to the
paid path — precision over recall, because a wrong $0 extraction is worse
than a $0.0013 read. Output goes through the same `validate -> store ->
publish` path with zero exemptions; confidence stays `reported` (a regex does
not make the source more credible), and the row carries `notes =
cheap_extract.EVIDENCE_NOTE` so a reader can see no model read it.

Measured on two real populations (the 2026-07-29 overnight 575-feed harvest
and a fresh live fetch six hours later): 970 and 1,039 prefilter survivors,
28 and 22 closed deterministically, 31 distinct records, **31/31 correct on a
full hand-check** — after four tightenings the sweep itself forced:
- "Kuwait raises $6 billion in three-tranche bond sale" → a name that IS a
  country or city declines, and bond/tranche/fund-vehicle wording declines.
- "Dutch-US MedTech Xeltis" stored the descriptor into the name → hyphen-
  embedded nationalities and sector-tech compounds (medtech, proptech, ...)
  poison the span.
- Title-cased headlines blind the capitalisation heuristic ("Building
  Materials Quick Commerce Startup Fixxly Raises...") → in a title-case
  headline only a single-token name is trusted.
- "a fivefold step-up from its Series B" stamped the PREVIOUS round's stage
  onto a $570m raise → a teaser stage only counts beside the money.

**Lever 2 — story clustering (`run_collect.cluster_stories`) + known rounds.**
The same round rewritten by several outlets survives URL and syndicated-title
dedup; now survivors clustering on the stated (employer, amount) get ONE
read. Two tiers: the strict key (validly named employer) marks its set-aside
copies seen; the loose key (final token before the verb — the four "…startup
Fixxly raises $5.5 Mn" rewrites) holds copies back this run only, so a false
merge can only defer a read, never lose a story. Cross-run,
`dedupe.funding_event_duplicate` matches a stored round by (company_key,
amount) BEFORE any model call — `fuzzy_duplicate` caught these only after the
read was already bought. Measured: 4-5 rewrites clustered away and 1 known
round per population. Small today; insurance for the story every feed carries.

**Lever 3 — read size.** Largely already bounded: news candidates are
headline + teaser (avg 436 chars, p95 599, max 1,250 on the live population —
zero ever reached the cap) and only SEC filing bodies truncate. The magic
numbers became `classify.GATE_CHARS` / `classify.FULL_READ_CHARS` with the
reasoning attached, and every run now logs avg chars sent vs fetched.

**Lever 4 — prompt caching: shape kept, no saving claimable today.** The
read-through's prefix (MINI_SYSTEM + SCHEMA_HINT, ~2,668 of ~3,100 input
tokens) is byte-stable with the item text last — exactly the shape DeepSeek's
automatic prefix cache wants, and OpenRouter passes that through unconfigured
at 0.1x input price. But the providers actually serving
`deepseek/deepseek-chat` today (StreamLake, DeepInfra, Novita — checked via
OpenRouter's endpoints API) advertise **no cache-read pricing**, so there is
no cache to hit on the current slug. `deepseek-chat-v3.1` providers do
(~0.5x), so the already-planned model switch would earn it for free. Guards
added anyway: a test pins SCHEMA_HINT at the head of the user message
(anything inserted before it silently forfeits the prefix), and every call
now records OpenRouter usage accounting (prompt/cached/completion tokens and
cost) into `classify.STATS`, printed per run — if routing ever lands on a
caching provider the run report says so, measured rather than estimated.

**What did NOT change:** `READTHROUGH_CAP` stays 60/run (raising it is the
owner's decision), spend.py still runs first and still hard-stops, the gate
is untouched. Worst-case LLM spend at the new defaults: gate ~1,050 × 2/day
× 30 × $0.00003 ≈ $1.9/mo, reads 60 × 2/day × 30 × $0.00128 ≈ $4.6/mo. The
~25 deterministic closes per sweep are read slots handed back to stories that
genuinely need a model.

---

## 2026-07-29 — company profile pages, and the threshold that decides which exist

`/talent-intelligence-tracker/company/{slug}/`. Profiles already rendered for
every employer we held a row for. The work was deciding which of them deserve
a URL, and making one decision serve both the page and the sitemap.

### The threshold, and why it counts documents

Measured against the live `/query` endpoint (15,630 current rows, 7,408
employers by display name; 7,301 by `company_key`, which is what the page groups
on):

| rows per employer | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| employers | 4,840 | 751 | 376 | 503 | 393 | 135 | 90 | 137 | 183 |

| documents per employer | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| employers | 5,317 | 1,215 | 274 | 70 | 60 | 66 | 87 | 137 | 182 |

Three readings, in the order they change the answer:

1. **Rows are the wrong unit.** 235 employers carry four rows behind ONE
   document, because `sec_execcomp` splits a single pay-versus-performance table
   into a row per fiscal year. A row count measures how finely we parse a
   filing, not how much we know.
2. **One document restated is not a page.** 72% of employers sit behind a single
   document, and a reader is better served by that document.
3. **Three documents from one feed is one thing said three times.** The UK pay
   gap rows carry an *identical* read-through sentence with a different
   percentage, one per reporting year. 638 employers would clear a plain
   three-document bar on that alone, which is the template-plus-a-number shape
   that gets a whole set filtered.

So: **3 documents, and either 2 kinds of evidence or 5 documents.** 713 of 7,301
employers, 9.8%. 186 qualify on breadth, 527 on a multi-year series.

Below the bar the page still renders and stays linked from the dashboard table,
but goes `noindex, follow` and is absent from the sitemap. Not a 404: the
dashboard links there and a recruiter following that link should get the page.

**One predicate does both.** `tit_company_meets_threshold()` answers it for the
page; `tit_company_gate_having()` builds the sitemap's `HAVING` clause from the
same three constants, and `tests/test_company_page.py` fails on a threshold
typed a second time. The sibling shipped noindex URLs inside its own sitemap and
heard about it from Search Console; that is not prevented by care.

Everything is computed on render from `wp_tit_signals`. No generated pages, no
regeneration step, and the sitemap is a query rather than a file.

### Three defects found by curling it, not by reading it

- **Two contradictory robots tags** (1.45.0). A thin profile served
  `noindex, follow` from us and `follow, index` from the SEO plugin. The first
  fix named Yoast's filter and did nothing: the tag is SEOPress's. Naming a
  plugin pins us to that plugin and to its hook names. The head is buffered and
  every robots tag replaced with exactly one of ours, the same trick
  `tit_render_header()` uses for `<title>`. A test now refuses any SEO plugin's
  name in the file. The `X-Robots-Tag` header goes out before any buffering, so
  a buffer that never closes cannot leave a thin profile indexable.
- **The sitemap 301ed** to `.../company-sitemap.xml/`, because WordPress
  trailing-slashes anything it does not recognise as a file. `redirect_canonical`
  is off for that one query var.
- **`%26` kills a company URL.** Found by fetching eight random URLs from our own
  sitemap: one 404. `rawurlencode()` writes `&` as `%26`, which does not survive
  the rewrite. `/company/b%26q/` is 404, `/company/b&q/` is 200, and `&` is a
  legal sub-delim in a path segment, so it is left literal. **144 of 7,301
  employer keys carry an ampersand** (Ernst & Young, Holland & Barrett, Mitchells
  & Butlers, most UK NHS trusts) and every one of their dashboard links had been
  dead since profiles shipped. A percent-encoded non-ASCII byte does not survive
  either, and neither does the literal character: 18 keys, now not indexable and
  not in the sitemap, because a sitemap full of 404s is what gets a set
  distrusted. Fixing those properly needs a stored ASCII slug on `company_key`,
  which is a pipeline change and a migration.

### Known and not fixed: sitemap discovery is one manual step

`/blog/robots.txt` is a physical file, so Apache serves it from disk and the
`robots_txt` filter never runs (gotcha 5). The robots.txt a crawler actually
reads for this host is `https://asktherecruiter.com/robots.txt`, which belongs
to the separate root app. Neither is reachable from this repo. Discovery today
is the internal links; **submit the sitemap in Search Console, or add its URL to
the root robots.txt.** The filter is left registered and is not counted as
working.

### Verified live, by curl, on 1.45.3

712 URLs in the sitemap, `application/xml`, no redirect, XML parses. 20 sampled
entries (including 6 with an ampersand) all 200 with exactly one
`index, follow`. `oracle` and `bloomberg` (3 documents, 1 kind) 200 with
`X-Robots-Tag: noindex, follow` and absent from the sitemap. Dashboard,
`/sources/`, `/recall/`, `/corrections/` and `/aggregate` all unchanged.
**The visual result is unverified: this session had no browser.**

---

## 2026-07-29 — pre-publish guardrails

Built because the $86bn Form D overstatement was never a thing nobody could
have checked. It was a thing nobody was going to remember to check. Four
arithmetic assertions now run on the write path, in `pipeline/guardrails.py`,
called from `pipeline/publish.py` before a single row is sent. No model, no
network, no cost.

**Flag, never drop. Fail loud.** Findings land in a `publish_guardrails` ledger
and block publishing until a person accepts or rejects each one. Accepting is
remembered, so ChangXin Memory's genuine $8.6bn raise is answered once and never
blocks again. Nothing is auto-binned: silent auto-correction would be a
different invisible defect, which is the same argument that keeps `link_check`
from retracting a row over an HTTP code.

### 1. Implausible single-row amounts

The threshold is **derived, never typed**: the value whose expected count under
a robust log-normal fit of the stored amounts is 0.1 rows. Centre is the median
of log10, scale is 1.4826 x MAD, and z comes from n. On 3,057 stored amounts
that is **$1,799,597,726**, and it flags 5 rows.

Median and MAD were chosen by **measurement, not by preference**. Replaying the
998 retracted vehicles back in:

| estimator | clean | contaminated | vehicles caught |
|---|---|---|---|
| median / MAD | $1.80bn | **$1.35bn** | 14, worth $68.4bn |
| mean / sd | $2.32bn | **$2.42bn** | 11, worth $62.5bn |

The robust pair tightens as bad rows arrive; the mean-based one relaxes.

**The limit, stated because it decides what the other three checks are for.**
The retracted vehicles were not a distinguishable population by amount: log10
median 6.641 against the clean corpus's 6.737. Only the individual extremes
stood out. This check catches the largest members of a bad class, never the
class, and a contaminant forming a large tightly-clustered mode two decades up
would lift any fitted threshold, robust or not.

### 2. Period totals must reconcile

Three invariants, from the page that carried "this quarter 268" against "2026 so
far 6,018" beside a headline of 14,019.

- **Ordering, derived from the start dates rather than assumed to nest.** Every
  cell counts rows on or after its own start, so an earlier start can never hold
  less. Asserting week-inside-month would have been wrong: "this week" reaches
  six days back and crosses the month boundary for roughly half of every month.
  Pinned by a test.
- Year-to-date never exceeds all-time.
- **A subset never exceeds "All updates"** in the same column. This is the shape
  of the original defect: 998 vehicles counted as funding under a clause scoped
  differently from the one counting updates.

### 3. The printed date span must match the data

From "Everything here spans 3,318 days, 28 Jun to 28 Jul 2026" — nine years of
days against thirty days of dates, because the count was measured over the whole
table while the bounds came from the recent window. The page still holds **two
legitimate scopes at once** (`lo_all/hi_all` drive the date inputs, `lo/hi` the
sentence under the tiles), so the check asserts both, that each day count comes
from its own bounds, that the view sits inside the whole, and that the span
reaches every period a tile reports a nonzero count for. `guardrails.py --live`
adds the only assertion that can see what a reader reads: the `span` object from
a live `/aggregate` must match one of the two recomputed scopes and nothing else.

### 4. Vehicle and SPV names on funding rows

Runs on **every** funding row, not only Form D ones: the collector's filter
governs what Form D collects, this governs what reaches a headline figure
whatever route it took. It reuses `sec_form_d.EXCLUDED_NAME_PATTERNS` rather
than restating it, and adds the publish-time set: street addresses, numbered
accounts, separate accounts, and **the abbreviations** — `GIC`, `GICs`, `BOLI`,
`COLI`, funding agreement, institutional life.

**Every pattern was measured** against the 998 real retracted rows (recoverable
from `signals` where `is_current = 0` and the retraction note) and against the
3,057 live funding rows. A pattern earns its place only if its yield on the real
defect beats its cost in live review. Two were tested and **rejected**, recorded
in the source so nobody re-adds them from first principles:

| candidate | retracted | live cost | verdict |
|---|---|---|---|
| `series \d+$` | 2 rows, $0.00bn | 16 rows, all one employer | rejected |
| `\d{1,2}\s*(llc\|lp)$` | 38 rows, $0.23bn | 24 rows incl. HawkEye 360, Inc. | rejected |

Measured recall of what shipped: **229 of 998 rows, but $71.3bn of the $85.6bn**,
because the vehicles are exactly the large ones. On today's live rows it flags 3.

**A finding worth reading even though it is empty.** The GIC/BOLI/COLI
abbreviations match nothing, and checking every stored text column of the
retracted rows says why: that wording lived only in the SEC dataset's
`DESCRIPTIONOFOTHERTYPE`, which was never stored. So the abbreviation's real
home is `sec_form_d_bulk.NOT_A_CAPITAL_RAISE`, where the description is read.
It is in the publish-time set as well because it costs nothing and the next
vehicle carrying it in its NAME should not need a second incident.

### Wiring, and the two things that would have made it a decoration

- **`merge_db.py` merges the ledger, and a human's answer beats a later
  automatic write.** Every other table there resolves a collision with "later
  wins", which is actively wrong for a review queue: the later write is usually
  a run re-firing the same finding, and the earlier one may be the owner's
  acceptance. Without this, a run in flight would silently reopen an accepted
  row. An unreviewed disagreement resolves to `open`, because this table decides
  whether a figure goes out.
- **`ops_status.py [2d]` evaluates live when the ledger is empty** instead of
  printing "nothing flagged". An empty ledger means nobody has looked, and the
  tool every session is told to trust must not confuse the two. It also says so
  when the interpreter cannot import the collector's patterns, so a narrower
  check never prints a smaller number silently.

`health_digest.py` puts a quarantine ahead of a stale collector in the subject
line, with its own paste-ready instruction. A stale scraper costs coverage; a
quarantined row that nobody has judged is an unchecked figure one decision away
from going out.

### The failure mode was wrong, and production said so within the hour

The first build HALTED the run on any open finding. Both of the first two
production runs failed on the same eight:

```
collect               -> 8 open guardrail finding(s). Nothing was published.
backfill-funding-bulk -> 8 open guardrail finding(s). Nothing was published.
```

Both were carrying dozens of perfectly good records. In steady state that means
**a single unreviewed row blocks every row**, and since X.AI's $16.6bn is a real
raise, the first genuine billion-dollar round of any week halts collection until
a human answers a prompt. The owner needs this running for days unattended, so
that is a design error and not a bug in the checks.

**Now it quarantines.** The flagged row is dropped from the batch; everything
else publishes. It is never marked published, so it reaches no headline figure
AND it is re-offered on every run, which means accepting the finding releases it
with no requeue and no replay path to remember. `enrich_published()` filters the
same set, because `funding_amount_usd` travels that way and would otherwise
reach the money total by the back door while `publish()` was carefully not
sending it by the front.

What did NOT change, and must not: an unreviewed row stays out of the data and
out of every aggregate. $86bn is the reason.

### The exit status, and why it is not simply 0 or 1

| state | run | why |
|---|---|---|
| quarantine only | **exit 0** | the guard SUCCEEDED. The suspect row is out of every figure. Red here would mean "the machine noticed", and a permanently red `drain-writers` already taught this project what that does to attention. |
| finding past its window | publish the clean rows, **then** exit non-zero | red should mean a human neglected it, which requires the human to have been told. `health-digest.yml` runs Mondays, so the email is the moment of telling. |
| aggregate finding | **halts immediately** | a period total or a date span that does not add up names no row, and there is no clean subset of a wrong total. |

Two grace windows, both derived from the cadence rather than picked:

- **192h** for a row that never reached the site: one full digest cycle plus a
  day. Before the first email fires, red would blame somebody who has not been
  asked, and nothing is wrong in public. After a whole cycle of silence, it is a
  choice.
- **72h** for a row **already on the site**. Different in kind: that figure is
  wrong in public right now and quarantine cannot pull it back, only a human
  retraction can. The owner's own ceiling is "days" unattended, so three days is
  the longest ordinary absence.

The escalation is raised **after** the send, never before. "One suspect row does
not take the batch down with it" has to hold on the day the run goes red too, so
the clean rows are already sent, marked published and committed by the time the
exception is thrown. Red there means "nobody answered", never "work was lost".
The countdown to red prints on every run, so the day it turns is never a
surprise.

### Being impossible to miss

`publish.py` prints the quarantine with `::warning::` / `::error::` GitHub
annotations, so it lands on the run summary page rather than in a log nobody
opens. Printed from THERE rather than from `run_collect` so six backfill
scripts, both corrections and the enrich job get it for free; not one of them
would have grown its own version. `ops_status.py [2d]` and the weekly digest
both separate **HELD** from **ALREADY LIVE** and print the countdown, because
those are different problems wearing the same word.

**Verified on a copy of the live database**, 46 rows offered to publish: 6
quarantined, 40 published, exit 0, and all six still unpublished afterwards.

---

## 2026-07-29 — the day everything that looked healthy turned out not to be

One theme ran through every defect found this day, and it is worth stating once
at the top because it predicts where the next one will be:

> **Every serious failure was something that looked healthy while being broken.**
> Not one was a crash, an error, or a red build. A status-code check, a green
> workflow run, a passing test and a confident ops tool each reported success
> while the thing underneath was dead, empty, unshipped or lost.

The engineering response is a rule, now applied throughout: **health must be
proven by output, never inferred from the absence of an error.** A feed is
healthy only if items arrived and are recent. A run is healthy only if it
executed a step. A deploy is healthy only if the live page changed. A correction
is healthy only if the figures moved.

### Link rot: the failure that is invisible by construction

Added the same day and in the same spirit as everything above, because it is the
purest case of the theme. A source link that dies renders identically to one
that works. Nothing errors, no run goes red, no test fails, and the claim it
supported quietly stops being sourced. This repo had no defence at all while the
sibling had two, and it matters more here: the promise is that every update
links to the filing behind it, across 575 publisher feeds in 139 countries.

`link_check.py` records status, final URL and date per URL; `archive_sources.py`
gives each cited document a Wayback fallback via the sibling's two-pass design;
`source_links` holds both, keyed on the URL because 15,631 signals share 12,890
of them. Both DORMANT, both free (no model call).

Three decisions worth inheriting:

- **A dead link never edits a row.** The only write to `signals` is
  `archive_url`. An automatic reaction to an HTTP code would let a publisher's
  bad afternoon delete evidence, so the state is recorded and surfaced and a
  human decides. This is the same instinct as `store.revise()`: the record of
  what a source said is not the place to put HTTP weather.
- **Status codes cannot catch the dangerous case.** `botswanaguardian.co.bw`
  became a betting site whose feed verified perfectly green. The only signal is
  that the bytes came from a domain other than the one we stored, so the
  checker reuses the collector's `registrable_domain()` drift guard. The first
  real sweep then found `hln.be` answering 200 from `myprivacy.dpgmedia.be` — a
  consent gate, not a takeover, distinguished because a gate carries the article
  URL back in its callback and a squatter has no reason to name the document it
  replaced. Without that distinction `drifted` would have degraded into a list
  of European cookie banners and the state that matters would be ignored.
- **Measure before arming.** Dry runs over 291 real stored URLs: 0% rot, and
  Wayback already holds 29% of publisher URLs against 3% of SEC/GOV.UK ones.
  That gap set the nightly default to the publisher tail, because EDGAR keeps
  its own filings and a 40-capture budget spent on 12,700 index pages would take
  most of a year to preserve what a government already preserves. 0% is a
  baseline on a corpus weeks old, not a clean bill of health.

An off-the-shelf WordPress broken-link-checker plugin was rejected explicitly
and the reason is written where someone might be tempted: they crawl post
content, our links live in `wp_tit_signals`, and one would have reported a green
badge over an entirely unchecked corpus. That is this day's theme wearing a
plugin.

### Plugin versions shipped

| Ver | What |
|---|---|
| 1.43.0 | **An archived copy beside every source link** (`shortcodes.php` and `dashboard.js` render it identically, `archive_url` added to `tit_enrichable_columns()`). A SECOND link, never a replacement: the publisher's own copy is the citation. **NOT DEPLOYED** at the time of writing; nothing carries an `archive_url` yet because every archiving run so far was a dry run. |
| 1.42.3 | **Sources page listed 5 collectors while 9 were running.** UK gender pay gap, SEC executive compensation and the entire 575-feed national press collector were live and unlisted — two of them among the largest contributors of rows. The guard missed it because `test_live_sources_are_only_the_ones_with_collectors` asserted a **hardcoded set of five names**, so it caught a source listed *without* a collector and was blind to a collector running *without* a source. The defect went the blind direction. Now derives the expected set from `run_collect.SOURCES` via a new `COLLECTOR_BY_SOURCE_NAME` map and fails both ways, with `tripwire_chase` excluded by name as deliberately dormant. |
| 1.42.2 | **Corrections page flipped to past tense** after the Form D correction actually ran. Three-column table (Before / We projected / Measured now) rather than silently replacing the projection with the actual — a corrections page that quietly revises its own numbers is doing the thing it exists to prevent. Tests now fail the build in **both** directions: past-tense wording on a pending entry, and pending wording on an applied one. |
| 1.41.1 | **"More filters" disclosure removed for real**, plus `Team Or Function` → `Team or Function` (naive title-casing had capitalised a conjunction). See incident below. |
| 1.41.0 | Recall page: scheduled weekly runs, trajectory chart, automated gold-set retirement, `POST /talent/v1/recall`. |
| 1.40.0 | Recall measurement published. |
| 1.39.2 | Corrections page rewritten to disclose the Form D defect as *identified and scheduled* rather than applied. |
| 1.37.1 | **Clipping regression fixed** — see incident below. |
| 1.37.0 | Owner's UI punch list: `2026 so far` → `2026 YTD`, always-zero Today column dropped, self-contradicting date-span line fixed, redundant region heading deleted, duplicate Headcount label removed, notable-vs-everything control rewritten definition-first, computed single-collector caveat on the country chart. Also the sticky-bar attempt that caused 1.37.1. |

---

## Incident log

### The deploy that shipped nothing (2026-07-29)

`deploy-plugin.yml` **defaults to `dry_run=true`**. A plain
`gh workflow run deploy-plugin.yml` produces a run that passes every step, lints
the PHP, confirms the version bump, reports **success** — and uploads zero
bytes. The "Upload over FTPS" step is simply skipped.

This was hit **after** the identical trap had been documented for
`correct-form-d.yml` and relayed in writing an hour earlier. Reading about a
trap does not prevent walking into it; only the verification step does.

**Guard:** always dispatch with `-f dry_run=false`, and **always curl the live
page** afterwards rather than trusting the green tick. The only reason this was
caught is that the live `ver=` was checked and still read the old version.

### Fifteen data-writing runs silently destroyed (2026-07-29)

Every workflow writing `data/talent_intel.db` shares the `talent-collect`
concurrency group with `cancel-in-progress: false`. That lock is **correct** and
must stay — it is what stops two writers doing reset-hard-then-copy-our-file-back
and destroying each other's rows.

But **GitHub keeps only ONE pending run per concurrency group.** With one run
executing and one waiting, dispatching a third silently *replaces* the waiting
one. The displaced run ends `cancelled`, having created **zero jobs**, with no
error and no annotation anywhere.

Measured: 15 runs lost — `correct-form-d`, `correct-sec-pillar` ×2, `enrich` ×3,
`recall`, `collect`, `collect national press`, and five backfills. Every one had
been reported to the owner as "queued".

Three things made it worse than it first appeared:
1. **Cron evicts too.** One `enrich` was displaced by the *scheduled* `collect`
   run created one second earlier. This was never only an agent-parallelism bug.
2. **`enrich.yml` was invisible to the guard.** `test_every_database_writer_shares_one_lock`
   found writers by searching for the string `talent_intel.db`; `enrich` writes
   through `pipeline.publish` and never names the file. It held the lock and was
   evicted three times while the test reported all-clear.
3. **Re-dispatching would have failed silently.** Both correction workflows
   default to `dry_run=true`, so a naive replay produces a green run that changes
   nothing.

**Fix — an invariant, not a retry.** `drain-writers.yml` dispatches the next
ticket **only into an empty group**, which is the one condition under which
nothing can be evicted. Work waits in a committed `data/writer_queue.json`
instead of GitHub's single lossy slot. The drainer is deliberately **not** in
`talent-collect` (a drainer queued behind the lock could never drain it), and it
goes red on any writer run that ended cancelled with zero jobs. **No writer
workflow was modified** — the lock cannot have been weakened by a change that
never touched it.

**Queue a writer, never dispatch one directly:**
```
gh workflow run drain-writers.yml -f enqueue=<workflow>.yml \
  -f inputs_json='{"dry_run":"false"}' -f reason='why'
```

**Residual gap:** direct dispatches — including cron — can still be evicted. The
drainer detects and reports them loudly; it cannot prevent them.

### A 350-minute backfill starved every correction (2026-07-29)

`backfill-gdelt-2026` held the writer lock from 04:59 to 10:49 UTC, hit its own
timeout, was cancelled, and its "Commit the database" step was **skipped** — so
roughly six hours of collection was lost *and* it blocked the corrections for
that entire window.

Priority ordering now puts short corrections ahead of backfills, and a lock held
past two hours with work waiting is reported as starvation. **Priority cannot
preempt a running job**, so the real fix is backfills that run in bounded slices
and requeue themselves. Not yet built — see HANDOVER "Open".

### ops_status.py printed a confident false all-clear (2026-07-29)

`[2b] WRITER QUEUE` reported *"Nothing queued, nothing lost"* while `origin/main`
recorded **15 orphans and a waiting ticket**. The queue lives in a committed
file, the checkout was two commits behind, so it read a file written before any
eviction happened.

CLAUDE.md tells every session to run `ops_status.py` **first**, which makes a
false all-clear the most expensive thing this tool can say — the eviction bug
wearing the reporting tool as a hat. An absent queue file now means **"unknown"**
rather than "nothing" whenever the checkout is behind, and a present one is
labelled stale. It deliberately does **not** fetch: `ops_status` is read-only and
must work offline and in an egress-blocked session.

### The sticky fix that guillotined the page on mobile (1.37.0 → 1.37.1)

A rule of `:has(#tit-dashboard) { overflow-x: clip }` with no element qualifier
matched **every ancestor**, including the 279px `.entry-content`. The dashboard
breaks out of that with a negative margin to reach full width, and a full-bleed
breakout inside a clipping ancestor is guillotined at that ancestor's edges.
`clip` does not scroll, so ~48px was cut off **each side** and unrecoverable. The
hero headline rendered as *"now who's hiring before the / b ad appears"*.

**The check that would have caught it reported healthy.** `scrollWidth === innerWidth`
passes here — because `clip` achieves that by destroying the content. Do not use
that check to validate overflow containment.

Now `html`/`body` get `overflow-x: clip; overflow-y: visible` (the only axis
combination that clips sideways bleed without creating a scroll container, since
`visible` degrades to `auto` when the other axis is `hidden`), and the narrow
wrappers are forced back to `visible`.

### Form D published property vehicles as startup funding

994 → **998** rows: single-asset property SPVs, insurance separate accounts and
synthetic GICs, filed on the same Form D real startups use, published with a
**hardcoded `"hiring"` direction** and an invented read-through the filing never
stated. They inflated the headline by roughly **$86bn**.

A second pass found the first fix incomplete: the exclusion was written from the
spelled-out phrase *"guaranteed investment contract"* and missed the trade's
abbreviation, leaving four **GIC/BOLI/COLI** rows at $12.4bn as the largest
remaining amounts. **Lesson: an exclusion written from a spelled-out phrase will
miss the abbreviation the industry actually uses.**

**Applied 2026-07-29, verified live:** money raised **$200.3bn → $124B**, funding
rows 4,072 → 3,081, employers 6,745 → 5,463. Came in ~$10bn above the $114.1bn
projection — and the cause was checked rather than assumed: **$9.25bn was NEW
data** (ten national-press records captured between projection and correction,
including a single $8.6bn ChangXin Memory raise), not rows the correction missed.
Only $0.9bn was local-versus-live divergence.

### 8-K Item 5.02 filings filed as pay events

**573** rows (not the 548 first counted — 25 more carry the same boilerplate
headline with the `(Item 5.02)` parenthetical dropped, so a substring search
missed them). Held in the database but invisible to anyone browsing leadership
moves.

The forward fix is deliberately **narrow**: it fires only while the row still
carries the collector's own officer-change headline. A blanket "sec_edgar means
leadership change" rule would have misfiled 20 genuine comp and M&A filings
(Masimo/Danaher, Bakkt, Littelfuse equity grants) — the collector stamps the same
generic headline on **every** document it fetches, so where the model replaced it,
it had read something specific and that judgement is kept.

Three rows were withdrawn explicitly because the correction turns them into
duplicates that `publish()` **counts without naming and marks published** — they
would have been withdrawn from the site, replaced by nothing, and logged as
success.

---

## Sources: what was verified, and the traps found

**565 feeds verified live across 137 countries**, every one fetched and parsed
rather than assumed. Americas 116/37 territories · Asia-Pacific 126/29 ·
Europe 217/38 · Middle East & Africa 106/33. Zero aggregator hosts.

**Israel was the acceptance test.** The owner found four missed rounds by asking
Gemini (Glow, Plantopia, Harmony, Enigma). Globes' English node carried Glow and
Plantopia; Geektime carried Harmony and Enigma. **CTech publishes no feed at all**
— its advertised `rss` endpoint 404s, its homepage declares no feed, and its HTML
contains no `rss` string. It was never going to work; it only looked like it
should.

**Those four rounds are not recoverable through feeds.** RSS serves a rolling
window with no archive — Globes reaches back 5 days, Geektime 3, Times of Israel 1.
Feeds fix **forward** recall only; historical recovery needs the GDELT archive
path, which takes explicit start/end dates.

### Feed traps — each produces a source that looks wired and delivers nothing

| Trap | Example |
|---|---|
| **200 OK, zero items** | IT World Canada returns well-formed RSS with only a `lastBuildDate`. Passes a status check forever. |
| **200 OK, years stale** | Sigmalive (2024-09), Moneycontrol (2024-04), NoCamels, MENAbytes, Disrupt Africa — three of these were *already in* the catalogue. |
| **Domain hijack** | `botswanaguardian.co.bw` now redirects to a **betting site** whose feed verifies perfectly green. We would have cited a gambling operator as a Botswana news source. |
| **Malformed XML** | Six feeds die under strict `ElementTree` — Times of Oman, Daily News Egypt, African Manager, Sika Finance, Condia, New Era. Oman looked sourceless while having a working publisher. |
| **Leading junk** | IO+ serves **two XML declarations** back to back; a strict parse dies at byte 38. The existing trim only handled trailing junk. |
| **Header-dependent** | Techpoint Africa and Arab News 403 a bare RSS `Accept` and 200 a browser one; four TownNews feeds do the **exact reverse**. Neither header set works globally. |
| **Relative links** | B2B Cambodia emits relative slugs, so source URLs break entirely. |
| **Non-standard dates** | KED Global uses `dc:publishDate`, Digital Business KZ uses `news:publication_date`; four feeds carry no item date at all. |
| **Oversized** | TechNode's feed is 11.8 MB and truncates mid-record under any sane read cap. |

**26 of 65 pre-existing feeds were already dead.** A prior audit claiming "12 of
15 verified" did not hold.

**25 feeds are disallowed by their publisher's robots.txt** and were withdrawn,
including three predating the collector. Enforcement is in code: robots fetched
once per host per run, cached, fails **open** on a missing file (no robots.txt is
the standard "no restriction") and **closed** only on an explicit `Disallow`.
SmartRecruiters was dropped on the same basis, costing 5 boards including Bosch's
4,747 postings — their API answers us `200` anyway, which is exactly why the
publisher's stated terms decide and not the server's behaviour.

---

## Recall: the number that makes this citable

**8 of 89 events held — 9.0%. Outside the US, 1 of 55. 27 of 29 countries scored
zero.** Published at `/talent-intelligence-tracker/recall/`.

The gold set was built by eight independent research passes **forbidden from
consulting our own database**, sealed before matching, every URL liveness-checked,
and **nothing dropped after assembly** — dropping items post-hoc is what makes a
recall number meaningless.

Set *retirement* is automated (35 days, or three measurements converging). Set
*assembly* is deliberately **not**: a generator would drift toward finding exactly
what we already collect, raising the number for the worst possible reason. What
is automated is the guard — `REQUIRED_SHAPE` rejects any future set that is too
small, too US, too large-event, or built from one document type.

Two defects it surfaced that eyeballing never would: an 8-K classified as a pay
event (held but unbrowsable), and `identity.enrich()` **which had never run in
production** — it is a no-op unless the caller passes a connection, and all five
callers omitted it.

---

## Language handling

Three languages were missing from the free regex prefilter — **Hebrew, Czech,
Danish** — which silently discarded items from wired feeds before they ever
reached the classifier.

- **Hebrew needed its own word boundary.** Clitics (*and*, *the*, *in*, *to*)
  are single letters glued to the next word and count as word characters, so
  `\bגיוס\b` matches only the bare noun and misses most real headlines. But loose
  substring matching fails oppositely: *salary* sits inside *a rental*,
  *employee* inside *the fact*.
- **`פיטר` is deliberately excluded** from the reduction vocabulary — it is also
  how *"Peter"* is spelled. A reduction verdict is a hard drop, so including it
  would have silently discarded every funding story mentioning a Peter.
- **Czech `investice` produced 9 false positives in 15.** English answers this by
  gating on *"funding round"* and not *"investment"*; Czech and Danish now do the
  same.
- A regex bug meant any alternative ending in *million* could never match, so
  real Danish funding headlines read as clean misses.

Measured keep rates after: 19% / 11% / 16%, the band the English gate already sits in.

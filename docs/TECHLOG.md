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

`health_digest.py` puts open findings ahead of a stale collector in the subject
line, with their own paste-ready instruction. A stale scraper costs coverage; an
unanswered guardrail means publishing is stopped and an unchecked figure is one
decision away from going out.

**Operational note for whoever runs the next collect.** The ledger is empty and
was deliberately not written from a laptop, so the first real run records **8
findings and goes red**: 5 amounts (X.AI $16.6bn, Madison Air $10.87bn, Masimo
$9.9bn, ChangXin $8.6bn, Dillard's $2.39bn) and 3 vehicle names (two
Metropolitan Tower Life Insurance Co rows, one `101 East Court Street LLC`).
`data/talent_intel.db` is still committed on that run (`!cancelled()`), so
nothing is lost. Answer them with `guardrails.py --accept` / `--reject`. Do not
accept anything to clear the queue: an accepted finding never blocks again.

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

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

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

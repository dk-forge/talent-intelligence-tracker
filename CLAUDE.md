# Talent Intelligence Tracker — orientation

Hiring-side talent market signals, sourced to primary documents, segmented by
city / region / country. Sibling project to the AI Layoff Tracker; the two
cross-link and share a host, but share no code and no database.

- **Live:** https://asktherecruiter.com/blog/talent-intelligence-tracker/
- **Repo:** https://github.com/dk-forge/talent-intelligence-tracker (public — keeps Actions minutes free, so never make it private and never commit a secret)
- **Sibling:** https://asktherecruiter.com/blog/ai-layoff-tracker/

## Start here, every session

```bash
python3 ops_status.py     # the data
python3 ci_status.py      # the runs behind the data
```

`ops_status.py` is read-only, no deps, no keys: what is actually stored, which
collectors are stale or degraded, the honest coverage claim. `ci_status.py`
needs `gh`, a credential and a network, which is why it is a separate command —
staying offline is exactly what stops ops_status from ever seeing a red run. It
reads Actions for **both** trackers: what is red on main right now, what failed
in the last 24h, and any run that ended `cancelled` with zero jobs (the eviction
signature, invisible in the GitHub UI).

Exit 2 in either means something needs a human. `ci_status.py` also exits **3**
for "I could not check" — no gh, no credential, no network — because that must
never read as an all-clear.

**Red CI EMAILS the owner, and the email survives the host being down.**
`ci_status.py` tells a session what is red; this tells the owner. ARMED since
2026-07-30. `.github/workflows/ci-alert.yml` listens for EVERY workflow
completing (one `workflow_run` listener, not an `if: failure()` step in each of
30 files) and runs `ci_alert.py`, which extracts the real failing assertion and
POSTs it to the keyed `talent/v1/alert`. Deduped **by cause, not by run**
(numbers normalised out before hashing; open/resolved state held in the
endpoint), and it mails **RECOVERED once** on the next green run. `cancelled` is
deliberately never alerted: this repo evicts runs by design, and `ci_status.py`
is what tells an eviction from a failure. Do not "fix" the quiet on a repeat — an
alarm that mails eight times in an afternoon is one you learn to filter, and a
filtered alarm is the original problem in a new hat. `ci_status.py` shares
`ci_alert.extract_cause` so the dashboard and the email can never describe one
failure two ways.

**`/alert` is a route on the host it reports about, and on 2026-07-31 that was
the whole defect.** Bluehost 504'd under `/blog/` for seven minutes: enrich
failed, drain-writers correctly went red, and the alerter then failed four times
saying "HTTP 504 from /alert" — mute at exactly the moment it was needed, and
manufacturing four extra red runs while it was. Three things now hold:

- **An undeliverable alert is HELD, not lost.** `ci_alert.py` retries transient
  failures in-run, then writes the alert to `data/alert_outbox.json` (committed
  — the repo IS the memory). `host-watch.yml` delivers it the next time it
  proves the host is answering. `alert_outbox.py` explains why a committed file
  and not a longer backoff.
- **A delivery failure is NOT a red run.** Holding an alert exits 0. The only
  non-zero left is "could neither deliver NOR hold", which is the one state
  where nobody hears about the original failure. **Do not restore the old
  `exit 1` on a failed POST** — that is what let one outage manufacture red runs
  which manufacture alerts which also fail.
- **Something watches the host now.** `host-watch.yml` GETs one public REST
  route every 15 minutes, records `data/host_status.json` (only on a change or a
  6h heartbeat, so it is not commit noise), and `ops_status.py [2f]` reads it
  offline. Three consecutive failed runs is a SUSTAINED outage, which opens
  **one** GitHub issue — the channel that is not on the host. Opening and
  closing it are two emails; every update in between edits the body and mails
  nobody. That is deliberate: GitHub's raw run notifications sent ~15 emails for
  one defect, and an undeduped channel is a filtered channel. **A down host does
  not redden `host-watch`** — a red run there would fire the CI alert, which
  posts to the down host, which is the loop this design exists to break.

## The 60-second model

```
collectors/   one file per source. Returns raw dicts. NEVER writes.
pipeline/     classify -> validate -> dedupe -> store. Shared by every source.
data/         talent_intel.db, committed. The repo IS the memory.
source_registry.py   markets, tiers, search vocabulary — all as data
analysis/     measurement, never collection: recall/ grades what we hold,
              tripwire/ finds what we are missing (run_tripwire.py, DORMANT)
```

GitHub Actions cron collects 2x/day, commits the database back, and POSTs to a
keyed WordPress endpoint that renders the dashboard. The plugin exists and is
deployed; `wordpress-plugin/` is it.

**Collection is ARMED.** `collect.yml` runs at 06:00 and 18:00 UTC. Disarm by
commenting the two schedule lines out again; nothing else changes. `ops_status.py`
is the authority on this, not this file.

**The plugin deploy is NOT armed.** The push trigger in `deploy-plugin.yml` is
still commented out, so a merged commit does not reach the site. After any
change under `wordpress-plugin/`, run it by hand and then verify the page:

```bash
gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker --ref main -f dry_run=false
```

**A SUBAGENT MUST NOT RUN THAT COMMAND.** It publishes to the live site, and
the deploy is the session's call to make, not a delegated one. This paragraph
used to say only "run it by hand", which every spawned agent correctly read as
an instruction addressed to itself — on 2026-08-01 a chart-grid agent shipped
1.62.0 to production off the back of it. The work was right and the page was
fine; the point is that nobody chose to publish it, the doc did.

So: an agent that changes `wordpress-plugin/` pushes to main, bumps `Version:`
and `TIT_VERSION`, and reports **"pushed SHA <sha>, not deployed"**. The
session that spawned it runs the deploy and does the live verification. If you
are an agent and you believe you are the exception, you are not.

**There is no Railway deployment.** Collection runs on Actions because the
database must be committed back to the repo; an ephemeral container discards
it. If you find a Railway service pointed at this repo, it is a leftover.

## Rules that are not negotiable

- **No source URL, no record.** Enforced in `validate.py`, tested.
- **The model never invents a number.** Any figure in a summary must appear
  verbatim in `raw_text` or the whole record is discarded, not repaired.
- **Confidence is capped by the source.** A news article cannot become
  `verified` however confident the model sounds. `reported` and `rumored` are
  never silently promoted.
- **Aggregators are discovery pointers, never stored sources.** Google News
  sends us to the publisher; the publisher is what we store. **A model is a
  discovery pointer too.** The tripwire (`run_tripwire.py`) asks a search-backed
  model what we are missing; every field it returns is prefixed `claimed_` and
  dies in the work list, and `collectors/tripwire_chase.py` takes the employer's
  name, finds the publisher's own article, and stores that instead.
- **Never write a row directly.** A new source builds a raw dict and goes
  through `classify -> validate -> store` like everything else. The raw dict
  MUST set `raw_text` — the classifier reads only that, and a source that
  forgets it posts zero records silently. That bug cost the sibling weeks.
- **Never overwrite a record.** Corrections append a revision via
  `store.revise()`; the original survives with `is_current = 0`. This is what
  makes "what did we know on date D" answerable, and it cannot be retrofitted.
- **Never restore the database by copying a file over it.** A job that loses its
  push resets to `origin/main` and MERGES its rows back with `merge_db.py`. A
  `cp` there replaces the whole file and destroys every row anyone else pushed
  while the job was collecting — it took 9,572 signal rows and the entire
  employer_identity cache on 2026-07-28/29, across five commits, without a
  single red run. The concurrency group does not make a copy safe and never
  could: it cannot see a human or an agent committing from a laptop, and a run
  it correctly queues is stale by exactly as long as it waited, because
  `actions/checkout` pinned the SHA at dispatch. Asserted by
  `tests/test_workflows.py`. The one exception is `correct-form-d.yml`, which
  edits rows in place rather than appending a revision, so a merge would
  silently skip its corrections; it rebases and goes red instead, and says so.
- **Never dispatch a database writer directly. Queue it.**

  ```bash
  gh workflow run drain-writers.yml -f enqueue=correct-form-d.yml \
       -f inputs_json='{"dry_run":"false"}' -f reason='why'
  ```

  Every writer shares the `talent-collect` lock, and GitHub keeps exactly ONE
  pending run per lock. Dispatching past a run that is already waiting **evicts
  it**: it ends `cancelled` having created no jobs — no steps, no logs, no
  annotation, nothing anywhere saying work was lost. Thirteen writer runs went
  that way on 2026-07-28/29, and every one was reported as "queued". A
  *scheduled* `collect` run evicted one too, so this is not only an
  agent-dispatch problem.

  `drain-writers.yml` dispatches the next ticket only into an **empty** group,
  so there is never a second pending run and queued work cannot be evicted. It
  is deliberately NOT in `talent-collect`: a drainer that queued behind the lock
  could never drain it. Work waits in `data/writer_queue.json`, which is
  committed, and `ops_status.py [2b]` is where you see it.

  Anything dispatched directly can still be evicted, so every tick reads the run
  list and shouts about any writer run that ended cancelled with zero jobs. A
  displaced **workflow_dispatch** run cannot be replayed — GitHub does not
  expose its inputs — so it is recorded as an orphan and stays listed until a
  human decides. A displaced **scheduled** run carries no inputs and its next
  cron repeats the pass, so it is recorded and auto-resolved (the decision a
  human typed by hand for the 2026-07-29 collect eviction, made structural).
  Since 2026-08-02 a needs-human item reds the drainer ONCE when first seen
  (plus once per ignored 24h), not on every 15-minute tick — the week before,
  one unhandled item was 180 red runs and as many GitHub emails. Muted items
  stay listed in the tick log, `writer_queue.py status` and ops_status [2b],
  and ci_alert skips the drain-writers RECOVERED mail while any remain.
  Resolve with:
  `gh workflow run drain-writers.yml -f resolve=<run_id> -f reason='why'`.
  **Never guess the inputs of a lost run**: `correct-form-d` and
  `correct-sec-pillar` both default to `dry_run=true`, so a re-dispatch with
  defaults is a green run that changes nothing.

  **A green drain tick is not a moving queue.** On 2026-07-30 a ticket carrying
  `slice`, an input `backfill-gdelt-2026.yml` does not declare, was refused
  `422` by the dispatch API; `set -e` killed the step before its own requeue, so
  the ticket sat in state `dispatched` bound to no run, nothing was ever in
  state `queued` again, and eleven consecutive ticks dispatched nothing and
  exited 0. Only pass inputs a workflow actually declares — `enqueue` now
  refuses the rest outright — and read `idle_since` in `ops_status.py [2b]`,
  which goes red after 90 minutes of work waiting on an empty lock group.
  **Count the ticket STATES, never the list length**: that file keeps landed
  work as history, and `resolve` marks an orphan in place rather than removing
  it, so "24 tickets" was 22 landed, 1 acknowledged failure and one live.
- **A dead source link is never fixed by editing the row.** The whole promise is
  that every update links to the document behind it, so a link that dies turns a
  sourced claim into an unsourced one while the page looks unchanged.
  `link_check.py` records the state (status, final URL, date) in `source_links`,
  keyed on the URL and never on the row; `archive_sources.py` gives each cited
  document a Wayback fallback so the evidence outlives the publisher's copy;
  `ops_status.py [2c]` and the weekly digest surface both. Nothing deletes or
  retracts automatically. The dangerous case is not a 404 but a **drifted
  domain**: a cited URL that now answers 200 from somebody else's site
  (`botswanaguardian.co.bw` became a betting site), which is why the checker
  reuses the collector's domain-drift guard rather than trusting status codes.
  Both cost nothing: no model is called. Both are **scheduled since 2026-07-30 —
  from `schedule-link-hygiene.yml`, never from a cron of their own.** They are
  database writers, so a `schedule:` in their own files would enter the
  `talent-collect` lock uncoordinated and either evict the pending run or be
  evicted and become an unreplayable orphan; the scheduler writes a *ticket*
  instead and `drain-writers.yml` dispatches it into an empty group.
  `ops_status.py [2c]` is the authority on this, not this line, and it goes red
  if either workflow ever grows a cron. **A captured snapshot reaches a reader
  only through `/enrich`**: `archive_url` is not a field of Signal (the row is
  built at classification time, the snapshot is taken afterwards), so it travels
  on the ENRICHABLE path, and `enrich.yml` is scheduled from the same file for
  that reason — without it the archiver fills the local ledger while every
  reader still sees the publisher's link alone. **Do not replace either with a
  WordPress broken-link-checker plugin** —
  those crawl post content, our source links live in `wp_tit_signals`, and it
  would paint a green badge over an entirely unchecked corpus.
- **Normalise through fixed vocabularies.** Nothing freeform is stored. A value
  that will not normalise is a rejected record, not a new category.
- **A collector returning zero is `degraded`, not `ok`.** Silent zero is how
  you discover in month three that a source died in month one.
- **Layoffs are NOT collected here.** They are read from the sibling's public
  API at render time. One source of truth per fact.
- **Coverage is earned.** A market in the registry is not a covered market. It
  is covered when it has a working connector, a health check and a passing
  test. `candidate_official_sources` is the roadmap, and must never render as
  the present.
- **Coverage is measured, not asserted.** `measure_recall.py` runs weekly
  (`recall.yml`) against a sealed gold set assembled from public sources
  WITHOUT consulting our own database, and publishes the result including the
  categories where we come off badly, at `/talent-intelligence-tracker/recall/`.
  It emits `data/recall_worklist.json`: the countries that held nothing and the
  document types under-delivering. That is the feed roadmap, and a country
  scoring zero is an instruction rather than a statistic.
  - **Never rebuild a gold set out of what is easy to find.** That is how the
    number climbs while coverage does not. `REQUIRED_SHAPE` in
    `analysis/recall/goldset.py` rejects a set that is too small, too US, too
    large-event, or built from one kind of document. Assembling each new set is
    a human step by design, and the page says so.
  - **Never re-use one set forever.** It converges, and then it measures memory
    rather than reach. The run detects that and asks for a replacement.
- **Don't claim "100% automated."** Scraper repair, novel-source judgement and
  assembling each new recall gold set are human. Say ~99% and name the sliver.

## Cost discipline

Budget is **$10/month** (`spend.MONTHLY_ALLOWANCE_USD`, the owner's number:
$10 on 2026-07-29, $25 on 2026-07-30, $5 on 2026-07-31, $10 on 2026-08-01), all LLM.

**$10 STILL DOES NOT FUND FULL COVERAGE, and that is the honest state of
this project rather than a bug to tune away.** `cost_projection.py [5]` at the
$5 ceiling: the LLM gate alone costs **$4.41/month**, leaving $0.59 for
read-throughs — 14 reads/day against a demand of 1,102/day, which is **1% of
full coverage**, and per-source caps of `1` for both google_news and
national_press. Full coverage is $49.14. Tuning caps cannot close a gap that
the gate has already spent.

So the road to $5 is **making the gate free**, not rationing reads: replace the
paid LLM gate with a trained classifier (`docs/PLAN-gate-to-five-dollars.md`,
steps 2-5). That needs labelled gate decisions, which `pipeline/gate_ledger.py`
records — see the warning on that module before assuming it is collecting them.

Until the gate is free, `spend.py --degrade` is what keeps the promise: it
switches paid reads off partway through the month and lets every free
collector, the free prefilter and both dedup layers keep running. Degraded is
the DESIGNED state at this ceiling, not an incident. Do not raise the allowance
to make it stop.

The $5 holds at all because: candidates are
keyword-gated before the model sees them; already-seen URLs are skipped *before*
any spend; the classification prompt is deliberately tiny; the read-through is
bought LAST, only for a record both dedup layers have already agreed will store;
and a `402` raises `CreditsExhausted` and stops the run instead of burning a
batch. There is a hard spend cap on the OpenRouter key itself — that is what
makes it a guarantee rather than a hope.

**The budget is a RATION, and saying so is the honest part.** Reading every
story that survives the gate — full worldwide coverage — costs **$100.99/month**
at current models, and **$59.29** with the conditional second pass. $5 is not
reachable: the GATE alone is $5.70 and is how we know what is worth reading. So a per-run cap decides how much gets read and
`pipeline/candidate_rank.py` decides WHICH, giving every country's best story a
place before any country's second. Do not quote a cost figure from memory:

```bash
python3 cost_projection.py     # exits 2 when full coverage does not fit
```

**Hitting the ceiling degrades, it does not halt.** `spend.py --degrade` sets
`TIT_PAID_READS=off`; the free collectors, the free prefilter, deterministic
extraction and both dedup layers carry on, and every candidate that would have
cost money defers UNMARKED for a later run. `--enforce` (a hard stop) is only
for `tripwire.yml`, whose sole action is a paid query — it once took every
collect job red at $9.47 of $10 and stopped a month of free collection with it.

## Before you touch it

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/pytest -q                                  # offline tests
.venv/bin/python run_collect.py --dry-run --offline  # whole pipeline, no spend
```

**Dependencies are hash-pinned. Never `pip install` a name.**
`requirements.txt` and `requirements-dev.txt` are the human-edited INPUTS:
floors, for a resolver to read. `requirements.lock` and `requirements-dev.lock`
are the resolved outputs, exact versions, every package hash-pinned
transitively, and they are what every workflow installs with `--require-hashes`
so pip refuses anything the lock did not vouch for. Two locks, because a
twice-daily collect run should not install a model-training stack: the dev lock
adds pytest and scikit-learn, and `tests.yml` and `gate-classifier.yml` are its
only users.

This is not hygiene. These runners hold `TIT_API_KEY` and
`OPENROUTER_API_KEY`, they run unattended, and a floor with no lockfile means a
scheduled job resolves fresh at run time with nobody reading what it picked.
One malicious release of any transitive dependency executes with both keys and
nothing in any log looks wrong. `tests/test_dependency_pinning.py` fails on a
bare install, an unhashed pin, a workflow naming a lock that does not exist, or
the two locks disagreeing about a shared package.

**The ritual when a dependency changes:**

```bash
python3 -m venv /tmp/lock && /tmp/lock/bin/pip install pip-tools
/tmp/lock/bin/pip-compile --generate-hashes --strip-extras \
    --output-file=requirements.lock requirements.txt
/tmp/lock/bin/pip-compile --generate-hashes --strip-extras \
    --output-file=requirements-dev.lock requirements-dev.txt
```

Then **read the diff**. A lock refresh nobody read is the unpinned state with
extra steps. `tests` runs on every push and installs from the dev lock, so a
lock that does not resolve on the runner goes red there rather than in a
collect run. `pip install --upgrade pip` is banned for the same reason the lock
exists: it is an unverified download into the same runner, immediately before
the verified one.

`--offline` uses a captured fixture and a deterministic stub classifier, so it
proves the plumbing without a network call or a cent of spend. Never store
anything from a new source until its dry run looks right.

## Test gotcha

Never stub a real module into `sys.modules`. The fake persists and shadows the
real module for every test loaded afterwards, so tests pass alone and fail in
the suite. Stub only third-party network libraries.

## Bluehost / Cloudflare gotchas

Every one of these shipped as a bug on the sibling:

1. Send a browser-ish `User-Agent` on every request to the WP host —
   ModSecurity blocks `python-requests` outright.
2. The site URL is `https://asktherecruiter.com/blog`, never the bare domain.
3. FTP deploys bypass WP hooks. Bump `Version:` **and** the version constant on
   every deploy, and use that bump to trigger cache flush and migrations.
4. FTP deploys race mid-upload. A hard `require` of a not-yet-uploaded file
   fatals the whole plugin. Guard with `is_readable()` and a stub fallback.
5. `.txt` never reaches WordPress — Apache serves it from disk. Write real
   files for `llms.txt` and similar.
6. The host injects `no-store` on REST responses; strip it per-endpoint in
   `.htaccess`, written with probe-and-rollback (a broken `.htaccess` 500s all
   of `/blog`).
7. Cloudflare caches the page. Add a random query string when you need the
   origin's truth, and never put a WP nonce in a full-page-cached form.
8. Shared hosting 500s randomly under load. Any paging job must retry transient
   5xx and continue.
9. Verify live before claiming a deploy landed, and match the **commit SHA**,
   not "the latest run" — the run listed right after a push is usually the
   previous commit's.
10. **Autoptimize aggregates INLINE scripts, and `autoptimize_filter_js_exclude`
    only matches assets by path.** So excluding `plugin/assets` protects the
    file and *not* the `wp_localize_script` object it depends on: the file stays
    put, the inline object is swept into a bundle that loads after it, and a
    script opening with `if (typeof FOO === 'undefined') return;` returns on its
    first statement. Nothing errors and the page looks normal. This shipped
    here: every filter, region tab, quick view and sort on the live dashboard
    was inert from day one. Pass config on a `data-` attribute of the root
    element as well, and name the inline object in the exclude list.
11. **A green deploy proves an upload, not a render.** Check the deployed page
    for the markup and the behaviour, not just the version string. The run that
    hid #10 was green and the CSS was entirely correct.

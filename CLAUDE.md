# Talent Intelligence Tracker — orientation

Hiring-side talent market signals, sourced to primary documents, segmented by
city / region / country. Sibling project to the AI Layoff Tracker; the two
cross-link and share a host, but share no code and no database.

- **Live:** https://asktherecruiter.com/blog/talent-intelligence-tracker/
- **Repo:** https://github.com/dk-forge/talent-intelligence-tracker (public — keeps Actions minutes free, so never make it private and never commit a secret)
- **Sibling:** https://asktherecruiter.com/blog/ai-layoff-tracker/

## Start here, every session

```bash
python3 ops_status.py
```

Read-only, no deps, no keys. Prints what is actually stored, which collectors
are stale or degraded, and the honest coverage claim. Exit 2 means something
needs a human.

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
  list and goes RED on any writer run that ended cancelled with zero jobs. Those
  cannot be replayed — GitHub does not expose a dispatched run's inputs — so
  they are recorded as orphans and stay loud until a human decides:
  `gh workflow run drain-writers.yml -f resolve=<run_id> -f reason='why'`.
  **Never guess the inputs of a lost run**: `correct-form-d` and
  `correct-sec-pillar` both default to `dry_run=true`, so a re-dispatch with
  defaults is a green run that changes nothing.
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
  if either workflow ever grows a cron. **Do not replace either with a WordPress
  broken-link-checker plugin** —
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

Budget is ~$3-5/month (measured; see spend.py), all LLM. It holds because: candidates are keyword-gated
before the model sees them; already-seen URLs are skipped *before* any spend;
the classification prompt is deliberately tiny; and a `402` raises
`CreditsExhausted` and stops the run instead of burning a batch. There is a
hard spend cap on the OpenRouter key itself — that is what makes it a
guarantee rather than a hope.

## Before you touch it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest -q                                  # 1,807 offline tests
.venv/bin/python run_collect.py --dry-run --offline  # whole pipeline, no spend
```

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

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
endpoint), and it mails **RECOVERED once** on the next green run. An **evicted**
run is deliberately never alerted: this repo evicts runs by design, and
`ci_status.py` is what tells an eviction from a failure. That quiet is decided on
EVIDENCE, not on the conclusion string — a job killed by its own
`timeout-minutes` also reports `cancelled`, and it was invisible in both channels
until 2026-08-12. The listener now admits `cancelled` and `ci_alert.py` alerts
only when the job's check-run annotations carry "has exceeded the maximum
execution time of ...", which nothing but a self-timeout produces (hence
`checks: read`). Everything else returns 0 and says why. Do not narrow this back
to a conclusion-string filter: the ceilings PR #32 put on collect, collect-press,
deploy-plugin, retract and tests are generous precisely because hitting one used
to be silent. Do not "fix" the quiet on a repeat — an
alarm that mails eight times in an afternoon is one you learn to filter, and a
filtered alarm is the original problem in a new hat. `ci_status.py` shares
`ci_alert.extract_cause` so the dashboard and the email can never describe one
failure two ways.

**OPERATIONAL MAIL LEFT THE HOST ON 2026-08-20. IT GOES THROUGH RESEND NOW.**
`/alert` was a route on the host it reports about, and it calls bare `wp_mail()`
— which the Brevo plugin on this install replaces wholesale with the SUBSCRIBER
relay identity, so alarms arrived wearing the reader newsletter's face. Two
defects, one route. `opsmail.py` (stdlib, Resend, `RESEND_API_KEY`) now carries
CI alerts, the RECOVERED notices, the weekly `health_digest.py`,
`ci_noise_report.py`, the benchmark recall gap and the gate-classifier notices.
**The alarm no longer depends on the thing it monitors.**

ONE IDENTITY, and it matches the sibling tracker so one mail rule catches both:

    From:    Talent Intelligence Tracker Ops <ops@asktherecruiter.com>
    Subject: [Talent Intelligence Tracker] <anything>

The prefix is what the endpoint stamped, kept byte for byte including the
trailing space, because the owner filters on it. `OPS_MAIL_FROM` overrides the
sender and is READ IN `opsmail.py` AND NOWHERE ELSE; it travels with
`RESEND_API_KEY` in every workflow that carries one, because the day it is set,
a job that does not carry it keeps the old From and the owner has two identities
to filter. **Reader mail keeps its own identity and never gains the ops prefix.**
There is no subscriber relay in this repo today; if one is added it stays on its
own provider and its own budget, so a bad afternoon of red CI cannot eat an
allowance readers depend on.

ENUMERATING EVERY SENDER IS THE WHOLE LESSON. The sibling's port
converted three callers and left nine, and **nothing noticed, because a wrong
From line produces no error anywhere.** `tests/test_ops_sender.py` is what makes
that impossible to repeat: no module may build a request to the `/alert` route,
only reviewed helpers may touch the transport, `OPS_MAIL_FROM` has exactly one
reader, the prefix is stamped once by the transport and never by a caller, every
mailing job's workflow carries `RESEND_API_KEY`, and `ALERT_STATE_COMMIT` is set
where alarms are raised.

**The open/resolved ledger moved with it, into `data/alert_state.json`, and THE
CLAIM IS COMMITTED BEFORE THE SEND.** That ordering is the whole reason this is
not a downgrade. The endpoint's read-modify-write window was milliseconds; a
committed file read at checkout and pushed 30 seconds later is not, and two
runners that both read "nothing is open" would both mail. `git push` to main is
the compare-and-swap: the loser re-derives, finds the cause open and goes quiet.
Resend's `Idempotency-Key` is a second guard on the same transition — a
TRANSITION, not a cause, because a cause can open, close and reopen inside
Resend's 24-hour window and a cause-scoped key answers the genuine second alarm
with HTTP 409. Everything dedup promised still holds: one cause is one email,
RECOVERED fires once, the 14-day STILL FAILING window is unchanged.
`ops_status.py [2f]` now prints what is currently being SUPPRESSED, which no
session could read while that state lived in a WordPress option. Do not
"simplify" this by writing the ledger after the send, and **do not let the drain
re-rule a held alert** — `host_watch.drain` calls `ci_alert.deliver`, never
`post_alert`, or the ledger would swallow the alert as a duplicate of itself.

**A LIVE-DATA failure is deduped by INCIDENT, not by branch.** The scope is
`workflow:branch` for a code failure — a test that fails on one branch only is
that branch's defect and must not hide inside main's alarm. An assertion naming
a `published_figures` check reads asktherecruiter.com, not the checkout, so
every branch sees the same one wrong published number and the branch that
noticed is noise. Those raise and clear under a branch-free
`<workflow>:live.data` scope, keyed on `live_data_identity()`, whose vocabulary
is read from `published_figures`' OWN registry so a rename cannot silently
return it to one email per branch. Do not broaden this to non-live failures, and
do not collapse two checks into one key.

**A pull-request failure on a non-main branch is ROUTED, not emailed.**
`ci_alert.route_to_ops_status` sends it to `ci_status.py` instead of the inbox:
it is still red in GitHub, still red in the pull request, still blocks the merge,
and a session is standing right there holding it. Four things it must never
catch, each of them a test: any failure on main, a live-data incident from any
branch, a scheduled or pushed run anywhere, and a RECOVERED notice. A routed
raise writes NOTHING to the ledger, so it orphans nothing and is owed no clear.

The outbox survives all of it, because a relay can be down too. Three rules
still, unchanged in substance:

- **An undeliverable alert is HELD, not lost.** `ci_alert.py` retries transient
  failures in-run, then writes it to `data/alert_outbox.json` (committed).
  `host-watch.yml` drains it every 15 minutes, and **the drain is no longer
  gated on the host answering** — delivery has nothing to do with Bluehost now,
  and waiting for a host probe would re-couple the two. An empty outbox makes no
  request at all, which is why that tick stays free.
- **A delivery failure is NOT a red run.** Holding exits 0. The only non-zero
  left is "could neither deliver NOR hold". **Do not restore the old `exit 1` on
  a failed POST**: that is what let one outage manufacture red runs which
  manufacture alerts which also fail.
- **`host-watch.yml` still opens ONE GitHub issue per sustained outage.** It is
  no longer the only channel that works during an outage — email works now — but
  it is still the record, and opening and closing it are two emails while every
  update in between mails nobody.

THE HISTORY, because it is why all of the above is shaped this way. On
2026-07-31 Bluehost 504'd under `/blog/` for seven minutes: enrich failed,
drain-writers correctly went red, and the alerter then failed four times saying
"HTTP 504 from /alert" — mute at exactly the moment it was needed, and
manufacturing four extra red runs while it was.

**The original coupling, kept because it is the argument.** `/alert` was a route on the host it reports about, and on 2026-07-31 that was
the whole defect. Bluehost 504'd under `/blog/` for seven minutes: enrich
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

**A NEW code-shaped red on main can also propose its own fix — as a DRAFT a
human merges.** `.github/workflows/self-heal.yml` + `self_heal.py`: the gate
refuses every red that is an alarm working as designed (drain-writers' red-once
signal, the live contrast audit, landmarks/recall, evictions, budget stops,
guardrail findings awaiting adjudication, host outages, branch reds). **A
SELF-TIMEOUT IS NOT in that list and is healed** — it wears the same
`cancelled` conclusion an eviction does, and until 2026-08-18 this gate refused
`cancelled` wholesale while `ci_alert.py` was mailing the very same events as CI
SELF-TIMEOUT. One definition now serves both: `ci_alert.self_timeout_of_run()` /
`is_self_timeout_cause()`, called by the gate, never re-implemented. For the
rest, the pinned
claude-code-action reproduces the failure from the run log and opens a draft PR
with red-before/green-after evidence, an adversarial second pass posts a review
comment, and the `guard` job re-diffs the branch against `self_heal.FORBIDDEN`
(data/, spend.py, budget.py, guardrails.py, the locks, HANDOVER, itself) and
goes red on a violation. It never dispatches a workflow and is deliberately
NOT in the talent-collect lock. One healer at a time, one open PR per cause,
ceiling 3.

**It MERGES its own draft when every condition holds** (owner authorization
2026-08-14): guard passed, reviewer verdict exactly
`SELF-HEAL-REVIEW-VERDICT: LOOKS SOUND` (absent or ambiguous is not),
source/test-only diff (never `.github/`, never a FORBIDDEN path), and a
merged preview that introduces no test failure main does not already have.
Every UNKNOWN resolves to "stay a draft". Each auto-merge then appends
`docs/HEALING-LOG.md` (the revert index: `git revert <merge sha>`) and a
narrative TECHLOG entry, best-effort — recording can never fail a heal.
Dormant until the `CLAUDE_CODE_OAUTH_TOKEN` secret exists. Kill switches:
`SELF_HEAL_AUTOMERGE_DISABLED=true` keeps the drafts and returns the click to
a human; `SELF_HEAL_DISABLED=true` stops the healer. TECHLOG 2026-08-14 is
the full design.

## The 60-second model

```
collectors/   one file per source. Returns raw dicts. NEVER writes.
pipeline/     classify -> validate -> dedupe -> store. Shared by every source.
data/         talent_intel.db + talent_intel_cache.db, both committed.
              The repo IS the memory. Two files since the 100 MiB split;
              connect() ATTACHes the second, so no query names it.
source_registry.py   markets, tiers, search vocabulary — all as data
analysis/     measurement, never collection: recall/ grades what we hold,
              tripwire/ finds what we are missing (run_tripwire.py, DORMANT)
```

GitHub Actions cron collects daily, commits the database back, and POSTs to a
keyed WordPress endpoint that renders the dashboard. The plugin exists and is
deployed; `wordpress-plugin/` is it.

**Collection is ARMED.** `collect.yml` runs ONCE daily at 22:00 UTC (6 PM US
Eastern in summer, AFTER the 4 PM close) and `collect-press.yml` an hour behind
it at 23:00. It was 16:00 until 2026-08-18 ("collect: run after the close, not
before it"), and this paragraph still said 16:00 afterwards while
`data/ingest-schedule.json` said the same, which is what put `tests` red on
main and printed the wrong "Next run" time to every reader. The generated file
is the reader-facing copy of this cron: change one and run
`python3 generate_ingest_schedule.py` in the SAME commit. It was 06:00 and
18:00 until 2026-08-14, when the owner traded the second run for its cost: the
product updates daily, a 24h freshness window is acceptable, and the second run
measured ~$0.43/day. Disarm by commenting the schedule line out again; nothing
else changes. `ops_status.py` is the authority on this, not this file — and
neither is any tool that hard-codes a cadence. `cost_projection.py` did, kept
saying 2, and printed double the real bill for four days
(TECHLOG 2026-08-18); it now counts the workflow's own live crons.

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

**What the page RENDERS AS is checked by a browser, not by reading CSS.**
Every other front-end guard here reads CSS, PHP or a version string as text. On
2026-08-11 that was the whole defect: a rule stored in the WordPress database,
attached to WordPress core's `wp-block-library` handle and in NEITHER repo, sets
`color:#2a2a2a !important` on `.entry-content p` (and `#1a1a1a` on h2, `#222` on
h3), which beats every token this plugin owns. In light it is 14.6:1 and
invisible. In dark it put 62 text elements on the dashboard at about 1:1, plus
the post title and every navigation label via the theme's
`--wp--preset--color--contrast:#111`. So `contrast_audit.py` loads the bare url
in real headless Chrome (`cdp.py`, stdlib only, no playwright in a runner that
holds keys) and reads the computed colour of every text element composited
against its real background, in four theme combinations at 1280 and 375.

- `tests/test_rendered_contrast.py` is the per-push half: it renders the shipped
  stylesheet against a local reproduction of the site override, needs no
  network, and proves the audit still FAILS when each fix is taken back out.
- `.github/workflows/contrast-audit.yml` is the live half: daily plus
  dispatchable, plus a step at the end of `deploy-plugin.yml`. **It measures the
  live site, and the deploy here is a human step, so between merging a CSS fix
  and running the deploy this job is RED and that red is correct.** Do not
  disarm it for a green board.
- Do not fix a contrast defect by moving a token. The tokens were correct the
  whole time; a custom property cannot win an argument it is not in. Raise the
  plugin's own declaration to the site rule's own scope plus `.tit-wrap`, one
  class more specific and never wider.

**THE REPOSITORY IS THE BACKUP, AND SOMETHING EXERCISES THAT CLAIM NOW.**
`data/talent_intel.db` is committed, so if the host disappears the rows are
still here and the site is rebuilt by clearing `published_at` and republishing.
That was true and unexercised until 2026-08-20. `backup_check.py` runs weekly
(`backup-check.yml`, Mondays 10:00 UTC, under a second, no model, no key) and
opens **the committed blob** rather than the working copy: `PRAGMA
integrity_check`, every table's row count against the last recorded reading in
`data/backup_check.json`, and the restored schema against `publish.FIELDS`. It
is not a database writer and is not in the `talent-collect` lock; it commits one
ledger. **A table that shrank is a FAIL with zero tolerance**, because nothing
here deletes a row: the 2026-07-28 reset-and-copy commits took 9,572 rows across
five commits with no red run anywhere. A baseline run is UNKNOWN, never a pass.
`ops_status.py [6]` reads the ledger offline; a red run alerts through
`ci-alert.yml` like every other red run, and there is no new channel.

**THE BACKUP HAD A CEILING AND IT WAS 32 DAYS AWAY. The database is two files
now.** GitHub refuses any single file over 100 MiB in a push;
`data/talent_intel.db` was 78.8 MiB growing 676 KB/day, measured over the
fortnight to 2026-08-20. The limit is per FILE, not per repository, and that is
the whole fix: the caches and ledgers moved to `data/talent_intel_cache.db`
(`seen_urls`, `source_links`, `employer_identity` — 47% of the daily growth),
both halves stay committed, and each is far from the wall.

    before  talent_intel.db        78.8 MiB  676 KB/day   32 days
    after   talent_intel.db        56.7 MiB  358 KB/day  ~127 days
            talent_intel_cache.db  18.7 MiB  318 KB/day  ~275 days

**Both halves stay in git on purpose, and this is the part not to undo.** `git
push` is the compare-and-swap that makes `merge_db.py` safe against two runners
and a laptop; a release asset has no such thing and two uploads clobber, which
is exactly the 2026-07-28 failure that cost 9,572 rows. `git show
<sha>:data/...` is `backup_check.py`, `restore_lost_rows.py` and RECOVERY.md.
LFS moves 80 MiB per checkout onto a 1 GB/month quota this repo would spend in
a day. VACUUM was measured and buys nothing — the freelist was zero pages.

At runtime they are ONE database: `schema.connect()` ATTACHes the cache as
`cache`, SQLite resolves unqualified table names across attached schemas, and a
commit spanning both files is atomic. **No query anywhere changed.** What did
change is that everything must carry the PAIR, and every place that could
silently carry one half now fails loudly: workflows stage and `cp` both
(pinned by `tests/test_workflows.py`), `merge_db` refuses a missing half rather
than merging an empty cache over a full one, `connect_ro` refuses a missing
cache file, `ops_status` names it and exits 2, and `backup_check` extracts BOTH
blobs, unions their counts and grades push size on the larger. A legacy or
branch-written copy of a cache table sitting in `main` would SHADOW the real one
silently, so `connect()` moves it out on open.

**The migration lost 1,232 rows on its first attempt with the suite green, and
the lesson generalises.** `ALTER TABLE ADD COLUMN archive_probes INTEGER` is
nullable, `CACHE_TABLES` declares it `NOT NULL DEFAULT 0`, and `INSERT OR
IGNORE` skips a NOT NULL violation without a word — 1,232 of 6,496
`source_links` rows. `split_cache_db.py` now rebuilds each table from its OWN
stored `CREATE` statement and verifies the row count before dropping anything.
Coercing those NULLs to 0 would have been the quieter mistake: NULL is "never
probed", 0 is "probed and told nothing", and `archive_sources` reads the
difference. **Never move rows between two tables you did not prove have the
same shape, and never let `OR IGNORE` be the thing that decides.**

**This bought about four months and is NOT the end of it.** `signals` is the
growth now, it is the product rather than a cache, nothing here deletes a row,
and there is nothing left to move out — so do not answer the next alarm with
another split or another VACUUM. The durable answer is a dated frozen shard
(`data/archive/signals-<period>.db`, never rewritten, attached read-only),
which bounds the file pushed every day. **[docs/RECOVERY.md](docs/RECOVERY.md)**
is the 2am document: what to check first, how to get any past revision back
(both halves, same sha), the republish sequence, and the unsoftened list of what
is NOT covered (`wp_posts` and the whole blog, uploads, the WordPress install,
and the shared email subscriber list, which is the sibling plugin's and is
personal data that must never reach either public repo).

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
- **A real number off a real filing is not automatically the fact you want.**
  Every issuer filter asks WHO filed; you must also ask WHAT THE FIGURE IS. A
  Form D's "amount sold" is not money raised when the securities were merger
  consideration, when the filing is an amendment (the figure is cumulative
  since the offering's first sale, and the original D already carries the raise
  at its own date), or when the offering states no size and runs past a year
  (a running total over an open window). `sec_form_d.money_raised_exclusion` is
  the one home for those three, called by BOTH Form D routes. They were 24.8%
  of the published Form D rows and $23.55bn. **Do not gate any of them on the
  filer's free-text clarification** — two thirds leave it blank, and the
  largest that fill it in describe one figure that is part cash and part
  consideration, which no column can split.
- **"Money raised" has ONE definition and it lives in
  `pipeline/money_raised.py`.** Company-inbound capital raises only. A
  divestiture price, an acquisition price, an investor closing its own fund, an
  outbound spend, a government award and an announced pledge are all real news
  and none of them is money the employer raised. The verdict is written at
  write time into `money_basis`, which has THREE states: `company_raise`
  (summed), an excluding `deal_type` (not summed, and it says why), and NULL,
  which means NEVER EXAMINED and is not a quiet yes. **Every sum asks for
  `company_raise` by name** (`tit_money_where()`), never `NOT IN (...)`, or a
  future value and every unjudged row would land on the summable side by
  default. `funding_amount` and `funding_amount_usd` are NOT nulled by this —
  the figure is correct and belongs on the row; only its membership of a total
  changes. That is the difference from `capital_event.py`, which nulls the
  columns because a bond is not a funding round in any sense.
  **The defect was never a reading failure.** On 2026-08-20 the live total was
  $564.79bn and the model had labelled "Alibaba said to be selling gaming arm
  for US$1.5 billion" a `divestiture`, correctly, and the sum added it anyway,
  because no money query had ever looked at the column. Six money sums across
  three PHP files, none of them asking. Correcting it took $33.22bn out.
  `tests/test_money_raised.py` fails on any `SUM(... funding_amount_usd ...)`
  in the plugin that carries no basis clause — the guard that was missing, and
  the sibling tracker's documented "the surface that forgot the filter".
  `correct_money_basis.py --check` is the standing assertion that no live row
  carries an unjudged figure; a number there means a write path is skipping
  `validate.build_signal`.
- **A leading backer qualifier is not part of an employer's name.** `company_key`
  strips `<somebody>-backed|-owned|-led|-funded|-founded|-controlled` from the
  front. Both dedup layers require key EQUALITY, so "Thrive Holdings" and
  "OpenAI-backed Thrive Holdings" never met and one $2bn round was counted
  twice. Only a HYPHENATED participle, only from the front, and only when a
  name survives it: a bare leading-word strip is how the sibling tracker's
  "Revision Optics" would have keyed as `optics`. Changing `company_key` leaves
  history spelled the old way — `correct_company_key.py` is the backward half
  and derives its worklist by calling the function, so it needs no editing.
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
- **Coverage is measured PER POPULATION, and the populations stay separate.**
  `analysis/recall/family.py` is the ONE definition of which populations are
  measured and where each one's gold sets, results, page data and health entry
  live. `measure_recall.py --family <id>`, `ops_status.py [3e]`,
  `health_digest.py` and `includes/recall.php` all read it, so nothing can
  disagree about where a number came from. Two families today:
  - **`world`** — `analysis/recall/`, 169 events, cells are countries.
  - **`us`** — `analysis/recall/us/`, 51 events, cells are hiring markets. It
    exists because the worldwide set's US cell is 34 events of a set assembled
    to be global, which is an impression rather than a measurement of the
    American market. First result, 2026-08-11: **held 21/51, 41.2%, 95%
    interval 28.8 to 54.8**.

  They NEVER share a directory. `goldset.latest_path()` takes the newest
  `goldset-*.json` in a directory and `goldset-us-*.json` sorts after every
  worldwide set there will ever be, so one file in the wrong folder silently
  turns the published worldwide figure into a US figure with no code change and
  nothing in the diff saying so.
  `tests/test_recall_us.py::test_a_us_set_cannot_hijack_the_worldwide_measurement`
  is that guard, and it is the reason for the subdirectory.
- **Every rate is published with its interval.** `analysis/recall/stats.py` is
  the single Wilson implementation and `thresholds.wilson` re-exports it, so the
  floor and the page can never round the same interval two ways. This matters
  most on the smaller set: 21/51 is 41% and it is also anything from 29% to
  55%. The US metro cells are 8 to 16 events, where two cells forty points apart
  still have overlapping intervals, so **a metro cell is a work list and never a
  rate.**
- **The US set covers FUNDING ONLY, and that is a finding rather than a
  shortcut.** US leadership events at privately held employers could not be
  enumerated from original sources. Open web search returns executive-moves
  databases, which are discovery pointers and may never be cited. The only free
  chronological index left is SEC EDGAR full-text search, which is exactly what
  our own `sec_edgar` collector walks, so a set built from it scores the tracker
  against its own feed. Four independent research passes reached for EDGAR
  unprompted, all four came back over 90% exchange-listed filings, and all four
  were discarded. `US_REQUIRED_SHAPE["max_source_type_share"]` makes that
  discard mechanical rather than a judgement somebody has to remember.
  **A wire-dateline walk DID work** (searching a press-release service for the
  literal string `DENVER, June` and walking the results in date order) and
  produced 34 private-employer leadership rows across Austin and the rest of the
  country. They are the seed for the next US set and are NOT in this one: a
  wire-only block would put one document type at 60% of the denominator and
  leave the San Francisco and New York leadership cells empty.
- **Landmarks are named, and their absence is a check.** Recall measures a
  representative sample; it cannot notice one enormous event going missing, and
  on 2026-08-04 the three largest private rounds ever recorded were absent for
  months while every automated check was green. `data/landmarks.json` names 20
  such events per quarter with the company's OWN announcement behind each, and
  `check_landmarks.py` runs weekly (`landmarks.yml`), reports
  HELD / WRONG_AMOUNT / MISSING through **two lenses** (the committed corpus,
  and the public endpoint a reader actually sees), and commits
  `data/landmarks_report.json`. Three rules hold it up:
  - **Only a REGRESSION reds it.** Something previously held that is not held
    now. A never-held landmark is a standing gap, listed every week and never
    red, because a permanent red on a backfill backlog trains the next session
    to ignore exit codes.
  - **Stored is not live.** `held_not_live` is a real outcome: a correct row
    behind an unanswered publish guardrail is invisible, and two landmarks are
    in exactly that state today. Do not collapse the two lenses.
  - **Never grow the set from our own database, and never shrink it to make a
    number look better.** It is assembled by hand from public sources, and the
    entry floors in `analysis/landmarks/landmarks.py` fail the tests if
    somebody thins it. An emptied file would read "0 of 0 held, 0 regressions"
    and exit 0 for ever.
- **Don't claim "100% automated."** Scraper repair, novel-source judgement,
  assembling each new recall gold set and appending each quarter's landmarks
  are human. Say ~99% and name the sliver.

## Cost discipline

Budget is **$8.00/month** (`spend.MONTHLY_ALLOWANCE_USD`; $10 on 2026-07-29,
$25 on 2026-07-30, $5 on 2026-07-31, $10 on 2026-08-01, $18 on 2026-08-12,
$6.04 then $8.00 on 2026-08-13), all LLM.

**$8.00 is ONE HALF OF A STATED $22.00/MONTH TOTAL** across both trackers, the
other half being **$14.00** in the AI Layoff Tracker's own `railway/spend.py`.
Both halves are written down in `budget.py` (`MONTHLY_TARGET_COMBINED_USD`,
`SIBLING_ALLOWANCE_USD`, `DERIVED_ALLOWANCE_USD`) so a session reads the whole
instead of a fraction.

**A SHARE IS NOT A BUDGET, and that is why it is stated now.** For one day this
file derived its ceiling as $8.00 combined x 75.52% = $6.04, where 75.52% was
genuinely measured ($0.8020/day here on 2026-08-13, the first full un-degraded
day, against $0.26/day for the sibling). The arithmetic was right and the
budget was wrong: $6.04 here IMPLIED $1.96 for the sibling, whose own file said
$7.00, so the two trackers were set to $13.04 against a stated target of $8.00
and neither side could see it. A share only bounds a total if somebody enforces
the denominator, and nobody can across two repos. Do not re-derive a share
here; change a literal and check the sum.

**THERE ARE TWO POTS AND ONLY ONE CAN BE RAIDED** (`budget.py`, 2026-08-13).
`spend.py` answers "is the month spent", which is a question about a total, and
a total could not answer the one August posed: **whose spend was it**. Backfill
walkers took 88% of that month in 2.5 days, the single 90% line closed over
everything, and the scheduled collectors ran degraded from 08-03 to 08-12.

* **COMMITTED** ($5.37, 88.91%) — the scheduled jobs. Measured against their
  OWN pot, so no amount of catch-up spending can degrade them.
* **DISCRETIONARY** ($0.67) — the `backfill_*` family and `ab_models`. Per-run
  ceiling is `remaining / days left`, so a walker **slows** in a lean month
  instead of racing to the ceiling and stopping. Zero headroom is a SKIP that
  exits **zero** and says why; a red run there would manufacture an alert for
  the budget working.

The classification is **structural, never a list**: a workflow that runs on a
timer is committed, and that includes the ones whose cron lives in
`schedule-link-hygiene.yml` rather than in their own file (`tripwire.yml`,
`benchmark-diff.yml` — `budget.scheduled_workflows()` is the one rule).
Dispatch-only paid workflows export `TIT_RUN_KIND: discretionary`, which lands
on `source_health.run_kind` so the ledger has a split and not just a total.
Read the split with `python3 budget.py`; it is `ops_status.py [5]`'s one line.

The ledger is a **FLOOR**: jobs that call a model without filing a priced
health row are not in it (it holds $1.68 of August's $10.08+). `spend.py` has
the authoritative total from the key and reconciles; the unattributed remainder
is charged to DISCRETIONARY first, deliberately, because that errs toward
protecting the collectors.

**$8.00 STILL DOES NOT FUND TODAY'S CONFIGURATION, and that is the honest
state of this project rather than a bug to tune away.** What it costs to run
as configured is **~$11.5/month** (`cost_projection.py [4]`, first row),
against a committed pot of $7.11. The MEASURED figure agrees: the committed
cost ledger reads **$0.38/day = $11.57/month** over 2026-08-14..17, the first
four un-degraded once-daily days. Two independent methods, and they now agree
because the projection stopped assuming two collect runs a day (TECHLOG
2026-08-18). Do not quote the older $24.06 — it was the same configuration at
twice the cadence.

Bringing the committed set inside its pot is a read-cap decision
(`classify.BINDING_READ_BUDGET`) and it is the owner's.
`cost_projection.py [4]/[5]`, re-measured 2026-08-18 at the real cadence:

| configuration | cost/month |
|---|---|
| **today's caps, AS RUNNING (measured)** | **~$11.5** |
| FULL coverage, second pass conditional | ~$21.8 |
| ... extraction on gemini-2.5-flash-lite | ~$9.2 |
| ... all of it, on the cheapest models | **~$3.2** |

**Quote these as approximate and re-run the tool for today's figure.** Every
row is recomputed live from two rolling windows over the committed ledger, so
$0.15 of movement between two readings is drift and not disagreement. A
session that reads $11.67 where this says $11.54 has read a slightly newer
ledger, not a bug.

**THE gemini ROW DOES NOT FIT, and this file said it did for one day.** The
claim was "$6.17 against $6.82, for the first time" — a coverage decision the
owner had never been offered. It rested on `CONDITIONAL_SHARE = 0.12`, a
hand-set constant the ledger contradicts outright: 396 of 1,215 storing
records buy a paid sentence, which is **32.6%**. Deriving it from the meter
(2026-08-18) moves that row to ~$9.2 against $6.82. Nothing got more
expensive; the estimate was flattering every "second pass CONDITIONAL" figure
and under-pricing a read in `[5]` by ~18% — and `[5]` is where the recommended
`TIT_READTHROUGH_CAP` values come from. **Do not restore the constant to make
the row fit.**

**`[4]`'s FIRST row is the bill; every row under it is a configuration that is
not running.** The two "today's caps" rows were labelled backwards until
2026-08-18: read-late shipped, the ledger's two ratios swapped meaning, and
the names did not follow, so the tool called the real bill "before read-late"
and gave a hypothetical nobody has run the name "today's caps, WITH
read-late". Reading the second as the current state over-budgets by ~42%.

It is bounded by the gate either way: **~$1.71 of every figure above is the
LLM gate**, which no model swap reaches. Tuning caps cannot close a gap the
gate has already spent.

So the road under $5 is **making the gate free**, not rationing reads: replace
the paid LLM gate with a trained classifier (`docs/PLAN-gate-to-five-dollars.md`,
steps 2-5). That needs labelled gate decisions, which `pipeline/gate_ledger.py`
records — see the warning on that module before assuming it is collecting them.

**AND BEFORE THE GATE IS REPLACED, IT HAS TO BE MEASURED.** Until 2026-08-14
this repo knew what the gate COST and not whether it was RIGHT: every mode of
`ab_models.py` scores a challenger's AGREEMENT with the incumbent, which is
blind when both are wrong and reads a correction as a regression.
`analysis/models/gate_goldset.py` is the ground truth — 80 real captured items
hand-labelled against `classify.GATE_SYSTEM`, of which 75 are scoreable and 5
are ambiguous and excluded rather than counted as passes. Graded **for free**
against the verdicts the ledger already holds, the live gate scores **32/36 =
88.9% (Wilson 95% 74.7-95.6)**, recall 95.0%, precision 86.4%. Run
`python3 -m analysis.models.gate_goldset` (offline) or `ab_models.py
--gate-gold` (paid, discretionary pot). **Read `KNOWN_LIMITS` before quoting
any of it**: the set is English-only against a gate that answers in 43
languages, it is positive-heavy so it measures recall far better than
precision, and at 75 items it can reject a model but cannot certify one at 98%.
Extend it; do not re-derive it.

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
python3 budget.py              # the two pots, offline, no key
python3 cost_projection.py     # exits 2 when full coverage does not fit
```

`cost_projection.py` now sizes against the **committed pot**, not the whole
allowance: the caps it recommends are for the scheduled collectors, and they
cannot spend the walkers' pot.

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

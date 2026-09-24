# Runbook: collection

For a future session with no context. Each collector's schedule, where it runs,
how you know it is healthy, how it breaks, and the exact fix. Written
2026-09-24 alongside the self-heal slice (TECHLOG 2026-09-24, "coverage and
self-heal"). The schedule table in README.md is generated from the crons
(`python3 readme_facts.py`), so trust that table over any time written here.

## First five minutes

```bash
python3 ops_status.py                      # data: stale, never-ran, zero-store streaks
python3 ci_status.py                       # runs: what is red, what was evicted
python3 collection_schedule.py --plan      # missed weekly/monthly slots, never-ran sources
python3 writer_queue.py status             # tickets waiting for the writer lock
```

`ops_status.py` exit 2 means something needs a human; `ci_status.py` exit 3
means it could not check (no `gh`, no token, no network), which is not a pass.

## How the self-healing fits together

| Mechanism | File | What it does | What it cannot do |
|---|---|---|---|
| Runner fallback | `runner_pick.py`, `runner-heartbeat.yml`, `pick-runner` job in each collector | The heartbeat succeeds on the box every 30 min. Each collector's `pick-runner` job (on `ubuntu-latest`) reads the last successful heartbeat. More than 2h old means the collector runs on `ubuntu-latest`. | It cannot see a box that is up but broken mid-job. If no heartbeat has ever run, or the API errors, it keeps the box (the status quo). |
| Force a runner | repo variable `TIT_RUNNER` = `hosted` or `self-hosted` | Overrides the decision for every collector. | Clear the variable afterwards, or it stays forced. |
| Missed-slot catch-up | `catch-up.yml` (daily 06:17 UTC, off-box), `collection_schedule.py --enqueue` | Any weekly or monthly structured source that is more than cadence + 1 day behind, or has never run, gets a `writer_queue` ticket. `drain-writers.yml` (every 15 min) dispatches it when the lock is free. At most 2 a day. | It covers `collect-structured.yml` sources only. Daily sources catch up on their next day, and `ats_boards` is perishable (a missed day is lost). |
| Never-ran detection | `ops_status.py [2]`, `collection_schedule.never_ran` | Takes the configured sources from the workflow crons and flags any with no `source_health` row. | Dormant sources (`denmark_cvr`, `israel_registrar`) have no cron, so they are correctly excluded. |
| Zero-store issues | `collection_schedule.py --issues` in `catch-up.yml` | Opens one GitHub issue per configured source whose last 3 runs stored 0 (marker `<!-- zero-store:SOURCE -->`). The body is edited in place, and the issue closes itself when the source stores again. | It judges stored rows, not quality. |
| Guardrail split | exit code 4 in `collect`, `collect-press`, `collect-structured` | An overdue guardrail finding turns only the `guardrail-overdue` job red. The collection's own status stays honest. | It does not answer the finding. Run `python3 guardrails.py`. |
| Transient LLM errors | `pipeline/classify.py` | Retries, then `Throttled`. The candidate defers unmarked. | – |
| Spend ceiling | `spend.py --degrade` | Past 90% of the allowance, paid reads switch off and the free stages keep running. | – |

**Never dispatch a writer directly** with `gh workflow run` while anything
holds the `talent-collect` lock. GitHub keeps one pending run per group, so a
direct dispatch can silently displace the one already waiting. Queue it
instead:

```bash
python3 writer_queue.py enqueue collect-structured.yml \
  --inputs '{"source":"edinet_japan","days":"14"}' --reason "manual catch-up" --by you
git add data/writer_queue.json && git commit -m "queue: edinet catch-up" && git push
```

## Per collector

"Health signal" means the `source_health` row. Read it with `ops_status.py`,
or directly with
`sqlite3 data/talent_intel.db "select * from source_health where collector='X' order by run_at desc limit 5"`.

### `google_news` (collect.yml, daily 22:00 UTC, paid)
- **What it runs:** the en-US anchor (`GOOGLE_NEWS_QUERIES`) plus 4 rotating non-English editions (`GOOGLE_NEWS_VOCAB` and the thin-pillar groups from `pipeline/pillar_vocab.py`), a city-led slice, and 3 English markets by name (`english_market_queries`).
- **Healthy:** `stored > 0` on most days, and no `UNRESOLVED` in `detail`.
- **Failure modes and fixes:**
  - *EU consent wall* (2026-09-17..21: `UNRESOLVED n/n ... consent wall`, 0 stored). Check that `CONSENT_COOKIES` in `collectors/google_news.py` is still accepted. The quick fix is `TIT_RUNNER=hosted` (US egress).
  - *OpenRouter dropped connection* (09-22). This should now retry and throttle. If `FETCH FAILED` / `ConnectionError` reappears, check `pipeline/classify._call`.
  - *Zero-store streak* (`ops_status`: `ZERO-STORE STREAK`, plus the issue). Read `detail`, then the seen ledger (`talent_intel_cache.db` `seen_urls`): bare homepages mean resolution broke.
  - *Everything filtered.* A new query term that the prefilter does not know. Pillar phrases are shared with the gate, and `tests/test_pillar_vocabulary.py` enforces that. Other new terms need a prefilter entry.

### `gdelt`, `sec_edgar`, `sec_form_d` (collect.yml sweep, daily 22:00 UTC)
- **gdelt:** `found = 0` for days in a row on the box (09-17..20) is suspected egress blocking. Try `TIT_RUNNER=hosted` for one run and compare.
- **sec_edgar** (8-K 5.02) and **sec_form_d:** these need a descriptive User-Agent, which is set in the collector. `403` from SEC means the UA or the rate. A thin row count (single digits a week) is normal.
- One source failing does not stop the others in the loop. The step goes red at the end.

### `national_press` (collect-press.yml, daily 23:00 UTC, paid)
- **Healthy:** `data/national_press_health.json` shows `live/feeds` high. The run log's "Per-feed health" lists the dead feeds.
- **Failure modes:**
  - A dead feed, `stale`, or `robots`: retire it or fix its row in `data/sources_catalogue.csv`. Business in Vancouver and Actu Cameroun were retired 09-24; BusinessWorld returns intermittent 5xx.
  - A red run that is only an overdue guardrail now goes to `guardrail-overdue`.

### `ats_boards` (collect-structured.yml, daily 09:00 UTC, perishable)
- **Healthy:** runs daily, and `data/ats_board_state.json` is committed each day. A missed day cannot be back-filled.
- **Fix:** if the box was down, the fallback should have moved it. If the run was cancelled with zero jobs, that is eviction (`ci_status.py`), so re-queue it through `writer_queue`.

### Weekly registries (collect-structured.yml, 04:00 UTC)

| Source | Day | Key | Back-fill input |
|---|---|---|---|
| `bse_india` | Mon | none | `days` → `TIT_BSE_DAYS` |
| `edinet_japan` | Tue | `EDINET_API_KEY_JP` | `days` → `TIT_EDINET_DAYS` |
| `opendart_korea` | Wed | `OPENDART_API_KEY_KR` | `days` → `TIT_DART_DAYS` |
| `companies_house` | Thu | `COMPANIES_HOUSE_API_KEY_UK` | `days`, `ch_slice`, `ch_min_size` |
| `czechia_ares` | Fri | none | `days` → `TIT_ARES_DAYS` |
| `estonia_ariregister` | Sat | none | `days` → `TIT_EE_DAYS` |
| `spain_borme` | Sun | none | `days` → `TIT_BORME_DAYS` |

- **Healthy:** a row within ~7.5 days (the `staleness.MAX_AGE_HOURS` leash of 180h).
- **Missed slot:** `catch-up.yml` queues it the next morning. To widen the window by hand, enqueue it with `days` set to the gap plus 7.
- **Key failures are loud by design.** EDINET answers an empty key with HTTP 200 and `StatusCode 401`, and the collector refuses an empty key. OpenDART answers a bad key with `status 010`. A Companies House *streaming* key is not a REST key, so it returns 401 forever. Only the owner can rotate keys (repo secrets).

### Monthly (collect-structured.yml)
- `sec_execcomp` (5th, 09:30), `uk_paygap` (6th, 10:30), `singapore_acra` (7th, 10:30).
- `singapore_acra` had **never** recorded a run as of 2026-09-24. It now shows up in `ops_status` as NEVER and gets queued by the catch-up. If it then fails, read its run log: data.gov.sg hands out a signed download URL, and a change to that flow is the likely break.

### Dormant by design: `denmark_cvr`, `israel_registrar`
These have no cron and no health row, and that is correct. Arming either is a
repository-variable change plus a cron line, and `staleness.MAX_AGE_HOURS`
tightens in the same change. See the header of `collect-structured.yml`.

### `tripwire` (ticket from schedule-link-hygiene.yml, Mon and Thu 07:00, paid)
- It uses `OPENROUTER_API_KEY` for search-backed queries (about $0.006 per query). Cost is capped by `planner.MAX_QUERIES_PER_RUN`.
- **Healthy:** a row within 7 days.

### `recall`, `recall_us`, `recall_eu` (recall.yml, Mon 08:00)
These are benchmarks, not collectors. `degraded` means recall fell below the
target, not that the job broke. Read `data/recall*_worklist.json`.

## Runner outage checklist

1. Run `python3 ci_status.py`. Is the self-hosted runner offline, or are jobs hanging in `checkout`?
2. Check that `runner-heartbeat.yml`'s last success is more than 2h old. If it is, the next collector run moves to hosted by itself. Confirm by looking for "falls back to ubuntu-latest" in the `pick-runner` log.
3. To move now, set the repo variable `TIT_RUNNER=hosted` and dispatch through the queue.
4. Owner only: restore SSH to the VPS and restart the `actions.runner.*` service. The sibling repo's `vps-heartbeat.yml` shows service state. Clear `TIT_RUNNER` afterwards.
5. Missed weekly or monthly slots are queued by `catch-up.yml` the next morning. Check with `python3 collection_schedule.py --plan`.

## Cost notes

- Google News RSS, GDELT, SEC and the registries are free to fetch. Money is
  spent only on classifier reads, which are capped per run by
  `classify.READTHROUGH_CAP` (`TIT_READTHROUGH_CAP` in each workflow) and per
  month by `spend.MONTHLY_ALLOWANCE_USD`. Adding query groups adds fetches, not
  reads. The new groups compete for the same read budget.
- `run_collect.free_gate` drops job-ad aggregator hosts
  (`prefilter.JOB_AD_DOMAINS`) before any paid stage. If a new board shows up
  in rejected candidates, add its host there, together with a test.

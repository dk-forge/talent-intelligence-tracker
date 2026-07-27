# Handover — Talent Intelligence Tracker

**Read this first if you are a new session.** It is the current state of the
build, what is proven, what is broken, and what to do next. Keep it updated as
you go: it is the only thing that survives a crashed session.

Last updated: 2026-07-27, after company pages and the funding collector (v1.3.2).

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

---

## Current state (2026-07-27, evening)

**Live:** plugin **v1.9.0**, 13 records, 198 tests green.
`https://asktherecruiter.com/blog/talent-intelligence-tracker/`

**The page now:**
- Hero with a live/last-updated pill and four at-a-glance lines: today, this
  week, this month, this year. Empty periods still print and say so.
- Four stat tiles, each with its own accent stripe.
- Region strip: World / United States / Europe / India / Asia Pacific, with
  counts. Regions with nothing in them are dropped; World always survives.
- Three chart cards (kind of update, where the activity is, growing or
  shrinking), plain HTML and CSS, no chart library.
- Eight filters, then the table.
- **On phones (<=860px) the table becomes cards**, each cell labelled from
  `data-label`. Below 700px the wrap goes full bleed to cancel the theme's two
  nested padded containers, which otherwise left it 219px wide on a 375px screen.

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

**Not done:**
- Collection is still DORMANT (schedule commented out in `collect.yml`)
- Filters change the table but **not** the stat tiles or charts. The stated goal
  is "every number, chart and row below updates to match" — the charts are
  server-rendered from the unfiltered set and do not yet re-fetch.
- ~~Google News is `US:en` only~~ **Done (v1.12.0).** It reads 25 national
  editions across 7 languages, three rotating per run plus a fixed US anchor.

  **The trap, if you touch this:** rotating `hl`/`gl`/`ceid` alone does nothing.
  Measured 2026-07-27, the same English phrases returned US:en 23 items,
  DE:de 2, BR:pt 0 — and German phrasing returned 20 from that same German
  edition. Each edition must ask in its own language
  (`GOOGLE_NEWS_VOCAB` in `source_registry.py`), and `prefilter.py` needs the
  matching non-English terms or every candidate is dropped for free before the
  model sees it. A locale without a phrase set is a silent zero dressed up as
  coverage; `tests/test_locale_rotation.py` refuses to let one exist.

  Going multilingual took a run from ~25 candidates to ~215, so
  `DEFAULT_CANDIDATE_CAP = 40` now lives in `run_collect.py`. The cap is a fair
  share (one item per query in turn), not a head slice.
- No date-range control, no sort, no quick views
- Model switch (Gemini Flash-Lite gate + Haiku read-through) designed, not applied

---

## The one thing that took six hours to learn

**Google News RSS cannot give article URLs.** Its `<source url>` is the outlet
homepage, its redirect no longer resolves, and the real URL is not recoverable
from the encoded token (tested — it is not in there). Every record it produced
linked to a homepage, which is not a receipt for any claim.

**GDELT** returns real article URLs but its throttling is erratic and its yield
collapsed to zero on a live publishing run.

**SEC EDGAR is the source that works.** 8-K Item 5.02 filings are legally
required within four business days, always have a real `sec.gov` document URL,
are primary sources (so records earn `verified`), and SEC allows 10 req/s.
`collectors/sec_edgar.py`. Build outward from here, not from news.

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

---

## Secrets (all set, in GitHub repo secrets)

`OPENROUTER_API_KEY` (has a **$5 lifetime cap** — collection stops dead when hit),
`WP_API_KEY` (must match the key set in WP admin → Talent Intel),
`WP_SITE_URL` (must end `/blog`), `FTP_HOST`, `FTP_USERNAME`, `FTP_PASSWORD`,
`FTP_PORT`, `WP_PLUGIN_REMOTE_DIR`.

**No Railway.** A leftover service exists; it plays no part. Collection runs on
Actions because the SQLite DB must be committed back to the repo.

---

## Cost, measured not estimated

- Gate call: 141 tokens in / 35 out (measured)
- `deepseek/deepseek-chat` (current): ~$1.15/month at 660 items/day
- `google/gemini-2.5-flash-lite`: 90% agreement with incumbent, ~$0.56/month
- `anthropic/claude-haiku-4.5` matches Sonnet 5 on read-through quality at half price
- Spent to date: ~$0.42 of the $5 cap, mostly on the model A/B

Model A/B is reproducible: `ab_models.py`, workflow `ab-models.yml`.

---

## Next steps, in order

1. **Page overhaul** — human language (no "signals"/"Any direction"), all
   filters in the template, accessible palette (done in CSS, needs deploy)
2. **Spend ceiling in code** — track OpenRouter `usage` per call, stop at a
   daily allowance, log what was skipped
3. **More SEC coverage** — Item 1.01/2.01 (M&A), Form D (funding)
4. **Arm collection** — uncomment the schedule in `collect.yml`
5. **Roo-style status banner** — last run, next run, live/resting
6. **Company profile pages**, momentum score, the signal categories in the
   owner's vision (Growth / Money / People moves / Hiring / Shrinking)

---

## Rules that are not negotiable

- No source URL, no record. A homepage is not a source.
- The model never invents a number: figures must appear verbatim in `raw_text`.
- Confidence is earned by the source and never promoted.
- Never overwrite a record — append a revision (`store.revise`).
- Layoffs are NOT collected here; read the sibling's public API.
- Coverage is earned: a market in the registry is not a covered market.
- Never publish fabricated records to the live site, for any reason.

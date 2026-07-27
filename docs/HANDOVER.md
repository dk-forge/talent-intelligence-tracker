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

## Current state (2026-07-27)

**Working and live:**
- WordPress plugin v1.1.0 deployed via FTPS from Actions
- 6 records published, all SEC 8-K filings, all `verified` confidence
- REST API: `query`, `aggregate`, `facets`, `source-health`, keyed `add`/`bulk`/`retract`
- Filters live in the API: country, city, pillar, direction, confidence, company,
  industry, state, function, funding, min_headcount, free-text `q`
- 170 tests, CI green

**Done since first publish:**
- Colourblind-safe palette (blue/orange/violet, CVD dE 24.7 validated). The old
  green/red was the classic red-green failure case.
- WCAG AA contrast. NOTE: a `prefers-color-scheme: dark` block was added and
  then removed — the WP theme forces a white page, so dark styles rendered as
  light text on white. Do not re-add it without a real theme dark mode.
- Recruiter language: "updates" not "signals", "Hiring up"/"Cutting back"/"Pay
  change" not "direction", columns read "What happened / What it means / How solid"
- All eight filters on the page, responsive 1/2/3 columns

**Done since the overhaul:**
- `collectors/sec_form_d.py` — funding, free and primary-sourced. Form D is
  structured XML: issuer, industry, city, state, amount actually sold. The money
  figure is read off a legal filing, never produced by a model.
  Funds are excluded twice: by `industryGroupType`, and by name pattern (real
  estate syndications and DSTs file under "Other Real Estate").
- Company profile pages at `/talent-intelligence-tracker/company/{slug}/`.
  Slug is the hyphenated `company_key` — `%20` does not survive the rewrite.
  404 when we hold nothing, rather than a shell page for every slug.
- 9 records live: 6 leadership moves, 3 funding rounds, all `verified`.

**Two traps found building those:**
- The classifier prompt listed only hiring/leadership/comp/location as talent
  signals, so it silently discarded every funding filing. It was following
  instructions; the instructions were wrong. Funding is now explicitly in scope.
- `is_talent_signal: false` printed nothing at all. Every failure tonight hid in
  a silent path. It now prints per candidate.

**Not done:**
- Collection is DORMANT (schedule commented out in `collect.yml`)
- No spend ceiling in code
- Model switch (Gemini Flash-Lite gate + Haiku read-through) designed, not applied
- Roo-style status banner, company profiles, momentum score: not started

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

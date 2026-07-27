# Handover — Talent Intelligence Tracker

**Read this first if you are a new session.** It is the current state of the
build, what is proven, what is broken, and what to do next. Keep it updated as
you go: it is the only thing that survives a crashed session.

Last updated: 2026-07-27, after the visual overhaul, the source audit and the
multilingual Google News work (plugin v1.13.0).

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

### Verifying a deploy actually landed

A green Actions run is not proof. The run listed right after a push is usually
the *previous* commit's, so match the **commit SHA**:

```bash
SHA=$(git rev-parse HEAD)
gh run list --repo dk-forge/talent-intelligence-tracker \
  --workflow=deploy-plugin.yml -L 6 \
  --json headSha,status,conclusion \
  -q ".[] | select(.headSha==\"$SHA\")"
```

Then check the live page, with the UA the host will accept:

```bash
UA="TalentIntel/1.0 (+https://asktherecruiter.com)"
curl -s -A "$UA" "https://asktherecruiter.com/blog/talent-intelligence-tracker/?cb=$RANDOM" \
  | grep -o "css?ver=[0-9.]*"
```

That version must be the `TIT_VERSION` you just shipped, with an mtime suffix.
Grepping for `dashboard.css` will find nothing even when everything is fine —
see gotcha 0.

There is no `php` binary on this machine. The deploy workflow lints every PHP
file with `php -l` before it uploads, so a syntax error fails the deploy rather
than the site. Do not skip the workflow to "save time".

---

## Current state (2026-07-27, late)

**Live:** plugin **v1.13.0**, 13 records, **209 tests** green.

Three page templates, all rendering:
`/talent-intelligence-tracker/`, `.../sources/`, `.../company/{slug}/`.

**The page:**
- Hero on the off-white ground: title and live pill on one line, then four
  at-a-glance cells (today / this week / this month / year), then one line of
  fine print carrying the totals. A period with nothing in it still prints and
  says so.
- **No stat tiles on the dashboard.** They repeated the numbers already in the
  hero's fine print. The sources page and company profiles keep theirs, where
  nothing repeats them.
- Region strip using the sibling's `.alt-tab` pill pattern, a hue per region.
  Regions with nothing in them are dropped; World always survives.
- Three chart cards in one row, plain HTML and CSS, no chart library.
- Eight filters in one panel, then the table.
- **Below 860px the table becomes cards**, each cell labelled from `data-label`.
  Below 700px the wrap goes full bleed to cancel the theme's two nested padded
  containers, which otherwise left it 219px wide on a 375px screen.
- Whole page is capped at 1160px; the theme's container runs to ~1500px.

**Design tokens are the sibling's**, read out of its live `layoffs.css` rather
than approximated, so the two products are one family: blue `#2a78d6`,
secondary `#D55E00`, ink `#16181d`, border `#e2e3e8`, surface `#f7f8fa`,
ground `#fafaf8`, system-ui type. One divergence: the sibling's `--alt-muted`
(`#868a93`) is ~3.4:1 on white and fails AA, so it is decorative-only here and
readable text uses its `--alt-ink-2` (`#4a4d55`, 8.6:1).

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

**Google News is multilingual (v1.12.0).** 25 national editions, 7 languages,
three rotating per run plus a fixed US anchor.

> **The trap, if you touch this:** rotating `hl`/`gl`/`ceid` alone does nothing.
> Measured 2026-07-27, the same English phrases returned US:en 23 items, DE:de
> **2**, BR:pt **0** — and German phrasing returned **20** from that same German
> edition. Each edition must ask in its own language (`GOOGLE_NEWS_VOCAB` in
> `source_registry.py`), and `prefilter.py` needs the matching non-English terms
> or every candidate is dropped for free before the model ever sees it. A locale
> without a phrase set is a silent zero dressed up as coverage;
> `tests/test_locale_rotation.py` refuses to let one exist.

Going multilingual took a run from ~25 candidates to ~215, so
`DEFAULT_CANDIDATE_CAP = 40` lives in `run_collect.py`. The cap is a **fair
share** (one item per query in turn), not a head slice: the sibling's flat
`MAX_ITEMS` meant a broad sweep filled the cap and the targeted queries never
fired.

**Still not done:**
- ~~Collection is DORMANT~~ **ARMED 2026-07-27**, 06:00 and 18:00 UTC, after six
  live dry runs. To disarm, comment the two schedule lines out again; nothing
  else changes. Spend is capped independently (`spend.py` exits 1 at 90% of the
  monthly allowance, `DEFAULT_CANDIDATE_CAP` bounds each run at 40 candidates).
- **Filters change the table but not the charts or the hero figures.** The
  stated goal is "every number, chart and row below updates to match"; the
  charts are server-rendered from the unfiltered set and do not re-fetch. This
  is the largest remaining gap in the UI.
- No date-range control, no sort, no quick views
- Model switch (Gemini Flash-Lite gate + Haiku read-through) designed, not applied
- 13 records is thin enough that any layout looks sparse. "Europe 1" is a real
  tab with one row behind it. The template holds up; it needs volume.

---

## The one thing that took six hours to learn

**Every record needs a URL that is a receipt for the claim, and a homepage is
not one.** That is the whole difficulty of this product. Two live records once
linked to `crn.com` and `ft.com` front pages; both were retracted, and
`validate.py` now rejects a URL whose path is empty.

**Correction, and read this before you touch `google_news.py`.** An earlier
version of this document said Google News article URLs were unrecoverable and
that the encoded token had been tested and did not contain them. **That was
wrong.** Google exposes its own resolution endpoint: the article page carries a
signature (`data-n-a-sg`) and a timestamp (`data-n-a-ts`), and posting those to
`news.google.com/_/DotsSplashUi/data/batchexecute` returns the publisher URL.
`resolve_source_url()` does this and it works.

Two ways that hunt goes wrong, both survived here:
- Decoding the base64 token and finding no URL proves only that it is not *in*
  the token. It does not prove Google will not resolve it for you.
- The URL comes back inside an **escaped** JSON string, so the natural
  `"(https?://[^"]+)"` stops at the backslash and matches nothing — which reads
  exactly like "not in the response". The live regex allows for the escaping and
  is pinned by `tests/test_google_news_resolution.py`.

Resolution is best-effort. On failure the item keeps the outlet homepage from
the RSS `<source>` element, and `validate.py` rejects it as a bare domain rather
than crediting the aggregator.

**GDELT** returns real article URLs but its throttling is erratic and its yield
collapsed to zero on a live publishing run. It has still produced no record.

**SEC EDGAR is the source that works.** 8-K Item 5.02 filings are legally
required within four business days, always have a real `sec.gov` document URL,
are primary sources (so records earn `verified`), and SEC allows 10 req/s.
`collectors/sec_edgar.py`.

---

## What six dry runs found

Each of these was invisible until a real run was read line by line. Every one
looked like a quiet news day from the outside.

| # | Bug | How it showed up |
|---|---|---|
| 1 | The free filter killed **every funding story** | 78 of 96 filtered candidates were raises. A funding headline has no employment word in it, so a pillar named in the page's own headline had never produced a news record |
| 2 | "No geography" **discarded paid candidates** | 6 of 12 classified records thrown away after the model had been paid, all real leadership changes. Geography is how we segment; it is not what makes a record true |
| 3 | The prompt was **too timid to record a country** | One read-through said "in Egypt" while the country field was empty. "Named IN THE TEXT" was read so literally that datelines and nationalities did not count |
| 4 | **OpenRouter 429s counted as rejections** | 5 candidates lost in one run, OpenAI tripling its Dublin headcount among them. A busy provider read as the model declining the story |
| 5 | The country vocabulary knew **23 countries** while we queried 25 editions | Philippines and Egypt both normalised to None. The model was right and the vocabulary threw the answer away |
| 6 | The **publisher never reached the classifier** | "USTA SC names new CEO" places nowhere alone; from the Post and Courier it is South Carolina. `source_name` sat in the item, unused |

Measured across the six runs: **4/12 stored → 11/14 stored, 2/11 placed → 6/11
placed, 0 deferred.** The remaining unplaced records are funding wire stories
where the outlet genuinely implies no country, and those now say "Location not
stated" rather than being discarded.

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
| Retraction survives re-collection (`dedupe.exact_duplicate` ignores `is_current`) | A retracted homepage-sourced record came back; checking only current rows also crashed the run with an IntegrityError, because the unique index spans all revisions |
| Non-English prefilter terms | Without them the multilingual queries fetch correctly and every candidate is dropped for free — zero records, looking like it works |
| Catalogue rows must be connectable | 272 spreadsheet names rendered as "researched" read as coverage we do not have |

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
9. **ModSecurity blocks curl's default UA and a browser UA alike.** Use
   `-A "TalentIntel/1.0 (+https://asktherecruiter.com)"` or the host returns a
   "Not Acceptable!" HTML page where you expected JSON.
10. **Easy Table of Contents injects itself into the middle of the hero.** Any
    post with headings gets one. Suppressed on our routes only, in `page.php`
    plus a CSS fallback; ordinary blog posts keep theirs.
11. **Our own routed pages have no theme container.** `sources.php` and
    `company.php` call `get_header()` and render straight into the body, so
    they ran edge to edge with no gutter until `.tit-sources` / `.tit-company`
    got their own 1160px container. The shortcode page must be excluded from
    that or it double-pads.
12. **Block themes set `margin-inline:auto` on every direct child of
    `.entry-content`,** which beats a single-class selector — the phone
    full-bleed rule silently moved nothing until the selector out-specified it.
    And `max-width:100%` resolves against the *padded* container, so it pinned
    the width back even once the negative margins applied.
13. **Tabular figures pad a narrow `1` to a full advance width.** Right in a
    stacked column, wrong for a single inline number: `13` rendered as `1 3` on
    every tile. Use proportional numerals for standalone figures.

---

## Secrets (all set, in GitHub repo secrets)

`OPENROUTER_API_KEY` — the key carries its own hard cap set in the OpenRouter
dashboard, which is the real guarantee. **This was recorded here as a $5
lifetime cap while `spend.py` enforces a $10 monthly allowance; the two
disagree and only the owner can say which is current.** If the key cap is
genuinely $5 lifetime it binds first and `MONTHLY_ALLOWANCE_USD` never fires.
Check the dashboard before sizing anything.
`WP_API_KEY` (must match the key set in WP admin → Talent Intel),
`WP_SITE_URL` (must end `/blog`), `FTP_HOST`, `FTP_USERNAME`, `FTP_PASSWORD`,
`FTP_PORT`, `WP_PLUGIN_REMOTE_DIR`.

**No Railway.** A leftover service exists; it plays no part. Collection runs on
Actions because the SQLite DB must be committed back to the repo.

---

## Cost, measured not estimated

**The ceiling is enforced in code, not hoped for.** `spend.py` runs as the first
step of every collect job and exits 1 at `STOP_AT_FRACTION` (0.9) of
`MONTHLY_ALLOWANCE_USD` (10.0). The OpenRouter key also has its own hard cap,
which is what makes it a guarantee rather than a policy.

- Gate call: 141 tokens in / 35 out (measured)
- `deepseek/deepseek-chat` (current): ~$1.15/month at 660 items/day
- `google/gemini-2.5-flash-lite`: 90% agreement with incumbent, ~$0.56/month
- `anthropic/claude-haiku-4.5` matches Sonnet 5 on read-through quality at half price
- Spent to date: **~$1.86**, last measured 2026-07-27, mostly on the model A/B

`spend.py` needs `OPENROUTER_API_KEY` to report; without it, it says so and
exits rather than guessing. Model A/B is reproducible: `ab_models.py`, workflow
`ab-models.yml`.

**Sizing anything new:** cost scales with candidates reaching the model, not
with source count. Candidates are gated by `prefilter.passes()` (free), then
already-seen URLs are skipped (free), then `DEFAULT_CANDIDATE_CAP` caps what is
left. 40 per run, twice a day, is ~2,400 classifications a month.

---

## Next steps, in order

Done since this list was first written: the page overhaul, the spend ceiling in
code, Form D, and company profile pages. What is actually left:

1. **Arm collection.** Uncomment the two schedule lines in `collect.yml`. Per
   CLAUDE.md this is a human decision after reading a live dry run, so run
   `run_collect.py --dry-run` and read it with the owner before pushing. Nothing
   else fills the tracker; 13 records is the symptom of a dormant collector, not
   of a missing source.
2. **Make the filters drive the charts and the hero figures**, not just the
   table. The `/aggregate` endpoint already accepts the same query parameters;
   the work is client-side re-render.
3. **Fix or retire GDELT.** Zero records, erratic throttling. Retiring it is a
   legitimate outcome and the sources page should then say 3 running, not 4.
4. **Date range, sort, quick views** on the table.
5. **Model switch** (Gemini Flash-Lite gate + Haiku read-through), designed and
   benchmarked, not applied.
6. **More languages for Google News.** Adding a language is what adds countries:
   a phrase set in `GOOGLE_NEWS_VOCAB` plus the matching terms in
   `prefilter._EMPLOYMENT_TERMS_INTL`, then its locales in
   `GOOGLE_NEWS_LOCALES`. Never add a locale without the phrase set.

---

## Rules that are not negotiable

- No source URL, no record. A homepage is not a source.
- The model never invents a number: figures must appear verbatim in `raw_text`.
- Confidence is earned by the source and never promoted.
- Never overwrite a record — append a revision (`store.revise`).
- Layoffs are NOT collected here; read the sibling's public API.
- Coverage is earned: a market in the registry is not a covered market.
- Never publish fabricated records to the live site, for any reason.

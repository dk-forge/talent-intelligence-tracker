# Scoping: making role changes comprehensive off mandatory filings

Written 2026-08-13. Nothing here is armed, nothing is wired into the collector
loop, no row was written, no model was called. **Cost of producing it: $0.**

Reproduce every number:

```bash
python3 analysis/scoping/measure_item502.py      # ~6 minutes, free, read-only
```

---

## The finding that reorders the brief

**All three sources this was asked to scope already exist here, and two of the
three are armed and running on a schedule.** This is not a "should we add a
registry" question. It is a "one existing source is sampled when it could be
enumerated" question, and the honest ranking follows from that.

| Asked to scope | State in this repo | Rows held (current) | Schedule |
|---|---|---|---|
| SEC 8-K Item 5.02 | `collectors/sec_edgar.py` — **this collector IS the Item 5.02 collector**, and always was | 3,802 | `collect.yml`, 2x/day, **degraded since 2026-08-03** |
| Companies House officer appointments | `collectors/companies_house.py`, 803 lines, measured against the register | 3,084 | `collect-structured.yml`, weekly Thursday |
| Other national registries | Czechia, Estonia, India, Japan, Korea, Spain, Israel, Singapore all built | see below | weekly, one per weekday |

The brief's premise — "this repo already reads 8-Ks for layoffs, so Item 5.02 is
a new item number on an existing pipe" — has the direction backwards. The
sibling reads Item 2.05. **This** repo reads Item 5.02 and nothing else; the
collector's own docstring opens on it, and `validate.forced_pillar` exists
because 573 already-published Item 5.02 rows had been filed under `rewards_comp`
by the model and had to be re-issued into `leadership_change`
(`correct_sec_pillar.py`).

So the pillar question the brief raised is **already settled in code and cannot
be got wrong by accident**: an Item 5.02 row is `leadership_change`, forced at
ingestion, whatever a model thinks the filing is mostly about. The reverse
breach — a layoff row landing here — is also already closed:
`correct_layoff_scope.py` withdrew seven of them, and two of those seven were
filings carrying **Item 2.05 and a real Item 5.02 together**. That combination
is the live hazard for any widening of this source, and it is 0 of the 194
filings in the week measured below, but it is not 0 in general.

---

## Priority 1 — SEC 8-K Item 5.02

### Endpoint, auth, limits, terms

| | |
|---|---|
| Enumeration | `https://efts.sec.gov/LATEST/search-index?q="item 5.02"&forms=8-K&dateRange=custom&startdt=&enddt=&from=` |
| Document | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{file}` |
| Auth | none. A descriptive `User-Agent` with a contact address is required; SEC 403s anonymous traffic |
| Rate limit | 10 requests/second, published. The collector sleeps 0.15s |
| robots.txt | `https://www.sec.gov/robots.txt` — verified 2026-08-13. Nothing under `/Archives` or `/cgi-bin/browse-edgar` is disallowed; the Disallow list is Drupal scaffolding (`/core/`, `/admin/`, `/user/login`, …) |
| Terms | public domain US government records. No paywall anywhere on the path |
| Cost | $0 to fetch |

### Volume, counted not estimated

EDGAR full-text search, `forms=8-K`, exact phrase `"item 5.02"`:

```
2026-04    942
2026-05  1,046
2026-06  1,089
2026-07    906
mean     ~996 filings a month
```

**Item selection is a structured field, not a text match.** Every one of the 194
hits in the week walked below carries `5.02` in EFTS's own `_source.items`
array — the filer's item list, not a phrase in prose. A comprehensive walk
should filter on that field and use the phrase only as the search key. The live
collector reads `display_names` and `file_date` off `_source` and ignores
`items` entirely.

**The month is enumerable.** The result window is Elasticsearch's default
`from + size <= 10,000` (verified: `from=2900` returns a page, `from=9990`
returns `Result window is too large`), a page is 100 hits whatever `size` says,
and Q2 2026 is 3,077 filings. **A calendar month costs ~10 requests to
enumerate and a week costs 2.**

### What the collector does today, and why that is a sample

`collect()` runs 5 fixed phrases at `max_per_phrase=4`, `days_back=7`. That is
**at most 20 URLs a run**, taken from the head of a **relevance-ordered** result
list over a rolling week — so consecutive runs re-see the same head and make
progress only through the `seen_urls` skip. Observed `items_found` in
`source_health` for the last five runs: 14, 14, 4, 14, 14.

Measured against the week enumerated in full: **75 of 194 filings (38.7%)**
already have a `leadership_change` row for the same CIK within 14 days, and
**every one of those 75 matches came from `sec_edgar` itself**. Nothing else in
the corpus — not news, not the 286 ATS boards — matched a single one of that
week's Item 5.02 filings. **The 119 that remain are the coverage this would
add, and they are not corroboration of anything: nothing else in this tracker
saw them.**

At ~996 filings a month, comprehensive enumeration is roughly **+610 filings a
month over what is being sampled today**, all US, all primary, all `verified`.

### What the text actually says — and this is where the brief's premise breaks

100 filings of that week, fetched, section extracted, statutory heading
stripped (the heading names all four sub-paragraphs, so reading it as content
makes 99 of 100 look like an appointment):

| The Item 5.02 body says | n |
|---|---|
| arrival + departure + pay | 21 |
| **departure only** | **21** |
| neither — amendment, or incorporation by reference to an exhibit | 13 |
| arrival + departure | 12 |
| departure + pay | 12 |
| arrival + pay | 11 |
| arrival only | 8 |
| pay only | 2 |

**52 of 100 carry arrival language. 48 are a departure, a pay change or an
amendment.** "Adding roles" is about half of what Item 5.02 is; the rest is
material and belongs to other pillars or to the departure direction.

### Can a deterministic parser read it? Measured: barely, and not safely yet

The brief's shape argument is that tonight's leadership parser hit 97.6%
employer accuracy at $0, so filings should do the same. A precision-first
grammar in exactly that style — closed seat list, complete-or-nothing, decline
on interim/acting, decline on more than one appointee — was written **after**
reading the real grammar of 100 filings, not guessed at. Over those 100:

```
decline: no parseable appointment clause    94
CLOSE                                        5
decline: more than one appointee named       1
```

**5% close rate**, which is 5 of the 52 filings that carry an arrival at all,
and the five closes hand-read:

| # | Filer | Parsed | Verdict |
|---|---|---|---|
| 1 | Kyndryl Holdings | Ellen Johnson, Chief Financial Officer | correct |
| 2 | Dow Inc. | Karen S. Carter, Chief Executive Officer | correct (an 8-K/A restating the original — a duplicate risk, not an error) |
| 3 | T-Mobile US | Chris Sambar, Chief Enterprise Officer | correct |
| 4 | **HF Sinclair** | **Steven Ledbetter, President** | **WRONG** |
| 5 | Keel Infrastructure | Ganesh Aiyer, President | correct |

Number 4 is the whole lesson, and here is the filing's own sentence:

> "the Board of Directors … appointed Steven Ledbetter to the position of
> President and Chief Operating Officer of the Corporation and Valerie Pompa to
> the position of President, …"

The seat is truncated (he is President **and Chief Operating Officer**) and a
second appointee is silently dropped, because the coordinated clause carries no
second verb for the multi-appointee guard to see. **One wrong row in five
closes.** For comparison, the news-headline parser this is modelled on measured
**zero wrong extractions over 124 closes**.

That difference is structural, not a matter of another afternoon on the regex.
A wire headline is one clause with one person and one seat by construction. An
8-K paragraph is drafted by securities counsel to be complete rather than
parseable: compound seats, coordinated appointees, effective dates conditional
on a future 10-Q filing, and 13% of filings that say nothing at all in the body
and incorporate a press release by reference. **A deterministic Item 5.02
parser is not the free win the leadership parser was, and shipping the one
measured here would put wrong seats on a public page.**

### So price the paid path instead — and it is cheap

Unit prices from the committed snapshot (`data/model_prices.json`), token
counts from `cost_projection.py`:

```
gate     $0.000051   google/gemini-2.5-flash-lite   (skipped for sec_edgar anyway)
extract  $0.001059   deepseek/deepseek-chat
read     $0.002000   anthropic/claude-sonnet-5
```

`sec_edgar` already skips the prefilter and the keyword gate (`run_collect.py`:
"an SEC 8-K Item 5.02 filing IS an officer or director change by definition"),
so a filing costs extraction plus, if bought, a read-through.

| Scope | extract only | extract + read |
|---|---|---|
| all ~996 filings a month | **$1.05/mo** | **$3.05/mo** |
| the ~52% carrying an arrival | $0.55/mo | $1.59/mo |

**Comprehensive Item 5.02 coverage costs about $3 a month at today's prices,
against an $18 allowance.** Put beside the read budget the brief cites — 398 of
768 reads a day, 52% of full coverage — this is 996 items a month against a
demand of ~23,000, i.e. **4.3% of the demand for 17% of the allowance**, and it
buys a slice that is 100% primary-source and `verified`, which no news read can
be.

It is also worth stating plainly: **the deterministic parser, if it worked
perfectly, would save at most $1.05 a month.** That is the ceiling on the whole
"make it free" idea for this source. It is not worth one wrong row.

### The entity join, measured

This is the part the brief expected to be the real cost, and for this source it
is not, because **the CIK is already carried end to end**. `_company_and_cik`
extracts it from the search hit, the raw dict sets `cik`, and `signals.cik` is a
column. 8,146 of 29,289 current rows (27.8%) carry one; 4,724 distinct CIKs.

- **125 of the week's 194 filers are already in the corpus by CIK.** The join is
  an integer equality. It costs nothing and cannot be wrong.
- A normalised name join over the same 125 would find **102 (82%)**, so a
  name-only source would pay an 18% miss rate on employers we demonstrably
  already hold. That number is the honest price of the join **for sources that
  have no CIK** — which is every non-SEC source in this document.

### Two real defects found while measuring, both in live code

1. **The ticker-strip regex accepts one ticker, not a list.**
   `re.sub(r"\s*\((?:[A-Z0-9.\-]{1,10})\)\s*", ...)` has no comma or space in
   the character class, so a multi-class filer keeps its ticker list.
   **36 of 194 names in the measured week (19%)**, and it has reached the
   published corpus: **125 of 3,802 stored `sec_edgar` headlines** read like
   `BED BATH & BEYOND, INC.  (BBBY, BBBY-WT) 8-K filing (Item 5.02): …`.
2. **The same regex eats a legitimate parenthetical.** `Jerash Holdings (US),
   Inc.` is stored with the headline `Jerash Holdings , Inc. 8-K filing …`.

Neither is in scope for this document to fix and neither is armed by it. Both
are one-line changes with a test, and both should be fixed **before** the volume
is widened, because widening multiplies them by six.

---

## Priority 2 — Companies House director appointments

**Already built, already armed, already running.** `collect-structured.yml`,
weekly on Thursday, last run 2026-08-06 `ok`, 303 found / 26 stored. 3,084
current rows.

| | |
|---|---|
| Endpoint | `https://api.company-information.service.gov.uk/company/{number}/officers?items_per_page=100&start_index=N` |
| Auth | REST key as HTTP Basic user, empty password. `COMPANIES_HOUSE_API_KEY_UK`, already a repo secret. Verified live 2026-08-13: no key returns `401 {"error":"Empty Authorization header"}` |
| Rate limit | 600 requests / 5 minutes. Collector sleeps 0.55s, ~9% of the allowance left as retry margin |
| robots.txt | the API host answers 401 to every path including `/robots.txt`, so there is no directive to honour; `find-and-update.company-information.service.gov.uk/robots.txt` 404s (verified 2026-08-13); `download.companieshouse.gov.uk/robots.txt` is `Disallow:` — explicitly everything |
| Licence | Open Government Licence v3.0, attribution carried in every stored row's summary |
| Cost | $0. It exposes `as_classified`, so no model is ever called |

**What it would add is already known and already measured, and the answer is
that the interesting decision was made in the other direction.** The register
holds ~5.7M live companies producing ~1.4M appointments a year (~27,000/week),
measured on a random sample of 120 companies at 0.246 appointments per company
per year. That is unusable: the median sampled company has 2 officers ever
recorded and names like `5374 LTD`. The collector's population is instead the
**gender pay gap roster** — 9,230 employers of 250+ staff, 0.16% of the
register — measured at 0.867 appointments per company per year, **~110 stored
rows a week** after the 0.81 fuzzy-dedup collapse.

So the honest answer to "what would it add on top of the 286 ATS boards": **it
is already adding it, at ~110 rows a week**, and the remaining headroom is the
5.7M-company tail that was deliberately excluded and should stay excluded. The
only unexplored widening is the size floor (250 employees), and dropping it
buys mostly dormant micro-companies.

There is nothing to build here. **Recommending work on Companies House would be
recommending work that has already shipped.**

---

## Priority 3 — other national registries

The brief said to check Estonia, Czechia and India first because we already read
them, and not to pad the list. All three already report officers, and so do four
more.

| Registry | Endpoint | Auth | What it states about officers | Held | Last run |
|---|---|---|---|---|---|
| **Czechia, ARES** | `ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty-vr/{ico}` — verified 200 live 2026-08-13, keyless; `robots.txt` disallows only `/cms/` | none | **Both directions per person.** `vznikClenstvi` / `zanikClenstvi` are the dates the office itself began and ended, distinct from the court-registration dates. The only registry here that states a departure without diffing snapshots. Population is the change feed, narrowed to the 1.0% at a 250+ employee band (`kategoriePoctuPracovniku >= 330`) | 77 | 2026-08-07 `ok` |
| **Estonia, Ariregister** | `avaandmed.ariregister.rik.ee/sites/default/files/avaandmed/…kaardile_kantud_isikud.json.zip` — page verified 200 live 2026-08-13, keyless | none | **Half a spine, on purpose.** The daily open-data file lists *current* office-holders only, so `lopp_kpv` is null on all 520,895 rows: appointments, never departures. That sentence is on every row it stores. Filtered to 50+ FTE | 14 | 2026-08-08 `ok` |
| **India, BSE** | `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?strCat=Company+Update&subcategory=Change+in+Directorate…` | none | SEBI Regulation 30 makes the filer pick a **fixed category**, which is the same kind of mandated taxonomy as Item 5.02 and the reason this is the one non-US jurisdiction readable at volume. ~1,080 filings in 29 days across four categories, ~13,000/year | **6,249 — the largest leadership source in the tracker** | 2026-08-10 `ok`, 557 found / 315 stored |
| Japan, EDINET | typed statutory clause 第19条第2項第9号 | secret, held | change of representative director only — the one officer clause Japan types | 348 | 2026-08-11 `ok` |
| Korea, OpenDART | KRX report title | secret, held | representative director; independent director appointment/dismissal | 31 | 2026-08-12 `ok` |
| Spain, BORME | Section A fixed headings | none | **states a departure**; filtered to `consejero delegado` | 0 | never run |
| Israel, registrar | daily changes file | none | funding acts, **not leadership** — no officer act codes used | 0 | never run |

Two things worth recording rather than padding:

- **The BSE India probe returned `403 Access Denied` from a plain stdlib client
  here.** The live collector's last run stored 315 rows on 2026-08-10, so the
  source is healthy and this is a header/origin difference in my probe, not an
  outage. Recorded because an unverified green claim is what this document
  exists to avoid: I could not reproduce that endpoint from this environment,
  so for me it is **UNKNOWN**, and the evidence it works is the health ledger,
  not my request.
- **Spain and Singapore are built, measured and have never run.** `spain_borme`
  has no `source_health` row at all. That is a larger and cheaper coverage gap
  than anything in this document — a built, keyless, $0 collector for a
  departure-reporting registry, ~12,700 rows a year, not scheduled. It is out
  of this document's scope and it should not be.

**Nothing new is worth adding.** The taxonomy argument in `bse_india`'s
docstring is the general rule and it holds: a registry is readable at volume
only where the *filer* is made to pick a machine-readable category. Item 5.02
and SEBI Regulation 30 do that. Form 6-K, which every foreign private issuer on
EDGAR uses, has no item taxonomy at all, and a full-text search for "appointed
as" against those filers returns roughly one useful hit in eight. A country
with no such taxonomy is not a source, and saying so is the finding.

---

## Ranked recommendation

**One of the three is clearly better than the others, and it is not close.**

### 1. Widen `sec_edgar` from a sample to an enumeration. Build this.

Not because it is free — it is not — but because it is the only one of the
three with a real gap, the gap is large (**+610 filings a month, ~119 a week
that nothing else in this corpus saw**), the join is an integer equality that
costs nothing, and the whole thing costs **~$3/month of an $18 allowance**.

What it means concretely:

- enumerate by `_source.items` containing `5.02`, not by the head of a
  relevance-ordered phrase search;
- page a fixed date window to exhaustion (2 requests a week, 10 a month);
- fix the two ticker-regex defects **first**;
- keep `forced_pillar`, and add a guard for a filing carrying **both** Item 2.05
  and Item 5.02 — that is the shape that put layoff rows in this tracker once
  already;
- decide arrivals-only ($1.59/mo) or arrivals-and-departures ($3.05/mo). Half of
  Item 5.02 is a departure, and Czechia and Spain already store departures, so
  the pillar can carry them.

### 2. Do nothing on Companies House.

It is built, armed, running weekly, and its population was chosen against
measured alternatives. The register's remaining 5.7M companies are the thing the
filter exists to exclude.

### 3. Add no new national registry. Schedule the two that are already built.

`spain_borme` and `singapore_acra` are written, measured, keyless, $0, and have
never executed. That is a better use of the next hour than any new country.

### And do not build the deterministic 5.02 parser.

Measured: 5% recall, and 1 of 5 closes wrong on a filing whose sentence names
two people and a compound seat. Its entire upside is $1.05 a month. The
throughput lesson that produced tonight's leadership parser does not transfer
from wire headlines to securities prose, and the measurement above is the reason
to say so before somebody spends a week on it.

---

## What this document did not establish

- **Whether the 119 unseen filings a week are *interesting*.** They are real,
  primary and unheld; nobody has read a sample to say how many are a
  micro-cap board seat versus a signal a reader wants. That is a judgement call
  and it is the owner's.
- **The BSE India endpoint, from this environment.** 403 here; healthy in the
  ledger. UNKNOWN, not green.
- **Any figure for how the widened volume interacts with `dedupe`'s 14-day
  fuzzy window.** Companies House measured its own collapse factor at 0.81 on
  its own sample; no equivalent was measured for a six-fold widening of SEC.
  Assume the stored count is lower than 996 and do not publish a projection
  until it is measured.

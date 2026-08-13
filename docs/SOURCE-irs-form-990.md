# IRS Form 990: the receipt, the join, the size, and the label

Written 2026-08-13, on branch `feat/form-990`. **The collector is built, tested
and dry-run, and it is DORMANT: nothing schedules it.** Arming it is a separate
decision with its own cost, set out at the bottom.

**Model spend for this work: $0.00.** No LLM was called at any point, and the
collector cannot call one: every field is a tagged XML element or a fixed
editorial line, so it exposes `as_classified` and skips the gate, the model and
the spend cap alike.

This document answers the four questions
[SCOPE-us-pay-filings.md](SCOPE-us-pay-filings.md) left open when it ranked
Form 990 first. The collector's own docstring
(`collectors/irs_form_990.py`) is the operating reference; this is the evidence.

---

## 1. The citable URL. Solved, and here is the proof

The scoping pass was right that the two routes it tried are dead, and wrong
that the source has no receipt. Everything below was re-verified on 2026-08-13
with the collector's own descriptive User-Agent.

**What is genuinely dead:**

| Route | Result |
|---|---|
| `apps.irs.gov/pub/epostcard/990/xml/2026/{OBJECT_ID}_public.xml` | 302 then HTTP 404, on an object ID taken from a batch zip that demonstrably contains the file |
| `s3.amazonaws.com/irs-form-990/{OBJECT_ID}_public.xml` | HTTP 404 |
| `s3.amazonaws.com/irs-form-990/?max-keys=20` | HTTP 200, `<IsTruncated>false</IsTruncated>`, **zero keys**. The bucket exists, is publicly listable, and is empty. That route is gone, not moved. |
| `apps.irs.gov/app/eos/` | HTTP 403 to a descriptive agent and to a browser User-Agent |

**And a fifth thing that is worse than a 403.** The Tax Exempt Organization
Search interface does render in a real browser, and its organisation page has
no URL. Searching by EIN, opening the result and reading `location.href` gives
`https://apps.irs.gov/app/eos/details/` with no parameters at all: the state is
held server side. It is unlinkable even for a human, so it could never have
been the receipt.

**What works.** That same page loads its content from a JSON route that is not
behind the bot wall:

```
GET https://apps.irs.gov/teos/details/returnsSearch/310707369
-> {"items":[{"EIN":"310707369","TAX_PERIOD":"202407","RETURN_TYPE":"990",
              "STATICFILEPATH":"/pub/epostcard/cor/310707369_202407_990_2025081423655359.pdf"}, ...]}
```

`https://apps.irs.gov` + `STATICFILEPATH` is the filed return itself, and it
answers **HTTP 200, `application/pdf`** to the collector's own User-Agent. Both
the lookup and the PDF are under `/teos/` and `/pub/`, never `/app/eos`.

**The filename cannot be composed, which is why this is a lookup and not a
formula.** `310707369_202407_990_2025081423655359.pdf` is EIN, tax period,
return type, then an IRS posting date (`20250814`) and the return id
(`23655359`). The return id is in `index_2025.csv` as `RETURN_ID`; the posting
date is in no published file. It is not in the index (`SUB_DATE` is the year
alone, `2025`), and it is not in the return's own XML (`ReturnTs` is
`2025-06-09`, `BuildTS` is `2025-03-06`, `SignatureDt` is `2025-06-14`, and the
DLN encodes a different day again). So the collector asks, once per
organisation, and a filing whose lookup finds nothing is **dropped**.

### Receipt rate, measured

100 randomly sampled long form 990 filings per index year, one lookup each,
one second apart, plus a HEAD on the first 15 PDFs of each sample:

| Index year | Receipt found | PDF HEAD 200 |
|---|---|---|
| 2025 | **100 / 100** | 15 / 15 |
| 2024 | 55 / 60 | 15 / 15 |
| 2026 (open) | **19 / 100** | 15 / 15 |

The 2026 figure is the whole reason for `latest_complete_year()`. TEOS posts
the copy of a return months after the XML batch, so collecting the current
year is collecting a log of NO RECEIPT. The five 2024 misses are small
organisations whose copy for that one period was never posted, with adjacent
periods present. Combined complete-year rate: **155 / 160**.

**One trap the dry run caught and no amount of reading would have.** Matching
`RETURN_TYPE` on the prefix `990` also matches `990T`, the unrelated business
income tax return: a different form, no Part VII in it, and filed for the same
tax period by a great many health systems. The first dry run put nine McLaren
hospital rows and two others on a 990-T URL, which is a real IRS document that
does not contain the figure on the row. The collector now matches
`{"990", "990O"}` exactly, and where two copies exist for one period it cites
the most recently posted one rather than whichever the API listed first.

### Robots

`www.irs.gov/robots.txt` carries no disallow for `/pub/` or for the downloads
page. It does disallow `/charities-non-profits/tax-exempt-organization-search`,
which is the www landing page and is not a path this collector fetches.
`apps.irs.gov/robots.txt` answers HTTP 503 through Akamai, which
`national_press.robots_allows` reads as no restriction, the same standard
reading every other collector here uses. The one path that actually refuses
automated clients is `/app/eos`, and nothing fetches it.

---

## 2. The employer join. Zero, measured on the population that ships

The scoping pass measured 0 of 100 random 990 filers against
`pipeline.vocab.company_key`. That is the right number for the wrong
population: nobody would ship random filers. Re-measured on the filtered
populations, against every employer the tracker currently holds:

| Population | n | Matches an employer we hold | In identity cache | Resolved |
|---|---|---|---|---|
| `TotalEmployeeCnt >= 1000` | 96 | **0** | 0 | 0 |
| `TotalEmployeeCnt >= 500` | 227 | **0** | 0 | 0 |
| `TotalEmployeeCnt >= 250` | 526 | 1 | 0 | 0 |
| 100 random filers, any size | 100 | 1 | 0 | 0 |

**Both matches are wrong.** `Midwest Energy Inc` is a Kansas electric
cooperative; the `Midwest Energy Ltd` we hold is a different company. So the
measured result is **0 correct matches in 526, and one false one**, which is
the argument for the source rather than against it: these are new employers,
not better keys for employers we already have.

### The decision: do NOT add an EIN column to `employer_identity` now

Three reasons, in the order that decides it.

1. **There is no second EIN carrying source built.** The exact-join argument is
   that Form 990, Form 5500 and the DOL disclosure files all key on EIN and
   could join to each other perfectly. That is true and none of them exists.
   Adding a column plus a resolution pass today buys a join between one source
   and nothing.
2. **The join it would replace is not doing any work.** A schema change is
   worth the cost when it fixes matches that are currently failing. Here there
   are no matches to fix: 0 of 526.
3. **The rows do not need it.** These employers stand alone, which is what the
   scoping pass suspected and what the numbers say.

**And the door is not closed, at zero cost.** The EIN is the first field of the
receipt URL, so `310707369` is recoverable with a string split from any stored
row. The day a second EIN carrying source lands, the backfill for these rows is
a regular expression over `source_url` and not a re-ingest.
`tests/test_irs_form_990.py::Receipt::test_the_ein_survives_in_the_url_so_no_schema_change_is_needed`
is the guard that keeps that true.

---

## 3. The size, and what it does to the pillar

Unfiltered, index year 2025 holds **376,920 long form 990s**, which is thirteen
times the entire 29,329 row database and all of it American. That is not depth,
and `uk_paygap.DEFAULT_MIN_SIZE` exists because the same thing was true of the
UK.

The filter is `CYTotalRevenueAmt >= $100,000,000`, and the number that matters
was measured by running the shipped parser over two entire batches,
`2025_TEOS_XML_01A` and `06A`, holding 31,706 long form returns between them,
8.4% of the year:

| Batch | Long form 990s | Storable at the floor |
|---|---|---|
| 01A | 9,264 | 48 |
| 06A | 22,442 | 99 |
| **combined** | **31,706** | **147 (0.464%)** |

Across the year's 376,920 that is **about 1,750 rows a year**.

| | rows | rewards_comp | share |
|---|---|---|---|
| today | 29,329 | 8,832 | 30.1% |
| + one year of Form 990 | 31,077 | 10,580 | 34.0% |
| + three years (2023 to 2025) | 34,573 | 14,076 | 40.7% |

**One year is depth. Three years is a decision.** A backfill to 2023 nearly
doubles the pay pillar and makes the tracker markedly more American without
moving the US recall figure by a single event, because recall is measured
against a gold set and not against row count. Collect the latest complete year
first and look at the page before deciding whether the backfill is depth or
noise.

### Why revenue and not headcount

`TotalEmployeeCnt` counts everyone the organisation issued a W-2 to, including
part time and seasonal staff, so it selects community organisations rather than
institutions. At a 1,000 employee floor the 06A batch is 96 filings of which
roughly 40% are YMCAs and Goodwills. At the $100M revenue floor the same batch
is:

| | share |
|---|---|
| hospitals and health systems | 20.4% |
| universities, colleges and schools | 16.8% |
| YMCA-shaped | 4.4% |
| research institutes | 2.7% |
| everything else | 54.0% |

"Everything else" is electric cooperatives, benefit funds, large foundations,
Feeding America, the NCAA and the United States Olympic and Paralympic
Committee. It is the medical, bio and education coverage the owner asked for,
plus a long tail of genuinely large American employers nothing else here sees.

The floor is `TIT_FORM990_MIN_REVENUE`. Widening it to $50M is 4,300 rows a
year and widening it to $25M is 8,800, which is the whole pay pillar again in
twelve months.

---

## 4. The label these rows can carry

**Supported.** The largest single compensation figure any *person* listed on
Part VII Section A of this organisation's Form 990 was reported to receive
**from the organisation**, with the title as filed, for the calendar year
ending with or within the tax year that ended on the stated date.

**Not supported, and each of these was an available mistake:**

- **Not the chief executive's pay.** The highest paid person on Part VII is
  frequently not the chief executive. In the batch this was measured on it is
  `HEAD COACH, BASKETBALL` at Pepperdine, `MUSIC DIRECTOR` at the Metropolitan
  Opera and `CHAIR/PHYSICIAN` at Greater Baltimore Medical Center. Picking "the
  CEO" would mean matching titles, which is inventing. The row says highest
  paid, because that is what was computed.
- **Not current pay.** Part VII states pay for the calendar year ending with or
  within the tax year, so a return for the year ended 2024-07-31 carries
  calendar 2023 pay, and returns arrive up to about 18 months after that. The
  row is dated by the tax period end, the way `sec_execcomp` dates by period
  end, and the summary says which year the money belongs to.
- **Not an average, and never divided.** Total payroll and the employee count
  are both stored as filed and are never combined: the numerator bundles
  benefits and pension, the denominator counts part time staff.
- **Not a person's name.** `sec_execcomp` stores "the principal executive
  officer" and never the officer, and the scoping pass refused state payroll
  portals largely because they are person level data. A source that is right
  about hospitals does not get a different rule. The title is filed and is
  stored; the name is filed and is dropped, and every row says so in writing.
- **Not always a person at all.** The Bank of America Charitable Gift Fund's
  2023 return carries $20,052,864 on Part VII against `<BusinessName>BANK OF
  AMERICA` with `InstitutionalTrusteeInd` set. That is a corporate trustee's
  fee filed in the same column as an officer's salary, and it was forty times
  the largest real pay figure in the same batch. The return makes the
  distinction itself, so the parser reads it: a Part VII group with no
  `PersonNm` is skipped. Five filings in one batch were nothing but such rows
  and are correctly dropped.

---

## 5. The dry run

```bash
TIT_FORM990_CACHE=/tmp/f990cache TIT_FORM990_MAX_BATCHES=1 \
  python run_collect.py --source irs_form_990 --dry-run
```

One batch, live against the IRS, 2026-08-13:

```
[irs_form_990] 2025: 16 batch file(s), reading 1, revenue floor $100,000,000
[irs_form_990]   2025_TEOS_XML_01A.zip: 17,044 returns, 48 above the floor so far, 0 dropped for no receipt
...
  STORE   MCLAREN HEALTH CARE CORPORATION: $10,748,364 reported for its highest paid officer or key employee, tax year ended 2023-09-30
          rewards_comp / comp_shift / verified
          US   published 2023-09-30
          source: https://apps.irs.gov/pub/epostcard/cor/382397643_202309_990_2025041123355125.pdf
  STORE   WARTBURG COLLEGE: $334,084 reported for its highest paid officer or key employee, tax year ended 2024-05-31
  STORE   ATLANTA COMMUNITY FOOD BANK INC: $370,788 ...
  STORE   BARBARA ANN KARMANOS CANCER HOSPITAL: $802,256 ...

[irs_form_990] found=48 would store=48 duplicate=0 rejected=0 deferred=0 budget-deferred=0 gate-errored=0 already-seen=0

DRY RUN - nothing was written.
```

That first source URL was fetched independently: HTTP 200, `application/pdf`,
2,534,418 bytes.

The run before the two fixes produced 53 rows, eleven of them citing a 990-T
and five of them a corporate trustee's fee. Both are the reason this repo does
a live dry run before it stores anything.

---

## 6. What arming it costs, and what to decide

Nothing schedules this collector. `run_collect.py` registers it,
`tests/test_sources_page.py::_DORMANT_COLLECTORS` excuses it from the sources
page, and it will keep claiming no coverage until somebody moves it out of that
set in the same change that schedules it.

To arm it, four things need deciding:

1. **The download.** A year is 15 or 16 batch zips of roughly 200 to 250MB, so
   about 3.5GB per year fetched from a government host. `TIT_FORM990_CACHE`
   stops a re-run repeating it. This is the real cost of the source and it is
   bandwidth rather than money.
2. **The lookups.** About 1,750 receipt lookups a year at one second apart, so
   half an hour of the job, plus whatever the batches took.
3. **The cadence.** Annual, not twice daily. The nearest existing shape is the
   monthly cron `collect-structured.yml` runs for `sec_execcomp` and
   `uk_paygap`; a Form 990 index year is complete once and never moves, so one
   run a year plus a manual backfill is the honest schedule, and
   `staleness.py` needs a ceiling that matches it or it will be permanently
   noisy.
4. **Whether to backfill.** See the pillar balance table above. One year is
   depth; three is a different product decision.

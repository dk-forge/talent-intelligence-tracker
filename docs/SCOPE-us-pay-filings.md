# Scoping: four US mandatory-filing sources for the pay pillar

Written 2026-08-12. **Nothing here is armed, wired or built.** No collector was
added, no row was written, no workflow was touched. This document exists so the
decision to build one of these is made on measurements rather than on how good
each source sounds.

Every figure below was taken from the real file on 2026-08-12. Where a number
could not be established from this environment it says UNKNOWN. **Model spend
for this work: $0.00.** No LLM was called. Every source here parses
deterministically, which is the whole reason to look at them.

## Why the question is worth asking

`cost_projection.py` on 2026-08-12 says the read budget buys **373 paid reads a
day against a demand of 768**, which is 48% of full worldwide coverage. News
costs a model call per candidate and always will. A mandatory structured filing
costs nothing per row and is complete for whoever it covers, so it does not
compete for that budget at all. `uk_paygap` is the proof: 4,761 stored rows, no
model, near-total coverage of large UK employers by construction.

The corpus today, from `data/talent_intel.db`, current rows only:

| Pillar | Rows |
|---|---|
| leadership_change | 15,291 |
| rewards_comp | 8,832 |
| company_development | 4,540 |
| how_we_work | 626 |
| **total** | **29,289** |

16,597 distinct employers. `employer_identity` holds 4,819 employers, of which
3,025 are resolved. Hold those numbers in mind: they are what "large" has to be
measured against, and three of the four sources below are larger than the whole
database.

---

## 1. DOL foreign labor certification disclosure data (LCA and PERM)

### Verified endpoint and format

The index page is
`https://www.dol.gov/agencies/eta/foreign-labor/performance`. It returned 200
and 380,907 bytes on 2026-08-12. `dol.gov/robots.txt` disallows `/core/`,
`/profiles/`, `/admin/`, `/search/` and a list of README files. It does **not**
disallow `/sites/dolgov/files/` or `/media/`, which is where every data file
lives. There is no crawl-delay and no AI-specific clause.

**The file URLs are not a stable pattern, and a collector that hardcodes one
will break.** Most quarters live under
`/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY2025_Q4.xlsx`. The
current one does not. It is
`https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx`, on a different
path, with "Disclosure" misspelled in the filename. The FY2026 Q1 file is not
linked from the page at all. So the collector must read the page and follow the
links it finds, the way `uk_paygap` reads a year rather than guessing a URL.

Two files, both `.xlsx`, both verified with a HEAD request:

| File | Bytes | Last-Modified |
|---|---|---|
| LCA_Dislclosure_Data_FY2026_Q2.xlsx | 137,758,384 | Thu, 14 May 2026 |
| PERM_Disclosure_Data_FY2025_Q4.xlsx | 87,007,731 | Thu, 18 Dec 2025 |

Cadence is quarterly and cumulative within a federal fiscal year. DOL's own
wording: "The following case disclosure files cover determinations issued
between October 1, 2025 and March 31, 2026", and "A small percentage of
determinations may be subject to change in subsequent quarterly releases due to
appeal or redetermination decisions." So a quarter is a **restatement of the
year to date**, not an increment, and a naive append would store the same case
four times. Files exist back to FY2008.

### Volume, measured

Parsed with a stdlib streaming reader. No dependency was added and none is
needed. The LCA file has 98 columns and PERM has 137.

| Measure | LCA FY2026 H1 | PERM FY2025 (full year) |
|---|---|---|
| Rows the sheet claims | 1,039,355 | 148,659 |
| Rows that hold data | **210,387** | **147,056** |
| Certified | 190,152 | UNKNOWN, not tabulated |
| Worker positions requested | 495,324 | one per case |
| Distinct employer name strings | 32,277 | 37,947 |
| Distinct employer FEIN | 29,606 | not tabulated |

**The first parser trap is in that table.** The LCA sheet contains 1,039,355
`<row>` elements and 828,968 of them are completely empty. A parser that counts
rows, or that trusts a row exists because the XML says so, overstates the file
by a factor of five and then reports a wildly wrong "coverage" number. Every
column in those rows is blank, including CASE_NUMBER.

Two more traps, both verified in the data:

- **Dates are Excel serial numbers, not dates.** `RECEIVED_DATE` reads `46105`.
- **The file carries personal data.** `EMPLOYER_POC_EMAIL`,
  `EMPLOYER_POC_PHONE`, named attorneys, `AGENT_ATTORNEY_EMAIL_ADDRESS` and
  `PREPARER_EMAIL` are all populated. DOL's page says PII is withheld, and it
  plainly is not withheld here. A collector must select columns explicitly and
  must never build `raw_text` by concatenating the row.

At full LCA cadence this is roughly **420,000 applications a year**, against a
whole-database total of 29,289 rows.

### Worked example, exactly as a parser meets it

First data row of `LCA_Dislclosure_Data_FY2026_Q2.xlsx`, fields trimmed to the
ones that matter:

```
CASE_NUMBER              I-200-26083-726723
CASE_STATUS              Certified - Withdrawn
RECEIVED_DATE            46105          (Excel serial)
VISA_CLASS               H-1B
JOB_TITLE                Specialist - Software Engineering
SOC_TITLE                Software Developers
EMPLOYER_NAME            LTIMindtree Limited
EMPLOYER_CITY / STATE    Edison / NJ
SECONDARY_ENTITY         Yes
SECONDARY_ENTITY_BUSINESS_NAME   Citibank
WORKSITE_CITY / STATE    New York / NY
WAGE_RATE_OF_PAY_FROM    139000
WAGE_UNIT_OF_PAY         Year
PREVAILING_WAGE          131997
PW_WAGE_LEVEL            II
TOTAL_WORKER_POSITIONS   1
```

**That single row is the honesty problem in miniature.** The employer is an IT
services firm in New Jersey. The worksite is a bank in Manhattan. The wage is
what the application offers, on a case that was certified and then withdrawn,
so nobody was necessarily ever paid it. Filed under "pay at LTIMindtree in New
York" it would be four different kinds of wrong at once.

39,374 of the 210,387 rows declare a secondary entity, so this is 19% of the
file and not an edge case.

### What this data can honestly support

It supports exactly one claim, and the claim has to carry its own label:

> **The wage offered on certified H-1B labour condition applications for
> {occupation} at {employer} with a worksite in {city}, {period}. This is the
> wage stated on an immigration application. It is not payroll, not a company
> average, and it covers only sponsored roles.**

Everything narrower than that fails. Specifically:

- **Not "average pay at X".** The population is sponsored hires only. 67,668 of
  the rows are Software Developers, and the top 25 occupations are almost
  entirely engineering, IT, finance and accounting. In Los Angeles the largest
  occupation is Software Developers at 185 rows, in a metro whose largest
  private employers are in health care and entertainment. The sample is not the
  workforce.
- **Not "what people earn".** `WAGE_RATE_OF_PAY_FROM` is an offer floor on an
  application. 190,152 of 210,387 rows are certified, 15,952 are certified then
  withdrawn, 3,298 withdrawn and 985 denied. A certification is a permission,
  not a hire.
- **Not one figure per employer.** A single quarter has 5,195 rows for one
  Amazon entity alone. Any employer-level number is an aggregate we computed,
  which puts it under the rule that the model never invents a number, and under
  the harder question of whether we are willing to publish an average of a
  skewed sample at all.
- **Not comparable across employers without the level.** `PW_WAGE_LEVEL` runs I
  to IV and is populated on 187,488 rows. Level I and level IV are different
  jobs. Comparing two employers without it compares their seniority mix.

**The honest product is narrow, and narrow is the right answer.** For the four
hub metros, the certified annual-wage rows in FY2026 H1:

| Worksite | Rows | Distinct employers |
|---|---|---|
| New York, NY | 12,396 | 3,180 |
| Austin, TX | 5,605 | 1,257 |
| San Francisco, CA | 5,074 | 1,338 |
| Los Angeles, CA | 1,345 | 558 |

For San Francisco, 4,432 certified rows quote an annual wage. Their
distribution: 5th percentile $97,926, 25th $153,000, median $187,741, 75th
$225,000, 95th $300,000. That is a real, checkable, deterministic figure about a
named population. It is useful to a job seeker in a way nothing else free is.
It is also **not the San Francisco software salary**, and the page would have to
say so in the headline rather than in a footnote.

Sample certified San Francisco rows, unedited:

```
Pinterest, Inc.    Sr. Software Engineer   Software Developers   187,741/yr  level II
Maplebear Inc.     Staff Software Engineer Software Developers   250,000/yr  level IV
Google LLC         Software Engineer       Software Developers   200,000/yr  level II
Komodo Health, Inc. Data Scientist III     Data Scientists       149,000/yr  level II
Cantina Inc.       AI Strategic Development Lead  Marketing Managers  200,000/yr  level II
```

### PERM

Same programme family, one file a year, 147,056 real rows in FY2025. It carries
`JOB_OPP_WAGE_FROM`, `PWD_SOC_TITLE`, `PRIMARY_WORKSITE_CITY`, and two fields
LCA does not have: **`EMP_NUM_PAYROLL`** (the employer's own headcount) and
`EMP_YEAR_COMMENCED`. Worked example, first data row:

```
CASE_NUMBER        G-200-23334-532147
CASE_STATUS        Certified
EMP_BUSINESS_NAME  South Georgia Pecan Company
EMP_CITY / STATE   Valdosta / GA
EMP_NUM_PAYROLL    190
EMP_YEAR_COMMENCED 1913
EMP_NAICS          115114
OCCUPATION_TYPE    Non-professional
```

PERM is the better of the two for our purposes and nobody expects that. It is
a fifth the volume, it is annual rather than restated quarterly, it carries a
self-reported headcount, and it reaches beyond technology. It is also further
from actual pay: a PERM wage is an offer on a permanent-residence application
that may be years from taking effect.

---

## 2. Form 5500 (DOL EBSA)

### Verified endpoint and format

`https://www.dol.gov/agencies/ebsa/about-ebsa/our-activities/public-disclosure/foia/form-5500-datasets`
returned 200. Files sit on `askebsa.dol.gov` as zipped CSV, one per plan year
from 1999, with a published field layout beside each. Downloaded and parsed:

| File | Zip bytes | CSV bytes | Rows |
|---|---|---|---|
| F_5500_2025_All.zip | 7,556,033 | 36,928,253 | 62,437 |
| F_5500_2024_All.zip | 30,043,492 | not measured | **237,199** |

The 2025 file is small because the plan year is still being filed. That is the
cadence trap: **a Form 5500 year is not complete until roughly October of the
following year**, the same shape `uk_paygap.latest_complete_year()` already
handles. Filings also restate. The 2024 file contains the Teamsters welfare
trust twice under two ACK_IDs with identical participant counts.

This is only the long-form 5500. Small plans file the 5500-SF, which is a
separate file (`F_5500_SF_2024_All.zip`) and was not parsed.

### What it actually gives us

Genuinely useful fields: `SPONSOR_DFE_NAME`, `SPONS_DFE_EIN`, sponsor city and
state, `BUSINESS_CODE` (NAICS), `PLAN_NAME`, `TOT_ACTIVE_PARTCP_CNT`,
`TOT_PARTCP_BOY_CNT` and a set of plan-type indicators. 145,434 distinct sponsor
EINs in 2024, under 161,767 distinct name spellings, which is itself a warning
about the name key.

25,704 plans report 1,000 or more active participants. The largest:

```
WALMART INC.              WALMART 401(K) PLAN                 active 1,670,732  BOY 1,921,006
WALMART INC.              ASSOCIATES' HEALTH AND WELFARE      active 1,650,312  BOY 1,615,389
AMAZON.COM SERVICES, LLC  AMAZON 401(K) PLAN                  active 1,207,759  BOY 1,336,478
AMAZON.COM SERVICES, LLC  GROUP HEALTH & WELFARE PLAN         active 1,194,891  BOY 2,053,062
NATIONAL EDUCATION ASSOCIATION OF THE UNITED STATES  NEA MEMBERS INSURANCE PLAN   active 2,528,845
```

### Is the participant count a usable employer-size signal?

**Partly, and only with a filter that the last line above explains.** The NEA
plan has 2.5 million active participants and the NEA does not employ 2.5 million
people. It is a union members' plan. The Teamsters and AFT trusts are the same
shape. Two more distortions in the same top 100: `PAYCHEX RETIREMENT LLC` and
`ADP TOTALSOURCE, INC.` are payroll providers filing on behalf of thousands of
unrelated small employers, so their participant count is not one employer's
headcount either.

And a single employer files several plans with overlapping populations. Amazon's
401(k) and health plans are 1.21m and 1.19m active participants. They are mostly
the same people. Adding them is a straightforward double count.

So: `TOT_ACTIVE_PARTCP_CNT` is a **defensible lower bound on US headcount for a
single-employer 401(k) plan at a named corporate sponsor**, and it is nothing at
all for multiemployer trusts, union plans and PEO filings.
`TYPE_PLAN_ENTITY_CD` separates them (218,229 rows are code 2, single-employer
plans; 10,053 are code 4). It is a real signal behind a real filter.

What a reader would care about is thinner than the field list suggests. "How
many people does this employer cover in its 401(k), and is that up or down on
the year" is answerable and mildly interesting. `TOT_PARTCP_BOY_CNT` gives the
year-on-year direction inside one row, which is the same shape that makes the UK
pay gap readable. Everything else in the file is plan administration.

---

## 3. IRS Form 990

### Verified endpoint and format

Bulk XML from `apps.irs.gov`, one index CSV plus a set of zips per calendar
year, linked from
`https://www.irs.gov/charities-non-profits/form-990-series-downloads`.
`irs.gov/robots.txt` carries no relevant disallow. `apps.irs.gov/robots.txt`
302s and returns nothing.

Verified by download:

| Object | Size | Contents |
|---|---|---|
| index_2025.csv | full year | 748,906 returns: 376,920 form 990, 217,102 990EZ, 130,347 990PF, 24,537 990T |
| index_2026.csv | 42,987,660 bytes | 353,650 returns to 15 July 2026 |
| 2026_TEOS_XML_06A.zip | 210,803,934 bytes | 38,244 XML filings |

**The per-filing URL does not work and this is the source's one hard problem.**
The documented shape
`https://apps.irs.gov/pub/epostcard/990/xml/2026/{OBJECT_ID}_public.xml` returned
302 then 404 for two different object IDs, including one taken from a file that
demonstrably exists inside the bulk zip. `https://apps.irs.gov/app/eos/`
returned 403 to both a descriptive agent and a browser user agent. So there is
**no verified reader-facing URL for an individual filing**, and this repo's
first rule is no source URL, no record. The only citable URL today is the 210MB
zip, which is a dataset and not a receipt, exactly the distinction
`sec_execcomp` was careful about. Resolving this is a precondition, not a
detail, and until it is resolved the honest status is UNKNOWN.

### Worked example

`202601549349301375_public.xml`, from the 06A zip:

```
EIN                          310707369
BusinessNameLine1Txt         FRANKLIN UNIVERSITY
CityNm / StateAbbreviationCd Columbus / OH
TaxPeriodEndDt               2025-07-31
TotalEmployeeCnt             1297
CYSalariesCompEmpBnftPaidAmt 59,909,448
CYTotalRevenueAmt            111,960,173

Part VII Section A, 35 rows, first six:
  Dr David R Decker            President                     base 956,585  other 50,593  hrs 55
  Christi Farley-Cabungcal     Chief of Staff & SVP Admin    base 388,867  other 49,933  hrs 40
  Dr Christopher Washington    Provost and SVP-Academic      base 381,590  other 55,849  hrs 50
  Dr Godfrey Mendes            SVP Global Programs           base 375,861  other 57,235  hrs 40
  Rick Sunderman               SVP and CIO                   base 347,222  other 54,954  hrs 40
  Dr Marv Briskey              Treasurer/SVP-CFO             base 347,463  other 49,935  hrs 40
```

**This is the richest of the four and it is not close.** One filing gives named
executive pay with titles, a headcount, a total payroll figure and a revenue
figure, all filed and all deterministic. It is the same fact `sec_execcomp`
collects, for the employers `sec_execcomp` structurally cannot see. Every one of
the 4,000 filings sampled carried `TotalEmployeeCnt`.

### The claim it supports and the claim it does not

Supported: **"{person} was reported as {title} at {organisation} with base
compensation of ${n} in the tax year ending {date}, on the organisation's Form
990."** That is a filed figure about a named officer of a tax-exempt
organisation, which is public by statute and is the entire point of the
disclosure.

Not supported: any read of it as market pay. A 990 covers officers, directors,
trustees, key employees and the five highest-paid employees over $100,000.
Everyone else is invisible. `CYSalariesCompEmpBnftPaidAmt` divided by
`TotalEmployeeCnt` is arithmetic, not an average salary, because it bundles
benefits and pension into the numerator and part-time staff into the
denominator. Also: the pay is stated for the tax year, and filings arrive up to
about 18 months late, so a 2026 filing is often a 2024 fact. Date it by
`TaxPeriodEndDt`, the way `sec_execcomp` dates by period end.

Volume is large but the useful slice is not. 376,920 form 990 filings a year,
each with tens of Part VII rows. Filtering to organisations above a size floor,
which is what `uk_paygap` does with its band filter, is what makes it tractable.

---

## 4. State and municipal public employee salary disclosure

### Verified position

This one is different from the other three, and the difference is the finding.

`publicpay.ca.gov` is California's Government Compensation portal, the largest
of its kind. Its `robots.txt` is Cloudflare-managed and reads:

```
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /
...
User-agent: ClaudeBot
Disallow: /
User-agent: GPTBot
Disallow: /
```

The raw export at `/Reports/RawExport.aspx` 301s to `gcc.sco.ca.gov`, which
returned **403** to a descriptive agent, and whose root also returned 403.
`gcc.sco.ca.gov/robots.txt` carries the same content signals. So the largest
state payroll dataset is not open to an automated client without a decision that
is well above a scoping document's pay grade, and no attempt was made to work
around it.

Where a state uses a Socrata portal it is open. `data.ny.gov/robots.txt` allows
crawling with `Crawl-delay: 1`. The MTA payroll dataset `kcjb-nf3e` returned
**236,326 rows** to an unauthenticated JSON call. A row:

```
name             Hamann Jr,Carl F
working_agency   MTA HQ
title            Deputy Chief
department       7557-MTA Headquarter
start_date       1997-10-13     separation_date 2026-04-17
pay_basis        Biweekly       hourly_rate 159.981463
regular_pay      100,400.15     overtime_pay 0     cash_outs 213,285.19
total_earnings   313,685.34
```

### Recommendation: do not build this

Three reasons, in the order that decides it.

1. **It is person-level data about named individuals.** Every other source in
   this tracker is about an employer. This one is 236,326 named people and what
   they were paid, and that is true of every state portal, not just this one.
   Republishing it would change what this product is. The 990 route publishes
   named individuals too, but only the officers whose pay Congress made public
   precisely so it would be published, and only a handful per organisation.
2. **The employer is a government agency.** "MTA HQ" is not an employer a job
   seeker is comparing against Google. The audience is different, and the owner
   named San Francisco, New York, Austin and Los Angeles as hubs for a private
   sector product.
3. **Fifty states is fifty scrapers with fifty schemas and fifty robots
   positions**, one of which already blocks us by name. `report_source_health`
   would carry fifty entries whose breakage cost lands on the human sliver the
   project already tries to keep small.

Saying it is out of scope is a legitimate answer, and it is the answer.

---

## The cost that is not the parsing: the employer join, measured

`identity.py` resolves 3,025 of 4,819 cached employers. A DOL filer name, an SEC
registrant name, an ATS name and a news name are four strings for one company,
and this is where a large source stops being free.

**Method.** For each source, take two samples of 100 employer names: the largest
filers, and a seeded random draw. Run each through `pipeline.vocab.company_key`,
the pipeline's own key. Then ask three questions of the live database. Does the
key match an employer we already hold in `signals`? Is it in `employer_identity`
at all? Is it resolved there?

| Sample | Matches an employer we hold | In identity cache | Resolved |
|---|---|---|---|
| LCA, top 100 filers | 35 | 24 | 22 |
| LCA, 100 random filers | **2** | 1 | 1 |
| PERM, top 100 filers | 32 | 25 | 24 |
| PERM, 100 random filers | **5** | 3 | 3 |
| Form 5500, top 100 sponsors | 30 | 23 | 21 |
| Form 5500, 100 random sponsors | **1** | 1 | 1 |
| Form 990, top 100 by revenue | **0** | 0 | 0 |
| Form 990, 100 random filers | **0** | 0 | 0 |

**Read the random rows, not the top rows.** In the body of every one of these
files, between 0% and 5% of employers are anybody we have ever heard of. That is
not a defect in the join. It is what these sources are: the long tail of American
employers, which is precisely the part news never covers and precisely the part
we have no identity for.

The top-100 rows are worse than they look too. The 35 LCA matches cover 14.8% of
the file's rows, against 34.2% for the whole top 100. The misses are legal
entities, not unknown companies:

```
Amazon.com Services LLC             -> key 'amazon com services', we hold 'amazon'
Amazon Web Services, Inc.           -> we hold 'amazon'
Amazon Development Center U.S., Inc.-> we hold 'amazon'
Ernst & Young U.S. LLP              -> we hold 'ernst & young'
Deloitte Consulting LLP             -> we hold 'deloitte'
GOLDMAN SACHS & CO. LLC             -> we hold 'goldman sachs'
Visa Technology & Operations LLC    -> we hold 'visa'
Oracle America, Inc.                -> we hold 'oracle'
```

24 of the 65 unmatched top-100 LCA filers have a leading-token prefix that is
already a stored key. **Do not turn that into a rule.** `UST Global Inc` reduces
to `ust`, and `vocab.DISTINCT_EMPLOYER_SLUG_COLLISIONS` already exists because
three different "Cornerstone" filers taught this project what a greedy name
merge costs. The entities also differ in fact: Amazon Web Services and
Amazon.com Services are different employers with different pay.

And one plain result that should end any assumption the join is nearly solved:
**Google LLC does not match.** Its key is `google`. We hold `alphabet`,
`google uk` and `google-backed isomorphic`, and not `google`. The single largest
name in American technology hiring fails the join on the first try.

**What the join would actually cost.** DOL gives one thing no other source here
does: `EMPLOYER_FEIN` on 29,606 distinct employers, and Form 5500 and Form 990
both key on EIN as well. A FEIN is an exact identifier. Three of the four
sources could join to **each other** perfectly and for free. None of them joins
to `employer_identity`, because that table has `cik` and `ticker` and no EIN
column at all. Adding one is a schema change plus a resolution pass, and the SEC
does not publish a CIK-to-EIN map, so the CIK side of that bridge is UNKNOWN and
would have to be measured before it is promised.

---

## What this would do to the product

The tracker holds 29,289 current rows across four pillars, and rewards_comp is
8,832 of them. Set that against the sources:

- LCA at one metro, certified only, annual wage only, one half-year: 4,432 rows
  for San Francisco. Four metros, one year: roughly 48,000 rows.
- LCA unfiltered: about 420,000 rows a year, which is **fourteen times the
  entire database**, all American, all rewards_comp, all from one programme.
- Form 990 at 376,920 filings a year with tens of Part VII rows each: larger
  still, and all of it nonprofit.

**Unfiltered, any one of these stops being depth and becomes the product.** The
dashboard's country coverage, its pillar balance and its recall measurement
would all be describing one US filing programme wearing the tracker's clothes.
`uk_paygap` avoided exactly this with `DEFAULT_MIN_SIZE`, which cuts 11,153 UK
employers to 613. A size or metro filter is not a nicety here. It is the
difference between a feature and a takeover.

There is a second effect worth naming. `analysis/recall/us` measured 21/51 on
2026-08-11, and the US family is funding-only because US leadership events at
private employers could not be enumerated. Adding 400,000 US wage rows would
make the tracker look enormously more American without moving that recall figure
by a single event, because recall is measured against a gold set and not against
row count. A reader would experience it as noise, and the honest coverage claim
would get harder to write, not easier.

---

## Ranked recommendation

**1. Build IRS Form 990 first.** It is the only one of the four that produces
the same kind of record the pay pillar already publishes: a named executive, a
title, a filed figure, an employer. It extends `sec_execcomp` into hospitals,
health systems, universities and research institutes, which the SEC route cannot
reach and which the owner named. It is free, deterministic, and it carries a
headcount and a payroll total as a bonus. It joins 0 out of 200 sampled
employers against what we hold, and that is the argument for it rather than
against it: it is genuinely new coverage rather than more rows about Apple.
**Blocked on one thing.** There is no verified per-filing URL. Solve that before
writing a line of collector, because without it the source cannot satisfy the
no-source-URL rule and the work is wasted.

**2. Build DOL PERM second, not LCA.** Same programme family, a fifth of the
volume, annual rather than restated quarterly, self-reported employer headcount
in `EMP_NUM_PAYROLL`, and it reaches beyond technology into the employers LCA
never sees. It is the cheaper way to learn whether this family of data reads
well on the page.

**3. Build DOL LCA third, and only as a metro slice.** The four hub metros,
certified cases only, annual wage unit only, with `PW_WAGE_LEVEL` carried on
every row and the label in the headline rather than the footnote. About 48,000
rows a year, which the product can hold. Unfiltered LCA should never be
ingested. If the metro slice does not read well, the right move is to drop it
rather than to widen it.

**4. Form 5500 is worth building only after one of the above proves the shape,
and only as an employer-size signal.** Filtered to `TYPE_PLAN_ENTITY_CD = 2`,
single-employer plans, with multiemployer trusts and PEO filings excluded by
name and by code. Its value is `TOT_ACTIVE_PARTCP_CNT` against
`TOT_PARTCP_BOY_CNT`, a covered-headcount direction for a US employer, which
nothing else here gives. As a standalone pay source it has nothing to say.

**Do not build state and municipal payroll at all.** Reasons in section 4. The
largest portal blocks automated agents by name, the data is person-level about
private individuals, and the employer is a government agency rather than one a
reader is choosing between.

## Before any of these is built

- Nothing above has a collector, a health entry, a test or a dry run. **A market
  in the registry is not a covered market**, and a source in this document is
  not a source.
- Every one ships dormant with dry-run diagnostics first, mirroring
  `collectors/uk_paygap.py`, which is the closest existing pattern.
- Each builds a raw dict with `raw_text` set and goes through
  `classify -> validate -> store`. None of them writes a row.
- **Select columns explicitly.** The DOL files carry named individuals' email
  addresses and phone numbers, and Form 5500 carries signatory names. None of
  that may reach `raw_text`, a summary or the page.
- The employer join is the real work. Decide whether `employer_identity` gets an
  EIN column before the first of these lands, because retrofitting the key after
  400,000 rows exist is the expensive order to do it in.

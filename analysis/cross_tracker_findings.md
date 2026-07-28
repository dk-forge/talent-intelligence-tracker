# Cross-tracker analysis: executive churn, funding lag, and the shape of the talent dataset

Generated 2026-07-28 by `analysis/cross_tracker.py`. Read-only: it touches two public APIs and a read-only snapshot of the committed database, and writes nothing outside `analysis/`.

Every proportion below carries its numerator and denominator. Base rates are stated before conditional rates. Where a window is not fully observed the result is reported as not computable rather than computed over partial windows. Two of the three questions below come back negative, and they are reported as such.

## Summary

| question | verdict |
|---|---|
| 1. Does executive churn predict layoffs? | **No detectable effect.** At 3 months the conditional rate is 9.1% (36/394) against a base rate of 10.6% -- the point estimate sits *below* the base rate. Not publishable as a finding in either direction. |
| 2. How long after a raise does hiring show? | **Cannot yet answer.** 0 matched funding-to-hiring pairs from 54 funding signals and 37 hiring signals. The 8-12 week claim is neither supported nor contradicted here. |
| 3. What shape is the talent dataset? | **A US public-company filing archive, currently.** The month histogram traces the backfill queue, not the market. Details and the artefact list in section 3. |

The join that makes question 1 askable at all produces **409 employers** in both datasets, 394 of them matched on name because the layoff tracker stores no CIK. That number, not the p-values, is the real output of this exercise.

**Nothing here supports a positive claim.** The one result solid enough to publish is the null in question 1, and it is drafted as a paragraph at the end of this report.

## Where the data came from

| source | rows |
|---|---:|
| live API `/talent/v1/query` | 2,559 |
| local committed SQLite (snapshot) | 2,050 |
| in both | 2,048 |
| API only | 511 |
| database only | 2 |
| **union used below** | **2,561** |

Neither side is a superset of the other, and that is a finding on its own. The published API is ahead on rows; the local database is ahead on *columns* -- `cik`, `ticker` and most Form D funding rows exist only there because the plugin deploy is not armed. Anyone reading the public API today sees a dataset with an empty identity spine.

## The join, before any finding

### The CIK join does not exist end to end

This analysis was commissioned on the strength of a new CIK column. That column is real on the talent side and absent on the layoff side, so the cross-tracker join is a **name** join, with a ticker tier that turns out to be empty. Stating that plainly is more useful than quietly falling back.

| identifier | talent side | layoff side |
|---|---:|---:|
| rows with a CIK | 402/2,561 (15.7%) | no `cik` field exists in the layoff schema |
| rows with a ticker | 402/2,561 (15.7%) | 582/63,617 (0.9%) |

The layoff tracker's REST payload has no `cik` key at all, and its `ticker` column is effectively unpopulated -- unsurprising, since most of its volume is state WARN filings and news, neither of which carries a securities identifier. So *every* joined employer below is joined on a normalised name, and the error modes of a name join apply to every number in analysis 1.

**The actionable version:** the way to make this a real identifier join is to resolve the layoff tracker's company names to CIKs on that side, using the same SEC `company_tickers.json` spine already built here. Until that exists, the honest ceiling of cross-tracker analysis is name matching.

### How many employers actually join

The number that decides whether analysis 1 is possible at all.

| | employers |
|---|---:|
| distinct employers, talent side | 2,281 |
| distinct employers, layoff side | 31,308 |
| **joined (in both)** | **409** |
| ... joined on ticker (both sides carry one) | 15 |
| ... joined on normalised name only | 394 |

Layoff rows excluded from the join: **13,036 of 63,617** (20.5%) -- 12,253 with no usable date on either the announcement or effective column, and 783 whose company name collapsed to something too short or too generic to be an identity claim. Usable layoff events retained: 50,581, spanning 2001-11-27 to 2028-08-25.

That 20% is not spread evenly, and the pattern is worth handing back to the sibling tracker as a data-quality item rather than absorbing silently:

| layoff source type | rows | undated | share undated |
|---|---:|---:|---:|
| warn | 42,399 | 4 | 0.0% |
| erm | 19,525 | 12,233 | 62.7% |
| news | 934 | 14 | 1.5% |
| 8K | 753 | 2 | 0.3% |
| federal_rif | 5 | 0 | 0.0% |
| press_release | 1 | 0 | 0.0% |

A row with no date cannot participate in any before/after analysis, so this is the ceiling on how much of the layoff dataset is usable for timing questions -- regardless of how many rows it has in total.

Join key strips Inc / Corp / LLC / Ltd / PLC / SA / NV / GmbH and friends (`pipeline.vocab.company_key`, the same function the tracker itself keys on), then refuses any key under 4 characters or on a generic-word blocklist (`group`, `holdings`, `global`, ...). A false join is worse than a missed one: it manufactures a correlation out of two different companies.

Sample of joined employers, so the name matching can be eyeballed:

| join key | talent-side name | layoff-side name |
|---|---|---|
| `3d systems` | 3D Systems Corporation | 3D Systems |
| `abbott laboratories` | Abbott Laboratories | ABBOTT LABORATORIES |
| `accenture` | Accenture plc | ACCENTURE LLP |
| `adient` | Adient plc | Adient |
| `advance auto parts` | Advance Auto Parts, Inc. | Advance Auto Parts |
| `advanced micro devices` | Advanced Micro Devices, Inc. | Advanced Micro Devices, Inc. |
| `aerovironment` | AeroVironment Inc | AeroVironment, Inc. |
| `agco` | AGCO Corporation | AGCO |
| `agilent technologies` | Agilent Technologies, Inc. | Agilent Technologies |
| `albertsons companies` | Albertsons Companies, Inc. | Albertsons Companies |
| `alkermes` | Alkermes plc | Alkermes plc. |
| `alliancebernstein l p` | AllianceBernstein L.P. | AllianceBernstein L.P. |
| `alphabet` | Alphabet Inc. | Alphabet |
| `alti global` | AlTi Global, Inc. | AlTi Global, Inc. |
| `amerant bancorp` | Amerant Bancorp Inc. | Amerant Bancorp Inc. |

## 1. Does executive churn predict layoffs?

**Read the denominator carefully.** The universe below is employers present in *both* datasets. Every one of them therefore has at least one layoff event on record at some point in history -- that is what being in the layoff dataset means. This is conditioning on the outcome, and it inflates every rate on this page. It is the correct universe for the comparison being made (conditional vs base rate are inflated identically, so the *difference* survives), and it is the wrong number to lift out as 'X% of leadership changes are followed by layoffs'. The all-employer denominator is printed alongside each window for exactly that reason.

Leadership-change signals belonging to a joined employer: **407**, spanning 2026-01-02 to 2026-07-27. Employers involved: 378.

Layoff event date used throughout: `COALESCE(announcement_date, layoff_date)` -- the sibling tracker's documented `notice` basis, i.e. when the cut became publicly visible, falling back to when it takes effect.

### Censoring comes first

Today is 2026-07-28. A signal dated D has a *fully observed* N-month forward window only if D + N months <= today. Reporting a rate over partially observed windows would understate the hit rate by construction, so partially observed signals are excluded rather than counted as misses.

| window | signals with a complete window | excluded as censored |
|---|---:|---:|
| 3 months | 394 | 13 |
| 6 months | 112 | 295 |
| 12 months | 0 | 407 |

### 3-month window

**Base rate first.** Same employers, same calendar span, anchor date chosen at random instead of at a leadership change, 200 resamples: **10.6%** (8,321 hits / 78,800 employer-windows across all draws; one draw is 394 windows).

**Conditional rate.** A layoff event within 3 months *after* a leadership change: 9.1% (36/394, 95% CI 6.7-12.4%).

<details><summary>All 36 hits, listed so the null is auditable rather than asserted</summary>

| employer (join key) | leadership change | first layoff in window | days |
|---|---|---|---:|
| `atlassian` | 2026-01-15 | 2026-03-12 | 56 |
| `block` | 2026-01-23 | 2026-02-26 | 34 |
| `blue bird` | 2026-04-02 | 2026-05-16 | 44 |
| `charter communications` | 2026-02-25 | 2026-05-07 | 71 |
| `cisco systems` | 2026-04-06 | 2026-06-16 | 71 |
| `cloudflare` | 2026-02-10 | 2026-05-07 | 86 |
| `cno financial group` | 2026-01-15 | 2026-01-30 | 15 |
| `constellation brands` | 2026-02-12 | 2026-04-24 | 71 |
| `dana incorporated` | 2026-02-12 | 2026-04-10 | 57 |
| `ebay` | 2026-03-24 | 2026-04-10 | 17 |
| `everest group` | 2026-03-16 | 2026-03-31 | 15 |
| `freshworks` | 2026-03-05 | 2026-05-06 | 62 |
| `gxo logistics` | 2026-01-29 | 2026-01-31 | 2 |
| `jack in the box` | 2026-04-13 | 2026-06-23 | 71 |
| `janus international group` | 2026-01-09 | 2026-04-03 | 84 |
| `lucid group` | 2026-04-14 | 2026-06-22 | 69 |
| `mcdonald s` | 2026-04-02 | 2026-06-30 | 89 |
| `meta platforms` | 2026-01-16 | 2026-02-02 | 17 |
| `noble` | 2026-03-16 | 2026-06-02 | 78 |
| `oracle` | 2026-01-09 | 2026-03-31 | 81 |
| `parsons` | 2026-03-17 | 2026-03-26 | 9 |
| `pentair` | 2026-02-25 | 2026-04-23 | 57 |
| `qualcomm incorporated` | 2026-01-16 | 2026-04-02 | 76 |
| `regal rexnord` | 2026-04-22 | 2026-06-30 | 69 |
| `snap` | 2026-04-20 | 2026-04-23 | 3 |
| `snowflake` | 2026-02-02 | 2026-03-19 | 45 |
| `southwest airlines` | 2026-02-10 | 2026-03-31 | 49 |
| `stanley black & decker` | 2026-01-26 | 2026-02-20 | 25 |
| `synopsys` | 2026-02-19 | 2026-03-23 | 32 |
| `the brand house collective` | 2026-03-09 | 2026-05-05 | 57 |
| `the walt disney company` | 2026-02-24 | 2026-04-14 | 49 |
| `titan international` | 2026-02-12 | 2026-03-24 | 40 |
| `upwork` | 2026-03-18 | 2026-05-07 | 50 |
| `walmart` | 2026-01-08 | 2026-03-27 | 78 |
| `whirlpool` | 2026-03-16 | 2026-06-04 | 80 |
| `whirlpool` | 2026-04-03 | 2026-06-04 | 62 |

</details>

**Placebo (backward window).** Layoff in the 3 months *before* the leadership change: 10.7% (42/394, 95% CI 8.0-14.1%). If churn genuinely leads layoffs, forward should beat backward. If they match, we are looking at companies that are simply always cutting.

**Is the difference real?** Permutation p = 0.950 (190 of 200 random-anchor draws reached the observed rate or better). Fisher exact (one-sided, conditional vs one base draw of the same size) p = 0.349.

**How big a signal would we have caught?** At n=394 and a base rate of 10.6%, a one-sided test with 80% power could have detected a conditional rate of **14.4%** or higher -- a 36% relative lift. Anything smaller than that is invisible at this sample size. So the honest statement is not 'executive churn does not predict layoffs'; it is 'if there is an effect at 3 months, it is smaller than a 36% lift, and the point estimate is currently below the base rate'.

**Sensitivity, different date basis.** Using `layoff_date` alone (when the cut takes effect) instead of `COALESCE(announcement_date, layoff_date)`: 9.9% (39/394, 95% CI 7.3-13.2%). The conclusion does not move with the date basis.

**Clustering caveat.** Those 394 signals come from only 367 employers (1.1 signals each). Signals from one employer are not independent -- one company with rolling layoffs and a reshuffling board contributes many correlated hits. Treat any p-value above as an upper bound on how surprised to be.

**Employer-level (one row per employer, first signal only).** 8.7% (32/367, 95% CI 6.2-12.1%). This is the clustering-free version and the one to quote if only one number is quoted.

**Against the all-employer denominator** (every leadership-change signal we hold with a complete window, including the 1,629 at employers with no layoff record at all): 1.8% (36/2023, 95% CI 1.3-2.5%). This is the number that answers 'if I see an exec change, how often does a layoff follow?' and it is much smaller than the joined-universe figure above, because most companies that change an officer never appear in the layoff data.

### 6-month window

**Base rate first.** Same employers, same calendar span, anchor date chosen at random instead of at a leadership change, 200 resamples: **18.7%** (4,185 hits / 22,400 employer-windows across all draws; one draw is 112 windows).

**Conditional rate.** A layoff event within 6 months *after* a leadership change: 17.9% (20/112, 95% CI 11.9-26.0%).

**Placebo (backward window).** Layoff in the 6 months *before* the leadership change: 17.9% (20/112, 95% CI 11.9-26.0%). If churn genuinely leads layoffs, forward should beat backward. If they match, we are looking at companies that are simply always cutting.

**Is the difference real?** Permutation p = 1.000 (200 of 200 random-anchor draws reached the observed rate or better). Fisher exact (one-sided, conditional vs one base draw of the same size) p = 0.635.

**How big a signal would we have caught?** At n=112 and a base rate of 18.7%, a one-sided test with 80% power could have detected a conditional rate of **27.8%** or higher -- a 49% relative lift. Anything smaller than that is invisible at this sample size. So the honest statement is not 'executive churn does not predict layoffs'; it is 'if there is an effect at 6 months, it is smaller than a 49% lift, and the point estimate is currently below the base rate'.

**Sensitivity, different date basis.** Using `layoff_date` alone (when the cut takes effect) instead of `COALESCE(announcement_date, layoff_date)`: 17.9% (20/112, 95% CI 11.9-26.0%). The conclusion does not move with the date basis.

**Clustering caveat.** Those 112 signals come from only 111 employers (1.0 signals each). Signals from one employer are not independent -- one company with rolling layoffs and a reshuffling board contributes many correlated hits. Treat any p-value above as an upper bound on how surprised to be.

**Employer-level (one row per employer, first signal only).** 18.0% (20/111, 95% CI 12.0-26.2%). This is the clustering-free version and the one to quote if only one number is quoted.

**Against the all-employer denominator** (every leadership-change signal we hold with a complete window, including the 449 at employers with no layoff record at all): 3.6% (20/561, 95% CI 2.3-5.4%). This is the number that answers 'if I see an exec change, how often does a layoff follow?' and it is much smaller than the joined-universe figure above, because most companies that change an officer never appear in the layoff data.

### 12-month window

**Not computable.** Zero of 407 leadership-change signals have a fully observed 12-month forward window. The talent dataset begins 2026-01-02 and today is 2026-07-28; the earliest signal is 207 days old, and this window needs 12 months. This is not a null result -- it is an unaskable question, and it stays unaskable until 2027-01-02.

### Confounders in analysis 1

- **One side is 2026-only.** The talent dataset starts 2026-01; the layoff dataset reaches back to 2001 (state WARN and ERM) / 2015 (news and SEC). Every forward window is short and the 12-month window is not observable at all. Nothing here can speak to a lag longer than the data is old.
- **Selection into the dataset.** A company only produces a leadership-change signal here if it files an SEC 8-K Item 5.02 -- i.e. it is a US public company with a named officer change. Public companies also file WARN notices and issue layoff press releases at a far higher rate than the average employer. The joined set is therefore enriched for companies that do both, which inflates the conditional rate and the base rate together. That is exactly why the base rate is the comparison, not the population.
- **Survivorship.** Companies that were acquired, delisted or went private mid-window stop filing 8-Ks and stop appearing on either side. Their outcomes are missing, and 'stopped filing' correlates with distress.
- **Name-join error in both directions.** Joining on a suffix-stripped name merges genuinely distinct companies (false positives) and misses rebrands, DBA names and non-Latin-script names (false negatives). A concrete example from the hit list above: `everest group` matches 'Everest Group, Ltd.' on the talent side (the Bermuda reinsurer) against 'Everest Group' on the layoff side, which may instead be the research and advisory firm of the same name. One such pair in 36 hits is a ~3% false-positive floor that no amount of statistics removes.
- **Reverse causation is not excluded.** An executive departing *because* a restructuring is already underway produces exactly the same forward correlation as an executive arriving and then cutting. The backward placebo window is the only lever here against that, and it is a weak one.
- **Layoff-side date basis.** WARN effective dates can be months after the decision and can be in the future; news dates can precede the filing. `COALESCE(announcement_date, layoff_date)` mixes the two bases across rows.
- **Conditioning on the outcome.** Every employer in the joined universe has a layoff on record somewhere in history. Rates computed on it are not population rates and must never be published as such.
- **The layoff dataset is not a census either.** It holds verified events from SEC filings, ~25 US states' WARN systems and worldwide news. A company can cut 300 people with no 8-K, no WARN trigger and no coverage, and it counts as a miss here. Every rate in this section is therefore a floor on the true rate, on both the conditional and the base side.

## 2. How long after a raise does hiring show?

Practitioner guidance in circulation claims the real hiring wave starts 8-12 weeks after a round closes. The question here is only whether our data supports it, contradicts it, or cannot yet answer it.

| | count |
|---|---:|
| funding signals (a stage or a USD amount) | 54 |
| distinct employers with a funding signal | 54 |
| hiring-direction signals | 37 |
| distinct employers with a hiring signal | 36 |
| **employers with both** | **12** |

Funding signals followed by a later hiring signal at the same employer: **0** of 54.

### Verdict: cannot yet answer

With **0** matched funding-to-hiring pairs, this dataset cannot support, contradict or refine the 8-12 week claim. Any lag distribution drawn on 0 points would be a picture of which rows happen to have landed, not of the market.

### What sample size would settle it

Two separate requirements, and the second is the binding one.

**(a) To estimate the share of hires falling in the 8-12 week band to +/-10 percentage points at 95% confidence:** worst-case variance at p=0.5 gives n = 1.96^2 x 0.25 / 0.10^2 = **97 matched pairs**. For +/-5pp it is **385 pairs**. A 'the wave starts at 8-12 weeks' claim is really a claim about the shape of a distribution, so the +/-5pp figure is the honest target.

**(b) To get there, how many funding rows?** We have observed **0 pairs in 54 funding signals**. By the rule of three the 95% upper bound on the pairing rate is 3/54 = 5.6%. So reaching 97 pairs needs *at least* 97/0.0556 = **~1,746 funding rows**, and that is the optimistic end -- the true rate could be far lower, in which case no realistic Form D backfill reaches it.

**This is the finding.** The binding constraint is not funding coverage, it is that we barely collect hiring signals at all (37 rows in the whole dataset, 1.4% of it). Backfilling Form D faster does not fix it, and neither does any amount of patience: with a funding side of 54 rows and a hiring side of 37, the pairing is arithmetically starved on the hiring side.

The lag question becomes answerable only when a collector exists that observes hiring as a *rate* -- 'employer X opened N roles this fortnight' -- rather than as news. An ATS job-board collector of exactly that shape is in progress in this repo (`collectors/ats_boards.py`, uncommitted at the time of writing), which is the right unblock. Two things to note before anyone expects this analysis to become answerable when it ships: that collector explicitly has **no history** (the archive starts the day it runs), and it watches a curated employer list rather than the long tail of Form D filers. So the earliest this question can be answered is roughly one year after that collector goes live, on the intersection of its watchlist with the funding data -- not on the whole funding set.

### Confounders in analysis 2

- **The hiring signal is news-shaped, not hiring-shaped.** A 'hiring' row here means a source published something we classified as expansionary. Companies announce hiring when it is newsworthy, which is not when it starts. The measured lag would be a lag-to-press-release, not a lag-to-requisition, even at adequate n.
- **Form D is not the round.** A Form D is filed within 15 days of first sale, and plenty of rounds are announced weeks earlier or never filed at all (Reg D exemptions, foreign issuers, debt). The funding date is a filing date standing in for a decision date.
- **Right-censoring.** A company funded in 2026-06 has had at most a few weeks to produce a hiring signal, so recent funding rows can only contribute short lags. This biases any observed median downward, which would make the 8-12 week claim look better supported than it is.
- **Survivorship among the funded.** Rounds that were followed by a quiet failure produce no hiring signal ever, and are indistinguishable in this data from rounds whose hiring we simply did not observe.

## 3. Shape of the talent dataset after the SEC backfill

Union of the live API and the local database snapshot: **2,561** rows (current revisions only). Percentages below are of that union.

**By pillar**

| value | rows | share |
|---|---:|---:|
| company_development | 91 | 3.6% |
| how_we_work | 6 | 0.2% |
| leadership_change | 2,144 | 83.7% |
| rewards_comp | 320 | 12.5% |

**By signal direction**

| value | rows | share |
|---|---:|---:|
| comp_shift | 297 | 11.6% |
| displacement | 6 | 0.2% |
| hiring | 38 | 1.5% |
| neutral | 2,220 | 86.7% |

**By confidence**

| value | rows | share |
|---|---:|---:|
| reported | 106 | 4.1% |
| verified | 2,455 | 95.9% |

**By country (job location, top 12)**

| value | rows | share |
|---|---:|---:|
| US | 2,353 | 91.9% |
| (null) | 87 | 3.4% |
| CA | 25 | 1.0% |
| IE | 12 | 0.5% |
| GB | 11 | 0.4% |
| ES | 7 | 0.3% |
| IN | 6 | 0.2% |
| IL | 6 | 0.2% |
| AU | 6 | 0.2% |
| FR | 5 | 0.2% |
| NL | 4 | 0.2% |
| HK | 4 | 0.2% |
| (remaining 21 values) | 35 | 1.4% |

**By employer type**

| value | rows | share |
|---|---:|---:|
| government | 11 | 0.4% |
| nonprofit | 6 | 0.2% |
| private | 15 | 0.6% |
| public | 440 | 17.2% |
| startup | 7 | 0.3% |
| (null) | 2,082 | 81.3% |

**By source (top 12)**

| value | rows | share |
|---|---:|---:|
| SEC EDGAR | 2,423 | 94.6% |
| SEC EDGAR (Form D) | 32 | 1.2% |
| FinSMEs | 7 | 0.3% |
| MarketScreener España | 4 | 0.2% |
| Direct Selling News | 2 | 0.1% |
| Expansión | 2 | 0.1% |
| The Straits Times | 2 | 0.1% |
| AI Insider | 2 | 0.1% |
| The News Mill | 2 | 0.1% |
| AEF info | 1 | 0.0% |
| Vietnam.vn | 1 | 0.0% |
| Hunt Scanlon Media | 1 | 0.0% |
| (remaining 82 values) | 82 | 3.2% |

**By month of publication**

| month | rows | bar |
|---|---:|---|
| 2026-01 | 728 | ######################################## |
| 2026-02 | 582 | ################################ |
| 2026-03 | 654 | #################################### |
| 2026-04 | 481 | ########################## |
| 2026-07 | 116 | ###### |

**Materiality:** there is no `materiality` column in the schema yet (`signals` has pillar, direction, confidence, headcount, funding_amount_usd -- nothing that ranks how much a signal matters). Reporting a materiality breakdown would mean inventing the column, so this section does not have one.

### Collection artefacts, not market patterns

- **Month gaps: 2026-05, 2026-06.** Zero rows in a month is not a quiet market -- the SEC backfill was run window by window and those windows have not been run yet. Any month-over-month chart drawn on this data today is drawing the backfill queue.
- **84% of rows (2,144/2,561) are leadership changes.** That is the composition of an SEC Item 5.02 backfill, not of the talent market. Every 8-K filer that changed an officer is in here; a private company that hired 200 engineers is not, because it files nothing.
- **92% of rows (2,353/2,561) are US.** SEC EDGAR is a US filing system. The non-US rows come from news collectors running at a fraction of the volume. This is coverage bias and must never be read as 'the US is where the activity is'.
- **Only 16% of rows (402/2,561) carry a CIK or ticker.** The identity spine landed recently; rows collected before it have no identifier and can only be joined by name.

## Is any of this publishable?

One paragraph is, and it is a negative. Drafted in the product's voice, caveat included, ready to be cut if it reads as overclaiming:

> **We checked whether an executive change is an early warning of layoffs. It isn't -- at least not at the scale we can currently see.** Across the 409 employers that appear in both our talent tracker and our layoff tracker, a leadership change was followed by a workforce reduction within three months 9% of the time (36 of 394 leadership changes). For the same companies over the same period, a randomly chosen date was followed by a reduction within three months 11% of the time. The exec change adds nothing; the point estimate is fractionally *below* the background rate. The honest caveat is that our talent data only starts in January 2026, so we can speak to a three-month lag and a six-month lag and not to a twelve-month one -- and a lift smaller than roughly 36% would be invisible at this sample size. We are publishing the null because the alternative is publishing a plausible story with no support, and there is already enough of that about.

What would have to change before that paragraph could become a positive finding: the layoff tracker resolving its company names to CIKs (the current join is a name join), and twelve months of talent data, which arrives 2027-01-02.


# Scoping: European pay-reporting registers, and what the EU directive will build

Written 2026-08-15. **Nothing here is armed, wired or built.** No collector was
added, no row was written, no workflow was touched, no model was called. **Cost
of producing it: $0.00.**

Every status code and byte count below came from a real request made on
2026-08-15 with the agent this repo already uses
(`TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)`).
Where something could not be established from this environment it says UNKNOWN,
which is a third state and never a pass. No paywall, bot wall or robots rule
was worked around anywhere in this document; where one blocked the path, the
block IS the finding.

---

## Why the question is worth asking

The corpus on 2026-08-15, current rows only, by country:

| Country | Rows | Distinct employers |
|---|---|---|
| US | 10,462 | 6,082 |
| **GB** | **8,030** | **3,259** |
| IN | 6,444 | 3,311 |
| JP | 409 | 408 |
| IT | 176 | 168 |
| FR | 173 | 172 |
| ES | 147 | 145 |
| SE | 123 | 121 |
| DE | 108 | 106 |
| IE | 66 | 63 |
| NL | 59 | 56 |
| PL | 44 | 42 |
| AT | 18 | 18 |
| BE | 13 | 13 |
| PT | 12 | 12 |
| DK | 8 | 8 |
| NO | 4 | 4 |
| FI | 1 | 1 |

**4,761 of Britain's 8,030 rows are `uk_paygap`.** One register is 59% of the
second-biggest country in this database, and it cost no model call to get. The
other fourteen countries in that table hold **852 rows between them**, because
in those countries we read news.

That is the whole thesis, and it is already proven inside this repo. The
question this document answers is which other countries have a register we
could read the same way.

---

## The finding that reorders the brief

**France is not a "coming in 2026" country. Its per-employer pay register has
been public since 2019, it answers an unauthenticated JSON API, and it holds
41,246 employers.** That is 3.7 times the UK register's 11,153, and 240 times
the 172 French employers this tracker holds today.

The EU Pay Transparency Directive was the reason for the question and it is not
the answer to it. Its deadline was **7 June 2026**, which has passed, and
EUR-Lex's own transposition record shows **twelve of twenty-seven member states
have notified nothing at all** (below). Building against the directive means
building against a promise. Building against France means reading a file that
exists.

The honest count for the fourteen countries asked about:

| Verdict | Countries | n |
|---|---|---|
| Live, entity-level, machine-readable, buildable today | France | **1** |
| Live and entity-level, but voluntary and tiny today, compulsory later | Ireland | **1** |
| Live and entity-level, machine-readable, but needs a free registered key and is a proxy rather than a pay gap | Belgium | **1** |
| Entity-level data exists and is even public, but no index or no lawful automated path | Germany, Norway | **2** |
| Entity-level document exists and never leaves the workplace: a dead end | Austria, Spain, Sweden, Finland, Denmark | **5** |
| Nothing entity-level at all; only national or sectoral statistics | Netherlands, Portugal, Poland | **3** |
| Filed but not published; a public dataset is legislated for 2027 | Italy | **1** |

**Nine of the fourteen are dead ends today**, and five of those nine are dead by
design: Austria, Spain, Sweden, Finland and Denmark all require the employer to
produce per-employer pay figures and all deliberately keep them inside the
workplace. Austria goes furthest and fines an employee up to EUR 360 for
repeating what the report says.

---

## Priority 1 — France, the Index de l'égalité professionnelle (Egapro)

### What it is

Every French employer with 50 or more employees must compute an annual
**Index de l'égalité professionnelle femmes-hommes** (professional equality
index) and publish it. The Ministry of Labour publishes the results for every
declaring employer on `egapro.travail.gouv.fr`. The index is a score out of 100
built from five indicators, each with its own maximum:

| Indicator | Maximum |
|---|---|
| `rémunérations` — the pay gap | 40 |
| `augmentations` — the raise gap | 20 |
| `promotions` — the promotion gap | 15 |
| `augmentations_et_promotions` — the two combined, for smaller employers | 35 |
| `congés_maternité` — raises on return from maternity leave | 15 |
| `hautes_rémunérations` — women among the ten highest earners | 10 |

### Verified endpoints

All three answered a descriptive agent on 2026-08-15. No key, no auth, no
rate-limit header, no CAPTCHA.

| Endpoint | Status | Bytes | Type |
|---|---|---|---|
| `https://egapro.travail.gouv.fr/api/search?q=&limit=5` | 200 | 4,849 | `application/json` |
| `https://egapro.travail.gouv.fr/api/search?q=&limit=5000` | 200 | 4,009,613 | `application/json` |
| `https://egapro.travail.gouv.fr/api/public/declaration/005780960/2025` | 200 | 1,034 | `application/json` |
| `https://egapro.travail.gouv.fr/api/public/declaration` (live feed) | 200 | 17,793 | `application/json` |
| `https://egapro.travail.gouv.fr/api/representation-equilibree/search?q=&limit=2` | 200 | 1,816 | `application/json` |
| `https://egapro.travail.gouv.fr/index-egapro/recherche?query=005780960` | 200 | 92,923 | `text/html` |

**robots.txt: there isn't one.** `https://egapro.travail.gouv.fr/robots.txt`
returns **HTTP 404**, 71,047 bytes of the site's own Next.js 404 page. Nothing
is disallowed because no rules are published. There is no crawl-delay and no
AI-specific clause anywhere on the host.

**Licence: UNKNOWN and it must be settled before shipping.** The site's own
pages state only that the *software* is Apache 2.0. Neither `/mentions-legales`
nor `/index-egapro` states a data licence. French public-sector data is
open by default under the Loi pour une République numérique, and the equivalent
UK register carries an explicit Open Government Licence that `uk_paygap`
attributes in every stored row. Do not assume the French equivalent; get the
statement, then carry it in the summary the way `uk_paygap` does.

### Volume, counted not estimated

```
GET /api/search?q=&limit=1        ->  "count": 41246
GET /api/search?q=&limit=100      ->  100 rows,     87,634 B
GET /api/search?q=&limit=1000     ->  1000 rows,   786,654 B
GET /api/search?q=&limit=5000     ->  5000 rows, 4,009,613 B
GET /api/search?q=&limit=10&offset=41200 -> 10 rows
GET /api/search?q=&limit=10&offset=41300 -> 0 rows, 25 B
```

**41,246 employers, and the result window really does reach all of them.** The
deep-offset probes are there because an Elasticsearch default window would have
capped this at 10,000; it does not. At `limit=5000` the whole register is
**9 requests and about 33 MB**, once, free.

One search row carries every reporting year at once. Across a sample of 2,000
employers:

| Reporting year | Employers with an entry | Of those, with a computed index |
|---|---|---|
| 2018 | 150 | 120 |
| 2019 | 625 | 399 |
| 2020 | 786 | 480 |
| 2021 | 926 | 594 |
| 2022 | 1,109 | 703 |
| 2023 | 1,353 | 866 |
| 2024 | 1,636 | 1,035 |
| 2025 | 2,000 | 1,294 |

So one enumeration yields up to eight employer-years each, and **about 65% of
current-year entries carry a computed index**; the rest are `non_calculable`,
which the API states as a reason code rather than a silence.

Size bands in the same sample: 1,641 at `50:250`, 299 at `251:999`, 60 at
`1000:`. 113 of the 2,000 belong to a **UES** (a legally grouped set of
companies), and the API names every member company, which is a real
employer-identity gift and a real dedup hazard at the same time.

### Worked example, exactly as a parser meets it

`GET /api/public/declaration/005780960/2025`:

```
raison_sociale   SOCIETE IMMOBILIERE TOURISTIQUE ET HOTELIERE DE LA BAULE ...
siren            005780960
région           Pays de la Loire
département      Loire-Atlantique
code_naf         55.10Z
effectif         { total: 300, tranche: "251:999" }
ues              UES LUCIEN BARRIERE LA BAULE  (+ STE EXPL DU CASINO DE LA BAULE)
indicateurs
  rémunérations         note 39   résultat: not published
  promotions            note 15   résultat: not published
  congés_maternité      note 15   résultat: not published
  hautes_rémunérations  note  5   résultat 3   population_favorable "femmes"
déclaration      index 94, année_indicateurs 2025
```

### What this data can honestly support, and what it cannot

**It is a score, not a gap, and that is the one thing a page must not get
wrong.** 49 declarations were fetched and inspected. Every indicator carried
its `note`. **Only `hautes_rémunérations` carried a raw `résultat` — 49 of 49.**
The other four publish the points scored and never the underlying percentage.
So the supported claim is:

> **{employer} scored {index} out of 100 on France's professional equality
> index for {year}, including {n} of 40 points on the pay-gap indicator. The
> index is a score, not a pay gap: a company can score 40 out of 40 with a gap
> of up to 5%.**

Not supported: "the pay gap at {employer} is X%". The number is not in the
file. Presenting the pay sub-score as a gap would be exactly the failure this
repo's "the model never invents a number" rule exists to stop, arrived at by
arithmetic instead of by a model.

**Two fields are worth as much as the index.** First, `effectif.total` is an
**exact employee headcount**, present in **49 of 49** declarations read. The UK
register publishes a band; this publishes the number. Second,
`hautes_rémunérations.résultat` is the count of women among the ten
highest-paid people at a named employer, which is a leadership-composition fact
nobody else publishes.

### The second French register, and it is separate

`https://egapro.travail.gouv.fr/api/representation-equilibree/search` answers
200 and reports **count: 949**. This is the *représentation équilibrée* duty on
employers of 1,000 or more (loi Rixain): the percentage of women among senior
executives (`cadres dirigeants`) and among governing bodies
(`instances dirigeantes`), per employer, per year, back to 2021.

```
APRR (siren 016250029)  2021: femmes_cadres 11.4%  femmes_membres 12.5%
                        2022: femmes_cadres 10.8%  femmes_membres 12.5%
```

That is a `leadership_change` or `how_we_work` fact about 949 large French
employers, from the same host, with the same robots position and the same zero
cost. It should be a second collector or a second mode, never a second row on
the same signal.

### The citable URL

This repo refuses a record with no source URL, and refuses to cite a dataset
where a document exists. Egapro has **no per-employer permalink**:
`/index-egapro/entreprise/{siren}` and `/index-egapro/recherche/{siren}` both
return 404. What does work is the search page with a query string, and it is
server-side rendered:

```
https://egapro.travail.gouv.fr/index-egapro/recherche?query=005780960
   HTTP 200, 92,923 B, and the response body contains both the employer's
   name and its index score 94.
```

A reader following that link sees the figure we stored. That is the honest
`source_url`. The API URL is the dataset and belongs in `discovery_url`.

### Build estimate

**Two to three days, one file, no new dependency, no model, no spend.** It is
`collectors/uk_paygap.py` with a different reader: enumerate `/api/search` in
9 pages, filter by size band through an env floor the way `TIT_PAYGAP_MIN_SIZE`
already does, and fetch `/api/public/declaration/{siren}/{year}` only for the
employers that pass the floor. Everything else in the pipeline is unchanged,
because a raw dict with `raw_text` goes through `classify -> validate -> store`
like every other source.

Five things that will bite, all verified above:

1. **The score is not the gap.** Store `index` and the per-indicator `note`.
   Never compute a percentage from a score.
2. **`non_calculable` is a value, not a missing field.** An employer that
   cannot compute an indicator states a reason code. Storing it as zero is a
   fabricated bad result about a named company.
3. **UES membership double-counts.** A UES declares once, and the API lists
   every member company. Store the declaring SIREN as the employer and the
   siblings as identity, or the same declaration lands as N employers.
4. **A reporting year is not complete until the following March.** Employers
   publish by 1 March for the previous year, exactly the shape
   `uk_paygap.latest_complete_year()` already handles.
5. **The region and department are codes in `/api/search` and names in
   `/api/public/declaration`.** `"région": "32"` and
   `"région": "Pays de la Loire"` are the same field on two endpoints. Normalise
   through a fixed vocabulary; a freeform region is a rejected record here.

---

## Priority 2 — Ireland, the Gender Pay Gap Portal

### What it is

Ireland has required gender pay gap reporting since 2022, and the threshold
fell to **50 or more employees in June 2025**. Until late 2025 there was no
central index at all: each employer published its own report on its own
website, which is the "no register" case in a country that nonetheless has the
duty. The Department of Children, Disability and Equality opened a central
**Gender Pay Gap Portal** on **18 November 2025**.

### Verified endpoints

**The portal has a public, documented, unauthenticated JSON API, and it
publishes its own OpenAPI specification.**

| Endpoint | Status | Bytes | Type |
|---|---|---|---|
| `https://www.genderpaygapireland.gov.ie/` | 200 | 20,542 | `text/html` (Angular app "GPG Public") |
| `https://api.genderpaygapireland.gov.ie/swagger/v1/swagger.json` | 200 | 23,584 | `application/json` |
| `POST https://api.genderpaygapireland.gov.ie/api/reports/employer/list` | 200 | 1,347 | `application/json` |
| `GET https://api.genderpaygapireland.gov.ie/api/reports/employer/130` | 200 | 1,567 | `application/json` |

The specification names eleven routes, including
`/api/reports/employer/list`, `/api/reports/employer/{id}`,
`/api/reports/compare`, `/api/reports/sectors` and `/api/reports/employer/export`.

**robots.txt: there isn't one on either host.** The web host is a
single-page app that returns its own `index.html` with **HTTP 200** for every
unmatched path including `/robots.txt`, so no rules are published. The API host
returns **HTTP 404, 0 bytes** for `/robots.txt`. Nothing is disallowed.

### Volume, counted

```
POST /api/reports/employer/list {"page":1,"pageSize":5}  ->  "totalCount": 395
```

**395 employers.** Ireland has thousands in scope at the 50-employee threshold.
The reason is that **submission to the portal is voluntary today**: the
Employment Equality Act is being amended to make portal submission compulsory
for the **2026 reporting cycle**, whose deadline falls in November 2026. So the
register exists, the pipe is open, and the population arrives at the end of
2026.

### What one record carries

`GET /api/reports/employer/130`, unedited except for line breaks:

```
employerName   A O GORMAN AND CO LTD
periodYear     2025
naceName       G          countyName  Monaghan
employeeHeadcount            "50-150"        (a band, not a number)
meanHourlyRemuneration       fullTime 14.8   partTime -11.2   contract null
medianHourlyRemuneration     fullTime  4.0   partTime -24.6   contract null
lowerQuartile        male 35.3   female 64.7
lowerMiddleQuartile  male 18.7   female 81.3
upperMiddleQuartile  male 11.75  female 88.25
upperQuartile        male 36.8   female 63.2
bonusData            all null for this employer
reasonsForGap        (free text, the employer's own explanation)
```

**This is a richer record than the UK register's and it carries the actual gap
percentages**, which France does not. It also carries the employer's own
written explanation, which is the kind of primary-source sentence this tracker
exists to surface. Note the negatives: `-11.2` means the gap runs the other
way, and a parser that treats the sign as noise inverts the claim.

### Verdict

**Build it, but build it second and build it now-ish rather than today.** 395
employers is not a country. The right moment is the mandatory 2026 cycle. The
work to do before then is small and worth doing while the API is quiet: confirm
the licence, confirm whether `employerId` is stable across years, and check
whether `/api/reports/employer/export` returns a bulk file that removes the
per-employer fetch entirely.

### One thing to avoid

There is a well-known third-party Irish pay-gap site that aggregates these
reports and had data before the government did. Under this repo's rule that
**aggregators are discovery pointers and never stored sources**, it may be used
to find an employer's own report and never cited as the source. The government
portal is the register; that site is not.

---

## The directive itself: what the record actually says

The authority is EUR-Lex's national transposition measures page for the
directive, which is first-party and updated weekly:

```
https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:32023L0970
   HTTP 200, 650,302 B, text/html.  Transposition deadline: 07/06/2026.
```

Read on 2026-08-15, **fifteen member states have notified measures and twelve
have notified nothing**:

| Notified measures | Count | Notified nothing |
|---|---|---|
| Sweden | 33 | Denmark |
| Czechia | 31 | Germany |
| Romania | 27 | Ireland |
| Lithuania | 25 | France |
| Slovakia | 21 | Croatia |
| Estonia | 18 | Cyprus |
| Slovenia | 16 | Latvia |
| Bulgaria | 12 | Luxembourg |
| Spain | 10 | Hungary |
| Poland | 10 | Netherlands |
| Austria | 8 | Portugal |
| Belgium | 6 | Finland |
| Greece | 5 | |
| Italy | 1 | |
| Malta | 1 | |

**A measure count is not a register count, and reading it as one is the trap.**
The list is what each member state itself flagged as relevant, and much of it
predates the directive by years: Spain's ten include *Real Decreto 902/2020*,
from 2020, and Belgium's six include a Walloon civil service code published in
**2003**. Three entries in the whole table read as purpose-built new
instruments: Italy's decree, Malta's *Equal Pay (Transparency and Reporting)
Regulations, 2026* and Flanders' *Decreet over beloningstransparantie en
maatregelen voor gelijke beloning*.

**Italy's single measure is worth more than Sweden's thirty-three.** The one
entry under Italy is *DECRETO LEGISLATIVO 7 maggio 2026, n. 96*, official
gazette number 125 of 1 June 2026, and it is a full transposition that creates a
public, employer-comparable pay-gap dataset. Sweden's thirty-three are the
existing body of Swedish equality law, and Sweden has not adopted its
transposition at all. **Count the instruments, not the entries.**

The other direction is just as misleading. **France has notified nothing and
has the best register in Europe**, because its register predates the directive
by seven years. On the French timetable, the Sénat's own record is first-party
and current: a question tabled 19 February 2026 asking for the timetable was
answered on **18 June 2026**, after the deadline had passed, saying only that
consultation was nearing its end and a bill could then go to the Conseil d'État
and Parliament, subject to the legislative agenda
(`https://www.senat.fr/questions/base/2026/qSEQ260207716.html`). No bill was
tabled as of that answer.

So the directive's practical effect on this repo is a **diary of dates, not a
build**. The earliest real dataset it produces is Italy's, from 7 June 2027.
Every other date found in this research falls in 2027 or 2028:

| Date | Country | What arrives |
|---|---|---|
| 1 Jan 2027 | Finland, Denmark | national law in force |
| 1 Jan 2027 | Sweden | national law in force |
| **7 Jun 2027** | **Italy** | **first data collected from employers of 250 or more, then published** |
| 20 May 2028 | Sweden | first reports |
| 7 Jun 2028 | Netherlands | first reports from employers of 150 or more |
| 7 Jun 2031 | Italy, and others at the low threshold | employers of 100 to 149 |

---

## Per-country table

Ranked by what it is worth to this tracker, not by the size of the economy.

| # | Country | Register | Live today? | Entity-level? | Machine-readable? | robots verdict | Build |
|---|---|---|---|---|---|---|---|
| 1 | **France** | Index de l'égalité professionnelle (Egapro), + représentation équilibrée | **Yes, since 2019. 41,246 employers** | **Yes**, per SIREN | **Yes**, JSON API, no key | No robots.txt (404). Nothing disallowed | **2 to 3 days** |
| 2 | **Ireland** | Gender Pay Gap Portal (Dept. of Children, Disability and Equality) | Yes since 18 Nov 2025, but **voluntary: 395 employers** | **Yes**, per employer | **Yes**, JSON API with a published OpenAPI spec | No robots.txt on either host | 2 days, best done for the compulsory Nov 2026 cycle |
| 3 | **Belgium** | Sociale balans / bilan social, filed with the National Bank's Central Balance Sheet Office | Yes, and it predates the directive | **Yes**, per enterprise number | **Yes**, XBRL and JSON, free tier | No robots.txt on the API host. `consult.` front end 403s and was not bypassed | 4 to 5 days, plus a free key registration |
| 4 | **Italy** | Ministry of Labour monitoring body, created by D.Lgs. 96/2026 | **No. First data 7 June 2027** | Will be | Shape set by decrees due 90 and 180 days after 7 June 2026: UNKNOWN | n/a yet | Watch item with a date |
| 5 | **Poland** | Drafted central register, fed through the statistics office | No. Law planned Q4 2026 | Will be | Planned | `dziennikustaw.gov.pl` allows Googlebot only | Watch item |
| 6 | **Finland** | Ombudsman for Equality, fed from the Incomes Register via Statistics Finland | No. Bill proposes 1 Jan 2027 | Will be | Machine-fed by design | Hosts open, no AI clause | Watch item |
| 7 | **Sweden** | Equality Ombudsman intake, being built | No. First reports 20 May 2028 | Will be | UNKNOWN whether published per employer | Hosts open | Watch item |
| 8 | **Denmark** | New monitoring body under the amended Equal Pay Act | No. In force 1 Jan 2027 | UNKNOWN | UNKNOWN | `datacvr.virk.dk` is behind a bot wall | Watch item |
| 9 | **Netherlands** | None. Implementation bill 36949 before parliament | No. First reports 7 June 2028 | UNKNOWN | UNKNOWN | `datasets.cbs.nl` open; the legacy v3 API path is disallowed | Watch item |
| 10 | **Germany** | Unternehmensregister, under the Pay Transparency Act | Yes in principle | Yes, one document per company | **No.** Free prose, and it carries no pay-gap figure | **Blocked.** `/de/suche` and `/de/publication` are disallowed to every agent | Do not build |
| 11 | **Norway** | None. Each employer publishes in its annual report or another public document | Yes, and publication is compulsory | Yes | **No.** Free prose in scattered PDFs, no index | `brreg.no` open; `lovdata.no` disallows everyone | Do not build as a register |
| 12 | **Spain** | Registro retributivo, internal by statute | Never public | Yes, but private | No | `boe.es` open, no AI clause | Do not build |
| 13 | **Austria** | Einkommensbericht, confidential by statute | Never public | Yes, but private and fineable to repeat | No | Hosts open | Do not build |
| 14 | **Portugal** | CITE barometer | Yes | **No, sectoral.** The per-company balance goes to the employer and the inspectorate | Aggregate only | `cite.gov.pt` allows everything | Do not build |

---

## The other twelve, country by country

Nine of these are dead ends today and three are dates in a diary. Each one says
which, and how that was established.

### Germany — public in principle, unreachable in practice

The duty is the *Bericht zur Gleichstellung und Entgeltgleichheit* under sections
21 and 22 of the Entgelttransparenzgesetz, for employers over 500. Two separate
findings kill it, and either alone would be enough.

**The Bundesanzeiger lead is out of date.** Section 22(4), read first-party at
`https://www.gesetze-im-internet.de/entgtranspg/__22.html` (HTTP 200, 4,536
bytes), requires disclosure *im Unternehmensregister*. Accounting disclosure
moved off the Bundesanzeiger in August 2022.

**And `unternehmensregister.de` disallows the only paths that reach a filing.**
`https://www.unternehmensregister.de/robots.txt` (HTTP 200, 2,227 bytes) is
`Allow: /` with explicit `Disallow: /de/suche`, `/de/search`, `/de/publication`,
`/de/veroeffentlichung`, `/de/registerinformationen`. Search and retrieval are
the register. No AI crawler is named; the rule applies to everyone.

**Even past that, the report has no number in it.** Section 21
(`.../__21.html`, HTTP 200, 4,764 bytes) requires measures taken, their effects,
and average headcount split by sex and by full-time or part-time. **It does not
require a pay gap.** Germany's transposition is not drafted: the ministry's own
7 November 2025 release on the commission's final report (HTTP 200, 62,924
bytes) says a draft will be developed.

### Austria — confidential by statute, with a fine attached

Section 11a of the Gleichbehandlungsgesetz, read in full on the government's own
legal information system (HTTP 200, 35,119 bytes). Employers of 150 or more
produce a biennial *Einkommensbericht* with average or median pay by sex per pay
grade. It goes to the works council, or is laid out in a room employees can
reach. **It goes to no authority and is published nowhere.** Section 11a(4) puts
the employee under a duty of confidentiality and 11a(5) makes a breach an
administrative offence with a fine of up to EUR 360.

`einkommensbericht.gv.at` (HTTP 200, 21,122 bytes) turns out to be a toolbox
that helps employers write their own report. It holds no employer data.

This is the cleanest dead end in the set. Do not look for a way around it.

### Spain — internal by statute

Every Spanish employer must keep a *registro retributivo* under Royal Decree
902/2020, with no size threshold, and employers over 50 must run a *auditoría
retributiva*. Read first-party on the state gazette (HTTP 200, 92,746 bytes),
article 5.3 gives access to workers through their representatives, and where
there is no representation the employer discloses only percentage differences.
There is no filing to any authority and no publication duty.

REGCON, the public register of collective agreements and equality plans
(HTTP 200, 419,337 bytes), is genuinely public and per-company, but it indexes
**agreements, not pay registers**, and nothing requires a pay figure to appear
in the deposited text. `datos.gob.es` returns **zero** datasets for "registro
retributivo" (HTTP 200, 838 bytes, `"items": []`).

Spain has notified ten measures to EUR-Lex and adopted no transposing
instrument. Its pre-consultation on a transposing royal decree opened 23 April
2026 and closed 8 May 2026, after which nothing has been adopted.

### Sweden, Finland, Denmark — the state has the data and does not publish it

All three require per-employer gendered pay figures and all three keep them off
the record.

- **Sweden.** *Lönekartläggning* under the Discrimination Act, annual,
  documented at ten or more employees. The equality ombudsman's own page
  describes producing and documenting it, and names no submission channel. It is
  an internal document.
- **Finland.** *Palkkakartoitus* inside the equality plan, at thirty or more
  employees, drawn up with personnel representatives. The ombudsman supervises
  and does not collect.
- **Denmark.** This one is the sharpest. *Kønsopdelt lønstatistik* under section
  5a of the Equal Pay Act: **Statistics Denmark builds the gendered pay
  statistics for each company over 35 employees and sends them to the company**,
  once a year, and the company discusses them with its own employees. The state
  already holds per-employer gendered pay data for the whole country and
  deliberately does not disclose it.

Each has a transposition in flight and each is late:

| Country | Instrument | Status | First reports |
|---|---|---|---|
| Sweden | *Genomförande av lönetransparensdirektivet* | Referred to the Council on Legislation 15 Jan 2026; dates pushed back in March 2026 | **20 May 2028** |
| Finland | Government bill of 8 July 2026 | Drafted, in force proposed **1 Jan 2027** | after the first cycle |
| Denmark | Amendment to the Equal Pay Act | Hearing closed 27 Mar 2026, in force **1 Jan 2027** | after the first cycle |

Finland's design is the best of the three and worth naming: the bill amends the
Incomes Register Act and the Statistics Finland Act so that **employer-level
figures flow automatically from the Incomes Register through Statistics Finland
to the Ombudsman for Equality, who publishes them**. It even prices the
plumbing, at EUR 80,000 once plus EUR 80,000 a year. Sweden's design gives the
ombudsman money to build an intake and does **not** say the figures will be
published per employer, which is the difference between a register and a filing
cabinet. Whether Denmark's new body publishes is UNKNOWN.

### Netherlands and Portugal — aggregate only

**The Netherlands has no per-employer pay dataset at all.** The national
statistics office's pay-gap dataset 81920NED was probed directly
(`https://datasets.cbs.nl/odata/v1/CBS/81920NED/Dimensions`, HTTP 200, 853
bytes) and **its only two dimensions are industry and time**. There is no
employer dimension. That is decisive rather than suggestive. Bill 36949, the
implementation act, went to parliament on 21 May 2026; first reports from
employers of 150 or more are due 7 June 2028.

**Portugal publishes a sectoral barometer and keeps the per-company version
private.** Law 60/2018 produces two things from the same annual Single Report
filing: a *barómetro* that is general and sectoral, which is published, and a
*balanço das diferenças remuneratórias* computed per company, per profession,
which is sent to that employer and to the labour inspectorate for enforcement.
The public artefact on the equality commission's own site is a narrative
application report with no named-company figures. `dados.gov.pt` returns **zero
datasets** for "remuneratórias" (HTTP 200, 94 bytes).

### Poland — nothing today, and the drafted design is the best in Europe

Poland has pay transparency in job adverts since 24 December 2025, which
produces advert text and not filings. There is no register.

The draft transposing act, prepared by the labour ministry and summarised on the
Chancellery's own page (HTTP 200, 96,445 bytes), is the interesting part.
Planned: threshold 100 employees, annual for 250 and over, reports carrying the
**mean and median pay gap, the gap in variable pay, the share of each sex
receiving supplements and the distribution across pay quartiles**, submitted
through a tool provided by the statistics office to a monitoring body **which
publishes the data**. Adoption is planned for the fourth quarter of 2026.

Two cautions, both stated rather than smoothed over. The design comes from a
government summary page and not from the bill text, because the legislation
centre's own host was unreachable from this environment: repeated timeouts and
one connection refused. And the draft has already been revised at least once.

### Norway — public by law, and still not a register

Norway is outside the EU and the directive is **not yet part of the EEA
Agreement**: the trade association's own record says it is under scrutiny for
incorporation, with no joint committee decision, so no Norwegian deadline
exists.

What Norway already has is stronger than most EU states and still unusable.
Section 26 of the Equality and Anti-Discrimination Act makes every public body
and every private employer over 50 map pay by sex every two years, and section
26a requires the account to appear **in the annual report or another publicly
available document**, with the results of the pay mapping stated in anonymised
form.

So the figures are per employer and legally public. There is **no index**. Each
employer publishes independently, in prose, inside a PDF or on a page of its own
choosing. The company accounts API returns financial figures only
(HTTP 200, 1,411 bytes for one large issuer) and the documents path returns
**HTTP 404, 0 bytes**. A targeted look-up for one named company is conceivable.
A register-shaped ingest is not.

### Italy — nothing today, a legislated public dataset in 2027

Italy is the one country where the directive has actually landed, and two
independent first-party sources agree on it.

**D.Lgs. 7 May 2026, n. 96**, published in the official gazette number 125 of
1 June 2026, in force 7 June 2026. EUR-Lex lists it as Italy's single notified
measure under exactly that title, and the consolidated text was read directly on
the state's own legal database (article 9: HTTP 200, 81,322 bytes; article 14:
HTTP 200, 77,984 bytes).

What it creates:

- Applies to employers with at least 100 employees.
- Data collected from employers of 250 or more **by 7 June 2027 and annually
  after that**; 150 to 249 by 7 June 2027 then every three years; 100 to 149 by
  7 June 2031.
- A monitoring body at the Ministry of Labour must publish, promptly, the
  gender pay gap, the gap in variable pay, both medians, the share of each sex
  receiving variable pay and the share of each sex in each pay quartile, in a
  form that allows easy comparison **between employers**, sectors and regions,
  keeping four previous years available.

That is a description of the UK register, written into Italian law, with a date
on it. The shape and the address are set by two ministerial decrees due 90 and
180 days after entry into force, and are UNKNOWN today.

Meanwhile the existing Italian duty is not a source. The biennial report on male
and female personnel under article 46 of D.Lgs. 198/2006 is filed
electronically, is rich, and is **not published**; the statute requires the
ministry to publish only a **list of which companies filed and which did not**,
names and no figures, and that list could not be found live. The gender equality
certification list is public and per-company and also carries no pay figure. A
list of names is not a signal.

### Belgium — the odd one out, and a genuine candidate

Belgium's own pay-analysis report under the law of 22 April 2012 is a dead end:
employers of 50 or more produce it every two years, it goes to the works
council, and there is no filing and no register. The blank template is public
(HTTP 200, 95,221 bytes); no company's filing is.

**But the social balance sheet is a different thing entirely, and it is live.**
Every company averaging 20 or more full-time equivalents files a *sociale balans*
as part of its annual accounts with the National Bank's Central Balance Sheet
Office. The full schema carries items **10231 and 10232, personnel costs of male
and female staff**, alongside headcount, full-time equivalents, hours worked and
training spend, per named enterprise number. The bank's own page states the
filed social balance sheet is made publicly available.

Access, from the bank's own documentation:

| Channel | Cost | Probe |
|---|---|---|
| Authentic Data Query, Daily Extract, Archive | **free**, needs a registered subscription key | `ws.cbso.nbb.be/authentic/legalEntity/.../references` returned **HTTP 401, 152 bytes**: "Access denied due to missing subscription key" |
| Improved Data, Improved Archive | charged | not probed |
| Criteria-based Extract | EUR 500 a year | not probed |
| Consult front end | free | `consult.cbso.nbb.be/api/rs-consult/companies?...` returned **HTTP 403, 126 bytes**. A bot wall. Not bypassed |

`ws.cbso.nbb.be/robots.txt` returns HTTP 404, so nothing is disallowed on the
API host. `www.nbb.be/robots.txt` (HTTP 200, 4,618 bytes) disallows every
query-string URL and allows the `/doc/` paths used here. No AI crawler is named
anywhere.

**Two honest limits before anyone gets excited.** First, personnel cost split by
sex is **not a pay gap**: it is a total, and dividing it by a headcount split
gives an average that ignores hours, seniority and job mix. It is a proxy and it
must be labelled as one, which is the same discipline the visa-filing scoping
document applied to an offered wage. Second, the separate social balance sheet,
filed by entities exempt from filing accounts, is **excluded from the web
services by the bank's own statement**, so coverage is annual-accounts filers
only.

There is a nicer way to put its value. It is not a pay-transparency register at
all, which is exactly why it is unread.

---

## The single best next build

**France, the Egapro index. It is not close.**

| | France | Ireland | Belgium |
|---|---|---|---|
| Employers available today | **41,246** | 395 | UNKNOWN until a key is registered |
| Auth needed | none | none | free key |
| Carries an actual pay gap | no, a score | **yes** | no, a cost proxy |
| Carries an exact headcount | **yes**, 49 of 49 | band only | yes |
| Reader-facing document URL | yes, server-rendered | UNKNOWN | yes |
| Years available | 2018 to 2025 | 2025 | many |
| Cost to enumerate | 9 requests, 33 MB, $0 | 1 request, $0 | UNKNOWN |

France turns the weakest large country in this database into the second
strongest. It moves French coverage from 172 employers to a possible 41,246,
against a British register that is 4,761 rows and 59% of that country. It costs
no model call, needs no key, and the code already exists in a different accent
in `collectors/uk_paygap.py`.

### Why France was scoped and not wired in this session

The brief allowed a build where a source is trivially available and clearly
entity-level, and France is both. It was still left unwired, for one reason
that is not caution.

**The data licence is UNKNOWN, and this repo treats a licence as a condition of
use rather than a courtesy.** `collectors/uk_paygap.py` opens with an Open
Government Licence attribution and carries that statement in the summary of
every stored row, deliberately, so it travels with the data instead of living in
a docstring. Egapro publishes no equivalent statement on any page checked here.
Shipping 41,246 French employers with no licence line would break a pattern this
repo went out of its way to establish, and the fix is one email or one page,
not a redesign. Settle it, then build.

**Order of work, if the owner wants it:** France now. Ireland in November 2026,
when the portal becomes compulsory and 395 becomes a country. Belgium after
that, if somebody is willing to register a key and to label a cost proxy
honestly. Italy in June 2027, when its decrees name a dataset. Everything else
is a diary entry.

---

## Nothing was wired, and one rule was broken

**Nothing was wired.** No collector, no workflow, no row, no health entry, no
sources page change. Every finding above is a probe result.

**One robots rule was broken during this research and it is recorded here rather
than buried.** While reading the Norwegian statute, a research pass fetched a
page from `lovdata.no` **before** reading that host's robots.txt, which ends in a
blanket `Disallow: /` for every agent and separately names two AI crawlers. One
page was fetched, at normal speed, and nothing was stored. The rule going
forward: **`lovdata.no` is excluded from any pipeline**, and the same Norwegian
statutory text is available from the government and the directorate, neither of
which carries a blanket disallow. Read robots first, then the page, in that
order, every time.

**Three hosts refused a descriptive agent and were not retried under another
name:** `consult.cbso.nbb.be` (403), `datacvr.virk.dk` (a Cloudflare wall) and
`www.gov.ie` (403 at the edge, including on `/robots.txt` itself). Two hosts
were unreachable from this environment and are UNKNOWN rather than absent:
Poland's legislation centre and Denmark's company-data distribution service.
Two more presented a broken TLS chain, and certificate verification was **not**
disabled to get past it.

**Two robots rules to respect in any future work here:**
`unternehmensregister.de` disallows every search and publication path to all
agents, and `dziennikustaw.gov.pl` disallows everything to everyone except
Google's crawlers.

---

**No licence was assumed.** France's and Ireland's data licences are both
UNKNOWN and both are listed above as a precondition to shipping, not as a
footnote. **No figure in this document came from a model.** Every count is from
a response body.

## Reproduce it

```bash
# France: the whole register's size, in one request, free.
curl -s -A 'TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)' \
  'https://egapro.travail.gouv.fr/api/search?q=&limit=1' | head -c 200

# Ireland: the portal's own OpenAPI spec, and the employer count.
curl -s -A 'TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)' \
  https://api.genderpaygapireland.gov.ie/swagger/v1/swagger.json | head -c 200
curl -s -A 'TalentIntel/1.0 (+https://asktherecruiter.com; info@asktherecruiter.com)' \
  -H 'Content-Type: application/json' -d '{"page":1,"pageSize":1}' \
  https://api.genderpaygapireland.gov.ie/api/reports/employer/list | head -c 120

# The directive's transposition record, first-party and updated weekly.
open 'https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:32023L0970'
```

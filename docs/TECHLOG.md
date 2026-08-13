# Tech Log — Talent Intelligence Tracker

Chronological record of what was built, why, what broke, and how it was fixed.
Newest first. **Keep this updated:** every deploy gets a line; every incident
gets an entry with root cause and the guard added, so the next session inherits
the reasoning and not just the diff.

This file is for the **Talent Intelligence Tracker only**. The sibling AI Layoff
Tracker has its own at `/Users/dakotta/Projects/atr-layoff-tracker/docs/TECHLOG.md`.
They share a WordPress install and nothing else — different repo, different
plugin constant (`TIT_VERSION` vs `ALT_VERSION`), different tables, different
REST namespace. Never write one repo's state into the other's docs.

---

## 2026-08-13 - a bond is not a round: the capital-event classification. 1.79.0, merged, NOT deployed

**A deterministic classifier at extraction, and a home for its verdict in
`deal_type`. No published row was changed; the rows already in public are
listed at the bottom for the owner to decide about.** Plugin 1.78.0 -> 1.79.0.

### The defect

Four large-company capital events in one month were stored as venture funding
rounds:

| what it was | figure | what it actually is |
|---|---|---|
| ChangXin Memory | $8.6bn | a STAR Market IPO, retracted after publication |
| Oracle | $25bn | a corporate bond issue |
| Intel | $20bn | a public stock sale by a listed company |
| Nvidia | $709bn | an infrastructure financing arrangement |

Every one was caught by `guardrails.check_amounts`, and that is the actual
problem rather than the reassurance it looks like. `check_amounts` is a
MAGNITUDE check: it asks whether the corpus's own log-normal shape can explain a
figure. It has no idea what a bond, an IPO, a secondary offering or a project
financing IS. So the four were caught for a reason that **does not generalise
downward**, and the corpus proves it — Zions Bancorporation's

> "Zions Bancorporation has raised US$ 500 million in a senior notes issuance."

is the same class of event, is on the live page as a funding round, and sits
four orders of magnitude below any threshold the corpus could ever derive.
Nothing has ever asked about it and nothing was going to.

`deal_type` is NULL on all four of the rows above, which is the tell: the column
for "what kind of transaction is this" existed the whole time and nothing was
filling it for capital events.

### The blast radius, measured before anything changed

`pipeline/capital_event.classify` run read-only over every current row carrying
a funding figure (4,407 rows, 4,396 of them already published):

    REFUSED   29 rows
      public_offering    9    $20.41bn
      bond_issue         8     $5.80bn
      ipo                8     $2.01bn
      project_finance    4   $714.06bn
    already published    28   ($31.27bn)
    never published       1   (Nvidia, held by the amount guardrail)

So this is a **small fix with a correction decision attached**: 29 rows out of
4,407 is 0.66% of the funding corpus, and 28 of them are already in public.

### The rule, and why precision beats recall here

A rule that refuses a real venture round loses coverage silently and for ever —
the row is never stored, nothing counts it, and `measure_recall.py` reads the
loss as a market we do not reach. A rule that lets a bond through costs ONE
guardrail decision by somebody already reading that queue. Those are not
symmetric, so the rule refuses only instruments that exist nowhere but the
public and lender markets.

Three traps are wired into its shape, all of them already paid for here:

- **"raises" means nothing.** It is in all four headlines and in every real
  round. No pattern reads a verb.
- **Debt is a legitimate venture instrument.** "Kids2 Raises $225M in Debt
  Funding", "Karta Raises $140M in Debt and Equity Funding" and "Wonder Raises
  USD 12 Million Venture Debt from HSBC Innovation Banking" are all real stored
  rounds. `\bdebt\b` is not disqualifying and neither is `convertible note` — a
  convertible note is how a seed round is papered. `debt OFFERING`, `SENIOR
  notes` and `notes due 20xx` are, because those are sold to a market.
- **Employer identity cannot decide it.** A company can raise venture money in
  the same week it issues a bond, so no ticker, CIK or employer_type is read.

The measurement that matters most: run over the `publish_guardrails` amount
ledger, the rule fires on **none of the nine rounds a human has ACCEPTED** —
Anthropic x3, OpenAI, xAI, X.AI Holdings, Waymo, DeepSeek, Databricks — and on
Nvidia, Intel, ChangXin and AirTrunk. It also fires on none of the human
REJECTIONS that belong to other rules (Arch's AUM, A16z's and Blackstone's and
Kingswood's fund closes, Turkish Airlines' capex, Masimo's and Dillard's merger
consideration). Two vocabularies, two tolerances, no overlap.

One pattern was retracted during the measurement rather than shipped: a bare
`credit facilit(y|ies)` refused "Danish Entravel Group raises €6.5 million to
secure larger supplier credit facilities", a real round whose USE OF PROCEEDS is
a credit line. A purpose clause is not an instrument.

### What is deliberately let through

**Oracle.** "Oracle raises $25 billion and reassures skeptical investors",
summary "Oracle has raised $25 billion." Neither sentence names an instrument.
There is no honest deterministic verdict there and the classifier returns None
rather than guess from the fact that Oracle is large and listed. It stays the
amount guardrail's problem, and `test_capital_event.py` asserts the gap so it
stays a decision. At extraction the classifier also reads `raw_text`, so a
teaser that says "bond" catches the next one; the stored row no longer holds
that text, so the replay above could not test it.

**Fund closes, AUM and capex.** Already held, over-eagerly and harmlessly, by
`guardrails.NOT_A_COMPANY_ROUND`. Widening a REFUSAL to `\bfunds?\b` would
refuse "Emergent raises $70M from Khosla Ventures and SoftBank Vision Fund 2".

**Anything IPO-adjacent.** "IPO-bound ... raises $100m in Series D", "eyes IPO",
"pre-IPO financing" and "$50 Million Follow-On Series A" are all real rounds and
all pass clean, because the listing has to be near the money AND nothing may put
it in the future AND no private-round marker may be present.

### Where the verdict is written

In `deal_type`, and NOT in a fifth parallel refusal path. Three new values
(`bond_issue`, `public_offering`, `project_finance`) join `ipo`, which was
already there. One verdict does two things:

- **the FIGURE is refused** — `funding_amount`, `funding_amount_usd` and
  `funding_stage` are all left NULL, so the money never reaches the "Funding
  raised" tile, the money total, or anything computed from them;
- **the ROW survives, saying what it actually was** — which is what makes the
  refusal countable. `SELECT deal_type, COUNT(*)` is the tally and
  `capital_event.STATS` is the same fact per run. A silent drop is how a source
  posts zero while reporting healthy, and this project has shipped that once.

It never displaces a `deal_type` the model already read: Compass's 8-K says
"completed its acquisition of Anywhere Real Estate Inc. and issued $1,000.0
million ... Convertible Senior Notes due 2031", and the acquisition is the
better answer to "what kind of transaction". Both halves hold.

Three consumers, one definition:

- `validate.build_signal` — the store path, gated on a figure having been
  accepted. A leadership row that mentions a bond is not a bond row.
- `cheap_extract.parse_funding` — where Intel and Oracle were actually minted.
  That parser read only the HEADLINE for the class question while the teaser sat
  in `raw_text` unread; it now declines and the item takes the paid path.
- `guardrails.not_a_company_round` — so the auto-accept cannot publish a
  mega-bond the store itself would have refused.

### The already-published rows, which are the owner's decision

**Nothing was retracted and nothing was quietly corrected.** Retraction is a
credentialed act (`python3 retract.py <signal_id> "why"`) and this project
retracts rather than correcting in place, on purpose. The 28 published rows are
listed in the PR body.

---

## 2026-08-13 - the money charts' empty state said nothing useful. 1.78.0, merged, NOT deployed

**Copy, plus one probe query that runs only when a chart is empty. No data
changed, no filter semantics changed, no number moved.** Plugin 1.77.1 -> 1.78.0.

### The defect, from the owner's own use

He set Looking For to **Pay and Benefits** and Where to **United States**, and
all three money charts said:

> No US dollar amounts in this view yet.

True, and useless. It reads as data we failed to collect, so he asked why no
cities were showing. Nothing was missing. Measured on the live endpoint the same
day: `pillar=rewards_comp` alone returns **one** dollar-stated update out of
**8,838**, and it names no country, no city and no industry, so all three charts
are structurally empty there; `pillar=rewards_comp&country=US` holds **no
funding update at all**. Meanwhile `company_development` puts real millions
against New York, San Francisco, Austin, Boston, Seattle and Los Angeles. The
data was fine. The filters disagreed, and the page would not say so.

### Three causes, three sentences, because two of them want opposite advice

"Change your filters" is a CONFIDENTLY WRONG answer to one of the three, which
is worse than a vague one.

- **unplaced** - the view HAS amounts and this dimension places none of them.
  Real here: **655 of 4,094** amount-bearing rows carry a city. A coverage gap,
  and no filter touches it, so the copy says so and points at nothing:
  *"This view holds 4,094 updates with a US dollar amount, and not one of them
  names a city. That is missing detail in the sources, not a filter you can
  widen."*
- **pillar** - no amount in the view, and the selected pillar could not fill
  this chart with every other filter taken off. The pillar is the cause, so it
  is named in the CONTROL's own word for it (`tit_looking_options()`):
  *"No Pay and Benefits update we hold pairs a US dollar amount with a city, so
  this chart stays empty under that setting. Try Looking For: Raised Money."*
- **filters** - no amount, and the pillar is not the reason:
  *"No update in this view states a US dollar amount. Try a wider country or
  date range."*

The only number in any of them is the view's own `coverage.with`, off the query
the totals already run. Nothing is typed.

### What tells them apart, and what it costs

`tit_money_pillar_reach()` measures the selected pillar under
`is_current = 1 AND pillar = ?` and **nothing else**, deliberately: every other
control narrows within that set, so a zero there means the chart cannot fill
under this pillar whatever else the reader picks, which is exactly the claim the
copy makes. It runs **only when the view holds no dollar amount at all**, so a
page whose money charts are drawing pays nothing for it. Measured on the render
harness: **184,535 bytes and 15 cold queries, unchanged in both directions.**

The pillar travels to `tit_money_aggregate()` beside the WHERE clause and is
read for this and nothing else. The sums, the coverage figures and the rankings
are still the caller's own clause and nothing but it.

### The guard

`tests/test_money_empty_state_explains_itself.py`, and it asserts on
**`innerText` read off the rendered chart ancestor in headless Chrome**, never
on markup: this page hides text in closed `<details>`, and `textContent` reports
that as present. The browser half runs the shipped `paintMoney()` and
`moneyEmptyNote()`; the PHP half executes the shipped `tit_money_empty_note()`
and asserts the two say the same words, because the server prints one of these
on first paint and the browser reprints it on every filter change. One test
feeds four different counts and requires four different sentences, so a figure
written into a string cannot pass. `TheSentenceThisReplacedTests` needs neither
Chrome nor PHP, so the defect reds everywhere.

**NOT DEPLOYED.** The session runs
`gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker --ref main -f dry_run=false`
and verifies the page.

---

## 2026-08-13 - three provider names were on the public main for eight hours, and main's own CI could not have told anyone

**No deploy, no version bump, nothing armed, nothing spent.** One module added
(`pipeline/provider_names.py`), one test file added, three write paths changed,
11 committed data lines rewritten, one workflow trigger added. Full suite green,
**3,790 passed, 431 subtests**.

### What was actually wrong

`tests/test_no_provider_names.py` was failing on `origin/main`:

```
Banned data-provider name(s) found in tracked files (pattern numbers index the
base64 list in tests/test_no_provider_names.py):
data/gate_labels/labels-2026-08.jsonl (banned pattern #1);
data/gate_labels/labels-2026-08.jsonl (banned pattern #2);
data/gate_labels/labels-2026-08.jsonl (banned pattern #3)
```

Three different commercial data providers, 15 occurrences across 8 of the
shard's 13,455 lines, in `host` (7), `headline` (7) and `teaser` (1). Not on a
branch - on **main**, on a **public** repo. They arrived in two bot commits:
`3691ea6` (2026-08-13 08:34Z, patterns #2 and #3) and `9c92c61` (12:26Z, which
added #1). Nobody typed them. A collector read a real headline off a real feed,
`gate_ledger.record` wrote it down, and `collect.yml` committed it.

A full sweep of every remote ref found the shard on **main only**. Ten other
branches also hold names, all of them abandoned snapshots from 2026-07-29..31 -
1,100 to 1,500 commits behind, every one predating the `scrub:` commits that
anonymized those same files on main. They are the pre-scrub tree, not new
leaks. `scope/us-pay-filings` (PR #39) carries none of its own; it was red
because a `pull_request` run tests the merge with main.

### The hole that matters more than the leak

**Main's `tests` workflow did not run on either commit, and could not have.**

`on: push: branches: [main]` reads like "every commit on main is tested". It is
not. Nearly every commit on main is pushed by a job in this repo using the
default `actions/checkout` credential, and **GitHub does not start workflows for
a push authenticated with GITHUB_TOKEN** - by design, to stop a workflow
triggering itself forever. So the unreviewed commits, the ones no human ever
looks at, were exactly the commits the suite never ran on. The last `tests` run
on main before this was 06:45Z, two hours before the first leak.

The failure surfaced on somebody else's pull request, because a `pull_request`
run checks out the merge of the branch and main, so the PR inherited main's
tree. A guard that fires only when an unrelated contributor opens a PR is a
guard pointed the wrong way. `schedule: '43 * * * *'` on tests.yml closes it -
the suite is ~3.5 minutes and this repo's Actions minutes are free.

### Why the text was redacted and not dropped

PR #36 hit the same rule the day before, in `data/gold_bucket_sweep.json`, and
fixed it by dropping the headline and publisher at write time and keeping an
opaque sha1 prefix. Right there, wrong here, and the difference is what the text
is for. In the gold sweep the headline was context for a verdict. In the gate
ledger the headline and teaser **are** the payload: `train_gate_classifier.
features(headline, teaser)` is the only thing the local classifier is ever
fitted on, and that classifier is the whole route to the $5 target. Dropping the
text would satisfy the rule and leave 13,455 verdicts attached to nothing to
learn from.

So `pipeline/provider_names.redact` replaces each banned occurrence with an
opaque tag derived from that name's own sha1 - `[dp-xxxxxx]` - and leaves the
sentence around it standing. The observation survives, two different providers
stay two different tokens, and no name is recoverable from the file. The
patterns are held base64-encoded, the same convention
`collectors/national_press.py` uses for its aggregator blocklist.

### The write path, closed

The redactor is called from `gate_ledger._clean()`, which is the one function
every free-text field of a label line passes through (headline, teaser), plus
`_host()` and the rejection `reason` - the latter is not a formality, because
`validate` refuses aggregator hosts BY NAME, so the rejection message for the
candidates most likely to mention a provider is the one most likely to spell its
domain. `bootstrap_gate_labels.py` builds its lines by hand rather than through
`record()`, so `slug_text` and `host_of` redact there too.

Structure rather than a filter, for the reason PR #36 gave: a filter is only as
good as somebody remembering to call it, and this file is appended to twice a
day by a bot.

### The exemption that was doing the opposite of its job

`data/gate_labels/bootstrap-weak.jsonl` was EXEMPT from the guard, on the
reasoning that collected records are observations and rewriting them is
falsification. It held three provider names, publicly, for as long as it has
existed, and the one test that exists to find them was looking away by
construction. Redaction makes the argument moot - the record survives, only the
name goes - so the exemption is gone and only the binary database remains.

### What is still in git history, and it is the owner's call

The names remain in the history of both shards: `labels-2026-08.jsonl` at
`3691ea6` and `9c92c61` today, `bootstrap-weak.jsonl` since its introduction,
and the ten stale branches hold the whole pre-scrub tree. **Nothing here rewrote
published history** - on a public repo that invalidates every clone, breaks
every open PR and is not a subagent's decision. The options, for the owner:

1. **Leave it.** The working tree is clean, the guard runs hourly, and the
   history is a technical artifact nobody greps. Cheapest and reversible.
2. **Delete the ten stale branches.** They are 1,100+ commits behind, every one
   superseded, and deleting a ref does not rewrite anything. This removes the
   bulk of the exposure (the pre-scrub tree, dozens of occurrences across eight
   files each) for essentially no cost. Recommended first step whatever else is
   decided.
3. **Rewrite history** (`git filter-repo` over the two shards). Removes the
   remaining traces at the cost of invalidating every clone and every open PR,
   and GitHub keeps unreachable objects reachable by SHA until a support-side
   GC. Highest cost, incomplete benefit.

## 2026-08-13 - Form 990 built and shipped dormant; the receipt was never missing, it was one route away

**No plugin change, no version bump, no deploy. $0.00 spent: no model was
called, and `collectors/irs_form_990.py` cannot call one.** Suite 3,786 passed.
Full write-up in [SOURCE-irs-form-990.md](SOURCE-irs-form-990.md).

`docs/SCOPE-us-pay-filings.md` ranked IRS Form 990 first and blocked it on "no
verified reader-facing URL for an individual filing". That was right about the
routes it tried and wrong about the source, and the difference is worth
recording because the same shape will recur.

**What was actually dead.** The documented per filing XML path 404s, verified
on object IDs taken from a batch zip that contains the file. The AWS S3 bucket
`irs-form-990` is publicly listable and returns **zero keys** with
`IsTruncated=false`, so that route is gone rather than moved. `/app/eos` 403s
to a descriptive agent and to a browser User-Agent alike.

**What the scoping pass could not have found from curl.** Driving the interface
in a real browser shows it renders fine and that its organisation page has no
URL at all: `location.href` on the details view is
`https://apps.irs.gov/app/eos/details/` with no parameters, state held server
side. So the page a reader would supposedly open was never linkable. But the
page loads its content from `GET /teos/details/returnsSearch/{EIN}`, which
answers our own descriptive User-Agent with HTTP 200 and returns
`STATICFILEPATH` per tax period: the filed return as a PDF under
`/pub/epostcard/cor/`, which also answers 200. Neither is behind the wall that
stops `/app/eos`.

**The filename cannot be composed and that is a finding, not an inconvenience.**
`310707369_202407_990_2025081423655359.pdf` ends in an IRS posting date plus
`RETURN_ID`. The return id is in `index_2025.csv`; the posting date is in no
published file (`SUB_DATE` is the year alone, and the return's own `ReturnTs`,
`BuildTS`, `SignatureDt` and DLN all encode different days). So the URL is
looked up once per organisation and a filing with no copy posted is dropped
rather than cited to the 210MB batch zip. Receipt rate: 100/100 for index year
2025, 55/60 for 2024, **19/100 for the open 2026**, which is the measurement
behind `latest_complete_year()`.

**Two defects the live dry run caught and no amount of reading would have.**

- Matching `RETURN_TYPE` on the prefix `990` also matches `990T`, the unrelated
  business income tax return: a different form with no Part VII, filed for the
  same tax period by many health systems. Eleven rows in the first dry run,
  nine of them McLaren hospitals, cited a real IRS document that does not
  contain the row's figure. Now matched against `{"990", "990O"}` exactly, and
  where two copies exist for one period the most recently posted one wins.
- The largest Part VII figure is not always a person. The Bank of America
  Charitable Gift Fund's 2023 return carries $20,052,864 against
  `<BusinessName>BANK OF AMERICA` with `InstitutionalTrusteeInd` set: a
  corporate trustee's fee, in the same column as an officer's salary, forty
  times the largest real pay figure in the batch. The return draws the
  distinction itself, so the parser reads it and skips any group without a
  `PersonNm`. Five filings in one batch were nothing but such rows.

**The join was measured on the wrong population and is still zero.** The
scoping pass ran 100 random filers and got 0. Re-run on what would actually
ship: 0 of 96 at 1,000 employees, 0 of 227 at 500, 1 of 526 at 250 - and that
one is `Midwest Energy Inc`, a Kansas electric cooperative colliding with a
different `Midwest Energy Ltd` we hold. **No EIN column was added to
`employer_identity`:** there is no second EIN carrying source built, so the
column would join one source to nothing, and the EIN survives inside the
receipt URL so the decision costs nothing to reverse.

**Sizing.** 376,920 long form 990s a year unfiltered is thirteen times the
database. At `CYTotalRevenueAmt >= $100M`, measured by running the shipped
parser over two whole batches (31,706 returns, 8.4% of the year, 147 storable,
0.464%), it is about 1,750 rows a year: rewards_comp 30.1% -> 34.0%. A three
year backfill would take it to 40.7% and is deliberately not the default.
Revenue rather than `TotalEmployeeCnt` because that field counts seasonal
staff: at a 1,000 employee floor 40% of the population is YMCAs and Goodwills,
and at the $100M revenue floor it is 20.4% hospitals, 16.8% universities and
2.7% research institutes.

**Dormant.** Registered in `run_collect.SOURCES`, excused from the sources page
by `tests/test_sources_page.py::_DORMANT_COLLECTORS`, scheduled by nothing.
Arming it costs ~3.5GB of batch zips a year plus ~1,750 lookups, wants an
annual cadence, and needs a `staleness.py` ceiling that matches that cadence
rather than the daily rotation's.

---

## 2026-08-13 - the US was never in a late bucket. The country-need remedy is aimed at the wrong mechanism

**Measurement only. Nothing was armed, dispatched, deployed or written.** No
model was called, no row was stored, no ledger line was written, no cent was
spent, and nothing under `analysis/recall/` was touched. Two files added, both
measurement: `analysis/ranking/gold_bucket.py` and `tests/test_gold_bucket.py`.
Full suite green, **3,711 passed, 1 skipped**.

### The question, and why it had to be answered before anything was changed

The 2026-08-12 audit (PR #24) established that `interleave_by_country` gives
every country's best candidate a place before any country's second, that the
stored news population holds **77 country buckets**, that the **US bucket sits
50th**, and that at `DAILY_GATE_RATION` the US therefore takes **zero** places.
It then corrected itself in a way that undermined the conclusion drawn from it:
`candidate_rank.candidate_country` reads the Google News EDITION or the
publisher's country, never where the event happened, so the ranking
deprioritises US-SOURCED candidates and not US EVENTS. Which left
"country-need ranking caused the 26 walked-never-read misses" an **inference**,
and how many of the 51 gold events were ever in the US bucket **UNKNOWN**.

**It is not what happened.** Of the 51 US funding gold events, **45 were in the
US bucket, 6 never surfaced at all, and NOT ONE was in a foreign bucket only.**
Of the 26 classified `walked_never_read`, **21 were in the US bucket, 5 never
surfaced, none was foreign-only and none is undetermined.**

### Why the 77-bucket model does not describe the walker

`read_share.py --model` replays over `measure.stored_population`, which sets
`source_country` from the catalogue and **no `locale`**. That is the
`national_press` shape. The google_news walker's items always carry a `locale`,
`candidate_country` reads it first, and `fetch_day` de-duplicates by
`discovery_url` keeping the **first** edition that answered - and
`all_locales()` puts the `("en","US")` anchor **first**. So an article the US
edition surfaces is stamped `US:en` before any other edition can claim it. The
Sao Paulo case is real and it is rare: three events also appeared in an Italian,
German or Vietnamese edition, and in every one of them a US copy existed too and
ranked better.

Measured over 37 day-windows, 35 editions, 3,959 queries, **0 query errors**:

| | measured |
|---|---|
| country buckets in a day of the walk | **16 to 25**, never 77 |
| US bucket's position in the visiting order | **1st (median), never worse than 5th** |
| edition countries holding no rows at all | **2** (SN, UY) |
| US bucket size | median **126** candidates, **19.6%** of the day |
| candidates past the free prefilter | median **677/day** (274 to 1,149) |
| the ration | **37**, i.e. **5.5%** of a median day |

The US bucket is visited FIRST. The round robin was handing the US its place in
pass one on 33 of 37 days, which is the opposite of the finding it was about to
be re-weighted for.

### So what is starving it: depth, and the in-bucket order

The gold events are not the best candidate in the US bucket. Their in-bucket
depth runs 1 to 147, median 28, so they lose the ration to about 25 other US
candidates that scored higher, not to Brazil. Both levers, measured over the
same 26:

| policy | of the 26 |
|---|---|
| today, cut 37 | **2** |
| cut 99 | 5 |
| cut 118 | 5 |
| cut 217 | 7 |
| cut 395 (a full day) | 13 |
| whole day, no cut | 21 |

| a US floor | US places of 37 | of the 26 |
|---|---|---|
| 10% | 3 | 3 |
| 20% | 7 | 5 |
| 35% | 12 | 7 |
| 50% | 18 | 7 |
| the ENTIRE ration | 37 | **10** |

**Handing the US every single place in the ration collects 10 of 26. Leaving
the ordering exactly as it is and buying a full day's depth collects 13, and
the whole day collects 21.** A floor is not a cheaper route to the same place;
it is a smaller route, and it is the one that charges the other countries. The
audit's own last line was right for a reason it could not yet demonstrate:
**this is a money problem wearing a ranking problem's clothes.**

### Two things the ranking never touched, found on the way

- **6 of the 51 never surfaced at all** under the walker's own query set on the
  days around their announcement (Arpio, Adaptive Insurance, Speakeasy, Brinc
  Drones, logcat.ai, InstaLILY AI). 5 of those 6 are inside the 26. No cut and
  no floor reaches a candidate the query set never produced.
- **88 of 3,959 queries came back at the 100-item `RESULT_CAP`**, so those
  windows were truncated at a width the walker already warns about.
- **6 of the 26 are dated after 2026-07-12**, and the google_news cursor stands
  at **2026-07-13**. That walker never reached them; their `walked` credit comes
  from `press_archive`. The gnews ration cannot be the mechanism for those six
  whatever the ordering does.

### What this probe can and cannot generalise to

It re-walks the window the reference set was drawn from, so it says what
happened to **these 51 events, in 2026-06/07, in one signal type, in one
country**, and nothing about any other window. It is a diagnosis and never a
recall figure, and no number in it may be published as coverage.

Four more limits, each written into the module:

- Google News was **re-queried today** for historical days. The index churns, so
  a NOT-SURFACED row is weaker evidence than a bucketed one and is reported as
  its own state rather than folded into a bucket.
- The free reducers between the prefilter and the ration (`already_seen`,
  `validate.precheck`, the funding-duplicate check) are **not replayed**, and
  `already_seen` today reflects rows stored since. The pool is therefore larger
  than the walker's, so every rank here is a **pessimistic bound**: the real
  position is this one or better.
- An event that surfaced on several days is credited with its **best** day.
- A place inside the cut buys a **gate call**, never a stored row. Nothing here
  converts a place into a row and the tables say so.

The ranking context is built from rows captured by **2026-08-04**, when the
walker actually swept this window, rather than from today's database - a country
that was empty during the walk outranked the US and may hold rows now precisely
because that bonus worked. It changes nothing here (US 10,376 then against
10,437 now, the same 2 empty and 12 thin edition countries), and it is built
that way so the next session does not have to wonder.

### What should be done differently

**Do not re-weight the country need, do not add a US floor, and do not remove
the round robin.** The premise those rest on is measured false: the US is not in
a late bucket in the walker's own population, and the biggest floor available
recovers fewer events than simply reading deeper. The three things this
measurement does support, in order of what they buy per dollar:

1. **Depth is the only lever that moves this and takes nothing from any other
   country.** The audit priced it: $0.0877/day of history, $5.35 for this
   61-day window, $32.09/year. That is a spend decision and belongs to the
   owner.
2. **The in-bucket order is worth looking at before any of that**, and it is
   free. The gold events sit at median depth 28 inside a bucket the robin
   already visits first, so what decides them is `score()` among US candidates,
   where the country term is constant and only `employer_new` and
   `keyword_force` separate anything.
3. **The 6 that never surfaced are a query-set question, not a budget one**, and
   the 88 truncated queries are a window-width question. Both are free to
   investigate and neither is touched by any ranking change.

### One thing the committed artifact does not carry, and why

The first attempt to commit the sweep went RED on
`tests/test_no_provider_names.py`: two of the matched HEADLINES named a
commercial data service, which is banned in every tracked file. The headline
and the publisher name are now dropped **at write time** (`scrub()`), leaving
each hit an opaque sha1 prefix so a later sweep can recognise the same article
without the file carrying anybody's name. Dropped rather than filtered on
purpose - a filter is only ever as good as the list behind it, and this file
grows every time the sweep is run. Two tests pin it.

`python3 -m analysis.ranking.gold_bucket --report` reproduces every number above
from the committed `data/gold_bucket_sweep.json` without a network call.

---

## 2026-08-13 - the leadership parser closed zero because it was never shown a sentence it could read; the 25.9% gate ERROR was one outage, already fixed

**No plugin change, no version bump, no deploy. Nothing was spent: no
`OPENROUTER_API_KEY` exists in a subagent session, so every number below is
read out of committed state.** Reproduce all of it with:

```bash
python3 analysis/throughput/measure_levers.py     # read-only, no keys
```

### The gate ERROR rate is not a standing loss, and the triage already happened

`docs/MEASURE-throughput-levers.md` (PR #26, still open) flags **2,356 of 9,089
candidates at gate `ERROR`, 25.9%**, as the cheapest item on the board and says
it should be triaged before any lever is built. The count is right. The reading
is not.

| day | total | YES | NO | ERROR | ERROR% |
|---|---:|---:|---:|---:|---:|
| 2026-08-01 | 2,426 | 1,098 | 1,328 | 0 | **0.0%** |
| 2026-08-02 | 3,514 | 1,504 | 2,010 | 0 | **0.0%** |
| 2026-08-03 | 3,149 | 418 | 375 | **2,356** | **74.8%** |

Every single one falls on 2026-08-03, between 07:00 and 21:00 UTC. That is the
provider outage this repo already diagnosed and already guarded, in the entry
above dated 2026-08-04: `classify.gate_verdict` returns `ERROR` on
`Throttled`/`ClassifyError`, `run_collect`'s `ClassifyError` arm counts it with
the DEFERRALS and **deliberately does not mark the URL seen**, and
`run_outcome(mostly_errored=)` turns a run that could not judge its candidates
into a failure instead of a quiet one. Those candidates were not lost. They
were deferred, unmarked, and the next healthy run picks them up.

**So the 25.9% is a three-day window that contains one bad day, and there is no
bug to fix and no reads to win.** Worth saying plainly because the doc ranks it
above both levers on the strength of that number, and a session reading the doc
alone would go looking for a defect that was closed the day after it happened.

### Why `_parse_leadership` closed zero for the entire priced window

Shipped 2026-07-29, ran the whole window, closed **zero** of 1,328 google_news
leadership rows. Replaying it over the 1,085 stored leadership rows that join to
their own ledger line: **1,033 of them, 95.2%, die at
`_LEADERSHIP_SHAPE.match(headline)`** — the English appoints/names/taps/hires
verb list. The languages behind them are French 140, Spanish 118, Swedish 102,
Portuguese 85, Italian 71, German 68, Korean 61, Hebrew 54, Turkish 54, Dutch
36, against English 144.

Nothing was wrong with the parser. `cheap_extract` rule 4 says English only,
deliberately, and 64.3% of everything that reaches paid extraction is not
English. The parser was never shown a sentence it could read, and no amount of
tuning it would have changed that.

### `pipeline/leadership_intl.py` — the same parser, eight more grammars

French, Spanish, Portuguese, Italian, German, Swedish, Dutch, Turkish. Only the
chief-executive seat, because `directeur général`, `amministratore delegato`,
`consejero delegado`, `vd`, `Vorstandsvorsitzender` and the literal `CEO` all
mean one seat and one title label, while `Geschäftsführer` of a subsidiary and
`genel müdür yardımcısı` shade into descriptions exactly as the English list's
"head of" and "VP" do.

**Rule 4 is moved, not removed.** Korean, Hebrew, Japanese and Vietnamese
appointments — 176 of the 922 google_news leadership rows — still take the paid
path. A Latin-script name grammar has nothing to say about them, and getting
them wrong is worse than paying for them. The rule is now enforced by a module
boundary and a `LANGUAGES` set rather than by an early return.

**Measured against the paid model's own reading of the same URLs**, over the
124 candidates the grammar closes:

| | | Wilson 95% |
|---|---:|---|
| employer key agrees with the model | **121/124, 97.6%** | [93.1, 99.2] |
| person agrees | **124/124, 100%** | [97.0, 100.0] |
| pillar agrees | **124/124, 100%** | [97.0, 100.0] |

All 124 were hand-read, and all three disagreements are the parser being MORE
literal than the model's `company` column and agreeing with the model's own
summary: `Colliers France` vs `Colliers`, `Siemens USA` vs `Siemens`,
`Orchestre National de Lille` vs `l'Orchestre National de Lille`. **Zero wrong
extractions.**

**Every decline in the test file is a real headline that parsed WRONG at some
point while this was being measured**, and each one bought a specific guard:

- `Ecotel-CEO Markus Hendrich tritt zurück` — a departure read as an
  appointment inverts the record. `tritt zurück`, `quitte ses fonctions`,
  `dimite`, `avgår`, `istifa` all decline. 76 of 1,085.
- `Marc Schuler wird CEO bei Blaser Swisslube ab März 2026` — the stated start
  month welded itself onto the employer's name. The row has no column for a
  start date, so rule 3 declines it.
- `Swisscom Banking-Spezialist wird CEO von Inacta` — German capitalises every
  noun, so two capitalised tokens are not a name. `_DESCRIPTOR_PARTS` is
  checked against every hyphen-separated part of a token, which catches
  `Banking-Spezialist` without rejecting `Jean-Baptiste`.
- `Diego Escalada, nuevo CEO de Alkemy en España` and `Cambio al vertice in
  Alstom: Martin Sion...` — a LOWERCASE token that is not a name particle means
  the span crossed a clause boundary. That one check fixed five separate
  disagreements and took the employer agreement from 87.5% to 97.6%.
- `CHRISTOPHE PINARD-LEGRY NOMMÉ ... DE CANA L EUROPE` — an all-caps headline
  erases every capitalisation boundary the span checks depend on, and this one
  also carries the publisher's own typo.
- The German genitive `des` is deliberately absent from the pattern: `CEO des
  Basler Energieversorgers IWB` takes a descriptive noun phrase as readily as a
  name, and no rule can tell them apart in that language.

**No place is ever claimed.** These grammars have no place span they could read
without guessing, so `city` and `country` are empty and
`identity.place_if_unplaced` does the one free resolution it already does. An
invented country is the defect that had a US-filtered reader seeing 5 of 51
events, and a blank is honest.

### What lever 1 is actually worth, and it is a quarter of what was modelled

| | doc's figure | measured here |
|---|---:|---:|
| share of paid extraction volume closed for $0 | 24.7% MODELLED | **5.5% MEASURED** |
| $/month at today's caps | $3.66 | **$0.99** |
| extra reads/day | 92 | **25** |

167 of the 3,020 candidates that reached paid extraction in the window. The doc
modelled leadership closing at funding's own measured 53.8%; a precision-first
grammar reaches 11.4% of leadership volume, and 18.3% of the volume in a
language it reads at all. The gap is not tuning — it is the 37.6% in an
unsupported script, the 25.4% whose sentence shape nobody has written a pattern
for, and the 7.0% that are departures and must decline.

A free close skips the GATE as well as the extraction, because `cheap_extract`
runs before `classify.classify` and the gate lives inside it, so the saving is
`5.5% x ($14.82 extraction + $3.09 gate)`.

### Lever 2 is real, measured, and worth $0 once lever 1 exists

What the 612 wasted extractions actually are: **372 of 612 (60.8%) are
chief-executive appointments and only 87 (14.2%) carry a currency amount at
all.** The cross-language duplicate is an appointment, not a round — PayPal's
appointment of Enrique Lores was bought twice more after it was stored, once in
Turkish and once in Spanish; Disney's of Josh D'Amaro three times.

`dedupe.leadership_event_duplicate` matches **employer plus person, both
required**, against the stored row's own English prose rather than a column,
because there is no person column. That is exactly what makes it work across
languages: "Josh D'Amaro" is spelled the same in the Italian headline and the
English summary while every other word differs. Employer alone would collapse a
CEO in March and a CFO in April, which are two records.

**And then it saves nothing.** 15 of the 15 correct skips are also closed for
$0 by lever 1, and a free close already costs nothing — no gate, no extraction,
no read — after which the existing content-hash and fuzzy layers drop the row.
The doc's separate $3.01/month for lever 2 does not survive lever 1 being
built. The two were called "one piece of work"; they are closer than that. They
are one population.

It is kept because it is where the code belongs and because it records the skip
as a duplicate rather than as a dedup-suppressed store. Not because it saves
money, and the module says so.

### The false-drop audit, which is why the pre-check is in SHADOW

The precondition neither tracker had done. Ground truth is the ledger's own
terminal outcome: `duplicate` means we did hold it and skipping is the saving;
`stored` means we did not and skipping is a coverage loss that is **invisible
by construction** once it ships, because extraction never runs and nothing
downstream can contradict the decision.

Replayed with the candidate's own row excluded and every row captured after it
excluded:

| | |
|---|---:|
| correct skips (it WAS already held) | **15** |
| false drops (it was NOT held) | **1** |
| false-drop rate | **6.2%, 1/16, Wilson [1.1, 28.3]** |

The one false drop is `Norbert Pulin nommé Directeur Général de Colliers
France`, matched against `Réseau de conseil immobilier : Norbert Pulin nommé
Directeur Général de Colliers France`. Same person, same employer, same seat.
It is the same appointment, and the ledger label is wrong: the pre-check caught
a duplicate the existing layers missed. So the **coverage-losing** false-drop
rate hand-reads as **0 of 16**.

**0/16 has a Wilson upper bound of 19.4%, and nobody should arm a silent skip
on that.** `TIT_LEADERSHIP_PRECHECK` defaults to `shadow`: the check computes
the same verdict, prints what it WOULD have dropped, and drops nothing. That is
the task's own stated fallback and it is the right one — the ledger will bound
the rate properly in a few weeks of collection, for free, and the decision can
then be taken on evidence rather than on an interval.
`test_the_precheck_is_in_shadow_until_something_measures_it` guards the default
and says what would change it.

### Tests

`tests/test_leadership_intl.py`, 52 cases. Red before: `ImportError: cannot
import name 'leadership_intl' from 'pipeline'`, and with the module present but
unwired, `AttributeError: module 'pipeline.dedupe' has no attribute
'leadership_event_duplicate'`, `AttributeError: module 'run_collect' has no
attribute 'leadership_precheck_arms'` and `assert None is not None` on the
cheap_extract routing test. Green after, and the full suite is 3,701 passed.

---

## 2026-08-12 - the door for taking back a wrong country, and the lesson that a cancelled job still finishes its step (1.77.0)

**Plugin change and version bump only. PUSHED, NOT DEPLOYED.** The deploy is
the owner's call and this session was an agent, so the live correction has not
run yet and every "before" number below is measured off the live site as it
stands. No model was called and nothing was spent: this correction removes
values, it never looks one up.

### The lesson worth keeping: a cancelled job still completes its current step

The first live run of `place-unplaced.yml` used a placement bar that declined
only AMBIGUOUS names. It was cancelled a few minutes in, deliberately, the
moment a check against the US recall set showed it resolving Premier Lacrosse
League to Canada. The cancellation was right and it was not enough. **GitHub
cancels a job by refusing to start further steps; the step already running is
allowed to finish.** The step already running was the commit, so the run's
output landed on `main`, and a later `/enrich` carried it to readers.

37 rows now hold an `hq_country` with no headquarters city behind it. `Cancel
workflow` is not a stop button; it is a promise about the NEXT step. Anything
whose commit step is the thing you would want to take back has to be stopped
before that step starts, or reversed afterwards, and reversing is what this
entry is about. This is the second time this repo has learned that a run's
visible status says nothing about what it already wrote (the other is the
eviction signature: `cancelled` with zero jobs, `ci_status.py`).

### What is wrong on the live page, quoted from it

`hq_country` is read from P17 of the entity's HEADQUARTERS and falls back to
P17 of the entity itself. The errors live in the fallback. Queried from
`talent/v1/query` today, no cache buster needed to see it:

    Synthesia   country=None  hq_city=None  hq_country='CZ'
    headline: "Synthesia secures GBP 146 million Series E investment led by
               Google Ventures (GV)"

That is the Czech chemical works, filed over the UK AI company, on a public
page. It sits on page 2 of the 80 rows a reader gets from a Czechia filter,
because `country_basis=any` unions job location with employer HQ and this row
carries no job location at all.

**36 of the 37 are on the site; the 37th never published.** Checked one row at
a time against `talent/v1/query` by `signal_id`, which is the `content_hash`.
The exception is the SECOND Synthesia row, `89aae556...` from `press_archive`:
it is in the committed database carrying CZ and it is not in the live table. So
the reversal is 37 rows of local work and 36 rows of live work, and a run
reporting 37 changed locally against 36 changed on the site is the expected
shape rather than a discrepancy. Nothing else in the list has drifted since it
was written: zero rows have since picked up a city or a different country.

`Ash Games` is a German namesake; `CFS` is filed CA and
appears three rows later as Commonwealth Fusion Systems, US. All 37 are named
by content_hash in `data/cityless_hq_to_reverse.json`, a file and not a derived
query, so the pass takes back exactly what that one run wrote and not the
cityless values that were there before and are nobody's mistake.

### The reader-visible number, measured rather than repeated

Applying the plugin's own clause (`country IN ('US') OR (country IS NULL AND
hq_country IN ('US'))`) to the 21 US funding events the sealed recall set says
we hold, read one row at a time off the LIVE endpoint:

| | events |
|---|---:|
| a US-filtered reader sees TODAY | **7 of 51** |
| after the reversal runs | **6 of 51** |

The seven are AlphaSense, Ramp, Ollin Biosciences, Databento, RapidPulse,
Singularity and Crystalys Therapeutics. **Databento is one of the 37**: live it
reads `hq_city=None, hq_country='US'`, so its visibility rests on the same
weak fallback, and taking it back costs a row a reader can currently see. That
is the right trade and it is worth stating plainly: 6 honest is better than 7
where one of the seven is only accidentally right. AlphaSense is NOT one of the
37 and stays: it reads `hq_city='New York'`, which is the bar `is_placeable`
now requires.

Databento comes back at 7 later and correctly, city-backed: the second, tighter
placement run resolved it to Boston locally, and that row is one of the 33
employers still waiting on `enrich.yml`.

### The change: `tit_clearable_columns()` gains `hq_city` and `hq_country`

`/enrich` ignores an absent or empty field on purpose, so that an enrichment
pass with one missing lookup cannot wipe a column. The explicit `clear` array
is the narrow exception, and its allowlist was `funding_amount_usd` and
`funding_stage` only. The old comment said `hq_city` / `hq_country` were
deliberately outside it, because clearing looked-up identity loses work rather
than removing a wrong claim.

That reasoning held only while every stored value had actually been looked up.
These 37 were not: they are a hint the pipeline printed as a fact. There is no
right value to send instead, because the right value is that we do not know,
and `/correct` does not carry `hq_country` either. So the only correction
available was a clear, and there was no route for it at all. The allowlist is
widened, and the reasoning is written into the function's own docblock rather
than only here. What is NOT widened: a clear still has to be named explicitly,
so an absent or empty field can no more erase a headquarters than it could
erase a funding figure, and `archive_url` stays out (it is the fallback that
outlives a dead publisher, and clearing it really does only lose work).

`is_placeable` is untouched and the placement backfill was NOT re-run. A better
placement pass is separate work and needs the owner's sign-off on the bar.

### The test that was designed to go red, went red, and was inverted rather than deleted

`tests/test_reverse_cityless_hq.py::test_the_refusal_is_still_correct`
asserted, before: `not rev.site_can_clear()`, with the failure message "the
reversal has a door: queue reverse-cityless-hq.yml". It existed as an alarm for
the allowlist being widened. Widening it is exactly what happened, so the alarm
fired as designed.

It asserts, after: `rev.site_can_clear()`, and it now guards the door against
being SHUT again. Same divergence, other direction: a later edit trimming the
allowlist back would leave `reverse_cityless_hq.py` refusing with nothing in
the diff saying a live correction route had been removed. Keep it until every
row in `cityless_hq_to_reverse.json` is reversed on the site and the file is
retired.

Two neighbours moved with it, and both kept their subject rather than being
dropped:

- `test_applying_without_the_door_exits_two_and_writes_nothing` became
  `test_applying_with_no_credentials_exits_two_and_writes_nothing`. Its point
  was never the refusal text: it was that `--apply` must not move the local
  database when the site cannot be written, because the site is written FIRST.
  Missing `WP_SITE_URL` / `WP_API_KEY` is the failure that still reaches that
  path, and it now seeds a temp database and asserts the row is untouched.
- `tests/php/enrich_and_correct.php` asserted `hq_country` was refused by the
  allowlist. It now asserts `hq_city` and `hq_country` clear, and uses
  `archive_url` for the refusal case, so "the allowlist is still an allowlist"
  is still covered by a live assertion and not by a comment.

Full suite: 3,701 passed, 1 skipped, 431 subtests. `php tests/php/enrich_and_correct.php` green.

### What is NOT done, and the exact order for whoever holds the deploy

1. `gh workflow run deploy-plugin.yml -R dk-forge/talent-intelligence-tracker --ref main -f dry_run=false`,
   then check a reader's view: bare URL, browser User-Agent, no cache buster.
   Assets stamp as `TIT_VERSION.mtime`, so match the `1.77.0.` prefix.
2. ```
   gh workflow run drain-writers.yml -f enqueue=reverse-cityless-hq.yml \
     -f inputs_json='{"dry_run":"false"}' -f reason='take back the cityless hq'
   ```
   Queued, never dispatched. Then confirm on the live page that Synthesia reads
   no country, and re-measure the US-filtered count expecting 6 of 51.
3. Only then `enqueue=enrich.yml`, which carries the 33 city-backed placements
   that a WordPress 503 left behind. Running it BEFORE the reversal would push
   more of the 37 cityless values to readers, which is why it was held.

## 2026-08-12 - "US recall is low by design": true, but the design is the round robin and not the weights

**AUDIT. No behaviour change, no plugin change, no version bump, no deploy. No
model was called and nothing was written to the database.** Two files are added
and both are measurement: `analysis/ranking/read_share.py` and
`tests/test_country_need_ceiling.py`. Every number below is reproducible from
the two files this repo already commits (`data/gate_labels/labels-2026-08.jsonl`
and `data/talent_intel.db`).

The gap-map entry below states, as the reason 90% is out of reach, that
`candidate_rank` scores by country need and therefore reads the US last. That
sentence came from reading the weights. This checks it against what runs.

### The claim is TRUE, and it names the weaker of the two mechanisms

The weights part is exactly right, and here is the enforcing code:

```python
W_COUNTRY_EMPTY = 6.0      # we hold zero rows for this country
W_COUNTRY_THIN = 3.0       # we hold some, but under COUNTRY_THIN_ROWS
W_EMPLOYER_NEW = 1.5
W_KEYWORD_FORCE = 1.0      # per class of stated evidence, up to three
W_SOURCE_TIER = 2.0
COUNTRY_THIN_ROWS = 25
```

The US holds 10,437 current rows, so `score()` adds nothing for its country. A
news candidate's remaining ceiling is `W_EMPLOYER_NEW + 3 * W_KEYWORD_FORCE =
4.5`, against 6.0 for the first story about a country holding nothing. So no US
news candidate can outscore an empty country's, whatever its headline says.
Pinned in `test_a_saturated_country_cannot_outscore_an_empty_one_on_a_news_run`.

Two corrections to the claim as written, neither of which rescues the US:

- **"Never" is false by 0.5.** A FILING adds `W_SOURCE_TIER`, reaching 6.5. It
  is inert because a run collects from one collector at a time, which
  `candidate_rank`'s own docstring already says of that signal ("Inert in
  practice today"). It stops being inert in a backfill that mixes sources.
- **The score is not what binds.** `interleave_by_country` runs after the sort
  and gives every country's best candidate a place before any country's second.
  At the cuts this project runs at, that round robin — not the 6.0 — is what
  decides whether the US is read at all.

### The round robin is the binding one, and it makes the share ZERO, not small

Measured over the 4,060 stored news candidates
(`python3 -m analysis.ranking.read_share --model`): 77 country buckets, the US
bucket's best candidate scores 2.0, which places it **50th of 77** in the round
robin's visiting order.

| read cut | what that cut is | US places, ranked | US places, arrival |
|---|---|---|---|
| 37 | `backfill_gnews_2026.DAILY_GATE_RATION` | **0** | 3 |
| 99 | `classify` cap, google_news | 1 | 6 |
| 118 | `classify` cap, national_press | 2 | 6 |
| 217 | `BINDING_READ_BUDGET`, both | 5 | 9 |
| 395 | `MEASURED_CANDIDATES_PER_DAY`, full depth | 18 | 17 |

**A cut smaller than the number of countries present never finishes pass one**,
so a bucket sitting after the cut receives nothing that run — not a smaller
share, nothing. That is the whole mechanism, and it explains the shape of the
last column: at full depth the ranking is neutral for the US (18 against 17),
because the round robin has reached everybody by then.

### What the budget actually bought, counted rather than modelled

`python3 -m analysis.ranking.read_share --ledger` over all 11,824 real gate
decisions in `labels-2026-08.jsonl` (2026-08-01 to 08-03; the 03rd is the gate
outage day, so treat its volumes as retries):

| collector | candidates | US | gate survivors | US | places under the cap | US |
|---|---|---|---|---|---|---|
| google_news | 8,853 | 1.6% | 2,137 | 4.0% | 1,993 | 3.5% |
| national_press | 903 | 11.3% | 290 | 23.4% | 224 | 14.3% |
| gdelt | 1,703 | 31.5% | 726 | 30.4% | 696 | 30.7% |
| ALL | 11,824 | 7.1% | 3,262 | 12.5% | 3,019 | 11.6% |

"Places under the cap" and not "reads": the cap counts `STATS["full_calls"]`,
incremented on entering stage 2 inside `classify.classify()`, which is upstream
of the dedup layers and of the conditional second pass. A candidate later found
duplicate still spent a place. 612 of the 3,019 did.

The share on its own understates it, because a country that supplies few
candidates should receive few reads. **The asymmetry is the finding:**

    national_press   52.9% of US gate survivors DEFERRED, against 13.5% of
                     everywhere else's  (n = 68 vs 222)
    google_news      17.6% against 6.2%  (n = 85 vs 2,052)

A deferral is not a loss — the candidate is left unmarked and returns — but
under a standing ration "returns next run" and "read four times less often" are
the same sentence.

**gdelt is the control that proves this is the ranking and not something about
American stories.** It is 31.5% US and gives 30.7% of its places to the US,
because its demand does not reach its ceiling, so nothing is rationed and the
ordering never binds.

### Against an even split and against a volume-weighted one

The question "is the US starved" has three different answers and they are all
correct, which is why the argument about it goes round in circles:

| yardstick | what the US would get | what it gets |
|---|---|---|
| even split over the 77 countries present | 1.3% | 11.6% of live places, **0%** of the walker's ration |
| volume-weighted (its 6.9% share of candidates) | 6.9% | same |
| share of gate SURVIVORS (12.5%) | 12.5% | 11.6% |

**The round robin already IS an even split**, so "give every market an equal
share" is not a change to ask for — it is what runs. On the live path the US
does slightly better than even, because the collectors whose demand never
reaches their ceiling (gdelt, press_archive) are 30-34% American and nothing
rations them.

Where the design bites is the two places a cut is smaller than the country
count: `national_press`, where 52.9% of US gate survivors defer, and the
historical walker, where the ration of 37 does not reach bucket 50. **The gold
set is June 2026 history, so the walker is the path that matters for the US
recall number, and on that path the measured share is zero.**

### Deliberate, or emergent? Deliberate, argued, and the argument is good

There is a written rationale and it should be read before anybody changes a
weight. `pipeline/candidate_rank.py` under WHY THESE SIGNALS AND NOT OTHERS:

> 57 of 200 countries hold any row at all, so 143 hold nothing; of the 55 that
> are neither US nor GB the median holds ONE row, and 15,140 of 15,711 current
> rows are US or GB. That concentration is the product's largest measured defect

and `interleave_by_country`, on why a round robin and not a quota:

> what is scarce is the FIRST row about a place, and each additional row about
> the same place is worth much less

`CLAUDE.md` says the same thing from the other end — "a country scoring zero is
an instruction rather than a statistic" — and `classify.READTHROUGH_CAP` cites
the ranking as what makes rationing acceptable. So this is not drift. **What it
buys is the worldwide claim**: 104 countries hold rows today against 57 when the
weights were set, and no other lever in this repo produces that.

What was NOT chosen is the consequence for the home market. Nothing in any
comment or entry weighs "the US is the largest market and the sibling tracker's
audience" against country need. The tradeoff is real, it is defensible, and it
was never stated as a tradeoff until the gap map hit it.

### One correction that changes what any of this can claim

`candidate_country` reads the Google News EDITION or the publisher's own
country, and says so:

> A hint, never a claim: `source_country` is the publisher's own country and
> `locale` is the Google News edition, and neither is what the story is about

So the ranking does not deprioritise US EVENTS. It deprioritises candidates
surfaced by US editions and US publishers. **A US funding round written up in
Sao Paulo is ranked as Brazil and collects the full 6.0.** Which means the
sentence "country-need ranking caused the 26 walked-but-never-read misses" is an
inference and not a measurement: **how many of the 51 gold events sat in the US
bucket is UNKNOWN**, and until it is known, the size of the prize from any
re-weighting is unknown with it. Pinned in
`test_the_penalty_falls_on_the_PUBLISHER_country_and_not_the_story`.

It is cheap to settle. `backfill_gnews_2026.py --fetch-only` sets
`writes = not (args.dry_run or args.fetch_only)`, gates nothing and calls no
model, so a re-walk of 2026-06-01..2026-07-31 with `--fetch-only` costs **$0**
and prints the country bucket of every candidate it would have gated. Match its
output against the sealed set and the causal question is answered before a cent
is spent.

### The options, with what each costs the other 104 countries

Modelled on the same 4,060-candidate population, `--model`. `countries` and
`thin` are the price: a place given to a market that already reads well is a
place taken from one that does not.

| policy | US at cut 37 | US at 118 | countries at 37 | countries at 118 |
|---|---|---|---|---|
| current | 0 (0.0%) | 2 (1.7%) | 37 | 77 |
| arrival (no ranking) | 3 (8.1%) | 6 (5.1%) | 2 | 15 |
| score, robin removed | 0 | 0 | 19 | 47 |
| floor: US 10% of every cut | 3 (8.1%) | 11 (9.3%) | 35 | 77 |
| floor: US 20% | 7 (18.9%) | 23 (19.5%) | 31 | 77 |
| volume-weighted | 2 (5.4%) | 9 (7.6%) | **2** | **4** |

Read that table twice before choosing:

- **Leave it alone.** $0. US stays where it is; repeat walks skip what stored
  and spend the ration on the next-best candidate, but the US bucket is 50th of
  77, so what it gets on the next walk is 0 again. The worldwide figure is
  protected. Nothing improves for the home market, ever, without a separate
  decision.
- **Volume weighting is the trap.** It is the rule most people reach for and at
  these cuts it collapses breadth from 37 countries to 2. It would raise US
  recall and demolish the thing the product sells. Refuse it.
- **Removing the round robin and keeping the score is worse than either.** 0
  for the US AND fewer countries (19 at cut 37). The robin is not the problem.
- **A floor is the honest cheap option, and its cost is exact.** US 10% at cut
  37 is 3 or 4 places, taken from the buckets sitting at positions 34-37 — the
  marginal country of that day, which loses its only story. Distinct countries
  in the cut drop 37 to 35; thin-country candidates drop 30 to 27. That cost is
  small in the table and large in kind, because those are FIRST rows about a
  place.
- **Raising the ration is the only option that takes nothing from anybody.** At
  a cut of 395 the ranking is already neutral (18 vs 17 arrival) and every
  country keeps its place. It is the only lever here that is not zero-sum, and
  it is priced: $0.0877 per day of history, $5.35 for the 61-day window,
  $32.09 for a year, against a $10 monthly allowance of which the LLM gate
  already takes $4.41-$5.70.

**So the honest framing is: this is a money problem wearing a ranking problem's
clothes.** Every non-monetary option moves the same fixed 37 places around.

### The realistic US ceiling under each, and 90% under none

The measurement is 21/51, 41.2%, Wilson 28.8-54.8. The bound the gap map
established is 41.2% to 96%, and nothing here narrows it. What can be said per
option, with the guesswork labelled:

| option | US recall | confidence |
|---|---|---|
| leave alone | ~41%, flat | measured floor; the flatness follows from bucket position 50/77 |
| floor 10% | UNKNOWN, and bounded by how many of the 51 are in the US bucket at all | that fraction is unmeasured, so no number can be given |
| floor 20% | UNKNOWN, same reason; and it costs the marginal country its only story roughly every fifth read | — |
| volume weighting | higher than a floor | at a cost to the worldwide figure that makes it unusable |
| full depth over the window | at most 96% (49/51), plan against 65-70% | the 65-70% is the gap map's stated assumption that depth converts half the 26, and half is a guess |

**90% is not substantiable under ANY of these options.** Under the ranking
options it is arithmetically unavailable — a floor redistributes 37 places and
cannot manufacture reads for events whose candidates were never gated. Under
full depth it is inside the 41-96% band but nobody has measured what full depth
converts, and 90% of 51 is 46 events, which requires depth to convert 25 of the
26 ration losses AND to fix the one filter loss and the one plumbing loss it
does not touch. Anyone quoting 90% is quoting a hope.

### How to tell a real improvement from teaching to the test

This is the part that matters, because **any change here alters the population
the recall measurement is computed over.** The gold set is sealed and was
assembled without consulting our database, which protects the DENOMINATOR. It
does not protect against pointing the collector at the measured window.

Three specific ways the number could rise while coverage does not:

1. **Temporal.** Re-walking exactly 2026-06-01..2026-07-31 at full depth
   improves the one window the US set is drawn from and leaves the other ten
   months untouched. The figure would move for a reason that does not
   generalise a day beyond the window.
2. **Route.** The set is FUNDING ONLY and its citations lean on wire services.
   Raising US-edition priority raises exactly the route those publishers are
   indexed in, so the score can rise by aligning collection with the set's
   sampling frame rather than with the American market.
3. **Compositional.** A US floor changes what stores everywhere, so the
   worldwide family's number moves at the same time and in the opposite
   direction, and attributing either to the change is guesswork after the fact.

The protocol that separates them, and none of it is expensive:

- **Pre-register the delta before spending.** Say what US recall should reach
  and why, in a commit, before the walk runs. A prediction written afterwards
  is not evidence.
- **Hold out a window.** Walk the gold set's window AND a second, disjoint
  window of equal length at the same depth. Assemble the next US set from the
  held-out window under the existing `US_REQUIRED_SHAPE` protocol. A gain that
  appears in both is coverage; a gain only in the walked window is the test
  being taught.
- **Watch the control.** The worldwide family measures the same collector under
  the same budget. A US gain that arrives with a worldwide loss of similar size
  is redistribution, and must be reported as one. `family.py` already keeps
  these separate, which is exactly what makes this check available.
- **Report two denominators, not one.** Reads bought and rows stored per
  country, before and after. A policy that raises US recall while lowering
  rows-per-read is buying the metric, not coverage.
- **Free first.** The `--fetch-only` re-walk above settles which bucket the gold
  events are in for $0. If most of the 51 were never in the US bucket, the
  entire re-weighting question is moot and no money should be spent on it.

**If those cannot be run, the sentence to keep is this one:** a US recall figure
measured over a window the collector was just pointed at, with no held-out
window and no control, cannot be distinguished from teaching to the test, and
should be published with that caveat attached to the number rather than in a
method note underneath it.

### What this session did NOT do

No weight was changed, no cap was raised, no workflow was dispatched, nothing
was walked, nothing was written and nothing was deployed. The two files added
are a measurement and a test. The decision belongs to the owner and it is a
spend decision, not a ranking one.

---

## 2026-08-12 - the rows we hold and no reader can find: 5 of 51, and the cache nothing fills

**No deploy, no plugin change, no version bump. No model was called and no
money was spent: every number below is free, and the one paid pass this needs
is priced and NOT run.**

PR #15 found that 13 of the 21 US funding events we hold carry no country. This
places that finding, fixes the ingest path it keeps coming from, and says
plainly which rows cannot be filled at all.

### The number the owner asked about

Applying the plugin's own geographic clause — `country IN ('US') OR (country IS
NULL AND hq_country IN ('US'))` — to the 21 events the sealed US recall set
says we hold:

| | events |
|---|---:|
| a US-filtered reader sees | **5 of 51** |
| held, but carrying NO place in either column | 13 |
| held, but filed under another country | 3 |

The 13 are Mirendil, Rime, AlphaSense, Digital Asset, General Intuition,
Premier Lacrosse League, Gauntlet, Harmony, Artis, TerraFirma, Throne Science,
Databento and Buildforce. The 3 are Fish Audio (BR), Standard Bots (ZA) and
Allen Control Systems (ZA) — the publisher's country, which is what
`prompts.py` defines `country` to mean, so the model did as it was told.

Site-wide the same state is **1,666 current rows**, held by **1,633 employers**
— close to one row each, which is itself the finding: these are employers seen
once.

### Where the place went. Three parts, and none of them is a validation bug

**It is never extracted.** A free scan of `headline + summary` over all 1,666
— country names, US states, and the `X-based` frame the deterministic extractor
already reads — placed exactly **zero** of them. The stored columns are
exhausted. `raw_text` is not persisted, so the sentence that might have carried
a place is gone.

**887 of the 1,666 never met a model at all.** They carry
`cheap_extract.EVIDENCE_NOTE`: the free deterministic extractor closed them,
and it returns `headquarters_city` and `headquarters_country` as the empty
string by construction. That is correct — a regex cannot know where an employer
is seated — but it means 93% of everything that path stores (887 of 950 rows)
comes out with no place.

**The one free mechanism that CAN answer it was wired to a cache nothing
fills.** `validate.build_signal` calls `identity.enrich(signal, conn)`
cache-only, with the comment "the cache is filled by `python -m
pipeline.identity --backfill`". That command has never been run by any
workflow. There is no `identity` job in `.github/workflows/`. So **12,881 of
16,597 employer keys have no cache row**, and the ingestion lookup for a newly
seen employer is not usually a miss — it is a guaranteed miss, every time, for
ever. The row stores placeless and nothing ever comes back for it.

That is why the share is going UP and not down: 4.8% of July's rows are
placeless, and 8.8% of August's.

### The forward fix

`identity.place_if_unplaced`, called from `build_signal` immediately after the
cache-only `enrich`. A signal that would be stored with no country in EITHER
column buys ONE resolution with the network on. Everything else stays exactly
as it was.

- Free. SEC + Wikidata, no model, ever. `test_the_spine_is_never_allowed_to_ask_a_model`
  fails if a future edit reaches for the classifier to fill a headquarters from
  a company name.
- Bounded: `PLACEMENT_LOOKUP_BUDGET`, 150 per process, so an unusual day cannot
  turn a collect run into a crawl.
- Fail-open, like every other line in `identity.py`. A dead Wikidata is a blank
  column and never a lost record.
- `TIT_IDENTITY_LOOKUP=off` restores the previous behaviour exactly.
  `run_collect --offline` sets it, because a dry run that promises no network
  call must not make one, and `tests/conftest.py` sets it for the suite —
  five existing tests hand `build_signal` a real connection and any of them
  could otherwise reach the open internet.

RED before, on the real assertion:

    AssertionError: 'Mirendil Raises $200 Million Seed Round' stored with
    country=None and hq_country=None, so a reader filtering the site to the
    United States cannot see it

`tests/test_unplaced_rows_get_placed.py`, 11 tests, green after; full suite
3,694 passed, 1 skipped, 427 subtests.

### The guard that stops the cheap fix becoming the expensive mistake

Turning the identity spine on over this population was measured before it was
shipped, and the first reading was a warning rather than a result. Over 300
placeless employers it resolves 57 — and the sample includes the AI video
company **Synthesia** resolved to the Czech chemical works, **Fluidstack** to a
French namesake, **BKV Corporation** to a Hungarian political party and
**Capital Bancorp** to a Nigerian bank.

That is not random error and it does not average out. `_names_agree` already
throws away every hit whose name merely BEGINS with the employer's, so two
survivors are two organisations with the **same** name, and `_best_candidate`
then hands it to whichever one the encyclopedia has more articles about. For a
listed company that is right. For a seed-stage startup sharing a name with an
established firm it is wrong, systematically, in one direction, and
confidently. On the public page it would be indistinguishable from a right
answer — and this project has already relabelled three rows into the wrong
country once.

So `_best_candidate` now returns how many organisations were in the running,
`_identity_from_props` records it in `Identity.detail` (which is cached, so the
marker survives a cache hit), and **`place_if_unplaced` declines every
ambiguous name.** The general `--backfill` is unchanged and still takes the
best candidate; only the placement paths refuse. Precision over recall, the
rule `cheap_extract` already states: a blank country is honestly blank.

### The backward half, and its dry run

`python -m pipeline.identity --place-unplaced`, and `place-unplaced.yml` to run
it — queued through `drain-writers`, never dispatched. It fills `hq_city` and
`hq_country` only, only on rows carrying no place at all, only from
unambiguous resolutions, and then pushes them with `publish.enrich_published`:
both columns are already in `tit_enrichable_columns()`, so **no plugin change
and no deploy is needed for the values to reach a reader.**

DRY RUN, measured on a copy of the committed database, ordered by how many
placeless rows each employer holds, `--retry-negative` so cached negatives are
re-asked:

| | employers | share |
|---|---:|---:|
| would be placed | 82 | 5.0% |
| Wikidata does not know them | 1,412 | 86.5% |
| resolved, and declined at the bar | 139 | 8.5% |
| **all 1,633** | | |

Cost: **$0.00.** No model is on this path.

**One placeless employer in twenty, and that is the honest ceiling on free.**
Wikidata does not know seed-stage private companies; it was never going to.

### The bar moved once, and the reason is a row from the recall set

The first version of this pass declined only ambiguous names and would have
placed 190 employers. Checking it against the 13 US events a reader cannot see
is what stopped it: it resolves 3 of the 13, and one of the 3 is **Premier
Lacrosse League as Canada.** A US league, filed under Canada, on a public page.
Two right and one wrong is not a rate to ship.

The discriminator turned out to be cheap and sharp. `hq_country` is read from
P17 of the entity's HEADQUARTERS and falls back to P17 of the entity itself,
and the errors live in the fallback:

| | employers | what the sample reads like |
|---|---:|---|
| a curated headquarters CITY came with it | 82 | Accel/Palo Alto, Databricks/San Francisco, Cyera/Tel Aviv, DeepSeek/Hangzhou, AlphaSense/New York |
| a bare country, no city | 108 | Premier Lacrosse League/CA, Synthesia/CZ, AirTrunk/AU, African Bank/ZA |

So `is_placeable` requires the city, on both the ingest path and the backfill.
It costs 108 employers of recall and it is the right trade: "this employer sits
in this city, which is in this country" is a fact, and "this entity is
associated with this country" is a hint.

Of the 13, that leaves **AlphaSense (New York) and Databento (Boston)** placed,
and Premier Lacrosse League correctly left blank. The other 10 are names
Wikidata has never heard of.

### What CANNOT be filled, and what the paid pass would buy

Re-reading the source document is the only route left, and it was probed on the
16 US rows above, free and deterministic (dateline, then the `X-based` frame):

| outcome | n |
|---|---:|
| placed from the publisher's own text | 2 (Rime → San Francisco, Allen Control Systems → Austin) |
| fetched, and the page states no place | 5 |
| **robots.txt disallows the fetch** | **6** |
| HTTP 403 / 404 | 3 |

**Six of sixteen may not be fetched at all**, and that is a ceiling no budget
moves: we do not crawl what a publisher's robots.txt refuses and we do not
bypass a paywall. Those rows stay null, permanently, unless the same event is
found through another document.

The owner authorised about **$2.14** for a paid re-read (1,674 rows at
$0.00128). **It was not spent, and nothing about it is estimated as if it
had been.** Two reasons, both plain: this environment holds no
`OPENROUTER_API_KEY`, so a paid pass must run on Actions; and the fetchable
half of the sample suggests the document often does not state a place either,
which is a thing to measure on a priced sample before buying 1,666 of them. The
next session's first move should be a 100-row paid sample at about **$0.13**,
which prices the whole pass honestly instead of assuming it.

**The one thing that must NOT be done to close this gap is ask a model where a
company is headquartered from its name alone.** It would answer for all 1,666,
it would sound certain, and nobody could tell the wrong ones from the right
ones.

### The 3 filed under another country: a plugin change nobody may make from here

They carry `country` = BR/ZA, so the fallback clause never reaches
`hq_country` and filling HQ does not recover them. Recovering them means
`country_basis=any` in `includes/api.php` becoming a real union of job location
OR employer HQ — which is what the sibling layoff tracker's `any` already is —
and that is a plugin change, and a plugin change is a deploy, and the deploy is
the session's call and not a delegated one. **Stated, not made.**

### Reader-visible, before and after

**Before: 5 of 51. Now: 7 of 51**, verified on the live API and not projected
— AlphaSense and Databento both answer `hq_country: US` today.

**And the honest number is 6, because Databento is one of the 37.** Its US came
with no headquarters city behind it, so the reversal below takes it back, and
what free actually bought that survives its own bar is **one event**:
AlphaSense, New York, from a headquarters Wikidata records properly.

One of 51 is a small number and it is the true one. Writing 7 and quietly
keeping a value the same session decided was not good enough to keep would be
the more comfortable sentence and the less honest one.

The remaining 14 break down honestly. 10 are employers Wikidata has never heard
of and whose own coverage states no place, so only a paid re-read of the source
can reach them, and 6 of the 16 sources may not be fetched at all. 3 are filed
under the publisher's country and need the plugin change above. 1 (Premier
Lacrosse League) is deliberately left blank rather than filed under Canada.

`measure_recall.py --family us` re-run after the pass is what settles it, and
it costs nothing.

### APPLIED, and what the run actually did

Queued through `drain-writers`, twice, and it took two more defects with it.

| | |
|---|---:|
| placeless rows before | 1,666 |
| placeless rows after | **1,573** |
| rows placed | **93** |
| placeless employers | 1,633 → 1,545 |
| rows carried to the live site by `/enrich` | 71 on the first pass |
| **rows that must be taken back** | **37, and the site cannot yet accept it** |
| **money spent** | **$0.00** |

The two defects, both found by running it rather than by reading it:

* **A three hour job with its commit at the end.** Resolution is a serial walk
  at about 7 seconds an employer, so `--limit 1633` is over three hours against
  a two hour timeout, and every resolution would have been thrown away. Both
  write steps are `always()` now and the lock ceiling is 90 minutes. The pass
  is meant to be run repeatedly with a limit.
* **The worklist did not shrink.** The first live run walked the 400 employers
  holding the most placeless rows, found 393 of them already cached by the
  general backfill, finished in thirty seconds and placed two. Every later run
  with the same limit would have walked the same 400.
  `_employers_needing_identity` had written that lesson down one function up —
  ask who has no cache row, because that is a question whose answer shrinks —
  and the placement worklist was written without it.

**1,545 employers are still placeless and the free route is finished with
them.** Wikidata does not know them, and their own coverage does not say where
they are.

### AND ONE THING WENT WRONG, and it is this session's own doing

The FIRST live run of the pass used the bar before it was tightened, the one
that declined only ambiguous names. It was cancelled a few minutes in, on
purpose, because checking it against the US recall set is what turned up
Premier Lacrosse League as Canada. **The cancellation was correct and it was
late.** A cancelled job still runs its current step: the commit had already
happened, and the `/enrich` in the next run then carried the values to the
live site.

**37 rows carry an `hq_country` with no headquarters city behind it, and they
are on the public page now.** Some are right (Beretta IT, CyrusOne US). Some
are not, and one of them is exactly the failure this whole entry is about:

    Synthesia   CZ   the Czech chemical works. The live row is the UK
                     company's GBP 146m Series E led by GV. Twice.
    Ash Games   DE   a German namesake
    CFS         CA   the same employer that also appears as
                     Commonwealth Fusion Systems, US

Nothing new can join that list — `is_placeable` refuses the whole class — and
the correction is written, in `reverse_cityless_hq.py` with
`reverse-cityless-hq.yml` behind it, listing all 37 by content_hash in
`data/cityless_hq_to_reverse.json`.

**It REFUSES to run, and that refusal is the honest state.**
`tit_clearable_columns()` returns `funding_amount_usd` and `funding_stage`
only, so `/enrich` cannot blank `hq_country`: an absent or empty field means
"we still do not know", deliberately, so a gap can never erase a known value.
There is no other door. A corrected database in front of an uncorrected page
is the divergence `correct_city_country.py` already refuses to create, so this
refuses too, exits 2, and prints what has to change:

    1. tit_clearable_columns() must return 'hq_city' and 'hq_country'.
    2. Bump Version: and TIT_VERSION, deploy, verify the page.
    3. Queue reverse-cityless-hq.yml with dry_run=false.

`test_the_refusal_is_still_correct` goes RED the moment somebody widens that
allowlist, which is exactly when the pass becomes runnable.

**The lesson, and it is not the one it looks like.** The bar was measured
before it shipped and the measurement caught the defect; what failed was
running the pass while the measurement was still being read. A cancelled
writer is not an unwritten one.

---

## 2026-08-12 - the allowance goes to $18, and the measurement says that is not enough

The owner raised this tracker's OpenRouter key to a **$30/month provider limit**
and found it bought nothing. It could not: the **policy** cap in `spend.py` was
$10 and it is the one that binds, so the provider headroom above $10 was
unreachable. `MONTHLY_ALLOWANCE_USD` is now **18.0**.

**Why $18 and not $20.** Two ceilings exist and only one can be the one that
fires. The provider cap is a HARD stop - the next paid call fails, wherever the
run happens to be. The policy cap is a GRACEFUL stop - `--degrade` switches paid
reads off, every free collector keeps running, each deferred candidate is left
unread and UNMARKED for a later run, and the writer-queue ticket is filed as
DEFERRED rather than failed. **At parity the graceful stop can never fire**, and
a disclosed degradation becomes a failed call mid-batch. The $2 gap is the whole
point of the number. If the provider limit moves, move this to stay under it;
do not match it. `test_the_allowance_is_the_number_the_owner_set` now asserts
`< 20.0` with that reason, so "use the whole key" goes red.

### The measurement: what August actually bought

There is **no per-job spend ledger in this repo** (no equivalent of the layoff
tracker's `railway/spend_jobs.json`), so none of this is estimated from one -
it is read back out of what the run logs already print. Two signals: every run
prints `spent on this key $X (lifetime)`, and `run_collect.py` prints
`$X.XXXX this run` from `classify.STATS['usd']`.

**The month did not spend $10.08 at a rate. It spent it in 2.5 days and then
stopped dead.**

| reading | lifetime | month-to-date |
|---|---:|---:|
| `data/spend_month.json` month start (08-01) | $16.8634 | $0.00 |
| 08-01 06:59 collect | | $1.1411 |
| 08-01 18:26 collect | | $2.6229 |
| 08-02 07:01 collect | | $5.0477 |
| 08-02 18:26 collect | | $7.3275 |
| 08-03 12:04 collect-press | $26.8070 | $9.9436 |
| 08-10 19:06 → 08-12 11:40 (9 readings) | **$26.9480, unchanged** | $10.0846 |

Lifetime usage has not moved since 08-03. The guard tripped on day 3 and paid
reads have been off for the nine days since. **Dividing $10.08 by 12 days is an
average over nine days that bought nothing**, which is why $0.84/day looked
survivable and $1.87/day looked alarming and neither is a rate.

**One-off vs recurring.** August's paid runs were overwhelmingly hand-dispatched
backfill walkers: **33 of 39 paid runs on 08-01, 36 of 41 on 08-02**, dozens of
`backfill-gnews-2026` through 08-03, and 19 `backfill-press-2026` on 08-05. All
six `backfill-*` workflows are `workflow_dispatch:` only and their headers
forbid a `schedule:`, so they are one-off by construction.

| | August | share |
|---|---:|---:|
| ONE-OFF (backfill walkers, tripwire, ab-models, benchmark-diff) | ~$8.87 | **88%** |
| RECURRING (collect + collect-press) | ~$1.21 | 12% |

### The recurring figure, and why $18 does not hold

Measured per-run, from the six scheduled runs that completed on 08-01/08-02 -
the only two days this month with the budget open (the ~09:35 press run on each
day was **cancelled**, so it is excluded, not counted as a zero):

| workflow | runs | mean |
|---|---:|---:|
| `collect` | 4 | $0.1379 |
| `collect national press` | 2 | $0.1642 |

At the real cadence (`collect` 2x/day, `collect-press` 2x/day):
**$0.6042/day = ~$18.1/month, recurring, before the tripwire or any backfill.**

**This is not a busy-week artifact, and it was checked for one.** The sibling
tracker's precedent is a measured "$0.43/day, ~$13/month" that turned out to
straddle a model swap. The same check here points the other way:

* **July was 3-5x dearer** ($0.7969, $0.6046, $0.5610 per run on 07-30), so the
  read-cap reallocation that landed 07-31/08-01 is already in these numbers.
  The August figure is the POST-optimisation one.
* **The cap is reached on every run**, so this is structural, not demand. Run
  30737018299: `[google_news] gate: 208 screened, 69 dropped cheap, 99 full
  read-throughs (cap 99/run)` for `$0.1815 this run`. Demand exceeds the ration
  every time, so the cost is the ration.
* **The window understates it if anything.** These two days sat inside the
  backfill campaign, whose stored rows the collectors then skip for free.

At $18 the 90% stop line is $16.20, so recurring alone crosses it around day 27
and exhausts the allowance at month end. **$18 buys a full month of the
scheduled collectors and nothing else** - no tripwire (~$1.00/month), no
backfill walkers (~$3.00/month declared across the three), no catch-up.

**This conversation repeats in September**, and the answer then is not a bigger
number: it is `docs/PLAN-gate-to-five-dollars.md`, or a smaller
`BINDING_READ_BUDGET`, or a slower cadence. Raising the policy cap again would
put it at or above the $20 provider cap, which is the one thing this entry says
not to do.

**A note on the deferral everyone is reading.** The
`the monthly spend allowance was already exhausted ($10.08 of $10)` ticket is
dated **2026-08-06T09:17:09Z**, not today. A session seeing it on 08-12 is
seeing `ops_status.py` re-display a six-day-old writer-queue ticket, correctly.

Tests: RED before on three real assertions (`assert 18.0 == 10.0` at
`test_budget_stop_is_not_a_failure.py:422` and `test_forward_first.py:264`,
plus `test_spend_degrades.test_the_allowance_is_the_number_the_owner_set`),
green after. Full suite **3642 passed**. No workflow was run and no model was
called: every figure above comes from run logs and committed files.
## 2026-08-12 - the 30 US misses, placed. It is the budget, and it was never the sources

**No deploy. No plugin change.** This is measurement and it moves no version.

`measure_recall.py --family us` says 21 of 51. It does not say what the other
30 need, and a percentage is not a work list. This places every one of them at
the stage it was lost, prices each stage, and answers the question the owner
actually asked, which was what it would take to reach 90%.

**The answer is that 26 of the 30 are the read ration, 0 are a source we do not
have, and 90% is not a number this project can substantiate at this budget. A
defensible target is the one a full-depth re-walk of one 61-day window
measures, and that walk costs $5.35.**

### What each of the 30 was lost to

| stage | n | what closes it |
|---|---|---|
| stored, not matched | 0 | nothing — we hold none of them |
| fetched, then rejected | 1 | a filter, and nobody can say which |
| read the feed, never took the item | 1 | plumbing |
| walked, never read | 26 | depth, which is money |
| never walked | 2 | dispatch the remaining slices |
| no source at all | 0 | — |

`analysis/recall/rejection_audit.py` already asked this question of the
worldwide set and had been surfaced in `ops_status [3c]` since 2026-07-29. It
now takes `--family`, writes one file per family
(`data/recall_us_rejection_audit.json`), breaks the misses out over the
family's own spread dimension rather than `by_country` (which for a US set is
one row saying US), and — the substantive change — **reads the historical
walkers' cursors**.

### The bucket that was wrong, in both families

Until today the audit knew only the LIVE routes: a route's reach was the day it
first ran minus the window it asks for. That was the whole story on 2026-07-28,
when it produced the finding this repo built `press_archive` and three walkers
on the back of: 51 of 81 misses `outside_our_history`, and 0 fetched and
dropped. It stopped being the whole story on 2026-07-30, when the walkers
started.

A walker's cursor is a fact on disk, in `data/backfill_state.json`. Reading it
moves the US set from **28 `outside_our_history`** to **2**, and the worldwide
set from **87** to **9**, with the difference landing in a new bucket,
`walked_never_read`.

**That is not arithmetic, it is a different bill.** A date no route has reached
is closed by dispatching slices. A date a walker has FINISHED is not:
`backfill_gnews_2026` gates `DAILY_GATE_RATION` candidates of a measured ~395 a
day, prints the rest as `left_for_later`, and advances `done_through` anyway —
"a window that spent its whole ration is FINISHED" is its own comment. That job
records **138,978 left for later** over 2026-01-01 to 2026-07-12. So more
slices walk straight past the same events, and only depth reaches them. Sending
the owner to dispatch slices would have been the wrong week's work, recommended
with confidence, off a number that was correct when it was written.

Three rules hold the cursor reading up, each with a test in
`tests/test_rejection_audit_walkers.py`, each proved red first:

- A `days` walker's cursor is the NEXT day to walk, so it is finished through
  the day before. Reading it as the last day done credits a day it has not
  started.
- `backfill_press_2026` walks the publisher ROSTER and takes the date range as
  a fixed input, because a sitemap costs the same fetch for one day as for six
  months. It counts only once the roster pass is `done`, and only for a
  publisher the catalogue knows, since its roster IS the catalogue. Google News
  and GDELT are searches and carry no such restriction — which is exactly what
  moves two of the US misses back into `outside_our_history`.
- A missing, broken or unrecognised state file credits nothing. Absence of a
  walker record is not evidence that a day was walked.

And the limit is written into the file it produces: a cursor says the day was
swept at the ration's depth, not that the query set would have surfaced this
event. It names the stage and never the outcome.

### The category that is empty, and it is the interesting one

**Not one of the 30 is a source we do not have.** Every one was published by
somebody Google News indexes, and Google News is the route that produced 14 of
the 21 we DO hold. The citation domain in the gold set is a red herring for
this: 11 of the 21 holds cite a publisher the catalogue has never heard of,
because we came by the event through a different document. Wiring PR Newswire,
which is 7 of the 30 citations and sits in the catalogue with no feed, is a
cheap and sensible thing to do and it is not what is losing these events.

### The one filter finding, and the limit that was half solved on the way

One event, TYBR Health on 2026-07-28, is in `seen_urls` with outcome
`rejected` — google_news resolved that exact URL on 2026-07-30 and let it go.
**Nothing records why**, and that is this module's oldest stated limit: the
prefilter, the gate model, `validate.precheck`, `validate.build_signal` and
both dedupe layers all write the same word.

It turns out to be half solved already and nobody had joined it up.
`pipeline/gate_ledger.py` has recorded a per-candidate outcome since
2026-08-01 (`gate_reject`, `model_reject`, `validate_reject`, `deferred`,
`duplicate`, `stored`, `error`) and carries a `reason` string where the
refusing code passes one, and its `key()` is a **sha1 of the same URL
`seen_urls` deduplicates on**. So the join needs nothing new on either side and
is now made: a `fetched_then_dropped` item gains `dropped_at` and, where there
is one, `dropped_because`.

It does not rescue TYBR. The ledger began two days after that rejection, and
the only line under that key comes from `bootstrap_gate_labels.py`, which
back-filled the ledger FROM `seen_urls` to give the classifier a weak training
set — so it says `rejected` and nothing more. **A ledger line that only echoes
the `seen_urls` verdict is deliberately not reported**, because
`dropped_at: rejected` would dress the limit up as an answer, and that is
exactly how the one bucket that means "loosen something" stops being read.
Tested. From here on the attribution is real.

Worth a look on the way past: 4,850 of 11,824 lines in the August shard carry
outcome `error`, which is 41% and is not a coverage question but is not nothing
either.

### The missing-country defect does NOT understate the measurement

PR #15 found 13 of the 21 held events carrying no country and called it the
most actionable thing in that PR. It is real. It does **not** move the 41.2%,
and the temptation to hope it did is why this was checked two ways:

- `measure_recall.rows_for` calls `/query?company=<term>&per_page=200`. There
  is no country parameter, and `tit_build_where` adds no country clause unless
  one is passed.
- A second, independent lens over the committed corpus — direct SQL, no
  geographic predicate of any kind — reproduces the published split exactly:
  **30 MISSED, 17 FOUND_PARTIAL, 4 FOUND**. Had country-blindness been hiding
  events, that lens would have found them.

So 41.2% is what we hold. **9.8% is what a reader sees.** Applying the
plugin's own clause, `country IN ('US') OR (country IS NULL AND hq_country IN
('US'))`, to the 21 rows we hold: 5 visible, 13 invisible because they carry no
place at all, 3 filed under another country. That gap is the finding, and it is
larger than any coverage number in either PR.

The 3 misfiled are one nameable bug and it is not a model error. `prompts.py`
defines `country` as the country IN THE TEXT, and says in as many words that
the publisher's own country counts. So a US round written up by ventureburn.com
is stored `ZA`, and one by Startups.com.br is `BR`; the model did what it was
told. The employer's country lives in `headquarters_country`, which is blank on
81% of news rows. Two consequences worth writing down before anybody "fixes"
either:

- `country_basis=any` in `includes/api.php` is a FALLBACK, not a union: HQ is
  consulted only when `country` is NULL. The sibling layoff tracker's `any`
  is a real union of job location OR employer HQ. Making this one match would
  recover the misfiled rows — but only once `hq_country` is filled, and today
  it is not.
- There is **no free way to fill it** for this population, and that was
  measured rather than assumed. `pipeline/identity.py` is free, deterministic
  and keyless, and resolving the 16 defective employers through it returns
  hq_country for **2**, one of which (Premier Lacrosse League as CA) is wrong.
  Wikidata does not know seed-stage private US companies. Joining to Form D by
  name is worse: 4 of 16 match and two of those four are collisions (Harmony to
  Harmony Biosciences, Throne Science to Science Corp), which is exactly the
  failure `identity.py`'s own docstring warns about. The place has to come out
  of the article text, which means the read, which is paid.

### What it costs, in the walkers' own measured prices

Read off `--plan-cost`, not from memory:

| lever | money | work |
|---|---|---|
| finish the stalled Google News walk to 2026-07-26 | ~$0.13 | dispatch 4 slices |
| re-walk 2026-06-01..2026-07-31 at FULL depth, every edition | **$5.35** (61 days at $0.0877) | one dispatch, plus an edition filter if it is to be US-only |
| a full-depth press-archive pass over the window | ~$1.56 | the window is free on this route; only the ration bills |
| attribute a rejection (done here, from 2026-08-01 forward) | $0 | joined `gate_ledger` to the audit |
| wire PR Newswire into the catalogue | $0 to add | the reads it generates are not $0 |
| fill the place on 1,674 country-less news rows | ~$2.14 at $0.00128 a read | a re-read pass, one-off |

For scale, from the same tool: a full-breadth Google News sweep of a whole year
is $32.09, of which the gate alone is $4.34.

### The ceiling, stated before anybody plans against 90%

**US recall is low partly BY DESIGN, and the design is defensible.**
`pipeline/candidate_rank.py` spends the read budget by country need:
`W_COUNTRY_EMPTY = 6.0`, `W_COUNTRY_THIN = 3.0`, and a country over
`COUNTRY_THIN_ROWS = 25` scores zero. The US holds thousands of rows. The most
a US candidate can score on everything else is 4.5. **So no US candidate can
ever outrank a candidate from a country that holds nothing**, and under a
ration that is the same as saying the US is read last. That is the right call
for a worldwide product and it is the direct cause of this number.

Which makes the honest position:

- **90% is not substantiable.** Not because the events are unreachable — none
  of them is — but because sustaining it means reversing the country-need
  ranking that the worldwide figure depends on, or buying full depth for one
  country every day forever, and nobody has measured what full depth yields.
- **The bound nobody can narrow from here is 41.2% to 96%.** 29 of the 30 were
  reachable, so depth could in principle close 28 of them (49 of 51); and the
  floor is what we hold today. That band is 55 points wide and it is honest.
  Any single number named today is a hope with a decimal point.
- **If a number is needed to plan against, plan against 65 to 70%,** and read
  it as the assumption it is: full depth removes the stage 26 of the 30 died
  at, and if it converts HALF of them that is 34 of 51, 66.7%. Half is a
  guess. The two non-ration losses (one filter, one plumbing) are 7% of the
  misses and depth does not touch either.
- **What CAN be defended is the next measurement.** Re-walk the window at full
  depth for $5.35, re-run `measure_recall.py --family us` against the same
  sealed set, and publish whatever it says. That number will be earned, and it
  costs half of one month's allowance to find out.
- The one thing worth doing first is not a coverage fix at all. Getting the
  reader-visible figure from 5 of 51 up to the 21 of 51 we already hold buys
  more than any walk, and it needs no new event.

---

## 2026-08-12 - how good are we in America, with a number and a range (1.76.0)

**Merged, NOT deployed** — the deploy here is a human step and a subagent does
not take it (CLAUDE.md).

The sibling layoff tracker can say exactly how good it is: 24 of 57 held-out
SEC Item 2.05 filings, with an interval, on its health page, behind a floor that
can go red. This tracker could not. The worldwide set has a US cell, it reads
38%, and it is 34 events of a set assembled to be global, so it is an impression
wearing a percentage.

**Result: held 21 of 51, 41.2%, 95% interval 28.8 to 54.8**, against a US
funding set for 2026-06-01 to 2026-07-31, assembled without consulting our own
data. Per metro: Austin 5/8, New York 8/16, rest of US 5/14, San Francisco 3/13.
Only 4 of the 21 are clean, and the dominant defect is `country_missing` on 13
of them, which is an extractor problem rather than a collection one.

### The number that matters is the range, and it was never published

`wilson()` had been in `thresholds.py` since 2026-07-30, used to derive a floor
and shown to nobody. On 51 events 41.2% is also 28.8% to 54.8%, and on a metro
cell of eight it is 30.6% to 86.3%. Publishing the point estimate alone invites
exactly the comparison the counts cannot support. It moved to
`analysis/recall/stats.py` as a leaf, `thresholds.wilson` re-exports it so the
floor and the page cannot round one interval two ways, and every cell in every
family now carries `held_interval`.

### Two populations, two directories, and why that is load-bearing

`analysis/recall/family.py` is the one definition of what is measured and where
each population's gold sets, results, page data and health entry live.
`measure_recall.py --family`, `ops_status.py [3e]`, `health_digest.py` and
`includes/recall.php` all read it.

The separate directory is not tidiness. `goldset.latest_path()` takes the newest
`goldset-*.json` in a directory, and `goldset-us-2026-06.json` sorts after every
worldwide set that will ever exist. One file in the wrong folder would have made
the published WORLDWIDE figure a US figure, with no code change and nothing in
any diff to notice. Asserted by
`test_a_us_set_cannot_hijack_the_worldwide_measurement`.

### Four passes independently walked into our own feed

The set was assembled by eight isolated research passes, one per metro and
signal type, each forbidden from consulting this tracker. Every one of the four
LEADERSHIP passes reached, unprompted, for SEC EDGAR full-text search, and for a
good reason: it is the only free, chronologically enumerable index of US
corporate events that is not a commercial database, and commercial databases are
discovery pointers we may never cite. It is also precisely what
`collectors/sec_edgar.py` walks. All four came back over 90% exchange-listed
filings. Measuring against them would have scored the tracker against its own
supply and produced a flattering number that meant nothing.

All four were discarded. The guard that makes that mechanical is
`US_REQUIRED_SHAPE["max_source_type_share"] = 0.50`: no single kind of document
may be a majority of the denominator. It is the sharp instrument here because
`size_band` is not one, being "500+ employees OR listed", which bands a
twelve-person listed biotech as large.

**A wire-dateline walk did work.** Searching a press-release service for the
literal dateline string (`DENVER, June`, `SALT LAKE CITY, July`) returns exactly
the releases datelined in that city that month, and walks in date order. Three
re-run passes produced 34 verified private-employer leadership rows with no
EDGAR at all. They are NOT in the sealed set: San Francisco and New York ran out
of search budget before their passes could run, so two of four metro cells would
be empty, and 30 of the 34 are press releases, which would put one document type
at 60%. They are parked at
`analysis/recall/us/goldset-us-2026-06-leadership.draft.json`, which
`all_paths()` skips by name, with the enumerator written down inside it.

### So the US set covers funding only, and says so where the number is

That is a real limitation and the page carries it in a callout beside the
figure, not in a method note further down. `signal_types` is declared in the file
and enforced against the items, so the scope cannot quietly widen into a
half-measured second signal type while the headline looks like the same number.

### Bars, and where each came from

`min_items: 45` and `max_interval_width: 0.28` are anchored to the sibling's
published benchmark, which resolves to about 25 points. A second number in the
same house that resolved much worse than the first would not be readable the
same way. The assembled set came in at 51 events and 26.5 points.

A higher small-employer floor was drafted and withdrawn. The passes showed the
small share is capped by publisher behaviour rather than by effort: the single
largest cause of a verified event being dropped was that no fetchable page
stated the company's headquarters, coverage of small rounds has largely stopped
writing "San Francisco-based", and a row with no verifiable metro cannot enter a
set whose cells are metros. A bar nothing honest can clear is a bar that gets
edited on the day it fires, so the floor stayed at the worldwide 30% and the
under-representation is declared in the set's own caveats instead.

### What is not done

The San Francisco and New York leadership passes, which need search budget.
Until they land, US leadership coverage is unmeasured and the page says so.

---

## 2026-08-12 - the controls had no edges, and the panel was shut (1.75.0)

Three things the owner asked for, in one pass over the filter bar. **Merged,
NOT deployed** — the deploy here is a human step and a subagent does not take
it (CLAUDE.md).

### 1. A control's boundary is not its text, and only one of them was measured

The owner said the filter controls "get lost". Every contrast check in this
repository was green while he was saying it, because they all ask "can this
text be read" and none of them asks "can this be seen as a control". Measured
in a real browser against the resolved cascade:

| control | before | after |
|---|---|---|
| Reset All | **1.00:1** (no border, no fill) | 4.58 light / 6.40 dark |
| every bordered control | 1.28 light / 1.56 dark | 4.58 light / 6.95 dark |
| worst on the bar, light | **1.00:1** | **4.48:1** |
| worst on the bar, dark | **1.00:1** | **6.40:1** |
| worst, auto on a dark OS | **1.00:1** | **6.40:1** |

**The bar is max(border vs outside, border vs fill), falling back to fill vs
outside where no border paints.** Crossing a control's edge a reader meets at
most three colours. A border matching the fill still shows against the page and
one matching the page still shows against the fill, so scoring against a single
neighbour would fail correct designs; scoring the fill alone would pass Reset
All, which is the case that actually shipped.

**The cause was `--tit-line`, used as a control edge.** It is a hairline for
dividing rows and it measures 1.28:1 / 1.56:1 as a boundary. The fix reuses the
two values the theme control was already given at 1.74.2 for this exact reason
(`#6f7681` / `#98a1b0`, both already proven to clear 3:1 against the ground and
against a white fill) as a new `--tit-ctl-line`. **No token was moved** — a new
one was added, and the tokens that were correct stayed correct.

Alongside it, one standard where there had been an accumulation: the bar
carried **four type sizes** (12.5, 13, 14, 14.5), **three heights** (23, 27-29,
37-39) and two radii across controls doing the same job. Now one height, one
radius, one type size, one edge, and width TOKENS rather than five hand-picked
pixel values. The lead controls keep a heavier weight, because weight
distinguishes them without adding a fourth size.

**The label stays BESIDE its control on the desktop bar.** The note above that
rule records why (stacked, the sticky bar was 280px tall on a 1280px viewport)
and that reasoning still holds. On a phone the bar is `position:static`, so it
costs nobody a pinned viewport, and there the label goes back above — see below.

**Two claims in the handover were wrong and are recorded here so nobody
re-derives them.** (a) "Six selects render with the browser default border
`rgb(118,118,118)`" — no control on the page renders with a UA border; those
six ids are hidden querystring mirrors that render nothing, and every visible
select already carried a styled 1px border. The defect was that the styled
border was too weak, which is a different fix. (b) "Search needs a real
`<label>`" — it already has one; the input sits inside
`<label class="tit-field tit-field--stack">` with a visible span. The theme
buttons at 27px were right (measured 26.9px).

### 2. The panel ships open

> "i like when both filters are just showing so it's obvious and not hidden on
> both trackers."

It was `:not(.is-open)` below 900px, so the bar shipped shut and a reader had
to know a panel was there before they could find out what it filtered. It is
`.is-collapsed` now: the served markup renders open, which is also exactly what
a reader with no JavaScript already got, so the two agree for the first time.
`aria-expanded` ships `true` to match, and `render_dashboard.php` asserts it.

- **A collapse is "not right now", not a preference**, so it is remembered in
  `sessionStorage` and not `localStorage`.
- **A deep-linked filtered view forces it open** and beats the remembered
  collapse. Someone opening a shared link lands on a page that is already
  narrowed, and the controls are the only thing explaining why the numbers are
  not the front page's.

**What it costs, measured rather than assumed.** At 1280px, nothing: the bar
was already open there. At 375px the first data row moves **348.8px**, 43% of
an 812px viewport. That is LESS than the 360.6px the old panel cost when a
reader opened it by hand, and it now buys 44px targets instead of 27-29px ones,
because the phone layout changed to pay for it: labels stack above their
controls so two cells fit per row, and the panel went 545px -> 413.8px.

### 3. The phone

Driven with real clicks at 375px, not read out of the stylesheet.

- **Usable content width 347px of 375, 92.5%.** `scrollWidth === clientWidth`
  the whole time, which on its own proves nothing — the sibling once rendered
  in 219px of a 375px phone while that equality held perfectly.
- **Tap targets under 44x44: 345 -> 226.** Every filter, the quick views, the
  view toggle, the region strip, the country and city chips, the watchlist
  stars, the jump bar, the theme buttons and both sort selects now clear 44px.
  The 226 that remain are **93 inline text links** (WCAG 2.5.8 exempts links
  inside a block of text), 58 ranking rows, 31 per-chart controls and 24 matrix
  cells. Those are dense data affordances; raising them is a layout decision
  about the charts, not a control-standard one, and it is left named rather
  than silently done.
- **The two sort selects above the updates were filters living outside the
  panel**, at 34 and 35px and on the old hairline. A control being somewhere
  else on the page is not a reason for it to be a different size.
- **Dropdown popovers ran off the bottom of the screen.** Team Or Function
  opened **590px** tall and Industry **624px** on an 812px phone, because
  `.tit-dd-panel > .tit-optbox` lifts the option list's max-height — correct on
  a desktop, and the options past the fold were unreachable since the page
  behind had already scrolled to the trigger. A viewport-unit cap cannot fix it
  alone: CSS does not know where the trigger is. So `openDrop_()` now measures
  the room above and below **once at open**, exactly as `is-flipped` already
  measured horizontally, writes `max-height` inline and adds `is-up` for a
  trigger low on the page. All four popovers now fit on both axes. Horizontal
  fit and clipping were never broken.
- **Nothing is hidden behind sticky chrome.** The one fixed element is the
  phone jump bar (39px), and `#tit-dashboard:has(.tit-jump)` already reserves
  74px for it.
- **The search field stays visible with a keyboard open** (emulated at
  375x475): it sits at top=215, bottom=259.

### The guard

`tests/test_control_boundaries.py`, four tests, in the `pytest` job where php
and Chrome both already exist. It renders the **real** shortcode through
`tests/php/render_dashboard.php` (that harness's `TIT_DUMP_HTML` hook exists
for this) rather than a hand-copied fixture that can drift, then measures the
resolved cascade in headless Chrome in all three theme states. No php or no
Chrome **skips loudly**; absence of a signal is not a pass.

Proved red against the pre-fix tree, all four:

```
1 not greater than or equal to 3.0 : light at 1280px: the control 'tit-reset'
  has a boundary of 1.00:1 (no border painted, so fill vs outside) ...
4 not less than or equal to 2 : the filter controls use 4 type sizes
  (12.5px, 13px, 14.5px, 14px) ...
False is not true : the filter panel body is not rendered at 375px, so a
  reader has to know the filters are there before they can find out what
  they filter
Lists differ: [{'name': 'tit-bar-toggle', ...}] != []   (under 44x44)
```

**A note on asserting this kind of thing.** `innerText` is not a safe reader-
visible test on the element you are hiding: for a **non-rendered** subtree it
falls back to `textContent`, so the collapsed panel's own `innerText` returned
5,239 characters. The honest reading is `innerText` off the **rendered**
ancestor, which excludes non-rendered descendants — 42 characters collapsed
against 377 open. The test does it that way.

**Byte budget: 184,578 of 184,600, and it went DOWN by 1.** Nothing was raised.
Almost all of this is CSS, which the budget does not count; the only markup
change was `aria-expanded="false"` -> `"true"`, which is a byte shorter.

## 2026-08-11 - the rename reached the chart and not its twin (1.74.6)

The place chart was retitled "Updates by Country" on 2026-08-05 because "Where
the Jobs Are" over a ranking of record counts is a wrong number written in
words. The filter ribbon sitting directly ABOVE that chart, over the same
numbers, kept the captions "Top Countries" and "Top Cities", with flags and a
descending sort. So the page said both things at once, and the owner read the
ribbon the way a list called "Top Countries" asks to be read: he asked why the
United Kingdom outranks the United States.

**It does not.** The UK leads (7,982 against 7,493 US and 6,439 India) because
Companies House publishes structured filings for very nearly every UK company
and we ingest them wholesale, while the US equivalent reaches public companies
only. London 2,268 against New York 478 is the same fact about cities. The
ordering is a picture of our collection method. Nothing about it is wrong, and
**no number, threshold, sort or filter semantic was touched here** - only the
words around them.

**Fix.** Both captions now name their unit in the chart's own vocabulary:
"Top Countries" -> "Countries by Updates Held", "Top Cities" -> "Cities by
Updates Held". Above both rows, visible, a basis line that states the cause
rather than only hedging: "These counts are updates we hold, not a ranking of
the market. Some countries publish a company registry we can read in full. We
hold many more updates per employer there than in countries where we rely on
news and filings."

**Visible prose, not a disclosure, and above the rows.** Both placements are
inherited arguments this page has already paid for: every `.tit-chart-note` is
closed by dashboard.js on load, which is how three caveats here computed
display:none and were read by nobody, and a correction printed under a
descending list arrives after the misreading, because the surprising country is
by definition near the top of it. Measured rendered rather than read as markup:
223 characters, 821x90 at 1280 and 309x179 at 375, contrast 8.03:1 in light and
15.03:1 in dark, `scrollWidth === clientWidth` at 375.

The caption also moved to its own line (`flex:1 0 100%`). It was a non-shrinking
flex item beside the chips, which was safe while it read "TOP COUNTRIES" and is
an overflow hazard once it names a unit: a `flex:none` item cannot give width
back.

**Guard: `tests/test_place_ribbon_names_its_unit.py`, 8 assertions, 5 of them
proven red against the pre-fix tree.** The other three (no jobs/people claim, no
dash punctuation, two captions present) held before and after and are pinned
against a future edit, not against this one. Comments are stripped before
anything is matched, and that is load-bearing here rather than tidy: the commit
that fixes this adds a note above the ribbon which quotes "Top Countries"
verbatim to explain the defect, so a checker that read comments would pass
against the broken tree and fail against the fixed one. Two of the five first
failed with a bare `ValueError: substring not found`, which names nothing a
reader of CI can act on; they asserted the paragraph's presence first after that.

Byte budget: the paragraph, wrapped the pretty way at that indentation depth,
put the page 79 bytes over TIT_DASH_BYTE_BUDGET. The budget was NOT raised. The
copy is printed on one source line and two aria-labels that had grown were put
back to what they were, which is 184,579 against a 184,600 ceiling.

### The theme control is not broken, and the thing near it that is

Reported this session as a live defect: only "Auto" reaches the page, "Light"
and "Dark" absent, suspected collateral from the 1.74.5 contrast work. **It is
not a defect.** The measurement behind the report read the SERVED HTML, and
nothing server-side has ever printed those words: `assets/dashboard.js` builds
all three buttons. Driven in a browser against the live page at 1.74.5, all
three render (69x27, 66x27, 67x27), `aria-pressed` tracks the choice, Light and
Dark both set `data-theme` and persist to localStorage, and Auto removes the
attribute. The three "Auto" hits in that HTML are the substring inside
"Automotive" in the industry dropdown. No file was changed on that account.

**What IS wrong, found while checking it:** `tit_theme_head()` prints the
before-first-paint stamp as a plain inline script, and it reaches a reader as
`<script defer src="data:text/javascript;base64,...">`. Decoded, the payload is
byte-for-byte ours. Something between the function and the browser rewrites
inline scripts into deferred external ones, which is sensible for nearly every
script on a page and precisely wrong for the one whose entire purpose is to run
BEFORE first paint. Deferred, it lands at the end of parsing (domInteractive
1317ms, DOMContentLoaded 2341ms on that load) and the served markup carries no
`data-theme` of its own, so a reader who chose Light on a dark-scheme device
gets a dark page first and a flip afterwards, every load. The tag now carries
`data-noptimize="1"` and `data-cfasync="false"`, the two standard opt-outs.
**This one is not yet proven fixed:** which layer does the rewriting was not
identified, and the paint-timing API was unavailable in the browser used, so the
flash is argued from the `defer` attribute plus the absent server-side attribute
rather than captured. After the next deploy, fetch the bare url and confirm the
tag is a plain inline `<script>` again; if it is still rewritten, the remaining
lever is the optimizer's exclusion setting in wp-admin, which is not in this repo.

---

## 2026-08-11 - the fix for the forever-spinner had a forever-spinner in it (1.74.4)

Found by driving the sibling tracker's live page: with every API call stalled,
the tiles reached the failed state on the deadline exactly as designed, and the
chart zone kept spinning underneath them. Same arrangement here.

**Root cause.** `refreshAggregate` began the charts and the board by hand and
ended them from the tracked promise's `then`/`catch`. A promise that neither
resolves nor rejects reaches neither, which is the precise case the deadline
exists for. So the region with the deadline recovered and the two regions
without one did not. The defect the whole change was written to prevent,
reintroduced by the fix for it, one indirection away.

**Fix.** `busyTrack` takes `companions` as `[id, label]` pairs and owns their
whole lifecycle: begun with the request, cleared with it, failed with it, and
failed on the deadline whether or not the promise ever settles. Callers no
longer touch them, and the `busyFailed()` probe that existed only to let the
caller tell a deadline abort from a supersede abort is gone with the
arrangement that needed it.

**Guard.** `test_a_companion_region_cannot_outlive_the_deadline_it_shares`,
whose `make` deliberately ignores the abort signal, because a companion whose
only exit is the tracked promise settling has no deadline at all. Both new
tests fail on the shipped 1.74.3.

## 2026-08-11 - two tests that went red without a code change, for opposite reasons

`main` had been red for a while on four assertions in two files. Neither was a
defect in shipped code; both were tests pinned to a moment rather than to a
mechanism, and they are worth separating because the right repair differs.

**`tests/test_ci_noise_report.py` - a fixed clock the code could not see.**
Three `TestMain` assertions. The fixtures are stamped relative to
`NOW = 2026-08-03 13:20Z`. `TestClassify` and `TestCompose` inject that instant
explicitly (`SINCE`, `now=`), but `TestMain` calls `cnr.main()`, which derives
its window from `_now()` - the wall clock, with no seam. On day eight every
fixture run fell outside main's own 7-day window, `classify` saw zero runs, and
`main()` took the quiet-week early return: no summary, no subject line, exit 0
where the test wanted 1. Fix: patch `cnr._now` alongside the seams `_patch`
already installs. No assertion, threshold or tolerance moved. Re-dating `NOW`
was rejected for the same reason it was rejected in the sibling repo on
2026-08-10 - it re-arms the identical expiry a week later. This is a port of
that fix (layoff repo `6857cf6`), adapted to pytest's `monkeypatch`.

**`tests/test_audit_publishers.py` - a guard that failed on success.** The
tripwire asserted `stages["publisher_unknown"] > 0`. It was 0. The other five
assertions in the file all passed, so no publisher had gone unanswered; the
opposite had happened. `publisher_unknown` means "the domain is not in the
catalogue at all", so researching a publisher necessarily empties the bucket.
Between the 2026-08-03 and 2026-08-10 runs it went 12 -> 0 while
`publisher_not_wired` went 12 -> 16: all 12 named on 08-03 were answered on
08-04, 8 wired to live feeds (which is why they now classify as
`feed_read_item_missed`, feed depth rather than a missing source) and 4 refused
with the status code seen - renewable-carbon.eu 500, commersant.ge 404,
ctee.com.tw 404, sharesansar.com 404. Those 4 stay in `publisher_not_wired`, so
they are still covered by the wired-or-refused assertions.

The guard was therefore asserting that this project must permanently hold at
least one unresearched publisher, and was guaranteed to fail the moment the
worklist was finished. What it actually protects is that the assertions below
it iterate over something. That is now asserted directly, on the same set they
iterate (`_audit_domains()`) plus the actionable total - strictly closer to the
hazard than counting one bucket was, and still red if both buckets empty. Both
of those failure modes were reproduced against a mutated audit file before the
change was accepted. No name was removed from the audit, no publisher probe was
skipped, and the catalogue was not touched, so `build_sources_json.py` did not
need to run.

Main: 3559 passed, 1 skipped, 421 subtests. Tests only - no plugin change, no
version bump, no deploy.

---

## 2026-08-10 - the page looked frozen while it was working (1.74.3)

**The defect, as the owner reported it.** Both dashboards appear stalled while
data loads. Measured against the code he was describing something exact. All
three fetches in `dashboard.js` ended in a catch that said, in words, "leave the
existing rows in place" or "leave the server-rendered numbers alone". As a
fallback that is right. As a signal it is silence: the previous figures stayed
on screen, fully styled, looking final, and nothing told a reader the difference
between a slow host and a finished page. A non-ok response was worse: it
resolved to `null` and took the same quiet path as success, so an HTTP 500 and a
healthy repaint looked identical from outside.

**What landed.** One small state machine in `dashboard.js` (`busyBegin` /
`busyClear` / `busyFail` / `busyTrack`) with every async region wired through
it: `#tit-fresh-stats`, `#tit-zone-insight`, `#tit-glance`, `#tit-rows`,
`#tit-more`.

- **Loading.** The stale content dims under an absolutely positioned
  `role="status"` overlay carrying a ring and a word, and the region gets
  `aria-busy="true"`. Two channels because they answer different questions: the
  attribute says what you can see is stale, the live region says work is
  happening.
- **Loaded.** Overlay removed, `aria-busy="false"`, reserved height released on
  the next frame so the region never collapses between the two paints.
- **Failed.** Overlay stays, `aria-busy` drops, the copy says we could not load
  it, and a retry button is the way back. `busyTrack` carries a 20s deadline, so
  a request that never answers is given up on and aborted rather than spun over.
  An indicator that spins forever is this codebase's recurring defect class
  wearing a sprite.

**Two things the tests caught rather than the reasoning.** A response arriving
after the deadline used to clear the failed state and paint its data behind the
reader's back; `busyFail` now retires the region's token. And the supersede
abort (a reader typing quickly replaces their own request) must not surface as
an error, which is the same token check read the other way.

`pending` and `pendingAgg` are gone: the supersede abort they existed for now
lives in `busyBegin`, so there is one place a request gets cancelled instead of
three.

No layout shift: the overlay is out of flow and the region's height is frozen
for the duration, with a 132px floor for a region empty on first paint. Under
`prefers-reduced-motion` the ring stops turning and the wording carries the
state, which it does in every case anyway. `--tit-load-scrim` is defined in all
three theme blocks.

**Guard.** `tests/test_loading_states.py`, 17 tests, all 17 failing on
origin/main@8a4ae9c. The state machine is executed for real in node against a
stub document rather than grepped, and every string assertion runs against
source with comments stripped.


## 2026-08-10 - the control that changes the theme was the hardest thing to find in dark mode (1.74.2)

**The defect, as the owner reported it.** The Light / Dark / Auto switcher
becomes hard to see once the page is dark, on the dashboard and on the recall
page. It is the one control that rescues a reader from a theme they cannot
read, so it is the last thing allowed to disappear in any theme, and it was one
of the first.

**Why every check read green.** The control never failed a text ratio. In the
dark scheme its labels measured 7.44:1 and its selected label 8.88:1. What
failed was 1.4.11, the BOUNDARY of the component, and nothing in this repo was
measuring that. The control borrowed page furniture: `--tit-surface` for the
well over a `--tit-ground` page, which is **1.19:1**, and `--tit-line` for its
edge, which is **1.56:1** against the page and **1.31:1** against its own fill.
The three buttons carried `border:0; background:none`, so they had no boundary
to measure at all. In dark mode the whole thing collapsed to three floating
words and one blue pill. Light was no better on the same measure and worse on
one of them (well 1.01:1, edge 1.22:1); it read as intact only because dark
text on a pale page carries itself.

**The fix.** The control owns its colours now, eight `--tit-theme-*` tokens
defined in `:root` and redefined in both dark blocks, and the boundary is
carried more than once rather than by a single hairline:

- the well has a fill AND an edge that clears 3:1 against the page ground and
  against the fill;
- every button has a fill AND an edge of its own that clears 3:1 against both
  the well and that fill, so three controls read as three controls;
- selected is a fill, a weight, `aria-pressed`, and a DOT that is present or
  absent, drawn in `currentColor`. That is the watchlist star's trick: state by
  shape, so it survives a reader who cannot separate the hues. The dot's
  footprint is reserved on all three buttons and only the paint changes, so
  pressing one cannot reflow the group;
- the focus ring is the control's own token at 2px with a 2px offset, measured
  against the well and against the page, which are the two surfaces the offset
  puts it on.

Measured on the rendered page at 375px in both schemes, composited values read
back off the live elements rather than off the source: dark 6.95 / 6.34 / 6.34
/ 4.73 / 10.23 / 8.88 / 7.69 / 5.73 / 9.83 / 10.77, light 4.35 / 3.94 / 3.94 /
4.58 / 12.32 / 5.19 / 4.46 / 5.19 / 6.74 / 7.43, against bars of 3.0 for the
edges and fills and 4.5 for every label. `documentElement.scrollWidth` equals
`clientWidth` at 375 and the group's right edge lands on 375 in every state.

**The guard.** `_theme_control_pairs` in `tests/test_theme_light_dark.py`
computes all eleven ratios from the shipped declarations, for both schemes, and
is folded into the two palette tests, so the control cannot regress quietly the
way it did. Six structural tests alongside it pin what the arithmetic cannot:
the tokens exist in all three blocks, the rules do not name `--tit-line` or
`--tit-surface` again, no button may go back to `border:0` or
`background:none`, the selected mark is a shape and not another colour, the
pressed marker may change paint but never layout, and the ring is the control's
own token. All eight fail on the pre-fix tree, comments stripped before
matching. Not deployed: pushed for the session to publish.

---

## 2026-08-10 - the budget knew how much, and never knew who first

**The defect, and it was an omission rather than a bug.** `spend.py --degrade`
answers "is this month spent", which is a question about a TOTAL. Nothing
answered "who should get the money first". A walk over 2024 and the live
collectors drew on one key at the same 90% line, so whoever happened to run
earlier in the month won the allowance. On 2026-08-03, 91 gnews-backfill
dispatches spent ~$21.5 and exhausted both the month and the key's credit cap;
`collect.yml` on 08-04 then deferred 351 google_news candidates unread. The
`--degrade` step was added to the walkers that same day and it was the right
fix for "a discretionary job must not outlive the cap". It could not fix
ordering, because degrade fires at 90% and by then the money is gone.

**What was already there and was NOT rebuilt.** The $10 UTC-calendar allowance,
`STOP_AT_FRACTION`, degrade-exits-0, the per-collector read rations, the
writer-queue self-requeue chain, `backfill_slices` cursors with `next_cursor` /
`stopped_early`, the `BudgetExhausted` defer-UNMARKED path, `gate_ledger`
deferral accounting, and the publish / retraction / guardrail / deploy approval
gates. All load-bearing, all untouched. In particular the pause-and-resume
machinery this policy depends on was already correct and already tested
(`test_a_budget_stop_resumes_on_the_first_window_it_did_not_do`): the walkers
knew how to stop cleanly, nothing had ever told them to.

**The fix.** `FORWARD_FROM = "2026-01-01"` and `apply_forward_first()` in
`spend.py`, called on the `--degrade` path. A walker workflow declares its
window as `TIT_BACKFILL_START`; if that window starts before `FORWARD_FROM` and
`TIT_HISTORICAL_BACKFILL` is unset, paid reads go off for the job. Five paid
walkers gained the env declaration and a `historical_backfill` dispatch input.

Three design choices worth not relearning:

* **`forward_first_defers` takes no balance argument.** Deliberate, and
  test-pinned by signature. A fraction-of-allowance reserve would have required
  inventing a number, and a threshold cannot fix ordering anyway: the whole
  failure was a walker spending the FIRST dollars of a healthy month.
* **UNKNOWN defers nothing.** An unparseable `TIT_BACKFILL_START` returns
  "no decision" rather than switching paid reads off. Failing closed here would
  have let a typo silence live collection, which is the opposite of the policy.
* **Correctness is out of scope by construction, not by promise.** The gate can
  only fire on a run that sets `TIT_BACKFILL_START`. No `correct-*.yml` and not
  `retract.yml` sets it, so a correction to an already-published pre-2026 row
  cannot be deferred. Pinned by a test that reads those nine workflows.

**Also found and fixed.** `ab-models.yml` held `OPENROUTER_API_KEY` with no
spend step at either end, the only paid workflow with no guard. It now runs
`spend.py --degrade`. `health-digest.yml` also holds the key but buys nothing
(it reads the balance for the digest's spend line), so it is an explicit,
reasoned exemption in the test rather than an omission.

**Also fixed: `ops_status._report_spend` was stale and toothless.** It claimed
"Enforced before every collection run via spend.py --enforce", which stopped
being true when the collect jobs moved to `--degrade`, and it returned `None`
so it could never raise anything. It now parses the policy constants out of
`spend.py` with `ast` (no import, no key, no network), prints FUNDED FIRST /
DEFERRED BY POLICY as distinct states, and RETURNS a problem once the review
date passes, so a pause cannot quietly become permanent. Review due 2026-09-08:
the start of the next UTC allowance month plus one health-digest cycle.

**Test.** `tests/test_forward_first.py`, 22 assertions. 18 fail on the pre-fix
tree with comments stripped before matching; the other 4 are deliberate
regression guards on behaviour that must NOT have moved (the cap is still $10
and still UTC-calendar, degrade still only switches one flag and never exits
non-zero, every paid walker still runs `--degrade`, no walker was deleted).

**No cost effect measured, because nothing was bought.** No model, cap or
ration changed. The intended effect is distributional only.

---

## 2026-08-06 - one budget event was producing two red workflows and two emails

**The defect.** `tripwire` run 31088398613 exited non-zero on "ACTION NEEDED:
this month's spend $10.08 is at or past 90% of the $10 allowance" — the spend
guard binding exactly as designed. Its writer ticket
`20260806T075314Z-tripwire` was then filed `state=failed`, so `drain-writers`
reported "the writer queue has NEW items that need a human" and went red too
(run 31088429711). The owner got a tripwire alert AND a drain-writers alert for
one expected, recurring, correct budget stop, on a schedule that would meet the
same closed gate every run for the rest of the month.

This project settled the principle for the collectors and the backfills on
2026-07-30 — a spend degrade is NOT a failure, `--degrade` exits 0, free work
continues — and never extended it to two places: the tripwire's own exit code,
and how a budget stop is recorded in the writer queue.

**Half one: a budget stop is a DEFERRAL, not a failure.** New terminal ticket
state `deferred` in `writer_queue.py`, meaning *not done, nothing broken, retry
when the allowance allows*.

- `summary()` excludes it from `problems`, which is the exact set drain-writers
  reddens on, so a budget stop cannot make the drainer red.
- It is excluded from RED, not from sight: `writer_queue.py status` lists it
  with its reason and its deadline, `counts` counts it, and `ops_status [2b]`
  prints it.
- **A deferral is not a delete.** `deferral_expires_at()` is the start of the
  month AFTER the deferral (which is when the allowance actually comes back,
  since `spend.month_delta` measures calendar months) plus `DEFERRAL_GRACE_DAYS
  = 5`. Past that the ticket becomes a genuine needs-a-human item. Five days
  because the deferring job runs Monday and Thursday, so the widest gap between
  two chances to resume is four; anything shorter would escalate a ticket that
  is waiting exactly as designed. An open queue nobody drained was found in this
  repo this week, so the deadline is computed from the calendar rather than left
  to anyone's memory.
- `superseded_by` is REUSED rather than duplicated: a later landed ticket of the
  same chain means discovery resumed, so the old deferral does not escalate.
- A ticket that carries no usable date is REPORTED, not forgiven. PASS / FAIL /
  UNKNOWN are three states.

**The channel.** The deferring run is the only thing that knows why it stopped,
and it cannot write `data/writer_queue.json` — drain-writers pushes that file
every tick and a second writer rebasing onto it is the lost-write shape this
repo has paid for twice. So it writes ONE marker named after its own run id
under `data/writer_deferrals/`. Unique filenames merge by existing, so the
tripwire's reset-and-copy-back cannot destroy another run's marker the way it
could destroy another run's rows. The next tick applies it, deletes it, and
commits the deletion; an unclaimed marker is swept after 30 days, out loud.

**Half two: the tripwire no longer goes red for stopping.** `spend.py --gate` is
a third mode beside `--degrade` and `--enforce`: exit 0 either way, print the
same report, emit a `::notice::` naming the spend and the allowance, and answer
the workflow through `$GITHUB_OUTPUT` as `over=true|false` so the paid step
skips itself. **Nothing about the ceiling moved** — same $10 allowance, same
`STOP_AT_FRACTION = 0.9`, and a gated run spends exactly $0.

A GENUINE tripwire fault stays loudly red, and that line is pinned: an
unreachable model, a crash, a bad key or a silent zero is `run_tripwire.py`'s
own non-zero exit, which the gate never touches.

**The signal survives as one email.** The red run used to be what told the
owner. Now that stopping is green, `ci_alert.py --notice-key` sends one alert on
the same endpoint, the same server-side dedupe and the same held-not-lost outbox
as every other alert, keyed `spend-ceiling:<YYYY-MM>` — one email per allowance
month however many runs meet a closed gate.

**The audit.** `tripwire.yml` was the ONLY workflow running `spend.py
--enforce`; every other one already used `--degrade`.
`test_no_workflow_is_hard_stopped_by_the_spend_guard` keeps it that way as a
test rather than as a claim in a report. `collect.yml`'s header had said "spend.py
runs first and exits 1 at 90%" since the 2026-07-30 change that stopped it doing
that; corrected.

**Reconciliation of what was stuck.**

- `20260806T075314Z-tripwire` — re-filed by hand as `deferred`, exactly as the
  new tick would file it, with the reason and the date the escalation clock runs
  from. Its `red_marks` entry was pruned. `writer_queue.py status` now exits 0.
- `20260805T134352Z-backfill-press-2026` — SUPERSEDED. Acknowledged 2026-08-05
  with the root cause (a urllib3 `ReadTimeoutError` escaping `head_text`, fixed
  on main at bc9c82c), and `superseded_by` resolves it to
  `20260805T184300Z-backfill-press-2026`, which landed; the chain then ran on to
  cursor 16 and finished. Both mechanisms already agree it needs nothing, so no
  edit was made rather than adding a second marker saying the same thing.
- `20260803T215353Z-backfill-gnews-2026` — **LEFT OPEN ON PURPOSE.** It is
  acknowledged, so it does not redden CI, but the chain has no later ticket at
  all: it is still stopped at 2026-07-13 against an end of 2026-07-26. The
  blocker is stated on the ticket — the OpenRouter key hit its $20 credit cap at
  $26.81 and every paid call has 402'd since 2026-08-03 — so the work is neither
  done nor superseded and must not be resolved. It resumes after a top-up.

**Tests.** `tests/test_budget_stop_is_not_a_failure.py`, 36 tests, every
workflow assertion made against the file with comment lines STRIPPED so a `#`
line quoting the old behaviour cannot satisfy a substring check. 30 of the 36
were proven to fail on the pre-fix tree; the 6 that pass there are the
deliberate guard-the-guard invariants (a `failed` ticket must still need a
human, a genuine tripwire fault must still be red, the allowance must still be
$10). `test_the_tripwire_still_hard_stops` pinned the defect and was replaced by
`test_the_tripwire_gates_rather_than_hard_stopping`, which also fails pre-fix.

---

## 2026-08-05 - one language standard across both trackers (1.74.0)

The owner's brief: the language on both dashboards should read like the Los
Angeles Times or the Boston Globe, understandable by a college-level reader.
That is a real requirement, and prose requirements decay quietly. So the
standard is written down once and a machine holds it.

- **`docs/STYLE.md`** is the standard, BYTE-IDENTICAL in both repos, same
  pattern as `docs/card-contract.json`. Register, sentence ceiling, the
  plain-word table, the attribution rule, the standing bans, and a BEFORE and
  AFTER table built from real strings on these pages.
- **`style_check.py`** (`railway/style_check.py` here, `style_check.py` in the
  sibling, same bytes) extracts only READER copy and scores it. Flesch-Kincaid
  implemented directly with its own syllable counter: no dependency added,
  because every install here is hash-pinned and the formula is arithmetic.
- **It strips comments first, and that is the point.** Both codebases write
  long rationale comments in the register of the copy, which quote display
  strings verbatim INCLUDING REPLACED ONES. A scorer that read them would grade
  the commentary, pass while the page was wrong, and fail after a correct fix.
  The stripper is quote-aware and length-preserving, so line numbers survive
  and a failure names the sentence, its file and its line.
- **Thresholds are MEASURED, not chosen** (reading taken 2026-08-05, before any
  rewrite): 30 words per body sentence, page mean grade 11.0, passive 25%.
  Set at or slightly better than where the better pages already sat.
- **Result.** Layoff mean grade 8.46 -> 7.01, talent 7.14 -> 6.54. Worst page
  12.7 -> 8.1. Most passive page 38% -> 3%. 123 over-length sentences -> 0.
  Roughly 174 reader strings rewritten across the two products. No number,
  basis, caveat meaning or legal framing was changed.
- **Three guard tests caught copy that other tests pin** (`kept out of search
  results`, `counted as unrecorded, not assigned one`, `Metro widgets are
  deliberately unavailable`). Those phrases were restored and the prose
  rewritten around them. Run the full suite, not just the style check.
- **A banned term inside quotation marks is exempt**, because we describe the
  phrases we SEARCH for and `"workforce reduction"` is a real discovery term in
  `source_registry.py`. Rewriting it out of that list would have made the page
  describe a collector that does not exist.
- **`canonical` does not mean "official"** here, it means the row we count in
  its own right. The jargon list says so, because a list that suggests a wrong
  synonym is worse than no list.

Held by three things, same design as the card contract: this repo's offline
test pins both digests; `docs/TECHLOG.md` records them, so a deliberate edit is
visible; and `.github/workflows/style-standard.yml` fetches the sibling's
copies daily and reddens while they differ.

**docs/STYLE.md sha256:** `28975ec6e9e5d99e95c8fc775f8ab033d558454091e8b8c3a972d314ef238c85`
**style_check.py sha256:** `a45b3347508d830d128042f524946755508b2e5fd56bf971905a9cf2930e68b9`

---

## 2026-08-05 - INCIDENT: the spend degrade killed the press backfill chain

**Symptom.** Run 30982514410 (`backfill-press-2026`, window 2026-01-01..06-30)
committed its slice and then went red on: *"the slice was committed but the
backfill chain did not advance, so nothing was requeued."* The chain was dead
after one slice, and every retry would have reproduced it exactly.

**Root cause, and it is an interaction rather than a bug in either half.**
PR #10 put the backfills under `spend.py --degrade` so discretionary work could
not spend the scheduled collectors' allowance. Its stated promise was that
`--degrade` "always exits 0", "can never fail a backfill step", and leaves the
candidates it cannot pay for UNMARKED for a later pass. With the month's
allowance spent, `classify.classify` raised `BudgetExhausted` on the FIRST
candidate of the run, and `backfill_press_2026` caught `BudgetDeferred` **before**
the `Throttled` handler that would have deferred and continued, setting
`stopped_early` and `break`ing out of the candidate loop and then out of the
PUBLISHER loop. The run walked **2 of the 40 publishers in roster index 0** and
stopped. Two-fortieths of an index is not a finished index, so `roster_progress`
left `done_through` at `None`, the emitted ticket carried a `next_cursor` equal
to the cursor the run started from, and `backfill_slices.record` did exactly
what it is designed to do: refused to requeue a chain that had made no progress,
and went red. **Every guard behaved correctly. The defect was upstream of all of
them** — a shut wallet was being reported as an unwalked roster.

The same cause had already stalled `backfill-press-2026:2026-01-01..2026-07-30`
at slice 4 on 2026-08-02, and killed a `backfill-gnews-2026` slice on 08-03 via
`CreditsExhausted` (writer-queue ticket `20260803T215353Z-backfill-gnews-2026`,
acknowledged as "class fix is PR #10" — it was not; PR #10 is what exposed it).

**The fix.** `paid_path_closed` is a **latch, not a stop**. The first
`BudgetDeferred`, `CreditsExhausted`, or `--max-readthroughs` refusal closes the
paid path for the rest of the run; the candidate is left UNMARKED and counted in
a new `left_unread` total, and **the walk continues**. Fetching, prefiltering and
every `cheap_extract` close are free and still run, so the publishers really are
read and the cursor advances honestly. What is lost is DEPTH, which already had
a name in this walker: `rationed_off`, the candidates past the ration, left
unmarked so a later pass reads them. A budget-deferred candidate is the same
thing for the same reason. `AuthFailed` still returns 1 — a bad key is wrong for
every run and cannot be left for later.

**No guard was weakened.** The runaway guard in `record` still refuses a cursor
that did not move; `roster_progress` still refuses to pass an index whose every
publisher failed at the transport layer; the wall-clock budget still stops a
slice at a publisher boundary and still stalls the chain if it stops mid-index.
Only the wallet stopped counting as either of those.

**Visibility, which was the second half of the failure.** The run that caused
the stall exited 0 and said nothing about it, so the only explanation anywhere
was `record` one step later saying "the cursor is still 0" without saying why.
Three changes: the walker now prints `NOT REQUEUEING:` naming the roster index
and how far it actually got (`N of 40 publishers`) and the reason the walk
ended; `record`'s stall message now quotes the `stopped_early` the ticket
carries, and says so explicitly when a ticket carries no reason at all, because
"no reason" must never read like "no problem"; and the workflow captures
`record`'s output and replays it into the `::error::` annotations instead of
telling a human to scroll.

**Pinned by** `tests/test_backfill_press_degrade.py`, 9 tests, **all nine
verified failing on the pre-fix tree**.

---

## 2026-08-05 - the shared email digest signup, pinned by an executed guard

The embed itself landed at 1.71.2 (`85bba44`): `tit_dashboard_html()` calls the
sibling plugin's `alt_digest_subscribe_form('talent')` behind
`function_exists()`, after the trust panel and before the citation footer, so
the form never pushes the data below the fold. One WordPress install, one
subscriber table, one consent record per person; no require crosses the plugin
boundary.

Its test did not hold. `test_the_dashboard_prints_the_shared_signup_form` was an
`assertIn` over the raw file, and the embed sits under fifty words of prose, so
**commenting the call out left all four tests passing on a dashboard that
rendered no form at all** (verified by mutation). Rewritten: every textual
assertion now strips comments first and matches a CALL rather than a mention,
and the missing-dependency path is **executed** rather than described. The
guarded block is lifted out of shortcodes.php by brace matching and run under
the real `php` binary twice: with the sibling absent it must exit clean and
print nothing, with a stub defined it must print what the stub returned. A third
test proves an unguarded call really does fatal on this php build, without which
the silent-degrade test would prove nothing.

---

## 2026-08-05 - the owner read his own pages: coverage named as coverage, and the dashboard's trend becomes a market claim (1.72.0, pushed, NOT deployed)

Three changes, all found by the owner reading his own live pages, none of them
a wrong number: every figure was correctly computed and the page still misled.

**1. The recall page made a doubling of coverage read as decline.** The
direction sentence said "Held has gone from 9% to 19.5%, a change of +10.5
points", and the owner asked "we're getting worse?". On a page about what we
MISS, a bare metric called "Held" rising reads as more of something bad. The
metric is the share of the independent gold set we hold, i.e. coverage, and
the sentence now says so, states the movement as a gain or a fall, and adds
"Higher is better" in words. Swept the rest of the page for the bare metric
name: the mobile data-labels on the two percentage columns now match their
column headers ("In the tracker" / "And every field right") and the chart's
screen reader text says "in the tracker", never a bare "held". The per-event
verdict labels ("Held and correct", "Held, field wrong") stay: those are
states the methodology section defines, not the metric. No number and no
measurement changed. Guarded by a full-page render in
tests/php/render_recall.php that holds the exact sentence and rejects the old
one.

**2. "Updates Collected a Day" left the dashboard for the sources page.** It
plots our own collection rate, which is an operations measure, and the owner
judged readers do not need it on the dashboard. Moved, not deleted:
sources.php renders the same tit_signal_trend() series beside the collectors
it describes, minus the tap-to-filter sentence (no filters and no dashboard.js
there; tit_signal_trend_html grew an $interactive flag). The sources page now
costs three queries cold instead of one, cached and itemised in its harness.

**3. Its dashboard slot carries a fixed-panel MARKET trend, with direction as
the split.** tit_market_trend(): twelve whole Monday-to-Sunday weeks, running
week excluded, weekly update counts split by stated headcount direction
(Adding Roles / Cutting Roles / Headcount Not Stated), drawn ONLY from the
collectors whose first ingest day is on or before the window start and whose
last is inside the final week: same-store-sales logic, liveness by
DATE(captured_at) and never by publication date, for exactly the reason the
2026-08-03 entry gives about the breadth scan. When the panel is thinner than
five sources the chart falls back to COMPOSITION (each week's shares of its
own updates, which survive volume changes), and under four weeks of data it
draws nothing and says so. The visible caveat on the card names the panel
size, the window, the variant and why, and says the page filters do not
narrow this card; it is .tit-chart-caveat prose, never note_html, per the
place-caveat lesson. Raw all-collector counts are never drawn as a market
claim in any state. Today, live, no collector has been live for a full twelve
week window (collection began 2026-07-26), so the page renders the
composition variant and says so; the counts variant switches on by itself
once the fleet has twelve weeks of history behind it.

The standalone "Updates by Stated Headcount Direction" card is gone: its
numbers are the split inside the market chart. The by_direction group stays
on /aggregate and its GROUP BY stays in the facts bundle (the
stated-headcount toggle's figure is summed from it). /aggregate still serves
trend_html for any consumer; the dashboard no longer injects it (the
tit-trend-box is gone, dashboard.js's lookup is null-guarded). Chart count 9
-> 8; the "What Kind Of Moves" group drops --four for the base three-column
grid.

**Budgets.** TIT_DASH_QUERY_BUDGET held at 15, two out (trend rollup, breadth
scan, both moved to the sources page) and two in (panel discovery, weekly
split), itemised at the constant. Byte budget raised 181,600 -> 184,600,
itemised in the harness: the fixture prices the DRAWN market chart against
the old collapsed trend card, so the fixture gets heavier while the live page
gets lighter (the drawn collection chart, ~5,400 bytes, leaves entirely).

**Guards, all proven red against the pre-fix tree (064472e):**
tests/php/render_recall.php (the coverage sentence, three ways),
tests/php/render_dashboard.php (market card present and honest, direction
card and collection card gone), tests/php/render_sources.php (moved not
deleted, three queries), tests/php/market_trend.php (NEW harness with
controlled ingest dates: the counts variant excludes a 100-row mid-window
flood, the thin panel falls back to shares, two sparse weeks draw nothing,
two queries exactly), and five new assertions in
tests/test_chart_titles_and_basis.py (8 titles, no "Collected" title on the
dashboard, sources.php carries the moved chart, the counts variant is
panel-restricted in source, the market caveat is visible prose).

**4. The recall country table became readable, and got real denominators
(same session, owner follow-ups).** Four parts:

- Country rows print FULL NAMES via tit_country_name(), the plugin's own map,
  never a second list; the by-country history table goes through the same
  country-aware label. AE, AR, AT is not a table a human reads.
- The two score headers are "Event captured" and "Captured with every detail
  correct" (the owner had to ask what "And every field right" meant), with
  one sentence above the tables saying the second score is stricter: an event
  held with one wrong detail passes the first and fails the second. The stat
  tiles and the chart legend use the same words, so the page keeps one
  vocabulary.
- UNDER each country row, the sources: live sources reading that country,
  publishers probed and refused with the probe's own dated one-line reason
  (capped at three shown plus a count), the researched queue, and where there
  is nothing: "No dedicated source yet. Events here can only arrive via
  worldwide discovery." Always-visible prose, never a collapsed panel. The
  data is data/country_sources.json, written by build_sources_json.py from
  source_registry.country_coverage() in the same run as sources.json (155
  countries, ~45KB), same em-dash refusal at the build boundary. A publisher
  the hand registry wires (Sifted) is never listed as refused, mirroring the
  manifest's hand-entries-win rule. Guard:
  tests/test_country_sources_manifest.py, six tests.
- A NEW whole-market table, "Against the whole market, by country": our
  holdings against EXTERNAL market-size counts for KR, DE, SG, IT, ES, with a
  Read column (Real gap / Thin coverage / Not comparable). South Korea is the
  load-bearing row: the external figure counts individual fund investments
  and ours counts rounds, so its 0.1% prints under "Not comparable" and never
  as a coverage score. Kept as a SEPARATE table from the gold set (different
  question, different units, labelled, never blended). Figures are a dated
  snapshot (both sides recorded together 2026-08-05) and the note says
  external counts use their own definitions and dates, so shares are
  indicative and not a parity claim. No reference is NAMED: none could be
  confirmed against these exact figures at the time of writing, so each
  carries a neutral descriptor instead, and competing trackers and paid data
  products are never cited at all.

**Found in passing and fixed:** the "Render the dashboard" step in tests.yml
was a plain-scalar `run:` whose continuation line YAML joins with a space, so
CI was running `php tests/php/jsonld_xss.php <ignored argv>` and the dashboard
harness NEVER RAN in CI. It is a block scalar now and both run.

---

## 2026-08-04 - the preamble exit: instrumented and priced honestly, NOT taken (key exhausted)

**The waste, measured.** Every extraction call re-sends the same byte-stable
preamble — `MINI_SYSTEM` + `SCHEMA_HINT`, 11,016 characters, 2,509 tokens at
the repo's 4.39 chars/token calibration (SCHEMA_HINT alone is 2,478) — as
FRESH input on `deepseek/deepseek-chat`, a slug where no OpenRouter endpoint
prices a cache read. The ledger agrees from the billing side: the last 27
priced runs report `cached_tokens = 0` on 7,918,361 prompt tokens. At full
coverage that preamble is ~$16.4/month of the $54.20 unconditional bill
(30%), all MODELLED numbers labelled as such.

**The exit `cost_projection.py` prices** is extraction on
`google/gemini-2.5-flash-lite` (already trusted as the gate): extract line
$27.23 -> $4.78/month, total conditional bill $33.18 -> $10.73. Decomposed:
the model swap alone (uncached) is $27.23 -> $10.62; the prefix cache
engaging (flash-lite bills cache reads at $0.010/M) is the remaining
$10.62 -> $4.78. **Neither part is claimable without the swap** — on the
incumbent slug caching is worth exactly $0, re-verified in the snapshot.

**Why it was NOT taken today.** Two proofs are owed and both need live calls,
and the OpenRouter key is exhausted ($26.81 lifetime against a $20 cap — every
call 402s):

1. **Extraction quality.** `ab_models.py --extraction` (built 2026-07-30 for
   exactly this decision) has never been run. A cheaper model that quietly
   loses `country` on a fifth of records is a coverage regression sold as a
   saving. The bar: read every `company`/`country` disagreement against the
   incumbent, per the tool's own output.
2. **The cache actually engaging.** A cache_control flag or a routed provider
   that "should" cache is worth nothing until billed tokens say so. New:
   `ab_models.py --cache-check [slug]` sends the PRODUCTION extraction prompt
   twice, 5s apart, and prints both calls' billed `prompt_tokens` /
   `cached_tokens` / cost from OpenRouter's usage accounting. Verdicts are
   three-state — CACHED (exit 0, >=1,024 cached tokens on call 2), NOT CACHED
   (exit 2), UNKNOWN (exit 3, could not check) — and a 402 is UNKNOWN, never a
   pass. Today it exits 3.

**What shipped instead of a claim:**

- `classify.extract_stable_prefix()` — the cacheable bytes, exposed like
  `prompts.stable_prefix`, so the token claims are measured, not remembered.
- `ab_models.py --cache-check` — the two-call verification, executable.
- `cost_projection.EXTRACT_PREFIX` corrected 2,754 -> 2,509: the old value was
  9.8% above what the prompt holds, nothing supported it, and it flattered
  every cached row by ~$0.57/month (the $4.21 previously quoted for the
  flash-lite row is really $4.78). A test now pins the constant to the live
  prefix.
- `tests/test_preamble_cache_exit.py` — 8 tests, all proven to fail on the
  pre-fix tree.

**The arming procedure for the next session with a live key**, in order, stop
at the first failure: (1) top up the key; (2) `python3 ab_models.py
--extraction` and READ the disagreements; (3) `python3 ab_models.py
--cache-check google/gemini-2.5-flash-lite` and record both calls' billed
numbers — the second call must show cached_tokens >= 1024; (4) only then set
`TIT_MODEL=google/gemini-2.5-flash-lite` on both collect jobs (env, one line
each) and watch `cached_tokens` become nonzero in the next ledger rows;
(5) revert is unsetting the variable. `spend.py --degrade` untouched and still
exit-0; no collector, language or cap changed.

## 2026-08-04 - the verifier cried twice about nothing, so it was the verifier that was wrong

Two of the five published-figure checks were FAILING live, and neither defect
existed. No plugin file changes here; `published_figures.py` and its guards do.

**1. The ribbon, 103 against 104.** `home.ribbon_countries` was stamped with an
empty query, so it was compared against an unfiltered `/aggregate`. The ribbon
counts distinct countries among NOTABLE rows (`tit_dashboard_facts()` groups its
country map under `is_current = 1 AND tit_notable_where()`); the unfiltered
endpoint counts routine rows as well and reaches one more country.
`/aggregate?detail=notable` answers 103, which is exactly what the ribbon
renders. The module's own note on `TILE_FIGURES` warns about this mistake in so
many words, and the figure two lines above it made it. Each figure now fetches
its OWN stamped query rather than sharing one unfiltered call.

**2. The region partition, -1,488.** `region_parts_reconcile` asserted
`sum(six regional badges) == World badge`. That held only while the World badge
was itself the sum of a placed country map, which is the defect 1.71.1 fixed.
World is the view's own total now, so it correctly includes the notable rows
carrying neither a country nor an HQ country, and those rows are in no region by
construction. The old equality was made false by a correct fix.

It is replaced rather than relaxed, and the replacement separates the two faults
the old sum could only report as one number:

- **disjoint**: the badges must equal what ONE query for the union of every
  region's codes returns, so a country listed in two regions fails and names
  itself (live: 23,991 = 23,991);
- **exhaustive**: every country `/aggregate?detail=notable` ranks must appear in
  some region list, so a country we hold rows for that no tab can reach fails
  and names itself (live: all 40, 23,835 records, with a 156-record tail that is
  counted and explicitly NOT name-checked, because the ranking is a LIMIT 40);
- **the remainder is named**: World minus the union is reported every run as the
  placeless population (live: 25,479 = 23,991 placed + 1,488 placeless) rather
  than passed over in silence.

A check that reports a defect every day is a check that gets switched off, and
then the real defect goes out under it. Both guards keep their failing halves:
`tests/test_published_figure_guards.py` gained an overlap fixture, an
unreachable-country fixture, and a genuine ribbon disagreement, and 7 of its
cases fail on the tree before this change.

---

## THE RESULT CARD CONTRACT (canonical spec, shared with the sibling tracker)

**Machine-readable copy:** `docs/card-contract.json`
**sha256:** `5ce62ea8d11073b132af83696e222f0a2c4184fba646c5f0adcb9c06f7493af2`
**Contract version:** 1.0.0, adopted 2026-07-31.

That file is **byte-identical** in `dk-forge/ai-layoff-tracker` and
`dk-forge/talent-intelligence-tracker`. This section and the section of the same
name in the sibling's TECHLOG are the human copy of it, and the digest above is
how you tell whether the copy you are reading is current.

### Why a contract and not shared code

The two trackers render the same kind of fact: an employer, a place, a
direction, an evidence tier, an amount, a headline, a source. The owner
screenshotted the talent tracker's list, liked it, and asked for the layoff
tracker's to match. By the time an agent looked, the talent tracker had already
changed its own labels, so neither side could say which design was current.

**The mismatch was not the defect. The inability to say which one was current
was the defect.** Matching the pixels once would have fixed nothing; they had
already drifted once and would drift again.

Shared code was considered and rejected. Different repos, different tables,
different REST namespaces, different plugins, different deploy paths, different
languages on the server side (one renders the first paint in PHP, the other
inlines a bootstrap and renders in JS). A shared library across that boundary
buys a smaller problem at the price of a much worse one: a coupled release
cycle, and a change to one product's card blocked on the other product's deploy.

What is shared is the **contract**, and what enforces it is a build that goes
red when one side wanders.

### The card

Every class below is a **suffix**. Each product renders it with its own prefix:
suffix `card-rail` is `alt-card-rail` here and `tit-card-rail` in the sibling. A
product may put extra classes on the same element (its own colour and state
classes); the contract class must be present.

```
<ol|ul class="{p}-cards">
  <li class="{p}-card">
    <div class="{p}-card-rail">            who, and where
      <span class="{p}-card-employer">     serif
      <span class="{p}-card-industry">     optional; ABSENT when unknown, never blank
      <span class="{p}-card-where">        location, or "Location not stated" in {p}-card-nowhere
    </div>
    <div class="{p}-card-body">
      <div class="{p}-card-badges">        direction, evidence, amount, then the product's own
        <span class="{p}-card-dir">
        <span class="{p}-card-ev">
        <span class="{p}-card-amt">        ONLY when there is an amount
      </div>
      <a|span class="{p}-card-h">          the fact, one line; link colour only when it links
      <p class="{p}-card-rt">              our plain-English read, visually separated
      <div class="{p}-card-foot">
        <time class="{p}-card-when">       or "Date not stated" in {p}-card-nowhere
        <span class="{p}-card-src">        publisher, outbound; archived copy SECOND, never instead
      </div>
    </div>
  </li>
</ol>
```

### The words

| Stored | Label |
|---|---|
| `hiring` | Adding Roles |
| `displacement` | Cutting Roles |
| `comp_shift` | Pay Change |
| `neutral` | Headcount Not Stated |

The **keys are each product's own and are not shared**; the four strings are.
The talent tracker reads them off its `signal_direction` column. The layoff
tracker has no such column and derives its key: a record naming a headcount is
`displacement`, a record naming none is `neutral`. `hiring` and `comp_shift`
never occur there, because everything it holds is a cut. They are absent, never
renamed, never reused for something else.

**Why this vocabulary.** "Adding Roles" replaced "Hiring up" in the talent
tracker after the owner asked what "hiring up" meant, which is a fair question
about a phrase nobody says: "up" was doing the work of "the source told us
headcount is going up". "Cutting Roles" is its opposite in the same shape, where
"Cutting back" could have meant costs, hours or investment. "Headcount Not
Stated" replaced "Other change", which told a reader nothing: it is the bucket
for a record whose source says nothing about headcount at all, and naming it
that way is truer to the rule that neither product infers a direction its source
did not state. That reasoning stands, so the layoff tracker adopted it rather
than the reverse.

**Why Title Case here specifically.** The general house rule is sentence case,
and it still governs every label outside these four. These four are the
exception on the record: the owner has asked for Title Case three times, the
talent tracker's `tests/php/render_dashboard.php` enforces it on its display
labels, and the decision that created this contract quoted the four strings in
Title Case. Changing that is a contract change, not a tidy-up.

**Evidence labels stay each product's own**, because the evidence really is
different (`SEC filing / WARN notice / Press release / News` against
`Official Filing / News Report / Unconfirmed`). What the contract fixes is the
**slot**: second badge, always present, always carrying words and never colour
alone.

**Shared verbatim:** "Location not stated", "Date not stated". Said out loud,
never left blank, never guessed.

**The amount badge is omitted when there is no amount.** It is not a pill
reading "count not stated" or "no funding stated": the direction badge has
already said so, and two badges saying one thing was the duplicate this contract
removed.

### Accessibility, pinned because both products already paid for it

- Anything that opens more detail is a real `<button type=button aria-expanded>`,
  never a click handler on the row. The layoff tracker's expander was a
  mouse-only `<tr>` click; it is a button and stays one.
- **No `aria-label` over visible text.** An aria-label on an element that
  already has text replaces that text for a screen reader; the talent tracker
  shipped longer, invisible, differently worded labels over its visible ones.
  Inside a card an aria-label is allowed only on an element with no text of its
  own, and today no element in either card qualifies.
- `title=` is a supplement. Nothing a reader needs lives only there.
- Source links: `target="_blank"` with `rel` containing `noopener`.

### 375px

The rail stacks above the body and nothing else changes. Nothing inside a card
sets a fixed width or a min-width; long values wrap with
`overflow-wrap: anywhere`. **Do not validate this with
`scrollWidth === innerWidth`** — that passes on a clipped page, an
`overflow-x: clip` on a narrow ancestor guillotined the talent tracker's hero
headline in 1.37.0, and the layoff tracker's theme ships an inline
`html,body{overflow-x:hidden}` that makes the comparison meaningless there too.
Both test suites therefore check the **cause** (no pinned widths, wrapping on
the free-text fields) rather than the symptom.

### What stops it drifting again

Three mechanisms, and each covers what the others cannot.

1. **`test_card_contract` in each repo**, offline, on every push. Reads the
   contract and asserts that the markup that repo actually renders satisfies it:
   every required class, the badge order, the region reading order, the label
   maps parsed out of the source, the two a11y rules, the mobile rules. It
   cannot see the sibling.
2. **The digest, recorded twice per repo** — in the test and in this section.
   An accidental edit to the contract fails the test. A deliberate edit means
   updating the digest, which is the moment you are told this is a two-repo
   change.
3. **`.github/workflows/card-contract.yml` in each repo**, which fetches the
   sibling's copy of `docs/card-contract.json` and goes red while the two
   differ. This is the only mechanism that can see across the repo boundary,
   which is why it needs a network and lives in CI rather than in the offline
   suite. Both repos are public, so it needs no token.

**Changing the card is a four-step job and you cannot do three of them and
ship:** edit the contract, update the digest in the test and here, change the
markup, copy the contract into the sibling. Miss the last step and both repos go
red until somebody finishes.

---

## 2026-08-04 - the CXMT IPO is withdrawn, and the withdrawal lost its own figure to a shell

**The retraction.** ChangXin Memory Technologies, `$8.6bn`, content_hash
`875d48bbbcc438a9744e8982d1843f6e`, published 2026-07-29 and live ever since.
The source says what it is in the headline — "CXMT becomes China's most valuable
A-share company after $8.6 billion IPO" — and again in the summary: "raised
RMB57.92 billion ($8.6 billion) in its Shanghai STAR Market IPO". An IPO is not
private funding. It is error class 4 of the four the audit named, and it was the
last one still live.

It survived because the amount guardrail DID flag it on 2026-07-29 and a
session ACCEPTED it as a genuine raise. An accepted finding is remembered
forever and reported by nothing, so the row published and every ops surface
stayed green. The guard worked; the review was wrong; nothing downstream can
tell those apart. `ops_status.py` exits 2 on findings left OPEN too long — it
has no opinion about a finding closed wrongly, and that asymmetry is the hole.

Withdrawn through `retract.yml` (WordPress first, database second: the order
matters, because the reverse leaves a row live on a page while our copy
believes it is gone). `wordpress=1 local=1`, run 30875221356, committed as
`4a6c7dc`. The ledger verdict is now `rejected`, so the record says what
happened rather than what was believed on the day.

**Queued, not dispatched.** `retract.yml` is a database writer and shares the
`talent-collect` lock, so it went through `drain-writers.yml` like everything
else. It is also the only path with `WP_SITE_URL` and `WP_API_KEY`, which is
why three previous sessions recorded this row as still owed: it does not need a
human, it needs the credentials that only Actions holds.

**The defect the withdrawal shipped with.** The reason sent was "... an $8.6bn
Shanghai STAR Market IPO ...". What WordPress and the database both recorded
was "an .6bn". `retract.yml` pasted `${{ inputs.reason }}` straight into the
`run` block, so bash expanded `$8` to the empty eighth positional parameter and
ate the figure the retraction exists to state. The run was green and nothing
warned.

`drain-writers.yml` has passed dispatch inputs through the ENVIRONMENT since it
was written, with a comment saying exactly why. The lesson had never been
carried across to the workflow it dispatches — the same shape as the push-replay
loop that `collect.yml` solved first and `retract.yml` learned only after losing
a withdrawal. `retract.yml` now takes `TIT_REASON`, `TIT_SIGNAL_ID` and
`TIT_BARE` from `env:` and quotes them. A reason carrying a backtick or
`$(...)` would have been executed rather than merely eaten.

The local note is repaired in this commit. **The WordPress-side note cannot
be:** `/talent/v1/retract` updates `WHERE is_current = 1` and the row is now 0,
and `/correct` refuses retracted rows by the same rule. It is not rendered —
`/corrections/` is a hand-maintained list, not a query over withdrawn rows — so
the damage is a wrong string in a column nobody reads. Recorded rather than
worked around, because reviving a retracted row to fix its prose is a worse
thing than the prose.

**Still owed, and now measured.** The audit said 6 of the top 25 rows by
`funding_amount_usd` are a correctly-scaled private round. The amount queue only
ever held rows above the derived ceiling of ~$6.5bn, so everything below it was
reviewed by nobody. Of the 12 rows in the top 25 with no `amount` ledger entry
at all, 7 do not survive reading their own headline:

| Row | What the source actually says |
|---|---|
| Marcos $2.5bn | Not a company. The Philippine president collecting investment *commitments* on a state visit; "Marcos" is stored as the employer |
| GSK $2.2bn | An acquisition (GSK buying Rapt), booked against the acquirer |
| Bradesco $2.0bn | A listed bank's capital increase |
| Revolution Medicines $2.0bn | "prices $2B raise" — a public stock and debt offering, the same class as CXMT |
| Cursor $2.0bn | "Set For ... Round", reported — not closed, and stored at `reported` |
| Nscale $2.0bn | Duplicate of an already-resolved Nscale row whose own headline is "Aims to Raise" |
| Ominimo $1.6bn | A *valuation*, not a raise — the summary says so and the amount column disagrees |

That is two error classes the audit's list of four does not contain: **an
acquisition counted as a raise**, and **a person counted as a company**. The
remaining 5 are sound (Isomorphic, Saronic, and three SEC Form D placements at
`verified`). None of this is fixed here; it is written down so the next session
inherits the finding and not the search.

---

## 2026-08-04 - the amount guardrail was quarantining the truth, and nobody was reading the queue (1.70.0, pushed, NOT deployed)

**The state that started it.** The publish quarantine held 15 rows worth
$874.2bn. Every one was `state='open'` with `reviewed_at` NULL. One had been
re-seen 229 times over five days. The live site published $212.5bn. So roughly
four fifths of the funding dollars we held had never reached a reader, and the
thing holding them was a human review step with nobody on it.

Nothing was broken. `pipeline/publish.py` excluded every quarantined
content_hash exactly as designed, the countdown printed exactly as designed, and
the design's own escalation was 192 hours away for findings already ignored for
four times that in aggregate. **A check whose queue is never read has been
silently converted into a delete.**

**The inversion, which is the actual defect.** The single-row ceiling is derived
from the corpus's own distribution, which is the right way to derive it. In 2026
that puts it at $6.55bn over 3,928 stored amounts, and $6.55bn is BELOW every
real AI mega-round of the year. The check was flagging correct answers at the
same rate as wrong ones.

Raising the threshold does not work, and the ledger is the proof rather than an
argument. Sorted by size the queue interleaved:

    $539bn  Arch              "Surpasses $539 Billion In Private Market ASSETS"
    $100bn  Turkish Airlines  a 100bn LIRA capex programme; the story is $2.3bn
     $30bn  Anthropic         a real round, GIC and Coatue at $380bn
     $20bn  xAI               a real Series E
     $15bn  A16z              an investor's own fund close

Any ceiling that admits Anthropic admits Arch. Size is not what is wrong with
them.

**Two questions, two answers, and both are required.**

*Is the FIGURE right?* Independent outlets. A misparse is one outlet's mistake;
a $30bn round is reported by everyone. And the corroboration was ARRIVING and
being destroyed: on 2026-08-01 the Anthropic row stored from one outlet at
14:25:39, reuters.com arrived at 14:26:21 and w.media at 16:53:45, and on
2026-08-04 Anthropic's own press release for that exact round arrived. All three
were marked `duplicate` in seen_urls, which keeps a url and the word
"duplicate": no employer, no amount, no pointer to what was duplicated. Four
independent reports of one figure, and the row they corroborated sat quarantined
for a fifth day for want of exactly that.

Dedup is the only place this can be captured, because by design the second
outlet's article never becomes a row. New table `funding_corroborations`
(signal_id, host, amount_usd), written by `store.record_corroboration` at the
three sites that call `dedupe.funding_event_duplicate`: `run_collect.py`,
`backfill_gnews_2026.py`, `backfill_press_2026.py`. Host is the registrable
domain, so syndication cannot inflate the count.

*Is it a COMPANY RAISE at all?* Outlet count cannot answer this, and the corpus
proves it: Kingswood's $4bn fund close was carried by businesswire.com AND
citybiz.co at the same $4bn. Corroboration alone would have auto-published a
private equity fund close as a company round. So `NOT_A_COMPANY_ROUND` is a
separate, independent condition over the row's own headline and summary: assets
/ AUM, `\bfunds?\b` but never "funding", IPO, capex and "injects". Measured
against all seventeen ledger rows: it vetoes A16z, Blackstone, Kingswood x2,
Arch, ASE and Turkish Airlines, and leaves xAI's "Series E funding round" and
Databricks' "in latest funding" clean.

It **never creates a finding of its own** and quarantines nothing. It only
withholds the shortcut. A new queue with nobody on it is the defect being
removed here, not one to add.

**The durable half, and it is the part that matters.** New
`AMOUNT_REVIEW_DEADLINE_HOURS = 48`, with one definition in `pipeline/guardrails`
read by both surfaces a human actually looks at. `ops_status.py [2d]` now exits
2 on any `amount` finding older than that and prints EVERY one with its dollars,
its age and its ledger key, never truncated: "and 9 more" is not a fact anybody
acts on. `health_digest.py` mails the same full list and names the withheld
dollars in the subject line.

Deliberately separate from `LIVE_FINDING_GRACE_HOURS` / `HELD_FINDING_GRACE_HOURS`,
which are unchanged. Those govern whether a DATA JOB goes red and are long on
purpose; this project has the permanently-red drain-writers to show what a
routinely red run teaches people. This governs the tool the session ritual runs
first, read by somebody already sitting down to work. 48 hours is four collect
runs, so a finding that old has been offered to a person four times.

**The fifteen, adjudicated individually.** Accepted and now publishing: xAI
$20bn, Waymo $16bn, DeepSeek $7.4bn, Databricks $5bn, plus Anthropic $30bn /
$13bn / $65bn and OpenAI $122bn already accepted earlier the same day. Rejected
and retracted locally (all were unpublished, so nothing was owed to the site):
Arch $539bn (AUM), Turkish Airlines $100bn (lira capex, ~43x), A16z $15bn (fund
close), ASE $10.5bn (capex budget), Blackstone $6.3bn (fund close), Corgi $4bn
(the $4bn is the valuation; the raise is undisclosed), Kingswood $4bn twice (one
fund close stored under two company_keys). OpenAI $100bn stays rejected: "closes
in on" is a round that had not closed.

The amount queue is now empty. Published funding total goes from $214.9bn to a
projected $493.3bn once the next publish run sends the eight released rows.

**One live figure is NOT resolved and is called out rather than quietly fixed.**
ChangXin Memory $8.6bn was accepted on 2026-07-29 and is on the site. Its source
says "$8.6 billion IPO" and "Shanghai STAR Market IPO", so it is the IPO error
class, and `pipeline/guardrails.py` was citing it in its own module docstring as
the canonical example of review working correctly. The docstring is corrected.
The row is left alone because a local retraction would take it off our copy
while leaving it on the page and remove it from every ops surface that would
otherwise nag. It needs a credentialed `python3 retract.py <signal_id>`.

**The live wrong number that was fixed.** Row 25799, published 2026-08-02:
`funding_amount` = "93.175 millones", printed verbatim, in bold, followed by
"raised", on the OpenAI profile and on place pages. In Spanish that dot is a
thousands separator; an English reader reads ninety three point one seven five.
The page showed one number and asserted another.

The parser was RIGHT to refuse it. `vocab._USD_MARKER` requires a dollar to be
stated and that string names no currency, so `funding_amount_usd` is correctly
NULL. That veto is load-bearing and was not touched. What was wrong was the
rendering. New `tit_amount_names_a_currency()` and `tit_amount_raised_html()` in
shortcodes.php, one definition for both pages: print OUR parsed dollars when we
have them, the source's own words when they name a currency at all, and NOTHING
when they name none. Measured over the 243 distinct live unparsed strings: 222
still print, 21 are silenced, and all 21 are a bare number plus a scale word
("5.300 millones", "300 miljoen", "93.175 millones"). Note the ISO codes are
matched with a lookahead and not a trailing `\b`, because they are written glued
to the number ("EUR10 milioni", "Rp2,35 Triliun", "RM540mil") - the same
boundary trap vocab.py records under the Turkish `mil`/`milyon` loss.

**Tests.** `tests/test_funding_corroboration.py`, 17 cases, asserting through
`publish.publish` so the batch assembly is exercised and not just
`check_amounts`. Three of them fail against the old rule and pass against the
new one, verified by reverting only `check_amounts` and re-running. Suite: 3,243
to 3,260, all green.

## 2026-08-04 - the LANDMARK GUARD: twenty named events, weekly, so a missing round is never again found by a human looking

The recovery recorded further down this day was correct and it was also the
wrong shape of win. Three enormous rounds were absent for months, a person
noticed, and an agent fixed them. Nothing in the system had an opinion about any of it while it was
happening, and nothing would have an opinion the next time. So this is the
guard, and it is deliberately not another pipeline fix.

**What it is.** `data/landmarks.json` holds 20 events across 7 quarters
(2025Q1 to 2026Q3): the largest disclosed funding round per quarter, each with
company, date, amount and **the company's own announcement URL**. Assembled by
public web research on 2026-08-04, no commercial funding database consulted and
none permitted (asserted in the tests, because those sites are the easiest
place to find the list and that is exactly the temptation). It is small and
hand-curatable on purpose: a new quarter is an edit to a JSON file.

**What it asks.** `check_landmarks.py` reports HELD / WRONG_AMOUNT / MISSING
per entry, through **two lenses that are allowed to disagree**:

- `stored`, the committed corpus, offline, recomputed by `ops_status.py [3d]`
  on every session start so it is never a week stale;
- `live`, the public `/query` endpoint, which is what a reader actually sees.

The two lenses are the whole design and not a nicety. On 2026-08-01 Anthropic's
$30bn round was IN the database, correctly extracted, and invisible to every
reader for five days behind an unanswered publish guardrail. A guard that only
asked the database would have reported that round held. `held_not_live` is
therefore a first-class outcome, and the first run found two more of them.

**What reddens, and what deliberately does not.** Only a REGRESSION: an entry a
previous report recorded as held that is not held now. A landmark that has
never been held is a STANDING GAP, printed every week and never red. A
permanent red on a backlog that only backfilling can move is a red that trains
the next session to stop reading exit codes, which is the same reasoning
`[3c]` already applies to the young-corpus finding. The live lens failing is
neither: an outage is UNKNOWN, and a guard that converts somebody else's seven
bad minutes into a red run and an alert email is a failure this repository has
already paid for twice.

**THE FIRST RUN, which is the number worth writing down: 4 of 20 held live,
6 of 20 held in the database, 0 regressions.** By quarter, live: 2025Q1 0/4,
2025Q2 0/2, 2025Q3 1/3, 2025Q4 0/3, 2026Q1 2/4, 2026Q2 1/2, 2026Q3 0/2. The
2025 gaps are the young corpus and agree with the rejection audit's
`outside_our_history` bucket, so they are a backfill list. The ones that are
not excusable are these:

- **xAI $20bn (2026-01-06) and Waymo $16bn (2026-02-02): stored and NOT live.**
  Both are held, correctly, and no reader can see either. Both sit in the
  publish guardrail queue described below. That queue is now the single
  largest cause of landmark invisibility, measured rather than argued.
- **Anduril $5bn (2026-05-13), Helsing $1.8bn (2026-07-13) and Fireworks
  $1.505bn (2026-07-16): never collected at all**, and the last two are three
  weeks old. Those are not history, they are the live pipeline missing rounds
  it should be catching this month.

**Wiring, because a number computed weekly and surfaced nowhere is the fourth
way a guard dies.** `ops_status.py [3d]` prints it at session start and adds an
ACTION NEEDED item for a regression or a stale report. `health_digest.py`
carries one line every week whether or not it is bad - `landmarks: 4 of 20
held, 16 standing gaps, 0 regressions, 2 stored but not live` - for the same
reason SOURCE LINKS is reported every week: a metric that only appears once it
is already bad cannot show a slow slide. A regression sets the email subject
and is its own `needs_human` trigger. `.github/workflows/landmarks.yml` runs
Mondays 09:00 UTC, between `recall` at 08:00 and the digest at 13:00, so the
order each Monday is measure the corpus, check the landmarks, mail one line.

**Costs nothing and cannot cost anything.** No model is imported on the path.
The live lens is fourteen public GETs against our own site.

**Not a recall measurement, and the file says so in its own method block.**
`analysis/recall/` answers "how much of the world do we hold" against a sealed
set with a required geographic shape. This answers "are the events nobody could
defend missing actually here". It is top-of-distribution and US/Europe heavy
because that is where the largest disclosed rounds were, so quoting a
percentage from it as coverage would be flattery.

**Not a database writer.** It opens the corpus read-only and commits one
snapshot file with no row identity to merge on, which is why it is not in the
`talent-collect` lock and why `drain-writers` does not apply. A test pins that:
if it ever writes a signal, both of those change.

**The sibling needs the same thing** and it is out of scope here: the largest
layoff EVENTS per quarter, with company, date, headcount and the primary
document (the 8-K, the WARN notice, or the employer's own statement). Recorded
in `data/landmarks.json` under `sibling_note` so the idea does not die with the
session that had it. It is a port of the shape, never an import of the module:
the two trackers share no code and no database.

**Files:** `data/landmarks.json`, `data/landmarks_report.json` (committed
history - the regression detector has no memory without it),
`analysis/landmarks/{landmarks,check}.py`, `check_landmarks.py`,
`.github/workflows/landmarks.yml`, `tests/test_landmarks.py` (41 tests).
`tests/test_health_digest.py` gained `analysis` to ops_status's allowed import
set, which `test_landmarks.py` earns by pinning the package stdlib-only.
---

## 2026-08-04 - the thirteen publishers the rejection audit named: eight wired, five refused in writing

`tests/test_audit_publishers.py` had been red on main for six commits. It names
the publishers in the audit's two ACTIONABLE buckets and demands, per publisher,
either a feed we fetched or a dated refusal carrying what was tried. Thirteen
domains had neither.

Every one was probed through the collector's own `fetch`, with its own
User-Agent, honouring robots.txt: `/feed`, `/feed/`, `/rss`, `/rss/`,
`/rss.xml`, `/atom.xml`, `/feed/rss`, `/index.xml`, `/feeds/posts/default`,
`/sitemap.xml`, plus every `rel="alternate"` link in the homepage head, plus
publisher-specific guesses where those failed.

**Eight wired (10 feed rows), all fetched and counted at the moment of wiring:**
Aviacionline `/rss` (20 items), Ziarul BURSA `titluri-bursa.xml` (23) and
`piata-capital-bursa.xml` (2), El Ecosistema Startup `/feed/` (10), Liputan6
`feed.liputan6.com/rss/bisnis` and `/rss/news` (25 each), Mining Weekly
`/page/international-home/feed` (8), Parkiet `/rss_main` (20), Startups Latam
`/feed/` (10), Youngster.id `/feed/` (10).

Three of those would never have been found by path probing. Parkiet and
Liputan6 declare feeds only in the homepage head (Liputan6's live on a separate
`feed.` host), and Mining Weekly's is linked from the publisher's own
`/page/rss-feed` page under a path that looks nothing like a feed. Parkiet's
wire carries ESPI current reports, which is the exact filing route the gold miss
came through.

**Five refused, with the probe list and status codes in `notes`:**
Commersant.ge and ShareSansar publish no feed at all (every path 404; ShareSansar
serves its full site template on a 404, so none of them is a soft redirect
hiding one). Commercial Times serves an object-store `/rss/` prefix where every
key 404s as `NoSuchKey`. Muscat Daily is WordPress with feeds switched OFF: five
paths and all seven menu-linked category feeds answer 200 and redirect to HTML.
Renewable Carbon News is an **outage** verdict and says so: every path including
the homepage returned 500 with `Error establishing a database connection`, so a
later session should recheck rather than conclude there is nothing there.

The distinction matters more than the count. "No feed" is finished work; "the
site was down when I looked" is not, and a note that blurs the two is how the
same fifteen paths get probed a third time.

Nothing was weakened to close this: the test is unchanged, no name was dropped
from the audit, and the two refusals that could have been quietly wired (a news
sitemap for Commersant.ge, an HTML parse for ShareSansar) are written up as
`NEEDS HTML PARSING` instead of pretending a sitemap is a feed.

---

## 2026-08-04 - the three biggest private rounds of 2026, recovered from the primary document (1.68.0, pushed, NOT deployed)

The owner measured a recall failure that outranks every open item: OpenAI held
eight rows and none of them was the March 2026 close; Anthropic held one row and
neither the February round nor the May Series H was in it. The diagnosis found
the same shape three times over: **every one of those rounds was discovered, in
several languages, and then died downstream** - the Yahoo copies host-blocked as
aggregators, the Hebrew and Spanish copies lost to an eight-hour gate outage on
2026-08-03, the German ones deferred unread when the month's allowance ran out.

Two things came out of fixing it, and the second is the larger one.

### 1. Nothing had ever read the announcement

Every copy we ever saw was a rewrite. `anthropic.com/news` and `openai.com` are
in nobody's feed list, so the document the rewrites were rewriting had never
been fetched by anything in this pipeline. `collectors/tripwire_chase.py` chases
a lead to *a publisher's* article; `collectors/primary_chase.py` chases it one
rung further, to *the primary document*.

Same discipline, stated in the module: the work list carries a URL and nothing
else that reaches the database. No amount, no date, no company, no headline.
Every field is read out of the document, and the item then goes through the
identical `prefilter -> precheck -> extract -> validate -> dedupe -> store`
path with every guard that implies. A work list that names a round the document
does not state stores nothing.

It cost **$0.00**, and that is not luck. A newsroom headline is written to state
the round - "Anthropic raises $65B in Series H funding at $965B post-money
valuation" IS the record - so `pipeline/cheap_extract.py` closed all four leads
deterministically and no model was called at all. The run was made with
`TIT_PAID_READS=off` so that claim is enforced and not merely observed.

    found=4  stored=2  duplicate=2  rejected=0  deferred=0
    deterministic: 4 closed with no model call

Stored: Anthropic $65,000,000,000 (Series H, 2026-05-28, anthropic.com) and
OpenAI $122,000,000,000 (2026-03-31, openai.com). Both figures match the
employer's own wording exactly. The other two leads were the February Series G
and the September 2025 Series F, which dedup correctly recognised as rounds we
already hold - that is the guard working, and it is why they were in the list.

`openai.com` answers 403 to every non-interactive client, so that lead carries
`archive_fallback` and the collector reads the Internet Archive's copy of the
same URL. The **cited** source stays the publisher's own permalink, because the
archive served the document and did not publish it; the archived copy is
recorded as `discovery_url`. This is the relationship `archive_sources.py`
already maintains from the other direction.

### 2. The rounds were not missing from the DATABASE. They were missing from the SITE

Anthropic's $30bn was in the database, correct and complete, and had never been
published - along with $874.2bn of other rows. `pipeline/guardrails.py` derives
its amount ceiling from the corpus's own log-normal fit, currently
**$6,229,521,923**, and the corpus median raise is ~$8M. In the AI mega-round
era that means "genuinely enormous" and "parse error" are the same signal to it,
so the check quarantines the correct answers at the same rate as the wrong ones,
and the rows it silently withholds are exactly the ones a funding tracker exists
to hold. Every one of the 15 unpublished rows in a 28,625-row database was a
mega-raise. Not one had ever been reviewed; the oldest had been re-seen 229
times over five days.

**The guard was not wrong. `open` had become a terminal state because nothing
drained it.** Four findings were answered against the employer's own
announcement, which is the evidence that distinguishes a real mega-round from a
mis-parse:

    accepted  Anthropic  $65,000,000,000   anthropic.com/news/series-h
    accepted  OpenAI    $122,000,000,000   openai.com/index/accelerating-the-next-phase-ai/
    accepted  Anthropic  $30,000,000,000   the Series G announcement corroborates the stored row
    accepted  Anthropic  $13,000,000,000   the Series F announcement corroborates the stored row

And one was answered the other way, which is the reason blanket-accepting the
queue would have been wrong:

    rejected  OpenAI    $100,000,000,000   "closes in on" / "is nearing" (CNBC, 9 Feb)

That is the SAME round, reported in progress seven weeks before it closed at
$122bn. Publishing it would have put two different sizes for one round on the
page.

**And rejecting it is not enough, which is worth writing down because it is a
trap.** `rejected` releases the row from quarantine exactly as `accepted` does -
the state means "a human has answered this", not "hold it forever" - so the
rejection alone would have PUBLISHED the $100bn figure on the next run. That is
what `guardrails.py`'s own docstring means by "rejecting one does not delete
anything: retract the row". Caught in a local `publish --dry-run` before the
run went out, and closed with `retract.retract_local()`: the row is
`is_current = 0` with the reason on it, and the remote half of a retract is a
genuine no-op here because `published_at` was NULL and the figure never reached
a reader. **Never reject an amount finding without deciding, in the same breath,
what happens to the row.**

The queue still holds eight open findings and at least one of them is plainly
wrong - Turkish Airlines' "100 billion lira ($2.3 billion)" parsed as $100bn -
which is the evidence that this queue must be answered row by row and never in
bulk.

**The standing defect this leaves.** A review queue with no human on it is a
check silently converted into a delete, and nothing here fixed that. The two
follow-ups, in order: make `ops_status.py` exit 2 on any amount finding open
past 48h, naming the row and the figure; and let a figure corroborated by two
independent sources at the same amount auto-accept, leaving human review for
singly-sourced outliers. Do NOT simply raise the threshold - that publishes
Arch's $539bn, which is a private-market ASSETS figure read as a raise.

### The source page, same session

`primary_chase` stored rows, so it is a live source and is named as one:
"Employer newsrooms (announcements read at the source)". It is listed rather
than excused as a chase (unlike `tripwire_chase` and `benchmark_chase`) because
those cite somebody else's article and this one cites the document it read. Its
honest limit is on the page: the URL list is assembled BY HAND from what a
recall measurement says is missing, so this source discovers nothing on its own,
and it must never be scheduled - a standing list of URLs re-fetched twice a day
is a list of documents that have already been read.

---

## 2026-08-03 - four defects a live UX audit found, and three of them were the page saying something it could not support (1.69.0, pushed, NOT deployed)

Nothing here was a broken query or a wrong sum. The markup was valid, the
numbers were correctly computed, and the page was wrong anyway, which is the
class of defect the render tests cannot see because they check that a thing is
printed and not whether it is true.

**1. The sticky site header was transparent, and it ate every tap.** At 375px
the site's own mobile rule is `header.wp-block-template-part{position:sticky;
top:0;z-index:999}` and the background meant to go with it is written for
`header .wp-block-group.alignfull`. The header group on this site carries
`atr-header` and never `alignfull`, so that selector matched nothing: a bar
pinned over the page with nothing painted into it. Page text printed letter on
letter under the wordmark, and because a sticky bar is a hit target whether or
not you can see it, `document.elementFromPoint` anywhere in the top 64px
returned the header rather than the page.

Two fixes, because a background alone fixes only half of it: an opaque
background and a hairline ON THE STICKY ELEMENT ITSELF (painting a descendant is
the original mistake, and it does not paint the parent's box), plus
`position:static` below the same breakpoint, which is what stops the
interception. Both scoped with `:has()` to the surfaces this stylesheet loads
on. The offending CSS is NOT in this repo; it is site-level custom CSS, so this
is an override on the tracker pages and the rest of the site still has the
transparent bar. Naming that limit rather than implying the site is fixed.

**2. A retraction that was never once seen.** The place card's one-collector
caveat ("read that bar as filing volume") was passed into `tit_chart_head()` as
its `note_html`, which puts it inside `.tit-chart-note`. `dashboard.js` closes
every one of those panels on load, so the element computed `display:none` and
measured 0x0 on every browser that ran the script. It is now prose on the card,
above the ranking, where its own base rule's `margin:0 0 9px` always assumed it
was. Same id, so the JS still rewrites and re-hides it under the filters; it is
now hidden only when it is not true.

**3. The trend chart certified its own collection rate as market movement.** The
basis sentence counted distinct collector names out of `tit_signal_trend()`'s
scan, which groups by `COALESCE(published_date, DATE(captured_at))`. So a
collector switched on last week that ingests back-dated articles was counted as
having fed the START of the window: the one confound the measurement existed to
detect was the one shape of it the measurement was blind to. It then concluded
"so the movement here is not a change in how many sources we read", which reads
as a certificate the evidence could not issue.

Rebuilt as `tit_trend_ingest_breadth()`, bucketed by `DATE(captured_at)`, which
is when we wrote the row down. It compares SETS rather than two counts, because
one collector stopping while another starts leaves the count untouched and is
still a change in what we read. Four branches now, and none of them certifies:
no ingest at all in the opening week (the left end is backfill), the set moved,
the set moved without the count moving, and the same set at both ends, which
says only that the COUNT of sources held and that this is not a measure of how
much each one returns. `TIT_DASH_QUERY_BUDGET` 14 -> 15, itemised at the
constant: it needs a different GROUP BY and a different GROUP BY is a different
scan.

**4. Six of nine chart titles did not name what they showed.** "Where the Jobs
Are" sat over a ranking of record counts, so a bar reading "United Kingdom
7,955" told a reader there were 7,955 jobs there. A title is the part of a chart
that travels: a share link, a screenshot and a headline all carry it. Every card
now names its dimension and its unit, and the reader-facing voice moved up to
the four section headings, which is where the questions belong.

| id | before | after |
|---|---|---|
| place | Where the Jobs Are | Updates by Country |
| trend | Updates a Day | Updates Collected a Day |
| kind | What Is Moving | Updates by Kind of Move |
| direction | Which Way Headcount Is Going | Updates by Stated Headcount Direction |
| confidence | How Solid the Evidence Is | Updates by Strength of Evidence |
| industry | Which Industries Are Moving | Updates by Industry |
| money-country | Money Raised by Country | unchanged |
| money-city | Where the Money Went | Money Raised by City |
| money-industry | Money Raised by Industry | unchanged |

`money-city` is the rename `docs/HANDOVER.md` already recorded as correct and
which was not in the tree; this is it landing.

**The guard: `tests/test_chart_titles_and_basis.py`, seven assertions, all seven
proven red against the pre-fix tree.** That last part is the reason one of them
exists in its current form: the assertion keeping the certifying sentence out
was written as a plain literal, and the sentence it rejects wrapped across two
source lines, so it PASSED against the very tree it was written to reject. It
collapses whitespace first now. A guard nobody has run against the defect is a
guard nobody should trust.

Byte budget not raised: 180,482 -> 180,678, itemised at the constant, headroom
322. The fixture still cannot price the trend panel (its rows sit within days of
the render date, so every signal fails the continuity gate), so the four basis
branches were proven with a throwaway harness instead and the drawn panel's
cost is an estimate, marked as one.

**Not touched, and deliberately:** the $228B money total and the funding
classification. Another workstream owns it.

---

## 2026-08-03 - the owner-approved batch of four: RSS per view, watchlist, card/table toggle, CRM export presets

One version bump, four reader-facing features, no data change and no model call.

1. **RSS per filtered view.** `GET talent/v1/feed` (includes/feed.php) takes
   the SAME filter params as /query (it calls tit_build_where, so the two can
   never drift) and returns RSS 2.0 of the newest 50 matching signals: title =
   headline, link = the SOURCE document, guid = signal_id
   (isPermaLink="false"), pubDate RFC 822, category = pillar. Same transient
   discipline as /query (tit_cache_key + TIT_CACHE_TTL, wiped by
   tit_flush_caches), CDN Cache-Control, and a 60-builds-per-10-min-per-IP cap
   counted only on cache MISSES so polling a cached feed can never 429. Raw
   XML leaves through a rest_pre_serve_request filter keyed on the
   Content-Type. An RSS link joined the export strip and rides the same href
   updater as every download; the unfiltered feed is announced with
   <link rel="alternate"> on the dashboard page. Proven well-formed by TWO
   independent strict parsers (DOMDocument in tests/php/feed_and_crm.php,
   ElementTree + email.utils in tests/test_feed_and_crm.py) over rows carrying
   ampersands, control characters and a missing published_date.
2. **Watchlist without accounts.** localStorage only: a star on every card's
   employer (INJECTED after paint so the shared card contract's two renderers
   are untouched), a Watchlist (N) chip beside the quick views that narrows
   client-side, and an "M new" badge from the previous visit's timestamp
   against card dates. /query's `company` is a single LIKE and takes no comma
   list, so the chip filters the loaded rows in the browser and the (i) panel
   says exactly that (sequential fetch-merge was considered and rejected).
   With localStorage unavailable the whole surface stays hidden.
3. **Card / table view toggle.** A compact sortable table as a SIBLING
   rendering of the same /query rows (date, employer, signal, direction,
   amount, country, evidence, source). Card markup untouched, contract tests
   still green. Headers write the same `sort` parameter as the select
   (aria-sort carried), so a click orders the whole filtered set server-side.
   The table scrolls inside its own container (.tit-updates-scroll, NOT
   .tit-table-scroll whose sub-860px rules stack rows into cards) and the page
   never bleeds at 375px, measured in a real browser. Choice persists as
   tit_view.
4. **CRM export presets.** "CSV for HubSpot" and "CSV for Salesforce"
   (includes/export_crm.php) stream the SAME filtered set through
   tit_export_walk with headers each import wizard maps by name; vendor docs
   cited in the file. The website/domain column ships EMPTY on every row: we
   hold no company websites and the publisher's domain in a CRM dedupe key
   would be invented data. Exact header rows pinned by the harness.

Byte budget raised 178,000 -> 180,000 in tests/php/render_dashboard.php, in
writing, for the chip, the toggle, the empty table container and three export
links. company_key joined /query's column list for the watchlist's benefit.
New harness tests/php/feed_and_crm.php runs in CI beside the other seven.
## 2026-08-03 — this-year/all-time pairing + Year/Quarter/Month selects (1.67.0, pushed, NOT deployed)

Two owner asks, one version. Supersedes pending task #67 (the entire-record
sublabel): the dual number ships INSTEAD of a sublabel.

**1. Every whole-record figure now says which span it covers.** The freshness
panel's four stats each lead with the current year big ("3,639 updates in
2026") and keep the entire record small beneath it ("25,397 all time"); the
year is derived from the clock at every layer (current_time in PHP, the
Date object in JS, TIT_FIXTURE_NOW in the harness), so the labels say 2027 in
January without an edit. Server side, the current-year slice rides the SAME
one-pass head scan in `tit_dashboard_facts()` as four more CASE expressions —
no extra query. Repaints keep the pairing: `refreshAggregate()` adds a second
/aggregate call under the same filters plus `since=Jan-1&include=fresh`, and
`include=fresh` is a new closed-vocabulary response shape on /aggregate that
returns only total/companies/countries/verified/money (the param joined the
cache-key whitelist, or slim and full responses would share an entry). The
pairing DROPS whenever the reader sets a date window of their own, because
"all time" under a date filter is a false label; the single filtered figure
returns. The dollar stat only pairs when the year actually holds a stated sum,
so a thin January never prints $0 over a real total. The hero "Search N
updates" button was verified unchanged: it carries the current-view count.

**2. Year / Quarter / Month selects, wired as SHORTHAND, not as state.** They
live inside the Date Range dropdown ABOVE the From/To boxes and WRITE the
window into them visibly (Year 2026 + Q3 fills 2026-07-01 / 2026-09-30), so
the querystring, chips, exports, Reset All and the signal board's cells keep
the one since/until source of truth, unchanged. `syncPeriodSelects()` is the
reverse read on every refresh: dates that exactly span a year, quarter or
month light the selects (that is what round-trips a shared URL — verified:
?since=2026-07-01&until=2026-09-30 reloads as Year 2026 + Quarter Q3), any
other hand-edited window blanks them. Chips name the period ("Year: 2026",
"Quarter: Q3") instead of two raw dates; removing the quarter/month chip
widens back to its year, removing the year chip clears the window. The year
list is DERIVED from the data's own date bounds, never typed, and the harness
asserts the year after the data's max is NOT an option. Month and quarter
never AND: picking one blanks the other.

Rendered at 375px and 1280px before push (the browser pane would not paint
scrolled content, so the fixed-overlay clone inside the plugin wrapper did the
seeing; panel geometry re-measured on the REAL open dropdown both widths: 349px
wide inside a 375px viewport, 265px on desktop, zero horizontal document
overflow). Full suite 3,053 passed with only the three pre-existing
test_audit_publishers reds (they fail identically on clean origin/main); all 7
PHP harnesses green. render_dashboard byte budget NOT raised: 175,187 ->
177,535 of 178,000, itemised in the harness. Card contract, wayback notes and
the signal board untouched. Version 1.66.3 -> 1.67.0, pushed to main, NOT
deployed.

---

## 2026-08-03 — the "feeds deliver, rows do not appear" pass: Spanish, Polish and Greek widened at the free gate, measured both sides

The private benchmark's diagnosis section named four causes for the thin wired
markets (Argentina 7 feeds / 138 items a run / 3 rows ever; Mexico 6/130/5;
Poland 4/85/13; Greece 4/109/0). This session verified each against the
ledgers and closed the free ones that were still open.

**What was already closed before this session, so nobody re-fixes it:** the
23-language pack (2026-08-01) covers Greek and Norwegian — the benchmark's
"no Greek pack" line predates it — and `google_news.edition_dateline` (also
2026-08-01) closed the no-country-context cause the same file names. What
remains open and is NOT code: the press collector losing runs to the
`talent-collect` lock (an eviction/scheduling question) and the read-cap /
key-lifetime arithmetic (an owner question).

**Measured, live, through the collector's own parse** (35 wired feeds for
AR/MX/PL/GR plus Germany as the covered-language control, 717 items,
2026-08-03; corpus + harness in the session scratchpad, not committed):

| market | items | pass before | pass after | every new pass hand-read |
|---|---:|---:|---:|---|
| Argentina | 137 | 11.7% | 12.4% | a fund putting $10m into startups |
| Mexico | 130 | 9.2% | 10.0% | the MSD leadership appointment |
| Poland | 85 | 7.1% | 12.9% | market entry, portfolio join, factory story, fund teaser, board seat |
| Greece | 100 | 0.0% | 0.0% | (all 100 items were fires/politics/sport — the pack works, tested) |
| Germany (control) | 265 | 8.3% | 8.3% | control did not move |

Seven newly passing items, all seven genuine signals or funding teasers; zero
movement on the control, so the widening bought recall without precision drift.

**What the widening actually was** (`pipeline/prefilter.py`):

* **Spanish knew Spain's register and not Latin America's.** "ronda de
  financiación" was the only funding phrase; LatAm copy writes
  "financiamiento", and the verbs it actually leads with — levantar, recaudar,
  cerrar una ronda — were absent, each now anchored to an amount or a round
  (bare "levanta" is a crane, bare "recauda" is a tax office). Leadership
  gained "asume la dirección / asume como", "nombramiento", "designado como",
  anchored "renuncia a/al/como"; employment gained "vacantes", "sueldos".
* **Polish was nominative-only and verbless.** "runda finansowania" is now
  stemmed ("rundę/rundzie…"), and the real newsroom verbs joined, anchored:
  "pozyskał … mln/finansowania", "zebrał … mln" (a crowd also gathers),
  "dołącza do portfolio", "powołała … na stanowisko", "obejmuje stanowisko",
  "odchodzi z firmy", "rekrutacja", "pensje", "podwyżki płac" — anchored to
  pay because Bankier's front page is price rises. Site vocabulary gained
  "fabryk\*/siedzib\*" and the open/close verbs (otwiera, zamknęli…).
* **Greek gained the singular** ("πρόσληψη" has a different accented stem than
  "προσλήψεις") and the funding verb "άντλησε", anchored to an amount because
  the fire brigade draws water with the same verb.
* **The boundary got a guard the new Polish site verbs made necessary:**
  "Zamknęli fabrykę, 200 pracowników zwolnionych" must go to the sibling, and
  "zwolnieni\w+" reached the noun but not the participle. The participle is
  now anchored to the people, both ways round — bare "zwolni\w+" slows down,
  releases and exempts.

**Verified NOT broken, so nobody chases them again:** `candidate_rank` buckets
national_press items by `source_country` correctly (Argentina→AR, 11 rows =
thin +3.0; Greece→GR, 0 rows = empty +6.0; the round robin gives each market a
slot); and seen-churn is the designed terminal-verdict behaviour
(national_press ledger: 2,487 rejected / 361 stored; deferrals stay unmarked).

Tests: 3,022 passing (was 3,004): live-observed AR/MX/PL headlines, real
historical rounds in both registers, anchor-holding noise cases, and the
factory-closure boundary case.
---

## 2026-08-03 - the classifier gate built as a SELF-ARMING system (plan step 2; ships UNARMED, no human step ever needed)

The $5 blocker is the paid gate ($5.70/month just to LOOK), and the plan's
answer is a local classifier in front of it. Built end to end today; nothing
routes yet, and nothing needs a human to make it start.

**Runtime** (`pipeline/gate_classifier.py`, standard library only): logistic
weights over hashed char 3-5-grams + word unigrams (CRC32, 2^18 buckets; the
featurizer is ONE function imported by trainer and runtime, so the bytes that
train are the bytes that serve). Three-way routing in `classify.classify()`:
confident-RELEVANT skips the LLM gate straight to extraction, UNCERTAIN pays
the LLM gate exactly as today, confident-IRRELEVANT drops. Fail-open
EVERYWHERE: artifact missing/corrupt/truncated, flag unarmed, replay report
missing/stale/under-bar/about-different-weights, a language the training set
never saw (under 25 real labels), an empty headline, any exception - all
route UNCERTAIN, i.e. yesterday's behaviour. There is an off switch
(`TIT_GATE_CLASSIFIER=off`) and deliberately NO on switch a human or env var
can force past a failed replay.

**Trainer** (`train_gate_classifier.py` + weekly `gate-classifier.yml`,
Tuesdays 05:15 UTC): trains from `data/gate_labels/` (weak bootstrap rides
along at 0.25 weight; `clf_reject` lines are excluded so the classifier never
grades its own homework), replays OUT OF SAMPLE (chronological day blocks,
each scored by a model that never saw its days, thresholds chosen on each
fold's own held-out tail), and self-arms ONLY when the shipping bar passes:
**>=99.5% of stored-row candidates routed relevant-or-uncertain over >=30
days of real labels**. Under the bar or under 30 days it prints
`not ready: N labels, D days, replay X%` and changes NOTHING. Passing commits
the artifact (`data/gate_classifier/model.json.gz`, ~1MB gz) + the flag
(`status.json`) and emails ONE arming notice via the keyed /alert; a later
retrain that fails the bar REVERTS the flag and emails once, deduped by
cause. Drift alarm: armed + uncertain share >35% over 7 days -> one deduped
alert, RECOVERED under 25%. scikit-learn is installed by the workflow alone
and stays out of requirements.txt; no collector runtime gained a dependency;
the workflow never touches the signals database so it takes no writer lock
and commits only its own snapshot files, recall.yml-style.

**Thresholds are recall-first by construction**: the drop threshold sits 20%
below the worst held-out stored score and never above 0.40; the skip band
opens only where the LLM gate agreed >=95% on >=20 rows (a skip trades a
cheap gate call for an expensive extraction, so an unproven skip saves
nothing and stays shut). **And the skip band is earned PER LANGUAGE**: the
ledger read behind the es/pl/el prefilter fix showed Polish passing the paid
gate at 17.7% against Spanish's 80.1%, with the Polish passes skewed to
football-club/municipal noise - a globally calibrated skip band would buy
extractions for exactly that. So a language needs >=200 real labels AND its
own held-out band agreement to enter `relevant_langs`; below that its high
scorers stay UNCERTAIN and keep paying the cheap gate. Confident routing of
ANY kind additionally needs >=25 labels and >=5 stored rows in the language
(a base that is all junk earns no drop band either). The 99.5% stored-row
replay bar stays GLOBAL on top of all of it. The test that matters most
(`tests/test_gate_classifier.py`): a synthetic classifier blind to a
vocabulary shift that fills the final replay block FAILS the bar - the drop
threshold learned on the old world drops the new world's stored rows and the
replay says so. Also proven: every fail-open, the flag-flip refusals, the
three-way routing in classify (confident drop makes zero paid calls), drift
dedupe, and the per-language skip roster. Suite: 3,023 on main before this, 3,057 after.

**Measured today on the real ledger** (run, not estimated): 6,900 real labels
over 3 days, 4,328 weak; first weekly run will print
`not ready: 6900 labels, 3 days, replay 97.82%`. At ~2,300 labels/day the 30-day
bar is met ~2026-08-31, so the earliest arming is the Tuesday after:
**2026-09-01**, with ~69k labels behind it. Confident-band coverage at 3 days
of data is 20.0% (34.5% before the per-language skip roster); the plan's 80%
hope gets measured at arming time, not assumed.

---

## 2026-08-02 - the benchmark-diff loop ported from the sibling (DORMANT, weekly, secrets-only, names never in a log)

The layoff tracker's tracker-diff tripwire, ported: an external reference list
of employers, supplied ONLY through the `BENCHMARK_FEED_URLS` (JSON or CSV
feeds) and `BENCHMARK_COMPANIES` (inline names) secrets, is diffed against our
stored employers and the gap is chased to each employer's OWN press coverage
and 8-K filings through the ordinary `run_collect` path. The reference is a
discovery pointer, exactly as a model or an aggregator is: it is never cited,
never stored, never named, and the stored source is always the publisher or
the registry.

What shipped, and the property each piece carries:

- `run_benchmark_diff.py` - entry point. With neither secret set it prints ONE
  line and exits 0: no database read, no network, no spend, no commit. The
  repo carries zero benchmark data; the owner arming a secret is the only
  activation, and nobody asks the owner to add one. Armed, it prints the
  RECALL line (held/listed as a percent, counts only), emails the missing
  names to the owner through the keyed `/alert` route ONLY when recall is
  below `BENCHMARK_RECALL_ALERT_PCT` (default 90), and chases a rotating
  slice (`BENCHMARK_DIFF_MAX`, default 40, cursored on the calendar date) so
  the whole backlog is walked across weeks.
- `collectors/benchmark_chase.py` - the chase, registered in
  `run_collect.SOURCES` so every guard applies once: prefilter, gate,
  precheck, both dedup layers, validate, store. Per lead: one targeted Google
  News query built from the NAME and nothing else, and one SEC full-text
  search kept only when the employer itself FILED the hit (a filing that
  merely mentions a name is not evidence about that name). The other
  structured registries are population-based feeds with no per-company query
  path, so their ordinary runs are the chase there.
- PRIVACY, stricter than the tripwire chase beside it: logs carry counts and
  slice indices only, never a name and never a feed URL. The collector's own
  narration is written that way; `run_collect`'s narration (REJECT/STORE
  lines carry headlines, and a chased headline names a list member) is
  captured and only the count-shaped lines re-emitted; and the gate-label
  ledger is switched off for the run (`TIT_GATE_LEDGER=off`) because labels
  are committed and a chased-but-unverified employer's headline is list
  membership in a public place. All three are pinned in
  `tests/test_benchmark_diff.py`.
- WRITER DISCIPLINE: `benchmark-diff.yml` sits in the `talent-collect` lock,
  carries no cron of its own, and is scheduled weekly (Tue 07:50 UTC) as a
  ticket from `schedule-link-hygiene.yml`, drained by `drain-writers.yml`
  like every other writer. Its commit step is the standard save / reset /
  `merge_db.py` / push ladder. `dry_run` defaults true on a bare dispatch.
- SPEND: `spend.py --degrade` runs first; `TIT_PAID_READS=off` defers every
  paid call; `TIT_READTHROUGH_CAP=25` bounds the run's reads explicitly
  (~$0.03 worst case at the measured $0.00128/read); the free diff, the free
  searches and both dedup layers run regardless. $0 while dormant, cents when
  armed.
- Sources page: `benchmark_chase` joins `_NOT_SOURCES` (with the reason) and
  `_DORMANT_COLLECTORS`, so the health row it files once armed never renders
  as a source of documents. Staleness leash 384h (weekly plus queue wait),
  inert while dormant because a dormant run files no health row.

Deliberately NOT ported from the sibling: the weaning machine (independent
recall, earned cadence, learn-from-wins outlet suggestions, vocab-miss
emails). This starts as the sibling's loop started - a plain tripwire - and
earns those parts when an armed month shows the dependence they measure.

Suite after the port: 3,004 offline tests passing, 25 of them new.

## 2026-08-02 — the owner's shared design adopted: signal board, editorial hero, freshness panel, coverage ribbon, ochre-for-money (1.66.0, pushed, NOT deployed)

The owner's design artifact ("Talent Intelligence Tracker.dc.html"), adopted per
the audience spec's ADDENDUM: the board and hero REPLACE prose, they do not add
to it. The owner's words: "So much text and so many areas - we need colored
narratives." Measured above-the-fold word counts (local render harness, fixture
data): desktop 1280x800 **333 -> 275**, mobile 375x812 **126 -> 124**. Byte
budget headroom widened too: 177,466 -> 175,187 against the 178,000 ceiling.

1. **THE SIGNAL BOARD replaces the dated strip's four text lines.** One
   container (`#tit-board`): head (derived date + "less [][][] more" heat
   legend + Copy as Post), the existing signal-by-period matrix, ONE footnote
   line (rows overlap; Total Raised points at `#tit-usd-note`). The old
   two-line lede + "Full notes" disclosure is gone; the strip's per-bucket
   employer/verified/largest-raise scan columns went with it
   (`tit_glance_matrix` slimmed, still one statement, still 14 queries cold).
   Copy as Post now reads the matrix rows (each cell's `.tit-cell-p` period is
   real text) plus the freshness figures and the chips, so it still can never
   hand somebody a figure the page is not showing. The week column header
   carries the derived "(Jul 28-Aug 3)" span the owner asked for on the strip,
   so week-exceeds-month still reads as the calendar fact it is. The
   week-over-week percentage and its young-corpus suppression went with the
   strip: the board prints counts, never a derived comparison.
2. **EDITORIAL HERO**: serif display thesis ("Know who's hiring before the job
   ad appears."), ONE subhead sentence carrying the trust claim, exactly two
   actions: "Search N updates" (focuses `#tit-f-company`, count repainted per
   view) and "How this is built" (`#tit-trust`).
3. **FRESHNESS PANEL top-right** absorbs the Live pill, Roo (whose next-run
   line drops its redundant relative half), the promise line, and the old
   "Everything We Hold" figures as four big stats (updates / employers /
   $ raised in ochre / official filings). `tit_fresh_stats_html()` +
   `freshStatsHtml()` are the mirrored pair; repaints per view like the fine
   print it replaces.
4. **COVERAGE RIBBON**: one `tit-hero-links` line under the hero — derived
   date span (`#tit-span`, period dropped from `tit_span_note`) + derived
   country count (`#tit-ribbon-c`, repainted) + Every source / What we miss,
   measured / Corrections / places directory / sibling link. The old
   employer-name sentence died; the places link survives (it is the crawl
   path to those routes).
5. **PALETTE**: ground goes warm paper (#faf9f6), ink goes navy (#1b2130),
   and OCHRE (#b07c00 / text #7a5800) becomes the money hue everywhere the
   violet was (matrix money row heat + figures, money cards, section tick).
   Heat cells are ONE muted blue (28,92,171) scaled within each row; the
   per-signal hues survive as the left-edge ticks (`--tick-rgb`) so a row is
   still decodable at a glance. Rule fixed in the tokens comment: money wins
   on figures, blue wins on controls (a selected money cell goes blue).

`/aggregate` no longer returns `glance.dated` (dashboard.js was its only
consumer); `datedHtml`, `syncDated`, `collapseMatrixNoteOnPhone` and the dgBox
handlers are deleted with their markup. Click contract intact: every cell still
round-trips filter+period through the same inputs/URL state, Reset All clears
it, and the harness now asserts the board's total-row cells against the
database per window. Verified in a real browser at 375px (stacked rows, zero
horizontal overflow) and 1280px. All seven PHP harnesses and the full offline
suite (2,974 + 202 subtests) green.

---

## 2026-08-02 — the audience-spec pass: zones, question headings, one caveat one home, and the trend joins the click contract (1.65.0, pushed, NOT deployed)

Implements the talent half of the evidence-based audience display spec (both
live pages fetched, both bundles audited; the spec lives with the sibling's
session notes). Six changes, all dashboard-only, verified against the local
render harness at 375px and desktop:

1. **The trend chart joins the click contract.** Updates a Day was the one
   chart whose elements looked tappable and did nothing. `tit_trend_svg()` now
   carries `data-start/end/n/avg` on `.tit-tc`, and a delegated handler in
   dashboard.js maps a tap's x position to a date and writes the avg-day
   window ending that day into the SAME since/until inputs the Date Range
   control uses, so chips, address bar, exports and every chart follow in one
   pass; a second tap clears it. The (i) names the Date Range control as the
   keyboard route, the same trade the sibling's canvas charts make. (The Top
   Cities buttons, the spec's other gap, were already wired in 1.64.0; the
   spec audited live 1.63.0.)
2. **One caveat, one home.** The currency sentence was printed five times
   (dated strip, matrix note, three money cards). It lives ONCE now, in the
   "About The Money Figures" disclosure over the money cards
   (`#tit-usd-note`, repainted per view by dashboard.js); everything else
   carries a short "USD-stated amounts only" pointer. Same treatment for the
   job-board read-through the board collector stamps verbatim on every row:
   `tit_boilerplate_readthroughs()` / `BOILERPLATE_RT` (exact-match only,
   mirrored strings, source is collectors/ats_boards.py) suppress it per card
   and "About Job Board Readings" says it once over the cards. Records are
   untouched; card-rt is contract-optional.
3. **The matrix's seven-bullet "How To Read This" became two visible lines**
   (what a cell counts + tap to filter; rows overlap so columns do not sum)
   **plus a closed native details** holding the rest. Not one fact cut.
4. **Three zones, one tint each**: controls (quick views + filter bar +
   chips, shared `tit-zone-controls` class because the sticky bar cannot be
   wrapped without shrinking its sticking range), insight (charts on the
   ground), updates (one white band: sort, cards, export).
5. **The nine charts sit under question headings** (Where The Activity Is /
   How It Is Trending / What Kind Of Moves, And How We Know / Where The Money
   Is Going), geography first; place and trend are full-width, the four-card
   group is 2x2. Ids untouched, so every share link and repaint still lands.
6. **Colour discipline**: one interactive accent (#0072B2, spec-audited,
   replaces #2a78d6 everywhere a control acts), the green/red pair
   (#006B4F / #B3402A) reserved for headcount direction alone (badges, bars,
   cross-tracker lines), the region pills' nine-hue rainbow removed, verified
   badge distinguished by weight instead of accent blue, pending callouts
   amber (--tit-warn). Chart series keep their categorical palette. Figure
   columns get tabular-nums (not standalone inline figures; gotcha 14).

Byte budget 177,000 -> 178,000 (measured 177,466), paid for partly by the
deleted repetition. All seven PHP harnesses and the full offline suite green.
JS repaint mirrors (datedHtml, matrixHtml, coverageNote) changed in the same
commit as their PHP twins, as the contract comments demand.

---

## 2026-08-02 — the archive pending state says WHEN, and the strip stops reading like a bug (1.64.0, pushed, NOT deployed)

The owner's ask, verbatim: "for wayback, say no wayback link yet, but we check
weekly, the next time we'll check will be — for both sites and all listings".
This entry is the talent tracker's half.

**Measured before designing** (2026-08-02, committed DB): the schedule's scope
is the four publisher collectors (national_press, google_news, gdelt,
ats_boards), 3,218 distinct current source URLs. **1,746 archived (54.3%)**,
1,272 confirmed absent from Wayback (the capture queue), 200 never answered
about, 0 unavailable. Whole-corpus coverage is 7.0% and has its ceiling near
13%, because ~87% of cited URLs are SEC/GOV.UK/registry documents the schedule
deliberately skips. The re-attempt cadence is real: every pending URL's ledger
row had been re-touched within 3 days (the 8-hourly pass examines 600 of a
1,472-URL queue per run, so the whole queue is swept roughly daily). Coverage
is NOT low and the cause needed no fixing; what was missing was the reader
being told any of it.

**Three archive states now render on every listing surface** (dashboard cards,
company profiles, place pages), all through ONE renderer chain:

- archived → the existing second link ("Archived" / "archived copy");
- in scope, no snapshot → "No archive snapshot yet. We re-check weekly; next
  check by <date>." — cadence word and date DERIVED, never typed;
- out of scope (SEC, GOV.UK, registries) → nothing. Promising a re-check
  nothing will make is a false sentence on every filing row.

**The derivation chain, one definition end to end:**
`pipeline/source_links.py` `RECHECK_PROMISE_DAYS = 7` (a deliberate
under-promise of the ~daily sweep, sized to survive throttled weeks and lock
contention) → `build_archive_promise.py` derives cadence (8h, parsed from the
cron in schedule-link-hygiene.yml), scope and per-run limit from the workflows,
REFUSES to build when the queue exceeds the window's capacity (12,600 vs 1,472
today), and writes `wordpress-plugin/.../data/archive_promise.json` →
`tit_archive_pending_note()` (plugin main file) composes the sentence, the ONLY
place it is written; dashboard.js reprints the server's copy off the root's
`data-archive-note` attribute, so a repaint cannot derive a second date.
`tests/test_archive_promise.py` pins shipped-matches-derivation, capacity, and
single-composer; `ops_status.py [2c]` prints `promise ... KEPT/BROKEN` and goes
RED via `archive_recheck_overdue()` when any in-scope unarchived URL has not
been re-attempted within the 7 days the pages promise (0 overdue at ship).

**The dated glance strip, same session (owner):** the week rung now carries its
derived span — "This week (Jul 27-Aug 2)" — because on the 2nd of a month the
week figure legitimately exceeding the month figure read as a bug; the largest
raise carries its row's own `country` field inside the parens ("$389M · United
States"), a third scalar subquery on the same ORDER BY so the name, amount and
country cannot describe three rows, and absent when the source named no place;
and the JS repaint gained the REAL space between the period label and its
figures that the sibling's strip lacks — selected-and-copied text pasted as
"This week1,366 updates" after the first repaint (server paint was fine; the
harness whitespace differs from `datedHtml()`'s concatenation).

Byte budget raised 174,000 → 177,000 in tests/php/render_dashboard.php,
itemised there. Rendered and verified at 375px on the harness dump
(TIT_DUMP_HTML): no horizontal overflow, sentence wraps on its own line under
the source link. Suite: 2,941 passed + 1 pre-existing failure
(test_funding_amount_parsing::test_the_plausibility_floor_is_not_allowed_to_stand_in_for_the_guard,
fails identically on clean origin/main). All seven PHP harnesses green.
**1.64.0 pushed to main, NOT deployed** — deploying is the session's call.

## 2026-08-02 — a "200 ok" feed audit, and the four dead feeds that were our reader

national_press became the main local-coverage route on 2026-08-01, when the
seventeen English non-US Google News editions were withdrawn on the grounds
that those markets already have direct publisher feeds. That made the claim
worth testing rather than repeating, so all 662 wired feeds were re-fetched
through the collector's own `fetch()` and `parse()` on 2026-08-02.

**A feed is live when `parse()` yields items, not when the host answers 200.**
The catalogue recorded `200 ok` for feeds that had never produced a single
item, because the July audit checked the status code.

### What was actually broken, and it was us four times

| Format | Feeds | What happened |
|---|---|---|
| RSS 1.0 (RDF) | 6 | `<item>` is namespaced. `.//item` matches unqualified names only, so Nikkei Asia, CNET Japan, Nikkei xTECH, Impress Watch, PR TIMES and the Taipei Times parsed cleanly and yielded nothing |
| Drupal RSS | 1 | The title is an anchor element. The reader took an element's own text only, so every item was dropped for having no title and The Daily Star's business desk read as dead |
| ISO 8601 dates | 6 | `_normalize_date` anchored on `\b`, and `T` is a word character, so `2026-08-02T12:00:00+09:00` did not parse. That is what `dc:date` carries in every RDF feed |
| RFC-822 two-digit year | 1 | `Sun, 02 Aug 26` matched no format, so a daily paper stored `published_date` NULL on every row |

The date half is not cosmetic. Staleness is how a feed that dies later gets
noticed, and an item whose date will not parse can never make its feed look
stale, so recovering the items without their dates would have swapped one
silent failure for a quieter one.

### Two half-applied fixes the audit found

* **Cayman Compass** was fetched twice a day against its own robots.txt. The
  note in the catalogue row already said "Feed not wired: robots.txt disallows
  this path" and the `rss` column was never cleared. A note is not a
  withdrawal; an empty column is, and a test now asserts it.
* **Disrupt Africa** rebranded to `disruptafrica.com` in July. The note records
  the rebrand and says the url column was updated to match. The `rss` column
  was not, so the domain-drift guard refused it on every run since and the row
  read as 922 days stale. It returns 10 current items on the live domain.

### Genuinely dead, and now recorded as dead

Twenty-two rows unwired. Three (Citinewsroom, Techweez, Techzim) had a
WordPress REST endpoint in the `rss` column: open, current, and JSON, which an
XML reader cannot read. Eight are refused or broken at the transport layer
(Moneycontrol, MedCity News, Capital.ba and Sifted answer 403/500; Newsday
Trinidad fails its TLS handshake on two rows; News.MN times out). Five answer
200 with an empty channel or a bot wall (Prothom Alo English, Quartz, Meet
Global, TheBusinessDesk, La French Tech). Seven have simply stopped publishing,
between 289 and 2,289 days ago (NoCamels, FinLedger, StrictlyVC,
RecruitingDaily, MENAbytes, ReadMe.lk, elsalvador.com negocios). One is the
Cayman Compass above.

CBC News Business is **not** recorded as dead. Its connection is reset before
any response, from two paths on the same host, on three attempts. A reset at
the transport layer from one location is not evidence a feed is gone, and
UNKNOWN is a third state.

### Wired, after two live verifications each

Nine rows: Norvan Reports, 3News Ghana and the Ghanaian Times (Ghana); The
Daily Star economy desk and The Business Standard corporates desk (Bangladesh);
Business Today Malaysia and Focus Malaysia; Cayman News Service; Newswire.lk.

**Two verifications, because one is not enough.** The New Straits Times feed
answered 200 with 25 parseable items on the first probe and 404 on eight
consecutive attempts afterwards, across two processes. It is not wired, and the
row says exactly that. Wiring it on the first green fetch would have put a
permanently dead feed in the catalogue with a live verdict beside it, which is
the failure this whole pass exists to remove.

Cayman News Service and Newswire.lk exist because the withdrawals would
otherwise have taken the Cayman Islands to zero wired feeds and no backstop,
and Sri Lanka to one. Mongolia lost News.MN and has no replacement: Montsame,
gogo.mn and the UB Post were all probed and none answers, so it holds its
coverage on the discovery backstop row alone. Trinidad and Tobago drops to one
wired feed, the Trinidad Express business search feed.

### Cost

Fetching is free and nothing here changes the gate. Net wired feeds 662 -> 648,
so the candidate load falls slightly; the recovered RDF feeds add Japanese and
Chinese items which the CJK prefilter block already reads, and the Bengali
general dailies that would have padded Bangladesh were deliberately NOT wired,
because Bengali has no prefilter pack and the 2026-08-01 measurement puts an
uncovered-language general daily at 1.3% in scope. Read-throughs are capped by
`classify.read_cap` and reallocated rather than raised, so no read money moved.

### Still open

`wordpress-plugin/.../data/sources.json` is out of sync with the catalogue and
`tests/test_sources_page.py::test_manifest_is_in_sync_with_the_registry` is red
until `python3 build_sources_json.py` is run and the plugin deployed. That was
left deliberately: the deploy is the session's call, not an agent's. The build
was dry-checked and reports zero dash offences, 771 entries, 15 live.
## 2026-08-02 — a three-minute chain was paying two hours of queue for it

Five backfill chains share the one `talent-collect` writer slot. Every one of
them carried `BACKFILL_PRIORITY` (10), because `default_priority()` answers a
single question — "does the workflow name start with `backfill-`" — so the
dispatch order fell through to a timestamp that knows nothing about what a
ticket costs.

**Measured from the queue's own history, the 19 hours to 2026-08-02T03:00Z.**
`slice` is dispatch to landing, `wait` is request to dispatch, both medians:

| chain | slice | wait | wait/slice |
|---|---|---|---|
| `backfill-gdelt-2026` | 56 | 92 | 1.6 |
| `backfill-gnews-2026` | 23 | 120 | 5.2 |
| `backfill-structured:companies_house` | 21 | 105 | 5.0 |
| `backfill-structured:bse_india` | **3** | **123** | **41.0** |
| `archive-sources` (priority 0) | 26 | 17 | 0.7 |

Round robin gives every chain one turn per round. That is fair in turns and
indefensible in latency.

**Two of the three obvious fixes are refused on the numbers, and the refusals
are written into the code so nobody re-proposes them from intuition.**

*Not a smaller slice budget.* Exactly ONE chain of five reaches
`SLICE_BUDGET_MINUTES` at all, and its 56 minutes is already inside
`LONG_HOLD_MINUTES` (120) — the line `writer_queue.py` itself draws at "the
queue is starved". Halving the budget shortens a full round from ~135 minutes to
~115 while costing the chain doing the most work about 40% more runs, each
paying the fixed 3-6 minutes of checkout, install, merge and push again. And it
leaves the bad row untouched: three minutes waiting behind twenty-five is the
same shape of unfair as waiting behind fifty.

*Not interleaving by chain.* It already interleaves. A chain requeues its next
slice at the END of its own run, so it re-enters the line with a fresh
`requested_at` and sorts behind every chain that has been waiting — a clean
round robin, free, out of the FIFO tiebreak. A second scheduler would have
reproduced the order we already had.

*It is the dispatch order, and priority was already honoured there.* Step 5 has
sorted on `priority` since the queue was built and it works. What was missing is
that priority carried no information. So `writer_queue.dispatch_key` now sorts
on four terms: the operator's priority, untouched and still deciding everything
below it; then age, past `FAIR_SHARE_AGE_MINUTES`, so nothing starves; then
**measured** cost, from `measured_hold_minutes`, which reads this file's own
landed tickets; then the FIFO that already worked.

`FAST_SLICE_MINUTES = 8` is not a taste. The fixed overhead of a slice run is
3-6 minutes measured (bse_india's ENTIRE run is 3), so a chain under that bar is
not "a shorter job", it is a job indistinguishable from the noise of scheduling
one, and letting it go first costs the chain it overtakes less than that chain's
own startup. Above the bar, reordering transfers real lock time between chains,
which is a policy decision and belongs to an operator's `--priority` — which now
survives a requeue, since `chain_priority` landed the day before.

`FAIR_SHARE_AGE_MINUTES` is deliberately the same number as
`LONG_HOLD_MINUTES` rather than a second tuned constant: it would be incoherent
to let the scheduler reorder a ticket past the threshold at which it reports
that ticket as a problem. It promotes, it does not preempt — nothing can
shorten a slice already running, which is the whole reason `backfill_slices.py`
exists.

**The key is computed at dispatch and never written back onto the ticket.** An
ordering that edited `ticket["priority"]` would be the `default_priority`
-on-requeue bug in a new hat: the stored number keeps meaning "what a human
asked for", and anything derived is derived again next tick where it can be seen
changing. Pinned by a test.

Also here: a chain's identity — workflow plus exact inputs — was written out
four separate times, so `chain_inputs`/`same_chain` are now the one definition
and `chain_priority`, `superseded_by` and `backfill_slices._live_ticket` all use
it. `ops_status.py [2b]` prints waiting tickets in the order they will actually
be dispatched, with the reason, because a scheduler that reorders silently is a
scheduler nobody can audit.

Against the live queue the new order puts bse_india (3 min, asked last) first
and leaves gnews, companies_house and press in FIFO. `SLICE_BUDGET_MINUTES` is
unchanged at 50.

**Proven in production within the hour, and the drain log says so in its own
words.** Three consecutive ticks after the push:

```
03:58:25  dispatching backfill-structured-2026 (bse_india)
            chosen because its measured slice is 3 min, inside the 8-minute fast lane
            it goes ahead of 20260802T022936Z-backfill-gnews-2026, which asked first.
04:01:50  dispatching backfill-structured-2026 (bse_india)   [same, its next slice]
04:04:00  dispatching backfill-gnews-2026
            chosen because its measured slice is 23 min, so it takes an ordinary turn
```

bse_india took its last TWO slices in six minutes and the chain finished
(`data/backfill_state.json`, 8 slices, done 04:03:29). At the measured FIFO wait
of ~123 minutes a turn those two slices were about four hours apart. gnews,
which it overtook twice, was delayed by six minutes in total — which is the
whole argument for the 8-minute bar, observed rather than predicted.

## 2026-08-02 — the tripwire was armed, measured, and documented as neither

Three claims about the discovery tripwire were current in this repo and all
three were wrong. Recorded because each one was believed and acted on.

**"It has never issued a live query."** It has. Run 30506967802, 2026-07-30
01:54Z: 17 search-backed queries against `perplexity/sonar`, $0.0977 billed,
**$0.0057 a query**, spread $0.0054-$0.0060. `analysis/tripwire/plan.py` has
carried that measurement and its source string since 8a74dd5 the same day.
`docs/HANDOVER.md` still said the cost was an estimate.

**"It is DORMANT."** It has been ARMED since 77becc5, 2026-07-30 — Mon+Thu
07:00 UTC, `dry_run=false`, from `schedule-link-hygiene.yml`, because a lock
member may not carry its own cron. `tripwire.yml`'s own header still told the
reader to "uncomment the two schedule lines", which arming had deleted.

**And that stale header cost something real.** `staleness.py` gave `tripwire` a
2400-hour (100-day) leash with the note "tighten this to 336 the day the
schedule in `.github/workflows/tripwire.yml` is uncommented". Arming REMOVES
that line, so the instruction's own trigger could never fire: a live
twice-weekly collector wore a 100-day leash for three days and would have
reported `ok` from a Monday breakage until November. Now 336, which is the
number that note always named — 3.5-day cadence, four missed runs, wide because
the slot writes a ticket that waits behind whatever holds the writer lock.

**What was genuinely undone: no tripwire run had ever WRITTEN.** Every run to
that point was `--dry-run`, so there was no `data/tripwire_worklist.json`, no
`analysis/tripwire/results/`, no `source_health` row and no first point for the
trend. Queued through `drain-writers` (never dispatched directly) as ticket
`20260802T032205Z-tripwire`, headroom checked first: August spend was $4.32 of a
$9.00 enforce ceiling against a run costing at most $0.44 at the pessimistic
estimate.

**Run 30731489198, 2026-08-02 — it wrote.** 22 queries (AT, AZ, BD, RS off the
measured recall rotation, plus the monthly 18-industry sweep), **$0.1248**,
**$0.0057 a query**, 108 usable leads against 25,152 stored signals, 15 already
held, **93 missing**. $0.0012 per usable lead, $0.0013 per candidate miss. The
work list, the dated trend point and one `source_health` row are on main
(02a8df3). US 40, IN 12, DE 5, GB 4 lead the miss counts, which is the feed
roadmap talking.

**The price reproduced exactly, and that is worth more than a bigger sample.**
Two runs three days apart, asking entirely different sets, both land on
$0.0057/query — 39 queries, $0.2225 total, spread $0.0053-$0.0060. So the price
tracks the query SHAPE, not what any one run happened to ask, and the $0.02
estimate stays 3.5x conservative in the right direction. What is still NOT
measurable is cost per CONFIRMED miss: that needs `collectors/tripwire_chase.py`
to store a row against a lead, and it has not run.

**`cost_projection.py` did not know the tripwire existed.** It is the tool that
exists so nobody quotes a cost from memory, and discovery — the one paid thing
in the product that is not a read of a source we already trust — appeared
nowhere in it, so every "what the allowance actually buys" figure was computed
against a ceiling something else had already partly spent. It now prices
discovery as MEASURED ($0.0057/query, 50 queries/month, **$0.29/month**) beside
the $0.02 estimate that sizes the plan and never reports it, and subtracts it
from what collection may spend. ARMED is derived from the schedule file that
actually dispatches it, not asserted, so the figure cannot drift the day
somebody disarms it; a dormant tripwire is charged $0.00 and still printed with
the price arming would cost.
## 2026-08-01 — the charts move above the updates, and eight pointers that named a position (1.63.0)

**Asked for by the owner**, in three parts: quick views and filters above the
country and city charts; the charts between the filters and the update cards;
and "Narrow It Down / Everything below follows these filters, including the
charts — do we need this?"

### What the order was, measured rather than assumed

The brief described the live order as `signal table -> quick views -> filters ->
charts -> cards`. The live page (1.62.2, fetched and read rather than recalled)
was `hero + signal table -> Narrow It Down -> quick views -> filters -> chips ->
cards -> export -> What The Data Says -> charts -> money charts -> trust`. So the
charts were **below** the fifty result cards, not above the filters: on a phone
they began 24,143px down a 28,000px page. Three comments in `shortcodes.php` still
described a third arrangement, one of them ("The market read comes BEFORE the
filter machinery") describing the opposite of what shipped.

### The new order

`hero -> lede -> quick views -> filters -> Filtering chips -> What The Data Says
-> 9 chart cards -> 3 money cards -> Routine Filings + Sort -> update cards ->
export`. Two rules decided the two seams, and they are worth keeping: the chips
bar states what the filters are doing, so it stays **with** the filters; Routine
Filings and Sort order the update list and nothing else, so they stay directly
on top of it.

The chart block moved as one, unedited. Every chart row is still a button that
writes the same hidden select, so click-to-filter, the querystring, the chips
bar and the exports are untouched — verified in a real DOM by clicking a country
row (`?country=GB`, `#tit-f-country` = GB), loading `?industry=technology` cold
(select set, chip painted, both export hrefs rewritten), and Reset All.

The charts now sit **inside `.tit-results`**. That subtree sets no overflow and
neither does `.tit-feed`, so the sticky filter bar is unaffected: measured at
1280px, `.tit-filterbar` computes `position:sticky` and holds `top:0` while the
page scrolls through both chart grids and the cards. A scrollable ancestor is the
one thing that cancels sticky outright and it fails **silently**, so anything
nested here later has to be re-measured, not reasoned about.

### "Narrow It Down": the heading goes, the sentence stays and shrinks

The heading was the redundant half. The next element on the page is a group
labelled **Quick Views** and the one after it a bar labelled **Filters**, and
below 900px the word Filters appears twice inside one screen of it (the bar head
plus the collapse toggle). A heading whose whole job is to announce that filters
follow, one line above something that says "Filters", is a row of dead pixels.

The sentence was the load-bearing half, because "the charts are filtered too" is
the one thing the layout could not say — and with the charts now directly under
the bar, the layout says most of it. So it is one line, no heading:

> The charts and the updates both follow these filters.

**The wrapper and its `id` survive on purpose.** `dashboard.js` builds the phone
jump bar with `data-jump="#tit-filter-sec"`; deleting the element would have taken
the Filters button on every phone with it, with nothing red anywhere.

### Eight pointers rewritten, two of them wrong the moment the block moved

A reorder breaks copy that names a POSITION and never breaks copy that names a
THING. This has now happened twice here (the 2026-07-30 pass left "click a number
in the matrix at the top" pointing at a matrix that was no longer at the top), so
every one of these is rewritten to name the thing:

| was | is |
|---|---|
| Everything **below** follows these filters, including the charts | The charts and the updates both follow these filters |
| For a time period, tap a number in the signal table **above** | ...tap a number in the signal table |
| Every employer name **below** links to that employer's own page | Every employer name **in the updates** links to... |
| becomes its own chip **above the table** | becomes its own chip **in the Filtering row** |
| the control **above the table** turns them back on (FAQ) | the **Routine Filings control** turns them back on |
| The export links **above** take the current view (FAQ) | The **CSV and JSON links** take the current view |
| The figures **above the table** count everything in this view | The **headline figures** count everything in this view |
| The counts are in the updates **above** (trend, twice) | ...in the updates **themselves** |

The last one is printed by BOTH `shortcodes.php` and `dashboard.js` (the matrix
note is repainted on every filter change), so both copies moved together; a
divergence there shows up as the block rewriting itself while a reader watches.

### "Moves Headcount(1,869)" — a gap that existed only for the eye

The owner read the chip as missing a space, and it was: the markup is
`Moves Headcount<span class="tit-qv-n">(1,869)</span>` and the separation was
`margin-left:5px`. A margin separates two words for a sighted reader and for
nobody else — the accessible name, the copied text and anything scraping the page
all read `Moves Headcount(1,869)`. It is a real space now and the margin is gone,
so the gap is the same width.

**Checked every other counted control for the same, and found three more.** The
server-rendered strips (regions, country pills, city pills, chart rows) all have
newlines between their spans and were already fine. The ones `dashboard.js`
builds on every repaint were not: the Filtering chip read `IndustryTechnology×`,
and the repainted chart and money rows read `United Kingdom2580` where the
server's copy of the same row read `United Kingdom 2580`. Fixed by emitting the
space the server already emits. It is free: `.tit-chip` is a flex container and
`.tit-rank-row` a grid, and whitespace-only anonymous boxes are not rendered in
either — measured, a row built with the spaces has byte-identical child
geometry to the server's.

### Not found: the "bare `--` separators"

Reported by the owner, and **not reproducible**. There is no `--` anywhere in the
dashboard: not in the served markup, not in `innerText` of the live page or of
the render harness, and not in any CSS `content:` rule (the four that exist are
`attr(data-label)`, a non-breaking-space middot, `+` and U+2212). The only thing
on the page that DRAWS as a bare dash is `.tit-trend-swatch` — a 14x3px line mark
that keys each series in the Updates a Day legend, so at 375px that card ends
with two lines opening on a short dash. It is a legend key rather than a
separator and removing it would take the colour mapping with it, so it is left
alone and raised instead.

### Verified

Full suite green (**2,930** offline tests plus 202 subtests, measured on this
branch rebased onto 1.62.3 — HANDOVER said 2,923, which is the count that was
true when somebody last typed it) and all seven PHP render harnesses green, before and after. The page
was **looked at**, in a layout engine, at 375px and at 1280px: no horizontal overflow at either
(`scrollWidth === clientWidth`), the two new seams read correctly, and the phone
jump bar's two targets (`#tit-filter-sec`, `.tit-detail`) both still resolve —
"Updates" now usefully skips the charts. Markup went 173,572 -> 173,556 bytes on
the harness fixture, so the byte budget is untouched.

**Not verified:** the live page. This work was done by an agent and agents do not
deploy (see CLAUDE.md); it is pushed to main and NOT deployed.

---

## 2026-08-01 — seventeen Google News editions were the US wire under another name

**The premise this started from was wrong, and re-measuring it first is the
only reason the change is defensible.** The brief said `en-GB`, `en-IE` and
`en-SG` return 100% the same items as `en-US`. On the full five-query
production pack they do not: overlap is **62-70%**, and only `en-BD` and
`en-HK` sit at 99.7%, which is that run's churn floor. The 100% came from a
single query and 47 items.

**Correcting the number did not move the conclusion, because overlap was never
the right instrument.** The ~35% that differs is the same global English wire
re-ranked. What decides is the publisher: how many of an edition's items come
from a newsroom in that edition's own country, are in scope by the free
prefilter, and are NOT from a publisher `national_press` already reads twice a
day. That is an edition's marginal value.

| | English non-US editions | non-English controls |
|---|---|---|
| items per visit | 366-381 | 157-285 |
| from a publisher in that country | **0.0-11.5%** | **49.0-67.7%** |
| new local, in scope, per visit | **0-7** | **53-163** |

`en-BD` and `en-HK` returned zero items that were not already in the anchor.
The seventeen also overlap EACH OTHER: individually each adds 48-84 candidates
on top of the anchor, all seventeen together add 230. One corpus, resampled
seventeen times.

The churn floor was measured in the same run — the anchor re-fetched at the end
repeated 99.7% of its own first pull — because without it "edition X differs by
35%" could just be the index moving during a twenty-minute run.

**What changed.** All seventeen are out of `GOOGLE_NEWS_LOCALES` (51 editions ->
34, plus the anchor). Nothing replaced them because nothing needed building:
every one of those markets already has direct publisher feeds in
`data/sources_catalogue.csv`, read on EVERY run rather than once per rotation.
The thinnest of them — Bangladesh, Ghana, Malaysia — returned 30, 33 and 45
items on the last recorded press run, against the 0-7 their edition gave every
four days. `tests/test_google_news_editions.py` pins that: a withdrawn market
that drops below two wired feeds turns the withdrawal into a coverage hole and
says so.

**`LOCALES_PER_RUN` went 5 -> 4, and that is the whole budget story.** Keeping
5 would have been a spend increase in disguise: the withdrawn editions were
cheap per visit precisely because they returned the anchor again (~71
prefilter-passing candidates on top of it, against ~189 for a non-English one),
so swapping ~3.3 cheap visits a day for expensive ones would have raised the
daily candidate load from ~1,497 to ~1,890 and gate spend with it. Four holds
the load at ~1,512, within 1% of today, and buys **8 productive edition-visits
a day where there were 6.7**. Read-throughs are capped at 99/run for
google_news and the cap is saturated, so no read money moved at all; what moved
is which candidates compete for it. Sweep 5.1d -> 4.25d, derived recency window
7d -> 6d.

**The measurement is committed, not just written down:**
`python3 -m analysis.editions.measure` re-runs it. Stdlib only, keyless, free,
resolves no redirects and stores nothing. The table beside
`WITHDRAWN_ENGLISH_EDITIONS` is that tool's verbatim output, so the comment and
the instrument cannot drift apart.

**One coupling had to be cut to do this, and it is worth knowing about.** The
segment matrix was budgeted against `recency_window_days(...)`, the window
DERIVED FROM THE LOCALE ROTATION. Shortening the locale sweep would therefore
have cut the segment ceiling from 56 to 40 and forced sixteen segments — twelve
markets — off the public coverage page as a side effect of an unrelated
improvement. The two rotations are independent and only the locale one carries
the hazard: a locale query has `when:Nd` and a story can age out before its
edition's turn, while a segment query has no `when:` at all (now asserted, so
the reasoning cannot rot). The ceiling is `SEGMENT_SWEEP_BUDGET_DAYS = 7` now —
same number, chosen rather than inherited.

**Still open, and NOT touched here.** The sources page says Google News RSS is
"38 country editions, 15 languages". That was already wrong before this change
(it was 51 plus the anchor) and the honest string is now "35 country editions,
16 languages". Fixing it means regenerating `wordpress-plugin/.../sources.json`
and deploying, which is a session's call and not a subagent's.

---

## 2026-08-01 — a backfill slice advanced its cursor over three days it never fetched

**Measured, not inferred.** `backfill-gnews-2026` run 30662474194 (2026-07-31,
74 minutes) reported:

```
  windows            3 (3 empty)
  queries sent       576 (576 failed, 0 truncated at the 100 cap)
  STOPPED EARLY      slice budget of 50 minutes reached (74 min elapsed)
  next cursor        2026-01-25
```

Google News refused every request for the whole slice — an IP-level block; the
next seven slices ran 768 queries with zero failures. The chain nonetheless
moved from 2026-01-22 to 2026-01-25 and requeued. In the committed database
**2026-01-24 holds zero google_news rows**, with 01-22/23/25 at 15/3/3 against
a ~20/day baseline for the surrounding fortnight. A chain only moves forwards,
so nothing will ever be sent back for those days.

### Nothing was missing. The guard existed, fired, and did not matter.

The obvious hypothesis — that the run stopped on its slice budget part way
through and set `done_through` from a partial walk whose days were not counted
as empty — is **wrong**, and the log disproves it. The three days that were
reached WERE counted (`3 empty` of `3`), the fail-loud check
`if windows and empty_windows == windows` DID fire, and the run exited 1. The
budget break happens at the TOP of the loop, before `windows += 1`, so the
fourth day (01-25) was correctly never claimed and the next slice collected it.

The mechanism is two deliberate design decisions meeting one wrong line:

1. The slice ticket is emitted **before** the fail-loud checks, on purpose, so
   that rows a run already collected are never the price of how it ended.
2. The workflow's commit step runs `if: !cancelled()`, on purpose, for the same
   reason. So a RED run still records its ticket and still requeues.
3. `done_through = lo` sat at the bottom of the loop body **unconditionally**.

So `done_through` measured "completed a loop iteration", not "collected
anything", and three days that were walked and could not be fetched were three
days finished.

**RED IS NOT THE SAME AS UNADVANCED.** The comment above the emit block said "a
run that finished NOTHING emits a cursor that has not moved, which
`backfill_slices record` refuses to requeue and goes red on" — which is true,
and which never described this run, because this run had "finished" three
windows. Going red is what gets a human to look; the cursor is the only thing
that decides whether a day is skipped for ever. Both are needed and neither
substitutes for the other.

### The fix: a window has THREE outcomes

`backfill_slices.py` now names them, for the same reason ops_status keeps
PASS / FAIL / UNKNOWN apart — the absence of an article and the absence of an
answer are different facts, and only one of them is progress:

| state | meaning | cursor |
|---|---|---|
| `COLLECTED` | the fetch worked and returned something | may pass |
| `EMPTY` | the fetch worked and there was nothing there | may pass |
| `UNREACHED` | the fetch itself failed | **must not pass** |

Two classifiers, because the walkers make two different promises:

* `sampled_window(items, fetch_errors)` — gnews and gdelt. They ration what
  reaches the model and leave the rest unmarked, so partial coverage of a day
  is the DESIGNED outcome and one flaky edition of 52 is weather. A window is
  unreached only when it produced nothing while the fetch was erroring — the
  measured incident exactly.
* `enumerated_window(items, fetch_errors)` — sec and form_d. The contract is
  completeness, so ANY failed search page leaves the window incomplete however
  many filings the earlier pages returned. Re-walking is nearly free
  (`store.already_seen` skips stored URLs before any model call); a hole is not.

`backfill_press_2026.py` walks a roster rather than a calendar, so it counts
per index: an index advances only when every publisher in it was attempted AND
at least one gave an answer. `dead` (a transport failure) is not an answer;
`no_window` and `hijacked` are, and are permanent, so refusing to advance over
them would freeze the walk for ever.

An unreached window now breaks the walk before `done_through` is set, prints
which window and why, and returns 1 — so the emitted cursor equals the one the
run started from, `record` marks the job `stalled`, nothing is requeued, and
the chain stops itself instead of stepping over the hole.

**What did NOT change:** committing rows from a failed run. That is correct and
deliberate — a failed run that already stored and published rows holds the only
local record of them. Only the cursor advance was wrong.

### The window 2026-01-22..2026-01-25 is still missing

Recorded here rather than silently repaired: recovering it means re-queueing
that range, and spending is the owner's call. Measured cost is ~$0.2184 per
gnews slice.

---

## 2026-08-01 — a cancelled chain does not requeue itself, and reported clean

A FAILED slice requeues; its commit step is `if: !cancelled()`, so it records
its ticket and appends the next one. A CANCELLED slice skips that step
entirely, so it records nothing and queues nothing and the chain simply ends.

`backfill-structured-2026` run 30594795739 was cancelled mid-run during the
2026-07-31 Bluehost outage. **bse_india sat at cursor 2026-01-29 and
companies_house at slice 1 of 7 for two days** while `backfill_slices.py status`
printed `problems: []`.

The writer queue does mark a cancelled-mid-run ticket `failed` and does report
it — but `writer_queue resolve` exists so a human can stop a permanently red
drain tick, and acknowledging that ticket clears the queue's alarm **without
putting anything back in the line**. The chain is then dead and every dashboard
is green. (Four such tickets were acknowledged on 2026-08-01, including
30662474194's.) So a chain has to be able to notice its own death from its own
state rather than inherit a signal from a queue that has legitimately moved on.

`summary()` now reports a `running` chain that has nothing live in the writer
queue behind it and has not moved for `CHAIN_IDLE_HOURS` (3). The match is on
workflow AND inputs, because one workflow drives several independent chains —
a ticket for bse_india says nothing about companies_house. `ops_status.py [2e]`
prints, per chain, either the ticket id its next slice is waiting as, or
`NOTHING QUEUED — the chain has stopped`, with the idle age either way. No
queue file at all prints `UNKNOWN`, never a stall: a check that could not run is
not a pass and must not manufacture a red run either.

**Decided: it does NOT auto-requeue**, and the reasoning is in the code.
Mid-run cancellation is what a host outage and a timeout both look like from
here. Requeueing into the first is the loop this repo already paid to break
once (an alerter that posts to the host it is reporting as down); requeueing
into the second burns one paid slice per attempt for ever and is green every
time. `writer_queue.tick` already draws this exact line for every other writer
— cancelled-with-no-jobs is displacement and auto-requeues,
cancelled-after-starting needs a human — and a backfill is not special enough
to get a second policy. What was missing was never the requeue. It was somebody
being told.

### Two smaller things fixed in passing

**`--priority` bought exactly one slice.** `default_priority()` is a property of
the WORKFLOW, so it reapplied to every requeued ticket. The free, no-model
structured walkers were queued at priority 5 so they would drain ahead of the
paid backfills, and each chain's own next slice came back at
`BACKFILL_PRIORITY` (10), behind the very work it was meant to overtake — the
parameter looked effective and was not. `writer_queue.chain_priority()` now
reads the last ticket of the SAME chain (workflow + exact inputs) and
`backfill_slices` inherits it instead of recomputing. A chain with no history
still falls back to the workflow default.

**`writer_queue.py drop`** withdraws a ticket that is still queued. Two
identical `backfill-gdelt-2026` tickets were queued on 2026-08-01; both resume
from the same committed cursor so nothing would have been redone or doubled,
but the second was a redundant paid slice (~$0.055). There was no way to
withdraw it and hand-editing the queue file is how it stops agreeing with the
runs it tracks. The ticket is marked `abandoned` and acknowledged in one step
so a deliberate decision does not report as an incident for ever. A
`dispatched` ticket cannot be dropped: it is bound to a run already holding the
lock, and forgetting it here is the eviction bug's own fingerprint.

---

## 2026-07-31 — five backfills bought gate labels and threw every one away

**What was wrong.** `pipeline/gate_ledger.py` records one line per gate
decision: the training set for the free classifier that is the only route to
the owner's $5/month target, because `cost_projection.py [5]` puts the paid
gate alone at $4.41 of the $5. The daily collectors were wired correctly and
their labels land. The five backfills were not, and the failure was silent in
both halves:

* **In the process.** `classify.classify()` calls `gate_ledger.record()`, which
  only BUFFERS; something has to call `flush()`. `run_collect.py` did, through
  a `_with_gate_labels` decorator. `backfill_sec_2026.py`,
  `backfill_form_d_2026.py`, `backfill_gdelt_2026.py`, `backfill_gnews_2026.py`
  and `backfill_press_2026.py` never imported `gate_ledger` at all, so every
  verdict they paid for went to the buffer and was dropped at process exit. The
  module cannot warn about this: a run that gated nothing and a run that lost
  everything look identical from inside it.
* **In the workflow.** Even a flushed shard would not have survived. Each
  backfill's commit step does `git reset --hard origin/main` before committing
  (deliberate: it is what stops a `cp` destroying another run's rows), and
  `merge_gate_labels.py` — which exists precisely for this, mirroring
  `merge_db.py` — was invoked by `collect.yml`, `collect-press.yml` and
  `collect-structured.yml` and by no backfill.

So the most expensive way to lose data: the money was already spent.

**The fix.** The reset/flush pairing now lives in ONE place,
`gate_ledger.around_run(label)`, and every entry point wears it —
`run_collect.py`'s decorator is now that function rather than a second copy of
it. Two copies of a pair is how one of them keeps being forgotten. The
backfills also close the join at the same branches `run_collect` does
(`deferred`, `error`, `model_reject`, `validate_reject`, `would_store`,
`stored`/`duplicate`), because the classifier's real target is "did this become
a stored row", not "did the LLM like it".

All five workflows now save `data/gate_labels` to `$RUNNER_TEMP` before the
reset, run `merge_gate_labels.py` after it, and `git add -A` the directory
before the commit — the same three steps, in the same order, that the daily
collectors already had.

**The verdict stays four-valued.** `YES`, `NO`, `ERROR`, `OFF`. The gate FAILS
OPEN, so "the model said yes" and "the model never answered" must never share a
label: recording an outage as a YES would teach the classifier that a busy
provider is a talent signal. `OFF` (a single-stage run, no gate call at all)
now has a test of its own; it did not before.

**Guards added, both of which fail on the code as it was.**
`test_every_entry_point_that_classifies_also_flushes` scans for any file that
calls `classify.classify` without `gate_ledger.around_run`, and
`test_every_workflow_that_classifies_merges_its_labels_back` derives the
workflow list from those scripts rather than from a list kept in the test — a
hand-maintained list is what let five backfills go unnoticed for months. Plus
tests that `around_run` flushes on an early return and on a raise (how a
backfill ends on exhausted credits and on a bad key) and that it resets between
two runs in one process.

Suite: 2,821 passing, up from 2,813. No production deploy: Python and workflow
YAML only.

---

## 2026-07-31 — nine chart cards, the trend demoted into the grid, and the prose behind an (i) (1.62.0)

`wordpress-plugin/talent-intelligence-tracker/includes/shortcodes.php`,
`assets/dashboard.js`, `assets/dashboard.css`,
`tests/php/render_dashboard.php`.

**What the owner asked for:** the trend chart small and inside the grid rather
than full width above it, positioned after the country card; the wording on the
cards minimised and moved behind an (i); two more cards so the grid is three
rows of three; every card expandable, and an expanded view shareable as a link.

**The grid is nine, in this order:** What Is Moving, Where the Jobs Are, Updates
a Day, Which Way Headcount Is Going, How Solid the Evidence Is, Which Industries
Are Moving, then the three money cards. Two `.tit-charts` grids of six and three,
each `repeat(3,1fr)` above 900px, which is three rows of three without merging
the money section that a previous pass deliberately kept as its own block.

**The two new cards, and why these two of the three on offer.**

* **How Solid the Evidence Is** (`by_confidence`). The only card on the page
  that counts how we know rather than what happened, in the shared card-contract
  vocabulary, and clicking a bar sets the Evidence filter. It costs **no query**:
  three CASE expressions joined the head scan that already counted the verified
  rows for the hero.
* **Which Industries Are Moving** (`by_industry`, by count). Distinct from the
  money-by-industry card, which can only see rows carrying a dollar figure, so a
  sector hiring hard and raising nothing is invisible there and visible here.

`by_state` was **declined**, and the reason is the same one the region strip
already follows. It holds 5,991 of 18,069 rows and every one of them is American,
so on the default worldwide view it is a card about a third of the data, and
under any non-US filter it is an empty card. An empty card reads as a filter that
broke. The industry ranking is populated under every filter the page offers.

Cold render is **14 queries, up from 13**: only the industry card cost one.

**The (i) preserves the caveats rather than hiding them.** The panels ship
**open** in the served markup and the button ships hidden; dashboard.js closes
the panel and reveals the button, in that order, so a reader whose script never
ran gets the prose. The button is a real `<button>` with `aria-expanded`, and
both it and each card's data group carry `aria-describedby` pointing at the
panel, so a screen reader gets the caveat whether the panel is open or shut. Not
a `title=`, which is reachable by neither a keyboard nor a screen reader. The
button carries **no `aria-label`** — its name is the visually hidden text inside
it. The expand button LOST an `aria-label` it should never have had: it was
silently replacing the "Expand"/"Collapse" text that the script rewrites.

What moved in: the trend's collector-count sentence and its "some signals are not
drawn" reasons, the one-collector place caveat, every card's subtitle, and the
money coverage sentence that was **printed three times, identically**.

**The trend survives the smaller box because the text came out of the drawing.**
It was 720 units wide with a 520px min-width and its labels inside it, which
inside a card is either a permanent horizontal scrollbar or 12px axis text
rendered at about five. The five axis values and the two dates are HTML beside
the SVG now, so they are CSS pixels at every size; the grid and both lines carry
`vector-effect="non-scaling-stroke"`; and the endpoint dots are a second SVG with
**no viewBox**, so `r="3.5"` is 3.5px whether the card is 300 or 780 wide. Both
series and both dots are still drawn. Measured: the drawn panel was 5,321 bytes
and is 5,132.

**An expanded card is part of the link.** `card=<chart-id>` joins the querystring
the page already syncs, `applyUrlState()`'s neighbour `openCardFromUrl()` restores
it, and every writer of the address bar goes through one `writeUrl()` so a filter
change cannot drop it. One card expands at a time, because two could not be
described by one value. Share copies the filters, the card and, when it is open,
the expansion. The trend's CSV button stays hidden: `chartCsv()` reads bar rows
and the trend has none, so it would have handed over a header and nothing else.

**Byte budget 169,000 -> 174,000**, itemised in the harness beside the constant.
The head is built as a string rather than a template because it prints nine times
and four indented buttons were two kilobytes of leading whitespace.

---

## 2026-07-31 — a Form D "amount sold" is not money raised, and 318 published rows said it was

`correct_form_d_overcount.py`, `.github/workflows/correct-form-d-overcount.yml`,
`tests/test_form_d_overcount_correction.py`, a pending corrections-log entry,
plugin **1.60.0**. **This is the BACKWARD half only.** The collector fixes are
owned elsewhere and nothing under `collectors/` was touched.

A Form D reports an **amount sold**. That is not the same fact as money raised,
and three kinds of filing were being published as funding rounds when the
document never said a company raised anything.

### The four that were retracted by hand, and what they had in common

| | what the filing says | published as |
|---|---|---|
| Masimo | Danaher is ACQUIRING Masimo at $180/share, "total consideration of $9.9 billion" | a $9.90bn raise |
| Dillard's | "Merger of W.D. Company, Inc. with and into Dillard's, Inc.", amount estimated off a closing share price, zero cash | $2.39bn |
| Madison Air | a reorganisation of entities under common control: 402,614,670 shares x $27.00 = $10,870,596,090 exactly. The only cash in the prospectus is $100.0m | $10.87bn |
| OPTCAPITAL LLC | a D/A, the fourteenth annual amendment to a continuous offering first sold 2012-07-22, offering amount "Indefinite" | a $1.77bn round |

None of the four is an extraction error. Every figure is exactly what the filing
prints. What is wrong is the **claim wrapped around it**, which is why none of
them could be fixed by re-reading the document.

### The three shapes, measured against the live API on 2026-07-31

3,312 live funding rows, **$118.416bn**. 3,013 come from Form D and 3,004 of
those join a cached quarter archive (2026q1, 2026q2). The nine that do not are
July filings: the quarterly data set is only published once a quarter has ended.

| | rows | money | withdrawn |
|---|---|---|---|
| `ISBUSINESSCOMBINATIONTRANS` = true | 177 | $8.535bn | 170 / $7.788bn |
| `TOTALOFFERINGAMOUNT` = "Indefinite" | 214 | $7.181bn | 75 / $5.472bn |
| an offering already published (same CIK + SEC file number) | 143 | — | 73 / $0.990bn |
| **union** | | | **318 / $14.250bn** |

**`ISBUSINESSCOMBINATIONTRANS` has been in the data set the whole time and no
code path had ever read it.** It is the issuer's own yes-or-no answer to whether
the offering is part of a business combination.

### What each rule costs, because a rule that deletes true records is not free

The publish guardrails rejected two candidate patterns on exactly this basis;
the same accounting is done here and the same kind of answer came out.

**Rule 1, business combinations — the one with a cost we cannot measure away.**
62 of the 177 filings write a clarification. 7 of those 62 say the proceeds were
cash that was then spent: *"a portion of the proceeds of the sale of securities
to investors was used to acquire"*, *"funds are being used to acquire a
hospital"*, *"the private placement (PIPE) financing closed concurrently with a
Merger"*. Those 7 (**$0.747bn**) are real raises and are kept, matched on
phrasing quoted from the filings themselves. The remaining **115 answer yes and
explain nothing**, and nothing in the data set separates them: EDGAR shows Sensei
Biotherapeutics now filing as **Faeth Therapeutics** (a reverse merger) and
Infleqtion's CIK resolving to **AltEnergy Acquisition Corp** (a SPAC), so the box
is being answered correctly — but a de-SPAC PIPE is also cash. On the rate of the
62 that do explain, **roughly a dozen of the 115 are real raises and go with the
rest**. That is stated on the corrections page rather than buried.

*Rejected candidate: sales commission as a rescue.* A merger does not pay a
broker to sell shares, so `SALESCOMM_DOLLARAMOUNT > 0` looks like it should mark
a cash placement. Measured: it rescues 8 of the 115 silent rows, and **wrongly
keeps 5 filings that state in words that the shares were merger consideration** —
four of them bank mergers where the "commission" is the adviser's fee. Fewer
rescued than wrongly kept. Not used.

*Rejected candidate: `ISSECURITYTOBEACQUIREDTYPE`.* 417 published rows tick
"security to be acquired in a business combination" and only 17 of them also
answer yes to the business-combination question. The checkbox is mis-ticked at
scale; using it would cost 400 rows.

**Rule 2, uncapped continuous offerings — where the naive version costs 138.**
"Indefinite" alone would take **138 more rows, $1.697bn**, including Harvey AI's
$200m: an uncapped offering that opened this quarter is a round, not a running
total. So the rule also requires the issuer to say the offering runs more than a
year AND the first sale to be at least 365 days before this filing. Every one of
the 75 it takes is a D/A whose first sale is 1.6 to 12 years earlier — Brown
Advisory Group Holdings at 10.1 years, GREAT-WEST LIFECO at 12.0.

**Rule 3, an offering already published — cost zero, and one trap.**
The offering is keyed on **(CIK, SEC file number)**, never on the issuer.
Fluidstack is why: a January D at $450m and a May D/A at $842m are one offering
(same file number, same first-sale date 2026-01-10), and its **June D at $730m is
a genuinely separate offering under a new file number**. Grouping by company
would have deleted a real $730m raise. Every one of the 66 groups the rule
touches shares a file number and a first-sale date, so nothing legitimate is
lost.

**Keeping the LATEST, not the first and not the sum.** A Form D amendment
restates the running total for the whole offering, so the last filing is the
entire raise and every earlier one is that same money again. In **65 of the 66**
groups the latest figure is also the largest; the single exception is Global
Gardens LLC revising its own total from $4.985m down to $4.335m, and the filer's
latest answer is still the right one to show. Keeping the largest would
republish a figure the issuer has withdrawn; summing would be the double-count
the rule exists to remove.

### The number that is smaller than it looks, and why

The brief that started this measured **554 amendment rows carrying $16.73bn** of
cumulative totals across 152 CIKs. Both figures are right and neither is the
double-count. 556 amendments are published; what is double-counted is only the
subset whose **earlier filing for the same offering is ALSO published**, which is
77 rows and $1.09bn before rules 1 and 2 take their share, 73 and $0.990bn after.
The rest of the amendments are the only row we hold for their offering, so their
cumulative total is the whole raise and withdrawing them would delete it. The
population that carries the risk is not the population that realises it.

### Flagged and NOT acted on: industry group "Investing"

91 rows, $1.564bn, absent from `EXCLUDED_INDUSTRIES`. Excluding the group would
be the real-estate rule again with a worse ratio: **0 of the 91 match the
collector's existing vehicle-name patterns**, and the list holds obvious
operating employers — Farther Finance $145.6m, GeoWealth $42.5m, AdvisorNet
Financial $32.5m, SecurCapital — beside obvious vehicles (Dorado 2024-1,
Solvanta Funding 2025-1, Cypress Point Funding, Madison Avenue Funding). 11 of
the 91 are already withdrawn by rules 1-3. **This is a name-vocabulary gap, not
an industry one**, and it belongs to whoever owns the collector: the missing
shapes are `... Funding LLC`, a `YYYY-N` serial, and `Blocker Corp`.

### Applied 2026-07-31, run 30605363355 — projected, then measured

| | before | projected | measured |
|---|---|---|---|
| funding records | 3,344 | 3,026 | **3,026** |
| money raised | $122.0bn | $107.7bn | **$107.713bn** |
| records drawn from Form D | 3,013 | 2,695 | **2,695** |
| business combinations published as raises | 177 / $8.5bn | 7 / $0.7bn | **7 / $0.7bn** |
| employers with a funding record | 3,127 | 2,906 | **2,937** |

The before column is the dashboard's own aggregate (`money.total`,
`money.coverage`), not a query of our own. `funding=1` over `/query` returns
3,312 rows and $118.416bn because the funding TEST and the sum of
`funding_amount_usd` count different populations, and a corrections page quoting
$118.4bn beside a headline of $122B is a reader's first reason to distrust both.
The 318 withdrawals are inside both populations, so the difference is a constant
and not a discrepancy.

221 employers lose every funding record they had. That is the correct outcome —
each was on the tracker for a takeover it was on the receiving end of — but it is
a visible change, which is why the employer count is on the page.

### What landed, and the one row that missed

Run **30605363355**, 04:54-05:10Z, 318 withdrawals, **zero failures**. Verified
by re-fetching every planned signal id from the live API afterwards: **0 of 318
still published**. Form D records 3,013 -> 2,695 and Form D money $87.375bn ->
$73.125bn, exactly the 318 rows and $14.250bn planned.

**The employer row missed by 31 and stays on the page.** 32 funding records worth
**$3.55bn** landed between the projection and the run — a historical gnews/gdelt
backfill slice plus a night of collection, while the ticket sat behind
`enrich` and `correct-layoff-scope` in the writer queue. Same cause as the 998-row
correction's $10bn gap in July, and the same handling: the projection stays
visible beside the result. It is also why the `funding=1` view fell only
$10.70bn ($118.416bn -> $107.713bn) while the aggregate the dashboard prints fell
the full $14.25bn: the aggregate snapshot was taken after those 32 had landed and
the `funding=1` snapshot before.

### Withdrawn, not revised

The stored figure is what the filing prints. What is wrong is that it is money
raised at all, and there is no smaller true number to revise it to; inventing one
is the thing this tracker exists not to do. `retract.py` marks the row
not-current with a reason per rule, so nothing is deleted and the corrections
page can still count them.

### Two things about the machinery

**The share that stops the run is 30%.** Measured withdrawal is 11% of the
published Form D rows. A truncated or wrong-quarter archive reads as "none of it
qualifies", which is indistinguishable from a result — `correct_form_d.py` learnt
this first and the same guard is here.

**A run of failures stops the pass.** The host fell over twice on 2026-07-30
(~6 min and ~21 min). Withdrawals go one at a time with a pause,
`retract.retract_remote` retries 5xx on its own, and five consecutive failures
end the run rather than turning one outage into 300 failed requests. Re-queueing
is safe: an already-withdrawn row is skipped.

### The tense test was scoped wrong and would have made a true sentence vaguer

`tests/test_corrections_page.py` read every entry as ONE string. That was
harmless only while all the entries shared a status. The moment a **pending**
entry joined two applied ones, `'The badge is now "Headcount Not Stated"'` — a
true past-tense sentence in a correction that ran on 29 July — failed the
never-write-a-pending-correction-in-the-past-tense check, and the only route to a
green suite was to make a true sentence vaguer. **That is the page rotting to
satisfy its own guard.** Both tense tests are now per entry, both phrase lists
gained the wordings this correction reaches for, and a new test refuses any
status that is neither `scheduled` nor `applied` — a third value would fall out
of both checks and be the one way this page can disagree with the data in
silence.

One smaller trap in the same place: the TENSE markers must be `//` comments and
never `/* */`. The tests strip the first and read the second as page copy, so a
block comment quoting the future past-tense wording fails the build.

---

## 2026-07-31 — Spain states both directions, and a board renewal states both about the same person

`collectors/spain_borme.py`, one new dormant Sunday slot in
`collect-structured.yml`, one fixture, **45 new offline tests**. Keyless, no
model, **$0**. It is the fifteenth live collector and the **second source in
this tracker that reports a DEPARTURE** — the only other one is
`czechia_ares`.

Every number below was fetched live from `www.boe.es` on 2026-07-30, and the
volume figures come from a real run over seven cached publication days rather
than from a projection.

### Why Spain, out of fourteen registries asked

The question the sweep asked was narrower than "does it have an API": **does
the source STATE a director change as a typed, dated event, or would we have to
infer one by diffing two snapshots?** Diffing is refused here — it is what
killed Korea's roster endpoints and Estonia's daily file, because a date the
source never stated is a figure we invented — and that one question sorted all
fourteen. The full ranking, with what each was measured at, is the
`THE 2026-07-31 REGISTRY SWEEP` block in `source_registry.py`.

Spain won it outright. BORME Section A is the bulletin every Spanish commercial
register publishes its inscribed acts in, it publishes every business day, it
needs no key, and it prints each act under a **fixed heading** and each office
under a **fixed abbreviation**:

```
353679 - SIBAN ASISTENCIA INDUSTRIAL, SOCIEDAD LIMITADA.
Ceses/Dimisiones. Representan: AROSA BELASTEGUI JON.
Nombramientos. Representan: MARC BAIGET MORENO.
Datos registrales. S 8 , H VI 13948, I/A 18 (20.07.26).
```

`Nombramientos`, `Ceses/Dimisiones`, `Revocaciones` and `Reelecciones` are the
register's own words for the direction, in the same way Item 5.02 and SEBI
Regulation 30 are, so no model reads anything.

### The population is not the bulletin, and Spain gives nothing to threshold on

| | per day | per year |
|---|---|---|
| company entries in Section A | ~2,230 | — |
| entries carrying any leadership act | ~1,600 | — |
| **board-grade acts** (Presidente, Consejero, Secretario, …) | **494** | **123,455** |
| **consejero delegado acts — what is collected** | **49** | **~12,700** |

123,455 rows a year is the Companies House failure (5.7M companies) and the
Estonian one (74,000 appointments a year, 86% at one-person `OÜ`s) for a third
time. **And Spain publishes NO headcount anywhere** — not in the bulletin, and
the accounts that would are deposited with the Colegio de Registradores and
sold. So the UK's pay-gap roster, Czechia's RES band and Estonia's annual-report
FTE figure all have no Spanish equivalent.

**The filter is therefore the OFFICE, drawn where Japan and Korea are drawn.**
`edinet_japan` collects one clause of forty-four — 代表取締役の異動, the
representative director alone — and `opendart_korea` collects 대표이사변경 for
the same reason: the office that can bind the company is a different kind of
event from a seat on a board. Spain's equivalent is the **consejero delegado**,
the director the board has delegated its powers to under article 249 of the Ley
de Sociedades de Capital.

Widening to `Presidente` and `Consejero` is one entry in `OFFICES` and eight
times the volume. It was declined **with that number** rather than left
unconsidered, and `OFFICES_DECLINED` keeps the refusal as data so the claim is
checkable.

### The trap that decides whether this source tells the truth

**A Spanish board renewal is inscribed as a total cancellation followed by a
total re-appointment.** One paragraph reads
`Ceses/Dimisiones. Con.Delegado: X. Nombramientos. Con.Delegado: X.` — same
person, same office, same inscription date, both directions, and nobody left.

Measured over the seven cached days: **58 of 373 person-company-date keys carry
both directions and 46 of them carry the same office too. 92 of 432 candidate
rows — 21% — were halves of such a pair.** A collector that stores both halves
reports a Spanish leaving rate that is not real. This is the Czech
`datumVymazu` finding in a new shape, and it was found by reading real
paragraphs rather than by reading the format.

**And the obvious over-correction is wrong in the other direction.** SPLA SA
ceased Javier Muñoz Gómez as `Con.Delegado` and appointed him `Cons.Del.Sol` on
one date. A sole delegation becoming a joint one is a change the register made,
not one we inferred, so collapsing on the person alone would delete it.
`drop_reinscriptions` keys on **(registry entry, person, office, date)**, and
both cases have a test.

### Three more things a later session would otherwise re-find

**The nice URL is forbidden.** `/diario_borme/xml.php?id=` serves this exact
text as clean XML, and `boe.es/robots.txt` says `Disallow: /diario_borme/xml.php?`
in as many words. `/diario_borme/txt.php?id=` carries the identical
`<h5 class="articulo">` / `<p class="parrafo">` structure and is disallowed by
no line of that file. The open-data API is not a way round it: it serves
SUMMARIES only, and `/datosabiertos/api/borme/id/{ident}` answers 404 `No se ha
localizado la operación requerida`. A test asserts the collector never builds a
request out of the XML path.

**The date is the inscription date and its year has two digits.** Every entry
ends `Datos registrales. … (22.07.26).`, which is when the registrar inscribed
the act; the bulletin publishes it about a week later — measured over 7,281
entries, **median 7 days, p90 8, p99 11**. So `published_date` is the
inscription and the publication day is only how the run finds it, which means
**a Spanish row is a week old by construction** and the sources page says so.
`(03.02.97)` read as `2000 + 97` is the year 2097; the pivot is the publication
date it must precede. One such entry appeared in the 7,281, and eleven were
inscribed more than a year before publication and are declined with a count for
the reason `czechia_ares` declines its own seven.

**The last item of every day's Section A is not a province.** It is
`ÍNDICE ALFABÉTICO DE SOCIEDADES`, an A-to-Z pointing back at the province
files, and it parses to zero company entries. It is skipped by title AND by the
`-99` suffix, because either alone would start counting an index as an empty
province the day the other changes. The per-file emptiness floor that found it
was **removed**: province files run from a handful of entries in Soria to 653 in
Madrid, so a per-file floor fires on a small province rather than on a broken
parser. The floor is per DAY.

### The scrubber that had never been tested, because there was nothing to strip

BORME publishes a NAME and nothing else for these acts — no birth date, no
address, no identifier, unlike the Czech and Estonian files. The first
`scrub_person` therefore passed the name through whole, and the fixture's
invented probe entry went straight into the headline, the summary and the
stored signal:

```
FUGA DE DATOS SL: SOLER MARTI CARMEN (nacida el 04.06.1975, DNI 12345678Z,
domicilio en Calle Mayor 1, 28013 Madrid) appointed Consejero delegado …
```

**A scrubber written against a source that publishes nothing private is a
scrubber that has never been run.** Two rules now, both checked against the real
bulletin: everything from the first parenthesis onward is dropped (**no holder
string in 534 real ones carries a parenthesis at all**), and a name still
carrying a DIGIT is refused rather than trimmed (two of those 534 did —
`GRUPO MOORE 2019 SL` and `PUERTO 58 SOCIEDAD LIMITADA` — and both are companies
`is_legal_person` had already declined).

Names are stored exactly as the register prints them and are **never
reordered**. BORME writes some people surname-first (`AROSA BELASTEGUI JON`) and
some given-name-first (`MARC BAIGET MORENO`) and no field says which; guessing
would rewrite a person's name to make a column look tidy. Diacritics round-trip
on real names: `MUÑOZ AÑÓN JOSÉ MARÍA`, `GOIKOETXEA ARRIETA IÑIGO`.

### The real run

Seven publication days, 2026-07-22..07-30, no network at run time (the bulletin
was cached first so the parser could be iterated without re-fetching 213
documents):

```
213 province files, 15,642 company entries, 469 chief-executive acts read
340 stored (141 arrivals, 199 departures)
declined: 92 halves of a cancel-and-re-inscribe pair, 34 legal-person holders,
          40 re-elections, 0 over the 365-day backlog, 1 with no inscription date
```

**All 340 build a Signal through `validate.build_signal`, 0 rejected, all
`verified`**, and 155 of them carry a city — Madrid 87, Barcelona 47, Seville
10, Malaga 9, Cordoba 2 — because the province goes through
`vocab.normalize_city`, which never invents one. 209 distinct employers in a
week.

A `Reelecciones` is declined and counted: the same person continuing in the
same office is the register recording that leadership did not change. A legal
person holding the office is declined too — the delegation is often to a
company (`BLUEMED EXPERIENCES SL`) which then names a natural person to
represent it under a separate act.

### Shipped DORMANT

The Sunday cron in `collect-structured.yml` is **commented out**. Sunday is the
last day of the week no other database writer holds, so Spain is the seventh
and last weekly structured slot the schedule has room for. Arming it is
uncommenting one line, and the gate is the standing one — a human reads a real
dry run first:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
  -f inputs_json='{"source":"spain_borme","dry_run":"true"}' \
  -f reason='first real BORME run'
```

A run is ~210 requests and 10 to 25 minutes of wall clock (www.boe.es answers a
province file in one to eight seconds), which is inside
`writer_queue.LONG_HOLD_MINUTES` with room. BORME's archive is permanent and
the summary API answers any past date, so a missed week is recovered by widening
`days` and nothing is lost.

### Where this brief was wrong about the repo

* **The brief said `leadership_change` is ~3,224 rows, essentially all US 8-K
  Item 5.02.** It is **5,384** as of this session, and six registry collectors
  already existed before it — `companies_house`, `bse_india`, `edinet_japan`,
  `opendart_korea`, `czechia_ares` and `estonia_ariregister`, four of them
  built the day before. The pillar was not untouched; it was six countries in.
* **The brief said the Companies House, EDINET and OpenDART keys are "in
  GitHub Secrets, partially used by the SIBLING repo".** All three are in THIS
  repo's `collect-structured.yml` and all three have live collectors here.
* **`estonia_ariregister.py` already named Spain as a register refused for
  size.** There is no triage entry behind that line — TECHLOG's own 2026-07-30
  entry says so explicitly — and the reason it gives (too many companies) is
  right about the bulletin and wrong about the office filter.

### Numbers

| | |
|---|---|
| tests | **+45**, suite green at **2,576** with 202 subtests (2,526 at the moment this branch was written; another session landed 50 more before it merged) |
| new collectors | 1, keyless, `as_classified`, **$0** |
| live collectors on the sources page | 14 → **15** |
| Spain, real 7-day dry run | 340 events, 141 arrivals, 199 departures, 0 rejected by validate |
| projected | ~49 a publication day, **~12,700 a year**, ~209 employers a week |
| registries swept | 14, of which 1 built, 5 measured-and-roadmapped, 8 refused with a reason |

`data/talent_intel.db` was never written: the dry run ran entirely in memory
against a cached copy of the bulletin.

---

## 2026-07-30 (later) — why were we paying for two model passes per story?

Nobody had asked. Extraction and the read-through were **$47.90 and $47.39 a
month** at full worldwide coverage — 94% of a $100.99 bill, for reading every
story twice. The answer turned out to be worth **$41.70 a month** and to cost
nothing at all in what gets stored.

### What the second pass actually buys: one field, and no facts

Established structurally, not by sampling, because the structure settles it:

- `interpret()` is asked for exactly one key and `interpret_late()` writes
  exactly one attribute, `signal.talent_readthrough`.
- It is never given `company`, `country`, `pillar`, `funding_amount`,
  `signal_direction` or `confidence` to return, and `_accept` refuses any
  sentence carrying a figure or place not already in the extracted facts.
- **It sees LESS of the source than extraction does**: 500 characters of teaser
  against extraction's 4,000. It is not "the model finally reads the article".
  It cannot know anything extraction did not.
- Extraction already produces that same field, for free, in the call that was
  already paid for. `SCHEMA_HINT` has always asked for `talent_readthrough`.

So the field-by-field A/B on the six fields that decide a record would return
**100% agreement by construction**. There is nothing to measure there, and
`tests/test_second_pass_is_conditional.py` asserts the structure instead.

What is left is prose quality on one field. That is worth something — but it is
worth it on the records where the free sentence is actually bad.

### How often is the free sentence bad? 8.8%

The corpus is real production prose: **4,171 rows carrying the sentence the
fused deepseek call wrote before the split, and 452 carrying claude-sonnet-5's
after it.** Against `prompts.weak_reasons`, five free deterministic tests:

| | rows | flagged | Latin-script subset |
|---|---|---|---|
| deepseek, fused | 4,171 | **8.8%** | 8.7% |
| claude-sonnet-5 | 452 | 2.2% | **1.0%** |

**Nine to one on comparable text.** That gap is the evidence the triage
measures the thing it claims to rather than flagging at random — the same tests
run over the frontier model's own output almost never fire.

The breakdown, and it is not what the earlier A/B suggested:

    hedged             5.6%    "suggests upcoming hiring in biotech roles"
    short              2.5%    "Brussels Airlines appoints a new CEO;
                                executive leadership changes."
    adds-no-fact       1.4%
    restates-headline  0.2%
    storage-code       0.0%

Mean headline overlap is **0.150 for deepseek against 0.158 for Sonnet** —
statistically identical. So "deepseek RESTATES the headline", which is what the
2026-07-30 A/B concluded from one sample and which this file has been repeating
since, **is not a property of the corpus.** What IS true: deepseek's sentences
are thinner (127 characters against 194) and hedge one time in fifteen. That is
a real but modest difference, and it is worth a frontier call on one record in
eleven rather than on all of them.

### The triage, and its two refusals to be clever

`pipeline/prompts.weak_reasons(sentence, headline)` — five regex-and-set
operations, no model, no network, no database, asserted.

1. **Anything it cannot score goes to the model.** Chinese, Japanese and Thai
   put no spaces between words, so a word split returns one enormous token and
   both overlap tests then measure an accident. Below a token floor, or above a
   word-length ceiling, the tests are skipped and the record buys the frontier
   sentence. That fails toward quality, and it spends the budget on exactly the
   languages the coverage gap is made of. (All four sentences the first draft
   flagged in the Sonnet corpus were Chinese, Arabic and Hebrew, and all four
   were fine — that is how the blind spot was found.)
2. **Extraction's own sentence must pass `ungrounded_reason` before it stands.**
   That check only ever ran on the PAID sentence, because the free one was
   always overwritten. Keeping the free one without it would have quietly
   reopened the invented-figure hole the split closed — a sentence with no other
   defect can still carry a number that is not in the source.

`TIT_READ_ALWAYS=1` restores the unconditional call in one variable.

**What this does NOT measure, and the code says so.** These tests find DEFECTS,
not dull prose. A sentence can pass all five and still be less useful than
Sonnet's extra 67 characters of context about what the company does. No regex
scores that. The honest claim is "this catches the defects".

### Two arithmetic bugs found on the way, one of them dangerous

**The read-through volume was `rows / read_throughs` (0.671) multiplied by
READS** — two different denominators. It overstated the read-through line by
28% and understated read-late's saving by the same.

**The funnel took the ledger WHOLESALE the moment any collector had data**, and
exactly one did. `national_press` — the hungriest collector, 249 reads a run —
plus gdelt and the SEC pair silently vanished from the projection, and the bill
fell from $75.99 to $57.24 on nothing but four missing collectors. **A number
that looks more authoritative and is less complete is worse than the estimate it
replaced.** The ledger now wins per collector, the seed fills the rest, and
every row is printed `measured` or `seeded`.

### The bill now, with google_news's real funnel in the ledger

Demand is bigger than the seeded log suggested: 726 gate survivors a day from
google_news alone against 306, so full coverage is **1,282 reads/day**.

| configuration | gate | extract | read | total |
|---|---|---|---|---|
| full coverage, read-late | 5.70 | 47.90 | 47.39 | **100.99** |
| second pass CONDITIONAL | 5.70 | 47.90 | 5.69 | **59.29** |
| + extraction on `gemini-2.5-flash-lite` | 5.70 | 7.40 | 5.69 | **18.79** |
| + read-through on `haiku-4.5:batch` too | 5.70 | 7.40 | 1.42 | **14.53** |
| leadership offloaded (61% of reads left) | 5.70 | 29.32 | 3.48 | 38.50 |
| + free extraction takes 33% of funding | 5.70 | 19.58 | 2.33 | 27.61 |
| all of it, cheapest models | 5.70 | 3.03 | 0.58 | **9.31** |

**$5 IS NOT REACHABLE. $9.31 is the floor with every lever stacked**, and that
floor assumes two model swaps that have not been quality-tested and a leadership
pillar that is not yet free. The gate alone is $5.70 and is not optional: it is
how we know which 1,282 of 3,156 daily candidates are worth reading. Any target
at or below $6 is a target below the cost of *looking*.

### Funding is where free extraction pays, and by fifteen times

Measured over the 289 stored funding rows on the paid path: `cheap_extract`
closes **33.2% from the headline alone**, against **2.2%** across the whole paid
path. Funding headlines state every field, which is exactly why. 88% of them are
Latin-script, so an English-first parser is not the ceiling people assumed.

The declines are where the remaining work is, and they are precision guards
firing rather than gaps: `LOOPTWORKS, INC raised $3.6M in a private placement`
(the comma in the name span), `Flourish Health Raises $26M Series A to Scale
High-Acuity Youth Psychiatric Care` (title-case, so only a single token before
the verb is trusted). Both are fixable with care and neither should be touched
without a hand-check at the existing 31/31 bar.

### The cap, raised because it was EARNED

`READTHROUGH_CAP` 75 -> 88, google_news 45 -> 129, gdelt 8 -> 9. A read costs
$0.00139 instead of $0.00278 now, so the same $25 buys twice as many. That rule
is the first line of the comment: **raise it when the money per read falls, and
not before.** Still rationing — $25 buys 461 reads a day against demand of
1,282 — and `candidate_rank` is what makes 36% of demand buy more than 36% of
the coverage.

---

## 2026-07-30 — worldwide coverage priced honestly: $75.99, and the cap goes down

> **SUPERSEDED THE SAME DAY by the entry above.** Two arithmetic bugs (the
> read-through volume factor, and the funnel dropping four collectors the
> moment one had ledger data) and google_news's real funnel put full
> coverage at $100.99 rather than $75.99. The reasoning below stands; the
> totals do not. `python3 cost_projection.py` is the authority.

The brief was "pull all the countries in the world, pull the missing sources,
run it for $5"; the allowance was then raised to $25 mid-session. Neither
number is met by full coverage today. **Full worldwide coverage costs $75.99 a
month at current models. That is the finding, it is measured, and the rest of
this entry is what closes the gap and what does not.**

Re-derive all of it rather than trusting this entry:

```bash
python3 cost_projection.py            # live prices; --offline for the snapshot
```

### First, the thing that was actually broken

`spend.py --enforce` had taken every collect job red at $9.47 of a $10
allowance, and NOTHING had been collected since 21:47 — including the SEC, UK
pay-gap, ATS, BSE, EDINET and DART collectors, which derive every field from a
column and call no model, and `cheap_extract`, which closes records from stated
text for $0. Halting all of that to protect a budget none of it spends is a
self-inflicted outage.

`spend.py --degrade` replaces it on `collect.yml` and `collect-press.yml`. It
never fails the step. Past the ceiling it writes `TIT_PAID_READS=off` into the
job environment; `classify()` refuses **before the gate**, so not one token is
spent, and raises `BudgetExhausted` — a `BudgetDeferred`, so the candidate
defers UNMARKED and a later run reads it. Hitting the allowance costs depth,
never coverage. The run says so twice: a summary line, and `DEGRADED: monthly
allowance spent` in the health ledger where `ops_status` and the health page
already read it. A degraded run reports `degraded` (the page is shallower than
usual), not `ok`, and deliberately not `every candidate rejected` — no guard
rejected anything, and sending a human to hunt a broken classifier over a
budget decision is worse than saying nothing. `--enforce` stays for
`tripwire.yml`, whose only action is a paid query.

`MONTHLY_ALLOWANCE_USD` 10 -> 25, the owner's number.

### The measurement the whole session rests on

The funnel, from two real runs (`30571205733` collect, `30532073727` press):

| collector | to classifier | gated | kept by the gate | read | UNREAD |
|---|---|---|---|---|---|
| national_press | 1,148 | 627 | 249 | 200 | **49** |
| google_news | 640 | 498 | 153 | 153 | 0 |
| gdelt | 74 | 40 | 26 | 26 | 0 |
| SEC pair | 17 | 3 | 3 | 3 | 0 |

**The 49 are the coverage gap, and reading their headlines is the whole
argument**: Chinese, Hebrew, Serbian, German, Vietnamese, Korean. Germany does
not have twelve rows because a German feed is missing or because a filter
rejects German stories. It has twelve rows because German stories fetch fine,
pass the free prefilter, survive the gate, and then queue behind a per-run
ceiling. Those four numbers now land in `source_health`
(`candidates`, `gate_calls`, `gate_rejects`, `budget_deferred`) so the next
session measures this instead of finding a workflow log before it expires.

Full coverage means reading every gate survivor: **862/day, 25,860/month.**

### What it costs, and where the money actually is

Unit prices live from OpenRouter, token counts from exact character counts,
calibrated ×1.16 against what the provider really charged over nine runs:

| stage | model | $/call | $/month at full coverage |
|---|---|---|---|
| gate | gemini-2.5-flash-lite | $0.000051 | $4.15 |
| extraction | deepseek/deepseek-chat | $0.001059 | **$31.69** |
| read-through | claude-sonnet-5 | $0.002000 | $40.14 |
| | | | **$75.99** |

Two things in that table were not what the brief expected.

**The gate is 5% of the bill, not the lever.** Batching it was briefed as "the
thing that makes screening everything affordable", on the reasoning that the
per-candidate cost falls by roughly N. It does not, and the reason is
arithmetic: `GATE_SYSTEM` is 217 tokens and the item text averages ~287, so the
shared prefix is 43% of a gate call rather than the 86% it is for extraction.
Batching ten candidates saves ~40% of $4.15, which is **$1.66 a month**. It is
worth doing eventually and it was not done here, because it needs the candidate
loop restructured into a free pass and a paid pass, and $1.66 does not buy that
risk in the same session that touched the write path twice.

**Extraction is the largest single line — larger than the frontier
read-through.** 2,754 of its 3,100 input tokens are the byte-stable
`SCHEMA_HINT`, and *no endpoint serving `deepseek/deepseek-chat` publishes an
`input_cache_read` price*, checked again today. So prompt caching — briefed as
"likely the single biggest win" — is worth **exactly $0** on the current slug.
That finding was already in the repo and it still holds.

### The levers, measured

| | $/month | note |
|---|---|---|
| full coverage, today | 75.99 | |
| read-late **(shipped)** | -5.79 | on today's caps; -~12 at full coverage |
| extraction -> deepseek-chat-v3.1 | -11.17 | its prefix DOES price a cache read, ~0.5x |
| extraction -> gemini-2.5-flash-lite | -26.79 | the model we already trust as the gate |
| read-through -> claude-haiku-4.5 | -20.07 | |
| read-through -> haiku-4.5:batch | -30.10 | 24h latency; freshness is what this sells |
| both cheapest together | **19.09** | under $25, and both are unverified swaps |
| leadership pillar offloaded | 52.30 | 67% of reads remain |
| that, plus both cheapest | **14.16** | |

**read-late shipped and is the one saving that cost nothing.** Measured over
the nine priced runs: **477 interpretations bought, 320 rows stored** — a third
of the most expensive call in the pipeline went to records that a `validate`
rejection or one of the two dedup layers settled a moment later, all three of
which are free. So `classify(interpret_now=False)` returns extraction only,
`store.duplicate_verdict()` asks both dedup layers without writing, and
`run_collect` buys the sentence last. Safe because `content_hash` never reads
the read-through (the fingerprint the dedup layers agreed on cannot move
underneath them) and `build_signal` checks that field only for emptiness — the
figure and place grounding is `_accept`'s job and runs on whatever sentence
comes back, whenever it is bought. Both properties are asserted, not reasoned
about.

### The cap goes DOWN, 200 -> 75, and that is not a retreat

The ceiling that binds moved from the RUN to the MONTH. A cap of 200 does not
spend $75; it lets demand — 862 reads a day — spend $75. The allowance would be
gone in ten days and `--degrade` would switch paid reads off for the other
twenty. **Ten good days and twenty thin ones is worse coverage than thirty even
ones**, and much worse for a tracker whose promise is that it is current.

75 is `national_press`'s share of what $25 buys after the gate's own $4.15.
`collect.yml` gets google_news 45 and gdelt 8 by the same split; the SEC pair
keep 40, because rationing a collector that finds two filings saves nothing and
would one day bind on the run that finds fifty. The bound in
`tests/test_locale_rotation.py` now carries this arithmetic and points at the
program that re-derives it.

### What makes rationing acceptable: a country's second story never outranks another country's first

`candidate_rank` already scored thin and empty countries up, and it was not
enough, because scoring is per-candidate and the shortage is per-COUNTRY. A busy
day in one thin country produces forty candidates that all score
`W_COUNTRY_EMPTY`, and forty identical scores in arrival order is forty reads
spent on one place while thirty others with a single story each wait behind.
Of the 55 countries that are neither US nor GB the **median holds one row**, so
what is scarce is the FIRST row about a place.

So ranking ends in a round robin over the candidate's country hint. A quota was
refused — most countries have nothing on most days, so a quota spends the budget
on absence — and a floor is the same problem in a politer form. A round robin
reserves nothing, wastes nothing when a country is silent, and needs no number
to tune. Countries are visited in the order their best candidate scored, so
merit still decides who goes first within a pass. Still only a permutation. The
run log gains the number it exists to move: what share of the read budget the
busiest country takes, before and after.

**A capped run is therefore not a random 75 of 249. It is the 75 that buy the
most countries.**

### What the non-US share actually is, and why the headline number misleads

10.8% of stored rows are neither US nor GB — and that number is about the FREE
collectors, not the paid path. `uk_paygap` (4,761 GB), `sec_execcomp` (3,910
US), `sec_edgar` (3,801), `sec_form_d_bulk` (2,998) and `companies_house` (437
GB) are US/UK filing regimes by construction. The paid news path is already
overwhelmingly not-US:

| collector | rows | US/GB | elsewhere |
|---|---|---|---|
| google_news | 388 | 26 | **362** |
| national_press | 205 | 25 | **180** |
| gdelt | 53 | 20 | 33 |

So the answer to "make it worldwide" is not a filter change and not a feed: it
is more paid reads, spread across more countries. That is what the cap and the
round robin decide between them.

### 43-language free extraction: not attempted, and why

`SCALE_WORDS_BY_LANGUAGE` solved amounts, but `cheap_extract`'s English
restriction is not one gate — it is `_RAISE_VERB`, `_UNCERTAIN`, `_DEAL_WORDS`,
`_NATIONALITIES`, `_SECTOR_DESCRIPTORS` and a capitalisation heuristic, each of
which would need a per-language pack held to the same precision bar. The
existing bar is 31/31 correct on a hand-check, and **there is no non-English
corpus in this repo to hand-check against** — `signals` stores headlines, not
the `raw_text` a parser reads. Shipping a multilingual extractor with no way to
measure its precision would be the exact mistake this project keeps writing
down: a saving claimed that was never verified. It is worth roughly $0.0032 per
record closed (both stages now, since a free close skips the read-through too),
so it stays on the list, behind a captured non-English corpus.

### What was refused

No saving was claimed for prompt caching on a slug that prices none. No
extraction model was switched — `ab_models.py --extraction` was built instead,
sending the production `SCHEMA_HINT` and scoring agreement field by field on
the six that decide what a record IS, because a cheaper model that quietly
loses `country` on a fifth of records reads as a saving now and a coverage
regression later. The read-through model was not switched. The gate was not
batched for $1.66. And $25 was not reported as met by rounding $75.99 down.

---

## 2026-07-30 — the report every session reads could not see a red run

The owner watched a dozen "Run failed" emails arrive across both trackers and
asked why no session had noticed. The honest answer is structural: **nothing
told a session about a red run.** `CLAUDE.md` sends every session to
`ops_status.py` first, and that file reports on data, collectors, the writer
queue, guardrails and link rot — none of which knows whether the workflow that
produced any of it exited non-zero. So a session opened, read ALL CLEAR, and
worked for hours beside a repo whose `tests` had been red on main, whose
`enrich` had died on a read timeout, and whose sibling had ten red `Tests` runs
in two hours.

The constraint that shapes the fix is that `ops_status.py` must stay offline,
dependency-free and key-free. That is not an accident of history —
`writer_queue_runs.py` exists as a separate module *for exactly this reason*,
and `tests/test_health_digest.py` already asserts that ops_status imports
nothing outside the standard library, because it must run before any venv
exists. Being offline is precisely what stops it from seeing a run. So the
answer is a second command, not a section: **`ci_status.py`**, run beside it.

```bash
python3 ops_status.py     # the data.  offline, no deps, no keys
python3 ci_status.py      # the runs behind the data.  needs gh + network
```

### What it reports, and why each part earns its place

- **RED NOW** — for every workflow that has failed at all, the newest run on the
  repo's default branch, asked one workflow at a time. Red *now* is the state
  that persists and the only claim worth exiting 2 over; a failure with a green
  run after it has already been answered by somebody. Deliberately **not**
  bounded by the window: a dispatch-only workflow that failed a week ago and has
  not run since is exactly the thing nobody notices.
- **the last 24h** — every red run in the window including ones since recovered,
  so a flapping job is visible before it becomes a permanent one. Listed, not
  alarmed.
- **EVICTED** — `cancelled` with **zero jobs**, the displacement signature this
  project has been bitten by repeatedly and which is invisible in the GitHub UI.
  `writer_queue.never_started()` is imported rather than restated, so the two
  tools cannot drift about what an eviction looks like.
- **the writer queue**, from `writer_queue.summary()` — printed as context and
  explicitly **not** counted into this tool's exit code, because
  `ops_status.py [2b]` already exits 2 on exactly those and one problem raising
  two alarms is how an alarm stops being read.

Exit codes: **0** green, **2** something needs a human (matching ops_status so
the two compose), **3 could not check**. Three is the whole point of three. No
`gh`, no credential, no route to github.com must never render as an all-clear —
that is the same false-healthy failure this repo keeps finding, and it is the
one an exit code can actually prevent. `tests/test_ci_status.py` asserts for all
three of those cases that the output contains COULD NOT CHECK and does **not**
contain "All green".

### The naive version of this is useless, and measuring it is what showed that

Written to the brief's literal shape — "any run that ended cancelled with zero
jobs" — the first working version produced **24 evictions and a 28-item ACTION
NEEDED list**, which is the same as no alarm at all. Reading them one by one:

| | |
|---|---|
| evictions found in the run list | **24** |
| already booked as orphans in `data/writer_queue.json` (all resolved, 2026-07-28/29) | **17** |
| `drain-writers` losing its own pending slot | **6** |
| `deploy-robots` losing its own pending slot | **1** |
| **unrecorded evictions of a database writer** | **0** |

The 17 are `ops_status [2b]`'s to raise and it does. The other 7 are **not
losses**: neither workflow is in the `talent-collect` group, and
`drain-writers.yml`'s own concurrency comment says why — a tick that loses its
slot reconciles from scratch next time and costs a cycle, no data. So an
eviction is an alarm only when it hit a member of the writer lock group, inside
the window, and the queue has not already booked the run id. The lock-group
membership comes from `writer_queue.lock_group_workflows()`, which already
parses the workflow files. That took the list from 28 items to **3**, all real.

For the **sibling** the lock groups are not knowable from here — the two repos
share no code, by the owner's rule — so `lock_group=None` means every eviction
there is reported. Silence would have been a guess in the wrong direction.

### What it says today, 2026-07-30 22:49 UTC

**dk-forge/talent-intelligence-tracker** — RED NOW on main: `collect national
press` (run 30583087376), `deploy-robots` (30577050236), `enrich`
(30586211637). 13 red runs in the last 24h across 5 workflows; `tests`,
`collect`, `retract`, `backfill-funding-bulk`, `correct-layoff-scope` and
`drain-writers` have all gone green since. No unrecorded writer eviction.

**dk-forge/ai-layoff-tracker** — nothing red now; 10 red `Tests` runs inside two
hours, all recovered. One eviction ("EDGAR history sweep (rotating)",
30393987230) sits outside the 24h window.

Exit 2, three items. That is the state the previous session worked beside for
hours believing it was all clear.

### The generalisation, not a fourth wrapper

`writer_queue_runs._gh` was reused rather than copied — this repo already grew
two `registrable_domain` implementations out of that habit. Three changes, all
small, none altering the drainer's behaviour:

- `run_list(...)` is the public query builder (`--status`, `-b`, `-w`, `--json`
  fields), and `fetch()` is now `attach_job_counts(run_list(...))`. The filters
  matter: on a repo doing forty runs an hour an unfiltered list of 100 covers
  about four hours, so filtering server-side is the difference between seeing a
  day and thinking a day was quiet.
- `attach_job_counts()` split out, so a caller holding a cancelled-only list can
  buy job counts for just the runs inside its window. Unwindowed this was 80 API
  calls and **28 seconds** at a session-start prompt; windowed and with the
  independent reads issued in parallel it is **~9 seconds**. A check nobody
  waits for is a check nobody runs.
- **`GhUnavailable(RuntimeError)`** — raised for a missing binary, a missing
  credential or an unreachable host, so "could not check" is nameable. It
  subclasses `RuntimeError` on purpose: every existing caller catches that and
  keeps its behaviour, including `attach_job_counts`'s unknown-is-not-zero
  fallback.

**Where the brief was wrong when the code was read:**

1. It described `_gh` as already hardened — "retries 429/5xx/timeouts, fails
   fast on 4xx", which is accurate. But `subprocess.run(["gh", ...])` raises
   `FileNotFoundError` when gh is not installed, and nothing caught it. The
   no-gh case was not merely unhandled by a new tool; it was **a traceback in
   the existing drainer**, which is exactly the "not a traceback" outcome the
   brief asked for. Fixed at the source rather than in the caller.
2. It warned that rebuilding `docs/TECHLOG.md` from local HEAD would drop a
   210-line section. In this worktree HEAD is **66 commits behind origin/main**
   and `docs/TECHLOG.md` is nevertheless **byte-identical** between them (blob
   `9f032d8b`): the whole divergence is `data/` plus `pipeline/vocab.py` and
   `tests/test_funding_amount_parsing.py`. Rebuilt from `origin/main` regardless
   — being right by luck is not a procedure.
3. The stated test baseline, 2,406, is correct. `CLAUDE.md` said **1,807**, which
   had been stale for some time; corrected to 2,435 alongside this work
   (+29 in `tests/test_ci_status.py`).

### Where this should fire from — recommended, not built

The ritual in `CLAUDE.md` is the whole delivery so far, and it is deliberately
the only thing that always applies: it is one file, every session reads it, and
it needs no infrastructure. Beyond that, three options were considered and none
was added unilaterally:

- **A `SessionStart` hook in `.claude/settings.json`** — *the recommendation.*
  It fires once per session, costs the ~9 seconds already measured, and makes
  the check impossible to skip rather than merely documented. Its real drawback
  is honest: it only helps sessions working in this repo with that settings
  file, and it does nothing for a session in the sibling. Nothing that fires on
  every tool call was considered — `gh` on every call is slow, noisy, and would
  train people to disable the hook.
- **A scheduled workflow that opens an issue** — rejected. It needs somebody who
  reads issues, and it has the failure mode it is meant to fix: a watcher
  workflow that itself goes red announces nothing, and nobody notices the
  silence.
- **A git hook** — rejected. `pre-commit` running `gh` is slow and offline-
  hostile, and it fires at the moment work is finished rather than at the moment
  a session forms its picture of the repo.

The sibling repo would benefit from the same tool. Per the standing rule that
the two share no code, that is a **separate implementation in its own repo** and
a recommendation for the owner, not something done from here. Note that
`ci_status.py` already watches the sibling's runs from this side, which covers
the reporting need even while the sibling has no copy of its own.

---

## 2026-07-30 — the archived copy was already shipped, and no reader had ever seen one

**Plugin 1.56.0 -> 1.57.0.** The brief for this session said to add an "Archived"
link to the record cards. It was already there. `shortcodes.php` and
`dashboard.js` have both printed one since 1.43.0, conditional on
`archive_url`, and `.tit-archived` has had a rule in the stylesheet the whole
time. What was missing is that **nothing renders it**, and the reason is two
layers down from the markup.

Measured before touching anything, on the live page and the live API:

| | |
|---|---|
| `tit-archived` spans on the live dashboard (1.56.0) | **0** |
| rows on page 1 of `/query` carrying `archive_url` | **0 of 50** |
| a 200-row sample of live `reported` rows carrying one | **0** |
| six named employers whose LOCAL row has a snapshot, checked one by one on the live API | 6 rows found, **0 with `archive_url`** |
| pipeline database, current signals with a snapshot | **71** |
| `source_links` ledger, distinct URLs with a snapshot | **72** |

So the 72 captures from the 2026-07-29 archive runs are in the pipeline database
and have never reached WordPress. They cannot arrive with the row:
`pipeline/publish.py` deliberately keeps `archive_url` out of `FIELDS`, because a
row is built at classification time and its snapshot is taken afterwards, so at
publish time the column is always empty. It travels in `ENRICHABLE` instead, via
`enrich_published()` and the `/enrich` route. That path exists, is allowlisted at
both ends (`tit_enrichable_columns()` names it), and has evidently not carried
these values yet. **That is the actual blocker on this feature, and it is a
pipeline run rather than a plugin change.** Nothing in this commit can fix it.

### What the card footer says now

`29 Jul 2026 · Reuters` becomes `29 Jul 2026 · Reuters · Archived`. The owner's
decisions, applied:

- The word is **Archived**, one word, Title Case. It was lowercase `archived`.
  Not "Wayback": a brand name a recruiter has no reason to know, on a card that
  is already dense.
- `title="Archived copy at the Internet Archive"`. It was a 15-word sentence.
- **Subordinate to the source, at the same size.** It was 12.5px against the
  cell's 14.5px, which is smaller doing the job colour should do; a footnote to
  the row rather than a second link anyone would click. Now `font-size:inherit`,
  measured at 14.5px desktop and 13px in the card, in `--tit-mut` (#4a4d55,
  8.6:1) against the source link's #1a5fb4, with the underline dropped to
  `--tit-faint`.
- **"Lighter weight" is carried by colour, not by a numeral, and that is a
  deliberate refusal.** The source link is already weight 400. A sub-400 value
  resolves to Light on a variable system-ui face and snaps back to 400 on
  everything else, so the same word would be two different greys on two phones.
  `font-weight:400` is set explicitly all the same, because the Employer cell
  next door is 650 and an inherited weight is a bug waiting for a refactor.
- **Printed only where a snapshot exists**, which the markup already did. Kept,
  and now asserted in both directions rather than assumed.

### The separator, which is three characters and one measurement

The middot was a text node in the markup: `<span class="tit-archived"> · <a>`.
Below 860px each row is a card and this cell shares one wrapping line with the
rest of the meta, where the standing rule is that spacing separates items and
punctuation does not, written down in `dashboard.css` because a middot rendered
as a `::before` once landed at the start of a wrapped line and read as a bullet
list that had lost its text.

Two things came out of fixing it that are worth writing down.

**The first attempt did nothing, and looked like it worked.** The override went
into the `@media (max-width:860px)` block at line 486; the base `.tit-archived`
rules are at line 1034. Equal specificity, later wins, so `content:none` lost to
the `content` declaration 550 lines below it. The tell was that the sibling
declaration in the same block (`margin-left:10px`) DID apply, because nothing
competed with it. Measured in the browser: `getComputedStyle(span,'::before')`
still returned `"·"` at a 390px viewport. The rules are now all in one place with
the media block directly after them.

**The desktop cell wraps too, and the fix is a no-break space.** The Source
column is ~170px, so `Business Standard · Archived` does not fit on one line
either. `content:"\00A0\00B7 "` with `white-space:pre-wrap`: the no-break space
glues the middot to the publisher's name so it can never lead a line, and the
ordinary space after it is the only break opportunity, so the line divides as
`Business Standard ·` / `Archived`. Swept 72 synthetic source-name widths (3 to
26 characters, three glyph widths) against the real stylesheet in a browser:
**the separator led a line 0 times.** Six real outlet names (Inc42, Reuters,
TechCrunch, Business Standard, Ottawa Citizen, TheJournal) all place it between
50 and 92px into the line.

### What it costs

| | before | after |
|---|---|---|
| dashboard queries, cold | 12 | **12** |
| dashboard queries, warm | 0 | **0** |
| dashboard markup | 167,299 B | **167,760 B** (budget 168,000) |
| sources page queries, cold | 0 | **1** |
| sources page queries, warm | 0 | **0** |
| horizontal overflow at 390px, dashboard | 0 | **0** (`scrollWidth` 375 against a 390 viewport) |
| containers needing a horizontal gesture | 0 | **0** |
| elements past the right edge | 0 | **0** |

`TIT_DASH_QUERY_BUDGET` is untouched and did not need to move: `archive_url` is
already in the row `SELECT` the shortcode runs, so the link costs no lookup at
all. The N+1 tripwire (re-render after inserting 5,000 rows) still reports the
same 12.

The 461 markup bytes are almost entirely FIXTURE. Before this, the whole
dashboard render carried zero archived spans while 1,800 rows in the harness held
an `archive_url`, because every one of those is `materiality=routine` and the
default view sets them aside, so neither half of the conditional was being
tested. Six rows were added, dated today and inserted last so the sort
(materiality bucket, date, `row_id DESC`) puts them on page one deterministically.
Production pays about 110 bytes per row that actually has a copy. Headroom on the
byte budget is now 240 bytes, which is not room for anything.

### The sources page, and the number that has to keep reading correctly

72 of 12,970 cited documents is 0.6%. Shown beside a sparse link and no
explanation, that reads as a hole: 99% of the page apparently missing something
the other 1% has. It is not a hole. **12,735 of those 12,970 are SEC and GOV.UK
filings whose publishers keep them indefinitely**, and copying one of those to a
third party preserves nothing that is not already preserved. The perishable tail
is 235 URLs, which is what `archive-sources.yml` is pointed at and what
`ops_status [2c]` already calls this schedule's ceiling rather than a stall.

There were **no existing archive figures on the sources page** to put a sentence
next to. The brief said there were. The page had never mentioned the archive at
all, so the figures had to be built as well as explained.

The split is DERIVED, not typed. `data/sources.json` already carries a category
per collector, written by `build_sources_json.py` from the registry, and the
filing systems are exactly the categories ending in "filings" (Regulatory
filings, Government filings). A collector with no catalogue entry counts as
perishable, because that direction overstates what needs preserving and never
claims somebody else is keeping a document on our behalf. The alternative is the
mistake the collector map already shipped on this same page: a hand-typed list
with five of nine entries, which left three collectors running twice a day
rendering as "not yet reported".

Two things the paragraph refuses to say:

- **It does not claim an absence.** The ledger knows three states (archived,
  pending, confirmed to have no copy) and only the first reaches WordPress. So
  the page says what it holds and stops: "We record a copy we hold, never an
  absence we have checked for." `ops_status [2c]` separates "12,898 never
  answered about" from "0 confirmed absent"; flattening that into "99.4% have no
  copy" would be the page contradicting the status tool.
- **It does not print a coverage figure of zero.** The split is always said,
  because "most of what we cite needs no copy" is true of this corpus whether or
  not a capture has landed. The figure is printed only when there is one, because
  "0 of 12,970 (0.0%)" is a paragraph explaining a link that is nowhere on the
  site. Which is exactly today's state, and is why that branch exists.

Rendered against a corpus shaped like production it reads: *"Of the 12,970
documents cited on this tracker, 12,735 are filings held by regulators and
government registers... The other 235 come from news publishers and employer
sites, which unpublish stories, change their URL schemes and let domains lapse,
and those are the ones worth saving. 72 of all cited documents (0.6%) carry a
copy at the Internet Archive..."* The same sentences at 55 of 1,261 read
*"55 of all cited documents (4.4%)"*, which is the point: the archiver is running
again and the figure climbs on its own. Both are asserted.

### Tests

`tests/php/render_sources.php` is new and wired into `tests.yml`, which brings
the PHP harnesses to **7**. It renders the real page against three corpora
(sparse, caught-up, nothing captured) and one empty table, checks the arithmetic
adds up rather than trusting it (filings + perishable == corpus), and holds the
page to 1 query cold and 0 warm, because `COUNT(DISTINCT source_url)` over 15,711
rows is not free and this page cost nothing before.

`render_dashboard.php` gained a row-by-row walk of the first page rather than a
count of spans: a count passes on a render that prints the link everywhere and on
one that prints it nowhere. Both halves are the assertion, and the half that
matters is the rows WITHOUT a copy printing nothing at all.

Offline suite **2,406 passed**, unchanged. All 7 PHP harnesses green.

`tests/php/render_press.php` has never been in `tests.yml` and still is not; that
is a pre-existing gap and is filed separately rather than folded in here.

### Where this stops

**Not deployed.** The worktree branch is 40 commits behind `origin/main` and the
brief said not to push, so there is no ref carrying this change for
`deploy-plugin.yml` to check out. Live is still 1.56.0. The verification that
matters here is DOM measurement in a browser against the real stylesheet, not an
eyeballing: the browser pane returned blank screenshots all session, so nothing
in this entry rests on having looked at it.

And when it does deploy, **the dashboard will look identical**, because no live
row carries an `archive_url` yet. The sources page will print the split and say
"None of them carries a saved copy on this site yet". The link appears when
`enrich_published()` carries the 72 snapshots across, which is a writer-queue
action and not this commit's to take.

---

## 2026-07-30 — a million in forty-three languages, and the separator that goes with it

`$190 Milyon Dolar` was stored as **one hundred and ninety dollars**. Turkish
for a million was in no list the amount parser held, the token fell through to
no multiplier at all, and a nine-figure round landed on the money chart as
pocket change. Four rows went that way in a single collection, and the mechanism
is not Turkish: **575 national press feeds across 139 countries and 43 languages
had been wired into a parser whose scale vocabulary was English with a handful
of Romance words bolted on.**

`funding_amount_usd` is the only ARITHMETIC figure here. Every other number on
the page is a count of rows; this one is summed into a headline total and read
by the implausible-amount guardrail. So the failure does not look like a missing
row. It looks like a total that is wrong by a factor of a million on a page that
renders perfectly.

### What was wrong, in two halves

| | |
|---|---|
| scale word in any language but English | ignored — `$190 Milyon` -> 190 |
| `.` as a thousands separator | read as a decimal point — `$150.000` -> 150 |

The second is the mirror-image risk of fixing the first: `1,5 milyon` is one and
a half million, and an English-tuned reader makes it fifteen.

### The vocabulary is declared per language, and the test reads the catalogue

Not a word list that grows by whichever string last broke. `SCALE_WORDS_BY_LANGUAGE`
is keyed by the language name `data/sources_catalogue.csv` uses, and
`tests/test_funding_amount_parsing.py` reads that CSV at test time: a **wired**
language that is neither covered nor named in `UNCOVERED_LANGUAGES` with a
reason is a red build. **43 wired languages covered, one named as a gap** —
Oshiwambo, whose single masthead (New Era, Namibia) writes its money copy in the
English half of an English/Oshiwambo title. Six further catalogue languages are
covered though nothing is wired for them yet (Bengali, Dhivehi, Kinyarwanda,
Kurdish, Maltese, Uzbek), so wiring those feeds costs nothing here.

That structure exists because of the measurement two entries below: a partial
magnitude vocabulary **fails silently and looks like sparse data**, which is why
the figure-guard work costed a 43-language fold and declined to guess at one.
This is the shape that makes the gap visible instead of guessing.

**What is attested and what is not.** Of the 48 languages in the table, **41
have at least one form attested** — matched against the 5,417 headlines pulled
from 116 wired feeds on 2026-07-30, or against the stored `funding_amount`
strings. **Seven are dictionary citation forms that have never been seen**:
Macedonian (14 wired feeds and not one money headline in the fetch), Nepali,
Swahili, and the four whose feeds are catalogued but unwired — Bengali, Dhivehi,
Kinyarwanda, Maltese. And for four more the attestation is weaker than it looks:
Albanian `milion`, Estonian `miljon`, Kurdish `milyon` and Uzbek `million` are
forms SHARED with a neighbouring language, so what was seen was Czech, Latvian,
Turkish and English rather than those four. Treat those eleven as unverified
until a row from one of them lands.

Forms are **enumerated, not stemmed**. Latvian carried `miljonus`, `miljonu`,
`miljoni` and `miljoniem` in ONE fetch of `db.lv`; a stem with a loose tail
would also catch `milionário`, and bare `investice` already cost nine false
positives in fifteen when the prefilter learned this. Every form is off a live
feed fetch on 2026-07-30 (one request per publisher in `data/feeds.csv`, titles
only, no model, no storage) or off a stored row.

### Three things carried over from the Hebrew/Czech/Danish prefilter work

They shaped the CODE and not only the word lists, which is the part worth
keeping:

1. **`\b` is meaningless in Chinese, Japanese, Korean and Thai.** Those write
   the number, the scale word and the currency as one unbroken run — `1亿美元`,
   `ล้านบาท` — so a pattern ending in `亿\b` can never match, because 美 is a word
   character too. And Thai scale words carry combining marks, which `\w` does
   not match at all, so no `\w`-based boundary exists anywhere in `ล้าน`. Those
   go through `_GLUED_SCALE` as plain prefixes, longest first so `百万` is not
   read as `万`. Their units are not translations either: 亿 is 10^8 and 万 is
   10^4, so reading 亿 as a billion is wrong by a factor of ten.
2. **Hebrew and Arabic glue clitics onto the FRONT of a word**, and they are
   word characters, so `מיליון` is written `כמיליון` as often as not. A short
   prefix list is stripped, and only when the remainder is a word already in the
   table — the narrow form of the rule, because a loose substring match is what
   puts *salary* inside *a rental*.
3. **An alternation whose alternative ends in a magnitude word can silently
   never match.** That IS the Turkish bug, exactly: inside
   `(k|m|...|mil|mi|...)?\b`, `mil` matched the front of `milyon`, the boundary
   then failed, and an OPTIONAL group settled for no multiplier at all. There is
   no alternation any more. The letter run after the number is read once and
   looked up in a dict, which is a boundary that cannot be got wrong and which
   removes the ordering trap entirely.

### Conflicts are detected, not ordered — and that found two entries that were WRONG

A token two languages claim with different multipliers joins the refusal set
unless `RESOLVED_SCALE_COLLISIONS` names the winner and the reason. Running that
over the new tables immediately surfaced two entries the old table had wrong
rather than merely missing:

* **`billones` and `billioner` were mapped to 10^9.** A Spanish *billón* and a
  Danish *billion* are **10^12**. That is the same thousand-fold error this
  whole pass exists to remove, pointing the other way, and it was waiting for
  its first row. Both refuse now, along with `billión`, `Billionen` (German
  10^12, spelled almost exactly like the English 10^9), `bilião`/`biliões`
  (European Portuguese 10^12, against Brazilian `bilhão` at 10^9) and `trillón`.

Which is why **milliard is now read rather than refused**, reversing the note
that excluded it. There is no long-scale disagreement about milliard anywhere:
`milliard`, `miliard`, `milyar`, `miljard`, `Milliarde`, `mia`, `mld`, `mrd`,
`млрд`, `مليار` and `מיליארד` are 10^9 in **every** language that has the word.
The earlier note excluded it in the same breath as `billón`, whose ambiguity is
real, and it inherited a refusal by association. `mil` and `mi` keep refusing,
from the sweeps that found them.

### Separators: shape first, locale only to REFUSE

The rule is shape, and it holds under **both** conventions rather than assuming
one. Spanish writes `1,5 millones` and `1.500 millones` and never `1,500
millones` for one and a half, so **a lone separator with exactly three digits
after it is a thousands group** — no locale needed, and that is what makes the
Indonesian `$150.000` a hundred and fifty thousand. Anything other than a
three-digit tail is a decimal fraction, again in both conventions, because a
thousands separator always leaves exactly three digits behind it.

Locale is consulted only where it CONTRADICTS that: a three-digit group written
with the separator the scale word's own language uses for decimals. Then the two
readings are a thousand apart and nothing in the string chooses, so `US$ 1,500
milhões` **refuses**. That is the standing rule — `$150.000` read as 150 is
worse than NULL, because NULL is visibly missing while 150 looks like data — and
it applies to the tie the shape rule cannot break, not to the ones it can.

Two other separator fixes rode along: **two separators now mean the LAST one is
the decimal**, which is true either way and stops `1.000,50` reading as 1.0005;
and space-grouped numbers (`1 500 000`, French and Polish, NBSP and U+202F
included) read.

### `_MIN_PLAUSIBLE_USD` had BLINDED the guard that found all of this

The sub-thousand floor added earlier the same day is right — a sub-thousand
figure means the string was cut short, the scale word was one we do not know, or
a separator was misread, and refusing beats guessing. But
`test_no_stored_amount_parses_to_an_absurdly_small_figure` reads *what the parser
says about the strings we hold*, so a parser that cannot produce a sub-thousand
figure makes that test **unfailable**. The property anyone wanted checked was
never the parser's output range; it was that no string we HOLD is being read
that way.

So `read_funding_figure()` returns the figure BEFORE the plausibility bounds,
and a companion test names every string the floor is swallowing with a reason
each (`FLOOR_REFUSALS`, one entry today: Pluang's `$1`, from a headline the
publisher truncated mid-figure — the article slug says 15 juta USD). It is an
allowlist rather than an exact set, so correcting a row keeps the build green
while a NEW one turns it red. The six Turkish and Indonesian rows would have
arrived there as six unexplained entries rather than as silence.

### Measured

`python3 correct_funding_amount.py` (dry run) against the committed database,
3,254 live rows carrying an amount string:

| | |
|---|---|
| rows whose stored figure disagrees with the parser | **12** (0.37%), all published |
| of those, corrected to a figure | 7 |
| of those, cleared to no figure at all | 5 |
| money total | $133,405,633,262 -> **$133,745,781,597** |
| net | **+$340,148,335** |

The seven corrected are four Turkish `Milyon` rows (ThreatLocker $190M, Mate
Güvenlik $35M, Hush Güvenlik $30M, UNIT AI $12M), Infobae's `USD 53 millones`,
BetaKit's hyphenated `$20-million USD` and Investing.com Indonesia's `$150.000`.
The five cleared are three foreign-currency amounts this page promises to leave
out rather than convert at a rate nobody published (`500 millones`, `25
millioner kroner`, `10,5 mio. kr.`), one ambiguous scale word (`US$ 544 mi`) and
one truncated headline (`$1`).

**The rows are not touched by this change.** `correct_funding_amount.py` already
exists for exactly this and re-derives the WHOLE column rather than taking a list
of twelve ids; it needs one queued run once this parser is on `main`:

```bash
gh workflow run drain-writers.yml -f enqueue=correct-funding-amount.yml \
  -f inputs_json='{"dry_run":"false"}' \
  -f reason='re-derive funding_amount_usd after the 43-language scale vocabulary'
```

**It does not have to wait for this change, and that is worth being precise
about.** Checked by running the same derivation against `origin/main`'s parser:
it produces the identical 12 rows and the identical +$340,148,335, because the
narrower fix earlier the same day (`milyon`, the dot-as-thousands reading, the
plausibility floor) already covers every defect the CURRENTLY STORED corpus
happens to contain. The 43-language vocabulary corrects nothing that is stored
today. It is a forward guard: what it stops is the next Latvian, Vietnamese or
Hebrew row arriving worth two hundred dollars, and there was no reason to expect
those to arrive as anything else.

### What is not covered, and is a decision rather than an oversight

**A dollar still has to be stated in English.** `_USD_MARKER` accepts `$`, `US$`
and `USD` and nothing else, so a Turkish `20 Milyon Dolar`, a Brazilian `33
milhões de dólares` or a Chinese `1亿美元` refuses for naming no currency the
parser recognises, even though every one of them says "dollar" in its own
language. **Six such rows are stored today** — five Turkish `dolar` and one
Brazilian `dolares` — each holding NULL where a real figure exists. Widening the MARKER is a different
and riskier decision from widening the scale vocabulary — a Turkish *dolar* is
usually a US dollar and not always, while `美元` is unambiguous — and the scale
vocabulary cannot turn a foreign amount into a dollar figure precisely because
the marker gates it. Left alone deliberately, and written down here so the next
session meets the choice rather than the gap.

2,402 tests pass.

---

## 2026-07-30 — the parser was fixed and the rows were not: funding_amount_usd is re-derived by a queued pass, not by a list of twelve

`correct_funding_amount.py`, `.github/workflows/correct-funding-amount.yml`, one
line added to `drain-writers.yml`, and `tests/test_funding_amount_correction.py`
(27 tests). Suite **2,377 -> 2,406** (+27 mine, +2 from the workflow tests that
are parametrised per workflow file). **$0**: no model is called, and the whole
job is one pure function over strings we already hold.

### The defect, measured against `main` at 7e22619

`funding_amount` is the publisher's own wording and never changes.
`funding_amount_usd` is what `vocab.parse_funding_usd` made of it **at the moment
the row was written** — and that function was improved twice this week
(2026-07-29: the hyphenated multiplier and the stated-dollar rule; 2026-07-30:
`milyon`, `mi`, dot-as-thousands, `_MIN_PLAUSIBLE_USD`). Every improvement leaves
the rows collected before it holding a figure the parser would no longer produce.

| | |
|---|---|
| live rows carrying a funding string | 3,254 |
| of those, holding a dollar figure | 3,196 |
| disagree with the current parser | **12 (0.37%)** |
| of those, published to the live site | **12 (all of them)** |
| would be re-derived to a figure | 7 |
| would be **cleared** to no figure | 5 (0.16% of the 3,196) |
| money total on the tracker | $133,405,633,262 -> **$133,745,781,597** |
| net change | **+$340,148,335** |

Every one of the twelve is stored as a two- or three-digit dollar amount standing
for a round of millions: `USD 53 millones` as $53, `$20-million USD` as $20,
`$190 Milyon Dolar` as $190, `$150.000` as $150. The page's money charts, the
`raised` sort and the `funding_amount_usd >= N` filter all read that column.

### Two shapes, and the second one is the point

Seven now parse to a correct figure. Five now **refuse** — `500 millones` (no
currency stated), `25 millioner kroner` and `10,5 mio. kr.` (Danish), `US$ 544 mi`
(ambiguous scale word), `$1` (a headline the publisher truncated mid-figure) —
and a row whose amount refuses must end with **no `funding_amount_usd` at all**.
The page states that an amount it cannot read is left out rather than converted
at a rate nobody published, and a stale wrong number sitting exactly where the
parser now says "I will not guess" is the falsehood that promise exists to
prevent.

That second shape is why `/enrich` grew `tit_clearable_columns()`. Its ordinary
rule is that an absent or empty field NEVER erases a stored value — that is what
stops one failed lookup wiping a column — so erasing has to be asked for by name,
in `{"clear": ["funding_amount_usd"]}`, and only for the two columns on that
list. A deployed plugin without it answers `not clearable` and the run fails
loudly rather than leaving five wrong figures on the page.

### Three shape decisions, each of which could have been the damaging one

**In place on the site, never withdraw-and-republish.** `funding_amount_usd` is
not an input to `content_hash`, so the corrected revision carries the SAME hash
as the row it replaces, and `tit_insert_signal()` refuses any hash it has seen at
ANY revision. Republishing would have taken twelve real records OFF the live page
and reported `retracted` when it tried to put them back — twelve silent
deletions, logged as duplicates. Same reasoning as `correct-city-country`, and
the opposite of `correct-company-key`, which moves the hash and therefore must
republish.

**Merge, not rebase — the opposite of `correct-form-d`.** That one rebases
because it edits rows in place: a merge has no new `(content_hash, revision)`
pair to carry, so it would turn a loud rerun into a silent no-op. This pass
APPENDS a revision through `store.revise()`, so every corrected row is a new
`(content_hash, revision + 1)` — exactly the key `merge_db.py` merges on. Rebasing
a 34MB binary instead would conflict every time a backfill lands mid-run, which
right now is most of the time.

**The whole column, not twelve row ids.** A hand-typed worklist would be stale
the next time somebody adds a scale word, and would not have found these twelve
in the first place. Two ceilings guard the generalisation, both needing a `--force`
a person types after reading the printed table: 5% of rows may move, and only 1%
of rows holding a figure may be **cleared**. The second is tighter on purpose — a
wrong value is fixed by the next run, a cleared one is not, because `/enrich`
ignores absent values by design.

### What could not have done this job, and why that is correct

`schema.backfill_funding_usd()` already runs on every `connect()` and already
picks up parser improvements — but only `WHERE funding_amount_usd IS NULL`. It
reaches a row that never had a figure and none of these twelve, every one of
which holds a wrong one. That asymmetry is right rather than an oversight:
filling a NULL invents nothing and owes no revision; replacing a stored value is
a correction and owes one. It is also what makes a cleared row STAY cleared — the
backfill re-examines it every run and asks the identical function, the one that
refused the string in the first place. Asserted, not assumed
(`test_a_cleared_row_is_not_refilled_by_the_connect_time_backfill`).

`publish.enrich_published()` cannot do it either. It carries a new VALUE happily;
it can never carry an absent one.

### Where the brief was wrong

It said `test_no_stored_amount_parses_to_an_absurdly_small_figure` is RED on
`main` and that CI stays red until the correction runs. It is not, and it does
not. That test was deliberately rewritten to read **what the parser says about
the strings we hold**, not the stored column — its own docstring says "so the
test passes as soon as the parser is right and does not wait on the correction
run" — and the parser fix at `1d636c8` is what turned it green. Run
`30581357181` (20:56Z) was the last red one and named six strings, not twelve;
run `30581763245` on `7e22619` (21:02Z) is green. **CI on `main` was already
green before this work started and stays green after it.**

The brief also listed twelve rows as present in the committed database. Seven of
them are only in `origin/main`'s copy; the worktree's `data/talent_intel.db` is a
32.9MB file predating those collections and disagreeing on five rows, not twelve.
Every figure in this entry was measured against `origin/main`'s blob, never the
dirty working copy, and nothing here commits a database.

### Queue it, never dispatch it

```
gh workflow run drain-writers.yml -f enqueue=correct-funding-amount.yml \
  -f inputs_json='{"dry_run":"false"}' \
  -f reason='re-derive funding_amount_usd after the milyon/mi/dot parser fixes'
```

`dry_run` defaults to true, so a dispatch that says nothing writes nothing. The
workflow is in `talent-collect` and is now in `drain-writers.yml`'s
`workflow_run` list, which `tests/test_workflows.py` derives and compares rather
than trusting.

---

## 2026-07-30 — Czechia states both directions and Estonia states only one, and the window belongs on the registration date

`collectors/czechia_ares.py` + `collectors/estonia_ariregister.py`, two new
weekly slots in `collect-structured.yml` (Friday and Saturday, the two days no
other database writer holds), 6 new fixtures, 91 new offline tests. Both are
keyless, both expose `as_classified`, neither calls a model: **$0**. Every
number below was fetched live on 2026-07-30 and most are from a real dry run
rather than a projection.

### The two sources, in one table

| | Czechia (ARES) | Estonia (Ariregister) |
|---|---|---|
| shape | change feed -> employee band -> register record | three static file downloads |
| population | 22,492 companies changed in 28 days | 375,305 companies, 520,895 person rows |
| materiality | RES band `>= 330` = 250+ staff, **226 of 22,492 (1.0%)** | 2025 annual report `>= 50` FTE, **825 of 194,851** |
| directions | **arrivals AND departures**, both source-dated | **arrivals only, and never anything else** |
| measured | **108 events in 14 days, ~2,800/yr** | **16 in 21 days, ~265/yr** |
| cost per run | 208 requests, ~2 min | 3 requests, 83MB, ~2 min |

### Czechia: the window does not belong on the office date, and a live run is the only thing that could have said so

The brief said: use the office dates as the event date, record the registration
date too, never diff snapshots. All three are right, and the first one is not a
window. `clenstvi.clenstvi.vznikClenstvi` / `zanikClenstvi` are when the office
began and ended; `datumZapisu` / `datumVymazu` are when the court wrote it down;
the notification feed announces the writing.

Filtering on the OFFICE date therefore asks the feed which companies moved this
week and then discards every change whose effective date was earlier than the
window. **A real seven-day run (2026-07-23..07-30, 76 material companies)
produced ZERO events** and tripped the emptiness floor, which is the only reason
this was found: every unit test passed, because a fixture built from a 28-day
window has its office dates inside it.

Same 76 companies, same week, selecting on the REGISTRATION date instead:
**41 events** — 18 arrivals, 13 departures, 8 promotions, 2 role endings — at a
median office-to-registration lag of **25 days**. That is why a 7-day office
window found nothing. Both dates are still source-stated and both are still on
the record; what changed is only which of them decides an event is new.

`MAX_BACKLOG_DAYS = 365` is the other half. Seven of those 41 had office dates
**one to ten years** before their registration — a court finally writing down a
2016 board change. There is no honest date for those: the true one puts a
decade-old change on a dashboard of this week's market and today's is a figure
nobody stated. Declined and counted; the shipped 14-day run declines 11.

### Czechia: `datumVymazu` is not a departure, and reading only the live version is not the fix

The VR record is a full version history. **353 of 543 member versions on ČEZ's
record carry a `datumVymazu` and no `zanikClenstvi`** — they are amendments.
Martin Novák's seat beginning 2026-05-25 appears twice, the first version
deleted five weeks later purely because his academic titles were added; he is
still on the board. Reading `datumVymazu` as an exit reports a leaving rate
about nine times the truth.

**And the obvious repair is wrong in the other direction.** Jean-Charles Chen's
seat at ICO 17774713 has a live version saying `Člen správní rady` with no dates
at all, and a superseded one carrying `zanikFunkce: 2026-07-10` for
`Předseda správní rady`. He stopped being chairman that day and stayed on the
board, and **the only place that fact exists is the version the register has
already deleted.** So `memberships()` groups every version on (organ, person,
membership start) and `_events` reads them all, deduplicating a membership event
on (kind, date) and a role event on (kind, date, role) — because one person can
be promoted and demoted inside one unbroken membership, while the same arrival
restated by five amendments is one row.

### Czechia: the materiality filter, and the hole in it

`kategoriePoctuPracovniku` code `330` is `250 - 499 zaměstnanců`, read from the
register's own codebook at `/ciselniky-nazevniky/vyhledat` rather than assumed.
There is no search-by-band: `EkonomickeSubjektyRegistraceFiltr` accepts an `ico`
array and nothing else, read from the OpenAPI document at
`/ekonomicke-subjekty-v-be/rest/v3/api-docs`. The change feed is what makes
per-ICO lookups affordable instead.

**Legal form was refused with a number.** `a.s.` joint-stock companies would poll
1,362 in that window to find 117 material ones — **8.6% precision**, the UK
accounts-category failure (6.35%) again and for the same structural reason:
legal form records how a business is owned, not how many people it employs.

**The hole is large, and it is on the sources page rather than only in a
docstring.** `000 Neuvedeno` is **12,624 of 19,285 RES records (65%)** and **567
of the 1,362 joint-stock companies (41.6%)**; another **3,207 of 22,492 (14.3%)**
have no RES record at all; and the band goes stale (ČEZ's `datumAktualizace` is
2023-06-29, and 75 of the 226 material companies were last updated in 2023). A
large employer whose statistical band was never populated is missed rather than
judged small. That is a recall hole, not a precision one.

### Czechia: the citation, because two nicer-looking URLs both fail

`source_url` is the API document, `ekonomicke-subjekty-vr/{ico}`. The two
alternatives were fetched rather than assumed:

* `ares.gov.cz/ekonomicke-subjekty/{ico}` is a Vue app and answers **HTTP 200
  with the same 912-byte shell** for ČEZ and for the invented `00000001`. That is
  the EDINET viewer trap and Korea's "Reject" body.
* `or.justice.cz/ias/ui/rejstrik-$firma?ico=` is the Ministry of Justice's own
  register and the nicest page for a human, but `or.justice.cz/robots.txt` says
  `Disallow: /ias/`, which is the whole application — citing it would make
  `link_check.py` record every Czech row as `robots` and check none of them.

A bogus ICO on the API is an unambiguous **404** with
`{"kod":"NENALEZENO", ...}`. `ares.gov.cz/robots.txt` disallows only `/cms/`.
The Ministry of Finance states the limit as more than **500 queries a minute**
and reserves the right to cut off anyone probing "náhodnými údaji" — random
values; this collector never guesses an ICO, every lookup comes from the
register's own feed, and a run sits at about a quarter of the ceiling.

### Estonia: the negative is the finding, and it is bigger than the feed

**`lopp_kpv` is null in 520,895 of 520,895 person rows.** The published file
holds current office-holders only, so Estonia yields appointments and never
departures. That sentence is in `raw_text`, in the summary, in the read-through
and in the sources-page note, and a test requires all four — a leadership feed
that silently reports only arrivals reads as a country where nobody ever leaves.

Refused rather than worked around: `arireg.ettevotjaMuudatusedTasuline_v1`, the
SOAP change list, needs an account and is *tasuline* (chargeable); and diffing
yesterday's file against today's, because a vanished row may be a departure, a
correction, a merger or a deregistration and the file states no date for any of
them. That is Korea's roster refusal again.

### Estonia: the threshold is somebody else's definition, and 250 was tried first

**18,155 appointments in the 90 days to 2026-07-30 — 202 a day, ~74,000 a year**
— from a country of 1.3 million people. `JUHL`, board member, is 446,636 of the
520,895 rows and most of those are one-person `OÜ` micro-companies. So there is a
threshold, from the annual reports'
`AverageNumberOfEmployeesInFullTimeEquivalentUnits` (3,006,385 element rows ->
194,930 figures -> 194,851 companies, joined on `report_id`):

| FTE floor | companies | appointments in 365 days |
|---|---|---|
| 10+ | 5,449 | 808 |
| 25+ | 1,878 | 384 |
| **50+** | **825** | **235** |
| 100+ | 368 | 119 |
| 250+ | 107 | **38** |

**The floor is 50**, EU Recommendation 2003/361's own line: micro under 10,
small under 50, medium 50-249, large 250+. **250 — what `companies_house` and
`czechia_ares` draw — was tried first and refused with its number**: 38 a year is
under one a week, so most weekly runs would store nothing, and a collector
returning zero is `degraded` by this repo's own rule. The threshold matching the
UK's letter produces a connector that is broken most weeks; 50 matches its
intent. Measured at 50 over 2026-05-01..07-30: **66 appointments in 91 days**,
at employers from Bondora (54 staff) to BAUHOF GROUP (492).

What it costs, stated: a company with no 2025 annual report has no figure and is
excluded, so a fast-growing new employer is invisible until it files. And the
report files are frozen at "kuni 30.06.2026", which is why
`discover_report_files` reads the download page for the current filenames rather
than hard-coding that date — a hard-coded URL 404s into every company failing the
threshold, which looks exactly like a quiet fortnight.

### GDPR: taken at the boundary, never persisted

The owner's ruling, and both sources needed it. The Czech national open data
catalogue states this dataset's own conditions of use as `neobsahuje autorská
díla`, `není autorskoprávně chráněnou databází`, `není chráněna zvláštním právem
pořizovatele databáze` (`narrowMatch` CC0) **and `obsahuje osobní údaje`** — the
publisher itself says it contains personal data. It does: `datumNarozeni` on
13,834 of 15,645 person rows in the material sample and a full residential
address on 15,619. Estonia's file carries a home address on 60,930 rows, a birth
date on 16,099, an email on 14,360 and a national-ID hash on 485,719.

`scrub_person` in each collector returns given name and surname and nothing else,
and it is the only path from the source to a row. Everything else is dropped
before a dict exists, so no later stage can leak what it never received. The
tests assert it end to end — a fixture that KEEPS every one of those fields,
through `as_classified` and `validate.build_signal`, with the stored Signal
required to contain none of them.

### `validate._NUMBER` cost twelve rows before both collectors were rebuilt around it

Twelve of Estonia's first 66 rows were discarded with
`figure(s) not present in source text: ['2026b']`. The summary read "...on 9
June 2026. BAUHOF GROUP AS reported 492 employees..." and the body read "...on 9
June 2026. The register names the role..." — `\d[\d,.]*` matches `2026.`
including the full stop, `_H_SPACE*` matches the space, and `B` is read as a
magnitude suffix. This is the case `validate.py` already names and deliberately
leaves alone ("any word starting with b, m or k still glues INSIDE a line"), and
the 2026-07-30 newline fix does not reach it.

Not fixed here — that regex has a measured reason to stay as it is, and it is
not this brief's lane. Both collectors now **compose the summary once in `_row`
and return it unchanged from `as_classified`**, so the summary is a literal
prefix of `raw_text` and every figure in it is verbatim in the source by
construction rather than by care. BAUHOF GROUP AS is in the Estonian fixture for
exactly this, and a test asserts the prefix property on every row.

Diacritics round-trip proved on real names in both collectors: `CHALOUPKOVÁ`,
`PÁLENÍČEK`, `Kõrve`, `Rieksts-Riekstinš`, `Möldre`, `Suislep-Peets`. Nothing is
re-cased or normalised — the register writes some people in capitals and some in
title case, and NFKC is not a safe blanket fix.

### Where this brief was wrong about the repo

* **There are no TECHLOG triage entries for Norway, Spain, Finland or Poland.**
  The brief said to read them as the discipline to apply. The 2026-07-30 triage
  in `source_registry.py` covers IL, GB, AU, IE, FR, DE, IN, CA, JP, SG and the
  Form 6-K dead end; none of those four appears anywhere in `docs/TECHLOG.md`.
  Norway and Finland are mentioned once, in the MARKETS comment, as countries
  excluded from the 2026-07-29 twelve for having no language pack. The
  discipline was taken from the Korea and Australia entries instead.
* **Estonia's person-row count is 520,895, not 599,289**, and appointments run
  **202 a day**, not ~230. The zero-end-dates finding itself is exactly right:
  0 of 520,895.
* **The notification-batch endpoint is not guessable.** `GET
  /ekonomicke-subjekty-notifikace/{n}` and five other shapes all 404; the real
  path is
  `/ekonomicke-subjekty-notifikace/datovy-zdroj/{datovyZdroj}/cislo-davky/{cisloDavky}`,
  found only by reading `/ekonomicke-subjekty-v-be/rest/v3/api-docs` — linked
  from `ares.gov.cz/swagger-ui/swagger-initializer.js`, whose default `url` is
  still the Swagger petstore.
* The 24,651-notification count, the `330` band code, the CC0 mapping, the
  robots position, the 500-per-minute limit, the 100-ICO ceiling on the RES
  search and Estonia's CC BY 4.0 all checked out exactly as briefed.

### Neither country is in `MARKETS`, and one of the two reasons is mechanical

The first is the one that keeps Japan and Korea at `discovery_only`: no run has
gone through `run_collect` and STORED a row, and a tier is a claim about the
connector rather than about the source.

The second is arithmetic. **The segment budget is full at 56 of 56.**
`build_segments()` spends one slot per market plus one per `terms` entry, and
`test_the_segment_matrix_still_sweeps_inside_the_recency_window` requires
`ceil(segments / 4 / 2) <= recency_window_days`, which is 7 at 51 locales. Two
more markets make the sweep 8 days and the guard refuses it. Room comes from
widening the locale rotation — a live-verified language pack, not a translation —
and not from raising `SEGMENTS_PER_RUN`, which is a guard that exists because
queries once asked `when:3d` while the matrix took 6.2 days. Both countries are
listed on the sources page with a live collector behind them, which is where
coverage is claimed truthfully today. The whole argument is written into the
triage comment in `source_registry.py` so nobody re-derives it.

### Numbers

| | |
|---|---|
| tests | **+91**, suite green at 2,377 with 202 subtests |
| new collectors | 2, both keyless, both `as_classified`, **$0** |
| Czechia, real 14-day dry run | 10,483 notifications, 10,190 companies, 92 material, **108 events**, 11 backlog declined, 0 rejected |
| Czechia by kind | 36 arrivals, 34 departures, 13 promotions, 12 role endings |
| Estonia, real 21-day dry run | 375,305 companies, 520,895 card entries, 388 legal persons declined, 405 roles declined, **16 appointments**, 0 rejected |
| new schedule slots | Friday 04:00 (CZ), Saturday 04:00 (EE) — the only two days `talent-collect` was free |
| live sources on the page | 12 -> **14** |

Nothing was dispatched, and `data/talent_intel.db` was never written: both dry
runs ran against a scratch copy in a temp directory, because a dry run still
writes a `source_health` row.

---

## 2026-07-30 — the audit's twelve and eleven: nine feeds wired, eight refusals written down, and Brazil is one day old

`data/recall_rejection_audit.json` classifies 81 gold-set misses. Two of its
buckets are a worklist rather than a statistic — `publisher_not_wired` (12
misses across 7 publishers) and `publisher_unknown` (11 across 10) — and until
today nobody had acted on either. All 17 publishers were attempted. **Nine
feeds are wired, eight publishers are refused with the evidence written into
the catalogue, and one was left alone because it was already exhaustively
checked.** Every figure below was fetched live on 2026-07-30 through
`collectors/national_press.py`'s own `robots_allows` / `fetch` / `parse` path,
not through curl and not through a browser.

`data/sources_catalogue.csv` 1,294 -> 1,305 rows; `data/feeds.csv` 653 -> 662
feeds. New offline tests in `tests/test_audit_publishers.py`. No collector, no
pipeline and no workflow changed: the catalogue IS the configuration, which is
the whole point of that collector.

### The judgement call: a press-release wire is not an aggregator

Five of the seventeen are wires — Business Wire, GlobeNewswire, PR Newswire,
Presseportal (news aktuell), and Yahoo Finance, which is not one but reads like
one from the URL. "Aggregators are discovery pointers, never stored sources" is
non-negotiable, so the question had to be answered from the policy that already
exists rather than invented here.

It already is answered, in three places that agree:

- `national_press._AGGREGATOR_HOSTS` lists Google News, Yahoo News, Flipboard,
  MSN, FeedBurner and ten commercial funding databases and startup
  directories (three of them held base64-encoded since 2026-08-03 under the
  standalone-brand rule). **No wire appears in it.**
- `validate._BLOCKED_SOURCE_HOSTS` is the same five aggregator hosts. **No wire
  appears in it either.**
- The database already cites `prnewswire.com` on 3 current rows and
  `businesswire.com` on 1.

The distinction the existing policy draws is about WHOSE DOCUMENT it is. A
release on a wire is the company's own announcement, published under its own
name — the same class of thing as a company newsroom, which this catalogue
already carries 16 of. Google News republishes somebody else's article and adds
a redirect. That is why one funding database's bylined newsroom subdomain is
in `_EDITORIAL_EXCEPTIONS` while the rest of its domain is blocked: the test
is the reporting, not the domain's other businesses. We have over-blocked by analogy once already, and
this is the shape it takes.

So the wires were treated as wireable and each one failed or passed on its own
merits. Four of the five still ended up refused, and **not one of those
refusals is a policy refusal** — three are mechanical and one is Yahoo.

**Yahoo Finance is the exception, and it is a policy refusal.** `news.yahoo.com`
is in both sets above, both of which are reduced to registrable domains, so
`yahoo.com` is blocked on every subdomain — `finance.yahoo.com` included. A feed
listed for it would be refused at load time and print a line in the run log
twice a day forever. The existing answer to a syndicated release is
`validate.prefer_canonical`, which follows the document's own `rel=canonical` to
the publisher and stores that instead; the audit's own miss here is described as
"Clinigen press release via Yahoo Finance", which is exactly the case that
function was measured on.

### Wired: nine feeds, all verified through the collector's own path

| publisher | country / coverage | feed | items | newest |
|---|---|---|---|---|
| LatamList | Latin America (regional) | `/feed/` | 10 | 0d |
| European Biotechnology Magazine | Europe (regional) | `/feed/` | 10 | 0d |
| pv magazine | Europe (regional), Global | `/feed/` | 10 | 0d |
| Techla Media | Spain, Regional (es) | `/feed/` | 10 | 0d |
| Business Upturn | India | `/feed.xml` | 25 | 0d |
| The Motley Fool Australia | Australia | `/feed/` | 20 | 0d |
| Presseportal (Wirtschaft) | Germany | `/rss/wirtschaft.rss2` | 15 | 0d |
| Presseportal (Finanzen) | Germany | `/rss/finanzen.rss2` | 15 | 0d |
| TNGlobal (TechNode Global) | Singapore, Regional | `/feed/` | 25 | 0d |

Run as a population through `national_press.collect(feeds=..., dry_run=True)`:
**9 feeds live, 0 not answering, 140 items, 3 duplicate URLs, 137 returned, of
which 21 pass the free prefilter (15%)** — against ~11% for the pipeline as a
whole. LatamList alone keeps 7 of 10, because it publishes almost nothing except
funding rounds.

Four things worth not rediscovering:

1. **Business Upturn's feed is at `/feed.xml` and nowhere else.** All fourteen
   other candidate paths 404 and the HTML head declares no `rel="alternate"`, so
   the only way to it is probing. A session that stops after `/feed/` and `/rss/`
   concludes the publisher has no feed.
2. **`technode.global` is a different publication from `technode.com`**, which
   this catalogue already carried. Shanghai and Singapore, separate registrable
   domains, so nothing de-duplicates them and the second is a real addition.
3. **Presseportal's advertised feed is the firehose.** Its `rel="alternate"`
   points at `presseportal.rss2`, which is every release including the police
   blotter; `/rss/` lists 38 subject feeds and the two that match our pillars are
   `wirtschaft` and `finanzen`. Both are wired; `PER_HOST_PAUSE` spaces them.
4. **Three of the nine state no home country** (LatamList publishes no imprint,
   European Biotechnology's imprint renders behind a form, pv magazine's
   `/imprint/` 404s). Each is recorded with the region convention this file
   already uses — `Latin America (regional)`, `Europe (regional)` — rather than a
   guessed seat, and all three carry `coverage` Regional or Global, so
   `national_press.dateline()` says "regional" and claims no country either way.
   A guessed seat would have been worse than no seat: it is a hint fed to the
   model.

### Refused, with the numbers, so nobody probes them again

| publisher | verdict | evidence |
|---|---|---|
| CTech (`calcalistech.com`) | no feed exists | already established on 2026-07-30: 21 paths, robots, and 20,000 Wayback captures. **Not re-probed.** 4 misses, the largest single share |
| Business Wire | nothing fetchable | `robots.txt` itself now answers **HTTP 403** to our UA, so its terms are no longer readable, and all 15 candidate paths 403 through both Accept sets |
| GlobeNewswire | publisher's own terms | `robots.txt` is 200 and names the feed: `Disallow: /SubscribeToRss/` and `Disallow: /newsroom/rss/`. 13 other paths 404, `/rss/news` is HTTP 500, no `rel=alternate` |
| PR Newswire | no feed published | robots allows (only `/templates/*`, `/widget-landing-page.html`, `/multivu/`); 11 of 15 paths 404 and 4 answer 200 with 0 parseable items; no `rel=alternate` |
| Yahoo Finance | policy | `yahoo.com` is an aggregator registrable domain in both blocklists. See above |
| Tech.eu | publisher's own terms | re-verified: `robots_allows("https://tech.eu/feed/")` is still False. Withdrawal from 2026-07-29 stands |
| Business Standard (Companies) | bot wall | re-verified: `/rss/companies-101.rss` still HTTP 403 through both Accept sets |
| FinSMEs | WAF, not terms | robots is 200 and **permissive for us** (only Ahrefs, scrapy, Semrush, BLEX, Dot, MJ12, Grapeshot are disallowed), yet 13 of 15 paths answer **403**, including `/feed/`. We already hold **10 stored rows citing finsmes.com**, all via google_news, so the outlet is reachable and only its feed is closed |
| WeAreAquaculture | thin, therefore degraded | `/feed/` is 200 and redirects to `/stories.rss`, well-formed RSS carrying **exactly one `<item>`** — an 8,401-byte body, verified twice, and undiscoverable from the HTML head. At two runs a day a one-item feed carries at most two stories a day and silently drops the rest |

**A news sitemap is not a substitute, and this is measured rather than assumed.**
Both GlobeNewswire and PR Newswire advertise `Sitemap:` lines for news sitemaps,
which is the last resort the search order calls for. Fetched through
`national_press.fetch()`, `sitemaps.globenewswire.com/news-en.xml` and
`www.prnewswire.com/sitemap-news.xml` each yield **0 items**: `parse()` looks for
RSS `<item>` or Atom `<entry>` and a sitemap has `<url>`. The same is true of the
three `wp-json/wp/v2/posts` rows already in the catalogue (Citinewsroom,
Techweez, Techzim) — all three read `dead` or `empty` in the last health ledger.
**Do not catalogue a sitemap or a JSON API as an `rss` feed**; it produces a row
that looks wired and reports `empty` forever.

### Brazil, measured rather than assumed

The owner asked why Brazilian startup funding is thin: 11 stored BR rows against
13 Brazilian feeds. The three candidate explanations were dead feeds, a
Portuguese keyword gate rejecting everything, and deferral at the read cap. It is
none of them in any interesting sense.

- **The feeds work.** Last recorded sweep: 13 BR feeds, **12 ok**, 1 dead
  (TI Inside, HTTP 403 — transient; it returned 25 items today), **156 items,
  every one 0–1 days old.** A live re-fetch today returned **181 items** across
  the same 13.
- **The prefilter is not eating them.** Of those 181, **25 pass the free
  prefilter — 13.8%**, against the ~10.9% the pipeline averages (9,308 fetched,
  8,290 filtered, per `run_collect`'s own recorded figures). **Brazil is above
  the average, not below it.** Reject reasons: 155 "no employment, site or
  work-policy term", 1 off-topic. A silent Portuguese gate would show as ~0%,
  and this is the check that rules it out.
- **It is a history problem, which is the audit's verdict for the corpus as a
  whole.** `national_press` first ran on 2026-07-29 and holds **88 current rows
  in total, across every country**. BR is **9 of those 88 — the largest
  single-country share of the collector's output**, ahead of AU 7, IN 5, CN 5,
  DE 4, CA 4. Brazil is not underperforming; the collector is one day old. The
  other 2 BR rows are SEC filings from April.
- **The read cap is a real but secondary constraint.** BR is 156 of the
  collector's 10,723 items (**1.5%**), so at `READTHROUGH_CAP` 200 and a fair
  share, Brazil buys roughly three reads a run.

**One real finding, small and worth its own line.** Re-reading the 156 rejected
items against a richer Portuguese vocabulary, **2 are genuine misses**:
"Governança Brasil tem novo CRO" (a leadership appointment) and "Com a agtech
Ecotrace, GS1 Ventures faz seu primeiro aporte" (a funding round). That is ~1.3%
of rejects, so it is a gap and not a bug. The cause is visible in
`prefilter._EMPLOYMENT_TERMS_INTL`: **Portuguese carries ten terms and exactly
one funding phrase, `rodada de investimento`**, while the everyday Brazilian
wording is `capta` / `captação` / `aporte` / `levanta R$` / `Série A`. Czech and
Turkish each got a measured expansion after exactly this kind of read; Portuguese
never has. Deliberately NOT changed here — `pipeline/prefilter.py` is another
session's file this week — and recorded so it can be done as its own measured
change. Note it is not costing us everything it looks like it might: "Einship,
startup de IA para comércio exterior, capta R$ 5,3 milhões" IS stored, because
the body carried a term the gate knows.

### What is asserted

`tests/test_audit_publishers.py`, six tests, offline, no network:

- every domain the audit named in the two actionable buckets exists in the
  catalogue;
- each is **either wired or refused in writing** — an empty `rss` AND an empty
  `feed_checked` fails, because that is indistinguishable from nobody having
  looked;
- a refusal carries **at least 200 characters of note**, so the next session
  inherits what was tried rather than repeating it;
- nothing from the audit was wired on an aggregator registrable domain, checked
  against `_AGGREGATOR_DOMAINS` itself;
- the wire precedent is pinned: no host containing `newswire`, `businesswire` or
  `presseportal` may enter `_AGGREGATOR_HOSTS` without someone deliberately
  changing this test and explaining the rows already in the database;
- a wired row records **what the feed returned** (item count and newest age) in
  `feed_checked`, because "a feed that returns nothing is degraded, not
  coverage" has to be checkable after the fact.

Matching is on the **registrable domain**, imported from
`collectors.national_press` rather than reimplemented. There are already two
copies in this repo (the collector and `analysis/recall/rejection_audit.py`); a
third deciding which publishers count as handled would be the one that goes
stale first.

**+6 offline tests.** The whole suite reads **2,367 passing** in this working
tree, which also holds two other sessions' in-flight files (`czechia_ares`,
`estonia_ariregister`, a press-archive walker); only the six above and the two
generated data files below belong to this change.

`data/sources.json` and `data/feeds.csv` are both GENERATED —
`build_sources_json.py` and `build_feeds_export.py`. Never hand-edit either;
`tests/test_sources_page.py` and `tests/test_national_press.py` each fail if you
do.

---

## 2026-07-30 — historical press: a sitemap is an archive and an RSS feed is not, and the honest ceiling is four of fifty-one

`collectors/press_archive.py` + `backfill_press_2026.py` +
`.github/workflows/backfill-press-2026.yml`, dispatch-only, 41 new offline
tests. Every figure below was fetched from live publishers on 2026-07-30, and
three of the premises this started from turned out to be wrong when the real
sources answered. Those are recorded here rather than quietly worked around,
because each one is the kind of mistake that produces a confident number.

### Why anything was built at all

`data/recall_rejection_audit.json`: of 81 gold-set misses, **zero were fetched
and rejected**. There is no filter defect anywhere in this product. **51 are
`outside_our_history`**, every one published between **2026-07-01 and
2026-07-17**, against news collectors that first ran on 2026-07-27 and a
`national_press` that first ran on 2026-07-29. We did not miss them; we did not
exist.

`national_press` reads 653 publisher feeds and can never help: **an RSS feed is
a window, not an archive.** It serves the last few dozen items and
`MAX_ITEMS_PER_FEED` takes 25 of them, which on a daily is two or three days.
Nothing in the RSS route reaches 2026-07-01 at any price. A publisher's XML
sitemap is a different document with a different promise, written for crawlers
that want the whole site.

### Route A: publisher sitemaps. Measured with the shipping code, 82 publishers

Against **2026-03** — a month that predates us AND is out of reach of a
48-hour news sitemap, which is the only kind of test month that cannot be faked.

| | |
|---|---|
| serve a discoverable sitemap | 72 / 82 (88%) |
| reach 2026-03 with a dated article URL | **34 / 82 (41%)** |
| ... with 50 URLs or more | 25 / 82 |
| ... with 100 or more | 23 / 82 |
| URLs per reaching publisher, one month | median 163, mean 233 |
| past the free prefilter on the SLUG | 244 / 7,910 (**3.08%**) |
| past it on the real title+teaser | ~5.6% (11 of 60 against the slug's 6) |
| wall clock | 980s for all 82, median 5.3s each |
| robots.txt refusals | 0 |

### Where the brief and the first pass were both wrong

**1. `<lastmod>` does not locate a month, and counting it overstates reach by
half.** A first probe counted lastmod months and reported **54 of 72 publishers
"reaching 2026-07"**. That is nonsense twice over. A section page's `<lastmod>`
moves to today whenever a story is added to it, so WirtschaftsWoche's
`sitemapExternal` index (a list of TOPICS: "cisco", "chiphersteller") and
Baguete's seven URLs of site furniture both scored. And a 48-hour news sitemap
read on 30 July trivially "reaches July": **PR TIMES scored 942 July URLs that
way and actually reaches four.**

Worse, the obvious selection — "fetch every child whose lastmod is not older
than the window" — is wrong on a real index. SmartCompany's `sitemap_index.xml`
lists 105 children:

    post-sitemap.xml            lastmod 2026-07-29   contents 2006-12..2007-08
    post-sitemap45.xml          lastmod 2013-08-29   contents 2013-07..2013-08
    post-sitemap89.xml          lastmod 2026-07-29   contents 2026-04..2026-07
    site-post-tag-sitemap5..9   lastmod 2026-07-30   contents TAG PAGES
    news-sitemap.xml            lastmod 2026-07-29   contents the last 48 hours

Page ONE of a chronologically paginated set carries the whole site's newest
modification date while holding posts from 2006. What IS reliable is the
ordering, so `locate_children()` **bisects the largest paginated family**:
July 2026 is one fetch instead of 105, bounded at `MAX_PROBES = 9`, and a family
whose probes come back out of order is abandoned rather than trusted. Every
probe's body is handed back through the caller's cache, so a child fetched to
locate the window is never fetched again to read it.

**2. A sitemap has no headline, and the slug is not a substitute.** `<urlset>`
gives a URL and a date. The free prefilter — the whole reason breadth is
affordable — has nothing to read. Three sources of text, measured:

* **the slug**: free, 3.08% survival, and **zero for PR TIMES
  (`/tv/detail/3164`) and CTech (`0,7340,L-3723664,00.html`)**. A prefilter that
  returns zero for Japan, Korea and Israel while looking healthy in English is
  the same silent-zero failure `tests/test_locale_rotation.py` exists to prevent
  one layer up. So the slug **ORDERS and never rejects**, and there is a test
  saying an unreadable slug still produces a candidate.
* **`<news:title>`** where the sitemap carries it: 17 of 72 publishers. Free and
  exact, used when present.
* **the article's own `og:title` / `og:description`**: the two fields a
  publisher writes so other people may quote the piece, which is what an RSS
  teaser is built from. 0.17s each on a keep-alive session, and 11 of 60
  SmartCompany URLs past the prefilter against the slug's 6. Only the first
  `HEAD_BYTES` (200KB) of the response is read and no body text is taken.

**3. Route B, the Wayback CDX API, is refused as a walk route — and the query
shape everyone reaches for first is the one that fails.**

| query | result |
|---|---|
| `url=<domain>/*` + a date range | **HTTP 504 after exactly 60s, every domain tried** |
| `url=<domain>/&matchType=prefix` + dates | 200 on 6 of 8 domains, 7-29s each; 504 on the other 2 |
| `url=<domain>` (exact) | 200, 7.3s |
| 6-query burst with no pause at all | 5x 200, 1x 504 |

**No 429 was observed at any point in ~20 queries.** archive.org's failure mode
here is a gateway 504, not a documented throttle with `Retry-After`. The
`ArchiveUnknown` rule covers 429, 5xx and timeouts alike, because the property
being guarded is "did not answer", not a particular number — and a non-answer
read as an empty result is how a throttle becomes a coverage claim nobody
re-checks.

The decisive finding is different and worse: **the CDX date range is a CAPTURE
window, not a publication window.** Asking for captures between 2026-07-01 and
2026-07-20 returned FINSMES articles from **2013 and 2014** and Wamda articles
from **2012**, because a crawler visiting a site in July 2026 re-captures a
decade of its pages. CDX cannot target a historical month at all. Combined with
7-60s per domain and a quarter of domains answering 504, 653 publishers is
hours of wall clock against a 50-minute slice budget. `wayback_urls()` stays in
the collector for a named dead publisher, called by hand, and is wired into
nothing.

### The cursor walks PUBLISHERS, and that is the one structural difference

A GDELT or Google News day costs a fetch. A publisher's sitemap costs the same
fetch whether the window is one day or six months — the date is a FILTER over
rows the document returned anyway. A date cursor would therefore "finish"
2026-01-01..04 having downloaded every publisher's whole 2026 and thrown 99% of
it away, then download it all again for the next four days. So the unit is
`slices`, the same one `backfill_slices.UNITS` documents for `companies_house`,
and **widening the window is free**: dispatching 2026-01-01..2026-07-26 costs
exactly what one week costs.

The property `tests/test_backfill_pace.py` asserts is unchanged and now asserted
for this walker too — two runs in the same clock second walk two different
roster slices — plus a new one: **a run that stopped on its budget after 5 of 40
publishers finishes NO roster index**, emits an unmoved cursor, and is correctly
refused a requeue. Advancing on "we got some of the way through" would leave 35
publishers unvisited with the run count looking perfect.

### Cost, and the refusal

At the ledger's measured prices (gate $0.00003, read $0.00128, gate survival
15%, so $0.000222 all-in per gated candidate), scaled to 653 feeds: ~271
publishers reach an arbitrary 2026 month at ~233 URLs each, ~5.6% candidates,
so ~3,500 candidates per month of history — about 115 per day of history against
the Google News walker's measured 395.

| | |
|---|---|
| one month of history, EVERY candidate gated | **$0.78** |
| a year of 2026 the same way | **$9.42** |
| GDELT's whole year | $4.51 |
| the Google News walker's year | $3.02 |

**A full-depth sweep is more expensive than either walker already built, so it
is refused.** The gate is rationed instead, exactly as `backfill_gnews_2026.py`
does and for its reason: a read-only ceiling STALLS a walker (the ceiling binds
inside slice one, no unit finishes, the cursor never advances, and the chain
halts behind a green exit code), whereas a ration lets a slice FINISH partially
read. `SLICE_GATE_RATION = 75` is DERIVED from `MONTHLY_WALKER_BUDGET_USD =
0.50` — the smallest of the three walkers, because GDELT holds $1.50 and Google
News $1.00 and those two are already half the ~$5 product budget. A pass over
the roster is **17 slices and $0.28**, reading 36% of a one-month window;
everything past the cut is left UNMARKED, so a second pass costs the same and
buys entirely different rows.

### The honest coverage estimate, which is why this is not a recommendation

Of the 51 `outside_our_history` misses, **11** are on a domain this collector
sweeps at all. The other 40 are on domains in the catalogue without a feed (20)
or not in the catalogue at all (20) — a SOURCE problem wearing a history
problem's clothes, which no history walk can fix. Each of the 11 was then run
through this collector for the real gold window, 2026-07-01..07-26:

| publisher | misses | result | reachable |
|---|---|---|---|
| SmartCompany | 3 | 218 URLs, 22 of 26 days | YES |
| THE BRIDGE | 1 | 65 URLs, 12 of 26 days | LIKELY |
| PR TIMES | 2 | 4 URLs (root index points at /tv/; main sitemap is the 48h news one) | NO |
| Globes | 2 | 0 URLs, news sitemap only | NO |
| Wamda | 2 | 0 URLs, serves no sitemap at all | NO |
| BetaKit | 1 | 0 URLs, news sitemap only | NO |

**So the ceiling is 4 of 51, about 8%, before the ration cuts it further.** That
is why this ships dispatch-only with its cost table attached rather than as
advice to run it. The Google News walker reaches all 51 in principle
(`widest_route` is `google_news` for every one of them) and is limited only by
its ration; **if one walker is to be dispatched for this measured miss, it is
that one.**

### Proven, and not proven

Proven: `--fetch-only` over the catalogue's first 12 publishers,
2026-07-01..07-26, real network. 11 publishers read in 6 minutes (33s each,
which is what sized `PUBLISHERS_PER_SLICE = 40` rather than 60), **6 of 11
reached back into the window**, 1,833 URLs, 48 headlines at a deliberately small
`--max-heads 8`, 14 past the free prefilter, real Cameroonian, Zimbabwean and
Congolese leadership and jobs headlines at the gate boundary.
`data/talent_intel.db` byte-identical before and after.

NOT proven: a real `--dry-run`, which classifies and therefore costs money. No
model has read a single item from this collector and no row has been stored.
`data/press_archive_health.json` does not exist yet.

One consequence to know before the first real run: a `press_archive` row carries
`source_name` = the publisher's own name, which
`source_registry.COLLECTOR_BY_SOURCE_NAME` already maps to `national_press`. The
sources page will therefore attribute it to that collector. That is defensible —
the SOURCE is the publisher either way and only the route differs — but it means
the page will not show `press_archive` as a running collector, and it must not
be "fixed" by typing a second map in PHP.

`staleness.py` gets `press_archive: 2400`, the dormant/dispatch-only leash, to be
tightened the day a pace is chosen. `drain-writers.yml` watches the new workflow,
so a slice cannot finish without waking the drainer.

---

## 2026-07-30 — the canonical decides who published it, CTech has no feed to wire, and the audit that says why we miss things is finally printed

Three fixes from one brief. Every figure below is measured; two of the brief's
own premises turned out to be wrong when the code and the live hosts were read,
and both are corrected here rather than quietly worked around.

### 1. `finance.yahoo.com`: block on the CANONICAL host, not the requested one

`validate._BLOCKED_SOURCE_HOSTS` matched on the EXACT host and listed
`news.yahoo.com`, so `finance.yahoo.com` and `sg.finance.yahoo.com` were never
compared to anything. **Three current rows** were cited to an aggregator.

The part that makes this a design fault rather than a missing entry:
`collectors/national_press.py` had ALREADY learned this and derives its
`_AGGREGATOR_DOMAINS` from the registrable domain, with a test
(`test_finance_yahoo_is_already_blocked_and_needs_no_second_entry`) asserting
that `finance.yahoo.com` is covered. So one rule lived in two layers, the two
disagreed, and **the layer deciding what may be STORED was the weaker of the
two.** `validate` now derives its domain set the same way, from the same host
list, so a host added to one is blocked on every subdomain of the other.

**A blanket domain block would have been wrong, and the canonicals say so.**
Checked live on 2026-07-30:

| row | `rel=canonical` |
|---|---|
| 7-Eleven | `www.cstoredive.com/news/7-eleven-names-new-ceo/826096/` |
| Haus Cramer Gruppe | `www.just-drinks.com/news/haus-cramer-gruppe-names-new-ceo/` |
| HSBC (`sg.finance.yahoo.com`) | **itself** |

Two of the three are a publisher's article behind a syndication URL and one is
the aggregator all the way down. `cstoredive.com` is a publisher this corpus
**already reads directly** — it holds Iowa 80 Group and Warrenton Oil rows from
that outlet. Refusing all three on the host would have thrown away two
publishers we can name, for a tidier rule.

So `validate.prefer_canonical()` follows the pointer and REWRITES `source_url`
to the publisher before anything else is judged, which is CLAUDE.md's
"aggregators are discovery pointers" being kept rather than excepted. It never
fetches: the canonical must be supplied by whatever read the page, because
validate runs on every candidate before any money is spent and a network call
there would be a per-candidate one.

**Backward half: `correct_aggregator_sources.py`.** Its worklist is DERIVED — it
asks `validate.is_aggregator_host()` the same question the write path asks — so
it covers whatever the next edit to that function moves, and needs no new
script. Run against a COPY of the database, because backfills were draining
through the writer queue at the time and the committed database is theirs:

```
3 current rows cited to an aggregator
  7-Eleven            -> C-Store Dive   revised, rev1 is_current=0, rev2 current
  Haus Cramer Gruppe  -> Just-Drinks    revised, rev1 is_current=0, rev2 current
  HSBC                -> LEFT ALONE (canonicalises to itself)
repaired 2, left for a human 1
```

**It does not retract.** A row whose canonical is the aggregator itself is
printed, named and left; an automatic retraction driven by an HTTP response
would let a publisher's bad afternoon delete evidence, which is the reasoning
`link_check.py` already carries.

Three things measured rather than assumed:

- **`content_hash` does NOT move.** `source_name` reaches it only through
  `strip_outlet_suffix()`, and the hashed payload is
  `company_key|pillar|published_date|normalised_headline`. Neither headline
  carries a trailing " - Outlet", so both fingerprints are unchanged
  (`dded0fa4b713`, `c4df895b98c8`). The script ASSERTS this and refuses rather
  than proceeding if it ever stops being true, because a moved fingerprint means
  the live row can never be matched again.
- **`og:site_name` has to be read from the PUBLISHER's page.** Read off the
  aggregator's copy, both rows came back labelled "Yahoo Finance" — the exact
  name this pass exists to stop citing. One extra fetch gets "C-Store Dive".
- **Blast radius of registrable-domain matching: zero.** Across 15,711 current
  rows, `google.com` 0, `msn.com` 0, `flipboard.com` 0, `yahoo.com` 3. No
  catalogue feed sits on any of those domains.

**The part that does not reach the live page, stated plainly.**
`tit_correctable_columns()` is `signal_direction, talent_readthrough, city,
region, country`. `source_url` and `source_name` are in neither it nor the
enrichable set, so this corrects the repo's memory and NOT the page. Because the
fingerprint is stable, widening that allowlist would be enough; nothing needs a
withdraw-and-republish.

11 new tests in `tests/test_canonical_source_host.py`, including the two cases
that matter most: a canonical pointing at ANOTHER aggregator is not followed,
and a canonical never rescues a row that fails a different check.

### 2. CTech: the brief's premise is wrong, and the empty column is a finding

The brief said CTech's `rss` column is empty "and the reason is mundane", asking
for a one-field fix. **There is no feed to put in it.** Re-verified 2026-07-30
against the live host with the collector's own browser UA:

| checked | result |
|---|---|
| `/ctechnews` | 200, 186,208 bytes, **no `rel=alternate`**, and no rss/feed/.xml URL anywhere in the markup |
| 21 candidate feed and sitemap paths | **all 404** |
| `robots.txt` | 200, 34 lines, every one a `Disallow`, **no `Sitemap:` directive** |
| the legacy "RSS FOR CALCALISTECH" page a search surfaces | itself **404** since the `/ctechnews` relaunch |
| Wayback CDX, **20,000** captures on the domain | **no feed URL, ever** — every hit is an article URL with a `/feed/` suffix that 404ed at capture time |
| parent `calcalist.co.il` | **403** on every path including the legacy `GeneralRSS` one |

The catalogue row already said this and it was right. It now also carries the
21 paths and the Wayback result, dated, so **no future session repeats the
probe.** The drift guard cannot be "armed for the host" because with no `rss`
the row is never loaded as a `Feed` at all; `expected_domains` has nothing to
compare.

**And the outlet is not unreachable.** Measured: **2 current rows cite
`calcalistech.com`, both found through `google_news`**, which resolves each item
to the publisher's own article URL. Israel has **10** publisher feeds actually
loaded by the collector (Globes x3, Geektime, NoCamels, Techtime, Haaretz, Ynet,
Jerusalem Post, Israel Innovation Authority).

The audit is more precise than "4 of 81": Israel has **9** misses — **5
`outside_our_history`** and **4 `publisher_not_wired`**, and all four of the
latter are CTech, while three of the five former are CTech too. So a feed, had
one existed, would have closed 4 and not 7. The real options are an HTML
collector against `/ctechnews`, or `google_news`, which already reaches it. The
discovery backstop is NOT one of them: it is country-scoped by design and says
so in its own header, and Israel already has ten direct feeds.

### 3. The rejection audit is printed now, as a diagnosis

`data/recall_rejection_audit.json` had been produced since 2026-07-29 and
surfaced NOWHERE — nothing ran it on a schedule, nothing committed it, and
`ops_status.py`, the file every session is told to run first, did not mention
it. Three links in that chain, all three now asserted by
`tests/test_rejection_audit_surfaced.py`, because any one breaking leaves the
other two looking fine:

1. `recall.yml` **runs** it (`--write`), after the measurement so it audits the
   corpus that measurement just scored, `continue-on-error` so a lost diagnosis
   can never cost the recall figure that is the point of the job.
2. `recall.yml` **commits** it, in `paths`.
3. `ops_status.py [3c]` **prints** it.

It reads as a roadmap, not a scoreboard, and the zero is read aloud:

```
[3c] WHY WE MISS WHAT WE MISS  (the feed roadmap, from the gold set)
    measured 2026-07-28 on gold set 2026-07-v1: held 8 of 89, missed 81
      0   0%  fetched_then_dropped   a filter rejected it
     51  63%  outside_our_history    older than the collector
                                       -> BACKFILL. Not filters, not sources
     12  15%  publisher_not_wired    researched, not connected
     11  14%  publisher_unknown      not researched
      7   9%  feed_read_item_missed  feed depth or run cadence
    READ THE ZERO: no filter has ever rejected a gold event. The corpus is
    young, not leaky.
```

Every percentage is computed from the file, and a test asserts that rather than
trusting it. **Deliberately not an ACTION NEEDED item**: a young corpus is not a
fault, and a permanent red on a number only time can move would train the next
session to ignore the exit code. A test pins that too.

**One thing the audit gets wrong, found while wiring it and NOT fixed here:**
its `unwired_publishers` list names `yahoo.com` as a publisher with 1 miss. It
is an aggregator, which fix 1 above now enforces on the write path, so that row
is a target that cannot be wired. `businesswire.com`, `globenewswire.com` and
`prnewswire.com` sit in the same list and are wire services rather than
publishers, which is a different argument and a real one. Left alone because it
is the audit's own classification and changing it changes a published number.

### Not done, and it is the big one

**The competitor diff (`tracker_diff` in the sibling) was NOT built.** It is the
largest gap in this tracker's learning loop and it costs $0, and it is a whole
collector plus a chase path plus a feedback ledger; started at the end of a long
session it would have been a half-built source, and a source that forgets
`raw_text` posts zero records silently. Two constraints for whoever picks it up,
both found in this session's work rather than in the brief:

- **Import `registrable_domain`, do not write a third one.** There are ALREADY
  two in this repo — `collectors/national_press.py` and
  `analysis/recall/rejection_audit.py` — and a third, deciding which competitor
  events count as ours, would be the one that matters most and the likeliest to
  go stale. `pipeline/validate.py` needed the same function this session and
  imports it (deferred, so `pipeline` takes no module-level dependency on
  `collectors`).
- The sibling's substring bug the brief describes is the same class of fault as
  the yahoo one fixed above: an exact-or-substring host test where the question
  is really about who owns the domain.

### Measured

| | |
|---|---|
| offline tests | 2,263 pass (was 2,172 at session start; other work landed in parallel) |
| PHP harnesses | 6 pass |
| `ops_status.py` | exits 2 before and after, for five collectors stale on wall-clock time and nothing here |
| model calls added | **0**. Both new paths are HTTP and string comparison |
| rows written to `data/talent_intel.db` | **0** — corrections proved on a copy, because backfills held the real one |

## 2026-07-30 — the filter sidebar is reversed into a frozen bar, and the column was what squeezed the table

Plugin **1.55.0 -> 1.56.0**. **This reverses the sidebar shipped in 1.54.0.**
That pass built a 262px sticky column of seven capped scrolling checkbox boxes,
to my predecessor's instruction, reading the owner's "filters dont move with the
page a like the layoff one" as a request for the sibling's layout. The owner has
now seen it on the live page and asked for the opposite. It was not always the
plan and this entry does not pretend it was.

The owner sent two complaints, and they are one complaint:

1. "the formatting do you see this? Make them more compact" — the What Happened
   column wrapping to one word per line.
2. "the filter so complicated with the scrolling up and down should we move
   those to above the stuff and compact and have it frozen on top when you
   scroll down??"

**(2) causes (1), and that is measured rather than argued.** The column plus its
20px gap took 282px. Rendered at 1280px against the real stylesheet:

| | 1.55.0 | 1.56.0 |
|---|---|---|
| filter panel width | 264px | 0 (it is a bar) |
| `.tit-results` width | 876px | **1,158px** |
| table width | 994px | 1,158px |
| **What Happened column** | **97px** | **187px** |
| table needs a horizontal gesture at 1280px | **YES** (994 into 874) | **no** |
| elements past the viewport edge at 1280px | 155 | **0** |

The 97px is the whole of complaint (1): a cell carrying a headline AND a
read-through, in 97px. Widening that column alone would only have taken the
space from another one, which is why the brief's instruction to fix the layout
first and re-measure was right.

### What replaced it

A compact bar above the results, `position:sticky` at `top:0`, holding every
control as ONE LINE. Each multi-value filter is a button carrying its own name
and a printed count; its checkboxes live in a panel that exists only while it is
open. The checkboxes did not change — `pillify()` still renders the same
`.tit-optrow` inputs from the same `<select multiple>`. What changed is that all
seven groups no longer have to be on screen at once, which was the entire cost
of the column.

**The flat alternative was refused.** Seven open checkbox lists laid across the
top is the same wall of options in a worse place, and the brief said so.

**Compactness, measured at 1280px with all fourteen controls present:**

| | height |
|---|---|
| labels stacked above controls | 280px (31% of a 900px viewport) |
| labels inline, 10px gutter, 158px control ceiling | 193px, three wrapped rows |
| labels inline, 8px gutter, 140px ceiling | **141px, two wrapped rows** |

The last two numbers are three pixels apart in cause: at a 10px gutter the
fourteenth cell missed the second row by 3px and wrapped alone onto a third.
Both constants are commented in `dashboard.css` as load-bearing. Nothing breaks
if a longer vocabulary pushes it back to three rows — it is a bar, it wraps —
but neither number may be widened without re-measuring.

Location became a dropdown too, and that trade is stated rather than hidden: its
value stops being readable on the bar, which is a real loss for the most-used
filter here. It buys the qualifier ("Only Countries A Source Named") travelling
with the control it qualifies instead of sitting on the bar as a 200px sentence
beside an unrelated control, and it is what took the bar from 193px to 141px.
The chips bar directly below already names the chosen place in words and offers
the way out of it.

### The phone, decided rather than inherited

Fourteen controls at 390px is four wrapped rows, and a sticky four-row bar pins
most of the viewport — the same mistake rotated. Below 900px the bar is its head
only: one **Filters** button with the chips count on it, opening the controls
beneath it as a sheet **in normal flow**, not fixed. A fixed sheet either traps
the page scroll or floats over the rows it filters, and the jump bar already
holds the fixed-position budget at the bottom of this page. The bar also stops
being sticky there, because two pinned bars fight — which is a finding from the
sibling's own history, not a guess.

Measured at 390px: **body `scrollWidth` 390 = `innerWidth` 390, 0 elements past
the viewport edge, 0 containers needing a horizontal gesture** — collapsed, with
the sheet open, and with a dropdown panel open. Unchanged from 1.55.0, which was
also 0/0/0.

### Three defects caught by measuring, two of which would have shipped

1. **`.tit-bar` was already taken.** It is the chart bar TRACK
   (`height:8px`), used by `places.php`, `shortcodes.php` and `dashboard.js`.
   The new bar rendered 10px tall. Renamed `.tit-filterbar`; the rename then
   over-matched and swallowed three chart rules, which the same measurement
   caught on the next pass.
2. **Converting the `<label>` wrapper to a `<div>` dropped `hidden`.** A
   `<label>` forwards a click to its own control, so a trigger button inside one
   also activates the select it hides; the wrapper therefore has to stop being a
   label. Copying only `class` and `id` lost the `hidden` attribute, and **five
   always-empty facet controls appeared on the bar** — Employer Type, Work
   Setup, Funding Stage, Deal Type, Site Change — which is precisely the failure
   that attribute exists to prevent. Now every attribute is copied except `for`.
3. **The help disclosure escaped its own `<details>`.** `position:absolute` on a
   child of a closed `<details>` defeats the UA's hiding, so the panel rendered
   permanently, over the controls, on a disclosure reporting itself shut. It
   showed up as two elements overflowing at 390px rather than by being looked
   at. Now hidden explicitly, and anchored to the bar head rather than to the
   summary — anchored to the summary it started at x=104 on a 390px screen and
   ended 80px off the right edge.

A fourth, found and fixed the same way: a panel open across a **resize** keeps
an `is-flipped` decision made for a viewport that no longer exists. Opened at
390px and widened to 1280px it ran 59px past the edge and put a scrollbar on the
body. A `resize` listener closes whatever is open.

### What did not move

- **No new state channel.** The `<select multiple>` is still the state. The
  querystring, chips bar, exports, quick views, click-to-filter, matrix cells
  and facet refills read and write it exactly as before; the dropdown layer
  hangs off `pillify()` and `dropCount()` and nothing else. Verified in a real
  DOM: two ticks fire **exactly two** change events and select two options; an
  untick fires one and leaves one; an **external write followed by a repaint**
  (what a chart tap, a deep link and Reset All all do) re-renders the checkboxes
  and the badge to match. That last case is the one that silently rots.
- **No-JS still gets working native controls.** Verified with the script tag
  removed: bar fully open at 305px, toggle hidden, `<select multiple size="5">`
  rendered at 140x131 and usable, labels, date inputs, place select and basis
  checkbox all visible. Every part of the dropdown layer is built at runtime, so
  a page whose script never ran is missing nothing.
- **Config still rides on `data-` attributes**, and nothing was added to
  `wp_localize_script`. No new inline object for Autoptimize to reorder.
- **`TIT_DASH_QUERY_BUDGET` untouched at 12 cold / 0 warm.** The N+1 tripwire
  reports the same count. This pass added no query.
- **Markup 166,802 -> 167,299 bytes** (+497, +0.30%), inside the budget.
- The routine-filings disclosure, the chips bar, the honesty surfaces and every
  control label are unchanged. Title Case is still asserted and still passes.

### Accessibility, where this is deliberately better than the pattern it copies

The sibling's dropdown has no Escape handler, no focus return, no
`:focus-visible` on the trigger, sets `aria-expanded` only on first interaction,
and claims `aria-haspopup="listbox"` over a panel containing no listbox roles.
All five are fixed here: Escape closes and **returns focus to the trigger**
(verified: `document.activeElement === trigger`), the trigger has a focus ring,
`aria-expanded` is set at construction, and nothing claims a listbox — what is
in the panel is a group of checkboxes and that is what it says. Tabbing out
closes the panel; a click on the panel's own padding does not, because that
reports a null `relatedTarget` and closing on it would shut the panel under the
reader's finger.

### Tests

`render_dashboard.php`'s assertion that the panel is a COLUMN is replaced by one
that the bar comes **before** the results in the document, plus three new ones:
the phone toggle exists, ships `hidden`, and carries `aria-expanded` at
construction. One existing assertion was also corrected rather than bumped: it
counted `aria-controls=` across the whole page and asserted 2, which quietly
meant "no other element on this page may ever control another" — the filter
bar's toggle legitimately points at the panel it opens and failed a test about
tab semantics. It now counts per tab element.

The harness gained an optional `TIT_DUMP_HTML` env var that writes the rendered
markup out for measuring in a real browser. Off by default. It exists because
three of this page's properties cannot be asserted from a string — whether
sticky actually pins, whether anything overflows 390px, and how wide a column
ends up — and the sticky one fails silently.

**6 PHP harnesses pass, 2,181 offline tests pass.**

### NOT DEPLOYED, and the reason is the same as last time

The brief asked for a deploy and a live verification. My standing instruction is
**do not push**, and `deploy-plugin.yml` uploads from a checked-out git ref, so
shipping this needs the branch pushed first. Publishing to the live site is also
the owner's call to make and not an agent's. So 1.56.0 is committed and
unshipped; the live page stays on 1.55.0. Everything in the tables above was
measured against the real render in a real browser, not against the source. The
one thing NOT verified is how it LOOKS: screenshots came back blank from this
pane, so every claim here is a measurement and none of them is an eyeballing.

## 2026-07-30 — the writer queue stopped for six hours behind eleven green ticks

**Root cause: one input the workflow does not declare.** At 17:42:17Z a GDELT
backfill was queued carrying `slice: "true"`. `backfill-gdelt-2026.yml` declares
five `workflow_dispatch` inputs — `start`, `end`, `dry_run`, `fetch_only`,
`max_readthroughs` — and `slice` is not one of them. The dispatch answered:

```
gh: Unexpected inputs provided: ["slice"] (HTTP 422)
```

**The 422 was not the outage. `set -euo pipefail` was.** That `gh api` call sat
under `set -e` in the "Dispatch it" step, so bash killed the step *on that line*
— before the verification loop below it, and before the `writer_queue.py
requeue` below that. Run 30567135192 lasted **16 seconds**; the verify loop
alone sleeps 60. The ticket was left in state `dispatched` with `run_id: null`.

Every tick after that found **zero tickets in state `queued`**, so
`tick()` returned `dispatch: None`, wrote no `plan.json`, and the workflow's
`if: steps.tick.outputs.planned == '1'` skipped the dispatch step. **Eleven
drain runs between 10:17Z and 18:09Z, every one green**, the queue file changing
only its `last_tick` line (`ced0ab4..7a31ade`: one insertion, one deletion).

Reproduced offline against the committed queue and a 200-run snapshot: `tick`
exits 0, prints nothing at all, emits no plan.

**Two things the diagnosis got wrong on the way in, both worth recording.**
`WRITER_QUEUE_TOKEN` was unset and was assumed to be the cause; it was not, and
setting it changed nothing, because the API had accepted the credential and
rejected the payload. And the queue was read as "24 tickets stuck": it held 24
tickets of which **22 were `landed`, 1 was `failed` and acknowledged, and
exactly one was live**. Counting the list rather than the states inflated a
one-ticket stall into a twenty-four-ticket one, and the 17 orphans were all
already `resolved` — `resolve` marks in place rather than removing.

### What changed

| Guard | Where |
|---|---|
| A ticket carrying an input the workflow does not declare is refused **at enqueue time**, naming the declared ones | `writer_queue.workflow_dispatch_inputs`, `enqueue` |
| The dispatch API call no longer runs under `set -e`; its exit code is captured and every failure path records the ticket and goes red | `drain-writers.yml` "Dispatch it" |
| A 422 on the inputs marks the ticket `failed` (`dispatch-failed --permanent`), because retrying a deterministic refusal is an infinite silent loop | same |
| The unbound requeue is counted (`unbound_count`) and reported; two vanished dispatches for one ticket is a `problem` | `tick`, `summary` |
| `idle_since`: work waiting + lock group empty + nothing dispatched, recomputed every tick, red after 90 minutes | `tick`, `summary`, `ops_status [2b]` |
| A tick that dispatches nothing says **why**, every time | `_cmd_tick` |
| The failed-dispatch record is pushed with the retry loop, not `git push \|\| true` | `drain-writers.yml` |

**90 minutes, not 15.** The `*/15` cron is throttled by GitHub to 34-60 minute
gaps on this repo (measured across nine consecutive scheduled ticks, 10:17Z to
17:31Z). Any stall threshold under an hour fires on a single ordinary gap.

**The alarm is derived, not stored.** `idle_since` is recomputed from the run
list on every tick and cleared only by a real dispatch, a busy group or an empty
queue — so editing it out of the committed file buys one tick of silence and no
more. That is the property `test_the_stall_clock_is_recomputed_from_facts_not_trusted`
pins.

**Undeclared inputs raise; missing *required* inputs only warn.** They are not
symmetrical: an undeclared name is always a typo, whereas `enqueue` is also the
canonical "can this workflow be queued at all" assertion, called with a token
input and no intent to dispatch (`tests/test_backfill_slices.py:233`). The
parser is regex-and-indentation rather than yaml because `ops_status.py` imports
this module and must stay dependency-free; it is checked against a real
`yaml.safe_load` for all **20** current lock members, so a formatting change
fails the suite instead of production. It returns `None` — skip validation —
when it cannot parse, because a parser that guesses wrong must fail open.

22 tests added; suite 2,099 -> 2,121.

---

## 2026-07-30 — the registry backfill: two of the four were already reachable, and India's ceiling is 32 days

Brief: build the 2026 historical backfill for the structured registry
collectors, on the premise that they all expose `as_classified`, so their spend
is $0 and back-filling them is the cheapest coverage win available. **The
premise is exactly right and the model spend for this session was $0.00.** What
the brief was wrong about is which of them needed anything built.

### What is actually held, measured first

`data/talent_intel.db`, 2026 rows by collector, current revisions only:

| collector | rows, all time | rows in 2026 | verdict |
|---|---|---|---|
| `sec_edgar` | 3,797 | **3,797**, every week of 2026-W01..W30 | **complete, no-op** |
| `sec_form_d_bulk` | 2,998 | 2,998, Jan..Jun | complete to the last published quarter |
| `uk_paygap` | 4,761 | 537 | **complete** — 2017..2025 run 403 to 595 a year |
| `sec_execcomp` | 3,910 | 133 | **complete for its shape.** `published_date` is the fiscal PERIOD END, so a CY2026 row needs a fiscal year that has ended in 2026. 2022..2025 hold 574 / 1,010 / 1,091 / 1,102 and 2026 fills as proxies land |
| `bse_india` | **0** | **0** | never run |
| `companies_house` | **0** | **0** | never run |
| `edinet_japan` | **0** | **0** | never run |
| `opendart_korea` | **0** | **0** | never run |

So "is 2026 already held" is **yes for all three SEC/UK sources and zero for
every registry collector**. The brief's guess that "some of this may be a no-op"
was right about which sources and right about why: the ~7,700 rows the dashboard
shows for 2026 are SEC plus the pay gap, and `backfill_sec_2026.py` already
walked them.

### Then: can each API even express a historical window? Two of four could

This is the question that decided what got built, and the answer is not the same
for any two of them.

| source | window it can express | reachable through `collect-structured.yml` today | built |
|---|---|---|---|
| `edinet_japan` | a LIST of calendar days; `MAX_DAYS` 366 | **yes.** `days=211` is one run of 211 calls at 0.5s — about two minutes | **nothing** |
| `companies_house` | `appointed_on` filter, any width, no state at all | partly: `days=211` + `ch_slice=0..3`, four dispatches, no cursor | walker |
| `bse_india` | **32 days.** Server-enforced, undocumented | **no** | walker |
| `opendart_korea` | 90 days, AND anchored on today | **no** — Jan..Apr unreachable | walker |

**`edinet_japan` needed nothing and gets nothing.** Its collector docstring
already says "a backfill widens the window; it does not become a script", its
own cap is a year, and one dispatch closes 2026:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
     -f inputs_json='{"source":"edinet_japan","days":"211","dry_run":"false"}' \
     -f reason='Japan 2026 catch-up'
```

A walker for that would be a second implementation of a cursor for 211 requests.
`test_edinet_is_absent_and_the_refusal_says_why` asserts the omission AND
asserts `edinet_japan.MAX_DAYS >= 366`, so if Japan's window ever shrinks the
omission stops being silently stale.

### THE FINDING: BSE refuses a window wider than 32 days, inside an HTTP 200

`collectors/bse_india.py` said a backfill is "a longer window through the same
path", and `collect-structured.yml`'s `days` input said "a gap is back-filled by
widening this". Measured live against `api.bseindia.com` on 2026-07-30, that is
**false above 32 days**:

```
strPrevDate=20260101, strToDate=20260131 (30d)  ->  200 {"Table": [50 rows]}
                                20260201 (31d)  ->  200 {"Table": [50 rows]}
                                20260202 (32d)  ->  200 {"Table": [50 rows]}
                                20260203 (33d)  ->  200 {"Status":"False",
                                                        "Message":"Date range
                                                         exceeded threshold."}
```

Binary-searched: 30/31/32 accepted; 33, 34, 35, 36, 40, 45, 90, 151 and 211 all
refused. The threshold is published nowhere. **The refusal is HTTP 200 with no
`Table` key**, so it landed in the collector's "the response shape has changed"
branch — a message that sends a reader looking for a redesigned API instead of
at a number in a workflow input. So India's history was not merely slow to
reach, it was unreachable through the documented route, and the error blamed the
wrong thing.

Three changes, all additive:

* `bse_india.WINDOW_CAP_DAYS = 32`, with the measurement beside it.
* `fetch_page` names the width refusal before the generic branch: *"BSE refused
  20260101..20260730 ... The undocumented ceiling ... is 32 days. This is a
  window that is too wide, not a changed API."*
* `days_from_env` refuses `TIT_BSE_DAYS > 32` rather than spending a run on a
  request that cannot succeed, and points at the walker.

Korea's ceiling is the quieter kind and was already documented: OpenDART limits
a `corp_code`-less search to three months and returns a **shorter window**
rather than an error, so a walker asking for 120 days would collect 90 and
record 120 as done. `window()` is also anchored on `datetime.now()`, which on
2026-07-30 put the earliest reachable day at 2026-05-01. January to April was
not a wide window away; it was unreachable. An explicit `--start` is the whole
fix.

### What was built

`backfill_structured_2026.py` + `.github/workflows/backfill-structured-2026.yml`.
One walker, three sources, `backfill_gdelt_2026.py`'s shape — monotonic
committed cursor, one slice a run, seen-URL skipping before any work, a `--plan`
summary, `--fetch-only`, `--dry-run`, and a `halt` path that records the slice
and declines to requeue into a wall.

**It is deliberately NOT a second priced walker.** GDELT walks news, so its
constraint is money and `--plan-cost` prices a pace. Every source here derives
its record from typed fields, so the constraints are the API ceiling and the
writer lock, and `--plan` prints **requests, wall clock and rate-limit
headroom** instead of dollars. There is no `--max-readthroughs`, no spend guard,
no gate — and `tests/test_backfill_structured.py` walks the module's AST to
assert `classify` is never imported, because a cap can be raised and an absent
import cannot.

Slice sizes, each derived from the API's own ceiling rather than picked:

| source | unit | slice | why that size |
|---|---|---|---|
| `bse_india` | days | **28** | four weeks, four days inside the measured 32-day ceiling, and it keeps the busiest sub-category at ~13 pages against the collector's `MAX_PAGES` of 40 — so a slice can neither be refused for width nor silently truncated for depth |
| `opendart_korea` | days | **60** | inside the documented 90, and ~56 list pages plus one `company.json` per filer |
| `companies_house` | **slices** | 1 of 8 | its cost is per COMPANY and nothing per day, so the ROSTER is what is walked |

**The roster cursor is a new unit in `backfill_slices.py`,** and it exists
because a date cursor for Companies House would be a lie: widening its window
from 42 days to 211 costs nothing (`appointed_on` is a filter over data the
endpoint returns anyway), while sweeping the 9,230-employer roster is 10,568
requests. So the job's `start`/`end` are slice indices `0..7` and the date
window rides on the job's committed `inputs`. `next_inputs` has an explicit
branch refusing to overwrite them — without it the next run would read a
one-day window and store nothing, silently, for seven of the eight slices.

**Eight backfill slices, not the rotation's four**, because the weekly job's
only work is the fetch while a backfill slice then puts ~590 rows through
validate/store/publish. `slice_of` is a blake2b digest, so any count partitions
the roster exactly once and the two do not have to agree; asserted over 4,000
numbers for both counts.

`backfill_slices.job_id` also gained an optional `label`. Three sources walking
the same 2026 window through one workflow would otherwise share one key and each
would resume where another stopped — a hole in one and a re-collection in the
other. It defaults to empty, so every cursor already committed keeps its id.

### Measured: two real slices, live, into a scratch database

`bse_india`, through the walker, 2026-07-30. Nothing was written to the
committed database at any point: `schema.DB_PATH` was pointed at a copy.

| slice | rows read | usable | stored | duplicate | wall |
|---|---|---|---|---|---|
| 2026-01-01..01-28 | — | **898** | **616** | 282 | **52s** |
| 2026-01-29..02-25 | 1,427 | **1,368** | **866** | 502 | **108s** |

The ~35% duplicate rate is `dedupe.fuzzy_duplicate` collapsing one employer's
leadership filings inside 14 days into one development, which is the intended
behaviour and the same factor `companies_house` was sized with. The chain was
driven end to end: slice 1 emitted a ticket with `next_cursor 2026-01-29`,
`record` advanced, slice 2 opened at exactly that day, `next_inputs` carried the
date window forward.

**A full 2026 walk, at a rate-limit-respecting pace** (`--plan`, which fetches
nothing):

| source | slices | req/slice | min/slice | rows/slice | rows total | req total |
|---|---|---|---|---|---|---|
| `bse_india` | 8 | 37 | **1.8** (measured) | 1,130 | ~9,000 fetched, ~6,000 stored | 296 |
| `companies_house` | 8 | 1,320 | 12.1 (paced) | 590 | ~4,700 | 10,560 |
| `opendart_korea` | 4 | 190 | 0.6 (paced) | 175 | ~700 | 760 |

So the whole 2026 registry catch-up is **20 queued runs, under two hours of
compute in total, ~11,600 requests and $0.00 of model spend**, for roughly
**11,000 rows** against a database that holds 15,711. India alone is more rows
than the tracker currently has from anywhere outside SEC and the UK pay gap.

Wall clock is printed **measured where a slice has actually been run and marked
`*` where it is arithmetic**, because the paced projection is only the time
spent waiting on the API: for `companies_house` that is almost the whole run,
for `bse_india` it is a twentieth of it (37 requests carrying 1,368 rows), and
projecting BSE from its pacing alone understates it by 20x. Two of the three are
unmeasured because `OPENDART_API_KEY_KR` and `COMPANIES_HOUSE_API_KEY_UK` are
GitHub secrets and are not set locally; every such figure says so in its own
`evidence` line, and a test fails if a projection is ever printed unmarked.

### Not armed, and the reason is different from the GDELT walker's

No cron, and `test_the_structured_walker_is_not_armed` refuses one. The reason
is written down because it is NOT the usual one: this walker is free, so a
reader looking for the cost argument will not find one and might conclude a cron
is harmless. It is not. Every source here writes the database and therefore
holds the single `talent-collect` lock, in which GitHub keeps exactly one
pending run, so a scheduled run enters that group uncoordinated and either
evicts the waiting run or becomes an unreplayable orphan.

The queue is currently blocked (`WRITER_QUEUE_TOKEN` unset, so a dispatch
produces no run and the ticket requeues), which makes **a slice being re-run the
ordinary case rather than the exception**. That is why the seen-URL skip is
before everything else and is measured: a repeated `bse_india` slice costs one
fetch and stores nothing, asserted by
`test_a_slice_stores_and_the_second_run_of_it_stores_nothing`. `companies_house`
is exempt from the skip and must be — its `source_url` is one PERSON's
appointments page and a person can be appointed twice, so skipping it on sight
would make the first appointment the last one that source ever reported. The
flag is read off `companies_house.REVISITS_ITS_SOURCE_URL` rather than restated.

### Figures round-trip, proved through the walker and not at the regex

Four silent data-loss bugs in three days came from the verbatim-figure guard
meeting non-Latin scripts and typographic separators, so both non-Latin sources
here are driven end to end rather than unit-tested:

* **India**: a filed description ending in `28.07.2026` at a company whose name
  begins with K — the exact newline-spanning `\s*` collision that read
  `28.07.2026\n\nK` as `28072026k` — stores, and
  `validate.assert_figures_are_sourced` agrees on the stored strings.
* **Korea**: full-width digits in the filer's Korean and English names survive
  the whole path, fold to ASCII, and the summary's figures are all present in
  `raw_text`. A companion test asserts that **NFKC is still not used**, because
  it rewrites the U+318D in `독립이사의선임ㆍ해임또는중도퇴임에관한신고` to
  U+119E and the report-name allowlist stops matching — the obvious blanket fix
  that would break the source.

### `staleness.py`: nothing changed, and that is the decision

The walker deliberately writes **no `source_health` row**, asserted by
`test_the_walker_writes_no_health_row`. Each of these collectors is leashed to
its WEEKLY cron (180h). If a backfill reported health it would reset that leash,
and a broken weekly run would be masked by a backfill that happened to succeed —
the leash measures whether the COLLECTOR ran, and a backfill is not that. The
backfill's own failure is a red run.

### Numbers

- Suite **2,044 -> 2,082, +38**, measured by running HEAD and the staged tree
  side by side rather than by counting the diff. 36 are written here — 30 in
  `tests/test_backfill_structured.py` and 6 in `tests/test_backfill_pace.py`
  (the not-armed test, the roster cursor's per-run property, a whole roster
  walk, the three-cursor property, the backward-compatible job id, and one more
  parametrized workflow) — and 2 are `tests/test_workflows.py` parametrizing
  over the new workflow file by itself.
- **$0.00** model spend, in the walker and in measuring it.
- 2 live slices, 898 + 1,368 rows fetched, 616 + 866 stored, 160s total.
- 1 undocumented API ceiling found, binary-searched and named in code.
- 0 rows written to `data/talent_intel.db`.

### What was refused

* **A walker for `edinet_japan`.** It is one dispatch of an existing workflow.
* **Arming anything.** No cron was added anywhere.
* **Re-fetching SEC or the UK pay gap.** 2026 is complete for all three; the
  counts are in the first table rather than an assurance.
* **A second cursor implementation.** `backfill_slices` gained a unit and an
  optional label, both additive and both defaulted so every committed cursor
  still resolves.
* **`run_collect.py` and `source_registry.py`.** Untouched — other lanes.

## 2026-07-30 — Google News DOES have an archive, and what it costs to walk it

`backfill_gnews_2026.py`, `.github/workflows/backfill-gnews-2026.yml`,
`tests/test_backfill_gnews.py` (18 tests). Suite 2,082 -> 2,100. Not armed.

### The premise this repo has been carrying, and its measurement

Three files said, in three wordings, that **"Google News RSS has no archive; it
serves a recent window and nothing else"** — `backfill_gdelt_2026.py`'s opening
paragraph, `backfill-gdelt-2026.yml` line 3, and this log at line 503. It is the
stated reason GDELT exists, and it was never tested. Measured 2026-07-30, one
leadership query, `en:US`, counting items and the pubDate span of what came back:

| query | items | pubDate span |
|---|---|---|
| no operator | 100 | 2026-03-11..07-30 |
| `when:7d` | 50 | 2026-07-23..07-30 |
| `after:2026-01-01 before:2026-02-01` | 100 | 2026-01-02..01-30 |
| `after:2026-01-01 before:2026-01-08` | 41 | 2026-01-01..01-08 |
| `after:2026-01-05 before:2026-01-06` | 16 | 2026-01-05..01-06 |
| `after:2025-03-01 before:2025-04-01` | 92 | 2025-03-03..04-01 |
| `after:2021-03-01 before:2021-04-01` | 36 | 2021-03-01..04-01 |
| `after:2016-03-01 before:2016-04-01` | 12 | 2016-03-01..03-29 |

`after:`/`before:` are honoured, the returned dates fall inside the window, and
the archive reaches at least a decade back. All three files are corrected in
place rather than left to be re-inherited.

### The cap is 100 and there is no pagination, so the window is one day

Slicing recovers what a wide window loses, and the recovery was measured rather
than assumed. January 2026, same query:

| | unique articles |
|---|---|
| one 31-day query | 100 (at the cap) |
| 31 one-day queries | 170 |
| in daily, not month | 70 |
| **in month, not daily** | **0** |

The month's set is a strict SUBSET of the daily sets, so a day window loses
nothing and finds 70% more. Busiest single day of that month: 22 items against
the 100 cap, 4.5x headroom, which is why a day is enough and half-days are not
needed. `RESULT_CAP` and a truncation counter in the run report are the guard —
a query that comes back at exactly 100 has silently lost the rest of its window,
and the answer is a narrower window, never a broader query.

### The aggregator problem is not worse with age. It is not a problem at all

Google News is a discovery pointer and the publisher is what we store, so a
historical pointer is worth nothing unless it still resolves. 54 items sampled
across four windows, resolved through `resolve_source_url` and then fetched:

| window | resolved to a publisher deep path | HTTP < 400 |
|---|---|---|
| 2026-01 leadership | 12/12 | 6/12 |
| 2026-04 leadership | 12/12 | 11/12 |
| 2026-07 leadership | 12/12 | 10/12 |
| 2021-03 leadership | 8/8 | 7/8 |
| 2026-01 funding | 10/10 | 8/10 |

**54 of 54 resolved**, every one to a deep path rather than a homepage, at every
age including a five-year-old window. The non-200s are the known bot-wall set —
bizjournals, bloomberg, businesswire, axios, costar — the same population
`link_check.py` already classifies as `bot_walled` rather than rotted, and the
same rate the daily collector's own URLs show (89 live / 10 bot-walled of 101 on
2026-07-29). Age is not a factor in either resolution or liveness.

### The funnel, measured on three real historical days

Full 52-edition sweep, 156 requests per day, nothing written:

| | 2026-01-14 | 2026-02-11 | 2026-03-18 |
|---|---|---|---|
| wall clock to fetch | 2.7 min | 2.6 min | 2.5 min |
| items after URL de-dup | 643 | 679 | 666 |
| past the free prefilter | 401 | 444 | 417 |
| **already seen** | **0** | **0** | **0** |

Zero already-seen across 1,262 candidates is the direct confirmation of the
rejection audit's finding: this history was never fetched, so it was never
filtered out. It is missing because we did not exist yet.

On a 140-candidate sample of 2026-02-11, run through the free reducers in
`run_collect.py`'s own order: resolution 0.26s/item on a keep-alive session
(so ~1.9 min for a whole day), clustering removed 1, precheck rejected 0,
`cheap_extract` closed 8 for $0, and **94% reached the gate**. The free stages
that carry the daily collector barely help on virgin history, which is the
finding that decides the shape below.

### The refusal, with its number

At the ledger's measured prices (gate $0.00003, read $0.00128) and its measured
gate survival (155 survivors of ~1,050 screened, 15%):

| | |
|---|---|
| one day of history swept IN FULL | **$0.0877** (gate $0.0119 + read $0.0758) |
| a year of 2026 in full | **$32.09** |
| ...of which the GATE ALONE | **$4.34** |
| GDELT's whole year, reads included | $4.51 |

**Merely LOOKING at a year of Google News across 52 editions costs as much as
GDELT's entire year.** A full-breadth sweep is 7x GDELT and is REFUSED here. It
is not a pace anybody can choose out of a ~$5/month budget.

### So the budget buys a RATION, and that is the one real divergence from GDELT

A read ceiling alone — the shape `backfill_gdelt_2026.py` uses — cannot fix
this, and would break the chain outright. A day of history demands ~59 reads; a
budget-derived ceiling is in the tens; the ceiling therefore binds inside window
ONE, the run finishes no window, `done_through` never moves, and
`backfill_slices.record` correctly refuses to requeue a cursor that did not
advance. **The chain would stall on its first slice with a green exit code** —
which is this repo's most-repeated failure mode, arriving through the mechanism
built to prevent it.

So `DAILY_GATE_RATION` rations the GATE, derived from
`MONTHLY_WALKER_BUDGET_USD` (`$1.00`, deliberately below GDELT's `$1.50`,
because the two share one product budget and GDELT's chain is the one already
dispatched). `pipeline.candidate_rank` — free, no model, no network, and a
permutation rather than a filter — decides which candidates of a day get it, so
the reads land on the country need the recall worklist measured. **A window that
spends its ration is FINISHED**, and everything past the cut is left UNMARKED,
so a second walk of the same range skips what the first stored (free, via
`store.already_seen`) and spends its ration on entirely different rows.
Coverage converges by repetition at the owner's pace instead of demanding $32
up front.

```
  slice = 4 day-windows, ration 37 candidates gated per day
  that reads 9.4% of a day, ranked by country need

  pace                    wall clock   $/month   $ 2026   reads
  1 slice/day                92 days      0.99     3.02    2042
  2 slices/day               46 days      1.97     3.02    2042
  4 slices/day               23 days      3.94     3.02    2042
```

The 9.4% is printed, not implied. A walker that claims to read a day it samples
a tenth of is a coverage claim, not a budget.

### Why it is worth building at all, given GDELT is cheaper

`registry.GDELT_QUERIES` is English-only by design — the comment above it says
so, and says why (reusing the Google News strings produced 216 noise items of
219). The recall worklist's zero-scoring markets are AU, CA, JP, GB, IN, BR, CN,
DE, SA, SG, AE, AR, CH, CO, with non-US funding at 2.3% held. GDELT cannot walk
the history of a market it cannot ask in that market's language. Google News has
51 non-English editions with live-verified phrase packs, reachable with the same
`after:`/`before:` operators. **The two walkers are complements, not
substitutes**, and the cheaper one structurally cannot do this job.

Measured on 2026-02-19 across `en:US,he:IL,ja:JP` in `--fetch-only`: 130
articles, 120 past the free filter, ration cut it to 37, and the would-gate
split was US=23 JP=10 IL=4 — Hebrew and Japanese leadership and funding stories
from a day in February that no collector had ever seen.

### What is asserted, and what was refused

`tests/test_backfill_gnews.py` pins the properties rather than the symptoms:
a historical query carries `after:`/`before:` and never `when:` (mixing them is
an empty set for every day older than the recency figure, silently); a locale
without a `GOOGLE_NEWS_VOCAB` pack is refused rather than defaulted to English;
a query at the cap is counted as truncated; the ration is derived from the
budget and moves when it moves; the gate's price includes the read it buys;
**a window that spends its whole ration still finishes and still advances the
cursor**; two runs recorded inside one frozen clock second walk different
windows (the sibling's date-ordinal trap, `~$3.80/day for six days`); a retried
run after a requeue resumes at the cursor and not at the dispatch input; a
finished job re-dispatched does nothing rather than starting over; a dry run
emits no ticket and writes no cursor; and a `--fetch-only` run calls no model
AND writes nothing.

That last one was a real bug found by writing the test. The free reducers
`mark_seen` their rejections, which is a database write, so `--fetch-only`
would have consumed the very candidates it was rehearsing. Fixed with a single
`writes` flag every write path asks.

**Refused, with reasons:**

* **A full-breadth year walk.** $32.09 against a ~$5/month budget. The number is
  printed by `--plan-cost` under the heading THE REFUSAL so nobody has to
  rediscover it.
* **A read-only ceiling, GDELT's shape.** It stalls the chain on window one, and
  it leaves the gate cost unbounded at $4.34/year.
* **Widening the window to a week or a month to save requests.** It truncates at
  100 and the truncation is silent. The 31-day query returned 100 while the days
  under it returned 170.
* **Any cron.** The ration IS the budget and a cron multiplies it by the runs per
  day. `test_the_google_news_walker_is_not_armed` fails if one appears.

### The one number in the model not measured here

`GATE_SURVIVAL = 0.15` comes from the daily collector's ledger (155 survivors of
~1,050 screened), not from a historical window, because measuring it needs the
API key and therefore real spend. It is a named constant with that provenance in
its comment so a future session corrects it in one place rather than in six
arithmetic expressions. `--max-readthroughs` is a per-run backstop sized at 3x
the expectation, so a window whose survival runs far above 15% cannot turn a
$0.03 slice into a surprise.

---

## 2026-07-30 — the page is dated now, the font question is answered with numbers, and the press page's links are checked against the code that reads them

Plugin **1.54.0 -> 1.55.0**. Second design pass, taking the four items the
first pass explicitly HELD. Every figure below is measured.

**NOT DEPLOYED.** The brief asked for a deploy and a live check, and also said
do not push. `deploy-plugin.yml` uploads from a checked-out git ref, so shipping
this needs the branch pushed first. The prohibition won. What was verified
instead is in the "Measured" table below, all of it against the real render in a
real browser rather than against the source. The live page was left on 1.54.0
and confirmed unharmed (HTTP 200, TTFB 2.72s, `dashboard.css?ver=1.54.0`).

### Measured, before -> after

| | before | after |
|---|---|---|
| cold render queries | 12 | **12** (constant untouched) |
| warm render queries | 0 | **0** |
| N+1 tripwire (+5,000 rows) | same count | **same count** |
| markup bytes (synthetic corpus, fixture prefixes excluded) | 153,670 | **166,802** |
| body sideways scroll at 390px | none | **none** (`scrollWidth` 390 = `innerWidth` 390) |
| elements overflowing the viewport at 390px | 0 | **0** |
| containers needing a horizontal gesture at 390px | 0 | **0** |
| offline tests | 2,040 | **2,044** |
| PHP harnesses | 5 pass | **6 pass** (`render_press.php` is new) |
| press page cold / warm queries | n/a | **5 / 0** |
| webfont bytes added | 0 | **0** |

`ops_status.py` exits 2 both before and after, and not because of anything here:
five collectors are stale on wall-clock time. It reads neither
`wordpress-plugin/` nor `tests/php/` (grep: zero references), so nothing in this
pass can move it.

### 1. The dated glance panel, and the four buckets that cost nothing

The hero opened with one undated lump — "12,566 updates · 5,542 employers ·
51 countries · $101B raised · 7,573 from official filings" — which answers "how
big is this dataset" in the position where a reader is asking "what has moved".
Every figure in it is as true in March as today, so nothing on the first screen
said whether the thing was still running.

It is now a ladder: **Today / This week / This month / 2026 so far**, each with
updates, employers, dollars raised, updates from official filings, and the
largest single raise named. The old line survives as the bottom rung, labelled
**Everything We Hold**, because it answers a real question and the meta
description is built from the same three figures.

- **Translated, not ported.** The sibling's row reads "1,864 workers · 3
  verified layoffs · largest: Damen Mangalia (1,000)". Layoffs are not collected
  here, so "workers" and "layoffs" have no meaning on this page. The equivalents
  are what this tracker holds.
- **Zero extra queries, and that is a correctness decision first.** The panel
  rides on `tit_glance_matrix()`'s existing single scan. The two describe the
  same windows over the same rows, so computing them separately could have put
  "this week, 1,204 updates" above a matrix cell reading 1,198 — invisible until
  a reader adds them up. Sharing one statement makes disagreement impossible
  rather than unlikely, and it is why the budget is still 12. Verified on screen:
  the panel's "This week 638" and the matrix's "Everything in This View / This
  week 638" are the same number because they are the same expression.
- **Largest raise: two scalar subqueries per bucket, not an argmax.** An
  aggregate returns the largest AMOUNT; it cannot return who raised it, and SQL
  has no portable argmax. The tricks that fake one are engine-specific — SQLite
  defines bare columns beside `MAX()`, MySQL does not; the string-packing form
  needs a different concat operator in each — and the harness is SQLite while
  production is MySQL, so anything that differs between them is a bug that ships
  green. Scalar subqueries are standard in both and stay inside one statement,
  the same shape the top-cities strip already uses. `row_id ASC` breaks ties, or
  two equal rounds resolve to whichever row the engine reached first, which is
  the defect the city flags had.
- **Today is computed and usually absent.** This repo already measured that
  "today" reads zero for most of most days (source dates, not capture dates;
  collection twice daily) and removed the column from the matrix for exactly
  that reason. Reintroducing it as a permanent zero would repeat a mistake that
  is written down. It is computed every render and the row is printed only when
  it holds something.

**The week-over-week comparison is suppressed, and the rule is about history
rather than size.** The sibling can say "down 25% vs the week before" because it
holds years. Here the news collectors first ran 2026-07-27 and `national_press`
on 07-29, so the prior week is not a quiet week, it is a week that mostly
predates the collector; dividing by it prints something like "up 4,000%", which
would be the most quotable number on the page and is an artefact of the corpus
start date. The comparison prints only when the view holds data from on or
before the start of the period being compared against, measured **per view** so
it also holds under a filter that narrows to a young collector. When it is
absent the panel says why in a few words, because a reader who sees nothing
cannot tell "flat" from "we cannot say yet".

`render_dashboard.php` pins **both directions**, which matters: a rule that only
ever suppresses is indistinguishable from a feature that never worked. The
40-day fixture prints the comparison; the same corpus with everything older than
9 days deleted must not, must emit no percentage of any kind, and must state the
reason.

**Copy as Post is honest or it is not shipped.** The sibling's version is scoped
only by its region tab and ignores the rest of its filter bar, so a reader
looking at one country can copy a worldwide total. This one reads the RENDERED
rows out of the DOM at click time and appends the active filters read from the
chips bar the page already maintains, and says "unfiltered" explicitly when
there are none. The panel repaints from `/aggregate` under those filters, so the
two halves cannot drift. The button is rendered `hidden` and revealed by script,
because its whole function is `navigator.clipboard` and a control that visibly
does nothing is worse than an absent one.

### 2. The font decision, settled with a measurement

Held last pass because the mock loads three Google webfonts on a page with a
2.5-4.0s cold TTFB against a deliberate no-CDN rule, and substituting by guess
was refused. Measured 2026-07-30 rather than argued:

| | bytes |
|---|---|
| stylesheet, fonts.googleapis.com | 17,959 |
| Source Serif 4, latin woff2 | 122,168 |
| IBM Plex Mono, latin woff2 (three static faces) | 30,232 |
| Public Sans, latin woff2 | 26,636 |
| **total added to first paint** | **~196,995** |

Against a live origin TTFB measured the same day at **2.72s** and a whole-markup
budget of 156KB. The fonts weigh more than the page they set, and Source Serif
alone is 68% of it for headings only. They also arrive on a **serialised
two-origin path**: the gstatic requests cannot start until the googleapis
stylesheet has been fetched and parsed, so it is DNS + TCP + TLS to one new host
and then to a second before a glyph is asked for, which a byte count does not
show.

And the site runs **Complianz** (`cmplz-manage-consent` is in the live markup,
confirmed by curl). Google Fonts is a named blockable third-party service in a
consent layer, so the design's character would reach some readers and not
others, decided by a cookie banner. Turning that off is a privacy decision that
belongs to the owner.

**Decision: no webfonts. Ship the mock's typographic STRUCTURE at zero bytes** —
a serif for display, a grotesque for body, a mono for labels and figures. That
contrast is what carries the character. What it does not get is Source Serif's
personality at 54px, which is a real loss and is stated rather than papered
over. Worth writing down: **the body face needed no change at all.** The stack
was already `system-ui, -apple-system, "Segoe UI", Roboto`, and Public Sans is a
neo-grotesque close enough to system-ui on both platforms that the two are hard
to tell apart.

**Self-hosting from the plugin is the right long-term answer and is NOT done
here.** It removes the CDN objection, both extra origins and the consent problem
outright, and the one thing that would have made it unsafe is already handled:
this plugin's assets are excluded from Autoptimize's CSS aggregation, so
relative `url()` paths in an `@font-face` resolve rather than break. What it
needs is 179KB of third-party font binaries plus their OFL licence downloaded
and committed into a public repository that deploys to production, which is the
owner's call and not an agent's. **The five latin woff2 files, so it is a
one-line yes:**

```
122,168  fonts.gstatic.com/s/sourceserif4/v14/vEFI2_tTDB4M7-auWDN0ahZJW1gb8te1Xb7G.woff2
 26,636  fonts.gstatic.com/s/publicsans/v21/ijwRs572Xtc6ZYQws9YVwnNGfJ7QwOk1.woff2
 10,052  fonts.gstatic.com/s/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q131nj-o.woff2       (400)
 10,060  fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgsAXHNk.woff2   (500)
 10,120  fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3vAOwlBFgsAXHNk.woff2   (600)
```

Subsetting to the glyphs this product actually uses would cut Source Serif hard,
since 122KB is a variable font carrying the full 8..60 optical-size axis and
200..900 weight range for a page that needs three weights.

### 3. "Why you can trust this", with the FAQ as its second tab

Did not exist anywhere: not in this repo, not in the sibling, not live. Built
from the mock now that the mock is on disk.

**Two fixes to the mock.** Its four numbered items sit in
`repeat(auto-fit, minmax(210px, 1fr))`, which resolves to three columns at most
desktop widths and strands the fourth alone on a second row. Explicit counts
instead — 1 / 2 / 4, all divisors of four — so there is no width at which one
item sits by itself. Verified in a browser: 4-across at 1280px, 2+2 at 900px,
stacked at 390px. And the mock has no FAQ; there was none anywhere in this
product to move, checked before writing, because two FAQs that drift apart is
worse than one.

**Every panel is in the initial HTML and nothing is fetched.** A tab that loads
on click hides its content from a crawler, and an FAQ is among the most
SEO-valuable blocks on a page. Both panels are rendered server-side in full;
JavaScript's entire job is to add `is-tabbed`, and the stylesheet does the hiding
only once that class is present. Verified in a real DOM with the script removed:
both panels `display:block` with 909 and 2,762 characters of text, all eight
questions visible, both panel headings visible, the tab strip `display:none` and
the copy button hidden — so nobody is offered a control that cannot work. With
the script, arrow keys move selection and focus, roving tabindex makes the strip
one stop, and `aria-selected` follows.

**Every number in the copy is computed**, checked by recomputing it from the
database in the harness. corrections.php here once shipped a typed "$124.0bn"
captioned "Measured now" against a live figure of $101B, and the sibling's press
page still carries a hardcoded "51 ... we currently carry every one of them"
with no query behind it. A panel whose subject is trustworthiness is the last
place on the site that can afford either.

FAQPage structured data is emitted, and it is the one line worth arguing about:
3,450 bytes duplicating visible prose. It earns them only because the answers
ARE visible — company.php and places.php both record that the sibling earned a
manual-action risk emitting identical FAQPage markup across ~1,830 URLs where
the answers appeared nowhere in the document. The harness asserts the two
together: every question the schema names must also be rendered as text, so if a
future session moves an answer behind a fetch the schema fails with it.

### 4. The press page, and a test that closes the sibling's silent-link bug

`/talent-intelligence-tracker/press/`. The owner assumed one existed. Sections:
numbers you can use right now (four windows, each with a preset view), context
for this year, the archive, **what this tracker does not do**, how to cite, press
contact. The sibling's page was read for shape only; nothing imported, nothing
copied.

**The archive is a live query and not a snapshot, deliberately.** Corrections
here append a revision rather than overwriting, so the current rows are what we
now believe; a frozen copy would preserve a figure we have since corrected and
present it as though it still stood. What makes an older number answerable is
the corrections log, and the page says so. Months with nothing in them are
skipped rather than rendered as zeroes, because every month before July 2026
would read as "nothing happened" rather than "we were not there".

**The link check is the point of the whole file.** The sibling shipped press-page
evidence links built on `ai_primary=1` — a parameter its REST API accepts and its
dashboard JavaScript ignores — so every "see the rows behind this number" link
advertised a filtered view and served the entire corpus, silently, in a way no
reader could detect. Its own ARCHITECTURE.md now cites it as the canonical
example: a bad parameter NAME over-reports, a bad VALUE under-reports, neither
raises.

A hand-maintained whitelist does not fix that, because the defect IS the
whitelist drifting from the front end. So `render_press.php` **parses the
`inputs` map out of `assets/dashboard.js`** and requires every parameter this
page emits to be in it, plus proves by string match that `applyUrlState()` still
reads `funding` and `stated_headcount` by name. Values are checked too: a
`country` must be an ISO code this product recognises, a `since`/`until` must be
a date the control accepts. Proved to work by temporarily emitting
`ai_primary=1` — the harness failed with the exact sentence describing the
sibling's bug — then reverted.

Also asserted: no superlatives (eight phrases), no em-dashes, Title Case
headings, no withdrawn record reaching any figure, the year label derived from
the clock, and a 5-query cold / 0-query warm budget so a per-row lookup inside
the archive loop fails here rather than under a crawl.

### Where the brief proved wrong about the code

- **"Query budget: `TIT_DASH_QUERY_BUDGET` is 12 cold / 0 warm ... Do not raise
  the constant to pass."** Correct, and the panel cost zero — but the reason it
  could is that the buckets it wanted were **not** the matrix's. The matrix runs
  week / month / quarter / YTD and the panel needed today / week / month / year.
  Three boundaries are shared and two are not, so this is a genuine extension of
  the scan rather than a re-use of existing columns.
- **The brief's model includes a "Today" row unconditionally.** This repo's own
  TECHLOG had already measured Today as structurally near-empty and removed it
  from the matrix. Shipping it as a permanent zero would have re-introduced a
  documented mistake; it self-suppresses instead.
- **"Self-hosting subset woff2 from the plugin is probably the right answer."**
  Right about the destination and wrong about who can take the step: it means
  downloading third-party binaries into a public repo that deploys to
  production. The Autoptimize question the brief asked about turned out already
  answered — our assets are excluded from CSS aggregation, so relative
  `@font-face` URLs would resolve. The blocker the brief did not anticipate is
  **Complianz**, which is installed and would gate a CDN font behind consent.
- **"Check whether FAQ content already exists somewhere before writing new."**
  Checked; none exists. The only FAQ-shaped thing in the codebase is the warning
  in company.php and places.php about the sibling's FAQPage manual-action risk,
  which shaped the design rather than supplying content.
- **A CSS miss worth recording.** `.tit-wrap .tit-press h2` matched nothing: the
  press page's root carries **both** classes, so it needed `.tit-wrap.tit-press`.
  The selector read as correct and the headings quietly kept the body stack. Only
  caught by reading `getComputedStyle().fontFamily` in a browser, which is the
  same lesson as gotcha 11 at a smaller scale.

---

## 2026-07-29 — four coverage levers at $0 and one priced walker, and three of the four briefs were wrong about the code

Five items, briefed as "close the coverage gap as cheaply as possible". Four had
to cost nothing in model spend and the fifth had to be paced rather than funded.
All five landed. **Model spend this session: $0.00.** No model call was made by
any code written here and none was made while measuring it.

Tests **1,996 -> 2,040** (+44, four new files). `ops_status.py` exits 2 before and
after with the *identical* five items — five collectors reading stale against a
checkout six commits behind origin. Verified by running `git show
HEAD:ops_status.py` against the same database: same exit, same list. Nothing
written here adds a problem.

**Three of the five briefs described code that is not there.** Each is recorded
below beside what is, because the wrong belief is the reusable part.

### 1. The archive queue: the sibling's bug is absent, and the mirror of it was not

**Brief:** 3,965 URLs sit `pending` on the sibling and never re-enter its
candidate list; this repo has the mirror problem, and records already pushed to a
terminal state by a blinded 429 probe need resetting. Count them.

**Count: ZERO, and neither premise held.**

| measured, `data/talent_intel.db`, 263 ledger rows | |
|---|---|
| `archive_state = 'unavailable'` | **0** |
| max `archive_attempts` on any row | **1** (of `MAX_ARCHIVE_ATTEMPTS` = 5) |
| archived / pending / no archive row yet | 72 / 69 / 122 |
| coverage | 72 of 12,970 distinct source URLs (0.6%) |

Nothing has ever reached the terminal state, so there was nothing to reset.
`archive_sources.py --recheck-terminal` says so and exits 0; it is kept because
both routes into that state shipped as green runs and a third would need it
again.

And **`pending` already re-entered the candidate list.**
`source_links.archive_candidates` excluded only `archived` and `unavailable`. The
sibling's defect is not in this function and never was.

**What WAS real, and it is the same bug in the second of the two places it can
happen.** The availability-API 429 was fixed on 2026-07-30. Save Page Now's 429
was not: `archive_attempts` was incremented unconditionally after a capture
attempt, so a *refused* capture spent one of the five. Five throttled nights —
which for an anonymous Save Page Now caller is an ordinary fortnight, not an
outlier — would have retired a perfectly capturable document to the terminal
state having never once been told it was uncapturable, out of five green runs.
`archive_candidates` drops it forever and only a hand-written UPDATE brings it
back.

**Second real defect: `pending` was re-examined but could not be REACHED.** The
candidate list was a strict newest-capture-first head slice under `limit`. At
12,970 distinct URLs and a 600-URL window, a URL nobody has ever had an answer
about sinks further every time a collect run stores something newer. That is the
sibling's outcome by a slower route, and it is invisible because the percentage
still climbs.

Both fixed structurally rather than by patching the symptom:

* **Terminal requires EVIDENCE.** `classify_archive_outcome` now takes `probes`
  and will not record `unavailable` until archive.org has answered at least once
  and said it holds nothing (`MIN_PROBES_BEFORE_TERMINAL`). A throttle can no
  longer retire a document, by construction, whatever the next caller does.
* **Blind rounds are counted apart from attempts.** Three new columns
  (`archive_probes`, `archive_blind_rounds`, `archive_detail`), appended to
  `MIGRATIONS`. NULL reads as "never probed", which is the honest reading of every
  row written before they existed.
* **The gap is reported SPLIT.** `source_links.archive_gap()` and
  `ops_status [2c]`: **12,898 never answered about, 0 confirmed absent from
  Wayback.** A percentage climbing slowly because Save Page Now is rate-limited
  (the design) and one climbing slowly because nothing can get an answer (a
  fault) are indistinguishable until those two numbers are printed apart. Today
  every un-archived URL is in the first bucket, which is a statement about what we
  know rather than about Wayback.
* **Two tiers in the candidate order**: never-probed first, then probed-and-absent.
  Every brand-new URL has zero probes, so the ingest-time property the module
  docstring defends is preserved exactly — within tier 1 the order is still
  newest-first. What changes is that the never-answered tail rides *with* the new
  rows instead of behind every one of them.
* **Real pacing.** Consecutive non-answers back the availability gap off
  geometrically to 30s, one answer resets it, and 12 unbroken non-answers end the
  free pass with the remainder unexamined and a `::warning::`. The old behaviour
  walked all 600 candidates at 2/s learning nothing and spent the deadline
  proving archive.org was still refusing.
* `ops_status [2c]` goes RED on any terminal-while-blind row and names the repair
  command. It must always be zero.

Cost: **$0**. No model is called by `archive_sources.py` or `link_check.py`, ever.

One existing assertion changed and it is worth naming.
`test_an_unanswered_url_never_spends_a_capture_or_an_attempt` asserted
`COUNT(*) == 0` on the ledger after a blind round. That proxy stopped being the
property: a blind round is now written down, because "nothing has answered about
this URL for six nights" is otherwise unknowable. The test now asserts the
substance — state `pending`, attempts 0, probes 0, blind_rounds 1 — and says why
the proxy was replaced.

### 2. Ranking the read budget: measured on a real candidate set, and it moves

**The brief's figure was stale.** `READTHROUGH_CAP` is already 200; the
95-deferral measurement was taken at 60, before the owner's 2026-07-30 raise.
The lever is still real, because a full `national_press` sweep produces ~1,018
gate survivors and 200 binds hard on that.

`pipeline/candidate_rank.py`. Ranks `kept` immediately before the classify loop,
which is where `BudgetDeferred` is thrown. Four free signals: country need (from
our own `signals.country` GROUP BY, not from a stale worklist file), employer
novelty, keyword force (reusing `cheap_extract`'s own reading), source tier.

**The property that makes it safe is that it is a permutation.** `rank()` returns
the same objects, asserted by identity rather than equality, so nothing was
rebuilt, normalised or quietly edited on the way through. It cannot reject,
filter or promote; `precheck`, the gate, `validate` and `store` are untouched and
unaware of it. A deferred candidate is still left unmarked and still returns next
run, so the ordering decides *when* a story is read and never *whether*.

**Measured, live, on a real candidate set** — 90 catalogue feeds one per country
in turn, 1,514 items, 162 past the free prefilter, which is exactly the population
a run hands the gate. `python3 -m analysis.ranking.measure --live --feeds 90`:

| cap 60 | US/GB | countries reached | from countries holding ZERO rows | no country hint |
|---|---|---|---|---|
| arrival order | 0 | 20 | **19** | 4 |
| ranked | 0 | **29** | **60** | 0 |

**3.2x the zero-row candidates read, +45% country breadth, at identical spend.**
At cap 200 the 162-candidate sample does not bind and the two orders are
identical — correct, and the honest shape of the result: ordering only matters
when the cap binds.

On the 226 stored news rows (`--stored`), at cap 60: 2 countries -> 23.

Three limits printed with the result rather than left to be discovered:

* **No real candidate set was ever captured, so none can be replayed.**
  `raw_text` is not persisted and a rejected candidate leaves a bare URL in
  `seen_urls` with no text and no reason — the same wall the rejection audit hit,
  and it printed a zero rather than an estimate for the same reason.
* The stored population is rows that *stored*, so the "holds zero" signal is
  circular on it by construction. That column is omitted there, not fudged.
* The live sample was breadth-first, one feed per country, which **flatters**
  arrival order — a real run reads 43 US feeds among 575. The true effect is
  likely larger, not smaller.

Cost: **$0**. One GROUP BY, one DISTINCT scan, and regexes already compiled. A
ranking signal that needed a fetch would cost more than the read it was trying to
prioritise.

### 3. MARKETS: 15 not 14, Korea already in it, and it drives neither of the two things it is believed to

**Brief:** MARKETS has 14 entries; Korea is in the Google News rotation without
being in MARKETS; more editions cost gate time; more candidates into a saturated
read cap produce more deferrals.

Actual: **15 entries, and KR was added on 2026-07-29** with the OpenDART work.
And the caution does not apply, because of what MARKETS actually controls —
traced through the code rather than assumed:

* It does **NOT** drive the Google News locale rotation. `GOOGLE_NEWS_LOCALES` is
  an independent tuple and `build_locales` reads only it. Every country added
  below has been swept twice a day for days while the coverage manifest said
  nothing about it — the same gap Korea had.
* It does **NOT** widen the prefilter's geography gate. The comment above
  `_geography_terms` claimed it "grows automatically as source_registry.MARKETS
  grows"; the function reads `vocab.COUNTRY_NAMES`, `vocab._CITY_ALIASES`,
  `vocab._COUNTRY_ALIASES` and a hardcoded short-code list, and has never
  referenced MARKETS. **Corrected in place**, because that belief is exactly what
  would make someone add a market expecting its stories to start surviving the
  free filter.
* `build_segments()` **does** read it, and `build_queries()` puts the result in
  the query list for every source that is not gdelt, google_news or
  tripwire_chase — which is every structured source, and **every one of them
  accepts `queries` and ignores it** (`national_press` says so in its docstring;
  the SEC pair search by form and item; a derived source has no search vocabulary
  at all). So a segment added here reaches no fetch today.

**Therefore expanding MARKETS costs $0 AND adds zero candidates AND zero gate
time.** It is a correction to a public claim, not a widening of collection. The
brief's caution (a) is true of widening `GOOGLE_NEWS_LOCALES`, which is a
different edit and was not made.

**The binding constraint is the segment sweep budget, and it is 56.**
`test_the_segment_matrix_still_sweeps_inside_the_recency_window` requires
`ceil(segments / 4 / 2) <= 7`. The 15 existing markets spend 44 (name + one per
`terms` entry). Twelve name-only markets spend the remaining twelve exactly.
**That is why none of the twelve carries `terms`** — one three-phrase pack costs
four slots and buys one market instead of four.

Added, **MARKETS 15 -> 27**: BR, ES, IT, MX, AR, CO, PT, CH, SE, AE, ZA, NZ.
Every gold-set zero-country that already has a swept Google News edition and at
least two wired publisher feeds. Both conditions were load-bearing:

* **No edition** -> a `discovery_only` market cannot honestly claim
  `live_sources=("google_news",)`, and adding an edition means adding a
  live-verified LANGUAGE PACK, not a translation. That excludes **CN** (7 feeds,
  no `zh` pack), **NO** (5, no pack) and **FI** (4, no pack).
* **One wired feed** is the single point of failure the catalogue refuses
  elsewhere. That excludes **SA**; its ar:SA edition keeps sweeping, simply
  unclaimed.

`tests/test_market_claims.py` pins all of it, including a test that fails if a
zero-scoring country with an edition and feeds is left unclaimed without being
named in `BUDGET_DEFERRED` with a reason. That dict is empty today: the twelve
spent the budget exactly, and every remaining zero-country is excluded for one of
the two reasons above.

### 4. The historical walker already existed. What did not exist was a price on it

**Brief:** build a cursor-based walker equivalent to the sibling's; read the
sibling read-only for the pattern.

**It has been here since 2026-07-29.** `backfill_gdelt_2026.py` +
`backfill_slices.py`: monotonic cursor committed to `data/backfill_state.json`,
one slice per run, server-side windows (GDELT DOC 2.0 takes explicit
`startdatetime`/`enddatetime`. **This line said "Google News RSS has no archive, which is why GDELT" and it is false — corrected 2026-07-30, see that day's entry; `after:`/`before:` reach back a decade.** GDELT
is the route), seen-URL skipping before any spend, `--fetch-only` for a free
rehearsal, `MAX_SLICES_PER_JOB`, and a `halt` path that records the slice and
declines to requeue into a wall. **The sibling was not read: there was nothing to
pattern-match, the pattern was already here.**

**The sibling's date-ordinal trap is structurally absent.** `record()` moves the
cursor from the ticket the run emitted and reads no clock, so two runs in one hour
advance twice and a run that finished nothing advances not at all — which it
catches, marks `stalled`, and refuses to requeue.

**It has never run.** `data/backfill_state.json` holds one job and it is
`backfill-funding-bulk`.

**What was NOT cheap by construction was the read ceiling — and the number that
actually applied was in the workflow, not the script.** Script default 1200; the
`max_readthroughs` workflow input default **also '1200'**, which is what a
dispatch uses. At the measured $0.00128 a read that is ~$1.54 a slice, and a year
of 2026 history is 92 slices: **the input default alone authorised ~$142 against a
~$5/month product budget.** A ceiling only `spend.py` can stop is not a ceiling,
it is a plan to be interrupted.

Now derived rather than typed:

```
MONTHLY_WALKER_BUDGET_USD = 1.50
USD_PER_READ_ALL_IN       = 0.00128 + 4 x 0.00003   # the read AND the gates that found it
DEFAULT_MAX_READTHROUGHS  = 1.50 / 30 / 0.0014 = 35
```

Deriving it from the read price alone overshot by 9% — small, and exactly the
arithmetic that makes a stated ceiling quietly untrue. The workflow default is now
blank, meaning "use the derived value", so the budget and the ceiling cannot
disagree.

`python3 backfill_gdelt_2026.py --plan-cost` (fetches nothing, calls nothing):

| pace | wall clock | $/month | $ total |
|---|---|---|---|
| 1 slice/day | 92 days | **1.47** | 4.51 |
| 2 slices/day | 46 days | 2.94 | 4.51 |
| 4 slices/day | 23 days | 5.88 | 4.51 |

**A year of 2026 history costs $4.51 at any pace.** The pace only decides how long
it takes and how much lands inside one month — and 4/day exceeds the whole product
budget on its own. **Not armed**: there is no cron, and arming one is the owner's
spend decision. `ops_status [2e]` now says so with the queue command beside it,
because the walker addresses **51 of the 81 recall misses** (`outside_our_history`
— the news collectors first ran 2026-07-27 and `national_press` on 2026-07-29,
against a gold window of 2026-07-01..28; the 9% is a two-day-old tracker measured
against a four-week window).

`tests/test_backfill_pace.py` asserts **the property and not the symptom**: two
`record` calls at the identical clock second advance the cursor twice; the cursor
is monotonic across a 30-slice chain; a budget stop resumes on the first window it
did not do; a stalled job yields no inputs; no sliced backfill workflow may carry
a cron faster than daily (with a cron-expression parser tested against the shapes
that actually appear here, including the sibling's `0 * * * *`); and the walker
carries no cron at all.

**Not measured, and it does not change the projection:** candidate volume per
day-window. Two `--fetch-only` probes were started and neither finished — GDELT
paces at 12s a query and 9 queries a window — and the session ended before they
did. It is not load-bearing: the gate term is a fortieth of the read term, so the
slice cost is a read-count projection with rounding, and the read ceiling is what
binds. Anyone wanting the number can have it for free:
`python3 backfill_gdelt_2026.py --start 2026-03-10 --end 2026-03-10 --fetch-only`.

### What was refused

* **Rebuilding the walker.** It exists; rebuilding it would have been a second
  implementation of a cursor, which is how two sources of truth start.
* **Arming any cron.** None was added. The walker, the tripwire and the plugin
  deploy all stay as they were.
* **`spend.py`.** Untouched. The $10 monthly allowance and the OpenRouter key cap
  are the enforcement; everything above is sizing.
* **Raising `SEGMENTS_PER_RUN`** to fit a thirteenth market. It would have relaxed
  a guard that exists because queries once asked `when:3d` while the matrix took
  6.2 days, and it would have bought a market by weakening the thing that keeps
  markets honest.
* **Mapping the catalogue's `source_type` column into a ranking signal.** The
  recall worklist's under-delivering types are `trade_press` (4% held),
  `press_release` (16%), `national_news` (0%), `filing` (40%); the catalogue's
  column is 66 freeform values from "News Organization" (888 rows) to "Patent
  Office". Mapping one onto the other invents a vocabulary to rank by, and a wrong
  mapping would be invisible — it would simply rank the wrong things first.
* **A registry connector**, `collectors/companies_house.py`,
  `data/sources_catalogue.csv` (read only) and everything under
  `wordpress-plugin/`. Other lanes.

---

## 2026-07-29 — the filter panel is a column of scrolling checkboxes, and the page has one vocabulary

Plugin **1.53.0 -> 1.54.0**. Owner-driven pass on the dashboard. Everything
below is measured; the numbers are from `data/talent_intel.db` at 15,711 current
signals and from `tests/php/render_dashboard.php` before and after.

### Measurements, before -> after

| | before | after |
|---|---|---|
| cold render queries | 12 | **12** (budget unchanged, constant untouched) |
| warm render queries | 0 | **0** |
| markup bytes (synthetic corpus, fixture prefixes excluded) | 151,801 | **153,670** |
| body sideways scroll at 390px | none | **none** (`scrollWidth` 390 = `innerWidth` 390) |
| containers needing a horizontal gesture at 390px | **3** (matrix, country strip, city strip) | **0** |
| offline tests | 1,924 | **2,006** |
| PHP harnesses | 5 pass | 5 pass |

Verified in a real DOM at 390x844 and 1280x860, not by reading the CSS:
`position: sticky` computed on `#tit-panel` and held at `top: 16px` after a
2,000px scroll; `.tit-matrix` computed `display: block` with `min-width: 0px`
and its scroller `overflow-x: visible`; every matrix cell still carrying
`data-filter` and `data-since`.

### The filter panel

The owner's words were "Fix the sapce all this" and "it's still not designed
well", and the diagnosis was that **there was no visual object called "a
group"**. Seven option groups sat in a three-column grid with 8px row gaps and
no boundary of any kind, so each group's options ran into the next group's
heading at the same weight and colour. A gap only reads as separation when it
exceeds the gap *inside* a group, and 8px never did.

- **Each group is now a bounded box**: heading, then its options inside a box
  with its own border, background and capped height. One column, 18px between
  groups, a hairline rule at each boundary.
- **Options are real checkboxes, one per line, and the box scrolls** — the owner
  asked for exactly that ("I like scrolling and check boxes"). This is the third
  shape this control has had: a native `select multiple size="5"` (keyboard-free
  for us, but a five-row window hiding fifteen of Industry's eighteen options,
  needing ctrl-click most readers do not know about), then a pill row (fixed
  discoverability, lost the list, and seven wrapping pill rows *were* the wall
  the owner complained about), now checkboxes. Measured: Industry renders 18 rows
  in a 162px box over 612px of scroll height.
- **The panel is a column beside the rows and sticks** at 1000px and up
  ("filters dont move with the page a like the layoff one"). It had to become a
  column first: a full-width block is taller than the viewport, so there is
  nothing to pin. Below 1000px it wraps to a normal stacked block, and
  `prefers-reduced-motion` forces `position: static`.
- Reset moved to the top of the panel, same `id`, so the same handler binds it.

**The state architecture did not move.** Each `<select multiple>` is still the
state and still what the querystring, chips bar, exports, quick views,
click-to-filter and share links read. `pillify()` in dashboard.js re-renders the
checkboxes *from* the select after every change. It also still hides the select
with a class it applies **at runtime**, which is what leaves a JavaScript-off
visitor a working native control; that is why the hiding must never move to the
server.

Two numbers that had to be kept in step by hand are gone: the list box was
pinned to 96px and the pill row to 96px because the swap happened after paint
and any difference was a layout shift. The select is `display:none` the moment
the script runs, so there is no swap and no pair.

### The three defects the owner named

1. **"remove exact locaiton only doens't make nses?"** — read `api.php` first.
   `country_basis=location` is real: it changes the country clause from
   `(country IN (..) OR (country IS NULL AND hq_country IN (..)))` to
   `country IN (..)`, dropping rows placed only by a substituted head office.
   So it was kept and renamed **"Only Countries A Source Named"**, which is the
   sentence the (i) panel was already using while the control called itself
   something else. **Stated limit rather than papered over:** it narrows the
   country clause only. The city clause in `tit_build_where()` is
   unconditionally the union form, so a city pick still admits a head-office
   match. Closing that is an `api.php` change and was out of this pass's lane,
   so the label says country and does not claim the city.

2. **"Only Updates That Move Headcount (54)" — "What does this mean?"** It
   filters `signal_direction IN ('hiring','displacement')` and reads nothing
   from the `headcount` column. Measured: `headcount` non-null on **11 of
   15,711** rows (0.07%); the direction test true on **53** (0.34%) — 51 hiring,
   2 displacement. So the label promised a column it does not touch, and the set
   is a third of one percent. **Decision: kept, relocated, relabelled** as the
   quick view **"Moves Headcount"** with its computed count printed on it.
   Removing it would have broken `/query` links already in the wild; leaving it
   in the panel gave a 0.34% control the same weight as Industry. A quick view is
   explicitly a narrow named cut, and the count means a reader sees the size
   *before* clicking. The checkbox survives in `.tit-state` as the state the
   button drives.

3. **The UK concentration note and the hidden-rows disclosure.** Both facts kept
   in full, both re-ordered so the reader meets the point before the arithmetic.
   The caveat now opens "Read United Kingdom as filing volume rather than as how
   much is happening there:" and the evidence follows. The detail note opens
   "You are seeing 12,568 of 15,711 updates. 3,143 routine filings are hidden."
   and defines "routine" in a trailing clause. The control itself was three
   stacked labels ("Officer and director filings" / "Hide the routine ones" /
   prose) and is now one setting and its value: **Routine Filings: Hidden /
   Shown**. Every figure still computed and still moves with the filters.

### Where I was told something that turned out to be wrong

- **"Funding Stage stops at Series B", "Work Setup has no Hybrid", "Site Change
  has no closure" — all three are neither render bugs nor vocabulary gaps.**
  `pipeline/vocab.py` already holds `series_c`, `series_d_plus`, `hybrid`,
  `closed` and `relocated`. `/facets` is deliberately **data-driven**: it lists
  only values actually present, because a control returning nothing reads as
  broken rather than as thin coverage. The real finding is coverage, and it is
  worse than the labels suggested. Across 15,711 current rows: `work_mode` is
  set on **4** (onsite 3, remote 1), `site_event` on **19**, `deal_type` on
  **23**, `funding_stage` on **33**. Five facet controls between them describe
  about **80 rows**. They hide themselves when a column is *empty*; they do not
  hide themselves when it is nearly empty, which is the same defect class as the
  headcount control. **Owner decision, not taken here:** raise a minimum-rows
  threshold before a facet control appears at all.
- **"Remove Where The Money Went entirely."** There is exactly one money surface,
  it is the one the owner pasted, and the owner separately said they loved the
  card format. Confirmed against the live page by curl (1.53.0): "Where The Money
  Went" appears once, and the three-card panel the endorsement described does not
  exist in this codebase at all. **So only the section HEADING went**, which is
  what the owner actually pasted and which repeated "Click a row to narrow the
  page" eight lines under "Click any row to narrow the whole page to it". The
  cards stay; the city card takes the wording "Where the Money Went".
- **"Manufacturing / Education / IPO appear in two groups each" — all three
  true.** Fixed as wording, never as vocabulary: `Production & Manufacturing`
  for the function (Industry keeps `Manufacturing`), `Educational Institution`
  for the employer type (Industry keeps `Education`), `Initial Public Offering`
  for the deal type (Funding Stage keeps `IPO`). Stored values untouched.
- **A "Why you can trust this" panel with numbered SOURCED / UNCONVERTED /
  UNGUESSED / CORRECTABLE items does not exist** in this repo, in the sibling, or
  on the live page. Not built: authoring it from a description of a screenshot
  would have meant inventing both a design and its copy.

### Title Case and one vocabulary

The owner asked for Title Case three times, so it is now **a test** rather than
a habit: `render_dashboard.php` reads the matrix row labels and the card
headings out of the rendered markup and asserts conventional Title Case (short
conjunctions and prepositions lowercase inside a label, first word always
capitalised, all-caps acronyms allowed). It regressed twice because a convention
nobody can check makes a wrong label look exactly as correct as a right one.

The deeper problem was **two vocabularies for one set of facts**. The charts said
`Pay and benefits` and `Growing and expanding`; the matrix beside them said
`Pay news` and `Funding raised` for the same rows. One list now, and the retired
phrases are asserted absent so a second vocabulary cannot creep back:

| was | is | why |
|---|---|---|
| Hiring up | **Adding Roles** | "up" was doing the work of "the source says headcount is rising" |
| Cutting back | **Cutting Roles** | "back" could have meant costs, hours or investment |
| Pay news | **Pay and Benefits** | the charts' phrase, which was already the better one |
| Funding raised | **Funding Rounds** | it counts updates |
| Money raised | **Total Raised** | it sums dollars, and "Total" says so |
| All updates | **Everything in This View** | a reader could not tell whether the 3,143 hidden filings were in it. They are not |

**Checked before renaming, because the sibling was bitten here:** on the layoff
tracker this same edit was a two-file data join, because an aggregate keyed its
rows *by label* and a cached response spanning the deploy window would have
silently killed click-to-filter. **That coupling does not exist here** — every
chart row carries its key on `data-k`, every matrix row on `data-signal`, the
filter a click applies is a separate `filter` field, and `tit_glance_matrix()`
keys its cells `c_{di}_{pi}` by index. Nothing reads a label. The test now pins
that it stays that way.

Renaming `Money raised` to `Total Raised` also **shortened a paragraph instead of
hiding it**: the block needed a sentence beginning "Money raised is the
exception" only because one row was lying about its unit.

### Mobile

Three separate containers required a horizontal gesture at 390px. All three are
gone.

- **The matrix stacks.** Five columns cannot fit 390px, so it had
  `min-width:560px` inside `overflow-x:auto` — which does stop the *body*
  scrolling, and was still wrong: the header rendered "THIS WEEK | THIS M..."
  under a scrollbar, on the first thing on the page, whose own copy says "Tap any
  number to filter the page". Below 860px each row is a card. **The period label
  is real markup** (`.tit-cell-p`, rendered by both `shortcodes.php` and
  `matrixHtml()`), not a CSS `::after` on a data attribute: `display:block` drops
  the implicit table roles and generated content is not reliably in the
  accessibility tree, is not selectable and is not findable. Nothing is keyed to
  `nth-child`. Every cell keeps its `data-filter` and `data-since`.
- **The geo strips wrap.** A previous pass had deliberately set
  `flex-wrap:nowrap; overflow-x:auto` on them below 560px, reasoning that a
  container scrolling beats the body scrolling. Both halves true, conclusion
  wrong: it put two stacked horizontal scrollbars on the first phone screen with
  "Glasgo" cut mid-word. The sibling reached the same verdict about its own pill
  strips — hiding options behind a swipe is the failure pills exist to fix.
- **The three explanations under the matrix are one disclosure**, collapsed on a
  phone, open on desktop, **not one word cut**. `open` is in the markup, so a
  crawler, a desktop reader, and a reader with no CSS or no JavaScript all get
  every word in the initial HTML with nothing fetched; a four-line function is
  the only thing that closes it, once, on a narrow viewport. It has to be script
  because `open` is an attribute and CSS cannot remove one. Re-collapsed after a
  repaint, or every filter change would undo it. The two paragraphs also became
  six single-idea lines ("this make s not sentds").
- **Dark scheme.** The stylesheet's existing note explains why there is no
  `prefers-color-scheme` block (the theme paints white regardless, so honouring
  the preference produced light text on white) and that reasoning stands. What was
  missing is that we never *told* the browser: without `color-scheme`, a UA in
  dark mode repaints controls, scrollbars and any background we did not set, which
  is exactly the mixed result in the owner's screenshot. `color-scheme: only
  light` plus explicit backgrounds and ink on our own headings. **Supported
  schemes are now stated: light.**

### Page order

Geo strips moved above the matrix ("Should we move this ... Aboe"): picking a
place is how most readers start. That invalidated a **pointer** — the quick-views
hint said "click a number in the matrix at the top" and the matrix is no longer
at the top. Grepped for others; that was the only one. A stale direction is worse
than none, because a reader follows it.

The chart cards also gained one bar pattern instead of two. "What Is Moving"
stacked its label above a full-width bar while the two cards beside it were
inline; the fix is `display:contents` on `.tit-pillar-head` so the button's own
grid takes over, **in CSS and not in markup**, because `.tit-pillar` is the
click-to-filter handler's selector and restructuring it would have risked a
working control to fix a visual inconsistency. Cards size to content
(`align-items:start`) rather than stretching a four-category card to match a
51-country one, the scroll edge fades rather than bisecting a row, and the
"Click a row to filter" that all six subtitles ended with is gone — the panel
header says it once.

### The harness now announces itself

The owner twice read a screenshot of `tests/php/render_dashboard.php`'s output as
the live site and concluded the data had broken. It renders the **real** dashboard
against a synthetic corpus, so it is byte-for-byte the shape of production with
different numbers, and the only tell was that its UK count outranks its US count.
Every fixture employer is now prefixed `TEST FIXTURE` and the placeholder headline
says so. The byte budget subtracts the prefix (~2.1KB of test-only content) before
measuring, or a legitimate change would eventually fail the budget for a reason
nobody could find.

### Held for a second pass, deliberately

- **The full Claude Design re-skin.** The mock is a 965-line React preview styled
  entirely with inline `style` attributes and **zero `@media` rules**, so it is a
  desktop specification only. Porting it means extracting every inline style into
  classes and authoring all responsive behaviour, and its character depends on
  three Google webfonts (`Source Serif 4`, `Public Sans`, `IBM Plex Mono`) on a
  page whose cold TTFB is already 2.5-4.0s and whose assets are deliberately
  CDN-free. **No font was substituted and none was added**; this pass changed
  layout and wording inside the existing token set. The mock's own decisions that
  cost nothing were adopted: the sidebar filter column, the checkbox rows, the
  place-basis wording, the headcount cut as a quick view, and the city money card
  as "Where the Money Went".
- **The dated four-bucket glance panel** replacing the hero figure line, with the
  week-over-week comparison suppressed until real history exists. Not started.
  The suppression rule is the load-bearing part: news collectors first ran on
  27 July, `national_press` on 29 July, so "this week vs last week" would compare
  a populated week against an empty one and print something like "up 4,000%".
- **The FAQ tab and the trust panel** (does not exist to move; see above).
- **The sibling port.** Not touched, and not only for budget: `CLAUDE.md` names
  the sibling "do not touch", it is outside the lane I was given, and that repo
  auto-deploys on push. It needs its own session in its own repo.
- **A minimum-rows threshold for facet controls** (the ~80-row finding above).
  That is an owner decision about what a nearly-empty control should do.

---

## 2026-07-30 — the UK register is not the source; the 250-employee roster is

Build the Companies House connector, now that the key exists. It ships. The
interesting half of the work is not the connector, it is the **refusal to point
it at the register**, and every figure below is measured rather than argued.

No authenticated call has been made from this repository — the key exists only
as a GitHub secret — so everything here was measured against the PUBLIC register
web pages (which need no key), the free bulk Company Data Product, the GOV.UK
gender pay gap download, and the published API specification. What that leaves
unproven is listed at the end, and it is a short list.

### The register is 190x too big, and the excess is dormant micro-companies

Part 1 of 7 of the free Company Data Product for 2026-07-01 holds **849,999
live companies**; the seven parts are not equal sizes (part 7 is 52Mb against
69-70Mb), so the register is **~5.7 to 5.9 million** rather than a round figure.
A random sample of 120 of those companies, read one officers page each:

| | random live register | GPG 250+ roster |
|---|---|---|
| companies | ~5.7M | **9,230** |
| appointments per company per year | 0.246 | **0.867** |
| active officers, median | 1 | 4 |
| officers ever recorded, median / mean | 2 / 4.0 | 26 / 44.4 |
| projected appointments a year | ~1.4M | **~7,354** |
| projected stored rows a week | ~27,000 | **~110** |

**~27,000 appointments a week against a database of 15,711 signals.** Four days
of unfiltered collection and the tracker is a list of UK director changes with
some other content attached. It is also mechanically impossible: 5.7M requests a
week is 33 days of continuous polling at 600 requests per 5 minutes.

And the excess is not merely large, it is empty. The random sample's median
company has **two officers in its entire history**; the names it returned are
`AD ASTRA BARS LTD`, `B-LEAF HEALTHCARE LTD`, `AVENIR WORKS 6 LTD`, `5374 LTD`.

### The filter is a statutory employee count, and the obvious alternative fails

The chosen population is the **GOV.UK gender pay gap roster**: every employer
with 250 or more employees in Great Britain must report, the CSV carries a
`CompanyNumber` column, and this repo already reads that file.

    2025 reporting year          11,154 employers
      well-formed CH number       9,634  (86.4%)
      in a 250+ size band         9,230  <- the population

Coverage of the biggest employers is *worse* than average and it is worth
knowing why: 301 of 546 in the 5,000-19,999 band and 51 of 67 in the 20,000+
band carry a company number, because the largest UK employers include NHS
trusts, councils and government departments that are not companies at all.

**The accounts-category filter the brief suggested was built as a measurement
and refused.** `FULL` / `GROUP` / `MEDIUM` is 2.05% of the register (~120,000
companies), and joining the roster to the same snapshot gives its precision
directly: **1,104 of 17,378** such companies in that slice are 250+ employee
employers — **6.35%**. So it is 13x the roster to poll, 94% of it is not what we
are looking for, and it still misses 14% of the roster (180 of 1,284 roster
companies in the slice file as audit-exempt subsidiaries, small, or nothing).
The reason is structural and worth keeping: **accounts category records how a
company chose to file, not how many people it employs.** A two-employee
property vehicle with a large balance sheet files FULL; a 400-person business
can file as a subsidiary. SIC code was refused for the same class of reason — it
is a topic filter, and it cannot tell a 3,000-person software company from a
dormant one. Nothing the register exposes as a search helps either:
`advanced-search/companies` filters name, status, type, incorporation date,
location and SIC, and nothing about size.

Full accounts-category distribution on that 849,999-row slice, since it took a
73MB download to get and should not need a second one: MICRO ENTITY 32.63%, NO
ACCOUNTS FILED 25.38%, TOTAL EXEMPTION FULL 22.61%, DORMANT 12.54%, UNAUDITED
ABRIDGED 2.82%, FULL 1.48%, SMALL 1.15%, AUDIT EXEMPTION SUBSIDIARY 0.57%,
GROUP 0.46%, TOTAL EXEMPTION SMALL 0.16%, MEDIUM 0.11%.

### Where the brief was wrong, and it was the load-bearing part

**"There is a streaming API ... that is almost certainly the right primitive
rather than polling companies one by one."** It is not, on two independent
grounds, both checked rather than assumed.

1. A REST key cannot open it. The streaming authentication guide says
   "Applications that are to use the streaming API must be registered as such,
   the REST API and streaming API keys are not interchangable."
   `COMPANIES_HOUSE_API_KEY_UK` is documented as a REST key with the REST rate
   limit, so it will 401 on the stream.
2. Even with the right key it is the wrong shape for this repo. The stream is a
   long-lived connection resumed by a stored `timepoint` (too old a timepoint
   returns 416), capped at two concurrent connections per account. Every
   database writer here shares one `talent-collect` lock and runs as a bounded
   Actions job that commits and exits. A process that must stay connected to
   keep its place is the opposite of that, and its missed windows would be
   unrecoverable rather than back-fillable.

Polling turns out to be the property that makes this safe rather than a
compromise: `appointed_on` is a field on every officer record, so a window is a
filter over data the endpoint always returns, and **this collector stores no
state whatsoever.** A missed run loses nothing and a wider window is one
integer.

Two smaller corrections. The brief said the free bulk product has no officers
data — true, and it is still the right thing to download, because it is the only
free way to count the denominator and test the accounts-category filter. And
`find-and-update.company-information.service.gov.uk` was **already** in
`vocab.PRIMARY_SOURCE_DOMAINS` before this session, so rows reach `verified`
with no vocabulary change.

### The rotation, because a whole sweep holds the lock too long

10,568 requests sweep the roster (1.145 requests per company at 100 officers a
page — officers-ever runs median 26, mean 44.4, p90 66, max 1,992, so 98% of
companies need exactly one page). At `REQUEST_DELAY = 0.55s` that is **97
minutes**, and `writer_queue.LONG_HOLD_MINUTES` is 120.

So the roster is sliced four ways by a **blake2b digest of the company number**
— not `hash()`, which is salted per process and would reshuffle the rotation
every run, leaving some companies unvisited for months while the run count
looked perfect — and the ISO week number picks the slice. Nothing is committed
and there is no cursor to corrupt. Measured slice sizes: **2,344 / 2,295 /
2,321 / 2,270**, so **~2,600 requests and ~25 minutes** a run.

The window is **derived** from the rotation the way `recency_window_days`
derives Google News's: `SLICES * 7 + 14` = 42 days. Each visit therefore covers
28 new days and 14 already seen. The overlap is the point: it costs nothing
(exact `content_hash` duplicates, skipped before any write) and it makes a
single missed run recoverable on the slice's next visit instead of a permanent
hole.

### Four judgements that are not obvious from the code

**A body corporate is not an employee** — the `bse_india` auditor rule again,
and the register proves it is needed: `LEGAL & GENERAL CO SEC LIMITED` is the
sitting secretary of Legal & General Resources Limited. So the role allowlist is
`director`, `secretary`, `llp-member`, `llp-designated-member` and nothing else;
every `corporate-*` and `nominee-*` role is named in `EXCLUDED_ROLES` rather
than merely absent. Measured cost: 2 of 231 appointments (0.9%) were a body
corporate, 63 of 3,151 officers (2.0%) were nominees.

**The allowlist reads `officer_role` verbatim, with no case folding, and that is
a deliberate strictness.** The public web page renders a `corporate-secretary`
as plain "Secretary" — which is also why the 150-company measurement that sized
this source could not see corporate officers at all, and why its yield figure is
about 1% high. Folding case would let the string the web page prints through the
one check that exists to catch it. The first version did `.lower()`; a test
caught it.

**Every row is `neutral`, never `hiring`.** The register records the legal fact
of an appointment and says nothing about where the person came from: a group
finance manager added to a subsidiary board is filed identically to an external
chief executive hire. Precision over recall, the same rule `bse_india` applies
to a re-appointment.

**Resignations are refused in v1**, with the number: `resigned_on` is on the
same records and would add **80% more rows** (184 resignations against 231
appointments in the sampled two years) that say the least of anything this
source could produce, because the register never says why somebody left.

### Identity, geography and the concentration this was meant to fix

The employer name is the pay-gap file's `CurrentName` falling back to
`EmployerName` — the **same expression `uk_paygap.parse_csv` uses**, on purpose,
so `vocab.company_key` lands on the same employer and a company profile shows
one employer's pay and its board rather than two near-identical employers.
Verified against the live database: `LEGAL & GENERAL RESOURCES LIMITED` keys to
`legal & general resources`, which already has `uk_paygap` rows; 493 of the
9,228 distinct roster keys do (the rest because `uk_paygap` defaults to a 5,000
employee floor).

Geography follows `uk_paygap` exactly, by importing its map rather than copying
it: the registered office postcode area fills `hq_city` and only for
unambiguous areas, `city` is never set at all, `industry` and `employer_type`
come from the filed SIC division. **Nothing here splits an address on a comma**,
and a test asserts the source text does not either — `ats_boards` turning
"Cambridge, MA" into Morocco is the reason.

GB rows today are **4,801, of which `uk_paygap` is 4,761 (99.2%)**. At ~110
rows a week the concentration falls below 90% inside five weeks.

`source_url` is `/officers/{officer_id}/appointments` — the register's own page
for that person, which names the company, the role and the appointment date, and
which is keyed on a permanent officer id **read out of the API's
`links.officer.appointments`, never composed** (BSE's AttachLive → AttachHis rot
is what an invented identifier does). It is not the company officers page, which
would be one URL for every appointment the company ever makes. Because one
person can be appointed twice, `REVISITS_ITS_SOURCE_URL = True`, so dedup runs
on `content_hash` and the fuzzy window rather than on URL-seen — the
`ats_boards` lesson.

### GB is promoted, and it should have been promoted before this

`GB` moves `discovery_only` → `structured_official`. Two things about that.
`uk_paygap` has been a working GB structured connector with a health check and a
passing test since 2026-07-28 and was **never listed in the market's
`live_sources`**, so the tier understated the country while the country chart
was dominated by that very source. And "Companies House appointments" sat in
`candidate_official_sources` — the roadmap — while being the thing this entry
builds; it is removed from there.

### Numbers

- 9,230 companies in the population, from 11,154 pay-gap employers.
- 4 slices, 2,344 / 2,295 / 2,321 / 2,270, ~2,600 requests and ~25 min each.
- 42-day window, derived. ~200 candidates a run, ~133 of them new.
- ~110 stored rows a week, ~5,600 a year, all at 250+ employee employers.
- **$0.** `as_classified` closes the record; no model is called on this path.
- 72 tests, offline, against a fixture of real register values.
- Suite 1,823 → 1,996 (the two concurrent Japan and Korea connectors are in
  that number too). `ops_status.py` exit 0, `structured_official` now `[GB, IN]`.

### Access and licence, checked first

- `api.company-information.service.gov.uk/robots.txt` → **401**
  (`{"error":"Empty Authorization header"}`). Every path on the API host needs
  auth, so there is no directive to honour and the default applies.
- `stream.company-information.service.gov.uk/robots.txt` → **401**, same.
- `find-and-update.company-information.service.gov.uk/robots.txt` → **404**
  with an HTML page. No directives. This is the host the measurements read.
- `download.companieshouse.gov.uk/robots.txt` → **200**, `User-agent: *` /
  `Disallow:` — explicitly everything.
- Public sector information; the OGL attribution rides in the summary of every
  stored row, exactly as `uk_paygap` carries its own.

### Unproven until the first real run, and it is a short list

Everything authenticated. Specifically: that `items_per_page=100` is accepted,
that `total_results` counts what the docs say, that HTTP Basic with an empty
password is the accepted credential form, and the exact `officer_role` strings
on live rows. All four are pinned by tests against the documented shape and all
four fail loudly rather than quietly. First run:

```bash
gh workflow run drain-writers.yml -f enqueue=collect-structured.yml \
     -f inputs_json='{"source":"companies_house","dry_run":"true"}' \
     -f reason='first authenticated Companies House run'
```

What was verified without the key: the roster (`ch.roster()` returns 9,230 from
the live pay-gap download), the rotation, the window arithmetic, the whole
`collect → as_classified → build_signal` path against a stubbed session — one
row out the far end, `verified`, `GB`, `hq_city=London`,
`industry=professional_services`, `published_date=2026-07-01`, direction
`neutral` — and that a keyless run fails with the message that names the
streaming-key trap rather than storing zero quietly. The emptiness floor fired
on that stub run before it was lifted for the demonstration, which is the guard
working.

---

## 2026-07-30 — Korea's spine is the report TITLE, because its typed codes stop one level too coarse

Build the Korean equivalent of the India connector. It ships, it costs nothing,
and Korea stays `discovery_only` because the SOURCE is measured and the
CONNECTOR has never made an authenticated call. Every number below came from a
command in this repo; no OpenDART credential was used at any point.

### The endpoint list was walked before a line was written

All six published API groups, 84 endpoints, read from
`https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001..DS006` on 2026-07-29.
There is no Item 5.02 equivalent to ask for, and the reason is precise:
**`pblntf_detail_ty` has about 60 values and every Korea Exchange timely
disclosure shares ONE of them, `I001`** — supply contracts, dividends, buybacks,
CEO changes and litigation all arrive under the same code. That is the Form 6-K
problem again: a filing type with no item taxonomy inside it.

**Two things rescue it, and one of them is a measurement rather than a document.**

`E005` (독립사외이사에관한신고) is a detail code of its own, and every row it
returns carries one report name. Inside `I001`, the exchange's own report TITLE
turns out to be a fixed vocabulary: **8,211 `I001` filings over 2026-05-01 to
2026-07-29 collapse into 360 DISTINCT titles**, and the leadership ones recur
character-for-character. Those are KRX's form titles, generated by the filing
system, not sentences a company composed — the same class of value as BSE's
`SUBCATNAME`, and the only thing that makes `I001` usable at all.

Measured unauthenticated through DART's own public search
(`dart.fss.or.kr/dsab007/detailSearch.ax`, which robots.txt permits — it
disallows six paths and that is not one of them), and then re-counted by running
the shipped collector's own `is_wanted` / `strip_amendment` / `REFUSED_REPORT_NAMES`
over the captured rows:

| window | rows read | stored-eligible | refused by name | amendments skipped | not leadership |
|---|---|---|---|---|---|
| 2026-05-01..07-29 (90d) | 8,363 | **261 (3.1%)** | 4 | 4 | 8,094 |
| 2026-07-01..07-29 (29d) | 2,561 | **88 (3.4%)** | 4 | 0 | 2,469 |

The 261, by title:

| report title | FSS's own English | 90d |
|---|---|---|
| 독립이사의선임ㆍ해임또는중도퇴임에관한신고 | Report on the Appointment, Dismissal or Early Retirement of Independent Directors | 150 |
| 대표이사변경 | Change of CEO | 79 |
| 대표이사(대표집행임원)변경(안내공시) | Notice on Change of CEO | 28 |
| 대표집행임원변경 | Change of Representative Executive Director | 4 |

Per ISO week over twelve full weeks: **12 to 49, median 19**, across KOSPI (29),
KOSDAQ (74) and KONEX (6) on the CEO items alone. `MIN_ROWS_PER_WINDOW = 5` sits
below the observed floor, so a run that returns fewer has broken rather than gone
quiet. March is Korea's shareholder-meeting season and runs higher, so a summer
measurement is the conservative one.

**~1,060 a year, which is about 8% of India's ~13,000, and the gap is scope not
diligence.** SEBI Regulation 30 covers every director and every key managerial
person. Korea's mandated item covers the representative director, and separately
independent directors. Ordinary inside directors are elected at a shareholder
meeting whose result is untyped prose, so they cannot be reached from here.

### What was refused, with the numbers

**The periodic-report endpoints are snapshots, exactly as the brief feared, and
diffing them was declined.** `exctvSttus.json` (임원현황) returns every sitting
officer as of `stlm_dt` — name, position, `hffc_pd` tenure as free text
("3년 6개월"), term expiry. `empSttus.json` (직원현황) returns headcount by
division and gender. Neither states that anybody was appointed and neither
carries an appointment DATE, so an event out of them means diffing year N against
N-1 and stamping the difference with a date the source never stated. Both are
also **per-`corp_code` only**: there is no date-ranged form, so even a snapshot
sweep is one request per filer per report code.

**The 주요사항보고서 family has no officer item at all.** All 36 endpoints in
group DS005 were read: insolvency, capital raises, buybacks, mergers, divisions,
asset transfers, business suspension. Not one is an appointment or a departure.
**The brief that commissioned this named that family as a candidate; it is a dead
end**, and that is now written into `source_registry.py` so nobody researches it
twice.

**`독립(사외)이사 및 그 변동현황` is the one endpoint with change FIELDS and they
are still not events.** `apnt` (선임), `rlsofc` (해임) and `mdstrm_resig`
(중도퇴임) are period COUNTS with no person and no date. An aggregate is not a
record about anybody.

**`elestock.json` (임원ㆍ주요주주 소유보고) was the near miss.** It is
event-driven, it carries a real `rcept_dt`, and it names the officer and their
position. But the API exposes **no 보고사유 field**, so an appointment cannot be
told from a share purchase, and reading one as the other would invent the event
type rather than the number.

**`대표이사변경 (자회사의 주요경영사항)` — the chaebol trap, 2 of 261.** A listed
PARENT reports a change at a subsidiary it does not name in the title, and
`corp_name` is the parent. Miwon Holdings and MAEIL HOLDINGS each filed one.
Refused by name. `기업인수목적회사의임원선임결정` (2 more) is a SPAC appointing
its own formation officers, which is a company being incorporated rather than
anybody being hired.

**Amendments are not second events.** `[기재정정]`-prefixed rows are skipped:
this tracker corrects a record with `store.revise()`, never with a second row.
That costs the amendments whose original fell outside the window — 4 of 265 over
90 days — and the price is written down rather than hidden.

### Four traps, each found by fetching something

**1. The English viewer answers 200 with the single word "Reject".**
`englishdart.fss.or.kr/dsbh001/main.do?rcpNo=` looked like the ideal
`source_url`: the same document with FSS's own English labels, on a host that
serves no robots.txt. Sampled on 20 real filings from the allowlist, **16
rendered and 4 returned a page whose entire body is the word "Reject"** — among
them Kia and Korea Gas Corporation, so it is not an obscurity effect. A citation
that answers 200 with one word is worse than a 404, because a link checker calls
it live. `source_url` is therefore `dart.fss.or.kr/dsaf001/main.do?rcpNo=`, the
form OpenDART's own field documentation gives for every `rcept_no`.

**Said plainly, because it is a real cost: `dart.fss.or.kr/robots.txt` disallows
`/dsaf001/main.do`.** Nothing here fetches it — the collector talks only to
`opendart.fss.or.kr/api/`, which serves no robots.txt at all — so `link_check.py`
will record these URLs as `robots` rather than checking them. That is the correct
outcome rather than a defect to route around, and Wayback already holds
`dsaf001/main.do` snapshots going back to 2009, so archiving is not blocked.

**2. A missing key is a 302; a bad key is a 200.** Verified live and keyless on
2026-07-29: `list.json` with no `crtfc_key` returns **HTTP 302** and an HTML
error page, while a syntactically valid but unregistered key returns
**HTTP 200 `{"status":"010"}`**. So neither the status code nor "the body parsed
as JSON" means success — `status` is the only authority. This matters more than
usual here because CLAUDE.md already records that mapping a MISSING GitHub secret
sets the variable to empty string, which is how a leadership dispatch once went
green having stored nothing. `api_key()` refuses an empty or non-40-character key
before a request is spent, and names the 302 in the message.

**3. Full-width digits break the verbatim-figure guard.** `validate._numbers_in`
tokenises with `\d`, which matches U+FF10..FF19, and `_normalize_number` does not
fold them, so `１２３` in a summary and `123` in `raw_text` compare unequal and a
correct record is discarded silently. `_squeeze` folds them to ASCII on the way
in. **NFKC would be the obvious fix and is WRONG here**: it rewrites U+318D — the
ㆍ inside 독립이사의선임ㆍ해임또는중도퇴임에관한신고 — to U+119E, so the
allowlist would stop matching the report name the API sends. Both halves are
pinned by tests. Belt and braces on top: every figure in the summary is a
substring of the same string quoted into `raw_text`, following `bse_india`.

**4. A Korean company_key produces a URL that 404s.** `vocab.company_key` passes
Hangul straight through (verified: `company_key("한울앤제주") == "한울앤제주"`),
and `tit_company_slug()` is `[^a-z0-9]+ -> '-'` with a `rawurlencode` fallback
when the result is empty — and HANDOVER.md records that percent-encoded slugs 404
on this host. So the stored employer is **`corp_name_eng` from `company.json`,
the company's OWN registered English name**, fetched once per company per run and
cached in memory. A filer whose `corp_name_eng` is blank is DECLINED and counted:
this file invents no transliteration. The Korean `corp_name` is still quoted into
`raw_text`, because it is the filer's own name and the record should carry it.

### Two things deliberately not stored

`stock_code` is not written to `ticker`. That column is SEC-authoritative
everywhere else in this tracker (`pipeline/identity.py` resolves it from
`company_tickers.json`), and a 6-digit KRX code beside `AAPL` is two vocabularies
in one filter. `adres` from `company.json` is not read either: a registered legal
seat is not where an appointment happened, and `identity.py` is already the single
authority for `hq_city`. No city is guessed, so Korean rows place at country level
only, as Indian ones do.

### Direction is never inferred, and that is the honest weakness

`대표이사변경` says a change happened. `독립이사의선임ㆍ해임또는중도퇴임에관한신고`
names all three possibilities in one title. Neither separates a joiner from a
leaver, so **every row here is `neutral`**, as India's departures already are.
`displacement` is never used: one officer leaving is a change of leadership, not a
workforce reduction, and workforce reductions are the sibling's scope. Recovering
the direction means downloading and parsing the filing body, which is document
parsing at best and an LLM call at worst, and zero cost is the premise.

One consequence worth stating: **`prefilter.filing_reduction_plan` returns None
on Korean text** (checked: `구조조정 인원 감축` -> None). The scope guard is
English-only, so on this source the report-title allowlist IS the scope guard —
and a CEO change cannot be a workforce reduction, which is why that is sufficient
here and would not be for a prose source.

### The sibling's OpenDART retirement does not transfer

`/Users/dakotta/Projects/atr-layoff-tracker` holds `railway/sources/opendart.py`,
retired on 2026-07-24 in commit `aead15e` with the reason "**0 layoff rows ever**
came from EDINET(JP)/OpenDART(KR)/CVM(BR)". Read read-only; nothing imported,
nothing copied. **That is outcome 2 of the three the coordinator named, and it is
outcome 2 for a reason that is now proved rather than assumed.** The sibling read
the disclosure list for discovery and then scanned document BODIES for Korean
layoff vocabulary. Korean statutory disclosure has no workforce-reduction item —
the 36 major-report endpoints above are the proof — so its zero was guaranteed by
the taxonomy, not by the source's quality. Read it as a fact about layoffs, not
about appointments. Two things from that codebase were genuinely useful as
RESEARCH and are re-derived here rather than borrowed: the `status` code
semantics, which are on FSS's own message table, and the fact that somebody
already tried the English viewer as a citation, which is what prompted measuring
it and finding the "Reject" page.

### Where this brief was wrong about the repo

1. **"Promote KR from `discovery_only`" — KR was not in `MARKETS` at all.**
   `("ko", "KR")` has been in `GOOGLE_NEWS_LOCALES` with its own Korean query
   pack the whole time, and `data/sources_catalogue.csv` carries five Korean
   publisher feeds, so the country was being swept while the coverage manifest
   said nothing about it. KR is added now, at `discovery_only`.
2. **"the major-report or 사업보고서 family"** carries no officer change. All 36
   endpoints checked; see above.
3. **`data/sources_catalogue.csv` needed no edit.** That file is the publisher
   FEED catalogue; a structured connector is registered in
   `source_registry.SOURCES` and `COLLECTOR_BY_SOURCE_NAME`, and
   `build_sources_json.py` derives the page from those.

### Korea stays discovery_only, on purpose

The rule is that coverage is earned by a working connector, a health check and a
passing test. There are 48 passing offline tests and the whole
`_row -> as_classified -> build_signal -> store` path runs against a throwaway
database (4 stored, 0 rejected, 3 declined for the three stated reasons). What
does not exist is a single authenticated call. **What the source holds is
measured; what the connector does is not**, and a tier is a public claim about
the connector. Promotion is one commit after the first real run: add
`opendart_korea` to `KR.live_sources` and move the status, recording what the run
returned.

Unproven until then, listed so the first run knows what to look at: the exact
`list.json` row shape (taken from FSS's published response spec rather than from
a response), **whether `corp_name_eng` is populated for every listed filer** —
which is the one that decides real yield, because a blank declines the row — and
the real request cost of one window (estimated at ~8 list pages plus one
`company.json` per distinct employer, so roughly 30 requests against a documented
20,000/day quota).

---

## 2026-07-30 — Japan has a typed CEO clause; the sibling's EDINET zero was the ordinance, not the source

Build the Japanese equivalent of the India connector. It ships, it costs nothing,
and it is **much narrower than the brief assumed** — narrow enough that Japan
stays `discovery_only`. Every number below is reproducible from a command; the
one thing that is NOT measured is the only thing that matters for promotion, and
it says so.

### The sibling had already built and retired this. That result does not transfer

`/Users/dakotta/Projects/atr-layoff-tracker/railway/sources/edinet.py`, wired to
`foreign-filings.yml`, retired in commit `aead15e` on 2026-07-24: *"0 layoff rows
ever came from EDINET(JP)/OpenDART(KR)/CVM(BR). Those regulatory filings
essentially never announce layoffs"*. Read read-only; nothing imported, nothing
copied.

**That zero was guaranteed by the ordinance, not earned by the source.** Read the
law and count:

```
python3 -c  # against e-gov lawdata 348M50000040005, parsed with ElementTree
  Article 19(2) has 44 items.
  Items containing ANY workforce-reduction word (解雇/人員/削減/希望退職/
    早期退職/整理解雇/リストラ/雇用/従業員数/退職): NONE
  Items mentioning 代表取締役: ['9']
```

An extraordinary report **cannot** announce a layoff, because no clause requires
one: the 44 triggers are disasters, lawsuits, mergers, divestitures, subsidiary
and shareholder changes, bankruptcy, debt covenants, auditor changes and one
officer clause. A layoff tracker pointed at this was structurally certain to
return zero on day one. So the retirement is a fact about layoffs and says
nothing about appointments.

Two further things the sibling's code shows, both load-bearing here:

* **It never read `currentReportReason`.** `grep` for it in that file returns
  nothing, as does `臨時`, `180` and `reason`. It fetched every document type,
  then downloaded ZIP archives and scanned bodies for layoff vocabulary — the
  expensive path, and it skipped the typed field entirely.
* **Its `source_url` does not resolve.** `viewer_url()` returns
  `disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID=<id>`. Measured 2026-07-29:
  that URL returns the **same 82,145 bytes** for a real id (`S100VV88`) and a
  nonsense one (`S100ZZZZ`), and `docID` appears nowhere in the HTML. It is the
  search screen. See the source-URL section below.

### The clause: verified, typed, and only one of them

`currentReportReason` (臨報提出事由) is a document-list **metadata** field, and the
EDINET API specification (Version 2, 2026-06, page 47 item 29 + footnote *4)
defines it as a clause number, comma-joined for multiple reasons:

> 「臨報提出事由は、『第19条第2項第1号』、『第29条第2項第1号』のように記載され…」

So the reason is a closed machine-readable label of the same class as Item 5.02
and a SEBI Regulation 30 category. **The brief's STOP condition — "if it is only
free prose, stop" — does not fire.** No document is downloaded and no model is
called; `as_classified` closes the record and spend is zero.

`docTypeCode` 180 = 臨時報告書, 190 = 訂正臨時報告書 (spec page 88).

**The scope is the representative director alone.** Article 19(2)(ix) is the only
officer clause in 44, and it reads 提出会社の代表取締役…の異動 — the chief
executive and co-representatives, not the wider board and not senior management.
India's Regulation 30 covers every director and every key managerial person;
Item 5.02 covers directors and principal officers. **Do not describe this as
"officer changes".** It is a CEO-change feed.

### Four traps, each of which would have shipped silently

1. **A substring match files audit firms as leadership changes.**
   `第19条第2項第9号の2`, `の3` and `の4` all have the accepted clause as a
   string PREFIX, and they are shareholder-meeting resolutions, a rejected AGM
   resolution, and **a change of accounting auditor**. That last is the
   `bse_india` auditor exclusion arriving in a different disguise: an audit firm
   is an appointed firm, not an employee. Worse, `第29条第2項第9号` belongs to a
   DIFFERENT ordinance (405M50000040022, specified securities) where item 9 is
   ファンドの併合 — a **fund merger**. Read from that ordinance, Article 29(2)
   has no officer clause at all, so REITs are excluded by law rather than by
   taste. Matching is therefore whole-element equality, never `in`.

2. **HTTP 200 on every error, in two different body shapes.** Verified live
   against the real host on 2026-07-29, and documented at spec pages 82-84:

   | condition | HTTP | body |
   |---|---|---|
   | no key / bad key | **200** | `{"StatusCode": 401, "message": "Access denied due to invalid subscription key…"}` |
   | throttled | **200** | `{"StatusCode": 429, …}` |
   | bad parameter / not found / server error | **200** | `{"metadata": {"status": "404", "message": "Not Found"}}` |

   A `resp.status_code != 200` check sees success, finds no `results`, and
   reports a healthy empty day — so an expired key and a throttled run would
   both look like "Japan filed nothing", forever. `_status_of` reads both shapes
   and anything but 200 raises. The sibling's client checked `status_code` only.

3. **Full-width digits eat correct records.** `currentReportReason` is typed
   全半角 in the spec, so the clause can arrive as `第１９条第２項第９号`.
   Python's `\d` matches full-width digits, so `validate._NUMBER` tokenises a
   half-width summary as `{19,2,9}` against a full-width `raw_text` as
   `{19,２,９}`, and `assert_figures_are_sourced` discards the whole record for
   "inventing" 2 and 9. Demonstrated before the fix was written:

   ```
   assert_figures_are_sourced("filed under 第19条第2項第9号",
                              "…内閣府令第19条第２項第９号の規定に基づき…")
   -> Rejected: figure(s) not present in source text: ['2', '9']
   ```

   The collector normalises the clause once and writes that SAME string into
   both the summary and `raw_text`, so the two cannot diverge. This is the third
   instance of this bug class in three days (the `sec_execcomp` newline glue and
   the missing thousands separator were the first two), and the pattern is
   always the same: two renderings of one figure that were never compared.
   Pinned by `test_a_full_width_clause_still_round_trips` and by a test that
   asserts the un-normalised pairing really is rejected.

4. **A Japanese company name produces an EMPTY slug.** `vocab.company_key`
   passes non-ASCII through untouched, so `株式会社オプトラン` becomes
   `株式会社オプトラン` and the company-profile slug
   (`[^a-z0-9]+ -> -`) is `""`. Every Japanese employer would collide on the
   empty slug and the profile route would break. **The fix is not a
   transliteration rule of ours.** The official EDINET code list publishes each
   filer's own English name, and a filer without one is DECLINED and counted.
   Measured on the real list, 2026-07-30: **3,428 of 3,829 listed filers carry
   one (89.5%)**, so ~10% of Japanese filings are refused by design.

### The source URL is the document, because the viewer is not

| candidate | real id | bogus id | verdict |
|---|---|---|---|
| `disclosure2dl…/searchdocument/pdf/{docID}.pdf` | 200 `application/pdf` | **404** | stored |
| `disclosure2…/WEEK0010.aspx?docID=` | 200, 82,145 B | 200, **82,145 B** | refused |

The BSE lesson was link ROT (AttachLive → AttachHis). Japan's trap is the
opposite and worse: a URL that can never rot **because it never resolves**, so
`link_check.py` would report it healthy forever while every Japanese row cited a
search box. The PDF permalink needs no API key, so a reader can open it.

### Licence: a green light, and it constrains the design

EDINET's terms (`WZEK0030.html`) put the content under the Japanese **Public Data
License 1.0** — commercial reuse and redistribution permitted — and require
attribution (carried in `source_name`). Unlike ASX, nothing here forbids
aggregating and republishing. But they prohibit scraping the website while
explicitly exempting the API:

> 「スクレイピング等を利用して本ウェブサイトからコンテンツを機械的に取得すること
> は禁止します。ただし、API機能を利用する場合はこの限りではありません。」

That is why every FACT comes from the API. The one non-API fetch is the code
list, which the spec itself publishes as a 固定リンク for API users (page 86), so
it is the sanctioned path rather than a scrape. It also closed off measuring
volume by crawling the viewer: a refusal to measure by a prohibited method.

### What it refuses to claim

* **Every row is `neutral`, never `hiring`.** Item 9 covers a person becoming a
  representative director and ceasing to be one under ONE clause, so the typed
  metadata cannot tell an arrival from a departure. Guessing would make half the
  rows wrong. Recovering the direction means reading the body — an LLM call per
  document — and that trade was declined, because zero-cost is the premise.
* **No person is named**, for the same reason. The filing is linked and says so.
* **No city, ever.** The code list's address is ward-level with full-width digits
  and, for the Tokyo wards holding most large filers,
  `新宿区西新宿六丁目５番１号` never says Tokyo. A city would need a ~1,900-entry
  municipality vocabulary, and guessing is how `ats_boards` turned
  "Cambridge, MA" into Morocco. `country` is Japan by construction.
* **No figure at all.** The metadata carries no amount and no headcount, so the
  only numerals reaching a summary are the clause and the filing date.
* **Corrections (190) are skipped, not stored.** Storing one would double-count
  an event, and this repo appends revisions rather than overwriting. The hook a
  future session needs is `parentDocID`, and it is on the row.

### THE RECALL HOLE, which is large and invisible

Item 9 exempts a change occurring between the annual shareholders' meeting and
the filing of the annual report when the annual report already describes it.
Japanese AGMs cluster in late June and 有価証券報告書 are filed in the same
weeks, so **the commonest timing of a Japanese presidential succession can
produce no extraordinary report at all.** This source is a floor on Japanese
leadership change, not a count of it. Said in the read-through, the registry note
and the sources page, and asserted by a test.

### Measured, and the one thing that is not

Offline, whole `run_collect` path, stubbed transport, nothing written:

| | |
|---|---|
| list API calls | 7 (one per calendar day; the endpoint takes one date) |
| code-list downloads | 1 |
| documents read | 12 |
| extraordinary reports (180) | 10 |
| reporting `第19条第2項第9号` | 6 |
| stored | 3 |
| declined (no English name / withdrawn / viewing expired) | 3 |
| corrections skipped | 1 |
| **rejected by validate** | **0** |
| **deferred** | **0** |
| cost | **$0.00** — no model, no document fetch |

Tests **1,823 → 1,876** (+53), all green. `ops_status.py` exits 2 before and
after, on the same three pre-existing stale collectors (gdelt 54h, sec_edgar 52h,
sec_form_d 60h); nothing here added an item. Two `source_health` error rows
written by keyless local dry runs were deleted afterwards, so the committed
database carries no false alarm — the database itself is NOT staged by this work.

**VOLUME IS UNMEASURED, and that is the whole reason Japan stays
`discovery_only`.** No authenticated call has ever been made from this repo: the
key exists as a GitHub secret and was deliberately not available locally, so
unlike India's 354-in-7-days and Australia's 192-in-30 there is no live count
here. The bound, stated as an estimate and not a measurement: **3,829 listed
filers** on the official code list against a published Japanese president-turnover
rate of **3.84% for 2025** (Teikoku Databank) puts the order of magnitude at a
**few hundred a year, roughly 1-3% of India's ~13,000** — before the AGM
exemption above removes more. Thin, but a CEO change is the highest-value
leadership row there is.

**Also unproven until the first real run**, and listed so nobody mistakes the
green suite for verification: the fixture's `currentReportReason` VALUES are
constructed to the published spec rather than captured, so the exact string form
(half-width vs full-width, spacing, and whether multi-reason joining uses `,`
without a space) is spec-derived; and the real ratio of 180s to item-9s is
unknown.

### Promotion gate, so it is one commit and not a judgement call

Japan becomes `structured_official` when a real run has measured it. Exactly:
dispatch `collect-structured.yml` with `source=edinet_japan`, `dry_run=true`;
read the printed line `N documents read, M extraordinary reports, K reporting
第19条第2項第9号, S usable`; then in ONE commit flip `MARKETS`'s JP entry to
`STRUCTURED_OFFICIAL`, add `edinet_japan` to its `live_sources`, and update
`test_japan_stays_discovery_only_until_a_real_run_measures_it`. If K is
implausibly zero over 7 days, the clause strings differ from the spec and the
matcher is what to fix — not the floor.

**Scheduled, on Tuesday.** `collect-structured.yml` gains `0 4 * * 2`, and the
day is deliberate: Monday already carries BSE at 04:00, the link-hygiene ticket
at 05:30 and the digest at 13:00, and every writer shares the one
`talent-collect` lock in which GitHub keeps a single pending run that a second
scheduled writer can evict. There is deliberately **no minimum-rows floor** of
the kind `bse_india` carries: India's 250-a-week makes a zero provably a
breakage, whereas one clause covering one role across 3,829 filers can genuinely
be quiet, so health is judged on `LAST_RUN["read"]` instead. The honest floor
cannot be set until the first real run measures the rate.

### Where the brief was wrong

* **"Documents are Japanese, often Shift-JIS or in XBRL."** The API's JSON
  metadata is UTF-8, and this collector never touches a document body, so the
  encoding trap does not arise on the stored path at all. Where encoding DOES
  bite is the code list, and there the specific claim is wrong in a way that
  matters: both lists are **cp932, not `shift_jis`** — `shift_jis` raises on
  byte `0xfb` at offset 35,244 of the Japanese list, because cp932 carries the
  NEC/IBM extended characters Japanese company names actually use. Naming the
  narrower codec would crash the run on such a filer. (The sibling decoded
  bodies as `utf-8` with `errors="replace"`, which would have mojibaked them
  silently; it never mattered because it found nothing.)
* **"万/億 magnitude characters."** Real, but not reachable here: no figure is
  stored, so there is nothing for a magnitude character to corrupt. The
  full-width DIGIT problem was the live one, and it was in the clause reference
  rather than in any amount.
* **"Extraordinary reports are the likely home for officer changes — confirm
  it."** Confirmed, but the brief's framing implied a category comparable to
  SEBI's. It is one clause covering one role, with an exemption that removes the
  commonest timing. The honest headline is "Japan types the CEO change", not
  "Japan types officer changes".
* **"MEASURE and report honestly: documents seen in a real recent window."** Not
  possible: the key is a GitHub secret and no authenticated call could be made,
  and the alternative — crawling the viewer — is prohibited by the terms. Stated
  as unmeasured rather than estimated into looking measured.

---

## 2026-07-30 — the page stops disagreeing with itself: sources, city pills, five amounts

Launch-blocker pass over `wordpress-plugin/`. The theme running through all of
it is a page stating a number that the same page contradicts one click later.
Every figure below is reproducible from a command in this repo or a curl against
the live site, and where the brief that started this work was wrong about the
code, it says so.

### The sources page named five of its nine live collectors

`/sources/` printed "not yet reported" for `national_press`, `sec_execcomp` and
`uk_paygap` (confirmed live before the fix: `grep -c "not yet reported"` on the
served page returned 3). Between them those three are most of the database:
`national_press` found 9,305 items on its last run, and `uk_paygap` supplies
4,761 of the United Kingdom's 4,793 rows. The cause was a five-entry
`$by_collector` map typed by hand in `includes/sources.php` beside a nine-entry
`COLLECTOR_BY_SOURCE_NAME` in `source_registry.py`.

Fixed by deriving it. `sources_manifest()` writes a `collector` key onto every
row of `data/sources.json`; `tit_sources_collector_map()` builds the join from
that. A source added to the registry now arrives on the page with its collector
attached.

The four collectors that report health and are NOT sources stay absent, with the
reason recorded in `_NOT_SOURCES` rather than implied by omission:
`archive_sources` and `link_check` maintain the ledger behind the links, `recall`
measures what we miss, `sec_form_d_bulk` backfills a source already listed. That
set is asserted disjoint from the manifest, so it cannot become a hiding place
for a real source nobody wants to write a row for.

### Every city pill returned a different number from the one printed on it

`SELECT city k, COALESCE(country, hq_country) cc ...` in `shortcodes.php` had
three defects at once. Measured against the committed database:

| city | pill printed | click returned | after |
|---|---|---|---|
| London | 19 | 1,339 | 1,339 |
| Manchester | absent | 106 | 106 |
| Edinburgh | absent | 49 | 49 |
| Toronto | 25, US flag | 27 | 27, CA flag |

1. **It grouped by bare `city`** while the pill writes `city=<name>`, which
   `api.php` resolves as `city = %s OR (city IS NULL AND hq_city = %s)`. Almost
   every London row is placed by its employer's head office, and this count
   could not see one of them. Manchester and Edinburgh were missing from a strip
   carrying Seattle (43) and Toronto (27).
2. **It was counted under a bare `is_current = 1`** rather than `{$base}`, the
   only strip on the page that was, so it included the 3,143 routine officer
   filings the table sets aside.
3. **`cc` was non-aggregated under `GROUP BY city`**, so the flag was whichever
   row the engine reached first and MySQL and SQLite need not agree. Toronto
   holds 24 Canadian rows, 2 American and 1 from Hong Kong, and flew a US flag.
   It is the modal country now, ties broken alphabetically.

`tit_city_expr()` and `tit_country_expr()` join the other shared predicates in
`api.php` so the grouping rule has one authority; `/aggregate`'s own `by_city`
had defect 1 and got the same fix. The index-friendly `OR` form stays in the
WHERE clauses for the reason `tit_place_kinds()` already documents.

Still ONE query and still **12 cold, 0 warm**. `tests/php/render_dashboard.php`
now parses every pill out of the rendered markup and asserts its printed count
against the clause `tit_place_kinds()` declares, with two new fixtures for the
shapes that caused the bugs.

### Five funding amounts off by a factor of a million, and the rule behind them

| employer | stored | was | now |
|---|---|---|---|
| Terminal | `$20-million USD` | 20 | 20,000,000 |
| Abaco Technologies | `USD 53 millones` | 53 | 53,000,000 |
| Visibuilt | `25 millioner kroner` | 25 | NULL |
| Serpier | `10,5 mio. kr.` | 105 | NULL |
| Multiverse | `500 millones` | 500 | NULL |

The multiplier vocabulary was English-only, and `\s*` does not match the hyphen
in `$20-million`. But the deeper rule was the denylist: `parse_funding_usd`
refused a currency only when `_NON_USD` recognised the word, so "no foreign
currency I know" read as "US dollars". `kron[ao]r?` does not match "kroner" and
"kr." was in no list at all, so two Danish rounds sat in a column the page
promises holds only amounts a source stated in dollars.

**The test is positive now**: no `$`, `US$` or `USD` in the string, no number. It
costs nothing to be strict. Of 3,097 current rows carrying an amount, **3,094**
name one of the three outright, and the only three that did not were exactly
these three. Verified across the whole corpus: those five rows change and
nothing else moves.

Widening the vocabulary opened a trap that is closed in the same commit.
`USD 1,5 millones` would strip the comma and store fifteen million for one and a
half, because every comma-decimal string used to be refused as foreign before
its number was read. `_read_number()` decides which comma is which by the
ordinary rule. And `mil` now REFUSES rather than falling through to no
multiplier: it is a million in Singapore English (`US$22 mil`, in the 2026-07-29
sweep) and a thousand in Spanish, and twenty-two dollars was wrong under both.

**The five stored rows are NOT corrected.** Three of them need their live
`funding_amount_usd` set to NULL, and until this session no route on the plugin
could write that: `/enrich` ignores an empty field by design, `/correct` cannot
blank a value, and a withdraw-and-republish would remove both rows because a
revision carries the same `content_hash`. `/enrich` now takes an explicit
`clear` array restricted to `tit_clearable_columns()`. Applying it is a queued
writer run and belongs to the owner.

### Four pages had no description, and og:description existed nowhere

The dashboard, `/sources/`, `/recall/` and `/corrections/` shipped with no
`meta[name=description]` (confirmed live: `grep -c` returned 0 on all four). The
brief said the mechanism existed and had merely not been applied; half true. The
`description` mechanism existed on the company and place pages.
**`og:description` existed on none of the six**, so no link to any page of this
product had share-card text. `tit_head_description()` prints both from one
string, truncating at a sentence rather than mid-figure.

### Three more places the copy contradicted the data

- **`tracked since` on all 715 indexable profiles said July 2026**, because it
  was `MIN(captured_at)` and every row was captured when the backfills ran,
  while the same page said "last update 3 months ago". Now
  `MIN(COALESCE(published_date, DATE(captured_at)))`, matching the span note.
- **`/corrections/` captioned a table with `date_i18n('j F Y')`** — today's
  date, whatever today was — over figures measured on 29 July, while a later
  correction had taken the money total from **$124.0bn to $101.4bn** (live
  `/aggregate`, 2026-07-30). The caption prints the date measured; the fall is a
  note with its own dates rather than an overwritten cell.
- **One filter had three names.** Checkbox "Only Updates That Move Headcount",
  chip "Only with a stated headcount", SQL `signal_direction IN (...)`. Only the
  checkbox was right: `headcount` is non-null on **11 of 15,711** rows (0.07%)
  and the control does not read that column. The comment claiming "about 87%"
  said 99.93%. Chip and comment fixed.
- **`/places/` counted 15,711 while the dashboard counted 12,568**, and only the
  dashboard explained itself. One sentence each side.
- **`tit-f-state` rendered 51 bare postal codes.** `tit_state_names()` carries
  all 50 states, DC and the five territories on day one, for the reason
  `tit_country_names()` once failed with 52 of ~200 codes. It rides on a `data-`
  attribute as well as `wp_localize_script` per gotcha 10, costs **2,096 bytes**
  of markup, and that is why the harness byte budget moved 152,000 to 156,000
  with the note saying what bought it.

### The cross-tracker pairing: built, measured, switched off

An employer cutting in one place while hiring in another is the signal only
somebody holding both halves can produce. `includes/cross_tracker.php` reads the
sibling's PUBLIC HTTP API at render time, caches it in a `tit_` transient keyed
on `TIT_VERSION`, retries once on a 5xx, times out at four seconds and caches a
miss short so a sibling that is down cannot make every render wait. No file
imported, no database joined.

It ships DISABLED, and that is a count:

```
our employers                                    7,377
sibling names on /layoffs/v1/companies          20,000 -> 18,648 keys
keys present in both                               559
of those, with a hiring-direction row here           6
```

Reading the six is what settles it. The sibling's own `?company=US Bank` answers
with **Piraeus Bank** for three of its four most recent rows, so a loose rule
publishes a Greek redundancy against a named American bank. Tesla matches
"TRIGO (Tesla)", a contractor. Saint-Gobain matches two subsidiaries. Infosys
and SouthState pair 2024-2025 cuts against July 2026 hires, and the claim is
concurrency. Exactly one pair is near defensible — HSBC, 20,000 cut in the UK on
2026-03-19 against 200 hired in wealth management in July — and that hiring
row's own geography is wrong here (`city=London, country=SG`).

**Zero pairs defensible, one fabricated claim available.** What would change it,
in order: a shared ticker or CIK instead of a name match; a decided subsidiary
rule; a recency window binding both sides; and more than 49 hiring rows in
15,711.

### Where the brief was wrong

- **`finance.yahoo.com` is NOT already blocked.** The brief was corrected
  mid-session to say `_AGGREGATOR_DOMAINS` blocks it by registrable domain.
  There is no `_AGGREGATOR_DOMAINS` in `pipeline/validate.py`. The guard is
  `host in _BLOCKED_SOURCE_HOSTS`, an exact-host `frozenset`, at line 466.
  Proved by running `validate.build_signal` on all three hosts:
  `news.yahoo.com` is rejected by name, `finance.yahoo.com` and
  `sg.finance.yahoo.com` pass the host check.
- **It is three rows, not two**, and blocking the domain would be WRONG for one
  of them. Fetching each URL's `rel=canonical` settles which is which:
  `finance.yahoo.com/small-business/articles/7-eleven-...` canonicalises to
  `cstoredive.com` and `...warsteiner-owner-haus-cramer...` to `just-drinks.com`
  — syndication, and we already read cstoredive.com directly for two other rows.
  But `sg.finance.yahoo.com/news/hsbc-plans-hire-100-ai-...` canonicalises to
  ITSELF: Yahoo Finance Singapore is the publisher of record. A registrable-domain
  block would drop it, which is the editorial-newsroom over-block
  (`_EDITORIAL_EXCEPTIONS`) again. Also
  `finance.yahoo.com` is a registered candidate source in `source_registry.py`
  and appears in the recall gold set. The right rule is the canonical host, not
  the requested one. NOT IMPLEMENTED: `validate.py` was held by another agent.
- **There IS a `php` binary on this machine** (8.5.8). `docs/HANDOVER.md` said
  there was not, so the five harnesses under `tests/php/` had been treated as
  CI-only. They run locally in under two seconds.
- The audit's counts drifted with the data by a row or two throughout (12,566 vs
  12,568 notable; Manchester 108 vs 106). Its diagnoses were otherwise accurate.

### Deliberately not done

- **Not deployed.** The session was told not to push, and `deploy-plugin.yml`
  checks out a ref on GitHub, so a deploy of local commits is impossible without
  one. Version bumped to **1.53.0** in both places; live still serves 1.52.0.
- **The five funding rows are not corrected**, per above.
- **The Toronto city/region/country correction is not run.** `/correct` accepts
  those columns now, which was the blocker; the run itself is a writer and must
  be queued through `drain-writers.yml`, never dispatched.
- **Nothing was armed.** No cron uncommented.
- **Nothing submitted to Search Console.** Neither tracker's sitemap is in
  `robots.txt` or `sitemap_index.xml`, so 748 indexable pages are reachable only
  by internal links. Owner action.

---

## 2026-07-30 — a figure guard that ate records, a cache that does not exist, and the 81 misses

Three jobs, and two of the three briefs turned out to be wrong about the code.
Every number below is reproducible from a command in this repo.

### 1. `validate._NUMBER` glued a magnitude across a line break

`\s` matches a newline, and the magnitude suffix sat behind a bare `\s*` with
nothing after it, so `"28.07.2026\n\nK M Sugar Mills"` tokenised as
`28072026k`. Since `assert_figures_are_sourced` compares two SETS, and every
collector joins its fields with a blank line, the glue lands on the SOURCE side
and a figure that IS verbatim in the source reads as invented — the whole record
discarded, silently. Fixed: the suffix now sits behind horizontal whitespace
only (`_H_SPACE`, every character `\s` matches except the ones that end a line,
so NBSP still counts and CR/LF/FF/VT/U+2028/U+2029 do not).

Measured, `python3 -m analysis.figures.replay`, 15,711 current rows:

| | |
|---|---|
| newline junctions rebuildable exactly | 11,678 |
| junctions where the glue FIRED | **465**, all `sec_execcomp` |
| records those 465 cost | 0 — that body repeats the filing date, so the clean token survives |
| rejections on record, attributable to this rule | **0 of 1,368**, and that is the honest answer |

`raw_text` is not persisted (`measure_city_placement.py` documents the same
limitation) and a rejected candidate leaves a URL in `seen_urls` with no text
and no reason. So the cost on the sources whose bodies we no longer hold is not
knowable, by this script or any other, and the script prints that as a zero
rather than an estimate.

**The brief said this affects `sec_edgar` and `national_press`. It affects
neither.** `sec_edgar.fetch_text` ends with `re.sub(r"\s+", " ", text)` and its
synthetic headline ends in the word "change"; `national_press._plain` collapses
whitespace too and its dateline opens with "(". The only collector whose
`headline\n\nbody` junction can put a digit next to a B, M or K is
`sec_execcomp` — headline ending in a filing date, body opening with the company
name — which is the 465 above. `bse_india` hit the bug first and worked around
it by quoting its filed description; that comment asked for this fix.

### What did NOT ship, because it was built and then measured

The same glue happens INSIDE a line — `"hire 300 by 2027"` -> `300b` — and it is
commoner: 261 sites over 163 stored rows. The obvious fix is `\b` after the
suffix. **It is a regression, and the measurement is why we know.**

| variant | frees | BREAKS |
|---|---|---|
| horizontal space only (shipped) | 0 | **0** |
| + word boundary | 5 | 23 |
| + word boundary + English magnitude fold | 5 | 14 |

The missing boundary is doing multilingual magnitude folding by accident:
`millones`, `millions`, `Millionen`, `miliona`, `millioner` and `millions` all
truncate to `m`, which is exactly what makes them compare equal to the model's
English "million". The 14 rows it breaks are every one a foreign-language
funding round — Multiverse's 500 millones, Proxima Fusion's 411 millions, 5U
AI's 3,2 Millionen. The feed set spans **43 languages** (`data/feeds.csv`), so
doing this on purpose means a magnitude vocabulary in 43 languages, and a
partial vocabulary fails silently and looks like sparse data. Left alone,
pinned by `tests/test_figure_guard.py` so the next person meets the reason
instead of the trap. (Adjacent, unfixed, same class: `£1bn` in a headline does
not match `$1 billion` in a summary, because `bn` matches whole and `billion`
truncates to `b`.)

### 2. The DeepSeek cache the routing was going to hit does not exist

TECHLOG's own "smallest further lever" priced pinned routing at **-$2.84/month
at zero cost to coverage**. Checked against OpenRouter's endpoints API on
2026-07-29:

* `deepseek/deepseek-chat`, the configured `TIT_MODEL`, has three endpoints —
  streamlake, deepinfra/fp4, novita/fp8 — and **not one publishes an
  `input_cache_read` price**. There is no cache on this slug to route to.
* `deepseek/deepseek-chat-v3.1` has four that do, at **~0.5x** (deepinfra
  0.00000013 against 0.00000025 prompt), not the 0.1x DeepSeek's own API
  charges. DeepSeek's first-party endpoint serves neither slug through
  OpenRouter.
* So the saving is a model switch away and worth about half the advertised
  figure. That is a decision about extraction quality, not a routing tweak.
* **The 60% cache rate (131k of 216k) that motivated the lever is not
  reproducible here.** `source_health` holds zero rows with a non-null
  `prompt_tokens`, and `ops_status [2a]` agrees: "No run has recorded a cost
  yet". Wherever it came from, it was not this ledger — and it would have mixed
  both stages anyway, since Gemini's implicit cache on the gate lands in the
  same counter.

Pinned anyway, because it costs nothing and buys three things: the prefix stops
scattering the day a caching endpoint appears, `cached_tokens` becomes
interpretable, and extraction stops being a lottery between an fp4 and an fp8
host. `provider.order = ["deepseek", "streamlake", "novita", "deepinfra"]`,
keyed by model author so no slug is sent to a model that provider does not
serve. **`allow_fallbacks` is true on every request and no code path sets it
false; `only` and `ignore` are never sent** — a pinned provider's outage must
cost the cache, never the run. Field names read from the docs, not guessed; a
misspelled key inside `provider` is accepted and silently ignored, so
`tests/test_provider_routing.py` asserts every key we send is one the schema
documents. No live call was possible (no `OPENROUTER_API_KEY` here), so
`STATS["providers"]` now records which endpoint OpenRouter says served each
call and the first real run settles it. `TIT_PROVIDER_ORDER=off` reverts.

### 3. The rejection audit: none of the 81 misses was ever fetched

`python3 -m analysis.recall.rejection_audit --write`, read-only, writes
`data/recall_rejection_audit.json` beside `data/recall_worklist.json`.

| stage | n | what it means |
|---|---|---|
| `outside_our_history` | **51** | backfill |
| `publisher_not_wired` | 12 | source, researched but not connected |
| `publisher_unknown` | 11 | source, not researched |
| `feed_read_item_missed` | 7 | filter/plumbing |
| `fetched_then_dropped` | **0** | filter |
| `stored_unmatched` | 0 | matcher defect |

**Zero.** An exact-URL lookup against `seen_urls`: no filter in this pipeline has
ever rejected a gold event, so tuning filters would have moved nothing. The
dominant bucket is a third answer nobody asked for — the gold window is
2026-07-01..28, the earliest run of any collector is 2026-07-27, and
`national_press` first ran on **2026-07-29, the day after the measurement it is
being judged by**. The furthest any route reached backwards on the 28th was
2026-07-20 (Google News, `when:7d`). The 9% is a two-day-old tracker measured
against a four-week window.

The actionable part is the 23 sourcing misses, and the sharpest is that **CTech
is still unreadable**: `national_press` exists because CTech broke four Israeli
rounds we missed, and its catalogue row's `rss` column is still empty. Four of
the 81 are CTech articles. Twelve sit on catalogued publishers with no feed
(calcalistech 4, businesswire 2, globenewswire 2, tech.eu, prnewswire, yahoo
finance — three of those are wire services, one connector each for a lot of
coverage); eleven on publishers not in the catalogue (latamlist 2, finsmes,
european-biotechnology, techla.pro, pv-magazine); seven are inside a live route
and a swept publisher, and four of those seven domains have already delivered us
other articles (betakit 6, entrackr 5, wamda 3, exame 2).

VERDICT: a HISTORY problem, not a filter problem and not yet mainly a source
problem. HIGH confidence on the zero (exact-URL lookup), MEDIUM on the split
between the rest, which rests on publication dates and route reach rather than
on a record of what each run saw. Limits printed with the result: no rejection
reason is persisted anywhere, and nothing records the items a feed carried that
a run did not reach — both can only UNDERSTATE the filter side. The one
judgement call (days of RSS backlog) is a parameter, and the report prints 1, 3,
7 and 14 days: at 14 the counts move (history 34, feed-read 14), the ordering
never does, and the zero never does.

Not done here, both in the owner's lane: adding
`data/recall_rejection_audit.json` to `recall.yml`'s committed `paths`, and a
block in `ops_status.py` to surface it.

### What was tried and thrown away

* The first glue measurement joined `headline + summary` with a space and
  reported 189 hits of `"31 B"` — an artefact of a junction the pipeline never
  builds. Glue is now measured one stored field at a time, and the docstring
  says why.
* Counting glue sites "across a newline" over stored fields returns 0 and always
  will: no stored field contains a newline. The number is printed with that
  caveat rather than quietly dropped, and the real newline exposure is measured
  by REBUILDING the `headline\n\nbody` junction for the collectors whose body
  opening is a template over a stored column.
* The backstop route's countries are catalogue country NAMES and the gold set
  uses ISO-2, so the first version never matched. Fixed, and it changes nothing:
  none of the gold set's 29 countries is one of the 21 backstop countries.

---

## 2026-07-30 — Australia has the spine and not the licence; sixty publishers instead

Two jobs. Build the Australian equivalent of the India connector, and widen the
publisher net from research the house already owns. The first ends in a
**refusal**, and the refusal is the more useful result.

### ASX: the taxonomy is there, the permission is not

The India unlock was a jurisdiction's MANDATED disclosure category. Australia
has one, and it is as good as SEBI's. Measured live over the whole window the
API exposes — 2026-06-30 to 2026-07-30, **10,000 announcements**, 400 pages of
25 from
`asx.api.markitdigital.com/asx-research/1.0/markets/announcements?count=25&page=N`
— ASX types every announcement, **142 distinct types**, and the board and
officer ones are:

| type | 30 days |
|---|---|
| `Director Appointment/Resignation` | 105 |
| `Company Secretary Appointment/Resignation` | 48 |
| `CEO/Managing Director - Appointment Resignation` | 46 |
| `Chair Appointment/Resignation` | 33 |
| **distinct announcements across those four** | **192** |

That is ~45 a week, ~2,300 a year, from a market of roughly 2,200 listed
entities. Not thin. Nothing about it is technically hard: the company name, the
ticker, the sector, the type and the date are all fields in the response, so it
would have been an `as_classified` collector spending nothing, exactly like
`bse_india`.

**www.asx.com.au/robots.txt permits it.** The entire file is `User-agent: *` /
`Disallow: /search*` plus a sitemap line, and neither
`asx.api.markitdigital.com` nor `announcements.asx.com.au` serves a robots.txt
at all. That was checked first, as instructed, and it is a green light.

**The terms of use are a red one, twice.** `www.asx.com.au/legals/terms-of-use`:

> Market Announcements are freely available for investors' private and personal
> use only, and cannot be used for any commercial purpose without the express
> written authority of ASX. A commercial purpose is any use other than accessing
> and using the content for your own personal and private decision making.

and, under Prohibited uses, "use any spider, screen scraper, robot, other
similar software or device, or other similar process, to use or access the Site
in any way whatsoever, including monitoring, downloading or copying any content
on the Site (except ... with ASX's prior written consent)". The legacy
interstitial at `/asx/v2/statistics/displayAnnouncement.do` makes a human click
it: "I confirm that any content I access will not be used for any commercial
purpose in the context as explained above". ASX sells this use as ComNews and
ComNews Direct.

This tracker aggregates the information and republishes it on a public
dashboard. That is the licensed use, and we do not hold the licence. **This is
the SmartRecruiters decision again** (`collectors/ats_watchlist.json`): every
endpoint answers 200, and the terms still say no, which is precisely why it is
recorded in code and in the registry rather than being settled by whether a
request works. **NEEDS-OWNER**: one email to ASX Information Services turns
2,300 sourced Australian leadership rows a year into a day's work, with the
measurement above already done.

**The trap the next attempt would otherwise re-find twice.**

1. *There is no announcement page and no `asx.com.au` document URL.* The API's
   `url` field is empty on all 10,000 rows. The PDF is reached from
   `documentKey` at `asx.api.markitdigital.com/asx-research/1.0/file/{key}` — the
   vendor's host, not the exchange's. The legacy `todayAnns.do` page does carry
   an `idsId` per announcement, but only for the current day, and its `idsId` is
   NOT the middle segment of `documentKey` (TERRACOM's Final Director's Interest
   Notice: `idsId=03119949`, `documentKey=2924-03115930-2A1686673`). Today those
   two counters happen to sit 4,019 apart. Deriving one from the other would be
   a guessed identifier inside a stored source URL, which is the AttachLive /
   AttachHis mistake with extra steps.
2. *`Change of Director's Interest Notice` is not an appointment.* It is
   Appendix 3Y under Listing Rule 3.19A — a SITTING director's shareholding
   moving — and at **589 in the same 30 days** it is the largest
   leadership-looking type by a factor of three. The brief named 3X/3Y/3Z as the
   likely spine; 3Y in particular would have trebled the volume with rows that
   are not talent signals at all. The appointments themselves sit under Listing
   Rule 3.16.1, which is what the four types above report.
3. *Appendix 3X and 3Z are duplicates as often as not.* `Initial Director's
   Interest Notice` (120) and `Final Director's Interest Notice` (81) are filed
   BECAUSE of an appointment or a cessation, so on 35 of 152 same-day
   (ticker, date) groups they sit beside the change announcement for the same
   person — TERRACOM filed "Final Director's Interest Notice (M Chadwick)" and
   "Director Resignation (M Chadwick)" 30 minutes apart. Including them would
   have taken the headline count from 385 to look like coverage while storing
   one event twice. 82 groups in 30 days ARE notice-only with no change
   announcement within four days, so excluding them costs real recall; that is
   the honest price of the cleaner unit, and it is written down rather than
   hidden. Moot while the licence stands.

`source_registry.py`'s triage block now carries all of this, and
`tests/test_source_widening.py` asserts the paragraph keeps BOTH halves. A
refusal that keeps the measurement and loses the licence reads to the next
session as a rich source nobody got round to.

Australia stays `discovery_only`. Nothing was added to `collect-structured.yml`,
because there is nothing to schedule.

### Sixty publishers, from research rather than from code

The sibling AI Layoff Tracker's `TRUSTED_DOMAINS` holds **705 distinct domains
(698 registrable)**. It was read READ-ONLY as research: no import, no file
copied, no database touched. An outlet list is a fact about the world; the
no-shared-code ruling is untouched.

**372 of those registrable domains are not swept here.** 116 were taken forward
and probed through `collectors/national_press.py`'s OWN `robots_allows` ->
`fetch` -> `parse` path, so nothing was admitted that the live run cannot read:
robots must permit, >=3 items must parse, the newest must be <=45 days old, and
the drift guard must land on the recorded registrable domain. **63 verified.
60 added. 3 refused, measured.**

| | before | after |
|---|---|---|
| feeds in `data/sources_catalogue.csv` | 593 | **653** |
| country buckets with at least one feed | 139 | **164** |

Twenty-three of the twenty-five new buckets are countries this catalogue could
not reach at all: DR Congo, Republic of the Congo, Gabon, Chad, Burundi,
Central African Republic, South Sudan, Sudan, Kosovo, Lesotho, Eswatini,
Malawi, Madagascar, Cape Verde, Sierra Leone, Guinea, Mali, Benin, Afghanistan,
Tonga, Cook Islands, New Caledonia, Bermuda. The other two are
`Pacific (regional)` (Islands Business) and `East Africa (regional)` (The
EastAfrican), filed at coverage `Regional` on purpose so `dateline()` tells the
model the outlet's base does not place the story rather than filing a Fiji round
under Tonga.

Depth went where the 2026-07-28 recall measured zero: **United Kingdom 3 -> 10**
(it had three feeds for the whole country), Canada 7 -> 15, Germany 8 -> 12,
France 10 -> 12, India 9 -> 11, Ireland 4 -> 7, Spain 4 -> 6, Australia 6 -> 8,
Singapore 5 -> 6, Switzerland 4 -> 5.

### Four things that are less good than the headline number

1. **Not one recall zero-coverage country was newly REACHED.** All 27 of them
   already had at least one feed before today. The recall zeros are not a
   feed-existence problem, so this widening is depth against them and nothing
   more; whether depth is what was missing is unmeasured until the next gold
   set runs.
2. **A feed is not coverage.** None of the 23 new countries is covered in the
   sense `CLAUDE.md` means. They have a connector that fetches and a health
   row; they have produced nothing, and several of them realistically never
   will. They are on the sources page as catalogue CANDIDATES, which is the tier
   that says exactly that, and none of them touched `MARKETS`.
3. **This widens a funnel that is already saturated.** The last real run bought
   all its read-throughs and still deferred 95 gate survivors, so the immediate
   effect of 60 more feeds is more deferrals, not more spend and not
   immediately more rows. The value lands when the read cap or the free
   deterministic close rate rises, not today.
4. **The four countries with no feed at all are still Aruba, Curacao, Kuwait and
   Saint Kitts and Nevis.** The sibling's list reaches none of them either.

### What was refused, with the measurement

- **theage.com.au, brisbanetimes.com.au, watoday.com.au.** All three verified
  green. All three serve the SAME Nine business feed as smh.com.au: measured
  2026-07-30, The Age and Brisbane Times share **20 of 20** headlines with the
  Herald and WAtoday shares **15 of 20**. `national_press` de-duplicates on
  `title_key`, so they would have added nothing to the corpus and three lines to
  the public sources page. Only the Herald is listed, plus The Canberra Times,
  which shares **0 of 20** because ACM is a different owner. Pinned by
  `test_no_syndicated_nine_masthead_was_listed_beside_the_herald`. FAZ, Spiegel,
  Sueddeutsche and Welt were checked the same way and share 0 with each other,
  so all four are listed.
- **53 of the 116 candidates found no readable feed** at all under the paths
  tried (Georgia, Armenia, Belarus, Somalia, Liberia, Seychelles, Comoros,
  Angola, Togo, Burkina Faso, Niger, Gambia, Vanuatu, Solomon Islands, Samoa,
  Guam, Northern Mariana Islands and French Polynesia among them, plus
  news24.com, businesslive.co.za, uol.com.br, corriere.it, publico.pt,
  caixin.com, zawya.com, aleqt.com, swissinfo.ch and interest.co.nz). Those are
  "not found by this pass", not "no feed exists" — a hand-found feed URL for any
  of them is a one-line catalogue addition.

### Two items in the brief that were already done

- **`finance.yahoo.com` needs no blocklist entry.** `_AGGREGATOR_DOMAINS` is
  DERIVED from `_AGGREGATOR_HOSTS` by registrable domain, so `news.yahoo.com`
  already blocks `finance.yahoo.com`, `uk.finance.yahoo.com` and every other
  Yahoo host. Adding it by name would imply the domain rule does not work.
  Checked first: the sibling allows no Yahoo host as editorial either. Asserted
  now rather than re-argued.
- **The funding database's newsroom subdomain is still in
  `_EDITORIAL_EXCEPTIONS`** and stays there. Asserted.

### One place the brief was wrong about this repo

Feeds are not added to `data/feeds.csv`. That file is a GENERATED export, built
by `build_feeds_export.py` from `data/sources_catalogue.csv`, and its intended
consumer is the sibling tracker — a test fails if it is hand-edited. So the 60
rows went into the catalogue and `feeds.csv` was regenerated, which is also why
the reciprocity here is neat: the sibling's outlet research came in as research,
and 60 more verified feeds go back out to it through a file that already
existed for that purpose.

---

## 2026-07-30 — link hygiene is armed, and the cron is not where it looks like it goes

The ask was to uncomment two crons: `40 3 * * *` in `archive-sources.yml` and
`30 5 * * 1` in `link-check.yml`. **Both jobs are now scheduled on exactly those
times, and neither of those crons exists.** The schedule lives in a new
`schedule-link-hygiene.yml`, which is not a database writer, and it writes a
queue *ticket* instead of starting a run.

### Why a cron in those two files is a job that skips nights silently

Both write `data/talent_intel.db`, so both sit in `talent-collect`, and GitHub
keeps exactly ONE pending run per group. A `schedule:` in a lock-group workflow
is a direct dispatch with a timer on it, and it has two outcomes:

* it evicts whatever was pending — recoverable if that was a ticket, because
  `writer_queue.tick` re-dispatches a displaced ticket with its inputs intact;
* it IS evicted, and then it is not recoverable. It ends `cancelled` with zero
  jobs — no steps, no logs, no annotation — and the dispatch API does not expose
  a run's inputs, so nothing can replay it. `data/writer_queue.json` still holds
  **15 orphans from 2026-07-29**, all closed by one hand-written triage note.

Both workflow headers already said "NEVER DISPATCH THIS DIRECTLY". A cron is a
direct dispatch that fires 365 times a year. So the commented crons were not
uncommented, they were **deleted**, and the headers now explain the refusal —
a `# schedule:` block left in place is an invitation to uncomment it, which is
the wrong fix arrived at by the most natural route available.
`tests/test_link_hygiene_schedule.py` fails if either file grows a cron, or a
commented-out one.

### What the description got wrong, on reading the code

1. **"Both were hand-dispatched and SUCCEEDED under supervision at 02:00Z
   today."** Green, yes — as **dry runs that recorded nothing**. Run
   30507215991: `DRY RUN: 24 of 164 already in Wayback, 140 would need a
   capture. Nothing recorded, nothing captured.` Run 30507217495:
   `##[warning]DRY RUN... dry run: nothing recorded`. Both workflows default
   `dry_run` to true, which is the trap `link-check.yml`'s own header warns
   about, and it caught the owner. The runs that actually prove the write path
   are the **17:0x pair on 2026-07-29** — 30473757174 and 30474293718 — which
   recorded, merged and pushed as `f56164e` and `c18288e`. That matters: it
   means the merge-and-push step is exercised, so arming is not a first
   unattended execution of untested code. It just isn't the pair cited.
2. **The cron-collision list omitted `0 4 * * 1`** — `collect-structured.yml`
   grew a Monday 04:00 BSE India slot in 95e6df1. A 03:40 archive run with
   `timeout-minutes: 60` can still hold the lock at 04:00 on a Monday. Under the
   queue this is latency, not loss, which is the point of moving it there.
3. **"Confirm both workflows follow the merge path, not a copy."** They do, and
   the `cp` in each is the *safe* direction: `cp data/…db "$RUNNER_TEMP/x.db"`
   saves the run's work before the reset, and `merge_db.py` brings it back
   after. `tests/test_workflows.py` already distinguishes these by destination.
   No launch blocker here.
4. **"Send a browser-ish User-Agent to the WP host."** Neither job touches the
   WP host at all — no `wp-json`, no POST, nothing. They talk to publishers and
   to archive.org, both with `national_press.USER_AGENT`, which is browser-ish
   and names us. The ModSecurity/`no-store`/Cloudflare rules do not apply.

### The launch blocker that was real: a 429 read as "not archived"

`check_availability` returned `None` for anything that was not a 200. Measured
2026-07-30 from this machine: `archive.org/wayback/available` answered **429 to
the first request**, and again 20 seconds later. Every consequence points the
same way:

* pass 1 invents a gap that does not exist;
* the phantom misses go to pass 2, spending a bounded capture budget
  re-archiving documents Wayback already holds;
* each attempt increments `archive_attempts`, and at `MAX_ARCHIVE_ATTEMPTS` (5)
  the URL is recorded `unavailable` — which `archive_candidates` treats as
  **terminal**. Five throttled nights would retire capturable documents forever,
  recoverable only by a hand-written UPDATE;
* and it is invisible: `throttled_out` only fired when Save Page Now was
  throttled *too*, so a run blinded in pass 1 reported `ok` next to a healthy
  capture count. The false-healthy shape again.

Fixed: 429/5xx/timeout now return `RATE_LIMITED`, which is neither a hit nor a
miss. Such a URL is skipped for the night, spends no capture, touches no attempt
counter, and stays in the gap. A run whose free pass went mostly unanswered is
`degraded` with a named warning. The free pass is also paced at
`DEFAULT_AVAIL_GAP = 0.5s` — it costs no money, which is not the same as being
welcome at any rate we like — and a test pins that 600 × (0.5 + 1.0s latency)
plus the 40 × 6s capture budget still fits inside the 1500s deadline, because an
over-long pass 1 would starve pass 2 of every capture while staying green.

`link_check.probe` now retries **once** on a transport failure or a 5xx. Not for
the rot rate — neither state is rot — but for the recheck window: one
observation costs that URL its whole 30-day rotation, so a publisher's bad
afternoon buys a month of not knowing. Never for a 4xx: a 429 is in
`WALLED_CODES` and retrying it would be answering "slow down" with "no".

### Numbers

| | |
|---|---|
| distinct source URLs | 12,970 |
| in the nightly pass's scope (4 publisher collectors) | 235 — **1.8%** |
| the other 98.2% | SEC (3,797 + 2,998 + 1,170 + 9) and GOV.UK (4,761), kept by their own publishers |
| archived now | 72 (48 free + 24 captured), 69 pending, 0 unavailable |
| free-pass hit rate, publisher tail | 48/141 = **34%** (17:12Z), 24/164 = **15%** (02:02Z) |
| checked now | 150/12,970, 0 rotted, 1 `error` (a 454 from techsavvy.media) |
| publisher-tail growth | 34 -> 78 -> 123 distinct URLs/day |
| model spend added | **$0.00** — asserted in two test files |

**What the cap costs, stated where the number is printed.** `1.8%` is the
ceiling this schedule can reach, so the `[2c]` coverage percentage will climb to
roughly there and stop. That is not a stall, and `ops_status.py` now says so on
the line below the percentage, with the scope read out of the workflow rather
than hard-coded. Separately: at 40 captures/night against ~123 new tail URLs/day
the nightly budget does **not** keep up with ingest, and raising `spn_max` makes
it worse, not better. Widening the collector default is the lever; the budget
is not.

`link_check` at 150 URLs/week is a **sample, not a sweep** — 7,800 checks/year
against a corpus that grew 9,347 URLs on 2026-07-28 alone. Left as measured
rather than retuned; `[2c]` prints `checked N/12,970` so the honesty is on the
page.

### Also

* `writer_queue.py enqueue --if-absent` (opt-in, so two retractions of two rows
  never collapse into one). Without it a nightly slot behind a long backfill
  leaves a ticket per night, each aging past `STUCK_AFTER_HOURS` and reporting
  the same single fact as "the lock is starved" once a night.
* The scheduler re-derives its ticket on top of `origin/main` after a rejected
  push rather than rebasing a JSON diff — the `merge_db` lesson one file along.
  `--if-absent` re-evaluated against the fresh queue makes that idempotent.
* It must never contain the string `talent_intel.db`: `test_every_database_writer_shares_one_lock`
  finds writers by raw-text search and would then demand this workflow join the
  very group it has to stay out of. Asserted.
* `staleness.py` leashes: `archive_sources` 2400 -> **54** (two nights plus the
  queue's worst-case wait), `link_check` 2400 -> **180** (the weekly shape
  `bse_india` already uses). The 200 both files suggested is eight missed nights
  for a daily job and only one missed Monday for a weekly one — one number could
  not be right for both.
* `ops_status.py [2c]` now derives and prints the arming state, and goes **red**
  if either writer ever grows a cron.

Suite 1,714 -> 1,782 (+68). One unrelated failure,
`test_form_d_correction.py::test_the_correction_route_writes_those_two_columns_and_nothing_else`,
is another agent's uncommitted edit to `wordpress-plugin/.../includes/api.php`:
a new doc comment containing `normalised_headline` trips that test's substring
allowlist check on the bare word `headline`. Passes against the committed file;
left alone, as that file is not this change's.

---

## 2026-07-30 — the read-through gets its own model, and $5 does not cover it

The owner asked for a frontier model on every read-through inside ~$5/month.
The plumbing is now built for it. **The budget is not met, and the number is
below** — say $13.61/month, not "about five".

### The diagnosis, which is the whole design

One model call was doing two jobs on ~3,100 input / ~400 output tokens.
EXTRACTION is pattern-matching: the employer, the amount, the stage, the place
and the role are all IN the text, and `deepseek/deepseek-chat` lifts them at the
measured $0.00128 a call. The READ-THROUGH is judgement: what a signal means for
hiring in a named place is NOT in the text. The quality A/B that `classify.py`
said had not been run has now been run (`ab_models.py --readthrough`, workflow
run 30506952969) and deepseek RESTATED the headline where the Claude models
wrote something a recruiter could act on.

Upgrading the fused call was the obvious move and the expensive one: ~2,476 of
its ~3,100 input tokens are `SCHEMA_HINT`, so a frontier rate gets paid on the
storage vocabulary the judgement never reads — **$0.0102 a record, $36.72/month
at 3,600 records.** So the call is split. Extraction keeps its model and its
prompt byte for byte; the read-through moves to `TIT_READ_MODEL` (default
`anthropic/claude-sonnet-5`) with its own small prompt in `pipeline/prompts.py`.
Per record that is **5.2x cheaper than the naive upgrade**.

`TIT_MODEL` and `TIT_GATE_MODEL` mean exactly what they meant.
`TIT_READ_MODEL=off` restores the fused behaviour in one line.

### The small prompt, and what it refuses to carry

Measured over all 4,023 current rows from model-path collectors: **median 1,739
characters, p90 1,819, max 2,028**, of which 1,193 is the stable prefix. The
teaser is capped at 500 characters because extraction already lifted every field
we store, so a longer window buys tokens rather than judgement.

Absent on purpose, each for a reason somebody already paid for: `SCHEMA_HINT`
(the whole saving); `headquarters_city`/`headquarters_country`, which are the
model's own knowledge of where a company sits and would place an unplaced
record; and the publisher line, because a writer handed the outlet files every
story in the outlet's home town.

### The rules still bind, three ways

STRUCTURALLY — the writer sees the headline, a teaser and the extracted facts, so
it has nothing to invent a place from but its own memory. DETERMINISTICALLY —
every figure and every gazetteer place in the returned sentence is checked
against the source text and the extracted fields, with word multipliers folded
so "$71M" and "71 million" are one figure rather than a false refusal, and with
place frames read rather than bare names so "Reading the announcement" and
"reports to Charlotte Jones" are not place claims. BY PROMPT for claim-level
grounding, which no regex can check and which is labelled as prompt-enforced
rather than claimed as verified. Confidence needs no new guard: the call returns
exactly one key, so there is no tier for it to promote, and `infer_confidence`
still caps on the source host. `validate` is untouched — it still discards any
record whose figures are not verbatim in `raw_text`.

### Failure handling: the whole record defers

Extraction succeeding while interpretation fails **defers the whole record**
(`ReadThroughUnavailable`, a `Throttled`). Storing a blank was refused because
the guard that would have to be weakened — `validate` requiring a non-empty
`talent_readthrough` — is precisely the one keeping blank differentiators off
the page.

A deferred record is not lost (its URL is deliberately not marked seen, so the
next run retries it inside a recency window measured in days), not silent (the
DEFER line names the reason, `STATS` counts `read_unavailable` and
`read_ungrounded` apart, the run log prints both beside the model that wrote the
prose, and the health row's `detail` carries them), and not free — the extraction
call was already paid for, and `read_unavailable` beside `full_calls` is where
that waste shows up. Because these deferrals feed `mostly_throttled`, a run
where interpretation is broken throughout reports `degraded` and `ops_status`
exits 2 for a human.

### The batch API: half price, a day late, flag off

OpenRouter runs an asynchronous batch API (`POST /api/beta/batches`) and prices
the batch variant at exactly half the sync rate — read off its own `/models`
endpoint, not assumed: `anthropic/claude-sonnet-5` is $2.00/$10.00 per M today
and `anthropic/claude-sonnet-5:batch` is $1.00/$5.00. Going through OpenRouter
rather than Anthropic directly is what makes it maintainable: same key, same 402
handling, same usage accounting, so `spend.py` still sees every cent.

The completion window is 24h, so **batching breaks same-run publishing**: one run
submits, a later run collects, and at twice-daily collection a story reaches the
page 12-24h after it was read. Nothing is lost; freshness is the price, and
freshness is what this product sells. Hence `TIT_READ_BATCH` defaults to off.
The flag adds two calls outside the candidate loop and changes nothing inside it.
One asymmetry worth knowing: a batch's cost lands on the health row of the run
that HARVESTED it, not the one that submitted it.

### Caching: nothing is claimed

The stable prefix is 1,193 characters, ~272 tokens. Sonnet 5's minimum cacheable
prefix is 1,024 tokens and Haiku 4.5's is 4,096, so **this prompt does not cache
and no saving is claimed for it.** A prefix under the floor does not error, it
silently does not cache — which is exactly how a saving gets claimed that was
never possible. The item text still goes last so the shape is right if the
prompt ever grows past the floor.

### The measurement table

Prices are live from OpenRouter's `/models` endpoint (2026-07-30). Token counts
are **derived, not provider-reported**: there is no `OPENROUTER_API_KEY` in the
session that built this, so no call was made and no `usage` block was read. The
character counts are exact; tokens come from this repo's own calibration
(`SCHEMA_HINT` = 10,877 chars = 2,476 tokens = 4.393 chars/token) with a 1.3x
pessimistic multiplier for Claude's heavier tokenizer. **538 in / 90 out** is
therefore a conservative projection of a p90 prompt, and the conclusion below
does not change at the un-multiplied 414 tokens either.

| read-through | $/read | $/month @3,600 | all-in @1,800 | all-in @3,600 |
|---|---|---|---|---|
| `deepseek/deepseek-chat` (fused, today) | — | — | $4.19 | $6.50 |
| `claude-sonnet-5` sync **(shipped default)** | $0.001976 | $7.11 | $7.75 | **$13.61** |
| `claude-sonnet-5:batch` | $0.000988 | $3.56 | $5.97 | $10.05 |
| `claude-haiku-4.5` sync | $0.000988 | $3.56 | $5.97 | $10.05 |
| `claude-haiku-4.5:batch` | $0.000494 | $1.78 | $5.08 | $8.28 |

All-in = gate + extraction + read-through, on the repo's own measured per-item
figures (gate $0.00003 x 1,050 screened/run x 60 runs = $1.89/month; extraction
$0.00128 x reads). Sonnet 5 is on introductory pricing until 2026-08-31; at the
standard $3/$15 the shipped default becomes $0.002964/read, $10.67/month at
3,600.

### $5 is not reached, and the honest number

**At 3,600 reads/month nothing lands under $5 — not even the read-through we
already had.** Gate plus extraction alone are $6.50 before a single
interpretation is bought. The frontier read-through is not what breaks the
budget; the budget was already broken at that read volume.

What $5 all-in actually buys, holding gate and extraction at their measured
prices:

| read-through | reads/month within $5 | per run |
|---|---|---|
| `deepseek` (fused, today) | 2,430 | 40 |
| `claude-sonnet-5` sync | 955 | 16 |
| `claude-sonnet-5:batch` | 1,371 | 23 |
| `claude-haiku-4.5:batch` | 1,753 | 29 |

Measured steady demand is 30-60 reads/run. So the shipped default fits $5 at
roughly half the low end of demand.

**The smallest further lever, and it is not the model.** Extraction is the
largest single line ($4.61/month at 3,600) and 2,476 of its 3,100 input tokens
are a byte-stable prefix that DeepSeek bills at 0.1x on a cache hit. The last
real run measured only 60% of prompt tokens served from cache (131k of 216k),
because OpenRouter routes a model across providers and a prefix scattered across
providers does not hit. Pinning that routing takes extraction from $0.00128 to
~$0.00049 a call — **-$2.84/month at 3,600 reads, at zero cost to coverage or
quality.** It still does not reach $5 with a frontier read-through; it is simply
the cheapest $2.84 available, and it should be spent before read volume is cut.

### `spend.py`: the allowance the owner would need

`MONTHLY_ALLOWANCE_USD` is left at 10.0 and `STOP_AT_FRACTION` at 0.9 — the
budget is policy and belongs to the owner. What the number would need to be:

| configuration | projected | allowance to set |
|---|---|---|
| shipped default, 30 reads/run | $7.75 | **$9** |
| shipped default, 60 reads/run | $13.61 | **$16** |
| `TIT_READ_BATCH=1`, 60 reads/run | $10.05 | **$12** |
| Haiku 4.5 batched, 60 reads/run | $8.28 | **$10** |

The allowance has to exceed the projection by 1/0.9, because the guard stops
collection at 90% of it. At today's $10 the shipped default would hard-stop
mid-month at 60 reads/run — which is the guard working, not failing.

### What was refused

The extraction prompt was not touched. The read-through was not allowed to see
the employer's headquarters or the publisher. No saving was claimed for prompt
caching. The batch path was not made the default, and its 24-hour latency is
printed by the run rather than buried in a comment. And $5 was not reported as
met by rounding a $13.61 projection down to a target.

---

## 2026-07-30 — the city gap: 93.8% of rows had no place, and the vocabulary was why

Measured, read-only, before anything was written: 969 of 15,711 current rows
carried a city, in 25 distinct cities. The assumption going in was that the
extractor was not lifting the city out of the text. The measurement says the
ceiling was one layer lower.

### What was actually broken

`normalize_city` knew 45 aliases across 26 markets. The house rule is
"normalise through a fixed vocabulary or be dropped", so Tel Aviv, Dubai, Sao
Paulo, Seoul, Lagos, Nairobi and Jakarta were places the product could not
report **even when a source stated them plainly**. Nothing errored; the column
came out NULL and the page said "location not stated". The gazetteer now holds
418 aliases -> 338 cities across 105 countries, with three invariants pinned by
tests: one region per country (`validate._region_for_country` scans the table,
so a disagreement is a dictionary-order accident), every country code nameable,
and no city name in two countries.

That last rule is the interesting one. Cambridge, Birmingham, Newcastle and San
Jose are **deliberately absent bare** and reachable only as "Cambridge, MA" /
"Cambridge, UK", because a bare "Cambridge-based" cannot be placed without
inventing a country. Same-country collisions (Portland OR/ME, Columbus OH/GA)
are in — the country is right either way — and stay out of `_CITY_STATE`, where
guessing between them would be visibly wrong. `vocab.place_qualifier_country`
reads the source's own qualifier, which is what makes "London, Ontario" stop
being London.

### The scanner, and the rule it had to be taught first

`cheap_extract.stated_city()` reads six phrasings that name a place outright:
`<City>-based`, `based in <City>`, `headquartered in <City>`,
`<City>-headquartered`, `opens a <City> office`, `its <City> office`. It fills a
NULL city on the funding, hiring and leadership closers, never overrides one,
and never overrides a country the prefix already sourced.

`national_press.dateline()` folds the PUBLISHER's seat into `raw_text` on
purpose, in the exact shape `(Outlet: The Recursive, based in Sofia, Bulgaria —
a hint, not a stated fact.)`. A scanner reading that would file every story a
Sofia outlet carried in Sofia and turn a sourced claim into an invented one.
Hint spans are blanked before anything reads them, offsets preserved so the
story's own "based in" still lines up. Same for `classify`'s "Published by:"
line. Both pinned.

The funding sweep's four tightenings, translated: a place INSIDE a name
declines ("Berlin Packaging-based" resolves to nothing because "Packaging" is
what touches the hyphen); `-based` is not a place frame (AI-based, cloud-based,
faith-based, US-based, Israeli-based); a contradicted qualifier declines
(Dublin/Ohio, Melbourne/Florida, Athens/Georgia, Manchester/New Hampshire,
Perth/Scotland are all real and all would have been wrong); and a city
belonging to someone else is skipped, not stored ("led by London-based Index"
states London about the INVESTOR), while two different cities decline outright.

### The read-through prompt

The `city` field now states the no-inference rule explicitly and names
`headquarters_city` as the place for anything the model merely knows.
SCHEMA_HINT goes 2,436 -> 2,476 tokens, **+40 (1.6%)**, prefix shape untouched
(byte-stable prefix, item text last — lever 4 below). At the measured
$0.00128 / 3,100-token read that is +$0.0000165 per read: +$0.06/month at 60
reads a run, +$0.20/month at the 200 cap.

### The backfill number, and why it is small

`measure_city_placement.py` runs the scanner over the committed database
read-only. **7 rows**, adding Munich, Palo Alto, Rome, Sao Paulo and Vilnius.
Not 3,000.

The reason is worth writing down because it changes what a backfill can be:
**`raw_text` is not persisted.** The pipeline reads headline + teaser,
classifies, and stores the RESULT. What survives is `headline`, `summary` and
`talent_readthrough`, so the sentence that carried the place is usually gone.
And the 14,742 unplaced rows are not news: 4,761 uk_paygap, 3,910
sec_execcomp, 3,476 sec_edgar, 2,363 sec_form_d_bulk, against 226 from every
news collector combined. Those filings never contained an English "X-based"
sentence to lift.

A third pass including `talent_readthrough` finds 17 rows and is **printed with
a refusal beside it**. Its matches read "the Houston-based food and beverage
giant" and "a real estate firm based in San Francisco" — the model's own
knowledge of where Sysco and Prologis are, not anything the 8-K said. Storing
those would be exactly the inference this product may not make, so the script
labels the pass NOT SOURCED and excludes it from the total.

Precision check against the 969 rows a model already placed: 1 agreement, 1
disagreement, 967 declines. The disagreement is instructive — "Ramp fully
launches in Canada alongside new Toronto office" stored Toronto (where the
roles are) while the scanner read "New York-based Ramp" (where the company is).
`extract()` already declines any item `prefilter.site_event_term` fires on, so
that class cannot reach storage through the cheap path; the standalone helper
says so in its docstring.

### The bug the measurement found on the way

`ats_boards.place_key` split a location on commas and tried every part as a
country, so a two-letter US state code resolved to whichever country shares it:
"Peoria, IL" -> Israel, "San Jose, CA" -> Canada, "Cambridge, MA" -> Morocco,
"Boise, ID" -> Indonesia, "Wilmington, DE" -> Germany. Fixed by trying the
WHOLE string before splitting (so the board's own "London, Ontario" survives),
by reading a two-letter state as a state, and by falling back to the country
when a qualifier contradicts the city ("Paris, TX" is not Paris). 10,357 of the
17,956 postings in the committed board state currently carry a country key
rather than a city; the next boards run is where that becomes visible.

### What was refused

Nothing infers a place. The outlet's base is never written to a record; a
country never implies a city; a company's known headquarters stays in the
separate `hq_*` columns and is never merged into `city`. The read-through pass
above was measured and left unstored for that reason, and the two legacy
Toronto/US rows were left alone — a correction is `store.revise()` work the
owner queues, not something a vocabulary change should do silently.

---

## 2026-07-30 — cost levers, second pass: every qualified candidate gets read

The first pass (below, "the cost levers") made looking cheap; this one makes
reading complete. The owner authorized raising the read cap on 2026-07-30, and
the levers around it exist so that raise buys coverage rather than a bill.
Measured facts these changes stand on: gate ≈ $0.00003/item, read-through
≈ $0.00128/item (3,100 in / 400 out), and the last real run bought all 60 of
its reads, stored 34 rows, and budget-deferred 95 gate survivors.

**Read only what can store (`validate.precheck`).** Every rejection
build_signal can reach from the raw item alone — no source URL, an aggregator
or job-board link, a bare domain, an empty body, a filing that ANNOUNCES a
workforce reduction — now fires in run_collect before the gate, with the same
messages. build_signal still calls it first, so backfills and corrections
cannot route around it, and a test table pins the two ends to identical
verdicts. Same rows stored; only WHEN the money is spent moved. The waste
that remains is now printed every run: `reads bought vs rows stored` beside
the token accounting, fed by `classify.STATS["read_stored"]` at store time —
the 60-bought/34-stored gap was invisible until it had a number.

**Leadership joins the deterministic extractor.** "<Employer> Appoints
<Person> as <C-title>" closes for $0 under the funding design: precision over
recall, DECLINE on any ambiguity, `reported` confidence, EVIDENCE_NOTE on the
row, zero exemptions from validate -> store. The funding sweep's four
tightenings translate one for one: a country/city employer span declines
(government stories), a role word in the person span declines ("Former Google
Executive Jane Doe" — where the description ends is a model's job), Title
Case trusts only a one-token employer and a two-token person, and the
stolen-detail lesson becomes stated start dates and interim arrangements —
facts the record cannot carry, so they decline the item. Any mention of a cut
declines outright: the subject-race heuristic keeps such stories FOR THE
MODEL, and a $0 close gets no benefit of the doubt.

**The gate default is `google/gemini-2.5-flash-lite`** (env
`TIT_GATE_MODEL`), citing the repo's own A/B: about half the incumbent's gate
price, and every disagreement was the challenger correcting the incumbent's
false rejection of a real funding round. The read-through model is explicitly
untouched — prompt changes and model switches there stay gated behind
`ab_models.py --readthrough`, which has not been run.

**READTHROUGH_CAP default 60 -> 200** (env `TIT_READTHROUGH_CAP`), the
owner's 2026-07-30 call recorded in the comment. 200 bounds one run at ~$0.26
of reads; it was never the monthly guarantee and still is not — spend.py runs
first on every collect job and hard-stops at 90% of the allowance, and the
OpenRouter key's own cap sits behind it. Projected month at the new defaults,
from the measured per-item figures: gates ~1,050 screened x 2/day x 30 x
$0.00003 ≈ $1.9; reads at the measured steady demand (~60-155 gate survivors
a run, less the deterministic closes) ≈ $2.3-4.6 at 30-60 reads/run bought,
with the theoretical at-cap ceiling $15.4 that spend.py exists to make
unreachable. Budget-deferred logging is unchanged, so the day the cap binds
again is a printed number, not a guess.

---

## 2026-07-29 — the stale employer keys, and the merge that could not be a rule

Plugin 1.47.0 and `correct_company_key.py`. Closes the correction the sitemap
entry below left owed, and the three slug collisions `includes/company.php`
refuses to serve.

### Deriving the worklist found two employers nobody had named

The paragraph left in HANDOVER named six employers, mangled by the `\b`
suffix strip. The three collision pairs made nine. The script takes its
worklist by asking a different question — **every live row whose stored
`company_key` differs from `vocab.company_key(row.company)`** — and that
returns **eleven employers and 38 rows**:

| stored key | corrected to | rows | why |
|---|---|---|---|
| `-operative group` | `co-operative group` | 9 | `\bco\b` ate the "co" |
| `the midcounties -operative` | `the midcounties co-operative` | 9 | same |
| `central england -operative` | `central england co-operative` | 8 | same |
| `-diagnostics` | `co-diagnostics` | 2 | same |
| `associated banc-` | `associated banc-corp` | 1 | same |
| `overlay alpha -gp` | `overlay alpha co-gp` | 1 | same |
| `barking havering & redbridge…` | `barking havering and redbridge…` | 4 | merge |
| `perma-fix environmental services` | `perma fix environmental services` | 1 | merge |
| `daré bioscience` | `dare bioscience` | 1 | merge |
| `crossamerica partners lp` | `crossamerica partners` | 1 | **`lp` joined the suffix list later** |
| `peace coffee pbc` | `peace coffee` | 1 | **`pbc` joined the suffix list later** |

The last two were not in anyone's list. They are the same defect from a
different direction: `company_key` is computed once and stored, so *every*
change to it leaves the rows behind it spelled the old way, and the ones nobody
wrote down are exactly the ones a hand-written script misses. Deriving the
worklist also means the next change to that function needs no new script.

### Why the merge is a list of three and not a rule

Three employers were recorded twice under keys differing only in punctuation,
because the filer spells them two ways: EDGAR's company index writes
`PERMA FIX` where the 8-K cover page writes `Perma-Fix`, and the GOV.UK pay-gap
service holds one NHS trust under two employer ids (15028 to 2022, 22115 from
2023), once with `&` and once with `and`. Both spellings claim one profile URL,
so neither was published.

The rule-shaped fix is obvious and was measured before it was rejected: make
`company_key` fold exactly what the slug folds — accents, `&` to `and`,
punctuation to a separator — so two names that produce one URL cannot produce
two keys. Over the 7,788 distinct stored names:

| folding | keys changed | employers merged |
|---|---|---|
| accents | 10 | 1 |
| `&` to `and` | 141 | 1 |
| hyphen to space | 124 | 1 |
| **all three** | **274 (624 rows)** | **3** |

274 keys re-spelled and 624 rows withdrawn and republished, to merge three
employers. And it contradicts the fix directly above it: folding hyphens to
spaces feeds "co" back to the suffix strip and mangles CO-OPERATIVE GROUP a
second way. So `vocab.EMPLOYER_KEY_ALIASES` states the three merges, one line
each, with the filer id that justifies it. The surviving spelling in each pair
is the one whose space-for-hyphen form is already the canonical slug, so the
fast path in `tit_company_rows()` finds it in SQL without touching the index.

**A list has to be added to, and that is what `ops_status.py [1c]` is for.** It
names any stored key that is no longer current with `vocab.py`, and any two keys
claiming one URL that are not merged, distinguishing "waiting on a human to
choose" from "merged, waiting on the correction to run". Before it, an unmerged
pair was a page that silently never appeared.

### The three URLs that moved, and why they still resolve

Correcting a key moves the profile slug, and three of these employers are over
the publishing threshold, so three URLs in the live sitemap changed:

    /company/operative-group/            -> /company/co-operative-group/
    /company/the-midcounties-operative/  -> /company/the-midcounties-co-operative/
    /company/central-england-operative/  -> /company/central-england-co-operative/

The old three had to 301 rather than 404. **The old URL is not lost
information: it is stored.** A correction appends a revision and the old row
survives at `is_current = 0` still carrying the old key, so
`tit_company_moved_slugs()` joins each superseded revision to the current
revision of the same signal, and step 3 of `tit_company_rows()` resolves the old
slug to the key that signal holds now. The canonical comparison already in
`tit_company_template()` then issues the 301, so there is no second redirect
rule to keep in step with the first — and it is a property of revisions rather
than a redirect list, so it covers every key correction there will ever be.

Both slug forms of the old key are indexed, because both were live URLs: the key
`-operative group` canonicalises to `operative-group` (the leading hyphen is
trimmed) and legacy-slugs to `-operative-group`, and the sitemap published the
first. Two refusals, matching the collision map beside it: a slug a **current**
key holds is never redirected away from (a merge leaves both spellings on one
slug and the survivor still serves it), and a slug two corrections both claim is
dropped rather than guessed.

### Proved by running it, because reading it would not have settled it

`tests/php/route_company_slugs.php` stubs WordPress, backs `$wpdb` with SQLite
so the JOIN executes instead of being matched as text, and asserts the routing
in three phases in three processes (the index memoises in a static): before the
correction, after it, and under an ambiguous move. Deleting the step-3 lookup
fails six assertions. This is the same lesson as the sitemap entry below — a
twenty-URL hand sample passed while 22 of 712 URLs were broken — one level up:
whether a URL 301s or 404s is a behaviour across a state change, and no reading
of the source settles it.

One thing the harness cannot catch, so it is written into the code: the SQL
aliases are `prev` and `live`, not `old` and `new`. SQLite accepts either;
MySQL has reserved both at one version or another for row aliases, and an
unquoted reserved word there is a parse error that takes out every company page
at once.

### What the correction does and does not touch

Shape follows `correct_sec_pillar.py`: dry run by default, `store.revise()` so
the original survives, retract before republish, one row at a time and committed
per row, both of the site's duplicate guards mirrored so a row it would refuse
is withdrawn with a reason instead of vanishing. **Two values move and no
others** — `company_key` and the `content_hash` it feeds. `materiality` is
deliberately *not* recomputed the way the pillar pass recomputes it, because
`compute_materiality` does not read the key, so recomputing could only introduce
a difference. Nothing is deleted, including the orphaned `employer_identity`
entry, which is copied onto the new key rather than moved.

The `--force` guard refuses a worklist above 5% of live rows. Measured here:
38 of 15,650, 0.24%. The one legitimate way to exceed it is a real edit to the
suffix vocabulary, and that deserves a human saying so out loud before hundreds
of rows are withdrawn from the site.

---

## 2026-07-29 — the sitemap was a list of promises and 22 of them were false

Plugin 1.45.5 and 1.46.0, both a consequence of the same review note.

### What a twenty-URL sample could not find

The company sitemap shipped 712 URLs. Twenty were fetched by hand and all
twenty passed. **Twenty-two were broken**, and a reviewer hit one on their fifth
random pick.

An employer key containing "&" was written into `<loc>` as the XML entity
`&#038;`. That is correct XML. The problem is that consumers disagree about it:

| form in the URL | result, measured |
|---|---|
| `%26` percent-encoded | 404, does not survive the WordPress rewrite |
| `&#038;` XML entity, unresolved | 301 to `/company/b-&/` then 404 |
| `&` literal, entity resolved | 200 |

The sample resolved the entity, so it only ever exercised the row that works.
**The sample and the bug were the same shape**, which is the only reason twenty
passes meant nothing. That pair of outcomes is exactly the "Page with redirect"
plus "Not found (404)" report the owner has already had to forward once from
Search Console.

`check_sitemap_urls.py` now fetches EVERY URL in the file with redirects
disabled and asserts 200, no redirect hop, no noindex, and no decoder-dependent
character in the RAW `<loc>` text. It reads the raw text rather than the parsed
tree, because the parsed tree is what hid this. 713 requests take about a
minute. **A sitemap is a list of promises and the only check that verifies a
list of promises is checking all of them.**

It also retries a 5xx three times with a long backoff. A first version retried
after 1.5s and 3s, reported one URL as a hard 504, and that URL answered 200 in
2.4s a minute later: all three attempts had landed inside one bad window, so the
checker was measuring its own impatience. Shared hosting 5xxes at random
(gotcha 8) and a checker that cries wolf teaches its reader to skim.

### 1.45.5, immediately: stop advertising them

The 22 were withheld from the sitemap and made noindex, because their URL was
about to change and asking a crawler to index a URL you are replacing is the
same defect from the other side. Pages stayed reachable and stayed linked.
Sitemap 712 -> 690, and 690/690 verified clean.

### 1.46.0: the slug transliterates

An ampersand has no safe encoding, so it stops being encoded. The slug is now
`remove_accents`, `&` -> `and`, everything outside `[a-z0-9]` -> `-`:
`b-and-m-retail`, `atkinsrealis-uk`. 167 of 7,301 keys change and **all 162 that
had no publishable URL get one**. Sitemap 690 -> **713, all verified clean**.

- **No live link breaks.** The lookup is two steps: the pre-1.46 comparison
  exactly as it was (which resolves every URL that has ever worked here), then a
  small index for the canonical forms SQL cannot express. The old URL 301s to
  the canonical one, so no employer is indexable at two addresses.
- **The index holds only the 167 keys whose two forms differ**, so the common
  path is one indexed query touching no map. A 7,301-entry array behind every
  request would have been a quarter of a megabyte.
- **Collisions are refused, not resolved.** Three canonical slugs are claimed by
  two keys each, and all three pairs are one employer recorded twice
  ("perma-fix"/"perma fix", "daré bioscience"/"dare bioscience", one NHS trust
  filed with "&" and with "and"). Neither side is served under the shared URL
  and neither is published. The fix is a merge in employer identity. None is
  over the publishing threshold.
- Two of those pairs also SHADOW: one key's canonical slug is another key's
  legacy slug. Checked explicitly; they are the same two duplicate pairs, so the
  collision rule already covers them and no third employer is affected.

### The truncated key, and its cause

`company_key` used `\b(inc|llc|ltd|...|co|...)\b` to strip legal suffixes, and a
hyphen is a word boundary, so `\bco\b` matched the "co" inside "co-operative".
**CO-OPERATIVE GROUP LIMITED was stored as `-operative group`.** Six real
employers were mangled: also ASSOCIATED BANC-CORP (`associated banc-`),
CO-DIAGNOSTICS (`-diagnostics`), two more co-operatives, and Overlay Alpha
Co-GP. A lookaround now requires a whole space-delimited token; measured across
7,770 distinct stored names, the key changes for those six and nothing else.

**Rows already stored keep the mangled key.** `company_key` feeds
`content_hash`, so a new signal for one of those six will not dedupe against the
old rows until a correction pass rewrites them through `store.revise()`. That is
a queued writer job and was deliberately not done in the same commit.
## 2026-07-29 — backfills in bounded slices, and a scope guard that reads the filing

Two fixes for two things that were true all day: a backfill could hold the only
writer lock for six hours, and a page promising it publishes no layoffs was
publishing seven.

### The 350-minute lock hold, fixed by finishing

`backfill-gdelt-2026` took the `talent-collect` lock at 04:59 UTC, ran 350
minutes, hit its own `timeout-minutes: 350` and was **cancelled** — so its
commit step, guarded by `if: !cancelled()`, was **skipped**. Six hours of
collection existed only on a runner that was then deleted, and every correction
queued behind it waited the whole time.

Priority ordering and starvation reporting both landed earlier the same day and
neither could have helped. **Priority decides who goes next and cannot preempt
a running job**; saying the lock has been held for two hours does not hand it
back. The only thing that bounds a lock hold is a job that finishes.

So a backfill is now a **chain of short runs**. A run takes one slice, commits
it, and appends a ticket for the next slice to `data/writer_queue.json` **in
the same commit**; `drain-writers.yml` dispatches it when the group empties,
behind whatever short corrections arrived meanwhile (a `backfill-*` ticket
still carries `BACKFILL_PRIORITY`). Progress lives in a committed
`data/backfill_state.json`, so a run that dies loses at most its own slice.

**The cursor is the authority, not the dispatch inputs.** A ticket can wait
hours behind other work, so an input saying where to start would be a second,
staler source of truth. Dispatch the whole window; the cursor decides where a
run begins.

Slice sizes are measured and the measurement is written beside each constant:

| Workflow | Slice | Measured basis |
|---|---|---|
| `backfill-gdelt-2026` | 4 days | the 350-min run had not finished a month; 9 queries at 12s pacing plus the retry ladder is ~11 min/day |
| `backfill-2026` | 7 days | seven month-long runs took 137, 145, 159, 184, 185, 188, 215 min |
| `backfill-funding-2026` | 28 days | 12.7 min for a whole month (run 30377226199) |
| `backfill-funding-bulk` | 1 quarter | 6.8 min for two quarters (run 30413051586) |

A size from measurement is an estimate, so the promise is elsewhere: a
**50-minute wall clock** stops the run at the next window boundary. And
`timeout-minutes` drops 350 → 90 on all four, below `LONG_HOLD_MINUTES` (120),
so a sliced backfill can no longer reach the condition the drainer reports as
starvation. The 40-minute gap between budget and timeout is what makes the run
end *cleanly*, which is the whole difference: a cancelled run's commit step is
skipped.

**The lock is untouched.** Same group, same `cancel-in-progress: false`, all
four. Slicing changes how LONG the lock is held, never how MANY writers hold it.

Three guards, because a self-requeuing job is a loop:

- a slice whose cursor did not move is never requeued and the run goes red;
- the cursor is **monotonic**. `actions/checkout` pins a run's SHA at DISPATCH,
  so a run that waited behind the lock read a state file as old as its wait, and
  recording it unconditionally would rewind the chain. Same shape as the stale
  checkout that destroyed 311 rows, one file along;
- a chain past 200 slices stops itself, and a dry or fetch-only run advances
  nothing.

`backfill_slices.py record` runs **after** `git reset --hard origin/main` and
merges into whatever main holds, for the same reason the database is merged
rather than copied. Its exit code is carried past the push, so a bookkeeping
failure never costs the collected rows.

#### What running one taught us

The first live sliced run (30481065108, `backfill-funding-bulk 2026q1`) took its
slice correctly, walked the quarter, and then **died inside `publish.publish`**
because the publish guardrails were holding open findings. The ticket was
emitted after the publish call, so nothing was emitted: the cursor never moved,
the state file was never written, and the chain stopped having recorded nothing.
Only the database commit survived — precisely the asymmetry slicing exists to
remove.

Collecting and publishing are **separate gates**. Each script now catches
`PublishError`, emits its ticket anyway, and then goes red. The ticket carries
`halt`, which is deliberately not the same as failing: the slice's cursor and
totals are applied in full so the work is never redone, and only the **requeue**
is withheld — whatever blocked this slice blocks the next one, and a chain
requeueing into a wall produces one red run per slice and buries the first, real
one. `ops_status.py [2e]` shows it, because between slices there is nothing
running and "is it still going?" stopped being answerable by looking for a job.

### Seven layoff records on a page that says it collects none

Layoffs are read from the sibling tracker's API and never collected here. The
footer says so. Seven records were live anyway.

**The guard read the HEADLINE, and `sec_edgar` has no headline.** It stamps the
identical string `"<Company> 8-K filing (Item 5.02): officer or director
change"` onto every document it fetches, so the first arm spent every run
matching a fourteen-language layoff vocabulary against the collector's own
boilerplate. The second arm only fires when the model chose `displacement`. The
reduction language sat untouched in `raw_text`, which nothing read. Nothing
errored, nothing went red, and a guard **with tests** reported healthy
throughout — this day's theme again.

**Running the existing predicate over the body does not fix it**, and that is
worth knowing before someone tries. `workforce_reduction_term` lets an in-scope
subject appearing EARLIER win the race, and every Item 5.02 filing opens with
"appointed" or "resigned", so a reduction announced three paragraphs later is
suppressed every time.

So `prefilter.filing_reduction_plan` is body-shaped, and the question it answers
is **not "does this mention a cut" but "does this ANNOUNCE one"** — because
getting it wrong the other way rejects the pillar this product is largest in
(3,777 live sec_edgar leadership rows). It fires on:

- **Item 2.05** — "Costs Associated with Exit or Disposal Activities", the SEC's
  own code for this event. Decisive alone: a registrant does not file one for
  somebody else's layoff.
- or **any two of {a reduction term, a plan, a stated scale}** within a
  paragraph. Two, because each alone is ambiguous: a reduction term alone is the
  passing mention ("she led finance through the 2024 layoffs at her former
  employer"), a plan alone is usually a compensation plan, a scale alone is a
  share count.

**Severance, termination benefits and one-time charges are deliberately not
corroborators.** They are the standard furniture of an Item 5.02 officer
departure, and admitting them would turn this into a rule that rejects
leadership changes.

**Measured over the whole corpus rather than asserted:** 3,784 filings re-read,
0 unreadable, 6 announcing a reduction — **0.16%**.

`correct_layoff_scope.py` is the backward half, and it **re-fetches**, because
`raw_text` is never stored: Atlassian's stored summary says "elimination of
certain roles", which the reduction vocabulary does not match, so judging these
rows on the database would reproduce the original defect one level up.
Withdrawal goes through `retract_remote` + `retract_local` like every other
correction here — nothing deleted, nothing edited in place.

It found **three the open list did not have**:

| | |
|---|---|
| Elastic N.V. | "expects to reduce its workforce by approximately 7%", $22-25m of severance and termination benefits |
| Commerce.com (BigCommerce) | a plan "to realign the Company's current workforce", $13.9m primarily severance |
| Verizon | "despedirá a 3,000 empleados" — from google_news, and **the very row the scope guard was written for**. The guard landed; nobody withdrew the row it was written about. |

Elastic and Commerce.com are the judgement call worth inheriting. Both filings
carry Item 2.05 **and** a real Item 5.02 event (a Chief Product Officer leaving,
a CFO taking on COO duties), and the model read only the 5.02 — both rows said
nothing more than "reported a change in its officer or director". Withdrawing
them loses the leadership event too. That is the right trade at this size: 6 of
3,784 live filings announce a reduction and 2 of those carry a leadership event,
so the boundary costs **0.05% of the leadership pillar** to keep a promise the
page makes in writing.
## 2026-07-29 — the cost levers: reading everything relevant on the same budget

The candidate cap raise (150 → 1500) made every prefilter survivor visible to
the pipeline; this session built the levers that keep that affordable. Naively
read-through-ing ~1,000 survivors/run is ~$77/month; the ceiling stays where
it was because most of what survives the free filter no longer needs a model.

**Lever 1 — deterministic teaser extraction (`pipeline/cheap_extract.py`).**
A funding or hiring headline that states every field IS the record. Regexes
close it: employer before a completed-raise verb, amount with its currency
verbatim (non-USD keeps its currency and a NULL USD integer, per the existing
no-FX rule), stage only where the text ties it to THIS round, place only from
a `-based`/possessive prefix that normalises. Everything else DECLINES to the
paid path — precision over recall, because a wrong $0 extraction is worse
than a $0.0013 read. Output goes through the same `validate -> store ->
publish` path with zero exemptions; confidence stays `reported` (a regex does
not make the source more credible), and the row carries `notes =
cheap_extract.EVIDENCE_NOTE` so a reader can see no model read it.

Measured on two real populations (the 2026-07-29 overnight 575-feed harvest
and a fresh live fetch six hours later): 970 and 1,039 prefilter survivors,
28 and 22 closed deterministically, 31 distinct records, **31/31 correct on a
full hand-check** — after four tightenings the sweep itself forced:
- "Kuwait raises $6 billion in three-tranche bond sale" → a name that IS a
  country or city declines, and bond/tranche/fund-vehicle wording declines.
- "Dutch-US MedTech Xeltis" stored the descriptor into the name → hyphen-
  embedded nationalities and sector-tech compounds (medtech, proptech, ...)
  poison the span.
- Title-cased headlines blind the capitalisation heuristic ("Building
  Materials Quick Commerce Startup Fixxly Raises...") → in a title-case
  headline only a single-token name is trusted.
- "a fivefold step-up from its Series B" stamped the PREVIOUS round's stage
  onto a $570m raise → a teaser stage only counts beside the money.

**Lever 2 — story clustering (`run_collect.cluster_stories`) + known rounds.**
The same round rewritten by several outlets survives URL and syndicated-title
dedup; now survivors clustering on the stated (employer, amount) get ONE
read. Two tiers: the strict key (validly named employer) marks its set-aside
copies seen; the loose key (final token before the verb — the four "…startup
Fixxly raises $5.5 Mn" rewrites) holds copies back this run only, so a false
merge can only defer a read, never lose a story. Cross-run,
`dedupe.funding_event_duplicate` matches a stored round by (company_key,
amount) BEFORE any model call — `fuzzy_duplicate` caught these only after the
read was already bought. Measured: 4-5 rewrites clustered away and 1 known
round per population. Small today; insurance for the story every feed carries.

**Lever 3 — read size.** Largely already bounded: news candidates are
headline + teaser (avg 436 chars, p95 599, max 1,250 on the live population —
zero ever reached the cap) and only SEC filing bodies truncate. The magic
numbers became `classify.GATE_CHARS` / `classify.FULL_READ_CHARS` with the
reasoning attached, and every run now logs avg chars sent vs fetched.

**Lever 4 — prompt caching: shape kept, no saving claimable today.** The
read-through's prefix (MINI_SYSTEM + SCHEMA_HINT, ~2,668 of ~3,100 input
tokens) is byte-stable with the item text last — exactly the shape DeepSeek's
automatic prefix cache wants, and OpenRouter passes that through unconfigured
at 0.1x input price. But the providers actually serving
`deepseek/deepseek-chat` today (StreamLake, DeepInfra, Novita — checked via
OpenRouter's endpoints API) advertise **no cache-read pricing**, so there is
no cache to hit on the current slug. `deepseek-chat-v3.1` providers do
(~0.5x), so the already-planned model switch would earn it for free. Guards
added anyway: a test pins SCHEMA_HINT at the head of the user message
(anything inserted before it silently forfeits the prefix), and every call
now records OpenRouter usage accounting (prompt/cached/completion tokens and
cost) into `classify.STATS`, printed per run — if routing ever lands on a
caching provider the run report says so, measured rather than estimated.

**What did NOT change:** `READTHROUGH_CAP` stays 60/run (raising it is the
owner's decision), spend.py still runs first and still hard-stops, the gate
is untouched. Worst-case LLM spend at the new defaults: gate ~1,050 × 2/day
× 30 × $0.00003 ≈ $1.9/mo, reads 60 × 2/day × 30 × $0.00128 ≈ $4.6/mo. The
~25 deterministic closes per sweep are read slots handed back to stories that
genuinely need a model.

---

## 2026-07-29 — company profile pages, and the threshold that decides which exist

`/talent-intelligence-tracker/company/{slug}/`. Profiles already rendered for
every employer we held a row for. The work was deciding which of them deserve
a URL, and making one decision serve both the page and the sitemap.

### The threshold, and why it counts documents

Measured against the live `/query` endpoint (15,630 current rows, 7,408
employers by display name; 7,301 by `company_key`, which is what the page groups
on):

| rows per employer | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| employers | 4,840 | 751 | 376 | 503 | 393 | 135 | 90 | 137 | 183 |

| documents per employer | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| employers | 5,317 | 1,215 | 274 | 70 | 60 | 66 | 87 | 137 | 182 |

Three readings, in the order they change the answer:

1. **Rows are the wrong unit.** 235 employers carry four rows behind ONE
   document, because `sec_execcomp` splits a single pay-versus-performance table
   into a row per fiscal year. A row count measures how finely we parse a
   filing, not how much we know.
2. **One document restated is not a page.** 72% of employers sit behind a single
   document, and a reader is better served by that document.
3. **Three documents from one feed is one thing said three times.** The UK pay
   gap rows carry an *identical* read-through sentence with a different
   percentage, one per reporting year. 638 employers would clear a plain
   three-document bar on that alone, which is the template-plus-a-number shape
   that gets a whole set filtered.

So: **3 documents, and either 2 kinds of evidence or 5 documents.** 713 of 7,301
employers, 9.8%. 186 qualify on breadth, 527 on a multi-year series.

Below the bar the page still renders and stays linked from the dashboard table,
but goes `noindex, follow` and is absent from the sitemap. Not a 404: the
dashboard links there and a recruiter following that link should get the page.

**One predicate does both.** `tit_company_meets_threshold()` answers it for the
page; `tit_company_gate_having()` builds the sitemap's `HAVING` clause from the
same three constants, and `tests/test_company_page.py` fails on a threshold
typed a second time. The sibling shipped noindex URLs inside its own sitemap and
heard about it from Search Console; that is not prevented by care.

Everything is computed on render from `wp_tit_signals`. No generated pages, no
regeneration step, and the sitemap is a query rather than a file.

### Three defects found by curling it, not by reading it

- **Two contradictory robots tags** (1.45.0). A thin profile served
  `noindex, follow` from us and `follow, index` from the SEO plugin. The first
  fix named Yoast's filter and did nothing: the tag is SEOPress's. Naming a
  plugin pins us to that plugin and to its hook names. The head is buffered and
  every robots tag replaced with exactly one of ours, the same trick
  `tit_render_header()` uses for `<title>`. A test now refuses any SEO plugin's
  name in the file. The `X-Robots-Tag` header goes out before any buffering, so
  a buffer that never closes cannot leave a thin profile indexable.
- **The sitemap 301ed** to `.../company-sitemap.xml/`, because WordPress
  trailing-slashes anything it does not recognise as a file. `redirect_canonical`
  is off for that one query var.
- **`%26` kills a company URL.** Found by fetching eight random URLs from our own
  sitemap: one 404. `rawurlencode()` writes `&` as `%26`, which does not survive
  the rewrite. `/company/b%26q/` is 404, `/company/b&q/` is 200, and `&` is a
  legal sub-delim in a path segment, so it is left literal. **144 of 7,301
  employer keys carry an ampersand** (Ernst & Young, Holland & Barrett, Mitchells
  & Butlers, most UK NHS trusts) and every one of their dashboard links had been
  dead since profiles shipped. A percent-encoded non-ASCII byte does not survive
  either, and neither does the literal character: 18 keys, now not indexable and
  not in the sitemap, because a sitemap full of 404s is what gets a set
  distrusted. Fixing those properly needs a stored ASCII slug on `company_key`,
  which is a pipeline change and a migration.

### Known and not fixed: sitemap discovery is one manual step

`/blog/robots.txt` is a physical file, so Apache serves it from disk and the
`robots_txt` filter never runs (gotcha 5). The robots.txt a crawler actually
reads for this host is `https://asktherecruiter.com/robots.txt`, which belongs
to the separate root app. Neither is reachable from this repo. Discovery today
is the internal links; **submit the sitemap in Search Console, or add its URL to
the root robots.txt.** The filter is left registered and is not counted as
working.

### Verified live, by curl, on 1.45.3

712 URLs in the sitemap, `application/xml`, no redirect, XML parses. 20 sampled
entries (including 6 with an ampersand) all 200 with exactly one
`index, follow`. `oracle` and `bloomberg` (3 documents, 1 kind) 200 with
`X-Robots-Tag: noindex, follow` and absent from the sitemap. Dashboard,
`/sources/`, `/recall/`, `/corrections/` and `/aggregate` all unchanged.
**The visual result is unverified: this session had no browser.**

---

## 2026-07-29 — pre-publish guardrails

Built because the $86bn Form D overstatement was never a thing nobody could
have checked. It was a thing nobody was going to remember to check. Four
arithmetic assertions now run on the write path, in `pipeline/guardrails.py`,
called from `pipeline/publish.py` before a single row is sent. No model, no
network, no cost.

**Flag, never drop. Fail loud.** Findings land in a `publish_guardrails` ledger
and block publishing until a person accepts or rejects each one. Accepting is
remembered, so ChangXin Memory's genuine $8.6bn raise is answered once and never
blocks again. Nothing is auto-binned: silent auto-correction would be a
different invisible defect, which is the same argument that keeps `link_check`
from retracting a row over an HTTP code.

### 1. Implausible single-row amounts

The threshold is **derived, never typed**: the value whose expected count under
a robust log-normal fit of the stored amounts is 0.1 rows. Centre is the median
of log10, scale is 1.4826 x MAD, and z comes from n. On 3,057 stored amounts
that is **$1,799,597,726**, and it flags 5 rows.

Median and MAD were chosen by **measurement, not by preference**. Replaying the
998 retracted vehicles back in:

| estimator | clean | contaminated | vehicles caught |
|---|---|---|---|
| median / MAD | $1.80bn | **$1.35bn** | 14, worth $68.4bn |
| mean / sd | $2.32bn | **$2.42bn** | 11, worth $62.5bn |

The robust pair tightens as bad rows arrive; the mean-based one relaxes.

**The limit, stated because it decides what the other three checks are for.**
The retracted vehicles were not a distinguishable population by amount: log10
median 6.641 against the clean corpus's 6.737. Only the individual extremes
stood out. This check catches the largest members of a bad class, never the
class, and a contaminant forming a large tightly-clustered mode two decades up
would lift any fitted threshold, robust or not.

### 2. Period totals must reconcile

Three invariants, from the page that carried "this quarter 268" against "2026 so
far 6,018" beside a headline of 14,019.

- **Ordering, derived from the start dates rather than assumed to nest.** Every
  cell counts rows on or after its own start, so an earlier start can never hold
  less. Asserting week-inside-month would have been wrong: "this week" reaches
  six days back and crosses the month boundary for roughly half of every month.
  Pinned by a test.
- Year-to-date never exceeds all-time.
- **A subset never exceeds "All updates"** in the same column. This is the shape
  of the original defect: 998 vehicles counted as funding under a clause scoped
  differently from the one counting updates.

### 3. The printed date span must match the data

From "Everything here spans 3,318 days, 28 Jun to 28 Jul 2026" — nine years of
days against thirty days of dates, because the count was measured over the whole
table while the bounds came from the recent window. The page still holds **two
legitimate scopes at once** (`lo_all/hi_all` drive the date inputs, `lo/hi` the
sentence under the tiles), so the check asserts both, that each day count comes
from its own bounds, that the view sits inside the whole, and that the span
reaches every period a tile reports a nonzero count for. `guardrails.py --live`
adds the only assertion that can see what a reader reads: the `span` object from
a live `/aggregate` must match one of the two recomputed scopes and nothing else.

### 4. Vehicle and SPV names on funding rows

Runs on **every** funding row, not only Form D ones: the collector's filter
governs what Form D collects, this governs what reaches a headline figure
whatever route it took. It reuses `sec_form_d.EXCLUDED_NAME_PATTERNS` rather
than restating it, and adds the publish-time set: street addresses, numbered
accounts, separate accounts, and **the abbreviations** — `GIC`, `GICs`, `BOLI`,
`COLI`, funding agreement, institutional life.

**Every pattern was measured** against the 998 real retracted rows (recoverable
from `signals` where `is_current = 0` and the retraction note) and against the
3,057 live funding rows. A pattern earns its place only if its yield on the real
defect beats its cost in live review. Two were tested and **rejected**, recorded
in the source so nobody re-adds them from first principles:

| candidate | retracted | live cost | verdict |
|---|---|---|---|
| `series \d+$` | 2 rows, $0.00bn | 16 rows, all one employer | rejected |
| `\d{1,2}\s*(llc\|lp)$` | 38 rows, $0.23bn | 24 rows incl. HawkEye 360, Inc. | rejected |

Measured recall of what shipped: **229 of 998 rows, but $71.3bn of the $85.6bn**,
because the vehicles are exactly the large ones. On today's live rows it flags 3.

**A finding worth reading even though it is empty.** The GIC/BOLI/COLI
abbreviations match nothing, and checking every stored text column of the
retracted rows says why: that wording lived only in the SEC dataset's
`DESCRIPTIONOFOTHERTYPE`, which was never stored. So the abbreviation's real
home is `sec_form_d_bulk.NOT_A_CAPITAL_RAISE`, where the description is read.
It is in the publish-time set as well because it costs nothing and the next
vehicle carrying it in its NAME should not need a second incident.

### Wiring, and the two things that would have made it a decoration

- **`merge_db.py` merges the ledger, and a human's answer beats a later
  automatic write.** Every other table there resolves a collision with "later
  wins", which is actively wrong for a review queue: the later write is usually
  a run re-firing the same finding, and the earlier one may be the owner's
  acceptance. Without this, a run in flight would silently reopen an accepted
  row. An unreviewed disagreement resolves to `open`, because this table decides
  whether a figure goes out.
- **`ops_status.py [2d]` evaluates live when the ledger is empty** instead of
  printing "nothing flagged". An empty ledger means nobody has looked, and the
  tool every session is told to trust must not confuse the two. It also says so
  when the interpreter cannot import the collector's patterns, so a narrower
  check never prints a smaller number silently.

`health_digest.py` puts a quarantine ahead of a stale collector in the subject
line, with its own paste-ready instruction. A stale scraper costs coverage; a
quarantined row that nobody has judged is an unchecked figure one decision away
from going out.

### The failure mode was wrong, and production said so within the hour

The first build HALTED the run on any open finding. Both of the first two
production runs failed on the same eight:

```
collect               -> 8 open guardrail finding(s). Nothing was published.
backfill-funding-bulk -> 8 open guardrail finding(s). Nothing was published.
```

Both were carrying dozens of perfectly good records. In steady state that means
**a single unreviewed row blocks every row**, and since X.AI's $16.6bn is a real
raise, the first genuine billion-dollar round of any week halts collection until
a human answers a prompt. The owner needs this running for days unattended, so
that is a design error and not a bug in the checks.

**Now it quarantines.** The flagged row is dropped from the batch; everything
else publishes. It is never marked published, so it reaches no headline figure
AND it is re-offered on every run, which means accepting the finding releases it
with no requeue and no replay path to remember. `enrich_published()` filters the
same set, because `funding_amount_usd` travels that way and would otherwise
reach the money total by the back door while `publish()` was carefully not
sending it by the front.

What did NOT change, and must not: an unreviewed row stays out of the data and
out of every aggregate. $86bn is the reason.

### The exit status, and why it is not simply 0 or 1

| state | run | why |
|---|---|---|
| quarantine only | **exit 0** | the guard SUCCEEDED. The suspect row is out of every figure. Red here would mean "the machine noticed", and a permanently red `drain-writers` already taught this project what that does to attention. |
| finding past its window | publish the clean rows, **then** exit non-zero | red should mean a human neglected it, which requires the human to have been told. `health-digest.yml` runs Mondays, so the email is the moment of telling. |
| aggregate finding | **halts immediately** | a period total or a date span that does not add up names no row, and there is no clean subset of a wrong total. |

Two grace windows, both derived from the cadence rather than picked:

- **192h** for a row that never reached the site: one full digest cycle plus a
  day. Before the first email fires, red would blame somebody who has not been
  asked, and nothing is wrong in public. After a whole cycle of silence, it is a
  choice.
- **72h** for a row **already on the site**. Different in kind: that figure is
  wrong in public right now and quarantine cannot pull it back, only a human
  retraction can. The owner's own ceiling is "days" unattended, so three days is
  the longest ordinary absence.

The escalation is raised **after** the send, never before. "One suspect row does
not take the batch down with it" has to hold on the day the run goes red too, so
the clean rows are already sent, marked published and committed by the time the
exception is thrown. Red there means "nobody answered", never "work was lost".
The countdown to red prints on every run, so the day it turns is never a
surprise.

### Being impossible to miss

`publish.py` prints the quarantine with `::warning::` / `::error::` GitHub
annotations, so it lands on the run summary page rather than in a log nobody
opens. Printed from THERE rather than from `run_collect` so six backfill
scripts, both corrections and the enrich job get it for free; not one of them
would have grown its own version. `ops_status.py [2d]` and the weekly digest
both separate **HELD** from **ALREADY LIVE** and print the countdown, because
those are different problems wearing the same word.

**Verified on a copy of the live database**, 46 rows offered to publish: 6
quarantined, 40 published, exit 0, and all six still unpublished afterwards.

---

## 2026-07-29 — the day everything that looked healthy turned out not to be

One theme ran through every defect found this day, and it is worth stating once
at the top because it predicts where the next one will be:

> **Every serious failure was something that looked healthy while being broken.**
> Not one was a crash, an error, or a red build. A status-code check, a green
> workflow run, a passing test and a confident ops tool each reported success
> while the thing underneath was dead, empty, unshipped or lost.

The engineering response is a rule, now applied throughout: **health must be
proven by output, never inferred from the absence of an error.** A feed is
healthy only if items arrived and are recent. A run is healthy only if it
executed a step. A deploy is healthy only if the live page changed. A correction
is healthy only if the figures moved.

### Link rot: the failure that is invisible by construction

Added the same day and in the same spirit as everything above, because it is the
purest case of the theme. A source link that dies renders identically to one
that works. Nothing errors, no run goes red, no test fails, and the claim it
supported quietly stops being sourced. This repo had no defence at all while the
sibling had two, and it matters more here: the promise is that every update
links to the filing behind it, across 575 publisher feeds in 139 countries.

`link_check.py` records status, final URL and date per URL; `archive_sources.py`
gives each cited document a Wayback fallback via the sibling's two-pass design;
`source_links` holds both, keyed on the URL because 15,631 signals share 12,890
of them. Both DORMANT, both free (no model call).

Three decisions worth inheriting:

- **A dead link never edits a row.** The only write to `signals` is
  `archive_url`. An automatic reaction to an HTTP code would let a publisher's
  bad afternoon delete evidence, so the state is recorded and surfaced and a
  human decides. This is the same instinct as `store.revise()`: the record of
  what a source said is not the place to put HTTP weather.
- **Status codes cannot catch the dangerous case.** `botswanaguardian.co.bw`
  became a betting site whose feed verified perfectly green. The only signal is
  that the bytes came from a domain other than the one we stored, so the
  checker reuses the collector's `registrable_domain()` drift guard. The first
  real sweep then found `hln.be` answering 200 from `myprivacy.dpgmedia.be` — a
  consent gate, not a takeover, distinguished because a gate carries the article
  URL back in its callback and a squatter has no reason to name the document it
  replaced. Without that distinction `drifted` would have degraded into a list
  of European cookie banners and the state that matters would be ignored.
- **Measure before arming.** Dry runs over 291 real stored URLs: 0% rot, and
  Wayback already holds 29% of publisher URLs against 3% of SEC/GOV.UK ones.
  That gap set the nightly default to the publisher tail, because EDGAR keeps
  its own filings and a 40-capture budget spent on 12,700 index pages would take
  most of a year to preserve what a government already preserves. 0% is a
  baseline on a corpus weeks old, not a clean bill of health.

An off-the-shelf WordPress broken-link-checker plugin was rejected explicitly
and the reason is written where someone might be tempted: they crawl post
content, our links live in `wp_tit_signals`, and one would have reported a green
badge over an entirely unchecked corpus. That is this day's theme wearing a
plugin.

### Plugin versions shipped

| Ver | What |
|---|---|
| 1.43.0 | **An archived copy beside every source link** (`shortcodes.php` and `dashboard.js` render it identically, `archive_url` added to `tit_enrichable_columns()`). A SECOND link, never a replacement: the publisher's own copy is the citation. **NOT DEPLOYED** at the time of writing; nothing carries an `archive_url` yet because every archiving run so far was a dry run. |
| 1.42.3 | **Sources page listed 5 collectors while 9 were running.** UK gender pay gap, SEC executive compensation and the entire 575-feed national press collector were live and unlisted — two of them among the largest contributors of rows. The guard missed it because `test_live_sources_are_only_the_ones_with_collectors` asserted a **hardcoded set of five names**, so it caught a source listed *without* a collector and was blind to a collector running *without* a source. The defect went the blind direction. Now derives the expected set from `run_collect.SOURCES` via a new `COLLECTOR_BY_SOURCE_NAME` map and fails both ways, with `tripwire_chase` excluded by name as deliberately dormant. |
| 1.42.2 | **Corrections page flipped to past tense** after the Form D correction actually ran. Three-column table (Before / We projected / Measured now) rather than silently replacing the projection with the actual — a corrections page that quietly revises its own numbers is doing the thing it exists to prevent. Tests now fail the build in **both** directions: past-tense wording on a pending entry, and pending wording on an applied one. |
| 1.41.1 | **"More filters" disclosure removed for real**, plus `Team Or Function` → `Team or Function` (naive title-casing had capitalised a conjunction). See incident below. |
| 1.41.0 | Recall page: scheduled weekly runs, trajectory chart, automated gold-set retirement, `POST /talent/v1/recall`. |
| 1.40.0 | Recall measurement published. |
| 1.39.2 | Corrections page rewritten to disclose the Form D defect as *identified and scheduled* rather than applied. |
| 1.37.1 | **Clipping regression fixed** — see incident below. |
| 1.37.0 | Owner's UI punch list: `2026 so far` → `2026 YTD`, always-zero Today column dropped, self-contradicting date-span line fixed, redundant region heading deleted, duplicate Headcount label removed, notable-vs-everything control rewritten definition-first, computed single-collector caveat on the country chart. Also the sticky-bar attempt that caused 1.37.1. |

---

## Incident log

### The deploy that shipped nothing (2026-07-29)

`deploy-plugin.yml` **defaults to `dry_run=true`**. A plain
`gh workflow run deploy-plugin.yml` produces a run that passes every step, lints
the PHP, confirms the version bump, reports **success** — and uploads zero
bytes. The "Upload over FTPS" step is simply skipped.

This was hit **after** the identical trap had been documented for
`correct-form-d.yml` and relayed in writing an hour earlier. Reading about a
trap does not prevent walking into it; only the verification step does.

**Guard:** always dispatch with `-f dry_run=false`, and **always curl the live
page** afterwards rather than trusting the green tick. The only reason this was
caught is that the live `ver=` was checked and still read the old version.

### Fifteen data-writing runs silently destroyed (2026-07-29)

Every workflow writing `data/talent_intel.db` shares the `talent-collect`
concurrency group with `cancel-in-progress: false`. That lock is **correct** and
must stay — it is what stops two writers doing reset-hard-then-copy-our-file-back
and destroying each other's rows.

But **GitHub keeps only ONE pending run per concurrency group.** With one run
executing and one waiting, dispatching a third silently *replaces* the waiting
one. The displaced run ends `cancelled`, having created **zero jobs**, with no
error and no annotation anywhere.

Measured: 15 runs lost — `correct-form-d`, `correct-sec-pillar` ×2, `enrich` ×3,
`recall`, `collect`, `collect national press`, and five backfills. Every one had
been reported to the owner as "queued".

Three things made it worse than it first appeared:
1. **Cron evicts too.** One `enrich` was displaced by the *scheduled* `collect`
   run created one second earlier. This was never only an agent-parallelism bug.
2. **`enrich.yml` was invisible to the guard.** `test_every_database_writer_shares_one_lock`
   found writers by searching for the string `talent_intel.db`; `enrich` writes
   through `pipeline.publish` and never names the file. It held the lock and was
   evicted three times while the test reported all-clear.
3. **Re-dispatching would have failed silently.** Both correction workflows
   default to `dry_run=true`, so a naive replay produces a green run that changes
   nothing.

**Fix — an invariant, not a retry.** `drain-writers.yml` dispatches the next
ticket **only into an empty group**, which is the one condition under which
nothing can be evicted. Work waits in a committed `data/writer_queue.json`
instead of GitHub's single lossy slot. The drainer is deliberately **not** in
`talent-collect` (a drainer queued behind the lock could never drain it), and it
goes red on any writer run that ended cancelled with zero jobs. **No writer
workflow was modified** — the lock cannot have been weakened by a change that
never touched it.

**Queue a writer, never dispatch one directly:**
```
gh workflow run drain-writers.yml -f enqueue=<workflow>.yml \
  -f inputs_json='{"dry_run":"false"}' -f reason='why'
```

**Residual gap:** direct dispatches — including cron — can still be evicted. The
drainer detects and reports them loudly; it cannot prevent them.

### A 350-minute backfill starved every correction (2026-07-29)

`backfill-gdelt-2026` held the writer lock from 04:59 to 10:49 UTC, hit its own
timeout, was cancelled, and its "Commit the database" step was **skipped** — so
roughly six hours of collection was lost *and* it blocked the corrections for
that entire window.

Priority ordering now puts short corrections ahead of backfills, and a lock held
past two hours with work waiting is reported as starvation. **Priority cannot
preempt a running job**, so the real fix is backfills that run in bounded slices
and requeue themselves. Not yet built — see HANDOVER "Open".

### ops_status.py printed a confident false all-clear (2026-07-29)

`[2b] WRITER QUEUE` reported *"Nothing queued, nothing lost"* while `origin/main`
recorded **15 orphans and a waiting ticket**. The queue lives in a committed
file, the checkout was two commits behind, so it read a file written before any
eviction happened.

CLAUDE.md tells every session to run `ops_status.py` **first**, which makes a
false all-clear the most expensive thing this tool can say — the eviction bug
wearing the reporting tool as a hat. An absent queue file now means **"unknown"**
rather than "nothing" whenever the checkout is behind, and a present one is
labelled stale. It deliberately does **not** fetch: `ops_status` is read-only and
must work offline and in an egress-blocked session.

### The sticky fix that guillotined the page on mobile (1.37.0 → 1.37.1)

A rule of `:has(#tit-dashboard) { overflow-x: clip }` with no element qualifier
matched **every ancestor**, including the 279px `.entry-content`. The dashboard
breaks out of that with a negative margin to reach full width, and a full-bleed
breakout inside a clipping ancestor is guillotined at that ancestor's edges.
`clip` does not scroll, so ~48px was cut off **each side** and unrecoverable. The
hero headline rendered as *"now who's hiring before the / b ad appears"*.

**The check that would have caught it reported healthy.** `scrollWidth === innerWidth`
passes here — because `clip` achieves that by destroying the content. Do not use
that check to validate overflow containment.

Now `html`/`body` get `overflow-x: clip; overflow-y: visible` (the only axis
combination that clips sideways bleed without creating a scroll container, since
`visible` degrades to `auto` when the other axis is `hidden`), and the narrow
wrappers are forced back to `visible`.

### Form D published property vehicles as startup funding

994 → **998** rows: single-asset property SPVs, insurance separate accounts and
synthetic GICs, filed on the same Form D real startups use, published with a
**hardcoded `"hiring"` direction** and an invented read-through the filing never
stated. They inflated the headline by roughly **$86bn**.

A second pass found the first fix incomplete: the exclusion was written from the
spelled-out phrase *"guaranteed investment contract"* and missed the trade's
abbreviation, leaving four **GIC/BOLI/COLI** rows at $12.4bn as the largest
remaining amounts. **Lesson: an exclusion written from a spelled-out phrase will
miss the abbreviation the industry actually uses.**

**Applied 2026-07-29, verified live:** money raised **$200.3bn → $124B**, funding
rows 4,072 → 3,081, employers 6,745 → 5,463. Came in ~$10bn above the $114.1bn
projection — and the cause was checked rather than assumed: **$9.25bn was NEW
data** (ten national-press records captured between projection and correction,
including a single $8.6bn ChangXin Memory raise), not rows the correction missed.
Only $0.9bn was local-versus-live divergence.

### 8-K Item 5.02 filings filed as pay events

**573** rows (not the 548 first counted — 25 more carry the same boilerplate
headline with the `(Item 5.02)` parenthetical dropped, so a substring search
missed them). Held in the database but invisible to anyone browsing leadership
moves.

The forward fix is deliberately **narrow**: it fires only while the row still
carries the collector's own officer-change headline. A blanket "sec_edgar means
leadership change" rule would have misfiled 20 genuine comp and M&A filings
(Masimo/Danaher, Bakkt, Littelfuse equity grants) — the collector stamps the same
generic headline on **every** document it fetches, so where the model replaced it,
it had read something specific and that judgement is kept.

Three rows were withdrawn explicitly because the correction turns them into
duplicates that `publish()` **counts without naming and marks published** — they
would have been withdrawn from the site, replaced by nothing, and logged as
success.

---

## Sources: what was verified, and the traps found

**565 feeds verified live across 137 countries**, every one fetched and parsed
rather than assumed. Americas 116/37 territories · Asia-Pacific 126/29 ·
Europe 217/38 · Middle East & Africa 106/33. Zero aggregator hosts.

**Israel was the acceptance test.** The owner found four missed rounds by asking
Gemini (Glow, Plantopia, Harmony, Enigma). Globes' English node carried Glow and
Plantopia; Geektime carried Harmony and Enigma. **CTech publishes no feed at all**
— its advertised `rss` endpoint 404s, its homepage declares no feed, and its HTML
contains no `rss` string. It was never going to work; it only looked like it
should.

**Those four rounds are not recoverable through feeds.** RSS serves a rolling
window with no archive — Globes reaches back 5 days, Geektime 3, Times of Israel 1.
Feeds fix **forward** recall only; historical recovery needs the GDELT archive
path, which takes explicit start/end dates.

### Feed traps — each produces a source that looks wired and delivers nothing

| Trap | Example |
|---|---|
| **200 OK, zero items** | IT World Canada returns well-formed RSS with only a `lastBuildDate`. Passes a status check forever. |
| **200 OK, years stale** | Sigmalive (2024-09), Moneycontrol (2024-04), NoCamels, MENAbytes, Disrupt Africa — three of these were *already in* the catalogue. |
| **Domain hijack** | `botswanaguardian.co.bw` now redirects to a **betting site** whose feed verifies perfectly green. We would have cited a gambling operator as a Botswana news source. |
| **Malformed XML** | Six feeds die under strict `ElementTree` — Times of Oman, Daily News Egypt, African Manager, Sika Finance, Condia, New Era. Oman looked sourceless while having a working publisher. |
| **Leading junk** | IO+ serves **two XML declarations** back to back; a strict parse dies at byte 38. The existing trim only handled trailing junk. |
| **Header-dependent** | Techpoint Africa and Arab News 403 a bare RSS `Accept` and 200 a browser one; four TownNews feeds do the **exact reverse**. Neither header set works globally. |
| **Relative links** | B2B Cambodia emits relative slugs, so source URLs break entirely. |
| **Non-standard dates** | KED Global uses `dc:publishDate`, Digital Business KZ uses `news:publication_date`; four feeds carry no item date at all. |
| **Oversized** | TechNode's feed is 11.8 MB and truncates mid-record under any sane read cap. |

**26 of 65 pre-existing feeds were already dead.** A prior audit claiming "12 of
15 verified" did not hold.

**25 feeds are disallowed by their publisher's robots.txt** and were withdrawn,
including three predating the collector. Enforcement is in code: robots fetched
once per host per run, cached, fails **open** on a missing file (no robots.txt is
the standard "no restriction") and **closed** only on an explicit `Disallow`.
SmartRecruiters was dropped on the same basis, costing 5 boards including Bosch's
4,747 postings — their API answers us `200` anyway, which is exactly why the
publisher's stated terms decide and not the server's behaviour.

---

## Recall: the number that makes this citable

**8 of 89 events held — 9.0%. Outside the US, 1 of 55. 27 of 29 countries scored
zero.** Published at `/talent-intelligence-tracker/recall/`.

The gold set was built by eight independent research passes **forbidden from
consulting our own database**, sealed before matching, every URL liveness-checked,
and **nothing dropped after assembly** — dropping items post-hoc is what makes a
recall number meaningless.

Set *retirement* is automated (35 days, or three measurements converging). Set
*assembly* is deliberately **not**: a generator would drift toward finding exactly
what we already collect, raising the number for the worst possible reason. What
is automated is the guard — `REQUIRED_SHAPE` rejects any future set that is too
small, too US, too large-event, or built from one document type.

Two defects it surfaced that eyeballing never would: an 8-K classified as a pay
event (held but unbrowsable), and `identity.enrich()` **which had never run in
production** — it is a no-op unless the caller passes a connection, and all five
callers omitted it.

---

## Language handling

Three languages were missing from the free regex prefilter — **Hebrew, Czech,
Danish** — which silently discarded items from wired feeds before they ever
reached the classifier.

- **Hebrew needed its own word boundary.** Clitics (*and*, *the*, *in*, *to*)
  are single letters glued to the next word and count as word characters, so
  `\bגיוס\b` matches only the bare noun and misses most real headlines. But loose
  substring matching fails oppositely: *salary* sits inside *a rental*,
  *employee* inside *the fact*.
- **`פיטר` is deliberately excluded** from the reduction vocabulary — it is also
  how *"Peter"* is spelled. A reduction verdict is a hard drop, so including it
  would have silently discarded every funding story mentioning a Peter.
- **Czech `investice` produced 9 false positives in 15.** English answers this by
  gating on *"funding round"* and not *"investment"*; Czech and Danish now do the
  same.
- A regex bug meant any alternative ending in *million* could never match, so
  real Danish funding headlines read as clean misses.

Measured keep rates after: 19% / 11% / 16%, the band the English gate already sits in.

---

## robots.txt: the file that breaks without breaking

Two sitemap lines had to reach `robots.txt`. It is served from disk by Apache
before WordPress runs, so no plugin, filter or REST route can add them — it is an
upload. And it is the `.htaccess` danger class one layer out: a truncated
`robots.txt` still answers 200, the site renders identically, nothing goes red,
and the domain quietly stops being crawled. The first symptom is a traffic graph
three weeks later.

So `robots_sitemaps.py` reuses the shape `includes/htaccess.php` already proved
on this host — keep the old bytes, write, probe the live URL, restore on any
doubt — with two additions that file does not need. The probe is **cache-busted**,
because Cloudflare will serve the pre-write copy back and make a failed write
look like a success. And the probe **retries a 5xx**, because this host 500s at
random under load and a rollback triggered by somebody else's bad minute is an
outage we caused.

**The remote path is never guessed.** An FTP account here is chrooted, so a path
from the control panel is not what the session sees, and writing to the wrong
`robots.txt` is unrecoverable in the only sense that counts: we would not know.
The file is fetched over HTTP first, then a candidate remote path is accepted
only if its bytes are **identical to what that URL just served**. No match, no
write. The root target additionally refuses any path containing `/blog/`, because
two copies holding identical bytes would otherwise let the root target adopt the
blog file and report two successes for one write.

It is a separate workflow from `deploy-plugin.yml` on purpose. That one refuses
to write anywhere but `WP_PLUGIN_REMOTE_DIR`, which is the guard that keeps it
away from the live sibling product. No cron, ever: this is one edit to one file.

### What was actually there

The brief said two copies, each holding only the `sitemap_index.xml` line. There
is **one**, and it holds four directives:

| URL | status | bytes | type |
|---|---|---|---|
| `/blog/robots.txt` | 200 | 175 | `text/plain` |
| `/robots.txt` | 200 | 13,181 | `text/html` |

The apex has no `robots.txt` at all. It answers `/robots.txt`,
`/definitely-not-here-xyz123.txt` and every other unmatched path with the same
13,181-byte "Coming soon" landing page. The content-binding refuses it on its
own, and the refusal says why rather than "served HTML": putting a file there is
a **create**, not an edit, and a root `robots.txt` where none existed changes the
crawl rules for the whole domain in one step.

Which matters more than it looks. RFC 9309 has a crawler read `/robots.txt` at
the host root **and nowhere else**, so the `Sitemap:` lines in
`/blog/robots.txt` — the existing `sitemap_index.xml` one included — are read by
nothing. Adding two more is correct, harmless, idempotent, and **will not on its
own get either sitemap crawled**. That needs a real file at the apex or a Search
Console submission, and it is a decision, not a default.

### The first real dispatch refused, and that is the entry

Run `30577050236`, `dry_run=false targets=blog`:

```
Refusal: blog: no remote file matched what https://asktherecruiter.com/blog/robots.txt
serves. Tried ['/blog/robots.txt', '/public_html/blog/robots.txt',
'blog/robots.txt', 'public_html/blog/robots.txt'].
```

The FTP session is rooted somewhere none of the four hand-written candidates
reach. **Nothing was written and the live file is unchanged.** This is the whole
argument for content-binding, and it is worth being explicit about the
counterfactual: a version of this job that trusted a path from the control panel
would have written a `robots.txt` into whatever directory the session happened to
land in, reported success, and left the owner believing the file was updated. The
file it created would be read by nothing, the file it was meant to update would
be untouched, and no run, log or page would ever have said so. Silent and
permanent, and the design is what made it a clean refusal instead.

Two fixes, both required, neither of them a fifth blind guess:

* **Derive from a path already proven to work.** `deploy-plugin.yml` mirrors into
  `WP_PLUGIN_REMOTE_DIR` successfully with these same credentials, so it is a
  real remote path for this exact account. `<wp-root>/wp-content/plugins/tit`
  walks up three levels to the WordPress root, which is where a robots.txt
  lives. That candidate is tried FIRST and is exempt from the name-shape filters
  — those exist to discipline guesses, and this is not one — but it is not
  exempt from anything that matters: it must still serve byte-identical content
  before a byte is written. The shape of the secret is checked rather than
  trusted, so a secret that stops being a plugin directory derives nothing at
  all rather than a plausible wrong path.
* **Make a refusal diagnostic.** When nothing matches, the run now prints the
  login directory the server chose, every parent of it, the parent of every
  candidate tried, and `/` — with the entries in each and a marker on any that
  holds a `robots.txt`. Read only, and it runs under `dry_run` too. A server
  that refuses a listing says so: an empty report and a forbidden one are
  different facts, and printing nothing for both is how the next dispatch learns
  nothing either.

**One assertion changed shape, deliberately.** The test that used to say
`secrets.WP_PLUGIN_REMOTE_DIR` never appears in `deploy-robots.yml` was a proxy
for the thing worth protecting, and the proxy broke the day that secret turned
out to be the only working remote path we have. Reading it to derive a candidate
to LOOK at widens no write path. So the test now asserts the property itself:
this job never writes inside `wp-content`, and `deploy-plugin.yml` still has no
robots.txt write path of its own. `NEVER_WRITE_INSIDE` enforces it three times —
in `candidate_paths`, in `process`, and last in `FtpTransport.write` — because
one refusal is one edit from gone.

### The URL and the filesystem belonged to different servers

The owner listed the account, and the ground truth explains both refusals.

The blog file is at **`/public_html/AskTheRecruiter.com/blog/robots.txt`**. The
mixed-case domain directory is why all four generic candidates missed — none of
them carried a domain segment, let alone a capitalised one. It is now a
candidate, and `WP_PLUGIN_REMOTE_DIR` derives the same prefix, so the general
form works for any account laid out this way. It is still content-checked before
a write: a path that was right last month is not evidence about today.

The apex refusal was more correct than it looked. `https://asktherecruiter.com/`
is **not on this host at all** — `/robots.txt` returns a 13,181-byte "Coming
soon" page built by Cloudflare, 25 of whose stylesheet references are
`/cf-fonts/`, and only `/blog/` routes through to Bluehost. So the content check
was comparing a file on Bluehost against a response from Cloudflare, and **no
file on the one could ever have equalled the other**. There is a
`/public_html/AskTheRecruiter.com/robots.txt` sitting on Bluehost and it is
served to nobody.

That failure mode is worth a name: **the URL and the filesystem belong to
different servers.** Nothing in a status code, a byte count or a `server:`
header says so — Cloudflare proxies both, so both answer 200 with
`server: cloudflare`. The only signal was that no file matched, and a job that
resolved paths by convention instead would have written into a real directory,
got a 200 back from a page it had not touched, and reported success. The
`root` refusal now names the reason and where the file would actually have to
go, because "served HTML" cost an hour to interpret and "Cloudflare builds this
page, not cPanel" costs none.

Standing conclusion, since the two lines themselves are done: the apex file was
added by hand and both sitemaps are submitted in Search Console, so the value
left here is the mechanism, not the lines. What it is worth keeping is the
property it proved twice in one afternoon — **a deploy that verifies its target
by content refuses loudly in exactly the cases where a deploy that trusts a
path would have succeeded silently.**

---

## What the tripwire costs

Derived before arming it, from the prompt and the price list, because at the time
`analysis/tripwire/results/` did not exist and
`tests/fixtures/tripwire_reply.json` is a captured *shape* carrying no token
counts. There was no run to read.

**Model.** `perplexity/sonar` (`ask.MODEL`, overridable by `TIT_TRIPWIRE_MODEL`).
OpenRouter's endpoint API prices it at **$1.00/M prompt, $1.00/M completion,
$0.005 per web search**. `_call()` skips the web plugin for any model whose name
contains `sonar`, so nothing pays OpenRouter's per-result plugin fee on top.

**Queries per run.** `COUNTRIES_PER_RUN` is derived, not chosen:
`int(1.00 / 0.02) = 50` queries a month, minus the 18-industry sweep, over 8 runs
= **4**. So an ordinary run issues 4 queries. One run a month also carries the
full sweep (`industries_due()` is derived from the dated result files), making it
**22** — exactly `MAX_QUERIES_PER_RUN`.

**Tokens per query.** The prompt is exact: `SYSTEM` 285 chars + `SCHEMA` 868 +
the question ≈ **1,433 chars**, ~**410 tokens** at 3.5 chars/token (the range
across 3.0–4.0 is 358–478). The reply is bounded by `LEADS_PER_QUERY = 8`, and
the fixture's items serialise at 242 chars each, so a full reply is ~1,946 chars,
~**560 tokens** (487–649).

| | per query |
|---|---|
| search fee | $0.00500 |
| ~410 prompt tokens | $0.00041 |
| ~560 completion tokens | $0.00056 |
| **total** | **$0.0060** |

**The search fee is 84% of it.** Token size is nearly irrelevant here, which is
worth knowing before anyone shortens the schema to save money.

The one stated uncertainty was whether OpenRouter reports Perplexity's retrieved
search context inside `prompt_tokens` (~3–6k), which would push a query to
$0.008–$0.011. It does not appear to: `schedule-link-hygiene.yml` records
**$0.0058 a query measured on a live run**, within 3% of the derivation and
below it, so the upper regime did not materialise. That figure is a comment, not
a committed result file — `analysis/tripwire/results/` is still empty here — so
the first committed run is what settles it for good. `usage.include` is already
on and `report.cost_block` already records it.

**Therefore**, at the Monday+Thursday 07:00 UTC slot (8.67 runs/month, near
enough the 8 `plan.py` is sized on — the slot now lives in
`schedule-link-hygiene.yml` as a ticket rather than as a cron in `tripwire.yml`,
for the eviction reason that file explains):

- ordinary run, 4 queries: **$0.024**
- monthly sweep run, 22 queries: **$0.13**
- **month: 53 queries, ~$0.31–$0.32**

against the $1.00 cap in `plan.TRIPWIRE_MONTHLY_USD`. The pessimistic
$0.02/query the cap was sized on is **~3.4× the real price**, so the plan is
conservative in the right direction and arming it needed no change to the cap.

### What the money buys

It is the only component that discovers sources nobody told us about. The
rejection audit is unambiguous about where the misses are: of 81 recall misses,
**0 were fetched and dropped** — no filter problem at all — while **23 are a
source problem**, 12 at publishers we have researched but not wired
(`calcalistech.com` 4, `businesswire.com` 2, `globenewswire.com` 2) and **11 at
10 publishers nobody has ever heard of here**: `latamlist.com`,
`european-biotechnology.com`, `finsmes.com`, `pv-magazine.com`, `techla.pro`.
A wiring backlog is work; an unknown-publisher list is *not knowable* from
inside, and that is precisely the gap a search-backed outside view closes. With
27 countries measured at zero recall and 4 asked about per run, a month walks
roughly a third of them. Leads are claims and die in the work list;
`collectors/tripwire_chase.py` converts them by finding the publisher's own
article, so the yield is measured in confirmed misses, and
`usd_per_confirmed_miss` stays "not yet measurable" until it has stored
something. For about $0.31 a month, the question is not whether it pays for
itself but whether we would rather keep guessing which publishers exist.

---

## The recall loop, made to turn (2026-07-30)

Three things were true at once: the measurement could not fail, the reference
set covered 29 countries, and the tripwire's price was arithmetic.

### 1. The one script whose job is quality exited 0 whatever it found

`measure_recall.py` computed recall and returned 0 on every path, so a 9% week
and a 95% week were the same event to every scheduler, alert and health check
downstream. `analysis/recall/thresholds.py` adds five gates, and the design
constraint that shaped all of them is that **no bar is a round number**. 90%
would be red forever and 5% green forever, and neither has anything to do with
what this tracker has been observed to do. Each floor is the **Wilson 95% bound
on the best rate ever recorded against the same reference set** — Wilson rather
than the normal approximation because at 8/89 the naive interval reaches below
zero, and a floor below zero is not a floor. On the 2026-07-28 measurement that
puts the floor at **4.63%**.

| gate | what it catches | bar |
|---|---|---|
| `instrument` | the API answered nothing anywhere | exit **4**, not 3 |
| `retraction` | events we were MEASURED as holding and no longer hold | `max(1, 10% of held)` |
| `held_floor` | overall rate vs the best on this digest | Wilson-95 low |
| `defect_ceiling` | defects per held event vs the worst on this digest | Wilson-95 high |
| `cell_collapse` | a cell that held 3+ and now holds 0 | a dead collector |

Two decisions worth keeping. **A rate cannot be compared across reference
sets** — a widened set deliberately samples harder countries, so recall falling
after a widening is the set working, and gating on it would teach everyone to
ignore the gate the first time it was right. So the first run against a new
digest reports **BASELINE**, not PASS. Exactly one quantity survives a change of
set: an individual gold event, by id. That is why `retraction` is the gate that
is always on, and why this repository, which has twice destroyed rows with no
red run, now has an instrument that would have gone red.

The gates run **last**, after the result is written and pushed, so a failing
gate reports a bad measurement instead of suppressing the evidence. A failed
gate also files the health entry as `degraded`: an exit code lives in one job
log, and the health page is what the digest reads.

**The sibling has the same defect and it is worse there.**
`atr-layoff-tracker/railway/recall_precision.py` returns 0 on every path *and*
posts `report_source_health("recall_precision", "ok", ...)` unconditionally — so
a collapse in precision or recall is filed as healthy, not merely unnoticed.
Reported, not fixed: that repo is read-only from here.

### 2. The gold set was 29 countries, so the miss list was a map of where we had already thought to look

`goldset-2026-07b.json` is v1 carried over **verbatim** plus 80 events from six
further independent research passes, one per world region, each run in isolation
and forbidden from consulting this tracker, its database, its repository or the
site. Same window, deliberately, so the result stays comparable with the
published 9.0% on the countries the two share.

| | v1 | v2 |
|---|---|---|
| events | 89 | 169 |
| countries | 29 | 79 |
| Africa | 1 event | 20 |
| eastern Europe + Baltics | 0 | 17 |
| Latin America | 5 | 13 |
| non-US share | 62% | 80% |

One item was dropped, for a 403, recorded in `dropped_unreachable`. Nothing was
dropped on any other ground and nothing changed after matching began.
**Assembly stayed manual**, which was the point.

Two rules the assembly earned. An **undisclosed round** is a real event, and a
set that cannot admit one measures only the events that came with a number — a
bias aimed straight at the markets the widening exists to cover.
`amount_disclosed: false` declares the omission rather than hiding it, capped at
15% of funding events. And the geographic guard now **ratchets**:
`_ratchet_problems` measures the widest set already on disk and refuses a new
one below 80% of it, comparing only against sets assembled EARLIER — a ratchet
that reached forwards would have invalidated v1 the moment v2 landed and made
its published 9.0% underivable.

**Measured against it: 24/169 held (14.2%), up from 8/89 (9.0%).** The
retraction gate is what makes that readable — all 8 events held on 07-28 are
still held, so the movement is backfill landing, not churn under a changed
denominator. Non-US went 1/55 to 14/135, and **66 countries still hold nothing**
against 27 before. The worst cell in the set is `national_news` at **2/33
(6.1%)**, which is the document type the entire non-English press route exists
to read.

Two guards fired on the widening and both were right to. `test_market_claims`
found seventeen new zero-countries that have a swept edition and 2+ wired feeds,
so reach is no longer their excuse; they went to `BUDGET_DEFERRED` with the real
reason rather than being claimed, because a market claim here is earned.
`test_backfill_gnews` asserted a literal 3 queries per edition and is now
derived from the phrase pack.

### 3. The tripwire's cost is a measurement now

First live queries: **run 30506967802, 2026-07-30, 17 search-backed queries,
$0.0977 billed** off OpenRouter's own `usage.cost`.

| | |
|---|---|
| measured | **$0.0057/query**, spread $0.0054–$0.0060 across the 17 |
| the estimate the cap was sized on | $0.0200 — **3.5x** the real price |
| Israel, the country checkable by eye | $0.0059, 8 leads |

`USD_PER_QUERY_MEASURED` is recorded and deliberately **not** substituted for the
estimate: feeding the real price into the sizing arithmetic takes
`COUNTRIES_PER_RUN` from 4 to 19 and quadruples the bill. The estimate sizes the
plan, the measurement says what the plan costs, and the gap is the safety
margin. Both are printed on every run.

Still outstanding: `analysis/tripwire/results/` remains empty, because the only
live run so far was a dry run. The cadence is armed (Mon+Thu 07:00 UTC, as a
ticket from `schedule-link-hygiene.yml`), and the arming commit landed at 19:37
UTC on a Thursday — **after** that day's slot — so the first automatic run is
Monday 2026-08-03.

### 4. The funding query could not match a round nobody called a Series

Checked because 36 base vocabulary terms with nothing about funding reads as a
hole in the largest pillar. It is not: `GOOGLE_NEWS_QUERIES`, all sixteen
`GOOGLE_NEWS_VOCAB` packs, `GDELT_QUERIES` and `BACKSTOP_INTENTS` each carry a
funding query. `BASE_VOCABULARY` genuinely has none and it does not matter —
`run_collect.build_queries` never hands it to a collector that issues queries.
Padding it would have looked like closing a gap and changed nothing that runs.

The real defect was structural. The query was `("raises" OR "raised") ("Series
A" OR "Series B" OR "seed funding")`, and Google News AND-s the groups: a growth
round, a debt facility, a credit line, a capital increase or an undisclosed
stage cannot match however many times the article says "raises". Measured
against the **54 funding events the 2026-07-28 run missed**, using each
publisher's own headline:

| | matched |
|---|---|
| old query | 13/54 (24%) |
| widened | **37/54 (69%)**, 0 false hits on the 19 leadership headlines |

Every verb and euphemism added appears verbatim in one of those 54 real
headlines; none was invented, and no bare high-frequency token stands alone (the
`investice` lesson, asserted by a test). Only `es` and `it` were widened among
the non-English packs, because they are the only two with a missed headline in
that language on file. **Stated ceiling, kept as a test fixture:** 17 of the 54
are "X raises $60m" — verb, abbreviated amount, no noun — and still out of
headline reach. Google News matches body text too, so both figures are lower
bounds; a direct RSS probe to settle it returned zero rows for every query
including the control, so it stays unverified rather than assumed.

### What is still not known

- Whether Google News matches article bodies as well as headlines, which is the
  difference between "69% now reachable" and "69% reachable from the headline
  alone". The probe that would settle it could not be run from here.
- Whether the widened set's new regions are under-measured because of events or
  because of fetchers: every research pass hit the same wall in Nigeria, Ghana,
  Uganda, Rwanda and Greece, and at the Indonesian, Malaysian, Sri Lankan,
  Kuwaiti, Athens and Johannesburg exchanges. Real events were found there and
  dropped unverified, so those countries will read better than they are.
- Eight of the 79 countries in the set have no region in the project's own
  vocabulary (`pipeline.validate._region_for_country` returns None for PY, BO,
  SV, JM, TT, NA, IQ and one more). That is our geography failing to admit a
  market the benchmark covers, and nothing currently notices.

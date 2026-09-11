# Moving extraction off DeepSeek: Haiku 4.5 and Gemini flash-lite measured

Measurement pass, 2026-09-12. No collector ran, no row was written, no
allowance was touched, no workflow was dispatched. Every table below is the
verbatim output of `ab_models.py`, run locally against the same OpenRouter key
the collectors use. Total measured spend for the whole pass: **$0.50** (four
runs; the figures per run are in the spend section at the end).

## Why

Two owner rulings meet here.

1. DeepSeek is ruled out on EU grounds (standing since 2026-08). The sandbox
   is already clean. This tracker still runs `deepseek/deepseek-chat` as
   `classify.MODEL`, the extraction call that `ops_status.py [2a]` labels
   "read-through" (the column is `source_health.model`; the interpretation
   sentence is a separate call on `READ_MODEL`, already Anthropic).
2. Spend per month has to come down while capture stays. `[2a]` reads $2.36
   per 7 days and projects $10.10/30d against the $8.00 allowance;
   `cost_projection.py [4]` prices today's configuration at $11.79/month,
   $7.70 of it extraction.

`docs/PLAN-gate-to-five-dollars.md` step 0 says the next step is "extraction
on gemini-2.5-flash-lite", blocked on one A/B. `ab_models.py` is that harness
and its docstring is explicit: a swap is taken on `--gate-gold` (accuracy
against hand labels) plus the extraction mode, never on agreement alone.

## What ran

Two candidates against the incumbent, in the three modes the docstring asks
for. The harness grew two flags for this (`--models`, `--limit`) so a session
can size a measurement to a budget without editing the candidate lists, and
`--dump` so the extraction disagreements can be read by hand rather than
bought twice. `--limit` was not needed: the whole fixture fit under the cap.

    incumbent   deepseek/deepseek-chat
    candidate   google/gemini-2.5-flash-lite   (already the gate)
    candidate   anthropic/claude-haiku-4.5

## Table 1: gate accuracy against hand labels (`--gate-gold`)

75 scoreable items of 80; 5 ambiguous and excluded. The live gate's own
recorded verdicts on the 36 ledger items: 32/36 = 88.9%.

```
model                                  acc    95% interval  recall    prec     $/item
google/gemini-2.5-flash-lite        89.3%  80.3%-94.5%   94.6%  91.4%   0.000022
deepseek/deepseek-chat              64.0%  52.7%-73.9%   53.6%  96.8%   0.000060
anthropic/claude-haiku-4.5          89.3%  80.3%-94.5%   92.9%  92.9%   0.000261
```

Set cost: flash-lite $0.00167, deepseek $0.00452, haiku $0.01961.

Read it with `KNOWN_LIMITS` in mind (English only, positive-heavy, 75
items). What it can say: neither candidate is distinguishably below the
model being replaced. DeepSeek on this surface misses 27 of 75, 26 of them
real signals it dropped, and its interval (52.7 to 73.9) does not touch the
candidates' (80.3 to 94.5). Both candidates score exactly what the live gate
scores.

## Table 2: extraction, field by field, production prompt (`--extraction`)

40 real fixture headlines through `classify.MINI_SYSTEM` + `SCHEMA_HINT`, the
production prompt byte for byte. Two identical runs were bought; the second
is the one whose answers were dumped and hand-read, and it is the one quoted.
The first run read 97/85/87/70/95/100 for flash-lite and 92/92/87/70/87/97
for Haiku, so a point or two between runs is temperature-0 provider drift and
not a signal.

```
model                             is_talent    company     pillar    country  signal_di  funding_a    $/item
deepseek/deepseek-chat                 100%       100%       100%       100%       100%       100%  0.000938
google/gemini-2.5-flash-lite            97%        87%        87%        72%        95%        97%  0.000388
anthropic/claude-haiku-4.5              92%        90%        87%        75%        87%        97%  0.004351
```

40/40 parsed for every model. Set cost: deepseek $0.03752, flash-lite
$0.01551, haiku $0.17406.

**This is headline input, not article body.** The fixture is headlines; the
production extraction call reads `FULL_READ_CHARS` of body. This repo does
not persist `raw_text`, so an extraction gold set cannot exist yet (the
plan's 2026-08-14 correction). The numbers above therefore measure a shorter
and easier version of the production task, and the gold set's own last
limit applies: these gate labels may not be reused to justify an extraction
swap. What the table can do is what the harness says it can: be read as a
floor, with every disagreement read rather than counted.

### The hand read: every disagreement on a deciding field

Rule from the plan: score a disagreement FOR the challenger when it is right
and the incumbent was wrong. Each row is one headline; a row that split
between fields is scored on the field that decides the record.

**flash-lite, 16 disagreements in 40 rows**

| # | headline | field(s) | incumbent | flash-lite | right |
|---|---|---|---|---|---|
| 1 | V and A workers hold ballot to strike over pay | signal, company, pillar, country | not a signal | V and A, rewards_comp, UK | flash-lite (gold set: a pay action at a named employer is YES) |
| 2 | Rossing donation and recruitment drive | pillar, country | empty | how_we_work, Namibia | flash-lite |
| 3 | Latvian ammo maker Ammunity names new CEO | country | "Latvian" | "Latvia" | flash-lite (a vocabulary value, not a demonym) |
| 4 | Tata Electronics biggest job creator in FY26 | pillar | company_development | how_we_work | unclear; the schema puts headcount growth in neither cleanly |
| 5 | CSSC appoints chief executive (Bdaily) | country | empty | United Kingdom | flash-lite, inferred from the publisher; the incumbent's empty is not wrong |
| 6 | Axis Trustee opens in GIFT City | country | empty | India | flash-lite |
| 7 | OpenAI expands AI workforce in Dublin | country | empty | Ireland | flash-lite |
| 8 | Eaton India plans to hire 700 | company | Eaton India | Eaton | incumbent (the named entity is the subsidiary) |
| 9 | Crystalys Therapeutics raises $130M Series B, strengthening Fortress Biotech | company | Crystalys Therapeutics | Fortress Biotech | incumbent |
| 10 | Klyde Warren Park names new CEO ahead of $200M expansion | pillar, country, funding | leadership_change, empty, empty | company_development, US, $200M | incumbent on pillar and funding (an expansion cost is not a raise); flash-lite on country |
| 11 | HSBC plans to hire 100 AI specialists | pillar | how_we_work | leadership_change | incumbent |
| 12 | Marcos says Pax Silica hub to create jobs | country | empty | Philippines | flash-lite on the field; both kept an item the gold set says to drop |
| 13 | Kraken Technology US names new CEO | company, country | Kraken Technology US, United States | Kraken Technology, US | incumbent, marginally; "US" normalises to the same country |
| 14 | JHA Companies opens new office at DuBois Airport | country | empty | United States | flash-lite |
| 15 | Tim Cook steps down as CEO | company | empty | Apple | flash-lite |
| 16 | Arunachal Pradesh transfers 7 IAS officers | country | India | empty | incumbent |

**Count: flash-lite right 9, incumbent right 6, unclear 1.** The 72% country
agreement is almost entirely the incumbent leaving `country` EMPTY where the
challenger filled it correctly (rows 2, 5, 6, 7, 10, 12, 14) plus one
demonym. Scored the plan's way, flash-lite is wrong against the incumbent on
`company` in 3 rows of 40 (92.5%), on `pillar` in 2 to 3 (92.5 to 95%), on
`country` in 1 to 2 (95 to 97.5%).

**Haiku 4.5, 15 disagreements in 40 rows**

| # | headline | field(s) | incumbent | Haiku | right |
|---|---|---|---|---|---|
| 1 | V and A strike ballot | country | empty | United Kingdom | Haiku |
| 2 | Two leaders appointed to advance Livengood | signal | signal, Livengood, leadership_change | not a signal | incumbent (gold set: YES) |
| 3 | Ammunity names new CEO | country | Latvian | Latvia | Haiku |
| 4 | Tata Electronics biggest job creator | country, direction | empty, hiring | India, neutral | Haiku on country, incumbent on direction |
| 5 | Axis Trustee, GIFT City | country | empty | India | Haiku |
| 6 | OpenAI expands in Dublin | country, direction | empty, hiring | Ireland, neutral | Haiku on country, incumbent on direction |
| 7 | Eaton India plans to hire 700 | pillar | how_we_work | company_development | unclear |
| 8 | Klyde Warren Park new CEO, $200M expansion | country, funding | empty, empty | United States, $200M | Haiku on country, incumbent on funding |
| 9 | HSBC plans to hire 100 AI specialists | pillar | how_we_work | leadership_change | incumbent |
| 10 | Marcos says Pax Silica hub | signal | signal | not a signal | Haiku (gold set: NO) |
| 11 | Cracker Barrel names new CEO | country | empty | United States | Haiku |
| 12 | Tim Cook steps down | company | empty | Apple | Haiku |
| 13 | Anurag Jain appointed CEO of NITI Aayog | signal | signal | not a signal | Haiku (gold set: NO, a government body) |
| 14 | Lorenza Maggio appointed CEO of Brussels Airlines | country | empty | Belgium | Haiku |
| 15 | CEO transition at USA Rare Earth | country | empty | United States | Haiku |

**Count: Haiku right 9, incumbent right 2, split 3, unclear 1.** Haiku is the
more careful extractor of the two candidates on this input, and it is also
**4.6x the incumbent's cost per item** and 11x flash-lite's, so it fails the
cost rule outright. It is the fallback if flash-lite is ever found wanting on
article bodies, not a route to a lower bill.

## Table 3: read-through sentence, side by side (`--readthrough`)

The harness's own note applies: this surface has no accuracy measurement and
a swap of `READ_MODEL` may not be taken on it. It is here because the
docstring names it, and because the fused sentence is what ships when
`TIT_READ_MODEL=off`. Sentences truncated at 150 characters by the harness.

```
--- Published by: thailand-business-news.com | TriNetX Appoints August Calho
  deepseek/deepseek-chat         August Calhoun becomes CEO of TriNetX, affecting leadership roles in the healthcare data analytics sector, particularly in Thailand and globally, requ
  google/gemini-2.5-flash-lite   TriNetX appoints August Calhoun as Chief Executive Officer, impacting the company's executive leadership.
  anthropic/claude-haiku-4.5     August Calhoun's appointment as CEO of TriNetX creates executive leadership transition opportunities for displaced C-suite candidates and opens direct

--- Published by: Stock Titan | Two leaders appointed to advance Livengood's
  deepseek/deepseek-chat         Gold mining professionals with experience in resource advancement are in demand in Livengood, Alaska, due to the appointment of two leaders to develop
  google/gemini-2.5-flash-lite   Two leaders appointed at Stock Titan to advance Livengood's 13.6M ounces in gold resources.
  anthropic/claude-haiku-4.5     Two new leaders at Livengood (Alaska gold project) will drive resource development and create senior executive, engineering, and operations roles acro

--- Published by: LSM | Latvian ammo maker Ammunity names new CEO
  deepseek/deepseek-chat         Latvian ammo maker Ammunity's new CEO appointment affects defense industry executives in Latvia, creating leadership opportunities in the sector.
  google/gemini-2.5-flash-lite   Ammunity, a Latvian ammo maker, has a new CEO, impacting their executive leadership.
  anthropic/claude-haiku-4.5     Latvian ammunition manufacturer Ammunity has appointed a new CEO, creating leadership transition opportunities for senior operations, supply chain, an

--- Published by: CNBC TV18 | Tata Electronics emerges as Tata Group's bigge
  deepseek/deepseek-chat         Tata Electronics employees in India will see increased job opportunities as the company becomes Tata Group's largest job creator by FY26.
  google/gemini-2.5-flash-lite   Tata Electronics hires significantly in FY26, impacting the electronics manufacturing workforce in India.
  anthropic/claude-haiku-4.5     Tata Electronics is hiring aggressively across India in FY26, creating immediate opportunities for manufacturing, engineering, and operations professi
```

Read: DeepSeek invents ("particularly in Thailand" for a US company covered
by a Thai outlet; "in demand in Livengood, Alaska"). flash-lite restates the
headline and once names the PUBLISHER as the employer ("appointed at Stock
Titan"), which is the exact failure `validate.py` exists to catch. Haiku is
the only one of the three that writes a sentence a recruiter could act on.
None of this touches production: the sentence readers see comes from
`READ_MODEL` (Sonnet 5).

## Projected cost

`cost_projection.py [4]` on the incumbent configuration (2026-09-12):

```
    configuration                                  gate   extr   read   TOTAL
    today's caps, AS RUNNING (measured)            1.92   7.70   2.17   11.79  OVER
    FULL coverage, second pass CONDITIONAL         1.92  16.72   4.71   23.35  OVER
      ... extraction on gemini-2.5-flash-lite      1.92   2.93   4.71    9.56  OVER
```

**Do not read the tool with `TIT_MODEL` overridden.** Its calibration factor
(`factor = measured cost / modelled cost`) is fitted on the ledger, which was
charged by DeepSeek, so pricing a cheaper model through it inflates the gate
and read lines to keep the total near what was charged (it printed gate
$3.35 and read $3.80 for the flash-lite override, from the same ledger that
says $1.92 and $2.17). The honest projection re-prices the extraction line
alone:

    extraction today                       $7.70
    at the tool's flash-lite ratio (2.93/16.72 = 0.175, cached prefix)   $1.35
    at the harness's measured ratio  (0.000388/0.000938 = 0.414)         $3.19

    today's caps on flash-lite extraction:  $5.44 to $7.28 / month
    today's caps on the incumbent:          $11.79 / month  (ledger: $10.10/30d)

Both ends sit under the $8.00 allowance for the first time at today's read
caps. The spread is whether flash-lite's implicit prefix cache serves in
production the way the tool models; `ab_models.py --cache-check` answers that
before the swap, not after.

## Decision

**No swap in this pull request. DeepSeek stays until the owner moves it.**

- Haiku 4.5 fails on cost (4.6x the incumbent per item). Out.
- Gemini flash-lite clears `--gate-gold` (89.3%, not distinguishably below
  anything; distinguishably above DeepSeek) and clears cost (0.41x measured,
  0.175x modelled with cache). On raw agreement it misses the 90% bar on
  `company` (87), `pillar` (87) and `country` (72); read by hand, the country
  gap is the incumbent's empties, and the challenger is right in 9 of 16
  disagreements against the incumbent's 6. Scored the way the plan says to
  score it, flash-lite clears 90% on every deciding field.
- Two things stop this session from taking it. The input was headlines and
  the production call reads bodies; nothing here measured the body task, and
  the repo's own gold set says not to use it that way. And moving a
  production model is the owner's adjudication under CLAUDE.md's standing
  authority, at any price.

**If the owner takes it, the change is three lines plus three tests**, and the
first thing to run after is the cache probe:

    pipeline/classify.py          MODEL default -> "google/gemini-2.5-flash-lite"
    cost_projection.py            MODELS["extract"] default, same string
    tests/test_readthrough_split.py, tests/test_cheap_extract.py,
    tests/test_provider_routing.py   pin the new default (the provider-order
                                     test expects deepseek first; flash-lite
                                     has no pinned order and that is correct)
    python3 ab_models.py --cache-check google/gemini-2.5-flash-lite

Then watch `ops_status.py [2a]` for a week: the per-stored-row figure
($0.00285 today) is what the swap has to move, and the reads-to-rows ratio
(48%) is what it must not.

**What would make the measurement stronger**, in order: persist `raw_text`
for a few hundred stored rows (a collector change, the plan's stated
blocker), then re-run `--extraction` on bodies; extend the gate gold set past
English; and run `--cache-check` on flash-lite so the $5.44 end of the range
is a measurement.

## Spend

    --gate-gold              $0.0258   (3 models x 75 items)
    --extraction, run 1      $0.2271   (3 models x 40 headlines)
    --extraction, run 2      $0.2271   (same, with --dump for the hand read)
    --readthrough            under $0.01 (3 models x 4 headlines; the harness
                                        does not price this mode)
    total                    about $0.50 of the $2.00 the session was given

`budget.py` put the discretionary pot at $0.89 remaining with a per-run
ceiling of $0.0444; this pass exceeded that per-run figure on three of its
four runs, on the caller's explicit $2.00 authorisation, and is filed here
so the pot's ledger is not the only place it is missing from. The harness
does not go through `budget.py --gate` or file a priced health row (the
standing gap `tests/test_budget_allocator.py` names), so the spend above is
the harness's own accounting from OpenRouter usage, and the key's own
monthly total is the authority.

# Open for the owner: one adjudication, one identity decision, one distinction

Raised 2026-08-29, out of an external adversarial review.

**Updated 2026-08-29 (second pass).** The four rejections made on another
company's reasoning have been redone by applying the owner's EXISTING rule, on
his instruction. Three were rule-determined and their verdicts are unchanged;
one (Broadcom) is genuinely not settled by the rules and is still his. The
Alibaba duplicate's open half is answered on the same ruling as its rejected
half. What is left for a person is section 5 (Broadcom), section 2's
`company_key` widening, section 3's stored-row correction pass, and section 4.

---

## 1. Six rejections were made on another company's reasoning. FOUR HAVE BEEN REDONE.

On 2026-08-22/23 an agent working the amount queue recorded ONE note against
three unrelated findings, twice over:

| state | row | amount | the note it carried was about |
|---|---|---:|---|
| rejected | Micron Technology | $10.0bn | **Micron** — correct, untouched |
| rejected | Alibaba Group Holding | $10.2bn | Micron |
| rejected | Lovable | $13.3bn | Micron |
| rejected | Nitto Denko | $28.0bn | **Nitto Denko** — correct, untouched |
| rejected | Nvidia | $150.0bn | Nitto Denko |
| rejected | Broadcom | $60.0bn | Nitto Denko |

`reviewed_by = 'claude(subagent)'` on all six. **$271.5bn withheld in total; four
of the six on evidence about a company they have nothing to do with.**

### What each of the four actually is, and what the existing rule says

Each row was read from `signals` (headline, outlet, date, stored `deal_type` and
`money_basis`), the source article was read where the headline was not
self-sufficient, and `pipeline/money_raised.py` + `pipeline/capital_event.py`
were run over the text rather than quoted from memory. **No model was called and
nothing was spent.**

| row | what it actually is | rule | outcome |
|---|---|---|---|
| Alibaba Group Holding $10.2bn | SCMP 2026-08-23, *"Alibaba to issue US$10 billion in new shares"* — HK$80bn of newly issued shares into public markets. `capital_event.classify()` → `public_offering` | money_raised rule 2, public and lender markets | **rule-determined, stays rejected** |
| Lovable $13.3bn | siliconrepublic 2026-08-12, *"Sweden's Lovable valued at $13.3bn"*. The figure is a **valuation**; the round is $400m, led by Menlo Ventures, co-led by the EQT-run Scaleup Europe Fund | the definition itself — "new capital that ARRIVED at the employer". A valuation is not capital | **rule-determined, stays rejected** |
| Nvidia $150.0bn | AFR 2026-08-18, *"Nvidia to invest almost $150b in OpenAI data centre"*. Nvidia is the payer. `money_raised.classify()` → `outbound_investment`; stored `deal_type` is `joint_venture` | rule 4 (outbound), whose own worked example is "Nvidia to invest $1.5b in SB Energy"; independently rule 1 | **rule-determined, stays rejected** |
| Broadcom $60.0bn | digitimes 2026-08-21, *"Broadcom reportedly eyes up to US$100B debt financing"*. **Inbound**, in talks with asset managers, instrument unnamed | **no rule reaches it** — see section 5 | **NOT DETERMINED, left rejected, yours** |

The three rule-determined rows were re-recorded with the same verdict and a note
about THAT row, so the ledger no longer cites a company the row has nothing to
do with. **No verdict changed and no money moved.** Broadcom's note was replaced
too, but with one that says in its first two words that it decides nothing —
leaving another company's reasoning standing as the permanent evidence for a
$60bn withholding was not an option, and guessing it was not either.

Two things the redo turned up that were not in the original brief:

* **Nvidia was NOT "right by luck".** This file guessed it might be; it is
  determined twice over, by rule 4 and by its own stored `deal_type`.
* **Lovable was not a taxonomy question at all.** It is an extraction defect —
  a valuation stored in `funding_amount_usd`. The rejection is correct, but the
  row's figure is also simply wrong, and the real $400m round is a separate
  correction that this rejection does **not** make. Flagged, not acted on.

**This can no longer happen silently.** `guardrails.review()` refuses a note
already recorded against a different event (`SharedNoteRefused`), and
`guardrails.py` exits 2 without writing. Siblings of one event may still share a
note, which is the correct way to answer a duplicate pair. Anything else needs
`--allow-shared-note`, typed on purpose. All five notes written in the redo are
unique and were accepted by that guard without an override.

```bash
python3 guardrails.py --withheld            # every rejection and its stated reason
python3 guardrails.py --accept amount/<hash> --note 'why, about THIS row'
```

---

## 2. The Alibaba duplicate: one event, two findings, opposite states

| | Alibaba Group Holding | Alibaba |
|---|---|---|
| amount | $10.2bn | $10.0bn |
| outlet | SCMP, 2026-08-23 | Taipei Times, 2026-08-25 |
| `company_key` | `alibaba group holding` | `alibaba` |
| guardrail | rejected — **redone 2026-08-29**, see section 1 | open → **rejected 2026-08-29** on the same ruling |

One announcement: HK$80bn from 710m newly issued shares. It reached the queue
twice because a guardrail decision attaches to `content_hash`
(`company_key|pillar|date|normalised-headline`), so a second outlet's wording is
a new subject with no memory of the first answer. The differing `company_key`
is also why neither dedup layer caught it — both require key EQUALITY.

It is the **fourth** occurrence, not the first: two DayOne rows, two Kingswood
rows and two Intel rows went the same way. The Kingswood note from 2026-08-04
already diagnoses it exactly — *"stored a second time under a shorter
company_key ... so dedup never saw them as one event."*

**Shipped:** `guardrails.siblings_of()` and a `SAME EVENT, ALREADY SEEN` block
printed under every open finding, carrying the earlier verdict and its note. The
matcher finds all four historical pairs and produces no false pair anywhere in
the ledger.

**The open half is now answered, and its answer FOLLOWS FROM the Alibaba ruling
in section 1 rather than being a second opinion.** `amount/85a1b6e2d284` (Taipei
Times, $10.0bn) was rejected 2026-08-29 with a note that names its sibling
`amount/9b919885d09d` and cites the same rule: `capital_event.classify()`
returns `public_offering` for its text too, and the guardrail's own finding
detail had already reached the same place ("its own text says 'share sale',
which is not a company raising money"). The amount queue is now empty; nothing
is on a grace clock.

**NOT shipped, and deliberately yours:** widening `company_key` so
`alibaba group holding` and `alibaba` collapse. `company_key` feeds
`content_hash`, so that is a rewrite of the dedup identity of all 32k stored
rows through `correct_company_key.py` — a decision about employer identity, not
a side effect of a review tool.

### 2026-08-30: measured, attempted, and NOT shipped. The recommendation is to leave it split.

Attempted via `EMPLOYER_KEY_ALIASES` and **the suite refused it**, correctly.
`tests/test_identity.py::test_an_alias_may_only_merge_two_spellings_of_one_name`
holds that the map "may only ever collapse PUNCTUATION. Two keys that already
differ in their letters are two employers, and renaming one to the other here
would be an editorial decision hiding in a lookup table." `alibaba group
holding` and `alibaba` differ in their letters. The change was reverted rather
than the test weakened.

What the attempt established, so nobody has to measure it again:

| | |
|---|---|
| `alibaba group holding` | **1 stored row, and it is NOT published** |
| `alibaba` | 4 rows, 3 published |
| `alibaba cloud` | a **subsidiary** — must never merge into the parent |
| a `strip trailing holding(s)` rule | **593 distinct stored keys** |

That last figure is the general form of this merge and it is not close: it would
fold `capri holdings`, `upstart holdings`, `labcorp holdings` and 590 others
into names that may belong to somebody else. It is the same answer the map's own
header already records for the slug-shaped rule (274 keys, three employers
fused).

**Three ways forward, and the recommendation is (a):**

* **(a) Leave it split.** The merge is worth ONE unpublished row today. Both
  Alibaba findings are `rejected`, so neither can publish and neither figure can
  reach a page; the only cost of splitting is that a future announcement covered
  under both spellings dedupes late. Cheapest, and reversible the day it matters.
* **(b) Record a ruling and add a SEPARATE documented-merge map**, leaving
  `EMPLOYER_KEY_ALIASES` and its punctuation invariant untouched. This satisfies
  the test's own stated condition — it objects to a merge asserted "without a
  document saying so" — rather than routing around it. ~20 lines plus the
  document. Do this if the split ever costs a real duplicate.
* **(c) The rule-shaped widening.** Refused twice on measurement. Do not.

Still the owner's call, and still not urgent: nothing public is wrong either way.

---

## 3. The taxonomy question is ALREADY RULED. The classifier was missing two phrasings.

The review asked for "an explicit ruling on whether primary public-equity
proceeds count as money raised". **It exists**, in `pipeline/money_raised.py`
rule 2 and `pipeline/capital_event.py`: equity sold into public markets
(`ipo`, `public_offering`) is excluded, and the owner rejected Intel's $20bn
stock sale twice on precisely that ground.

The defect was vocabulary, not policy. `capital_event.classify()` caught
`stock sale` but not:

* `"...record Hong Kong share sale"` — a bare `share sale` had been
  deliberately refused because of *"9fin completes first employee share sale
  after $170m raise"*, where the amount belongs to the round;
* `"...issue US$10 billion in new shares"`.

Meanwhile a THIRD headline for the same Alibaba event — *"...; third-largest
follow-on offering"* — **was** excluded. One announcement was landing on both
sides of the ruling depending on which outlet's wording arrived first.

**Shipped:** both phrasings, qualified so `employee/employees/staff/insider
share sale` still does not match. Measured across the whole corpus before
shipping: **2 rows of 32,451 change classification, and they are these two.**

### What this implies, for you to confirm

Under the existing ruling both Alibaba rows are `public_offering` and belong
**out** of the money total. They are currently stored `money_basis =
company_raise` and are therefore summable. The classifier fix only governs new
writes; the stored rows need a correction pass, which is a database writer and
must be queued:

```bash
gh workflow run drain-writers.yml -f enqueue=correct-money-basis.yml \
     -f inputs_json='{"dry_run":"false"}' -f reason='Alibaba public offering'
```

Confirm the reading first — it is your ruling being applied, not a new one.

**2026-08-30: the prescribed correction was a NO-OP, and is not any more.**
The command above was correct about what should happen and could not make it
happen. `correct_money_basis.py` derives its verdict by calling
`money_raised.basis()`, and `basis()` asked the stored `deal_type` and its own
patterns and never the instrument. Both Alibaba rows are stored with
`deal_type` NULL -- they predate the capital_event vocabulary fix, which is the
whole reason they are wrong -- so `basis()` returned `company_raise` on both and
the pass would have re-judged them to exactly what they already were, reported
"3 rows to judge", and left the ruling unapplied. `basis()` now asks
`capital_event` last, when the row carries no label and the patterns found
nothing. Measured on the whole corpus: **3 rows of 4,831 with a figure change
verdict, none of them published, and the published money total moves by $0.**
The third is Nvidia's $709bn "AI factory funding deal" -> `project_finance`,
one of the four capital events `pipeline/validate.py` already names in its own
comment. The branch is unreachable on the write path (build_signal calls
capital_event first and nulls the figure when it answers), so no new write
changes behaviour. See `docs/TECHLOG.md`, 2026-08-30.

**2026-08-29: the GUARDRAIL half of this is done and the DATABASE half is not.**
Both Alibaba findings are now rejected, so neither row can publish and neither
figure can reach a page. The stored `money_basis` on both is still
`company_raise`, which matters only if a row that is already live were to be
summed — neither is — but it leaves the column disagreeing with the ruling.
Running `correct-money-basis` is still yours, because it is a database writer
and this session did not run one.

---

## 5. Genuinely open, and the only one of the six that is: Broadcom $60bn

`amount/f6f157c66266303281e92eb7f320b022` — digitimes 2026-08-21, *"Broadcom
reportedly eyes up to US$100B debt financing to expand AI chip push"*. The story
says Broadcom is **in talks with asset managers** to raise more than US$60bn,
with the package potentially reaching US$100bn.

It is the one of the four that is **inbound**: unlike Nvidia's OpenAI
data-centre spend, unlike Nitto Denko's capex, unlike Micron's research
investment, this is money that would arrive at the employer named on the row.
So none of the exclusions reaches it by direction, and three separate questions
have to be answered before it can be:

1. **Private instrument or market instrument?** `money_raised.py` rule 2 states
   the debt ruling plainly — venture debt, "debt funding" and convertible notes
   stay in; bonds, notes, syndicated facilities and project finance stay out,
   "because a venture lender is backing the company, a bond market is buying an
   obligation". The article names neither. "Asset managers" leans toward private
   credit, which is the **IN** side, but leaning is not the words on the page,
   and the module's own precision argument says an exclusion "still has to be
   defended from the words on the page".
2. **Does a raise that is only in talks count?** Rule 6 says "a raise that has
   not closed is not a raise" — but it says it as the justification for
   `pledge`, and `_PLEDGE` is deliberately narrow (investment commitments,
   pledges to invest, MoUs, letters of intent) and does not reach "in talks" or
   "reportedly eyes". Nothing in the taxonomy covers a reported negotiation.
   **Extending it would be new policy, which is why this session did not.** It
   is also not a one-row question: a `reportedly eyes / in talks to raise /
   is exploring` class would touch every future row of that shape.
3. **Which number?** The headline says up to $100bn, the body says more than
   $60bn, and $60bn — the floor of a range — is what is stored.

**What the ledger says now.** The row is still `rejected`, exactly where the bad
note left it, because leaving a verdict alone was the instruction and guessing
is what caused this file. Its note was replaced with one about Broadcom that
opens with the words NOT DETERMINED and lists the three questions above, so
nobody reading `--withheld` is told a decision was made that was not.

**What you can do.**

```bash
# it is a real private raise after all -> release it
python3 guardrails.py --accept amount/f6f157c66266303281e92eb7f320b022 \
    --note 'why, about THIS row'
# it is not, and you want the reason on the record -> re-record
python3 guardrails.py --reject amount/f6f157c66266303281e92eb7f320b022 \
    --note 'why, about THIS row'
```

If question 2 gets a general answer rather than a Broadcom one, it belongs in
`money_raised.py` as a named kind with its own pattern block and its own
paragraph, not as a widened `_PLEDGE`.

---

## 6. Genuinely open: company-issued shares vs shareholders selling existing stock

The review is right that nothing distinguishes these, and the current taxonomy
does not need it to: a **secondary** sale (existing holders selling) is not a
company raise either, because the money goes to the sellers. Both are excluded
today, so the two land in the same place for different reasons.

It starts to matter if you ever want to publish "capital raised by the employer"
including public issuance, where a primary issue is inbound to the company and a
secondary is not. That is a product decision and there is no defect to fix until
it is taken. Left alone on purpose.

---

## What was shipped with this file

| change | proven by removing it |
|---|---|
| `guardrails.same_event` / `siblings_of` | `test_the_rejected_alibaba_sibling_is_surfaced` (+2) fail |
| `SharedNoteRefused` in `review()` | `test_a_note_that_already_decided_another_event_is_refused` fails |
| `share sale` (qualified) in `_PUBLIC_EQUITY` | `test_a_qualified_share_sale_by_a_listed_issuer_is_a_public_offering` fails |
| `issue ... new shares` in `_PUBLIC_EQUITY` | `test_issuing_new_shares_is_a_public_offering` fails |

The Alibaba pair is a permanent fixture in `tests/test_guardrail_siblings.py`.

## What the 2026-08-29 second pass changed

**Ledger and this document only. No code changed, so there is no new test and
nothing to prove by mutation** — the guard that makes this class of defect
impossible (`SharedNoteRefused`) shipped with the first pass and is already
pinned by `test_a_note_that_already_decided_another_event_is_refused`. It was
exercised for real here: all five notes below were written fresh and passed it
without `--allow-shared-note`.

| finding | before | after |
|---|---|---|
| `amount/9b919885d09d` Alibaba Group Holding $10.2bn | rejected, on the Micron note | rejected, on an SCMP/public-offering note |
| `amount/26c3c9ab2dce` Lovable $13.3bn | rejected, on the Micron note | rejected, on a valuation-not-capital note |
| `amount/3a2a9b08a5db` Nvidia $150.0bn | rejected, on the Nitto Denko note | rejected, on an outbound-investment note |
| `amount/f6f157c66266` Broadcom $60.0bn | rejected, on the Nitto Denko note | rejected, on a NOT DETERMINED note — section 5 |
| `amount/85a1b6e2d284` Alibaba $10.0bn | open, 5d | rejected as the sibling of `9b919885d09d` |

Micron's and Nitto Denko's own rows were not touched: their notes are about
them and are correct.

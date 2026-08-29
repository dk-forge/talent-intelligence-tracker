# Open for the owner: six adjudications to redo, one duplicate, one distinction

Raised 2026-08-29, out of an external adversarial review. Everything here needs
a person. Nothing in this file has been decided by a session, and the code
shipped alongside it deliberately changes **what a reviewer is shown**, never
what a row is worth.

---

## 1. Six rejections were made on another company's reasoning. They need redoing.

On 2026-08-22/23 an agent working the amount queue recorded ONE note against
three unrelated findings, twice over. The ledger still holds them:

| state | row | amount | the note it carries is about |
|---|---|---:|---|
| rejected | Micron Technology | $10.0bn | **Micron** — correct |
| rejected | Alibaba Group Holding | $10.2bn | Micron |
| rejected | Lovable | $13.3bn | Micron |
| rejected | Nitto Denko | $28.0bn | **Nitto Denko** — correct |
| rejected | Nvidia | $150.0bn | Nitto Denko |
| rejected | Broadcom | $60.0bn | Nitto Denko |

`reviewed_by = 'claude(subagent)'` on all six. **$271.5bn withheld in total; four
of the six on evidence about a company they have nothing to do with.**

Two of the six may still be right by luck — Nvidia's $150bn is an OpenAI
data-centre investment and Broadcom's $60bn looks like infrastructure
financing, both of which the owner has excluded before. That is a guess, and a
guess is what put them there. A rejection is permanent and shows up nowhere but
`--withheld`, so each of the four needs its own reading:

```bash
python3 guardrails.py --withheld            # every rejection and its stated reason
python3 guardrails.py --accept amount/<hash> --note 'why, about THIS row'
```

**This can no longer happen silently.** `guardrails.review()` now refuses a note
already recorded against a different event (`SharedNoteRefused`), and
`guardrails.py` exits 2 without writing. Siblings of one event may still share a
note, which is the correct way to answer a duplicate pair. Anything else needs
`--allow-shared-note`, typed on purpose.

---

## 2. The Alibaba duplicate: one event, two findings, opposite states

| | Alibaba Group Holding | Alibaba |
|---|---|---|
| amount | $10.2bn | $10.0bn |
| outlet | SCMP, 2026-08-23 | Taipei Times, 2026-08-25 |
| `company_key` | `alibaba group holding` | `alibaba` |
| guardrail | **rejected** (on the Micron note) | **open**, 5d unanswered |

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

**NOT shipped, and deliberately yours:** widening `company_key` so
`alibaba group holding` and `alibaba` collapse. `company_key` feeds
`content_hash`, so that is a rewrite of the dedup identity of all 32k stored
rows through `correct_company_key.py` — a decision about employer identity, not
a side effect of a review tool.

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

---

## 4. Genuinely open: company-issued shares vs shareholders selling existing stock

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

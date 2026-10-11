# Verification tiers

Owner-approved public trust labels (2026-10-11), shared in meaning with the AI
Layoff Tracker. Every public record carries exactly one tier.

| Tier | Means | Comes from |
|---|---|---|
| **verified** | An official or primary filed document. | Stored `confidence = verified`, which `validate.infer_confidence` only allows when the source host is in `vocab.PRIMARY_SOURCE_DOMAINS` (SEC EDGAR, Companies House, BOE, EDINET, DART, CVR, ARES, IRS 990 and the other registers listed there). |
| **reported** | News or press. | Stored `confidence = reported`, and stored `rumored` with the sub-flag `rumored = true`. A rumored row is never promoted. |
| **measured** | Our own measurement of a job board. | The `ats_boards` collector (Greenhouse, Ashby, Lever), or any source named "... job board". |

## The stored labels, mapped

| Stored `confidence` | Source kind | Tier | `rumored` |
|---|---|---|---|
| verified | primary register or filing host | verified | false |
| verified | any other host (should not exist; the write guard caps it) | reported | false |
| reported | news, press release, company site, aggregator-chased publisher | reported | false |
| rumored | outlet calls it unconfirmed | reported | true |
| any | `ats_boards` collector or "job board" source | measured | false |

The job-board rule wins over `confidence`. The board count is our reading of
the employer's page on two dates, not a figure the employer filed, so it is
neither a filing nor a news report.

## Rules

- **News-only can never be verified.** `pipeline/tiers.py` re-checks the host on
  read, on top of the write cap in `validate.py`.
- **A measured item is never an announced hiring event.** `count_meaning`
  files job-board rows as `OPEN_VACANCIES`, not `CONFIRMED_HIRES`.
  `tests/test_tiers.py` pins both.
- The stored `confidence` column does not change. The tier is derived.

## Where it appears

- `pipeline/tiers.py`: the pure mapping (`tier_for`, `tier_of_row`).
- `wordpress-plugin/.../includes/tiers.php`: the PHP twin. It trusts the stored
  `verified` because the write guard already checked the host.
- `/query` rows and the CSV and JSON downloads: additive `tier` and `rumored`
  fields. The CSV appends them as the last two columns.
- The public table's evidence column, and the daily digest's row tag.

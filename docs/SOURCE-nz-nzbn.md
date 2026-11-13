# NZ: the NZBN register, terms of reuse and the rules a collector must meet

Recorded 2026-11-12. `collectors/nz_nzbn.py` (built 2026-11-13) is **DORMANT**:
no workflow schedules it and `TIT_NZ_NZBN` defaults off. The key (GitHub secret
`NZBN_API_KEY_NZ`) was approved by MBIE on 2026-11-11. **Its routes and field
names were written from MBIE's v5 documentation and have never been read against
the live API** (`ROUTES_VERIFIED_LIVE = False`; an armed run refuses until that
is flipped by someone who has read a real response). Test fixtures are
constructed, not captured. It stays off the Sources page until a run has stored
from it (coverage is earned), and arming is the owner's call.

Not yet done, needed before arming: a probe script run with the key, a cron via
`schedule-link-hygiene.yml`/`drain-writers.yml` (never its own `schedule:`),
persisting ETags between runs (the collector takes them as a parameter because a
collector never writes), and the Sources page entry naming MBIE's NZBN register
and the API Access agreement.

## Terms, confirmed in writing

MBIE API Support, case **00094961** (Stephen Lee), 2026-11-12:

- **Licence and restrictions:** MBIE API Access agreement section 4, plus NZ law:
  Companies Office legislation, the Privacy Act principles, and the Unsolicited
  Electronic Messages Act 2007. MBIE has no issue with the initial use case:
  store entity name, NZBN, status, registration date, industry classification and
  location, and show them publicly.
- **Attribution (agreement schedule 2):** no fixed wording, but every surface that
  shows NZBN data must state (1) the source and (2) the date and time the data
  was retrieved. MBIE's example: "Data sourced from the Ministry of Business,
  Innovation and Employment's NZBN register. Last refreshed <date time>."
- **Sole traders** can make every field except the NZBN private at any time,
  including their name. Never show sole-trader personal details, and a refresh
  must drop any field that has become private.
- **Limiting calls:** use Change Event Search (date-range polling, filtered by
  entity type and changeEventType) and/or ETag with an `If-Match` header on
  `GET /entities/:nzbn` (304 when unchanged). Watchlists need a push endpoint;
  we skip them.

## Rules the collector must meet before it is armed

1. Every surface showing NZBN data carries the attribution line above with the
   last-refresh timestamp, and the Sources page entry names MBIE's NZBN register
   and the API Access agreement.
2. Sole-trader entities are stored with the NZBN only. A refresh overwrites, so a
   field that came back absent is dropped, never kept from the previous read.
3. Refresh is incremental (Change Event Search, or conditional GET), never a full
   re-fetch.
4. Route through `classify -> validate -> store`; set `raw_text`; a zero return
   is `degraded`. The key is read from the environment only.

`tests/test_nzbn_terms_record.py` pins this record so it cannot be lost.

# NZ: the NZBN register, terms of reuse and the rules a collector must meet

Recorded 2026-11-12. **No NZ collector exists yet.** `source_registry.py` carries
a note that NZ needs a registered key; the key (GitHub secret `NZBN_API_KEY_NZ`)
was approved by MBIE on 2026-11-11, and nothing in the repo reads it. Nothing may
render on the Sources page as covered until a collector, a health check and a
passing test exist (coverage is earned). Arming it is the owner's call.

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

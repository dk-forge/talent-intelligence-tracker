"""The order in which a run spends its read budget. Free, and only an order.

WHAT THIS IS FOR
----------------
`classify.READTHROUGH_CAP` bounds how many FULL read-throughs one run may buy.
When it binds, every candidate after it raises `BudgetDeferred`, is printed as
DEFER, and is deliberately NOT marked seen, so the next run picks it up. The last
real run before the cap was raised bought all 60 of its reads and deferred 95
gate survivors that way.

Which 60 of those 155 got read was decided by arrival order — feed order inside
`national_press`, edition order inside `google_news`. Nothing about that ordering
was ever chosen. This module chooses it.

THE TWO RULES THIS MODULE LIVES UNDER
-------------------------------------
1. **It changes the ORDER and never the SET.** `rank()` returns a permutation,
   asserted in tests. Nothing here can reject, filter, drop or promote a
   candidate, and no score of any value changes whether a record may store —
   `precheck`, the gate, `validate` and `store` are untouched and unaware of it.
   A deferred candidate stays unmarked and returns on the next run, so the
   ordering decides WHEN a story is read, never WHETHER.
2. **It costs nothing.** No model call and no network call, at any score. The
   country table is one GROUP BY over a committed index, the employer check is
   one indexed lookup per candidate against a cache we already hold, and the
   keyword work is the regexes `prefilter` has already compiled. Ranking a
   thousand candidates is milliseconds and $0. A ranking signal that needed a
   fetch would cost more than the read it was trying to prioritise.

WHY THESE SIGNALS AND NOT OTHERS
--------------------------------
Every one is measured off something this repo already holds:

  country_need   57 of 200 countries hold any row at all, so 143 hold nothing;
                 of the 55 that are neither US nor GB the median holds ONE row,
                 and 15,140 of 15,711 current rows are US or GB. That
                 concentration is the product's largest measured defect
                 and it is not a feed problem — the editions are swept — so it is
                 substantially a question of which candidates got read.
  employer_new   an employer we hold nothing about is a company profile that does
                 not exist yet; the 40th row on a US mega-cap is a row on a page
                 that already reads well. Weighted below country on purpose: a
                 new name correlates with junk as well as with coverage, and the
                 gate is the thing qualified to tell those apart.
  keyword_force  a headline that states an employer AND an amount, or an employer
                 AND a C-title, is one the read-through can complete. One that
                 merely mentions "jobs" may or may not be about an employer at
                 all. This is the same evidence `cheap_extract` uses to close a
                 record for $0, so a candidate scoring high here is either free
                 or cheap and certain.
  source_tier    a filing over a news item. Inert in practice today, because a
                 run collects from one collector at a time, and kept for the
                 backfills, which do mix.

DELIBERATELY NOT SIGNALS
------------------------
* **The catalogue's `source_type` column.** The recall worklist's under-delivering
  document types are `trade_press` (4% held), `press_release` (16%),
  `national_news` (0%), `filing` (40%) — and the catalogue's column is 66
  freeform values from "News Organization" (888 rows) to "Patent Office". Mapping
  one onto the other is inventing a vocabulary to rank by, which is exactly what
  "normalise through fixed vocabularies" forbids, and a wrong mapping would be
  invisible: it would just quietly rank the wrong things first.
* **Anything a model would have to judge.** The point of ranking is to spend the
  model's budget better, so paying the model to decide the order is circular.
"""

from __future__ import annotations

import re
import sqlite3

from . import cheap_extract, vocab

#: Weights. Deliberately coarse and deliberately ordered: the effect worth
#: having is "country we hold nothing about first", and a finely tuned scalar
#: would be fitted to one captured run rather than to a property of the corpus.
#: Ties fall back to arrival order (a stable sort), so a run with no signal at
#: all behaves exactly as it did before this module existed.
W_COUNTRY_EMPTY = 6.0      # we hold zero rows for this country
W_COUNTRY_THIN = 3.0       # we hold some, but under COUNTRY_THIN_ROWS
W_EMPLOYER_NEW = 1.5
W_KEYWORD_FORCE = 1.0      # per class of stated evidence, up to three
W_SOURCE_TIER = 2.0

#: Under this many stored rows a country is "thin". Measured 2026-07-29 over
#: 15,711 current rows: 57 countries hold any row at all, 143 hold none, and of
#: the 55 that are neither US nor GB the MEDIAN holds 1 and 53 of 55 hold fewer
#: than 25. So this threshold is the shape of the distribution rather than a
#: round number — above it (Canada 64, Ireland 30) a country has a real presence
#: on the page, below it a single row moves its coverage measurably.
COUNTRY_THIN_ROWS = 25

#: Collectors whose items are primary documents rather than news prose. A filing
#: earns `verified` and cannot be a rewrite of somebody else's story.
FILING_COLLECTORS = frozenset({
    "sec_edgar", "sec_form_d", "sec_execcomp", "uk_paygap", "bse_india",
    "edinet_japan", "opendart_korea", "companies_house",
})

_C_TITLE = re.compile(
    r"\b(?:chief executive|CEO|CFO|CTO|COO|CIO|chair(?:man|woman|person)?|"
    r"managing director|president|director)\b", re.I)
_HEADCOUNT = re.compile(r"\b\d{2,6}\s+(?:new\s+)?(?:jobs|roles|staff|"
                        r"employees|positions|hires)\b", re.I)


class Context:
    """Everything the scorer needs, read once per run.

    Built from the database and nothing else, so it is reproducible and needs no
    key, no network and no fixture. `for_conn(None)` gives an empty context whose
    scores are still well defined — a dry run with no database ranks on keyword
    force alone rather than crashing.
    """

    def __init__(self, rows_by_country: dict[str, int] | None = None,
                 known_employers: frozenset[str] | None = None):
        self.rows_by_country = dict(rows_by_country or {})
        self.known_employers = frozenset(known_employers or ())

    @classmethod
    def for_conn(cls, conn: sqlite3.Connection | None) -> "Context":
        if conn is None:
            return cls()
        try:
            rows = {r[0]: r[1] for r in conn.execute(
                "SELECT country, COUNT(*) FROM signals "
                " WHERE is_current = 1 AND country IS NOT NULL AND country != '' "
                " GROUP BY country") if r[0]}
            # The employer set is read whole rather than queried per candidate.
            # 7,318 keys is a few hundred kilobytes and one scan; a per-candidate
            # query is a thousand round trips for the same answer.
            employers = frozenset(r[0] for r in conn.execute(
                "SELECT DISTINCT company_key FROM signals "
                " WHERE is_current = 1 AND company_key IS NOT NULL "
                "   AND company_key != ''") if r[0])
        except sqlite3.OperationalError:
            return cls()
        return cls(rows, employers)

    def country_rows(self, iso2: str | None) -> int | None:
        """Rows held for a country, or None when the candidate names none."""
        if not iso2:
            return None
        return self.rows_by_country.get(iso2.upper(), 0)


def candidate_country(item: dict) -> str | None:
    """The candidate's country HINT as ISO-2, or None.

    A hint, never a claim: `source_country` is the publisher's own country and
    `locale` is the Google News edition, and neither is what the story is about —
    a Brazilian outlet covering a US round is a US job. validate.py refuses to
    treat either as sourced geography and this module has no business doing
    otherwise. It is a fine signal for ORDERING, because being wrong about it
    costs a place in a queue rather than a wrong row on a page.
    """
    locale = str(item.get("locale") or "")
    if ":" in locale:
        code = locale.split(":", 1)[0].strip().upper()
        if len(code) == 2:
            return code
    name = str(item.get("source_country") or "").strip()
    if not name:
        return None
    if len(name) == 2 and name.upper() in vocab.COUNTRY_NAMES:
        return name.upper()
    return vocab.normalize_country(name)


def keyword_force(item: dict) -> int:
    """How much of a record the headline already states, 0-3.

    Reuses the deterministic extractor's own reading of the text, so a candidate
    scoring 3 here is frequently one `cheap_extract` will close for $0 — in which
    case ranking it first costs nothing and stores a row. The rest of the scale
    is what the read-through has the best chance of completing.
    """
    text = str(item.get("raw_text") or item.get("headline") or "")
    if not text:
        return 0
    score = 0
    parsed = cheap_extract.parse_funding(item)
    if parsed is not None:
        score += 2          # an employer AND a stated amount
    elif _C_TITLE.search(text):
        score += 1
    if _HEADCOUNT.search(text):
        score += 1
    return min(score, 3)


def score(item: dict, context: Context) -> float:
    """This candidate's priority. Higher is read sooner. Pure, and free."""
    total = 0.0

    held = context.country_rows(candidate_country(item))
    if held == 0:
        total += W_COUNTRY_EMPTY
    elif held is not None and held < COUNTRY_THIN_ROWS:
        total += W_COUNTRY_THIN

    if str(item.get("collector") or "") in FILING_COLLECTORS:
        total += W_SOURCE_TIER

    if context.known_employers:
        parsed = cheap_extract.parse_funding(item)
        key = getattr(parsed, "company_key", "") if parsed else ""
        if key and key not in context.known_employers:
            total += W_EMPLOYER_NEW

    return total + W_KEYWORD_FORCE * keyword_force(item)


def rank(items: list[dict], context: Context) -> list[dict]:
    """The same candidates, in the order the budget should meet them.

    A stable sort on the negated score, so equal candidates keep the order the
    collector produced and a run where nothing scores behaves precisely as it did
    before this existed. The return value is a permutation of the input — same
    objects, same count — and a test asserts it, because the one thing this must
    never do is change what is eligible.
    """
    return sorted(items, key=lambda item: -score(item, context))


def explain(items: list[dict], context: Context, *, top: int = 0) -> str:
    """One line for the run log: what the ordering actually moved.

    Printed rather than inferred. Reordering is invisible by nature — the run
    stores rows either way — so without a number beside it nobody can tell
    whether it is doing anything, and "it feels better" is how a reordering that
    achieves nothing survives for months.
    """
    if not items:
        return ""
    cut = top or len(items)
    ordered = rank(items, context)

    def empty_countries(rows: list[dict]) -> int:
        return sum(1 for row in rows[:cut]
                   if context.country_rows(candidate_country(row)) == 0)

    def distinct_countries(rows: list[dict]) -> int:
        return len({candidate_country(row) for row in rows[:cut]} - {None})

    return (f"ranked {len(items)} candidate(s); the first {min(cut, len(items))} "
            f"now hold {empty_countries(ordered)} from countries with no stored "
            f"rows (was {empty_countries(items)}) across "
            f"{distinct_countries(ordered)} countries (was "
            f"{distinct_countries(items)})")

#!/usr/bin/env python3
"""Judge every stored figure against the definition of "money raised".

    python3 correct_money_basis.py --dry-run     # the whole verdict, nothing written
    python3 correct_money_basis.py               # apply locally
    python3 correct_money_basis.py --enrich      # apply, then push to the site

WHAT WAS WRONG
--------------
The site's "money raised" total was `SUM(funding_amount_usd)` over every row in
the funding view, and nothing had ever asked what those figures were of. The
top of the published set held five different kinds of thing that are not a
company raising money:

    $3.50bn  Accel raises $3.5 billion to invest in emerging global AI startups
    $3.30bn  NextEra secures $3.3bn in state funding for 10GW of gas generation
    $2.50bn  Marcos secures US$2.5B in investment commitments from Canada visit
    $1.50bn  Nvidia to invest $1.5b in SB Energy
    $1.50bn  Alibaba said to be selling gaming arm for US$1.5 billion

The last line is the one worth remembering, because nothing failed to read it.
The classifier labelled that row `divestiture`, correctly, and the total added
it up anyway: the column that answers the question existed and no sum was
asking it. On the other 5,584 rows carrying a figure, `deal_type` was empty —
and empty was being summed as though it meant "we checked and it is a round",
when it meant "nothing ever looked".

WHAT THIS DOES
--------------
Writes `money_basis` on every live row that carries a figure, by calling
`pipeline.money_raised.basis()` — the SAME function `validate.build_signal`
calls on the write path. That is the point of the script and the reason it is
not a list of row ids: it cannot disagree with the pipeline, and re-running it
after a change to the definition re-judges the corpus under the new one.

    'company_raise'      money the named employer raised          SUMMED
    an excluding kind    a sale price, a fund close, an outbound
                         spend, a subsidy, a pledge, a bond       NOT summed
    NULL                 never examined                           NOT summed

The row is untouched otherwise. `funding_amount` and `funding_amount_usd`
stay exactly as extracted, because they are correct: the source really did say
$1.5bn, and a reader looking at the Alibaba row should see $1.5bn on it. What
changes is only whether that number may be added into a total.

WHY AN UPDATE IN PLACE AND NOT A REVISION
-----------------------------------------
`money_basis` is DERIVED, like funding_amount_usd and materiality: computed by
us from text the row already holds, never stated by a source. It is not an
input to `validate.content_hash`, so writing it moves no fingerprint and the
row can still match its own history — which is the entire reason
correct_company_key.py and correct_sec_pillar.py have to re-issue instead.
Nothing a source said is edited here, and nothing can be: the only column this
script writes is one no source has an opinion about.

The site takes it through `/enrich`, whose allowlist is derived values only, so
this cannot rewrite a headline, a company or an amount even if it were wrong.

WHY IT IS NOT A ONE-OFF BACKFILL
--------------------------------
The cause is fixed in `pipeline/validate.py`: every row stored from now on gets
a verdict at write time, from all four collection paths, because they all go
through `build_signal`. This pass exists for the rows that predate the column,
and `--check` is the standing assertion that the write path is still doing its
job. A live row with a figure and a NULL basis means something is storing rows
without judging them, and it is a FAILURE rather than a backlog.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter

from pipeline import money_raised, publish, schema

#: A pass that excludes a large share of the corpus is not a correction, it is
#: somebody having broken the classifier. Measured on the 2026-08-20 corpus:
#: 61 of 4,238 published rows with a figure, 1.4%. Ten percent is most of an
#: order of magnitude above that. A real widening of the definition is the one
#: legitimate way past it and deserves a human typing --force.
MAX_EXCLUDED_SHARE = 0.10


class Unsafe(RuntimeError):
    """So many rows are excluded that the likeliest explanation is a defect in
    money_raised.py rather than a defect in the corpus."""


def rows_with_a_figure(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT row_id, signal_id, company, headline, summary, deal_type, "
        "       money_basis, funding_amount, funding_amount_usd, "
        "       published_at, collector "
        "  FROM signals "
        " WHERE is_current = 1 "
        "   AND ((funding_amount IS NOT NULL AND funding_amount <> '') "
        "        OR funding_amount_usd IS NOT NULL) "
        " ORDER BY row_id")]


def verdict(row: dict) -> str:
    """The basis for one stored row.

    `raw_text` is not available here — it is not a column, it was the document
    at classification time — so the judgement is made on the headline and the
    summary. That is a real difference from the write path and it can only make
    this pass MORE conservative: a tell that appears solely in the body of an
    article is a tell this pass cannot see, so it leaves the row summable. It
    never invents an exclusion the write path would not have made.
    """
    return money_raised.basis(
        row.get("deal_type"), row.get("company"),
        row.get("headline"), row.get("summary"))


def changes(rows: list[dict], *, force: bool = False) -> list[tuple[dict, str]]:
    """(row, new basis) for every row whose stored value is not the verdict."""
    out = [(r, verdict(r)) for r in rows]
    out = [(r, v) for r, v in out if (r.get("money_basis") or None) != v]
    excluded = sum(1 for _, v in out if v != money_raised.COMPANY_RAISE)
    if rows and not force and excluded > len(rows) * MAX_EXCLUDED_SHARE:
        raise Unsafe(
            f"{excluded} of {len(rows)} rows with a figure would be excluded "
            f"({excluded / len(rows):.1%}), over the {MAX_EXCLUDED_SHARE:.0%} "
            f"ceiling. Read pipeline/money_raised.py before passing --force.")
    return out


def unjudged(conn) -> list[dict]:
    """Live rows carrying a US dollar figure that nothing has examined.

    The standing assertion. After this pass and one collection run it must be
    empty, because build_signal judges every figure it stores.
    """
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT signal_id, collector, company, headline FROM signals "
        " WHERE is_current = 1 AND funding_amount_usd IS NOT NULL "
        "   AND money_basis IS NULL ORDER BY row_id")]


def apply(conn, work: list[tuple[dict, str]]) -> int:
    for row, basis in work:
        conn.execute("UPDATE signals SET money_basis = ? WHERE row_id = ?",
                     (basis, row["row_id"]))
    conn.commit()
    return len(work)


def _money(rows) -> float:
    return sum((r.get("funding_amount_usd") or 0) for r in rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report the verdict and write nothing")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any live row carries an unjudged figure")
    parser.add_argument("--enrich", action="store_true",
                        help="after applying, push money_basis to the live site")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share is excluded")
    parser.add_argument("--top", type=int, default=25,
                        help="how many excluded rows to name (default 25)")
    args = parser.parse_args()

    conn = schema.connect()

    if args.check:
        stragglers = unjudged(conn)
        if not stragglers:
            print("every live figure has been judged")
            return 0
        print(f"{len(stragglers)} live row(s) carry a US dollar figure that "
              f"nothing has judged. They are LEFT OUT of every published "
              f"total, which is correct, but a row arriving unjudged means a "
              f"write path is skipping validate.build_signal:", file=sys.stderr)
        for row in stragglers[:20]:
            print(f"    {row['collector']:<18} {row['headline'][:70]}",
                  file=sys.stderr)
        return 1

    rows = rows_with_a_figure(conn)
    print(f"{len(rows)} live rows carry a funding figure")

    try:
        work = changes(rows, force=args.force)
    except Unsafe as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    excluded = [(r, v) for r, v in work if v != money_raised.COMPANY_RAISE]
    published = [r for r, _ in excluded if r.get("published_at")]

    print(f"\n  rows to judge                   {len(work):>5}")
    print(f"  of those, NOT a company raise   {len(excluded):>5}")
    print(f"  already published, so already"
          f" in a public total  {len(published):>5}")

    by_kind = Counter(v for _, v in excluded)
    print()
    for kind, n in by_kind.most_common():
        money = _money([r for r, v in excluded if v == kind])
        print(f"    {kind:<22}{n:>5} rows   ${money / 1e9:>9,.2f}bn")

    before = _money([r for r in rows if r.get("published_at")])
    removed = _money(published)
    print(f"\n  published money total before    ${before:>18,.0f}")
    print(f"  removed by this correction      ${removed:>18,.0f}")
    print(f"  published money total after     ${before - removed:>18,.0f}")

    print("\n  the largest rows leaving the total:")
    for row, basis in sorted(excluded,
                             key=lambda rv: -(rv[0].get("funding_amount_usd") or 0)
                             )[:args.top]:
        usd = (row.get("funding_amount_usd") or 0) / 1e9
        print(f"    {usd:>8.3f}bn  {basis:<20} {row['headline'][:78]}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    n = apply(conn, work)
    print(f"\njudged {n} rows")

    if args.enrich:
        report = publish.enrich_published(conn)
        print(f"enriched: sent {report['sent']}, updated {report['updated']}")
        for err in (report.get("errors") or [])[:10]:
            print(f"  ERROR {err}", file=sys.stderr)
        if report.get("errors"):
            return 1
    else:
        print("NOT sent to the site. Re-run with --enrich, or let the next "
              "collect run's enrich leg carry it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

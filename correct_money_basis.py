#!/usr/bin/env python3
"""Judge every stored figure against the definition of "money raised".

    python3 correct_money_basis.py --dry-run     # the whole verdict, nothing written
    python3 correct_money_basis.py               # apply locally
    python3 correct_money_basis.py --enrich      # apply, then push to the site
    python3 correct_money_basis.py --check       # the standing assertion, both corpora

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

WHICH CORPUS `--check` ASKS, AND WHY IT IS TWO
----------------------------------------------
It asked the COMMITTED DATABASE, and answered "every live figure has been
judged" on 2026-08-30 while the live site's own /aggregate reported
`money.coverage.unjudged = 2`. The committed database is where the pipeline
keeps its judgement; it is not where a number gets published. An assertion that
only reads it is reading the one corpus that cannot hold the defect, and it
passed for as long as it existed.

So it asks both, and they are different questions:

    committed database   did validate.build_signal judge what it stored?
    live site            is an unjudged figure sitting on a published page?

`money.coverage.unjudged` is computed by `tit_money_unjudged_where()` in
includes/api.php from the identical predicate `unjudged()` uses here. Two
corpora, one definition.

PASS / FAIL / UNKNOWN, and exit 0 / 1 / 3. A site that could not be read has
not told us it is clean, so it is never a pass -- and a plugin too old to
report the key is UNKNOWN rather than zero, which is the reason this reads the
key by name instead of defaulting it.

AND ON A FAILURE IT NAMES THE ROWS. `/aggregate` answers how many and never
which, so a nonzero count used to be the end of what a session could learn
without a human opening the database. `name_unjudged()` walks `/query` for the
rows that carry a figure and keeps the ones the site returns with no basis --
best effort, run ONLY when the count is already nonzero, and it declares itself
incomplete rather than presenting a short list as the whole answer.

NOTHING HERE CORRECTS ANYTHING. `--check` is a reader and holds no lock. The
correction is a database write and is queued like every other one:

    gh workflow run drain-writers.yml -f enqueue=correct-money-basis.yml \
      -f inputs_json='{"dry_run":"false"}' -f reason='why'
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter

from pipeline import money_raised, publish, schema

#: The published corpus, read the way a reader reads it.
#:
#: `--check` asked the COMMITTED DATABASE whether every figure had been judged,
#: and the committed database is not where a wrong number gets published. On
#: 2026-08-30 it answered "every live figure has been judged", exit 0, while
#: the site's own /aggregate reported `money.coverage.unjudged = 2`: two
#: published rows carrying a funding_amount_usd with a NULL money_basis, which
#: is exactly and only the state this assertion exists to catch. The assertion
#: was reading the one corpus that cannot hold the defect.
#:
#: The site computes it with `tit_money_unjudged_where()` in includes/api.php --
#: `funding_amount_usd IS NOT NULL AND money_basis IS NULL` -- which is the same
#: predicate as `unjudged()` below, spelled in the other language. Two corpora,
#: one definition; neither side may drift without the other noticing.
DEFAULT_SITE = "https://asktherecruiter.com/blog"
AGGREGATE_PATH = "/wp-json/talent/v1/aggregate"
QUERY_PATH = "/wp-json/talent/v1/query"

#: How many /query pages the diagnosis may read before giving up.
#:
#: 200 rows a page (the endpoint's own ceiling) and ~4,400 live rows carry a
#: figure, so 30 pages covers the corpus with room to grow. A CAP rather than a
#: walk-until-done, because a diagnosis that follows a growing corpus forever is
#: how a check becomes the outage it was meant to report. Hitting it makes the
#: naming INCOMPLETE, never a shorter answer presented as the whole one.
MAX_DIAGNOSIS_PAGES = 30
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

#: PASS / FAIL / UNKNOWN, the three states backup_check.py keeps apart, and for
#: the same reason: a check that could not run must never render as a check that
#: found nothing.
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

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


class LiveUnavailable(RuntimeError):
    """The live corpus could not be read.

    UNKNOWN, and never a pass. A site that cannot be reached has not told us it
    is clean; it has told us nothing.
    """


def _site(site: str | None = None) -> str:
    """Where the published corpus lives.

    WP_SITE_URL when the caller has it (the workflows do), the public base
    otherwise -- /aggregate needs no key, so the standing assertion can run in
    a job that holds no secret at all, which is the right shape for a reader.
    """
    chosen = (site or os.environ.get("WP_SITE_URL") or DEFAULT_SITE).strip()
    return chosen.rstrip("/")


def live_coverage(site: str | None = None, timeout: int = 40,
                  session=None) -> dict:
    """`money.coverage` exactly as the live site reports it.

    Not cache-busted, deliberately, matching guardrails.py: a random query
    string is a key nothing holds an entry for, so it measures the ORIGIN
    rather than what a reader is served, and it is what shared hosting
    throttles. The figure is a count of rows and a minute of staleness cannot
    turn a 2 into a 0.
    """
    url = _site(site) + AGGREGATE_PATH
    try:
        import requests
    except ImportError as exc:                                # pragma: no cover
        raise LiveUnavailable(f"requests is not installed ({exc})") from exc
    try:
        resp = (session or requests).get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        raise LiveUnavailable(f"{url}: {exc}") from exc

    if not isinstance(body, dict):
        raise LiveUnavailable(f"{url}: the response is not a JSON object")
    money = body.get("money")
    if not isinstance(money, dict):
        raise LiveUnavailable(
            f"{url}: the response carries no `money` block. tit_aggregate_money"
            f"() returns null when tit_money_aggregate() is absent, so this is "
            f"a plugin too old to answer -- UNKNOWN, not zero.")
    coverage = money.get("coverage")
    if not isinstance(coverage, dict):
        raise LiveUnavailable(f"{url}: `money` carries no `coverage` block")
    return coverage


def live_unjudged(site: str | None = None, timeout: int = 40,
                  session=None) -> int:
    """How many published rows carry a figure nothing has judged.

    THE ABSENT KEY IS THE WHOLE POINT. `coverage.get("unjudged", 0)` would read
    a plugin that predates tit_money_unjudged_where() as a corpus with nothing
    wrong in it -- the silent pass this project refuses everywhere else. A
    missing key, a null, or anything that is not a number is UNKNOWN.
    """
    coverage = live_coverage(site, timeout, session)
    if "unjudged" not in coverage:
        raise LiveUnavailable(
            "the live `money.coverage` does not report `unjudged`. The site is "
            "running a plugin older than tit_money_unjudged_where() in "
            "includes/api.php. That is UNKNOWN and must not be read as zero.")
    value = coverage["unjudged"]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise LiveUnavailable(f"`unjudged` is not a number: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LiveUnavailable(f"`unjudged` is not a number: {value!r}") from exc


def name_unjudged(site: str | None = None, timeout: int = 40, session=None,
                  max_pages: int = MAX_DIAGNOSIS_PAGES
                  ) -> tuple[list[dict], bool]:
    """The rows behind the count. Best effort, and it says when it is not sure.

    `/aggregate` reports HOW MANY carry an unjudged figure and never WHICH:
    `money_basis` is a closed-vocabulary filter (`tit_multi_param` drops any
    value outside `tit_allowed_money_bases()`), so `/query` cannot be asked for
    `IS NULL` -- an absent filter is no clause at all, not a null test.

    So this walks the rows that carry a figure and keeps the ones the site
    returns with no basis. `min_funding_usd=1` is the only way to say
    "funding_amount_usd IS NOT NULL" through the public API, and it is exact
    enough for a diagnosis: SQL's `>= 1` is false for NULL, and a real funding
    figure is never 0.

    Returns `(rows, complete)`. `complete` is False when the walk hit the page
    cap or a request failed, and a caller must NOT present a partial list as the
    whole answer -- the count from /aggregate is the authority on how many there
    are, and this only ever tries to put names to them.
    """
    try:
        import requests
    except ImportError as exc:                                # pragma: no cover
        raise LiveUnavailable(f"requests is not installed ({exc})") from exc

    base = _site(site) + QUERY_PATH
    found: list[dict] = []
    page = 1
    while page <= max_pages:
        try:
            resp = (session or requests).get(
                base,
                params={"min_funding_usd": 1, "per_page": 200, "page": page},
                headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
        except Exception:
            # A failed page makes the naming incomplete. It must not turn a
            # real FAIL into a softer verdict, so it is reported, not raised.
            return found, False
        if not isinstance(body, dict):
            return found, False
        rows = body.get("rows")
        if not isinstance(rows, list) or not rows:
            return found, True
        for row in rows:
            if isinstance(row, dict) and not (row.get("money_basis") or None):
                found.append(row)
        total = body.get("total")
        if isinstance(total, int) and page * 200 >= total:
            return found, True
        page += 1
    return found, False


def check(conn, *, offline: bool = False, site: str | None = None,
          timeout: int = 40, session=None) -> tuple[str, list[str]]:
    """The standing assertion, over BOTH corpora.

    They answer different questions and the project needs both. The committed
    database says whether `validate.build_signal` judged what it stored; the
    live site says whether an unjudged figure is sitting on a published page.
    A row can be clean in the first and unjudged in the second -- that is
    precisely the state found on 2026-08-30 -- so checking only the pipeline's
    own copy is checking the one place the defect cannot appear.

    FAIL WINS OVER UNKNOWN, as in backup_check.py: if something is definitely
    wrong, that is the answer, whatever else could not be read.
    """
    lines: list[str] = []
    verdicts: list[str] = []

    stragglers = unjudged(conn)
    if stragglers:
        verdicts.append(FAIL)
        lines.append(
            f"FAIL  pipeline: {len(stragglers)} row(s) in the committed "
            f"database carry a US dollar figure that nothing has judged. A row "
            f"arriving unjudged means a write path is skipping "
            f"validate.build_signal:")
        for row in stragglers[:20]:
            lines.append(f"          {row['collector']:<18} "
                         f"{(row['headline'] or '')[:70]}")
    else:
        verdicts.append(PASS)
        lines.append("PASS  pipeline: every figure in the committed database "
                     "has been judged")

    if offline:
        verdicts.append(UNKNOWN)
        lines.append(
            "UNKNOWN  site: --offline, so the published corpus was NOT "
            "consulted. The committed database is not where a wrong number "
            "gets published, so this is not a pass.")
        return _worst(verdicts), lines

    try:
        live = live_unjudged(site, timeout, session)
    except LiveUnavailable as exc:
        verdicts.append(UNKNOWN)
        lines.append(f"UNKNOWN  site: could not be read ({exc}). NOT a pass.")
        return _worst(verdicts), lines

    if live:
        verdicts.append(FAIL)
        lines.append(
            f"FAIL  site: {live} published row(s) carry a funding_amount_usd "
            f"with a NULL money_basis, per money.coverage.unjudged at "
            f"{_site(site)}{AGGREGATE_PATH}.")
        lines.append(
            "          No published TOTAL is wrong: tit_money_where() asks for "
            "`company_raise` BY NAME, so an unjudged row is never summed. That "
            "is why this can sit unnoticed, and it is the reason the assertion "
            "exists rather than the reason to ignore it.")
        named, complete = name_unjudged(site, timeout, session)
        if named:
            lines.append("          The rows, from /query:")
            for row in named[:20]:
                lines.append(
                    f"            {(row.get('collector') or '?'):<18} "
                    f"{(row.get('signal_id') or '?')[:12]}  "
                    f"{(row.get('headline') or '')[:60]}")
        if not complete:
            lines.append(
                "          NAMING INCOMPLETE -- the walk hit the page cap or a "
                "request failed, so this list may be short. The count above is "
                "the authority on how many there are; this is only an attempt "
                "to put names to them.")
        elif len(named) != live:
            lines.append(
                f"          NAMING DISAGREES -- /aggregate counts {live} and "
                f"/query names {len(named)}. Both readings are live but not "
                f"simultaneous, and /query's figure filter is "
                f"`funding_amount_usd >= 1` where the count's is `IS NOT NULL`. "
                f"Treat the count as the verdict.")
        if not stragglers:
            lines.append(
                "          The pipeline's own copy is clean, so this is the "
                "site holding rows the current judgement has not reached: "
                "pipeline.publish.enrich_published() is the only route "
                "money_basis takes to an already-published row, and it stops "
                "on RUN_BUDGET_SECONDS. Queue the correction, do not run it "
                "from here:")
            lines.append(
                "          gh workflow run drain-writers.yml "
                "-f enqueue=correct-money-basis.yml "
                "-f inputs_json='{\"dry_run\":\"false\"}' "
                "-f reason='live money.coverage.unjudged is nonzero'")
    else:
        verdicts.append(PASS)
        lines.append("PASS  site: no published row carries an unjudged figure")

    return _worst(verdicts), lines


def _worst(verdicts: list[str]) -> str:
    if FAIL in verdicts:
        return FAIL
    if UNKNOWN in verdicts:
        return UNKNOWN
    return PASS


#: `--check`'s exit codes. 1 rather than 2 for FAIL because CLAUDE.md and this
#: module's own help have said "exit 1 if any live row carries an unjudged
#: figure" since the flag existed, and 3 for UNKNOWN because that is what every
#: other three-state check in this repo returns.
EXIT = {PASS: 0, FAIL: 1, UNKNOWN: 3}


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
                        help="assert no row carries an unjudged figure, in the "
                             "committed database AND on the live site "
                             "(0 pass, 1 fail, 3 could not check)")
    parser.add_argument("--offline", action="store_true",
                        help="with --check, skip the live read. The published "
                             "corpus then reports UNKNOWN, never a pass.")
    parser.add_argument("--site", default=None,
                        help="site base for --check (default WP_SITE_URL, then "
                             f"{DEFAULT_SITE})")
    parser.add_argument("--timeout", type=int, default=40,
                        help="seconds to wait for the live read (default 40)")
    parser.add_argument("--enrich", action="store_true",
                        help="after applying, push money_basis to the live site")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if an implausible share is excluded")
    parser.add_argument("--top", type=int, default=25,
                        help="how many excluded rows to name (default 25)")
    args = parser.parse_args()

    conn = schema.connect()

    if args.check:
        verdict, lines = check(conn, offline=args.offline, site=args.site,
                               timeout=args.timeout)
        stream = sys.stdout if verdict == PASS else sys.stderr
        for line in lines:
            print(line, file=stream)
        print(f"\nVERDICT: {verdict}", file=stream)
        return EXIT[verdict]

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

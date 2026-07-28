#!/usr/bin/env python3
"""Collector entrypoint.

    python run_collect.py --dry-run           # show what WOULD be stored
    python run_collect.py --dry-run --offline # no network, no LLM, fixtures only
    python run_collect.py                     # actually store

Nothing is stored until a dry run looks right (spec 11 step 2).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import date

import source_registry as registry
from collectors import gdelt, google_news, sec_edgar, sec_form_d
from pipeline import classify, prefilter, publish, schema, store, validate

RUNS_PER_DAY = 2
SEGMENTS_PER_RUN = 4


def build_queries(run_index: int, source: str = "google_news") -> list[str]:
    """Layer 1 broad sweep + a rotating slice of the segment matrix, plus the
    standalone euphemism queries that must never be AND-ed with the base
    vocabulary (spec 14).

    GDELT gets its own list: its query language differs (space is AND, OR needs
    parentheses) and reusing the Google News strings returned 216 pieces of
    noise out of 219."""
    if source == "gdelt":
        return list(registry.GDELT_QUERIES)
    if source == "google_news":
        # Precise phrases plus `when:` recency. The old broad sweep returned
        # political job-creation stories with no employer in them.
        return list(registry.GOOGLE_NEWS_QUERIES)

    base = " OR ".join(f'"{term}"' for term in registry.BASE_VOCABULARY[:12])

    segments = registry.rotate(
        registry.build_segments(),
        day_of_year=date.today().timetuple().tm_yday,
        run_index=run_index,
        runs_per_day=RUNS_PER_DAY,
        per_run=SEGMENTS_PER_RUN,
    )

    queries = [base]
    queries += [f"({base}) AND \"{segment}\"" for segment in segments]
    queries += list(registry.STANDALONE_QUERIES)
    return queries


# Three editions a run, twice a day, sweeps the whole list in about four days.
LOCALES_PER_RUN = 3

# Candidates are what cost money, so the run carries its own cap rather than
# relying on --limit being passed.
#
# Measured 2026-07-27, from the spend step across consecutive real runs:
#   $0.0511 per 40-candidate run  ->  $0.00128 per classification
#
#   40/run   (this cap)                 $3.07/month     2,400 classifications
#   122/run  (all the filter passes)    $9.35/month     7,320
#   244/run  (that, with the 8d window) $18.70/month   14,640
#
# SUPERSEDED 2026-07-28 by the two-stage gate (classify.gate). The maths above
# priced every candidate at a full read-through, which made the cap the
# coverage constraint: the last single-stage run fetched 140, passed 122
# through the free filter, and classified only 40 — the other 82 discarded for
# cost, not quality.
#
# With the gate, looking at a candidate costs ~1/40th of classifying it, so
# this cap is generous on purpose: it bounds gate spend (150 x ~$0.00003 =
# half a cent per run), while the money is bounded separately by
# classify.READTHROUGH_CAP, the ceiling on FULL classifications per run.
# Worst case per month at the defaults:
#   gate   150 x 2/day x 30            ~$0.30
#   full    60 x 2/day x 30 x $0.00128 ~$4.60   (realistically far less:
#           only gate survivors reach it, ~1/3 of candidates on measured runs)
# The OpenRouter key's own limit still binds before any of this.
DEFAULT_CANDIDATE_CAP = 150


def build_locales(run_index: int) -> list[tuple[str, str]]:
    """The US anchor plus a deterministic slice of the rest.

    The anchor never rotates out: it is the largest market and the one the SEC
    collectors also cover, so dropping it on a given day would leave a visible
    hole for no benefit.
    """
    rotating = registry.rotate(
        list(registry.GOOGLE_NEWS_LOCALES),
        day_of_year=date.today().timetuple().tm_yday,
        run_index=run_index,
        runs_per_day=RUNS_PER_DAY,
        per_run=LOCALES_PER_RUN,
    )
    return [registry.GOOGLE_NEWS_ANCHOR] + rotating


def fair_share(items: list[dict], limit: int) -> list[dict]:
    """Cap the run without starving the queries that ran last.

    A flat head slice is the trap the sibling fell into: with a broad sweep
    first, the targeted company queries filled the cap and never fired. Taking
    one item from each query in turn means every query contributes before any
    query contributes twice.
    """
    if len(items) <= limit:
        return items

    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(item.get("query", ""), []).append(item)

    out: list[dict] = []
    round_index = 0
    while len(out) < limit:
        added = False
        for bucket in buckets.values():
            if round_index < len(bucket):
                out.append(bucket[round_index])
                added = True
                if len(out) == limit:
                    break
        if not added:
            break
        round_index += 1
    return out


def run(*, dry_run: bool, offline: bool, run_index: int, limit: int | None,
        source: str = "google_news") -> int:
    conn = schema.connect()
    module = {"gdelt": gdelt, "sec_edgar": sec_edgar,
              "sec_form_d": sec_form_d}.get(source, google_news)
    collector = module.COLLECTOR

    if offline:
        items = _fixture_items()
    else:
        queries = build_queries(run_index, source)
        # The SEC collectors search filings by form and item, not by query
        # string, so reporting a query count for them is just misleading.
        if source.startswith("sec_"):
            print(f"[{collector}] searching SEC filings")
        else:
            print(f"[{collector}] {len(queries)} queries")
        try:
            if source == "google_news":
                locales = build_locales(run_index)
                print(f"[{collector}] editions: "
                      + ", ".join(f"{c}:{l}" for l, c in locales))
                window = registry.recency_window_days(LOCALES_PER_RUN, RUNS_PER_DAY)
                print(f"[{collector}] recency window: {window}d "
                      f"(a locale comes round every "
                      f"{len(registry.GOOGLE_NEWS_LOCALES) / LOCALES_PER_RUN / RUNS_PER_DAY:.1f}d)")
                items = module.collect(
                    queries, locales=locales,
                    queries_for=lambda lang: registry.google_news_queries(
                        lang, window_days=window))
            else:
                items = module.collect(queries)
        except Exception as exc:
            store.report_health(conn, collector, status="error", detail=str(exc)[:400])
            conn.commit()
            print(f"[{collector}] FETCH FAILED: {exc}", file=sys.stderr)
            return 1

    found = len(items)

    # Order matters, cheapest first. Each stage throws work away before a more
    # expensive one has to look at it:
    #   fetch (done) -> prefilter (free) -> limit -> resolve (HTTP) -> LLM (paid)
    # The keyword gate exists to avoid paying to classify noise. A primary
    # source is not noise: an SEC 8-K Item 5.02 filing IS an officer or
    # director change by definition, so gating it on news vocabulary drops
    # exactly the filings we went to SEC to get.
    skip_prefilter = source in ("sec_edgar", "sec_form_d")

    kept, filtered = [], 0
    for item in items:
        ok, reason = (True, "") if skip_prefilter else prefilter.passes(item.get("raw_text", ""))
        if ok:
            kept.append(item)
        else:
            filtered += 1
            print(f"  filtered  {item.get('headline','')[:60]}  ({reason})")

    cap = limit or DEFAULT_CANDIDATE_CAP
    if len(kept) > cap:
        print(f"[{collector}] capping {len(kept)} candidates to {cap}, "
              f"one per query in turn")
        kept = fair_share(kept, cap)

    # Google News hands back an aggregator redirect and needs resolving. GDELT
    # already returns the publisher's own URL, which is the whole reason it
    # leads: a homepage is not a receipt.
    if not offline and source == "google_news":
        kept = [google_news.resolve_source_url(item) for item in kept]

    stored = duplicates = rejected = skipped = throttled = budget_deferred = 0
    print(f"\n[{collector}] {found} fetched, {filtered} filtered out, "
          f"{len(kept)} going to the classifier\n")

    for item in kept:
        url = item.get("source_url") or item.get("discovery_url") or ""

        # Deduplicate BEFORE the LLM, never after (spec 4 rule 2).
        if url and store.already_seen(conn, url):
            skipped += 1
            continue

        try:
            classified = _stub_classify(item) if offline else classify.classify(item)
        except classify.AuthFailed as exc:
            # A bad key is permanent for this run. The first live run printed
            # the same 401 twenty-five times before anyone learned anything.
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            store.report_health(conn, collector, status="error",
                                items_found=found, items_stored=stored,
                                detail=f"auth failed: {str(exc)[:200]}")
            conn.commit()
            return 1
        except classify.CreditsExhausted as exc:
            print(f"\nSTOPPING: {exc}", file=sys.stderr)
            store.report_health(conn, collector, status="error",
                                items_found=found, items_stored=stored,
                                detail="OpenRouter credits exhausted")
            conn.commit()
            return 1
        except classify.BudgetDeferred as exc:
            # The per-run spend ceiling, not a busy provider. Same retry-next-
            # run handling, counted apart so a run that deferred work ON
            # PURPOSE cannot trip the mostly-throttled breakage alarm below.
            budget_deferred += 1
            print(f"  DEFER   {item.get('headline','')[:70]}\n          {exc}")
            continue
        except classify.Throttled as exc:
            # Not a verdict on the candidate. Counted separately, printed as
            # DEFER, and deliberately NOT marked seen, so the next run picks it
            # up instead of losing it. A busy provider must never look like a
            # quiet news day.
            throttled += 1
            print(f"  DEFER   {item.get('headline','')[:70]}\n          {exc}")
            continue
        except classify.ClassifyError as exc:
            rejected += 1
            print(f"  REJECT  {item.get('headline','')[:70]}\n          classify: {exc}")
            continue

        if classified is None:
            rejected += 1
            # Never reject silently. A run that stores nothing must say why for
            # every candidate — this exact silence hid three funding filings
            # being discarded because the prompt did not list funding.
            print(f"  REJECT  {item.get('headline','')[:70]}\n"
                  f"          model judged it not a talent signal")
            continue

        try:
            signal = validate.build_signal(classified, item, collector)
        except validate.Rejected as exc:
            rejected += 1
            print(f"  REJECT  {item.get('headline','')[:70]}\n          {exc}")
            continue

        if dry_run:
            _print_signal(signal)
            stored += 1
            continue

        outcome = store.store(conn, signal)
        store.mark_seen(conn, url, collector, outcome)
        if outcome == "stored":
            stored += 1
            _print_signal(signal)
        else:
            duplicates += 1
            if outcome == "retracted":
                # Say so plainly: a withdrawn record resurfacing is a judgement
                # holding, not routine dedup.
                print(f"  SKIP    {signal.headline[:66]}\n"
                      f"          previously retracted, not re-stored")

    print(
        f"\n[{collector}] found={found} "
        f"{'would store' if dry_run else 'stored'}={stored} "
        f"duplicate={duplicates} rejected={rejected} "
        f"deferred={throttled} budget-deferred={budget_deferred} already-seen={skipped}"
    )
    # Spend visibility: the gate is the cost-avoidance stage, so say what it
    # did. gate_rejects is money NOT spent on full read-throughs.
    if classify.STATS["gate_calls"]:
        print(
            f"[{collector}] gate: {classify.STATS['gate_calls']} screened, "
            f"{classify.STATS['gate_rejects']} dropped cheap, "
            f"{classify.STATS['full_calls']} full read-throughs "
            f"(cap {classify.READTHROUGH_CAP}/run)"
        )

    if dry_run:
        print("\nDRY RUN — nothing was written.")
        conn.rollback()
        return 0

    # Fail loud (spec 6 rule 4). Two distinct breakages, both of which look
    # like a quiet day if you only count stored rows:
    #   - found nothing at all: the feed or the query is broken
    #   - found plenty and kept none of it, with nothing even landing as a
    #     duplicate: the classifier or a guard is broken, not the news
    everything_rejected = len(kept) > 0 and stored == 0 and duplicates == 0
    # A run that mostly hit a busy provider stored little through no fault of
    # the pipeline. That is still not "ok": it means coverage has a hole that
    # only the next run can fill, and silence about it is how a throttled
    # source looks like a quiet news day for a month.
    mostly_throttled = throttled > 0 and throttled >= max(1, len(kept) // 2)
    broken = found == 0 or everything_rejected or mostly_throttled

    store.report_health(
        conn, collector,
        status="degraded" if broken else "ok",
        items_found=found, items_stored=stored,
        detail=(f"{duplicates} dup, {rejected} rejected, {throttled} deferred"
                + (" | every candidate rejected" if everything_rejected else "")
                + (f" | {throttled} deferred to the next run, provider was busy"
                   if mostly_throttled else "")),
    )
    conn.commit()

    if everything_rejected:
        print(f"\n[{collector}] DEGRADED: {found} candidates, none stored, none duplicate.",
              file=sys.stderr)
    return 1 if broken else 0


def _print_signal(s) -> None:
    # Show which basis the geography came from. A row can be stored on employer
    # HQ alone, and printing a bare "None" for country hides that entirely.
    if s.country:
        where = ", ".join(p for p in (s.city, s.country) if p)
    elif s.hq_country:
        where = ", ".join(p for p in (s.hq_city, s.hq_country) if p) + " (HQ)"
    else:
        where = "unknown"

    print(f"  STORE   {s.company} — {s.headline[:70]}")
    print(f"          {s.pillar} / {s.signal_direction} / {s.confidence}")
    print(f"          {where}   published {s.published_date or 'unknown'}")
    print(f"          read-through: {s.talent_readthrough[:100]}")
    print(f"          source: {s.source_url[:90]}")


def _fixture_items() -> list[dict]:
    from pathlib import Path
    fixture = Path(__file__).parent / "tests" / "fixtures" / "google_news_sample.xml"
    return google_news.parse(fixture.read_bytes(), query="offline-fixture")


def _stub_classify(item: dict) -> dict | None:
    """Deterministic stand-in so --offline exercises the whole path without
    spending a cent. Never used in a real run."""
    text = item.get("raw_text", "")
    lowered = text.lower()
    if "appoint" in lowered or "chief executive" in lowered or "steps down" in lowered:
        pillar, direction = "leadership_change", "neutral"
    elif "pay" in lowered or "salary" in lowered or "bonus" in lowered:
        pillar, direction = "rewards_comp", "comp_shift"
    elif "office" in lowered or "remote" in lowered or "hybrid" in lowered:
        pillar, direction = "how_we_work", "neutral"
    else:
        pillar, direction = "company_development", "hiring"

    city = next((c for c in ("Dublin", "London", "Berlin", "Amsterdam", "Paris")
                 if c.lower() in lowered), "")
    country = next((c for c in ("Irish", "German", "French", "Dutch", "British")
                    if c.lower() in lowered), "")
    country = {"Irish": "Ireland", "German": "Germany", "French": "France",
               "Dutch": "Netherlands", "British": "United Kingdom"}.get(country, "")

    return {
        "is_talent_signal": True,
        "company": (item.get("headline", "").split()[0] or "Unknown"),
        "pillar": pillar,
        "signal_direction": direction,
        "city": city,
        "country": country,
        "confidence": "reported",
        "headline": item.get("headline", ""),
        "summary": item.get("headline", ""),
        "talent_readthrough": "Offline stub — not a real read-through.",
        "predicted_outcome": "",
        "check_after_date": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect talent intelligence signals.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be stored")
    parser.add_argument("--offline", action="store_true", help="fixtures only, no network or LLM")
    parser.add_argument("--run-index", type=int, default=0, help="0 or 1, for segment rotation")
    parser.add_argument("--limit", type=int, help="cap candidates, for cheap testing")
    parser.add_argument("--source", choices=["google_news", "gdelt", "sec_edgar", "sec_form_d"],
                        default="google_news", help="which collector to run")
    parser.add_argument("--publish", action="store_true",
                        help="after storing, push unpublished rows to WordPress")
    args = parser.parse_args()

    if args.offline and not args.dry_run:
        parser.error("--offline is only meaningful with --dry-run")

    code = run(dry_run=args.dry_run, offline=args.offline,
               run_index=args.run_index, limit=args.limit, source=args.source)

    if args.publish and not args.dry_run:
        code = max(code, _publish())
    return code


def _publish() -> int:
    """Publishing is separate from collecting on purpose: a failed push must
    never lose collected rows, and rows stay unpublished until WordPress has
    actually accepted them."""
    conn = schema.connect()
    try:
        # Health first and never fatally: a stale timestamp is a much smaller
        # problem than a lost signal, so a failure here must not stop the
        # records being sent.
        try:
            sent_health = publish.publish_health(conn)
            if sent_health:
                print(f"[publish] health for {sent_health} collectors")
        except Exception as exc:
            print(f"[publish] health not sent: {exc}", file=sys.stderr)

        result = publish.publish(conn)
    except publish.PublishError as exc:
        print(f"\nPUBLISH FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"\n[publish] sent={result['sent']} stored={result['stored']} "
          f"duplicate={result['duplicate']} errors={len(result['errors'])}")
    for err in result["errors"][:10]:
        print(f"  ERROR  row {err.get('index')}: {err.get('error')}")

    # Partial failure is still failure: the workflow must go red.
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

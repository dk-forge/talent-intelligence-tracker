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
from collectors import google_news
from pipeline import classify, prefilter, publish, schema, store, validate

RUNS_PER_DAY = 2
SEGMENTS_PER_RUN = 4


def build_queries(run_index: int) -> list[str]:
    """Layer 1 broad sweep + a rotating slice of the segment matrix, plus the
    standalone euphemism queries that must never be AND-ed with the base
    vocabulary (spec 14)."""
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


def run(*, dry_run: bool, offline: bool, run_index: int, limit: int | None) -> int:
    conn = schema.connect()
    collector = google_news.COLLECTOR

    if offline:
        items = _fixture_items()
    else:
        queries = build_queries(run_index)
        print(f"[{collector}] {len(queries)} queries")
        try:
            items = google_news.collect(queries)
        except Exception as exc:
            store.report_health(conn, collector, status="error", detail=str(exc)[:400])
            conn.commit()
            print(f"[{collector}] FETCH FAILED: {exc}", file=sys.stderr)
            return 1

    found = len(items)

    # Order matters, cheapest first. Each stage throws work away before a more
    # expensive one has to look at it:
    #   fetch (done) -> prefilter (free) -> limit -> resolve (HTTP) -> LLM (paid)
    kept, filtered = [], 0
    for item in items:
        ok, reason = prefilter.passes(item.get("raw_text", ""))
        if ok:
            kept.append(item)
        else:
            filtered += 1
            print(f"  filtered  {item.get('headline','')[:60]}  ({reason})")

    if limit:
        kept = kept[:limit]

    if not offline:
        kept = [google_news.resolve_source_url(item) for item in kept]

    stored = duplicates = rejected = skipped = 0
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
        except classify.ClassifyError as exc:
            rejected += 1
            print(f"  REJECT  {item.get('headline','')[:70]}\n          classify: {exc}")
            continue

        if classified is None:
            rejected += 1
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

    print(
        f"\n[{collector}] found={found} "
        f"{'would store' if dry_run else 'stored'}={stored} "
        f"duplicate={duplicates} rejected={rejected} already-seen={skipped}"
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
    broken = found == 0 or everything_rejected

    store.report_health(
        conn, collector,
        status="degraded" if broken else "ok",
        items_found=found, items_stored=stored,
        detail=(f"{duplicates} dup, {rejected} rejected"
                + (" | every candidate rejected" if everything_rejected else "")),
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
    parser.add_argument("--publish", action="store_true",
                        help="after storing, push unpublished rows to WordPress")
    args = parser.parse_args()

    if args.offline and not args.dry_run:
        parser.error("--offline is only meaningful with --dry-run")

    code = run(dry_run=args.dry_run, offline=args.offline,
               run_index=args.run_index, limit=args.limit)

    if args.publish and not args.dry_run:
        code = max(code, _publish())
    return code


def _publish() -> int:
    """Publishing is separate from collecting on purpose: a failed push must
    never lose collected rows, and rows stay unpublished until WordPress has
    actually accepted them."""
    conn = schema.connect()
    try:
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

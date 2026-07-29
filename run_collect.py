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
from collectors import (ats_boards, gdelt, google_news, national_press,
                        sec_edgar, sec_execcomp, sec_form_d, tripwire_chase,
                        uk_paygap)
from pipeline import (cheap_extract, classify, dedupe, prefilter, publish,
                      schema, store, validate)

# Registration. A collector that exposes `as_classified` derives its own
# record from structured fields and never calls the model, so it skips the
# keyword gate, the LLM and the spend cap alike — there is no spend to cap.
SOURCES = {
    "google_news": google_news,
    "gdelt": gdelt,
    "national_press": national_press,
    "sec_edgar": sec_edgar,
    "sec_form_d": sec_form_d,
    "sec_execcomp": sec_execcomp,
    "uk_paygap": uk_paygap,
    "ats_boards": ats_boards,
    # Dormant: nothing schedules it. It reads the tripwire's work list and
    # searches for each lead's PUBLISHER, so the model's claims never reach the
    # store — only the article does. See collectors/tripwire_chase.py.
    "tripwire_chase": tripwire_chase,
}

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
    if source == "tripwire_chase":
        # The tripwire's work list IS the population: one targeted query per
        # lead, built from the employer's name inside the collector.
        return []

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


# Five editions a run, twice a day, sweeps the 51-edition list in 5.1 days
# (was 3/run over 36 editions = six days; he:IL made it 51 on 2026-07-29).
# The recency window derives from this — 51 editions push it from 6d to 7d
# with nobody remembering to — so nothing ages out between visits. The
# honest cost of the wide list is LATENCY, not loss: a non-anchor market's
# new story waits up to ~5 days for its edition's turn. The fix would be a
# third daily cron slot (RUNS_PER_DAY=3 sweeps in 3.4d), but that is +50%
# on every per-run spend ceiling, and raising spend is the owner's decision,
# the same rule that pins READTHROUGH_CAP. Raise RUNS_PER_DAY and the cron
# together or not at all: the rotation arithmetic reads this constant.
LOCALES_PER_RUN = 5

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
# this cap is generous on purpose: it bounds gate spend, while the money is
# bounded separately by classify.READTHROUGH_CAP, the ceiling on FULL
# classifications per run.
#
# RAISED 150 -> 1500 on 2026-07-29, and the reason is measured, not guessed.
# The first real national_press run:
#
#   575 feeds -> 530 live -> 10,741 items -> 9,308 after dedup
#   9,308 fetched, 8,290 filtered out by the FREE prefilter,
#   150 going to the classifier          <-- this cap
#   countries reached: 141
#
# So 1,018 items survived the free relevance filter and only 150 were ever
# looked at. 868 items we had ALREADY JUDGED RELEVANT were discarded for cost,
# not quality - which is verbatim the failure the two-stage gate was built to
# end (see the superseded note above; the same sentence appears there about the
# single-stage era). The symptom was visible on the live page: 97% of all rows
# were US or GB, both driven by bulk FILING sources, while the entire rest of
# the world had 467 rows and Israel had 15 - not because its feeds were broken
# but because its 9 items that run were competing with 1,018 others for 150
# slots.
#
# Worst case per month at the new defaults:
#   gate  1500 x 2/day x 30 x $0.00003  ~$2.70  (was ~$0.30)
#   full    60 x 2/day x 30 x $0.00128  ~$4.60  UNCHANGED - the readthrough cap
#           is what actually bounds the money, and it is untouched here.
#
# So this buys SELECTION, not throughput: the gate now screens everything the
# prefilter passed and the same 60 best get read, instead of 60 out of an
# arbitrary first-150. Raising READTHROUGH_CAP is the separate, genuinely
# expensive decision and is the owner's to make.
# The OpenRouter key's own limit still binds before any of this.
DEFAULT_CANDIDATE_CAP = 1500


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


def cluster_stories(items: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Story clustering, before anything is paid for (cost lever 2).

    URL dedup and the syndicated-title check catch verbatim copies; what
    survives them is the same round REWRITTEN by six outlets — six distinct
    URLs, six distinct headlines, one event, and until this existed six paid
    reads. Gate-survivors whose headlines state the same (employer, amount)
    are one story: read ONE representative, and the rest are not re-read.

    The key is deterministic (cheap_extract.cluster_key) and requires both
    the employer and the amount stated outright, so a false merge needs two
    different companies with colliding normalised names raising an identical
    stated amount inside one run's fetch window. Items whose headline states
    no (employer, amount) pair are never clustered.

    The representative is the member the deterministic extractor can close
    (the whole cluster then costs $0), else the one with the most text for
    the model.

    Two tiers. The STRICT key needs a validly named employer, so its set-aside
    copies are marked seen and never fetched again. The LOOSE key (final token
    before the verb — "…startup Fixxly raises $5.5 Mn", where the strict name
    rules rightly refuse the descriptor phrase) is only trusted within this
    run: its copies are set aside unmarked, so a false merge costs a deferred
    read, never a lost story.

    Returns (kept, removed_strict, removed_loose, clusters_formed).
    """
    strict: dict[tuple, list[int]] = {}
    loose: dict[tuple, list[int]] = {}
    for i, item in enumerate(items):
        key = cheap_extract.cluster_key(item)
        if key is not None:
            strict.setdefault(key, []).append(i)
            continue
        key = cheap_extract.loose_cluster_key(item)
        if key is not None:
            loose.setdefault(key, []).append(i)

    def representative(members: list[int]) -> int:
        rep = next((i for i in members
                    if cheap_extract.extract(items[i], count=False) is not None),
                   None)
        if rep is None:
            rep = max(members, key=lambda i: len(items[i].get("raw_text") or ""))
        return rep

    drop_strict: set[int] = set()
    drop_loose: set[int] = set()
    clusters = 0
    for groups, drop in ((strict, drop_strict), (loose, drop_loose)):
        for members in groups.values():
            if len(members) < 2:
                continue
            clusters += 1
            rep = representative(members)
            drop.update(i for i in members if i != rep)

    kept = [it for i, it in enumerate(items)
            if i not in drop_strict and i not in drop_loose]
    removed_strict = [items[i] for i in sorted(drop_strict)]
    removed_loose = [items[i] for i in sorted(drop_loose)]
    return kept, removed_strict, removed_loose, clusters


def run(*, dry_run: bool, offline: bool, run_index: int, limit: int | None,
        source: str = "google_news") -> int:
    conn = schema.connect()
    module = SOURCES.get(source, google_news)
    collector = module.COLLECTOR
    # Structured source: the fields are columns, so the `classified` half is
    # derived instead of generated. No model is called anywhere on this path.
    derive = getattr(module, "as_classified", None)

    if offline:
        items = _fixture_items()
    else:
        queries = build_queries(run_index, source)
        # The SEC collectors search filings by form and item, not by query
        # string, so reporting a query count for them is just misleading. A
        # derived source has no query vocabulary at all: the frame, the CSV or
        # the watchlist IS the population.
        if derive is not None:
            print(f"[{collector}] structured source, no search vocabulary")
        elif source.startswith("sec_"):
            print(f"[{collector}] searching SEC filings")
        elif source == "tripwire_chase":
            print(f"[{collector}] one targeted query per lead, from the work list")
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
            elif getattr(module, "ACCEPTS_DRY_RUN", False):
                # A collector that keeps state between runs must be told, or a
                # rehearsal consumes the very movement it is rehearsing.
                items = module.collect(queries, dry_run=dry_run)
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
    # A derived source is gated the same way and for the same reason: the
    # keyword filter exists to avoid PAYING to classify noise, and an XBRL
    # frame, a statutory pay return and a job-board diff are not news prose.
    skip_prefilter = source in ("sec_edgar", "sec_form_d") or derive is not None

    kept, filtered = [], 0
    for item in items:
        ok, reason = (True, "") if skip_prefilter else prefilter.passes(item.get("raw_text", ""))
        if ok:
            kept.append(item)
        else:
            filtered += 1
            print(f"  filtered  {item.get('headline','')[:60]}  ({reason})")

    # The cap is a MONEY cap. A derived source spends nothing, and applying it
    # would have thrown away five sixths of a year of exec-comp filings for a
    # cost that does not exist. An explicit --limit still applies.
    cap = limit or (None if derive else DEFAULT_CANDIDATE_CAP)
    if cap and len(kept) > cap:
        print(f"[{collector}] capping {len(kept)} candidates to {cap}, "
              f"one per query in turn")
        kept = fair_share(kept, cap)

    # Google News hands back an aggregator redirect and needs resolving. GDELT
    # already returns the publisher's own URL, which is the whole reason it
    # leads: a homepage is not a receipt.
    if not offline and source == "google_news":
        kept = [google_news.resolve_source_url(item) for item in kept]

    # Cost lever 2: one story, one read. Six outlets rewriting the same round
    # survive URL and title dedup as six candidates; cluster them on the
    # stated (employer, amount) and pay for one. Strict-tier copies are marked
    # seen so the next run does not fetch them back into the queue; loose-tier
    # copies stay unmarked (see cluster_stories). Never for a derived source:
    # its rows are structured facts, not stories.
    away_strict, away_loose, clusters_formed = [], [], 0
    if not derive:
        kept, away_strict, away_loose, clusters_formed = cluster_stories(kept)
    clustered_away = away_strict + away_loose

    stored = duplicates = rejected = skipped = throttled = budget_deferred = 0
    cheap_closed = known_rounds = 0
    print(f"\n[{collector}] {found} fetched, {filtered} filtered out, "
          f"{len(kept)} going to the classifier\n")
    if clusters_formed:
        print(f"[{collector}] clustering: {clusters_formed} stories seen from "
              f"multiple outlets, {len(clustered_away)} rewrites will not be "
              f"re-read ({len(away_loose)} held back this run only)\n")
        if not dry_run:
            for extra in away_strict:
                extra_url = extra.get("source_url") or extra.get("discovery_url") or ""
                if extra_url:
                    store.mark_seen(conn, extra_url, collector, "clustered")

    for item in kept:
        url = item.get("source_url") or item.get("discovery_url") or ""

        # Deduplicate BEFORE the LLM, never after (spec 4 rule 2).
        #
        # Except where the source URL is a standing page the collector revisits
        # on purpose. An ATS board lives at one URL forever and its signal is
        # the movement between two readings, so marking that URL seen would
        # make the first movement the last one this collector ever reported.
        # Those rows are deduped by content_hash and the fuzzy window instead.
        revisits = getattr(module, "REVISITS_ITS_SOURCE_URL", False)
        if url and not revisits and store.already_seen(conn, url):
            skipped += 1
            continue

        # Cost lever 2, across runs: a round we already stored, resurfacing
        # from yet another outlet days later, is recognisable from its stated
        # (employer, amount) before any model is paid. fuzzy_duplicate would
        # catch it too — after the read-through was bought.
        cheap = None
        if not derive:
            parsed = cheap_extract.parse_funding(item)
            if parsed is not None:
                if dedupe.funding_event_duplicate(
                        conn, parsed.company_key, parsed.amount_usd,
                        parsed.amount_canon):
                    known_rounds += 1
                    duplicates += 1
                    print(f"  SKIP    {item.get('headline','')[:66]}\n"
                          f"          round already stored, matched before any read")
                    if url and not dry_run:
                        store.mark_seen(conn, url, collector, "duplicate")
                    continue
            # Cost lever 1: when the headline/teaser states every field, the
            # record is built deterministically — no gate call, no read-
            # through, $0. extract() declines anything ambiguous, and its
            # output goes through the SAME validate/store path below, marked
            # on `notes` so a reader can see no model read it.
            cheap = cheap_extract.extract(item)

        try:
            if cheap is not None:
                classified = cheap
                cheap_closed += 1
            elif derive:
                classified = derive(item)
            else:
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
            # A model NO is a terminal verdict on this URL, so remember it.
            # Unmarked rejects were re-fetched, re-resolved and re-classified
            # on every run for the length of the recency window - the same
            # story billed at 2 runs/day for up to a week, while occupying a
            # candidate slot a fresh story needed (audit 2026-07-28, finding
            # 4: seen_urls held zero 'rejected' rows against a documented
            # outcome vocabulary that includes it). Throttled stays unmarked
            # above: busy is not a verdict.
            if url and not dry_run:
                store.mark_seen(conn, url, collector, "rejected")
            continue

        try:
            # `conn` is not optional in practice, whatever the signature says.
            # Without it the `identity.enrich()` call inside build_signal is a
            # no-op by design — "until a caller passes conn this line does
            # nothing at all" — and every collector and backfill in this repo
            # omitted it, so the identity spine has never once run on the
            # ingestion path, only in the offline backfill. That is how rows
            # reach the site with no ticker, no employer type and, worst of
            # all, no country in EITHER column, which makes them invisible to
            # every geographic filter (the site unions the two as
            # country_basis=any). Cache-only and network-free: this costs one
            # indexed lookup and cannot fail a record.
            signal = validate.build_signal(classified, item, collector, conn=conn)
        except validate.Rejected as exc:
            rejected += 1
            print(f"  REJECT  {item.get('headline','')[:70]}\n          {exc}")
            if url and not dry_run:
                store.mark_seen(conn, url, collector, "rejected")
            continue

        if cheap is not None:
            # The evidence marker: this row was parsed from stated text, no
            # model read it. Confidence is unchanged — the source is exactly
            # as credible either way and stays capped at "reported".
            signal.notes = cheap_extract.EVIDENCE_NOTE

        if dry_run:
            stored += 1
            if _should_print(stored):
                _print_signal(signal)
            continue

        outcome = store.store(conn, signal)
        store.mark_seen(conn, url, collector, outcome)
        if outcome == "stored":
            stored += 1
            if _should_print(stored):
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
    # Spend visibility, cheapest stage first. Every line here is money NOT
    # spent: deterministic closes and known rounds cost nothing at all,
    # clustered rewrites never reach the gate, gate rejects never reach the
    # read-through.
    if cheap_closed or known_rounds or clusters_formed:
        print(
            f"[{collector}] deterministic: {cheap_closed} closed with no "
            f"model call, {known_rounds} known rounds skipped pre-read, "
            f"{len(clustered_away)} outlet rewrites clustered away"
        )
    if classify.STATS["gate_calls"]:
        print(
            f"[{collector}] gate: {classify.STATS['gate_calls']} screened, "
            f"{classify.STATS['gate_rejects']} dropped cheap, "
            f"{classify.STATS['full_calls']} full read-throughs "
            f"(cap {classify.READTHROUGH_CAP}/run)"
        )
    if classify.STATS["full_calls"]:
        n = classify.STATS["full_calls"]
        print(
            f"[{collector}] read size: avg {classify.STATS['full_chars_sent'] // n} "
            f"chars sent of {classify.STATS['full_chars_raw'] // n} fetched "
            f"(cap {classify.FULL_READ_CHARS})"
        )
    if classify.STATS["prompt_tokens"]:
        cached = classify.STATS["cached_tokens"]
        total = classify.STATS["prompt_tokens"]
        line = (f"[{collector}] tokens: {total} prompt "
                f"({cached} cached, {cached * 100 // max(total, 1)}%), "
                f"{classify.STATS['completion_tokens']} completion")
        if classify.STATS["usd"]:
            line += f", ${classify.STATS['usd']:.4f} this run"
        print(line)

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

    # A DIFF-shaped collector emits a row only when something MOVED, so counting
    # emitted rows as `items_found` marks a perfectly healthy quiet day
    # `degraded` (store.report_health downgrades any zero) every single day,
    # until nobody reads the health page any more. Such a collector publishes
    # what it actually READ as LAST_RUN['read'], and health is measured on that.
    # Nothing changes for any other source: there the two are the same number,
    # and a run that reads nothing is still degraded.
    observed = (getattr(module, "LAST_RUN", None) or {}).get("read")
    observed = found if observed is None else observed
    broken = observed == 0 or everything_rejected or mostly_throttled

    store.report_health(
        conn, collector,
        status="degraded" if broken else "ok",
        items_found=observed, items_stored=stored,
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


# A news run stores a dozen rows and every one of them is worth reading. A
# structured backfill stores thousands, and printing five lines each pushes the
# step log past what GitHub keeps, which costs the run its diagnostics at
# exactly the moment they matter. Full detail for the first hundred, a heartbeat
# after that.
PRINT_IN_FULL = 100


def _should_print(stored: int) -> bool:
    return stored <= PRINT_IN_FULL or stored % 250 == 0


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
    parser.add_argument("--source", choices=sorted(SOURCES),
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

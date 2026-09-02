"""Every rotating ring is walked at the cadence the workflow ACTUALLY runs.

A rotation picks its slice with `(day * runs_per_day + run_index) * per_run`,
so the run counter it is handed decides the stride. When the counter is a
literal that nothing ties to the cron, a cadence change moves the stride
without moving the slice size, and a stride that shares a factor with the ring
size is not slower coverage, it is permanent loss: at one real run a day
against a hard-coded two, Canada's 12-city ring at 3 a run reached 6 cities
and never Toronto, Italy's and Poland's 6-city rings reached 3, and the
56-term segment matrix reached 28. Nothing reported it, because a query that
is never issued produces no error, no health row and no log line. The sibling
tracker lost half its euphemism ring the same way (its TECHLOG, 2026-08-20).

So the counter is READ from collect.yml here, the run index is derived the
way the workflow's own `Pick run index` step derives it (UTC hour >= 12 is 1,
else 0), and every shipped ring is walked for 400 days at that cadence. One
unreached term is a failure. A ring reached only at some OTHER cadence is the
defect this file exists to catch, so the walk never asks about a cadence the
schedule does not run.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import source_registry as registry
import run_collect

ROOT = Path(__file__).resolve().parent.parent
COLLECT_YML = ROOT / ".github" / "workflows" / "collect.yml"
DAYS = 400


def _live_crons(path: Path) -> list[str]:
    """Uncommented `- cron:` lines only. A `#   - cron:` line is prose."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- cron:"):
            out.append(stripped.split(":", 1)[1].split("#")[0].strip().strip("'\""))
    return out


def _run_indexes_for(crons: list[str]) -> list[int]:
    """The run_index each live cron produces under the workflow's own rule."""
    indexes = []
    for expr in crons:
        fields = expr.split()
        assert len(fields) == 5, expr
        hour_field = fields[1]
        assert re.fullmatch(r"\d{1,2}", hour_field), (
            f"collect.yml cron {expr!r} has an hour field this test cannot map "
            f"to the workflow's run-index rule; extend _run_indexes_for")
        indexes.append(1 if int(hour_field) >= 12 else 0)
    return indexes


def _workflow_rule_text() -> str:
    return COLLECT_YML.read_text(encoding="utf-8")


def _reached(ring, per_run, runs_per_day, run_indexes) -> dict:
    visits = collections.defaultdict(list)
    for day in range(1, DAYS + 1):
        for run_index in run_indexes:
            for term in registry.rotate(ring, day_of_year=day, run_index=run_index,
                                        runs_per_day=runs_per_day, per_run=per_run):
                visits[term].append(day)
    return visits


def test_the_run_counter_is_read_from_the_schedule_not_typed():
    crons = _live_crons(COLLECT_YML)
    assert crons, "collect.yml carries no live cron; the rotation has no cadence"
    assert run_collect.RUNS_PER_DAY == len(crons), (
        f"run_collect.RUNS_PER_DAY is {run_collect.RUNS_PER_DAY} but collect.yml "
        f"fires {len(crons)} time(s) a day ({crons}). The rotation strides by "
        f"this number; a typed value that disagrees with the cron is how half "
        f"a ring goes unqueried with every run green.")
    assert run_collect.scheduled_runs_per_day(ROOT / "no-such-file.yml") == 1, (
        "an unreadable schedule must fall back to ONE run a day: repeating a "
        "slice is safe and skipping one is not")


def test_the_run_index_rule_in_this_file_matches_the_workflow():
    """The workflow derives run_index from the UTC hour. Pin that shape here so
    a workflow that changes the rule turns this walk red instead of walking a
    cadence the runner does not use."""
    text = _workflow_rule_text()
    assert 'if [ "$hour" -ge 12 ]' in text and 'echo "value=1"' in text, (
        "collect.yml's Pick run index step no longer reads `hour -ge 12 -> 1`; "
        "update _run_indexes_for to the new rule")


def test_every_locale_is_reached_and_the_window_covers_the_revisit_gap():
    crons = _live_crons(COLLECT_YML)
    runs_per_day, indexes = run_collect.RUNS_PER_DAY, _run_indexes_for(crons)
    ring = list(registry.GOOGLE_NEWS_LOCALES)
    visits = _reached(ring, run_collect.LOCALES_PER_RUN, runs_per_day, indexes)
    missing = [t for t in ring if t not in visits]
    assert not missing, f"editions never queried at the real cadence: {missing}"
    # A locale query carries `when:Nd`. If an edition's turn comes round less
    # often than N days, stories age out of the window between visits and the
    # market reads as quiet. The window is derived from the same counter, so
    # the two must agree at the cadence that actually runs.
    gaps = [b - a for days in visits.values() for a, b in zip(days, days[1:])]
    window = registry.recency_window_days(run_collect.LOCALES_PER_RUN, runs_per_day)
    assert max(gaps) < window, (
        f"an edition waits up to {max(gaps)} days between visits but its query "
        f"asks for when:{window}d, a {max(gaps) - window}-day hole nothing reports")


def test_every_city_of_every_edition_is_reached():
    crons = _live_crons(COLLECT_YML)
    runs_per_day, indexes = run_collect.RUNS_PER_DAY, _run_indexes_for(crons)
    editions = [registry.GOOGLE_NEWS_ANCHOR] + list(registry.GOOGLE_NEWS_LOCALES)
    unreached = {}
    for _lang, country in editions:
        cities = list(registry.gazetteer_cities().get(country, ()))
        if not cities:
            continue
        visits = _reached(cities, registry.CITY_QUERIES_PER_EDITION,
                          runs_per_day, indexes)
        missing = [c for c in cities if c not in visits]
        if missing:
            unreached[country] = missing
    assert not unreached, (
        f"city terms never queried at the real cadence: {unreached}. Do not "
        f"answer this by changing CITY_QUERIES_PER_EDITION; the stride is the "
        f"run counter, and the counter is read from collect.yml")


def test_every_segment_is_reached():
    crons = _live_crons(COLLECT_YML)
    runs_per_day, indexes = run_collect.RUNS_PER_DAY, _run_indexes_for(crons)
    ring = registry.build_segments()
    visits = _reached(ring, run_collect.SEGMENTS_PER_RUN, runs_per_day, indexes)
    missing = [t for t in ring if t not in visits]
    assert not missing, f"segments never rotated in at the real cadence: {missing[:8]}"

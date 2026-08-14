"""The committed ingest schedule must match the REAL cron, forever.

data/ingest-schedule.json powers the dashboard strip's "Next run ..." promise
(Roo's line) and the FAQ's cadence sentence. It is generated from
`.github/workflows/collect.yml` by generate_ingest_schedule.py; these tests
fail the build if either side moves without the other, so the public promise
can never drift from the schedule that actually runs. The defect this closes:
on 2026-08-14 the strip still promised "Next run Aug 15, 6:00 AM UTC" after
the collect cron moved to 16:00 UTC (df0efdf), because the hours were typed
into tit_next_run() and the FAQ rather than derived.
"""
import json
import re
from pathlib import Path

import pytest

from generate_ingest_schedule import OUT, WORKFLOW, parse_cron_schedule

ROOT = Path(__file__).resolve().parent.parent
SHORTCODES = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
              / "includes" / "shortcodes.php")


def test_committed_json_matches_collect_yml():
    expected = parse_cron_schedule(WORKFLOW.read_text(encoding="utf-8"))
    assert OUT.exists(), ("data/ingest-schedule.json is missing; run "
                          "python3 generate_ingest_schedule.py")
    committed = json.loads(OUT.read_text(encoding="utf-8"))
    assert expected == committed, (
        "ingest-schedule.json drifted from collect.yml; regenerate with "
        "python3 generate_ingest_schedule.py")


def test_schedule_shape_is_sane():
    committed = json.loads(OUT.read_text(encoding="utf-8"))
    hours = committed["utc_hours"]
    assert hours, "schedule must carry at least one UTC hour"
    assert hours == sorted(set(hours))
    for h in hours:
        assert 0 <= h <= 23
    assert 0 <= committed["utc_minute"] <= 59


def test_no_typed_run_hours_left_in_the_plugin():
    """tit_next_run() must derive its hours from tit_ingest_schedule()
    (data/ingest-schedule.json), never a typed list, and the FAQ's cadence
    sentence must not carry hand-written clock times either. Typed hours are
    exactly what promised a 6:00 AM run the cron no longer honoured."""
    php = SHORTCODES.read_text(encoding="utf-8")
    assert "array(6, 18)" not in php, (
        "tit_next_run() has a typed hour list again; read tit_ingest_schedule()")
    assert "06:00 and 18:00" not in php, (
        "the FAQ carries typed run times again; derive them from the schedule")
    assert "tit_ingest_schedule" in php, (
        "the plugin no longer reads data/ingest-schedule.json")


def test_a_missing_schedule_renders_nothing_rather_than_a_guess():
    """The PHP contract: no readable schedule file means no next-run promise
    and no cadence claim. Asserted textually, the way this suite checks PHP:
    tit_next_run() must return 0 (the strip omits the note) when
    tit_ingest_schedule() returns null."""
    php = SHORTCODES.read_text(encoding="utf-8")
    body = php.split("function tit_next_run()", 1)[1].split("\nfunction ", 1)[0]
    assert "tit_ingest_schedule()" in body
    assert re.search(r"if\s*\(\s*!\s*\$\w+\s*\)\s*return\s+0\s*;", body), (
        "tit_next_run() must return 0 without a schedule, never guess an hour")


def test_the_staleness_leash_matches_the_real_cadence():
    """A 14-hour leash on a once-daily cron is permanent noise: every source
    reads stale for hours before its next scheduled run, which trains the
    reader to ignore the health page (the sibling learned this on a 2-day
    ceiling over a weekly job). The leash for collect.yml's sweep must cover
    the real gap between runs, plus slack, and not multiples of it."""
    import staleness

    schedule = parse_cron_schedule(WORKFLOW.read_text(encoding="utf-8"))
    hours = schedule["utc_hours"]
    gaps = ([24] if len(hours) == 1 else
            [(b - a) % 24 for a, b in zip(hours, hours[1:] + [hours[0] + 24])])
    widest_gap = max(gaps)
    leash = staleness.MAX_AGE_HOURS["google_news"]
    assert leash >= widest_gap + 1, (
        f"the {leash}h leash is shorter than the {widest_gap}h gap between "
        f"scheduled runs; every run window ends in phantom staleness")
    assert leash <= widest_gap * 2, (
        "the leash hides a whole missed run; tighten it to cadence plus slack")


@pytest.mark.parametrize("bad", [
    "on:\n  schedule:\n    - cron: '0 16 * * 1'\n",       # not every day
    "on:\n  schedule:\n    - cron: '*/30 16 * * *'\n",    # minute pattern
    "on:\n  workflow_dispatch: {}\n",                      # no cron at all
])
def test_a_shape_the_summary_cannot_promise_raises_rather_than_guesses(bad):
    with pytest.raises(ValueError):
        parse_cron_schedule(bad)

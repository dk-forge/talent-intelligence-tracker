# -*- coding: utf-8 -*-
"""The read ration follows measured conversion, and its total does not move.

THE DEFECT (measured 2026-08-01, source_health, the seven days to that date)

    collector        runs  items found  candidates  reads  rows  conversion
    google_news         6        6,870       3,892    761   354       46.5%
    national_press      2       21,158       1,160    288   160       55.6%
    gdelt               4          967         106     62    26       41.9%
    sec_edgar           3           30          11     12     5       41.7%
    sec_form_d          3           23           8      6     4       66.7%

The per-run ceilings behind those were google_news 129 (a bash `case` in
collect.yml) and national_press 88 (pipeline/classify.py's module default,
because nothing set one for it). So the collector converting a read into a
stored row LESS often, off a third as many items read, held the larger ration.
Nobody chose that: the two numbers were set in different files, months apart.

THE RULE is now in code with its arithmetic (classify.READ_CONVERSION,
BINDING_READ_BUDGET, read_cap), and these tests are what stop it drifting back
into a pair of numbers.
"""

from __future__ import annotations

import pytest

from pipeline import classify


def test_the_split_spends_exactly_what_the_two_caps_already_bought():
    """THE constraint. The obvious way to fix a starved collector is to give it
    more, and the obvious way to do that is to raise the total. This is a
    REALLOCATION: 129 + 88 in, 217 out, and MONTHLY_ALLOWANCE_USD untouched."""
    assert sum(classify.COLLECTOR_READ_CAPS.values()) == classify.BINDING_READ_BUDGET
    assert classify.BINDING_READ_BUDGET == 129 + 88


def test_the_ration_is_ordered_by_measured_conversion():
    """The rule in one assertion: better conversion, bigger share. A change
    that leaves this passing while inverting the numbers is not possible."""
    by_cap = sorted(classify.COLLECTOR_READ_CAPS,
                    key=lambda n: classify.COLLECTOR_READ_CAPS[n], reverse=True)
    by_conversion = sorted(classify.READ_CONVERSION,
                           key=lambda n: classify.READ_CONVERSION[n], reverse=True)
    assert by_cap == by_conversion


def test_the_arithmetic_is_reproducible_from_the_two_constants():
    """Recomputed here from the documented inputs rather than compared against
    hard-coded 99/118, so editing a conversion figure moves the caps and this
    test follows rather than fighting it."""
    total = sum(classify.READ_CONVERSION.values())
    for name, share in classify.READ_CONVERSION.items():
        exact = classify.BINDING_READ_BUDGET * share / total
        assert abs(classify.COLLECTOR_READ_CAPS[name] - exact) < 1.0, name


def test_the_starved_collector_is_the_one_that_gains():
    """national_press deferred 162 candidates on its last run against a cap of
    88; google_news deferred 12 against 129. The direction is the whole point,
    so it is asserted rather than left to the arithmetic."""
    assert classify.COLLECTOR_READ_CAPS["national_press"] > 88
    assert classify.COLLECTOR_READ_CAPS["google_news"] < 129


def test_a_collector_with_no_ration_keeps_the_module_default():
    """A cap only rations a collector whose demand reaches it. sec_edgar bought
    2 reads against a ceiling of 40 and sec_form_d bought 1 against 40; taking
    headroom from a source that never uses it frees no money and would look
    like a saving."""
    assert classify.read_cap("sec_edgar") == classify.READTHROUGH_CAP
    assert classify.read_cap("") == classify.READTHROUGH_CAP
    assert classify.read_cap(None) == classify.READTHROUGH_CAP


def test_an_explicit_env_cap_still_wins(monkeypatch):
    """The backfills set TIT_READTHROUGH_CAP=5000 because a month of filings
    would otherwise defer almost entirely. A derived daily ration silently
    overriding an explicitly requested one is exactly the class of surprise
    this repo keeps paying for: explicit beats derived, derived beats default.
    """
    monkeypatch.setenv("TIT_READTHROUGH_CAP", "5000")
    monkeypatch.setattr(classify, "READTHROUGH_CAP", 5000)
    assert classify.read_cap("national_press") == 5000
    assert classify.read_cap("google_news") == 5000


def test_the_ranker_is_told_the_ceiling_the_run_will_actually_hit():
    """`candidate_rank.explain` is what a reader uses to judge whether a capped
    run bought breadth. One describing 88 reads while the run buys 118
    describes a run that did not happen."""
    import inspect

    import run_collect

    src = inspect.getsource(run_collect)
    assert "top=classify.read_cap(source)" in src
    assert "top=classify.READTHROUGH_CAP" not in src


def test_ops_status_can_see_the_schedules_own_numbers():
    """TIT_READTHROUGH_CAP in a workflow beats the derived value, so the two
    can disagree and the disagreement is invisible from either side: the code
    looks right and the run buys something else. ops_status is where it becomes
    visible, and it reads the workflow rather than keeping a constant that
    describes one."""
    import ops_status

    scheduled = ops_status._scheduled_read_caps()
    assert scheduled, "ops_status can no longer read collect.yml's per-source caps"
    assert "google_news" in scheduled
    # Both halves of a `sec_edgar|sec_form_d)` arm have to be picked up, or the
    # check would silently skip whichever was written second.
    assert "sec_edgar" in scheduled and "sec_form_d" in scheduled


def test_this_ration_does_not_replace_the_country_round_robin():
    """Two different questions. This decides HOW MANY reads a collector may
    buy; candidate_rank decides WHICH, giving every country's best story a
    place before any country's second. A change that made this the only answer
    would put every read in one market."""
    from pipeline import candidate_rank

    assert hasattr(candidate_rank, "rank")
    assert hasattr(candidate_rank, "explain")

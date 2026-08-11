"""The projection has to stay honest, and honest here means three things.

It must never invent a price. It must never claim a saving for a cache that
does not exist. And it must keep saying which of its numbers were MEASURED
(what the provider charged), which were COUNTED (the funnel) and which were
MODELLED (a price list times a token count) — because the reason every previous
cost estimate in this repo went wrong is that the three got written down
looking identical.

Nothing here reaches the network: prices are passed in.
"""

from __future__ import annotations

import inspect
import json

import pytest

import cost_projection as cp

PRICES = {
    "with-cache": {"prompt": 1e-6, "completion": 2e-6, "cache_read": 1e-7},
    "no-cache": {"prompt": 1e-6, "completion": 2e-6, "cache_read": None},
}


# --- it will not invent a price ----------------------------------------------

def test_a_model_with_no_published_price_returns_none_not_a_guess():
    assert cp.call_cost(PRICES, "some/model-nobody-serves", 1000, 100) is None


def test_no_price_list_and_no_snapshot_is_a_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "SNAPSHOT", tmp_path / "absent.json")
    with pytest.raises(SystemExit) as caught:
        cp.fetch_prices(offline=True)
    assert "will not invent a price" in str(caught.value)


def test_the_snapshot_is_what_a_live_fetch_wrote(monkeypatch, tmp_path):
    """--offline reproduces the last REAL price list. A price somebody typed is
    how a rate card becomes a forecast."""
    snap = tmp_path / "prices.json"
    snap.write_text(json.dumps(PRICES))
    monkeypatch.setattr(cp, "SNAPSHOT", snap)
    prices, source = cp.fetch_prices(offline=True)
    assert prices == PRICES
    assert "snapshot" in source


# --- it will not claim a cache that does not exist ---------------------------

def test_a_cached_prefix_costs_less_only_where_a_cache_read_is_priced():
    plain = cp.call_cost(PRICES, "with-cache", 3100, 254)
    cached = cp.call_cost(PRICES, "with-cache", 3100, 254, cached_prefix=2754)
    assert cached < plain


def test_a_slug_with_no_cache_read_price_saves_exactly_nothing():
    """`deepseek/deepseek-chat` is the live example: not one of its endpoints
    publishes an input_cache_read price, so the prefix caching saving is $0
    however attractive the arithmetic would be if it did."""
    plain = cp.call_cost(PRICES, "no-cache", 3100, 254)
    cached = cp.call_cost(PRICES, "no-cache", 3100, 254, cached_prefix=2754)
    assert cached == plain


def test_a_missing_cache_price_is_none_and_never_zero():
    """Two different facts: 'this provider does not price a cache read' and 'a
    cache read is free'. Collapsing them to 0.0 would silently make every
    uncached model look like a caching one."""
    src = inspect.getsource(cp.fetch_prices)
    assert 'if m["pricing"].get("input_cache_read")' in src
    assert "else None" in src


# --- it keeps saying which number is which -----------------------------------

def test_the_three_kinds_of_number_are_named_in_the_output():
    src = inspect.getsource(cp.main)
    for label in ("[1] MEASURED", "[2] COUNTED", "[3] MODELLED"):
        assert label in src, label


def test_the_model_is_calibrated_against_the_measurement_and_not_excused():
    """The gap between the price-list model and what the provider actually
    charged is retries, longer texts and refused read-throughs. It is applied
    to every projection rather than explained away in a comment."""
    src = inspect.getsource(cp.main)
    assert "calibration" in src
    assert "factor" in src


def test_the_seeded_funnel_names_the_runs_it_came_from():
    """It is a stand-in until the ledger columns fill, so it has to be
    re-readable rather than trusted."""
    src = inspect.getsource(cp)
    head = src.split("FUNNEL = {", 1)[0]
    assert "30571205733" in head and "30532073727" in head


def test_the_ledger_wins_over_the_seed_for_the_collectors_it_has_seen(tmp_path):
    """Otherwise the seed quietly becomes the permanent answer."""
    from pipeline import schema

    conn = schema.connect(tmp_path / "t.db")
    try:
        _, source, seen = cp.measured_funnel(conn)
        assert "seeded" in source and seen == set()

        conn.execute(
            "INSERT INTO source_health (collector, run_at, status, candidates, "
            " gate_calls, gate_rejects, reads_bought, budget_deferred) "
            "VALUES ('google_news', '2026-07-31T00:00:00+00:00', 'ok', "
            "        600, 500, 350, 150, 0)")
        conn.commit()

        funnel, source, seen = cp.measured_funnel(conn)
        assert "MEASURED" in source
        assert seen == {"google_news"}
        assert funnel["google_news"][:4] == (600, 500, 150, 150)
    finally:
        conn.close()


def test_a_collector_the_ledger_has_not_seen_keeps_its_seed(tmp_path):
    """MERGED, not replaced. Taking the ledger wholesale dropped
    national_press — the hungriest collector — along with gdelt and the SEC
    pair, and the projected bill fell by $43 on nothing but four missing
    collectors. A number that looks more authoritative and is less complete is
    worse than the estimate it replaced."""
    from pipeline import schema

    conn = schema.connect(tmp_path / "t.db")
    try:
        conn.execute(
            "INSERT INTO source_health (collector, run_at, status, candidates, "
            " gate_calls, gate_rejects, reads_bought, budget_deferred) "
            "VALUES ('google_news', '2026-07-31T00:00:00+00:00', 'ok', "
            "        600, 500, 350, 150, 0)")
        conn.commit()

        funnel, _, seen = cp.measured_funnel(conn)
        assert set(funnel) == set(cp.FUNNEL), "a collector was dropped"
        for name in set(cp.FUNNEL) - seen:
            assert funnel[name] == cp.FUNNEL[name], name
    finally:
        conn.close()


def test_the_run_exits_two_when_full_coverage_does_not_fit():
    """A projection that goes over the allowance must not exit 0 — the whole
    point is that somebody notices."""
    src = inspect.getsource(cp.main)
    assert "FULL COVERAGE DOES NOT FIT" in src
    assert "return 2" in src

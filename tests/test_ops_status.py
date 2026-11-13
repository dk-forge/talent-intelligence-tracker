"""ops_status is the first thing every session runs, so what it escalates
matters as much as what it prints.

The rule these tests pin: a market may only claim structured coverage while a
structured collector actually backs it, and the two ways that claim can go
wrong are NOT the same failure.

  the collector ran and stored nothing for the market   an incident
  the collector has never run in this checkout          a database behind main

Escalating the second would mean every fresh clone and every branch cut before
the next scheduled run starts with ACTION NEEDED, which is how a session learns
to skip the one report it should read.
"""

import ops_status
import source_registry as registry
from pipeline import schema, store


def _db(tmp_path):
    return schema.connect(tmp_path / "ops.db")


def _promoted(*live_sources):
    return (
        registry.Market("GB", "United Kingdom", registry.STRUCTURED_OFFICIAL,
                        live_sources=live_sources),
    )


def test_a_promoted_market_whose_collector_ran_and_stored_nothing_is_escalated(
        tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path)
    store.report_health(conn, "uk_paygap", status="ok", items_found=11153,
                        items_stored=0)
    monkeypatch.setattr(registry, "MARKETS", _promoted("google_news", "uk_paygap"))

    problems = ops_status._report_coverage(conn)
    capsys.readouterr()

    assert len(problems) == 1
    assert "uk_paygap" in problems[0] and "GB" in problems[0]
    conn.close()


def test_a_promoted_market_whose_collector_has_never_run_is_printed_not_escalated(
        tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path)
    monkeypatch.setattr(registry, "MARKETS", _promoted("google_news", "uk_paygap"))

    problems = ops_status._report_coverage(conn)
    printed = capsys.readouterr().out

    assert problems == []
    assert "not yet run here" in printed
    conn.close()


def test_news_discovery_alone_cannot_back_a_structured_claim(
        tmp_path, monkeypatch, capsys):
    """google_news is what every market already has. Counting it here would let
    a promoted market look backed by structured data it does not hold."""
    conn = _db(tmp_path)
    store.report_health(conn, "google_news", status="ok", items_found=140,
                        items_stored=27)
    monkeypatch.setattr(registry, "MARKETS", _promoted("google_news", "uk_paygap"))

    ops_status._report_coverage(conn)
    printed = capsys.readouterr().out

    assert "google_news" not in printed.split("[3]")[1]
    conn.close()

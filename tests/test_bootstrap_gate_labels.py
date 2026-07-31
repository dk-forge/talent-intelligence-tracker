"""The weak historical label set.

The one property that decides whether this file is useful or actively harmful:
**the two classes must carry the SAME features**. Positives could have their
real headline (`signals` holds it) and negatives could not (`seen_urls` holds
only a URL), and a classifier handed that asymmetry separates them on field
shape alone — a perfect score that means nothing. So both classes get the URL
slug and nothing else, and these tests pin that.
"""

import json
import sqlite3

import pytest

import bootstrap_gate_labels as bootstrap
from pipeline import gate_ledger


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE seen_urls (url TEXT PRIMARY KEY, first_seen TEXT,"
                 " collector TEXT, outcome TEXT)")
    rows = [
        ("https://www.finsmes.com/2026/07/vector-legal-closes-seed-funding-round.html",
         "2026-07-20T10:00:00+00:00", "google_news", "stored"),
        ("https://betakit.com/why-is-amazon-opening-a-disaster-relief-hub-near-edmonton/",
         "2026-07-21T10:00:00+00:00", "google_news", "rejected"),
        ("https://www.luxtimes.lu/luxembourg/travellers-advised-to-get-there-early/159379267.html",
         "2026-07-22T10:00:00+00:00", "national_press", "rejected"),
        # No slug a human could read: dropped, and dropped from both classes.
        ("https://www.sec.gov/Archives/edgar/data/1819142/000181914226000019/ses-20260423x8k.htm",
         "2026-07-23T10:00:00+00:00", "sec_edgar", "stored"),
        # Ambiguous outcome: a real signal we already held. Excluded, not guessed.
        ("https://example.com/acme-raises-a-huge-series-b-round/",
         "2026-07-24T10:00:00+00:00", "google_news", "duplicate"),
        # A derived collector never calls the gate, so it is not this
        # population however many rows it stored.
        ("https://gender-pay-gap.service.gov.uk/employers/6731/reporting-year-2020",
         "2026-07-25T10:00:00+00:00", "uk_paygap", "stored"),
    ]
    conn.executemany("INSERT INTO seen_urls VALUES (?, ?, ?, ?)", rows)
    return conn


def test_both_classes_carry_exactly_the_same_features(conn):
    labels, _ = bootstrap.build(conn)
    positives = [l for l in labels if l["outcome"] == "stored"]
    negatives = [l for l in labels if l["outcome"] == "rejected"]
    assert positives and negatives

    assert {tuple(sorted(l)) for l in labels} == {tuple(sorted(positives[0]))}
    for label in labels:
        assert label["teaser"] == ""
        assert label["lang"] == "" and label["country"] == ""
        assert label["basis"] == gate_ledger.BASIS_URL_SLUG
        assert label["weak"] is True


def test_the_gate_verdict_is_recorded_as_unrecoverable(conn):
    """A gate NO and an extraction NO both landed in seen_urls as 'rejected'.
    Guessing which was which is exactly what makes this set unshippable, so it
    says UNKNOWN instead."""
    labels, _ = bootstrap.build(conn)
    assert {l["gate"] for l in labels} == {"UNKNOWN"}


def test_unreadable_slugs_and_derived_collectors_are_left_out(conn):
    labels, tally = bootstrap.build(conn)
    hosts = {l["host"] for l in labels}
    assert "www.sec.gov" not in hosts            # accession numbers, no slug
    assert "gender-pay-gap.service.gov.uk" not in hosts   # never gated
    assert "example.com" not in hosts            # duplicate: ambiguous target
    assert tally["dropped_no_slug"] == 1
    assert tally["stored"] == 1 and tally["rejected"] == 2


def test_a_slug_becomes_a_readable_pseudo_headline():
    assert bootstrap.slug_text(
        "https://www.finsmes.com/2026/07/vector-legal-closes-seed-round.html"
    ) == "vector legal closes seed round"
    # The headline segment wins over the taxonomy and the numeric id.
    assert bootstrap.slug_text(
        "https://www.luxtimes.lu/luxembourg/travellers-advised-to-get-there-early/159379267.html"
    ) == "travellers advised to get there early"
    # Too few words to be anything: dropped rather than guessed at.
    assert bootstrap.slug_text("https://www.sec.gov/Archives/edgar/data/1/2/x8k.htm") == ""
    assert bootstrap.slug_text("https://example.com/") == ""


def test_the_key_joins_to_the_live_ledger(conn):
    """Same key function as pipeline/gate_ledger, so a weak row and a real one
    for the same URL are recognisably the same candidate."""
    labels, _ = bootstrap.build(conn)
    url = "https://betakit.com/why-is-amazon-opening-a-disaster-relief-hub-near-edmonton/"
    keys = {l["key"] for l in labels}
    assert gate_ledger.key({"source_url": url}) in keys


def test_the_committed_set_is_labelled_weak_on_every_line():
    """The file that actually ships in the repo, not a fixture. A training
    script that globs data/gate_labels/ must be able to see what this is."""
    import os
    path = os.path.join(gate_ledger.LEDGER_DIR, bootstrap.OUT_NAME)
    if not os.path.exists(path):          # a checkout that has not built it
        pytest.skip("bootstrap-weak.jsonl not present")
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            row = json.loads(raw)
            assert row["weak"] is True
            assert row["basis"] == gate_ledger.BASIS_URL_SLUG
            assert row["gate"] == "UNKNOWN"

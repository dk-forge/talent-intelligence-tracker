"""The gate-label ledger: the training set for the classifier gate.

Three properties are load-bearing and each has a test that would fail loudly if
somebody changed the module's mind about them:

  * a gate NO is TERMINAL — nothing downstream may relabel it, because gate
    rejects are the one class the classifier cannot get anywhere else;
  * the ledger can NEVER fail a collect run, whatever the filesystem does;
  * no line carries more than the gate itself saw.
"""

import gzip
import json
from datetime import datetime, timezone

import pytest

from pipeline import classify, gate_ledger


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(gate_ledger, "DRY_RUN", False)
    gate_ledger.reset()
    yield tmp_path
    gate_ledger.reset()


# A real google_news item, verbatim in shape: the body is the headline again,
# wrapped in an anchor around a base64 aggregator URL.
GNEWS = {
    "source_url": "https://www.inc.com/story/leaders-regret-ai-layoffs",
    "discovery_url": "https://news.google.com/rss/articles/CBMivAFBVV95cUxO",
    "headline": "Enigma Raises $71M in Seed Funding - inc.com",
    "raw_text": (
        "Enigma Raises $71M in Seed Funding - inc.com\n\n"
        '<a href="https://news.google.com/rss/articles/CBMivAFBVV95cUxOX3JWVGtv'
        'YXN6YktXQTN1WVZWUi1TWHhQU1VTTGJkb1g2N19nWmpNYUNOSGpkOUxMWEpMYTF4Mlcz" '
        'target="_blank">Enigma Raises $71M in Seed Funding</a>&nbsp;&nbsp;'
        '<font color="#6f6f6f">inc.com</font>'
    ),
    "locale": "US:en",
    "collector": "google_news",
}

PRESS = {
    "source_url": "https://www.luxtimes.lu/luxembourg/firm-opens-dublin-hub/1.html",
    "headline": "Stripe opens Dublin engineering hub",
    "raw_text": ("Stripe opens Dublin engineering hub\n\nThe company said it "
                 "would add 300 engineering roles at a new Dublin site over "
                 "2026, its largest intake in the city this year.\n\n"
                 "Luxembourg Times"),
    "language": "en",
    "source_country": "LU",
    "collector": "national_press",
}


def _lines(directory):
    path = gate_ledger.shard_path(gate_ledger.month())
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def test_one_line_per_gate_decision_with_the_outcome_joined(ledger):
    gate_ledger.record(GNEWS, "google_news", gate_ledger.YES)
    gate_ledger.outcome(GNEWS, "stored")
    assert gate_ledger.flush() == 1

    (line,) = _lines(ledger)
    assert line["gate"] == "YES"
    assert line["outcome"] == "stored"
    assert line["collector"] == "google_news"
    assert line["host"] == "www.inc.com"
    assert line["lang"] == "en"
    assert line["country"] == "US"
    assert line["headline"] == "Enigma Raises $71M in Seed Funding - inc.com"
    assert line["basis"] == gate_ledger.BASIS_GATE_TEXT
    # The join key is derivable from the item alone, by both sides.
    assert line["key"] == gate_ledger.key(GNEWS)


def test_the_key_uses_run_collects_own_url_precedence(ledger):
    """`run_collect` dedupes on `source_url or discovery_url`. If the ledger
    keyed on anything else the outcome would attach to the wrong line."""
    assert gate_ledger.key(GNEWS) == gate_ledger.key(
        {"source_url": GNEWS["source_url"]})
    assert gate_ledger.key({"discovery_url": "x"}) == gate_ledger.key(
        {"discovery_url": "x", "headline": "different headline"})


def test_a_gate_no_is_terminal_and_cannot_be_relabelled(ledger):
    """run_collect sees a gate NO and an extraction NO as the same `None`, so
    it offers "model_reject" for both. The gate reject must survive that."""
    gate_ledger.record(PRESS, "national_press", gate_ledger.NO)
    gate_ledger.outcome(PRESS, "model_reject")
    gate_ledger.flush()

    (line,) = _lines(ledger)
    assert line["gate"] == "NO"
    assert line["outcome"] == "gate_reject"


def test_a_fail_open_gate_is_not_recorded_as_a_yes(ledger):
    """`classify.gate` returns True when the provider is busy. A YES there
    would teach the classifier that outages are talent signals."""
    gate_ledger.record(GNEWS, "google_news", gate_ledger.ERROR)
    gate_ledger.flush()
    (line,) = _lines(ledger)
    assert line["gate"] == "ERROR"
    assert line["outcome"] == "unknown"


@pytest.fixture
def stats():
    """classify.STATS is module state shared by the whole suite, and the counter
    tests elsewhere assert on absolute values. A test that calls the gate must
    hand it back exactly as it found it."""
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


def test_gate_still_returns_a_bool_and_still_fails_open(monkeypatch, stats):
    """The refactor that produced `gate_verdict` must not have changed the
    routing decision for any existing caller."""
    monkeypatch.setattr(classify, "_call", lambda *a, **k: "YES")
    assert classify.gate("anything") is True
    monkeypatch.setattr(classify, "_call", lambda *a, **k: "NO")
    assert classify.gate("anything") is False

    def busy(*a, **k):
        raise classify.Throttled("provider busy")

    monkeypatch.setattr(classify, "_call", busy)
    assert classify.gate("anything") is True
    assert classify.gate_verdict("anything") == gate_ledger.ERROR


def test_the_teaser_never_carries_more_than_the_gate_saw(ledger):
    """Markup out, headline repeat out, 300 characters maximum. Every rule here
    REMOVES information; none can add any."""
    assert gate_ledger.teaser(GNEWS["raw_text"], GNEWS["headline"]) == ""

    teaser = gate_ledger.teaser(PRESS["raw_text"], PRESS["headline"])
    assert teaser.startswith("The company said it would add 300 engineering")
    assert "<" not in teaser and "\n" not in teaser

    long_body = "Headline\n\n" + ("word " * 500)
    assert len(gate_ledger.teaser(long_body, "Headline")) <= gate_ledger.TEASER_CHARS


def test_no_field_carries_the_full_article_or_anything_personal(ledger):
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
    gate_ledger.flush()
    (line,) = _lines(ledger)
    assert set(line) == {"key", "ts", "collector", "host", "lang", "country",
                         "headline", "teaser", "gate", "outcome", "basis"}
    assert len(line["teaser"]) <= gate_ledger.TEASER_CHARS
    assert len(line["headline"]) <= gate_ledger.HEADLINE_CHARS


def test_a_dry_run_writes_nothing(ledger, monkeypatch):
    monkeypatch.setattr(gate_ledger, "DRY_RUN", True)
    gate_ledger.record(GNEWS, "google_news", gate_ledger.YES)
    assert gate_ledger.flush() == 0
    assert not list(ledger.iterdir())


def test_the_ledger_can_never_fail_a_run(ledger, monkeypatch, capsys):
    """Whatever the filesystem does, collection carries on. The gate is on the
    hot path and a bookkeeping file is worth nothing beside a day of signals."""
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(ledger / "wall" / "x"))
    (ledger / "wall").write_text("not a directory")

    gate_ledger.record(GNEWS, "google_news", gate_ledger.YES)
    assert gate_ledger.flush() == 0            # no exception
    assert gate_ledger.STATS["failures"] == 1
    assert "DISABLED for this run" in capsys.readouterr().err

    # And it stays quiet afterwards rather than printing per candidate.
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
    assert gate_ledger.STATS["recorded"] == 1


def test_a_malformed_item_is_survived(ledger):
    gate_ledger.record({"headline": None, "raw_text": None, "source_url": "a"},
                       "x", gate_ledger.YES)
    gate_ledger.record({}, "x", gate_ledger.NO)
    assert gate_ledger.flush() == 2
    assert gate_ledger.STATS["failures"] == 0


def test_two_records_for_one_candidate_keep_only_the_later_verdict(ledger):
    """One key, one line per run. A candidate re-gated inside the same run (a
    collector that emitted it twice) must not appear twice with two verdicts."""
    gate_ledger.record(GNEWS, "google_news", gate_ledger.NO)
    gate_ledger.record(GNEWS, "google_news", gate_ledger.YES)
    assert gate_ledger.flush() == 1
    (line,) = _lines(ledger)
    assert line["gate"] == "YES"


def test_closed_months_are_gzipped_and_old_ones_dropped(ledger, monkeypatch):
    monkeypatch.setattr(gate_ledger, "KEEP_MONTHS", 3)
    for stem in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        (ledger / f"labels-{stem}.jsonl").write_text(
            json.dumps({"key": stem, "gate": "YES"}) + "\n")
    # A file that is not a shard must be left alone: the weak bootstrap set
    # lives in this directory and is not a month.
    (ledger / "bootstrap-weak.jsonl").write_text("{}\n")

    gate_ledger.compact(datetime(2026, 5, 9, tzinfo=timezone.utc))

    names = sorted(p.name for p in ledger.iterdir())
    assert names == ["bootstrap-weak.jsonl", "labels-2026-03.jsonl.gz",
                     "labels-2026-04.jsonl.gz", "labels-2026-05.jsonl"]
    with gzip.open(ledger / "labels-2026-03.jsonl.gz", "rt") as fh:
        assert json.loads(fh.read())["key"] == "2026-03"


def test_read_all_spans_plain_and_compressed_shards(ledger):
    (ledger / "labels-2026-06.jsonl").write_text(
        json.dumps({"key": "old"}) + "\n")
    gate_ledger.compact(datetime(2026, 7, 1, tzinfo=timezone.utc))
    gate_ledger.record(GNEWS, "google_news", gate_ledger.YES)
    gate_ledger.flush()

    keys = [row.get("key") for row in gate_ledger.read_all(str(ledger))]
    assert "old" in keys and gate_ledger.key(GNEWS) in keys


# --- merging a run's labels back onto main ----------------------------------
#
# collect.yml does `git reset --hard origin/main` before committing, which
# discards the shard this run just wrote. merge_gate_labels.py folds it back.

def test_merge_appends_only_what_main_does_not_hold(tmp_path):
    import merge_gate_labels

    src, dst = tmp_path / "saved", tmp_path / "ledger"
    src.mkdir(), dst.mkdir()
    shared = json.dumps({"key": "a", "gate": "YES"})
    mine = json.dumps({"key": "b", "gate": "NO"})
    theirs = json.dumps({"key": "c", "gate": "YES"})
    (src / "labels-2026-07.jsonl").write_text(shared + "\n" + mine + "\n")
    # main moved on while this run was collecting.
    (dst / "labels-2026-07.jsonl").write_text(shared + "\n" + theirs + "\n")

    added, _notes = merge_gate_labels.merge(str(src), str(dst))
    assert added == 1
    kept = [json.loads(l) for l in
            (dst / "labels-2026-07.jsonl").read_text().splitlines() if l]
    assert [row["key"] for row in kept] == ["a", "c", "b"]


def test_merge_leaves_the_weak_bootstrap_set_alone(tmp_path):
    """It is not per-run output. `git reset --hard` restores it from origin
    already, and merging it would duplicate 4,328 lines every run."""
    import merge_gate_labels

    src, dst = tmp_path / "saved", tmp_path / "ledger"
    src.mkdir(), dst.mkdir()
    (src / "bootstrap-weak.jsonl").write_text('{"key":"w","weak":true}\n')
    (src / "README.md").write_text("# docs\n")
    (dst / "bootstrap-weak.jsonl").write_text('{"key":"w","weak":true}\n')

    added, _notes = merge_gate_labels.merge(str(src), str(dst))
    assert added == 0
    assert (dst / "bootstrap-weak.jsonl").read_text().count("\n") == 1


def test_merge_handles_a_month_that_closed_mid_run(tmp_path):
    """One side compressed, the other not. The compressed form survives."""
    import merge_gate_labels

    src, dst = tmp_path / "saved", tmp_path / "ledger"
    src.mkdir(), dst.mkdir()
    (src / "labels-2026-07.jsonl").write_text('{"key":"new"}\n')
    with gzip.open(dst / "labels-2026-07.jsonl.gz", "wt") as fh:
        fh.write('{"key":"old"}\n')

    added, _notes = merge_gate_labels.merge(str(src), str(dst))
    assert added == 1
    assert not (dst / "labels-2026-07.jsonl").exists()
    with gzip.open(dst / "labels-2026-07.jsonl.gz", "rt") as fh:
        assert [json.loads(l)["key"] for l in fh if l.strip()] == ["old", "new"]


def test_merge_never_fails_the_commit_step(tmp_path, capsys):
    """A collect run that has already stored and published rows must not go red
    over bookkeeping. The CLI returns 0 whatever happens."""
    import merge_gate_labels

    assert merge_gate_labels.main.__module__  # imported, not a stub
    monkey = tmp_path / "not-a-directory"
    monkey.write_text("x")
    added, notes = merge_gate_labels.merge(str(monkey), str(tmp_path / "out"))
    assert added == 0 and notes


# --- end to end, through the real run loop -----------------------------------

def test_a_whole_run_writes_a_verdict_and_a_joined_outcome(ledger, monkeypatch,
                                                           tmp_path, stats):
    """The join is closed in memory inside one run, so this is the test that
    proves it: two candidates, one gated NO and one gated YES and stored, and
    the shard on disk has to say so afterwards."""
    import run_collect
    from pipeline import schema

    keep = {
        "raw_text": "Stripe to create 300 new jobs at expanded Dublin hub\n\n"
                    "The payments company said the roles would be filled "
                    "over 2026 at a new engineering site in Dublin.",
        "headline": "Stripe to create 300 new jobs at expanded Dublin hub",
        "source_url": "https://www.irishtimes.com/business/stripe-dublin-hub/",
        "discovery_url": "https://www.irishtimes.com/business/stripe-dublin-hub/",
        "source_name": "The Irish Times", "collector": "fake_news",
        "published_date": "2026-07-30", "language": "en", "source_country": "IE",
    }
    drop = dict(keep, headline="Council debates hiring rules for the season",
                raw_text="Council debates hiring rules for the season\n\n"
                         "Local representatives discussed recruitment policy.",
                source_url="https://www.irishtimes.com/news/council-hiring/",
                discovery_url="https://www.irishtimes.com/news/council-hiring/")

    module = type("FakeCollector", (), {
        "COLLECTOR": "fake_news",
        "collect": staticmethod(lambda queries, **kw: [dict(keep), dict(drop)]),
    })
    monkeypatch.setitem(run_collect.SOURCES, "fake_news", module)
    real_connect = schema.connect
    monkeypatch.setattr(schema, "connect",
                        lambda *a, **k: real_connect(tmp_path / "t.db"))
    # The interpretation is a separate paid call and not what is under test.
    monkeypatch.setattr(classify, "read_enabled", lambda: False)

    extraction = json.dumps({
        "is_talent_signal": True, "company": "Stripe",
        "pillar": "how_we_work", "signal_direction": "hiring",
        "city": "Dublin", "country": "Ireland", "confidence": "reported",
        "headline": "Stripe to create 300 new jobs at expanded Dublin hub",
        "summary": "Stripe said it would add 300 roles at a new Dublin site.",
        "talent_readthrough": "Adds 300 engineering roles to the Dublin market "
                              "over 2026.",
        "predicted_outcome": "", "check_after_date": "",
    })

    def fake_call(model, system, user, *, timeout, max_tokens=None,
                  json_mode=True):
        if max_tokens == 4:                       # the one-word gate
            return "YES" if "Stripe" in user else "NO"
        return extraction

    monkeypatch.setattr(classify, "_call", fake_call)

    run_collect.run(dry_run=False, offline=False, run_index=0, limit=None,
                    source="fake_news")

    by_key = {line["key"]: line for line in _lines(ledger)}
    assert len(by_key) == 2
    kept = by_key[gate_ledger.key(keep)]
    dropped = by_key[gate_ledger.key(drop)]

    assert kept["gate"] == "YES" and kept["outcome"] == "stored"
    assert kept["collector"] == "fake_news" and kept["country"] == "IE"
    assert dropped["gate"] == "NO" and dropped["outcome"] == "gate_reject"
    # And the gate reject cost nothing downstream: it never reached extraction.
    assert classify.STATS["full_calls"] == 1


# --- the fourth verdict ------------------------------------------------------

def test_a_single_stage_run_is_recorded_as_OFF_and_not_as_a_YES(ledger,
                                                               monkeypatch,
                                                               stats):
    """The fourth value, and the one with no gate call behind it at all.

    `GATE_MODEL=off` makes every candidate go straight to the read-through.
    That is not a verdict, and the three ways of losing the distinction all
    corrupt the training set in a different direction: as YES it teaches that
    a disabled gate is a talent signal, as NO it teaches the opposite, and as
    "no line at all" it silently drops the outcome — which is the label the
    classifier is actually trained on — for every candidate of every
    single-stage run.
    """
    monkeypatch.setattr(classify, "gate_enabled", lambda: False)
    monkeypatch.setattr(classify, "paid_reads_enabled", lambda: True)
    monkeypatch.setattr(classify, "read_enabled", lambda: False)
    monkeypatch.setattr(classify, "_call", lambda *a, **k: json.dumps(
        {"is_talent_signal": True, "company": "Stripe"}))

    assert classify.classify(dict(PRESS), interpret_now=False) is not None
    gate_ledger.flush()

    (line,) = _lines(ledger)
    assert line["gate"] == "OFF"
    assert line["gate"] not in (gate_ledger.YES, gate_ledger.NO)
    # Still open, because the outcome is decided by the caller, not here.
    assert line["outcome"] == "unknown"
    # And no gate call was billed for it.
    assert classify.STATS["gate_calls"] == 0


def test_the_four_verdicts_are_four_distinct_values():
    """A refactor that collapsed any two of these back into a boolean would
    pass every other test in this file."""
    assert len({gate_ledger.YES, gate_ledger.NO,
                gate_ledger.ERROR, gate_ledger.OFF}) == 4


# --- record() only buffers, so something has to flush ------------------------

def test_around_run_flushes_whatever_the_entry_point_does(ledger):
    """`record()` BUFFERS. Every label is lost at process exit unless somebody
    flushes, and the module cannot warn about it: a run that gated nothing and
    a run that dropped everything look identical from inside here.

    That is not hypothetical. `classify.classify` has always recorded, but the
    five backfills never flushed, so every gate verdict they paid for went to
    the buffer and nowhere else while the daily run's labels landed fine. So
    the flush is tested on the paths that actually lose it: an early return and
    a raise, which is how a backfill ends on exhausted credits and on a bad key.
    """
    @gate_ledger.around_run("early-return")
    def returns_early():
        gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
        return 1

    assert returns_early() == 1
    assert [line["gate"] for line in _lines(ledger)] == ["YES"]

    @gate_ledger.around_run("raises")
    def blows_up():
        gate_ledger.record(GNEWS, "google_news", gate_ledger.ERROR)
        raise RuntimeError("bad key")

    with pytest.raises(RuntimeError):
        blows_up()
    assert [line["gate"] for line in _lines(ledger)] == ["YES", "ERROR"]


def test_around_run_starts_each_entry_point_from_an_empty_buffer(ledger):
    """Two runs in one process must not have the first one's candidates
    written twice. `reset()` before, not merely `flush()` after."""
    @gate_ledger.around_run("one")
    def first():
        gate_ledger.record(PRESS, "national_press", gate_ledger.YES)

    first()
    first()
    assert len(_lines(ledger)) == 2      # one line per run, not one then two


def test_every_entry_point_that_classifies_also_flushes():
    """The invariant behind the bug, asserted so it cannot come back.

    Anything that calls `classify.classify` pays for gate calls, and a paid
    gate call whose verdict is not written is training data bought and thrown
    away. `gate_ledger.around_run` is the only thing that pairs the reset with
    the flush, so requiring it by name is what makes a new backfill notice.
    """
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "classify.classify(" not in source:
            continue
        if "gate_ledger.around_run" not in source:
            offenders.append(path.name)
    assert not offenders, (
        "these entry points classify — so they buffer gate labels — but never "
        f"flush them, and every verdict they pay for is lost: {offenders}"
    )

"""The classifier gate (plan step 2): the bar, the fail-opens, the flag.

Four properties are load-bearing, and each is asserted here without
scikit-learn — the replay's fit step is injectable precisely so the bar's
arithmetic can be proven offline on the free CI runner:

  * the replay bar FAILS on a synthetic classifier that costs recall, and the
    weekly run then changes nothing;
  * every degraded state fails OPEN to the LLM gate — missing, corrupt,
    unarmed, stale, wrong-weights, unseen-language, exception;
  * the flag flip is REFUSED when the replay report is missing, thin or under
    the bar, however arm() is called;
  * classify() routes three ways: confident-RELEVANT never pays a gate call,
    confident-IRRELEVANT never reaches extraction, UNCERTAIN behaves exactly
    as before the classifier existed.
"""

import gzip
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import train_gate_classifier as trainer
from pipeline import classify, gate_classifier, gate_ledger


@pytest.fixture(autouse=True)
def classifier_dir(tmp_path, monkeypatch):
    out = tmp_path / "clf"
    monkeypatch.setenv("TIT_GATE_CLASSIFIER_DIR", str(out))
    monkeypatch.delenv("TIT_GATE_CLASSIFIER", raising=False)
    gate_classifier.reset_cache()
    yield out
    gate_classifier.reset_cache()


# --- Synthetic labels ---------------------------------------------------------

FUNDING = "acme lifts funding zebra round"
WEATHER = "storm weather rain forecast tomorrow"
OMEGA = "quorx jolt wins deal vexing"


def make_line(day, headline, outcome, gate="YES", lang="en", key=None):
    return {"key": key or f"{day}-{abs(hash(headline)) % 10**8}-{outcome}",
            "ts": f"{day}T08:00Z", "collector": "google_news", "host": "x.com",
            "lang": lang, "country": "US", "headline": headline, "teaser": "",
            "gate": gate, "outcome": outcome,
            "basis": gate_ledger.BASIS_GATE_TEXT}


def days_back_from(start, count):
    base = datetime.fromisoformat(start)
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]


def synthetic_labels(n_days=36, stored_headline=FUNDING,
                     reject_headline=WEATHER):
    real = []
    for i, day in enumerate(days_back_from("2026-06-01", n_days)):
        real.append(make_line(day, f"{stored_headline} {i}", "stored",
                              key=f"s{i}"))
        real.append(make_line(day, f"{reject_headline} {i}", "gate_reject",
                              gate="NO", key=f"r{i}"))
    return real


def write_ledger(directory, lines):
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "labels-2026-06.jsonl", "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def weights_for(positive="", negative="", scale=2.0, bias=0.0):
    """A hand-built weight vector: +scale on every hashed feature of
    `positive`, -scale on every feature of `negative` (positive wins a
    collision). The deterministic stand-in for a fitted model."""
    weights = [0.0] * gate_classifier.DIM
    for bucket in gate_classifier.features(negative):
        weights[bucket] = -scale
    for bucket in gate_classifier.features(positive):
        weights[bucket] = scale
    return weights, bias


def separating_fit(rows):
    """A 'fit' that genuinely separates the synthetic corpus."""
    return weights_for(positive=FUNDING, negative=WEATHER)


def blind_to_omega_fit(rows):
    """A 'fit' that loves the old vocabulary and scores everything else near
    zero — the classifier that costs recall the moment the world moves. The
    bias is deep enough that the few n-grams OMEGA shares with FUNDING
    ("ing", from vexing/funding) cannot rescue an omega headline."""
    return weights_for(positive=FUNDING, bias=-12.0)


# --- The shipping bar ----------------------------------------------------------


def drifted_corpus():
    """32 days of the old vocabulary, then 8 days of a new one — sized so the
    new days fill exactly the final replay block and NO fold's threshold
    hold-out ever sees a new-vocabulary stored row. That is the honest worst
    case: the world moved after training and nothing warned the thresholds."""
    real = synthetic_labels(n_days=32)
    for i, day in enumerate(days_back_from("2026-07-03", 8)):
        real.append(make_line(day, f"{OMEGA} {i}", "stored", key=f"o{i}"))
        real.append(make_line(day, f"{WEATHER} late {i}", "gate_reject",
                              gate="NO", key=f"rl{i}"))
    return real


def test_the_replay_bar_fails_on_a_classifier_that_costs_recall():
    """Stored rows whose vocabulary the model never learned score low, fall
    under a drop threshold chosen on the old vocabulary, and the bar catches
    exactly that: the rate comes in under 99.5 and nothing may arm."""
    report = trainer.replay(drifted_corpus(), [], fit_fn=blind_to_omega_fit)
    assert report["days"] >= trainer.MIN_REPLAY_DAYS
    assert report["rate_pct"] < gate_classifier.SHIP_BAR_PCT
    assert not trainer.bar_passes(report)
    assert report["kept"] < report["stored"]


def test_a_failed_bar_run_changes_nothing_and_prints_the_not_ready_line(
        tmp_path, classifier_dir, capsys):
    labels = tmp_path / "labels"
    real = drifted_corpus()
    write_ledger(labels, real)

    assert trainer.run(label_dir=str(labels), out_dir=str(classifier_dir),
                       fit_fn=blind_to_omega_fit,
                       poster=lambda payload: True) == 0
    out = capsys.readouterr().out
    assert "not ready: " in out
    assert f"{len(real)} labels" in out
    assert not classifier_dir.exists(), "a not-ready run must write nothing"


def test_too_few_days_is_not_ready_even_when_the_replay_is_perfect(
        tmp_path, classifier_dir, capsys):
    labels = tmp_path / "labels"
    write_ledger(labels, synthetic_labels(n_days=5))
    assert trainer.run(label_dir=str(labels), out_dir=str(classifier_dir),
                       fit_fn=separating_fit,
                       poster=lambda payload: True) == 0
    assert "not ready: " in capsys.readouterr().out
    assert not classifier_dir.exists()


def test_a_passing_bar_arms_writes_the_artifact_and_mails_once(
        tmp_path, classifier_dir):
    labels = tmp_path / "labels"
    write_ledger(labels, synthetic_labels(n_days=36))
    posted = []

    def poster(payload):
        posted.append(payload)
        return True

    assert trainer.run(label_dir=str(labels), out_dir=str(classifier_dir),
                       fit_fn=separating_fit, poster=poster) == 0

    status = json.loads((classifier_dir / "status.json").read_text())
    assert status["armed"] is True
    assert status["replay"]["rate_pct"] >= gate_classifier.SHIP_BAR_PCT
    assert status["replay"]["days"] >= trainer.MIN_REPLAY_DAYS
    assert "notice_pending" not in status, "the sent notice must not linger"
    assert (classifier_dir / "model.json.gz").stat().st_size < 5 * 2**20

    armed_mails = [p for p in posted if "ARMED" in p["subject"]]
    assert len(armed_mails) == 1
    assert str(status["replay"]["rate_pct"]) in armed_mails[0]["subject"] + \
        armed_mails[0]["body"]
    assert armed_mails[0]["dedupe_key"].startswith("gate-classifier-armed:")

    # And the runtime accepts exactly what the trainer wrote.
    model, why = gate_classifier.load()
    assert model is not None, why
    assert gate_classifier.route(FUNDING, lang="en") != gate_classifier.IRRELEVANT
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.IRRELEVANT


def test_a_retrain_that_fails_the_bar_reverts_the_flag_and_mails_once(
        tmp_path, classifier_dir):
    labels = tmp_path / "labels"
    write_ledger(labels, synthetic_labels(n_days=36))
    posted = []
    poster = lambda payload: posted.append(payload) or True  # noqa: E731

    trainer.run(label_dir=str(labels), out_dir=str(classifier_dir),
                fit_fn=separating_fit, poster=poster)
    assert json.loads((classifier_dir / "status.json").read_text())["armed"]

    # The vocabulary moves; the retrain fails the bar.
    write_ledger(labels, drifted_corpus())
    trainer.run(label_dir=str(labels), out_dir=str(classifier_dir),
                fit_fn=blind_to_omega_fit, poster=poster)

    status = json.loads((classifier_dir / "status.json").read_text())
    assert status["armed"] is False
    assert "REVERTED" in status["reason"]
    reverted = [p for p in posted if "REVERTED" in p["subject"]]
    assert len(reverted) == 1

    gate_classifier.reset_cache()
    model, why = gate_classifier.load()
    assert model is None
    assert "not armed" in why
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.UNCERTAIN


# --- The flag flip is refused without a clean replay report ---------------------


def test_arm_refuses_a_missing_thin_or_failing_replay_report(classifier_dir):
    good = {"rate_pct": 99.9, "stored": 400, "kept": 400, "days": 34,
            "window": ["2026-06-01", "2026-07-04"], "candidates": 800,
            "confident_pct": 50.0, "routed": {}}
    for bad in (
        {},                                     # missing everything
        {**good, "days": 12},                   # thin: under 30 days
        {**good, "rate_pct": 99.2},             # under the bar
        {**good, "stored": 0, "kept": 0},       # no stored rows at all
    ):
        with pytest.raises(ValueError, match="refusing to arm"):
            trainer.arm(str(classifier_dir), bad, artifact_sha="abc",
                        trained_at="2026-07-05T00:00:00Z", n_labels=1,
                        prior={})
    assert not (classifier_dir / "status.json").exists()


def test_the_runtime_refuses_an_armed_flag_whose_report_is_stale_or_wrong(
        classifier_dir):
    model = gate_classifier.Model(*weights_for(positive=FUNDING),
                                  t_lo=0.4, t_hi=2.0, langs=["en"])
    sha = trainer.write_artifact(model, str(classifier_dir), n_labels=100,
                                 trained_at="2026-01-01T00:00:00Z")
    report = {"rate_pct": 99.9, "days": 34, "stored": 400, "kept": 400}

    def status(**over):
        base = {"armed": True, "artifact_sha": sha, "replay": report,
                "trained_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")}
        base.update(over)
        return base

    cases = {
        "stale": status(trained_at="2026-01-01T00:00:00Z"),
        "missing report": status(replay=None),
        "under the bar": status(replay={**report, "rate_pct": 99.0}),
        "thin report": status(replay={**report, "days": 20}),
        "different weights": status(artifact_sha="0000000000000000"),
        "unarmed": status(armed=False),
    }
    for name, doc in cases.items():
        trainer.write_status(str(classifier_dir), doc)
        gate_classifier.reset_cache()
        model_loaded, why = gate_classifier.load()
        assert model_loaded is None, f"{name}: loaded anyway"
        assert why, name
        assert gate_classifier.route(FUNDING, lang="en") == \
            gate_classifier.UNCERTAIN, name


# --- Fail open, everywhere -------------------------------------------------------


def armed_setup(classifier_dir, langs=("en",), t_lo=0.4, t_hi=0.9,
                relevant_langs=None):
    model = gate_classifier.Model(
        *weights_for(positive=FUNDING, negative=WEATHER, scale=2.0),
        t_lo=t_lo, t_hi=t_hi, langs=list(langs),
        relevant_langs=list(langs) if relevant_langs is None else relevant_langs)
    trained_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = trainer.write_artifact(model, str(classifier_dir), n_labels=1000,
                                 trained_at=trained_at)
    trainer.write_status(str(classifier_dir), {
        "armed": True, "artifact_sha": sha, "trained_at": trained_at,
        "replay": {"rate_pct": 99.9, "days": 34, "stored": 500, "kept": 499}})
    gate_classifier.reset_cache()


def test_no_artifact_at_all_fails_open():
    assert gate_classifier.route(FUNDING, lang="en") == gate_classifier.UNCERTAIN


def test_a_corrupt_artifact_fails_open(classifier_dir, capsys):
    armed_setup(classifier_dir)
    (classifier_dir / "model.json.gz").write_bytes(b"not gzip at all")
    gate_classifier.reset_cache()
    assert gate_classifier.route(FUNDING, lang="en") == gate_classifier.UNCERTAIN
    assert "failing open" in capsys.readouterr().err


def test_a_truncated_weight_vector_fails_open(classifier_dir):
    armed_setup(classifier_dir)
    with gzip.open(classifier_dir / "model.json.gz", "rt") as fh:
        doc = json.load(fh)
    doc["weights"] = doc["weights"][:400]
    with gzip.open(classifier_dir / "model.json.gz", "wt") as fh:
        json.dump(doc, fh)
    gate_classifier.reset_cache()
    assert gate_classifier.route(FUNDING, lang="en") == gate_classifier.UNCERTAIN


def test_a_language_the_training_set_never_saw_fails_open(classifier_dir):
    armed_setup(classifier_dir, langs=("en",))
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.IRRELEVANT
    assert gate_classifier.route(WEATHER, lang="he") == gate_classifier.UNCERTAIN
    assert gate_classifier.route(WEATHER, lang="") == gate_classifier.UNCERTAIN


def test_the_skip_band_is_earned_per_language(classifier_dir):
    """A language may be known well enough to DROP in and still not well
    enough to SKIP THE GATE in — the Polish shape: a small or skewed label
    base whose high scorers must keep paying the cheap gate rather than buy
    extractions for football-club noise."""
    armed_setup(classifier_dir, langs=("en", "pl"), relevant_langs=["en"])
    assert gate_classifier.route(FUNDING, lang="en") == gate_classifier.RELEVANT
    assert gate_classifier.route(FUNDING, lang="pl") == gate_classifier.UNCERTAIN
    # The drop band stays available — it is policed by the replay bar.
    assert gate_classifier.route(WEATHER, lang="pl") == gate_classifier.IRRELEVANT


def test_an_artifact_without_a_relevant_roster_grants_the_skip_to_nobody(
        classifier_dir):
    armed_setup(classifier_dir, relevant_langs=[])
    assert gate_classifier.route(FUNDING, lang="en") == gate_classifier.UNCERTAIN
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.IRRELEVANT


def test_a_language_with_no_stored_evidence_earns_no_confident_band_at_all():
    """MIN_LANG_STORED: 30 labels that are all rejects say nothing about what
    a positive looks like in that language, so it stays on the LLM gate."""
    real = [make_line("2026-06-01", f"{WEATHER} {i}", "gate_reject", gate="NO",
                      lang="pl", key=f"p{i}") for i in range(30)]
    real += [make_line("2026-06-01", f"{FUNDING} {i}", "stored", lang="en",
                       key=f"e{i}") for i in range(30)]
    assert trainer.language_roster(real) == ["en"]


def test_relevant_languages_need_volume_and_their_own_gate_agreement():
    def lines(lang, n, gate):
        return [make_line("2026-06-01", f"{FUNDING} {lang} {i}",
                          "stored" if gate == "YES" else "gate_reject",
                          gate=gate, lang=lang, key=f"{lang}{gate}{i}")
                for i in range(n)]

    # es: high volume, high band agreement. pl: same volume, low agreement.
    # it: perfect agreement, thin volume.
    real = lines("es", 250, "YES") + lines("pl", 200, "YES") + \
        lines("pl", 200, "NO") + lines("it", 30, "YES")
    scored = [(0.99, line) for line in real]
    assert trainer.relevant_languages(scored, 0.9, real) == ["es"]
    # And a shut global band shuts every language.
    assert trainer.relevant_languages(scored, 2.0, real) == []


def test_an_empty_headline_fails_open(classifier_dir):
    armed_setup(classifier_dir)
    assert gate_classifier.route("", lang="en") == gate_classifier.UNCERTAIN


def test_the_off_switch_forces_the_llm_gate(classifier_dir, monkeypatch):
    armed_setup(classifier_dir)
    monkeypatch.setenv("TIT_GATE_CLASSIFIER", "off")
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.UNCERTAIN


def test_an_exception_inside_routing_fails_open(classifier_dir, monkeypatch):
    armed_setup(classifier_dir)

    def boom(*a, **k):
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr(gate_classifier.Model, "score", boom)
    assert gate_classifier.route(WEATHER, lang="en") == gate_classifier.UNCERTAIN


# --- Three-way routing inside classify() -------------------------------------------


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path / "ledger"))
    gate_ledger.reset()
    yield
    gate_ledger.reset()


@pytest.fixture
def stats():
    before = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(before)


RAW = {"source_url": "https://ex.com/a", "headline": FUNDING,
       "raw_text": FUNDING + "\n\nAcme said the round closed on Monday.",
       "language": "en", "collector": "google_news"}


def _classify_with(monkeypatch, route):
    monkeypatch.setattr(gate_classifier, "route_item", lambda item: route)
    monkeypatch.setattr(
        classify, "_call", lambda *a, **k: '{"is_talent_signal": false}')


def test_confident_relevant_skips_the_llm_gate(monkeypatch, ledger, stats):
    _classify_with(monkeypatch, gate_classifier.RELEVANT)

    def no_gate(*a, **k):
        raise AssertionError("the LLM gate must not be called")

    monkeypatch.setattr(classify, "gate_verdict", no_gate)
    assert classify.classify(dict(RAW)) is None      # extraction said no
    assert stats["clf_relevant"] == 1
    assert stats["full_calls"] == 1, "it must go straight to extraction"
    line = gate_ledger._BUFFER[gate_ledger.key(RAW)]
    assert line["gate"] == gate_ledger.CLF_YES


def test_confident_irrelevant_drops_without_any_paid_call(
        monkeypatch, ledger, stats):
    _classify_with(monkeypatch, gate_classifier.IRRELEVANT)

    def no_call(*a, **k):
        raise AssertionError("no model may be called for a confident drop")

    monkeypatch.setattr(classify, "_call", no_call)
    assert classify.classify(dict(RAW)) is None
    assert stats["clf_irrelevant"] == 1
    assert stats["gate_calls"] == 0 and stats["full_calls"] == 0
    line = gate_ledger._BUFFER[gate_ledger.key(RAW)]
    assert line["gate"] == gate_ledger.CLF_NO
    assert line["outcome"] == "clf_reject"
    # ...and run_collect's generic reject cannot relabel it.
    gate_ledger.outcome(RAW, "model_reject")
    assert line["outcome"] == "clf_reject"


def test_uncertain_pays_the_llm_gate_exactly_as_before(
        monkeypatch, ledger, stats):
    _classify_with(monkeypatch, gate_classifier.UNCERTAIN)
    monkeypatch.setattr(classify, "gate_verdict",
                        lambda text, **k: gate_ledger.NO)
    assert classify.classify(dict(RAW)) is None
    assert stats["clf_relevant"] == 0 and stats["clf_irrelevant"] == 0
    line = gate_ledger._BUFFER[gate_ledger.key(RAW)]
    assert line["gate"] == gate_ledger.NO
    assert line["outcome"] == "gate_reject"


def test_with_no_artifact_classify_behaves_exactly_as_yesterday(
        monkeypatch, ledger, stats):
    """The integration default: nothing armed, so route_item is UNCERTAIN and
    the whole block is a no-op around the existing gate call."""
    monkeypatch.setattr(classify, "gate_verdict",
                        lambda text, **k: gate_ledger.YES)
    monkeypatch.setattr(
        classify, "_call", lambda *a, **k: '{"is_talent_signal": false}')
    assert classify.classify(dict(RAW)) is None
    assert stats["clf_relevant"] == 0 and stats["clf_irrelevant"] == 0
    line = gate_ledger._BUFFER[gate_ledger.key(RAW)]
    assert line["gate"] == gate_ledger.YES


# --- The classifier's own verdicts never train the classifier ----------------------


def test_clf_rejects_are_excluded_from_training_targets():
    line = make_line("2026-06-01", WEATHER, "clf_reject", gate="CLF_NO")
    rows = trainer.training_rows([line], [])
    assert rows == []
    stored = make_line("2026-06-01", FUNDING, "stored", gate="CLF_YES")
    rows = trainer.training_rows([stored], [])
    assert len(rows) == 1 and rows[0][1] == 1


def test_weak_bootstrap_rows_ride_along_at_a_discount():
    weak = [dict(make_line("2026-05-01", FUNDING, "stored"), basis="url_slug"),
            dict(make_line("2026-05-01", WEATHER, "rejected"), basis="url_slug")]
    rows = trainer.training_rows([], weak)
    assert [(label, weight) for _f, label, weight, _l in rows] == \
        [(1, trainer.WEAK_WEIGHT), (0, trainer.WEAK_WEIGHT)]


# --- Drift alarm ----------------------------------------------------------------


def _drift_lines(n_confident, n_uncertain, now):
    day = now.strftime("%Y-%m-%d")
    lines = [make_line(day, f"{FUNDING} {i}", "stored", gate="CLF_YES",
                       key=f"c{i}") for i in range(n_confident)]
    lines += [make_line(day, f"{WEATHER} {i}", "gate_reject", gate="NO",
                        key=f"u{i}") for i in range(n_uncertain)]
    return lines


def test_drift_alarm_fires_once_and_clears_once(classifier_dir):
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    posted = []
    poster = lambda payload: posted.append(payload) or True  # noqa: E731
    armed_setup(classifier_dir)
    status = trainer.read_status(str(classifier_dir))

    high = _drift_lines(40, 60, now)                 # 60% uncertain
    trainer.check_drift(str(classifier_dir), status, high, poster, now)
    trainer.check_drift(str(classifier_dir), status, high, poster, now)
    drift_mails = [p for p in posted if "drift" in p["subject"].lower()]
    assert len(drift_mails) == 1, "the alarm must dedupe"
    assert drift_mails[0]["dedupe_key"].startswith("gate-classifier-drift:")

    low = _drift_lines(90, 10, now)                  # 10% uncertain
    trainer.check_drift(str(classifier_dir), status, low, poster, now)
    trainer.check_drift(str(classifier_dir), status, low, poster, now)
    cleared = [p for p in posted if p.get("resolve_scope")]
    assert len(cleared) == 1
    assert cleared[0]["resolve_scope"] == "gate-classifier-drift"


def test_drift_is_silent_before_the_classifier_routes_anything(classifier_dir):
    """Pre-arming, every candidate pays the LLM gate, so the uncertain share
    is 100% by construction and must not be read as drift."""
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    share, _n = trainer.uncertain_share(
        _drift_lines(0, 200, now), now=now)
    assert share is None


# --- Train/serve contract ----------------------------------------------------------


def test_the_featurizer_is_deterministic_across_processes():
    """CRC32, not hash(): PYTHONHASHSEED must not be able to change a score."""
    feats = gate_classifier.features("Enigma Raises $71M", "seed round")
    assert feats == gate_classifier.features("Enigma Raises $71M", "seed round")
    assert all(0 <= b < gate_classifier.DIM for b in feats)
    # NFKC + lowercase + whitespace folding.
    assert gate_classifier.features("ＡＣＭＥ  Raises") == \
        gate_classifier.features("acme raises")


def test_the_artifact_roundtrips_bit_exact(classifier_dir):
    model = gate_classifier.Model(*weights_for(positive=FUNDING, scale=1.5),
                                  t_lo=0.2, t_hi=0.95, langs=["en", "es"])
    trainer.write_artifact(model, str(classifier_dir), n_labels=7,
                           trained_at="2026-07-01T00:00:00Z")
    with gzip.open(classifier_dir / "model.json.gz", "rt") as fh:
        doc = json.load(fh)
    loaded = gate_classifier.decode_weights(doc["weights"])
    import struct
    assert list(loaded) == list(struct.unpack(
        f"<{gate_classifier.DIM}f",
        struct.pack(f"<{gate_classifier.DIM}f", *model.weights)))
    assert doc["t_lo"] == 0.2 and doc["t_hi"] == 0.95
    assert doc["langs"] == ["en", "es"]


def test_lang_key_folds_the_ledgers_spellings():
    assert gate_classifier.lang_key("English") == "en"
    assert gate_classifier.lang_key("en") == "en"
    assert gate_classifier.lang_key("pt-BR") == "pt"
    assert gate_classifier.lang_key("") == ""


def test_thresholds_protect_recall_before_anything_else():
    """t_lo sits under the worst held-out stored score with margin, and never
    above the hard ceiling; with no stored rows the drop band is empty."""
    scored = [(0.30, {"outcome": "stored"}), (0.90, {"outcome": "stored"}),
              (0.05, {"outcome": "gate_reject", "gate": "NO"})]
    t_lo, t_hi = trainer.choose_thresholds(scored)
    assert t_lo == pytest.approx(0.30 * trainer.T_LO_MARGIN)
    assert t_lo < 0.30

    t_lo, _ = trainer.choose_thresholds(
        [(0.99, {"outcome": "stored"}), (0.98, {"outcome": "stored"})])
    assert t_lo == trainer.T_LO_CEILING

    t_lo, _ = trainer.choose_thresholds([(0.5, {"outcome": "gate_reject"})])
    assert t_lo == 0.0, "no stored evidence means no drop band"


def test_the_skip_band_needs_gate_agreement_or_stays_empty():
    """Confident-RELEVANT trades a cheap gate call for an expensive extraction,
    so without enough gate-YES agreement the band must stay empty."""
    scored = [(0.99, {"outcome": "stored", "gate": "YES"})] * 10
    _t_lo, t_hi = trainer.choose_thresholds(scored)
    assert t_hi > 1.0, "ten rows is not enough evidence to open the band"

    scored = [(0.99, {"outcome": "stored", "gate": "YES"})] * 30
    _t_lo, t_hi = trainer.choose_thresholds(scored)
    assert t_hi <= 1.0, "thirty unanimous rows is"


@pytest.mark.filterwarnings("ignore")
def test_the_real_fit_learns_the_synthetic_corpus():
    """The one scikit-learn test, skipped where it is not installed (CI's
    offline suite): the actual fit() + the pure-python scorer agree end to
    end, proving the train/serve featurizer contract with the real solver."""
    pytest.importorskip("sklearn")
    real = synthetic_labels(n_days=36)
    report = trainer.replay(real, [], fit_fn=trainer.fit)
    assert report["rate_pct"] >= gate_classifier.SHIP_BAR_PCT
    assert trainer.bar_passes(report)

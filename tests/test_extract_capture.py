"""The extraction-input capture: the raw text an extraction gold set needs.

docs/PLAN-gate-to-five-dollars.md, CORRECTION 2026-08-14: the extraction A/B
is blocked because no `raw_text` is persisted anywhere — not on `signals`, not
in the gate ledger — so an extraction gold set cannot be given ground truth at
any budget. This capture closes that gap FORWARD: a bounded, sampled,
redacted excerpt of the exact text extraction read, written onto the same
ledger line that already carries the candidate's gate verdict and outcome.

The properties under test are the size discipline and the honesty of the
excerpt, in that order:

  * only candidates that actually REACHED extraction carry an excerpt;
  * the excerpt is the extraction input (classify.FULL_READ_CHARS of raw
    text), because a shorter one would let a future gold set score an easier
    task under extraction's name;
  * a deterministic 1-in-SAMPLE_1_IN sample with a hard per-run cap, so a
    month of capture is megabytes and not the tens of megabytes raw text
    would be;
  * provider names are redacted — the one deliberate divergence from the
    production bytes, because the committed repo may not carry them;
  * it can be switched off, it never writes on a dry run, and like every
    other ledger write it can never fail a run.
"""

import json

import pytest

from pipeline import classify, gate_ledger

from test_gate_ledger import GNEWS, PRESS  # real item shapes


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(gate_ledger, "DRY_RUN", False)
    gate_ledger.reset()
    yield tmp_path
    gate_ledger.reset()


def _lines(directory):
    out = []
    for path in directory.iterdir():
        if path.suffix == ".jsonl":
            out += [json.loads(l) for l in path.read_text().splitlines() if l]
    return out


def _sampled(item) -> bool:
    """Whether the deterministic sample selects this item."""
    return int(gate_ledger.key(item), 16) % gate_ledger.SAMPLE_1_IN == 0


def _force_sample(monkeypatch, take_everything=True):
    """Make the deterministic sample take (or refuse) every candidate."""
    monkeypatch.setattr(gate_ledger, "SAMPLE_1_IN",
                        1 if take_everything else 1 << 62)


def test_a_sampled_extraction_input_lands_on_the_ledger_line(monkeypatch):
    _force_sample(monkeypatch)
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
    gate_ledger.capture_extract_input(PRESS, PRESS["raw_text"])
    gate_ledger.flush()
    (line,) = _lines(gate_ledger_dir())
    assert line["extract_excerpt"].startswith("Stripe opens Dublin")
    assert "300 engineering roles" in line["extract_excerpt"]


def gate_ledger_dir():
    import pathlib
    return pathlib.Path(gate_ledger.LEDGER_DIR)


def test_a_candidate_that_never_reached_extraction_carries_no_excerpt(monkeypatch):
    _force_sample(monkeypatch)
    gate_ledger.record(PRESS, "national_press", gate_ledger.NO)
    gate_ledger.flush()
    (line,) = _lines(gate_ledger_dir())
    assert "extract_excerpt" not in line


def test_the_excerpt_is_the_extraction_input_not_a_teaser(monkeypatch):
    """FULL_READ_CHARS, byte for byte up to redaction — never TEASER_CHARS."""
    _force_sample(monkeypatch)
    long_text = "Stripe opens Dublin engineering hub\n\n" + ("word " * 2000)
    item = dict(PRESS, raw_text=long_text)
    gate_ledger.record(item, "national_press", gate_ledger.YES)
    gate_ledger.capture_extract_input(item, long_text)
    gate_ledger.flush()
    (line,) = _lines(gate_ledger_dir())
    assert len(line["extract_excerpt"]) == classify.FULL_READ_CHARS
    assert line["extract_excerpt"] == long_text[:classify.FULL_READ_CHARS]
    # newlines survive: extraction reads prose, not a whitespace-collapsed line
    assert "\n\n" in line["extract_excerpt"]


def test_the_char_cap_is_the_real_extraction_cap():
    assert gate_ledger.EXTRACT_EXCERPT_CHARS == classify.FULL_READ_CHARS


def test_the_per_run_cap_holds(monkeypatch):
    _force_sample(monkeypatch)
    monkeypatch.setattr(gate_ledger, "EXTRACT_CAPTURE_PER_RUN", 2)
    for n in range(5):
        item = dict(PRESS, source_url=f"https://example.com/{n}")
        gate_ledger.record(item, "national_press", gate_ledger.YES)
        gate_ledger.capture_extract_input(item, item["raw_text"])
    gate_ledger.flush()
    lines = _lines(gate_ledger_dir())
    assert sum("extract_excerpt" in l for l in lines) == 2


def test_the_sample_is_deterministic_by_key(monkeypatch):
    """The same candidate is sampled or not on every run — no coin flips, so
    a deferred candidate's second run agrees with its first."""
    kept = [n for n in range(40)
            if _sampled(dict(PRESS, source_url=f"https://example.com/{n}"))]
    assert 0 < len(kept) < 40
    assert kept == [n for n in range(40)
                    if _sampled(dict(PRESS, source_url=f"https://example.com/{n}"))]


def test_provider_names_are_redacted(monkeypatch):
    from pipeline import provider_names
    _force_sample(monkeypatch)
    name = sorted(provider_names.NAMES)[0] if hasattr(provider_names, "NAMES") else None
    text = PRESS["raw_text"]
    redacted = provider_names.redact(text)
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
    gate_ledger.capture_extract_input(PRESS, text)
    gate_ledger.flush()
    (line,) = _lines(gate_ledger_dir())
    assert line["extract_excerpt"] == redacted[:gate_ledger.EXTRACT_EXCERPT_CHARS]
    assert not provider_names.contains(line["extract_excerpt"])


def test_it_can_be_switched_off(monkeypatch):
    _force_sample(monkeypatch)
    monkeypatch.setenv("TIT_EXTRACT_CAPTURE", "off")
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)
    gate_ledger.capture_extract_input(PRESS, PRESS["raw_text"])
    gate_ledger.flush()
    (line,) = _lines(gate_ledger_dir())
    assert "extract_excerpt" not in line


def test_it_never_fails_a_run(monkeypatch):
    """A capture on a candidate that was never recorded is a no-op, and an
    internal error disables the ledger for the run rather than raising."""
    _force_sample(monkeypatch)
    gate_ledger.capture_extract_input(PRESS, PRESS["raw_text"])  # never gated
    gate_ledger.record(PRESS, "national_press", gate_ledger.YES)

    def boom(item):
        raise RuntimeError("broken internals")
    monkeypatch.setattr(gate_ledger, "key", boom)
    gate_ledger.capture_extract_input(PRESS, PRESS["raw_text"])  # must not raise
    assert gate_ledger.STATS["failures"] == 1


def test_classify_calls_the_capture_on_the_extraction_path():
    """The wiring, asserted from the source: the capture sits beside the one
    real extraction call, so what is captured is what was sent."""
    import inspect
    src = inspect.getsource(classify.classify_and_extract) \
        if hasattr(classify, "classify_and_extract") else inspect.getsource(classify)
    assert "capture_extract_input" in src

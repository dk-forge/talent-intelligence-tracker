"""The redactor, and the write paths that must be unable to skip it.

`tests/test_no_provider_names.py` is the DETECTOR: it finds a name that already
reached a tracked file. These are the pins on the thing that stops one getting
there — `pipeline/provider_names.redact`, called inside the one function every
free-text field of a gate label passes through, and (2026-09-03) inside
`analysis.tripwire.report.write`, the choke point both committed tripwire
files pass through.

No banned name is written in this file. Both lists are read base64-encoded from
the modules under test, so the fixtures are built at runtime and the file
itself stays greppable-clean. The earlier scrub (PR #36) nearly committed the
real names into the very test asserting they never escape.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap_gate_labels                       # noqa: E402
from pipeline import gate_ledger, provider_names   # noqa: E402
import test_no_provider_names as detector          # noqa: E402
from analysis.tripwire import report               # noqa: E402


def _names():
    return list(provider_names.BANNED)


def test_redactor_and_detector_know_the_same_names():
    """A name the redactor does not know is a name the detector finds tomorrow.

    The two lists are deliberately duplicated rather than imported one from the
    other — the test suite must not depend on production code for the rule it
    enforces — so this is what keeps them honest.
    """
    assert sorted(provider_names.BANNED) == sorted(detector._BANNED)


@pytest.mark.parametrize("idx", range(len(_names())))
def test_every_banned_name_is_redacted(idx):
    """Reported by index, never by name, exactly as the detector reports."""
    name = _names()[idx]
    for text in (name, name.upper(), name.title(),
                 f"Prefix {name} suffix", f"www.{name}.com/a/b"):
        out = provider_names.redact(text)
        assert name not in out.lower(), f"banned pattern #{idx + 1} survived"
        assert provider_names.TAG_PREFIX in out


def test_redaction_keeps_the_sentence_around_it():
    """The whole reason this redacts instead of dropping the field: the text is
    the classifier's only feature. What is not a provider name must survive."""
    name = _names()[0]
    out = provider_names.redact(f"Acme raises 40 million, per {name} data")
    assert out.startswith("Acme raises 40 million, per ")
    assert out.endswith(" data")


def test_two_providers_stay_two_tokens():
    """An opaque tag, not a single [redacted]: a classifier can still tell the
    lines apart, and a reader can still count how many providers appeared."""
    a, b = _names()[0], _names()[2]
    assert provider_names.redact(a) != provider_names.redact(b)
    assert provider_names.redact(a) == provider_names.redact(a.upper())


def test_clean_text_is_untouched():
    for text in ("", "Acme opens a Berlin office", "reuters.com"):
        assert provider_names.redact(text) == text


def test_gate_ledger_cannot_write_a_banned_name(tmp_path, monkeypatch):
    """The structural claim, end to end: a candidate whose headline, host and
    body all name a provider produces a shard with none of them in it.

    This is the 2026-08-13 leak reproduced. Three names reached origin/main in
    two bot commits eight hours apart, through exactly this path, because
    nothing between the RSS item and the commit could refuse them.
    """
    name = _names()[1]
    monkeypatch.setattr(gate_ledger, "LEDGER_DIR", str(tmp_path))
    gate_ledger.reset()
    gate_ledger.record({
        "source_url": f"https://{name}.com/news/round",
        "headline": f"{name.title()} reports a record quarter",
        "raw_text": f"<p>Coverage compiled from {name} and elsewhere.</p>",
        "locale": "US:en",
    }, "national_press", gate_ledger.YES)
    gate_ledger.outcome({"source_url": f"https://{name}.com/news/round"},
                        "validate_reject", reason=f"aggregator host {name}.com")
    assert gate_ledger.flush() == 1

    written = (tmp_path / os.path.basename(
        gate_ledger.shard_path(gate_ledger.month()))).read_text("utf-8")
    assert name not in written.lower()
    line = json.loads(written.strip())
    # Redacted, not emptied: every field still carries its content.
    for field in ("headline", "teaser", "host", "reason"):
        assert line[field], f"{field} was dropped rather than redacted"
        assert provider_names.TAG_PREFIX in line[field]


def test_tripwire_report_cannot_write_a_banned_name(tmp_path):
    """The 2026-09-03 leak reproduced: a HELD lead's `matched.source_url`
    (copied verbatim from our own `signals` table, which is legitimately
    exempt because it is the system's memory, not a published artifact) named
    a provider, and nothing between `diff.verdict()` and the committed
    `tripwire-YYYY-MM-DD.json` could refuse it. `report.write()` now redacts
    every string leaf of both files it produces before either touches disk —
    covering that field, `ask.py`'s model-claimed `claimed_outlet` /
    `claimed_url`, and any field neither of us thought of yet.
    """
    name = _names()[1]
    result = {
        "ran_at": "2026-09-03T00:00:00-04:00", "ran_on": "2026-09-03",
        "plan": {"basis": "test"}, "queries_asked": [],
        "counts": {"leads": 1, "held": 1, "missing": 0, "unusable": 0, "usable": 1},
        "by_country": {}, "by_industry": {},
        "cost": {"run_usd": 0, "queries": 0, "usd_per_query": None,
                 "usd_per_lead": None, "usd_per_candidate_miss": None,
                 "lifetime_usd": 0, "confirmed_misses_lifetime": None,
                 "usd_per_confirmed_miss": None, "confirmed_miss_note": ""},
        "diagnostics": [],
        "leads": [{
            "claimed_company": "Mutation Test Co",
            "claimed_outlet": f"{name.title()} News",
            "verdict": "HELD",
            "matched": {"signal_id": "x", "company": "Mutation Test Co",
                        "source_url": f"https://app.{name}.co/news/x"},
        }],
    }
    worklist = {"generated_at": "", "ran_on": "2026-09-03", "basis": "test",
                "counts": result["counts"], "cost": result["cost"],
                "country_misses": {}, "industry_misses": {},
                "missing_total": 0, "leads": [], "instruction": ""}

    results_dir = tmp_path / "results"
    worklist_path = tmp_path / "worklist.json"
    result_path, worklist_path = report.write(
        result, worklist, results_dir=str(results_dir),
        worklist_path=str(worklist_path))

    for path in (result_path, worklist_path):
        text = open(path, encoding="utf-8").read().lower()
        assert name not in text, f"banned pattern survived a report.write() of {path}"
    written = json.loads(open(result_path, encoding="utf-8").read())
    assert provider_names.TAG_PREFIX in written["leads"][0]["matched"]["source_url"]
    assert provider_names.TAG_PREFIX in written["leads"][0]["claimed_outlet"]
    # Redacted, not dropped: the rest of the record still stands.
    assert written["leads"][0]["claimed_company"] == "Mutation Test Co"


def test_bootstrap_slug_and_host_are_redacted():
    """The other writer of a tracked label file. It builds its lines by hand
    rather than through `gate_ledger.record`, so it needs its own pin."""
    name = _names()[2]
    url = f"https://www.{name}.com/news/{name}-raises-a-round-in-berlin"
    assert name not in bootstrap_gate_labels.host_of(url).lower()
    slug = bootstrap_gate_labels.slug_text(url)
    assert slug and name not in slug.lower()


def test_this_test_file_names_nobody():
    """The fixtures above are built from base64 at runtime. Prove it."""
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        text = fh.read().lower()
    for idx, name in enumerate(_names()):
        assert name not in text, f"banned pattern #{idx + 1} is in this file"

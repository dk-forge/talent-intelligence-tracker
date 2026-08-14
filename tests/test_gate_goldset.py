"""The gate gold set: its shape, its scorer, and the surfaces it must not move.

Offline. No key, no network, no model. The set is committed data and the
production baseline is graded out of verdicts the ledger already recorded, so
everything here runs on a free runner.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import ab_models
from analysis.models import gate_goldset

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def doc():
    return gate_goldset.load()


# --- the set is not allowed to drift toward the easy items -------------------

def test_the_set_keeps_its_required_shape(doc):
    """`analysis/recall/goldset.py` refuses a set that is too small or too
    one-sided, for the reason its own comment gives: a set rebuilt from what is
    easy to find measures memory rather than reach. Same bar here."""
    shape = gate_goldset.REQUIRED_SHAPE
    items = doc["items"]
    assert len(items) >= shape["min_items"], (
        f"{len(items)} items against a floor of {shape['min_items']}; a gate "
        f"set this small cannot reject a model that misses one in ten")

    negatives = [i for i in items if i["gold_is_talent_signal"] is False]
    assert len(negatives) >= shape["min_negatives"], (
        f"only {len(negatives)} NO items. A set of positives measures recall "
        f"and says nothing about precision, and precision is where the money "
        f"is: every false YES buys an extraction call")

    hard = [i for i in items if i["hard"]]
    assert len(hard) >= shape["min_hard"], (
        f"only {len(hard)} items marked hard. A set of obvious items scores "
        f"every model at 100% and decides nothing")

    kinds = {i["provenance"].split(" ")[0].split(":")[0] for i in items}
    assert len(kinds) >= shape["min_provenances"], (
        f"every item came from {kinds}; one fixture is one sampling bias")


def test_every_item_carries_a_reason_a_human_can_argue_with(doc):
    """A label with no stated reason cannot be challenged, and an unchallengeable
    gold set is an opinion with a filename."""
    for item in doc["items"]:
        assert item["why"].strip(), item["id"]
        assert len(item["why"]) >= 25, (
            f"{item['id']} justifies its label in {len(item['why'])} "
            f"characters: {item['why']!r}")


def test_the_known_limits_are_carried_with_the_set(doc):
    """The set's ceiling travels with it. Every one of these is a thing a
    number computed from the set cannot express."""
    text = " ".join(gate_goldset.KNOWN_LIMITS).lower()
    for must in ("english", "precision", "extraction"):
        assert must in text, f"KNOWN_LIMITS no longer mentions {must!r}"


# --- three states, and absence of an answer is not one of the good ones ------

def test_ambiguous_is_excluded_from_the_denominator_and_not_counted_as_a_pass(doc):
    """Five items a careful reader could defend either way. Charging a model
    for the rubric's silence would manufacture a difference between models
    that is really a gap in GATE_SYSTEM."""
    ambiguous = [i for i in doc["items"] if i["gold_is_talent_signal"] is None]
    assert ambiguous, "the set no longer records any ambiguous item"
    scoreable = gate_goldset.scoreable(doc)
    assert len(scoreable) == len(doc["items"]) - len(ambiguous)
    ids = {i["id"] for i in scoreable}
    for item in ambiguous:
        assert item["id"] not in ids

    # And a model answering them cannot buy credit for it.
    answers = {i["id"]: True for i in doc["items"]}
    assert gate_goldset.score(doc, answers)["total"] == len(scoreable)


def test_an_unanswered_item_is_a_miss_and_never_a_skip(doc):
    """A gate that errors on an item drops it in production. Scoring silence
    as a skip would rank a broken model above a merely wrong one."""
    s = gate_goldset.score(doc, {})
    assert s["total"] == len(gate_goldset.scoreable(doc))
    assert s["fn"] > 0 and s["tp"] == 0, (
        "answering nothing scored as something other than missing everything")
    assert len(s["unanswered"]) == s["total"]


def test_the_interval_is_reported_and_it_is_the_repos_one_wilson(doc):
    """CLAUDE.md: every rate is published with its interval, from the single
    implementation, so the floor and the page cannot round it two ways."""
    from analysis.recall import stats

    assert gate_goldset.wilson is stats.wilson
    s = gate_goldset.score(doc, {i["id"]: True for i in doc["items"]})
    assert s["accuracy_lo"] < s["accuracy"] < s["accuracy_hi"]


# --- the free baseline -------------------------------------------------------

def test_the_incumbent_gate_has_a_measured_accuracy_and_it_is_not_perfect(doc):
    """The point of the whole exercise. Before this set the repo could say what
    the gate COST and not whether it was RIGHT, and `ab_models.py` could only
    say whether a challenger resembled it."""
    base = gate_goldset.production_baseline(doc)
    assert base["total"] >= 30, base["total"]
    assert 0.5 < base["accuracy"] < 1.0, (
        f"the live gate scored {base['accuracy']:.1%} on the hand labels; a "
        f"perfect score on a set with 17 hard items means the labels drifted "
        f"toward the gate rather than the gate being measured")
    assert base["fn"] >= 1, (
        "the recorded gate no longer misses any labelled signal — check "
        "whether the labels were relaxed rather than the gate improved")


def test_the_baseline_calls_no_model_and_reads_no_network(doc):
    """It is graded out of `production_gate`, which the ledger already wrote."""
    src = inspect.getsource(gate_goldset)
    assert "requests" not in src and "urllib" not in src
    assert "OPENROUTER" not in src


# --- the set asks the PRODUCTION question, on the PRODUCTION input -----------

def test_the_scorer_asks_gate_system_itself_rather_than_a_copy_of_it():
    """A benchmark that carries its own paraphrase of the prompt measures a
    prompt nobody ships. `run_gate_gold` reads classify.GATE_SYSTEM."""
    src = inspect.getsource(ab_models._gate_call)
    assert "classify.GATE_SYSTEM" in src, src
    assert "classify.GATE_CHARS" in src, (
        "the gold call no longer truncates where production truncates")


def test_no_gold_item_is_silently_truncated_by_the_production_bound(doc):
    from pipeline import classify

    for item in doc["items"]:
        assert len(item["text"]) <= classify.GATE_CHARS, (
            f"{item['id']} is {len(item['text'])} chars, past GATE_CHARS="
            f"{classify.GATE_CHARS}: the model would be scored on a label "
            f"written from text it never saw")


# --- one id per surface, each with its own default ---------------------------

#: Every place this repo sends a paid model call, and the environment variable
#: that pins it. The sibling project learned this the expensive way: its
#: classification model USED to default to its extraction model, so an A/B
#: measured on one surface would have silently moved two more. Nothing here may
#: inherit another surface's id.
PAID_SURFACES = {
    "gate": ("pipeline/classify.py", "GATE_MODEL", "TIT_GATE_MODEL"),
    "extraction": ("pipeline/classify.py", "MODEL", "TIT_MODEL"),
    "read_through": ("pipeline/classify.py", "READ_MODEL", "TIT_READ_MODEL"),
    "discovery": ("analysis/tripwire/ask.py", "MODEL", "TIT_TRIPWIRE_MODEL"),
}


def _module_default(relpath: str, name: str) -> tuple[str, str]:
    """(env var, literal default) for `NAME = os.environ.get("X", "lit")`.

    Parsed rather than imported, so the test reads what the file SAYS and not
    what this process's environment happens to make it.
    """
    tree = ast.parse((ROOT / relpath).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name
                   for t in node.targets):
            continue
        call = node.value
        assert isinstance(call, ast.Call), f"{relpath}:{name} is not a lookup"
        env, default = call.args[0].value, call.args[1].value
        return env, default
    raise AssertionError(f"{relpath} no longer assigns {name}")


def test_every_paid_surface_is_pinned_separately():
    """One id per surface, each changeable on its own evidence."""
    seen_env = {}
    for surface, (relpath, name, expected_env) in PAID_SURFACES.items():
        env, default = _module_default(relpath, name)
        assert env == expected_env, (
            f"{surface} reads {env}, not {expected_env}")
        assert default and default.strip(), (
            f"{surface} has no default model id; an unset variable would send "
            f"an empty model to the provider")
        assert env not in seen_env, (
            f"{surface} and {seen_env[env]} both read {env}: one variable "
            f"across two surfaces means a swap measured on one moves both, "
            f"which is the defect the sibling repo pinned its classification "
            f"model separately to avoid")
        seen_env[env] = surface


def test_no_surface_inherits_another_surfaces_default():
    """Two surfaces MAY name the same model, and today none do. What they may
    not do is name it BY REFERENCE, because then one edit moves both and the
    diff shows one line."""
    src = (ROOT / "pipeline" / "classify.py").read_text()
    for name in ("GATE_MODEL", "READ_MODEL"):
        line = next(ln for ln in src.splitlines()
                    if ln.startswith(f"{name} = "))
        assert "MODEL)" not in line and ", MODEL" not in line, (
            f"{name} falls back to another surface's constant: {line!r}")


def test_the_cost_model_prices_the_surfaces_the_pipeline_actually_uses():
    """`cost_projection.py` keeps its own copy of the three ids. It is a
    forecast of the deployment, so a drift there is a bill projected for a
    configuration nobody runs."""
    import cost_projection

    for surface, key in (("gate", "gate"), ("extraction", "extract"),
                         ("read_through", "read")):
        relpath, name, _env = PAID_SURFACES[surface]
        _env_name, default = _module_default(relpath, name)
        assert cost_projection.MODELS[key] == default, (
            f"cost_projection prices {surface} as "
            f"{cost_projection.MODELS[key]} while {relpath} runs {default}")

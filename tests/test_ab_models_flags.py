"""`ab_models.py --models` and `--limit`: the spend controls a session uses to
measure two candidates without buying the whole default list.

Both flags exist so a measurement can be sized to a budget. What they must
never do is drop the incumbent, because every table the harness prints is a
comparison against it and a table with no baseline column measures nothing.
"""

import argparse

import ab_models


def _args(**kw):
    base = dict(gate_gold=False, extraction=False, readthrough=False,
                models="", limit=0)
    base.update(kw)
    return argparse.Namespace(**base)


def test_restrict_keeps_the_incumbent_first_even_when_not_named(monkeypatch):
    monkeypatch.setattr(ab_models, "EXTRACTION_MODELS",
                        ["inc/model", "a/one", "b/two"])
    ab_models._restrict_candidates(_args(extraction=True, models="b/two,c/new"))
    assert ab_models.EXTRACTION_MODELS == ["inc/model", "b/two", "c/new"]


def test_naming_the_incumbent_does_not_list_it_twice(monkeypatch):
    monkeypatch.setattr(ab_models, "GATE_GOLD_MODELS", ["inc/gate", "x/y"])
    ab_models._restrict_candidates(_args(gate_gold=True,
                                         models="x/y, inc/gate ,z/w"))
    assert ab_models.GATE_GOLD_MODELS == ["inc/gate", "x/y", "z/w"]


def test_each_mode_narrows_its_own_list_and_no_other(monkeypatch):
    monkeypatch.setattr(ab_models, "GATE_MODELS", ["g/inc", "g/a"])
    monkeypatch.setattr(ab_models, "READTHROUGH_MODELS", ["r/inc", "r/a"])
    monkeypatch.setattr(ab_models, "EXTRACTION_MODELS", ["e/inc", "e/a"])
    monkeypatch.setattr(ab_models, "GATE_GOLD_MODELS", ["gg/inc", "gg/a"])
    ab_models._restrict_candidates(_args(readthrough=True, models="r/new"))
    assert ab_models.READTHROUGH_MODELS == ["r/inc", "r/new"]
    assert ab_models.GATE_MODELS == ["g/inc", "g/a"]
    assert ab_models.EXTRACTION_MODELS == ["e/inc", "e/a"]
    assert ab_models.GATE_GOLD_MODELS == ["gg/inc", "gg/a"]


def test_the_default_lists_still_open_with_the_incumbents():
    """The flags read the incumbent as element zero, so element zero has to
    stay the incumbent: the extraction model is `classify.MODEL`'s default and
    the gate-gold baseline is the live gate."""
    from pipeline import classify
    assert ab_models.EXTRACTION_MODELS[0] == classify.MODEL
    assert ab_models.GATE_GOLD_MODELS[0] == classify.GATE_MODEL

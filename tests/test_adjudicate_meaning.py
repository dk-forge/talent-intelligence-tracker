"""The agreement rule decides on MEANING, not spelling (2026-09-28).

The question that matters for the site is whether a figure is summed into the
raise totals. Referees who agree it is NOT a raise but name the basis
differently ("acquired" / "acquisition" / "outbound_investment") agreed on
that question; the old exact-string rule called them a split and left the row
for a human the owner said never to page. Rows: Quanome 104ac8e0 and
MetaOptics 267ce439, adjudicate-rows run 36466327334.
"""
from __future__ import annotations

import json

import adjudicate_guardrail as adj

STORED = 18_800_000


def _v(action, amount=None, basis=None):
    return adj.parse_verdict(json.dumps({
        "recommended": action, "corrected_amount": amount, "corrected_basis": basis,
        "money_basis": basis or "unknown", "confidence": 95, "reasoning": "quoted"}))


def _decide3(verdicts, stored=STORED):
    judge = adj.amount_judge(stored)
    status, action, correction = judge(dict(list(verdicts.items())[:2]))
    if status == "disagree":
        status, action, correction = adj.majority(verdicts, judge)
    return status, action, correction


def test_synonyms_are_normalised_to_the_canonical_basis():
    assert _v("edit", 1, "purchase")["corrected_basis"] == "acquisition"
    assert _v("edit", 1, "acquired")["corrected_basis"] == "acquisition"
    assert _v("edit", 1, "Acquisition")["corrected_basis"] == "acquisition"
    assert _v("edit", 1, "grant")["corrected_basis"] == "state_funding"
    assert _v("edit", 1, "loan")["corrected_basis"] is None, "unknown label is unparsed"


def test_quanome_three_non_raise_synonyms_resolve_non_raise():
    verdicts = {"a": _v("edit", STORED, "outbound_investment"),
                "b": _v("edit", STORED, "acquired"),
                "c": _v("edit", STORED, "acquisition")}
    # The first two already agree it is not a raise: no third referee is
    # needed, and differing non-raise bases take the fallback.
    status, action, correction = _decide3(verdicts)
    assert (status, action) == ("agree", "edit")
    assert correction == {"corrected_basis": adj.NOT_A_RAISE_FALLBACK,
                          "corrected_amount": STORED}
    # With all three on the table, the pair naming one basis outranks it.
    status, action, correction = adj.majority(verdicts, adj.amount_judge(STORED))
    assert correction == {"corrected_amount": STORED, "corrected_basis": "acquisition"}


def test_metaoptics_two_of_three_non_raise_resolve_with_a_basis_only_fix():
    verdicts = {"a": _v("edit", 10_000_000, "pledge"),
                "b": _v("edit", None, "project_finance"),
                "c": _v("edit", 10_000_000, "company_raise")}
    status, action, correction = _decide3(verdicts, stored=10_000_000)
    assert (status, action) == ("agree", "edit")
    assert correction["corrected_basis"] == adj.NOT_A_RAISE_FALLBACK == "pledge"
    assert "corrected_amount" not in correction, "a null amount leaves the stored figure alone"


def test_majority_raise_is_kept_as_a_raise():
    verdicts = {"a": _v("accept"),
                "b": _v("edit", STORED, "company_raise"),
                "c": _v("edit", STORED, "pledge")}
    status, action, correction = _decide3(verdicts)
    assert (status, action, correction) == ("agree", "accept", None)


def test_a_raise_at_a_different_figure_is_not_a_raise_agreement():
    status, _, _ = adj.amount_judge(STORED)({"a": _v("accept"),
                                            "b": _v("edit", 5, "company_raise")})
    assert status == "disagree"


def test_one_one_with_an_unusable_third_stays_unresolved():
    verdicts = {"a": _v("accept"), "b": _v("edit", STORED, "pledge"), "c": None}
    assert _decide3(verdicts)[0] == "disagree"
    verdicts["c"] = _v("edit", STORED, "loan")  # unknown label: no basis, no side
    assert _decide3(verdicts)[0] == "disagree"


def test_full_agreement_behaves_as_before():
    assert adj.decide({"a": _v("reject"), "b": _v("reject")}) == ("agree", "reject", None)
    assert adj.decide({"a": _v("edit", 7, "ipo"), "b": _v("edit", 7, "ipo")}) == (
        "agree", "edit", {"corrected_amount": 7, "corrected_basis": "ipo"})

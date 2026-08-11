"""The gate has to recognise a signal written the way a newsroom writes it.

Two measured gaps, both found on 2026-07-30 by re-reading what the free
keyword gate REJECTED rather than what it accepted:

1. Portuguese had exactly one funding phrase, "rodada de investimento", which
   is the formal register. Brazilian business copy writes captar, aportar and
   levantar. Re-reading 156 rejected items from one Brazilian sweep found 2
   genuine misses (~1.3%).

2. C-suite acronyms were in the term list only behind an English verb
   ("names its new CFO"). A headline whose only signal is the acronym --
   "Governanca Brasil tem novo CRO", "Nombran nuevo CMO" -- was rejected in
   every language including English.

The false-negative cases below are what those fixes recover. The
false-POSITIVE cases matter as much and are the reason the fix is narrow:
this gate stands in front of roughly 18,600 candidates a day, and every one
it wrongly admits spends a share of a read budget that is the binding
constraint on the whole corpus. A gate that lets everything through is the
budget spent on nothing.
"""

from __future__ import annotations

import re
import unittest

from pipeline import prefilter


def _gate() -> re.Pattern:
    """The same expression the prefilter builds, assembled the same way."""
    terms = (prefilter._EMPLOYMENT_TERMS
             + prefilter._EMPLOYMENT_TERMS_INTL
             + prefilter._FUNDING_TERMS)
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.I)


class PortugueseFundingTests(unittest.TestCase):
    def test_the_two_measured_misses_now_pass(self):
        gate = _gate()
        for headline in (
            "Governanca Brasil tem novo CRO",
            "GS1 Ventures faz seu primeiro aporte",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(gate.search(headline))

    def test_the_verbs_a_brazilian_newsroom_actually_uses(self):
        gate = _gate()
        for headline in (
            "Vammo capta US$ 45 milhoes em rodada Serie B",
            "Startup levantou R$ 30 milhoes",
            "Fintech anuncia captação de R$ 12 milhoes",
            "Empresa recebe aporte de R$ 8 milhoes",
            "BemAgro fecha rodada pre-seed",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(gate.search(headline))

    def test_the_words_deliberately_left_out_still_do_not_match(self):
        # Bare "rodada", "levanta" and "capta" were considered and refused.
        # Each of these is why.
        gate = _gate()
        for headline in (
            "Policia faz captura de suspeito em Sao Paulo",
            "Levantamento aponta queda no consumo",
            "Rodada de negociacoes entre paises termina sem acordo",
            "Time vence na terceira rodada do campeonato",
        ):
            with self.subTest(headline=headline):
                self.assertFalse(gate.search(headline),
                                 f"{headline!r} would flood the gate")


class CSuiteAcronymTests(unittest.TestCase):
    def test_an_acronym_is_signal_enough_in_any_language(self):
        gate = _gate()
        for headline in (
            "Governanca Brasil tem novo CRO",
            "Nombran nuevo CMO",
            "Empresa nomeia novo CFO",
            "New CHRO appointed",
            "Konzern beruft neuen COO",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(gate.search(headline))

    def test_a_hyphenated_word_is_not_an_acronym(self):
        # "Cro-Magnon" matched before the (?!-\w) lookahead, because a hyphen
        # is a word boundary.
        gate = _gate()
        self.assertFalse(gate.search("The Cro-Magnon exhibit opens"))

    def test_ordinary_words_containing_these_letters_do_not_match(self):
        gate = _gate()
        for headline in (
            "Cairo hosts summit on trade",
            "Croatia beats Spain",
            "Cost of living rises again",
            "Micro and macro trends in retail",
            "A new CD player review",
        ):
            with self.subTest(headline=headline):
                self.assertFalse(gate.search(headline))


class KnownLimitTests(unittest.TestCase):
    def test_cjk_text_without_spaces_is_a_documented_blind_spot(self):
        """Not a bug to fix here, but it must not be discovered by surprise.

        The whole term list is wrapped in \\b...\\b, and \\b does not fire
        between a Japanese character and a Latin one because both are word
        characters. So an acronym embedded in CJK copy is invisible to the
        gate -- and so is every other term. This asserts the CURRENT
        behaviour so that anyone who fixes the anchoring sees this test go
        red and knows to widen it rather than delete it.
        """
        gate = _gate()
        self.assertFalse(gate.search("新しいCEOが就任"),
                         "if this now passes, the \\b anchoring was changed: "
                         "re-measure the gate against real CJK feeds and "
                         "update this test rather than removing it")


if __name__ == "__main__":
    unittest.main()

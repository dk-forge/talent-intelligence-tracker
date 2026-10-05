"""Split and metric code of the free-classifier experiment (classifier_eval).

WHY. Every candidate must be scored on the SAME held-out items, or the
comparison is meaningless; and the "skip the paid LLM" figure must never
claim a threshold that drops more positives than it says. Pure Python, no
model is loaded here.
"""
import importlib.util
import pathlib
import unittest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "analysis/models/classifier_eval.py"
_spec = importlib.util.spec_from_file_location("classifier_eval_under_test", _PATH)
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)


class Split(unittest.TestCase):
    def test_same_seed_same_split_and_disjoint(self):
        labels = [True] * 31 + [False] * 169
        a, b = ce.stratified_split(labels), ce.stratified_split(labels)
        self.assertEqual(a, b)
        train, test = a
        self.assertFalse(set(train) & set(test))
        self.assertEqual(sorted(train + test), list(range(200)))

    def test_split_is_stratified_70_30(self):
        labels = [True] * 30 + [False] * 70
        _, test = ce.stratified_split(labels)
        self.assertEqual(len(test), 30)
        self.assertEqual(sum(labels[i] for i in test), 9)

    def test_other_seed_other_split(self):
        labels = [True] * 30 + [False] * 70
        self.assertNotEqual(ce.stratified_split(labels, seed=1),
                            ce.stratified_split(labels, seed=2))


class Metrics(unittest.TestCase):
    def test_positive_class_metrics(self):
        gold = [True, True, False, False]
        pred = [True, False, True, False]
        m = ce.metrics(gold, pred)
        self.assertEqual(m["accuracy"], 0.5)
        self.assertEqual(m["precision"], 0.5)
        self.assertEqual(m["recall"], 0.5)
        self.assertEqual(m["f1"], 0.5)

    def test_always_no_has_zero_f1_and_majority_accuracy(self):
        gold = [True] + [False] * 9
        self.assertEqual(ce.always_no_accuracy(gold), 0.9)
        self.assertEqual(ce.metrics(gold, [False] * 10)["f1"], 0.0)


class PreFilterValue(unittest.TestCase):
    def test_threshold_keeps_every_positive_when_there_are_few(self):
        # 10 positives: 98% of 10 rounds up to all 10.
        gold = [True] * 10 + [False] * 10
        scores = [0.9] * 9 + [0.3] + [0.1] * 5 + [0.5] * 5
        skip, t = ce.skippable_at_recall(gold, scores)
        self.assertEqual(t, 0.3)
        self.assertEqual(skip, 0.25)  # the five 0.1 negatives

    def test_never_skips_a_positive_beyond_the_budget(self):
        gold = [True] * 100 + [False] * 100
        scores = [i / 100 for i in range(100)] + [0.0] * 100
        skip, t = ce.skippable_at_recall(gold, scores)
        kept = sum(1 for g, s in zip(gold, scores) if g and s >= t)
        self.assertGreaterEqual(kept, 98)
        self.assertEqual(kept, 98)

    def test_no_positives_means_no_claim(self):
        self.assertEqual(ce.skippable_at_recall([False, False], [0.1, 0.2]),
                         (0.0, None))


if __name__ == "__main__":
    unittest.main()

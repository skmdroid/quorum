"""The detection benchmark must be deterministic and gate-able under the mock
provider — no network, no keys, no flakiness."""
import os
import unittest

from quorum.evals import evaluate, SCORE_CATEGORIES

DATASET = os.path.join(os.path.dirname(os.path.dirname(__file__)), "evals", "dataset")


class TestEvals(unittest.TestCase):
    def setUp(self):
        self.report = evaluate("mock", DATASET)

    def test_corpus_loaded(self):
        self.assertEqual(self.report.n_cases, 11)
        self.assertEqual(self.report.n_clean_cases, 2)
        self.assertEqual(set(self.report.by_category), set(SCORE_CATEGORIES))

    def test_no_false_alarms_on_clean_code(self):
        # the deterministic heuristic must stay silent on the clean diffs
        self.assertEqual(self.report.clean_false_positives(), 0)

    def test_recall_clears_floor(self):
        # recall is capped (~0.67) because 3 defects are semantic and need an
        # LLM — but the keyword-detectable defects must all be caught.
        self.assertGreaterEqual(self.report.recall(), 0.6)

    def test_security_defects_caught(self):
        self.assertGreaterEqual(self.report.by_category["security"].detected, 3)

    def test_deterministic(self):
        again = evaluate("mock", DATASET)
        self.assertEqual(self.report.to_dict()["overall"],
                         again.to_dict()["overall"])


if __name__ == "__main__":
    unittest.main()

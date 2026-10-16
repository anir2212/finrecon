"""Regression for the hand-authored boundary fixture and empty source feeds."""

import unittest

from eval.independent_cases import run


class IndependentCasesTests(unittest.TestCase):
    def test_all_boundary_labels_and_source_rows(self):
        report = run()
        self.assertEqual(report["cases"], 15)
        self.assertEqual(report["correct"], 15)
        self.assertEqual(report["traceable_figures"], 15)
        self.assertEqual(report["source_rows_traced"], 40)
        self.assertEqual(report["mismatches"], [])


if __name__ == "__main__":
    unittest.main()

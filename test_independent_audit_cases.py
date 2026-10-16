"""Hand-authored posting-time boundaries and anomaly evaluation evidence."""

import unittest

from eval.independent_audit_cases import run


class IndependentAuditCasesTests(unittest.TestCase):
    def test_after_hours_boundaries_and_optional_anomaly_metrics(self):
        report = run()
        self.assertEqual(report["rows"], 24)
        self.assertEqual(report["after_hours_ids"], ["L-20", "L-21"])
        self.assertEqual(report["boundary_in_hours_ids"], ["L-22", "L-23"])
        self.assertEqual(report["audit_evaluation"]["true_positives"], 2)
        self.assertEqual(report["audit_evaluation"]["false_positives"], 0)
        self.assertEqual(report["audit_evaluation"]["false_negatives"], 0)
        self.assertGreaterEqual(report["traceable_figures"], 2)
        if report["anomaly_evaluation"]["status"] == "measured":
            score = report["anomaly_evaluation"]
            self.assertEqual(score["injected"], 2)
            self.assertEqual(score["tp"] + score["fn"], 2)
            self.assertEqual(score["tp"] + score["fp"], score["flagged"])


if __name__ == "__main__":
    unittest.main()

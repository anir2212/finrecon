import tempfile
import unittest
from pathlib import Path
from finrecon import fuzzy_suggestions, generate, load, reconcile


class ReconTests(unittest.TestCase):
    def test_ground_truth(self):
        with tempfile.TemporaryDirectory() as folder:
            generate(folder, n=40)
            results = reconcile(load(Path(folder)/"ledger.csv"), load(Path(folder)/"bank.csv"))
            by_ref = {r["ref"]: r for r in results}
            self.assertEqual(by_ref["PAY-0017"]["status"], "duplicate_bank")
            self.assertEqual(by_ref["PAY-0000"]["status"], "missing_bank")
            self.assertEqual(by_ref["PAY-0013"]["status"], "missing_bank")
            self.assertEqual(by_ref["PAY-0011"]["status"], "amount_mismatch")
            self.assertEqual(by_ref["PAY-0001"]["status"], "matched")

    def test_tolerance(self):
        left = [{"id":"L", "ref":"R", "currency":"SGD", "amount_cents":100}]
        right = [{"id":"B", "ref":"R", "currency":"SGD", "amount_cents":101}]
        self.assertEqual(reconcile(left, right, 1)[0]["status"], "matched")

    def test_many_to_one_and_review_only_fuzzy(self):
        left = [{"id":"L", "ref":"PAY-123", "currency":"SGD", "amount_cents":100}]
        split = [{"id":"B1", "ref":"PAY-123", "currency":"SGD", "amount_cents":60}, {"id":"B2", "ref":"PAY-123", "currency":"SGD", "amount_cents":40}]
        self.assertEqual(reconcile(left, split)[0]["status"], "many_to_one")
        typo = [{"id":"B3", "ref":"PAY-132", "currency":"SGD", "amount_cents":100}]
        matches = reconcile(left, typo)
        self.assertEqual(matches[0]["status"], "missing_bank")
        self.assertEqual(fuzzy_suggestions(left, typo, matches, .7)[0]["status"], "needs_manual_review")


if __name__ == "__main__":
    unittest.main()

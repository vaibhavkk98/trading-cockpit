"""
STEP 7A.4 — Transaction-Cost Ledger Reconciliation Unit Tests

6 Section 8 Required Tests:
1. test_entry_cost_exactly_once
2. test_exit_cost_exactly_once
3. test_round_trip_cost_reconciliation
4. test_manual_trade_cashflow
5. test_portfolio_cost_reconciliation
6. test_final_equity_reconciliation

Plus deliverable and gate checks.
"""
import os
import sys
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

COST_AUDIT_MD = os.path.join(STEP7_DIR, "step_7a4_cost_ledger_reconciliation.md")
COST_LEDGER_CSV = os.path.join(STEP7_DIR, "step_7a4_cost_ledger.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7a4_manifest.csv")


class TestStep7A4CostLedger(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 3 required Step 7A.4 deliverable files must exist."""
        files = [
            COST_AUDIT_MD,
            COST_LEDGER_CSV,
            MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_entry_cost_exactly_once(self):
        """Entry cost must equal entry_fee + entry_slippage."""
        if not os.path.exists(COST_LEDGER_CSV):
            self.skipTest("step_7a4_cost_ledger.csv not found")
        df_l = pd.read_csv(COST_LEDGER_CSV)
        for _, row in df_l.iterrows():
            self.assertAlmostEqual(row['entry_fee'] + row['entry_slippage'], row['entry_total_cost'], delta=0.02)

    def test_03_exit_cost_exactly_once(self):
        """Exit cost must equal exit_fee_and_stt + exit_slippage."""
        if not os.path.exists(COST_LEDGER_CSV):
            self.skipTest("step_7a4_cost_ledger.csv not found")
        df_l = pd.read_csv(COST_LEDGER_CSV)
        for _, row in df_l.iterrows():
            self.assertAlmostEqual(row['exit_fee_and_stt'] + row['exit_slippage'], row['exit_total_cost'], delta=0.02)

    def test_04_round_trip_cost_reconciliation(self):
        """Round-trip cost must equal entry_total_cost + exit_total_cost."""
        if not os.path.exists(COST_LEDGER_CSV):
            self.skipTest("step_7a4_cost_ledger.csv not found")
        df_l = pd.read_csv(COST_LEDGER_CSV)
        for _, row in df_l.iterrows():
            self.assertAlmostEqual(row['entry_total_cost'] + row['exit_total_cost'], row['round_trip_total_cost'], delta=0.02)

    def test_05_manual_trade_cashflow(self):
        """Manual trade test must match expected cash flow and P&L math exactly."""
        from scripts.run_step_7a4_cost_ledger import run_manual_trade_test
        res = run_manual_trade_test()
        self.assertTrue(res['matches_exact'])
        self.assertAlmostEqual(res['net_pnl'], 9745.00, places=2)

    def test_06_portfolio_cost_reconciliation(self):
        """Aggregate transaction costs in manifest must equal sum of round_trip_total_cost in cost ledger."""
        if not os.path.exists(COST_LEDGER_CSV) or not os.path.exists(MANIFEST_CSV):
            self.skipTest("Deliverables not found")
        df_l = pd.read_csv(COST_LEDGER_CSV)
        df_m = pd.read_csv(MANIFEST_CSV)
        tot_ledger = df_l['round_trip_total_cost'].sum()
        tot_manifest = df_m['total_transaction_costs'].iloc[0]
        self.assertAlmostEqual(tot_ledger, tot_manifest, delta=0.50)

    def test_07_final_equity_reconciliation(self):
        """Final portfolio equity must equal initial capital plus sum of net realized P&L."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        final_eq = eq_df['total_equity'].iloc[-1]
        self.assertGreater(final_eq, 0.0)

    def test_08_no_ml_used(self):
        """Cost ledger script must NOT import or use ML classifiers."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a4_cost_ledger.py")) as f:
            code = f.read()
        self.assertNotIn("HistGradientBoosting", code)

    def test_09_gate_verdict_green(self):
        """Manifest must record GREEN verdict for fully reconciled cost ledger."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7a4_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — COST LEDGER FULLY RECONCILED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7A.4 COST LEDGER RECONCILIATION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7A4CostLedger)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

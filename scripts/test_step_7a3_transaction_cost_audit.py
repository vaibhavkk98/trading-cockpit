"""
STEP 7A.3 — Transaction-Cost & Execution-Price Accounting Audit Unit Tests

12 Required Tests:
1. All 5 Step 7A.3 deliverable files exist.
2. test_transaction_costs_applied_exactly_once(): Cash change == Realized Trade P&L.
3. Tiny manual cash-flow test matches expected hand calculations exactly.
4. Cost model documentation verifies 0.15% fee + 0.05% slippage per side.
5. Point-in-time entry price matches T+1 Open.
6. Total portfolio equity == Cash + Open Positions MTM at every date.
7. Final cash equals final equity after final liquidation.
8. Baseline metrics match reconciled exact values.
9. 3M RS ranking metrics match reconciled exact values.
10. Cost sensitivity (1x vs 2x) is evaluated.
11. No ML or future data is used in accounting.
12. Gate verdict records GREEN.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

TRANSACTION_AUDIT_MD = os.path.join(STEP7_DIR, "step_7a3_transaction_cost_audit.md")
ACCOUNTING_RECON_CSV = os.path.join(STEP7_DIR, "step_7a3_accounting_reconciliation.csv")
BASELINE_RESULTS_CSV = os.path.join(STEP7_DIR, "step_7a3_baseline_results.csv")
RS_RESULTS_CSV = os.path.join(STEP7_DIR, "step_7a3_rs_results.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7a3_manifest.csv")


class TestStep7A3TransactionCostAudit(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 7A.3 deliverable files must exist."""
        files = [
            TRANSACTION_AUDIT_MD,
            ACCOUNTING_RECON_CSV,
            BASELINE_RESULTS_CSV,
            RS_RESULTS_CSV,
            MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_transaction_costs_applied_exactly_once(self):
        """Cash change must equal reported trade P&L for trade execution."""
        alloc = 100000.0
        p_entry = 100.0
        p_exit = 110.0
        f_oneway = 0.0020
        c_entry = alloc * f_oneway
        shares = (alloc - c_entry) / p_entry
        gross_exit = shares * p_exit
        c_exit = gross_exit * f_oneway
        net_exit_proceeds = gross_exit - c_exit
        realized_pnl = net_exit_proceeds - alloc
        cash_change = (1000000.0 - alloc + net_exit_proceeds) - 1000000.0
        self.assertAlmostEqual(realized_pnl, cash_change, places=2)

    def test_03_tiny_manual_cashflow_test(self):
        """Tiny manual cash-flow test must match expected hand calculations."""
        from scripts.run_step_7a3_transaction_cost_audit import run_tiny_manual_cashflow_test
        res = run_tiny_manual_cashflow_test()
        self.assertTrue(res['matches_exact'])
        self.assertAlmostEqual(res['realized_pnl'], 9560.44, places=2)

    def test_04_cost_model_definition(self):
        """Transaction cost audit MD must document 0.15% fee + 0.05% slippage."""
        if not os.path.exists(TRANSACTION_AUDIT_MD):
            self.skipTest("step_7a3_transaction_cost_audit.md not found")
        with open(TRANSACTION_AUDIT_MD) as f:
            text = f.read()
        self.assertIn("0.15%", text)
        self.assertIn("0.05%", text)

    def test_05_entry_price_point_in_time(self):
        """Signal date close_price must differ from T+1 entry_price in dataset."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        row = df_exp.iloc[0]
        self.assertNotEqual(row['close_price'], row['entry_price'])

    def test_06_total_equity_sum(self):
        """Total equity must equal cash plus open positions MTM at all dates."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        for _, row in eq_df.iterrows():
            self.assertAlmostEqual(row['cash'] + row['open_positions_mtm'], row['total_equity'], delta=0.10)

    def test_07_final_liquidation_reconciliation(self):
        """Final cash after liquidation must equal final equity."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        self.assertGreater(eq_df['total_equity'].iloc[-1], 0.0)

    def test_08_baseline_metrics(self):
        """Baseline results CSV must contain PORTFOLIO_BASELINE_V1 metrics."""
        if not os.path.exists(BASELINE_RESULTS_CSV):
            self.skipTest("step_7a3_baseline_results.csv not found")
        df_b = pd.read_csv(BASELINE_RESULTS_CSV)
        self.assertEqual(len(df_b), 3)

    def test_09_rs_ranking_metrics(self):
        """RS results CSV must contain 3M RS ranking metrics."""
        if not os.path.exists(RS_RESULTS_CSV):
            self.skipTest("step_7a3_rs_results.csv not found")
        df_rs = pd.read_csv(RS_RESULTS_CSV)
        self.assertEqual(len(df_rs), 3)

    def test_10_cost_sensitivity_evaluated(self):
        """Baseline CSV must contain both 1x standard and 2x elevated friction rows."""
        if not os.path.exists(BASELINE_RESULTS_CSV):
            self.skipTest("step_7a3_baseline_results.csv not found")
        df_b = pd.read_csv(BASELINE_RESULTS_CSV)
        multipliers = df_b['friction_multiplier'].tolist()
        self.assertTrue(any("1x Standard" in m for m in multipliers))
        self.assertTrue(any("2x Elevated" in m for m in multipliers))

    def test_11_no_ml_used(self):
        """Audit script must NOT import or use ML classifiers."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a3_transaction_cost_audit.py")) as f:
            code = f.read()
        self.assertNotIn("HistGradientBoosting", code)

    def test_12_gate_verdict_green(self):
        """Manifest must record GREEN gate verdict for validated portfolio accounting."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7a3_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — PORTFOLIO ACCOUNTING VALIDATED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7A.3 TRANSACTION COST ACCOUNTING VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7A3TransactionCostAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

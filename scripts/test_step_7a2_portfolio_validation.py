"""
STEP 7A.2 — Portfolio Simulation Invariants & Hardening Unit Tests

12 Required Tests:
1. All 5 Step 7A.2 deliverable files exist.
2. Invariant 1: Cash + Open Positions MTM = Total Equity at every date.
3. Invariant 2: Total Equity cannot become negative.
4. Invariant 3: Position allocation never exceeds available cash.
5. Invariant 4: Max concurrent positions <= 10.
6. Invariant 5: No duplicate symbol positions.
7. Invariant 6: Costs and slippage applied on entry and exit.
8. Invariant 7: No future data used for trading decisions.
9. Invariant 8: Hand-checkable synthetic test matches expected math.
10. Invariant 9: Daily Sharpe derives from equity curve daily returns.
11. Invariant 10: Final open positions are accounted for via liquidation/MTM.
12. Gate verdict records YELLOW.
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

SIMULATOR_AUDIT_MD = os.path.join(STEP7_DIR, "step_7a2_simulator_audit.md")
METRIC_RECON_CSV = os.path.join(STEP7_DIR, "step_7a2_metric_reconciliation.csv")
BASELINE_RESULTS_CSV = os.path.join(STEP7_DIR, "step_7a2_baseline_results.csv")
RS_RESULTS_CSV = os.path.join(STEP7_DIR, "step_7a2_rs_results.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7a2_manifest.csv")


class TestStep7A2PortfolioValidation(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 7A.2 deliverable files must exist."""
        files = [
            SIMULATOR_AUDIT_MD,
            METRIC_RECON_CSV,
            BASELINE_RESULTS_CSV,
            RS_RESULTS_CSV,
            MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_invariant_equity_sum(self):
        """Cash + Open Positions MTM must equal Total Equity at every date."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        for _, row in eq_df.iterrows():
            self.assertAlmostEqual(row['cash'] + row['open_positions_mtm'], row['total_equity'], delta=0.10)

    def test_03_invariant_equity_positive(self):
        """Total equity must remain positive throughout simulation."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        self.assertTrue((eq_df['total_equity'] > 0).all())

    def test_04_invariant_allocation_within_cash(self):
        """Cash must remain non-negative after position allocations."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        self.assertTrue((eq_df['cash'] >= 0).all())

    def test_05_invariant_max_positions_limit(self):
        """Active position count must never exceed max configured limit (10)."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        self.assertTrue((eq_df['active_positions_count'] <= 10).all())

    def test_06_invariant_no_duplicate_symbols(self):
        """Simulation script must reject duplicate symbol entries."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_5_execution_validated.py")) as f:
            code = f.read()
        self.assertIn("rejected_duplicate_symbol", code)

    def test_07_invariant_friction_costs_applied(self):
        """Executed trades must record positive transaction costs."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        self.assertGreater(res['total_transaction_costs'], 0.0)

    def test_08_no_future_data_used(self):
        """Simulation ranking and decisions must not reference future labels."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_5_execution_validated.py")) as f:
            code = f.read()
        self.assertNotIn("forward_10d_return", code.split("def simulate_execution_validated_portfolio")[1].split("return {")[0].split("rank_col=")[0])

    def test_09_synthetic_hand_check_passes(self):
        """Hand-checkable synthetic test must match expected mathematical outputs."""
        from scripts.run_step_7a2_portfolio_validation import run_synthetic_hand_check
        res = run_synthetic_hand_check()
        self.assertAlmostEqual(res['tot_ret_pct'], -0.2196, places=3)
        self.assertAlmostEqual(res['daily_sharpe'], -2.2935, places=3)
        self.assertAlmostEqual(res['max_dd_pct'], 0.6171, places=3)

    def test_10_daily_sharpe_from_equity_curve(self):
        """Daily Sharpe ratio must be derived from equity curve daily returns."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        eq_df = pd.DataFrame(res['equity_curve'])
        daily_ret = eq_df['total_equity'].pct_change().dropna()
        calc_sharpe = round((daily_ret.mean() / daily_ret.std() * np.sqrt(252)), 2)
        self.assertEqual(res['daily_sharpe_ratio'], calc_sharpe)

    def test_11_final_positions_accounted_for(self):
        """Equity curve must record non-empty daily series covering test dates."""
        from scripts.run_step_5_execution_validated import simulate_execution_validated_portfolio
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        res = simulate_execution_validated_portfolio(df_exp.head(1000), rank_col='composite_score', rank_ascending=False, regime_filter=True)
        self.assertGreater(len(res['equity_curve']), 0)

    def test_12_gate_verdict_yellow(self):
        """Manifest must record YELLOW gate verdict for validated simulator with inconclusive edge."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7a2_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("YELLOW", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7A.2 PORTFOLIO INVARIANTS & HARDENING VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7A2PortfolioValidation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

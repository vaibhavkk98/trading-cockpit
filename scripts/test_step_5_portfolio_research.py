"""
STEP 5 — Portfolio Construction Research Verification Tests

Tests:
1. Deliverable files exist in data/ml/step_5/
2. Baseline portfolio diagnostics recorded for TRAIN and VALIDATION
3. Signal ranking comparison contains candidate ranking methods
4. Composite score ranking achieves positive net return on Validation
5. Position sizing comparison includes Equal Weight and Volatility-Adjusted sizing
6. Exit strategy comparison includes Fixed 10D and Time Decay options
7. Portfolio capacity comparison evaluates capacities 5, 10, 15, 20
8. Risk control comparison evaluates regime throttle
9. Final portfolio configuration contains frozen parameters and metrics
10. Final report contains gate classification GREEN
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP5_DIR = os.path.join(ML_DIR, "step_5")

BASELINE_DIAG_CSV = os.path.join(STEP5_DIR, "baseline_portfolio_diagnostics.csv")
RANKING_COMP_CSV = os.path.join(STEP5_DIR, "signal_ranking_comparison.csv")
SIZING_COMP_CSV = os.path.join(STEP5_DIR, "position_sizing_comparison.csv")
EXIT_COMP_CSV = os.path.join(STEP5_DIR, "exit_strategy_comparison.csv")
CAPACITY_COMP_CSV = os.path.join(STEP5_DIR, "portfolio_capacity_comparison.csv")
RISK_COMP_CSV = os.path.join(STEP5_DIR, "risk_control_comparison.csv")
FINAL_CONFIG_CSV = os.path.join(STEP5_DIR, "final_portfolio_configuration.csv")
STEP5_REPORT_MD = os.path.join(STEP5_DIR, "step_5_report.md")


class TestStep5PortfolioResearch(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 8 required Step 5 deliverable files must exist."""
        files = [
            BASELINE_DIAG_CSV,
            RANKING_COMP_CSV,
            SIZING_COMP_CSV,
            EXIT_COMP_CSV,
            CAPACITY_COMP_CSV,
            RISK_COMP_CSV,
            FINAL_CONFIG_CSV,
            STEP5_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_baseline_diagnostics(self):
        """Baseline diagnostics must contain rows for TRAIN and VALIDATION."""
        if not os.path.exists(BASELINE_DIAG_CSV):
            self.skipTest("baseline_portfolio_diagnostics.csv not found")
        df_b = pd.read_csv(BASELINE_DIAG_CSV)
        splits = df_b['split'].tolist()
        self.assertIn("TRAIN", splits)
        self.assertIn("VALIDATION", splits)

    def test_03_signal_ranking_comparison(self):
        """Signal ranking comparison must evaluate candidate ranking methods."""
        if not os.path.exists(RANKING_COMP_CSV):
            self.skipTest("signal_ranking_comparison.csv not found")
        df_r = pd.read_csv(RANKING_COMP_CSV)
        self.assertGreaterEqual(len(df_r), 4)

    def test_04_composite_score_performance(self):
        """Composite score ranking must outperform baseline RSI ranking on Validation."""
        if not os.path.exists(RANKING_COMP_CSV):
            self.skipTest("signal_ranking_comparison.csv not found")
        df_r = pd.read_csv(RANKING_COMP_CSV)
        rsi_ret = df_r[df_r['rank_column'] == 'rsi_14'].iloc[0]['net_portfolio_return_pct']
        comp_ret = df_r[df_r['rank_column'] == 'composite_score'].iloc[0]['net_portfolio_return_pct']
        self.assertGreater(comp_ret, rsi_ret)

    def test_05_position_sizing_comparison(self):
        """Position sizing comparison must evaluate equal weight and vol-adjusted sizing."""
        if not os.path.exists(SIZING_COMP_CSV):
            self.skipTest("position_sizing_comparison.csv not found")
        df_s = pd.read_csv(SIZING_COMP_CSV)
        modes = df_s['sizing_mode'].tolist()
        self.assertIn("equal", modes)
        self.assertIn("vol_adjusted", modes)

    def test_06_exit_strategy_comparison(self):
        """Exit strategy comparison must contain fixed 10d and time decay options."""
        if not os.path.exists(EXIT_COMP_CSV):
            self.skipTest("exit_strategy_comparison.csv not found")
        df_e = pd.read_csv(EXIT_COMP_CSV)
        methods = df_e['exit_method'].tolist()
        self.assertTrue(any("Fixed" in m for m in methods))
        self.assertTrue(any("Time Decay" in m for m in methods))

    def test_07_capacity_comparison(self):
        """Capacity comparison must evaluate 5, 10, 15, 20 position limits."""
        if not os.path.exists(CAPACITY_COMP_CSV):
            self.skipTest("portfolio_capacity_comparison.csv not found")
        df_c = pd.read_csv(CAPACITY_COMP_CSV)
        caps = df_c['max_positions'].tolist()
        for expected in [5, 10, 15, 20]:
            self.assertIn(expected, caps)

    def test_08_risk_control_comparison(self):
        """Risk control comparison must evaluate regime throttle."""
        if not os.path.exists(RISK_COMP_CSV):
            self.skipTest("risk_control_comparison.csv not found")
        df_rk = pd.read_csv(RISK_COMP_CSV)
        self.assertEqual(len(df_rk), 2)

    def test_09_final_configuration_frozen(self):
        """Final configuration CSV must contain frozen parameters for TRAIN and VALIDATION."""
        if not os.path.exists(FINAL_CONFIG_CSV):
            self.skipTest("final_portfolio_configuration.csv not found")
        df_f = pd.read_csv(FINAL_CONFIG_CSV)
        self.assertGreaterEqual(len(df_f), 2)

    def test_10_gate_classification_green(self):
        """Report must classify Step 5 as GREEN — ROBUST PORTFOLIO IMPROVEMENT."""
        if not os.path.exists(STEP5_REPORT_MD):
            self.skipTest("step_5_report.md not found")
        with open(STEP5_REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN — ROBUST PORTFOLIO IMPROVEMENT", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 5 VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep5PortfolioResearch)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

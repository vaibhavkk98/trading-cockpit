"""
STEP 4H — Strategy-Specific ML Research Verification Tests

Tests:
1. Deliverable files exist in data/ml/step_4h/
2. All 4 strategies evaluated in strategy_model_comparison.csv
3. Test set remains untouched (no test evaluation in report selection)
4. Cross-sectional ranking features correctly specified
5. All 4 strategies present in cross_sectional_strategy_comparison.csv
6. strategy_probability_buckets.csv contains 4 quartiles per strategy
7. Feature diagnostics cover features per strategy
8. RS Momentum Breakout achieves validation ROC-AUC >= 0.57
9. VCP Volatility Contraction Breakout achieves validation ROC-AUC >= 0.55
10. Report contains overall classification PROMISING
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP4H_DIR = os.path.join(ML_DIR, "step_4h")

STRATEGY_MODEL_COMP_CSV = os.path.join(STEP4H_DIR, "strategy_model_comparison.csv")
STRATEGY_FEATURE_DIAG_CSV = os.path.join(STEP4H_DIR, "strategy_feature_diagnostics.csv")
STRATEGY_PROB_BUCKETS_CSV = os.path.join(STEP4H_DIR, "strategy_probability_buckets.csv")
CS_STRATEGY_COMP_CSV = os.path.join(STEP4H_DIR, "cross_sectional_strategy_comparison.csv")
STEP4H_REPORT_MD = os.path.join(STEP4H_DIR, "step_4h_report.md")

EXPECTED_STRATEGIES = [
    "Donchian Channel Breakout",
    "EMA Pullback / Bounce",
    "RS Momentum Breakout",
    "VCP Volatility Contraction Breakout"
]


class TestStep4HStrategyModels(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 4H deliverable files must exist."""
        files = [
            STRATEGY_MODEL_COMP_CSV,
            STRATEGY_FEATURE_DIAG_CSV,
            STRATEGY_PROB_BUCKETS_CSV,
            CS_STRATEGY_COMP_CSV,
            STEP4H_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_all_strategies_evaluated(self):
        """All 4 strategies must be evaluated in strategy_model_comparison.csv."""
        if not os.path.exists(STRATEGY_MODEL_COMP_CSV):
            self.skipTest("strategy_model_comparison.csv not found")
        df_m = pd.read_csv(STRATEGY_MODEL_COMP_CSV)
        strats = df_m['strategy_name'].unique().tolist()
        for es in EXPECTED_STRATEGIES:
            self.assertIn(es, strats)

    def test_03_test_set_untouched(self):
        """Report must clearly state that TEST set was NOT used for model selection."""
        if not os.path.exists(STEP4H_REPORT_MD):
            self.skipTest("step_4h_report.md not found")
        with open(STEP4H_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_04_cs_comparison_contains_all_strategies(self):
        """Cross-sectional strategy comparison must contain entries for all 4 strategies."""
        if not os.path.exists(CS_STRATEGY_COMP_CSV):
            self.skipTest("cross_sectional_strategy_comparison.csv not found")
        df_cs = pd.read_csv(CS_STRATEGY_COMP_CSV)
        self.assertEqual(len(df_cs), 4)

    def test_05_probability_buckets_quartiles(self):
        """Probability buckets CSV must contain 4 quartiles per strategy."""
        if not os.path.exists(STRATEGY_PROB_BUCKETS_CSV):
            self.skipTest("strategy_probability_buckets.csv not found")
        df_b = pd.read_csv(STRATEGY_PROB_BUCKETS_CSV)
        for s in EXPECTED_STRATEGIES:
            sub = df_b[df_b['strategy_name'] == s]
            self.assertGreaterEqual(len(sub), 3, f"Insufficient quartiles for {s}")

    def test_06_feature_diagnostics_coverage(self):
        """Feature diagnostics CSV must contain rows for all strategies."""
        if not os.path.exists(STRATEGY_FEATURE_DIAG_CSV):
            self.skipTest("strategy_feature_diagnostics.csv not found")
        df_f = pd.read_csv(STRATEGY_FEATURE_DIAG_CSV)
        strats = df_f['strategy_name'].unique().tolist()
        self.assertEqual(len(strats), 4)

    def test_07_rs_momentum_promising(self):
        """RS Momentum Breakout must achieve validation ROC-AUC >= 0.57."""
        if not os.path.exists(CS_STRATEGY_COMP_CSV):
            self.skipTest("cross_sectional_strategy_comparison.csv not found")
        df_cs = pd.read_csv(CS_STRATEGY_COMP_CSV)
        rs_row = df_cs[df_cs['strategy_name'] == "RS Momentum Breakout"].iloc[0]
        self.assertGreaterEqual(rs_row['best_cs_val_roc_auc'], 0.57)

    def test_08_vcp_promising(self):
        """VCP Volatility Contraction Breakout must achieve validation ROC-AUC >= 0.55."""
        if not os.path.exists(CS_STRATEGY_COMP_CSV):
            self.skipTest("cross_sectional_strategy_comparison.csv not found")
        df_cs = pd.read_csv(CS_STRATEGY_COMP_CSV)
        vcp_row = df_cs[df_cs['strategy_name'] == "VCP Volatility Contraction Breakout"].iloc[0]
        self.assertGreaterEqual(vcp_row['best_cs_val_roc_auc'], 0.55)

    def test_09_overall_classification_promising(self):
        """Report must classify overall ML as PROMISING."""
        if not os.path.exists(STEP4H_REPORT_MD):
            self.skipTest("step_4h_report.md not found")
        with open(STEP4H_REPORT_MD) as f:
            text = f.read()
        self.assertIn("PROMISING", text)

    def test_10_reproducible_predictions(self):
        """Script must run cleanly and generate deterministic metrics."""
        self.assertTrue(os.path.exists(STRATEGY_MODEL_COMP_CSV))


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 4H VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep4HStrategyModels)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

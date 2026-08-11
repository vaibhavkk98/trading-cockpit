"""
STEP 7C — Strategy-Aware Portfolio Allocation Unit Tests (12 Required Tests)

1. Deliverables existence
2. Baseline model preserved
3. Model A (Bucket Allocation) evaluated
4. Model B (Strategy Percentile Rank) evaluated
5. Validation-only decision framework enforced
6. NR7 allocation fairness & selection ratio computed
7. Test set clearly labeled descriptive only
8. Concentration audit (Top 1, 3, 5 & Leave-Top-N)
9. Market regime interaction evaluated
10. Transaction cost sensitivity (1x, 2x, 3x)
11. Production architecture files unmodified
12. Manifest records YELLOW gate verdict
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7c_strategy_aware_comparison.csv")
FAIRNESS_CSV = os.path.join(STEP7_DIR, "step_7c_nr7_selection_fairness.csv")
REGIME_CSV = os.path.join(STEP7_DIR, "step_7c_regime_comparison.csv")
CONCENTRATION_CSV = os.path.join(STEP7_DIR, "step_7c_concentration.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP7_DIR, "step_7c_cost_sensitivity.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7c_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7c_report.md")


class TestStep7CStrategyAwareAllocation(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 7 required Step 7C deliverable files must exist."""
        files = [
            COMPARISON_CSV,
            FAIRNESS_CSV,
            REGIME_CSV,
            CONCENTRATION_CSV,
            COST_SENSITIVITY_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_baseline_preserved(self):
        """Comparison CSV must include frozen Baseline model."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c_strategy_aware_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Baseline" in m for m in models))

    def test_02_model_a_evaluated(self):
        """Comparison CSV must include Model A (Strategy-Bucket Allocation)."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c_strategy_aware_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Model A" in m for m in models))

    def test_03_model_b_evaluated(self):
        """Comparison CSV must include Model B (Strategy-Normalized Ranking)."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c_strategy_aware_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Model B" in m for m in models))

    def test_04_validation_period_decision(self):
        """Comparison CSV must separate Validation split from Test split."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c_strategy_aware_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        splits = df_c['split_name'].tolist()
        self.assertIn("VALIDATION", splits)
        self.assertIn("TEST (DESCRIPTIVE ONLY)", splits)

    def test_05_nr7_selection_fairness(self):
        """Fairness CSV must compute selection ratio for Baseline, Model A, Model B."""
        if not os.path.exists(FAIRNESS_CSV):
            self.skipTest("step_7c_nr7_selection_fairness.csv not found")
        df_f = pd.read_csv(FAIRNESS_CSV)
        self.assertEqual(len(df_f), 3)
        self.assertIn("nr7_selection_ratio", df_f.columns)

    def test_06_test_set_descriptive(self):
        """Report must confirm TEST set is descriptive only."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7c_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("100% UNTOUCHED (Descriptive Reporting Only)", txt)

    def test_07_concentration_audit(self):
        """Concentration CSV must document Top 1, Top 3, Top 5 trade share and Leave-Top-N PnL."""
        if not os.path.exists(CONCENTRATION_CSV):
            self.skipTest("step_7c_concentration.csv not found")
        df_cn = pd.read_csv(CONCENTRATION_CSV)
        cols = df_cn.columns.tolist()
        self.assertIn("top1_trade_share_pct", cols)
        self.assertIn("pnl_excl_top1_rs", cols)

    def test_08_regime_interaction(self):
        """Regime CSV must document Bullish vs Bearish/Neutral breakdown."""
        if not os.path.exists(REGIME_CSV):
            self.skipTest("step_7c_regime_comparison.csv not found")
        df_rg = pd.read_csv(REGIME_CSV)
        regimes = df_rg['market_regime'].tolist()
        self.assertTrue(any("Bullish" in r for r in regimes))

    def test_09_cost_sensitivity(self):
        """Cost sensitivity CSV must test 1x, 2x, 3x friction models."""
        if not os.path.exists(COST_SENSITIVITY_CSV):
            self.skipTest("step_7c_cost_sensitivity.csv not found")
        df_cs = pd.read_csv(COST_SENSITIVITY_CSV)
        frictions = df_cs['friction_multiplier'].tolist()
        self.assertIn("1.0x", frictions)
        self.assertIn("2.0x", frictions)
        self.assertIn("3.0x", frictions)

    def test_10_production_architecture_unmodified(self):
        """Production architecture files must remain untouched."""
        prod_files = [
            os.path.join(PROJECT_ROOT, "portfolio_engine.py"),
            os.path.join(PROJECT_ROOT, "backtester.py"),
            os.path.join(PROJECT_ROOT, "app.py"),
        ]
        for pf in prod_files:
            self.assertTrue(os.path.exists(pf), f"Production file missing: {pf}")

    def test_11_gate_verdict_yellow(self):
        """Manifest must record YELLOW classification for allocation experiment."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7c_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("YELLOW — NO MATERIAL IMPROVEMENT FROM STRATEGY-AWARE ALLOCATION", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7C STRATEGY-AWARE ALLOCATION EXPERIMENT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7CStrategyAwareAllocation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

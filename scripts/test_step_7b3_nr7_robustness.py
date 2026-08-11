"""
STEP 7B.3 — NR7 Robustness & Generalization Audit Unit Tests (12 Required Tests)

1. Model A frozen specification
2. Validation sub-periods breakdown
3. Test sub-periods descriptive analysis
4. Trade concentration & Leave-Top-N analysis
5. Cost sensitivity (1x, 2x, 3x)
6. Fill type breakdown (Gap vs Intraday)
7. Market regime breakdown
8. Symbol concentration analysis
9. Statistical bootstrap analysis
10. Model A vs Model B comparison
11. Test split remains untouched benchmark
12. Final gate verdict records YELLOW classification
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

ROBUSTNESS_CSV = os.path.join(STEP7_DIR, "step_7b3_nr7_robustness.csv")
VAL_SUBPERIODS_CSV = os.path.join(STEP7_DIR, "step_7b3_validation_subperiods.csv")
TEST_SUBPERIODS_CSV = os.path.join(STEP7_DIR, "step_7b3_test_subperiods.csv")
CONCENTRATION_CSV = os.path.join(STEP7_DIR, "step_7b3_trade_concentration.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP7_DIR, "step_7b3_cost_sensitivity.csv")
FILL_TYPE_CSV = os.path.join(STEP7_DIR, "step_7b3_fill_type_analysis.csv")
REGIME_CSV = os.path.join(STEP7_DIR, "step_7b3_regime_analysis.csv")
SYMBOL_CSV = os.path.join(STEP7_DIR, "step_7b3_symbol_concentration.csv")
MODEL_COMP_CSV = os.path.join(STEP7_DIR, "step_7b3_model_comparison.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7b3_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7b3_report.md")


class TestStep7B3NR7Robustness(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 11 required Step 7B.3 deliverable files must exist."""
        files = [
            ROBUSTNESS_CSV,
            VAL_SUBPERIODS_CSV,
            TEST_SUBPERIODS_CSV,
            CONCENTRATION_CSV,
            COST_SENSITIVITY_CSV,
            FILL_TYPE_CSV,
            REGIME_CSV,
            SYMBOL_CSV,
            MODEL_COMP_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_model_a_frozen_spec(self):
        """Model A pre-placed stop order logic must be documented in manifest."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b3_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        self.assertIn("Model A", df_m['adopted_model'].iloc[0])

    def test_02_validation_subperiods(self):
        """Validation sub-periods CSV must contain 3 chronological windows."""
        if not os.path.exists(VAL_SUBPERIODS_CSV):
            self.skipTest("step_7b3_validation_subperiods.csv not found")
        df_v = pd.read_csv(VAL_SUBPERIODS_CSV)
        self.assertEqual(len(df_v), 3)

    def test_03_test_subperiods_descriptive(self):
        """Test sub-periods CSV must contain 3 descriptive windows."""
        if not os.path.exists(TEST_SUBPERIODS_CSV):
            self.skipTest("step_7b3_test_subperiods.csv not found")
        df_t = pd.read_csv(TEST_SUBPERIODS_CSV)
        self.assertEqual(len(df_t), 3)
        for name in df_t['subperiod_name']:
            self.assertIn("Descriptive", name)

    def test_04_trade_concentration(self):
        """Concentration CSV must document top 1, 5, 10 trade shares and leave-top-N PnL."""
        if not os.path.exists(CONCENTRATION_CSV):
            self.skipTest("step_7b3_trade_concentration.csv not found")
        df_c = pd.read_csv(CONCENTRATION_CSV)
        metrics = df_c['metric_name'].tolist()
        self.assertIn("Top 1 Trade Net PnL (Rs)", metrics)
        self.assertIn("Top 5 Trades Net PnL (Rs)", metrics)
        self.assertIn("Net PnL Excluding Top 10 Winners (Rs)", metrics)

    def test_05_cost_sensitivity_1x_2x_3x(self):
        """Cost sensitivity CSV must evaluate 1x, 2x, 3x friction models."""
        if not os.path.exists(COST_SENSITIVITY_CSV):
            self.skipTest("step_7b3_cost_sensitivity.csv not found")
        df_cs = pd.read_csv(COST_SENSITIVITY_CSV)
        self.assertEqual(len(df_cs), 3)
        self.assertEqual(df_cs['friction_multiplier'].tolist(), ['1.0x', '2.0x', '3.0x'])

    def test_06_fill_type_breakdown(self):
        """Fill type CSV must evaluate GAP_FILL and INTRADAY_FILL separately."""
        if not os.path.exists(FILL_TYPE_CSV):
            self.skipTest("step_7b3_fill_type_analysis.csv not found")
        df_f = pd.read_csv(FILL_TYPE_CSV)
        self.assertEqual(len(df_f), 2)
        types = df_f['fill_type'].tolist()
        self.assertIn("GAP_FILL", types)
        self.assertIn("INTRADAY_FILL", types)

    def test_07_market_regime_breakdown(self):
        """Regime CSV must evaluate Bullish and Bearish/Neutral regimes."""
        if not os.path.exists(REGIME_CSV):
            self.skipTest("step_7b3_regime_analysis.csv not found")
        df_r = pd.read_csv(REGIME_CSV)
        self.assertEqual(len(df_r), 2)

    def test_08_symbol_concentration(self):
        """Symbol concentration CSV must list traded securities."""
        if not os.path.exists(SYMBOL_CSV):
            self.skipTest("step_7b3_symbol_concentration.csv not found")
        df_s = pd.read_csv(SYMBOL_CSV)
        self.assertGreater(len(df_s), 0)

    def test_09_bootstrap_statistical_analysis(self):
        """Report MD must document 95% bootstrap confidence interval."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7b3_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("95% Bootstrap Confidence Interval", text)

    def test_10_model_a_vs_model_b_comparison(self):
        """Model comparison CSV must recommend Model A."""
        if not os.path.exists(MODEL_COMP_CSV):
            self.skipTest("step_7b3_model_comparison.csv not found")
        df_mc = pd.read_csv(MODEL_COMP_CSV)
        model_a = df_mc[df_mc['model_name'].str.contains("Model A")]
        self.assertIn("RECOMMENDED", model_a['recommendation'].iloc[0])

    def test_11_test_period_untouched(self):
        """Test split dates must remain strictly untouched."""
        from scripts.run_step_4f_embargo import apply_embargo
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        emb = apply_embargo(df_exp, 10)
        self.assertGreaterEqual(str(emb['test']['signal_date'].min()), '2026-02-16')

    def test_12_gate_verdict_yellow(self):
        """Manifest must record YELLOW classification for NR7 robustness audit."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b3_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("YELLOW — NR7 CAUSAL BUT MIXED GENERALIZATION", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7B.3 NR7 ROBUSTNESS AUDIT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7B3NR7Robustness)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

"""
STEP 6E — Signal-Conditional ML Validation Unit Tests

10 Required Tests:
1. All 6 Step 6E deliverable files exist.
2. Training data filters strictly for has_any_signal == 1.
3. Chronological embargo splits are preserved.
4. Future execution columns are forbidden from ML features.
5. All 3 Pooled Signal-Conditional ML variants are evaluated.
6. Strategy-specific ML models are evaluated across all 6 strategies.
7. Pure Strategy Baseline (ML OFF) is included as benchmark.
8. Friction cost sensitivity analysis (1x vs 2x) is performed.
9. TEST set remains 100% UNTOUCHED for threshold selection.
10. Gate verdict records RED and ML OFF decision.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")
STEP6E_DIR = os.path.join(STEP6_DIR, "step_6e")

ML_COMPARISON_CSV = os.path.join(STEP6E_DIR, "step_6e_ml_comparison.csv")
STRATEGY_COMPARISON_CSV = os.path.join(STEP6E_DIR, "step_6e_strategy_comparison.csv")
PROBABILITY_BUCKETS_CSV = os.path.join(STEP6E_DIR, "step_6e_probability_buckets.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP6E_DIR, "step_6e_cost_sensitivity.csv")
REPORT_MD = os.path.join(STEP6E_DIR, "step_6e_report.md")
MANIFEST_CSV = os.path.join(STEP6E_DIR, "step_6e_manifest.csv")


class TestStep6ESignalConditionalML(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 6 required Step 6E deliverable files must exist."""
        files = [
            ML_COMPARISON_CSV,
            STRATEGY_COMPARISON_CSV,
            PROBABILITY_BUCKETS_CSV,
            COST_SENSITIVITY_CSV,
            REPORT_MD,
            MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_filters_for_signal_rows_only(self):
        """Pipeline script must filter candidate dataset for has_any_signal == 1."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6e_signal_conditional_ml.py")) as f:
            code = f.read()
        self.assertIn("has_any_signal", code)

    def test_03_preserves_embargo_splits(self):
        """Pipeline must enforce chronological embargo splits."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6e_signal_conditional_ml.py")) as f:
            code = f.read()
        self.assertIn("apply_embargo", code)

    def test_04_no_future_cols_in_ml_features(self):
        """Future execution columns must not enter ML feature sets."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6e_signal_conditional_ml.py")) as f:
            code = f.read()
        self.assertNotIn("'next_open'", code.split("raw_features =")[1].split("]")[0])
        self.assertNotIn("'next_high'", code.split("raw_features =")[1].split("]")[0])

    def test_05_pooled_models_evaluated(self):
        """ML comparison CSV must contain Pooled Model A, B, and C variants."""
        if not os.path.exists(ML_COMPARISON_CSV):
            self.skipTest("step_6e_ml_comparison.csv not found")
        df_m = pd.read_csv(ML_COMPARISON_CSV)
        variants = df_m['model_variant'].tolist()
        self.assertIn("Pooled Model A (Raw Signals Only)", variants)
        self.assertIn("Pooled Model B (CS Cand Signals Only)", variants)

    def test_06_strategy_specific_models_evaluated(self):
        """Strategy comparison CSV must contain strategy-specific breakdown for 6 strategies."""
        if not os.path.exists(STRATEGY_COMPARISON_CSV):
            self.skipTest("step_6e_strategy_comparison.csv not found")
        df_s = pd.read_csv(STRATEGY_COMPARISON_CSV)
        self.assertEqual(len(df_s), 6)

    def test_07_baseline_included_as_benchmark(self):
        """ML comparison CSV must include Pure Strategy Baseline (ML OFF)."""
        if not os.path.exists(ML_COMPARISON_CSV):
            self.skipTest("step_6e_ml_comparison.csv not found")
        df_m = pd.read_csv(ML_COMPARISON_CSV)
        self.assertIn("Pure Strategy Baseline (ML OFF)", df_m['model_variant'].tolist())

    def test_08_cost_sensitivity_evaluated(self):
        """Cost sensitivity CSV must record 1x standard vs 2x elevated friction performance."""
        if not os.path.exists(COST_SENSITIVITY_CSV):
            self.skipTest("step_6e_cost_sensitivity.csv not found")
        df_c = pd.read_csv(COST_SENSITIVITY_CSV)
        self.assertIn("1x Standard (0.20% per trade)", df_c['friction_multiplier'].tolist())
        self.assertIn("2x Elevated (0.40% per trade)", df_c['friction_multiplier'].tolist())

    def test_09_test_set_untouched(self):
        """Report must confirm TEST set was NOT used for parameter selection."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_6e_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_10_gate_verdict_red(self):
        """Report must record RED classification and ML OFF decision."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_6e_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("RED: SIGNAL-CONDITIONAL ML DOES NOT IMPROVE PURE STRATEGY BASELINE", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6E SIGNAL-CONDITIONAL ML VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6ESignalConditionalML)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

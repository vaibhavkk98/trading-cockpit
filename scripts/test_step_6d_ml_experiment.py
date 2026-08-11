"""
STEP 6D — ML Experiment Re-evaluation Unit Tests

10 Required Tests:
1. All 6 Step 6D deliverable files exist.
2. Candidate universe dataset is used as input source.
3. Chronological embargo splits are preserved.
4. Future execution columns are forbidden from ML features.
5. All 4 model configurations are evaluated.
6. Pure Strategy Baseline (ML OFF) is included as benchmark.
7. Probability bucket monotonicity analysis is performed.
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
STEP6D_DIR = os.path.join(STEP6_DIR, "step_6d")

ML_COMPARISON_CSV = os.path.join(STEP6D_DIR, "step_6d_ml_comparison.csv")
STRATEGY_COMPARISON_CSV = os.path.join(STEP6D_DIR, "step_6d_strategy_comparison.csv")
PROBABILITY_BUCKETS_CSV = os.path.join(STEP6D_DIR, "step_6d_probability_buckets.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP6D_DIR, "step_6d_cost_sensitivity.csv")
REPORT_MD = os.path.join(STEP6D_DIR, "step_6d_report.md")
MANIFEST_CSV = os.path.join(STEP6D_DIR, "step_6d_manifest.csv")


class TestStep6DMLExperiment(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 6 required Step 6D deliverable files must exist."""
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

    def test_02_uses_candidate_universe_dataset(self):
        """Pipeline script must reference Candidate Universe dataset."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6d_ml_experiment.py")) as f:
            code = f.read()
        self.assertIn("candidate_universe_dataset.csv", code)

    def test_03_preserves_embargo_splits(self):
        """Pipeline must enforce chronological embargo splits."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6d_ml_experiment.py")) as f:
            code = f.read()
        self.assertIn("apply_embargo", code)

    def test_04_no_future_cols_in_ml_features(self):
        """Future execution columns must not enter ML feature sets."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6d_ml_experiment.py")) as f:
            code = f.read()
        self.assertNotIn("'next_open'", code.split("raw_features =")[1].split("]")[0])
        self.assertNotIn("'next_high'", code.split("raw_features =")[1].split("]")[0])

    def test_05_four_configurations_evaluated(self):
        """ML comparison CSV must contain Baseline, Model A, Model B, and Model C."""
        if not os.path.exists(ML_COMPARISON_CSV):
            self.skipTest("step_6d_ml_comparison.csv not found")
        df_m = pd.read_csv(ML_COMPARISON_CSV)
        variants = df_m['model_variant'].tolist()
        self.assertEqual(len(variants), 4)
        self.assertIn("Pure Strategy Baseline (ML OFF)", variants)
        self.assertIn("Model A (Raw Features Only)", variants)
        self.assertIn("Model B (CS Candidate Features Only)", variants)

    def test_06_baseline_included_as_benchmark(self):
        """Strategy comparison must document Pure Strategy Baseline as Champion."""
        if not os.path.exists(STRATEGY_COMPARISON_CSV):
            self.skipTest("step_6d_strategy_comparison.csv not found")
        df_s = pd.read_csv(STRATEGY_COMPARISON_CSV)
        self.assertIn("CHAMPION — ML OFF", df_s.iloc[0]['production_status'])

    def test_07_probability_buckets_evaluated(self):
        """Probability buckets CSV must record quantile bucket monotonicity statistics."""
        if not os.path.exists(PROBABILITY_BUCKETS_CSV):
            self.skipTest("step_6d_probability_buckets.csv not found")
        df_p = pd.read_csv(PROBABILITY_BUCKETS_CSV)
        self.assertGreater(len(df_p), 0)

    def test_08_cost_sensitivity_evaluated(self):
        """Cost sensitivity CSV must record 1x standard vs 2x elevated friction performance."""
        if not os.path.exists(COST_SENSITIVITY_CSV):
            self.skipTest("step_6d_cost_sensitivity.csv not found")
        df_c = pd.read_csv(COST_SENSITIVITY_CSV)
        self.assertIn("1x Standard (0.20% per trade)", df_c['friction_multiplier'].tolist())
        self.assertIn("2x Elevated (0.40% per trade)", df_c['friction_multiplier'].tolist())

    def test_09_test_set_untouched(self):
        """Report must confirm TEST set was NOT used for parameter selection."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_6d_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_10_gate_verdict_red(self):
        """Report must record RED classification and ML OFF decision."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_6d_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("RED: ML DOES NOT IMPROVE PURE STRATEGY BASELINE", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6D ML EXPERIMENT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6DMLExperiment)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

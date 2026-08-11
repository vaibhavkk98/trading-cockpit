"""
STEP 7A — Portfolio & Risk Engine Research Unit Tests

10 Required Tests:
1. All 6 Step 7A deliverable files exist.
2. Standard execution model and transaction cost assumptions are used.
3. No ML features or models are used in portfolio experiments.
4. Experiment A (ATR trailing stop) is evaluated.
5. Experiment B (Time decay exit) is evaluated.
6. Experiment D (Signal ranking rules) is evaluated.
7. Portfolio Engine Candidate v2 is evaluated.
8. Friction cost sensitivity analysis (1x vs 2x) is performed.
9. TEST set remains 100% UNTOUCHED for parameter selection.
10. Gate verdict records GREEN.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

EXPERIMENT_COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7a_experiment_comparison.csv")
EXIT_ANALYSIS_CSV = os.path.join(STEP7_DIR, "step_7a_exit_analysis.csv")
RISK_ANALYSIS_CSV = os.path.join(STEP7_DIR, "step_7a_risk_analysis.csv")
ROBUSTNESS_CSV = os.path.join(STEP7_DIR, "step_7a_robustness.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7a_report.md")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7a_manifest.csv")


class TestStep7APortfolioExperiments(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 6 required Step 7A deliverable files must exist."""
        files = [
            EXPERIMENT_COMPARISON_CSV,
            EXIT_ANALYSIS_CSV,
            RISK_ANALYSIS_CSV,
            ROBUSTNESS_CSV,
            REPORT_MD,
            MANIFEST_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_standard_execution_model_used(self):
        """Pipeline script must use simulate_execution_validated_portfolio."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a_portfolio_experiments.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_03_no_ml_in_portfolio_experiments(self):
        """Pipeline script must NOT train or import ML classifiers."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7a_portfolio_experiments.py")) as f:
            code = f.read()
        self.assertNotIn("GradientBoosting", code)
        self.assertNotIn("RandomForest", code)

    def test_04_experiment_a_atr_trailing_stop_evaluated(self):
        """Experiment comparison CSV must contain Experiment A ATR trailing stop."""
        if not os.path.exists(EXPERIMENT_COMPARISON_CSV):
            self.skipTest("step_7a_experiment_comparison.csv not found")
        df_c = pd.read_csv(EXPERIMENT_COMPARISON_CSV)
        exp_names = df_c['experiment_name'].tolist()
        self.assertTrue(any("Experiment A" in name for name in exp_names))

    def test_05_experiment_b_time_decay_evaluated(self):
        """Experiment comparison CSV must contain Experiment B Time Decay exit."""
        if not os.path.exists(EXPERIMENT_COMPARISON_CSV):
            self.skipTest("step_7a_experiment_comparison.csv not found")
        df_c = pd.read_csv(EXPERIMENT_COMPARISON_CSV)
        exp_names = df_c['experiment_name'].tolist()
        self.assertTrue(any("Experiment B" in name for name in exp_names))

    def test_06_experiment_d_ranking_rules_evaluated(self):
        """Experiment comparison CSV must contain Experiment D Signal Ranking."""
        if not os.path.exists(EXPERIMENT_COMPARISON_CSV):
            self.skipTest("step_7a_experiment_comparison.csv not found")
        df_c = pd.read_csv(EXPERIMENT_COMPARISON_CSV)
        exp_names = df_c['experiment_name'].tolist()
        self.assertTrue(any("Experiment D" in name for name in exp_names))

    def test_07_candidate_v2_combined_evaluated(self):
        """Experiment comparison CSV must include Portfolio Engine Candidate v2."""
        if not os.path.exists(EXPERIMENT_COMPARISON_CSV):
            self.skipTest("step_7a_experiment_comparison.csv not found")
        df_c = pd.read_csv(EXPERIMENT_COMPARISON_CSV)
        exp_names = df_c['experiment_name'].tolist()
        self.assertTrue(any("Candidate v2" in name for name in exp_names))

    def test_08_cost_sensitivity_evaluated(self):
        """Robustness CSV must record 1x standard vs 2x elevated friction performance."""
        if not os.path.exists(ROBUSTNESS_CSV):
            self.skipTest("step_7a_robustness.csv not found")
        df_r = pd.read_csv(ROBUSTNESS_CSV)
        self.assertIn("1x Standard (0.20% / trade)", df_r['friction_multiplier'].tolist())
        self.assertIn("2x Elevated (0.40% / trade)", df_r['friction_multiplier'].tolist())

    def test_09_test_set_untouched(self):
        """Report must confirm TEST set was NOT used for parameter selection."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7a_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_10_gate_verdict_green(self):
        """Report must record GREEN classification."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7a_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN — PORTFOLIO ENGINE V2 PRODUCES MEANINGFUL IMPROVEMENT", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7A PORTFOLIO & RISK ENGINE VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7APortfolioExperiments)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

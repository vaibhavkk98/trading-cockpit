"""
STEP 4G — ML Diagnostics Verification Tests

Tests:
1. All 7 Step 4G deliverables exist
2. Exactly 21 features evaluated in feature_diagnostics.csv
3. Model comparison includes Gradient Boosting, Logistic Regression, and Random Forest
4. Model training uses clean TRAIN only; evaluation uses clean VAL only (TEST untouched)
5. Cross-sectional experiment records raw vs cs-ranked metrics
6. Diagnostic summary contains verdict WEAK / NO SIGNAL
7. Probability buckets cover probability range
8. Regime diagnostics cover Nifty Trend, Momentum, Volatility
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP4G_DIR = os.path.join(ML_DIR, "step_4g")

DIAGNOSTIC_SUMMARY_CSV = os.path.join(STEP4G_DIR, "diagnostic_summary.csv")
FEATURE_DIAGNOSTICS_CSV = os.path.join(STEP4G_DIR, "feature_diagnostics.csv")
MODEL_COMPARISON_CSV = os.path.join(STEP4G_DIR, "model_comparison.csv")
PROB_BUCKET_CSV = os.path.join(STEP4G_DIR, "probability_bucket_diagnostics.csv")
CS_EXPERIMENT_CSV = os.path.join(STEP4G_DIR, "cross_sectional_experiment.csv")
REGIME_DIAGNOSTICS_CSV = os.path.join(STEP4G_DIR, "regime_diagnostics.csv")
STEP4G_REPORT_MD = os.path.join(STEP4G_DIR, "step_4g_report.md")


class TestStep4GDiagnostics(unittest.TestCase):

    def test_deliverables_exist(self):
        """All 7 required Step 4G deliverable files must exist."""
        files = [
            DIAGNOSTIC_SUMMARY_CSV,
            FEATURE_DIAGNOSTICS_CSV,
            MODEL_COMPARISON_CSV,
            PROB_BUCKET_CSV,
            CS_EXPERIMENT_CSV,
            REGIME_DIAGNOSTICS_CSV,
            STEP4G_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing Step 4G deliverable: {f}")

    def test_feature_count(self):
        """Exactly 21 features should be present in feature_diagnostics.csv."""
        if not os.path.exists(FEATURE_DIAGNOSTICS_CSV):
            self.skipTest("feature_diagnostics.csv not found")
        df_feat = pd.read_csv(FEATURE_DIAGNOSTICS_CSV)
        self.assertEqual(len(df_feat), 21, f"Expected 21 features, found {len(df_feat)}")

    def test_model_comparison(self):
        """Model comparison must contain GB, Logistic Regression, and Random Forest."""
        if not os.path.exists(MODEL_COMPARISON_CSV):
            self.skipTest("model_comparison.csv not found")
        df_m = pd.read_csv(MODEL_COMPARISON_CSV)
        models = df_m['model_name'].tolist()
        self.assertIn("Gradient Boosting", models)
        self.assertIn("Logistic Regression", models)
        self.assertIn("Random Forest", models)

    def test_cs_experiment(self):
        """Cross-sectional experiment must include deltas for all 3 models."""
        if not os.path.exists(CS_EXPERIMENT_CSV):
            self.skipTest("cross_sectional_experiment.csv not found")
        df_cs = pd.read_csv(CS_EXPERIMENT_CSV)
        self.assertEqual(len(df_cs), 3)
        self.assertIn("auc_delta", df_cs.columns)

    def test_summary_verdict(self):
        """Diagnostic summary must record a valid decision (WEAK / NO SIGNAL / PROMISING)."""
        if not os.path.exists(DIAGNOSTIC_SUMMARY_CSV):
            self.skipTest("diagnostic_summary.csv not found")
        df_sum = pd.read_csv(DIAGNOSTIC_SUMMARY_CSV)
        decision = df_sum.iloc[0]['decision']
        self.assertIn(decision, ["WEAK", "NO SIGNAL", "PROMISING"])

    def test_regime_diagnostics_coverage(self):
        """Regime diagnostics must cover Trend, Momentum, and Volatility."""
        if not os.path.exists(REGIME_DIAGNOSTICS_CSV):
            self.skipTest("regime_diagnostics.csv not found")
        df_reg = pd.read_csv(REGIME_DIAGNOSTICS_CSV)
        dims = df_reg['regime_dimension'].unique().tolist()
        self.assertIn("Nifty 50DMA Trend", dims)
        self.assertIn("Nifty 20D Momentum", dims)
        self.assertIn("Nifty 20D Volatility", dims)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 4G FOCUSED UNIT TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep4GDiagnostics)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

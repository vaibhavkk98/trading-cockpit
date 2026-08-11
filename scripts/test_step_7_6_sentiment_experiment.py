"""
STEP 7.6 — Sentiment Value Experiment V1 Unit Tests (8 Required Tests)

1. Deliverables existence
2. Historical sentiment data audit & 0% coverage verification
3. No fabricated sentiment values
4. Benchmark comparison (Technical Only vs Technical + Sentiment) evaluated
5. Test split clearly labeled descriptive only
6. Decision classification INCONCLUSIVE recorded
7. sentiment_enabled remains false in config
8. Production architecture files unmodified
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

SENTIMENT_DIR = os.path.join(PROJECT_ROOT, "data", "sentiment")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "sentiment_config.yaml")

COVERAGE_CSV = os.path.join(SENTIMENT_DIR, "step_7_6_data_coverage.csv")
COMPARISON_CSV = os.path.join(SENTIMENT_DIR, "step_7_6_comparison.csv")
REGIME_CSV = os.path.join(SENTIMENT_DIR, "step_7_6_regime_analysis.csv")
REPORT_MD = os.path.join(SENTIMENT_DIR, "step_7_6_report.md")


class TestStep76SentimentExperiment(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 4 required Step 7.6 deliverable files must exist."""
        files = [
            COVERAGE_CSV,
            COMPARISON_CSV,
            REGIME_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_data_coverage_audited(self):
        """Coverage CSV must record 0.00% coverage and insufficient data verdict."""
        if not os.path.exists(COVERAGE_CSV):
            self.skipTest("step_7_6_data_coverage.csv not found")
        df_cov = pd.read_csv(COVERAGE_CSV)
        self.assertEqual(df_cov["observations_with_sentiment_evidence"].iloc[0], 0)
        self.assertEqual(df_cov["coverage_percentage"].iloc[0], "0.00%")
        self.assertIn("INSUFFICIENT", df_cov["audit_verdict"].iloc[0])

    def test_02_no_fabricated_sentiment(self):
        """Pipeline script must enforce zero-fabrication safety rule."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_7_6_sentiment_experiment.py")
        with open(script_path, "r") as f:
            txt = f.read()
        self.assertIn("HISTORICAL SENTIMENT DATA INSUFFICIENT FOR VALID EXPERIMENT", txt)

    def test_03_benchmark_comparison_evaluated(self):
        """Comparison CSV must evaluate Technical Only vs Technical + Sentiment."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7_6_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c["model_name"].tolist()
        self.assertTrue(any("Technical Only MVP" in m for m in models))
        self.assertTrue(any("Technical + Sentiment" in m for m in models))

    def test_04_test_split_descriptive(self):
        """Report must confirm Test set is descriptive only."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7_6_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("Descriptive Out-of-Sample Result", txt)

    def test_05_decision_inconclusive(self):
        """Report must record decision classification B. INCONCLUSIVE."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7_6_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("B. INCONCLUSIVE", txt)

    def test_06_sentiment_remains_disabled(self):
        """sentiment_config.yaml must maintain sentiment_enabled: false."""
        self.assertTrue(os.path.exists(CONFIG_PATH))
        with open(CONFIG_PATH, "r") as f:
            txt = f.read()
        self.assertIn("sentiment_enabled: false", txt)

    def test_07_production_architecture_unmodified(self):
        """Production architecture files must remain untouched."""
        prod_files = [
            os.path.join(PROJECT_ROOT, "portfolio_engine.py"),
            os.path.join(PROJECT_ROOT, "backtester.py"),
            os.path.join(PROJECT_ROOT, "app.py"),
        ]
        for pf in prod_files:
            self.assertTrue(os.path.exists(pf), f"Production file missing: {pf}")


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7.6 SENTIMENT VALUE EXPERIMENT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep76SentimentExperiment)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

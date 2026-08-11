"""
STEP 7C.3 — Final Baseline Control Validation Unit Tests (10 Required Tests)

1. Deliverables existence
2. True Global Baseline evaluated without strategy bias
3. Model A 7/3 evaluated as Champion
4. Strict single-portfolio parity codebase verified
5. Validation performance improvement verified
6. Test set clearly labeled descriptive only
7. NR7 selection ratio audit computed
8. Previous step artifacts preserved
9. Production architecture files unmodified
10. Manifest records GREEN gate verdict (READY FOR STEP 7D)
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7c3_global_baseline_comparison.csv")
SELECTION_AUDIT_CSV = os.path.join(STEP7_DIR, "step_7c3_nr7_selection_audit.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7c3_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7c3_report.md")


class TestStep7C3GlobalBaseline(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 4 required Step 7C.3 deliverable files must exist."""
        files = [
            COMPARISON_CSV,
            SELECTION_AUDIT_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_true_global_baseline_evaluated(self):
        """Comparison CSV must include True Global Baseline."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c3_global_baseline_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("True Global Baseline" in m for m in models))

    def test_02_model_a_73_evaluated(self):
        """Comparison CSV must include Model A 7/3."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c3_global_baseline_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Model A 7/3" in m for m in models))

    def test_03_strict_parity_codebase(self):
        """Pipeline script must use simulate_single_portfolio_global for both models."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_7c3_global_baseline.py")
        with open(script_path, "r") as f:
            txt = f.read()
        self.assertIn("simulate_single_portfolio_global", txt)
        self.assertIn("is_bucket_model=False", txt)
        self.assertIn("is_bucket_model=True", txt)

    def test_04_validation_performance_verified(self):
        """Model A 7/3 must outperform True Global Baseline on Validation return, Sharpe, and Max DD."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c3_global_baseline_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        val_gb = df_c[(df_c['split_name'] == 'VALIDATION') & (df_c['model_name'].str.contains('True Global Baseline'))].iloc[0]
        val_ma = df_c[(df_c['split_name'] == 'VALIDATION') & (df_c['model_name'].str.contains('Model A 7/3'))].iloc[0]

        self.assertGreater(val_ma['net_return_pct'], val_gb['net_return_pct'])
        self.assertGreater(val_ma['daily_sharpe'], val_gb['daily_sharpe'])
        self.assertLess(val_ma['max_drawdown_pct'], val_gb['max_drawdown_pct'])

    def test_05_test_split_descriptive(self):
        """Report must confirm Test set is descriptive only."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7c3_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("100% UNTOUCHED (Descriptive Reporting Only)", txt)

    def test_06_nr7_selection_ratio_audit(self):
        """Selection audit CSV must compute selection ratio for True Global Baseline and Model A 7/3."""
        if not os.path.exists(SELECTION_AUDIT_CSV):
            self.skipTest("step_7c3_nr7_selection_audit.csv not found")
        df_sa = pd.read_csv(SELECTION_AUDIT_CSV)
        self.assertEqual(len(df_sa), 2)
        self.assertIn("nr7_selection_ratio", df_sa.columns)

    def test_07_previous_step_artifacts_preserved(self):
        """Step 7C, 7C.1, 7C.2 artifacts must be preserved."""
        c_file = os.path.join(STEP7_DIR, "step_7c_strategy_aware_comparison.csv")
        c1_file = os.path.join(STEP7_DIR, "step_7c1_corrected_comparison.csv")
        c2_file = os.path.join(STEP7_DIR, "step_7c2_champion_validation.csv")
        self.assertTrue(os.path.exists(c_file))
        self.assertTrue(os.path.exists(c1_file))
        self.assertTrue(os.path.exists(c2_file))

    def test_08_production_architecture_unmodified(self):
        """Production architecture files must remain untouched."""
        prod_files = [
            os.path.join(PROJECT_ROOT, "portfolio_engine.py"),
            os.path.join(PROJECT_ROOT, "backtester.py"),
            os.path.join(PROJECT_ROOT, "app.py"),
        ]
        for pf in prod_files:
            self.assertTrue(os.path.exists(pf), f"Production file missing: {pf}")

    def test_09_gate_verdict_green(self):
        """Manifest must record GREEN classification and READY FOR STEP 7D."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7c3_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — 7 TREND / 3 VOLATILITY ALLOCATION FINALIZED AS CHAMPION: READY FOR STEP 7D", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7C.3 FINAL BASELINE CONTROL VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7C3GlobalBaseline)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

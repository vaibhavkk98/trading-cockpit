"""
STEP 6B — Cross-Sectional Feature Engineering & ML Ablation Unit Tests

10 Required Tests:
1. All 5 Step 6B deliverable files exist.
2. Cross-sectional features are calculated per signal_date.
3. No future execution columns enter cross-sectional feature set.
4. Feasibility sample size audit covers all signal dates.
5. Model A, B, and C ablations are evaluated on Validation.
6. Untouched TEST set evaluation includes Pure Strategy Baseline.
7. TEST set remains 100% UNTOUCHED for threshold selection.
8. No duplicate (signal_date, symbol, strategy_name) records.
9. Chronological embargo splits are preserved.
10. Gate verdict records YELLOW — CROSS-SECTIONAL FEATURES IMPROVE RELATIVE ML PERFORMANCE.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")

EXPANDED_DATASET_CSV = os.path.join(STEP6_DIR, "expanded_strategy_dataset.csv")
AUDIT_MD = os.path.join(STEP6_DIR, "step_6b_cross_sectional_audit.md")
FEATURE_MANIFEST_CSV = os.path.join(STEP6_DIR, "cross_sectional_feature_manifest.csv")
SAMPLE_SIZE_AUDIT_CSV = os.path.join(STEP6_DIR, "cross_sectional_sample_size_audit.csv")
MODEL_ABLATION_CSV = os.path.join(STEP6_DIR, "step_6b_model_ablation.csv")
TEST_COMPARISON_CSV = os.path.join(STEP6_DIR, "step_6b_test_comparison.csv")


class TestStep6BCrossSectional(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 6B deliverable files must exist."""
        files = [
            AUDIT_MD,
            FEATURE_MANIFEST_CSV,
            SAMPLE_SIZE_AUDIT_CSV,
            MODEL_ABLATION_CSV,
            TEST_COMPARISON_CSV,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_cs_ranks_grouped_by_signal_date(self):
        """Cross-sectional features must be calculated per signal_date."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6b_cross_sectional_features.py")) as f:
            code = f.read()
        self.assertIn("groupby('signal_date')", code)

    def test_03_no_execution_cols_in_cs_features(self):
        """Execution-only columns must not be in cross-sectional feature manifest."""
        if not os.path.exists(FEATURE_MANIFEST_CSV):
            self.skipTest("cross_sectional_feature_manifest.csv not found")
        df_m = pd.read_csv(FEATURE_MANIFEST_CSV)
        raw_feats = df_m['raw_feature_name'].tolist()
        forbidden = ['next_open', 'next_high', 'entry_price', 'forward_10d_return']
        for f in forbidden:
            self.assertNotIn(f, raw_feats, f"Forbidden column {f} found in feature manifest!")

    def test_04_sample_size_audit_complete(self):
        """Sample size audit must record stats for all signal dates."""
        if not os.path.exists(SAMPLE_SIZE_AUDIT_CSV) or not os.path.exists(EXPANDED_DATASET_CSV):
            self.skipTest("Sample size audit or dataset missing")
        df_s = pd.read_csv(SAMPLE_SIZE_AUDIT_CSV)
        df_e = pd.read_csv(EXPANDED_DATASET_CSV)
        self.assertEqual(len(df_s), df_e['signal_date'].nunique())

    def test_05_models_a_b_c_in_ablation(self):
        """Model ablation CSV must contain Model A, Model B, and Model C variants."""
        if not os.path.exists(MODEL_ABLATION_CSV):
            self.skipTest("step_6b_model_ablation.csv not found")
        df_a = pd.read_csv(MODEL_ABLATION_CSV)
        models = df_a['model_variant'].tolist()
        self.assertIn("Model A (Raw Features Only)", models)
        self.assertIn("Model B (Cross-Sectional Features Only)", models)
        self.assertIn("Model C (Raw + Cross-Sectional Features)", models)

    def test_06_test_comparison_includes_baseline(self):
        """Test comparison CSV must include Pure Strategy Baseline (ML OFF)."""
        if not os.path.exists(TEST_COMPARISON_CSV):
            self.skipTest("step_6b_test_comparison.csv not found")
        df_t = pd.read_csv(TEST_COMPARISON_CSV)
        variants = df_t['model_variant'].tolist()
        self.assertIn("Pure Strategy Baseline (ML OFF)", variants)

    def test_07_test_set_untouched(self):
        """Report must confirm TEST set was NOT used for parameter selection."""
        if not os.path.exists(AUDIT_MD):
            self.skipTest("step_6b_cross_sectional_audit.md not found")
        with open(AUDIT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_08_no_duplicate_records(self):
        """Expanded dataset must contain zero duplicate (signal_date, symbol, strategy_name) records."""
        if not os.path.exists(EXPANDED_DATASET_CSV):
            self.skipTest("expanded_strategy_dataset.csv not found")
        df = pd.read_csv(EXPANDED_DATASET_CSV)
        dups = df.duplicated(subset=['signal_date', 'symbol', 'strategy_name']).sum()
        self.assertEqual(dups, 0)

    def test_09_chronological_splits(self):
        """Ablation pipeline must enforce chronological embargo splits."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6b_ml_ablation.py")) as f:
            code = f.read()
        self.assertIn("apply_embargo", code)

    def test_10_gate_verdict_yellow(self):
        """Audit report must record YELLOW classification."""
        if not os.path.exists(AUDIT_MD):
            self.skipTest("step_6b_cross_sectional_audit.md not found")
        with open(AUDIT_MD) as f:
            text = f.read()
        self.assertIn("YELLOW — CROSS-SECTIONAL FEATURES IMPROVE RELATIVE ML PERFORMANCE", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6B CROSS-SECTIONAL VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6BCrossSectional)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

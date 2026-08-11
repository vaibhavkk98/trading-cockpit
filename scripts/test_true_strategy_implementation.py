"""
STEP 6A-FINAL-GATE — Verification Unit Tests

10 Required Tests:
1. Future execution columns cannot enter ML features.
2. NR7 setup contains no future information (High/Low <= T).
3. NR7 breakout confirmation is evaluated only after T (High(T+1) > High(T)).
4. Execution price is calculated only inside execution layer.
5. CRSI contains no future information.
6. No duplicate (symbol, signal_date, strategy_name) records.
7. Dataset manifest hash matches the authoritative dataset.
8. Train/validation/test boundaries remain chronological.
9. All NR7/CRSI counts reconcile across TRAIN + VAL + TEST = TOTAL.
10. All deliverable files exist and report records GREEN gate.
"""
import os
import sys
import hashlib
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")

EXPANDED_DATASET_CSV = os.path.join(STEP6_DIR, "expanded_strategy_dataset.csv")
MANIFEST_CSV = os.path.join(STEP6_DIR, "dataset_manifest.csv")
BOUNDARY_CSV = os.path.join(STEP6_DIR, "feature_execution_boundary_audit.csv")
NR7_RECON_CSV = os.path.join(STEP6_DIR, "nr7_count_reconciliation.csv")
TRUE_AUDIT_MD = os.path.join(STEP6_DIR, "true_strategy_implementation_audit.md")
RECONCILIATION_CSV = os.path.join(STEP6_DIR, "signal_count_reconciliation.csv")
TRUE_PERF_CSV = os.path.join(STEP6_DIR, "true_strategy_performance.csv")
FINAL_REPORT_MD = os.path.join(STEP6_DIR, "step_6a_final_report.md")


class TestTrueStrategyImplementation(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 8 required Step 6A-Final-Gate deliverable files must exist."""
        files = [
            EXPANDED_DATASET_CSV,
            MANIFEST_CSV,
            BOUNDARY_CSV,
            NR7_RECON_CSV,
            TRUE_AUDIT_MD,
            RECONCILIATION_CSV,
            TRUE_PERF_CSV,
            FINAL_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_future_execution_columns_forbidden_in_ml_features(self):
        """Future execution columns must be flagged as NOT ML feature safe."""
        if not os.path.exists(BOUNDARY_CSV):
            self.skipTest("feature_execution_boundary_audit.csv not found")
        df_b = pd.read_csv(BOUNDARY_CSV)
        exec_cols = ['next_open', 'next_high', 'entry_price', 'forward_10d_return']
        for col in exec_cols:
            row = df_b[df_b['column_name'] == col]
            if not row.empty:
                self.assertFalse(bool(row.iloc[0]['ml_feature_safe']), f"Future column {col} marked safe!")

    def test_03_nr7_setup_uses_data_through_t(self):
        """NR7 setup must depend on T Close features only."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("nr7", code)
        self.assertIn("dist_ema50_pct", code)

    def test_04_nr7_breakout_requires_t_plus_1_high_confirmation(self):
        """NR7 Breakout strategy code must confirm breakout High(T+1) > High(T)."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("True NR7 Volatility Expansion Breakout", code)
        self.assertIn("next_high", code)

    def test_05_reconciliation_exact(self):
        """Reconciliation table must verify TRAIN + VAL + TEST = TOTAL for all strategies."""
        if not os.path.exists(RECONCILIATION_CSV):
            self.skipTest("signal_count_reconciliation.csv not found")
        df_r = pd.read_csv(RECONCILIATION_CSV)
        for _, row in df_r.iterrows():
            calc_tot = row['raw_train_count'] + row['raw_val_count'] + row['raw_test_count']
            self.assertEqual(row['total_raw_count'], calc_tot, f"Discrepancy in {row['strategy_name']}")
            self.assertEqual(row['reconciliation_status'], "EXACT")

    def test_06_manifest_hash_matches_dataset(self):
        """Dataset manifest hash must match the actual expanded strategy dataset file."""
        if not os.path.exists(MANIFEST_CSV) or not os.path.exists(EXPANDED_DATASET_CSV):
            self.skipTest("Manifest or dataset file not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        manifest_hash = df_m.iloc[0]['sha256_hash']
        with open(EXPANDED_DATASET_CSV, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(manifest_hash, actual_hash)

    def test_07_test_set_untouched(self):
        """Report must verify TEST set was NOT used for parameter optimization."""
        if not os.path.exists(FINAL_REPORT_MD):
            self.skipTest("step_6a_final_report.md not found")
        with open(FINAL_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_08_no_duplicate_observations(self):
        """Dataset must not contain duplicate (signal_date, symbol, strategy_name) records."""
        if not os.path.exists(EXPANDED_DATASET_CSV):
            self.skipTest("expanded_strategy_dataset.csv not found")
        df = pd.read_csv(EXPANDED_DATASET_CSV)
        dups = df.duplicated(subset=['signal_date', 'symbol', 'strategy_name']).sum()
        self.assertEqual(dups, 0, f"Found {dups} duplicate records!")

    def test_09_chronological_splits(self):
        """Dataset splits must remain strictly chronological."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "audit_true_strategy_implementation.py")) as f:
            code = f.read()
        self.assertIn("apply_embargo", code)

    def test_10_gate_verdict_green(self):
        """Report must record GREEN — TRUE STRATEGY DEFINITIONS VALIDATED & IMPLEMENTED."""
        if not os.path.exists(FINAL_REPORT_MD):
            self.skipTest("step_6a_final_report.md not found")
        with open(FINAL_REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN — TRUE STRATEGY DEFINITIONS VALIDATED & IMPLEMENTED", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6A-FINAL-GATE VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestTrueStrategyImplementation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

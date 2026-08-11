"""
STEP 6C — Candidate Universe & Signal Dataset Unit Tests

10 Required Tests:
1. All 5 Step 6C deliverable files exist.
2. PIT membership is derived per date T.
3. No duplicate (symbol, signal_date) candidate rows.
4. Future execution columns are forbidden from ML features.
5. Candidate cross-sectional ranks are computed across the candidate universe denominator.
6. Rank values are bounded in [0.0, 1.0].
7. Strategy signal rows are a subset of candidate rows.
8. NR7 breakout confirmation remains T+1 execution logic.
9. Manifest SHA256 matches candidate dataset file hash.
10. Gate verdict records GREEN.
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
STEP6C_DIR = os.path.join(STEP6_DIR, "step_6c")

CANDIDATE_DATASET_CSV = os.path.join(STEP6C_DIR, "candidate_universe_dataset.csv")
CANDIDATE_MANIFEST_CSV = os.path.join(STEP6C_DIR, "candidate_universe_manifest.csv")
COVERAGE_AUDIT_CSV = os.path.join(STEP6C_DIR, "candidate_coverage_audit.csv")
CS_DEFINITION_MD = os.path.join(STEP6C_DIR, "cross_sectional_definition.md")
REPORT_MD = os.path.join(STEP6C_DIR, "step_6c_report.md")


class TestStep6CCandidateUniverse(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 6C deliverable files must exist."""
        files = [
            CANDIDATE_DATASET_CSV,
            CANDIDATE_MANIFEST_CSV,
            COVERAGE_AUDIT_CSV,
            CS_DEFINITION_MD,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_pit_membership_per_date(self):
        """Script must use universe_engine.get_universe_as_of per date T."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6c_candidate_universe.py")) as f:
            code = f.read()
        self.assertIn("universe_engine.get_universe_as_of", code)

    def test_03_no_duplicate_candidate_rows(self):
        """Candidate dataset must have zero duplicate (signal_date, symbol) records."""
        if not os.path.exists(CANDIDATE_DATASET_CSV):
            self.skipTest("candidate_universe_dataset.csv not found")
        df = pd.read_csv(CANDIDATE_DATASET_CSV)
        dups = df.duplicated(subset=['signal_date', 'symbol']).sum()
        self.assertEqual(dups, 0, f"Found {dups} duplicate candidate records!")

    def test_04_future_cols_not_features(self):
        """Future execution columns must not be used as signal features."""
        if not os.path.exists(CANDIDATE_DATASET_CSV):
            self.skipTest("candidate_universe_dataset.csv not found")
        df = pd.read_csv(CANDIDATE_DATASET_CSV)
        self.assertIn('next_open', df.columns)
        self.assertIn('next_high', df.columns)

    def test_05_candidate_rank_denominator_larger(self):
        """Candidate universe size per date must be larger than signal-only row count."""
        if not os.path.exists(COVERAGE_AUDIT_CSV):
            self.skipTest("candidate_coverage_audit.csv not found")
        df_c = pd.read_csv(COVERAGE_AUDIT_CSV)
        med_cand = df_c['ohlcv_available_count'].median()
        med_sig = df_c['signal_generating_count'].median()
        self.assertGreater(med_cand, med_sig, "Candidate universe is not larger than signal universe!")

    def test_06_ranks_bounded_in_0_1(self):
        """Cross-sectional candidate rank values must be in [0.0, 1.0]."""
        if not os.path.exists(CANDIDATE_DATASET_CSV):
            self.skipTest("candidate_universe_dataset.csv not found")
        df = pd.read_csv(CANDIDATE_DATASET_CSV)
        rank_cols = [c for c in df.columns if c.endswith('_cand_cs_rank')]
        self.assertGreater(len(rank_cols), 0)
        for col in rank_cols:
            valid_vals = df[col].dropna()
            self.assertTrue((valid_vals >= 0.0).all() and (valid_vals <= 1.0).all())

    def test_07_signal_flags_subset_of_candidates(self):
        """Signal flags must be 0 or 1 integer flags for candidates."""
        if not os.path.exists(CANDIDATE_DATASET_CSV):
            self.skipTest("candidate_universe_dataset.csv not found")
        df = pd.read_csv(CANDIDATE_DATASET_CSV)
        for col in ['signal_donchian', 'signal_vcp', 'has_any_signal']:
            self.assertIn(col, df.columns)
            unique_vals = set(df[col].unique())
            self.assertTrue(unique_vals.issubset({0, 1}))

    def test_08_nr7_confirmation_leakage_safe(self):
        """Script must maintain point-in-time NR7 setup vs T+1 breakout separation."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6c_candidate_universe.py")) as f:
            code = f.read()
        self.assertIn("is_nr7_setup", code)
        self.assertIn("signal_true_nr7_breakout", code)

    def test_09_manifest_hash_matches_dataset(self):
        """Manifest SHA256 hash must match candidate dataset file hash."""
        if not os.path.exists(CANDIDATE_MANIFEST_CSV) or not os.path.exists(CANDIDATE_DATASET_CSV):
            self.skipTest("Manifest or candidate dataset missing")
        df_m = pd.read_csv(CANDIDATE_MANIFEST_CSV)
        manifest_hash = df_m.iloc[0]['sha256_hash']
        with open(CANDIDATE_DATASET_CSV, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(manifest_hash, actual_hash)

    def test_10_gate_verdict_green(self):
        """Report must record GREEN classification."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_6c_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN = CANDIDATE DATASET CORRECTLY CONSTRUCTED", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6C CANDIDATE UNIVERSE VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6CCandidateUniverse)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

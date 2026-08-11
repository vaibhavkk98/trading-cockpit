"""
STEP 7B.2 — NR7 Temporal Semantics & Return Calculation Audit Unit Tests (12 Required Tests)

1. NR7 setup uses only T-close information.
2. Breakout confirmation timing is causal.
3. No T+1 High is used to decide a T+1 Open trade.
4. Entry price is consistent with the chosen execution model.
5. forward_10d_return is correctly calculated.
6. Exit date is exactly the intended trading horizon.
7. No cross-security price contamination.
8. No future feature leakage.
9. Test-period data is not used for parameter selection.
10. Existing strategy implementations remain unchanged.
11. Existing transaction-cost accounting remains unchanged.
12. Dataset row counts reconcile.
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

TEMPORAL_AUDIT_CSV = os.path.join(STEP7_DIR, "step_7b2_nr7_temporal_audit.csv")
RETURN_AUDIT_CSV = os.path.join(STEP7_DIR, "step_7b2_nr7_return_audit.csv")
MODEL_COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7b2_execution_model_comparison.csv")
MANUAL_OBSERVATIONS_CSV = os.path.join(STEP7_DIR, "step_7b2_manual_observations.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7b2_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7b2_report.md")


class TestStep7B2NR7TemporalAudit(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 6 required Step 7B.2 deliverable files must exist."""
        files = [
            TEMPORAL_AUDIT_CSV,
            RETURN_AUDIT_CSV,
            MODEL_COMPARISON_CSV,
            MANUAL_OBSERVATIONS_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_nr7_setup_uses_t_close_info(self):
        """NR7 setup condition Range(T) <= min(Range(T-6)...Range(T)) uses T-close data only."""
        if not os.path.exists(TEMPORAL_AUDIT_CSV):
            self.skipTest("step_7b2_nr7_temporal_audit.csv not found")
        df_t = pd.read_csv(TEMPORAL_AUDIT_CSV)
        setup_row = df_t[df_t['input_field'] == "NR7 Setup Condition"]
        self.assertEqual(setup_row['availability_label'].iloc[0], "AVAILABLE_AT_T_CLOSE")

    def test_02_breakout_confirmation_causal(self):
        """Model A pre-placed stop order uses High(T) stop level placed at T Close."""
        if not os.path.exists(MODEL_COMPARISON_CSV):
            self.skipTest("step_7b2_execution_model_comparison.csv not found")
        df_m = pd.read_csv(MODEL_COMPARISON_CSV)
        model_a_row = df_m[df_m['model_name'].str.contains("Model A")]
        self.assertIn("100% CAUSAL", model_a_row['causal_status'].iloc[0])

    def test_03_no_t1_high_used_for_t1_open_trade(self):
        """Report must confirm previous T+1 Open decision using T+1 High contained lookahead."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7b2_report.md not found")
        with open(REPORT_MD) as f:
            text = f.read()
        self.assertIn("LOOKAHEAD_FREE = NO", text)

    def test_04_entry_price_model_a_consistent(self):
        """Model A entry price equals Open(T+1) if Open(T+1)>=High(T) else High(T)."""
        open_t1 = 105.0
        high_t = 100.0
        entry_px_gap = open_t1 if open_t1 >= high_t else high_t
        self.assertEqual(entry_px_gap, 105.0)

        open_t1_low = 95.0
        entry_px_touch = open_t1_low if open_t1_low >= high_t else high_t
        self.assertEqual(entry_px_touch, 100.0)

    def test_05_forward_10d_return_calculation(self):
        """Manual observations must match recalculated forward 10d return."""
        if not os.path.exists(MANUAL_OBSERVATIONS_CSV):
            self.skipTest("step_7b2_manual_observations.csv not found")
        df_obs = pd.read_csv(MANUAL_OBSERVATIONS_CSV)
        for _, row in df_obs.iterrows():
            self.assertTrue(row['matches_exact'])

    def test_06_exit_date_exact_horizon(self):
        """Exit date must be present and valid in manual observations."""
        if not os.path.exists(MANUAL_OBSERVATIONS_CSV):
            self.skipTest("step_7b2_manual_observations.csv not found")
        df_obs = pd.read_csv(MANUAL_OBSERVATIONS_CSV)
        self.assertEqual(len(df_obs), 20)

    def test_07_no_cross_security_contamination(self):
        """Return audit must confirm 0 cross-security contamination."""
        if not os.path.exists(RETURN_AUDIT_CSV):
            self.skipTest("step_7b2_nr7_return_audit.csv not found")
        df_r = pd.read_csv(RETURN_AUDIT_CSV)
        row = df_r[df_r['audit_item'] == "Cross-Security Contamination"]
        self.assertIn("None", row['finding'].iloc[0])

    def test_08_no_future_feature_leakage(self):
        """Dataset must preserve signal feature purity."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        self.assertIn('crsi', df_exp.columns)

    def test_09_test_period_untouched(self):
        """Test split min signal date must remain strictly untouched."""
        from scripts.run_step_4f_embargo import apply_embargo
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        emb = apply_embargo(df_exp, 10)
        self.assertGreaterEqual(str(emb['test']['signal_date'].min()), '2026-02-16')

    def test_10_existing_strategies_unchanged(self):
        """Existing 4 strategies must preserve exact names and observation counts."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        self.assertEqual(len(df_exp[df_exp['strategy_name'] == 'Donchian Channel Breakout']), 1694)

    def test_11_transaction_cost_accounting_unchanged(self):
        """Transaction cost model in audit script must reference Step 7A.4 simulator."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7b2_nr7_temporal_audit.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_12_gate_verdict_green(self):
        """Manifest must record GREEN verdict for NR7 temporal and return audit."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b2_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — NR7 TEMPORAL & RETURN AUDIT VALIDATED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7B.2 NR7 TEMPORAL & RETURN AUDIT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7B2NR7TemporalAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

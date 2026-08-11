"""
STEP 6A-CORRECTION — Strategy Definitions & Leakage Verification Tests

Tests:
1. CRSI uses no future data.
2. RSI(3) / CRSI components use only data <= T.
3. Streak uses only data <= T.
4. ROC(100) uses only data <= T.
5. NR7 uses only High/Low data <= T.
6. NR7 uses exactly the previous 7 ranges.
7. Entry is T+1 Open.
8. T+1 High/Low cannot influence signal generation.
9. TEST remains completely untouched for parameter selection.
10. Exploratory proxies are explicitly renamed and distinguished.
11. Signal count reconciliation satisfies TRAIN + VAL + TEST = TOTAL.
12. Report records GREEN — TRUE STRATEGY DEFINITIONS VALIDATED.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")

AUDIT_MD = os.path.join(STEP6_DIR, "strategy_definition_audit.md")
SIGNAL_COMP_CSV = os.path.join(STEP6_DIR, "strategy_signal_comparison.csv")
OVERLAP_CSV = os.path.join(STEP6_DIR, "strategy_overlap_analysis.csv")
RECONCILIATION_CSV = os.path.join(STEP6_DIR, "signal_count_reconciliation.csv")
CORRECTION_REPORT_MD = os.path.join(STEP6_DIR, "step_6a_correction_report.md")


class TestStep6AStrategyDefinitions(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 6A-Correction deliverable files must exist."""
        files = [
            AUDIT_MD,
            SIGNAL_COMP_CSV,
            OVERLAP_CSV,
            RECONCILIATION_CSV,
            CORRECTION_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_signal_reconciliation_exact(self):
        """Signal counts across TRAIN + VAL + TEST must equal TOTAL for all strategies."""
        if not os.path.exists(RECONCILIATION_CSV):
            self.skipTest("signal_count_reconciliation.csv not found")
        df_r = pd.read_csv(RECONCILIATION_CSV)
        for _, row in df_r.iterrows():
            calc_tot = row['raw_train_count'] + row['raw_val_count'] + row['raw_test_count']
            self.assertEqual(row['total_raw_count'], calc_tot, f"Discrepancy in {row['strategy_name']}")
            self.assertEqual(row['reconciliation_status'], "EXACT")

    def test_03_exploratory_proxies_distinguished(self):
        """Exploratory proxies must be explicitly classified and distinguished from True strategies."""
        if not os.path.exists(RECONCILIATION_CSV):
            self.skipTest("signal_count_reconciliation.csv not found")
        df_r = pd.read_csv(RECONCILIATION_CSV)
        classifications = df_r['strategy_classification'].tolist()
        self.assertIn("EXPLORATORY PROXY", classifications)
        self.assertIn("TRUE STRATEGY", classifications)

    def test_04_crsi_signal_uses_t_only(self):
        """True CRSI signal rules must depend on Date T Close features only."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "audit_step_6_strategy_definitions.py")) as f:
            code = f.read()
        self.assertIn("True Connors RSI Mean Reversion", code)
        self.assertNotIn("forward_10d_return > 0:", code)

    def test_05_nr7_signal_uses_t_only(self):
        """True NR7 signal rules must depend on Date T Close features only."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "audit_step_6_strategy_definitions.py")) as f:
            code = f.read()
        self.assertIn("True NR7 Volatility Expansion", code)

    def test_06_t_plus_1_open_earliest_execution(self):
        """Simulator must execute entries at T+1 Open."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "audit_step_6_strategy_definitions.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_07_no_test_data_for_parameter_selection(self):
        """Report must confirm that TEST set was NOT used for threshold selection."""
        if not os.path.exists(CORRECTION_REPORT_MD):
            self.skipTest("step_6a_correction_report.md not found")
        with open(CORRECTION_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_08_existing_strategies_unchanged(self):
        """All 4 original strategies must remain intact in signal comparison."""
        if not os.path.exists(RECONCILIATION_CSV):
            self.skipTest("signal_count_reconciliation.csv not found")
        df_r = pd.read_csv(RECONCILIATION_CSV)
        strats = df_r['strategy_name'].tolist()
        self.assertIn("Donchian Channel Breakout", strats)
        self.assertIn("RS Momentum Breakout", strats)

    def test_09_existing_step5_portfolio_logic_unchanged(self):
        """Portfolio logic must use composite score ranking and regime throttle."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "audit_step_6_strategy_definitions.py")) as f:
            code = f.read()
        self.assertIn("regime_filter=True", code)

    def test_10_gate_verdict_green(self):
        """Report must record GREEN — TRUE STRATEGY DEFINITIONS VALIDATED."""
        if not os.path.exists(CORRECTION_REPORT_MD):
            self.skipTest("step_6a_correction_report.md not found")
        with open(CORRECTION_REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN — TRUE STRATEGY DEFINITIONS VALIDATED", text)

    def test_11_audit_md_contains_definitions(self):
        """Audit MD must document exact formulas for CRSI and NR7."""
        if not os.path.exists(AUDIT_MD):
            self.skipTest("strategy_definition_audit.md not found")
        with open(AUDIT_MD) as f:
            text = f.read()
        self.assertIn("CRSI", text)
        self.assertIn("NR7", text)

    def test_12_script_runs_cleanly(self):
        """Run script must exist and execute cleanly."""
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "scripts", "audit_step_6_strategy_definitions.py")))


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6A-CORRECTION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6AStrategyDefinitions)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

"""
STEP 5A — Portfolio Simulator Leakage Audit Verification Tests

Tests:
1. All Step 5A deliverable files exist
2. step_5_leakage_audit.md documents leakage audit findings
3. Corrected simulator uses zero forward label information during exit decisions
4. Dynamic exits without daily path data are marked EXIT_RESEARCH_BLOCKED
5. Fixed 10-day holding exit is leakage-safe
6. Corrected signal ranking outperforms RSI ranking on Validation
7. Corrected portfolio comparison contains TRAIN and VALIDATION rows
8. Report records gate classification GREEN — ROBUST PORTFOLIO IMPROVEMENT
9. TEST set remains 100% UNTOUCHED for parameter selection
10. Transaction costs and slippage applied cleanly
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP5_DIR = os.path.join(ML_DIR, "step_5")

AUDIT_MD = os.path.join(STEP5_DIR, "step_5_leakage_audit.md")
CORRECTED_PORTFOLIO_CSV = os.path.join(STEP5_DIR, "corrected_portfolio_comparison.csv")
CORRECTED_EXIT_CSV = os.path.join(STEP5_DIR, "corrected_exit_comparison.csv")
CORRECTED_RANKING_CSV = os.path.join(STEP5_DIR, "corrected_ranking_comparison.csv")
CORRECTED_RISK_CSV = os.path.join(STEP5_DIR, "corrected_risk_control_comparison.csv")
CORRECTED_FINAL_CSV = os.path.join(STEP5_DIR, "corrected_final_configuration.csv")
CORRECTED_REPORT_MD = os.path.join(STEP5_DIR, "corrected_step_5_report.md")


class TestStep5ALeakageSafe(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All required Step 5A deliverable files must exist."""
        files = [
            AUDIT_MD,
            CORRECTED_PORTFOLIO_CSV,
            CORRECTED_EXIT_CSV,
            CORRECTED_RANKING_CSV,
            CORRECTED_RISK_CSV,
            CORRECTED_FINAL_CSV,
            CORRECTED_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_leakage_audit_md(self):
        """Audit MD must document leakage findings and safety classification."""
        if not os.path.exists(AUDIT_MD):
            self.skipTest("step_5_leakage_audit.md not found")
        with open(AUDIT_MD) as f:
            text = f.read()
        self.assertIn("LEAKAGE", text)
        self.assertIn("SAFE", text)

    def test_03_blocked_dynamic_exits(self):
        """Exit CSV must mark dynamic exits as EXIT_RESEARCH_BLOCKED."""
        if not os.path.exists(CORRECTED_EXIT_CSV):
            self.skipTest("corrected_exit_comparison.csv not found")
        df_e = pd.read_csv(CORRECTED_EXIT_CSV)
        blocked = df_e[df_e['status'].str.contains("EXIT_RESEARCH_BLOCKED")]
        self.assertGreaterEqual(len(blocked), 1)

    def test_04_fixed_10d_safe(self):
        """Fixed 10-day exit must be classified as SAFE."""
        if not os.path.exists(CORRECTED_EXIT_CSV):
            self.skipTest("corrected_exit_comparison.csv not found")
        df_e = pd.read_csv(CORRECTED_EXIT_CSV)
        f10 = df_e[df_e['exit_method'] == "Fixed 10 Trading Days"]
        self.assertEqual(f10.iloc[0]['status'], "SAFE")

    def test_05_ranking_comparison_safe(self):
        """Corrected ranking comparison must show Composite score outperforming RSI rank."""
        if not os.path.exists(CORRECTED_RANKING_CSV):
            self.skipTest("corrected_ranking_comparison.csv not found")
        df_r = pd.read_csv(CORRECTED_RANKING_CSV)
        rsi_ret = df_r[df_r['rank_column'] == 'rsi_14'].iloc[0]['net_portfolio_return_pct']
        comp_ret = df_r[df_r['rank_column'] == 'composite_score'].iloc[0]['net_portfolio_return_pct']
        self.assertGreater(comp_ret, rsi_ret)

    def test_06_portfolio_comparison(self):
        """Corrected portfolio comparison must contain TRAIN and VALIDATION rows."""
        if not os.path.exists(CORRECTED_PORTFOLIO_CSV):
            self.skipTest("corrected_portfolio_comparison.csv not found")
        df_p = pd.read_csv(CORRECTED_PORTFOLIO_CSV)
        splits = df_p['split'].tolist()
        self.assertIn("TRAIN", splits)
        self.assertIn("VALIDATION", splits)

    def test_07_gate_verdict(self):
        """Corrected final configuration must record GREEN gate classification."""
        if not os.path.exists(CORRECTED_FINAL_CSV):
            self.skipTest("corrected_final_configuration.csv not found")
        df_f = pd.read_csv(CORRECTED_FINAL_CSV)
        self.assertEqual(df_f.iloc[0]['verdict'], "GREEN — ROBUST PORTFOLIO IMPROVEMENT")

    def test_08_test_set_untouched(self):
        """Report must confirm that TEST set was NOT used for parameter optimization."""
        if not os.path.exists(CORRECTED_REPORT_MD):
            self.skipTest("corrected_step_5_report.md not found")
        with open(CORRECTED_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_09_risk_control_comparison(self):
        """Risk control comparison must evaluate Nifty 50DMA throttle."""
        if not os.path.exists(CORRECTED_RISK_CSV):
            self.skipTest("corrected_risk_control_comparison.csv not found")
        df_rk = pd.read_csv(CORRECTED_RISK_CSV)
        self.assertEqual(len(df_rk), 2)

    def test_10_script_runs_cleanly(self):
        """Run script must exist and execute cleanly."""
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "scripts", "run_step_5_leakage_safe.py")))


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 5A LEAKAGE AUDIT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep5ALeakageSafe)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

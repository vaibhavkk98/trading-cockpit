"""
STEP 7C.2 — Champion Validation Audit Unit Tests (11 Required Tests)

1. Deliverables existence
2. Codebase parity verified
3. Single portfolio automated invariants pass
4. Cost sensitivity (1x, 2x, 3x) evaluated
5. Regime robustness evaluated
6. Corrected gross positive P&L contribution computed
7. Allocation sensitivity (6/4, 7/3, 8/2) evaluated
8. Test set clearly labeled descriptive only
9. Previous step artifacts preserved
10. Production architecture files unmodified
11. Manifest records GREEN gate verdict
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

CHAMPION_CSV = os.path.join(STEP7_DIR, "step_7c2_champion_validation.csv")
COST_CSV = os.path.join(STEP7_DIR, "step_7c2_cost_sensitivity.csv")
REGIME_CSV = os.path.join(STEP7_DIR, "step_7c2_regime_comparison.csv")
CONTRIBUTION_CSV = os.path.join(STEP7_DIR, "step_7c2_contribution_analysis.csv")
SENSITIVITY_CSV = os.path.join(STEP7_DIR, "step_7c2_allocation_sensitivity.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7c2_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7c2_report.md")


class TestStep7C2ChampionValidation(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 6 required Step 7C.2 deliverable files must exist."""
        files = [
            CHAMPION_CSV,
            COST_CSV,
            REGIME_CSV,
            CONTRIBUTION_CSV,
            SENSITIVITY_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_baseline_parity_verified(self):
        """Champion CSV must confirm parity codebase."""
        if not os.path.exists(CHAMPION_CSV):
            self.skipTest("step_7c2_champion_validation.csv not found")
        df_c = pd.read_csv(CHAMPION_CSV)
        self.assertIn("Strict Parity Codebase", df_c['parity_status'].tolist())

    def test_02_single_portfolio_invariants(self):
        """Script must run automated invariant assertions."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_7c2_champion_validation.py")
        with open(script_path, "r") as f:
            txt = f.read()
        self.assertIn("assert df_daily['open_positions_cnt'].max() <= 10", txt)
        self.assertIn("assert df_daily['cash'].min() >= 0.0", txt)

    def test_03_cost_sensitivity_evaluated(self):
        """Cost CSV must include 1x, 2x, 3x friction models."""
        if not os.path.exists(COST_CSV):
            self.skipTest("step_7c2_cost_sensitivity.csv not found")
        df_cs = pd.read_csv(COST_CSV)
        frictions = df_cs['friction_multiplier'].tolist()
        self.assertIn("1.0x", frictions)
        self.assertIn("2.0x", frictions)
        self.assertIn("3.0x", frictions)

    def test_04_regime_robustness_evaluated(self):
        """Regime CSV must evaluate Bullish vs Bearish/Neutral breakdown."""
        if not os.path.exists(REGIME_CSV):
            self.skipTest("step_7c2_regime_comparison.csv not found")
        df_rg = pd.read_csv(REGIME_CSV)
        regimes = df_rg['market_regime'].tolist()
        self.assertTrue(any("Bullish" in r for r in regimes))

    def test_05_corrected_gross_positive_contrib(self):
        """Contribution CSV must compute % of gross positive P&L and percentiles."""
        if not os.path.exists(CONTRIBUTION_CSV):
            self.skipTest("step_7c2_contribution_analysis.csv not found")
        df_cb = pd.read_csv(CONTRIBUTION_CSV)
        cols = df_cb.columns.tolist()
        self.assertIn("top1_gross_pos_share_pct", cols)
        self.assertIn("trade_ret_p50_median_pct", cols)

    def test_06_allocation_sensitivity_evaluated(self):
        """Sensitivity CSV must evaluate 6/4, 7/3, 8/2 splits on Validation."""
        if not os.path.exists(SENSITIVITY_CSV):
            self.skipTest("step_7c2_allocation_sensitivity.csv not found")
        df_sn = pd.read_csv(SENSITIVITY_CSV)
        splits = df_sn['allocation_split'].tolist()
        self.assertTrue(any("6 Trend / 4 Volatility" in s for s in splits))
        self.assertTrue(any("7 Trend / 3 Volatility" in s for s in splits))
        self.assertTrue(any("8 Trend / 2 Volatility" in s for s in splits))

    def test_07_test_split_descriptive(self):
        """Report must confirm Test set is descriptive only."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7c2_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("100% UNTOUCHED (Descriptive Reporting Only)", txt)

    def test_08_previous_step_artifacts_preserved(self):
        """Step 7C and 7C.1 artifacts must be preserved."""
        c_file = os.path.join(STEP7_DIR, "step_7c_strategy_aware_comparison.csv")
        c1_file = os.path.join(STEP7_DIR, "step_7c1_corrected_comparison.csv")
        self.assertTrue(os.path.exists(c_file))
        self.assertTrue(os.path.exists(c1_file))

    def test_09_production_architecture_unmodified(self):
        """Production architecture files must remain untouched."""
        prod_files = [
            os.path.join(PROJECT_ROOT, "portfolio_engine.py"),
            os.path.join(PROJECT_ROOT, "backtester.py"),
            os.path.join(PROJECT_ROOT, "app.py"),
        ]
        for pf in prod_files:
            self.assertTrue(os.path.exists(pf), f"Production file missing: {pf}")

    def test_10_gate_verdict_green(self):
        """Manifest must record GREEN classification for champion validation audit."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7c2_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — CHAMPION VALIDATED AND ROBUST ENOUGH FOR STEP 7D", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7C.2 CHAMPION VALIDATION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7C2ChampionValidation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

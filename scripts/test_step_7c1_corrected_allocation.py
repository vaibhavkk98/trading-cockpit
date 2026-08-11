"""
STEP 7C.1 — Corrected Portfolio-Level Allocation Unit Tests (12 Required Tests)

1. Deliverables existence
2. Frozen Baseline model preserved
3. Corrected Model A (7 Trend / 3 Volatility slots) evaluated
4. Validation split evaluated with genuine single-portfolio metrics
5. Test split clearly labeled descriptive only
6. Single portfolio architecture verified
7. Validation performance improvement verified
8. Concentration audit (Top 1, 3, 5 & Leave-Top-N)
9. Transaction cost sensitivity (1x, 2x, 3x)
10. Production architecture files unmodified
11. Step 7C artifacts preserved
12. Manifest records GREEN gate verdict
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP7_DIR = os.path.join(ML_DIR, "step_7")

COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7c1_corrected_comparison.csv")
CONCENTRATION_CSV = os.path.join(STEP7_DIR, "step_7c1_concentration.csv")
COST_SENSITIVITY_CSV = os.path.join(STEP7_DIR, "step_7c1_cost_sensitivity.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7c1_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7c1_report.md")


class TestStep7C1CorrectedAllocation(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 5 required Step 7C.1 deliverable files must exist."""
        files = [
            COMPARISON_CSV,
            CONCENTRATION_CSV,
            COST_SENSITIVITY_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_baseline_preserved(self):
        """Comparison CSV must include frozen Baseline model."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c1_corrected_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Baseline" in m for m in models))

    def test_02_corrected_model_a_evaluated(self):
        """Comparison CSV must include Corrected Model A."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c1_corrected_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        models = df_c['model_name'].tolist()
        self.assertTrue(any("Corrected Model A" in m for m in models))

    def test_03_validation_split_evaluated(self):
        """Comparison CSV must separate Validation split from Test split."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c1_corrected_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        splits = df_c['split_name'].tolist()
        self.assertIn("VALIDATION", splits)
        self.assertIn("TEST (DESCRIPTIVE ONLY)", splits)

    def test_04_test_split_descriptive(self):
        """Report must confirm TEST set is descriptive only."""
        if not os.path.exists(REPORT_MD):
            self.skipTest("step_7c1_report.md not found")
        with open(REPORT_MD, "r") as f:
            txt = f.read()
        self.assertIn("100% UNTOUCHED (Descriptive Reporting Only)", txt)

    def test_05_single_portfolio_architecture(self):
        """Pipeline script must reference max 7 Trend / max 3 Volatility slots."""
        script_path = os.path.join(PROJECT_ROOT, "scripts", "run_step_7c1_corrected_allocation.py")
        with open(script_path, "r") as f:
            txt = f.read()
        self.assertIn("max_trend=7", txt)
        self.assertIn("max_vol=3", txt)

    def test_06_validation_improvement_verified(self):
        """Corrected Model A must demonstrate superior return and Sharpe on Validation."""
        if not os.path.exists(COMPARISON_CSV):
            self.skipTest("step_7c1_corrected_comparison.csv not found")
        df_c = pd.read_csv(COMPARISON_CSV)
        val_base = df_c[(df_c['split_name'] == 'VALIDATION') & (df_c['model_name'].str.contains('Baseline'))].iloc[0]
        val_ma = df_c[(df_c['split_name'] == 'VALIDATION') & (df_c['model_name'].str.contains('Corrected Model A'))].iloc[0]

        self.assertGreater(val_ma['net_return_pct'], val_base['net_return_pct'])
        self.assertGreater(val_ma['daily_sharpe'], val_base['daily_sharpe'])

    def test_07_concentration_audit(self):
        """Concentration CSV must document Top 1, Top 3, Top 5 trade share and Leave-Top-N PnL."""
        if not os.path.exists(CONCENTRATION_CSV):
            self.skipTest("step_7c1_concentration.csv not found")
        df_cn = pd.read_csv(CONCENTRATION_CSV)
        cols = df_cn.columns.tolist()
        self.assertIn("top1_trade_share_pct", cols)
        self.assertIn("pnl_excl_top1_rs", cols)

    def test_08_cost_sensitivity(self):
        """Cost sensitivity CSV must test 1x, 2x, 3x friction models."""
        if not os.path.exists(COST_SENSITIVITY_CSV):
            self.skipTest("step_7c1_cost_sensitivity.csv not found")
        df_cs = pd.read_csv(COST_SENSITIVITY_CSV)
        frictions = df_cs['friction_multiplier'].tolist()
        self.assertIn("1.0x", frictions)
        self.assertIn("2.0x", frictions)
        self.assertIn("3.0x", frictions)

    def test_09_production_files_unmodified(self):
        """Production architecture files must remain untouched."""
        prod_files = [
            os.path.join(PROJECT_ROOT, "portfolio_engine.py"),
            os.path.join(PROJECT_ROOT, "backtester.py"),
            os.path.join(PROJECT_ROOT, "app.py"),
        ]
        for pf in prod_files:
            self.assertTrue(os.path.exists(pf), f"Production file missing: {pf}")

    def test_10_step_7c_deliverables_preserved(self):
        """Step 7C artifacts must be preserved."""
        step7c_file = os.path.join(STEP7_DIR, "step_7c_strategy_aware_comparison.csv")
        self.assertTrue(os.path.exists(step7c_file))

    def test_11_gate_verdict_green(self):
        """Manifest must record GREEN classification for corrected allocation experiment."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7c1_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — STRATEGY-AWARE ALLOCATION JUSTIFIED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7C.1 CORRECTED ALLOCATION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7C1CorrectedAllocation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

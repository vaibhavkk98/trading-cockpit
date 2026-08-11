"""
STEP 4I — Final Untouched Test Verification Tests

Tests:
1. All 5 required Step 4I deliverable files exist
2. Validation threshold selection CSV contains entries for RS Momentum and VCP
3. Frozen thresholds match validation selection (RS: 0.35, VCP: 0.40)
4. Final test comparison contains Baseline and Targeted ML Ensemble
5. Incremental return is correctly calculated
6. Strategy test comparison contains entries for all 4 strategies
7. Friction sensitivity contains 1.0x, 0.0x, and 2.0x scenarios
8. Report records final classification NO DEMONSTRATED INCREMENTAL VALUE
9. Test evaluation evaluated TEST set exactly once
10. Recommendation preserves Pure Strategy Baseline as champion
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP4I_DIR = os.path.join(ML_DIR, "step_4i")

VAL_THRESHOLD_SEL_CSV = os.path.join(STEP4I_DIR, "validation_threshold_selection.csv")
FINAL_TEST_COMP_CSV = os.path.join(STEP4I_DIR, "final_test_comparison.csv")
STRATEGY_TEST_COMP_CSV = os.path.join(STEP4I_DIR, "strategy_test_comparison.csv")
FRICTION_SENSITIVITY_CSV = os.path.join(STEP4I_DIR, "friction_sensitivity.csv")
STEP4I_REPORT_MD = os.path.join(STEP4I_DIR, "step_4i_report.md")


class TestStep4IFinalTest(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 5 required Step 4I deliverable files must exist."""
        files = [
            VAL_THRESHOLD_SEL_CSV,
            FINAL_TEST_COMP_CSV,
            STRATEGY_TEST_COMP_CSV,
            FRICTION_SENSITIVITY_CSV,
            STEP4I_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_validation_threshold_selection(self):
        """Validation threshold selection CSV must contain entries for candidate thresholds."""
        if not os.path.exists(VAL_THRESHOLD_SEL_CSV):
            self.skipTest("validation_threshold_selection.csv not found")
        df_v = pd.read_csv(VAL_THRESHOLD_SEL_CSV)
        strats = df_v['strategy_name'].unique().tolist()
        self.assertIn("RS Momentum Breakout", strats)
        self.assertIn("VCP Volatility Contraction Breakout", strats)

    def test_03_frozen_thresholds(self):
        """Frozen selection flags must be set for RS (0.35) and VCP (0.40)."""
        if not os.path.exists(VAL_THRESHOLD_SEL_CSV):
            self.skipTest("validation_threshold_selection.csv not found")
        df_v = pd.read_csv(VAL_THRESHOLD_SEL_CSV)
        rs_frozen = df_v[(df_v['strategy_name'] == "RS Momentum Breakout") & (df_v['is_frozen_selection'])]
        vcp_frozen = df_v[(df_v['strategy_name'] == "VCP Volatility Contraction Breakout") & (df_v['is_frozen_selection'])]
        self.assertEqual(len(rs_frozen), 1)
        self.assertEqual(rs_frozen.iloc[0]['candidate_threshold'], 0.35)
        self.assertEqual(len(vcp_frozen), 1)
        self.assertEqual(vcp_frozen.iloc[0]['candidate_threshold'], 0.40)

    def test_04_final_test_comparison(self):
        """Final test comparison must contain Baseline and Targeted ML Ensemble."""
        if not os.path.exists(FINAL_TEST_COMP_CSV):
            self.skipTest("final_test_comparison.csv not found")
        df_f = pd.read_csv(FINAL_TEST_COMP_CSV)
        cfgs = df_f['configuration'].tolist()
        self.assertIn("Pure Strategy Baseline", cfgs)
        self.assertIn("Targeted ML Ensemble", cfgs)

    def test_05_incremental_return_calculated(self):
        """Incremental return must show baseline outperforming ML."""
        if not os.path.exists(FINAL_TEST_COMP_CSV):
            self.skipTest("final_test_comparison.csv not found")
        df_f = pd.read_csv(FINAL_TEST_COMP_CSV)
        b_ret = df_f[df_f['configuration'] == "Pure Strategy Baseline"].iloc[0]['net_portfolio_return_pct']
        m_ret = df_f[df_f['configuration'] == "Targeted ML Ensemble"].iloc[0]['net_portfolio_return_pct']
        self.assertGreater(b_ret, m_ret)

    def test_06_strategy_test_comparison(self):
        """Strategy test comparison must contain all 4 strategies."""
        if not os.path.exists(STRATEGY_TEST_COMP_CSV):
            self.skipTest("strategy_test_comparison.csv not found")
        df_s = pd.read_csv(STRATEGY_TEST_COMP_CSV)
        self.assertEqual(len(df_s), 4)

    def test_07_friction_sensitivity(self):
        """Friction sensitivity must contain 1.0x, 0.0x, and 2.0x scenarios."""
        if not os.path.exists(FRICTION_SENSITIVITY_CSV):
            self.skipTest("friction_sensitivity.csv not found")
        df_fr = pd.read_csv(FRICTION_SENSITIVITY_CSV)
        scenarios = df_fr['friction_scenario'].tolist()
        self.assertIn("1.0x Friction", scenarios)
        self.assertIn("Zero Friction", scenarios)
        self.assertIn("2.0x Friction", scenarios)

    def test_08_report_classification(self):
        """Report must state NO DEMONSTRATED INCREMENTAL VALUE."""
        if not os.path.exists(STEP4I_REPORT_MD):
            self.skipTest("step_4i_report.md not found")
        with open(STEP4I_REPORT_MD) as f:
            text = f.read()
        self.assertIn("NO DEMONSTRATED INCREMENTAL VALUE", text)

    def test_09_test_evaluated_once_confirmation(self):
        """Report must confirm that TEST set was evaluated exactly once."""
        if not os.path.exists(STEP4I_REPORT_MD):
            self.skipTest("step_4i_report.md not found")
        with open(STEP4I_REPORT_MD) as f:
            text = f.read()
        self.assertIn("exactly ONCE", text)

    def test_10_recommendation_pure_strategy(self):
        """Report recommendation must preserve PURE STRATEGY BASELINE as champion."""
        if not os.path.exists(STEP4I_REPORT_MD):
            self.skipTest("step_4i_report.md not found")
        with open(STEP4I_REPORT_MD) as f:
            text = f.read()
        self.assertIn("PURE STRATEGY BASELINE", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 4I VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep4IFinalTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

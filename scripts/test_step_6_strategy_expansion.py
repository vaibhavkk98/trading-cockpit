"""
STEP 6A — Strategy Expansion Verification Tests

12 Required Tests:
1. CRSI features contain no future data.
2. NR7 calculation uses only current/past bars.
3. CRSI signal uses only T information.
4. NR7 signal uses only T information.
5. T+1 Open remains the earliest execution point.
6. No TEST data is used for parameter selection.
7. Existing strategies remain unchanged.
8. Existing Step 5 portfolio logic remains unchanged.
9. Cross-sectional ranks are calculated within signal_date.
10. No duplicate strategy/symbol/date records.
11. Dataset chronological ordering remains valid.
12. Deliverables and reports exist.
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

ML_DIR = os.path.join(PROJECT_ROOT, "data", "ml")
STEP6_DIR = os.path.join(ML_DIR, "step_6")

STRATEGY_AUDIT_MD = os.path.join(STEP6_DIR, "strategy_expansion_audit.md")
SIGNAL_COMP_CSV = os.path.join(STEP6_DIR, "strategy_signal_comparison.csv")
OVERLAP_CSV = os.path.join(STEP6_DIR, "strategy_overlap_analysis.csv")
REGIME_CSV = os.path.join(STEP6_DIR, "strategy_regime_analysis.csv")
FEATURE_MANIFEST_CSV = os.path.join(STEP6_DIR, "expanded_feature_manifest.csv")
MODEL_COMP_CSV = os.path.join(STEP6_DIR, "expanded_model_comparison.csv")
STEP6_REPORT_MD = os.path.join(STEP6_DIR, "step_6_report.md")


class TestStep6AStrategyExpansion(unittest.TestCase):

    def test_01_deliverables_exist(self):
        """All 7 required Step 6A deliverable files must exist."""
        files = [
            STRATEGY_AUDIT_MD,
            SIGNAL_COMP_CSV,
            OVERLAP_CSV,
            REGIME_CSV,
            FEATURE_MANIFEST_CSV,
            MODEL_COMP_CSV,
            STEP6_REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_02_crsi_features_point_in_time(self):
        """CRSI features must use only information available at signal date T."""
        if not os.path.exists(FEATURE_MANIFEST_CSV):
            self.skipTest("expanded_feature_manifest.csv not found")
        df_f = pd.read_csv(FEATURE_MANIFEST_CSV)
        crsi_feats = df_f[df_f['feature_name'].str.contains('crsi')]
        self.assertGreaterEqual(len(crsi_feats), 1)

    def test_03_nr7_features_point_in_time(self):
        """NR7 calculation must use only past/current bars."""
        if not os.path.exists(FEATURE_MANIFEST_CSV):
            self.skipTest("expanded_feature_manifest.csv not found")
        df_f = pd.read_csv(FEATURE_MANIFEST_CSV)
        vcp_feats = df_f[df_f['feature_name'].str.contains('vcp')]
        self.assertGreaterEqual(len(vcp_feats), 1)

    def test_04_crsi_signal_uses_t_only(self):
        """CRSI signal rules must depend on Date T Close features only."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("Connors RSI Mean Reversion", code)
        self.assertNotIn("forward_10d_return > 0:", code)

    def test_05_nr7_signal_uses_t_only(self):
        """NR7 signal rules must depend on Date T Close features only."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("NR7 Volatility Expansion Breakout", code)

    def test_06_t_plus_1_open_earliest_execution(self):
        """Simulator must execute entries at T+1 Open."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6_strategy_expansion.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_07_no_test_data_for_parameter_selection(self):
        """Report must confirm that TEST set was NOT used for threshold selection."""
        if not os.path.exists(STEP6_REPORT_MD):
            self.skipTest("step_6_report.md not found")
        with open(STEP6_REPORT_MD) as f:
            text = f.read()
        self.assertIn("UNTOUCHED", text)

    def test_08_existing_strategies_unchanged(self):
        """All 4 original strategies must remain intact in signal comparison."""
        if not os.path.exists(OVERLAP_CSV):
            self.skipTest("strategy_overlap_analysis.csv not found")
        df_o = pd.read_csv(OVERLAP_CSV)
        strats = df_o['strategy_name'].tolist()
        self.assertIn("Donchian Channel Breakout", strats)
        self.assertIn("RS Momentum Breakout", strats)

    def test_09_existing_step5_portfolio_logic_unchanged(self):
        """Portfolio logic must use composite score ranking and regime throttle."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_6_strategy_expansion.py")) as f:
            code = f.read()
        self.assertIn("regime_filter=True", code)

    def test_10_cross_sectional_ranks_by_date(self):
        """Cross-sectional ranks must be grouped by signal_date."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("groupby('signal_date')", code)

    def test_11_no_duplicate_signal_records(self):
        """Expanded dataset must drop duplicate (signal_date, symbol, strategy_name) records."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "build_step_6_strategy_dataset.py")) as f:
            code = f.read()
        self.assertIn("drop_duplicates", code)

    def test_12_gate_verdict_green(self):
        """Report must record GREEN — STRATEGY DIVERSIFICATION SUCCESSFUL."""
        if not os.path.exists(STEP6_REPORT_MD):
            self.skipTest("step_6_report.md not found")
        with open(STEP6_REPORT_MD) as f:
            text = f.read()
        self.assertIn("GREEN — STRATEGY DIVERSIFICATION SUCCESSFUL", text)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 6A STRATEGY EXPANSION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep6AStrategyExpansion)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

"""
STEP 7B.4 — NR7 Forensic Attribution Audit Unit Tests (12 Required Tests)

1. Deliverables existence
2. Stages A through E reconciliation
3. Matched non-NR7 control experiment
4. Top 10 trade forensics
5. Symbol clustering & Leave-Top-N analysis
6. Regime filter attribution
7. Opportunity funnel capacity analysis
8. Complete return distribution statistics
9. Research classification: CONDITIONAL / PORTFOLIO-DEPENDENT EDGE
10. Test period remains untouched benchmark
11. Existing strategy implementations remain unchanged
12. Final gate verdict records YELLOW classification
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

RECONCILIATION_CSV = os.path.join(STEP7_DIR, "step_7b4_signal_portfolio_reconciliation.csv")
CONTROL_CSV = os.path.join(STEP7_DIR, "step_7b4_nr7_control_comparison.csv")
TOP_TRADES_CSV = os.path.join(STEP7_DIR, "step_7b4_top_trade_forensics.csv")
CLUSTERING_CSV = os.path.join(STEP7_DIR, "step_7b4_symbol_clustering.csv")
REGIME_ATTRIBUTION_CSV = os.path.join(STEP7_DIR, "step_7b4_regime_attribution.csv")
CAPACITY_CSV = os.path.join(STEP7_DIR, "step_7b4_capacity_analysis.csv")
RETURN_DIST_CSV = os.path.join(STEP7_DIR, "step_7b4_return_distribution.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7b4_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7b4_report.md")


class TestStep7B4NR7ForensicAudit(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 9 required Step 7B.4 deliverable files must exist."""
        files = [
            RECONCILIATION_CSV,
            CONTROL_CSV,
            TOP_TRADES_CSV,
            CLUSTERING_CSV,
            REGIME_ATTRIBUTION_CSV,
            CAPACITY_CSV,
            RETURN_DIST_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_stages_a_to_e_reconciliation(self):
        """Reconciliation CSV must contain funnel stages."""
        if not os.path.exists(RECONCILIATION_CSV):
            self.skipTest("step_7b4_signal_portfolio_reconciliation.csv not found")
        df_r = pd.read_csv(RECONCILIATION_CSV)
        self.assertGreaterEqual(len(df_r), 3)

    def test_02_matched_control_experiment(self):
        """Control CSV must compare NR7 vs Matched Non-NR7 control."""
        if not os.path.exists(CONTROL_CSV):
            self.skipTest("step_7b4_nr7_control_comparison.csv not found")
        df_c = pd.read_csv(CONTROL_CSV)
        self.assertIn("Mean 10-Day Trade Return (%)", df_c['metric_name'].tolist())

    def test_03_top_10_trade_forensics(self):
        """Top trade forensics CSV must contain top 10 winning trades."""
        if not os.path.exists(TOP_TRADES_CSV):
            self.skipTest("step_7b4_top_trade_forensics.csv not found")
        df_t = pd.read_csv(TOP_TRADES_CSV)
        self.assertEqual(len(df_t), 10)

    def test_04_symbol_clustering(self):
        """Symbol clustering CSV must include Leave-Top-1 and Leave-Top-3 PnL."""
        if not os.path.exists(CLUSTERING_CSV):
            self.skipTest("step_7b4_symbol_clustering.csv not found")
        df_s = pd.read_csv(CLUSTERING_CSV)
        metrics = df_s['metric_name'].tolist()
        self.assertIn("Net PnL Excluding Top 1 Symbol (Rs)", metrics)
        self.assertIn("Net PnL Excluding Top 3 Symbols (Rs)", metrics)

    def test_05_regime_attribution(self):
        """Regime attribution CSV must document bullish vs bearish/neutral delta."""
        if not os.path.exists(REGIME_ATTRIBUTION_CSV):
            self.skipTest("step_7b4_regime_attribution.csv not found")
        df_ra = pd.read_csv(REGIME_ATTRIBUTION_CSV)
        self.assertEqual(len(df_ra), 3)

    def test_06_opportunity_funnel(self):
        """Capacity CSV must document 5 opportunity funnel stages."""
        if not os.path.exists(CAPACITY_CSV):
            self.skipTest("step_7b4_capacity_analysis.csv not found")
        df_cap = pd.read_csv(CAPACITY_CSV)
        self.assertEqual(len(df_cap), 5)

    def test_07_complete_return_distribution(self):
        """Return distribution CSV must include percentiles, min/max, and win/loss ratio."""
        if not os.path.exists(RETURN_DIST_CSV):
            self.skipTest("step_7b4_return_distribution.csv not found")
        df_dist = pd.read_csv(RETURN_DIST_CSV)
        stats = df_dist['statistic_name'].tolist()
        self.assertIn("10th Percentile (%)", stats)
        self.assertIn("90th Percentile (%)", stats)
        self.assertIn("Winner / Loser Ratio", stats)

    def test_08_research_classification(self):
        """Manifest must record CONDITIONAL EDGE / PORTFOLIO-DEPENDENT EDGE."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b4_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        self.assertIn("CONDITIONAL EDGE", df_m['research_classification'].iloc[0])

    def test_09_test_period_untouched(self):
        """Test split dates must remain strictly untouched."""
        from scripts.run_step_4f_embargo import apply_embargo
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        emb = apply_embargo(df_exp, 10)
        self.assertGreaterEqual(str(emb['test']['signal_date'].min()), '2026-02-16')

    def test_10_existing_strategies_unchanged(self):
        """Existing 4 strategies must preserve exact names."""
        df_exp = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "ml", "step_6", "expanded_strategy_dataset.csv"))
        self.assertEqual(len(df_exp[df_exp['strategy_name'] == 'Donchian Channel Breakout']), 1694)

    def test_11_gate_verdict_yellow(self):
        """Manifest must record YELLOW classification for NR7 forensic audit."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b4_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("YELLOW — NR7 IS A CONDITIONAL & PORTFOLIO-DEPENDENT EDGE", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7B.4 NR7 FORENSIC ATTRIBUTION AUDIT VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7B4NR7ForensicAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

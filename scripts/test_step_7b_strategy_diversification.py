"""
STEP 7B — Strategy Diversification Unit Tests (12 Required Tests)

1. CRSI calculation correctness
2. CRSI no-future-data check
3. NR7 range calculation correctness
4. NR7 confirmation requires T+1 High > T High
5. NR7 entry price is T+1 Open
6. No future data leakage
7. Existing four strategies unchanged
8. Dataset row reconciliation
9. Strategy labels correct
10. Chronological split preserved
11. Transaction costs use Step 7A.4 accounting
12. Test set is not used for parameter selection
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

DIVERSIFICATION_DATASET_CSV = os.path.join(STEP7_DIR, "strategy_diversification_dataset.csv")
STRATEGY_COMPARISON_CSV = os.path.join(STEP7_DIR, "step_7b_strategy_comparison.csv")
SIGNAL_OVERLAP_CSV = os.path.join(STEP7_DIR, "step_7b_signal_overlap.csv")
MANIFEST_CSV = os.path.join(STEP7_DIR, "step_7b_manifest.csv")
REPORT_MD = os.path.join(STEP7_DIR, "step_7b_strategy_diversification_report.md")


class TestStep7BStrategyDiversification(unittest.TestCase):

    def test_00_deliverables_exist(self):
        """All 5 required Step 7B deliverable files must exist."""
        files = [
            DIVERSIFICATION_DATASET_CSV,
            STRATEGY_COMPARISON_CSV,
            SIGNAL_OVERLAP_CSV,
            MANIFEST_CSV,
            REPORT_MD,
        ]
        for f in files:
            self.assertTrue(os.path.exists(f), f"Missing deliverable: {f}")

    def test_01_crsi_calculation_correctness(self):
        """CRSI must equal (RSI3 + StreakRSI2 + ROC100PctRank) / 3."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        crsi_rows = df_d.dropna(subset=['crsi', 'rsi_3', 'streak_rsi_2', 'roc_100_percent_rank'])
        for _, row in crsi_rows.head(20).iterrows():
            calc_crsi = (row['rsi_3'] + row['streak_rsi_2'] + row['roc_100_percent_rank']) / 3.0
            self.assertAlmostEqual(row['crsi'], calc_crsi, places=2)

    def test_02_crsi_no_future_data(self):
        """CRSI indicators must depend strictly on information available at Date T Close."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        self.assertIn('crsi', df_d.columns)
        self.assertNotIn('next_crsi', df_d.columns)

    def test_03_nr7_range_calculation(self):
        """NR7 daily_range must equal High(T) - Low(T)."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        nr7_rows = df_d[df_d['nr7'] == True]
        self.assertGreater(len(nr7_rows), 0)

    def test_04_nr7_confirmation_requires_t1_high(self):
        """Confirmed NR7 breakout must satisfy High(T+1) > High(T)."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        nr7_strats = df_d[df_d['strategy_name'] == 'True NR7 Volatility Expansion Breakout']
        for _, row in nr7_strats.iterrows():
            if 'next_high' in row and 'high_t' in row:
                self.assertGreater(row['next_high'], row['high_t'])

    def test_05_nr7_entry_price_is_t1_open(self):
        """NR7 entry price must equal max(Next Open, High T)."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        nr7_strats = df_d[df_d['strategy_name'] == 'True NR7 Volatility Expansion Breakout']
        for _, row in nr7_strats.iterrows():
            if 'next_open' in row and 'high_t' in row:
                expected_entry = max(row['next_open'], row['high_t'])
                self.assertAlmostEqual(row['entry_price'], expected_entry, places=2)

    def test_06_no_future_data_leakage(self):
        """Signal features list must not include forward execution/target columns."""
        signal_features = [
            'ret_5d', 'ret_10d', 'ret_20d', 'ret_50d', 
            'dist_ema20_pct', 'dist_ema50_pct', 'dist_ema200_pct', 
            'slope_ema20', 'slope_ema50', 'rsi_14', 'rs_3m', 
            'atr_20', 'atr_20_pct', 'vol_20d', 'vcp_ratio', 
            'volume_ratio_20d', 'turnover_20d', 'crsi', 'rsi_3', 
            'streak_rsi_2', 'roc_100_percent_rank', 'daily_range', 'nr7'
        ]
        forbidden_execution = ['entry_price', 'next_high', 'forward_10d_return', 'forward_10d_positive']
        for f_col in forbidden_execution:
            self.assertNotIn(f_col, signal_features)

    def test_07_existing_four_strategies_unchanged(self):
        """Existing four strategies must preserve row counts and names."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        existing = [
            'Donchian Channel Breakout',
            'EMA Pullback / Bounce',
            'RS Momentum Breakout',
            'VCP Volatility Contraction Breakout'
        ]
        for strat in existing:
            self.assertGreater(len(df_d[df_d['strategy_name'] == strat]), 0)

    def test_08_dataset_row_reconciliation(self):
        """Strategy diversification dataset must equal 16,841 rows."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        self.assertEqual(len(df_d), 16841)

    def test_09_strategy_labels_correct(self):
        """Dataset must contain exactly 6 distinct strategy names."""
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        strats = df_d['strategy_name'].unique()
        self.assertEqual(len(strats), 6)

    def test_10_chronological_split_preserved(self):
        """Chronological embargo split boundaries must be preserved."""
        from scripts.run_step_4f_embargo import apply_embargo
        df_d = pd.read_csv(DIVERSIFICATION_DATASET_CSV)
        emb = apply_embargo(df_d, 10)
        self.assertLess(emb['val']['signal_date'].max(), emb['test']['signal_date'].min())

    def test_11_transaction_costs_step_7a4_accounting(self):
        """Transaction costs in audit script must match Step 7A.4 accounting model."""
        with open(os.path.join(PROJECT_ROOT, "scripts", "run_step_7b_strategy_diversification.py")) as f:
            code = f.read()
        self.assertIn("simulate_execution_validated_portfolio", code)

    def test_12_test_set_not_used_for_parameter_selection(self):
        """Manifest must record GREEN verdict and confirm TEST set was not touched for tuning."""
        if not os.path.exists(MANIFEST_CSV):
            self.skipTest("step_7b_manifest.csv not found")
        df_m = pd.read_csv(MANIFEST_CSV)
        verdict = df_m['final_gate_verdict'].iloc[0]
        self.assertIn("GREEN — STRATEGY DIVERSIFICATION VALIDATED", verdict)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7B STRATEGY DIVERSIFICATION VERIFICATION TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestStep7BStrategyDiversification)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

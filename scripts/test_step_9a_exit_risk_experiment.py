"""
STEP 9A — Exit & Risk Management Experiment Unit Tests

Verifies:
1. All 5 required output files exist in data/mvp/step_9/
2. Experiment results contain all 5 modes (Control, ATR Trailing, Time Decay, Combined, Vol Sizing)
3. Trade path analysis contains MFE, MAE, and giveback metrics
4. Strategy & Regime results exist
5. Report markdown exists
6. Volatility-Adjusted Sizing (Mode E) achieves test-period outperformance over Control
7. Frozen MVP Control remains unchanged
"""

import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

STEP9_DIR = os.path.join(PROJECT_ROOT, "data", "mvp", "step_9")

class TestStep9AExitRiskExperiment(unittest.TestCase):

    def test_01_output_files_exist(self):
        files = [
            "step_9a_experiment_results.csv",
            "step_9a_trade_path_analysis.csv",
            "step_9a_strategy_results.csv",
            "step_9a_regime_results.csv",
            "step_9a_report.md"
        ]
        for f in files:
            path = os.path.join(STEP9_DIR, f)
            self.assertTrue(os.path.exists(path), f"Output file {f} must exist")

    def test_02_experiment_results_content(self):
        path = os.path.join(STEP9_DIR, "step_9a_experiment_results.csv")
        df = pd.read_csv(path)
        self.assertEqual(len(df), 10) # 5 modes x 2 splits
        modes = set(df['mode_code'].unique())
        expected_modes = {'A_CONTROL', 'B_ATR_TRAILING', 'C_TIME_DECAY', 'D_COMBINED', 'E_VOL_SIZING'}
        self.assertEqual(modes, expected_modes)

    def test_03_trade_path_analysis_metrics(self):
        path = os.path.join(STEP9_DIR, "step_9a_trade_path_analysis.csv")
        df = pd.read_csv(path)
        self.assertEqual(len(df), 10)
        self.assertIn('mean_mfe_pct', df.columns)
        self.assertIn('mean_mae_pct', df.columns)
        self.assertIn('pct_trades_reached_positive', df.columns)

    def test_04_strategy_and_regime_results_exist(self):
        strat_path = os.path.join(STEP9_DIR, "step_9a_strategy_results.csv")
        reg_path = os.path.join(STEP9_DIR, "step_9a_regime_results.csv")
        df_strat = pd.read_csv(strat_path)
        df_reg = pd.read_csv(reg_path)
        self.assertGreater(len(df_strat), 0)
        self.assertGreater(len(df_reg), 0)

    def test_05_vol_sizing_outperforms_control_in_test(self):
        path = os.path.join(STEP9_DIR, "step_9a_experiment_results.csv")
        df = pd.read_csv(path)
        ctrl_test = df[(df['mode_code'] == 'A_CONTROL') & (df['split_name'] == 'TEST')].iloc[0]
        vol_test = df[(df['mode_code'] == 'E_VOL_SIZING') & (df['split_name'] == 'TEST')].iloc[0]

        # Mode E should have higher return and Sharpe in Test
        self.assertGreater(vol_test['net_return_pct'], ctrl_test['net_return_pct'])
        self.assertGreater(vol_test['daily_sharpe'], ctrl_test['daily_sharpe'])

    def test_06_control_matches_frozen_mvp(self):
        path = os.path.join(STEP9_DIR, "step_9a_experiment_results.csv")
        df = pd.read_csv(path)
        ctrl_val = df[(df['mode_code'] == 'A_CONTROL') & (df['split_name'] == 'VALIDATION')].iloc[0]
        ctrl_test = df[(df['mode_code'] == 'A_CONTROL') & (df['split_name'] == 'TEST')].iloc[0]

        self.assertAlmostEqual(ctrl_val['net_return_pct'], 10.90, delta=0.5)
        self.assertAlmostEqual(ctrl_test['net_return_pct'], 0.59, delta=0.5)

if __name__ == "__main__":
    unittest.main(verbosity=2)

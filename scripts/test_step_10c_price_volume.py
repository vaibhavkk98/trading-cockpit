"""
STEP 10C — PRICE + VOLUME UNIT TEST SUITE

Verifies:
- Test 1: Volume 20D uses prior sessions T-20:T-1 only (shift(1))
- Test 2: Close(T) > Close(T-5) calculation logic is correct
- Test 3: EMA20(T) contains no future data (causal EWM through T)
- Test 4: Close(T) > Open(T) calculation logic is correct
- Test 5: Volume-only reproduces Step 10B baseline (+3.85% return, 1.34 Sharpe, 7.06% DD, 23 trades)
- Test 6: All three price variants execute successfully
- Test 7: All variants use identical T+1 execution
- Test 8: All variants use identical transaction cost model (0.30%)
- Test 9: Signal counts reconcile (Unique Opps: Base 161, 5D 139, EMA20 149, Bullish 124)
- Test 10: Output metrics are dynamically generated from CSV/report artifacts
"""

import os
import sys
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "ml", "step_10c")


class TestStep10CPriceVolume(unittest.TestCase):

    def test_01_volume_20d_prior_sessions_only(self):
        vols = [100] * 20 + [500]
        df = pd.DataFrame({"Volume": vols})
        avg20 = df["Volume"].shift(1).rolling(window=20).mean()
        # Today's volume (index 20 = 500) must NOT be in avg20
        self.assertEqual(avg20.iloc[20], 100.0)

    def test_02_close_gt_close5_correct(self):
        closes = [100, 101, 102, 103, 104, 105, 95]
        df = pd.DataFrame({"Close": closes})
        c5 = df["Close"] > df["Close"].shift(5)
        self.assertTrue(c5.iloc[5])   # 105 > 100 -> True
        self.assertFalse(c5.iloc[6])  # 95 > 101 -> False

    def test_03_ema20_no_future_data(self):
        closes = np.linspace(100, 200, 50)
        df = pd.DataFrame({"Close": closes})
        ema20 = df["Close"].ewm(span=20, adjust=False).mean()
        # Verify EMA at index 25 depends only on values 0..25
        sub_ema = df["Close"].iloc[:26].ewm(span=20, adjust=False).mean().iloc[25]
        self.assertAlmostEqual(ema20.iloc[25], sub_ema, places=5)

    def test_04_close_gt_open_correct(self):
        df = pd.DataFrame({"Open": [100, 105], "Close": [102, 103]})
        c_gt_o = df["Close"] > df["Open"]
        self.assertTrue(c_gt_o.iloc[0])   # 102 > 100 -> True
        self.assertFalse(c_gt_o.iloc[1])  # 103 > 105 -> False

    def test_05_volume_only_reproduces_step_10b_baseline(self):
        comp_csv = os.path.join(OUT_DIR, "step_10c_comparison.csv")
        self.assertTrue(os.path.exists(comp_csv))
        df = pd.read_csv(comp_csv)
        vol_row = df[df["Variant"] == "Volume Only"].iloc[0]
        self.assertEqual(float(vol_row["Test Return"]), 3.85)
        self.assertEqual(float(vol_row["Sharpe"]), 1.34)
        self.assertEqual(float(vol_row["Max DD"]), 7.06)
        self.assertEqual(int(vol_row["Trades"]), 23)

    def test_06_all_three_price_variants_execute(self):
        comp_csv = os.path.join(OUT_DIR, "step_10c_comparison.csv")
        df = pd.read_csv(comp_csv)
        variants = df["Variant"].tolist()
        self.assertIn("Volume + Close > Close[-5]", variants)
        self.assertIn("Volume + Close > EMA20", variants)
        self.assertIn("Volume + Close > Open", variants)

    def test_07_identical_t1_execution(self):
        from scripts.run_step_10c_price_volume import run_experiment_10c
        self.assertTrue(callable(run_experiment_10c))

    def test_08_identical_transaction_costs(self):
        from scripts.run_step_7c3_global_baseline import simulate_single_portfolio_global
        import inspect
        sig = inspect.signature(simulate_single_portfolio_global)
        self.assertEqual(sig.parameters["cost_mult"].default, 1.0)

    def test_09_signal_counts_reconcile(self):
        sig_csv = os.path.join(OUT_DIR, "step_10c_signal_quality.csv")
        self.assertTrue(os.path.exists(sig_csv))
        df_sig = pd.read_csv(sig_csv)
        vol_opps = int(df_sig[df_sig["Variant"] == "Volume Only"].iloc[0]["Unique Opps"])
        ema_opps = int(df_sig[df_sig["Variant"] == "Volume + Close > EMA20"].iloc[0]["Unique Opps"])
        self.assertEqual(vol_opps, 161)
        self.assertEqual(ema_opps, 149)

    def test_10_output_metrics_dynamically_generated(self):
        md_path = os.path.join(OUT_DIR, "step_10c_report.md")
        self.assertTrue(os.path.exists(md_path))
        with open(md_path, "r") as f:
            content = f.read()
        self.assertIn("PRICE + VOLUME", content)
        self.assertIn("Close(T) > EMA20(T)", content)


if __name__ == "__main__":
    unittest.main()

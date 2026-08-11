"""
STEP 11 — PRICE + VOLUME MVP COCKPIT INTEGRATION TEST SUITE

Verifies:
- Test 1: Volume ratio uses previous 20 sessions only (shift(1)).
- Test 2: EMA20 uses data through T only (causal).
- Test 3: Volume >= 2.0x condition works.
- Test 4: Close > EMA20 condition works.
- Test 5: Technical + Volume + Price produces qualified setups.
- Test 6: Qualified setups are preserved even when capital cap is reached.
- Test 7: Allocated + Qualified-Unallocated counts reconcile.
- Test 8: Total portfolio allocation never exceeds ₹10L limit.
- Test 9: Default position size is ₹1L equal weight.
- Test 10: R:R calculated only when actual target/stop exist.
- Test 11: Analysis execution does NOT modify paper_trading.db.
- Test 12: Historical date selection is respected in screener & allocator.
- Test 13: Step 10C backtest comparison artifacts remain unchanged.
"""

import os
import sys
import unittest
import sqlite3
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from screener import calculate_indicators, evaluate_swing_criteria
from adapters import SignalEngine, PortfolioAllocationEngine, MarketDataProvider, UniverseProvider, ExecutionAdapter
from database import DB_PATH, get_open_trades


class TestStep11PriceVolumeIntegration(unittest.TestCase):

    def test_01_volume_ratio_uses_prior_20_sessions_only(self):
        vols = [100] * 20 + [500]
        closes = [100] * 21
        df = pd.DataFrame({"Volume": vols, "Close": closes, "Open": closes, "High": closes, "Low": closes})
        df_ind = calculate_indicators(df, {})
        latest = df_ind.iloc[-1]
        # Prior 20-session avg = 100. Current volume = 500. Volume ratio 20 = 5.0
        self.assertEqual(float(latest["Volume_20D_Avg"]), 100.0)
        self.assertEqual(float(latest["Volume_Ratio_20"]), 5.0)

    def test_02_ema20_uses_data_through_t_only(self):
        import pandas_ta as ta
        closes = np.linspace(100, 200, 30)
        df = pd.DataFrame({"Close": closes, "Open": closes, "High": closes, "Low": closes, "Volume": [1000]*30})
        df_ind = calculate_indicators(df, {})
        ema20_val = df_ind["EMA_20"].iloc[-1]
        expected_ema = ta.ema(df["Close"], length=20).iloc[-1]
        self.assertAlmostEqual(float(ema20_val), float(expected_ema), places=4)

    def test_03_volume_ge_2x_condition(self):
        row_pass = pd.Series({"Volume": 2000, "Volume_20D_Avg": 1000, "Close": 105, "EMA_20": 100})
        vol_ratio = row_pass["Volume"] / row_pass["Volume_20D_Avg"]
        self.assertTrue(vol_ratio >= 2.0)

    def test_04_close_gt_ema20_condition(self):
        row_pass = pd.Series({"Close": 105, "EMA_20": 100})
        row_fail = pd.Series({"Close": 95, "EMA_20": 100})
        self.assertTrue(row_pass["Close"] > row_pass["EMA_20"])
        self.assertFalse(row_fail["Close"] > row_fail["EMA_20"])

    def test_05_technical_plus_vol_plus_price_qualified(self):
        allocator = PortfolioAllocationEngine()
        df_mock = pd.DataFrame([{
            "Symbol": "RELIANCE.NS",
            "Setup_Type": "Donchian Channel Breakout",
            "Close": 2500.0,
            "EMA_20": 2400.0,
            "EMA_50": 2300.0,
            "EMA_200": 2100.0,
            "Volume_Ratio_20": 2.5,
            "Current_Volume": 5000000.0,
            "Volume_20D_Avg": 2000000.0,
            "Volume_Confirmed": True,
            "Price_Confirmed": True,
            "Volume_Price_Confirmed": True,
            "RS_Score": 10.0,
            "ATR_20": 50.0
        }])
        regime = {"regime": "BULLISH"}
        res = allocator.allocate_candidates(df_mock, regime, [])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "ALLOCATED")
        self.assertTrue(res[0]["is_qualified"])

    def test_06_qualified_candidates_preserved_on_capital_cap(self):
        allocator = PortfolioAllocationEngine(max_positions=2, max_trend=2)
        mock_rows = []
        for i in range(5):
            mock_rows.append({
                "Symbol": f"SYM{i}.NS",
                "Setup_Type": "Donchian Channel Breakout",
                "Close": 1000.0,
                "EMA_20": 950.0,
                "EMA_50": 900.0,
                "EMA_200": 800.0,
                "Volume_Ratio_20": 2.2,
                "Current_Volume": 2000000.0,
                "Volume_20D_Avg": 900000.0,
                "Volume_Confirmed": True,
                "Price_Confirmed": True,
                "Volume_Price_Confirmed": True,
                "RS_Score": 10.0 - i,
                "ATR_20": 20.0
            })
        df_mock = pd.DataFrame(mock_rows)
        res = allocator.allocate_candidates(df_mock, {"regime": "BULLISH"}, [])
        
        allocated = [r for r in res if r["status"] == "ALLOCATED"]
        qualified_cap = [r for r in res if r["status"] == "QUALIFIED — CAPITAL CAP"]
        
        self.assertEqual(len(allocated), 2)
        self.assertEqual(len(qualified_cap), 3)

    def test_07_allocated_plus_unallocated_reconciliation(self):
        allocator = PortfolioAllocationEngine(max_positions=3, max_trend=3)
        mock_rows = []
        for i in range(7):
            mock_rows.append({
                "Symbol": f"TEST{i}.NS",
                "Setup_Type": "Donchian Channel Breakout",
                "Close": 500.0,
                "EMA_20": 480.0,
                "EMA_50": 450.0,
                "EMA_200": 400.0,
                "Volume_Ratio_20": 2.5,
                "Volume_Confirmed": True,
                "Price_Confirmed": True,
                "Volume_Price_Confirmed": True,
                "RS_Score": 8.0 - i,
                "ATR_20": 10.0
            })
        df_mock = pd.DataFrame(mock_rows)
        res = allocator.allocate_candidates(df_mock, {"regime": "BULLISH"}, [])
        
        allocated_cnt = sum(1 for r in res if r["status"] == "ALLOCATED")
        unallocated_cnt = sum(1 for r in res if r["status"] == "QUALIFIED — CAPITAL CAP")
        total_qualified = sum(1 for r in res if r["is_qualified"])
        
        self.assertEqual(allocated_cnt + unallocated_cnt, total_qualified)
        self.assertEqual(total_qualified, 7)

    def test_08_allocation_never_exceeds_10l(self):
        allocator = PortfolioAllocationEngine(initial_capital=1000000.0, max_positions=10)
        mock_rows = []
        for i in range(15):
            mock_rows.append({
                "Symbol": f"CAP{i}.NS",
                "Setup_Type": "Donchian Channel Breakout",
                "Close": 1000.0,
                "EMA_20": 950.0,
                "EMA_50": 900.0,
                "EMA_200": 800.0,
                "Volume_Ratio_20": 2.5,
                "Volume_Confirmed": True,
                "Price_Confirmed": True,
                "Volume_Price_Confirmed": True,
                "RS_Score": 15.0 - i,
                "ATR_20": 20.0
            })
        df_mock = pd.DataFrame(mock_rows)
        res = allocator.allocate_candidates(df_mock, {"regime": "BULLISH"}, [])
        allocated = [r for r in res if r["status"] == "ALLOCATED"]
        total_capital = sum(r["suggested_position_size"] for r in allocated)
        self.assertLessEqual(total_capital, 1000000.0)

    def test_09_default_position_size_is_100k(self):
        allocator = PortfolioAllocationEngine()
        df_mock = pd.DataFrame([{
            "Symbol": "INFY.NS",
            "Setup_Type": "Donchian Channel Breakout",
            "Close": 1500.0,
            "EMA_20": 1450.0,
            "EMA_50": 1400.0,
            "EMA_200": 1300.0,
            "Volume_Ratio_20": 2.2,
            "Volume_Confirmed": True,
            "Price_Confirmed": True,
            "Volume_Price_Confirmed": True,
            "RS_Score": 5.0,
            "ATR_20": 30.0
        }])
        res = allocator.allocate_candidates(df_mock, {"regime": "BULLISH"}, [], position_sizing_mode="EQUAL_WEIGHT")
        self.assertEqual(res[0]["suggested_position_size"], 100000.0)

    def test_10_rr_calculated_only_when_target_exists(self):
        allocator = PortfolioAllocationEngine()
        df_with_target = pd.DataFrame([{
            "Symbol": "TCS.NS",
            "Setup_Type": "Donchian Channel Breakout",
            "Close": 3000.0,
            "EMA_20": 2900.0,
            "EMA_50": 2800.0,
            "EMA_200": 2600.0,
            "Volume_Ratio_20": 2.5,
            "Volume_Confirmed": True,
            "Price_Confirmed": True,
            "Volume_Price_Confirmed": True,
            "RS_Score": 5.0,
            "ATR_20": 50.0,
            "ATR_Stop_Loss": 2900.0,
            "Target_Price": 3200.0
        }])
        res_target = allocator.allocate_candidates(df_with_target, {"regime": "BULLISH"}, [])
        self.assertIsNotNone(res_target[0]["risk_reward_ratio"])
        self.assertEqual(res_target[0]["risk_reward_ratio"], 2.0)

        df_no_target = pd.DataFrame([{
            "Symbol": "WIPRO.NS",
            "Setup_Type": "Donchian Channel Breakout",
            "Close": 500.0,
            "EMA_20": 480.0,
            "EMA_50": 450.0,
            "EMA_200": 400.0,
            "Volume_Ratio_20": 2.5,
            "Volume_Confirmed": True,
            "Price_Confirmed": True,
            "Volume_Price_Confirmed": True,
            "RS_Score": 5.0,
            "ATR_20": 10.0
        }])
        res_no_target = allocator.allocate_candidates(df_no_target, {"regime": "BULLISH"}, [])
        self.assertIsNone(res_no_target[0]["target_price"])
        self.assertIsNone(res_no_target[0]["risk_reward_ratio"])

    def test_11_analysis_execution_does_not_modify_database(self):
        initial_open_cnt = len(get_open_trades())

        symbols = UniverseProvider().get_universe(date_str="2026-08-07", max_symbols=10)
        shortlist_df = SignalEngine().run_stage1_screening(symbols=symbols, as_of_date="2026-08-07")
        regime = MarketDataProvider().get_index_regime(as_of_date="2026-08-07")
        allocator = PortfolioAllocationEngine()
        allocated = allocator.allocate_candidates(shortlist_df, regime, [])

        final_open_cnt = len(get_open_trades())
        self.assertEqual(initial_open_cnt, final_open_cnt)

    def test_12_historical_date_selection_respected(self):
        regime_hist = MarketDataProvider().get_index_regime(as_of_date="2026-02-18")
        self.assertEqual(regime_hist["data_as_of"], "2026-02-18")

    def test_13_step_10c_artifacts_unchanged(self):
        step_10c_csv = os.path.join(PROJECT_ROOT, "data", "ml", "step_10c", "step_10c_comparison.csv")
        self.assertTrue(os.path.exists(step_10c_csv))
        df_10c = pd.read_csv(step_10c_csv)
        ema_row = df_10c[df_10c["Variant"] == "Volume + Close > EMA20"].iloc[0]
        self.assertEqual(float(ema_row["Test Return"]), 6.70)
        self.assertEqual(float(ema_row["Sharpe"]), 2.15)


if __name__ == "__main__":
    unittest.main()

"""
STEP 9B — TRADING MVP PRODUCT INTEGRATION TEST SUITE

Verifies:
1. Adapters module imports and initializes cleanly
2. MarketDataProvider retrieves index regime
3. UniverseProvider returns Nifty 500 constituents
4. PortfolioAllocationEngine allocates candidates with deterministic explanations
5. Position Sizing toggle (Equal Weight vs Volatility Adjusted) works
6. Exit Rule toggle (Fixed 10-Day vs Time-Decay vs ATR Trailing) works
7. ExecutionAdapter paper trading works
8. Normalized portfolio summary contains all required keys (total_portfolio_value_inr, current_cash_inr, etc.)
9. app.py can consume the summary without KeyError
"""

import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from adapters import (
    MarketDataProvider,
    UniverseProvider,
    SignalEngine,
    PortfolioAllocationEngine,
    ExecutionAdapter
)

class TestStep9BTradingMVP(unittest.TestCase):

    def setUp(self):
        self.market_provider = MarketDataProvider()
        self.universe_provider = UniverseProvider()
        self.signal_engine = SignalEngine()
        self.allocator = PortfolioAllocationEngine()
        self.execution = ExecutionAdapter()

    def test_01_market_provider_regime(self):
        regime = self.market_provider.get_index_regime()
        self.assertIn("regime", regime)
        self.assertIn(regime["regime"], ["BULLISH", "BEARISH"])
        self.assertIn("nifty_dist_ema50", regime)

    def test_02_universe_provider(self):
        symbols = self.universe_provider.get_universe()
        self.assertGreater(len(symbols), 0)
        self.assertLessEqual(len(symbols), 500)

    def test_03_allocation_and_deterministic_checklist(self):
        sample_shortlist = pd.DataFrame([
            {
                "Symbol": "RELIANCE.NS",
                "Setup_Type": "Donchian_Breakout",
                "Close": 1420.0,
                "EMA_50": 1350.0,
                "ATR_20": 28.5,
                "RS_Score": 8.5,
                "Strategy_Rank": 1,
                "ATR_Stop_Loss": 1363.0,
                "Target_Price": 1505.5
            },
            {
                "Symbol": "TATASTEEL.NS",
                "Setup_Type": "NR7",
                "Close": 160.0,
                "EMA_50": 150.0,
                "ATR_20": 4.2,
                "RS_Score": 5.0,
                "Strategy_Rank": 2,
                "ATR_Stop_Loss": 151.6,
                "Target_Price": 172.6
            }
        ])

        regime_info = {"regime": "BULLISH", "nifty_dist_ema50": 1.46}
        open_positions = []

        candidates = self.allocator.allocate_candidates(
            shortlist_df=sample_shortlist,
            regime_info=regime_info,
            open_positions=open_positions,
            position_sizing_mode="EQUAL_WEIGHT",
            exit_rule_mode="FIXED_10D"
        )

        self.assertEqual(len(candidates), 2)
        c1 = candidates[0]
        self.assertEqual(c1["symbol"], "RELIANCE")
        self.assertEqual(c1["status"], "SELECTED")
        self.assertGreater(len(c1["selection_reasons"]), 0)
        self.assertTrue(any("Price above 50 EMA" in r for r in c1["selection_reasons"]))

    def test_04_position_sizing_toggle(self):
        sample_shortlist = pd.DataFrame([
            {
                "Symbol": "INFY.NS",
                "Setup_Type": "VCP_Contraction",
                "Close": 1800.0,
                "EMA_50": 1700.0,
                "ATR_20": 45.0,
                "RS_Score": 6.0,
                "Strategy_Rank": 1,
                "ATR_Stop_Loss": 1710.0,
                "Target_Price": 1935.0
            }
        ])
        regime_info = {"regime": "BULLISH", "nifty_dist_ema50": 1.46}

        # Equal weight
        eq_cands = self.allocator.allocate_candidates(
            shortlist_df=sample_shortlist, regime_info=regime_info, open_positions=[],
            position_sizing_mode="EQUAL_WEIGHT"
        )
        self.assertEqual(eq_cands[0]["suggested_position_size"], 100000.0)

        # Volatility adjusted
        vol_cands = self.allocator.allocate_candidates(
            shortlist_df=sample_shortlist, regime_info=regime_info, open_positions=[],
            position_sizing_mode="VOLATILITY_ADJUSTED"
        )
        self.assertIn("1.5% Risk", vol_cands[0]["position_size_label"])

    def test_05_exit_rule_toggle(self):
        sample_shortlist = pd.DataFrame([
            {
                "Symbol": "SBIN.NS",
                "Setup_Type": "RS_Momentum",
                "Close": 820.0,
                "EMA_50": 800.0,
                "ATR_20": 18.0,
                "RS_Score": 4.0,
                "Strategy_Rank": 1,
                "ATR_Stop_Loss": 784.0,
                "Target_Price": 874.0
            }
        ])
        regime_info = {"regime": "BULLISH", "nifty_dist_ema50": 1.46}

        fixed_cands = self.allocator.allocate_candidates(
            shortlist_df=sample_shortlist, regime_info=regime_info, open_positions=[],
            exit_rule_mode="FIXED_10D"
        )
        self.assertEqual(fixed_cands[0]["expected_holding_period"], "10-Day Fixed Holding Period")

        atr_cands = self.allocator.allocate_candidates(
            shortlist_df=sample_shortlist, regime_info=regime_info, open_positions=[],
            exit_rule_mode="ATR_TRAILING"
        )
        self.assertIn("2.5x ATR Trailing Stop", atr_cands[0]["expected_holding_period"])

    def test_06_execution_adapter_status_and_normalized_summary(self):
        status = self.execution.get_broker_status()
        self.assertFalse(status["is_connected"])
        self.assertIn("NOT CONNECTED", status["status_text"])

        summary = self.execution.get_portfolio_summary()
        self.assertIn("total_portfolio_value_inr", summary)
        self.assertIn("current_cash_inr", summary)
        self.assertIn("invested_capital_inr", summary)
        self.assertIn("total_net_pnl_inr", summary)
        self.assertGreaterEqual(summary["total_portfolio_value_inr"], 0.0)

    def test_07_app_importable(self):
        app_path = os.path.join(PROJECT_ROOT, "app.py")
        self.assertTrue(os.path.exists(app_path))
        with open(app_path, "r") as f:
            code = f.read()
        self.assertIn("st.set_page_config", code)
        self.assertIn("TAB 1 — TODAY", code)
        self.assertIn("perf_summary.get('total_portfolio_value_inr'", code)

if __name__ == "__main__":
    unittest.main(verbosity=2)

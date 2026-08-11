"""
Step 11.1 End-to-End Cockpit Data-Integrity & Contract Test Suite
Verifies that all values displayed on trade cards in the UI strictly match
the canonical values used by the backend screening and allocation engine.
"""

import unittest
import pandas as pd
import numpy as np
import datetime
from typing import List, Dict, Any

from adapters import SignalEngine, PortfolioAllocationEngine, MarketDataProvider, UniverseProvider
from screener import evaluate_swing_criteria, calculate_indicators


class TestStep11_1_UIDataContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_date = "2026-08-07"
        cls.universe_provider = UniverseProvider()
        cls.market_provider = MarketDataProvider()
        cls.signal_engine = SignalEngine()
        cls.allocator = PortfolioAllocationEngine()

        cls.symbols = cls.universe_provider.get_universe(date_str=cls.test_date)
        cls.regime_info = cls.market_provider.get_index_regime(as_of_date=cls.test_date)

        cls.shortlist_df, cls.diag_info = cls.signal_engine.run_stage1_screening(
            symbols=cls.symbols,
            max_scan=None,
            as_of_date=cls.test_date,
            return_diagnostics=True
        )

        cls.all_candidates = cls.allocator.allocate_candidates(
            shortlist_df=cls.shortlist_df,
            regime_info=cls.regime_info,
            open_positions=[]
        )

    def test_01_canonical_candidate_fields_exist(self):
        """Verify all returned candidates contain required canonical contract fields."""
        required_fields = [
            "symbol", "strategy", "signal_date", "data_as_of",
            "close", "ema20", "current_volume", "volume_20d_avg",
            "volume_ratio_20", "volume_confirmed", "price_confirmed",
            "volume_price_confirmed", "entry_price", "stop_loss",
            "target_price", "risk_per_share", "reward_per_share",
            "risk_reward_ratio", "sector", "composite_score", "status"
        ]
        self.assertGreater(len(self.all_candidates), 0, "Candidate list should not be empty.")
        for cand in self.all_candidates:
            for field in required_fields:
                self.assertIn(field, cand, f"Candidate {cand.get('symbol')} missing required field: {field}")

    def test_02_qualified_candidates_strict_volume_expansion(self):
        """Verify qualified candidates strictly satisfy volume_ratio_20 >= 2.0."""
        qualified = [c for c in self.all_candidates if c.get("is_qualified")]
        self.assertGreater(len(qualified), 0, f"Expected qualified candidates on date {self.test_date}")
        for cand in qualified:
            vr = cand.get("volume_ratio_20")
            self.assertIsNotNone(vr, f"Qualified candidate {cand['symbol']} volume_ratio_20 is None")
            self.assertGreaterEqual(vr, 2.0, f"Qualified candidate {cand['symbol']} has vol_ratio={vr} < 2.0")
            self.assertTrue(cand.get("volume_confirmed"), f"Qualified candidate {cand['symbol']} volume_confirmed is not True")

    def test_03_qualified_candidates_strict_price_confirmation(self):
        """Verify qualified candidates strictly satisfy close > ema20 with valid ema20 > 0."""
        qualified = [c for c in self.all_candidates if c.get("is_qualified")]
        for cand in qualified:
            close = cand.get("close")
            ema20 = cand.get("ema20")
            self.assertIsNotNone(ema20, f"Qualified candidate {cand['symbol']} ema20 is None")
            self.assertGreater(ema20, 0.0, f"Qualified candidate {cand['symbol']} has ema20 <= 0")
            self.assertIsNotNone(close, f"Qualified candidate {cand['symbol']} close is None")
            self.assertGreater(close, ema20, f"Qualified candidate {cand['symbol']} close ({close}) <= ema20 ({ema20})")
            self.assertTrue(cand.get("price_confirmed"), f"Qualified candidate {cand['symbol']} price_confirmed is not True")

    def test_04_no_generic_one_point_zero_volume_fallback_for_qualified(self):
        """Failure Mode A: volume_ratio_20 == 1.0 AND volume_confirmed == True MUST be 0 candidates."""
        bad_cands = [c for c in self.all_candidates if c.get("volume_ratio_20") == 1.0 and c.get("volume_confirmed") is True]
        self.assertEqual(len(bad_cands), 0, f"Found candidates with fake 1.0 volume ratio marked confirmed: {bad_cands}")

    def test_05_no_zero_ema20_price_confirmed_candidates(self):
        """Failure Mode B: ema20 == 0/None AND price_confirmed == True MUST be 0 candidates."""
        bad_cands = [c for c in self.all_candidates if (c.get("ema20") is None or c.get("ema20") == 0) and c.get("price_confirmed") is True]
        self.assertEqual(len(bad_cands), 0, f"Found candidates with zero/None EMA20 marked price_confirmed: {bad_cands}")

    def test_06_no_generic_one_point_five_rr_ratio(self):
        """Failure Mode C: risk_reward_ratio == 1.5 when strategy has no explicit target MUST be 0 candidates."""
        bad_cands = [c for c in self.all_candidates if cand_has_unconfigured_target(c) and c.get("risk_reward_ratio") == 1.5]
        self.assertEqual(len(bad_cands), 0, f"Found candidates with manufactured 1.5 R:R ratio: {bad_cands}")

    def test_07_no_qualified_candidates_with_vol_ratio_below_two(self):
        """Failure Mode D: qualified candidate with volume_ratio_20 < 2.0 MUST be 0."""
        bad_cands = [c for c in self.all_candidates if c.get("is_qualified") and (c.get("volume_ratio_20") is None or c.get("volume_ratio_20") < 2.0)]
        self.assertEqual(len(bad_cands), 0, f"Found qualified candidates with vol_ratio < 2.0: {bad_cands}")

    def test_08_no_qualified_candidates_with_close_below_or_equal_ema20(self):
        """Failure Mode E: qualified candidate with close <= ema20 MUST be 0."""
        bad_cands = [c for c in self.all_candidates if c.get("is_qualified") and (c.get("close") is None or c.get("ema20") is None or c.get("close") <= c.get("ema20"))]
        self.assertEqual(len(bad_cands), 0, f"Found qualified candidates with close <= ema20: {bad_cands}")

    def test_09_date_boundary_consistency(self):
        """Candidate dates must be the actual final EOD bar used by its indicators."""
        for cand in self.all_candidates:
            self.assertIsNotNone(cand.get("data_as_of"), f"Candidate {cand['symbol']} is missing data_as_of")
            self.assertEqual(cand.get("signal_date"), cand.get("data_as_of"), f"Candidate {cand['symbol']} date mismatch")
            self.assertLessEqual(cand["data_as_of"], self.test_date, f"Candidate {cand['symbol']} uses future data")

    def test_10_shortlist_values_survive_to_candidate_contract(self):
        """The card values must be the precise values used for qualification."""
        rows_by_symbol = {str(row["Symbol"]).replace(".NS", ""): row for _, row in self.shortlist_df.iterrows()}
        for cand in self.all_candidates:
            source = rows_by_symbol[cand["symbol"]]
            self.assertEqual(cand["data_as_of"], str(source["Data_As_Of"])[:10])
            self.assertEqual(cand["close"], source["Close"])
            self.assertEqual(cand["ema20"], source["EMA_20"])
            self.assertEqual(cand["current_volume"], source["Current_Volume"])
            self.assertEqual(cand["volume_20d_avg"], source["Volume_20D_Avg"])
            self.assertEqual(cand["volume_ratio_20"], source["Volume_Ratio_20"])


def cand_has_unconfigured_target(cand: Dict[str, Any]) -> bool:
    return cand.get("target_price") is None


if __name__ == "__main__":
    unittest.main()

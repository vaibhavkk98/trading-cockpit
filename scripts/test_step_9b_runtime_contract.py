"""
STEP 9B.4 — RUNTIME CONTRACT & INTERFACE REGRESSION TEST SUITE

Verifies:
1. SignalEngine.run_stage1_screening accepts as_of_date and return_diagnostics.
2. Screener contract compatibility.
3. Execution without TypeError.
4. Correct return types for return_diagnostics=True/False.
"""

import unittest
import inspect
import pandas as pd
from adapters import SignalEngine, MarketDataProvider, UniverseProvider

class TestRuntimeContract(unittest.TestCase):

    def test_01_signal_engine_signature(self):
        sig = inspect.signature(SignalEngine.run_stage1_screening)
        params = sig.parameters
        self.assertIn('as_of_date', params, "SignalEngine.run_stage1_screening must accept 'as_of_date'")
        self.assertIn('return_diagnostics', params, "SignalEngine.run_stage1_screening must accept 'return_diagnostics'")
        self.assertIn('max_scan', params, "SignalEngine.run_stage1_screening must accept 'max_scan'")

    def test_02_signal_engine_execution_with_as_of_date(self):
        engine = SignalEngine()
        test_symbols = ["RELIANCE.NS", "TCS.NS"]
        
        # Test with return_diagnostics=False
        res_df = engine.run_stage1_screening(
            symbols=test_symbols,
            max_scan=2,
            as_of_date="2026-08-07",
            return_diagnostics=False
        )
        self.assertIsInstance(res_df, pd.DataFrame)

        # Test with return_diagnostics=True
        res_tuple = engine.run_stage1_screening(
            symbols=test_symbols,
            max_scan=2,
            as_of_date="2026-08-07",
            return_diagnostics=True
        )
        self.assertIsInstance(res_tuple, tuple)
        self.assertEqual(len(res_tuple), 2)
        df, diag = res_tuple
        self.assertIsInstance(df, pd.DataFrame)
        self.assertIsInstance(diag, dict)

    def test_03_market_provider_regime_as_of_date(self):
        provider = MarketDataProvider()
        regime_info = provider.get_index_regime(as_of_date="2026-08-07")
        self.assertIn("data_as_of", regime_info)
        self.assertIn("regime", regime_info)
        self.assertEqual(regime_info["data_as_of"], "2026-08-07")

if __name__ == "__main__":
    unittest.main()

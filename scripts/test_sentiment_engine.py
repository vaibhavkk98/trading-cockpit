"""
STEP 7.5 — SENTIMENT ENGINE V1 INTERFACE UNIT TESTS

Verifies:
1. Schema validity (all required standard fields present)
2. Score range (-1.0 to +1.0)
3. Confidence range (0.0 to 1.0)
4. Unavailable-data behavior
5. No fabricated historical values
6. Deterministic output
7. sentiment_enabled=false behavior
8. Audit logging to data/sentiment/sentiment_log.csv
"""
import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import sentiment_engine as se

LOG_PATH = os.path.join(PROJECT_ROOT, "data", "sentiment", "sentiment_log.csv")


class TestSentimentEngineV1(unittest.TestCase):

    def setUp(self):
        # Clear log file before each test for clean isolation if needed
        if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)

    def test_01_schema_validity(self):
        """Standard SentimentResult output must contain all required schema fields."""
        res = se.get_symbol_sentiment("RELIANCE.NS", "2026-01-15")
        d = res.to_dict()
        required_keys = [
            "symbol",
            "as_of_date",
            "sentiment_score",
            "sentiment_regime",
            "confidence",
            "source_count",
            "data_timestamp",
            "evidence_status",
            "source_type",
        ]
        for k in required_keys:
            self.assertIn(k, d, f"Missing schema key: {k}")

        self.assertEqual(res.symbol, "RELIANCE.NS")
        self.assertEqual(res.as_of_date, "2026-01-15")

    def test_02_score_range_and_regimes(self):
        """Sentiment score (if present) must be within [-1.0, +1.0], and regime must be valid."""
        res = se.get_symbol_sentiment("TCS.NS", "2026-01-15")
        if res.sentiment_score is not None:
            self.assertGreaterEqual(res.sentiment_score, -1.0)
            self.assertLessEqual(res.sentiment_score, 1.0)

        valid_regimes = ["BULLISH", "NEUTRAL", "BEARISH", "UNAVAILABLE"]
        self.assertIn(res.sentiment_regime, valid_regimes)

    def test_03_confidence_range(self):
        """Confidence (if present) must be within [0.0, 1.0]."""
        res = se.get_market_sentiment("2026-01-15")
        if res.confidence is not None:
            self.assertGreaterEqual(res.confidence, 0.0)
            self.assertLessEqual(res.confidence, 1.0)

        valid_statuses = ["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT_DATA"]
        self.assertIn(res.evidence_status, valid_statuses)

    def test_04_unavailable_data_behavior(self):
        """When historical evidence is absent, engine must return UNAVAILABLE with null score/confidence."""
        res = se.get_symbol_sentiment("INFY.NS", "2025-06-15")
        self.assertIsNone(res.sentiment_score)
        self.assertIsNone(res.confidence)
        self.assertEqual(res.sentiment_regime, "UNAVAILABLE")
        self.assertEqual(res.evidence_status, "UNAVAILABLE")

    def test_05_no_fabricated_historical_values(self):
        """Engine must NEVER fabricate synthetic sentiment for historical dates."""
        historical_dates = ["2024-01-01", "2025-03-15", "2025-10-10"]
        for dt in historical_dates:
            res_sym = se.get_symbol_sentiment("SBIN.NS", dt)
            res_mkt = se.get_market_sentiment(dt)
            self.assertIsNone(res_sym.sentiment_score, f"Fabricated score detected for {dt}")
            self.assertEqual(res_sym.sentiment_regime, "UNAVAILABLE")
            self.assertIsNone(res_mkt.sentiment_score, f"Fabricated market score detected for {dt}")
            self.assertEqual(res_mkt.sentiment_regime, "UNAVAILABLE")

    def test_06_deterministic_output(self):
        """Multiple calls with identical inputs must return identical outputs."""
        res1 = se.get_symbol_sentiment("ICICIBANK.NS", "2026-02-01")
        res2 = se.get_symbol_sentiment("ICICIBANK.NS", "2026-02-01")
        self.assertEqual(res1, res2)

    def test_07_sentiment_enabled_default_false_behavior(self):
        """When sentiment_enabled is false (default config), engine returns DISABLED status."""
        self.assertFalse(se.is_sentiment_enabled())
        res = se.get_symbol_sentiment("HDFCBANK.NS", "2026-02-01")
        self.assertEqual(res.sentiment_regime, "UNAVAILABLE")
        self.assertEqual(res.source_type, "DISABLED")

        feats = se.get_sentiment_features("HDFCBANK.NS", "2026-02-01")
        self.assertFalse(feats["sentiment_enabled"])
        self.assertIsNone(feats["symbol_sentiment_score"])
        self.assertEqual(feats["symbol_sentiment_regime"], "UNAVAILABLE")

    def test_08_audit_logging(self):
        """Calls to sentiment functions must log rows to data/sentiment/sentiment_log.csv."""
        _ = se.get_symbol_sentiment("BHARTIARTL.NS", "2026-01-20")
        _ = se.get_market_sentiment("2026-01-20")

        self.assertTrue(os.path.exists(LOG_PATH), "Log file was not created")
        df_log = pd.read_csv(LOG_PATH)
        self.assertGreaterEqual(len(df_log), 2)
        symbols = df_log["symbol"].tolist()
        self.assertIn("BHARTIARTL.NS", symbols)
        self.assertIn("NIFTY50", symbols)


if __name__ == "__main__":
    print("=" * 80)
    print("STEP 7.5 SENTIMENT ENGINE V1 UNIT TESTS")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSentimentEngineV1)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)

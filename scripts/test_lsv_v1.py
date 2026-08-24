#!/usr/bin/env python3
"""Focused causal and immutable LSV-V1 contracts."""

from __future__ import annotations

import datetime as dt
import inspect
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


_TEMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(_TEMP.name) / "lsv.db")

import database
import latent_state_vector as lsv
import recommendation_ledger as ledger


def history(periods=280, multiplier=1.0):
    index = pd.bdate_range("2025-01-01", periods=periods)
    close = (100 + np.arange(periods) * 0.15) * multiplier
    open_ = close * (1 + np.sin(np.arange(periods)) * 0.002)
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    volume = 100_000 + (np.arange(periods) % 20) * 2_500
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index)


class LSVV1Tests(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_no_future_leakage(self):
        stock = history(); benchmark = history(multiplier=2)
        cutoff = stock.index[-6].date().isoformat()
        before = lsv.build_causal_vector(stock, benchmark, cutoff)
        stock.loc[stock.index[-5]:, ["Close", "High", "Volume"]] *= 100
        benchmark.loc[benchmark.index[-5]:, "Close"] *= 10
        after = lsv.build_causal_vector(stock, benchmark, cutoff)
        self.assertEqual(before, after)

    def test_vector_has_all_eight_families(self):
        vector = lsv.build_causal_vector(history(), history(multiplier=2), "2026-01-27")
        self.assertEqual(set(lsv.FAMILIES), set(vector).intersection(lsv.FAMILIES))
        self.assertEqual(vector["positioning"]["futures_open_interest"], lsv.NOT_AVAILABLE)
        self.assertEqual(vector["participation"]["delivery_pct"], lsv.NOT_AVAILABLE)

    def test_correct_causal_cross_sectional_percentiles(self):
        rows = []
        for symbol, value in (("A", 1.0), ("B", 2.0), ("C", 3.0)):
            vector = lsv.empty_vector("2026-08-18")
            vector["relative_demand"]["stock_vs_nifty500_return_20d_pct"] = value
            rows.append({"symbol": symbol, "signal_date": "2026-08-18", "lsv_v1": vector})
        lsv.assign_cross_sectional_rs_percentiles(rows)
        values = [row["lsv_v1"]["relative_demand"]["cross_sectional_rs_percentile"] for row in rows]
        self.assertEqual(values, [100 / 6, 50.0, 500 / 6])

    def test_cross_section_does_not_mix_dates(self):
        rows = []
        for date, value in (("2026-08-18", 1.0), ("2026-08-19", 100.0)):
            vector = lsv.empty_vector(date); vector["relative_demand"]["stock_vs_nifty500_return_20d_pct"] = value
            rows.append({"signal_date": date, "lsv_v1": vector})
        lsv.assign_cross_sectional_rs_percentiles(rows)
        self.assertTrue(all(row["lsv_v1"]["relative_demand"]["cross_sectional_rs_percentile"] == 50.0 for row in rows))

    def _snapshot(self):
        vector = lsv.empty_vector("2026-08-18")
        return {
            "opportunity_id": "2026-08-18:ABC:VCP", "signal_date": "2026-08-18",
            "signal_timestamp": "2026-08-18T11:00:00+00:00", "symbol": "ABC", "strategy": "VCP",
            "reference_price": 123.0, "allocator_status": "ALLOCATED", "opportunity_rank": 1,
            "lsv_v1": vector, "historical_analog": {"evidence_quality": lsv.NOT_AVAILABLE},
            "market_context": {}, "methodologies": {"lsv": lsv.LSV_METHOD_HASH},
            "source_timestamps": {}, "missingness": vector["missingness"], "provenance": [],
        }

    def test_idempotent_write_preserves_first_timestamp(self):
        snapshot = self._snapshot()
        first = database.persist_recommendation_snapshot(snapshot)
        snapshot["signal_timestamp"] = "2026-08-18T12:00:00+00:00"
        second = database.persist_recommendation_snapshot(snapshot)
        loaded = database.load_recommendation_snapshot(snapshot["opportunity_id"], lsv.LSV_METHOD_HASH)
        self.assertTrue(first["saved"]); self.assertFalse(second["saved"])
        self.assertEqual(loaded["signal_timestamp"], "2026-08-18T11:00:00")

    def test_immutable_snapshot_rejects_changed_facts(self):
        snapshot = self._snapshot(); database.persist_recommendation_snapshot(snapshot)
        snapshot["reference_price"] = 124.0
        with self.assertRaises(database.RecommendationLedgerConflictError):
            database.persist_recommendation_snapshot(snapshot)

    def test_missing_data_remains_not_available(self):
        vector = lsv.reconstruct_from_decision_payload({"volume_ratio_20": None}, "2024-01-10")
        self.assertEqual(vector["price_state"]["return_60d_pct"], lsv.NOT_AVAILABLE)
        self.assertEqual(vector["participation"]["delivery_ratio"], lsv.NOT_AVAILABLE)
        self.assertIn("positioning.futures_basis_pct", vector["missingness"])

    def test_market_context_is_loaded_as_of_cutoff(self):
        old = {"as_of_date": "2026-08-18", "as_of_timestamp": "2026-08-18T10:37:00+00:00", "methodology_version": "MC1", "trend": {"state": "NEUTRAL"}, "breadth": {"state": "WEAK"}, "volatility": {"state": "LOW"}, "sector_participation": {"state": "MIXED"}, "coverage": {}, "missingness": [], "provenance": []}
        future = {**old, "as_of_date": "2026-08-19", "as_of_timestamp": "2026-08-19T10:37:00+00:00", "trend": {"state": "SUPPORTIVE"}}
        database.persist_market_context_snapshot(old); database.persist_market_context_snapshot(future)
        bundle = database.load_market_context_bundle_as_of("2026-08-18", "2026-08-18T11:00:00+00:00")
        self.assertEqual(bundle["structural"]["trend"]["state"], "NEUTRAL")

    def test_capture_reads_persisted_context_without_provider_calls(self):
        source = inspect.getsource(ledger.capture_qualified_recommendations)
        self.assertIn("load_market_context_bundle_as_of", source)
        self.assertNotIn("fetch_", source)
        self.assertNotIn("build_structural_context", source)

    def test_navigation_cannot_create_recommendations(self):
        import cockpit_ui
        self.assertNotIn("persist_recommendation_snapshot", inspect.getsource(cockpit_ui))

    def test_capture_every_qualified_row(self):
        decisions = []
        for index in range(2):
            decisions.append({"opportunity_id": f"O{index}", "signal_date": "2026-08-18", "symbol": f"S{index}", "strategy": "VCP", "entry_price": 100 + index, "qualification_status": "QUALIFIED", "lsv_v1": lsv.empty_vector("2026-08-18")})
        result = ledger.capture_qualified_recommendations(decisions, "2026-08-18T11:00:00+00:00", "TEST")
        self.assertEqual(result, {"saved": 2, "idempotent": 0, "failed": 0, "total": 2})

    def test_coverage_report_is_read_only_and_explicit(self):
        report = database.recommendation_ledger_coverage()
        self.assertGreaterEqual(report["rows"], 0)
        if report["rows"]:
            self.assertIn("price_state", report["families"])
            self.assertIn("coverage_pct", report["families"]["price_state"])

    def test_no_score_or_decision_behavior(self):
        source = inspect.getsource(lsv) + inspect.getsource(ledger)
        self.assertNotIn("lsv_score", source.lower())
        self.assertNotIn("execute_paper_trade", source)
        self.assertNotIn("allocate_candidates", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

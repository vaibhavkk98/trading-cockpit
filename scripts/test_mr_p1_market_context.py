#!/usr/bin/env python3
"""Focused MR-P1 production contracts."""

from __future__ import annotations

import datetime as dt
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


_TEMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(_TEMP.name) / "market_context.db")

import database
import market_context as mc


def histories(count=4, periods=90):
    dates = pd.bdate_range("2026-01-01", periods=periods)
    result = {}
    for index in range(count):
        close = 100 + np.arange(periods) * (0.1 + index / 100)
        result[f"S{index}"] = pd.DataFrame({"Close": close}, index=dates)
    return result


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload; self.content = content
    def raise_for_status(self): return None
    def json(self): return self._payload


class FakeSession:
    def __init__(self, rows): self.rows = rows; self.headers = {}
    def get(self, url, timeout=None): return FakeResponse(self.rows if "fiidii" in url else {})


class MarketContextProductionTests(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_structural_market_context_and_idempotent_persistence(self):
        stock = histories(5); n500 = histories(1)["S0"]; vix = pd.DataFrame({"Close": np.linspace(12, 18, 90)}, index=n500.index)
        payload = mc.build_structural_context(stock, n500, vix, histories(4), n500.index[-1].date())
        self.assertNotEqual(payload["trend"]["state"], mc.NOT_AVAILABLE)
        self.assertNotEqual(payload["breadth"]["state"], mc.NOT_AVAILABLE)
        self.assertNotEqual(payload["volatility"]["state"], mc.NOT_AVAILABLE)
        self.assertNotEqual(payload["sector_participation"]["state"], mc.NOT_AVAILABLE)
        self.assertTrue(database.persist_market_context_snapshot(payload)["saved"])
        self.assertFalse(database.persist_market_context_snapshot(payload)["saved"])

    def test_production_has_no_mr_v1_composite(self):
        source = inspect.getsource(mc) + inspect.getsource(__import__("cockpit_ui"))
        for forbidden in ("candidate_A_stress", "candidate_B_stress", "market_stress_index", "overall predictive score"):
            self.assertNotIn(forbidden, source)

    def test_investor_source_mapping_and_current_values(self):
        rows = [{"category": "FII/FPI", "date": "18-Aug-2026", "buyValue": "100", "sellValue": "130", "netValue": "-30"}, {"category": "DII", "date": "18-Aug-2026", "buyValue": "150", "sellValue": "100", "netValue": "50"}]
        payload = mc.fetch_investor_participation(session_factory=lambda: FakeSession(rows))
        self.assertEqual(payload["fii_net_today_cr"], -30)
        self.assertEqual(payload["dii_net_today_cr"], 50)
        self.assertTrue(payload["institutional_absorption"])
        self.assertEqual(payload["historical_calibration"], mc.NOT_AVAILABLE)

    def test_client_is_never_retail(self):
        self.assertEqual(mc.map_participant_category("Client"), "CLIENT_NOT_RETAIL")
        self.assertNotEqual(mc.map_participant_category("Client"), "RETAIL")

    def test_missing_flow_stays_not_available(self):
        rows = [{"category": "DII", "date": "18-Aug-2026", "buyValue": "150", "sellValue": "100", "netValue": "50"}]
        payload = mc.fetch_investor_participation(session_factory=lambda: FakeSession(rows))
        self.assertEqual(payload["state"], mc.NOT_AVAILABLE)
        self.assertIn("fii_fpi_cash", payload["missingness"])

    def test_cross_asset_calculation_is_deterministic(self):
        dates = pd.bdate_range("2025-01-01", periods=100)
        columns = {}
        for index, spec in enumerate(mc.CROSS_ASSETS.values()):
            columns[(spec["ticker"], "Close")] = np.linspace(100 + index, 110 + index, 100)
        frame = pd.DataFrame(columns, index=dates); frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        fetcher = lambda *args, **kwargs: frame
        one = mc.fetch_cross_asset_snapshot(fetcher); two = mc.fetch_cross_asset_snapshot(fetcher)
        self.assertEqual(one["state"], two["state"])
        self.assertEqual(one["aggregate_stress_percentile"], two["aggregate_stress_percentile"])
        self.assertGreaterEqual(one["available_core_inputs"], 3)

    def test_unavailable_cross_assets_do_not_become_neutral(self):
        payload = mc.fetch_cross_asset_snapshot(lambda *a, **k: pd.DataFrame())
        self.assertEqual(payload["state"], mc.NOT_AVAILABLE)
        self.assertTrue(all(row["status"] == mc.NOT_AVAILABLE for row in payload["series"].values()))

    def test_event_materiality_contract(self):
        now = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)
        item = {"title": "India faces major crude shock after oil supply disruption", "description": "Rupee and Indian equities affected", "url": "https://example.test/event", "published": "Wed, 19 Aug 2026 06:00:00 GMT"}
        event = mc.classify_material_event(item, mc.EVENT_SOURCES[1], now)
        self.assertEqual(event["india_relevance"], "HIGH")
        self.assertIn(event["magnitude"], {"HIGH", "SEVERE"})
        self.assertTrue(event["provenance"])

    def test_scheduled_event_handling(self):
        now = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)
        rows = mc.load_scheduled_events(now)
        self.assertTrue(rows)
        self.assertTrue(all(row["scheduled"] and "time_until_event_seconds" in row for row in rows))

    def test_stale_event_is_rejected(self):
        now = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)
        item = {"title": "India crude shock", "description": "oil supply", "url": "https://example.test/old", "published": "Sat, 01 Aug 2026 06:00:00 GMT"}
        self.assertIsNone(mc.classify_material_event(item, mc.EVENT_SOURCES[1], now))

    def test_all_snapshot_types_persist_and_reload(self):
        timestamp = "2026-08-19T03:00:00+00:00"
        investor = {"observation_date": "2026-08-19", "as_of_timestamp": timestamp, "methodology_version": mc.INVESTOR_VERSION, "state": "MIXED", "coverage": "CURRENT_ONLY", "missingness": [], "provenance": []}
        cross = {"as_of_timestamp": timestamp, "methodology_version": mc.CROSS_ASSET_VERSION, "state": "NORMAL", "available_core_inputs": 3, "required_core_inputs": 3, "missingness": [], "provenance": []}
        event = {"as_of_timestamp": timestamp, "methodology_version": mc.EVENT_VERSION, "state": "LOW", "coverage": "AVAILABLE", "missingness": [], "source_diagnostics": [], "events": []}
        self.assertTrue(database.persist_investor_participation_snapshot(investor)["saved"])
        self.assertTrue(database.persist_cross_asset_snapshot(cross, "PREOPEN")["saved"])
        self.assertTrue(database.persist_event_risk_snapshot(event, "PREOPEN")["saved"])
        bundle = database.load_latest_market_context_bundle()
        self.assertEqual(bundle["investor_participation"]["state"], "MIXED")
        self.assertEqual(bundle["cross_asset"]["state"], "NORMAL")
        self.assertEqual(bundle["event_risk"]["state"], "LOW")

    def test_ui_uses_persisted_cache_only(self):
        import cockpit_cache, cockpit_ui
        ui_source = inspect.getsource(cockpit_ui._render_market_context)
        cache_source = inspect.getsource(cockpit_cache.load_market_context_bundle)
        self.assertIn("load_market_context_bundle", ui_source)
        self.assertIn("database.load_latest_market_context_bundle", cache_source)
        self.assertNotIn("fetch_", ui_source)

    def test_navigation_has_no_scan_or_trade_side_effect(self):
        import cockpit_ui
        source = inspect.getsource(cockpit_ui._render_market_context)
        self.assertNotIn("run_stage1", source)
        self.assertNotIn("execute_paper_trade", source)
        self.assertNotIn("add_constrained_paper_trade", source)

    def test_p1_and_ha_boundaries_are_absent(self):
        source = inspect.getsource(mc)
        self.assertNotIn("preview_paper_trade", source)
        self.assertNotIn("execute_paper_trade", source)
        self.assertNotIn("HistoricalAnalogService", source)
        self.assertNotIn("persist_historical_analog", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

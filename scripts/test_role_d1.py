#!/usr/bin/env python3
"""Focused ROLE-D1 causal outcome contracts."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


_TEMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(_TEMP.name) / "role.db")

import database
import latent_state_vector as lsv
import role_outcome_engine as role


SIGNAL_DATE = "2026-01-02"


def bars(future_count: int, overrides=None):
    index = pd.bdate_range(SIGNAL_DATE, periods=future_count + 1)
    close = np.array([100.0] + [100.0 + index_ * 0.5 for index_ in range(1, future_count + 1)])
    frame = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close}, index=index)
    for position, values in (overrides or {}).items():
        for key, value in values.items():
            frame.iloc[position, frame.columns.get_loc(key)] = value
    return frame


class RoleD1Tests(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def recommendation(self, suffix=None):
        opportunity_id = f"ROLE:{suffix or self._testMethodName}"
        snapshot = {
            "opportunity_id": opportunity_id, "signal_date": SIGNAL_DATE,
            "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": "ABC", "strategy": "VCP",
            "reference_price": 100.0, "allocator_status": "ALLOCATED", "opportunity_rank": 1,
            "lsv_v1": lsv.empty_vector(SIGNAL_DATE), "historical_analog": {}, "market_context": {},
            "methodologies": {}, "source_timestamps": {}, "missingness": [], "provenance": [],
        }
        database.persist_recommendation_snapshot(snapshot)
        return opportunity_id

    def test_recommendation_outcome_identity(self):
        opportunity_id = self.recommendation()
        role.observe_pending_recommendations({"ABC.NS": bars(1)}, "2026-01-05")
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        self.assertEqual(observed["opportunity_id"], opportunity_id)
        self.assertEqual(observed["signal_date"], SIGNAL_DATE)
        self.assertEqual(observed["reference_price"], 100.0)

    def test_no_future_leakage(self):
        frame = bars(5); first = role.calculate_horizon_outcome(frame.iloc[1:], 100.0, 3)
        frame.iloc[4:, frame.columns.get_loc("High")] = 1000
        frame.iloc[4:, frame.columns.get_loc("Low")] = 1
        second = role.calculate_horizon_outcome(frame.iloc[1:], 100.0, 3)
        self.assertEqual(first, second)

    def test_trading_session_horizons_ignore_weekends(self):
        frame = bars(3)
        outcome = role.calculate_horizon_outcome(frame.iloc[1:], 100.0, 3)
        self.assertEqual(outcome["source_start_date"], "2026-01-05")
        self.assertEqual(outcome["source_end_date"], "2026-01-07")

    def test_mfe_mae_gain_and_drawdown(self):
        frame = bars(3, {1: {"High": 104, "Low": 99, "Close": 102}, 2: {"High": 103, "Low": 96, "Close": 99}, 3: {"High": 106, "Low": 98, "Close": 105}})
        outcome = role.calculate_horizon_outcome(frame.iloc[1:], 100.0, 3)
        self.assertAlmostEqual(outcome["mfe_pct"], 6.0)
        self.assertAlmostEqual(outcome["mae_pct"], -4.0)
        self.assertAlmostEqual(outcome["maximum_gain_pct"], 5.0)
        self.assertAlmostEqual(outcome["maximum_drawdown_pct"], (99 / 102 - 1) * 100)

    def test_canonical_same_bar_adverse_first(self):
        frame = bars(1, {1: {"High": 106, "Low": 96, "Close": 101}})
        result = role.canonical_barrier_outcome(frame.iloc[1:], 100.0, 5.0, 3.0)
        self.assertFalse(result["value"])
        self.assertEqual(result["first_hit"], "SAME_BAR_ADVERSE_FIRST")
        self.assertEqual(result["target_session"], 1)
        self.assertEqual(result["adverse_session"], 1)

    def test_pending_partial_mature_lifecycle(self):
        opportunity_id = self.recommendation()
        role.observe_pending_recommendations({"ABC.NS": bars(0)}, SIGNAL_DATE)
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        self.assertEqual(observed["lifecycle_state"], "PENDING")
        role.observe_pending_recommendations({"ABC.NS": bars(3)}, "2026-01-07")
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        self.assertEqual(observed["lifecycle_state"], "PARTIAL"); self.assertEqual(set(observed["horizons"]), {"1", "3"})
        role.observe_pending_recommendations({"ABC.NS": bars(20)}, "2026-01-30")
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        self.assertEqual(observed["lifecycle_state"], "MATURE"); self.assertEqual(set(observed["horizons"]), {"1", "3", "5", "10", "20"})

    def test_matured_horizon_is_immutable(self):
        opportunity_id = self.recommendation(); role.observe_pending_recommendations({"ABC.NS": bars(1)}, "2026-01-05")
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        payload = dict(observed["horizons"]["1"]); payload["close_return_pct"] += 1
        with self.assertRaises(database.RoleOutcomeConflictError):
            database.persist_role_outcome_horizon(observed["observation_id"], 1, payload["source_end_date"], payload)

    def test_repeated_eod_is_idempotent(self):
        self.recommendation(); first = role.observe_pending_recommendations({"ABC.NS": bars(5)}, "2026-01-09")
        second = role.observe_pending_recommendations({"ABC.NS": bars(5)}, "2026-01-09")
        self.assertEqual(first["horizons_saved"], 3)
        self.assertEqual(second["horizons_saved"], 0)
        self.assertEqual(second["horizons_idempotent"], 3)

    def test_missing_market_data_stays_explicit(self):
        opportunity_id = self.recommendation(); role.observe_pending_recommendations({}, "2026-01-09")
        observed = database.load_role_outcome_observation(opportunity_id, lsv.LSV_METHOD_HASH, role.OUTCOME_METHOD_HASH)
        self.assertEqual(observed["lifecycle_state"], "NOT_AVAILABLE")
        self.assertIn("completed_session_ohlc", observed["missingness"])
        self.assertEqual(observed["horizons"], {})

    def test_recommendation_snapshot_never_mutates(self):
        opportunity_id = self.recommendation()
        before = database.load_recommendation_snapshot(opportunity_id, lsv.LSV_METHOD_HASH)
        role.observe_pending_recommendations({"ABC.NS": bars(20)}, "2026-01-30")
        after = database.load_recommendation_snapshot(opportunity_id, lsv.LSV_METHOD_HASH)
        self.assertEqual(before, after)

    def test_navigation_performs_no_role_work(self):
        import cockpit_ui
        source = inspect.getsource(cockpit_ui)
        self.assertNotIn("observe_pending_recommendations", source)
        self.assertNotIn("role_outcome_engine", source)

    def test_no_learning_score_or_decision_behavior(self):
        source = inspect.getsource(role)
        for forbidden in ("ROLE score", "FAVORABLE", "CAUTION", "execute_paper_trade", "allocate_candidates"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

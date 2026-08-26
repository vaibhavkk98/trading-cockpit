#!/usr/bin/env python3
"""Focused ROLE-F2 observability and Path Risk feature-contract tests."""
from __future__ import annotations

import datetime as dt
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


_TEMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(_TEMP.name) / "role-f2.db")

import cockpit_ui
import database
import latent_state_vector as lsv
import recommendation_ledger
import role_learning_analytics as analytics
import role_outcome_engine as outcomes
from eod_pipeline import execute_eod_pipeline
from path_risk_frozen import EXPECTED_ARTIFACT_FILE_SHA256, EXPECTED_METHODOLOGY_HASH


SIGNAL_DATE = "2026-01-02"


def bars(count=3):
    index = pd.bdate_range(SIGNAL_DATE, periods=count + 1)
    close = 100.0 + np.arange(count + 1)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close}, index=index)


def snapshot(opportunity_id="ROLE-F2:ONE", vector=None):
    vector = vector or lsv.empty_vector(SIGNAL_DATE)
    return {
        "opportunity_id": opportunity_id, "signal_date": SIGNAL_DATE,
        "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": "ABC", "strategy": "VCP",
        "reference_price": 100.0, "lsv_v1": vector, "historical_analog": {}, "market_context": {},
        "methodologies": {"decision_contract": "LIVE"}, "source_timestamps": {},
        "missingness": vector["missingness"], "provenance": [],
    }


def path_risk_payload():
    return {
        "state": "ELEVATED", "adverse_barrier_probability": 0.64,
        "predicted_adverse_magnitude_pct": 7.2, "feature_coverage_pct": 100.0,
        "methodology_hash": EXPECTED_METHODOLOGY_HASH,
        "artifact_sha256": EXPECTED_ARTIFACT_FILE_SHA256,
        "as_of_timestamp": "2026-01-02T23:59:59+00:00",
        "learning_features": {
            "information_discreteness_11m_skip1m": -0.2, "max_daily_return_20d": 4.0,
            "max_daily_return_30d": 6.0, "largest_positive_contribution_20d": 0.3,
            "top3_positive_contribution_20d": 0.6, "positive_return_hhi_20d": 0.18,
            "volume_persistence_5d": 0.8, "abnormal_volume_frequency_20d": 0.25,
            "realized_vol_ratio_5v20": 1.3, "range_ratio_5v20": 1.1,
            "range_expansion_ratio": 1.2, "volatility_acceleration": 0.7,
            "close_location_value": 0.8, "upper_wick_ratio": 0.1, "lower_wick_ratio": 0.2,
            "gap_return": 0.5, "intraday_return": 1.0, "traded_value_ratio": 1.4,
            "traded_value_percentile": 80.0, "log_amihud_impact_20d": 0.4,
            "market_realized_vol_20d": 18.0, "market_breadth_ema20": 0.55,
            "market_trend_ema20": 2.0, "volatility_x_range": 1.56,
            "upper_wick_x_max": 0.4, "impact_x_range": 0.48,
            "fip_x_abnormal_volume": -0.05,
        },
    }


class RoleF2Tests(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_observation_failures_are_explicit(self):
        database.persist_recommendation_snapshot(snapshot())
        with patch.object(outcomes, "calculate_horizon_outcome", side_effect=RuntimeError("broken outcome")):
            result = outcomes.observe_pending_recommendations({"ABC.NS": bars(3)}, "2026-01-07")
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["failures_count"], 1)
        self.assertIn("RuntimeError: broken outcome", result["failure_reasons"])

    def test_health_freshness_and_counts_persist(self):
        result = {
            "run_id": "EOD-2026-01-07", "status": "HEALTHY", "recommendations_examined": 4,
            "observations_updated": 3, "new_horizons_matured": {"1": 2, "3": 1, "5": 0, "10": 0, "20": 0},
            "lifecycle_counts": {"PENDING": 1, "PARTIAL": 3, "MATURE": 0, "NOT_AVAILABLE": 0},
            "failures_count": 0, "failure_reasons": [], "newest_session_date": "2026-01-07",
            "completed_at": "2026-08-27T11:00:00+00:00", "outcome_methodology_hash": outcomes.OUTCOME_METHOD_HASH,
        }
        database.persist_role_pipeline_health(result)
        loaded = database.load_latest_role_pipeline_health()
        self.assertEqual(loaded["status"], "HEALTHY")
        self.assertEqual(loaded["observations_updated"], 3)
        self.assertEqual(loaded["new_horizons_matured"]["3"], 1)
        self.assertEqual(loaded["newest_session_date"], "2026-01-07")
        self.assertEqual(loaded["last_successful_refresh_at"], "2026-08-27T11:00:00")

    def test_old_snapshot_immutable_new_snapshot_has_path_features(self):
        old_vector = lsv.empty_vector(SIGNAL_DATE)
        old_vector["contract_version"] = "LSV_V1"
        old_vector["methodology_hash"] = "old-methodology-hash"
        database.persist_recommendation_snapshot(snapshot("OLD", old_vector))
        before = database.load_recommendation_snapshot("OLD", "old-methodology-hash")
        decision = {**snapshot("NEW"), "path_risk": path_risk_payload()}
        decision.pop("lsv_v1")
        decision["entry_price"] = 100.0
        result = recommendation_ledger.capture_qualified_recommendations([decision], "2026-01-02T11:00:00+00:00", "LIVE")
        after = database.load_recommendation_snapshot("OLD", "old-methodology-hash")
        enriched = database.load_recommendation_snapshot("NEW", lsv.LSV_METHOD_HASH)["lsv_v1"]
        self.assertEqual(before, after)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(enriched["price_state"]["max_daily_return_30d"], 6.0)
        self.assertEqual(enriched["path_risk_context"]["state"], "ELEVATED")
        self.assertNotIn("path_risk_context.state", enriched["missingness"])

    def test_path_risk_state_is_context_not_outcome(self):
        source = inspect.getsource(analytics)
        self.assertIn("descriptive_context_only", source)
        self.assertNotIn("path_risk_context", inspect.getsource(outcomes.calculate_horizon_outcome))

    def test_role_r1_reads_only_predefined_path_risk_fields(self):
        vector = lsv.apply_path_risk_learning_context(lsv.empty_vector(SIGNAL_DATE), path_risk_payload())
        row = {
            "opportunity_id": "ANALYTICS", "signal_date": SIGNAL_DATE, "strategy": "VCP",
            "lsv_methodology_hash": lsv.LSV_METHOD_HASH, "outcome_methodology_hash": outcomes.OUTCOME_METHOD_HASH,
            "lsv_v1": vector, "methodologies": {"decision_contract": "LIVE"}, "provenance": [],
            "horizons": {"10": {"observation_date": "2026-01-20", "payload": {
                "mfe_pct": 6.0, "mae_pct": -2.0, "close_return_pct": 3.0,
                "plus_5_before_minus_3": {"value": True, "first_hit": "TARGET_FIRST"},
            }}},
        }
        result = analytics.build_role_r1_analytics([row], "2026-01-20")
        self.assertEqual(set(result["path_risk_research_cohorts"]), set(analytics.PATH_RISK_RESEARCH_ANCHORS))
        self.assertEqual(result["baseline"]["minus_3_before_plus_5_pct"], 0.0)

    def test_learning_navigation_is_read_only(self):
        source = inspect.getsource(cockpit_ui._render_learning)
        self.assertIn("ROLE outcomes refreshed", source)
        for forbidden in ("persist_role_pipeline_health", "observe_pending_recommendations", "fetch_"):
            self.assertNotIn(forbidden, source)

    def test_core_eod_continues_when_role_fails(self):
        class Market:
            def get_index_regime(self, as_of_date=None): return {"data_as_of": "2026-08-26", "regime": "BULLISH"}
        class Universe:
            def get_universe(self, date_str=None): return ["ABC.NS"]
        class Signals:
            def run_stage1_screening(self, **kwargs): return object(), {"valid_data_count": 1}
        class Allocator:
            def allocate_candidates(self, **kwargs):
                return [{"symbol": "ABC.NS", "strategy": "VCP", "is_qualified": True, "status": "ALLOCATED", "entry_price": 100.0, "signal_date": "2026-08-26"}]
        class Execution:
            def get_open_positions(self): return []
            def refresh_portfolio_positions(self, source_run_id=None): return {"successful_marks": 0}
            def save_portfolio_snapshot(self, reason): return {"saved": True}
        class Events:
            def enrich_candidates(self, candidates, cutoff=None): return candidates
        deps = {"market_data": Market(), "universe": Universe(), "signals": Signals(), "allocator": Allocator(), "execution": Execution(), "event_service": Events()}
        with patch.object(outcomes, "observe_pending_recommendations", side_effect=RuntimeError("ROLE offline")):
            result = execute_eod_pipeline(dt.date(2026, 8, 26), dependencies=deps)
        self.assertTrue(result["persisted"])
        self.assertIn(result["status"], ("SUCCESS", "PARTIAL_SUCCESS"))
        self.assertEqual(result["role_outcomes"]["status"], "DEGRADED")
        self.assertEqual(database.load_latest_role_pipeline_health()["status"], "DEGRADED")


if __name__ == "__main__":
    unittest.main(verbosity=2)

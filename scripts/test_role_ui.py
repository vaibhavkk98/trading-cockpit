#!/usr/bin/env python3
"""Focused read-only ROLE Learning page checks."""

import datetime as dt
import inspect
import os
from pathlib import Path
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "role-ui.sqlite")

import cockpit_ui  # noqa: E402
import database  # noqa: E402
import latent_state_vector as lsv  # noqa: E402
from cockpit_cache import load_role_learning_analytics  # noqa: E402
from role_outcome_engine import OUTCOME_METHOD_HASH, ROLE_OUTCOME_VERSION  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def seed_role():
    for index in range(12):
        vector = lsv.empty_vector("2026-01-02")
        vector["price_state"]["return_20d_pct"] = -6 + index
        vector["participation"]["volume_ratio_20d"] = 0.7 + index / 10
        vector["relative_demand"]["cross_sectional_rs_percentile"] = 10 + index * 7
        vector["volatility_state"]["short_long_volatility_ratio"] = 0.7 + index / 10
        vector["price_response"]["close_location_value"] = -0.6 + index / 10
        vector["liquidity"]["traded_value_percentile_252d"] = 10 + index * 7
        vector["environment"]["breadth"] = "SUPPORTIVE" if index % 2 else "WEAK"
        opportunity_id = f"ROLE-UI:{index}"
        database.persist_recommendation_snapshot({
            "opportunity_id": opportunity_id, "signal_date": "2026-01-02",
            "signal_timestamp": "2026-01-02T11:00:00+00:00", "symbol": f"S{index}",
            "strategy": "VCP" if index < 9 else "Donchian", "reference_price": 100.0,
            "lsv_v1": vector, "historical_analog": {}, "market_context": {},
            "methodologies": {"decision_contract": "LIVE" if index < 6 else "HISTORICAL_CANONICAL_PAYLOAD_BACKFILL"},
            "source_timestamps": {}, "missingness": vector["missingness"], "provenance": [],
        })
        header = database.persist_role_observation_state({
            "opportunity_id": opportunity_id, "lsv_methodology_hash": lsv.LSV_METHOD_HASH,
            "signal_date": "2026-01-02", "reference_price": 100.0,
            "outcome_contract_version": ROLE_OUTCOME_VERSION, "outcome_methodology_hash": OUTCOME_METHOD_HASH,
            "lifecycle_state": "PARTIAL", "sessions_observed": 10,
            "last_observation_date": "2026-01-16", "source": {}, "completeness": {}, "missingness": [],
        })
        for horizon, date in ((5, "2026-01-09"), (10, "2026-01-16")):
            database.persist_role_outcome_horizon(header["observation_id"], horizon, date, {
                "close_return_pct": float(index - 5), "mfe_pct": float(index + 1), "mae_pct": float(-index / 2),
                "plus_5_before_minus_3": {"value": index % 3 == 0},
            })


def run():
    seed_role(); load_role_learning_analytics.clear()
    passed = 0
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert "Learning" in app.radio[0].options
    app.radio[0].set_value("Learning").run(timeout=30)
    assert not app.exception
    passed += 1  # page renders

    captions = "\n".join(item.value for item in app.caption)
    markdown = "\n".join(item.value for item in app.markdown)
    assert "12 recommendations" in captions and "Prospective 10D: 6" in captions
    assert "10D origin" in markdown and "6 / 6" in markdown and "Primary evidence" in markdown
    passed += 1  # status and origin distinction

    selector = next(widget for widget in app.segmented_control if widget.label == "Outcome horizon")
    assert selector.options == ["5D", "10D", "20D"] and selector.value == "10D"
    assert "N=12 · Evidence EARLY" in markdown
    selector.set_value("20D").run(timeout=30)
    markdown = "\n".join(item.value for item in app.markdown)
    assert "N=0 · Evidence INSUFFICIENT" in markdown
    passed += 1  # selector and evidence/N follow horizon

    strategy_table = app.dataframe[0].value
    assert {"Cohort", "N", "Coverage", "Evidence", "Vs baseline"}.issubset(strategy_table.columns)
    assert set(strategy_table["Evidence"]) == {"INSUFFICIENT"}
    assert set(strategy_table["Vs baseline"]) == {"Insufficient evidence"}
    passed += 1  # no misleading comparison

    infos = "\n".join(item.value for item in app.info)
    assert "INSUFFICIENT evidence" in infos
    assert all(label in markdown for label in ("What ROLE is learning", "Predefined interactions"))
    passed += 1  # missing family/interaction evidence explicit

    before = (database.recommendation_ledger_coverage()["rows"], database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH))
    started = time.perf_counter(); app.run(timeout=30); warm_ms = (time.perf_counter() - started) * 1000
    after = (database.recommendation_ledger_coverage()["rows"], database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH))
    assert before == after and warm_ms < 2_000
    passed += 1  # navigation is read-only and bounded

    source = inspect.getsource(cockpit_ui._render_learning)
    assert all(forbidden not in source for forbidden in ("fetch_", "persist_", "observe_", "execute_paper_trade"))
    passed += 1  # no provider/write path

    app.query_params.clear(); app.query_params["page"] = "learning"; app.run(timeout=30)
    assert not app.exception and app.radio[0].value == "Learning"
    passed += 1  # durable top-level route

    assert passed == 8
    print(f"ROLE Learning UI tests: PASS ({passed}/8 scenarios) · warm render {warm_ms:.2f} ms")


if __name__ == "__main__":
    try: run()
    finally: temporary.cleanup()

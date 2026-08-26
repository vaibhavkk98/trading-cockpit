#!/usr/bin/env python3
"""Focused PR-P1 production inference, persistence, and UI checks."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import warnings

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "pr_p1.sqlite")

import database  # noqa: E402
from cockpit_ui import PAGES  # noqa: E402
from path_risk_frozen import (  # noqa: E402
    EXPECTED_ARTIFACT_FILE_SHA256, EXPECTED_METHODOLOGY_HASH, FrozenPathRiskModel,
)
from path_risk_service import ARTIFACT_PATH, apply_path_risk  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def histories():
    count = 320
    index = pd.bdate_range("2025-06-01", periods=count)
    step = np.arange(count)
    close = 100 * np.exp(0.0008 * step + 0.02 * np.sin(step / 7))
    open_ = close * (1 + 0.003 * np.sin(step))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    volume = 1_000_000 * (1 + 0.3 * np.sin(step / 5)); volume[-1] = 3_000_000
    stock = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index)
    market_close = 100 * np.exp(0.0004 * step)
    market = pd.DataFrame({"Open": market_close, "High": market_close * 1.005,
                           "Low": market_close * .995, "Close": market_close,
                           "Volume": volume}, index=index)
    return stock, market


def candidate(path_risk=None):
    return {
        "opportunity_id": "PR-P1:AAA:2026-08-21", "symbol": "AAA.NS",
        "signal_date": "2026-08-21", "qualification_status": "QUALIFIED",
        "is_qualified": True, "strategy": "Donchian Channel Breakout",
        "entry_price": 128.0, "current_close": 128.0, "previous_close": 127.0,
        "allocation_status": "ALLOCATED", "volume_ratio_20": 3.0,
        "path_risk": path_risk,
    }


def run():
    passed = 0
    raw = ARTIFACT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_ARTIFACT_FILE_SHA256
    model = FrozenPathRiskModel.from_path(ARTIFACT_PATH)
    assert model.artifact["methodology_hash"] == EXPECTED_METHODOLOGY_HASH
    passed += 1  # 1 artifact and methodology identities

    stock, market = histories(); decision = candidate()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        first = apply_path_risk([decision], {"AAA.NS": stock}, market, stock.index[-1])
        first_payload = dict(decision["path_risk"])
        second = apply_path_risk([decision], {"AAA.NS": stock}, market, stock.index[-1])
    assert first == second == {"available": 1, "not_available": 0, "failed": 0}
    assert decision["path_risk"] == first_payload
    assert decision["path_risk"]["state"] in {"LOW", "NORMAL", "ELEVATED", "HIGH"}
    assert np.isfinite(decision["path_risk"]["adverse_barrier_probability"])
    assert np.isfinite(decision["path_risk"]["predicted_adverse_magnitude_pct"])
    passed += 1  # 2 deterministic warning-free inference and state boundary

    short = stock.tail(100); unavailable = candidate()
    apply_path_risk([unavailable], {"AAA.NS": short}, market, short.index[-1])
    assert unavailable["path_risk"]["state"] == "NOT AVAILABLE"
    assert unavailable["path_risk"]["missing_features"]
    passed += 1  # 3 missing required state is explicit

    database.persist_analysis_run({
        "run_id": "PR-P1-RUN", "analysis_date": stock.index[-1].date(),
        "status": "SUCCESS", "decision_contract_version": "TEST",
    }, [decision])
    loaded = database.load_latest_analysis_run()["decisions"][0]
    assert loaded["path_risk"] == decision["path_risk"]
    assert loaded["path_risk"]["artifact_sha256"] == EXPECTED_ARTIFACT_FILE_SHA256
    passed += 1  # 4 canonical EOD opportunity persistence

    before_run = database.load_latest_analysis_run()
    before_trades = len(database.get_open_trades())
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.session_state["live_decisions"] = [decision]
    app.session_state["qualified_candidates"] = [decision]
    app.run(timeout=30)
    dashboard_tables = [item.value for item in app.dataframe if "Path Risk" in item.value.columns]
    assert dashboard_tables and dashboard_tables[0].iloc[0]["Path Risk"] == decision["path_risk"]["state"]
    passed += 1  # 5 Dashboard state

    app.radio[0].set_value("Opportunities").run(timeout=30)
    opportunity_tables = [item.value for item in app.dataframe if "Path Risk" in item.value.columns]
    assert opportunity_tables and opportunity_tables[-1].iloc[0]["Path Risk"] == decision["path_risk"]["state"]
    passed += 1  # 6 Opportunities state without sorting/filtering

    app.query_params["page"] = "stock"; app.query_params["symbol"] = "AAA"; app.query_params["tab"] = "path_risk"
    app.run(timeout=30)
    assert not app.exception and app.segmented_control[0].value == "Path Risk"
    rendered = "\n".join(item.value for item in app.info) + "\n" + "\n".join(item.value for item in app.markdown)
    assert "Path Risk measures downside-path adversity, not expected direction" in rendered
    passed += 1  # 7 qualified Stock Intelligence panel and explainer

    captions = "\n".join(item.value for item in app.caption)
    assert "Methodology:" in captions and EXPECTED_ARTIFACT_FILE_SHA256 in captions
    assert "barrier contribution" in captions and "magnitude contribution" in captions
    passed += 1  # 8 explainability and provenance

    assert database.load_latest_analysis_run() == before_run
    assert len(database.get_open_trades()) == before_trades
    source = (ROOT / "cockpit_ui.py").read_text()
    assert "apply_path_risk" not in source and "fit(" not in source
    passed += 1  # 9 navigation has no writes, providers, or fitting

    assert app.radio[0].options == PAGES and not app.exception
    passed += 1  # 10 navigation contract and headless render

    assert passed == 10
    print(f"PR-P1 production Path Risk tests: PASS ({passed}/10 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()

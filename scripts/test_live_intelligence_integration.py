#!/usr/bin/env python3
"""Focused live intelligence and compact-navigation integration checks."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from historical_analogs_service import HistoricalAnalogService  # noqa: E402


def run():
    dates = pd.bdate_range("2025-08-01", periods=270)
    stock_close = pd.Series(np.linspace(100.0, 145.0, len(dates)), index=dates)
    stock = pd.DataFrame({
        "Open": stock_close * 0.998, "High": stock_close * 1.012,
        "Low": stock_close * 0.988, "Close": stock_close,
        "Volume": np.linspace(1_000_000, 1_800_000, len(dates)),
    })
    nifty_close = pd.Series(np.linspace(20_000.0, 23_000.0, len(dates)), index=dates)
    nifty = pd.DataFrame({"Close": nifty_close})
    vix = pd.DataFrame({"Close": 14.0 + np.sin(np.arange(len(dates)) / 9.0)}, index=dates)
    state = HistoricalAnalogService.build_causal_query_state(stock, nifty, vix, dates[-1])
    assert all(key in state["ha_features"] for key in ("ret_5d", "ret_10d", "ret_20d"))

    service = HistoricalAnalogService()
    result = service.evaluate({
        "opportunity_id": "LIVE-INTEGRATION", "symbol": "TEST", "signal_date": dates[-1].date().isoformat(),
        "qualification_status": "QUALIFIED", "is_qualified": True, **state,
    }, persist=False)
    assert result["analog_count"] == 40 and result["evidence_quality"] != "INSUFFICIENT"

    source = (ROOT / "cockpit_ui.py").read_text()
    assert 'PAGES = ["Dashboard", "Opportunities", "Portfolio", "Stock Research", "Settings"]' in source
    assert 'tabs = ["Performance", "Positions & Risk"]' in source
    assert 'with st.sidebar.expander("Portfolio controls", expanded=False)' in source
    assert "_render_events(candidate)" in source and "_render_trade_economics(candidate)" in source
    assert 'st.selectbox(\n        "Company or NSE symbol"' in source
    assert "if not chosen:" in source and "_load_stock_research(chosen)" in source
    assert "Open full opportunity intelligence" not in source and "sync_query=False" in source
    pipeline = (ROOT / "eod_pipeline.py").read_text()
    identity_at = pipeline.index('decision.setdefault(\n                "opportunity_id"')
    evaluate_at = pipeline.index("analog_service.evaluate(decision, persist=True)")
    persist_at = pipeline.index("persisted = persist_analysis_run(run, decisions)")
    assert identity_at < evaluate_at < persist_at
    print("Live intelligence integration tests: PASS (7/7 scenarios)")


if __name__ == "__main__":
    run()

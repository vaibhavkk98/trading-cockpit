#!/usr/bin/env python3
"""Focused AutoPaper-UI1 read-model and Streamlit smoke contracts."""
import datetime as dt
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "autopaper_ui1_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
from autopaper_ui_service import humanize_reason, lifecycle_display, load_autopaper_ui_state, review_gate_progress


DATES = pd.bdate_range("2026-09-14", "2026-09-30")


def histories():
    frame = pd.DataFrame({"Open": [100 + i for i in range(len(DATES))],
        "High": [102 + i for i in range(len(DATES))], "Low": [99 + i for i in range(len(DATES))],
        "Close": [101 + i for i in range(len(DATES))], "Volume": [1_000_000] * len(DATES)}, index=DATES)
    return {"AAA.NS": frame}


def decision():
    return {"opportunity_id": "2026-09-14:AAA:Donchian", "symbol": "AAA.NS", "signal_date": "2026-09-14",
        "strategy": "Donchian Channel Breakout", "entry_price": 100., "atr_20": 8., "current_volume": 1_000_000,
        "path_risk": {"state": "HIGH"}, "is_qualified": True}


def counts():
    session = database.SessionLocal()
    try:
        return tuple(session.query(model).count() for model in (
            database.AutoPaperOrder, database.AutoPaperPosition, database.AutoPaperDecision,
            database.AutoPaperPortfolioSnapshot, database.AutoPaperHealth))
    finally: session.close()


def main():
    assert database.init_db()
    zero = load_autopaper_ui_state()
    assert zero["health"]["status"] == "NOT YET RUN" and zero["accounts"]["BASELINE_C3"] is None
    assert zero["review_gate"]["status"] == "NOT YET MET"

    assert "position capacity" in humanize_reason("NO_CAPACITY")
    assert lifecycle_display(order_status="PENDING", side="BUY") == "ORDERED"
    assert lifecycle_display(order_status="PENDING", side="SELL") == "EXIT PENDING"
    assert lifecycle_display(position_status="OPEN") == "HOLDING"
    assert review_gate_progress({key: value for key, value in engine.FUTURE_REVIEW_GATE.items() if isinstance(value, int)})["met"] is False

    stamp = dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc)
    engine.run_prospective_autopaper([decision()], histories(), DATES[0].date(), stamp, "EOD-2026-09-14")
    pending = load_autopaper_ui_state((decision()["opportunity_id"],))
    assert pending["health"]["status"] == "HEALTHY"
    assert pending["decision_funnel"]["qualified"] == 1 and pending["decision_funnel"]["queued"] == 1
    assert pending["opportunity_statuses"][decision()["opportunity_id"]]["state"] == "ORDERED"
    assert len(pending["orders"]["pending_entries"]) == 1 and not pending["positions"]

    date = DATES[1].date()
    engine.run_prospective_autopaper([], histories(), date,
        dt.datetime.combine(date, dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{date}")
    before = counts(); state = load_autopaper_ui_state((decision()["opportunity_id"],)); after = counts()
    assert before == after  # Navigation projection is strictly read-only.
    assert state["accounts"]["BASELINE_C3"]["open_positions"] == 1
    assert len(state["positions"]) == 1 and state["positions"][0]["hold_label"] == "Day 1 / 10"
    assert state["positions"][0]["advisory"]["path_risk"]["state"] == "HIGH"
    baseline_cash = state["accounts"]["BASELINE_C3"]["cash"]
    assert state["accounts"]["SHADOW_C0"]["cash"] != baseline_cash
    assert all(row["position_id"] == state["positions"][0]["position_id"] for row in state["positions"])
    assert state["role_linkage_status"] == "ACTIVE" and not state["review_gate"]["met"]
    assert "historical" not in str(state["accounts"]).lower()

    source = (ROOT / "cockpit_ui.py").read_text()
    assert 'tabs = ["Overview", "Positions", "Orders & Decisions", "Performance", "Shadows"]' in source
    assert "run_prospective_autopaper" not in source and "ExecutionSimulator" not in source
    assert "load_autopaper_cockpit" in source and "AutoPaper reason" in source

    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"
    app.run(timeout=30)
    assert not app.exception
    assert any("AutoPaper Research Baseline V1" in item.value for item in app.success)
    for view in ("Positions", "Orders & Decisions", "Performance", "Shadows"):
        app.session_state["portfolio_view"] = view
        app.run(timeout=30)
        assert not app.exception
    for page in ("signals", "learning"):
        routed = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
        routed.query_params["page"] = page
        routed.run(timeout=30)
        assert not routed.exception
    print("AutoPaper-UI1 focused tests: 20 passed")


if __name__ == "__main__": main()

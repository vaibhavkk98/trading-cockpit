#!/usr/bin/env python3
"""Focused AutoPaper-P1.1 observability and catastrophe-shadow contracts."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "autopaper_p1_1_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
from autopaper_observability import (NOT_AVAILABLE, classify_market_state,
    correlation_metrics, sector_concentration)
from autopaper_ui_service import load_autopaper_ui_state


DATES = pd.bdate_range("2026-09-14", periods=16)


def history(*, locked_date=None, crash_date=None, retry_date=None):
    rows = []
    for i, date in enumerate(DATES):
        o, h, low, close, volume = 100 + i, 102 + i, 99 + i, 101 + i, 1_000_000
        if crash_date is not None and date == crash_date: o, h, low, close = 101, 103, 84, 90
        if locked_date is not None and date == locked_date: o = h = low = close = 70
        if retry_date is not None and date == retry_date: o, h, low, close = 65, 70, 63, 68
        rows.append((o, h, low, close, volume))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=DATES)


def decision(symbol, date, sector="Industrials", atr=4.0):
    clean = symbol.removesuffix(".NS")
    return {"opportunity_id": f"{date}:{clean}:Donchian", "symbol": symbol,
        "signal_date": str(date), "strategy": "Donchian Channel Breakout",
        "entry_price": 100., "atr_20": atr, "current_volume": 1_000_000,
        "sector": sector, "is_qualified": True}


def run(rows, histories, index):
    date = DATES[index].date()
    return engine.run_prospective_autopaper(rows, histories, date,
        dt.datetime.combine(date, dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{date}")


def main():
    # Frozen identities and market-state semantics.
    assert engine.METHODOLOGY_HASH == "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79"
    assert engine.CONFIG_HASH == "86ed4f1153186874330f62e0c8bb4ad80aef7972a20188952af3697aced1d9c2"
    normal = (100., 102., 99., 101., 1000., 101000.)
    assert classify_market_state(normal, 100)[0] == "NORMAL_EXECUTABLE"
    assert classify_market_state(normal, 99)[0] == "GAP_EXECUTION"
    assert classify_market_state((100, 102, 99, 101, 0, 0), 100)[0] == "ZERO_VOLUME"
    locked = (70., 70., 70., 70., 1000., 70000.)
    assert classify_market_state(locked, 100)[0] == "LOCKED_RANGE"
    assert classify_market_state(locked, 100, circuit_lower=70)[0] == "CONFIRMED_LOWER_CIRCUIT"
    assert classify_market_state(None, 100)[0] == "NON_TRADING"
    assert classify_market_state((100, 90, 95, 100, 1000, 100000), 100)[0] == "CIRCUIT_STATUS_NOT_AVAILABLE"

    threshold, downside = engine.catastrophe_threshold(100, 8 / 3.5)
    assert round(threshold, 6) == 88 and round(downside, 6) == .12
    threshold, downside = engine.catastrophe_threshold(100, 15 / 3.5)
    assert round(threshold, 6) == 85 and round(downside, 6) == .15
    assert sector_concentration(["A", "A", "B"])["hhi"] == 5 / 9
    assert sector_concentration([NOT_AVAILABLE])["hhi"] is None
    assert correlation_metrics({"AAA.NS": history()}, ["AAA.NS"])["status"] == NOT_AVAILABLE
    corr = correlation_metrics({"AAA.NS": history(), "BBB.NS": history() * pd.Series(
        {"Open": 2, "High": 2, "Low": 2, "Close": 2, "Volume": 1})}, ["AAA.NS", "BBB.NS"])
    assert corr["status"] == "AVAILABLE" and abs(corr["average_pairwise_correlation"] - 1) < 1e-9

    histories = {"AAA.NS": history(crash_date=DATES[2]),
                 "BBB.NS": history(locked_date=DATES[4], retry_date=DATES[5])}
    a = decision("AAA.NS", DATES[0].date(), "Industrials", atr=4)
    first = run([a], histories, 0)
    assert first["accounts"]["BASELINE_C3"]["new_entries"] == 0
    assert len(first["accounts"]) == 4
    run([], histories, 1)
    session = database.SessionLocal()
    try:
        positions = session.query(database.AutoPaperPosition).filter_by(status="OPEN").all()
        assert len(positions) == 4
        base = next(x for x in positions if x.account_id == "BASELINE_C3")
        catastrophe = next(x for x in positions if x.account_id == "SHADOW_CATASTROPHE")
        assert base.quantity == catastrophe.quantity
        assert "catastrophe_threshold" not in json.loads(base.payload)
        assert json.loads(catastrophe.payload)["catastrophe_threshold"] < catastrophe.entry_price
        snapshot = session.get(database.AutoPaperOpportunityMetadata, a["opportunity_id"])
        original_hash = snapshot.snapshot_hash
        assert snapshot.sector == "Industrials"
    finally: session.close()

    # Reusing identity with a changed classification cannot rewrite the snapshot.
    b = decision("BBB.NS", DATES[2].date(), "Financials", atr=4)
    run([{**a, "sector": "Financials"}, b], histories, 2)
    session = database.SessionLocal()
    try:
        snapshot = session.get(database.AutoPaperOpportunityMetadata, a["opportunity_id"])
        assert snapshot.sector == "Industrials" and snapshot.snapshot_hash == original_hash
        base_position = session.query(database.AutoPaperPosition).filter_by(account_id="BASELINE_C3", status="OPEN").one()
        catastrophe_trade = session.query(database.AutoPaperTrade).filter_by(account_id="SHADOW_CATASTROPHE").one()
        assert base_position.status == "OPEN"
        assert json.loads(catastrophe_trade.payload)["exit_reason"] == "CATASTROPHE_EXIT"
        assert session.query(database.AutoPaperCatastropheObservation).count() == 1
        assert session.query(database.AutoPaperExecutionTelemetry).count() >= 5
    finally: session.close()

    # A second catastrophe is unfilled on an OHLC-locked bar, then retries next session.
    run([], histories, 3)  # T+1 fill.
    run([], histories, 4)  # locked-range catastrophe trigger remains pending.
    session = database.SessionLocal()
    try:
        pending = session.query(database.AutoPaperOrder).filter_by(
            account_id="SHADOW_CATASTROPHE", opportunity_id=b["opportunity_id"], side="SELL", status="PENDING").one()
        assert pending is not None
    finally: session.close()
    run([], histories, 5)  # eventual executable open.
    session = database.SessionLocal()
    try:
        trade = session.query(database.AutoPaperTrade).filter_by(
            account_id="SHADOW_CATASTROPHE", opportunity_id=b["opportunity_id"]).one()
        assert json.loads(trade.payload)["exit_reason"] == "CATASTROPHE_EXIT"
    finally: session.close()

    state = load_autopaper_ui_state()
    assert "SHADOW_CATASTROPHE" in state["accounts"]
    assert state["execution_quality"]["attempts"] > 0
    assert "portfolio_correlation" in state["risk_observability"]
    assert state["catastrophe"]["triggers"] >= 1
    assert state["review_gate"]["rows"][-1]["target"] == 80

    # Projection remains SELECT-only and source has no decision authority hook.
    before = DB.stat().st_size
    load_autopaper_ui_state()
    after = DB.stat().st_size
    assert before == after
    source = (ROOT / "autopaper_observability.py").read_text()
    assert "ExecutionSimulator" not in source and "AutoPaperOrder(" not in source
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    assert not app.exception
    assert any("Prospective risk observability" in str(x.value) for x in app.markdown)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    assert not app.exception and any("Catastrophe shadow" in str(x.value) for x in app.caption)
    print("AutoPaper-P1.1 focused tests: 34 passed")


if __name__ == "__main__": main()

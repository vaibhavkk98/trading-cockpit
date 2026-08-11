#!/usr/bin/env python3
"""Focused Phase 1A persistence and headless-runner contract tests."""
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["TRADING_COCKPIT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "phase_1a_eod_test.db")
try:
    os.remove(os.environ["TRADING_COCKPIT_DB_PATH"])
except FileNotFoundError:
    pass

from database import (DailyOpportunity, SessionLocal, add_paper_trade, get_latest_position_marks,
                      get_open_trades_persisted, load_latest_analysis_run, persist_position_marks,
                      save_portfolio_snapshot)
from eod_pipeline import execute_eod_pipeline, has_completed_market_bar


class Market:
    def __init__(self, data_as_of): self.data_as_of = data_as_of
    def get_index_regime(self, as_of_date=None): return {"data_as_of": self.data_as_of, "regime": "BULLISH"}


class Universe:
    def get_universe(self, date_str=None): return ["AAA.NS", "BBB.NS"]


class Signals:
    def run_stage1_screening(self, **kwargs): return object(), {"valid_data_count": 1}


class Allocator:
    def allocate_candidates(self, **kwargs):
        return [
            {"symbol": "AAA.NS", "strategy": "Donchian Channel Breakout", "is_qualified": True, "regime": "BULLISH", "volume_ratio_20": 3.1, "entry_price": 100.0, "atr_20": 2.0, "atr_20_available": True, "status": "ALLOCATED", "signal_date": "2026-08-12"},
            {"symbol": "BBB.NS", "strategy": "EMA Pullback / Bounce", "is_qualified": True, "regime": "BULLISH", "volume_ratio_20": 2.5, "entry_price": 200.0, "atr_20": 3.0, "atr_20_available": True, "status": "QUALIFIED — CAPITAL CAP", "signal_date": "2026-08-12"},
        ]


class Execution:
    def get_open_positions(self): return []
    def refresh_portfolio_positions(self, source_run_id=None): return {"positions": []}
    def save_portfolio_snapshot(self, reason): return {"saved": True, "reason": reason}


class EventService:
    def enrich_candidates(self, candidates, cutoff=None): return candidates


def deps(data_as_of):
    return {"market_data": Market(data_as_of), "universe": Universe(), "signals": Signals(), "allocator": Allocator(), "execution": Execution(), "event_service": EventService()}


def run():
    assert "streamlit" not in open("eod_pipeline.py").read().lower()
    date = dt.date(2026, 8, 12)
    assert has_completed_market_bar({"data_as_of": "2026-08-12"}, date)
    assert not has_completed_market_bar({"data_as_of": "2026-08-11"}, date)
    no_bar = execute_eod_pipeline(date, dependencies=deps("2026-08-11"))
    assert no_bar["status"] == "NO_COMPLETED_MARKET_BAR" and not no_bar["persisted"] and load_latest_analysis_run() is None

    first = execute_eod_pipeline(date, dependencies=deps("2026-08-12"))
    assert first["status"] == "PARTIAL_SUCCESS" and first["persisted"]
    latest = load_latest_analysis_run()
    assert latest and latest["analysis_date"] == "2026-08-12" and len(latest["decisions"]) == 2
    second = execute_eod_pipeline(date, dependencies=deps("2026-08-12"))
    assert second["run_id"] == first["run_id"]
    session = SessionLocal()
    try:
        assert session.query(DailyOpportunity).count() == 2
    finally:
        session.close()

    trade = add_paper_trade("LEGACY.NS", 100.0, 10, "Donchian Channel Breakout")
    persist_position_marks([{ "trade_id": trade.id, "symbol": trade.symbol, "mark_price": 112.0, "mark_date": date, "provider": "TEST" }], source_run_id=first["run_id"])
    persist_position_marks([{ "trade_id": trade.id, "symbol": trade.symbol, "mark_price": 112.0, "mark_date": date, "provider": "TEST" }], source_run_id=first["run_id"])
    position = next(row for row in get_open_trades_persisted() if row["id"] == trade.id)
    assert position["current_price"] == 112.0 and position["unrealized_pnl_inr"] == 120.0 and position["risk_metadata_status"] == "AVAILABLE"
    assert get_latest_position_marks()[trade.id]["source_run_id"] == first["run_id"]
    snapshot = {"portfolio_equity": 1_000_120.0, "cash": 999_000.0, "deployed_capital": 1_000.0, "unrealized_pnl": 120.0, "open_positions": 1, "price_coverage_count": 1, "price_coverage_total": 1}
    assert save_portfolio_snapshot(snapshot, "AUTOMATED_EOD")["saved"]
    assert not save_portfolio_snapshot(snapshot, "AUTOMATED_EOD")["saved"]

    app_source = open("app.py").read()
    assert "load_latest_analysis_run" in app_source and "persisted_run_hydrated" in app_source
    assert "Load price chart" in app_source
    assert "execute_eod_pipeline" in app_source and "refresh_portfolio_positions()" not in app_source.split("with tab_portfolio:")[0]
    print("Phase 1A EOD automation tests: PASS")


if __name__ == "__main__":
    run()

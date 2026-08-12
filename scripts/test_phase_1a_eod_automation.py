#!/usr/bin/env python3
"""Focused Phase 1A persistence and headless-runner contract tests."""
import datetime as dt
import os
import sys
import tempfile
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["TRADING_COCKPIT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "phase_1a_eod_test.db")
try:
    os.remove(os.environ["TRADING_COCKPIT_DB_PATH"])
except FileNotFoundError:
    pass

import database
from database import (DailyOpportunity, SessionLocal, add_paper_trade, get_latest_position_marks,
                      get_open_trades_with_live_data,
                      get_open_trades_persisted, load_latest_analysis_run, persist_analysis_run,
                      persist_position_marks, save_portfolio_snapshot)
from eod_pipeline import (execute_eod_pipeline, has_completed_market_bar,
                          normalize_market_date, resolve_expected_completed_market_date)
from provider_symbols import yahoo_nse_symbol
from zoneinfo import ZoneInfo


class Market:
    def __init__(self, data_as_of): self.data_as_of = data_as_of
    def get_index_regime(self, as_of_date=None): return {"data_as_of": self.data_as_of, "regime": "BULLISH"}


class LaggingMarket:
    def get_index_regime(self, as_of_date=None):
        latest = "2026-08-10" if as_of_date in {"2026-08-11", "2026-08-10"} else None
        return {"data_as_of": latest, "regime": "BULLISH"}


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
    ist = ZoneInfo("Asia/Kolkata")
    assert resolve_expected_completed_market_date(dt.datetime(2026, 8, 12, 1, 38, tzinfo=ist)) == dt.date(2026, 8, 11)
    assert resolve_expected_completed_market_date(dt.datetime(2026, 8, 12, 13, 0, tzinfo=ist)) == dt.date(2026, 8, 11)
    assert resolve_expected_completed_market_date(dt.datetime(2026, 8, 12, 17, 0, tzinfo=ist)) == dt.date(2026, 8, 12)
    assert resolve_expected_completed_market_date(dt.datetime(2026, 8, 15, 17, 0, tzinfo=ist)) == dt.date(2026, 8, 14)
    valid_sessions = {dt.date(2026, 8, 14)}
    assert resolve_expected_completed_market_date(
        dt.datetime(2026, 8, 17, 17, 0, tzinfo=ist), completed_bar_lookup=lambda candidate: candidate in valid_sessions
    ) == dt.date(2026, 8, 14)
    assert has_completed_market_bar({"data_as_of": "2026-08-12"}, date)
    assert has_completed_market_bar({"data_as_of": pd.Timestamp("2026-08-12 00:00:00", tz="Asia/Kolkata")}, date)
    assert normalize_market_date(pd.Timestamp("2026-08-11 18:30:00", tz="UTC")) == date
    assert not has_completed_market_bar({"data_as_of": "2026-08-11"}, date)
    assert yahoo_nse_symbol("FINCABLES") == "FINCABLES.NS"
    assert yahoo_nse_symbol("GRASIM") == "GRASIM.NS"
    assert yahoo_nse_symbol("FINCABLES.NS") == "FINCABLES.NS"
    no_bar = execute_eod_pipeline(date, dependencies=deps("2026-08-11"))
    assert no_bar["status"] == "NO_COMPLETED_MARKET_BAR" and not no_bar["persisted"] and load_latest_analysis_run() is None

    persist_analysis_run({"analysis_date": date, "status": "NO_COMPLETED_MARKET_BAR", "decision_contract_version": "TEST"}, [])
    assert load_latest_analysis_run() is None

    first = execute_eod_pipeline(date, dependencies=deps("2026-08-12"))
    assert first["status"] == "PARTIAL_SUCCESS" and first["persisted"]
    latest = load_latest_analysis_run()
    assert latest and latest["analysis_date"] == "2026-08-12" and len(latest["decisions"]) == 2
    second = execute_eod_pipeline(date, dependencies=deps("2026-08-12"))
    assert second["run_id"] == first["run_id"]
    premarket = execute_eod_pipeline(dependencies={**deps("2026-08-11"), "now": dt.datetime(2026, 8, 12, 1, 38, tzinfo=ist)})
    assert str(premarket["analysis_date"]) == "2026-08-11" and premarket["persisted"]
    lagged = execute_eod_pipeline(dependencies={**deps("2026-08-10"), "market_data": LaggingMarket(), "now": dt.datetime(2026, 8, 12, 1, 38, tzinfo=ist)})
    assert str(lagged["analysis_date"]) == "2026-08-10"
    assert [item["candidate"] for item in lagged["market_date_diagnostics"]] == ["2026-08-11", "2026-08-10"]
    assert lagged["market_date_diagnostics"][0]["provider_latest_bar_date"] == "2026-08-10"
    session = SessionLocal()
    try:
        assert session.query(DailyOpportunity).filter_by(run_id=first["run_id"]).count() == 2
    finally:
        session.close()

    trade = add_paper_trade("LEGACY.NS", 100.0, 10, "Donchian Channel Breakout")
    persist_position_marks([{ "trade_id": trade.id, "symbol": trade.symbol, "mark_price": 112.0, "mark_date": date, "provider": "TEST" }], source_run_id=first["run_id"])
    persist_position_marks([{ "trade_id": trade.id, "symbol": trade.symbol, "mark_price": 112.0, "mark_date": date, "provider": "TEST" }], source_run_id=first["run_id"])
    position = next(row for row in get_open_trades_persisted() if row["id"] == trade.id)
    assert position["current_price"] == 112.0 and position["unrealized_pnl_inr"] == 120.0 and position["risk_metadata_status"] == "AVAILABLE"
    assert get_latest_position_marks()[trade.id]["source_run_id"] == first["run_id"]

    fincables = add_paper_trade("FINCABLES", 100.0, 10, "Donchian Channel Breakout")
    grasim = add_paper_trade("GRASIM", 200.0, 5, "Donchian Channel Breakout")
    missing = add_paper_trade("MISSING", 50.0, 4, "Donchian Channel Breakout")
    requested_provider_symbols = []

    class FakeTicker:
        def __init__(self, symbol):
            requested_provider_symbols.append(symbol); self.symbol = symbol
        def history(self, **kwargs):
            assert kwargs.get("auto_adjust") is True
            if self.symbol == "MISSING.NS":
                return pd.DataFrame()
            price = 112.0 if self.symbol == "FINCABLES.NS" else 220.0
            return pd.DataFrame({"Close": [price], "High": [price], "Low": [price]}, index=pd.DatetimeIndex(["2026-08-12"], tz="Asia/Kolkata"))

    original_ticker = database.yf.Ticker
    database.yf.Ticker = FakeTicker
    try:
        refreshed = get_open_trades_with_live_data(source_run_id="TEST-MARK-RUN")
    finally:
        database.yf.Ticker = original_ticker
    assert {"FINCABLES.NS", "GRASIM.NS"}.issubset(set(requested_provider_symbols))
    assert all(item not in requested_provider_symbols for item in ("FINCABLES", "GRASIM", "FINCABLES.NS.NS"))
    refreshed_by_id = {row["id"]: row for row in refreshed}
    assert refreshed_by_id[fincables.id]["symbol"] == "FINCABLES" and refreshed_by_id[fincables.id]["current_price"] == 112.0
    assert refreshed_by_id[fincables.id]["unrealized_pnl_inr"] == 120.0
    assert refreshed_by_id[grasim.id]["symbol"] == "GRASIM" and refreshed_by_id[grasim.id]["current_price"] == 220.0
    assert refreshed_by_id[missing.id]["current_price"] is None and refreshed_by_id[missing.id]["unrealized_pnl_inr"] is None
    assert get_latest_position_marks()[fincables.id]["provider"] == "YFINANCE"
    snapshot = {"portfolio_equity": 1_000_120.0, "cash": 999_000.0, "deployed_capital": 1_000.0, "unrealized_pnl": 120.0, "open_positions": 1, "price_coverage_count": 1, "price_coverage_total": 1}
    assert save_portfolio_snapshot(snapshot, "AUTOMATED_EOD")["saved"]
    assert not save_portfolio_snapshot(snapshot, "AUTOMATED_EOD")["saved"]

    app_source = open("app.py").read()
    screener_source = open("screener.py").read()
    database_source = open("database.py").read()
    assert "persistence.load_latest_analysis_run" not in app_source and "persisted_run_hydrated" in app_source
    assert "getattr(persistence, \"load_latest_analysis_run\", None)" in app_source
    assert "Load price chart" in app_source
    assert "execute_eod_pipeline" in app_source and "refresh_portfolio_positions()" not in app_source.split("with tab_portfolio:")[0]
    assert "yahoo_nse_symbol" in screener_source and "auto_adjust=True" in screener_source
    assert "yf.Ticker(yahoo_nse_symbol(symbol))" in database_source
    print("Phase 1A EOD automation tests: PASS")


if __name__ == "__main__":
    run()

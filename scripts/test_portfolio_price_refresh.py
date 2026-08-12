#!/usr/bin/env python3
"""Deterministic production-style tests for the narrow Portfolio mark path."""
import datetime as dt
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
test_db = os.path.join(tempfile.gettempdir(), "portfolio_price_refresh_test.db")
os.environ["TRADING_COCKPIT_DB_PATH"] = test_db
try:
    os.remove(test_db)
except FileNotFoundError:
    pass

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import database
from database import (PositionMark, add_paper_trade, get_latest_position_marks,
                      get_open_trades_persisted, refresh_open_trade_marks,
                      update_trade_status)
from interaction_architecture import portfolio_position_rows
from position_mark_provider import fetch_latest_yahoo_marks
from ui_components import display_value, format_currency, format_percent, format_price


def run():
    fincables = add_paper_trade("FINCABLES", 100.0, 10, "Donchian Channel Breakout")
    grasim = add_paper_trade("GRASIM", 200.0, 5, "EMA Pullback / Bounce")
    closed = add_paper_trade("CLOSEDONLY", 50.0, 2, "Donchian Channel Breakout")
    assert update_trade_status(closed.id, "CLOSED_MANUAL", 55.0)

    batch_requests = []
    fallback_requests = []

    def batch_download(symbols, **kwargs):
        batch_requests.append(list(symbols))
        assert kwargs["auto_adjust"] is True and kwargs["timeout"] == 10
        columns = pd.MultiIndex.from_tuples([
            ("FINCABLES.NS", "Close"), ("GRASIM.NS", "Close"),
        ])
        return pd.DataFrame([[112.0, 220.0]], columns=columns, index=pd.DatetimeIndex(["2026-08-12"], tz="Asia/Kolkata"))

    class NoFallback:
        def __init__(self, symbol): fallback_requests.append(symbol)
        def history(self, **kwargs): return pd.DataFrame()

    def production_fetcher(symbols):
        return fetch_latest_yahoo_marks(symbols, download_fn=batch_download, ticker_factory=NoFallback)

    first = refresh_open_trade_marks(source_run_id="MANUAL_PRICE_REFRESH", mark_fetcher=production_fetcher)
    assert batch_requests == [["FINCABLES.NS", "GRASIM.NS"]]
    assert fallback_requests == [] and first["provider_calls"] == 1
    assert first["open_positions"] == 2 and first["unique_symbols"] == 2
    assert first["successful_marks"] == 2 and first["failed_marks"] == 0
    assert "CLOSEDONLY" not in batch_requests[0] and "CLOSEDONLY.NS" not in batch_requests[0]

    positions = {row["symbol"]: row for row in first["positions"]}
    assert positions["FINCABLES"]["entry_price"] == 100.0
    assert positions["FINCABLES"]["current_price"] == 112.0
    assert positions["FINCABLES"]["unrealized_pnl_inr"] == 120.0
    assert positions["FINCABLES"]["unrealized_pnl_pct"] == 12.0
    assert positions["GRASIM"]["unrealized_pnl_inr"] == 100.0
    assert positions["FINCABLES"]["marked_at"] and positions["FINCABLES"]["mark_date"] == "2026-08-12"
    assert get_latest_position_marks()[fincables.id]["source_run_id"] == "MANUAL_PRICE_REFRESH"

    # A failed new quote must leave the prior valid GRASIM mark intact.
    partial = refresh_open_trade_marks(source_run_id="MANUAL_PRICE_REFRESH", mark_fetcher=lambda symbols: {
        "marks": {"FINCABLES": {"mark_price": 115.0, "mark_date": dt.date(2026, 8, 13)}},
        "failed_symbols": ["GRASIM"], "provider_calls": 2, "unique_symbols": 2, "elapsed_seconds": 0.02,
    })
    partial_positions = {row["symbol"]: row for row in partial["positions"]}
    assert partial["successful_marks"] == 1 and partial["failed_marks"] == 1
    assert partial_positions["FINCABLES"]["current_price"] == 115.0
    assert partial_positions["GRASIM"]["current_price"] == 220.0

    # Persisted reload is provider-free and retains price/P&L.
    calls_before_reload = len(batch_requests) + len(fallback_requests)
    reloaded = {row["symbol"]: row for row in get_open_trades_persisted()}
    assert len(batch_requests) + len(fallback_requests) == calls_before_reload
    assert reloaded["FINCABLES"]["current_price"] == 115.0 and reloaded["FINCABLES"]["unrealized_pnl_inr"] == 150.0

    table = portfolio_position_rows(list(reloaded.values()), {
        "currency": format_currency, "percent": format_percent, "price": format_price, "display": display_value,
    })
    fin_row = next(row for row in table if row["Symbol"] == "FINCABLES")
    assert fin_row["Strategy"] == "Donchian" and fin_row["Entry price"] == "₹100.00"
    assert "₹150.00" in fin_row["P&L"] and "15.00%" in fin_row["P&L"]
    assert fin_row["Current price"] == "₹115.00" and fin_row["Price as of"] != "Not available"

    # Missing values remain unavailable in the view model, never zero.
    missing_row = portfolio_position_rows([{"symbol": "X", "entry_price": None, "price_status": "PRICE_NOT_AVAILABLE"}], {
        "currency": format_currency, "percent": format_percent, "price": format_price, "display": display_value,
    })[0]
    assert missing_row["Entry price"] == "Not available" and missing_row["P&L"] == "Not available"

    ddl = str(CreateTable(PositionMark.__table__).compile(dialect=postgresql.dialect()))
    assert "position_marks" in ddl and "trade_id" in ddl and "BIGINT" not in ddl

    app_source = open("app.py").read()
    adapter_source = open("adapters.py").read()
    portfolio_fragment = app_source.split("def render_portfolio_workspace", 1)[1].split("with tab_portfolio:", 1)[0]
    assert "@st.fragment" in app_source and "Entry price" not in portfolio_fragment  # View model owns columns.
    refresh_section = portfolio_fragment.split("db_health =", 1)[0]
    assert "refresh_portfolio_positions" in refresh_section and "st.rerun()" not in refresh_section
    assert all(token not in portfolio_fragment for token in ("run_stage1_screening", "allocate_candidates", "get_market_risk_context_for_ui"))
    assert "refresh_open_trade_marks" in adapter_source and "sync_result = self.sync_live_prices()" not in adapter_source
    print("Portfolio price refresh tests: PASS")


if __name__ == "__main__":
    run()

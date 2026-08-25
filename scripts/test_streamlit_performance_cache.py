#!/usr/bin/env python3
"""Focused cache, invalidation, lazy-render, and transactional safety checks."""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "performance.sqlite")

import adapters as adapters_module  # noqa: E402
import cockpit_ui  # noqa: E402
import database  # noqa: E402
from adapters import ExecutionAdapter  # noqa: E402
from cockpit_cache import (  # noqa: E402
    invalidate_opportunity_reads, invalidate_portfolio_reads, load_ha_snapshot,
    load_latest_opportunities, load_portfolio_pnl, load_portfolio_state,
)
from historical_analogs_service import METHODOLOGY_HASH, METHODOLOGY_ID  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def candidate(symbol="AAA", price=100.0):
    return {"opportunity_id": f"PERF:{symbol}:2026-08-14", "symbol": symbol,
            "signal_date": "2026-08-14", "qualification_status": "QUALIFIED",
            "is_qualified": True, "strategy": "Donchian Channel Breakout",
            "entry_price": price, "current_close": price, "previous_close": price - 1.0,
            "allocation_status": "QUALIFIED"}


def snapshot(opportunity_id, symbol):
    return {"opportunity_id": opportunity_id, "signal_date": "2026-08-14", "symbol": symbol,
            "methodology_id": METHODOLOGY_ID, "methodology_hash": METHODOLOGY_HASH,
            "evidence_quality": "HIGH", "analog_count": 0, "unique_security_count": 0,
            "earliest_analog_date": None, "latest_analog_date": None,
            "maximum_year_share": 0.0, "maximum_date_share": 0.0,
            "outcome_attractiveness": {}, "downside_evidence": {}, "evidence": {}, "analogs": []}


def run():
    passed = 0
    execution = ExecutionAdapter()

    engine_id = id(database.engine)
    assert database.init_db() and database.init_db() and id(database.engine) == engine_id
    assert "@st.cache_resource\ndef get_database_resource" in (ROOT / "app.py").read_text()
    passed += 1  # 1 resource reused

    class CountedExecution:
        position_calls = 0
        summary_calls = 0
        def get_open_positions(self):
            self.position_calls += 1
            return []
        def get_portfolio_summary(self):
            self.summary_calls += 1
            return {"current_cash_inr": 1_000_000.0}
    fake = CountedExecution(); load_portfolio_state.clear()
    assert load_portfolio_state(fake) == load_portfolio_state(fake)
    assert fake.position_calls == 1 and fake.summary_calls == 1
    passed += 1  # 2 consistent read cache

    invalidate_portfolio_reads(); before = load_portfolio_state(execution)
    result = execution.execute_paper_trade(candidate("TRADE"), 1_000.0)
    after = load_portfolio_state(execution)
    assert result["success"] and len(after["positions"]) == len(before["positions"]) + 1
    passed += 1  # 3 paper trade invalidates portfolio reads

    trade = database.get_open_trades()[0]
    original_refresh = adapters_module.refresh_open_trade_marks
    def marked_refresh(source_run_id=None):
        database.persist_position_marks([{"trade_id": trade.id, "symbol": trade.symbol, "mark_price": 110.0,
            "mark_date": dt.date.today(), "marked_at": dt.datetime.now(dt.timezone.utc),
            "provider": "FIXTURE", "mark_status": "AVAILABLE"}], source_run_id)
        return {"positions": database.get_open_trades_persisted(), "open_positions": 1,
                "successful_marks": 1, "failed_marks": 0, "marked_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    adapters_module.refresh_open_trade_marks = marked_refresh
    try:
        execution.refresh_portfolio_positions()
    finally:
        adapters_module.refresh_open_trade_marks = original_refresh
    assert load_portfolio_state(execution)["positions"][0]["current_price"] == 110.0
    passed += 1  # 4 mark refresh invalidates positions/P&L reads

    load_latest_opportunities.clear(); assert load_latest_opportunities() is None
    analysis_date = dt.date(2026, 8, 14)
    database.persist_analysis_run({"run_id": "PERF-RUN", "analysis_date": analysis_date,
        "status": "SUCCESS", "completed_at": dt.datetime.now(dt.timezone.utc),
        "decision_contract_version": "TEST"}, [candidate("RUN")])
    assert load_latest_opportunities() is None
    invalidate_opportunity_reads()
    assert load_latest_opportunities()["run_id"] == "PERF-RUN"
    passed += 1  # 5 scan invalidates opportunities

    first, second = snapshot("HA:A", "A"), snapshot("HA:B", "B")
    database.persist_historical_analog_snapshot(first); database.persist_historical_analog_snapshot(second)
    load_ha_snapshot.clear()
    assert load_ha_snapshot("HA:A", "2026-08-14", METHODOLOGY_HASH)["symbol"] == "A"
    assert load_ha_snapshot("HA:B", "2026-08-14", METHODOLOGY_HASH)["symbol"] == "B"
    passed += 1  # 6 HA cache identity includes opportunity/hash

    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    app.session_state["live_decisions"] = [candidate("FRAGMENT")]
    app.session_state["qualified_candidates"] = [candidate("FRAGMENT")]
    app.query_params["page"] = "stock"; app.query_params["symbol"] = "FRAGMENT"; app.query_params["tab"] = "trade"
    app.run(timeout=30); trade_count = len(database.get_open_trades())
    next(widget for widget in app.number_input if widget.label == "Investment amount (₹)").set_value(2_000.0).run(timeout=30)
    assert len(database.get_open_trades()) == trade_count
    passed += 1  # 7 fragment interaction does not trade

    before_run = database.load_latest_analysis_run(); before_marks = database.get_latest_position_marks()
    app.run(timeout=30)
    assert database.load_latest_analysis_run() == before_run and database.get_latest_position_marks() == before_marks
    passed += 1  # 8 fragment interaction does not scan/refresh

    app.query_params.clear(); app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.segmented_control[0].set_value("Positions & Risk").run(timeout=30)
    original_pnl = cockpit_ui.load_portfolio_pnl
    cockpit_ui.load_portfolio_pnl = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("inactive Performance loader ran"))
    try:
        app.run(timeout=30)
    finally:
        cockpit_ui.load_portfolio_pnl = original_pnl
    assert not app.exception
    passed += 1  # 9 inactive Performance section avoids P&L loader

    database.add_paper_trade("UNMARKED", 200.0, 1, "Donchian Channel Breakout")
    load_portfolio_pnl.clear()
    assert load_portfolio_pnl("LIFETIME")["status"] == "NOT_AVAILABLE"
    passed += 1  # 10 missing P&L remains unavailable

    assert cockpit_ui.stock_url("NSE:ABC", "historical_analogs") == "?page=stock&symbol=ABC&tab=historical_analogs"
    passed += 1  # 11 deep links preserved

    stale = load_portfolio_state(execution)
    duplicate = execution.execute_paper_trade(candidate("TRADE"), 1_000.0)
    assert stale and not duplicate["success"] and "already exists" in duplicate["message"]
    passed += 1  # 12 database enforcement overrides cached UI state

    assert passed == 12
    print(f"Streamlit performance/cache tests: PASS ({passed}/12 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()

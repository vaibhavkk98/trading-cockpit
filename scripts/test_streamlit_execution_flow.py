#!/usr/bin/env python3
"""Production-route isolation and frozen P1 interface regression checks."""
import datetime as dt
import inspect
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "execution_flow.sqlite")

import cockpit_cache  # noqa: E402
import database  # noqa: E402
from adapters import ExecutionAdapter  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def candidate(symbol="FLOW"):
    return {
        "opportunity_id": f"FLOW:{symbol}:2026-08-14", "symbol": symbol,
        "signal_date": "2026-08-14", "analysis_date": "2026-08-14",
        "qualification_status": "QUALIFIED", "is_qualified": True,
        "strategy": "Donchian Channel Breakout", "entry_price": 100.0,
        "allocation_status": "QUALIFIED", "volume_ratio_20": 2.0,
    }


def run():
    passed = 0
    preview_parameters = list(inspect.signature(ExecutionAdapter.preview_paper_trade).parameters)
    assert preview_parameters == ["self", "candidate", "investment_amount"]
    assert not hasattr(ExecutionAdapter, "get_portfolio_state")
    assert "portfolio_state=" not in (ROOT / "cockpit_ui.py").read_text()
    passed += 1  # 1 frozen P1 adapter contract restored

    import cockpit_ui
    original_research = cockpit_ui._load_stock_research
    def forbidden(*args, **kwargs):
        raise AssertionError("Stock Research fetched provider data before Search")
    cockpit_ui._load_stock_research = forbidden
    try:
        research = AppTest.from_file(str(ROOT / "app.py"))
        research.session_state["navigation_page"] = "research"
        research.session_state["sidebar_workspace"] = "Stock Research"
        research.query_params["page"] = "research"
        research.run(timeout=30)
        assert not research.exception and any("Stock Research" in item.value for item in research.markdown)
    finally:
        cockpit_ui._load_stock_research = original_research
    passed += 1  # 2 Stock Research provider lookup is explicit

    flow_candidate = candidate()
    database.persist_analysis_run({
        "run_id": "FLOW-RUN", "analysis_date": dt.date(2026, 8, 14), "status": "SUCCESS",
        "completed_at": dt.datetime.now(dt.timezone.utc), "decision_contract_version": "FLOW-TEST",
    }, [flow_candidate])
    cockpit_cache.load_latest_opportunities.clear(); cockpit_cache.load_ha_summaries.clear()
    counts = {"opportunities": 0, "ha": 0, "portfolio": 0}
    original_latest_db = database.load_latest_analysis_run
    original_ha_db = database.load_historical_analog_summaries
    original_summary_method = ExecutionAdapter.get_portfolio_summary
    original_positions_method = ExecutionAdapter.get_open_positions
    def counted_latest():
        counts["opportunities"] += 1
        return original_latest_db()
    def counted_ha(identities, methodology_hash):
        counts["ha"] += 1
        return original_ha_db(identities, methodology_hash)
    def forbidden_portfolio(*args, **kwargs):
        counts["portfolio"] += 1
        raise AssertionError("Opportunities hydrated portfolio state")
    database.load_latest_analysis_run = counted_latest
    database.load_historical_analog_summaries = counted_ha
    ExecutionAdapter.get_portfolio_summary = forbidden_portfolio
    ExecutionAdapter.get_open_positions = forbidden_portfolio
    try:
        opportunities = AppTest.from_file(str(ROOT / "app.py"))
        opportunities.session_state["navigation_page"] = "signals"
        opportunities.session_state["sidebar_workspace"] = "Opportunities"
        opportunities.query_params["page"] = "signals"
        opportunities.run(timeout=30)
        assert not opportunities.exception
        assert counts == {"opportunities": 1, "ha": 1, "portfolio": 0}
    finally:
        database.load_latest_analysis_run = original_latest_db
        database.load_historical_analog_summaries = original_ha_db
        ExecutionAdapter.get_portfolio_summary = original_summary_method
        ExecutionAdapter.get_open_positions = original_positions_method
    passed += 1  # 3 Opportunities uses only latest decisions + bulk HA summaries

    original_summary_loader = cockpit_ui._load_ha_summary
    original_full_loader = cockpit_ui._load_ha
    cockpit_ui._load_ha_summary = forbidden
    cockpit_ui._load_ha = forbidden
    try:
        stock = AppTest.from_file(str(ROOT / "app.py"))
        stock.session_state["candidate_contract_version"] = 3
        stock.session_state["navigation_page"] = "stock"
        stock.session_state["sidebar_workspace"] = "Opportunities"
        stock.session_state["persisted_run_hydrated"] = True
        stock.session_state["live_decisions"] = [flow_candidate]
        stock.session_state["qualified_candidates"] = [flow_candidate]
        stock.query_params["page"] = "stock"; stock.query_params["symbol"] = "FLOW"; stock.query_params["tab"] = "overview"
        stock.run(timeout=30)
        assert not stock.exception and any("Reference price" in item.value for item in stock.markdown)
    finally:
        cockpit_ui._load_ha_summary = original_summary_loader
        cockpit_ui._load_ha = original_full_loader
    passed += 1  # 4 Stock Overview does not invoke HA loaders

    source = (ROOT / "cockpit_ui.py").read_text()
    cases_body = source.split("def _render_ha_cases", 1)[1].split("def _render_ha", 1)[0]
    assert "if not show_cases" in cases_body and "full_snapshot = _load_ha(candidate)" in cases_body
    assert "@st.fragment\ndef _render_ha_cases" in source
    passed += 1  # 5 HA mappings remain lazy and locally interactive

    assert passed == 5
    print(f"Streamlit execution-flow tests: PASS ({passed}/5 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()

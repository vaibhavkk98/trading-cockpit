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

    original_catalog = cockpit_ui._stock_catalog
    original_generic_ha = cockpit_ui._load_generic_ha
    cockpit_ui._stock_catalog = lambda: (("FLOW", "Flow Limited"), ("OTHER", "Other Industries Ltd."))
    cockpit_ui._load_stock_research = lambda symbol, as_of_date=None: {
        "symbol": symbol, "signal_date": "2026-08-14", "entry_price": 120.0,
        "ha_features": {"ret_5d": 2.0, "ret_10d": 4.0, "ret_20d": 6.0,
                        "distance_from_ema20_pct": 1.5, "distance_from_ema20_atr": .8,
                        "largest_positive_daily_return_10d": 2.2, "positive_sessions_10": 6,
                        "volume_ratio_20": 1.4},
        "ha_stock_percentiles": {"ready": 1.0}, "chart": {},
    }
    cockpit_ui._load_generic_ha = lambda *args, **kwargs: {
        "query_scope": "GENERIC_RESEARCH_STATE", "evidence_quality": "HIGH", "analog_count": 40,
        "unique_security_count": 32, "earliest_analog_date": "2017-01-01", "latest_analog_date": "2022-12-01",
        "maximum_year_share": .3, "maximum_date_share": .05,
        "outcome_attractiveness": {}, "downside_evidence": {}, "analogs": [],
    }
    try:
        generic = AppTest.from_file(str(ROOT / "app.py"))
        generic.session_state["navigation_page"] = "research"
        generic.session_state["sidebar_workspace"] = "Stock Research"
        generic.query_params["page"] = "research"
        generic.run(timeout=30)
        selector = next(widget for widget in generic.selectbox if widget.label == "Company or NSE symbol")
        assert "Other Industries Ltd. · OTHER" in selector.options
        selector.set_value("Other Industries Ltd. · OTHER").run(timeout=30)
        detail_tabs = next(widget for widget in generic.segmented_control if widget.label == "Stock detail view")
        assert detail_tabs.options == ["Overview", "Rally", "Historical Analogs", "Events", "Trade"]
        detail_tabs.set_value("Historical Analogs").run(timeout=30)
        assert any("Generic completed-session state" in item.value for item in generic.caption)
        detail_tabs = next(widget for widget in generic.segmented_control if widget.label == "Stock detail view")
        detail_tabs.set_value("Trade").run(timeout=30)
        assert any("Paper execution is available only" in item.value for item in generic.markdown)
    finally:
        cockpit_ui._stock_catalog = original_catalog
        cockpit_ui._load_stock_research = original_research
        cockpit_ui._load_generic_ha = original_generic_ha
    passed += 1  # 3 searchable selector and generic five-tab research never enable execution

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
    passed += 1  # 4 Opportunities uses only latest decisions + bulk HA summaries

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
    passed += 1  # 5 Stock Overview does not invoke HA loaders

    source = (ROOT / "cockpit_ui.py").read_text()
    cases_body = source.split("def _render_ha_cases", 1)[1].split("def _render_ha", 1)[0]
    assert "if not show_cases" in cases_body and "provided_snapshot or _load_ha(candidate)" in cases_body
    assert "@st.fragment\ndef _render_ha_cases" in source
    passed += 1  # 6 HA mappings remain lazy and locally interactive

    assert passed == 6
    print(f"Streamlit execution-flow tests: PASS ({passed}/6 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        temporary.cleanup()

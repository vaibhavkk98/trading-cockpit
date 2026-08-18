#!/usr/bin/env python3
"""Focused HA-P2 UI, routing, advisory safety, and headless checks."""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(temporary.name) / "ha_p2.sqlite")

import database  # noqa: E402
from adapters import ExecutionAdapter  # noqa: E402
from cockpit_ui import PAGES, stock_url  # noqa: E402
from historical_analogs_service import HistoricalAnalogContractError, HistoricalAnalogService, METHODOLOGY_HASH, METHODOLOGY_ID  # noqa: E402
from portfolio_analytics import get_portfolio_pnl  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def run():
    passed = 0
    candidate = {
        "opportunity_id": "HA-P2:AAA:2026-08-14", "symbol": "AAA.NS", "signal_date": "2026-08-14",
        "qualification_status": "QUALIFIED", "is_qualified": True, "strategy": "Donchian Channel Breakout",
        "entry_price": 100.0, "allocation_status": "QUALIFIED", "volume_ratio_20": 2.8,
        "ha_features": {"ret_10d": 8.5, "ret_5d": 3.0, "ret_20d": 12.0, "distance_from_ema20_pct": 4.0,
                        "distance_from_ema20_atr": 1.2, "largest_positive_daily_return_10d": 2.1, "positive_sessions_10": 7.0},
    }
    snapshot = {
        "opportunity_id": candidate["opportunity_id"], "signal_date": candidate["signal_date"], "symbol": "AAA",
        "methodology_id": METHODOLOGY_ID, "methodology_version": "HA-V1-FINAL-EXPANDED-v1",
        "methodology_hash": METHODOLOGY_HASH, "analog_count": 40, "unique_security_count": 34,
        "earliest_analog_date": "2017-01-10", "latest_analog_date": "2022-11-20",
        "maximum_year_share": .35, "maximum_date_share": .05, "evidence_quality": "HIGH",
        "outcome_attractiveness": {"median_mfe_10d": 7.2, "median_mfe_20d": 10.4,
                                   "plus_5_before_minus_3_rate": .625, "median_time_to_mfe_10d": 5.0,
                                   "median_time_to_mfe_20d": 9.0},
        "downside_evidence": {"median_mae_10d": -3.1, "median_mae_20d": -4.4},
        "evidence": {"median_distance": .8, "median_feature_coverage": 1.0},
        "analogs": [{"rank": rank, "opportunity_id": f"H{rank}", "symbol": f"S{rank}",
                     "signal_date": "2021-06-01", "label_end_date": "2021-06-29", "distance": rank / 100,
                     "feature_coverage": 1.0, "ret_10d": 5.0, "nifty500_ret_10d": 1.0,
                     "mfe_10d": 7.0, "mfe_20d": 9.0, "mae_10d": -2.0, "mae_20d": -3.0,
                     "target_5_before_stop_3_20d": 1.0, "time_to_mfe_10d": 4.0, "time_to_mfe_20d": 8.0}
                    for rank in range(1, 41)],
    }
    database.persist_historical_analog_snapshot(snapshot)

    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception and app.radio[0].options == PAGES
    passed += 1  # 1 sidebar routes

    app.radio[0].set_value("Portfolio").run(timeout=30)
    assert not app.exception and app.segmented_control[0].options == ["Performance", "Positions & Risk"]
    passed += 1  # 2 portfolio tabs

    period = next(widget for widget in app.selectbox if widget.label == "P&L period")
    assert period.value == "Lifetime"
    passed += 1  # 3 Lifetime default

    period.set_value("YTD").run(timeout=30)
    assert next(widget for widget in app.selectbox if widget.label == "P&L period").value == "YTD"
    assert "load_portfolio_pnl(period, custom_start=custom_start, custom_end=custom_end)" in (ROOT / "cockpit_ui.py").read_text()
    passed += 1  # 4 filter reaches backend

    trade = database.add_paper_trade("AAA", 100.0, 10, "Donchian Channel Breakout")
    database.persist_position_marks([{"trade_id": trade.id, "symbol": "AAA", "mark_price": 110.0,
        "mark_date": dt.date.today(), "marked_at": dt.datetime.now(dt.timezone.utc), "provider": "FIXTURE", "mark_status": "AVAILABLE"}])
    pnl = get_portfolio_pnl("Lifetime")
    assert pnl["status"] == "AVAILABLE" and sum(row["total_pnl"] for row in pnl["stock_contributions"]) == pnl["total_pnl"] == 100.0
    passed += 1  # 5 contribution reconciliation

    assert stock_url("NSE:AAA") == "?page=stock&symbol=AAA&tab=overview"
    passed += 1  # 6 symbol deep link
    assert stock_url("AAA.NS", "historical_analogs") == "?page=stock&symbol=AAA&tab=historical_analogs"
    passed += 1  # 7 HA deep link

    app.session_state["live_decisions"] = [candidate]
    app.session_state["qualified_candidates"] = [candidate]
    app.query_params["page"] = "stock"; app.query_params["symbol"] = "AAA"; app.query_params["tab"] = "historical_analogs"
    app.run(timeout=30)
    assert not app.exception and app.segmented_control[0].value == "Historical Analogs"
    app.run(timeout=30)
    assert app.segmented_control[0].value == "Historical Analogs"
    passed += 1  # 8 selected tab survives rerun

    source = (ROOT / "cockpit_ui.py").read_text()
    assert all(label not in source for label in ('"Fresh"', '"Developing"', '"Extended"', '"Exhausted"', '"Rally Score"'))
    passed += 1  # 9 rejected P3 labels absent

    rendered = "\n".join(item.value for item in app.markdown)
    captions = "\n".join(item.value for item in app.caption)
    production_service = HistoricalAnalogService()
    assert production_service.production_pool_path.exists() and "data/production" in str(production_service.production_pool_path)
    assert "40 analogs across 34 securities" in captions and "Median 10D upside potential" in rendered
    assert "Historical evidence, not a forecast" in captions
    passed += 1  # 10 frozen snapshot cards

    case_toggle = next(widget for widget in app.toggle if widget.label == "Inspect the 40 closest analog cases")
    case_toggle.set_value(True).run(timeout=30)
    assert app.dataframe and len(app.dataframe[-1].value) == 40
    assert {"Rank", "Historical date", "Historical symbol", "MFE 10D", "MAE 10D"}.issubset(app.dataframe[-1].value.columns)
    passed += 1  # 11 analog cases

    preview = ExecutionAdapter().preview_paper_trade({**candidate, "symbol": "BBB.NS", "opportunity_id": "HA-P2:BBB"}, 1_000.0)
    assert preview["valid"] is True and preview["allocator_selected"] is False
    passed += 1  # 12 HA/advisory state cannot block trade

    before_trades = len(database.get_open_trades())
    app.query_params["tab"] = "trade"; app.run(timeout=30)
    assert not app.exception and len(database.get_open_trades()) == before_trades
    passed += 1  # 13 navigation/render creates no trade

    before_runs = database.load_latest_analysis_run()
    before_marks = database.get_latest_position_marks()
    app.query_params["tab"] = "overview"; app.run(timeout=30)
    assert database.load_latest_analysis_run() == before_runs and database.get_latest_position_marks() == before_marks
    passed += 1  # 14 navigation creates no scan/price refresh

    assert not app.exception
    passed += 1  # 15 Streamlit headless render

    history, _ = production_service._load_train_pool()
    row = history.iloc[-1]
    generic_state = {
        "opportunity_id": "GENERIC:AAA:2026-08-14", "symbol": "AAA", "signal_date": "2026-08-14",
        "qualification_status": "RESEARCH_ONLY", "is_qualified": False,
        "ha_features": {name: float(row[name]) for name in production_service.features},
        "ha_stock_percentiles": {output: float(row[output]) for output in production_service.blends.values()},
    }
    generic_snapshot = production_service.evaluate_generic_state(generic_state)
    assert generic_snapshot["query_scope"] == "GENERIC_RESEARCH_STATE" and generic_snapshot["analog_count"] == 40
    assert database.load_historical_analog_snapshot(
        generic_state["opportunity_id"], generic_state["signal_date"], METHODOLOGY_HASH
    ) is None
    try:
        production_service.evaluate(generic_state, persist=False)
        raise AssertionError("qualified-only HA entrypoint accepted a research-only state")
    except HistoricalAnalogContractError:
        pass
    passed += 1  # 16 generic-state HA is read-only and cannot bypass the qualified contract

    assert passed == 16
    print(f"HA-P2 focused UI tests: PASS ({passed}/16 scenarios)")


if __name__ == "__main__":
    try: run()
    finally: temporary.cleanup()

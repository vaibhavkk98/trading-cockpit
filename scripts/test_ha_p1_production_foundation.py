#!/usr/bin/env python3
"""Focused HA-P1 production, persistence, P&L, and routing acceptance checks."""
import datetime as dt
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
test_dir = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["TRADING_COCKPIT_DB_PATH"] = str(Path(test_dir.name) / "ha_p1.sqlite")

import database  # noqa: E402
from database import (  # noqa: E402
    HistoricalAnalogMapping, HistoricalAnalogSnapshot, PositionMark, SessionLocal,
    add_paper_trade, get_open_trades, load_historical_analog_snapshot,
    persist_position_marks, update_trade_status,
)
from historical_analogs_service import (  # noqa: E402
    HistoricalAnalogContractError, HistoricalAnalogService, METHODOLOGY_HASH, METHODOLOGY_ID,
)
from interaction_architecture import hydrate_navigation_state, navigation_query, navigation_state  # noqa: E402
from portfolio_analytics import get_portfolio_pnl, resolve_period  # noqa: E402


def run() -> None:
    passes = 0
    artifact = ROOT / "data/research/historical_analogs/ha_v1_final_expanded"
    service = HistoricalAnalogService(artifact)
    assert service.contract["candidate_id"] == METHODOLOGY_ID and len(service.features) == 23
    assert METHODOLOGY_HASH == "c886a21dac9e14a3e6dadfa167b83f5066a8b63b1367b6e426d8da59c967407f"
    passes += 1  # 1 frozen contract/hash

    blend_columns = list(service.blends.values())
    columns = [*service.features, *blend_columns]
    fixture = pq.read_table(
        artifact / "expanded_opportunity_states.parquet", columns=columns,
        filters=[("signal_date", "<", dt.datetime(2023, 1, 1))],
    ).slice(500, 1).to_pandas().iloc[0]
    opportunity = {
        "opportunity_id": "LIVE:ABC:2026-08-14", "symbol": "ABC.NS", "signal_date": "2026-08-14",
        "qualification_status": "QUALIFIED", "allocation_status": "NOT_ALLOCATED",
        "ha_features": {name: float(fixture[name]) for name in service.features},
        "ha_stock_percentiles": {name: float(fixture[name]) for name in blend_columns},
    }
    result = service.evaluate(opportunity)
    assert result["analog_count"] == 40 and result["methodology_hash"] == METHODOLOGY_HASH
    assert result["latest_analog_date"] < "2023-01-01"
    assert result["evidence"]["validation_outcomes_accessed"] is False
    assert result["evidence"]["final_test_outcomes_accessed"] is False
    passes += 1  # 2 causal retrieval and sealed boundaries

    assert set(result["outcome_attractiveness"]) == {
        "median_mfe_10d", "median_mfe_20d", "plus_5_before_minus_3_rate",
        "median_time_to_mfe_10d", "median_time_to_mfe_20d",
    }
    assert set(result["downside_evidence"]) == {"median_mae_10d", "median_mae_20d"}
    assert result["evidence_quality"] in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
    years = Counter(row["signal_date"][:4] for row in result["analogs"])
    dates = Counter(row["signal_date"] for row in result["analogs"])
    assert result["maximum_year_share"] == max(years.values()) / 40
    assert result["maximum_date_share"] == max(dates.values()) / 40
    passes += 1  # 3 outcome/downside/evidence separation

    before_allocation = opportunity["allocation_status"]
    assert result["analogs"][0]["rank"] == 1 and opportunity["allocation_status"] == before_allocation
    assert all(analog["signal_date"] < opportunity["signal_date"] and analog["label_end_date"] < opportunity["signal_date"] for analog in result["analogs"])
    passes += 1  # 4 ranked mapping and advisory-only behavior

    first_snapshot_id = result["persistence"]["snapshot_id"]
    repeated = service.evaluate(opportunity)
    assert repeated["persistence"] == {"saved": False, "snapshot_id": first_snapshot_id}
    session = SessionLocal()
    try:
        assert session.query(HistoricalAnalogSnapshot).count() == 1
        assert session.query(HistoricalAnalogMapping).count() == 40
    finally:
        session.close()
    loaded = load_historical_analog_snapshot(opportunity["opportunity_id"], opportunity["signal_date"], METHODOLOGY_HASH)
    assert loaded and len(loaded["analogs"]) == 40 and loaded["analogs"][0]["rank"] == 1
    passes += 1  # 5 immutable/idempotent/inspectable snapshots

    try:
        service.evaluate({**opportunity, "qualification_status": "REJECTED", "is_qualified": False}, persist=False)
        raise AssertionError("unqualified opportunity was accepted")
    except HistoricalAnalogContractError:
        pass
    try:
        service.evaluate({k: v for k, v in opportunity.items() if k != "ha_features"}, persist=False)
        raise AssertionError("incomplete causal feature state was accepted")
    except HistoricalAnalogContractError:
        pass
    passes += 1  # 6 eligibility and query-state enforcement

    trade_a = add_paper_trade("AAA", 90.0, 10, "Donchian Channel Breakout")
    trade_b = add_paper_trade("BBB", 50.0, 10, "EMA Pullback / Bounce")
    session = SessionLocal()
    try:
        session.query(database.PaperTrade).filter_by(id=trade_a.id).one().entry_date = dt.datetime(2025, 12, 1, tzinfo=dt.timezone.utc)
        session.query(database.PaperTrade).filter_by(id=trade_b.id).one().entry_date = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
        session.commit()
    finally:
        session.close()
    assert update_trade_status(trade_b.id, "CLOSED_MANUAL", 60.0, dt.datetime(2026, 1, 20, tzinfo=dt.timezone.utc))
    persist_position_marks([
        {"trade_id": trade_a.id, "symbol": "AAA", "mark_price": 100.0, "mark_date": "2025-12-31",
         "marked_at": dt.datetime(2025, 12, 31, 12, tzinfo=dt.timezone.utc), "provider": "FIXTURE", "mark_status": "AVAILABLE"},
        {"trade_id": trade_a.id, "symbol": "AAA", "mark_price": 120.0, "mark_date": "2026-01-31",
         "marked_at": dt.datetime(2026, 1, 31, 12, tzinfo=dt.timezone.utc), "provider": "FIXTURE", "mark_status": "AVAILABLE"},
    ])
    ytd = get_portfolio_pnl("YTD", as_of_date="2026-01-31")
    assert ytd["status"] == "AVAILABLE" and ytd["total_pnl"] == 300.0
    assert ytd["realized_pnl"] == 100.0 and ytd["unrealized_or_mark_contribution"] == 200.0
    assert ytd["trade_count"] == 2
    passes += 1  # 7 mark-based period attribution

    assert sum(row["total_pnl"] for row in ytd["stock_contributions"]) == ytd["total_pnl"]
    assert {row["symbol"] for row in ytd["stock_contributions"]} == {"AAA", "BBB"}
    lifetime = get_portfolio_pnl("LIFETIME", as_of_date="2026-01-31")
    assert lifetime["total_pnl"] == 400.0 and lifetime["realized_pnl"] == 100.0
    passes += 1  # 8 stock and lifetime reconciliation

    trade_c = add_paper_trade("CCC", 80.0, 5, "Donchian Channel Breakout")
    session = SessionLocal()
    try:
        session.query(database.PaperTrade).filter_by(id=trade_c.id).one().entry_date = dt.datetime(2025, 12, 15, tzinfo=dt.timezone.utc)
        session.commit()
    finally:
        session.close()
    incomplete = get_portfolio_pnl("YTD", as_of_date="2026-01-31")
    assert incomplete["status"] == "NOT_AVAILABLE" and incomplete["total_pnl"] is None
    assert incomplete["return_pct"] is None and incomplete["coverage"]["missing_position_count"] == 1
    passes += 1  # 9 explicit insufficient mark coverage

    expected_starts = {
        "YTD": "2026-01-01", "1Y": "2025-08-14", "6M": "2026-02-14",
        "3M": "2026-05-14", "1M": "2026-07-14",
    }
    for period, expected in expected_starts.items():
        assert resolve_period(period, "2026-08-14")[0].isoformat() == expected
    assert resolve_period("CUSTOM", custom_start="2026-03-01", custom_end="2026-04-01")[0].isoformat() == "2026-03-01"
    passes += 1  # 10 all requested date filters

    before_navigation = len(get_open_trades())
    state = {}
    route = hydrate_navigation_state(state, {"page": "stock", "symbol": "NSE:reliance", "tab": "historical_analogs"})
    assert route == {"page": "stock", "symbol": "RELIANCE", "tab": "historical_analogs"}
    assert hydrate_navigation_state(state, {}) == route
    assert navigation_query("stock", "RELIANCE.NS", "trade") == {"page": "stock", "symbol": "RELIANCE", "tab": "trade"}
    assert len(get_open_trades()) == before_navigation
    passes += 1  # 11 deep link survives state hydration and cannot trade

    assert navigation_state({"page": "stock", "symbol": "bad symbol", "tab": "rally"})["page"] == "today"
    assert navigation_state({"page": "portfolio", "symbol": "AAA", "tab": "trade"}) == {"page": "portfolio", "symbol": None, "tab": "overview"}
    assert "confirm" not in navigation_query("stock", "AAA", "overview")
    passes += 1  # 12 invalid routes fail closed; execution state excluded

    assert passes == 12
    print(f"HA-P1 production foundation tests: PASS ({passes}/12 scenarios)")


if __name__ == "__main__":
    try:
        run()
    finally:
        test_dir.cleanup()

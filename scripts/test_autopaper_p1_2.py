#!/usr/bin/env python3
"""Focused AutoPaper-P1.2 Friday cohort bootstrap acceptance test."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "autopaper_p1_2_test.db"
if DB.exists():
    DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
from autopaper_friday_bootstrap import (
    AMENDED_CONFIG_HASH, AMENDMENT_ID, BootstrapNoGo, FIRST_EXECUTION_DATE,
    SIGNAL_DATE, execute_friday_bootstrap,
)
from autopaper_ui_service import load_autopaper_ui_state


passed = 0


def check(condition):
    global passed
    assert condition
    passed += 1


def main():
    completed = dt.datetime(2026, 9, 11, 11, 0, tzinfo=dt.timezone.utc)
    opportunities = []
    for rank, (symbol, strategy, sector) in enumerate((
        ("AAA.NS", "Donchian Channel Breakout", "Industrials"),
        ("BBB.NS", "EMA Pullback", "Financial Services"),
        ("CCC.NS", "VCP", "Healthcare"),
    ), 1):
        opportunities.append({
            "opportunity_id": f"2026-09-11:{symbol[:-3]}:{strategy}",
            "symbol": symbol, "signal_date": SIGNAL_DATE.isoformat(), "strategy": strategy,
            "entry_price": 100.0, "atr_20": 4.0, "current_volume": 1_000_000,
            "sector": sector, "qualification_status": "QUALIFIED",
            "opportunity_priority_rank": rank,
        })
    database.persist_analysis_run({
        "run_id": "EOD-2026-09-11", "analysis_date": SIGNAL_DATE,
        "started_at": completed - dt.timedelta(minutes=3), "completed_at": completed,
        "status": "SUCCESS", "qualified_count": len(opportunities),
        "decision_contract_version": "V1_1_EOD", "source": "AUTOMATED_EOD",
    }, opportunities)

    result = execute_friday_bootstrap(
        amendment_timestamp=dt.datetime(2026, 9, 14, 2, 45, tzinfo=dt.timezone.utc),
        code_commit="TEST")
    check(result["status"] == "COMPLETED")
    check(result["signal_date"] == "2026-09-11")
    check(result["first_execution_date"] == "2026-09-14")
    check(result["canonical_friday_run"]["run_id"] == "EOD-2026-09-11")
    check(result["canonical_friday_run"]["qualified_count"] == 3)
    check(result["precondition_counts"]["orders"] == 0)
    check(result["monday_data_loaded"] is False)
    check(result["friday_fills"] == 0)
    check(result["historical_backfill"] is False and result["historical_holdout_opened"] is False)
    check(result["baseline_methodology_hash"] == engine.METHODOLOGY_HASH)
    check(result["old_config_hash"] == engine.CONFIG_HASH)
    check(result["amended_config_hash"] == AMENDED_CONFIG_HASH != engine.CONFIG_HASH)
    check(result["methodology_changed"] is False)
    check(set(result["accounts"]) == set(engine.ACCOUNTS))
    check(result["pending_entries_by_account"]["BASELINE_C3"] == 3)
    check(result["pending_entries_by_account"]["SHADOW_D1"] == 2)

    session = database.SessionLocal()
    try:
        check(session.query(database.AutoPaperAccount).count() == 4)
        check(session.query(database.AutoPaperPosition).count() == 0)
        check(session.query(database.AutoPaperTrade).count() == 0)
        check(session.query(database.AutoPaperExecutionTelemetry).count() == 0)
        check(session.query(database.AutoPaperCounterfactualLink).count() == 12)
        check(session.query(database.AutoPaperCounterfactualLink).filter_by(origin="FRIDAY_BOOTSTRAP").count() == 12)
        check(session.query(database.AutoPaperOrder).filter_by(status="PENDING").count() == 11)
        check(all(row.quantity is None for row in session.query(database.AutoPaperOrder).all()))
        check(all(json.loads(row.payload).get("intended_execution_date") == "2026-09-14"
                  for row in session.query(database.AutoPaperOrder).all()))
        check(session.query(database.AutoPaperOpportunityMetadata).count() == 3)
        check(all(row.captured_at.date() == SIGNAL_DATE for row in session.query(database.AutoPaperOpportunityMetadata).all()))
        check(session.query(database.AutoPaperRiskTelemetry).count() == 4)
        check(all(json.loads(row.payload)["portfolio_correlation"]["status"] == "NOT_AVAILABLE"
                  for row in session.query(database.AutoPaperRiskTelemetry).all()))
        amendment = session.get(database.AutoPaperActivationAmendment, AMENDMENT_ID)
        check(amendment is not None and amendment.methodology_hash == engine.METHODOLOGY_HASH)
        check(amendment.first_execution_date == FIRST_EXECUTION_DATE)
        for account_id in engine.ACCOUNTS:
            ranks = sorted(json.loads(row.payload).get("rank") for row in session.query(database.AutoPaperDecision).filter_by(
                account_id=account_id).all() if json.loads(row.payload).get("rank") is not None)
            check(ranks == [1, 2, 3])
    finally:
        session.close()

    state = load_autopaper_ui_state()
    check(state["health"]["market_date"] == "2026-09-11")
    check(state["decision_funnel"]["market_date"] == "2026-09-11")
    check(len(state["orders"]["pending_entries"]) == 3 and not state["positions"])
    check(state["activation_amendment"]["id"] == AMENDMENT_ID)
    check(state["review_gate"]["rows"][1]["value"] == 1)

    retry = execute_friday_bootstrap(code_commit="TEST-RETRY")
    check(retry["status"] == "IDEMPOTENT_NOOP" and retry["idempotent"])
    session = database.SessionLocal()
    try:
        check(session.query(database.AutoPaperOrder).count() == 11)
        session.delete(session.get(database.AutoPaperActivationAmendment, AMENDMENT_ID))
        session.commit()
    finally:
        session.close()
    try:
        execute_friday_bootstrap(code_commit="UNSAFE-RETRY")
    except BootstrapNoGo as exc:
        check("ACTIVITY_ALREADY_EXISTS" in str(exc))
    else:
        raise AssertionError("non-zero prospective activity was not blocked")

    source = (ROOT / "autopaper_friday_bootstrap.py").read_text()
    check("yfinance" not in source and "download(" not in source)
    check("_process_account(\n            account_id, decisions, {}, SIGNAL_DATE" in source)
    print(f"AutoPaper-P1.2 focused tests: {passed} passed")


if __name__ == "__main__":
    main()

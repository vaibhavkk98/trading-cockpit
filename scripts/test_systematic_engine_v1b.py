#!/usr/bin/env python3
"""Focused Systematic Engine V1B acceptance tests."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "systematic_engine_v1b_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
import portfolio_risk_engine as risk
from autopaper_friday_bootstrap import SIGNAL_DATE, execute_friday_bootstrap
from autopaper_portfolio_risk_activation import activate_portfolio_risk
from autopaper_ui_service import humanize_reason, load_autopaper_ui_state

passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def candidate(symbol, date, rank=1, atr=4., sector="Industrials"):
    return {"opportunity_id": f"{date}:{symbol}:VCP", "symbol": f"{symbol}.NS",
        "signal_date": str(date), "strategy": "VCP", "entry_price": 100., "atr_20": atr,
        "current_volume": 1_000_000, "sector": sector, "qualification_status": "QUALIFIED",
        "opportunity_priority_rank": rank}


def frame(symbols, end="2026-10-15", periods=55, opposing=False):
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=periods)
    result = {}
    for index, symbol in enumerate(symbols):
        slope = -1 if opposing and index % 2 else 1
        close = 100 + slope * np.linspace(0, 10, len(dates))
        result[f"{symbol}.NS"] = pd.DataFrame({"Open": close, "High": close + 1,
            "Low": close - 1, "Close": close, "Volume": 1_000_000.}, index=dates)
    return result


def seed_friday():
    rows = [candidate("ALOW", SIGNAL_DATE, 1, 2., "Industrials"),
            candidate("BHIGH", SIGNAL_DATE, 2, 8., "Industrials")]
    rows.extend(candidate(f"F{i:02d}", SIGNAL_DATE, i + 2, 4., "Industrials") for i in range(1, 10))
    completed = dt.datetime(2026, 9, 13, 9, tzinfo=dt.timezone.utc)
    database.persist_analysis_run({"run_id": "EOD-2026-09-11", "analysis_date": SIGNAL_DATE,
        "started_at": completed - dt.timedelta(minutes=1), "completed_at": completed,
        "status": "SUCCESS", "qualified_count": len(rows),
        "decision_contract_version": "TRADING_COCKPIT_V1_1_EOD", "source": "AUTOMATED_EOD"}, rows)
    return rows


def core_fingerprint():
    session = database.SessionLocal()
    try:
        core = (*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID, *engine.EDGE_ACCOUNT_CONFIGS)
        return engine._hash({"accounts": [(x.account_id, x.cash, x.last_market_date, x.state_version)
            for x in session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_(core)).order_by(database.AutoPaperAccount.account_id)],
            "orders": sorted(x.payload_hash for x in session.query(database.AutoPaperOrder).filter(
                database.AutoPaperOrder.account_id.in_(core))),
            "decisions": sorted(x.payload_hash for x in session.query(database.AutoPaperDecision).filter(
                database.AutoPaperDecision.account_id.in_(core)))})
    finally:
        session.close()


def main():
    check(engine.METHODOLOGY_HASH == "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79")
    check(set(engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS) == {"SHADOW_RISK_BUDGET", "SHADOW_RISK_DIVERSIFIED"})
    check(all(config["hold_sessions"] == 10 for config in engine.PORTFOLIO_RISK_CONFIGS.values()))
    check(all(config["replacement"] is False for config in engine.PORTFOLIO_RISK_CONFIGS.values()))
    check(all(config["pb_r2_authority"] is False and config["path_risk_authority"] is False
              for config in engine.PORTFOLIO_RISK_CONFIGS.values()))
    check(engine.PORTFOLIO_RISK_CONFIGS["SHADOW_RISK_BUDGET"]["redundancy"] is False)
    check(engine.PORTFOLIO_RISK_CONFIGS["SHADOW_RISK_DIVERSIFIED"]["redundancy"] is True)
    check(risk.TARGET_POSITION_RISK == .004 and risk.NORMAL_HEAT_LIMIT == .036 and risk.HARD_HEAT_LIMIT == .04)
    check(risk.RV20_MIN_RETURNS == 15 and risk.CORRELATION_MIN_OVERLAP == 10)

    histories = frame(["A", "B"], end="2026-09-11", periods=30)
    rv = risk.realized_volatility_20(histories, "A.NS", SIGNAL_DATE)
    check(rv["valid_returns"] == 20 and isinstance(rv["rv20_pct"], float))
    future = histories["A.NS"].copy()
    future.loc[pd.Timestamp("2026-09-14")] = [500, 501, 499, 500, 1_000_000]
    causal = dict(histories); causal["A.NS"] = future
    check(risk.realized_volatility_20(causal, "A.NS", SIGNAL_DATE)["rv20_pct"] == rv["rv20_pct"])
    short = frame(["SHORT"], end="2026-09-11", periods=15)
    check(risk.realized_volatility_20(short, "SHORT.NS", SIGNAL_DATE)["rv20_pct"] == "NOT_AVAILABLE")
    check(risk.candidate_risk_proxy(4., 6.)["risk_proxy_pct"] == 6.)
    check(risk.candidate_risk_proxy(4., "NOT_AVAILABLE")["risk_proxy_coverage"] == "ATR_ONLY")
    check(risk.candidate_risk_proxy("NOT_AVAILABLE", 6.)["eligible"] is False)
    positive = risk.causal_correlation(histories, "A.NS", "B.NS", SIGNAL_DATE)
    check(round(positive["correlation"], 8) == 1. and positive["overlap"] == 20)
    weighted = risk.weighted_average_correlation(histories, "A.NS",
        [{"symbol": "B.NS", "risk_rupees": 4_000}], SIGNAL_DATE)
    check(weighted["correlation_multiplier"] == 1.5)
    opposite = frame(["A", "B"], end="2026-09-11", periods=30)
    alternating = np.array([.01 if index % 2 else -.01 for index in range(30)])
    opposite["A.NS"]["Close"] = 100 * np.cumprod(1 + alternating)
    opposite["B.NS"]["Close"] = 100 * np.cumprod(1 - alternating)
    weighted_negative = risk.weighted_average_correlation(opposite, "A.NS",
        [{"symbol": "B.NS", "risk_rupees": 4_000}], SIGNAL_DATE)
    check(weighted_negative["correlation_multiplier"] == 1.)
    unavailable = risk.weighted_average_correlation(short, "SHORT.NS",
        [{"symbol": "MISSING.NS", "risk_rupees": 4_000}], SIGNAL_DATE)
    check(unavailable["correlation_state"] == "NOT_AVAILABLE" and unavailable["correlation_multiplier"] == 1.)

    rows = seed_friday(); execute_friday_bootstrap(code_commit="RISK-TEST")
    before = core_fingerprint()
    activation = activate_portfolio_risk(
        activation_timestamp=dt.datetime(2026, 9, 14, 3, tzinfo=dt.timezone.utc), code_commit="RISK-TEST")
    check(activation["status"] == "ACTIVE" and activation["activation_mode"] == "FRIDAY_BOOTSTRAP")
    check(activation["provenance"] == "FRIDAY_BOOTSTRAP_PORTFOLIO_RISK")
    check(activation["historical_holdout_opened"] is False)
    check(core_fingerprint() == before)
    check(all(value["cash"] == 1_000_000 and value["positions"] == 0 and value["pending_entries"] == 2
              for value in activation["initial_state"].values()))
    check(all(value["decision_snapshots"] == 11 for value in activation["initial_state"].values()))
    session = database.SessionLocal()
    try:
        check(session.query(database.PortfolioRiskMatch).count() == 33)
        check(session.query(database.PortfolioRiskDecisionSnapshot).count() == 22)
        b1_orders = session.query(database.AutoPaperOrder).filter_by(
            account_id="SHADOW_RISK_BUDGET", side="BUY", status="PENDING").all()
        amounts = {order.symbol: order.requested_capital for order in b1_orders}
        check(round(amounts["ALOW.NS"], 2) == 100_000 and round(amounts["BHIGH.NS"], 2) == 50_000)
        check(all(_payload.get("risk_proxy_coverage") == "ATR_ONLY" for _payload in
            (json.loads(row.payload) for row in session.query(database.PortfolioRiskDecisionSnapshot).filter_by(
                final_decision="ADMIT").all())))
        first_hash = session.query(database.PortfolioRiskDecisionSnapshot).first().payload_hash
    finally:
        session.close()
    retry = activate_portfolio_risk(code_commit="RISK-RETRY")
    check(retry["status"] == "IDEMPOTENT_NOOP" and retry["idempotent"])
    session = database.SessionLocal()
    try:
        check(session.query(database.PortfolioRiskDecisionSnapshot).first().payload_hash == first_hash)
    finally:
        session.close()

    fill_date = dt.date(2026, 9, 14)
    live_histories = frame(["ALOW", "BHIGH", *[f"F{i:02d}" for i in range(1, 10)]], end="2026-09-14")
    for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS:
        engine._process_account(account_id, [], live_histories, fill_date,
            dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc))
    session = database.SessionLocal()
    try:
        for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS:
            positions = session.query(database.AutoPaperPosition).filter_by(account_id=account_id, status="OPEN").all()
            check(len(positions) == 2)
            check(all(json.loads(position.payload).get("risk_proxy_pct") for position in positions))
            state = risk.portfolio_heat_state(positions, 1_000_000)
            check(0 < state["portfolio_heat_pct"] <= 4.)
        check(session.query(database.PortfolioRiskMatch).filter_by(
            account_id="SHADOW_RISK_BUDGET", entered=True).count() == 2)
    finally:
        session.close()

    # Multiple same-sector admissions consume B2's frozen 35%-of-normal-heat
    # allowance without reordering the stream.
    next_date = dt.date(2026, 9, 15)
    new_rows = [candidate(f"NEW{i}", next_date, i, 4., "Industrials") for i in range(1, 4)]
    next_histories = frame([*(row["symbol"].removesuffix(".NS") for row in new_rows), "ALOW", "BHIGH"],
        end="2026-09-15")
    result_b2 = engine._process_account("SHADOW_RISK_DIVERSIFIED", new_rows, next_histories, next_date,
        dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc))
    check(result_b2["admission_used"] <= 2 and result_b2["portfolio_heat_pct"] <= 4.)
    session = database.SessionLocal()
    try:
        decisions = session.query(database.PortfolioRiskDecisionSnapshot).filter_by(
            account_id="SHADOW_RISK_DIVERSIFIED", market_date=next_date).all()
        new_decisions = [row for row in decisions if row.opportunity_id.startswith("2026-09-15:")]
        check(len(new_decisions) == 3)
        check(any(row.final_decision in {"ADMIT_DOWNSIZED", "DEFER"} for row in new_decisions))
        check(all(json.loads(row.payload).get("weighted_avg_corr") != "NOT_EVALUATED"
                  for row in new_decisions if row.final_decision.startswith("ADMIT")))
        check(session.query(database.PortfolioRiskTelemetry).filter_by(
            account_id="SHADOW_RISK_DIVERSIFIED", market_date=next_date).count() == 1)
    finally:
        session.close()
    check("35%" in humanize_reason("SECTOR_RISK_CONCENTRATION"))

    ui = load_autopaper_ui_state()
    check(ui["portfolio_risk"]["activation"]["status"] == "ACTIVE")
    check(set(ui["portfolio_risk"]["accounts"]) == set(engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS))
    check(ui["portfolio_risk"]["matched_groups"] >= 11)
    check(all((ui["accounts"][account] or {}).get("cash", -1) >= 0
              for account in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS))
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    check(not app.exception)
    check(any("Portfolio Risk Engine" in str(item.value) for item in app.markdown))
    learning = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    learning.query_params["page"] = "learning"; learning.run(timeout=30)
    check(not learning.exception)

    # Case B: once Monday execution has been consumed, only a future cohort may activate.
    database.Base.metadata.drop_all(bind=database.engine); database.Base.metadata.create_all(bind=database.engine)
    engine._manifest_and_accounts()
    session = database.SessionLocal()
    try:
        session.get(database.AutoPaperAccount, "BASELINE_C3").last_market_date = dt.date(2026, 9, 14)
        session.commit()
    finally:
        session.close()
    deferred = activate_portfolio_risk(
        activation_timestamp=dt.datetime(2026, 9, 14, 13, tzinfo=dt.timezone.utc), code_commit="RISK-CASE-B")
    check(deferred["status"] == "PENDING_NEXT_COHORT")
    check(deferred["activation_signal_date"] is None and deferred["after_market_date"] == "2026-09-14")
    prospective_date = dt.date(2026, 9, 15)
    prospective = engine.run_prospective_autopaper([candidate("NEXT", prospective_date)],
        frame(["NEXT"], end="2026-09-15"), prospective_date,
        dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc), "RISK-NEXT")
    check(all(prospective["accounts"][account]["admission_used"] == 1
              for account in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS))
    session = database.SessionLocal()
    try:
        row = session.get(database.PortfolioRiskActivation, "PORTFOLIO_RISK_V1B_ACTIVATION")
        check(row.status == "ACTIVE" and row.activation_signal_date == prospective_date)
        check(all(session.query(database.AutoPaperCounterfactualLink).filter_by(
            account_id=account, origin="PORTFOLIO_RISK_PROSPECTIVE").count() == 1
            for account in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS))
    finally:
        session.close()
    print(f"Systematic Engine V1B focused tests: {passed} passed")


if __name__ == "__main__":
    main()

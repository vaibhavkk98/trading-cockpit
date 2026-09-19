#!/usr/bin/env python3
"""Focused Systematic Engine V1D acceptance tests."""
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
DB = Path(tempfile.gettempdir()) / "systematic_engine_v1d_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as prospective
import dynamic_exposure_engine as exposure
from autopaper_ui_service import humanize_reason, load_autopaper_ui_state

passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def histories(symbols, end="2026-09-18", benchmark_down=True):
    dates = pd.bdate_range(end=end, periods=330); result = {}
    for symbol in symbols:
        close = 100 + np.linspace(0, 20, len(dates))
        result[f"{symbol}.NS"] = pd.DataFrame({"Open": close, "High": close + 1,
            "Low": close - 1, "Close": close, "Volume": 2_000_000.}, index=dates)
    benchmark = np.linspace(140, 100, len(dates)) if benchmark_down else np.linspace(100, 140, len(dates))
    # deterministic varying returns avoid a degenerate volatility percentile
    benchmark *= 1 + .002 * np.sin(np.arange(len(dates)) / 3)
    if benchmark_down: benchmark[-1] *= .85
    result["NIFTY500"] = pd.DataFrame({"Open": benchmark, "High": benchmark + 1,
        "Low": benchmark - 1, "Close": benchmark, "Volume": 1_000_000.}, index=dates)
    return result


def candidate(symbol, date, rank):
    return {"opportunity_id": f"{date}:{symbol}:VCP", "symbol": f"{symbol}.NS",
        "signal_date": str(date), "strategy": "VCP", "entry_price": 90_000.,
        "atr_20": 3_600., "current_volume": 2_000_000, "sector": "Industrials",
        "qualification_status": "QUALIFIED", "opportunity_priority_rank": rank}


def main():
    expected = {0.: 1., -.05: 1., -.075: .9, -.10: .8, -.125: .65,
                -.15: .5, -.175: .375, -.20: .25, -.30: .25}
    for drawdown, multiplier in expected.items():
        check(abs(exposure.portfolio_multiplier(drawdown) - multiplier) < 1e-12)
    check(exposure.trend_multiplier(.01) == 1.)
    check(exposure.trend_multiplier(-.025) == .875)
    check(exposure.trend_multiplier(-.06) == .75)
    check(exposure.volatility_multiplier(60) == 1.)
    check(exposure.volatility_multiplier(80) == .9)
    check(exposure.volatility_multiplier(95) == .8)
    check(exposure.combined_multiplier(.25, .75) == .20)
    check(abs(exposure.combined_multiplier(.8, .75) - .6) < 1e-12)
    check(abs(exposure.BASE_NORMAL_HEAT * .6 - .0216) < 1e-12)

    as_of = dt.date(2026, 9, 18)
    data = histories(["A", "B"])
    market = exposure.market_state(data, as_of)
    check(market["status"] == "AVAILABLE" and market["observations"] == 330)
    check(market["trend_multiplier"] == .75)
    future = {key: value.copy() for key, value in data.items()}
    future["NIFTY500"].loc[pd.Timestamp("2026-09-21")] = [500, 501, 499, 500, 1]
    check(exposure.market_state(future, as_of) == market)
    short_dates = pd.bdate_range(end=as_of, periods=80)
    short = {"NIFTY500": pd.DataFrame({"Close": np.arange(80) + 100}, index=short_dates)}
    check(exposure.market_state(short, as_of)["status"] == "NOT_AVAILABLE")
    fallback = exposure.exposure_state(800_000, 1_000_000, short, as_of, True)
    check(fallback["market_fallback"] and abs(fallback["combined_multiplier"] - .25) < 1e-12)
    d2 = exposure.exposure_state(750_000, 1_000_000, data, as_of, True)
    check(abs(d2["combined_multiplier"] - .20) < 1e-12 and
          abs(d2["dynamic_normal_heat_pct"] - .72) < 1e-12)
    d1 = exposure.exposure_state(875_000, 1_000_000, data, as_of, False)
    check(abs(d1["portfolio_multiplier"] - .65) < 1e-12 and
          abs(d1["dynamic_normal_heat_pct"] - 2.34) < 1e-12)

    check(set(prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS) == {
        "SHADOW_EXPOSURE_PORTFOLIO", "SHADOW_EXPOSURE_COMBINED"})
    check(all(config["target_position_risk"] == .004 for config in exposure.CONFIGS.values()))
    check(all(config["hold_sessions"] == 10 and config["replacement"] is False
              for config in exposure.CONFIGS.values()))
    check(all(config["decision_authority"] is False for config in exposure.CONFIGS.values()))
    check(prospective.METHODOLOGY_HASH ==
          "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79")

    database.init_db(); prospective._manifest_and_accounts()
    session = database.SessionLocal()
    try:
        phase_b = {"status": "ACTIVE", "activation_signal_date": as_of.isoformat()}
        session.add(database.PortfolioRiskActivation(
            activation_id="PORTFOLIO_RISK_V1B_ACTIVATION", status="ACTIVE",
            activation_mode="SAME_COHORT",
            activation_timestamp=dt.datetime(2026, 9, 18, 8, tzinfo=dt.timezone.utc),
            activation_signal_date=as_of, after_market_date=None, provenance="TEST",
            payload=prospective._json(phase_b), payload_hash=prospective._hash(phase_b)))
        session.commit()
    finally: session.close()
    activation = prospective.ensure_dynamic_exposure_activation(
        as_of, dt.datetime(2026, 9, 18, 9, tzinfo=dt.timezone.utc))
    check(activation["status"] == "ACTIVE" and activation["activation_mode"] == "SAME_D0_COHORT")
    prospective.ensure_dynamic_exposure_accounts(
        dt.datetime(2026, 9, 18, 9, tzinfo=dt.timezone.utc), as_of)
    session = database.SessionLocal()
    try:
        for account_id in prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS:
            # Prior completed-session peak creates a causally known stress state.
            payload = {"test_peak": True}
            session.add(database.AutoPaperPortfolioSnapshot(account_id=account_id,
                market_date=as_of - dt.timedelta(days=1), cash=2_000_000., nav=2_000_000.,
                open_positions=0, state_fingerprint=prospective._hash([account_id, "peak"]),
                payload=prospective._json(payload)))
        session.commit()
    finally: session.close()
    rows = [candidate("A", as_of, 1), candidate("B", as_of, 2)]
    results = {}
    for account_id in prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS:
        results[account_id] = prospective._process_account(account_id, rows, data, as_of,
            dt.datetime(2026, 9, 18, 12, tzinfo=dt.timezone.utc))
    check(results["SHADOW_EXPOSURE_PORTFOLIO"]["exposure_multiplier"] == .25)
    check(results["SHADOW_EXPOSURE_COMBINED"]["exposure_multiplier"] == .20)
    session = database.SessionLocal()
    try:
        decisions = session.query(database.DynamicExposureDecisionSnapshot).all()
        check(len(decisions) == 4)
        d2_decisions = [row for row in decisions if row.account_id == "SHADOW_EXPOSURE_COMBINED"]
        check(any(row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED" for row in d2_decisions))
        deferred = next(row for row in d2_decisions if row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED")
        check(session.query(database.AutoPaperQueueItem).filter_by(
            account_id=deferred.account_id, opportunity_id=deferred.opportunity_id,
            status="ACTIVE").count() == 1)
        check(all(json.loads(row.payload)["existing_position_action"] == "UNCHANGED_H10"
                  for row in decisions))
        check(session.query(database.DynamicExposureTelemetry).count() == 2)
        check(session.query(database.AutoPaperTrade).filter(
            database.AutoPaperTrade.account_id.in_(tuple(prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS))).count() == 0)
        check(all(session.get(database.AutoPaperAccount, account_id).initial_capital == 1_000_000
                  for account_id in prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS))
    finally: session.close()
    check("remains queued" in humanize_reason("DYNAMIC_EXPOSURE_DEFERRED"))
    state = load_autopaper_ui_state()
    check(state["dynamic_exposure"]["activation"]["status"] == "ACTIVE")
    check(set(state["dynamic_exposure"]["accounts"]) == set(prospective.DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS))
    check(state["dynamic_exposure"]["review_gate"]["intervention_events"]["target"] == 30)
    check(state["dynamic_exposure"]["accounts"]["SHADOW_EXPOSURE_COMBINED"]["deferred"] == 1)

    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    check(not app.exception)
    check(any("Dynamic Exposure Engine" in str(item.value) for item in app.markdown))
    learning = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    learning.query_params["page"] = "learning"; learning.run(timeout=30)
    check(not learning.exception)
    check(any("Dynamic Exposure Engine prospective evidence" in str(item.label)
              for item in learning.expander))
    print(f"Systematic Engine V1D focused tests: {passed} passed")


if __name__ == "__main__":
    main()

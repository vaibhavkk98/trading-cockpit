#!/usr/bin/env python3
"""Focused AutoPaper-P1.3 rolling-admission shadow acceptance tests."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "autopaper_p1_3_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
from autopaper_friday_bootstrap import SIGNAL_DATE, execute_friday_bootstrap
from autopaper_rolling_activation import ACTIVATION_ID, activate_rolling_shadow
from autopaper_ui_service import humanize_reason, load_autopaper_ui_state


passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def candidate(symbol, date, rank, strategy="Donchian Channel Breakout"):
    return {"opportunity_id": f"{date}:{symbol}:{strategy}", "symbol": f"{symbol}.NS",
        "signal_date": str(date), "strategy": strategy, "entry_price": 100., "atr_20": 4.,
        "current_volume": 1_000_000, "sector": "Industrials", "qualification_status": "QUALIFIED",
        "opportunity_priority_rank": rank}


def seed_friday():
    rows = [candidate(f"F{i:02d}", SIGNAL_DATE, i) for i in range(1, 12)]
    completed = dt.datetime(2026, 9, 13, 9, 0, tzinfo=dt.timezone.utc)
    database.persist_analysis_run({"run_id": "EOD-2026-09-11", "analysis_date": SIGNAL_DATE,
        "started_at": completed - dt.timedelta(minutes=2), "completed_at": completed,
        "status": "SUCCESS", "qualified_count": 11,
        "decision_contract_version": "TRADING_COCKPIT_V1_1_EOD", "source": "AUTOMATED_EOD"}, rows)
    return rows


def histories(symbols):
    dates = pd.bdate_range("2026-09-11", periods=18)
    frame = pd.DataFrame({"Open": [100 + i for i in range(len(dates))],
        "High": [102 + i for i in range(len(dates))], "Low": [99 + i for i in range(len(dates))],
        "Close": [101 + i for i in range(len(dates))], "Volume": [1_000_000] * len(dates)}, index=dates)
    return {f"{symbol}.NS": frame for symbol in symbols}


def core_fingerprint():
    session = database.SessionLocal()
    try:
        account_ids = tuple(engine.ACCOUNTS)
        payload = {
            "accounts": [(x.account_id, x.cash, x.last_market_date, x.state_version) for x in session.query(
                database.AutoPaperAccount).filter(database.AutoPaperAccount.account_id.in_(account_ids)).order_by(
                database.AutoPaperAccount.account_id).all()],
            "orders": sorted(x.payload_hash for x in session.query(database.AutoPaperOrder).filter(
                database.AutoPaperOrder.account_id.in_(account_ids)).all()),
            "decisions": sorted(x.payload_hash for x in session.query(database.AutoPaperDecision).filter(
                database.AutoPaperDecision.account_id.in_(account_ids)).all()),
            "links": sorted(x.link_id for x in session.query(database.AutoPaperCounterfactualLink).filter(
                database.AutoPaperCounterfactualLink.account_id.in_(account_ids)).all()),
        }
        return engine._hash(payload)
    finally: session.close()


def main():
    check(engine.METHODOLOGY_HASH == "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79")
    for occupancy, expected in ((0, 2), (2, 2), (4, 2), (5, 1), (8, 1), (9, 0), (10, 0)):
        check(engine.rolling_admission_budget(occupancy) == expected)
    check(engine.ROLLING_CONFIG["target_occupancy"] == 9)
    check(engine.ROLLING_CONFIG["max_positions_hard"] == 10)
    check(engine.ROLLING_CONFIG["hold_sessions"] == 10 and not engine.ROLLING_CONFIG["replacement"])
    check(engine.ROLLING_CONFIG["queue_expiry_sessions"] == engine.CONFIG["queue_expiry_sessions"])

    friday = seed_friday()
    execute_friday_bootstrap(code_commit="P1.3-TEST")
    before = core_fingerprint()
    activation = activate_rolling_shadow(
        activation_timestamp=dt.datetime(2026, 9, 14, 3, 0, tzinfo=dt.timezone.utc), code_commit="P1.3-TEST")
    check(activation["status"] == "ACTIVE" and activation["activation_mode"] == "FRIDAY_BOOTSTRAP")
    check(activation["provenance"] == "FRIDAY_BOOTSTRAP_ROLLING")
    check(activation["pending_entries"] == 2 and activation["positions"] == activation["trades"] == 0)
    check(core_fingerprint() == before)
    session = database.SessionLocal()
    try:
        account = session.get(database.AutoPaperAccount, engine.ROLLING_ACCOUNT_ID)
        check(account.cash == 1_000_000 and account.last_market_date == SIGNAL_DATE)
        check(session.query(database.AutoPaperOrder).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count() == 2)
        check(session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count() == 11)
        check(session.query(database.AutoPaperCounterfactualLink).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, origin="FRIDAY_BOOTSTRAP_ROLLING").count() == 11)
        check(session.query(database.AutoPaperDecision).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, reason_code="ROLLING_DAILY_ADMISSION_LIMIT").count() == 9)
        check(session.query(database.AutoPaperRollingTelemetry).count() == 1)
        check(session.query(database.AutoPaperPosition).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count() == 0)
    finally: session.close()
    retry = activate_rolling_shadow(code_commit="P1.3-RETRY")
    check(retry["status"] == "IDEMPOTENT_NOOP" and retry["idempotent"])

    monday = dt.date(2026, 9, 14)
    monday_rows = [candidate("M01", monday, 1), candidate("M02", monday, 2)]
    all_symbols = [f"F{i:02d}" for i in range(1, 12)] + ["M01", "M02"]
    result = engine.run_prospective_autopaper(monday_rows, histories(all_symbols), monday,
        dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc), "EOD-2026-09-14")
    rolling_result = result["accounts"][engine.ROLLING_ACCOUNT_ID]
    check(rolling_result["open_positions"] == 2)
    check(rolling_result["admission_budget"] == 2 and rolling_result["admission_used"] == 2)
    session = database.SessionLocal()
    try:
        rolling_positions = session.query(database.AutoPaperPosition).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, status="OPEN").all()
        check(len(rolling_positions) == 2 and all(x.age == 1 for x in rolling_positions))
        check(session.query(database.AutoPaperOrder).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, status="PENDING").count() == 2)
        pending_symbols = {x.symbol for x in session.query(database.AutoPaperOrder).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, status="PENDING").all()}
        check(pending_symbols == {"M01.NS", "M02.NS"})  # fresh P0 outranks Friday queue
        check(session.query(database.AutoPaperOrder).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, side="SELL").count() == 0)
        core_accounts = {x.account_id: x for x in session.query(database.AutoPaperAccount).filter(
            database.AutoPaperAccount.account_id.in_(tuple(engine.ACCOUNTS))).all()}
        check(all(x.cash >= 0 for x in core_accounts.values()) and account.account_id not in core_accounts)
    finally: session.close()

    tuesday = dt.date(2026, 9, 15)
    engine.run_prospective_autopaper([], histories(all_symbols), tuesday,
        dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc), "EOD-2026-09-15")
    state = load_autopaper_ui_state()
    rolling = state["rolling"]
    check(state["accounts"][engine.ROLLING_ACCOUNT_ID]["open_positions"] == 4)
    check(rolling["deferred_then_entered"] == 0)  # Monday names were admitted immediately.
    check(rolling["deferred_then_expired"] == 9)
    check(rolling["current"]["hard_capacity"] == 10 and rolling["current"]["target_occupancy"] == 9)
    check(rolling["current"]["largest_same_age_share"] <= 1)
    check("remains queued" in humanize_reason("ROLLING_DAILY_ADMISSION_LIMIT"))
    check("9-position target" in humanize_reason("ROLLING_TARGET_OCCUPANCY"))
    check(state["health"]["reconciliation"] == "PASS")
    check(engine.ROLLING_ACCOUNT_ID in state["health"]["accounts"])
    check(len(state["positions"]) != len(state["shadow_positions"][engine.ROLLING_ACCOUNT_ID]))
    session = database.SessionLocal()
    try:
        navigation_counts = (session.query(database.AutoPaperOrder).count(),
            session.query(database.AutoPaperDecision).count(),
            session.query(database.AutoPaperRollingTelemetry).count())
    finally: session.close()
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    check(not app.exception)
    check(any("Rolling Admission Shadow" in str(x.value) for x in app.markdown))
    learning = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    learning.query_params["page"] = "learning"; learning.run(timeout=30)
    check(not learning.exception)
    session = database.SessionLocal()
    try:
        check(navigation_counts == (session.query(database.AutoPaperOrder).count(),
            session.query(database.AutoPaperDecision).count(),
            session.query(database.AutoPaperRollingTelemetry).count()))
    finally: session.close()

    # Case B: any processed Monday state defers activation to the next cohort.
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    engine._manifest_and_accounts()
    session = database.SessionLocal()
    try:
        session.get(database.AutoPaperAccount, "BASELINE_C3").last_market_date = monday
        session.commit()
    finally: session.close()
    deferred = activate_rolling_shadow(
        activation_timestamp=dt.datetime(2026, 9, 14, 13, tzinfo=dt.timezone.utc), code_commit="CASE-B")
    check(deferred["status"] == "PENDING_NEXT_COHORT")
    check(deferred["activation_signal_date"] is None and deferred["after_market_date"] == "2026-09-14")
    session = database.SessionLocal()
    try: check(session.get(database.AutoPaperAccount, engine.ROLLING_ACCOUNT_ID) is None)
    finally: session.close()
    next_date = dt.date(2026, 9, 15)
    next_row = candidate("NEXT", next_date, 1)
    next_result = engine.run_prospective_autopaper([next_row], histories(["NEXT"]), next_date,
        dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc), "EOD-NEXT")
    check(next_result["accounts"][engine.ROLLING_ACCOUNT_ID]["admission_used"] == 1)
    session = database.SessionLocal()
    try:
        activation_row = session.get(database.AutoPaperRollingActivation, ACTIVATION_ID)
        check(activation_row.status == "ACTIVE" and activation_row.activation_signal_date == next_date)
        link = session.query(database.AutoPaperCounterfactualLink).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID).one()
        check(link.origin == "ROLLING_PROSPECTIVE")
    finally: session.close()

    source = (ROOT / "autopaper_prospective.py").read_text()
    check("add_constrained_paper_trade" not in source)
    check(engine.CONFIG["hold_sessions"] == 10 and engine.CONFIG["replacement"] is False)
    print(f"AutoPaper-P1.3 focused tests: {passed} passed")


if __name__ == "__main__":
    main()

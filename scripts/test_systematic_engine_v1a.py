#!/usr/bin/env python3
"""Focused Systematic Engine V1A acceptance tests."""
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
DB = Path(tempfile.gettempdir()) / "systematic_engine_v1a_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as engine
from autopaper_edge_activation import activate_edge_capture
from autopaper_friday_bootstrap import SIGNAL_DATE, execute_friday_bootstrap
from autopaper_ui_service import load_autopaper_ui_state

passed = 0


def check(value):
    global passed
    assert value
    passed += 1


def candidate(symbol, date, rank=1):
    return {"opportunity_id": f"{date}:{symbol}:VCP", "symbol": f"{symbol}.NS",
        "signal_date": str(date), "strategy": "VCP", "entry_price": 100., "atr_20": 4.,
        "current_volume": 1_000_000, "sector": "Industrials", "qualification_status": "QUALIFIED",
        "opportunity_priority_rank": rank}


def seed_friday():
    rows = [candidate(f"F{i:02d}", SIGNAL_DATE, i) for i in range(1, 12)]
    completed = dt.datetime(2026, 9, 13, 9, tzinfo=dt.timezone.utc)
    database.persist_analysis_run({"run_id": "EOD-2026-09-11", "analysis_date": SIGNAL_DATE,
        "started_at": completed - dt.timedelta(minutes=1), "completed_at": completed,
        "status": "SUCCESS", "qualified_count": len(rows),
        "decision_contract_version": "TRADING_COCKPIT_V1_1_EOD", "source": "AUTOMATED_EOD"}, rows)
    return rows


def frame(symbols, end="2026-10-15", declining=False):
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=55)
    base = np.linspace(90, 110, len(dates))
    if declining: base[-10:] = np.linspace(110, 90, 10)
    volume = np.full(len(dates), 1_000_000.); volume[-1] = 2_000_000.
    stock = pd.DataFrame({"Open": base, "High": base + 1, "Low": base - 1,
        "Close": base - (.8 if declining else -.2), "Volume": volume}, index=dates)
    benchmark = pd.DataFrame({"Open": 100., "High": 101., "Low": 99., "Close": 100.,
        "Volume": 1_000_000.}, index=dates)
    result = {f"{symbol}.NS": stock.copy() for symbol in symbols}; result["NIFTY500"] = benchmark
    return result


def core_fingerprint():
    session = database.SessionLocal()
    try:
        core = (*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID)
        return engine._hash({"accounts": [(x.account_id, x.cash, x.last_market_date, x.state_version)
            for x in session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_(core)).order_by(database.AutoPaperAccount.account_id)],
            "orders": sorted(x.payload_hash for x in session.query(database.AutoPaperOrder).filter(
                database.AutoPaperOrder.account_id.in_(core))),
            "decisions": sorted(x.payload_hash for x in session.query(database.AutoPaperDecision).filter(
                database.AutoPaperDecision.account_id.in_(core)))})
    finally: session.close()


def main():
    check(engine.METHODOLOGY_HASH == "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79")
    check(set(engine.EDGE_ACCOUNT_CONFIGS) == {"SHADOW_EDGE_H20", "SHADOW_EDGE_H20_CATASTROPHE", "SHADOW_EDGE_H20_THESIS"})
    check(all(x["hold_sessions"] == 20 for x in engine.EDGE_ACCOUNT_CONFIGS.values()))
    check(engine.EDGE_ACCOUNT_CONFIGS["SHADOW_EDGE_H20_CATASTROPHE"]["catastrophe"])
    check(engine.EDGE_ACCOUNT_CONFIGS["SHADOW_EDGE_H20_THESIS"]["thesis"])
    check(all(engine.ACCOUNT_CONFIGS[x]["rolling"] for x in engine.EDGE_ACCOUNT_CONFIGS))
    check(all(engine.EDGE_CONFIGS[x]["replacement"] is False for x in engine.EDGE_CONFIGS))
    check(len(set(engine.EDGE_METHODOLOGY_HASHES.values())) == 3)
    threshold, downside = engine.catastrophe_threshold(100., 8 / 3.5)
    check(round(threshold, 6) == 88 and round(downside, 6) == .12)
    threshold, downside = engine.catastrophe_threshold(100., 15 / 3.5)
    check(round(threshold, 6) == 85 and round(downside, 6) == .15)

    rows = seed_friday(); execute_friday_bootstrap(code_commit="EDGE-TEST")
    before = core_fingerprint()
    activation = activate_edge_capture(
        activation_timestamp=dt.datetime(2026, 9, 14, 3, tzinfo=dt.timezone.utc), code_commit="EDGE-TEST")
    check(activation["status"] == "ACTIVE" and activation["activation_mode"] == "FRIDAY_BOOTSTRAP")
    check(activation["provenance"] == "FRIDAY_BOOTSTRAP_EDGE_CAPTURE")
    check(core_fingerprint() == before)
    check(all(v["cash"] == 1_000_000 and v["positions"] == 0 and v["pending_entries"] == 2
              for v in activation["initial_state"].values()))
    session = database.SessionLocal()
    try:
        check(session.query(database.EdgeCaptureMatch).count() == 33)
        check(session.query(database.AutoPaperCounterfactualLink).filter(
            database.AutoPaperCounterfactualLink.account_id.in_(tuple(engine.EDGE_ACCOUNT_CONFIGS))).count() == 33)
        check(session.query(database.AutoPaperDecision).filter(
            database.AutoPaperDecision.account_id.in_(tuple(engine.EDGE_ACCOUNT_CONFIGS)),
            database.AutoPaperDecision.reason_code == "ROLLING_DAILY_ADMISSION_LIMIT").count() == 27)
    finally: session.close()
    retry = activate_edge_capture(code_commit="EDGE-RETRY")
    check(retry["status"] == "IDEMPOTENT_NOOP" and retry["idempotent"])

    symbols = [f"F{i:02d}" for i in range(1, 12)]
    histories = frame(symbols)
    sessions = [d.date() for d in pd.bdate_range("2026-09-14", periods=21)]
    for date in sessions:
        for account_id in ("SHADOW_EDGE_H20", "SHADOW_EDGE_H20_CATASTROPHE"):
            engine._process_account(account_id, [], histories, date,
                dt.datetime.combine(date, dt.time(12), tzinfo=dt.timezone.utc))
    session = database.SessionLocal()
    try:
        e1_trades = session.query(database.AutoPaperTrade).filter_by(account_id="SHADOW_EDGE_H20").all()
        check(len(e1_trades) == 2 and all(x.holding_sessions == 20 for x in e1_trades))
        check(all(json.loads(x.payload)["exit_reason"] == "H20_TIME_EXIT" for x in e1_trades))
        check(session.query(database.AutoPaperTrade).filter_by(account_id="SHADOW_EDGE_H20_CATASTROPHE").count() == 2)
        legs = session.query(database.EdgeCaptureMatch).filter_by(account_id="SHADOW_EDGE_H20").all()
        check(sum(x.execution_date is not None for x in legs) == 4)
        check(sum(x.exit_date is not None for x in legs) == 2)
        check(all(x.entry_size is None for x in legs if x.execution_date is None))
    finally: session.close()

    declining = frame(["DROP"], end="2026-09-18", declining=True)
    thesis = engine.thesis_failure_components(declining, "DROP.NS", dt.date(2026, 9, 18))
    check(thesis["available_components"] == 3)
    check(thesis["components"]["T"] is True and thesis["components"]["R"] is True)
    check(thesis["components"]["V"] is True and thesis["state"] == "TRUE")
    missing_benchmark = dict(declining); missing_benchmark.pop("NIFTY500")
    missing = engine.thesis_failure_components(missing_benchmark, "DROP.NS", dt.date(2026, 9, 18))
    check(missing["components"]["R"] == "NOT_AVAILABLE")
    check(missing["available_components"] == 2)

    # E3: grace through age two, then two causal 2-of-3 failure states,
    # followed by next-open—not trigger-close—execution.
    thesis_histories = frame(["DROP"], end="2026-09-21", declining=True)
    thesis_dates = [d.date() for d in pd.bdate_range("2026-09-14", "2026-09-21")]
    for index, date in enumerate(thesis_dates):
        stream = [candidate("DROP", date)] if index == 0 else []
        engine._process_account("SHADOW_EDGE_H20_THESIS", stream, thesis_histories, date,
            dt.datetime.combine(date, dt.time(12), tzinfo=dt.timezone.utc))
    session = database.SessionLocal()
    try:
        thesis_trade = session.query(database.AutoPaperTrade).filter_by(
            account_id="SHADOW_EDGE_H20_THESIS", opportunity_id=f"2026-09-14:DROP:VCP").one()
        check(json.loads(thesis_trade.payload)["exit_reason"] == "THESIS_FAILURE_EXIT")
        check(thesis_trade.exit_date > dt.date(2026, 9, 18))
        observations = session.query(database.EdgeCaptureThesisObservation).filter_by(
            opportunity_id=thesis_trade.opportunity_id).order_by(database.EdgeCaptureThesisObservation.market_date).all()
        check(sum(x.state == "GRACE_PERIOD" for x in observations) == 2)
        check(sum(x.state == "TRUE" for x in observations) >= 2)
        check(session.query(database.EdgeCaptureExitObservation).filter_by(
            opportunity_id=thesis_trade.opportunity_id, exit_type="THESIS_FAILURE_EXIT").count() == 1)
    finally: session.close()

    # E2: the frozen wider-of catastrophe protection remains independent of H20.
    crash_date, crash_fill = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    crash_histories = frame(["CRASH"], end="2026-10-14")
    crash = crash_histories["CRASH.NS"]
    crash.loc[pd.Timestamp(crash_fill), ["Open", "High", "Low", "Close", "Volume"]] = [100., 101., 80., 90., 2_000_000.]
    engine._process_account("SHADOW_EDGE_H20_CATASTROPHE", [candidate("CRASH", crash_date)],
        crash_histories, crash_date, dt.datetime(2026, 10, 13, 12, tzinfo=dt.timezone.utc))
    engine._process_account("SHADOW_EDGE_H20_CATASTROPHE", [], crash_histories, crash_fill,
        dt.datetime(2026, 10, 14, 12, tzinfo=dt.timezone.utc))
    session = database.SessionLocal()
    try:
        crash_trade = session.query(database.AutoPaperTrade).filter_by(
            account_id="SHADOW_EDGE_H20_CATASTROPHE", opportunity_id=f"{crash_date}:CRASH:VCP").one()
        check(json.loads(crash_trade.payload)["exit_reason"] == "CATASTROPHE_EXIT")
        check(session.query(database.EdgeCaptureExitObservation).filter_by(
            opportunity_id=crash_trade.opportunity_id, exit_type="CATASTROPHE_EXIT").count() == 1)
    finally: session.close()

    state = load_autopaper_ui_state()
    check(state["edge_capture"]["activation"]["status"] == "ACTIVE")
    check(set(state["edge_capture"]["accounts"]) == set(engine.EDGE_ACCOUNT_CONFIGS))
    check(state["edge_capture"]["matched_groups"] >= 4)
    check(state["edge_capture"]["completed_groups"] >= 2)
    check(all((state["accounts"][x] or {}).get("cash", 0) >= 0 for x in engine.EDGE_ACCOUNT_CONFIGS))

    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params["page"] = "portfolio"; app.run(timeout=30)
    app.session_state["portfolio_view"] = "Shadows"; app.run(timeout=30)
    check(not app.exception)
    check(any("Edge Capture" in str(x.value) for x in app.markdown))
    learning = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    learning.query_params["page"] = "learning"; learning.run(timeout=30)
    check(not learning.exception)
    check(engine.CONFIG["hold_sessions"] == 10 and engine.CONFIG["replacement"] is False)

    # Case B: already-consumed Monday state can only arm the next cohort.
    database.Base.metadata.drop_all(bind=database.engine); database.Base.metadata.create_all(bind=database.engine)
    engine._manifest_and_accounts()
    session = database.SessionLocal()
    try:
        session.get(database.AutoPaperAccount, "BASELINE_C3").last_market_date = dt.date(2026, 9, 14)
        session.commit()
    finally: session.close()
    deferred = activate_edge_capture(
        activation_timestamp=dt.datetime(2026, 9, 14, 13, tzinfo=dt.timezone.utc), code_commit="EDGE-CASE-B")
    check(deferred["status"] == "PENDING_NEXT_COHORT")
    check(deferred["activation_signal_date"] is None and deferred["after_market_date"] == "2026-09-14")
    next_date = dt.date(2026, 9, 15); next_row = candidate("NEXT", next_date)
    next_result = engine.run_prospective_autopaper([next_row], frame(["NEXT"], end="2026-09-15"), next_date,
        dt.datetime(2026, 9, 15, 12, tzinfo=dt.timezone.utc), "EDGE-NEXT")
    check(all(next_result["accounts"][key]["admission_used"] == 1 for key in engine.EDGE_ACCOUNT_CONFIGS))
    session = database.SessionLocal()
    try:
        activation_row = session.get(database.EdgeCaptureActivation, "EDGE_CAPTURE_V1A_ACTIVATION")
        check(activation_row.status == "ACTIVE" and activation_row.activation_signal_date == next_date)
        check(all(session.query(database.AutoPaperCounterfactualLink).filter_by(
            account_id=key, origin="EDGE_CAPTURE_PROSPECTIVE").count() == 1 for key in engine.EDGE_ACCOUNT_CONFIGS))
    finally: session.close()
    print(f"Systematic Engine V1A focused tests: {passed} passed")


if __name__ == "__main__": main()

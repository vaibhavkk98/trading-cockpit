"""Causal one-time activation for Systematic Engine V1B Phase-B shadows."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import database
import autopaper_prospective as engine
from autopaper_friday_bootstrap import FIRST_EXECUTION_DATE, SIGNAL_DATE, _load_friday_stream
from autopaper_observability import capture_sector_metadata, persist_risk_snapshot


ACTIVATION_ID = "PORTFOLIO_RISK_V1B_ACTIVATION"
RUN_ID = "PORTFOLIO_RISK_V1B_ACTIVATION_20260914"


def _future_state(session) -> dict[str, int]:
    existing = (*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID, *engine.EDGE_ACCOUNT_CONFIGS)
    telemetry = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(existing),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE).count()
    fills = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(existing),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE,
        database.AutoPaperExecutionTelemetry.execution_status == "FILLED").count()
    processed = session.query(database.AutoPaperAccount).filter(
        database.AutoPaperAccount.account_id.in_(existing),
        database.AutoPaperAccount.last_market_date >= FIRST_EXECUTION_DATE).count()
    positions = session.query(database.AutoPaperPosition).filter(
        database.AutoPaperPosition.account_id.in_(existing),
        database.AutoPaperPosition.entry_date >= FIRST_EXECUTION_DATE).count()
    trades = session.query(database.AutoPaperTrade).filter(
        database.AutoPaperTrade.account_id.in_(existing),
        database.AutoPaperTrade.entry_date >= FIRST_EXECUTION_DATE).count()
    return {"monday_execution_telemetry": telemetry, "monday_fills": fills,
            "accounts_processed_through_monday": processed,
            "monday_positions": positions, "monday_trades": trades}


def _payload(status, mode, timestamp, signal_date, after_date, provenance, evidence, commit):
    return {"activation_id": ACTIVATION_ID, "status": status, "activation_mode": mode,
        "activation_timestamp": timestamp.isoformat(),
        "activation_signal_date": signal_date.isoformat() if signal_date else None,
        "after_market_date": after_date.isoformat() if after_date else None,
        "provenance": provenance, "code_commit": commit,
        "control": {"policy": "B0", "account_id": engine.ROLLING_ACCOUNT_ID,
                    "methodology_hash": engine.ROLLING_METHODOLOGY_HASH},
        "risk_methodology_hashes": engine.PORTFOLIO_RISK_METHODOLOGY_HASHES,
        "risk_config_hashes": engine.PORTFOLIO_RISK_CONFIG_HASHES,
        "risk_engine_version": engine.PORTFOLIO_RISK_VERSION,
        "future_execution_state_at_decision": evidence,
        "historical_holdout_opened": False, "baseline_authority": False,
        "phase_a_authority": False}


def activate_portfolio_risk(*, activation_timestamp: dt.datetime | None = None,
                            code_commit: str | None = None) -> dict[str, Any]:
    if not database.init_db():
        raise RuntimeError("DATABASE_UNAVAILABLE")
    timestamp = engine._utc(activation_timestamp or dt.datetime.now(dt.timezone.utc))
    commit = code_commit or os.environ.get("GITHUB_SHA", "NOT_AVAILABLE")
    session = database.SessionLocal()
    try:
        existing = session.get(database.PortfolioRiskActivation, ACTIVATION_ID)
        if existing and existing.status in {"ACTIVE", "PENDING_NEXT_COHORT"}:
            return {**json.loads(existing.payload), "status": "IDEMPOTENT_NOOP", "idempotent": True}
        unexplained = sum(session.query(model).filter_by(account_id=account_id).count()
            for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS
            for model in (database.AutoPaperOrder, database.AutoPaperPosition, database.AutoPaperTrade))
        if unexplained and existing is None:
            raise RuntimeError("UNEXPLAINED_PORTFOLIO_RISK_ACTIVITY")
        future = _future_state(session); consumed = any(future.values())
        if consumed:
            dates = [row.last_market_date for row in session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_((*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID,
                    *engine.EDGE_ACCOUNT_CONFIGS))).all() if row.last_market_date]
            after_date = max([FIRST_EXECUTION_DATE, *dates])
            payload = _payload("PENDING_NEXT_COHORT", "NEXT_FINALIZED_COHORT", timestamp,
                None, after_date, "PORTFOLIO_RISK_PROSPECTIVE", future, commit)
            session.add(database.PortfolioRiskActivation(
                activation_id=ACTIVATION_ID, status="PENDING_NEXT_COHORT",
                activation_mode="NEXT_FINALIZED_COHORT", activation_timestamp=timestamp,
                activation_signal_date=None, after_market_date=after_date,
                provenance="PORTFOLIO_RISK_PROSPECTIVE", payload=engine._json(payload),
                payload_hash=engine._hash(payload)))
            session.commit(); return {**payload, "idempotent": False}
        run, decisions = _load_friday_stream(session)
        payload = _payload("INITIALIZING", "FRIDAY_BOOTSTRAP", timestamp, SIGNAL_DATE,
            None, "FRIDAY_BOOTSTRAP_PORTFOLIO_RISK", future, commit)
        if existing is None:
            session.add(database.PortfolioRiskActivation(
                activation_id=ACTIVATION_ID, status="INITIALIZING", activation_mode="FRIDAY_BOOTSTRAP",
                activation_timestamp=timestamp, activation_signal_date=SIGNAL_DATE,
                after_market_date=None, provenance="FRIDAY_BOOTSTRAP_PORTFOLIO_RISK",
                payload=engine._json(payload), payload_hash=engine._hash(payload)))
            capture_sector_metadata(session, decisions, timestamp)
            session.commit()
    finally:
        session.close()

    engine.ensure_portfolio_risk_accounts(timestamp, SIGNAL_DATE)
    stream = [{**row, "prospective_origin": "FRIDAY_BOOTSTRAP_PORTFOLIO_RISK",
               "intended_execution_date": FIRST_EXECUTION_DATE.isoformat()} for row in decisions]
    engine._sync_portfolio_risk_control_matches(stream, SIGNAL_DATE)
    results = {}
    for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS:
        results[account_id] = engine._process_account(
            account_id, stream, {}, SIGNAL_DATE, engine._utc(run.completed_at))
        try:
            persist_risk_snapshot(account_id, SIGNAL_DATE, {}, stream)
        except Exception as exc:
            results[account_id].setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")

    session = database.SessionLocal()
    try:
        activation = session.get(database.PortfolioRiskActivation, ACTIVATION_ID)
        activation.status = "ACTIVE"
        payload.update({"status": "ACTIVE", "canonical_friday_run_id": run.run_id,
                        "qualified_count": len(decisions), "accounts": results})
        activation.payload = engine._json(payload); activation.payload_hash = engine._hash(payload)
        for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS:
            links = session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=account_id).all()
            matches = session.query(database.PortfolioRiskMatch).filter_by(account_id=account_id).all()
            if len(links) != len(decisions) or len(matches) != len(decisions):
                raise RuntimeError("PORTFOLIO_RISK_BOOTSTRAP_RECONCILIATION_FAILED")
            if any(row.origin != "FRIDAY_BOOTSTRAP_PORTFOLIO_RISK" for row in links):
                raise RuntimeError("PORTFOLIO_RISK_BOOTSTRAP_PROVENANCE_FAILED")
        if session.query(database.PortfolioRiskMatch).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count() != len(decisions):
            raise RuntimeError("PORTFOLIO_RISK_CONTROL_LINKAGE_FAILED")
        health_accounts = {key: {"status": "HEALTHY"} for key in (
            *engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID, *engine.EDGE_ACCOUNT_CONFIGS)}
        health_accounts.update(results)
        health = {"run_id": RUN_ID, "run_timestamp": timestamp.isoformat(),
            "market_date": SIGNAL_DATE.isoformat(), "qualified_opportunities": len(decisions),
            "paper_only": True, "accounts": health_accounts, "status": "HEALTHY",
            "portfolio_risk_status": "HEALTHY", "errors": [],
            "warnings": [warning for result in results.values() for warning in result.get("warnings", [])]}
        if session.get(database.AutoPaperHealth, RUN_ID) is None:
            session.add(database.AutoPaperHealth(run_id=RUN_ID, market_date=SIGNAL_DATE,
                run_timestamp=timestamp, status="HEALTHY", payload=engine._json(health),
                payload_hash=engine._hash(health)))
        session.commit()
        return {**payload, "idempotent": False, "initial_state": {account_id: {
            "cash": session.get(database.AutoPaperAccount, account_id).cash,
            "positions": session.query(database.AutoPaperPosition).filter_by(account_id=account_id).count(),
            "pending_entries": session.query(database.AutoPaperOrder).filter_by(
                account_id=account_id, side="BUY", status="PENDING").count(),
            "decision_snapshots": session.query(database.PortfolioRiskDecisionSnapshot).filter_by(
                account_id=account_id).count()} for account_id in engine.PORTFOLIO_RISK_ACCOUNT_CONFIGS}}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()

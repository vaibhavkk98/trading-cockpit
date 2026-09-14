"""Causal one-time activation for the isolated Systematic Engine V1A shadows."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import database
import autopaper_prospective as engine
from autopaper_friday_bootstrap import FIRST_EXECUTION_DATE, SIGNAL_DATE, _load_friday_stream
from autopaper_observability import persist_risk_snapshot


ACTIVATION_ID = "EDGE_CAPTURE_V1A_ACTIVATION"
RUN_ID = "EDGE_CAPTURE_V1A_ACTIVATION_20260914"


def _future_state(session) -> dict[str, int]:
    existing = (*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID)
    telemetry = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(existing),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE).count()
    fills = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(existing),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE,
        database.AutoPaperExecutionTelemetry.execution_status == "FILLED").count()
    positions = session.query(database.AutoPaperPosition).filter(
        database.AutoPaperPosition.account_id.in_(existing)).count()
    trades = session.query(database.AutoPaperTrade).filter(
        database.AutoPaperTrade.account_id.in_(existing)).count()
    processed = session.query(database.AutoPaperAccount).filter(
        database.AutoPaperAccount.account_id.in_(existing),
        database.AutoPaperAccount.last_market_date >= FIRST_EXECUTION_DATE).count()
    return {"monday_execution_telemetry": telemetry, "monday_fills": fills,
            "positions": positions, "trades": trades,
            "accounts_processed_through_monday": processed}


def _payload(status, mode, timestamp, signal_date, after_date, provenance, evidence, commit):
    return {"activation_id": ACTIVATION_ID, "status": status, "activation_mode": mode,
        "activation_timestamp": timestamp.isoformat(),
        "activation_signal_date": signal_date.isoformat() if signal_date else None,
        "after_market_date": after_date.isoformat() if after_date else None,
        "provenance": provenance, "code_commit": commit,
        "control": {"policy": "E0_H10_CONTROL", "account_id": engine.ROLLING_ACCOUNT_ID,
                    "methodology_hash": engine.ROLLING_METHODOLOGY_HASH},
        "edge_methodology_hashes": engine.EDGE_METHODOLOGY_HASHES,
        "edge_config_hashes": engine.EDGE_CONFIG_HASHES,
        "future_execution_state_at_decision": evidence, "historical_holdout_opened": False,
        "baseline_authority": False}


def activate_edge_capture(*, activation_timestamp: dt.datetime | None = None,
                          code_commit: str | None = None) -> dict[str, Any]:
    if not database.init_db(): raise RuntimeError("DATABASE_UNAVAILABLE")
    timestamp = engine._utc(activation_timestamp or dt.datetime.now(dt.timezone.utc))
    commit = code_commit or os.environ.get("GITHUB_SHA", "NOT_AVAILABLE")
    session = database.SessionLocal()
    try:
        existing = session.get(database.EdgeCaptureActivation, ACTIVATION_ID)
        if existing and existing.status in {"ACTIVE", "PENDING_NEXT_COHORT"}:
            return {**json.loads(existing.payload), "status": "IDEMPOTENT_NOOP", "idempotent": True}
        unexplained = sum(session.query(model).filter_by(account_id=account_id).count()
            for account_id in engine.EDGE_ACCOUNT_CONFIGS
            for model in (database.AutoPaperOrder, database.AutoPaperPosition, database.AutoPaperTrade))
        if unexplained and existing is None: raise RuntimeError("UNEXPLAINED_EDGE_ACTIVITY")
        future = _future_state(session); consumed = any(future.values())
        if consumed:
            dates = [x.last_market_date for x in session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_((*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID))).all()
                if x.last_market_date]
            after_date = max([FIRST_EXECUTION_DATE, *dates])
            payload = _payload("PENDING_NEXT_COHORT", "NEXT_FINALIZED_COHORT", timestamp,
                               None, after_date, "EDGE_CAPTURE_PROSPECTIVE", future, commit)
            session.add(database.EdgeCaptureActivation(
                activation_id=ACTIVATION_ID, status="PENDING_NEXT_COHORT",
                activation_mode="NEXT_FINALIZED_COHORT", activation_timestamp=timestamp,
                activation_signal_date=None, after_market_date=after_date,
                provenance="EDGE_CAPTURE_PROSPECTIVE", payload=engine._json(payload),
                payload_hash=engine._hash(payload)))
            session.commit(); return {**payload, "idempotent": False}
        run, decisions = _load_friday_stream(session)
        payload = _payload("INITIALIZING", "FRIDAY_BOOTSTRAP", timestamp, SIGNAL_DATE,
                           None, "FRIDAY_BOOTSTRAP_EDGE_CAPTURE", future, commit)
        if existing is None:
            session.add(database.EdgeCaptureActivation(
                activation_id=ACTIVATION_ID, status="INITIALIZING", activation_mode="FRIDAY_BOOTSTRAP",
                activation_timestamp=timestamp, activation_signal_date=SIGNAL_DATE,
                after_market_date=None, provenance="FRIDAY_BOOTSTRAP_EDGE_CAPTURE",
                payload=engine._json(payload), payload_hash=engine._hash(payload)))
            session.commit()
    finally: session.close()

    engine.ensure_edge_accounts(timestamp, SIGNAL_DATE)
    stream = [{**row, "prospective_origin": "FRIDAY_BOOTSTRAP_EDGE_CAPTURE",
               "intended_execution_date": FIRST_EXECUTION_DATE.isoformat()} for row in decisions]
    results = {}
    for account_id in engine.EDGE_ACCOUNT_CONFIGS:
        results[account_id] = engine._process_account(
            account_id, stream, {}, SIGNAL_DATE, engine._utc(run.completed_at))
        try: persist_risk_snapshot(account_id, SIGNAL_DATE, {}, stream)
        except Exception as exc: results[account_id].setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")

    session = database.SessionLocal()
    try:
        activation = session.get(database.EdgeCaptureActivation, ACTIVATION_ID)
        activation.status = "ACTIVE"
        payload.update({"status": "ACTIVE", "canonical_friday_run_id": run.run_id,
                        "qualified_count": len(decisions), "accounts": results})
        activation.payload = engine._json(payload); activation.payload_hash = engine._hash(payload)
        for account_id in engine.EDGE_ACCOUNT_CONFIGS:
            links = session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=account_id).all()
            matches = session.query(database.EdgeCaptureMatch).filter_by(account_id=account_id).all()
            if len(links) != len(decisions) or len(matches) != len(decisions):
                raise RuntimeError("EDGE_BOOTSTRAP_RECONCILIATION_FAILED")
            if any(x.origin != "FRIDAY_BOOTSTRAP_EDGE_CAPTURE" for x in links):
                raise RuntimeError("EDGE_BOOTSTRAP_PROVENANCE_FAILED")
        health_accounts = {key: {"status": "HEALTHY"} for key in (*engine.ACCOUNTS, engine.ROLLING_ACCOUNT_ID)}
        health_accounts.update(results)
        health = {"run_id": RUN_ID, "run_timestamp": timestamp.isoformat(),
            "market_date": SIGNAL_DATE.isoformat(), "qualified_opportunities": len(decisions),
            "kill_switch": False, "paper_only": True, "accounts": health_accounts,
            "shadow_run_status": "HEALTHY", "rolling_shadow_status": "HEALTHY",
            "edge_capture_status": "HEALTHY", "errors": [],
            "warnings": [warning for value in results.values() for warning in value.get("warnings", [])],
            "status": "HEALTHY"}
        if session.get(database.AutoPaperHealth, RUN_ID) is None:
            session.add(database.AutoPaperHealth(run_id=RUN_ID, market_date=SIGNAL_DATE,
                run_timestamp=timestamp, status="HEALTHY", payload=engine._json(health),
                payload_hash=engine._hash(health)))
        session.commit()
        return {**payload, "idempotent": False,
            "initial_state": {key: {"cash": session.get(database.AutoPaperAccount, key).cash,
                "positions": session.query(database.AutoPaperPosition).filter_by(account_id=key).count(),
                "pending_entries": session.query(database.AutoPaperOrder).filter_by(
                    account_id=key, side="BUY", status="PENDING").count()}
                for key in engine.EDGE_ACCOUNT_CONFIGS}}
    except Exception:
        session.rollback(); raise
    finally: session.close()

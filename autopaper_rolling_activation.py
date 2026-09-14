"""Causal one-time activation for the isolated AutoPaper rolling shadow."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import database
import autopaper_prospective as engine
from autopaper_friday_bootstrap import FIRST_EXECUTION_DATE, SIGNAL_DATE, _load_friday_stream
from autopaper_observability import persist_risk_snapshot


ACTIVATION_ID = "AUTOPAPER_ROLLING_ACTIVATION_V1"
RUN_ID = "AUTOPAPER_ROLLING_ACTIVATION_20260914"


def _future_execution_state(session) -> dict[str, int]:
    core = tuple(engine.ACCOUNTS)
    monday_attempts = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(core),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE).count()
    monday_fills = session.query(database.AutoPaperExecutionTelemetry).filter(
        database.AutoPaperExecutionTelemetry.account_id.in_(core),
        database.AutoPaperExecutionTelemetry.observed_session >= FIRST_EXECUTION_DATE,
        database.AutoPaperExecutionTelemetry.execution_status == "FILLED").count()
    positions = session.query(database.AutoPaperPosition).filter(
        database.AutoPaperPosition.account_id.in_(core)).count()
    trades = session.query(database.AutoPaperTrade).filter(database.AutoPaperTrade.account_id.in_(core)).count()
    processed = session.query(database.AutoPaperAccount).filter(
        database.AutoPaperAccount.account_id.in_(core),
        database.AutoPaperAccount.last_market_date >= FIRST_EXECUTION_DATE).count()
    return {"monday_execution_attempts": monday_attempts, "monday_fills": monday_fills,
            "positions": positions, "trades": trades, "accounts_processed_through_monday": processed}


def _store_activation(session, *, status, mode, timestamp, signal_date, after_market_date,
                      provenance, evidence, code_commit):
    payload = {"activation_id": ACTIVATION_ID, "status": status, "activation_mode": mode,
        "activation_timestamp": timestamp.isoformat(),
        "activation_signal_date": signal_date.isoformat() if signal_date else None,
        "after_market_date": after_market_date.isoformat() if after_market_date else None,
        "provenance": provenance, "methodology_hash": engine.ROLLING_METHODOLOGY_HASH,
        "config_hash": engine.ROLLING_CONFIG_HASH, "baseline_methodology_hash": engine.METHODOLOGY_HASH,
        "baseline_config_hash": engine.CONFIG_HASH, "code_commit": code_commit,
        "future_execution_state_at_decision": evidence, "historical_holdout_opened": False,
        "baseline_authority": False}
    row = database.AutoPaperRollingActivation(
        activation_id=ACTIVATION_ID, status=status, activation_mode=mode,
        activation_timestamp=timestamp, activation_signal_date=signal_date,
        after_market_date=after_market_date, methodology_hash=engine.ROLLING_METHODOLOGY_HASH,
        config_hash=engine.ROLLING_CONFIG_HASH, provenance=provenance,
        payload=engine._json(payload), payload_hash=engine._hash(payload))
    session.add(row)
    return row, payload


def activate_rolling_shadow(*, activation_timestamp: dt.datetime | None = None,
                            code_commit: str | None = None) -> dict[str, Any]:
    if not database.init_db():
        raise RuntimeError("DATABASE_UNAVAILABLE")
    timestamp = engine._utc(activation_timestamp or dt.datetime.now(dt.timezone.utc))
    commit = code_commit or os.environ.get("GITHUB_SHA", "NOT_AVAILABLE")
    session = database.SessionLocal()
    try:
        existing = session.get(database.AutoPaperRollingActivation, ACTIVATION_ID)
        if existing and existing.status in {"ACTIVE", "PENDING_NEXT_COHORT"}:
            return {**json.loads(existing.payload), "status": "IDEMPOTENT_NOOP", "idempotent": True}
        rolling_activity = sum((
            session.query(database.AutoPaperOrder).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count(),
            session.query(database.AutoPaperPosition).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count(),
            session.query(database.AutoPaperTrade).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count(),
            session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count(),
        ))
        if rolling_activity and not existing:
            raise RuntimeError("UNEXPLAINED_ROLLING_ACTIVITY")
        future = _future_execution_state(session)
        consumed = any(future.values())
        if consumed:
            dates = [x.last_market_date for x in session.query(database.AutoPaperAccount).filter(
                database.AutoPaperAccount.account_id.in_(tuple(engine.ACCOUNTS))).all() if x.last_market_date]
            # Any Monday execution information makes Monday itself ineligible,
            # even if a partial EOD failure left account.last_market_date on Friday.
            after_date = max([FIRST_EXECUTION_DATE, *dates])
            _, payload = _store_activation(session, status="PENDING_NEXT_COHORT",
                mode="NEXT_FINALIZED_COHORT", timestamp=timestamp, signal_date=None,
                after_market_date=after_date, provenance="ROLLING_PROSPECTIVE",
                evidence=future, code_commit=commit)
            session.commit()
            return {**payload, "idempotent": False}
        run, decisions = _load_friday_stream(session)
        if existing is None:
            existing, payload = _store_activation(session, status="INITIALIZING",
                mode="FRIDAY_BOOTSTRAP", timestamp=timestamp, signal_date=SIGNAL_DATE,
                after_market_date=None, provenance="FRIDAY_BOOTSTRAP_ROLLING",
                evidence=future, code_commit=commit)
            session.commit()
        else:
            payload = json.loads(existing.payload)
    finally:
        session.close()

    engine.ensure_rolling_account(timestamp, SIGNAL_DATE)
    rolling_decisions = [{**row, "prospective_origin": "FRIDAY_BOOTSTRAP_ROLLING",
                          "intended_execution_date": FIRST_EXECUTION_DATE.isoformat()} for row in decisions]
    result = engine._process_account(
        engine.ROLLING_ACCOUNT_ID, rolling_decisions, {}, SIGNAL_DATE, engine._utc(run.completed_at))
    warnings = []
    try:
        persist_risk_snapshot(engine.ROLLING_ACCOUNT_ID, SIGNAL_DATE, {}, rolling_decisions)
    except Exception as exc:
        warnings.append(f"RISK_TELEMETRY:{type(exc).__name__}")

    session = database.SessionLocal()
    try:
        activation = session.get(database.AutoPaperRollingActivation, ACTIVATION_ID)
        activation.status = "ACTIVE"
        payload.update({"status": "ACTIVE", "canonical_friday_run_id": run.run_id,
            "qualified_count": len(decisions), "rolling_result": result, "warnings": warnings})
        activation.payload = engine._json(payload); activation.payload_hash = engine._hash(payload)
        orders = session.query(database.AutoPaperOrder).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID, status="PENDING").count()
        links = session.query(database.AutoPaperCounterfactualLink).filter_by(
            account_id=engine.ROLLING_ACCOUNT_ID).all()
        if len(links) != len(decisions) or any(x.origin != "FRIDAY_BOOTSTRAP_ROLLING" for x in links):
            raise RuntimeError("ROLLING_BOOTSTRAP_RECONCILIATION_FAILED")
        account_health = {account_id: {"status": "HEALTHY"} for account_id in engine.ACCOUNTS}
        account_health[engine.ROLLING_ACCOUNT_ID] = result
        health = {"run_id": RUN_ID, "run_timestamp": timestamp.isoformat(),
            "market_date": SIGNAL_DATE.isoformat(), "qualified_opportunities": len(decisions),
            "kill_switch": False, "paper_only": True, "accounts": account_health,
            "shadow_run_status": "HEALTHY", "rolling_shadow_status": "HEALTHY",
            "errors": [], "warnings": warnings, "status": "HEALTHY"}
        if session.get(database.AutoPaperHealth, RUN_ID) is None:
            session.add(database.AutoPaperHealth(run_id=RUN_ID, market_date=SIGNAL_DATE,
                run_timestamp=timestamp, status="HEALTHY", payload=engine._json(health),
                payload_hash=engine._hash(health)))
        session.commit()
        return {**payload, "idempotent": False, "pending_entries": orders,
            "positions": session.query(database.AutoPaperPosition).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count(),
            "trades": session.query(database.AutoPaperTrade).filter_by(account_id=engine.ROLLING_ACCOUNT_ID).count()}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()

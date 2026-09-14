"""One-time causal Friday-cohort activation amendment for AutoPaper P1.2."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import database
import autopaper_prospective as engine
from autopaper_observability import capture_sector_metadata, persist_risk_snapshot
from autopaper_v1 import digest


AMENDMENT_ID = "FRIDAY_COHORT_BOOTSTRAP_20260911"
RUN_ID = "AUTOPAPER_FRIDAY_BOOTSTRAP_20260911"
SIGNAL_DATE = dt.date(2026, 9, 11)
FIRST_EXECUTION_DATE = dt.date(2026, 9, 14)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INFORMATION_CUTOFF = dt.datetime(2026, 9, 11, 15, 30, tzinfo=IST)
RUN_FINALIZATION_CUTOFF = dt.datetime(2026, 9, 11, 23, 59, 59, tzinfo=IST)
AMENDED_CONFIG = {**engine.CONFIG,
    "activation_market_date": SIGNAL_DATE.isoformat(),
    "first_possible_execution_date": FIRST_EXECUTION_DATE.isoformat(),
    "activation_amendment": AMENDMENT_ID,
}
AMENDED_CONFIG_HASH = digest(AMENDED_CONFIG)


class BootstrapNoGo(RuntimeError):
    pass


def _activity_counts(session) -> dict[str, Any]:
    accounts = {}
    for account_id in engine.ACCOUNTS:
        accounts[account_id] = {
            "orders": session.query(database.AutoPaperOrder).filter_by(account_id=account_id).count(),
            "positions": session.query(database.AutoPaperPosition).filter_by(account_id=account_id).count(),
            "trades": session.query(database.AutoPaperTrade).filter_by(account_id=account_id).count(),
            "signals": session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=account_id).count(),
        }
    return {"accounts": accounts,
        "orders": session.query(database.AutoPaperOrder).count(),
        "positions": session.query(database.AutoPaperPosition).count(),
        "trades": session.query(database.AutoPaperTrade).count(),
        "signals": session.query(database.AutoPaperCounterfactualLink).count(),
        "decisions": session.query(database.AutoPaperDecision).count(),
        "execution_attempts": session.query(database.AutoPaperExecutionTelemetry).count(),
        "fills": session.query(database.AutoPaperExecutionTelemetry).filter_by(execution_status="FILLED").count(),
        "catastrophe_observations": session.query(database.AutoPaperCatastropheObservation).count()}


def _load_friday_stream(session) -> tuple[database.AnalysisRun, list[dict[str, Any]]]:
    run = session.query(database.AnalysisRun).filter_by(analysis_date=SIGNAL_DATE).filter(
        database.AnalysisRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS"])).order_by(
        database.AnalysisRun.completed_at.desc()).first()
    if run is None:
        raise BootstrapNoGo("FRIDAY_CANONICAL_RUN_NOT_FOUND")
    completed = run.completed_at
    if completed is None or (completed.tzinfo is not None and completed > RUN_FINALIZATION_CUTOFF.astimezone(completed.tzinfo)) or (
            completed.tzinfo is None and completed > RUN_FINALIZATION_CUTOFF.replace(tzinfo=None)):
        raise BootstrapNoGo("FRIDAY_RUN_OUTSIDE_INFORMATION_CUTOFF")
    rows = session.query(database.DailyOpportunity).filter_by(run_id=run.run_id).order_by(
        database.DailyOpportunity.priority.asc(), database.DailyOpportunity.symbol.asc()).all()
    decisions = []
    for row in rows:
        payload = json.loads(row.decision_payload)
        payload.setdefault("opportunity_id", row.opportunity_id)
        payload.setdefault("signal_date", SIGNAL_DATE.isoformat())
        payload.setdefault("strategy", row.strategy)
        payload.setdefault("entry_price", row.entry_price)
        payload["qualification_status"] = row.qualification_status
        if str(payload.get("signal_date"))[:10] != SIGNAL_DATE.isoformat():
            raise BootstrapNoGo("FRIDAY_SIGNAL_IDENTITY_MISMATCH")
        if str(payload.get("qualification_status") or "QUALIFIED").upper() != "QUALIFIED":
            raise BootstrapNoGo("NONQUALIFIED_ROW_IN_CANONICAL_STREAM")
        payload["prospective_origin"] = "FRIDAY_BOOTSTRAP"
        payload["intended_execution_date"] = FIRST_EXECUTION_DATE.isoformat()
        decisions.append(payload)
    if not decisions:
        raise BootstrapNoGo("FRIDAY_CANONICAL_STREAM_EMPTY")
    return run, decisions


def _existing_result(session, amendment):
    payload = json.loads(amendment.payload)
    return {**payload, "status": "IDEMPOTENT_NOOP", "idempotent": True,
        "amendment_id": amendment.amendment_id}


def execute_friday_bootstrap(*, amendment_timestamp: dt.datetime | None = None,
                             code_commit: str | None = None) -> dict[str, Any]:
    """Freeze Friday decisions only. This function never loads Monday market data."""
    if not database.init_db():
        raise BootstrapNoGo("DATABASE_UNAVAILABLE")
    amendment_timestamp = amendment_timestamp or dt.datetime.now(dt.timezone.utc)
    if amendment_timestamp.tzinfo is None: amendment_timestamp = amendment_timestamp.replace(tzinfo=dt.timezone.utc)
    session = database.SessionLocal()
    try:
        amendment = session.get(database.AutoPaperActivationAmendment, AMENDMENT_ID)
        if amendment and amendment.status == "COMPLETED":
            return _existing_result(session, amendment)
        before = _activity_counts(session)
        if any(before[key] for key in ("orders", "positions", "trades", "signals", "decisions",
                                      "execution_attempts", "catastrophe_observations")):
            raise BootstrapNoGo("PROSPECTIVE_ACTIVITY_ALREADY_EXISTS; ACTIVATION_REBASE_UNSAFE")
        run, decisions = _load_friday_stream(session)
        run_timestamp = engine._utc(run.completed_at)
        canonical = {"run_id": run.run_id, "completed_at": run_timestamp.isoformat(),
            "qualified_count": len(decisions), "decision_contract_version": run.decision_contract_version}
    finally:
        session.close()

    # Account creation preserves the original behavioral manifest and adds only
    # the already-frozen P1.1 shadow/telemetry manifests.
    engine._manifest_and_accounts()
    warnings = []
    metadata_session = database.SessionLocal()
    try:
        capture_sector_metadata(metadata_session, decisions, run_timestamp)
        metadata_session.commit()
    except Exception as exc:
        metadata_session.rollback()
        warnings.append(f"SECTOR_METADATA:{type(exc).__name__}")
    finally:
        metadata_session.close()

    results = {}
    for account_id in engine.ACCOUNTS:
        results[account_id] = engine._process_account(
            account_id, decisions, {}, SIGNAL_DATE, run_timestamp)
        # Friday-only correlation is explicitly unavailable without a canonical
        # trailing-history bundle; no provider call is made by this bootstrap.
        try:
            persist_risk_snapshot(account_id, SIGNAL_DATE, {}, decisions)
        except Exception as exc:
            warnings.append(f"RISK_TELEMETRY:{account_id}:{type(exc).__name__}")

    session = database.SessionLocal()
    try:
        after = _activity_counts(session)
        pending_by_account = {account_id: session.query(database.AutoPaperOrder).filter_by(
            account_id=account_id, side="BUY", status="PENDING").count() for account_id in engine.ACCOUNTS}
        links = session.query(database.AutoPaperCounterfactualLink).all()
        if any(len({x.opportunity_id for x in links if x.account_id == account_id}) != len(decisions)
               for account_id in engine.ACCOUNTS):
            raise BootstrapNoGo("BOOTSTRAP_RECONCILIATION_FAILED")
        if any(x.origin != "FRIDAY_BOOTSTRAP" for x in links):
            raise BootstrapNoGo("BOOTSTRAP_PROVENANCE_FAILED")
        decision_counts = {}
        for account_id in engine.ACCOUNTS:
            rows = session.query(database.AutoPaperDecision).filter_by(account_id=account_id, market_date=SIGNAL_DATE).all()
            decision_counts[account_id] = {
                "considered": sum(x.account_id == account_id for x in links),
                "queued": sum(x.action == "QUEUE" for x in rows),
                "ordered": pending_by_account[account_id],
                "rejected": sum(x.action == "REJECT" for x in rows),
            }
        metadata = session.query(database.AutoPaperOpportunityMetadata).filter_by(signal_date=SIGNAL_DATE).all()
        sector_coverage = {"available": sum(x.confidence_status == "AVAILABLE" for x in metadata),
                           "not_available": sum(x.confidence_status != "AVAILABLE" for x in metadata)}
        risk_rows = session.query(database.AutoPaperRiskTelemetry).filter_by(market_date=SIGNAL_DATE).all()
        correlation_coverage = {x.account_id: json.loads(x.payload).get("portfolio_correlation") for x in risk_rows}
        result = {"status": "COMPLETED", "idempotent": False, "amendment_id": AMENDMENT_ID,
            "bootstrap_run_id": RUN_ID, "canonical_friday_run": canonical,
            "information_cutoff": INFORMATION_CUTOFF.isoformat(), "signal_date": SIGNAL_DATE.isoformat(),
            "first_execution_date": FIRST_EXECUTION_DATE.isoformat(), "prospective_origin": "FRIDAY_BOOTSTRAP",
            "precondition_counts": before, "post_bootstrap_counts": after,
            "accounts": results, "pending_entries_by_account": pending_by_account,
            "decision_counts_by_account": decision_counts, "sector_coverage": sector_coverage,
            "correlation_coverage": correlation_coverage,
            "baseline_methodology_hash": engine.METHODOLOGY_HASH,
            "old_config_hash": engine.CONFIG_HASH, "amended_config_hash": AMENDED_CONFIG_HASH,
            "methodology_changed": False, "code_commit": code_commit or os.environ.get("GITHUB_SHA", "NOT_AVAILABLE"),
            "amendment_timestamp": amendment_timestamp.isoformat(),
            "amendment_reason": "Include the pre-existing Friday close cohort before any prospective observation or Monday execution data",
            "monday_data_loaded": False, "friday_fills": 0, "historical_backfill": False,
            "historical_holdout_opened": False, "warnings": warnings}
        encoded = engine._json(result)
        amendment = database.AutoPaperActivationAmendment(amendment_id=AMENDMENT_ID, status="COMPLETED",
            previous_activation_timestamp=engine.ACTIVATION_TIMESTAMP, amended_signal_date=SIGNAL_DATE,
            first_execution_date=FIRST_EXECUTION_DATE, amendment_timestamp=amendment_timestamp,
            methodology_hash=engine.METHODOLOGY_HASH, old_config_hash=engine.CONFIG_HASH,
            amended_config_hash=AMENDED_CONFIG_HASH, payload=encoded, payload_hash=engine._hash(result))
        session.add(amendment)
        health = {"run_id": RUN_ID, "run_timestamp": amendment_timestamp.isoformat(),
            "market_date": SIGNAL_DATE.isoformat(), "qualified_opportunities": len(decisions),
            "kill_switch": False, "paper_only": True, "accounts": results,
            "shadow_run_status": "HEALTHY", "errors": [], "warnings": warnings, "status": "HEALTHY",
            "prospective_origin": "FRIDAY_BOOTSTRAP", "first_execution_date": FIRST_EXECUTION_DATE.isoformat()}
        session.add(database.AutoPaperHealth(run_id=RUN_ID, market_date=SIGNAL_DATE,
            run_timestamp=amendment_timestamp, status="HEALTHY", payload=engine._json(health),
            payload_hash=engine._hash(health)))
        session.commit()
        return result
    except Exception:
        session.rollback(); raise
    finally:
        session.close()

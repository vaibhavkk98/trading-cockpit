"""Read-only AutoPaper UI projection. Never advances, simulates, or mutates state."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Iterable

import database


BASELINE = "BASELINE_C3"
SHADOWS = ("SHADOW_C0", "SHADOW_D1", "SHADOW_CATASTROPHE", "SHADOW_ROLLING")
EDGE_ACCOUNTS = ("SHADOW_EDGE_H20", "SHADOW_EDGE_H20_CATASTROPHE", "SHADOW_EDGE_H20_THESIS")
RISK_ACCOUNTS = ("SHADOW_RISK_BUDGET", "SHADOW_RISK_DIVERSIFIED")
SELECTION_ACCOUNTS = ("SHADOW_SELECT_QUALITY", "SHADOW_SELECT_RISK_ADJ")
EXPOSURE_ACCOUNTS = ("SHADOW_EXPOSURE_PORTFOLIO", "SHADOW_EXPOSURE_COMBINED")
VERSION = "AUTOPAPER_PROSPECTIVE_BASELINE_V1"
METHODOLOGY_HASH = "3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79"
CONFIG_HASH = "86ed4f1153186874330f62e0c8bb4ad80aef7972a20188952af3697aced1d9c2"
ACTIVATION_TIMESTAMP = "2026-09-13T12:21:55+00:00"
FIRST_MARKET_DATE = "2026-09-14"
REVIEW_GATE = {
    "completed_trades": 100, "signal_dates": 40, "calendar_days": 90,
    "strategies": 3, "capacity_decisions": 30, "challenger_overlaps": 80,
}
REASON_TEXT = {
    "ENTERED": "Order filled at the next executable open.",
    "ORDER_SUBMITTED_T1": "Entry order is waiting for the next executable open.",
    "H10_TIME_EXIT": "The frozen ten-session holding period completed.",
    "H10_TIME_EXIT_SUBMITTED": "H10 exit is waiting for the next executable open.",
    "HOLD": "Position remains inside its frozen ten-session holding period.",
    "NO_CAPACITY": "Portfolio is currently at position capacity.",
    "INSUFFICIENT_CASH": "Available cash was insufficient for the requested allocation.",
    "LIQUIDITY_FAIL": "Opportunity failed the frozen liquidity constraint.",
    "LOWER_PRIORITY": "Higher-priority eligible opportunities consumed available capacity.",
    "DATA_UNAVAILABLE": "Mandatory execution or sizing data was unavailable.",
    "EXPIRED": "The opportunity expired before an executable entry was available.",
    "POSITION_ALREADY_EXISTS": "The baseline already owns this stock.",
    "ROLLING_DAILY_ADMISSION_LIMIT": "Daily rolling-admission budget has been reached; opportunity remains queued while valid.",
    "ROLLING_TARGET_OCCUPANCY": "Rolling portfolio has reached its normal 9-position target; one slot is intentionally reserved for future opportunities.",
    "PORTFOLIO_RISK_BUDGET": "Available portfolio risk budget is insufficient for this position.",
    "SECTOR_RISK_CONCENTRATION": "The frozen 35% sector heat allowance is currently exhausted.",
    "SIGNAL_DATE_RISK_CONCENTRATION": "The frozen 35% same-signal-date heat allowance is currently exhausted.",
    "CORRELATION_RISK_BUDGET": "Correlation-adjusted risk exceeds the remaining portfolio heat budget.",
    "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK": "One whole share would exceed the remaining risk budget.",
    "RISK_BUDGET_DOWNSIZE": "Position was downsized to fit the frozen portfolio risk budget.",
    "DYNAMIC_EXPOSURE_DEFERRED": "The opportunity remains queued because the current dynamic normal-heat budget cannot admit one whole share.",
}


def humanize_reason(reason: Any) -> str:
    key = str(reason or "NOT_AVAILABLE").upper()
    return REASON_TEXT.get(key, key.replace("_", " ").title() if key != "NOT_AVAILABLE" else "Not available.")


def lifecycle_display(*, action: Any = None, order_status: Any = None,
                      position_status: Any = None, side: Any = None) -> str:
    action, order_status = str(action or "").upper(), str(order_status or "").upper()
    position_status, side = str(position_status or "").upper(), str(side or "").upper()
    if position_status == "OPEN": return "HOLDING"
    if position_status == "CLOSED": return "CLOSED"
    if order_status == "PENDING": return "EXIT PENDING" if side == "SELL" else "ORDERED"
    if order_status == "FILLED": return "FILLED"
    if order_status in {"FAILED_DATA", "REJECTED"}: return "UNFILLED" if order_status == "FAILED_DATA" else "REJECTED"
    if order_status == "EXPIRED" or action == "EXPIRE": return "EXPIRED"
    return {"ENTER": "FILLED", "QUEUE": "QUEUED", "REJECT": "REJECTED",
            "HOLD": "HOLDING", "EXIT": "CLOSED"}.get(action, "CONSIDERED")


def review_gate_progress(actual: dict[str, int]) -> dict[str, Any]:
    rows, met = [], True
    for key, target in REVIEW_GATE.items():
        value = max(0, int(actual.get(key) or 0)); complete = value >= target
        rows.append({"key": key, "label": key.replace("_", " ").title(),
                     "value": value, "target": target, "complete": complete})
        met = met and complete
    return {"met": met, "status": "MET — OFFLINE REVIEW PERMITTED" if met else "NOT YET MET", "rows": rows}


def _json(value: Any) -> dict[str, Any]:
    try: return json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError): return {}


def _finite(value: Any) -> float | None:
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) else None


def _account_metrics(account, positions, trades, snapshots) -> dict[str, Any]:
    nav = snapshots[-1].nav if snapshots else (account.cash if account else None)
    peak = max((row.nav for row in snapshots), default=nav or 0)
    invested = sum(row.quantity * row.current_mark for row in positions)
    payloads = [_json(row.payload) for row in trades]
    costs = sum((p.get("entry_fee") or 0) + (p.get("exit_fee") or 0) +
                (p.get("entry_slippage") or 0) + (p.get("exit_slippage") or 0) for p in payloads)
    turnover = sum((p.get("entry_cost") or 0) + (p.get("exit_price") or 0) * (p.get("quantity") or 0) for p in payloads)
    realized = sum(row.net_pnl for row in trades)
    return {
        "nav": nav, "cash": account.cash if account else None, "invested": invested,
        "invested_pct": invested / nav * 100 if nav else None, "open_positions": len(positions),
        "completed_trades": len(trades), "realized_pnl": realized,
        "net_return_pct": (nav / account.initial_capital - 1) * 100 if account and nav else None,
        "current_drawdown_pct": (nav / peak - 1) * 100 if nav and peak else None,
        "max_drawdown_pct": min((row.nav / max(x.nav for x in snapshots[:i + 1]) - 1 for i, row in enumerate(snapshots)), default=0) * 100 if snapshots else None,
        "costs": costs, "turnover_pct": turnover / account.initial_capital * 100 if account else None,
        "today_pnl": snapshots[-1].nav - snapshots[-2].nav if len(snapshots) >= 2 else None,
        "average_trade_return_pct": sum(row.realized_return_pct for row in trades) / len(trades) if trades else None,
        "average_holding_period": sum(row.holding_sessions for row in trades) / len(trades) if trades else None,
        "catastrophe_exits": sum(_json(row.payload).get("exit_reason") == "CATASTROPHE_EXIT" for row in trades),
    }


def _position_row(position, buy_order, entry_decision, nav, metadata=None) -> dict[str, Any]:
    pp, op, dp = _json(position.payload), _json(buy_order.payload) if buy_order else {}, _json(entry_decision.payload) if entry_decision else {}
    candidate = op.get("candidate") or {}
    market_value = position.quantity * position.current_mark
    horizon = int(pp.get("planned_hold_sessions") or 10)
    planned = "Exit pending — next executable open" if "PENDING" in position.planned_exit_state or position.planned_exit_state == "SUBMITTED_NEXT_OPEN" else (
        f"H{horizon} exit approaching" if position.age >= horizon - 2 else f"Hold through session {horizon}")
    protection = ("Catastrophe protection: armed" if pp.get("catastrophe_threshold") is not None else
        (f"Thesis state: {str(pp.get('thesis_state') or 'intact').lower()}" +
         (f" · {pp.get('thesis_failure_streak', 0)}/2 confirmation sessions" if pp.get("thesis_failure_streak") else ""))
        if position.account_id == "SHADOW_EDGE_H20_THESIS" else "No protective exit")
    return {
        "position_id": position.position_id, "opportunity_id": position.opportunity_id,
        "symbol": position.symbol, "strategy": pp.get("strategy"), "signal_date": pp.get("signal_date"),
        "reference_price": candidate.get("reference_price"), "entry_date": position.entry_date.isoformat(),
        "entry_price": position.entry_price, "current_mark": position.current_mark, "quantity": position.quantity,
        "market_value": market_value, "portfolio_weight_pct": market_value / nav * 100 if nav else None,
        "unrealized_pnl": position.unrealized_pnl, "mfe_pct": position.mfe_pct, "mae_pct": position.mae_pct,
        "hold_day": position.age, "maximum_horizon": horizon,
        "hold_label": f"Day {position.age} / {horizon}", "planned_action": planned,
        "protection_state": protection,
        "planned_exit_date": position.planned_exit_date.isoformat() if position.planned_exit_date else None,
        "order_date": buy_order.requested_session.isoformat() if buy_order else None,
        "fill_date": buy_order.fill_timestamp.date().isoformat() if buy_order and buy_order.fill_timestamp else None,
        "fees": buy_order.fees if buy_order else None, "slippage": buy_order.slippage if buy_order else None,
        "atr_pct": candidate.get("atr_pct"), "sizing_multiplier": dp.get("sizing_multiplier"),
        "requested_capital": dp.get("requested_capital"), "actual_allocation": dp.get("actual_allocated_capital"),
        "advisory": candidate.get("advisory_annotations") or {},
        "sector": metadata.sector if metadata else "NOT_AVAILABLE",
        "catastrophe_threshold": pp.get("catastrophe_threshold"),
        "rv20_pct": pp.get("rv20_pct"), "risk_proxy_pct": pp.get("risk_proxy_pct"),
        "risk_proxy_coverage": pp.get("risk_proxy_coverage"),
        "risk_contribution": (market_value * float(pp.get("risk_proxy_pct")) / 100 *
            float(pp.get("correlation_multiplier") or 1.)) if pp.get("risk_proxy_pct") not in {None, "NOT_AVAILABLE"} else None,
        "correlation_multiplier": pp.get("correlation_multiplier"),
    }


def _execution_projection(rows) -> dict[str, Any]:
    payloads = [(_json(row.payload), row) for row in rows]
    by_order = defaultdict(list)
    for payload, row in payloads: by_order[row.order_id].append((payload, row))
    filled = [item for item in payloads if item[1].execution_status == "FILLED"]
    states = Counter(row.market_state for _, row in payloads)
    slippage = defaultdict(list)
    for payload, row in filled:
        theoretical, actual = payload.get("theoretical_slippage_price"), payload.get("simulated_fill_price")
        if theoretical is not None and actual is not None: slippage[row.market_state].append(float(actual) - float(theoretical))
    return {"attempted_entries": sum(row.side == "BUY" for _, row in payloads),
        "attempted_exits": sum(row.side == "SELL" for _, row in payloads),
        "filled_attempts": len(filled), "attempts": len(rows),
        "executable_fill_rate": len(filled) / len(rows) if rows else None,
        "normal_fills": sum(row.market_state == "NORMAL_EXECUTABLE" for _, row in filled),
        "gap_fills": sum(row.market_state == "GAP_EXECUTION" for _, row in filled),
        "locked_range_attempts": states["LOCKED_RANGE"], "zero_volume_attempts": states["ZERO_VOLUME"],
        "confirmed_upper_circuit_attempts": states["CONFIRMED_UPPER_CIRCUIT"],
        "confirmed_lower_circuit_attempts": states["CONFIRMED_LOWER_CIRCUIT"],
        "unfilled_orders": len({row.order_id for _, row in payloads if row.execution_status != "FILLED"}),
        "next_session_eventual_fills": sum(len(items) > 1 and any(row.execution_status == "FILLED" for _, row in items) for items in by_order.values()),
        "average_execution_delay_sessions": (sum(max(0, len(items) - 1) for items in by_order.values() if any(row.execution_status == "FILLED" for _, row in items)) /
            sum(any(row.execution_status == "FILLED" for _, row in items) for items in by_order.values())) if any(any(row.execution_status == "FILLED" for _, row in items) for items in by_order.values()) else None,
        "slippage_vs_theoretical_by_state": {key: sum(values) / len(values) for key, values in slippage.items()},
        "circuit_source": "NOT_AVAILABLE"}


def load_autopaper_ui_state(opportunity_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Build a persisted-state projection. This function performs SELECTs only."""
    enabled = os.environ.get("AUTOPAPER_PROSPECTIVE_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    killed = os.environ.get("AUTOPAPER_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes"}
    base = {"version": VERSION, "methodology_hash": METHODOLOGY_HASH, "config_hash": CONFIG_HASH,
            "activation_timestamp": ACTIVATION_TIMESTAMP, "first_market_date": FIRST_MARKET_DATE,
            "paper_only": True, "enabled": enabled, "kill_switch": killed, "accounts": {}, "shadow_positions": {},
            "positions": [], "orders": {"pending_entries": [], "pending_exits": [], "unfilled": []},
            "latest_decisions": [], "opportunity_statuses": {}, "snapshots": {}, "recent_trades": [],
            "review_gate": review_gate_progress({}), "health": {"status": "NOT YET RUN"},
            "execution_quality": _execution_projection([]), "risk_observability": {},
            "catastrophe": {"triggers": 0, "fills": 0, "mature": 0}}
    try:
        database._require_database()
        session = database.SessionLocal()
        try:
            accounts = {row.account_id: row for row in session.query(database.AutoPaperAccount).all()}
            health_row = session.query(database.AutoPaperHealth).order_by(database.AutoPaperHealth.market_date.desc(), database.AutoPaperHealth.created_at.desc()).first()
            successful_health = session.query(database.AutoPaperHealth).filter_by(status="HEALTHY").order_by(
                database.AutoPaperHealth.market_date.desc(), database.AutoPaperHealth.created_at.desc()).first()
            all_positions = session.query(database.AutoPaperPosition).all()
            all_orders = session.query(database.AutoPaperOrder).order_by(database.AutoPaperOrder.created_at.desc()).all()
            all_trades = session.query(database.AutoPaperTrade).order_by(database.AutoPaperTrade.exit_date.desc()).all()
            all_snapshots = session.query(database.AutoPaperPortfolioSnapshot).order_by(database.AutoPaperPortfolioSnapshot.market_date.asc()).all()
            decisions = session.query(database.AutoPaperDecision).order_by(database.AutoPaperDecision.market_date.desc(), database.AutoPaperDecision.created_at.desc()).all()
            links = session.query(database.AutoPaperCounterfactualLink).all()
            execution_telemetry = session.query(database.AutoPaperExecutionTelemetry).order_by(
                database.AutoPaperExecutionTelemetry.observed_session.desc()).all()
            metadata_rows = session.query(database.AutoPaperOpportunityMetadata).all()
            risk_rows = session.query(database.AutoPaperRiskTelemetry).order_by(
                database.AutoPaperRiskTelemetry.market_date.desc()).all()
            catastrophe_rows = session.query(database.AutoPaperCatastropheObservation).all()
            queue_rows = session.query(database.AutoPaperQueueItem).all()
            role_observations = session.query(database.RoleOutcomeObservation).all()
            role_ids = [x.id for x in role_observations]
            role_horizons = session.query(database.RoleOutcomeHorizon).filter(
                database.RoleOutcomeHorizon.observation_id.in_(role_ids),
                database.RoleOutcomeHorizon.horizon_sessions == 10).all() if role_ids else []
            activation_amendment = session.query(database.AutoPaperActivationAmendment).filter_by(
                status="COMPLETED").order_by(database.AutoPaperActivationAmendment.created_at.desc()).first()
            rolling_activation = session.query(database.AutoPaperRollingActivation).order_by(
                database.AutoPaperRollingActivation.created_at.desc()).first()
            rolling_telemetry = session.query(database.AutoPaperRollingTelemetry).filter_by(
                account_id="SHADOW_ROLLING").order_by(
                database.AutoPaperRollingTelemetry.market_date.asc()).all()
            edge_activation = session.query(database.EdgeCaptureActivation).order_by(
                database.EdgeCaptureActivation.created_at.desc()).first()
            edge_matches = session.query(database.EdgeCaptureMatch).all()
            edge_thesis = session.query(database.EdgeCaptureThesisObservation).all()
            edge_exits = session.query(database.EdgeCaptureExitObservation).all()
            edge_telemetry = session.query(database.EdgeCaptureTelemetry).order_by(
                database.EdgeCaptureTelemetry.market_date.asc()).all()
            portfolio_risk_activation = session.query(database.PortfolioRiskActivation).order_by(
                database.PortfolioRiskActivation.created_at.desc()).first()
            portfolio_risk_decisions = session.query(database.PortfolioRiskDecisionSnapshot).order_by(
                database.PortfolioRiskDecisionSnapshot.market_date.desc()).all()
            portfolio_risk_matches = session.query(database.PortfolioRiskMatch).all()
            portfolio_risk_telemetry = session.query(database.PortfolioRiskTelemetry).order_by(
                database.PortfolioRiskTelemetry.market_date.asc()).all()
            selection_activation = session.query(database.OpportunitySelectionActivation).order_by(
                database.OpportunitySelectionActivation.created_at.desc()).first()
            selection_snapshots = session.query(database.OpportunitySelectionCandidateSnapshot).order_by(
                database.OpportunitySelectionCandidateSnapshot.market_date.desc(),
                database.OpportunitySelectionCandidateSnapshot.selection_rank.asc()).all()
            selection_random = session.query(database.OpportunitySelectionRandomOrdering).all()
            exposure_activation = session.query(database.DynamicExposureActivation).order_by(
                database.DynamicExposureActivation.created_at.desc()).first()
            exposure_decisions = session.query(database.DynamicExposureDecisionSnapshot).order_by(
                database.DynamicExposureDecisionSnapshot.market_date.desc()).all()
            exposure_telemetry = session.query(database.DynamicExposureTelemetry).order_by(
                database.DynamicExposureTelemetry.market_date.asc()).all()
        finally: session.close()
    except Exception as exc:
        base["health"] = {"status": "NOT AVAILABLE", "reason": type(exc).__name__}
        return base

    by_positions, by_orders, by_trades, by_snapshots = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for row in all_positions: by_positions[row.account_id].append(row)
    for row in all_orders: by_orders[row.account_id].append(row)
    for row in all_trades: by_trades[row.account_id].append(row)
    for row in all_snapshots: by_snapshots[row.account_id].append(row)
    for account_id in (BASELINE, *SHADOWS, *EDGE_ACCOUNTS, *RISK_ACCOUNTS,
                       *SELECTION_ACCOUNTS, *EXPOSURE_ACCOUNTS):
        account = accounts.get(account_id); positions = [p for p in by_positions[account_id] if p.status == "OPEN"]
        base["accounts"][account_id] = _account_metrics(account, positions, by_trades[account_id], by_snapshots[account_id]) if account else None
        base["snapshots"][account_id] = [{"date": x.market_date.isoformat(), "nav": x.nav, "cash": x.cash} for x in by_snapshots[account_id]]

    baseline_orders = by_orders[BASELINE]
    metadata = {row.opportunity_id: row for row in metadata_rows}
    order_by_opportunity = {row.opportunity_id: row for row in reversed(baseline_orders) if row.side == "BUY"}
    entry_decisions = {row.opportunity_id: row for row in reversed(decisions) if row.account_id == BASELINE and row.action == "ENTER"}
    nav = (base["accounts"].get(BASELINE) or {}).get("nav")
    base["positions"] = [_position_row(row, order_by_opportunity.get(row.opportunity_id), entry_decisions.get(row.opportunity_id), nav, metadata.get(row.opportunity_id))
                         for row in by_positions[BASELINE] if row.status == "OPEN"]
    for account_id in (*SHADOWS, *EDGE_ACCOUNTS, *RISK_ACCOUNTS,
                       *SELECTION_ACCOUNTS, *EXPOSURE_ACCOUNTS):
        shadow_orders = {row.opportunity_id: row for row in reversed(by_orders[account_id]) if row.side == "BUY"}
        shadow_decisions = {row.opportunity_id: row for row in reversed(decisions) if row.account_id == account_id and row.action == "ENTER"}
        shadow_nav = (base["accounts"].get(account_id) or {}).get("nav")
        base["shadow_positions"][account_id] = [_position_row(row, shadow_orders.get(row.opportunity_id),
            shadow_decisions.get(row.opportunity_id), shadow_nav, metadata.get(row.opportunity_id))
            for row in by_positions[account_id] if row.status == "OPEN"]

    for order in baseline_orders:
        payload = _json(order.payload); candidate = payload.get("candidate") or {}
        row = {"symbol": order.symbol, "opportunity_id": order.opportunity_id,
               "signal_date": candidate.get("signal_date"), "requested_session": order.requested_session.isoformat(),
               "intended_execution_date": payload.get("intended_execution_date"),
               "origin": payload.get("prospective_origin") or candidate.get("prospective_origin") or "PROSPECTIVE",
               "side": order.side, "quantity": order.quantity, "requested_capital": order.requested_capital,
               "status": order.status, "display_status": lifecycle_display(order_status=order.status, side=order.side),
               "next_action": "Next executable open" if order.status == "PENDING" else humanize_reason(order.status)}
        latest_attempt = next((x for x in execution_telemetry if x.order_id == order.order_id), None)
        row["market_state"] = latest_attempt.market_state if latest_attempt else "NOT_AVAILABLE"
        row["fillability_reason"] = (_json(latest_attempt.payload).get("fillability_reason") if latest_attempt else "Not yet attempted")
        if order.status == "PENDING": base["orders"]["pending_exits" if order.side == "SELL" else "pending_entries"].append(row)
        elif order.status in {"FAILED_DATA", "EXPIRED", "REJECTED"}: base["orders"]["unfilled"].append(row)

    latest_date = max((row.market_date for row in decisions if row.account_id == BASELINE), default=None)
    today = [row for row in decisions if row.account_id == BASELINE and row.market_date == latest_date]
    latest_by_opportunity = {}
    for row in reversed(today): latest_by_opportunity[row.opportunity_id] = row
    for row in latest_by_opportunity.values():
        payload = _json(row.payload)
        order = next((x for x in baseline_orders if x.opportunity_id == row.opportunity_id), None)
        order_candidate = (_json(order.payload).get("candidate") or {}) if order else {}
        base["latest_decisions"].append({"symbol": order.symbol if order else row.opportunity_id.split(":")[1] if ":" in row.opportunity_id else row.opportunity_id,
            "strategy": order_candidate.get("strategy") or "Not available",
            "opportunity_id": row.opportunity_id, "decision": row.action, "reason_code": row.reason_code,
            "reason": humanize_reason(row.reason_code), "rank": payload.get("rank"),
            "requested_capital": payload.get("requested_capital"), "actual_allocation": payload.get("actual_allocated_capital"),
            "sizing_multiplier": payload.get("sizing_multiplier"),
            "status": lifecycle_display(action=row.action, order_status=order.status if order else None, side=order.side if order else None),
            "next_action": "Next executable open" if order and order.status == "PENDING" else humanize_reason(row.reason_code)})

    requested = set(str(x) for x in opportunity_ids)
    all_baseline_decisions = [row for row in decisions if row.account_id == BASELINE and (not requested or row.opportunity_id in requested)]
    for row in all_baseline_decisions:
        if row.opportunity_id in base["opportunity_statuses"]: continue
        payload = _json(row.payload); order = next((x for x in baseline_orders if x.opportunity_id == row.opportunity_id), None)
        base["opportunity_statuses"][row.opportunity_id] = {"state": lifecycle_display(action=row.action, order_status=order.status if order else None, side=order.side if order else None),
            "reason": humanize_reason(row.reason_code), "reason_code": row.reason_code,
            "rank": payload.get("rank"), "sizing_multiplier": payload.get("sizing_multiplier"),
            "actual_allocation": payload.get("actual_allocated_capital"), "order_status": order.status if order else None}

    health_payload = _json(health_row.payload) if health_row else {}
    health_status = "DISABLED" if not enabled else "DISABLED" if killed else health_row.status if health_row else "NOT YET RUN"
    if health_row and health_status == "HEALTHY" and (dt.date.today() - health_row.market_date).days > 4:
        health_status = "STALE"
    base["health"] = {"status": health_status, "market_date": health_row.market_date.isoformat() if health_row else None,
        "last_run": health_row.run_timestamp.isoformat() if health_row else None, "warnings": health_payload.get("warnings") or [],
        "errors": health_payload.get("errors") or [], "accounts": health_payload.get("accounts") or {},
        "last_successful_run": successful_health.run_timestamp.isoformat() if successful_health else None,
        "reconciliation": "PASS" if health_row and not health_payload.get("errors") else "NOT AVAILABLE"}
    if activation_amendment:
        base["activation_amendment"] = {
            "id": activation_amendment.amendment_id,
            "signal_date": activation_amendment.amended_signal_date.isoformat(),
            "first_execution_date": activation_amendment.first_execution_date.isoformat(),
            "amended_config_hash": activation_amendment.amended_config_hash,
            "methodology_hash": activation_amendment.methodology_hash,
        }
        base["config_hash"] = activation_amendment.amended_config_hash
    baseline_links = [x for x in links if x.account_id == BASELINE]
    completed = len(by_trades[BASELINE]); signal_dates = len({x.signal_date for x in baseline_links})
    first_signal = min((x.signal_date for x in baseline_links), default=None)
    calendar_days = max(0, (dt.date.today() - first_signal).days + 1) if first_signal else 0
    strategies = len({_json(x.payload).get("strategy") for x in by_trades[BASELINE] if _json(x.payload).get("strategy")})
    capacity = sum(x.account_id == BASELINE and x.reason_code == "NO_CAPACITY" for x in decisions)
    # Frozen baseline review gate remains tied to its original C0/D1 challengers.
    overlap = min([len(by_trades[x]) for x in (BASELINE, "SHADOW_C0", "SHADOW_D1")], default=0)
    base["review_gate"] = review_gate_progress({"completed_trades": completed, "signal_dates": signal_dates,
        "calendar_days": calendar_days, "strategies": strategies, "capacity_decisions": capacity, "challenger_overlaps": overlap})
    base["catastrophe_overlap"] = min(len(by_trades[BASELINE]), len(by_trades["SHADOW_CATASTROPHE"]))
    base["role_linkage_status"] = "ACTIVE" if baseline_links else "NOT YET RUN"
    rolling_links = [x for x in links if x.account_id == "SHADOW_ROLLING"]
    rolling_decisions = [x for x in decisions if x.account_id == "SHADOW_ROLLING"]
    rolling_queue = [x for x in queue_rows if x.account_id == "SHADOW_ROLLING"]
    rolling_orders = by_orders["SHADOW_ROLLING"]
    deferred_ids = {x.opportunity_id for x in rolling_decisions if x.reason_code in {
        "ROLLING_DAILY_ADMISSION_LIMIT", "ROLLING_TARGET_OCCUPANCY"}}
    entered_ids = {x.opportunity_id for x in rolling_links if x.entered}
    expired_ids = {x.opportunity_id for x in rolling_links if x.terminal_reason == "EXPIRED"}
    telemetry_payloads = [_json(x.payload) for x in rolling_telemetry]
    latest_rolling = telemetry_payloads[-1] if telemetry_payloads else {}
    base["rolling"] = {
        "activation": ({"status": rolling_activation.status, "mode": rolling_activation.activation_mode,
            "activation_signal_date": rolling_activation.activation_signal_date.isoformat() if rolling_activation.activation_signal_date else None,
            "activation_timestamp": rolling_activation.activation_timestamp.isoformat(),
            "provenance": rolling_activation.provenance, "methodology_hash": rolling_activation.methodology_hash,
            "config_hash": rolling_activation.config_hash} if rolling_activation else {"status": "NOT_ACTIVATED"}),
        "current": latest_rolling,
        "total_considered": len(rolling_links), "total_entered": len(entered_ids),
        "total_queued_decisions": sum(x.action == "QUEUE" for x in rolling_decisions),
        "total_expired": len(expired_ids),
        "hard_capacity_blocked": sum(x.reason_code == "NO_CAPACITY" for x in rolling_decisions),
        "rolling_deferred": len(deferred_ids),
        "deferred_then_entered": len(deferred_ids & entered_ids),
        "deferred_then_expired": len(deferred_ids & expired_ids),
        "valid_queued": sum(x.status == "ACTIVE" for x in rolling_queue),
        "pending_entries": sum(x.side == "BUY" and x.status == "PENDING" for x in rolling_orders),
        "mean_occupancy": (sum(float(x.get("open_positions") or 0) for x in telemetry_payloads) / len(telemetry_payloads)) if telemetry_payloads else None,
        "median_occupancy": median(float(x.get("open_positions") or 0) for x in telemetry_payloads) if telemetry_payloads else None,
        "pct_sessions_at_target": (sum(int(x.get("open_positions") or 0) == 9 for x in telemetry_payloads) / len(telemetry_payloads)) if telemetry_payloads else None,
        "pct_sessions_at_hard_capacity": (sum(int(x.get("open_positions") or 0) >= 10 for x in telemetry_payloads) / len(telemetry_payloads)) if telemetry_payloads else None,
        "qualified_participation_rate": len(entered_ids) / len(rolling_links) if rolling_links else None,
        "hard_capacity_block_rate": sum(x.reason_code == "NO_CAPACITY" for x in rolling_decisions) / len(rolling_links) if rolling_links else None,
        "rolling_deferral_rate": len(deferred_ids) / len(rolling_links) if rolling_links else None,
        "eventual_entry_rate": len(deferred_ids & entered_ids) / len(deferred_ids) if deferred_ids else None,
        "expiry_after_deferral_rate": len(deferred_ids & expired_ids) / len(deferred_ids) if deferred_ids else None,
    }
    edge_payloads = defaultdict(list)
    for row in edge_telemetry: edge_payloads[row.account_id].append(_json(row.payload))
    edge_trade_payloads = {account_id: [_json(x.payload) for x in by_trades[account_id]] for account_id in EDGE_ACCOUNTS}
    matched_groups = defaultdict(set)
    for row in edge_matches:
        if row.execution_date: matched_groups[row.edge_capture_match_id].add(row.account_id)
    completed_groups = sum(len(accounts) >= 2 and all(any(
        x.edge_capture_match_id == match_id and x.account_id == account and x.exit_date
        for x in edge_matches) for account in accounts) for match_id, accounts in matched_groups.items())
    thesis_payloads = [_json(x.payload) for x in edge_thesis]
    exit_payloads = [_json(x.payload) for x in edge_exits]
    edge_accounts = {}
    labels = {"SHADOW_EDGE_H20": ("E1", "Rolling + H20"),
        "SHADOW_EDGE_H20_CATASTROPHE": ("E2", "Rolling + H20 + catastrophe"),
        "SHADOW_EDGE_H20_THESIS": ("E3", "Rolling + H20 + thesis failure")}
    for account_id, (policy, philosophy) in labels.items():
        telemetry = edge_payloads[account_id]; trades_for_account = by_trades[account_id]
        trade_payload = edge_trade_payloads[account_id]
        valid_capture = [x.realized_return_pct / float(payload["mfe_pct"]) for x, payload in zip(
            trades_for_account, trade_payload) if float(payload.get("mfe_pct") or 0) > .25]
        edge_accounts[account_id] = {"policy": policy, "exit_philosophy": philosophy,
            "metrics": base["accounts"].get(account_id),
            "mean_hold": (sum(x.holding_sessions for x in trades_for_account) / len(trades_for_account)) if trades_for_account else None,
            "mfe_capture": median(valid_capture) if valid_capture else None,
            "mean_occupancy": (sum(float(x.get("open_positions") or 0) for x in telemetry) / len(telemetry)) if telemetry else None,
            "capital_days": sum(float(x.get("capital_days") or 0) for x in telemetry),
            "capacity_divergence_events": sum(bool(x.get("capacity_divergence")) for x in telemetry),
            "capacity_blocked": sum(int(x.get("capacity_blocked") or 0) for x in telemetry),
            "rolling_deferred": sum(int(x.get("rolling_deferred") or 0) for x in telemetry)}
    signal_dates = {row.signal_date for row in edge_matches}
    strategies = {p.get("strategy") for values in edge_trade_payloads.values() for p in values if p.get("strategy")}
    activation_payload = _json(edge_activation.payload) if edge_activation else {}
    base["edge_capture"] = {"activation": activation_payload or {"status": "NOT_ACTIVATED"},
        "control": {"policy": "E0", "account_id": "SHADOW_ROLLING", "exit_philosophy": "Rolling + H10",
                    "metrics": base["accounts"].get("SHADOW_ROLLING")},
        "accounts": edge_accounts, "matched_groups": len(matched_groups), "completed_groups": completed_groups,
        "unique_signal_dates": len(signal_dates), "represented_strategies": len(strategies),
        "capacity_divergence_events": sum(v["capacity_divergence_events"] for v in edge_accounts.values()),
        "thesis_state_observations": len(edge_thesis),
        "confirmed_thesis_failures": sum(p.get("state") == "TRUE" and int(p.get("consecutive_failure_sessions") or 0) >= 2 for p in thesis_payloads),
        "catastrophe_exits": sum(x.exit_type == "CATASTROPHE_EXIT" for x in edge_exits),
        "thesis_exits": sum(x.exit_type == "THESIS_FAILURE_EXIT" for x in edge_exits),
        "mature_early_exits": sum(x.status == "MATURE" for x in edge_exits),
        "mean_exit_regret_pct": (median(float(x["exit_regret_pct"]) for x in exit_payloads if x.get("exit_regret_pct") is not None)
            if any(x.get("exit_regret_pct") is not None for x in exit_payloads) else None),
        "review_gate": {"calendar_days": 90, "completed_matched_groups": 60,
            "unique_signal_dates": 30, "represented_strategies": 3, "capacity_divergence_events": 20}}
    risk_payloads = defaultdict(list)
    for row in portfolio_risk_telemetry:
        risk_payloads[row.account_id].append(_json(row.payload))
    risk_accounts = {}
    for account_id, policy, meaning in (("SHADOW_RISK_BUDGET", "B1", "Explicit risk budget"),
            ("SHADOW_RISK_DIVERSIFIED", "B2", "Risk budget + redundancy")):
        telemetry = risk_payloads[account_id]
        latest = telemetry[-1] if telemetry else {}
        account_decisions = [row for row in portfolio_risk_decisions if row.account_id == account_id]
        risk_reason_codes = {"PORTFOLIO_RISK_BUDGET", "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK",
            "CORRELATION_RISK_BUDGET", "SECTOR_RISK_CONCENTRATION",
            "SIGNAL_DATE_RISK_CONCENTRATION"}
        deferred_ids = {row.opportunity_id for row in account_decisions
            if row.final_decision == "DEFER" and row.reason_code in risk_reason_codes}
        links_for_account = [row for row in links if row.account_id == account_id]
        entered_ids_for_account = {row.opportunity_id for row in links_for_account if row.entered}
        expired_ids_for_account = {row.opportunity_id for row in links_for_account if row.terminal_reason == "EXPIRED"}
        risk_accounts[account_id] = {"policy": policy, "meaning": meaning,
            "metrics": base["accounts"].get(account_id), "latest": latest,
            "risk_deferred": len(deferred_ids),
            "downsized": sum(row.final_decision == "ADMIT_DOWNSIZED" for row in account_decisions),
            "redundancy_deferred": sum(row.reason_code in {"SECTOR_RISK_CONCENTRATION",
                "SIGNAL_DATE_RISK_CONCENTRATION", "CORRELATION_RISK_BUDGET"} for row in account_decisions),
            "risk_deferred_then_entered": len(deferred_ids & entered_ids_for_account),
            "risk_deferred_then_expired": len(deferred_ids & expired_ids_for_account),
            "capital_days": sum(float(payload.get("capital_days") or 0) for payload in telemetry)}
    groups = defaultdict(set)
    completed = defaultdict(set)
    for row in portfolio_risk_matches:
        groups[row.match_id].add(row.account_id)
        if row.exit_date:
            completed[row.match_id].add(row.account_id)
    risk_reason_codes = {"PORTFOLIO_RISK_BUDGET", "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK",
        "CORRELATION_RISK_BUDGET", "SECTOR_RISK_CONCENTRATION",
        "SIGNAL_DATE_RISK_CONCENTRATION"}
    risk_interventions = sum(row.final_decision == "ADMIT_DOWNSIZED" or
        (row.final_decision == "DEFER" and row.reason_code in risk_reason_codes)
        for row in portfolio_risk_decisions)
    redundancy_interventions = sum(row.reason_code in {"SECTOR_RISK_CONCENTRATION",
        "SIGNAL_DATE_RISK_CONCENTRATION", "CORRELATION_RISK_BUDGET"} for row in portfolio_risk_decisions)
    risk_dates = {row.market_date for row in portfolio_risk_decisions}
    activation_risk_payload = _json(portfolio_risk_activation.payload) if portfolio_risk_activation else {}
    control_metrics = dict(base["accounts"].get("SHADOW_ROLLING") or {})
    control_risk = 0.
    for position in by_positions["SHADOW_ROLLING"]:
        if position.status != "OPEN":
            continue
        payload = _json(position.payload)
        try:
            control_risk += position.quantity * position.current_mark * float(payload["atr_pct"]) / 100
        except (KeyError, TypeError, ValueError):
            pass
    if control_metrics.get("nav"):
        control_metrics["portfolio_heat_pct"] = control_risk / control_metrics["nav"] * 100
    base["portfolio_risk"] = {"activation": activation_risk_payload or {"status": "NOT_ACTIVATED"},
        "control": {"policy": "B0", "meaning": "Rolling + C3 control",
            "metrics": control_metrics},
        "accounts": risk_accounts, "matched_groups": len(groups),
        "completed_matched_groups": sum(len(completed[key]) >= 3 for key in groups),
        "unique_signal_dates": len(risk_dates), "risk_interventions": risk_interventions,
        "redundancy_interventions": redundancy_interventions,
        "review_gate": {"calendar_days": 90, "completed_b1_trades": 75,
            "completed_b2_trades": 75, "unique_signal_dates": 40,
            "risk_interventions": 30, "redundancy_interventions": 20,
            "completed_matched_groups": 60},
        "latest_decisions": [{"account_id": row.account_id, "opportunity_id": row.opportunity_id,
            "market_date": row.market_date.isoformat(), "decision": row.final_decision,
            "reason_code": row.reason_code, "reason": humanize_reason(row.reason_code),
            **{key: value for key, value in _json(row.payload).items() if key in {
                "risk_proxy_pct", "risk_proxy_coverage", "final_allocation", "current_heat_pct",
                "remaining_normal_heat_rupees", "sector", "sector_heat_share_of_normal_budget",
                "signal_date_heat_share_of_normal_budget", "weighted_avg_corr",
                "correlation_multiplier"}}} for row in portfolio_risk_decisions[:100]]}
    from opportunity_selection_engine import selection_attribution
    horizon_by_observation = {row.observation_id: _json(row.payload) for row in role_horizons}
    outcome_by_opportunity = {row.opportunity_id: horizon_by_observation[row.id]
                              for row in role_observations if row.id in horizon_by_observation}
    selection_accounts = {}
    latest_selection_date = max((row.market_date for row in selection_snapshots), default=None)
    opportunity_details = {}
    for account_id in SELECTION_ACCOUNTS:
        snapshots = [row for row in selection_snapshots if row.account_id == account_id]
        payloads = [{**_json(row.payload), "admitted": row.admitted,
                     "outcome": outcome_by_opportunity.get(row.opportunity_id)} for row in snapshots]
        score_field = "quality_score" if account_id == "SHADOW_SELECT_QUALITY" else "risk_adjusted_quality"
        constrained_events = {row.selection_event_id for row in snapshots if row.constrained}
        mature = [row for row in payloads if isinstance(row.get("outcome"), dict)]
        by_event = defaultdict(list)
        for row in mature:
            if row.get("constrained"): by_event[row["selection_event_id"]].append(row)
        event_analytics = [selection_attribution(rows, score_field) for rows in by_event.values()]
        def avg(path, default=None):
            values = [item.get(path) for item in event_analytics if _finite(item.get(path)) is not None]
            return sum(values) / len(values) if values else default
        latest = [row for row in payloads if str(row.get("market_date")) == str(latest_selection_date)]
        selection_accounts[account_id] = {
            "policy": "C1" if account_id.endswith("QUALITY") else "C2",
            "selection": "Transparent quality" if account_id.endswith("QUALITY") else "Risk-adjusted quality",
            "metrics": base["accounts"].get(account_id), "constrained_events": len(constrained_events),
            "mature_comparisons": len(mature), "latest": latest,
            "rank_ic": avg("event_rank_ic"),
            "selection_lift_h10": (sum(float(item["selection_lift"]["h10_net_return_pct"])
                for item in event_analytics if _finite(item["selection_lift"].get("h10_net_return_pct")) is not None) /
                sum(_finite(item["selection_lift"].get("h10_net_return_pct")) is not None for item in event_analytics)
                if any(_finite(item["selection_lift"].get("h10_net_return_pct")) is not None for item in event_analytics) else None),
            "event_analytics": event_analytics,
        }
        for row in latest:
            opportunity_details.setdefault(row["opportunity_id"], {})[selection_accounts[account_id]["policy"]] = {
                "quality_score": row.get("quality_score"), "risk_percentile": row.get("risk_percentile"),
                "risk_adjusted_quality": row.get("risk_adjusted_quality"), "rank": row.get("selection_rank"),
                "state": "SELECTED" if row.get("admitted") else "DEFERRED",
                "reason": humanize_reason(row.get("reason_code")), "label": "Selection rank"}
    selection_dates = {row.market_date for row in selection_snapshots if row.constrained}
    first_selection_date = min((row.market_date for row in selection_snapshots), default=None)
    calendar_days = (dt.date.today() - first_selection_date).days + 1 if first_selection_date else 0
    constrained_event_ids = {row.selection_event_id for row in selection_snapshots if row.constrained}
    competitive_ids = {event_id for event_id in constrained_event_ids if sum(
        row.selection_event_id == event_id and row.account_id == "SHADOW_SELECT_QUALITY"
        for row in selection_snapshots) >= 3}
    # C0 is deliberately not duplicated.  Reconcile its existing B1 decisions
    # to the immutable C1 event identity so completed comparisons are genuinely
    # matched across C0/C1/C2 rather than inferred from aggregate returns.
    c0_lookup = {(row.market_date, row.opportunity_id): row for row in portfolio_risk_decisions
                 if row.account_id == "SHADOW_RISK_BUDGET"}
    c2_event_ids = {row.selection_event_id for row in selection_snapshots
                    if row.account_id == "SHADOW_SELECT_RISK_ADJ"}
    c1_events = defaultdict(list)
    for row in selection_snapshots:
        if row.account_id == "SHADOW_SELECT_QUALITY" and row.constrained:
            c1_events[row.selection_event_id].append(row)
    matched_control_analytics = []
    for event_id, rows in c1_events.items():
        control_rows = [c0_lookup.get((row.market_date, row.opportunity_id)) for row in rows]
        if event_id not in c2_event_ids or any(row is None for row in control_rows):
            continue
        event_payloads = []
        for selection_row, control_row in zip(rows, control_rows):
            selection_payload = _json(selection_row.payload)
            outcome = outcome_by_opportunity.get(selection_row.opportunity_id)
            if not isinstance(outcome, dict):
                event_payloads = []
                break
            event_payloads.append({"opportunity_id": selection_row.opportunity_id,
                "p0_score": -float(selection_payload["p0_freshness_order"]),
                "admitted": str(control_row.final_decision).startswith("ADMIT"),
                "outcome": outcome})
        if event_payloads:
            matched_control_analytics.append(selection_attribution(event_payloads, "p0_score"))
    completed_matched_events = len(matched_control_analytics)
    base["opportunity_selection"] = {
        "activation": _json(selection_activation.payload) if selection_activation else {"status": "NOT_ACTIVATED"},
        "control": {"policy": "C0", "selection": "P0 freshness",
                    "metrics": base["accounts"].get("SHADOW_RISK_BUDGET"),
                    "matched_event_analytics": matched_control_analytics},
        "accounts": selection_accounts, "opportunity_details": opportunity_details,
        "constrained_events": len(constrained_event_ids), "competitive_events": len(competitive_ids),
        "completed_matched_events": completed_matched_events,
        "unique_signal_dates": len(selection_dates), "mature_comparisons": sum(
            values["mature_comparisons"] for values in selection_accounts.values()),
        "random_seed_count": len({row.seed for row in selection_random}),
        "review_gate": {"calendar_days": {"value": calendar_days, "target": 90},
            "unique_signal_dates": {"value": len(selection_dates), "target": 40},
            "constrained_selection_events": {"value": len(constrained_event_ids), "target": 50},
            "competitive_events": {"value": len(competitive_ids), "target": 30},
            "mature_comparisons": {"value": sum(v["mature_comparisons"] for v in selection_accounts.values()), "target": 100},
            "completed_matched_events": {"value": completed_matched_events, "target": 60},
            "random_seeds": {"value": len({row.seed for row in selection_random}), "target": 20}},
    }
    exposure_payloads = defaultdict(list)
    for row in exposure_telemetry:
        exposure_payloads[row.account_id].append(_json(row.payload))
    exposure_accounts = {}
    for account_id, policy, meaning in (
            ("SHADOW_EXPOSURE_PORTFOLIO", "D1", "Portfolio-stress throttle"),
            ("SHADOW_EXPOSURE_COMBINED", "D2", "Portfolio + market-stress throttle")):
        telemetry = exposure_payloads[account_id]
        latest = telemetry[-1] if telemetry else {}
        decisions_for_account = [row for row in exposure_decisions if row.account_id == account_id]
        deferred_ids = {row.opportunity_id for row in decisions_for_account
                        if row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED"}
        links_for_account = [row for row in links if row.account_id == account_id]
        entered_ids = {row.opportunity_id for row in links_for_account if row.entered}
        expired_ids = {row.opportunity_id for row in links_for_account
                       if row.terminal_reason == "EXPIRED"}
        reduced = [payload for payload in telemetry
                   if float(payload.get("combined_multiplier") or 1.) < 1.]
        low_multiplier = [payload for payload in telemetry
                          if float(payload.get("combined_multiplier") or 1.) <= .75]
        exposure_accounts[account_id] = {"policy": policy, "meaning": meaning,
            "metrics": base["accounts"].get(account_id), "latest": latest,
            "days_full_exposure": len(telemetry) - len(reduced),
            "days_reduced": len(reduced),
            "multiplier_distribution": [payload.get("combined_multiplier") for payload in telemetry],
            "interventions": sum(row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED" or
                                 row.final_decision == "ADMIT_DOWNSIZED"
                                 for row in decisions_for_account),
            "multiplier_at_or_below_075_events": len(low_multiplier),
            "deferred": len(deferred_ids),
            "deferred_then_entered": len(deferred_ids & entered_ids),
            "deferred_then_expired": len(deferred_ids & expired_ids),
            "deferred_outcomes": [{"opportunity_id": opportunity_id,
                "lifecycle": "LATER_ENTERED" if opportunity_id in entered_ids else
                    ("EXPIRED" if opportunity_id in expired_ids else "PENDING"),
                "outcome": outcome_by_opportunity.get(opportunity_id)}
                for opportunity_id in sorted(deferred_ids)]}
    matched_completed = set.intersection(*[
        {row.opportunity_id for row in by_trades[account_id]}
        for account_id in ("SHADOW_RISK_BUDGET", *EXPOSURE_ACCOUNTS)
    ]) if all(by_trades[account_id] for account_id in ("SHADOW_RISK_BUDGET", *EXPOSURE_ACCOUNTS)) else set()
    exposure_dates = {row.market_date for row in exposure_decisions}
    exposure_interventions = sum(row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED" or
                                 row.final_decision == "ADMIT_DOWNSIZED"
                                 for row in exposure_decisions)
    below_075 = sum(float(_json(row.payload).get("combined_multiplier") or 1.) <= .75
                    for row in exposure_telemetry)
    first_exposure_date = min((row.market_date for row in exposure_telemetry), default=None)
    exposure_calendar_days = ((dt.date.today() - first_exposure_date).days + 1
                              if first_exposure_date else 0)
    base["dynamic_exposure"] = {
        "activation": _json(exposure_activation.payload) if exposure_activation else {"status": "NOT_ACTIVATED"},
        "explanation": "Dynamic Exposure changes how much new risk may enter. It does not force existing positions to exit.",
        "control": {"policy": "D0", "meaning": "B1 + P0 freshness control",
                    "metrics": base["accounts"].get("SHADOW_RISK_BUDGET")},
        "accounts": exposure_accounts, "unique_signal_dates": len(exposure_dates),
        "intervention_events": exposure_interventions,
        "multiplier_at_or_below_075_events": below_075,
        "matched_completed_opportunities": len(matched_completed),
        "review_gate": {
            "calendar_days": {"value": exposure_calendar_days, "target": 90},
            "unique_signal_dates": {"value": len(exposure_dates), "target": 40},
            "d1_completed_trades": {"value": len(by_trades["SHADOW_EXPOSURE_PORTFOLIO"]), "target": 75},
            "d2_completed_trades": {"value": len(by_trades["SHADOW_EXPOSURE_COMBINED"]), "target": 75},
            "intervention_events": {"value": exposure_interventions, "target": 30},
            "multiplier_at_or_below_075_events": {"value": below_075, "target": 20},
            "matched_completed_opportunities": {"value": len(matched_completed), "target": 60}},
        "latest_decisions": [{"account_id": row.account_id,
            "opportunity_id": row.opportunity_id, "market_date": row.market_date.isoformat(),
            "decision": row.final_decision, "reason_code": row.reason_code,
            "reason": humanize_reason(row.reason_code)} for row in exposure_decisions[:100]],
    }
    latest_rolling_by_opportunity = {}
    for row in rolling_decisions:
        if row.opportunity_id not in latest_rolling_by_opportunity:
            latest_rolling_by_opportunity[row.opportunity_id] = row
    base["rolling_opportunity_statuses"] = {}
    for opportunity_id, row in latest_rolling_by_opportunity.items():
        order = next((x for x in rolling_orders if x.opportunity_id == opportunity_id), None)
        state = ("ROLLING DEFERRED" if row.reason_code in {"ROLLING_DAILY_ADMISSION_LIMIT", "ROLLING_TARGET_OCCUPANCY"}
                 else lifecycle_display(action=row.action, order_status=order.status if order else None,
                                        side=order.side if order else None))
        base["rolling_opportunity_statuses"][opportunity_id] = {
            "state": state, "reason_code": row.reason_code, "reason": humanize_reason(row.reason_code)}
    base["decision_funnel"] = {"qualified": int(health_payload.get("qualified_opportunities") or 0),
        "considered": len(latest_by_opportunity),
        "entered": len({x.opportunity_id for x in today if x.action == "ENTER"}),
        "queued": len({x.opportunity_id for x in today if x.action == "QUEUE"}),
        "rejected": len({x.opportunity_id for x in today if x.action == "REJECT"}),
        "expired": len({x.opportunity_id for x in today if x.action == "EXPIRE"}),
        "market_date": latest_date.isoformat() if latest_date else None}
    base["recent_trades"] = [{"symbol": x.symbol, "strategy": _json(x.payload).get("strategy"),
        "entry": x.entry_date.isoformat(), "exit": x.exit_date.isoformat(), "holding_period": x.holding_sessions,
        "exit_reason": str(_json(x.payload).get("exit_reason") or "H10_TIME_EXIT").replace("_", " ").title(), "gross_pnl": _json(x.payload).get("gross_pnl"), "costs": sum((_json(x.payload).get(k) or 0) for k in ("entry_fee", "exit_fee", "entry_slippage", "exit_slippage")),
        "net_pnl": x.net_pnl, "mfe_pct": _json(x.payload).get("mfe_pct"), "mae_pct": _json(x.payload).get("mae_pct")} for x in by_trades[BASELINE][:20]]
    baseline_telemetry = [x for x in execution_telemetry if x.account_id == BASELINE]
    base["execution_quality"] = _execution_projection(baseline_telemetry)
    latest_risk = next((x for x in risk_rows if x.account_id == BASELINE), None)
    base["risk_observability"] = _json(latest_risk.payload) if latest_risk else {}
    catastrophe_payloads = [_json(x.payload) for x in catastrophe_rows]
    base["catastrophe"] = {"triggers": len(catastrophe_rows),
        "fills": sum(x.status in {"FILLED_AWAITING_H10", "MATURE"} for x in catastrophe_rows),
        "mature": sum(x.status == "MATURE" for x in catastrophe_rows),
        "trigger_rate": len(catastrophe_rows) / len([x for x in links if x.account_id == "SHADOW_CATASTROPHE"]) if any(x.account_id == "SHADOW_CATASTROPHE" for x in links) else None,
        "average_catastrophe_loss_pct": (sum(x.get("net_catastrophe_return") for x in catastrophe_payloads if x.get("net_catastrophe_return") is not None) /
            sum(x.get("net_catastrophe_return") is not None for x in catastrophe_payloads)) if any(x.get("net_catastrophe_return") is not None for x in catastrophe_payloads) else None,
        "average_exit_regret_pct": (sum(x.get("exit_regret_pct") for x in catastrophe_payloads if x.get("exit_regret_pct") is not None) /
            sum(x.get("exit_regret_pct") is not None for x in catastrophe_payloads)) if any(x.get("exit_regret_pct") is not None for x in catastrophe_payloads) else None}
    observations_by_id = {x.id: x for x in role_observations}
    outcomes = {observations_by_id[x.observation_id].opportunity_id: _json(x.payload) for x in role_horizons}
    def _rolling_outcome_summary(ids):
        rows = [outcomes[opportunity_id] for opportunity_id in ids if opportunity_id in outcomes]
        return {"mature_n": len(rows),
            "median_close_return_pct": float(median(float(x["close_return_pct"]) for x in rows)) if rows else None,
            "median_mfe_pct": float(median(float(x["mfe_pct"]) for x in rows)) if rows else None,
            "median_mae_pct": float(median(float(x["mae_pct"]) for x in rows)) if rows else None,
            "plus_5_before_minus_3_rate": sum(x.get("plus_5_before_minus_3") == "TARGET_FIRST" for x in rows) / len(rows) if rows else None}
    if base.get("rolling"):
        base["rolling"]["entered_outcomes"] = _rolling_outcome_summary(entered_ids)
        base["rolling"]["deferred_outcomes"] = _rolling_outcome_summary(deferred_ids - entered_ids)
        base["rolling"]["expired_after_deferral_outcomes"] = _rolling_outcome_summary(deferred_ids & expired_ids)
    baseline_queue = {x.opportunity_id: x for x in queue_rows if x.account_id == BASELINE}
    cohorts = defaultdict(lambda: {"qualified": 0, "entered": 0, "sectors": Counter(),
        "strategies": Counter(), "allocation": 0.0, "outcomes": []})
    for link in baseline_links:
        key = link.signal_date.isoformat(); cohort = cohorts[key]; cohort["qualified"] += 1
        cohort["entered"] += int(link.entered)
        meta = metadata.get(link.opportunity_id); cohort["sectors"][meta.sector if meta else "NOT_AVAILABLE"] += 1
        queue = baseline_queue.get(link.opportunity_id); qp = _json(queue.payload) if queue else {}
        cohort["strategies"][qp.get("strategy", "NOT_AVAILABLE")] += 1
        order = next((x for x in baseline_orders if x.opportunity_id == link.opportunity_id and x.side == "BUY" and x.status == "FILLED"), None)
        if order: cohort["allocation"] += float(order.quantity or 0) * float(order.fill_price or 0) + float(order.fees or 0)
        if link.opportunity_id in outcomes: cohort["outcomes"].append(outcomes[link.opportunity_id])
    base["date_cohorts"] = []
    for date, cohort in sorted(cohorts.items(), reverse=True):
        outcome_rows = cohort.pop("outcomes")
        base["date_cohorts"].append({"signal_date": date, "qualified": cohort["qualified"],
            "entered": cohort["entered"], "sector_mix": dict(cohort["sectors"]),
            "strategy_mix": dict(cohort["strategies"]), "aggregate_allocation": cohort["allocation"],
            "mature_h10_n": len(outcome_rows),
            "median_h10_return_pct": float(median(float(x["close_return_pct"]) for x in outcome_rows)) if outcome_rows else None,
            "median_mfe_pct": float(median(float(x["mfe_pct"]) for x in outcome_rows)) if outcome_rows else None,
            "median_mae_pct": float(median(float(x["mae_pct"]) for x in outcome_rows)) if outcome_rows else None,
            "plus_5_before_minus_3_rate": sum(x.get("plus_5_before_minus_3") == "TARGET_FIRST" for x in outcome_rows) / len(outcome_rows) if outcome_rows else None})
    return base

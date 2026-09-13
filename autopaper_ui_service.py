"""Read-only AutoPaper UI projection. Never advances, simulates, or mutates state."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Iterable

import database


BASELINE = "BASELINE_C3"
SHADOWS = ("SHADOW_C0", "SHADOW_D1")
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
    }


def _position_row(position, buy_order, entry_decision, nav) -> dict[str, Any]:
    pp, op, dp = _json(position.payload), _json(buy_order.payload) if buy_order else {}, _json(entry_decision.payload) if entry_decision else {}
    candidate = op.get("candidate") or {}
    market_value = position.quantity * position.current_mark
    planned = "Exit pending — next executable open" if position.planned_exit_state == "SUBMITTED_NEXT_OPEN" else (
        "H10 exit approaching" if position.age >= 8 else "Hold through session 10")
    return {
        "position_id": position.position_id, "opportunity_id": position.opportunity_id,
        "symbol": position.symbol, "strategy": pp.get("strategy"), "signal_date": pp.get("signal_date"),
        "reference_price": candidate.get("reference_price"), "entry_date": position.entry_date.isoformat(),
        "entry_price": position.entry_price, "current_mark": position.current_mark, "quantity": position.quantity,
        "market_value": market_value, "portfolio_weight_pct": market_value / nav * 100 if nav else None,
        "unrealized_pnl": position.unrealized_pnl, "mfe_pct": position.mfe_pct, "mae_pct": position.mae_pct,
        "hold_day": position.age, "hold_label": f"Day {position.age} / 10", "planned_action": planned,
        "planned_exit_date": position.planned_exit_date.isoformat() if position.planned_exit_date else None,
        "order_date": buy_order.requested_session.isoformat() if buy_order else None,
        "fill_date": buy_order.fill_timestamp.date().isoformat() if buy_order and buy_order.fill_timestamp else None,
        "fees": buy_order.fees if buy_order else None, "slippage": buy_order.slippage if buy_order else None,
        "atr_pct": candidate.get("atr_pct"), "sizing_multiplier": dp.get("sizing_multiplier"),
        "requested_capital": dp.get("requested_capital"), "actual_allocation": dp.get("actual_allocated_capital"),
        "advisory": candidate.get("advisory_annotations") or {},
    }


def load_autopaper_ui_state(opportunity_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Build a persisted-state projection. This function performs SELECTs only."""
    enabled = os.environ.get("AUTOPAPER_PROSPECTIVE_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    killed = os.environ.get("AUTOPAPER_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes"}
    base = {"version": VERSION, "methodology_hash": METHODOLOGY_HASH, "config_hash": CONFIG_HASH,
            "activation_timestamp": ACTIVATION_TIMESTAMP, "first_market_date": FIRST_MARKET_DATE,
            "paper_only": True, "enabled": enabled, "kill_switch": killed, "accounts": {},
            "positions": [], "orders": {"pending_entries": [], "pending_exits": [], "unfilled": []},
            "latest_decisions": [], "opportunity_statuses": {}, "snapshots": {}, "recent_trades": [],
            "review_gate": review_gate_progress({}), "health": {"status": "NOT YET RUN"}}
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
        finally: session.close()
    except Exception as exc:
        base["health"] = {"status": "NOT AVAILABLE", "reason": type(exc).__name__}
        return base

    by_positions, by_orders, by_trades, by_snapshots = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for row in all_positions: by_positions[row.account_id].append(row)
    for row in all_orders: by_orders[row.account_id].append(row)
    for row in all_trades: by_trades[row.account_id].append(row)
    for row in all_snapshots: by_snapshots[row.account_id].append(row)
    for account_id in (BASELINE, *SHADOWS):
        account = accounts.get(account_id); positions = [p for p in by_positions[account_id] if p.status == "OPEN"]
        base["accounts"][account_id] = _account_metrics(account, positions, by_trades[account_id], by_snapshots[account_id]) if account else None
        base["snapshots"][account_id] = [{"date": x.market_date.isoformat(), "nav": x.nav, "cash": x.cash} for x in by_snapshots[account_id]]

    baseline_orders = by_orders[BASELINE]
    order_by_opportunity = {row.opportunity_id: row for row in reversed(baseline_orders) if row.side == "BUY"}
    entry_decisions = {row.opportunity_id: row for row in reversed(decisions) if row.account_id == BASELINE and row.action == "ENTER"}
    nav = (base["accounts"].get(BASELINE) or {}).get("nav")
    base["positions"] = [_position_row(row, order_by_opportunity.get(row.opportunity_id), entry_decisions.get(row.opportunity_id), nav)
                         for row in by_positions[BASELINE] if row.status == "OPEN"]

    for order in baseline_orders:
        payload = _json(order.payload); candidate = payload.get("candidate") or {}
        row = {"symbol": order.symbol, "opportunity_id": order.opportunity_id,
               "signal_date": candidate.get("signal_date"), "requested_session": order.requested_session.isoformat(),
               "side": order.side, "quantity": order.quantity, "requested_capital": order.requested_capital,
               "status": order.status, "display_status": lifecycle_display(order_status=order.status, side=order.side),
               "next_action": "Next executable open" if order.status == "PENDING" else humanize_reason(order.status)}
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
    baseline_links = [x for x in links if x.account_id == BASELINE]
    completed = len(by_trades[BASELINE]); signal_dates = len({x.signal_date for x in baseline_links})
    first_signal = min((x.signal_date for x in baseline_links), default=None)
    calendar_days = max(0, (dt.date.today() - first_signal).days + 1) if first_signal else 0
    strategies = len({_json(x.payload).get("strategy") for x in by_trades[BASELINE] if _json(x.payload).get("strategy")})
    capacity = sum(x.account_id == BASELINE and x.reason_code == "NO_CAPACITY" for x in decisions)
    overlap = min([len(by_trades[x]) for x in (BASELINE, *SHADOWS)], default=0)
    base["review_gate"] = review_gate_progress({"completed_trades": completed, "signal_dates": signal_dates,
        "calendar_days": calendar_days, "strategies": strategies, "capacity_decisions": capacity, "challenger_overlaps": overlap})
    base["role_linkage_status"] = "ACTIVE" if baseline_links else "NOT YET RUN"
    base["decision_funnel"] = {"qualified": int(health_payload.get("qualified_opportunities") or 0),
        "considered": len(latest_by_opportunity),
        "entered": len({x.opportunity_id for x in today if x.action == "ENTER"}),
        "queued": len({x.opportunity_id for x in today if x.action == "QUEUE"}),
        "rejected": len({x.opportunity_id for x in today if x.action == "REJECT"}),
        "expired": len({x.opportunity_id for x in today if x.action == "EXPIRE"}),
        "market_date": latest_date.isoformat() if latest_date else None}
    base["recent_trades"] = [{"symbol": x.symbol, "strategy": _json(x.payload).get("strategy"),
        "entry": x.entry_date.isoformat(), "exit": x.exit_date.isoformat(), "holding_period": x.holding_sessions,
        "exit_reason": "H10 time exit", "gross_pnl": _json(x.payload).get("gross_pnl"), "costs": sum((_json(x.payload).get(k) or 0) for k in ("entry_fee", "exit_fee", "entry_slippage", "exit_slippage")),
        "net_pnl": x.net_pnl, "mfe_pct": _json(x.payload).get("mfe_pct"), "mae_pct": _json(x.payload).get("mae_pct")} for x in by_trades[BASELINE][:20]]
    return base

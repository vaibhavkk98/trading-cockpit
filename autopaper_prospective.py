"""Prospective-only, automated paper research baseline and isolated shadows."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from typing import Any, Mapping

import numpy as np
import pandas as pd
from sqlalchemy.exc import IntegrityError

import database
from autopaper_v1 import Configuration, ExecutionSimulator, digest
from autopaper_observability import (
    TELEMETRY_CONFIG, TELEMETRY_CONFIG_HASH, TELEMETRY_HASH, TELEMETRY_VERSION,
    bar_context, capture_sector_metadata, persist_risk_snapshot, record_execution_attempt,
)
from provider_symbols import yahoo_nse_symbol

VERSION = "AUTOPAPER_PROSPECTIVE_BASELINE_V1"
ACTIVATION_TIMESTAMP = dt.datetime(2026, 9, 13, 12, 21, 55, tzinfo=dt.timezone.utc)
ACTIVATION_MARKET_DATE = dt.date(2026, 9, 14)
CODE_IDENTITY = "autopaper_prospective.py:v1"
BASELINE_ACCOUNTS = {
    "BASELINE_C3": {"volatility_sizing": True, "date_cap": None, "authority": "RESEARCH_BASELINE"},
    "SHADOW_C0": {"volatility_sizing": False, "date_cap": None, "authority": "SHADOW_ONLY"},
    "SHADOW_D1": {"volatility_sizing": True, "date_cap": .20, "authority": "SHADOW_ONLY"},
}
CONFIG = {
    "capital": 1_000_000.0, "max_positions": 10, "max_stock_weight": .10,
    "max_strategy_weight": .60, "reserve": .10, "minimum_traded_value": 20_000_000.0,
    "participation_limit": .01, "queue_expiry_sessions": 2, "hold_sessions": 10,
    "slippage": .0005, "brokerage_rate": .0015, "brokerage_cap": 20.0,
    "sell_stt": .001, "priority": "P0_FRESHNESS", "replacement": False,
    "stop": None, "target": None, "paper_only": True, "accounts": BASELINE_ACCOUNTS,
    "activation_timestamp": ACTIVATION_TIMESTAMP.isoformat(),
    "activation_market_date": ACTIVATION_MARKET_DATE.isoformat(),
}
CONFIG_HASH = digest(CONFIG)
METHODOLOGY_HASH = digest({"version": VERSION, "config_hash": CONFIG_HASH, "code_identity": CODE_IDENTITY})
CATASTROPHE_VERSION = "AUTOPAPER_SHADOW_CATASTROPHE_V1"
CATASTROPHE_ACTIVATION_TIMESTAMP = dt.datetime(2026, 9, 13, 17, 0, tzinfo=dt.timezone.utc)
CATASTROPHE_CONFIG = {
    "baseline_methodology_hash": METHODOLOGY_HASH, "capital": 1_000_000.0,
    "volatility_sizing": True, "date_cap": None, "hold_sessions": 10,
    "exit": "WIDER_OF_3_5_ENTRY_ATR_OR_12_PERCENT", "execution": "CAUSAL_DAILY_BAR",
    "ordinary_stop": None, "target": None, "replacement": False, "authority": "SHADOW_ONLY",
    "activation_timestamp": CATASTROPHE_ACTIVATION_TIMESTAMP.isoformat(),
}
CATASTROPHE_CONFIG_HASH = digest(CATASTROPHE_CONFIG)
CATASTROPHE_METHODOLOGY_HASH = digest({"version": CATASTROPHE_VERSION,
    "config_hash": CATASTROPHE_CONFIG_HASH, "code_identity": "autopaper_prospective.py:catastrophe-v1"})
ACCOUNTS = {**BASELINE_ACCOUNTS, "SHADOW_CATASTROPHE": {
    "volatility_sizing": True, "date_cap": None, "authority": "SHADOW_ONLY", "catastrophe": True}}
ROLLING_ACCOUNT_ID = "SHADOW_ROLLING"
ROLLING_VERSION = "AUTOPAPER_SHADOW_ROLLING_V1"
ROLLING_CONFIG = {
    "baseline_methodology_hash": METHODOLOGY_HASH, "capital": 1_000_000.0,
    "volatility_sizing": True, "max_positions_hard": 10, "target_occupancy": 9,
    "admissions_below_5": 2, "admissions_from_5_to_8": 1, "admissions_at_9": 0,
    "queue_expiry_sessions": CONFIG["queue_expiry_sessions"], "hold_sessions": 10,
    "priority": "P0_FRESHNESS", "replacement": False, "stop": None, "target": None,
    "authority": "SHADOW_ONLY", "paper_only": True,
}
ROLLING_CONFIG_HASH = digest(ROLLING_CONFIG)
ROLLING_METHODOLOGY_HASH = digest({"version": ROLLING_VERSION,
    "config_hash": ROLLING_CONFIG_HASH, "code_identity": "autopaper_prospective.py:rolling-v1"})
ACCOUNT_CONFIGS = {**ACCOUNTS, ROLLING_ACCOUNT_ID: {
    "volatility_sizing": True, "date_cap": None, "authority": "SHADOW_ONLY", "rolling": True}}
FUTURE_REVIEW_GATE = {
    "minimum_completed_baseline_trades": 100,
    "minimum_unique_signal_dates": 40,
    "minimum_calendar_days_since_first_signal": 90,
    "minimum_represented_strategies": 3,
    "minimum_capacity_constrained_decisions": 30,
    "minimum_completed_challenger_overlap": 80,
    "review_only": True,
    "automatic_promotion": False,
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _utc(value):
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _bar(histories: Mapping[str, pd.DataFrame], symbol: str, market_date: dt.date):
    frame = histories.get(yahoo_nse_symbol(symbol))
    if frame is None:
        frame = histories.get(symbol)
    if frame is None or frame.empty:
        return None
    data = frame.copy(); data.columns = [str(x).lower().replace("adjusted_", "") for x in data.columns]
    if not {"open", "high", "low", "close", "volume"}.issubset(data.columns):
        return None
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    rows = data.loc[data.index == pd.Timestamp(market_date)]
    if rows.empty:
        return None
    row = rows.iloc[-1]
    values = tuple(float(row[x]) for x in ("open", "high", "low", "close", "volume"))
    return (*values, values[3] * values[4])


def _candidate(decision, market_date):
    signal_date = str(decision.get("signal_date") or decision.get("data_as_of") or "")[:10]
    entry = decision.get("entry_price")
    atr = decision.get("atr_20")
    volume = decision.get("current_volume")
    try:
        entry, atr, volume = float(entry), float(atr), float(volume)
    except (TypeError, ValueError):
        entry = atr = volume = float("nan")
    missing = []
    if signal_date != market_date.isoformat(): missing.append("SIGNAL_DATE")
    if not math.isfinite(entry) or entry <= 0: missing.append("REFERENCE_PRICE")
    if not math.isfinite(atr) or atr <= 0: missing.append("ATR")
    if not math.isfinite(volume) or volume <= 0: missing.append("VOLUME")
    opportunity_id = str(decision.get("opportunity_id") or "").strip()
    if not opportunity_id: missing.append("OPPORTUNITY_ID")
    candidate = {
        "opportunity_id": opportunity_id, "symbol": str(decision.get("symbol") or "").upper(),
        "signal_date": signal_date, "strategy": str(decision.get("strategy") or "NOT_AVAILABLE"),
        "reference_price": entry, "atr_pct": atr / entry * 100 if entry > 0 else float("nan"),
        "traded_value": entry * volume if entry > 0 and volume > 0 else float("nan"),
        "missing": missing,
        "advisory_annotations": {key: decision.get(key) for key in ("path_risk", "role_evidence", "historical_analogs", "pb_asymmetry") if key in decision},
    }
    if decision.get("prospective_origin"):
        candidate["prospective_origin"] = str(decision["prospective_origin"])
    if decision.get("intended_execution_date"):
        candidate["intended_execution_date"] = str(decision["intended_execution_date"])[:10]
    return candidate


def _safe(value):
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(value): return "NOT_AVAILABLE"
    return database._json_safe(value)


def _json(value):
    return json.dumps(_safe(value), sort_keys=True, default=str, allow_nan=False)


def _journal(session, account, market_date, timestamp, action, opportunity_id, reason, **details):
    payload = {"methodology_hash": account.methodology_hash, "portfolio_snapshot_id": f"{account.account_id}:{market_date}", **details}
    identity = _hash([account.account_id, market_date.isoformat(), opportunity_id, action, reason])
    if session.get(database.AutoPaperDecision, identity):
        return
    session.add(database.AutoPaperDecision(
        decision_id=identity, account_id=account.account_id, opportunity_id=opportunity_id,
        decision_timestamp=timestamp, market_date=market_date, action=action, reason_code=reason,
        payload=_json(payload), payload_hash=_hash(payload)))


def _manifest_and_accounts():
    database._require_database()
    session = database.SessionLocal()
    try:
        existing = session.get(database.AutoPaperManifest, METHODOLOGY_HASH)
        payload = {**CONFIG, "future_review_gate": FUTURE_REVIEW_GATE,
                   "known_limitations": ["date/regime dependence", "no validated prioritization edge",
                    "no replacement", "C3 concentration fragility", "historical holdout unopened"]}
        if existing is None:
            session.add(database.AutoPaperManifest(
                methodology_hash=METHODOLOGY_HASH, version=VERSION,
                activation_timestamp=ACTIVATION_TIMESTAMP, activation_market_date=ACTIVATION_MARKET_DATE,
                config_payload=_json(payload), config_hash=CONFIG_HASH, code_identity=CODE_IDENTITY))
        elif existing.config_hash != CONFIG_HASH:
            raise RuntimeError("AUTOPAPER_IMMUTABLE_MANIFEST_CONFLICT")
        extra_manifests = (
            (CATASTROPHE_METHODOLOGY_HASH, CATASTROPHE_VERSION, CATASTROPHE_CONFIG,
             CATASTROPHE_CONFIG_HASH, "autopaper_prospective.py:catastrophe-v1", CATASTROPHE_ACTIVATION_TIMESTAMP),
            (TELEMETRY_HASH, TELEMETRY_VERSION, TELEMETRY_CONFIG,
             TELEMETRY_CONFIG_HASH, "autopaper_observability.py:v1", CATASTROPHE_ACTIVATION_TIMESTAMP),
        )
        for method_hash, version, config, config_hash, code_identity, activation_timestamp in extra_manifests:
            manifest = session.get(database.AutoPaperManifest, method_hash)
            if manifest is None:
                session.add(database.AutoPaperManifest(methodology_hash=method_hash, version=version,
                    activation_timestamp=activation_timestamp, activation_market_date=ACTIVATION_MARKET_DATE,
                    config_payload=_json(config), config_hash=config_hash, code_identity=code_identity))
            elif manifest.config_hash != config_hash:
                raise RuntimeError("AUTOPAPER_IMMUTABLE_MANIFEST_CONFLICT")
        now = dt.datetime.now(dt.timezone.utc)
        for account_id in ACCOUNTS:
            account_method = CATASTROPHE_METHODOLOGY_HASH if account_id == "SHADOW_CATASTROPHE" else METHODOLOGY_HASH
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None:
                session.add(database.AutoPaperAccount(
                    account_id=account_id, methodology_hash=account_method,
                    initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                    state_version=0, created_at=now, updated_at=now))
            elif account.methodology_hash != account_method:
                raise RuntimeError("AUTOPAPER_ACCOUNT_METHODOLOGY_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def ensure_rolling_account(activation_timestamp, activation_market_date):
    """Create only the isolated rolling manifest/account; never alters baseline state."""
    database._require_database()
    timestamp = _utc(activation_timestamp)
    session = database.SessionLocal()
    try:
        manifest = session.get(database.AutoPaperManifest, ROLLING_METHODOLOGY_HASH)
        if manifest is None:
            session.add(database.AutoPaperManifest(
                methodology_hash=ROLLING_METHODOLOGY_HASH, version=ROLLING_VERSION,
                activation_timestamp=timestamp, activation_market_date=activation_market_date,
                config_payload=_json(ROLLING_CONFIG), config_hash=ROLLING_CONFIG_HASH,
                code_identity="autopaper_prospective.py:rolling-v1"))
        elif manifest.config_hash != ROLLING_CONFIG_HASH:
            raise RuntimeError("AUTOPAPER_ROLLING_MANIFEST_CONFLICT")
        account = session.get(database.AutoPaperAccount, ROLLING_ACCOUNT_ID)
        if account is None:
            session.add(database.AutoPaperAccount(
                account_id=ROLLING_ACCOUNT_ID, methodology_hash=ROLLING_METHODOLOGY_HASH,
                initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                state_version=0, created_at=timestamp, updated_at=timestamp))
        elif account.methodology_hash != ROLLING_METHODOLOGY_HASH:
            raise RuntimeError("AUTOPAPER_ROLLING_ACCOUNT_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _active_positions(session, account_id):
    return session.query(database.AutoPaperPosition).filter_by(account_id=account_id, status="OPEN").all()


def _nav(account, positions):
    return float(account.cash + sum(p.quantity * p.current_mark for p in positions))


def catastrophe_threshold(entry_price: float, entry_atr: float) -> tuple[float, float]:
    """Return threshold and permissible downside; rule is frozen, not optimized."""
    downside = max(.12, 3.5 * entry_atr / entry_price)
    return entry_price * (1 - downside), downside


def rolling_admission_budget(open_positions: int) -> int:
    """Frozen 2/1/0 policy; target occupancy is nine, hard capacity remains ten."""
    count = max(0, int(open_positions))
    if count >= ROLLING_CONFIG["target_occupancy"]:
        return 0
    return 2 if count < 5 else 1


def _submit_catastrophe_order(session, account, position, market_date, now, bar, execution,
                              previous_close=None, telemetry_attempts=None):
    pp = json.loads(position.payload); threshold = float(pp["catastrophe_threshold"])
    if bar is None or len(bar) < 6 or not all(math.isfinite(x) for x in bar[:4]) or bar[2] > threshold:
        return False
    gap = bar[0] <= threshold
    breach_type = "GAP_THROUGH" if gap else "INTRADAY_BREACH"
    executable = execution.executable(bar)
    order_id = _hash([account.account_id, position.position_id, market_date.isoformat(), "CATASTROPHE_SELL"])
    order = session.get(database.AutoPaperOrder, order_id)
    payload = {"position_id": position.position_id, "reason": "CATASTROPHE_EXIT",
        "execution": "ACTUAL_OPEN" if gap else "OBSERVED_INTRADAY_THRESHOLD",
        "trigger_date": market_date.isoformat(), "trigger_threshold": threshold,
        "threshold_basis": pp["catastrophe_formula"], "entry_atr": pp["entry_atr"],
        "entry_price": position.entry_price, "breach_type": breach_type, "gap": gap}
    if order is None:
        order = database.AutoPaperOrder(order_id=order_id, account_id=account.account_id,
            opportunity_id=position.opportunity_id, symbol=position.symbol, side="SELL",
            requested_session=market_date, order_timestamp=now, status="PENDING",
            quantity=position.quantity, requested_capital=None, payload=_json(payload), payload_hash=_hash(payload))
        session.add(order); session.flush()
    observation_id = _hash([account.account_id, position.position_id, "CATASTROPHE"])
    observation = session.get(database.AutoPaperCatastropheObservation, observation_id)
    if observation is None:
        observation_payload = {**payload, "execution_delay": None, "actual_exit_price": None,
            "net_catastrophe_return": None, "h10_counterfactual_return": None,
            "subsequent_mfe_pct": None, "subsequent_mae_pct": None,
            "position_age_at_trigger": position.age}
        observation = database.AutoPaperCatastropheObservation(observation_id=observation_id,
            account_id=account.account_id, opportunity_id=position.opportunity_id,
            position_id=position.position_id, symbol=position.symbol, trigger_date=market_date,
            status="TRIGGERED_PENDING" if not executable else "TRIGGERED",
            sessions_after_trigger=0, payload=_json(observation_payload), payload_hash=_hash(observation_payload))
        session.add(observation)
    if not executable:
        position.planned_exit_state = "CATASTROPHE_PENDING_NEXT_EXECUTABLE_OPEN"
        position.planned_exit_date = market_date
        _journal(session, account, market_date, now, "QUEUE", position.opportunity_id,
            "CATASTROPHE_EXIT_PENDING", order_id=order_id,
            **{key: value for key, value in payload.items() if key != "reason"})
        (telemetry_attempts if telemetry_attempts is not None else []).append(
            (order_id, bar, previous_close, "PENDING", None))
        return True
    # Apply frozen sell slippage without creating a synthetic fill below the
    # completed bar's observed low.
    reference = max(bar[0] if gap else threshold, bar[2] / (1 - CONFIG["slippage"]))
    fill = execution.sell(reference, position.quantity)
    account.cash += fill["proceeds"]
    entry_cost = pp["entry_cost"]; pnl = fill["proceeds"] - entry_cost
    trade_payload = {**pp, **payload, "exit_date": market_date.isoformat(),
        "quantity": position.quantity, "exit_price": fill["price"], "exit_fee": fill["fee"],
        "exit_slippage": fill["slippage_cost"], "gross_pnl": (reference - position.entry_price / (1 + CONFIG["slippage"])) * position.quantity,
        "net_pnl": pnl, "holding_sessions": position.age, "mfe_pct": position.mfe_pct,
        "mae_pct": min(position.mae_pct, (bar[2] / position.entry_price - 1) * 100),
        "exit_reason": "CATASTROPHE_EXIT"}
    session.add(database.AutoPaperTrade(trade_id=_hash([account.account_id, position.position_id, "TRADE"]),
        account_id=account.account_id, opportunity_id=position.opportunity_id, symbol=position.symbol,
        entry_date=position.entry_date, exit_date=market_date, net_pnl=pnl,
        realized_return_pct=(fill["proceeds"] / entry_cost - 1) * 100,
        holding_sessions=position.age, payload=_json(trade_payload), payload_hash=_hash(trade_payload)))
    position.status = "CLOSED"; position.planned_exit_state = "FILLED"
    order.status = "FILLED"; order.fill_timestamp = now; order.fill_price = fill["price"]
    order.fees = fill["fee"]; order.slippage = fill["slippage_cost"]
    op = json.loads(observation.payload); op.update({"execution_delay": 0,
        "actual_exit_price": fill["price"], "net_catastrophe_return": (fill["proceeds"] / entry_cost - 1) * 100})
    observation.status = "FILLED_AWAITING_H10"; observation.payload = _json(op); observation.payload_hash = _hash(op)
    _journal(session, account, market_date, now, "EXIT", position.opportunity_id,
        "CATASTROPHE_EXIT", order_id=order_id, actual_allocated_capital=fill["proceeds"],
        **{key: value for key, value in payload.items() if key != "reason"})
    (telemetry_attempts if telemetry_attempts is not None else []).append(
        (order_id, bar, previous_close, "FILLED", fill))
    return True


def _advance_catastrophe_observations(session, histories, market_date, execution):
    rows = session.query(database.AutoPaperCatastropheObservation).filter_by(
        account_id="SHADOW_CATASTROPHE", status="FILLED_AWAITING_H10").all()
    for row in rows:
        bar = _bar(histories, row.symbol, market_date)
        if not execution.executable(bar) or market_date <= row.trigger_date:
            continue
        payload = json.loads(row.payload); row.sessions_after_trigger += 1
        exit_price = payload.get("actual_exit_price")
        if exit_price:
            gain = (bar[1] / exit_price - 1) * 100; loss = (bar[2] / exit_price - 1) * 100
            payload["subsequent_mfe_pct"] = max(payload.get("subsequent_mfe_pct") or 0., gain)
            payload["subsequent_mae_pct"] = min(payload.get("subsequent_mae_pct") or 0., loss)
        remaining = max(0, 10 - int(payload.get("position_age_at_trigger") or 0))
        if row.sessions_after_trigger >= remaining:
            reference = bar[0]; entry_price = float(payload["entry_price"])
            payload["h10_counterfactual_return"] = (reference * (1 - CONFIG["slippage"]) / entry_price - 1) * 100
            payload["exit_regret_pct"] = payload["h10_counterfactual_return"] - float(payload.get("net_catastrophe_return") or 0)
            row.status = "MATURE"
        row.payload = _json(payload); row.payload_hash = _hash(payload)


def _process_account(account_id, decisions, histories, market_date, timestamp):
    cfg = Configuration()
    execution = ExecutionSimulator(cfg)
    session = database.SessionLocal()
    stats = {"account_id": account_id, "new_entries": 0, "exits": 0, "failed_fills": 0,
             "warnings": [], "idempotent": False}
    telemetry_attempts = []
    try:
        if database.DATABASE_BACKEND == "SQLITE":
            from sqlalchemy import text
            session.execute(text("BEGIN IMMEDIATE"))
        account = session.query(database.AutoPaperAccount).filter_by(account_id=account_id).with_for_update().one()
        if account.last_market_date and account.last_market_date >= market_date:
            stats["idempotent"] = True
            return stats
        account_cfg = ACCOUNT_CONFIGS[account_id]
        now = timestamp
        if account_id == "SHADOW_CATASTROPHE":
            _advance_catastrophe_observations(session, histories, market_date, execution)
        # Advance the persisted queue by one observed EOD session.
        queue = session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, status="ACTIVE").all()
        for item in queue:
            item.age_sessions += 1; item.updated_at = now
        # Resolve orders fixed on prior completed sessions: sells before buys.
        pending = session.query(database.AutoPaperOrder).filter_by(account_id=account_id, status="PENDING").order_by(
            database.AutoPaperOrder.side.desc(), database.AutoPaperOrder.created_at.asc()).all()
        for order in sorted(pending, key=lambda x: (x.side != "SELL", x.created_at)):
            context = bar_context(histories, order.symbol, market_date)
            bar = context["bar"]
            if not execution.executable(bar):
                # Frozen simulator semantics retain time exits until the next
                # executable open. Entry attempts can be regenerated only
                # while their bounded freshness queue remains valid.
                if order.side != "SELL":
                    order.status = "FAILED_DATA"
                stats["failed_fills"] += 1
                _journal(session, account, market_date, now, "QUEUE", order.opportunity_id,
                         "DATA_UNAVAILABLE", order_id=order.order_id)
                telemetry_attempts.append((order.order_id, bar, context["previous_close"],
                    "PENDING" if order.side == "SELL" else "UNFILLED", None))
                continue
            if order.side == "SELL":
                payload = json.loads(order.payload); position = session.get(database.AutoPaperPosition, payload["position_id"])
                if position is None or position.status != "OPEN":
                    order.status = "CANCELLED_DUPLICATE"; continue
                fill = execution.sell(bar[0], position.quantity)
                account.cash += fill["proceeds"]
                entry_cost = json.loads(position.payload)["entry_cost"]
                pnl = fill["proceeds"] - entry_cost
                exit_reason = payload.get("reason", "H10_TIME_EXIT")
                trade_payload = {**json.loads(position.payload), "exit_date": market_date.isoformat(),
                    "quantity": position.quantity, "exit_price": fill["price"],
                    "exit_fee": fill["fee"], "exit_slippage": fill["slippage_cost"],
                    "gross_pnl": (bar[0] - position.entry_price / (1 + CONFIG["slippage"])) * position.quantity,
                    "net_pnl": pnl, "holding_sessions": position.age, "mfe_pct": position.mfe_pct,
                    "mae_pct": position.mae_pct, "exit_reason": exit_reason}
                trade_id = _hash([account_id, position.position_id, "TRADE"])
                session.add(database.AutoPaperTrade(
                    trade_id=trade_id, account_id=account_id, opportunity_id=position.opportunity_id,
                    symbol=position.symbol, entry_date=position.entry_date, exit_date=market_date,
                    net_pnl=pnl, realized_return_pct=(fill["proceeds"] / entry_cost - 1) * 100,
                    holding_sessions=position.age, payload=_json(trade_payload), payload_hash=_hash(trade_payload)))
                position.status = "CLOSED"; position.planned_exit_state = "FILLED"
                order.status = "FILLED"; order.quantity = position.quantity; order.fill_timestamp = now
                order.fill_price = fill["price"]; order.fees = fill["fee"]; order.slippage = fill["slippage_cost"]
                _journal(session, account, market_date, now, "EXIT", position.opportunity_id,
                         exit_reason, order_id=order.order_id, actual_allocated_capital=fill["proceeds"])
                telemetry_attempts.append((order.order_id, bar, context["previous_close"], "FILLED", fill))
                if exit_reason == "CATASTROPHE_EXIT":
                    observation = session.query(database.AutoPaperCatastropheObservation).filter_by(
                        account_id=account_id, position_id=position.position_id).first()
                    if observation:
                        op = json.loads(observation.payload); delay = max(0, (market_date - observation.trigger_date).days)
                        op.update({"execution_delay": delay, "actual_exit_price": fill["price"],
                            "net_catastrophe_return": (fill["proceeds"] / entry_cost - 1) * 100})
                        observation.status = "FILLED_AWAITING_H10"; observation.payload = _json(op)
                        observation.payload_hash = _hash(op)
                stats["exits"] += 1
            else:
                queue_item = session.query(database.AutoPaperQueueItem).filter_by(
                    account_id=account_id, opportunity_id=order.opportunity_id, status="ACTIVE").first()
                if queue_item is None or queue_item.age_sessions > CONFIG["queue_expiry_sessions"]:
                    order.status = "EXPIRED"; continue
                positions = _active_positions(session, account_id)
                if any(p.symbol == order.symbol for p in positions):
                    order.status = "REJECTED"; queue_item.status = "REJECTED"
                    _journal(session, account, market_date, now, "REJECT", order.opportunity_id,
                             "POSITION_ALREADY_EXISTS", order_id=order.order_id)
                    continue
                current_nav = _nav(account, positions)
                available = max(0., account.cash - current_nav * CONFIG["reserve"])
                fill = execution.buy(bar, min(float(order.requested_capital or 0), available))
                if fill is None:
                    order.status = "FAILED_DATA"; stats["failed_fills"] += 1
                    telemetry_attempts.append((order.order_id, bar, context["previous_close"], "UNFILLED", None))
                    continue
                account.cash -= fill["cost"]
                position_id = _hash([account_id, order.opportunity_id, "POSITION"])
                qpayload = json.loads(queue_item.payload)
                ppayload = {"signal_date": qpayload["signal_date"], "strategy": qpayload["strategy"],
                    "entry_cost": fill["cost"], "entry_fee": fill["fee"], "entry_slippage": fill["slippage_cost"],
                    "methodology_hash": account.methodology_hash, "planned_hold_sessions": 10}
                if account_id == "SHADOW_CATASTROPHE":
                    entry_atr = float(qpayload["reference_price"]) * float(qpayload["atr_pct"]) / 100
                    threshold, downside = catastrophe_threshold(fill["price"], entry_atr)
                    ppayload.update({"entry_atr": float(qpayload["reference_price"]) * float(qpayload["atr_pct"]) / 100,
                        "catastrophe_downside_pct": downside * 100,
                        "catastrophe_threshold": threshold,
                        "catastrophe_formula": "WIDER_OF_3_5_ENTRY_ATR_OR_12_PERCENT"})
                session.add(database.AutoPaperPosition(
                    position_id=position_id, account_id=account_id, opportunity_id=order.opportunity_id,
                    symbol=order.symbol, status="OPEN", entry_date=market_date, quantity=fill["quantity"],
                    entry_price=fill["price"], age=0, current_mark=fill["price"], unrealized_pnl=0.,
                    mfe_pct=0., mae_pct=0., planned_exit_date=None, planned_exit_state="HOLD",
                    payload=_json(ppayload), updated_at=now))
                order.status = "FILLED"; order.quantity = fill["quantity"]; order.fill_timestamp = now
                order.fill_price = fill["price"]; order.fees = fill["fee"]; order.slippage = fill["slippage_cost"]
                queue_item.status = "ENTERED"
                link = session.get(database.AutoPaperCounterfactualLink, _hash([account_id, order.opportunity_id]))
                if link: link.entered = True; link.terminal_reason = "ENTERED"
                _journal(session, account, market_date, now, "ENTER", order.opportunity_id, "ENTERED",
                         order_id=order.order_id, sizing_multiplier=json.loads(order.payload)["sizing_multiplier"],
                         requested_capital=order.requested_capital, actual_allocated_capital=fill["cost"],
                         quantity=fill["quantity"], fill_price=fill["price"])
                telemetry_attempts.append((order.order_id, bar, context["previous_close"], "FILLED", fill))
                stats["new_entries"] += 1
        # Entry occurs at today's open, so today's completed bar is holding session 1.
        session.flush()
        # Mark positions with completed-session OHLC and submit H10 exits for next open.
        for position in _active_positions(session, account_id):
            mark_context = bar_context(histories, position.symbol, market_date)
            bar = mark_context["bar"]
            if account_id == "SHADOW_CATASTROPHE" and _submit_catastrophe_order(
                    session, account, position, market_date, now, bar, execution,
                    mark_context["previous_close"], telemetry_attempts):
                if position.status == "CLOSED": stats["exits"] += 1
                else: stats["failed_fills"] += 1
                continue
            if not execution.executable(bar):
                stats["warnings"].append(f"DATA_UNAVAILABLE:{position.symbol}"); continue
            position.age += 1; position.current_mark = bar[3]
            position.unrealized_pnl = position.quantity * bar[3] - json.loads(position.payload)["entry_cost"]
            position.mfe_pct = max(position.mfe_pct, (bar[1] / position.entry_price - 1) * 100)
            position.mae_pct = min(position.mae_pct, (bar[2] / position.entry_price - 1) * 100)
            position.updated_at = now
            if position.age >= CONFIG["hold_sessions"]:
                order_id = _hash([account_id, position.position_id, market_date.isoformat(), "SELL"])
                if session.get(database.AutoPaperOrder, order_id) is None:
                    payload = {"position_id": position.position_id, "reason": "H10_TIME_EXIT",
                               "execution": "NEXT_EXECUTABLE_OPEN"}
                    session.add(database.AutoPaperOrder(
                        order_id=order_id, account_id=account_id, opportunity_id=position.opportunity_id,
                        symbol=position.symbol, side="SELL", requested_session=market_date,
                        order_timestamp=now, status="PENDING", quantity=position.quantity,
                        requested_capital=None, payload=_json(payload), payload_hash=_hash(payload)))
                position.planned_exit_state = "SUBMITTED_NEXT_OPEN"
                position.planned_exit_date = market_date
            _journal(session, account, market_date, now, "HOLD", position.opportunity_id,
                     "H10_TIME_EXIT_SUBMITTED" if position.age >= 10 else "HOLD",
                     position_age=position.age, planned_exit_state=position.planned_exit_state)
        # Ingest exactly today's new qualified stream and create outcome links.
        for raw in decisions:
            candidate = _candidate(raw, market_date); oid = candidate["opportunity_id"]
            if not oid: continue
            link_id = _hash([account_id, oid])
            if session.get(database.AutoPaperCounterfactualLink, link_id) is None:
                session.add(database.AutoPaperCounterfactualLink(
                    link_id=link_id, account_id=account_id, opportunity_id=oid, signal_date=market_date,
                    entered=False, terminal_reason=None, origin=candidate.get("prospective_origin", "PROSPECTIVE"),
                    payload=_json({"outcome_source": "ROLE_D1", "horizons": [5,10,20],
                                   "methodology_hash": METHODOLOGY_HASH})))
            existing = session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, opportunity_id=oid).first()
            if existing is None:
                session.add(database.AutoPaperQueueItem(
                    account_id=account_id, opportunity_id=oid, symbol=candidate["symbol"],
                    signal_date=market_date, age_sessions=0, status="ACTIVE", payload=_json(candidate),
                    created_at=now, updated_at=now))
        session.flush()
        # Expire after pending fills had their final valid opportunity.
        for item in session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, status="ACTIVE").all():
            if item.age_sessions >= CONFIG["queue_expiry_sessions"]:
                item.status = "EXPIRED"; item.updated_at = now
                link = session.get(database.AutoPaperCounterfactualLink, _hash([account_id, item.opportunity_id]))
                if link: link.terminal_reason = "EXPIRED"
                _journal(session, account, market_date, now, "EXPIRE", item.opportunity_id, "EXPIRED")
        # P0 freshness only; advisory fields never enter this decision path.
        active = session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, status="ACTIVE").all()
        active.sort(key=lambda x: (-x.signal_date.toordinal(), x.opportunity_id))
        positions = _active_positions(session, account_id)
        rolling = bool(account_cfg.get("rolling"))
        admission_start_positions = len(positions)
        rolling_budget = rolling_admission_budget(admission_start_positions)
        rolling_used = 0
        rolling_deferred = 0
        virtual_symbols = {p.symbol for p in positions}; virtual_positions = len(positions)
        virtual_cash = account.cash; current_nav = _nav(account, positions)
        strategy_value = {}
        date_commitment = {}
        for p in positions:
            pp = json.loads(p.payload); strategy_value[pp["strategy"]] = strategy_value.get(pp["strategy"], 0.) + p.quantity * p.current_mark
            date_commitment[pp["signal_date"]] = date_commitment.get(pp["signal_date"], 0.) + pp["entry_cost"]
        for item in active:
            candidate = json.loads(item.payload); oid = item.opportunity_id
            reason = None
            if candidate["missing"]: reason = "DATA_UNAVAILABLE"
            elif item.symbol in virtual_symbols: reason = "POSITION_ALREADY_EXISTS"
            elif virtual_positions >= CONFIG["max_positions"]: reason = "NO_CAPACITY"
            elif candidate["traded_value"] < CONFIG["minimum_traded_value"]: reason = "LIQUIDITY_FAIL"
            elif rolling and virtual_positions >= ROLLING_CONFIG["target_occupancy"]:
                reason = "ROLLING_TARGET_OCCUPANCY"
            elif rolling and rolling_used >= rolling_budget:
                reason = "ROLLING_DAILY_ADMISSION_LIMIT"
            multiplier = 1.
            if account_cfg["volatility_sizing"] and not reason:
                multiplier = max(.5, min(1., 4. / candidate["atr_pct"])) if candidate["atr_pct"] > 0 else 0.
            strategy_room = current_nav * CONFIG["max_strategy_weight"] - strategy_value.get(candidate["strategy"], 0.)
            budget = min(current_nav * CONFIG["max_stock_weight"],
                         virtual_cash - current_nav * CONFIG["reserve"], strategy_room,
                         candidate["traded_value"] * CONFIG["participation_limit"]) * multiplier if not reason else 0.
            if account_cfg["date_cap"] is not None and budget > 0:
                room = current_nav * account_cfg["date_cap"] - date_commitment.get(candidate["signal_date"], 0.)
                budget = min(budget, max(0., room))
            if budget <= 0 and not reason:
                reason = "INSUFFICIENT_CASH" if virtual_cash - current_nav * CONFIG["reserve"] <= 0 else "NO_CAPACITY"
            if reason in ("DATA_UNAVAILABLE", "LIQUIDITY_FAIL", "POSITION_ALREADY_EXISTS"):
                item.status = "REJECTED"; item.updated_at = now
                link = session.get(database.AutoPaperCounterfactualLink, _hash([account_id, oid]))
                if link: link.terminal_reason = reason
                _journal(session, account, market_date, now, "REJECT", oid, reason,
                         rank=active.index(item) + 1, sizing_multiplier=multiplier)
                continue
            if budget <= 0:
                if reason in {"ROLLING_DAILY_ADMISSION_LIMIT", "ROLLING_TARGET_OCCUPANCY"}:
                    rolling_deferred += 1
                _journal(session, account, market_date, now, "QUEUE", oid, reason or "LOWER_PRIORITY",
                         rank=active.index(item) + 1, sizing_multiplier=multiplier)
                continue
            order_id = _hash([account_id, oid, market_date.isoformat(), "BUY"])
            payload = {"candidate": candidate, "execution": "NEXT_EXECUTABLE_OPEN",
                       "sizing_multiplier": multiplier, "methodology_hash": account.methodology_hash}
            if candidate.get("intended_execution_date"):
                payload["intended_execution_date"] = candidate["intended_execution_date"]
                payload["prospective_origin"] = candidate.get("prospective_origin")
            if session.get(database.AutoPaperOrder, order_id) is None:
                session.add(database.AutoPaperOrder(
                    order_id=order_id, account_id=account_id, opportunity_id=oid, symbol=item.symbol,
                    side="BUY", requested_session=market_date, order_timestamp=now, status="PENDING",
                    requested_capital=budget, payload=_json(payload), payload_hash=_hash(payload)))
            journal_details = {"rank": active.index(item) + 1, "sizing_multiplier": multiplier,
                "requested_capital": budget, "actual_allocated_capital": 0.}
            if candidate.get("prospective_origin"):
                journal_details.update(prospective_origin=candidate["prospective_origin"],
                    intended_execution_date=candidate.get("intended_execution_date"))
            _journal(session, account, market_date, now, "QUEUE", oid, "ORDER_SUBMITTED_T1", **journal_details)
            virtual_cash -= budget; virtual_positions += 1; virtual_symbols.add(item.symbol)
            if rolling: rolling_used += 1
            strategy_value[candidate["strategy"]] = strategy_value.get(candidate["strategy"], 0.) + budget
            date_commitment[candidate["signal_date"]] = date_commitment.get(candidate["signal_date"], 0.) + budget
        positions = _active_positions(session, account_id); nav = _nav(account, positions)
        if len(positions) > CONFIG["max_positions"]:
            raise RuntimeError("AUTOPAPER_HARD_CAPACITY_BREACH")
        if rolling and virtual_positions > ROLLING_CONFIG["target_occupancy"]:
            raise RuntimeError("AUTOPAPER_ROLLING_TARGET_BREACH")
        if account.cash < -1e-7 or abs(nav - (account.cash + sum(p.quantity * p.current_mark for p in positions))) > .01:
            raise RuntimeError("AUTOPAPER_PORTFOLIO_RECONCILIATION_FAILED")
        snapshot_payload = {"methodology_hash": account.methodology_hash, "account": account_id,
            "cash": account.cash, "nav": nav, "open_positions": len(positions),
            "pending_orders": session.query(database.AutoPaperOrder).filter_by(account_id=account_id, status="PENDING").count()}
        if rolling:
            snapshot_payload.update({"target_occupancy": ROLLING_CONFIG["target_occupancy"],
                "hard_capacity": ROLLING_CONFIG["max_positions_hard"],
                "admission_start_positions": admission_start_positions,
                "admission_budget": rolling_budget, "admission_used": rolling_used,
                "rolling_deferred": rolling_deferred})
        fingerprint = _hash(snapshot_payload)
        if not session.query(database.AutoPaperPortfolioSnapshot).filter_by(
                account_id=account_id, market_date=market_date, state_fingerprint=fingerprint).first():
            session.add(database.AutoPaperPortfolioSnapshot(
                account_id=account_id, market_date=market_date, cash=account.cash, nav=nav,
                open_positions=len(positions), state_fingerprint=fingerprint, payload=_json(snapshot_payload)))
        account.last_market_date = market_date; account.state_version += 1; account.updated_at = now
        if rolling:
            rolling_positions = _active_positions(session, account_id)
            active_queue = session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, status="ACTIVE").all()
            ages = [int(p.age) for p in rolling_positions]
            age_counts = {str(age): ages.count(age) for age in sorted(set(ages))}
            signal_value = {}
            signal_positions = {}
            for position in rolling_positions:
                signal = json.loads(position.payload).get("signal_date", "NOT_AVAILABLE")
                signal_value[signal] = signal_value.get(signal, 0.) + position.quantity * position.current_mark
                signal_positions[signal] = signal_positions.get(signal, 0) + 1
            total_value = sum(signal_value.values())
            shares = [value / total_value for value in signal_value.values()] if total_value else []
            position_shares = [value / len(rolling_positions) for value in signal_positions.values()] if rolling_positions else []
            prior_deferred = {x.opportunity_id for x in session.query(database.AutoPaperDecision).filter_by(
                account_id=account_id).all() if x.reason_code in {"ROLLING_DAILY_ADMISSION_LIMIT", "ROLLING_TARGET_OCCUPANCY"}}
            entered_ids = {x.opportunity_id for x in session.query(database.AutoPaperCounterfactualLink).filter_by(
                account_id=account_id, entered=True).all()}
            expired_ids = {x.opportunity_id for x in session.query(database.AutoPaperCounterfactualLink).filter_by(
                account_id=account_id, terminal_reason="EXPIRED").all()}
            telemetry = {"version": ROLLING_VERSION, "methodology_hash": ROLLING_METHODOLOGY_HASH,
                "market_date": market_date.isoformat(), "decision_authority": False,
                "open_positions": len(rolling_positions), "target_occupancy": 9, "hard_capacity": 10,
                "admission_start_positions": admission_start_positions, "admission_budget": rolling_budget,
                "admission_used": rolling_used, "valid_queued": len(active_queue),
                "rolling_deferred_today": rolling_deferred,
                "expiring_soon": sum(x.age_sessions >= CONFIG["queue_expiry_sessions"] - 1 for x in active_queue),
                "age_counts": age_counts, "mean_age": float(np.mean(ages)) if ages else None,
                "median_age": float(np.median(ages)) if ages else None,
                "largest_same_age_share": max(age_counts.values()) / len(ages) if ages else None,
                "unique_active_signal_dates": len(signal_value),
                "largest_signal_date_capital_share": max(shares) if shares else None,
                "signal_date_hhi": sum(x * x for x in shares) if shares else None,
                "largest_signal_date_position_share": max(position_shares) if position_shares else None,
                "signal_date_position_hhi": sum(x * x for x in position_shares) if position_shares else None,
                "deferred_then_entered": len(prior_deferred & entered_ids),
                "deferred_then_expired": len(prior_deferred & expired_ids)}
            telemetry_id = _hash([ROLLING_VERSION, account_id, market_date.isoformat()])
            if session.get(database.AutoPaperRollingTelemetry, telemetry_id) is None:
                session.add(database.AutoPaperRollingTelemetry(
                    telemetry_id=telemetry_id, account_id=account_id, market_date=market_date,
                    payload=_json(telemetry), payload_hash=_hash(telemetry)))
        session.commit()
        if telemetry_attempts:
            telemetry_session = database.SessionLocal()
            try:
                telemetry_account = telemetry_session.get(database.AutoPaperAccount, account_id)
                for order_id, observed_bar, previous_close, attempt_status, fill in telemetry_attempts:
                    telemetry_order = telemetry_session.get(database.AutoPaperOrder, order_id)
                    if telemetry_order:
                        record_execution_attempt(telemetry_session, account=telemetry_account,
                            order=telemetry_order, market_date=market_date, bar=observed_bar,
                            previous_close=previous_close, filled=attempt_status == "FILLED",
                            fill=fill, status=attempt_status)
                telemetry_session.commit()
            except Exception as exc:
                telemetry_session.rollback(); stats["warnings"].append(f"EXECUTION_TELEMETRY:{type(exc).__name__}")
            finally:
                telemetry_session.close()
        stats.update(cash=account.cash, nav=nav, open_positions=len(positions),
                     queue_size=session.query(database.AutoPaperQueueItem).filter_by(account_id=account_id, status="ACTIVE").count())
        if rolling:
            stats.update(admission_budget=rolling_budget, admission_used=rolling_used,
                         rolling_deferred=rolling_deferred, target_occupancy=9, hard_capacity=10)
        return stats
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _process_rolling_if_activated(decisions, histories, market_date, timestamp, killed):
    """Advance the isolated challenger, activating only on an allowed future cohort."""
    session = database.SessionLocal()
    try:
        activation = session.query(database.AutoPaperRollingActivation).order_by(
            database.AutoPaperRollingActivation.created_at.desc()).first()
        if activation is None:
            return None
        if activation.status == "PENDING_NEXT_COHORT":
            if activation.after_market_date is not None and market_date <= activation.after_market_date:
                return {"account_id": ROLLING_ACCOUNT_ID, "status": "PENDING_NEXT_COHORT"}
            activation.status = "ACTIVE"
            activation.activation_signal_date = market_date
            activation.provenance = "ROLLING_PROSPECTIVE"
            payload = json.loads(activation.payload)
            payload.update({"status": "ACTIVE", "activation_signal_date": market_date.isoformat(),
                            "provenance": "ROLLING_PROSPECTIVE"})
            activation.payload = _json(payload); activation.payload_hash = _hash(payload)
            session.commit()
        activation_date = activation.activation_signal_date
        provenance = activation.provenance
        activation_timestamp = activation.activation_timestamp
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
    if activation_date is None or market_date < activation_date:
        return {"account_id": ROLLING_ACCOUNT_ID, "status": "PRE_ACTIVATION"}
    ensure_rolling_account(activation_timestamp, activation_date)
    rolling_decisions = [{**row, "prospective_origin": provenance} for row in ([] if killed else decisions)]
    result = _process_account(ROLLING_ACCOUNT_ID, rolling_decisions, histories, market_date, timestamp)
    try:
        persist_risk_snapshot(ROLLING_ACCOUNT_ID, market_date, histories, decisions)
    except Exception as exc:
        result.setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")
    return result


def run_prospective_autopaper(decisions, histories, market_date, run_timestamp, run_id,
                              source="AUTOMATED_EOD"):
    """Advance baseline and shadows once. Never routes to a broker or manual portfolio."""
    market_date = market_date if isinstance(market_date, dt.date) else dt.date.fromisoformat(str(market_date)[:10])
    timestamp = _utc(run_timestamp)
    enabled = os.environ.get("AUTOPAPER_PROSPECTIVE_ENABLED", "1").strip().lower() in ("1", "true", "yes")
    killed = os.environ.get("AUTOPAPER_KILL_SWITCH", "0").strip().lower() in ("1", "true", "yes")
    if source != "AUTOMATED_EOD":
        return {"status": "SKIPPED_NON_AUTOMATED_SOURCE", "active": False, "paper_only": True}
    if not enabled:
        return {"status": "DISABLED", "active": False, "paper_only": True}
    if market_date < ACTIVATION_MARKET_DATE or timestamp < ACTIVATION_TIMESTAMP:
        return {"status": "PRE_ACTIVATION", "active": False, "paper_only": True, "historical_backfill": False}
    _manifest_and_accounts()
    results, errors, observability_warnings = {}, [], []
    metadata_session = database.SessionLocal()
    try:
        capture_sector_metadata(metadata_session, decisions, timestamp)
        metadata_session.commit()
    except Exception as exc:
        metadata_session.rollback(); observability_warnings.append(f"SECTOR_TELEMETRY:{type(exc).__name__}")
    finally:
        metadata_session.close()
    for account_id in ACCOUNTS:
        try:
            # Kill switch blocks new opportunities but still permits deterministic H10 exits.
            results[account_id] = _process_account(
                account_id, [] if killed else decisions, histories, market_date, timestamp)
        except Exception as exc:
            errors.append(f"{account_id}:{type(exc).__name__}")
            results[account_id] = {"account_id": account_id, "status": "FAILED", "error": type(exc).__name__}
        try:
            if results[account_id].get("status") != "FAILED":
                persist_risk_snapshot(account_id, market_date, histories, decisions)
        except Exception as exc:
            observability_warnings.append(f"RISK_TELEMETRY:{account_id}:{type(exc).__name__}")
    try:
        rolling_result = _process_rolling_if_activated(decisions, histories, market_date, timestamp, killed)
        if rolling_result is not None:
            results[ROLLING_ACCOUNT_ID] = rolling_result
    except Exception as exc:
        # Challenger isolation: a rolling failure is visible but cannot degrade
        # baseline or the already-frozen shadows.
        results[ROLLING_ACCOUNT_ID] = {"account_id": ROLLING_ACCOUNT_ID,
            "status": "FAILED", "error": type(exc).__name__}
        observability_warnings.append(f"{ROLLING_ACCOUNT_ID}:{type(exc).__name__}")
    health = {"run_id": f"AUTOPAPER-{run_id}", "run_timestamp": timestamp.isoformat(),
        "market_date": market_date.isoformat(), "qualified_opportunities": len(decisions),
        "kill_switch": killed, "paper_only": True, "accounts": results,
        "shadow_run_status": "HEALTHY" if all(k in results and results[k].get("status") != "FAILED" for k in ("SHADOW_C0", "SHADOW_D1", "SHADOW_CATASTROPHE")) else "DEGRADED",
        "rolling_shadow_status": (results.get(ROLLING_ACCOUNT_ID) or {}).get("status", "NOT_ACTIVATED"),
        "errors": errors, "warnings": [w for value in results.values() for w in value.get("warnings", [])] + observability_warnings,
        "status": "HEALTHY" if not errors else "DEGRADED"}
    session = database.SessionLocal()
    try:
        health_id = health["run_id"]; payload = _json(health); payload_hash = _hash(health)
        row = session.get(database.AutoPaperHealth, health_id)
        if row is None:
            session.add(database.AutoPaperHealth(run_id=health_id, market_date=market_date,
                run_timestamp=timestamp, status=health["status"], payload=payload, payload_hash=payload_hash))
        elif row.payload_hash != payload_hash and not all(v.get("idempotent") for v in results.values()):
            raise RuntimeError("AUTOPAPER_HEALTH_RETRY_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
    baseline_failed = results.get("BASELINE_C3", {}).get("status") == "FAILED"
    return {**health, "active": not killed and not baseline_failed, "methodology_hash": METHODOLOGY_HASH,
            "config_hash": CONFIG_HASH, "activation_timestamp": ACTIVATION_TIMESTAMP.isoformat(),
            "telemetry_version": TELEMETRY_VERSION, "catastrophe_methodology_hash": CATASTROPHE_METHODOLOGY_HASH,
            "catastrophe_config_hash": CATASTROPHE_CONFIG_HASH,
            "historical_holdout_opened": False, "real_money_authority": False}


def prospective_evidence():
    """Read-only accumulated evidence; immature observations are never losses."""
    if not database.init_db(): return {"status": "NOT_AVAILABLE"}
    session = database.SessionLocal(); result = {"methodology_hash": METHODOLOGY_HASH, "accounts": {}}
    try:
        links_all = session.query(database.AutoPaperCounterfactualLink).all()
        opportunity_ids = sorted({row.opportunity_id for row in links_all})
        observations = session.query(database.RoleOutcomeObservation).filter(
            database.RoleOutcomeObservation.opportunity_id.in_(opportunity_ids)).all() if opportunity_ids else []
        observation_ids = [row.id for row in observations]
        horizon_rows = session.query(database.RoleOutcomeHorizon).filter(
            database.RoleOutcomeHorizon.observation_id.in_(observation_ids),
            database.RoleOutcomeHorizon.horizon_sessions == 10).all() if observation_ids else []
        observation_by_id = {row.id: row for row in observations}
        outcomes_10d = {}
        for row in horizon_rows:
            observation = observation_by_id[row.observation_id]
            outcomes_10d[observation.opportunity_id] = json.loads(row.payload)

        def outcome_summary(ids):
            rows = [outcomes_10d[oid] for oid in ids if oid in outcomes_10d]
            returns = [float(row["close_return_pct"]) for row in rows]
            return {"mature_10d_n": len(rows),
                    "median_close_return_pct": float(np.median(returns)) if returns else None,
                    "plus_5_before_minus_3_rate": float(np.mean([
                        row.get("plus_5_before_minus_3") == "TARGET_FIRST" for row in rows])) if rows else None,
                    "median_mfe_pct": float(np.median([row["mfe_pct"] for row in rows])) if rows else None,
                    "median_mae_pct": float(np.median([row["mae_pct"] for row in rows])) if rows else None}

        for account_id in (*ACCOUNTS, ROLLING_ACCOUNT_ID):
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None: continue
            trades = session.query(database.AutoPaperTrade).filter_by(account_id=account_id).all()
            snapshots = session.query(database.AutoPaperPortfolioSnapshot).filter_by(account_id=account_id).order_by(
                database.AutoPaperPortfolioSnapshot.market_date.asc()).all()
            links = session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=account_id).all()
            decisions = session.query(database.AutoPaperDecision).filter_by(account_id=account_id).all()
            nav = pd.Series([x.nav for x in snapshots], dtype=float)
            returns = nav.pct_change().dropna(); std = returns.std(ddof=1)
            trade_returns = pd.Series([x.realized_return_pct for x in trades], dtype=float)
            trade_payloads = [json.loads(x.payload) for x in trades]
            costs = sum(json.loads(x.payload).get("entry_fee", 0) + json.loads(x.payload).get("exit_fee", 0) +
                        json.loads(x.payload).get("entry_slippage", 0) + json.loads(x.payload).get("exit_slippage", 0) for x in trades)
            turnover = sum(row.get("entry_cost", 0) + row.get("exit_price", 0) * row.get("quantity", 0)
                           for row in trade_payloads)
            date_profit = {}
            for trade in trades:
                payload = json.loads(trade.payload); date = payload.get("signal_date", "NOT_AVAILABLE")
                date_profit[date] = date_profit.get(date, 0.) + trade.net_pnl
            positive = sorted((x for x in date_profit.values() if x > 0), reverse=True); gross_positive = sum(positive)
            downside = returns[returns < 0]; downside_std = downside.std(ddof=1)
            entered_ids = {row.opportunity_id for row in links if row.entered}
            nonentered_ids = {row.opportunity_id for row in links if not row.entered}
            strategy_counts = {}
            for payload in trade_payloads:
                strategy = payload.get("strategy", "NOT_AVAILABLE")
                strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
            capture = [trade.realized_return_pct / payload["mfe_pct"] for trade, payload in zip(trades, trade_payloads)
                       if payload.get("mfe_pct") is not None and payload["mfe_pct"] > 0]
            result["accounts"][account_id] = {
                "days_observed": len(snapshots), "opportunities": len(links), "entered": sum(x.entered for x in links),
                "not_entered": len(nonentered_ids),
                "queued_decisions": sum(x.action == "QUEUE" for x in decisions),
                "rejected_decisions": sum(x.action in ("REJECT", "EXPIRE") for x in decisions),
                "completed_trades": len(trades), "open_positions": len(_active_positions(session, account_id)),
                "net_return_pct": float((nav.iloc[-1] / account.initial_capital - 1) * 100) if len(nav) else 0.,
                "max_drawdown_pct": float((nav / nav.cummax() - 1).min() * 100) if len(nav) else 0.,
                "sharpe": float(returns.mean()/std*math.sqrt(252)) if len(returns) > 1 and std > 0 else None,
                "sortino": float(returns.mean()/downside_std*math.sqrt(252)) if len(downside) > 1 and downside_std > 0 else None,
                "turnover_pct_of_initial_capital": float(turnover / account.initial_capital * 100),
                "costs": float(costs),
                "average_exposure_pct": float(np.mean([(x.nav - x.cash) / x.nav * 100 for x in snapshots if x.nav > 0])) if snapshots else 0.,
                "strategy_distribution": strategy_counts,
                "mean_trade_return_pct": float(trade_returns.mean()) if len(trade_returns) else None,
                "median_trade_return_pct": float(trade_returns.median()) if len(trade_returns) else None,
                "median_trade_mfe_pct": float(np.median([x.get("mfe_pct") for x in trade_payloads])) if trades else None,
                "median_trade_mae_pct": float(np.median([x.get("mae_pct") for x in trade_payloads])) if trades else None,
                "median_h10_capture_ratio": float(np.median(capture)) if capture else None,
                "plus_5_before_minus_3": outcome_summary(entered_ids)["plus_5_before_minus_3_rate"],
                "entered_outcomes": outcome_summary(entered_ids),
                "nonentered_outcomes": outcome_summary(nonentered_ids),
                "top_date_positive_share": positive[0]/gross_positive if gross_positive else None,
                "top5_date_positive_share": sum(positive[:5])/gross_positive if gross_positive else None,
                "capacity_constrained_decisions": sum(x.reason_code == "NO_CAPACITY" for x in decisions),
                "immature_not_counted_as_losses": True}
        result["future_review_gate"] = FUTURE_REVIEW_GATE
        baseline = result["accounts"].get("BASELINE_C3")
        result["challenger_comparison"] = {
            account_id: {"baseline": "BASELINE_C3", "challenger": account_id,
                         "completed_overlap": min(baseline["completed_trades"], values["completed_trades"]) if baseline else 0,
                         "net_return_difference_pct": values["net_return_pct"] - baseline["net_return_pct"] if baseline else None,
                         "max_drawdown_difference_pct": values["max_drawdown_pct"] - baseline["max_drawdown_pct"] if baseline else None}
            for account_id, values in result["accounts"].items() if account_id != "BASELINE_C3"}
        return result
    finally:
        session.close()

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
import portfolio_risk_engine as portfolio_risk
import opportunity_selection_engine as opportunity_selection
import dynamic_exposure_engine as dynamic_exposure

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
EDGE_VERSION = "AUTOPAPER_EDGE_CAPTURE_V1A"
THESIS_RULE_VERSION = "EDGE_THESIS_FAILURE_V1"
EDGE_ACCOUNT_CONFIGS = {
    "SHADOW_EDGE_H20": {"policy": "E1_H20", "hold_sessions": 20, "catastrophe": False, "thesis": False},
    "SHADOW_EDGE_H20_CATASTROPHE": {"policy": "E2_H20_CATASTROPHE", "hold_sessions": 20, "catastrophe": True, "thesis": False},
    "SHADOW_EDGE_H20_THESIS": {"policy": "E3_H20_THESIS", "hold_sessions": 20, "catastrophe": False, "thesis": True},
}
EDGE_CONFIGS = {account_id: {
    "version": EDGE_VERSION, "policy": cfg["policy"],
    "upstream_methodology_hash": ROLLING_METHODOLOGY_HASH,
    "capital": 1_000_000.0, "volatility_sizing": True,
    "max_positions_hard": 10, "target_occupancy": 9,
    "admission_rule": "2_BELOW_5__1_FROM_5_TO_8__0_AT_9",
    "priority": "P0_FRESHNESS", "queue_expiry_sessions": CONFIG["queue_expiry_sessions"],
    "hold_sessions": cfg["hold_sessions"], "catastrophe": cfg["catastrophe"],
    "thesis_failure": cfg["thesis"], "thesis_rule_version": THESIS_RULE_VERSION if cfg["thesis"] else None,
    "replacement": False, "ordinary_stop": None, "target": None,
    "authority": "SHADOW_ONLY", "paper_only": True,
} for account_id, cfg in EDGE_ACCOUNT_CONFIGS.items()}
EDGE_CONFIG_HASHES = {key: digest(value) for key, value in EDGE_CONFIGS.items()}
EDGE_METHODOLOGY_HASHES = {key: digest({"version": EDGE_VERSION, "account": key,
    "config_hash": EDGE_CONFIG_HASHES[key], "code_identity": "autopaper_prospective.py:edge-v1a"})
    for key in EDGE_ACCOUNT_CONFIGS}
ACCOUNT_CONFIGS.update({account_id: {"volatility_sizing": True, "date_cap": None,
    "authority": "SHADOW_ONLY", "rolling": True, **cfg} for account_id, cfg in EDGE_ACCOUNT_CONFIGS.items()})
EDGE_REVIEW_GATE = {"calendar_days": 90, "completed_matched_groups": 60,
    "unique_signal_dates": 30, "represented_strategies": 3, "capacity_divergence_events": 20,
    "automatic_promotion": False}
PORTFOLIO_RISK_VERSION = portfolio_risk.VERSION
PORTFOLIO_RISK_ACCOUNT_CONFIGS = {
    "SHADOW_RISK_BUDGET": {"policy": "B1", "redundancy": False},
    "SHADOW_RISK_DIVERSIFIED": {"policy": "B2", "redundancy": True},
}
PORTFOLIO_RISK_CONFIGS = {account_id: {
    "version": PORTFOLIO_RISK_VERSION, "policy": cfg["policy"],
    "control_methodology_hash": ROLLING_METHODOLOGY_HASH,
    "capital": 1_000_000.0, "rolling_admission": "2_1_0", "priority": "P0_FRESHNESS",
    "hold_sessions": 10, "execution": "T_PLUS_1", "replacement": False,
    "ordinary_stop": None, "target": None, "target_position_risk": portfolio_risk.TARGET_POSITION_RISK,
    "normal_heat_limit": portfolio_risk.NORMAL_HEAT_LIMIT, "hard_heat_limit": portfolio_risk.HARD_HEAT_LIMIT,
    "risk_proxy": "MAX_ATR_PCT_RV20_DAILY_PCT", "rv20_window": portfolio_risk.RV20_WINDOW,
    "rv20_min_returns": portfolio_risk.RV20_MIN_RETURNS,
    "redundancy": cfg["redundancy"],
    "maximum_sector_heat_share": portfolio_risk.MAX_SECTOR_HEAT_SHARE if cfg["redundancy"] else None,
    "maximum_signal_date_heat_share": portfolio_risk.MAX_SIGNAL_DATE_HEAT_SHARE if cfg["redundancy"] else None,
    "correlation_window": portfolio_risk.CORRELATION_WINDOW if cfg["redundancy"] else None,
    "correlation_min_overlap": portfolio_risk.CORRELATION_MIN_OVERLAP if cfg["redundancy"] else None,
    "correlation_multiplier": "1+0.5*MAX(0,weighted_avg_corr);CAP_1.5" if cfg["redundancy"] else None,
    "pb_r2_authority": False, "path_risk_authority": False, "market_regime_authority": False,
    "authority": "SHADOW_ONLY", "paper_only": True,
} for account_id, cfg in PORTFOLIO_RISK_ACCOUNT_CONFIGS.items()}
PORTFOLIO_RISK_CONFIG_HASHES = {key: digest(value) for key, value in PORTFOLIO_RISK_CONFIGS.items()}
PORTFOLIO_RISK_METHODOLOGY_HASHES = {key: digest({"version": PORTFOLIO_RISK_VERSION,
    "account": key, "config_hash": PORTFOLIO_RISK_CONFIG_HASHES[key],
    "code_identity": "autopaper_prospective.py:portfolio-risk-v1b"}) for key in PORTFOLIO_RISK_ACCOUNT_CONFIGS}
ACCOUNT_CONFIGS.update({account_id: {"volatility_sizing": False, "date_cap": None,
    "authority": "SHADOW_ONLY", "rolling": True, "hold_sessions": 10,
    "portfolio_risk": True, **cfg} for account_id, cfg in PORTFOLIO_RISK_ACCOUNT_CONFIGS.items()})
PORTFOLIO_RISK_REVIEW_GATE = {"calendar_days": 90, "completed_b1_trades": 75,
    "completed_b2_trades": 75, "unique_signal_dates": 40, "risk_interventions": 30,
    "redundancy_interventions": 20, "completed_matched_groups": 60,
    "automatic_promotion": False}
OPPORTUNITY_SELECTION_VERSION = opportunity_selection.VERSION
OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS = {
    "SHADOW_SELECT_QUALITY": {"policy": "C1"},
    "SHADOW_SELECT_RISK_ADJ": {"policy": "C2"},
}
OPPORTUNITY_SELECTION_CONFIGS = {
    account_id: {
        "version": OPPORTUNITY_SELECTION_VERSION, "policy": cfg["policy"],
        "control_account": "SHADOW_RISK_BUDGET", "downstream_risk_policy": "B1",
        "capital": 1_000_000.0, "rolling_admission": "2_1_0", "hold_sessions": 10,
        "execution": "T_PLUS_1", "replacement": False, "ordinary_stop": None, "target": None,
        "feature_manifest_hash": opportunity_selection.FEATURE_MANIFEST_HASH,
        "random_manifest_hash": opportunity_selection.RANDOM_MANIFEST_HASH,
        "ranking_config_hash": (opportunity_selection.C1_CONFIG_HASH if cfg["policy"] == "C1"
                                else opportunity_selection.C2_CONFIG_HASH),
        "pb_authority": False, "role_authority": False, "ha_authority": False,
        "market_regime_authority": False, "decision_authority": False,
        "authority": "SHADOW_ONLY", "paper_only": True,
    } for account_id, cfg in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS.items()
}
OPPORTUNITY_SELECTION_CONFIG_HASHES = {key: digest(value) for key, value in OPPORTUNITY_SELECTION_CONFIGS.items()}
OPPORTUNITY_SELECTION_METHODOLOGY_HASHES = {
    "SHADOW_SELECT_QUALITY": opportunity_selection.C1_METHODOLOGY_HASH,
    "SHADOW_SELECT_RISK_ADJ": opportunity_selection.C2_METHODOLOGY_HASH,
}
ACCOUNT_CONFIGS.update({account_id: {"volatility_sizing": False, "date_cap": None,
    "authority": "SHADOW_ONLY", "rolling": True, "hold_sessions": 10,
    "portfolio_risk": True, "selection_policy": cfg["policy"], "redundancy": False}
    for account_id, cfg in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS.items()})
DYNAMIC_EXPOSURE_VERSION = dynamic_exposure.VERSION
DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS = {
    "SHADOW_EXPOSURE_PORTFOLIO": {"policy": "D1", "market_layer": False},
    "SHADOW_EXPOSURE_COMBINED": {"policy": "D2", "market_layer": True},
}
DYNAMIC_EXPOSURE_CONFIGS = dynamic_exposure.CONFIGS
DYNAMIC_EXPOSURE_CONFIG_HASHES = dynamic_exposure.CONFIG_HASHES
DYNAMIC_EXPOSURE_METHODOLOGY_HASHES = dynamic_exposure.METHODOLOGY_HASHES
ACCOUNT_CONFIGS.update({account_id: {"volatility_sizing": False, "date_cap": None,
    "authority": "SHADOW_ONLY", "rolling": True, "hold_sessions": 10,
    "portfolio_risk": True, "dynamic_exposure": True, "redundancy": False, **cfg}
    for account_id, cfg in DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS.items()})
DYNAMIC_EXPOSURE_REVIEW_GATE = {"calendar_days": 90, "unique_signal_dates": 40,
    "completed_trades_per_account": 75, "intervention_events": 30,
    "multiplier_at_or_below_075_events": 20,
    "matched_d0_d1_d2_completed_opportunities": 60, "automatic_promotion": False}
OPPORTUNITY_SELECTION_REVIEW_GATE = {"calendar_days": 90, "unique_signal_dates": 40,
    "constrained_selection_events": 50, "competitive_events": 30,
    "mature_comparisons": 100, "completed_matched_events": 60,
    "random_seeds": 20, "automatic_promotion": False}
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
    if isinstance(decision.get("selection_inputs"), Mapping):
        candidate["selection_inputs"] = dict(decision["selection_inputs"])
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


def ensure_edge_accounts(activation_timestamp, activation_market_date):
    """Create only the three isolated Edge Capture manifests/accounts."""
    database._require_database()
    timestamp = _utc(activation_timestamp)
    session = database.SessionLocal()
    try:
        for account_id in EDGE_ACCOUNT_CONFIGS:
            method_hash = EDGE_METHODOLOGY_HASHES[account_id]
            config_hash = EDGE_CONFIG_HASHES[account_id]
            manifest = session.get(database.AutoPaperManifest, method_hash)
            if manifest is None:
                session.add(database.AutoPaperManifest(
                    methodology_hash=method_hash, version=f"{EDGE_VERSION}_{EDGE_ACCOUNT_CONFIGS[account_id]['policy']}",
                    activation_timestamp=timestamp, activation_market_date=activation_market_date,
                    config_payload=_json(EDGE_CONFIGS[account_id]), config_hash=config_hash,
                    code_identity="autopaper_prospective.py:edge-v1a"))
            elif manifest.config_hash != config_hash:
                raise RuntimeError("EDGE_CAPTURE_MANIFEST_CONFLICT")
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None:
                session.add(database.AutoPaperAccount(
                    account_id=account_id, methodology_hash=method_hash,
                    initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                    state_version=0, created_at=timestamp, updated_at=timestamp))
            elif account.methodology_hash != method_hash:
                raise RuntimeError("EDGE_CAPTURE_ACCOUNT_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def ensure_portfolio_risk_accounts(activation_timestamp, activation_market_date):
    """Create only the isolated Phase-B manifests/accounts."""
    database._require_database()
    timestamp = _utc(activation_timestamp)
    session = database.SessionLocal()
    try:
        for account_id, config in PORTFOLIO_RISK_CONFIGS.items():
            method_hash = PORTFOLIO_RISK_METHODOLOGY_HASHES[account_id]
            config_hash = PORTFOLIO_RISK_CONFIG_HASHES[account_id]
            manifest = session.get(database.AutoPaperManifest, method_hash)
            if manifest is None:
                session.add(database.AutoPaperManifest(
                    methodology_hash=method_hash,
                    version=f"{PORTFOLIO_RISK_VERSION}_{PORTFOLIO_RISK_ACCOUNT_CONFIGS[account_id]['policy']}",
                    activation_timestamp=timestamp, activation_market_date=activation_market_date,
                    config_payload=_json(config), config_hash=config_hash,
                    code_identity="autopaper_prospective.py:portfolio-risk-v1b"))
            elif manifest.config_hash != config_hash:
                raise RuntimeError("PORTFOLIO_RISK_MANIFEST_CONFLICT")
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None:
                session.add(database.AutoPaperAccount(
                    account_id=account_id, methodology_hash=method_hash,
                    initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                    state_version=0, created_at=timestamp, updated_at=timestamp))
            elif account.methodology_hash != method_hash:
                raise RuntimeError("PORTFOLIO_RISK_ACCOUNT_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def ensure_dynamic_exposure_accounts(activation_timestamp, activation_market_date):
    """Create fresh isolated D1/D2 accounts; never clone the evolved B1 control."""
    database._require_database(); timestamp = _utc(activation_timestamp)
    session = database.SessionLocal()
    try:
        for account_id, config in DYNAMIC_EXPOSURE_CONFIGS.items():
            method_hash = DYNAMIC_EXPOSURE_METHODOLOGY_HASHES[account_id]
            config_hash = DYNAMIC_EXPOSURE_CONFIG_HASHES[account_id]
            manifest = session.get(database.AutoPaperManifest, method_hash)
            if manifest is None:
                session.add(database.AutoPaperManifest(
                    methodology_hash=method_hash,
                    version=f"{DYNAMIC_EXPOSURE_VERSION}_{DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS[account_id]['policy']}",
                    activation_timestamp=timestamp, activation_market_date=activation_market_date,
                    config_payload=_json(config), config_hash=config_hash,
                    code_identity="dynamic_exposure_engine.py:v1"))
            elif manifest.config_hash != config_hash:
                raise RuntimeError("DYNAMIC_EXPOSURE_MANIFEST_CONFLICT")
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None:
                session.add(database.AutoPaperAccount(
                    account_id=account_id, methodology_hash=method_hash,
                    initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                    state_version=0, created_at=timestamp, updated_at=timestamp))
            elif account.methodology_hash != method_hash:
                raise RuntimeError("DYNAMIC_EXPOSURE_ACCOUNT_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def ensure_opportunity_selection_accounts(activation_timestamp, activation_market_date):
    """Create only the isolated Phase-C manifests/accounts."""
    database._require_database(); timestamp = _utc(activation_timestamp)
    session = database.SessionLocal()
    try:
        for account_id, config in OPPORTUNITY_SELECTION_CONFIGS.items():
            method_hash = OPPORTUNITY_SELECTION_METHODOLOGY_HASHES[account_id]
            config_hash = OPPORTUNITY_SELECTION_CONFIG_HASHES[account_id]
            manifest = session.get(database.AutoPaperManifest, method_hash)
            if manifest is None:
                session.add(database.AutoPaperManifest(
                    methodology_hash=method_hash,
                    version=f"{OPPORTUNITY_SELECTION_VERSION}_{OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS[account_id]['policy']}",
                    activation_timestamp=timestamp, activation_market_date=activation_market_date,
                    config_payload=_json(config), config_hash=config_hash,
                    code_identity="autopaper_prospective.py:opportunity-selection-v1c"))
            elif manifest.config_hash != config_hash:
                raise RuntimeError("OPPORTUNITY_SELECTION_MANIFEST_CONFLICT")
            account = session.get(database.AutoPaperAccount, account_id)
            if account is None:
                session.add(database.AutoPaperAccount(
                    account_id=account_id, methodology_hash=method_hash,
                    initial_capital=CONFIG["capital"], cash=CONFIG["capital"], status="ACTIVE",
                    state_version=0, created_at=timestamp, updated_at=timestamp))
            elif account.methodology_hash != method_hash:
                raise RuntimeError("OPPORTUNITY_SELECTION_ACCOUNT_CONFLICT")
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _risk_methodology_hash(account_id):
    if account_id in DYNAMIC_EXPOSURE_METHODOLOGY_HASHES:
        return DYNAMIC_EXPOSURE_METHODOLOGY_HASHES[account_id]
    if account_id in OPPORTUNITY_SELECTION_METHODOLOGY_HASHES:
        return OPPORTUNITY_SELECTION_METHODOLOGY_HASHES[account_id]
    return PORTFOLIO_RISK_METHODOLOGY_HASHES[account_id]


def _persist_selection_event(session, account_id, market_date, rows, admission_budget, risk_intervention):
    rows = [row for row in rows if bool(row["candidate"].get("ranking_eligible", True))]
    if account_id not in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS or not rows:
        return {"constrained": False, "selection_event_id": None}
    event_id = opportunity_selection.selection_event_id(market_date, [row["candidate"]["opportunity_id"] for row in rows])
    constrained = len(rows) > admission_budget or bool(risk_intervention)
    for row in rows:
        candidate = row["candidate"]; policy = OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS[account_id]["policy"]
        payload = {"selection_event_id": event_id, "account_id": account_id, "policy": policy,
            "market_date": market_date.isoformat(), "opportunity_id": candidate["opportunity_id"],
            "symbol": candidate["symbol"], "strategy": candidate["strategy"],
            "signal_date": candidate["signal_date"], "p0_freshness_order": row["p0_order"],
            **(candidate.get("selection_inputs") or {}),
            "feature_percentiles": candidate.get("feature_percentiles"),
            "quality_score": candidate.get("quality_score"),
            "quality_feature_coverage": candidate.get("quality_feature_coverage"),
            "risk_percentile": candidate.get("risk_percentile"),
            "risk_adjusted_quality": candidate.get("risk_adjusted_quality"),
            "selection_rank": candidate.get("selection_rank"), "admitted": row["admitted"],
            "final_decision": row["final_decision"], "reason_code": row["reason_code"],
            "constrained": constrained, "admission_budget": admission_budget,
            "decision_authority": False, "pb_authority": False,
            "feature_manifest_hash": opportunity_selection.FEATURE_MANIFEST_HASH,
            "methodology_hash": OPPORTUNITY_SELECTION_METHODOLOGY_HASHES[account_id]}
        snapshot_id = _hash([OPPORTUNITY_SELECTION_VERSION, event_id, account_id, candidate["opportunity_id"]])
        payload_hash = _hash(payload); existing = session.get(database.OpportunitySelectionCandidateSnapshot, snapshot_id)
        if existing is None:
            session.add(database.OpportunitySelectionCandidateSnapshot(
                snapshot_id=snapshot_id, selection_event_id=event_id, account_id=account_id,
                opportunity_id=candidate["opportunity_id"], signal_date=dt.date.fromisoformat(candidate["signal_date"]),
                market_date=market_date, policy=policy, selection_rank=int(candidate["selection_rank"]),
                admitted=bool(row["admitted"]), constrained=constrained,
                final_decision=row["final_decision"], reason_code=row["reason_code"],
                payload=_json(payload), payload_hash=payload_hash))
        elif existing.payload_hash != payload_hash:
            raise RuntimeError("OPPORTUNITY_SELECTION_SNAPSHOT_IMMUTABILITY_CONFLICT")
    if constrained:
        for seed, ordering in opportunity_selection.random_orderings(event_id,
                [row["candidate"]["opportunity_id"] for row in rows]).items():
            for rank, opportunity_id in enumerate(ordering, 1):
                payload = {"selection_event_id": event_id, "seed": seed, "opportunity_id": opportunity_id,
                    "random_rank": rank, "algorithm_version": opportunity_selection.RANDOM_MANIFEST["version"],
                    "random_manifest_hash": opportunity_selection.RANDOM_MANIFEST_HASH,
                    "live_authority": False}
                ordering_id = _hash([event_id, seed, opportunity_id])
                existing = session.get(database.OpportunitySelectionRandomOrdering, ordering_id)
                if existing is None:
                    session.add(database.OpportunitySelectionRandomOrdering(
                        ordering_id=ordering_id, selection_event_id=event_id, seed=seed,
                        opportunity_id=opportunity_id, random_rank=rank,
                        payload=_json(payload), payload_hash=_hash(payload)))
                elif existing.payload_hash != _hash(payload):
                    raise RuntimeError("OPPORTUNITY_SELECTION_RANDOM_IMMUTABILITY_CONFLICT")
    return {"constrained": constrained, "selection_event_id": event_id}


def _portfolio_risk_match_id(opportunity_id, signal_date):
    return _hash(["PORTFOLIO_RISK_MATCH_V1", opportunity_id, str(signal_date)])


def _ensure_portfolio_risk_match(session, account_id, candidate):
    if account_id not in PORTFOLIO_RISK_ACCOUNT_CONFIGS:
        return
    match_id = _portfolio_risk_match_id(candidate["opportunity_id"], candidate["signal_date"])
    leg_id = _hash([match_id, account_id])
    if session.get(database.PortfolioRiskMatch, leg_id) is None:
        payload = {"match_id": match_id, "opportunity_id": candidate["opportunity_id"],
            "signal_date": candidate["signal_date"], "account_id": account_id,
            "policy": PORTFOLIO_RISK_ACCOUNT_CONFIGS[account_id]["policy"],
            "methodology_hash": PORTFOLIO_RISK_METHODOLOGY_HASHES[account_id],
            "actual_entries_only": True}
        session.add(database.PortfolioRiskMatch(
            leg_id=leg_id, match_id=match_id, opportunity_id=candidate["opportunity_id"],
            signal_date=dt.date.fromisoformat(candidate["signal_date"]), account_id=account_id,
            policy=PORTFOLIO_RISK_ACCOUNT_CONFIGS[account_id]["policy"], considered=True,
            entered=False, payload=_json(payload), payload_hash=_hash(payload)))


def _update_portfolio_risk_match(session, account_id, opportunity_id, **values):
    if account_id not in PORTFOLIO_RISK_ACCOUNT_CONFIGS:
        return
    row = session.query(database.PortfolioRiskMatch).filter_by(
        account_id=account_id, opportunity_id=opportunity_id).first()
    if row is None:
        return
    for key, value in values.items():
        setattr(row, key, value)
    payload = json.loads(row.payload); payload.update(values)
    row.payload = _json(payload); row.payload_hash = _hash(payload)


def _active_positions(session, account_id):
    return session.query(database.AutoPaperPosition).filter_by(account_id=account_id, status="OPEN").all()


def _pending_exit_for_position(session, account_id, position_id):
    for order in session.query(database.AutoPaperOrder).filter_by(
            account_id=account_id, side="SELL", status="PENDING").all():
        if json.loads(order.payload).get("position_id") == position_id:
            return order
    return None


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


def _edge_match_id(opportunity_id: str, signal_date: str) -> str:
    return _hash(["EDGE_CAPTURE_MATCH_V1", opportunity_id, signal_date])


def _ensure_edge_match(session, account_id, candidate):
    if account_id not in EDGE_ACCOUNT_CONFIGS:
        return
    match_id = _edge_match_id(candidate["opportunity_id"], candidate["signal_date"])
    leg_id = _hash([match_id, account_id])
    if session.get(database.EdgeCaptureMatch, leg_id) is None:
        payload = {"edge_capture_match_id": match_id, "opportunity_id": candidate["opportunity_id"],
            "signal_date": candidate["signal_date"], "account_id": account_id,
            "exit_policy": EDGE_ACCOUNT_CONFIGS[account_id]["policy"],
            "actual_entries_only": True, "methodology_hash": EDGE_METHODOLOGY_HASHES[account_id]}
        session.add(database.EdgeCaptureMatch(
            leg_id=leg_id, edge_capture_match_id=match_id, opportunity_id=candidate["opportunity_id"],
            signal_date=dt.date.fromisoformat(candidate["signal_date"]), account_id=account_id,
            exit_policy=EDGE_ACCOUNT_CONFIGS[account_id]["policy"], payload=_json(payload),
            payload_hash=_hash(payload)))


def _update_edge_match(session, account_id, opportunity_id, **values):
    if account_id not in EDGE_ACCOUNT_CONFIGS:
        return
    row = session.query(database.EdgeCaptureMatch).filter_by(
        account_id=account_id, opportunity_id=opportunity_id).first()
    if row is None:
        return
    for key, value in values.items():
        setattr(row, key, value)
    payload = json.loads(row.payload); payload.update({key: value for key, value in values.items()})
    row.payload = _json(payload); row.payload_hash = _hash(payload)


def _causal_frame(histories, symbol, market_date):
    frame = histories.get(yahoo_nse_symbol(symbol))
    if frame is None: frame = histories.get(symbol)
    if frame is None or frame.empty: return None
    data = frame.copy(); data.columns = [str(x).lower().replace("adjusted_", "") for x in data.columns]
    if not {"open", "high", "low", "close", "volume"}.issubset(data.columns): return None
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize(); data = data.sort_index()
    data = data.loc[data.index <= pd.Timestamp(market_date)]
    return data if len(data) else None


def thesis_failure_components(histories, symbol, market_date):
    """Frozen completed-session T/R/V state; missingness can only suppress exits."""
    data = _causal_frame(histories, symbol, market_date)
    components = {"T": "NOT_AVAILABLE", "R": "NOT_AVAILABLE", "V": "NOT_AVAILABLE"}
    details = {}
    if data is not None:
        close = pd.to_numeric(data["close"], errors="coerce")
        if len(close) >= 21:
            ema = close.ewm(span=20, adjust=False, min_periods=20).mean()
            if pd.notna(ema.iloc[-1]) and pd.notna(ema.iloc[-2]) and pd.notna(close.iloc[-1]):
                components["T"] = bool(close.iloc[-1] < ema.iloc[-1] and ema.iloc[-1] < ema.iloc[-2])
                details.update(close=float(close.iloc[-1]), ema20=float(ema.iloc[-1]), ema20_slope=float(ema.iloc[-1] - ema.iloc[-2]))
        if len(data) >= 21:
            volume = pd.to_numeric(data["volume"], errors="coerce")
            previous_mean = float(volume.iloc[-21:-1].mean())
            high, low, current = (float(data[x].iloc[-1]) for x in ("high", "low", "close"))
            previous = float(close.iloc[-2]); day_range = high - low
            if math.isfinite(previous_mean) and previous_mean > 0 and day_range > 0:
                volume_ratio = float(volume.iloc[-1]) / previous_mean
                close_location = (current - low) / day_range
                components["V"] = bool(current < previous and volume_ratio >= 1.5 and close_location <= .35)
                details.update(previous_close=previous, volume_ratio=volume_ratio, close_location=close_location)
        benchmark = histories.get("NIFTY500")
        if benchmark is not None and len(close) >= 6:
            bench = benchmark.copy(); bench.columns = [str(x).lower().replace("adjusted_", "") for x in bench.columns]
            if "close" in bench.columns:
                bench.index = pd.to_datetime(bench.index).tz_localize(None).normalize(); bench = bench.sort_index()
                bench = bench.loc[bench.index <= pd.Timestamp(market_date)]
                if len(bench) >= 6:
                    stock_return = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100
                    bench_close = pd.to_numeric(bench["close"], errors="coerce")
                    benchmark_return = (float(bench_close.iloc[-1]) / float(bench_close.iloc[-6]) - 1) * 100
                    relative = stock_return - benchmark_return
                    if math.isfinite(relative):
                        components["R"] = bool(relative <= -3.0)
                        details.update(stock_return_5d=stock_return, nifty500_return_5d=benchmark_return,
                                       relative_return_5d=relative)
    available = sum(value != "NOT_AVAILABLE" for value in components.values())
    active = sum(value is True for value in components.values())
    state = "NOT_AVAILABLE" if available < 2 else ("TRUE" if active >= 2 else "FALSE")
    return {"components": components, "available_components": available,
            "active_components": active, "state": state, **details}


def _observe_thesis_state(session, account, position, market_date, now, histories):
    pp = json.loads(position.payload)
    if position.age < 3:
        result = {"state": "GRACE_PERIOD", "components": {"T": "NOT_AVAILABLE", "R": "NOT_AVAILABLE", "V": "NOT_AVAILABLE"},
                  "available_components": 0, "active_components": 0}
    else:
        result = thesis_failure_components(histories, position.symbol, market_date)
    previous_streak = int(pp.get("thesis_failure_streak") or 0)
    streak = previous_streak + 1 if result["state"] == "TRUE" else 0
    result.update({"position_age": position.age, "consecutive_failure_sessions": streak,
                   "thesis_rule_version": THESIS_RULE_VERSION})
    obs_id = _hash([account.account_id, position.position_id, market_date.isoformat(), THESIS_RULE_VERSION])
    if session.get(database.EdgeCaptureThesisObservation, obs_id) is None:
        session.add(database.EdgeCaptureThesisObservation(
            observation_id=obs_id, account_id=account.account_id, position_id=position.position_id,
            opportunity_id=position.opportunity_id, market_date=market_date, state=result["state"],
            payload=_json(result), payload_hash=_hash(result)))
    pp["thesis_state"] = result["state"]; pp["thesis_components"] = result["components"]
    pp["thesis_failure_streak"] = streak
    if result["state"] == "TRUE" and not pp.get("thesis_first_failure_date"):
        pp["thesis_first_failure_date"] = market_date.isoformat()
    position.payload = _json(pp)
    return result


def _record_edge_early_exit(session, account, position, market_date, exit_type, fill, trigger_payload):
    if account.account_id not in EDGE_ACCOUNT_CONFIGS:
        return
    pp = json.loads(position.payload); horizon = int(pp.get("planned_hold_sessions") or 20)
    trigger_date = dt.date.fromisoformat(str(trigger_payload.get("trigger_date") or market_date)[:10])
    trigger_age = (trigger_payload.get("thesis") or {}).get("position_age")
    if trigger_age is None and exit_type == "CATASTROPHE_EXIT":
        catastrophe = session.query(database.AutoPaperCatastropheObservation).filter_by(
            account_id=account.account_id, position_id=position.position_id).first()
        if catastrophe is not None:
            trigger_age = json.loads(catastrophe.payload).get("position_age_at_trigger")
    trigger_age = int(position.age if trigger_age is None else trigger_age)
    entry_cost = float(pp["entry_cost"]); actual_return = (fill["proceeds"] / entry_cost - 1) * 100
    payload = {"exit_type": exit_type, "trigger_date": trigger_date.isoformat(),
        "actual_exit_date": market_date.isoformat(), "actual_exit_price": fill["price"],
        "actual_exit_net_return_pct": actual_return, "original_horizon": horizon,
        "position_age_at_trigger": trigger_age, "horizon_counterfactual_net_return_pct": None,
        "horizon_mfe_pct": None, "horizon_mae_pct": None, "post_exit_maximum_recovery_pct": None,
        "post_exit_additional_downside_pct": None, "exit_regret_pct": None,
        "trigger_evidence": trigger_payload.get("thesis") or trigger_payload}
    observation_id = _hash([account.account_id, position.position_id, "EDGE_EARLY_EXIT"])
    if session.get(database.EdgeCaptureExitObservation, observation_id) is None:
        session.add(database.EdgeCaptureExitObservation(
            observation_id=observation_id, account_id=account.account_id,
            position_id=position.position_id, opportunity_id=position.opportunity_id,
            symbol=position.symbol, exit_type=exit_type, trigger_date=trigger_date,
            actual_exit_date=market_date, status="AWAITING_H20", sessions_observed=0,
            payload=_json(payload), payload_hash=_hash(payload)))


def _advance_edge_exit_observations(session, account_id, histories, market_date, execution):
    rows = session.query(database.EdgeCaptureExitObservation).filter_by(
        account_id=account_id, status="AWAITING_H20").all()
    for row in rows:
        if row.actual_exit_date is None or market_date <= row.actual_exit_date: continue
        bar = _bar(histories, row.symbol, market_date)
        if not execution.executable(bar): continue
        payload = json.loads(row.payload); row.sessions_observed += 1
        actual_exit = float(payload["actual_exit_price"])
        payload["post_exit_maximum_recovery_pct"] = max(payload.get("post_exit_maximum_recovery_pct") or 0.,
            (bar[1] / actual_exit - 1) * 100)
        payload["post_exit_additional_downside_pct"] = min(payload.get("post_exit_additional_downside_pct") or 0.,
            (bar[2] / actual_exit - 1) * 100)
        entry = session.get(database.AutoPaperPosition, row.position_id).entry_price
        payload["horizon_mfe_pct"] = max(payload.get("horizon_mfe_pct") or 0., (bar[1] / entry - 1) * 100)
        payload["horizon_mae_pct"] = min(payload.get("horizon_mae_pct") or 0., (bar[2] / entry - 1) * 100)
        remaining = max(0, int(payload["original_horizon"]) - int(payload["position_age_at_trigger"]))
        if row.sessions_observed >= remaining:
            counterfactual = (bar[0] * (1 - CONFIG["slippage"]) / entry - 1) * 100
            payload["horizon_counterfactual_net_return_pct"] = counterfactual
            payload["exit_regret_pct"] = counterfactual - float(payload["actual_exit_net_return_pct"])
            row.status = "MATURE"
        row.payload = _json(payload); row.payload_hash = _hash(payload)


def _submit_catastrophe_order(session, account, position, market_date, now, bar, execution,
                              previous_close=None, telemetry_attempts=None, hold_sessions=10):
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
            "net_catastrophe_return": None, f"h{hold_sessions}_counterfactual_return": None,
            "subsequent_mfe_pct": None, "subsequent_mae_pct": None,
            "position_age_at_trigger": position.age, "counterfactual_horizon": hold_sessions}
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
    if account.account_id in EDGE_ACCOUNT_CONFIGS:
        observation.status = "FILLED_AWAITING_H20"
        _record_edge_early_exit(session, account, position, market_date, "CATASTROPHE_EXIT", fill, payload)
        _update_edge_match(session, account.account_id, position.opportunity_id,
            exit_date=market_date, exit_price=fill["price"])
    _journal(session, account, market_date, now, "EXIT", position.opportunity_id,
        "CATASTROPHE_EXIT", order_id=order_id, actual_allocated_capital=fill["proceeds"],
        **{key: value for key, value in payload.items() if key != "reason"})
    (telemetry_attempts if telemetry_attempts is not None else []).append(
        (order_id, bar, previous_close, "FILLED", fill))
    return True


def _advance_catastrophe_observations(session, histories, market_date, execution,
                                      account_id="SHADOW_CATASTROPHE", hold_sessions=10):
    rows = session.query(database.AutoPaperCatastropheObservation).filter_by(
        account_id=account_id, status=f"FILLED_AWAITING_H{hold_sessions}").all()
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
        remaining = max(0, hold_sessions - int(payload.get("position_age_at_trigger") or 0))
        if row.sessions_after_trigger >= remaining:
            reference = bar[0]; entry_price = float(payload["entry_price"])
            key = f"h{hold_sessions}_counterfactual_return"
            payload[key] = (reference * (1 - CONFIG["slippage"]) / entry_price - 1) * 100
            payload["exit_regret_pct"] = payload[key] - float(payload.get("net_catastrophe_return") or 0)
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
        hold_sessions = int(account_cfg.get("hold_sessions") or CONFIG["hold_sessions"])
        now = timestamp
        if account_cfg.get("catastrophe"):
            _advance_catastrophe_observations(session, histories, market_date, execution,
                                              account_id, hold_sessions)
        if account_id in EDGE_ACCOUNT_CONFIGS:
            _advance_edge_exit_observations(session, account_id, histories, market_date, execution)
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
                        observation.status = f"FILLED_AWAITING_H{hold_sessions}"; observation.payload = _json(op)
                        observation.payload_hash = _hash(op)
                _update_edge_match(session, account_id, position.opportunity_id,
                    exit_date=market_date, exit_price=fill["price"])
                _update_portfolio_risk_match(session, account_id, position.opportunity_id,
                    exit_date=market_date, realized_return_pct=(fill["proceeds"] / entry_cost - 1) * 100)
                if exit_reason in {"THESIS_FAILURE_EXIT", "CATASTROPHE_EXIT"}:
                    _record_edge_early_exit(session, account, position, market_date,
                                            exit_reason, fill, payload)
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
                    "methodology_hash": account.methodology_hash, "planned_hold_sessions": hold_sessions}
                if account_cfg.get("portfolio_risk"):
                    ppayload.update({key: qpayload.get(key) for key in (
                        "atr_pct", "rv20_pct", "risk_proxy_pct", "risk_proxy_coverage", "sector",
                        "target_risk_rupees", "correlation_multiplier", "weighted_avg_corr",
                        "raw_risk_rupees", "effective_risk_rupees", "risk_decision_snapshot_id")})
                if account_cfg.get("catastrophe"):
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
                _update_edge_match(session, account_id, order.opportunity_id,
                    execution_date=market_date, entry_price=fill["price"], entry_size=fill["cost"])
                _update_portfolio_risk_match(session, account_id, order.opportunity_id,
                    entered=True, allocation=fill["cost"], entry_date=market_date,
                    entry_price=fill["price"])
                _journal(session, account, market_date, now, "ENTER", order.opportunity_id, "ENTERED",
                         order_id=order.order_id, sizing_multiplier=json.loads(order.payload)["sizing_multiplier"],
                         requested_capital=order.requested_capital, actual_allocated_capital=fill["cost"],
                         quantity=fill["quantity"], fill_price=fill["price"])
                telemetry_attempts.append((order.order_id, bar, context["previous_close"], "FILLED", fill))
                stats["new_entries"] += 1
        # Entry occurs at today's open, so today's completed bar is holding session 1.
        session.flush()
        # Mark positions with completed-session OHLC and submit the account's
        # frozen time/protection exit for the next executable open.
        for position in _active_positions(session, account_id):
            mark_context = bar_context(histories, position.symbol, market_date)
            bar = mark_context["bar"]
            existing_edge_exit = (_pending_exit_for_position(session, account_id, position.position_id)
                                  if account_id in EDGE_ACCOUNT_CONFIGS else None)
            if account_cfg.get("catastrophe") and existing_edge_exit is None and _submit_catastrophe_order(
                    session, account, position, market_date, now, bar, execution,
                    mark_context["previous_close"], telemetry_attempts, hold_sessions):
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
            if existing_edge_exit is not None:
                position.planned_exit_state = "PENDING_NEXT_EXECUTABLE_OPEN"
                _journal(session, account, market_date, now, "HOLD", position.opportunity_id,
                         "EXIT_PENDING_NEXT_EXECUTABLE_OPEN", position_age=position.age,
                         planned_exit_state=position.planned_exit_state,
                         order_id=existing_edge_exit.order_id)
                continue
            thesis_triggered = False
            if account_cfg.get("thesis"):
                thesis = _observe_thesis_state(session, account, position, market_date, now, histories)
                if thesis["state"] == "TRUE" and thesis["consecutive_failure_sessions"] >= 2:
                    order_id = _hash([account_id, position.position_id, market_date.isoformat(), "THESIS_SELL"])
                    if session.get(database.AutoPaperOrder, order_id) is None:
                        payload = {"position_id": position.position_id, "reason": "THESIS_FAILURE_EXIT",
                            "execution": "NEXT_EXECUTABLE_OPEN", "trigger_date": market_date.isoformat(),
                            "thesis": thesis, "thesis_rule_version": THESIS_RULE_VERSION}
                        session.add(database.AutoPaperOrder(
                            order_id=order_id, account_id=account_id, opportunity_id=position.opportunity_id,
                            symbol=position.symbol, side="SELL", requested_session=market_date,
                            order_timestamp=now, status="PENDING", quantity=position.quantity,
                            requested_capital=None, payload=_json(payload), payload_hash=_hash(payload)))
                    position.planned_exit_state = "THESIS_FAILURE_PENDING_NEXT_OPEN"
                    position.planned_exit_date = market_date; thesis_triggered = True
            if not thesis_triggered and position.age >= hold_sessions:
                order_id = _hash([account_id, position.position_id, market_date.isoformat(), "SELL"])
                if session.get(database.AutoPaperOrder, order_id) is None:
                    payload = {"position_id": position.position_id, "reason": f"H{hold_sessions}_TIME_EXIT",
                               "execution": "NEXT_EXECUTABLE_OPEN"}
                    session.add(database.AutoPaperOrder(
                        order_id=order_id, account_id=account_id, opportunity_id=position.opportunity_id,
                        symbol=position.symbol, side="SELL", requested_session=market_date,
                        order_timestamp=now, status="PENDING", quantity=position.quantity,
                        requested_capital=None, payload=_json(payload), payload_hash=_hash(payload)))
                position.planned_exit_state = "SUBMITTED_NEXT_OPEN"
                position.planned_exit_date = market_date
            _journal(session, account, market_date, now, "HOLD", position.opportunity_id,
                     ("THESIS_FAILURE_EXIT_SUBMITTED" if thesis_triggered else
                      f"H{hold_sessions}_TIME_EXIT_SUBMITTED" if position.age >= hold_sessions else "HOLD"),
                     position_age=position.age, planned_exit_state=position.planned_exit_state)
        # Ingest exactly today's new qualified stream and create outcome links.
        for raw in decisions:
            candidate = _candidate(raw, market_date); oid = candidate["opportunity_id"]
            if not oid: continue
            if account_id in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS and not isinstance(candidate.get("selection_inputs"), Mapping):
                candidate["selection_inputs"] = opportunity_selection.extract_candidate_features(candidate, histories)
            _ensure_edge_match(session, account_id, candidate)
            _ensure_portfolio_risk_match(session, account_id, candidate)
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
        p0_order = {item.opportunity_id: rank for rank, item in enumerate(sorted(
            active, key=lambda x: (-x.signal_date.toordinal(), x.opportunity_id)), 1)}
        if account_id in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS:
            policy = OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS[account_id]["policy"]
            open_symbols = {position.symbol for position in _active_positions(session, account_id)}
            candidate_rows = []
            for item in active:
                candidate = json.loads(item.payload)
                inputs = candidate.get("selection_inputs") or {}
                candidate["ranking_eligible"] = bool(
                    not candidate.get("missing") and
                    item.symbol not in open_symbols and
                    float(candidate.get("traded_value") or 0.) >= CONFIG["minimum_traded_value"] and
                    inputs.get("eligible") is True)
                candidate_rows.append(candidate)
            ranked = opportunity_selection.score_candidate_set(
                candidate_rows, policy)
            item_by_id = {item.opportunity_id: item for item in active}
            active = []
            for candidate in ranked:
                item = item_by_id[candidate["opportunity_id"]]
                item.payload = _json(candidate); item.updated_at = now; active.append(item)
        else:
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
        risk_mode = bool(account_cfg.get("portfolio_risk"))
        risk_rows = [portfolio_risk.frozen_position_risk(position) for position in positions] if risk_mode else []
        virtual_heat = sum(float(row["risk_rupees"]) for row in risk_rows)
        dynamic_mode = bool(account_cfg.get("dynamic_exposure"))
        prior_peak_nav = current_nav
        if dynamic_mode:
            prior_snapshots = session.query(database.AutoPaperPortfolioSnapshot).filter(
                database.AutoPaperPortfolioSnapshot.account_id == account_id,
                database.AutoPaperPortfolioSnapshot.market_date < market_date).all()
            prior_peak_nav = max([current_nav] + [float(row.nav) for row in prior_snapshots])
        exposure_state = (dynamic_exposure.exposure_state(
            current_nav, prior_peak_nav, histories, market_date,
            bool(account_cfg.get("market_layer"))) if dynamic_mode else None)
        normal_heat_fraction = (float(exposure_state["dynamic_normal_heat_fraction"])
                                if exposure_state else portfolio_risk.NORMAL_HEAT_LIMIT)
        sector_heat, signal_heat = {}, {}
        for row in risk_rows:
            sector_heat[row["sector"]] = sector_heat.get(row["sector"], 0.) + float(row["risk_rupees"])
            signal_heat[row["signal_date"]] = signal_heat.get(row["signal_date"], 0.) + float(row["risk_rupees"])
        risk_deferred = risk_downsized = redundancy_deferred = 0
        selection_rows = []
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
            risk_snapshot = None
            # Preserve the frozen C3/Phase-A volatility sizing contract.  The
            # Phase-B accounts replace this multiplier with explicit risk
            # budgeting below; all pre-existing accounts retain it verbatim.
            if not risk_mode and account_cfg.get("volatility_sizing") and not reason:
                multiplier = max(.5, min(1., 4. / float(candidate["atr_pct"])))
            if risk_mode and not reason:
                rv = portfolio_risk.realized_volatility_20(histories, candidate["symbol"], market_date)
                proxy = portfolio_risk.candidate_risk_proxy(candidate.get("atr_pct"), rv["rv20_pct"])
                metadata = session.get(database.AutoPaperOpportunityMetadata, oid)
                sector = metadata.sector if metadata else candidate.get("sector") or "UNKNOWN"
                if not proxy["eligible"]:
                    reason = "DATA_UNAVAILABLE"
                else:
                    candidate.update({"rv20_pct": rv["rv20_pct"], "rv20_valid_returns": rv["valid_returns"],
                        "risk_proxy_pct": proxy["risk_proxy_pct"],
                        "risk_proxy_coverage": proxy["risk_proxy_coverage"], "sector": sector})
            strategy_room = current_nav * CONFIG["max_strategy_weight"] - strategy_value.get(candidate["strategy"], 0.)
            ordinary_cap = min(current_nav * CONFIG["max_stock_weight"],
                virtual_cash - current_nav * CONFIG["reserve"], strategy_room,
                candidate["traded_value"] * CONFIG["participation_limit"]) if not reason else 0.
            budget = ordinary_cap * multiplier
            if risk_mode and not reason:
                risk_fraction = float(candidate["risk_proxy_pct"]) / 100
                target_risk = current_nav * portfolio_risk.TARGET_POSITION_RISK
                raw_allocation = target_risk / risk_fraction
                correlation = ({"weighted_avg_corr": "NOT_AVAILABLE", "correlation_state": "NOT_AVAILABLE",
                    "correlation_multiplier": 1., "observations": []})
                if account_cfg.get("redundancy"):
                    correlation = portfolio_risk.weighted_average_correlation(
                        histories, candidate["symbol"], risk_rows, market_date)
                corr_multiplier = float(correlation["correlation_multiplier"])
                base_normal_remaining = max(0., current_nav * portfolio_risk.NORMAL_HEAT_LIMIT - virtual_heat)
                normal_remaining = max(0., current_nav * normal_heat_fraction - virtual_heat)
                heat_allocation_cap = normal_remaining / (risk_fraction * corr_multiplier)
                budget = min(ordinary_cap, raw_allocation, heat_allocation_cap)
                cap_reason = ("CORRELATION_RISK_BUDGET" if corr_multiplier > 1. and
                    heat_allocation_cap < float(candidate["reference_price"]) <= normal_remaining / risk_fraction else None)
                if account_cfg.get("redundancy"):
                    # Concentration is measured against the frozen normal heat
                    # capacity, avoiding an impossible 100%-share startup state.
                    concentration_cap = current_nav * portfolio_risk.NORMAL_HEAT_LIMIT * portfolio_risk.MAX_SECTOR_HEAT_SHARE
                    sector_room = max(0., concentration_cap - sector_heat.get(candidate["sector"], 0.))
                    date_room = max(0., concentration_cap - signal_heat.get(candidate["signal_date"], 0.))
                    sector_cap = sector_room / (risk_fraction * corr_multiplier)
                    date_cap = date_room / (risk_fraction * corr_multiplier)
                    if sector_cap < budget: cap_reason = "SECTOR_RISK_CONCENTRATION"
                    budget = min(budget, sector_cap)
                    if date_cap < budget: cap_reason = "SIGNAL_DATE_RISK_CONCENTRATION"
                    budget = min(budget, date_cap)
                minimum_allocation = float(candidate["reference_price"])
                if budget < minimum_allocation:
                    base_heat_allocation_cap = base_normal_remaining / (risk_fraction * corr_multiplier)
                    if dynamic_mode and heat_allocation_cap < minimum_allocation <= base_heat_allocation_cap:
                        reason = "DYNAMIC_EXPOSURE_DEFERRED"
                    elif virtual_heat + minimum_allocation * risk_fraction * corr_multiplier > current_nav * portfolio_risk.HARD_HEAT_LIMIT:
                        reason = "PORTFOLIO_RISK_BUDGET"
                    elif cap_reason:
                        reason = cap_reason
                    else:
                        reason = "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK"
                    budget = 0.
                final_raw_risk = budget * risk_fraction
                final_effective_risk = final_raw_risk * corr_multiplier
                downsized = budget > 0 and budget + .01 < min(ordinary_cap, raw_allocation)
                risk_snapshot = {"version": PORTFOLIO_RISK_VERSION,
                    "methodology_hash": _risk_methodology_hash(account_id),
                    "account_id": account_id, "opportunity_id": oid, "market_date": market_date.isoformat(),
                    "account_nav": current_nav, "cash": virtual_cash, "open_positions": virtual_positions,
                    "current_heat_rupees": virtual_heat,
                    "current_heat_pct": virtual_heat / current_nav * 100 if current_nav else None,
                    "remaining_normal_heat_rupees": normal_remaining,
                    "base_normal_heat_rupees": current_nav * portfolio_risk.NORMAL_HEAT_LIMIT,
                    "effective_normal_heat_rupees": current_nav * normal_heat_fraction,
                    "atr_pct": candidate.get("atr_pct"), "rv20_pct": rv["rv20_pct"],
                    "risk_proxy_pct": candidate["risk_proxy_pct"],
                    "risk_proxy_coverage": candidate["risk_proxy_coverage"],
                    "target_risk_rupees": target_risk, "raw_risk_budget_allocation": raw_allocation,
                    "requested_allocation": ordinary_cap, "final_allocation": budget,
                    "candidate_raw_risk": final_raw_risk, "candidate_effective_risk": final_effective_risk,
                    "sector": candidate["sector"],
                    "sector_heat_share_of_normal_budget": ((sector_heat.get(candidate["sector"], 0.) + final_effective_risk) /
                        (current_nav * portfolio_risk.NORMAL_HEAT_LIMIT)) if current_nav else None,
                    "signal_date_heat_share_of_normal_budget": ((signal_heat.get(candidate["signal_date"], 0.) + final_effective_risk) /
                        (current_nav * portfolio_risk.NORMAL_HEAT_LIMIT)) if current_nav else None,
                    "weighted_avg_corr": correlation["weighted_avg_corr"],
                    "correlation_state": correlation["correlation_state"],
                    "correlation_multiplier": corr_multiplier, "final_decision": "DEFER" if reason else (
                        "ADMIT_DOWNSIZED" if downsized else "ADMIT"), "reason": reason or (
                        "RISK_BUDGET_DOWNSIZE" if downsized else "ORDER_SUBMITTED_T1"),
                    "decision_authority": False, "path_risk_authority": False,
                    "pb_r2_authority": False, "market_regime_authority": False}
                if exposure_state:
                    risk_snapshot["dynamic_exposure"] = exposure_state
                snapshot_id = _hash([PORTFOLIO_RISK_VERSION, account_id, oid, market_date.isoformat()])
                candidate.update({"target_risk_rupees": target_risk,
                    "correlation_multiplier": corr_multiplier, "weighted_avg_corr": correlation["weighted_avg_corr"],
                    "raw_risk_rupees": final_raw_risk, "effective_risk_rupees": final_effective_risk,
                    "risk_decision_snapshot_id": snapshot_id})
                existing_snapshot = session.get(database.PortfolioRiskDecisionSnapshot, snapshot_id)
                if existing_snapshot is None:
                    session.add(database.PortfolioRiskDecisionSnapshot(
                        snapshot_id=snapshot_id, account_id=account_id, opportunity_id=oid,
                        market_date=market_date, final_decision=risk_snapshot["final_decision"],
                        reason_code=risk_snapshot["reason"], payload=_json(risk_snapshot),
                        payload_hash=_hash(risk_snapshot)))
                elif existing_snapshot.payload_hash != _hash(risk_snapshot):
                    raise RuntimeError("PORTFOLIO_RISK_DECISION_IMMUTABILITY_CONFLICT")
                item.payload = _json(candidate); item.updated_at = now
                if reason:
                    risk_deferred += 1
                    if reason in {"SECTOR_RISK_CONCENTRATION", "SIGNAL_DATE_RISK_CONCENTRATION", "CORRELATION_RISK_BUDGET"}:
                        redundancy_deferred += 1
                elif downsized:
                    risk_downsized += 1
            if account_cfg["date_cap"] is not None and budget > 0:
                room = current_nav * account_cfg["date_cap"] - date_commitment.get(candidate["signal_date"], 0.)
                budget = min(budget, max(0., room))
            if budget <= 0 and not reason:
                reason = "INSUFFICIENT_CASH" if virtual_cash - current_nav * CONFIG["reserve"] <= 0 else "NO_CAPACITY"
            if risk_mode and risk_snapshot is None:
                snapshot_id = _hash([PORTFOLIO_RISK_VERSION, account_id, oid, market_date.isoformat()])
                risk_snapshot = {"version": PORTFOLIO_RISK_VERSION,
                    "methodology_hash": _risk_methodology_hash(account_id),
                    "account_id": account_id, "opportunity_id": oid, "market_date": market_date.isoformat(),
                    "account_nav": current_nav, "cash": virtual_cash, "open_positions": virtual_positions,
                    "current_heat_rupees": virtual_heat,
                    "remaining_normal_heat_rupees": max(0., current_nav * normal_heat_fraction - virtual_heat),
                    "risk_proxy_pct": "NOT_EVALUATED", "risk_proxy_coverage": "NOT_EVALUATED",
                    "target_risk_rupees": current_nav * portfolio_risk.TARGET_POSITION_RISK,
                    "requested_allocation": ordinary_cap, "final_allocation": 0.,
                    "weighted_avg_corr": "NOT_EVALUATED", "correlation_multiplier": "NOT_EVALUATED",
                    "sector": candidate.get("sector") or "UNKNOWN", "final_decision": "DEFER" if reason not in {
                        "DATA_UNAVAILABLE", "LIQUIDITY_FAIL", "POSITION_ALREADY_EXISTS"} else "REJECT",
                    "reason": reason or "NO_CAPACITY", "decision_authority": False,
                    "path_risk_authority": False, "pb_r2_authority": False, "market_regime_authority": False}
                if exposure_state:
                    risk_snapshot["dynamic_exposure"] = exposure_state
                if session.get(database.PortfolioRiskDecisionSnapshot, snapshot_id) is None:
                    session.add(database.PortfolioRiskDecisionSnapshot(
                        snapshot_id=snapshot_id, account_id=account_id, opportunity_id=oid,
                        market_date=market_date, final_decision=risk_snapshot["final_decision"],
                        reason_code=risk_snapshot["reason"], payload=_json(risk_snapshot),
                        payload_hash=_hash(risk_snapshot)))
            if dynamic_mode and risk_snapshot is not None:
                dynamic_payload = {"version": DYNAMIC_EXPOSURE_VERSION,
                    "methodology_hash": DYNAMIC_EXPOSURE_METHODOLOGY_HASHES[account_id],
                    "config_hash": DYNAMIC_EXPOSURE_CONFIG_HASHES[account_id],
                    "account_id": account_id, "policy": account_cfg["policy"],
                    "opportunity_id": oid, "symbol": candidate.get("symbol"),
                    "signal_date": candidate.get("signal_date"),
                    "market_date": market_date.isoformat(), **exposure_state,
                    "current_portfolio_heat_rupees": virtual_heat,
                    "current_portfolio_heat_pct": virtual_heat / current_nav * 100 if current_nav else None,
                    "remaining_dynamic_heat_rupees": max(0., current_nav * normal_heat_fraction - virtual_heat),
                    "requested_allocation": ordinary_cap,
                    "final_allocation": float(risk_snapshot.get("final_allocation") or 0.),
                    "final_decision": risk_snapshot["final_decision"],
                    "reason": risk_snapshot["reason"], "decision_authority": False,
                    "existing_position_action": "UNCHANGED_H10"}
                dynamic_snapshot_id = _hash([DYNAMIC_EXPOSURE_VERSION, account_id, oid,
                                             market_date.isoformat()])
                existing_dynamic = session.get(database.DynamicExposureDecisionSnapshot,
                                               dynamic_snapshot_id)
                if existing_dynamic is None:
                    session.add(database.DynamicExposureDecisionSnapshot(
                        snapshot_id=dynamic_snapshot_id, account_id=account_id,
                        opportunity_id=oid, market_date=market_date,
                        final_decision=dynamic_payload["final_decision"],
                        reason_code=dynamic_payload["reason"], payload=_json(dynamic_payload),
                        payload_hash=_hash(dynamic_payload)))
                elif existing_dynamic.payload_hash != _hash(dynamic_payload):
                    raise RuntimeError("DYNAMIC_EXPOSURE_DECISION_IMMUTABILITY_CONFLICT")
            if reason in ("DATA_UNAVAILABLE", "LIQUIDITY_FAIL", "POSITION_ALREADY_EXISTS"):
                item.status = "REJECTED"; item.updated_at = now
                link = session.get(database.AutoPaperCounterfactualLink, _hash([account_id, oid]))
                if link: link.terminal_reason = reason
                _journal(session, account, market_date, now, "REJECT", oid, reason,
                         rank=active.index(item) + 1, sizing_multiplier=multiplier)
                selection_rows.append({"candidate": candidate, "p0_order": p0_order.get(oid),
                    "admitted": False, "final_decision": "REJECT", "reason_code": reason})
                continue
            if budget <= 0:
                if reason in {"ROLLING_DAILY_ADMISSION_LIMIT", "ROLLING_TARGET_OCCUPANCY"}:
                    rolling_deferred += 1
                _journal(session, account, market_date, now, "QUEUE", oid, reason or "LOWER_PRIORITY",
                         rank=active.index(item) + 1, sizing_multiplier=multiplier)
                selection_rows.append({"candidate": candidate, "p0_order": p0_order.get(oid),
                    "admitted": False, "final_decision": "DEFER", "reason_code": reason or "LOWER_PRIORITY"})
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
            if risk_snapshot:
                journal_details.update(risk_decision_snapshot_id=candidate["risk_decision_snapshot_id"],
                    risk_proxy_pct=candidate["risk_proxy_pct"],
                    risk_proxy_coverage=candidate["risk_proxy_coverage"],
                    target_risk_rupees=candidate["target_risk_rupees"],
                    resulting_portfolio_heat_pct=(virtual_heat + candidate["effective_risk_rupees"]) /
                        current_nav * 100 if current_nav else None)
            if candidate.get("prospective_origin"):
                journal_details.update(prospective_origin=candidate["prospective_origin"],
                    intended_execution_date=candidate.get("intended_execution_date"))
            _journal(session, account, market_date, now, "QUEUE", oid, "ORDER_SUBMITTED_T1", **journal_details)
            selection_rows.append({"candidate": candidate, "p0_order": p0_order.get(oid),
                "admitted": True, "final_decision": "ADMIT", "reason_code": "ORDER_SUBMITTED_T1"})
            virtual_cash -= budget; virtual_positions += 1; virtual_symbols.add(item.symbol)
            if rolling: rolling_used += 1
            strategy_value[candidate["strategy"]] = strategy_value.get(candidate["strategy"], 0.) + budget
            date_commitment[candidate["signal_date"]] = date_commitment.get(candidate["signal_date"], 0.) + budget
            if risk_mode:
                risk_row = {"symbol": candidate["symbol"], "sector": candidate["sector"],
                    "signal_date": candidate["signal_date"], "market_value": budget,
                    "risk_proxy_pct": candidate["risk_proxy_pct"],
                    "raw_risk_rupees": candidate["raw_risk_rupees"],
                    "correlation_multiplier": candidate["correlation_multiplier"],
                    "risk_rupees": candidate["effective_risk_rupees"]}
                risk_rows.append(risk_row); virtual_heat += candidate["effective_risk_rupees"]
                sector_heat[candidate["sector"]] = sector_heat.get(candidate["sector"], 0.) + candidate["effective_risk_rupees"]
                signal_heat[candidate["signal_date"]] = signal_heat.get(candidate["signal_date"], 0.) + candidate["effective_risk_rupees"]
        selection_event = _persist_selection_event(
            session, account_id, market_date, selection_rows, rolling_budget,
            any(row["reason_code"] in {"PORTFOLIO_RISK_BUDGET", "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK"}
                for row in selection_rows))
        positions = _active_positions(session, account_id); nav = _nav(account, positions)
        if len(positions) > CONFIG["max_positions"]:
            raise RuntimeError("AUTOPAPER_HARD_CAPACITY_BREACH")
        if rolling and virtual_positions > ROLLING_CONFIG["target_occupancy"]:
            raise RuntimeError("AUTOPAPER_ROLLING_TARGET_BREACH")
        if account.cash < -1e-7 or abs(nav - (account.cash + sum(p.quantity * p.current_mark for p in positions))) > .01:
            raise RuntimeError("AUTOPAPER_PORTFOLIO_RECONCILIATION_FAILED")
        if risk_mode:
            # The hard ceiling applies to admission-time frozen risk. Market
            # marks may move heat above it later; that is telemetry, never a
            # forced exit. A new commitment may not cross it.
            if virtual_heat > nav * portfolio_risk.HARD_HEAT_LIMIT + .01:
                raise RuntimeError("PORTFOLIO_RISK_HARD_HEAT_BREACH")
        snapshot_payload = {"methodology_hash": account.methodology_hash, "account": account_id,
            "cash": account.cash, "nav": nav, "open_positions": len(positions),
            "pending_orders": session.query(database.AutoPaperOrder).filter_by(account_id=account_id, status="PENDING").count()}
        if rolling:
            snapshot_payload.update({"target_occupancy": ROLLING_CONFIG["target_occupancy"],
                "hard_capacity": ROLLING_CONFIG["max_positions_hard"],
                "admission_start_positions": admission_start_positions,
                "admission_budget": rolling_budget, "admission_used": rolling_used,
                "rolling_deferred": rolling_deferred})
        if risk_mode:
            snapshot_payload.update({"portfolio_heat_pct": virtual_heat / nav * 100 if nav else None,
                "normal_heat_limit_pct": normal_heat_fraction * 100,
                "base_normal_heat_limit_pct": portfolio_risk.NORMAL_HEAT_LIMIT * 100,
                "hard_heat_limit_pct": portfolio_risk.HARD_HEAT_LIMIT * 100,
                "remaining_normal_heat_pct": max(0., nav * normal_heat_fraction - virtual_heat) /
                    nav * 100 if nav else None,
                "risk_deferred": risk_deferred, "risk_downsized": risk_downsized,
                "redundancy_deferred": redundancy_deferred})
        if exposure_state:
            snapshot_payload["dynamic_exposure"] = exposure_state
        fingerprint = _hash(snapshot_payload)
        if not session.query(database.AutoPaperPortfolioSnapshot).filter_by(
                account_id=account_id, market_date=market_date, state_fingerprint=fingerprint).first():
            session.add(database.AutoPaperPortfolioSnapshot(
                account_id=account_id, market_date=market_date, cash=account.cash, nav=nav,
                open_positions=len(positions), state_fingerprint=fingerprint, payload=_json(snapshot_payload)))
        account.last_market_date = market_date; account.state_version += 1; account.updated_at = now
        if account_id == ROLLING_ACCOUNT_ID:
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
        if account_id in EDGE_ACCOUNT_CONFIGS:
            edge_positions = _active_positions(session, account_id)
            today_decisions = session.query(database.AutoPaperDecision).filter_by(
                account_id=account_id, market_date=market_date).all()
            control_positions = len(_active_positions(session, ROLLING_ACCOUNT_ID)) if session.get(
                database.AutoPaperAccount, ROLLING_ACCOUNT_ID) else None
            blocked = sum(x.reason_code in {"NO_CAPACITY", "ROLLING_TARGET_OCCUPANCY"} for x in today_decisions)
            capacity_divergence = bool(control_positions is not None and len(edge_positions) != control_positions)
            telemetry = {"version": EDGE_VERSION, "account_id": account_id,
                "policy": EDGE_ACCOUNT_CONFIGS[account_id]["policy"], "market_date": market_date.isoformat(),
                "decision_authority": False, "open_positions": len(edge_positions), "cash": account.cash,
                "nav": nav, "gross_exposure": sum(x.quantity * x.current_mark for x in edge_positions),
                "cash_utilization": (nav - account.cash) / nav if nav else None,
                "capital_days": sum(x.age * x.quantity * x.current_mark for x in edge_positions),
                "pending_entries": session.query(database.AutoPaperOrder).filter_by(
                    account_id=account_id, side="BUY", status="PENDING").count(),
                "rolling_deferred": sum(x.reason_code == "ROLLING_DAILY_ADMISSION_LIMIT" for x in today_decisions),
                "capacity_blocked": blocked, "control_open_positions": control_positions,
                "capacity_divergence": capacity_divergence,
                "blocked_while_control_below_target": blocked if control_positions is not None and control_positions < 9 else 0,
                "hold_sessions": hold_sessions}
            telemetry_id = _hash([EDGE_VERSION, account_id, market_date.isoformat()])
            if session.get(database.EdgeCaptureTelemetry, telemetry_id) is None:
                session.add(database.EdgeCaptureTelemetry(
                    telemetry_id=telemetry_id, account_id=account_id, market_date=market_date,
                    payload=_json(telemetry), payload_hash=_hash(telemetry)))
        if (account_id in PORTFOLIO_RISK_ACCOUNT_CONFIGS or
                account_id in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS or
                account_id in DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS):
            current_state = portfolio_risk.portfolio_heat_state(_active_positions(session, account_id), nav)
            prior_rows = session.query(database.PortfolioRiskTelemetry).filter_by(account_id=account_id).all()
            prior_payloads = [json.loads(row.payload) for row in prior_rows]
            today_risk_decisions = session.query(database.PortfolioRiskDecisionSnapshot).filter_by(
                account_id=account_id, market_date=market_date).all()
            all_risk_decisions = session.query(database.PortfolioRiskDecisionSnapshot).filter_by(
                account_id=account_id).all()
            risk_reason_codes = {"PORTFOLIO_RISK_BUDGET", "MIN_EXECUTABLE_SIZE_EXCEEDS_RISK",
                "CORRELATION_RISK_BUDGET", "SECTOR_RISK_CONCENTRATION",
                "SIGNAL_DATE_RISK_CONCENTRATION"}
            deferred_ids = {row.opportunity_id for row in all_risk_decisions
                if row.final_decision == "DEFER" and row.reason_code in risk_reason_codes}
            links = session.query(database.AutoPaperCounterfactualLink).filter_by(account_id=account_id).all()
            entered_ids = {row.opportunity_id for row in links if row.entered}
            expired_ids = {row.opportunity_id for row in links if row.terminal_reason == "EXPIRED"}
            sector_values = current_state["sector_heat"]
            signal_values = current_state["signal_date_heat"]
            corr_values = [float(row.get("correlation_multiplier") or 1.) for row in current_state["positions"]]
            weighted_corr_values = [float(row["weighted_avg_corr"]) for row in current_state["positions"]
                if row.get("weighted_avg_corr") not in {None, "NOT_AVAILABLE"}]
            telemetry = {"version": PORTFOLIO_RISK_VERSION, "account_id": account_id,
                "policy": (PORTFOLIO_RISK_ACCOUNT_CONFIGS.get(account_id) or
                           OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS.get(account_id) or
                           DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS[account_id])["policy"],
                "methodology_hash": _risk_methodology_hash(account_id),
                "market_date": market_date.isoformat(), "decision_authority": False,
                "nav": nav, "cash": account.cash,
                "gross_exposure": sum(position.quantity * position.current_mark for position in positions),
                "cash_utilization": (nav - account.cash) / nav if nav else None,
                "open_positions": len(positions), "portfolio_heat_pct": current_state["portfolio_heat_pct"],
                "committed_heat_pct": virtual_heat / nav * 100 if nav else None,
                "remaining_normal_heat_pct": max(0., nav * normal_heat_fraction - virtual_heat) /
                    nav * 100 if nav else None,
                "normal_heat_limit_pct": normal_heat_fraction * 100,
                "base_normal_heat_limit_pct": portfolio_risk.NORMAL_HEAT_LIMIT * 100,
                "hard_heat_limit_pct": portfolio_risk.HARD_HEAT_LIMIT * 100,
                "peak_heat_pct": max([float(p.get("portfolio_heat_pct") or 0) for p in prior_payloads] +
                    [float(current_state["portfolio_heat_pct"] or 0)]),
                "largest_sector_heat_share": max(sector_values.values()) / (nav * portfolio_risk.NORMAL_HEAT_LIMIT)
                    if sector_values and nav else None,
                "largest_signal_date_heat_share": max(signal_values.values()) / (nav * portfolio_risk.NORMAL_HEAT_LIMIT)
                    if signal_values and nav else None,
                "average_position_correlation_multiplier": float(np.mean(corr_values)) if corr_values else None,
                "weighted_average_portfolio_correlation": (float(np.mean(weighted_corr_values))
                    if weighted_corr_values else None),
                "risk_deferred_today": sum(row.final_decision == "DEFER" and
                    row.reason_code in risk_reason_codes for row in today_risk_decisions),
                "downsized_today": sum(row.final_decision == "ADMIT_DOWNSIZED" for row in today_risk_decisions),
                "redundancy_deferred_today": sum(row.reason_code in {"SECTOR_RISK_CONCENTRATION",
                    "SIGNAL_DATE_RISK_CONCENTRATION", "CORRELATION_RISK_BUDGET"} for row in today_risk_decisions),
                "risk_deferred_then_entered": len(deferred_ids & entered_ids),
                "risk_deferred_then_expired": len(deferred_ids & expired_ids),
                "capital_days": sum(position.age * position.quantity * position.current_mark for position in positions)}
            if exposure_state:
                telemetry["dynamic_exposure"] = exposure_state
            telemetry_id = _hash([PORTFOLIO_RISK_VERSION, account_id, market_date.isoformat()])
            if session.get(database.PortfolioRiskTelemetry, telemetry_id) is None:
                session.add(database.PortfolioRiskTelemetry(
                    telemetry_id=telemetry_id, account_id=account_id, market_date=market_date,
                    payload=_json(telemetry), payload_hash=_hash(telemetry)))
            if dynamic_mode:
                dynamic_decisions = session.query(database.DynamicExposureDecisionSnapshot).filter_by(
                    account_id=account_id, market_date=market_date).all()
                all_dynamic = session.query(database.DynamicExposureDecisionSnapshot).filter_by(
                    account_id=account_id).all()
                dynamic_deferred_ids = {row.opportunity_id for row in all_dynamic
                    if row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED"}
                dynamic_links = session.query(database.AutoPaperCounterfactualLink).filter_by(
                    account_id=account_id).all()
                dynamic_payload = {"version": DYNAMIC_EXPOSURE_VERSION,
                    "methodology_hash": DYNAMIC_EXPOSURE_METHODOLOGY_HASHES[account_id],
                    "config_hash": DYNAMIC_EXPOSURE_CONFIG_HASHES[account_id],
                    "account_id": account_id, "policy": account_cfg["policy"],
                    "market_date": market_date.isoformat(), **exposure_state,
                    "portfolio_heat_pct": current_state["portfolio_heat_pct"],
                    "committed_heat_pct": virtual_heat / nav * 100 if nav else None,
                    "gross_exposure": sum(position.quantity * position.current_mark for position in positions),
                    "cash_utilization": (nav - account.cash) / nav if nav else None,
                    "deferred_today": sum(row.reason_code == "DYNAMIC_EXPOSURE_DEFERRED"
                                          for row in dynamic_decisions),
                    "admissions_allowed_today": sum(str(row.final_decision).startswith("ADMIT")
                                                    for row in dynamic_decisions),
                    "deferred_then_entered": len(dynamic_deferred_ids & {
                        row.opportunity_id for row in dynamic_links if row.entered}),
                    "deferred_then_expired": len(dynamic_deferred_ids & {
                        row.opportunity_id for row in dynamic_links if row.terminal_reason == "EXPIRED"}),
                    "queue_reconciled": True, "account_isolated": True,
                    "decision_authority": False}
                telemetry_id = _hash([DYNAMIC_EXPOSURE_VERSION, account_id, market_date.isoformat()])
                if session.get(database.DynamicExposureTelemetry, telemetry_id) is None:
                    session.add(database.DynamicExposureTelemetry(
                        telemetry_id=telemetry_id, account_id=account_id,
                        market_date=market_date, payload=_json(dynamic_payload),
                        payload_hash=_hash(dynamic_payload)))
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
        if risk_mode:
            stats.update(portfolio_heat_pct=virtual_heat / nav * 100 if nav else None,
                remaining_heat_pct=max(0., nav * normal_heat_fraction - virtual_heat) / nav * 100 if nav else None,
                risk_deferred=risk_deferred, downsized=risk_downsized,
                redundancy_deferred=redundancy_deferred)
        if exposure_state:
            stats.update(exposure_multiplier=exposure_state["combined_multiplier"],
                portfolio_multiplier=exposure_state["portfolio_multiplier"],
                market_multiplier=exposure_state["market_multiplier"],
                market_state=exposure_state["market"]["status"],
                dynamic_heat_pct=exposure_state["dynamic_normal_heat_pct"])
        if account_id in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS:
            stats.update(selection_policy=OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS[account_id]["policy"],
                selection_event_id=selection_event["selection_event_id"],
                constrained_selection_event=selection_event["constrained"],
                feature_coverage_failures=sum((row["candidate"].get("quality_feature_coverage") or 0) < 3
                                              for row in selection_rows))
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


def _process_edge_if_activated(decisions, histories, market_date, timestamp, killed):
    """Advance each Edge account independently after its causal activation boundary."""
    session = database.SessionLocal()
    try:
        activation = session.query(database.EdgeCaptureActivation).order_by(
            database.EdgeCaptureActivation.created_at.desc()).first()
        if activation is None: return {}
        if activation.status == "PENDING_NEXT_COHORT":
            if activation.after_market_date is not None and market_date <= activation.after_market_date:
                return {key: {"account_id": key, "status": "PENDING_NEXT_COHORT"} for key in EDGE_ACCOUNT_CONFIGS}
            activation.status = "ACTIVE"; activation.activation_signal_date = market_date
            activation.provenance = "EDGE_CAPTURE_PROSPECTIVE"
            payload = json.loads(activation.payload); payload.update({"status": "ACTIVE",
                "activation_signal_date": market_date.isoformat(), "provenance": activation.provenance})
            activation.payload = _json(payload); activation.payload_hash = _hash(payload); session.commit()
        signal_date, provenance, activated_at = (activation.activation_signal_date,
            activation.provenance, activation.activation_timestamp)
    except Exception:
        session.rollback(); raise
    finally: session.close()
    if signal_date is None or market_date < signal_date: return {}
    ensure_edge_accounts(activated_at, signal_date)
    stream = [{**row, "prospective_origin": provenance} for row in ([] if killed else decisions)]
    results = {}
    for account_id in EDGE_ACCOUNT_CONFIGS:
        try:
            results[account_id] = _process_account(account_id, stream, histories, market_date, timestamp)
            try: persist_risk_snapshot(account_id, market_date, histories, decisions)
            except Exception as exc: results[account_id].setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")
        except Exception as exc:
            results[account_id] = {"account_id": account_id, "status": "FAILED", "error": type(exc).__name__}
    return results


def _sync_portfolio_risk_control_matches(decisions, market_date):
    """Record the existing B0 control as attribution only; never changes B0."""
    session = database.SessionLocal()
    try:
        for raw in decisions:
            candidate = _candidate(raw, market_date)
            if not candidate["opportunity_id"]:
                continue
            match_id = _portfolio_risk_match_id(candidate["opportunity_id"], candidate["signal_date"])
            leg_id = _hash([match_id, ROLLING_ACCOUNT_ID])
            row = session.get(database.PortfolioRiskMatch, leg_id)
            if row is None:
                payload = {"match_id": match_id, "opportunity_id": candidate["opportunity_id"],
                    "signal_date": candidate["signal_date"], "account_id": ROLLING_ACCOUNT_ID,
                    "policy": "B0", "methodology_hash": ROLLING_METHODOLOGY_HASH,
                    "attribution_only": True}
                row = database.PortfolioRiskMatch(
                    leg_id=leg_id, match_id=match_id, opportunity_id=candidate["opportunity_id"],
                    signal_date=dt.date.fromisoformat(candidate["signal_date"]), account_id=ROLLING_ACCOUNT_ID,
                    policy="B0", considered=True, entered=False, payload=_json(payload), payload_hash=_hash(payload))
                session.add(row)
            position = session.query(database.AutoPaperPosition).filter_by(
                account_id=ROLLING_ACCOUNT_ID, opportunity_id=candidate["opportunity_id"]).first()
            trade = session.query(database.AutoPaperTrade).filter_by(
                account_id=ROLLING_ACCOUNT_ID, opportunity_id=candidate["opportunity_id"]).first()
            if position:
                row.entered = True; row.allocation = json.loads(position.payload).get("entry_cost")
                row.entry_date = position.entry_date; row.entry_price = position.entry_price
            if trade:
                row.entered = True; row.exit_date = trade.exit_date
                row.realized_return_pct = trade.realized_return_pct
            values = json.loads(row.payload); values.update({"entered": row.entered,
                "allocation": row.allocation, "entry_date": row.entry_date,
                "entry_price": row.entry_price, "exit_date": row.exit_date,
                "realized_return_pct": row.realized_return_pct})
            row.payload = _json(values); row.payload_hash = _hash(values)
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _process_portfolio_risk_if_activated(decisions, histories, market_date, timestamp, killed):
    """Advance B1/B2 independently after the persisted causal boundary."""
    session = database.SessionLocal()
    try:
        activation = session.query(database.PortfolioRiskActivation).order_by(
            database.PortfolioRiskActivation.created_at.desc()).first()
        if activation is None:
            return {}
        if activation.status == "PENDING_NEXT_COHORT":
            if activation.after_market_date is not None and market_date <= activation.after_market_date:
                return {key: {"account_id": key, "status": "PENDING_NEXT_COHORT"}
                        for key in PORTFOLIO_RISK_ACCOUNT_CONFIGS}
            activation.status = "ACTIVE"; activation.activation_signal_date = market_date
            activation.provenance = "PORTFOLIO_RISK_PROSPECTIVE"
            payload = json.loads(activation.payload); payload.update({"status": "ACTIVE",
                "activation_signal_date": market_date.isoformat(), "provenance": activation.provenance})
            activation.payload = _json(payload); activation.payload_hash = _hash(payload); session.commit()
        signal_date, provenance, activated_at = (activation.activation_signal_date,
            activation.provenance, activation.activation_timestamp)
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
    if signal_date is None or market_date < signal_date:
        return {}
    ensure_portfolio_risk_accounts(activated_at, signal_date)
    stream = [{**row, "prospective_origin": provenance} for row in ([] if killed else decisions)]
    results = {}
    try:
        _sync_portfolio_risk_control_matches(stream, market_date)
    except Exception:
        # Matching is attribution-only and may not affect any execution path.
        pass
    for account_id in PORTFOLIO_RISK_ACCOUNT_CONFIGS:
        try:
            results[account_id] = _process_account(account_id, stream, histories, market_date, timestamp)
            try:
                persist_risk_snapshot(account_id, market_date, histories, decisions)
            except Exception as exc:
                results[account_id].setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")
        except Exception as exc:
            results[account_id] = {"account_id": account_id, "status": "FAILED", "error": type(exc).__name__}
    return results


def ensure_dynamic_exposure_activation(market_date, timestamp):
    """Freeze V1D at the first cohort not already consumed by the D0 control."""
    session = database.SessionLocal()
    try:
        activation = session.get(database.DynamicExposureActivation,
                                 "DYNAMIC_EXPOSURE_V1D_ACTIVATION")
        if activation is not None:
            return json.loads(activation.payload)
        phase_b = session.query(database.PortfolioRiskActivation).order_by(
            database.PortfolioRiskActivation.created_at.desc()).first()
        if phase_b is None or phase_b.status not in {"ACTIVE", "PENDING_NEXT_COHORT"}:
            return {"status": "WAITING_FOR_B1", "decision_authority": False}
        d0 = session.get(database.AutoPaperAccount, "SHADOW_RISK_BUDGET")
        consumed = bool(d0 and d0.last_market_date and d0.last_market_date >= market_date)
        status = "PENDING_NEXT_COHORT" if consumed else "ACTIVE"
        signal_date = None if consumed else market_date
        after_date = d0.last_market_date if consumed else None
        payload = {"activation_id": "DYNAMIC_EXPOSURE_V1D_ACTIVATION", "status": status,
            "activation_mode": "NEXT_FINALIZED_COHORT" if consumed else "SAME_D0_COHORT",
            "activation_timestamp": timestamp.isoformat(),
            "activation_signal_date": signal_date.isoformat() if signal_date else None,
            "after_market_date": after_date.isoformat() if after_date else None,
            "provenance": "DYNAMIC_EXPOSURE_PROSPECTIVE",
            "control": {"account_id": "SHADOW_RISK_BUDGET", "policy": "D0_B1_P0"},
            "methodology_hashes": DYNAMIC_EXPOSURE_METHODOLOGY_HASHES,
            "config_hashes": DYNAMIC_EXPOSURE_CONFIG_HASHES,
            "market_missing_fallback": "PORTFOLIO_MULTIPLIER_ONLY",
            "historical_backfill": False, "decision_authority": False}
        session.add(database.DynamicExposureActivation(
            activation_id=payload["activation_id"], status=status,
            activation_mode=payload["activation_mode"], activation_timestamp=timestamp,
            activation_signal_date=signal_date, after_market_date=after_date,
            provenance=payload["provenance"], payload=_json(payload),
            payload_hash=_hash(payload)))
        session.commit(); return payload
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _process_dynamic_exposure_if_activated(decisions, histories, market_date, timestamp, killed):
    """Advance D1/D2 independently; never interrupts D0 or prior phases."""
    session = database.SessionLocal()
    try:
        activation = session.get(database.DynamicExposureActivation,
                                 "DYNAMIC_EXPOSURE_V1D_ACTIVATION")
        if activation is None:
            return {}
        if activation.status == "PENDING_NEXT_COHORT":
            if activation.after_market_date is not None and market_date <= activation.after_market_date:
                return {key: {"account_id": key, "status": "PENDING_NEXT_COHORT"}
                        for key in DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS}
            activation.status = "ACTIVE"; activation.activation_signal_date = market_date
            payload = json.loads(activation.payload); payload.update({"status": "ACTIVE",
                "activation_signal_date": market_date.isoformat()})
            activation.payload = _json(payload); activation.payload_hash = _hash(payload)
            session.commit()
        signal_date, provenance, activated_at = (activation.activation_signal_date,
            activation.provenance, activation.activation_timestamp)
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
    if signal_date is None or market_date < signal_date:
        return {}
    ensure_dynamic_exposure_accounts(activated_at, signal_date)
    stream = [{**row, "prospective_origin": provenance}
              for row in ([] if killed else decisions)]
    results = {}
    for account_id in DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS:
        try:
            results[account_id] = _process_account(
                account_id, stream, histories, market_date, timestamp)
            try:
                persist_risk_snapshot(account_id, market_date, histories, decisions)
            except Exception as exc:
                results[account_id].setdefault("warnings", []).append(
                    f"RISK_TELEMETRY:{type(exc).__name__}")
        except Exception as exc:
            results[account_id] = {"account_id": account_id, "status": "FAILED",
                                   "error": type(exc).__name__}
    return results


def ensure_opportunity_selection_activation(market_date, timestamp):
    """Freeze Phase-C at the first not-yet-consumed B1 cohort, otherwise next cohort."""
    session = database.SessionLocal()
    try:
        activation = session.get(database.OpportunitySelectionActivation, "OPPORTUNITY_SELECTION_V1C_ACTIVATION")
        if activation is not None:
            return json.loads(activation.payload)
        phase_b = session.query(database.PortfolioRiskActivation).order_by(
            database.PortfolioRiskActivation.created_at.desc()).first()
        if phase_b is None or phase_b.status not in {"ACTIVE", "PENDING_NEXT_COHORT"}:
            return {"status": "WAITING_FOR_B1", "decision_authority": False}
        b1 = session.get(database.AutoPaperAccount, "SHADOW_RISK_BUDGET")
        consumed = bool(b1 and b1.last_market_date and b1.last_market_date >= market_date)
        mode = "NEXT_FINALIZED_COHORT" if consumed else "SAME_B1_COHORT"
        status = "PENDING_NEXT_COHORT" if consumed else "ACTIVE"
        signal_date = None if consumed else market_date
        after_date = b1.last_market_date if consumed else None
        payload = {"activation_id": "OPPORTUNITY_SELECTION_V1C_ACTIVATION", "status": status,
            "activation_mode": mode, "activation_timestamp": timestamp.isoformat(),
            "activation_signal_date": signal_date.isoformat() if signal_date else None,
            "after_market_date": after_date.isoformat() if after_date else None,
            "provenance": "OPPORTUNITY_SELECTION_PROSPECTIVE",
            "control": {"account_id": "SHADOW_RISK_BUDGET", "policy": "C0", "selection": "P0_FRESHNESS"},
            "methodology_hashes": OPPORTUNITY_SELECTION_METHODOLOGY_HASHES,
            "config_hashes": OPPORTUNITY_SELECTION_CONFIG_HASHES,
            "feature_manifest_hash": opportunity_selection.FEATURE_MANIFEST_HASH,
            "random_manifest_hash": opportunity_selection.RANDOM_MANIFEST_HASH,
            "historical_backfill": False, "decision_authority": False}
        session.add(database.OpportunitySelectionActivation(
            activation_id=payload["activation_id"], status=status, activation_mode=mode,
            activation_timestamp=timestamp, activation_signal_date=signal_date,
            after_market_date=after_date, provenance=payload["provenance"],
            payload=_json(payload), payload_hash=_hash(payload)))
        session.commit(); return payload
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def _process_opportunity_selection_if_activated(decisions, histories, market_date, timestamp, killed):
    """Advance C1/C2 independently; a failure cannot interrupt existing accounts."""
    session = database.SessionLocal()
    try:
        activation = session.get(database.OpportunitySelectionActivation, "OPPORTUNITY_SELECTION_V1C_ACTIVATION")
        if activation is None:
            return {}
        if activation.status == "PENDING_NEXT_COHORT":
            if activation.after_market_date is not None and market_date <= activation.after_market_date:
                return {key: {"account_id": key, "status": "PENDING_NEXT_COHORT"}
                        for key in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS}
            activation.status = "ACTIVE"; activation.activation_signal_date = market_date
            payload = json.loads(activation.payload); payload.update({"status": "ACTIVE",
                "activation_signal_date": market_date.isoformat(), "provenance": activation.provenance})
            activation.payload = _json(payload); activation.payload_hash = _hash(payload); session.commit()
        signal_date, provenance, activated_at = activation.activation_signal_date, activation.provenance, activation.activation_timestamp
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
    if signal_date is None or market_date < signal_date:
        return {}
    ensure_opportunity_selection_accounts(activated_at, signal_date)
    stream = []
    for row in ([] if killed else decisions):
        candidate = _candidate(row, market_date)
        stream.append({**row, "prospective_origin": provenance,
            "selection_inputs": opportunity_selection.extract_candidate_features(candidate, histories)})
    results = {}
    for account_id in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS:
        try:
            results[account_id] = _process_account(account_id, stream, histories, market_date, timestamp)
            try:
                persist_risk_snapshot(account_id, market_date, histories, decisions)
            except Exception as exc:
                results[account_id].setdefault("warnings", []).append(f"RISK_TELEMETRY:{type(exc).__name__}")
        except Exception as exc:
            results[account_id] = {"account_id": account_id, "status": "FAILED", "error": type(exc).__name__}
    return results


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
    try:
        ensure_opportunity_selection_activation(market_date, timestamp)
    except Exception as exc:
        observability_warnings.append(f"OPPORTUNITY_SELECTION_ACTIVATION:{type(exc).__name__}")
    try:
        ensure_dynamic_exposure_activation(market_date, timestamp)
    except Exception as exc:
        observability_warnings.append(f"DYNAMIC_EXPOSURE_ACTIVATION:{type(exc).__name__}")
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
    try:
        edge_results = _process_edge_if_activated(decisions, histories, market_date, timestamp, killed)
        results.update(edge_results)
        for account_id, value in edge_results.items():
            if value.get("status") == "FAILED": observability_warnings.append(f"{account_id}:{value.get('error')}")
    except Exception as exc:
        observability_warnings.append(f"EDGE_CAPTURE:{type(exc).__name__}")
    try:
        risk_results = _process_portfolio_risk_if_activated(
            decisions, histories, market_date, timestamp, killed)
        results.update(risk_results)
        for account_id, value in risk_results.items():
            if value.get("status") == "FAILED":
                observability_warnings.append(f"{account_id}:{value.get('error')}")
    except Exception as exc:
        # Phase-B research is isolated from baseline, rolling and Phase A.
        observability_warnings.append(f"PORTFOLIO_RISK:{type(exc).__name__}")
    try:
        exposure_results = _process_dynamic_exposure_if_activated(
            decisions, histories, market_date, timestamp, killed)
        results.update(exposure_results)
        for account_id, value in exposure_results.items():
            if value.get("status") == "FAILED":
                observability_warnings.append(f"{account_id}:{value.get('error')}")
    except Exception as exc:
        observability_warnings.append(f"DYNAMIC_EXPOSURE:{type(exc).__name__}")
    try:
        selection_results = _process_opportunity_selection_if_activated(
            decisions, histories, market_date, timestamp, killed)
        results.update(selection_results)
        for account_id, value in selection_results.items():
            if value.get("status") == "FAILED":
                observability_warnings.append(f"{account_id}:{value.get('error')}")
    except Exception as exc:
        observability_warnings.append(f"OPPORTUNITY_SELECTION:{type(exc).__name__}")
    health = {"run_id": f"AUTOPAPER-{run_id}", "run_timestamp": timestamp.isoformat(),
        "market_date": market_date.isoformat(), "qualified_opportunities": len(decisions),
        "kill_switch": killed, "paper_only": True, "accounts": results,
        "shadow_run_status": "HEALTHY" if all(k in results and results[k].get("status") != "FAILED" for k in ("SHADOW_C0", "SHADOW_D1", "SHADOW_CATASTROPHE")) else "DEGRADED",
        "rolling_shadow_status": (results.get(ROLLING_ACCOUNT_ID) or {}).get("status", "NOT_ACTIVATED"),
        "edge_capture_status": ("HEALTHY" if all((results.get(key) or {}).get("status") != "FAILED"
            for key in EDGE_ACCOUNT_CONFIGS) else "DEGRADED"),
        "portfolio_risk_status": ("HEALTHY" if all((results.get(key) or {}).get("status") != "FAILED"
            for key in PORTFOLIO_RISK_ACCOUNT_CONFIGS) else "DEGRADED"),
        "dynamic_exposure_status": ("HEALTHY" if all((results.get(key) or {}).get("status") != "FAILED"
            for key in DYNAMIC_EXPOSURE_ACCOUNT_CONFIGS) else "DEGRADED"),
        "opportunity_selection_status": ("HEALTHY" if all((results.get(key) or {}).get("status") != "FAILED"
            for key in OPPORTUNITY_SELECTION_ACCOUNT_CONFIGS) else "DEGRADED"),
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

        for account_id in (*ACCOUNTS, ROLLING_ACCOUNT_ID, *EDGE_ACCOUNT_CONFIGS):
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
            capacity_blocked_ids = {row.opportunity_id for row in decisions if row.reason_code in {
                "NO_CAPACITY", "ROLLING_TARGET_OCCUPANCY"}}
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
                "capacity_blocked_outcomes": outcome_summary(capacity_blocked_ids),
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

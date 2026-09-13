"""Pure/read-mostly AutoPaper P1.1 observability helpers.

Nothing in this module is consulted by qualification, sizing, ordering, or fills.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections import Counter
from typing import Any, Mapping

import numpy as np
import pandas as pd

import database
from provider_symbols import yahoo_nse_symbol


TELEMETRY_VERSION = "AUTOPAPER_RISK_OBSERVABILITY_V1"
CORRELATION_WINDOW = 20
CORRELATION_MIN_OBSERVATIONS = 10
NOT_AVAILABLE = "NOT_AVAILABLE"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


TELEMETRY_CONFIG = {
    "version": TELEMETRY_VERSION,
    "correlation_window_sessions": CORRELATION_WINDOW,
    "minimum_pair_observations": CORRELATION_MIN_OBSERVATIONS,
    "circuit_source": NOT_AVAILABLE,
    "decision_authority": False,
}
TELEMETRY_CONFIG_HASH = digest(TELEMETRY_CONFIG)
TELEMETRY_HASH = digest({"contract": TELEMETRY_CONFIG_HASH, "code": "autopaper_observability.py:v1"})


def _safe_json(value: Any) -> str:
    return json.dumps(database._json_safe(value), sort_keys=True, default=str, allow_nan=False)


def bar_context(histories: Mapping[str, pd.DataFrame], symbol: str, market_date: dt.date) -> dict[str, Any]:
    frame = histories.get(yahoo_nse_symbol(symbol))
    if frame is None:
        frame = histories.get(symbol)
    empty = {"bar": None, "previous_close": None}
    if frame is None or frame.empty:
        return empty
    data = frame.copy()
    data.columns = [str(x).lower().replace("adjusted_", "") for x in data.columns]
    if not {"open", "high", "low", "close", "volume"}.issubset(data.columns):
        return empty
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    data = data.sort_index()
    stamp = pd.Timestamp(market_date)
    rows = data.loc[data.index == stamp]
    if rows.empty:
        return empty
    row = rows.iloc[-1]
    values = tuple(float(row[x]) for x in ("open", "high", "low", "close", "volume"))
    previous = data.loc[data.index < stamp, "close"]
    return {"bar": (*values, values[3] * values[4]),
            "previous_close": float(previous.iloc[-1]) if len(previous) else None}


def classify_market_state(bar, previous_close=None, *, circuit_upper=None, circuit_lower=None) -> tuple[str, str]:
    """Classify observed state without pretending OHLCV proves an exchange circuit."""
    if bar is None or len(bar) < 6:
        return "NON_TRADING", "No completed-session OHLCV row."
    o, h, low, close, volume, traded = bar[:6]
    prices_finite = all(math.isfinite(x) and x > 0 for x in (o, h, low, close))
    if not prices_finite:
        return "NON_TRADING", "OHLC prices are missing or invalid."
    if not math.isfinite(volume) or volume <= 0 or not math.isfinite(traded) or traded <= 0:
        return "ZERO_VOLUME", "Completed bar has no executable volume."
    if circuit_upper is not None and o == h == low == close == circuit_upper:
        return "CONFIRMED_UPPER_CIRCUIT", "Exact sourced upper price band matched."
    if circuit_lower is not None and o == h == low == close == circuit_lower:
        return "CONFIRMED_LOWER_CIRCUIT", "Exact sourced lower price band matched."
    if h == low:
        return "LOCKED_RANGE", "Equal OHLC range; exchange circuit status is unconfirmed."
    valid = low <= min(o, close) <= max(o, close) <= h
    if not valid:
        return "CIRCUIT_STATUS_NOT_AVAILABLE", "OHLC geometry is invalid for execution."
    if previous_close and math.isfinite(previous_close) and previous_close > 0 and o != previous_close:
        return "GAP_EXECUTION", "Executable open differs from previous completed close."
    return "NORMAL_EXECUTABLE", "Executable completed-session bar."


def record_execution_attempt(session, *, account, order, market_date, bar, previous_close,
                             filled=False, fill=None, status=None) -> None:
    market_state, reason = classify_market_state(bar, previous_close)
    telemetry_id = digest([order.order_id, market_date.isoformat(), "EXECUTION_ATTEMPT"])
    if session.get(database.AutoPaperExecutionTelemetry, telemetry_id):
        return
    gap = ((bar[0] / previous_close - 1) * 100) if bar and previous_close and previous_close > 0 else None
    theoretical = None
    if bar and math.isfinite(bar[0]) and bar[0] > 0:
        theoretical = bar[0] * (1.0005 if order.side == "BUY" else .9995)
    payload = {
        "telemetry_version": TELEMETRY_VERSION, "account_id": account.account_id,
        "methodology_hash": account.methodology_hash, "opportunity_id": order.opportunity_id,
        "order_id": order.order_id, "symbol": order.symbol, "side": order.side,
        "intended_execution_session": order.requested_session.isoformat(),
        "observed_session": market_date.isoformat(), "previous_close": previous_close,
        "observed_open": bar[0] if bar else None, "ohlcv": list(bar[:5]) if bar else None,
        "traded_value": bar[5] if bar else None, "theoretical_slippage_price": theoretical,
        "simulated_fill_price": (fill or {}).get("price"),
        "execution_status": status or ("FILLED" if filled else "UNFILLED"),
        "market_state": market_state, "circuit_upper_limit": NOT_AVAILABLE,
        "circuit_lower_limit": NOT_AVAILABLE, "circuit_source": NOT_AVAILABLE,
        "gap_pct": gap, "locked_range": market_state == "LOCKED_RANGE",
        "fillability_reason": reason,
    }
    encoded = _safe_json(payload)
    session.add(database.AutoPaperExecutionTelemetry(
        telemetry_id=telemetry_id, account_id=account.account_id,
        methodology_hash=account.methodology_hash, order_id=order.order_id,
        opportunity_id=order.opportunity_id, symbol=order.symbol, side=order.side,
        intended_session=order.requested_session, observed_session=market_date,
        execution_status=payload["execution_status"], market_state=market_state,
        payload=encoded, payload_hash=digest(payload)))


def capture_sector_metadata(session, decisions, captured_at: dt.datetime) -> None:
    for raw in decisions:
        oid = str(raw.get("opportunity_id") or "").strip()
        if not oid or session.get(database.AutoPaperOpportunityMetadata, oid):
            continue
        value = str(raw.get("sector") or "").strip()
        available = bool(value and value.upper() not in {"GENERAL", "NOT_AVAILABLE", "UNKNOWN"})
        payload = {
            "canonical_security_id": str(raw.get("canonical_security_id") or raw.get("symbol") or ""),
            "symbol": str(raw.get("symbol") or "").upper(),
            "signal_date": str(raw.get("signal_date") or raw.get("data_as_of") or "")[:10],
            "sector": value if available else NOT_AVAILABLE,
            "industry": str(raw.get("industry") or NOT_AVAILABLE) if available else NOT_AVAILABLE,
            "classification_source": str(raw.get("sector_source") or "NIFTY500_RUNTIME_CLASSIFICATION") if available else NOT_AVAILABLE,
            "classification_version": str(raw.get("sector_version") or "CAPTURED_AT_RECOMMENDATION_V1") if available else NOT_AVAILABLE,
            "confidence_status": "AVAILABLE" if available else NOT_AVAILABLE,
            "captured_at": captured_at.isoformat(),
        }
        encoded = _safe_json(payload)
        session.add(database.AutoPaperOpportunityMetadata(
            opportunity_id=oid, symbol=payload["symbol"],
            signal_date=dt.date.fromisoformat(payload["signal_date"]), sector=payload["sector"],
            industry=payload["industry"], classification_source=payload["classification_source"],
            classification_version=payload["classification_version"], confidence_status=payload["confidence_status"],
            payload=encoded, snapshot_hash=digest(payload), captured_at=captured_at))


def sector_concentration(sectors: list[str], weights: list[float] | None = None) -> dict[str, Any]:
    valid = [(s, (weights or [1.0] * len(sectors))[i]) for i, s in enumerate(sectors) if s != NOT_AVAILABLE]
    if not valid:
        return {"unique_sectors": 0, "largest_sector_share": None, "hhi": None, "coverage": 0.0}
    totals = Counter()
    for sector, weight in valid:
        totals[sector] += float(weight)
    denominator = sum(totals.values())
    shares = [value / denominator for value in totals.values()] if denominator else []
    return {"unique_sectors": len(totals), "largest_sector_share": max(shares) if shares else None,
            "largest_sector": max(totals, key=totals.get) if totals else None,
            "hhi": sum(x * x for x in shares) if shares else None,
            "coverage": len(valid) / len(sectors) if sectors else 0.0}


def correlation_metrics(histories: Mapping[str, pd.DataFrame], symbols: list[str]) -> dict[str, Any]:
    returns = {}
    for symbol in sorted(set(symbols)):
        frame = histories.get(yahoo_nse_symbol(symbol))
        if frame is None: frame = histories.get(symbol)
        if frame is None or "Close" not in frame.columns: continue
        returns[symbol] = pd.to_numeric(frame["Close"], errors="coerce").pct_change().tail(CORRELATION_WINDOW)
    if len(returns) < 2:
        return {"status": NOT_AVAILABLE, "reason": "FEWER_THAN_TWO_POSITIONS", "window_sessions": CORRELATION_WINDOW}
    correlations = pd.DataFrame(returns).corr(min_periods=CORRELATION_MIN_OBSERVATIONS)
    values = [correlations.iloc[i, j] for i in range(len(correlations)) for j in range(i + 1, len(correlations))
              if math.isfinite(correlations.iloc[i, j])]
    if not values:
        return {"status": NOT_AVAILABLE, "reason": "INSUFFICIENT_OVERLAP", "window_sessions": CORRELATION_WINDOW}
    return {"status": "AVAILABLE", "window_sessions": CORRELATION_WINDOW,
            "minimum_observations": CORRELATION_MIN_OBSERVATIONS,
            "average_pairwise_correlation": float(np.mean(values)),
            "median_pairwise_correlation": float(np.median(values)),
            "maximum_pairwise_correlation": float(np.max(values)), "pair_count": len(values)}


def persist_risk_snapshot(account_id: str, market_date: dt.date, histories, decisions) -> None:
    """Best-effort post-commit telemetry. Failure must not roll back trading state."""
    session = database.SessionLocal()
    try:
        positions = session.query(database.AutoPaperPosition).filter_by(account_id=account_id, status="OPEN").all()
        metadata = {x.opportunity_id: x for x in session.query(database.AutoPaperOpportunityMetadata).all()}
        qualified_sectors = [metadata.get(str(x.get("opportunity_id"))).sector if metadata.get(str(x.get("opportunity_id"))) else NOT_AVAILABLE for x in decisions]
        entered = [x for x in positions if x.entry_date == market_date]
        entered_sectors = [metadata.get(x.opportunity_id).sector if metadata.get(x.opportunity_id) else NOT_AVAILABLE for x in entered]
        entered_values = [x.quantity * x.entry_price for x in entered]
        all_sectors = [metadata.get(x.opportunity_id).sector if metadata.get(x.opportunity_id) else NOT_AVAILABLE for x in positions]
        all_values = [x.quantity * x.current_mark for x in positions]
        date_values = Counter()
        for position, value in zip(positions, all_values):
            date_values[json.loads(position.payload).get("signal_date", NOT_AVAILABLE)] += value
        total_value = sum(all_values)
        payload = {
            "schema_version": TELEMETRY_VERSION, "market_date": market_date.isoformat(),
            "account_id": account_id, "decision_authority": False,
            "signal_concentration": sector_concentration(qualified_sectors),
            "entered_sector_concentration": sector_concentration(entered_sectors),
            "new_capital_sector_concentration": sector_concentration(entered_sectors, entered_values),
            "portfolio_sector_concentration": sector_concentration(all_sectors, all_values),
            "largest_signal_date_exposure": max(date_values.values()) / total_value if total_value and date_values else None,
            "aggregate_new_capital": sum(entered_values),
            "qualified_opportunity_count": len(decisions), "entered_opportunity_count": len(entered),
            "portfolio_correlation": correlation_metrics(histories, [x.symbol for x in positions]),
            "same_date_entrant_correlation": correlation_metrics(histories, [x.symbol for x in entered]),
            "benchmark_beta": NOT_AVAILABLE, "size_liquidity_concentration": NOT_AVAILABLE,
        }
        telemetry_id = digest([TELEMETRY_VERSION, account_id, market_date.isoformat()])
        if session.get(database.AutoPaperRiskTelemetry, telemetry_id) is None:
            encoded = _safe_json(payload)
            session.add(database.AutoPaperRiskTelemetry(telemetry_id=telemetry_id, account_id=account_id,
                market_date=market_date, schema_version=TELEMETRY_VERSION, payload=encoded,
                payload_hash=digest(payload)))
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

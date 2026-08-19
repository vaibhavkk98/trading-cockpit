"""Descriptive production Market Context and lightweight risk overlays.

The module has no trading hooks.  Builders are deterministic; network fetches
run only from explicit EOD/pre-open/intraday refresh entry points.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests
import yfinance as yf


STRUCTURAL_VERSION = "MARKET_CONTEXT_STRUCTURAL_V1"
INVESTOR_VERSION = "INVESTOR_PARTICIPATION_V1"
CROSS_ASSET_VERSION = "CROSS_ASSET_STRESS_V1"
EVENT_VERSION = "EVENT_RISK_V1"
NOT_AVAILABLE = "NOT_AVAILABLE"

SECTOR_TICKERS = {
    "Auto": "^CNXAUTO", "Bank": "^NSEBANK", "FMCG": "^CNXFMCG",
    "IT": "^CNXIT", "Media": "^CNXMEDIA", "Metal": "^CNXMETAL",
    "Pharma": "^CNXPHARMA", "Realty": "^CNXREALTY",
    "PSU Bank": "^CNXPSUBANK", "Energy": "^CNXENERGY",
}

CROSS_ASSETS = {
    "usdinr": {"ticker": "INR=X", "label": "USD/INR", "stress_direction": 1},
    "brent": {"ticker": "BZ=F", "label": "Brent crude", "stress_direction": 1},
    "us_10y": {"ticker": "^TNX", "label": "US 10Y yield", "stress_direction": 1},
    "dxy": {"ticker": "DX-Y.NYB", "label": "US Dollar Index", "stress_direction": 1},
    "global_equity": {"ticker": "^GSPC", "label": "S&P 500", "stress_direction": -1},
}

EVENT_SOURCES = (
    {"name": "Press Information Bureau India", "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1", "confidence": "HIGH", "tier": "OFFICIAL_INDIA"},
    {"name": "Economic Times Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "confidence": "MODERATE", "tier": "REPUTABLE_INDIAN_NEWS"},
    {"name": "Business Standard Markets", "url": "https://www.business-standard.com/rss/markets-106.rss", "confidence": "MODERATE", "tier": "REPUTABLE_INDIAN_NEWS"},
)

MATERIAL_EVENT_WORDS = {
    "GEOPOLITICAL": ("war", "military escalation", "sanction", "missile", "border conflict"),
    "COMMODITY_ENERGY": ("crude shock", "oil shock", "oil supply", "refinery outage", "opec"),
    "INDIA_POLICY": ("rbi emergency", "sebi order", "government action", "capital control", "union budget"),
    "FINANCIAL_SYSTEM": ("bank failure", "systemic stress", "liquidity crisis", "default crisis"),
    "TRADE_POLICY": ("tariff", "trade restriction", "export ban", "import ban"),
    "GLOBAL_MARKET_SHOCK": ("market crash", "global selloff", "volatility shock", "emergency rate"),
}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _cut_history(frame: pd.DataFrame | None, as_of_date: dt.date | str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    cutoff = pd.Timestamp(as_of_date)
    index = pd.to_datetime(result.index).tz_localize(None) if getattr(pd.to_datetime(result.index), "tz", None) else pd.to_datetime(result.index)
    result.index = index
    return result[result.index <= cutoff].sort_index()


def _percentile(series: pd.Series, value: float, minimum: int = 60) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna().tail(252)
    if len(clean) < minimum:
        return None
    return round(float((clean <= value).mean() * 100), 2)


def fetch_sector_histories(period: str = "2y") -> dict[str, pd.DataFrame]:
    """One bounded bulk fetch used by the explicit EOD refresh only."""
    tickers = list(SECTOR_TICKERS.values())
    raw = yf.download(tickers, period=period, interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=True)
    result: dict[str, pd.DataFrame] = {}
    for label, ticker in SECTOR_TICKERS.items():
        try:
            frame = raw[ticker].dropna(how="all") if isinstance(raw.columns, pd.MultiIndex) else raw
            if not frame.empty:
                result[label] = frame
        except (KeyError, TypeError):
            continue
    return result


def build_structural_context(
    stock_histories: dict[str, pd.DataFrame],
    nifty500: pd.DataFrame | None,
    india_vix: pd.DataFrame | None,
    sector_histories: dict[str, pd.DataFrame],
    as_of_date: dt.date | str,
) -> dict[str, Any]:
    """Build separate causal pillars; intentionally produces no composite score."""
    cutoff = dt.date.fromisoformat(str(as_of_date)[:10])
    n500 = _cut_history(nifty500, cutoff)
    vix = _cut_history(india_vix, cutoff)
    missing: list[str] = []

    trend: dict[str, Any] = {"state": NOT_AVAILABLE}
    volatility: dict[str, Any] = {"state": NOT_AVAILABLE}
    if len(n500) >= 60 and "Close" in n500:
        close = pd.to_numeric(n500["Close"], errors="coerce").dropna()
        latest = _float(close.iloc[-1])
        ret20 = _float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
        ema50 = _float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        extension = _float((latest / ema50 - 1) * 100) if latest and ema50 else None
        drawdown = _float((latest / close.tail(60).max() - 1) * 100) if latest else None
        if ret20 is not None and extension is not None and drawdown is not None:
            state = "SUPPORTIVE" if ret20 > 2 and extension > 0 else "DEFENSIVE" if ret20 < -3 or drawdown < -8 else "WEAKENING" if ret20 < 0 or extension < 0 else "NEUTRAL"
            trend = {"state": state, "nifty500_ret_20d_pct": round(ret20, 2), "ema50_extension_pct": round(extension, 2), "drawdown_60d_pct": round(drawdown, 2)}
        returns = close.pct_change(fill_method=None)
        realized = returns.rolling(20).std() * np.sqrt(252) * 100
        realized_latest = _float(realized.iloc[-1])
        realized_pctile = _percentile(realized, realized_latest) if realized_latest is not None else None
        vix_close = pd.to_numeric(vix.get("Close"), errors="coerce").dropna() if not vix.empty and "Close" in vix else pd.Series(dtype=float)
        vix_latest = _float(vix_close.iloc[-1]) if len(vix_close) else None
        vix_change = _float((vix_close.iloc[-1] / vix_close.iloc[-6] - 1) * 100) if len(vix_close) >= 6 else None
        vix_pctile = _percentile(vix_close, vix_latest) if vix_latest is not None else None
        available_percentiles = [x for x in (realized_pctile, vix_pctile) if x is not None]
        if vix_latest is not None and realized_latest is not None and available_percentiles:
            regime_value = float(np.mean(available_percentiles))
            regime = "LOW" if regime_value < 35 else "NORMAL" if regime_value < 60 else "ELEVATED" if regime_value < 80 else "HIGH"
            volatility = {"state": regime, "india_vix_level": round(vix_latest, 2), "india_vix_change_5d_pct": round(vix_change, 2) if vix_change is not None else None, "india_vix_percentile": vix_pctile, "nifty500_realized_vol_20d_ann_pct": round(realized_latest, 2), "realized_vol_percentile": realized_pctile}
    if trend["state"] == NOT_AVAILABLE:
        missing.append("trend")
    if volatility["state"] == NOT_AVAILABLE:
        missing.append("volatility")

    breadth_rows = []
    for symbol, raw in stock_histories.items():
        frame = _cut_history(raw, cutoff)
        if len(frame) < 51 or "Close" not in frame:
            continue
        close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        if len(close) < 51:
            continue
        breadth_rows.append({
            "symbol": symbol, "above20": close.iloc[-1] > close.ewm(span=20, adjust=False).mean().iloc[-1],
            "above50": close.iloc[-1] > close.ewm(span=50, adjust=False).mean().iloc[-1],
            "advance": close.iloc[-1] > close.iloc[-2], "new_high": close.iloc[-1] > close.iloc[-21:-1].max(),
            "new_low": close.iloc[-1] < close.iloc[-21:-1].min(), "ret10": (close.iloc[-1] / close.iloc[-11] - 1) * 100,
        })
    breadth: dict[str, Any] = {"state": NOT_AVAILABLE, "coverage": len(breadth_rows)}
    if breadth_rows:
        frame = pd.DataFrame(breadth_rows)
        above20 = float(frame.above20.mean() * 100); above50 = float(frame.above50.mean() * 100); advance = float(frame.advance.mean() * 100)
        state = "SUPPORTIVE" if above20 >= 60 and advance >= 55 else "WEAK" if above20 < 35 or advance < 40 else "MIXED" if above20 < 50 else "NEUTRAL"
        breadth = {"state": state, "coverage": len(frame), "above_ema20_pct": round(above20, 2), "above_ema50_pct": round(above50, 2), "advance_participation_pct": round(advance, 2), "new_high_20d_pct": round(float(frame.new_high.mean() * 100), 2), "new_low_20d_pct": round(float(frame.new_low.mean() * 100), 2), "median_return_10d_pct": round(float(frame.ret10.median()), 2)}
    else:
        missing.append("breadth")

    sector_rows = []
    for label, raw in sector_histories.items():
        frame = _cut_history(raw, cutoff)
        if len(frame) < 21 or "Close" not in frame:
            continue
        close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        if len(close) >= 21:
            ret10 = float((close.iloc[-1] / close.iloc[-11] - 1) * 100)
            sector_rows.append({"sector": label, "above20": close.iloc[-1] > close.ewm(span=20, adjust=False).mean().iloc[-1], "positive10": ret10 > 0, "ret10": ret10})
    sectors: dict[str, Any] = {"state": NOT_AVAILABLE, "coverage": len(sector_rows)}
    if sector_rows:
        frame = pd.DataFrame(sector_rows)
        above20 = float(frame.above20.mean() * 100); positive = float(frame.positive10.mean() * 100)
        state = "BROAD" if above20 >= 65 and positive >= 60 else "WEAK" if above20 < 35 else "MIXED"
        sectors = {"state": state, "coverage": len(frame), "above_ema20_pct": round(above20, 2), "positive_10d_pct": round(positive, 2), "dispersion_10d_pct": round(float(frame.ret10.std(ddof=0)), 2), "strongest_10d_pct": round(float(frame.ret10.max()), 2), "weakest_10d_pct": round(float(frame.ret10.min()), 2)}
    else:
        missing.append("sector_participation")

    return _json_safe({
        "as_of_date": cutoff.isoformat(), "as_of_timestamp": dt.datetime.combine(cutoff, dt.time(16, 7), tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(),
        "methodology_version": STRUCTURAL_VERSION, "trend": trend, "breadth": breadth,
        "volatility": volatility, "sector_participation": sectors,
        "coverage": {"stock_histories": len(breadth_rows), "sector_indices": len(sector_rows)},
        "missingness": missing, "provenance": ["Yahoo Finance completed-session NSE index/equity histories"],
        "advisory_only": True,
    })


def map_participant_category(category: str) -> str:
    text = str(category or "").strip().upper()
    if text in {"FII", "FPI", "FII/FPI"}:
        return "FII_FPI"
    if text == "DII":
        return "DII"
    if text == "CLIENT":
        return "CLIENT_NOT_RETAIL"
    return "UNMAPPED"


def fetch_investor_participation(timeout: int = 12, history: Iterable[dict[str, Any]] = (), session_factory: Callable[[], Any] = requests.Session) -> dict[str, Any]:
    """Current official NSE cash activity; historical fields grow from activation."""
    now = dt.datetime.now(dt.timezone.utc)
    headers = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "application/json,text/plain,*/*", "Referer": "https://www.nseindia.com/reports/fii-dii"}
    session = session_factory(); session.headers.update(headers)
    try:
        session.get("https://www.nseindia.com", timeout=timeout)
        response = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=timeout)
        response.raise_for_status(); rows = response.json()
    except Exception as exc:
        return {"as_of_timestamp": now.isoformat(), "methodology_version": INVESTOR_VERSION, "state": NOT_AVAILABLE, "coverage": "NOT_AVAILABLE", "missingness": ["fii_fpi_cash", "dii_cash"], "failure_reason": type(exc).__name__, "provenance": ["NSE FII/FPI & DII trading activity endpoint"], "advisory_only": True}
    mapped = {map_participant_category(row.get("category")): row for row in rows}
    fii = mapped.get("FII_FPI"); dii = mapped.get("DII")
    if not fii or not dii:
        return {"as_of_timestamp": now.isoformat(), "methodology_version": INVESTOR_VERSION, "state": NOT_AVAILABLE, "coverage": "PARTIAL", "missingness": ["fii_fpi_cash" if not fii else "dii_cash"], "provenance": ["NSE FII/FPI & DII trading activity endpoint"], "advisory_only": True}
    observation_date = dt.datetime.strptime(str(fii["date"]), "%d-%b-%Y").date()
    fii_net = float(fii["netValue"]); dii_net = float(dii["netValue"]); combined = fii_net + dii_net
    prior = [row for row in history if row.get("observation_date") and row.get("state") != NOT_AVAILABLE]
    series_fii = [float(row["fii_net_today_cr"]) for row in reversed(prior) if row.get("fii_net_today_cr") is not None] + [fii_net]
    series_dii = [float(row["dii_net_today_cr"]) for row in reversed(prior) if row.get("dii_net_today_cr") is not None] + [dii_net]
    def cumulative(values: list[float], length: int) -> float | None:
        return round(sum(values[-length:]), 2) if len(values) >= length else None
    intensity = None
    if len(series_fii) >= 10:
        base = np.asarray(series_fii[-20:-1] or series_fii[:-1], dtype=float)
        median = float(np.median(base)); mad = float(np.median(np.abs(base - median)))
        intensity = round((fii_net - median) / (1.4826 * mad), 2) if mad > 0 else None
    absorption = bool(fii_net < 0 < dii_net and dii_net >= abs(fii_net))
    state = "HIGH SELLING PRESSURE" if fii_net < -3000 and combined < 0 else "SUPPORTIVE" if fii_net >= 0 and combined > 0 else "MIXED" if absorption or combined >= 0 else "DEFENSIVE"
    return {
        "observation_date": observation_date.isoformat(), "as_of_timestamp": now.isoformat(), "methodology_version": INVESTOR_VERSION,
        "state": state, "fii_buy_today_cr": float(fii["buyValue"]), "fii_sell_today_cr": float(fii["sellValue"]), "fii_net_today_cr": fii_net,
        "dii_buy_today_cr": float(dii["buyValue"]), "dii_sell_today_cr": float(dii["sellValue"]), "dii_net_today_cr": dii_net,
        "fii_net_5d_cr": cumulative(series_fii, 5), "fii_net_20d_cr": cumulative(series_fii, 20),
        "dii_net_5d_cr": cumulative(series_dii, 5), "dii_net_20d_cr": cumulative(series_dii, 20),
        "fii_flow_robust_z": intensity, "institutional_absorption": absorption,
        "futures_positioning": NOT_AVAILABLE, "retail_participation": NOT_AVAILABLE,
        "provisional_status": "PROVISIONAL", "coverage": "CURRENT_ONLY" if len(series_fii) < 5 else "RECENT_HISTORY",
        "historical_calibration": NOT_AVAILABLE if len(series_fii) < 20 else "AVAILABLE",
        "missingness": [name for name, value in {"fii_net_5d_cr": cumulative(series_fii, 5), "fii_net_20d_cr": cumulative(series_fii, 20), "futures_positioning": None, "retail_participation": None}.items() if value is None],
        "provenance": [{"source": "NSE India", "url": "https://www.nseindia.com/reports/fii-dii", "source_timestamp": observation_date.isoformat()}], "advisory_only": True,
    }


def fetch_cross_asset_snapshot(fetcher: Callable[..., pd.DataFrame] | None = None) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc); fetcher = fetcher or yf.download
    tickers = [item["ticker"] for item in CROSS_ASSETS.values()]
    try:
        raw = fetcher(tickers, period="1y", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=True)
    except Exception as exc:
        raw = pd.DataFrame(); failure = type(exc).__name__
    else:
        failure = None
    series_payload: dict[str, Any] = {}; components = []
    for key, spec in CROSS_ASSETS.items():
        try:
            frame = raw[spec["ticker"]] if isinstance(raw.columns, pd.MultiIndex) else raw
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
            if len(close) < 60:
                raise ValueError("insufficient_history")
            latest = float(close.iloc[-1]); move1 = float((latest / close.iloc[-2] - 1) * 100); move5_series = close.pct_change(5, fill_method=None) * 100
            move5 = float(move5_series.iloc[-1]); directed = move5_series * spec["stress_direction"]
            percentile = _percentile(directed, move5 * spec["stress_direction"])
            series_payload[key] = {"status": "AVAILABLE", "label": spec["label"], "latest_value": round(latest, 4), "move_1d_pct": round(move1, 2), "move_5d_pct": round(move5, 2), "stress_percentile": percentile, "stress_direction": "RISING_IS_STRESS" if spec["stress_direction"] > 0 else "FALLING_IS_STRESS", "source_timestamp": pd.Timestamp(close.index[-1]).isoformat(), "provenance": f"Yahoo Finance {spec['ticker']}"}
            if percentile is not None:
                components.append(percentile)
        except (KeyError, TypeError, ValueError, IndexError):
            series_payload[key] = {"status": NOT_AVAILABLE, "label": spec["label"], "latest_value": None, "move_1d_pct": None, "move_5d_pct": None, "stress_percentile": None, "stress_direction": "RISING_IS_STRESS" if spec["stress_direction"] > 0 else "FALLING_IS_STRESS", "provenance": f"Yahoo Finance {spec['ticker']}"}
    if len(components) >= 3:
        aggregate = round(float(np.mean(components)), 2)
        state = "CALM" if aggregate < 35 else "NORMAL" if aggregate < 55 else "ELEVATED" if aggregate < 75 else "HIGH STRESS"
    else:
        aggregate = None; state = NOT_AVAILABLE
    return {"as_of_timestamp": now.isoformat(), "methodology_version": CROSS_ASSET_VERSION, "state": state, "aggregate_stress_percentile": aggregate, "available_core_inputs": len(components), "required_core_inputs": 3, "series": series_payload, "missingness": [k for k, v in series_payload.items() if v["status"] == NOT_AVAILABLE], "failure_reason": failure, "provenance": ["Yahoo Finance delayed public market data"], "advisory_only": True}


def _feed_items(xml_data: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_data); items = []
    for item in root.findall(".//item"):
        items.append({"title": (item.findtext("title") or "").strip(), "description": re.sub("<[^>]+>", " ", item.findtext("description") or "").strip(), "url": (item.findtext("link") or "").strip(), "published": (item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()})
    return items


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    """Match whole words/phrases so 'award' and 'outward' are not 'war'."""
    for phrase in phrases:
        pattern = re.escape(phrase).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){pattern}(?!\w)", text):
            return True
    return False


def classify_material_event(item: dict[str, str], source: dict[str, str], now: dt.datetime | None = None) -> dict[str, Any] | None:
    now = now or dt.datetime.now(dt.timezone.utc); text = f"{item.get('title','')} {item.get('description','')}".lower()
    if _contains_any(text, ("stock pick", "shares to buy", "ipo", "earnings", "quarterly result", "target price", "gmp")):
        return None
    event_type = next((category for category, words in MATERIAL_EVENT_WORDS.items() if _contains_any(text, words)), None)
    if not event_type:
        return None
    direct_india = _contains_any(text, ("india", "indian", "rbi", "sebi", "rupee", "nifty", "sensex"))
    transmission = _contains_any(text, ("crude", "oil", "fed", "tariff", "war", "sanction", "dollar", "global market"))
    relevance = "HIGH" if direct_india else "MEDIUM" if transmission else "LOW"
    severe = _contains_any(text, ("systemic crisis", "market crash", "major war escalation", "emergency capital control"))
    high = _contains_any(text, ("war", "sanction", "oil shock", "crude shock", "emergency rate", "tariff"))
    magnitude = "SEVERE" if severe else "HIGH" if high else "MEDIUM"
    if relevance == "LOW" or (relevance == "MEDIUM" and magnitude == "MEDIUM"):
        return None
    try:
        published = parsedate_to_datetime(item.get("published") or "").astimezone(dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    age_hours = max(0.0, (now - published).total_seconds() / 3600)
    if age_hours > 72:
        return None
    negative = _contains_any(text, ("war", "sanction", "crisis", "shock", "crash", "tariff", "emergency"))
    positive = _contains_any(text, ("ceasefire", "relief", "cut tariff", "stabilise"))
    direction = "MIXED" if negative and positive else "NEGATIVE" if negative else "POSITIVE" if positive else "UNCLEAR"
    affected = [sector for sector, words in {"Energy": ("oil", "crude", "gas"), "Financials": ("bank", "rate", "liquidity"), "IT": ("technology", "tariff", "dollar"), "Exporters": ("trade", "tariff", "export")}.items() if _contains_any(text, words)]
    identity = hashlib.sha256(f"{source['name']}|{item.get('url')}|{item.get('title')}".encode()).hexdigest()[:24]
    return {"event_id": f"EV_{identity}", "event_timestamp": published.isoformat(), "headline": item.get("title"), "short_description": item.get("description", "")[:500], "event_type": event_type, "scheduled": False, "india_relevance": relevance, "magnitude": magnitude, "direction": direction, "freshness_hours": round(age_hours, 1), "affected_scope": "SECTORS" if affected else "MARKET", "affected_sectors": affected, "source_confidence": source["confidence"], "provenance": [{"source": source["name"], "tier": source["tier"], "url": item.get("url") or source["url"]}], "status": "ACTIVE", "methodology_version": EVENT_VERSION, "advisory_only": True}


def load_scheduled_events(now: dt.datetime | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    now = now or dt.datetime.now(dt.timezone.utc); path = path or Path(__file__).resolve().parent / "data" / "market_context" / "scheduled_events.json"
    try:
        configured = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    events = []
    for row in configured:
        event_time = dt.datetime.fromisoformat(row["event_timestamp"].replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        seconds = (event_time - now).total_seconds()
        if -86400 <= seconds <= 45 * 86400:
            events.append({**row, "event_id": row.get("event_id") or f"SCHED_{hashlib.sha256(row['event_timestamp'].encode()).hexdigest()[:20]}", "scheduled": True, "time_until_event_seconds": round(seconds), "freshness_hours": 0, "status": "UPCOMING" if seconds >= 0 else "RECENT", "methodology_version": EVENT_VERSION, "advisory_only": True})
    return events


def fetch_event_risk_snapshot(timeout: int = 12, now: dt.datetime | None = None, get: Callable[..., Any] | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc); get = get or requests.get
    diagnostics = []; material = []
    for source in EVENT_SOURCES:
        try:
            response = get(source["url"], timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}); response.raise_for_status()
            items = _feed_items(response.content); diagnostics.append({"source": source["name"], "status": "SUCCESS", "items": len(items), "checked_at": now.isoformat()})
            for item in items:
                event = classify_material_event(item, source, now)
                if event:
                    material.append(event)
        except Exception as exc:
            diagnostics.append({"source": source["name"], "status": "FAILED", "items": 0, "failure_reason": type(exc).__name__, "checked_at": now.isoformat()})
    scheduled = load_scheduled_events(now)
    unique = {event["event_id"]: event for event in material}
    events = list(unique.values()) + scheduled
    active = [e for e in events if not e.get("scheduled") and e.get("status") == "ACTIVE"]
    if any(e.get("magnitude") == "SEVERE" for e in active): state = "SEVERE"
    elif any(e.get("magnitude") == "HIGH" and e.get("india_relevance") == "HIGH" for e in active): state = "HIGH"
    elif active or any(0 <= e.get("time_until_event_seconds", 10**12) <= 48 * 3600 for e in scheduled): state = "ELEVATED"
    elif any(d["status"] == "SUCCESS" for d in diagnostics): state = "LOW"
    else: state = NOT_AVAILABLE
    return {"as_of_timestamp": now.isoformat(), "methodology_version": EVENT_VERSION, "state": state, "events": sorted(events, key=lambda e: (not e.get("scheduled"), e.get("event_timestamp", ""))), "active_material_events": len(active), "scheduled_events": len(scheduled), "source_diagnostics": diagnostics, "coverage": "AVAILABLE" if any(d["status"] == "SUCCESS" for d in diagnostics) else NOT_AVAILABLE, "missingness": [] if any(d["status"] == "SUCCESS" for d in diagnostics) else ["unscheduled_event_sources"], "advisory_only": True}


def refresh_market_context(*, phase: str, structural: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explicit refresh orchestrator.  Never called by a UI renderer."""
    import database
    phase = phase.upper(); result: dict[str, Any] = {"phase": phase}
    if phase == "EOD":
        if structural and structural.get("methodology_version") == STRUCTURAL_VERSION:
            result["structural"] = database.persist_market_context_snapshot(structural)
        history = database.load_investor_participation_snapshots(limit=20)
        investor = fetch_investor_participation(history=history)
        result["investor"] = database.persist_investor_participation_snapshot(investor)
    if phase in {"PREOPEN", "INTRADAY"}:
        cross = fetch_cross_asset_snapshot(); events = fetch_event_risk_snapshot()
        result["cross_asset"] = database.persist_cross_asset_snapshot(cross, phase)
        result["events"] = database.persist_event_risk_snapshot(events, phase)
    return result


def summarize_context(bundle: dict[str, Any]) -> str:
    """Deterministic sentence composed only from already-persisted states."""
    structural = bundle.get("structural") or {}; investor = bundle.get("investor_participation") or {}; cross = bundle.get("cross_asset") or {}; event = bundle.get("event_risk") or {}
    parts = []
    if investor.get("state") not in {None, NOT_AVAILABLE, "MIXED"}: parts.append(f"institutional participation is {str(investor['state']).lower()}")
    if cross.get("state") not in {None, NOT_AVAILABLE, "NORMAL", "CALM"}: parts.append(f"cross-asset pressure is {str(cross['state']).lower()}")
    breadth = (structural.get("breadth") or {}).get("state")
    if breadth not in {None, NOT_AVAILABLE, "NEUTRAL"}: parts.append(f"domestic breadth is {str(breadth).lower()}")
    if event.get("state") in {"ELEVATED", "HIGH", "SEVERE"}: parts.append(f"external event risk is {str(event['state']).lower()}")
    return ("; ".join(parts).capitalize() + ".") if parts else "Available indicators do not show a dominant market-context pressure."

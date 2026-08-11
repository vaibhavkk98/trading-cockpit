"""
SENTIMENT ENGINE V1 INTERFACE

Provides standardized, historically safe sentiment evaluation interfaces for
market-level and symbol-level signals.

Integrates with config/sentiment_config.yaml with sentiment_enabled defaulting to FALSE.
Enforces HISTORICAL SAFETY: Never fabricates synthetic historical sentiment.
If timestamped real historical sentiment data is absent for requested as_of_date,
returns sentiment_regime="UNAVAILABLE" and sentiment_score=None.
"""

import os
import pandas as pd
from typing import Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "sentiment_config.yaml")
LOG_PATH = os.path.join(PROJECT_ROOT, "data", "sentiment", "sentiment_log.csv")

# STANDARD REGIMES & EVIDENCE STATUSES
REGIME_BULLISH = "BULLISH"
REGIME_NEUTRAL = "NEUTRAL"
REGIME_BEARISH = "BEARISH"
REGIME_UNAVAILABLE = "UNAVAILABLE"

EVIDENCE_AVAILABLE = "AVAILABLE"
EVIDENCE_UNAVAILABLE = "UNAVAILABLE"
EVIDENCE_INSUFFICIENT = "INSUFFICIENT_DATA"


@dataclass
class SentimentResult:
    symbol: str
    as_of_date: str
    sentiment_score: Optional[float]  # -1.0 to +1.0 or None
    sentiment_regime: str             # BULLISH, NEUTRAL, BEARISH, UNAVAILABLE
    confidence: Optional[float]       # 0.0 to 1.0 or None
    source_count: int
    data_timestamp: Optional[str]
    evidence_status: str              # AVAILABLE, UNAVAILABLE, INSUFFICIENT_DATA
    source_type: str                  # e.g., "PLACEHOLDER", "DISABLED", "NONE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_sentiment_config() -> Dict[str, Any]:
    """Loads config from config/sentiment_config.yaml safely without external dependencies."""
    if not os.path.exists(CONFIG_PATH):
        return {"sentiment_enabled": False}

    try:
        import yaml
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        pass

    # Lightweight zero-dependency fallback parser for basic key: value YAML
    config = {"sentiment_enabled": False}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str or line_str.startswith("#"):
                    continue
                if ":" in line_str:
                    parts = line_str.split(":", 1)
                    key = parts[0].strip()
                    val_str = parts[1].strip().strip('"').strip("'")
                    if val_str.lower() in ("true", "yes", "on"):
                        val = True
                    elif val_str.lower() in ("false", "no", "off"):
                        val = False
                    elif val_str.isdigit():
                        val = int(val_str)
                    else:
                        val = val_str
                    config[key] = val
    except Exception:
        pass

    return config


def is_sentiment_enabled() -> bool:
    """Returns boolean indicating if sentiment engine is active."""
    cfg = load_sentiment_config()
    return bool(cfg.get("sentiment_enabled", False))


def _log_sentiment_evaluation(result: SentimentResult) -> None:
    """Logs evaluation to data/sentiment/sentiment_log.csv if logging is enabled."""
    cfg = load_sentiment_config()
    logging_enabled = cfg.get("logging.enabled", cfg.get("enabled", True))
    if not logging_enabled:
        return

    rel_log = cfg.get("log_file", "data/sentiment/sentiment_log.csv")
    log_file = os.path.join(PROJECT_ROOT, rel_log) if not os.path.isabs(rel_log) else rel_log
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    row = {
        "symbol": result.symbol,
        "as_of_date": result.as_of_date,
        "sentiment_score": "" if result.sentiment_score is None else f"{result.sentiment_score:.4f}",
        "sentiment_regime": result.sentiment_regime,
        "confidence": "" if result.confidence is None else f"{result.confidence:.4f}",
        "source_count": result.source_count,
        "data_timestamp": result.data_timestamp or "",
        "evidence_status": result.evidence_status,
        "source_type": result.source_type,
        "created_at": datetime.now().isoformat()
    }

    file_exists = os.path.exists(log_file)
    df_row = pd.DataFrame([row])
    df_row.to_csv(log_file, mode="a" if file_exists else "w", header=not file_exists, index=False)


def create_unavailable_result(symbol: str, as_of_date: str, source_type: str = "NONE") -> SentimentResult:
    """Helper to construct standard UNAVAILABLE sentiment result."""
    return SentimentResult(
        symbol=symbol,
        as_of_date=as_of_date,
        sentiment_score=None,
        sentiment_regime=REGIME_UNAVAILABLE,
        confidence=None,
        source_count=0,
        data_timestamp=None,
        evidence_status=EVIDENCE_UNAVAILABLE,
        source_type=source_type
    )


def get_market_sentiment(as_of_date: Optional[str] = None) -> SentimentResult:
    """
    Evaluates market-level sentiment as of requested date.
    Returns UNAVAILABLE if sentiment_enabled=false or real historical data absent.
    """
    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    if not is_sentiment_enabled():
        res = create_unavailable_result("NIFTY50", as_of_date, source_type="DISABLED")
        _log_sentiment_evaluation(res)
        return res

    # Deterministic placeholder implementation returning UNAVAILABLE when real evidence is absent
    res = create_unavailable_result("NIFTY50", as_of_date, source_type="NO_HISTORICAL_DATA")
    _log_sentiment_evaluation(res)
    return res


def get_symbol_sentiment(symbol: str, as_of_date: Optional[str] = None) -> SentimentResult:
    """
    Evaluates symbol-level sentiment as of requested date.
    Returns UNAVAILABLE if sentiment_enabled=false or real historical data absent.
    """
    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    if not is_sentiment_enabled():
        res = create_unavailable_result(symbol, as_of_date, source_type="DISABLED")
        _log_sentiment_evaluation(res)
        return res

    # Deterministic placeholder implementation returning UNAVAILABLE when real evidence is absent
    res = create_unavailable_result(symbol, as_of_date, source_type="NO_HISTORICAL_DATA")
    _log_sentiment_evaluation(res)
    return res


def get_sentiment_features(symbol: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns feature dictionary format suitable for future model integration.
    If unavailable or disabled, returns standardized null feature set.
    """
    sym_res = get_symbol_sentiment(symbol, as_of_date)
    mkt_res = get_market_sentiment(as_of_date)

    return {
        "symbol": symbol,
        "as_of_date": sym_res.as_of_date,
        "sentiment_enabled": is_sentiment_enabled(),
        "symbol_sentiment_score": sym_res.sentiment_score,
        "symbol_sentiment_regime": sym_res.sentiment_regime,
        "symbol_confidence": sym_res.confidence,
        "symbol_evidence_status": sym_res.evidence_status,
        "market_sentiment_score": mkt_res.sentiment_score,
        "market_sentiment_regime": mkt_res.sentiment_regime,
        "market_confidence": mkt_res.confidence,
        "market_evidence_status": mkt_res.evidence_status
    }

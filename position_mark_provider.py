"""Small-portfolio Yahoo mark fetcher shared by manual and automated triggers."""

import datetime as dt
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, Optional

import pandas as pd
import yfinance as yf

from provider_symbols import yahoo_nse_symbol


def _symbol_frame(downloaded: pd.DataFrame, provider_symbol: str, symbol_count: int) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    if isinstance(downloaded.columns, pd.MultiIndex):
        level_zero = downloaded.columns.get_level_values(0)
        level_one = downloaded.columns.get_level_values(1)
        if provider_symbol in level_zero:
            return downloaded[provider_symbol].copy()
        if provider_symbol in level_one:
            return downloaded.xs(provider_symbol, axis=1, level=1).copy()
        return pd.DataFrame()
    return downloaded.copy() if symbol_count == 1 else pd.DataFrame()


def _latest_close(frame: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if frame.empty or "Close" not in frame:
        return None
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if closes.empty:
        return None
    timestamp = pd.Timestamp(closes.index[-1])
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Kolkata")
    return {"mark_price": float(closes.iloc[-1]), "mark_date": timestamp.date()}


def fetch_latest_yahoo_marks(
    canonical_symbols: Iterable[str],
    download_fn: Callable[..., pd.DataFrame] = yf.download,
    ticker_factory: Callable[..., Any] = yf.Ticker,
) -> Dict[str, Any]:
    """Batch open symbols once, then fall back only for missing batch results."""
    started = perf_counter()
    symbols = list(dict.fromkeys(str(symbol) for symbol in canonical_symbols if symbol))
    provider_map = {symbol: yahoo_nse_symbol(symbol) for symbol in symbols}
    provider_symbols = list(dict.fromkeys(provider_map.values()))
    marks: Dict[str, Dict[str, Any]] = {}
    provider_calls = 0
    if provider_symbols:
        try:
            provider_calls += 1
            downloaded = download_fn(
                provider_symbols, period="5d", interval="1d", group_by="ticker",
                threads=True, progress=False, auto_adjust=True, timeout=10,
            )
            for canonical, provider_symbol in provider_map.items():
                extracted = _latest_close(_symbol_frame(downloaded, provider_symbol, len(provider_symbols)))
                if extracted:
                    marks[canonical] = {**extracted, "provider_symbol": provider_symbol}
        except Exception:
            pass
    for canonical, provider_symbol in provider_map.items():
        if canonical in marks:
            continue
        try:
            provider_calls += 1
            frame = ticker_factory(provider_symbol).history(period="5d", interval="1d", auto_adjust=True, timeout=10)
            extracted = _latest_close(frame)
            if extracted:
                marks[canonical] = {**extracted, "provider_symbol": provider_symbol}
        except Exception:
            continue
    return {
        "marks": marks,
        "failed_symbols": [symbol for symbol in symbols if symbol not in marks],
        "provider_calls": provider_calls,
        "unique_symbols": len(symbols),
        "elapsed_seconds": round(perf_counter() - started, 3),
    }

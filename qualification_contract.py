"""Authoritative, strategy-agnostic current-opportunity qualification gates."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


POSITIVE_PRICE_CHANGE_GATE_VERSION = "POSITIVE_CLOSE_TO_CLOSE_V1"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def positive_price_change_evidence(close: Any, previous_close: Any) -> dict[str, Any]:
    """Evaluate completed-session Close[T] > Close[T-1]; missing data fails closed."""
    current = _number(close)
    previous = _number(previous_close)
    valid = current is not None and previous is not None and previous > 0
    daily_return = ((current / previous) - 1.0) * 100.0 if valid else None
    return {
        "current_close": round(current, 6) if current is not None else None,
        "previous_close": round(previous, 6) if previous is not None else None,
        "daily_close_to_close_return_pct": round(daily_return, 6) if daily_return is not None else None,
        "positive_price_change_gate_pass": bool(valid and current > previous),
        "positive_price_change_gate_version": POSITIVE_PRICE_CHANGE_GATE_VERSION,
    }


def qualification_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    close = row.get("current_close", row.get("close", row.get("Close", row.get("entry_price"))))
    previous = row.get("previous_close", row.get("Previous_Close"))
    return positive_price_change_evidence(close, previous)


def passes_positive_price_change_gate(row: Mapping[str, Any]) -> bool:
    """Require reproducible close-to-close evidence, not a stored flag alone."""
    return qualification_evidence(row)["positive_price_change_gate_pass"]


def filter_current_qualified_decisions(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return only rows satisfying the current global gate without mutating inputs."""
    return [dict(row) for row in rows if passes_positive_price_change_gate(row)]

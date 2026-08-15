"""Opt-in lightweight server timing for production render diagnostics."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager


ENABLED = os.environ.get("TRADING_COCKPIT_TIMING", "1").strip().lower() not in {"0", "false", "no"}
LOGGER = logging.getLogger("trading_cockpit.performance")
if ENABLED:
    LOGGER.setLevel(logging.INFO)


def log_elapsed(operation: str, started: float, **dimensions) -> None:
    if ENABLED:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        suffix = " ".join(f"{key}={value}" for key, value in dimensions.items())
        LOGGER.warning("PERF operation=%s elapsed_ms=%.2f %s", operation, elapsed_ms, suffix)


def log_route(route: str, bootstrap_ms: float, page_ms: float, total_ms: float) -> None:
    if ENABLED:
        LOGGER.warning(
            "PERF route=%s bootstrap_ms=%.2f page_ms=%.2f total_ms=%.2f",
            route, bootstrap_ms, page_ms, total_ms,
        )


@contextmanager
def timed(operation: str, **dimensions):
    """Log elapsed backend time without retaining payloads or credentials."""
    started = time.perf_counter()
    try:
        yield
    finally:
        if ENABLED:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            suffix = " ".join(f"{key}={value}" for key, value in dimensions.items())
            LOGGER.warning("PERF operation=%s elapsed_ms=%.2f %s", operation, elapsed_ms, suffix)

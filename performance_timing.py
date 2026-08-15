"""Opt-in lightweight server timing for production render diagnostics."""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager


ENABLED = os.environ.get("TRADING_COCKPIT_TIMING", "").strip().lower() in {"1", "true", "yes"}
LOGGER = logging.getLogger("trading_cockpit.performance")
if ENABLED:
    LOGGER.setLevel(logging.INFO)


def log_elapsed(operation: str, started: float, **dimensions) -> None:
    if ENABLED:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        suffix = " ".join(f"{key}={value}" for key, value in dimensions.items())
        LOGGER.info("PERF operation=%s elapsed_ms=%.2f %s", operation, elapsed_ms, suffix)


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
            LOGGER.info("PERF operation=%s elapsed_ms=%.2f %s", operation, elapsed_ms, suffix)

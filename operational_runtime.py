"""F2 operational state, freshness, and low-volume event logging helpers."""
from datetime import datetime, timezone
import logging
from pathlib import Path

PRODUCT_VERSION = "TRADING_COCKPIT_V1"
SCAN_NOT_RUN = "NOT_RUN"
SCAN_RUNNING = "RUNNING"
SCAN_SUCCESS = "SUCCESS"
SCAN_PARTIAL = "PARTIAL_SUCCESS"
SCAN_FAILED = "FAILED"
SCAN_STALE = "STALE"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def initial_scan_state():
    return {"status": SCAN_NOT_RUN, "scan_started_at": None, "scan_completed_at": None,
            "analysis_date": None, "symbols_requested": 0, "symbols_succeeded": 0,
            "symbols_failed": 0, "qualified_count": 0, "allocated_count": 0, "error_summary": None}


def scan_freshness(state, today=None):
    if not state or state.get("status") in {SCAN_NOT_RUN, SCAN_RUNNING, SCAN_FAILED}:
        return "NOT_AVAILABLE"
    analysis_date = str(state.get("analysis_date") or "")
    today = today or datetime.now(timezone.utc).date().isoformat()
    return "CURRENT" if analysis_date == today and state.get("scan_completed_at") else "STALE"


def logger(project_root):
    log = logging.getLogger("trading_cockpit.operations")
    if not log.handlers:
        log.setLevel(logging.INFO)
        path = Path(project_root) / "data" / "research" / "trading_cockpit_operations.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    return log


def log_event(project_root, event, **fields):
    """Log state transitions only; callers do not log credentials or every rerender."""
    compact = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger(project_root).info("%s %s", event, compact)

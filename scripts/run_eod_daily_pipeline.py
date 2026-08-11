#!/usr/bin/env python3
"""Scheduled entrypoint; deliberately independent of Streamlit."""
import json
import sys

from eod_pipeline import execute_eod_pipeline


def main() -> int:
    result = execute_eod_pipeline(source="AUTOMATED_EOD")
    summary = {key: result.get(key) for key in ("status", "analysis_date", "run_id", "symbols_requested", "symbols_succeeded", "symbols_failed", "qualified_count", "allocated_count", "mark_count")}
    print(json.dumps(summary, sort_keys=True, default=str))
    return 0 if result.get("status") in {"SUCCESS", "PARTIAL_SUCCESS", "NO_COMPLETED_MARKET_BAR"} else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guarded ROLE-D1 outcome backfill using free recent completed-session OHLCV."""

import argparse
import datetime as dt
import json

import database
from role_outcome_engine import OUTCOME_METHOD_HASH, observe_pending_recommendations
from screener import fetch_bulk_stock_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-production-db", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    diagnostics = database.get_database_diagnostics(check_connection=True)
    if args.require_production_db and not (
        diagnostics["deployment_mode"] == "CLOUD"
        and diagnostics["database_backend"] == "POSTGRES"
        and diagnostics["database_status"] == "AVAILABLE"
    ):
        raise SystemExit("Production database guard failed")
    if args.report_only:
        result = {"lifecycle_counts": database.role_outcome_lifecycle_counts(OUTCOME_METHOD_HASH)}
    else:
        pending = database.load_recommendations_for_role_observation(OUTCOME_METHOD_HASH)
        symbols = sorted({str(row["symbol"]) for row in pending})
        histories = fetch_bulk_stock_data(symbols, period="2y") if symbols else {}
        result = observe_pending_recommendations(histories, dt.date.today())
        result.update({"symbols_requested": len(symbols), "histories_available": len(histories),
                       "coverage_mode": "FREE_RECENT_COMPLETED_SESSION_OHLCV"})
    print(json.dumps(result, sort_keys=True, default=str))

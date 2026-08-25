#!/usr/bin/env python3
"""Read-only production ROLE-R2 representative and calibration report."""

import argparse
import datetime as dt
import json

import database
from role_opportunity_evidence import build_role_r2_report, validate_role_r2_calibration
from role_outcome_engine import OUTCOME_METHOD_HASH


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of-date")
    parser.add_argument("--representatives", type=int, default=3)
    parser.add_argument("--require-production-db", action="store_true")
    args = parser.parse_args()
    diagnostics = database.get_database_diagnostics(check_connection=True)
    if args.require_production_db and not (
        diagnostics["deployment_mode"] == "CLOUD"
        and diagnostics["database_backend"] == "POSTGRES"
        and diagnostics["database_status"] == "AVAILABLE"
    ):
        raise SystemExit("Production database guard failed")
    cutoff = args.as_of_date or dt.date.today().isoformat()
    rows = database.load_role_learning_rows(OUTCOME_METHOD_HASH)
    latest = sorted(rows, key=lambda row: (str(row.get("signal_date")), str(row.get("opportunity_id"))), reverse=True)
    reports = [build_role_r2_report(target, rows, cutoff) for target in latest[:max(0, args.representatives)]]
    result = {
        "as_of_date": cutoff, "recommendation_count": len(rows),
        "representative_reports": reports,
        "calibration_validation": validate_role_r2_calibration(rows, cutoff),
    }
    print(json.dumps(result, sort_keys=True, default=str))

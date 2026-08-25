#!/usr/bin/env python3
"""Read-only ROLE-R1 live analytics report."""

import argparse
import json

import database
from role_learning_analytics import load_live_role_r1_analytics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of-date")
    parser.add_argument("--require-production-db", action="store_true")
    args = parser.parse_args()
    diagnostics = database.get_database_diagnostics(check_connection=True)
    if args.require_production_db and not (
        diagnostics["deployment_mode"] == "CLOUD"
        and diagnostics["database_backend"] == "POSTGRES"
        and diagnostics["database_status"] == "AVAILABLE"
    ):
        raise SystemExit("Production database guard failed")
    print(json.dumps(load_live_role_r1_analytics(args.as_of_date), sort_keys=True, default=str))

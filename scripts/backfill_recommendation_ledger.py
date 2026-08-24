#!/usr/bin/env python3
"""Conservative, idempotent LSV-V1 backfill from canonical payloads only."""

import argparse
import json

import database
from recommendation_ledger import backfill_canonical_history


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
    result = database.recommendation_ledger_coverage() if args.report_only else backfill_canonical_history()
    print(json.dumps(result, sort_keys=True))

#!/usr/bin/env python3
"""Explicit Market Context refresh; UI never imports this runner."""

from __future__ import annotations

import argparse
import json

import database
from adapters import UniverseProvider
from market_context import fetch_structural_context, refresh_market_context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("EOD", "PREOPEN", "INTRADAY"), required=True)
    parser.add_argument("--as-of-date", help="Required completed NSE session for EOD")
    parser.add_argument("--require-production-db", action="store_true")
    args = parser.parse_args()
    diagnostics = database.get_database_diagnostics(check_connection=True)
    if args.require_production_db and not (
        diagnostics["deployment_mode"] == "CLOUD"
        and diagnostics["database_backend"] == "POSTGRES"
        and diagnostics["database_status"] == "AVAILABLE"
    ):
        raise SystemExit("Production database guard failed")
    if args.phase == "EOD":
        if not args.as_of_date:
            raise SystemExit("--as-of-date is required for EOD")
        symbols = UniverseProvider().get_universe(date_str=args.as_of_date)
        structural = fetch_structural_context(args.as_of_date, symbols)
        result = refresh_market_context(phase="EOD", structural=structural)
    else:
        result = refresh_market_context(phase=args.phase)
    bundle = database.load_latest_market_context_bundle()
    readback = {
        "structural_as_of": (bundle.get("structural") or {}).get("as_of_date"),
        "trend": ((bundle.get("structural") or {}).get("trend") or {}).get("state"),
        "breadth": ((bundle.get("structural") or {}).get("breadth") or {}).get("state"),
        "volatility": ((bundle.get("structural") or {}).get("volatility") or {}).get("state"),
        "sector_participation": ((bundle.get("structural") or {}).get("sector_participation") or {}).get("state"),
        "investor_observation_date": (bundle.get("investor_participation") or {}).get("observation_date"),
        "investor_participation": (bundle.get("investor_participation") or {}).get("state"),
        "cross_asset": (bundle.get("cross_asset") or {}).get("state"),
        "external_risk": (bundle.get("event_risk") or {}).get("state"),
    }
    if args.phase == "EOD" and (
        readback["structural_as_of"] != args.as_of_date
        or any(readback[key] in {None, "NOT_AVAILABLE"} for key in ("trend", "breadth", "volatility", "sector_participation", "investor_participation"))
    ):
        raise SystemExit(f"EOD activation readback failed: {json.dumps(readback, sort_keys=True)}")
    print(json.dumps({"database": diagnostics, "refresh": result, "readback": readback}, sort_keys=True))


if __name__ == "__main__":
    main()

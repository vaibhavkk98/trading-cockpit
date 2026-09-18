#!/usr/bin/env python3
"""Activate the isolated Systematic Engine V1B accounts."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autopaper_portfolio_risk_activation import activate_portfolio_risk

if __name__ == "__main__":
    print(json.dumps(activate_portfolio_risk(), sort_keys=True, default=str))

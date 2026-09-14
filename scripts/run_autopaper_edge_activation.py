#!/usr/bin/env python3
"""Activate the isolated Systematic Engine V1A accounts."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autopaper_edge_activation import activate_edge_capture

if __name__ == "__main__":
    print(json.dumps(activate_edge_capture(), sort_keys=True, default=str))

#!/usr/bin/env python3
"""Activate AutoPaper SHADOW_ROLLING against the configured database."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autopaper_rolling_activation import activate_rolling_shadow


if __name__ == "__main__":
    print(json.dumps(activate_rolling_shadow(), sort_keys=True, default=str))

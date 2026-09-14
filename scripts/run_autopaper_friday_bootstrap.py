#!/usr/bin/env python3
"""Run the one-time Friday AutoPaper bootstrap against the configured database."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autopaper_friday_bootstrap import BootstrapNoGo, execute_friday_bootstrap


def main() -> int:
    try:
        result = execute_friday_bootstrap()
    except BootstrapNoGo as exc:
        print(json.dumps({"status": "NO_GO", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

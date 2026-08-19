#!/usr/bin/env python3
"""Explicit lightweight pre-open/intraday Market Context refresh."""

from __future__ import annotations

import argparse
import json

from market_context import refresh_market_context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("PREOPEN", "INTRADAY"), required=True)
    args = parser.parse_args()
    print(json.dumps(refresh_market_context(phase=args.phase), sort_keys=True))


if __name__ == "__main__":
    main()

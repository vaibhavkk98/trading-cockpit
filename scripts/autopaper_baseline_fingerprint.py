#!/usr/bin/env python3
"""Produce a deterministic frozen-baseline behavior fingerprint for P1.1 proof."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
db_path = Path(os.environ.get("AUTOPAPER_FINGERPRINT_DB") or tempfile.gettempdir())
if db_path.is_dir(): db_path = db_path / "autopaper_baseline_fingerprint.db"
if db_path.exists(): db_path.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(db_path)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database

module_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "autopaper_prospective.py"
spec = importlib.util.spec_from_file_location("autopaper_fingerprint_target", module_path)
engine = importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)

dates = pd.bdate_range("2026-09-14", periods=14)
frame = pd.DataFrame({"Open": [100 + i for i in range(len(dates))],
    "High": [102 + i for i in range(len(dates))], "Low": [99 + i for i in range(len(dates))],
    "Close": [101 + i for i in range(len(dates))], "Volume": [1_000_000] * len(dates)}, index=dates)
histories = {"AAA.NS": frame}
decision = {"opportunity_id": "2026-09-14:AAA:Donchian", "symbol": "AAA.NS",
    "signal_date": "2026-09-14", "strategy": "Donchian Channel Breakout",
    "entry_price": 100., "atr_20": 8., "current_volume": 1_000_000, "is_qualified": True}
for index, stamp in enumerate(dates[:13]):
    engine.run_prospective_autopaper([decision] if index == 0 else [], histories, stamp.date(),
        dt.datetime.combine(stamp.date(), dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{stamp.date()}")

session = database.SessionLocal()
try:
    account = session.get(database.AutoPaperAccount, "BASELINE_C3")
    orders = session.query(database.AutoPaperOrder).filter_by(account_id="BASELINE_C3").order_by(database.AutoPaperOrder.order_id).all()
    positions = session.query(database.AutoPaperPosition).filter_by(account_id="BASELINE_C3").order_by(database.AutoPaperPosition.position_id).all()
    trades = session.query(database.AutoPaperTrade).filter_by(account_id="BASELINE_C3").order_by(database.AutoPaperTrade.trade_id).all()
    decisions = session.query(database.AutoPaperDecision).filter_by(account_id="BASELINE_C3").order_by(database.AutoPaperDecision.decision_id).all()
    snapshots = session.query(database.AutoPaperPortfolioSnapshot).filter_by(account_id="BASELINE_C3").order_by(database.AutoPaperPortfolioSnapshot.market_date).all()
    payload = {"account": [account.methodology_hash, account.initial_capital, account.cash,
            str(account.last_market_date), account.status, account.state_version],
        "orders": [[x.order_id, x.opportunity_id, x.symbol, x.side, str(x.requested_session), x.status,
            x.quantity, x.requested_capital, x.fill_price, x.fees, x.slippage, json.loads(x.payload)] for x in orders],
        "positions": [[x.position_id, x.opportunity_id, x.symbol, x.status, str(x.entry_date), x.quantity,
            x.entry_price, x.age, x.current_mark, x.unrealized_pnl, x.mfe_pct, x.mae_pct,
            str(x.planned_exit_date), x.planned_exit_state, json.loads(x.payload)] for x in positions],
        "trades": [[x.trade_id, x.opportunity_id, x.symbol, str(x.entry_date), str(x.exit_date),
            x.net_pnl, x.realized_return_pct, x.holding_sessions, json.loads(x.payload)] for x in trades],
        "decisions": [[x.decision_id, str(x.market_date), x.action, x.reason_code, json.loads(x.payload)] for x in decisions],
        "snapshots": [[str(x.market_date), x.cash, x.nav, x.open_positions,
            x.state_fingerprint, json.loads(x.payload)] for x in snapshots]}
finally: session.close()
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
FROZEN_PRE_P1_1_FINGERPRINT = "b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640"
if module_path == (ROOT / "autopaper_prospective.py").resolve():
    assert fingerprint == FROZEN_PRE_P1_1_FINGERPRINT, "FROZEN_BASELINE_BEHAVIOR_CHANGED"
print(fingerprint)

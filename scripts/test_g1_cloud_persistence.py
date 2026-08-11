"""Focused G1 storage tests. All writes use an isolated temporary SQLite file."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_isolated_sqlite() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "g1_test.sqlite"
        code = r'''
from database import add_paper_trade, get_closed_trades, get_open_trades, init_db, load_portfolio_snapshots, save_portfolio_snapshot, update_trade_status
assert init_db() is True
trade = add_paper_trade("G1TEST.NS", 100.0, 10, "Donchian Channel Breakout", risk_metadata={
  "risk_contract_version":"F2_V1", "risk_reference_available":True, "risk_reference_type":"PRIOR_LOW",
  "reference_risk_per_share":2.0, "reference_risk_rupees":20.0, "executable_stop_enabled":True,
  "initial_executable_stop":96.0, "executable_risk_per_share":4.0, "executable_risk_rupees":40.0,
  "gap_risk_possible":True, "target_status":"NOT_AVAILABLE"})
assert trade.position_value == 1000.0 and trade.target_status == "NOT_AVAILABLE"
reloaded = get_open_trades()[0]
assert reloaded.risk_reference_available is True and reloaded.initial_executable_stop == 96.0
closed = update_trade_status(reloaded.id, "CLOSED_MANUAL", 110.0)
assert closed.realized_pnl == 100.0 and closed.realized_return_pct == 10.0 and closed.exit_timestamp is not None
assert get_closed_trades()[0].realized_pnl == 100.0
snapshot = {"portfolio_equity":1000100.0, "cash":999100.0, "deployed_capital":0.0, "realized_pnl":100.0,
  "unrealized_pnl":0.0, "open_positions":0, "reference_heat_pct":None, "reference_heat_coverage_count":0,
  "reference_heat_missing_count":0, "executable_stop_heat_pct":None, "executable_stop_coverage_count":0}
assert save_portfolio_snapshot(snapshot, "TEST") ["saved"] is True
assert save_portfolio_snapshot(snapshot, "TEST") ["saved"] is False
assert len(load_portfolio_snapshots()) == 1
print("isolated persistence: PASS")
'''
        environment = dict(os.environ, TRADING_COCKPIT_DB_PATH=str(db_path))
        environment.pop("DATABASE_URL", None)
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "isolated persistence: PASS" in result.stdout


def run_legacy_migration() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "legacy.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol VARCHAR(20) NOT NULL,
            sector VARCHAR(50), entry_date DATETIME NOT NULL, entry_price FLOAT NOT NULL,
            quantity INTEGER NOT NULL, stop_loss FLOAT NOT NULL, target FLOAT NOT NULL,
            status VARCHAR(20), exit_date DATETIME, exit_price FLOAT, realized_pnl FLOAT,
            strategy_used VARCHAR(100) NOT NULL)""")
        connection.commit(); connection.close()
        code = """
import sqlite3
from database import init_db
assert init_db() is True and init_db() is True
connection = sqlite3.connect(__import__('os').environ['TRADING_COCKPIT_DB_PATH'])
columns = {row[1] for row in connection.execute('PRAGMA table_info(trades)')}
assert {'risk_contract_version','realized_return_pct','position_value','entry_timestamp'}.issubset(columns)
assert {row[0] for row in connection.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}.issuperset({'portfolio_snapshots','agent_attribution'})
print('legacy migration: PASS')
"""
        environment = dict(os.environ, TRADING_COCKPIT_DB_PATH=str(db_path))
        environment.pop("DATABASE_URL", None)
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "legacy migration: PASS" in result.stdout


def run() -> None:
    run_isolated_sqlite()
    run_legacy_migration()
    database_source = (ROOT / "database.py").read_text()
    assert "os.environ.get(\"DATABASE_URL\") or _streamlit_database_url()" in database_source
    assert "DATABASE_BACKEND = \"POSTGRES\" if _configured_database_url else \"SQLITE\"" in database_source
    assert "sqlite:///{DB_PATH}" in database_source
    assert "pool_pre_ping" in database_source and "DatabaseUnavailableError" in database_source
    assert "PortfolioSnapshot" in database_source and "UniqueConstraint" in database_source
    assert "ALTER TABLE trades ADD COLUMN" in database_source
    app_source = (ROOT / "app.py").read_text()
    assert "database_diagnostics" in app_source and "Paper-trade storage is unavailable" in app_source
    assert "execute_eod_pipeline" in app_source and "load_latest_analysis_run" in app_source
    assert "target_price" not in app_source and "risk_reward_ratio" not in app_source
    print("G1 cloud persistence tests: PASS")


if __name__ == "__main__":
    run()

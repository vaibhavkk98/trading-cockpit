"""Short-lived, read-only Streamlit caches with domain-level invalidation."""
from __future__ import annotations

from typing import Any

import streamlit as st

import database
from performance_timing import timed
from portfolio_analytics import get_portfolio_pnl
from role_learning_analytics import load_live_role_r1_analytics


@st.cache_data(ttl=20, show_spinner=False)
def load_open_positions(_execution) -> list[dict[str, Any]]:
    with timed("db.open_positions"):
        return _execution.get_open_positions()


@st.cache_data(ttl=20, show_spinner=False)
def load_portfolio_summary(_execution) -> dict[str, Any]:
    with timed("db.portfolio_summary"):
        return _execution.get_portfolio_summary()


@st.cache_data(ttl=20, show_spinner=False)
def load_portfolio_state(_execution) -> dict[str, Any]:
    """Compatibility composition over the two frozen P1 read contracts."""
    return {
        "positions": load_open_positions(_execution),
        "summary": load_portfolio_summary(_execution),
    }


@st.cache_data(ttl=45, show_spinner=False)
def load_latest_opportunities() -> dict[str, Any] | None:
    with timed("db.latest_opportunities"):
        return database.load_latest_analysis_run()


@st.cache_data(ttl=300, show_spinner=False)
def load_market_context_bundle() -> dict[str, Any]:
    """Persisted snapshots only; this read path has no provider access."""
    with timed("db.market_context_bundle"):
        return database.load_latest_market_context_bundle()


@st.cache_data(ttl=300, show_spinner=False)
def load_role_learning_analytics() -> dict[str, Any]:
    """Read persisted ROLE state; never fetch market data or write outcomes."""
    with timed("db.role_learning_analytics"):
        return load_live_role_r1_analytics()


@st.cache_data(ttl=20, show_spinner=False)
def load_portfolio_pnl(period: str, custom_start: Any = None, custom_end: Any = None) -> dict[str, Any]:
    with timed("db.portfolio_pnl", period=period):
        return get_portfolio_pnl(period, custom_start=custom_start, custom_end=custom_end)


@st.cache_data(ttl=20, show_spinner=False)
def load_portfolio_snapshots(limit: int = 365) -> list[dict[str, Any]]:
    with timed("db.portfolio_snapshots", limit=limit):
        rows = database.load_portfolio_snapshots(limit=limit)
        return [
            {
                "snapshot_date": row.snapshot_date.isoformat(),
                "snapshot_timestamp": row.snapshot_timestamp.isoformat(),
                "portfolio_equity": row.portfolio_equity,
            }
            for row in rows
        ]


@st.cache_data(ttl=20, show_spinner=False)
def load_closed_trade_rows(limit: int = 250) -> list[dict[str, Any]]:
    with timed("db.closed_trades", limit=limit):
        trades = database.get_closed_trades()[:limit]
        return [
            {
                "symbol": trade.symbol, "strategy_used": trade.strategy_used,
                "entry_price": trade.entry_price, "exit_price": trade.exit_price,
                "quantity": trade.quantity, "realized_pnl": trade.realized_pnl,
                "exit_date": trade.exit_date.date().isoformat() if trade.exit_date else None,
            }
            for trade in trades
        ]


@st.cache_data(ttl=1800, show_spinner=False)
def load_ha_summaries(identities: tuple[tuple[str, str], ...], methodology_hash: str) -> dict[str, dict[str, Any]]:
    with timed("db.ha_summaries", count=len(identities)):
        return database.load_historical_analog_summaries(list(identities), methodology_hash)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ha_snapshot(opportunity_id: str, signal_date: str, methodology_hash: str) -> dict[str, Any] | None:
    with timed("db.ha_snapshot", opportunity_id=opportunity_id):
        return database.load_historical_analog_snapshot(opportunity_id, signal_date, methodology_hash)


def invalidate_portfolio_reads() -> None:
    """Invalidate only portfolio/trade/mark dependent read domains."""
    load_open_positions.clear()
    load_portfolio_summary.clear()
    load_portfolio_state.clear()
    load_portfolio_pnl.clear()
    load_portfolio_snapshots.clear()
    load_closed_trade_rows.clear()


def invalidate_opportunity_reads() -> None:
    load_latest_opportunities.clear()


def invalidate_ha_snapshot(opportunity_id: str, signal_date: str, methodology_hash: str) -> None:
    load_ha_snapshot.clear(opportunity_id, signal_date, methodology_hash)
    # Summary pages are one bulk cache entry per visible opportunity set.
    load_ha_summaries.clear()

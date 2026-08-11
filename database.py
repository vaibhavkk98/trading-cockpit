"""Provider-neutral durable storage for the Trading Cockpit paper ledger.

SQLite remains the local-development default.  A configured ``DATABASE_URL``
selects external Postgres; it never falls back to a new local database when
that configured backend is unavailable.
"""

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import declarative_base, joinedload, relationship, sessionmaker


def _streamlit_database_url() -> Optional[str]:
    """Read an optional Streamlit secret without making Streamlit a DB dependency."""
    try:
        import streamlit as st
        direct = st.secrets.get("DATABASE_URL")
        nested = st.secrets.get("database", {}).get("url")
        return direct or nested
    except Exception:
        return None


def _configured_url() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or _streamlit_database_url()


def _normalise_postgres_url(url: str) -> str:
    # SQLAlchemy accepts the explicit driver name consistently across local and cloud.
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_configured_database_url = _configured_url()
DB_PATH = os.environ.get("TRADING_COCKPIT_DB_PATH", "paper_trading.db")
DATABASE_URL = _normalise_postgres_url(_configured_database_url) if _configured_database_url else f"sqlite:///{DB_PATH}"
DATABASE_BACKEND = "POSTGRES" if _configured_database_url else "SQLITE"
DEPLOYMENT_MODE = "CLOUD" if _configured_database_url else "LOCAL"

_engine_kwargs: Dict[str, Any] = {"echo": False, "pool_pre_ping": True}
if DATABASE_BACKEND == "SQLITE":
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({"pool_size": 5, "max_overflow": 2, "pool_recycle": 1800})

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()
_database_available: Optional[bool] = None
_database_error: Optional[str] = None


class DatabaseUnavailableError(RuntimeError):
    """Raised for a configured backend that cannot safely service a write."""


class PaperTrade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    sector = Column(String(50), default="General")
    entry_date = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    entry_timestamp = Column(DateTime(timezone=True), nullable=True)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    position_value = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=False)
    target = Column(Float, nullable=False)
    status = Column(String(20), default="OPEN", index=True)
    exit_date = Column(DateTime(timezone=True), nullable=True)
    exit_timestamp = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    realized_return_pct = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc), nullable=True)
    strategy_used = Column(String(100), nullable=False)
    risk_contract_version = Column(String(40), nullable=True)
    allocation_status = Column(String(80), nullable=True)
    opportunity_reference = Column(String(160), nullable=True)
    risk_reference_type = Column(String(120), nullable=True)
    risk_reference_value = Column(Float, nullable=True)
    risk_reference_available = Column(Boolean, nullable=True)
    reference_risk_per_share = Column(Float, nullable=True)
    reference_risk_rupees = Column(Float, nullable=True)
    executable_stop_enabled = Column(Boolean, nullable=True)
    initial_executable_stop = Column(Float, nullable=True)
    executable_risk_per_share = Column(Float, nullable=True)
    executable_risk_rupees = Column(Float, nullable=True)
    gap_risk_possible = Column(Boolean, nullable=True)
    target_status = Column(String(40), nullable=True)

    attribution = relationship("AgentAttribution", back_populates="trade", uselist=False, cascade="all, delete-orphan")


class AgentAttribution(Base):
    __tablename__ = "agent_attribution"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, unique=True)
    tech_score = Column(Float, nullable=False)
    fund_score = Column(Float, nullable=False)
    sent_score = Column(Float, nullable=False)
    weights_used = Column(String(255), nullable=False)
    agent_reasoning = Column(Text, nullable=True)

    trade = relationship("PaperTrade", back_populates="attribution")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_date", "snapshot_reason", "state_fingerprint", name="uq_snapshot_state"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_timestamp = Column(DateTime(timezone=True), nullable=False)
    portfolio_equity = Column(Float, nullable=True)
    cash = Column(Float, nullable=True)
    deployed_capital = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    open_positions = Column(Integer, nullable=True)
    reference_heat_pct = Column(Float, nullable=True)
    reference_heat_coverage_count = Column(Integer, nullable=True)
    reference_heat_missing_count = Column(Integer, nullable=True)
    executable_stop_heat_pct = Column(Float, nullable=True)
    executable_stop_coverage_count = Column(Integer, nullable=True)
    snapshot_reason = Column(String(80), nullable=False)
    state_fingerprint = Column(String(64), nullable=False)
    source = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


_TRADE_ADDITIONS = {
    "entry_timestamp": "TIMESTAMP", "position_value": "FLOAT", "exit_timestamp": "TIMESTAMP",
    "realized_return_pct": "FLOAT", "created_at": "TIMESTAMP", "updated_at": "TIMESTAMP",
    "risk_contract_version": "VARCHAR(40)", "allocation_status": "VARCHAR(80)",
    "opportunity_reference": "VARCHAR(160)", "risk_reference_type": "VARCHAR(120)",
    "risk_reference_value": "FLOAT", "risk_reference_available": "BOOLEAN",
    "reference_risk_per_share": "FLOAT", "reference_risk_rupees": "FLOAT",
    "executable_stop_enabled": "BOOLEAN", "initial_executable_stop": "FLOAT",
    "executable_risk_per_share": "FLOAT", "executable_risk_rupees": "FLOAT",
    "gap_risk_possible": "BOOLEAN", "target_status": "VARCHAR(40)",
}


def init_db() -> bool:
    """Idempotently create tables and additive columns without destroying data."""
    global _database_available, _database_error
    try:
        Base.metadata.create_all(bind=engine)
        existing = {column["name"] for column in inspect(engine).get_columns("trades")}
        with engine.begin() as connection:
            for name, sql_type in _TRADE_ADDITIONS.items():
                if name not in existing:
                    # Names and types are module-owned constants, never user values.
                    connection.exec_driver_sql(f"ALTER TABLE trades ADD COLUMN {name} {sql_type}")
        _database_available, _database_error = True, None
        return True
    except SQLAlchemyError as exc:
        _database_available, _database_error = False, type(exc).__name__
        return False


def get_database_diagnostics(check_connection: bool = True) -> Dict[str, str]:
    if check_connection:
        init_db()
    return {
        "deployment_mode": DEPLOYMENT_MODE,
        "database_backend": DATABASE_BACKEND,
        "database_status": "AVAILABLE" if _database_available else "NOT_AVAILABLE",
    }


def _require_database() -> None:
    if not init_db():
        raise DatabaseUnavailableError("Configured paper-trade storage is unavailable.")


def _safe_read(query):
    if not init_db():
        return []
    session = SessionLocal()
    try:
        return query(session)
    except SQLAlchemyError:
        return []
    finally:
        session.close()


def add_paper_trade(symbol: str, entry_price: float, quantity: int, strategy_used: str,
                    stop_loss: Optional[float] = None, target: Optional[float] = None,
                    sector: str = "General", tech_score: float = 0.85, fund_score: float = 7.0,
                    sent_score: float = 0.0, weights_used: Optional[Dict[str, float]] = None,
                    agent_reasoning: str = "", risk_metadata: Optional[Dict[str, Any]] = None) -> PaperTrade:
    _require_database()
    session = SessionLocal()
    try:
        metadata = risk_metadata or {}
        now = dt.datetime.now(dt.timezone.utc)
        # Retained legacy fields are compatibility-only for F2/V1 records.
        storage_stop = stop_loss if isinstance(stop_loss, (int, float)) else entry_price
        storage_target = target if isinstance(target, (int, float)) else entry_price
        trade = PaperTrade(
            symbol=symbol, sector=sector, entry_date=now, entry_timestamp=now, entry_price=entry_price,
            quantity=quantity, position_value=round(entry_price * quantity, 2), stop_loss=storage_stop,
            target=storage_target, status="OPEN", strategy_used=strategy_used, created_at=now, updated_at=now,
            risk_contract_version=metadata.get("risk_contract_version", "F2_V1"),
            allocation_status=metadata.get("allocation_status"), opportunity_reference=metadata.get("opportunity_reference"),
            risk_reference_type=metadata.get("risk_reference_type"), risk_reference_value=metadata.get("risk_reference_value"),
            risk_reference_available=bool(metadata.get("risk_reference_available")),
            reference_risk_per_share=metadata.get("reference_risk_per_share"), reference_risk_rupees=metadata.get("reference_risk_rupees"),
            executable_stop_enabled=bool(metadata.get("executable_stop_enabled")), initial_executable_stop=metadata.get("initial_executable_stop"),
            executable_risk_per_share=metadata.get("executable_risk_per_share"), executable_risk_rupees=metadata.get("executable_risk_rupees"),
            gap_risk_possible=bool(metadata.get("gap_risk_possible")), target_status=metadata.get("target_status", "NOT_AVAILABLE"),
        )
        trade.attribution = AgentAttribution(tech_score=tech_score, fund_score=fund_score, sent_score=sent_score,
                                             weights_used=json.dumps(weights_used or {"w_tech": .5, "w_fund": .3, "w_sent": .2}),
                                             agent_reasoning=agent_reasoning)
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def get_open_trades() -> List[PaperTrade]:
    return _safe_read(lambda s: s.query(PaperTrade).options(joinedload(PaperTrade.attribution)).filter(PaperTrade.status == "OPEN").all())


def get_closed_trades() -> List[PaperTrade]:
    return _safe_read(lambda s: s.query(PaperTrade).options(joinedload(PaperTrade.attribution)).filter(PaperTrade.status != "OPEN").order_by(PaperTrade.exit_date.desc()).all())


def update_trade_status(trade_id: int, new_status: str, exit_price: float,
                        exit_date: Optional[dt.datetime] = None) -> Optional[PaperTrade]:
    _require_database()
    session = SessionLocal()
    try:
        trade = session.query(PaperTrade).filter(PaperTrade.id == trade_id).first()
        if not trade:
            return None
        closed_at = exit_date or dt.datetime.now(dt.timezone.utc)
        trade.status, trade.exit_price, trade.exit_date, trade.exit_timestamp = new_status, exit_price, closed_at, closed_at
        trade.realized_pnl = round((exit_price - trade.entry_price) * trade.quantity, 2)
        trade.realized_return_pct = round(((exit_price - trade.entry_price) / trade.entry_price) * 100, 4) if trade.entry_price else None
        trade.updated_at = dt.datetime.now(dt.timezone.utc)
        session.commit(); session.refresh(trade)
        return trade
    except SQLAlchemyError:
        session.rollback()
        return None
    finally:
        session.close()


def save_portfolio_snapshot(snapshot: Dict[str, Any], reason: str, source: str = "COCKPIT") -> Dict[str, Any]:
    """Persist a state-deduplicated snapshot; safe for future scheduled callers."""
    _require_database()
    session = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        state = {key: snapshot.get(key) for key in (
            "portfolio_equity", "cash", "deployed_capital", "realized_pnl", "unrealized_pnl", "open_positions",
            "reference_heat_pct", "reference_heat_coverage_count", "reference_heat_missing_count",
            "executable_stop_heat_pct", "executable_stop_coverage_count")}
        fingerprint = __import__("hashlib").sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
        snapshot_date = snapshot.get("snapshot_date") or now.date()
        if isinstance(snapshot_date, str):
            snapshot_date = dt.date.fromisoformat(snapshot_date)
        existing = session.query(PortfolioSnapshot).filter_by(snapshot_date=snapshot_date, snapshot_reason=reason, state_fingerprint=fingerprint).first()
        if existing:
            return {"saved": False, "snapshot_id": existing.id}
        row = PortfolioSnapshot(snapshot_date=snapshot_date, snapshot_timestamp=now, snapshot_reason=reason,
                                state_fingerprint=fingerprint, source=source, **state)
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id}
    except IntegrityError:
        session.rollback()
        return {"saved": False, "snapshot_id": None}
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def load_portfolio_snapshots(limit: int = 365) -> List[PortfolioSnapshot]:
    return _safe_read(lambda s: s.query(PortfolioSnapshot).order_by(PortfolioSnapshot.snapshot_timestamp.desc()).limit(limit).all())


def sync_paper_trades() -> Dict[str, Any]:
    """Retain the pre-existing legacy close behavior; F2 records remain manual."""
    open_trades = get_open_trades()
    if not open_trades:
        return {"updated_count": 0, "closed_target": 0, "closed_sl": 0, "details": [], "price_unavailable": []}
    bars, unavailable, details = {}, [], []
    for symbol in sorted({trade.symbol for trade in open_trades}):
        try:
            data = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
            if not data.empty: bars[symbol] = data.iloc[-1]
            else: unavailable.append(symbol)
        except Exception: unavailable.append(symbol)
    targets = stops = 0
    for trade in open_trades:
        bar = bars.get(trade.symbol)
        if bar is None:
            continue
        if trade.risk_contract_version == "F2_V1":
            continue
        status = price = None
        if float(bar["High"]) >= trade.target: status, price, targets = "CLOSED_TARGET", trade.target, targets + 1
        elif float(bar["Low"]) <= trade.stop_loss: status, price, stops = "CLOSED_SL", trade.stop_loss, stops + 1
        if status:
            changed = update_trade_status(trade.id, status, price)
            if changed: details.append({"id": trade.id, "symbol": trade.symbol, "status": status, "exit_price": price, "pnl": changed.realized_pnl})
    return {"updated_count": len(details), "closed_target": targets, "closed_sl": stops, "details": details, "price_unavailable": unavailable}


def get_portfolio_performance_summary() -> Dict[str, Any]:
    open_trades, closed_trades = get_open_trades(), get_closed_trades()
    total_closed = len(closed_trades); realized = sum(trade.realized_pnl or 0 for trade in closed_trades)
    winners = sum((trade.realized_pnl or 0) > 0 for trade in closed_trades)
    deployed = sum(trade.entry_price * trade.quantity for trade in open_trades)
    return {"open_trades_count": len(open_trades), "closed_trades_count": total_closed, "winning_trades_count": winners,
            "win_rate_pct": round(winners / total_closed * 100, 1) if total_closed else 0.0,
            "total_realized_pnl": round(realized, 2), "open_capital_deployed": round(deployed, 2)}


def get_open_trades_with_live_data() -> List[Dict[str, Any]]:
    trades = get_open_trades()
    prices: Dict[str, float] = {}
    for symbol in {trade.symbol for trade in trades}:
        try:
            data = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
            if not data.empty: prices[symbol] = float(data["Close"].iloc[-1])
        except Exception: pass
    rows = []
    for trade in trades:
        current = prices.get(trade.symbol); available = current is not None; attr = trade.attribution
        rows.append({"id": trade.id, "symbol": trade.symbol, "sector": trade.sector,
            "entry_date": trade.entry_date.strftime("%Y-%m-%d %H:%M") if trade.entry_date else "", "entry_price": trade.entry_price,
            "quantity": trade.quantity, "position_value": trade.position_value or trade.entry_price * trade.quantity,
            "stop_loss": trade.stop_loss, "target": trade.target, "current_price": current,
            "price_status": "AVAILABLE" if available else "PRICE_NOT_AVAILABLE",
            "unrealized_pnl_inr": round((current-trade.entry_price)*trade.quantity, 2) if available else None,
            "unrealized_pnl_pct": round((current-trade.entry_price)/trade.entry_price*100, 2) if available and trade.entry_price else None,
            "strategy_used": trade.strategy_used, "tech_score": attr.tech_score if attr else .85, "fund_score": attr.fund_score if attr else 7.,
            "sent_score": attr.sent_score if attr else 0., "reasoning": attr.agent_reasoning if attr else "",
            "risk_contract_version": trade.risk_contract_version, "allocation_status": trade.allocation_status,
            "opportunity_reference": trade.opportunity_reference, "risk_reference_type": trade.risk_reference_type,
            "risk_reference_value": trade.risk_reference_value, "risk_reference_available": bool(trade.risk_reference_available) if trade.risk_contract_version else False,
            "reference_risk_per_share": trade.reference_risk_per_share, "reference_risk_rupees": trade.reference_risk_rupees,
            "executable_stop_enabled": bool(trade.executable_stop_enabled) if trade.risk_contract_version else False,
            "initial_executable_stop": trade.initial_executable_stop, "executable_risk_per_share": trade.executable_risk_per_share,
            "executable_risk_rupees": trade.executable_risk_rupees, "gap_risk_possible": bool(trade.gap_risk_possible) if trade.risk_contract_version else False,
            "target_status": trade.target_status or "NOT_AVAILABLE", "risk_metadata_status": "AVAILABLE" if trade.risk_contract_version else "RISK_METADATA_NOT_AVAILABLE"})
    return rows


def get_agent_analytics_summary() -> Dict[str, Any]:
    """Retained lightweight analytics interface for existing callers."""
    closed = get_closed_trades()
    if not closed: return {"pnl_by_strategy": pd.DataFrame(), "conviction_vs_return": pd.DataFrame(), "post_mortem": {}}
    records = [{"id": t.id, "strategy": t.strategy_used, "realized_pnl": t.realized_pnl or 0.0,
                "return_pct": t.realized_return_pct or 0.0, "win": (t.realized_pnl or 0) > 0} for t in closed]
    df = pd.DataFrame(records)
    return {"pnl_by_strategy": df.groupby("strategy").agg(Total_Trades=("id", "count"), Realized_PnL=("realized_pnl", "sum")).reset_index(),
            "conviction_vs_return": df, "post_mortem": {}}


init_db()

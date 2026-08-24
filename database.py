"""Provider-neutral durable storage for the Trading Cockpit paper ledger.

SQLite remains the local-development default.  A configured ``DATABASE_URL``
selects external Postgres; it never falls back to a new local database when
that configured backend is unavailable.
"""

import datetime as dt
import hashlib
import json
import math
import os
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import yfinance as yf
from provider_symbols import yahoo_nse_symbol
from position_mark_provider import fetch_latest_yahoo_marks
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, inspect, text
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
_database_init_lock = threading.Lock()


class DatabaseUnavailableError(RuntimeError):
    """Raised for a configured backend that cannot safely service a write."""


class PaperTradeConstraintError(ValueError):
    """Raised when a requested paper trade would violate the portfolio contract."""


class RecommendationLedgerConflictError(ValueError):
    """Raised when an immutable recommendation identity is reused with new facts."""


class RoleOutcomeConflictError(ValueError):
    """Raised when an already-matured ROLE horizon changes."""


DEFAULT_PORTFOLIO_CAPITAL_INR = 1_000_000.0
DEFAULT_MAX_OPEN_POSITIONS = 10


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
    price_coverage_count = Column(Integer, nullable=True)
    price_coverage_total = Column(Integer, nullable=True)
    snapshot_reason = Column(String(80), nullable=False)
    state_fingerprint = Column(String(64), nullable=False)
    source = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class AnalysisRun(Base):
    """Canonical EOD decision record, safely retried once per market date."""
    __tablename__ = "analysis_runs"
    __table_args__ = (UniqueConstraint("analysis_date", name="uq_analysis_run_date"),)
    run_id = Column(String(80), primary_key=True)
    analysis_date = Column(Date, nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(40), nullable=False, index=True)
    symbols_requested = Column(Integer, nullable=False, default=0)
    symbols_succeeded = Column(Integer, nullable=False, default=0)
    symbols_failed = Column(Integer, nullable=False, default=0)
    qualified_count = Column(Integer, nullable=False, default=0)
    allocated_count = Column(Integer, nullable=False, default=0)
    provider_summary = Column(Text, nullable=True)
    decision_contract_version = Column(String(80), nullable=False)
    error_summary = Column(Text, nullable=True)
    source = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class DailyOpportunity(Base):
    __tablename__ = "daily_opportunities"
    __table_args__ = (UniqueConstraint("run_id", "opportunity_id", name="uq_daily_opportunity_run"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(80), ForeignKey("analysis_runs.run_id"), nullable=False, index=True)
    analysis_date = Column(Date, nullable=False, index=True)
    opportunity_id = Column(String(180), nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    strategy = Column(String(120), nullable=False)
    priority = Column(Integer, nullable=True)
    volume_ratio_20 = Column(Float, nullable=True)
    qualification_status = Column(String(80), nullable=False)
    entry_price = Column(Float, nullable=True)
    allocation_status = Column(String(120), nullable=True)
    allocation_reason = Column(String(120), nullable=True)
    decision_payload = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class RecommendationLedger(Base):
    """Immutable causal state captured when a qualified opportunity is recommended."""
    __tablename__ = "recommendation_ledger"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "lsv_methodology_hash", name="uq_recommendation_lsv_identity"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(String(180), nullable=False, index=True)
    signal_date = Column(Date, nullable=False, index=True)
    signal_timestamp = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    strategy = Column(String(120), nullable=False)
    reference_price = Column(Float, nullable=True)
    allocator_status = Column(String(120), nullable=True)
    opportunity_rank = Column(Integer, nullable=True)
    lsv_contract_version = Column(String(40), nullable=False)
    lsv_methodology_hash = Column(String(64), nullable=False, index=True)
    vector_payload = Column(Text, nullable=False)
    historical_analog_payload = Column(Text, nullable=False)
    market_context_payload = Column(Text, nullable=False)
    methodology_payload = Column(Text, nullable=False)
    source_timestamps = Column(Text, nullable=False)
    missingness = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class RoleOutcomeObservation(Base):
    """Mutable lifecycle header linked to one immutable recommendation."""
    __tablename__ = "role_outcome_observations"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "lsv_methodology_hash", "outcome_methodology_hash", name="uq_role_outcome_identity"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(String(180), nullable=False, index=True)
    lsv_methodology_hash = Column(String(64), nullable=False, index=True)
    signal_date = Column(Date, nullable=False, index=True)
    reference_price = Column(Float, nullable=True)
    outcome_contract_version = Column(String(40), nullable=False)
    outcome_methodology_hash = Column(String(64), nullable=False, index=True)
    lifecycle_state = Column(String(20), nullable=False, index=True)
    sessions_observed = Column(Integer, nullable=False, default=0)
    last_observation_date = Column(Date, nullable=True)
    source_payload = Column(Text, nullable=False)
    completeness = Column(Text, nullable=False)
    missingness = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class RoleOutcomeHorizon(Base):
    """Immutable metrics for one matured trading-session horizon."""
    __tablename__ = "role_outcome_horizons"
    __table_args__ = (UniqueConstraint("observation_id", "horizon_sessions", name="uq_role_matured_horizon"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    observation_id = Column(Integer, ForeignKey("role_outcome_observations.id"), nullable=False, index=True)
    horizon_sessions = Column(Integer, nullable=False)
    observation_date = Column(Date, nullable=False)
    payload = Column(Text, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    matured_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class PositionMark(Base):
    __tablename__ = "position_marks"
    __table_args__ = (UniqueConstraint("trade_id", "mark_date", name="uq_position_mark_trade_date"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    mark_price = Column(Float, nullable=False)
    mark_date = Column(Date, nullable=False, index=True)
    marked_at = Column(DateTime(timezone=True), nullable=False)
    provider = Column(String(80), nullable=False)
    mark_status = Column(String(40), nullable=False)
    source_run_id = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class PortfolioConfiguration(Base):
    """Singleton paper-portfolio configuration; trade history remains the ledger."""
    __tablename__ = "portfolio_configuration"

    id = Column(Integer, primary_key=True)
    initial_capital = Column(Float, nullable=False)
    max_open_positions = Column(Integer, nullable=True, default=DEFAULT_MAX_OPEN_POSITIONS)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), onupdate=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class HistoricalAnalogSnapshot(Base):
    """Immutable production evidence captured for one qualified opportunity."""
    __tablename__ = "historical_analog_snapshots"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "signal_date", "methodology_hash", name="uq_ha_snapshot_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(String(180), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    signal_date = Column(Date, nullable=False, index=True)
    methodology_id = Column(String(160), nullable=False)
    methodology_hash = Column(String(64), nullable=False, index=True)
    evidence_quality = Column(String(20), nullable=False)
    analog_count = Column(Integer, nullable=False)
    summary_payload = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class HistoricalAnalogMapping(Base):
    """Ranked immutable constituents of a production HA snapshot."""
    __tablename__ = "historical_analog_mappings"
    __table_args__ = (UniqueConstraint("snapshot_id", "rank", name="uq_ha_snapshot_rank"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(Integer, ForeignKey("historical_analog_snapshots.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    analog_opportunity_id = Column(String(180), nullable=False)
    analog_symbol = Column(String(32), nullable=False)
    analog_signal_date = Column(Date, nullable=False)
    analog_label_end_date = Column(Date, nullable=False)
    distance = Column(Float, nullable=False)
    feature_coverage = Column(Float, nullable=False)
    outcome_payload = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class MarketContextSnapshot(Base):
    __tablename__ = "market_context_snapshots"
    __table_args__ = (UniqueConstraint("as_of_date", "methodology_version", name="uq_market_context_daily_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    as_of_date = Column(Date, nullable=False, index=True)
    as_of_timestamp = Column(DateTime(timezone=True), nullable=False)
    methodology_version = Column(String(80), nullable=False)
    payload = Column(Text, nullable=False)
    coverage = Column(Text, nullable=False)
    missingness = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class InvestorParticipationSnapshot(Base):
    __tablename__ = "investor_participation_snapshots"
    __table_args__ = (UniqueConstraint("observation_date", "methodology_version", name="uq_investor_daily_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    observation_date = Column(Date, nullable=False, index=True)
    as_of_timestamp = Column(DateTime(timezone=True), nullable=False)
    methodology_version = Column(String(80), nullable=False)
    state = Column(String(40), nullable=False)
    payload = Column(Text, nullable=False)
    coverage = Column(String(80), nullable=False)
    missingness = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class CrossAssetSnapshot(Base):
    __tablename__ = "cross_asset_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_key", "methodology_version", name="uq_cross_asset_key_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False, index=True)
    as_of_timestamp = Column(DateTime(timezone=True), nullable=False)
    refresh_phase = Column(String(20), nullable=False)
    methodology_version = Column(String(80), nullable=False)
    state = Column(String(40), nullable=False)
    payload = Column(Text, nullable=False)
    coverage = Column(Text, nullable=False)
    missingness = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class EventRiskEvent(Base):
    __tablename__ = "event_risk_events"
    event_id = Column(String(80), primary_key=True)
    event_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    scheduled = Column(Boolean, nullable=False)
    event_type = Column(String(80), nullable=False)
    india_relevance = Column(String(20), nullable=False)
    magnitude = Column(String(20), nullable=False)
    direction = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    payload = Column(Text, nullable=False)
    methodology_version = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False)


class EventRiskSnapshot(Base):
    __tablename__ = "event_risk_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_key", "methodology_version", name="uq_event_snapshot_key_version"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(80), nullable=False, index=True)
    as_of_timestamp = Column(DateTime(timezone=True), nullable=False)
    refresh_phase = Column(String(20), nullable=False)
    methodology_version = Column(String(80), nullable=False)
    state = Column(String(40), nullable=False)
    payload = Column(Text, nullable=False)
    coverage = Column(String(80), nullable=False)
    missingness = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
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
_SNAPSHOT_ADDITIONS = {"price_coverage_count": "INTEGER", "price_coverage_total": "INTEGER"}
_PORTFOLIO_CONFIGURATION_ADDITIONS = {"max_open_positions": "INTEGER DEFAULT 10"}


def init_db() -> bool:
    """Idempotently create tables and additive columns without destroying data."""
    global _database_available, _database_error
    if _database_available is True:
        return True
    with _database_init_lock:
        if _database_available is True:
            return True
        try:
            Base.metadata.create_all(bind=engine)
            existing = {column["name"] for column in inspect(engine).get_columns("trades")}
            with engine.begin() as connection:
                for name, sql_type in _TRADE_ADDITIONS.items():
                    if name not in existing:
                        # Names and types are module-owned constants, never user values.
                        connection.exec_driver_sql(f"ALTER TABLE trades ADD COLUMN {name} {sql_type}")
                existing_snapshots = {column["name"] for column in inspect(engine).get_columns("portfolio_snapshots")}
                for name, sql_type in _SNAPSHOT_ADDITIONS.items():
                    if name not in existing_snapshots:
                        connection.exec_driver_sql(f"ALTER TABLE portfolio_snapshots ADD COLUMN {name} {sql_type}")
                existing_configuration = {column["name"] for column in inspect(engine).get_columns("portfolio_configuration")}
                for name, sql_type in _PORTFOLIO_CONFIGURATION_ADDITIONS.items():
                    if name not in existing_configuration:
                        connection.exec_driver_sql(f"ALTER TABLE portfolio_configuration ADD COLUMN {name} {sql_type}")
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
        "database_target_fingerprint": get_database_target_fingerprint(),
    }


def get_database_target_fingerprint() -> str:
    """Non-secret identifier used to confirm refresh/runtime target alignment."""
    if not _configured_database_url:
        return "LOCAL_SQLITE"
    parsed = urlparse(_normalise_postgres_url(_configured_database_url))
    target = f"{parsed.hostname or ''}:{parsed.port or 5432}{parsed.path or ''}"
    return hashlib.sha256(target.encode()).hexdigest()[:16]


def _require_database() -> None:
    if not init_db():
        raise DatabaseUnavailableError("Configured paper-trade storage is unavailable.")


def _safe_read(query):
    global _database_available, _database_error
    if not init_db():
        return []
    session = SessionLocal()
    try:
        return query(session)
    except SQLAlchemyError as exc:
        # pool_pre_ping repairs stale pooled connections; a failed read makes
        # the next request re-run the one-time availability/schema check.
        _database_available, _database_error = False, type(exc).__name__
        return []
    finally:
        session.close()


def get_portfolio_configuration() -> Dict[str, Any]:
    """Return the canonical persisted paper-portfolio configuration."""
    if not init_db():
        return {
            "initial_capital": DEFAULT_PORTFOLIO_CAPITAL_INR,
            "max_open_positions": DEFAULT_MAX_OPEN_POSITIONS,
        }
    session = SessionLocal()
    try:
        row = session.query(PortfolioConfiguration).filter_by(id=1).first()
        if row is None:
            return {
                "initial_capital": DEFAULT_PORTFOLIO_CAPITAL_INR,
                "max_open_positions": DEFAULT_MAX_OPEN_POSITIONS,
            }
        return {
            "initial_capital": float(row.initial_capital),
            "max_open_positions": row.max_open_positions,
        }
    except SQLAlchemyError:
        return {
            "initial_capital": DEFAULT_PORTFOLIO_CAPITAL_INR,
            "max_open_positions": DEFAULT_MAX_OPEN_POSITIONS,
        }
    finally:
        session.close()


def get_portfolio_capital() -> float:
    """Backward-compatible capital-only accessor."""
    return float(get_portfolio_configuration()["initial_capital"])


def set_portfolio_configuration(initial_capital: float, max_open_positions: Optional[int]) -> Dict[str, Any]:
    """Persist validated controls without rewriting or deleting ledger state."""
    try:
        capital = float(initial_capital)
    except (TypeError, ValueError) as exc:
        raise PaperTradeConstraintError("Portfolio capital must be a positive amount.") from exc
    if not math.isfinite(capital) or capital <= 0:
        raise PaperTradeConstraintError("Portfolio capital must be a positive amount.")
    if max_open_positions is not None:
        if isinstance(max_open_positions, bool):
            raise PaperTradeConstraintError("Maximum open positions must be a positive whole number or no limit.")
        try:
            parsed_limit = int(max_open_positions)
        except (TypeError, ValueError) as exc:
            raise PaperTradeConstraintError("Maximum open positions must be a positive whole number or no limit.") from exc
        if parsed_limit <= 0 or float(max_open_positions) != parsed_limit:
            raise PaperTradeConstraintError("Maximum open positions must be a positive whole number or no limit.")
        max_open_positions = parsed_limit

    _require_database()
    session = SessionLocal()
    try:
        if DATABASE_BACKEND == "SQLITE":
            session.execute(text("BEGIN IMMEDIATE"))
        row = session.query(PortfolioConfiguration).filter_by(id=1).with_for_update().first()
        now = dt.datetime.now(dt.timezone.utc)
        if row is None:
            row = PortfolioConfiguration(
                id=1, initial_capital=DEFAULT_PORTFOLIO_CAPITAL_INR,
                max_open_positions=DEFAULT_MAX_OPEN_POSITIONS, created_at=now, updated_at=now
            )
            session.add(row)
            session.flush()
        open_trades = session.query(PaperTrade).filter(PaperTrade.status == "OPEN").all()
        closed_trades = session.query(PaperTrade).filter(PaperTrade.status != "OPEN").all()
        deployed = sum(float(trade.entry_price) * int(trade.quantity) for trade in open_trades)
        realized = sum(float(trade.realized_pnl or 0.0) for trade in closed_trades)
        if capital - deployed + realized < -0.005:
            raise PaperTradeConstraintError(
                f"Portfolio capital cannot be below existing net capital commitments of ₹{deployed - realized:,.2f}."
            )
        row.initial_capital = capital
        row.max_open_positions = max_open_positions
        row.updated_at = now
        session.commit()
        return {"initial_capital": capital, "max_open_positions": max_open_positions}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_portfolio_capital(initial_capital: float) -> float:
    """Backward-compatible capital-only mutator preserving the position limit."""
    current = get_portfolio_configuration()
    saved = set_portfolio_configuration(initial_capital, current["max_open_positions"])
    return float(saved["initial_capital"])


def canonical_position_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if value.startswith("NSE:"):
        value = value[4:]
    return value[:-3] if value.endswith(".NS") else value


def _new_paper_trade(
    symbol: str,
    entry_price: float,
    quantity: int,
    strategy_used: str,
    stop_loss: Optional[float],
    target: Optional[float],
    sector: str,
    tech_score: float,
    fund_score: float,
    sent_score: float,
    weights_used: Optional[Dict[str, float]],
    agent_reasoning: str,
    risk_metadata: Optional[Dict[str, Any]],
) -> PaperTrade:
    metadata = risk_metadata or {}
    now = dt.datetime.now(dt.timezone.utc)
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
    trade.attribution = AgentAttribution(
        tech_score=tech_score, fund_score=fund_score, sent_score=sent_score,
        weights_used=json.dumps(weights_used or {"w_tech": .5, "w_fund": .3, "w_sent": .2}),
        agent_reasoning=agent_reasoning,
    )
    return trade


def add_paper_trade(symbol: str, entry_price: float, quantity: int, strategy_used: str,
                    stop_loss: Optional[float] = None, target: Optional[float] = None,
                    sector: str = "General", tech_score: float = 0.85, fund_score: float = 7.0,
                    sent_score: float = 0.0, weights_used: Optional[Dict[str, float]] = None,
                    agent_reasoning: str = "", risk_metadata: Optional[Dict[str, Any]] = None) -> PaperTrade:
    _require_database()
    session = SessionLocal()
    try:
        trade = _new_paper_trade(
            symbol, entry_price, quantity, strategy_used, stop_loss, target, sector,
            tech_score, fund_score, sent_score, weights_used, agent_reasoning, risk_metadata,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def add_constrained_paper_trade(
    symbol: str,
    entry_price: float,
    quantity: int,
    strategy_used: str,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    sector: str = "General",
    tech_score: float = 0.85,
    fund_score: float = 7.0,
    sent_score: float = 0.0,
    weights_used: Optional[Dict[str, float]] = None,
    agent_reasoning: str = "",
    risk_metadata: Optional[Dict[str, Any]] = None,
) -> PaperTrade:
    """Atomically enforce the live paper-portfolio cash/position contract."""
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise PaperTradeConstraintError("Paper-trade quantity must be at least one whole share.")
    if not isinstance(entry_price, (int, float)) or isinstance(entry_price, bool) or not math.isfinite(float(entry_price)) or entry_price <= 0:
        raise PaperTradeConstraintError("A positive execution/reference price is required.")

    _require_database()
    session = SessionLocal()
    try:
        if DATABASE_BACKEND == "SQLITE":
            session.execute(text("BEGIN IMMEDIATE"))
        configuration = session.query(PortfolioConfiguration).filter_by(id=1).with_for_update().first()
        if configuration is None:
            now = dt.datetime.now(dt.timezone.utc)
            configuration = PortfolioConfiguration(
                id=1, initial_capital=DEFAULT_PORTFOLIO_CAPITAL_INR,
                max_open_positions=DEFAULT_MAX_OPEN_POSITIONS, created_at=now, updated_at=now
            )
            session.add(configuration)
            session.flush()
        capital = float(configuration.initial_capital)
        open_trades = session.query(PaperTrade).filter(PaperTrade.status == "OPEN").all()
        canonical = canonical_position_symbol(symbol)
        if any(canonical_position_symbol(trade.symbol) == canonical for trade in open_trades):
            raise PaperTradeConstraintError(f"An open paper position already exists for {canonical}.")
        max_positions = configuration.max_open_positions
        if max_positions is not None and len(open_trades) >= int(max_positions):
            raise PaperTradeConstraintError(f"The configured maximum of {int(max_positions)} open paper positions has been reached.")

        closed_trades = session.query(PaperTrade).filter(PaperTrade.status != "OPEN").all()
        deployed = sum(float(trade.entry_price) * int(trade.quantity) for trade in open_trades)
        realized = sum(float(trade.realized_pnl or 0.0) for trade in closed_trades)
        available_cash = round(capital - deployed + realized, 2)
        position_value = round(float(entry_price) * quantity, 2)
        if position_value > available_cash + 0.005:
            raise PaperTradeConstraintError(
                f"Insufficient available cash: ₹{available_cash:,.2f} available; ₹{position_value:,.2f} required."
            )

        trade = _new_paper_trade(
            symbol, float(entry_price), quantity, strategy_used, stop_loss, target, sector,
            tech_score, fund_score, sent_score, weights_used, agent_reasoning, risk_metadata,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade
    except Exception:
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
            "executable_stop_heat_pct", "executable_stop_coverage_count", "price_coverage_count", "price_coverage_total")}
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


def persist_historical_analog_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    """Append one HA result once; an existing identity is never rewritten."""
    _require_database()
    signal_date = result["signal_date"]
    if isinstance(signal_date, str):
        signal_date = dt.date.fromisoformat(signal_date)
    identity = {
        "opportunity_id": str(result["opportunity_id"]),
        "signal_date": signal_date,
        "methodology_hash": str(result["methodology_hash"]),
    }
    session = SessionLocal()
    try:
        existing = session.query(HistoricalAnalogSnapshot).filter_by(**identity).first()
        if existing:
            return {"saved": False, "snapshot_id": existing.id}
        summary = {key: value for key, value in result.items() if key != "analogs"}
        row = HistoricalAnalogSnapshot(
            **identity, symbol=str(result["symbol"]), methodology_id=str(result["methodology_id"]),
            evidence_quality=str(result["evidence_quality"]), analog_count=int(result["analog_count"]),
            summary_payload=json.dumps(_json_safe(summary), sort_keys=True, allow_nan=False),
        )
        session.add(row)
        session.flush()
        for analog in result.get("analogs", []):
            analog_date = analog["signal_date"]
            label_end = analog["label_end_date"]
            if isinstance(analog_date, str): analog_date = dt.date.fromisoformat(analog_date)
            if isinstance(label_end, str): label_end = dt.date.fromisoformat(label_end)
            identity_fields = {
                "opportunity_id", "symbol", "signal_date", "label_end_date", "rank",
                "distance", "feature_coverage",
            }
            session.add(HistoricalAnalogMapping(
                snapshot_id=row.id, rank=int(analog["rank"]),
                analog_opportunity_id=str(analog["opportunity_id"]), analog_symbol=str(analog["symbol"]),
                analog_signal_date=analog_date, analog_label_end_date=label_end,
                distance=float(analog["distance"]), feature_coverage=float(analog["feature_coverage"]),
                outcome_payload=json.dumps(_json_safe({k: v for k, v in analog.items() if k not in identity_fields}), sort_keys=True, allow_nan=False),
            ))
        session.commit()
        return {"saved": True, "snapshot_id": row.id}
    except IntegrityError:
        session.rollback()
        existing = session.query(HistoricalAnalogSnapshot).filter_by(**identity).first()
        return {"saved": False, "snapshot_id": existing.id if existing else None}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_historical_analog_snapshot(opportunity_id: str, signal_date: Any, methodology_hash: str) -> Optional[Dict[str, Any]]:
    """Load one inspectable snapshot and its persisted ranked mapping."""
    if isinstance(signal_date, str): signal_date = dt.date.fromisoformat(signal_date)
    if not init_db(): return None
    session = SessionLocal()
    try:
        row = session.query(HistoricalAnalogSnapshot).filter_by(
            opportunity_id=str(opportunity_id), signal_date=signal_date, methodology_hash=str(methodology_hash)
        ).first()
        if not row: return None
        result = json.loads(row.summary_payload)
        mappings = session.query(HistoricalAnalogMapping).filter_by(snapshot_id=row.id).order_by(HistoricalAnalogMapping.rank.asc()).all()
        result["analogs"] = [
            {
                "rank": item.rank, "opportunity_id": item.analog_opportunity_id,
                "symbol": item.analog_symbol, "signal_date": item.analog_signal_date.isoformat(),
                "label_end_date": item.analog_label_end_date.isoformat(), "distance": item.distance,
                "feature_coverage": item.feature_coverage, **json.loads(item.outcome_payload),
            }
            for item in mappings
        ]
        result["snapshot_id"] = row.id
        return result
    finally:
        session.close()


def load_historical_analog_summaries(identities: List[tuple[str, Any]], methodology_hash: str) -> Dict[str, Dict[str, Any]]:
    """Bulk-load summary-only HA evidence for an opportunity table.

    Ranked K40 mappings are intentionally excluded; the selected stock's HA
    view loads those lazily through ``load_historical_analog_snapshot``.
    """
    normalized = []
    for opportunity_id, signal_date in identities:
        if not opportunity_id or not signal_date:
            continue
        parsed_date = signal_date if isinstance(signal_date, dt.date) else dt.date.fromisoformat(str(signal_date)[:10])
        normalized.append((str(opportunity_id), parsed_date))
    if not normalized or not init_db():
        return {}
    opportunity_ids = sorted({item[0] for item in normalized})
    valid_identities = set(normalized)
    session = SessionLocal()
    try:
        rows = session.query(HistoricalAnalogSnapshot).filter(
            HistoricalAnalogSnapshot.methodology_hash == str(methodology_hash),
            HistoricalAnalogSnapshot.opportunity_id.in_(opportunity_ids),
        ).all()
        result = {}
        for row in rows:
            if (row.opportunity_id, row.signal_date) not in valid_identities:
                continue
            summary = json.loads(row.summary_payload)
            summary["snapshot_id"] = row.id
            result[f"{row.opportunity_id}|{row.signal_date.isoformat()}"] = summary
        return result
    finally:
        session.close()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def persist_analysis_run(run: Dict[str, Any], opportunities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Upsert one canonical run and replace only that run's qualified payload."""
    _require_database()
    session = SessionLocal()
    try:
        analysis_date = run.get("analysis_date")
        if isinstance(analysis_date, str):
            analysis_date = dt.date.fromisoformat(analysis_date)
        if not isinstance(analysis_date, dt.date):
            raise ValueError("analysis_date is required")
        now = dt.datetime.now(dt.timezone.utc)
        run_id = str(run.get("run_id") or f"EOD-{analysis_date.isoformat()}")
        row = session.query(AnalysisRun).filter_by(analysis_date=analysis_date).first()
        values = {
            "run_id": run_id, "analysis_date": analysis_date, "started_at": run.get("started_at") or now,
            "completed_at": run.get("completed_at") or now, "status": str(run.get("status", "FAILED")),
            "symbols_requested": int(run.get("symbols_requested") or 0), "symbols_succeeded": int(run.get("symbols_succeeded") or 0),
            "symbols_failed": int(run.get("symbols_failed") or 0), "qualified_count": int(run.get("qualified_count") or 0),
            "allocated_count": int(run.get("allocated_count") or 0), "provider_summary": json.dumps(_json_safe(run.get("provider_summary") or {}), sort_keys=True),
            "decision_contract_version": str(run.get("decision_contract_version") or "V1_1_EOD"),
            "error_summary": run.get("error_summary"), "source": run.get("source") or "MANUAL_REFRESH",
        }
        if row is None:
            row = AnalysisRun(created_at=now, updated_at=now, **values)
            session.add(row)
        else:
            # The unique date is the canonical identity; retries update it in place.
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now
            session.query(DailyOpportunity).filter_by(run_id=row.run_id).delete(synchronize_session=False)
        for candidate in opportunities:
            opportunity_id = str(candidate.get("opportunity_id") or f"{analysis_date.isoformat()}:{candidate.get('symbol')}:{candidate.get('strategy')}")
            payload = dict(candidate); payload["opportunity_id"] = opportunity_id
            session.add(DailyOpportunity(
                run_id=row.run_id, analysis_date=analysis_date, opportunity_id=opportunity_id,
                symbol=str(candidate.get("symbol") or ""), strategy=str(candidate.get("strategy") or "NOT_AVAILABLE"),
                priority=candidate.get("opportunity_priority_rank"), volume_ratio_20=candidate.get("volume_ratio_20"),
                qualification_status=str(candidate.get("qualification_status") or "QUALIFIED"), entry_price=candidate.get("entry_price"),
                allocation_status=candidate.get("allocation_status"), allocation_reason=candidate.get("allocation_reason_code"),
                decision_payload=json.dumps(_json_safe(payload), sort_keys=True, default=str), created_at=now,
            ))
        session.commit(); session.refresh(row)
        return {"run_id": row.run_id, "saved": True}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def load_latest_analysis_run() -> Optional[Dict[str, Any]]:
    """Read the newest valid completed run and its canonical decision payloads."""
    if not init_db():
        return None
    session = SessionLocal()
    try:
        row = session.query(AnalysisRun).filter(AnalysisRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS"])).order_by(AnalysisRun.analysis_date.desc(), AnalysisRun.completed_at.desc()).first()
        if not row:
            return None
        items = session.query(DailyOpportunity).filter_by(run_id=row.run_id).order_by(DailyOpportunity.priority.asc(), DailyOpportunity.symbol.asc()).all()
        return {
            "run_id": row.run_id, "analysis_date": row.analysis_date.isoformat(), "started_at": row.started_at.isoformat() if row.started_at else None,
            "scan_completed_at": row.completed_at.isoformat() if row.completed_at else None, "status": row.status,
            "symbols_requested": row.symbols_requested, "symbols_succeeded": row.symbols_succeeded, "symbols_failed": row.symbols_failed,
            "qualified_count": row.qualified_count, "allocated_count": row.allocated_count, "source": row.source,
            "provider_summary": json.loads(row.provider_summary or "{}"), "decisions": [json.loads(item.decision_payload) for item in items],
        }
    except (SQLAlchemyError, ValueError, TypeError):
        return None
    finally:
        session.close()


def load_canonical_opportunity_history() -> List[Dict[str, Any]]:
    """Return frozen qualified payloads for conservative ledger backfill."""
    rows = _safe_read(lambda s: s.query(DailyOpportunity).order_by(
        DailyOpportunity.analysis_date.asc(), DailyOpportunity.id.asc()
    ).all())
    result = []
    for row in rows:
        try:
            payload = json.loads(row.decision_payload)
        except (TypeError, ValueError):
            continue
        payload.setdefault("opportunity_id", row.opportunity_id)
        payload.setdefault("signal_date", row.analysis_date.isoformat())
        payload["canonical_created_at"] = row.created_at.isoformat() if row.created_at else None
        result.append(payload)
    return result


def persist_recommendation_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Insert one immutable ledger row; identical retries are idempotent."""
    _require_database(); session = SessionLocal()
    try:
        signal_date = snapshot.get("signal_date")
        if isinstance(signal_date, str):
            signal_date = dt.date.fromisoformat(signal_date[:10])
        signal_timestamp = _context_timestamp(snapshot.get("signal_timestamp"))
        vector = _json_safe(snapshot.get("lsv_v1") or {})
        opportunity_id = str(snapshot.get("opportunity_id") or "").strip()
        methodology_hash = str(vector.get("methodology_hash") or "").strip()
        if not opportunity_id or not methodology_hash or not isinstance(signal_date, dt.date):
            raise ValueError("opportunity_id, signal_date and LSV methodology hash are required")
        content = {
            "opportunity_id": opportunity_id, "signal_date": signal_date.isoformat(),
            "signal_timestamp": signal_timestamp.isoformat(), "symbol": str(snapshot.get("symbol") or ""),
            "strategy": str(snapshot.get("strategy") or "NOT_AVAILABLE"),
            "reference_price": snapshot.get("reference_price"),
            "allocator_status": snapshot.get("allocator_status"), "opportunity_rank": snapshot.get("opportunity_rank"),
            "lsv_v1": vector, "historical_analog": _json_safe(snapshot.get("historical_analog") or {}),
            "market_context": _json_safe(snapshot.get("market_context") or {}),
            "methodologies": _json_safe(snapshot.get("methodologies") or {}),
            "source_timestamps": _json_safe(snapshot.get("source_timestamps") or {}),
            "missingness": _json_safe(snapshot.get("missingness") or []),
            "provenance": _json_safe(snapshot.get("provenance") or []),
        }
        # Retry time is not recommendation state.  Preserve the first timestamp
        # while treating an otherwise identical retry as idempotent.
        hash_content = dict(content); hash_content.pop("signal_timestamp", None)
        snapshot_hash = hashlib.sha256(json.dumps(hash_content, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        existing = session.query(RecommendationLedger).filter_by(
            opportunity_id=opportunity_id, lsv_methodology_hash=methodology_hash
        ).first()
        if existing:
            if existing.snapshot_hash != snapshot_hash:
                raise RecommendationLedgerConflictError(
                    f"Immutable recommendation snapshot conflict for {opportunity_id}"
                )
            return {"saved": False, "snapshot_id": existing.id, "snapshot_hash": snapshot_hash}
        row = RecommendationLedger(
            opportunity_id=opportunity_id, signal_date=signal_date, signal_timestamp=signal_timestamp,
            symbol=content["symbol"], strategy=content["strategy"], reference_price=content["reference_price"],
            allocator_status=content["allocator_status"], opportunity_rank=content["opportunity_rank"],
            lsv_contract_version=str(vector.get("contract_version") or "LSV_V1"),
            lsv_methodology_hash=methodology_hash,
            vector_payload=json.dumps(vector, sort_keys=True, default=str),
            historical_analog_payload=json.dumps(content["historical_analog"], sort_keys=True, default=str),
            market_context_payload=json.dumps(content["market_context"], sort_keys=True, default=str),
            methodology_payload=json.dumps(content["methodologies"], sort_keys=True, default=str),
            source_timestamps=json.dumps(content["source_timestamps"], sort_keys=True, default=str),
            missingness=json.dumps(content["missingness"], sort_keys=True, default=str),
            provenance=json.dumps(content["provenance"], sort_keys=True, default=str),
            snapshot_hash=snapshot_hash,
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id, "snapshot_hash": snapshot_hash}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def load_recommendation_snapshot(opportunity_id: str, methodology_hash: str) -> Optional[Dict[str, Any]]:
    rows = _safe_read(lambda s: s.query(RecommendationLedger).filter_by(
        opportunity_id=str(opportunity_id), lsv_methodology_hash=str(methodology_hash)
    ).limit(1).all())
    if not rows:
        return None
    row = rows[0]
    return {
        "snapshot_id": row.id, "opportunity_id": row.opportunity_id,
        "signal_date": row.signal_date.isoformat(), "signal_timestamp": row.signal_timestamp.isoformat(),
        "symbol": row.symbol, "strategy": row.strategy, "reference_price": row.reference_price,
        "allocator_status": row.allocator_status, "opportunity_rank": row.opportunity_rank,
        "lsv_v1": json.loads(row.vector_payload), "historical_analog": json.loads(row.historical_analog_payload),
        "market_context": json.loads(row.market_context_payload), "methodologies": json.loads(row.methodology_payload),
        "source_timestamps": json.loads(row.source_timestamps), "missingness": json.loads(row.missingness),
        "provenance": json.loads(row.provenance), "snapshot_hash": row.snapshot_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def recommendation_ledger_coverage() -> Dict[str, Any]:
    """Summarize stored availability without changing immutable rows."""
    rows = _safe_read(lambda s: s.query(RecommendationLedger.vector_payload).all())
    families: Dict[str, Dict[str, int]] = {}
    for row in rows:
        try:
            vector = json.loads(row.vector_payload)
        except (TypeError, ValueError):
            continue
        for family, values in vector.items():
            if not isinstance(values, dict):
                continue
            stats = families.setdefault(family, {"available_values": 0, "total_values": 0, "rows_with_any": 0})
            available = sum(value not in (None, "NOT_AVAILABLE") for value in values.values())
            stats["available_values"] += available
            stats["total_values"] += len(values)
            stats["rows_with_any"] += int(available > 0)
    return {
        "rows": len(rows),
        "families": {
            family: {**stats, "coverage_pct": round(100.0 * stats["available_values"] / stats["total_values"], 2) if stats["total_values"] else 0.0}
            for family, stats in sorted(families.items())
        },
    }


def load_recommendations_for_role_observation(outcome_methodology_hash: str) -> List[Dict[str, Any]]:
    """Read immutable recommendations whose ROLE lifecycle is not yet mature."""
    if not init_db():
        return []
    session = SessionLocal()
    try:
        observations = {
            (row.opportunity_id, row.lsv_methodology_hash): row.lifecycle_state
            for row in session.query(RoleOutcomeObservation).filter_by(
                outcome_methodology_hash=str(outcome_methodology_hash)
            ).all()
        }
        rows = session.query(RecommendationLedger).order_by(
            RecommendationLedger.signal_date.asc(), RecommendationLedger.id.asc()
        ).all()
        result = []
        for row in rows:
            if observations.get((row.opportunity_id, row.lsv_methodology_hash)) == "MATURE":
                continue
            result.append({
                "opportunity_id": row.opportunity_id, "lsv_methodology_hash": row.lsv_methodology_hash,
                "signal_date": row.signal_date.isoformat(), "signal_timestamp": row.signal_timestamp.isoformat(),
                "symbol": row.symbol, "reference_price": row.reference_price,
                "recommendation_snapshot_hash": row.snapshot_hash,
            })
        return result
    finally:
        session.close()


def persist_role_observation_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Create or advance a ROLE lifecycle header without touching its recommendation."""
    _require_database(); session = SessionLocal()
    try:
        opportunity_id = str(state["opportunity_id"]); lsv_hash = str(state["lsv_methodology_hash"])
        outcome_hash = str(state["outcome_methodology_hash"])
        signal_date = dt.date.fromisoformat(str(state["signal_date"])[:10])
        raw_reference = state.get("reference_price")
        reference_price = float(raw_reference) if raw_reference is not None else None
        if reference_price is not None and (not math.isfinite(reference_price) or reference_price <= 0):
            reference_price = None
        row = session.query(RoleOutcomeObservation).filter_by(
            opportunity_id=opportunity_id, lsv_methodology_hash=lsv_hash,
            outcome_methodology_hash=outcome_hash,
        ).first()
        now = dt.datetime.now(dt.timezone.utc)
        observation_date = state.get("last_observation_date")
        if observation_date:
            observation_date = dt.date.fromisoformat(str(observation_date)[:10])
        values = {
            "outcome_contract_version": str(state["outcome_contract_version"]),
            "lifecycle_state": str(state["lifecycle_state"]),
            "sessions_observed": int(state.get("sessions_observed") or 0),
            "last_observation_date": observation_date,
            "source_payload": json.dumps(_json_safe(state.get("source") or {}), sort_keys=True),
            "completeness": json.dumps(_json_safe(state.get("completeness") or {}), sort_keys=True),
            "missingness": json.dumps(_json_safe(state.get("missingness") or []), sort_keys=True),
        }
        if row is None:
            row = RoleOutcomeObservation(
                opportunity_id=opportunity_id, lsv_methodology_hash=lsv_hash,
                signal_date=signal_date, reference_price=reference_price,
                outcome_methodology_hash=outcome_hash, created_at=now, updated_at=now, **values,
            )
            session.add(row)
        else:
            reference_matches = (row.reference_price is None and reference_price is None) or (
                row.reference_price is not None and reference_price is not None
                and math.isclose(row.reference_price, reference_price, rel_tol=0, abs_tol=1e-9)
            )
            if row.signal_date != signal_date or not reference_matches:
                raise RoleOutcomeConflictError("ROLE observation identity disagrees with immutable recommendation")
            if row.lifecycle_state != "MATURE":
                for key, value in values.items():
                    setattr(row, key, value)
                row.updated_at = now
        session.commit(); session.refresh(row)
        return {"observation_id": row.id, "lifecycle_state": row.lifecycle_state}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def persist_role_outcome_horizon(observation_id: int, horizon_sessions: int,
                                 observation_date: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Insert one matured horizon; retries must reproduce identical content."""
    _require_database(); session = SessionLocal()
    try:
        horizon = int(horizon_sessions)
        date_value = dt.date.fromisoformat(str(observation_date)[:10])
        safe_payload = _json_safe(payload)
        payload_hash = hashlib.sha256(json.dumps(safe_payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        existing = session.query(RoleOutcomeHorizon).filter_by(
            observation_id=int(observation_id), horizon_sessions=horizon
        ).first()
        if existing:
            if existing.payload_hash != payload_hash:
                raise RoleOutcomeConflictError(f"Matured ROLE horizon {horizon} changed")
            return {"saved": False, "horizon_id": existing.id, "payload_hash": payload_hash}
        row = RoleOutcomeHorizon(
            observation_id=int(observation_id), horizon_sessions=horizon,
            observation_date=date_value, payload=json.dumps(safe_payload, sort_keys=True, default=str),
            payload_hash=payload_hash,
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "horizon_id": row.id, "payload_hash": payload_hash}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def load_role_outcome_observation(opportunity_id: str, lsv_methodology_hash: str,
                                  outcome_methodology_hash: str) -> Optional[Dict[str, Any]]:
    if not init_db():
        return None
    session = SessionLocal()
    try:
        row = session.query(RoleOutcomeObservation).filter_by(
            opportunity_id=str(opportunity_id), lsv_methodology_hash=str(lsv_methodology_hash),
            outcome_methodology_hash=str(outcome_methodology_hash),
        ).first()
        if not row:
            return None
        horizons = session.query(RoleOutcomeHorizon).filter_by(observation_id=row.id).order_by(
            RoleOutcomeHorizon.horizon_sessions.asc()
        ).all()
        return {
            "observation_id": row.id, "opportunity_id": row.opportunity_id,
            "lsv_methodology_hash": row.lsv_methodology_hash, "signal_date": row.signal_date.isoformat(),
            "reference_price": row.reference_price, "outcome_contract_version": row.outcome_contract_version,
            "outcome_methodology_hash": row.outcome_methodology_hash, "lifecycle_state": row.lifecycle_state,
            "sessions_observed": row.sessions_observed,
            "last_observation_date": row.last_observation_date.isoformat() if row.last_observation_date else None,
            "source": json.loads(row.source_payload), "completeness": json.loads(row.completeness),
            "missingness": json.loads(row.missingness),
            "horizons": {str(item.horizon_sessions): json.loads(item.payload) for item in horizons},
        }
    finally:
        session.close()


def role_outcome_lifecycle_counts(outcome_methodology_hash: str) -> Dict[str, int]:
    rows = _safe_read(lambda s: s.query(
        RoleOutcomeObservation.lifecycle_state, func.count(RoleOutcomeObservation.id)
    ).filter_by(outcome_methodology_hash=str(outcome_methodology_hash)).group_by(
        RoleOutcomeObservation.lifecycle_state
    ).all())
    counts = {state: 0 for state in ("PENDING", "PARTIAL", "MATURE", "NOT_AVAILABLE")}
    for state, count in rows:
        counts[str(state)] = int(count)
    counts["TOTAL"] = sum(counts.values())
    return counts


def _context_timestamp(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def persist_market_context_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one immutable structural snapshot per market date/version."""
    _require_database(); session = SessionLocal()
    try:
        as_of_date = dt.date.fromisoformat(str(payload["as_of_date"])[:10])
        version = str(payload["methodology_version"])
        existing = session.query(MarketContextSnapshot).filter_by(as_of_date=as_of_date, methodology_version=version).first()
        if existing:
            return {"saved": False, "snapshot_id": existing.id}
        row = MarketContextSnapshot(
            as_of_date=as_of_date, as_of_timestamp=_context_timestamp(payload.get("as_of_timestamp")), methodology_version=version,
            payload=json.dumps(_json_safe(payload), sort_keys=True), coverage=json.dumps(_json_safe(payload.get("coverage") or {}), sort_keys=True),
            missingness=json.dumps(_json_safe(payload.get("missingness") or [])), provenance=json.dumps(_json_safe(payload.get("provenance") or [])),
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def persist_investor_participation_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_database(); session = SessionLocal()
    try:
        observation_date = dt.date.fromisoformat(str(payload.get("observation_date") or dt.datetime.now(dt.timezone.utc).date())[:10])
        version = str(payload["methodology_version"])
        existing = session.query(InvestorParticipationSnapshot).filter_by(observation_date=observation_date, methodology_version=version).first()
        if existing:
            return {"saved": False, "snapshot_id": existing.id}
        row = InvestorParticipationSnapshot(
            observation_date=observation_date, as_of_timestamp=_context_timestamp(payload.get("as_of_timestamp")), methodology_version=version,
            state=str(payload.get("state") or "NOT_AVAILABLE"), payload=json.dumps(_json_safe(payload), sort_keys=True),
            coverage=str(payload.get("coverage") or "NOT_AVAILABLE"), missingness=json.dumps(_json_safe(payload.get("missingness") or [])),
            provenance=json.dumps(_json_safe(payload.get("provenance") or [])),
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def load_investor_participation_snapshots(limit: int = 20) -> List[Dict[str, Any]]:
    rows = _safe_read(lambda s: s.query(InvestorParticipationSnapshot).order_by(InvestorParticipationSnapshot.observation_date.desc()).limit(limit).all())
    return [json.loads(row.payload) for row in rows]


def _phase_snapshot_key(timestamp: dt.datetime, phase: str) -> str:
    phase = str(phase).upper()
    return f"{timestamp.date().isoformat()}:{phase}" if phase == "PREOPEN" else f"{timestamp.date().isoformat()}:{phase}:{timestamp.hour:02d}"


def persist_cross_asset_snapshot(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    _require_database(); session = SessionLocal()
    try:
        timestamp = _context_timestamp(payload.get("as_of_timestamp")); version = str(payload["methodology_version"]); key = _phase_snapshot_key(timestamp, phase)
        existing = session.query(CrossAssetSnapshot).filter_by(snapshot_key=key, methodology_version=version).first()
        if existing:
            return {"saved": False, "snapshot_id": existing.id}
        row = CrossAssetSnapshot(
            snapshot_key=key, as_of_timestamp=timestamp, refresh_phase=str(phase).upper(), methodology_version=version,
            state=str(payload.get("state") or "NOT_AVAILABLE"), payload=json.dumps(_json_safe(payload), sort_keys=True),
            coverage=json.dumps({"available_core_inputs": payload.get("available_core_inputs"), "required_core_inputs": payload.get("required_core_inputs")}, sort_keys=True),
            missingness=json.dumps(_json_safe(payload.get("missingness") or [])), provenance=json.dumps(_json_safe(payload.get("provenance") or [])),
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def persist_event_risk_snapshot(payload: Dict[str, Any], phase: str) -> Dict[str, Any]:
    _require_database(); session = SessionLocal()
    try:
        timestamp = _context_timestamp(payload.get("as_of_timestamp")); version = str(payload["methodology_version"]); key = _phase_snapshot_key(timestamp, phase)
        for event in payload.get("events") or []:
            event_id = str(event.get("event_id") or "")
            if not event_id or session.query(EventRiskEvent).filter_by(event_id=event_id).first():
                continue
            session.add(EventRiskEvent(
                event_id=event_id, event_timestamp=_context_timestamp(event.get("event_timestamp")), scheduled=bool(event.get("scheduled")),
                event_type=str(event.get("event_type") or "NOT_AVAILABLE"), india_relevance=str(event.get("india_relevance") or "NOT_AVAILABLE"),
                magnitude=str(event.get("magnitude") or "NOT_AVAILABLE"), direction=str(event.get("direction") or "UNCLEAR"),
                status=str(event.get("status") or "ACTIVE"), payload=json.dumps(_json_safe(event), sort_keys=True), methodology_version=version,
            ))
        existing = session.query(EventRiskSnapshot).filter_by(snapshot_key=key, methodology_version=version).first()
        if existing:
            session.rollback()
            return {"saved": False, "snapshot_id": existing.id}
        row = EventRiskSnapshot(
            snapshot_key=key, as_of_timestamp=timestamp, refresh_phase=str(phase).upper(), methodology_version=version,
            state=str(payload.get("state") or "NOT_AVAILABLE"), payload=json.dumps(_json_safe(payload), sort_keys=True),
            coverage=str(payload.get("coverage") or "NOT_AVAILABLE"), missingness=json.dumps(_json_safe(payload.get("missingness") or [])),
            provenance=json.dumps(_json_safe([item for diagnostic in payload.get("source_diagnostics") or [] for item in [diagnostic.get("source")]])),
        )
        session.add(row); session.commit(); session.refresh(row)
        return {"saved": True, "snapshot_id": row.id}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def load_latest_market_context_bundle() -> Dict[str, Any]:
    """Read-only latest snapshots; never invokes market or news providers."""
    if not init_db():
        return {"structural": None, "investor_participation": None, "cross_asset": None, "event_risk": None}
    session = SessionLocal()
    try:
        structural = session.query(MarketContextSnapshot).order_by(MarketContextSnapshot.as_of_date.desc(), MarketContextSnapshot.created_at.desc()).first()
        investor = session.query(InvestorParticipationSnapshot).order_by(InvestorParticipationSnapshot.observation_date.desc(), InvestorParticipationSnapshot.created_at.desc()).first()
        cross = session.query(CrossAssetSnapshot).order_by(CrossAssetSnapshot.as_of_timestamp.desc(), CrossAssetSnapshot.created_at.desc()).first()
        event = session.query(EventRiskSnapshot).order_by(EventRiskSnapshot.as_of_timestamp.desc(), EventRiskSnapshot.created_at.desc()).first()
        return {
            "structural": json.loads(structural.payload) if structural else None,
            "investor_participation": json.loads(investor.payload) if investor else None,
            "cross_asset": json.loads(cross.payload) if cross else None,
            "event_risk": json.loads(event.payload) if event else None,
        }
    except (SQLAlchemyError, ValueError, TypeError):
        return {"structural": None, "investor_participation": None, "cross_asset": None, "event_risk": None}
    finally:
        session.close()


def load_market_context_bundle_as_of(as_of_date: Any, as_of_timestamp: Any) -> Dict[str, Any]:
    """Load only Market Context snapshots known by the recommendation cutoff."""
    if not init_db():
        return {"structural": None, "investor_participation": None, "cross_asset": None, "event_risk": None}
    date_value = dt.date.fromisoformat(str(as_of_date)[:10])
    timestamp = _context_timestamp(as_of_timestamp)
    session = SessionLocal()
    try:
        structural = session.query(MarketContextSnapshot).filter(
            MarketContextSnapshot.as_of_date <= date_value,
            MarketContextSnapshot.as_of_timestamp <= timestamp,
        ).order_by(MarketContextSnapshot.as_of_date.desc(), MarketContextSnapshot.created_at.desc()).first()
        investor = session.query(InvestorParticipationSnapshot).filter(
            InvestorParticipationSnapshot.observation_date <= date_value,
            InvestorParticipationSnapshot.as_of_timestamp <= timestamp,
        ).order_by(InvestorParticipationSnapshot.observation_date.desc(), InvestorParticipationSnapshot.created_at.desc()).first()
        cross = session.query(CrossAssetSnapshot).filter(
            CrossAssetSnapshot.as_of_timestamp <= timestamp,
        ).order_by(CrossAssetSnapshot.as_of_timestamp.desc(), CrossAssetSnapshot.created_at.desc()).first()
        event = session.query(EventRiskSnapshot).filter(
            EventRiskSnapshot.as_of_timestamp <= timestamp,
        ).order_by(EventRiskSnapshot.as_of_timestamp.desc(), EventRiskSnapshot.created_at.desc()).first()
        return {
            "structural": json.loads(structural.payload) if structural else None,
            "investor_participation": json.loads(investor.payload) if investor else None,
            "cross_asset": json.loads(cross.payload) if cross else None,
            "event_risk": json.loads(event.payload) if event else None,
        }
    except (SQLAlchemyError, ValueError, TypeError):
        return {"structural": None, "investor_participation": None, "cross_asset": None, "event_risk": None}
    finally:
        session.close()


def persist_position_marks(marks: List[Dict[str, Any]], source_run_id: Optional[str] = None) -> Dict[str, int]:
    """Daily mark upsert keyed by trade/date; unavailable marks are never fabricated."""
    _require_database()
    session = SessionLocal(); saved = 0
    try:
        for mark in marks:
            if not isinstance(mark.get("mark_price"), (int, float)) or mark["mark_price"] <= 0:
                continue
            mark_date = mark.get("mark_date") or dt.datetime.now(dt.timezone.utc).date()
            if isinstance(mark_date, str): mark_date = dt.date.fromisoformat(mark_date)
            marked_at = mark.get("marked_at") or dt.datetime.now(dt.timezone.utc)
            existing = session.query(PositionMark).filter_by(trade_id=int(mark["trade_id"]), mark_date=mark_date).first()
            values = {"symbol": str(mark.get("symbol") or ""), "mark_price": float(mark["mark_price"]), "marked_at": marked_at,
                      "provider": str(mark.get("provider") or "YFINANCE"), "mark_status": str(mark.get("mark_status") or "AVAILABLE"),
                      "source_run_id": source_run_id or mark.get("source_run_id")}
            if existing:
                for key, value in values.items(): setattr(existing, key, value)
                existing.updated_at = dt.datetime.now(dt.timezone.utc)
            else:
                session.add(PositionMark(trade_id=int(mark["trade_id"]), mark_date=mark_date, created_at=dt.datetime.now(dt.timezone.utc), **values))
            saved += 1
        session.commit(); return {"saved": saved}
    except Exception:
        session.rollback(); raise
    finally:
        session.close()


def get_latest_position_marks() -> Dict[int, Dict[str, Any]]:
    rows = _safe_read(lambda s: s.query(PositionMark).order_by(PositionMark.trade_id.asc(), PositionMark.mark_date.desc(), PositionMark.marked_at.desc()).all())
    latest: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if row.trade_id not in latest:
            latest[row.trade_id] = {"mark_price": row.mark_price, "mark_date": row.mark_date.isoformat(), "marked_at": row.marked_at.isoformat(), "provider": row.provider, "mark_status": row.mark_status, "source_run_id": row.source_run_id}
    return latest


def _persisted_position_row(trade: PaperTrade, mark: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Serialize one durable open position without any provider access."""
    attr = trade.attribution
    current = mark.get("mark_price") if mark else None
    return {
        "id": trade.id, "symbol": trade.symbol, "sector": trade.sector,
        "entry_date": trade.entry_date.strftime("%Y-%m-%d %H:%M") if trade.entry_date else "",
        "entry_price": trade.entry_price, "quantity": trade.quantity,
        "position_value": trade.position_value or trade.entry_price * trade.quantity,
        "stop_loss": trade.stop_loss, "target": trade.target, "current_price": current,
        "price_status": "AVAILABLE" if current is not None else "PRICE_NOT_AVAILABLE",
        "unrealized_pnl_inr": round((current - trade.entry_price) * trade.quantity, 2) if current is not None else None,
        "unrealized_pnl_pct": round((current - trade.entry_price) / trade.entry_price * 100, 2) if current is not None and trade.entry_price else None,
        "mark_date": mark.get("mark_date") if mark else None, "marked_at": mark.get("marked_at") if mark else None,
        "mark_provider": mark.get("provider") if mark else None,
        "mark_status": mark.get("mark_status") if mark else "PRICE_NOT_AVAILABLE",
        "strategy_used": trade.strategy_used, "tech_score": attr.tech_score if attr else .85,
        "fund_score": attr.fund_score if attr else 7., "sent_score": attr.sent_score if attr else 0.,
        "reasoning": attr.agent_reasoning if attr else "", "risk_contract_version": trade.risk_contract_version,
        "allocation_status": trade.allocation_status, "opportunity_reference": trade.opportunity_reference,
        "risk_reference_type": trade.risk_reference_type, "risk_reference_value": trade.risk_reference_value,
        "risk_reference_available": bool(trade.risk_reference_available) if trade.risk_contract_version else False,
        "reference_risk_per_share": trade.reference_risk_per_share, "reference_risk_rupees": trade.reference_risk_rupees,
        "executable_stop_enabled": bool(trade.executable_stop_enabled) if trade.risk_contract_version else False,
        "initial_executable_stop": trade.initial_executable_stop, "executable_risk_per_share": trade.executable_risk_per_share,
        "executable_risk_rupees": trade.executable_risk_rupees,
        "gap_risk_possible": bool(trade.gap_risk_possible) if trade.risk_contract_version else False,
        "target_status": trade.target_status or "NOT_AVAILABLE",
        "risk_metadata_status": "AVAILABLE" if trade.risk_contract_version else "RISK_METADATA_NOT_AVAILABLE",
    }


def get_portfolio_read_model() -> Dict[str, Any]:
    """Bulk portfolio read model for page rendering (three bounded SQL reads).

    Trade/configuration writes remain transactional and authoritative. This
    function only eliminates overlapping UI reads and never contacts a price
    provider.
    """
    if not init_db():
        return {
            "positions": [],
            "configuration": {"initial_capital": DEFAULT_PORTFOLIO_CAPITAL_INR,
                              "max_open_positions": DEFAULT_MAX_OPEN_POSITIONS},
            "performance": {"open_trades_count": 0, "closed_trades_count": 0,
                            "winning_trades_count": 0, "win_rate_pct": 0.0,
                            "total_realized_pnl": 0.0, "open_capital_deployed": 0.0},
        }
    session = SessionLocal()
    try:
        trades = session.query(PaperTrade).options(joinedload(PaperTrade.attribution)).order_by(PaperTrade.id.asc()).all()
        open_trades = [trade for trade in trades if trade.status == "OPEN"]
        closed_trades = [trade for trade in trades if trade.status != "OPEN"]
        open_ids = [trade.id for trade in open_trades]
        mark_rows = (
            session.query(PositionMark)
            .filter(PositionMark.trade_id.in_(open_ids))
            .order_by(PositionMark.trade_id.asc(), PositionMark.mark_date.desc(), PositionMark.marked_at.desc())
            .all()
            if open_ids else []
        )
        latest: Dict[int, Dict[str, Any]] = {}
        for mark in mark_rows:
            if mark.trade_id not in latest:
                latest[mark.trade_id] = {
                    "mark_price": mark.mark_price, "mark_date": mark.mark_date.isoformat(),
                    "marked_at": mark.marked_at.isoformat(), "provider": mark.provider,
                    "mark_status": mark.mark_status, "source_run_id": mark.source_run_id,
                }
        config = session.query(PortfolioConfiguration).filter_by(id=1).first()
        configuration = {
            "initial_capital": float(config.initial_capital) if config else DEFAULT_PORTFOLIO_CAPITAL_INR,
            "max_open_positions": config.max_open_positions if config else DEFAULT_MAX_OPEN_POSITIONS,
        }
        realized = sum(float(trade.realized_pnl or 0.0) for trade in closed_trades)
        winners = sum(float(trade.realized_pnl or 0.0) > 0 for trade in closed_trades)
        deployed = sum(float(trade.entry_price) * int(trade.quantity) for trade in open_trades)
        return {
            "positions": [_persisted_position_row(trade, latest.get(trade.id)) for trade in open_trades],
            "configuration": configuration,
            "performance": {
                "open_trades_count": len(open_trades), "closed_trades_count": len(closed_trades),
                "winning_trades_count": winners,
                "win_rate_pct": round(winners / len(closed_trades) * 100, 1) if closed_trades else 0.0,
                "total_realized_pnl": round(realized, 2), "open_capital_deployed": round(deployed, 2),
            },
        }
    except SQLAlchemyError:
        return {"positions": [], "configuration": {"initial_capital": DEFAULT_PORTFOLIO_CAPITAL_INR,
                "max_open_positions": DEFAULT_MAX_OPEN_POSITIONS}, "performance": {}}
    finally:
        session.close()


def sync_paper_trades() -> Dict[str, Any]:
    """Retain the pre-existing legacy close behavior; F2 records remain manual."""
    open_trades = get_open_trades()
    if not open_trades:
        return {"updated_count": 0, "closed_target": 0, "closed_sl": 0, "details": [], "price_unavailable": []}
    bars, unavailable, details = {}, [], []
    for symbol in sorted({trade.symbol for trade in open_trades}):
        try:
            data = yf.Ticker(yahoo_nse_symbol(symbol)).history(period="5d", interval="1d", auto_adjust=True)
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


def refresh_open_trade_marks(source_run_id: Optional[str] = None, mark_fetcher=fetch_latest_yahoo_marks) -> Dict[str, Any]:
    """Refresh OPEN trades only; successful marks persist and failures retain prior marks."""
    trades = get_open_trades()
    fetched = mark_fetcher(sorted({trade.symbol for trade in trades}))
    marks = fetched.get("marks", {})
    marked_at = dt.datetime.now(dt.timezone.utc)
    rows = [
        {"trade_id": trade.id, "symbol": trade.symbol, "mark_price": marks[trade.symbol]["mark_price"],
         "mark_date": marks[trade.symbol]["mark_date"], "marked_at": marked_at,
         "provider": "YFINANCE", "mark_status": "LATEST_PROVIDER_MARK"}
        for trade in trades if trade.symbol in marks
    ]
    persist_position_marks(rows, source_run_id=source_run_id)
    positions = get_open_trades_persisted()
    successful_trade_ids = {row["trade_id"] for row in rows}
    return {
        "positions": positions,
        "open_positions": len(trades),
        "unique_symbols": fetched.get("unique_symbols", 0),
        "provider_calls": fetched.get("provider_calls", 0),
        "successful_marks": len(successful_trade_ids),
        "failed_marks": len(trades) - len(successful_trade_ids),
        "price_unavailable": fetched.get("failed_symbols", []),
        "elapsed_seconds": fetched.get("elapsed_seconds", 0.0),
        "marked_at": marked_at.isoformat(),
    }


def get_open_trades_with_live_data(source_run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Compatibility wrapper for callers that need refreshed position rows only."""
    return refresh_open_trade_marks(source_run_id=source_run_id)["positions"]


def _legacy_get_open_trades_with_live_data() -> List[Dict[str, Any]]:
    """Compatibility implementation retained for historical reference only."""
    trades = get_open_trades()
    prices: Dict[str, float] = {}
    for symbol in {trade.symbol for trade in trades}:
        try:
            data = yf.Ticker(yahoo_nse_symbol(symbol)).history(period="5d", interval="1d", auto_adjust=True)
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


def get_open_trades_persisted() -> List[Dict[str, Any]]:
    """Load durable position state without requesting a current market price."""
    rows = []
    latest_marks = get_latest_position_marks()
    for trade in get_open_trades():
        attr = trade.attribution
        mark = latest_marks.get(trade.id)
        current = mark.get("mark_price") if mark else None
        rows.append({"id": trade.id, "symbol": trade.symbol, "sector": trade.sector,
            "entry_date": trade.entry_date.strftime("%Y-%m-%d %H:%M") if trade.entry_date else "", "entry_price": trade.entry_price,
            "quantity": trade.quantity, "position_value": trade.position_value or trade.entry_price * trade.quantity,
            "stop_loss": trade.stop_loss, "target": trade.target, "current_price": current,
            "price_status": "AVAILABLE" if current is not None else "PRICE_NOT_AVAILABLE",
            "unrealized_pnl_inr": round((current - trade.entry_price) * trade.quantity, 2) if current is not None else None,
            "unrealized_pnl_pct": round((current - trade.entry_price) / trade.entry_price * 100, 2) if current is not None and trade.entry_price else None,
            "mark_date": mark.get("mark_date") if mark else None, "marked_at": mark.get("marked_at") if mark else None,
            "mark_provider": mark.get("provider") if mark else None, "mark_status": mark.get("mark_status") if mark else "PRICE_NOT_AVAILABLE", "strategy_used": trade.strategy_used,
            "tech_score": attr.tech_score if attr else .85, "fund_score": attr.fund_score if attr else 7., "sent_score": attr.sent_score if attr else 0.,
            "reasoning": attr.agent_reasoning if attr else "", "risk_contract_version": trade.risk_contract_version,
            "allocation_status": trade.allocation_status, "opportunity_reference": trade.opportunity_reference,
            "risk_reference_type": trade.risk_reference_type, "risk_reference_value": trade.risk_reference_value,
            "risk_reference_available": bool(trade.risk_reference_available) if trade.risk_contract_version else False,
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

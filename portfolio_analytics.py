"""Mark-based paper-portfolio P&L and stock attribution for production views."""
from __future__ import annotations

import datetime as dt
from calendar import monthrange
from typing import Any, Optional

from database import PaperTrade, PositionMark, SessionLocal, get_portfolio_configuration, init_db


PERIODS = {"LIFETIME", "YTD", "1Y", "6M", "3M", "1M", "CUSTOM"}


def _date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _subtract_months(value: dt.date, months: int) -> dt.date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return dt.date(year, month, min(value.day, monthrange(year, month)[1]))


def resolve_period(period: str, as_of_date: Any = None, custom_start: Any = None, custom_end: Any = None) -> tuple[Optional[dt.date], dt.date]:
    period = str(period or "LIFETIME").upper()
    if period not in PERIODS:
        raise ValueError(f"Unsupported portfolio period: {period}")
    end = _date(custom_end if period == "CUSTOM" else as_of_date) or dt.date.today()
    if period == "LIFETIME": start = None
    elif period == "YTD": start = dt.date(end.year, 1, 1)
    elif period == "1Y": start = dt.date(end.year - 1, end.month, min(end.day, monthrange(end.year - 1, end.month)[1]))
    elif period == "6M": start = _subtract_months(end, 6)
    elif period == "3M": start = _subtract_months(end, 3)
    elif period == "1M": start = _subtract_months(end, 1)
    else:
        start = _date(custom_start)
        if start is None:
            raise ValueError("CUSTOM portfolio period requires custom_start.")
    if start is not None and start > end:
        raise ValueError("Portfolio period start cannot be after its end.")
    return start, end


def _latest_mark(marks: list[PositionMark], boundary: dt.date, entry_date: dt.date) -> Optional[PositionMark]:
    eligible = [mark for mark in marks if entry_date <= mark.mark_date <= boundary and mark.mark_status != "PRICE_NOT_AVAILABLE"]
    return max(eligible, key=lambda mark: (mark.mark_date, mark.marked_at)) if eligible else None


def get_portfolio_pnl(period: str = "LIFETIME", *, as_of_date: Any = None,
                      custom_start: Any = None, custom_end: Any = None) -> dict[str, Any]:
    """Attribute changes in position P&L between persisted mark boundaries.

    Positions opened during the selected period start at zero P&L. Positions
    already open at the boundary require a persisted start mark. A historical
    end likewise requires a mark for positions not yet closed. Missing marks
    are reported as NOT_AVAILABLE and are never replaced by entry prices.
    """
    start, end = resolve_period(period, as_of_date, custom_start, custom_end)
    if not init_db():
        return {"status": "NOT_AVAILABLE", "reason": "DATABASE_NOT_AVAILABLE"}
    session = SessionLocal()
    try:
        trades = session.query(PaperTrade).order_by(PaperTrade.id.asc()).all()
        mark_rows = session.query(PositionMark).order_by(PositionMark.trade_id.asc(), PositionMark.mark_date.asc()).all()
        marks_by_trade: dict[int, list[PositionMark]] = {}
        for mark in mark_rows:
            marks_by_trade.setdefault(mark.trade_id, []).append(mark)

        rows = []
        missing = []
        for trade in trades:
            entry = _date(trade.entry_date)
            exit_date = _date(trade.exit_date)
            if entry is None or entry > end or (start is not None and exit_date is not None and exit_date < start):
                continue
            marks = marks_by_trade.get(trade.id, [])
            start_pnl = 0.0
            start_mark_date = None
            row_missing = []
            if start is not None and entry < start and (exit_date is None or exit_date >= start):
                start_mark = _latest_mark(marks, start, entry)
                if start_mark is None:
                    row_missing.append("START_MARK")
                else:
                    start_pnl = (float(start_mark.mark_price) - float(trade.entry_price)) * int(trade.quantity)
                    start_mark_date = start_mark.mark_date.isoformat()

            realized_in_period = 0.0
            end_mark_date = None
            if exit_date is not None and exit_date <= end:
                end_pnl = float(trade.realized_pnl if trade.realized_pnl is not None else
                                (float(trade.exit_price) - float(trade.entry_price)) * int(trade.quantity))
                if start is None or exit_date >= start:
                    realized_in_period = end_pnl
            else:
                end_mark = _latest_mark(marks, end, entry)
                if end_mark is None:
                    row_missing.append("END_MARK")
                    end_pnl = None
                else:
                    end_pnl = (float(end_mark.mark_price) - float(trade.entry_price)) * int(trade.quantity)
                    end_mark_date = end_mark.mark_date.isoformat()

            contribution = None if row_missing or end_pnl is None else round(end_pnl - start_pnl, 2)
            mark_contribution = None if contribution is None else round(contribution - realized_in_period, 2)
            row = {
                "trade_id": trade.id, "symbol": trade.symbol, "quantity": int(trade.quantity),
                "entry_date": entry.isoformat(), "exit_date": exit_date.isoformat() if exit_date else None,
                "status": "NOT_AVAILABLE" if row_missing else "AVAILABLE",
                "missing_boundaries": row_missing, "start_mark_date": start_mark_date, "end_mark_date": end_mark_date,
                "total_pnl": contribution, "realized_pnl": round(realized_in_period, 2),
                "unrealized_or_mark_contribution": mark_contribution,
            }
            rows.append(row)
            if row_missing:
                missing.append({"trade_id": trade.id, "symbol": trade.symbol, "missing": row_missing})

        stock_rows = []
        for symbol in sorted({row["symbol"] for row in rows}):
            members = [row for row in rows if row["symbol"] == symbol]
            complete = all(row["status"] == "AVAILABLE" for row in members)
            stock_rows.append({
                "symbol": symbol, "trade_count": len(members), "status": "AVAILABLE" if complete else "NOT_AVAILABLE",
                "total_pnl": round(sum(row["total_pnl"] for row in members), 2) if complete else None,
                "realized_pnl": round(sum(row["realized_pnl"] for row in members), 2),
                "unrealized_or_mark_contribution": round(sum(row["unrealized_or_mark_contribution"] for row in members), 2) if complete else None,
            })
        complete = not missing
        total = round(sum(row["total_pnl"] for row in rows), 2) if complete else None
        realized = round(sum(row["realized_pnl"] for row in rows), 2)
        mark_contribution = round(sum(row["unrealized_or_mark_contribution"] for row in rows), 2) if complete else None
        capital = float(get_portfolio_configuration()["initial_capital"])
        return {
            "period": str(period).upper(), "period_start": start.isoformat() if start else None, "period_end": end.isoformat(),
            "status": "AVAILABLE" if complete else "NOT_AVAILABLE", "total_pnl": total,
            "realized_pnl": realized, "unrealized_or_mark_contribution": mark_contribution,
            "return_pct": round(total / capital * 100.0, 4) if complete and capital else None,
            "return_denominator": "CONFIGURED_INITIAL_CAPITAL", "trade_count": len(rows),
            "stock_contributions": stock_rows, "trade_contributions": rows,
            "coverage": {"complete": complete, "missing_position_count": len(missing), "missing_boundaries": missing},
        }
    finally:
        session.close()

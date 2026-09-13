#!/usr/bin/env python3
"""Focused end-to-end prospective AutoPaper P1 safety contract."""
import datetime as dt
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = Path(tempfile.gettempdir()) / "autopaper_p1_test.db"
if DB.exists(): DB.unlink()
os.environ["TRADING_COCKPIT_DB_PATH"] = str(DB)
os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
os.environ["AUTOPAPER_KILL_SWITCH"] = "0"

import database
import autopaper_prospective as ap


DATES = pd.bdate_range("2026-09-14", "2026-10-02")


def histories():
    frame = pd.DataFrame({"Open": [100 + i for i in range(len(DATES))],
        "High": [102 + i for i in range(len(DATES))], "Low": [99 + i for i in range(len(DATES))],
        "Close": [101 + i for i in range(len(DATES))], "Volume": [1_000_000] * len(DATES)}, index=DATES)
    return {"AAA.NS": frame, "BBB.NS": frame * pd.Series({"Open":2,"High":2,"Low":2,"Close":2,"Volume":1})}


def decision(symbol="AAA.NS", opportunity="2026-09-14:AAA:Donchian", advisory=None):
    row = {"opportunity_id": opportunity, "symbol": symbol, "signal_date": "2026-09-14",
        "strategy": "Donchian Channel Breakout", "entry_price": 100., "atr_20": 8.,
        "current_volume": 1_000_000, "is_qualified": True}
    if advisory is not None: row["path_risk"] = advisory
    return row


def count(model, **filters):
    assert database.init_db()
    session = database.SessionLocal()
    try: return session.query(model).filter_by(**filters).count()
    finally: session.close()


def main():
    os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "0"
    disabled = ap.run_prospective_autopaper([decision()], histories(), dt.date(2026,9,14),
        dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc), "DISABLED")
    assert disabled["status"] == "DISABLED" and count(database.AutoPaperAccount) == 0
    os.environ["AUTOPAPER_PROSPECTIVE_ENABLED"] = "1"
    old = ap.run_prospective_autopaper([decision()], histories(), dt.date(2026,7,16),
        dt.datetime(2026,9,13,13,tzinfo=dt.timezone.utc), "OLD", source="AUTOMATED_EOD")
    assert old["status"] == "PRE_ACTIVATION" and count(database.AutoPaperAccount) == 0
    assert ap.run_prospective_autopaper([decision()], histories(), dt.date(2026,9,14),
        dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc), "MANUAL", source="MANUAL_REFRESH")["status"].startswith("SKIPPED")

    at = dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc)
    first = ap.run_prospective_autopaper([decision(advisory={"state":"HIGH"})], histories(), DATES[0].date(), at, "EOD-2026-09-14")
    assert first["active"] and first["paper_only"] and not first["real_money_authority"]
    assert count(database.AutoPaperAccount) == 4 and count(database.AutoPaperOrder, status="PENDING") == 4
    assert count(database.AutoPaperPosition, status="OPEN") == 0
    assert count(database.AutoPaperCounterfactualLink, origin="PROSPECTIVE") == 4
    retry = ap.run_prospective_autopaper([decision(advisory={"state":"LOW"})], histories(), DATES[0].date(), at, "EOD-2026-09-14")
    assert all(x["idempotent"] for x in retry["accounts"].values())
    assert count(database.AutoPaperOrder) == 4 and count(database.AutoPaperQueueItem) == 4

    second = ap.run_prospective_autopaper([], histories(), DATES[1].date(),
        dt.datetime.combine(DATES[1].date(), dt.time(12), tzinfo=dt.timezone.utc), "EOD-2026-09-15")
    assert second["accounts"]["BASELINE_C3"]["new_entries"] == 1
    session = database.SessionLocal()
    try:
        accounts = {x.account_id:x for x in session.query(database.AutoPaperAccount).all()}
        positions = {x.account_id:x for x in session.query(database.AutoPaperPosition).filter_by(status="OPEN").all()}
        assert positions["BASELINE_C3"].quantity == positions["SHADOW_D1"].quantity
        assert positions["SHADOW_C0"].quantity > positions["BASELINE_C3"].quantity
        assert accounts["SHADOW_C0"].cash < accounts["BASELINE_C3"].cash
        for account_id, account in accounts.items():
            position = positions[account_id]
            assert account.cash >= 0
            order = session.query(database.AutoPaperOrder).filter_by(account_id=account_id, side="BUY").one()
            expected_fee = min(20.0, order.quantity * order.fill_price * .0015)
            assert abs(order.fees - expected_fee) < 1e-9 and order.slippage > 0
            expected = account.cash + position.quantity * position.current_mark
            snap = session.query(database.AutoPaperPortfolioSnapshot).filter_by(account_id=account_id).order_by(
                database.AutoPaperPortfolioSnapshot.market_date.desc()).first()
            assert abs(snap.nav - expected) < .01
    finally: session.close()

    # Restart recovery is storage-driven: each later invocation reconstructs state from the DB.
    for date in DATES[2:11]:
        ap.run_prospective_autopaper([], histories(), date.date(),
            dt.datetime.combine(date.date(), dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{date.date()}")
    assert count(database.AutoPaperOrder, side="SELL", status="PENDING") == 4
    no_exit_bar = {symbol: frame.drop(DATES[11]) for symbol, frame in histories().items()}
    deferred = ap.run_prospective_autopaper([], no_exit_bar, DATES[11].date(),
        dt.datetime.combine(DATES[11].date(), dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{DATES[11].date()}")
    assert all(x["failed_fills"] == 1 for x in deferred["accounts"].values())
    assert count(database.AutoPaperOrder, side="SELL", status="PENDING") == 4
    exit_date = DATES[12].date()
    final = ap.run_prospective_autopaper([], histories(), exit_date,
        dt.datetime.combine(exit_date, dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{exit_date}")
    assert all(x["exits"] == 1 for x in final["accounts"].values())
    assert count(database.AutoPaperTrade) == 4 and count(database.AutoPaperPosition, status="OPEN") == 0
    assert count(database.AutoPaperDecision, action="EXIT") == 4
    assert count(database.AutoPaperHealth) == 13
    evidence = ap.prospective_evidence()
    assert evidence["accounts"]["BASELINE_C3"]["completed_trades"] == 1
    assert evidence["accounts"]["BASELINE_C3"]["immature_not_counted_as_losses"]
    assert evidence["future_review_gate"]["minimum_completed_baseline_trades"] == 100

    high = ap._candidate(decision(advisory={"state":"HIGH"}), DATES[0].date())
    low = ap._candidate(decision(advisory={"state":"LOW"}), DATES[0].date())
    high.pop("advisory_annotations"); low.pop("advisory_annotations")
    assert high == low
    os.environ["AUTOPAPER_KILL_SWITCH"] = "1"
    killed = ap.run_prospective_autopaper([], histories(), DATES[13].date(),
        dt.datetime.combine(DATES[13].date(), dt.time(12), tzinfo=dt.timezone.utc), f"EOD-{DATES[13].date()}")
    assert killed["kill_switch"] and not killed["active"] and count(database.AutoPaperOrder) == 8
    os.environ["AUTOPAPER_KILL_SWITCH"] = "0"
    source = Path(ap.__file__).read_text()
    assert "Replacement" not in source and "add_constrained_paper_trade" not in source
    assert ap.CONFIG["paper_only"] and ap.CONFIG["stop"] is None and ap.CONFIG["target"] is None
    assert ap.ACTIVATION_MARKET_DATE > dt.date(2026,7,16)
    print("AutoPaper-P1 focused tests: 20 passed")


if __name__ == "__main__": main()

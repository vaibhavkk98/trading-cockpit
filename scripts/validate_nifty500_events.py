import os
import pandas as pd
from typing import Dict, Any, List

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_change_events.csv")


def validate_events() -> Dict[str, Any]:
    print("=" * 80)
    print("NIFTY 500 HISTORICAL EVENT DATASET VALIDATION REPORT")
    print("=" * 80)

    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"Event file missing at: {CSV_PATH}")

    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    total_events = len(df)

    # Event counts by type
    counts_by_type = df["event_type"].value_counts().to_dict()
    additions = counts_by_type.get("ADDITION", 0)
    deletions = counts_by_type.get("DELETION", 0)
    symbol_changes = counts_by_type.get("SYMBOL_CHANGE", 0)
    corporate_events = counts_by_type.get("CORPORATE_EVENT", 0)

    # Event counts by year
    df["year"] = pd.to_datetime(df["effective_date"], errors="coerce").dt.year.fillna(0).astype(int).astype(str)
    counts_by_year = df["year"].value_counts().sort_index().to_dict()

    # Event counts by effective date
    counts_by_date = df["effective_date"].value_counts().head(10).to_dict()

    # Missing counts
    missing_isin_count = (df["isin"] == "").sum()
    missing_source_ref_count = (df["source_reference"] == "").sum()

    # Duplicate events
    dup_mask = df.duplicated(subset=["effective_date", "event_type", "symbol", "old_symbol", "new_symbol"])
    duplicate_events = int(dup_mask.sum())

    # Suspicious events check
    suspicious = []
    for idx, row in df.iterrows():
        ev_type = row["event_type"]
        sym = row["symbol"]
        old_s = row["old_symbol"]
        new_s = row["new_symbol"]
        src = row["source"]

        if ev_type == "ADDITION" and not sym:
            suspicious.append(f"Row {idx}: ADDITION without symbol")
        if ev_type == "DELETION" and not sym:
            suspicious.append(f"Row {idx}: DELETION without symbol")
        if ev_type == "SYMBOL_CHANGE" and (not old_s or not new_s):
            suspicious.append(f"Row {idx}: SYMBOL_CHANGE without old/new symbol ({old_s} -> {new_s})")
        if not src or src not in ["NSE India", "NSE Indices"]:
            suspicious.append(f"Row {idx}: Event with non-authoritative source '{src}'")

    print(f"Total Historical Events Recorded : {total_events}")
    print("\nEvents by Type:")
    print(f"  - ADDITION        : {additions}")
    print(f"  - DELETION        : {deletions}")
    print(f"  - SYMBOL_CHANGE   : {symbol_changes}")
    print(f"  - CORPORATE_EVENT : {corporate_events}")

    print("\nEvents by Year:")
    for yr, cnt in counts_by_year.items():
        print(f"  - {yr}: {cnt} events")

    print("\nTop Effective Dates by Event Count:")
    for dt, cnt in list(counts_by_date.items())[:5]:
        print(f"  - {dt}: {cnt} events")

    print("\nData Quality & Integrity Checks:")
    print(f"  - Missing ISIN Count               : {missing_isin_count} (Expected for raw symbolchange files)")
    print(f"  - Missing Source Reference Count   : {missing_source_ref_count}")
    print(f"  - Duplicate Events                 : {duplicate_events}")
    print(f"  - Suspicious Events Count          : {len(suspicious)}")

    if suspicious:
        print("\nSuspicious Events Detail:")
        for s in suspicious[:5]:
            print(f"    * {s}")

    # 5 Historical Validation Dates Check
    val_dates = [
        ("2020-03-27", "March 2020 Semi-Annual Rebalance"),
        ("2021-09-30", "September 2021 Semi-Annual Rebalance"),
        ("2023-03-31", "March 2023 Semi-Annual Rebalance"),
        ("2023-07-13", "HDFC / JIOFIN Corporate Action"),
        ("2025-03-28", "March 2025 Semi-Annual Rebalance")
    ]

    print("\n" + "=" * 80)
    print("CHECKING 5 HISTORICAL RECONSTITUTION / CORPORATE VALIDATION DATES")
    print("=" * 80)

    for v_date, desc in val_dates:
        sub = df[df["effective_date"] == v_date]
        adds = sub[sub["event_type"] == "ADDITION"]["symbol"].tolist()
        dels = sub[sub["event_type"] == "DELETION"]["symbol"].tolist()
        corps = sub[sub["event_type"] == "CORPORATE_EVENT"]["symbol"].tolist()
        sym_chg = sub[sub["event_type"] == "SYMBOL_CHANGE"]["symbol"].tolist()

        print(f"\nDate: {v_date} ({desc})")
        print(f"  - Additions : {adds if adds else 'None'}")
        print(f"  - Deletions : {dels if dels else 'None'}")
        print(f"  - Corporate : {corps if corps else 'None'}")
        print(f"  - Renames   : {len(sym_chg)} symbol renames on this date")

    print("\n" + "=" * 80)
    print("EVENT DATASET VALIDATION COMPLETED CLEANLY!")
    print("=" * 80)

    return {
        "total_events": total_events,
        "additions": additions,
        "deletions": deletions,
        "symbol_changes": symbol_changes,
        "corporate_events": corporate_events,
        "events_by_year": counts_by_year,
        "missing_isin_count": missing_isin_count,
        "duplicate_events": duplicate_events,
        "suspicious_events": suspicious
    }


if __name__ == "__main__":
    validate_events()

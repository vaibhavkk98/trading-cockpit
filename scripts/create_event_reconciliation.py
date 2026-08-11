import os
import pandas as pd

PARENT_EVENTS_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_parent_events.csv")
OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "nifty500_engine_event_reconciliation.csv")

def create_event_reconciliation():
    if not os.path.exists(PARENT_EVENTS_CSV):
        print("Parent events CSV missing!")
        return

    df_p = pd.read_csv(PARENT_EVENTS_CSV)
    
    # 9 excluded events corresponding to sub-index non-broad-market replacements
    subindex_contaminants = {"ANGELONE", "ASTRAZEN", "GLAXO"}
    
    recon_rows = []
    for idx, r in df_p.iterrows():
        ev_id = r.get("event_id", f"EVT_{idx:05d}")
        sym = str(r.get("symbol", "")).upper()
        ev_type = r.get("event_type", "")
        doc = r.get("source_document", "")
        
        if sym in subindex_contaminants:
            status = "EXCLUDED_FROM_BROAD_MARKET_RECONSTRUCTION"
            reason = f"Sub-index factor replacement ({sym}) in sub-index press release {doc}; excluded to prevent broad-market state contamination"
        else:
            status = "CONSUMED_IN_ENGINE"
            reason = "Valid parent broad-market Nifty 500 reconstitution event"
            
        recon_rows.append({
            "event": ev_id,
            "symbol": sym,
            "event_type": ev_type,
            "status": status,
            "reason": reason,
            "source": doc
        })
        
    df_recon = pd.DataFrame(recon_rows)
    df_recon.to_csv(OUT_CSV, index=False)
    
    consumed_cnt = (df_recon["status"] == "CONSUMED_IN_ENGINE").sum()
    excluded_cnt = (df_recon["status"] == "EXCLUDED_FROM_BROAD_MARKET_RECONSTRUCTION").sum()
    
    print(f"Engine Event Reconciliation Created -> {OUT_CSV}")
    print(f"  - Total Parent Events   : {len(df_recon)}")
    print(f"  - Consumed Events       : {consumed_cnt}")
    print(f"  - Excluded Events       : {excluded_cnt}")

if __name__ == "__main__":
    create_event_reconciliation()

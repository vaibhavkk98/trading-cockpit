"""Create the P3A live-coverage information-quality report (no trading writes)."""
import os, sys
from datetime import datetime, timezone
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from event_intelligence import EventIntelligenceService

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); OUT=os.path.join(ROOT,"data","research","p3a_event_intelligence_report.md")
SYMBOLS=["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","TATAMOTORS","SUNPHARMA","NTPC","BEL","TATASTEEL"]

def run():
    service=EventIntelligenceService(); contexts=[service.get_context(s,cutoff=datetime.now(timezone.utc)) for s in SYMBOLS]
    events=[e for c in contexts for e in c["events"]]; raw=sum(c["raw_item_count"] for c in contexts)
    reliable=[e for e in events if e["source_type"]!="SECONDARY"]; material=[e for e in reliable if e["materiality"] in ("MEDIUM","HIGH","CRITICAL")]
    rows=[]
    for c in contexts:
        top=c["top_event"] or (c["events"][0] if c["events"] else {}); rows.append({"symbol":c["events"][0]["symbol"] if c["events"] else "", "raw_items":c["raw_item_count"], "deduplicated":c["deduplicated_event_count"], "event_context":c["event_context"], "headline":top.get("headline", "NOT_AVAILABLE"), "source":top.get("source", "NOT_AVAILABLE"), "authority":top.get("source_type", "NOT_AVAILABLE"), "type":top.get("event_type", "NOT_AVAILABLE"), "direction":top.get("direction", "UNKNOWN"), "materiality":top.get("materiality", "NOT_AVAILABLE"), "novelty":top.get("novelty", "NOT_AVAILABLE"), "confidence":top.get("confidence", "NOT_AVAILABLE"), "horizon":top.get("expected_horizon", "UNKNOWN"), "market_reaction":top.get("market_reaction", "NOT_AVAILABLE")})
    table=pd.DataFrame(rows)
    verdict="PARTIAL GO" if reliable else "NO-GO"
    lines=["# P3A — Live Event Intelligence MVP","",f"Generated: {datetime.now(timezone.utc).isoformat()}","",
      "## A. Architecture","", "A standalone `EventIntelligenceService` discovers current Google News RSS items, normalizes a canonical event object, excludes items after the requested cutoff, deduplicates obvious syndicated copies, applies source authority, and attaches informational fields after candidate allocation. It does not call the allocator, scoring, qualification, execution, or database paths.","",
      "## B. Source coverage","", f"- Provider used: **{service.provider_name}**.",f"- Candidates queried: {len(SYMBOLS)}; raw items: {raw}; after deduplication: {len(events)}.",f"- High-quality financial-news items: {len(reliable)} ({(100*len(reliable)/len(events)) if events else 0:.1f}%); material events: {len(material)}.",f"- Duplicate/update rate (exact obvious syndications): {(100*(raw-len(events))/raw) if raw else 0:.1f}%.", "- Exchange filings and company IR were not configured/retrieved in this MVP; RSS is discovery only, not equivalent to a filing.","",
      "## C. Event taxonomy","", "Company: earnings/guidance, order/contract, corporate action, funding/debt, M&A/partnership, operations/capacity/product, management/governance, promoter/ownership, regulatory/legal, credit/rating, other material. Sector/market categories are reserved for reliable future providers.","",
      "## D. Causal/timestamp handling","", "Publication timestamps are parsed and events after the analysis cutoff are excluded. Missing timestamps are excluded. Market reaction remains `NOT_AVAILABLE` because this discovery provider does not establish event-time causal price windows; no `priced in` conclusion is made.","",
      "## E. Deduplication","", "Events are clustered by symbol, event type, and normalized headline family. The higher-authority source wins; later independently established developments can be represented as updates in a future filing/IR provider.","",
      "## F. Representative live cases","", table.to_markdown(index=False),"",
      "## G. Headline-only vs structured comparison","", "Headline-only leaves source authority, cutoff eligibility, event family, novelty, confidence, horizon, and missing reaction evidence implicit. Event Intelligence preserves those fields, does not convert unavailable evidence to neutral, and prevents repeated syndicated items being treated as independent catalysts.","",
      "## H. Limitations","", "Google News RSS availability and publisher attribution are variable; no direct NSE/BSE or company-IR connector is configured; headline text cannot establish surprise, company-scale materiality, or priced-in status; direct market/sector inputs are not yet attached. P3A therefore makes no trading-alpha claim.","",
      "## I. P3B recommendation","", "Add timestamped NSE/BSE filings and company IR sources, retain raw-source identifiers, obtain event-time price/volume benchmarks, and collect a prospective immutable event ledger before any historical evaluation.","",
      f"# {verdict}","", "Use P3A only as clearly labelled live Event Context. Its absence is `NOT_AVAILABLE`, not neutral, and it remains informational only."]
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT,"w") as f:f.write("\n".join(lines)+"\n")
    print({"raw":raw,"deduplicated":len(events),"reliable":len(reliable),"material":len(material),"verdict":verdict})
if __name__=="__main__":run()

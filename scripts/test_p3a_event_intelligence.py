"""Focused P3A contract tests; no provider/network or database writes."""
import hashlib, os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from event_intelligence import Event, aggregate, build_event_families, add_authoritative_evidence, deduplicate, normalize_raw_items

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
def stamp(hours): return (datetime.now(timezone.utc)+timedelta(hours=hours)).strftime("%a, %d %b %Y %H:%M:%S +0000")
def sha(path):
    with open(path,"rb") as f: return hashlib.sha256(f.read()).hexdigest()

class TestP3A(unittest.TestCase):
    def test_cutoff_and_unknown_preservation(self):
        items=[{"title":"ABC announces routine update", "pub_date":stamp(-3), "link":"a"},{"title":"ABC wins order", "pub_date":stamp(3), "link":"b"}]
        events=normalize_raw_items("ABC",items,cutoff=datetime.now(timezone.utc))
        self.assertEqual(len(events),1); self.assertEqual(events[0].direction,"UNKNOWN"); self.assertEqual(events[0].surprise,"UNKNOWN")
        self.assertEqual(aggregate("ABC",[])["event_context"],"NOT_AVAILABLE")

    def test_duplicates_and_authority_precedence(self):
        base=dict(symbol="ABC",event_time="2026-01-01T00:00:00+00:00",source_url_or_id="x",scope="COMPANY",event_type="ORDER / CONTRACT")
        a=Event(**base,source="Secondary",source_type="SECONDARY",headline="ABC wins order")
        b=Event(**base,source="NSE",source_type="EXCHANGE_FILING",headline="ABC wins order")
        result=deduplicate([a,b]); self.assertEqual(len(result),1); self.assertEqual(result[0].source,"NSE")

    def test_conflicting_events_and_no_state_changes(self):
        base=dict(symbol="ABC",event_time="2026-01-01T00:00:00+00:00",source_type="FINANCIAL_NEWS",source_url_or_id="x",scope="COMPANY",materiality="HIGH",confidence=.7)
        pos=Event(**base,source="Reuters",headline="ABC wins order",event_type="ORDER / CONTRACT",direction="POSITIVE")
        neg=Event(**base,source="Reuters",headline="ABC faces penalty",event_type="REGULATORY / LEGAL",direction="NEGATIVE")
        self.assertEqual(aggregate("ABC",[pos,neg])["event_context"],"MIXED")
        frozen=["data/mvp/performance_report.json","data/research/p1_qualified_signals.csv"]
        before={p:sha(os.path.join(ROOT,p)) for p in frozen}
        aggregate("ABC",[pos,neg])
        self.assertEqual(before,{p:sha(os.path.join(ROOT,p)) for p in frozen})
        db=os.path.join(ROOT,"paper_trading.db")
        db_before=sha(db) if os.path.exists(db) else None
        aggregate("ABC",[pos,neg])
        self.assertEqual(db_before, sha(db) if os.path.exists(db) else None)

    def test_authoritative_family_update_and_unknown_materiality(self):
        items=[{"symbol":"ABC","pub_date":stamp(-2),"source":"NSE filing","source_type":"EXCHANGE_FILING","url":"nse","headline":"ABC wins order","event_type":"ORDER / CONTRACT","direction":"POSITIVE","materiality":"HIGH","evidence":"Order value disclosed"}, {"symbol":"ABC","pub_date":stamp(-1),"source":"News","source_type":"FINANCIAL_NEWS","url":"news","headline":"ABC wins order"}]
        events=add_authoritative_evidence([],items,datetime.now(timezone.utc)); family=build_event_families(events)
        self.assertEqual(len(family),1); self.assertEqual(family[0].source_type,"EXCHANGE_FILING"); self.assertEqual(family[0].materiality,"HIGH")
        unknown=add_authoritative_evidence([], [{"symbol":"ABC","pub_date":stamp(-1),"source":"NSE","source_type":"EXCHANGE_FILING","headline":"ABC update"}], datetime.now(timezone.utc))
        self.assertEqual(unknown[0].materiality,"UNKNOWN")

if __name__=="__main__": unittest.main()

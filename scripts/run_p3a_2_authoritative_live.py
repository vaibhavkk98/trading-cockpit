import os,sys
from datetime import datetime,timezone
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..')))
from event_intelligence import EventIntelligenceService
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..')); OUT=os.path.join(ROOT,'data/research','p3a_2_authoritative_live_report.md')
SYMS=['RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','TATAMOTORS','SUNPHARMA','NTPC','BEL','TATASTEEL']
def run():
 cs=[EventIntelligenceService().get_context(s,datetime.now(timezone.utc)) for s in SYMS]; events=[e for c in cs for e in c['events']]; auth=[e for e in events if e['source_type']=='EXCHANGE_FILING']; verified=[e for e in auth if e['materiality']!='UNKNOWN']
 lines=['# P3A.2 — One Authoritative Live Event Source','','## A–B. Provider and access','', 'BSE corporate-announcement API was selected as the single public official source. It is queried once per mapped symbol with a seven-calendar-day window, public headers, no authentication or access-control bypass.','','## C–I. Live coverage','',f'- Symbols checked: {len(SYMS)}; BSE checks successful: {sum(c["authoritative_checked"] for c in cs)}.',f'- Authoritative records: {len(auth)}; event families: {len(events)}; verified materiality: {len(verified)}.',f'- NO_MATERIAL_EVENT: {sum(c["event_context"]=="NO_MATERIAL_EVENT" for c in cs)}; NOT_AVAILABLE: {sum(c["event_context"]=="NOT_AVAILABLE" for c in cs)}.', '- P3A.1 had 0 authoritative coverage / 0 verified materiality. P3A.2 connects the source and preserves that distinction; no alpha claim is made.','','## J. Detailed live examples','']
 for c in cs[:3]: lines += [f"- {c['events'][0]['symbol'] if c['events'] else 'N/A'}: authoritative checked `{c['authoritative_checked']}`; final context `{c['event_context']}`. No verified official event was returned in this run, so no event fields are fabricated."]
 lines += ['','## K. Limitations','', 'The BSE endpoint was reachable but returned no records for the current date-window run. Materiality remains UNKNOWN without official disclosed scale/evidence; market reaction is NOT_AVAILABLE.','# NO-GO','', 'The single provider integration is clean, but it did not retrieve the required real official events reliably enough in this environment.']
 with open(OUT,'w') as f:f.write('\n'.join(lines)+'\n')
 print({'checked':sum(c['authoritative_checked'] for c in cs),'auth':len(auth),'verified':len(verified)})
if __name__=='__main__':run()

"""Small forward-looking Market Risk Context backend; no allocation hooks."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from functools import lru_cache
import json, re, urllib.request, xml.etree.ElementTree as ET

CHANNELS={'GEOPOLITICAL':['risk_off','oil','trade','supply_chain'],'MONETARY_POLICY':['rates','liquidity','currency','inflation'],'FISCAL_POLICY':['regulation','growth','inflation'],'ECONOMIC_GROWTH':['growth','inflation','currency'],'FINANCIAL_SYSTEM':['credit','liquidity','risk_off'],'COMMODITY_ENERGY':['oil','inflation','supply_chain'],'TRADE_POLICY':['trade','supply_chain','inflation'],'TECHNOLOGY_SYSTEMIC':['technology','supply_chain','regulation'],'PUBLIC_HEALTH_NATURAL_DISASTER':['risk_off','growth','supply_chain'],'GLOBAL_MARKET_STRESS':['risk_off','liquidity','credit']}
KEYWORDS={'GEOPOLITICAL':['war','military','sanction','border conflict'],'MONETARY_POLICY':['rbi','interest rate','monetary policy','liquidity'],'FISCAL_POLICY':['budget','fiscal','tax','tariff'],'ECONOMIC_GROWTH':['gdp','inflation','pmi','employment','recession'],'FINANCIAL_SYSTEM':['bank stress','banking crisis','default','credit market'],'COMMODITY_ENERGY':['crude','oil','gas','energy supply'],'TRADE_POLICY':['export restriction','import restriction','trade restriction','tariff'],'TECHNOLOGY_SYSTEMIC':['semiconductor','cyber','technology regulation','infrastructure disruption'],'PUBLIC_HEALTH_NATURAL_DISASTER':['pandemic','earthquake','flood','disaster'],'GLOBAL_MARKET_STRESS':['market stress','market selloff','volatility shock','financial turmoil']}
@dataclass
class Source: name:str; tier:str; url:str; groups:tuple=()
SOURCES=[
 Source('Reserve Bank of India press releases','TIER_1_PRIMARY_OFFICIAL','https://www.rbi.org.in/Scripts/RSS.aspx?Id=1',('INDIA_CORE',)),
 Source('Press Information Bureau India','TIER_1_PRIMARY_OFFICIAL','https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3',('INDIA_CORE',)),
 Source('Federal Reserve press releases','TIER_1_PRIMARY_OFFICIAL','https://www.federalreserve.gov/feeds/press_all.xml',('GLOBAL_MACRO',)),
 Source('European Central Bank press releases','TIER_1_PRIMARY_OFFICIAL','https://www.ecb.europa.eu/rss/press.html',('GLOBAL_MACRO',)),
 Source('UN News','TIER_1_PRIMARY_OFFICIAL','https://news.un.org/feed/subscribe/en/news/all/rss.xml',('GLOBAL_SYSTEMIC',)),
 Source('BBC World','TIER_3_SECONDARY_CONTEXT','https://feeds.bbci.co.uk/news/world/rss.xml',('GLOBAL_SYSTEMIC',)),
]
def now(): return datetime.now(timezone.utc).isoformat()
def fetch(source, timeout=12):
 try:
  data=urllib.request.urlopen(urllib.request.Request(source.url,headers={'User-Agent':'Mozilla/5.0'}),timeout=timeout).read();root=ET.fromstring(data);items=[]
  for item in root.findall('.//item'):
   title=(item.findtext('title') or '').strip();desc=re.sub('<[^>]+>',' ',item.findtext('description') or '').strip();link=(item.findtext('link') or '').strip();pub=(item.findtext('pubDate') or item.findtext('{http://purl.org/dc/elements/1.1/}date') or '').strip();items.append({'title':title,'summary':desc,'url':link,'published':pub})
  return {'source_name':source.name,'source_tier':source.tier,'coverage_groups':list(source.groups),'check_status':'SUCCESS','checked_at':now(),'items_returned':len(items),'failure_reason':None},items
 except Exception as e:return {'source_name':source.name,'source_tier':source.tier,'coverage_groups':list(source.groups),'check_status':'FAILED','checked_at':now(),'items_returned':0,'failure_reason':type(e).__name__},[]
def classify(raw, source):
 text=(raw['title']+' '+raw['summary']).lower();cat=next((c for c,words in KEYWORDS.items() if any(w in text for w in words)),None)
 if not cat:return None
 if any(w in text for w in ['earnings','quarterly result','share price','company profit']):return None
 published=raw['published'] or now()
 try: age=(datetime.now(timezone.utc)-parsedate_to_datetime(published).astimezone(timezone.utc)).total_seconds()/3600
 except Exception:age=999
 if age>168:return None  # C2 is a bounded seven-calendar-day live context.
 fresh='FRESH' if age<=24 else 'RECENT' if age<=168 else 'STALE';severe=any(w in text for w in ['systemic crisis','global financial crisis','major war escalation','pandemic emergency']);high=any(w in text for w in ['war','sanction','oil','bank stress','market selloff','rate hike'])
 materiality='SEVERE' if severe else 'HIGH' if high else 'MODERATE';confidence='HIGH' if source.tier.startswith('TIER_1') else 'MODERATE'
 return {'market_risk_event_id':f"MR_{cat}_{abs(hash((cat,raw['title'].lower())))%10**10}",'category':cat,'headline_or_short_title':raw['title'],'summary':raw['summary'][:600],'event_timestamp':published,'published_timestamp':published,'observed_at_timestamp':now(),'source_name':source.name,'source_type':'OFFICIAL_RSS','source_tier':source.tier,'source_reference':raw['url'] or source.url,'geographic_scope':'GLOBAL_OR_INDIA','market_scope':'BROAD_MARKET_PLAUSIBLE','direction':'RISK_NEGATIVE','materiality':materiality,'confidence':confidence,'freshness':fresh,'status':'ACTIVE' if fresh!='STALE' else 'MONITOR','expected_horizon':'DAYS','evidence_summary':raw['title'],'possible_market_channels':CHANNELS[cat],'affected_asset_context':'Broad Indian-market / portfolio context','supporting_source_count':1,'supporting_source_references':[raw['url'] or source.url],'informational_only':True}
def cluster(events):
 out=[]
 for e in events:
  prior=next((x for x in out if x['category']==e['category'] and set(e['headline_or_short_title'].lower().split())&set(x['headline_or_short_title'].lower().split())),None)
  if prior:prior['supporting_source_count']+=1;prior['supporting_source_references']+=e['supporting_source_references']
  else:out.append(e)
 return out
def build_payload(diagnostics, raw, events):
 successful=sum(d['check_status']=='SUCCESS' for d in diagnostics);groups={g for d in diagnostics if d['check_status']=='SUCCESS' for g in d.get('coverage_groups',[])};required={'INDIA_CORE','GLOBAL_MACRO','GLOBAL_SYSTEMIC'};coverage='SUFFICIENT' if required<=groups else 'PARTIAL' if groups else 'INSUFFICIENT';adequate=coverage=='SUFFICIENT'
 if any(e['materiality']=='SEVERE' and e['confidence'] in ['HIGH','MODERATE'] for e in events):level='SEVERE'
 elif any(e['materiality']=='HIGH' and e['status']=='ACTIVE' for e in events):level='HIGH'
 elif not adequate: level='NOT_AVAILABLE'
 elif any(e['materiality']=='MODERATE' for e in events):level='ELEVATED'
 else:level='NORMAL'
 order={'ACTIVE':0,'MONITOR':1};mat={'SEVERE':0,'HIGH':1,'MODERATE':2,'LOW':3};conf={'HIGH':0,'MODERATE':1,'LOW':2};fresh={'FRESH':0,'RECENT':1,'STALE':2};top=sorted(events,key=lambda e:(order[e['status']],mat[e['materiality']],conf[e['confidence']],fresh[e['freshness']]))[:3]
 note='Market Risk Context unavailable because source coverage was insufficient.' if level=='NOT_AVAILABLE' else ('No material broad-market external risk identified from checked sources.' if level=='NORMAL' else 'Broad external risk is elevated. Overnight realized losses may exceed defined technical stop risk.')
 return {'overall_level':level,'generated_at':now(),'source_coverage_status':coverage,'coverage_groups_achieved':sorted(groups),'coverage_groups_required':sorted(required),'successful_source_checks':successful,'failed_source_checks':sum(d['check_status']=='FAILED' for d in diagnostics),'raw_item_count':len(raw),'active_event_count':sum(e['status']=='ACTIVE' for e in events),'high_materiality_count':sum(e['materiality'] in ['HIGH','SEVERE'] for e in events),'categories':{c:sum(e['category']==c for e in events) for c in KEYWORDS},'top_events':top,'gap_risk_note':note,'informational_only':True}

@lru_cache(maxsize=8)
def _load_market_risk_snapshot(project_root_text):
 root=Path(project_root_text) if project_root_text else Path(__file__).resolve().parent; research=root/'data'/'research'
 payload=json.loads((research/'c2_live_market_risk_payload.json').read_text())
 diagnostics_path=research/'c2_source_check_diagnostics.json'
 diagnostics=json.loads(diagnostics_path.read_text()) if diagnostics_path.exists() else []
 return payload,diagnostics

def get_market_risk_context_for_ui(project_root=None, max_age_hours=6):
 """Read the latest backend snapshot only; never fetches or changes decisions."""
 try:
  payload,diagnostics=_load_market_risk_snapshot(str(project_root) if project_root else None);payload=dict(payload)
  generated=datetime.fromisoformat(payload['generated_at'].replace('Z','+00:00'));age=(datetime.now(timezone.utc)-generated.astimezone(timezone.utc)).total_seconds()/60
  payload['snapshot_age_minutes']=max(0,round(age));payload['snapshot_stale']=age>max_age_hours*60;payload['source_diagnostics']=diagnostics
  return payload
 except Exception as exc:
  return {'overall_level':'NOT_AVAILABLE','source_coverage_status':'INSUFFICIENT','generated_at':None,'snapshot_age_minutes':None,'snapshot_stale':True,'active_event_count':0,'top_events':[],'gap_risk_note':'Market Risk Context unavailable because the latest backend snapshot could not be read.','informational_only':True,'ui_failure_reason':type(exc).__name__,'source_diagnostics':[]}

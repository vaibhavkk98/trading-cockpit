from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));R=ROOT/'data/research'
from market_risk_live import SOURCES,fetch,classify,cluster,build_payload
def run():
 diagnostics=[];raw=[];events=[]
 for s in SOURCES:
  d,items=fetch(s);diagnostics.append(d);raw.extend(items);events.extend(x for x in (classify(i,s) for i in items) if x)
 final=cluster(events);payload=build_payload(diagnostics,raw,final)
 (R/'c2_live_market_risk_events.json').write_text(json.dumps(final,indent=2)+'\n');(R/'c2_live_market_risk_payload.json').write_text(json.dumps(payload,indent=2)+'\n');(R/'c2_source_check_diagnostics.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
 coverage={'configured_sources':[{'source_name':s.name,'source_tier':s.tier,'coverage_groups':s.groups} for s in SOURCES],'required_groups':['INDIA_CORE','GLOBAL_MACRO','GLOBAL_SYSTEMIC'],'achieved_groups':payload['coverage_groups_achieved'],'coverage_status':payload['source_coverage_status'],'normal_rule':'NORMAL requires successful source coverage in INDIA_CORE, GLOBAL_MACRO and GLOBAL_SYSTEMIC plus no retained MODERATE/HIGH/SEVERE event. HIGH/SEVERE retained events override insufficient coverage; otherwise no group coverage is NOT_AVAILABLE.'}
 (R/'c2_1_source_coverage_matrix.json').write_text(json.dumps(coverage,indent=2)+'\n')
 report=['# C2 Live Market Risk Context','',f"Overall context: **{payload['overall_level']}**; raw items {len(raw)}, retained {len(events)}, deduplicated {len(final)}.",'','## Source checks','',json.dumps(diagnostics,indent=2),'','## Gap-risk note','',payload['gap_risk_note'],'','C3 readiness: **PARTIAL** when live source coverage is imperfect; payload safely emits `NOT_AVAILABLE` instead of inferred NORMAL.']
 (R/'c2_live_market_risk_report.md').write_text('\n'.join(report)+'\n');print(payload)
if __name__=='__main__':run()

from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));from market_risk_live import SOURCES,build_payload
def diag(groups,status='SUCCESS'):return {'check_status':status,'source_tier':'TIER_1_PRIMARY_OFFICIAL','coverage_groups':groups}
def run():
 assert 5<=len(SOURCES)<=8
 normal=build_payload([diag(['INDIA_CORE']),diag(['GLOBAL_MACRO']),diag(['GLOBAL_SYSTEMIC'])],[],[]);partial=build_payload([diag(['INDIA_CORE'])],[],[]);high={'materiality':'HIGH','confidence':'HIGH','status':'ACTIVE','freshness':'FRESH','category':'GEOPOLITICAL','headline_or_short_title':'confirmed','supporting_source_count':1}
 surfaced=build_payload([diag(['INDIA_CORE'])],[],[high]);assert normal['overall_level']=='NORMAL' and normal['source_coverage_status']=='SUFFICIENT';assert partial['overall_level']=='NOT_AVAILABLE' and partial['source_coverage_status']=='PARTIAL';assert surfaced['overall_level']=='HIGH' and surfaced['source_coverage_status']=='PARTIAL';print('C2.1 focused coverage tests: PASS')
if __name__=='__main__':run()

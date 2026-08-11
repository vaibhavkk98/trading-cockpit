from pathlib import Path
import json,pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 d=pd.read_csv(R/'e2_canonical_decision_payload.csv');j=json.loads((R/'e2_final_cockpit_payload.json').read_text())
 assert len(d)==526 and d.opportunity_id.is_unique and d.allocated.sum()==216;assert (~d.allocated).sum()==310;assert d.allocation_reason_code.value_counts().to_dict()=={'ALLOCATED':216,'INSUFFICIENT_CASH':153,'DUPLICATE_POSITION':129,'CAPITAL_CAP':28};assert d.market_risk_context.eq('NOT_APPLICABLE_HISTORICAL_PREVIEW').all();assert d.trade_economics_target_status.eq('NOT_AVAILABLE').all();assert j['metadata']['opportunity_count']==526 and not j['portfolio_summary']['risk_based_sizing_active'];print('E2 focused payload tests: PASS')
if __name__=='__main__':run()

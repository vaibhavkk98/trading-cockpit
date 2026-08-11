from pathlib import Path
import json,pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 p=json.loads((R/'b3_phase_b_policy_contract.json').read_text());d=pd.read_csv(R/'b3_canonical_portfolio_risk_lineage.csv');s=pd.read_csv(R/'b3_portfolio_risk_summary.csv')
 assert len(d)==526 and p['nominal_ticket_rupees']==100000 and not p['risk_based_sizing_enabled'] and not p['portfolio_heat_limit_enabled'];assert {'reference_risk_rupees','executable_risk_rupees','reference_heat_before_pct','risk_availability_status'}.issubset(d.columns);assert s.reference_heat_missing_count.iloc[0]>0;print('B3 focused Phase B freeze tests: PASS')
if __name__=='__main__':run()

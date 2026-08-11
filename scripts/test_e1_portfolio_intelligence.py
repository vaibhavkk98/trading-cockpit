from pathlib import Path
import json,pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 d=pd.read_csv(R/'e1_decision_explainability_preview.csv');m=pd.read_csv(R/'e1_binding_vs_informational_matrix.csv');c=json.loads((R/'e1_decision_dimension_contract.json').read_text())
 assert len(d)==526 and d.opportunity_id.is_unique and (d.allocation_status=='ALLOCATED').sum()==216;assert d.qualification_status.eq('QUALIFIED').all();assert m.loc[m.dimension.eq('Portfolio Heat'),'affects_allocation'].iloc[0]=='NO';assert m.loc[m.dimension.eq('Executable Stop'),'affects_exit'].iloc[0]=='YES';assert c['no_composite_score'];assert d.market_risk_informational_only.all();print('E1 focused portfolio-intelligence tests: PASS')
if __name__=='__main__':run()

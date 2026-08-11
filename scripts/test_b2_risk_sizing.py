from pathlib import Path
import json,pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 d=pd.read_csv(R/'b2_trade_sizing_diagnostics.csv');p=pd.read_csv(R/'b2_portfolio_comparison.csv');x=json.loads((R/'b2_frozen_sizing_contract.json').read_text());b=pd.read_csv(R/'b1_trade_risk_dispersion.csv')
 assert len(d)==526 and x['target_risk_pct']==b.reference_risk_pct_equity_at_entry.median() and (d.risk_sized_position_value<=100000+1e-6).all();assert (d.risk_sized_quantity.dropna()>=0).all();assert len(p)==2 and p.iloc[0].trade_count==216;print('B2 focused sizing tests: PASS')
if __name__=='__main__':run()

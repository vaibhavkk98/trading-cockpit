from pathlib import Path
import json,pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 d=pd.read_csv(R/'d1_strategy_economics.csv');c=json.loads((R/'d1_expected_edge_context.json').read_text())
 assert set(d.split)=={'TRAIN','VALIDATION','TEST'} and d.strategy.nunique()==6 and len(c)==6;assert d.target_status.eq('NOT_AVAILABLE').all();assert 'predicted_return' not in d.columns;assert d['return_definition'].str.contains('Frozen Phase A').all();print('D1 focused economics tests: PASS')
if __name__=='__main__':run()

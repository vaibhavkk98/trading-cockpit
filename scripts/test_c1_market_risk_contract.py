from pathlib import Path
import json
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 t=json.loads((R/'c1_market_risk_taxonomy.json').read_text());c=json.loads((R/'c1_market_risk_contract.json').read_text());g=c['guardrails']
 assert len(t['categories'])==10 and c['informational_only'] and not c['live_data_fetching_implemented'];assert not any(g.values());assert 'NOT_AVAILABLE' in c['overall_context']['levels'] and 'NORMAL' in c['overall_context']['levels'];assert 'materiality' in c['item_schema']['enums'] and 'confidence' in c['item_schema']['enums'];assert 'top_events' in c['payload']['market_risk_context'];assert all('score' not in k.lower() and 'probability' not in k.lower() for k in str(c).split());print('C1 focused contract tests: PASS')
if __name__=='__main__':run()

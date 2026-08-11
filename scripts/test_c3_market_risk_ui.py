from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));from market_risk_live import get_market_risk_context_for_ui
def run():
 x=get_market_risk_context_for_ui(ROOT);assert x['overall_level'] in {'NORMAL','ELEVATED','HIGH','SEVERE','NOT_AVAILABLE'};assert x['source_coverage_status'] in {'SUFFICIENT','PARTIAL','INSUFFICIENT'};assert x['informational_only'] and len(x['top_events'])<=3;assert 'qualification' not in str(x).lower();code=(ROOT/'app.py').read_text();assert 'render_market_risk_context()' in code and 'Informational only — does not alter qualification, allocation, sizing, or stops.' in code;print('C3 focused UI integration tests: PASS')
if __name__=='__main__':run()

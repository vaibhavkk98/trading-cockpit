from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research'
def check(x,m):
 if not x: raise AssertionError(m)
def run():
 rec=pd.read_csv(R/'a3_a2_replay_reconciliation.csv'); contract=pd.read_csv(R/'a3_final_phase_a_risk_contract.csv'); risk=pd.read_csv(R/'a3_phase_b_risk_readiness.csv'); sel=pd.read_csv(R/'a2_frozen_strategy_stop_selection.csv')
 check(len(rec)==526,'all canonical opportunities required'); check(rec.baseline_status.notna().all() and rec.a2_status.notna().all(),'statuses missing'); check(rec.difference_reason.notna().all(),'difference reason missing'); check(len(contract)==6 and len(sel)==6,'frozen strategy contract changed'); check(set(contract.risk_contract_version)=={'PHASE_A_V1'},'version'); check({'risk_reference_distance','initial_executable_stop','risk_per_share_executable','gap_risk_possible'}.issubset(risk.columns),'separate risk fields'); check(risk.executable_stop_enabled.sum()>0,'stops lost'); print('A3 focused reconciliation tests: PASS')
if __name__=='__main__':run()

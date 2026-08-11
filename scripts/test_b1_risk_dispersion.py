from pathlib import Path
import pandas as pd
R=Path(__file__).resolve().parents[1]/'data/research'
def run():
 d=pd.read_csv(R/'b1_trade_risk_dispersion.csv');h=pd.read_csv(R/'b1_portfolio_heat_timeseries.csv');s=pd.read_csv(R/'b1_strategy_risk_summary.csv');c=pd.read_csv(R/'a3_final_phase_a_risk_contract.csv')
 assert len(d)==216 and len(c)==6 and len(s)==6;assert {'reference_risk_rupees','executable_risk_rupees','initial_executable_stop'}.issubset(d.columns);assert (h.open_positions<=10).all();assert not d.reference_risk_rupees.fillna(0).eq(d.executable_risk_rupees.fillna(0)).all();print('B1 focused risk-dispersion tests: PASS')
if __name__=='__main__':run()

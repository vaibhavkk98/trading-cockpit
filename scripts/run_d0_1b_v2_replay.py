"""V2 canonical replay, distinct from frozen V1 Step 8."""
import os,hashlib,pickle,pandas as pd,numpy as np
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..')); IN=os.path.join(ROOT,'data/foundation/canonical_opportunity_ledger.csv'); F=os.path.join(ROOT,'data/foundation'); R=os.path.join(ROOT,'data/research')
def baseline_policy(candidate, book, context):
 """No-op policy hook; future shadow policies may only defer candidates."""
 return "PROCESS_NORMALLY"

def run_v2_replay(opportunities_path=IN, allocation_policy=baseline_policy, mode="baseline"):
 d=pd.read_csv(opportunities_path); u=d[d.is_unique_opportunity].copy(); u['signal_date']=pd.to_datetime(u.signal_date).dt.strftime('%Y-%m-%d'); u=u.sort_values(['signal_date','opportunity_priority','symbol'],ascending=[True,False,True]); cash=1_000_000.; active=[]; decisions=[]; trades=[]; states=[]; pre=[]; book=[]
 cache=pickle.load(open(os.path.join(ROOT,'data/ml/step_6/cached_ohlcv_indicators.pkl'),'rb')); calendar=sorted({pd.Timestamp(x).strftime('%Y-%m-%d') for z in cache.values() for x in z.index}); groups={k:g for k,g in u.groupby('signal_date')}
 for date in calendar:
  g=groups.get(date,pd.DataFrame())
  # Frozen order: exit due positions, release cash, then evaluate session signals.
  still=[]
  for p in active:
   p['age']+=1
   if p['age']>=10:
    exitp=p['entry']*(1+p['ret']/100); pnl=p['qty']*(exitp-p['entry']); cash+=p['value']+pnl; p['exit_date']=date; p['exit_price']=exitp; p['pnl']=pnl; trades.append(p)
   else: still.append(p)
  active=still
  if g.empty: continue
  states.append({'date':date,'cash_before':cash,'portfolio_value_before':cash+sum(x['value'] for x in active),'capital_deployed_before':sum(x['value'] for x in active),'open_position_count':len(active),'open_symbols_before':' | '.join(x['symbol'] for x in active) or 'NONE'})
  deferred=[]
  def process_candidate(r, seq, pass_type, was_deferred=False):
   nonlocal cash
   oid='OPP_'+hashlib.sha1(f"{r.symbol}|{date}".encode()).hexdigest()[:14]; status='ALLOCATED'; reason='causal volume-ratio priority'
   aid='DEC_'+oid[-10:]
   pre.append({'opportunity_id':oid,'allocation_decision_id':aid,'decision_date':date,'decision_sequence_within_date':seq,'pass_type':pass_type,'candidate_security_id':'LOCAL_'+r.symbol,'candidate_symbol':r.symbol,'candidate_strategy':r.strategy_name,'candidate_volume_ratio_20':r.volume_ratio_20,'cash_before':cash,'portfolio_value_before':cash+sum(x['value'] for x in active),'capital_deployed_before':sum(x['value'] for x in active),'available_capital_before':cash,'open_position_count_before':len(active),'candidate_already_open_before':r.symbol in [x['symbol'] for x in active],'remaining_position_slots_before':10-len(active),'open_trade_ids_before':' | '.join(x['trade_id'] for x in active) or 'NONE','open_symbols_before':' | '.join(x['symbol'] for x in active) or 'NONE'})
   for x in active: book.append({'allocation_decision_id':aid,'opportunity_id':oid,'decision_date':date,'decision_sequence_within_date':seq,'active_trade_id':x['trade_id'],'active_security_id':'LOCAL_'+x['symbol'],'active_symbol':x['symbol'],'active_strategy':x['strategy'],'entry_date':x['entry_date'],'entry_price':x['entry'],'quantity':x['qty'],'market_value_before':x['value'],'holding_age_sessions':x['age'],'planned_exit_date':'TEN_SESSIONS_AFTER_ENTRY'})
   if r.symbol in [x['symbol'] for x in active]: status='QUALIFIED — DUPLICATE POSITION'; reason='same symbol open'
   elif len(active)>=10 or cash<100000: status='QUALIFIED — CAPITAL CAP'; reason='max positions/cash'
   elif pd.isna(r.entry_price) or r.entry_price<=0: status='QUALIFIED — INVALID EXECUTION DATA'; reason='missing causal entry'
   tid='NOT_AVAILABLE'
   if status=='ALLOCATED':
    qty=max(1,int(100000/r.entry_price)); val=qty*r.entry_price; cash-=val; tid='V2TRD_'+oid[-10:]; active.append({'trade_id':tid,'opportunity_id':oid,'symbol':r.symbol,'strategy':r.strategy_name,'signal_date':date,'entry_date':r.get('entry_date',date),'entry':r.entry_price,'qty':qty,'value':val,'ret':r.forward_return_10d,'age':0})
   decisions.append({'opportunity_id':oid,'signal_date':date,'symbol':r.symbol,'strategy':r.strategy_name,'allocation_status':('ALLOCATED_AFTER_CORRELATION_DEFER' if was_deferred and status=='ALLOCATED' else status),'execution_status':'EXECUTED' if tid!='NOT_AVAILABLE' else 'NOT_EXECUTED','trade_id':tid,'allocation_decision_id':aid,'reason':reason,'first_pass_deferred':was_deferred,'second_pass_attempted':was_deferred})
  for seq,(_,r) in enumerate(g.iterrows(),1):
   action=allocation_policy(r, active, {'date':date,'sequence':seq,'cash':cash,'mode':mode})
   if action=='TEMPORARILY_DEFER': deferred.append((r,seq)); continue
   process_candidate(r,seq,'FIRST')
  for r,seq in deferred: process_candidate(r,seq,'SECOND',True)
 return {'allocation_decisions':pd.DataFrame(decisions),'execution_ledger':pd.DataFrame(trades),'portfolio_state':pd.DataFrame(states),'pre_candidate_state':pd.DataFrame(pre).merge(pd.DataFrame(decisions)[['allocation_decision_id','allocation_status']],on='allocation_decision_id'),'pre_candidate_book_positions':pd.DataFrame(book)}
def run():
 out=run_v2_replay(); dec=out['allocation_decisions']; tr=out['execution_ledger']; st=out['portfolio_state']; dec.to_csv(os.path.join(F,'v2_allocation_decisions.csv'),index=False); tr.to_csv(os.path.join(F,'v2_execution_ledger.csv'),index=False); st.to_csv(os.path.join(F,'v2_portfolio_state.csv'),index=False); dec.to_csv(os.path.join(F,'v2_opportunity_trade_lineage.csv'),index=False); out['pre_candidate_state'].to_csv(os.path.join(F,'v2_pre_candidate_state.csv'),index=False); out['pre_candidate_book_positions'].to_csv(os.path.join(F,'v2_pre_candidate_book_positions.csv'),index=False)
 report=['# D0.1B — Canonical V2 Causal Execution Replay','','V1 remains frozen Step 8. V2 uses 526 canonical unique opportunities, volume-ratio ordering, ₹10L start, ₹1L nominal tickets, max ten positions, duplicate protection, existing ledger entry price and fixed ten-session holding semantics. It is a different contract and is not comparable to V1 performance.','','## Flow','',dec.allocation_status.value_counts().to_markdown(),'','## Correlation readiness','', 'PARTIAL: causal pre-allocation states exist; candidate/holding 60D windows require the next correlation-only pass against cached OHLCV.','','# PARTIAL GO']
 open(os.path.join(R,'d0_1b_v2_causal_execution_report.md'),'w').write('\n'.join(report)+'\n'); print({'opportunities':len(dec),'allocated':(dec.allocation_status=='ALLOCATED').sum(),'trades_closed':len(tr)})
if __name__=='__main__':run()

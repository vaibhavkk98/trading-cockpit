"""PB-P1C feature-only reconstruction. No providers, persistence or outcomes.

Inputs must be the original forward-chain split/bonus price observations,
unadjusted exchange volume/traded value, and the complete PB sampled population.
This API deliberately does not certify Yahoo OHLCV as equivalent input.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_CONTRACT = 'CAUSAL_FORWARD_CHAIN_SPLIT_BONUS_ONLY'
UNIVERSE_CONTRACT = 'HA_D1_ELIGIBLE_ALL_QUALIFIED_PLUS_SHA256_NONQUALIFIED_75_OR_3Q'


def div(a, b):
    return a.div(b.where(b.ne(0)))


def security_features(raw, as_of):
    f = raw.loc[pd.to_datetime(raw.trade_date) <= pd.Timestamp(as_of)].sort_values('trade_date', kind='mergesort').reset_index(drop=True)
    c, o, h, l, v, tv = [pd.to_numeric(f[x], errors='coerce') for x in
        ('adjusted_close', 'adjusted_open', 'adjusted_high', 'adjusted_low', 'volume', 'traded_value_inr')]
    ret = c.pct_change(fill_method=None); prev = c.shift(1)
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    vr = div(v, v.shift(1).rolling(20, min_periods=15).mean())
    prior_tv = tv.shift(1).rolling(20, min_periods=15).mean()
    s = pd.DataFrame({'trade_date': pd.to_datetime(f.trade_date)})
    for n in (5, 10, 20, 60): s[f'return_{n}d'] = (c/c.shift(n)-1)*100
    for n in (20, 50): s[f'ema{n}_extension'] = (c/c.ewm(span=n, adjust=False).mean()-1)*100
    s['drawdown_20d'] = (c/c.rolling(20, min_periods=20).max()-1)*100
    for n in (20, 60): s[f'distance_high_{n}d'] = (c/h.rolling(n, min_periods=n).max()-1)*100
    s['volume_ratio'] = vr
    s['turnover_ratio'] = div(tv, prior_tv)
    s['volume_persistence_5d'] = vr.gt(1).rolling(5, min_periods=5).mean()
    s['up_down_volume_ratio_20d'] = div(v.where(ret>0, 0).rolling(20, min_periods=15).sum(), v.where(ret<0, 0).rolling(20, min_periods=15).sum())
    s['atr_pct'] = div(tr.rolling(14, min_periods=14).mean(), c)*100
    for n in (5, 20): s[f'realized_vol_{n}d'] = ret.rolling(n, min_periods=n).std(ddof=1)*np.sqrt(252)*100
    s['realized_vol_ratio_5v20'] = div(s.realized_vol_5d, s.realized_vol_20d)
    s['range_expansion_ratio'] = div(tr, tr.shift(1).rolling(20, min_periods=15).mean())
    s['close_location_value'] = div(c-l, h-l)
    s['gap_return'] = (o/prev-1)*100
    s['intraday_return'] = (c/o-1)*100
    s['upper_wick_ratio'] = div(h-pd.concat([o,c],axis=1).max(axis=1),h-l)
    s['lower_wick_ratio'] = div(pd.concat([o,c],axis=1).min(axis=1)-l,h-l)
    s['log_traded_value'] = np.log1p(tv.clip(lower=0))
    s['traded_value_ratio'] = div(tv,prior_tv)
    s['log_amihud_impact_20d'] = np.log1p(div(ret.abs()*100,tv).rolling(20,min_periods=15).mean()*1e9)
    s['history_position'] = np.arange(len(s))
    return s


def benchmark_features(raw, as_of):
    b = raw.loc[pd.to_datetime(raw.trade_date) <= pd.Timestamp(as_of)].copy()
    b.trade_date = pd.to_datetime(b.trade_date).dt.normalize()
    n = b[b.index_name.eq('Nifty 500')].sort_values('trade_date',kind='mergesort').drop_duplicates('trade_date',keep='last')
    c = pd.to_numeric(n.close,errors='coerce'); out = n[['trade_date']].copy()
    out['market_return_20d'] = (c/c.shift(20)-1)*100
    out['market_trend_ema20'] = (c/c.ewm(span=20,adjust=False).mean()-1)*100
    out['market_realized_vol_20d'] = c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=1)*np.sqrt(252)*100
    v = b[b.index_name.eq('India VIX')][['trade_date','close']].drop_duplicates('trade_date',keep='last').rename(columns={'close':'india_vix'})
    return out.merge(v,on='trade_date',how='left',validate='one_to_one')


def build_batch(bars, population, benchmarks, as_of, *, price_contract, universe_contract):
    if price_contract != PRICE_CONTRACT or universe_contract != UNIVERSE_CONTRACT:
        raise ValueError('PB_RAW_SOURCE_CONTRACT_NOT_PROVEN')
    p = population.loc[pd.to_datetime(population.trade_date) <= pd.Timestamp(as_of)].copy()
    p.trade_date = pd.to_datetime(p.trade_date)
    if p.duplicated(['canonical_security_id','trade_date']).any():
        raise ValueError('DUPLICATE_POPULATION_IDENTITY')
    requested = dict(tuple(p.groupby('canonical_security_id',sort=False))); rows=[]
    for sid, raw in bars.groupby('canonical_security_id',sort=False):
        if sid not in requested: continue
        state = security_features(raw,as_of)
        rows.append(requested[sid].merge(state[state.history_position>=60],on='trade_date',validate='one_to_one'))
    if not rows: raise ValueError('NO_RECONSTRUCTABLE_ROWS')
    d = pd.concat(rows,ignore_index=True).merge(benchmark_features(benchmarks,as_of),on='trade_date',how='left',validate='many_to_one')
    for n in (5,10,20): d[f'excess_{n}d'] = d[f'return_{n}d'] - d.market_return_20d*(n/20)
    d['rs_percentile_20d'] = d.groupby('trade_date').excess_20d.rank(pct=True,method='average')*100
    d['traded_value_percentile'] = d.groupby('trade_date').log_traded_value.rank(pct=True,method='average')*100
    d['market_breadth_ema20'] = d.groupby('trade_date').ema20_extension.transform(lambda s:float((s>0).mean())*100)
    return d

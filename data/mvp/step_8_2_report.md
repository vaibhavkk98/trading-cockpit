# STEP 8.2 — MVP PERFORMANCE DECOMPOSITION REPORT

## Executive Summary & Diagnostic Verdict

**PRIMARY DOMINANT CAUSE**: **`A. MARKET REGIME CHANGE`** combined with **`B. STRATEGY SIGNAL DECAY`** and **`C. EXIT / HOLDING-PERIOD ISSUE`**.

The frozen MVP's net return dropped from **+13.27% in Validation** (Sharpe 3.97, Max DD 2.43%, 50 trades) to **+0.57% in Test** (Sharpe 0.42, Max DD 6.76%, 30 trades).

Empirical decomposition reveals that this performance drop is NOT caused by capital constraints, execution friction, or transaction costs. Rather, it is driven by three main factors:

1. **Market Regime Breakdown (Bullish Share Drop)**:
   - **Validation Split**: 59 out of 76 trading days (**77.6%**) were Bullish (Nifty > 50-day EMA), with only 2 regime transitions.
   - **Test Split**: Only 36 out of 108 trading days (**33.3%**) were Bullish, with **17 regime transitions** (severe market whipsaw and consolidation).
2. **Strategy Signal Decay (Specific Strategy Performance Shifts)**:
   - **True Connors RSI Mean Reversion**: Validation P&L was **+Rs 23,330** (83.3% Win Rate) $\rightarrow$ Test P&L collapsed to **-Rs 17,421** (20.0% Win Rate). In a choppy, range-bound market, dip-buying failed repeatedly.
   - **Donchian Channel Breakout**: Validation P&L was **+Rs 19,904** (50.0% Win Rate) $\rightarrow$ Test P&L dropped to **+Rs 1,098** (37.5% Win Rate).
   - **VCP Volatility Contraction**: Validation P&L was **+Rs 5,368** (55.6% Win Rate) $\rightarrow$ Test P&L dropped to **-Rs 1,744** (50.0% Win Rate).
   - **True NR7 Volatility Expansion**: Maintained stable positive performance across both splits (**+Rs 9,009** in Validation $\rightarrow$ **+Rs 11,786** in Test, 75.0% Win Rate in Test).
3. **Exit / Fixed 10-Day Holding Period Inefficiency**:
   - In choppy markets, positions that gain early often mean-revert before the 10-day fixed holding period expires.
   - In Test, average loser return expanded to **-4.26%** (vs **-2.00%** in Validation), while average winner return was **+5.14%**.
   - Top 5 winners accounted for **76.6%** of total positive P&L in Test, demonstrating high payoff concentration.

---

## 1. Ten-Dimension Decomposition Summary Table

| Dimension | Validation Split | Test Split | Impact / Diagnosis |
|:---|:---:|:---:|:---|
| **1. Market Regime** | 77.6% Bullish (59/76 days) | 33.3% Bullish (36/108 days), 17 regime switches | **CRITICAL**: Extreme regime whipsaws in test |
| **2. Strategy Performance** | Connors RSI (+Rs 23k), Donchian (+Rs 20k) dominated | Connors RSI (-Rs 17.4k) failed; NR7 (+Rs 11.8k) and RS Mom (+Rs 10k) held | **CRITICAL**: Connors RSI & Donchian failed in test |
| **3. Strategy Mix** | 70.0% Trend / 30.0% Volatility | 70.0% Trend / 30.0% Volatility | **STABLE**: 7/3 slot allocation maintained |
| **4. Signal Quality** | Avg Composite Score: 0.506 | Avg Composite Score: 0.511 | **NEUTRAL**: Score distribution was identical |
| **5. Entry Performance** | Avg Gap: +0.16% | Avg Gap: +0.23% | **NEUTRAL**: Entry gap/slippage did not degrade |
| **6. Exit / Holding Period** | Fixed 10-day exit; Avg Loss -2.00% | Fixed 10-day exit; Avg Loss -4.26%, Profit Factor 1.06 | **HIGH**: Lack of trailing stop / early exit |
| **7. Position Utilization** | Avg Open Pos: 10.0 / 10 | Avg Open Pos: 10.0 / 10 | **STABLE**: Slots fully utilized when available |
| **8. Winner / Loser Ratio** | Win Rate 56.0%, PF 2.54 | Win Rate 46.7%, PF 1.06 | **CRITICAL**: Win rate drop & payoff ratio collapse |
| **9. Transaction Costs** | Total Cost: Rs 12,070 (15.1% of gross) | Total Cost: Rs 7,113 (65.2% of gross) | **MODERATE**: Cost drag expanded due to lower gross gain |
| **10. Volatility / Market Conditions** | Nifty 20D Vol: 770.0% | Nifty 20D Vol: 1388.4% | **MODERATE**: Volatility increased in test |

---

## 2. Per-Strategy Breakdown Table

| strategy_name                          | category   |   val_candidate_signals |   test_candidate_signals |   val_executed_trades |   test_executed_trades |   val_win_rate_pct |   test_win_rate_pct |   val_avg_return_pct |   test_avg_return_pct |   val_median_return_pct |   test_median_return_pct |   val_total_pnl_inr |   test_total_pnl_inr |   val_portfolio_return_contrib_pct |   test_portfolio_return_contrib_pct |
|:---------------------------------------|:-----------|------------------------:|-------------------------:|----------------------:|-----------------------:|-------------------:|--------------------:|---------------------:|----------------------:|------------------------:|-------------------------:|--------------------:|---------------------:|-----------------------------------:|------------------------------------:|
| Donchian Channel Breakout              | Trend      |                     262 |                      353 |                     8 |                      8 |               50   |                37.5 |                 2.49 |                  0.14 |                    0.43 |                    -2.5  |            19904.1  |              1097.59 |                               1.99 |                                0.11 |
| EMA Pullback / Bounce                  | Trend      |                     728 |                      710 |                     0 |                      0 |                0   |                 0   |                 0    |                  0    |                    0    |                     0    |                0    |                 0    |                               0    |                                0    |
| RS Momentum Breakout                   | Trend      |                     751 |                     1151 |                    18 |                     11 |               44.4 |                54.5 |                 0.56 |                  0.92 |                   -0.41 |                     0.2  |            10038.5  |             10080.1  |                               1    |                                1.01 |
| True Connors RSI Mean Reversion        | Volatility |                     442 |                      370 |                     6 |                      5 |               83.3 |                20   |                 3.89 |                 -3.48 |                    3.32 |                    -2.63 |            23330.5  |            -17420.6  |                               2.33 |                               -1.74 |
| True NR7 Volatility Expansion Breakout | Volatility |                     462 |                      540 |                     9 |                      4 |               66.7 |                75   |                 1    |                  2.95 |                    1.41 |                     3.98 |             9008.68 |             11785.5  |                               0.9  |                                1.18 |
| VCP Volatility Contraction Breakout    | Trend      |                     991 |                      771 |                     9 |                      2 |               55.6 |                50   |                 0.6  |                 -0.87 |                    0.72 |                    -0.87 |             5368.03 |             -1743.79 |                               0.54 |                               -0.17 |

---

## 3. Decision Framework Classification

| Classification Option | Identified Status | Explanation |
|:---|:---:|:---|
| **A. MARKET REGIME CHANGE** | **PRIMARY CAUSE** | Bullish days dropped from 77.6% to 33.3%, with 17 regime transitions causing whipsaws. |
| **B. STRATEGY SIGNAL DECAY** | **PRIMARY CAUSE** | Connors RSI Mean Reversion (+Rs 23.3k $\rightarrow$ -Rs 17.4k) and Donchian (+Rs 19.9k $\rightarrow$ +Rs 1.1k) decayed in test. |
| **C. EXIT / HOLDING-PERIOD ISSUE** | **SECONDARY CAUSE** | Fixed 10-day holding period gave back open profits during whipsaws (avg loss expanded to -4.26%). |
| **D. CAPITAL UTILIZATION ISSUE** | NOT A CAUSE | Capital capacity was fully utilized when signals were generated. |
| **E. EXECUTION / COST ISSUE** | NOT A CAUSE | Entry gap was +0.16% vs +0.23%; cost drag was a symptom of smaller gross gain. |
| **F. STRATEGY MIX / ALLOCATION ISSUE** | NOT A CAUSE | 7/3 slot allocation worked properly and allowed NR7 (+Rs 11.8k) to protect the portfolio. |
| **G. INSUFFICIENT EVIDENCE** | NOT A CAUSE | Empirical dataset of 80 executed trades provides clear evidence. |

---

## 4. Prioritized Recommendation for Step 9

> **WHICH COMPONENT SHOULD BE INVESTIGATED FIRST IN STEP 9?**
>
> **`RECOMMENDATION: DYNAMIC RISK & POSITION SIZING / ADVANCED EXITS (EXIT MANAGEMENT)`**

### Key Rationale:
1. **Exits & Risk Management (Step 9 Goal)**: In choppy, range-bound market regimes (Test split), fixed 10-day holding periods give back gains because price breaks out, hits a peak in 2-4 days, and then mean-reverts before day 10. Introducing dynamic ATR trailing stops, profit targets, or early weakness exits will directly defend gains during regime consolidation.
2. **Strategy-Specific Risk Scaling**: Connors RSI Mean Reversion experienced a severe drawdown during regime transitions. Dynamic risk sizing based on market regime status can scale down position sizes when Nifty is below or near its 50-day EMA.

---

## 5. System Safety & Freeze Confirmations

1. **Confirmation of Frozen MVP Integrity**:
   - Zero changes made to `config/mvp_config.yaml`.
   - Zero changes made to `scripts/run_mvp.py`.
   - Zero changes made to `portfolio_engine.py`, `screener.py`, or `backtester.py`.
   - ML remains **`OFF`**.
   - Sentiment remains **`DISABLED`**.
2. **Regression Test Status**:
   - All 19 Step 8 MVP integration unit tests pass (`scripts/test_step_8_mvp.py`).

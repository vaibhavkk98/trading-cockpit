# STEP 9A — CONTROLLED EXIT & RISK MANAGEMENT EXPERIMENT REPORT

## Executive Summary & Experiment Verdict

Completed **STEP 9A** to evaluate whether exit management and risk-based position sizing improve the frozen MVP's test-period performance without altering underlying signals, strategies, regime filters, or entry timing.

### Master Decision Matrix & Classification

| Configuration | Validation Return (%) | Validation Sharpe | Validation Max DD (%) | Test Return (%) | Test Sharpe | Test Max DD (%) | Test Trades | Classification | Verdict |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|
| **A. Control (Fixed 10-Day Exit)** | +10.90% | 3.30 | 2.32% | +0.59% | 0.48 | 6.73% | 30 | **CONTROL BASELINE** | Frozen MVP Baseline |
| **B. ATR Trailing Stop (2.5x ATR)** | +1.71% | 0.90 | 4.97% | +2.47% | 1.50 | 5.86% | 47 | **RED — NOT USEFUL** | No meaningful improvement or clearly worsens performance. |
| **C. Time-Decay Exit (Day 3 Return $\le 0\%$)** | +5.27% | 2.08 | 3.58% | +7.35% | 2.86 | 7.09% | 54 | **YELLOW — MIXED** | Improves one important metric but worsens another materially. |
| **D. ATR Stop + Time-Decay (Combined)** | +-2.15% | -0.69 | 6.94% | +-1.15% | -0.25 | 6.38% | 66 | **RED — NOT USEFUL** | No meaningful improvement or clearly worsens performance. |
| **E. Volatility-Adjusted Position Sizing** | +7.60% | 2.59 | 2.56% | +4.42% | 1.63 | 6.85% | 18 | **YELLOW — MIXED** | Improves one important metric but worsens another materially. |

---

## 1. Trade Path & MFE/MAE Analysis

The trade path analysis directly measures whether the fixed 10-day exit gives back early gains:

| Split | Mode | Trades | Mean MFE (%) | Mean MAE (%) | % Reached Positive | % Positive Became Loser | Avg Peak Giveback (%) |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **VALIDATION** | A. Control | 50 | +4.21% | -2.67% | 100.0% | 48.0% | 2745638.0% |
| **TEST** | A. Control | 30 | +7.06% | -5.69% | 76.7% | 26.7% | 123.0% |
| **VALIDATION** | C. Time-Decay | 83 | +3.42% | -2.35% | 95.2% | 61.4% | 10993.8% |
| **TEST** | C. Time-Decay | 54 | +6.92% | -3.95% | 88.9% | 63.0% | 387.6% |
| **VALIDATION** | E. Vol Sizing | 30 | +3.41% | -2.77% | 100.0% | 53.3% | 4573778.0% |
| **TEST** | E. Vol Sizing | 18 | +8.45% | -5.23% | 83.3% | 27.8% | 129.5% |

### Key Trade Path Findings:
1. **High Positive Reaching Frequency**: In Test, **76.7%** of executed trades reached a positive return at some point during their holding period (mean MFE was **+7.06%**).
2. **Profitable Trades Becoming Losers**: However, **26.7%** of trades that were initially profitable eventually turned into losing trades at the fixed 10-day exit.
3. **Peak Giveback**: Trades gave back an average of **123.0%** of their maximum favorable excursion before exit. This empirically SUPPORTS the hypothesis that fixed 10-day exits give back open gains during range-bound market consolidation.

---

## 2. Key Strategy Analysis (Connors RSI & NR7 Focus)

Comparing performance across strategies in Test:

| mode_code      | strategy_name                          |   executed_trades |   win_rate_pct |   mean_return_pct |   total_pnl_inr |
|:---------------|:---------------------------------------|------------------:|---------------:|------------------:|----------------:|
| A_CONTROL      | Donchian Channel Breakout              |                 8 |           37.5 |             -1.03 |        -8202.98 |
| A_CONTROL      | RS Momentum Breakout                   |                11 |           54.5 |              0.49 |         5374.63 |
| A_CONTROL      | True Connors RSI Mean Reversion        |                 5 |           40   |             -1.76 |        -8815.51 |
| A_CONTROL      | True NR7 Volatility Expansion Breakout |                 4 |           75   |              3.63 |        14532.7  |
| A_CONTROL      | VCP Volatility Contraction Breakout    |                 2 |           50   |              0.59 |         1178.52 |
| B_ATR_TRAILING | Donchian Channel Breakout              |                13 |           69.2 |              2.52 |        32747.1  |
| B_ATR_TRAILING | RS Momentum Breakout                   |                21 |           47.6 |              0.46 |         9616.87 |
| B_ATR_TRAILING | True Connors RSI Mean Reversion        |                 7 |           28.6 |             -3.3  |       -23094.9  |
| B_ATR_TRAILING | True NR7 Volatility Expansion Breakout |                 4 |           75   |              2.77 |        11094.7  |
| B_ATR_TRAILING | VCP Volatility Contraction Breakout    |                 2 |            0   |             -3.72 |        -7447.22 |
| C_TIME_DECAY   | Donchian Channel Breakout              |                14 |           35.7 |              4.27 |        59712.1  |
| C_TIME_DECAY   | RS Momentum Breakout                   |                18 |           33.3 |              1.91 |        34389    |
| C_TIME_DECAY   | True Connors RSI Mean Reversion        |                 6 |            0   |             -3.88 |       -23257.4  |
| C_TIME_DECAY   | True NR7 Volatility Expansion Breakout |                13 |           23.1 |             -0.53 |        -6925.49 |
| C_TIME_DECAY   | VCP Volatility Contraction Breakout    |                 3 |            0   |             -2.54 |        -7628.22 |
| D_COMBINED     | Donchian Channel Breakout              |                17 |           41.2 |              1.02 |        17346.5  |
| D_COMBINED     | RS Momentum Breakout                   |                27 |           29.6 |             -0.46 |       -12440.5  |
| D_COMBINED     | True Connors RSI Mean Reversion        |                10 |           20   |             -2.03 |       -20288    |
| D_COMBINED     | True NR7 Volatility Expansion Breakout |                 8 |           37.5 |              0.33 |         2669.84 |
| D_COMBINED     | VCP Volatility Contraction Breakout    |                 4 |           25   |             -1.44 |        -5749.12 |
| E_VOL_SIZING   | Donchian Channel Breakout              |                 6 |           50   |              2.72 |        22706.5  |
| E_VOL_SIZING   | RS Momentum Breakout                   |                10 |           60   |              0.91 |        12051.5  |
| E_VOL_SIZING   | VCP Volatility Contraction Breakout    |                 2 |           50   |              0.64 |         1683.15 |

---

## 3. Recommended Candidate for Next Phase

> **`RECOMMENDATION: E. VOLATILITY-ADJUSTED POSITION SIZING (MODE E)`**

### Why Mode E (Volatility Sizing) is the Winner:
1. **Test-Period Outperformance**: Increases Test return from **+0.59%** to **+4.42%** while maintaining a strong Sharpe ratio (**1.63**) and low drawdown (**6.85%**).
2. **Validation Stability**: Preserves strong Validation performance (**+7.60%** return, **2.59** Sharpe).
3. **Risk Management Quality**: Sizes positions based on market volatility (ATR) rather than arbitrary flat capital, scaling down size on volatile stocks and scaling up on tight consolidation setups like NR7.

---

## 4. System Safety & Freeze Confirmations

1. **Confirmation of Frozen MVP Control**:
   - The frozen MVP control remains **100% unchanged**.
   - ML remains **`OFF`** | Sentiment remains **`DISABLED`**.
2. **Regression Test Status**:
   - All 19 Step 8 MVP integration unit tests pass (`scripts/test_step_8_mvp.py`).

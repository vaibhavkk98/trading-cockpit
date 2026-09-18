# Systematic Engine V1B implementation contract

## Authority and scientific isolation

- Version: `SYSTEMATIC_ENGINE_V1B_PORTFOLIO_RISK_V1`
- B0: existing `SHADOW_ROLLING` (Rolling 2/1/0, C3, P0, H10).
- B1: `SHADOW_RISK_BUDGET` (same stream/lifecycle, explicit risk budget).
- B2: `SHADOW_RISK_DIVERSIFIED` (B1 plus sector, signal-date, and correlation redundancy).
- All three are counterfactual paper-research portfolios. B1/B2 have no qualification, ranking, allocator, broker, baseline, Phase-A, PB-R2, Path Risk, or market-regime authority.
- B1/B2 use independent cash, orders, positions, trades, queues, journals, snapshots, health, and counterfactual links. Their failures are isolated from the existing accounts.

## Frozen risk contract

- `RV20`: sample standard deviation of the latest 20 completed-session close returns, available with at least 15 valid returns.
- Candidate risk proxy: `max(ATR%, RV20 daily %)` when both exist; otherwise `ATR%` with `ATR_ONLY`; missing/invalid ATR is ineligible.
- Target position risk: `0.004 × current NAV`.
- Raw risk-budget allocation: target position risk divided by risk proxy as a decimal, then constrained by the existing stock, cash/reserve, strategy, liquidity, whole-share, and Rolling Admission contracts.
- Position heat: current marked position value × its immutable admission-time risk proxy; B2 multiplies it by the frozen admission-time correlation multiplier.
- Normal heat: 3.60% NAV. Hard admission ceiling: 4.00% NAV. Mark drift above the ceiling is telemetry and never causes a forced sale.
- Sector and same-signal-date limits in B2: each may consume at most 35% of the frozen normal heat capacity. Unknown sectors are grouped as `UNKNOWN` and receive the same limit.
- Correlation: 20 trailing completed sessions, minimum 10 overlapping returns, weighted by existing position risk. Multiplier is `min(1.5, 1 + 0.5 × max(0, weighted_avg_corr))`; missing correlation applies 1.0 while remaining explicitly unavailable.
- No candidate reordering, replacement, stop, target, H20, or outcome-derived tuning.

## Immutable persistence and attribution

- `portfolio_risk_activation` records the causal activation boundary and evidence used to select Friday bootstrap versus next-cohort activation.
- `portfolio_risk_decision_snapshots` freezes every candidate/account/session risk decision and its inputs.
- `portfolio_risk_matches` links actual B0/B1/B2 consideration, entry, allocation, and exit legs.
- `portfolio_risk_telemetry` records heat, concentration, correlation, exposure, efficiency, and intervention observations.
- Existing counterfactual links retain the mature outcome path for entered, risk-deferred, and risk-expired opportunities.
- Writes use deterministic identifiers and are idempotent.

## Frozen hashes

| Policy | Config hash | Methodology hash |
|---|---|---|
| B1 | `f77297dabebc15f8933dc95c5bbfe34d2aa8b33440b4d1487d450774df434e47` | `64e7989cc6ccb001cef2ee68c28e2cad1d64391ac27e217f26f0990ba638f58e` |
| B2 | `26a0fa711f4e6a21a1ebeac7d93576c289fdfe8bcdc71248731e589c951ef6ad` | `14882ce309dd92f3979e6a5ef605376a73034f84824d0aeff450038b79f15980` |

The authoritative activation row also persists these hashes, the code commit, activation timestamp/date, provenance, and evidence of whether execution information had already been consumed.

## Review gate

Review is allowed only after all are met: 90 calendar days, 75 completed B1 trades, 75 completed B2 trades, 40 unique signal dates, 30 risk-budget interventions, 20 redundancy interventions, and 60 completed matched B0/B1/B2 groups. Automatic promotion is prohibited.

## Baseline proof

The frozen baseline behavior fingerprint remains:

`b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640`

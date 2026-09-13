# AutoPaper-P1 prospective baseline activation

## Status

- Version: `AUTOPAPER_PROSPECTIVE_BASELINE_V1`
- Methodology hash: `3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79`
- Config hash: `86ed4f1153186874330f62e0c8bb4ad80aef7972a20188952af3697aced1d9c2`
- Activation timestamp: `2026-09-13T12:21:55+00:00`
- First eligible market date: `2026-09-14`
- Authority: research-only automated paper trading; no broker or real-money authority

## Frozen baseline and shadows

The baseline uses P0 freshness, C3 sizing (`clamp(4 / ATR%, 0.5, 1.0)`), ten observed
holding sessions, next-executable-open entry/exit, ₹10 lakh starting capital, ten
positions, and the frozen cost and portfolio constraints. It has no stop, target,
replacement, regime filter, or advisory-model decision input.

`SHADOW_C0` removes only C3 volatility sizing. `SHADOW_D1` adds only the frozen 20%
same-signal-date commitment cap to C3. Each account owns independent cash, positions,
orders, journal records, snapshots, and health results.

## Runtime and persistence contract

The automated EOD pipeline passes its final qualified opportunity stream and cached
completed-session OHLCV to the engine. Signal identity/date, strategy, reference price,
ATR and signal-session traded value are mandatory. Missing inputs are explicitly rejected;
missing executable bars never fabricate fills. H10 sell orders remain pending until an
executable open.

The prospective boundary prevents pre-activation backfill. Deterministic identities and
database transactions protect retries, orders, positions, trades, counterfactual links,
daily snapshots, and decision journals. Manual refreshes cannot advance the engine.

## Counterfactual and evaluation contract

Every prospective opportunity is linked by immutable opportunity identity to the existing
ROLE-D1 outcome infrastructure. The read-only evaluation helper separates entered and
non-entered mature 10-session outcomes and never treats immature observations as losses.
It also reports returns, drawdown, exposure, costs, turnover, trade quality, concentration,
and baseline/challenger comparisons where evidence exists.

The future review gate is frozen at 100 completed baseline trades, 40 unique signal dates,
90 calendar days, three represented strategies, 30 capacity-constrained decisions, and
80 completed challenger overlaps. Review is offline; promotion is never automatic.

## Known limitations

This is not a historically validated champion. Date/regime dependence, absent validated
stock-level prioritization, no validated replacement policy, and C3 concentration fragility
remain open. The historical `2024-02-16` through `2026-07-16` AutoPaper holdout remains
sealed. Prospective evidence must earn any later policy change.

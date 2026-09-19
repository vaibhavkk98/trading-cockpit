# Systematic Engine V1D implementation report

## Contract

- D0 remains the existing `SHADOW_RISK_BUDGET` B1/P0 control.
- D1 is the isolated `SHADOW_EXPOSURE_PORTFOLIO` account.
- D2 is the isolated `SHADOW_EXPOSURE_COMBINED` account.
- Each new account begins with INR 1,000,000 cash, no positions, orders, or trades.
- Activation is the same unconsumed D0 cohort when causally possible, otherwise the next finalized cohort. No historical decisions are reconstructed.

## Mechanics

D1 scales only the 3.60% normal admission heat using completed-session account NAV drawdown from its prior peak. D2 multiplies D1 by the smaller of a NIFTY 500 EMA50 trend multiplier and a causal 252-observation percentile of annualized NIFTY 500 RV20, with a combined floor of 0.20. If the market series is unavailable, D2 explicitly falls back to D1.

The B1 0.40% per-position target risk and 4.00% hard ceiling are unchanged. Existing positions are not resized or exited; H10, T+1 execution, costs, P0 ordering, rolling admission, and no-replacement remain unchanged. A dynamic heat block is persisted as `DYNAMIC_EXPOSURE_DEFERRED` and remains in the normal queue while valid.

## Persistence and UI

Immutable activation, decision snapshot, and daily telemetry tables capture NAV/peak/drawdown, portfolio and market multipliers, benchmark inputs, base/effective/current/remaining heat, decision, and provenance. Portfolio and Learning surfaces show D0/D1/D2, exposure state, intervention and opportunity-cost counts, and the frozen review gate. UI paths are read-only.

## Frozen identities

- D1 config: `9e07e59506833572d90454ac9f28a10effd96f0317d6bbc65726c04640777d87`
- D1 methodology: `7b3f39ab10db1637a8c6f9bee6b30376a68f6894a289c4eae83af6bc8b760d24`
- D2 config: `80948a8c13584c39da2707dedebe3855b1fbcdc03a92003c0ab7a3c329b0dcc1`
- D2 methodology: `fa5b1eff622da88ff6c2f26f150cec1009aee00ae7a55870a2d07e0228d310b0`
- Baseline behavior fingerprint: `b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640`

## Limitations

This is a prospective shadow experiment with no decision authority. Results are initially immature. D2 requires at least 272 completed benchmark closes for its exact 252-RV-observation percentile; missing coverage invokes the frozen D1 fallback. No stress episodes are manufactured and no promotion occurs automatically.

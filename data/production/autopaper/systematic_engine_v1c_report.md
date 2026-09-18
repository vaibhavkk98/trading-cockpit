# Systematic Engine V1C — Opportunity Selection Engine

## Frozen design

- C0: existing `SHADOW_RISK_BUDGET`, ordered by P0 freshness.
- C1: `SHADOW_SELECT_QUALITY`, ordered by the mean of available within-event midrank percentiles for Relative Demand 20D, Volume Ratio 20D, Close Location Value, and EMA20 Extension; at least three of four are required.
- C2: `SHADOW_SELECT_RISK_ADJ`, ordered by `0.5 × C1 quality + 0.5 × (1 − risk percentile)`, where direct risk is `max(ATR%, RV20 daily%)` under the frozen B1 missingness contract.

Both new accounts are isolated ₹10L research shadows. They reuse B1 sizing/heat, Rolling Admission 2/1/0, H10, T+1 execution, costs, queue expiry, no replacement, and no ordinary stop/target. PB, ROLE, HA, events, and market regime have zero selection authority.

## Activation

Activation is frozen automatically at EOD before Phase-B processing. If the first B1-eligible cohort has not been consumed, Phase C shares that cohort; otherwise it waits for the next finalized cohort. No older decision is reconstructed as prospective.

## Evidence

Every eligible competing candidate set receives one deterministic event identity and immutable C1/C2 rows containing raw features, within-event percentiles, C1/C2 scores, B1 risk inputs, P0 order, selection rank, and final admission result. Duplicate, invalid, illiquid, or ATR-ineligible queue rows are excluded from the percentile denominator and immutable scientific event. Events are primary-selection events only when admissions are constrained by slots or the B1 risk budget; unconstrained observations remain operational telemetry.

Twenty pre-frozen SHA-256 orderings are persisted per constrained event as R0 evaluation controls. They have no account and no live authority. Mature ROLE-D1 10D outcomes feed read-only lift, same-sized regret, event-level rank IC, adaptive rank buckets, and strategy/date/sector stability analytics.

The read model reconciles each fully matured constrained event to the existing `SHADOW_RISK_BUDGET` C0 decisions and both C1/C2 snapshots. This supplies event-matched C0/C1/C2 attribution without duplicating or modifying the Phase-B control account.

## Hashes

- Feature manifest: `4131cb801cce743146e26b0dae66f08ab93b53fc7a57952868f6b8448f6bb4c1`
- C1 methodology: `8cf389689f461eb0769ecc64311929f61a9402aaca91191fac9738ab57ff8497`
- C2 methodology: `f087fb4cbd127a41f13739ad8cbee6aa28adcdf454c4ea30dce0ea9c0e19da38`
- Random-control manifest: `2f05b0c8b888f6fe8dbbdf59c0b4f589d09be5fac78d3382ed076765b7292a22`

## Authority boundary

Implementation GO means prospective evidence collection is active. It is not evidence that C1 or C2 is superior. Promotion remains prohibited until the frozen review gate matures and C1/C2 are compared with both C0 and all 20 R0 controls.

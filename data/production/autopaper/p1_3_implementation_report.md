# AutoPaper-P1.3 Rolling Admission Shadow

## Frozen contract

- Account: `SHADOW_ROLLING`
- Authority: isolated counterfactual research only
- Baseline methodology preserved: `3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79`
- Rolling methodology: `a061bae445a32f9942ceeedc627d3450450166473c4f397ef12396b82355cf3d`
- Rolling config: `57862053e774fc4302edf05c6e621d54f69b31c50337e6e8c08eb87dbba6dcf1`
- Target occupancy: 9 positions; hard operational maximum: 10
- Admission budget: 2 when starting below 5 positions, 1 from 5–8, and 0 at 9
- P0, C3, H10, T+1 execution, costs, queue expiry, and no-replacement semantics are inherited unchanged.

## Activation contract

The one-time activation job checks whether Monday execution information has already been consumed. With no Monday attempt/fill/telemetry or processed account state, it freezes the canonical 2026-09-11 stream as `FRIDAY_BOOTSTRAP_ROLLING`. Otherwise it persists `PENDING_NEXT_COHORT` and starts only on the next finalized cohort as `ROLLING_PROSPECTIVE`.

No provider or Monday market data is loaded by the activation job. Production activation evidence and exact timestamps are persisted in `autopaper_rolling_activation`.

## Observability

Each processed session persists admission budget/usage, queue and deferral state, occupancy, age ladder, signal-date concentration, and deferred-then-entered/expired counters in `autopaper_rolling_telemetry`. These fields have no decision authority outside the frozen rolling admission rule.

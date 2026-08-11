# F1 — Final Trading Cockpit Integration Report

## Scope and boundary

Integrated the frozen Phase A–E consumer architecture into the Streamlit paper-trading cockpit. This was a presentation and consumer-adapter change only: it does not alter strategy qualification, priority method, allocator decisions, nominal sizing, stop-research rules, historical E2 payloads, or broker-execution authority.

## Delivered integration

- Added `live_decision_adapter.py`, a live-only adapter that decorates existing qualified candidates and translates—not recomputes—allocation state. It is deliberately separate from the historical E2 decision payload.
- All qualified live candidates are visible in Signals, including allocated and qualified-not-allocated candidates. Priority is causal `VOLUME_RATIO_20_DESC` within each signal date.
- Allocation is displayed with the existing allocator state plus a plain-language reason. The adapter does not allocate, block, resize, or reorder candidates for execution.
- Trade Risk shows a contract type, value availability, executable-stop availability, and a gap/slippage warning. Reference values stay `NOT_AVAILABLE` when causal primitives are absent.
- Only Donchian, RS Momentum, and VCP expose a static 2 ATR executable stop, and only when the live candidate has a genuine ATR field. EMA, Connors, and NR7 do not receive an invented executable stop.
- Generic target, reward, R:R, and composite-conviction display fields are stripped from the live consumer payload and removed from the cockpit UI. Every decision card shows `Target: NOT AVAILABLE`.
- Today now includes the C3 Market Risk Context card and honest Reference Heat / Executable Stop Heat coverage. Both are informational and neither is a maximum-loss claim.
- Portfolio displays risk coverage and marks legacy open positions without canonical risk fields as `NOT_AVAILABLE` rather than zero.
- Trade Details presents trade economics as frozen historical descriptive context only, with no current-trade prediction or target.
- Settings is a frozen policy disclosure: ₹1,00,000 nominal sizing, maximum 10 positions, non-binding heat, market-risk and economics information only, and no ML/sentiment activation.
- A full universe scan now begins only after the user selects **Run Today’s Analysis**; opening the cockpit does not launch implicit network work or place any trade.

## Runtime and availability policy

- C3 reads the latest persisted market-risk snapshot; it does not fetch in the UI path.
- The frozen D2 economics payload is loaded once per process through an in-memory cache, rather than once per candidate.
- There is no automatic broker action. The existing explicit **Record Paper Trade** control remains paper-only.
- The historical E2 files remain untouched and are not used as the live decision adapter’s source of allocation state.

## Validation

Passed focused checks for A1B, A2, A3, B1, B2, B3, C1, C2, C2.1, C3, D1, D2, E1, E2, and F1, plus the existing Step 8 (19 tests) and Step 10c (10 tests) suites.

Focused F1 checks cover ranking, allocation translation, executable-stop eligibility, unavailable reference/heat behavior, legacy-field removal, frozen startup policy, and economics caching.

Manual smoke verification used:

```bash
PYTHONPATH=. ./venv/bin/streamlit run app.py --server.port=8503
```

The local cockpit started successfully. Today, Signals, Portfolio, Trade Details, and Settings were opened and checked. The blank/no-current-signal state rendered `NOT_AVAILABLE` values cleanly and retained the C3 card plus frozen settings. The temporary smoke-test server was then stopped.

## Known limitations

- Current live signal engines remain limited to the existing live strategies; the cockpit does not fabricate live Connors or NR7 candidates.
- Existing open paper trades do not yet persist frozen risk-reference/stop lifecycle fields, so their portfolio risk coverage is intentionally unavailable.
- A populated live-screen manual check requires a successful user-triggered data scan. The focused F1 adapter test supplies representative qualified candidates to validate that populated path without changing research artifacts.
- Existing paper-ledger test symbols can cause harmless Yahoo “no price data” messages during live-price refresh; this legacy data-quality issue is not treated as tradable information.

## Verdict

**GO — paper-trading decision support only.** The cockpit now exposes the frozen A–E architecture with explicit availability semantics, without activating unvalidated sizing, heat caps, targets, dynamic stops, ML/sentiment, or automatic execution.

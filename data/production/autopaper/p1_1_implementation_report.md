# AutoPaper-P1.1 implementation record

- Frozen baseline: `AUTOPAPER_PROSPECTIVE_BASELINE_V1`
- Baseline methodology: `3ed64bac9138d36cc1c582c78803215e0142cd12bf8c9db3c50bfc05e3f43d79`
- Baseline config: `86ed4f1153186874330f62e0c8bb4ad80aef7972a20188952af3697aced1d9c2`
- Pre/post behavioral fingerprint: `b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640`
- Catastrophe shadow methodology: `ae006e7bc63e34a3ecf90540fea32f1c49787a97dabb33e03911072185dc47c9`
- Catastrophe config: `5c3604553c971ceda010d5b4dfbb002cf29174868314376ce29278de0f0e1c09`
- Telemetry methodology: `ce7bc5d0bb6120d164c28c039669a1cc6fdb709eef231e635fce7a8e39a45767`
- Telemetry config: `c80c3918c4e811bb14403accc2c334029cef30f7b2b3817e10d74cb28198b9de`

Circuit status is `NOT_AVAILABLE` unless an exact causal price-band source exists. Equal OHLC is recorded as `LOCKED_RANGE`, never as a confirmed circuit. Sector is frozen from the runtime NIFTY 500 classification carried by the qualified recommendation; missing/General classifications become `NOT_AVAILABLE`. Portfolio correlation uses 20 trailing completed-session close returns and requires at least 10 overlapping observations.

`SHADOW_CATASTROPHE` uses the same P0/C3/H10/capital/cost/queue contracts as baseline. Its only difference is an exceptional exit at the wider downside distance of 3.5 entry ATR or 12% from entry. It has separate cash, orders, positions, trades, journal, methodology, and counterfactual observations.

The four new persistence tables are additive and idempotently created. Execution observations are written after the account transaction; sector and risk telemetry are also best-effort isolated. Their failure cannot roll back baseline trading state. Streamlit reads persisted projections only.

Initial prospective P1.1 production counts are zero before the first eligible post-deployment EOD session. No historical AutoPaper holdout was opened.

# PB-P1C bounded contract reconstruction

**NO-GO — EXACT PB PARITY NOT ACHIEVABLE; PB-R2B/R3B PRODUCTION-NATIVE RETRAINING REQUIRED**

The research formulas are reproducible. Their required raw inputs are not available under the current live data contract. Frozen PB shadow activation remains disabled; production code, configuration, tables and trading behavior were not changed.

## Authoritative contract

Recovered all 36 fields from `predictability_boundary_research.py`, the persisted PB feature dataset and frozen inference manifest. PB-R2 selects qualified rows *after* the PB-R1 sample cross-section is constructed. The cross-section includes all qualified rows plus a SHA256-selected sample of nonqualified historically eligible NSE securities. It is neither the qualified-only set nor the complete fetched NIFTY500 set.

Research prices use a forward split/bonus adjustment chain; exchange share volume and traded value remain raw. Live screening uses Yahoo `auto_adjust=True` history. Matching formulas or field names cannot prove equivalence between these sources. EWM seed length also matters at the requested 1e-10 tolerance.

## Mapping and parity

| Gate | Result |
|---|---|
| Manifest completeness | 36/36 |
| Historical formula/missing-mask parity | 36/36 PASS |
| Sample | 5,603 rows, 44 complete sampled cross-sections, 2016–2026, four strategies |
| Maximum absolute error | 0 |
| Missing-mask mismatches | 0 |
| Production mapping | 28 NOT_RECONSTRUCTABLE under existing source contracts; 5 NOT_AVAILABLE; 3 RECONSTRUCTABLE_EXACT benchmark formulas |
| PB-R2 new-path inference parity | GATED: live feature contract fails |
| PB-R3 new-path parity | GATED: preceding gate fails |
| Prospective shadow activation | Disabled |

The three reconstructable benchmark formulas are conditional on supplying the original source/seed contract; they are not certified existing live matches. Historical formula PASS does not override any live-source blocker.

The sample was selected deterministically by date ordinal, without outcomes. Insufficient history and zero-volume/range cases were checked independently against the authoritative feature prefix. It is not a separately stratified corporate-action audit and cannot establish live corporate-action parity.

## Blocking inputs

1. Exchange-reported traded value (TOTTRDVAL/TtlTrfVal) is archived historically but absent from runtime OHLCV. Close × volume changes turnover and liquidity meanings.
2. Live scanning does not reconstruct the dated HA_D1 eligibility population and its qualified/nonqualified sampling rule.
3. Yahoo dividend/back-adjusted prices and provider volume are not the archived forward split/bonus-only source. The exact cumulative seed/history is not persisted live.
4. India VIX is fetched transiently elsewhere and rounded in Market Context, but the exact same-date NSE archive value is not retained in the PB live inputs. It cannot be substituted or assumed equal.

No approximate mapping, imputation to hide a source gap, new model fit, prospective historical backfill or AutoPaper holdout analysis was performed.

## Deliverables and future implementation

`pb_canonical_features.py` is a feature-only batch builder requiring explicit raw-source and population contracts. It has no database, provider, fitting, outcome or UI integration. `scripts/run_pb_p1c_contract_audit.py` is the reproducible bounded harness. The JSON artifacts alongside this report provide the 36-field formulas, mapping, parity, source provenance, performance and regression results.

`pb_r2b_production_native_spec.json` specifies 35 explicitly renamed native features and removes India VIX. It requires immutable raw NSE bars and dated identities, explicitly names close × volume as a proxy, fixes a qualified-only cross-section, defines all five 10D targets, chronological purging/embargo, bounded model/calibration selection, evaluation and new PB-R3B floors/buckets. Raw source ingestion and causal historical qualification reconstruction are implementation prerequisites in that next task. It does not claim these new contracts already exist in production. No old coefficients or thresholds may be reused.

## Performance and safety

The historical batch completed in 13.47 seconds over 1,659 securities, with zero database queries and zero provider calls. Live EOD latency is not certified because the exact input contract is unavailable. There is no navigation compute path.

332 counted tests/checks passed across focused PB-P1C, PB-P1/P1A, AutoPaper P1/P1.1/P1.2/P1.3, V1A/V1B, UI, performance and qualification suites; EOD automation also passed. Qualification initially needed the existing `PYTHONPATH=.` invocation and passed 7/7 with it. No executable PB-P1B suite exists; its manifest and disabled activation are checked by the focused suite. Python compilation and diff checks passed.

Baseline fingerprint remains `b93e8c2dd1a99fba712f89a38ba3a5689595891a47375e5c4f20a31d567d3640`.

Frozen PB-R2 methodology: `cd82dc5e6a354445506306a1a98c54de8f7287b248e89bf3f6044342d68b8e27`.

Frozen artifact SHA: `5d06576fb89ea3d9d6619d07dbf6b08fc5a07e88ad4456fb623b5d7cf53ea0fd`.

Frozen PB-R3 methodology: `7534d1bc782c12e03bf726dcb45a5cd104693c8263c6446f80785fe6638eaf9c`.

Frozen PB-R2/R3 must not receive production decision authority. The provided PB-R2B/R3B specification is the sole recommended next path.

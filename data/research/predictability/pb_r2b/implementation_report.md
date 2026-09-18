# PB-R2B / PB-R3B production-native retraining

## Contract

The frozen PB-P1C specification was implemented without reopening feature discovery. The retained contract has 35 inputs computed from immutable raw NSE EQ bhavcopy OHLCV, with raw close × raw volume as the explicitly named traded-value proxy. India VIX and market-adjusted direction remain removed. Historical replay and live-EOD entry points share `pb_native_features.security_state`; no parallel research formula exists.

Raw-price discontinuities of at least 40% quarantine the security for T plus 19 following observed sessions. Features use completed session T only. Outcomes use T close and the next 5/10/20 NSE sessions. Split/bonus action-crossing outcome windows are excluded. The positive close-to-close qualification gate passed for every retained row.

## Dataset and splits

- 61,901 qualified security-date observations
- 1,976 canonical securities
- 2,384 sessions
- 2016-10-25 through 2026-08-13
- Base: 2016-2019; calibration: 2020-2021; selection validation: 2022-2023
- Disclosed reused evaluation: 2024-2026, evaluated once after model/calibrator selection
- Labels end strictly before boundaries; maximum-horizon embargo is preserved
- AutoPaper holdout and AutoPaper outcomes were not opened

The bounded candidates were Ridge (`alpha=10`) and L2 logistic (`C=1`, liblinear), with raw versus signed-log features and none versus affine/Platt calibration. Preprocessing is train-only 1st/99th percentile winsorization, median imputation, standard scaling, and missing indicators. Ridge uses the deterministic SAG numerical solver because the local Accelerate BLAS emitted false divide-by-zero warnings for finite bounded matrix multiplication; the objective and alpha are unchanged. The final run completed with runtime warnings elevated to errors.

## Locked 10D PB-R2B results

| Output | N | Evidence | Primary metric | Baseline | Increment | Ordering |
|---|---:|---|---:|---:|---:|---:|
| Forward volatility | 18,481 | WELL_CALIBRATED | MAE 16.0403 | 18.6878 | +14.17% | Spearman 0.5556 |
| Signed MAE | 18,481 | CALIBRATED_LOW_STRENGTH | MAE 4.1911 | 4.3815 | +4.34% | Spearman 0.2709 |
| MFE | 18,481 | ORDINAL_ONLY | MAE 6.3052 | 5.9293 | -6.34% | Spearman 0.2999 |
| Adverse-first | 18,481 | CALIBRATED_LOW_STRENGTH | Brier 0.2390 | 0.2440 | +2.06% | AUC 0.5760 |
| +5-before-3 | 18,481 | CALIBRATED_LOW_STRENGTH | Brier 0.23284 | 0.23302 | +0.08% | AUC 0.5441 |

Forward volatility beat its baseline in all three annual folds. MAE beat baseline in 2024 and 2025 but missed in 2026. MFE failed point-calibration in all three annual folds despite positive ordinal ordering. The four major strategy slices show the same MFE point-error failure.

Relative to original PB-R2, live-reproducible PB-R2B is slightly weaker on volatility and MAE, materially weaker on numeric MFE, similar/slightly better on adverse-first discrimination, and weaker on +5-before-3 discrimination. These are different source/population contracts and are not asserted to be directly identical.

## PB-R3B diagnostic

Calibration-derived floor: 3.9597305836. Calibration tertile boundaries: 1.3987472683 and 1.7124061941.

| State | N | +5-before-3 | Median MFE | Median MAE | Realized reward/adversity |
|---|---:|---:|---:|---:|---:|
| UNFAVORABLE | 6,951 | 32.27% | 5.00% | -5.63% | 0.957 |
| MIXED | 6,067 | 36.41% | 5.91% | -5.93% | 1.001 |
| FAVORABLE | 5,463 | 43.57% | 7.01% | -5.01% | 1.414 |

The diagnostic ordering is favorable across four strategies, but it relies on a 10D MFE point estimate that failed the required numeric calibration gate. It therefore cannot be frozen as PB-R3B.

## Live and activation status

The canonical builder has shared historical/live semantics and fail-closed inputs, but no production ingestion, runtime artifact, UI, EOD hook, or shadow manifest was activated because the scientific gate failed. Existing PB-R2/R3 identities and artifacts remain unchanged. Decision authority remains false.

## Verdict

**NO-GO — PRODUCTION-NATIVE PB DOES NOT RETAIN SUFFICIENT PREDICTIVE VALUE**

Forward volatility and downside-path outputs remain scientifically useful research findings, but the required numeric MFE/MAE pair does not support a production-native PB-R3B asymmetry methodology. PB prospective shadow collection remains inactive.

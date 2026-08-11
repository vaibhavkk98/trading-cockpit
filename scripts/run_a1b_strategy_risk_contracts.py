"""Freeze the A2 risk-reference contract from A1A causal primitives only."""
from pathlib import Path
import pickle

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/research/a1a_causal_stop_primitives.csv"
OHLCV = ROOT / "data/ml/step_6/cached_ohlcv_indicators.pkl"
OUTPUT = ROOT / "data/research/a1b_strategy_risk_contracts.csv"
REPORT = ROOT / "data/research/a1b_strategy_risk_contract_report.md"

CONTRACT_VERSION = "A1B_RISK_REFERENCE_V1"
STRATEGIES = {
    "Donchian Channel Breakout": {
        "reference_type": "PRIOR_20_COMPLETED_SESSION_LOW",
        "reference_class": "TECHNICAL_INVALIDATION",
        "primitive": "prior_20_session_low",
        "candidate": True,
        "readiness": "READY_FOR_A2",
        "matrix": "B. prior-20-session-low primary reference",
    },
    "EMA Pullback / Bounce": {
        "reference_type": "PRIOR_5_COMPLETED_SESSION_LOW_PROXY",
        "reference_class": "TECHNICAL_INVALIDATION_PROXY",
        "primitive": "prior_5_session_low",
        "candidate": True,
        "readiness": "READY_FOR_A2_WITH_PROXY",
        "matrix": "B. prior-5-session-low proxy",
    },
    "RS Momentum Breakout": {
        "reference_type": "PRIOR_20_COMPLETED_SESSION_LOW",
        "reference_class": "TECHNICAL_INVALIDATION",
        "primitive": "prior_20_session_low",
        "candidate": True,
        "readiness": "READY_FOR_A2",
        "matrix": "B. prior-20-session-low primary reference",
    },
    "VCP Volatility Contraction Breakout": {
        "reference_type": "CAUSAL_PRIOR_RANGE_LOW_PROXY",
        "reference_class": "TECHNICAL_INVALIDATION_PROXY",
        "primitive": "recent_20_session_low",
        "candidate": True,
        "readiness": "READY_FOR_A2_WITH_PROXY",
        "matrix": "B. causal prior-range-low proxy",
    },
    "True Connors RSI Mean Reversion": {
        "reference_type": "SETUP_BAR_LOW",
        "reference_class": "RISK_REFERENCE",
        "primitive": "setup_bar_low",
        "candidate": False,
        "readiness": "READY_FOR_A2_AS_RISK_REFERENCE",
        "matrix": "B. setup-bar-low risk reference",
    },
    "True NR7 Volatility Expansion Breakout": {
        "reference_type": "NR7_SETUP_BAR_LOW",
        "reference_class": "TECHNICAL_INVALIDATION",
        "primitive": "nr7_setup_low",
        "candidate": True,
        "readiness": "READY_FOR_A2",
        "matrix": "B. NR7 setup-bar low",
    },
}


def numeric(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def percentile(series, q):
    return series.quantile(q) if len(series) else np.nan


def format_number(value, digits=2):
    return "NOT_AVAILABLE" if pd.isna(value) else f"{value:.{digits}f}"


def build_contract(a1a):
    with OHLCV.open("rb") as handle:
        cached_ohlcv = pickle.load(handle)
    rows = []
    for source in a1a.itertuples(index=False):
        spec = STRATEGIES[source.strategy]
        entry, reference, atr = (numeric(source.entry_price_or_existing_entry_reference),
                                 numeric(source.primitive_value), numeric(source.atr20))
        primary_available, reason = False, "NOT_AVAILABLE"
        if source.primitive_name != spec["primitive"]:
            reason = "A1A_PRIMITIVE_MISMATCH"
        elif pd.isna(reference) or reference <= 0:
            reason = "NOT_AVAILABLE"
        elif pd.isna(entry) or entry <= 0:
            reason = "ENTRY_REFERENCE_NOT_AVAILABLE"
        elif reference >= entry:
            reason = "INVALID_REFERENCE_AT_OR_ABOVE_ENTRY"
        else:
            primary_available, reason = True, "AVAILABLE"

        distance = entry - reference if primary_available else np.nan
        atr_available = bool(pd.notna(atr) and atr > 0 and pd.notna(entry) and entry > 0)
        atr_value = entry - 2.0 * atr if atr_available else np.nan
        # A daily OHLC bar cannot order a signal/entry and a stop touch. This
        # is only a readiness flag; it makes no stop attribution.
        bar = cached_ohlcv.get(source.symbol)
        same_bar = False
        if primary_available and bar is not None and str(source.signal_date)[:10] in bar.index:
            entry_bar = bar.loc[str(source.signal_date)[:10]]
            same_bar = bool(entry_bar.High >= entry and entry_bar.Low <= reference)
        rows.append({
            "signal_id": source.signal_id, "security_id": source.security_id,
            "symbol": source.symbol, "strategy": source.strategy,
            "strategy_version": "P1_QUALIFIED_SIGNAL_V1",
            "signal_date": source.signal_date, "entry_reference_date": source.signal_date,
            "entry_reference_price": entry,
            "primary_reference_type": spec["reference_type"],
            "primary_reference_class": spec["reference_class"],
            "primary_reference_value": reference if pd.notna(reference) else "NOT_AVAILABLE",
            "primary_reference_available": primary_available,
            "primary_reference_reason": reason,
            "primary_stop_distance": distance if primary_available else "NOT_AVAILABLE",
            "primary_stop_distance_pct": (distance / entry * 100) if primary_available else "NOT_AVAILABLE",
            "primary_stop_distance_atr": (distance / atr) if primary_available and atr_available else "NOT_AVAILABLE",
            "primary_risk_per_share": distance if primary_available else "NOT_AVAILABLE",
            "atr20": atr if pd.notna(atr) else "NOT_AVAILABLE", "atr20_available": atr_available,
            "atr_benchmark_value": atr_value if atr_available else "NOT_AVAILABLE",
            "atr_benchmark_available": atr_available,
            "atr_benchmark_distance": (2.0 * atr) if atr_available else "NOT_AVAILABLE",
            "atr_benchmark_distance_pct": (2.0 * atr / entry * 100) if atr_available else "NOT_AVAILABLE",
            "atr_benchmark_risk_per_share": (2.0 * atr) if atr_available else "NOT_AVAILABLE",
            "same_bar_sequence_possible": same_bar,
            "same_bar_sequence_ambiguous": same_bar,
            "gap_through_possible": primary_available,
            "executable_stop_candidate": spec["candidate"],
            "a2_readiness": spec["readiness"], "contract_version": CONTRACT_VERSION,
        })
    contract = pd.DataFrame(rows)
    duplicate_number = contract.groupby("signal_id").cumcount()
    contract.loc[duplicate_number.gt(0), "signal_id"] += "__" + duplicate_number[duplicate_number.gt(0)].astype(str)
    return contract


def distribution_table(contract):
    records = []
    for strategy, group in contract.groupby("strategy", sort=True):
        primary = pd.to_numeric(group.loc[group.primary_reference_available, "primary_stop_distance_pct"], errors="coerce").dropna()
        atr = pd.to_numeric(group.loc[group.atr_benchmark_available, "atr_benchmark_distance_pct"], errors="coerce").dropna()
        records.append({"strategy": strategy, "signals": len(group),
            "primary_coverage_pct": 100 * len(primary) / len(group),
            "primary_median_pct": percentile(primary, .5), "primary_p10_pct": percentile(primary, .1),
            "primary_p25_pct": percentile(primary, .25), "primary_p75_pct": percentile(primary, .75), "primary_p90_pct": percentile(primary, .9),
            "primary_lt_1pct_count": (primary < 1).sum(), "primary_gt_10pct_count": (primary > 10).sum(), "primary_gt_20pct_count": (primary > 20).sum(),
            "atr_coverage_pct": 100 * len(atr) / len(group), "atr_median_pct": percentile(atr, .5), "atr_p10_pct": percentile(atr, .1), "atr_p90_pct": percentile(atr, .9),
            "same_bar_ambiguous_count": group.same_bar_sequence_possible.sum(),
            "same_bar_ambiguous_pct": 100 * group.same_bar_sequence_possible.mean(),
        })
    return pd.DataFrame(records)


def markdown_table(frame, percent_columns=()):
    display = frame.copy()
    for column in percent_columns:
        display[column] = display[column].map(lambda value: format_number(value, 2))
    return display.to_markdown(index=False)


def write_report(contract, summary):
    matrix = pd.DataFrame([{"strategy": name, "A": "no-stop 10-session baseline", "B": spec["matrix"], "C": "2×ATR20 benchmark"}
                           for name, spec in STRATEGIES.items()])
    definitions = pd.DataFrame([{"strategy": name, "primary_reference": spec["reference_type"], "classification": spec["reference_class"],
                                 "executable_stop_candidate": spec["candidate"], "A2_readiness": spec["readiness"]}
                                for name, spec in STRATEGIES.items()])
    pct_cols = [c for c in summary if c.endswith("_pct")]
    report = ["# A1B Strategy Risk-Reference Contracts", "",
              "Research-only contract freeze from A1A's 1,009 causal primitives. No stop exits, performance tests, qualification, sizing, V2, or live behavior were changed.",
              "", "## A. Frozen risk-reference matrix", "", markdown_table(matrix),
              "", "## B–C. Exact definitions and classifications", "", markdown_table(definitions),
              "", "The 2×ATR20 comparator is exactly `entry_reference_price - 2.0 × ATR20`; it is a RISK_CONTROL_BENCHMARK and never a structural fallback.",
              "", "## D–G. Coverage, stop-distance and ambiguity diagnostics", "", markdown_table(summary, pct_cols),
              "", "Distance percentiles are descriptive for valid primary references only. Counts are retained for `<1%`, `>10%`, and `>20%`; they do not qualify or reject signals.",
              "", "## H. Executable-stop candidacy", "", "Donchian, EMA proxy, RS, VCP proxy, and NR7 may be tested as static initial stops. Connors setup-low remains diagnostic/unproven (`False`) because further weakness can be compatible with its mean-reversion thesis.",
              "", "## I. Frozen A2 execution semantics", "", "Static initial stop only; no trailing; qualification, ordering, ₹1L nominal sizing, maximum positions, costs/slippage and repaired V2 lifecycle remain unchanged. Exit is STOP or the existing repaired ten-trading-session exit, whichever occurs first. Later sessions: `Open <= Stop` → Open / `GAP_THROUGH`; else `Low <= Stop` → Stop / `INTRADAY_TOUCH`; otherwise no event.",
              "", "## Entry-bar ambiguity and gap readiness", "", "Where the entry/activation bar can contain both entry and primary reference, `same_bar_sequence_possible=True`. A2 must mark it `SAME_BAR_SEQUENCE_AMBIGUOUS`, exclude same-bar stop attribution in the primary study, and continue from the next eligible session. A1A cached daily OHLC supports the later-session Open/Low rules; no exits were simulated here.",
              "", "## K. Limitations", "", "EMA and VCP are explicitly causal proxies, not recovered swing/final-contraction metadata. Connors is a risk reference, not a pre-approved technical stop. Invalid or unavailable structural references remain unavailable and are never replaced by ATR."]
    REPORT.write_text("\n".join(report) + "\n")


def run():
    a1a = pd.read_csv(INPUT)
    if len(a1a) != 1009 or set(a1a.strategy) != set(STRATEGIES):
        raise ValueError("A1A input is not the canonical 1,009-row six-strategy population")
    contract = build_contract(a1a)
    contract.to_csv(OUTPUT, index=False)
    summary = distribution_table(contract)
    write_report(contract, summary)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    run()

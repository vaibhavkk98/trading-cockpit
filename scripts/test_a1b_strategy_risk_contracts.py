"""Focused invariants for the frozen A1B research contract."""
from pathlib import Path

import pandas as pd

from run_a1b_strategy_risk_contracts import CONTRACT_VERSION, STRATEGIES, build_contract


ROOT = Path(__file__).resolve().parents[1]
A1A = ROOT / "data/research/a1a_causal_stop_primitives.csv"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    source = pd.read_csv(A1A)
    contract = build_contract(source)
    check(len(contract) == 1009 == len(source), "signal population changed")
    check(set(contract.strategy) == set(STRATEGIES), "strategy population changed")
    check(contract.signal_id.is_unique, "one contract per signal required")
    check(contract.same_bar_sequence_possible.equals(contract.same_bar_sequence_ambiguous), "same-bar contract flags disagree")
    check(contract.contract_version.eq(CONTRACT_VERSION).all(), "contract version mismatch")
    for strategy, spec in STRATEGIES.items():
        rows = contract[contract.strategy.eq(strategy)]
        check(rows.primary_reference_type.nunique() == 1, f"one primary reference: {strategy}")
        check(rows.primary_reference_type.iloc[0] == spec["reference_type"], f"wrong primary: {strategy}")
        check(rows.primary_reference_class.nunique() == 1, f"wrong class: {strategy}")
        check(rows.atr_benchmark_available.notna().all(), f"one ATR benchmark: {strategy}")
    check(contract.primary_reference_type.isin([s["reference_type"] for s in STRATEGIES.values()]).all(), "additional stop variants")
    check(contract.atr_benchmark_distance.dropna().empty or (pd.to_numeric(contract.atr_benchmark_distance, errors="coerce") == 2 * pd.to_numeric(contract.atr20, errors="coerce")).dropna().all(), "ATR is not exactly 2x")
    check(contract.loc[~contract.primary_reference_available, "primary_reference_reason"].ne("AVAILABLE").all(), "unavailable structure silently available")
    invalid = contract.primary_reference_reason.eq("INVALID_REFERENCE_AT_OR_ABOVE_ENTRY")
    check((~invalid | ~contract.primary_reference_available).all(), "at/above-entry reference not invalidated")
    check(contract.loc[contract.strategy.eq("EMA Pullback / Bounce"), "primary_reference_class"].eq("TECHNICAL_INVALIDATION_PROXY").all(), "EMA proxy lost")
    check(contract.loc[contract.strategy.eq("VCP Volatility Contraction Breakout"), "primary_reference_type"].eq("CAUSAL_PRIOR_RANGE_LOW_PROXY").all(), "VCP proxy lost")
    check(contract.loc[contract.strategy.eq("True Connors RSI Mean Reversion"), "primary_reference_class"].eq("RISK_REFERENCE").all(), "Connors class changed")
    check(contract.loc[contract.strategy.eq("True Connors RSI Mean Reversion"), "executable_stop_candidate"].eq(False).all(), "Connors candidacy changed")
    check(contract.loc[contract.strategy.eq("True NR7 Volatility Expansion Breakout"), "primary_reference_type"].eq("NR7_SETUP_BAR_LOW").all(), "NR7 setup-bar semantics changed")
    for strategy, primitive in [("Donchian Channel Breakout", "prior_20_session_low"), ("RS Momentum Breakout", "prior_20_session_low"), ("EMA Pullback / Bounce", "prior_5_session_low")]:
        check(source.loc[source.strategy.eq(strategy), "primitive_name"].eq(primitive).all(), f"non-causal primitive: {strategy}")
    check(set(contract.columns).isdisjoint({"forward_return_3d", "forward_return_5d", "forward_return_10d", "mfe_10d", "mae_10d"}), "future data included")
    print("A1B focused contract tests: PASS")


if __name__ == "__main__":
    run()

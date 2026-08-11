"""
universe_engine.py — Historical Nifty 500 Research Universe Engine
====================================================================

Provides point-in-time constituent membership queries based on verified parent
membership events, corporate action identity mappings, and current anchor snapshot.

Public APIs:
    - get_universe_as_of(date_str: str, mode: str = "research") -> list[str]
    - is_constituent(symbol: str, date_str: str, mode: str = "research") -> bool
    - get_universe_metadata(date_str: str) -> dict
    - get_security_universe_as_of(date_str: str, mode: str = "research") -> list[dict]

Exceptions:
    - HistoricalUniverseNotVerifiedError
"""

import os
import glob
import pandas as pd
from typing import Dict, Any, List, Set, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "universe")

CONST_CSV = os.path.join(DATA_DIR, "nifty500_constituents.csv")
PARENT_EVENTS_CSV = os.path.join(DATA_DIR, "nifty500_parent_events.csv")
SEC_MASTER_CSV = os.path.join(DATA_DIR, "nifty500_security_master.csv")
MEM_INTERVALS_CSV = os.path.join(DATA_DIR, "nifty500_membership_intervals.csv")
HIST_STATUS_CSV = os.path.join(DATA_DIR, "nifty500_historical_universe_status.csv")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")

# Sub-index non-broad-market contaminants to exclude dynamically during state replay
SUBINDEX_CONTAMINANTS = {"ANGELONE", "ASTRAZEN", "GLAXO"}


class HistoricalUniverseNotVerifiedError(ValueError):
    """Raised when a date is requested in strict mode that lacks verified official snapshot evidence."""
    pass


# Global Lazy Data Cache
_CACHE = {
    "anchor_syms": None,
    "parent_events": None,
    "sec_master": None,
    "sym_change_map": None,
    "snapshots": {}
}


def _load_data():
    """Lazily loads and caches reference datasets."""
    if _CACHE["anchor_syms"] is None:
        if os.path.exists(CONST_CSV):
            df_c = pd.read_csv(CONST_CSV).fillna("")
            _CACHE["anchor_syms"] = set(df_c["symbol"].str.upper().unique())
        else:
            _CACHE["anchor_syms"] = set()

    if _CACHE["parent_events"] is None:
        if os.path.exists(PARENT_EVENTS_CSV):
            df_p = pd.read_csv(PARENT_EVENTS_CSV).fillna("")
            df_p["eff_dt_parsed"] = pd.to_datetime(df_p["effective_date"], errors="coerce")
            _CACHE["parent_events"] = df_p.sort_values(by=["eff_dt_parsed", "event_id"], ascending=[False, False])
        else:
            _CACHE["parent_events"] = pd.DataFrame()

    if _CACHE["sec_master"] is None:
        if os.path.exists(SEC_MASTER_CSV):
            df_sm = pd.read_csv(SEC_MASTER_CSV).fillna("")
            _CACHE["sec_master"] = df_sm
            _CACHE["sym_change_map"] = dict(zip(df_sm["historical_symbol"], df_sm["canonical_symbol"]))
        else:
            _CACHE["sec_master"] = pd.DataFrame()
            _CACHE["sym_change_map"] = {}

    # Check for future snapshot files in data/universe/snapshots/
    if os.path.exists(SNAPSHOTS_DIR):
        snap_files = glob.glob(os.path.join(SNAPSHOTS_DIR, "nifty500_*.csv"))
        for s_path in snap_files:
            fname = os.path.basename(s_path)
            dt_part = fname.replace("nifty500_", "").replace(".csv", "")
            if len(dt_part) == 8 and dt_part.isdigit():
                formatted_dt = f"{dt_part[:4]}-{dt_part[4:6]}-{dt_part[6:]}"
                if formatted_dt not in _CACHE["snapshots"]:
                    df_s = pd.read_csv(s_path).fillna("")
                    sym_col = "symbol" if "symbol" in df_s.columns else "Symbol"
                    if sym_col in df_s.columns:
                        _CACHE["snapshots"][formatted_dt] = set(df_s[sym_col].str.upper().unique())


def _get_reconstructed_set(target_dt_str: str) -> Set[str]:
    """Computes reconstructed constituent set for a given target date."""
    _load_data()

    # Priority 1: Check if official snapshot file exists for target date
    if target_dt_str in _CACHE["snapshots"]:
        return set(_CACHE["snapshots"][target_dt_str])

    # Priority 2: Active official anchor snapshot
    if target_dt_str >= "2026-08-10":
        return set(_CACHE["anchor_syms"])

    target_dt = pd.to_datetime(target_dt_str)
    df_rev = _CACHE["parent_events"]

    # Start with active anchor snapshot
    state = set(_CACHE["anchor_syms"])

    if not df_rev.empty:
        events_after = df_rev[df_rev["eff_dt_parsed"] > target_dt]
        for idx, r in events_after.iterrows():
            sym = str(r["symbol"]).upper()
            if sym in SUBINDEX_CONTAMINANTS:
                continue

            ev_type = r["event_type"]
            if ev_type == "ADDITION":
                if sym in state:
                    state.remove(sym)
            elif ev_type == "DELETION":
                if sym not in state:
                    state.add(sym)

    return state


def get_universe_as_of(date_str: str, mode: str = "research") -> List[str]:
    """
    Returns list of constituent symbols as of the specified date.

    Args:
        date_str: Target date in 'YYYY-MM-DD' format.
        mode: 'research' (default) or 'strict'.
              In 'strict' mode, raises HistoricalUniverseNotVerifiedError for
              dates prior to 2024-03-31 lacking verified official snapshot files.

    Returns:
        List of constituent ticker symbols.
    """
    _load_data()
    mode = mode.lower()

    if mode not in ["strict", "research"]:
        raise ValueError(f"Invalid mode '{mode}'. Expected 'strict' or 'research'.")

    # In strict mode, enforce verification boundary (2024-03-31)
    if mode == "strict" and date_str < "2024-03-31" and date_str not in _CACHE["snapshots"]:
        raise HistoricalUniverseNotVerifiedError(
            f"Date '{date_str}' is prior to 2024-03-31 where historical constituent addition evidence "
            f"is unverified due to 2018-2021 press release addition omissions. "
            f"Use mode='research' to inspect reconstructed state, or acquire official historical snapshot CSVs."
        )

    res_set = _get_reconstructed_set(date_str)
    return sorted(list(res_set))


def is_constituent(symbol: str, date_str: str, mode: str = "research") -> bool:
    """
    Checks whether a symbol was a constituent as of the specified date.
    """
    _load_data()
    sym_upper = symbol.strip().upper()
    canon_sym = _CACHE["sym_change_map"].get(sym_upper, sym_upper)

    univ = get_universe_as_of(date_str, mode=mode)
    univ_upper = set(s.upper() for s in univ)

    return (sym_upper in univ_upper) or (canon_sym in univ_upper)


def get_security_universe_as_of(date_str: str, mode: str = "research") -> List[Dict[str, Any]]:
    """
    Authoritative internal security-level universe API.
    """
    _load_data()
    symbols = get_universe_as_of(date_str, mode=mode)
    
    df_sm = _CACHE["sec_master"]
    sec_map = {}
    if not df_sm.empty:
        for idx, r in df_sm.iterrows():
            sec_map[r["historical_symbol"]] = r.to_dict()

    result = []
    for s in symbols:
        info = sec_map.get(s, {
            "security_id": f"SEC_{s}",
            "isin": "INSUFFICIENT_ISIN_COVERAGE",
            "historical_symbol": s,
            "canonical_symbol": _CACHE["sym_change_map"].get(s, s),
            "company_name": s
        })
        result.append(info)

    return result


def get_universe_metadata(date_str: str) -> Dict[str, Any]:
    """
    Exposes evidence status, verification status, and quality metrics for a requested date.
    """
    _load_data()

    has_official_snapshot = date_str in _CACHE["snapshots"] or date_str >= "2026-08-10"
    
    if has_official_snapshot:
        ev_status = "OFFICIAL_CURRENT_SNAPSHOT"
        surv_risk = "LOW"
        coverage = "COMPLETE"
        recon_method = "OFFICIAL_SNAPSHOT_FILE"
    elif date_str >= "2024-03-31":
        ev_status = "EVENT_RECONSTRUCTED"
        surv_risk = "MEDIUM"
        coverage = "COMPLETE_FOR_AVAILABLE_PERIOD"
        recon_method = "REVERSE_EVENT_REPLAY"
    else:
        ev_status = "UNVERIFIED_RECONSTRUCTION"
        surv_risk = "HIGH"
        coverage = "PARTIAL_OMISSION"
        recon_method = "REVERSE_EVENT_REPLAY_WITH_GAP"

    reconstructed_syms = _get_reconstructed_set(date_str)
    tot_events = len(_CACHE["parent_events"]) if _CACHE["parent_events"] is not None else 0

    return {
        "date": date_str,
        "anchor_date": "2026-08-10",
        "universe_count": len(reconstructed_syms),
        "evidence_status": ev_status,
        "official_snapshot_available": has_official_snapshot,
        "reconstruction_method": recon_method,
        "event_coverage": coverage,
        "identity_normalization": "APPLIED",
        "corporate_action_mapping": "APPLIED_WHERE_SUPPORTED",
        "survivorship_bias_risk": surv_risk,
        "source_event_count": tot_events,
        "reconstructed_symbol_count": len(reconstructed_syms)
    }


def get_current_universe(mode: str = "research") -> List[str]:
    """Convenience helper returning the current active universe (August 2026 anchor)."""
    return get_universe_as_of("2026-08-10", mode=mode)


if __name__ == "__main__":
    print("universe_engine.py research module initialized.")

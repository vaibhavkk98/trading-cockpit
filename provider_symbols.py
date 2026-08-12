"""Provider-specific symbol mapping; canonical database symbols stay unchanged."""


def yahoo_nse_symbol(canonical_symbol: str) -> str:
    """Map a canonical NSE equity symbol to Yahoo without double suffixing."""
    symbol = str(canonical_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("A canonical symbol is required.")
    if symbol.startswith("^") or symbol.endswith(".NS"):
        return symbol
    return f"{symbol}.NS"

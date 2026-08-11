# Trading Cockpit V1 operating guide

## Launch

```bash
PYTHONPATH=. ./venv/bin/streamlit run app.py --server.port=8503
```

Stop with `Ctrl+C`. For a clean restart after a source change, stop the prior process, then run the same command again. Streamlit session state is per browser session; a fresh browser session starts with **No scan yet**. Paper trades are persisted in `paper_trading.db`.

## Daily workflow

1. Open **Today** and check analysis freshness plus Market Risk coverage.
2. Select **Run today's analysis** once. The scan is user-triggered and does not rerun on normal tab changes.
3. Review every qualified setup in **Signals**. Priority is causal Volume Ratio 20 descending.
4. Allocation is binding existing portfolio logic; **Not allocated** does not mean unqualified.
5. Use **Trade Details** for entry, reference risk, executable stop, portfolio context, and descriptive historical economics.
6. To record a paper trade, select an allocated opportunity, review the confirmation summary, check the manual-confirmation box, and choose **Record paper trade**.

## Risk and intelligence labels

- **Reference Risk** is structural context, not necessarily an executable stop.
- **Executable Stop** is available only for validated Donchian, RS Momentum, and VCP contracts with a valid live ATR input. EMA, Connors, and NR7 show no executable stop.
- **Reference Heat** and **Executable Stop Heat** are informational only. Coverage shows how many open positions have canonical entry-time fields; legacy positions remain unavailable and are not backfilled.
- **Trade Economics** is descriptive historical strategy context, not a prediction, target, probability, or trade score.
- **Market Risk** is informational broad-market context. A missing or six-hour-stale snapshot is unavailable; the rest of the cockpit continues.
- **Not available** means the required frozen input was not available. It is never substituted with current data or zero risk.

## V1 boundaries

- Paper-trading decision support only; no broker order placement or automatic execution.
- ₹1,00,000 nominal ticket; maximum 10 positions; risk-based sizing and heat caps are inactive.
- Target is not available; there is no R:R or composite score.
- Live screening does not fabricate Connors/NR7 signals. Sector, correlation, entry timing, and Event Context coverage can be incomplete.
- Individual data-provider failures are tracked as missing data, not as a negative trading signal.

# Cloud runtime artifact manifest

## REQUIRED_AT_RUNTIME

- Python application modules, including `app.py`, `database.py`, adapters, and UI components.
- `requirements.txt`, `runtime.txt`, and the Python 3.12 deployment runtime.
- `data/mvp/performance_report.json` and `data/mvp/equity_curve.csv` for the clearly labelled historical-research panel.
- `data/research/d2_trade_economics_display_payload.json` for descriptive Trade Economics context.
- `data/research/c2_live_market_risk_payload.json` and related C1/C2 configuration/payload files consumed by `market_risk_live.py`.
- `data/universe/` files used by the universe provider.

## OPTIONAL

- Persisted Market Risk snapshot refresh artifacts. If absent or stale, Market Risk renders as unavailable and the cockpit continues.
- `data/ml/step_6/cached_ohlcv_indicators.pkl`, when available, to improve cached market-data coverage.

## RESEARCH_ONLY

- Historical Phase A–E reports, diagnostic CSVs, backtest exports, and exploratory ML/sentiment artifacts not imported by runtime modules.
- Local SQLite files and operational logs. These are intentionally excluded from cloud source control.

Paper trades and portfolio snapshots are never runtime files in cloud: when `DATABASE_URL` is set, they live in the configured Postgres database.

# Phase 1A EOD automation report

## Pipeline architecture

`scripts/run_eod_daily_pipeline.py` is a Streamlit-free scheduled entrypoint over `eod_pipeline.execute_eod_pipeline`. It reuses the production universe, market data, screen, allocator, live-decision adapter, execution adapter, and persistence layer. Manual analysis invokes the same canonical pipeline with a `MANUAL_REFRESH` source.

## Persistence and idempotency

Additive Postgres/SQLite-compatible tables provide `analysis_runs`, `daily_opportunities`, and `position_marks`. One canonical run is retained per analysis date; retrying updates that record and replaces only its own qualified payload. Opportunity and mark uniqueness prevent duplicate rows. Portfolio snapshots remain state-fingerprint deduplicated.

## Completed-bar safety

The runner resolves the Indian market date and requires the fetched index dataset to contain that exact completed daily bar. If it does not, it returns `NO_COMPLETED_MARKET_BAR`, writes no zero-signal run, and leaves the latest valid report intact.

## Latest-run startup and portfolio marks

App startup loads the latest valid persisted run into session state without a scan. Today, Signals, and Trade Details use its persisted decision payload. Normal portfolio reads use latest persisted marks, calculate P&L only for valid marks, display price freshness, and do not call a provider. Explicit `Refresh prices` remains the only UI price-fetch path and persists returned marks.

## Snapshots and workflow

Valid automated runs refresh open-position marks before an `AUTOMATED_EOD` portfolio snapshot, including price coverage. `.github/workflows/eod-trading-pipeline.yml` schedules weekdays at 16:07 Asia/Kolkata (10:37 UTC), supports manual dispatch, uses Python 3.12, concurrency protection, and `secrets.DATABASE_URL` without printing it.

## Verification

Focused Phase 1A tests passed for headless operation, completed-bar gating, persistence, idempotency, persisted marks/P&L, and snapshot deduplication. Local Streamlit acceptance against isolated persisted state showed the latest run at startup, Signals populated without scanning, and persisted price-mark freshness in Portfolio. V1.1, F1, F1.5, F2, G1, and selected frozen semantic suites passed.

## Remaining manual setup and limitations

Create a GitHub repository secret named `DATABASE_URL`, then enable/approve the scheduled GitHub Actions workflow in the repository Actions settings. The bar-presence gate intentionally defers publication on NSE holidays, delayed data, or provider outages; it does not attempt to infer an exchange calendar.

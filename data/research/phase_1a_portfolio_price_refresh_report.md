# Phase 1A portfolio price refresh report

## Production root cause

The persisted Portfolio loader was correctly provider-free, but no mark could appear until a provider refresh succeeded. The production refresh path was not narrow: it ran the legacy lifecycle sync, made a sequential Yahoo request per open symbol, saved a snapshot before the mark fetch, repeated the requests for marks, and forced a global Streamlit rerun. This doubled provider exposure and left the pre-rendered Portfolio table stale during the click cycle. Entry price was present in the paper ledger but omitted from the table.

## Final refresh architecture

Manual and automated triggers now share one lower-level mark engine. It loads OPEN trades only, maps canonical symbols through `yahoo_nse_symbol()`, performs one batch Yahoo download, falls back individually only for missing batch symbols, persists successful marks, retains prior marks on partial failure, and reloads the persisted position view. The Portfolio workspace is fragment-scoped; refresh feedback and rendering remain local.

Engineering metrics record open positions, unique symbols, provider calls, successful/failed marks, and elapsed provider time. For the deterministic FINCABLES/GRASIM acceptance case, two open positions required one batch provider call and no fallback.

## Portfolio display and semantics

The primary table shows Symbol, short Strategy, Entry Price, Entry Value, Current Price, P&L (rupees and percent), Price As Of, and Executable Stop. Entry Price and Entry Value come directly from the paper trade. Unrealized P&L remains `(mark_price - entry_price) * quantity`; missing inputs stay unavailable.

## Persistence and Postgres compatibility

Successful marks retain trade ID, canonical symbol, mark price/date/timestamp, provider, status, and trigger/source identity in the existing `position_marks` model. SQLAlchemy PostgreSQL DDL generation and integer trade-ID lookup compatibility were validated without exposing credentials. Persisted reload requires no provider access.

## Verification and deployment

Deterministic Portfolio refresh, Phase 1A, V1.1, F1, F1.5, F2, G1, frozen strategy, and focused risk/economics/payload tests passed. A cloud-like Streamlit startup against persisted FINCABLES/GRASIM state displayed entry/current prices, P&L, and mark timestamps immediately. Direct production-provider availability remains external to the application; partial failures preserve earlier valid marks.

Deployment status: `PUSHED_FOR_AUTO_DEPLOY` after the verified release reaches `origin/main`; direct live Streamlit verification is not available from this workspace.

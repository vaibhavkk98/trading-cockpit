# Trading Cockpit V1 cloud deployment

This guide deploys `TRADING_COCKPIT_V1` as paper-trading decision support only. It adds no broker credentials or automatic execution.

## 1. Prepare a private GitHub repository

From the project directory, review `.gitignore`, then run:

```bash
git init
git add .
git commit -m "Prepare Trading Cockpit V1 cloud deployment"
git branch -M main
git remote add origin <YOUR_PRIVATE_GITHUB_REPOSITORY_URL>
git push -u origin main
```

Do not commit `.streamlit/secrets.toml`, `.env`, or a local database.

## 2. Create provider-neutral managed Postgres

Create a managed Postgres instance with any suitable provider (for example Neon, Supabase, or Render) and copy its standard connection URL. Require TLS if the provider requires it, commonly with `?sslmode=require`. Application code only uses the URL; it has no provider-specific financial logic.

## 3. Configure Streamlit Community Cloud

Connect the private repository, select branch `main`, and set `app.py` as the entrypoint. The checked-in `runtime.txt` requests Python 3.12, matching the project virtual environment; install dependencies from `requirements.txt`.

In Streamlit Cloud **Secrets**, add exactly one secret:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"
```

`DATABASE_URL` has precedence over the optional local `[database] url` secret form. The same placeholder is in `.streamlit/secrets.toml.example`; never add real credentials to it in Git.

## 4. Deploy and verify

1. Deploy the app and open **Settings**.
2. Confirm `Deployment: CLOUD`, `Database: POSTGRES · AVAILABLE`.
3. Run **Run today's analysis** manually; no scan runs on startup.
4. Record one confirmed test paper trade.
5. Restart the app and verify the open trade remains.
6. Use **Sync live market prices** (or another meaningful action) and verify a persisted paper-portfolio snapshot appears in Performance.
7. Use **Portfolio → Manually close a paper trade**, enter and confirm a manual exit price, then confirm its realized P&L and return persist after restart.

If Settings shows `POSTGRES · NOT_AVAILABLE`, writes are disabled. Correct the secret/network configuration; the app will not fall back to SQLite in that state.

## Market Risk and future snapshots

Market Risk reads the committed/persisted snapshot and does not fetch on every render. If the snapshot is absent, it displays unavailable safely. Refresh it outside the app using the existing bounded source-refresh workflow before deployment as needed.

Portfolio snapshots are saved after completed analysis, paper-trade recording, and portfolio refresh. They are state-deduplicated and can later be called by an after-market scheduler; no scheduler is deployed in V1.

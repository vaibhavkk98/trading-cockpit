# Cloud go-live checklist

- [ ] Private GitHub repository created and `.gitignore` reviewed.
- [ ] `requirements.txt` builds on Streamlit Community Cloud.
- [ ] No secrets, `DATABASE_URL`, local databases, or logs are committed.
- [ ] Managed Postgres created with TLS requirements understood.
- [ ] `DATABASE_URL` configured in Streamlit Cloud Secrets.
- [ ] Settings shows `CLOUD`, `POSTGRES`, and `AVAILABLE`.
- [ ] Empty state works after deployment.
- [ ] Manual populated live scan works with the selected provider session.
- [ ] Confirmed test paper trade persists across an app restart.
- [ ] Current return renders when a market price is available.
- [ ] Confirmed manual close flow persists realized P&L and return.
- [ ] Portfolio snapshot persists and reloads.
- [ ] No automatic execution and no broker credentials are present.
- [ ] Final V1 paper-trading acceptance completed.

# Dammi’s personal site

Personal site with **finances** (Plaid + Span) and **coding** (GitHub contribution graph).

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# fill .env — see below
python import_csv.py "/path/to/Primary Savings Transactions.csv"   # optional backfill
python app.py   # http://127.0.0.1:8001
```

Routes: `/` · `/finances` · `/coding` · `/login` · `/link` (Plaid re-link)

## `.env`

```
PLAID_CLIENT_ID=
PLAID_SECRET=
PLAID_ACCESS_TOKEN=
PLAID_WEBHOOK_URL=https://your-tunnel/plaid/webhook
PLAID_REDIRECT_URI=https://your-tunnel/oauth-return

# GitHub OAuth (required for production lock)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
ALLOWED_GITHUB_LOGIN=your-github-username
SECRET_KEY=long-random-string

# Optional: PAT so /coding works without a browser session
GITHUB_TOKEN=

DATABASE_PATH=transactions.db
```

Without GitHub OAuth vars, the site stays open locally (auth off).

## Deploy (Fly.io)

1. Buy/point `dammimastersite.com` to your Fly app.
2. Create a GitHub OAuth App — callback `https://dammimastersite.com/auth/github/callback`.
3. `fly apps create dammi-personal-site`
4. `fly volumes create dammi_data --size 1 --region ewr`
5. `fly secrets set PLAID_CLIENT_ID=... PLAID_SECRET=... PLAID_ACCESS_TOKEN=... GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... ALLOWED_GITHUB_LOGIN=... SECRET_KEY=...`
6. `fly deploy`
7. `fly certs add dammimastersite.com`
8. Re-link bank at `/link` if you need 24 months of Plaid merchant history.
9. Copy `transactions.db` onto the volume once if you want local history in prod (`fly sftp` / console).

Plaid webhook URL in production: `https://dammimastersite.com/plaid/webhook`.

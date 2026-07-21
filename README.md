# Dammi’s personal site

Personal site with **finances** (Plaid + Span), **coding** (GitHub contribution graph), and **servers** (live health, a web terminal, and file transfer for a Raspberry Pi 5 over Tailscale).

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# fill .env — see below
python import_csv.py "/path/to/Primary Savings Transactions.csv"   # optional backfill
python app.py   # http://127.0.0.1:8001
```

Routes: `/` · `/finances` · `/coding` · `/servers` · `/login` · `/link` (Plaid re-link)

## Servers — Raspberry Pi console

`/servers` shows a live RPi5 card (CPU, temp, memory, disk, load, uptime, polled every ~4s). Click it to open a console:

- **Terminal** — an xterm.js shell wired over Socket.IO to `terminal.py`, which SSHes to the Pi *through a local Tailscale SOCKS5 proxy*. Run/download anything by typing.
- **Files** — an SFTP-backed browser (`pi.py`) to navigate, download files from the Pi, and drag-and-drop uploads. Confined to `PI_HOME` (default `/home/<PI_SSH_USER>`).

How it reaches the Pi: the deployed container runs `tailscaled` in **userspace mode** (`start.sh`) exposing a SOCKS5 proxy on `localhost:1055`, joins your tailnet with `TS_AUTHKEY`, and every SSH/SFTP connection is routed through that proxy to `PI_TAILSCALE_IP`. Only your GitHub id (`OWNER_GITHUB_ID`) may open a shell — the socket re-checks identity on connect, so the OAuth page gate isn't the only guard.

**Requires ONE worker** (`--workers 1`) — each browser's SSH shell lives in that worker's memory. `start.sh` already sets this.

### Servers `.env` / secrets

```
TS_AUTHKEY=tskey-...        # ephemeral, tagged, reusable Tailscale auth key (container joins tailnet)
PI_TAILSCALE_IP=100.x.y.z   # the Pi's tailnet IP
PI_SSH_USER=dammi
PI_SSH_PASSWORD=...         # password auth (consider switching to SSH keys later)
OWNER_GITHUB_ID=65834138    # only this GitHub id can open the shell
# optional: PI_SSH_PORT=22, PI_HOME=/home/dammi, TS_SOCKS_HOST=127.0.0.1, TS_SOCKS_PORT=1055
```

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

# Required for /coding contribution graph (server-side PAT — not stored in cookies)
GITHUB_TOKEN=

# Public URL of this deploy (https). Used for OAuth callback + secure cookies.
BASE_URL=https://dammimastersite.com

DATABASE_PATH=transactions.db
```

Without GitHub OAuth vars, the site stays open locally (auth off).

Local: `python app.py` (set `FLASK_DEBUG=1` only if you want the debugger).  
Production: gunicorn via Docker (`workers=1`).

## Deploy (Fly.io)

1. Buy/point `dammimastersite.com` to your Fly app.
2. Create a GitHub OAuth App — callback `https://dammimastersite.com/auth/github/callback`.
3. `fly apps create dammi-personal-site`
4. `fly volumes create dammi_data --size 1 --region ewr`
5. `fly secrets set PLAID_CLIENT_ID=... PLAID_SECRET=... PLAID_ACCESS_TOKEN=... GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... ALLOWED_GITHUB_LOGIN=... SECRET_KEY=... GITHUB_TOKEN=... BASE_URL=https://dammimastersite.com`
6. `fly deploy`
7. `fly certs add dammimastersite.com`
8. Re-link bank at `/link` if you need 24 months of Plaid merchant history.
9. Copy `transactions.db` onto the volume once if you want local history in prod (`fly sftp` / console).

Plaid webhook URL in production: `https://dammimastersite.com/plaid/webhook`.

## Deploy (Render)

1. Web Service from this repo using **Docker** (the Dockerfile installs Tailscale and runs `start.sh`, which is required for the `/servers` Pi console).
2. `start.sh` boots `tailscaled` (userspace, SOCKS5 on `localhost:1055`), joins the tailnet with `TS_AUTHKEY`, then runs gunicorn with **1 worker** (required — the SSH shell lives in worker memory).
3. Attach a **persistent disk** and set `DATABASE_PATH=/data/transactions.db` (or your mount path). Re-linked Plaid tokens are also written next to the DB as `plaid_access_token`.
4. Environment variables (all required for a locked production site):

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Long random string (required — app will refuse to start without it on Render) |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth App |
| `ALLOWED_GITHUB_LOGIN` | Your GitHub username |
| `ALLOWED_GITHUB_ID` | Optional but recommended — your numeric GitHub user id (`https://api.github.com/users/<you>`). Pins the allowlist to the immutable id so a freed/renamed username can't be re-registered to log in. |
| `GITHUB_TOKEN` | PAT for `/coding` (read:user is enough for public contrib graph) |
| `BASE_URL` | `https://your-service.onrender.com` or custom domain |
| `GITHUB_CALLBACK_URL` | Optional override; default `{BASE_URL}/auth/github/callback` |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` / `PLAID_ACCESS_TOKEN` | Plaid |
| `PLAID_WEBHOOK_URL` | `https://your-host/plaid/webhook` |
| `PLAID_REDIRECT_URI` | Must match Plaid dashboard + `/oauth-return` |
| `TS_AUTHKEY` | Tailscale auth key (ephemeral + tagged + reusable) so the container joins your tailnet |
| `PI_TAILSCALE_IP` / `PI_SSH_USER` / `PI_SSH_PASSWORD` | Raspberry Pi reachable over the tailnet + SSH login |
| `OWNER_GITHUB_ID` | Only this numeric GitHub id may open the Pi shell |

5. In the GitHub OAuth App, set Authorization callback URL to  
   `https://<your-render-host>/auth/github/callback` (same as `BASE_URL`).
6. Do **not** set `FLASK_DEBUG=1` on Render.

import os
import re
import threading
from functools import wraps
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products

from coding import fetch_contributions
from db import get_conn, init_db
import sync

load_dotenv()
init_db()

ENV_PATH = Path(__file__).resolve().parent / ".env"
REDIRECT_URI = os.environ.get(
    "PLAID_REDIRECT_URI",
    "https://false-stiffness-popular.ngrok-free.dev/oauth-return",
)
WEBHOOK_URL = os.environ.get("PLAID_WEBHOOK_URL")
DAYS_REQUESTED = 730
ALLOWED_GITHUB_LOGIN = (os.environ.get("ALLOWED_GITHUB_LOGIN") or "").strip().lstrip("@")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
AUTH_ENABLED = bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET and ALLOWED_GITHUB_LOGIN)

_plaid_config = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={
        "clientId": os.environ["PLAID_CLIENT_ID"],
        "secret": os.environ["PLAID_SECRET"],
    },
)
plaid_client = plaid_api.PlaidApi(plaid.ApiClient(_plaid_config))
stored_link_token = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("PLAID_SECRET") or "dev-only-change-me"

oauth = OAuth(app)
if AUTH_ENABLED:
    oauth.register(
        name="github",
        client_id=GITHUB_CLIENT_ID,
        client_secret=GITHUB_CLIENT_SECRET,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user"},
    )

SYNC_WEBHOOK_CODES = {
    "SYNC_UPDATES_AVAILABLE",
    "INITIAL_UPDATE",
    "HISTORICAL_UPDATE",
    "DEFAULT_UPDATE",
    "TRANSACTIONS_REMOVED",
}
TRANSFER_CATEGORIES = ("TRANSFER_IN", "TRANSFER_OUT")
PUBLIC_ENDPOINTS = {
    "login", "login_github", "auth_github_callback", "logout",
    "plaid_webhook", "static",
}


def _write_env_access_token(token: str):
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    if re.search(r"^PLAID_ACCESS_TOKEN=.*$", text, flags=re.M):
        text = re.sub(r"^PLAID_ACCESS_TOKEN=.*$", f"PLAID_ACCESS_TOKEN={token}", text, flags=re.M)
    else:
        text = text.rstrip() + f"\nPLAID_ACCESS_TOKEN={token}\n"
    ENV_PATH.write_text(text)
    os.environ["PLAID_ACCESS_TOKEN"] = token
    sync.set_access_token(token)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not AUTH_ENABLED:
            return view(*args, **kwargs)
        if session.get("github_login") == ALLOWED_GITHUB_LOGIN:
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login", next=request.path))
    return wrapped


@app.before_request
def _guard():
    if not AUTH_ENABLED:
        return None
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if request.endpoint == "plaid_webhook":
        return None
    if session.get("github_login") == ALLOWED_GITHUB_LOGIN:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect(url_for("login", next=request.path))


# ——— Auth ———

@app.route("/login")
def login():
    if AUTH_ENABLED and session.get("github_login") == ALLOWED_GITHUB_LOGIN:
        return redirect(url_for("home"))
    if not AUTH_ENABLED:
        return (
            "<html><body style='font-family:system-ui;padding:2rem'>"
            "<h1>Auth off</h1>"
            "<p>Set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, and ALLOWED_GITHUB_LOGIN in .env to enable OAuth.</p>"
            "<p><a href='/'>home</a></p></body></html>"
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>login — Dammi’s personal site</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700&family=Sora:wght@400;600&display=swap" rel="stylesheet">
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Sora,sans-serif;color:#0e1a24;
background:radial-gradient(900px 500px at 10% 0%,#c8f3ea,transparent 55%),#eef2f5}}
.panel{{text-align:center;padding:48px 32px}}
h1{{font-family:Fraunces,serif;font-size:42px;letter-spacing:-.03em;margin:0 0 12px}}
p{{color:#4a5a68;margin:0 0 28px}}
a.btn{{display:inline-block;font:600 15px Sora,sans-serif;border-radius:999px;padding:14px 28px;
background:#0e1a24;color:#f4fbf8;text-decoration:none}}
</style></head><body><div class="panel">
<h1>Dammi’s personal site</h1>
<p>Sign in with GitHub to continue.</p>
<a class="btn" href="{url_for('login_github')}">Continue with GitHub</a>
</div></body></html>"""


@app.route("/login/github")
def login_github():
    if not AUTH_ENABLED:
        return redirect(url_for("home"))
    redirect_uri = url_for("auth_github_callback", _external=True)
    return oauth.github.authorize_redirect(redirect_uri)


@app.route("/auth/github/callback")
def auth_github_callback():
    if not AUTH_ENABLED:
        return redirect(url_for("home"))
    token = oauth.github.authorize_access_token()
    resp = oauth.github.get("user", token=token)
    profile = resp.json()
    login_name = profile.get("login") or ""
    if login_name != ALLOWED_GITHUB_LOGIN:
        session.clear()
        return (
            f"<html><body style='font-family:system-ui;padding:2rem'>"
            f"<h1>Access denied</h1><p>{login_name!r} is not allowlisted.</p>"
            f"<a href='/login'>try again</a></body></html>",
            403,
        )
    session["github_login"] = login_name
    session["github_token"] = token.get("access_token")
    return redirect(request.args.get("next") or url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login") if AUTH_ENABLED else url_for("home"))


# ——— Pages ———

@app.route("/")
def home():
    return send_from_directory(".", "home.html")


@app.route("/finances")
@app.route("/span")
@app.route("/span.html")
@app.route("/dashboard")
def finances():
    return send_from_directory(".", "span.html")


@app.route("/coding")
def coding_page():
    return send_from_directory(".", "coding.html")


@app.route("/servers")
def servers_page():
    return send_from_directory(".", "servers.html")


@app.route("/dashboard_a")
@app.route("/dashboard_a.html")
def dashboard_a():
    return send_from_directory(".", "dashboard_a.html")


@app.route("/dashboard_b")
@app.route("/dashboard_b.html")
def dashboard_b():
    return send_from_directory(".", "dashboard_b.html")


@app.route("/table")
def table():
    return TABLE_HTML


# ——— Plaid webhook / link ———

@app.route("/plaid/webhook", methods=["POST"])
def plaid_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    print(f"plaid webhook: {webhook_type}.{webhook_code} item={payload.get('item_id')}")
    if webhook_type == "TRANSACTIONS" and webhook_code in SYNC_WEBHOOK_CODES:
        threading.Thread(target=sync.run, daemon=True).start()
    return jsonify({"ok": True}), 200


@app.route("/api/sync", methods=["POST"])
def api_sync():
    return jsonify(sync.run())


@app.route("/link")
def link_bank():
    global stored_link_token
    kwargs = dict(
        user=LinkTokenCreateRequestUser(client_user_id="me"),
        client_name="Dammi finances",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
        redirect_uri=REDIRECT_URI,
        transactions=LinkTokenTransactions(days_requested=DAYS_REQUESTED),
    )
    if WEBHOOK_URL:
        kwargs["webhook"] = WEBHOOK_URL
    resp = plaid_client.link_token_create(LinkTokenCreateRequest(**kwargs))
    stored_link_token = resp.link_token
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>link — finances</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Sora:wght@400;600&display=swap');
body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Sora,sans-serif;color:#0e1a24;
background:radial-gradient(900px 500px at 10% 0%,#c8f3ea,transparent 55%),#eef2f5}}
.panel{{text-align:center;max-width:420px;padding:48px 32px}}
h1{{font-family:Fraunces,serif;font-size:42px;letter-spacing:-.03em;margin:0 0 12px}}
p{{color:#4a5a68;line-height:1.5;margin:0 0 28px}}
button{{font:600 15px Sora,sans-serif;border:0;border-radius:999px;padding:14px 28px;background:#0e1a24;color:#f4fbf8;cursor:pointer}}
</style></head><body>
<div class="panel">
  <h1>finances</h1>
  <p>Re-link DCU for up to 24 months of history. Your previous Item may only have ~90 days.</p>
  <button id="btn">Link DCU — all-time</button>
</div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const handler = Plaid.create({{
    token: "{stored_link_token}",
    onSuccess: (public_token) => {{
      document.body.innerHTML = "<div class='panel'><h1>Syncing…</h1><p>Pulling full history.</p></div>";
      fetch("/exchange", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{public_token}})
      }}).then(r => r.json()).then(d => {{
        if (d.error) {{ document.body.innerHTML = "<div class='panel'><h1>Error</h1><p>"+d.error+"</p></div>"; return; }}
        location.href = "/finances?fresh=1";
      }});
    }}
  }});
  document.getElementById("btn").onclick = () => handler.open();
</script>
</body></html>"""


@app.route("/oauth-return")
def oauth_return():
    return f"""<!doctype html>
<html><body style="font-family:system-ui;padding:2rem">
<p>Completing DCU login…</p>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const handler = Plaid.create({{
    token: "{stored_link_token}",
    receivedRedirectUri: window.location.href,
    onSuccess: (public_token) => {{
      document.body.innerHTML = "<p>Syncing full history…</p>";
      fetch("/exchange", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{public_token}})
      }}).then(r => r.json()).then(d => {{
        if (d.error) {{ document.body.innerText = d.error; return; }}
        location.href = "/finances?fresh=1";
      }});
    }}
  }});
  handler.open();
</script>
</body></html>"""


@app.route("/exchange", methods=["POST"])
def exchange():
    public_token = (request.json or {}).get("public_token")
    if not public_token:
        return jsonify({"error": "missing public_token"}), 400
    old_token = os.environ.get("PLAID_ACCESS_TOKEN")
    try:
        resp = plaid_client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token))
        new_token = resp.access_token
        _write_env_access_token(new_token)
        if WEBHOOK_URL:
            try:
                sync.register_webhook(WEBHOOK_URL)
            except Exception as e:
                print(f"webhook register after link: {e}")
        if old_token and old_token != new_token:
            try:
                plaid_client.item_remove(ItemRemoveRequest(access_token=old_token))
                print("removed previous Item")
            except Exception as e:
                print(f"old item remove skipped: {e}")
        stats = sync.reset_and_sync()
        return jsonify({"access_token": new_token, "stats": stats, "days_requested": DAYS_REQUESTED})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ——— APIs ———

@app.route("/api/transactions")
def api_transactions():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/range")
def api_range():
    conn = get_conn()
    row = conn.execute(
        "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM transactions"
    ).fetchone()
    conn.close()
    return jsonify({"min_date": row["min_date"], "max_date": row["max_date"]})


@app.route("/api/summary")
def api_summary():
    conn = get_conn()
    bounds = conn.execute(
        "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM transactions"
    ).fetchone()
    start = request.args.get("start") or bounds["min_date"]
    end = request.args.get("end") or bounds["max_date"]

    flow = conn.execute("""
        SELECT
            ROUND(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 2) AS total_out,
            ROUND(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 2) AS total_in
        FROM transactions WHERE pending = 0 AND date BETWEEN ? AND ?
    """, (start, end)).fetchone()

    categories = conn.execute("""
        SELECT category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS n
        FROM transactions
        WHERE pending = 0 AND amount > 0 AND category NOT IN (?, ?) AND date BETWEEN ? AND ?
        GROUP BY category ORDER BY total DESC
    """, TRANSFER_CATEGORIES + (start, end)).fetchall()

    daily = conn.execute("""
        SELECT date, ROUND(SUM(CASE WHEN amount > 0 AND category NOT IN (?, ?) THEN amount ELSE 0 END), 2) AS spent
        FROM transactions
        WHERE pending = 0 AND date BETWEEN ? AND ?
        GROUP BY date ORDER BY date
    """, TRANSFER_CATEGORIES + (start, end)).fetchall()

    weekly = conn.execute("""
        SELECT strftime('%Y-W%W', date) AS week, MIN(date) AS week_start,
               ROUND(SUM(CASE WHEN amount > 0 AND category NOT IN (?, ?) THEN amount ELSE 0 END), 2) AS spent
        FROM transactions
        WHERE pending = 0 AND date BETWEEN ? AND ?
        GROUP BY week ORDER BY week
    """, TRANSFER_CATEGORIES + (start, end)).fetchall()

    monthly = conn.execute("""
        SELECT strftime('%Y-%m', date) AS month,
               ROUND(SUM(CASE WHEN amount > 0 AND category NOT IN (?, ?) THEN amount ELSE 0 END), 2) AS spent
        FROM transactions
        WHERE pending = 0 AND date BETWEEN ? AND ?
        GROUP BY month ORDER BY month
    """, TRANSFER_CATEGORIES + (start, end)).fetchall()

    conn.close()
    return jsonify({
        "start": start,
        "end": end,
        "min_date": bounds["min_date"],
        "max_date": bounds["max_date"],
        "total_out": flow["total_out"] or 0,
        "total_in": flow["total_in"] or 0,
        "net": round((flow["total_in"] or 0) - (flow["total_out"] or 0), 2),
        "categories": [dict(r) for r in categories],
        "daily": [dict(r) for r in daily],
        "weekly": [dict(r) for r in weekly],
        "monthly": [dict(r) for r in monthly],
    })


@app.route("/api/coding")
def api_coding():
    login = session.get("github_login") or ALLOWED_GITHUB_LOGIN
    token = session.get("github_token") or os.environ.get("GITHUB_TOKEN")
    if not login:
        return jsonify({"error": "Set ALLOWED_GITHUB_LOGIN (and sign in) to load coding."})
    if not token:
        return jsonify({
            "error": "Sign in with GitHub (or set GITHUB_TOKEN) to load your contribution graph."
        })
    return jsonify(fetch_contributions(token, login))


TABLE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>table — finances</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 1.5rem; background: #111; color: #eee; }
  #controls { margin-bottom: 1rem; display: flex; gap: 1rem; align-items: center; }
  #search { padding: .4rem .6rem; width: 300px; background: #222; border: 1px solid #444; color: #eee; }
  #count { color: #888; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #333; white-space: nowrap; }
  th { position: sticky; top: 0; background: #1a1a1a; cursor: pointer; }
  td.amount-pos { color: #f77; } td.amount-neg { color: #7f7; }
  tr.pending td { opacity: .55; font-style: italic; }
  #wrap { max-height: 85vh; overflow: auto; border: 1px solid #333; }
  a { color: #7fd; }
</style></head><body>
  <div id="controls">
    <a href="/finances">← finances</a>
    <input id="search" placeholder="Filter…">
    <span id="count"></span>
  </div>
  <div id="wrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
<script>
let rows=[], cols=[], sortCol="date", sortDir=-1;
async function load(){ rows=await (await fetch("/api/transactions")).json(); cols=rows.length?Object.keys(rows[0]):[]; renderHead(); render(); }
function renderHead(){ const thead=document.querySelector("#tbl thead");
  thead.innerHTML="<tr>"+cols.map(c=>`<th data-col="${c}">${c}</th>`).join("")+"</tr>";
  thead.querySelectorAll("th").forEach(th=>th.onclick=()=>{ const c=th.dataset.col; if(sortCol===c) sortDir*=-1; else {sortCol=c;sortDir=1;} render(); });
}
function render(){ const q=document.getElementById("search").value.toLowerCase();
  let filtered=rows.filter(r=>!q||Object.values(r).some(v=>v!=null&&String(v).toLowerCase().includes(q)));
  filtered.sort((a,b)=>{ const av=a[sortCol], bv=b[sortCol]; if(av==null)return 1; if(bv==null)return -1; if(av<bv)return -1*sortDir; if(av>bv)return 1*sortDir; return 0; });
  document.getElementById("count").textContent=`${filtered.length} / ${rows.length}`;
  document.querySelector("#tbl tbody").innerHTML=filtered.map(r=>{
    const cls=r.pending?"pending":"";
    return "<tr class='"+cls+"'>"+cols.map(c=>{ let v=r[c], tdClass=""; if(c==="amount"&&v!=null) tdClass=v>0?"amount-pos":"amount-neg"; return `<td class="${tdClass}">${v==null?"":v}</td>`; }).join("")+"</tr>";
  }).join("");
}
document.getElementById("search").addEventListener("input", render); load();
</script></body></html>
"""


if __name__ == "__main__":
    print(f"auth enabled: {AUTH_ENABLED}")
    if WEBHOOK_URL:
        try:
            sync.register_webhook(WEBHOOK_URL)
        except Exception as e:
            print(f"webhook register skipped: {e}")
    try:
        sync.run()
    except Exception as e:
        print(f"startup sync skipped: {e}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), debug=True, use_reloader=False)

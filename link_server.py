import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.link_token_transactions import LinkTokenTransactions

load_dotenv()
config = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={"clientId": os.environ["PLAID_CLIENT_ID"], "secret": os.environ["PLAID_SECRET"]},
)
client = plaid_api.PlaidApi(plaid.ApiClient(config))
REDIRECT_URI = os.environ.get(
    "PLAID_REDIRECT_URI",
    "https://false-stiffness-popular.ngrok-free.dev/oauth-return",
)
WEBHOOK_URL = os.environ.get("PLAID_WEBHOOK_URL")
# Max history Plaid allows (24 months). Locked in at Item creation — re-link to change.
DAYS_REQUESTED = 730

app = Flask(__name__)
stored_link_token = None  # holds token across the OAuth redirect

@app.route("/")
def index():
    global stored_link_token
    kwargs = dict(
        user=LinkTokenCreateRequestUser(client_user_id="me"),
        client_name="Spending Tracker",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
        redirect_uri=REDIRECT_URI,
        transactions=LinkTokenTransactions(days_requested=DAYS_REQUESTED),
    )
    if WEBHOOK_URL:
        kwargs["webhook"] = WEBHOOK_URL
    resp = client.link_token_create(LinkTokenCreateRequest(**kwargs))
    stored_link_token = resp.link_token
    return f"""
    <html><body>
    <button id="btn">Link DCU</button>
    <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <script>
      const handler = Plaid.create({{
        token: "{stored_link_token}",
        onSuccess: (public_token) => {{
          fetch("/exchange", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{public_token}})
          }}).then(r => r.json()).then(d => document.body.innerText =
            "ACCESS TOKEN (paste into .env): " + d.access_token);
        }}
      }});
      document.getElementById("btn").onclick = () => handler.open();
    </script>
    </body></html>
    """

# DCU's OAuth flow redirects back here after you log in on their site.
# We reopen Link with the same token to finish the handshake.
@app.route("/oauth-return")
def oauth_return():
    return f"""
    <html><body>
    <p>Completing DCU login...</p>
    <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <script>
      const handler = Plaid.create({{
        token: "{stored_link_token}",
        receivedRedirectUri: window.location.href,
        onSuccess: (public_token) => {{
          fetch("/exchange", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{public_token}})
          }}).then(r => r.json()).then(d => document.body.innerText =
            "ACCESS TOKEN (paste into .env): " + d.access_token);
        }}
      }});
      handler.open();
    </script>
    </body></html>
    """

@app.route("/exchange", methods=["POST"])
def exchange():
    public_token = request.json["public_token"]
    resp = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token))
    return jsonify({"access_token": resp.access_token})

if __name__ == "__main__":
    app.run(port=8000)

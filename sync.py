import json
import os
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from db import get_conn, init_db

load_dotenv()
config = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={"clientId": os.environ["PLAID_CLIENT_ID"], "secret": os.environ["PLAID_SECRET"]},
)
client = plaid_api.PlaidApi(plaid.ApiClient(config))
ACCESS_TOKEN = os.environ["PLAID_ACCESS_TOKEN"]

def get_cursor(conn):
    row = conn.execute("SELECT cursor FROM sync_state WHERE item = 'main'").fetchone()
    return row["cursor"] if row else None

def save_cursor(conn, cursor):
    conn.execute(
        "INSERT INTO sync_state (item, cursor) VALUES ('main', ?) "
        "ON CONFLICT(item) DO UPDATE SET cursor = excluded.cursor", (cursor,))

def upsert(conn, t):
    d = t.to_dict()
    pfc = d.get("personal_finance_category") or {}
    loc = d.get("location") or {}
    pm = d.get("payment_meta") or {}
    conn.execute("""
        INSERT INTO transactions (
            id, account_id, date, authorized_date, datetime, authorized_datetime,
            name, original_description, merchant_name, merchant_entity_id,
            amount, iso_currency_code, unofficial_currency_code,
            category, category_detailed, category_confidence, category_icon_url,
            plaid_category, category_id,
            payment_channel, transaction_code, transaction_type, check_number,
            pending, pending_transaction_id, account_owner,
            logo_url, website,
            location_address, location_city, location_region, location_postal_code,
            location_country, location_lat, location_lon, location_store_number,
            payment_meta, counterparties
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            date=excluded.date, authorized_date=excluded.authorized_date,
            datetime=excluded.datetime, authorized_datetime=excluded.authorized_datetime,
            name=excluded.name, original_description=excluded.original_description,
            merchant_name=excluded.merchant_name, merchant_entity_id=excluded.merchant_entity_id,
            amount=excluded.amount, iso_currency_code=excluded.iso_currency_code,
            unofficial_currency_code=excluded.unofficial_currency_code,
            category=excluded.category, category_detailed=excluded.category_detailed,
            category_confidence=excluded.category_confidence, category_icon_url=excluded.category_icon_url,
            plaid_category=excluded.plaid_category, category_id=excluded.category_id,
            payment_channel=excluded.payment_channel, transaction_code=excluded.transaction_code,
            transaction_type=excluded.transaction_type, check_number=excluded.check_number,
            pending=excluded.pending, pending_transaction_id=excluded.pending_transaction_id,
            account_owner=excluded.account_owner, logo_url=excluded.logo_url, website=excluded.website,
            location_address=excluded.location_address, location_city=excluded.location_city,
            location_region=excluded.location_region, location_postal_code=excluded.location_postal_code,
            location_country=excluded.location_country, location_lat=excluded.location_lat,
            location_lon=excluded.location_lon, location_store_number=excluded.location_store_number,
            payment_meta=excluded.payment_meta, counterparties=excluded.counterparties
    """, (
        d.get("transaction_id"), d.get("account_id"), str(d.get("date")),
        str(d.get("authorized_date")) if d.get("authorized_date") else None,
        d.get("datetime").isoformat() if d.get("datetime") else None,
        d.get("authorized_datetime").isoformat() if d.get("authorized_datetime") else None,
        d.get("name"), d.get("original_description"), d.get("merchant_name"), d.get("merchant_entity_id"),
        d.get("amount"), d.get("iso_currency_code"), d.get("unofficial_currency_code"),
        pfc.get("primary"), pfc.get("detailed"), pfc.get("confidence_level"),
        d.get("personal_finance_category_icon_url"),
        json.dumps(d.get("category")) if d.get("category") else None,
        d.get("category_id"),
        d.get("payment_channel"), d.get("transaction_code"), d.get("transaction_type"), d.get("check_number"),
        int(d.get("pending") or False), d.get("pending_transaction_id"), d.get("account_owner"),
        d.get("logo_url"), d.get("website"),
        loc.get("address"), loc.get("city"), loc.get("region"), loc.get("postal_code"),
        loc.get("country"), loc.get("lat"), loc.get("lon"), loc.get("store_number"),
        json.dumps(pm, default=str) if pm else None,
        json.dumps(d.get("counterparties"), default=str) if d.get("counterparties") else None,
    ))

def run():
    """Pull latest transaction updates from Plaid into SQLite. Returns a stats dict."""
    init_db()
    conn = get_conn()
    cursor = get_cursor(conn)
    added = modified = removed = 0
    has_more = True
    while has_more:
        resp = client.transactions_sync(
            TransactionsSyncRequest(access_token=ACCESS_TOKEN, cursor=cursor or ""))
        for t in resp.added:    upsert(conn, t); added += 1
        for t in resp.modified: upsert(conn, t); modified += 1
        for t in resp.removed:
            conn.execute("DELETE FROM transactions WHERE id = ?", (t.transaction_id,)); removed += 1
        cursor = resp.next_cursor
        has_more = resp.has_more
    save_cursor(conn, cursor)
    conn.commit()
    conn.close()
    stats = {"added": added, "modified": modified, "removed": removed}
    print(f"sync: added={added} modified={modified} removed={removed}")
    return stats


def register_webhook(webhook_url: str):
    """Point this Item's Plaid webhook at webhook_url (must be publicly reachable)."""
    from plaid.model.item_webhook_update_request import ItemWebhookUpdateRequest
    resp = client.item_webhook_update(
        ItemWebhookUpdateRequest(access_token=ACCESS_TOKEN, webhook=webhook_url))
    print(f"webhook registered: {webhook_url} (item={resp.item.item_id})")
    return resp


def set_access_token(token: str):
    """Hot-swap the access token used by sync (after a re-link)."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = token


def reset_and_sync():
    """Clear sync cursor + local txs, then pull the Item's full available history."""
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM sync_state")
    conn.commit()
    conn.close()
    return run()


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--register-webhook":
        register_webhook(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "--reset":
        reset_and_sync()
    else:
        run()

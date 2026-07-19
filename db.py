import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "transactions.db"))


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        account_id TEXT,
        date TEXT,
        authorized_date TEXT,
        datetime TEXT,
        authorized_datetime TEXT,
        name TEXT,
        original_description TEXT,
        merchant_name TEXT,
        merchant_entity_id TEXT,
        amount REAL,
        iso_currency_code TEXT,
        unofficial_currency_code TEXT,
        category TEXT,
        category_detailed TEXT,
        category_confidence TEXT,
        category_icon_url TEXT,
        plaid_category TEXT,
        category_id TEXT,
        payment_channel TEXT,
        transaction_code TEXT,
        transaction_type TEXT,
        check_number TEXT,
        pending INTEGER,
        pending_transaction_id TEXT,
        account_owner TEXT,
        logo_url TEXT,
        website TEXT,
        location_address TEXT,
        location_city TEXT,
        location_region TEXT,
        location_postal_code TEXT,
        location_country TEXT,
        location_lat REAL,
        location_lon REAL,
        location_store_number TEXT,
        payment_meta TEXT,
        counterparties TEXT
    );
    CREATE TABLE IF NOT EXISTS sync_state (
        item TEXT PRIMARY KEY,
        cursor TEXT
    );
    CREATE TABLE IF NOT EXISTS coding_cache (
        key TEXT PRIMARY KEY,
        fetched_at TEXT,
        payload TEXT
    );
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()

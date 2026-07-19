"""Import a DCU account CSV into transactions.db.

IDs are prefixed with csv: so they never collide with Plaid transaction_ids.
Re-running is safe (UPSERT). Plaid sync continues to append new rows separately.

Usage:
  python import_csv.py "/Users/damodarpai/Downloads/Primary Savings Transactions.csv"
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

from db import get_conn, init_db

# DCU CSV: credits are positive dollars in; debits are negative.
# Plaid convention in this app: positive amount = money leaving the account.


def parse_amount(raw: str) -> float:
    s = (raw or "").strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    # CSV: CREDIT $100 → keep as income (negative in Plaid sense)
    #       DEBIT -$9.95 → money out → positive
    val = float(s)
    return -val  # flip bank-export sign into Plaid spend convention


def parse_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")


def classify(description: str, tx_type: str) -> tuple[str, str]:
    d = (description or "").upper()
    if "OVERDRAFT" in d or "WITHDRAWAL-OVERDRAFT" in d:
        return "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"
    if "TRANSFER TO" in d or "TRANSFER FROM" in d or d.startswith("TRANSFER -"):
        if tx_type == "CREDIT":
            return "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"
        return "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"
    if "DIVIDEND" in d:
        return "INCOME", "INCOME_DIVIDENDS"
    if "PAYROLL" in d or "DEPOSIT" in d or "GUSTO" in d or "INTELLIGEN" in d:
        return "INCOME", "INCOME_WAGES"
    if tx_type == "CREDIT":
        return "INCOME", "INCOME_OTHER_INCOME"
    return "GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE"


def row_id(csv_id: str, date: str, description: str, amount: float) -> str:
    raw = (csv_id or "").strip()
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"csv:{digest}"
    fallback = f"{date}|{description}|{amount:.2f}"
    return "csv:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:32]


def clean_name(description: str) -> str:
    name = re.sub(r"\s+", " ", (description or "").strip())
    return name[:200]


def import_csv(path: Path) -> dict:
    init_db()
    conn = get_conn()
    added = updated = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = parse_date(row["DATE"])
            tx_type = (row.get("TRANSACTION TYPE") or "").strip().upper()
            description = row.get("DESCRIPTION") or ""
            amount = parse_amount(row.get("AMOUNT") or "0")
            status = (row.get("STATUS") or "").upper()
            pending = 0 if status in ("", "POSTED") else 1
            category, detailed = classify(description, tx_type)
            tid = row_id(row.get("ID") or "", date, description, amount)
            name = clean_name(description)

            exists = conn.execute("SELECT 1 FROM transactions WHERE id = ?", (tid,)).fetchone()
            conn.execute(
                """
                INSERT INTO transactions (
                    id, date, name, original_description, amount,
                    iso_currency_code, category, category_detailed,
                    pending, payment_channel, transaction_type
                ) VALUES (?, ?, ?, ?, ?, 'USD', ?, ?, ?, 'other', ?)
                ON CONFLICT(id) DO UPDATE SET
                    date=excluded.date,
                    name=excluded.name,
                    original_description=excluded.original_description,
                    amount=excluded.amount,
                    category=excluded.category,
                    category_detailed=excluded.category_detailed,
                    pending=excluded.pending
                """,
                (tid, date, name, description, amount, category, detailed, pending, tx_type.lower() or None),
            )
            if exists:
                updated += 1
            else:
                added += 1
    conn.commit()
    conn.close()
    return {"added": added, "updated": updated, "path": str(path)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_csv.py <path-to-csv>")
        sys.exit(1)
    stats = import_csv(Path(sys.argv[1]).expanduser())
    print(f"import: added={stats['added']} updated={stats['updated']} from {stats['path']}")

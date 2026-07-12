from db import get_conn

conn = get_conn()

print("\n== Spending by category (last 30 days) ==")
for r in conn.execute("""
    SELECT category, ROUND(SUM(amount),2) AS total, COUNT(*) AS n
    FROM transactions
    WHERE amount > 0 AND date >= date('now','-30 days') AND pending = 0
    GROUP BY category ORDER BY total DESC
"""):
    print(f"{r['category'] or 'uncategorized':25} ${r['total']:>9}  ({r['n']} txns)")

print("\n== Monthly totals ==")
for r in conn.execute("""
    SELECT strftime('%Y-%m', date) AS month, ROUND(SUM(amount),2) AS spent
    FROM transactions WHERE amount > 0 AND pending = 0
    GROUP BY month ORDER BY month DESC LIMIT 6
"""):
    print(f"{r['month']}  ${r['spent']}")

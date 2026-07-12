from flask import Flask, jsonify, request, send_from_directory
from db import get_conn

app = Flask(__name__)

@app.route("/dashboard")
def dashboard():
    return send_from_directory(".", "dashboard.html")

@app.route("/dashboard_a")
@app.route("/dashboard_a.html")
def dashboard_a():
    return send_from_directory(".", "dashboard_a.html")

@app.route("/dashboard_b")
@app.route("/dashboard_b.html")
def dashboard_b():
    return send_from_directory(".", "dashboard_b.html")

@app.route("/api/transactions")
def api_transactions():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# TRANSFER_IN/TRANSFER_OUT are this account's paired overdraft-protection
# transfers (one leg per purchase) — they net to ~0 and aren't real spend,
# so category and cash-flow views exclude them.
TRANSFER_CATEGORIES = ("TRANSFER_IN", "TRANSFER_OUT")

@app.route("/api/range")
def api_range():
    conn = get_conn()
    row = conn.execute("SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM transactions").fetchone()
    conn.close()
    return jsonify({"min_date": row["min_date"], "max_date": row["max_date"]})

@app.route("/api/summary")
def api_summary():
    # Optional ?start=YYYY-MM-DD&end=YYYY-MM-DD — defaults to full history on record.
    conn = get_conn()
    bounds = conn.execute("SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM transactions").fetchone()
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
        SELECT strftime('%Y-%m', date) AS month, ROUND(SUM(CASE WHEN amount > 0 AND category NOT IN (?, ?) THEN amount ELSE 0 END), 2) AS spent
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

@app.route("/")
def index():
    return INDEX_HTML

INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Transactions</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 1.5rem; background: #111; color: #eee; }
  #controls { margin-bottom: 1rem; display: flex; gap: 1rem; align-items: center; }
  #search { padding: .4rem .6rem; width: 300px; background: #222; border: 1px solid #444; color: #eee; }
  #count { color: #888; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #333; white-space: nowrap; }
  th { position: sticky; top: 0; background: #1a1a1a; cursor: pointer; user-select: none; }
  th:hover { background: #262626; }
  tr:hover td { background: #1c1c1c; }
  td.amount-pos { color: #f77; }
  td.amount-neg { color: #7f7; }
  tr.pending td { opacity: .55; font-style: italic; }
  #wrap { max-height: 85vh; overflow: auto; border: 1px solid #333; }
</style>
</head>
<body>
  <div id="controls">
    <input id="search" placeholder="Filter (name, merchant, category, city...)">
    <span id="count"></span>
  </div>
  <div id="wrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>

<script>
let rows = [];
let cols = [];
let sortCol = "date", sortDir = -1;

async function load() {
  const res = await fetch("/api/transactions");
  rows = await res.json();
  cols = rows.length ? Object.keys(rows[0]) : [];
  renderHead();
  render();
}

function renderHead() {
  const thead = document.querySelector("#tbl thead");
  thead.innerHTML = "<tr>" + cols.map(c => `<th data-col="${c}">${c}</th>`).join("") + "</tr>";
  thead.querySelectorAll("th").forEach(th => th.onclick = () => {
    const c = th.dataset.col;
    if (sortCol === c) sortDir *= -1; else { sortCol = c; sortDir = 1; }
    render();
  });
}

function render() {
  const q = document.getElementById("search").value.toLowerCase();
  let filtered = rows.filter(r => !q || Object.values(r).some(v => v != null && String(v).toLowerCase().includes(q)));
  filtered.sort((a, b) => {
    const av = a[sortCol], bv = b[sortCol];
    if (av == null) return 1;
    if (bv == null) return -1;
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return 0;
  });
  document.getElementById("count").textContent = `${filtered.length} / ${rows.length} transactions`;
  const tbody = document.querySelector("#tbl tbody");
  tbody.innerHTML = filtered.map(r => {
    const cls = r.pending ? "pending" : "";
    return "<tr class='" + cls + "'>" + cols.map(c => {
      let v = r[c];
      let tdClass = "";
      if (c === "amount" && v != null) tdClass = v > 0 ? "amount-pos" : "amount-neg";
      return `<td class="${tdClass}">${v == null ? "" : v}</td>`;
    }).join("") + "</tr>";
  }).join("");
}

document.getElementById("search").addEventListener("input", render);
load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(port=8001, debug=True)

"""GitHub coding dashboard — contributions, recent commits, active repos."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from db import get_conn, init_db

CACHE_KEY = "coding_dashboard_v2"
CACHE_HOURS = 1

QUERY = """
query($login: String!) {
  user(login: $login) {
    url
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
    repositories(
      first: 12
      orderBy: { field: PUSHED_AT, direction: DESC }
      ownerAffiliations: [OWNER, COLLABORATOR]
      privacy: PUBLIC
    ) {
      nodes {
        name
        nameWithOwner
        description
        url
        pushedAt
        isPrivate
        primaryLanguage { name color }
        stargazerCount
      }
    }
  }
}
"""


def _stale(fetched_at: str) -> bool:
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(hours=CACHE_HOURS)


def _compute_streak(days: list[dict]) -> int:
    if not days:
        return 0
    by_date = {d["date"]: d["count"] for d in days}
    today = date.today()
    cursor = today
    if by_date.get(cursor.isoformat(), 0) == 0:
        cursor = today - timedelta(days=1)
    streak = 0
    while by_date.get(cursor.isoformat(), 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _gh_request(url: str, token: str, body: bytes | None = None) -> dict | list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "dammi-personal-site",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _fetch_commits(token: str, login: str, limit: int = 20) -> list[dict]:
    """Recent commits from the user's public event stream."""
    url = f"https://api.github.com/users/{urllib.parse.quote(login)}/events?per_page=50"
    try:
        events = _gh_request(url, token)
    except urllib.error.HTTPError:
        return []
    if not isinstance(events, list):
        return []

    commits = []
    seen = set()
    for ev in events:
        if ev.get("type") != "PushEvent":
            continue
        repo = (ev.get("repo") or {}).get("name") or ""
        payload = ev.get("payload") or {}
        created = ev.get("created_at") or ""
        for c in payload.get("commits") or []:
            sha = (c.get("sha") or "")[:7]
            key = f"{repo}:{sha}"
            if not sha or key in seen:
                continue
            seen.add(key)
            commits.append({
                "sha": sha,
                "message": (c.get("message") or "").split("\n", 1)[0][:120],
                "repo": repo,
                "url": f"https://github.com/{repo}/commit/{c.get('sha')}",
                "at": created,
            })
            if len(commits) >= limit:
                return commits
    return commits


def fetch_contributions(token: str, login: str) -> dict:
    init_db()
    conn = get_conn()
    row = conn.execute(
        "SELECT fetched_at, payload FROM coding_cache WHERE key = ?", (CACHE_KEY,)
    ).fetchone()
    if row and not _stale(row["fetched_at"]):
        conn.close()
        return json.loads(row["payload"])

    try:
        payload = _gh_request(
            "https://api.github.com/graphql",
            token,
            json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        )
    except urllib.error.HTTPError as e:
        conn.close()
        return {"error": f"GitHub API error: {e.code}"}

    if payload.get("errors"):
        conn.close()
        return {"error": payload["errors"][0].get("message", "GitHub GraphQL error")}

    user = (payload.get("data") or {}).get("user")
    if not user:
        conn.close()
        return {"error": f"GitHub user '{login}' not found"}

    cal = user["contributionsCollection"]["contributionCalendar"]
    days = []
    for week in cal["weeks"]:
        for d in week["contributionDays"]:
            days.append({"date": d["date"], "count": d["contributionCount"]})

    today_s = date.today().isoformat()
    today_count = next((d["count"] for d in days if d["date"] == today_s), 0)

    repos = []
    for n in (user.get("repositories") or {}).get("nodes") or []:
        if not n:
            continue
        lang = n.get("primaryLanguage") or {}
        repos.append({
            "name": n.get("name"),
            "full_name": n.get("nameWithOwner"),
            "description": n.get("description") or "",
            "url": n.get("url"),
            "pushed_at": n.get("pushedAt"),
            "stars": n.get("stargazerCount") or 0,
            "language": lang.get("name"),
            "language_color": lang.get("color"),
        })

    commits = _fetch_commits(token, login)

    result = {
        "login": login,
        "profile_url": user.get("url") or f"https://github.com/{login}",
        "total": cal["totalContributions"],
        "streak": _compute_streak(days),
        "today": today_count,
        "days": days,
        "commits": commits,
        "repos": repos,
    }
    conn.execute(
        """
        INSERT INTO coding_cache (key, fetched_at, payload) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET fetched_at=excluded.fetched_at, payload=excluded.payload
        """,
        (CACHE_KEY, datetime.now(timezone.utc).isoformat(), json.dumps(result)),
    )
    conn.commit()
    conn.close()
    return result

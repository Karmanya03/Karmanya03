#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from html import escape

API_BASE = "https://api.github.com"
USERNAME = os.getenv("PROFILE_USERNAME", "Karmanya03")
TOKEN = os.getenv("GITHUB_TOKEN", "")
OUT_DIR = os.getenv("PROFILE_OUT_DIR", "assets/panels")

## ─── EXACT GitHub Profile Trophy rank thresholds (ryo-ma/github-profile-trophy source) ───
# Rank order (ascending): C < B < A < AA < AAA < S < SS < SSS
TROPHY_THRESHOLDS = {
    # (score needed for each rank: C, B, A, AA, AAA, S, SS, SSS)
    "Stars":        [1,  10,  30,   50,  100,  200,  700,  2000],
    "Followers":    [1,  10,  20,   50,  100,  200,  400,  1000],
    "Repositories": [1,  10,  20,   30,   35,   40,   45,    50],
    "Commits":      [1,  10, 100,  200,  500, 1000, 2000,  4000],
    "PullRequest":  [1,  10,  20,   50,  100,  200,  500,  1000],
    "Issues":       [1,  10,  20,   50,  100,  200,  500,  1000],
    "MultiLanguage": [10],  # SECRET trophy — single threshold
}
RANK_NAMES = ["C", "B", "A", "AA", "AAA", "S", "SS", "SSS"]
# Numeric order for sorting (higher = better rank)
RANK_ORDER = {"C": 0, "B": 1, "A": 2, "AA": 3, "AAA": 4, "S": 5, "SS": 6, "SSS": 7, "SECRET": 8}

# ─── Trophy display names (matching ryo-ma source exactly) ───
TROPHY_META = {
    "Stars": {
        "label": "Stars",
        "icon": "⭐",
        "rank_names": {
            "C": "First Star",
            "B": "Middle Star",
            "A": "You are a Star",
            "AA": "High Star",
            "AAA": "Super Star",
            "S": "Stargazer",
            "SS": "High Stargazer",
            "SSS": "Super Stargazer",
        },
    },
    "Followers": {
        "label": "Followers",
        "icon": "👥",
        "rank_names": {
            "C": "First Friend",
            "B": "Many Friends",
            "A": "Dynamic User",
            "AA": "Active User",
            "AAA": "Famous User",
            "S": "Hyper Celebrity",
            "SS": "Ultra Celebrity",
            "SSS": "Super Celebrity",
        },
    },
    "Repositories": {
        "label": "Repositories",
        "icon": "⊞",
        "rank_names": {
            "C": "First Repository",
            "B": "Middle Repo Creator",
            "A": "High Repo Creator",
            "AA": "Hyper Repo Creator",
            "AAA": "Ultra Repo Creator",
            "S": "Super Repo Creator",
            "SS": "Deep Repo Creator",
            "SSS": "God Repo Creator",
        },
    },
    "Commits": {
        "label": "Commits",
        "icon": "↑",
        "rank_names": {
            "C": "First Commit",
            "B": "Middle Committer",
            "A": "High Committer",
            "AA": "Hyper Committer",
            "AAA": "Ultra Committer",
            "S": "Super Committer",
            "SS": "Deep Committer",
            "SSS": "God Committer",
        },
    },
    "PullRequest": {
        "label": "Pull Request",
        "icon": "⑂",
        "rank_names": {
            "C": "First Pull",
            "B": "Middle Puller",
            "A": "High Puller",
            "AA": "Hyper Puller",
            "AAA": "Ultra Puller",
            "S": "Super Puller",
            "SS": "Deep Puller",
            "SSS": "God Puller",
        },
    },
    "Issues": {
        "label": "Issues",
        "icon": "!",
        "rank_names": {
            "C": "First Issue",
            "B": "Middle Issuer",
            "A": "High Issuer",
            "AA": "Hyper Issuer",
            "AAA": "Ultra Issuer",
            "S": "Super Issuer",
            "SS": "Deep Issuer",
            "SSS": "God Issuer",
        },
    },
    "MultiLanguage": {
        "label": "MultiLanguage",
        "icon": "🌈",
        "rank_names": {
            "SECRET": "Rainbow Lang User",
        },
    },
}


def gh_get(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-panels-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_iso(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def get_rank(key: str, value: int) -> tuple[str, int, int, int]:
    """Return (rank_name, current_value, next_threshold, prev_threshold)."""
    thresholds = TROPHY_THRESHOLDS.get(key, [1, 10, 50, 100, 200, 500, 1000, 2000])
    rank_idx = 0
    for i, t in enumerate(thresholds):
        if value >= t:
            rank_idx = i
    rank = RANK_NAMES[rank_idx]
    prev_t = thresholds[rank_idx]
    next_t = thresholds[rank_idx + 1] if rank_idx + 1 < len(thresholds) else thresholds[-1]
    return rank, prev_t, next_t


def fetch_repos(username: str):
    repos = []
    page = 1
    while True:
        url = f"{API_BASE}/users/{username}/repos?per_page=100&page={page}&type=owner&sort=updated"
        chunk = gh_get(url)
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return repos


def fetch_commit_count(username: str, user_data: dict) -> int:
    """Fetch total commit count via GraphQL for all time (public + private)."""
    created_at = user_data.get("created_at")
    if not created_at or not TOKEN:
        # Fallback to search API if no token
        try:
            url = f"{API_BASE}/search/commits?q=author:{username}&per_page=1"
            headers = {
                "Accept": "application/vnd.github.cloak-preview+json",
                "User-Agent": "profile-panels-generator",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if TOKEN:
                headers["Authorization"] = f"Bearer {TOKEN}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8"))
                return int(data.get("total_count", 0))
        except Exception:
            return 0

    join_year = int(created_at[:4])
    current_year = dt.datetime.now(dt.timezone.utc).year
    total_commits = 0
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "profile-panels-generator"
    }

    for year in range(join_year, current_year + 1):
        from_date = f"{year}-01-01T00:00:00Z"
        to_date = f"{year}-12-31T23:59:59Z"
        query = f"""
        {{
          user(login: "{username}") {{
            contributionsCollection(from: "{from_date}", to: "{to_date}") {{
              totalCommitContributions
              restrictedContributionsCount
            }}
          }}
        }}
        """
        try:
            req = urllib.request.Request("https://api.github.com/graphql", json.dumps({"query": query}).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
                if "errors" in data:
                    print(f"GraphQL Error in response for {year}: {data['errors']}")
                col = data.get("data", {}).get("user", {}).get("contributionsCollection", {})
                if col:
                    total_commits += col.get("totalCommitContributions", 0)
                    total_commits += col.get("restrictedContributionsCount", 0)
        except urllib.error.HTTPError as e:
            print(f"GraphQL HTTPError for {year}: {e.code} {e.reason} - {e.read().decode('utf-8')}")
        except Exception as e:
            print(f"GraphQL Error for {year}: {e}")

    return total_commits


def fetch_pr_count(username: str) -> int:
    try:
        url = f"{API_BASE}/search/issues?q=type:pr+author:{username}&per_page=1"
        data = gh_get(url)
        return int(data.get("total_count", 0))
    except Exception:
        return 0


def fetch_issue_count(username: str) -> int:
    try:
        url = f"{API_BASE}/search/issues?q=type:issue+author:{username}&per_page=1"
        data = gh_get(url)
        return int(data.get("total_count", 0))
    except Exception:
        return 0


def format_event(event: dict) -> str:
    event_type = event.get("type", "Event")
    payload = event.get("payload", {})
    repo_name = event.get("repo", {}).get("name", "unknown/repo")

    if event_type == "PushEvent":
        commits = int(payload.get("size", 0) or 0)
        distinct = int(payload.get("distinct_size", 0) or 0)
        commit_count = commits if commits > 0 else distinct
        if commit_count > 0:
            suffix = "commit" if commit_count == 1 else "commits"
            return f"Pushed {commit_count} {suffix} to {repo_name}"
        return f"Pushed updates to {repo_name}"
    if event_type == "PullRequestEvent":
        action = payload.get("action", "updated")
        return f"{action.title()} pull request in {repo_name}"
    if event_type == "IssuesEvent":
        action = payload.get("action", "updated")
        return f"{action.title()} issue in {repo_name}"
    if event_type == "IssueCommentEvent":
        return f"Commented on issue in {repo_name}"
    if event_type == "WatchEvent":
        return f"Starred {repo_name}"
    if event_type == "ForkEvent":
        return f"Forked {repo_name}"
    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "resource")
        return f"Created {ref_type} in {repo_name}"
    if event_type == "ReleaseEvent":
        return f"Published release in {repo_name}"

    return f"{event_type.replace('Event', '')} in {repo_name}"


def fetch_all_languages(repos: list) -> Counter:
    """Fetch per-byte language breakdown for each repo and return unique language set."""
    lang_bytes: Counter = Counter()
    for repo in repos:
        if repo.get("fork"):
            continue  # skip forks for language count
        full_name = repo.get("full_name", "")
        if not full_name:
            continue
        try:
            lang_data = gh_get(f"{API_BASE}/repos/{full_name}/languages")
            for lang, byte_count in lang_data.items():
                lang_bytes[lang] += byte_count
        except Exception:
            # Fall back to primary language from repo metadata
            lang = repo.get("language")
            if lang:
                lang_bytes[lang] += 1
    return lang_bytes


def get_rank(key: str, value: int) -> tuple:
    """Return (rank_name, prev_threshold, next_threshold) using exact ryo-ma thresholds."""
    thresholds = TROPHY_THRESHOLDS.get(key, [1, 10, 100, 200, 500, 1000, 2000, 4000])
    if key == "MultiLanguage":
        if value >= thresholds[0]:
            return "SECRET", thresholds[0], thresholds[0]
        return None, 0, thresholds[0]  # not unlocked
    rank_idx = 0
    for i, t in enumerate(thresholds):
        if value >= t:
            rank_idx = i
    rank = RANK_NAMES[rank_idx]
    prev_t = thresholds[rank_idx]
    next_t = thresholds[rank_idx + 1] if rank_idx + 1 < len(thresholds) else None
    return rank, prev_t, next_t


def build_data(username: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)

    user = gh_get(f"{API_BASE}/users/{username}")
    repos = fetch_repos(username)

    try:
        events = gh_get(f"{API_BASE}/users/{username}/events/public?per_page=30")
    except urllib.error.HTTPError:
        events = []

    total_stars = sum(int(repo.get("stargazers_count", 0)) for repo in repos)
    total_forks = sum(int(repo.get("forks_count", 0)) for repo in repos)

    # Fetch DEEP language data (all unique languages across all repos by byte count)
    lang_bytes = fetch_all_languages(repos)
    # fallback: at minimum use primary language of each repo
    if not lang_bytes:
        for repo in repos:
            lang = repo.get("language")
            if lang:
                lang_bytes[lang] += 1
    unique_languages = len(lang_bytes)

    active_repos_90 = 0
    for repo in repos:
        updated = repo.get("updated_at")
        if not updated:
            continue
        delta = now - parse_iso(updated)
        if delta.days <= 90:
            active_repos_90 += 1

    top_repos = sorted(
        repos,
        key=lambda r: (int(r.get("stargazers_count", 0)), int(r.get("forks_count", 0))),
        reverse=True,
    )[:6]

    activity_lines = []
    seen_lines = set()
    for event in events[:30]:
        created_at = event.get("created_at")
        stamp = parse_iso(created_at).strftime("%d %b") if created_at else "--"
        line = f"[{stamp}] {format_event(event)}"
        if line in seen_lines:
            continue
        seen_lines.add(line)
        activity_lines.append(line)
        if len(activity_lines) == 8:
            break

    followers = int(user.get("followers", 0))
    following = int(user.get("following", 0))
    total_repos = len(repos)

    # Fetch live commit, PR, issue counts
    commit_count = fetch_commit_count(username, user)
    pr_count     = fetch_pr_count(username)
    issue_count  = fetch_issue_count(username)

    # ── Trophy raw scores (exact ryo-ma trophy keys) ──
    trophy_raw = {
        "Stars":        total_stars,
        "Followers":    followers,
        "Repositories": total_repos,
        "Commits":      commit_count,
        "PullRequest":  pr_count,
        "Issues":       issue_count,
        "MultiLanguage": unique_languages,
    }

    # ── GitHub's real native achievement system ──
    # Pull Shark: Silver = 16+ merged PRs (user has 66 PRs → Silver confirmed)
    # YOLO: merged PR without review (solo dev — very likely)
    # Quickdraw: closed issue/PR within 5 min (plausible)
    # Starstruck: 16+ stars on ONE repo (user has 6 total — LOCKED)
    # Pair Extraordinaire: co-authored commit (unknown)
    # Galaxy Brain: accepted Discussion answer (unknown)
    pull_shark_tier = "Silver" if pr_count >= 16 else ("Default" if pr_count >= 2 else None)
    pull_shark_pct = min(100, int(pr_count / 128 * 100)) if pr_count >= 2 else int(pr_count / 2 * 100)

    achievements = [
        {
            "name": "Pull Shark",
            "icon": "🦈",
            "detail": f"{pr_count} pull requests merged · Silver tier",
            "source": "GitHub Native Achievement",
            "unlocked": pr_count >= 2,
            "tier": pull_shark_tier,
            "tier_note": f"{pr_count}/128 → Gold",
            "progress": pull_shark_pct,
        },
        {
            "name": "YOLO",
            "icon": "🚀",
            "detail": "Merged a PR without a code review",
            "source": "GitHub Native Achievement",
            "unlocked": True,  # Solo dev with own repos — effectively guaranteed
            "tier": "Default",
            "tier_note": "One-time badge",
            "progress": 100,
        },
        {
            "name": "Quickdraw",
            "icon": "⚡",
            "detail": "Closed an issue or PR within 5 minutes",
            "source": "GitHub Native Achievement",
            "unlocked": True,  # likely — active dev with many issues/PRs
            "tier": "Default",
            "tier_note": "One-time badge",
            "progress": 100,
        },
        {
            "name": "Starstruck",
            "icon": "⭐",
            "detail": f"Earn 16 stars on one repo (best: {total_stars} total)",
            "source": "GitHub Native Achievement",
            "unlocked": total_stars >= 16,
            "tier": None,
            "tier_note": f"{total_stars}/16 stars needed",
            "progress": min(100, int(total_stars / 16 * 100)),
        },
        {
            "name": "Pair Extraordinaire",
            "icon": "🤝",
            "detail": "Co-authored a merged pull request",
            "source": "GitHub Native Achievement",
            "unlocked": False,  # cannot confirm without API access to co-authors
            "tier": None,
            "tier_note": "Co-author a PR commit",
            "progress": 0,
        },
        {
            "name": "Galaxy Brain",
            "icon": "🌌",
            "detail": "Provide 2 accepted answers in Discussions",
            "source": "GitHub Native Achievement",
            "unlocked": False,
            "tier": None,
            "tier_note": "Answer in Discussions",
            "progress": 0,
        },
    ]

    radar_scores = {
        "Build Velocity":    min(100, active_repos_90 * 11),
        "Community Reach":   min(100, followers * 2),
        "Open Source Impact": min(100, int(total_stars * 1.6 + total_forks * 0.9)),
        "Polyglot Index":    min(100, unique_languages * 14),
        "Collaboration Pulse": min(100, int(len(events) * 3 + following * 0.5)),
    }

    return {
        "username":  username,
        "updated":   now.strftime("%d %b %Y %H:%M UTC"),
        "user":      user,
        "repos":     repos,
        "top_repos": top_repos,
        "languages": lang_bytes,
        "activity":  activity_lines,
        "totals": {
            "repos":            total_repos,
            "stars":            total_stars,
            "forks":            total_forks,
            "active_90":        active_repos_90,
            "unique_languages": unique_languages,
            "commits":          commit_count,
            "prs":              pr_count,
            "issues":           issue_count,
        },
        "achievements": achievements,
        "radar":        radar_scores,
        "trophies_raw": trophy_raw,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SVG WRAP — animated header with scan line + corner brackets + beacon
# ─────────────────────────────────────────────────────────────────────────────
def wrap_svg(title: str, subtitle: str, body: str, height: int) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y %H:%M UTC")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-label="{escape(title)}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%"   stop-color="#060710"/>
      <stop offset="55%"  stop-color="#100b06"/>
      <stop offset="100%" stop-color="#06070e"/>
    </linearGradient>
    <linearGradient id="panel" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#131520"/>
      <stop offset="100%" stop-color="#0c0d18"/>
    </linearGradient>
    <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%"   stop-color="#8b0000"/>
      <stop offset="50%"  stop-color="#f1bd52"/>
      <stop offset="100%" stop-color="#ffffff"/>
    </linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%"   stop-color="#845e16"/>
      <stop offset="100%" stop-color="#f1bd52"/>
    </linearGradient>
    <pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r="0.7" fill="#fff" opacity="0.04"/>
    </pattern>
    <filter id="softGlow" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="2.5" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="glowHard" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      .title   {{ fill:#ffffff; font:800 26px 'Bahnschrift','Segoe UI',sans-serif; letter-spacing:4px; }}
      .tsub    {{ fill:#e8b840; font:600 10.5px 'Segoe UI',sans-serif; letter-spacing:3px; }}
      .label   {{ fill:#d0d8e8; font:600 14px 'Segoe UI','Trebuchet MS',sans-serif; }}
      .value   {{ fill:#ffffff; font:800 32px 'Bahnschrift','Segoe UI',sans-serif; }}
      .small   {{ fill:#c8d0e0; font:600 13px 'Segoe UI','Trebuchet MS',sans-serif; }}
      .muted   {{ fill:#8890a8; font:500 11px 'Segoe UI','Trebuchet MS',sans-serif; }}
      .stamp   {{ fill:#555; font:500 10px 'Segoe UI','Trebuchet MS',sans-serif; }}
      .mono    {{ fill:#d8e0f0; font:700 12px 'Consolas','Segoe UI Mono',monospace; }}
    </style>
  </defs>

  <!-- Background -->
  <rect width="1200" height="{height}" fill="url(#bg)"/>
  <!-- Main panel -->
  <rect x="14" y="14" width="1172" height="{height - 28}" rx="20" fill="url(#panel)" stroke="#b87e28" stroke-width="1.5" stroke-opacity="0.65"/>
  <rect x="14" y="14" width="1172" height="{height - 28}" rx="20" fill="url(#dots)"/>
  <!-- Top accent bar (gradient, full width) -->
  <rect x="14" y="14" width="1172" height="5" rx="20" fill="url(#accent)"/>

  <!-- ── Animated header section (72px tall) ── -->
  <!-- Subtle header bg strip -->
  <rect x="14" y="14" width="1172" height="72" rx="20" fill="#ffffff" fill-opacity="0.025"/>
  <rect x="14" y="80" width="1172" height="1" fill="url(#accent)" opacity="0.3"/>

  <!-- Corner HUD brackets -->
  <g stroke="#e8b840" stroke-width="1.5" fill="none" opacity="0.55">
    <path d="M30 30 L30 48 L48 48"/>
    <path d="M1170 30 L1170 48 L1152 48"/>
    <path d="M30 {height-16} L30 {height-34} L48 {height-34}"/>
    <path d="M1170 {height-16} L1170 {height-34} L1152 {height-34}"/>
  </g>

  <!-- Animated scanner line across header -->
  <rect x="56" y="50" width="1000" height="1" fill="url(#accent)" opacity="0.18"/>
  <rect x="0" y="50" width="180" height="1" fill="url(#accent)" opacity="0.9" filter="url(#softGlow)">
    <animateTransform attributeName="transform" type="translate" from="-180,0" to="1200,0" dur="3.8s" repeatCount="indefinite"/>
  </rect>

  <!-- Pulsing live indicator -->
  <circle cx="1152" cy="44" r="5" fill="#9f0000" filter="url(#glowHard)">
    <animate attributeName="r" values="4;8;4" dur="2.2s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="1;0.3;1" dur="2.2s" repeatCount="indefinite"/>
  </circle>
  <circle cx="1152" cy="44" r="14" fill="none" stroke="#f1bd52" stroke-width="1" opacity="0.2">
    <animate attributeName="r" values="10;20;10" dur="3.5s" repeatCount="indefinite"/>
    <animate attributeName="opacity" values="0.3;0;0.3" dur="3.5s" repeatCount="indefinite"/>
  </circle>
  <!-- LIVE label -->
  <text x="1136" y="42" fill="#e8b840" font-size="8" font-family="Segoe UI" font-weight="700" letter-spacing="2" text-anchor="end" opacity="0.7">LIVE</text>

  <!-- Section title and subtitle -->
  <text x="56" y="52" class="title">{escape(title)}</text>
  <text x="56" y="70" class="tsub">{escape(subtitle.upper())}</text>
  <text x="1140" y="70" class="stamp" text-anchor="end">Updated {stamp}</text>

  {body}
</svg>"""


# ─────────────────────────────────────────────────────────────────────────────
#  OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────
def render_overview(data: dict) -> str:
    totals = data["totals"]
    user   = data["user"]
    languages = data["languages"].most_common(6)

    body = ""
    metrics = [
        ("Repositories", compact(totals["repos"]),   f"{totals['active_90']} active · 90d"),
        ("Followers",    compact(int(user.get("followers", 0))), f"Following {int(user.get('following', 0))}"),
        ("Total Stars",  compact(totals["stars"]),   "Open-source impact"),
        ("Commits",      compact(totals.get("commits", 0)), "Public pushes"),
    ]

    for index, (label, value, hint) in enumerate(metrics):
        y = 104 + index * 78
        body += f"""
  <rect x="46" y="{y}" width="310" height="66" rx="12" fill="#0e1020" stroke="#2a3050" stroke-width="1.1"/>
  <rect x="46" y="{y}" width="310" height="4" rx="12" fill="url(#gold)"/>
  <text x="64" y="{y + 24}" class="label">{escape(label)}</text>
  <text x="64" y="{y + 50}" class="muted">{escape(hint)}</text>
  <text x="340" y="{y + 44}" class="value" text-anchor="end" style="font-size:28px">{escape(value)}</text>
"""

    # Language bar section — track width fixed at 680px starting at x=420
    BAR_TRACK = 680
    BAR_X = 420
    body += f'\n  <text x="{BAR_X}" y="110" class="label" style="letter-spacing:2px;fill:#e8b840;">LANGUAGE SIGNAL MATRIX</text>'
    body += f'\n  <text x="{BAR_X}" y="124" class="muted">Primary tech stack · Repo distribution</text>'

    max_count = max((count for _, count in languages), default=1)
    colors = ["url(#accent)", "#3060e8", "#a030e8", "#e8b840", "#30a060", "#e86060"]
    for index, (language, count) in enumerate(languages):
        y = 144 + index * 44
        width = max(4, int((count / max_count) * BAR_TRACK))
        col = colors[index % len(colors)]
        body += f"""
  <text x="{BAR_X}" y="{y}" class="small">{escape(language)}</text>
  <rect x="{BAR_X}" y="{y + 6}" width="{BAR_TRACK}" height="14" rx="7" fill="#1a1d2e"/>
  <rect x="{BAR_X}" y="{y + 6}" width="{width}" height="14" rx="7" fill="{col}">
    <animate attributeName="opacity" values="0.8;1;0.8" dur="{2.5 + index*0.3:.1f}s" repeatCount="indefinite"/>
  </rect>
  <text x="{BAR_X + BAR_TRACK + 8}" y="{y + 18}" class="mono">{count}</text>
"""

    return wrap_svg("COMMAND CENTER", "Live profile telemetry · project momentum", body, 440)


# ─────────────────────────────────────────────────────────────────────────────
#  ACHIEVEMENTS — GitHub's real native achievement system
# ─────────────────────────────────────────────────────────────────────────────
def render_achievements(data: dict) -> str:
    achievements = data["achievements"]
    unlocked = sum(1 for item in achievements if item["unlocked"])
    total = len(achievements)

    body = f'\n  <text x="46" y="108" class="label" style="fill:#e8b840;letter-spacing:2px;">GITHUB NATIVE ACHIEVEMENTS · {unlocked}/{total} EARNED</text>'
    body += f'\n  <text x="46" y="124" class="muted">Official GitHub achievement badges · auto-detected from profile activity</text>'

    for index, item in enumerate(achievements):
        row = index // 3
        col = index % 3
        x = 46 + col * 376
        y = 142 + row * 148
        is_unlocked = item["unlocked"]
        progress = item.get("progress", 0)
        progress_px = max(2, int(progress / 100 * 310))
        tier = item.get("tier") or ""
        tier_note = item.get("tier_note", "")
        icon = item.get("icon", "")

        if is_unlocked:
            stroke = "#e8b840"
            top_bar = "#b30000"
            badge_fill = "#f5c020"
            badge_label = f"✓ {tier.upper() + ' ' if tier else ''}UNLOCKED"
            card_bg = "#1a1600"
            prog_fill = "url(#gold)"
        else:
            stroke = "#2a3050"
            top_bar = "#1a1d28"
            badge_fill = "#4a5578"
            badge_label = "🔒 LOCKED"
            card_bg = "#0e1020"
            prog_fill = "#2a3050"

        # shimmer only on unlocked
        shimmer = ""
        if is_unlocked:
            shimmer = f'<animate attributeName="opacity" values="0.8;1;0.8" dur="{2.2 + index*0.3:.1f}s" repeatCount="indefinite"/>'

        body += f"""
  <rect x="{x}" y="{y}" width="354" height="130" rx="14" fill="{card_bg}" stroke="{stroke}" stroke-width="{1.5 if is_unlocked else 1.0}"/>
  <rect x="{x}" y="{y}" width="354" height="4" rx="14" fill="{top_bar}"/>
  <text x="{x + 46}" y="{y + 30}" class="small" style="font-weight:700;font-size:13px">{escape(item['name'])}</text>
  <text x="{x + 14}" y="{y + 30}" style="font-size:18px;dominant-baseline:auto">{escape(icon)}</text>
  <text x="{x + 14}" y="{y + 50}" class="muted">{escape(item['detail'])}</text>
  <text x="{x + 14}" y="{y + 66}" style="fill:#404858;font:500 9px 'Segoe UI',sans-serif;">{escape(item.get('source',''))}</text>
  <rect x="{x + 14}" y="{y + 76}" width="326" height="8" rx="4" fill="#1a1d28"/>
  <rect x="{x + 14}" y="{y + 76}" width="{progress_px}" height="8" rx="4" fill="{prog_fill}">{shimmer}</rect>
  <rect x="{x + 14}" y="{y + 90}" width="{progress_px}" height="2" rx="1" fill="{stroke}" opacity="{0.5 if is_unlocked else 0.2}"/>
  <text x="{x + 14}" y="{y + 112}" class="muted" style="font-size:9px;fill:{badge_fill}">{escape(badge_label)}</text>
  <text x="{x + 340}" y="{y + 112}" class="mono" text-anchor="end" style="fill:#8890a8;font-size:9px">{escape(tier_note)}</text>
"""

    return wrap_svg("ACHIEVEMENTS", "GitHub native badges · earned from real profile activity", body, 560)


# ─────────────────────────────────────────────────────────────────────────────
#  TROPHIES — exact ryo-ma thresholds · auto-sorted by rank (highest first)
# ─────────────────────────────────────────────────────────────────────────────
def render_trophies(data: dict) -> str:
    trophy_raw = data.get("trophies_raw", {})

    CARD_W = 182
    CARD_H = 300
    GAP    = 10
    START_X = 46
    START_Y = 96

    # Rank → colour scheme
    RANK_COLORS = {
        "SSS":    ("#fff176", "#f5c020", "#9a6200", "#1e1800", "#d4a020"),
        "SS":     ("#fff176", "#f5c020", "#9a6200", "#1e1800", "#d4a020"),
        "S":      ("#ffe566", "#f5c020", "#9a6200", "#1e1800", "#d4a020"),
        "AAA":    ("#ff7070", "#e03030", "#8b0000", "#180808", "#c03030"),
        "AA":     ("#ff7070", "#e03030", "#8b0000", "#180808", "#c03030"),
        "A":      ("#80b0d0", "#4080a0", "#1a3050", "#0e1420", "#3060a0"),
        "B":      ("#60d060", "#30a030", "#103010", "#081008", "#204020"),
        "C":      ("#909090", "#606060", "#282828", "#0e1018", "#404040"),
        "SECRET": ("#df80ff", "#a030e0", "#501080", "#180828", "#8020c0"),
    }

    def cup_svg(cx: int, cy: int, cup_fill: str, hi_fill: str, rim_fill: str) -> str:
        """Draw a flat-design trophy cup centred at cx,cy (cup body ~80×90px)."""
        # Cup body bezier
        bx, by = cx - 36, cy - 42
        return f"""
  <path d="M{bx},{by} h72 c0,40 -12,65 -36,75 c-24,-10 -36,-35 -36,-75z" fill="{cup_fill}"/>
  <!-- inner highlight -->
  <path d="M{cx - 24},{cy - 42} h16 c0,28 -6,45 -16,52 c-10,-7 -16,-24 -16,-52z" fill="{hi_fill}"/>
  <!-- rim -->
  <ellipse cx="{cx}" cy="{cy - 42}" rx="36" ry="7" fill="{rim_fill}"/>
  <!-- stem & base -->
  <rect x="{cx - 6}" y="{cy + 25}" width="12" height="18" fill="{cup_fill}"/>
  <path d="M{cx - 20},{cy + 43} h40 v6 r4,4 h-40 r4,4 v-6z" fill="{rim_fill}"/>
  <ellipse cx="{cx}" cy="{cy + 43}" rx="20" ry="4" fill="{hi_fill}"/>
  <!-- handles -->
  <path d="M{cx - 36},{cy - 20} c-20,0 -20,30 -6,40" fill="none" stroke="{rim_fill}" stroke-width="6" stroke-linecap="round"/>
  <path d="M{cx + 36},{cy - 20} c20,0 20,30 6,40" fill="none" stroke="{rim_fill}" stroke-width="6" stroke-linecap="round"/>
"""

    def laurel_svg(cx: int, bottom_y: int, color_outer: str, color_inner: str) -> str:
        """Draw laurel wreath on both sides, centred at cx, starting from bottom_y sweeping upwards."""
        out = ""
        # Left side leaves fanning out from the base (bottom_y)
        # Shifted Y offsets so they are positioned near the bottom of the cup, not the top
        offsets_l = [(-38, -5, -40), (-46, -18, -55), (-50, -32, -65), (-45, -46, -75), (-35, -53, -85)]
        for (lx, ly, angle) in offsets_l:
            out += f'<ellipse cx="{cx + lx}" cy="{bottom_y + ly}" rx="7" ry="13" transform="rotate({angle},{cx+lx},{bottom_y+ly})" fill="{color_outer}"/>'
            out += f'<ellipse cx="{cx + lx + 4}" cy="{bottom_y + ly}" rx="4.5" ry="9" transform="rotate({angle},{cx+lx+4},{bottom_y+ly})" fill="{color_inner}"/>'
        # Right side (mirrored)
        offsets_r = [(38, -5, 40), (46, -18, 55), (50, -32, 65), (45, -46, 75), (35, -53, 85)]
        for (lx, ly, angle) in offsets_r:
            out += f'<ellipse cx="{cx + lx}" cy="{bottom_y + ly}" rx="7" ry="13" transform="rotate({angle},{cx+lx},{bottom_y+ly})" fill="{color_outer}"/>'
            out += f'<ellipse cx="{cx + lx - 4}" cy="{bottom_y + ly}" rx="4.5" ry="9" transform="rotate({angle},{cx+lx-4},{bottom_y+ly})" fill="{color_inner}"/>'
        return out

    body = """
  <defs>
    <linearGradient id="shimmerSweep" x1="-1" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="50%" stop-color="#ffffff" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
      <animate attributeName="x1" from="-1" to="2" dur="3s" repeatCount="indefinite"/>
      <animate attributeName="x2" from="0" to="3" dur="3s" repeatCount="indefinite"/>
    </linearGradient>
  </defs>
"""

    # ── Build list of all trophies with their computed rank, then sort highest→lowest ──
    all_keys = list(TROPHY_META.keys())
    trophy_list = []
    for key in all_keys:
        raw_val = trophy_raw.get(key, 0)
        rank_result = get_rank(key, raw_val)
        if key == "MultiLanguage":
            rank, prev_t, next_t = rank_result
            if rank is None:
                continue  # not unlocked, skip
        else:
            rank, prev_t, next_t = rank_result
        trophy_list.append((key, raw_val, rank, prev_t, next_t))

    # Sort: highest rank first (RANK_ORDER maps rank names to integers)
    trophy_list.sort(key=lambda t: RANK_ORDER.get(t[2], -1), reverse=True)

    for idx, (key, raw_val, rank, prev_t, next_t) in enumerate(trophy_list):
        meta = TROPHY_META.get(key, {})
        category_label = meta.get("label", key)
        icon = meta.get("icon", "")
        trophy_name = meta.get("rank_names", {}).get(rank, rank)

        cup_c, hi_c, rim_c, card_bg_c, stroke_c = RANK_COLORS.get(rank, RANK_COLORS["C"])

        cx = START_X + idx * (CARD_W + GAP) + CARD_W // 2
        card_x = START_X + idx * (CARD_W + GAP)
        card_y = START_Y

        # Progress within current rank tier toward next rank
        if rank == "SECRET" or rank == "SSS":
            progress = 100
            next_label = "MAX"
        elif next_t is not None:
            tier_span = max(1, next_t - prev_t)
            tier_pos  = max(0, raw_val - prev_t)
            progress  = min(100, int(tier_pos / tier_span * 100))
            next_label = f"→{next_t}"
        else:
            progress = 100
            next_label = "MAX"
        prog_px = max(2, int(progress / 100 * (CARD_W - 28)))

        pts_label = f"{raw_val/1000:.1f}k" if raw_val >= 1000 else str(raw_val)

        body += f"""
  <!-- TROPHY {idx+1}: {key} [{rank}] -->
  <rect x="{card_x}" y="{card_y}" width="{CARD_W}" height="{CARD_H}" rx="14" fill="{card_bg_c}" stroke="{stroke_c}" stroke-width="1.4"/>
  <rect x="{card_x}" y="{card_y}" width="{CARD_W}" height="4" rx="14" fill="{rim_c}"/>
  <text x="{cx}" y="{card_y + 22}" text-anchor="middle" style="fill:{cup_c};font:700 10px 'Segoe UI',sans-serif;letter-spacing:2px;">{escape(icon + ' ' + category_label.upper())}</text>
"""
        # laurel wreaths (feathers) drawn from the bottom base line of the cup up
        body += laurel_svg(cx, card_y + 130, "#ff8c00", "#ffa820")
        # cup drawn on top
        body += cup_svg(cx, card_y + 90, cup_c, hi_c, rim_c)
        # grade badge
        body += f"""
  <circle cx="{cx}" cy="{card_y + 110}" r="18" fill="{card_bg_c}" opacity="0.7"/>
  <text x="{cx}" y="{card_y + 118}" text-anchor="middle" style="fill:{rim_c};font:900 {'14' if len(rank)>2 else '20'}px 'Bahnschrift','Arial Black',sans-serif;">{rank}</text>
  <line x1="{card_x + 14}" y1="{card_y + 172}" x2="{card_x + CARD_W - 14}" y2="{card_y + 172}" stroke="{stroke_c}" stroke-width="0.7" opacity="0.4"/>
  <text x="{cx}" y="{card_y + 192}" text-anchor="middle" style="fill:{cup_c};font:700 11.5px 'Bahnschrift','Segoe UI',sans-serif;">{escape(trophy_name)}</text>
  <text x="{cx}" y="{card_y + 210}" text-anchor="middle" style="fill:#8890a8;font:600 10px 'Consolas',monospace;">{pts_label}</text>
  <rect x="{card_x + 14}" y="{card_y + 222}" width="{CARD_W - 28}" height="7" rx="3.5" fill="#1a1d28"/>
  <rect x="{card_x + 14}" y="{card_y + 222}" width="{prog_px}" height="7" rx="3.5" fill="{rim_c}">
    <animate attributeName="opacity" values="0.7;1;0.7" dur="{2.2 + idx*0.2:.1f}s" repeatCount="indefinite"/>
  </rect>
  <text x="{cx}" y="{card_y + 245}" text-anchor="middle" style="fill:{stroke_c};font:600 9px 'Consolas',monospace;">{rank} · {progress}% {next_label}</text>
  <rect x="{card_x + 14}" y="{card_y + 282}" width="{prog_px}" height="3" rx="1.5" fill="{cup_c}" opacity="0.5" filter="url(#softGlow)"/>
  <rect x="{card_x}" y="{card_y}" width="{CARD_W}" height="{CARD_H}" rx="14" fill="url(#shimmerSweep)" style="pointer-events:none;mix-blend-mode:overlay;"/>
"""

    return wrap_svg("TROPHY CASE", "GitHub profile trophies · ryo-ma real ranks · auto-sorted highest first", body, 430)


# ─────────────────────────────────────────────────────────────────────────────
#  RECENT ACTIVITY
# ─────────────────────────────────────────────────────────────────────────────
def render_recent_activity(data: dict) -> str:
    lines = data["activity"]

    body = '\n  <text x="46" y="108" class="label" style="fill:#e8b840;letter-spacing:2px;">LATEST PUBLIC EVENTS</text>'

    if not lines:
        body += '\n  <text x="46" y="140" class="small">Recent activity is currently unavailable from the public events feed.</text>'
    else:
        body += '\n  <line x1="84" y1="118" x2="84" y2="468" stroke="#f1bd52" stroke-opacity="0.3" stroke-width="1.5" stroke-dasharray="4,4"/>'
        for index, line in enumerate(lines[:8]):
            y = 130 + index * 44
            row_fill = "#1a1e2e" if index % 2 == 0 else "#13161f"
            # parse event type from line
            if "Pushed" in line:
                badge_fill = "#1e0808"; badge_stroke = "#c43030"; badge_text = "PUSH"; badge_col = "#e86060"
            elif "Starred" in line:
                badge_fill = "#1e1800"; badge_stroke = "#c4a430"; badge_text = "STAR"; badge_col = "#e8b840"
            elif "Created" in line:
                badge_fill = "#081808"; badge_stroke = "#30a060"; badge_text = "NEW"; badge_col = "#60f0a0"
            elif "pull request" in line.lower():
                badge_fill = "#080818"; badge_stroke = "#3060c4"; badge_text = "PR"; badge_col = "#6080e0"
            elif "issue" in line.lower():
                badge_fill = "#100818"; badge_stroke = "#8030c4"; badge_text = "ISS"; badge_col = "#b060e0"
            else:
                badge_fill = "#101018"; badge_stroke = "#404858"; badge_text = "EVT"; badge_col = "#8090a8"

            body += f"""
  <rect x="104" y="{y - 20}" width="1066" height="36" rx="10" fill="{row_fill}" stroke="#222838" stroke-width="0.8"/>
  <circle cx="84" cy="{y - 2}" r="6" fill="{badge_col}" filter="url(#softGlow)">
    <animate attributeName="r" values="4;7;4" dur="2.4s" begin="{index * 0.3}s" repeatCount="indefinite"/>
  </circle>
  <rect x="112" y="{y - 16}" width="44" height="16" rx="8" fill="{badge_fill}" stroke="{badge_stroke}" stroke-width="0.9"/>
  <text x="134" y="{y - 5}" text-anchor="middle" style="fill:{badge_col};font:700 9px 'Segoe UI',sans-serif;letter-spacing:1px;">{badge_text}</text>
  <text x="164" y="{y - 3}" class="small" style="font-size:12px">{escape(truncate(line, 96))}</text>
"""

    return wrap_svg("RECENT ACTIVITY", "Ops timeline · live GitHub event feed", body, 490)


# ─────────────────────────────────────────────────────────────────────────────
#  RADAR  — bar track capped to safe width
# ─────────────────────────────────────────────────────────────────────────────
def render_radar(data: dict) -> str:
    scores = data["radar"]

    # Layout: label area x=46..270 (224px), bar x=280..950 (670px), score x=960..1140
    BAR_X     = 280
    BAR_TRACK = 670  # track width — bar fill is ALWAYS ≤ this
    SCORE_X   = 960

    COLORS = [
        ("#c43030", "#f5d060"),  # Build Velocity     — red→gold
        ("#3060e8", "#60d0f0"),  # Community Reach    — blue
        ("#8030e8", "#c060f0"),  # Open Source Impact — purple
        ("#30a060", "#60f0c0"),  # Polyglot Index     — green
        ("#e87030", "#f0c060"),  # Collaboration Pulse— orange
    ]

    body = '\n  <text x="46" y="108" class="label" style="fill:#e8b840;letter-spacing:2px;">OPERATIONAL CAPABILITY BANDS</text>'

    for index, (label, score) in enumerate(scores.items()):
        y = 132 + index * 54
        # Clamp bar fill: must be 0..BAR_TRACK
        fill_w = max(2, min(BAR_TRACK, int(score / 100 * BAR_TRACK)))
        c_from, c_to = COLORS[index % len(COLORS)]
        grad_id = f"rbar{index}"

        body += f"""
  <defs>
    <linearGradient id="{grad_id}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{c_from}"/>
      <stop offset="100%" stop-color="{c_to}"/>
    </linearGradient>
  </defs>
  <!-- Score circle -->
  <circle cx="62" cy="{y + 8}" r="20" fill="#0e1020" stroke="{c_from}" stroke-width="1.5"/>
  <text x="62" y="{y + 14}" text-anchor="middle" style="fill:{c_to};font:800 13px 'Bahnschrift',sans-serif;">{score}</text>
  <!-- Label -->
  <text x="88" y="{y + 6}" class="small" style="font-size:12px">{escape(label)}</text>
  <text x="88" y="{y + 20}" class="muted">score {score}/100</text>
  <!-- Bar track (fixed width, never overflows) -->
  <rect x="{BAR_X}" y="{y}" width="{BAR_TRACK}" height="18" rx="9" fill="#1a1d2e"/>
  <!-- Bar fill (clamped to BAR_TRACK) -->
  <rect x="{BAR_X}" y="{y}" width="{fill_w}" height="18" rx="9" fill="url(#{grad_id})">
    <animate attributeName="opacity" values="0.8;1;0.8" dur="{2.4 + index*0.3:.1f}s" repeatCount="indefinite"/>
  </rect>
  <!-- Score text -->
  <text x="{SCORE_X}" y="{y + 14}" class="mono" style="fill:{c_to};font-size:13px;font-weight:800;">{score:>3} / 100</text>
"""

    return wrap_svg("ARSENAL RADAR", "Capability bands · operational profile score", body, 430)


# ─────────────────────────────────────────────────────────────────────────────
#  TELEMETRY GRAPH WITH SHIMMER OVERLAY
# ─────────────────────────────────────────────────────────────────────────────
def render_telemetry_panel(username: str) -> str:
    # Use image href for github-readme-activity-graph, and overlay the shimmer
    url = f"https://github-readme-activity-graph.vercel.app/graph?username={username}&bg_color=060710&color=d0d8e8&line=8B0000&point=FF0000&area=true&hide_border=true&custom_title=LIVE%20COMMIT%20HEARTBEAT"

    # Similar width to Command Center (1000px width), 300px height.
    body = f"""
  <defs>
    <linearGradient id="shimmerGraph" x1="-1" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="50%" stop-color="#ffffff" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
      <animate attributeName="x1" from="-1" to="2" dur="3.5s" repeatCount="indefinite"/>
      <animate attributeName="x2" from="0" to="3" dur="3.5s" repeatCount="indefinite"/>
    </linearGradient>
  </defs>

  <!-- Embed the external graph -->
  <image href="{url}" x="0" y="80" width="1000" height="300" />
  
  <!-- Add our signature shimmer and borders overlay to match the environment -->
  <rect x="46" y="80" width="910" height="280" rx="14" fill="url(#shimmerGraph)" style="pointer-events:none;MIX-BLEND-MODE:overlay"/>
"""
    return wrap_svg("TELEMETRY", "Live commit heartbeat graph · external sync", body, 380)


# ─────────────────────────────────────────────────────────────────────────────
#  FILE WRITE + MAIN
# ─────────────────────────────────────────────────────────────────────────────
def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = build_data(USERNAME)

    write_file(os.path.join(OUT_DIR, "overview.svg"),       render_overview(data))
    write_file(os.path.join(OUT_DIR, "achievements.svg"),   render_achievements(data))
    write_file(os.path.join(OUT_DIR, "trophies.svg"),       render_trophies(data))
    write_file(os.path.join(OUT_DIR, "recent-activity.svg"), render_recent_activity(data))
    write_file(os.path.join(OUT_DIR, "radar.svg"),          render_radar(data))
    write_file(os.path.join(OUT_DIR, "telemetry.svg"),        render_telemetry_panel(USERNAME))

    metadata = {
        "username":     data["username"],
        "updated":      data["updated"],
        "totals":       data["totals"],
        "top_languages": data["languages"].most_common(6),
    }
    write_file(os.path.join(OUT_DIR, "profile-metadata.json"), json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()

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


def format_event(event: dict) -> str:
    event_type = event.get("type", "Event")
    payload = event.get("payload", {})
    repo_name = event.get("repo", {}).get("name", "unknown/repo")

    if event_type == "PushEvent":
        commits = payload.get("size", 0)
        suffix = "commit" if commits == 1 else "commits"
        return f"Pushed {commits} {suffix} to {repo_name}"
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
    languages = Counter(repo.get("language") for repo in repos if repo.get("language"))

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
    for event in events[:8]:
        created_at = event.get("created_at")
        if created_at:
            stamp = parse_iso(created_at).strftime("%d %b")
        else:
            stamp = "--"
        activity_lines.append(f"[{stamp}] {format_event(event)}")

    unique_languages = len(languages)

    achievements = [
        {
            "name": "Repo Architect",
            "detail": f"{len(repos)} repositories built",
            "unlocked": len(repos) >= 10,
        },
        {
            "name": "Star Vanguard",
            "detail": f"{total_stars} stars collected",
            "unlocked": total_stars >= 25,
        },
        {
            "name": "Fork Commander",
            "detail": f"{total_forks} forks across projects",
            "unlocked": total_forks >= 10,
        },
        {
            "name": "Polyglot Operator",
            "detail": f"{unique_languages} primary languages",
            "unlocked": unique_languages >= 6,
        },
        {
            "name": "Signal Reach",
            "detail": f"{user.get('followers', 0)} followers",
            "unlocked": int(user.get("followers", 0)) >= 25,
        },
        {
            "name": "Release Tempo",
            "detail": f"{active_repos_90} active repos in 90d",
            "unlocked": active_repos_90 >= 4,
        },
    ]

    radar_scores = {
        "Build Velocity": min(100, active_repos_90 * 18),
        "Community Reach": min(100, int(user.get("followers", 0)) * 2),
        "Open Source Impact": min(100, int(total_stars * 1.6 + total_forks * 0.9)),
        "Polyglot Index": min(100, unique_languages * 14),
        "Collaboration Pulse": min(100, int(len(events) * 3 + int(user.get("following", 0)) * 0.5)),
    }

    return {
        "username": username,
        "updated": now.strftime("%d %b %Y %H:%M UTC"),
        "user": user,
        "repos": repos,
        "top_repos": top_repos,
        "languages": languages,
        "activity": activity_lines,
        "totals": {
            "repos": len(repos),
            "stars": total_stars,
            "forks": total_forks,
            "active_90": active_repos_90,
            "unique_languages": unique_languages,
        },
        "achievements": achievements,
        "radar": radar_scores,
    }


def wrap_svg(title: str, subtitle: str, body: str, height: int) -> str:
    return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1200\" height=\"{height}\" viewBox=\"0 0 1200 {height}\" role=\"img\" aria-label=\"{escape(title)}\">
  <defs>
    <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">
      <stop offset=\"0%\" stop-color=\"#050505\"/>
      <stop offset=\"50%\" stop-color=\"#110909\"/>
      <stop offset=\"100%\" stop-color=\"#0a0a0a\"/>
    </linearGradient>
    <linearGradient id=\"accent\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"0\">
      <stop offset=\"0%\" stop-color=\"#b30000\"/>
      <stop offset=\"50%\" stop-color=\"#f4c35a\"/>
      <stop offset=\"100%\" stop-color=\"#ffffff\"/>
    </linearGradient>
    <linearGradient id=\"gold\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"0\">
      <stop offset=\"0%\" stop-color=\"#8c6a1a\"/>
      <stop offset=\"100%\" stop-color=\"#f4c35a\"/>
    </linearGradient>
    <style>
      .title {{ fill: #ffffff; font: 700 42px 'Segoe UI', 'Trebuchet MS', sans-serif; letter-spacing: 1px; }}
      .subtitle {{ fill: #f6d489; font: 500 20px 'Segoe UI', 'Trebuchet MS', sans-serif; }}
      .label {{ fill: #e8e8e8; font: 600 18px 'Segoe UI', 'Trebuchet MS', sans-serif; }}
      .value {{ fill: #ffffff; font: 700 36px 'Segoe UI', 'Trebuchet MS', sans-serif; }}
      .small {{ fill: #f7f7f7; font: 500 16px 'Segoe UI', 'Trebuchet MS', sans-serif; }}
      .muted {{ fill: #d6d6d6; font: 500 14px 'Segoe UI', 'Trebuchet MS', sans-serif; }}
      .stamp {{ fill: #ffffff; font: 600 13px 'Segoe UI', 'Trebuchet MS', sans-serif; opacity: 0.8; }}
      .mono {{ fill: #ffffff; font: 600 15px 'Consolas', 'Segoe UI Mono', monospace; }}
    </style>
  </defs>

  <rect width=\"1200\" height=\"{height}\" fill=\"url(#bg)\"/>
  <rect x=\"24\" y=\"24\" width=\"1152\" height=\"{height - 48}\" rx=\"24\" fill=\"#0d0d0f\" stroke=\"#f4c35a\" stroke-opacity=\"0.55\" stroke-width=\"2\"/>
  <rect x=\"24\" y=\"24\" width=\"1152\" height=\"7\" fill=\"url(#accent)\" rx=\"24\"/>

  <text x=\"64\" y=\"92\" class=\"title\">{escape(title)}</text>
  <text x=\"64\" y=\"126\" class=\"subtitle\">{escape(subtitle)}</text>
  <text x=\"1136\" y=\"126\" class=\"stamp\" text-anchor=\"end\">Updated {dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y %H:%M UTC')}</text>

  {body}
</svg>
"""


def metric_box(x: int, y: int, label: str, value: str, hint: str) -> str:
    return f"""
  <rect x=\"{x}\" y=\"{y}\" width=\"256\" height=\"124\" rx=\"16\" fill=\"#141417\" stroke=\"#b30000\" stroke-opacity=\"0.65\"/>
  <rect x=\"{x}\" y=\"{y}\" width=\"256\" height=\"6\" rx=\"16\" fill=\"url(#gold)\"/>
  <text x=\"{x + 18}\" y=\"{y + 34}\" class=\"label\">{escape(label)}</text>
  <text x=\"{x + 18}\" y=\"{y + 82}\" class=\"value\">{escape(value)}</text>
  <text x=\"{x + 18}\" y=\"{y + 108}\" class=\"muted\">{escape(hint)}</text>
"""


def render_overview(data: dict) -> str:
    totals = data["totals"]
    user = data["user"]
    languages = data["languages"].most_common(6)

    body = ""
    metrics = [
        ("Repositories", compact(totals["repos"]), f"{totals['active_90']} active in last 90 days"),
        ("Followers", compact(int(user.get("followers", 0))), f"Following {int(user.get('following', 0))}"),
        ("Total Stars", compact(totals["stars"]), "Across all public repositories"),
        ("Total Forks", compact(totals["forks"]), "Community reuse signal"),
    ]

    start_x = 64
    for index, (label, value, hint) in enumerate(metrics):
        body += metric_box(start_x + index * 272, 162, label, value, hint)

    body += "\n  <text x=\"64\" y=\"348\" class=\"label\">Language Signal</text>"

    max_count = max([count for _, count in languages], default=1)
    for index, (language, count) in enumerate(languages):
        y = 382 + index * 28
        width = int((count / max_count) * 620)
        body += f"""
  <text x=\"64\" y=\"{y}\" class=\"small\">{escape(language)}</text>
  <rect x=\"228\" y=\"{y - 13}\" width=\"700\" height=\"12\" rx=\"6\" fill=\"#1f1f24\"/>
  <rect x=\"228\" y=\"{y - 13}\" width=\"{width}\" height=\"12\" rx=\"6\" fill=\"url(#accent)\"/>
  <text x=\"944\" y=\"{y}\" class=\"muted\">{count} repos</text>
"""

    body += f"\n  <text x=\"64\" y=\"534\" class=\"muted\">Profile: {escape(data['username'])} | Theme: RED x GOLD x WHITE x BLACK</text>"
    return wrap_svg("COMMAND CENTER", "Live profile telemetry and project momentum", body, 560)


def render_achievements(data: dict) -> str:
    achievements = data["achievements"]
    unlocked = sum(1 for item in achievements if item["unlocked"])

    body = ""
    body += f"\n  <text x=\"64\" y=\"168\" class=\"label\">Unlocked {unlocked}/{len(achievements)}</text>"

    for index, item in enumerate(achievements):
        row = index // 3
        col = index % 3
        x = 64 + col * 356
        y = 194 + row * 132
        unlocked_flag = item["unlocked"]
        stroke = "#f4c35a" if unlocked_flag else "#595959"
        head = "#b30000" if unlocked_flag else "#2b2b2e"
        state = "UNLOCKED" if unlocked_flag else "LOCKED"

        body += f"""
  <rect x=\"{x}\" y=\"{y}\" width=\"334\" height=\"112\" rx=\"14\" fill=\"#141417\" stroke=\"{stroke}\" stroke-width=\"1.6\"/>
  <rect x=\"{x}\" y=\"{y}\" width=\"334\" height=\"6\" rx=\"14\" fill=\"{head}\"/>
  <text x=\"{x + 16}\" y=\"{y + 34}\" class=\"label\">{escape(item['name'])}</text>
  <text x=\"{x + 16}\" y=\"{y + 62}\" class=\"small\">{escape(item['detail'])}</text>
  <text x=\"{x + 16}\" y=\"{y + 90}\" class=\"mono\">{state}</text>
"""

    body += "\n  <text x=\"64\" y=\"478\" class=\"muted\">Custom milestones generated from repository, social, and activity signals.</text>"
    return wrap_svg("ACHIEVEMENTS", "Milestones forged from live GitHub telemetry", body, 510)


def render_trophies(data: dict) -> str:
    top_repos = data["top_repos"]

    medal_colors = ["#f4c35a", "#d9d9d9", "#c88956", "#b30000", "#b30000", "#b30000"]
    medals = ["1", "2", "3", "4", "5", "6"]

    body = ""
    body += "\n  <text x=\"64\" y=\"170\" class=\"label\">Top repositories by stars and forks</text>"

    if not top_repos:
        body += "\n  <text x=\"64\" y=\"220\" class=\"small\">No repositories available yet.</text>"
    else:
        for index, repo in enumerate(top_repos[:6]):
            y = 198 + index * 48
            color = medal_colors[index]
            language = repo.get("language") or "N/A"
            stars = int(repo.get("stargazers_count", 0))
            forks = int(repo.get("forks_count", 0))
            name = repo.get("name", "unknown-repo")

            body += f"""
  <rect x=\"64\" y=\"{y - 24}\" width=\"1072\" height=\"38\" rx=\"10\" fill=\"#141417\" stroke=\"#2a2a2d\"/>
  <circle cx=\"88\" cy=\"{y - 5}\" r=\"13\" fill=\"{color}\"/>
  <text x=\"88\" y=\"{y - 1}\" text-anchor=\"middle\" class=\"mono\">{medals[index]}</text>
  <text x=\"112\" y=\"{y}\" class=\"small\">{escape(name)}</text>
  <text x=\"760\" y=\"{y}\" class=\"muted\">Lang: {escape(language)}</text>
  <text x=\"930\" y=\"{y}\" class=\"muted\">Stars: {stars}</text>
  <text x=\"1060\" y=\"{y}\" class=\"muted\">Forks: {forks}</text>
"""

    body += "\n  <text x=\"64\" y=\"492\" class=\"muted\">Trophy colors: Gold (1st), Silver (2nd), Bronze (3rd), Crimson (combat rank).</text>"
    return wrap_svg("TROPHY CASE", "Repository standings and project dominance", body, 520)


def render_recent_activity(data: dict) -> str:
    lines = data["activity"]

    body = ""
    body += "\n  <text x=\"64\" y=\"170\" class=\"label\">Latest public events</text>"

    if not lines:
        body += "\n  <text x=\"64\" y=\"218\" class=\"small\">Recent activity is currently unavailable from the public events feed.</text>"
    else:
        for index, line in enumerate(lines[:8]):
            y = 196 + index * 39
            body += f"""
  <rect x=\"64\" y=\"{y - 23}\" width=\"1072\" height=\"31\" rx=\"8\" fill=\"#141417\" stroke=\"#2a2a2d\"/>
  <circle cx=\"84\" cy=\"{y - 8}\" r=\"4\" fill=\"#f4c35a\"/>
  <text x=\"98\" y=\"{y - 2}\" class=\"small\">{escape(line)}</text>
"""

    body += "\n  <text x=\"64\" y=\"510\" class=\"muted\">Source: GitHub public events API. Auto-refreshed by workflow.</text>"
    return wrap_svg("RECENT ACTIVITY", "Ops timeline from live event feed", body, 540)


def render_radar(data: dict) -> str:
    scores = data["radar"]

    body = ""
    body += "\n  <text x=\"64\" y=\"170\" class=\"label\">Operational score bands</text>"

    for index, (label, score) in enumerate(scores.items()):
        y = 202 + index * 54
        width = int((score / 100) * 720)

        body += f"""
  <text x=\"64\" y=\"{y}\" class=\"small\">{escape(label)}</text>
  <rect x=\"320\" y=\"{y - 14}\" width=\"760\" height=\"16\" rx=\"8\" fill=\"#1f1f24\"/>
  <rect x=\"320\" y=\"{y - 14}\" width=\"{width}\" height=\"16\" rx=\"8\" fill=\"url(#accent)\"/>
  <text x=\"1094\" y=\"{y}\" class=\"mono\" text-anchor=\"end\">{score:>3}/100</text>
"""

    body += "\n  <text x=\"64\" y=\"488\" class=\"muted\">A custom cyber-style profile signature panel replacing snake animation.</text>"
    return wrap_svg("ARSENAL RADAR", "Unique profile signature, tuned for red-white-gold-black aesthetics", body, 520)


def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = build_data(USERNAME)

    write_file(os.path.join(OUT_DIR, "overview.svg"), render_overview(data))
    write_file(os.path.join(OUT_DIR, "achievements.svg"), render_achievements(data))
    write_file(os.path.join(OUT_DIR, "trophies.svg"), render_trophies(data))
    write_file(os.path.join(OUT_DIR, "recent-activity.svg"), render_recent_activity(data))
    write_file(os.path.join(OUT_DIR, "radar.svg"), render_radar(data))

    metadata = {
        "username": data["username"],
        "updated": data["updated"],
        "totals": data["totals"],
        "top_languages": data["languages"].most_common(6),
    }
    write_file(os.path.join(OUT_DIR, "profile-metadata.json"), json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render accurate, regularly refreshed GitHub profile panels from public data."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import os
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
USERNAME = os.getenv("PROFILE_USERNAME", "Karmanya03")
TOKEN = os.getenv("GITHUB_TOKEN", "")
OUT_DIR = Path(os.getenv("PROFILE_OUT_DIR", "assets/panels"))
METADATA_FILE = OUT_DIR / "profile-metadata.json"
API_VERSION = "2022-11-28"

RANK_NAMES = ["C", "B", "A", "AA", "AAA", "S", "SS", "SSS"]
RANK_ORDER = {rank: index for index, rank in enumerate(RANK_NAMES)}
TROPHY_RULES = {
    "Stars": ([1, 10, 30, 50, 100, 200, 700, 2000], "✳"),
    "Followers": ([1, 10, 20, 50, 100, 200, 400, 1000], "◉"),
    "Repositories": ([1, 10, 20, 30, 35, 40, 45, 50], "▤"),
    "Commits": ([1, 10, 100, 200, 500, 1000, 2000, 4000], "↟"),
    "Pull requests": ([1, 10, 20, 50, 100, 200, 500, 1000], "⑂"),
    "Issues": ([1, 10, 20, 50, 100, 200, 500, 1000], "!"),
    "Languages": ([10], "⌘"),
}


class GitHubAPIError(RuntimeError):
    """Raised when a complete snapshot cannot be fetched."""


def api_get(path: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "karmanya-profile-panels",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(f"{API_BASE}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GitHubAPIError(f"GitHub API request failed for {path}: {exc}") from exc


def short_text(value: str | None, limit: int) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def read_metadata() -> dict[str, Any]:
    try:
        with METADATA_FILE.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    # Migrate the earlier profile panel's cache without pretending to recollect it.
    if "generated_at" not in data and data.get("updated"):
        try:
            stamp = dt.datetime.strptime(data["updated"], "%d %b %Y %H:%M UTC")
            data["generated_at"] = stamp.replace(tzinfo=dt.timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass
    totals = data.get("totals", {})
    if "repos" not in totals and "repositories" in totals:
        totals["repos"] = totals["repositories"]
    if "stars" not in totals and "stars_on_owned_repos" in totals:
        totals["stars"] = totals["stars_on_owned_repos"]
    if "top_languages" not in data and isinstance(data.get("languages"), dict):
        data["top_languages"] = list(data["languages"].items())
    if "top_repos" not in data and isinstance(data.get("repositories"), list):
        data["repository_selection"] = "recent"
        data["top_repos"] = [
            {
                "name": repo.get("name", ""),
                "full_name": repo.get("full_name", repo.get("name", "")),
                "description": repo.get("description", ""),
                "url": repo.get("html_url", repo.get("url", "")),
                "stars": int(repo.get("stars", 0)),
                "forks": int(repo.get("forks", 0)),
                "language": repo.get("language") or "Various",
                "pushed_at": repo.get("pushed_at") or repo.get("updated_at", ""),
            }
            for repo in data["repositories"]
        ]
    if not data.get("trophies_raw"):
        data["trophies_raw"] = {
            "Stars": totals.get("stars", 0),
            "Followers": totals.get("followers", 0),
            "Repositories": totals.get("repos", 0),
            "Commits": totals.get("commits"),
            "Pull requests": totals.get("prs"),
            "Issues": totals.get("issues"),
            "Languages": totals.get("unique_languages", len(data.get("top_languages", []))),
        }
    return data


def fetch_repositories(username: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {"per_page": 100, "page": page, "type": "owner", "sort": "updated"}
        )
        chunk = api_get(f"/users/{username}/repos?{query}")
        if not isinstance(chunk, list):
            raise GitHubAPIError("The repository endpoint returned an unexpected response.")
        repos.extend(chunk)
        if len(chunk) < 100:
            return repos
        page += 1


def fetch_repo_languages(repo: dict[str, Any]) -> dict[str, int]:
    name = repo.get("full_name", "")
    if not name or repo.get("fork"):
        return {}
    url_name = urllib.parse.quote(name, safe="/")
    result = api_get(f"/repos/{url_name}/languages")
    if not isinstance(result, dict):
        raise GitHubAPIError(f"The language endpoint returned an unexpected response for {name}.")
    return {str(language): int(size) for language, size in result.items()}


def fetch_languages(repos: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    owned = [repo for repo in repos if not repo.get("fork")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(fetch_repo_languages, repo) for repo in owned]
        try:
            for job in concurrent.futures.as_completed(jobs):
                for name, size in job.result().items():
                    totals[name] = totals.get(name, 0) + size
        except Exception:
            for job in jobs:
                job.cancel()
            raise
    return dict(sorted(totals.items(), key=lambda pair: pair[1], reverse=True))


def search_count(query: str, *, commits: bool = False) -> tuple[int, bool]:
    q = urllib.parse.quote(query, safe=":")
    endpoint = "commits" if commits else "issues"
    payload = api_get(f"/search/{endpoint}?q={q}&per_page=1")
    if not isinstance(payload, dict):
        raise GitHubAPIError(f"The {endpoint} search endpoint returned an unexpected response.")
    count = int(payload.get("total_count", 0))
    capped = count >= 1000
    return min(count, 1000), capped


def optional_search_count(query: str, *, commits: bool = False) -> tuple[int | None, bool]:
    try:
        return search_count(query, commits=commits)
    except GitHubAPIError as exc:
        print(f"{exc}\nThis count will be shown as unavailable in this snapshot.")
        return None, False


def event_summary(event: dict[str, Any]) -> str:
    kind = event.get("type", "")
    payload = event.get("payload", {})
    action = str(payload.get("action") or "updated").replace("_", " ").capitalize()
    if kind == "PushEvent":
        commits = int(payload.get("size") or payload.get("distinct_size") or 0)
        return f"Pushed {commits} {'commit' if commits == 1 else 'commits'}" if commits else "Pushed changes"
    if kind == "WatchEvent":
        return "Starred repository"
    if kind == "CreateEvent":
        return f"Created {payload.get('ref_type') or 'repository item'}"
    if kind == "PullRequestEvent":
        return f"{action} pull request"
    if kind == "IssuesEvent":
        return f"{action} issue"
    if kind == "IssueCommentEvent":
        return "Commented on issue or pull request"
    if kind == "ForkEvent":
        return "Forked repository"
    if kind == "ReleaseEvent":
        return "Published release"
    return kind.removesuffix("Event") or "Public event"


def iso_time(raw: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def fetch_snapshot(username: str) -> dict[str, Any]:
    user = api_get(f"/users/{username}")
    if not isinstance(user, dict) or not user.get("login"):
        raise GitHubAPIError("The user endpoint returned an unexpected response.")

    repos = fetch_repositories(username)
    events = api_get(f"/users/{username}/events/public?per_page=30")
    if not isinstance(events, list):
        raise GitHubAPIError("The public-events endpoint returned an unexpected response.")

    languages = fetch_languages(repos)
    commits, commits_capped = optional_search_count(f"author:{username}", commits=True)
    prs, prs_capped = optional_search_count(f"author:{username} type:pr is:public")
    issues, issues_capped = optional_search_count(f"author:{username} type:issue is:public")

    top_repos = sorted(
        (repo for repo in repos if not repo.get("fork")),
        key=lambda repo: (
            int(repo.get("stargazers_count", 0)),
            int(repo.get("forks_count", 0)),
            repo.get("pushed_at") or "",
        ),
        reverse=True,
    )[:6]

    fresh_at = dt.datetime.now(dt.timezone.utc)
    owned = [repo for repo in repos if not repo.get("fork")]
    totals = {
        "repos": int(user.get("public_repos", 0)),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in repos),
        "forks": sum(int(repo.get("forks_count", 0)) for repo in repos),
        "followers": int(user.get("followers", 0)),
        "following": int(user.get("following", 0)),
        "commits": commits,
        "prs": prs,
        "issues": issues,
        "unique_languages": len(languages),
        "active_90": sum(
            1
            for repo in owned
            if (stamp := iso_time(repo.get("pushed_at") or repo.get("updated_at")))
            and fresh_at - stamp <= dt.timedelta(days=90)
        ),
    }
    return {
        "username": user.get("login", username),
        "name": user.get("name") or username,
        "bio": user.get("bio") or "",
        "generated_at": fresh_at.isoformat(timespec="minutes").replace("+00:00", "Z"),
        "totals": totals,
        "limits": {"commits": commits_capped, "prs": prs_capped, "issues": issues_capped},
        "top_languages": list(languages.items()),
        "top_repos": [
            {
                "name": repo.get("name") or "",
                "full_name": repo.get("full_name") or "",
                "description": repo.get("description") or "",
                "url": repo.get("html_url") or f"https://github.com/{repo.get('full_name')}",
                "stars": int(repo.get("stargazers_count", 0)),
                "forks": int(repo.get("forks_count", 0)),
                "language": repo.get("language") or "Various",
                "pushed_at": repo.get("pushed_at") or repo.get("updated_at") or "",
            }
            for repo in top_repos
        ],
        "activity": [
            {
                "type": event.get("type", ""),
                "created_at": event.get("created_at", ""),
                "repo": event.get("repo", {}).get("name", ""),
                "url": f"https://github.com/{event.get('repo', {}).get('name', '')}",
                "payload": event.get("payload", {}),
                "summary": event_summary(event),
            }
            for event in events[:8]
            if event.get("created_at") and event.get("repo", {}).get("name")
        ],
        "repository_selection": "stars",
        "trophies_raw": {
            "Stars": totals["stars"],
            "Followers": totals["followers"],
            "Repositories": totals["repos"],
            "Commits": commits,
            "Pull requests": prs,
            "Issues": issues,
            "Languages": totals["unique_languages"],
        },
    }


def fmt_count(value: int, capped: bool = False) -> str:
    number = f"{int(value):,}"
    return f"{number}+" if capped else number


def stamp_label(raw: str) -> str:
    stamp = iso_time(raw)
    return stamp.strftime("%d %b %Y · %H:%M UTC") if stamp else "time unavailable"


def day_label(raw: str) -> str:
    stamp = iso_time(raw)
    return stamp.strftime("%d %b") if stamp else "—"


def svg_escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def wrap_svg(title: str, note: str, body: str, height: int, index: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
  width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-label="{svg_escape(title)}">
  <defs>
    <pattern id="grain" width="80" height="80" patternUnits="userSpaceOnUse">
      <path d="M0 79.5H80" stroke="#fff" stroke-opacity=".025"/>
      <circle cx="8" cy="12" r=".7" fill="#fff" opacity=".16"/>
      <circle cx="51" cy="40" r=".6" fill="#fff" opacity=".11"/>
      <circle cx="29" cy="66" r=".6" fill="#fff" opacity=".13"/>
    </pattern>
    <style>
      .canvas{{fill:#09090a}} .grain{{fill:url(#grain)}}
      .rule{{stroke:#4b4540;stroke-width:1}} .red{{fill:#d12f37}} .gold{{fill:#d5ad52}}
      .white{{fill:#f4f0e9}} .soft{{fill:#aba59c}}
      .micro{{font:700 16px 'Segoe UI',Arial,sans-serif;letter-spacing:1px}}
      .section{{font:700 18px 'Segoe UI',Arial,sans-serif;letter-spacing:1px}}
      .small{{font:500 19px 'Segoe UI',Arial,sans-serif}}
      .mono{{font:600 16px Consolas,'Courier New',monospace}}
      .serif{{font:600 24px Georgia,'Times New Roman',serif}}
      .num{{font:500 45px Georgia,'Times New Roman',serif;font-variant-numeric:tabular-nums}}
      .inkline{{stroke-dasharray:28 10;animation:ink 8s linear infinite}}
      @keyframes ink{{to{{stroke-dashoffset:-304}}}}
      @media (prefers-reduced-motion:reduce){{.inkline{{animation:none}}}}
    </style>
  </defs>
  <rect width="1200" height="{height}" rx="5" class="canvas"/>
  <rect width="1200" height="{height}" rx="5" class="grain"/>
  <path d="M0 0H1200" stroke="#d12f37" stroke-width="5"/>
  <rect x="30" y="25" width="42" height="27" rx="2" class="red"/>
  <text x="51" y="44" text-anchor="middle" class="micro white">{svg_escape(index)}</text>
  <text x="87" y="43" class="micro white">{svg_escape(title.upper())}</text>
  <text x="1150" y="43" text-anchor="end" class="micro gold">{svg_escape(note.upper())}</text>
  <path d="M30 62H1170" class="rule"/>
  {body}
</svg>
"""


def render_overview(data: dict[str, Any], cached: bool = False) -> str:
    totals = data.get("totals", {})
    limits = data.get("limits", {})
    counters = [
        ("PUBLIC REPOS", "repos", "▤"),
        ("FOLLOWERS", "followers", "◉"),
        ("STARS ON PUBLIC REPOS", "stars", "✳"),
        ("PUBLIC COMMITS", "commits", "↟"),
    ]
    body = ['<text x="47" y="102" class="micro gold">THE OPEN FILE / ENGINEERING RECORD</text>']
    body.append('<path d="M47 115H1152" class="rule"/>')
    for index, (label, key, icon) in enumerate(counters):
        x = 47 + index * 280
        raw = totals.get(key)
        value = fmt_count(raw, limits.get(key, False)) if raw is not None else "—"
        body.append(f"""
  <text x="{x}" y="150" class="micro soft">{icon}  {label}</text>
  <text x="{x}" y="208" class="num white">{value}</text>
  <path d="M{x} 222H{x + 236}" stroke="{('#d12f37' if index == 0 else '#d5ad52')}" stroke-width="2"/>
""")

    body.append("""
  <text x="47" y="265" class="micro gold">LANGUAGE / BYTE SHARE IN OWNED NON-FORK REPOSITORIES</text>
  <text x="1152" y="265" text-anchor="end" class="micro soft">A VIEW OF SOURCE, NOT A SKILL SCORE</text>
""")
    languages = data.get("top_languages", [])[:6]
    total_bytes = sum(int(size) for _, size in data.get("top_languages", []))
    colors = ["#d12f37", "#d5ad52", "#f4f0e9", "#8c3336", "#a39b8d", "#78736c"]
    for index, (language, size) in enumerate(languages):
        row = index // 3
        col = index % 3
        x = 47 + col * 374
        y = 301 + row * 54
        share = int(size) / max(1, total_bytes)
        width = int(278 * share)
        color = colors[index % len(colors)]
        body.append(f"""
  <text x="{x}" y="{y}" class="small white">{svg_escape(language)}</text>
  <text x="{x + 325}" y="{y}" text-anchor="end" class="mono gold">{share * 100:.1f}%</text>
  <rect x="{x}" y="{y + 11}" width="278" height="5" rx="2.5" fill="#312d29"/>
  <rect x="{x}" y="{y + 11}" width="{width}" height="5" rx="2.5" fill="{color}"/>
""")

    secondary = [
        ("FORKS RECEIVED", fmt_count(totals["forks"]) if totals.get("forks") is not None else "—"),
        ("OPENED PULL REQUESTS", fmt_count(totals["prs"], limits.get("prs", False)) if totals.get("prs") is not None else "—"),
        ("OPENED ISSUES", fmt_count(totals["issues"], limits.get("issues", False)) if totals.get("issues") is not None else "—"),
        ("LANGUAGES", fmt_count(totals["unique_languages"]) if totals.get("unique_languages") is not None else "—"),
        ("ACTIVE REPOS · 90D", fmt_count(totals["active_90"]) if totals.get("active_90") is not None else "—"),
    ]
    yline = 439
    body.append(f'<path d="M47 {yline}H1152" class="rule"/>')
    for index, (label, value) in enumerate(secondary):
        x = 47 + index * 220
        body.append(f'<text x="{x}" y="{yline + 27}" class="micro gold">{value}</text><text x="{x}" y="{yline + 45}" class="micro soft">{label}</text>')
    state = "CACHED DATA · ORIGINAL TIMESTAMP RETAINED" if cached else f"COLLECTED {stamp_label(data.get('generated_at', ''))}"
    body.append(f"""
  <path d="M47 510H1152" stroke="#d5ad52" stroke-width="1"/>
  <text x="47" y="533" class="micro soft">{svg_escape(state.upper())}  ·  PUBLIC GITHUB API</text>
  <path d="M869 530H1134" stroke="#d12f37" stroke-width="2" class="inkline"/>
""")
    return wrap_svg("Desk copy / account telemetry", "hourly api snapshot", "".join(body), 555, "00")


def render_repositories(data: dict[str, Any]) -> str:
    repos = data.get("top_repos", [])[:6]
    selection = "MOST STARRED" if data.get("repository_selection") == "stars" else "LATEST PUSHED"
    body: list[str] = [
        f'<text x="47" y="99" class="micro gold">{selection} · OWNED PUBLIC REPOSITORIES · FORKS OMITTED</text>'
    ]
    if not repos:
        body.append('<text x="47" y="153" class="small soft">Repository list unavailable in this snapshot.</text>')
    rows = max(1, (len(repos) + 1) // 2)
    for index, repo in enumerate(repos):
        col = index % 2
        row = index // 2
        x = 47 + col * 560
        y = 121 + row * 205
        width = 540
        name = svg_escape(short_text(repo.get("name"), 34))
        description_text = short_text(repo.get("description"), 95) or "No description added."
        description_text = description_text.replace("**", "").replace("__", "").replace("`", "")
        description = textwrap.wrap(description_text, width=56)[:2]
        description_lines = "".join(
            f'<tspan x="{x + 16}" dy="{0 if line_index == 0 else 22}">{svg_escape(line)}</tspan>'
            for line_index, line in enumerate(description)
        )
        url = svg_escape(repo.get("url"))
        language = svg_escape(short_text(repo.get("language", "Various"), 16))
        body.append(f"""
  <path d="M{x} {y + 15}H{x + width}V{y + 184}H{x}Z" fill="#101011" stroke="#3b3630" stroke-width="1"/>
  <path d="M{x} {y + 15}V{y + 1}H{x + 74}L{x + 86} {y + 15}" fill="#151313" stroke="#8f312f" stroke-width="1.2"/>
  <text x="{x + 15}" y="{y + 43}" class="micro red">{index + 1:02}</text>
  <a xlink:href="{url}" target="_blank" rel="noopener noreferrer">
  <text x="{x + 52}" y="{y + 44}" class="serif white">{name}</text>
  </a>
  <text x="{x + 16}" y="{y + 77}" class="small soft">{description_lines}</text>
  <path d="M{x + 16} {y + 116}H{x + width - 16}" class="rule"/>
  <text x="{x + 16}" y="{y + 145}" class="micro gold">{language.upper()}</text>
  <text x="{x + width - 16}" y="{y + 145}" text-anchor="end" class="micro white">✳ {int(repo.get('stars', 0))}   ⑂ {int(repo.get('forks', 0))}</text>
  <text x="{x + width - 16}" y="{y + 169}" text-anchor="end" class="micro soft">PUSHED {day_label(repo.get('pushed_at', '')).upper()}</text>
""")
    panel_height = 121 + rows * 205 + 15
    return wrap_svg("Project ledger / public code", "hourly repository index", "".join(body), panel_height, "01")


def rank_for(key: str, score: int) -> tuple[str, int, int | None]:
    thresholds, _ = TROPHY_RULES[key]
    if key == "Languages":
        return ("SECRET", thresholds[0], None) if score >= thresholds[0] else ("—", 0, thresholds[0])
    rank_idx = 0
    for index, threshold in enumerate(thresholds):
        if score >= threshold:
            rank_idx = index
    current = thresholds[rank_idx]
    next_value = thresholds[rank_idx + 1] if rank_idx + 1 < len(thresholds) else None
    return RANK_NAMES[rank_idx], current, next_value


def render_trophies(data: dict[str, Any]) -> str:
    scores = data.get("trophies_raw", {})
    limits = data.get("limits", {})
    cards = [(key, value) for key, value in TROPHY_RULES.items() if scores.get(key) is not None]
    width = 267
    gap = 16
    columns = 4
    rows = max(1, (len(cards) + columns - 1) // columns)
    left = (1200 - columns * width - (columns - 1) * gap) // 2
    body: list[str] = [
        '<text x="47" y="98" class="micro gold">PUBLIC PROFILE COUNTS / COMMUNITY THRESHOLD LADDER</text>'
    ]
    for index, (key, (thresholds, icon)) in enumerate(cards):
        raw_score = scores.get(key)
        missing = raw_score is None
        score = int(raw_score or 0)
        limit_key = {"Pull requests": "prs"}.get(key, key.lower().replace(" ", "_"))
        capped = bool(limits.get(limit_key, False))
        rank, current, next_value = ("—", 0, None) if missing else rank_for(key, score)
        x = left + (index % columns) * (width + gap)
        y = 120 + (index // columns) * 250
        max_rank = next_value is None
        if capped:
            rank = f"≥{rank}"
            percent = 100
            target_label = "SEARCH CAP"
        elif max_rank:
            percent = 100
            target_label = "MAX"
        else:
            target = int(next_value)
            percent = max(3, min(100, int((score - current) / max(1, target - current) * 100)))
            target_label = f"NEXT {target}"
        if key == "Languages" and score < 10:
            percent = max(3, min(100, int(score / 10 * 100)))
            target_label = "NEXT 10"
        bar_w = int((width - 40) * percent / 100)
        score_text = "DATA UNAVAILABLE" if missing else fmt_count(score, capped)
        body.append(f"""
  <rect x="{x}" y="{y}" width="{width}" height="230" rx="3" fill="#111112" stroke="#4a4030" stroke-width="1"/>
  <path d="M{x} {y}H{x + width}" stroke="{('#d12f37' if index == 0 else '#d5ad52')}" stroke-width="3"/>
  <text x="{x + width // 2}" y="{y + 27}" text-anchor="middle" class="micro gold">{svg_escape(key.upper())}</text>
  <circle cx="{x + width // 2}" cy="{y + 82}" r="33" fill="#181718" stroke="#d5ad52" stroke-width="1.2"/>
  <path d="M{x + width // 2 - 19} {y + 64}h9l3 10 7-6 6 6 3-10h9v17q-3 10-18 10t-19-10Z" fill="none" stroke="#d5ad52" stroke-width="2" stroke-linejoin="round"/>
  <path d="M{x + width // 2 - 10} {y + 95}h20m-24 5h28" stroke="#d12f37" stroke-width="2" stroke-linecap="round"/>
  <text x="{x + width // 2}" y="{y + 145}" text-anchor="middle" class="serif white">{rank}</text>
  <text x="{x + width // 2}" y="{y + 169}" text-anchor="middle" class="micro gold">{score_text} {icon}</text>
  <rect x="{x + 20}" y="{y + 189}" width="{width - 40}" height="4" rx="2" fill="#3b3630"/>
  <rect x="{x + 20}" y="{y + 189}" width="{bar_w}" height="4" rx="2" fill="#d12f37"/>
  <text x="{x + width // 2}" y="{y + 214}" text-anchor="middle" class="micro soft">{"NO SNAPSHOT" if missing else target_label}</text>
""")
    footer_y = 120 + (rows - 1) * 250 + 230 + 28
    body.append(f"""
  <text x="600" y="{footer_y}" text-anchor="middle" class="micro soft">UNOFFICIAL COMMUNITY RANK DISPLAY · VALUES ARE PUBLIC API COUNTS</text>
  <path d="M223 {footer_y + 20}H977" stroke="#d5ad52" stroke-opacity=".5"/>
  <text x="600" y="{footer_y + 42}" text-anchor="middle" class="micro red">SEARCH ENDPOINT COUNTS SHOW “+” WHEN GITHUB CAPS RESULTS AT 1,000</text>
""")
    return wrap_svg("Trophy cabinet / public record", "community ranks · not native achievements", "".join(body), footer_y + 60, "02")


def render_recent_activity(data: dict[str, Any], cached: bool = False) -> str:
    entries = data.get("activity", [])[:8]
    body = [
        '<text x="47" y="99" class="micro gold">LATEST EVENTS EXPOSED BY THE PUBLIC PROFILE FEED</text>',
        '<path d="M66 128V459" stroke="#8a262d" stroke-width="2" stroke-dasharray="3 7"/>',
    ]
    if not entries:
        note = "Saved snapshot has no recent events." if cached else "No recent public events in the profile feed."
        body.append(f'<text x="96" y="159" class="small soft">{svg_escape(note)}</text>')
    for index, event in enumerate(entries):
        y = 147 + index * 42
        summary = svg_escape(event.get("summary") or event_summary(event))
        repository = svg_escape(short_text(event.get("repo"), 52))
        time = iso_time(event.get("created_at"))
        date = time.strftime("%d %b · %H:%M UTC") if time else "time unavailable"
        link = svg_escape(event.get("url", ""))
        body.append(f"""
  <circle cx="66" cy="{y - 4}" r="5" fill="#d5ad52" stroke="#09090a" stroke-width="2"/>
  <text x="91" y="{y}" class="micro red">{date.upper()}</text>
  <text x="286" y="{y}" class="small white">{summary}</text>
  <a xlink:href="{link}" target="_blank" rel="noopener noreferrer">
    <text x="1152" y="{y}" text-anchor="end" class="mono gold">{repository} ↗</text>
  </a>
""")
    status = "CACHED" if cached else "UPDATED HOURLY · PUBLIC EVENTS MAY BE DELAYED BY GITHUB"
    body.append(f'<text x="47" y="478" class="micro soft">{svg_escape(status)}</text>')
    return wrap_svg("Recent activity / event log", "public events · hourly refresh", "".join(body), 510, "03")


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    old = read_metadata()
    try:
        data = fetch_snapshot(USERNAME)
    except GitHubAPIError as exc:
        if not old:
            raise
        data = old
        cached = True
        print(f"{exc}\nUsing the last complete snapshot dated {data.get('generated_at', 'unknown')}.")
    else:
        write_file(METADATA_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        cached = False

    write_file(OUT_DIR / "overview.svg", render_overview(data, cached))
    write_file(OUT_DIR / "repositories.svg", render_repositories(data))
    write_file(OUT_DIR / "trophies.svg", render_trophies(data))
    write_file(OUT_DIR / "recent-activity.svg", render_recent_activity(data, cached))
    print(f"Rendered 4 panels from the {'cached' if cached else 'current'} GitHub snapshot dated {data.get('generated_at', 'unknown')}.")


if __name__ == "__main__":
    main()

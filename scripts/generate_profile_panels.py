#!/usr/bin/env python3
"""Render accurate, regularly refreshed GitHub profile panels from public data."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
import re
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
    "Stars": ([1, 10, 30, 50, 100, 200, 700, 2000], "✦"),
    "Followers": ([1, 10, 20, 50, 100, 200, 400, 1000], "◎"),
    "Repositories": ([1, 10, 20, 30, 35, 40, 45, 50], "▤"),
    "Commits": ([1, 10, 100, 200, 500, 1000, 2000, 4000], "↟"),
    "Pull requests": ([1, 10, 20, 50, 100, 200, 500, 1000], "⑂"),
    "Issues": ([1, 10, 20, 50, 100, 200, 500, 1000], "!"),
    "Languages": ([10], "〈/〉"),
}

SHIELD_GROUPS = {
    "LANGUAGES & SYSTEMS": [
        ("Rust", "rust"), ("WebAssembly", "webassembly"), ("C", "c"),
        ("C++", "cplusplus"), ("C Sharp", "csharp"), ("Java", "openjdk"),
        ("JavaScript", "javascript"), ("Python", "python"), ("PowerShell", "powershell"),
        ("HTML5", "html5"), ("CSS3", "css3"), ("R", "r"),
    ],
    "CLOUD, SERVICES & DATA": [
        ("AWS", "amazonwebservices"), ("Azure", "microsoftazure"),
        ("Google Cloud", "googlecloud"), ("Cloudflare", "cloudflare"),
        ("Django", "django"), ("Flask", "flask"), ("FastAPI", "fastapi"),
        ("Node.js", "nodedotjs"), ("MySQL", "mysql"), ("MongoDB", "mongodb"),
        ("PostgreSQL", "postgresql"), ("SQLite", "sqlite"),
    ],
    "SECURITY, BUILD & CREATIVE": [
        ("Git", "git"), ("GitHub", "github"), ("GitLab", "gitlab"),
        ("Nginx", "nginx"), ("Jira", "jira"), ("Unity", "unity"),
        ("Unreal Engine", "unrealengine"), ("TensorFlow", "tensorflow"),
        ("NumPy", "numpy"), ("Adobe", "adobe"),
        ("Premiere Pro", "adobepremierepro"), ("After Effects", "adobeaftereffects"),
    ],
}


class GitHubAPIError(RuntimeError):
    """Raised when a complete snapshot cannot be fetched."""


class ContributionCalendarParser(HTMLParser):
    """Read GitHub's public calendar cells and their accessible day labels."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading_parts: list[str] = []
        self.heading_depth = 0
        self.tooltip_for: str | None = None
        self.tooltip_parts: list[str] = []
        self.tooltips: dict[str, str] = {}
        self.cells: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h2" and attributes.get("id") == "js-contribution-activity-description":
            self.heading_depth = 1
        elif self.heading_depth:
            self.heading_depth += 1

        if tag == "tool-tip" and attributes.get("for"):
            self.tooltip_for = attributes["for"]
            self.tooltip_parts = []

        if tag == "td" and "ContributionCalendar-day" in (attributes.get("class") or ""):
            date = attributes.get("data-date")
            if date:
                try:
                    level = int(attributes.get("data-level") or 0)
                except ValueError:
                    level = 0
                self.cells.append({"id": attributes.get("id", ""), "date": date, "level": level})

    def handle_endtag(self, tag: str) -> None:
        if self.heading_depth:
            self.heading_depth -= 1
        if tag == "tool-tip" and self.tooltip_for:
            self.tooltips[self.tooltip_for] = " ".join(" ".join(self.tooltip_parts).split())
            self.tooltip_for = None
            self.tooltip_parts = []

    def handle_data(self, data: str) -> None:
        if self.heading_depth:
            self.heading_parts.append(data)
        if self.tooltip_for:
            self.tooltip_parts.append(data)


def parse_contribution_calendar(page: str, *, expected_year: int | None = None) -> dict[str, Any]:
    parser = ContributionCalendarParser()
    parser.feed(page)
    heading = " ".join(" ".join(parser.heading_parts).split())
    total_match = re.search(r"(\d[\d,]*)\s+contributions?\b", heading, re.IGNORECASE)
    if not total_match:
        raise GitHubAPIError("GitHub's contribution page did not include its contribution total.")
    total = int(total_match.group(1).replace(",", ""))
    days: list[dict[str, Any]] = []
    for cell in parser.cells:
        try:
            date = dt.date.fromisoformat(cell["date"])
        except ValueError as exc:
            raise GitHubAPIError("GitHub's contribution page included an invalid date.") from exc
        label = parser.tooltips.get(cell["id"], "")
        count_match = re.match(r"(\d[\d,]*)\s+contributions?\b", label, re.IGNORECASE)
        count = int(count_match.group(1).replace(",", "")) if count_match else 0
        if expected_year is not None and date.year != expected_year:
            continue
        days.append({"date": date.isoformat(), "count": count, "level": int(cell["level"])})

    days.sort(key=lambda day: day["date"])
    if expected_year is not None:
        expected_days = (dt.date(expected_year + 1, 1, 1) - dt.date(expected_year, 1, 1)).days
        expected_dates = [
            (dt.date(expected_year, 1, 1) + dt.timedelta(days=index)).isoformat()
            for index in range(expected_days)
        ]
        if (
            [day["date"] for day in days] != expected_dates
            or sum(day["count"] for day in days) != total
        ):
            raise GitHubAPIError(
                f"GitHub's {expected_year} calendar was incomplete or did not match its displayed total."
            )
    elif not parser.cells:
        raise GitHubAPIError("GitHub's contribution page did not include calendar cells.")

    return {"total": total, "heading": heading, "days": days}


def github_page(path: str) -> str:
    request = urllib.request.Request(
        f"https://github.com{path}",
        headers={
            "Accept": "text/html",
            "Cache-Control": "no-cache",
            "User-Agent": "Karmanya03-profile-panels",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GitHubAPIError(f"GitHub contribution page request failed for {path}: {exc}") from exc


def fetch_contribution_history(username: str, created_at: str, today: dt.date) -> dict[str, Any]:
    created = iso_time(created_at)
    if not created:
        raise GitHubAPIError("The public profile did not include its account creation date.")
    years = []
    for year in range(created.year, today.year + 1):
        query = urllib.parse.urlencode({"from": f"{year}-01-01", "to": f"{year}-12-31"})
        calendar = parse_contribution_calendar(
            github_page(f"/users/{urllib.parse.quote(username)}/contributions?{query}"),
            expected_year=year,
        )
        # Dates are implied by the year and position; keep the checked-in cache compact.
        years.append({
            "year": year,
            "total": calendar["total"],
            "counts": ",".join(str(day["count"]) for day in calendar["days"]),
            "levels": "".join(str(day["level"]) for day in calendar["days"]),
        })

    trailing = parse_contribution_calendar(
        github_page(f"/users/{urllib.parse.quote(username)}/contributions")
    )
    trailing_days = trailing["days"]
    if not trailing_days:
        raise GitHubAPIError("GitHub's rolling calendar did not include daily contribution counts.")
    first_date = dt.date.fromisoformat(trailing_days[0]["date"])
    if [day["date"] for day in trailing_days] != [
        (first_date + dt.timedelta(days=index)).isoformat()
        for index in range(len(trailing_days))
    ]:
        raise GitHubAPIError("GitHub's rolling contribution calendar had missing or duplicate dates.")
    return {
        "created_at": created_at,
        "last_year": trailing["total"],
        "last_year_heading": trailing["heading"],
        "last_year_days": {
            "from": trailing_days[0]["date"],
            "counts": ",".join(str(day["count"]) for day in trailing_days),
            "levels": "".join(str(day["level"]) for day in trailing_days),
        },
        "years": years,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z"),
    }


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
                "date_label": "PUSHED" if repo.get("pushed_at") else "UPDATED",
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
        "created_at": user.get("created_at", ""),
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
                "date_label": "PUSHED",
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
            for event in events[:30]
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


def wrap_svg(title: str, body: str, height: int, index: str) -> str:
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
      .ambient-line{{stroke-dasharray:46 1500;animation:rail 18s linear infinite}}
      .card-edge{{animation:edge 6s ease-in-out infinite}}
      .pulse{{transform-box:fill-box;transform-origin:center;animation:pulse 4.8s ease-in-out infinite}}
      .warmth{{animation:warmth 5.8s ease-in-out infinite}}
      .heat-cell{{animation:heat 6.5s ease-in-out infinite;animation-delay:var(--delay,0s)}}
      .rank-meter{{animation:rankGlow 4.2s ease-in-out infinite}}
      .month{{font:700 12px 'Segoe UI',Arial,sans-serif;letter-spacing:.5px}}
      .weekday{{font:700 11px 'Segoe UI',Arial,sans-serif}}
      .yearnum{{font:600 30px Georgia,'Times New Roman',serif;font-variant-numeric:tabular-nums}}
      .badge-label{{font:600 13px 'Segoe UI',Arial,sans-serif;letter-spacing:.2px}}
      .badge-glyph{{font:700 11px Consolas,'Courier New',monospace}}
      @keyframes ink{{to{{stroke-dashoffset:-304}}}}
      @keyframes rail{{to{{stroke-dashoffset:-1546}}}}
      @keyframes edge{{0%,100%{{stroke-opacity:.48}}50%{{stroke-opacity:.92}}}}
      @keyframes pulse{{0%,100%{{opacity:.72;transform:scale(.94)}}50%{{opacity:1;transform:scale(1.06)}}}}
      @keyframes warmth{{0%,100%{{opacity:.2}}50%{{opacity:.55}}}}
      @keyframes heat{{0%,100%{{filter:brightness(1)}}50%{{filter:brightness(1.24)}}}}
      @keyframes rankGlow{{0%,100%{{opacity:.74}}50%{{opacity:1}}}}
      @media (prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;scroll-behavior:auto!important}}}}
    </style>
  </defs>
  <rect width="1200" height="{height}" rx="5" class="canvas"/>
  <rect width="1200" height="{height}" rx="5" class="grain"/>
  <path d="M0 0H1200" stroke="#d12f37" stroke-width="5"/>
  <path d="M0 2H1200" stroke="#f4f0e9" stroke-opacity=".48" stroke-width="1" class="ambient-line"/>
  <rect x="30" y="25" width="42" height="27" rx="2" class="red"/>
  <text x="51" y="44" text-anchor="middle" class="micro white">{svg_escape(index)}</text>
  <text x="87" y="43" class="micro white">{svg_escape(title.upper())}</text>
  <path d="M30 62H1170" class="rule"/>
  {body}
</svg>
"""


def render_overview(data: dict[str, Any]) -> str:
    totals = data.get("totals", {})
    limits = data.get("limits", {})
    counters = [
        ("PUBLIC REPOS", "repos", "▤"),
        ("FOLLOWERS", "followers", "◉"),
        ("STARS ON PUBLIC REPOS", "stars", "✳"),
        ("PUBLIC COMMITS", "commits", "↟"),
    ]
    body: list[str] = []
    for index, (label, key, icon) in enumerate(counters):
        x = 47 + index * 280
        raw = totals.get(key)
        value = fmt_count(raw, limits.get(key, False)) if raw is not None else "—"
        body.append(f"""
  <text x="{x}" y="112" class="micro soft">{icon}  {label}</text>
  <text x="{x}" y="170" class="num white">{value}</text>
  <path d="M{x} 184H{x + 236}" stroke="{('#d12f37' if index == 0 else '#d5ad52')}" stroke-width="2"/>
""")

    body.append("""
  <text x="47" y="224" class="micro gold">LANGUAGE MIX</text>
""")
    languages = data.get("top_languages", [])[:6]
    total_bytes = sum(int(size) for _, size in data.get("top_languages", []))
    colors = ["#d12f37", "#d5ad52", "#f4f0e9", "#8c3336", "#a39b8d", "#78736c"]
    for index, (language, size) in enumerate(languages):
        row = index // 3
        col = index % 3
        x = 47 + col * 374
        y = 258 + row * 54
        share = int(size) / max(1, total_bytes)
        width = int(278 * share)
        color = colors[index % len(colors)]
        body.append(f"""
  <text x="{x}" y="{y}" class="small white">{svg_escape(language)}</text>
  <text x="{x + 325}" y="{y}" text-anchor="end" class="mono gold">{share * 100:.1f}%</text>
  <rect x="{x}" y="{y + 11}" width="278" height="5" rx="2.5" fill="#312d29"/>
  <rect x="{x}" y="{y + 11}" width="{width}" height="5" rx="2.5" fill="{color}" class="warmth"/>
""")

    secondary = [
        ("FORKS RECEIVED", fmt_count(totals["forks"]) if totals.get("forks") is not None else "—"),
        ("FOLLOWING", fmt_count(totals["following"]) if totals.get("following") is not None else "—"),
        ("PULL REQUESTS", fmt_count(totals["prs"], limits.get("prs", False)) if totals.get("prs") is not None else "—"),
        ("ISSUES", fmt_count(totals["issues"], limits.get("issues", False)) if totals.get("issues") is not None else "—"),
        ("LANGUAGES", fmt_count(totals["unique_languages"]) if totals.get("unique_languages") is not None else "—"),
        ("ACTIVE REPOS · 90D", fmt_count(totals["active_90"]) if totals.get("active_90") is not None else "—"),
    ]
    yline = 376
    body.append(f'<path d="M47 {yline}H1152" class="rule"/>')
    for index, (label, value) in enumerate(secondary):
        x = 47 + index * 185
        body.append(f'<text x="{x}" y="{yline + 27}" class="micro gold">{value}</text><text x="{x}" y="{yline + 45}" class="micro soft">{label}</text>')
    state = f"AS OF {stamp_label(data.get('generated_at', ''))}"
    body.append(f"""
  <path d="M47 456H1152" stroke="#d5ad52" stroke-width="1"/>
  <text x="47" y="482" class="micro soft">{svg_escape(state.upper())}</text>
  <path d="M869 479H1134" stroke="#d12f37" stroke-width="2" class="inkline"/>
""")
    return wrap_svg("Profile / live record", "".join(body), 505, "00")


def render_repositories(data: dict[str, Any]) -> str:
    repos = data.get("top_repos", [])[:6]
    selection = {
        "stars": "MOST STARRED",
        "pinned": "PINNED PROJECTS",
    }.get(data.get("repository_selection"), "SELECTED WORK")
    body: list[str] = [
        f'<text x="47" y="99" class="micro gold">{selection}</text>'
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
        description_text = short_text(repo.get("description"), 95)
        description_text = description_text.replace("**", "").replace("__", "").replace("`", "")
        description = textwrap.wrap(description_text, width=56)[:2]
        description_lines = "".join(
            f'<tspan x="{x + 16}" dy="{0 if line_index == 0 else 22}">{svg_escape(line)}</tspan>'
            for line_index, line in enumerate(description)
        )
        url = svg_escape(repo.get("url"))
        language = svg_escape(short_text(repo.get("language", "Various"), 16))
        pushed_at = repo.get("pushed_at", "")
        date_line = ""
        if iso_time(pushed_at):
            date_label = svg_escape(repo.get("date_label", "PUSHED"))
            date_line = (
                f'<text x="{x + width - 16}" y="{y + 169}" text-anchor="end" '
                f'class="micro soft">{date_label} {day_label(pushed_at).upper()}</text>'
            )
        body.append(f"""
  <path d="M{x} {y + 15}H{x + width}V{y + 184}H{x}Z" fill="#101011" stroke="#3b3630" stroke-width="1" class="card-edge"/>
  <path d="M{x} {y + 15}V{y + 1}H{x + 74}L{x + 86} {y + 15}" fill="#151313" stroke="#8f312f" stroke-width="1.2"/>
  <text x="{x + 15}" y="{y + 43}" class="micro red">{index + 1:02}</text>
  <a xlink:href="{url}" target="_blank" rel="noopener noreferrer">
  <text x="{x + 52}" y="{y + 44}" class="serif white">{name}</text>
  </a>
  <text x="{x + 16}" y="{y + 77}" class="small soft">{description_lines}</text>
  <path d="M{x + 16} {y + 116}H{x + width - 16}" class="rule"/>
  <text x="{x + 16}" y="{y + 145}" class="micro gold">{language.upper()}</text>
  <text x="{x + width - 16}" y="{y + 145}" text-anchor="end" class="micro white">✳ {int(repo.get('stars', 0))}   ⑂ {int(repo.get('forks', 0))}</text>
  {date_line}
""")
    panel_height = 121 + rows * 205 + 15
    return wrap_svg("Selected work", "".join(body), panel_height, "01")


def rank_for(key: str, score: int) -> tuple[str, int, int | None]:
    thresholds, _ = TROPHY_RULES[key]
    if key == "Languages":
        return ("SECRET", thresholds[0], None) if score >= thresholds[0] else ("LOCKED", 0, thresholds[0])
    if score < thresholds[0]:
        return "LOCKED", 0, thresholds[0]
    rank_idx = 0
    for index, threshold in enumerate(thresholds):
        if score >= threshold:
            rank_idx = index
    current = thresholds[rank_idx]
    next_value = thresholds[rank_idx + 1] if rank_idx + 1 < len(thresholds) else None
    return RANK_NAMES[rank_idx], current, next_value


def render_trophies(data: dict[str, Any]) -> str:
    rank_colors = {
        "C": "#aaa49a", "B": "#bd7955", "A": "#e2464d", "AA": "#ef8a4c",
        "AAA": "#d5ad52", "S": "#f4f0e9", "SS": "#e39cc2", "SSS": "#ffe99a",
        "SECRET": "#94d6aa", "LOCKED": "#766f67", "—": "#766f67",
    }
    scores = data.get("trophies_raw", {})
    limits = data.get("limits", {})
    cards = [(key, value) for key, value in TROPHY_RULES.items() if scores.get(key) is not None]
    width = 267
    gap = 16
    columns = 4
    rows = max(1, (len(cards) + columns - 1) // columns)
    left = (1200 - columns * width - (columns - 1) * gap) // 2
    body: list[str] = []
    for index, (key, (thresholds, icon)) in enumerate(cards):
        raw_score = scores.get(key)
        missing = raw_score is None
        score = int(raw_score or 0)
        limit_key = {"Pull requests": "prs"}.get(key, key.lower().replace(" ", "_"))
        capped = bool(limits.get(limit_key, False))
        rank, current, next_value = ("—", 0, None) if missing else rank_for(key, score)
        x = left + (index % columns) * (width + gap)
        y = 96 + (index // columns) * 248
        max_rank = next_value is None
        if capped:
            rank = f"≥{rank}"
            percent = 100
            target_label = "MAX+"
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
        elif rank == "LOCKED":
            percent = max(3, min(100, int(score / max(1, int(next_value or 1)) * 100)))
            target_label = f"FIRST · {int(next_value or 1)}"
        bar_w = int((width - 40) * percent / 100)
        score_text = "DATA UNAVAILABLE" if missing else fmt_count(score, capped)
        rank_color = rank_colors[rank.lstrip("≥")]
        center = x + width // 2
        body.append(f"""
  <rect x="{x}" y="{y}" width="{width}" height="226" rx="7" fill="#101011" stroke="{rank_color}" stroke-opacity=".58" class="card-edge"/>
  <path d="M{x + 1} {y + 7}H{x + width - 1}" stroke="{rank_color}" stroke-width="3"/>
  <path d="M{x + 14} {y + 20}h16m-16 0v16M{x + width - 14} {y + 20}h-16m16 0v16" fill="none" stroke="{rank_color}" stroke-opacity=".46" stroke-width="1.3"/>
  <text x="{x + 18}" y="{y + 36}" class="micro" fill="{rank_color}">{svg_escape(key.upper())}</text>
  <circle cx="{center}" cy="{y + 88}" r="39" fill="{rank_color}" fill-opacity=".07" stroke="{rank_color}" stroke-opacity=".24" stroke-width="7" class="pulse"/>
  <circle cx="{center}" cy="{y + 88}" r="31" fill="#151414" stroke="{rank_color}" stroke-width="1.5"/>
  <circle cx="{center}" cy="{y + 88}" r="35" fill="none" stroke="{rank_color}" stroke-opacity=".48" stroke-width="1" stroke-dasharray="2 7" class="inkline"/>
  <text x="{center}" y="{y + 99}" text-anchor="middle" fill="{rank_color}" font-family="Georgia,'Times New Roman',serif" font-size="32" font-weight="700">{svg_escape(icon)}</text>
  <text x="{center}" y="{y + 153}" text-anchor="middle" fill="{rank_color}" font-family="Georgia,'Times New Roman',serif" font-size="28" font-weight="700">{svg_escape(rank)}</text>
  <text x="{center}" y="{y + 176}" text-anchor="middle" class="micro white">{score_text} <tspan fill="{rank_color}">· {svg_escape(icon)}</tspan></text>
  <rect x="{x + 20}" y="{y + 192}" width="{width - 40}" height="5" rx="2.5" fill="#3b3630"/>
  <rect x="{x + 20}" y="{y + 192}" width="{bar_w}" height="5" rx="2.5" fill="{rank_color}" class="rank-meter"/>
  <text x="{center}" y="{y + 216}" text-anchor="middle" class="micro soft">{"NO SNAPSHOT" if missing else target_label}</text>
""")
    panel_height = 96 + rows * 248 + 8
    return wrap_svg("Trophy case", "".join(body), panel_height, "02")


def render_whoami(data: dict[str, Any]) -> str:
    name = svg_escape(data.get("name") or "Karmanya Ravindra")
    body = f"""
  <rect x="34" y="83" width="1132" height="320" rx="5" fill="#0d0d0e" stroke="#463a30" class="card-edge"/>
  <path d="M34 88V397" stroke="#d12f37" stroke-width="4"/>
  <rect x="58" y="104" width="136" height="24" rx="12" fill="#301316" stroke="#8f312f"/>
  <text x="126" y="121" text-anchor="middle" class="micro white">IDENTITY / 01</text>
  <rect x="209" y="104" width="220" height="24" rx="12" fill="#282116" stroke="#8a6b34"/>
  <text x="319" y="121" text-anchor="middle" class="micro gold">CRTP · PT1 · CNSP</text>
  <text x="58" y="175" class="serif white" font-size="42">{name}</text>
  <text x="60" y="202" class="mono soft">@{svg_escape(USERNAME)}  ·  PENETRATION TESTER / RED TEAMING</text>
  <path d="M58 221H782" class="rule"/>
  <text x="60" y="250" class="micro red">FOCUS</text>
  <text x="183" y="250" class="small white">Penetration testing  ·  Red Teaming</text>
  <text x="60" y="281" class="micro red">ENGINEER</text>
  <text x="183" y="281" class="small white">Security engineering  ·  Programming</text>
  <text x="60" y="312" class="micro red">FOUNDER</text>
  <text x="183" y="312" class="small white">Game Developer  ·  game-development startup</text>
  <text x="60" y="343" class="micro red">PROFESSOR</text>
  <text x="183" y="343" class="small white">College cybersecurity department</text>
  <text x="60" y="374" class="micro red">CERTIFIED</text>
  <text x="183" y="374" class="small white">CRTP  ·  PT1  ·  CNSP</text>

  <path d="M814 98V388" stroke="#3e352b"/>
  <rect x="837" y="104" width="304" height="274" rx="4" fill="#121011" stroke="#5a3031" class="card-edge"/>
  <path d="M837 104H1141" stroke="#d12f37" stroke-width="3"/>
  <text x="861" y="135" class="micro gold">BUILD STATUS</text>
  <circle cx="1109" cy="129" r="5" fill="#e2464d" class="pulse"/>
  <circle cx="1109" cy="129" r="11" fill="none" stroke="#e2464d" stroke-opacity=".35" class="pulse"/>
  <circle cx="989" cy="220" r="49" fill="#201719" stroke="#6e2d32" stroke-width="1"/>
  <circle cx="989" cy="220" r="39" fill="none" stroke="#d5ad52" stroke-opacity=".6" stroke-dasharray="3 6" class="inkline"/>
  <text x="989" y="234" text-anchor="middle" fill="#d5ad52" font-family="Georgia,'Times New Roman',serif" font-size="38">☕</text>
  <text x="989" y="300" text-anchor="middle" class="micro white">[ PANIC ]</text>
  <text x="989" y="327" text-anchor="middle" class="small soft">Rust compiler won. Send coffee...</text>
  <text x="989" y="352" text-anchor="middle" class="micro red">OPERATOR STILL DEBUGGING</text>
  <path d="M58 395H782" stroke="#d5ad52" stroke-opacity=".72" stroke-width="1" class="ambient-line"/>
"""
    return wrap_svg("Who I am", body, 426, "ID")


def contribution_values(year_data: dict[str, Any]) -> list[tuple[int, int]]:
    if "counts" in year_data and "levels" in year_data:
        return list(zip(
            (int(value) for value in year_data["counts"].split(",")),
            (int(value) for value in year_data["levels"]),
        ))
    days = year_data.get("days", [])
    if days and isinstance(days[0], dict):
        return [(int(day.get("count", 0)), int(day.get("level", 0))) for day in days]
    return [(int(day[0]), int(day[1])) for day in days]


def render_contributions(
    data: dict[str, Any], year_data: dict[str, Any] | None = None, *, trailing: bool = False
) -> str:
    contributions = data.get("contributions", {})
    years = sorted(contributions.get("years", []), key=lambda item: int(item.get("year", 0)), reverse=True)
    if year_data is None:
        year_data = years[0] if years else None
    if not year_data:
        body = '<text x="48" y="118" class="serif white">The public GitHub contribution history is not in this saved snapshot yet.</text>'
        return wrap_svg("Contribution history", body, 190, "05")

    year = int(year_data.get("year", 0))
    values = contribution_values(year_data)
    day_total = sum(count for count, _ in values)
    active_days = sum(1 for count, _ in values if count > 0)
    if trailing:
        expected_total = int(contributions.get("last_year", -1))
        stamp = iso_time(contributions.get("updated_at") or data.get("generated_at"))
        rolling_end = stamp.date() if stamp else dt.datetime.now(dt.timezone.utc).date()
        rolling_dates = [rolling_end - dt.timedelta(days=364 - index) for index in range(365)]
        rolling_values: list[tuple[int, int]] = []
        recent = contributions.get("last_year_days") or {}
        if recent.get("counts") and recent.get("levels"):
            recent_values = list(zip(
                (int(value) for value in recent["counts"].split(",")),
                (int(value) for value in recent["levels"]),
            ))
            recent_start = dt.date.fromisoformat(recent["from"])
            recent_by_date = {
                recent_start + dt.timedelta(days=index): value
                for index, value in enumerate(recent_values)
            }
            if all(date in recent_by_date for date in rolling_dates):
                rolling_values = [recent_by_date[date] for date in rolling_dates]

        if not rolling_values:
            # Older snapshots have complete daily calendars by year. Recompose the rolling
            # window from those verified days, and use it only when its sum matches GitHub's
            # independently reported trailing-year total.
            yearly_values: dict[dt.date, tuple[int, int]] = {}
            for item in contributions.get("years", []):
                item_year = int(item.get("year", 0))
                item_start = dt.date(item_year, 1, 1)
                for index, value in enumerate(contribution_values(item)):
                    yearly_values[item_start + dt.timedelta(days=index)] = value
            if all(date in yearly_values for date in rolling_dates):
                rolling_values = [yearly_values[date] for date in rolling_dates]

        rolling_total = sum(count for count, _ in rolling_values)
        if len(rolling_values) == len(rolling_dates) and rolling_total == expected_total:
            dates = rolling_dates
            values = rolling_values
            day_total = expected_total
            heading = contributions.get("last_year_heading", "contributions in the last year")
        else:
            # Keep the real current-year cells and label them as such rather than
            # presenting a mismatched daily window as a rolling-year calendar.
            dates = [dt.date(year, 1, 1) + dt.timedelta(days=index) for index in range(len(values))]
            heading = f"{fmt_count(day_total)} contributions in {year}"
    else:
        first_date = dt.date(year, 1, 1)
        dates = [first_date + dt.timedelta(days=index) for index in range(len(values))]
        heading = f"{fmt_count(day_total)} contributions in {year}"

    if len(dates) != len(values):
        raise GitHubAPIError(f"The saved {year} contribution calendar did not match its daily counts.")
    active_days = sum(1 for count, _ in values if count > 0)
    end_date = dates[-1] if dates else first_date
    grid_start = dates[0] - dt.timedelta(days=(dates[0].weekday() + 1) % 7)
    grid_x = 100
    step_x = 20
    step_y = 14
    cell_w = 13
    cell_h = 10
    row_start = 157
    greens = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
    body: list[str] = [
        '<rect x="34" y="82" width="1132" height="206" rx="5" fill="#0d0d0e" stroke="#3c3732" class="card-edge"/>',
        '<path d="M34 87H1166" stroke="#d12f37" stroke-opacity=".9" stroke-width="2"/>',
    ]
    count_text = fmt_count(day_total)
    body.append(f'''
  <text x="52" y="128" class="num gold">{svg_escape(count_text)}</text>
  <text x="196" y="111" class="micro white">{svg_escape(heading.upper())}</text>
  <text x="196" y="133" class="micro soft">{dates[0].strftime('%d %b %Y').upper()} — {end_date.strftime('%d %b %Y').upper()}  ·  {active_days} ACTIVE DAYS</text>
  <text x="893" y="111" text-anchor="end" class="micro soft">LESS</text>
''')
    legend_start = 912
    for level, color in enumerate(greens):
        body.append(f'<rect x="{legend_start + level * 22}" y="99" width="12" height="12" rx="2" fill="{color}" stroke="#39423c" stroke-width=".5"/>')
    body.append('<text x="1036" y="111" class="micro soft">MORE</text>')

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    week_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    seen_months: set[tuple[int, int]] = set()
    for date in dates:
        if (date.year, date.month) in seen_months:
            continue
        seen_months.add((date.year, date.month))
        week = (date - grid_start).days // 7
        body.append(f'<text x="{grid_x + week * step_x}" y="151" class="month soft">{month_names[date.month - 1]}</text>')

    for index, label in enumerate(week_names):
        if index in (0, 2, 4, 6):
            body.append(f'<text x="65" y="{row_start + index * step_y + 9}" text-anchor="middle" class="weekday soft">{label}</text>')
    for index, (count, level_value) in enumerate(values):
        date = dates[index]
        week = (date - grid_start).days // 7
        weekday = (date.weekday() + 1) % 7
        level = max(0, min(4, int(level_value)))
        color = greens[level]
        delay = ((week * .11) + (weekday * .07)) % 6.5
        title = f"{date.isoformat()} · {count} {'contribution' if count == 1 else 'contributions'}"
        body.append(
            f'<rect x="{grid_x + week * step_x}" y="{row_start + weekday * step_y}" '
            f'width="{cell_w}" height="{cell_h}" rx="2" fill="{color}" '
            f'class="heat-cell" style="--delay:{delay:.2f}s"><title>{svg_escape(title)}</title></rect>'
        )
    body.append(f'<text x="{grid_x}" y="277" class="micro soft">{year}</text>')
    body.append(f'<path d="M{grid_x + 52} 273H{grid_x + 52 + int(950 * active_days / max(1, len(values)))}" stroke="#d12f37" stroke-width="2" class="inkline"/>')
    return wrap_svg(f"Contribution calendar {year}", "".join(body), 304, "05")


def render_toolkit() -> str:
    colors = [("D5AD52", "09090A"), ("F4F0E9", "09090A"), ("D12F37", "F4F0E9")]
    marks = {
        "Rust": "R", "WebAssembly": "WASM", "C": "C", "C++": "C++", "C Sharp": "C#",
        "Java": "J", "JavaScript": "JS", "Python": "Py", "PowerShell": "PS", "HTML5": "H5",
        "CSS3": "C3", "R": "R", "AWS": "AWS", "Azure": "Az", "Google Cloud": "GC",
        "Cloudflare": "CF", "Django": "Dj", "Flask": "Fl", "FastAPI": "API", "Node.js": "JS",
        "MySQL": "SQL", "MongoDB": "Mg", "PostgreSQL": "Pg", "SQLite": "Sq", "Git": "Git",
        "GitHub": "GH", "GitLab": "GL", "Nginx": "N", "Jira": "J", "Unity": "U",
        "Unreal Engine": "UE", "TensorFlow": "TF", "NumPy": "NP", "Adobe": "Ae",
        "Premiere Pro": "Pr", "After Effects": "Ae",
    }
    body: list[str] = ['<rect x="34" y="82" width="1132" height="470" rx="5" fill="#0d0d0e" stroke="#463a30" class="card-edge"/>']
    badge_clips: list[str] = []
    badge_count = 0
    top = 98
    for group_index, (group, items) in enumerate(SHIELD_GROUPS.items()):
        y_title = top + group_index * 142
        body.append(f'<text x="58" y="{y_title + 16}" class="micro gold">{svg_escape(group)}</text>')
        body.append(f'<path d="M58 {y_title + 28}H1142" stroke="#3d3834"/>')
        x = 58
        y = y_title + 43
        for label, _logo in items:
            color, logo_color = colors[badge_count % len(colors)]
            width = min(196, max(74, 48 + len(label) * 7.3))
            if x + width > 1142:
                x = 58
                y += 31
            badge_clips.append(f'<rect x="{x}" y="{y}" width="{width}" height="21" rx="3"/>')
            body.append(
                f'<rect x="{x}" y="{y}" width="{width}" height="21" rx="3" fill="#151414" '
                f'stroke="#{color}" stroke-opacity=".72" stroke-width=".8" class="card-edge"/>'
                f'<path d="M{x + 3} {y + 3}H{x + 22}V{y + 18}H{x + 3}Z" fill="#{color}" fill-opacity=".94"/>'
                f'<text x="{x + 12.5}" y="{y + 14.5}" text-anchor="middle" fill="#{logo_color}" class="badge-glyph">{svg_escape(marks.get(label, label[:2]))}</text>'
                f'<text x="{x + 29}" y="{y + 15}" fill="#f4f0e9" class="badge-label">{svg_escape(label)}</text>'
            )
            x += width + 12
            badge_count += 1

    clips = ''.join(badge_clips)
    body.append(f'<defs><clipPath id="shield-mask">{clips}</clipPath></defs>')
    body.append('<rect x="-120" y="140" width="84" height="360" fill="url(#shieldShine)" opacity=".44" clip-path="url(#shield-mask)" class="shield-sweep"/>')
    body.append('<text x="58" y="529" class="micro soft">LANGUAGES  ·  CLOUD  ·  SECURITY  ·  GAME DEVELOPMENT  ·  CREATIVE TOOLS</text>')
    svg = wrap_svg("Toolkit", "".join(body), 572, "06")
    svg = svg.replace(
        '<defs>\n    <pattern id="grain"',
        '<defs>\n    <linearGradient id="shieldShine" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".54"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>\n    <pattern id="grain"',
        1,
    )
    svg = svg.replace(
        '.heat-cell{animation:heat 6.5s ease-in-out infinite;animation-delay:var(--delay,0s)}',
        '.heat-cell{animation:heat 6.5s ease-in-out infinite;animation-delay:var(--delay,0s)}\n      .shield-sweep{animation:shieldSweep 8.5s ease-in-out infinite;}',
        1,
    )
    svg = svg.replace(
        '@keyframes heat{0%,100%{filter:brightness(1)}50%{filter:brightness(1.24)}}',
        '@keyframes heat{0%,100%{filter:brightness(1)}50%{filter:brightness(1.24)}}\n      @keyframes shieldSweep{0%,8%{transform:translateX(0);opacity:0}14%{opacity:.55}28%,100%{transform:translateX(1370px);opacity:0}}',
        1,
    )
    return svg


def render_recent_activity(data: dict[str, Any]) -> str:
    entries = data.get("activity", [])[:8]
    body = ['<path d="M66 105V459" stroke="#8a262d" stroke-width="2" stroke-dasharray="3 7" class="inkline"/>']
    if not entries:
        body.append('<text x="96" y="159" class="small soft">No recent events</text>')
    for index, event in enumerate(entries):
        y = 124 + index * 42
        summary = svg_escape(short_text(event.get("summary") or event_summary(event), 26))
        repository = svg_escape(short_text(event.get("repo"), 52))
        time = iso_time(event.get("created_at"))
        date = time.strftime("%d %b · %H:%M UTC") if time else "time unavailable"
        link = svg_escape(event.get("url", ""))
        body.append(f"""
  <circle cx="66" cy="{y - 4}" r="5" fill="#d5ad52" stroke="#09090a" stroke-width="2" class="pulse"/>
  <text x="91" y="{y}" class="micro red">{date.upper()}</text>
  <text x="286" y="{y}" class="small white">{summary}</text>
  <a xlink:href="{link}" target="_blank" rel="noopener noreferrer">
    <text x="1152" y="{y}" text-anchor="end" class="mono gold">{repository} ↗</text>
  </a>
""")
    return wrap_svg("Recent activity", "".join(body), 490, "03")


def render_event_trace(data: dict[str, Any]) -> str:
    events = [
        event for event in data.get("activity", [])
        if iso_time(event.get("created_at"))
    ]
    events.sort(key=lambda event: iso_time(event.get("created_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    events = events[-30:]
    x_start, x_end = 202, 1152
    lanes = {
        "PUSH": (119, "#d12f37"),
        "STAR": (173, "#d5ad52"),
        "PULL": (227, "#f4f0e9"),
        "ISSUE": (281, "#b7aea2"),
        "OTHER": (335, "#78736c"),
    }
    body: list[str] = []
    for label, (y, color) in lanes.items():
        body.append(f'<text x="47" y="{y + 5}" class="micro soft">{label}</text>')
        body.append(f'<path d="M{x_start} {y}H{x_end}" stroke="#312d29" stroke-width="1"/>')

    if events:
        points: list[tuple[float, int, str, str]] = []
        denominator = max(1, len(events) - 1)
        for index, event in enumerate(events):
            kind = event.get("type", "")
            if kind == "PushEvent":
                lane = "PUSH"
            elif kind == "WatchEvent":
                lane = "STAR"
            elif kind == "PullRequestEvent":
                lane = "PULL"
            elif kind in {"IssuesEvent", "IssueCommentEvent"}:
                lane = "ISSUE"
            else:
                lane = "OTHER"
            y, color = lanes[lane]
            x = x_start + (x_end - x_start) * index / denominator
            event_time = iso_time(event.get("created_at"))
            when = event_time.strftime("%d %b %Y · %H:%M UTC") if event_time else ""
            description = event.get("summary") or event_summary(event)
            points.append((x, y, color, f"{when} · {description}"))
        trace = " ".join(
            f'{"M" if index == 0 else "L"}{x:.1f} {y}'
            for index, (x, y, _, _) in enumerate(points)
        )
        body.append(f'<path d="{trace}" fill="none" stroke="#d5ad52" stroke-opacity=".42" stroke-width="2" class="inkline"/>')
        for x, y, color, description in points:
            body.append(f'<circle cx="{x:.1f}" cy="{y}" r="6" fill="{color}" stroke="#09090a" stroke-width="2" class="pulse"><title>{svg_escape(description)}</title></circle>')
        oldest = iso_time(events[0].get("created_at"))
        newest = iso_time(events[-1].get("created_at"))
        left_date = oldest.strftime("%d %b %Y").upper() if oldest else ""
        right_date = newest.strftime("%d %b %Y").upper() if newest else ""
        body.append(f'<text x="{x_start}" y="382" class="micro soft">{left_date}</text>')
        body.append(f'<text x="{x_end}" y="382" text-anchor="end" class="micro soft">{right_date}</text>')
    else:
        body.append('<text x="600" y="245" text-anchor="middle" class="small soft">NO EVENTS</text>')

    return wrap_svg("Event trace", "".join(body), 415, "04")


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def update_readme_contribution_years(data: dict[str, Any]) -> None:
    readme = Path("README.md")
    if not readme.exists():
        return
    source = readme.read_text(encoding="utf-8")
    start_marker = "<!-- CONTRIBUTION_YEARS_START -->"
    end_marker = "<!-- CONTRIBUTION_YEARS_END -->"
    if start_marker not in source or end_marker not in source:
        raise GitHubAPIError("README.md is missing the generated contribution-year markers.")

    years = sorted(
        data.get("contributions", {}).get("years", []),
        key=lambda item: int(item.get("year", 0)),
        reverse=True,
    )
    latest_year = int(years[0].get("year", 0)) if years else None
    rows: list[str] = []
    for year_data in years:
        year = int(year_data.get("year", 0))
        if year == latest_year:
            label = data.get("contributions", {}).get(
                "last_year_heading", f"{fmt_count(year_data.get('total', 0))} contributions in the last year"
            )
        else:
            label = f"{fmt_count(year_data.get('total', 0))} contributions in {year}"
        is_open = " open" if year == latest_year else ""
        rows.append(
            f'<details name="contribution-calendar" id="contribution-{year}"{is_open}>\n'
            f"<summary><strong>{year}</strong> · {html.escape(label)}</summary>\n\n"
            f'<p align="center"><img src="assets/panels/contributions-{year}.svg" '
            f'alt="Daily GitHub contributions for {year}" width="100%"></p>\n'
            f"</details>"
        )
    replacement = f"{start_marker}\n\n" + "\n\n".join(rows) + f"\n\n{end_marker}"
    before, _, remainder = source.partition(start_marker)
    _, _, after = remainder.partition(end_marker)
    readme.write_text(before + replacement + after, encoding="utf-8")


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
        cached = False

    created_at = data.get("created_at") or old.get("created_at")
    if created_at:
        try:
            data["created_at"] = created_at
            data["contributions"] = fetch_contribution_history(
                USERNAME,
                created_at,
                dt.datetime.now(dt.timezone.utc).date(),
            )
            print(
                f"Fetched {len(data['contributions']['years'])} contribution years; "
                f"the current trailing-year total is {fmt_count(data['contributions']['last_year'])}."
            )
        except GitHubAPIError as exc:
            if not data.get("contributions"):
                raise
            print(f"{exc}\nUsing the last saved contribution history.")
    elif not data.get("contributions"):
        raise GitHubAPIError("The saved snapshot needs the public account creation date before history can be rendered.")

    write_file(METADATA_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    write_file(OUT_DIR / "whoami.svg", render_whoami(data))
    write_file(OUT_DIR / "overview.svg", render_overview(data))
    write_file(OUT_DIR / "repositories.svg", render_repositories(data))
    write_file(OUT_DIR / "trophies.svg", render_trophies(data))
    write_file(OUT_DIR / "recent-activity.svg", render_recent_activity(data))
    write_file(OUT_DIR / "telemetry.svg", render_event_trace(data))
    write_file(OUT_DIR / "toolkit.svg", render_toolkit())
    years = sorted(data.get("contributions", {}).get("years", []), key=lambda item: int(item.get("year", 0)), reverse=True)
    if years:
        write_file(OUT_DIR / "contributions.svg", render_contributions(data, years[0], trailing=True))
        for year_data in years:
            year = int(year_data.get("year", 0))
            write_file(
                OUT_DIR / f"contributions-{year}.svg",
                render_contributions(data, year_data, trailing=year == int(years[0].get("year", 0))),
            )
    else:
        write_file(OUT_DIR / "contributions.svg", render_contributions(data))
    update_readme_contribution_years(data)
    print(f"Rendered profile panels and {len(years)} single-year calendars from the {'cached' if cached else 'current'} GitHub snapshot dated {data.get('generated_at', 'unknown')}.")


if __name__ == "__main__":
    main()

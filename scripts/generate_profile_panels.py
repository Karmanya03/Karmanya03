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

    if expected_year is not None:
        expected_days = (dt.date(expected_year + 1, 1, 1) - dt.date(expected_year, 1, 1)).days
        days.sort(key=lambda day: day["date"])
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
    return {
        "created_at": created_at,
        "last_year": trailing["total"],
        "last_year_heading": trailing["heading"],
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
      .month{{font:700 12px 'Segoe UI',Arial,sans-serif;letter-spacing:.5px}}
      .weekday{{font:700 11px 'Segoe UI',Arial,sans-serif}}
      .yearnum{{font:600 30px Georgia,'Times New Roman',serif;font-variant-numeric:tabular-nums}}
      @keyframes ink{{to{{stroke-dashoffset:-304}}}}
      @keyframes rail{{to{{stroke-dashoffset:-1546}}}}
      @keyframes edge{{0%,100%{{stroke-opacity:.48}}50%{{stroke-opacity:.92}}}}
      @keyframes pulse{{0%,100%{{opacity:.72;transform:scale(.94)}}50%{{opacity:1;transform:scale(1.06)}}}}
      @keyframes warmth{{0%,100%{{opacity:.2}}50%{{opacity:.55}}}}
      @keyframes heat{{0%,100%{{filter:brightness(1)}}50%{{filter:brightness(1.24)}}}}
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
  <rect x="{x + 20}" y="{y + 192}" width="{bar_w}" height="5" rx="2.5" fill="{rank_color}"/>
  <text x="{center}" y="{y + 216}" text-anchor="middle" class="micro soft">{"NO SNAPSHOT" if missing else target_label}</text>
""")
    panel_height = 96 + rows * 248 + 8
    return wrap_svg("Trophy case", "".join(body), panel_height, "02")


def render_whoami(data: dict[str, Any]) -> str:
    name = svg_escape(data.get("name") or "Karmanya Ravindra")
    body = f"""
  <rect x="34" y="83" width="1132" height="284" rx="5" fill="#0d0d0e" stroke="#463a30" class="card-edge"/>
  <path d="M34 88V361" stroke="#d12f37" stroke-width="4"/>
  <rect x="58" y="104" width="136" height="24" rx="12" fill="#301316" stroke="#8f312f"/>
  <text x="126" y="121" text-anchor="middle" class="micro white">IDENTITY / 01</text>
  <rect x="209" y="104" width="178" height="24" rx="12" fill="#282116" stroke="#8a6b34"/>
  <text x="298" y="121" text-anchor="middle" class="micro gold">CRTP CERTIFIED</text>
  <text x="58" y="175" class="serif white" font-size="42">{name}</text>
  <text x="60" y="202" class="mono soft">@{svg_escape(USERNAME)}  ·  RED TEAM PROFESSIONAL</text>
  <path d="M58 221H782" class="rule"/>
  <text x="60" y="250" class="micro red">MISSION</text>
  <text x="183" y="250" class="small white">Offensive security  ·  Secure engineering</text>
  <text x="60" y="282" class="micro red">MAKES</text>
  <text x="183" y="282" class="small white">Game worlds · Unity / Unreal   /   XR · AR / VR</text>
  <text x="60" y="314" class="micro red">TOOLCHAIN</text>
  <text x="183" y="314" class="small white">Rust · WebAssembly · C / C++</text>

  <path d="M814 98V350" stroke="#3e352b"/>
  <rect x="837" y="104" width="304" height="238" rx="4" fill="#121011" stroke="#5a3031" class="card-edge"/>
  <path d="M837 104H1141" stroke="#d12f37" stroke-width="3"/>
  <text x="861" y="135" class="micro gold">CURRENT PROCESS</text>
  <circle cx="1109" cy="129" r="5" fill="#e2464d" class="pulse"/>
  <circle cx="1109" cy="129" r="11" fill="none" stroke="#e2464d" stroke-opacity=".35" class="pulse"/>
  <circle cx="989" cy="217" r="47" fill="#201719" stroke="#6e2d32" stroke-width="1"/>
  <circle cx="989" cy="217" r="37" fill="none" stroke="#d5ad52" stroke-opacity=".6" stroke-dasharray="3 6" class="inkline"/>
  <text x="989" y="231" text-anchor="middle" fill="#d5ad52" font-family="Georgia,'Times New Roman',serif" font-size="38">☕</text>
  <text x="989" y="290" text-anchor="middle" class="micro white">[ PANIC ]</text>
  <text x="989" y="315" text-anchor="middle" class="small soft">Rust compiler won. Send coffee...</text>
  <path d="M58 349H782" stroke="#d5ad52" stroke-opacity=".72" stroke-width="1" class="ambient-line"/>
"""
    return wrap_svg("Who I am", body, 390, "ID")


def render_contributions(data: dict[str, Any]) -> str:
    contributions = data.get("contributions", {})
    years = sorted(contributions.get("years", []), key=lambda item: int(item.get("year", 0)), reverse=True)
    if not years:
        body = '<text x="48" y="118" class="serif white">The public GitHub contribution history is not in this saved snapshot yet.</text>'
        return wrap_svg("Contribution history", body, 190, "05")

    grid_x = 87
    step_x = 14
    step_y = 12
    cell_w = 10
    cell_h = 9
    row_start = 160
    row_height = 126
    greens = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
    body: list[str] = []
    last_year = contributions.get("last_year")
    last_year_text = fmt_count(last_year) if last_year is not None else "—"
    first_year = min(int(item.get("year", 0)) for item in years)
    last_calendar_year = max(int(item.get("year", 0)) for item in years)
    body.append(f'''
  <text x="48" y="119" class="num gold">{svg_escape(last_year_text)}</text>
  <text x="51" y="144" class="micro soft">CONTRIBUTIONS · LAST YEAR</text>
  <text x="456" y="122" class="micro white">{first_year} — {last_calendar_year}</text>
  <text x="456" y="145" class="micro soft">DAY BY DAY · EVERY PROFILE YEAR</text>
  <text x="806" y="117" class="micro soft">LESS</text>
''')
    legend_start = 860
    for level, color in enumerate(greens):
        body.append(f'<rect x="{legend_start + level * 22}" y="106" width="12" height="12" rx="2" fill="{color}" stroke="#39423c" stroke-width=".5"/>')
    body.append('<text x="982" y="117" class="micro soft">MORE</text>')

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    week_names = ["S", "M", "T", "W", "T", "F", "S"]
    for year_index, year_data in enumerate(years):
        year = int(year_data.get("year", 0))
        y = row_start + year_index * row_height
        body.append(f'<rect x="34" y="{y}" width="1132" height="118" rx="4" fill="#0d0d0e" stroke="#3c3732" class="card-edge"/>')
        body.append(f'<path d="M34 {y + 4}H1166" stroke="#d5ad52" stroke-opacity=".65" stroke-width="1"/>')
        jan1 = dt.date(year, 1, 1)
        grid_start = jan1 - dt.timedelta(days=(jan1.weekday() + 1) % 7)
        for month_num, month_name in enumerate(month_names, start=1):
            month_day = dt.date(year, month_num, 1)
            week = (month_day - grid_start).days // 7
            body.append(f'<text x="{grid_x + week * step_x}" y="{y + 19}" class="month soft">{month_name}</text>')
        for day_index, label in enumerate(week_names):
            body.append(f'<text x="58" y="{y + 40 + day_index * step_y}" text-anchor="middle" class="weekday soft">{label}</text>')

        days = year_data.get("days", [])
        if "counts" in year_data and "levels" in year_data:
            day_values = list(zip(
                (int(value) for value in year_data["counts"].split(",")),
                (int(value) for value in year_data["levels"]),
            ))
            day_total = sum(count for count, _ in day_values)
            active_days = sum(1 for count, _ in day_values if count > 0)
        elif days and isinstance(days[0], dict):
            day_values = [
                (int(day.get("count", 0)), int(day.get("level", 0)))
                for day in days
            ]
            day_total = sum(int(day.get("count", 0)) for day in days)
            active_days = sum(1 for day in days if int(day.get("count", 0)) > 0)
        else:
            day_values = [(int(day[0]), int(day[1])) for day in days]
            day_total = sum(int(day[0]) for day in days)
            active_days = sum(1 for day in days if int(day[0]) > 0)
        year_start = dt.date(year, 1, 1)
        for day_index, (count, level_value) in enumerate(day_values):
            date = (
                dt.date.fromisoformat(days[day_index]["date"])
                if days and isinstance(days[0], dict)
                else year_start + dt.timedelta(days=day_index)
            )
            week = (date - grid_start).days // 7
            weekday = (date.weekday() + 1) % 7
            level = max(0, min(4, int(level_value)))
            color = greens[level]
            rect = (
                f'<rect x="{grid_x + week * step_x}" y="{y + 30 + weekday * step_y}" '
                f'width="{cell_w}" height="{cell_h}" rx="2" fill="{color}"'
            )
            if count or level:
                delay = ((week * .11) + (weekday * .07) + (year_index * .35)) % 5.5
                title = f"{date.isoformat()} · {count} {'contribution' if count == 1 else 'contributions'}"
                body.append(f'{rect} class="heat-cell" style="--delay:{delay:.2f}s"><title>{svg_escape(title)}</title></rect>')
            else:
                body.append(f"{rect}/>")
        body.append(f'<text x="884" y="{y + 55}" class="yearnum white">{year}</text>')
        body.append(f'<text x="1144" y="{y + 52}" text-anchor="end" class="micro gold">{fmt_count(day_total)} CONTRIBUTIONS</text>')
        body.append(f'<text x="1144" y="{y + 76}" text-anchor="end" class="micro soft">{active_days} ACTIVE DAYS</text>')
        body.append(f'<path d="M884 {y + 90}H1144" stroke="#3b3630"/>')
        body.append(f'<path d="M884 {y + 90}H{884 + int(260 * active_days / max(1, len(days)))}" stroke="#d12f37" stroke-width="2" class="inkline"/>')

    height = row_start + len(years) * row_height + 6
    return wrap_svg("Contribution history", "".join(body), height, "05")


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
    write_file(OUT_DIR / "contributions.svg", render_contributions(data))
    print(f"Rendered 7 panels from the {'cached' if cached else 'current'} GitHub snapshot dated {data.get('generated_at', 'unknown')}.")


if __name__ == "__main__":
    main()

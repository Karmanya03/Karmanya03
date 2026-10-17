#!/usr/bin/env python3
"""Render accurate, regularly refreshed GitHub profile panels from public data."""
from __future__ import annotations

import concurrent.futures
import copy
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
REFRESH_HISTORICAL_CONTRIBUTIONS = os.getenv("REFRESH_CONTRIBUTION_HISTORY", "false").lower() in {"1", "true", "yes"}

RANK_NAMES = ["C", "B", "A", "AA", "AAA", "S", "SS", "SSS"]
RANK_ORDER = {rank: index for index, rank in enumerate(RANK_NAMES)}
TROPHY_RULES = {
    "Stars": ([1, 10, 30, 50, 100, 200, 700, 2000], "✦"),
    "Followers": ([1, 10, 20, 50, 100, 200, 400, 1000], "◎"),
    "Repositories": ([1, 10, 20, 30, 35, 40, 45, 50], "▤"),
    "Commits": ([1, 10, 100, 200, 500, 1000, 2000, 4000], "↟"),
    "Pull requests": ([1, 10, 20, 50, 100, 200, 500, 1000], "⑂"),
    "Issues": ([1, 10, 20, 50, 100, 200, 500, 1000], "!"),
    "Languages": ([10, 15, 20, 25, 30, 40, 50, 75], "〈/〉"),
}

ACHIEVEMENT_COLORS = {
    "starstruck": "#d5ad52",
    "pull-shark": "#68b7d3",
    "yolo": "#e2464d",
    "quickdraw": "#d5ad52",
    "galaxy-brain": "#a996e8",
    "pair-extraordinaire": "#91c99b",
    "arctic-code-vault-contributor": "#92cbd1",
    "mars-2020-contributor": "#e78a68",
}
ACHIEVEMENT_GLYPHS = {
    "starstruck": "✦",
    "pull-shark": "↗",
    "yolo": "!",
    "quickdraw": "⌁",
    "galaxy-brain": "✧",
    "pair-extraordinaire": "Ⅱ",
    "arctic-code-vault-contributor": "❄",
    "mars-2020-contributor": "◉",
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


class GitHubAchievementParser(HTMLParser):
    """Read only earned achievement links visible on a public GitHub profile."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.badges: list[dict[str, str]] = []
        self.current: dict[str, Any] | None = None
        self.seen_achievement_page = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if "achievement" in " ".join(str(value or "") for value in attributes.values()).lower():
            self.seen_achievement_page = True
        if tag != "a":
            if self.current and tag == "img":
                alt = (attributes.get("alt") or "").strip()
                if alt:
                    self.current["name"] = alt
            return

        href = attributes.get("href") or ""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        slug = next(iter(params.get("achievement", [])), "").strip()
        if not slug:
            return
        self.seen_achievement_page = True
        label = attributes.get("aria-label") or attributes.get("title") or ""
        self.current = {"slug": slug, "name": label, "url": href, "text": []}

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["text"].append(data)
        if "achievement" in data.lower():
            self.seen_achievement_page = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self.current is None:
            return
        item = self.current
        name = str(item.get("name") or "").strip()
        name = re.sub(r"^achievement\s*:\s*", "", name, flags=re.IGNORECASE).strip()
        if not name:
            text = " ".join(" ".join(item["text"]).split())
            name = re.sub(r"^achievement\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        if not name:
            name = str(item["slug"]).replace("-", " ").title()
        self.badges.append({
            "slug": str(item["slug"]),
            "name": name,
            "url": str(item["url"]),
        })
        self.current = None


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
        raise GitHubAPIError(f"GitHub page request failed for {path}: {exc}") from exc


def parse_github_achievements(page: str) -> list[dict[str, str]]:
    parser = GitHubAchievementParser()
    parser.feed(page)
    if not parser.seen_achievement_page:
        raise GitHubAPIError("GitHub's public profile did not expose an achievement section.")
    unique: dict[str, dict[str, str]] = {}
    for badge in parser.badges:
        unique[badge["slug"]] = badge
    return sorted(unique.values(), key=lambda badge: badge["name"].casefold())


def fetch_github_achievements(username: str) -> list[dict[str, str]]:
    page = github_page(f"/{urllib.parse.quote(username)}?tab=achievements")
    return parse_github_achievements(page)


def valid_cached_year(item: dict[str, Any], year: int) -> bool:
    days = (dt.date(year + 1, 1, 1) - dt.date(year, 1, 1)).days
    try:
        counts = [int(value) for value in str(item.get("counts", "")).split(",")]
        levels = str(item.get("levels", ""))
        return (
            int(item.get("year", 0)) == year
            and len(counts) == days
            and len(levels) == days
            and all(0 <= level <= 4 for level in map(int, levels))
            and sum(counts) == int(item.get("total", -1))
        )
    except (TypeError, ValueError):
        return False


def fetch_contribution_history(
    username: str,
    created_at: str,
    today: dt.date,
    cached: dict[str, Any] | None = None,
    *,
    refresh_historical: bool = False,
) -> dict[str, Any]:
    created = iso_time(created_at)
    if not created:
        raise GitHubAPIError("The public profile did not include its account creation date.")
    cached_years = {
        int(item.get("year", 0)): item
        for item in (cached or {}).get("years", [])
        if isinstance(item, dict)
    }
    years: list[dict[str, Any]] = []
    for year in range(created.year, today.year + 1):
        saved = cached_years.get(year)
        if year < today.year and not refresh_historical and saved and valid_cached_year(saved, year):
            years.append(saved)
            continue
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


def fetch_languages(
    repos: list[dict[str, Any]], cached: dict[str, Any] | None = None,
) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    totals: dict[str, int] = {}
    owned = [repo for repo in repos if not repo.get("fork")]
    previous = cached or {}
    refreshed: dict[str, dict[str, Any]] = {}
    fetch: list[tuple[dict[str, Any], str]] = []
    for repo in owned:
        name = str(repo.get("full_name") or "")
        pushed_at = str(repo.get("pushed_at") or repo.get("updated_at") or "")
        saved = previous.get(name, {})
        saved_languages = saved.get("languages") if isinstance(saved, dict) else None
        if isinstance(saved, dict) and saved.get("pushed_at") == pushed_at and isinstance(saved_languages, dict):
            refreshed[name] = {"pushed_at": pushed_at, "languages": saved_languages}
        else:
            fetch.append((repo, pushed_at))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {
            pool.submit(fetch_repo_languages, repo): (repo, pushed_at)
            for repo, pushed_at in fetch
        }
        try:
            for job in concurrent.futures.as_completed(jobs):
                repo, pushed_at = jobs[job]
                name = str(repo.get("full_name") or "")
                refreshed[name] = {"pushed_at": pushed_at, "languages": job.result()}
        except Exception:
            for job in jobs:
                job.cancel()
            raise
    for saved in refreshed.values():
        for language, size in saved["languages"].items():
            totals[language] = totals.get(language, 0) + int(size)
    return dict(sorted(totals.items(), key=lambda pair: pair[1], reverse=True)), refreshed


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


def fetch_snapshot(username: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    user = api_get(f"/users/{username}")
    if not isinstance(user, dict) or not user.get("login"):
        raise GitHubAPIError("The user endpoint returned an unexpected response.")

    repos = fetch_repositories(username)
    events = api_get(f"/users/{username}/events/public?per_page=30")
    if not isinstance(events, list):
        raise GitHubAPIError("The public-events endpoint returned an unexpected response.")

    languages, language_cache = fetch_languages(repos, (previous or {}).get("language_cache", {}))
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
        "language_cache": language_cache,
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


def diagonal_sheen(
    x: int,
    y: int,
    width: int,
    height: int,
    key: str,
    *,
    tint: str = "#f4f0e9",
    duration: str = "9s",
    delay: str = "0s",
    opacity: str = ".16",
    radius: int = 6,
    gradient_id: str | None = None,
) -> str:
    """A clipped down-right light pass for a single card, paced per section."""
    start_x, start_y = x - height - 100, y + height + 100
    band = width + height + 180
    travel_x, travel_y = width * .9 + 50, height * .9 + 50
    light_id = gradient_id or f"{key}-light"
    gradient_definition = "" if gradient_id else f'''<linearGradient id="{light_id}" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{tint}" stop-opacity="0"/>
    <stop offset=".46" stop-color="{tint}" stop-opacity=".08"/>
    <stop offset=".56" stop-color="{tint}" stop-opacity=".92"/>
    <stop offset=".66" stop-color="{tint}" stop-opacity=".12"/>
    <stop offset="1" stop-color="{tint}" stop-opacity="0"/>
  </linearGradient>'''
    return f'''<defs>
  <clipPath id="{key}-clip"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}"/></clipPath>
{gradient_definition}
</defs>
<g clip-path="url(#{key}-clip)" opacity="{opacity}">
  <path d="M{start_x} {start_y}h54l{band} -{band}h-54Z" fill="url(#{light_id})">
    <animateTransform attributeName="transform" type="translate" values="{-travel_x},{-travel_y};{travel_x},{travel_y}" dur="{duration}" begin="{delay}" repeatCount="indefinite"/>
  </path>
</g>'''


def horizontal_sheen(
    x: int,
    y: int,
    width: int,
    height: int,
    key: str,
    *,
    tint: str = "#d5ad52",
    duration: str = "8s",
    delay: str = "0s",
    opacity: str = ".16",
    radius: int = 4,
) -> str:
    """A short horizontal glass pass for repository cards."""
    band_width = max(64, int(width * .18))
    return f'''<defs>
  <clipPath id="{key}-clip"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}"/></clipPath>
  <linearGradient id="{key}-light" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{tint}" stop-opacity="0"/>
    <stop offset=".42" stop-color="{tint}" stop-opacity=".10"/>
    <stop offset=".52" stop-color="#fffaf0" stop-opacity=".92"/>
    <stop offset=".62" stop-color="{tint}" stop-opacity=".18"/>
    <stop offset="1" stop-color="{tint}" stop-opacity="0"/>
  </linearGradient>
</defs>
<g clip-path="url(#{key}-clip)" opacity="{opacity}">
  <rect x="{x - band_width}" y="{y}" width="{band_width}" height="{height}" fill="url(#{key}-light)">
    <animateTransform attributeName="transform" type="translate" values="0,0;{width + band_width},0" dur="{duration}" begin="{delay}" repeatCount="indefinite"/>
  </rect>
</g>'''


def wrap_svg(title: str, body: str, height: int, index: str, *, show_header: bool = True) -> str:
    header = f'''
  <path d="M0 2H1200" stroke="#f4f0e9" stroke-opacity=".48" stroke-width="1" class="ambient-line"/>
  <rect x="30" y="25" width="42" height="27" rx="2" class="red"/>
  <text x="51" y="44" text-anchor="middle" class="micro white">{svg_escape(index)}</text>
  <text x="87" y="43" class="micro white">{svg_escape(title.upper())}</text>
  <path d="M30 62H1170" class="rule"/>
''' if show_header else ""
    header = header.strip()
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
      .footer-sweep-glow{{stroke-dasharray:188 712;animation:footerSweep 8.5s linear infinite;filter:url(#footer-glow)}}
      .footer-sweep{{stroke-dasharray:188 712;animation:footerSweep 8.5s linear infinite}}
      .card-edge{{animation:edge 6s ease-in-out infinite}}
      .quote-word{{animation:quoteWord 5.2s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}
      .quote-word-late{{animation:quoteWord 5.2s ease-in-out infinite 1.8s;transform-box:fill-box;transform-origin:center}}
      .quote-bar-glow{{animation:quoteBar 7.2s ease-in-out infinite}}
      .quote-rail{{stroke-dasharray:112 566;animation:quoteRail 7.8s cubic-bezier(.35,0,.22,1) infinite}}
      .pulse{{transform-box:fill-box;transform-origin:center;animation:pulse 4.8s ease-in-out infinite}}
      .warmth{{animation:warmth 5.8s ease-in-out infinite}}
      .heat-cell{{transform-box:fill-box;transform-origin:center;animation:heat 3.4s ease-in-out infinite;animation-delay:var(--delay,0s)}}
      .rank-meter{{animation:rankGlow 4.2s ease-in-out infinite}}
      .month{{font:700 13px 'Segoe UI',Arial,sans-serif;letter-spacing:.5px}}
      .weekday{{font:700 12px 'Segoe UI',Arial,sans-serif}}
      .yearnum{{font:600 30px Georgia,'Times New Roman',serif;font-variant-numeric:tabular-nums}}
      .badge-label{{font:600 15px 'Segoe UI',Arial,sans-serif;letter-spacing:.15px}}
      .badge-glyph{{font:700 16px Consolas,'Courier New',monospace}}
      .trophy-orbit{{transform-box:fill-box;transform-origin:center;animation:trophyOrbit 11s linear infinite}}
      .achievement-bloom{{transform-box:fill-box;transform-origin:center;animation:achievementBloom 6.4s ease-in-out infinite}}
      @keyframes ink{{to{{stroke-dashoffset:-304}}}}
      @keyframes rail{{to{{stroke-dashoffset:-1546}}}}
      @keyframes footerSweep{{from{{stroke-dashoffset:900}}to{{stroke-dashoffset:0}}}}
      @keyframes edge{{0%,100%{{stroke-opacity:.48}}50%{{stroke-opacity:.92}}}}
      @keyframes pulse{{0%,100%{{opacity:.72;transform:scale(.94)}}50%{{opacity:1;transform:scale(1.06)}}}}
      @keyframes warmth{{0%,100%{{opacity:.2}}50%{{opacity:.55}}}}
      @keyframes heat{{0%,100%{{filter:brightness(.9);opacity:.8}}50%{{filter:brightness(1.7);opacity:1}}}}
      @keyframes rankGlow{{0%,100%{{opacity:.74}}50%{{opacity:1}}}}
      @keyframes quoteWord{{0%,100%{{opacity:.78}}45%{{opacity:1}}}}
      @keyframes quoteBar{{0%,100%{{opacity:.06}}50%{{opacity:.28}}}}
      @keyframes quoteRail{{from{{stroke-dashoffset:678}}to{{stroke-dashoffset:0}}}}
      @keyframes trophyOrbit{{to{{transform:rotate(360deg)}}}}
      @keyframes achievementBloom{{0%,100%{{opacity:.52;transform:scale(.92)}}50%{{opacity:1;transform:scale(1.06)}}}}
      @media (prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;scroll-behavior:auto!important}}}}
    </style>
  </defs>
  <rect width="1200" height="{height}" rx="5" class="canvas"/>
  <rect width="1200" height="{height}" rx="5" class="grain"/>
  <path d="M0 0H1200" stroke="#d12f37" stroke-width="5"/>
  {header}
  {body.strip()}
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
        body.append(diagonal_sheen(
            x - 8, 91, 252, 103, f'overview-sheen-{index}',
            tint="#d12f37" if index == 0 else "#d5ad52",
            duration=f'{10.4 + index * 1.1:.2f}s', delay=f'{index * .8:.2f}s',
            opacity=".11", radius=3,
        ))
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
  {horizontal_sheen(x, y + 15, width, 169, f'repo-sheen-{index}', tint="#d5ad52" if index % 2 == 0 else "#d12f37", duration=f'{8.6 + index * .53:.2f}s', delay=f'{index * .37:.2f}s', opacity=".18", radius=2)}
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
    if score < thresholds[0]:
        return "LOCKED", 0, thresholds[0]
    rank_idx = 0
    for index, threshold in enumerate(thresholds):
        if score >= threshold:
            rank_idx = index
    current = thresholds[rank_idx]
    next_value = thresholds[rank_idx + 1] if rank_idx + 1 < len(thresholds) else None
    if next_value is None:
        step = max(1, thresholds[-1] - thresholds[-2])
        extra_levels = (score - thresholds[-1]) // step
        next_value = thresholds[-1] + (extra_levels + 1) * step
        if extra_levels:
            return f"{RANK_NAMES[-1]}+{extra_levels}", thresholds[-1] + extra_levels * step, next_value
    return RANK_NAMES[rank_idx], current, next_value


def render_trophies(data: dict[str, Any]) -> str:
    rank_colors = {
        "C": "#aaa49a", "B": "#bd7955", "A": "#e2464d", "AA": "#ef8a4c",
        "AAA": "#d5ad52", "S": "#c9a3ff", "SS": "#dc8dff", "SSS": "#f0bdff",
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
        rank_color = rank_colors.get(rank.lstrip("≥").split("+")[0], rank_colors["SSS"])
        is_s_rank = rank.lstrip("≥").split("+")[0].startswith("S")
        rank_sheen = diagonal_sheen(
            x, y, width, 226, f'trophy-sheen-{index}',
            tint="#d7a6ff" if is_s_rank else "#d5ad52",
            duration=f'{7.1 + index * .63:.2f}s', delay=f'{index * .42:.2f}s',
            opacity=".22" if is_s_rank else ".10", radius=7,
        )
        center = x + width // 2
        rank_font_size = 19 if len(rank) > 4 else 28
        body.append(f"""
  <rect x="{x}" y="{y}" width="{width}" height="226" rx="7" fill="#101011" stroke="{rank_color}" stroke-opacity=".58" class="card-edge"/>
  {rank_sheen}
  <path d="M{x + 1} {y + 7}H{x + width - 1}" stroke="{rank_color}" stroke-width="3"/>
  <path d="M{x + 14} {y + 20}h16m-16 0v16M{x + width - 14} {y + 20}h-16m16 0v16" fill="none" stroke="{rank_color}" stroke-opacity=".46" stroke-width="1.3"/>
  <text x="{x + 18}" y="{y + 36}" class="micro" fill="{rank_color}">{svg_escape(key.upper())}</text>
  <circle cx="{center}" cy="{y + 88}" r="39" fill="{rank_color}" fill-opacity=".07" stroke="{rank_color}" stroke-opacity=".24" stroke-width="7" class="pulse"/>
  <circle cx="{center}" cy="{y + 88}" r="31" fill="#151414" stroke="{rank_color}" stroke-width="1.5"/>
  <circle cx="{center}" cy="{y + 88}" r="35" fill="none" stroke="{rank_color}" stroke-opacity=".58" stroke-width="1" stroke-dasharray="2 7" class="trophy-orbit" style="animation-duration:{8.2 + (index % 4) * .8:.1f}s;animation-direction:{'normal' if index % 2 == 0 else 'reverse'}"/>
  <text x="{center}" y="{y + 99}" text-anchor="middle" fill="{rank_color}" font-family="Georgia,'Times New Roman',serif" font-size="32" font-weight="700">{svg_escape(icon)}</text>
  <text x="{center}" y="{y + 153}" text-anchor="middle" fill="{rank_color}" font-family="Georgia,'Times New Roman',serif" font-size="{rank_font_size}" font-weight="700">{svg_escape(rank)}</text>
  <text x="{center}" y="{y + 176}" text-anchor="middle" class="micro white">{score_text} <tspan fill="{rank_color}">· {svg_escape(icon)}</tspan></text>
  <rect x="{x + 20}" y="{y + 192}" width="{width - 40}" height="5" rx="2.5" fill="#3b3630"/>
  <rect x="{x + 20}" y="{y + 192}" width="{bar_w}" height="5" rx="2.5" fill="{rank_color}" class="rank-meter"/>
  <text x="{center}" y="{y + 216}" text-anchor="middle" class="micro soft">{"NO SNAPSHOT" if missing else target_label}</text>
""")
    panel_height = 96 + rows * 248 + 8
    return wrap_svg("Trophy case", "".join(body), panel_height, "02")


def render_achievements(data: dict[str, Any]) -> str:
    badges = data.get("achievements", [])
    columns = 3
    card_width = 356
    gap = 22
    rows = max(1, (len(badges) + columns - 1) // columns)
    left = (1200 - columns * card_width - (columns - 1) * gap) // 2
    grid_y = 104
    card_height = 122
    body: list[str] = [
        f'<text x="56" y="91" class="micro gold">{len(badges)} PUBLIC UNLOCKS</text>',
        f'<text x="1144" y="91" text-anchor="end" class="micro soft">LAST CHANGE · {stamp_label(data.get("generated_at"))}</text>',
    ]
    if not badges:
        body.append('<text x="60" y="155" class="small soft">No GitHub achievement badges are visible on the public profile in this snapshot.</text>')
    for index, badge in enumerate(badges):
        slug = str(badge.get("slug", "")).lower()
        name = short_text(str(badge.get("name") or slug.replace("-", " ").title()), 34)
        color = ACHIEVEMENT_COLORS.get(slug, "#d5ad52")
        glyph = ACHIEVEMENT_GLYPHS.get(slug, "✦")
        x = left + (index % columns) * (card_width + gap)
        y = grid_y + (index // columns) * 140
        url = str(badge.get("url") or "")
        if url.startswith("/"):
            url = "https://github.com" + url
        if not url.startswith("https://github.com/"):
            url = f"https://github.com/{urllib.parse.quote(USERNAME)}?achievement={urllib.parse.quote(slug)}&tab=achievements"
        font_size = 18 if len(name) > 23 else 22
        body.append(f"""
  <a xlink:href="{svg_escape(url)}" target="_blank" rel="noopener noreferrer">
    <rect x="{x}" y="{y}" width="{card_width}" height="{card_height}" rx="6" fill="#101011" stroke="{color}" stroke-opacity=".52" class="card-edge"/>
    {diagonal_sheen(x, y, card_width, card_height, f'achievement-sheen-{index}', tint=color, duration=f'{8.6 + index * .56:.2f}s', delay=f'{index * .27:.2f}s', opacity=".17", radius=6)}
    <path d="M{x + 1} {y + 7}H{x + card_width - 1}" stroke="{color}" stroke-width="3"/>
    <circle cx="{x + 44}" cy="{y + 49}" r="24" fill="{color}" fill-opacity=".10" stroke="{color}" stroke-opacity=".65" class="achievement-bloom"/>
    <circle cx="{x + 44}" cy="{y + 49}" r="30" fill="none" stroke="{color}" stroke-opacity=".28" stroke-dasharray="2 7" class="inkline"/>
    <text x="{x + 44}" y="{y + 57}" text-anchor="middle" fill="{color}" font-family="Georgia,'Times New Roman',serif" font-size="23" font-weight="700">{svg_escape(glyph)}</text>
    <text x="{x + 82}" y="{y + 54}" class="serif white" font-size="{font_size}">{svg_escape(name)}</text>
    <path d="M{x + 20} {y + 84}H{x + card_width - 20}" class="rule"/>
    <text x="{x + 20}" y="{y + 108}" class="micro" fill="{color}">EARNED · PUBLIC GITHUB PROFILE</text>
    <text x="{x + card_width - 20}" y="{y + 108}" text-anchor="end" class="micro soft">↗</text>
  </a>
""")
    body.append(f'<text x="56" y="{grid_y + rows * 140 + 8}" class="micro soft">BADGES LINK TO THEIR GITHUB ACHIEVEMENT PAGE</text>')
    panel_height = grid_y + rows * 140 + 28
    return wrap_svg("Verified achievements", "".join(body), panel_height, "02A")


def render_whoami(data: dict[str, Any]) -> str:
    name = svg_escape(data.get("name") or "Karmanya Ravindra")
    body = f"""
  <rect x="34" y="83" width="1132" height="320" rx="5" fill="#0d0d0e" stroke="#463a30" class="card-edge"/>
  {diagonal_sheen(34, 83, 1132, 320, "whoami-main-sheen", tint="#d12f37", duration="11.8s", delay="1.1s", opacity=".07", radius=5)}
  <path d="M34 88V397" stroke="#d12f37" stroke-width="4"/>
  <rect x="58" y="104" width="136" height="24" rx="12" fill="#301316" stroke="#8f312f"/>
  <text x="126" y="121" text-anchor="middle" class="micro white">IDENTITY / 01</text>
  <rect x="209" y="104" width="292" height="24" rx="12" fill="#282116" stroke="#8a6b34"/>
  <text x="355" y="121" text-anchor="middle" class="micro gold">CRTP · PT1 · CNSP CERTIFIED</text>
  <text x="58" y="175" class="serif white" font-size="42">{name}</text>
  <text x="60" y="202" class="mono soft">@{svg_escape(USERNAME)}  ·  PENETRATION TESTER / RED TEAMING</text>
  <path d="M58 221H782" class="rule"/>
  <text x="60" y="250" class="micro red">FOCUS</text>
  <text x="183" y="250" class="small white">Penetration testing  ·  Red Teaming</text>
  <text x="60" y="281" class="micro red">ENGINEER</text>
  <text x="183" y="281" class="small white">Security engineering  ·  Programming</text>
  <text x="60" y="312" class="micro red">GAME DEV</text>
  <text x="183" y="312" class="small white">Game Developer · Game Programmer</text>
  <text x="60" y="343" class="micro red">TEACHING</text>
  <text x="183" y="343" class="small white">Cybersecurity Professor · College</text>
  <text x="60" y="374" class="micro red">CERTIFIED</text>
  <text x="183" y="374" class="small white">CRTP Certified  ·  PT1 Certified  ·  CNSP Certified</text>

  <path d="M814 98V388" stroke="#3e352b"/>
  <rect x="837" y="104" width="304" height="274" rx="4" fill="#121011" stroke="#5a3031" class="card-edge"/>
  {diagonal_sheen(837, 104, 304, 274, "whoami-status-sheen", tint="#d5ad52", duration="9.4s", delay="2.4s", opacity=".12", radius=4)}
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
    step_y = 16
    cell_w = 16
    cell_h = 13
    row_start = 174
    levels = ["#171518", "#3a151a", "#79212b", "#c6323d", "#d5ad52"]
    body: list[str] = [
        '<rect x="34" y="82" width="1132" height="240" rx="5" fill="#0d0d0e" stroke="#3c3732" class="card-edge"/>',
        '<path d="M34 87H1166" stroke="#d12f37" stroke-opacity=".9" stroke-width="2"/>',
    ]
    count_text = fmt_count(day_total)
    body.append(f'''
  <text x="52" y="128" class="num gold">{svg_escape(count_text)}</text>
  <text x="196" y="111" class="micro white">{svg_escape(heading.upper())}</text>
  <text x="196" y="138" class="micro soft">{dates[0].strftime('%d %b %Y').upper()} — {end_date.strftime('%d %b %Y').upper()}  ·  {active_days} ACTIVE DAYS</text>
  <text x="893" y="116" text-anchor="end" class="micro soft">LESS</text>
''')
    legend_start = 912
    for level, color in enumerate(levels):
        body.append(f'<rect x="{legend_start + level * 22}" y="104" width="14" height="14" rx="3" fill="{color}" stroke="#615344" stroke-width=".5"/>')
    body.append('<text x="1036" y="116" class="micro soft">MORE</text>')

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    week_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    seen_months: set[tuple[int, int]] = set()
    for date in dates:
        if (date.year, date.month) in seen_months:
            continue
        seen_months.add((date.year, date.month))
        week = (date - grid_start).days // 7
        body.append(f'<text x="{grid_x + week * step_x}" y="160" class="month soft">{month_names[date.month - 1]}</text>')

    for index, label in enumerate(week_names):
        if index in (0, 2, 4, 6):
            body.append(f'<text x="65" y="{row_start + index * step_y + 11}" text-anchor="middle" class="weekday soft">{label}</text>')
    for index, (count, level_value) in enumerate(values):
        date = dates[index]
        week = (date - grid_start).days // 7
        weekday = (date.weekday() + 1) % 7
        level = max(0, min(4, int(level_value)))
        color = levels[level]
        delay = ((week * .17) + (weekday * .23)) % 3.4
        title = f"{date.isoformat()} · {count} {'contribution' if count == 1 else 'contributions'}"
        body.append(
            f'<rect x="{grid_x + week * step_x}" y="{row_start + weekday * step_y}" '
            f'width="{cell_w}" height="{cell_h}" rx="2" fill="{color}" '
            f'class="heat-cell" style="--delay:{delay:.2f}s"><title>{svg_escape(title)}</title></rect>'
        )
    body.append(f'<text x="{grid_x}" y="310" class="micro gold">{year}</text>')
    body.append(f'<path d="M{grid_x + 52} 306H{grid_x + 52 + int(950 * active_days / max(1, len(values)))}" stroke="#d12f37" stroke-width="2" class="inkline"/>')
    return wrap_svg(f"Contribution calendar {year}", "".join(body), 338, "05")


def toolkit_icon_markup(label: str, x: int, y: int, accent: str, foreground: str) -> str:
    """Return a local, font-free technology emblem sized for one toolkit tile."""
    marks = {
        "Rust": '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.7"/><path d="M12 1.8v2.8m0 14.8v2.8M1.8 12h2.8m14.8 0h2.8M4.8 4.8l2 2m10.4 10.4 2 2m0-14.4-2 2M6.8 17.2l-2 2"/>',
        "WebAssembly": '<path d="m12 2 9 5v10l-9 5-9-5V7z"/><path d="m7 8 5 8 5-8M8.8 11h6.4"/>',
        "C": '<path d="m8.5 5-7 7 7 7m7-14 7 7-7 7"/>',
        "C++": '<path d="m7 5-6 7 6 7m10-14 6 7-6 7M13 8v8m-4-4h8"/>',
        "C Sharp": '<path d="m7 5-6 7 6 7m10-14 6 7-6 7M13 8l-2 8m6-8-2 8m-5-5h8m-9 3h8"/>',
        "Java": '<path d="M8 15h9l-1 5H9zM6 15h13m-8-7c-2-2 3-2 1-5m2 7c3-2-1-3 1-5M7 18c-2 1-1 2 2 2m8-2c2 1 1 2-2 2"/>',
        "JavaScript": '<path d="M4 3h16v18H4z" fill="{ink}" stroke="none"/><text x="12" y="16.5" text-anchor="middle" font-family="Arial,sans-serif" font-size="7.4" font-weight="700" fill="{color}" stroke="none">JS</text>',
        "Python": '<path d="M12 3c-5 0-6 2-6 6v3h8v2H5c-3 0-4 2-4 5 0 2 2 3 5 3 4 0 6-2 6-5v-3H8v-2h7c3 0 4-2 4-5 0-3-2-4-7-4z"/><circle cx="9" cy="6.5" r=".8" fill="{ink}" stroke="none"/><circle cx="15" cy="17.5" r=".8" fill="{ink}" stroke="none"/>',
        "PowerShell": '<path d="m3 5 7 7-7 7m9 0h9"/>',
        "HTML5": '<path d="m4 2 1.5 18L12 22l6.5-2L20 2z"/><path d="M8 7h8l-.5 3.5H8.5L9 15l3 1 3-1 .3-2"/>',
        "CSS3": '<path d="m4 2 1.5 18L12 22l6.5-2L20 2z"/><path d="M8 7h8M8.5 11h7l-.5 4-3 1-3-1"/>',
        "R": '<path d="M4 20V4h8a5 5 0 0 1 0 10H4m7 0 6 6m-5-16v10"/><circle cx="18.5" cy="5.5" r="1.5"/>',
        "AWS": '<path d="M4 14a5 5 0 0 1 5-5 6 6 0 0 1 11 2 4 4 0 0 1-1 8H7a4 4 0 0 1-3-5zM5 21c5 2 10 2 15 0m-4-2 4 2-3 3"/>',
        "Azure": '<path d="M13 3 4 19h6l9 2-6-10 3-5zM8 17h8"/>',
        "Google Cloud": '<path d="M7 18h11a4 4 0 0 0 1-8 6 6 0 0 0-11-2 5 5 0 0 0-1 10z"/><path d="M7 18a5 5 0 0 1 1-10m10 2a4 4 0 0 1 1 8" stroke="{color}"/>',
        "Cloudflare": '<path d="M5 17a4 4 0 0 1 1-8 6 6 0 0 1 11-2 4 4 0 0 1 2 7H9m1 4h9"/><path d="M15 3v3M21 6l-2 2M4 7l2 2"/>',
        "Django": '<path d="M5 3h7a8 8 0 1 1 0 16H5z"/><path d="M9 7h3a4 4 0 1 1 0 8H9z"/>',
        "Flask": '<path d="M9 3h6m-5 0v6l-6 10a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3L14 9V3m-8 12h12m-9-3h2"/>',
        "FastAPI": '<path d="m14 2-9 12h7l-2 8 9-12h-7z"/>',
        "Node.js": '<path d="m12 2 9 5v10l-9 5-9-5V7z"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="9" r="1.5"/><circle cx="15" cy="16" r="1.5"/><path d="m10.3 11.2 3.4-1.4m-3.2 3.1 3 2.2"/>',
        "MySQL": '<path d="M3 17c3 0 4-7 8-8 3-.8 4 2 6 2 2 0 3-2 4-2m-8 0c0-2 2-3 4-2m-6 6c2 2 5 3 8 2m-2-4 2 4 3-2"/>',
        "MongoDB": '<path d="M12 2c-3 5-7 8-6 13 1 4 4 6 6 7 2-1 5-3 6-7 1-5-3-8-6-13z"/><path d="M12 7v14m0-7 4-3"/>',
        "PostgreSQL": '<path d="M5 19c-2-2-2-8 0-11 2-4 6-5 10-4 3 1 4 4 4 7 0 2-1 3-3 3h-2l1 3c0 2-2 3-4 2l-1-3-3 3z"/><path d="M13 8c2-2 4-2 6-1m-8 2h.1"/>',
        "SQLite": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v13c0 2 4 4 8 4s8-2 8-4V5M4 11c0 2 4 3 8 3s8-1 8-3M4 17c0 2 4 3 8 3s8-1 8-3"/>',
        "Git": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10m2-7c5 0 4-3 8-3"/>',
        "GitHub": '<path d="M8 6 6 3 5 9c-2 2-2 7 1 9l2 1v2h8v-2l2-1c3-2 3-7 1-9l-1-6-3 3a13 13 0 0 0-7 0z"/><path d="M9 16c1 2 5 2 6 0m-7-3h.1m8 0h.1"/>',
        "GitLab": '<path d="m12 21 10-8-3-10-3 6H8L5 3 2 13z"/><path d="m6 9 6 12 6-12m-6 12L2 13m10 8 10-8"/>',
        "Nginx": '<path d="M3 6h5l3 3h10M3 12h18M3 18h5l3-3h10"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="18" r="1"/>',
        "Jira": '<path d="m12 2 9 9-9 11-9-11z"/><path d="m12 2 5 9-5 11-5-11z"/>',
        "Unity": '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5m-9 5v10m-4-12 8 5m0-5-8 5"/>',
        "Unreal Engine": '<circle cx="12" cy="12" r="10"/><path d="M17 8v9h-2v-2c-1 2-5 3-7 1V8h2v7c1 1 3 1 4 0V8z"/>',
        "TensorFlow": '<path d="m5 5 7 7 7-7M5 19l7-7 7 7M5 5v14m14-14v14"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/>',
        "NumPy": '<path d="M3 4h18v16H3zM3 10h18M3 15h18M9 4v16m6-16v16"/>',
        "Adobe": '<path d="M3 20 10 4h4l7 16h-5l-4-10-4 10z"/><path d="M7 16h10"/>',
        "Premiere Pro": '<rect x="3" y="3" width="18" height="18" rx="3" fill="{ink}" stroke="none"/><path d="M8 17V7h4a3 3 0 0 1 0 6h-1m4 4v-7m0 3c1-2 2-3 4-3" stroke="{color}"/>',
        "After Effects": '<path d="M4 5h9M8.5 5v14M4 19h9m3-1v-7h5v7m-5-3h5m-5 3c0 3 4 4 6 2"/>',
    }
    mark = marks[label].format(color=f"#{accent}", ink=f"#{foreground}")
    return (
        f'<g transform="translate({x + 7} {y + 8}) scale(.95)" fill="none" '
        f'stroke="#{foreground}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">'
        f'{mark}</g>'
    )


def render_toolkit() -> str:
    colors = [("D5AD52", "09090A"), ("F4F0E9", "09090A"), ("D12F37", "F4F0E9")]
    defs = [
        '<clipPath id="toolkit-panel-clip"><rect x="34" y="82" width="1132" height="508" rx="5"/></clipPath>',
    ]
    body: list[str] = [
        '<rect x="34" y="82" width="1132" height="508" rx="5" fill="#0d0d0e" stroke="#463a30" class="card-edge"/>',
        '<path d="M35 87H1165" stroke="#d5ad52" stroke-opacity=".34" stroke-width="1"/>',
    ]
    badge_count = 0
    top = 98
    for group_index, (group, items) in enumerate(SHIELD_GROUPS.items()):
        y_title = top + group_index * 158
        body.append(f'<text x="58" y="{y_title + 16}" class="micro gold">{svg_escape(group)}</text>')
        body.append(f'<path d="M58 {y_title + 28}H1142" stroke="#3d3834"/>')
        x = 58
        y = y_title + 43
        for label, _logo_slug in items:
            color, logo_color = colors[badge_count % len(colors)]
            width = min(218, max(92, int(76 + len(label) * 8.2)))
            if x + width > 1142:
                x = 58
                y += 48
            height = 40
            body.append(
                f'<g class="toolkit-badge" aria-label="{svg_escape(label)}">'
                f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="4" fill="#151414" '
                f'stroke="#{color}" stroke-opacity=".72" stroke-width=".8"/>'
                f'<rect x="{x + 4}" y="{y + 4}" width="30" height="32" rx="3" fill="#{color}" fill-opacity=".88"/>'
                f'{toolkit_icon_markup(label, x + 4, y + 1, color, logo_color)}'
                f'<text x="{x + 43}" y="{y + 25}" fill="#f4f0e9" class="badge-label">{svg_escape(label)}</text>'
                f'</g>'
            )
            x += width + 10
            badge_count += 1

    body.insert(0, f'''<defs>{"".join(defs)}
      <linearGradient id="toolkit-glass-color" gradientUnits="userSpaceOnUse" x1="-460" y1="660" x2="1150" y2="-950">
        <stop offset="0" stop-color="#f4f0e9" stop-opacity="0"/>
        <stop offset=".43" stop-color="#f4f0e9" stop-opacity=".08"/>
        <stop offset=".49" stop-color="#fff9eb" stop-opacity=".78"/>
        <stop offset=".53" stop-color="#d5ad52" stop-opacity=".72"/>
        <stop offset=".57" stop-color="#f2cfc5" stop-opacity=".56"/>
        <stop offset=".63" stop-color="#f4f0e9" stop-opacity=".08"/>
        <stop offset="1" stop-color="#f4f0e9" stop-opacity="0"/>
      </linearGradient>
      <filter id="toolkit-glass-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="7"/></filter>
    </defs>''')
    body.append('''<g clip-path="url(#toolkit-panel-clip)" style="mix-blend-mode:screen;isolation:isolate" aria-label="One colour-responsive toolkit glass sweep">
      <g>
        <animateTransform attributeName="transform" type="translate" values="-1800,-1800;1450,1450" dur="14.8s" begin="-10s" repeatCount="indefinite"/>
        <path d="M-460 660h52l1610 -1610h-52Z" fill="url(#toolkit-glass-color)" opacity=".34" filter="url(#toolkit-glass-glow)"/>
        <path d="M-460 660h52l1610 -1610h-52Z" fill="url(#toolkit-glass-color)" opacity=".70"/>
      </g>
    </g>''')
    body.append('<text x="58" y="568" class="micro soft">LANGUAGES  ·  CLOUD  ·  SECURITY  ·  GAME DEVELOPMENT  ·  CREATIVE TOOLS</text>')
    return wrap_svg("Toolkit", "".join(body), 610, "06")


def render_footer_quote() -> str:
    body = '''
  <defs>
    <linearGradient id="footer-sweep-color" x1="150" y1="65" x2="1050" y2="65" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#8f252c" stop-opacity=".12"/>
      <stop offset=".48" stop-color="#d12f37"/>
      <stop offset=".68" stop-color="#ffd2c6"/>
      <stop offset="1" stop-color="#d12f37" stop-opacity=".12"/>
    </linearGradient>
    <filter id="footer-glow" x="-30%" y="-300%" width="160%" height="700%">
      <feGaussianBlur stdDeviation="3"/>
    </filter>
  </defs>
  <path d="M150 65H1050" stroke="#463a30" stroke-width="1"/>
  <path d="M150 65H1050" stroke="#d12f37" stroke-opacity=".28" stroke-width="7" stroke-linecap="round" class="footer-sweep-glow"/>
  <path d="M150 65H1050" stroke="url(#footer-sweep-color)" stroke-width="2.5" stroke-linecap="round" class="footer-sweep"/>
  <circle cx="150" cy="65" r="3" fill="#d5ad52" class="pulse"/>
  <circle cx="1050" cy="65" r="3" fill="#d5ad52" class="pulse"/>
  <text x="600" y="48" text-anchor="middle" class="serif white" font-size="24">
    <tspan fill="#d12f37">/dev/null</tspan><tspan fill="#f4f0e9"> is my </tspan>
    <tspan fill="#d5ad52">inspiration</tspan><tspan fill="#aba59c"> · </tspan>
    <tspan fill="#d5ad52">Rust</tspan><tspan fill="#f4f0e9"> is my </tspan>
    <tspan fill="#d12f37">weapon</tspan><tspan fill="#f4f0e9"> of </tspan><tspan fill="#d5ad52">choice</tspan>
  </text>
'''
    return wrap_svg("Closing quote", body, 88, "07", show_header=False)


def render_kali_quote() -> str:
    body = '''
  <defs>
    <linearGradient id="quote-glint" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#d12f37" stop-opacity="0"/>
      <stop offset=".5" stop-color="#f4f0e9" stop-opacity=".95"/>
      <stop offset="1" stop-color="#d12f37" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="quote-line-clip"><rect x="93" y="77" width="675" height="3" rx="1.5"/></clipPath>
  </defs>
  <rect x="44" y="12" width="1112" height="76" rx="5" fill="#0d0d0e" stroke="#332a28"/>
  <path d="M45 18V82" stroke="#d12f37" stroke-width="4"/>
  <path d="M45 18V82" stroke="#ff7378" stroke-opacity=".22" stroke-width="8" class="quote-bar-glow"/>
  <text x="75" y="39" class="micro red">FIELD NOTE  /  ATTENTION</text>
  <text x="75" y="67" class="serif white" font-size="23">
    “The <tspan fill="#d5ad52" class="quote-word">quieter</tspan> you become, the more you can <tspan fill="#e2464d" class="quote-word-late">hear.</tspan>”
  </text>
  <text x="1125" y="66" text-anchor="end" class="micro gold">— KALI LINUX</text>
  <g clip-path="url(#quote-line-clip)">
    <path d="M92 78H770" stroke="#552126" stroke-width="2"/>
    <path d="M92 78H770" stroke="url(#quote-glint)" stroke-width="2.5" stroke-linecap="round" class="quote-rail"/>
  </g>
'''
    return wrap_svg("Kali Linux field note", body, 100, "", show_header=False)


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
        data = fetch_snapshot(USERNAME, old)
    except GitHubAPIError as exc:
        if not old:
            raise
        data = old
        cached = True
        print(f"{exc}\nUsing the last complete snapshot dated {data.get('generated_at', 'unknown')}.")
    else:
        cached = False

    created_at = data.get("created_at") or old.get("created_at")
    try:
        data["achievements"] = fetch_github_achievements(USERNAME)
        print(f"Found {len(data['achievements'])} public GitHub achievement badges.")
    except GitHubAPIError as exc:
        data["achievements"] = old.get("achievements", [])
        print(f"{exc}\nUsing the last saved public achievement list.")

    if created_at:
        try:
            data["created_at"] = created_at
            data["contributions"] = fetch_contribution_history(
                USERNAME,
                created_at,
                dt.datetime.now(dt.timezone.utc).date(),
                old.get("contributions"),
                refresh_historical=REFRESH_HISTORICAL_CONTRIBUTIONS,
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

    # Keep no-change runs quiet: the scheduled job still checks the live endpoints,
    # but it does not rewrite timestamps and create a repository commit every five minutes.
    comparable = copy.deepcopy(data)
    old_comparable = copy.deepcopy(old)
    comparable.pop("generated_at", None)
    old_comparable.pop("generated_at", None)
    for snapshot in (comparable, old_comparable):
        if isinstance(snapshot.get("contributions"), dict):
            snapshot["contributions"].pop("updated_at", None)
    if comparable == old_comparable:
        data["generated_at"] = old.get("generated_at", data.get("generated_at"))
        if isinstance(data.get("contributions"), dict):
            old_contributions = old.get("contributions", {})
            data["contributions"]["updated_at"] = old_contributions.get("updated_at", data.get("generated_at"))
    else:
        checked_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z")
        data["generated_at"] = checked_at
        if isinstance(data.get("contributions"), dict):
            data["contributions"]["updated_at"] = checked_at

    write_file(METADATA_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    write_file(OUT_DIR / "whoami.svg", render_whoami(data))
    write_file(OUT_DIR / "overview.svg", render_overview(data))
    write_file(OUT_DIR / "repositories.svg", render_repositories(data))
    write_file(OUT_DIR / "trophies.svg", render_trophies(data))
    write_file(OUT_DIR / "achievements.svg", render_achievements(data))
    write_file(OUT_DIR / "recent-activity.svg", render_recent_activity(data))
    write_file(OUT_DIR / "telemetry.svg", render_event_trace(data))
    write_file(OUT_DIR / "toolkit.svg", render_toolkit())
    write_file(OUT_DIR / "kali-quote.svg", render_kali_quote())
    write_file(OUT_DIR / "footer-quote.svg", render_footer_quote())
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

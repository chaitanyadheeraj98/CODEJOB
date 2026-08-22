from __future__ import annotations

import httpx

from app.config import settings


class GithubIssueServiceError(RuntimeError):
    pass


def create_github_issue(title: str, body: str) -> dict[str, object]:
    """Create an issue in the configured private repo via the GitHub REST API."""
    if not settings.github_token or not settings.github_repo:
        raise GithubIssueServiceError("GitHub issue reporting is not configured")

    response = httpx.post(
        f"https://api.github.com/repos/{settings.github_repo}/issues",
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body},
        timeout=15.0,
    )
    if response.status_code >= 400:
        raise GithubIssueServiceError(f"GitHub API error {response.status_code}: {response.text[:300]}")

    data = response.json()
    return {"issue_number": data["number"], "issue_url": data["html_url"]}

import asyncio
import base64
import os
from typing import Any, Dict, List, Optional
import httpx


class GitHubExtractionError(Exception):
    """Base exception for GitHub extraction errors."""
    pass


class GitHubUserNotFoundError(GitHubExtractionError):
    """Raised when GitHub username does not exist."""
    pass


class GitHubNoPublicReposError(GitHubExtractionError):
    """Raised when GitHub user has zero public or non-fork repositories."""
    pass


class GitHubRateLimitError(GitHubExtractionError):
    """Raised when GitHub API rate limit is exceeded."""
    pass


def _derive_repo_proof(repo: Dict[str, Any], readme_snippet: str) -> str:
    """Generate a clean, high-signal one-line 'what this proves' summary for a repo."""
    name = repo.get("name", "Project")
    language = repo.get("language") or "Full-stack"
    stars = repo.get("stargazers_count", 0)
    topics = repo.get("topics", [])
    topics_str = ", ".join(topics[:3]) if topics else ""

    star_indicator = f" with community traction ({stars}★)" if stars > 10 else ""
    topic_indicator = f" focusing on {topics_str}" if topics_str else ""

    # Check for notable keywords in README or description
    desc_and_readme = f"{repo.get('description') or ''} {readme_snippet[:400]}".lower()

    if any(k in desc_and_readme for k in ["distributed", "consensus", "raft", "paxos", "grpc"]):
        return f"Proves low-level systems & distributed architectures in {language}{star_indicator}."
    elif any(k in desc_and_readme for k in ["llm", "rag", "langchain", "embeddings", "vector", "transformer", "agent"]):
        return f"Proves practical AI/agentic engineering & modern LLM workflows in {language}{star_indicator}."
    elif any(k in desc_and_readme for k in ["database", "postgres", "sql", "storage", "engine", "wal"]):
        return f"Proves deep data persistence, schema design, and query optimization expertise."
    elif any(k in desc_and_readme for k in ["react", "vite", "next", "frontend", "ui", "tailwind"]):
        return f"Proves production-grade UI/UX craftsmanship and modern frontend state management."
    elif any(k in desc_and_readme for k in ["compiler", "runtime", "interpreter", "ast", "wasm"]):
        return f"Proves deep compiler, AST parsing, and systems runtime engineering competency."
    elif any(k in desc_and_readme for k in ["api", "fastapi", "microservice", "backend"]):
        return f"Proves reliable API design and scalable backend service orchestration in {language}."

    return f"Proves active shipping competency in {language}{topic_indicator}{star_indicator}."


async def extract_github_profile(
    username: str,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Extract public GitHub profile and top 10 non-fork repositories by recent activity.

    Parameters:
    - username: GitHub handle
    - token: Optional GitHub Personal Access Token (defaults to GITHUB_TOKEN env var)
    - client: Optional httpx.AsyncClient instance (for testing/mocking)

    Returns:
    Dict containing user details and list of enriched top repositories.
    """
    clean_username = username.strip().lstrip("@")
    if not clean_username:
        raise GitHubUserNotFoundError("GitHub username cannot be empty.")

    auth_token = token or os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Unlisted-App-Extractor"
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token.strip()}"

    should_close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        should_close_client = True

    try:
        # 1. Fetch User Profile
        user_url = f"https://api.github.com/users/{clean_username}"
        user_resp = await client.get(user_url, headers=headers)

        if user_resp.status_code == 404:
            raise GitHubUserNotFoundError(f"GitHub user '{clean_username}' was not found.")
        elif user_resp.status_code == 403 and "rate limit" in user_resp.text.lower():
            raise GitHubRateLimitError("GitHub API rate limit exceeded. Please set GITHUB_TOKEN or try again later.")
        elif user_resp.status_code != 200:
            raise GitHubExtractionError(f"GitHub API returned error {user_resp.status_code}: {user_resp.text[:200]}")

        user_data = user_resp.json()
        public_repos_count = user_data.get("public_repos", 0)
        if public_repos_count == 0:
            raise GitHubNoPublicReposError(f"GitHub user '{clean_username}' has zero public repositories.")

        # 2. Fetch Repositories (sorted by pushed_at descending)
        repos_url = f"https://api.github.com/users/{clean_username}/repos?sort=pushed&per_page=100&type=owner"
        repos_resp = await client.get(repos_url, headers=headers)

        if repos_resp.status_code == 403 and "rate limit" in repos_resp.text.lower():
            raise GitHubRateLimitError("GitHub API rate limit exceeded while fetching repositories.")
        elif repos_resp.status_code != 200:
            raise GitHubExtractionError(f"Failed to fetch repositories: {repos_resp.status_code}")

        raw_repos = repos_resp.json()
        if not isinstance(raw_repos, list):
            raise GitHubExtractionError("Unexpected response format from GitHub repos endpoint.")

        # Filter out forks
        non_fork_repos = [r for r in raw_repos if not r.get("fork", False)]
        if not non_fork_repos:
            raise GitHubNoPublicReposError(f"GitHub user '{clean_username}' has no non-fork public repositories.")

        # Sort by pushed_at descending (just to guarantee recency)
        non_fork_repos.sort(
            key=lambda r: r.get("pushed_at") or r.get("updated_at") or "",
            reverse=True
        )

        # Select top 10 repos
        selected_repos = non_fork_repos[:10]

        # 3. Pull README for each selected repository concurrently
        async def fetch_single_readme(r: Dict[str, Any]) -> Dict[str, Any]:
            repo_name = r["name"]
            readme_url = f"https://api.github.com/repos/{clean_username}/{repo_name}/readme"
            readme_snippet = ""

            try:
                readme_resp = await client.get(readme_url, headers=headers)
                if readme_resp.status_code == 200:
                    readme_json = readme_resp.json()
                    content_b64 = readme_json.get("content", "")
                    if content_b64:
                        decoded_bytes = base64.b64decode(content_b64)
                        decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
                        readme_snippet = decoded_text[:2500].strip()
            except Exception:
                readme_snippet = ""

            proof = _derive_repo_proof(r, readme_snippet)

            return {
                "name": repo_name,
                "description": r.get("description") or "No description provided.",
                "language": r.get("language") or "Other",
                "stars": r.get("stargazers_count", 0),
                "forks": r.get("forks_count", 0),
                "topics": r.get("topics") or [],
                "html_url": r.get("html_url") or f"https://github.com/{clean_username}/{repo_name}",
                "pushed_at": r.get("pushed_at") or r.get("updated_at") or "",
                "readme_snippet": readme_snippet,
                "what_this_proves": proof
            }

        enriched_repos = await asyncio.gather(*[fetch_single_readme(r) for r in selected_repos])

        return {
            "username": clean_username,
            "name": user_data.get("name") or clean_username,
            "bio": user_data.get("bio") or "",
            "avatar_url": user_data.get("avatar_url") or "",
            "html_url": user_data.get("html_url") or f"https://github.com/{clean_username}",
            "public_repos_count": len(enriched_repos),
            "repos": enriched_repos
        }

    finally:
        if should_close_client:
            await client.aclose()

import base64
import json
import pytest
import httpx
from github_extractor import (
    extract_github_profile,
    GitHubUserNotFoundError,
    GitHubNoPublicReposError,
    GitHubRateLimitError,
    _derive_repo_proof
)


@pytest.mark.asyncio
async def test_extract_user_not_found():
    def handler(request: httpx.Request):
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubUserNotFoundError) as exc_info:
            await extract_github_profile("nonexistent_user_99999", client=client)
        assert "not found" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_extract_user_zero_public_repos():
    def handler(request: httpx.Request):
        if "/users/emptyuser" in str(request.url):
            return httpx.Response(200, json={
                "login": "emptyuser",
                "name": "Empty User",
                "public_repos": 0
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubNoPublicReposError):
            await extract_github_profile("emptyuser", client=client)


@pytest.mark.asyncio
async def test_extract_rate_limit_error():
    def handler(request: httpx.Request):
        return httpx.Response(403, text="API rate limit exceeded")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(GitHubRateLimitError):
            await extract_github_profile("someone", client=client)


@pytest.mark.asyncio
async def test_extract_filters_forks_and_decodes_readme():
    sample_readme_content = "# Test Framework\nHigh performance database client."
    encoded_readme = base64.b64encode(sample_readme_content.encode("utf-8")).decode("utf-8")

    def handler(request: httpx.Request):
        url = str(request.url)
        if url.endswith("/users/octocat"):
            return httpx.Response(200, json={
                "login": "octocat",
                "name": "The Octocat",
                "bio": "GitHub mascot",
                "public_repos": 2,
                "avatar_url": "https://avatars.githubusercontent.com/u/583231"
            })
        elif "/users/octocat/repos" in url:
            return httpx.Response(200, json=[
                {
                    "name": "forked-repo",
                    "fork": True,
                    "language": "Go",
                    "stargazers_count": 500,
                    "pushed_at": "2026-03-20T00:00:00Z"
                },
                {
                    "name": "my-db-engine",
                    "fork": False,
                    "language": "Rust",
                    "stargazers_count": 120,
                    "description": "Embedded SQL engine with WAL support",
                    "topics": ["database", "rust", "storage"],
                    "pushed_at": "2026-03-25T00:00:00Z"
                }
            ])
        elif "/repos/octocat/my-db-engine/readme" in url:
            return httpx.Response(200, json={
                "content": encoded_readme,
                "encoding": "base64"
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await extract_github_profile("octocat", client=client)

    assert result["username"] == "octocat"
    assert result["public_repos_count"] == 1
    assert len(result["repos"]) == 1
    repo = result["repos"][0]
    assert repo["name"] == "my-db-engine"
    assert repo["language"] == "Rust"
    assert "High performance database client" in repo["readme_snippet"]
    assert "what_this_proves" in repo
    assert len(repo["what_this_proves"]) > 10


def test_derive_repo_proof_categories():
    ai_repo = {"name": "agentic-rag", "language": "Python", "description": "LLM agent using embeddings", "topics": ["llm", "rag"]}
    proof = _derive_repo_proof(ai_repo, "vector database integration")
    assert "AI" in proof or "LLM" in proof

    db_repo = {"name": "pg-wal-replicator", "language": "Go", "description": "Postgres logical decoding", "topics": ["database"]}
    proof_db = _derive_repo_proof(db_repo, "WAL parsing")
    assert "data persistence" in proof_db or "database" in proof_db

# ================================================
# CodeNova MCP - GitHub OAuth + Token Management
# ================================================

import os
import secrets
import httpx
from jose import jwt
from cryptography.fernet import Fernet
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------
# Config
# ------------------------------------------------

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_CALLBACK_URL  = os.getenv("GITHUB_CALLBACK_URL", "http://localhost:8000/auth/github/callback")

JWT_SECRET    = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY    = int(os.getenv("JWT_EXPIRY_HOURS", 168))

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# ------------------------------------------------
# Token Encryption (for storing GitHub tokens in DB)
# ------------------------------------------------

def get_cipher() -> Fernet:
    if not ENCRYPTION_KEY:
        raise ValueError("ENCRYPTION_KEY not set in .env")
    return Fernet(ENCRYPTION_KEY.encode())

def encrypt_token(token: str) -> str:
    """Encrypt GitHub access token before storing in DB"""
    cipher = get_cipher()
    return cipher.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt GitHub access token retrieved from DB"""
    cipher = get_cipher()
    return cipher.decrypt(encrypted_token.encode()).decode()


# ------------------------------------------------
# JWT Token Management (for session)
# ------------------------------------------------

def create_jwt(user_id: str, github_id: int, username: str) -> str:
    """Create JWT token for authenticated user session"""
    payload = {
        "sub": user_id,
        "github_id": github_id,
        "username": username,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_jwt(token: str) -> dict:
    """Verify and decode JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except Exception:
        return None


# ------------------------------------------------
# GitHub OAuth Flow
# ------------------------------------------------

def get_github_oauth_url(state: str) -> str:
    """Build GitHub OAuth authorization URL"""
    # Only public data — never request private repo access
    scopes = "read:user,user:email,public_repo"
    return (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_CALLBACK_URL}"
        f"&scope={scopes}"
        f"&state={state}"
    )

async def exchange_code_for_token(code: str) -> str:
    """Exchange OAuth code for GitHub access token"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_CALLBACK_URL
            }
        )
        data = response.json()

        if "error" in data:
            raise ValueError(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

        return data.get("access_token")

async def fetch_github_user(access_token: str) -> dict:
    """Fetch authenticated user's GitHub profile"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
        )
        response.raise_for_status()
        return response.json()

async def fetch_github_repos(access_token: str, per_page: int = 100) -> list:
    """Fetch only public repos for skill analysis — never private repos"""
    repos = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                f"https://api.github.com/user/repos",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json"
                },
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "type": "owner",
                    "visibility": "public"    # ← public only, never private
                }
            )

            if response.status_code != 200:
                break

            page_data = response.json()
            if not page_data:
                break

            # Extra guard: drop any repo that GitHub still marks private
            public_only = [r for r in page_data if not r.get("private", False)]
            repos.extend(public_only)
            page += 1

            if len(page_data) < per_page:
                break

    return repos

async def verify_pr_on_github(access_token: str, owner: str, repo: str, pr_number: int, expected_username: str) -> dict:
    """Verify that a PR exists and is authored by the user"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json"
            }
        )

        if response.status_code == 404:
            raise ValueError("PR not found — check the URL")

        response.raise_for_status()
        pr = response.json()

        if pr["user"]["login"].lower() != expected_username.lower():
            raise ValueError(f"PR was not authored by {expected_username}")

        return {
            "title": pr["title"],
            "state": pr["state"],
            "html_url": pr["html_url"],
            "author": pr["user"]["login"],
            "created_at": pr["created_at"],
            "merged": pr.get("merged", False)
        }


# ------------------------------------------------
# Skill Extraction from GitHub Repos
# ------------------------------------------------

def extract_skills_from_repos(repos: list) -> dict:
    """
    Analyze user's GitHub repos and extract skill profile.
    Language confidence = (repos using it / total repos) * 100
    """
    total = len(repos)
    if total == 0:
        return {},[]

    language_counts = {}
    topic_set = set()

    for repo in repos:
        # Count language
        lang = repo.get("language")
        if lang:
            language_counts[lang] = language_counts.get(lang, 0) + 1

        # Collect topics for interest matching
        topics = repo.get("topics", [])
        topic_set.update(topics)

    # Convert to confidence scores (0-100)
    skills = {
        lang: round((count / total) * 100)
        for lang, count in language_counts.items()
    }

    return skills, list(topic_set)

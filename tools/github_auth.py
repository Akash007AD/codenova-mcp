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
                    # mercy-preview header is required to get topics in the repo list
                    "Accept": "application/vnd.github.mercy-preview+json"
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

# Map GitHub repo topics → CodeNova interest categories
TOPIC_TO_INTEREST = {
    # web
    "react": "web", "vue": "web", "angular": "web", "nextjs": "web",
    "html": "web", "css": "web", "frontend": "frontend", "web": "web",
    "django": "web", "flask": "web", "fastapi": "web", "express": "web",
    # backend
    "backend": "backend", "api": "backend", "rest-api": "backend",
    "graphql": "backend", "microservices": "backend",
    # ml / data
    "machine-learning": "ml", "deep-learning": "ml", "neural-network": "ml",
    "tensorflow": "ml", "pytorch": "ml", "sklearn": "ml", "nlp": "ml",
    "data-science": "data", "pandas": "data", "numpy": "data",
    "jupyter": "data", "visualization": "data",
    # devtools / infra / cloud
    "docker": "infra", "kubernetes": "infra", "devops": "infra",
    "ci-cd": "infra", "terraform": "infra", "ansible": "infra",
    "aws": "cloud", "gcp": "cloud", "azure": "cloud", "cloud": "cloud",
    "linux": "infra", "bash": "cli", "shell": "cli", "cli": "cli",
    # embedded / iot
    "esp32": "embedded", "arduino": "embedded", "raspberry-pi": "embedded",
    "iot": "embedded", "embedded": "embedded", "firmware": "embedded",
    "lora": "embedded", "rtos": "embedded",
    # security
    "security": "security", "cryptography": "security", "cybersecurity": "security",
    # networking
    "networking": "networking", "protocol": "networking", "socket": "networking",
    # mobile
    "android": "mobile", "ios": "mobile", "flutter": "mobile", "react-native": "mobile",
    # database
    "database": "database", "mongodb": "database", "postgresql": "database",
    "redis": "database", "sql": "database",
    # testing / docs
    "testing": "testing", "docs": "docs", "documentation": "docs",
}


def extract_skills_from_repos(repos: list) -> tuple:
    """
    Analyze user's public GitHub repos and extract a skill profile.

    Scoring:
      - Each repo contributes its primary language
      - Repos with more stars get a slight weight boost
      - Language score = weighted share * 100, capped at 95
      - Topics are mapped to interest categories
      - Fallback: if primary language is None but repo name hints at a
        language (e.g. "-py", "-js"), we infer it
    """
    if not repos:
        return {}, []

    language_weights: dict = {}
    interest_set: set = set()
    total_weight = 0

    for repo in repos:
        # Weight = 1 base + small star bonus (log scale so one big repo
        # doesn't dominate everything)
        stars  = repo.get("stargazers_count", 0) or 0
        weight = 1 + min(stars * 0.1, 3)   # max +3 bonus from stars
        total_weight += weight

        # Primary language from GitHub
        lang = repo.get("language")

        # Fallback: infer from repo name if language is null
        if not lang:
            name = (repo.get("name") or "").lower()
            desc = (repo.get("description") or "").lower()
            text = name + " " + desc
            if any(k in text for k in ("-py", "_py", "python", ".py")):
                lang = "Python"
            elif any(k in text for k in ("-js", "javascript", "nodejs", "node-", "react")):
                lang = "JavaScript"
            elif any(k in text for k in ("typescript", "-ts", "_ts")):
                lang = "TypeScript"
            elif any(k in text for k in ("rust", "-rs")):
                lang = "Rust"
            elif any(k in text for k in ("golang", "-go", "_go")):
                lang = "Go"
            elif any(k in text for k in ("cpp", "c++", "-cpp")):
                lang = "C++"
            elif any(k in text for k in ("arduino", "esp32", "firmware", "embedded")):
                lang = "C"
            elif any(k in text for k in ("java",)):
                lang = "Java"
            elif any(k in text for k in ("shell", "bash", ".sh")):
                lang = "Shell"

        if lang:
            language_weights[lang] = language_weights.get(lang, 0) + weight

        # Topics → interests
        for topic in (repo.get("topics") or []):
            t = topic.lower().strip()
            mapped = TOPIC_TO_INTEREST.get(t)
            if mapped:
                interest_set.add(mapped)
            # Also add the raw topic if it matches a known interest directly
            known_interests = {
                "web", "cli", "backend", "frontend", "devtools", "ml", "data",
                "networking", "security", "embedded", "mobile", "cloud",
                "infra", "docs", "testing", "compiler", "database", "gamedev",
                "graphics", "crypto"
            }
            if t in known_interests:
                interest_set.add(t)

    # Convert weights to 0-95 confidence scores
    skills = {}
    if total_weight > 0:
        for lang, w in language_weights.items():
            score = round((w / total_weight) * 100)
            skills[lang] = min(score, 95)   # cap at 95; 100 would mean only one language ever

    # Sort by score descending, keep top 15
    skills = dict(sorted(skills.items(), key=lambda x: x[1], reverse=True)[:15])

    return skills, list(interest_set)

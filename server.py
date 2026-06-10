# ================================================
# CodeNova MCP Server  —  Local single-user edition
#
# Reads GITHUB_TOKEN from .env once at startup.
# No SERVER_SECRET needed — this runs on your own machine.
# All tools work without passing any credentials in each call.
#
# Entry points:
#   stdio  (Claude Desktop):  python mcp_stdio.py
#   HTTP   (MCP Inspector):   uvicorn main:app --port 8000
# ================================================

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import httpx
import re
import time
from datetime import datetime
from fastmcp import FastMCP

# =====================================================
# LOCAL CONFIG  — read once from .env
# =====================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not GITHUB_TOKEN:
    sys.stderr.write(
        "[codenova] ERROR: GITHUB_TOKEN not set in .env.\n"
        "Create a token at https://github.com/settings/tokens\n"
        "Required scopes: read:user, public_repo\n"
        "Then add: GITHUB_TOKEN=ghp_... to your .env file.\n"
    )

# =====================================================
# OPTIONAL: MongoDB + Redis
# =====================================================

_db    = None
_cache = None

try:
    from database.models import Database
    Database.connect()
    _db = Database.get_db()
    sys.stderr.write("[codenova] MongoDB connected\n")
except Exception as _e:
    sys.stderr.write(f"[codenova] MongoDB unavailable (profile caching off): {_e}\n")

try:
    from cache.redis_manager import CacheManager
    CacheManager.connect()
    _cache = CacheManager
    sys.stderr.write("[codenova] Redis connected\n")
except Exception as _e:
    sys.stderr.write(f"[codenova] Redis unavailable (request caching off): {_e}\n")

# =====================================================
# IN-MEMORY FALLBACK CACHE
# Used when Redis is unavailable to prevent hammering
# the GitHub Search API (30 req/min limit).
# Stores: { key -> {"value": ..., "expires_at": float} }
# =====================================================

_mem_cache: dict = {}

def _mem_get(key: str):
    """Get from in-memory cache. Returns None on miss or expiry."""
    entry = _mem_cache.get(key)
    if not entry:
        return None
    if time.monotonic() > entry["expires_at"]:
        del _mem_cache[key]
        return None
    return entry["value"]

def _mem_set(key: str, value, ttl: int):
    """Store in in-memory cache with TTL (seconds)."""
    _mem_cache[key] = {"value": value, "expires_at": time.monotonic() + ttl}

def _mem_delete(key: str):
    _mem_cache.pop(key, None)

def _mem_delete_prefix(prefix: str):
    to_del = [k for k in _mem_cache if k.startswith(prefix)]
    for k in to_del:
        del _mem_cache[k]

# =====================================================
# UNIFIED CACHE HELPERS
# Try Redis first; fall back to in-memory.
# =====================================================

def _cache_get(key: str):
    """Read from Redis if available, else in-memory fallback."""
    if _cache:
        try:
            val = _cache.get(key)
            if val is not None:
                return val
        except Exception:
            pass
    return _mem_get(key)

def _cache_set(key: str, value, ttl: int = 1800):
    """Write to Redis if available AND in-memory (dual-write for resilience)."""
    if _cache:
        try:
            _cache.set(key, value, ttl)
        except Exception:
            pass
    # Always write to memory so we have a local fallback
    _mem_set(key, value, ttl)

def _cache_delete(key: str):
    if _cache:
        try:
            _cache.delete(key)
        except Exception:
            pass
    _mem_delete(key)

def _cache_delete_prefix(prefix: str):
    if _cache:
        try:
            _cache.delete_pattern(f"{prefix}*")
        except Exception:
            pass
    _mem_delete_prefix(prefix)

# =====================================================
# FastMCP instance
# =====================================================

mcp = FastMCP(
    "codenova",
    instructions=(
        "CodeNova helps you find and contribute to open source projects on GitHub. "
        "Start with get_my_profile() to load your skills, then recommend_issues() "
        "to find matching issues, then get_issue_details() and explain_code_file() "
        "before writing any code."
    ),
)

# =====================================================
# SHARED HELPERS
# =====================================================

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _resolve_user() -> dict | None:
    """
    Resolve the current user from GITHUB_TOKEN.
    Cached for 5 minutes to avoid hammering the GitHub API.
    """
    short_key = f"codenova:whoami:{GITHUB_TOKEN[:16]}"

    cached = _cache_get(short_key)
    if cached and isinstance(cached, dict) and "id" in cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.github.com/user",
                headers=_gh_headers(),
            )
            if r.status_code == 401:
                return None
            r.raise_for_status()
            user = r.json()
    except Exception as e:
        sys.stderr.write(f"[codenova] GitHub /user error: {e}\n")
        return None

    result = {
        "id":           user["id"],
        "login":        user["login"],
        "name":         user.get("name", ""),
        "avatar_url":   user.get("avatar_url", ""),
        "bio":          user.get("bio", ""),
        "location":     user.get("location", ""),
        "public_repos": user.get("public_repos", 0),
        "followers":    user.get("followers", 0),
    }

    _cache_set(short_key, result, 300)
    return result


async def _gh_get(url: str, params: dict = None):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_gh_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


def _call_groq(prompt: str, max_tokens: int = 1000) -> str:
    if not GROQ_API_KEY:
        return ""
    import requests
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# =====================================================
# DB HELPERS
# =====================================================

def _db_get_profile(github_id: int) -> dict | None:
    if _db is None:
        return None
    try:
        doc = _db.users.find_one({"github_id": github_id})
        # Treat as cache MISS if skills are empty — prevents stale empty profiles
        # from being served forever after a failed/incomplete initial fetch.
        if doc and not doc.get("skills"):
            sys.stderr.write(
                f"[codenova] Cache miss (empty skills) for github_id={github_id}. "
                "Will re-fetch from GitHub.\n"
            )
            return None
        return doc
    except Exception:
        return None


def _db_save_profile(github_id: int, login: str, extra: dict, skills: dict, interests: list):
    if _db is None:
        return
    try:
        _db.users.update_one(
            {"github_id": github_id},
            {"$set": {
                "github_id":  github_id,
                "username":   login,
                "skills":     skills,
                "interests":  interests,
                "updated_at": datetime.utcnow(),
                **{k: v for k, v in extra.items()
                   if k in ("avatar_url", "name", "public_repos")},
            }},
            upsert=True,
        )
    except Exception as e:
        sys.stderr.write(f"[codenova] DB save warn: {e}\n")


# =====================================================
# SKILL EXTRACTION + DIFFICULTY HELPERS
# =====================================================

DIFFICULTY_BEGINNER     = {"good first issue", "good-first-issue", "beginner", "easy",
                            "starter", "first-timers-only", "help wanted", "beginner-friendly"}
DIFFICULTY_ADVANCED     = {"advanced", "hard", "complex", "performance", "security", "architecture"}
DIFFICULTY_INTERMEDIATE = {"intermediate", "medium", "enhancement", "feature", "improvement"}

TOPIC_INTEREST = {
    "react": "web", "vue": "web", "angular": "web", "nextjs": "web", "html": "web", "css": "web",
    "frontend": "frontend", "django": "backend", "flask": "backend", "fastapi": "backend",
    "express": "backend", "backend": "backend", "api": "backend",
    "machine-learning": "ml", "deep-learning": "ml", "tensorflow": "ml", "pytorch": "ml", "nlp": "ml",
    "data-science": "data", "docker": "infra", "kubernetes": "infra", "devops": "infra",
    "terraform": "infra", "aws": "cloud", "gcp": "cloud", "azure": "cloud",
    "esp32": "embedded", "arduino": "embedded", "iot": "embedded", "embedded": "embedded",
    "lora": "embedded", "security": "security", "cryptography": "security",
    "android": "mobile", "ios": "mobile", "flutter": "mobile",
    "mongodb": "database", "postgresql": "database", "redis": "database", "sql": "database",
}


def _estimate_difficulty(labels: list) -> str:
    s = {l.lower() for l in labels}
    if s & DIFFICULTY_BEGINNER:     return "beginner"
    if s & DIFFICULTY_ADVANCED:     return "advanced"
    if s & DIFFICULTY_INTERMEDIATE: return "intermediate"
    return "beginner"


def _recommend_difficulty(skills: dict) -> str:
    if not skills:
        return "beginner"
    top = max(skills.values(), default=0)
    if top < 40: return "beginner"
    if top < 70: return "intermediate"
    return "advanced"


def _extract_skills(repos: list) -> tuple:
    lang_w: dict = {}
    interests: set = set()
    total = 0
    for repo in repos:
        stars = repo.get("stargazers_count", 0) or 0
        w = 1 + min(stars * 0.1, 3)
        total += w
        lang = repo.get("language")
        if not lang:
            txt = (repo.get("name", "") + " " + (repo.get("description") or "")).lower()
            for kw, canon in [
                ("python", "Python"), ("javascript", "JavaScript"), ("typescript", "TypeScript"),
                ("rust", "Rust"), ("golang", "Go"), ("arduino", "C"), ("esp32", "C"),
                ("java ", "Java"), ("c++", "C++"), ("shell", "Shell"),
            ]:
                if kw in txt:
                    lang = canon
                    break
        if lang:
            lang_w[lang] = lang_w.get(lang, 0) + w
        for topic in (repo.get("topics") or []):
            mapped = TOPIC_INTEREST.get(topic.lower())
            if mapped:
                interests.add(mapped)
    skills = {}
    if total > 0:
        for lang, w in lang_w.items():
            skills[lang] = min(round((w / total) * 100), 95)
    return dict(sorted(skills.items(), key=lambda x: x[1], reverse=True)[:15]), list(interests)


_NO_TOKEN = {
    "error": "NO_GITHUB_TOKEN",
    "message": (
        "GITHUB_TOKEN is not set in your .env file. "
        "Create a token at https://github.com/settings/tokens "
        "with scopes: read:user, public_repo — then add it to .env."
    ),
}

_BAD_TOKEN = {
    "error": "INVALID_GITHUB_TOKEN",
    "message": (
        "Could not authenticate with GitHub. "
        "Check that GITHUB_TOKEN in your .env is valid and has "
        "scopes: read:user, public_repo."
    ),
}

# TTL for search result caches (seconds)
_TTL_ISSUES = 60 * 60       # 1 hour — issues don't change that fast
_TTL_RECS   = 60 * 60       # 1 hour


# =====================================================
# INTERNAL SEARCH HELPER — rate-limit-aware + cached
# =====================================================

async def _search_issues_cached(
    cache_key: str,
    queries: list[str],       # list of GitHub search query strings
    lang_tag: list[str],      # parallel list: language label per query
    ttl: int = _TTL_ISSUES,
) -> tuple[list, bool]:
    """
    Execute one or more GitHub issue searches, with full caching.

    Returns (issues_list, from_cache).
    If rate-limited and cache is empty, returns ([], False).
    If rate-limited but stale cache exists, returns (stale_list, True).
    """
    # --- Cache read ---
    cached = _cache_get(cache_key)
    if cached is not None:
        sys.stderr.write(f"[codenova] Search cache HIT: {cache_key}\n")
        return cached, True

    sys.stderr.write(f"[codenova] Search cache MISS: {cache_key} — querying GitHub\n")

    all_issues = []
    rate_limited = False

    async with httpx.AsyncClient(timeout=30) as client:
        for q, lang in zip(queries, lang_tag):
            r = await client.get(
                "https://api.github.com/search/issues",
                headers=_gh_headers(),
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 30},
            )
            if r.status_code in (403, 429):
                sys.stderr.write(
                    f"[codenova] GitHub search rate-limited (HTTP {r.status_code}) "
                    f"for lang={lang}. Remaining search quota exhausted.\n"
                )
                rate_limited = True
                break
            if r.status_code != 200:
                sys.stderr.write(f"[codenova] Search HTTP {r.status_code} for lang={lang}, skipping.\n")
                continue
            for item in r.json().get("items", []):
                labels = [lb["name"] for lb in item.get("labels", [])]
                all_issues.append({
                    "title":        item["title"],
                    "url":          item["html_url"],
                    "repo":         item["repository_url"].replace("https://api.github.com/repos/", ""),
                    "repo_url":     item["repository_url"].replace(
                                        "https://api.github.com/repos/",
                                        "https://github.com/"),
                    "language":     lang,
                    "difficulty":   _estimate_difficulty(labels),
                    "labels":       labels,
                    "comments":     item.get("comments", 0),
                    "updated_at":   item.get("updated_at", "")[:10],
                    "body_preview": (item.get("body") or "")[:300].strip(),
                })

    if rate_limited and not all_issues:
        # Complete miss — nothing fetched before the rate limit hit.
        # Return empty so callers can surface a clear error.
        return [], False

    # Cache whatever we managed to get (even partial results from before rate-limit)
    if all_issues:
        _cache_set(cache_key, all_issues, ttl)

    return all_issues, False


# =====================================================
# MCP TOOLS
# =====================================================

@mcp.tool()
async def get_my_profile() -> dict:
    """
    Fetch your GitHub profile and infer your skill set from your public repos.
    Call this first — it loads your identity for all other tools.
    Your GITHUB_TOKEN is read automatically from .env.
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    github_id = user["id"]
    login     = user["login"]

    cached = _db_get_profile(github_id)
    if cached:
        return {
            "source":                 "cache",
            "username":               cached.get("username"),
            "name":                   cached.get("name", ""),
            "avatar_url":             cached.get("avatar_url", ""),
            "public_repos":           cached.get("public_repos", 0),
            "skills":                 cached.get("skills", {}),
            "interests":              cached.get("interests", []),
            "recommended_difficulty": _recommend_difficulty(cached.get("skills", {})),
        }

    repos = []
    try:
        page = 1
        async with httpx.AsyncClient(timeout=20) as client:
            while len(repos) < 200:
                r = await client.get(
                    f"https://api.github.com/users/{login}/repos",
                    headers=_gh_headers(),
                    params={"per_page": 100, "page": page, "sort": "updated", "type": "public"},
                )
                if r.status_code != 200:
                    break
                batch = r.json()
                if not batch:
                    break
                repos.extend(batch)
                page += 1
                if len(batch) < 100:
                    break
    except Exception as e:
        sys.stderr.write(f"[codenova] repo fetch warn: {e}\n")

    skills, interests = _extract_skills(repos)
    # Only cache if we actually got skill data — prevents poisoning the cache
    # with an empty dict when the repo fetch returned nothing useful.
    if skills:
        _db_save_profile(github_id, login, user, skills, interests)
    else:
        sys.stderr.write(
            f"[codenova] Skipping DB cache write for '{login}': "
            f"no skills extracted from {len(repos)} repos. "
            "Will retry on next get_my_profile() call.\n"
        )

    top_repos = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:5]

    return {
        "source":       "github",
        "username":     login,
        "name":         user.get("name", ""),
        "bio":          user.get("bio", ""),
        "avatar_url":   user.get("avatar_url", ""),
        "public_repos": user.get("public_repos", 0),
        "followers":    user.get("followers", 0),
        "location":     user.get("location", ""),
        "skills":       skills,
        "interests":    interests,
        "top_repos": [
            {
                "name":        r["name"],
                "description": r.get("description", ""),
                "language":    r.get("language", ""),
                "stars":       r.get("stargazers_count", 0),
                "url":         r["html_url"],
            }
            for r in top_repos
        ],
        "recommended_difficulty": _recommend_difficulty(skills),
    }


@mcp.tool()
async def recommend_issues(
    difficulty: str = "auto",
    count: int = 10,
) -> dict:
    """
    Full pipeline: loads your skills then finds matching open GitHub issues.
    Results are cached for 1 hour to avoid GitHub Search API rate limits (30 req/min).
    This is the main 'help me contribute' tool.

    Args:
        difficulty: 'beginner', 'intermediate', 'advanced', or 'auto'
        count:      Number of issues to return (max 20)
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    profile = await get_my_profile()
    if "error" in profile:
        return profile

    skills: dict    = profile.get("skills", {})
    interests: list = profile.get("interests", [])

    if difficulty == "auto":
        difficulty = profile.get("recommended_difficulty", "beginner")

    top_langs = list(skills.keys())[:2] or ["Python", "JavaScript"]
    label = "good first issue" if difficulty == "beginner" else "help wanted"

    # Build a stable cache key: user + difficulty + their top languages
    langs_key  = "_".join(sorted(top_langs))
    cache_key  = f"codenova:recs:{user['id']}:{difficulty}:{langs_key}"

    queries  = [
        f'label:"{label}" language:{lang} stars:>50 is:open is:issue no:assignee'
        for lang in top_langs
    ]
    lang_tags = top_langs

    issues, from_cache = await _search_issues_cached(cache_key, queries, lang_tags, ttl=_TTL_RECS)

    if not issues and not from_cache:
        return {
            "error": "github_search_rate_limited",
            "message": (
                "GitHub's Search API rate limit (30 req/min) was hit and no cached results "
                "are available yet. Wait ~60 seconds and try again — subsequent calls will "
                "be served from cache for 1 hour without touching the Search API."
            ),
            "username":   user["login"],
            "difficulty": difficulty,
        }

    def _score(issue: dict) -> float:
        lang          = issue.get("language", "")
        skill_val     = skills.get(lang, 0) / 100
        recency       = issue.get("updated_at", "")
        try:
            days_old = (datetime.utcnow() - datetime.strptime(recency, "%Y-%m-%d")).days
        except Exception:
            days_old = 365
        recency_score  = max(0, 1 - days_old / 180)
        low_comp       = 0.2 if issue.get("comments", 99) < 3 else 0
        interest_bonus = 0.15 if any(
            i in " ".join(issue.get("labels", [])).lower() for i in interests
        ) else 0
        return skill_val + recency_score * 0.3 + low_comp + interest_bonus

    issues.sort(key=_score, reverse=True)

    return {
        "username":      user["login"],
        "difficulty":    difficulty,
        "skill_summary": {k: v for k, v in list(skills.items())[:6]},
        "count":         len(issues[:count]),
        "issues":        issues[:count],
        "cached":        from_cache,
        "next_step":     "Call get_issue_details(url) on any issue, then explain_code_file() before editing.",
    }


@mcp.tool()
async def get_issue_details(issue_url: str) -> dict:
    """
    Fetch full details of a GitHub issue including comments and an AI task summary.

    Args:
        issue_url: Full GitHub issue URL, e.g. https://github.com/django/django/issues/1234
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_url)
    if not m:
        return {"error": "Invalid URL. Expected https://github.com/owner/repo/issues/NUMBER"}

    repo, number = m.group(1), m.group(2)
    try:
        issue = await _gh_get(f"https://api.github.com/repos/{repo}/issues/{number}")
    except httpx.HTTPStatusError as e:
        return {"error": f"GitHub API error {e.response.status_code}"}

    comments = []
    try:
        raw = await _gh_get(
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            {"per_page": 10},
        )
        for c in raw[:10]:
            comments.append({
                "author":     c["user"]["login"],
                "body":       (c.get("body") or "")[:500],
                "created_at": c.get("created_at", "")[:10],
            })
    except Exception:
        pass

    labels = [lb["name"] for lb in issue.get("labels", [])]

    task_summary = ""
    if GROQ_API_KEY and issue.get("body"):
        try:
            task_summary = _call_groq(
                f"Summarize what a contributor needs to DO to fix/implement this GitHub issue "
                f"in 3 concrete bullet points.\n\n"
                f"Title: {issue['title']}\n\nBody:\n{(issue.get('body') or '')[:2000]}",
                max_tokens=300,
            )
        except Exception:
            pass

    return {
        "title":          issue["title"],
        "url":            issue["html_url"],
        "repo":           repo,
        "state":          issue["state"],
        "author":         issue["user"]["login"],
        "labels":         labels,
        "difficulty":     _estimate_difficulty(labels),
        "created_at":     issue.get("created_at", "")[:10],
        "updated_at":     issue.get("updated_at", "")[:10],
        "comments_count": issue.get("comments", 0),
        "assignees":      [a["login"] for a in issue.get("assignees", [])],
        "is_assigned":    bool(issue.get("assignees")),
        "body":           (issue.get("body") or "")[:2000],
        "comments":       comments,
        "task_summary":   task_summary,
    }


@mcp.tool()
async def explain_code_file(
    repo_full_name: str,
    file_path: str,
    issue_context: str = "",
) -> dict:
    """
    Fetch a source file from GitHub and explain it for a first-time contributor.

    Args:
        repo_full_name: 'owner/repo' (e.g. 'django/django')
        file_path:      Path inside repo (e.g. 'django/db/models/query.py')
        issue_context:  Optional issue title for a more focused explanation.
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    if "/" not in repo_full_name:
        return {"error": "Use 'owner/repo' format."}

    default_branch = "main"
    try:
        repo_info = await _gh_get(f"https://api.github.com/repos/{repo_full_name}")
        default_branch = repo_info.get("default_branch", "main")
    except Exception:
        pass

    content = ""
    async with httpx.AsyncClient(timeout=15) as client:
        for branch in [default_branch, "main", "master"]:
            r = await client.get(
                f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/{file_path}"
            )
            if r.status_code == 200:
                content = r.text
                break

    if not content:
        return {"error": f"Could not fetch '{file_path}' from '{repo_full_name}'. Check the path."}

    lines       = content.splitlines()
    line_count  = len(lines)
    content_llm = "\n".join(lines[:400])

    explanation = key_concepts = modification_tips = ""

    if GROQ_API_KEY:
        try:
            raw = _call_groq(
                f"You are helping a developer contribute to open source for the first time.\n"
                f"{'Issue context: ' + issue_context if issue_context else ''}\n"
                f"File: {file_path} in {repo_full_name}\n\n```\n{content_llm}\n```\n\n"
                f"Reply in EXACTLY this format:\n\n"
                f"WHAT IT DOES:\n(2-3 sentences)\n\n"
                f"KEY CONCEPTS:\n- concept 1\n- concept 2\n- concept 3\n\n"
                f"WHERE TO LOOK:\n- specific area for the issue/feature\n- what not to touch\n- how to test",
                max_tokens=1000,
            )
            current = None
            for line in raw.split("\n"):
                up = line.strip().upper()
                if "WHAT IT DOES"   in up: current = "w"
                elif "KEY CONCEPTS" in up: current = "k"
                elif "WHERE TO LOOK" in up: current = "t"
                elif current == "w" and line.strip(): explanation       += line + "\n"
                elif current == "k" and line.strip(): key_concepts      += line + "\n"
                elif current == "t" and line.strip(): modification_tips += line + "\n"
        except Exception as e:
            explanation = f"LLM error: {e}"
    else:
        explanation = "GROQ_API_KEY not set in .env — AI explanations disabled."

    return {
        "file":              file_path,
        "repo":              repo_full_name,
        "lines":             line_count,
        "truncated":         line_count > 400,
        "explanation":       explanation.strip(),
        "key_concepts":      key_concepts.strip(),
        "modification_tips": modification_tips.strip(),
        "full_source":       content if line_count <= 150 else content_llm,
    }


@mcp.tool()
async def get_repo_details(repo_full_name: str) -> dict:
    """
    Get details about a GitHub repo: languages, CONTRIBUTING guide, README, clone command.

    Args:
        repo_full_name: 'owner/repo' (e.g. 'facebook/react')
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    if "/" not in repo_full_name:
        return {"error": "Use 'owner/repo' format."}

    try:
        repo = await _gh_get(f"https://api.github.com/repos/{repo_full_name}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"Repo '{repo_full_name}' not found."}
        return {"error": f"GitHub API error {e.response.status_code}"}

    langs = {}
    try:
        langs = await _gh_get(f"https://api.github.com/repos/{repo_full_name}/languages")
    except Exception:
        pass

    has_contributing = False
    contributing_url = ""
    readme_preview   = ""

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/contents/CONTRIBUTING.md",
            headers=_gh_headers(),
        )
        if r.status_code == 200:
            has_contributing = True
            contributing_url = f"https://github.com/{repo_full_name}/blob/HEAD/CONTRIBUTING.md"

        for branch in [repo.get("default_branch", "main"), "main", "master"]:
            r2 = await client.get(
                f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/README.md"
            )
            if r2.status_code == 200:
                readme_preview = r2.text[:600].strip()
                break

    total_bytes = sum(langs.values()) or 1
    lang_pct    = {k: round(v / total_bytes * 100, 1) for k, v in langs.items()}

    return {
        "name":                   repo["name"],
        "full_name":              repo["full_name"],
        "description":            repo.get("description", ""),
        "url":                    repo["html_url"],
        "stars":                  repo.get("stargazers_count", 0),
        "forks":                  repo.get("forks_count", 0),
        "open_issues":            repo.get("open_issues_count", 0),
        "languages":              lang_pct,
        "topics":                 repo.get("topics", []),
        "license":                (repo.get("license") or {}).get("spdx_id", ""),
        "has_contributing_guide": has_contributing,
        "contributing_url":       contributing_url,
        "readme_preview":         readme_preview,
        "default_branch":         repo.get("default_branch", "main"),
        "last_updated":           repo.get("updated_at", "")[:10],
        "archived":               repo.get("archived", False),
        "clone_command":          f"git clone https://github.com/{repo_full_name}.git",
        "fork_url":               f"https://github.com/{repo_full_name}/fork",
        "good_first_issues_url":  (
            f"https://github.com/{repo_full_name}/issues"
            f"?q=is:open+label:\"good+first+issue\""
        ),
    }


@mcp.tool()
async def find_issues(
    languages: str,
    difficulty: str = "beginner",
    count: int = 10,
    min_stars: int = 50,
) -> dict:
    """
    Search GitHub for open issues by language and difficulty.
    Results are cached for 1 hour to avoid GitHub Search API rate limits (30 req/min).
    Use recommend_issues() for issues auto-matched to your skill profile.

    Args:
        languages:  Comma-separated (e.g. 'Python,JavaScript')
        difficulty: 'beginner', 'intermediate', or 'advanced'
        count:      Results to return (max 30)
        min_stars:  Minimum repo stars
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    if not lang_list:
        return {"error": "Provide at least one language."}

    label = "good first issue" if difficulty == "beginner" else "help wanted"

    # Stable cache key: difficulty + sorted languages + min_stars
    langs_key = "_".join(sorted(lang_list[:5]))
    cache_key = f"codenova:issues:{difficulty}:{langs_key}:s{min_stars}"

    queries = [
        f'label:"{label}" language:{lang} stars:>{min_stars} is:open is:issue no:assignee'
        for lang in lang_list[:5]
    ]

    all_issues, from_cache = await _search_issues_cached(
        cache_key, queries, lang_list[:5], ttl=_TTL_ISSUES
    )

    if not all_issues and not from_cache:
        return {
            "error": "github_search_rate_limited",
            "message": (
                "GitHub's Search API rate limit (30 req/min) was hit and no cached results "
                "are available. Wait ~60 seconds and try again."
            ),
            "difficulty": difficulty,
            "languages_searched": lang_list,
        }

    all_issues.sort(key=lambda x: (x["comments"], x["updated_at"]))
    return {
        "count":              len(all_issues[:count]),
        "difficulty":         difficulty,
        "languages_searched": lang_list,
        "cached":             from_cache,
        "issues":             all_issues[:count],
    }


@mcp.tool()
async def search_repos(
    query: str,
    language: str = "",
    min_stars: int = 100,
    count: int = 10,
) -> dict:
    """
    Search GitHub for repositories to contribute to.

    Args:
        query:     Keywords (e.g. 'web framework', 'machine learning cli')
        language:  Filter by language (e.g. 'Python'). Empty = any.
        min_stars: Minimum star count
        count:     Results to return (max 20)
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    q = f"{query} stars:>{min_stars} is:public archived:false"
    if language:
        q += f" language:{language}"

    try:
        data = await _gh_get(
            "https://api.github.com/search/repositories",
            {"q": q, "sort": "stars", "order": "desc", "per_page": min(count, 20)},
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 429):
            return {"error": "GitHub rate limit hit. Wait ~1 minute and try again."}
        return {"error": f"GitHub API error {e.response.status_code}"}

    return {
        "query": query,
        "count": len(data.get("items", [])),
        "repos": [
            {
                "name":        r["full_name"],
                "description": r.get("description", ""),
                "url":         r["html_url"],
                "stars":       r.get("stargazers_count", 0),
                "language":    r.get("language", ""),
                "topics":      r.get("topics", [])[:5],
                "open_issues": r.get("open_issues_count", 0),
                "last_updated": r.get("updated_at", "")[:10],
                "good_first_issues_url": (
                    f"https://github.com/{r['full_name']}/issues"
                    f"?q=is:open+label:\"good+first+issue\""
                ),
            }
            for r in data.get("items", [])[:count]
        ],
    }


@mcp.tool()
async def check_rate_limit() -> dict:
    """
    Check your current GitHub API rate limit.
    Call this if tools are returning rate limit errors.
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    try:
        data = await _gh_get("https://api.github.com/rate_limit")

        def _fmt(r: dict) -> dict:
            rem   = r.get("remaining", 0)
            lim   = r.get("limit", 0)
            reset = r.get("reset", 0)
            secs  = max(0, reset - int(datetime.utcnow().timestamp()))
            return {
                "remaining":  rem,
                "limit":      lim,
                "used":       lim - rem,
                "resets_in":  f"{secs}s",
                "resets_at":  datetime.utcfromtimestamp(reset).isoformat() + "Z" if reset else "",
            }

        return {
            "for_user": user["login"],
            "core":     _fmt(data.get("resources", {}).get("core", {})),
            "search":   _fmt(data.get("resources", {}).get("search", {})),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def clear_profile_cache() -> dict:
    """
    Clears your cached GitHub profile from MongoDB (and Redis/memory if connected).
    Use this if get_my_profile() is returning stale or empty skill data.
    After clearing, the next get_my_profile() call will re-fetch live from GitHub.
    """
    if not GITHUB_TOKEN:
        return _NO_TOKEN

    user = await _resolve_user()
    if not user:
        return _BAD_TOKEN

    github_id = user["id"]
    login     = user["login"]
    cleared   = []

    # Clear MongoDB profile
    if _db is not None:
        try:
            result = _db.users.delete_one({"github_id": github_id})
            if result.deleted_count:
                cleared.append("MongoDB profile")
                sys.stderr.write(f"[codenova] Cleared MongoDB profile for {login}\n")
        except Exception as e:
            sys.stderr.write(f"[codenova] MongoDB clear error: {e}\n")

    # Clear whoami key + all recommendations for this user
    whoami_key = f"codenova:whoami:{GITHUB_TOKEN[:16]}"
    _cache_delete(whoami_key)
    _cache_delete_prefix(f"codenova:recs:{user['id']}:")
    cleared.append("search/recommendation cache (Redis + memory)")

    return {
        "status":  "cleared",
        "username": login,
        "cleared": cleared,
        "message": (
            f"Cache cleared for '{login}'. "
            "Call get_my_profile() to rebuild with fresh data."
        ),
    }


# =====================================================
# ENTRY POINT  (stdio — Claude Desktop)
# =====================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")

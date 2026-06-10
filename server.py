# ================================================
# CodeNova MCP Server
# Pure MCP server — stdio transport for Claude Desktop.
#
# SECURITY MODEL:
#   1. GITHUB_USERNAME in .env  → only this account's data is served.
#      Tools do NOT accept arbitrary usernames — locked to the owner.
#   2. MCP_SECRET in .env       → every tool call must supply this token
#      via the `secret` parameter. Wrong/missing = blocked immediately.
#   3. MongoDB queries are always scoped to the owner's github_id.
#      No cross-user data is ever readable.
#
# Setup: set GITHUB_USERNAME, GITHUB_TOKEN, MCP_SECRET in .env
# ================================================

import sys
import os

# Force UTF-8 stdout/stderr (Windows cp1252 fix)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import httpx
import re
import secrets as _secrets
from datetime import datetime
from fastmcp import FastMCP

# =====================================================
# CONFIG  (all from .env — never hardcoded)
# =====================================================

GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "").strip()   # owner's GitHub login
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "").strip()
MCP_SECRET      = os.getenv("MCP_SECRET", "").strip()        # tool access password

if not GITHUB_USERNAME:
    sys.stderr.write(
        "[codenova] FATAL: GITHUB_USERNAME is not set in .env. "
        "Set it to your GitHub username and restart.\n"
    )

if not MCP_SECRET:
    sys.stderr.write(
        "[codenova] WARNING: MCP_SECRET is not set. "
        "Anyone who can invoke this server can call all tools. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(24))\"\n"
    )

GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GITHUB_TOKEN:
    GH_HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

# =====================================================
# OPTIONAL: MongoDB + Redis  (graceful fallback)
# =====================================================

_db    = None
_cache = None

try:
    from database.models import Database
    Database.connect()
    _db = Database.get_db()
    sys.stderr.write("[codenova] MongoDB connected\n")
except Exception as _e:
    sys.stderr.write(f"[codenova] MongoDB unavailable (no caching): {_e}\n")

try:
    from cache.redis_manager import CacheManager
    CacheManager.connect()
    _cache = CacheManager
    sys.stderr.write("[codenova] Redis connected\n")
except Exception as _e:
    sys.stderr.write(f"[codenova] Redis unavailable (no caching): {_e}\n")

# =====================================================
# FastMCP INSTANCE
# =====================================================

mcp = FastMCP(
    "codenova",
    instructions=(
        "CodeNova is a personal open-source contribution mentor locked to one GitHub account. "
        "Every tool requires a `secret` parameter matching the server's MCP_SECRET. "
        "Start with get_my_profile(), then recommend_issues(), then get_issue_details() "
        "and explain_code_file() before touching any code."
    ),
)

# =====================================================
# AUTH GUARD
# =====================================================

_DENY = {"error": "ACCESS_DENIED", "message": "Invalid or missing secret. Set MCP_SECRET in .env and pass it as the `secret` argument."}

def _auth(secret: str) -> bool:
    """Return True only if secret matches MCP_SECRET (constant-time compare)."""
    if not MCP_SECRET:
        return True   # No secret configured → open (warn was already printed)
    return _secrets.compare_digest(secret.strip(), MCP_SECRET)


# =====================================================
# GITHUB HELPERS
# =====================================================

async def _gh_get(url: str, params: dict = None) -> dict | list:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=GH_HEADERS, params=params or {})
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


DIFFICULTY_BEGINNER    = {"good first issue","good-first-issue","beginner","easy","starter","first-timers-only","help wanted","beginner-friendly"}
DIFFICULTY_ADVANCED    = {"advanced","hard","complex","performance","security","architecture"}
DIFFICULTY_INTERMEDIATE= {"intermediate","medium","enhancement","feature","improvement"}

TOPIC_INTEREST = {
    "react":"web","vue":"web","angular":"web","nextjs":"web","html":"web","css":"web",
    "frontend":"frontend","django":"backend","flask":"backend","fastapi":"backend",
    "express":"backend","backend":"backend","api":"backend",
    "machine-learning":"ml","deep-learning":"ml","tensorflow":"ml","pytorch":"ml","nlp":"ml",
    "data-science":"data","docker":"infra","kubernetes":"infra","devops":"infra",
    "terraform":"infra","aws":"cloud","gcp":"cloud","azure":"cloud",
    "esp32":"embedded","arduino":"embedded","iot":"embedded","embedded":"embedded","lora":"embedded",
    "security":"security","cryptography":"security",
    "android":"mobile","ios":"mobile","flutter":"mobile",
    "mongodb":"database","postgresql":"database","redis":"database","sql":"database",
}

def _estimate_difficulty(labels: list) -> str:
    s = {l.lower() for l in labels}
    if s & DIFFICULTY_BEGINNER:    return "beginner"
    if s & DIFFICULTY_ADVANCED:    return "advanced"
    if s & DIFFICULTY_INTERMEDIATE: return "intermediate"
    return "beginner"

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
            name = (repo.get("name","") + " " + (repo.get("description") or "")).lower()
            for kw, canon in [
                ("python","Python"),("javascript","JavaScript"),("typescript","TypeScript"),
                ("rust","Rust"),("golang","Go"),("arduino","C"),("esp32","C"),
                ("java ","Java"),("c++","C++"),("shell","Shell"),
            ]:
                if kw in name:
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


def _db_save_profile(github_user: dict, skills: dict, interests: list):
    """Save profile to MongoDB, scoped to this owner only."""
    if _db is None:
        return
    try:
        _db.users.update_one(
            {"github_id": github_user["id"]},           # key = numeric github id
            {"$set": {
                "github_id":   github_user["id"],
                "username":    github_user["login"],
                "avatar_url":  github_user.get("avatar_url",""),
                "skills":      skills,
                "interests":   interests,
                "public_repos":github_user.get("public_repos", 0),
                "updated_at":  datetime.utcnow(),
            }},
            upsert=True,
        )
    except Exception as e:
        sys.stderr.write(f"[codenova] DB save warn: {e}\n")


def _db_get_profile() -> dict | None:
    """Read the owner's own profile from MongoDB — no other user's data accessible."""
    if _db is None or not GITHUB_USERNAME:
        return None
    try:
        return _db.users.find_one({"username": GITHUB_USERNAME})
    except Exception:
        return None


# =====================================================
# MCP TOOLS
# =====================================================

@mcp.tool()
async def get_my_profile(secret: str) -> dict:
    """
    Fetch YOUR GitHub profile (the account set in GITHUB_USERNAME in .env).
    Returns skills inferred from your public repos, top languages, interests.

    This is always the first tool to call — it sets context for everything else.

    Args:
        secret: your MCP_SECRET from .env
    """
    if not _auth(secret):
        return _DENY

    if not GITHUB_USERNAME:
        return {"error": "GITHUB_USERNAME not set in .env"}

    # Try DB cache first
    cached = _db_get_profile()
    if cached:
        return {
            "source": "cache",
            "username":    cached.get("username"),
            "name":        cached.get("name",""),
            "avatar_url":  cached.get("avatar_url",""),
            "public_repos":cached.get("public_repos",0),
            "skills":      cached.get("skills",{}),
            "interests":   cached.get("interests",[]),
            "recommended_difficulty": _recommend_difficulty(cached.get("skills",{})),
        }

    # Live fetch from GitHub
    try:
        user = await _gh_get(f"https://api.github.com/users/{GITHUB_USERNAME}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"GitHub user '{GITHUB_USERNAME}' not found. Check GITHUB_USERNAME in .env"}
        return {"error": f"GitHub API error {e.response.status_code}"}

    # Fetch public repos
    repos = []
    try:
        page = 1
        async with httpx.AsyncClient(timeout=20) as client:
            while len(repos) < 200:
                r = await client.get(
                    f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
                    headers=GH_HEADERS,
                    params={"per_page":100,"page":page,"sort":"updated","type":"public"},
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
    _db_save_profile(user, skills, interests)

    top_repos = sorted(repos, key=lambda r: r.get("stargazers_count",0), reverse=True)[:5]

    return {
        "source": "github",
        "username":    user["login"],
        "name":        user.get("name",""),
        "bio":         user.get("bio",""),
        "avatar_url":  user.get("avatar_url",""),
        "public_repos":user.get("public_repos",0),
        "followers":   user.get("followers",0),
        "location":    user.get("location",""),
        "skills":      skills,
        "interests":   interests,
        "top_repos":   [
            {"name":r["name"],"description":r.get("description",""),
             "language":r.get("language",""),"stars":r.get("stargazers_count",0),
             "url":r["html_url"]}
            for r in top_repos
        ],
        "recommended_difficulty": _recommend_difficulty(skills),
    }


def _recommend_difficulty(skills: dict) -> str:
    if not skills:
        return "beginner"
    top = max(skills.values(), default=0)
    if top < 40:   return "beginner"
    if top < 70:   return "intermediate"
    return "advanced"


@mcp.tool()
async def recommend_issues(
    secret: str,
    difficulty: str = "auto",
    count: int = 10,
) -> dict:
    """
    Full pipeline for YOU: reads your skills from .env username → finds matching GitHub issues.
    This is the main 'help me contribute' tool.

    Args:
        secret:     your MCP_SECRET from .env
        difficulty: 'beginner', 'intermediate', 'advanced', or 'auto' (inferred from your skills)
        count:      number of issues to return (max 20)
    """
    if not _auth(secret):
        return _DENY

    # Get profile (uses DB cache if available)
    profile = await get_my_profile(secret)
    if "error" in profile:
        return profile

    skills: dict  = profile.get("skills", {})
    interests: list = profile.get("interests", [])

    if difficulty == "auto":
        difficulty = profile.get("recommended_difficulty", "beginner")

    top_langs = list(skills.keys())[:4] or ["Python", "JavaScript"]

    # Fetch issues per language
    label_map = {"beginner":"good first issue","intermediate":"help wanted","advanced":"help wanted"}
    label = label_map.get(difficulty, "good first issue")

    all_issues = []
    async with httpx.AsyncClient(timeout=30) as client:
        for lang in top_langs:
            q = f'label:"{label}" language:{lang} stars:>50 is:open is:issue no:assignee'
            r = await client.get(
                "https://api.github.com/search/issues",
                headers=GH_HEADERS,
                params={"q":q,"sort":"updated","order":"desc","per_page":15},
            )
            if r.status_code == 403:
                return {"error":"GitHub rate limit hit. Wait ~1 minute."}
            if r.status_code != 200:
                continue
            for item in r.json().get("items",[]):
                labels = [lb["name"] for lb in item.get("labels",[])]
                all_issues.append({
                    "title":       item["title"],
                    "url":         item["html_url"],
                    "repo":        item["repository_url"].replace("https://api.github.com/repos/",""),
                    "repo_url":    item["repository_url"].replace("https://api.github.com/repos/","https://github.com/"),
                    "language":    lang,
                    "difficulty":  _estimate_difficulty(labels),
                    "labels":      labels,
                    "comments":    item.get("comments",0),
                    "updated_at":  item.get("updated_at","")[:10],
                    "body_preview":(item.get("body") or "")[:300].strip(),
                })

    # Score: skill match + recency + low competition
    def _score(issue: dict) -> float:
        lang = issue.get("language","")
        skill_val = skills.get(lang,0) / 100
        recency = issue.get("updated_at","")
        try:
            days_old = (datetime.utcnow() - datetime.strptime(recency, "%Y-%m-%d")).days
        except Exception:
            days_old = 365
        recency_score = max(0, 1 - days_old / 180)
        low_comp = 0.2 if issue.get("comments",99) < 3 else 0
        interest_bonus = 0.15 if any(i in " ".join(issue.get("labels",[])).lower() for i in interests) else 0
        return skill_val + recency_score * 0.3 + low_comp + interest_bonus

    all_issues.sort(key=_score, reverse=True)

    return {
        "username":     GITHUB_USERNAME,
        "difficulty":   difficulty,
        "skill_summary":{k:v for k,v in list(skills.items())[:6]},
        "count":        len(all_issues[:count]),
        "issues":       all_issues[:count],
        "next_step":    "Call get_issue_details(url) on any issue above, then explain_code_file() before editing.",
    }


@mcp.tool()
async def get_issue_details(secret: str, issue_url: str) -> dict:
    """
    Fetch full details of a GitHub issue including all comments + AI task summary.

    Args:
        secret:    your MCP_SECRET from .env
        issue_url: full GitHub issue URL, e.g. https://github.com/django/django/issues/1234
    """
    if not _auth(secret):
        return _DENY

    m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_url)
    if not m:
        return {"error":"Invalid URL. Expected https://github.com/owner/repo/issues/NUMBER"}

    repo, number = m.group(1), m.group(2)
    try:
        issue = await _gh_get(f"https://api.github.com/repos/{repo}/issues/{number}")
    except httpx.HTTPStatusError as e:
        return {"error": f"GitHub API error {e.response.status_code}"}

    # Comments
    comments = []
    try:
        raw = await _gh_get(f"https://api.github.com/repos/{repo}/issues/{number}/comments", {"per_page":10})
        for c in raw[:10]:
            comments.append({
                "author":     c["user"]["login"],
                "body":       (c.get("body") or "")[:500],
                "created_at": c.get("created_at","")[:10],
            })
    except Exception:
        pass

    labels = [lb["name"] for lb in issue.get("labels",[])]

    # AI task summary
    task_summary = ""
    if GROQ_API_KEY and issue.get("body"):
        try:
            task_summary = _call_groq(
                f"Summarize what a contributor needs to DO to fix/implement this GitHub issue in 3 concrete bullet points.\n\n"
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
        "created_at":     issue.get("created_at","")[:10],
        "updated_at":     issue.get("updated_at","")[:10],
        "comments_count": issue.get("comments",0),
        "assignees":      [a["login"] for a in issue.get("assignees",[])],
        "is_assigned":    bool(issue.get("assignees")),
        "body":           (issue.get("body") or "")[:2000],
        "comments":       comments,
        "task_summary":   task_summary,
    }


@mcp.tool()
async def explain_code_file(
    secret: str,
    repo_full_name: str,
    file_path: str,
    issue_context: str = "",
) -> dict:
    """
    Fetch a file from a GitHub repo and explain it for a first-time contributor.

    Args:
        secret:         your MCP_SECRET from .env
        repo_full_name: 'owner/repo' (e.g. 'django/django')
        file_path:      path inside repo (e.g. 'django/db/models/query.py')
        issue_context:  optional issue title for more focused explanation
    """
    if not _auth(secret):
        return _DENY

    if "/" not in repo_full_name:
        return {"error": "Use 'owner/repo' format."}

    # Get default branch
    default_branch = "main"
    try:
        repo_info = await _gh_get(f"https://api.github.com/repos/{repo_full_name}")
        default_branch = repo_info.get("default_branch","main")
    except Exception:
        pass

    # Fetch file content
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
        return {"error": f"Could not fetch '{file_path}' from '{repo_full_name}'. Check the path and branch."}

    lines = content.splitlines()
    line_count = len(lines)
    content_for_llm = "\n".join(lines[:400])

    explanation = key_concepts = modification_tips = ""

    if GROQ_API_KEY:
        try:
            current = None
            raw = _call_groq(
                f"You are helping a developer contribute to open source for the first time.\n"
                f"{'Issue context: ' + issue_context if issue_context else ''}\n"
                f"File: {file_path} in {repo_full_name}\n\n```\n{content_for_llm}\n```\n\n"
                f"Reply in EXACTLY this format:\n\n"
                f"WHAT IT DOES:\n(2-3 sentences)\n\n"
                f"KEY CONCEPTS:\n- concept 1\n- concept 2\n- concept 3\n\n"
                f"WHERE TO LOOK:\n- specific area for the issue/feature\n- what not to touch\n- how to test",
                max_tokens=1000,
            )
            for line in raw.split("\n"):
                up = line.strip().upper()
                if "WHAT IT DOES" in up:   current = "w"
                elif "KEY CONCEPTS" in up: current = "k"
                elif "WHERE TO LOOK" in up:current = "t"
                elif current == "w" and line.strip(): explanation       += line + "\n"
                elif current == "k" and line.strip(): key_concepts      += line + "\n"
                elif current == "t" and line.strip(): modification_tips += line + "\n"
        except Exception as e:
            explanation = f"LLM error: {e}"
    else:
        explanation = "Set GROQ_API_KEY in .env for AI explanations."

    return {
        "file":             file_path,
        "repo":             repo_full_name,
        "lines":            line_count,
        "truncated":        line_count > 400,
        "explanation":      explanation.strip(),
        "key_concepts":     key_concepts.strip(),
        "modification_tips":modification_tips.strip(),
        "full_source":      content if line_count <= 150 else content_for_llm,
    }


@mcp.tool()
async def get_repo_details(secret: str, repo_full_name: str) -> dict:
    """
    Get details about a GitHub repository: languages, CONTRIBUTING guide, README, setup clone command.

    Args:
        secret:         your MCP_SECRET from .env
        repo_full_name: 'owner/repo' (e.g. 'facebook/react')
    """
    if not _auth(secret):
        return _DENY

    if "/" not in repo_full_name:
        return {"error":"Use 'owner/repo' format."}

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
            headers=GH_HEADERS,
        )
        if r.status_code == 200:
            has_contributing = True
            contributing_url = f"https://github.com/{repo_full_name}/blob/HEAD/CONTRIBUTING.md"

        default_branch = repo.get("default_branch","main")
        for branch in [default_branch, "main", "master"]:
            r2 = await client.get(
                f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/README.md"
            )
            if r2.status_code == 200:
                readme_preview = r2.text[:600].strip()
                break

    total_bytes = sum(langs.values()) or 1
    lang_pct = {k: round(v / total_bytes * 100, 1) for k, v in langs.items()}

    return {
        "name":                  repo["name"],
        "full_name":             repo["full_name"],
        "description":           repo.get("description",""),
        "url":                   repo["html_url"],
        "stars":                 repo.get("stargazers_count",0),
        "forks":                 repo.get("forks_count",0),
        "open_issues":           repo.get("open_issues_count",0),
        "languages":             lang_pct,
        "topics":                repo.get("topics",[]),
        "license":               (repo.get("license") or {}).get("spdx_id",""),
        "has_contributing_guide":has_contributing,
        "contributing_url":      contributing_url,
        "readme_preview":        readme_preview,
        "default_branch":        repo.get("default_branch","main"),
        "last_updated":          repo.get("updated_at","")[:10],
        "archived":              repo.get("archived",False),
        "clone_command":         f"git clone https://github.com/{repo_full_name}.git",
        "fork_url":              f"https://github.com/{repo_full_name}/fork",
        "good_first_issues_url": f"https://github.com/{repo_full_name}/issues?q=is:open+label:\"good+first+issue\"",
    }


@mcp.tool()
async def find_issues(
    secret: str,
    languages: str,
    difficulty: str = "beginner",
    count: int = 10,
    min_stars: int = 50,
) -> dict:
    """
    Search GitHub for open issues by language and difficulty.
    Use recommend_issues() instead if you want issues matched to YOUR skill profile.

    Args:
        secret:     your MCP_SECRET from .env
        languages:  comma-separated (e.g. 'Python,JavaScript')
        difficulty: 'beginner', 'intermediate', or 'advanced'
        count:      results to return (max 30)
        min_stars:  minimum repo stars
    """
    if not _auth(secret):
        return _DENY

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    if not lang_list:
        return {"error":"Provide at least one language."}

    label = "good first issue" if difficulty == "beginner" else "help wanted"
    all_issues = []

    async with httpx.AsyncClient(timeout=30) as client:
        for lang in lang_list[:5]:
            q = f'label:"{label}" language:{lang} stars:>{min_stars} is:open is:issue no:assignee'
            r = await client.get(
                "https://api.github.com/search/issues",
                headers=GH_HEADERS,
                params={"q":q,"sort":"updated","order":"desc","per_page":min(count*2,30)},
            )
            if r.status_code == 403:
                return {"error":"GitHub rate limit hit. Wait ~1 minute."}
            if r.status_code != 200:
                continue
            for item in r.json().get("items",[]):
                labels = [lb["name"] for lb in item.get("labels",[])]
                all_issues.append({
                    "title":       item["title"],
                    "url":         item["html_url"],
                    "repo":        item["repository_url"].replace("https://api.github.com/repos/",""),
                    "language":    lang,
                    "difficulty":  _estimate_difficulty(labels),
                    "labels":      labels,
                    "comments":    item.get("comments",0),
                    "updated_at":  item.get("updated_at","")[:10],
                    "body_preview":(item.get("body") or "")[:300].strip(),
                })

    all_issues.sort(key=lambda x:(x["comments"], x["updated_at"]))
    return {
        "count":               len(all_issues[:count]),
        "difficulty":          difficulty,
        "languages_searched":  lang_list,
        "issues":              all_issues[:count],
    }


@mcp.tool()
async def search_repos(
    secret: str,
    query: str,
    language: str = "",
    min_stars: int = 100,
    count: int = 10,
) -> dict:
    """
    Search GitHub for repositories to contribute to.

    Args:
        secret:    your MCP_SECRET from .env
        query:     keywords (e.g. 'web framework', 'machine learning cli')
        language:  filter by language (e.g. 'Python'). Empty = any.
        min_stars: minimum star count
        count:     results to return (max 20)
    """
    if not _auth(secret):
        return _DENY

    q = f"{query} stars:>{min_stars} is:public archived:false"
    if language:
        q += f" language:{language}"

    try:
        data = await _gh_get(
            "https://api.github.com/search/repositories",
            {"q":q,"sort":"stars","order":"desc","per_page":min(count,20)},
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            return {"error":"GitHub rate limit hit."}
        return {"error": f"GitHub API error {e.response.status_code}"}

    return {
        "query": query,
        "count": len(data.get("items",[])),
        "repos": [
            {
                "name":        r["full_name"],
                "description": r.get("description",""),
                "url":         r["html_url"],
                "stars":       r.get("stargazers_count",0),
                "language":    r.get("language",""),
                "topics":      r.get("topics",[])[:5],
                "open_issues": r.get("open_issues_count",0),
                "last_updated":r.get("updated_at","")[:10],
                "good_first_issues_url": f"https://github.com/{r['full_name']}/issues?q=is:open+label:\"good+first+issue\"",
            }
            for r in data.get("items",[])[:count]
        ],
    }


@mcp.tool()
async def check_rate_limit(secret: str) -> dict:
    """
    Check current GitHub API rate limit. Call this if tools return rate limit errors.

    Args:
        secret: your MCP_SECRET from .env
    """
    if not _auth(secret):
        return _DENY

    try:
        data = await _gh_get("https://api.github.com/rate_limit")
        def _fmt(r: dict) -> dict:
            rem   = r.get("remaining",0)
            lim   = r.get("limit",0)
            reset = r.get("reset",0)
            secs  = max(0, reset - int(datetime.utcnow().timestamp()))
            return {
                "remaining": rem,
                "limit":     lim,
                "used":      lim - rem,
                "resets_in": f"{secs}s",
                "resets_at": datetime.utcfromtimestamp(reset).isoformat()+"Z" if reset else "",
            }
        return {
            "authenticated": bool(GITHUB_TOKEN),
            "core":   _fmt(data.get("resources",{}).get("core",{})),
            "search": _fmt(data.get("resources",{}).get("search",{})),
            "tip": "" if GITHUB_TOKEN else "Add GITHUB_TOKEN in .env for 5000/hr instead of 60/hr.",
        }
    except Exception as e:
        return {"error": str(e)}


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")

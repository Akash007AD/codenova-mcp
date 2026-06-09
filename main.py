# ================================================
# CodeNova MCP - Main Server
# FastMCP + FastAPI + GitHub OAuth + Redis + MongoDB
# ================================================
#
# LLM PROVIDER SWITCH
# -------------------
# DEFAULT (free):   Groq — llama-3.3-70b-versatile
# PRODUCTION:       Anthropic Claude — uncomment Option B below
#
# To switch to Claude:
#   1. Comment out the three lines under "Option A: Groq"
#   2. Uncomment the three lines under "Option B: Anthropic"
#   3. Add ANTHROPIC_API_KEY to your .env
# ================================================

import os
import secrets
import httpx
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------
# Local Imports
# ------------------------------------------------

from database.models import Database, UserModel, IssueModel, ExplanationModel, ContributionModel
from cache.redis_manager import CacheManager
from tools.github_auth import (
    get_github_oauth_url,
    exchange_code_for_token,
    fetch_github_user,
    fetch_github_repos,
    extract_skills_from_repos,
    verify_pr_on_github,
    encrypt_token,
    decrypt_token,
    create_jwt,
    verify_jwt
)
from tools.matching import get_top_recommendations
from jobs.scheduler import create_scheduler, run_initial_indexing

# ================================================
# LLM CLIENT SETUP
# ================================================

# ── Option A: Groq (FREE — active by default) ──────────────────
# Sign up and get a free key at https://console.groq.com
from groq import Groq
llm_client   = Groq(api_key=os.getenv("GROQ_API_KEY"))
LLM_PROVIDER = "groq"
LLM_MODEL    = "llama-3.3-70b-versatile"

# ── Option B: Anthropic / Claude (best quality, paid) ──────────
# Comment out Option A above, then uncomment the four lines below.
# Get your key at https://console.anthropic.com
# import anthropic
# llm_client   = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
# LLM_PROVIDER = "anthropic"
# LLM_MODEL    = "claude-sonnet-4-6"

# ================================================

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


# ------------------------------------------------
# Shared LLM Call Helper
# ------------------------------------------------

def call_llm(prompt: str, max_tokens: int = 1500) -> str:
    """
    Single function to call whichever LLM is active.
    Swap providers by changing Option A / Option B above.
    """
    if LLM_PROVIDER == "groq":
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3
        )
        return response.choices[0].message.content

    elif LLM_PROVIDER == "anthropic":
        # Uncomment Option B above to reach this branch
        response = llm_client.messages.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")


# ================================================
# App Lifecycle
# ================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all services on startup"""
    print("🚀 Starting CodeNova MCP Server...")
    print(f"   LLM Provider : {LLM_PROVIDER} ({LLM_MODEL})")

    # 1. Connect MongoDB
    Database.connect()

    # 2. Connect Redis
    CacheManager.connect()

    # 3. Start background scheduler
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    print("✅ Background scheduler started")

    # 4. Run initial issue indexing if DB is empty
    run_initial_indexing()

    print("✅ CodeNova MCP Server is ready!")
    yield

    # Shutdown
    print("🛑 Shutting down CodeNova MCP Server...")
    scheduler.shutdown(wait=False)
    Database.close()
    print("✅ Shutdown complete")


# ================================================
# FastAPI App
# ================================================

app = FastAPI(
    title="CodeNova MCP Server",
    description="AI-powered open-source contribution mentor backend",
    version="1.0.0",
    lifespan=lifespan
)

# CORS — allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# ------------------------------------------------
# FastMCP Setup
# ------------------------------------------------

mcp = FastMCP("codenova-mcp")


# ------------------------------------------------
# Auth Middleware Helper
# ------------------------------------------------

def get_current_user(request: Request) -> dict:
    """Extract and verify JWT from Authorization header"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.split(" ")[1]
    payload = verify_jwt(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return payload


# ================================================
# GITHUB OAUTH ROUTES
# ================================================

@app.get("/auth/github/login", tags=["Auth"])
async def github_login():
    """
    Step 1: Redirect user to GitHub OAuth page.
    Frontend calls this → user is sent to GitHub.
    """
    state = secrets.token_urlsafe(32)
    CacheManager.store_oauth_state(state)
    oauth_url = get_github_oauth_url(state)
    return RedirectResponse(url=oauth_url)


@app.get("/auth/github/callback", tags=["Auth"])
async def github_callback(code: str, state: str):
    """
    Step 2: GitHub redirects here with auth code.
    We exchange it for a token, fetch user profile, extract skills.
    """
    # Validate CSRF state
    # In dev mode, skip state validation if Redis missed it
    state_valid = CacheManager.validate_oauth_state(state)
    if not state_valid and os.getenv("ENVIRONMENT", "development") == "production":
        raise HTTPException(status_code=400, detail="Invalid OAuth state — possible CSRF attack")

    # Exchange code for GitHub access token
    try:
        github_token = await exchange_code_for_token(code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fetch GitHub user profile
    github_user = await fetch_github_user(github_token)

    # Fetch repos for skill extraction
    repos = await fetch_github_repos(github_token)
    skills, interests = extract_skills_from_repos(repos)

    # Save to MongoDB
    user_model = UserModel()
    encrypted_token = encrypt_token(github_token)
    user = user_model.create_or_update(github_user, encrypted_token)

    # Update skills
    user_model.update_skills(str(user["_id"]), skills, interests)

    # Create session JWT
    jwt_token = create_jwt(
        user_id=str(user["_id"]),
        github_id=github_user["id"],
        username=github_user["login"]
    )

    # Cache profile in Redis
    CacheManager.cache_profile(github_user["login"], {
        "user_id": str(user["_id"]),
        "username": github_user["login"],
        "avatar_url": github_user.get("avatar_url"),
        "skills": skills,
        "interests": interests,
        "contributions": user.get("contributions", 0),
        "streak": user.get("streak", 0)
    })

    # Redirect frontend with JWT token
    #return RedirectResponse(url=f"{FRONTEND_URL}/auth/success?token={jwt_token}")
    return JSONResponse({"token": jwt_token, "message": "Copy this token and use it in Swagger"})


@app.post("/auth/logout", tags=["Auth"])
async def logout(current_user: dict = Depends(get_current_user)):
    """Invalidate user cache on logout"""
    CacheManager.invalidate_profile(current_user["username"])
    CacheManager.invalidate_recommendations(current_user["sub"])
    return {"message": "Logged out successfully"}


# ================================================
# USER PROFILE ROUTES
# ================================================

@app.get("/api/profile", tags=["Profile"])
async def get_profile(current_user: dict = Depends(get_current_user)):
    """
    Get current user's profile.
    Checks Redis cache first, then MongoDB.
    """
    username = current_user["username"]

    # Check Redis first
    cached = CacheManager.get_profile(username)
    if cached:
        return {"source": "cache", "data": cached}

    # Fetch from MongoDB
    user_model = UserModel()
    user = user_model.get_by_github_id(current_user["github_id"])

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    profile = {
        "user_id": str(user["_id"]),
        "username": user["username"],
        "avatar_url": user.get("avatar_url"),
        "skills": user.get("skills", {}),
        "interests": user.get("interests", []),
        "contributions": user.get("contributions", 0),
        "streak": user.get("streak", 0),
        "total_xp": user.get("total_xp", 0),
        "completed_issues": user.get("completed_issues", []),
        "last_contribution_date": str(user.get("last_contribution_date", ""))
    }

    # Re-cache
    CacheManager.cache_profile(username, profile)

    return {"source": "database", "data": profile}


class UpdateSkillsRequest(BaseModel):
    skills: dict
    interests: list


@app.put("/api/profile/skills", tags=["Profile"])
async def update_skills(
    body: UpdateSkillsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Let user edit their detected skill profile"""
    user_model = UserModel()
    updated = user_model.update_skills(
        current_user["sub"],
        body.skills,
        body.interests
    )

    # Invalidate caches
    CacheManager.invalidate_profile(current_user["username"])
    CacheManager.invalidate_recommendations(current_user["sub"])

    return {"message": "Skills updated", "skills": updated.get("skills")}


# ================================================
# ISSUE RECOMMENDATION ROUTES
# ================================================

class RecommendRequest(BaseModel):
    difficulty: str = "beginner"    # beginner | intermediate | advanced | all
    count: int = 20
    languages: list = []


@app.post("/api/issues/recommend", tags=["Issues"])
async def recommend_issues(
    body: RecommendRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Return personalized issue recommendations.
    Order: Redis cache → MongoDB + algorithm → cache result.
    """
    user_id = current_user["sub"]

    # Check Redis for cached recommendations
    cached = CacheManager.get_recommendations(user_id, body.difficulty)
    if cached:
        return {"source": "cache", "count": len(cached), "issues": cached}

    # Fetch user profile for skills
    user_model = UserModel()
    user = user_model.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_skills    = user.get("skills", {})
    user_interests = user.get("interests", [])

    # Fetch issues from MongoDB
    issue_model = IssueModel()
    issues = issue_model.get_active_issues(
        difficulty=body.difficulty if body.difficulty != "all" else None,
        languages=body.languages if body.languages else None,
        limit=300
    )

    if not issues:
        return {
            "source": "database",
            "count": 0,
            "issues": [],
            "message": "No issues indexed yet — try POST /admin/reindex"
        }

    # Run matching algorithm
    recommendations = get_top_recommendations(
        issues=issues,
        user_skills=user_skills,
        user_interests=user_interests,
        difficulty_filter=body.difficulty if body.difficulty != "all" else None,
        count=body.count
    )

    # Cache results in Redis (1 hour)
    CacheManager.cache_recommendations(user_id, body.difficulty, recommendations)

    return {
        "source": "computed",
        "count": len(recommendations),
        "issues": recommendations
    }


@app.get("/api/issues/stats", tags=["Issues"])
async def get_issue_stats():
    """Return stats about indexed issues"""
    issue_model = IssueModel()
    count = issue_model.count_active()
    return {"active_issues_count": count}


# ================================================
# CODE EXPLANATION ROUTES
# ================================================

class ExplainRequest(BaseModel):
    repo_url: str       # e.g. https://github.com/facebook/react
    file_path: str      # e.g. packages/react/src/React.js
    issue_title: str = ""


@app.post("/api/explain", tags=["Explanation"])
async def explain_code(
    body: ExplainRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Get AI explanation for a code file.
    Cache order: Redis → MongoDB → LLM API → save to both caches.
    """
    # 1. Check Redis cache (fastest)
    cached = CacheManager.get_explanation(body.file_path)
    if cached:
        return {"source": "redis_cache", **cached}

    # 2. Check MongoDB cache (persistent)
    explanation_model = ExplanationModel()
    db_cached = explanation_model.get(body.file_path)
    if db_cached:
        result = {
            "file_path": db_cached["file_path"],
            "explanation": db_cached["explanation"],
            "key_concepts": db_cached.get("key_concepts", ""),
            "modification_tips": db_cached.get("modification_tips", "")
        }
        # Restore to Redis for future hits
        CacheManager.cache_explanation(body.file_path, result)
        explanation_model.increment_used(body.file_path)
        return {"source": "db_cache", **result}

    # 3. Fetch file content from GitHub
    try:
        file_content = await _fetch_github_file(body.repo_url, body.file_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not fetch file: {e}")

    # 4. Build prompt
    prompt = f"""You are helping a beginner developer contribute to open source.
{f"Issue context: {body.issue_title}" if body.issue_title else ""}

Explain this file clearly:

```
{file_content[:8000]}
```

Structure your response EXACTLY as:

WHAT IT DOES:
(2-3 clear sentences)

KEY CONCEPTS:
- concept 1
- concept 2
- concept 3

SAFE MODIFICATIONS:
- what you can safely change
- what to avoid touching
- where to look for the bug/feature area"""

    # 5. Call active LLM (Groq by default, Claude if Option B uncommented)
    try:
        explanation_text = call_llm(prompt, max_tokens=1500)
        sections = _parse_explanation(explanation_text)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"LLM error [{LLM_PROVIDER}/{LLM_MODEL}]: {e}"
        )

    # 6. Save to MongoDB (permanent cache)
    explanation_model.save(
        file_path=body.file_path,
        explanation=sections["what_it_does"],
        key_concepts=sections["key_concepts"],
        modification_tips=sections["safe_modifications"]
    )

    result = {
        "file_path": body.file_path,
        "explanation": sections["what_it_does"],
        "key_concepts": sections["key_concepts"],
        "modification_tips": sections["safe_modifications"],
        "full_response": explanation_text,
        "generated_by": f"{LLM_PROVIDER}/{LLM_MODEL}"
    }

    # 7. Save to Redis (1 week TTL)
    CacheManager.cache_explanation(body.file_path, result)

    return {"source": "generated", **result}


async def _fetch_github_file(repo_url: str, file_path: str) -> str:
    """Fetch raw file content from GitHub (tries main then master)"""
    repo_path = repo_url.replace("https://github.com/", "").rstrip("/")
    raw_base  = f"https://raw.githubusercontent.com/{repo_path}"

    async with httpx.AsyncClient(timeout=15) as client:
        for branch in ("main", "master"):
            response = await client.get(f"{raw_base}/{branch}/{file_path}")
            if response.status_code == 200:
                return response.text

    raise ValueError(f"File not found on main or master: {file_path}")


def _parse_explanation(text: str) -> dict:
    """Parse structured LLM response into sections"""
    sections = {"what_it_does": "", "key_concepts": "", "safe_modifications": ""}
    current  = None
    buffer   = []

    for line in text.split("\n"):
        upper = line.strip().upper()
        if "WHAT IT DOES" in upper:
            if current and buffer:
                sections[current] = "\n".join(buffer).strip()
            current, buffer = "what_it_does", []
        elif "KEY CONCEPTS" in upper:
            if current and buffer:
                sections[current] = "\n".join(buffer).strip()
            current, buffer = "key_concepts", []
        elif "SAFE MODIFICATION" in upper:
            if current and buffer:
                sections[current] = "\n".join(buffer).strip()
            current, buffer = "safe_modifications", []
        elif current and line.strip():
            buffer.append(line)

    if current and buffer:
        sections[current] = "\n".join(buffer).strip()

    # Fallback: dump everything into explanation if parsing failed
    if not sections["what_it_does"]:
        sections["what_it_does"] = text

    return sections


# ================================================
# CONTRIBUTION TRACKING ROUTES
# ================================================

class ContributionRequest(BaseModel):
    issue_id: str
    pr_url: str     # Full URL: https://github.com/owner/repo/pull/123


@app.post("/api/contributions/verify", tags=["Contributions"])
async def verify_contribution(
    body: ContributionRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Verify a PR on GitHub and update user progress.
    Uses the stored GitHub OAuth token to confirm PR authorship.
    """
    user_id  = current_user["sub"]
    username = current_user["username"]

    # Prevent duplicate tracking
    contribution_model = ContributionModel()
    if contribution_model.already_exists(user_id, body.issue_id):
        raise HTTPException(status_code=409, detail="You've already tracked this contribution")

    # Fetch and decrypt user's GitHub token
    user_model = UserModel()
    user = user_model.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        github_token = decrypt_token(user["github_token_encrypted"])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Could not decrypt GitHub token — please log out and log in again"
        )

    # Parse PR URL → owner / repo / pr_number
    try:
        parts     = body.pr_url.rstrip("/").split("/")
        pr_number = int(parts[-1])
        repo_name = parts[-3]
        owner     = parts[-4]
    except (IndexError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid PR URL — expected https://github.com/owner/repo/pull/123")

    # Verify PR on GitHub
    try:
        pr_data = await verify_pr_on_github(github_token, owner, repo_name, pr_number, username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")

    # Get issue metadata from MongoDB
    from bson import ObjectId
    db = Database.get_db()
    try:
        issue = db.issues.find_one({"_id": ObjectId(body.issue_id)})
    except Exception:
        issue = None

    language   = issue.get("languages", ["Unknown"])[0] if issue else "Unknown"
    difficulty = issue.get("difficulty", "beginner")    if issue else "beginner"

    # Record contribution
    contribution_model.create(user_id, body.issue_id, body.pr_url, language, difficulty)

    # Update user stats in MongoDB
    updated_user = user_model.update_contribution(
        user_id, language, difficulty, body.issue_id, body.pr_url
    )

    # Invalidate all user-specific caches
    CacheManager.invalidate_profile(username)
    CacheManager.invalidate_progress(user_id)
    CacheManager.invalidate_recommendations(user_id)

    xp_map    = {"beginner": 50, "intermediate": 100, "advanced": 200}
    skill_map = {"beginner": 5,  "intermediate": 10,  "advanced": 15}

    return {
        "success": True,
        "message": f"🎉 Contribution verified! +{xp_map.get(difficulty, 50)} XP earned",
        "pr": pr_data,
        "xp_earned": xp_map.get(difficulty, 50),
        "skill_gained": {language: skill_map.get(difficulty, 5)},
        "updated_profile": {
            "contributions": updated_user.get("contributions", 0),
            "streak":        updated_user.get("streak", 0),
            "total_xp":      updated_user.get("total_xp", 0),
            "skills":        updated_user.get("skills", {})
        }
    }


@app.get("/api/contributions/history", tags=["Contributions"])
async def contribution_history(current_user: dict = Depends(get_current_user)):
    """Get user's full contribution history"""
    contribution_model = ContributionModel()
    history = contribution_model.get_user_contributions(current_user["sub"])
    return {
        "count": len(history),
        "contributions": [{**c, "_id": str(c["_id"])} for c in history]
    }


# ================================================
# PROGRESS DASHBOARD ROUTE
# ================================================

@app.get("/api/progress", tags=["Progress"])
async def get_progress(current_user: dict = Depends(get_current_user)):
    """
    Aggregated progress data for the dashboard.
    Returns skills, streak, XP, language breakdown, recent contributions.
    """
    user_id = current_user["sub"]

    # Check Redis cache
    cached = CacheManager.get_progress(user_id)
    if cached:
        return {"source": "cache", **cached}

    user_model = UserModel()
    user = user_model.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    completed = user.get("completed_issues", [])
    skills    = user.get("skills", {})

    # Build language breakdown from completed issues
    lang_breakdown: dict = {}
    for issue in completed:
        lang = issue.get("language", "Unknown")
        lang_breakdown[lang] = lang_breakdown.get(lang, 0) + 1

    progress = {
        "username":              user["username"],
        "avatar_url":            user.get("avatar_url"),
        "total_contributions":   user.get("contributions", 0),
        "streak":                user.get("streak", 0),
        "total_xp":              user.get("total_xp", 0),
        "skills":                skills,
        "language_breakdown":    lang_breakdown,
        "recent_contributions":  completed[-10:][::-1],   # newest first
        "last_contribution_date": str(user.get("last_contribution_date", "")),
        "member_since":          str(user.get("created_at", ""))
    }

    # Cache result
    CacheManager.cache_progress(user_id, progress)

    return {"source": "database", **progress}


# ================================================
# HEALTH + ADMIN ROUTES
# ================================================

@app.get("/", tags=["Health"])
async def root():
    """Root endpoint — confirms server is running"""
    return {
        "service": "CodeNova MCP Server",
        "status": "running",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Check status of all services"""
    db_ok = False
    try:
        Database.get_db().command("ping")
        db_ok = True
    except Exception:
        pass

    redis_health = CacheManager.health_check()
    issue_count  = IssueModel().count_active()

    return {
        "status":                "healthy" if db_ok else "degraded",
        "mongodb":               "connected" if db_ok else "disconnected",
        "redis":                 redis_health,
        "llm_provider":          LLM_PROVIDER,
        "llm_model":             LLM_MODEL,
        "active_issues_indexed": issue_count,
        "timestamp":             datetime.utcnow().isoformat()
    }


@app.post("/admin/reindex", tags=["Admin"])
async def manual_reindex(request: Request):
    """Manually trigger GitHub issue indexing (dev / demo use)"""
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.getenv("JWT_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden — wrong X-Admin-Key header")

    from jobs.scheduler import index_github_issues
    index_github_issues()
    return {"message": "Re-indexing triggered successfully"}


# ================================================
# FastMCP TOOLS  (Claude chat / MCP client access)
# ================================================

@mcp.tool()
def mcp_get_recommendations(user_id: str, difficulty: str = "beginner", count: int = 20) -> dict:
    """MCP Tool: Get issue recommendations for a user"""
    user_model  = UserModel()
    issue_model = IssueModel()

    user = user_model.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}

    issues = issue_model.get_active_issues(difficulty=difficulty, limit=300)
    recommendations = get_top_recommendations(
        issues=issues,
        user_skills=user.get("skills", {}),
        user_interests=user.get("interests", []),
        difficulty_filter=difficulty,
        count=count
    )
    return {"count": len(recommendations), "issues": recommendations}


@mcp.tool()
def mcp_get_user_progress(user_id: str) -> dict:
    """MCP Tool: Get user progress summary"""
    user_model = UserModel()
    user = user_model.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}
    return {
        "contributions": user.get("contributions", 0),
        "streak":        user.get("streak", 0),
        "total_xp":      user.get("total_xp", 0),
        "skills":        user.get("skills", {})
    }


@mcp.tool()
def mcp_analyze_profile(github_username: str) -> dict:
    """MCP Tool: Get stored profile for a GitHub username"""
    user_model = UserModel()
    user = user_model.get_by_username(github_username)
    if not user:
        return {"error": f"User '{github_username}' not found in CodeNova"}
    return {
        "username":      user["username"],
        "skills":        user.get("skills", {}),
        "interests":     user.get("interests", []),
        "contributions": user.get("contributions", 0)
    }


# ================================================
# MOUNT FastMCP onto FastAPI  (FastMCP 3.x)
# ================================================
# In FastMCP 3.x, mount the MCP ASGI app under /mcp
# This exposes SSE at  GET  /mcp/sse
# and messages at     POST /mcp/messages
mcp_asgi = mcp.http_app(path="/", transport="sse")
app.mount("/mcp", mcp_asgi)


# ================================================
# ENTRY POINT
# ================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("DEBUG", "True").lower() == "true",
        log_level="info"
    )

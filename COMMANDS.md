# ================================================
# CodeNova MCP Server — Commands Reference
# Read this top to bottom before starting
# ================================================
# All commands are run from:
#   D:\Open Source Contribution\codenova-mcp\
# ================================================


# ================================================================
# STEP 1 — PREREQUISITES  (do these once, ever)
# ================================================================

# 1A. Python 3.11+
#     Download → https://www.python.org/downloads/
#     During install: ✅ check "Add Python to PATH"

# Verify after install:
python --version
# Expected: Python 3.11.x or 3.12.x

# 1B. Docker Desktop
#     Download → https://www.docker.com/products/docker-desktop/
#     Install, then START Docker Desktop before Step 4.

# 1C. Git (optional but recommended)
#     Download → https://git-scm.com/downloads


# ================================================================
# STEP 2 — PROJECT SETUP  (do once)
# ================================================================

# Navigate to the project folder
cd "D:\Open Source Contribution\codenova-mcp"

# Create Python virtual environment
python -m venv venv

# Activate it — Windows CMD:
venv\Scripts\activate

# Activate it — Windows PowerShell:
venv\Scripts\Activate.ps1
# If PowerShell blocks it, run this once first:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Install all dependencies
pip install -r requirements.txt

# Verify key packages installed correctly
pip show fastmcp fastapi groq pymongo redis apscheduler


# ================================================================
# STEP 3 — ENVIRONMENT SETUP  (do once)
# ================================================================

# 3A. Copy the example env file
copy .env.example .env

# 3B. Generate JWT_SECRET — run this, copy the output into .env
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))"

# 3C. Generate ENCRYPTION_KEY — run this, copy the output into .env
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# 3D. Open .env and fill in these values:
#
#   GROQ_API_KEY          → free key from https://console.groq.com
#   GITHUB_CLIENT_ID      → from your GitHub OAuth App (Step 3E)
#   GITHUB_CLIENT_SECRET  → from your GitHub OAuth App (Step 3E)
#   GITHUB_TOKEN          → personal access token (Step 3F)
#   JWT_SECRET            → output of Step 3B
#   ENCRYPTION_KEY        → output of Step 3C
#   MONGODB_URI           → keep default for local Docker
#   REDIS_URL             → keep default for local Docker

# 3E. Create GitHub OAuth App
#     1. Go to https://github.com/settings/developers
#     2. Click "New OAuth App"
#     3. Fill in:
#          Application name : CodeNova MCP
#          Homepage URL     : http://localhost:3000
#          Callback URL     : http://localhost:8000/auth/github/callback
#     4. Click "Register application"
#     5. Copy Client ID   → GITHUB_CLIENT_ID in .env
#     6. Click "Generate a new client secret"
#     7. Copy the secret  → GITHUB_CLIENT_SECRET in .env

# 3F. Create GitHub Personal Access Token
#     1. Go to https://github.com/settings/tokens
#     2. Click "Generate new token (classic)"
#     3. Give it a name: codenova-indexer
#     4. Select scopes: ✅ repo  ✅ read:user  ✅ user:email
#     5. Click "Generate token"
#     6. Copy it → GITHUB_TOKEN in .env

# 3G. (Optional) Switch to Claude instead of Groq
#     Get Anthropic key from https://console.anthropic.com
#     Add to .env: ANTHROPIC_API_KEY=sk-ant-...
#     Then in main.py:
#       - Comment out the 3 lines under "Option A: Groq"
#       - Uncomment the 4 lines under "Option B: Anthropic"


# ================================================================
# STEP 4 — START DATABASES  (every time you work on the project)
# ================================================================

# Make sure Docker Desktop is running first, then:
docker-compose up mongodb redis -d

# Check both containers are healthy
docker ps

# Expected output (two containers running):
# CONTAINER ID   IMAGE           PORTS                      NAMES
# xxxxxxxxxxxx   mongo:7.0       0.0.0.0:27017->27017/tcp   codenova_mongo
# xxxxxxxxxxxx   redis:7-alpine  0.0.0.0:6379->6379/tcp     codenova_redis

# Verify MongoDB is responding
docker exec codenova_mongo mongosh --eval "db.runCommand('ping').ok" --quiet
# Expected: 1

# Verify Redis is responding
docker exec codenova_redis redis-cli ping
# Expected: PONG


# ================================================================
# STEP 5 — RUN THE SERVER  (every time you work on the project)
# ================================================================

# Make sure you are in the project folder with venv active:
cd "D:\Open Source Contribution\codenova-mcp"
venv\Scripts\activate

# Run with auto-reload (development)
python main.py

# OR run directly with uvicorn (same thing, more explicit)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --log-level info

# ─── What you should see on startup ───────────────────────────
# 🚀 Starting CodeNova MCP Server...
#    LLM Provider : groq (llama-3.3-70b-versatile)
# ✅ MongoDB connected
# ✅ MongoDB indexes created
# ✅ Redis connected
# ✅ Background scheduler started
# ⚡ Only 0 issues in DB — running initial indexing...   ← first run only
# ✅ Issue indexing complete
# ✅ CodeNova MCP Server is ready!
# INFO:     Uvicorn running on http://0.0.0.0:8000
# ──────────────────────────────────────────────────────────────

# Server URLs (open these in browser):
#   API docs (Swagger)  → http://localhost:8000/docs
#   Alternative docs    → http://localhost:8000/redoc
#   Health check        → http://localhost:8000/health
#   Start OAuth login   → http://localhost:8000/auth/github/login


# ================================================================
# STEP 6 — VERIFY EVERYTHING IS WORKING
# ================================================================

# 6A. Health check (open in browser or run with curl)
curl http://localhost:8000/health

# Expected response:
# {
#   "status": "healthy",
#   "mongodb": "connected",
#   "redis": { "status": "connected", ... },
#   "llm_provider": "groq",
#   "llm_model": "llama-3.3-70b-versatile",
#   "active_issues_indexed": 1500,
#   "timestamp": "2025-..."
# }

# 6B. Check how many issues are indexed
curl http://localhost:8000/api/issues/stats

# 6C. Trigger manual re-indexing (if issues count is 0)
curl -X POST http://localhost:8000/admin/reindex ^
  -H "X-Admin-Key: YOUR_JWT_SECRET_HERE"
# Replace YOUR_JWT_SECRET_HERE with the value from your .env

# 6D. Test GitHub OAuth (open this in browser — do NOT use curl)
#     http://localhost:8000/auth/github/login
#     → Should redirect you to GitHub's authorization page

# 6E. View all API endpoints
#     http://localhost:8000/docs


# ================================================================
# STEP 7 — USING THE API  (after logging in with GitHub OAuth)
# ================================================================

# After OAuth login you receive a JWT token in the redirect URL:
#   http://localhost:3000/auth/success?token=eyJhbGci...
# Copy this token — use it as YOUR_TOKEN below.

# Get your profile
curl http://localhost:8000/api/profile ^
  -H "Authorization: Bearer YOUR_TOKEN"

# Get recommended issues (beginner level)
curl -X POST http://localhost:8000/api/issues/recommend ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"difficulty\": \"beginner\", \"count\": 10}"

# Get AI explanation for a file
curl -X POST http://localhost:8000/api/explain ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"repo_url\": \"https://github.com/facebook/react\", \"file_path\": \"packages/react/src/React.js\", \"issue_title\": \"Fix validation bug\"}"

# Verify a contribution (after you submit a real PR)
curl -X POST http://localhost:8000/api/contributions/verify ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"issue_id\": \"MONGO_ISSUE_ID\", \"pr_url\": \"https://github.com/owner/repo/pull/123\"}"

# View progress dashboard data
curl http://localhost:8000/api/progress ^
  -H "Authorization: Bearer YOUR_TOKEN"

# Update your skill profile manually
curl -X PUT http://localhost:8000/api/profile/skills ^
  -H "Authorization: Bearer YOUR_TOKEN" ^
  -H "Content-Type: application/json" ^
  -d "{\"skills\": {\"Python\": 70, \"JavaScript\": 50}, \"interests\": [\"web\", \"ai\"]}"


# ================================================================
# STEP 8 — DATABASE INSPECTION  (debugging / development)
# ================================================================

# Open MongoDB shell
docker exec -it codenova_mongo mongosh codenova

# Useful MongoDB queries (run inside mongosh):
#   db.users.find().pretty()
#   db.users.countDocuments()
#   db.issues.countDocuments()
#   db.issues.find({difficulty: "beginner"}).limit(3).pretty()
#   db.issues.find().sort({stars: -1}).limit(5).pretty()
#   db.explanations.find().pretty()
#   db.contributions.find().pretty()
#   exit

# Open Redis CLI
docker exec -it codenova_redis redis-cli

# Useful Redis commands (run inside redis-cli):
#   KEYS codenova:*                    → list all CodeNova cache keys
#   GET codenova:profile:akash_dev     → view a cached profile
#   TTL codenova:issues:beginner:all   → check TTL remaining (seconds)
#   DEL codenova:recs:USER_ID:beginner → manually invalidate one key
#   FLUSHALL                           → clear entire Redis cache (careful!)
#   INFO memory                        → check memory usage
#   exit


# ================================================================
# STEP 9 — BACKGROUND JOBS  (run manually if needed)
# ================================================================

# These run automatically on schedule, but you can trigger them manually:

# Manually index issues NOW (without starting full server)
python -c "
from database.models import Database
from cache.redis_manager import CacheManager
Database.connect()
CacheManager.connect()
from jobs.scheduler import index_github_issues
index_github_issues()
"

# Manually pre-warm explanation cache
python -c "
from database.models import Database
from cache.redis_manager import CacheManager
Database.connect()
CacheManager.connect()
from jobs.scheduler import prewarm_explanation_cache
prewarm_explanation_cache()
"

# Manually run expired issue cleanup
python -c "
from database.models import Database
Database.connect()
from jobs.scheduler import cleanup_expired_issues
cleanup_expired_issues()
"


# ================================================================
# STEP 10 — DOCKER FULL STACK  (optional, production-like)
# ================================================================

# Build and start everything (MongoDB + Redis + MCP Server)
docker-compose up --build

# Run in background (detached mode)
docker-compose up --build -d

# View live server logs
docker-compose logs -f mcp-server

# View all container logs
docker-compose logs -f

# Stop everything (keeps data volumes)
docker-compose down

# Stop everything AND delete all data (full reset)
docker-compose down -v

# Rebuild only the server after code changes
docker-compose up --build mcp-server


# ================================================================
# STEP 11 — SWITCHING LLM PROVIDER
# ================================================================

# DEFAULT: Groq (free) is already active.
# No changes needed — just set GROQ_API_KEY in .env.

# To switch to Anthropic Claude:
# 1. Get API key from https://console.anthropic.com
# 2. Add to .env:  ANTHROPIC_API_KEY=sk-ant-...
# 3. Open main.py and find the "LLM CLIENT SETUP" section:
#    - Comment out the 3 lines under "Option A: Groq"
#    - Uncomment the 4 lines under "Option B: Anthropic"
# 4. Restart the server — that's it.

# The call_llm() function in main.py handles both providers.
# Everything else (caching, routes, tools) stays identical.


# ================================================================
# STEP 12 — TROUBLESHOOTING
# ================================================================

# ── Problem: ModuleNotFoundError ────────────────────────────────
# venv is not activated. Run:
venv\Scripts\activate
pip install -r requirements.txt

# ── Problem: "Connection refused" on port 27017 or 6379 ─────────
# Docker containers are not running. Run:
docker-compose up mongodb redis -d
# Wait 10 seconds then retry.

# ── Problem: GitHub OAuth redirects fail ────────────────────────
# Check these match EXACTLY (no trailing slash, right port):
#   .env GITHUB_CALLBACK_URL = http://localhost:8000/auth/github/callback
#   GitHub App Callback URL  = http://localhost:8000/auth/github/callback

# ── Problem: "Invalid OAuth state" error ────────────────────────
# Redis is not running — OAuth state cannot be stored. Start Redis:
docker-compose up redis -d

# ── Problem: "Invalid or expired token" on API calls ────────────
# JWT expired (default 7 days). Re-login:
#   Open browser → http://localhost:8000/auth/github/login

# ── Problem: issues count is 0 ──────────────────────────────────
# Indexing has not run yet or GITHUB_TOKEN is missing. Fix:
#   1. Check GITHUB_TOKEN is set in .env
#   2. Trigger manual reindex:
curl -X POST http://localhost:8000/admin/reindex ^
  -H "X-Admin-Key: YOUR_JWT_SECRET_HERE"

# ── Problem: Groq API error ──────────────────────────────────────
# Check GROQ_API_KEY is set correctly in .env
# Get a free key from https://console.groq.com

# ── Problem: "Could not decrypt GitHub token" ───────────────────
# ENCRYPTION_KEY changed since last login. Log out and log in again.

# ── Problem: PowerShell won't activate venv ─────────────────────
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
venv\Scripts\Activate.ps1


# ================================================================
# QUICK START CHEATSHEET  (copy-paste every session)
# ================================================================

# Terminal — start databases
cd "D:\Open Source Contribution\codenova-mcp"
docker-compose up mongodb redis -d

# Terminal — start server
venv\Scripts\activate
python main.py

# Browser tabs to open:
#   http://localhost:8000/health          ← confirm all services up
#   http://localhost:8000/docs            ← full API reference
#   http://localhost:8000/auth/github/login  ← test OAuth login

# ================================================================
# API ENDPOINT REFERENCE
# ================================================================
#
# AUTH
#   GET  /auth/github/login           Start GitHub OAuth (open in browser)
#   GET  /auth/github/callback        GitHub redirect target (automatic)
#   POST /auth/logout                 Invalidate session caches
#
# PROFILE
#   GET  /api/profile                 Get your profile (JWT required)
#   PUT  /api/profile/skills          Edit skill profile (JWT required)
#
# ISSUES
#   POST /api/issues/recommend        Get personalised recommendations (JWT required)
#   GET  /api/issues/stats            Number of indexed issues (public)
#
# EXPLANATION
#   POST /api/explain                 AI code explanation (JWT required)
#
# CONTRIBUTIONS
#   POST /api/contributions/verify    Verify PR + update progress (JWT required)
#   GET  /api/contributions/history   Full contribution history (JWT required)
#
# PROGRESS
#   GET  /api/progress                Dashboard data (JWT required)
#
# ADMIN
#   GET  /health                      Service health check (public)
#   POST /admin/reindex               Trigger issue indexing (X-Admin-Key header)
#
# DOCS
#   GET  /docs                        Swagger UI
#   GET  /redoc                       ReDoc UI

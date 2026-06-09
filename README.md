# CodeNova MCP

> AI-powered open-source contribution mentor — backend server

CodeNova matches developers to GitHub issues that fit their skill level, explains unfamiliar codebases in plain English, and tracks their learning progress over time. Built as a **FastMCP + FastAPI** server with **GitHub OAuth**, **MongoDB**, **Redis caching**, and **background job scheduling**.

---

## Features

- **GitHub OAuth login** — secure sign-in, skill profile auto-detected from your repos
- **Personalised issue recommendations** — 5-factor weighted matching algorithm (skill, difficulty, interest, repo quality, recency)
- **AI code explanations** — powered by Groq (free) or Anthropic Claude (optional)
- **Contribution tracking** — paste a PR link, server verifies it on GitHub and updates your XP
- **Progress dashboard** — skill confidence scores, streak, XP, language breakdown
- **Production-grade caching** — Redis with TTL management (30 min profiles, 1 hr issues, 1 week explanations)
- **Background jobs** — APScheduler indexes up to 3 000 GitHub issues every 3 hours automatically

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Server framework | FastMCP + FastAPI |
| Language | Python 3.11+ |
| Database | MongoDB Atlas (via PyMongo) |
| Cache | Redis (redis-py) |
| LLM (default, free) | Groq — llama-3.3-70b-versatile |
| LLM (optional, paid) | Anthropic — claude-sonnet-4-6 |
| Auth | GitHub OAuth 2.0 + JWT + Fernet encryption |
| Background jobs | APScheduler |
| HTTP client | httpx (async) |
| Containerisation | Docker + Docker Compose |

---

## Project Structure

```
codenova-mcp/
├── main.py                  # Entry point — all FastAPI routes + FastMCP tools
├── requirements.txt
├── .env.example             # Copy to .env and fill in your values
├── docker-compose.yml       # MongoDB + Redis + server containers
├── Dockerfile
├── COMMANDS.md              # Every command you need, explained
│
├── database/
│   └── models.py            # MongoDB connection + User / Issue / Explanation / Contribution models
│
├── cache/
│   └── redis_manager.py     # Redis caching with domain-specific TTLs
│
├── tools/
│   ├── github_auth.py       # GitHub OAuth flow, token encryption, skill extraction
│   └── matching.py          # 5-factor weighted recommendation algorithm
│
└── jobs/
    └── scheduler.py         # Background jobs: issue indexing, cache warming, cleanup
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker Desktop (for MongoDB + Redis)

### 1. Clone and set up

```bash
cd "D:\Open Source Contribution\codenova-mcp"

python -m venv venv
venv\Scripts\activate          # Windows CMD
# venv\Scripts\Activate.ps1   # Windows PowerShell

pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
```

Generate secrets:

```bash
# JWT secret
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))"

# Encryption key
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

Open `.env` and fill in:

| Key | Where to get it |
|-----|----------------|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) — free |
| `GITHUB_CLIENT_ID` | [github.com/settings/developers](https://github.com/settings/developers) → New OAuth App |
| `GITHUB_CLIENT_SECRET` | Same OAuth App page |
| `GITHUB_TOKEN` | [github.com/settings/tokens](https://github.com/settings/tokens) → classic token, scopes: `repo read:user user:email` |
| `JWT_SECRET` | Output of command above |
| `ENCRYPTION_KEY` | Output of command above |

GitHub OAuth App settings:
- Homepage URL: `http://localhost:3000`
- Callback URL: `http://localhost:8000/auth/github/callback`

### 3. Start databases

```bash
docker-compose up mongodb redis -d
```

### 4. Run the server

```bash
python main.py
```

Server starts at **http://localhost:8000**

| URL | Purpose |
|-----|---------|
| http://localhost:8000/docs | Swagger UI — all endpoints |
| http://localhost:8000/health | Service health check |
| http://localhost:8000/auth/github/login | Start OAuth login |

---

## LLM Provider

The server ships with **Groq** as the default LLM — it is free and requires no credit card.

To switch to **Anthropic Claude** (better quality, paid):

1. Get an API key from [console.anthropic.com](https://console.anthropic.com)
2. Add `ANTHROPIC_API_KEY=sk-ant-...` to `.env`
3. In `main.py`, find the `LLM CLIENT SETUP` section:
   - Comment out the three lines under **Option A: Groq**
   - Uncomment the four lines under **Option B: Anthropic**
4. Restart the server

Everything else — caching, routes, MCP tools — stays identical.

---

## API Endpoints

### Auth
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/auth/github/login` | Start GitHub OAuth (open in browser) |
| GET | `/auth/github/callback` | OAuth callback — handled automatically |
| POST | `/auth/logout` | Invalidate session |

### Profile
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/profile` | Get your profile |
| PUT | `/api/profile/skills` | Edit skill profile |

### Issues
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/issues/recommend` | Get personalised recommendations |
| GET | `/api/issues/stats` | Count of indexed issues |

### Explanation
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/explain` | AI explanation for a file |

### Contributions
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/contributions/verify` | Verify PR and update XP |
| GET | `/api/contributions/history` | Full contribution history |

### Progress
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/progress` | Dashboard data |

### Admin
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/health` | Health check (public) |
| POST | `/admin/reindex` | Trigger issue indexing — requires `X-Admin-Key` header |

> All `/api/*` routes require `Authorization: Bearer <JWT>` header.

---

## Recommendation Algorithm

Issues are scored using a 5-factor weighted system:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Skill match | 40% | How well your languages match the issue |
| Difficulty match | 25% | Beginner / intermediate / advanced vs your level |
| Interest match | 20% | Issue labels and topics vs your interests |
| Repo quality | 10% | Stars, open issues, description quality |
| Recency | 5% | How recently the issue was updated |

---

## Background Jobs

| Job | Schedule | What it does |
|-----|----------|-------------|
| Issue indexing | Every 3 hours | Fetches up to 3 000 good-first-issues from GitHub Search API |
| Cache warming | Every night 2 AM | Restores popular file explanations from MongoDB into Redis |
| Cleanup | Every midnight | Deletes expired issues from MongoDB |

On first startup, if fewer than 100 issues exist, indexing runs immediately.

---

## Caching Strategy

| Data | Redis TTL | MongoDB |
|------|-----------|---------|
| User profile | 30 minutes | Permanent |
| Issue lists | 1 hour | 30-day TTL index |
| Recommendations | 1 hour | Not stored |
| Code explanations | 1 week | Permanent |
| Progress dashboard | 30 minutes | Permanent |
| OAuth state | 10 minutes | Not stored |

Cache is automatically invalidated when a user verifies a contribution or updates their skills.

---

## Docker (Full Stack)

```bash
# Start everything
docker-compose up --build -d

# View logs
docker-compose logs -f mcp-server

# Stop everything
docker-compose down

# Full reset (deletes all data)
docker-compose down -v
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | ✅ | — | Groq API key (free LLM) |
| `ANTHROPIC_API_KEY` | Optional | — | Claude API key (if switching to Option B) |
| `GITHUB_CLIENT_ID` | ✅ | — | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | ✅ | — | GitHub OAuth App secret |
| `GITHUB_CALLBACK_URL` | ✅ | `http://localhost:8000/auth/github/callback` | Must match GitHub App settings |
| `GITHUB_TOKEN` | ✅ | — | Personal access token for background indexing |
| `MONGODB_URI` | ✅ | `mongodb://root:password@localhost:27017/codenova?authSource=admin` | MongoDB connection string |
| `REDIS_URL` | ✅ | `redis://localhost:6379` | Redis connection string |
| `JWT_SECRET` | ✅ | — | Random hex string for JWT signing |
| `ENCRYPTION_KEY` | ✅ | — | Fernet key for GitHub token encryption |
| `FRONTEND_URL` | ✅ | `http://localhost:3000` | Frontend origin for CORS + OAuth redirect |
| `HOST` | — | `0.0.0.0` | Server bind address |
| `PORT` | — | `8000` | Server port |
| `DEBUG` | — | `True` | Enables uvicorn auto-reload |

---

## MCP Tools (Claude Integration)

The server exposes three FastMCP tools that Claude can call directly:

| Tool | Description |
|------|-------------|
| `mcp_get_recommendations` | Get issue recommendations for a user ID |
| `mcp_get_user_progress` | Get contributions, streak, XP, skills |
| `mcp_analyze_profile` | Look up a GitHub username's stored profile |

---

## Author

Akash Debnath — B.Tech CSE (Data Science), Heritage Institute of Technology, Kolkata
GitHub: [Akash007AD](https://github.com/Akash007AD)

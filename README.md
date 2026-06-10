# CodeNova MCP Server

AI-powered open-source contribution mentor. Works as:
- **Local** — stdio transport, connected directly to Claude Desktop
- **Remote** — SSE over HTTP, deployed to Render (always-on, any device)

---

## Security model

| Layer | What it does |
|-------|-------------|
| `GITHUB_USERNAME` in `.env` | Server locked to one account. Tools never accept arbitrary usernames. |
| `MCP_SECRET` in `.env` | Every tool call must pass this as `secret`. Wrong = `ACCESS_DENIED`. |
| MongoDB scoped to `github_id` | All DB queries filter by the owner's numeric GitHub ID. No cross-user reads. |

---

## Files

```
server.py   — MCP tools + auth + DB logic. Entry point for Claude Desktop (stdio).
main.py     — FastAPI wrapper that mounts server.py over SSE. Entry point for Render.
```

Tools are defined **once** in `server.py`. `main.py` just imports the `mcp` instance and exposes it over HTTP. No duplication.

---

## Option A — Local (Claude Desktop, stdio)

### 1. Install

```bash
cd codenova-mcp
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
GITHUB_USERNAME=Akash007AD
MCP_SECRET=<generate below>
GITHUB_TOKEN=ghp_...
GROQ_API_KEY=gsk_...         # optional
MONGODB_URI=...               # optional
REDIS_URL=...                 # optional
```

Generate secret:
```bash
python -c "import secrets; print(secrets.token_hex(24))"
```

### 3. Claude Desktop config

`%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codenova": {
      "command": "D:\\Open Source Contribution\\codenova-mcp\\venv\\Scripts\\python.exe",
      "args": ["D:\\Open Source Contribution\\codenova-mcp\\server.py"]
    }
  }
}
```

Restart Claude Desktop.

---

## Option B — Remote (Render, SSE over HTTP)

### 1. Push to GitHub

```bash
git add .
git commit -m "feat: pure MCP server, SSE + stdio"
git push origin main
```

Render auto-deploys on push (Auto-Deploy is ON in your dashboard).

### 2. Set environment variables in Render Dashboard

`codenova-mcp → Environment → Add Environment Variable`:

| Key | Value |
|-----|-------|
| `GITHUB_USERNAME` | `Akash007AD` |
| `GITHUB_TOKEN` | `ghp_...` |
| `MCP_SECRET` | generate with `secrets.token_hex(24)` |
| `GROQ_API_KEY` | `gsk_...` (optional) |
| `MONGODB_URI` | `mongodb+srv://...` (optional) |
| `REDIS_URL` | `redis://...` (optional) |

### 3. Fix start command in Render Dashboard

`Settings → Deploy → Start Command`:
```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

### 4. Verify deployment

```
https://codenova-mcp.onrender.com/health
```

Should return:
```json
{
  "status": "ok",
  "owner": "Akash007AD",
  "github_token": "set",
  "mcp_endpoint": "/sse"
}
```

### 5. Claude Desktop config for remote

```json
{
  "mcpServers": {
    "codenova": {
      "url": "https://codenova-mcp.onrender.com/sse"
    }
  }
}
```

---

## Tools

All tools require `secret` matching your `MCP_SECRET`.

| Tool | What it does |
|------|-------------|
| `get_my_profile` | Your GitHub profile + skills from public repos |
| `recommend_issues` | Full pipeline → your skills → matched open issues |
| `get_issue_details` | Full issue, comments, AI task summary |
| `explain_code_file` | Fetch file + AI explanation of what to change |
| `get_repo_details` | Repo stats, languages, CONTRIBUTING.md |
| `find_issues` | Search issues by language/difficulty |
| `search_repos` | Find repos to contribute to |
| `check_rate_limit` | Debug GitHub API rate limit |

---

## Typical Claude session

```
You: Help me find open source issues to contribute to.

Claude: [calls get_my_profile → recommend_issues]
        Here are 10 issues matched to your Python/C skills...

You: Tell me more about this one → <url>

Claude: [calls get_issue_details]
        Task summary: ...

You: Explain the file I need to edit.

Claude: [calls explain_code_file]
        What it does: ...  Key concepts: ...  Where to look: ...
```

---

## GitHub Token scopes

Minimum: `read:user`, `public_repo`

Create at: https://github.com/settings/tokens → Generate new token (classic)

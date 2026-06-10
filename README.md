# CodeNova MCP

AI-powered open-source contribution mentor for Claude Desktop.

CodeNova connects Claude to GitHub — it reads your public repos, infers your skills, and finds open issues matched to your level. No setup beyond adding your GitHub token.

**Deployment modes:**
- **Remote (recommended)** — connect to the hosted server on Render, no install needed
- **Local** — run the server on your own machine via stdio

---

## Quickstart — Remote Server (No Install)

This is the easiest way. The server is already running at `https://codenova-mcp.onrender.com`.

### 1. Get a GitHub token

Go to https://github.com/settings/tokens → **Generate new token (classic)**

Required scopes: `read:user`, `public_repo`

### 2. Add to Claude Desktop config

Open `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) and add:

```json
{
  "mcpServers": {
    "codenova": {
      "type": "sse",
      "url": "https://codenova-mcp.onrender.com/sse",
      "env": {
        "GITHUB_TOKEN": "ghp_yourTokenHere"
      }
    }
  }
}
```

### 3. Restart Claude Desktop

Fully quit (system tray → Quit) and reopen. You're done.

### 4. Try it

Just ask Claude:
> *"Find me open source issues to contribute to"*

Claude will automatically call `get_my_profile` → `recommend_issues` and return matched issues — no credentials to type.

> **Note on cold starts:** The free Render tier sleeps after 15 min of inactivity. The first tool call after a sleep takes ~30 seconds. Subsequent calls are instant.

---

## Security Model

| Layer | What it does |
|-------|-------------|
| `github_token` per call | Your GitHub token identifies you. The server calls `GET /user` to get your `github_id`. This is the identity — no separate username input. |
| Token never stored | Only your `github_id`, username, and inferred skills are saved to MongoDB. Your token stays in your Claude config only. |
| MongoDB scoped to `github_id` | All DB reads/writes use your numeric GitHub ID as the key. User A can never read or write User B's data. |
| `SERVER_SECRET` optional | If set by the server operator, it gates access. Remote users connecting via the Render URL don't need to pass it — your GitHub token is the real authentication. |

---

## Tools

| Tool | What it does |
|------|-------------|
| `get_my_profile` | Fetch your GitHub profile and infer skills from your public repos. **Call this first.** |
| `recommend_issues` | Full pipeline: loads your skills → finds open GitHub issues matched to your level. |
| `get_issue_details` | Full issue body, comments, and an AI-generated task summary (what exactly to implement). |
| `explain_code_file` | Fetch a source file from GitHub and explain what it does, key concepts, and where to make changes. |
| `get_repo_details` | Repo stats, language breakdown, CONTRIBUTING guide, README preview, clone command. |
| `find_issues` | Search issues by language and difficulty — use when you want to specify languages manually. |
| `search_repos` | Find repos to contribute to by keyword and language. |
| `check_rate_limit` | Check remaining GitHub API quota for your token. |

All tools automatically read `GITHUB_TOKEN` from your Claude Desktop env — you never type credentials.

---

## Typical Session

```
You: Help me find open source issues to contribute to.

Claude: [calls get_my_profile → recommend_issues]
        Here are 10 issues matched to your Python and JavaScript skills...

You: Tell me more about the second one.

Claude: [calls get_issue_details]
        Here's what you need to implement: ...

You: Explain the file I need to edit.

Claude: [calls explain_code_file]
        What it does: ...  Key concepts: ...  Where to look: ...
```

---

## Self-Hosting (Run Your Own Server)

If you want to run your own instance instead of using the hosted one.

### Local (Claude Desktop stdio)

```bash
git clone https://github.com/Akash007AD/codenova-mcp
cd codenova-mcp
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:

```env
SERVER_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(24))">
GROQ_API_KEY=gsk_...        # optional — enables AI explanations
MONGODB_URI=...             # optional — enables profile caching
REDIS_URL=...               # optional — enables request caching
```

Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "codenova": {
      "command": "C:\\path\\to\\codenova-mcp\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\codenova-mcp\\mcp_stdio.py"],
      "env": {
        "GITHUB_TOKEN": "ghp_yourTokenHere"
      }
    }
  }
}
```

### Deploy to Render

1. Fork this repo and push to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Set start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables in Render Dashboard:

| Key | Value |
|-----|-------|
| `SERVER_SECRET` | `python -c "import secrets; print(secrets.token_hex(24))"` |
| `GROQ_API_KEY` | `gsk_...` (optional) |
| `MONGODB_URI` | `mongodb+srv://...` (optional) |
| `REDIS_URL` | `redis://...` (optional) |

5. Verify: `https://your-app.onrender.com/health`

Users connect exactly like the Quickstart above, just replace the URL with your own.

---

## Project Structure

```
mcp_stdio.py   — Claude Desktop entry point (stdio transport)
main.py        — Render/remote entry point (SSE over HTTP via FastAPI)
server.py      — All MCP tools, auth, GitHub API logic, DB helpers
database/      — MongoDB models and connection
cache/         — Redis cache manager
jobs/          — Background jobs (issue indexing)
```

Tools are defined once in `server.py`. Both `mcp_stdio.py` and `main.py` just import the `mcp` instance — no duplication.

---

## Contributing

PRs welcome. If you find a bug or want a new tool, open an issue.

To run locally for development:
```bash
python mcp_stdio.py   # test stdio mode
uvicorn main:app --reload --port 8000   # test SSE mode
```

Health check: `http://localhost:8000/health`

# CodeNova MCP

AI-powered open-source contribution mentor. Connects Claude Desktop to GitHub — reads your public repos, infers your skills, and finds matching open issues to contribute to.

---

## How it works

You install this server locally. Claude Desktop runs it as a subprocess (stdio). It reads your GitHub token from a local `.env` file. No hosted server, no accounts, no API keys beyond GitHub and optionally Groq.

```
Claude Desktop  ──stdio──►  mcp_stdio.py  ──►  server.py  ──►  GitHub API
```

---

## Install

**Requirements:** Python 3.11+, Git

```bash
git clone https://github.com/Akash007AD/codenova-mcp
cd codenova-mcp
python -m venv venv

# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate

pip install -r requirements.txt
```

---

## Configure

**Step 1 — Create your `.env` file:**

```bash
# Windows
copy .env.example .env

# Mac / Linux
cp .env.example .env
```

**Step 2 — Get a GitHub token:**

Go to https://github.com/settings/tokens → **Generate new token (classic)**

Required scopes: `read:user`, `public_repo`

**Step 3 — Add it to `.env`:**

```env
GITHUB_TOKEN=ghp_yourTokenHere
```

That's the only required setting. Groq, MongoDB, and Redis are optional (see `.env.example` for details).

---

## Connect to Claude Desktop

Open your Claude Desktop config file:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the `codenova` entry (replace the path with wherever you cloned the repo):

```json
{
  "mcpServers": {
    "codenova": {
      "command": "C:\\path\\to\\codenova-mcp\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\codenova-mcp\\mcp_stdio.py"]
    }
  }
}
```

> **Mac / Linux** — use the unix path instead:
> ```json
> {
>   "mcpServers": {
>     "codenova": {
>       "command": "/path/to/codenova-mcp/venv/bin/python",
>       "args": ["/path/to/codenova-mcp/mcp_stdio.py"]
>     }
>   }
> }
> ```

**Restart Claude Desktop** — fully quit (system tray → Quit) and reopen.

You should see a hammer icon (🔨) in the Claude Desktop input bar. Click it to confirm CodeNova tools are listed.

---

## Use it

Just ask Claude naturally:

> *"Find me open source issues to contribute to"*
> 
> *"I know Python and JavaScript — what should I work on?"*
> 
> *"Tell me more about that issue"*
> 
> *"Explain the file I need to edit"*

Claude automatically chains the tools in the right order.

---

## Tools

| Tool | What it does |
|------|-------------|
| `get_my_profile` | Fetches your GitHub profile and infers your skills from public repos. Called automatically. |
| `recommend_issues` | Finds open GitHub issues matched to your skill level and languages. |
| `get_issue_details` | Full issue body, comments, and an AI task summary (what exactly to do). |
| `explain_code_file` | Fetches a source file and explains what it does, key concepts, where to make changes. |
| `get_repo_details` | Repo stats, languages, CONTRIBUTING guide, README preview, clone command. |
| `find_issues` | Search issues manually by language and difficulty. |
| `search_repos` | Find repos to contribute to by keyword and language. |
| `check_rate_limit` | Check your remaining GitHub API quota. |

---

## Typical session

```
You:    Help me find open source issues to contribute to.

Claude: [calls get_my_profile → recommend_issues]
        Here are 10 issues matched to your Python and JavaScript skills...

You:    Tell me more about the second one.

Claude: [calls get_issue_details]
        Here's what you need to implement...

You:    Explain the file I need to edit.

Claude: [calls explain_code_file]
        What it does: ...  Key concepts: ...  Where to look: ...
```

---

## Optional features

### AI explanations (Groq)

Without `GROQ_API_KEY`, `explain_code_file` returns the raw source. With it, you get a plain-English breakdown. Free at https://console.groq.com.

Add to `.env`:
```env
GROQ_API_KEY=gsk_yourKeyHere
```

### Profile caching (MongoDB)

Without `MONGODB_URI`, your skill profile is re-inferred from GitHub on every `get_my_profile` call (takes a few seconds). With it, the profile is cached and reused.

```env
MONGODB_URI=mongodb://localhost:27017/codenova
```

### Request caching (Redis)

Without `REDIS_URL`, every tool call hits the GitHub API directly. With Redis, repeated calls for the same data return instantly.

```env
REDIS_URL=redis://localhost:6379
```

---

## Development / testing

To test tools without Claude Desktop, run the HTTP server and open it in [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
uvicorn main:app --reload --port 8000
```

Then open: `http://localhost:8000/sse` in MCP Inspector.

Health check: `http://localhost:8000/health`

---

## Project structure

```
mcp_stdio.py      — Claude Desktop entry point (stdio)
main.py           — HTTP/SSE entry point (MCP Inspector / local dev)
server.py         — All 8 MCP tools, GitHub API calls, DB helpers
.env.example      — Config template
database/         — MongoDB models (optional)
cache/            — Redis cache manager (optional)
jobs/             — Background issue indexer (optional, needs MongoDB)
tools/            — Skill extraction + matching algorithm
```

---

## Troubleshooting

**Claude Desktop doesn't show the hammer icon**
- Check that the path in `claude_desktop_config.json` is correct and uses double backslashes on Windows
- Open Claude Desktop logs: `%APPDATA%\Claude\logs\` and look for errors
- Run `python mcp_stdio.py` directly in a terminal — if it errors, fix that first

**`GITHUB_TOKEN not set` error**
- Make sure `.env` exists in the repo root (not `.env.example`)
- The token line should be `GITHUB_TOKEN=ghp_...` with no spaces around `=`

**Rate limit errors**
- Ask Claude: *"Check my rate limit"* — it calls `check_rate_limit()` and tells you when it resets
- GitHub allows 5000 requests/hour with an authenticated token

**`ModuleNotFoundError`**
- Make sure you activated the venv before running: `venv\Scripts\activate`
- Then: `pip install -r requirements.txt`

---

## Contributing

PRs welcome. If you find a bug or want a new tool, open an issue.

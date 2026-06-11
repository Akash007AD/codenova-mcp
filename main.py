# ================================================
# CodeNova MCP — HTTP Entry Point (local dev / MCP Inspector)
#
# Use this to test tools via MCP Inspector or any SSE client.
# For Claude Desktop, use mcp_stdio.py instead.
#
# Run:
#   uvicorn main:app --reload --port 8000
#
# Health check: http://localhost:8000/health
# MCP endpoint: http://localhost:8000/sse
# ================================================

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Import the shared MCP instance (all tools already registered in server.py)
from server import mcp, GROQ_API_KEY, _db

# ── Scheduler: index issues on startup + refresh every 3 hours ──────────────
from jobs.scheduler import create_scheduler, run_initial_indexing

_scheduler = create_scheduler()
_scheduler.start()
run_initial_indexing()   # fills db.issues immediately if count < 100
# ────────────────────────────────────────────────────────────────────────────

# =====================================================
# FastAPI app
# =====================================================

app = FastAPI(
    title="CodeNova MCP (local)",
    description="AI-powered open-source contribution mentor — local dev server",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

# =====================================================
# Health check
# =====================================================

@app.get("/health")
async def health():
    db_ok = False
    if _db is not None:
        try:
            _db.command("ping")
            db_ok = True
        except Exception:
            pass

    return JSONResponse({
        "status":       "ok",
        "service":      "codenova-mcp",
        "mode":         "local single-user",
        "groq":         "set" if GROQ_API_KEY else "not set (AI explanations disabled)",
        "mongodb":      "connected" if db_ok else "unavailable (optional)",
        "mcp_endpoint": "/sse",
        "timestamp":    datetime.utcnow().isoformat() + "Z",
    })


@app.get("/")
async def root():
    return JSONResponse({
        "service":      "CodeNova MCP Server (local)",
        "mcp_endpoint": "/sse",
        "health":       "/health",
        "transport":    "SSE",
        "docs":         "https://github.com/Akash007AD/codenova-mcp",
    })


# =====================================================
# Mount MCP over SSE
# =====================================================

mcp_asgi = mcp.http_app(path="/", transport="sse")
app.mount("/", mcp_asgi)


# =====================================================
# Local dev runner
# =====================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
        log_level="info",
    )
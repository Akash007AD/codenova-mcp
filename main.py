# ================================================
# CodeNova MCP — Remote Entry Point (Render / any host)
#
# Imports the same `mcp` instance from server.py
# (all tools already registered there) and mounts
# it over SSE transport via FastAPI + uvicorn.
#
# Render start command:
#   uvicorn main:app --host 0.0.0.0 --port $PORT
#
# MCP endpoint:  GET  https://codenova-mcp.onrender.com/sse
# Health check:  GET  https://codenova-mcp.onrender.com/health
# ================================================

import os
import sys

# UTF-8 safety
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

# ── Import the shared MCP instance (tools already registered) ────────
# server.py handles all tool definitions, auth, DB/Redis init.
# We just need the mcp object to mount it over SSE here.
from server import mcp, SERVER_SECRET, GROQ_API_KEY, _db

# =====================================================
# FastAPI app
# =====================================================

app = FastAPI(
    title="CodeNova MCP",
    description="AI-powered open-source contribution mentor — MCP over SSE",
    version="2.0.0",
    docs_url=None,   # no swagger UI on prod
    redoc_url=None,
)

# =====================================================
# Health check  (Render uses this to confirm liveness)
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
        "status":           "ok",
        "service":          "codenova-mcp",
        "mode":             "multi-user (github_token per call)",
        "server_secret":    "set" if SERVER_SECRET else "NOT SET (open access — set SERVER_SECRET in env)",
        "groq":             "set" if GROQ_API_KEY else "not set (AI explanations disabled)",
        "mongodb":          "connected" if db_ok else "unavailable",
        "mcp_endpoint":     "/sse",
        "timestamp":        datetime.utcnow().isoformat() + "Z",
    })

@app.get("/")
async def root():
    return JSONResponse({
        "service":      "CodeNova MCP Server",
        "mcp_endpoint": "/sse",
        "health":       "/health",
        "transport":    "SSE",
        "docs":         "https://github.com/Akash007AD/codenova-mcp",
    })

# =====================================================
# Mount MCP over SSE
# FastMCP 2.x: http_app(path, transport)
# This exposes:
#   GET  /sse          ← SSE stream (Claude connects here)
#   POST /messages     ← tool call messages
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
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=False,
        log_level="info",
    )

# ================================================
# CodeNova MCP - Claude Desktop Entry Point
# Runs the MCP server in STDIO mode for Claude Desktop.
#
# server.py already handles DB + Redis init on import,
# so we just load env, import mcp, and run stdio.
# stdout must be pure JSON-RPC — NO print() here.
# ================================================

import sys
import os

# Force UTF-8 on Windows so nothing bleeds onto stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# server.py connects DB + Redis itself on import (via try/except, stderr only)
from server import mcp

mcp.run(transport="stdio")

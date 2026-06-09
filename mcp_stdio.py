# ================================================
# CodeNova MCP - Claude Desktop Entry Point
# This file runs the MCP server in STDIO mode
# which is what Claude Desktop requires.
#
# Claude Desktop uses STDIO transport (not SSE/HTTP).
# This wrapper boots the DB + cache connections,
# then hands off to FastMCP's stdio runner.
# ================================================

import sys
import os

# Force UTF-8 output so emoji in print() don't crash on Windows cp1252
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

# Load .env from the project directory
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Boot database and cache
from database.models import Database
from cache.redis_manager import CacheManager

Database.connect()
CacheManager.connect()

# Import the FastMCP instance from main
# (tools are already registered on it via @mcp.tool())
from main import mcp

# Run in STDIO mode — this is what Claude Desktop talks to
mcp.run(transport="stdio")

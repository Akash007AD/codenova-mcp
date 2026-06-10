# ================================================
# CodeNova MCP - Redis Cache Layer
# Production-grade caching with TTL management
# ================================================

import redis
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------
# TTL Constants (in seconds)
# ------------------------------------------------

TTL_PROFILE       = 30 * 60          # 30 minutes
TTL_ISSUES        = 60 * 60          # 1 hour
TTL_RECOMMENDATIONS = 60 * 60        # 1 hour
TTL_EXPLANATION   = 7 * 24 * 60 * 60 # 1 week
TTL_AUTH_STATE    = 10 * 60          # 10 minutes (OAuth state param)
TTL_PR_VERIFY     = 5 * 60          # 5 minutes

# ------------------------------------------------
# Cache Key Builders
# ------------------------------------------------

def key_profile(username: str) -> str:
    return f"codenova:profile:{username}"

def key_issues(difficulty: str, langs: list = None) -> str:
    lang_str = "_".join(sorted(langs)) if langs else "all"
    return f"codenova:issues:{difficulty}:{lang_str}"

def key_recommendations(user_id: str, difficulty: str) -> str:
    return f"codenova:recs:{user_id}:{difficulty}"

def key_explanation(file_path: str) -> str:
    safe_path = file_path.replace("/", "_").replace("\\", "_")
    return f"codenova:explain:{safe_path}"

def key_oauth_state(state: str) -> str:
    return f"codenova:oauth:{state}"

def key_user_progress(user_id: str) -> str:
    return f"codenova:progress:{user_id}"


# ------------------------------------------------
# Redis Cache Manager
# ------------------------------------------------

class CacheManager:
    _client: redis.Redis = None

    @classmethod
    def connect(cls):
        """Initialize Redis connection"""
        if cls._client is None:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            password = os.getenv("REDIS_PASSWORD", None)

            cls._client = redis.Redis.from_url(
                redis_url,
                password=password if password else None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )

            try:
                cls._client.ping()
                import sys; sys.stderr.write("[codenova] Redis connected\n")
            except redis.ConnectionError:
                import sys; sys.stderr.write("[codenova] Redis not available — caching disabled\n")
                cls._client = None

        return cls._client

    @classmethod
    def get_client(cls) -> redis.Redis:
        if cls._client is None:
            return cls.connect()
        return cls._client

    # ------------------------------------------------
    # Core Cache Operations
    # ------------------------------------------------

    @classmethod
    def get(cls, key: str):
        """Get value from cache, returns None if miss or error"""
        client = cls.get_client()
        if not client:
            return None
        try:
            value = client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            import sys; sys.stderr.write(f"[codenova] Cache GET error [{key}]: {e}\n")
            return None

    @classmethod
    def set(cls, key: str, value, ttl: int):
        """Set value in cache with TTL"""
        client = cls.get_client()
        if not client:
            return False
        try:
            serialized = json.dumps(value, default=str)
            client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            import sys; sys.stderr.write(f"[codenova] Cache SET error [{key}]: {e}\n")
            return False

    @classmethod
    def delete(cls, key: str):
        """Delete a specific key"""
        client = cls.get_client()
        if not client:
            return
        try:
            client.delete(key)
        except Exception as e:
            import sys; sys.stderr.write(f"[codenova] Cache DELETE error [{key}]: {e}\n")

    @classmethod
    def delete_pattern(cls, pattern: str):
        """Delete all keys matching a pattern"""
        client = cls.get_client()
        if not client:
            return
        try:
            keys = client.keys(pattern)
            if keys:
                client.delete(*keys)
                import sys; sys.stderr.write(f"[codenova] Cleared {len(keys)} cache keys matching [{pattern}]\n")
        except Exception as e:
            import sys; sys.stderr.write(f"[codenova] Cache pattern delete error [{pattern}]: {e}\n")

    @classmethod
    def exists(cls, key: str) -> bool:
        client = cls.get_client()
        if not client:
            return False
        try:
            return client.exists(key) > 0
        except Exception:
            return False

    # ------------------------------------------------
    # Domain-Specific Cache Methods
    # ------------------------------------------------

    @classmethod
    def cache_profile(cls, username: str, profile_data: dict):
        cls.set(key_profile(username), profile_data, TTL_PROFILE)

    @classmethod
    def get_profile(cls, username: str):
        return cls.get(key_profile(username))

    @classmethod
    def invalidate_profile(cls, username: str):
        cls.delete(key_profile(username))

    @classmethod
    def cache_issues(cls, difficulty: str, issues: list, langs: list = None):
        cls.set(key_issues(difficulty, langs), issues, TTL_ISSUES)

    @classmethod
    def get_issues(cls, difficulty: str, langs: list = None):
        return cls.get(key_issues(difficulty, langs))

    @classmethod
    def invalidate_issues(cls):
        """Called after background indexing job completes"""
        cls.delete_pattern("codenova:issues:*")
        cls.delete_pattern("codenova:recs:*")

    @classmethod
    def cache_recommendations(cls, user_id: str, difficulty: str, recs: list):
        cls.set(key_recommendations(user_id, difficulty), recs, TTL_RECOMMENDATIONS)

    @classmethod
    def get_recommendations(cls, user_id: str, difficulty: str):
        return cls.get(key_recommendations(user_id, difficulty))

    @classmethod
    def invalidate_recommendations(cls, user_id: str):
        cls.delete_pattern(f"codenova:recs:{user_id}:*")

    @classmethod
    def cache_explanation(cls, file_path: str, explanation: dict):
        cls.set(key_explanation(file_path), explanation, TTL_EXPLANATION)

    @classmethod
    def get_explanation(cls, file_path: str):
        return cls.get(key_explanation(file_path))

    @classmethod
    def store_oauth_state(cls, state: str, redirect_uri: str = "/dashboard"):
        """Store OAuth state param to prevent CSRF"""
        cls.set(key_oauth_state(state), {"state": state, "redirect": redirect_uri}, TTL_AUTH_STATE)

    @classmethod
    def validate_oauth_state(cls, state: str) -> bool:
        """Validate and consume OAuth state"""
        data = cls.get(key_oauth_state(state))
        if data:
            cls.delete(key_oauth_state(state))
            return True
        return False

    @classmethod
    def cache_progress(cls, user_id: str, progress: dict):
        cls.set(key_user_progress(user_id), progress, TTL_PROFILE)

    @classmethod
    def get_progress(cls, user_id: str):
        return cls.get(key_user_progress(user_id))

    @classmethod
    def invalidate_progress(cls, user_id: str):
        cls.delete(key_user_progress(user_id))

    @classmethod
    def health_check(cls) -> dict:
        """Return Redis health status"""
        client = cls.get_client()
        if not client:
            return {"status": "disconnected"}
        try:
            client.ping()
            info = client.info()
            return {
                "status": "connected",
                "used_memory": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                "keyspace": info.get("db0", {})
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

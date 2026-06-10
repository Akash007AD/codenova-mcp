# ================================================
# CodeNova - Full User Flush Script
# Deletes user doc + contributions from MongoDB
# and wipes all related Redis cache keys.
#
# Usage (from project root):
#   python flush_user.py
# ================================================

import os
from dotenv import load_dotenv
from pymongo import MongoClient
import redis

load_dotenv()

USERNAME = "Akash007AD"

MONGODB_URI    = os.getenv("MONGODB_URI")
REDIS_URL      = os.getenv("REDIS_URL")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

print(f"🔍 Targeting user: {USERNAME}\n")

# ================================================
# 1. MongoDB — delete user + contributions
# ================================================
print("── MongoDB ──────────────────────────────────")
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    db     = client["codenova"]

    user = db.users.find_one({"username": USERNAME})
    if user:
        uid = str(user["_id"])

        # Delete user document
        db.users.delete_one({"_id": user["_id"]})
        print(f"✅ Deleted user document  (_id: {uid})")

        # Delete all contributions for this user
        c = db.contributions.delete_many({"user_id": uid})
        print(f"✅ Deleted {c.deleted_count} contribution record(s)")
    else:
        print(f"ℹ️  User '{USERNAME}' not found in MongoDB (already clean)")

    client.close()

except Exception as e:
    print(f"❌ MongoDB error: {e}")

# ================================================
# 2. Redis — flush all codenova keys for this user
# ================================================
print("\n── Redis ────────────────────────────────────")
try:
    r = redis.Redis.from_url(
        REDIS_URL,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        decode_responses=True,
        socket_connect_timeout=5
    )
    r.ping()

    # All key patterns that could belong to this user
    patterns = [
        f"codenova:profile:{USERNAME}",
        "codenova:recs:*",
        "codenova:progress:*",
        "codenova:oauth:*",
    ]

    total = 0
    for pattern in patterns:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
            total += len(keys)
            print(f"🗑️  Flushed {len(keys):>2} key(s)  [{pattern}]")

    if total == 0:
        print("ℹ️  No Redis keys found (already clean)")
    else:
        print(f"\n✅ Total Redis keys flushed: {total}")

    r.close()

except Exception as e:
    print(f"❌ Redis error: {e}")

# ================================================
print("\n✅ Done — account fully wiped.")
print("   Now visit: https://codenova-mcp.onrender.com/auth/github/login")
print("   (or http://localhost:8000/auth/github/login for local dev)")

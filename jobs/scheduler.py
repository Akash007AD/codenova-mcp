# ================================================
# CodeNova MCP - Background Jobs
# APScheduler: Issue Indexing + Cache Warming
# ================================================

import os
import httpx
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Lazily imported to avoid circular imports
def get_models():
    from database.models import IssueModel, ExplanationModel
    return IssueModel(), ExplanationModel()

def get_cache():
    from cache.redis_manager import CacheManager
    return CacheManager


# ------------------------------------------------
# Job 1: Index GitHub Issues (every 3 hours)
# ------------------------------------------------

def index_github_issues():
    """
    Fetch up to 3000 good-first-issues from GitHub
    and store them in MongoDB. Clears related caches.
    """
    print(f"[{datetime.utcnow()}] 🔄 Starting GitHub issue indexing...")

    issue_model, _ = get_models()
    CacheManager = get_cache()

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    # Search queries for different languages
    search_queries = [
        "label:good-first-issue language:JavaScript stars:>50 is:open is:issue",
        "label:good-first-issue language:Python stars:>50 is:open is:issue",
        "label:good-first-issue language:TypeScript stars:>50 is:open is:issue",
        "label:good-first-issue language:Java stars:>50 is:open is:issue",
        "label:good-first-issue language:Go stars:>50 is:open is:issue",
        "label:good-first-issue language:Rust stars:>50 is:open is:issue",
        "label:good-first-issue language:C++ stars:>50 is:open is:issue",
        "label:good-first-issue label:help-wanted stars:>100 is:open is:issue",
    ]

    total_indexed = 0

    for query in search_queries:
        try:
            _index_query(query, headers, issue_model)
            total_indexed += 1
        except Exception as e:
            print(f"⚠️  Query failed [{query[:50]}...]: {e}")

    # Invalidate Redis caches for issue lists
    CacheManager.invalidate_issues()
    print(f"✅ Issue indexing complete — {total_indexed} queries processed")


def _index_query(query: str, headers: dict, issue_model):
    """Fetch and store issues for a single search query"""
    from tools.matching import estimate_difficulty

    with httpx.Client(timeout=30) as client:
        for page in range(1, 4):  # 3 pages × 100 = 300 per query
            response = client.get(
                "https://api.github.com/search/issues",
                headers=headers,
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": 100,
                    "page": page
                }
            )

            if response.status_code == 422:
                break  # Invalid query
            if response.status_code == 403:
                print("⚠️  GitHub rate limit hit — stopping indexing")
                break

            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])

            if not items:
                break

            issues_to_upsert = []
            for item in items:
                repo_data = item.get("repository", {}) or {}

                labels = [l["name"] for l in item.get("labels", [])]
                languages = _extract_languages_from_issue(item)

                issue_doc = {
                    "github_id": item["id"],
                    "title": item["title"],
                    "description": item.get("body", "")[:2000],  # Cap at 2000 chars
                    "issue_url": item["html_url"],
                    "repo": item["repository_url"].split("/repos/")[-1],
                    "repo_url": item["repository_url"].replace(
                        "https://api.github.com/repos/",
                        "https://github.com/"
                    ),
                    "difficulty": estimate_difficulty(labels),
                    "labels": labels,
                    "languages": languages,
                    "stars": repo_data.get("stargazers_count", 0),
                    "open_issues_count": repo_data.get("open_issues_count", 0),
                    "comments": item.get("comments", 0),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "indexed_at": datetime.utcnow(),
                    "expires_at": datetime.utcnow() + timedelta(days=30)
                }

                issues_to_upsert.append(issue_doc)

            if issues_to_upsert:
                issue_model.bulk_upsert(issues_to_upsert)

            if len(items) < 100:
                break  # Last page


def _extract_languages_from_issue(item: dict) -> list:
    """Extract language from issue labels and title hints"""
    languages = set()
    LANGUAGE_KEYWORDS = {
        "javascript", "typescript", "python", "java", "go", "golang",
        "rust", "c++", "c#", "ruby", "php", "swift", "kotlin", "dart",
        "scala", "elixir", "haskell", "clojure", "r", "matlab"
    }

    title_lower = (item.get("title") or "").lower()
    for lang in LANGUAGE_KEYWORDS:
        if lang in title_lower:
            languages.add(lang.capitalize())

    for label in item.get("labels", []):
        label_name = label.get("name", "").lower()
        for lang in LANGUAGE_KEYWORDS:
            if lang in label_name:
                languages.add(lang.capitalize())

    return list(languages)


# ------------------------------------------------
# Job 2: Pre-warm explanation cache (nightly 2 AM)
# ------------------------------------------------

def prewarm_explanation_cache():
    """
    Restore popular file explanations from MongoDB into Redis.
    Runs nightly so frequently-requested files are cache-ready.
    """
    print(f"[{datetime.utcnow()}] 📚 Pre-warming explanation cache...")

    _, explanation_model = get_models()
    CacheManager = get_cache()

    popular = explanation_model.get_popular(limit=50)

    restored = 0
    for exp in popular:
        file_path = exp.get("file_path")
        if not file_path:
            continue

        cached = CacheManager.get_explanation(file_path)
        if not cached:
            CacheManager.cache_explanation(file_path, {
                "file_path": file_path,
                "explanation": exp.get("explanation"),
                "key_concepts": exp.get("key_concepts"),
                "modification_tips": exp.get("modification_tips"),
                "cached_at": datetime.utcnow().isoformat()
            })
            restored += 1

    print(f"✅ Pre-warmed {restored} explanations into Redis")


# ------------------------------------------------
# Job 3: Cleanup expired issues (daily midnight)
# ------------------------------------------------

def cleanup_expired_issues():
    """Delete issues past their expiry date from MongoDB"""
    print(f"[{datetime.utcnow()}] 🧹 Cleaning expired issues...")

    issue_model, _ = get_models()
    deleted = issue_model.delete_expired()

    print(f"✅ Deleted {deleted} expired issues")


# ------------------------------------------------
# Scheduler Setup
# ------------------------------------------------

def create_scheduler() -> BackgroundScheduler:
    """
    Create and configure APScheduler with all background jobs.
    Returns the scheduler (not started yet).
    """
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,           # Merge missed runs
            "max_instances": 1,         # No overlapping runs
            "misfire_grace_time": 300   # 5 min grace if missed
        }
    )

    # Job 1: Index GitHub issues every 3 hours
    scheduler.add_job(
        index_github_issues,
        trigger=IntervalTrigger(hours=3),
        id="index_issues",
        name="Index GitHub Issues",
        replace_existing=True
    )

    # Job 2: Pre-warm explanation cache every night at 2 AM
    scheduler.add_job(
        prewarm_explanation_cache,
        trigger=CronTrigger(hour=2, minute=0),
        id="prewarm_cache",
        name="Pre-warm Explanation Cache",
        replace_existing=True
    )

    # Job 3: Cleanup expired issues daily at midnight
    scheduler.add_job(
        cleanup_expired_issues,
        trigger=CronTrigger(hour=0, minute=0),
        id="cleanup_issues",
        name="Cleanup Expired Issues",
        replace_existing=True
    )

    return scheduler


def run_initial_indexing():
    """
    Run issue indexing immediately on server startup if DB is empty.
    """
    issue_model, _ = get_models()
    count = issue_model.count_active()

    if count < 100:
        print(f"⚡ Only {count} issues in DB — running initial indexing...")
        index_github_issues()
    else:
        print(f"✅ {count} issues already indexed — skipping initial run")

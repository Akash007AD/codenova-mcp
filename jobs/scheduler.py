# ================================================
# CodeNova MCP - Background Jobs
# APScheduler: Issue Indexing + Cache Warming
# ================================================

import os
import sys
import httpx
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()


# Lazily imported to avoid circular imports at server startup
def _get_models():
    from database.models import IssueModel, ExplanationModel
    return IssueModel(), ExplanationModel()


def _get_cache():
    from cache.redis_manager import CacheManager
    return CacheManager


# ------------------------------------------------
# Job 1: Index GitHub Issues (every 3 hours)
# ------------------------------------------------

def index_github_issues():
    """
    Fetch good-first-issues from GitHub per language and store in MongoDB.
    Skips silently if GITHUB_TOKEN or MongoDB are not configured.
    """
    if not GITHUB_TOKEN:
        sys.stderr.write("[codenova] Skipping issue indexing — GITHUB_TOKEN not set\n")
        return

    try:
        issue_model, _ = _get_models()
    except Exception as e:
        sys.stderr.write(f"[codenova] Skipping issue indexing — MongoDB unavailable: {e}\n")
        return

    CacheManager = _get_cache()

    sys.stderr.write(f"[codenova] [{datetime.utcnow().isoformat()}] Refreshing GitHub issue index...\n")

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    search_queries = [
        ('label:good-first-issue language:JavaScript stars:>50 is:open is:issue', "JavaScript"),
        ('label:good-first-issue language:Python stars:>50 is:open is:issue',     "Python"),
        ('label:good-first-issue language:TypeScript stars:>50 is:open is:issue', "TypeScript"),
        ('label:good-first-issue language:Java stars:>50 is:open is:issue',       "Java"),
        ('label:good-first-issue language:Go stars:>50 is:open is:issue',         "Go"),
        ('label:good-first-issue language:Rust stars:>50 is:open is:issue',       "Rust"),
        ('label:good-first-issue language:C++ stars:>50 is:open is:issue',        "C++"),
        ('label:good-first-issue language:HTML stars:>50 is:open is:issue',       "HTML"),
    ]

    total_indexed = 0
    for query, language in search_queries:
        try:
            _index_query(query, headers, issue_model, language)
            total_indexed += 1
        except Exception as e:
            sys.stderr.write(f"[codenova] Query failed [{query[:50]}...]: {e}\n")

    # Invalidate Redis caches so the next request recomputes recommendations
    try:
        CacheManager.invalidate_issues()
    except Exception:
        pass

    sys.stderr.write(f"[codenova] Issue indexing complete — {total_indexed} queries processed\n")


def _index_query(query: str, headers: dict, issue_model, primary_language: str = ""):
    """Fetch and upsert issues for a single search query."""
    from tools.matching import estimate_difficulty

    with httpx.Client(timeout=30) as client:
        for page in range(1, 4):   # 3 pages × 100 = up to 300 per language
            response = client.get(
                "https://api.github.com/search/issues",
                headers=headers,
                params={
                    "q":        query,
                    "sort":     "updated",
                    "order":    "desc",
                    "per_page": 100,
                    "page":     page,
                },
            )

            if response.status_code == 422:
                break
            if response.status_code == 403:
                sys.stderr.write("[codenova] GitHub rate limit hit — stopping indexing\n")
                break

            response.raise_for_status()
            items = response.json().get("items", [])
            if not items:
                break

            issues_to_upsert = []
            for item in items:
                labels = [lb["name"] for lb in item.get("labels", [])]
                languages = _extract_languages_from_issue(item, primary_language)

                issues_to_upsert.append({
                    "github_id":        item["id"],
                    "title":            item["title"],
                    "description":      (item.get("body") or "")[:2000],
                    "issue_url":        item["html_url"],
                    "repo":             item["repository_url"].split("/repos/")[-1],
                    "repo_url":         item["repository_url"].replace(
                                            "https://api.github.com/repos/",
                                            "https://github.com/"),
                    "difficulty":       estimate_difficulty(labels),
                    "labels":           labels,
                    "languages":        languages,
                    "stars":            0,
                    "open_issues_count": 0,
                    "comments":         item.get("comments", 0),
                    "created_at":       item.get("created_at"),
                    "updated_at":       item.get("updated_at"),
                    "indexed_at":       datetime.utcnow(),
                    "expires_at":       datetime.utcnow() + timedelta(days=30),
                })

            if issues_to_upsert:
                issue_model.bulk_upsert(issues_to_upsert)

            if len(items) < 100:
                break


def _extract_languages_from_issue(item: dict, primary_language: str = "") -> list:
    """Build the language list for an issue."""
    import re
    languages = set()

    if primary_language:
        languages.add(primary_language)

    LANGUAGE_KEYWORDS = {
        "javascript": "JavaScript", "typescript": "TypeScript",
        "python": "Python",         "java": "Java",
        "go": "Go",                 "golang": "Go",
        "rust": "Rust",             "c++": "C++",
        "c#": "C#",                 "ruby": "Ruby",
        "php": "PHP",               "swift": "Swift",
        "kotlin": "Kotlin",         "dart": "Dart",
        "html": "HTML",             "css": "CSS",
    }

    title_lower = (item.get("title") or "").lower()
    for kw, canonical in LANGUAGE_KEYWORDS.items():
        if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
            languages.add(canonical)

    for label in item.get("labels", []):
        label_name = label.get("name", "").lower()
        for kw, canonical in LANGUAGE_KEYWORDS.items():
            if re.search(r'\b' + re.escape(kw) + r'\b', label_name):
                languages.add(canonical)

    return list(languages)


# ------------------------------------------------
# Job 2: Pre-warm explanation cache (nightly 2 AM)
# ------------------------------------------------

def prewarm_explanation_cache():
    """Restore popular file explanations from MongoDB into Redis."""
    try:
        _, explanation_model = _get_models()
        CacheManager = _get_cache()
    except Exception as e:
        sys.stderr.write(f"[codenova] Skipping cache pre-warm — DB unavailable: {e}\n")
        return

    sys.stderr.write(f"[codenova] [{datetime.utcnow().isoformat()}] Pre-warming explanation cache...\n")

    popular  = explanation_model.get_popular(limit=50)
    restored = 0

    for exp in popular:
        file_path = exp.get("file_path")
        if not file_path:
            continue
        if not CacheManager.get_explanation(file_path):
            CacheManager.cache_explanation(file_path, {
                "file_path":        file_path,
                "explanation":      exp.get("explanation"),
                "key_concepts":     exp.get("key_concepts"),
                "modification_tips": exp.get("modification_tips"),
                "cached_at":        datetime.utcnow().isoformat(),
            })
            restored += 1

    sys.stderr.write(f"[codenova] Pre-warmed {restored} explanations into Redis\n")


# ------------------------------------------------
# Job 3: Cleanup expired issues (daily midnight)
# ------------------------------------------------

def cleanup_expired_issues():
    """Delete issues past their expiry date from MongoDB."""
    try:
        issue_model, _ = _get_models()
    except Exception as e:
        sys.stderr.write(f"[codenova] Skipping cleanup — DB unavailable: {e}\n")
        return

    deleted = issue_model.delete_expired()
    sys.stderr.write(f"[codenova] Deleted {deleted} expired issues\n")


# ------------------------------------------------
# Scheduler Setup
# ------------------------------------------------

def create_scheduler() -> BackgroundScheduler:
    """Create and configure APScheduler. Returns the scheduler (not started)."""
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce":          True,
            "max_instances":     1,
            "misfire_grace_time": 300,
        }
    )

    scheduler.add_job(
        index_github_issues,
        trigger=IntervalTrigger(hours=3),
        id="index_issues",
        name="Index GitHub Issues",
        replace_existing=True,
    )

    scheduler.add_job(
        prewarm_explanation_cache,
        trigger=CronTrigger(hour=2, minute=0),
        id="prewarm_cache",
        name="Pre-warm Explanation Cache",
        replace_existing=True,
    )

    scheduler.add_job(
        cleanup_expired_issues,
        trigger=CronTrigger(hour=0, minute=0),
        id="cleanup_issues",
        name="Cleanup Expired Issues",
        replace_existing=True,
    )

    return scheduler


def run_initial_indexing():
    """Run issue indexing on startup if the DB is near-empty."""
    try:
        issue_model, _ = _get_models()
        count = issue_model.count_active()
    except Exception:
        return

    if count < 100:
        sys.stderr.write(f"[codenova] Only {count} issues in DB — running initial indexing...\n")
        index_github_issues()
    else:
        sys.stderr.write(f"[codenova] {count} issues already indexed — skipping initial run\n")

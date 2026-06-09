# ================================================
# CodeNova MCP - Issue Matching Algorithm
# 5-factor weighted scoring engine
# ================================================

from datetime import datetime
from typing import Optional


# ------------------------------------------------
# Weights (must sum to 1.0)
# ------------------------------------------------

WEIGHTS = {
    "skill_match":    0.40,
    "difficulty":     0.25,
    "interest":       0.20,
    "repo_quality":   0.10,
    "recency":        0.05
}

# ------------------------------------------------
# Difficulty Estimation
# ------------------------------------------------

BEGINNER_LABELS = {
    "good first issue", "good-first-issue", "beginner", "beginner-friendly",
    "easy", "starter", "first-timers-only", "help wanted"
}

INTERMEDIATE_LABELS = {
    "intermediate", "medium", "enhancement", "feature", "improvement"
}

ADVANCED_LABELS = {
    "advanced", "hard", "complex", "performance", "security", "architecture"
}

def estimate_difficulty(labels: list) -> str:
    """Estimate issue difficulty from GitHub labels"""
    label_names = {l.lower() for l in labels}

    if label_names & BEGINNER_LABELS:
        return "beginner"
    elif label_names & ADVANCED_LABELS:
        return "advanced"
    elif label_names & INTERMEDIATE_LABELS:
        return "intermediate"
    else:
        return "beginner"  # Default to beginner for unknown


# ------------------------------------------------
# Scoring Functions
# ------------------------------------------------

def score_skill_match(issue_languages: list, user_skills: dict) -> float:
    """
    How well does user's skill match the issue languages?
    Returns 0.0 to 1.0
    """
    if not issue_languages:
        return 0.5  # Neutral if no language info

    matched_langs = [lang for lang in issue_languages if lang in user_skills]

    if not matched_langs:
        return 0.0

    # Weighted by user's confidence in matched languages
    total_confidence = sum(
        user_skills.get(lang, 0) for lang in matched_langs
    )

    # Normalize by number of issue languages * max confidence (100)
    max_possible = len(issue_languages) * 100
    return min(total_confidence / max_possible, 1.0)


def score_difficulty_match(issue_difficulty: str, user_skills: dict) -> float:
    """
    Match issue difficulty to user's experience level.
    Returns 0.0 to 1.0
    """
    # Calculate user's average skill confidence
    if not user_skills:
        avg_skill = 0
    else:
        avg_skill = sum(user_skills.values()) / len(user_skills)

    # Map user experience to recommended difficulty
    if avg_skill < 30:
        ideal = "beginner"
    elif avg_skill < 60:
        ideal = "intermediate"
    else:
        ideal = "advanced"

    difficulty_score_map = {
        ("beginner", "beginner"):         1.0,
        ("beginner", "intermediate"):     0.5,
        ("beginner", "advanced"):         0.1,
        ("intermediate", "beginner"):     0.7,
        ("intermediate", "intermediate"): 1.0,
        ("intermediate", "advanced"):     0.5,
        ("advanced", "beginner"):         0.4,
        ("advanced", "intermediate"):     0.7,
        ("advanced", "advanced"):         1.0,
    }

    return difficulty_score_map.get((ideal, issue_difficulty), 0.5)


def score_interest_match(issue_labels: list, issue_languages: list, user_interests: list) -> float:
    """
    Does the issue topic align with user's interests?
    Returns 0.0 to 1.0
    """
    if not user_interests:
        return 0.3

    user_interest_set = {i.lower() for i in user_interests}
    issue_signals = {l.lower() for l in (issue_labels + issue_languages)}

    overlap = user_interest_set & issue_signals
    if not overlap:
        return 0.1

    return min(len(overlap) / max(len(user_interest_set), 1), 1.0)


def score_repo_quality(stars: int, open_issues: int, has_good_description: bool) -> float:
    """
    Repo quality based on stars, engagement, and issue clarity.
    Returns 0.0 to 1.0
    """
    star_score = min(stars / 5000, 1.0)  # Cap at 5000 stars = 1.0
    issue_engagement = min(open_issues / 100, 1.0) * 0.3  # More open issues = more active
    description_bonus = 0.2 if has_good_description else 0.0

    return (star_score * 0.5) + issue_engagement + description_bonus


def score_recency(updated_at: datetime) -> float:
    """
    Prefer recently updated issues.
    Returns 0.0 to 1.0
    """
    if not updated_at:
        return 0.3

    days_old = (datetime.utcnow() - updated_at).days

    if days_old <= 7:
        return 1.0
    elif days_old <= 30:
        return 0.8
    elif days_old <= 90:
        return 0.5
    elif days_old <= 180:
        return 0.2
    else:
        return 0.1


# ------------------------------------------------
# Main Scoring Function
# ------------------------------------------------

def calculate_match_score(issue: dict, user_skills: dict, user_interests: list) -> float:
    """
    Calculate weighted match score for an issue.
    Returns 0-100 score.
    """
    # Extract issue fields
    issue_languages = issue.get("languages", [])
    issue_labels    = issue.get("labels", [])
    issue_difficulty = issue.get("difficulty", "beginner")
    stars           = issue.get("stars", 0)
    open_issues     = issue.get("open_issues_count", 0)
    description     = issue.get("description", "")
    updated_at_raw  = issue.get("updated_at")

    # Parse updated_at if it's a string
    if isinstance(updated_at_raw, str):
        try:
            updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            updated_at = None
    else:
        updated_at = updated_at_raw

    has_good_desc = len(description or "") > 100

    # Calculate individual scores
    s_skill      = score_skill_match(issue_languages, user_skills)
    s_difficulty = score_difficulty_match(issue_difficulty, user_skills)
    s_interest   = score_interest_match(issue_labels, issue_languages, user_interests)
    s_quality    = score_repo_quality(stars, open_issues, has_good_desc)
    s_recency    = score_recency(updated_at)

    # Weighted sum
    total = (
        s_skill      * WEIGHTS["skill_match"]  +
        s_difficulty * WEIGHTS["difficulty"]   +
        s_interest   * WEIGHTS["interest"]     +
        s_quality    * WEIGHTS["repo_quality"] +
        s_recency    * WEIGHTS["recency"]
    )

    return round(total * 100, 2)


# ------------------------------------------------
# Recommendation Engine
# ------------------------------------------------

def get_top_recommendations(
    issues: list,
    user_skills: dict,
    user_interests: list,
    difficulty_filter: Optional[str] = None,
    count: int = 20
) -> list:
    """
    Score all issues, apply filters, return top N.
    """
    results = []

    for issue in issues:
        # Apply difficulty filter if specified
        if difficulty_filter and issue.get("difficulty") != difficulty_filter:
            continue

        score = calculate_match_score(issue, user_skills, user_interests)

        results.append({
            **{k: str(v) if hasattr(v, '__class__') and v.__class__.__name__ == 'ObjectId' else v
               for k, v in issue.items()},
            "match_score": score,
            "score_breakdown": {
                "skill_match": round(score_skill_match(
                    issue.get("languages", []), user_skills) * 40, 1),
                "difficulty": round(score_difficulty_match(
                    issue.get("difficulty", "beginner"), user_skills) * 25, 1),
                "interest": round(score_interest_match(
                    issue.get("labels", []),
                    issue.get("languages", []),
                    user_interests) * 20, 1),
                "repo_quality": round(score_repo_quality(
                    issue.get("stars", 0),
                    issue.get("open_issues_count", 0),
                    len(issue.get("description", "") or "") > 100) * 10, 1),
                "recency": round(5, 1)
            }
        })

    # Sort by score descending
    results.sort(key=lambda x: x["match_score"], reverse=True)

    return results[:count]

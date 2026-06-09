# ================================================
# CodeNova MCP - Database Layer
# MongoDB Atlas connection + all collection models
# ================================================

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from bson import ObjectId
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------
# MongoDB Connection
# ------------------------------------------------

class Database:
    _client: MongoClient = None
    _db = None

    @classmethod
    def connect(cls):
        """Initialize MongoDB connection"""
        if cls._client is None:
            uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/codenova")
            cls._client = MongoClient(uri)
            cls._db = cls._client["codenova"]
            cls._setup_indexes()
            print("✅ MongoDB connected")
        return cls._db

    @classmethod
    def get_db(cls):
        if cls._db is None:
            return cls.connect()
        return cls._db

    @classmethod
    def _setup_indexes(cls):
        """Create all necessary indexes for performance"""
        db = cls._db

        # Users collection
        db.users.create_index("github_id", unique=True)
        db.users.create_index("username")
        db.users.create_index("email")

        # Issues collection
        db.issues.create_index("github_id", unique=True)
        db.issues.create_index("difficulty")
        db.issues.create_index("languages")
        db.issues.create_index("stars")
        db.issues.create_index("updated_at")
        # TTL index: auto-delete expired issues
        db.issues.create_index(
            "expires_at",
            expireAfterSeconds=0
        )

        # Explanations collection
        db.explanations.create_index("file_path", unique=True)
        db.explanations.create_index("used_count")

        # Contributions collection
        db.contributions.create_index("user_id")
        db.contributions.create_index("issue_id")
        db.contributions.create_index([("user_id", 1), ("issue_id", 1)], unique=True)

        print("✅ MongoDB indexes created")

    @classmethod
    def close(cls):
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None


# ------------------------------------------------
# User Model
# ------------------------------------------------

class UserModel:
    def __init__(self):
        self.db = Database.get_db()
        self.collection: Collection = self.db.users

    def create_or_update(self, github_data: dict, encrypted_token: str) -> dict:
        """Create user or update on login"""
        user_doc = {
            "github_id": github_data["id"],
            "username": github_data["login"],
            "email": github_data.get("email", ""),
            "avatar_url": github_data.get("avatar_url", ""),
            "github_token_encrypted": encrypted_token,
            "public_repos": github_data.get("public_repos", 0),
            "updated_at": datetime.utcnow(),
        }

        result = self.collection.find_one_and_update(
            {"github_id": github_data["id"]},
            {
                "$set": user_doc,
                "$setOnInsert": {
                    "skills": {},
                    "interests": [],
                    "contributions": 0,
                    "streak": 0,
                    "total_xp": 0,
                    "completed_issues": [],
                    "last_contribution_date": None,
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True,
            return_document=True
        )
        return result

    def update_skills(self, user_id: str, skills: dict, interests: list) -> dict:
        """Update user skill profile"""
        return self.collection.find_one_and_update(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "skills": skills,
                    "interests": interests,
                    "skills_updated_at": datetime.utcnow()
                }
            },
            return_document=True
        )

    def get_by_id(self, user_id: str) -> dict:
        return self.collection.find_one({"_id": ObjectId(user_id)})

    def get_by_github_id(self, github_id: int) -> dict:
        return self.collection.find_one({"github_id": github_id})

    def get_by_username(self, username: str) -> dict:
        return self.collection.find_one({"username": username})

    def update_contribution(self, user_id: str, language: str, difficulty: str, issue_id: str, pr_url: str):
        """Update user progress after verified contribution"""
        xp_map = {"beginner": 50, "intermediate": 100, "advanced": 200}
        skill_map = {"beginner": 5, "intermediate": 10, "advanced": 15}

        xp_gain = xp_map.get(difficulty, 50)
        skill_gain = skill_map.get(difficulty, 5)

        return self.collection.find_one_and_update(
            {"_id": ObjectId(user_id)},
            {
                "$inc": {
                    "contributions": 1,
                    "streak": 1,
                    "total_xp": xp_gain,
                    f"skills.{language}": skill_gain
                },
                "$push": {
                    "completed_issues": {
                        "issue_id": issue_id,
                        "pr_url": pr_url,
                        "completed_at": datetime.utcnow(),
                        "difficulty": difficulty,
                        "language": language,
                        "xp_earned": xp_gain
                    }
                },
                "$set": {
                    "last_contribution_date": datetime.utcnow()
                }
            },
            return_document=True
        )


# ------------------------------------------------
# Issue Model
# ------------------------------------------------

class IssueModel:
    def __init__(self):
        self.db = Database.get_db()
        self.collection: Collection = self.db.issues

    def upsert_issue(self, issue_data: dict):
        """Insert or update a GitHub issue"""
        return self.collection.update_one(
            {"github_id": issue_data["github_id"]},
            {"$set": issue_data},
            upsert=True
        )

    def bulk_upsert(self, issues: list):
        """Bulk insert/update issues"""
        from pymongo import UpdateOne
        operations = [
            UpdateOne(
                {"github_id": issue["github_id"]},
                {"$set": issue},
                upsert=True
            )
            for issue in issues
        ]
        if operations:
            return self.collection.bulk_write(operations)

    def get_active_issues(self, difficulty: str = None, languages: list = None, limit: int = 200) -> list:
        """Fetch active (non-expired) issues"""
        query = {"expires_at": {"$gt": datetime.utcnow()}}
        if difficulty:
            query["difficulty"] = difficulty
        if languages:
            query["languages"] = {"$in": languages}

        return list(
            self.collection.find(query)
            .sort("stars", DESCENDING)
            .limit(limit)
        )

    def count_active(self) -> int:
        return self.collection.count_documents({"expires_at": {"$gt": datetime.utcnow()}})

    def delete_expired(self) -> int:
        result = self.collection.delete_many({"expires_at": {"$lt": datetime.utcnow()}})
        return result.deleted_count


# ------------------------------------------------
# Explanation Model
# ------------------------------------------------

class ExplanationModel:
    def __init__(self):
        self.db = Database.get_db()
        self.collection: Collection = self.db.explanations

    def get(self, file_path: str) -> dict:
        return self.collection.find_one({"file_path": file_path})

    def save(self, file_path: str, explanation: str, key_concepts: str, modification_tips: str) -> dict:
        doc = {
            "file_path": file_path,
            "explanation": explanation,
            "key_concepts": key_concepts,
            "modification_tips": modification_tips,
            "created_at": datetime.utcnow(),
            "used_count": 0
        }
        self.collection.update_one(
            {"file_path": file_path},
            {"$set": doc},
            upsert=True
        )
        return doc

    def increment_used(self, file_path: str):
        self.collection.update_one(
            {"file_path": file_path},
            {"$inc": {"used_count": 1}}
        )

    def get_popular(self, limit: int = 50) -> list:
        return list(
            self.collection.find()
            .sort("used_count", DESCENDING)
            .limit(limit)
        )


# ------------------------------------------------
# Contribution Model
# ------------------------------------------------

class ContributionModel:
    def __init__(self):
        self.db = Database.get_db()
        self.collection: Collection = self.db.contributions

    def create(self, user_id: str, issue_id: str, pr_url: str, language: str, difficulty: str) -> dict:
        doc = {
            "user_id": user_id,
            "issue_id": issue_id,
            "pr_url": pr_url,
            "language": language,
            "difficulty": difficulty,
            "verified_at": datetime.utcnow()
        }
        self.collection.insert_one(doc)
        return doc

    def already_exists(self, user_id: str, issue_id: str) -> bool:
        return self.collection.find_one({
            "user_id": user_id,
            "issue_id": issue_id
        }) is not None

    def get_user_contributions(self, user_id: str) -> list:
        return list(self.collection.find({"user_id": user_id}).sort("verified_at", DESCENDING))

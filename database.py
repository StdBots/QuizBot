import logging
from pymongo import MongoClient
from bson import ObjectId
from config import MONGO_URI, DB_NAME

logger = logging.getLogger(__name__)

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]
quizzes_col = db["quizzes"]
scores_col = db["scores"]

quizzes_col.create_index("owner_id")


# ── Quiz CRUD ──────────────────────────────────────────────

def create_quiz(owner_id: int, title: str) -> str:
    res = quizzes_col.insert_one({
        "owner_id": owner_id,
        "title": title,
        "description": "",
        "questions": [],   # [{question, options:[str], correct_index:int, explanation:str}]
        "timer": None,
        "shuffle": None,
    })
    return str(res.inserted_id)


def get_quiz(quiz_id: str) -> dict | None:
    try:
        return quizzes_col.find_one({"_id": ObjectId(quiz_id)})
    except Exception:
        return None


def get_user_quizzes(user_id: int) -> list:
    return list(quizzes_col.find({"owner_id": user_id}))


def update_quiz(quiz_id: str, fields: dict):
    quizzes_col.update_one({"_id": ObjectId(quiz_id)}, {"$set": fields})


def push_question(quiz_id: str, q: dict):
    quizzes_col.update_one({"_id": ObjectId(quiz_id)}, {"$push": {"questions": q}})


def pop_last_question(quiz_id: str) -> bool:
    """Remove last question — for /undo."""
    quiz = get_quiz(quiz_id)
    if not quiz or not quiz.get("questions"):
        return False
    questions = quiz["questions"][:-1]
    quizzes_col.update_one({"_id": ObjectId(quiz_id)}, {"$set": {"questions": questions}})
    return True


def delete_quiz(quiz_id: str):
    quizzes_col.delete_one({"_id": ObjectId(quiz_id)})
    scores_col.delete_many({"quiz_id": quiz_id})


def delete_question_by_index(quiz_id: str, idx: int) -> bool:
    quiz = get_quiz(quiz_id)
    if not quiz:
        return False
    qs = quiz.get("questions", [])
    if 0 <= idx < len(qs):
        qs.pop(idx)
        quizzes_col.update_one({"_id": ObjectId(quiz_id)}, {"$set": {"questions": qs}})
        return True
    return False


# ── Scores ─────────────────────────────────────────────────

def save_player_score(quiz_id: str, chat_id: int, user_id: int, username: str, correct: int, total: int, total_time: float):
    scores_col.update_one(
        {"quiz_id": quiz_id, "chat_id": chat_id, "user_id": user_id},
        {"$set": {"username": username, "correct": correct, "total": total, "total_time": round(total_time, 1)}},
        upsert=True
    )


def get_leaderboard(quiz_id: str, chat_id: int) -> list:
    # Sort by correct DESC, then by speed ASC
    return list(
        scores_col.find({"quiz_id": quiz_id, "chat_id": chat_id})
        .sort([("correct", -1), ("total_time", 1)])
        .limit(10)
    )

import os

def get_env(name: str, required=True, default=None):
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"❌ Environment variable '{name}' missing!")
    return value

# 🔑 Required
TELEGRAM_TOKEN = get_env("BOT_TOKEN")
MONGO_URI = get_env("MONGO_URL")

# 📦 Optional
DB_NAME = os.getenv("DB_NAME", "quizbot")

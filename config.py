"""
config.py
Centralized configuration. All tunables come from environment variables so the
same codebase can run in dev (SQLite) or production (Postgres + Redis) without
code changes.
"""

import os
from dotenv import load_dotenv

load_dotenv()

def _bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

class BaseConfig:
    # --- Core ---
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        # Fail loudly in production; generate an ephemeral key in dev so the
        # app still boots locally without a .env file.
        if _bool("PRODUCTION", False):
            raise RuntimeError(
                "SECRET_KEY environment variable is required in production."
            )
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

    ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

    # --- Database ---
    # Postgres (recommended for 5k concurrent users) via DATABASE_URL, e.g.
    #   postgresql+psycopg2://user:pass@host:5432/aeroguide
    # Falls back to a local SQLite file with WAL mode for development.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///aeroguide.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        SQLALCHEMY_ENGINE_OPTIONS.update(
            {
                "pool_size": int(os.environ.get("DB_POOL_SIZE", 20)),
                "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 40)),
                "pool_recycle": 1800,
                "pool_timeout": 30,
            }
        )

    # --- Cache / Rate limiting backend ---
    # Redis is strongly recommended once you run more than one worker process
    # or more than one machine, since in-memory state is per-process only.
    REDIS_URL = os.environ.get("REDIS_URL", "")
    RATELIMIT_STORAGE_URI = REDIS_URL if REDIS_URL else "memory://"

    # --- Rate limits ---
    RATE_LIMIT_ASK = os.environ.get("RATE_LIMIT_ASK", "20 per minute")
    RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60 per minute")

    # --- AI providers ---
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    GROK_API_KEY = os.environ.get("GROK_API_KEY", "").strip()
    WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "").strip()
    FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "").strip()

    AI_REQUEST_TIMEOUT = float(os.environ.get("AI_REQUEST_TIMEOUT", 20))
    AI_MAX_RETRIES = int(os.environ.get("AI_MAX_RETRIES", 2))

    # duckduckgo_search has its own strict rate limits and is a single
    # external dependency shared by every request; under heavy concurrent
    # load it can become a bottleneck or start failing. Flip this off to
    # skip live web search entirely and rely purely on the model + knowledge
    # base (recommended if you don't have a dedicated search API key).
    ENABLE_LIVE_SEARCH = _bool("ENABLE_LIVE_SEARCH", True)

    # --- Adaptive learning ---
    # Similarity threshold (0-1) above which a previously-answered question is
    # considered "the same" and served from the knowledge base instead of
    # calling an external model again.
    KB_SIMILARITY_THRESHOLD = float(os.environ.get("KB_SIMILARITY_THRESHOLD", 0.86))
    KB_MAX_CANDIDATES = int(os.environ.get("KB_MAX_CANDIDATES", 500))

    # --- Misc ---
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 64 * 1024))  # 64KB
    JSON_SORT_KEYS = False


class DevConfig(BaseConfig):
    DEBUG = True


class ProdConfig(BaseConfig):
    DEBUG = False


def get_config():
    env = os.environ.get("FLASK_ENV", "development").lower()
    return ProdConfig if env == "production" else DevConfig
"""
extensions.py
Single place where shared Flask extensions live, so every module imports the
same instances instead of creating circular imports.
"""

import logging
import time
import threading
from collections import OrderedDict

from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()

import os

# ... baaki code waisa hi rahega ...

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("REDIS_URL", "memory://")
)

logger = logging.getLogger("aeroguide")


class TTLCache:
    """
    A minimal thread-safe in-process TTL cache used as a fallback when Redis
    isn't configured. Good enough for a single-process dev server; for real
    5k-concurrent-user deployments behind multiple gunicorn workers, plug in
    Redis instead (see RedisCache below) so cache state is shared.
    """

    def __init__(self, max_size: int = 2000):
        self._data = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            value, expires_at = item
            if expires_at is not None and time.time() > expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key, value, ttl_seconds: int = None):
        with self._lock:
            expires_at = time.time() + ttl_seconds if ttl_seconds else None
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)


class RedisCache:
    """Thin wrapper matching TTLCache's interface, backed by Redis."""

    def __init__(self, redis_url: str):
        import redis  # imported lazily so redis is optional

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def get(self, key):
        try:
            return self._client.get(key)
        except Exception as e:
            logger.warning("Redis GET failed, treating as cache miss: %s", e)
            return None

    def set(self, key, value, ttl_seconds: int = None):
        try:
            if ttl_seconds:
                self._client.setex(key, ttl_seconds, value)
            else:
                self._client.set(key, value)
        except Exception as e:
            logger.warning("Redis SET failed, ignoring: %s", e)

    def delete(self, key):
        try:
            self._client.delete(key)
        except Exception as e:
            logger.warning("Redis DELETE failed, ignoring: %s", e)


def build_cache(redis_url: str):
    """Return a RedisCache if configured and reachable, else fall back to TTLCache."""
    if redis_url:
        try:
            cache = RedisCache(redis_url)
            cache._client.ping()
            logger.info("Using Redis cache backend.")
            return cache
        except Exception as e:
            logger.warning(
                "Redis configured but unreachable (%s). Falling back to "
                "in-process cache. This will NOT be shared across workers.",
                e,
            )
    logger.info("Using in-process TTL cache backend (single-process only).")
    return TTLCache()


# Populated by main.create_app() once config is known.
cache = None
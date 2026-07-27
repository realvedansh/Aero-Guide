"""
services/adaptive_learning.py

The "adaptive learning" skill: whenever Aero Guide AI is asked something it
doesn't already have a good stored answer for, it:
  1. checks a fast cache, then the knowledge-base table, for an
     exact-or-near-duplicate question it has already resolved before
  2. if nothing close enough is found, treats the question as "unknown":
     pulls in live web search context, asks the model, and PERSISTS the
     resolved answer to the knowledge base
  3. next time a similar question comes in, it's answered instantly from the
     knowledge base instead of hitting an external model again

This keeps answers consistent, cuts AI-provider load (important at 5k
concurrent users), and gives the assistant a genuinely growing memory.
"""

import difflib
import json
import logging
import datetime
from collections import namedtuple
import extensions 
from extensions import db
from models import KnowledgeEntry
from utils import normalize_for_matching

logger = logging.getLogger("aeroguide")

CACHE_TTL_SECONDS = 24 * 60 * 60

# Lightweight stand-in returned on a cache hit, so callers don't need to know
# whether an answer came from the shared cache or straight from the DB.
CachedAnswer = namedtuple("CachedAnswer", ["normalized_question", "answer", "verified"])


def _cache_key(normalized_question: str) -> str:
    return f"kb:{normalized_question}"


def _cache_entry(entry: KnowledgeEntry):
    """Best-effort write-through to the shared cache (Redis or in-process)."""
    if not extensions.cache:
        return
    try:
        extensions.cache.set(
            _cache_key(entry.normalized_question),
            json.dumps({"answer": entry.answer, "verified": bool(entry.verified)}),
            ttl_seconds=CACHE_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("Failed to write-through knowledge entry to cache: %s", e)


def _find_exact(normalized_question: str):
    return KnowledgeEntry.query.filter_by(
        normalized_question=normalized_question
    ).first()


def _find_fuzzy(normalized_question: str, threshold: float, max_candidates: int):
    """
    Lightweight fuzzy match: pull the most recent N knowledge entries and
    score them with difflib. This avoids adding a heavy embedding/vector-DB
    dependency while still catching near-duplicate phrasing. For very large
    knowledge bases, swap this for a vector similarity search (e.g. pgvector)
    without changing the calling code.
    """
    candidates = (
        KnowledgeEntry.query.order_by(KnowledgeEntry.last_used_at.desc())
        .limit(max_candidates)
        .all()
    )
    best_entry, best_score = None, 0.0
    for entry in candidates:
        score = difflib.SequenceMatcher(
            None, normalized_question, entry.normalized_question
        ).ratio()
        if score > best_score:
            best_entry, best_score = entry, score
    if best_entry and best_score >= threshold:
        return best_entry, best_score
    return None, 0.0


def recall(question: str, threshold: float, max_candidates: int):
    """
    Try to answer `question` from prior learning.
    Returns (KnowledgeEntry | CachedAnswer | None, similarity_score).
    """
    normalized = normalize_for_matching(question)
    if not normalized:
        return None, 0.0

    # Fast path: shared cache (Redis in production, in-process otherwise).
    if extensions.cache:
        try:
            cached_raw = extensions.cache.get(_cache_key(normalized))
            if cached_raw:
                payload = json.loads(cached_raw)
                return CachedAnswer(normalized, payload["answer"], bool(payload["verified"])), 1.0
        except Exception as e:
            logger.warning("Cache lookup failed, falling back to DB: %s", e)

    try:
        exact = _find_exact(normalized)
        if exact:
            _cache_entry(exact)
            return exact, 1.0
        return _find_fuzzy(normalized, threshold, max_candidates)
    except Exception as e:
        logger.error("Knowledge-base recall failed, treating as unknown: %s", e)
        return None, 0.0


def record_hit(entry) -> None:
    """Bump hit_count/last_used_at. Works for both a real KnowledgeEntry and a CachedAnswer."""
    try:
        db_entry = entry if isinstance(entry, KnowledgeEntry) else _find_exact(entry.normalized_question)
        if not db_entry:
            return
        db_entry.hit_count = (db_entry.hit_count or 0) + 1
        db_entry.last_used_at = datetime.datetime.utcnow()
        db.session.commit()
    except Exception as e:
        logger.warning("Failed to update knowledge entry hit stats: %s", e)
        db.session.rollback()


def learn(question: str, answer: str, source: str = "model"):
    """Persist a newly-resolved answer so the same question is instant next time."""
    normalized = normalize_for_matching(question)
    if not normalized or not answer:
        return None
    try:
        existing = _find_exact(normalized)
        if existing:
            existing.answer = answer
            existing.source = source
            existing.last_used_at = datetime.datetime.utcnow()
            db.session.commit()
            _cache_entry(existing)
            return existing

        entry = KnowledgeEntry(
            normalized_question=normalized,
            original_question=question[:2000],
            answer=answer,
            source=source,
            verified=False,
            hit_count=1,
        )
        db.session.add(entry)
        db.session.commit()
        _cache_entry(entry)
        return entry
    except Exception as e:
        logger.error("Failed to persist learned answer: %s", e)
        db.session.rollback()
        return None


def build_hedge_prefix(is_verified: bool) -> str:
    """A short, honest framing prepended when serving a not-yet-verified learned answer."""
    if is_verified:
        return ""
    return (
        "📘 *Maine pehle is jaisa sawaal seekha tha, is base par jawab de raha hoon "
        "(learned answer, teacher se verify kar lena):*\n\n"
    )
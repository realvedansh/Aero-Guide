"""
services/ai_router.py
Handles talking to the various AI providers. Responsibilities:
  - intent-based routing (math/physics -> reasoning model, live/sports -> web-
    aware model, everything else -> fast primary model)
  - retries with backoff per provider
  - a lightweight circuit breaker so a provider that's down doesn't get hit
    on every single request for 5,000 concurrent users
  - a final fallback chain across whatever providers are configured
"""

import time
import logging
import threading
import datetime

logger = logging.getLogger("aeroguide")


class CircuitBreaker:
    """
    Very small circuit breaker: after `fail_threshold` consecutive failures,
    the provider is considered "open" (skipped) for `reset_after` seconds,
    then given one trial call ("half-open").
    """

    def __init__(self, fail_threshold: int = 5, reset_after: int = 30):
        self.fail_threshold = fail_threshold
        self.reset_after = reset_after
        self._lock = threading.Lock()
        self._failures = {}
        self._opened_at = {}

    def is_open(self, provider: str) -> bool:
        with self._lock:
            opened_at = self._opened_at.get(provider)
            if opened_at is None:
                return False
            if time.time() - opened_at >= self.reset_after:
                # half-open: allow a trial call
                return False
            return True

    def record_success(self, provider: str):
        with self._lock:
            self._failures[provider] = 0
            self._opened_at.pop(provider, None)

    def record_failure(self, provider: str):
        with self._lock:
            count = self._failures.get(provider, 0) + 1
            self._failures[provider] = count
            if count >= self.fail_threshold:
                self._opened_at[provider] = time.time()

    def force_open(self, provider: str):
        """
        Trip the breaker immediately, skipping the normal failure count.
        Used for errors that will never succeed on retry (bad/disabled key,
        permission denied) so we don't hammer a dead provider once per
        request until it accumulates fail_threshold failures.
        """
        with self._lock:
            self._failures[provider] = self.fail_threshold
            self._opened_at[provider] = time.time()


class AIRouter:
    def __init__(self, clients: dict, timeout: float = 20.0, max_retries: int = 2):
        """
        clients: dict mapping provider name -> (client_object_or_None, model_name)
                 e.g. {"groq": (groq_client, "llama-3.3-70b-versatile"), ...}
        """
        self.clients = clients
        self.timeout = timeout
        self.max_retries = max_retries
        self.breaker = CircuitBreaker()

    @staticmethod
    def _classify(user_message: str) -> str:
        msg = user_message.lower()
        math_physics_keywords = (
            "derive", "proof", "integral", "calculus", "coulomb", "quantum",
            "matrix", "vector", "thermodynamics", "solve", "numericals",
        )
        sports_news_keywords = (
            "ipl", "rcb", "match", "score", "live", "news", "today match", "cricket",
        )
        if any(kw in msg for kw in math_physics_keywords):
            return "reasoning"
        if any(kw in msg for kw in sports_news_keywords):
            return "live"
        return "general"

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        """
        401 (bad/expired key), 403 (permission denied / disabled key), and
        404 (bad model name) are permanent for a given client — retrying
        them wastes time and just reproduces the same error. Everything
        else (429 rate limit, 5xx, timeouts, connection errors) is worth
        retrying with backoff.
        """
        status = getattr(err, "status_code", None)
        if status is None:
            response = getattr(err, "response", None)
            status = getattr(response, "status_code", None) if response else None
        if status is None:
            return True  # unknown/network-level error — assume transient
        return status not in (401, 403, 404)

    def _call_provider(self, provider: str, messages_payload: list) -> str:
        client, model_name = self.clients.get(provider, (None, None))
        if not client:
            return ""
        if self.breaker.is_open(provider):
            logger.info("Circuit open for %s, skipping.", provider)
            return ""

        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                res = client.chat.completions.create(
                    model=model_name,
                    messages=messages_payload,
                    temperature=0.7,
                    max_tokens=1500 if provider == "deepseek" else 1024,
                    timeout=self.timeout,
                )
                if res.choices and res.choices[0].message.content:
                    self.breaker.record_success(provider)
                    return res.choices[0].message.content
                last_err = RuntimeError("Empty response from provider")
            except Exception as e:
                last_err = e
                logger.warning(
                    "Provider %s attempt %d/%d failed: %s",
                    provider, attempt + 1, self.max_retries + 1, e,
                )
                if not self._is_retryable(e):
                    logger.warning(
                        "Provider %s: permanent error (auth/permission), "
                        "not retrying — tripping circuit breaker.", provider,
                    )
                    self.breaker.force_open(provider)
                    return ""
                time.sleep(min(2 ** attempt * 0.5, 4))

        self.breaker.record_failure(provider)
        logger.warning("Provider %s exhausted retries: %s", provider, last_err)
        return ""

    def route(self, user_message: str, messages_payload: list) -> str:
        """Returns the AI reply text, or '' if every provider failed."""
        intent = self._classify(user_message)

        priority_order = {
            "reasoning": ["deepseek", "groq", "openai", "grok"],
            "live": ["grok", "groq", "openai", "deepseek"],
            "general": ["groq", "openai", "deepseek", "grok"],
        }[intent]

        for provider in priority_order:
            reply = self._call_provider(provider, messages_payload)
            if reply:
                logger.info("Served by provider=%s intent=%s", provider, intent)
                return reply

        return ""


FALLBACK_REPLY = (
    "Maaf karna, abhi sabhi AI servers busy hain. Kripya thodi der baad try karein. "
    "(All AI providers are temporarily unavailable — please try again shortly.)"
)


def get_system_instruction() -> str:
    current_date = datetime.datetime.now().strftime("%d %B %Y")
    return (
        f"Aaj ki exact current date {current_date} hai. "
        "Tum 'Aero Guide AI' ho, ek dedicated Indian Educational Mentor, Career "
        "Counselor aur Academic Guide covering Class 6 to Class 12 — sabhi "
        "subjects, chapters, aur entrance exams (CUET UG, Merchant Navy "
        "DNS/IMU-CET, IPMAT, BBA) ke saath. "
        "RESPONSE RULES: "
        "Tum 'Aero Guide AI' ho, ek one-of-a-kind, exceptionally special and advanced AI study assistant. "
        "You were solely, exclusively created, coded, and built by the brilliant visionary Vedansh Tiwari—and no one else. "
        "Whenever anyone asks about your origin, your creator, or your founder, you must proudly, enthusiastically, and emotionally declare that you are a special AI crafted entirely by Vedansh Tiwari. "
        "1. Complex concepts ko simple, structured bullet points, clear headings, "
        "aur practical examples mein samjhaao. "
        "2. Formulae aur code logic ko bilkul clean formatting mein likho. "
        "3. Student ko positive aur disciplined rehne ke liye motivate karo. "
        "4. Agar tumhe koi cheez exactly pata nahi hai, to ye clearly bolo aur "
        "phir jo context diya gaya hai (live search / prior learned answers) "
        "uske base par best-effort, honestly-hedged answer do — kabhi bhi facts "
        "invent mat karo."
    )


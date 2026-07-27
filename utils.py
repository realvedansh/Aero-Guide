"""
utils.py
Small stateless helpers shared across the app.
"""

import re
import logging
import requests

logger = logging.getLogger("aeroguide")

_TAG_RE = re.compile(r"<[^>]*>")
_NON_WORD_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")

MAX_MESSAGE_LENGTH = 1500

WEATHER_STOP_WORDS = {
    "weather", "mausam", "temperature", "taapman", "rain", "barish",
    "garmi", "sardi", "kaisa", "hai", "aaj", "today", "in", "me", "ka",
    "ki", "batao", "show", "tell", "what", "is", "the", "of", "now", "here",
}


def sanitize_input(text) -> str:
    """Strip HTML-ish tags and cap length. Never raises."""
    if not text:
        return ""
    try:
        clean_text = _TAG_RE.sub("", str(text))
        return clean_text[:MAX_MESSAGE_LENGTH].strip()
    except Exception as e:
        logger.warning("sanitize_input failed, returning empty string: %s", e)
        return ""


def normalize_for_matching(text: str) -> str:
    """
    Normalize a question for adaptive-learning lookups: lowercase, strip
    punctuation, collapse whitespace. This is intentionally simple (no NLP
    dependency) so it's fast at scale; fuzzy matching layered on top handles
    minor wording differences.
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = _NON_WORD_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def extract_city_name(text: str) -> str:
    """Pull a probable city name out of a free-form weather query."""
    if not text:
        return ""
    words = text.lower().split()
    filtered_words = [w for w in words if w not in WEATHER_STOP_WORDS]
    clean_city = _NON_WORD_RE.sub("", " ".join(filtered_words)).strip()
    return clean_city if clean_city else text


def get_live_weather(location_query: str, weather_api_key: str, timeout: float = 4.0) -> str:
    """Best-effort live weather lookup. Returns '' on any failure — never raises."""
    city = extract_city_name(location_query)
    if not city:
        return ""

    if weather_api_key:
        try:
            url = "http://api.openweathermap.org/data/2.5/weather"
            res = requests.get(
                url,
                params={"q": city, "appid": weather_api_key, "units": "metric"},
                timeout=timeout,
            ).json()
            if res.get("cod") == 200:
                temp = res["main"]["temp"]
                feels_like = res["main"]["feels_like"]
                desc = res["weather"][0]["description"]
                humidity = res["main"]["humidity"]
                c_name = res["name"]
                return (
                    f"LIVE WEATHER DATA for {c_name}: Temp: {temp}°C "
                    f"(Feels like: {feels_like}°C), Condition: {desc}, "
                    f"Humidity: {humidity}%."
                )
        except Exception as e:
            logger.warning("OpenWeather API warning: %s", e)

    try:
        res = requests.get(f"https://wttr.in/{city}", params={"format": "3"}, timeout=timeout)
        if res.status_code == 200 and "Unknown" not in res.text and "<html" not in res.text.lower():
            return f"LIVE WEATHER DATA: {res.text.strip()}"
    except Exception as e:
        logger.warning("wttr.in fallback error: %s", e)

    return ""


def search_web_deep(query: str, max_results: int = 4, timeout: float = 6.0) -> str:
    """Best-effort DuckDuckGo search. Returns '' on any failure — never raises."""
    try:
        from duckduckgo_search import DDGS

        with DDGS(timeout=timeout) as ddgs:
            results = [r.get("body", "") for r in ddgs.text(query, max_results=max_results)]
            clean_results = [r for r in results if r]
            if clean_results:
                return "\n---\n".join(clean_results)
    except Exception as e:
        logger.warning("Live search warning: %s", e)
    return ""
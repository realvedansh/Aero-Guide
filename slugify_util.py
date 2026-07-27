"""slugify_util.py — tiny dependency-free slug generator."""

import re

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = _NON_ALNUM_RE.sub("-", text).strip("-")
    return text or "item"
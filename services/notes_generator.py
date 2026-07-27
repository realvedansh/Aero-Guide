"""
services/notes_generator.py

Generates clean, beautifully styled "handwritten-style" study notes.

Approach:
  1. Ask the AI model for STRICT JSON: a title plus a list of sections, each
     with a heading and bullet points.
  2. Parse defensively (models sometimes wrap JSON in prose or code fences).
  3. Render that structured content into a self-contained HTML page styled
     like notebook paper, using a handwriting web font, so the frontend can
     display or export it directly (e.g. in an <iframe> or as a PDF).

Returning structured JSON *and* rendered HTML means the frontend can either
render the HTML directly or build its own custom UI from the JSON.
"""

import json
import logging
import re
import html
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeroguide")

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

NOTES_JSON_PROMPT_TEMPLATE = (
    "Topic: '{topic}'.\n"
    "Generate concise Class 6-12 style study notes as STRICT JSON ONLY — no "
    "prose before or after, no markdown code fences. Schema:\n"
    "{{\n"
    '  "title": "string",\n'
    '  "sections": [\n'
    '    {{"heading": "string", "points": ["string", "string", ...]}}\n'
    "  ]\n"
    "}}\n"
    "Rules: 4-7 sections, each with 3-6 short, exam-focused bullet points. "
    "Include key formulas/definitions where relevant, written in plain text "
    "(no LaTeX). Keep every bullet under 20 words."
)


def build_notes_prompt(topic: str) -> str:
    safe_topic = str(topic).strip() if topic is not None else ""
    if not safe_topic:
        safe_topic = "General Study Topic"
    return NOTES_JSON_PROMPT_TEMPLATE.format(topic=safe_topic)


def _fallback_notes(raw_text: str) -> Dict[str, Any]:
    """Wrap raw text as a single plain-text section."""
    snippet = raw_text.strip()[:2000] if raw_text else "No content was generated."
    return {
        "title": "Study Notes",
        "sections": [{"heading": "Notes", "points": [snippet]}],
    }


def parse_notes_json(raw_text: Optional[str]) -> Dict[str, Any]:
    """
    Defensively parse the model's JSON response. Falls back to wrapping raw
    text as a single section if the model didn't return valid JSON (or the
    JSON doesn't match the expected shape), so the endpoint never hard-fails
    on a malformed model response.
    """
    if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
        return {"title": "Notes", "sections": []}

    text = raw_text.strip()
    # Strip common code-fence wrapping.
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    data = None
    try:
        data = json.loads(text)
    except Exception:
        match = _JSON_BLOCK_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = None

    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        logger.warning("Notes model reply wasn't valid JSON; falling back to plain text section.")
        return _fallback_notes(raw_text)

    # Normalize shape defensively — every field is untrusted model output.
    try:
        raw_title = data.get("title")
        title = str(raw_title).strip() if raw_title is not None else ""
        title = title or "Study Notes"

        sections: List[Dict[str, Any]] = []
        for sec in data.get("sections", [])[:12]:
            if not isinstance(sec, dict):
                continue

            raw_heading = sec.get("heading")
            heading = (str(raw_heading).strip() if raw_heading is not None else "") or "Section"

            raw_points = sec.get("points")
            if not isinstance(raw_points, list):
                continue
            points = [str(p).strip() for p in raw_points if p is not None and str(p).strip()][:10]
            if points:
                sections.append({"heading": heading, "points": points})

        if not sections:
            # Valid JSON shape but nothing usable came out of it.
            return _fallback_notes(raw_text)

        return {"title": title, "sections": sections}
    except Exception:
        logger.exception("Unexpected error normalizing notes JSON; falling back to plain text section.")
        return _fallback_notes(raw_text)


_HANDWRITTEN_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Kalam:wght@400;700&family=Caveat:wght@600;700&display=swap');

.hw-note-page {
  --paper: #fdf6e3;
  --line: #cfd6e0;
  --ink: #2b3a55;
  --accent: #d1495b;
  max-width: 720px;
  margin: 0 auto;
  background:
    linear-gradient(var(--paper), var(--paper)),
    repeating-linear-gradient(
      to bottom,
      transparent 0px,
      transparent 33px,
      var(--line) 34px
    );
  background-blend-mode: normal;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.18);
  padding: 40px 36px 48px 64px;
  position: relative;
  font-family: 'Kalam', cursive;
  color: var(--ink);
  line-height: 34px;
}
.hw-note-page::before {
  content: "";
  position: absolute;
  top: 0; bottom: 0; left: 44px;
  width: 2px;
  background: #f2b8c6;
}
.hw-note-title {
  font-family: 'Caveat', cursive;
  font-size: 40px;
  font-weight: 700;
  color: var(--accent);
  margin: 0 0 6px 0;
  line-height: 40px;
}
.hw-note-underline {
  border: none;
  border-top: 2px dashed var(--accent);
  margin: 0 0 18px 0;
  opacity: 0.6;
}
.hw-note-section {
  margin-bottom: 6px;
}
.hw-note-heading {
  font-size: 24px;
  font-weight: 700;
  margin: 18px 0 2px 0;
  color: var(--ink);
}
.hw-note-points {
  margin: 0 0 0 0;
  padding-left: 22px;
}
.hw-note-points li {
  font-size: 20px;
  margin-bottom: 0;
}
"""


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def render_handwritten_html(notes_data: Optional[dict]) -> str:
    """
    Render the structured notes dict into a self-contained HTML fragment.
    Defensive against any malformed shape — this never raises; on any
    unexpected input it falls back to a minimal-but-valid page.
    """
    try:
        notes_data = notes_data if isinstance(notes_data, dict) else {}
        title = html.escape(_safe_str(notes_data.get("title"), "Study Notes"))

        raw_sections = notes_data.get("sections")
        raw_sections = raw_sections if isinstance(raw_sections, list) else []

        sections_html = []
        for sec in raw_sections:
            if not isinstance(sec, dict):
                continue
            heading = html.escape(_safe_str(sec.get("heading"), "Section"))

            raw_points = sec.get("points")
            raw_points = raw_points if isinstance(raw_points, list) else []
            points = "".join(
                f"<li>{html.escape(_safe_str(p))}</li>" for p in raw_points if _safe_str(p)
            )
            if not points:
                continue

            sections_html.append(
                f'<div class="hw-note-section">'
                f'<div class="hw-note-heading">{heading}</div>'
                f'<ul class="hw-note-points">{points}</ul>'
                f"</div>"
            )

        return (
            f"<style>{_HANDWRITTEN_CSS}</style>"
            f'<div class="hw-note-page">'
            f'<div class="hw-note-title">{title}</div>'
            f'<hr class="hw-note-underline"/>'
            f'{"".join(sections_html)}'
            f"</div>"
        )
    except Exception:
        logger.exception("Unexpected error rendering handwritten notes HTML.")
        return (
            f"<style>{_HANDWRITTEN_CSS}</style>"
            f'<div class="hw-note-page">'
            f'<div class="hw-note-title">Study Notes</div>'
            f'<hr class="hw-note-underline"/>'
            f"</div>"
        )
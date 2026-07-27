"""
main.py — Aero Guide AI backend

Flask application factory. Wires together:
  - config (env-driven, dev/prod)
  - extensions (db, rate limiter, cache)
  - AI router with retries + circuit breaker + fallback chain
  - adaptive learning (knowledge base that grows over time)
  - handwritten-style notes renderer
  - the Class 6-12 curriculum database

Run for local development:
    python main.py

Run in production (recommended — see README.md for full details):
    gunicorn -k gevent -w 4 --threads 8 -b 0.0.0.0:8000 "main:create_app()"
"""

import logging
import uuid

from flask import Flask, request, jsonify, session, render_template

from config import get_config
import extensions
from extensions import db, limiter
from groq import Groq
from openai import OpenAI

from utils import sanitize_input, get_live_weather, search_web_deep
from services.ai_router import AIRouter, get_system_instruction, FALLBACK_REPLY
from services import adaptive_learning
from services import notes_generator
from services import education_service


def _build_ai_clients(cfg):
    """Build the provider client map once, tolerating missing API keys gracefully."""
    clients = {}

    groq_client = Groq(api_key=cfg.GROQ_API_KEY) if cfg.GROQ_API_KEY else None
    clients["groq"] = (groq_client, "llama-3.3-70b-versatile")

    openai_client = OpenAI(api_key=cfg.OPENAI_API_KEY) if cfg.OPENAI_API_KEY else None
    clients["openai"] = (openai_client, "gpt-4o-mini")

    deepseek_client = (
        OpenAI(api_key=cfg.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        if cfg.DEEPSEEK_API_KEY else None
    )
    clients["deepseek"] = (deepseek_client, "deepseek-reasoner")

    grok_client = (
        OpenAI(api_key=cfg.GROK_API_KEY, base_url="https://api.x.ai/v1")
        if cfg.GROK_API_KEY else None
    )
    if grok_client:
        try:
            # Cheap startup probe: catches a disabled/revoked key immediately
            # instead of discovering it 3 retries deep on every user request.
            grok_client.models.list()
        except Exception as e:
            logging.getLogger("aeroguide").warning(
                "Grok key configured but not usable (%s) — disabling provider "
                "until the key is fixed at https://console.x.ai", e
            )
            grok_client = None
    clients["grok"] = (grok_client, "grok-4.3")

    configured = [name for name, (c, _) in clients.items() if c]
    if not configured:
        logging.getLogger("aeroguide").warning(
            "No AI provider API keys configured — /ask and friends will "
            "return the static fallback reply until at least one is set."
        )
    else:
        logging.getLogger("aeroguide").info("AI providers configured: %s", configured)

    return clients


def create_app():
    cfg = get_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("aeroguide")

    app = Flask(__name__)
    app.config.from_object(cfg)

    db.init_app(app)
    limiter.init_app(app)
    extensions.cache = extensions.build_cache(cfg.REDIS_URL)

    with app.app_context():
        db.create_all()

    ai_clients = _build_ai_clients(cfg)
    ai_router = AIRouter(ai_clients, timeout=cfg.AI_REQUEST_TIMEOUT, max_retries=cfg.AI_MAX_RETRIES)

    # ------------------------------------------------------------------
    # Error handlers — every route returns clean JSON on failure, never a
    # raw traceback, and every 5xx is logged with enough context to debug.
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "Request body too large."}), 413

    @app.errorhandler(429)
    def rate_limited(e):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429

    @app.errorhandler(500)
    def server_error(e):
        logger.error("Unhandled server error: %s", e)
        return jsonify({"error": "Internal server error. Please try again."}), 500

    # ------------------------------------------------------------------
    # Session / health
    # ------------------------------------------------------------------

    @app.route("/")
    def home():
        if "user_session_id" not in session:
            session["user_session_id"] = str(uuid.uuid4())
        try:
            return render_template("index.html", firebase_api_key=cfg.FIREBASE_API_KEY)
        except Exception:
            # Template is optional if this backend is used purely as an API.
            return jsonify({"app": "Aero Guide AI", "status": "online"})

    @app.route("/health", methods=["GET"])
    def health_check():
        db_ok = True
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception as e:
            db_ok = False
            logger.error("Health check DB failure: %s", e)
        configured_providers = [name for name, (c, _) in ai_clients.items() if c]
        return jsonify({
            "status": "online" if db_ok else "degraded",
            "app": "Aero Guide AI Complete Education Platform",
            "database": "ok" if db_ok else "error",
            "ai_providers_configured": configured_providers,
        }), (200 if db_ok else 503)

    # ------------------------------------------------------------------
    # Core chat endpoint — adaptive learning + live context + smart routing
    # ------------------------------------------------------------------

    @app.route("/ask", methods=["POST"])
    @limiter.limit(cfg.RATE_LIMIT_ASK)
    def ask_ai():
        try:
            data = request.get_json(silent=True) or {}
            user_message = sanitize_input(data.get("message", ""))

            if not user_message:
                return jsonify({"reply": "Kripya ek valid message likhein."}), 400

            session_id = data.get("user_id") or session.get("user_session_id") or "default_user"
            session_id = str(session_id)[:64]

            # 1. Adaptive learning: have we resolved something like this before?
            learned_entry, similarity = adaptive_learning.recall(
                user_message, cfg.KB_SIMILARITY_THRESHOLD, cfg.KB_MAX_CANDIDATES
            )
            if learned_entry:
                adaptive_learning.record_hit(learned_entry)
                reply = adaptive_learning.build_hedge_prefix(learned_entry.verified) + learned_entry.answer
                _persist_turn(session_id, user_message, reply)
                return jsonify({
                    "reply": reply,
                    "source": "knowledge_base",
                    "similarity": round(similarity, 3),
                })

            # 2. Unknown question -> gather live context.
            weather_keywords = ("weather", "mausam", "temperature", "taapman", "rain", "barish", "garmi", "sardi")
            is_weather = any(word in user_message.lower() for word in weather_keywords)
            live_weather_info = (
                get_live_weather(user_message, cfg.WEATHER_API_KEY) if is_weather else ""
            )
            search_context = search_web_deep(user_message) if cfg.ENABLE_LIVE_SEARCH else ""

            context_parts = []
            if live_weather_info:
                context_parts.append(f"[LIVE WEATHER] {live_weather_info}")
            if search_context:
                context_parts.append(f"[LIVE SEARCH CONTEXT]\n{search_context}")

            enhanced_message = (
                "[SYSTEM CONTEXT]\n" + "\n".join(context_parts) + f"\n\nUser Question: {user_message}"
                if context_parts else user_message
            )

            chat_history = _get_recent_history(session_id, limit=10)
            ai_payload = [{"role": "system", "content": get_system_instruction()}]
            ai_payload.extend(chat_history)
            ai_payload.append({"role": "user", "content": enhanced_message})

            ai_reply = ai_router.route(user_message, ai_payload)
            source = "model"

            if not ai_reply:
                ai_reply = FALLBACK_REPLY
                source = "fallback"

            # 3. Learn from this resolution so next time it's instant.
            if source == "model":
                adaptive_learning.learn(
                    user_message, ai_reply,
                    source="web_search" if search_context else "model",
                )

            _persist_turn(session_id, user_message, ai_reply)
            return jsonify({"reply": ai_reply, "source": source})

        except Exception as e:
            logger.error("Server error in /ask: %s", e)
            return jsonify({"reply": "Maaf karna, server par thodi dikkat aa rahi hai."}), 500

    def _get_recent_history(session_id, limit=10):
        from models import ChatMessage
        try:
            rows = (
                ChatMessage.query.filter_by(session_id=session_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
                .all()
            )
            return [m.to_dict() for m in reversed(rows)]
        except Exception as e:
            logger.error("Error reading chat history: %s", e)
            return []

    def _persist_turn(session_id, user_message, ai_reply):
        from models import ChatMessage
        try:
            db.session.add(ChatMessage(session_id=session_id, role="user", content=user_message))
            db.session.add(ChatMessage(session_id=session_id, role="assistant", content=ai_reply))
            db.session.commit()
        except Exception as e:
            logger.error("Error saving chat turn: %s", e)
            db.session.rollback()

    @app.route("/reset", methods=["POST"])
    def reset_memory():
        from models import ChatMessage
        try:
            data = request.get_json(silent=True) or {}
            session_id = str(data.get("user_id") or session.get("user_session_id") or "default_user")[:64]
            ChatMessage.query.filter_by(session_id=session_id).delete()
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error("Error resetting history: %s", e)
            db.session.rollback()
            return jsonify({"success": False, "error": "Could not reset history."}), 500

    # ------------------------------------------------------------------
    # Handwritten-style notes
    # ------------------------------------------------------------------

    @app.route("/get_notes", methods=["POST"])
    @limiter.limit(cfg.RATE_LIMIT_DEFAULT)
    def get_notes():
        try:
            data = request.get_json(silent=True) or {}
            subject = sanitize_input(data.get("subject", "Class 12"))
            style = (data.get("style") or "handwritten").lower()

            if style == "handwritten":
                prompt = notes_generator.build_notes_prompt(subject)
                ai_payload = [
                    {"role": "system", "content": get_system_instruction()},
                    {"role": "user", "content": prompt},
                ]
                raw = ai_router.route(subject, ai_payload) or ""
                notes_data = notes_generator.parse_notes_json(raw)
                html_out = notes_generator.render_handwritten_html(notes_data)
                return jsonify({"notes": notes_data, "html": html_out})

            # plain-text fallback style
            prompt = (
                f"Subject/Chapter '{subject}' ke Important Formulas, Key Terms, aur Quick "
                "Revision Notes ekdam clean bullet points, bold headings aur visual boxes "
                "ke format mein generate karo."
            )
            ai_payload = [
                {"role": "system", "content": get_system_instruction()},
                {"role": "user", "content": prompt},
            ]
            notes_text = ai_router.route(subject, ai_payload) or FALLBACK_REPLY
            return jsonify({"notes": notes_text})
        except Exception as e:
            logger.error("Error in /get_notes: %s", e)
            return jsonify({"error": "Notes generate karne mein dikkat aayi."}), 500

    # ------------------------------------------------------------------
    # Quiz + study plan
    # ------------------------------------------------------------------

    @app.route("/generate_quiz", methods=["POST"])
    @limiter.limit(cfg.RATE_LIMIT_DEFAULT)
    def generate_quiz():
        try:
            data = request.get_json(silent=True) or {}
            topic = sanitize_input(data.get("topic", "Class 12 Physics"))
            prompt = (
                f"Topic '{topic}' par 3 Multiple Choice Questions (MCQs) banao. "
                "Format ye hona chahiye:\n"
                "Question N:\n[A] ...\n[B] ...\n[C] ...\n[D] ...\n"
                "Correct Answer: [X]\nExplanation: ..."
            )
            ai_payload = [
                {"role": "system", "content": get_system_instruction()},
                {"role": "user", "content": prompt},
            ]
            quiz_text = ai_router.route(topic, ai_payload) or FALLBACK_REPLY
            return jsonify({"quiz": quiz_text})
        except Exception as e:
            logger.error("Error in /generate_quiz: %s", e)
            return jsonify({"error": "Quiz generate karne mein dikkat aayi."}), 500

    @app.route("/generate_plan", methods=["POST"])
    @limiter.limit(cfg.RATE_LIMIT_DEFAULT)
    def generate_plan():
        try:
            data = request.get_json(silent=True) or {}
            exam = sanitize_input(data.get("exam", "CUET & Class 12 Boards"))
            hours = sanitize_input(data.get("hours", "4 hours"))
            prompt = (
                f"Target Exam: {exam}. Daily Study Time: {hours}.\n"
                "Ek highly practical, realistic daily timetable aur subject-wise strategy "
                "plan bana kar do jisme Revision Slots aur Short Breaks (Pomodoro) included hon."
            )
            ai_payload = [
                {"role": "system", "content": get_system_instruction()},
                {"role": "user", "content": prompt},
            ]
            plan_text = ai_router.route(exam, ai_payload) or FALLBACK_REPLY
            return jsonify({"plan": plan_text})
        except Exception as e:
            logger.error("Error in /generate_plan: %s", e)
            return jsonify({"error": "Timetable generate karne mein dikkat aayi."}), 500

    # ------------------------------------------------------------------
    # Class 6-12 curriculum database (read endpoints)
    # ------------------------------------------------------------------

    @app.route("/api/curriculum/classes", methods=["GET"])
    def api_list_classes():
        return jsonify({"classes": [c.to_dict() for c in education_service.list_classes()]})

    @app.route("/api/curriculum/<int:grade>/subjects", methods=["GET"])
    def api_list_subjects(grade):
        if grade < 6 or grade > 12:
            return jsonify({"error": "grade must be between 6 and 12"}), 400
        return jsonify({"subjects": [s.to_dict() for s in education_service.list_subjects(grade)]})

    @app.route("/api/curriculum/<int:grade>/<subject_slug>/chapters", methods=["GET"])
    def api_list_chapters(grade, subject_slug):
        chapters = education_service.list_chapters(grade, subject_slug)
        return jsonify({"chapters": [c.to_dict() for c in chapters]})

    @app.route("/api/curriculum/<int:grade>/<subject_slug>/<chapter_slug>", methods=["GET"])
    def api_get_chapter(grade, subject_slug, chapter_slug):
        chapter = education_service.get_chapter(grade, subject_slug, chapter_slug)
        if not chapter:
            return jsonify({"error": "Chapter not found."}), 404
        return jsonify({"chapter": chapter.to_dict(include_children=True)})

    @app.route("/api/curriculum/search", methods=["GET"])
    @limiter.limit(cfg.RATE_LIMIT_DEFAULT)
    def api_search_chapters():
        q = sanitize_input(request.args.get("q", ""))
        if not q:
            return jsonify({"results": []})
        results = education_service.search_chapters(q)
        return jsonify({"results": [c.to_dict() for c in results]})

    # ------------------------------------------------------------------
    # Admin write endpoints for growing the curriculum database.
    # Protected by a static API key — swap for real auth before production.
    # ------------------------------------------------------------------

    def _require_admin():
        key = request.headers.get("X-Admin-Key", "")
        if not cfg.ADMIN_API_KEY or key != cfg.ADMIN_API_KEY:
            return False
        return True

    @app.route("/admin/content/chapter", methods=["POST"])
    def admin_add_chapter():
        from models import Question
        if not _require_admin():
            return jsonify({"error": "Unauthorized."}), 401
        try:
            data = request.get_json(silent=True) or {}
            grade = int(data["grade"])
            subject_name = str(data["subject"])
            chapter_name = str(data["chapter"])
            summary = str(data.get("summary", ""))
            if grade < 6 or grade > 12:
                return jsonify({"error": "grade must be between 6 and 12"}), 400

            notes_in = data.get("notes", [])
            questions_in = data.get("questions", [])

            # Validate every item up front. If item 3 of 5 questions is
            # malformed we must fail before writing anything, otherwise
            # items 1-2 would already be committed while the client sees a
            # 400 as if nothing was saved.
            for i, note in enumerate(notes_in):
                if not isinstance(note, dict):
                    return jsonify({"error": f"notes[{i}] must be an object"}), 400
            for i, q in enumerate(questions_in):
                if not isinstance(q, dict):
                    return jsonify({"error": f"questions[{i}] must be an object"}), 400
                if "question_text" not in q or not str(q["question_text"]).strip():
                    return jsonify({"error": f"questions[{i}] is missing 'question_text'"}), 400
                if "correct_answer" not in q or not str(q["correct_answer"]).strip():
                    return jsonify({"error": f"questions[{i}] is missing 'correct_answer'"}), 400
                if q.get("q_type", "mcq") not in Question.QUESTION_TYPES:
                    return jsonify({
                        "error": f"questions[{i}] has invalid q_type "
                                 f"(must be one of {Question.QUESTION_TYPES})"
                    }), 400

            chapter = education_service.upsert_chapter(grade, subject_name, chapter_name, summary)

            for note in notes_in:
                education_service.add_note(chapter, note.get("title", "Notes"), note.get("content", ""))
            for q in questions_in:
                education_service.add_question(
                    chapter,
                    q_type=q.get("q_type", "mcq"),
                    question_text=q["question_text"],
                    correct_answer=q["correct_answer"],
                    options=q.get("options"),
                    explanation=q.get("explanation", ""),
                    difficulty=q.get("difficulty", "medium"),
                )
            return jsonify({"success": True, "chapter": chapter.to_dict(include_children=True)})
        except KeyError as e:
            return jsonify({"error": f"Missing required field: {e}"}), 400
        except Exception as e:
            logger.error("Error in /admin/content/chapter: %s", e)
            db.session.rollback()
            return jsonify({"error": "Could not add chapter."}), 500

    @app.route("/admin/knowledge/verify/<entry_uuid>", methods=["POST"])
    def admin_verify_knowledge(entry_uuid):
        if not _require_admin():
            return jsonify({"error": "Unauthorized."}), 401
        from models import KnowledgeEntry
        entry = KnowledgeEntry.query.filter_by(entry_uuid=entry_uuid).first()
        if not entry:
            return jsonify({"error": "Not found."}), 404
        entry.verified = True
        entry.source = "admin"
        db.session.commit()
        return jsonify({"success": True})

    return app


if __name__ == "__main__":
    # Flask's built-in dev server is single-threaded/single-process and is
    # NOT suitable for 5,000 concurrent users. For local development only.
    # For production, see README.md (gunicorn + gevent/eventlet workers).
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
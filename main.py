import os
import datetime
import sqlite3
import uuid
import re
import logging
import requests
from flask import Flask, render_template, request, jsonify, session
from groq import Groq
from openai import OpenAI
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# 1. Environment Variables Load
load_dotenv()

# 2. Production-Grade Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "vedansh_super_secret_key_2026")

# 3. API Keys Loading with String Cleanups
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
GROK_API_KEY = os.environ.get("GROK_API_KEY", "").strip()
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "").strip()
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "").strip()

# 4. Initialize AI Clients Safely
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# DeepSeek R1 Official OpenAI-Compatible Client
deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY, 
    base_url="https://api.deepseek.com"
) if DEEPSEEK_API_KEY else None

# Grok (xAI) Official OpenAI-Compatible Client
grok_client = OpenAI(
    api_key=GROK_API_KEY, 
    base_url="https://api.x.ai/v1"
) if GROK_API_KEY else None

# 5. SQLite Database Setup
DB_NAME = "chat_database.db"

def init_db():
    """Database initialize karne ke liye function"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT
            )
        ''')
        conn.commit()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

init_db()

# --- Database Helper Functions ---

def get_db_history(session_id, limit=10):
    """Database se session wise chat history fetch karo"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND role != 'system' ORDER BY id DESC LIMIT ?", 
            (session_id, limit)
        )
        rows = cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]
    except Exception as e:
        logging.error(f"Error reading history from DB: {e}")
        return []
    finally:
        if conn:
            conn.close()

def save_to_db(session_id, role, content):
    """Naya message DB mein save karo"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", 
            (session_id, role, content)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Error saving message to DB: {e}")
    finally:
        if conn:
            conn.close()

def clear_db_history(session_id):
    """User ki chat history delete karo"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Error clearing history from DB: {e}")
    finally:
        if conn:
            conn.close()

# --- Security & Helper Functions ---

def sanitize_input(text):
    """Security: Input Sanitization"""
    if not text:
        return ""
    clean_text = re.sub(r'<[^>]*>', '', str(text))
    return clean_text[:1500].strip()

def extract_city_name(text):
    """Sentence me se exact city name extract karne ka smart helper"""
    stop_words = [
        "weather", "mausam", "temperature", "taapman", "rain", "barish", 
        "garmi", "sardi", "kaisa", "hai", "aaj", "today", "in", "me", "ka", 
        "ki", "batao", "show", "tell", "what", "is", "the", "of", "now", "here"
    ]
    words = text.lower().split()
    filtered_words = [w for w in words if w not in stop_words]
    clean_city = re.sub(r'[^\w\s]', '', " ".join(filtered_words)).strip()
    return clean_city if clean_city else text

def get_live_weather(location_query):
    """Live Weather Fetch Function"""
    city = extract_city_name(location_query)
    if not city:
        return ""
        
    if WEATHER_API_KEY:
        try:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric"
            res = requests.get(url, timeout=4).json()
            if res.get("cod") == 200:
                temp = res["main"]["temp"]
                feels_like = res["main"]["feels_like"]
                desc = res["weather"][0]["description"]
                humidity = res["main"]["humidity"]
                c_name = res["name"]
                return f"LIVE WEATHER DATA for {c_name}: Temp: {temp}°C (Feels like: {feels_like}°C), Condition: {desc}, Humidity: {humidity}%."
        except Exception as e:
            logging.warning(f"OpenWeather API Warning: {e}")

    try:
        res = requests.get(f"https://wttr.in/{city}?format=3", timeout=4)
        if res.status_code == 200 and "Unknown" not in res.text and "<html" not in res.text.lower():
            return f"LIVE WEATHER DATA: {res.text.strip()}"
    except Exception as e:
        logging.warning(f"wttr.in fallback error: {e}")

    return ""

def search_web_deep(query):
    """Detailed Live Search via DuckDuckGo"""
    try:
        with DDGS() as ddgs:
            results = [r.get('body', '') for r in ddgs.text(query, max_results=4)]
            clean_results = [r for r in results if r]
            if clean_results:
                return "\n---\n".join(clean_results)
    except Exception as e:
        logging.warning(f"Live Search Warning: {e}")
    return ""

def get_system_instruction():
    """Education Dedicated AI Persona & System Rules"""
    current_date = datetime.datetime.now().strftime("%d %B %Y")
    return (
        f"Aaj ki exact current date {current_date} hai. "
        "Tum 'Aero Guide AI' ✈️ ho, ek dedicated Indian Educational Mentor, Career Counselor aur Academic Guide. "
        "Tumhe Vedansh Tiwari ne banaya hai. "
        "Tumhara main focus Class 12 (PCM, Computer Science, English), Entrance Exams (CUET UG, Merchant Navy DNS/IMU-CET, IPMAT, BBA) aur Study Planning par hai. "
        "RESPONSE RULES: "
        "1. Complex concepts ko simple, structured bullet points, clear headings, aur practical examples mein samjhaao. "
        "2. Formulae aur CS code logic ko bilkul clean formatting mein likho. "
        "3. Student ko positive aur disciplined rehne ke liye motivate karo."
    )

def call_ai_model_smart_routing(user_message, messages_payload):
    """Smart Intent-Based AI Model Routing"""
    ai_reply = ""
    msg_lower = user_message.lower()

    math_physics_keywords = ["derive", "proof", "integral", "calculus", "coulomb", "quantum", "matrix", "vector", "thermodynamics", "solve", "numericals"]
    is_math_complex = any(kw in msg_lower for kw in math_physics_keywords)

    sports_news_keywords = ["ipl", "rcb", "match", "score", "live", "news", "today match", "cricket"]
    is_sports_news = any(kw in msg_lower for kw in sports_news_keywords)

    try:
        if is_math_complex and deepseek_client:
            logging.info("Routing query to DeepSeek R1 (Complex Math/Reasoning)...")
            res = deepseek_client.chat.completions.create(model="deepseek-reasoner", messages=messages_payload, temperature=0.6, max_tokens=1500)
            if res.choices: ai_reply = res.choices[0].message.content

        elif is_sports_news and grok_client:
            logging.info("Routing query to Grok (Live/Real-time Trends)...")
            res = grok_client.chat.completions.create(model="grok-2-latest", messages=messages_payload, temperature=0.7, max_tokens=1024)
            if res.choices: ai_reply = res.choices[0].message.content

        if not ai_reply and groq_client:
            logging.info("Routing query to Groq (Primary Educational Engine)...")
            res = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages_payload, temperature=0.7, max_tokens=1024)
            if res.choices: ai_reply = res.choices[0].message.content

    except Exception as primary_err:
        logging.warning(f"Primary routed model error: {primary_err}. Triggering Fallback Chain...")

    if not ai_reply:
        for client, model_name in [(openai_client, "gpt-4o-mini"), (deepseek_client, "deepseek-reasoner"), (grok_client, "grok-2-latest")]:
            if client:
                try:
                    res = client.chat.completions.create(model=model_name, messages=messages_payload, temperature=0.7, max_tokens=1024)
                    if res.choices:
                        ai_reply = res.choices[0].message.content
                        break
                except Exception:
                    continue

    if not ai_reply:
        ai_reply = "Maaf karna, abhi sabhi AI servers busy hain. Kripya thodi der baad try karein."

    return ai_reply

# --- Routes & Endpoints ---

@app.route("/")
def home():
    if "user_session_id" not in session:
        session["user_session_id"] = str(uuid.uuid4())
    return render_template("index.html", firebase_api_key=FIREBASE_API_KEY)

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "app": "Aero Guide AI Complete Education Platform"}), 200

@app.route("/ask", methods=["POST"])
def ask_ai():
    try:
        data = request.get_json(silent=True) or {}
        raw_message = data.get("message", "")
        user_message = sanitize_input(raw_message)

        if not user_message:
            return jsonify({"reply": "Kripya ek valid message likhein."})

        session_id = data.get("user_id") or session.get("user_session_id", "default_user")

        weather_keywords = ["weather", "mausam", "temperature", "taapman", "rain", "barish", "garmi", "sardi"]
        is_weather = any(word in user_message.lower() for word in weather_keywords)
        live_weather_info = get_live_weather(user_message) if is_weather else ""
        search_context = search_web_deep(user_message)

        context_parts = []
        if live_weather_info:
            context_parts.append(f"📌 {live_weather_info}")
        if search_context:
            context_parts.append(f"📌 LIVE SEARCH CONTEXT:\n{search_context}")

        if context_parts:
            enhanced_message = f"[SYSTEM CONTEXT]\n" + "\n".join(context_parts) + f"\n\nUser Question: {user_message}"
        else:
            enhanced_message = user_message

        chat_history = get_db_history(session_id, limit=10)
        ai_payload = [{"role": "system", "content": get_system_instruction()}]
        ai_payload.extend(chat_history)
        ai_payload.append({"role": "user", "content": enhanced_message})

        ai_reply = call_ai_model_smart_routing(user_message, ai_payload)

        save_to_db(session_id, "user", user_message)
        save_to_db(session_id, "assistant", ai_reply)

        return jsonify({"reply": ai_reply})

    except Exception as e:
        logging.error(f"Server Error in /ask: {e}")
        return jsonify({"reply": "Maaf karna, server par thodi dikkat aa rahi hai."}), 500

@app.route("/generate_image", methods=["POST"])
def generate_image():
    """AI Image Generation via OpenAI DALL-E 3"""
    try:
        data = request.get_json(silent=True) or {}
        prompt = sanitize_input(data.get("prompt", ""))
        
        if not prompt:
            return jsonify({"error": "Prompt is required"}), 400
            
        if not openai_client:
            return jsonify({"error": "OpenAI client not initialized. Check API Key."}), 500
            
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        return jsonify({"image_url": image_url})
    except Exception as e:
        logging.error(f"Error in /generate_image: {e}")
        return jsonify({"error": "Image generate karne mein dikkat aayi."}), 500

@app.route("/generate_quiz", methods=["POST"])
def generate_quiz():
    """Instant MCQ Quiz Generator"""
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
            {"role": "user", "content": prompt}
        ]
        quiz_text = call_ai_model_smart_routing(topic, ai_payload)
        return jsonify({"quiz": quiz_text})
    except Exception as e:
        logging.error(f"Error in /generate_quiz: {e}")
        return jsonify({"error": "Quiz generate karne mein dikkat aayi."}), 500

@app.route("/get_notes", methods=["POST"])
def get_notes():
    """Quick Revision & Formula Sheet Generator"""
    try:
        data = request.get_json(silent=True) or {}
        subject = sanitize_input(data.get("subject", "Class 12"))
        
        prompt = (
            f"Subject/Chapter '{subject}' ke Important Formulas, Key Terms, aur Quick Revision Notes "
            "ekdam clean bullet points, bold headings aur visual boxes ke format mein generate karo."
        )
        ai_payload = [
            {"role": "system", "content": get_system_instruction()},
            {"role": "user", "content": prompt}
        ]
        notes_text = call_ai_model_smart_routing(subject, ai_payload)
        return jsonify({"notes": notes_text})
    except Exception as e:
        logging.error(f"Error in /get_notes: {e}")
        return jsonify({"error": "Notes generate karne mein dikkat aayi."}), 500

@app.route("/generate_plan", methods=["POST"])
def generate_plan():
    """Daily/Weekly Study Timetable Generator"""
    try:
        data = request.get_json(silent=True) or {}
        exam = sanitize_input(data.get("exam", "CUET & Class 12 Boards"))
        hours = sanitize_input(data.get("hours", "4 hours"))
        
        prompt = (
            f"Target Exam: {exam}. Daily Study Time: {hours}.\n"
            "Ek highly practical, realistic daily timetable aur subject-wise strategy plan bana kar do "
            "jisme Revision Slots aur Short Breaks (Pomodoro) included hon."
        )
        ai_payload = [
            {"role": "system", "content": get_system_instruction()},
            {"role": "user", "content": prompt}
        ]
        plan_text = call_ai_model_smart_routing(exam, ai_payload)
        return jsonify({"plan": plan_text})
    except Exception as e:
        logging.error(f"Error in /generate_plan: {e}")
        return jsonify({"error": "Timetable generate karne mein dikkat aayi."}), 500

@app.route("/reset", methods=["POST"])
def reset_memory():
    data = request.get_json(silent=True) or {}
    session_id = data.get("user_id") or session.get("user_session_id", "default_user")
    clear_db_history(session_id)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
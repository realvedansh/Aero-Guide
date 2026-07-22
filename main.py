from flask import Flask, render_template, request, jsonify
from google import genai

app = Flask(__name__)

# 🔑 Apni Gemini API Key yahan daalo
GEMINI_API_KEY = "AQ.Ab8RN6IsMeuxnJzz6gSx3JV5EDDrmuZ9kFh1AHvKrKHJkOjifg"
client = genai.Client(api_key=GEMINI_API_KEY)

# 🔒 Secure Website Login Details
VALID_USERNAME = "vedansh"
VALID_PASSWORD = "vedbotopen2026"

@app.route("/")
def home():
    # Ye user ko pehle login page dikhayega
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login_verify():
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    
    if username == VALID_USERNAME and password == VALID_PASSWORD:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "Galat Username ya Password hai bhau! ❌"})

@app.route("/ask", methods=["POST"])
def ask_ai():
    user_message = request.json.get("message", "")
    
    if not user_message:
        return jsonify({"reply": "Kuch likho toh sahi bhai!"})
    
    ai_name = "Aero Guide 🚀"
    my_name = "Vedansh Tiwari"
    
    try:
        # 🎯 Aero Guide Ka Custom Professional System Prompt
        system_instruction = (
            f"Tum Aero Guide 🚀 ho, ek professional aur highly disciplined Indian Career Mentor aur Counselor ho. "
            f"Tumhe {my_name} ne banaya hai. Agar student tumse pehli baar baat kar raha hai, toh uska bohot energetic "
            f"andaaz me Hinglish me swagat karo aur unse ye 3 basic cheezein poocho:\n"
            f"1. Tera stream kya hai? (e.g., PCM, PCB, Commerce)\n"
            f"2. Kaun se exams target kar rahe ho? (e.g., CUET, IPMAT, DNS, IMU-CET)\n"
            f"3. Roz kitne ghante self-study ko de sakte ho?\n"
            f"Hamesha ekdam motivated, career-focused aur supportive tone me baat karna. Messages ko chhota aur clear rakhna."
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_message,
            config={'system_instruction': system_instruction}
        )
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"[{ai_name}]: Server thoda heavy chal raha hai, dobara try kar!"})

if __name__ == "__main__":
    app.run(port=5000, debug=True)
import os
import requests
import fitz  # PyMuPDF
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# --- NASTAVENÍ DATABÁZE ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///test.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODEL DATABÁZE ---
class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50))
    filename = db.Column(db.String(255))
    score = db.Column(db.Integer)
    total_questions = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# Načtení klíče z proměnných prostředí (bezpečné)
AUTH_KEY = os.environ.get("AUTH_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    # Frontend už klíč neposílá, komunikace probíhá mezi tvým serverem a AI
    file = request.files.get("file")
    if not file or not AUTH_KEY:
        return jsonify({"error": "Chybí soubor nebo konfigurace"}), 400

    content = ""
    try:
        file_stream = file.read()
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=file_stream, filetype="pdf")
            for page in doc:
                content += page.get_text()
        else:
            content = file_stream.decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"Chyba zpracování: {e}"}), 400

    prompt = f"""Z textu vytvoř přesně 10 kartiček a 10 testových otázek. 
    Výstup musí být POUZE validní JSON.
    {{
      "flashcards": [{{"q": "otázka", "a": "odpověď"}}],
      "quiz": [{{"q": "otázka", "options": ["A", "B", "C", "D"], "correct": "správná_možnost"}}]
    }}
    Text: {content[:3800]}"""

    try:
        url = OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
        payload = {
            "model": "gemma3:27b",
            "messages": [
                {"role": "system", "content": "Jsi JSON generátor."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1
        }
        res = requests.post(url, json=payload, headers={"Authorization": f"Bearer {AUTH_KEY}"}, timeout=120)
        raw_content = res.json()["choices"][0]["message"]["content"]
        clean_json = raw_content.replace("```json", "").replace("```", "").strip()
        return jsonify({"result": clean_json})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/save_score", methods=["POST"])
def save_score():
    data = request.json
    new_record = UserSession(
        ip_address=request.remote_addr,
        filename=data.get('filename'),
        score=data.get('score'),
        total_questions=data.get('total')
    )
    db.session.add(new_record)
    db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
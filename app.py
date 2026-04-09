import os
import requests
import fitz  # PyMuPDF
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# --- OPRAVA CESTY A PŘIPOJENÍ ---
db_url = os.environ.get("DATABASE_URL")

if db_url:
    print(f"DEBUG: Připojuji se k PostgreSQL: {db_url}")
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # Pokud Postgres není, použijeme SQLite v /tmp/ (tam to vždycky projde)
    print("DEBUG: DATABASE_URL nenalezena, používám nouzové SQLite v /tmp/")
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:////tmp/maturita_emergency.db"

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

# Vytvoření tabulek (s ošetřením chyb)
try:
    with app.app_context():
        db.create_all()
        print("DEBUG: Databáze úspěšně připravena.")
except Exception as e:
    print(f"CHYBA DATABÁZE: {e}")

AUTH_KEY = os.environ.get("AUTH_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file or not AUTH_KEY:
        return jsonify({"error": "Chybí soubor nebo klíč"}), 400

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
        return jsonify({"error": str(e)}), 400

    prompt = f"Vytvoř 10 kartiček a 10 testových otázek z textu: {content[:3500]}"

    try:
        res = requests.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [
                    {"role": "system", "content": "Jsi JSON generátor."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=120
        )
        raw_content = res.json()["choices"][0]["message"]["content"]
        clean_json = raw_content.replace("```json", "").replace("```", "").strip()
        return jsonify({"result": clean_json})
    except Exception as e:
        return jsonify({"error": "AI fail"}), 500

@app.route("/save_score", methods=["POST"])
def save_score():
    data = request.json
    try:
        new_record = UserSession(
            ip_address=request.remote_addr,
            filename=data.get('filename'),
            score=data.get('score'),
            total_questions=data.get('total')
        )
        db.session.add(new_record)
        db.session.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Chyba ukládání: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

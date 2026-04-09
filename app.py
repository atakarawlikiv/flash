import os
import requests
import fitz
import json
import re
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# --- DATABÁZE ---
# Prioritně Postgres z Dockeru, jinak SQLite v /tmp (pro zápis bez chyb)
db_url = os.environ.get("DATABASE_URL", "sqlite:////tmp/maturita.db")
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255))
    score = db.Column(db.Integer)
    total_questions = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# --- KONFIGURACE AI ---
AUTH_KEY = os.environ.get("AUTH_KEY")
API_URL = "https://kurim.ithope.eu/v1/chat/completions"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file or not AUTH_KEY:
        return jsonify({"error": "Chybí soubor nebo klíč v .env"}), 400

    try:
        # 1. Extrakce textu z PDF/TXT
        content = ""
        file_bytes = file.read()
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page in doc: content += page.get_text()
        else:
            content = file_bytes.decode("utf-8")
        
        # 2. Dotaz na AI
        prompt = f"Z textu vytvoř 10 kartiček a 10 testových otázek v JSONu. Text: {content[:3500]}"
        
        res = requests.post(API_URL, json={
            "model": "gemma3:27b",
            "messages": [
                {"role": "system", "content": "Jsi JSON generátor. Odpovídej VŽDY A POUZE čistým JSONem bez keců."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1
        }, headers={"Authorization": f"Bearer {AUTH_KEY}"}, timeout=120)

        raw_data = res.json()["choices"][0]["message"]["content"]
        
        # 3. ČIŠTĚNÍ JSONU (Klíčové pro funkčnost)
        # Odstraní ```json ... ``` a všechno okolo
        clean_json = re.search(r'\{.*\}', raw_data, re.DOTALL)
        if clean_json:
            return jsonify({"result": clean_json.group()})
        else:
            return jsonify({"result": raw_data}) # Nouzovka

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/save_score", methods=["POST"])
def save_score():
    data = request.json
    try:
        rec = UserSession(filename=data.get('filename'), score=data.get('score'), total_questions=data.get('total'))
        db.session.add(rec)
        db.session.commit()
        return jsonify({"status": "ok"})
    except:
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

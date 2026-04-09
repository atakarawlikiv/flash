import os
import requests
import fitz
import re
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# Databáze - opraveno pro Docker i lokál
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

# Konfigurace AI z Dockeru
AUTH_KEY = os.environ.get("AUTH_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1").rstrip('/')

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    # Oprava: Kontrola souboru v requestu
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        content = ""
        f_bytes = file.read()
        
        # Oprava čtení PDF
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=f_bytes, filetype="pdf")
            for page in doc:
                content += page.get_text()
        else:
            content = f_bytes.decode("utf-8")

        prompt = f"Vytvoř 10 kartiček a 10 otázek jako JSON. Text: {content[:3500]}"
        
        # Oprava: Správné volání API s hlavičkami
        res = requests.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [
                    {"role": "system", "content": "Jsi JSON stroj. Odpovídej jen čistým JSONEM."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=120
        )

        # Čištění JSONu od případných keců AI
        raw_content = res.json()["choices"][0]["message"]["content"]
        clean_json = re.search(r'\{.*\}', raw_content, re.DOTALL)
        
        return jsonify({"result": clean_json.group() if clean_json else raw_content})

    except Exception as e:
        print(f"ERROR: {str(e)}")
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

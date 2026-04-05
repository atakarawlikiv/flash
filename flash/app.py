import os
import requests
import fitz  # PyMuPDF
from flask import Flask, request, render_template, jsonify

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Klíč ze screenshotu (pro tvůj server)
AUTH_KEY = "sk-0MlocXvcIJNS9usp-OlaAg"
# URL Olammy (Gemma)
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    # Kontrola autorizace
    if request.headers.get("x-api-key") != AUTH_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Chybí soubor"}), 400

    # Uložení a čtení
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    content = ""
    try:
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(filepath)
            for page in doc:
                content += page.get_text()
        else:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
    except Exception as e:
        return jsonify({"error": f"Chyba čtení: {e}"}), 400

    # Prompt pro Gemmu
    prompt = f"Vytvoř JSON (flashcards: [{{q,a}}], quiz: [{{q,options,correct}}]) z textu: {content[:3500]}"

    try:
        url = OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
        payload = {
            "model": "gemma3:27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }
        headers = {
            "Authorization": f"Bearer {AUTH_KEY}",
            "Content-Type": "application/json"
        }
        res = requests.post(url, json=payload, headers=headers, timeout=120)
        
        # Vrátíme výsledek AI
        return jsonify({"result": res.json()["choices"][0]["message"]["content"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
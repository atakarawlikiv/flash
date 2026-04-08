import os
import requests
import fitz  # PyMuPDF
from flask import Flask, request, render_template, jsonify
import io

app = Flask(__name__)

# Klíč ze screenshotu
AUTH_KEY = "sk-0MlocXvcIJNS9usp-OlaAg"
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    if request.headers.get("x-api-key") != AUTH_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Chybí soubor"}), 400

    content = ""
    try:
        # Čteme soubor přímo z paměti (stream)
        file_stream = file.read()
        
        if file.filename.lower().endswith(".pdf"):
            # Otevřeme PDF z paměti bez ukládání na disk
            doc = fitz.open(stream=file_stream, filetype="pdf")
            for page in doc:
                content += page.get_text()
        else:
            # Textový soubor převedeme na string
            content = file_stream.decode("utf-8")
            
    except Exception as e:
        return jsonify({"error": f"Chyba zpracování: {e}"}), 400

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
        return jsonify({"result": res.json()["choices"][0]["message"]["content"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

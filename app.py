import os
import requests
import fitz  # PyMuPDF
from flask import Flask, request, render_template, jsonify

app = Flask(__name__)

# DOPORUČENÍ: V produkci klíč schovej do proměnné prostředí
AUTH_KEY = "sk-0MlocXvcIJNS9usp-OlaAg"
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "[https://kurim.ithope.eu/v1](https://kurim.ithope.eu/v1)")

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
        file_stream = file.read()
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=file_stream, filetype="pdf")
            for page in doc:
                content += page.get_text()
        else:
            content = file_stream.decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"Chyba zpracování: {e}"}), 400

    # Čistý prompt pro stabilní JSON
    prompt = f"""Z textu vytvoř přesně 10 kartiček a 10 testových otázek. 
    Výstup musí být POUZE validní JSON bez doprovodného textu.
    Struktura:
    {{
      "flashcards": [{{"q": "otázka", "a": "odpověď"}}],
      "quiz": [{{"q": "otázka", "options": ["volba1", "volba2", "volba3", "volba4"], "correct": "přesná_textová_shoda_volby"}}]
    }}
    Text: {content[:3800]}"""

    try:
        url = OPENAI_BASE_URL.rstrip("/") + "/chat/completions"
        payload = {
            "model": "gemma3:27b",
            "messages": [
                {"role": "system", "content": "Jsi JSON generátor. Odpovídáš pouze ve formátu JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1
        }
        headers = {
            "Authorization": f"Bearer {AUTH_KEY}",
            "Content-Type": "application/json"
        }
        
        res = requests.post(url, json=payload, headers=headers, timeout=120)
        res_data = res.json()
        
        # Ošetření výstupu (odstranění ```json a dalších nečistot)
        raw_content = res_data["choices"][0]["message"]["content"]
        clean_json = raw_content.replace("```json", "").replace("```", "").strip()
        
        return jsonify({"result": clean_json})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

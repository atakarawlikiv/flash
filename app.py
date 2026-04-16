import os
import time
import requests
import fitz
import re
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# Databáze — PostgreSQL na serveru, SQLite lokálně
db_url = os.environ.get("DATABASE_URL", "sqlite:////data/maturita.db")
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class UzivatelskaSezeni(db.Model):
    __tablename__ = "uzivatelska_sezeni"
    id          = db.Column(db.Integer, primary_key=True)
    prezdivka   = db.Column(db.String(80), default="Anonym")   # NOVÉ: přezdívka uživatele
    nazev_souboru = db.Column(db.String(255))
    skore       = db.Column(db.Integer)
    celkem_otazek = db.Column(db.Integer)
    vytvoreno   = db.Column(db.DateTime, default=datetime.utcnow)

# Počkej na databázi (PostgreSQL startuje pomaleji)
def cekej_na_db():
    for i in range(10):
        try:
            with app.app_context():
                db.create_all()
            print("Databáze připojena!")
            return
        except Exception as e:
            print(f"Čekám na databázi... pokus {i+1}: {e}")
            time.sleep(2)
    print("CHYBA: Nepodařilo se připojit k databázi.")

cekej_na_db()

# Konfigurace AI
AUTH_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1").rstrip("/")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def nahrat():
    if "file" not in request.files:
        return jsonify({"error": "Žádný soubor nebyl odeslán"}), 400

    soubor = request.files["file"]
    if soubor.filename == "":
        return jsonify({"error": "Nevybrán žádný soubor"}), 400

    try:
        obsah = ""
        bajty = soubor.read()

        if soubor.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=bajty, filetype="pdf")
            for stranka in doc:
                obsah += stranka.get_text()
            doc.close()
        else:
            obsah = bajty.decode("utf-8", errors="ignore")

        if not obsah.strip():
            return jsonify({"error": "Soubor je prázdný nebo nečitelný"}), 400

        obsah_zkraceny = obsah[:2000]

        prompt = (
            "Z textu níže vytvoř PŘESNĚ 5 kartiček a 5 testových otázek.\n"
            "Vrať POUZE tento JSON, nic jiného:\n"
            '{"flashcards":[{"q":"...","a":"..."}],'
            '"quiz":[{"q":"...","options":["A) ...","B) ...","C) ...","D) ..."],"correct":"A) ..."}]}\n\n'
            f"Text: {obsah_zkraceny}"
        )

        print(f"Odesílám požadavek na AI... ({len(obsah_zkraceny)} znaků)")

        odpoved = requests.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [
                    {"role": "system", "content": "Jsi JSON generátor. Odpovídej POUZE surowym JSON objektem bez markdownu."},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}", "Content-Type": "application/json"},
            timeout=180,
            verify=False,
        )

        print(f"AI odpověděla se stavem: {odpoved.status_code}")
        odpoved.raise_for_status()

        raw = odpoved.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*",     "", raw).strip()

        nalezeno = re.search(r"\{.*\}", raw, re.DOTALL)
        if not nalezeno:
            print(f"AI vrátila neplatný formát: {raw[:300]}")
            return jsonify({"error": "AI vrátila neplatný formát dat"}), 500

        return jsonify({"result": nalezeno.group()})

    except requests.exceptions.Timeout:
        return jsonify({"error": "AI server neodpovídá (timeout). Zkus to znovu."}), 504
    except requests.exceptions.RequestException as e:
        print(f"Chyba při volání AI: {e}")
        return jsonify({"error": f"Chyba při komunikaci s AI: {str(e)}"}), 502
    except Exception as e:
        print(f"Neočekávaná chyba: {e}")
        return jsonify({"error": f"Chyba: {str(e)}"}), 500


@app.route("/save_score", methods=["POST"])
def ulozit_skore():
    data = request.json
    try:
        zaznam = UzivatelskaSezeni(
            prezdivka     = data.get("prezdivka", "Anonym")[:80],
            nazev_souboru = data.get("filename"),
            skore         = data.get("score"),
            celkem_otazek = data.get("total"),
        )
        db.session.add(zaznam)
        db.session.commit()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Chyba při ukládání skóre: {e}")
        return jsonify({"status": "chyba"}), 500


@app.route("/statistiky")
def statistiky():
    zaznamy = UzivatelskaSezeni.query.order_by(UzivatelskaSezeni.vytvoreno.desc()).limit(50).all()
    vysledky = [
        {
            "prezdivka": z.prezdivka,
            "soubor":    z.nazev_souboru,
            "skore":     z.skore,
            "celkem":    z.celkem_otazek,
            "datum":     z.vytvoreno.strftime("%d.%m.%Y %H:%M"),
        }
        for z in zaznamy
    ]
    return jsonify(vysledky)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

import os
import time
import requests
import fitz
import re
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import text

app = Flask(__name__)

# ✅ bezpečnější DB path
db_url = os.environ.get("DATABASE_URL") or "sqlite:///maturita.db"
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class UzivatelskaSezeni(db.Model):
    __tablename__ = "uzivatelska_sezeni"
    id = db.Column(db.Integer, primary_key=True)
    prezdivka = db.Column(db.String(80), default="Anonym")
    nazev_souboru = db.Column(db.String(255))
    skore = db.Column(db.Integer)
    celkem_otazek = db.Column(db.Integer)
    vytvoreno = db.Column(db.DateTime, default=datetime.utcnow)


# ✅ AUTO FIX SCHEMA (žádné migrace potřeba)
def oprav_db():
    with app.app_context():
        db.create_all()

        try:
            # zkusíme přidat sloupec (pokud existuje → fail ignorujeme)
            db.session.execute(text(
                "ALTER TABLE uzivatelska_sezeni ADD COLUMN prezdivka VARCHAR(80)"
            ))
            db.session.commit()
            print("✔ Přidán sloupec prezdivka")
        except Exception:
            print("✔ Sloupec už existuje (OK)")


oprav_db()


# ================= AI =================
AUTH_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1").rstrip("/")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def nahrat():
    if "file" not in request.files:
        return jsonify({"error": "Žádný soubor"}), 400

    soubor = request.files["file"]

    try:
        bajty = soubor.read()

        if soubor.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=bajty, filetype="pdf")
            obsah = "".join([p.get_text() for p in doc])
            doc.close()
        else:
            obsah = bajty.decode("utf-8", errors="ignore")

        if not obsah.strip():
            return jsonify({"error": "Prázdný soubor"}), 400

        obsah = obsah[:4000]  # víc textu = lepší výstup

        # 🔥 ZDE JE ZMĚNA NA 15
        prompt = f"""
Z textu vytvoř:
- PŘESNĚ 15 flashcards
- PŘESNĚ 15 testových otázek

Formát JSON:
{{
 "flashcards":[{{"q":"...","a":"..."}}],
 "quiz":[{{"q":"...","options":["A) ...","B) ...","C) ...","D) ..."],"correct":"A) ..."}}]
}}

Text:
{obsah}
"""

        r = requests.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [
                    {"role": "system", "content": "Vracíš pouze čistý JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 3000,
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=180,
            verify=False,
        )

        r.raise_for_status()

        raw = r.json()["choices"][0]["message"]["content"]

        # cleanup
        raw = re.sub(r"```.*?```", "", raw, flags=re.DOTALL)
        raw = raw.strip()

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return jsonify({"error": "AI vrátila blbost"}), 500

        return jsonify({"result": match.group()})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/save_score", methods=["POST"])
def ulozit():
    try:
        d = request.json

        z = UzivatelskaSezeni(
            prezdivka=d.get("prezdivka", "Anonym")[:80],
            nazev_souboru=d.get("filename"),
            skore=d.get("score"),
            celkem_otazek=d.get("total"),
        )

        db.session.add(z)
        db.session.commit()

        return jsonify({"ok": True})

    except Exception as e:
        print("SAVE ERROR:", e)
        return jsonify({"error": "DB chyba"}), 500


@app.route("/statistiky")
def statistiky():
    try:
        zaznamy = UzivatelskaSezeni.query.order_by(
            UzivatelskaSezeni.vytvoreno.desc()
        ).limit(50).all()

        return jsonify([
            {
                "prezdivka": z.prezdivka,
                "soubor": z.nazev_souboru,
                "skore": z.skore,
                "celkem": z.celkem_otazek,
                "datum": z.vytvoreno.strftime("%d.%m.%Y %H:%M"),
            }
            for z in zaznamy
        ])

    except Exception as e:
        print("STAT ERROR:", e)
        return jsonify([]), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

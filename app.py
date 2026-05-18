import os
import time
import requests
import fitz
import re
import json
import urllib3
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import text

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, instance_path="/tmp")

# DB
db_url = os.environ.get("DATABASE_URL") or "sqlite:////tmp/maturita.db"
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ================= MODELY =================

class UzivatelskaSezeni(db.Model):
    __tablename__ = "uzivatelska_sezeni"
    id = db.Column(db.Integer, primary_key=True)
    prezdivka = db.Column(db.String(80), default="Anonym")
    nazev_souboru = db.Column(db.String(255))
    skore = db.Column(db.Integer)
    celkem_otazek = db.Column(db.Integer)
    vytvoreno = db.Column(db.DateTime, default=datetime.utcnow)


class HistorieMaterialu(db.Model):
    __tablename__ = "historie_materialu"
    id = db.Column(db.Integer, primary_key=True)
    prezdivka = db.Column(db.String(80))
    nazev_souboru = db.Column(db.String(255))
    flashcards = db.Column(db.Text)
    quiz = db.Column(db.Text)
    vytvoreno = db.Column(db.DateTime, default=datetime.utcnow)


# ================= DB FIX =================

def oprav_db():
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(text(
                "ALTER TABLE uzivatelska_sezeni ADD COLUMN prezdivka VARCHAR(80)"
            ))
            db.session.commit()
        except:
            pass

oprav_db()


# ================= AI =================

AUTH_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1").rstrip("/")


@app.route("/")
def index():
    return render_template("index.html")


# ================= UPLOAD =================

@app.route("/upload", methods=["POST"])
def nahrat():
    soubor = request.files.get("file")
    if not soubor:
        return jsonify({"error": "Žádný soubor"}), 400

    prezdivka = request.form.get("prezdivka", "Anonym")

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

        obsah = obsah[:4000]

        prompt = f"""
Vytvoř:
- PŘESNĚ 15 flashcards
- PŘESNĚ 15 testových otázek

JSON:
{{
 "flashcards":[{{"q":"...","a":"..."}}],
 "quiz":[{{"q":"...","options":["A) ...","B) ...","C) ...","D) ..."],"correct":"A) ..."}}]
}}

Text:
{obsah}
"""

        try:
            r = requests.post(
                f"{BASE_URL}/chat/completions",
                json={
                    "model": "gemma3:27b",
                    "messages": [
                        {"role": "system", "content": "Vracíš pouze JSON."},
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
        except requests.exceptions.ConnectionError as e:
            print("AI CONNECTION ERROR:", e)
            return jsonify({"error": f"Nelze se připojit k AI serveru ({BASE_URL}). Zkontroluj OPENAI_BASE_URL."}), 503
        except requests.exceptions.Timeout:
            return jsonify({"error": "AI server neodpověděl včas (timeout 180s). Zkus to znovu."}), 504
        except requests.exceptions.HTTPError as e:
            print("AI HTTP ERROR:", e, r.text[:300] if r else "")
            return jsonify({"error": f"AI server vrátil chybu: {r.status_code}. {r.text[:200]}"}), 502

        try:
            raw = r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            print("AI PARSE ERROR:", e, r.text[:300])
            return jsonify({"error": "AI vrátila neočekávaný formát odpovědi."}), 500

        raw = re.sub(r"```(?:json)?", "", raw)
        raw = re.sub(r"```", "", raw)
        match = re.search(r"\{.*\}", raw, re.DOTALL)

        if not match:
            print("NO JSON IN RESPONSE:", raw[:300])
            return jsonify({"error": "AI nevrátila JSON. Zkus to znovu."}), 500

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as e:
            print("JSON DECODE ERROR:", e, match.group()[:300])
            return jsonify({"error": "AI vrátila poškozený JSON. Zkus to znovu."}), 500

        # ===== VYNUTIT 15 =====
        def fix(arr, typ):
            if len(arr) > 15:
                return arr[:15]
            while len(arr) < 15:
                if typ == "f":
                    arr.append({"q": "Doplň otázku", "a": "Doplň odpověď"})
                else:
                    arr.append({
                        "q": "Doplň otázku",
                        "options": ["A) ...","B) ...","C) ...","D) ..."],
                        "correct": "A) ..."
                    })
            return arr

        data["flashcards"] = fix(data.get("flashcards", []), "f")
        data["quiz"]       = fix(data.get("quiz", []), "q")

        # ===== ULOŽ HISTORII =====
        try:
            z = HistorieMaterialu(
                prezdivka=prezdivka[:80],
                nazev_souboru=soubor.filename,
                flashcards=json.dumps(data["flashcards"]),
                quiz=json.dumps(data["quiz"])
            )
            db.session.add(z)
            db.session.commit()
        except Exception as e:
            print("HISTORIE ERROR:", e)

        return jsonify({"result": json.dumps(data)})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"error": str(e)}), 500


# ================= STATISTIKY =================

@app.route("/statistiky")
def statistiky():
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


# ================= HISTORIE =================

@app.route("/historie")
def historie():
    zaznamy = HistorieMaterialu.query.order_by(
        HistorieMaterialu.vytvoreno.desc()
    ).limit(20).all()

    return jsonify([
        {
            "id": z.id,
            "prezdivka": z.prezdivka,
            "soubor": z.nazev_souboru,
            "datum": z.vytvoreno.strftime("%d.%m.%Y %H:%M")
        }
        for z in zaznamy
    ])


@app.route("/historie/<int:id>")
def historie_detail(id):
    z = HistorieMaterialu.query.get(id)
    if not z:
        return jsonify({"error": "nenalezeno"}), 404

    return jsonify({
        "flashcards": json.loads(z.flashcards),
        "quiz": json.loads(z.quiz)
    })


# ================= SAVE SCORE =================

@app.route("/save_score", methods=["POST"])
def ulozit():
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

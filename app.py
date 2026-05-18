import os
import re
import json
import uuid
import threading
import urllib3
import requests
import fitz
from flask import Flask, request, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import text

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, instance_path="/tmp")

db_url = os.environ.get("DATABASE_URL") or "sqlite:////tmp/maturita.db"
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

AUTH_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://kurim.ithope.eu/v1").rstrip("/")

# In-memory job store  {job_id: {"status": "pending"|"done"|"error", "result": ..., "error": ...}}
jobs: dict = {}
jobs_lock = threading.Lock()


# ================= MODELY =================

class UzivatelskaSezeni(db.Model):
    __tablename__ = "uzivatelska_sezeni"
    id            = db.Column(db.Integer, primary_key=True)
    prezdivka     = db.Column(db.String(80), default="Anonym")
    nazev_souboru = db.Column(db.String(255))
    skore         = db.Column(db.Integer)
    celkem_otazek = db.Column(db.Integer)
    vytvoreno     = db.Column(db.DateTime, default=datetime.utcnow)


class HistorieMaterialu(db.Model):
    __tablename__ = "historie_materialu"
    id            = db.Column(db.Integer, primary_key=True)
    prezdivka     = db.Column(db.String(80))
    nazev_souboru = db.Column(db.String(255))
    flashcards    = db.Column(db.Text)
    quiz          = db.Column(db.Text)
    vytvoreno     = db.Column(db.DateTime, default=datetime.utcnow)


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


# ================= POMOCNÉ FUNKCE =================

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


def volej_ai(job_id: str, obsah: str, prezdivka: str, nazev_souboru: str):
    """Běží v samostatném vlákně — nginx timeout nás nezabije."""
    prompt = f"""Vytvoř PŘESNĚ 15 flashcards a PŘESNĚ 15 testových otázek.
Vrať POUZE validní JSON, žádný jiný text:
{{
 "flashcards":[{{"q":"...","a":"..."}}],
 "quiz":[{{"q":"...","options":["A) ...","B) ...","C) ...","D) ..."],"correct":"A) ..."}}]
}}

Text:
{obsah}"""

    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [
                    {"role": "system", "content": "Vracíš POUZE JSON bez jakéhokoliv dalšího textu."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 3000,
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=300,
            verify=False,
        )
        r.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        print("AI CONNECTION ERROR:", e)
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Nelze se připojit k AI serveru ({BASE_URL})."}
        return
    except requests.exceptions.Timeout:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": "AI server neodpověděl (timeout). Zkus to znovu."}
        return
    except requests.exceptions.HTTPError as e:
        print("AI HTTP ERROR:", e, r.text[:300])
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"AI server vrátil HTTP chybu {r.status_code}: {r.text[:200]}"}
        return

    # Parsování odpovědi
    try:
        raw = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("AI PARSE ERROR:", e, r.text[:300])
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"AI vrátila neočekávaný formát. Raw: {r.text[:200]}"}
        return

    raw = re.sub(r"```(?:json)?", "", raw)
    raw = re.sub(r"```", "", raw)
    match = re.search(r"\{.*\}", raw, re.DOTALL)

    if not match:
        print("NO JSON:", raw[:300])
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"AI nevrátila JSON. Odpověď: {raw[:200]}"}
        return

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        print("JSON DECODE ERROR:", e)
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"AI vrátila poškozený JSON: {str(e)}"}
        return

    data["flashcards"] = fix(data.get("flashcards", []), "f")
    data["quiz"]       = fix(data.get("quiz", []), "q")

    # Ulož do DB
    try:
        with app.app_context():
            z = HistorieMaterialu(
                prezdivka=prezdivka[:80],
                nazev_souboru=nazev_souboru,
                flashcards=json.dumps(data["flashcards"]),
                quiz=json.dumps(data["quiz"])
            )
            db.session.add(z)
            db.session.commit()
    except Exception as e:
        print("HISTORIE ERROR:", e)

    with jobs_lock:
        jobs[job_id] = {"status": "done", "result": json.dumps(data)}


# ================= ROUTES =================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def nahrat():
    soubor = request.files.get("file")
    if not soubor:
        return jsonify({"error": "Žádný soubor"}), 400

    prezdivka = request.form.get("prezdivka", "Anonym")

    try:
        bajty = soubor.read()
        if soubor.filename.lower().endswith(".pdf"):
            doc   = fitz.open(stream=bajty, filetype="pdf")
            obsah = "".join([p.get_text() for p in doc])
            doc.close()
        else:
            obsah = bajty.decode("utf-8", errors="ignore")

        if not obsah.strip():
            return jsonify({"error": "Prázdný soubor"}), 400

        obsah = obsah[:4000]

    except Exception as e:
        return jsonify({"error": f"Chyba při čtení souboru: {e}"}), 500

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending"}

    t = threading.Thread(
        target=volej_ai,
        args=(job_id, obsah, prezdivka, soubor.filename),
        daemon=True
    )
    t.start()

    # Vrátíme job_id okamžitě — nginx nečeká na AI
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


@app.route("/test-ai")
def test_ai():
    """Diagnostický endpoint — otestuje jestli AI server odpovídá."""
    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": "gemma3:27b",
                "messages": [{"role": "user", "content": "Řekni jen: OK"}],
                "max_tokens": 10,
            },
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=30,
            verify=False,
        )
        return jsonify({"http_status": r.status_code, "body": r.text[:500]})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/statistiky")
def statistiky():
    zaznamy = UzivatelskaSezeni.query.order_by(
        UzivatelskaSezeni.vytvoreno.desc()
    ).limit(50).all()
    return jsonify([
        {
            "prezdivka": z.prezdivka,
            "soubor":    z.nazev_souboru,
            "skore":     z.skore,
            "celkem":    z.celkem_otazek,
            "datum":     z.vytvoreno.strftime("%d.%m.%Y %H:%M"),
        }
        for z in zaznamy
    ])


@app.route("/historie")
def historie():
    zaznamy = HistorieMaterialu.query.order_by(
        HistorieMaterialu.vytvoreno.desc()
    ).limit(20).all()
    return jsonify([
        {
            "id":       z.id,
            "prezdivka": z.prezdivka,
            "soubor":   z.nazev_souboru,
            "datum":    z.vytvoreno.strftime("%d.%m.%Y %H:%M")
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
        "quiz":       json.loads(z.quiz)
    })


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

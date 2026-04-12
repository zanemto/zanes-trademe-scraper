"""
TradeMe Scraper — Flask UI
Run with: python app.py
Then open http://localhost:5000
"""

import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "trademe_cars.db"
SCRAPER_FILTERS_PATH = BASE_DIR / "scraper_filters.json"
MAILER_FILTERS_PATH = BASE_DIR / "mailer_filters.json"

jobs = {}  # job_id -> {"output": str, "done": bool, "ok": bool}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def load_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def run_subprocess(job_id, cmd):
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(BASE_DIR),
        )
        output = ""
        for line in proc.stdout:
            output += line
            jobs[job_id]["output"] = output
        proc.wait()
        jobs[job_id]["done"] = True
        jobs[job_id]["ok"] = proc.returncode == 0
    except Exception as e:
        jobs[job_id]["output"] += f"\nError: {e}"
        jobs[job_id]["done"] = True
        jobs[job_id]["ok"] = False


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    try:
        con = get_db()
        total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        today = con.execute(
            "SELECT COUNT(*) FROM listings WHERE date_scraped >= date('now')"
        ).fetchone()[0]
        week = con.execute(
            "SELECT COUNT(*) FROM listings WHERE date_scraped >= date('now', '-7 days')"
        ).fetchone()[0]
        con.close()
    except Exception:
        total = today = week = 0

    scraper_config = load_json(SCRAPER_FILTERS_PATH) or {"max_pages": 10, "filter_sets": []}
    scraper_names = [f.get("name") or f"Filter {i+1}" for i, f in enumerate(scraper_config.get("filter_sets", []))]
    mailer_filters = load_json(MAILER_FILTERS_PATH) or []
    mailer_names = [f.get("name") or f"Filter {i+1}" for i, f in enumerate(mailer_filters)]

    return render_template(
        "index.html",
        total=total, today=today, week=week,
        scraper_names=scraper_names,
        mailer_names=mailer_names,
    )


@app.route("/listings")
def listings():
    page = max(1, int(request.args.get("page", 1)))
    per_page = 50
    offset = (page - 1) * per_page
    try:
        con = get_db()
        total = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        rows = con.execute(
            """
            SELECT listing_id, title, price, kilometres, year, make, model,
                   url, date_scraped, listed_as, region_id
            FROM listings
            ORDER BY date_scraped DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
        con.close()
    except Exception:
        total, rows = 0, []
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("listings.html", rows=rows, page=page, pages=pages, total=total)


@app.route("/filters")
def filters():
    scraper_config = load_json(SCRAPER_FILTERS_PATH) or {"max_pages": 10, "filter_sets": []}
    return render_template(
        "filters.html",
        scraper_config=scraper_config,
        mailer_filters=load_json(MAILER_FILTERS_PATH),
    )


@app.route("/filters/scraper", methods=["POST"])
def save_scraper_filters():
    save_json(SCRAPER_FILTERS_PATH, request.get_json())
    return jsonify({"ok": True})


@app.route("/filters/mailer", methods=["POST"])
def save_mailer_filters():
    save_json(MAILER_FILTERS_PATH, request.get_json())
    return jsonify({"ok": True})


@app.route("/run/scraper", methods=["POST"])
def run_scraper():
    body = request.get_json() or {}
    selected = body.get("filter_names", [])
    cmd = [sys.executable, str(BASE_DIR / "scraper.py")]
    if selected:
        cmd += ["--filters", ",".join(selected)]
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"output": "Starting scraper...\n", "done": False, "ok": False}
    t = threading.Thread(target=run_subprocess, args=(job_id, cmd))
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/run/mailer", methods=["POST"])
def run_mailer():
    body = request.get_json() or {}
    selected = body.get("filter_names", [])
    cmd = [sys.executable, str(BASE_DIR / "mailer.py")]
    if selected:
        cmd += ["--filters", ",".join(selected)]
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"output": "Starting mailer...\n", "done": False, "ok": False}
    t = threading.Thread(target=run_subprocess, args=(job_id, cmd))
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/run/status/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re

app = Flask(__name__)
CORS(app)

# ---------- CONFIGURATION ----------
PMU_API_ENDPOINTS = [
    "https://online.turfinfo.api.pmu.fr/rest/client/61/programme/",
    "https://online.turfinfo.api.pmu.fr/rest/client/1/programme/",
    "https://offline.turfinfo.api.pmu.fr/rest/client/7/programme/",
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
    'Referer': 'https://www.pmu.fr/',
    'Origin': 'https://www.pmu.fr',
}

def today_pmu(offset_days=0):
    date = datetime.now() + timedelta(days=offset_days)
    return date.strftime("%d%m%Y")

def try_fetch_json(url):
    """Essaie de récupérer une URL JSON (direct uniquement, l'API PMU accepte)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Erreur fetch {url}: {e}")
    return None

def fetch_with_fallback(path_suffix, reunion, course=None):
    """
    Essaie plusieurs combinaisons endpoint + date.
    path_suffix : ex: 'participants', 'analyse', 'pronostics', 'combinaisons'
    """
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            date = today_pmu(offset)
            if course:
                url = f"{endpoint}{date}/R{reunion}/C{course}/{path_suffix}"
            else:
                url = f"{endpoint}{date}/R{reunion}/{path_suffix}"
            data = try_fetch_json(url)
            if data:
                return data
    return None

# ---------- PAGE D'ACCUEIL ----------
@app.route('/')
def home():
    return jsonify({
        "status": "ok",
        "message": "Serveur proxy Crazy Cavallo en ligne",
        "endpoints": [
            "/api/programme",
            "/api/participants?reunion=1&course=1",
            "/api/analyse?reunion=1&course=1",
            "/api/pronostics-pmu?reunion=1&course=1",
            "/api/combinaisons?reunion=1&course=1"
        ]
    })

# ---------- 1. PROGRAMME ----------
@app.route('/api/programme')
def get_programme():
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            url = f"{endpoint}{today_pmu(offset)}"
            data = try_fetch_json(url)
            if data and 'programme' in data:
                prog = data['programme']
                if 'reunions' in prog and len(prog['reunions']) > 0:
                    return jsonify(prog)
            elif data and 'reunions' in data and len(data['reunions']) > 0:
                return jsonify(data)
    return jsonify({"error": "Programme indisponible"}), 500

# ---------- 2. PARTICIPANTS ----------
@app.route('/api/participants')
def get_participants():
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_with_fallback('participants', reunion, course)
    if data and 'participants' in data:
        return jsonify(data)
    return jsonify({"error": "Participants indisponibles"}), 500

# ---------- 3. ANALYSE (commentaires + favoris) ----------
@app.route('/api/analyse')
def get_analyse():
    """Analyse presse PMU : commentaires + rangs + cotes probables."""
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_with_fallback('analyse', reunion, course)
    if data and 'selection' in data:
        return jsonify(data)
    return jsonify({"error": "Analyse indisponible", "selection": []}), 200

# ---------- 4. PRONOSTICS PRESSE ----------
@app.route('/api/pronostics-pmu')
def get_pronostics_pmu():
    """Pronostics presse PMU avec sélections ordonnées."""
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_with_fallback('pronostics', reunion, course)
    if data and 'selection' in data:
        return jsonify(data)
    return jsonify({"error": "Pronostics indisponibles", "selection": []}), 200

# ---------- 5. COMBINAISONS (masses d'enjeux réelles) ----------
@app.route('/api/combinaisons')
def get_combinaisons():
    """Masses d'enjeux par pari (Simple, Couplé, Trio, etc.)."""
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_with_fallback('combinaisons', reunion, course)
    if data and 'combinaisons' in data:
        return jsonify(data)
    return jsonify({"error": "Combinaisons indisponibles", "combinaisons": []}), 200

# ---------- 6. PRONOSTICS EQUIDIA (optionnel, peut échouer) ----------
@app.route('/api/pronostics/equidia')
def get_pronos_equidia():
    try:
        url = "https://www.equidia.fr/courses/pronostics"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pronostics = []
        for bloc in soup.select('article'):
            try:
                selection = bloc.select_one('.selection, .picks, .nums')
                course_titre = bloc.select_one('.race-title, h3')
                if selection:
                    pronostics.append({
                        "course": course_titre.get_text(strip=True) if course_titre else "—",
                        "expert": "Équipe Equidia",
                        "selection": selection.get_text(strip=True)
                    })
            except Exception:
                continue
        return jsonify({"pronostics": pronostics})
    except Exception as e:
        return jsonify({"error": str(e), "pronostics": []}), 200

# ---------- LANCEMENT ----------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, threaded=True)

import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import urllib.parse

app = Flask(__name__)
CORS(app)

# ---------- CONFIGURATION ----------
# Endpoints PMU à essayer
PMU_API_ENDPOINTS = [
    "https://online.turfinfo.api.pmu.fr/rest/client/61/programme/",
    "https://online.turfinfo.api.pmu.fr/rest/client/1/programme/",
    "https://offline.turfinfo.api.pmu.fr/rest/client/7/programme/",
]

# Proxys publics pour contourner les blocages d'IP (Render = cloud bloqué par PMU)
PUBLIC_PROXIES = [
    "https://api.allorigins.win/raw?url=",
    "https://corsproxy.io/?",
    "https://api.codetabs.com/v1/proxy?quest=",
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
    'Referer': 'https://www.pmu.fr/',
    'Origin': 'https://www.pmu.fr',
    'X-Requested-With': 'XMLHttpRequest',
}

def today_pmu(offset_days=0):
    """Retourne une date au format YYYYMMDD, avec décalage optionnel."""
    date = datetime.now() + timedelta(days=offset_days)
    return date.strftime("%Y%m%d")

def try_fetch(url):
    """Essaie de récupérer une URL : direct, puis via proxys publics."""
    # Tentative directe
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                pass
    except Exception:
        pass

    # Tentative via proxys publics
    for proxy in PUBLIC_PROXIES:
        try:
            proxy_url = proxy + urllib.parse.quote(url, safe='')
            r = requests.get(proxy_url, timeout=15)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    pass
        except Exception:
            continue

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
            "/api/pronostics/equidia",
            "/api/pronostics/geny",
            "/api/pronostics/turfbzh",
            "/api/pronostics/paristurf"
        ]
    })

# ---------- TEST DE CONNEXION PMU ----------
@app.route('/api/test')
def test_pmu():
    """Endpoint de diagnostic : teste la connexion au PMU."""
    results = []
    for endpoint in PMU_API_ENDPOINTS:
        url = f"{endpoint}{today_pmu(0)}"
        # Test direct
        direct_status = "?"
        try:
            r = requests.get(url, headers=HEADERS, timeout=8)
            direct_status = str(r.status_code)
        except Exception as e:
            direct_status = f"ERR: {str(e)[:50]}"

        # Test via proxy
        proxy_status = "?"
        try:
            proxy_url = PUBLIC_PROXIES[0] + urllib.parse.quote(url, safe='')
            r = requests.get(proxy_url, timeout=12)
            proxy_status = str(r.status_code)
        except Exception as e:
            proxy_status = f"ERR: {str(e)[:50]}"

        results.append({
            "endpoint": endpoint,
            "direct": direct_status,
            "via_proxy": proxy_status
        })
    return jsonify({"tests": results})

# ---------- 1. PROGRAMME DU JOUR ----------
@app.route('/api/programme')
def get_programme():
    """Récupère le programme du jour avec plusieurs tentatives."""
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            url = f"{endpoint}{today_pmu(offset)}"
            data = try_fetch(url)
            if data and 'reunions' in data and len(data.get('reunions', [])) > 0:
                return jsonify(data)
    return jsonify({
        "error": "Impossible de récupérer le programme du jour",
        "details": "Toutes les tentatives (directes + proxys) ont échoué",
        "conseil": "Essaie /api/test pour diagnostiquer"
    }), 500

# ---------- 2. PARTICIPANTS + COTES ----------
@app.route('/api/participants')
def get_participants():
    """Récupère les participants avec plusieurs tentatives."""
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            url = f"{endpoint}{today_pmu(offset)}/R{reunion}/C{course}/participants"
            data = try_fetch(url)
            if data and 'participants' in data and len(data.get('participants', [])) > 0:
                return jsonify(data)
    return jsonify({"error": "Participants indisponibles"}), 500

# ---------- 3. PRONOSTICS EQUIDIA ----------
@app.route('/api/pronostics/equidia')
def get_pronos_equidia():
    try:
        url = "https://www.equidia.fr/courses/pronostics"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pronostics = []
        for bloc in soup.select('.pronostic-item, .expert-pick, article'):
            try:
                nom = bloc.select_one('.expert-name, .author, strong')
                selection = bloc.select_one('.selection, .picks, .nums')
                course_titre = bloc.select_one('.race-title, h3')
                if selection:
                    pronostics.append({
                        "course": course_titre.get_text(strip=True) if course_titre else "—",
                        "expert": nom.get_text(strip=True) if nom else "Équipe Equidia",
                        "selection": selection.get_text(strip=True)
                    })
            except Exception:
                continue
        return jsonify({"pronostics": pronostics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- 4. PRONOSTICS GENY ----------
@app.route('/api/pronostics/geny')
def get_pronos_geny():
    try:
        url = "https://www.geny.com/partants-pmu"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pronostics = []
        for table in soup.select('table'):
            for row in table.select('tr'):
                cells = row.select('td')
                if len(cells) >= 3:
                    texte = row.get_text(" ", strip=True)
                    if re.search(r'\d+\s*-\s*\d+', texte):
                        pronostics.append({"texte": texte})
        return jsonify({"pronostics": pronostics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- 5. PRONOSTICS TURF.BZH ----------
@app.route('/api/pronostics/turfbzh')
def get_pronos_turfbzh():
    try:
        url = "https://www.turf.bzh/pronostics"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pronostics = []
        for bloc in soup.select('.race, .course, article'):
            titre = bloc.select_one('h2, h3, .title')
            top4 = bloc.select_one('.top4, .selection, .picks')
            if top4:
                pronostics.append({
                    "course": titre.get_text(strip=True) if titre else "—",
                    "top4": top4.get_text(strip=True)
                })
        return jsonify({"pronostics": pronostics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- 6. PRONOSTICS PARIS-TURF ----------
@app.route('/api/pronostics/paristurf')
def get_pronos_paristurf():
    try:
        url = "https://www.paris-turf.com/pronostics"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pronostics = []
        for article in soup.select('article, .pronostic-card'):
            titre = article.select_one('h2, h3')
            selection = article.select_one('.selection, .nums, strong')
            if selection:
                pronostics.append({
                    "course": titre.get_text(strip=True) if titre else "—",
                    "selection": selection.get_text(strip=True)
                })
        return jsonify({"pronostics": pronostics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- LANCEMENT ----------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, threaded=True)

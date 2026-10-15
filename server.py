import os
import re
import json
import time
import subprocess
import sys
import threading
from urllib.parse import urljoin, quote
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

# =========================================================
#  PARTIE 1 : PROXY HLS VIDÉO (ex-app.py)
# =========================================================

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

REFERERS = {
    "equidia": "https://www.equidia.fr/",
    "canalturf": "https://www.canalturf.com/",
    "letrot": "https://www.letrot.com/",
    "genybet": "https://www.genybet.fr/",
    "zeturf": "https://www.zeturf.fr/",
}

HEADERS_DEFAULT = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

SESSION_HTTP = requests.Session()
SESSION_HTTP.headers.update(HEADERS_DEFAULT)

LOCK = threading.Lock()
STATE = {"url": None, "site": None, "at": 0.0}
TTL = 900

def resolve_stream(force=False):
    """Resout (et met en cache) l'URL master signee via finder.py."""
    with LOCK:
        if not force and STATE["url"] and time.time() - STATE["at"] < TTL:
            return STATE["url"], STATE["site"]

        print("Resolution du flux (via finder.py)...", file=sys.stderr)
        try:
            out = subprocess.run(
                [sys.executable, "finder.py"],
                capture_output=True, text=True, timeout=180
            )
            lines = [l for l in out.stdout.strip().splitlines() if l.strip().startswith("{")]
            if lines:
                data = json.loads(lines[-1])
                if data.get("url"):
                    STATE.update(url=data["url"], site=data.get("site"), at=time.time())
                    print(f"Flux resolu : {data['site']} -> {data['url'][:80]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("Timeout du finder", file=sys.stderr)
        except Exception as e:
            print(f"Erreur finder : {e}", file=sys.stderr)

        if not STATE["url"]:
            raise RuntimeError("Aucun flux disponible")
        return STATE["url"], STATE["site"]

def headers_for_stream(url):
    h = dict(HEADERS_DEFAULT)
    for site, ref in REFERERS.items():
        if site in url.lower():
            h["Referer"] = ref
            h["Origin"] = ref.rstrip("/")
            break
    return h

def fetch_stream(url, extra=None, stream=False):
    h = headers_for_stream(url)
    if extra:
        h.update(extra)
    return SESSION_HTTP.get(url, headers=h, timeout=(10, 30), stream=stream)

ATTR = re.compile(r'URI="([^"]+)"')

def prox_url(u, base):
    return "/seg?u=" + quote(urljoin(base, u), safe="")

def rewrite_m3u8(text, base):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            out.append(ATTR.sub(lambda m: 'URI="%s"' % prox_url(m.group(1), base), line))
        else:
            out.append(prox_url(s, base))
    return "\n".join(out) + "\n"

# =========================================================
#  PARTIE 2 : PROXY PMU (ex-server.py)
# =========================================================

PMU_API_ENDPOINTS = [
    "https://online.turfinfo.api.pmu.fr/rest/client/61/programme/",
    "https://online.turfinfo.api.pmu.fr/rest/client/1/programme/",
    "https://offline.turfinfo.api.pmu.fr/rest/client/7/programme/",
]

PMU_HEADERS = {
    'User-Agent': UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
    'Referer': 'https://www.pmu.fr/',
    'Origin': 'https://www.pmu.fr',
}

def today_pmu(offset_days=0):
    date = datetime.now() + timedelta(days=offset_days)
    return date.strftime("%d%m%Y")

def try_fetch_pmu(url):
    try:
        r = requests.get(url, headers=PMU_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Erreur PMU {url}: {e}", file=sys.stderr)
    return None

def fetch_pmu_with_fallback(path_suffix, reunion, course=None):
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            date = today_pmu(offset)
            if course:
                url = f"{endpoint}{date}/R{reunion}/C{course}/{path_suffix}"
            else:
                url = f"{endpoint}{date}/R{reunion}/{path_suffix}"
            data = try_fetch_pmu(url)
            if data:
                return data
    return None

# =========================================================
#  ROUTES : PAGE D'ACCUEIL + DIAGNOSTIC
# =========================================================

@app.route("/")
def home():
    """Sert la page du player vidéo."""
    try:
        return send_from_directory(".", "player.html")
    except Exception:
        return jsonify({
            "status": "ok",
            "message": "Serveur Crazy Cavallo",
            "endpoints": [
                "/status", "/stream.m3u8", "/resolve",
                "/api/programme", "/api/participants",
                "/api/analyse", "/api/pronostics-pmu", "/api/combinaisons"
            ]
        })

@app.route("/api")
def api_info():
    """Retourne les infos sur les endpoints API."""
    return jsonify({
        "status": "ok",
        "message": "Serveur Crazy Cavallo - API + Proxy HLS",
        "endpoints": {
            "video": ["/", "/status", "/stream.m3u8", "/resolve"],
            "pmu": ["/api/programme", "/api/participants?reunion=1&course=1",
                    "/api/analyse?reunion=1&course=1",
                    "/api/pronostics-pmu?reunion=1&course=1",
                    "/api/combinaisons?reunion=1&course=1"]
        }
    })

@app.route("/status")
def status():
    """État du flux vidéo."""
    return jsonify({
        "ready": STATE["url"] is not None,
        "site": STATE["site"],
        "url": (STATE["url"][:80] + "...") if STATE["url"] else None,
        "age_sec": int(time.time() - STATE["at"]) if STATE["at"] else None,
    })

# =========================================================
#  ROUTES : PROXY HLS VIDÉO
# =========================================================

@app.route("/stream.m3u8")
def stream():
    try:
        url, site = resolve_stream()
        r = fetch_stream(url)
        if r.status_code in (403, 404):
            url, site = resolve_stream(force=True)
            r = fetch_stream(url)
        r.raise_for_status()
        return Response(
            rewrite_m3u8(r.text, url),
            mimetype="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )
    except Exception as e:
        return f"Erreur flux : {e}", 500

@app.route("/seg")
def seg():
    url = request.args.get("u")
    if not url:
        return "parametre u manquant", 400

    if ".m3u8" in url.split("?")[0]:
        r = fetch_stream(url)
        return Response(
            rewrite_m3u8(r.text, url),
            mimetype="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )

    extra = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
    r = fetch_stream(url, extra=extra, stream=True)

    def gen():
        try:
            for chunk in r.iter_content(65536):
                if chunk:
                    yield chunk
        except Exception as e:
            print(f"Erreur stream : {e}", file=sys.stderr)

    hdrs = {"Cache-Control": "public, max-age=10"}
    for k in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if k in r.headers:
            hdrs[k] = r.headers[k]

    return Response(
        gen(),
        status=r.status_code,
        content_type=r.headers.get("Content-Type", "video/mp2t"),
        headers=hdrs
    )

@app.route("/resolve")
def force_resolve():
    try:
        url, site = resolve_stream(force=True)
        return jsonify({"ok": True, "site": site, "url": url[:80] + "..."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =========================================================
#  ROUTES : API PMU (PROGRAMME + PARTICIPANTS)
# =========================================================

@app.route("/api/programme")
def get_programme():
    for endpoint in PMU_API_ENDPOINTS:
        for offset in [0, 1, -1, 2, -2]:
            url = f"{endpoint}{today_pmu(offset)}"
            data = try_fetch_pmu(url)
            if data and 'programme' in data:
                prog = data['programme']
                if 'reunions' in prog and len(prog['reunions']) > 0:
                    return jsonify(prog)
            elif data and 'reunions' in data and len(data['reunions']) > 0:
                return jsonify(data)
    return jsonify({"error": "Programme indisponible"}), 500

@app.route("/api/participants")
def get_participants():
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_pmu_with_fallback('participants', reunion, course)
    if data and 'participants' in data:
        return jsonify(data)
    return jsonify({"error": "Participants indisponibles"}), 500

@app.route("/api/analyse")
def get_analyse():
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_pmu_with_fallback('analyse', reunion, course)
    if data and 'selection' in data:
        return jsonify(data)
    return jsonify({"error": "Analyse indisponible", "selection": []}), 200

@app.route("/api/pronostics-pmu")
def get_pronostics_pmu():
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_pmu_with_fallback('pronostics', reunion, course)
    if data and 'selection' in data:
        return jsonify(data)
    return jsonify({"error": "Pronostics indisponibles", "selection": []}), 200

@app.route("/api/combinaisons")
def get_combinaisons():
    reunion = request.args.get('reunion', '1')
    course = request.args.get('course', '1')
    data = fetch_pmu_with_fallback('combinaisons', reunion, course)
    if data and 'combinaisons' in data:
        return jsonify(data)
    return jsonify({"error": "Combinaisons indisponibles", "combinaisons": []}), 200

# =========================================================
#  LANCEMENT
# =========================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1"
    app.run(host=host, port=port, threaded=True)

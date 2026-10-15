#!/usr/bin/env python3
"""
app.py — Proxy HLS + CORS avec cache et fallback multi-sites.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, quote

import requests
from flask import Flask, Response, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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

SESSION = requests.Session()
SESSION.headers.update(HEADERS_DEFAULT)

LOCK = threading.Lock()
STATE = {"url": None, "site": None, "at": 0.0}
TTL = 900

def resolve(force=False):
    """Resout (et met en cache) l'URL master signee."""
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
                    print(f"Flux resolu : {data['site']} -> {data['url'][:80]}...", file=sys.stderr)
                else:
                    print("Aucun flux trouve par finder.py", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("Timeout du finder (180s)", file=sys.stderr)
        except Exception as e:
            print(f"Erreur finder : {e}", file=sys.stderr)

        if not STATE["url"]:
            raise RuntimeError("Aucun flux disponible pour le moment")
        return STATE["url"], STATE["site"]

def headers_for(url):
    h = dict(HEADERS_DEFAULT)
    for site, ref in REFERERS.items():
        if site in url.lower():
            h["Referer"] = ref
            h["Origin"] = ref.rstrip("/")
            break
    return h

def fetch(url, extra=None, stream=False):
    h = headers_for(url)
    if extra:
        h.update(extra)
    return SESSION.get(url, headers=h, timeout=(10, 30), stream=stream)

ATTR = re.compile(r'URI="([^"]+)"')

def prox(u, base):
    return "/seg?u=" + quote(urljoin(base, u), safe="")

def rewrite(text, base):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            out.append(ATTR.sub(lambda m: 'URI="%s"' % prox(m.group(1), base), line))
        else:
            out.append(prox(s, base))
    return "\n".join(out) + "\n"

@app.route("/")
def index():
    return send_from_directory(".", "player.html")

@app.route("/status")
def status():
    return {
        "ready": STATE["url"] is not None,
        "site": STATE["site"],
        "url": (STATE["url"][:80] + "...") if STATE["url"] else None,
        "age_sec": int(time.time() - STATE["at"]) if STATE["at"] else None,
    }

@app.route("/stream.m3u8")
def stream():
    try:
        url, site = resolve()
        r = fetch(url)
        if r.status_code in (403, 404):
            url, site = resolve(force=True)
            r = fetch(url)
        r.raise_for_status()
        return Response(
            rewrite(r.text, url),
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
        r = fetch(url)
        if r.status_code in (403, 404):
            r = fetch(url)
        return Response(
            rewrite(r.text, url),
            mimetype="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )

    extra = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
    r = fetch(url, extra=extra, stream=True)

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
        url, site = resolve(force=True)
        return {"ok": True, "site": site, "url": url[:80] + "..."}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1"
    app.run(host=host, port=port, threaded=True)

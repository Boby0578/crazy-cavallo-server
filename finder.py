#!/usr/bin/env python3
"""
finder.py — Cherche l'URL master HLS dans le code source HTML.
Version légère : utilise requests + regex (pas Playwright).
"""
import json
import re
import sys

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

M3U8_REGEX = re.compile(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^\s"\'<>]*)?', re.IGNORECASE)
M3U8_REL_REGEX = re.compile(r'["\'](/[^"\']+\.m3u8(?:\?[^"\']*)?)["\']', re.IGNORECASE)

SITES = [
    {
        "nom": "equidia",
        "urls": [
            "https://www.equidia.fr/direct",
            "https://www.equidia.fr/",
        ],
        "base": "https://www.equidia.fr",
    },
    {
        "nom": "canalturf",
        "urls": [
            "https://www.canalturf.com/canalturf_video_courses.php",
            "https://www.canalturf.com/",
        ],
        "base": "https://www.canalturf.com",
    },
    {
        "nom": "letrot",
        "urls": [
            "https://www.letrot.com/videos",
            "https://www.letrot.com/",
        ],
        "base": "https://www.letrot.com",
    },
    {
        "nom": "genybet",
        "urls": [
            "https://www.genybet.fr/",
            "https://m.genybet.fr/",
        ],
        "base": "https://www.genybet.fr",
    },
]

def extraire_m3u8(html, base):
    """Extrait la meilleure URL .m3u8 du HTML."""
    candidats = []

    for m in M3U8_REGEX.findall(html):
        candidats.append(m)

    for rel in M3U8_REL_REGEX.findall(html):
        candidats.append(base + rel)

    for pattern in [
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']+)["\']',
        r'data-src=["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'data-hls=["\']([^"\']+)["\']',
        r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]:
        for match in re.findall(pattern, html, re.IGNORECASE):
            if ".m3u8" in match:
                if match.startswith("http"):
                    candidats.append(match)
                elif match.startswith("/"):
                    candidats.append(base + match)

    vus = set()
    uniques = []
    for c in candidats:
        if c not in vus:
            vus.add(c)
            uniques.append(c)

    masters = [u for u in uniques if "master" in u.lower() or "playlist" in u.lower()]
    return (masters or uniques)[0] if (masters or uniques) else None

def test_site(site):
    """Teste les URLs d'un site et renvoie la première URL .m3u8 trouvée."""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    for url in site["urls"]:
        try:
            print(f"[{site['nom']}] Test de {url}...", file=sys.stderr)
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                print(f"[{site['nom']}] HTTP {r.status_code}", file=sys.stderr)
                continue

            url_m3u8 = extraire_m3u8(r.text, site["base"])
            if url_m3u8:
                print(f"[{site['nom']}] Flux trouve : {url_m3u8}", file=sys.stderr)
                return url_m3u8
            else:
                print(f"[{site['nom']}] Aucun .m3u8 dans {url}", file=sys.stderr)

        except requests.exceptions.Timeout:
            print(f"[{site['nom']}] Timeout sur {url}", file=sys.stderr)
        except Exception as e:
            print(f"[{site['nom']}] Erreur : {e}", file=sys.stderr)

    return None

def main():
    """Teste tous les sites dans l'ordre et renvoie le premier flux qui marche."""
    for site in SITES:
        url = test_site(site)
        if url:
            print(json.dumps({"site": site["nom"], "url": url, "all": [url]}))
            return

    print(json.dumps({"site": None, "url": None, "all": []}))
    sys.exit(1)

if __name__ == "__main__":
    main()

"""Zugang zur eigenen Geo-Box (geo.simbox.site): Anmeldung und Luftbilder.

Alle Dienste der Geo-Box verlangen einen Keycloak-Token (Realm ``simbox``,
Client-Credentials eines Service-Kontos ``simbox-geo-<werkzeug>``). Ein Token
lebt nur wenige Minuten; er wird zwischengespeichert und rechtzeitig erneuert.

Luftbilder: ``GET /osm/imagery`` ist WMS-GetMap-artig. Das Addon fragt jede
Web-Mercator-Kachel als eigenes Bild in EPSG:3857 ab - so fügen sich die
Luftbilder in denselben Kachelweg ein wie alle anderen Kartenquellen.

Quellenangaben sind Pflicht: Die Antwort nennt im Kopf ``X-Imagery-Sources``
die beteiligten Datensätze, ``/osm/meta/extent`` deren Namensnennung und Lizenz.

Das Modul kommt ohne bpy aus. Zugangsdaten setzt das Addon im Hauptthread
über :func:`configure`; die Abruf-Threads lesen sie nur.
"""

import base64
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

GEO_HOST = "geo.simbox.site"
GEO_BASE = "https://" + GEO_HOST
TOKEN_URL = "https://auth.bii-gmbh.com/realms/simbox/protocol/openid-connect/token"
OVERPASS_URL = GEO_BASE + "/overpass/api/interpreter"
TURBO_URL = "https://turbo.simbox.site/"
DEFAULT_CLIENT_ID = "simbox-geo-blender"

# Kleinste Zoomstufe für Luftbilder: Eine Kachel darf höchstens 4096 Pixel einer
# Quelldatei umfassen. Bei 20-cm-Orthofotos (z. B. Österreich) gilt das erst ab
# Zoomstufe 16 (gut 400 m Kantenlänge).
IMAGERY_MIN_ZOOM = 16
IMAGERY_MAX_ZOOM = 19
# gleichzeitige Abrufe - der Server braucht kalt 6-10 s je Bild
IMAGERY_WORKERS = 4

USER_AGENT = "blender-umgebung (Blender add-on; Geo-Box imagery)"
ORIGIN_SHIFT = 20037508.342789244

_lock = threading.Lock()
_credentials = {"client_id": "", "secret": ""}
_token = {"value": None, "expires": 0.0, "key": None}
_catalogue = {"datasets": None}


class SimboxError(Exception):
    pass


# =====================================================================
#  Zugangsdaten und Token
# =====================================================================

def configure(client_id, secret):
    """Zugangsdaten setzen (Hauptthread). Umgebungsvariablen ergänzen leere Felder."""
    client_id = (client_id or os.environ.get("SIMBOX_GEO_CLIENT_ID") or DEFAULT_CLIENT_ID).strip()
    secret = (secret or os.environ.get("SIMBOX_GEO_CLIENT_SECRET") or "").strip()
    with _lock:
        if (client_id, secret) != (_credentials["client_id"], _credentials["secret"]):
            _token.update({"value": None, "expires": 0.0, "key": None})
        _credentials.update({"client_id": client_id, "secret": secret})


def has_credentials():
    return bool(_credentials["secret"])


def client_id():
    return _credentials["client_id"]


def _tls_context():
    from .maptexture import tls_context
    return tls_context()


def token(force=False):
    """Gültiger Zugriffstoken; wird bei Bedarf neu geholt."""
    with _lock:
        key = (_credentials["client_id"], _credentials["secret"])
        if not key[1]:
            raise SimboxError("Für die Geo-Box ist kein Zugang eingetragen - bitte Client-ID und "
                              "Secret in den Add-on-Einstellungen hinterlegen")
        if (not force and _token["value"] and _token["key"] == key
                and time.time() < _token["expires"] - 30.0):
            return _token["value"]
        # wie in der Geo-Box-Doku: Client-Credentials mit HTTP-Basic-Anmeldung
        basic = base64.b64encode(("%s:%s" % (urllib.parse.quote(key[0], safe=""),
                                             urllib.parse.quote(key[1], safe=""))).encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        request = urllib.request.Request(TOKEN_URL, data=body, headers={
            "User-Agent": USER_AGENT, "Authorization": "Basic " + basic,
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=30, context=_tls_context()) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401):
                raise SimboxError("Anmeldung an der Geo-Box abgelehnt (Client-ID „%s“) - Client-ID "
                                  "und Secret prüfen" % key[0])
            raise SimboxError("Anmeldedienst antwortet mit HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise SimboxError("Anmeldedienst nicht erreichbar: %s" % exc)
        value = answer.get("access_token")
        if not value:
            raise SimboxError("Anmeldedienst lieferte keinen Token")
        _token.update({"value": value, "key": key,
                       "expires": time.time() + float(answer.get("expires_in", 300))})
        return value


def is_geo_url(url):
    try:
        return urllib.parse.urlparse(url).hostname == GEO_HOST
    except ValueError:
        return False


def auth_headers(url):
    """Authorization-Kopf für Adressen der Geo-Box, sonst leer."""
    if not is_geo_url(url):
        return {}
    return {"Authorization": "Bearer " + token()}


def _get(url, accept, timeout=60):
    """GET mit Token; ein abgelaufener Token wird einmal erneuert."""
    for attempt in range(2):
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": accept,
            "Authorization": "Bearer " + token(force=attempt > 0)})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_tls_context()) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                continue
            raise


# =====================================================================
#  Luftbilder
# =====================================================================

def tile_bounds_3857(zoom, x, y):
    """Web-Mercator-Kachel -> (minX, minY, maxX, maxY) in Metern (EPSG:3857)."""
    size = 2.0 * ORIGIN_SHIFT / (2 ** zoom)
    return (x * size - ORIGIN_SHIFT, ORIGIN_SHIFT - (y + 1) * size,
            (x + 1) * size - ORIGIN_SHIFT, ORIGIN_SHIFT - y * size)


def imagery_url(zoom, x, y, size=256):
    bbox = tile_bounds_3857(zoom, x, y)
    query = urllib.parse.urlencode({
        "SRS": "EPSG:3857", "BBOX": ",".join("%.3f" % v for v in bbox),
        "WIDTH": size, "HEIGHT": size, "FORMAT": "image/jpeg"})
    return "%s/osm/imagery?%s" % (GEO_BASE, query)


def fetch_imagery_tile(zoom, x, y, path, size=256):
    """Lädt ein Luftbild für die Kachel nach ``path``.

    Rückgabe: (Pfad oder None ohne Abdeckung, Menge der Quell-IDs).
    """
    sources_path = path + ".quellen"
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            with open(sources_path, encoding="utf-8") as handle:
                return path, set(filter(None, handle.read().split(",")))
        except OSError:
            return path, set()
    url = imagery_url(zoom, x, y, size)
    for attempt in range(3):
        try:
            data, headers = _get(url, "image/jpeg,image/*;q=0.8")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, set()          # keine Quelle deckt diese Kachel ab
            if exc.code in (502, 503) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            if exc.code == 400:
                raise SimboxError("Geo-Box lehnt den Luftbild-Ausschnitt ab (HTTP 400, Zoom %d)" % zoom)
            if exc.code == 401:
                raise SimboxError("Geo-Box lehnt den Token ab - Client braucht die Audience "
                                  "„simbox-geo“")
            raise SimboxError("Geo-Box antwortet mit HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise SimboxError("Geo-Box nicht erreichbar: %s" % exc)
    if not data:
        return None, set()                  # leere Antwort wie fehlende Abdeckung behandeln
    sources = {s.strip() for s in (headers.get("X-Imagery-Sources") or "").split(",") if s.strip()}
    with open(path, "wb") as handle:
        handle.write(data)
    try:
        with open(sources_path, "w", encoding="utf-8") as handle:
            handle.write(",".join(sorted(sources)))
    except OSError:
        pass
    return path, sources


def fetch_imagery_tiles(keys, zoom, path_for, progress=None):
    """Lädt viele Kacheln parallel. Rückgabe ({(x, y): pfad}, Quell-IDs, Anzahl ohne Bild)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    token()                                   # Anmeldung vorab prüfen, nicht je Kachel
    result, sources, missing = {}, set(), 0
    total = len(keys)
    with ThreadPoolExecutor(max_workers=IMAGERY_WORKERS) as pool:
        futures = {pool.submit(fetch_imagery_tile, zoom, x, y, path_for(x, y)): (x, y)
                   for x, y in keys}
        for number, future in enumerate(as_completed(futures), start=1):
            path, ids = future.result()
            if path is None:
                missing += 1
            else:
                result[futures[future]] = path
            sources |= ids
            if progress and (number % 5 == 0 or number == total):
                progress("Luftbilder: %d von %d" % (number, total), number / float(total or 1))
    return result, sources, missing


def catalogue(refresh=False):
    """Datensätze der Luftbilder aus ``/osm/meta/extent`` (zwischengespeichert)."""
    if refresh or _catalogue["datasets"] is None:
        try:
            data, _headers = _get(GEO_BASE + "/osm/meta/extent", "application/json")
        except urllib.error.HTTPError as exc:
            raise SimboxError("Geo-Box-Katalog antwortet mit HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise SimboxError("Geo-Box nicht erreichbar: %s" % exc)
        extent = json.loads(data.decode("utf-8"))
        _catalogue["datasets"] = {d.get("id"): d for d in
                                  (extent.get("imagery") or {}).get("datasets", ()) if d.get("id")}
    return _catalogue["datasets"]


def attribution(source_ids):
    """Quellenangabe für die beteiligten Datensätze - Lizenzpflicht der Geo-Box."""
    if not source_ids:
        return "Luftbilder: Geo-Box (simbox), Landes-Orthophotos"
    try:
        datasets = catalogue()
    except Exception:                                             # noqa: BLE001
        datasets = {}
    parts = []
    for source in sorted(source_ids):
        entry = datasets.get(source) or {}
        text = entry.get("attribution") or source
        if entry.get("licence"):
            text += " (%s)" % entry["licence"]
        parts.append(text)
    return "Luftbilder: " + "; ".join(parts)


def tile_width_m(lat, zoom):
    """Kantenlänge einer Kachel in Metern - zur Plausibilitätsprüfung."""
    return 2.0 * math.pi * 6378137.0 * math.cos(math.radians(lat)) / (2 ** zoom)

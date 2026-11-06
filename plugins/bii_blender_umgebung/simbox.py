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

import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Voreinstellung: die eigene Geo-Box. Alle Adressen der Dienste leiten sich aus
# der einen Server-Adresse im Panel „Datenquelle“ ab.
DEFAULT_BASE = "https://geo.simbox.site"
DEFAULT_TOKEN_URL = "https://auth.bii-gmbh.com/realms/simbox/protocol/openid-connect/token"
DEFAULT_CLIENT_ID = "simbox-geo-dev"

# Öffentliches Gegenstück, wenn die Geo-Box nicht genutzt wird
PUBLIC_TURBO_URL = "https://overpass-turbo.eu/"

# Kleinste Zoomstufe für Luftbilder: Eine Kachel darf höchstens 4096 Pixel einer
# Quelldatei umfassen. Bei 20-cm-Orthofotos (z. B. Österreich) gilt das erst ab
# Zoomstufe 16 (gut 400 m Kantenlänge).
IMAGERY_MIN_ZOOM = 16
IMAGERY_MAX_ZOOM = 19
# gleichzeitige Abrufe - der Server braucht kalt 6-10 s je Bild
IMAGERY_WORKERS = 4

def _version():
    """Version aus blender_manifest.toml - eine Quelle für Addon und Kennung."""
    import re
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml"),
                  encoding="utf-8") as handle:
            found = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
        return found.group(1) if found else "?"
    except OSError:
        return "?"


# Kennung aller Abrufe an die Geo-Box. Sie steht im Zugriffsprotokoll der Geo-Box
# (Feld request.headers.User-Agent) und weist die Anfragen dem Blender-Addon zu.
USER_AGENT = "Umgebung-Blender/%s (Blender add-on; simbox Geo-Box)" % _version()
ORIGIN_SHIFT = 20037508.342789244

_lock = threading.Lock()
_credentials = {"enabled": True, "base": DEFAULT_BASE, "token_url": DEFAULT_TOKEN_URL,
                "client_id": "", "secret": ""}
_token = {"value": None, "expires": 0.0, "key": None}
_catalogue = {"datasets": None}


class SimboxError(Exception):
    pass


# =====================================================================
#  Zugangsdaten und Token
# =====================================================================

ENV_KEYS = ("SIMBOX_GEO_URL", "SIMBOX_GEO_TOKEN_URL", "SIMBOX_GEO_CLIENT_ID",
            "SIMBOX_GEO_CLIENT_SECRET")
_env = {"path": "", "values": {}, "error": ""}


def read_env_file(path):
    """Liest ``KEY=VALUE``-Zeilen (Format wie ``.env.local`` der simbox) - nur ENV_KEYS."""
    values = {}
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key in ENV_KEYS and value:
                values[key] = value
    return values


def env_source():
    """(Pfad der geladenen .env-Datei, Schlüssel daraus, Fehlertext)."""
    return _env["path"], tuple(sorted(_env["values"])), _env["error"]


def configure(client_id, secret, base=DEFAULT_BASE, token_url=DEFAULT_TOKEN_URL, enabled=True,
              env_file=""):
    """Zentrale Einstellung setzen (Hauptthread).

    Reihenfolge je Wert: ``.env``-Datei, dann Feld im Panel „Datenquelle“, dann
    Umgebungsvariable, dann Voreinstellung. Alle Abrufe des Addons - Overpass,
    Gebäude, Karten, Luftbilder, Höhen und Ortssuche - lesen nur diese Werte.
    """
    env, fehler = {}, ""
    if env_file:
        try:
            env = read_env_file(env_file)
        except OSError as exc:
            fehler = "Zugangsdatei nicht lesbar: %s" % exc
    _env.update({"path": env_file if env else "", "values": env, "error": fehler})

    def wert(key, feld, vorgabe):
        return (env.get(key) or feld or os.environ.get(key) or vorgabe).strip()

    client_id = wert("SIMBOX_GEO_CLIENT_ID", client_id, DEFAULT_CLIENT_ID)
    secret = wert("SIMBOX_GEO_CLIENT_SECRET", secret, "")
    base = wert("SIMBOX_GEO_URL", base, DEFAULT_BASE).rstrip("/")
    token_url = wert("SIMBOX_GEO_TOKEN_URL", token_url, DEFAULT_TOKEN_URL)
    neu = {"enabled": bool(enabled), "base": base, "token_url": token_url,
           "client_id": client_id, "secret": secret}
    with _lock:
        if neu != _credentials:
            _token.update({"value": None, "expires": 0.0, "key": None})
            _catalogue["datasets"] = None
            _status.update({"text": "", "ok": None})
        _credentials.update(neu)


_status = {"text": "", "ok": None}


def set_status(text, ok):
    """Ergebnis des letzten Verbindungstests - wird im Panel „Datenquelle“ angezeigt."""
    _status.update({"text": text, "ok": ok})


def status():
    return _status["text"], _status["ok"]


def has_credentials():
    return bool(_credentials["secret"])


def enabled():
    """Ist die Geo-Box als Datenquelle gewählt?"""
    return _credentials["enabled"]


def active():
    """Geo-Box gewählt und Zugang eingetragen - dann laufen alle Abrufe über sie."""
    return _credentials["enabled"] and bool(_credentials["secret"])


def client_id():
    return _credentials["client_id"]


def base_url():
    return _credentials["base"]


def overpass_url():
    return base_url() + "/overpass/api/interpreter"


def basemap_template():
    return base_url() + "/tiles/basemap/{z}/{x}/{y}.png"


def search_url():
    return base_url() + "/nominatim/search"


def elevation_url():
    return base_url() + "/osm/elevation/sample"


def turbo_url():
    """Overpass Turbo der gewählten Datenquelle (Geo-Box: Anmeldung im Browser)."""
    return base_url() + "/turbo/" if enabled() else PUBLIC_TURBO_URL


def _tls_context():
    from .maptexture import tls_context
    return tls_context()


def token(force=False):
    """Gültiger Zugriffstoken; wird bei Bedarf neu geholt."""
    with _lock:
        key = (_credentials["client_id"], _credentials["secret"])
        token_url = _credentials["token_url"]
        if not key[1]:
            raise SimboxError("Für die Geo-Box ist kein Zugang eingetragen - bitte Client-ID und "
                              "Secret im Panel „Datenquelle“ hinterlegen")
        if (not force and _token["value"] and _token["key"] == key
                and time.time() < _token["expires"] - 30.0):
            return _token["value"]
        # wie in der Anleitung zur Geo-Box: Client-ID und Secret im Formular
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": key[0], "client_secret": key[1]}).encode()
        request = urllib.request.Request(token_url, data=body, headers={
            "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
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


def retry_after(headers, default=5.0, limit=60.0):
    """Wartezeit aus dem Kopf ``Retry-After`` (Sekunden), begrenzt."""
    try:
        return max(1.0, min(limit, float((headers or {}).get("Retry-After") or default)))
    except (TypeError, ValueError):
        return default


# Datenabdeckung der Geo-Box (Deutschland, Österreich, Schweiz) als grobes Rechteck
DACH = (45.8, 5.8, 55.1, 17.2)


def covers(bbox):
    """Liegt das Gebiet (min_lat, min_lon, max_lat, max_lon) im Datenbereich der Geo-Box?"""
    lat = (bbox[0] + bbox[2]) / 2.0
    lon = (bbox[1] + bbox[3]) / 2.0
    return DACH[0] <= lat <= DACH[2] and DACH[1] <= lon <= DACH[3]


def is_geo_url(url):
    try:
        return (urllib.parse.urlparse(url).hostname
                == urllib.parse.urlparse(base_url()).hostname)
    except ValueError:
        return False


def auth_headers(url):
    """Authorization-Kopf für Adressen der Geo-Box, sonst leer."""
    if not is_geo_url(url):
        return {}
    return {"Authorization": "Bearer " + token()}


def _open(url, accept, data=None, content_type=None, timeout=60):
    """Anfrage mit Token; ein abgelaufener Token wird einmal erneuert."""
    for attempt in range(2):
        headers = {"User-Agent": USER_AGENT, "Accept": accept,
                   "Authorization": "Bearer " + token(force=attempt > 0)}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_tls_context()) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                continue
            raise


def _get(url, accept, timeout=60):
    return _open(url, accept, timeout=timeout)


def request(url, accept="*/*", data=None, content_type=None, timeout=60, what="Geo-Box"):
    """Angemeldeter Abruf mit Wiederholung bei ``429``/``502``/``503``.

    Rückgabe (Daten, Kopf). Fehler kommen als :class:`SimboxError` mit Klartext;
    ``404`` bleibt ``urllib.error.HTTPError`` (keine Abdeckung).
    """
    for attempt in range(3):
        try:
            return _open(url, accept, data, content_type, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if exc.code in (429, 502, 503) and attempt < 2:
                time.sleep(retry_after(exc.headers, default=2.0 * (attempt + 1)))
                continue
            if exc.code == 401:
                raise SimboxError("%s lehnt den Token ab (Client-ID „%s“) - Client braucht "
                                  "die Audience „simbox-geo“" % (what, client_id()))
            raise SimboxError("%s antwortet mit HTTP %s" % (what, exc.code))
        except (urllib.error.URLError, OSError) as exc:
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise SimboxError("%s nicht erreichbar: %s" % (what, exc))


def sample_elevation(points, timeout=120):
    """Höhen für [(lat, lon), …] über ``/osm/elevation/sample`` (höchstens 10000 je Abruf).

    Rückgabe (Höhen in Metern oder None, Quellenangaben der beteiligten Datensätze).
    """
    body = json.dumps({"locations": [{"latitude": lat, "longitude": lon}
                                     for lat, lon in points]}).encode("utf-8")
    try:
        data, _headers = request(elevation_url(), "application/json", data=body,
                                 content_type="application/json", timeout=timeout,
                                 what="Geo-Box-Höhendienst")
    except urllib.error.HTTPError as exc:
        raise SimboxError("Geo-Box-Höhendienst antwortet mit HTTP %s" % exc.code)
    answer = json.loads(data.decode("utf-8"))
    heights = [r.get("elevation") for r in answer.get("results") or ()]
    if len(heights) != len(points):
        raise SimboxError("Geo-Box-Höhendienst lieferte %d statt %d Werte"
                          % (len(heights), len(points)))
    return heights, answer


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
    return "%s/osm/imagery?%s" % (base_url(), query)


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
            if exc.code in (429, 502, 503) and attempt < 2:
                time.sleep(retry_after(exc.headers, default=2.0 * (attempt + 1)))
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
            data, _headers = _get(base_url() + "/osm/meta/extent", "application/json")
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

"""Abfrage von Bahndaten (OpenStreetMap / OpenRailwayMap) ueber die Overpass-API.

OpenRailwayMap ist eine Kartendarstellung der Bahn-Daten aus OpenStreetMap.
Genau diese Rohdaten (railway=*, railway:position an railway=milestone) werden
hier abgefragt - es ist dieselbe Datenquelle, die ORM rendert.
"""

import gzip
import hashlib
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "blender-railway-chainage/1.0 (Blender add-on; https://www.openrailwaymap.org)"

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Cache-Lebensdauer in Sekunden (1 Tag)
CACHE_TTL = 24 * 3600


class OverpassError(Exception):
    pass


def build_query(bbox, track_types, timeout=180, with_milestones=True):
    """bbox = (min_lat, min_lon, max_lat, max_lon); track_types = Liste von railway-Werten."""
    south, west, north, east = bbox
    area = "%.7f,%.7f,%.7f,%.7f" % (south, west, north, east)
    types = "|".join(track_types) if track_types else "rail"
    parts = [
        "[out:json][timeout:%d];" % int(timeout),
        'way["railway"~"^(%s)$"]["railway"!~"^(abandoned|razed|proposed)$"](%s)->.w;' % (types, area),
        'rel(bw.w)["route"~"^(railway|tracks)$"]->.r;',
        "(.w; .w >;);",
        "out body qt;",
        ".r out body;",
    ]
    if with_milestones:
        parts.append('node["railway"~"^(milestone|km_post)$"](%s);' % area)
        parts.append("out body qt;")
    # Betriebsstellen, um Bahnhofsgleise zuordnen zu können
    parts.append('node["railway"~"^(station|halt)$"](%s);' % area)
    parts.append("out body qt;")
    return "\n".join(parts)


def _cache_path(query):
    key = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "overpass_%s.json.gz" % key)


def _read_cache(path):
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CACHE_TTL:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return None


def _write_cache(path, data):
    try:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def _request(endpoint, query, timeout):
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    if not text.lstrip().startswith("{"):
        # Overpass meldet Fehler als HTML-Seite
        snippet = " ".join(text.split())[:300]
        raise OverpassError("Unerwartete Antwort vom Server: %s" % snippet)
    return json.loads(text)


# groesste Kantenlaenge einer Einzelabfrage in Grad - darueber wird gekachelt
MAX_SPAN = 0.25


def split_bbox(bbox, max_span=MAX_SPAN):
    """Zerlegt ein Gebiet in Kacheln, die der Server sicher beantworten kann."""
    south, west, north, east = bbox
    rows = max(1, int(math.ceil((north - south) / max_span)))
    cols = max(1, int(math.ceil((east - west) / max_span)))
    d_lat = (north - south) / rows
    d_lon = (east - west) / cols
    return [(south + r * d_lat, west + c * d_lon,
             south + (r + 1) * d_lat, west + (c + 1) * d_lon)
            for r in range(rows) for c in range(cols)]


def merge(responses):
    """Fuehrt mehrere Overpass-Antworten zusammen und entfernt Doppelungen."""
    elements = {}
    for data in responses:
        for element in data.get("elements", ()):
            elements[(element.get("type"), element.get("id"))] = element
    return {"elements": list(elements.values())}


def fetch(bbox, track_types, timeout=180, use_cache=True, endpoint=None, with_milestones=True,
          progress=None):
    """Laedt Bahndaten fuer die bbox - bei grossen Gebieten kachelweise."""
    tiles = split_bbox(bbox)
    if len(tiles) > 1:
        responses = []
        for number, tile in enumerate(tiles):
            if progress:
                progress("Gebiet %d von %d wird geladen …" % (number + 1, len(tiles)),
                         (number + 1) / float(len(tiles)))
            responses.append(_fetch_single(tile, track_types, timeout, use_cache, endpoint,
                                           with_milestones, progress=None))
        return merge(responses)
    return _fetch_single(bbox, track_types, timeout, use_cache, endpoint, with_milestones,
                         progress)


def _fetch_single(bbox, track_types, timeout, use_cache, endpoint, with_milestones, progress):
    query = build_query(bbox, track_types, timeout=timeout, with_milestones=with_milestones)
    return _fetch_raw(query, timeout, use_cache, endpoint, progress)


def _fetch_raw(query, timeout, use_cache, endpoint, progress):
    """Eine fertige Overpass-Abfrage absetzen - mit Cache und Serverwechsel."""
    path = _cache_path(query)

    if use_cache:
        cached = _read_cache(path)
        if cached is not None:
            if progress:
                progress("Daten aus lokalem Cache geladen", 1.0)
            return cached

    endpoints = [endpoint] if endpoint else list(ENDPOINTS)
    errors = []
    for i, ep in enumerate(endpoints):
        if progress:
            progress("Frage Overpass-Server ab (%d/%d): %s" % (i + 1, len(endpoints), ep),
                     None)
        try:
            data = _request(ep, query, timeout)
        except urllib.error.HTTPError as exc:
            reason = {429: "Zu viele Anfragen (Rate-Limit)",
                      504: "Server ueberlastet (Timeout)"}.get(exc.code, exc.reason)
            errors.append("%s: HTTP %s - %s" % (ep, exc.code, reason))
            continue
        except (urllib.error.URLError, OSError) as exc:
            errors.append("%s: %s" % (ep, exc))
            continue
        except OverpassError as exc:
            errors.append("%s: %s" % (ep, exc))
            continue

        if use_cache:
            _write_cache(path, data)
        return data

    raise OverpassError(
        "Keine Daten erhalten. Bitte Gebiet verkleinern oder später erneut versuchen.\n"
        + "\n".join(errors)
    )


# ------------------------------------------------------------------ Gebäude

# Gebäudedaten sind deutlich dichter als Gleisdaten - daher kleinere Kacheln
BUILDING_SPAN = 0.05


def build_building_query(bbox, timeout=180, with_parts=False):
    """Abfrage aller Gebäudegrundrisse im Gebiet (Wege und Multipolygone)."""
    south, west, north, east = bbox
    area = "%.7f,%.7f,%.7f,%.7f" % (south, west, north, east)
    teile = '  way["building:part"](%s);\n' % area if with_parts else ""
    return ("[out:json][timeout:%d];\n(\n  way[\"building\"](%s);\n%s"
            "  rel[\"building\"][\"type\"=\"multipolygon\"](%s);\n);\n"
            "out body;\n>;\nout skel qt;" % (int(timeout), area, teile, area))


def fetch_buildings(bbox, timeout=180, use_cache=True, endpoint=None, with_parts=False,
                    progress=None):
    """Lädt Gebäudegrundrisse für die bbox - bei großen Gebieten kachelweise."""
    tiles = split_bbox(bbox, max_span=BUILDING_SPAN)
    if len(tiles) == 1:
        return _fetch_raw(build_building_query(bbox, timeout, with_parts),
                          timeout, use_cache, endpoint, progress)
    responses = []
    for number, tile in enumerate(tiles):
        if progress:
            progress("Gebäude: Teilgebiet %d von %d …" % (number + 1, len(tiles)),
                     (number + 1) / float(len(tiles)))
        responses.append(_fetch_raw(build_building_query(tile, timeout, with_parts),
                                    timeout, use_cache, endpoint, None))
    return merge(responses)


def clear_cache():
    """Loescht zwischengespeicherte Overpass-Antworten. Gibt die Anzahl zurueck."""
    folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage")
    count = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith("overpass_"):
                try:
                    os.remove(os.path.join(folder, name))
                    count += 1
                except OSError:
                    pass
    return count

"""Kartenkacheln als Textur für den Streckenkorridor.

Die Textur folgt der Kilometrierungslinie: Sie wird entlang der Strecke
"gestreckt" (Längsachse = Station, Querachse = Abstand zur Achse). Dadurch
bleibt das Bild schmal und klein, statt die gesamte - bei schräg verlaufenden
Strecken sehr große - Bounding Box abzudecken.
"""

import math
import os
import ssl
import tempfile
import urllib.error
import urllib.request

try:
    import certifi
except ImportError:                                             # pragma: no cover
    certifi = None

USER_AGENT = ("blender-railway-chainage/1.4 (Blender add-on; corridor map texture; "
              "contact via add-on documentation)")

# Schlüssel -> (URL-Vorlage, max. Zoom, Kachelgröße, Überlagerung, Quellenangabe)
SOURCES = {
    'BASEMAP_DE': ("https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/"
                   "de_basemapde_web_raster_farbe/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png",
                   19, 256, None,
                   "© GeoBasis-DE / BKG, basemap.de (dl-de/by-2-0)"),
    'TOPPLUS': ("https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/web/default/"
                "WEBMERCATOR/{z}/{y}/{x}.png", 18, 256, None,
                "© Bundesamt für Kartographie und Geodäsie, TopPlusOpen (dl-de/by-2-0)"),
    'TOPPLUS_GRAU': ("https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/web_grau/"
                     "default/WEBMERCATOR/{z}/{y}/{x}.png", 18, 256, None,
                     "© Bundesamt für Kartographie und Geodäsie, TopPlusOpen (dl-de/by-2-0)"),
    'OSM': ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, 256, None,
            "© OpenStreetMap-Mitwirkende"),
    'OSM_ORM': ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, 256,
                "https://tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png",
                "© OpenStreetMap-Mitwirkende, Bahnkarte: OpenRailwayMap (CC-BY-SA)"),
    'TOPO': ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 17, 256, None,
             "© OpenStreetMap-Mitwirkende, SRTM | Darstellung © OpenTopoMap (CC-BY-SA)"),
}

# Quellen, die auf ehrenamtlich betriebenen Servern liegen: dort gilt eine strenge
# Obergrenze, weil massenhafte Abrufe deren Nutzungsbedingungen verletzen.
SOURCES['BKG_ORM'] = (SOURCES['BASEMAP_DE'][0], 19, 256, SOURCES['OSM_ORM'][3],
    SOURCES['BASEMAP_DE'][4] + ' | Daten © OpenStreetMap-Mitwirkende, Stil OpenRailwayMap (CC-BY-SA 2.0)')

VOLUNTEER_SOURCES = {'OSM', 'OSM_ORM', 'BKG_ORM', 'TOPO'}
VOLUNTEER_LIMIT = 250

_TLS_CONTEXT = None


class MapError(Exception):
    pass


def tls_context():
    """TLS-Kontext mit Blenders und Windows' vertrauenswuerdigen Zertifikaten.

    Blenders eingebautes Python findet unter Windows nicht in jeder Installation
    einen aktuellen OpenSSL-Zertifikatsspeicher. Blenders mitgeliefertes certifi-
    Buendel enthaelt die oeffentlichen Root-CAs; der Windows-Speicher ergaenzt
    beispielsweise Zertifikate eines Firmen-Proxys. Die Zertifikatspruefung
    bleibt dabei vollstaendig aktiv.
    """
    global _TLS_CONTEXT
    if _TLS_CONTEXT is not None:
        return _TLS_CONTEXT

    # Python lädt unter Windows die für Serverauthentifizierung freigegebenen
    # Zertifikate aus ROOT und CA. Dabei werden Vertrauenszweck und Hostname
    # weiterhin geprüft; keine pauschale Übernahme aller Zertifikate.
    context = ssl.create_default_context()
    if certifi is not None:
        context.load_verify_locations(cafile=certifi.where())

    _TLS_CONTEXT = context
    return context


def tile_resolution(lat, zoom, tile_size=256):
    """Bodenauflösung einer Kachel in Metern je Pixel."""
    return 156543.03392804097 * math.cos(math.radians(lat)) / (2 ** zoom) * (256.0 / tile_size)


def choose_zoom(lat, resolution, max_zoom, tile_size=256):
    """Kleinste Zoomstufe, die mindestens die gewünschte Auflösung liefert."""
    for zoom in range(1, max_zoom + 1):
        if tile_resolution(lat, zoom, tile_size) <= resolution:
            return zoom
    return max_zoom


def global_pixels(lat, lon, zoom, tile_size=256):
    """Geografische Koordinate -> globale Pixelkoordinate der Kachelpyramide."""
    n = 2 ** zoom * tile_size
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def cache_dir():
    folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage", "tiles")
    os.makedirs(folder, exist_ok=True)
    return folder


def tile_path(source_key, layer, zoom, x, y):
    return os.path.join(cache_dir(), "%s_%s_%d_%d_%d.png" % (source_key, layer, zoom, x, y))


def download_tile(url, path, timeout=30):
    """Lädt eine Kachel, sofern sie nicht bereits im Zwischenspeicher liegt."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "image/png,image/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls_context()) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise MapError("Kartendienst antwortet mit HTTP %s" % exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise MapError("Kartendienst nicht erreichbar: %s" % exc)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def fetch_tiles(source_key, keys, zoom, progress=None, tile_url=""):
    """Lädt alle benötigten Kacheln. Rückgabe {(x, y): {layer: pfad}}.

    ``tile_url`` überschreibt die Quelle durch einen eigenen Kachelserver.
    """
    template, _max_zoom, _size, overlay, _credit = SOURCES[source_key]
    if tile_url:
        template, overlay = tile_url, None
        source_key = "custom"
    result = {}
    total = len(keys)
    for number, (x, y) in enumerate(keys):
        if progress and (number % 5 == 0 or number == total - 1):
            progress("Kartenkacheln: %d von %d" % (number + 1, total),
                     (number + 1) / float(total or 1))
        entry = {}
        entry["base"] = download_tile(template.format(z=zoom, x=x, y=y),
                                      tile_path(source_key, "base", zoom, x, y))
        if overlay:
            entry["overlay"] = download_tile(overlay.format(z=zoom, x=x, y=y),
                                             tile_path(source_key, "over", zoom, x, y))
        result[(x, y)] = entry
    return result


def credit(source_key):
    return SOURCES[source_key][4]

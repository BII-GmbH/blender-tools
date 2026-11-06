"""Amtliche Höhendaten (DGM) über einen OGC-Web-Coverage-Service.

Voreingestellt ist das offene DGM1 von Baden-Württemberg (LGL, 1 m Raster,
Lizenz dl-de/by-2-0). Der Dienst liefert GeoTIFF in ETRS89/UTM; die Rasterweite
wird serverseitig über SCALEFACTOR gewählt, sodass nur die tatsächlich
benötigte Datenmenge übertragen wird.

Das Modul kommt ohne bpy und ohne Fremdbibliotheken aus - der GeoTIFF-Leser
deckt die vom Dienst gelieferten unkomprimierten Raster ab.
"""

import gzip
import hashlib
import math
import os
import struct
import tempfile
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "blender-railway-chainage/1.3 (Blender add-on)"

# Vordefinierte Höhendienste. "axes" sind die Achsnamen der WCS-Subsets,
# "bounds" der abgedeckte Bereich (min_lat, min_lon, max_lat, max_lon).
SERVICES = {
    'BW_DGM1': {
        "url": "https://owsproxy.lgl-bw.de/owsproxy/wcs/WCS_INSP_BW_Hoehe_Coverage_DGM1",
        "coverage": "EL.ElevationGridCoverage",
        "zone": 32, "axes": ("E", "N"),
        "bounds": (47.40, 7.20, 50.00, 10.70),
        "label": "Baden-Württemberg, DGM1 (LGL, dl-de/by-2-0)",
    },
    'BY_DGM1': {
        "kind": "tiles",
        "url": "https://download1.bayernwolke.de/a/dgm/dgm1/{e}_{n}.tif",
        "coverage": "", "zone": 32, "axes": ("E", "N"),
        "bounds": (47.20, 8.90, 50.60, 13.90),
        "label": "Bayern, DGM1 (Bayerische Vermessungsverwaltung, CC BY 4.0 - "
                 "Datenquelle: www.geodaten.bayern.de)",
    },
    'NRW_DGM1': {
        "url": "https://www.wcs.nrw.de/geobasis/wcs_nw_dgm",
        "coverage": "nw_dgm",
        "zone": 32, "axes": ("x", "y"),
        "bounds": (50.20, 5.80, 52.60, 9.50),
        "label": "Nordrhein-Westfalen, DGM1 (Geobasis NRW, dl-de/zero-2-0)",
    },
}


def service_for(lat, lon):
    """Dienst, der die Koordinate abdeckt - oder None."""
    for key, entry in SERVICES.items():
        min_lat, min_lon, max_lat, max_lon = entry["bounds"]
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return key
    return None

CACHE_TTL = 30 * 24 * 3600

# Versatz in Metern, mit dem die Nachbarkachel gesucht wird, wenn ein Punkt
# genau auf einer Kachelgrenze liegt
TILE_EDGE = 1e-3


class DgmError(Exception):
    pass


# --------------------------------------------------------------- Projektion

def _grs80():
    a = 6378137.0
    f = 1.0 / 298.257222101
    return a, f


def wgs84_to_utm(lat, lon, zone=32):
    """(lat, lon) in Grad -> (East, North) in ETRS89/UTM (Krüger-Reihen)."""
    a, f = _grs80()
    n = f / (2.0 - f)
    k0, e0 = 0.9996, 500000.0
    lon0 = math.radians(zone * 6 - 183)
    phi, lam = math.radians(lat), math.radians(lon) - lon0
    big_a = a / (1.0 + n) * (1.0 + n ** 2 / 4.0 + n ** 4 / 64.0)
    alpha = (n / 2.0 - 2.0 * n ** 2 / 3.0 + 5.0 * n ** 3 / 16.0,
             13.0 * n ** 2 / 48.0 - 3.0 * n ** 3 / 5.0,
             61.0 * n ** 3 / 240.0)
    t = math.sinh(math.atanh(math.sin(phi))
                  - 2.0 * math.sqrt(n) / (1.0 + n)
                  * math.atanh(2.0 * math.sqrt(n) / (1.0 + n) * math.sin(phi)))
    xi = math.atan(t / math.cos(lam))
    eta = math.atanh(math.sin(lam) / math.hypot(1.0, t))
    east = e0 + k0 * big_a * (eta + sum(
        alpha[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j in range(3)))
    north = k0 * big_a * (xi + sum(
        alpha[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j in range(3)))
    return east, north


def utm_to_wgs84(east, north, zone=32):
    """(East, North) in ETRS89/UTM -> (lat, lon) in Grad."""
    a, f = _grs80()
    n = f / (2.0 - f)
    k0, e0 = 0.9996, 500000.0
    lon0 = math.radians(zone * 6 - 183)
    big_a = a / (1.0 + n) * (1.0 + n ** 2 / 4.0 + n ** 4 / 64.0)
    beta = (n / 2.0 - 2.0 * n ** 2 / 3.0 + 37.0 * n ** 3 / 96.0,
            n ** 2 / 48.0 + n ** 3 / 15.0,
            17.0 * n ** 3 / 480.0)
    delta = (2.0 * n - 2.0 * n ** 2 / 3.0 - 2.0 * n ** 3,
             7.0 * n ** 2 / 3.0 - 8.0 * n ** 3 / 5.0,
             56.0 * n ** 3 / 15.0)
    xi = north / (k0 * big_a)
    eta = (east - e0) / (k0 * big_a)
    xi_ = xi - sum(beta[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta)
                   for j in range(3))
    eta_ = eta - sum(beta[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta)
                     for j in range(3))
    chi = math.asin(max(-1.0, min(1.0, math.sin(xi_) / math.cosh(eta_))))
    lat = chi + sum(delta[j] * math.sin(2 * (j + 1) * chi) for j in range(3))
    lon = lon0 + math.atan(math.sinh(eta_) / math.cos(xi_))
    return math.degrees(lat), math.degrees(lon)


# ------------------------------------------------------------------ GeoTIFF

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _lzw_decode(data):
    """LZW-Dekodierung in der TIFF-Variante (MSB zuerst, Early Change)."""
    clear_code, end_code = 256, 257
    table = None
    out = bytearray()
    previous = None
    bit_pos = 0
    code_width = 9
    next_code = 258
    total_bits = len(data) * 8

    while bit_pos + code_width <= total_bits:
        byte_index, bit_offset = divmod(bit_pos, 8)
        chunk = data[byte_index:byte_index + 3]
        value = int.from_bytes(chunk.ljust(3, b"\0"), "big")
        code = (value >> (24 - bit_offset - code_width)) & ((1 << code_width) - 1)
        bit_pos += code_width

        if code == clear_code:
            table = [bytes([i]) for i in range(256)] + [b"", b""]
            next_code, code_width, previous = 258, 9, None
            continue
        if code == end_code:
            break
        if table is None:
            table = [bytes([i]) for i in range(256)] + [b"", b""]

        if code < len(table):
            entry = table[code]
        elif previous is not None:
            entry = previous + previous[:1]
        else:
            break

        out += entry
        if previous is not None:
            table.append(previous + entry[:1])
            next_code = len(table)
        previous = entry

        # "early change": die Codebreite wächst einen Code früher
        if next_code + 1 >= (1 << code_width) and code_width < 12:
            code_width += 1
    return bytes(out)


def _undo_predictor(data, width, samples, bits, byteorder):
    """Horizontale Differenzbildung (TIFF-Predictor 2) rückgängig machen."""
    step = bits // 8
    row_length = width * samples * step
    result = bytearray(data)
    for row_start in range(0, len(result), row_length):
        row = result[row_start:row_start + row_length]
        if len(row) < row_length:
            break
        for index in range(samples * step, row_length, step):
            previous = int.from_bytes(row[index - samples * step:index - samples * step + step],
                                      byteorder)
            current = int.from_bytes(row[index:index + step], byteorder)
            row[index:index + step] = ((current + previous) % (1 << bits)).to_bytes(step, byteorder)
        result[row_start:row_start + row_length] = row
    return bytes(result)


class GeoTiff:
    """Minimaler Leser für unkomprimierte (Geo-)TIFF-Raster."""

    def __init__(self, data):
        if data[:2] == b'MM':
            self.bo = '>'
        elif data[:2] == b'II':
            self.bo = '<'
        else:
            raise DgmError("Antwort ist kein TIFF (vermutlich eine Fehlermeldung des Dienstes)")
        self.data = data
        tags = {}
        offset = struct.unpack(self.bo + 'I', data[4:8])[0]
        count = struct.unpack(self.bo + 'H', data[offset:offset + 2])[0]
        for i in range(count):
            entry = offset + 2 + i * 12
            tag, typ, num = struct.unpack(self.bo + 'HHI', data[entry:entry + 8])
            size = _TYPE_SIZE.get(typ, 1) * num
            if size <= 4:
                raw = data[entry + 8:entry + 12]
            else:
                start = struct.unpack(self.bo + 'I', data[entry + 8:entry + 12])[0]
                raw = data[start:start + size]
            tags[tag] = (typ, num, raw)
        self.tags = tags

        self.width = self._int(256)
        self.height = self._int(257)
        self.bits = self._int(258) or 16
        self.compression = self._int(259) or 1
        if self.compression not in (1, 5):
            raise DgmError("TIFF-Kompression %d wird nicht unterstützt" % self.compression)
        self.predictor = self._int(317) or 1
        self.sample_format = self._int(339) or 1
        self.scale = self._doubles(33550) or (1.0, 1.0, 0.0)
        tiepoint = self._doubles(33922) or (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.origin = (tiepoint[3], tiepoint[4])
        self.nodata = self._nodata()

        key = (self.bits, self.sample_format)
        self.fmt = {(16, 1): 'H', (16, 2): 'h', (32, 3): 'f', (32, 2): 'i', (8, 1): 'B'}.get(key)
        if self.fmt is None:
            raise DgmError("Rasterformat %d bit / Typ %d wird nicht unterstützt" % key)
        self.values = self._read_pixels()

    def _int(self, tag):
        item = self.tags.get(tag)
        if not item:
            return None
        typ, _num, raw = item
        fmt = {3: 'H', 4: 'I', 8: 'h', 9: 'i'}.get(typ)
        return struct.unpack(self.bo + fmt, raw[:_TYPE_SIZE[typ]])[0] if fmt else None

    def _doubles(self, tag):
        item = self.tags.get(tag)
        if not item:
            return None
        _typ, num, raw = item
        return struct.unpack(self.bo + 'd' * num, raw[:8 * num])

    def _nodata(self):
        item = self.tags.get(42113)
        if not item:
            return None
        try:
            return float(item[2].split(b'\0')[0].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            return None

    def _read_pixels(self):
        offsets = self.tags.get(273)
        counts = self.tags.get(279)
        if not offsets or not counts:
            raise DgmError("TIFF ohne Bilddaten")
        step = self.bits // 8
        values = []
        offs = self._values(273)
        cnts = self._values(279)
        order = "big" if self.bo == ">" else "little"
        for off, cnt in zip(offs, cnts):
            chunk = self.data[off:off + cnt]
            if self.compression == 5:
                chunk = _lzw_decode(chunk)
                if self.predictor == 2:
                    chunk = _undo_predictor(chunk, self.width, 1, self.bits, order)
            values.extend(struct.unpack(self.bo + self.fmt * (len(chunk) // step),
                                        chunk[:len(chunk) // step * step]))
        return values

    def _values(self, tag):
        typ, num, raw = self.tags[tag]
        fmt = {3: 'H', 4: 'I', 8: 'h', 9: 'i'}[typ]
        return struct.unpack(self.bo + fmt * num, raw[:_TYPE_SIZE[typ] * num])

    def height_at(self, east, north, tolerance=0.0):
        """Höhe an einer UTM-Koordinate (bilinear). None außerhalb oder ohne Daten.

        ``tolerance`` erlaubt Punkte bis zu so vielen Rasterschritten außerhalb
        der letzten Stützstelle; ihr Wert wird dann vom Rand übernommen. Das
        wird für den schmalen Streifen zwischen zwei Kacheln gebraucht: Eine
        Kachel mit 1000 x 1000 Stützstellen deckt 1000 m ab, ihre letzte
        Stützstelle liegt also 1 m vor der Kachelgrenze, und die erste der
        Nachbarkachel genau auf ihr. Zwischen beiden fehlt sonst jeder Wert.
        """
        col = (east - self.origin[0]) / self.scale[0]
        row = (self.origin[1] - north) / self.scale[1]
        if (col < -tolerance or row < -tolerance
                or col > self.width - 1 + tolerance or row > self.height - 1 + tolerance):
            return None
        col = min(max(col, 0.0), self.width - 1.0)
        row = min(max(row, 0.0), self.height - 1.0)
        c0, r0 = int(col), int(row)
        c1, r1 = min(c0 + 1, self.width - 1), min(r0 + 1, self.height - 1)
        fc, fr = col - c0, row - r0
        corners = []
        for r in (r0, r1):
            for c in (c0, c1):
                v = self.values[r * self.width + c]
                if self.nodata is not None and v == self.nodata:
                    return None
                corners.append(float(v))
        top = corners[0] + (corners[1] - corners[0]) * fc
        bottom = corners[2] + (corners[3] - corners[2]) * fc
        return top + (bottom - top) * fr


# ----------------------------------------------------------------- Download

def _cache_path(key):
    folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage", "dgm")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "%s.tif.gz" % key)


def fetch_download_tile(url_template, east, north, size, use_cache=True, timeout=180):
    """Lädt eine fertige Kachel eines Datei-Downloads (z. B. Bayern: 1-km-Kacheln)."""
    # Die Kacheln sind nach ihrer Südwest-Ecke in Kilometern benannt
    url = url_template.format(e=int(east // 1000), n=int(north // 1000))
    path = _cache_path(hashlib.sha1(url.encode("utf-8")).hexdigest()[:20])
    if use_cache and os.path.exists(path):
        try:
            with gzip.open(path, "rb") as fh:
                return GeoTiff(fh.read())
        except Exception:
            pass
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise DgmError("Höhenkachel nicht verfügbar (HTTP %s)" % exc.code)
    except (urllib.error.URLError, OSError) as exc:
        raise DgmError("Höhendienst nicht erreichbar: %s" % exc)
    tile = GeoTiff(data)
    if use_cache:
        try:
            with gzip.open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
    return tile


def fetch_tile(endpoint, coverage, east, north, size, scale_factor, use_cache=True, timeout=120,
               overlap=25.0, axes=("E", "N")):
    """Lädt eine quadratische Kachel ab (east, north) mit Kantenlänge ``size`` Meter.

    Die Kachel wird mit etwas Überlappung angefordert, damit an den Kachelrändern
    keine Lücken im Geländegitter entstehen.
    """
    query = {
        "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
        "COVERAGEID": coverage, "FORMAT": "image/tiff",
    }
    url = "%s?%s&SUBSET=%s(%d,%d)&SUBSET=%s(%d,%d)" % (
        endpoint, urllib.parse.urlencode(query),
        axes[0], int(east - overlap), int(east + size + overlap),
        axes[1], int(north - overlap), int(north + size + overlap))
    if scale_factor and scale_factor != 1.0:
        url += "&SCALEFACTOR=%s" % ("%.6f" % scale_factor).rstrip("0").rstrip(".")

    path = _cache_path(hashlib.sha1(url.encode("utf-8")).hexdigest()[:20])
    if use_cache and os.path.exists(path):
        try:
            with gzip.open(path, "rb") as fh:
                return GeoTiff(fh.read())
        except Exception:
            pass

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise DgmError("Höhendienst antwortet mit HTTP %s (%s)" % (exc.code, exc.reason))
    except (urllib.error.URLError, OSError) as exc:
        raise DgmError("Höhendienst nicht erreichbar: %s" % exc)

    tile = GeoTiff(data)
    if use_cache:
        try:
            with gzip.open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
    return tile


class HeightModel:
    """Sammlung geladener Kacheln mit gemeinsamer Höhenabfrage."""

    def __init__(self, endpoint, coverage, zone, spacing, tile_size=1000.0, use_cache=True,
                 axes=("E", "N"), fallback=None, kind="wcs"):
        self.kind = kind
        self.endpoint = endpoint
        self.coverage = coverage
        self.zone = zone
        self.axes = axes
        self.fallback = fallback        # weltweite Kacheln für Bereiche ohne Landesdaten
        self.from_fallback = 0
        self.spacing = spacing
        self.tile_size = tile_size
        self.use_cache = use_cache
        self.tiles = {}
        self.failed = 0

    @property
    def scale_factor(self):
        return min(1.0, 1.0 / max(1.0, self.spacing))

    def load(self, keys, progress=None):
        """Lädt die angegebenen Kacheln (Liste von (i, j))."""
        if self.fallback is not None:
            # passende weltweite Kacheln gleich mitladen, damit später kein
            # Netzzugriff im Hauptthread nötig ist
            wanted = set()
            for i, j in keys:
                for dx in (0.0, self.tile_size):
                    for dy in (0.0, self.tile_size):
                        lat, lon = utm_to_wgs84(i * self.tile_size + dx,
                                                j * self.tile_size + dy, self.zone)
                        wanted.add(self.fallback.tile_for(lat, lon))
            self.fallback.load(sorted(wanted), progress=progress)

        for index, (i, j) in enumerate(keys):
            if (i, j) in self.tiles:
                continue
            if progress:
                progress("Höhendaten: Kachel %d von %d" % (index + 1, len(keys)),
                         (index + 1) / float(len(keys) or 1))
            east, north = i * self.tile_size, j * self.tile_size
            try:
                if self.kind == "tiles":
                    self.tiles[(i, j)] = fetch_download_tile(
                        self.endpoint, east, north, self.tile_size, use_cache=self.use_cache)
                else:
                    self.tiles[(i, j)] = fetch_tile(
                        self.endpoint, self.coverage, east, north, self.tile_size,
                        self.scale_factor, use_cache=self.use_cache, axes=self.axes)
            except DgmError:
                self.tiles[(i, j)] = None
                self.failed += 1

    def tile_key(self, east, north):
        return int(east // self.tile_size), int(north // self.tile_size)

    def _tile_height(self, east, north):
        """Höhe aus den geladenen Kacheln - auch genau auf einer Kachelgrenze.

        An den Kachelgrenzen gehen zwei Dinge schief, die sonst je eine ganze
        Reihe aus dem Gelände schneiden: Ein Punkt genau auf der Grenze gehört
        rechnerisch zur nördlich anschließenden Kachel, liegt in deren Raster
        aber auf dem ausgeschlossenen Rand (GeoTIFF zählt die Zeilen von der
        Nordkante nach unten). Und zwischen der letzten Stützstelle einer Kachel
        und der ersten der Nachbarkachel bleibt ein Streifen von einem
        Rasterschritt ohne Wert. Deshalb werden die angrenzenden Kacheln
        mitgeprüft und notfalls deren Randwert genommen.
        """
        geprueft = []
        for d_east, d_north in ((0.0, 0.0), (0.0, -TILE_EDGE),
                                (-TILE_EDGE, 0.0), (-TILE_EDGE, -TILE_EDGE)):
            key = self.tile_key(east + d_east, north + d_north)
            if key in geprueft:
                continue
            geprueft.append(key)
            kachel = self.tiles.get(key)
            if kachel is not None:
                wert = kachel.height_at(east, north)
                if wert is not None:
                    return wert
        # Im Streifen zwischen zwei Kacheln liegt keine Stützstelle mehr; dort
        # gilt der Randwert der angrenzenden Kachel.
        for key in geprueft:
            kachel = self.tiles.get(key)
            if kachel is not None:
                wert = kachel.height_at(east, north, tolerance=1.0)
                if wert is not None:
                    return wert
        return None

    def height_at(self, east, north):
        value = self._tile_height(east, north)
        if value is None and self.fallback is not None:
            lat, lon = utm_to_wgs84(east, north, self.zone)
            value = self.fallback.height_at(lat, lon)
            if value is not None:
                self.from_fallback += 1
        return value


# =====================================================================
#  Weltweite Höhenkacheln als Ersatz, wo kein Landesdienst greift
# =====================================================================

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_CREDIT = ("Höhen: AWS Terrain Tiles (u. a. SRTM, EU-DEM) - "
                    "siehe registry.opendata.aws/terrain-tiles")


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(data):
    """Minimaler PNG-Leser für 8-Bit-Bilder ohne Interlacing.

    Rückgabe (Breite, Höhe, Kanäle, Pixelbytes). Reicht für die Höhenkacheln;
    Fremdbibliotheken sind in Blenders Python nicht verlässlich vorhanden.
    """
    import zlib
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DgmError("Höhenkachel ist keine PNG-Datei")
    pos = 8
    width = height = 0
    channels = 3
    idat = bytearray()
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            depth, colour = chunk[8], chunk[9]
            if depth != 8 or chunk[12] != 0:
                raise DgmError("Höhenkachel in nicht unterstütztem PNG-Format")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour, 3)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    offset = 0
    for row in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upper_left = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return width, height, channels, bytes(out)


class TerrainTiles:
    """Weltweite Höhenkacheln im Terrarium-Format (Höhe in RGB kodiert)."""

    def __init__(self, zoom=13, use_cache=True, tile_size=256):
        self.zoom = zoom
        self.use_cache = use_cache
        self.tile_size = tile_size
        self.tiles = {}
        self.failed = 0

    def tile_for(self, lat, lon):
        n = 2 ** self.zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return x, y

    def _path(self, x, y):
        folder = os.path.join(tempfile.gettempdir(), "blender_railway_chainage", "terrain")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "terrarium_%d_%d_%d.png" % (self.zoom, x, y))

    def load(self, keys, progress=None):
        keys = [k for k in keys if k not in self.tiles]
        for number, (x, y) in enumerate(keys):
            if progress and (number % 5 == 0 or number == len(keys) - 1):
                progress("Ersatz-Höhendaten: Kachel %d von %d" % (number + 1, len(keys)),
                         (number + 1) / float(len(keys) or 1))
            path = self._path(x, y)
            try:
                if not (self.use_cache and os.path.exists(path) and os.path.getsize(path)):
                    url = TERRARIUM_URL.format(z=self.zoom, x=x, y=y)
                    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(request, timeout=60) as response:
                        data = response.read()
                    with open(path, "wb") as handle:
                        handle.write(data)
                else:
                    with open(path, "rb") as handle:
                        data = handle.read()
                self.tiles[(x, y)] = decode_png(data)
            except Exception:                                     # noqa: BLE001
                self.tiles[(x, y)] = None
                self.failed += 1

    def _pixel(self, gx, gy):
        """Höhe an einer globalen Pixelkoordinate - auch über Kachelgrenzen hinweg."""
        tx, ty = int(math.floor(gx / self.tile_size)), int(math.floor(gy / self.tile_size))
        tile = self.tiles.get((tx, ty))
        if tile is None:
            return None
        width, height, channels, pixels = tile
        col = int((gx - tx * self.tile_size) * width / self.tile_size)
        row = int((gy - ty * self.tile_size) * height / self.tile_size)
        col = min(width - 1, max(0, col))
        row = min(height - 1, max(0, row))
        index = (row * width + col) * channels
        return (pixels[index] * 256.0 + pixels[index + 1]
                + pixels[index + 2] / 256.0) - 32768.0

    @staticmethod
    def _catmull_rom(p0, p1, p2, p3, t):
        """Weiche Interpolation zwischen p1 und p2 (Catmull-Rom-Spline)."""
        return 0.5 * ((2.0 * p1)
                      + (-p0 + p2) * t
                      + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t
                      + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t)

    def height_at(self, lat, lon):
        """Höhe in Metern über Normalnull, oder None.

        Zwischen den Rasterpunkten wird bikubisch interpoliert. Die Quelldaten
        liegen in ganzen Metern auf einem rund 30 m weiten Raster vor - ohne
        weiche Interpolation entstünden daraus sichtbare Geländestufen.
        """
        n = 2 ** self.zoom * self.tile_size
        gx = (lon + 180.0) / 360.0 * n
        gy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n

        fx, fy = gx - 0.5, gy - 0.5
        x0, y0 = math.floor(fx), math.floor(fy)
        tx, ty = fx - x0, fy - y0

        zeilen = []
        for dy in (-1, 0, 1, 2):
            werte = [self._pixel(x0 + dx, y0 + dy) for dx in (-1, 0, 1, 2)]
            if any(v is None for v in werte):
                # am Rand der geladenen Kacheln bilinear bzw. direkt abtasten
                nahe = self._pixel(gx, gy)
                return nahe
            zeilen.append(self._catmull_rom(werte[0], werte[1], werte[2], werte[3], tx))
        return self._catmull_rom(zeilen[0], zeilen[1], zeilen[2], zeilen[3], ty)


def utm_zone(lon):
    """UTM-Zone zu einer geografischen Länge (Mitteleuropa: 32 bzw. 33)."""
    return max(1, min(60, int(math.floor((lon + 180.0) / 6.0)) + 1))


class FlatModel:
    """Ebenes „Höhenmodell“ - liefert überall dieselbe Höhe.

    Es hat dieselbe Schnittstelle wie :class:`HeightModel`, lädt aber nichts.
    Damit lässt sich die vorhandene Geländeerzeugung unverändert für eine ebene
    Fläche nutzen - etwa als Arbeitsgrundlage, wenn für das Gebiet kein
    amtliches Höhenmodell vorliegt oder es schlicht nicht gebraucht wird.
    """

    def __init__(self, height=0.0, zone=32, tile_size=1000.0):
        self.height = float(height)
        self.zone = zone
        self.tile_size = tile_size
        self.tiles = {}
        self.failed = 0
        self.from_fallback = 0
        self.kind = "flat"

    def load(self, keys, progress=None):
        if progress:
            progress("Ebenes Gelände - es werden keine Höhendaten geladen", 1.0)

    def tile_key(self, east, north):
        return int(east // self.tile_size), int(north // self.tile_size)

    def height_at(self, east, north):
        return self.height

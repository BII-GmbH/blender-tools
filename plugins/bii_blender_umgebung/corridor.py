"""Korridor entlang der Kilometrierungslinie und daraus abgeleitetes Geländegitter.

Der Korridor ist die Menge aller Punkte, die höchstens ``radius`` Meter von der
Kilometrierungslinie entfernt liegen. Er dient zweierlei: dem Zuschnitt einer
vorhandenen Szene und dem Aufbau eines Geländemodells, das nur dort erzeugt
wird, wo es gebraucht wird.
"""

import math

from . import dgm
from .railgeom import SegmentIndex


class Corridor:
    """Punktmenge im Abstand ``radius`` um eine Polylinie."""

    def __init__(self, points, radius, cell=None):
        self.points = list(points)
        self.radius = radius
        self.index = SegmentIndex(self.points, cell=cell or max(25.0, radius))
        self.stations = [0.0]
        for i in range(1, len(self.points)):
            self.stations.append(self.stations[-1] + math.dist(self.points[i - 1], self.points[i]))

    @property
    def length(self):
        return self.stations[-1]

    def locate(self, x, y):
        """Rueckgabe (Station, seitlicher Abstand mit Vorzeichen) oder None.

        Positive Werte liegen links der Achse (in Richtung aufsteigender Station).
        """
        hit = self.index.nearest_ex(x, y, self.radius)
        if hit is None:
            return None
        (qx, qy), dist, (tx, ty), segment, t = hit
        station = self.stations[segment] + t * (self.stations[segment + 1] - self.stations[segment])
        side = (x - qx) * (-ty) + (y - qy) * tx
        return station, math.copysign(dist, side if side else 1.0)

    @property
    def bounds(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs) - self.radius, min(ys) - self.radius,
                max(xs) + self.radius, max(ys) + self.radius)

    def distance(self, x, y):
        hit = self.index.nearest(x, y, self.radius)
        return hit[1] if hit else None

    def contains(self, x, y):
        return self.index.nearest(x, y, self.radius) is not None


def to_utm(points, projection, zone):
    """Polylinie aus lokalen Blender-Koordinaten nach UTM."""
    result = []
    for x, y in points:
        lat, lon = projection.to_geographic(x, y)
        result.append(dgm.wgs84_to_utm(lat, lon, zone))
    return result


def grid_points(corridor_utm, spacing):
    """Gitterpunkte (Spalte, Zeile) -> (East, North) innerhalb des Korridors.

    Geprüft werden nur Zellen in Reichweite der einzelnen Achsabschnitte - bei
    langen, schräg verlaufenden Strecken ist die Bounding Box des Korridors
    sonst um ein Vielfaches größer als der Korridor selbst.
    """
    radius = corridor_utm.radius
    points = corridor_utm.points
    wanted = {}
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        col0 = int(math.floor((min(x0, x1) - radius) / spacing))
        col1 = int(math.ceil((max(x0, x1) + radius) / spacing))
        row0 = int(math.floor((min(y0, y1) - radius) / spacing))
        row1 = int(math.ceil((max(y0, y1) + radius) / spacing))
        for row in range(row0, row1 + 1):
            north = row * spacing
            for col in range(col0, col1 + 1):
                key = (col, row)
                if key in wanted:
                    continue
                east = col * spacing
                if corridor_utm.contains(east, north):
                    wanted[key] = (east, north)
    return wanted


def tiles_for(wanted, model):
    """Kacheln, die für die Gitterpunkte gebraucht werden.

    Punkte genau auf einer Kachelgrenze holen ihren Wert aus der angrenzenden
    Kachel (siehe ``HeightModel._tile_height``) - die muss deshalb mitgeladen
    werden.
    """
    rand = dgm.TILE_EDGE
    return sorted({model.tile_key(e + de, n + dn)
                   for e, n in wanted.values()
                   for de, dn in ((0.0, 0.0), (0.0, -rand), (-rand, 0.0), (-rand, -rand))})


def build_grid(wanted, model, projection, zone, progress=None, area=None):
    """Erzeugt aus den Gitterpunkten Vertices (lokale Koordinaten) und Quads.

    Wird ``area`` (der Corridor in UTM) übergeben, enthält die Rückgabe
    zusätzlich je Vertex die Korridorkoordinaten (Station, seitlicher Abstand) -
    sie dienen als UV-Grundlage für eine Textur, die der Strecke folgt.

    Rückgabe: (Vertices, Quads, Anzahl ohne Höhenwert, Korridorkoordinaten)
    """
    if not wanted:
        return [], [], 0, []
    if progress:
        progress("Geländegitter: %d Punkte" % len(wanted), None)

    verts = []
    coords = []
    vertex_index = {}
    missing = 0
    for key in sorted(wanted):
        east, north = wanted[key]
        height = model.height_at(east, north)
        if height is None:
            missing += 1
            continue
        lat, lon = dgm.utm_to_wgs84(east, north, zone)
        x, y = projection.from_geographic(lat, lon)
        vertex_index[key] = len(verts)
        verts.append((x, y, height))
        if area is not None:
            located = area.locate(east, north)
            coords.append(located if located else (0.0, 0.0))

    faces = []
    for (col, row), index in vertex_index.items():
        right = vertex_index.get((col + 1, row))
        up = vertex_index.get((col, row + 1))
        diagonal = vertex_index.get((col + 1, row + 1))
        if right is not None and up is not None and diagonal is not None:
            faces.append((index, right, diagonal, up))
    return verts, faces, missing, coords


def terrain_grid(corridor_utm, model, spacing, projection, zone, progress=None):
    """Gitterpunkte bestimmen, Kacheln laden und Gelände aufbauen - in einem Schritt."""
    wanted = grid_points(corridor_utm, spacing)
    if not wanted:
        return [], [], 0, []
    model.load(tiles_for(wanted, model), progress=progress)
    return build_grid(wanted, model, projection, zone, progress=progress, area=corridor_utm)


# ------------------------------------------------- Gebiet als Fläche

def area_bounds_utm(bbox, zone, reserve=0.0):
    """Umschließendes UTM-Rechteck einer geografischen Bounding Box."""
    min_lat, min_lon, max_lat, max_lon = bbox
    ecken = [dgm.wgs84_to_utm(lat, lon, zone)
             for lat in (min_lat, max_lat) for lon in (min_lon, max_lon)]
    east0 = min(e for e, _ in ecken) - reserve
    east1 = max(e for e, _ in ecken) + reserve
    north0 = min(n for _, n in ecken) - reserve
    north1 = max(n for _, n in ecken) + reserve
    return east0, north0, east1, north1


def area_grid(bbox, zone, spacing, model, projection, progress=None):
    """Geländegitter über ein Gebiet (statt entlang einer Strecke).

    Rückgabe: (Vertices in lokalen Koordinaten, Quads, UV je Vertex,
    Anzahl der Punkte ohne Höhenwert)
    """
    east0, north0, east1, north1 = area_bounds_utm(bbox, zone)
    east0 = math.floor(east0 / spacing) * spacing
    north0 = math.floor(north0 / spacing) * spacing
    columns = int((east1 - east0) / spacing) + 1
    rows = int((north1 - north0) / spacing) + 1

    keys = sorted({(int((east0 + c * spacing) // model.tile_size),
                    int((north0 + r * spacing) // model.tile_size))
                   for c in (0, columns - 1) for r in (0, rows - 1)} |
                  {(int((east0 + c * spacing) // model.tile_size),
                    int((north0 + r * spacing) // model.tile_size))
                   for c in range(0, columns, max(1, int(model.tile_size / spacing)))
                   for r in range(0, rows, max(1, int(model.tile_size / spacing)))})
    model.load(keys, progress=progress)

    if progress:
        progress("Geländegitter: %d x %d Punkte" % (columns, rows), None)

    width = max(1e-6, (columns - 1) * spacing)
    height = max(1e-6, (rows - 1) * spacing)
    verts, uvs, index = [], [], {}
    missing = 0
    for row in range(rows):
        north = north0 + row * spacing
        for col in range(columns):
            east = east0 + col * spacing
            value = model.height_at(east, north)
            if value is None:
                missing += 1
                continue
            lat, lon = dgm.utm_to_wgs84(east, north, zone)
            x, y = projection.from_geographic(lat, lon)
            index[(col, row)] = len(verts)
            verts.append((x, y, value))
            uvs.append(((east - east0) / width, (north - north0) / height))

    faces = []
    for (col, row), i in index.items():
        right = index.get((col + 1, row))
        up = index.get((col, row + 1))
        diagonal = index.get((col + 1, row + 1))
        if right is not None and up is not None and diagonal is not None:
            faces.append((i, right, diagonal, up))
    return verts, faces, uvs, missing, (east0, north0, east0 + width, north0 + height)


def area_bounds_local(bbox, projection, spacing=0.0):
    """Achsparalleles Rechteck der Szene, das die Bounding Box umschließt.

    Das Gelände wird entlang dieser Achsen aufgebaut - nicht entlang der
    UTM-Achsen. Ein UTM-Gitter steht in der Szene sonst schräg: Gitternord der
    UTM-Zone und Nord der Szenenprojektion unterscheiden sich um die
    Meridiankonvergenz, und die wächst mit dem Abstand zum Mittelmeridian (in
    Bayern östlich von 12° sind es mehrere Grad, weil dort das ganze Land in
    Zone 32 geführt wird).
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    ecken = [projection.from_geographic(lat, lon)
             for lat in (min_lat, max_lat) for lon in (min_lon, max_lon)]
    x0 = min(p[0] for p in ecken)
    x1 = max(p[0] for p in ecken)
    y0 = min(p[1] for p in ecken)
    y1 = max(p[1] for p in ecken)
    if spacing > 0.0:
        # auf ganze Rasterschritte aufrunden, damit die Kanten gerade liegen
        x1 = x0 + math.ceil((x1 - x0) / spacing) * spacing
        y1 = y0 + math.ceil((y1 - y0) / spacing) * spacing
    return x0, y0, x1, y1


def local_bounds_utm(local, projection, zone, reserve=0.0):
    """UTM-Rechteck, das ein achsparalleles Szenenrechteck abdeckt."""
    x0, y0, x1, y1 = local
    ecken = []
    for x in (x0, x1):
        for y in (y0, y1):
            lat, lon = projection.to_geographic(x, y)
            ecken.append(dgm.wgs84_to_utm(lat, lon, zone))
    return (min(e for e, _ in ecken) - reserve, min(n for _, n in ecken) - reserve,
            max(e for e, _ in ecken) + reserve, max(n for _, n in ecken) + reserve)


def area_grid_local(local, zone, spacing, model, projection, progress=None):
    """Geländegitter über ein Gebiet - achsparallel zur Szene.

    Rückgabe: (Vertices in lokalen Koordinaten, Quads, UV je Vertex,
    Anzahl der Punkte ohne Höhenwert, das benutzte Rechteck)
    """
    x0, y0, x1, y1 = local
    columns = max(2, int(round((x1 - x0) / spacing)) + 1)
    rows = max(2, int(round((y1 - y0) / spacing)) + 1)
    if progress:
        progress("Geländegitter: %d x %d Punkte" % (columns, rows), None)

    width = max(1e-6, (columns - 1) * spacing)
    height = max(1e-6, (rows - 1) * spacing)
    verts, uvs, index = [], [], {}
    missing = 0
    for row in range(rows):
        y = y0 + row * spacing
        for col in range(columns):
            x = x0 + col * spacing
            lat, lon = projection.to_geographic(x, y)
            east, north = dgm.wgs84_to_utm(lat, lon, zone)
            value = model.height_at(east, north)
            if value is None:
                missing += 1
                continue
            index[(col, row)] = len(verts)
            verts.append((x, y, value))
            uvs.append(((x - x0) / width, (y - y0) / height))

    faces = []
    for (col, row), i in index.items():
        right = index.get((col + 1, row))
        up = index.get((col, row + 1))
        diagonal = index.get((col + 1, row + 1))
        if right is not None and up is not None and diagonal is not None:
            faces.append((i, right, diagonal, up))
    return verts, faces, uvs, missing, (x0, y0, x0 + width, y0 + height)


# ------------------------------------------------- Stückweiser Aufbau
#
# Große Gebiete und lange Korridore werden nicht mehr als ein Gitter aufgebaut,
# sondern Stück für Stück: Je Stück werden nur die dafür nötigen Höhenkacheln
# geladen, das Mesh entsteht direkt aus numpy-Arrays, und danach wird der
# Speicher wieder frei. So hängt der Spitzenverbrauch an der Stückgröße, nicht
# an der Gebietsgröße.

import numpy as np  # noqa: E402

# Höchstzahl Rasterschritte je Stückkante (1000 x 1000 = 1 Mio Punkte je Stück)
MAX_CHUNK_CELLS = 1000


def tile_keys(east, north, model):
    """Wie :func:`tiles_for`, aber für numpy-Arrays von Gitterpunkten."""
    rand = dgm.TILE_EDGE
    size = model.tile_size
    codes = set()
    for de, dn in ((0.0, 0.0), (0.0, -rand), (-rand, 0.0), (-rand, -rand)):
        ti = np.floor((east + de) / size).astype(np.int64)
        tj = np.floor((north + dn) / size).astype(np.int64)
        codes.update(np.unique(dgm.tile_code(ti, tj)).tolist())
    return sorted(dgm.tile_decode(c) for c in codes)


def grid_quads(valid):
    """Vierecke eines regelmäßigen Gitters (Zeilen x Spalten), dessen Punkte ``valid``
    sind. Rückgabe (neue Vertexnummer je Gitterpunkt oder -1, Vierecke F x 4)."""
    rows, cols = valid.shape
    index = np.full(valid.shape, -1, dtype=np.int64)
    index[valid] = np.arange(int(valid.sum()))
    a = index[:-1, :-1]
    b = index[:-1, 1:]
    c = index[1:, 1:]
    d = index[1:, :-1]
    ok = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
    quads = np.stack((a[ok], b[ok], c[ok], d[ok]), axis=1)
    return index, quads


def area_chunks(local, spacing, chunk_size):
    """Teilt das Szenenrechteck in Stücke. Rückgabe (Spalten, Zeilen, Stücke).

    Jedes Stück ist (c0, c1, r0, r1) - Gitterindizes einschließlich der
    Endpunkte; benachbarte Stücke teilen sich die Randreihe, damit das Gelände
    ohne Lücke durchläuft.
    """
    x0, y0, x1, y1 = local
    columns = max(2, int(round((x1 - x0) / spacing)) + 1)
    rows = max(2, int(round((y1 - y0) / spacing)) + 1)
    per = max(2, min(MAX_CHUNK_CELLS, int(round(chunk_size / spacing))))

    def grenzen(zellen):
        # gleichmäßig verteilt - kein schmaler Reststreifen am Rand
        anzahl = max(1, int(math.ceil(zellen / float(per))))
        return np.round(np.linspace(0, zellen, anzahl + 1)).astype(int).tolist()

    spalten, zeilen = grenzen(columns - 1), grenzen(rows - 1)
    chunks = [(c0, c1, r0, r1)
              for r0, r1 in zip(zeilen[:-1], zeilen[1:])
              for c0, c1 in zip(spalten[:-1], spalten[1:])]
    return columns, rows, chunks


def area_chunk(local, spacing, chunk, projection, zone):
    """Gitterpunkte eines Stücks: lokale x/y und UTM east/north (Zeilen x Spalten)."""
    x0, y0 = local[0], local[1]
    c0, c1, r0, r1 = chunk
    xs = x0 + np.arange(c0, c1 + 1) * spacing
    ys = y0 + np.arange(r0, r1 + 1) * spacing
    gx, gy = np.meshgrid(xs, ys)
    lat, lon = projection.to_geographic_array(gx, gy)
    east, north = dgm.wgs84_to_utm_array(lat, lon, zone)
    return gx, gy, east, north


def corridor_segment_count(length, segment_length):
    if segment_length and segment_length > 0.0 and length > segment_length:
        return max(1, int(math.ceil(length / segment_length)))
    return 1


def corridor_segment(area, spacing, index, count):
    """Gitter eines Korridorabschnitts (Stationen ``index``/``count`` der Achse).

    Geprüft werden nur Punkte in der Nähe des Achsstücks; zugeordnet wird jede
    Fläche nach der Station ihrer Mitte, damit die Abschnitte lückenlos
    aneinandergrenzen. Rückgabe (east, north, Korridorkoordinaten N x 2,
    Vierecke F x 4) - Vertices ohne Höhen.
    """
    from .railgeom import slice_by_stations
    total = area.length
    span = total / count if count else total
    rand = area.radius + 2.0 * spacing
    stueck = slice_by_stations(area.points, area.stations,
                               index * span - rand, (index + 1) * span + rand)
    if len(stueck) < 2:
        return None
    kandidaten = grid_points(Corridor(stueck, area.radius), spacing)
    if not kandidaten:
        return None
    schluessel = sorted(kandidaten)
    nummer = {key: i for i, key in enumerate(schluessel)}
    coords = np.zeros((len(schluessel), 2))
    for i, key in enumerate(schluessel):
        located = area.locate(*kandidaten[key])
        if located:
            coords[i] = located

    quads = []
    for (col, row), i in nummer.items():
        right = nummer.get((col + 1, row))
        up = nummer.get((col, row + 1))
        diagonal = nummer.get((col + 1, row + 1))
        if right is not None and up is not None and diagonal is not None:
            quads.append((i, right, diagonal, up))
    if not quads:
        return None
    quads = np.array(quads, dtype=np.int64)
    middle = coords[quads, 0].mean(axis=1)
    zugehoerig = np.clip((middle / span).astype(np.int64), 0, count - 1) if span else \
        np.zeros(len(quads), dtype=np.int64)
    quads = quads[zugehoerig == index]
    if not len(quads):
        return None
    east = np.array([kandidaten[k][0] for k in schluessel])
    north = np.array([kandidaten[k][1] for k in schluessel])
    return east, north, coords, quads


def compact(quads, keep):
    """Nur Vierecke, deren Ecken alle ``keep`` sind; Vertices neu durchnummeriert.

    Rückgabe (Vertexauswahl, Vierecke mit neuen Nummern)."""
    quads = quads[keep[quads].all(axis=1)]
    used = np.zeros(len(keep), dtype=bool)
    used[quads.reshape(-1)] = True
    neu = np.full(len(keep), -1, dtype=np.int64)
    neu[used] = np.arange(int(used.sum()))
    return used, neu[quads]

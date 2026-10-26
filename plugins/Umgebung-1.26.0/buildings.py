"""Gebäudegrundrisse aus OpenStreetMap auswerten.

Aus der Overpass-Antwort entstehen Grundrisse in lokalen Blender-Koordinaten
samt Bauhöhe. Die Höhe kommt - in dieser Reihenfolge - aus ``height``,
``building:height``, ``building:levels`` (mal Geschosshöhe) und andernfalls aus
dem eingestellten Vorgabewert. ``min_height`` bzw. ``building:min_level``
beschreiben Bauteile, die erst über dem Boden beginnen (etwa Auskragungen).

Das Modul kommt ohne bpy aus; das Mesh baut :mod:`builder`.
"""

import math
import re

# Vorgaben, wenn das Gebäude keine Höhenangabe trägt
DEFAULT_HEIGHT = 9.0
DEFAULT_LEVEL_HEIGHT = 3.0

_ZAHL = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(m|meter|metre|ft|')?\s*$", re.IGNORECASE)


class Building:
    """Ein Grundriss mit Bauhöhe (alles in Metern, lokale Koordinaten)."""

    __slots__ = ("points", "height", "min_height", "tags", "osm_id")

    def __init__(self, points, height, min_height=0.0, tags=None, osm_id=0):
        self.points = points
        self.height = height
        self.min_height = min_height
        self.tags = tags or {}
        self.osm_id = osm_id

    @property
    def area(self):
        """Grundfläche in m² (Gaußsche Trapezformel)."""
        return abs(signed_area(self.points))

    @property
    def center(self):
        return (sum(p[0] for p in self.points) / len(self.points),
                sum(p[1] for p in self.points) / len(self.points))


def signed_area(points):
    total = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def parse_length(value):
    """„12“, „12 m“, „12,5m“ oder „40'“ in Meter - oder None."""
    if value is None:
        return None
    match = _ZAHL.match(str(value))
    if not match:
        return None
    try:
        zahl = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    einheit = (match.group(2) or "").lower()
    if einheit in ("ft", "'"):
        zahl *= 0.3048
    return zahl


def height_from_tags(tags, default_height=DEFAULT_HEIGHT,
                     level_height=DEFAULT_LEVEL_HEIGHT):
    """(Bauhöhe, Unterkante) aus den OSM-Merkmalen."""
    hoehe = parse_length(tags.get("height"))
    if hoehe is None:
        hoehe = parse_length(tags.get("building:height"))
    if hoehe is None:
        geschosse = parse_length(tags.get("building:levels"))
        if geschosse is not None:
            hoehe = geschosse * level_height
            dach = parse_length(tags.get("roof:height"))
            if dach is not None:
                hoehe += dach
    if hoehe is None or hoehe <= 0.0:
        hoehe = default_height

    unten = parse_length(tags.get("min_height"))
    if unten is None:
        ebene = parse_length(tags.get("building:min_level"))
        unten = ebene * level_height if ebene is not None else 0.0
    return hoehe, max(0.0, min(unten, hoehe - 0.5))


def _ring(way, nodes, projection):
    """Geschlossener Ring eines Weges in lokalen Koordinaten - oder None."""
    ids = way.get("nodes") or ()
    if len(ids) < 4:
        return None
    if ids[0] == ids[-1]:
        ids = ids[:-1]
    punkte = []
    for node_id in ids:
        koordinate = nodes.get(node_id)
        if koordinate is None:
            return None
        punkte.append(projection.from_geographic(koordinate[0], koordinate[1]))
    return punkte if len(punkte) >= 3 else None


def parse(data, projection, default_height=DEFAULT_HEIGHT,
          level_height=DEFAULT_LEVEL_HEIGHT, min_area=0.0, bbox_filter=None):
    """Overpass-Antwort -> Liste von :class:`Building`.

    ``bbox_filter`` ist eine Funktion (x, y) -> bool; sie entscheidet anhand des
    Schwerpunkts, ob ein Gebäude übernommen wird.
    """
    nodes, ways, relations = {}, {}, []
    for element in data.get("elements", ()):
        art = element.get("type")
        if art == "node":
            nodes[element["id"]] = (element["lat"], element["lon"])
        elif art == "way":
            ways[element["id"]] = element
        elif art == "relation":
            relations.append(element)

    ergebnis = []
    aus_relation = set()

    for relation in relations:
        tags = relation.get("tags") or {}
        if "building" not in tags:
            continue
        hoehe, unten = height_from_tags(tags, default_height, level_height)
        for member in relation.get("members", ()):
            if member.get("type") != "way":
                continue
            aus_relation.add(member.get("ref"))
            if member.get("role") not in ("outer", ""):
                continue
            weg = ways.get(member.get("ref"))
            if weg is None:
                continue
            punkte = _ring(weg, nodes, projection)
            if punkte:
                ergebnis.append(Building(punkte, hoehe, unten, tags, relation["id"]))

    for way_id, weg in ways.items():
        tags = weg.get("tags") or {}
        if "building" not in tags and "building:part" not in tags:
            continue
        if way_id in aus_relation and "building" not in tags:
            continue
        punkte = _ring(weg, nodes, projection)
        if not punkte:
            continue
        hoehe, unten = height_from_tags(tags, default_height, level_height)
        ergebnis.append(Building(punkte, hoehe, unten, tags, way_id))

    gefiltert = []
    for gebaeude in ergebnis:
        if min_area and gebaeude.area < min_area:
            continue
        if bbox_filter is not None and not bbox_filter(*gebaeude.center):
            continue
        # gegen den Uhrzeigersinn, damit die Deckfläche nach oben zeigt
        if signed_area(gebaeude.points) < 0.0:
            gebaeude.points.reverse()
        gefiltert.append(gebaeude)
    return gefiltert


def statistics(buildings):
    """Kurze Kennzahlen für die Rückmeldung im Panel."""
    if not buildings:
        return {"anzahl": 0, "flaeche": 0.0, "hoehe": 0.0, "mit_angabe": 0}
    mit_angabe = sum(1 for b in buildings
                     if b.tags.get("height") or b.tags.get("building:levels")
                     or b.tags.get("building:height"))
    return {"anzahl": len(buildings),
            "flaeche": sum(b.area for b in buildings),
            "hoehe": sum(b.height for b in buildings) / len(buildings),
            "mit_angabe": mit_angabe}


def bbox_test(projection, bbox):
    """Prüffunktion (x, y) -> bool für das geografische Rechteck."""
    min_lat, min_lon, max_lat, max_lon = bbox

    def innerhalb(x, y):
        lat, lon = projection.to_geographic(x, y)
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

    return innerhalb


def total_vertices(buildings):
    """Anzahl der Eckpunkte, die das Mesh bekommen würde."""
    return sum(2 * len(b.points) for b in buildings)


def footprint_bounds(buildings):
    if not buildings:
        return None
    xs = [p[0] for b in buildings for p in b.points]
    ys = [p[1] for b in buildings for p in b.points]
    return min(xs), min(ys), max(xs), max(ys)


def distance(a, b):
    return math.dist(a, b)

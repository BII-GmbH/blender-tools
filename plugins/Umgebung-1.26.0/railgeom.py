"""Auswertung der OSM-Bahndaten: Strecken gruppieren, Gleisachsen verketten,
Kilometrierung aus railway:position ableiten und Markenpositionen berechnen.

Dieses Modul ist bewusst frei von bpy-Abhaengigkeiten und damit eigenstaendig
testbar.
"""

import math
import re

# railway:position kann als "105.2", "105,2", "km 105.2" oder "105+200" vorkommen
_POS_RE = re.compile(
    r"^\s*(?:km\s*)?(-?\d+(?:[.,]\d+)?)(?:\s*\+\s*(\d+(?:[.,]\d+)?))?\s*(?:km)?\s*$",
    re.IGNORECASE,
)

MILESTONE_KEYS = ("railway:position", "railway:position:exact")


def parse_positions(tags):
    """Liest alle plausiblen Kilometerangaben eines Milestones (in km)."""
    values = []
    for key in MILESTONE_KEYS:
        raw = tags.get(key)
        if not raw:
            continue
        for token in str(raw).split(";"):
            m = _POS_RE.match(token)
            if not m:
                continue
            km = float(m.group(1).replace(",", "."))
            if m.group(2):
                # Stationierungsschreibweise 105+200 -> 105,200 km
                km += float(m.group(2).replace(",", ".")) / 1000.0
            if km not in values:
                values.append(km)
    return values


class RailData:
    """Projizierte Rohdaten eines abgefragten Gebiets."""

    def __init__(self):
        self.nodes = {}       # node-id -> (x, y)
        self.node_tags = {}   # Originale OSM-Namen und Referenzen behalten
        self.ways = {}        # way-id  -> {"nodes": [...], "tags": {...}}
        self.relations = []   # [{"tags":…, "ways": set()}]
        self.milestones = []  # [{"id":…, "x":…, "y":…, "km": [werte]}]
        self.stations = []    # [{"id":…, "x":…, "y":…, "name":…, "art":…}]

    # ------------------------------------------------------------------ Laden

    @classmethod
    def from_overpass(cls, data, projection, track_types=None, skip_service=True):
        self = cls()
        track_types = set(track_types) if track_types else None
        for el in data.get("elements", ()):
            etype = el.get("type")
            if etype == "node":
                x, y = projection.from_geographic(el["lat"], el["lon"])
                self.nodes[el["id"]] = (x, y)
                tags = el.get("tags") or {}
                self.node_tags[el["id"]] = dict(tags)
                if tags.get("railway") in ("milestone", "km_post"):
                    km = parse_positions(tags)
                    if km:
                        self.milestones.append({"id": el["id"], "x": x, "y": y, "km": km})
                elif tags.get("railway") in ("station", "halt"):
                    self.stations.append({
                        "id": el["id"], "x": x, "y": y,
                        "name": tags.get("name") or "unbenannte Betriebsstelle",
                        "art": "Haltepunkt" if tags.get("railway") == "halt" else "Bahnhof",
                    })
            elif etype == "way":
                tags = el.get("tags") or {}
                if track_types and tags.get("railway") not in track_types:
                    continue
                if skip_service and tags.get("service") and tags.get("service") != "crossover":
                    # Rangier-, Abstell- und Anschlussgleise nur verwerfen, wenn sie
                    # auch nicht als Bahnhofsgleise gebraucht werden
                    continue
                nodes = el.get("nodes") or []
                if len(nodes) >= 2:
                    self.ways[el["id"]] = {"nodes": nodes, "tags": tags}
            elif etype == "relation":
                tags = el.get("tags") or {}
                if tags.get("route") not in ("railway", "tracks"):
                    continue
                members = {m["ref"] for m in el.get("members", ()) if m.get("type") == "way"}
                if members:
                    self.relations.append({"tags": tags, "ways": members})
        return self

    # ------------------------------------------------------------- Hilfsmittel

    def way_length(self, way_id):
        pts = [self.nodes[n] for n in self.ways[way_id]["nodes"] if n in self.nodes]
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


class Route:
    """Eine Strecke - alles, was zur selben Streckennummer / zum selben Namen gehoert."""

    def __init__(self, key, ref="", name=""):
        self.key = key
        self.ref = ref
        self.name = name
        self.way_ids = []
        self.length = 0.0
        self.is_station = False   # True bei Gleisgruppen einer Betriebsstelle
        self.usage_lengths = {}   # usage-Tag -> Laenge, fuer Haupt-/Nebenstrecke

    @property
    def usage(self):
        """Haeufigste Nutzungsart der Gleise (main = Hauptstrecke)."""
        if not self.usage_lengths:
            return ""
        return max(self.usage_lengths.items(), key=lambda kv: kv[1])[0]

    @property
    def label(self):
        if self.ref and self.name:
            return "%s  %s" % (self.ref, self.name)
        return self.ref or self.name or "ohne Bezeichnung"


def group_routes(rail, use_relations=True, skip_service=True):
    """Gruppiert die Gleis-Ways zu Strecken.

    Primaerkriterium ist das Way-Tag ``ref`` (die Streckennummer, auf die sich
    die Kilometrierung bezieht), danach ``name``, danach eine Streckenrelation
    (route=railway), sonst eine Sammelgruppe.
    """
    way_to_rel = {}
    if use_relations:
        for rel in rail.relations:
            tags = rel["tags"]
            key = tags.get("ref") or tags.get("name")
            if not key:
                continue
            for wid in rel["ways"]:
                way_to_rel.setdefault(wid, (tags.get("ref", ""), tags.get("name", "")))

    routes = {}
    for wid, way in rail.ways.items():
        tags = way["tags"]
        if skip_service and tags.get("service"):
            # Rangier-, Abstell- und Anschlussgleise gehören zu keiner Strecke
            continue
        ref = (tags.get("ref") or "").strip()
        name = (tags.get("name") or "").strip()
        if ref:
            key = "ref:%s" % ref
        elif name:
            key = "name:%s" % name
        elif wid in way_to_rel:
            ref, name = way_to_rel[wid]
            key = "rel:%s" % (ref or name)
        else:
            service = tags.get("service")
            key = "sonstige:%s" % (service or tags.get("railway", "rail"))
            name = "sonstige Gleise (%s)" % (service or tags.get("railway", "rail"))

        route = routes.get(key)
        if route is None:
            route = routes[key] = Route(key, ref, name)
        route.way_ids.append(wid)
        length = rail.way_length(wid)
        route.length += length
        usage = tags.get("usage", "")
        route.usage_lengths[usage] = route.usage_lengths.get(usage, 0.0) + length

    return sorted(routes.values(), key=lambda r: -r.length)


def group_station_tracks(rail, radius=600.0, min_length=30.0):
    """Fasst die Gleise im Bereich einer Betriebsstelle zu einer Gruppe zusammen.

    Zugeordnet wird nach dem naechstgelegenen Bahnhof bzw. Haltepunkt; Gleise ohne
    Betriebsstelle in Reichweite bleiben unberuecksichtigt.
    """
    if not rail.stations:
        return []

    index = {}
    for station in rail.stations:
        index.setdefault((int(station["x"] // radius), int(station["y"] // radius)),
                         []).append(station)

    def nearest(x, y):
        cx, cy = int(x // radius), int(y // radius)
        best, best_dist = None, radius
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for station in index.get((cx + dx, cy + dy), ()):
                    dist = math.hypot(station["x"] - x, station["y"] - y)
                    if dist < best_dist:
                        best, best_dist = station, dist
        return best

    gruppen = {}
    for wid, way in rail.ways.items():
        if not way["tags"].get("service"):
            # durchgehende Streckengleise gehören zur Strecke, nicht zur Betriebsstelle
            continue
        points = [rail.nodes[n] for n in way["nodes"] if n in rail.nodes]
        if len(points) < 2:
            continue
        laenge = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        if laenge < min_length:
            continue
        mitte = points[len(points) // 2]
        station = nearest(mitte[0], mitte[1])
        if station is None:
            continue
        key = "bf:%s" % station["id"]
        gruppe = gruppen.get(key)
        if gruppe is None:
            gruppe = gruppen[key] = Route(key, "", "%s %s" % (station["art"], station["name"]))
            gruppe.is_station = True
        gruppe.way_ids.append(wid)
        gruppe.length += laenge
    for gruppe in gruppen.values():
        gruppe.usage_lengths = {"station": gruppe.length}
    return sorted(gruppen.values(), key=lambda g: -g.length)


# --------------------------------------------------------------- Verkettung

def _angle_between(d1, d2):
    dot = max(-1.0, min(1.0, d1[0] * d2[0] + d1[1] * d2[1]))
    return math.degrees(math.acos(dot))


def _unit(p_from, p_to):
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        return None
    return dx / length, dy / length


def build_chains(rail, way_ids, max_angle=75.0):
    """Verkettet einzelne Ways zu moeglichst langen, durchgehenden Gleisachsen.

    An Weichen/Abzweigen wird der geradlinigste Anschluss gewaehlt; ist kein
    Anschluss flacher als ``max_angle``, endet die Kette.
    Rueckgabe: Liste von Knoten-ID-Listen, nach Laenge absteigend sortiert.
    """
    ways = []
    for wid in way_ids:
        nodes = [n for n in rail.ways[wid]["nodes"] if n in rail.nodes]
        # aufeinanderfolgende Duplikate entfernen
        cleaned = [n for i, n in enumerate(nodes) if i == 0 or n != nodes[i - 1]]
        if len(cleaned) >= 2:
            ways.append(cleaned)

    adjacency = {}
    for i, nodes in enumerate(ways):
        adjacency.setdefault(nodes[0], []).append(i)
        adjacency.setdefault(nodes[-1], []).append(i)

    used = [False] * len(ways)
    pt = rail.nodes

    def extend(chain):
        """Verlaengert die Kette an ihrem Ende, solange ein Anschluss passt."""
        while True:
            end = chain[-1]
            direction = None
            for i in range(len(chain) - 2, -1, -1):
                direction = _unit(pt[chain[i]], pt[end])
                if direction:
                    break
            if direction is None:
                return
            best = None
            for idx in adjacency.get(end, ()):
                if used[idx]:
                    continue
                nodes = ways[idx]
                if nodes[0] == end:
                    seq = nodes
                elif nodes[-1] == end:
                    seq = nodes[::-1]
                else:
                    continue
                out = None
                for j in range(1, len(seq)):
                    out = _unit(pt[seq[0]], pt[seq[j]])
                    if out:
                        break
                if out is None:
                    continue
                angle = _angle_between(direction, out)
                if angle <= max_angle and (best is None or angle < best[2]):
                    best = (idx, seq, angle)
            if best is None:
                return
            used[best[0]] = True
            chain.extend(best[1][1:])

    # Ways an Streckenenden (freier Endpunkt) zuerst - ergibt sauberere Ketten
    order = sorted(
        range(len(ways)),
        key=lambda i: (
            min(len(adjacency[ways[i][0]]), len(adjacency[ways[i][-1]])),
            -len(ways[i]),
        ),
    )

    chains = []
    for i in order:
        if used[i]:
            continue
        used[i] = True
        chain = list(ways[i])
        extend(chain)
        chain.reverse()
        extend(chain)
        chains.append(chain)

    chains.sort(key=lambda c: -chain_length(rail, c))
    return chains


def chain_length(rail, node_ids):
    pts = [rail.nodes[n] for n in node_ids]
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def polyline(rail, node_ids):
    """Knoten-IDs -> (Punktliste, kumulierte Stationierung)."""
    points = []
    for n in node_ids:
        p = rail.nodes[n]
        if not points or math.dist(points[-1], p) > 1e-6:
            points.append(p)
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + math.dist(points[i - 1], points[i]))
    return points, cum


# ------------------------------------------------- Lineare Referenzierung

def project_point(points, cum, px, py):
    """Naechster Punkt auf der Polylinie. Rueckgabe (station_s, abstand)."""
    best_s, best_d2 = 0.0, float("inf")
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        if seg2 == 0.0:
            continue
        t = ((px - x1) * dx + (py - y1) * dy) / seg2
        t = max(0.0, min(1.0, t))
        cx, cy = x1 + t * dx, y1 + t * dy
        d2 = (px - cx) ** 2 + (py - cy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_s = cum[i] + t * math.sqrt(seg2)
    return best_s, math.sqrt(best_d2)


def point_at(points, cum, s):
    """Position und Tangente (Einheitsvektor) an der Station s."""
    total = cum[-1]
    s = max(0.0, min(total, s))
    lo, hi = 0, len(cum) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    (x1, y1), (x2, y2) = points[lo], points[lo + 1]
    seg = cum[lo + 1] - cum[lo]
    t = (s - cum[lo]) / seg if seg > 0 else 0.0
    tangent = _unit(points[lo], points[lo + 1]) or (1.0, 0.0)
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1)), tangent


# ------------------------------------------------------ Kilometrierungs-Fit

class ChainageFit:
    """km(s) = a * s + b   mit s in Metern, km in Kilometern, a = +/-0.001."""

    def __init__(self, a=0.001, b=0.0, inliers=0, total=0, max_dev=0.0, source="MANUAL",
                 samples=None):
        self.a = a
        self.b = b
        self.inliers = inliers
        self.total = total
        self.max_dev = max_dev   # groesste Abweichung der Milestones in Metern
        self.source = source
        self.samples = samples or []   # verwendete Stuetzstellen [(station_s, km)]

    def km(self, s):
        return self.a * s + self.b

    def station(self, km):
        return (km - self.b) / self.a

    def shifted(self, offset):
        """Fit fuer ein Teilstueck, das bei Station ``offset`` beginnt."""
        if not offset:
            return self
        return ChainageFit(a=self.a, b=self.b + self.a * offset, inliers=self.inliers,
                           total=self.total, max_dev=self.max_dev, source=self.source,
                           samples=[(s - offset, km) for s, km in self.samples])

    @property
    def ascending(self):
        return self.a > 0


def collect_milestone_samples(rail, points, cum, max_distance=30.0):
    """Ordnet Milestones der Achse zu. Rueckgabe [(station_s, [km-kandidaten])]."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs) - max_distance, max(xs) + max_distance
    y_min, y_max = min(ys) - max_distance, max(ys) + max_distance

    samples = []
    for ms in rail.milestones:
        # grober Vorfilter, damit grosse Gebiete zuegig ausgewertet werden
        if not (x_min <= ms["x"] <= x_max and y_min <= ms["y"] <= y_max):
            continue
        s, dist = project_point(points, cum, ms["x"], ms["y"])
        if dist <= max_distance:
            samples.append((s, ms["km"]))
    return samples


def fit_chainage(samples, tolerance=25.0, min_inliers=2):
    """Robuster Fit (RANSAC-artig) der Kilometrierung aus Milestone-Angaben.

    Die Steigung ist physikalisch festgelegt (1 km je 1000 m), gesucht sind
    Richtung und Nullpunkt. Ausreisser und mehrdeutige Angaben an
    Streckenkreuzungen werden dadurch zuverlaessig verworfen.
    """
    if not samples:
        return None

    best = None
    for a in (0.001, -0.001):
        candidates = [(km - a * s, idx) for idx, (s, kms) in enumerate(samples) for km in kms]
        for b0, _ in candidates:
            chosen = {}
            for b, idx in candidates:
                if abs(b - b0) * 1000.0 <= tolerance and idx not in chosen:
                    chosen[idx] = b
            if not chosen:
                continue
            values = sorted(chosen.values())
            b = values[len(values) // 2]
            spread = max(abs(v - b) for v in values) * 1000.0
            score = (len(chosen), -spread)
            if best is None or score > best[0]:
                best = (score, a, b, len(chosen))

    if best is None:
        return None

    _, a, b, _ = best
    # Feinabgleich: Nullpunkt als Median aller Inlier
    offsets = []
    used = []
    for s, kms in samples:
        for km in kms:
            if abs((km - a * s) - b) * 1000.0 <= tolerance:
                offsets.append(km - a * s)
                used.append((s, km))
                break
    if len(offsets) < min_inliers:
        return None
    offsets.sort()
    b = offsets[len(offsets) // 2]
    max_dev = max(abs(v - b) for v in offsets) * 1000.0

    return ChainageFit(a=a, b=b, inliers=len(offsets), total=len(samples),
                       max_dev=max_dev, source="MILESTONES", samples=used)


# ----------------------------------------------------------------- Marken

def build_marks(points, cum, fit, interval=100.0, include_start=True):
    """Erzeugt die Kilometrierungsmarken.

    Rueckgabe: Liste von dicts mit Position, Tangente, Station und km-Wert.
    Die Marken liegen auf runden Vielfachen des Intervalls der *Kilometrierung*
    (nicht der Kettenlaenge) - genau wie die Hektometertafeln im Gelaende.

    Zusaetzlich wird mit ``include_start`` immer eine Marke am Anfangspunkt der
    Linie gesetzt. Dort faengt die Kilometrierung in aller Regel bei einem
    krummen Wert an, der genau (auf Meter) angeschrieben werden soll.
    """
    total = cum[-1]
    step_km = interval / 1000.0
    km_a, km_b = fit.km(0.0), fit.km(total)
    km_min, km_max = min(km_a, km_b), max(km_a, km_b)

    eps = 1e-9
    k_start = math.ceil(km_min / step_km - eps)
    k_end = math.floor(km_max / step_km + eps)

    marks = []
    for k in range(k_start, k_end + 1):
        km = round(k * step_km, 6)
        s = fit.station(km)
        if s < -0.001 or s > total + 0.001:
            continue
        pos, tangent = point_at(points, cum, s)
        if not fit.ascending:
            tangent = (-tangent[0], -tangent[1])
        marks.append({
            "km": km,
            "station": s,
            "pos": pos,
            "tangent": tangent,
            "is_km": abs(km - round(km)) < 1e-6,
            "is_start": False,
        })

    if include_start:
        if marks and marks[0]["station"] < 0.5:
            # der Linienanfang faellt mit einer regulaeren Marke zusammen
            marks[0]["is_start"] = True
        else:
            pos, tangent = point_at(points, cum, 0.0)
            if not fit.ascending:
                tangent = (-tangent[0], -tangent[1])
            km0 = fit.km(0.0)
            marks.insert(0, {
                "km": km0,
                "station": 0.0,
                "pos": pos,
                "tangent": tangent,
                "is_km": abs(km0 - round(km0)) < 1e-6,
                "is_start": True,
            })
    return marks


def resample(points, spacing):
    """Polylinie mit gleichmaessigem Stuetzpunktabstand neu abtasten.

    Eine feinere Stuetzung ist die Voraussetzung dafuer, dass die Linie dem
    Gelaende folgen kann: Ein Shrinkwrap verschiebt nur die vorhandenen Punkte,
    zwischen ihnen bleibt die Linie gerade.
    """
    if spacing <= 0.0 or len(points) < 2:
        return list(points)
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + math.dist(points[i - 1], points[i]))
    total = cum[-1]
    if total <= 0.0:
        return list(points)

    count = max(1, int(round(total / spacing)))
    result = []
    index = 0
    for step in range(count + 1):
        s = min(total, step * total / count)
        while index < len(cum) - 2 and cum[index + 1] < s:
            index += 1
        segment = cum[index + 1] - cum[index]
        t = 0.0 if segment <= 0.0 else (s - cum[index]) / segment
        x = points[index][0] + t * (points[index + 1][0] - points[index][0])
        y = points[index][1] + t * (points[index + 1][1] - points[index][1])
        result.append((x, y))
    return result


def slice_by_stations(points, stations, s_from, s_to):
    """Teilstueck einer Polylinie zwischen zwei Stationen.

    ``stations`` ist die Lauflaenge je Stuetzpunkt - sie darf aus einem anderen
    Koordinatensystem stammen als ``points`` (etwa UTM gegenueber lokalen
    Koordinaten), solange beide dieselben Stuetzpunkte beschreiben.
    """
    if len(points) < 2:
        return list(points)
    s_from = max(0.0, min(s_from, stations[-1]))
    s_to = max(s_from, min(s_to, stations[-1]))

    def at(s):
        lo, hi = 0, len(stations) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if stations[mid] <= s:
                lo = mid
            else:
                hi = mid
        span = stations[lo + 1] - stations[lo]
        t = 0.0 if span <= 0 else (s - stations[lo]) / span
        x0, y0 = points[lo]
        x1, y1 = points[lo + 1]
        return (x0 + t * (x1 - x0), y0 + t * (y1 - y0)), lo

    start, index0 = at(s_from)
    end, index1 = at(s_to)
    middle = [points[i] for i in range(index0 + 1, index1 + 1)]
    result = [start] + middle + [end]
    cleaned = [result[0]]
    for point in result[1:]:
        if math.dist(cleaned[-1], point) > 1e-6:
            cleaned.append(point)
    return cleaned if len(cleaned) >= 2 else list(points[index0:index1 + 2])


def format_km(km, decimals=1, separator=",", prefix="", suffix=""):
    text = ("%." + str(decimals) + "f") % km
    if separator != ".":
        text = text.replace(".", separator)
    return "%s%s%s" % (prefix, text, suffix)


class PiecewiseFit:
    """Stueckweise lineare Kilometrierung zwischen den Milestones.

    Bildet die tatsaechliche Kilometrierung genauer ab als ein globaler
    Geradenfit, weil reale Strecken Fehlkilometer (Sprungstellen durch
    Streckenumbauten) enthalten koennen. Ausserhalb des durch Milestones
    abgedeckten Bereichs wird mit 1 km je 1000 m extrapoliert.
    """

    source = "MILESTONES_PW"

    def __init__(self, nodes, a, inliers=0, total=0, max_dev=0.0):
        self.nodes = nodes          # [(station_s, km)] streng monoton nach s
        self.a = a                  # Grundsteigung (+/-0.001) fuer die Extrapolation
        self.inliers = inliers
        self.total = total
        self.max_dev = max_dev
        self.samples = list(nodes)

    @property
    def ascending(self):
        return self.a > 0

    def shifted(self, offset):
        """Fit fuer ein Teilstueck, das bei Station ``offset`` beginnt."""
        if not offset:
            return self
        nodes = [(s - offset, km) for s, km in self.nodes]
        return PiecewiseFit(nodes, self.a, inliers=self.inliers,
                            total=self.total, max_dev=self.max_dev)

    def km(self, s):
        nodes = self.nodes
        if s <= nodes[0][0]:
            return nodes[0][1] + self.a * (s - nodes[0][0])
        if s >= nodes[-1][0]:
            return nodes[-1][1] + self.a * (s - nodes[-1][0])
        lo, hi = 0, len(nodes) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if nodes[mid][0] <= s:
                lo = mid
            else:
                hi = mid
        (s0, k0), (s1, k1) = nodes[lo], nodes[lo + 1]
        t = (s - s0) / (s1 - s0)
        return k0 + t * (k1 - k0)

    def station(self, km):
        nodes = self.nodes
        asc = self.ascending
        first_km, last_km = nodes[0][1], nodes[-1][1]
        if (asc and km <= first_km) or (not asc and km >= first_km):
            return nodes[0][0] + (km - first_km) / self.a
        if (asc and km >= last_km) or (not asc and km <= last_km):
            return nodes[-1][0] + (km - last_km) / self.a
        lo, hi = 0, len(nodes) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if (nodes[mid][1] <= km) == asc:
                lo = mid
            else:
                hi = mid
        (s0, k0), (s1, k1) = nodes[lo], nodes[lo + 1]
        if k1 == k0:
            return s0
        return s0 + (km - k0) / (k1 - k0) * (s1 - s0)

    def covered_range(self):
        """Stationsbereich, der tatsaechlich durch Milestones belegt ist."""
        return self.nodes[0][0], self.nodes[-1][0]


def make_piecewise(fit, merge_distance=5.0, min_nodes=2):
    """Baut aus den Inliern eines linearen Fits eine stueckweise Kilometrierung.

    Hektometertafeln stehen haeufig paarweise an beiden Gleisen; Stuetzstellen
    mit nahezu gleicher Station werden deshalb zusammengefasst.
    """
    if not fit or not fit.samples or len(fit.samples) < min_nodes:
        return None

    # Tafeln mit gleichem km-Wert (je eine an jedem Gleis) zusammenfassen
    by_km = {}
    for s, km in fit.samples:
        by_km.setdefault(round(km, 6), []).append(s)
    nodes = sorted((sum(ss) / len(ss), km) for km, ss in by_km.items())

    # sehr eng benachbarte Stuetzstellen verschmelzen
    compact = []
    for s, km in nodes:
        if compact and abs(s - compact[-1][0]) <= merge_distance:
            continue
        compact.append((s, km))
    nodes = compact

    # Monotonie erzwingen - nicht passende Stuetzstellen verwerfen
    asc = fit.a > 0
    cleaned = [nodes[0]]
    for s, km in nodes[1:]:
        prev_s, prev_km = cleaned[-1]
        if s - prev_s < 1.0:
            continue
        if (km > prev_km) == asc and km != prev_km:
            cleaned.append((s, km))
    if len(cleaned) < min_nodes:
        return None

    return PiecewiseFit(cleaned, fit.a, inliers=len(cleaned),
                        total=fit.total, max_dev=fit.max_dev)


# ------------------------------------------------------- Zuschnitt auf Gebiet

def _clip_segment(x0, y0, x1, y1, xmin, ymin, xmax, ymax):
    """Liang-Barsky: sichtbarer Parameterbereich eines Segments im Rechteck."""
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        if p == 0.0:
            if q < 0.0:
                return None
        else:
            r = q / p
            if p < 0.0:
                if r > t1:
                    return None
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return None
                if r < t1:
                    t1 = r
    return t0, t1


def clip_to_bbox(points, cum, projection, bbox):
    """Schneidet eine Polylinie exakt am Rand des geografischen Gebiets ab.

    ``bbox`` ist (min_lat, min_lon, max_lat, max_lon). Der Zuschnitt erfolgt in
    geografischen Koordinaten, damit die Grenze genau der angegebenen Bounding
    Box entspricht.

    Rueckgabe: Liste von (Teilpunkte, Stationsversatz). Der Versatz gibt an, wo
    das Teilstueck auf der ungeschnittenen Linie beginnt - damit bleibt die
    einmal bestimmte Kilometrierung gueltig.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    geo = [projection.to_geographic(x, y) for x, y in points]

    def lerp(i, t):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        return (x0 + t * (x1 - x0), y0 + t * (y1 - y0))

    pieces = []
    current = None
    for i in range(len(points) - 1):
        lat0, lon0 = geo[i]
        lat1, lon1 = geo[i + 1]
        clipped = _clip_segment(lon0, lat0, lon1, lat1, min_lon, min_lat, max_lon, max_lat)
        if clipped is None:
            current = None
            continue
        t0, t1 = clipped
        start, end = lerp(i, t0), lerp(i, t1)
        seg = cum[i + 1] - cum[i]
        s0 = cum[i] + t0 * seg

        if current is not None and t0 <= 0.0:
            if math.dist(current["points"][-1], end) > 1e-6:
                current["points"].append(end)
        else:
            current = {"points": [start], "offset": s0}
            pieces.append(current)
            if math.dist(start, end) > 1e-6:
                current["points"].append(end)

        if t1 < 1.0:
            current = None

    return [(p["points"], p["offset"]) for p in pieces if len(p["points"]) >= 2]


# =====================================================================
#  Lage der Kilometrierungslinie nach Ril 883.0010, Abschnitt 3
# =====================================================================

class SegmentIndex:
    """Einfaches Gitter ueber die Segmente einer Polylinie fuer schnelle
    Naechster-Punkt-Abfragen."""

    def __init__(self, points, cell=25.0):
        self.points = points
        self.cell = cell
        self.grid = {}
        for i in range(len(points) - 1):
            (x0, y0), (x1, y1) = points[i], points[i + 1]
            length = math.hypot(x1 - x0, y1 - y0)
            steps = max(1, int(length / (cell * 0.5)) + 1)
            for k in range(steps + 1):
                t = k / steps
                key = (int((x0 + t * (x1 - x0)) // cell), int((y0 + t * (y1 - y0)) // cell))
                bucket = self.grid.setdefault(key, [])
                if not bucket or bucket[-1] != i:
                    bucket.append(i)

    def nearest_ex(self, px, py, max_distance):
        """Wie nearest, zusaetzlich mit Segmentindex und Lauflaenge im Segment.

        Rueckgabe (punkt, abstand, tangente, segment, t) oder None.
        """
        cx, cy = int(px // self.cell), int(py // self.cell)
        reach = int(max_distance // self.cell) + 1
        best = None
        seen = set()
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for i in self.grid.get((cx + dx, cy + dy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    (x0, y0), (x1, y1) = self.points[i], self.points[i + 1]
                    ux, uy = x1 - x0, y1 - y0
                    seg2 = ux * ux + uy * uy
                    if seg2 == 0.0:
                        continue
                    t = max(0.0, min(1.0, ((px - x0) * ux + (py - y0) * uy) / seg2))
                    qx, qy = x0 + t * ux, y0 + t * uy
                    dist = math.hypot(px - qx, py - qy)
                    if dist <= max_distance and (best is None or dist < best[1]):
                        length = math.sqrt(seg2)
                        best = ((qx, qy), dist, (ux / length, uy / length), i, t)
        return best

    def nearest(self, px, py, max_distance):
        """Rueckgabe (punkt, abstand, tangente) oder None."""
        cx, cy = int(px // self.cell), int(py // self.cell)
        reach = int(max_distance // self.cell) + 1
        best = None
        seen = set()
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for i in self.grid.get((cx + dx, cy + dy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    (x0, y0), (x1, y1) = self.points[i], self.points[i + 1]
                    ux, uy = x1 - x0, y1 - y0
                    seg2 = ux * ux + uy * uy
                    if seg2 == 0.0:
                        continue
                    t = max(0.0, min(1.0, ((px - x0) * ux + (py - y0) * uy) / seg2))
                    qx, qy = x0 + t * ux, y0 + t * uy
                    dist = math.hypot(px - qx, py - qy)
                    if dist <= max_distance and (best is None or dist < best[1]):
                        length = math.sqrt(seg2)
                        best = ((qx, qy), dist, (ux / length, uy / length))
        return best


def _tangents(points):
    """Tangente je Stuetzpunkt (gemittelt aus den angrenzenden Segmenten)."""
    result = []
    for i in range(len(points)):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        t = _unit(a, b) or (1.0, 0.0)
        result.append(t)
    return result


def _smooth_offsets(offsets, cum, transition):
    """Gleitendes Mittel ueber die Stationierung.

    Ril 883.0010 Abschnitt 3 (2) verbietet seitliche Spruenge in der
    Kilometrierungslinie; Abschnitt 3 (3) verlangt beim Uebergang von ein- auf
    zweigleisig einen mittig einlaufenden Bogen. Beides wird durch die
    Glaettung ueber die Uebergangslaenge erreicht.
    """
    if transition <= 0.0 or len(offsets) < 3:
        return offsets
    half = transition * 0.5
    smoothed = []
    start = 0
    end = 0
    for i, s in enumerate(cum):
        while cum[start] < s - half:
            start += 1
        while end + 1 < len(cum) and cum[end + 1] <= s + half:
            end += 1
        count = end - start + 1
        sx = sum(offsets[k][0] for k in range(start, end + 1)) / count
        sy = sum(offsets[k][1] for k in range(start, end + 1)) / count
        smoothed.append((sx, sy))
    return smoothed


def build_center_axis(reference, others, max_distance=15.0, max_angle=25.0, transition=150.0):
    """Streckenachse: Mitte zwischen den Gleisen (Ril 883.0010, Abschnitt 3 (1)).

    ``reference`` ist die laengste Gleisachse, ``others`` sind die uebrigen
    Gleise derselben Strecke. Wo ein Nachbargleis in Reichweite und annaehernd
    parallel verlaeuft, wandert die Linie in die Mitte; wo die Strecke
    eingleisig ist, bleibt sie auf der Gleisachse. Der Wechsel wird geglaettet.

    Rueckgabe: (Punktliste, Anteil zweigleisiger Stuetzpunkte)
    """
    if not others:
        return list(reference), 0.0

    indexes = [SegmentIndex(o, cell=max(10.0, max_distance)) for o in others if len(o) >= 2]
    if not indexes:
        return list(reference), 0.0

    cum = [0.0]
    for i in range(1, len(reference)):
        cum.append(cum[-1] + math.dist(reference[i - 1], reference[i]))
    tangents = _tangents(reference)

    offsets = []
    paired = 0
    for i, (px, py) in enumerate(reference):
        tx, ty = tangents[i]
        partners = []
        for index in indexes:
            hit = index.nearest(px, py, max_distance)
            if hit is None:
                continue
            (qx, qy), _dist, (ox, oy) = hit
            # nur annaehernd parallele Gleise zaehlen zur Streckenachse
            if abs(_angle_between((tx, ty), (ox, oy))) > max_angle and \
               abs(_angle_between((tx, ty), (-ox, -oy))) > max_angle:
                continue
            partners.append((qx, qy))
        if partners:
            paired += 1
            mx = (px + sum(q[0] for q in partners)) / (len(partners) + 1)
            my = (py + sum(q[1] for q in partners)) / (len(partners) + 1)
            offsets.append((mx - px, my - py))
        else:
            offsets.append((0.0, 0.0))

    offsets = _smooth_offsets(offsets, cum, transition)
    axis = [(p[0] + o[0], p[1] + o[1]) for p, o in zip(reference, offsets)]
    return axis, paired / len(reference)


def offset_axis(points, distance, side='LEFT'):
    """Linie parallel zu einer Gleisachse (Ril 883.0010, Abschnitt 3 (1))."""
    if not distance:
        return list(points)
    sign = 1.0 if side == 'LEFT' else -1.0
    tangents = _tangents(points)
    return [(x - ty * distance * sign, y + tx * distance * sign)
            for (x, y), (tx, ty) in zip(points, tangents)]


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def count_crossings(axis, tracks):
    """Zaehlt Schnittpunkte der Kilometrierungslinie mit den Gleisachsen.

    Ril 883.0010 Abschnitt 3 (1): bei zweigleisigen Strecken darf keine
    Gleisachse die Kilometrierungslinie ueberschneiden.
    """
    if len(axis) < 2:
        return 0
    index = SegmentIndex(axis, cell=50.0)
    crossings = 0
    for track in tracks:
        for i in range(len(track) - 1):
            a, b = track[i], track[i + 1]
            mx, my = (a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5
            reach = math.dist(a, b) * 0.5 + 50.0
            cx, cy = int(mx // index.cell), int(my // index.cell)
            span = int(reach // index.cell) + 1
            checked = set()
            for dx in range(-span, span + 1):
                for dy in range(-span, span + 1):
                    for k in index.grid.get((cx + dx, cy + dy), ()):
                        if k in checked:
                            continue
                        checked.add(k)
                        if _segments_cross(a, b, axis[k], axis[k + 1]):
                            crossings += 1
    return crossings


def format_km_db(km, separator=",", meter_decimals=2):
    """Schreibweise nach Ril 883.0010, Abschnitt 3 (4): z. B. 13,4+23,05.

    Der Hektometeranteil wird zur Null hin abgeschnitten, der Restbetrag in
    Metern behaelt sein Vorzeichen - damit stimmt auch die in der Richtlinie
    angegebene Schreibweise negativer Kilometerangaben (-6,1 + -82).
    """
    hm = math.trunc(round(km * 10.0, 6)) / 10.0
    rest = (km - hm) * 1000.0
    text = ("%.1f" % hm).replace(".", separator)
    if abs(rest) < 0.5 * 10 ** (-meter_decimals):
        return text
    meters = ("%." + str(meter_decimals) + "f") % rest
    return "%s+%s" % (text, meters.replace(".", separator))

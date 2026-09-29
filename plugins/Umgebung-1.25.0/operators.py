"""Operatoren: Gebiet festlegen, Strecken suchen, Kilometrierung erzeugen."""

import math
import re
import threading
import uuid

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import (buildings, builder, corridor as corridor_mod, dgm, maptexture, overpass,
               railgeom, selector, tracknetwork)
from .projection import TransverseMercator

# Obergrenze fuer die Summe aller Kartentexturen (RGBA, rund 4 Byte je Pixel)
# Beim Aufbau eines Texturgitters fallen je Pixel mehrere Zwischenwerte an;
# die Grenze muss daher deutlich unter dem reinen Bildspeicher liegen.
MAX_TEXTURE_PIXELS = 25_000_000

# Zwischenspeicher zwischen "Strecken suchen" und "Kilometrierung erzeugen"
_STATE = {
    "rail": None,
    "routes": {},
    "projection": None,
    "signature": None,
}


# ------------------------------------------------------------- Hilfsmittel

def get_bbox(props):
    """(min_lat, min_lon, max_lat, max_lon)"""
    if props.area_mode == 'CENTER':
        d_lat = props.radius / 111320.0
        d_lon = props.radius / (111320.0 * max(0.02, math.cos(math.radians(props.center_lat))))
        return (props.center_lat - d_lat, props.center_lon - d_lon,
                props.center_lat + d_lat, props.center_lon + d_lon)
    return (min(props.min_lat, props.max_lat), min(props.min_lon, props.max_lon),
            max(props.min_lat, props.max_lat), max(props.min_lon, props.max_lon))


def get_projection(context, props):
    """Projektionsmittelpunkt bestimmen - kompatibel zu BLOSM."""
    scene = context.scene
    if props.use_scene_origin and "lat" in scene and "lon" in scene:
        return TransverseMercator(scene["lat"], scene["lon"]), False
    bbox = get_bbox(props)
    return TransverseMercator((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0), True


def signature(props):
    return (get_bbox(props), tuple(sorted(props.track_types)), props.skip_service_tracks,
            props.include_station_tracks, overpass_endpoint(props))


def is_loaded(props):
    return _STATE["rail"] is not None and _STATE["signature"] == signature(props)


def map_resolution(props):
    """Gewaehlte Kartenaufloesung in Metern je Pixel."""
    if props.map_quality == 'CUSTOM':
        return props.map_resolution
    return float(props.map_quality)


def estimate_map(props, span_x, span_y, lat=51.0):
    """Schaetzt Bildgroesse, Kachelzahl und feinstmoegliche Aufloesung.

    ``span_x``/``span_y`` sind die Ausdehnungen der Kartenflaeche in Metern.
    Rueckgabe: dict mit breite, hoehe, pixel, kacheln, grenze, min_res, passt.
    """
    aufloesung = max(0.01, map_resolution(props))
    breite = min(int(props.map_max_edge), max(16, int(math.ceil(span_x / aufloesung))))
    hoehe = min(int(props.map_max_edge), max(8, int(math.ceil(span_y / aufloesung))))
    pixel = breite * hoehe

    # feinste Aufloesung, die Bildbreite und Pixelbudget noch zulassen
    res_kante = max(span_x / props.map_max_edge, span_y / props.map_max_edge)
    res_budget = math.sqrt(max(1e-9, span_x * span_y / MAX_TEXTURE_PIXELS))
    min_res = max(res_kante, res_budget)

    schritt = span_x / max(1, breite)
    max_zoom = maptexture.SOURCES[props.map_source][1] if not props.tile_url.strip() else 22
    zoom = maptexture.choose_zoom(lat, schritt, max_zoom)
    kachel_m = 256.0 * maptexture.tile_resolution(lat, zoom)
    # schräg verlaufende Korridore berühren deutlich mehr Kacheln als das Rechteck
    schraeg = 2.0 if span_x > 10.0 * span_y else 1.0
    kacheln = max(1, int(math.ceil(span_x / kachel_m + 1.0)
                         * math.ceil(span_y / kachel_m + 1.0) * schraeg))

    grenze = props.map_max_tiles
    if props.map_source in maptexture.VOLUNTEER_SOURCES and not props.tile_url.strip():
        grenze = min(grenze, maptexture.VOLUNTEER_LIMIT)

    return {"breite": breite, "hoehe": hoehe, "pixel": pixel, "kacheln": kacheln,
            "grenze": grenze, "min_res": min_res, "zoom": zoom, "schritt": schritt,
            "passt": pixel <= MAX_TEXTURE_PIXELS and kacheln <= grenze}


def overpass_endpoint(props):
    """Gewaehlter Overpass-Server (None = oeffentliche Server der Reihe nach)."""
    if props.overpass_mode == 'CUSTOM' and props.overpass_url.strip():
        return props.overpass_url.strip()
    return None


def fetch_data(props, status=None):
    """Reiner Netzwerkteil - laeuft ohne bpy und damit auch in einem Thread."""
    return overpass.fetch(
        get_bbox(props), sorted(props.track_types) or ['rail'],
        timeout=props.request_timeout,
        use_cache=props.use_cache,
        endpoint=overpass_endpoint(props),
        progress=status,
    )


def process_data(context, props, data):
    """Rohdaten auswerten und im Zwischenspeicher ablegen (Hauptthread)."""
    projection, is_new_origin = get_projection(context, props)
    rail = railgeom.RailData.from_overpass(
        data, projection, sorted(props.track_types) or ['rail'],
        # Bahnhofsgleise müssen erhalten bleiben, wenn sie aufgelistet werden sollen
        skip_service=props.skip_service_tracks and not props.include_station_tracks)

    _STATE.update({"rail": rail, "projection": projection, "signature": signature(props)})

    if is_new_origin:
        # wie BLOSM den Projektionsmittelpunkt in der Szene hinterlegen
        context.scene["lat"] = projection.lat
        context.scene["lon"] = projection.lon
    return rail, projection


def load_data(context, props):
    """Daten bereitstellen - aus dem Zwischenspeicher oder (synchron) vom Server."""
    if is_loaded(props):
        return _STATE["rail"], _STATE["projection"]
    return process_data(context, props, fetch_data(props))


class _Fetcher:
    """Laedt die Overpass-Daten in einem Hintergrundthread, damit Blender bedienbar bleibt."""

    def __init__(self, props):
        self._props = props
        self.data = None
        self.error = None
        self.status = "Verbindung wird aufgebaut …"
        self.fraction = -1.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def _set_status(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction

    def _run(self):
        try:
            self.data = fetch_data(self._props, status=self._set_status)
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc


def build_route_chains(rail, route, props):
    chains = railgeom.build_chains(rail, route.way_ids, max_angle=props.max_angle)
    chains = [c for c in chains if railgeom.chain_length(rail, c) >= props.min_chain_length]
    if props.only_longest_chain:
        chains = chains[:1]
    return chains


def track_polylines(rail, route, props, minimum=50.0):
    """Alle Gleisachsen einer Strecke als Polylinien, nach Laenge sortiert."""
    chains = railgeom.build_chains(rail, route.way_ids, max_angle=props.max_angle)
    polys = []
    for chain in chains:
        points, cum = railgeom.polyline(rail, chain)
        if len(points) >= 2 and cum[-1] >= minimum:
            polys.append((points, cum[-1]))
    polys.sort(key=lambda pl: -pl[1])
    return [p for p, _ in polys]


def build_axes(rail, route, props):
    """Lage der Kilometrierungslinie nach Ril 883.0010, Abschnitt 3 (1).

    Rueckgabe: (Liste der Achsen, Gleisachsen fuer die Pruefung, Hinweistext)
    """
    tracks = track_polylines(rail, route, props)
    if not tracks:
        return [], [], ""

    def _refine(points):
        return railgeom.resample(points, props.line_resample) if props.line_resample else points

    if props.axis_mode == 'CENTER':
        axis, share = railgeom.build_center_axis(
            tracks[0], tracks[1:],
            max_distance=props.track_spacing,
            transition=props.axis_transition)
        if len(tracks) == 1:
            note = "eingleisig, Linie auf der Gleisachse"
        else:
            note = "Streckenachse, %d Gleise, %.0f %% zweigleisig" % (len(tracks), share * 100.0)
        return [_refine(axis)], tracks[1:], note

    if props.axis_mode == 'PARALLEL':
        axis = railgeom.offset_axis(tracks[0], props.parallel_offset, props.parallel_side)
        return [_refine(axis)], tracks, "parallel zur Gleisachse (%.2f m)" % props.parallel_offset

    # Gleisachse
    axes = tracks[:1] if props.only_longest_chain else [
        t for t in tracks if _length(t) >= props.min_chain_length]
    return [_refine(a) for a in axes], tracks[1:], "auf der Gleisachse"


def _length(points):
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def get_interval(props, route):
    """Markenabstand nach Ril 883.0010, Abschnitt 6 (1)."""
    if props.interval_mode == 'FIXED':
        return props.interval
    return 500.0 if route.usage == "branch" else 100.0


def make_fit(rail, points, cum, props):
    """Kilometrierung bestimmen. Rueckgabe (fit, points, cum, hinweis)."""
    if props.chainage_source == 'MANUAL':
        if props.reverse:
            points = points[::-1]
            points, cum = _recompute(points)
        fit = railgeom.ChainageFit(a=0.001, b=props.start_km, source="MANUAL")
        return fit, points, cum, "manuell ab km %s" % railgeom.format_km(props.start_km, 3)

    samples = railgeom.collect_milestone_samples(rail, points, cum, props.milestone_distance)
    fit = railgeom.fit_chainage(samples, tolerance=props.milestone_tolerance)

    if fit is not None and not fit.ascending:
        # Linie in Richtung aufsteigender Kilometrierung orientieren
        points = points[::-1]
        points, cum = _recompute(points)
        samples = railgeom.collect_milestone_samples(rail, points, cum, props.milestone_distance)
        fit = railgeom.fit_chainage(samples, tolerance=props.milestone_tolerance)

    if fit is None:
        if not props.fallback_manual:
            return None, points, cum, "keine Kilometrierungstafeln gefunden"
        if props.reverse:
            points = points[::-1]
            points, cum = _recompute(points)
        manual = railgeom.ChainageFit(a=0.001, b=props.start_km, source="MANUAL")
        return manual, points, cum, "keine Tafeln gefunden - manuell ab km %s" % \
            railgeom.format_km(props.start_km, 3)

    note = "%d Tafeln, max. Abweichung %.1f m" % (fit.inliers, fit.max_dev)
    if props.chainage_source == 'MILESTONES_PW':
        piecewise = railgeom.make_piecewise(fit)
        if piecewise is not None:
            return piecewise, points, cum, note + ", stückweise interpoliert"
        note += " (zu wenige Stützstellen für stückweise Interpolation)"
    return fit, points, cum, note


def _recompute(points):
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + math.dist(points[i - 1], points[i]))
    return points, cum


def _zu_gross(masse):
    return ("Die Kartentextur ergäbe %d x %d Pixel (%.0f Mio, Grenze %.0f Mio). Bitte die "
            "Auflösung gröber wählen oder das Gebiet verkleinern"
            % (masse[0], masse[1], masse[0] * masse[1] / 1e6, MAX_TEXTURE_PIXELS / 1e6))


def check_tile_budget(props, count, pixels=0):
    """Prueft, ob ein Abruf gegenueber der Kartenquelle vertretbar ist.

    Rueckgabe: Fehlertext oder None. Ehrenamtlich betriebene Dienste (OSM,
    OpenRailwayMap, OpenTopoMap) sind streng begrenzt - massenhafte Abrufe
    verstossen gegen deren Nutzungsbedingungen und fuehren zur Sperrung.
    """
    grenze = props.map_max_tiles
    if props.map_source in maptexture.VOLUNTEER_SOURCES and not props.tile_url.strip():
        grenze = min(grenze, maptexture.VOLUNTEER_LIMIT)
        if count > grenze:
            return ("Diese Karte läuft auf ehrenamtlich betriebenen Servern; %d Kacheln "
                    "wären zu viel (Grenze %d). Bitte basemap.de oder TopPlusOpen wählen, "
                    "einen eigenen Kachelserver eintragen oder das Gebiet verkleinern"
                    % (count, grenze))
    if count > grenze:
        return ("Dafür wären %d Kartenkacheln nötig (Grenze %d). Bitte die Auflösung gröber "
                "wählen oder das Gebiet verkleinern" % (count, grenze))
    if pixels and pixels > MAX_TEXTURE_PIXELS:
        return ("Die Texturen ergäben zusammen %.0f Millionen Pixel (Grenze %.0f Mio). "
                "Bitte die Auflösung gröber wählen"
                % (pixels / 1e6, MAX_TEXTURE_PIXELS / 1e6))
    return None


def _steps(start, stop, step):
    """Werte von start bis stop im Abstand step (stop immer enthalten)."""
    werte, value = [], start
    while value < stop:
        werte.append(value)
        value += step
    werte.append(stop)
    return werte


def _zeige_fortschritt(context, props, fetcher):
    """Statuszeile, Fortschrittsbalken und Statusleiste aktualisieren."""
    props.busy = True
    props.scan_info = fetcher.status
    anteil = getattr(fetcher, "fraction", -1.0)
    props.progress = 0.0 if anteil is None or anteil < 0 else anteil * 100.0
    try:
        context.workspace.status_text_set(fetcher.status)
    except AttributeError:
        pass
    _redraw(context)


def _fortschritt_beenden(context, props):
    props.busy = False
    props.progress = 0.0
    try:
        context.workspace.status_text_set(None)
    except AttributeError:
        pass


def _redraw(context):
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


# --------------------------------------------------------------- Operatoren

class RC_OT_scan(Operator):
    bl_idname = "railway_chainage.scan"
    bl_label = "Strecken suchen"
    bl_description = "Bahndaten (OpenStreetMap / OpenRailwayMap) für das Gebiet laden und " \
                     "die enthaltenen Strecken auflisten"
    bl_options = {'REGISTER'}

    _timer = None
    _fetcher = None

    def _check(self, context, props):
        bbox = get_bbox(props)
        tiles = len(overpass.split_bbox(bbox))
        if tiles > 1:
            # grosse Gebiete werden gekachelt abgefragt, es gibt keine feste Obergrenze
            print("[Kilometrierung] Gebiet wird in %d Teilabfragen geladen" % tiles)
        if tiles > 200:
            self.report({'ERROR'},
                        "Das Gebiet ergäbe %d Teilabfragen - das überlastet den freien "
                        "Kartendienst. Bitte das Gebiet verkleinern" % tiles)
            return False
        if not props.track_types:
            self.report({'ERROR'}, "Bitte mindestens eine Gleisart auswählen")
            return False
        return True

    # -- interaktiv: Daten im Hintergrund laden, Blender bleibt bedienbar --
    def invoke(self, context, event):
        props = context.scene.railway_chainage
        if not self._check(context, props):
            return {'CANCELLED'}
        if is_loaded(props):
            return self.execute(context)

        props.scan_info = "Daten werden geladen … (Esc bricht ab)"
        self._fetcher = _Fetcher(props)
        self._fetcher.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._finish(context)
            props.scan_info = "Abgebrochen (der Abruf läuft im Hintergrund noch aus)"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        fetcher = self._fetcher
        if fetcher.running:
            _zeige_fortschritt(context, props, fetcher)
            return {'RUNNING_MODAL'}

        self._finish(context)
        if fetcher.error is not None:
            message = str(fetcher.error).splitlines()[0]
            props.scan_info = message
            print("[Kilometrierung] %s" % fetcher.error)
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        process_data(context, props, fetcher.data)
        self._collect(context, props)
        _redraw(context)
        return {'FINISHED'}

    def _finish(self, context):
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    # -- aus Skripten heraus: synchron --
    def execute(self, context):
        props = context.scene.railway_chainage
        if not self._check(context, props):
            return {'CANCELLED'}
        try:
            load_data(context, props)
        except overpass.OverpassError as exc:
            props.scan_info = str(exc).splitlines()[0]
            print("[Kilometrierung] %s" % exc)
            self.report({'ERROR'}, props.scan_info)
            return {'CANCELLED'}
        except Exception as exc:                                  # noqa: BLE001
            self.report({'ERROR'}, "Fehler beim Laden: %s" % exc)
            return {'CANCELLED'}
        self._collect(context, props)
        return {'FINISHED'}

    def _collect(self, context, props):
        """Gefundene Strecken in die Auswahlliste eintragen."""
        rail = _STATE["rail"]
        routes = railgeom.group_routes(rail, skip_service=props.skip_service_tracks)
        if props.include_station_tracks:
            routes = routes + railgeom.group_station_tracks(rail, radius=props.station_radius)
        _STATE["routes"] = {r.key: r for r in routes}

        props.routes.clear()
        for route in routes:
            chains = build_route_chains(rail, route, props)
            if not chains:
                continue
            points, cum = railgeom.polyline(rail, chains[0])
            samples = railgeom.collect_milestone_samples(
                rail, points, cum, props.milestone_distance)
            item = props.routes.add()
            item.name = route.label
            item.key = route.key
            item.ref = route.ref
            item.route_name = route.name
            item.length_km = route.length / 1000.0
            item.way_count = len(route.way_ids)
            item.milestone_count = len(samples)
            item.usage = route.usage
            item.is_station = getattr(route, "is_station", False)
            # Zahl der Gleise unabhaengig von der Einstellung "nur laengste Linie"
            item.track_count = len(track_polylines(rail, route, props))
            item.selected = (not item.is_station) and bool(samples) and route.length > 1000.0

        props.scan_info = "%d Strecken, %d Kilometrierungstafeln im Gebiet" % (
            len(props.routes), len(rail.milestones))
        self.report({'INFO'}, props.scan_info)


class RC_OT_generate(Operator):
    bl_idname = "railway_chainage.generate"
    bl_label = "Kilometrierung erzeugen"
    bl_description = "Für die ausgewählten Strecken je eine Kilometrierungslinie mit " \
                     "Beschriftung erzeugen"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.railway_chainage
        selected = [item for item in props.routes if item.selected]
        if not selected:
            self.report({'ERROR'}, "Keine Strecke ausgewählt - bitte zuerst Strecken suchen "
                                   "und auswählen")
            return {'CANCELLED'}

        # Niemals hier nachladen - das wuerde Blender waehrend der Abfrage einfrieren
        if not is_loaded(props):
            props.scan_info = ("Die geladenen Daten passen nicht zum eingestellten Gebiet - "
                               "bitte erneut „Strecken suchen“")
            self.report({'ERROR'}, props.scan_info)
            return {'CANCELLED'}

        rail = _STATE["rail"]
        routes = _STATE["routes"]
        if not routes:
            routes = {r.key: r for r in railgeom.group_routes(
                rail, skip_service=props.skip_service_tracks)}
            if props.include_station_tracks:
                routes.update({r.key: r for r in railgeom.group_station_tracks(
                    rail, radius=props.station_radius)})
            _STATE["routes"] = routes

        lines, labels, messages = 0, 0, []
        for item in selected:
            route = routes.get(item.key)
            if route is None:
                messages.append("%s: nicht mehr in den Daten enthalten" % item.name)
                continue
            if getattr(route, "is_station", False):
                messages.append("%s: Betriebsstelle - hier gibt es keine eigene Kilometrierung, "
                                "bitte „Gleisachsen erzeugen“ verwenden" % item.name)
                continue

            axes, neighbour_tracks, axis_note = build_axes(rail, route, props)
            if not axes:
                messages.append("%s: keine durchgehende Linie gefunden" % item.name)
                continue
            interval = get_interval(props, route)

            segments = []
            for axis in axes:
                points, cum = _recompute(list(axis))
                if len(points) < 2:
                    continue
                crossings = railgeom.count_crossings(points, neighbour_tracks) \
                    if props.check_crossings else 0
                fit, points, cum, note = make_fit(rail, points, cum, props)
                if fit is None:
                    messages.append("%s: %s" % (item.name, note))
                    continue

                if props.clip_to_area:
                    # exakt am Rand des eingestellten Gebiets abschneiden
                    pieces = railgeom.clip_to_bbox(points, cum, _STATE["projection"],
                                                   get_bbox(props))
                else:
                    pieces = [(points, 0.0)]

                for piece_points, offset in pieces:
                    piece_points, piece_cum = _recompute(list(piece_points))
                    if piece_cum[-1] < props.min_chain_length:
                        continue
                    segments.append((piece_points, piece_cum, fit.shifted(offset), note,
                                     crossings))

            for index, (points, cum, fit, note, crossings) in enumerate(segments):
                marks = railgeom.build_marks(points, cum, fit, interval)
                if not marks:
                    messages.append("%s: keine Marken im Intervall" % item.name)
                    continue
                suffix = "" if len(segments) == 1 else " (%d)" % (index + 1)
                builder.build_route(context, props, item.name, points, marks, fit, suffix,
                                    total_length=cum[-1], axis_note=axis_note,
                                    crossings=crossings, interval=interval)
                lines += 1
                labels += len(marks)
                extra = "" if not crossings else \
                    ", %d Überschneidung(en) mit Gleisachsen - Ril 883 Abschnitt 3 (1)" % crossings
                messages.append("%s%s: %.2f km, km %s bis %s, Marken alle %d m [%s] (%s)%s" % (
                    item.name, suffix, cum[-1] / 1000.0,
                    railgeom.format_km_db(fit.km(0.0)),
                    railgeom.format_km_db(fit.km(cum[-1])),
                    int(interval), axis_note, note, extra))

        for line in messages:
            print("[Kilometrierung] %s" % line)
        props.scan_info = " | ".join(messages[:3])

        if not lines:
            self.report({'WARNING'}, messages[0] if messages else "Nichts erzeugt")
            return {'CANCELLED'}
        self.report({'INFO'}, "%d Kilometrierungslinie(n), %d Marken erzeugt "
                              "(Details in der Systemkonsole)" % (lines, labels))
        return {'FINISHED'}


def _chainage_lines(context):
    """Alle vom Addon erzeugten Kilometrierungslinien der Szene."""
    return [o for o in context.scene.objects
            if o.type == 'CURVE' and o.get("chainage_source")]


def _terrain_objects(context):
    """Vom Addon erzeugte Korridor-Geländemodelle."""
    return [o for o in context.scene.objects
            if o.type == 'MESH' and o.get("corridor_radius_m")]


def _corridor_from_lines(context, radius):
    """Baut den Korridor aus allen Kilometrierungslinien (lokale Koordinaten)."""
    points = []
    for line in _chainage_lines(context):
        matrix = line.matrix_world
        for spline in line.data.splines:
            for point in spline.points:
                world = matrix @ point.co.to_3d()
                points.append((world.x, world.y))
    if len(points) < 2:
        return None
    return corridor_mod.Corridor(points, radius)


class _TileFetcher:
    """Lädt Höhenkacheln im Hintergrund, damit Blender bedienbar bleibt."""

    def __init__(self, model, keys):
        self.model = model
        self.keys = keys
        self.error = None
        self.status = "Höhendaten werden geladen …"
        self.fraction = -1.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def _fortschritt(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction

    def _run(self):
        try:
            self.model.load(self.keys, progress=self._fortschritt)
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc


class RC_OT_import_terrain(Operator):
    bl_idname = "railway_chainage.import_terrain"
    bl_label = "Gelände im Korridor laden"
    bl_description = ("Amtliches Höhenmodell entlang der Kilometrierungslinie laden und "
                      "nur im eingestellten Korridor als Gelände erzeugen")
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _fetcher = None
    _plan = None

    @classmethod
    def poll(cls, context):
        return bool(_chainage_lines(context))

    # ------------------------------------------------------------ Vorbereitung
    def _prepare(self, context, props):
        projection, _ = get_projection(context, props)
        bbox = get_bbox(props)
        if props.terrain_source == 'FLAT':
            zone = dgm.utm_zone((bbox[1] + bbox[3]) / 2.0)
            spacing = float(props.flat_spacing)
            model = dgm.FlatModel(props.flat_height, zone)
            label = "ebene Fläche (ohne Höhenmodell)"
        else:
            key = props.dem_service
            if key == 'AUTO':
                key = dgm.service_for((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
                if key is None:
                    return None
            entry = dgm.SERVICES[key]
            zone, label = entry["zone"], entry["label"]
            spacing = float(props.dem_spacing)
            fallback = dgm.TerrainTiles(zoom=props.dem_fallback_zoom,
                                        use_cache=props.use_cache) if props.dem_fill_gaps else None
            model = dgm.HeightModel(entry["url"], entry["coverage"], zone, spacing,
                                    use_cache=props.use_cache, axes=entry["axes"],
                                    fallback=fallback, kind=entry.get("kind", "wcs"))
            if fallback is not None:
                label += " + " + dgm.TERRARIUM_CREDIT

        jobs = []
        for line in _chainage_lines(context):
            matrix = line.matrix_world
            points = [(float((matrix @ p.co.to_3d()).x), float((matrix @ p.co.to_3d()).y))
                      for spline in line.data.splines for p in spline.points]
            if len(points) < 2:
                continue
            area = corridor_mod.Corridor(corridor_mod.to_utm(points, projection, zone),
                                         props.corridor_radius)
            wanted = corridor_mod.grid_points(area, spacing)
            if wanted:
                jobs.append((line, wanted, area))

        keys = sorted({key for _line, wanted, _area in jobs
                       for key in corridor_mod.tiles_for(wanted, model)})
        return {"model": model, "jobs": jobs, "keys": keys, "projection": projection,
                "zone": zone, "label": label, "spacing": spacing}

    # ------------------------------------------------------------ Aufbau
    def _build(self, context, props, plan):
        model, projection, zone = plan["model"], plan["projection"], plan["zone"]
        total_verts, total_faces, missing_total, created = 0, 0, 0, []

        for line, wanted, area in plan["jobs"]:
            verts, faces, missing, coords = corridor_mod.build_grid(
                wanted, model, projection, zone, area=area)
            if not verts:
                continue
            # Eine ebene Fläche liegt genau auf der eingestellten Höhe - ein
            # Höhenbezug aus BLOSM würde sie unbemerkt nach unten schieben.
            offset = 0.0
            if props.terrain_source == 'FLAT':
                pass
            elif props.dem_use_blosm_offset and "height_offset" in context.scene:
                offset = float(context.scene["height_offset"])
            elif props.dem_zero_level:
                offset = min(v[2] for v in verts)

            top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
            name = "Gelände %s" % line.name.replace("KM-Linie ", "")
            parts = builder.create_terrain_segments(
                context, name, verts, faces, coords, top, height_offset=offset,
                segment_length=props.terrain_segment_length, radius=props.corridor_radius)
            if not parts:
                continue
            if (props.dem_zero_level and props.terrain_source != 'FLAT'
                    and "height_offset" not in context.scene):
                context.scene["height_offset"] = offset
            for obj in parts:
                obj["chainage_line"] = line.name
                obj["dem_service"] = plan["label"]
                obj["dem_spacing_m"] = plan["spacing"]
                obj["corridor_radius_m"] = props.corridor_radius
                obj["corridor_length_m"] = round(area.length, 1)
                obj["utm_zone"] = zone
                created.append(obj.name)

            if props.drape_mode == 'DIRECT':
                self._drape(line, model, projection, zone, offset, props.z_offset)
            elif props.drape_mode == 'SHRINKWRAP':
                _apply_shrinkwrap(line, parts, props.z_offset)
            total_verts += len(verts)
            total_faces += len(faces)
            missing_total += missing

        missing_total += model.failed
        if not created:
            return None
        info = "Gelände erzeugt: %d Punkte, %d Flächen in %d Abschnitt(en), Raster %g m, " \
               "Korridor %g m" % (total_verts, total_faces, len(created), plan["spacing"],
                                  props.corridor_radius)
        if model.from_fallback:
            info += ", davon %d Punkte aus den weltweiten Ersatzdaten" % model.from_fallback
        if missing_total:
            info += " (%d Punkte ohne Höhenwert)" % missing_total
        return info

    @staticmethod
    def _drape(line, model, projection, zone, offset, z_offset):
        """Legt Linie, Marken und Beschriftung auf die Geländehöhe."""
        from mathutils import Vector

        def height_of(x, y):
            lat, lon = projection.to_geographic(x, y)
            east, north = dgm.wgs84_to_utm(lat, lon, zone)
            value = model.height_at(east, north)
            return None if value is None else value - offset + z_offset

        matrix = line.matrix_world
        inverse = matrix.inverted()
        for spline in line.data.splines:
            for point in spline.points:
                world = matrix @ point.co.to_3d()
                height = height_of(world.x, world.y)
                if height is None:
                    continue
                local = inverse @ Vector((world.x, world.y, height))
                point.co = (local.x, local.y, local.z, point.co[3])

        for child in line.children:
            if child.type == 'FONT':
                world = child.matrix_world.translation
                height = height_of(world.x, world.y)
                if height is not None:
                    child.location.z += height - world.z
            elif child.type == 'MESH' and child.data:
                child_matrix = child.matrix_world
                child_inverse = child_matrix.inverted()
                for vertex in child.data.vertices:
                    world = child_matrix @ vertex.co
                    height = height_of(world.x, world.y)
                    if height is None:
                        continue
                    vertex.co = child_inverse @ Vector((world.x, world.y, height))
                child.data.update()
        line.data.update_tag()

    # ------------------------------------------------------------ interaktiv
    def invoke(self, context, event):
        props = context.scene.railway_chainage
        self._plan = self._prepare(context, props)
        if self._plan is None:
            self.report({'ERROR'}, "Für dieses Gebiet gibt es keinen hinterlegten Höhendienst - "
                                   "bitte einen Dienst von Hand wählen")
            return {'CANCELLED'}
        if not self._plan["jobs"]:
            self.report({'ERROR'}, "Keine Kilometrierungslinie mit Punkten gefunden")
            return {'CANCELLED'}

        props.scan_info = ("Ebenes Gelände wird erzeugt …" if not self._plan["keys"]
                           else "Höhendaten: %d Kacheln werden geladen … (Esc bricht ab)"
                                % len(self._plan["keys"]))
        self._fetcher = _TileFetcher(self._plan["model"], self._plan["keys"])
        self._fetcher.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._release(context)
            props.scan_info = "Abgebrochen"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        if self._fetcher.running:
            _zeige_fortschritt(context, props, self._fetcher)
            return {'RUNNING_MODAL'}

        self._release(context)
        if self._fetcher.error is not None:
            message = str(self._fetcher.error).splitlines()[0]
            props.scan_info = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        info = self._build(context, props, self._plan)
        if info is None:
            self.report({'WARNING'}, "Keine Höhendaten im Korridor erhalten - liegt die "
                                     "Strecke im Gebiet des gewählten Dienstes?")
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        _redraw(context)
        return {'FINISHED'}

    def _release(self, context):
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    # ------------------------------------------------------------ aus Skripten
    def execute(self, context):
        props = context.scene.railway_chainage
        plan = self._prepare(context, props)
        if plan is None:
            self.report({'ERROR'}, "Für dieses Gebiet gibt es keinen hinterlegten Höhendienst")
            return {'CANCELLED'}
        if not plan["jobs"]:
            self.report({'ERROR'}, "Keine Kilometrierungslinie mit Punkten gefunden")
            return {'CANCELLED'}
        try:
            plan["model"].load(plan["keys"])
        except dgm.DgmError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        info = self._build(context, props, plan)
        if info is None:
            self.report({'WARNING'}, "Keine Höhendaten im Korridor erhalten")
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


def _apply_shrinkwrap(line, terrains, offset):
    """Legt Linie und Marken per Modifier auf das Gelände.

    Bei mehreren Geländeabschnitten bekommt jedes Objekt einen eigenen
    Modifier: Punkte, die ein Abschnitt nicht trifft, lässt er unverändert -
    in Reihe ergibt das die durchgehende Auflage.
    """
    if not isinstance(terrains, (list, tuple)):
        terrains = [terrains]
    terrains = [t for t in terrains if t is not None]
    if not terrains:
        return

    def apply(obj):
        for modifier in list(obj.modifiers):
            if modifier.name.startswith("Auf Gelände"):
                obj.modifiers.remove(modifier)
        for number, target in enumerate(terrains, start=1):
            name = "Auf Gelände" if len(terrains) == 1 else "Auf Gelände %02d" % number
            builder.add_shrinkwrap(obj, target, offset, name=name)

    apply(line)
    samplers = None
    for child in line.children:
        if child.type == 'MESH':
            apply(child)
        elif child.type == 'FONT':
            # Textobjekte würde ein Shrinkwrap verformen - daher nur die Höhe setzen
            if samplers is None:
                samplers = [builder.terrain_sampler(t) for t in terrains]
            world = child.matrix_world.translation
            for sampler in samplers:
                height = sampler(world.x, world.y)
                if height is not None:
                    child.location.z += height + offset - world.z
                    break


class RC_OT_shrinkwrap(Operator):
    bl_idname = "railway_chainage.shrinkwrap"
    bl_label = "Auf Gelände legen (Shrinkwrap)"
    bl_description = ("Kilometrierungslinie und Marken über einen Shrinkwrap-Modifier auf dem "
                      "Gelände halten - nicht zerstörend")
    bl_options = {'REGISTER', 'UNDO'}

    remove: bpy.props.BoolProperty(name="Entfernen", default=False)

    @classmethod
    def poll(cls, context):
        return bool(_chainage_lines(context))

    def execute(self, context):
        props = context.scene.railway_chainage
        lines = _chainage_lines(context)
        if not lines:
            self.report({'ERROR'}, "Keine Kilometrierungslinie in der Szene")
            return {'CANCELLED'}

        if self.remove:
            count = 0
            betroffen = list(lines)
            betroffen += [o for o in context.scene.objects
                          if o.type in {'CURVE', 'MESH'} and o.get("track_number")]
            for line in betroffen:
                for obj in [line] + list(line.children):
                    for modifier in list(obj.modifiers):
                        if modifier.name.startswith("Auf Gelände"):
                            obj.modifiers.remove(modifier)
                            count += 1
            self.report({'INFO'}, "%d Modifier entfernt" % count)
            return {'FINISHED'}

        terrains = _terrain_objects(context)
        if not terrains:
            terrains = [o for o in context.scene.objects
                        if o.type == 'MESH' and o.name.lower().startswith(("gelände", "terrain"))]
        if not terrains:
            self.report({'ERROR'}, "Kein Geländeobjekt gefunden - bitte zuerst Gelände laden "
                                   "oder ein Terrain in der Szene bereitstellen")
            return {'CANCELLED'}

        for line in lines:
            targets = [t for t in terrains if t.get("chainage_line") == line.name] or terrains
            _apply_shrinkwrap(line, targets, props.z_offset)

        # Gleisachsen ebenfalls auflegen
        tracks = [o for o in context.scene.objects
                  if o.type in {'CURVE', 'MESH'} and o.get("track_number")]
        for track in tracks:
            for modifier in list(track.modifiers):
                if modifier.name.startswith("Auf Gelände"):
                    track.modifiers.remove(modifier)
            for number, target in enumerate(terrains, start=1):
                name = "Auf Gelände" if len(terrains) == 1 else "Auf Gelände %02d" % number
                builder.add_shrinkwrap(track, target, props.z_offset, name=name)

        self.report({'INFO'}, "%d Linie(n) und %d Gleisachse(n) auf %d Geländeabschnitt(e) gelegt"
                    % (len(lines), len(tracks), len(terrains)))
        return {'FINISHED'}


class RC_OT_generate_rails(Operator):
    bl_idname = "railway_chainage.generate_rails"
    bl_label = "Gleisachsen erzeugen"
    bl_description = "Die Gleisachsen der ausgewählten Strecken als Splines anlegen"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.railway_chainage
        selected = [item for item in props.routes if item.selected]
        if not selected:
            self.report({'ERROR'}, "Keine Strecke ausgewählt - bitte zuerst Strecken suchen")
            return {'CANCELLED'}
        if not is_loaded(props):
            self.report({'ERROR'}, "Die geladenen Daten passen nicht zum eingestellten Gebiet - "
                                   "bitte erneut „Strecken suchen“")
            return {'CANCELLED'}

        rail = _STATE["rail"]
        routes = _STATE["routes"]
        projection = _STATE["projection"]

        terrains = _terrain_objects(context)
        terrain = terrains[0] if terrains else None
        sampler = None
        if terrain is not None and props.rails_drape == 'DIRECT':
            samplers = [builder.terrain_sampler(t) for t in terrains]
            def sampler(x, y):
                for sample in samplers:
                    value = sample(x, y)
                    if value is not None:
                        return value
                return None

        # Gemeinsam auswerten: Weichen können Strecken-/Bahnhofsgruppen verbinden.
        way_ids = set()
        for item in selected:
            route = routes.get(item.key)
            if route is not None:
                way_ids.update(route.way_ids)
        if not way_ids:
            self.report({'WARNING'}, "Keine Gleise in der Auswahl")
            return {'CANCELLED'}
        if not props.rails_all_tracks:
            chains = railgeom.build_chains(rail, way_ids, max_angle=props.max_angle)
            # Die Option bedeutet weiterhin nur das längste durchgehende Gleis.
            tracks = (tracknetwork.numbered_chain(rail, way_ids, chains[0])
                      if props.rails_object_mode == 'SEPARATE' else
                      [railgeom.polyline(rail, chains[0])[0]]) if chains else []
            switches, diagnostics = [], {"ambiguous": []}
        else:
            way_ids = tracknetwork.include_crossovers(rail, way_ids)
            tracks, switches, diagnostics = tracknetwork.build(
                rail, way_ids, separate=props.rails_separate_switches,
                arm_length=props.rails_switch_length,
                split_ways=props.rails_object_mode == 'SEPARATE',
                crossings=props.rails_crossings,
                max_angle=math.degrees(props.rails_max_diverging),
                transition=props.rails_transition,
                cap_crossings=props.rails_cap_crossings)
        if props.clip_to_area:
            def clip(points):
                pts, cum = _recompute(list(points))
                return [piece for piece, _ in railgeom.clip_to_bbox(
                    pts, cum, projection, get_bbox(props)) if len(piece) >= 2]
            tracks = [tracknetwork.copy_path(piece, points) for points in tracks for piece in clip(points)]
            complete = []
            for switch in switches:
                center, a, b = switch["points"]
                arms = [[center, a], [center, b]]
                pieces = [clip(arm) for arm in arms]
                if all(len(c) == 1 and all(math.dist(x,y) < 1e-7 for x,y in zip(c[0],arm))
                       for c,arm in zip(pieces,arms)):
                    complete.append(switch)
                else:
                    # Keine unvollständigen Drei-Vertex-Weichen am Gebietsrand.
                    for i, clipped in enumerate(pieces):
                        ids = switch.get('arm_way_ids', [(), ()])[i]
                        tracks.extend(tracknetwork.TrackPath(piece, ids, tracknetwork.path_metadata(rail, ids))
                                      for piece in clipped)
            switches = complete
        tracks = [tracknetwork.densify(t, props.rails_spacing) for t in tracks]
        label = selected[0].name if len(selected) == 1 else "Gleisbild Auswahl"
        if len(selected) > 1:
            for item in selected:
                legacy = bpy.data.collections.get("Gleise %s" % item.name.replace("/", "-"))
                if legacy is not None and legacy.name != "Gleise " + label:
                    builder.clear_generated(legacy)
                    for child in legacy.children:
                        if child.get("railway_switch_collection"):
                            builder.clear_generated(child)
        stats = builder.build_track_splines(
            context, props, label, tracks, sampler=sampler,
            terrain=terrains if props.rails_drape == 'SHRINKWRAP' else None,
            switches=switches)
        built = stats["gleise"] + stats["weichen"]
        messages = ["%s: %d Gleisabschnitte, %d separate Weichen, %.2f km" %
                    (label, stats["gleise"], stats["weichen"], stats["laenge_km"])]
        if diagnostics["ambiguous"]:
            messages.append("%d unklare Abzweige unverändert erhalten" %
                            len(diagnostics["ambiguous"]))
        netz = diagnostics.get("stats")
        if netz and switches:
            grenze = math.degrees(props.rails_max_diverging)
            text = "%d Weichen, %d Kreuzungsweichen, %d Kreuzungen" % (
                netz["Weiche"], netz["Kreuzungsweiche"], netz["Kreuzung"])
            if netz["angepasst"]:
                text += ", %d auf %.0f° gebracht (größte Drehung %.1f°)" % (
                    netz["angepasst"], grenze, netz["max_drehung"])
            if netz["belassen"]:
                text += ", %d einfache Kreuzung(en) steiler belassen" % netz["belassen"]
            messages.append(text)
            if grenze > 0:
                pruefung = tracknetwork.check_connections(tracks, switches, grenze)
                if pruefung["zu_steil"] or pruefung["oeffnung_zu_steil"]:
                    messages.append("Prüfung: %d Übergänge, %d über %.0f° (max. %.1f°), %d Weichen "
                                    "weiter geöffnet (max. %.1f°)" % (
                                        pruefung["uebergaenge"], len(pruefung["zu_steil"]), grenze,
                                        pruefung["max"], len(pruefung["oeffnung_zu_steil"]),
                                        pruefung["oeffnung_max"]))
                else:
                    messages.append("Prüfung: %d Übergänge höchstens %.1f°, Weichenöffnung "
                                    "höchstens %.1f°" % (pruefung["uebergaenge"], pruefung["max"],
                                                         pruefung["oeffnung_max"]))

        for line in messages:
            print("[Kilometrierung] Gleise: %s" % line)
        if not built:
            self.report({'WARNING'}, messages[0] if messages else "Nichts erzeugt")
            return {'CANCELLED'}
        info = " | ".join(messages)
        if terrain is None and props.rails_drape != 'NONE':
            info += " | kein Gelände gefunden - Achsen liegen auf der eingestellten Höhe"
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


class _MapFetcher:
    """Lädt Kartenkacheln im Hintergrund - je Zoomstufe getrennt."""

    def _fortschritt(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction


    def __init__(self, source_key, by_zoom, tile_url=""):
        self.source_key = source_key
        self.tile_url = tile_url
        self.by_zoom = by_zoom          # {zoom: [(x, y), …]}
        self.tiles = {}                 # {zoom: {(x, y): …}}
        self.error = None
        self.status = "Kartenkacheln werden geladen …"
        self.fraction = -1.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def _run(self):
        try:
            for zoom, keys in self.by_zoom.items():
                self.tiles[zoom] = maptexture.fetch_tiles(
                    self.source_key, keys, zoom, progress=self._fortschritt,
                    tile_url=self.tile_url)
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc


class RC_OT_map_texture(Operator):
    bl_idname = "railway_chainage.map_texture"
    bl_label = "Kartentextur laden"
    bl_description = ("Kartenbild entlang der Strecke auf das Korridorgelände legen - die "
                      "Textur folgt der Achse und bleibt dadurch klein")
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _fetcher = None
    _plan = None

    @classmethod
    def poll(cls, context):
        return bool(_terrain_objects(context))

    def _prepare(self, context, props):
        terrains = _terrain_objects(context)
        if props.map_selected_only:
            selected = set(context.selected_objects)
            terrains = [obj for obj in terrains if obj in selected]
        if not terrains:
            return None
        projection, _ = get_projection(context, props)
        lines = {line.name: line for line in _chainage_lines(context)}

        jobs = []
        zu_gross = None
        station_cache = {}
        axis_cache = {}
        for obj in sorted(terrains, key=lambda o: o.name):
            line = lines.get(obj.get("chainage_line", "")) or \
                (list(lines.values())[0] if lines else None)
            if line is None:
                continue
            axis = axis_cache.get(line.name)
            if axis is None:
                matrix = line.matrix_world
                axis = [(float((matrix @ p.co.to_3d()).x), float((matrix @ p.co.to_3d()).y))
                        for spline in line.data.splines for p in spline.points]
                axis_cache[line.name] = axis
            if len(axis) < 2:
                continue

            # Abschnitt der Strecke, den dieses Geländestück abdeckt
            start = obj.get("corridor_station_start")
            end = obj.get("corridor_station_end")
            if start is not None and end is not None and end > start:
                zone = int(obj.get("utm_zone", 32))
                key = (line.name, zone)
                stations = station_cache.get(key)
                if stations is None:
                    utm = corridor_mod.to_utm(axis, projection, zone)
                    stations = [0.0]
                    for i in range(1, len(utm)):
                        stations.append(stations[-1] + math.dist(utm[i - 1], utm[i]))
                    station_cache[key] = stations
                axis = railgeom.slice_by_stations(axis, stations, float(start), float(end))
                if len(axis) < 2:
                    continue

            radius = float(obj.get("corridor_radius_m", props.corridor_radius))
            laenge = sum(math.dist(axis[i], axis[i + 1]) for i in range(len(axis) - 1))
            vorab_w = min(int(props.map_max_edge),
                          max(16, int(math.ceil(laenge / map_resolution(props)))))
            vorab_h = min(int(props.map_max_edge),
                          max(8, int(round(2.0 * radius / map_resolution(props)))))
            if vorab_w * vorab_h > MAX_TEXTURE_PIXELS:
                zu_gross = (vorab_w, vorab_h)
                continue
            grid = builder.corridor_latlon_grid(axis, radius, projection,
                                                map_resolution(props), props.map_max_edge)
            lat, lon, width, height, length, step = grid
            middle = float(lat[lat.shape[0] // 2, lat.shape[1] // 2])
            source = maptexture.SOURCES[props.map_source]
            max_zoom = source[1] if not props.tile_url.strip() else 22
            zoom = maptexture.choose_zoom(middle, step, max_zoom)
            keys = builder.corridor_tile_keys(lat, lon, zoom)
            jobs.append({"object": obj, "grid": grid, "zoom": zoom, "keys": keys})
        if not jobs and zu_gross:
            return {"too_large": zu_gross}
        return jobs

    @staticmethod
    def _keys_by_zoom(jobs):
        """Benötigte Kacheln, nach Zoomstufe gruppiert."""
        by_zoom = {}
        for job in jobs:
            by_zoom.setdefault(job["zoom"], set()).update(job["keys"])
        return {zoom: sorted(keys) for zoom, keys in by_zoom.items()}

    def _build(self, context, props, jobs, tiles_by_zoom):
        built = []
        cache = {}      # geladene Kacheln über alle Abschnitte hinweg wiederverwenden
        for number, job in enumerate(jobs, start=1):
            lat, lon, width, height, length, step = job["grid"]
            name = "Karte %s %s" % (job["object"].name[:35], uuid.uuid4().hex[:12])
            image = builder.corridor_texture(lat, lon, width, height, step,
                                             props.map_source,
                                             tiles_by_zoom.get(job["zoom"], {}),
                                             job["zoom"], name=name,
                                             tile_url=props.tile_url.strip(), cache=cache)
            builder.apply_map_material(job["object"], image, name=name)
            built.append("%d x %d Pixel, %.2f m/Pixel, Zoom %d" %
                         (width, height, step, job["zoom"]))
        if len(built) > 1:
            return ["%d Abschnitte je %s" % (len(built), built[0])]
        return built

    def invoke(self, context, event):
        props = context.scene.railway_chainage
        jobs = self._prepare(context, props)
        if isinstance(jobs, dict) and jobs.get("too_large"):
            self.report({'ERROR'}, _zu_gross(jobs["too_large"]))
            return {'CANCELLED'}
        if not jobs:
            self.report({'ERROR'}, "Kein passendes Korridorgelände gefunden – Auswahl und Gelände prüfen")
            return {'CANCELLED'}
        by_zoom = self._keys_by_zoom(jobs)
        total = sum(len(keys) for keys in by_zoom.values())
        pixels = sum(job["grid"][2] * job["grid"][3] for job in jobs)
        problem = check_tile_budget(props, total, pixels)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}

        self._plan = jobs
        props.scan_info = "Kartentextur: %d Kacheln werden geladen … (Esc bricht ab)" % total
        self._fetcher = _MapFetcher(props.map_source, by_zoom, props.tile_url.strip())
        self._fetcher.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._release(context)
            props.scan_info = "Abgebrochen"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if self._fetcher.running:
            _zeige_fortschritt(context, props, self._fetcher)
            return {'RUNNING_MODAL'}

        self._release(context)
        if self._fetcher.error is not None:
            message = str(self._fetcher.error).splitlines()[0]
            props.scan_info = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        built = self._build(context, props, self._plan, self._fetcher.tiles)
        info = " | ".join(built) + " | Quelle: " + maptexture.credit(props.map_source)
        props.scan_info = info
        self.report({'INFO'}, info)
        _redraw(context)
        return {'FINISHED'}

    def _release(self, context):
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def execute(self, context):
        props = context.scene.railway_chainage
        jobs = self._prepare(context, props)
        if isinstance(jobs, dict) and jobs.get("too_large"):
            self.report({'ERROR'}, _zu_gross(jobs["too_large"]))
            return {'CANCELLED'}
        if not jobs:
            self.report({'ERROR'}, "Kein passendes Korridorgelände gefunden – Auswahl und Gelände prüfen")
            return {'CANCELLED'}
        by_zoom = self._keys_by_zoom(jobs)
        total = sum(len(keys) for keys in by_zoom.values())
        pixels = sum(job["grid"][2] * job["grid"][3] for job in jobs)
        problem = check_tile_budget(props, total, pixels)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        tiles = {}
        try:
            for zoom, keys in by_zoom.items():
                tiles[zoom] = maptexture.fetch_tiles(props.map_source, keys, zoom,
                                                     tile_url=props.tile_url.strip())
        except maptexture.MapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        built = self._build(context, props, jobs, tiles)
        info = " | ".join(built) + " | Quelle: " + maptexture.credit(props.map_source)
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


class _AreaFetcher:
    """Lädt Höhen- und Kartendaten eines Gebiets im Hintergrund."""

    def _fortschritt(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction


    def __init__(self, job):
        self.job = job
        self.error = None
        self.status = "Daten werden geladen …"
        self.fraction = -1.0
        self.result = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def _run(self):
        try:
            job = self.job
            job["model"].load(job["tiles"], progress=self._fortschritt)
            if job["map_keys"]:
                self.status = "Kartenkacheln werden geladen …"
                job["map_tiles"] = maptexture.fetch_tiles(
                    job["map_source"], job["map_keys"], job["map_zoom"],
                    progress=self._fortschritt, tile_url=job["tile_url"])
            self.result = True
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc


class RC_OT_import_area(Operator):
    bl_idname = "railway_chainage.import_area"
    bl_label = "Gebiet als Fläche laden"
    bl_description = ("Geländemodell für das eingestellte Gebiet laden und mit der Karte "
                      "belegen - ohne Bezug zu einer Strecke")
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _fetcher = None

    def _prepare(self, context, props):
        bbox = get_bbox(props)
        if props.terrain_source == 'FLAT':
            zone = dgm.utm_zone((bbox[1] + bbox[3]) / 2.0)
            spacing = float(props.flat_spacing)
            model = dgm.FlatModel(props.flat_height, zone)
            label = "ebene Fläche (ohne Höhenmodell)"
        else:
            key = props.dem_service
            if key == 'AUTO':
                key = dgm.service_for((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
            if key is None:
                return None
            entry = dgm.SERVICES[key]
            zone = entry["zone"]
            label = entry["label"]
            spacing = float(props.dem_spacing)
            fallback = dgm.TerrainTiles(zoom=props.dem_fallback_zoom,
                                        use_cache=props.use_cache) if props.dem_fill_gaps else None
            model = dgm.HeightModel(entry["url"], entry["coverage"], zone, spacing,
                                    use_cache=props.use_cache, axes=entry["axes"],
                                    fallback=fallback, kind=entry.get("kind", "wcs"))

        # Das Gelände wird entlang der Szenenachsen aufgebaut, nicht entlang der
        # UTM-Achsen - sonst steht der Ausschnitt um die Meridiankonvergenz
        # verdreht in der Szene.
        projection, _ = get_projection(context, props)
        lokal = corridor_mod.area_bounds_local(bbox, projection, spacing)
        bounds = corridor_mod.local_bounds_utm(lokal, projection, zone, reserve=spacing)
        size = model.tile_size
        tiles = [] if props.terrain_source == 'FLAT' else sorted(
            {(int(e // size), int(n // size))
             for e in _steps(bounds[0], bounds[2], size)
             for n in _steps(bounds[1], bounds[3], size)})

        job = {"model": model, "tiles": tiles, "zone": zone, "bbox": bbox,
               "spacing": spacing, "label": label, "bounds": bounds, "lokal": lokal,
               "projection": projection,
               "map_keys": [], "map_zoom": 0, "map_tiles": {},
               "map_source": props.map_source, "tile_url": props.tile_url.strip()}

        if props.area_with_map:
            # Umfang zuerst rechnerisch prüfen - das Gitter selbst belegt je Pixel
            # mehrere Fließkommawerte und darf gar nicht erst zu groß entstehen
            vorab_w = min(int(props.map_max_edge),
                          max(16, int(math.ceil((lokal[2] - lokal[0]) / map_resolution(props)))))
            vorab_h = min(int(props.map_max_edge),
                          max(16, int(math.ceil((lokal[3] - lokal[1]) / map_resolution(props)))))
            if vorab_w * vorab_h > MAX_TEXTURE_PIXELS:
                job["too_large"] = (vorab_w, vorab_h)
                return job
            lat, lon, width, height, step = builder.local_latlon_grid(
                lokal, projection, map_resolution(props), props.map_max_edge)
            middle = float(lat[lat.shape[0] // 2, lat.shape[1] // 2])
            max_zoom = maptexture.SOURCES[props.map_source][1] if not job["tile_url"] else 22
            zoom = maptexture.choose_zoom(middle, step, max_zoom)
            job.update({"grid": (lat, lon, width, height, step), "map_zoom": zoom,
                        "map_keys": builder.corridor_tile_keys(lat, lon, zoom)})
        return job

    def _build(self, context, props, job):
        projection = job["projection"]
        verts, faces, uvs, missing, bounds = corridor_mod.area_grid_local(
            job["lokal"], job["zone"], job["spacing"], job["model"], projection)
        if not verts:
            return None

        offset = 0.0
        if props.terrain_source == 'FLAT':
            pass
        elif props.dem_use_blosm_offset and "height_offset" in context.scene:
            offset = float(context.scene["height_offset"])
        elif props.dem_zero_level:
            offset = min(v[2] for v in verts)

        top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
        obj = builder.create_area_terrain(context, "Gelände Gebiet", verts, faces, uvs,
                                          top, offset)
        if (props.dem_zero_level and props.terrain_source != 'FLAT'
                and "height_offset" not in context.scene):
            context.scene["height_offset"] = offset
        obj["dem_service"] = job["label"]
        obj["dem_spacing_m"] = job["spacing"]
        obj["utm_zone"] = job["zone"]

        info = "Gebiet geladen: %d Punkte, %d Flächen, Raster %g m" % (
            len(verts), len(faces), job["spacing"])
        if job["model"].from_fallback:
            info += ", davon %d aus Ersatzdaten" % job["model"].from_fallback
        if missing:
            info += " (%d ohne Höhenwert)" % missing

        if props.area_with_map and job.get("grid"):
            lat, lon, width, height, step = job["grid"]
            image = builder.corridor_texture(lat, lon, width, height, step, props.map_source,
                                             job["map_tiles"], job["map_zoom"],
                                             name="Gebietskarte", tile_url=job["tile_url"])
            builder.apply_map_material(obj, image, name="Gebietskarte")
            info += " | Karte %d x %d Pixel, %.2f m/Pixel, Zoom %d" % (
                width, height, step, job["map_zoom"])
        return info

    def invoke(self, context, event):
        props = context.scene.railway_chainage
        job = self._prepare(context, props)
        if job is None:
            self.report({'ERROR'}, "Für dieses Gebiet gibt es keinen hinterlegten Höhendienst")
            return {'CANCELLED'}
        if job.get("too_large"):
            self.report({'ERROR'}, _zu_gross(job["too_large"]))
            return {'CANCELLED'}
        problem = check_tile_budget(props, len(job["map_keys"]),
                                    job["grid"][2] * job["grid"][3] if job.get("grid") else 0)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        props.scan_info = "Gebiet: %d Höhenkacheln, %d Kartenkacheln …" % (
            len(job["tiles"]), len(job["map_keys"]))
        self._fetcher = _AreaFetcher(job)
        self._fetcher.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._release(context)
            props.scan_info = "Abgebrochen"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if self._fetcher.running:
            _zeige_fortschritt(context, props, self._fetcher)
            return {'RUNNING_MODAL'}

        self._release(context)
        if self._fetcher.error is not None:
            message = str(self._fetcher.error).splitlines()[0]
            props.scan_info = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        info = self._build(context, props, self._fetcher.job)
        if info is None:
            self.report({'WARNING'}, "Keine Höhendaten für dieses Gebiet erhalten")
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        _redraw(context)
        return {'FINISHED'}

    def _release(self, context):
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def execute(self, context):
        props = context.scene.railway_chainage
        job = self._prepare(context, props)
        if job is None:
            self.report({'ERROR'}, "Für dieses Gebiet gibt es keinen hinterlegten Höhendienst")
            return {'CANCELLED'}
        if job.get("too_large"):
            self.report({'ERROR'}, _zu_gross(job["too_large"]))
            return {'CANCELLED'}
        problem = check_tile_budget(props, len(job["map_keys"]),
                                    job["grid"][2] * job["grid"][3] if job.get("grid") else 0)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        try:
            job["model"].load(job["tiles"])
            if job["map_keys"]:
                job["map_tiles"] = maptexture.fetch_tiles(
                    job["map_source"], job["map_keys"], job["map_zoom"],
                    tile_url=job["tile_url"])
        except Exception as exc:                                  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        info = self._build(context, props, job)
        if info is None:
            self.report({'WARNING'}, "Keine Höhendaten für dieses Gebiet erhalten")
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


class RC_OT_clip_scene(Operator):
    bl_idname = "railway_chainage.clip_scene"
    bl_label = "Szene auf Korridor zuschneiden"
    bl_description = ("Alles außerhalb des Korridors entfernen - Flächen, Objekte und "
                      "die zugehörigen Bildtexturen")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_chainage_lines(context))

    def execute(self, context):
        props = context.scene.railway_chainage
        area = _corridor_from_lines(context, props.corridor_radius)
        if area is None:
            self.report({'ERROR'}, "Keine Kilometrierungslinie in der Szene")
            return {'CANCELLED'}

        try:
            stats = builder.clip_scene(
                context, area,
                crop_textures=props.clip_crop_textures,
                max_texture=props.clip_max_texture)
        except Exception as exc:                                  # noqa: BLE001
            self.report({'ERROR'}, "Zuschnitt fehlgeschlagen: %s" % exc)
            print("[Kilometrierung] Zuschnitt: %s" % exc)
            return {'CANCELLED'}

        info = "%d Objekte entfernt, %d zugeschnitten, %d Flächen gelöscht" % (
            stats["objekte_entfernt"], stats["objekte_zugeschnitten"],
            stats["flaechen_entfernt"])
        for line in stats["texturen"]:
            print("[Kilometrierung] Textur %s" % line)
        if stats["texturen"]:
            info += ", %d Textur(en) zugeschnitten" % len(stats["texturen"])
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


class RC_OT_select_routes(Operator):
    bl_idname = "railway_chainage.select_routes"
    bl_label = "Auswahl"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=(('ALL', "Alle", ""), ('NONE', "Keine", ""),
                                ('MILESTONES', "Mit Tafeln", "")))

    def execute(self, context):
        for item in context.scene.railway_chainage.routes:
            if self.action == 'ALL':
                item.selected = True
            elif self.action == 'NONE':
                item.selected = False
            else:
                item.selected = item.milestone_count > 0
        return {'FINISHED'}


class RC_OT_from_blosm(Operator):
    bl_idname = "railway_chainage.from_blosm"
    bl_label = "Gebiet von BLOSM"
    bl_description = "Das im BLOSM-Addon eingestellte Gebiet übernehmen"

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, "blosm")

    def execute(self, context):
        props = context.scene.railway_chainage
        blosm = getattr(context.scene, "blosm", None)
        if blosm is None:
            self.report({'ERROR'}, "BLOSM ist nicht installiert oder nicht aktiviert")
            return {'CANCELLED'}
        try:
            props.min_lat, props.max_lat = blosm.minLat, blosm.maxLat
            props.min_lon, props.max_lon = blosm.minLon, blosm.maxLon
        except AttributeError:
            self.report({'ERROR'}, "Im BLOSM-Addon ist kein Gebiet gesetzt")
            return {'CANCELLED'}
        props.area_mode = 'BBOX'
        props.center_lat = (props.min_lat + props.max_lat) / 2.0
        props.center_lon = (props.min_lon + props.max_lon) / 2.0
        self.report({'INFO'}, "Gebiet von BLOSM übernommen")
        return {'FINISHED'}


class RC_OT_paste_bbox(Operator):
    bl_idname = "railway_chainage.paste_bbox"
    bl_label = "Aus Zwischenablage"
    bl_description = "Gebiet aus einer kopierten Adresse (OpenRailwayMap, OpenStreetMap, " \
                     "Google Maps) oder aus Koordinaten in der Zwischenablage übernehmen"

    def execute(self, context):
        props = context.scene.railway_chainage
        text = (context.window_manager.clipboard or "").strip()
        if not text:
            self.report({'ERROR'}, "Die Zwischenablage ist leer")
            return {'CANCELLED'}

        num = r"-?\d+\.?\d*"

        # 1) OSM-Export: ?bbox=west,south,east,north
        m = re.search(r"bbox=(%s),(%s),(%s),(%s)" % (num, num, num, num), text)
        if m:
            west, south, east, north = (float(v) for v in m.groups())
            props.area_mode = 'BBOX'
            props.min_lat, props.max_lat = min(south, north), max(south, north)
            props.min_lon, props.max_lon = min(west, east), max(west, east)
            props.center_lat = (props.min_lat + props.max_lat) / 2.0
            props.center_lon = (props.min_lon + props.max_lon) / 2.0
            self.report({'INFO'}, "Gebiet übernommen")
            return {'FINISHED'}

        lat = lon = None
        # 2) OpenRailwayMap / allgemeine Parameter: lat=…&lon=…
        m = re.search(r"[?&]lat=(%s).*?[?&]lon=(%s)" % (num, num), text)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
        if lat is None:
            # 3) OpenStreetMap: #map=zoom/lat/lon
            m = re.search(r"#map=\d+(?:\.\d+)?/(%s)/(%s)" % (num, num), text)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
        if lat is None:
            # 4) Google Maps: @lat,lon,17z
            m = re.search(r"@(%s),(%s)" % (num, num), text)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
        if lat is None:
            # 5) vier Zahlen: west,süd,ost,nord (z. B. aus BLOSM oder QGIS)
            m = re.match(r"^\s*(%s)\s*[,;]\s*(%s)\s*[,;]\s*(%s)\s*[,;]\s*(%s)\s*$"
                         % (num, num, num, num), text)
            if m:
                west, south, east, north = (float(v) for v in m.groups())
                props.area_mode = 'BBOX'
                props.min_lat, props.max_lat = min(south, north), max(south, north)
                props.min_lon, props.max_lon = min(west, east), max(west, east)
                props.center_lat = (props.min_lat + props.max_lat) / 2.0
                props.center_lon = (props.min_lon + props.max_lon) / 2.0
                self.report({'INFO'}, "Gebiet übernommen")
                return {'FINISHED'}

        if lat is None:
            # 6) reines Koordinatenpaar
            m = re.match(r"^\s*(%s)\s*[,;]\s*(%s)\s*$" % (num, num), text)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))

        if lat is None or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            self.report({'ERROR'}, "In der Zwischenablage wurden keine Koordinaten gefunden")
            return {'CANCELLED'}

        props.center_lat, props.center_lon = lat, lon
        props.area_mode = 'CENTER'
        bbox = get_bbox(props)
        props.min_lat, props.min_lon, props.max_lat, props.max_lon = bbox
        self.report({'INFO'}, "Mittelpunkt %.5f / %.5f übernommen" % (lat, lon))
        return {'FINISHED'}


class RC_OT_open_map(Operator):
    bl_idname = "railway_chainage.open_map"
    bl_label = "In eigener Karte ansehen"
    bl_description = ("Das eingestellte Gebiet in der eigenen Kartenanwendung öffnen "
                      "(Adresse im Feld darunter)")

    def execute(self, context):
        props = context.scene.railway_chainage
        bbox = get_bbox(props)
        lat = (bbox[0] + bbox[2]) / 2.0
        lon = (bbox[1] + bbox[3]) / 2.0
        zoom = 13
        span = max(abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1]) * 0.6) or 0.01
        while zoom > 3 and span > 360.0 / (2 ** zoom) * 1.5:
            zoom -= 1

        url = props.map_view_url.strip()
        if not url:
            self.report({'ERROR'}, "Keine Adresse für die Kartenansicht eingetragen")
            return {'CANCELLED'}
        if "{lat}" in url or "{lon}" in url or "{zoom}" in url:
            url = url.replace("{lat}", "%.6f" % lat).replace("{lon}", "%.6f" % lon) \
                     .replace("{zoom}", str(zoom))
        else:
            # Overpass turbo versteht die Kartenposition als C=lat;lon;zoom
            separator = "&" if "?" in url else "?"
            url = "%s%sC=%.6f;%.6f;%d" % (url.rstrip("#"), separator, lat, lon, zoom)
        bpy.ops.wm.url_open(url=url)
        self.report({'INFO'}, "Kartenansicht geöffnet")
        return {'FINISHED'}


def _selector_tiles(props):
    """Kacheladresse und Zoomgrenze für die Auswahlkarte.

    Ehrenamtlich betriebene Kartenserver bleiben außen vor - beim Herumfahren
    auf der Karte kämen dort schnell hunderte Abrufe zusammen.
    """
    eigen = props.tile_url.strip()
    if eigen:
        return eigen, 22
    quelle = maptexture.SOURCES.get(props.map_source)
    if quelle is None or props.map_source in maptexture.VOLUNTEER_SOURCES:
        return selector.DEFAULT_TILES, 19
    return quelle[0], quelle[1]


def _set_area(props, bbox):
    """Gewähltes Rechteck in die Eigenschaften übernehmen."""
    props.area_mode = 'BBOX'
    props.min_lat, props.min_lon, props.max_lat, props.max_lon = bbox
    props.center_lat = (bbox[0] + bbox[2]) / 2.0
    props.center_lon = (bbox[1] + bbox[3]) / 2.0


class RC_OT_select_area(Operator):
    bl_idname = "railway_chainage.select_area"
    bl_label = "Gebiet auswählen"
    bl_description = ("Karte im Browser öffnen, dort ein Rechteck aufziehen und mit einem "
                      "Klick nach Blender übernehmen - ohne Umweg über die Zwischenablage")
    bl_options = {'REGISTER', 'UNDO'}

    # Nach dieser Zeit ohne Rückmeldung wird die Auswahl beendet
    TIMEOUT = 900.0

    _timer = None
    _server = None
    _wartet = 0.0

    def _seite(self, props):
        bbox = get_bbox(props)
        kacheln, max_zoom = _selector_tiles(props)
        quelle = maptexture.SOURCES.get(props.map_source)
        return selector.build_html(
            bbox[0], bbox[1], bbox[2], bbox[3],
            tile_url=kacheln, max_zoom=max_zoom,
            attribution=quelle[4] if quelle and not props.tile_url.strip() else "",
            with_server=True)

    def invoke(self, context, event):
        props = context.scene.railway_chainage
        try:
            self._server = selector.SelectorServer(self._seite(props)).start()
        except OSError as exc:
            # Kein lokaler Server möglich - dann über die Zwischenablage
            self.report({'WARNING'}, "Lokaler Auswahldienst nicht möglich (%s) - die Seite "
                                     "wird als Datei geöffnet, bitte „Koordinaten kopieren“ "
                                     "und dann „Aus Zwischenablage“" % exc)
            return self.execute(context)

        bpy.ops.wm.url_open(url=self._server.url)
        props.scan_info = ("Auswahlkarte geöffnet - Rechteck aufziehen, dann dort auf "
                           "„Nach Blender übernehmen“ (Esc bricht ab)")
        self._wartet = 0.0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.3, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._release(context)
            props.scan_info = "Gebietsauswahl abgebrochen"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        bbox = self._server.bbox
        if bbox is None:
            self._wartet += 0.3
            if self._wartet > self.TIMEOUT:
                self._release(context)
                props.scan_info = "Gebietsauswahl beendet (keine Rückmeldung)"
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}

        _set_area(props, bbox)
        self._release(context)
        info = ("Gebiet übernommen: %.5f, %.5f bis %.5f, %.5f"
                % (bbox[1], bbox[0], bbox[3], bbox[2]))
        props.scan_info = info
        self.report({'INFO'}, info)
        _redraw(context)
        return {'FINISHED'}

    def _release(self, context):
        if self._server is not None:
            self._server.stop()
            self._server = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def execute(self, context):
        """Rückfallweg: Seite als Datei, Übergabe über die Zwischenablage."""
        props = context.scene.railway_chainage
        bbox = get_bbox(props)
        kacheln, max_zoom = _selector_tiles(props)
        try:
            path = selector.build_page(bbox[0], bbox[1], bbox[2], bbox[3],
                                       tile_url=kacheln, max_zoom=max_zoom)
        except OSError as exc:
            self.report({'ERROR'}, "Auswahlseite konnte nicht angelegt werden: %s" % exc)
            return {'CANCELLED'}
        bpy.ops.wm.url_open(url="file://" + path)
        self.report({'INFO'}, "Auswahlseite geöffnet - Rechteck aufziehen, „Koordinaten "
                              "kopieren“, dann „Aus Zwischenablage“")
        return {'FINISHED'}


class RC_OT_select_area_blosm(Operator):
    bl_idname = "railway_chainage.select_area_blosm"
    bl_label = "BLOSM-Karte"
    bl_description = ("Die Auswahlkarte des BLOSM-Addons (prochitecture.com) im Browser "
                      "öffnen. Dort Rechteck aufziehen, Koordinaten kopieren und hier "
                      "„Aus Zwischenablage“ wählen - benötigt eine Internetverbindung")

    def execute(self, context):
        version = bpy.app.version
        bpy.ops.wm.url_open(url=selector.blosm_url(blender_version=version))
        self.report({'INFO'}, "BLOSM-Auswahlkarte geöffnet - dort kopieren, hier "
                              "„Aus Zwischenablage“")
        return {'FINISHED'}


class _BuildingFetcher:
    """Lädt Gebäudegrundrisse im Hintergrund."""

    def __init__(self, props, bbox):
        self._props = props
        self._bbox = bbox
        self.data = None
        self.error = None
        self.status = "Gebäudedaten werden geladen …"
        self.fraction = -1.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def _fortschritt(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction

    def _run(self):
        try:
            self.data = overpass.fetch_buildings(
                self._bbox, timeout=self._props.request_timeout,
                use_cache=self._props.use_cache,
                endpoint=overpass_endpoint(self._props),
                with_parts=self._props.buildings_parts,
                progress=self._fortschritt)
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc


def _terrain_samplers(context):
    """Höhenabfragen über alle vom Addon erzeugten Geländeobjekte."""
    flaechen = [o for o in context.scene.objects
                if o.type == 'MESH' and o.get("dem_service")]
    return [builder.terrain_sampler(o) for o in flaechen], flaechen


class RC_OT_import_buildings(Operator):
    bl_idname = "railway_chainage.import_buildings"
    bl_label = "Gebäude erzeugen"
    bl_description = ("Gebäudegrundrisse aus OpenStreetMap für das eingestellte Gebiet laden "
                      "und als Baukörper erzeugen")
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _fetcher = None

    # ------------------------------------------------------------ Aufbau
    def _build(self, context, props, data):
        projection, _ = get_projection(context, props)
        bbox = get_bbox(props)
        innerhalb = buildings.bbox_test(projection, bbox)

        if props.buildings_only_corridor:
            korridor = _corridor_from_lines(context, props.corridor_radius)
            if korridor is None:
                return None, "Für „Nur im Korridor“ wird eine Kilometrierungslinie gebraucht"
            im_gebiet = innerhalb

            def innerhalb(x, y):                                  # noqa: F811
                return im_gebiet(x, y) and korridor.contains(x, y)

        haeuser = buildings.parse(data, projection,
                                  default_height=props.buildings_default_height,
                                  level_height=props.buildings_level_height,
                                  min_area=props.buildings_min_area,
                                  bbox_filter=innerhalb)
        if not haeuser:
            return None, "Im Gebiet sind keine Gebäude eingetragen"
        if len(haeuser) > props.buildings_max:
            return None, ("Das Gebiet enthält %d Gebäude (Grenze %d). Bitte das Gebiet "
                          "verkleinern, die Mindestfläche erhöhen oder die Höchstzahl "
                          "anheben" % (len(haeuser), props.buildings_max))

        samplers = []
        gelaende = []
        if props.buildings_drape:
            samplers, gelaende = _terrain_samplers(context)

        top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
        sammlung = builder.ensure_collection("Gebäude", top)
        obj, ohne_gelaende = builder.create_buildings(
            context, "Gebäude", haeuser, sammlung, samplers=samplers,
            skirt=props.buildings_skirt)
        if obj is None:
            return None, "Es konnte kein Gebäudekörper erzeugt werden"

        kennzahlen = buildings.statistics(haeuser)
        obj["osm_buildings"] = kennzahlen["anzahl"]
        info = ("%d Gebäude erzeugt, %.1f ha Grundfläche, mittlere Höhe %.1f m; "
                "%d davon mit Höhenangabe aus OSM"
                % (kennzahlen["anzahl"], kennzahlen["flaeche"] / 10000.0,
                   kennzahlen["hoehe"], kennzahlen["mit_angabe"]))
        if gelaende:
            info += " | auf %d Geländeabschnitt(e) gesetzt" % len(gelaende)
            if ohne_gelaende:
                info += ", %d ohne Geländetreffer" % ohne_gelaende
        return info, None

    # ------------------------------------------------------------ interaktiv
    def invoke(self, context, event):
        props = context.scene.railway_chainage
        self._fetcher = _BuildingFetcher(props, get_bbox(props))
        self._fetcher.start()
        props.scan_info = "Gebäudedaten werden geladen … (Esc bricht ab)"
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._release(context)
            props.scan_info = "Abgebrochen"
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if self._fetcher.running:
            _zeige_fortschritt(context, props, self._fetcher)
            return {'RUNNING_MODAL'}

        self._release(context)
        if self._fetcher.error is not None:
            message = str(self._fetcher.error).splitlines()[0]
            props.scan_info = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        info, problem = self._build(context, props, self._fetcher.data)
        if problem:
            props.scan_info = problem
            self.report({'WARNING'}, problem)
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        _redraw(context)
        return {'FINISHED'}

    def _release(self, context):
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    # ------------------------------------------------------------ aus Skripten
    def execute(self, context):
        props = context.scene.railway_chainage
        try:
            data = overpass.fetch_buildings(
                get_bbox(props), timeout=props.request_timeout, use_cache=props.use_cache,
                endpoint=overpass_endpoint(props), with_parts=props.buildings_parts)
        except Exception as exc:                                  # noqa: BLE001
            self.report({'ERROR'}, str(exc).splitlines()[0])
            return {'CANCELLED'}
        info, problem = self._build(context, props, data)
        if problem:
            self.report({'WARNING'}, problem)
            return {'CANCELLED'}
        props.scan_info = info
        self.report({'INFO'}, info)
        return {'FINISHED'}


class RC_OT_organize_tracks(Operator):
    bl_idname = "railway_chainage.organize_tracks"
    bl_label = "Vorhandene Gleise zusammenfassen"
    bl_description = "Plugin-Gleisabschnitte zu einem Objekt je Import vereinigen und Weichen in eine eigene Sammlung verschieben"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        stats = builder.organize_track_objects(context)
        self.report({'INFO'}, "%d Gleisobjekte, %d einzelne Weichen" %
                    (stats["gleisobjekte"], stats["weichen"]))
        return {'FINISHED'}


class RC_OT_clear_cache(Operator):
    bl_idname = "railway_chainage.clear_cache"
    bl_label = "Zwischenspeicher leeren"
    bl_description = "Lokal gespeicherte Serverantworten löschen und beim nächsten Mal neu laden"

    def execute(self, context):
        count = overpass.clear_cache()
        _STATE.update({"rail": None, "routes": {}, "signature": None})
        self.report({'INFO'}, "%d zwischengespeicherte Antwort(en) gelöscht" % count)
        return {'FINISHED'}


classes = (RC_OT_scan, RC_OT_generate, RC_OT_import_terrain, RC_OT_import_area,
           RC_OT_map_texture, RC_OT_import_buildings,
           RC_OT_shrinkwrap, RC_OT_generate_rails, RC_OT_clip_scene,
           RC_OT_select_routes, RC_OT_from_blosm, RC_OT_paste_bbox,
           RC_OT_open_map, RC_OT_select_area, RC_OT_select_area_blosm,
           RC_OT_clear_cache, RC_OT_organize_tracks)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

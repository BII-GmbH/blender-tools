"""Operatoren: Gebiet festlegen, Strecken suchen, Kilometrierung erzeugen."""

import math
import queue
import re
import threading
import urllib.error
import urllib.parse
import uuid

import bpy
import numpy as np
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import (buildings, builder, corridor as corridor_mod, dgm, geoproxy, maptexture, simbox,
               overpass, railgeom, selector, tracknetwork)
from .projection import TransverseMercator

# Obergrenze je Kartentextur (RGBA, rund 4 Byte je Pixel). Jede Textur wird
# einzeln gebaut und später nach Unity übernommen, daher gilt die Grenze je Bild,
# nicht für die Summe. Beim Aufbau eines Texturgitters fallen je Pixel mehrere
# Zwischenwerte an; die Grenze liegt daher deutlich unter dem reinen Bildspeicher.
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
    """Projektionsmittelpunkt bestimmen - kompatibel zu BLOSM.

    Ein neuer Mittelpunkt wird sofort in der Szene hinterlegt (scene["lat"] /
    scene["lon"]), bei jedem Bauschritt: Gelände, Kartentextur, Fläche und
    Gebäude allein brauchen ihn genauso wie die Gleise, und andere Add-ons
    (BLOSM, BII Import) richten sich danach.
    """
    scene = context.scene
    if props.use_scene_origin and "lat" in scene and "lon" in scene:
        return TransverseMercator(scene["lat"], scene["lon"]), False
    bbox = get_bbox(props)
    projection = TransverseMercator((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    scene["lat"] = projection.lat
    scene["lon"] = projection.lon
    return projection, True


def signature(props):
    return (get_bbox(props), tuple(sorted(props.track_types)), props.skip_service_tracks,
            props.include_station_tracks, overpass_endpoint(props))


def is_loaded(props):
    return _STATE["rail"] is not None and _STATE["signature"] == signature(props)


def needs_refresh():
    """Nach einem Ausweichen auf die oeffentlichen Server soll "Strecken suchen" neu
    laden - sonst bliebe nach korrigiertem Geo-Box-Zugang der alte Stand stehen."""
    return bool(_STATE.get("overpass_hinweis"))


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
    zoom = maptexture.zoom_for(map_source(props), lat, schritt)
    kachel_m = 256.0 * maptexture.tile_resolution(lat, zoom)
    # schräg verlaufende Korridore berühren deutlich mehr Kacheln als das Rechteck
    schraeg = 2.0 if span_x > 10.0 * span_y else 1.0
    kacheln = max(1, int(math.ceil(span_x / kachel_m + 1.0)
                         * math.ceil(span_y / kachel_m + 1.0) * schraeg))

    grenze = tile_limit(props)
    return {"breite": breite, "hoehe": hoehe, "pixel": pixel, "kacheln": kacheln,
            "grenze": grenze, "min_res": min_res, "zoom": zoom, "schritt": schritt,
            "passt": pixel <= MAX_TEXTURE_PIXELS and (grenze is None or kacheln <= grenze)}


def tile_limit(props):
    """Höchstzahl Kartenkacheln je Abruf - None für die eigene Geo-Box.

    Die Grenze schützt fremde, oft ehrenamtlich betriebene Kartenserver. Die
    Geo-Box ist der eigene Server; große Gebiete werden dort ohnehin Stück für
    Stück abgerufen und lassen sich jederzeit mit Esc abbrechen.
    """
    source = map_source(props)
    if source in maptexture.GEOBOX_SOURCES:
        return None
    if source in maptexture.VOLUNTEER_SOURCES:
        return min(props.map_max_tiles, maptexture.VOLUNTEER_LIMIT)
    return props.map_max_tiles


def map_source(props):
    """Kartenquelle laut Panel „Datenquelle“: mit Geo-Box deren Karten, sonst die
    öffentliche Auswahl."""
    if simbox.active():
        return props.geo_map_source
    return props.map_source or 'BASEMAP_DE'


def overpass_endpoint(props):
    """Overpass-Server laut Panel „Datenquelle“ (None = öffentliche Server der Reihe nach).

    Die Geo-Box wird genutzt, wenn sie gewählt, ein Zugang eingetragen ist und das
    Gebiet in ihrem Datenbereich (DACH) liegt - sonst die öffentlichen Server.
    """
    if simbox.active() and simbox.covers(get_bbox(props)):
        return simbox.overpass_url()
    return None


def overpass_label(props):
    """Kurzer Hinweis, welcher Server die Daten geliefert hat."""
    if _STATE.get("overpass_hinweis"):
        return _STATE["overpass_hinweis"]
    if overpass_endpoint(props):
        return "Geo-Box"
    if simbox.enabled():
        if not simbox.has_credentials():
            return "öffentliche Overpass-Server (kein Geo-Box-Zugang eingetragen)"
        return "öffentliche Overpass-Server (Gebiet außerhalb von DACH)"
    return "öffentliche Overpass-Server"


def overpass_query(props, run, status=None):
    """Fuehrt ``run(endpoint)`` aus. Im Modus Geo-Box wird bei gescheiterter Anmeldung
    auf die oeffentlichen Server ausgewichen - mit Hinweis in der Meldung."""
    endpoint = overpass_endpoint(props)
    _STATE["overpass_hinweis"] = None
    try:
        return run(endpoint)
    except overpass.AuthError as exc:
        if not endpoint:
            raise
        _STATE["overpass_hinweis"] = "öffentliche Overpass-Server (Geo-Box: %s)" % exc
        if status:
            status("Geo-Box-Anmeldung gescheitert - öffentliche Server", None)
        return run(None)


def fetch_data(props, status=None):
    """Reiner Netzwerkteil - laeuft ohne bpy und damit auch in einem Thread."""
    return overpass_query(props, lambda endpoint: overpass.fetch(
        get_bbox(props), sorted(props.track_types) or ['rail'],
        timeout=props.request_timeout,
        use_cache=props.use_cache,
        endpoint=endpoint,
        progress=status,
    ), status)


def process_data(context, props, data):
    """Rohdaten auswerten und im Zwischenspeicher ablegen (Hauptthread)."""
    projection, _ = get_projection(context, props)
    rail = railgeom.RailData.from_overpass(
        data, projection, sorted(props.track_types) or ['rail'],
        # Bahnhofsgleise müssen erhalten bleiben, wenn sie aufgelistet werden sollen
        skip_service=props.skip_service_tracks and not props.include_station_tracks)

    _STATE.update({"rail": rail, "projection": projection, "signature": signature(props)})

    return rail, projection


def load_data(context, props, refresh=False):
    """Daten bereitstellen - aus dem Zwischenspeicher oder (synchron) vom Server."""
    if is_loaded(props) and not refresh:
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


def bericht_uebernehmen(plan, bericht):
    """Kartenbericht eines Stücks in den Lauf übernehmen (Hauptthread).

    Rückgabe die Quellenangabe dieses Stücks - bei Luftbildern genau die
    Datensätze, die seine Kacheln geliefert haben.
    """
    gesamt = plan["luftbild"]
    gesamt["sources"].update(bericht["sources"])
    gesamt["missing"] += bericht["missing"]
    return maptexture.credit(plan["source"], bericht)


def luftbild_hinweis(plan):
    """Ergänzung der Meldung, wenn Kacheln ohne Luftbild geblieben sind."""
    fehlend = plan["luftbild"]["missing"]
    if plan["source"] == maptexture.IMAGERY and fehlend:
        return " | %d Kachel(n) ohne Luftbild (außerhalb der Abdeckung)" % fehlend
    return ""


def kartenquelle_merken(scene, quelle):
    """Quellenangabe in ``scene["kartenquelle"]`` ergänzen, nicht ersetzen - die Pflicht
    zur Namensnennung gilt für jede Karte, die in der Szene liegt."""
    teile = [t for t in str(scene.get("kartenquelle", "")).split(" | ") if t]
    if quelle not in teile:
        teile.append(quelle)
    scene["kartenquelle"] = " | ".join(teile)


def check_tile_budget(props, count):
    """Prueft, ob ein Abruf gegenueber der Kartenquelle vertretbar ist.

    Rueckgabe: Fehlertext oder None. Ehrenamtlich betriebene Dienste (OSM,
    OpenRailwayMap, OpenTopoMap) sind streng begrenzt - massenhafte Abrufe
    verstossen gegen deren Nutzungsbedingungen und fuehren zur Sperrung.
    """
    grenze = tile_limit(props)
    if map_source(props) in maptexture.VOLUNTEER_SOURCES:
        if count > grenze:
            return ("Diese Karte läuft auf ehrenamtlich betriebenen Servern; %d Kacheln "
                    "wären zu viel (Grenze %d). Bitte basemap.de oder TopPlusOpen wählen, "
                    "die Geo-Box als Datenquelle nutzen oder das Gebiet verkleinern"
                    % (count, grenze))
    if grenze is not None and count > grenze:
        return ("Dafür wären %d Kartenkacheln nötig (Grenze %d). Bitte die Auflösung gröber "
                "wählen oder das Gebiet verkleinern" % (count, grenze))
    return None


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
        # die Geo-Box bekommt das ganze Gebiet als eine Abfrage
        tiles = len(overpass._split_for(bbox, overpass_endpoint(props), overpass.MAX_SPAN))
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
        if is_loaded(props) and not needs_refresh():
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
            load_data(context, props, refresh=needs_refresh())
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

        props.scan_info = "%d Strecken, %d Kilometrierungstafeln im Gebiet | Quelle: %s" % (
            len(props.routes), len(rail.milestones), overpass_label(props))
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


class _Hintergrund:
    """Führt eine Erzeugerfunktion in einem Hintergrundthread aus.

    Jedes gelieferte Stück wartet in einer kurzen Schlange auf den Hauptthread,
    der daraus die Blender-Objekte baut. Es liegen höchstens ``vorlauf`` Stücke
    bereit - der Speicher wächst so mit der Stückgröße, nicht mit dem Gebiet.
    """

    def __init__(self, erzeuger, vorlauf=1):
        self._erzeuger = erzeuger
        self.queue = queue.Queue(maxsize=vorlauf)
        self.abbruch = threading.Event()
        self.error = None
        self.status = "Daten werden geladen …"
        self.fraction = -1.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    @property
    def running(self):
        return self._thread.is_alive()

    def fortschritt(self, message, fraction=None):
        self.status = message
        self.fraction = -1.0 if fraction is None else fraction

    def _run(self):
        try:
            for stueck in self._erzeuger(self.fortschritt, self.abbruch):
                while not self.abbruch.is_set():
                    try:
                        self.queue.put(stueck, timeout=0.2)
                        break
                    except queue.Full:
                        continue
                if self.abbruch.is_set():
                    return
        except Exception as exc:                                  # noqa: BLE001
            self.error = exc

    def next(self):
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None


# Laufender stückweiser Befehl (bl_idname) - nur zur Laufzeit, nie in der .blend-Datei
_LAEUFT = {"op": None}


class _Stueckweise:
    """Gemeinsamer Ablauf: im Hintergrund laden, im Hauptthread Stück für Stück aufbauen.

    Unterklassen liefern
      ``_planen(context, props)`` -> (Plan, Fehlertext)            Hauptthread
      ``_erzeugen(plan, fortschritt, abbruch)`` -> Stücke           Hintergrund, ohne bpy
      ``_aufbauen(context, props, plan, stueck)``                   Hauptthread
      ``_abschluss(context, props, plan, abgebrochen)`` -> (Stufe, Text)

    Interaktiv bleibt Blender bedienbar, jedes Stück erscheint sofort in der
    Szene, und Esc bricht ab - die schon fertigen Stücke bleiben erhalten.
    Aus Skripten (``execute``) läuft derselbe Ablauf ohne Thread.
    """

    _timer = None
    _worker = None
    _plan = None

    @classmethod
    def poll(cls, context):
        # Nur ein stückweiser Lauf zur Zeit: ein zweiter würde z. B. die Stücke des
        # ersten wegräumen, während dieser noch weitere anlegt
        return _LAEUFT["op"] is None and cls._bereit(context)

    @classmethod
    def _bereit(cls, context):
        """Voraussetzungen der Unterklasse (statt ``poll``)."""
        return True

    def invoke(self, context, event):
        props = context.scene.railway_chainage
        plan, fehler = self._planen(context, props)
        if fehler:
            self.report({'ERROR'}, fehler)
            return {'CANCELLED'}
        self._plan = plan
        _LAEUFT["op"] = self.bl_idname
        props.scan_info = plan.get("start", "Daten werden geladen …") + " (Esc bricht ab)"
        self._worker = _Hintergrund(
            lambda fortschritt, abbruch: self._erzeugen(plan, fortschritt, abbruch)).start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.railway_chainage
        if event.type == 'ESC':
            self._worker.abbruch.set()
            self._release(context)
            return self._ende(context, props, self._plan, abgebrochen=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        stueck = self._worker.next()
        if stueck is not None:
            try:
                self._aufbauen(context, props, self._plan, stueck)
            except Exception as exc:                              # noqa: BLE001
                self._worker.abbruch.set()
                self._worker.error = exc
            _redraw(context)
        if self._worker.error is not None or (not self._worker.running
                                              and self._worker.queue.empty()):
            self._release(context)
            return self._ende(context, props, self._plan, fehler=self._worker.error)
        _zeige_fortschritt(context, props, self._worker)
        return {'RUNNING_MODAL'}

    def _release(self, context):
        _LAEUFT["op"] = None
        _fortschritt_beenden(context, context.scene.railway_chainage)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def cancel(self, context):
        # Blender beendet den Lauf selbst (Datei laden, Fenster schließen)
        self._worker.abbruch.set()
        self._release(context)

    def execute(self, context):
        props = context.scene.railway_chainage
        plan, fehler = self._planen(context, props)
        if fehler:
            self.report({'ERROR'}, fehler)
            return {'CANCELLED'}
        try:
            for stueck in self._erzeugen(plan, lambda *_args: None, threading.Event()):
                self._aufbauen(context, props, plan, stueck)
        except Exception as exc:                                  # noqa: BLE001
            return self._ende(context, props, plan, fehler=exc)
        return self._ende(context, props, plan)

    def _ende(self, context, props, plan, abgebrochen=False, fehler=None):
        stufe, text = self._abschluss(context, props, plan, abgebrochen)
        if fehler is not None:
            print("[Kilometrierung] %s" % fehler)
            meldung = str(fehler).splitlines()[0] if str(fehler) else type(fehler).__name__
            text = meldung if stufe != 'INFO' else "%s | abgebrochen: %s" % (text, meldung)
            stufe = 'ERROR' if stufe != 'INFO' else 'WARNING'
        elif abgebrochen:
            text = "Abgebrochen - " + text if plan.get("gebaut") else "Abgebrochen"
            stufe = 'INFO'
        props.scan_info = text
        self.report({stufe}, text)
        _redraw(context)
        # Teilergebnisse bleiben stehen und lassen sich als ein Schritt rückgängig machen
        return {'FINISHED'} if plan.get("gebaut") else {'CANCELLED'}


def _height_model(props, bbox):
    """(Höhenmodell, UTM-Zone, Rasterweite, Quellenangabe) laut Panel - oder None,
    wenn für das Gebiet kein Höhendienst hinterlegt ist."""
    if props.terrain_source == 'FLAT':
        zone = dgm.utm_zone((bbox[1] + bbox[3]) / 2.0)
        return (dgm.FlatModel(props.flat_height, zone), zone, float(props.flat_spacing),
                "ebene Fläche (ohne Höhenmodell)")
    spacing = float(props.dem_spacing)
    if simbox.active():
        zone = dgm.utm_zone((bbox[1] + bbox[3]) / 2.0)
        return (dgm.GeoBoxHeights(zone, spacing, use_cache=props.use_cache), zone, spacing,
                dgm.GEOBOX_LABEL)
    key = props.dem_service
    if key == 'AUTO':
        key = dgm.service_for((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        if key is None:
            return None
    entry = dgm.SERVICES[key]
    fallback = dgm.TerrainTiles(zoom=props.dem_fallback_zoom,
                                use_cache=props.use_cache) if props.dem_fill_gaps else None
    model = dgm.HeightModel(entry["url"], entry["coverage"], entry["zone"], spacing,
                            use_cache=props.use_cache, axes=entry["axes"],
                            fallback=fallback, kind=entry.get("kind", "wcs"))
    label = entry["label"] + (" + " + dgm.TERRARIUM_CREDIT if fallback is not None else "")
    return model, entry["zone"], spacing, label


def _needs_probe(context, props):
    """Wird der Höhenbezug aus dem Gelände selbst bestimmt (tiefster Punkt)?"""
    if props.terrain_source == 'FLAT' or not props.dem_zero_level:
        return False
    return not (props.dem_use_blosm_offset and "height_offset" in context.scene)


def _probe(plan, east, north):
    """Tiefster Punkt einer groben Stichprobe über das ganze Gebiet, bevor das erste
    Stück entsteht - sonst bestimmte allein das erste Stück die Null. Nur mit der
    Geo-Box (ein Abruf); andere Dienste nehmen das erste Stück."""
    if plan.get("probe") and isinstance(plan["model"], dgm.GeoBoxHeights):
        plan["bezug"] = plan["model"].probe_min(east, north)


def _height_offset(context, props, heights, bezug=None):
    """Höhenbezug, einmal je Lauf festgelegt - alle Stücke liegen auf derselben Null."""
    if props.terrain_source == 'FLAT':
        return 0.0
    if props.dem_use_blosm_offset and "height_offset" in context.scene:
        return float(context.scene["height_offset"])
    if props.dem_zero_level:
        werte = [float(np.nanmin(heights))] if np.isfinite(heights).any() else []
        if bezug is not None:
            werte.append(float(bezug))
        offset = min(werte) if werte else 0.0
        if "height_offset" not in context.scene:
            context.scene["height_offset"] = offset
        return offset
    return 0.0


# Ab dieser Punktzahl wird gewarnt: Das Laden bleibt stückweise klein, aber die fertige
# Szene belegt etwa 100 Byte je Punkt und Blender wird spürbar träger. Gesperrt wird nichts.
WARN_TERRAIN_POINTS = 40_000_000


def _punkte_hinweis(punkte):
    if punkte <= WARN_TERRAIN_POINTS:
        return ""
    return " - Achtung: rund %.0f Mio Punkte, ca. %.0f GB in Blender" % (
        punkte / 1e6, punkte * 100 / 1e9)


class RC_OT_import_terrain(_Stueckweise, Operator):
    bl_idname = "railway_chainage.import_terrain"
    bl_label = "Gelände im Korridor laden"
    bl_description = ("Amtliches Höhenmodell entlang der Kilometrierungslinie laden und "
                      "nur im eingestellten Korridor als Gelände erzeugen - Abschnitt für "
                      "Abschnitt")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def _bereit(cls, context):
        return bool(_chainage_lines(context))

    def _planen(self, context, props):
        projection, _ = get_projection(context, props)
        bbox = get_bbox(props)
        gewaehlt = _height_model(props, bbox)
        if gewaehlt is None:
            return None, ("Für dieses Gebiet gibt es keinen hinterlegten Höhendienst - "
                          "bitte einen Dienst von Hand wählen")
        model, zone, spacing, label = gewaehlt

        jobs, punkte = [], 0.0
        for line in _chainage_lines(context):
            matrix = line.matrix_world
            points = [(float((matrix @ p.co.to_3d()).x), float((matrix @ p.co.to_3d()).y))
                      for spline in line.data.splines for p in spline.points]
            if len(points) < 2:
                continue
            area = corridor_mod.Corridor(corridor_mod.to_utm(points, projection, zone),
                                         props.corridor_radius)
            count = corridor_mod.corridor_segment_count(area.length,
                                                        props.terrain_segment_length)
            jobs.append({"line": line.name, "area": area, "count": count})
            punkte += area.length * 2.0 * props.corridor_radius / (spacing * spacing)
        if not jobs:
            return None, "Keine Kilometrierungslinie mit Punkten gefunden"
        total = sum(job["count"] for job in jobs)
        return {"model": model, "jobs": jobs, "projection": projection, "zone": zone,
                "label": label, "spacing": spacing, "total": total, "offset": None,
                "probe": _needs_probe(context, props), "bezug": None,
                "parts": {}, "verts": 0, "faces": 0, "missing": 0, "gebaut": 0,
                "start": "Gelände: %d Abschnitt(e) werden geladen%s …"
                         % (total, _punkte_hinweis(punkte))}, None

    @staticmethod
    def _erzeugen(plan, fortschritt, abbruch):
        model, zone, projection = plan["model"], plan["zone"], plan["projection"]
        if plan["probe"]:
            fortschritt("Gelände: Höhenbezug wird bestimmt …")
            erste = plan["jobs"][0]["area"]
            grob = max(plan["spacing"], math.sqrt(erste.length * 2.0 * erste.radius / 5000.0))
            punkte = list(corridor_mod.grid_points(erste, grob).values())
            if punkte:
                _probe(plan, [p[0] for p in punkte], [p[1] for p in punkte])
        nummer = 0
        for job in plan["jobs"]:
            for index in range(job["count"]):
                if abbruch.is_set():
                    return
                nummer += 1
                fortschritt("Gelände: Abschnitt %d von %d" % (nummer, plan["total"]),
                            (nummer - 1) / float(plan["total"]))
                teil = corridor_mod.corridor_segment(job["area"], plan["spacing"],
                                                     index, job["count"])
                if teil is None:
                    continue
                east, north, coords, quads = teil
                model.load(corridor_mod.tile_keys(east, north, model))
                heights = model.sample(east, north)
                lat, lon = dgm.utm_to_wgs84_array(east, north, zone)
                x, y = projection.from_geographic_array(lat, lon)
                yield {"line": job["line"], "index": index, "count": job["count"],
                       "span": job["area"].length / job["count"],
                       "length": job["area"].length, "x": x, "y": y, "z": heights,
                       "coords": coords, "quads": quads}
            yield {"line": job["line"], "fertig": True}

    def _aufbauen(self, context, props, plan, stueck):
        line = context.scene.objects.get(stueck["line"])
        parts = plan["parts"].setdefault(stueck["line"], [])
        if stueck.get("fertig"):
            if line is not None and parts:
                if props.drape_mode == 'DIRECT':
                    self._drape(line, plan["model"], plan["projection"], plan["zone"],
                                plan["offset"] or 0.0, props.z_offset)
                elif props.drape_mode == 'SHRINKWRAP':
                    # über Namen: ein Rückgängig während des Laufs entfernt Objekte
                    objekte = [o for o in map(bpy.data.objects.get, parts) if o is not None]
                    if objekte:
                        _apply_shrinkwrap(line, objekte, props.z_offset)
            return

        z = stueck["z"]
        keep = ~np.isnan(z)
        plan["missing"] += int((~keep).sum())
        used, quads = corridor_mod.compact(stueck["quads"], keep)
        if not len(quads):
            return
        if plan["offset"] is None:
            plan["offset"] = _height_offset(context, props, z[used], plan["bezug"])
        verts = np.column_stack((stueck["x"][used], stueck["y"][used],
                                 z[used] - plan["offset"]))

        top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
        name = "Gelände %s" % stueck["line"].replace("KM-Linie ", "")
        if stueck["count"] > 1:
            name = "%s %02d" % (name, stueck["index"] + 1)
        obj = builder.mesh_from_arrays(name, verts, quads, top)
        start = stueck["index"] * stueck["span"]
        builder.apply_corridor_uv(obj, stueck["coords"][used], props.corridor_radius,
                                  stueck["span"], station_offset=start)
        obj["height_offset"] = plan["offset"]
        obj["corridor_station_start"] = round(start, 2)
        obj["corridor_station_end"] = round(min(stueck["length"], start + stueck["span"]), 2)
        obj["chainage_line"] = stueck["line"]
        obj["dem_service"] = plan["label"]
        obj["dem_spacing_m"] = plan["spacing"]
        obj["corridor_radius_m"] = props.corridor_radius
        obj["corridor_length_m"] = round(stueck["length"], 1)
        obj["utm_zone"] = plan["zone"]
        parts.append(obj.name)
        plan["verts"] += len(verts)
        plan["faces"] += len(quads)
        plan["gebaut"] += 1

    def _abschluss(self, context, props, plan, abgebrochen):
        if not plan["gebaut"]:
            return 'WARNING', ("Keine Höhendaten im Korridor erhalten - liegt die Strecke im "
                               "Gebiet des gewählten Dienstes?")
        model = plan["model"]
        info = ("Gelände erzeugt: %d Punkte, %d Flächen in %d Abschnitt(en), Raster %g m, "
                "Korridor %g m" % (plan["verts"], plan["faces"], plan["gebaut"],
                                   plan["spacing"], props.corridor_radius))
        if model.from_fallback:
            info += ", davon %d Punkte aus den weltweiten Ersatzdaten" % model.from_fallback
        missing = plan["missing"] + model.failed
        if missing:
            info += " (%d Punkte ohne Höhenwert)" % missing
        return 'INFO', info

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
            sampler = builder.terrain_lookup(terrains)

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


class RC_OT_map_texture(_Stueckweise, Operator):
    bl_idname = "railway_chainage.map_texture"
    bl_label = "Kartentextur laden"
    bl_description = ("Kartenbild entlang der Strecke auf das Korridorgelände legen - die "
                      "Textur folgt der Achse und bleibt dadurch klein")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def _bereit(cls, context):
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
            zoom = maptexture.zoom_for(map_source(props), middle, step)
            keys = builder.corridor_tile_keys(lat, lon, zoom)
            jobs.append({"object": obj.name, "grid": grid, "zoom": zoom, "keys": keys})
        if not jobs and zu_gross:
            return {"too_large": zu_gross}
        return jobs

    def _planen(self, context, props):
        jobs = self._prepare(context, props)
        if isinstance(jobs, dict) and jobs.get("too_large"):
            return None, _zu_gross(jobs["too_large"])
        if not jobs:
            return None, "Kein passendes Korridorgelände gefunden – Auswahl und Gelände prüfen"
        total = len({(job["zoom"],) + key for job in jobs for key in job["keys"]})
        problem = check_tile_budget(props, total)
        if problem:
            return None, problem
        return {"jobs": jobs, "source": map_source(props), "built": [], "gebaut": 0,
                "luftbild": maptexture.new_report(),
                "start": "Kartentextur: %d Kacheln für %d Abschnitt(e) werden geladen …"
                         % (total, len(jobs))}, None

    @staticmethod
    def _erzeugen(plan, fortschritt, abbruch):
        for number, job in enumerate(plan["jobs"], start=1):
            if abbruch.is_set():
                return
            fortschritt("Kartentextur: Abschnitt %d von %d" % (number, len(plan["jobs"])),
                        (number - 1) / float(len(plan["jobs"])))
            bericht = maptexture.new_report()
            yield job, maptexture.fetch_tiles(plan["source"], job["keys"], job["zoom"],
                                              report=bericht, abbruch=abbruch), bericht

    def _aufbauen(self, context, props, plan, stueck):
        job, tiles, bericht = stueck
        quelle = bericht_uebernehmen(plan, bericht)
        obj = bpy.data.objects.get(job["object"])
        if obj is None:
            return
        lat, lon, width, height, length, step = job["grid"]
        name = "Karte %s %s" % (obj.name[:35], uuid.uuid4().hex[:12])
        image = builder.corridor_texture(lat, lon, width, height, step, plan["source"],
                                         tiles, job["zoom"], name=name, credit=quelle)
        builder.apply_map_material(obj, image, name=name)
        plan["built"].append("%d x %d Pixel, %.2f m/Pixel, Zoom %d" %
                             (width, height, step, job["zoom"]))
        plan["gebaut"] += 1

    def _abschluss(self, context, props, plan, abgebrochen):
        built = plan["built"]
        if not built:
            return 'WARNING', "Keine Kartentextur erzeugt"
        quelle = maptexture.credit(plan["source"], plan["luftbild"])
        kartenquelle_merken(context.scene, quelle)
        teile = ["%d Abschnitte je %s" % (len(built), built[0])] if len(built) > 1 else built
        return 'INFO', " | ".join(teile) + " | Quelle: " + quelle + luftbild_hinweis(plan)


# Größte Kantenlänge einer Kartentextur je Geländestück in Pixeln
MAX_CHUNK_TEXTURE = 4096


def tile_range(bbox, zoom, tile_size=256):
    """Anzahl der Kartenkacheln, die ein geografisches Rechteck berührt - ohne das
    Pixelgitter aufzubauen."""
    (x0, y0), (x1, y1) = (maptexture.global_pixels(bbox[2], bbox[1], zoom, tile_size),
                          maptexture.global_pixels(bbox[0], bbox[3], zoom, tile_size))
    return (int(x1 // tile_size) - int(x0 // tile_size) + 1) * \
        (int(y1 // tile_size) - int(y0 // tile_size) + 1)


class RC_OT_import_area(_Stueckweise, Operator):
    bl_idname = "railway_chainage.import_area"
    bl_label = "Gebiet als Fläche laden"
    bl_description = ("Geländemodell für das eingestellte Gebiet laden und mit der Karte "
                      "belegen - ohne Bezug zu einer Strecke. Große Gebiete entstehen "
                      "Stück für Stück")
    bl_options = {'REGISTER', 'UNDO'}

    def _planen(self, context, props):
        bbox = get_bbox(props)
        gewaehlt = _height_model(props, bbox)
        if gewaehlt is None:
            return None, "Für dieses Gebiet gibt es keinen hinterlegten Höhendienst"
        model, zone, spacing, label = gewaehlt

        # Das Gelände wird entlang der Szenenachsen aufgebaut, nicht entlang der
        # UTM-Achsen - sonst steht der Ausschnitt um die Meridiankonvergenz
        # verdreht in der Szene.
        projection, _ = get_projection(context, props)
        lokal = corridor_mod.area_bounds_local(bbox, projection, spacing)
        groesse = props.chunk_size
        aufloesung = map_resolution(props)
        if props.area_with_map:
            # jede Textur bleibt unter MAX_CHUNK_TEXTURE Pixel Kantenlänge
            groesse = min(groesse, MAX_CHUNK_TEXTURE * aufloesung)
        columns, rows, chunks = corridor_mod.area_chunks(lokal, spacing, groesse)

        plan = {"model": model, "zone": zone, "spacing": spacing, "label": label,
                "lokal": lokal, "projection": projection, "chunks": chunks,
                "source": map_source(props), "map": props.area_with_map,
                "resolution": aufloesung, "zoom": 0, "offset": None, "verts": 0,
                "faces": 0, "missing": 0, "gebaut": 0, "pixel": None,
                "flat": props.terrain_source == 'FLAT',
                "probe": _needs_probe(context, props), "bezug": None,
                "luftbild": maptexture.new_report()}
        kacheln = 0
        if props.area_with_map:
            mitte = (bbox[0] + bbox[2]) / 2.0
            plan["zoom"] = maptexture.zoom_for(plan["source"], mitte, aufloesung)
            kacheln = tile_range(bbox, plan["zoom"])
            problem = check_tile_budget(props, kacheln)
            if problem:
                return None, problem
        plan["start"] = ("Gebiet: %d x %d Punkte in %d Stück(en)%s%s …"
                         % (columns, rows, len(chunks),
                            ", ca. %d Kartenkacheln" % kacheln if kacheln else "",
                            _punkte_hinweis(columns * rows)))
        return plan, None

    @staticmethod
    def _erzeugen(plan, fortschritt, abbruch):
        model, chunks, spacing = plan["model"], plan["chunks"], plan["spacing"]
        x0, y0, x1, y1 = plan["lokal"]
        if plan["probe"]:
            fortschritt("Gebiet: Höhenbezug wird bestimmt …")
            gx, gy = np.meshgrid(np.linspace(x0, x1, 100), np.linspace(y0, y1, 100))
            lat, lon = plan["projection"].to_geographic_array(gx, gy)
            _probe(plan, *dgm.wgs84_to_utm_array(lat, lon, plan["zone"]))
        for number, chunk in enumerate(chunks, start=1):
            if abbruch.is_set():
                return
            fortschritt("Gebiet: Stück %d von %d - Höhen" % (number, len(chunks)),
                        (number - 1) / float(len(chunks)))
            gx, gy, east, north = corridor_mod.area_chunk(plan["lokal"], spacing, chunk,
                                                          plan["projection"], plan["zone"])
            if not plan["flat"]:
                keys = corridor_mod.tile_keys(east, north, model)
                model.evict(set(keys))        # Kacheln früherer Stücke freigeben
                model.load(keys)
            heights = model.sample(east, north)
            del east, north
            c0, c1, r0, r1 = chunk
            bounds = (x0 + c0 * spacing, y0 + r0 * spacing, x0 + c1 * spacing, y0 + r1 * spacing)
            stueck = {"number": number, "x": gx, "y": gy, "z": heights, "bounds": bounds}
            if plan["map"]:
                fortschritt("Gebiet: Stück %d von %d - Karte" % (number, len(chunks)),
                            (number - 0.5) / float(len(chunks)))
                lat, lon, width, height, step = builder.local_latlon_grid(
                    bounds, plan["projection"], plan["resolution"], MAX_CHUNK_TEXTURE)
                keys = builder.corridor_tile_keys(lat, lon, plan["zoom"])
                bericht = maptexture.new_report()
                stueck.update(grid=(lat, lon, width, height, step), bericht=bericht,
                              tiles=maptexture.fetch_tiles(plan["source"], keys, plan["zoom"],
                                                           report=bericht, abbruch=abbruch))
            yield stueck

    def _aufbauen(self, context, props, plan, stueck):
        top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
        sammlung = builder.ensure_collection("Gelände Gebiet", top)
        if not plan["gebaut"] and not plan.get("geleert"):
            builder.clear_generated(sammlung)      # ein neuer Lauf ersetzt den vorigen
            plan["geleert"] = True

        z = stueck["z"]
        valid = ~np.isnan(z)
        plan["missing"] += int((~valid).sum())
        _index, quads = corridor_mod.grid_quads(valid)
        if not len(quads):
            return
        if plan["offset"] is None:
            plan["offset"] = _height_offset(context, props, z[valid], plan["bezug"])
        bx0, by0, bx1, by1 = stueck["bounds"]
        x, y = stueck["x"][valid], stueck["y"][valid]
        verts = np.column_stack((x, y, z[valid] - plan["offset"]))
        uvs = np.column_stack(((x - bx0) / max(1e-6, bx1 - bx0),
                               (y - by0) / max(1e-6, by1 - by0)))
        name = "Gelände Gebiet %03d" % stueck["number"]
        obj = builder.mesh_from_arrays(name, verts, quads, sammlung, uvs, uv_name="Gebiet")
        obj["height_offset"] = plan["offset"]
        obj["dem_service"] = plan["label"]
        obj["dem_spacing_m"] = plan["spacing"]
        obj["utm_zone"] = plan["zone"]
        plan["verts"] += len(verts)
        plan["faces"] += len(quads)
        plan["gebaut"] += 1

        if plan["map"] and stueck.get("grid"):
            lat, lon, width, height, step = stueck["grid"]
            bildname = "Gebietskarte %03d" % stueck["number"]
            quelle = bericht_uebernehmen(plan, stueck["bericht"])
            image = builder.corridor_texture(lat, lon, width, height, step, plan["source"],
                                             stueck["tiles"], plan["zoom"], name=bildname,
                                             credit=quelle)
            builder.apply_map_material(obj, image, name=bildname)
            if plan["pixel"] is None or width * height > plan["pixel"][0] * plan["pixel"][1]:
                plan["pixel"] = (width, height, step)

    def _abschluss(self, context, props, plan, abgebrochen):
        if not plan["gebaut"]:
            return 'WARNING', "Keine Höhendaten für dieses Gebiet erhalten"
        info = "Gebiet geladen: %d Punkte, %d Flächen in %d Stück(en), Raster %g m" % (
            plan["verts"], plan["faces"], plan["gebaut"], plan["spacing"])
        if plan["model"].from_fallback:
            info += ", davon %d aus Ersatzdaten" % plan["model"].from_fallback
        if plan["missing"]:
            info += " (%d ohne Höhenwert)" % plan["missing"]
        if plan["pixel"]:
            quelle = maptexture.credit(plan["source"], plan["luftbild"])
            kartenquelle_merken(context.scene, quelle)
            info += " | Karte je Stück bis %d x %d Pixel, %.2f m/Pixel, Zoom %d | Quelle: %s%s" % (
                plan["pixel"][0], plan["pixel"][1], plan["pixel"][2], plan["zoom"], quelle,
                luftbild_hinweis(plan))
        return 'INFO', info


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
            # 5) vier Zahlen: west,süd,ost,nord (z. B. aus der Auswahlseite oder QGIS)
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
    bl_label = "In Overpass Turbo ansehen"
    bl_description = ("Das eingestellte Gebiet in Overpass Turbo der gewählten Datenquelle "
                      "öffnen. Geo-Box: über einen lokalen Zugang in Blender, der das "
                      "Service-Konto aus „Datenquelle“ nutzt - keine Anmeldung im Browser")

    def execute(self, context):
        props = context.scene.railway_chainage
        bbox = get_bbox(props)
        lat = (bbox[0] + bbox[2]) / 2.0
        lon = (bbox[1] + bbox[3]) / 2.0
        zoom = 13
        span = max(abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1]) * 0.6) or 0.01
        while zoom > 3 and span > 360.0 / (2 ** zoom) * 1.5:
            zoom -= 1

        if simbox.active():
            # Turbo der Geo-Box verlangt einen Token, den der Browser nicht hat - der
            # lokale Zugang reicht Seite, Abfragen und Kacheln angemeldet weiter
            try:
                url = geoproxy.start() + "turbo/"
            except OSError as exc:
                self.report({'ERROR'}, "Lokaler Geo-Box-Zugang nicht startbar: %s" % exc)
                return {'CANCELLED'}
            quelle = "Overpass Turbo der Geo-Box (lokaler Zugang %s)" % url
        else:
            url = simbox.turbo_url()
            quelle = "Overpass Turbo (öffentlich)"
        # Overpass Turbo versteht die Kartenposition als C=lat;lon;zoom
        url = "%s?C=%.6f;%.6f;%d" % (url, lat, lon, zoom)
        bpy.ops.wm.url_open(url=url)
        self.report({'INFO'}, quelle + " geöffnet")
        return {'FINISHED'}


def _selector_tiles(props):
    """Kacheladresse und Zoomgrenze für die Auswahlkarte ohne lokalen Server.

    Ehrenamtlich betriebene Kartenserver bleiben außen vor - beim Herumfahren
    auf der Karte kämen dort schnell hunderte Abrufe zusammen. Karten der Geo-Box
    brauchen eine Anmeldung, die der Browser nicht hat.
    """
    key = map_source(props)
    quelle = maptexture.SOURCES.get(key)
    if (quelle is None or key in maptexture.VOLUNTEER_SOURCES
            or key in maptexture.GEOBOX_SOURCES):
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
        if simbox.active():
            # Basiskarte und Ortssuche der Geo-Box über den lokalen Server, der den
            # Token ergänzt - der Browser selbst ist nicht angemeldet
            kacheln, max_zoom, suche = selector.GEOBOX_TILES, 19, selector.GEOBOX_SEARCH
            quellenangabe = maptexture.SOURCES[maptexture.BASEMAP][4]
        else:
            kacheln, max_zoom = _selector_tiles(props)
            suche = ""
            if kacheln == selector.DEFAULT_TILES:
                quellenangabe = maptexture.SOURCES['BASEMAP_DE'][4]
            else:
                quellenangabe = maptexture.credit(map_source(props))
        return selector.build_html(
            bbox[0], bbox[1], bbox[2], bbox[3],
            tile_url=kacheln, max_zoom=max_zoom, search_url=suche,
            attribution=quellenangabe, with_server=True)

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


def _terrain_samplers(context):
    """Höhenabfrage über alle vom Addon erzeugten Geländeobjekte."""
    flaechen = [o for o in context.scene.objects
                if o.type == 'MESH' and o.get("dem_service")]
    return builder.terrain_lookup(flaechen), flaechen


def _overpass_chunk(endpoint, run, plan):
    """Ein Overpass-Abruf im Hintergrund; bei gescheiterter Geo-Box-Anmeldung einmalig
    auf die öffentlichen Server ausweichen (wie :func:`overpass_query`)."""
    if plan.get("ausweichen"):
        endpoint = None
    try:
        return run(endpoint)
    except overpass.AuthError as exc:
        if not endpoint:
            raise
        plan["ausweichen"] = True
        plan["hinweis"] = "öffentliche Overpass-Server (Geo-Box: %s)" % exc
        return run(None)


class RC_OT_import_buildings(_Stueckweise, Operator):
    bl_idname = "railway_chainage.import_buildings"
    bl_label = "Gebäude erzeugen"
    bl_description = ("Gebäudegrundrisse aus OpenStreetMap für das eingestellte Gebiet laden "
                      "und als Baukörper erzeugen - Teilgebiet für Teilgebiet")
    bl_options = {'REGISTER', 'UNDO'}

    def _planen(self, context, props):
        projection, _ = get_projection(context, props)
        bbox = get_bbox(props)
        korridor = None
        if props.buildings_only_corridor:
            korridor = _corridor_from_lines(context, props.corridor_radius)
            if korridor is None:
                return None, "Für „Nur im Korridor“ wird eine Kilometrierungslinie gebraucht"
        teile = overpass.split_bbox(bbox, overpass.BUILDING_SPAN)
        lookup, gelaende = (_terrain_samplers(context) if props.buildings_drape
                            else (None, []))
        return {"projection": projection, "bbox": bbox, "korridor": korridor, "teile": teile,
                "endpoint": overpass_endpoint(props), "timeout": props.request_timeout,
                "cache": props.use_cache, "parts": props.buildings_parts,
                "default_height": props.buildings_default_height,
                "level_height": props.buildings_level_height,
                "min_area": props.buildings_min_area, "lookup": lookup,
                "gelaende": len(gelaende), "skirt": props.buildings_skirt,
                "anzahl": 0, "flaeche": 0.0, "hoehe": 0.0, "mit_angabe": 0,
                "ohne_gelaende": 0, "gebaut": 0,
                "start": "Gebäude: %d Teilgebiet(e) werden geladen …" % len(teile)}, None

    @staticmethod
    def _erzeugen(plan, fortschritt, abbruch):
        # Erst zählen - nur zur Anzeige, wie viel kommt
        fortschritt("Gebäude werden gezählt …")
        try:
            anzahl = _overpass_chunk(plan["endpoint"], lambda ep: overpass.count_buildings(
                plan["bbox"], timeout=plan["timeout"], endpoint=ep), plan)
        except overpass.OverpassError:
            anzahl = None
        vorab = "ca. %d Gebäude, " % anzahl if anzahl is not None else ""

        projection, teile = plan["projection"], plan["teile"]
        gesamt = buildings.bbox_test(projection, plan["bbox"])
        korridor = plan["korridor"]
        for number, teil in enumerate(teile, start=1):
            if abbruch.is_set():
                return
            fortschritt("Gebäude: %sTeilgebiet %d von %d" % (vorab, number, len(teile)),
                        (number - 1) / float(len(teile)))
            data = _overpass_chunk(plan["endpoint"], lambda ep: overpass.fetch_buildings(
                teil, timeout=plan["timeout"], use_cache=plan["cache"], endpoint=ep,
                with_parts=plan["parts"]), plan)

            def innerhalb(x, y, teil=teil):
                # Schwerpunkt im Teilgebiet (halboffen) - Gebäude auf der Grenze kommen
                # in beiden Antworten vor, werden aber nur einmal gebaut
                lat, lon = projection.to_geographic(x, y)
                if not (teil[0] <= lat < teil[2] or (lat == teil[2] == plan["bbox"][2])):
                    return False
                if not (teil[1] <= lon < teil[3] or (lon == teil[3] == plan["bbox"][3])):
                    return False
                return gesamt(x, y) and (korridor is None or korridor.contains(x, y))

            haeuser = buildings.parse(data, projection,
                                      default_height=plan["default_height"],
                                      level_height=plan["level_height"],
                                      min_area=plan["min_area"], bbox_filter=innerhalb)
            del data
            if haeuser:
                yield number, haeuser

    def _aufbauen(self, context, props, plan, stueck):
        number, haeuser = stueck
        top = builder.ensure_collection(builder.TOP_COLLECTION, context.scene.collection)
        sammlung = builder.ensure_collection("Gebäude", top)
        if not plan["gebaut"]:
            builder.clear_generated(sammlung)      # ein neuer Lauf ersetzt den vorigen
        samplers = [plan["lookup"]] if plan["lookup"] is not None else []
        obj, ohne = builder.create_buildings(
            context, "Gebäude %03d" % number, haeuser, sammlung, samplers=samplers,
            skirt=plan["skirt"])
        if obj is None:
            return
        kennzahlen = buildings.statistics(haeuser)
        obj["osm_buildings"] = kennzahlen["anzahl"]
        plan["anzahl"] += kennzahlen["anzahl"]
        plan["flaeche"] += kennzahlen["flaeche"]
        plan["hoehe"] += kennzahlen["hoehe"] * kennzahlen["anzahl"]
        plan["mit_angabe"] += kennzahlen["mit_angabe"]
        plan["ohne_gelaende"] += ohne
        plan["gebaut"] += 1

    def _abschluss(self, context, props, plan, abgebrochen):
        if not plan["anzahl"]:
            return 'WARNING', "Im Gebiet sind keine Gebäude eingetragen"
        info = ("%d Gebäude in %d Teil(en) erzeugt, %.1f ha Grundfläche, mittlere Höhe %.1f m; "
                "%d davon mit Höhenangabe aus OSM"
                % (plan["anzahl"], plan["gebaut"], plan["flaeche"] / 10000.0,
                   plan["hoehe"] / plan["anzahl"], plan["mit_angabe"]))
        if plan["gelaende"]:
            info += " | auf %d Geländeabschnitt(e) gesetzt" % plan["gelaende"]
            if plan["ohne_gelaende"]:
                info += ", %d ohne Geländetreffer" % plan["ohne_gelaende"]
        if plan.get("hinweis"):
            info += " | Quelle: " + plan["hinweis"]
        return 'INFO', info


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


class RC_OT_simbox_test(Operator):
    bl_idname = "railway_chainage.simbox_test"
    bl_label = "Verbindung testen"
    bl_description = ("Mit dem eingetragenen Service-Konto an der Geo-Box anmelden und jeden "
                      "Dienst einmal abfragen: Overpass, Luftbilder, Höhen, Basiskarte, Ortssuche")

    # Kleine Probeanfragen; der Ort spielt keine Rolle, er muss nur im Datenbereich liegen
    PROBE = (49.7913, 9.9534)

    def _dienste(self):
        """(Name, Probeanfrage) je Dienst der Geo-Box."""
        lat, lon = self.PROBE
        overpass_abfrage = urllib.parse.urlencode({"data": "[out:json][timeout:15];node(%.4f,%.4f,"
                                                           "%.4f,%.4f);out ids 1;"
                                                           % (lat, lon, lat + 0.001, lon + 0.001)})
        return (
            ("Overpass", lambda: simbox.request(
                simbox.overpass_url(), "application/json", data=overpass_abfrage.encode("ascii"),
                content_type="application/x-www-form-urlencoded", what="Overpass")),
            ("Luftbilder", lambda: simbox.catalogue(refresh=True)),
            ("Höhen", lambda: simbox.sample_elevation([self.PROBE])),
            ("Basiskarte", lambda: simbox.request(
                simbox.basemap_template().format(z=12, x=2161, y=1392), "image/png",
                what="Basiskarte")),
            ("Ortssuche", lambda: simbox.request(
                simbox.search_url() + "?format=json&limit=1&q=Berlin", "application/json",
                what="Ortssuche")),
        )

    _timer = None
    _thread = None
    _ergebnis = None

    @staticmethod
    def _zugang_neu_lesen(context):
        from . import props as props_mod
        prefs = props_mod.preferences(context)
        if prefs is not None:
            props_mod._zugang_setzen(prefs)       # geänderte .env-Datei neu einlesen

    def _pruefen(self):
        """Anmeldung und alle Dienste prüfen - ohne bpy, läuft im Hintergrund.

        Rückgabe (Text, ok); ok ist None, wenn schon die Anmeldung scheitert.
        """
        try:
            simbox.token(force=True)
        except Exception as exc:                                  # noqa: BLE001
            return str(exc), None

        erreichbar, fehler = [], []
        for name, probe in self._dienste():
            try:
                probe()
                erreichbar.append(name)
            except urllib.error.HTTPError as exc:
                fehler.append("%s (HTTP %s)" % (name, exc.code))
            except Exception as exc:                              # noqa: BLE001
                code = re.search(r"HTTP \d+", str(exc))
                fehler.append("%s (%s)" % (name, code.group(0) if code else exc))

        text = "Angemeldet als „%s“" % simbox.client_id()
        if erreichbar:
            text += " - erreichbar: " + ", ".join(erreichbar)
        if fehler:
            text += " - Fehler: " + "; ".join(fehler)
        return text, not fehler

    def _melden(self, context, text, ok):
        simbox.set_status(text, ok=bool(ok))
        self.report({'ERROR'} if ok is None else {'INFO'} if ok else {'WARNING'}, text)
        _redraw(context)
        return {'CANCELLED'} if ok is None else {'FINISHED'}

    def execute(self, context):
        self._zugang_neu_lesen(context)
        return self._melden(context, *self._pruefen())

    def invoke(self, context, event):
        # Die Probeabrufe können bei einem hängenden Server Minuten dauern - im
        # Hintergrund, damit Blender bedienbar bleibt
        self._zugang_neu_lesen(context)
        simbox.set_status("Verbindung wird geprüft …", ok=None)

        def lauf():
            self._ergebnis = self._pruefen()

        self._thread = threading.Thread(target=lauf, daemon=True)
        self._thread.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER' or self._thread.is_alive():
            return {'PASS_THROUGH'}
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        text, ok = self._ergebnis or ("Verbindungstest ohne Ergebnis", None)
        return self._melden(context, text, ok)


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
           RC_OT_select_routes, RC_OT_paste_bbox,
           RC_OT_open_map, RC_OT_select_area,
           RC_OT_clear_cache, RC_OT_organize_tracks, RC_OT_simbox_test)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

"""Bedienoberfläche im 3D-Viewport (N-Panel, Reiter „Umgebung“).

Selten gebrauchte Befehle stehen bewusst nicht im Panel, bleiben aber über die
Suche (F3) erreichbar: „Zwischenspeicher leeren“ und „Szene auf Korridor
zuschneiden“ samt der zugehörigen Einstellungen.
"""

import math

import bpy
from bpy.types import Panel, UIList

from . import uitext


class RC_UL_routes(UIList):

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        sub = row.row()
        sub.label(text=item.name,
                  icon='HOME' if item.is_station else 'NONE')
        info = row.row(align=True)
        info.alignment = 'RIGHT'
        if item.track_count > 1:
            info.label(text="%d Gl." % item.track_count)
        info.label(text="%.1f km" % item.length_km)
        info.label(text=str(item.milestone_count), icon='SNAP_MIDPOINT')


def _status_anzeigen(layout, props):
    """Meldungen und - während eines Ladevorgangs - einen Fortschrittsbalken.

    Steht nur im Panel „Status“ ganz oben, damit Fortschritt und Ergebnis jedes
    Befehls an derselben Stelle erscheinen, egal welches Panel aufgeklappt ist.
    """
    if props.busy:
        box = layout.box()
        uitext.label(box, props.scan_info or "Daten werden geladen …", icon='SORTTIME')
        row = box.row()
        row.enabled = False
        row.prop(props, "progress", text="")
    elif props.scan_info:
        box = layout.box()
        for chunk in props.scan_info.split(" | "):
            uitext.label(box, chunk)


def _karten_abschaetzung(layout, context, props):
    """Zeigt vorab, wie groß die Kartentextur würde und wo die Grenze liegt."""
    import importlib
    ops = importlib.import_module(__package__ + ".operators")

    gelaende = [o for o in context.scene.objects
                if o.type == 'MESH' and o.get("corridor_radius_m")]
    if gelaende:
        laengen = [float(o.get("corridor_station_end", 0.0)) - float(o.get("corridor_station_start", 0.0))
                   for o in gelaende]
        span_x = max(laengen) if laengen else 0.0
        span_y = 2.0 * float(gelaende[0].get("corridor_radius_m", props.corridor_radius))
        titel = "Abschnitt:"
    else:
        return              # „Gebiet als Fläche laden“ zeigt seine eigene Abschätzung
    if span_x <= 0 or span_y <= 0:
        return

    bbox = ops.get_bbox(props)
    schaetzung = ops.estimate_map(props, span_x, span_y, (bbox[0] + bbox[2]) / 2.0)
    box = layout.box()
    box.scale_y = 0.75
    uitext.label(box, "%s %d x %d px (%.1f Mio)" %
                 (titel, schaetzung["breite"], schaetzung["hoehe"], schaetzung["pixel"] / 1e6),
                 icon='INFO' if schaetzung["passt"] else 'ERROR', context=context)
    uitext.label(box, "%.2f m/px · Stufe %d · ca. %d Kacheln"
                 % (schaetzung["schritt"], schaetzung["zoom"], schaetzung["kacheln"]),
                 context=context)
    if not schaetzung["passt"]:
        uitext.label(box, "zu groß (Grenze %.0f Mio px%s)"
                     % (ops.MAX_TEXTURE_PIXELS / 1e6, ", %d Kacheln" % schaetzung["grenze"]
                        if schaetzung["grenze"] is not None else ""), context=context)
    elif schaetzung["min_res"] > 0.02:
        uitext.label(box, "Grenze hier: %.2f m/px" % schaetzung["min_res"], context=context)


def _gebiet_abschaetzung(layout, context, props):
    """Umfang von „Gebiet als Fläche laden“ vorab: Punkte, Stücke, Kartenkacheln."""
    import importlib
    ops = importlib.import_module(__package__ + ".operators")
    bbox = ops.get_bbox(props)
    mitte = math.radians((bbox[0] + bbox[2]) / 2.0)
    span_x = (bbox[3] - bbox[1]) * 111320.0 * math.cos(mitte)
    span_y = (bbox[2] - bbox[0]) * 111320.0
    if span_x <= 0 or span_y <= 0:
        return
    raster = float(props.flat_spacing if props.terrain_source == 'FLAT' else props.dem_spacing)
    punkte = (span_x / raster + 1.0) * (span_y / raster + 1.0)
    kante = min(props.chunk_size, ops.corridor_mod.MAX_CHUNK_CELLS * raster)
    if props.area_with_map:
        kante = min(kante, ops.MAX_CHUNK_TEXTURE * ops.map_resolution(props))
    stuecke = math.ceil(span_x / kante) * math.ceil(span_y / kante)
    box = layout.box()
    box.scale_y = 0.75
    zu_viel = False
    gross = punkte > ops.WARN_TERRAIN_POINTS
    uitext.label(box, "Gebiet %.1f x %.1f km: %.1f Mio Punkte in %d Stück(en)"
                 % (span_x / 1000.0, span_y / 1000.0, punkte / 1e6, stuecke),
                 icon='ERROR' if gross else 'INFO', context=context)
    if gross:
        uitext.label(box, "sehr groß: ca. %.0f GB in Blender" % (punkte * 100 / 1e9),
                     context=context)
    if props.area_with_map:
        zoom = ops.maptexture.zoom_for(ops.map_source(props), (bbox[0] + bbox[2]) / 2.0,
                                       ops.map_resolution(props))
        kacheln = ops.tile_range(bbox, zoom)
        grenze = ops.tile_limit(props)
        zu_viele = grenze is not None and kacheln > grenze
        uitext.label(box, "Karte %.2f m/px · Stufe %d · ca. %d Kacheln"
                     % (ops.map_resolution(props), zoom, kacheln),
                     icon='ERROR' if zu_viele else 'NONE', context=context)
        if zu_viele:
            uitext.label(box, "zu viele Kacheln (Grenze %d)" % grenze, context=context)
            zu_viel = True
    return not zu_viel


# Symbole in den Panel-Überschriften: Blender zeichnet sie sonst in voller Größe.
# Verkleinern geht nur über scale_x - das schmälert aber auch den Platz, den die
# Kopfzeile für das Symbol reserviert, sodass die Überschrift sonst darauf rutscht.
# ICON_GAP gleicht das wieder aus.
ICON_SCALE = 0.75
ICON_GAP = 0.6


class _Base:
    """Gemeinsame Grundlage. Alle Karteien außer „Gebiet“ starten eingeklappt -
    Blender merkt sich den Zustand danach je Seitenleiste."""

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Umgebung"
    rc_icon = 'NONE'

    def draw_header(self, context):
        """Kleines Symbol vor der Panel-Überschrift - zur schnelleren Orientierung.

        Siehe ICON_SCALE / ICON_GAP.
        """
        zeile = self.layout.row()
        zeile.scale_x = ICON_SCALE
        zeile.label(text="", icon=self.rc_icon)
        self.layout.separator(factor=ICON_GAP)


class RC_PT_status(_Base, Panel):
    """Fortschritt und letzte Meldung - für alle Befehle an einer Stelle."""

    bl_idname = "RC_PT_status"
    bl_options = {'HIDE_HEADER'}
    bl_order = 0
    bl_label = "Status"

    @classmethod
    def poll(cls, context):
        props = context.scene.railway_chainage
        return props.busy or bool(props.scan_info)

    def draw(self, context):
        _status_anzeigen(self.layout, context.scene.railway_chainage)


class RC_PT_source(_Base, Panel):
    """Einzige Stelle für Server und Zugang - alle anderen Panels übernehmen sie."""

    bl_idname = "RC_PT_source"
    rc_icon = 'NETWORK_DRIVE'
    bl_order = 1
    bl_label = "Datenquelle"

    def draw(self, context):
        from . import props as props_mod
        layout = self.layout
        prefs = props_mod.preferences(context)
        if prefs is None:
            uitext.label(layout, "Add-on-Einstellungen nicht verfügbar", icon='ERROR')
            return
        props_mod.draw_data_source(layout, prefs)


class RC_PT_extent(_Base, Panel):
    bl_idname = "RC_PT_extent"
    rc_icon = 'WORLD'
    bl_order = 2
    bl_label = "Gebiet"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.row().prop(props, "area_mode", expand=True)
        col = layout.column(align=True)
        if props.area_mode == 'BBOX':
            col.prop(props, "min_lat")
            col.prop(props, "max_lat")
            col.prop(props, "min_lon")
            col.prop(props, "max_lon")
        else:
            col.prop(props, "center_lat")
            col.prop(props, "center_lon")
            col.prop(props, "radius")

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("railway_chainage.select_area", icon='SELECT_SET')
        row = layout.row(align=True)
        row.operator("railway_chainage.paste_bbox", icon='PASTEDOWN')
        row.operator("railway_chainage.open_map", icon='URL')
        layout.prop(props, "use_scene_origin")


class RC_PT_routelist(_Base, Panel):
    bl_idname = "RC_PT_routelist"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'SNAP_EDGE'
    bl_order = 3
    bl_label = "Strecken Auswahl"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout

        col = layout.column(align=True)
        col.prop(props, "track_types", expand=True)
        layout.prop(props, "skip_service_tracks")

        col = layout.column(align=True)
        col.prop(props, "include_station_tracks")
        if props.include_station_tracks:
            col.prop(props, "station_radius")

        layout.operator("railway_chainage.scan", icon='VIEWZOOM')

        if props.routes:
            layout.template_list("RC_UL_routes", "", props, "routes", props, "routes_index", rows=6)
            row = layout.row(align=True)
            row.operator("railway_chainage.select_routes", text="Alle").action = 'ALL'
            row.operator("railway_chainage.select_routes", text="Keine").action = 'NONE'
            row.operator("railway_chainage.select_routes", text="Mit Tafeln").action = 'MILESTONES'


class RC_PT_chainline(_Base, Panel):
    bl_idname = "RC_PT_chainline"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'SNAP_INCREMENT'
    bl_order = 4
    bl_label = "Kilometrierung"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "chainage_source")
        col = layout.column(align=True)
        if props.chainage_source == 'MANUAL':
            col.prop(props, "start_km")
            col.prop(props, "reverse")
        else:
            col.prop(props, "milestone_distance")
            col.prop(props, "milestone_tolerance")
            col.prop(props, "fallback_manual")
            if props.fallback_manual:
                col.prop(props, "start_km")

        layout.separator()
        box = layout.box()
        box.label(text="Lage der Linie", icon='CON_TRACKTO')
        col = box.column(align=True)
        col.prop(props, "axis_mode", text="")
        if props.axis_mode == 'CENTER':
            col.prop(props, "track_spacing")
            col.prop(props, "axis_transition")
        elif props.axis_mode == 'PARALLEL':
            col.prop(props, "parallel_offset")
            col.prop(props, "parallel_side")
        else:
            col.prop(props, "only_longest_chain")
        col.prop(props, "check_crossings")

        layout.separator()
        col = layout.column(align=True)
        col.prop(props, "line_resample")
        col.prop(props, "interval_mode")
        if props.interval_mode == 'FIXED':
            col.prop(props, "interval")
        col.prop(props, "clip_to_area")
        col.prop(props, "min_chain_length")
        col.prop(props, "max_angle")
        col.prop(props, "z_offset")

        count = sum(1 for item in props.routes if item.selected)
        row = layout.row()
        row.scale_y = 1.4
        row.enabled = count > 0
        row.operator("railway_chainage.generate", icon='CURVE_PATH')


class RC_PT_chainstyle(_Base, Panel):
    """Darstellung der Kilometrierung - als Unterpanel von „Kilometrierung“."""

    bl_idname = "RC_PT_chainstyle"
    bl_parent_id = "RC_PT_chainline"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'MATERIAL'
    bl_order = 1
    bl_label = "Darstellung"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(props, "line_width")
        col.prop(props, "make_ticks")
        if props.make_ticks:
            col.prop(props, "tick_length")
            col.prop(props, "tick_length_km")

        layout.separator()
        layout.prop(props, "make_labels")
        if props.make_labels:
            col = layout.column(align=True)
            col.prop(props, "label_format")
            col.prop(props, "label_mode")
            col.prop(props, "label_prefix")
            if props.label_format == 'DECIMAL':
                col.prop(props, "decimals")
            col.prop(props, "decimal_separator")
            col.prop(props, "text_size")
            col.prop(props, "km_text_factor")
            col.prop(props, "label_offset")
            col.prop(props, "label_side")
            col.prop(props, "label_orientation")

        layout.separator()
        layout.prop(props, "parent_objects")
        layout.prop(props, "create_material")
        if props.create_material:
            layout.prop(props, "color")


class RC_PT_ground(_Base, Panel):
    bl_idname = "RC_PT_ground"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'MESH_GRID'
    bl_order = 6
    bl_label = "Gelände und Korridor"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "corridor_radius")

        box = layout.box()
        box.label(text="Geländemodell", icon='RNDCURVE')
        box.row().prop(props, "terrain_source", expand=True)
        from . import simbox
        col = box.column(align=True)
        if props.terrain_source == 'FLAT':
            col.prop(props, "flat_spacing")
            col.prop(props, "flat_height")
        else:
            if not simbox.active():
                col.prop(props, "dem_service")
            col.prop(props, "dem_spacing")
            if not simbox.active():
                col.prop(props, "dem_fill_gaps")
                if props.dem_fill_gaps:
                    col.prop(props, "dem_fallback_zoom")
            col.prop(props, "dem_zero_level")
            col.prop(props, "dem_use_blosm_offset")
        col.prop(props, "terrain_segment_length")
        col.prop(props, "drape_mode")
        box.operator("railway_chainage.import_terrain", icon='IMPORT')

        sub = box.column(align=True)
        sub.prop(props, "chunk_size")
        sub.prop(props, "area_with_map")
        row = sub.row()
        row.enabled = _gebiet_abschaetzung(sub, context, props) is not False
        row.operator("railway_chainage.import_area", icon='MESH_GRID')

        row = box.row(align=True)
        row.operator("railway_chainage.shrinkwrap", text="Shrinkwrap", icon='MOD_SHRINKWRAP')
        row.operator("railway_chainage.shrinkwrap", text="", icon='X').remove = True

        box = layout.box()
        box.label(text="Kartentextur", icon='IMAGE_DATA')
        col = box.column(align=True)
        col.prop(props, "geo_map_source" if simbox.active() else "map_source")
        col.prop(props, "map_selected_only")
        col.prop(props, "map_quality")
        if props.map_quality == 'CUSTOM':
            col.prop(props, "map_resolution")
        _karten_abschaetzung(box, context, props)
        sub = box.column(align=True)
        sub.prop(props, "map_max_edge")
        sub.prop(props, "map_max_tiles")
        box.operator("railway_chainage.map_texture", icon='TEXTURE')


class RC_PT_trackaxes(_Base, Panel):
    bl_idname = "RC_PT_trackaxes"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'CURVE_PATH'
    bl_order = 5
    bl_label = "Gleisachsen"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(props, "rails_object_mode")
        col.prop(props, "rails_all_tracks")
        col.prop(props, "rails_separate_switches")
        sub = col.column(align=True)
        sub.enabled = props.rails_separate_switches
        sub.prop(props, "rails_switch_length")
        sub.prop(props, "rails_crossings")
        sub.prop(props, "rails_max_diverging")
        winkel = sub.column(align=True)
        winkel.enabled = props.rails_max_diverging > 0.0
        winkel.prop(props, "rails_transition")
        winkel.prop(props, "rails_cap_crossings")
        col.prop(props, "rails_spacing")
        col.prop(props, "rails_drape")

        row = layout.row()
        row.scale_y = 1.4
        row.operator("railway_chainage.generate_rails", icon='CURVE_PATH')
        layout.operator("railway_chainage.organize_tracks", icon='OUTLINER_COLLECTION')


class RC_PT_houses(_Base, Panel):
    bl_idname = "RC_PT_houses"
    bl_options = {'DEFAULT_CLOSED'}
    rc_icon = 'HOME'
    bl_order = 7
    bl_label = "Gebäude"

    def draw(self, context):
        props = context.scene.railway_chainage
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(props, "buildings_default_height")
        col.prop(props, "buildings_level_height")
        col.prop(props, "buildings_min_area")
        col.prop(props, "buildings_parts")

        col = layout.column(align=True)
        col.prop(props, "buildings_only_corridor")
        col.prop(props, "buildings_drape")
        if props.buildings_drape:
            col.prop(props, "buildings_skirt")

        row = layout.row()
        row.scale_y = 1.4
        row.operator("railway_chainage.import_buildings", icon='HOME')


# Die Reihenfolge im Panel steuert bl_order. Blender merkt sich die Anordnung
# allerdings je Seitenleiste: Panels, die eine Seitenleiste schon kennt, bleiben
# dort stehen, wo sie waren. Deshalb tragen die Panels seit 1.14 neue Kennungen -
# so legt Blender sie neu an und sortiert sie nach bl_order.
classes = (RC_UL_routes, RC_PT_status, RC_PT_source, RC_PT_extent, RC_PT_routelist,
           RC_PT_chainline, RC_PT_chainstyle,          # Unterpanel nach dem Elternpanel
           RC_PT_trackaxes, RC_PT_ground, RC_PT_houses)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

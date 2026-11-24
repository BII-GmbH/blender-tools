"""Crop, georeference and align plan planes, plus the sidebar panel.

Georeference: click grid crosses on the plan and enter their Rechtswert and
Hochwert. A 2D similarity transform (scale, rotation, shift) fits the sheet
to the plan coordinates. "Place in Scene" converts plan coordinates to WGS84
and then to the scene meters of the Umgebung extension (scene["lat"] /
scene["lon"]), so the plan lies on its maps and tracks. With two points or
more, each new click gets its coordinates predicted, and the grid overlay
shows whether the points are right.

Align to Scene: pairs of a sheet point and a scene point (the 3D cursor).
Turns the plan about the vertical axis and moves it; with Fit Scale it also
scales it. Works for upright views (Ansichten) too.
"""

import math
import os

import blf
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import import_plan, plan_geo

SNAP_RADIUS = 0.004  # paper meters around the click that snapping looks at

_pixel_cache = {}  # image name -> (key, numpy array)
_pending_crop = {}  # object name -> first corner of a crop rectangle
_picking = {}  # area pointer -> banner text of the pick running in that 3D view
_draw_handle = None
_banner_handle = None
PICK_COLOR = (1.0, 0.55, 0.1, 1.0)


# ---------------------------------------------------------------- fitting

def has_values(p):
    return p.easting_state != 'EMPTY' or p.northing_state != 'EMPTY'


def used_points(pg):
    """Points with values that take part in the fit (read or guessed)."""
    return [p for p in pg.points if p.use and has_values(p)]


def read_values(pg, axis):
    """Values of one axis ("easting" or "northing") read from the plan."""
    return [getattr(p, axis) for p in used_points(pg) if getattr(p, axis + "_state") == 'READ']


def is_guessed(p):
    return p.easting_state == 'GUESSED' or p.northing_state == 'GUESSED'


def is_read(p):
    return p.easting_state == 'READ' or p.northing_state == 'READ'


_WEIGHTS = {'READ': 1.0, 'GUESSED': plan_geo.GUESS_WEIGHT, 'EMPTY': 0.0}


def _fit_points(pg, guesses=True):
    """Points for plan_geo's weighted fits; guesses=False leaves the guesses out."""
    def weight(state):
        return _WEIGHTS[state] if guesses or state == 'READ' else 0.0
    return [(p.x, p.y, p.easting, weight(p.easting_state), p.northing, weight(p.northing_state))
            for p in used_points(pg)]


def scene_position(scene, obj, pg, x, y):
    """Plan coordinates of a sheet point at the plan's current place in the scene.

    Goes back from scene meters through the Umgebung projection (scene["lat"]
    / scene["lon"]) to WGS84 and into the plan's coordinate system. The zone
    is the one of the entered points, else the one the point lies in.
    Returns (easting, northing), or None without a scene origin.
    """
    if "lat" not in scene or "lon" not in scene:
        return None
    world = obj.matrix_world @ Vector((x, y, 0.0))
    lat, lon = plan_geo.SceneProjection(scene["lat"], scene["lon"]).to_geographic(
        world.x, world.y)
    known = read_values(pg, "easting")
    if pg.crs == 'ETRS89':
        prefixed = [e for e in known if e >= 1e7]
        zone = int(prefixed[0] // 1e6) if prefixed else pg.utm_zone
        return plan_geo.wgs84_to_grid(pg.crs, lat, lon, zone, zone_prefix=bool(prefixed))
    zones = [int(e // 1e6) for e in known if 2e6 <= e < 6e6]
    zone = zones[0] if zones else plan_geo.default_zone(pg.crs, lon)
    return plan_geo.wgs84_to_grid(pg.crs, lat, lon, zone)


def estimate_from_scene(scene, obj, pg, p):
    """Fill a point with its current scene position, rounded to the grid spacing
    when one is set. Returns False without a scene origin."""
    pos = scene_position(scene, obj, pg, p.x, p.y)
    if pos is None:
        return False
    s = pg.grid_spacing or 1
    import_plan.set_point_value(p, "easting", round(pos[0] / s) * s, 'GUESSED')
    import_plan.set_point_value(p, "northing", round(pos[1] / s) * s, 'GUESSED')
    return True


def refresh_guesses(scene, obj, pg):
    """Set all guessed values again from where the points lie in the scene now.

    Values read from the plan stay. Returns the number of changed values, or
    -1 without a scene origin.
    """
    if "lat" not in scene or "lon" not in scene:
        return -1
    s = pg.grid_spacing or 1
    changed = 0
    for p in pg.points:
        if not is_guessed(p):
            continue
        e, n = scene_position(scene, obj, pg, p.x, p.y)
        if p.easting_state == 'GUESSED':
            import_plan.set_point_value(p, "easting", round(e / s) * s, 'GUESSED')
            changed += 1
        if p.northing_state == 'GUESSED':
            import_plan.set_point_value(p, "northing", round(n / s) * s, 'GUESSED')
            changed += 1
    return changed


def sheet_fit(pg):
    """Similarity transform sheet (paper meters) -> plan coordinates, or None.

    Values read from the plan decide; guessed values only fill in what they
    leave open. Without any value read from the plan there is no fit.
    """
    if not any(is_read(p) for p in used_points(pg)):
        return None
    return plan_geo.fit_similarity_weighted(_fit_points(pg))


def grid_spacing(pg):
    """The Grid Spacing setting, else the coarsest standard spacing that fits the
    values read so far (an upper bound: the real grid may be finer), else 0."""
    if pg.grid_spacing:
        return pg.grid_spacing
    diffs = set()
    for values in (read_values(pg, "easting"), read_values(pg, "northing")):
        diffs |= {abs(a - b) for a in values for b in values if a != b}
    for s in plan_geo.STANDARD_SPACINGS:
        if diffs and all(d % s == 0 for d in diffs):
            return s
    return 0


def predict_values(pg, x, y):
    """Plan coordinates of a sheet point from the fit, rounded to the grid, or None.

    With Grid Spacing set, rounded to it. Else to the grid the prediction
    lies near: the spacing inferred from the read values is only an upper
    bound, see plan_geo.round_to_grid.
    """
    fit = sheet_fit(pg)
    if fit is None:
        return None
    e, n = plan_geo.apply_similarity(fit, x, y)
    if pg.grid_spacing:
        s = pg.grid_spacing
        return round(e / s) * s, round(n / s) * s
    spacing = grid_spacing(pg)
    return plan_geo.round_to_grid(e, spacing), plan_geo.round_to_grid(n, spacing)


def update_residuals(pg):
    """Residuals of the values read from the plan; guessed values do not count."""
    fit = sheet_fit(pg)
    for p in pg.points:
        if fit and p.use and is_read(p):
            e, n = plan_geo.apply_similarity(fit, p.x, p.y)
            de = e - p.easting if p.easting_state == 'READ' else 0.0
            dn = n - p.northing if p.northing_state == 'READ' else 0.0
            p.residual = math.hypot(de, dn)
        else:
            p.residual = -1.0


def fit_summary(pg):
    """(Maßstab, RMS residual, worst point, warnings), or None without a fit."""
    fit = sheet_fit(pg)
    if not fit:
        return None
    used = [p for p in used_points(pg) if p.residual >= 0]
    if not used:
        return None
    rms = math.sqrt(sum(p.residual ** 2 for p in used) / len(used))
    worst = max(used, key=lambda p: p.residual)
    warnings = []
    if not plan_geo.fixes_scale_and_rotation(_fit_points(pg, guesses=False)):
        warnings.append("The values read from the plan do not fix Maßstab and rotation, the "
                        "guesses turn the plan. Read more values: at least 3, and for one "
                        "axis alone from crosses not all in one line")
    full = [p for p in used_points(pg)
            if p.easting_state == 'READ' and p.northing_state == 'READ']
    warnings += plan_geo.check_axes([(p.x, p.y) for p in full],
                                    [(p.easting, p.northing) for p in full])
    return plan_geo.similarity_scale(fit), rms, worst, warnings


# ---------------------------------------------------------------- picking

def _inside(region, x, y):
    return (region.width > 1 and region.height > 1 and region.x <= x < region.x + region.width
            and region.y <= y < region.y + region.height)


def window_region(area, x, y):
    """The 3D view's main region under the mouse (window coordinates), or None.

    None also where the sidebar, toolbar, header or another region lies on
    top of the main region: with Region Overlap the main region reaches
    under them, and a click on a button there must not count as a click on
    the plan.
    """
    if any(r.type != 'WINDOW' and _inside(r, x, y) for r in area.regions):
        return None
    for region in area.regions:
        if region.type == 'WINDOW' and _inside(region, x, y):
            return region
    return None


class PickMode:
    """Mixin for modal operators that take clicks on the plan in the 3D view.

    While it runs, the view shows an orange frame and a banner saying what to
    click, the mouse is a crosshair over the view, and clicks on the sidebar,
    toolbar or header go to their buttons, not to the plan.
    """

    def pick_start(self, context, text):
        self.pick_area = context.area.as_pointer()
        self.pick_cross = False
        self.pick_hint(context, text)
        context.window_manager.modal_handler_add(self)

    def pick_hint(self, context, text):
        _picking[self.pick_area] = text
        context.workspace.status_text_set(text)
        if context.area:
            context.area.tag_redraw()

    def pick_end(self, context):
        _picking.pop(getattr(self, "pick_area", None), None)
        context.workspace.status_text_set(None)
        if getattr(self, "pick_cross", False) and context.window:
            context.window.cursor_modal_restore()
            self.pick_cross = False
        import_plan._redraw(None, context)

    def pick_on_view(self, context, event):
        """True if the mouse is over the 3D view itself; sets the cursor to match."""
        inside = (context.area is not None
                  and window_region(context.area, event.mouse_x, event.mouse_y) is not None)
        if inside != self.pick_cross and context.window:
            if inside:
                context.window.cursor_modal_set('CROSSHAIR')
            else:
                context.window.cursor_modal_restore()
            self.pick_cross = inside
        return inside


def _draw_banner():
    """Orange frame and banner in the 3D view a pick runs in."""
    context = bpy.context
    area, region = context.area, context.region
    text = _picking.get(area.as_pointer()) if area else None
    if not text or region is None:
        return
    scale = context.preferences.view.ui_scale
    w, h = region.width, region.height
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    def rects(color, boxes):
        verts = []
        for x0, y0, x1, y1 in boxes:
            verts += [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]
        batch = batch_for_shader(shader, 'TRIS', {"pos": verts})
        shader.uniform_float("color", color)
        batch.draw(shader)

    gpu.state.blend_set('ALPHA')
    t = 3 * scale
    rects(PICK_COLOR, [(0, 0, w, t), (0, h - t, w, h), (0, 0, t, h), (w - t, 0, w, h)])
    font = 0
    blf.size(font, 15 * scale)
    tw, th = blf.dimensions(font, text)
    pad = 8 * scale
    x = max(pad, (w - tw) / 2)
    y = h - 70 * scale
    rects((0.08, 0.08, 0.08, 0.85), [(x - pad, y - pad, x + tw + pad, y + th + pad)])
    rects(PICK_COLOR, [(x - pad, y - pad, x + tw + pad, y - pad + 2 * scale)])
    gpu.state.blend_set('NONE')
    blf.color(font, *PICK_COLOR)
    blf.position(font, x, y, 0)
    blf.draw(font, text)


def mouse_to_sheet(area, event, obj):
    """Sheet point (paper meters) under the mouse, or None."""
    region = window_region(area, event.mouse_x, event.mouse_y)
    if region is None:
        return None
    rv3d = area.spaces.active.region_3d
    co = (event.mouse_x - region.x, event.mouse_y - region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
    inv = obj.matrix_world.inverted()
    o = inv @ origin
    d = inv.to_3x3() @ direction
    if abs(d.z) < 1e-12:
        return None
    p = o + d * (-o.z / d.z)
    pg = obj.bii_plan
    if not (0 <= p.x <= pg.width and 0 <= p.y <= pg.height):
        return None
    return p.x, p.y


def clear_pixel_cache():
    """Free the gray pixels kept for snapping while points are picked."""
    _pixel_cache.clear()


def sheet_pixels(image):
    """Gray pixels of the plan image, kept while one pick runs (see clear_pixel_cache)."""
    from . import plan_raster
    if image is None:
        return None
    key = (image.filepath, image.packed_file.size if image.packed_file else 0)
    cached = _pixel_cache.get(image.name)
    if cached and cached[0] == key:
        return cached[1]
    if image.packed_file:
        pixels = plan_raster.gray_pixels(bytes(image.packed_file.data))
    else:
        pixels = plan_raster.gray_pixels(bpy.path.abspath(image.filepath))
    _pixel_cache.clear()  # one plan at a time: they are large
    _pixel_cache[image.name] = (key, pixels)
    return pixels


def snap_to_cross(pg, x, y):
    from . import plan_raster
    pixels = sheet_pixels(pg.image)
    if pixels is None:
        return None
    h, w = pixels.shape
    px, py = x / pg.width * w, (1 - y / pg.height) * h
    radius = max(5, SNAP_RADIUS / pg.width * w)
    center = plan_raster.find_mark_center(pixels, px, py, radius)
    if center is None:
        return None
    return center[0] / w * pg.width, (1 - center[1] / h) * pg.height


class PlanPickOperator(PickMode, bpy.types.Operator):
    """Click points on the plan in the viewport"""
    bl_idname = "bii_plan.pick"
    bl_label = "Pick on Plan"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(items=[
        ('CROP', "Crop Rectangle", "Click two corners of the part of the sheet to keep"),
        ('GEOREF', "Add Grid Cross", "Click a grid cross, then enter its coordinates"),
        ('ALIGN', "Add Align Point", "Click a sheet point; it goes to the 3D cursor's "
         "position at the time of the click")])
    multiple: BoolProperty(name="Several", options={'SKIP_SAVE'},
                           description="Add several points, until Esc or right click")

    @classmethod
    def description(cls, context, properties):
        return {'CROP': "Click two corners of the part of the sheet to keep",
                'GEOREF': "Click a grid cross on the plan. From the third point on, the "
                          "coordinates are predicted.\nShift-click: add several, until Esc",
                'ALIGN': "Click a point on the sheet. It goes to the position of the 3D "
                         "cursor at the time of the click.\nShift-click: add several, "
                         "until Esc"}[properties.mode]

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan and context.area
                and context.area.type == 'VIEW_3D')

    def hint(self):
        if self.mode == 'CROP':
            if _pending_crop.get(self.obj_name):
                return "CROP: click the opposite corner   ·   Esc: cancel"
            return "CROP: click the first corner of the part to keep   ·   Esc: cancel"
        what = "GRID CROSS" if self.mode == 'GEOREF' else "ALIGN POINT"
        if not self.multiple:
            where = ("click a grid cross on the plan" if self.mode == 'GEOREF' else
                     "click the sheet point that goes to the 3D cursor")
            return f"ADD {what}: {where}   ·   Esc: cancel"
        where = ("click grid crosses on the plan" if self.mode == 'GEOREF' else
                 "click sheet points, Shift + right click moves the 3D cursor")
        return f"ADD {what}S: {where}   ·   {self.added} added   ·   Esc / right click: done"

    def invoke(self, context, event):
        if self.mode == 'CROP' and context.object.mode != 'OBJECT':
            self.report({'ERROR'}, "Crop in Object Mode, or edit the outline in Edit Mode")
            return {'CANCELLED'}
        self.obj_name = context.object.name
        self.added = 0
        self.multiple = self.mode != 'CROP' and (self.multiple or event.shift)
        _pending_crop.pop(self.obj_name, None)
        self.pick_start(context, self.hint())
        return {'RUNNING_MODAL'}

    def finish(self, context):
        self.pick_end(context)
        _pending_crop.pop(self.obj_name, None)
        clear_pixel_cache()  # a full sheet takes about 200 MB as gray pixels

    def modal(self, context, event):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            self.finish(context)
            return {'CANCELLED'}
        on_view = self.pick_on_view(context, event)
        if event.type == 'RIGHTMOUSE' and event.shift:
            return {'PASS_THROUGH'}  # Shift + right click places the 3D cursor
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
            self.finish(context)
            return {'FINISHED'} if self.added else {'CANCELLED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if not on_view:
            return {'PASS_THROUGH'}  # the sidebar, so values can be typed meanwhile
        point = mouse_to_sheet(context.area, event, obj)
        if point is None:
            self.report({'WARNING'}, "Click on the sheet")
            return {'RUNNING_MODAL'}
        pg = obj.bii_plan
        if self.mode == 'CROP':
            first = _pending_crop.get(self.obj_name)
            if first is None:
                _pending_crop[self.obj_name] = point
                self.pick_hint(context, self.hint())
                return {'RUNNING_MODAL'}
            import_plan.set_sheet_rectangle(obj, *first, *point)
            if pg.full_sheet:
                pg.full_sheet = False
            self.added = 1
            self.finish(context)
            return {'FINISHED'}
        if self.mode == 'GEOREF':
            self.add_cross(pg, point, context)
        else:
            pair = pg.pairs.add()
            pair.name = f"A{len(pg.pairs)}"
            pair.x, pair.y = point
            pair.target = context.scene.cursor.location
            pg.pair_index = len(pg.pairs) - 1
        self.added += 1
        if not self.multiple:
            self.finish(context)
            return {'FINISHED'}
        self.pick_hint(context, self.hint())
        import_plan._redraw(None, context)
        return {'RUNNING_MODAL'}

    def add_cross(self, pg, point, context=None):
        snapped = snap_to_cross(pg, *point) if pg.snap else None
        if pg.snap and snapped is None:
            self.report({'WARNING'}, "No cross found near the click, the point stays where "
                        "you clicked")
        x, y = snapped or point
        predicted = predict_values(pg, x, y)
        p = pg.points.add()
        p.name = f"P{len(pg.points)}"
        p.x, p.y = x, y
        if predicted:
            # Predicted values are guesses until the user confirms or types them.
            import_plan.set_point_value(p, "easting", predicted[0], 'GUESSED')
            import_plan.set_point_value(p, "northing", predicted[1], 'GUESSED')
        elif context is not None:
            obj = bpy.data.objects[self.obj_name]
            if estimate_from_scene(context.scene, obj, pg, p):
                self.report({'INFO'}, f"{p.name}: {p.easting} / {p.northing} estimated from "
                            "where the plan lies now. Round them to the real values")
        pg.point_index = len(pg.points) - 1
        update_residuals(pg)


# ---------------------------------------------------------------- placing

def umgebung_origin(scene):
    """Projection center the Umgebung extension would use, from its panel, or None.

    Same rule as Umgebung's get_projection without a stored origin: the
    center of its area (center point, or middle of the bounding box). Only
    right as long as nobody changed the area since Umgebung built the scene.
    """
    props = getattr(scene, "railway_chainage", None)
    if props is None:
        return None
    try:
        if props.area_mode == 'CENTER':
            lat, lon = props.center_lat, props.center_lon
        else:
            lat = (props.min_lat + props.max_lat) / 2.0
            lon = (props.min_lon + props.max_lon) / 2.0
    except AttributeError:  # another Umgebung version
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


class PlanOriginFromUmgebungOperator(bpy.types.Operator):
    """Store the projection center of the Umgebung panel's area as scene origin
    (scene["lat"] / scene["lon"]). Right only if the area has not changed since
    Umgebung built the scene"""
    bl_idname = "bii_plan.origin_from_umgebung"
    bl_label = "Origin from Umgebung Panel"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return ("lat" not in scene or "lon" not in scene) and umgebung_origin(scene) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        lat, lon = umgebung_origin(context.scene)
        col = self.layout.column(align=True)
        col.label(text=f"Scene origin: {lat:.6f}, {lon:.6f}")
        col.label(text="The center of the area in the Umgebung panel.")
        col.label(text="Only right if the area is unchanged since the last build.", icon='ERROR')

    def execute(self, context):
        lat, lon = umgebung_origin(context.scene)
        context.scene["lat"], context.scene["lon"] = lat, lon
        self.report({'INFO'}, f"Scene origin set to {lat:.6f}, {lon:.6f} from the Umgebung panel")
        return {'FINISHED'}


class PlanGeoreferenceOperator(bpy.types.Operator):
    """Place the plan in the scene by its grid crosses, at the Umgebung coordinates
    (scene["lat"] / scene["lon"])"""
    bl_idname = "bii_plan.georeference"
    bl_label = "Place in Scene"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan and obj.mode == 'OBJECT'

    def execute(self, context):
        obj = context.object
        pg = obj.bii_plan
        fit = sheet_fit(pg)
        if fit is None:
            self.report({'ERROR'}, "Enter Rechtswert and Hochwert of at least 2 grid crosses")
            return {'CANCELLED'}
        scene = context.scene
        try:
            def to_latlon(x, y):
                e, n = plan_geo.apply_similarity(fit, x, y)
                return plan_geo.grid_to_wgs84(pg.crs, e, n, pg.utm_zone)

            new_origin = "lat" not in scene or "lon" not in scene
            if new_origin:
                lat, lon = to_latlon(pg.width / 2, pg.height / 2)
                scene["lat"], scene["lon"] = round(lat, 6), round(lon, 6)
            proj = plan_geo.SceneProjection(scene["lat"], scene["lon"])
            sheet = [(0, 0), (pg.width, 0), (pg.width, pg.height), (0, pg.height),
                     (pg.width / 2, pg.height / 2)]
            world = [proj.from_geographic(*to_latlon(x, y)) for x, y in sheet]
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        place = plan_geo.fit_similarity(sheet, world)
        bend = max(math.hypot(*(a - b for a, b in zip(plan_geo.apply_similarity(place, *s), w)))
                   for s, w in zip(sheet, world))

        z = obj.matrix_world.translation.z
        pg.orientation = 'FLAT'
        s = plan_geo.similarity_scale(place)
        obj.matrix_world = (Matrix.Translation((place[2], place[3], z))
                            @ Matrix.Rotation(plan_geo.similarity_angle(place), 4, 'Z')
                            @ Matrix.Diagonal((s, s, s, 1.0)))

        scale, rms, worst, warnings = fit_summary(pg)
        text = (f"Placed with {len(used_points(pg))} points: Maßstab 1:{scale:.1f} measured, "
                f"residuals {rms:.2f} m RMS, largest {worst.residual:.2f} m ({worst.name})")
        if new_origin:
            text += f"; scene origin set to {scene['lat']:.6f}, {scene['lon']:.6f}"
        if bend > 0.05:
            text += f"; the projection bends the sheet by up to {bend:.2f} m"
        text, chain_warnings = place_attached(context, obj, text)
        warnings = warnings + chain_warnings
        for w in warnings:
            text += f". {w}"
        self.report({'WARNING'} if warnings else {'INFO'}, text)
        return {'FINISHED'}


class PlanAlignOperator(bpy.types.Operator):
    """Move and turn the plan so its points meet their targets in the scene"""
    bl_idname = "bii_plan.align"
    bl_label = "Align"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan and obj.mode == 'OBJECT'
                and len(obj.bii_plan.pairs) > 0)

    def execute(self, context):
        obj = context.object
        pg = obj.bii_plan
        m = obj.matrix_world
        src = [m @ Vector((p.x, p.y, 0.0)) for p in pg.pairs]
        dst = [Vector(p.target) for p in pg.pairs]
        n = len(src)
        cs = sum(src, Vector()) / n
        ct = sum(dst, Vector()) / n
        a = [v - cs for v in src]
        b = [v - ct for v in dst]

        # Turn about the vertical axis only, from the horizontal directions.
        dot = sum(u.x * v.x + u.y * v.y for u, v in zip(a, b))
        cross = sum(u.x * v.y - u.y * v.x for u, v in zip(a, b))
        angle = math.atan2(cross, dot) if math.hypot(dot, cross) > 1e-9 else 0.0
        # Least-squares scale, as for grid crosses. A flat plan spans XY only:
        # the height of the targets (3D cursor on the terrain) says nothing
        # about its scale.
        scale = 1.0
        scale_fixed = False
        if pg.fit_scale and n >= 2:
            flat = pg.orientation == 'FLAT'
            turn = Matrix.Rotation(angle, 3, 'Z')
            ra = [turn @ v for v in a]
            if flat:
                ra = [Vector((v.x, v.y, 0.0)) for v in ra]
            sa = sum(v.length_squared for v in ra)
            if sa / n > 4.0:  # points at least about 2 m from their middle
                scale = sum(u.dot(v) for u, v in zip(ra, b)) / sa
                scale_fixed = scale > 0
            if not scale_fixed:
                scale = 1.0

        delta = (Matrix.Translation(ct) @ Matrix.Rotation(angle, 4, 'Z')
                 @ Matrix.Diagonal((scale, scale, scale, 1.0)) @ Matrix.Translation(-cs))
        obj.matrix_world = delta @ m
        for p, s, t in zip(pg.pairs, src, dst):
            off = (delta @ s) - t
            p.residual = off.xy.length if pg.orientation == 'FLAT' else off.length
        rms = math.sqrt(sum(p.residual ** 2 for p in pg.pairs) / n)
        text = f"Aligned with {n} points: turned {math.degrees(angle):.2f}°"
        if scale_fixed:
            text += f", Maßstab 1:{pg.massstab:.1f} measured"
        if n >= 2:
            text += f", residuals {rms:.3f} m RMS"
        text, warnings = place_attached(context, obj, text)
        if pg.fit_scale and not scale_fixed:
            warnings.insert(0, "The points do not fix the Maßstab: add points farther apart. "
                            "The plan keeps its Maßstab")
        self.report({'WARNING'} if warnings else {'INFO'}, ". ".join([text] + warnings))
        return {'FINISHED'}


def place_attached(context, obj, text):
    """Move the neighbor sheets attached to a plan that was just placed.

    Returns the report text with the moved and kept sheets added, and the
    warnings of the chain.
    """
    from . import plan_place  # plan_place imports this module
    context.view_layer.update()
    moved, kept, warnings = plan_place.place_chain(context.scene, obj)
    if moved:
        text += f"; attached sheets placed too: {', '.join(moved)}"
    if kept:
        warnings.append(f"Placed by their own km points, not moved: {', '.join(kept)}")
    return text, warnings


class PlanPairTargetOperator(bpy.types.Operator):
    """Set the target of the active align point to the 3D cursor"""
    bl_idname = "bii_plan.pair_target_from_cursor"
    bl_label = "Target from 3D Cursor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan and len(obj.bii_plan.pairs) > 0

    def execute(self, context):
        pg = context.object.bii_plan
        pg.pairs[min(pg.pair_index, len(pg.pairs) - 1)].target = context.scene.cursor.location
        return {'FINISHED'}


class PlanPointFromSceneOperator(bpy.types.Operator):
    """Set the active point's values to where it lies in the scene now, rounded to the
    grid spacing if one is set. Needs the scene origin (scene["lat"] / scene["lon"])"""
    bl_idname = "bii_plan.point_from_scene"
    bl_label = "From Scene Position"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan and len(obj.bii_plan.points) > 0
                and "lat" in context.scene and "lon" in context.scene)

    def execute(self, context):
        obj = context.object
        pg = obj.bii_plan
        p = pg.points[min(pg.point_index, len(pg.points) - 1)]
        try:
            estimate_from_scene(context.scene, obj, pg, p)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        update_residuals(pg)
        import_plan._redraw(None, context)
        return {'FINISHED'}


class PlanRefreshGuessesOperator(bpy.types.Operator):
    """Set all guessed values (?) again from where the points lie in the scene now,
    for example after moving the plan by hand. Values read from the plan stay"""
    bl_idname = "bii_plan.refresh_guesses"
    bl_label = "Update Guesses"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan
                and any(is_guessed(p) for p in obj.bii_plan.points)
                and "lat" in context.scene and "lon" in context.scene)

    def execute(self, context):
        pg = context.object.bii_plan
        try:
            n = refresh_guesses(context.scene, context.object, pg)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        update_residuals(pg)
        import_plan._redraw(None, context)
        self.report({'INFO'}, f"{n} guessed values updated")
        return {'FINISHED'}


class PlanRemoveOperator(bpy.types.Operator):
    """Remove the active point"""
    bl_idname = "bii_plan.remove"
    bl_label = "Remove Point"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(items=[('POINT', "Grid Cross", ""), ('PAIR', "Align Point", "")])
    clear: bpy.props.BoolProperty(name="All", description="Remove all points",
                                  options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan

    def execute(self, context):
        pg = context.object.bii_plan
        items, index = ((pg.points, "point_index") if self.kind == 'POINT'
                        else (pg.pairs, "pair_index"))
        if self.clear:
            items.clear()
        elif len(items):
            i = min(getattr(pg, index), len(items) - 1)
            items.remove(i)
            setattr(pg, index, max(0, i - 1))
        update_residuals(pg)
        import_plan._redraw(None, context)
        return {'FINISHED'}


# ---------------------------------------------------------------- overlay

def _clip_segment(p0, p1, width, height):
    """Liang-Barsky clipping of a 2D segment to [0, width] x [0, height]."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0), (dx, width - x0), (-dy, y0), (dy, height - y0)):
        if abs(p) < 1e-15:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def _grid_lines(pg, fit, spacing):
    """Grid lines on the sheet: (value, axis, sheet start, sheet end)."""
    corners = [plan_geo.apply_similarity(fit, x, y)
               for x, y in ((0, 0), (pg.width, 0), (pg.width, pg.height), (0, pg.height))]
    es, ns = [c[0] for c in corners], [c[1] for c in corners]
    lines = []
    for axis, (lo, hi), (olo, ohi) in ((0, (min(es), max(es)), (min(ns), max(ns))),
                                       (1, (min(ns), max(ns)), (min(es), max(es)))):
        count = (hi - lo) / spacing
        if count > 400:
            continue
        v = math.ceil(lo / spacing) * spacing
        while v <= hi:
            if axis == 0:
                a, b = (v, olo), (v, ohi)
            else:
                a, b = (olo, v), (ohi, v)
            seg = _clip_segment(plan_geo.invert_similarity(fit, *a),
                                plan_geo.invert_similarity(fit, *b), pg.width, pg.height)
            if seg:
                lines.append((v, axis, *seg))
            v += spacing
    return lines


def _draw_overlay():
    context = bpy.context
    obj = context.active_object
    if obj is None or not getattr(obj, "bii_plan", None) or not obj.bii_plan.is_plan:
        return
    if not obj.visible_get():
        return
    region, rv3d = context.region, context.region_data
    if rv3d is None:
        return
    pg = obj.bii_plan
    m = obj.matrix_world

    def to2d(x, y):
        return view3d_utils.location_3d_to_region_2d(region, rv3d, m @ Vector((x, y, 0.0)))

    lines = {}  # color -> list of 2D points, pairs form segments
    labels = []  # (position, text, color)

    def segment(color, a, b):
        if a is not None and b is not None:
            lines.setdefault(color, []).extend((a, b))

    def marker(color, p, size=7):
        if p is not None:
            segment(color, p + Vector((-size, -size)), p + Vector((size, size)))
            segment(color, p + Vector((-size, size)), p + Vector((size, -size)))

    fit = sheet_fit(pg)
    if fit and pg.show_grid:
        spacing = grid_spacing(pg) or 100
        e_color, n_color = (0.2, 0.6, 1.0, 0.7), (1.0, 0.45, 0.1, 0.7)
        for value, axis, a, b in _grid_lines(pg, fit, spacing):
            color = e_color if axis == 0 else n_color
            pa, pb = to2d(*a), to2d(*b)
            segment(color, pa, pb)
            if pa is not None:
                labels.append((pa, f"{value:.0f}", color))

    for p in pg.points:
        if p.use and is_guessed(p):
            color = (0.5, 0.8, 1.0, 1.0)
        elif not p.use or not has_values(p):
            color = (0.6, 0.6, 0.6, 1.0)
        elif p.residual > 0.5:
            color = (1.0, 0.2, 0.2, 1.0)
        else:
            color = (1.0, 0.9, 0.1, 1.0)
        pos = to2d(p.x, p.y)
        marker(color, pos)
        if pos is not None:
            text = p.name if p.residual < 0 else f"{p.name}  {p.residual:.2f} m"
            if is_guessed(p):
                text += "  ≈"
            labels.append((pos + Vector((9, 6)), text, color))

    cyan = (0.1, 0.9, 0.9, 1.0)
    for p in pg.pairs:
        pos = to2d(p.x, p.y)
        target = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(p.target))
        marker(cyan, pos)
        marker(cyan, target, 4)
        segment(cyan, pos, target)
        if pos is not None:
            labels.append((pos + Vector((9, 6)), p.name, cyan))

    first = _pending_crop.get(obj.name)
    if first:
        marker((1.0, 1.0, 1.0, 1.0), to2d(*first), 10)

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    for color, coords in lines.items():
        batch = batch_for_shader(shader, 'LINES', {"pos": [tuple(c) for c in coords]})
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.blend_set('NONE')
    font = 0
    blf.size(font, 12)
    for pos, text, color in labels:
        blf.color(font, *color)
        blf.position(font, pos.x + 3, pos.y + 3, 0)
        blf.draw(font, text)


# ---------------------------------------------------------------- panel

class BII_UL_plan_points(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        # The values are edited below the list: two 7-digit fields do not
        # fit into a list row of the sidebar.
        row = layout.row(align=True)
        row.prop(item, "use", text="")

        def value(axis):
            state = getattr(item, axis + "_state")
            if state == 'EMPTY':
                return "–"
            return ("≈" if state == 'GUESSED' else "") + str(getattr(item, axis))
        values = f"{value('easting')} / {value('northing')}" if has_values(item) else "–"
        row.label(text=f"{item.name}   {values}")
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text="" if item.residual < 0 else f"{item.residual:.2f} m")


class BII_UL_plan_pairs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.label(text=item.name)
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.ui_units_x = 3
        sub.label(text="" if item.residual < 0 else f"{item.residual:.3f} m")


def _active_unselected(context):
    """True if there is an active object, but it is not selected (clicked into empty
    space). The sidebar then shows no plan."""
    return context.object is not None and not context.object.select_get()


def _plan(context):
    """The active plan, if it is the only plan selected; else None.

    Georeference, Kilometrierung, neighbor sheets and Align to Scene place
    one plan, so their panels show only then.
    """
    if _active_unselected(context):
        return None
    plans = import_plan.selected_plans(context)
    if len(plans) != 1 or plans[0] != context.object:
        return None
    return plans[0]


def _draw_view_hints(layout, context):
    space = context.space_data
    shading = getattr(space, "shading", None)
    if shading and shading.type == 'SOLID' and shading.color_type != 'TEXTURE':
        row = layout.row(align=True)
        row.label(text="No image in Solid view", icon='ERROR')
        row.prop_enum(shading, "color_type", 'TEXTURE', text="Show")
    view = context.scene.view_settings
    if (shading and shading.type in {'MATERIAL', 'RENDERED'}
            and view.view_transform != 'Standard'):
        wrapped_label(layout, context, f"View Transform {view.view_transform} pales the "
                      "plan's colors and contrast. Standard shows them as printed",
                      'ERROR')
        layout.prop_enum(view, "view_transform", 'Standard', text="Use Standard")


def _draw_files(layout, context, pg):
    """Where the plan's original and its image (texture) are, and the Relink button."""
    col = layout.column(align=True)
    source = import_plan.absolute_path(pg.source)
    col.prop(pg, "source", text="Original", icon='FILE' if os.path.isfile(source) else 'ERROR')
    image = pg.image
    if image is not None:
        if image.packed_file is not None:
            col.label(text="Image packed into the .blend file", icon='PACKAGE')
        else:
            missing = import_plan.texture_missing(image)
            col.prop(image, "filepath", text="Image", icon='ERROR' if missing else 'IMAGE_DATA')
            if missing:
                wrapped_label(col, context, "The image file is missing. Relink writes it "
                              "again from the original", 'ERROR')
    col.operator(import_plan.PlanRelinkOperator.bl_idname, icon='FILE_REFRESH')


def _draw_several(layout, context, plans):
    """The sidebar with several plans selected: only what applies to all of them."""
    col = layout.column(align=True)
    col.label(text=f"{len(plans)} plans selected", icon='IMAGE_DATA')
    images = {o.bii_plan.image.name: o.bii_plan.image for o in plans if o.bii_plan.image}
    row = col.row(align=True)
    row.label(text=f"{sum(import_plan.image_megabytes(i) for i in images.values()):.0f} MB RAM "
              f"for {len(images)} images")
    row.operator(import_plan.PlanResolutionOperator.bl_idname, text="", icon='IMAGE_REFERENCE')
    missing = [i for i in images.values() if import_plan.texture_missing(i)]
    if missing:
        wrapped_label(col, context, f"{len(missing)} image files missing. Relink writes "
                      "them again from the originals", 'ERROR')
    col.operator(import_plan.PlanRelinkOperator.bl_idname, icon='FILE_REFRESH')
    if any(import_plan.outline_off_sheet(o) for o in plans):
        box = layout.box()
        wrapped_label(box, context, "The outline of a plan lies outside its sheet: select it "
                      "alone for details", 'ERROR')
    multi = context.scene.bii_plan_multi
    layout.use_property_split = True
    layout.use_property_decorate = False
    for name, text, kwargs in (("massstab", "Maßstab 1:", {}),
                               ("orientation", "Orientation", {"expand": True}),
                               ("opacity", "Opacity", {}),
                               ("background", "Background", {}),
                               ("full_sheet", "Full Sheet", {})):
        mixed = import_plan.values_differ(plans, name)
        layout.prop(multi, name, text=text + (" (mixed)" if mixed else ""), **kwargs)
    layout.use_property_split = False
    row = layout.row(align=True)
    row.operator("bii_plan.auto_crop", icon='FULLSCREEN_EXIT')
    row.operator(import_plan.PlanCropResetOperator.bl_idname, icon='LOOP_BACK')
    wrapped_label(layout, context, "A change goes to all selected plans. To crop, "
                  "georeference or place a plan, select it alone", 'INFO')
    _draw_view_hints(layout, context)


class VIEW3D_PT_bii_plan(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Plan"
    bl_label = "Plan"

    def draw(self, context):
        layout = self.layout
        obj = _plan(context)
        if obj is None:
            plans = [] if _active_unselected(context) else import_plan.selected_plans(context)
            if plans:
                _draw_several(layout, context, plans)
                return
            layout.operator(import_plan.ImportPlanOperator.bl_idname, icon='IMPORT')
            layout.label(text="Select a plan to edit it")
            return
        pg = obj.bii_plan
        if import_plan.outline_off_sheet(obj):
            box = layout.box()
            wrapped_label(box, context, "The outline lies outside the sheet. Set Origin and "
                          "Apply Rotation or Scale move the sheet against the image and the "
                          "points: undo them, or Reset Crop", 'ERROR')
        col = layout.column(align=True)
        col.label(text=pg.image.name if pg.image else obj.name, icon='IMAGE_DATA')
        col.label(text=f"{pg.width * 1000:.0f} x {pg.height * 1000:.0f} mm, {pg.dpi:.0f} dpi")
        if pg.image is not None:
            row = col.row(align=True)
            row.label(text=f"{pg.image.size[0]} x {pg.image.size[1]} px, "
                      f"{import_plan.image_megabytes(pg.image):.0f} MB RAM")
            row.operator(import_plan.PlanResolutionOperator.bl_idname, text="",
                         icon='IMAGE_REFERENCE')
        _draw_files(layout, context, pg)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(pg, "massstab", text="Maßstab 1:")
        layout.prop(pg, "orientation", expand=True)
        layout.prop(pg, "opacity")
        layout.prop(pg, "background")
        layout.prop(pg, "full_sheet")
        layout.use_property_split = False
        row = layout.row(align=True)
        op = row.operator(PlanPickOperator.bl_idname, text="Crop Rectangle", icon='SELECT_SET')
        op.mode = 'CROP'
        row.operator("bii_plan.auto_crop", text="", icon='FULLSCREEN_EXIT')
        row.operator(import_plan.PlanCropResetOperator.bl_idname, text="", icon='LOOP_BACK')
        layout.label(text="Or edit the outline in Edit Mode", icon='INFO')
        if pg.orientation == 'FLAT':
            layout.operator("bii_plan.check_placement", icon='CHECKMARK')
        _draw_view_hints(layout, context)


class PlanFindGridOperator(bpy.types.Operator):
    """Find the grid crosses on the original file and add each as a point without values.
Then enter Rechtswert and Hochwert of 2 or 3 crosses and click Fill from Grid"""
    bl_idname = "bii_plan.find_grid"
    bl_label = "Find Grid"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan

    def execute(self, context):
        from . import plan_api
        context.window.cursor_set('WAIT')
        try:
            found = plan_api.find_grid(context.object)
        except (ValueError, OSError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        finally:
            context.window.cursor_set('DEFAULT')
        if not found:
            self.report({'WARNING'}, "No grid crosses found: click them by hand (+)")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{found['crosses']} grid crosses, {found['spacing_m'] or '?'} m apart. "
                    "Enter Rechtswert and Hochwert of 2 or 3 of them, then Fill from Grid")
        return {'FINISHED'}


class PlanFillFromGridOperator(bpy.types.Operator):
    """Give every grid cross its Rechtswert and Hochwert from the values entered at 2 or more of
them (crosses not all in one line)"""
    bl_idname = "bii_plan.fill_from_grid"
    bl_label = "Fill from Grid"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan and "bii_grid" in obj

    def execute(self, context):
        from . import plan_api
        try:
            n = plan_api.fill_from_grid(context.object)
        except ValueError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Filled {n} crosses. Check the residuals, then Place in Scene")
        return {'FINISHED'}


class VIEW3D_PT_bii_plan_georef(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Plan"
    bl_parent_id = "VIEW3D_PT_bii_plan"
    bl_label = "Georeference"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _plan(context) is not None

    def draw(self, context):
        layout = self.layout
        pg = context.object.bii_plan
        layout.prop(pg, "crs", text="")
        if pg.crs == 'ETRS89':
            layout.prop(pg, "utm_zone")
        info, warnings = plan_geo.check_coordinates(
            pg.crs, [(p.easting, p.northing if p.northing_state == 'READ' else None)
                     for p in used_points(pg) if p.easting_state == 'READ'], pg.utm_zone)
        if info or warnings:
            col = layout.column(align=True)
            if info:
                wrapped_label(col, context, info, icon='CHECKMARK' if not warnings else 'NONE')
            for w in warnings:
                wrapped_label(col, context, w, icon='ERROR')
        row = layout.row()
        row.template_list("BII_UL_plan_points", "", pg, "points", pg, "point_index", rows=4)
        col = row.column(align=True)
        op = col.operator(PlanPickOperator.bl_idname, text="", icon='ADD')
        op.mode = 'GEOREF'
        op = col.operator(PlanRemoveOperator.bl_idname, text="", icon='REMOVE')
        op.kind = 'POINT'
        col.separator()
        op = col.operator(PlanRemoveOperator.bl_idname, text="", icon='X')
        op.kind, op.clear = 'POINT', True
        if len(pg.points):
            point = pg.points[min(pg.point_index, len(pg.points) - 1)]
            col = layout.column(align=True)
            col.use_property_split = True
            col.use_property_decorate = False
            for name, flag in (("easting", "easting_guessed"), ("northing", "northing_guessed")):
                row = col.row(align=True)
                row.prop(point, name)
                sub = row.row(align=True)
                sub.enabled = getattr(point, name + "_state") != 'EMPTY'
                sub.prop(point, flag, text="", icon='QUESTION', toggle=True)
            if "lat" in context.scene and "lon" in context.scene:
                try:
                    pos = scene_position(context.scene, context.object, pg, point.x, point.y)
                except ValueError:
                    pos = None
                if pos:
                    col = layout.column(align=True)
                    col.label(text=f"Lies now at {pos[0]:.1f} / {pos[1]:.1f}")
                    row = col.row(align=True)
                    row.operator(PlanPointFromSceneOperator.bl_idname, icon='EYEDROPPER')
                    row.operator(PlanRefreshGuessesOperator.bl_idname, icon='FILE_REFRESH')
            if is_guessed(point):
                wrapped_label(layout, context, "? = guess: only fills what the values read "
                              "from the plan leave open. Check it against the plan, then "
                              "click ? to confirm it, or type the right value", 'INFO')
        row = layout.row(align=True)
        row.operator(PlanFindGridOperator.bl_idname, icon='VIEWZOOM')
        sub = row.row(align=True)
        sub.enabled = "bii_grid" in context.object
        sub.operator(PlanFillFromGridOperator.bl_idname, icon='GRID')
        row = layout.row(align=True)
        row.prop(pg, "snap", toggle=True)
        row.prop(pg, "show_grid", toggle=True)
        layout.prop(pg, "grid_spacing")
        summary = fit_summary(pg)
        if summary:
            scale, rms, worst, fit_warnings = summary
            col = layout.column(align=True)
            col.label(text=f"Measured Maßstab 1:{scale:.1f}")
            col.label(text=f"Residuals {rms:.2f} m RMS, largest {worst.residual:.2f} m")
            spacing = grid_spacing(pg)
            if spacing and not pg.grid_spacing:
                col.label(text=f"Grid spacing {spacing} m (from the points)")
            for w in fit_warnings:
                wrapped_label(col, context, w, icon='ERROR')
        else:
            wrapped_label(layout, context, "Click 2 or more crosses, enter their values. "
                          "Without values, the first ones are estimated from where the "
                          "plan lies now", 'INFO')
        origin = context.scene.get("lat"), context.scene.get("lon")
        if None in origin:
            if umgebung_origin(context.scene) is not None:
                wrapped_label(layout, context, "No scene origin stored. Umgebung scenes "
                              "built before Umgebung 1.29.1 may lack it", 'ERROR')
                layout.operator(PlanOriginFromUmgebungOperator.bl_idname, icon='IMPORT')
            else:
                layout.label(text="No scene origin yet: the plan sets it", icon='WORLD')
        layout.operator(PlanGeoreferenceOperator.bl_idname, icon='WORLD')


def wrapped_label(layout, context, text, icon='NONE'):
    """Labels that wrap at the sidebar width; the first line carries the icon."""
    import textwrap
    width = max(20, int(context.region.width / (7 * context.preferences.view.ui_scale)) - 4)
    for i, line in enumerate(textwrap.wrap(text, width)):
        layout.label(text=line, icon=icon if i == 0 else 'BLANK1')


CRS_HELP = (
    ('INFO', "Look on the plan for Lagesystem, Lagebezug or Koordinatensystem. "
             "Heights (Höhensystem) do not matter for the placement."),
    ('DOT', "DB_REF: plans of DB Netz, also written DB_REF2003, DB_REF2016 or "
            "DB-Referenzsystem. 7-digit Rechtswert, like 3588400."),
    ('DOT', "DHDN: Gauß-Krüger of the Landesvermessung before ETRS89, also GK, "
            "DHDN 90, Bessel or Potsdam datum. 7-digit Rechtswert."),
    ('DOT', "ETRS89 / UTM: today's Landesvermessung, also UTM or EPSG:25832 / "
            "25833. Easting with 8 digits (32xxxxxx) or 6 digits."),
    ('ERROR', "Count the digits on the plan, do not add any: a 6-digit label like "
              "582450 is a UTM easting, not a Gauß-Krüger Rechtswert without its 3. "
              "Read as Gauß-Krüger, the plan lands about 100 to 150 m off east-west "
              "and 2 km north-south. With only Rechtswerte read, the north-south part "
              "hides in the guesses."),
    ('DOT', "DHHN92, DHHN2016, NHN and NN are height systems, never Lagesysteme. "
            "If a plan names one of them for the coordinates, the labels are "
            "mixed up: take the other system it names."),
    ('DOT', "DB_REF and DHDN have nearly the same numbers. Mixing them up moves "
            "the plan by 0.2 to 1 m, so when unsure take DB_REF for DB plans."),
)


class VIEW3D_PT_bii_plan_crs_help(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Plan"
    bl_parent_id = "VIEW3D_PT_bii_plan_georef"
    bl_label = "Which Coordinate System?"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column(align=True)
        for icon, text in CRS_HELP:
            wrapped_label(col, context, text, icon)
            col.separator()


class VIEW3D_PT_bii_plan_align(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Plan"
    bl_parent_id = "VIEW3D_PT_bii_plan"
    bl_label = "Align to Scene"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return _plan(context) is not None

    def draw(self, context):
        layout = self.layout
        pg = context.object.bii_plan
        layout.label(text="3D cursor first, then + and click", icon='INFO')
        row = layout.row()
        row.template_list("BII_UL_plan_pairs", "", pg, "pairs", pg, "pair_index", rows=3)
        col = row.column(align=True)
        op = col.operator(PlanPickOperator.bl_idname, text="", icon='ADD')
        op.mode = 'ALIGN'
        op = col.operator(PlanRemoveOperator.bl_idname, text="", icon='REMOVE')
        op.kind = 'PAIR'
        col.separator()
        op = col.operator(PlanRemoveOperator.bl_idname, text="", icon='X')
        op.kind, op.clear = 'PAIR', True
        if len(pg.pairs):
            pair = pg.pairs[min(pg.pair_index, len(pg.pairs) - 1)]
            col = layout.column(align=True)
            col.prop(pair, "target", text="")
            col.operator(PlanPairTargetOperator.bl_idname, icon='PIVOT_CURSOR')
        layout.prop(pg, "fit_scale")
        layout.operator(PlanAlignOperator.bl_idname, icon='SNAP_ON')


classes = (PlanFindGridOperator, PlanFillFromGridOperator, PlanPickOperator, PlanOriginFromUmgebungOperator, PlanGeoreferenceOperator, PlanAlignOperator, PlanPairTargetOperator,
           PlanPointFromSceneOperator, PlanRefreshGuessesOperator, PlanRemoveOperator,
           BII_UL_plan_points, BII_UL_plan_pairs, VIEW3D_PT_bii_plan,
           VIEW3D_PT_bii_plan_georef, VIEW3D_PT_bii_plan_crs_help, VIEW3D_PT_bii_plan_align)


def register():
    global _draw_handle, _banner_handle
    for cls in classes:
        bpy.utils.register_class(cls)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW',
                                                          'POST_PIXEL')
    _banner_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_banner, (), 'WINDOW',
                                                            'POST_PIXEL')


def unregister():
    global _draw_handle, _banner_handle
    for handle in (_draw_handle, _banner_handle):
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _draw_handle = _banner_handle = None
    _picking.clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _pixel_cache.clear()

"""Place plans by Kilometrierung, and join neighbor sheets at their match lines.

Kilometrierung: DB Lagepläne without grid crosses still show the km of the
Strecke (hectometer labels 5,3 / 5,4 / 5,5) and landmarks with their km. Click
points on the plan track and type their km; the Kilometrierung lines of the
Umgebung extension give the scene position of each km. The fit keeps the
Maßstab and only turns the plan about the vertical axis and moves it (see
plan_km.fit_rigid). Points on the plan track without km pull the plan track
onto the scene track, Align to Scene points (3D cursor) count as landmarks
with a high weight.

Neighbor sheets: long Strecken come as several sheets that join at dashed
"Anschlussblatt" match lines. Import Sheets imports several files with the
same Maßstab side by side. Attach to Neighbor takes two clicks on this sheet
and two on the neighbor (the shared point where the track crosses the match
line, and a second point on the line) and snaps the sheet to its neighbor.
The attachment is kept, so placing one sheet places the ones attached to it.
"""

import math
import os
import re

import blf
import bpy
import gpu
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import import_plan, plan_georef, plan_km

UMGEBUNG_MARKER = "railway_chainage"  # set by BII Umgebung on everything it builds
KM_LINE_PREFIX = "KM-Linie "
TICKS_PREFIX = "KM-Marken"
TRACKS_COLLECTION_PREFIX = "Gleise "
NEAR_TRACK = 50.0  # meters a click may lie off the line to get a km predicted from it
RMS_WARNING = 2.0  # meters

_pending = {}  # object name -> list of (object name, sheet point) of an unfinished attach
_draw_handle = None


# ---------------------------------------------------------------- properties

def _redraw(self, context):
    import_plan._redraw(self, context)


def _km_edited(self, context):
    # A typed km is read from the plan. A whole landmark label also gives the Strecke.
    if self.km_guessed:
        self.km_guessed = False
    strecke, _ = plan_km.parse_label(self.km_text)
    obj = self.id_data
    if strecke and obj is not None and not obj.bii_plan_place.strecke:
        obj.bii_plan_place.strecke = strecke
    _redraw(self, context)


class KmPoint(bpy.types.PropertyGroup):
    """A point on the plan track, with the km printed there (or none)."""
    x: FloatProperty(name="Sheet X", subtype='DISTANCE', precision=5)
    y: FloatProperty(name="Sheet Y", subtype='DISTANCE', precision=5)
    km_text: StringProperty(
        name="km", update=_km_edited,
        description="Kilometrierung at this point as printed on the plan: 5,3 or 5,504 or "
        "13,4+23,05. A landmark label like 5240/5,504/1616 also works. Empty: the point "
        "only lies on the track")
    km_guessed: BoolProperty(
        name="km Guessed", update=_redraw,
        description="The km is predicted, not read from the plan. Until confirmed, the "
        "point only pulls the plan onto the track. Click to confirm it; typing a km does "
        "the same")
    use: BoolProperty(name="Use", default=True, update=_redraw,
                      description="Use this point for the fit")
    residual: FloatProperty(name="Residual", default=-1.0,
                            description="Distance to the fitted position, in meters")

    @property
    def km(self):
        """The km read from the plan, or None (empty, guessed or unreadable)."""
        if self.km_guessed:
            return None
        return plan_km.parse_label(self.km_text)[1]


def _is_other_plan(self, obj):
    return obj.bii_plan.is_plan and obj != self.id_data


class PlanPlaceSettings(bpy.types.PropertyGroup):
    strecke: StringProperty(
        name="Strecke", update=_redraw,
        description="Number of the Strecke (VzG), as in the plan header or in landmark labels "
        "like 5240/5,504. Empty: the only Strecke with a Kilometrierung line in the scene")
    km_points: CollectionProperty(type=KmPoint)
    km_point_index: IntProperty()
    cross_reference: EnumProperty(
        name="Track", default='AUTO',
        items=[('AUTO', "Auto", "Track axes of the Strecke if the scene has them, else its "
                "Kilometrierung line"),
               ('KM_LINE', "Kilometrierung Line", "Pull the plan track onto the "
                "Kilometrierung line (the axis of the Strecke)"),
               ('TRACKS', "Track Axes", "Pull the plan track onto the nearest track axis "
                "built by BII Umgebung (Gleisachsen)")],
        description="Scene lines the points on the plan track are pulled onto, across "
        "the track")
    landmark_weight: FloatProperty(
        name="Landmark Weight", default=10.0, min=0.0, soft_max=100.0,
        description="Weight of the Align to Scene points (landmarks at the 3D cursor) "
        "against km points. 0 leaves them out")
    attach_weight: FloatProperty(
        name="Neighbor Weight", default=4.0, min=0.0, soft_max=100.0,
        description="Weight of the match line to the neighbor sheet, so the sheets stay "
        "joined. 0 leaves it out")
    placed_by_km: BoolProperty(
        description="Placed by its own km points. Placing the neighbor does not move it")
    report: StringProperty(description="Result of the last fit, one line each; "
                           "warnings start with !")

    attach_parent: PointerProperty(
        type=bpy.types.Object, name="Neighbor", poll=_is_other_plan, update=_redraw,
        description="The neighbor sheet this one is attached to")
    attach_own: FloatVectorProperty(size=4, description="Match points on this sheet")
    attach_other: FloatVectorProperty(size=4, description="Match points on the neighbor")
    attach_shared: BoolProperty(
        name="Two Shared Points", update=_redraw,
        description="Both clicks on each sheet are the same places. Off: the first click is "
        "the shared point (where the track crosses the match line), the second any point "
        "further along the match line")


# ---------------------------------------------------------------- scene lines

def world_xy(obj, x, y):
    v = obj.matrix_world @ Vector((x, y, 0.0))
    return v.x, v.y


def curve_polylines(obj):
    """World XY polylines of a curve object, one per spline."""
    m = obj.matrix_world
    out = []
    for spline in obj.data.splines:
        if spline.type == 'BEZIER':
            cos = [p.co for p in spline.bezier_points]
        else:
            cos = [p.co.to_3d() for p in spline.points]
        pts = [(v.x, v.y) for v in (m @ c for c in cos)]
        if len(pts) >= 2:
            out.append(pts)
    return out


def strecke_of_label(label):
    """Strecke number at the start of an Umgebung route label ("5240  Augsburg")."""
    parts = label.split()
    return parts[0] if parts else ""


def km_line_objects(scene, strecke=""):
    lines = [o for o in scene.objects if o.type == 'CURVE' and o.get("chainage_source")
             and o.name.startswith(KM_LINE_PREFIX)]
    if strecke:
        lines = [o for o in lines if strecke_of_label(o.name[len(KM_LINE_PREFIX):]) == strecke]
    return lines


def scene_strecken(scene):
    return sorted({strecke_of_label(o.name[len(KM_LINE_PREFIX):]) for o in km_line_objects(scene)})


def _ticks(line_obj):
    """World XY of the tick marks of a Kilometrierung line (midpoints of their edges)."""
    out = []
    for coll in line_obj.users_collection:
        for o in coll.objects:
            if o.type != 'MESH' or not o.name.startswith(TICKS_PREFIX):
                continue
            m = o.matrix_world
            verts = [m @ v.co for v in o.data.vertices]
            for e in o.data.edges:
                a, b = verts[e.vertices[0]], verts[e.vertices[1]]
                out.append(((a.x + b.x) / 2, (a.y + b.y) / 2))
    return out


def km_line_from_object(obj):
    """plan_km.KmLine of an Umgebung Kilometrierung line, or None.

    Umgebung stores the km at both ends; the tick marks lie on round km and
    give the km in between (plan_km.ticks_to_anchors).
    """
    polylines = curve_polylines(obj)
    if not polylines or "chainage_km_start" not in obj or "chainage_km_end" not in obj:
        return None
    pts = polylines[0]
    anchors = plan_km.ticks_to_anchors(pts, float(obj["chainage_km_start"]),
                                       float(obj["chainage_km_end"]), _ticks(obj),
                                       float(obj.get("chainage_interval_m", 0.0)))
    return plan_km.KmLine.from_anchors(pts, anchors, obj.name)


def km_lines(scene, strecke):
    """KmLines of the Strecke; without Strecke those of the only one in the scene."""
    if not strecke:
        found = scene_strecken(scene)
        if len(found) != 1:
            return []
        strecke = found[0]
    return [line for line in (km_line_from_object(o) for o in km_line_objects(scene, strecke))
            if line is not None]


def track_polylines(scene, strecke):
    """Polylines of the Umgebung track axes (Gleisachsen) of a Strecke."""
    out = []
    for coll in bpy.data.collections:
        if not coll.name.startswith(TRACKS_COLLECTION_PREFIX):
            continue
        if strecke and strecke_of_label(coll.name[len(TRACKS_COLLECTION_PREFIX):]) != strecke:
            continue
        for o in coll.objects:
            if o.type == 'CURVE' and "track_number" in o and o.name in scene.objects:
                out += curve_polylines(o)
    return out


# ---------------------------------------------------------------- fit

def sheet_center(obj):
    """World XY of the middle of the plan's outline (its crop)."""
    m = obj.matrix_world
    verts = obj.data.vertices
    if not len(verts):
        return world_xy(obj, obj.bii_plan.width / 2, obj.bii_plan.height / 2)
    s = sum(((m @ v.co) for v in verts), Vector())
    return s.x / len(verts), s.y / len(verts)


def attach_observations(obj, weight):
    """Observations keeping a sheet joined to its neighbor at the match line."""
    place = obj.bii_plan_place
    parent = place.attach_parent
    if parent is None or weight <= 0:
        return []
    a, o = place.attach_own, place.attach_other
    p1, p2 = world_xy(obj, a[0], a[1]), world_xy(obj, a[2], a[3])
    q1, q2 = world_xy(parent, o[0], o[1]), world_xy(parent, o[2], o[3])
    obs = [plan_km.Observation('POINT', "Neighbor", p1, q1, weight=weight)]
    if place.attach_shared:
        obs.append(plan_km.Observation('POINT', "Neighbor", p2, q2, weight=weight))
    else:
        dx, dy = q2[0] - q1[0], q2[1] - q1[1]
        length = math.hypot(dx, dy)
        if length > 1e-6:
            obs.append(plan_km.Observation('DIR', "Neighbor", p2, q1, (-dy / length, dx / length),
                                           weight=weight))
    return obs


def build_observations(scene, obj):
    """(observations, km checks, messages); raises ValueError when nothing fits."""
    place = obj.bii_plan_place
    pg = obj.bii_plan
    lines = km_lines(scene, place.strecke)
    tracks = []
    if place.cross_reference in ('AUTO', 'TRACKS'):
        tracks = track_polylines(scene, place.strecke)
        if place.cross_reference == 'TRACKS' and not tracks:
            raise ValueError("No track axes of this Strecke in the scene: build them with BII "
                             "Umgebung, or set Track to Kilometrierung Line")
    cross = tracks or [line.points for line in lines]
    obs, checks, messages = [], [], []
    for p in place.km_points:
        if not p.use:
            continue
        wp = world_xy(obj, p.x, p.y)
        km = p.km
        if km is None:
            if p.km_text.strip() and not p.km_guessed:
                messages.append(f"!{p.name}: cannot read km \"{p.km_text}\", used as a "
                                "point on the track")
            if cross:
                obs.append(plan_km.Observation('LINE', p.name, wp, lines=cross))
            continue
        if not lines:
            name = place.strecke or "the Strecke"
            raise ValueError(f"No Kilometrierung line of {name} in the scene. Build it with BII "
                             "Umgebung, or set the Strecke")
        hit = plan_km.find_km(lines, km)
        if hit is None:
            lo = min(line.km_range[0] for line in lines)
            hi = max(line.km_range[1] for line in lines)
            messages.append(f"!{p.name}: km {plan_km.format_km(km)} is not on the "
                            f"Kilometrierung lines (km {plan_km.format_km(lo)} to "
                            f"{plan_km.format_km(hi)}), used as a point on the track")
            obs.append(plan_km.Observation('LINE', p.name, wp, lines=cross))
            continue
        _, pos, tangent = hit
        obs.append(plan_km.Observation('DIR', p.name, wp, pos, tangent))
        obs.append(plan_km.Observation('LINE', p.name, wp, lines=cross))
        checks.append((p.name, km, wp, pos))
    if place.landmark_weight > 0:
        for pair in pg.pairs:
            obs.append(plan_km.Observation('POINT', pair.name, world_xy(obj, pair.x, pair.y),
                                           (pair.target[0], pair.target[1]),
                                           weight=place.landmark_weight))
    obs += attach_observations(obj, place.attach_weight)
    if not any(o.kind in ('POINT', 'DIR') for o in obs):
        raise ValueError("Type the km of at least one point, or add an Align to Scene point "
                         "(landmark)")
    return obs, checks, messages


def delta_matrix(angle, pivot, shift):
    return (Matrix.Translation((pivot[0] + shift[0], pivot[1] + shift[1], 0.0))
            @ Matrix.Rotation(angle, 4, 'Z') @ Matrix.Translation((-pivot[0], -pivot[1], 0.0)))


def align_by_km(scene, obj):
    """Fit and move the plan. Returns (report lines, warnings present)."""
    place = obj.bii_plan_place
    pg = obj.bii_plan
    if pg.orientation != 'FLAT':
        raise ValueError("Only flat plans (Lagepläne) can be placed by Kilometrierung")
    obs, checks, lines = build_observations(scene, obj)
    fit = plan_km.fit_rigid(obs)
    if fit is None:
        raise ValueError("The points do not fix the plan")
    obj.matrix_world = delta_matrix(fit.angle, fit.pivot, fit.shift) @ obj.matrix_world

    by_name = {}
    for o in fit.observations:
        if o.residual >= 0:
            by_name[o.name] = math.hypot(by_name.get(o.name, 0.0), o.residual)
    for p in place.km_points:
        p.residual = by_name.get(p.name, -1.0) if p.use else -1.0
    for pair in pg.pairs:
        pair.residual = by_name.get(pair.name, -1.0)
    place.placed_by_km = True

    n_km = len(checks)
    n_track = sum(1 for p in place.km_points if p.use and p.km is None)
    n_land = len(pg.pairs) if place.landmark_weight > 0 else 0
    residuals = list(by_name.values())
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals)) if residuals else 0.0
    worst_name, worst = max(by_name.items(), key=lambda kv: kv[1]) if by_name else ("", 0.0)
    moved = math.hypot(*fit.shift)
    text = (f"{n_km} km points, {n_track} track points, {n_land} landmarks"
            + (", neighbor" if place.attach_parent and place.attach_weight > 0 else "")
            + f": turned {math.degrees(fit.angle):.2f}°, moved {moved:.1f} m")
    lines.insert(0, text)
    lines.insert(1, f"Residuals {rms:.2f} m RMS, largest {worst:.2f} m ({worst_name})")
    if 'ROTATION' in fit.weak:
        lines.append("!The rotation is not fixed: add a point farther along the track. The "
                     "plan keeps its rotation")
    if 'SHIFT' in fit.weak:
        lines.append("!The position along the track is not fixed: type the km of a point, "
                     "or add a landmark")
    if rms > RMS_WARNING:
        lines.append("!Large residuals: check the km values, the Strecke and the Maßstab")
    if worst > max(3.0, 3 * rms) and len(by_name) > 2:
        lines.append(f"!{worst_name} is {worst:.1f} m off: check its km")
    lines += ["!" + w for w in plan_km.spacing_warnings(checks)]
    place.report = "\n".join(lines)
    return lines


def predict_km(scene, obj, x, y):
    """km predicted for a new point on the plan track, or None.

    Once the plan is placed by km, from the Kilometrierung line under the
    point. Before, from two typed km points on this sheet (counting along
    the sheet at the Maßstab), else from the line if the plan lies near it.
    Rounded to the hectometer when it lies within 8 m of one.
    """
    place = obj.bii_plan_place

    def from_lines():
        lines = km_lines(scene, place.strecke)
        if not lines:
            return None
        wx, wy = world_xy(obj, x, y)
        km, d = min((line.km_at(wx, wy) for line in lines), key=lambda kd: kd[1])
        return km if d < NEAR_TRACK else None

    typed = [(p, p.km) for p in place.km_points if p.use and p.km is not None]
    km = from_lines() if place.placed_by_km else None
    if km is None and len(typed) >= 2:
        (pa, ka), (pb, kb) = max(((a, b) for a in typed for b in typed if a[0] != b[0]),
                                 key=lambda ab: (ab[0][0].x - ab[1][0].x) ** 2
                                 + (ab[0][0].y - ab[1][0].y) ** 2)
        dx, dy = pb.x - pa.x, pb.y - pa.y
        length2 = dx * dx + dy * dy
        if length2 > 1e-12 and abs(kb - ka) > 1e-9:
            t = ((x - pa.x) * dx + (y - pa.y) * dy) / length2
            km = ka + t * (kb - ka)
    if km is None:
        km = from_lines()
    if km is None:
        return None
    hecto = round(km * 10) / 10
    return hecto if abs(km - hecto) < 0.008 else round(km, 3)


# ---------------------------------------------------------------- sheets

def attach(obj):
    """Snap a sheet to its neighbor by the stored match points. Returns warnings."""
    place = obj.bii_plan_place
    parent = place.attach_parent
    if parent is None:
        raise ValueError(f"{obj.name} is not attached to a neighbor")
    warnings = []
    if abs(obj.scale[0] / parent.scale[0] - 1) > 1e-3:
        warnings.append(f"{obj.name} has Maßstab 1:{obj.scale[0]:.0f}, its neighbor "
                        f"1:{parent.scale[0]:.0f}")
    if obj.bii_plan.orientation != 'FLAT' or parent.bii_plan.orientation != 'FLAT':
        raise ValueError("Only flat plans (Lagepläne) can be attached")
    a, o = place.attach_own, place.attach_other
    own = (world_xy(obj, a[0], a[1]), world_xy(obj, a[2], a[3]))
    other = (world_xy(parent, o[0], o[1]), world_xy(parent, o[2], o[3]))
    angle, pivot, shift = plan_km.match_transform(own, other, place.attach_shared,
                                                  sheet_center(obj), sheet_center(parent))
    delta = delta_matrix(angle, pivot, shift)
    m = delta @ obj.matrix_world
    m.translation.z = parent.matrix_world.translation.z
    obj.matrix_world = m
    return warnings


def attached_sheets(scene, obj):
    return [o for o in scene.objects
            if o.bii_plan.is_plan and o.bii_plan_place.attach_parent == obj]


def place_chain(scene, root, include_refined=False):
    """Place the sheets attached to root, and the ones attached to them, and so on.

    Sheets placed by their own km points stay unless include_refined, and so
    do the ones attached to them. Returns (moved, kept, warnings).
    """
    moved, kept, warnings = [], [], []
    seen = {root.name}
    todo = [root]
    while todo:
        current = todo.pop(0)
        for child in attached_sheets(scene, current):
            if child.name in seen:
                continue
            seen.add(child.name)
            if child.bii_plan_place.placed_by_km and not include_refined:
                kept.append(child.name)
                continue
            try:
                warnings += attach(child)
            except ValueError as e:  # say an attached sheet was turned upright since
                warnings.append(f"{child.name} not moved: {e}")
                continue
            bpy.context.view_layer.update()
            moved.append(child.name)
            todo.append(child)
    return moved, kept, warnings


def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def lay_out_in_row(objs, location, gap):
    """Lay flat sheets left to right, centered on location, at their true size."""
    sizes = [(o.bii_plan.width * o.scale[0], o.bii_plan.height * o.scale[0]) for o in objs]
    offsets = plan_km.row_layout(sizes, gap)
    total = offsets[-1] + sizes[-1][0] if objs else 0.0
    for o, (w, h), dx in zip(objs, sizes, offsets):
        o.bii_plan.orientation = 'FLAT'
        o.rotation_euler = (0.0, 0.0, 0.0)
        o.location = (location[0] - total / 2 + dx, location[1] - h / 2, location[2])


# ---------------------------------------------------------------- operators

def _plan_poll(context):
    obj = context.object
    return obj is not None and obj.bii_plan.is_plan and obj.mode == 'OBJECT'


class PlanAlignKmOperator(bpy.types.Operator):
    """Turn and move the plan so its km points lie at their km on the Strecke, keeping the
    Maßstab. Points without km pull the plan track onto the scene track, Align to Scene
    points count as landmarks"""
    bl_idname = "bii_plan.align_km"
    bl_label = "Align by Kilometrierung"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _plan_poll(context)

    def execute(self, context):
        obj = context.object
        try:
            lines = align_by_km(context.scene, obj)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        context.view_layer.update()
        moved, kept, warnings = place_chain(context.scene, obj)
        if moved:
            lines.append(f"Attached sheets placed too: {', '.join(moved)}")
        if kept:
            lines.append(f"!Placed by their own km points, not moved: {', '.join(kept)}. "
                         "Align them again")
        lines += ["!" + w for w in warnings]
        obj.bii_plan_place.report = "\n".join(lines)
        has_warnings = any(line.startswith("!") for line in lines)
        self.report({'WARNING'} if has_warnings else {'INFO'},
                    ". ".join(line.lstrip("!") for line in lines))
        return {'FINISHED'}


class PlanPickKmOperator(bpy.types.Operator):
    """Click points on the plan track, then type the km printed there. Points without km
    pull the plan track onto the scene track"""
    bl_idname = "bii_plan.pick_km"
    bl_label = "Add km Points"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan and context.area
                and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        self.obj_name = context.object.name
        self.added = 0
        context.workspace.status_text_set("Click points on the plan track: hectometer "
                                          "marks, landmarks   Esc / Right click: done")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            context.workspace.status_text_set(None)
            return {'CANCELLED'}
        if event.type == 'RIGHTMOUSE' and event.shift:
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
            context.workspace.status_text_set(None)
            _redraw(None, context)
            return {'FINISHED'} if self.added else {'CANCELLED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if plan_georef.window_region(context.area, event.mouse_x, event.mouse_y) is None:
            return {'PASS_THROUGH'}  # the sidebar, so km can be typed meanwhile
        point = plan_georef.mouse_to_sheet(context.area, event, obj)
        if point is None:
            self.report({'WARNING'}, "Click on the sheet")
            return {'RUNNING_MODAL'}
        p = add_km_point(context.scene, obj, *point)
        if p.km_guessed:
            self.report({'INFO'}, f"{p.name}: km ≈ {p.km_text} predicted. Confirm it with ? "
                        "or type the km printed there")
        self.added += 1
        _redraw(None, context)
        return {'RUNNING_MODAL'}


def add_km_point(scene, obj, x, y):
    place = obj.bii_plan_place
    km = predict_km(scene, obj, x, y)
    p = place.km_points.add()
    p.name = f"K{max([int(q.name[1:]) for q in place.km_points if q.name[1:].isdigit()] + [0]) + 1}"
    p.x, p.y = x, y
    if km is not None:
        p.km_text = plan_km.format_km(km)
        p.km_guessed = True  # after the text: typing clears it
    place.km_point_index = len(place.km_points) - 1
    return p


class PlanKmRemoveOperator(bpy.types.Operator):
    """Remove the active km point"""
    bl_idname = "bii_plan.km_remove"
    bl_label = "Remove km Point"
    bl_options = {'REGISTER', 'UNDO'}

    clear: BoolProperty(name="All", description="Remove all km points", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.bii_plan.is_plan and len(obj.bii_plan_place.km_points)

    def execute(self, context):
        place = context.object.bii_plan_place
        if self.clear:
            place.km_points.clear()
        else:
            i = min(place.km_point_index, len(place.km_points) - 1)
            place.km_points.remove(i)
            place.km_point_index = max(0, i - 1)
        _redraw(None, context)
        return {'FINISHED'}


class PlanAttachOperator(bpy.types.Operator):
    """Snap this sheet to a neighbor at the match line (Anschlussblatt): click two points on
    this sheet, then the same two on the neighbor"""
    bl_idname = "bii_plan.attach"
    bl_label = "Attach to Neighbor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (_plan_poll(context) and context.object.bii_plan.orientation == 'FLAT'
                and context.area and context.area.type == 'VIEW_3D')

    def hint(self, context, shared):
        n = len(_pending.get(self.obj_name, []))
        if shared:
            texts = ["Click a point on this sheet that the neighbor shows too",
                     "Click a second shared point on this sheet, far from the first",
                     "Click the first point on the neighbor sheet",
                     "Click the second point on the neighbor sheet"]
        else:
            texts = ["Click where the track crosses the match line on this sheet",
                     "Click a second point on the match line of this sheet",
                     "Click the same crossing on the neighbor sheet",
                     "Click a second point on the neighbor's match line"]
        context.workspace.status_text_set(f"{texts[n]}   Esc: cancel")

    def invoke(self, context, event):
        self.obj_name = context.object.name
        _pending[self.obj_name] = []
        self.hint(context, context.object.bii_plan_place.attach_shared)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def finish(self, context):
        context.workspace.status_text_set(None)
        _pending.pop(self.obj_name, None)
        _redraw(None, context)

    def modal(self, context, event):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is None:
            self.finish(context)
            return {'CANCELLED'}
        if event.type == 'RIGHTMOUSE' and event.shift:
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self.finish(context)
            return {'CANCELLED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if plan_georef.window_region(context.area, event.mouse_x, event.mouse_y) is None:
            return {'PASS_THROUGH'}
        clicks = _pending.setdefault(self.obj_name, [])
        if len(clicks) < 2:
            hit = plan_georef.mouse_to_sheet(context.area, event, obj)
            if hit is None:
                self.report({'WARNING'}, f"Click on {obj.name}")
                return {'RUNNING_MODAL'}
            clicks.append((obj.name, hit))
        else:
            other = bpy.data.objects.get(clicks[2][0]) if len(clicks) == 3 else None
            candidates = [other] if other else [
                o for o in context.visible_objects
                if o.bii_plan.is_plan and o != obj and o.bii_plan.orientation == 'FLAT']
            hit = None
            for o in candidates:
                sheet_point = plan_georef.mouse_to_sheet(context.area, event, o)
                if sheet_point is not None:
                    hit = (o.name, sheet_point)
                    break
            if hit is None:
                self.report({'WARNING'}, "Click on the neighbor sheet" if other else
                            "Click on another plan sheet")
                return {'RUNNING_MODAL'}
            clicks.append(hit)
        _redraw(None, context)
        if len(clicks) < 4:
            self.hint(context, obj.bii_plan_place.attach_shared)
            return {'RUNNING_MODAL'}
        place = obj.bii_plan_place
        # Keep the new link only if the sheet can snap: a cancelled operator leaves
        # no undo step behind, so a failed attach must not change anything.
        old = (place.attach_parent, tuple(place.attach_own), tuple(place.attach_other),
               place.placed_by_km)
        place.attach_parent = bpy.data.objects[clicks[2][0]]
        place.attach_own = (*clicks[0][1], *clicks[1][1])
        place.attach_other = (*clicks[2][1], *clicks[3][1])
        place.placed_by_km = False
        self.finish(context)
        try:
            warnings = attach(obj)
        except ValueError as e:
            (place.attach_parent, place.attach_own, place.attach_other,
             place.placed_by_km) = old
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        context.view_layer.update()
        moved, kept, more = place_chain(context.scene, obj)
        text = f"{obj.name} attached to {place.attach_parent.name}"
        if moved:
            text += f"; moved along: {', '.join(moved)}"
        warnings += more
        self.report({'WARNING'} if warnings else {'INFO'}, ". ".join([text] + warnings))
        return {'FINISHED'}


class PlanSnapToNeighborOperator(bpy.types.Operator):
    """Snap this sheet to its neighbor again, by the stored match points"""
    bl_idname = "bii_plan.snap_to_neighbor"
    bl_label = "Snap to Neighbor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _plan_poll(context) and context.object.bii_plan_place.attach_parent is not None

    def execute(self, context):
        obj = context.object
        try:
            warnings = attach(obj)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        obj.bii_plan_place.placed_by_km = False
        context.view_layer.update()
        warnings += place_chain(context.scene, obj)[2]
        self.report({'WARNING'} if warnings else {'INFO'},
                    ". ".join([f"{obj.name} snapped to {obj.bii_plan_place.attach_parent.name}"]
                              + warnings))
        return {'FINISHED'}


class PlanDetachOperator(bpy.types.Operator):
    """Forget the neighbor of this sheet. The sheet stays where it is"""
    bl_idname = "bii_plan.detach"
    bl_label = "Detach"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None and obj.bii_plan.is_plan
                and obj.bii_plan_place.attach_parent is not None)

    def execute(self, context):
        context.object.bii_plan_place.attach_parent = None
        _redraw(None, context)
        return {'FINISHED'}


class PlanPlaceChainOperator(bpy.types.Operator):
    """Place the sheets attached to this one, and the ones attached to them, at their match
    lines"""
    bl_idname = "bii_plan.place_chain"
    bl_label = "Place Attached Sheets"
    bl_options = {'REGISTER', 'UNDO'}

    include_refined: BoolProperty(
        name="Also Sheets Placed by km", default=False,
        description="Also move the sheets placed by their own km points")

    @classmethod
    def poll(cls, context):
        return _plan_poll(context)

    def execute(self, context):
        moved, kept, warnings = place_chain(context.scene, context.object, self.include_refined)
        if not moved and not kept:
            self.report({'WARNING'}, "No sheet is attached to this one")
            return {'CANCELLED'}
        text = f"{len(moved)} sheets placed"
        if kept:
            text += (f"; placed by their own km points, not moved: {', '.join(kept)} "
                     "(see Also Sheets Placed by km)")
        self.report({'WARNING'} if warnings else {'INFO'}, ". ".join([text] + warnings))
        return {'FINISHED'}


class ImportPlanSheetsOperator(bpy.types.Operator, ImportHelper):
    """Import several plan sheets with the same Maßstab, side by side in name order"""
    bl_idname = "import_image.bii_plan_sheets"
    bl_label = "Import Plan Sheets"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(default="*.pdf;*.tif;*.tiff;*.png;*.jpg;*.jpeg;*.bmp",
                                options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    massstab: FloatProperty(name="Maßstab 1:", default=1000, min=1e-6, soft_min=1,
                            description="Scale printed on the sheets, the same for all")
    background: FloatProperty(name="Background", min=0, max=1, default=0, subtype='FACTOR',
                              description="Opacity of the white paper, see Import Plan")
    gap: FloatProperty(name="Gap", default=20.0, min=0.0, subtype='DISTANCE',
                       description="Space between the sheets in the scene")
    page: IntProperty(name="PDF Page", default=1, min=1, options={'SKIP_SAVE'})
    dpi: FloatProperty(name="PDF Resolution", default=0, min=0, soft_max=1200,
                       options={'SKIP_SAVE'},
                       description="Pixels per inch for PDF pages. 0: the resolution of the "
                       "scan in the PDF, 300 for vector drawings")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        for name in ("massstab", "background", "gap", "page", "dpi"):
            layout.prop(self, name)

    def execute(self, context):
        names = [f.name for f in self.files if f.name] or [os.path.basename(self.filepath)]
        folder = self.directory or os.path.dirname(self.filepath)
        paths = [os.path.join(folder, n) for n in sorted(names, key=natural_key)]
        objs, failed = [], []
        for path in paths:
            before = set(bpy.data.objects)
            try:
                result = bpy.ops.import_image.bii_plan(
                    filepath=path, massstab=self.massstab, orientation='FLAT',
                    background=self.background, page=self.page, dpi=self.dpi)
            except RuntimeError as e:
                failed.append(f"{os.path.basename(path)}: {e}")
                continue
            new = [o for o in bpy.data.objects if o not in before and o.bii_plan.is_plan]
            if 'FINISHED' not in result or not new:
                failed.append(os.path.basename(path))
                continue
            objs += new
        if not objs:
            self.report({'ERROR'}, "No sheet imported: " + "; ".join(failed))
            return {'CANCELLED'}
        lay_out_in_row(objs, context.scene.cursor.location, self.gap)
        for o in context.selected_objects:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        context.view_layer.objects.active = objs[0]
        text = (f"{len(objs)} sheets at 1:{self.massstab:.0f} side by side. Place the first, "
                "then Attach to Neighbor for each next one")
        if failed:
            text += ". Not imported: " + "; ".join(failed)
        self.report({'WARNING'} if failed else {'INFO'}, text)
        return {'FINISHED'}


# ---------------------------------------------------------------- overlay

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
    place = obj.bii_plan_place

    def to2d(o, x, y):
        return view3d_utils.location_3d_to_region_2d(region, rv3d,
                                                     o.matrix_world @ Vector((x, y, 0.0)))

    lines, labels = {}, []

    def segment(color, a, b):
        if a is not None and b is not None:
            lines.setdefault(color, []).extend((a, b))

    def marker(color, p, size=7, diamond=False):
        if p is None:
            return
        if diamond:
            pts = [p + Vector(d) for d in ((0, size), (size, 0), (0, -size), (-size, 0))]
            for a, b in zip(pts, pts[1:] + pts[:1]):
                segment(color, a, b)
        else:
            segment(color, p + Vector((-size, -size)), p + Vector((size, size)))
            segment(color, p + Vector((-size, size)), p + Vector((size, -size)))

    for p in place.km_points:
        if not p.use:
            color = (0.6, 0.6, 0.6, 1.0)
        elif p.km_guessed or p.km is None:
            color = (0.5, 0.8, 1.0, 1.0)
        elif p.residual > RMS_WARNING:
            color = (1.0, 0.2, 0.2, 1.0)
        else:
            color = (0.3, 1.0, 0.4, 1.0)
        pos = to2d(obj, p.x, p.y)
        marker(color, pos, diamond=True)
        if pos is not None:
            km = ("≈" if p.km_guessed else "") + p.km_text if p.km_text.strip() else "track"
            text = f"{p.name}  {km}" + ("" if p.residual < 0 else f"  {p.residual:.2f} m")
            labels.append((pos + Vector((9, 6)), text, color))

    magenta = (1.0, 0.3, 1.0, 1.0)
    parent = place.attach_parent
    if parent is not None and parent.name in context.view_layer.objects:
        a, o = place.attach_own, place.attach_other
        own = [to2d(obj, a[0], a[1]), to2d(obj, a[2], a[3])]
        other = [to2d(parent, o[0], o[1]), to2d(parent, o[2], o[3])]
        segment(magenta, *own)
        segment(magenta, *other)
        for p, q in zip(own, other):
            marker(magenta, p, 6)
            marker(magenta, q, 4)
        if own[0] is not None:
            labels.append((own[0] + Vector((9, -14)), f"→ {parent.name}", magenta))

    for name, point in _pending.get(obj.name, []):
        o = bpy.data.objects.get(name)
        if o is not None:
            marker((1.0, 1.0, 1.0, 1.0), to2d(o, *point), 10)

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


# ---------------------------------------------------------------- panels

class BII_UL_plan_km_points(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        if item.km_text.strip():
            km = ("≈" if item.km_guessed else "") + item.km_text
        else:
            km = "on track"
        row.label(text=f"{item.name}   {km}")
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text="" if item.residual < 0 else f"{item.residual:.2f} m")


def _report(layout, context, text):
    for line in text.splitlines():
        if line.startswith("!"):
            plan_georef.wrapped_label(layout, context, line[1:], 'ERROR')
        else:
            plan_georef.wrapped_label(layout, context, line)


class VIEW3D_PT_bii_plan_km(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Import"
    bl_parent_id = "VIEW3D_PT_bii_plan"
    bl_label = "Kilometrierung"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return plan_georef._plan(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        place = obj.bii_plan_place
        layout.prop(place, "strecke")
        found = scene_strecken(context.scene)
        lines = km_line_objects(context.scene, place.strecke or (found[0] if len(found) == 1
                                                                 else "__none__"))
        if lines:
            lo = min(min(o["chainage_km_start"], o["chainage_km_end"]) for o in lines)
            hi = max(max(o["chainage_km_start"], o["chainage_km_end"]) for o in lines)
            layout.label(text=f"{len(lines)} Kilometrierung lines, km "
                         f"{plan_km.format_km(lo)} to {plan_km.format_km(hi)}", icon='CHECKMARK')
        elif found and not place.strecke:
            plan_georef.wrapped_label(layout, context, "Several Strecken in the scene ("
                                      + ", ".join(found) + "): set the Strecke", 'ERROR')
        else:
            plan_georef.wrapped_label(layout, context, "No Kilometrierung line of this "
                                      "Strecke in the scene: build it with BII Umgebung", 'ERROR')
        layout.prop(place, "cross_reference")
        row = layout.row()
        row.template_list("BII_UL_plan_km_points", "", place, "km_points", place,
                          "km_point_index", rows=4)
        col = row.column(align=True)
        col.operator(PlanPickKmOperator.bl_idname, text="", icon='ADD')
        col.operator(PlanKmRemoveOperator.bl_idname, text="", icon='REMOVE')
        col.separator()
        col.operator(PlanKmRemoveOperator.bl_idname, text="", icon='X').clear = True
        if len(place.km_points):
            point = place.km_points[min(place.km_point_index, len(place.km_points) - 1)]
            row = layout.row(align=True)
            row.prop(point, "km_text")
            sub = row.row(align=True)
            sub.enabled = bool(point.km_text.strip())
            sub.prop(point, "km_guessed", text="", icon='QUESTION', toggle=True)
            if point.km_text.strip() and not point.km_guessed and point.km is None:
                layout.label(text="Cannot read this km", icon='ERROR')
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        n_pairs = len(obj.bii_plan.pairs)
        col.prop(place, "landmark_weight", text=f"Landmarks ({n_pairs})")
        if place.attach_parent is not None:
            col.prop(place, "attach_weight")
        layout.operator(PlanAlignKmOperator.bl_idname, icon='CURVE_PATH')
        if place.report:
            _report(layout.column(align=True), context, place.report)
        elif not len(place.km_points):
            plan_georef.wrapped_label(
                layout, context, "Move the plan roughly into place. Click + and then "
                "hectometer marks on the plan track; type their km. Two km points far apart "
                "fix the plan; more points and landmarks (Align to Scene) make it better",
                'INFO')


class VIEW3D_PT_bii_plan_sheets(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Import"
    bl_parent_id = "VIEW3D_PT_bii_plan"
    bl_label = "Neighbor Sheets"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return plan_georef._plan(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        place = obj.bii_plan_place
        layout.operator(ImportPlanSheetsOperator.bl_idname, icon='IMPORT')
        if place.attach_parent is not None:
            row = layout.row(align=True)
            row.label(text=f"Attached to {place.attach_parent.name}", icon='LINKED')
            row.operator(PlanDetachOperator.bl_idname, text="", icon='UNLINKED')
        layout.prop(place, "attach_shared")
        row = layout.row(align=True)
        row.operator(PlanAttachOperator.bl_idname, icon='SNAP_EDGE')
        row.operator(PlanSnapToNeighborOperator.bl_idname, text="", icon='FILE_REFRESH')
        children = attached_sheets(context.scene, obj)
        if children:
            layout.label(text="Attached here: " + ", ".join(o.name for o in children))
            layout.operator(PlanPlaceChainOperator.bl_idname, icon='LINKED')
        plan_georef.wrapped_label(
            layout, context, "Attach: click where the track crosses the match line "
            "(Anschlussblatt) on this sheet, then a second point on that line; then the "
            "same two on the neighbor", 'INFO')


def menu_func_import(self, context):
    self.layout.operator(ImportPlanSheetsOperator.bl_idname,
                         text="Plan Sheets (several, side by side)")


classes = (KmPoint, PlanPlaceSettings, PlanAlignKmOperator, PlanPickKmOperator,
           PlanKmRemoveOperator, PlanAttachOperator, PlanSnapToNeighborOperator,
           PlanDetachOperator, PlanPlaceChainOperator, ImportPlanSheetsOperator,
           BII_UL_plan_km_points, VIEW3D_PT_bii_plan_km, VIEW3D_PT_bii_plan_sheets)


def register():
    global _draw_handle
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.bii_plan_place = PointerProperty(type=PlanPlaceSettings)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW',
                                                          'POST_PIXEL')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    del bpy.types.Object.bii_plan_place
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _pending.clear()

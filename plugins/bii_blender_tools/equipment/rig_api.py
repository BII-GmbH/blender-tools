"""Equipment rigs for scripts and AI agents (for example through the Blender MCP).

    from bl_ext.user_default.bii_blender_tools.equipment import rig_api as rig

    root = rig.make_rig("Crane")                        # an object becomes the rig root
    rig.set_joint("Tower", "REVOLUTE", axis="Z")         # unlimited hinge about local Z
    rig.set_joint("Boom", "REVOLUTE", axis="X", limits=(-10, 80))   # degrees
    rig.set_joint("Trolley", "PRISMATIC", axis="Y", limits=(0, 12)) # meters
    rig.set_pivot("Boom", (0, 1.5, 22))                 # joints turn about the object origin
    rig.preview("Crane", {"Tower": 45, "Boom": 30, "Trolley": 6})
    rig.describe("Crane")                               # the whole rig as a dict
    rig.export("Crane", r"C:\\work\\crane.dequip")       # write the dProB equipment
    rig.verify("Crane")                                 # compare the file with Blender

Units: angles in degrees, lengths in meters (Blender units with the scene's unit scale 1),
vectors in the object's local axes unless the name says world. Objects can be given as objects
or names. Errors raise rig_props.RigError (or ValueError) with a message for the user.
"""

import math

import bpy
from mathutils import Euler, Matrix, Vector

from . import dequip_format, ik_chain, rig_export, rig_preview, rig_props
from .rig_props import RigError

JOINT_TYPES = dequip_format.JOINT_TYPES


def _obj(o):
    if isinstance(o, str):
        found = bpy.data.objects.get(o)
        if found is None:
            raise RigError(f"no object named {o!r}")
        return found
    if o is None:
        raise RigError("no object given")
    return o


def _root(o):
    obj = _obj(o)
    root = rig_props.rig_root(obj)
    if root is None:
        raise RigError(f"{obj.name} is not part of a rig; make_rig() its top object first")
    return root


# ------------------------------------------------------------------ rig

_FORWARD = {"+Y": "POS_Y", "-Y": "NEG_Y", "+X": "POS_X", "-X": "NEG_X", "Y": "POS_Y", "X": "POS_X"}


def make_rig(root, export_path=None, forward=None):
    """Makes the object the root of a rig: it and its descendants become the equipment.

    The root's origin becomes the equipment's origin (put it on the ground, under the slewing
    center for a crane). forward: where the model's front points in Blender, "+Y" (default),
    "-Y", "+X" or "-X"; the export turns the equipment so that this is dProB's forward (+Z),
    and Blender's +Z is dProB's up (+Y). The root's own rotation is part of the model.
    """
    obj = _obj(root)
    if not rig_props.is_rig_object(obj):
        raise RigError(f"{obj.name} is a {obj.type.lower()}; a rig root must be a mesh, empty or curve")
    above = rig_props.rig_root(obj.parent) if obj.parent else None
    if above is not None:
        raise RigError(f"{obj.name} is already inside the rig of {above.name}")
    for o in obj.children_recursive:
        if o.bii_rig.is_root:
            o.bii_rig.is_root = False
    obj.bii_rig.is_root = True
    if export_path:
        obj.bii_rig.export_path = export_path
    if forward is not None:
        set_forward(obj, forward)
    rig_props.ensure_ids(obj)
    return obj


def set_forward(root, forward):
    """Where the model's front points in Blender: "+Y", "-Y", "+X" or "-X" (see make_rig)."""
    key = _FORWARD.get(str(forward).upper().replace(" ", ""))
    if key is None:
        raise ValueError(f"forward {forward!r}: one of +Y, -Y, +X, -X")
    _root(root).bii_rig.forward = key
    return forward


def remove_rig(root):
    """The object is no rig root any more; the joint settings stay on the objects."""
    obj = _root(root)
    rig_preview.reset(obj)
    obj.bii_rig.is_root = False


def rigs():
    """Names of all rig roots in the file."""
    return [o.name for o in rig_props.rig_roots()]


def rig_of(obj):
    """Name of the rig root an object belongs to, or None."""
    root = rig_props.rig_root(_obj(obj))
    return root.name if root else None


# ------------------------------------------------------------------ joints

def _axis(j, axis, obj=None, world=False):
    if world:
        v = Vector(axis)
        if v.length < 1e-9:
            raise ValueError("the axis vector is zero")
        # the object's own axes: its rotation without scale, in the rest pose
        rot = (rig_props.parent_space(obj).to_3x3() @ rig_props.rest_rotation(obj)).normalized()
        j.custom_axis = (rot.inverted() @ v).normalized()
        j.axis = "CUSTOM"
        return
    if isinstance(axis, str):
        key = axis.upper().replace("-", "NEG_")
        if key not in rig_props.AXIS_VECTORS:
            raise ValueError(f"axis {axis!r}: one of X, Y, Z, -X, -Y, -Z or a vector")
        j.axis = key
    else:
        v = Vector(axis)
        if v.length < 1e-9:
            raise ValueError("the axis vector is zero")
        j.custom_axis = v.normalized()
        j.axis = "CUSTOM"


def set_joint(obj, joint_type, axis=None, limits="keep", visible=None, attachment_point=None, axis_world=None):
    """Sets the joint of a rig object.

    joint_type: FIXED, REVOLUTE, PRISMATIC or BALL.
    axis: "X", "Y", "Z", "-X", "-Y", "-Z" or a vector, in the object's local axes (its rotation,
        without scale). The axis goes through the object's origin (see set_pivot).
    axis_world: instead of axis, a direction in world space (taken in the rest pose), for
        rotated parts whose axis is no local axis.
    limits: (min, max) in degrees for REVOLUTE, in meters for PRISMATIC; None makes a revolute
        joint unlimited; "keep" leaves them.
    visible: whether dProB shows the part's geometry. attachment_point: whether other objects can
        be attached to the part in dProB.
    Returns describe_joint(obj).
    """
    o = _obj(obj)
    if rig_props.rig_root(o) is None:
        raise RigError(f"{o.name} is not part of a rig")
    jt = joint_type.upper()
    if jt not in JOINT_TYPES:
        raise ValueError(f"joint type {joint_type!r}: one of {JOINT_TYPES}")
    j = o.bii_rig
    j.joint_type = jt
    if axis_world is not None:
        _axis(j, axis_world, o, world=True)
    elif axis is not None:
        _axis(j, axis)
    if limits != "keep":
        if jt == "REVOLUTE":
            if limits is None:
                j.use_limits = False
            else:
                lo, hi = (math.radians(v) for v in limits)
                if lo > hi:
                    raise ValueError("limits: min is larger than max")
                j.angle_min, j.angle_max, j.use_limits = lo, hi, True
        elif jt == "PRISMATIC":
            if limits is None:
                raise ValueError("a prismatic joint needs limits (min, max) in meters")
            lo, hi = (v / _unit_scale() for v in limits)
            if lo > hi:
                raise ValueError("limits: min is larger than max")
            j.length_min, j.length_max = lo, hi
        elif limits is not None:
            raise ValueError(f"a {jt.lower()} joint has no limits")
    if visible is not None:
        j.visible = bool(visible)
    if attachment_point is not None:
        j.attachment_point = bool(attachment_point)
    rig_props.apply_preview(o)
    return describe_joint(o)


def clear_joint(obj):
    """Makes the object's joint FIXED (and puts it back to its rest pose)."""
    o = _obj(obj)
    o.bii_rig.joint_type = "FIXED"
    rig_props.clear_preview(o)


def _unit_scale():
    return bpy.context.scene.unit_settings.scale_length or 1.0


def _rest_world_full(o, worlds=None):
    """World matrix of o with every joint of its rig at rest (also when its parents are posed).
    worlds: ik_chain.rest_worlds of the rig, to reuse."""
    if worlds is not None and o in worlds:
        return worlds[o]
    root = rig_props.rig_root(o)
    if root is None:
        return rig_export._np(o.matrix_world)
    return ik_chain.rest_worlds(root)[o]


def describe_joint(obj, _worlds=None):
    """Joint of an object as a dict (degrees, meters; the axis also in world space)."""
    o = _obj(obj)
    j = o.bii_rig
    d = {"object": o.name, "joint": j.joint_type, "visible": j.visible,
         "attachment_point": j.attachment_point, "element_id": j.element_id,
         "pivot_world": tuple(round(float(v), 6) for v in _rest_world_full(o, _worlds)[:3, 3])}
    if j.joint_type in ("REVOLUTE", "PRISMATIC"):
        d["axis"] = j.axis.replace("NEG_", "-") if j.axis != "CUSTOM" else tuple(j.custom_axis)
        try:
            d["axis_world"] = tuple(round(v, 6) for v in rig_props.world_axis(o))
        except RigError as e:
            d["axis_error"] = str(e)
    if j.joint_type == "REVOLUTE":
        d["limits_deg"] = (math.degrees(j.angle_min), math.degrees(j.angle_max)) if j.use_limits else None
        d["preview_deg"] = math.degrees(j.preview_angle)
    elif j.joint_type == "PRISMATIC":
        d["limits_m"] = (j.length_min * _unit_scale(), j.length_max * _unit_scale())
        d["preview_m"] = j.preview_length * _unit_scale()
    elif j.joint_type == "BALL":
        d["preview_deg"] = tuple(math.degrees(v) for v in j.preview_ball)
    if j.joint_type in ("REVOLUTE", "PRISMATIC"):
        d["drive"] = j.drive
        if j.drive == "FOLLOW":
            d["follow"] = [(t.leader.name if t.leader else None, t.factor) for t in j.drive_terms]
            off = rig_props.drive_offset(j)
            d["follow_offset"] = math.degrees(off) if j.joint_type == "REVOLUTE" else off * _unit_scale()
    if j.ik_target:
        d["ik"] = {"name": j.ik_name or f"Move {o.name}", "root_joint": j.ik_root.name if j.ik_root else None,
                   "iterations": j.ik_iterations, "passes": j.ik_loops,
                   "orientation_weight": j.ik_orientation_weight, "seed_margin": j.ik_seed_margin,
                   "capability_id": j.ik_capability_id}
    return d


def describe(root):
    """The rig as a dict: its parts as a tree with their joints, and its problems."""
    r = _root(root)

    all_kids = rig_props.children_map()
    worlds = ik_chain.rest_worlds(r)

    def node(o):
        d = describe_joint(o, worlds)
        d["type"] = o.type
        kids = [c for c in all_kids.get(o, ()) if rig_props.is_rig_object(c)]
        if kids:
            d["children"] = [node(c) for c in kids]
        return d

    return {"root": r.name, "equipment_id": r.bii_rig.equipment_id, "export_path": r.bii_rig.export_path,
            "forward": {"POS_Y": "+Y", "NEG_Y": "-Y", "POS_X": "+X", "NEG_X": "-X"}[r.bii_rig.forward],
            "crane_hook": r.bii_rig.crane_hook.name if r.bii_rig.crane_hook else None,
            "keep_capabilities": r.bii_rig.keep_capabilities,
            "parts": len(rig_props.rig_objects(r)), "joints": [o.name for o in rig_props.joints(r)],
            "tree": node(r), "problems": [f"{s}: {t}" for s, t in rig_export.problems(r)]}


# ------------------------------------------------------------------ drive (couplings)

def _jointed(obj):
    o = _obj(obj)
    if rig_props.rig_root(o) is None:
        raise RigError(f"{o.name} is not part of a rig")
    if o.bii_rig.joint_type not in ("REVOLUTE", "PRISMATIC"):
        raise RigError(f"{o.name} is not a revolute or prismatic joint")
    return o


def _check_drive(o, restore):
    root = rig_props.rig_root(o)
    errors = rig_props.drive_problems(o)
    if not errors:
        try:
            rig_props.followers(root)
        except RigError as e:
            errors = [str(e)]
    if errors:
        restore()
        raise RigError("; ".join(errors))
    rig_props.update_followers(root)
    bpy.context.view_layer.update()


def _save_drive(o):
    j = o.bii_rig
    saved = (j.drive, [(t.leader, t.factor) for t in j.drive_terms], j.drive_offset_angle, j.drive_offset_length)

    def restore():
        j.drive_terms.clear()
        for leader, factor in saved[1]:
            t = j.drive_terms.add()
            t.leader, t.factor = leader, factor
        j.drive_offset_angle, j.drive_offset_length = saved[2], saved[3]
        j.drive = saved[0]
    return restore


def set_follow(obj, leaders, offset=0.0):
    """The joint follows other joints: value = offset + sum(factor * leader value).

    leaders: [(object, factor)]; both joints revolute or both prismatic. Empty: a constant.
    offset: degrees (revolute) or meters (prismatic).
    Example, a pitch reversal that keeps the hook level: set_follow("Hook Rot", [("Boom", 1)])
    (with opposite axes; use -1 if both turn the same way). IK capabilities set it after each pass.
    """
    o = _jointed(obj)
    restore = _save_drive(o)
    j = o.bii_rig
    j.drive_terms.clear()
    for leader, factor in leaders:
        t = j.drive_terms.add()
        t.leader = _obj(leader)
        t.factor = float(factor)
    if j.joint_type == "REVOLUTE":
        j.drive_offset_angle = math.radians(offset)
    else:
        j.drive_offset_length = offset / _unit_scale()
    j.drive = "FOLLOW"
    _check_drive(o, restore)
    return describe_joint(o)


def fit_follow_aim(part, leader, target, samples=9, value_range=None, exact_at="rest"):
    """Makes a revolute part follow a leader so that it keeps pointing at a target point that
    moves with the leader: a hydraulic cylinder that points at its piston's eye on the boom, or
    the piston that points back at the cylinder's base pin.

    part: revolute joint (its axis and pivot set). leader: revolute joint. target:
    an object whose origin the part should point at (an empty at the other pin). Samples the
    leader over its limits (or value_range in degrees), works out the angle the part needs
    at each, fits value = factor * leader + offset and sets that Follow. exact_at: a leader value
    (degrees) where the fit is exact, by default "rest" (0, where the machine mostly stands, or
    the lower limit when 0 is outside the range); None fits the whole range evenly. Returns factor, offset
    (degrees) and the worst angle error over the samples (degrees): the pointing is not linear,
    so report the error to the user when it is noticeable.
    """
    p, l, t = _obj(part), _obj(leader), _obj(target)
    if p.bii_rig.joint_type != "REVOLUTE":
        raise RigError(f"{p.name} is not a revolute joint")
    lj = l.bii_rig
    if lj.joint_type != "REVOLUTE":
        raise RigError(f"{l.name} is not a revolute joint (followers follow joints of their own type)")
    root = rig_props.rig_root(p)
    if value_range is not None:
        lo, hi = math.radians(value_range[0]), math.radians(value_range[1])
    else:
        lim = rig_props.limits(lj)
        if lim is None:
            raise RigError(f"{l.name} has no limits; pass value_range")
        lo, hi = lim
    saved = {j: rig_props.preview_value(j.bii_rig) for j in rig_props.joints(root)}
    old_drive = p.bii_rig.drive
    p.bii_rig.drive = "FREE"
    try:
        rig_preview.reset(root)
        bpy.context.view_layer.update()
        rest = p.matrix_world.copy()
        rest_rot = rest.to_3x3().normalized()
        zero_dir = rest_rot.inverted() @ (t.matrix_world.translation - rest.translation)   # in part axes
        axis_local = rig_props.local_axis(p.bii_rig)
        xs, ys = [], []
        prev = 0.0
        if exact_at == "rest":
            v0 = 0.0 if lo <= 0.0 <= hi else lo
        elif exact_at is None:
            v0 = None
        else:
            v0 = math.radians(exact_at)
        values = [lo + (hi - lo) * i / max(samples - 1, 1) for i in range(samples)]
        if v0 is not None and all(abs(v - v0) > 1e-9 for v in values):
            values = sorted(values + [v0])
        for v in values:
            rig_props.set_preview_value(l, v, followers=False)
            rig_props.set_preview_value(p, 0.0, followers=False)
            bpy.context.view_layer.update()
            m = p.matrix_world
            rot = m.to_3x3().normalized()
            want = rot.inverted() @ (t.matrix_world.translation - m.translation)
            # angle about the part's axis from where it points (at 0) to the target
            a = zero_dir - axis_local * zero_dir.dot(axis_local)
            b = want - axis_local * want.dot(axis_local)
            ang = math.atan2(axis_local.dot(a.cross(b)), a.dot(b))
            while ang - prev > math.pi:          # keep the samples continuous
                ang -= 2 * math.pi
            while ang - prev < -math.pi:
                ang += 2 * math.pi
            prev = ang
            xs.append(v)
            ys.append(ang)
        n = len(xs)
        if v0 is None:
            mx, my = sum(xs) / n, sum(ys) / n
        else:                                   # the line through the sample at v0
            k = min(range(n), key=lambda i: abs(xs[i] - v0))
            mx, my = xs[k], ys[k]
        sxx = sum((x - mx) ** 2 for x in xs)
        factor = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 1e-12 else 0.0
        offset = my - factor * mx
        worst = max(abs(y - (factor * x + offset)) for x, y in zip(xs, ys))
    finally:
        p.bii_rig.drive = old_drive
        rig_preview.reset(root)
        for j, v in saved.items():
            rig_props.set_preview_value(j, v, followers=False)
        rig_props.update_followers(root)
        bpy.context.view_layer.update()
    set_follow(p, [(l, factor)], offset=math.degrees(offset))
    return {"factor": factor, "offset_deg": math.degrees(offset), "worst_error_deg": math.degrees(worst),
            "samples": n}


def set_hold(obj):
    """IK capabilities keep the joint at the value it has when they start (it does not move)."""
    o = _jointed(obj)
    o.bii_rig.drive = "HOLD"
    return describe_joint(o)


def set_free(obj):
    """The joint is set by hand or by IK again (no Follow or Hold)."""
    o = _jointed(obj)
    o.bii_rig.drive = "FREE"
    return describe_joint(o)


# ------------------------------------------------------------------ IK

def set_ik_target(obj, enabled=True, name=None, root_joint="keep", iterations=None, loops=None,
                  orientation_weight=None, description=None, seed_margin=None):
    """Exports an IK capability for the part: "move it to a pose" with dProB's IK node.

    name: capability name (default "Move <object>"). root_joint: lowest joint the IK may move
    (None: all down to the rig root). A fixed part becomes an attachment point (the capability
    reads its offset from there). iterations: IK steps per pass. loops: passes when joints
    follow or hold (after each pass they are set). orientation_weight: 0 = position only; dProB
    measures the orientation error in degrees, so 0.001 to 0.02 are usual (above about 0.03 it
    overshoots and does not settle).
    seed_margin: the IK starts with limited joints at least this share of their range inside
    their limits (default 0.1); joints resting at a limit often stall the solver.
    """
    o = _obj(obj)
    if rig_props.rig_root(o) is None:
        raise RigError(f"{o.name} is not part of a rig")
    j = o.bii_rig
    j.ik_target = bool(enabled)
    if name is not None:
        j.ik_name = name
    if description is not None:
        j.ik_description = description
    if root_joint != "keep":
        j.ik_root = _obj(root_joint) if root_joint is not None else None
    if iterations is not None:
        j.ik_iterations = int(iterations)
    if loops is not None:
        j.ik_loops = int(loops)
    if orientation_weight is not None:
        j.ik_orientation_weight = float(orientation_weight)
    if seed_margin is not None:
        j.ik_seed_margin = float(seed_margin)
    if enabled:
        if j.joint_type == "FIXED":
            j.attachment_point = True       # dProB reads a fixed target's pose from its attachment point
        rig_props.ensure_ids(rig_props.rig_root(o))
        ik_chain.Setup(o)       # raises if no joint moves it
    return describe_joint(o)


def set_crane_hook(root, hook):
    """Makes the equipment a dProB Crane whose hook is `hook` (None: no Crane type).

    The hook becomes an attachment point with an IK capability (see set_ik_target). The export
    then adds the Crane type: Move Arm invokes the hook's IK capability with the destination
    relative to the equipment, Lift and Deliver are dProB's own implementations on this hook,
    and the state variables Current Cargo and Current Cargo Hook Offset are added.
    """
    r = _root(root)
    if hook is None:
        r.bii_rig.crane_hook = None
        return None
    h = _obj(hook)
    if rig_props.rig_root(h) is not r:
        raise RigError(f"{h.name} is not part of the rig of {r.name}")
    h.bii_rig.attachment_point = True
    if not h.bii_rig.ik_target:
        set_ik_target(h)
    r.bii_rig.crane_hook = h
    return h.name


def ik_targets(root):
    """Names of the rig's parts that get an IK capability."""
    return [o.name for o in rig_props.rig_objects(_root(root)) if o.bii_rig.ik_target]


def solve_ik(obj, position_world=None, rotation_world=None, matrix_world=None, apply=True):
    """Solves the IK of a part as its generated capability would in dProB, from the current
    preview values (the equipment's state), and shows the result (apply=True).

    The target: matrix_world (4x4), or position_world (x, y, z) and rotation_world (Euler XYZ
    degrees or a 3x3 matrix). Without a rotation it solves for the position only, as the
    capability's Position Only input: the part keeps its rotation, turned with the slewing.
    Returns the joint values (degrees / meters), the remaining errors, and the destination as
    dProB gets it (the capability's Destination input: position in meters, rotation x, y, z, w).
    """
    o = _obj(obj)
    setup = ik_chain.Setup(o)
    current = Matrix([list(r) for r in setup.current_world()])
    if matrix_world is not None:
        target = Matrix(matrix_world)
    else:
        loc, rot = current.to_translation(), current.to_3x3()
        if position_world is not None:
            loc = Vector(position_world)
        if rotation_world is not None:
            rot = (Matrix(rotation_world) if len(rotation_world) == 3 and hasattr(rotation_world[0], "__len__")
                   else Euler([math.radians(v) for v in rotation_world]).to_matrix())
        target = Matrix.LocRotScale(loc, rot, None)
    dest = setup.destination(rig_export._np(target))
    # no rotation given: as the capability's Position Only (the part keeps its rotation)
    values, pos_err, rot_err = setup.solve(dest, position_only=matrix_world is None and rotation_world is None)
    preview_values = setup.preview_values(values)
    if apply:
        for ob, v in preview_values.items():
            rig_props.set_preview_value(ob, v, followers=False)
        bpy.context.view_layer.update()
    pos, quat = ik_chain.pose_tuple(dest)
    out = {}
    for ob, v in preview_values.items():
        kind = ob.bii_rig.joint_type
        out[ob.name] = (math.degrees(v) if kind == "REVOLUTE" else v * _unit_scale() if kind == "PRISMATIC"
                        else tuple(math.degrees(a) for a in v))
    return {"values": out, "position_error_m": pos_err, "orientation_error_deg": rot_err,
            "destination": {"position": pos, "rotation": quat}}


def ik_destination(obj):
    """The part's current pose as an IK capability's Destination (dProB, relative to the equipment)."""
    o = _obj(obj)
    setup = ik_chain.Setup(o)
    pos, quat = ik_chain.pose_tuple(setup.destination(setup.current_world()))
    return {"position": pos, "rotation": quat}


# ------------------------------------------------------------------ pivot

def set_pivot(obj, point_world):
    """Moves the object's origin (the joint's pivot) to a world point. Its geometry and its
    children stay where they are. A mesh or curve shared with other objects is made single-user."""
    o = _obj(obj)
    target = Vector(point_world)
    root = rig_props.rig_root(o)
    saved = {}
    if root is not None:   # work in the rest pose, else the children would keep a previewed pose
        saved = {j: rig_props.preview_value(j.bii_rig) for j in rig_props.joints(root)}
        rig_preview.reset(root)
    bpy.context.view_layer.update()
    kids = {c: c.matrix_world.copy() for c in o.children}
    mw = o.matrix_world.copy()
    local = mw.inverted() @ target
    if o.data is not None and hasattr(o.data, "transform"):
        if o.data.users > 1:
            o.data = o.data.copy()
        o.data.transform(Matrix.Translation(-local))
    # move the object so that its local point `local` stays put
    world_shift = mw.to_3x3() @ local
    if o.parent is None:
        o.location += world_shift
    else:
        o.location += rig_props.parent_space(o).to_3x3().inverted_safe() @ world_shift
    bpy.context.view_layer.update()
    for c, m in kids.items():
        c.matrix_world = m
    bpy.context.view_layer.update()
    pivot = tuple(o.matrix_world.translation)
    for j, v in saved.items():
        rig_props.set_preview_value(j, v)
    bpy.context.view_layer.update()
    return pivot


def rotate_parts(objects, pivot_world, axis_world, degrees):
    """Turns objects about a world axis through a world point, with their children (as a
    rigid group), for example to re-pose a hook block that is modeled tilted for transport.
    Objects whose parent is also in the list move with it. Works in the rest pose; the joint
    preview is restored afterwards. Returns the names turned."""
    objs = [_obj(o) for o in objects]
    root = rig_props.rig_root(objs[0]) if objs else None
    saved = {}
    if root is not None:
        saved = {j: rig_props.preview_value(j.bii_rig) for j in rig_props.joints(root)}
        rig_preview.reset(root)
    bpy.context.view_layer.update()
    pivot = Vector(pivot_world)
    turn = (Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(degrees), 4, Vector(axis_world).normalized())
            @ Matrix.Translation(-pivot))
    chosen = set(objs)
    tops = [o for o in objs if not any(p in chosen for p in rig_props.ancestors(o))]
    worlds = {o: o.matrix_world.copy() for o in tops}
    for o in tops:
        o.matrix_world = turn @ worlds[o]
    bpy.context.view_layer.update()
    for j, v in saved.items():
        rig_props.set_preview_value(j, v, followers=False)
    if root is not None:
        rig_props.update_followers(root)
    bpy.context.view_layer.update()
    return [o.name for o in tops]


def outline(obj, depth=3, group=True, limit=200):
    """A compact tree of an object's hierarchy for a first look at big models: name, type and,
    with group, siblings with the same base name (Bolt, Bolt.001, ...) folded into one line
    with a count. Returns a list of indented lines (at most limit)."""
    import re
    kids = rig_props.children_map()
    lines = []

    def base(n):
        return re.sub(r"\.\d{3}$", "", n)

    def visit(o, level):
        if len(lines) >= limit:
            return
        n_kids = len(kids.get(o, ()))
        lines.append(f"{'  ' * level}{o.name} [{o.type.lower()}" + (f", {n_kids} children]" if n_kids else "]"))
        if level + 1 >= depth:
            return
        children = kids.get(o, ())
        if not group:
            for c in children:
                visit(c, level + 1)
            return
        groups = {}
        for c in children:
            groups.setdefault(base(c.name), []).append(c)
        for name, members in groups.items():
            if len(members) == 1 or any(kids.get(c) for c in members):
                for c in members:
                    visit(c, level + 1)
            elif len(lines) < limit:
                lines.append(f"{'  ' * (level + 1)}{name} x{len(members)} [{members[0].type.lower()}]")

    visit(_obj(obj), 0)
    if len(lines) >= limit:
        lines.append("... (more; raise limit or depth)")
    return lines


def to_dprob(root, point_world):
    """A world point in dProB's equipment space (meters; +Y up, +Z forward, +X right), as the
    exported equipment has it. Use it to check the Forward setting."""
    space = ik_chain.Space(_root(root))
    m = Matrix.Translation(Vector(point_world))
    return tuple(round(float(v), 6) for v in space.pose(rig_export._np(m))[:3, 3])


def verify_ik(obj, targets_world, path=None, position_only=True):
    """Runs the IK capability of a part as exported (the graph in the .dequip, with a port of
    dProB's IK node) for world targets, and compares it with the Blender preview's solve.

    Returns per target: the position error of the exported graph's result (meters, the target
    part's distance from the target) and the largest joint difference to the preview.
    """
    import numpy as np
    from . import ik_capability, ik_solver
    o = _obj(obj)
    root = rig_props.rig_root(o)
    path = bpy.path.abspath(path or root.bii_rig.export_path)
    model = dequip_format.read(path).model
    cap_id = o.bii_rig.ik_capability_id
    cap = next((c for c in model.get("CustomCapabilities", []) if c["Signature"]["Id"]["Guid"] == cap_id), None)
    if cap is None:
        raise RigError(f"{path} has no IK capability of {o.name}; export first")
    setup = ik_chain.Setup(o)
    by_name = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
    joints, parents, fixed = ik_solver.rig_from_model(model["HierarchyRoots"])
    state = {k: (np.eye(3) if j.kind == "BALL" else 0.0) for k, j in joints.items()}
    for name, v in setup.start_values.items():
        if name in by_name:
            state[by_name[name]] = v
    out = []
    cur = setup.current_world()
    for t in targets_world:
        world = np.array(cur)
        world[:3, 3] = t
        dest = setup.destination(world)
        moves = ik_capability.simulate(cap, model, dest, state, inputs={"Position Only": position_only})
        values, _, _ = setup.solve(dest, position_only=position_only)
        sim = ik_capability._Sim(model, {**state, **moves}, None)
        reached = sim.point_pose(by_name[o.name])[:3, 3]
        diff = 0.0
        for name, v in values.items():
            k = by_name.get(name)
            if k in moves and not isinstance(v, np.ndarray):
                d = float(moves[k]) - float(v)
                if joints[k].kind == "REVOLUTE" and joints[k].limits is None:
                    d = (d + math.pi) % (2 * math.pi) - math.pi
                diff = max(diff, abs(d))
        out.append({"target": tuple(t), "position_error_m": float(np.linalg.norm(reached - dest[:3, 3])),
                    "max_joint_difference": diff})
    return out


def add_point(name, parent, point_world, axes_from=None, attachment_point=True, size=0.2):
    """Adds a fixed empty at a world point under `parent` and returns its name: the load point of
    a hook or a tool (attachment_point=True), the exact point an IK capability moves and cargo
    hangs from. axes_from: the object whose world rotation it takes (default the parent's); the
    cargo hangs in these axes. Nothing else moves."""
    name = insert_joint(name, parent, [], point_world, axes_from=axes_from, size=size)
    o = bpy.data.objects[name]
    o.bii_rig.joint_type = "FIXED"
    o.bii_rig.attachment_point = bool(attachment_point)
    return name


def insert_joint(name, parent, children, pivot_world, axes_from=None, size=0.3):
    """Adds an empty between `parent` and `children` and returns its name: a joint of its own
    without geometry, for example a pitch reversal at the boom tip, a hook rotation separate from
    the hoist, or a hinge whose pivot is not the origin of any mesh. Nothing moves in the world.

    pivot_world: the empty's origin (the joint's pivot). axes_from: an object whose world rotation
    the empty takes (default the parent's), so axis="X" means the same direction as on that
    object. The children keep their world transforms and joint settings. Give it a joint with
    set_joint afterwards.
    """
    p = _obj(parent)
    kids = [_obj(c) for c in children]
    for c in kids:
        if c.parent is not p:
            raise RigError(f"{c.name} is not a child of {p.name}")
    if bpy.data.objects.get(name) is not None:
        raise RigError(f"an object named {name!r} exists already")
    root = rig_props.rig_root(p)
    saved = {}
    if root is not None:   # work in the rest pose
        saved = {j: rig_props.preview_value(j.bii_rig) for j in rig_props.joints(root)}
        rig_preview.reset(root)
    bpy.context.view_layer.update()
    ref = _obj(axes_from) if axes_from is not None else p
    rot = ref.matrix_world.to_3x3().normalized()
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "ARROWS"
    e.empty_display_size = size
    for col in p.users_collection:
        col.objects.link(e)
    e.parent = p
    e.matrix_world = Matrix.LocRotScale(Vector(pivot_world), rot.to_quaternion(), None)
    bpy.context.view_layer.update()
    worlds = {c: c.matrix_world.copy() for c in kids}
    for c in kids:
        c.parent = e
        c.matrix_world = worlds[c]
    bpy.context.view_layer.update()
    if root is not None:
        rig_props.ensure_ids(root)
        for j, v in saved.items():
            rig_props.set_preview_value(j, v)
        bpy.context.view_layer.update()
    return e.name


# ------------------------------------------------------------------ preview

def set_preview(obj, value):
    """Shows a joint value: degrees (REVOLUTE), meters (PRISMATIC), (x, y, z) degrees (BALL).
    Values outside the limits are clamped. Returns the value shown."""
    o = _obj(obj)
    j = o.bii_rig
    if j.joint_type == "REVOLUTE":
        rig_props.set_preview_value(o, math.radians(value))
        return math.degrees(j.preview_angle)
    if j.joint_type == "PRISMATIC":
        rig_props.set_preview_value(o, value / _unit_scale())
        return j.preview_length * _unit_scale()
    if j.joint_type == "BALL":
        rig_props.set_preview_value(o, tuple(math.radians(v) for v in value))
        return tuple(math.degrees(v) for v in j.preview_ball)
    raise RigError(f"{o.name} has no joint (FIXED)")


def preview(root, values):
    """Shows several joint values: {object name: value} (see set_preview). Returns the values shown."""
    _root(root)
    out = {name: set_preview(name, v) for name, v in values.items()}
    bpy.context.view_layer.update()
    return out


def sweep_pose(root, phase_deg):
    """Puts every joint at a phase of the Play Joints sweep (0: min, 180: max), for screenshots."""
    r = _root(root)
    for i, o in enumerate(rig_props.joints(r)):
        rig_props.set_preview_value(o, rig_preview.sweep_value(o.bii_rig, math.radians(phase_deg)))
    bpy.context.view_layer.update()


def reset_preview(root):
    """Every joint back to 0 (the rest pose). Follow joints then take their leaders' values,
    a constant its offset: the rest preview is the modeled pose only without constants."""
    rig_preview.reset(_root(root))
    bpy.context.view_layer.update()


# ------------------------------------------------------------------ export

def problems(root):
    """["ERROR: ...", "WARNING: ..."]; errors stop the export."""
    return [f"{s}: {t}" for s, t in rig_export.problems(_root(root))]


def export(root, path=None, keep_capabilities=None):
    """Writes the rig as a dProB scriptable equipment (.dequip) and returns a summary.

    path: the file (default: the rig's last file). keep_capabilities: if the file exists, keep its
    capabilities, state variables and vehicle data scripted in dProB (default: the rig's setting,
    on). Element ids stay the same over exports, so the capabilities keep their joints.
    """
    return rig_export.export(_root(root), path, keep_capabilities)


def verify(root, path=None, samples=3):
    """Checks a written .dequip against Blender: random joint values, evaluated the way dProB
    does, compared with the Blender preview. max_error (meters) should be below 1e-4."""
    return rig_export.check(_root(root), path, samples)


def read_dequip(path):
    """Summary of a .dequip file: metadata, elements as a tree with their joints, capabilities."""
    dq = dequip_format.read(bpy.path.abspath(path))
    m = dq.model

    def node(e):
        jc = e["JointConstraint"]
        d = {"name": e["Name"], "id": e["Id"], "joint": jc["$case"], "visible": e["IsVisible"],
             "attachment_point": e["IsAttachmentPoint"], "geometry": e["GeometrySource"]["$case"]}
        if "Axis" in jc:
            d["axis"] = (jc["Axis"]["X"], jc["Axis"]["Y"], jc["Axis"]["Z"])
        if jc["$case"] == "Revolute" and jc.get("Limits"):
            d["limits_deg"] = (math.degrees(jc["Limits"]["MinRadians"]), math.degrees(jc["Limits"]["MaxRadians"]))
        if jc["$case"] == "Prismatic":
            d["limits_m"] = (jc["MinMeters"], jc["MaxMeters"])
        if e["Children"]:
            d["children"] = [node(c) for c in e["Children"]]
        return d

    return {
        "metadata": dq.metadata, "asset_guid": dq.guid, "equipment_id": m.get("EquipmentId"),
        "capabilities": [c["Signature"]["Name"] for c in m.get("CustomCapabilities", [])],
        "equipment_types": len(m.get("EquipmentTypeImplementations", [])),
        "state_variables": [v["Name"] for v in m.get("StateVariables", [])],
        "files": sorted(dq.files),
        "hierarchy": [node(e) for e in m.get("HierarchyRoots", [])],
    }

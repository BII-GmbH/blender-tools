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

def make_rig(root, export_path=None):
    """Makes the object the root of a rig: it and its descendants become the equipment.

    The root's origin becomes the equipment's origin. Blender +Y is dProB's forward, +Z up.
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
    rig_props.ensure_ids(obj)
    return obj


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

def _axis(j, axis):
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


def set_joint(obj, joint_type, axis=None, limits="keep", visible=None, attachment_point=None):
    """Sets the joint of a rig object.

    joint_type: FIXED, REVOLUTE, PRISMATIC or BALL.
    axis: "X", "Y", "Z", "-X", "-Y", "-Z" or a vector, in the object's local axes (its rotation,
        without scale). The axis goes through the object's origin (see set_pivot).
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
    if axis is not None:
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


def describe_joint(obj):
    """Joint of an object as a dict (degrees, meters; the axis also in world space)."""
    o = _obj(obj)
    j = o.bii_rig
    d = {"object": o.name, "joint": j.joint_type, "visible": j.visible,
         "attachment_point": j.attachment_point, "element_id": j.element_id,
         "pivot_world": tuple(round(v, 6) for v in rig_props.rest_world(o).translation)}
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

    def node(o):
        d = describe_joint(o)
        d["type"] = o.type
        kids = [c for c in sorted(o.children, key=lambda c: c.name) if rig_props.is_rig_object(c)]
        if kids:
            d["children"] = [node(c) for c in kids]
        return d

    return {"root": r.name, "equipment_id": r.bii_rig.equipment_id, "export_path": r.bii_rig.export_path,
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
    degrees or a 3x3 matrix); what is not given stays as the part is now.
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
    values, pos_err, rot_err = setup.solve(dest)
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
    """Every joint back to 0 (the rest pose)."""
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

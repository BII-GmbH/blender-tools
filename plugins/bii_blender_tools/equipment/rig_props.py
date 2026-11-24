"""Rig settings stored on objects (Object.bii_rig) and the joint preview.

A rig is a root object and all its descendants. Every object of the rig becomes one element of
the dProB equipment hierarchy. An object's joint moves it, with its children, relative to its
parent, about or along an axis through its origin, as dProB does:

    pose = rest pose * offset,   offset = rotation about the axis (Revolute),
                                          shift along the axis (Prismatic) or any rotation (Ball)

The axis is given in the object's own axes (its rotation, without scale).

The preview shows a joint value with the object's delta transforms (Object > Transform > Delta
Transform), so the object's own location, rotation and scale stay the rest pose. Objects with
joints therefore must not use delta transforms otherwise.
"""

import math

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import dequip_format

RIG_TYPES = {"MESH", "EMPTY", "CURVE", "SURFACE", "FONT", "META"}

JOINT_ITEMS = [
    ("FIXED", "Fixed", "Moves only with its parent", "LOCKED", 0),
    ("REVOLUTE", "Revolute", "Turns about an axis (hinge)", "DRIVER_ROTATIONAL_DIFFERENCE", 1),
    ("PRISMATIC", "Prismatic", "Slides along an axis", "EMPTY_SINGLE_ARROW", 2),
    ("BALL", "Ball", "Turns freely about its origin (ball joint)", "SPHERE", 3),
]

AXIS_ITEMS = [
    ("X", "X", "Local X axis"),
    ("Y", "Y", "Local Y axis"),
    ("Z", "Z", "Local Z axis"),
    ("NEG_X", "-X", "Negative local X axis"),
    ("NEG_Y", "-Y", "Negative local Y axis"),
    ("NEG_Z", "-Z", "Negative local Z axis"),
    ("CUSTOM", "Custom", "A direction in the object's local axes"),
]

AXIS_VECTORS = {
    "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1),
    "NEG_X": (-1, 0, 0), "NEG_Y": (0, -1, 0), "NEG_Z": (0, 0, -1),
}


class RigError(Exception):
    pass


# ------------------------------------------------------------------ rig structure

def is_rig_object(obj):
    return obj is not None and obj.type in RIG_TYPES


def rig_root(obj):
    """The rig root that obj belongs to (obj itself if it is one), or None."""
    while obj is not None:
        if obj.bii_rig.is_root:
            return obj
        obj = obj.parent
    return None


def rig_objects(root):
    """Root and its descendants that become elements, parents before children.

    A descendant of an object of another type (camera, light, armature) is left out with it.
    """
    out = []

    def visit(o):
        if not is_rig_object(o):
            return
        out.append(o)
        for c in sorted(o.children, key=lambda c: c.name):
            visit(c)

    visit(root)
    return out


def rig_roots(scene=None):
    objs = scene.objects if scene else bpy.data.objects
    return [o for o in objs if o.bii_rig.is_root]


def joints(root):
    return [o for o in rig_objects(root) if o.bii_rig.joint_type != "FIXED"]


def ensure_ids(root):
    """Gives every object of the rig a stable element id; duplicated objects get new ones.

    dProB capabilities refer to joints by these ids, so they must survive re-exports.
    """
    r = root.bii_rig
    if not r.equipment_id:
        r.equipment_id = dequip_format.new_id()
    if not r.geometry_id:
        r.geometry_id = dequip_format.new_id()
    seen = {}
    for o in rig_objects(root):
        j = o.bii_rig
        if j.element_id in seen or not j.element_id:
            other = seen.get(j.element_id)
            # the copy (the one not named as when the id was given) gets the new id
            if other is not None and other.bii_rig.element_owner != other.name and j.element_owner == o.name:
                other.bii_rig.element_id = dequip_format.new_id()
                other.bii_rig.element_owner = other.name
                seen[other.bii_rig.element_id] = other
            else:
                j.element_id = dequip_format.new_id()
        j.element_owner = o.name
        seen[j.element_id] = o
    caps = set()
    for o in rig_objects(root):
        j = o.bii_rig
        if j.ik_target and (not j.ik_capability_id or j.ik_capability_id in caps):
            j.ik_capability_id = dequip_format.new_id()
        if j.ik_target:
            caps.add(j.ik_capability_id)


# ------------------------------------------------------------------ joint values

def local_axis(j):
    """Unit joint axis in the object's local axes."""
    v = Vector(AXIS_VECTORS.get(j.axis) or j.custom_axis)
    if v.length < 1e-9:
        raise RigError("the custom joint axis is zero")
    return v.normalized()


def limits(j):
    """(min, max) of the joint value: radians, Blender units, or None (revolute without limits)."""
    if j.joint_type == "REVOLUTE":
        return (j.angle_min, j.angle_max) if j.use_limits else None
    if j.joint_type == "PRISMATIC":
        return (j.length_min, j.length_max)
    return None


def _clamp(j, value):
    lim = limits(j)
    if lim is None:
        return value
    lo, hi = min(lim), max(lim)
    return max(lo, min(hi, value))


def _get_angle(self):
    return self.get("_preview_angle", 0.0)


def _set_angle(self, value):
    self["_preview_angle"] = _clamp(self, value) if self.joint_type == "REVOLUTE" else value


def _get_length(self):
    return self.get("_preview_length", 0.0)


def _set_length(self, value):
    self["_preview_length"] = _clamp(self, value) if self.joint_type == "PRISMATIC" else value


def preview_value(j):
    if j.joint_type == "REVOLUTE":
        return j.preview_angle
    if j.joint_type == "PRISMATIC":
        return j.preview_length
    if j.joint_type == "BALL":
        return tuple(j.preview_ball)
    return None


_PREVIEW_PROPS = {"REVOLUTE": "preview_angle", "PRISMATIC": "preview_length", "BALL": "preview_ball"}


def set_preview_value(obj, value, followers=True):
    """Sets and shows a joint value; raises RigError (value unchanged) if it cannot be shown.
    The joints that follow it (Drive: Follow) are updated too, unless followers is False."""
    j = obj.bii_rig
    prop = _PREVIEW_PROPS.get(j.joint_type)
    if prop is None:
        raise RigError(f"{obj.name} has no joint")
    old = getattr(j, prop)
    old = tuple(old) if j.joint_type == "BALL" else old
    global _updating
    was = _updating
    _updating = True
    try:
        setattr(j, prop, tuple(value) if j.joint_type == "BALL" else float(value))
        try:
            apply_preview(obj)
        except RigError:
            setattr(j, prop, old)
            raise
    finally:
        _updating = was
    if followers and not was:
        update_followers(rig_root(obj))


# ------------------------------------------------------------------ couplings (Drive)

DRIVE_ITEMS = [
    ("FREE", "Free", "Set by hand or by inverse kinematics"),
    ("FOLLOW", "Follow", "Value = offset + sum of factor x leader joint value. Without leaders a "
                         "constant. IK capabilities set it after each IK pass"),
    ("HOLD", "Hold", "IK capabilities keep the value the joint has when they start"),
]

_updating = False


def drive_unit_kind(j):
    """'ANGLE', 'LENGTH' or None: what a joint's drive offset is measured in."""
    return {"REVOLUTE": "ANGLE", "PRISMATIC": "LENGTH"}.get(j.joint_type)


def drive_offset(j):
    return j.drive_offset_angle if j.joint_type == "REVOLUTE" else j.drive_offset_length


def drive_problems(obj):
    """Errors in the Drive settings of one object: [text]."""
    j = obj.bii_rig
    if j.drive != "FOLLOW" or j.joint_type == "FIXED":
        return []
    if j.joint_type == "BALL":
        return [f"{obj.name}: a ball joint cannot follow other joints"]
    out = []
    root = rig_root(obj)
    for t in j.drive_terms:
        leader = t.leader
        if leader is None:
            out.append(f"{obj.name}: a Follow entry has no leader joint")
        elif leader is obj:
            out.append(f"{obj.name} follows itself")
        elif rig_root(leader) is not root:
            out.append(f"{obj.name} follows {leader.name}, which is not in the same rig")
        elif leader.bii_rig.joint_type != j.joint_type:
            out.append(f"{obj.name} ({j.joint_type.lower()}) follows {leader.name} "
                       f"({leader.bii_rig.joint_type.lower()}); both must be revolute or both prismatic")
    return out


def followers(root):
    """Joints of the rig with Drive Follow or Hold, leaders before their followers.
    Raises RigError for invalid settings or a cycle."""
    objs = [o for o in joints(root) if o.bii_rig.drive in ("FOLLOW", "HOLD")]
    errors = [e for o in objs for e in drive_problems(o)]
    if errors:
        raise RigError("; ".join(errors))
    order, state = [], {}

    def visit(o):
        s = state.get(o)
        if s == 2:
            return
        if s == 1:
            raise RigError(f"the Follow settings form a cycle through {o.name}")
        state[o] = 1
        if o.bii_rig.drive == "FOLLOW":
            for t in o.bii_rig.drive_terms:
                if t.leader.bii_rig.drive in ("FOLLOW", "HOLD"):
                    visit(t.leader)
        state[o] = 2
        order.append(o)

    for o in objs:
        visit(o)
    return order


def follow_value(obj, values=None):
    """Value of a Follow joint from its leaders' preview values (or values: {object: value})."""
    j = obj.bii_rig
    v = drive_offset(j)
    for t in j.drive_terms:
        lv = values[t.leader] if values is not None and t.leader in values else preview_value(t.leader.bii_rig)
        v += t.factor * lv
    return v


def update_followers(root):
    """Sets the preview of every Follow joint of the rig from its leaders."""
    global _updating
    if root is None or _updating:
        return
    try:
        order = followers(root)
    except RigError as e:
        print(f"BII Tools: {e}")
        return
    _updating = True
    try:
        for o in order:
            if o.bii_rig.drive != "FOLLOW":
                continue
            j = o.bii_rig
            j[_PREVIEW_PROPS[j.joint_type].replace("preview", "_preview")] = _clamp(j, follow_value(o))
            try:
                apply_preview(o)
            except RigError as e:
                print(f"BII Tools: {e}")
    finally:
        _updating = False


# ------------------------------------------------------------------ preview

def rest_rotation(obj):
    """3x3 rotation of the object's own rotation values (no deltas)."""
    mode = obj.rotation_mode
    if mode == "QUATERNION":
        return obj.rotation_quaternion.normalized().to_matrix()
    if mode == "AXIS_ANGLE":
        a = obj.rotation_axis_angle
        return Matrix.Rotation(a[0], 3, Vector(a[1:]).normalized())
    return obj.rotation_euler.to_matrix()


def rest_basis(obj):
    return Matrix.LocRotScale(obj.location, rest_rotation(obj), obj.scale)


def parent_space(obj):
    """World matrix of the space the object's basis is in (parent and parent inverse)."""
    if obj.parent is None:
        return Matrix.Identity(4)
    return obj.matrix_world @ obj.matrix_basis.inverted_safe()


def rest_world(obj):
    """World matrix of the object without the preview (valid while parents are previewed too)."""
    return parent_space(obj) @ rest_basis(obj)


def world_axis(obj):
    """Unit joint axis in world space, in the rest pose of the object."""
    m = parent_space(obj).to_3x3() @ rest_rotation(obj) @ local_axis(obj.bii_rig)
    return m.normalized()


def _deltas_clean(obj):
    if obj.delta_location.length > 1e-9:
        return False
    if obj.rotation_mode == "QUATERNION":
        q = obj.delta_rotation_quaternion
        return abs(abs(q.w) - 1) < 1e-9
    return Vector(obj.delta_rotation_euler).length < 1e-9


def _write_deltas(obj, location, rotation):
    obj.delta_location = location
    if obj.rotation_mode == "QUATERNION":
        obj.delta_rotation_quaternion = rotation.to_quaternion()
        obj.delta_rotation_euler = (0, 0, 0)
    else:
        obj.delta_rotation_euler = rotation.to_euler(obj.rotation_mode)
        obj.delta_rotation_quaternion = (1, 0, 0, 0)


def apply_preview(obj):
    """Shows the object's preview value with its delta transforms."""
    j = obj.bii_rig
    value = preview_value(j)
    zero = value is None or (j.joint_type == "BALL" and Vector(value).length < 1e-12) or value == 0
    if not j.preview_owned:
        if zero:
            return
        if not _deltas_clean(obj):
            raise RigError(f"{obj.name} uses delta transforms; apply them (Object > Apply > "
                           "All Transforms to Deltas, then Animated Transforms to Deltas off) or clear them "
                           "before previewing its joint")
    if obj.rotation_mode == "AXIS_ANGLE" and not zero:
        raise RigError(f"{obj.name} uses Axis Angle rotation; switch it to Quaternion or Euler to preview")
    if zero:
        _write_deltas(obj, Vector(), Matrix.Identity(3))
        j.preview_owned = False
        return
    rest = rest_rotation(obj)
    location, rotation = Vector(), Matrix.Identity(3)
    if j.joint_type == "REVOLUTE":
        rotation = Matrix.Rotation(value, 3, rest @ local_axis(j))
    elif j.joint_type == "PRISMATIC":
        # delta_location is in parent space; it must move the object `value` along the world axis
        # (also under a non-uniformly scaled parent)
        ps = parent_space(obj).to_3x3()
        d = rest @ local_axis(j)
        scale = (ps @ d).length or 1.0
        location = d * (value / scale)
    elif j.joint_type == "BALL":
        rotation = rest @ Euler(value, "XYZ").to_matrix() @ rest.transposed()
    _write_deltas(obj, location, rotation)
    j.preview_owned = True


def clear_preview(obj):
    j = obj.bii_rig
    if j.preview_owned:
        _write_deltas(obj, Vector(), Matrix.Identity(3))
        j.preview_owned = False


def _preview_update(self, context):
    obj = self.id_data
    try:
        apply_preview(obj)
    except RigError as e:
        print(f"BII Tools: {e}")
    update_followers(rig_root(obj))


def _joint_update(self, context):
    obj = self.id_data
    # the stored values may be outside new limits or belong to another joint type
    self.preview_angle = self.preview_angle
    self.preview_length = self.preview_length
    if self.joint_type != "BALL" and tuple(self.preview_ball) != (0.0, 0.0, 0.0):
        self["preview_ball"] = (0.0, 0.0, 0.0)
    try:
        apply_preview(obj)
    except RigError as e:
        print(f"BII Tools: {e}")
    update_followers(rig_root(obj))


def _drive_update(self, context):
    update_followers(rig_root(self.id_data))


def _leader_poll(self, obj):
    return obj.bii_rig.joint_type in ("REVOLUTE", "PRISMATIC")


def _hook_poll(self, obj):
    return obj.bii_rig.attachment_point and rig_root(obj) is self.id_data


class BiiRigDriveTerm(bpy.types.PropertyGroup):
    leader: bpy.props.PointerProperty(
        name="Leader", type=bpy.types.Object, poll=_leader_poll, update=_drive_update,
        description="Joint whose value this joint follows")
    factor: bpy.props.FloatProperty(
        name="Factor", default=1.0, update=_drive_update,
        description="Multiplies the leader's value (1: same angle, -1: opposite)")


class BiiRigSettings(bpy.types.PropertyGroup):
    # ---- rig root
    is_root: bpy.props.BoolProperty(
        name="Rig Root", description="This object and its children form a dProB equipment")
    equipment_id: bpy.props.StringProperty(
        name="Equipment ID", description="dProB equipment id; stays the same over exports")
    geometry_id: bpy.props.StringProperty(name="Geometry ID")
    export_path: bpy.props.StringProperty(
        name="File", subtype="FILE_PATH", description="The .dequip file to write")
    keep_capabilities: bpy.props.BoolProperty(
        name="Keep Capabilities", default=True,
        description="When the file exists, keep its capabilities, state variables and vehicle data "
                    "(scripted in dProB) and replace only the model and joints")
    show_axes: bpy.props.BoolProperty(
        name="Show Joints", default=True, description="Draw the joint axes and limits in the viewport")
    crane_hook: bpy.props.PointerProperty(
        name="Crane Hook", type=bpy.types.Object, poll=_hook_poll,
        description="Attachment point with an IK capability that is the crane's hook. The export "
                    "then makes the equipment a dProB Crane: Move Arm (the hook's IK capability), "
                    "Lift and Deliver")

    # ---- element
    element_id: bpy.props.StringProperty(name="Element ID")
    element_owner: bpy.props.StringProperty()
    visible: bpy.props.BoolProperty(
        name="Visible", default=True, description="Show this part's geometry in dProB")
    attachment_point: bpy.props.BoolProperty(
        name="Attachment Point", description="Other objects can be attached here in dProB")

    # ---- joint
    joint_type: bpy.props.EnumProperty(name="Joint", items=JOINT_ITEMS, default="FIXED", update=_joint_update)
    axis: bpy.props.EnumProperty(name="Axis", items=AXIS_ITEMS, default="Z", update=_joint_update)
    custom_axis: bpy.props.FloatVectorProperty(
        name="Custom Axis", size=3, default=(0.0, 0.0, 1.0), subtype="XYZ", update=_joint_update)
    use_limits: bpy.props.BoolProperty(
        name="Limits", default=True, description="Limit the angle", update=_joint_update)
    angle_min: bpy.props.FloatProperty(
        name="Min", subtype="ANGLE", default=-math.pi / 2, update=_joint_update)
    angle_max: bpy.props.FloatProperty(
        name="Max", subtype="ANGLE", default=math.pi / 2, update=_joint_update)
    length_min: bpy.props.FloatProperty(
        name="Min", subtype="DISTANCE", unit="LENGTH", default=0.0, update=_joint_update)
    length_max: bpy.props.FloatProperty(
        name="Max", subtype="DISTANCE", unit="LENGTH", default=1.0, update=_joint_update)

    # ---- preview
    preview_angle: bpy.props.FloatProperty(
        name="Angle", subtype="ANGLE", get=_get_angle, set=_set_angle, update=_preview_update,
        description="Preview angle (within the limits)")
    preview_length: bpy.props.FloatProperty(
        name="Offset", subtype="DISTANCE", unit="LENGTH", get=_get_length, set=_set_length,
        update=_preview_update, description="Preview offset (within the limits)")
    preview_ball: bpy.props.FloatVectorProperty(
        name="Rotation", size=3, subtype="EULER", update=_preview_update,
        description="Preview rotation, in the object's local axes")
    preview_owned: bpy.props.BoolProperty(options={"HIDDEN"})

    # ---- drive (coupling)
    drive: bpy.props.EnumProperty(name="Drive", items=DRIVE_ITEMS, default="FREE", update=_drive_update)
    drive_terms: bpy.props.CollectionProperty(type=BiiRigDriveTerm)
    drive_offset_angle: bpy.props.FloatProperty(
        name="Offset", subtype="ANGLE", update=_drive_update, description="Added to the leaders' sum")
    drive_offset_length: bpy.props.FloatProperty(
        name="Offset", subtype="DISTANCE", unit="LENGTH", update=_drive_update,
        description="Added to the leaders' sum")

    # ---- IK target: an IK capability moves this part to a pose
    ik_target: bpy.props.BoolProperty(
        name="IK Capability", description="Export a dProB capability that moves this part to a "
                                          "given pose with inverse kinematics")
    ik_name: bpy.props.StringProperty(name="Name", description="Name of the capability in dProB")
    ik_description: bpy.props.StringProperty(name="Description")
    ik_capability_id: bpy.props.StringProperty(name="Capability ID")
    ik_root: bpy.props.PointerProperty(
        name="Root Joint", type=bpy.types.Object, poll=_leader_poll,
        description="Lowest joint the IK moves (empty: all joints down to the rig root)")
    ik_iterations: bpy.props.IntProperty(
        name="Iterations", default=100, min=1, max=10000,
        description="Steps of the IK solver per pass")
    ik_loops: bpy.props.IntProperty(
        name="Passes", default=10, min=1, max=1000,
        description="IK passes when joints of the chain follow others or hold (after each pass "
                    "the followers are set); one pass without")
    ik_seed_margin: bpy.props.FloatProperty(
        name="Start Away from Limits", default=0.1, min=0.0, max=0.45, subtype="FACTOR",
        description="The IK starts with every limited joint of the chain at least this share of its "
                    "range away from its limits (joints already inside stay as they are). A joint "
                    "at a limit often stalls the solver, for example a boom lying flat that would "
                    "have to rise to reach closer. 0: start from the current values")
    ik_orientation_weight: bpy.props.FloatProperty(
        name="Orientation Weight", default=0.01, min=0.0, precision=4, step=0.1,
        description="How much the orientation counts against the position (0: position only). "
                    "dProB measures the orientation error in degrees")


def register():
    bpy.utils.register_class(BiiRigDriveTerm)
    bpy.utils.register_class(BiiRigSettings)
    bpy.types.Object.bii_rig = bpy.props.PointerProperty(type=BiiRigSettings)


def unregister():
    del bpy.types.Object.bii_rig
    bpy.utils.unregister_class(BiiRigSettings)
    bpy.utils.unregister_class(BiiRigDriveTerm)

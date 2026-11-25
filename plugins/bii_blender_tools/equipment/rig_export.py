"""Export a rig as a dProB scriptable equipment (.dequip).

Steps:
1. Blender's FBX exporter writes the rig objects (rest pose, modifiers applied). Every texture is
   embedded in the FBX, as PNG or JPEG (textures.py converts the others to PNG).
2. fbx_binary and dprob_math read that FBX the way dProB's configurator imports an FBX: one
   hierarchy element per FBX model, with its pose and its geometry scale/shear.
3. The joints are converted into each element's frame. Blender's world and dProB's (Unity's)
   differ by an axis swap and a mirror; it is measured per element as K = G * B^-1, from the
   element's matrix in dProB (G) and the object's world matrix in Blender (B). A mirror flips
   the turning sense, so revolute axes are negated then: the same angle turns the same way.
4. The .dequip zip gets the hierarchy and the FBX. The root object's origin is the equipment's
   origin; Blender's +Y is dProB's forward (+Z), +Z is up (+Y).
"""

import math
import os
import random
import tempfile

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

from . import crane_type, dequip_format, dprob_math, fbx_binary, ik_capability, ik_chain, rig_props, textures
from .rig_props import RigError

# an IK target's rest frame may differ this much from the equipment's axes
ALIGN_TOLERANCE_DEG = 0.5

FBX_TYPES = {"EMPTY", "MESH", "OTHER"}


def _np(m):
    return np.array([list(r) for r in m], dtype=float)


def _det_sign(m):
    return -1.0 if np.linalg.det(m) < 0 else 1.0


# ------------------------------------------------------------------ checks

def problems(root):
    """List of (severity, text) about the rig; severity 'ERROR' stops the export."""
    out = []
    if root is None or not root.bii_rig.is_root:
        return [("ERROR", "no rig root")]
    kids = rig_props.children_map()
    objs = rig_props.rig_objects(root, kids)
    for o in objs:
        j = o.bii_rig
        for c in kids.get(o, ()):
            if not rig_props.is_rig_object(c):
                out.append(("WARNING", f"{c.name} ({c.type.lower()}) under {o.name} is left out, with its children"))
        if j.joint_type == "FIXED":
            continue
        if j.joint_type in ("REVOLUTE", "PRISMATIC"):
            try:
                rig_props.local_axis(j)
            except RigError as e:
                out.append(("ERROR", f"{o.name}: {e}"))
        if j.joint_type == "REVOLUTE" and j.use_limits and j.angle_min > j.angle_max:
            out.append(("ERROR", f"{o.name}: angle Min is larger than Max"))
        if j.joint_type == "PRISMATIC" and j.length_min > j.length_max:
            out.append(("ERROR", f"{o.name}: offset Min is larger than Max"))
        if o is root:
            out.append(("WARNING", f"{o.name} is the root: its joint moves the whole equipment"))
        if not j.preview_owned and not rig_props._deltas_clean(o):
            out.append(("WARNING", f"{o.name} has delta transforms; they are exported as part of its rest pose"))
        s = o.scale
        if max(abs(s.x), abs(s.y), abs(s.z)) - min(abs(s.x), abs(s.y), abs(s.z)) > 1e-6 and kids.get(o):
            out.append(("WARNING", f"{o.name} has non-uniform scale and children: apply its scale, "
                                   "else the children's joints may differ in dProB"))
    if not objs:
        out.append(("ERROR", f"{root.name} cannot be exported ({root.type.lower()})"))
    out += [("ERROR", t) for o in objs for t in rig_props.drive_problems(o)]
    if not any(s == "ERROR" for s, _ in out):
        try:
            rig_props.followers(root)
        except RigError as e:
            out.append(("ERROR", str(e)))
    out += [("WARNING", t) for t in follower_range_warnings(objs)]
    hook = root.bii_rig.crane_hook
    if hook is not None:
        if rig_props.rig_root(hook) is not root:
            out.append(("ERROR", f"the crane hook {hook.name} is not part of this rig"))
        elif not (hook.bii_rig.attachment_point and hook.bii_rig.ik_target):
            out.append(("ERROR", f"the crane hook {hook.name} must be an attachment point with an IK capability"))
        elif ik_chain.misalignment_deg(hook, root) > ALIGN_TOLERANCE_DEG:
            out.append(("ERROR", f"the crane hook {hook.name} is turned {ik_chain.misalignment_deg(hook, root):.0f} "
                                 "degrees from the equipment's axes: Lift and Deliver pass rotations with "
                                 "dProB's up as up. Align it with the equipment (fixed point), or add a "
                                 "load point under it"))
    names = {}
    space = None
    for o in objs:
        j = o.bii_rig
        if not j.ik_target:
            continue
        name = j.ik_name or f"Move {o.name}"
        if name in names:
            out.append(("ERROR", f"IK capabilities of {names[name]} and {o.name} have the same name {name!r}"))
        names[name] = o.name
        if ik_chain.end_joint(o)[0] is None:
            out.append(("ERROR", f"IK capability of {o.name}: no joint above it moves it"))
        elif j.joint_type == "FIXED" and not j.attachment_point:
            out.append(("ERROR", f"IK capability of {o.name}: a fixed part must be an attachment point "
                                 "(dProB reads its pose from there)"))
        elif o is not hook:
            if space is None:
                space = ik_chain.Space(root)
            angle = ik_chain.misalignment_deg(o, root, space)
            if angle > ALIGN_TOLERANCE_DEG:
                out.append(("WARNING", f"IK capability of {o.name}: its frame is turned {angle:.0f} degrees "
                                       "from the equipment's axes, so its Destination's rotation is too "
                                       "(identity is not upright). Align it with the equipment if it is a "
                                       "load point"))
        elif j.ik_root is not None and j.ik_root is not o and j.ik_root not in rig_props.ancestors(o):
            out.append(("ERROR", f"IK capability of {o.name}: its root joint {j.ik_root.name} is not above it"))
    return out


def follower_range_warnings(objs):
    """Follow joints whose leaders' limits drive them beyond their own limits. IK capabilities
    set followers without clamping them (the Blender preview clamps), so the two would differ."""
    out = []
    for o in objs:
        j = o.bii_rig
        if j.drive != "FOLLOW" or j.joint_type not in ("REVOLUTE", "PRISMATIC"):
            continue
        own = rig_props.limits(j)
        if own is None:
            continue
        lo = hi = rig_props.drive_offset(j)
        for t in j.drive_terms:
            lim = rig_props.limits(t.leader.bii_rig) if t.leader else None
            if lim is None:
                lo, hi = -math.inf, math.inf
                break
            a, b = t.factor * lim[0], t.factor * lim[1]
            lo, hi = lo + min(a, b), hi + max(a, b)
        eps = 1e-6
        if lo < min(own) - eps or hi > max(own) + eps:
            out.append(f"{o.name} follows values beyond its limits; IK capabilities do not clamp "
                       "followers, the preview does. Widen its limits to the leaders' range")
    return out


def ik_capabilities(root, element_ids):
    """Custom capabilities (JSON) of the rig's IK targets."""
    out = []
    for o in rig_props.rig_objects(root):
        if o.bii_rig.ik_target:
            setup = ik_chain.Setup(o)
            out.append(ik_capability.build(setup.capability_spec(element_ids)))
    return out


def merge_capabilities(existing, generated):
    """Existing custom capabilities without the earlier generated ones, then the generated ones."""
    ids = {c["Signature"]["Id"]["Guid"] for c in generated}
    keep = [c for c in existing
            if c.get("Signature", {}).get("Id", {}).get("Guid") not in ids
            and ik_capability.GENERATED_MARK not in (c.get("Signature", {}).get("Description") or "")]
    return keep + generated


# ------------------------------------------------------------------ FBX

class _PreviewStash:
    """Takes the joint preview off while exporting and puts it back afterwards."""

    def __init__(self, objs):
        self.objs = [o for o in objs if o.bii_rig.preview_owned]

    def __enter__(self):
        for o in self.objs:
            rig_props._write_deltas(o, Vector(), Matrix.Identity(3))
        bpy.context.view_layer.update()
        return self

    def __exit__(self, *exc):
        for o in self.objs:
            try:
                rig_props.apply_preview(o)
            except RigError:
                pass
        bpy.context.view_layer.update()


def write_fbx(objs, path):
    """Exports exactly objs with Blender's FBX exporter (the settings dProB's import expects)."""
    view_layer = bpy.context.view_layer
    old_selected = [o for o in view_layer.objects if o.select_get()]
    old_active = view_layer.objects.active
    hidden = [o for o in objs if o.hide_get() or o.hide_viewport]
    states = {o: (o.hide_get(), o.hide_viewport) for o in hidden}
    missing = [o.name for o in objs if o.name not in view_layer.objects]
    if missing:
        raise RigError(f"not in the current view layer: {', '.join(missing)}")
    try:
        for o in hidden:
            o.hide_viewport = False
            o.hide_set(False)
        for o in old_selected:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        view_layer.objects.active = objs[0]
        result = bpy.ops.export_scene.fbx(
            filepath=path, check_existing=False, use_selection=True, use_visible=False,
            use_active_collection=False, object_types=FBX_TYPES, use_mesh_modifiers=True,
            apply_unit_scale=True, apply_scale_options="FBX_SCALE_NONE", global_scale=1.0,
            axis_forward="-Z", axis_up="Y", use_space_transform=True, bake_space_transform=False,
            mesh_smooth_type="FACE", use_custom_props=False, add_leaf_bones=False, bake_anim=False,
            path_mode="COPY", embed_textures=True, batch_mode="OFF")
        if "FINISHED" not in result:
            raise RigError("the FBX export failed")
    finally:
        for o in objs:
            o.select_set(False)
        for o, (h, hv) in states.items():
            o.hide_viewport = hv
            o.hide_set(h)
        for o in old_selected:
            if o.name in view_layer.objects:
                o.select_set(True)
        view_layer.objects.active = old_active


# ------------------------------------------------------------------ model

def _joint_model(obj, element, blender_world, unity_from_blender):
    j = obj.bii_rig
    if j.joint_type in ("FIXED", "BALL"):
        return dequip_format.joint(j.joint_type)
    a_w = np.array(rig_props.world_axis(obj))
    d = unity_from_blender @ a_w
    k = float(np.linalg.norm(d))
    u = d / k
    local = element.rotation.T @ u
    if j.joint_type == "REVOLUTE":
        local = local * _det_sign(unity_from_blender)
        lim = rig_props.limits(j)
        return dequip_format.joint("REVOLUTE", local, *(lim if lim else (None, None)))
    return dequip_format.joint("PRISMATIC", local, j.length_min * k, j.length_max * k)


def build(root, fbx_path):
    """(model roots, details) for a rig whose FBX is at fbx_path. The preview must be off."""
    objs = rig_props.rig_objects(root)
    by_name = {o.name: o for o in objs}
    fbx_roots = dprob_math.hierarchy(fbx_binary.read(fbx_path))
    r = root.bii_rig
    details = {"elements": 0, "joints": []}

    def make(el, origin):
        obj = by_name.get(el.name)
        if obj is None:
            raise RigError(f"the FBX has a model {el.name!r} that is not in the rig")
        b = _np(obj.matrix_world)
        if abs(np.linalg.det(b[:3, :3])) < 1e-12:
            raise RigError(f"{obj.name} has zero scale")
        unity_from_blender = el.global_transform[:3, :3] @ np.linalg.inv(b[:3, :3])
        j = obj.bii_rig
        position = el.local_position - origin if el.parent is None else el.local_position
        source = (dequip_format.fbx_source(r.geometry_id, el.model.id) if el.model.has_geometry
                  else {"$case": "Empty"})
        joint = _joint_model(obj, el, b, unity_from_blender)
        details["elements"] += 1
        if j.joint_type != "FIXED":
            details["joints"].append({"object": obj.name, "id": j.element_id, **joint})
        return dequip_format.element(
            obj.name, j.element_id, source,
            dequip_format.pose(position, dprob_math.quat_from_matrix(el.local_rotation)),
            el.scale_and_shear, joint, j.visible, j.attachment_point,
            [make(c, origin) for c in el.children])

    roots = [make(el, el.position) for el in fbx_roots]
    # the model's front to dProB's forward: turn the roots about dProB's up axis (Y)
    turn = _np(rig_props.forward_turn(root))
    turn_u = dprob_math_C @ turn @ dprob_math_C
    for el, model in zip(fbx_roots, roots):
        q = dprob_math.quat_from_matrix(turn_u @ el.rotation)
        model["Pose"]["Rotation"] = dequip_format.quat(q)
    return roots, details


dprob_math_C = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])    # Blender axes -> dProB axes


def _safe_name(name):
    keep = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip()
    return keep or "equipment"


def export(root, path=None, keep_capabilities=None):
    """Writes the rig of root to a .dequip file; returns a summary dict."""
    root = rig_props.rig_root(root)
    if root is None:
        raise RigError("the object is not part of a rig (no rig root above it)")
    r = root.bii_rig
    path = bpy.path.abspath(path or r.export_path)
    if not path:
        raise RigError("no file given")
    if not path.lower().endswith(".dequip"):
        path += ".dequip"
    keep = r.keep_capabilities if keep_capabilities is None else keep_capabilities
    errors = [t for s, t in problems(root) if s == "ERROR"]
    if errors:
        raise RigError("; ".join(errors))

    base = None
    if keep and os.path.isfile(path):
        try:
            base = dequip_format.read(path).model
        except (ValueError, OSError, KeyError) as e:
            raise RigError(f"{path} exists but cannot be read ({e}); delete it or turn off Keep Capabilities")
        if base.get("EquipmentId"):
            r.equipment_id = base["EquipmentId"]
    rig_props.ensure_ids(root)
    objs = rig_props.rig_objects(root)
    name = _safe_name(root.name)
    with tempfile.TemporaryDirectory() as tmp:
        fbx_path = os.path.join(tmp, name + ".fbx")
        texture_dir = os.path.join(tmp, "textures")
        os.makedirs(texture_dir)
        with _PreviewStash(objs), textures.StagedTextures(objs, texture_dir) as staged:
            write_fbx(objs, fbx_path)
        with _PreviewStash(objs):
            roots, details = build(root, fbx_path)
        with open(fbx_path, "rb") as f:
            fbx_bytes = f.read()
        texture_errors = textures.check_fbx(fbx_binary.parse(fbx_bytes))
        if texture_errors:
            raise RigError("; ".join(texture_errors))
    entry = dequip_format.fbx_entry_name(r.geometry_id, name)
    model = dequip_format.equipment(r.equipment_id, r.geometry_id, entry, roots, base)
    generated = ik_capabilities(root, {o: o.bii_rig.element_id for o in objs})
    model["CustomCapabilities"] = merge_capabilities(model["CustomCapabilities"], generated)
    crane_variables = None
    hook = r.crane_hook
    if hook is not None:
        cap = next(c for c in generated if c["Signature"]["Id"]["Guid"] == hook.bii_rig.ik_capability_id)
        crane_variables = crane_type.apply(model, r.equipment_id, hook.bii_rig.element_id, hook.name, cap)
    dequip_format.write(path, model, fbx_bytes, entry)
    r.export_path = path
    kept = bool(base) and bool(base.get("CustomCapabilities") or base.get("EquipmentTypeImplementations"))
    return {"path": path, "equipment_id": r.equipment_id, "elements": details["elements"],
            "joints": details["joints"], "kept_capabilities": kept,
            "ik_capabilities": [c["Signature"]["Name"] for c in generated],
            "crane": None if crane_variables is None else {"hook": hook.name, "added_variables": crane_variables},
            "textures_converted": staged.converted,
            "warnings": [t for s, t in problems(root) if s == "WARNING"]}


# ------------------------------------------------------------------ check

def _unity_from_blender_map(root, model, fbx_roots):
    """4x4 map from Blender world to dProB equipment space, measured on the root."""
    rest = rig_props.rest_world(root)
    b = _np(rest)
    el = fbx_roots[0]
    g = el.global_transform.copy()
    g[:3, 3] -= el.position          # the root's origin is the equipment's origin
    turn = np.eye(4)
    turn[:3, :3] = dprob_math_C @ _np(rig_props.forward_turn(root)) @ dprob_math_C
    return turn @ g @ np.linalg.inv(b)


def _dprob_state(obj, el_rotation, unity_from_blender, rest_rotation):
    """dProB joint value for the object's preview value. rest_rotation: the object's world
    rotation in the rest pose of the whole rig."""
    j = obj.bii_rig
    v = rig_props.preview_value(j)
    if j.joint_type == "REVOLUTE":
        return v
    if j.joint_type == "PRISMATIC":
        a_w = np.array(rig_props.world_axis(obj))
        return float(np.linalg.norm(unity_from_blender[:3, :3] @ a_w)) * v
    if j.joint_type == "BALL":
        rest = rest_rotation
        q_w = rest @ _np(Euler(v, "XYZ").to_matrix()) @ rest.T
        s = unity_from_blender[:3, :3]
        s = s / abs(np.linalg.det(s)) ** (1 / 3)
        return el_rotation.T @ (s @ q_w @ np.linalg.inv(s)) @ el_rotation
    return None


def check(root, path=None, samples=3, seed=1):
    """Compares a written .dequip with the Blender preview.

    Sets random joint values within the limits, evaluates the .dequip as dProB does
    (dprob_math.element_world_matrices) and compares each part's geometry placement with Blender.
    Returns {"max_error": meters, "samples": n, "worst": object name}. The preview is restored.
    """
    root = rig_props.rig_root(root)
    path = bpy.path.abspath(path or root.bii_rig.export_path)
    dq = dequip_format.read(path)
    model = dq.model
    fbx_entry = model["GeometryModels"][0]["Asset"]["FbxFile"]
    fbx_roots = dprob_math.hierarchy(fbx_binary.parse(dq.files[fbx_entry]))
    elements = {e["Name"]: e for e in dequip_format.walk(model["HierarchyRoots"])}
    fbx_elements = {e.name: e for r in fbx_roots for e in r.walk()}
    objs = [o for o in rig_props.rig_objects(root) if o.name in elements]
    jointed = rig_props.joints(root)
    saved = {o: rig_props.preview_value(o.bii_rig) for o in jointed}
    rng = random.Random(seed)
    worst, worst_name = 0.0, None
    errors = {}
    with _PreviewStash(objs):
        ufb = _unity_from_blender_map(root, model, fbx_roots)
        rest_rotations = {o: _np(o.matrix_world.to_3x3().normalized()) for o in jointed}
    try:
        for i in range(samples + 1):
            for o in jointed:
                j = o.bii_rig
                if i == 0:
                    value = (0.0, 0.0, 0.0) if j.joint_type == "BALL" else 0.0
                elif j.joint_type == "BALL":
                    value = tuple(rng.uniform(-1, 1) for _ in range(3))
                else:
                    lim = rig_props.limits(j) or (-math.pi, math.pi)
                    value = rng.uniform(*lim)
                rig_props.set_preview_value(o, value)
            bpy.context.view_layer.update()
            turn_u = dprob_math_C @ _np(rig_props.forward_turn(root)) @ dprob_math_C
            states = {elements[o.name]["Id"]: _dprob_state(o, turn_u @ fbx_elements[o.name].rotation, ufb,
                                                           rest_rotations[o])
                      for o in jointed}
            worlds = dprob_math.element_world_matrices(model["HierarchyRoots"], states)
            for o in objs:
                e = elements[o.name]
                geo = worlds[e["Id"]].copy()
                geo[:3, :3] = geo[:3, :3] @ dprob_math.scale_and_shear(e)
                expected = ufb @ _np(o.matrix_world)
                err = float(np.abs(geo - expected).max())
                errors[o.name] = max(err, errors.get(o.name, 0.0))
                if err > worst:
                    worst, worst_name = err, o.name
    finally:
        for o, v in saved.items():
            rig_props.set_preview_value(o, v)
        bpy.context.view_layer.update()
    return {"max_error": worst, "samples": samples, "worst": worst_name, "errors": errors}

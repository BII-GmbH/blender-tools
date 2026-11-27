"""dProB's FBX import and rig math, ported to numpy.

dProB (Unity) builds an equipment's hierarchy from an FBX file in
`FbxLoadedAsset.GetHierarchy` and `ModelHierarchy.From` (BII.Common.Equipment). This module repeats
those steps on the FBX that Blender wrote, so the poses and scale/shear matrices in the exported
.dequip are the ones dProB would compute itself when the FBX were imported in its configurator.

Conventions (as in dProB):
- 4x4 matrices act on column vectors; Unity's Matrix4x4 and float4x4 do the same.
- Rotations are 3x3 matrices here and (x, y, z, w) quaternions in the .dequip.
- Unity's frame is left-handed, but the numbers are ordinary matrices: Quaternion.AngleAxis(a, v)
  is the usual rotation matrix about v. Handedness only shows when a mirror is involved.
"""

import math

import numpy as np

from . import fbx_binary


# ------------------------------------------------------------------ basics

def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def axis_angle(axis, angle):
    """Rotation matrix about axis by angle (radians), right-hand rule (= Quaternion.AngleAxis)."""
    x, y, z = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    return np.array([
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ])


def trs(t, r, s):
    m = np.eye(4)
    m[:3, :3] = np.asarray(r) @ np.diag(s)
    m[:3, 3] = t
    return m


def pose_matrix(pos, rot):
    return trs(pos, rot, (1.0, 1.0, 1.0))


def look_rotation_safe(forward, up):
    """Unity.Mathematics quaternion.LookRotationSafe as a 3x3 matrix."""
    forward = np.asarray(forward, dtype=float)
    up = np.asarray(up, dtype=float)
    fl, ul = forward @ forward, up @ up
    if not (fl > 1e-35 and ul > 1e-35):
        return np.eye(3)
    forward = forward / math.sqrt(fl)
    up = up / math.sqrt(ul)
    t = np.cross(up, forward)
    tl = t @ t
    if not (1e-35 < tl < 1e35):
        return np.eye(3)
    t = t / math.sqrt(tl)
    return np.column_stack([t, np.cross(forward, t), forward])


def quat_from_matrix(m):
    """(x, y, z, w) of a rotation matrix."""
    m = np.asarray(m, dtype=float)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    q /= np.linalg.norm(q)
    if q[3] < 0:
        q = -q
    return tuple(float(v) for v in q)


def matrix_from_quat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


# ------------------------------------------------------------------ FBX import (dProB)

def common_global_transform(settings):
    """FbxGlobalSettings.CommonGlobalTransform: axis conversion and unit scale of the file."""
    def get(name, default):
        v = settings.get(name)
        return v[0] if v else default

    up, up_sign = int(get("UpAxis", 1)), int(get("UpAxisSign", 1))
    front, front_sign = int(get("FrontAxis", 2)), -int(get("FrontAxisSign", 1))  # dProB negates it
    coord, coord_sign = int(get("CoordAxis", 0)), int(get("CoordAxisSign", 1))
    unit = float(get("UnitScaleFactor", 1.0))
    default_scale = 100.0 / unit  # "For some reason, (Blender) models are always scaled x100"
    e = np.eye(3)
    m = np.eye(4)
    m[:3, 0] = e[coord] * coord_sign
    m[:3, 1] = e[up] * up_sign
    m[:3, 2] = e[front] * front_sign
    return m @ np.diag([1 / default_scale] * 3 + [1.0])


class FbxModel:
    def __init__(self, node):
        self.id = node.props[0]
        self.name = fbx_binary.model_name(node.props[1])
        self.type = node.props[2] if len(node.props) > 2 else ""
        p = fbx_binary.properties70(node)
        t = np.array(p.get("Lcl Translation", (0, 0, 0))[:3], dtype=float)
        r = p.get("Lcl Rotation", (0, 0, 0))[:3]
        s = np.array(p.get("Lcl Scaling", (1, 1, 1))[:3], dtype=float)
        # FbxModel.rotationFrom: Euler(0,0,z) * Euler(0,y,0) * Euler(x,0,0)
        self.rotation = rot_z(r[2]) @ rot_y(r[1]) @ rot_x(r[0])
        self.scale = s
        g = p.get("GeometricTranslation")
        self.position = t + self.rotation @ (np.array(g[:3], dtype=float) * s) if g else t
        self.children = []
        self.parent = None
        self.has_geometry = False

    @property
    def local(self):
        return trs(self.position, self.rotation, self.scale)


def read_models(root):
    """Models of a parsed FBX file with their parents and children (dProB's FbxConnections)."""
    objects = root.first("Objects")
    models = {n.props[0]: FbxModel(n) for n in objects.all("Model")} if objects else {}
    geometries = {n.props[0] for n in objects.all("Geometry")} if objects else set()
    conns = root.first("Connections")
    for c in (conns.all("C") if conns else []):
        if len(c.props) < 3 or c.props[0] != "OO":
            continue
        child, parent = c.props[1], c.props[2]
        if child in models and parent in models:
            models[child].parent = models[parent]
            models[parent].children.append(models[child])
        elif child in geometries and parent in models:
            models[parent].has_geometry = True
    return models


class Element:
    """One equipment hierarchy element, as ModelHierarchy.From makes it from an FBX model."""

    def __init__(self, model, global_transform, parent):
        self.model = model
        self.name = model.name
        self.global_transform = global_transform          # G: geometry local -> Unity world
        self.position = global_transform[:3, 3].copy()
        self.rotation = look_rotation_safe(global_transform[:3, 2], global_transform[:3, 1])
        # inverse(TRS(position, rotation, 1)) * G, upper 3x3
        self.scale_and_shear = self.rotation.T @ global_transform[:3, :3]
        self.parent = parent
        self.children = []

    @property
    def global_pose(self):
        return pose_matrix(self.position, self.rotation)

    @property
    def local_position(self):
        if self.parent is None:
            return self.position
        return self.parent.rotation.T @ (self.position - self.parent.position)

    @property
    def local_rotation(self):
        if self.parent is None:
            return self.rotation
        return self.parent.rotation.T @ self.rotation

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def hierarchy(root_node):
    """Root elements dProB makes from a parsed FBX file (FbxLoadedAsset.GetHierarchy)."""
    settings = fbx_binary.properties70(root_node.first("GlobalSettings"))
    common = common_global_transform(settings)
    models = read_models(root_node)

    def make(model, parent_transform, parent):
        g = parent_transform @ model.local
        e = Element(model, g, parent)
        e.children = [make(c, g, e) for c in model.children]
        return e

    return [make(m, common, None) for m in models.values() if m.parent is None]


# ------------------------------------------------------------------ rig evaluation (dProB)

def joint_offset(joint, value):
    """Local offset of a joint for a state value (RigJoint.State.OffsetFor).

    joint: dict as in the .dequip ("$case" Revolute/Prismatic/Ball/Fixed); value: radians,
    meters or a 3x3 rotation for Ball.
    """
    m = np.eye(4)
    case = joint["$case"]
    if case == "Revolute" and value:
        a = joint["Axis"]
        m[:3, :3] = axis_angle((a["X"], a["Y"], a["Z"]), value)
    elif case == "Prismatic" and value:
        a = joint["Axis"]
        axis = np.array((a["X"], a["Y"], a["Z"]))
        m[:3, 3] = axis / np.linalg.norm(axis) * value
    elif case == "Ball" and value is not None:
        m[:3, :3] = value
    return m


def element_world_matrices(roots, states=None):
    """{element id: world matrix of its pose} for .dequip hierarchy roots and joint states.

    roots: the "HierarchyRoots" list of a .dequip model; states: {element id: value}.
    The geometry of an element is drawn at world @ GeometryScaleAndShear.
    """
    states = states or {}
    out = {}

    def visit(el, parent_world):
        p, r = el["Pose"]["Position"], el["Pose"]["Rotation"]
        local = pose_matrix((p["X"], p["Y"], p["Z"]), matrix_from_quat((r["X"], r["Y"], r["Z"], r["W"])))
        world = parent_world @ local @ joint_offset(el["JointConstraint"], states.get(el["Id"]))
        out[el["Id"]] = world
        for c in el["Children"]:
            visit(c, world)

    for r in roots:
        visit(r, np.eye(4))
    return out


def scale_and_shear(el):
    m = el["GeometryScaleAndShear"]
    return np.column_stack([[m[c]["X"], m[c]["Y"], m[c]["Z"]] for c in ("C0", "C1", "C2")])

"""dProB's Inverse Kinematics node, ported to numpy, plus the coupling loop of the generated
IK capabilities.

The solver repeats `InverseKinematicsNode` (BII.Common.VisualScripting, dProB 2026.2): damped least
squares over the chain from the root joint to the end effector, lambda 0.1, step norm at most 0.1,
scalar limits clamped after every step. Like dProB it measures the orientation error in degrees,
times the orientation weight.

Everything here is in dProB's equipment space (meters, Unity axes) and needs no Blender, so it can
run in a worker thread. ik_chain.py builds the chains from a Blender rig.

A chain is a list of Joint (root first). Joint values: radians (revolute), meters (prismatic),
3x3 rotation matrix (ball).
"""

import math

import numpy as np

LAMBDA = 0.1
STEP_MAX = 0.1
DEFAULT_ITERATIONS = 500          # the node's default
DEFAULT_ORIENTATION_WEIGHT = 0.05


class Joint:
    """One rig joint of a chain.

    key: any hashable id (the object name in Blender).
    kind: "REVOLUTE", "PRISMATIC" or "BALL".
    axis: unit axis in the joint's own frame (revolute, prismatic).
    limits: (min, max) or None (unlimited revolute, ball).
    initial_pose: 4x4 rigid matrix: the joint's frame relative to the previous joint's end frame
        (the first joint: relative to the chain's base frame).
    """

    def __init__(self, key, kind, axis=None, limits=None, initial_pose=None):
        self.key = key
        self.kind = kind
        self.axis = None if axis is None else np.asarray(axis, dtype=float) / np.linalg.norm(axis)
        self.limits = limits
        self.initial_pose = np.eye(4) if initial_pose is None else np.asarray(initial_pose, dtype=float)

    @property
    def dofs(self):
        return 3 if self.kind == "BALL" else 1

    def zero(self):
        return np.eye(3) if self.kind == "BALL" else 0.0


def axis_angle(axis, angle):
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    return np.array([
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ])


def offset(joint, value):
    """RigJoint.State.OffsetFor: the joint's motion as a 4x4 matrix."""
    m = np.eye(4)
    if joint.kind == "REVOLUTE":
        m[:3, :3] = axis_angle(joint.axis, value)
    elif joint.kind == "PRISMATIC":
        m[:3, 3] = joint.axis * value
    elif joint.kind == "BALL":
        m[:3, :3] = value
    return m


def forward(chain, values, base=None):
    """(start frames, end frames) of the joints for values (list, chain order)."""
    parent = np.eye(4) if base is None else base
    starts, ends = [], []
    for j, v in zip(chain, values):
        s = parent @ j.initial_pose
        e = s @ offset(j, v)
        starts.append(s)
        ends.append(e)
        parent = e
    return starts, ends


def rotation_vector(m):
    """Axis * angle (radians) of a rotation matrix, angle in [0, pi]."""
    c = max(-1.0, min(1.0, (np.trace(m) - 1) / 2))
    angle = math.acos(c)
    if angle < 1e-12:
        return np.zeros(3)
    if math.pi - angle < 1e-6:
        # axis from the symmetric part
        b = (m + np.eye(3)) / 2
        i = int(np.argmax(np.diag(b)))
        axis = b[:, i] / math.sqrt(max(b[i, i], 1e-30))
        return axis * angle
    axis = np.array([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1]]) / (2 * math.sin(angle))
    return axis * angle


def error(target, end, orientation_weight):
    """6-vector: position error (meters), orientation error (degrees * weight)."""
    e = np.empty(6)
    e[:3] = target[:3, 3] - end[:3, 3]
    # Quaternion.ToAngleAxis gives degrees
    e[3:] = orientation_weight * np.degrees(rotation_vector(target[:3, :3] @ end[:3, :3].T))
    return e


def jacobian(chain, starts, ends):
    n = sum(j.dofs for j in chain)
    jac = np.zeros((6, n))
    tip = ends[-1][:3, 3]
    col = 0
    for j, s, e in zip(chain, starts, ends):
        r = tip - s[:3, 3]
        if j.kind == "REVOLUTE":
            a = s[:3, :3] @ j.axis
            jac[:3, col] = np.cross(a, r)
            jac[3:, col] = a
            col += 1
        elif j.kind == "PRISMATIC":
            jac[:3, col] = s[:3, :3] @ j.axis
            col += 1
        else:
            for k in range(3):
                a = e[:3, k]
                jac[:3, col] = np.cross(a, r)
                jac[3:, col] = a
                col += 1
    return jac


def dls_step(jac, err):
    a = jac @ jac.T + LAMBDA * LAMBDA * np.eye(6)
    dq = jac.T @ np.linalg.solve(a, err)
    norm = float(np.linalg.norm(dq))
    if norm > STEP_MAX and norm > 1e-12:
        dq *= STEP_MAX / norm
    return dq


def _clamp(v, lim):
    if lim is None:
        return v
    return min(max(v, lim[0]), lim[1])


def solve(chain, target, initial, iterations=DEFAULT_ITERATIONS,
          orientation_weight=DEFAULT_ORIENTATION_WEIGHT, base=None):
    """The IK node: values (list, chain order) that bring the last joint's end frame to target.

    target: 4x4 rigid matrix in the same space as base (the equipment space).
    initial: start values (list, chain order).
    """
    values = [np.array(v, dtype=float) if j.kind == "BALL" else float(v) for j, v in zip(chain, initial)]
    for _ in range(iterations):
        starts, ends = forward(chain, values, base)
        dq = dls_step(jacobian(chain, starts, ends), error(target, ends[-1], orientation_weight))
        col = 0
        for i, j in enumerate(chain):
            if j.kind == "BALL":
                rv = dq[col:col + 3]
                angle = float(np.linalg.norm(rv))
                if angle >= 1e-12:
                    values[i] = values[i] @ axis_angle(rv / angle, angle)
                col += 3
            else:
                values[i] = _clamp(values[i] + dq[col], j.limits)
                col += 1
    return values


# ------------------------------------------------------------------ rig of a .dequip

def _pose(p):
    pos, rot = p["Position"], p["Rotation"]
    x, y, z, w = rot["X"], rot["Y"], rot["Z"], rot["W"]
    m = np.eye(4)
    m[:3, :3] = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m[:3, 3] = (pos["X"], pos["Y"], pos["Z"])
    return m


def rig_from_model(roots):
    """dProB's rig of a .dequip hierarchy (ScriptableEquipmentAsset: Fixed elements fold into
    the next joint's pose). Returns (joints {element id: Joint}, parents {element id: parent
    joint id or None}, fixed {element id: (offset to its joint, joint id or None)})."""
    joints, parents, fixed = {}, {}, {}

    def visit(el, offset, parent):
        jc = el["JointConstraint"]
        if jc["$case"] == "Fixed":
            fixed[el["Id"]] = (offset, parent)
            for c in el["Children"]:
                visit(c, offset @ _pose(c["Pose"]), parent)
            return
        kind = jc["$case"].upper()
        axis = limits = None
        if kind in ("REVOLUTE", "PRISMATIC"):
            a = jc["Axis"]
            axis = (a["X"], a["Y"], a["Z"])
        if kind == "REVOLUTE" and jc.get("Limits"):
            limits = (jc["Limits"]["MinRadians"], jc["Limits"]["MaxRadians"])
        if kind == "PRISMATIC":
            limits = (jc["MinMeters"], jc["MaxMeters"])
        joints[el["Id"]] = Joint(el["Id"], kind, axis, limits, offset)
        parents[el["Id"]] = parent
        for c in el["Children"]:
            visit(c, _pose(c["Pose"]), el["Id"])

    for r in roots:
        visit(r, _pose(r["Pose"]), None)
    return joints, parents, fixed


def chain_to(joints, parents, end_id, root_id=None):
    """Joints from the top (or root_id) down to end_id, as the IK node collects them."""
    chain = []
    j = end_id
    while j is not None:
        chain.insert(0, joints[j])
        if j == root_id:
            break
        j = parents[j]
    return chain


# ------------------------------------------------------------------ couplings

class Coupling:
    """How a follower joint's value is set after each IK pass.

    mode "FOLLOW": value = offset + sum(factor * value of leader)
    mode "HOLD": value = the value the joint had when the capability started
    """

    def __init__(self, key, mode, terms=(), offset=0.0):
        self.key = key
        self.mode = mode
        self.terms = list(terms)          # [(leader key, factor)]
        self.offset = offset


def apply_couplings(couplings, values, start_values):
    """Sets the followers in values (dict key -> value), in the given order (leaders first).
    start_values: values when the capability started (also for leaders outside the chain)."""
    for c in couplings:
        if c.mode == "HOLD":
            values[c.key] = start_values[c.key]
        else:
            v = c.offset
            for leader, factor in c.terms:
                v += factor * values.get(leader, start_values[leader])
            values[c.key] = v
    return values


def seed_values(chain, start_values, margin, skip=()):
    """Start values of the IK: limited joints at least margin * range inside their limits.
    Joints in skip (Follow, Hold) keep their value."""
    seeds = {}
    if margin <= 0:
        return seeds
    for j in chain:
        if j.key in skip or j.kind == "BALL" or j.limits is None:
            continue
        lo, hi = j.limits
        m = (hi - lo) * margin
        seeds[j.key] = min(max(start_values[j.key], lo + m), hi - m)
    return seeds


def aim_angle(axis_point, axis, point, target):
    """Angle about the axis (through axis_point) that turns point towards target: the signed
    angle between their offsets from the axis, projected on the plane normal to the axis (as
    dProB's Signed Angle on Plane macro computes it)."""
    n = np.asarray(axis, dtype=float)
    n = n / np.linalg.norm(n)
    a = np.asarray(point, dtype=float) - axis_point
    b = np.asarray(target, dtype=float) - axis_point
    a = a - n * (a @ n)
    b = b - n * (b @ n)
    la, lb = np.linalg.norm(a), np.linalg.norm(b)
    if la < 1e-9 or lb < 1e-9:
        return 0.0
    a, b = a / la, b / lb
    return math.atan2(float(np.cross(a, b) @ n), float(a @ b))


def solve_coupled(chain, target, start_values, couplings=(), loops=10, iterations=100,
                  orientation_weight=DEFAULT_ORIENTATION_WEIGHT, base=None, seed_margin=0.0,
                  extra_seeds=None):
    """What a generated IK capability computes: values (dict key -> value) for the chain and the
    followers. Without couplings one IK pass; with couplings `loops` passes, the followers set
    after each, the next pass starting from the result. The first pass starts from
    seed_values(seed_margin) and extra_seeds (for example the aimed slewing).

    start_values: dict key -> value of every joint involved (the equipment's state).
    """
    values = seed_values(chain, start_values, seed_margin, {c.key for c in couplings})
    values.update(extra_seeds or {})
    passes = loops if couplings else 1
    for _ in range(passes):
        initial = [values.get(j.key, start_values[j.key]) for j in chain]
        result = solve(chain, target, initial, iterations, orientation_weight, base)
        values.update({j.key: v for j, v in zip(chain, result)})
        if couplings:
            apply_couplings(couplings, values, start_values)
    return values


def residual(chain, target, values, base=None):
    """(position error in meters, orientation error in degrees) of the chain's end."""
    _, ends = forward(chain, [values[j.key] for j in chain], base)
    e = error(target, ends[-1], 1.0)
    return float(np.linalg.norm(e[:3])), float(np.linalg.norm(e[3:]))

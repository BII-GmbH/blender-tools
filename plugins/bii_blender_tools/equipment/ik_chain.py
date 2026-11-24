"""IK chains of a Blender rig in dProB's equipment space.

dProB solves IK on its rig joints (ScriptableEquipmentAsset: Fixed elements are folded into the
pose of the next joint). This module builds the same joints straight from the Blender objects,
without writing an FBX:

- Equipment space: Unity axes (Blender x, y, z -> x, z, y), meters, origin at the rig root's origin.
- An element's frame: position C (p - origin) * unit scale, rotation LookRotationSafe of its mapped
  Z and Y axes, as dProB's FBX import computes it (dprob_math.Element).
- Joint axes and limits are converted as in rig_export._joint_model: a revolute axis is negated
  (the map is a mirror) so angles keep their sign, prismatic offsets are scaled to meters.

All of this uses the rest pose computed from the objects' own transforms, so it neither changes
the scene nor needs a depsgraph update.
"""

import math

import bpy
import numpy as np
from mathutils import Euler, Matrix

from . import dprob_math, ik_solver, rig_props
from .rig_props import RigError

C = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])   # Blender world axes -> Unity axes
D = np.diag([-1.0, 1.0, 1.0])                            # object axes -> element frame


def _np(m):
    return np.array([list(r) for r in m], dtype=float)


def unit_scale():
    return bpy.context.scene.unit_settings.scale_length or 1.0


def rest_worlds(root):
    """{object: 4x4 world matrix with every joint of the rig at rest}."""
    out = {}

    def visit(o, parent_world):
        if o.parent is None:
            w = _np(rig_props.rest_basis(o))
        else:
            w = parent_world @ _np(o.matrix_parent_inverse) @ _np(rig_props.rest_basis(o))
        out[o] = w
        for c in kids.get(o, ()):
            if rig_props.is_rig_object(c):
                visit(c, w)

    kids = rig_props.children_map()
    above = _np(root.parent.matrix_world) if root.parent is not None else np.eye(4)
    visit(root, above)
    return out


class Space:
    """Maps between Blender world matrices and dProB equipment space for one rig."""

    def __init__(self, root, worlds=None):
        self.root = root
        self.worlds = worlds or rest_worlds(root)
        self.origin = self.worlds[root][:3, 3].copy()
        self.k = unit_scale()
        self.F = _np(rig_props.forward_turn(root))          # model front -> Blender +Y

    def pose(self, world):
        """dProB element frame (4x4 rigid) for a Blender world matrix."""
        w = np.asarray(world, dtype=float)
        r = self.F @ w[:3, :3]
        m = np.eye(4)
        m[:3, :3] = dprob_math.look_rotation_safe(C @ r[:, 2], C @ r[:, 1])
        m[:3, 3] = C @ (self.F @ (w[:3, 3] - self.origin)) * self.k
        return m

    def world(self, pose):
        """Blender world matrix (rigid, no scale) for a dProB element frame."""
        m = np.eye(4)
        m[:3, :3] = self.F.T @ C.T @ pose[:3, :3] @ D
        m[:3, 3] = self.F.T @ (C.T @ pose[:3, 3]) / self.k + self.origin
        return m

    def rest_pose(self, obj):
        return self.pose(self.worlds[obj])

    def world_axis(self, obj):
        """Unit joint axis in Blender world space, rest pose."""
        o = obj
        if o.parent is None:
            ps = np.eye(3)
        else:
            ps = (self.worlds.get(o.parent, _np(o.parent.matrix_world)) @ _np(o.matrix_parent_inverse))[:3, :3]
        a = ps @ _np(rig_props.rest_rotation(o)) @ np.array(rig_props.local_axis(o.bii_rig))
        return a / np.linalg.norm(a)

    def joint(self, obj, initial_pose):
        """ik_solver.Joint of a jointed object; initial_pose relative to the previous joint."""
        j = obj.bii_rig
        if j.joint_type == "BALL":
            return ik_solver.Joint(obj.name, "BALL", initial_pose=initial_pose)
        rot = self.rest_pose(obj)[:3, :3]
        u = C @ self.F @ self.world_axis(obj)
        local = rot.T @ (u / np.linalg.norm(u))
        if j.joint_type == "REVOLUTE":
            return ik_solver.Joint(obj.name, "REVOLUTE", -local, rig_props.limits(j), initial_pose)
        lo, hi = rig_props.limits(j)
        return ik_solver.Joint(obj.name, "PRISMATIC", local, (lo * self.k, hi * self.k), initial_pose)

    # ---- values: Blender preview <-> dProB state
    def to_dprob(self, obj, value):
        kind = obj.bii_rig.joint_type
        if kind == "PRISMATIC":
            return value * self.k
        if kind == "BALL":
            return D @ _np(Euler(value, "XYZ").to_matrix()) @ D
        return value

    def to_blender(self, obj, value):
        kind = obj.bii_rig.joint_type
        if kind == "PRISMATIC":
            return float(value) / self.k
        if kind == "BALL":
            return tuple(Matrix([list(r) for r in D @ value @ D]).to_euler("XYZ"))
        return float(value)


def parent_joint(obj, root):
    """Nearest jointed ancestor of obj inside the rig, or None."""
    p = obj.parent
    while p is not None:
        if p.bii_rig.joint_type != "FIXED":
            return p
        if p is root:
            return None
        p = p.parent
    return None


def end_joint(target):
    """(joint object that the IK moves to the target, the target is that joint itself)."""
    root = rig_props.rig_root(target)
    if target.bii_rig.joint_type != "FIXED":
        return target, True
    return parent_joint(target, root), False


class Setup:
    """Everything to solve one IK target, taken from the scene (main thread); solve() is pure."""

    def __init__(self, target):
        target = target if not isinstance(target, str) else bpy.data.objects[target]
        root = rig_props.rig_root(target)
        if root is None:
            raise RigError(f"{target.name} is not part of a rig")
        end, is_joint = end_joint(target)
        if end is None:
            raise RigError(f"{target.name} has no joint above it; nothing can move it")
        self.root, self.target, self.end = root, target, end
        r = target.bii_rig
        self.iterations, self.loops, self.weight = r.ik_iterations, r.ik_loops, r.ik_orientation_weight
        self.seed_margin = r.ik_seed_margin
        self.space = s = Space(root)

        # every joint of the rig with its InitialPose relative to its parent joint
        all_joints = rig_props.joints(root)
        self.parent = {o: parent_joint(o, root) for o in all_joints}
        self.joints = {}
        for o in all_joints:
            p = self.parent[o]
            rel = np.linalg.inv(s.rest_pose(p)) @ s.rest_pose(o) if p is not None else s.rest_pose(o)
            self.joints[o] = s.joint(o, rel)

        # the chain: end joint up to the root joint (or the top)
        chain = []
        o = end
        stop = r.ik_root
        while o is not None:
            chain.insert(0, o)
            if o is stop:
                break
            o = self.parent[o]
        if stop is not None and stop not in chain:
            raise RigError(f"the IK root joint {stop.name} of {target.name} is not above it")
        self.chain_objs = chain
        self.chain = [self.joints[o] for o in chain]
        self.above = []
        o = self.parent[chain[0]]
        while o is not None:
            self.above.insert(0, o)
            o = self.parent[o]
        # the target relative to its joint (Fixed parts between)
        self.end_offset = None if is_joint else np.linalg.inv(s.rest_pose(end)) @ s.rest_pose(target)
        ball = [o.name for o in chain if o.bii_rig.joint_type == "BALL"]
        self.has_ball = bool(ball)

        # couplings in dProB units, leaders first; only those that concern the chain: joints of
        # the chain, and Follow joints whose leaders (also indirectly) are in the chain
        involved = set(chain)
        order = []
        for o in rig_props.followers(root):
            j = o.bii_rig
            if o in involved or (j.drive == "FOLLOW" and any(t.leader in involved for t in j.drive_terms)):
                involved.add(o)
                order.append(o)
        self.couplings = []
        self.follower_objs = order
        for o in order:
            j = o.bii_rig
            if j.drive == "HOLD":
                self.couplings.append(ik_solver.Coupling(o.name, "HOLD"))
            else:
                off = rig_props.drive_offset(j)
                self.couplings.append(ik_solver.Coupling(
                    o.name, "FOLLOW", [(t.leader.name, t.factor) for t in j.drive_terms],
                    s.to_dprob(o, off)))
        self.objects = {o.name: o for o in all_joints}
        self.aim = self._find_aim_joint(s)
        self.capture_state()

    AIM_MIN_RADIUS = 0.5      # meters: an unlimited joint this close to the target only spins it

    def _find_aim_joint(self, s):
        """The top-most free unlimited revolute joint of the chain that swings the target round
        (a slewing ring): the IK starts with it turned towards the target. Without that, a
        target exactly behind it gives the solver no gradient to turn it at all."""
        coupled = {c.key for c in self.couplings}
        target = s.rest_pose(self.target)[:3, 3]
        for o in self.chain_objs:
            j = self.joints[o]
            if j.kind != "REVOLUTE" or j.limits is not None or j.key in coupled:
                continue
            frame = s.rest_pose(o)
            axis = frame[:3, :3] @ j.axis
            d = target - frame[:3, 3]
            if np.linalg.norm(d - axis * (d @ axis)) >= self.AIM_MIN_RADIUS:
                return o
        return None

    def aim_seed(self, destination, start_values=None):
        """{joint name: start value} turning the aim joint towards the destination, as the
        generated graph computes it, or {}."""
        if self.aim is None:
            return {}
        values = start_values or self.start_values
        path = [o for o in self.above + self.chain_objs]
        path = path[:path.index(self.aim) + 1]
        _, ends = ik_solver.forward([self.joints[o] for o in path], [values[o.name] for o in path])
        frame = ends[-1]
        j = self.joints[self.aim]
        current = self._pose_at(values)[:3, 3]
        angle = ik_solver.aim_angle(frame[:3, 3], frame[:3, :3] @ j.axis, current, destination[:3, 3])
        return {j.key: values[j.key] + angle}

    def _pose_at(self, values):
        _, ends = ik_solver.forward(self.chain, [values[j.key] for j in self.chain], self.base(values))
        return ends[-1] if self.end_offset is None else ends[-1] @ self.end_offset

    def capture_state(self):
        """The joints' current preview values are the equipment's state when the IK starts."""
        self.start_values = {o.name: self.space.to_dprob(o, rig_props.preview_value(o.bii_rig))
                             for o in self.joints}

    def base(self, values=None):
        """Frame of the chain's base: the joints above the chain at their values."""
        values = values or self.start_values
        if not self.above:
            return np.eye(4)
        _, ends = ik_solver.forward([self.joints[o] for o in self.above],
                                    [values[o.name] for o in self.above])
        return ends[-1]

    # ---- targets
    def destination(self, target_world):
        """The capability's Destination (4x4) for a Blender world matrix of the target part: the
        part's frame in the equipment, as dProB's attachment point poses are."""
        return self.space.pose(target_world)

    def current_pose(self):
        """The target part's frame in the equipment at the start values."""
        values = self.start_values
        _, ends = ik_solver.forward(self.chain, [values[j.key] for j in self.chain], self.base())
        return ends[-1] if self.end_offset is None else ends[-1] @ self.end_offset

    def turned_rotation(self, destination, start_values=None):
        """The target part's current rotation, turned with the aim joint towards the destination
        (the rotation it will about have)."""
        values = start_values or self.start_values
        rot = self._pose_at(values)[:3, :3]
        if self.aim is None:
            return rot
        angle = self.aim_seed(destination, values)[self.aim.name] - values[self.aim.name]
        path = (self.above + self.chain_objs)[:(self.above + self.chain_objs).index(self.aim) + 1]
        _, ends = ik_solver.forward([self.joints[o] for o in path], [values[o.name] for o in path])
        axis = ends[-1][:3, :3] @ self.joints[self.aim].axis
        return ik_solver.axis_angle(axis / np.linalg.norm(axis), angle) @ rot

    def joint_destination(self, destination, position_only=False, start_values=None):
        """Target frame of the end joint for a Destination, as the generated graph computes it.
        Rotation: the Destination's (or with position_only the part's current rotation turned
        with the aim joint), times inverse(local pose). Position: the Destination's, with the
        part's offset from its joint turned like the part will be (exact for a hook hanging below
        its joint, also when the IK cannot or need not reach the Destination's rotation)."""
        turned = np.eye(4)
        turned[:3, 3] = destination[:3, 3]
        turned[:3, :3] = self.turned_rotation(destination, start_values)
        if self.end_offset is None:
            return turned if position_only else destination
        inv = np.linalg.inv(self.end_offset)
        target = (turned if position_only else destination) @ inv
        target[:3, 3] = (turned @ inv)[:3, 3]
        return target

    def current_world(self):
        """Blender world matrix (rigid) of the target part at the current preview values."""
        return self.space.world(self.current_pose())

    def solve(self, destination, start_values=None, position_only=False):
        """dProB values {joint name: value} for a dProB destination of the target part, as the
        generated capability computes them (its Position Only input = position_only); also the
        residual errors."""
        start = dict(start_values or self.start_values)
        dest = self.joint_destination(destination, position_only, start)
        values = ik_solver.solve_coupled(
            self.chain, dest, start, self.couplings, self.loops, self.iterations, self.weight,
            self.base(start), self.seed_margin, self.aim_seed(destination, start))
        pos_err, rot_err = ik_solver.residual(self.chain, dest, values, self.base(start))
        return values, pos_err, rot_err

    def seed_ranges(self):
        """{joint name: (min, max)} the IK's start values are clamped to (dProB units)."""
        skip = {c.key for c in self.couplings}
        out = {}
        if self.seed_margin <= 0:
            return out
        for j in self.chain:
            if j.key in skip or j.kind == "BALL" or j.limits is None:
                continue
            lo, hi = j.limits
            m = (hi - lo) * self.seed_margin
            out[j.key] = (lo + m, hi - m)
        return out

    def preview_values(self, values):
        """{object: Blender preview value} for dProB values."""
        return {self.objects[k]: self.space.to_blender(self.objects[k], v) for k, v in values.items()}

    def moved(self):
        """Objects whose value the capability sets: the chain and the Follow joints."""
        out = [o for o in self.chain_objs if o.bii_rig.drive != "HOLD"]
        out += [o for o in self.follower_objs if o.bii_rig.drive == "FOLLOW" and o not in out]
        return out

    def capability_spec(self, element_ids):
        """Input for ik_capability.build; element_ids: {object: element id}."""
        r = self.target.bii_rig
        eid = {o: element_ids[o] for o in self.joints}
        by_name = {o.name: o for o in self.joints}
        return {
            "id": r.ik_capability_id,
            "name": r.ik_name or f"Move {self.target.name}",
            "description": r.ik_description,
            "target_name": self.target.name,
            "target": (element_ids[self.target], self.target.name),
            "end": (eid[self.end], self.end.name),
            "attachment_point": self.end_offset is not None,
            "root": (eid[r.ik_root], r.ik_root.name) if r.ik_root is not None else None,
            "iterations": self.iterations,
            "loops": self.loops,
            "orientation_weight": self.weight,
            "seed_margin": self.seed_margin,
            "seeds": [eid[by_name[k]] for k in self.seed_ranges()],
            "end_path": [eid[o] for o in self.above + self.chain_objs],
            "aim": None if self.aim is None else {
                "joint": eid[self.aim],
                "path": [eid[o] for o in (self.above + self.chain_objs)[:(self.above + self.chain_objs).index(self.aim) + 1]],
            },
            "joints": {eid[o]: (o.name, o.bii_rig.joint_type) for o in self.joints},
            "chain": [eid[o] for o in self.chain_objs],
            "couplings": [(eid[by_name[c.key]], c.mode, [(eid[by_name[n]], f) for n, f in c.terms], c.offset)
                          for c in self.couplings],
            "moved": [eid[o] for o in self.moved()],
        }


def pose_tuple(m):
    """(position (x, y, z), rotation (x, y, z, w)) of a 4x4 rigid matrix."""
    return tuple(float(v) for v in m[:3, 3]), dprob_math.quat_from_matrix(m[:3, :3])


def pose_matrix(position, rotation):
    """4x4 from (x, y, z) and a quaternion (x, y, z, w)."""
    return dprob_math.pose_matrix(position, dprob_math.matrix_from_quat(rotation))


def degrees(v):
    return math.degrees(v)

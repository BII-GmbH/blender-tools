"""Joint preview in the viewport: joint gizmos, sweeping the joints through their range, reset."""

import math
import time

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import rig_props
from .rig_props import RigError

COLORS = {
    "REVOLUTE": (1.0, 0.55, 0.1, 1.0),
    "PRISMATIC": (0.2, 0.8, 1.0, 1.0),
    "BALL": (0.9, 0.3, 0.9, 1.0),
}
ARC_SEGMENTS = 48


# ------------------------------------------------------------------ sweep values

def sweep_value(j, phase):
    """Joint value at a phase (radians) of the sweep: back and forth through the range."""
    t = 0.5 - 0.5 * math.cos(phase)
    if j.joint_type == "REVOLUTE":
        lo, hi = rig_props.limits(j) or (-math.pi, math.pi)
        return lo + (hi - lo) * t
    if j.joint_type == "PRISMATIC":
        lo, hi = rig_props.limits(j)
        return lo + (hi - lo) * t
    if j.joint_type == "BALL":
        return (0.5 * math.sin(phase), 0.5 * math.sin(phase * 1.3 + 1.0), 0.5 * math.sin(phase * 0.7 + 2.0))
    return None


def set_all(root, values):
    """values: {object: value}; objects missing keep theirs."""
    for o, v in values.items():
        rig_props.set_preview_value(o, v)


def reset(root):
    """Puts every joint of the rig back to its rest pose."""
    for o in rig_props.rig_objects(root):
        j = o.bii_rig
        j.preview_angle = 0.0
        j.preview_length = 0.0
        j.preview_ball = (0.0, 0.0, 0.0)
        rig_props.clear_preview(o)


class BII_RIG_OT_sweep(bpy.types.Operator):
    bl_idname = "bii_rig.sweep"
    bl_label = "Play Joints"
    bl_description = ("Moves every joint of the rig back and forth through its range. "
                      "Esc or right click stops and restores the preview values")

    speed: bpy.props.FloatProperty(name="Speed", default=1.0, min=0.05, max=10.0,
                                   description="Back-and-forth cycles per 4 seconds")

    _timer = None

    @classmethod
    def poll(cls, context):
        return rig_props.rig_root(context.object) is not None

    def invoke(self, context, event):
        self.root = rig_props.rig_root(context.object)
        self.joints = rig_props.joints(self.root)
        if not self.joints:
            self.report({"WARNING"}, "The rig has no joints")
            return {"CANCELLED"}
        self.saved = {o: rig_props.preview_value(o.bii_rig) for o in self.joints}
        self.start = time.monotonic()
        self._timer = context.window_manager.event_timer_add(1 / 30, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set("Playing joints: Esc / right click to stop")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self.finish(context)
            return {"CANCELLED"}
        if event.type == "TIMER":
            phase = (time.monotonic() - self.start) * self.speed * math.pi / 2
            try:
                for i, o in enumerate(self.joints):
                    rig_props.set_preview_value(o, sweep_value(o.bii_rig, phase + i * 0.6))
            except (RigError, ReferenceError) as e:
                self.report({"ERROR"}, str(e))
                self.finish(context)
                return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def finish(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        for o, v in self.saved.items():
            try:
                rig_props.set_preview_value(o, v)
            except (RigError, ReferenceError):
                pass


class BII_RIG_OT_reset_preview(bpy.types.Operator):
    bl_idname = "bii_rig.reset_preview"
    bl_label = "Rest Pose"
    bl_description = "Sets every joint of the rig to 0 (its rest pose)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return rig_props.rig_root(context.object) is not None

    def execute(self, context):
        reset(rig_props.rig_root(context.object))
        return {"FINISHED"}


# ------------------------------------------------------------------ gizmos

def _size(obj):
    """Gizmo size for an object: about its bounding box, in world units."""
    dims = obj.dimensions if obj.type != "EMPTY" else Vector([obj.empty_display_size * 2] * 3)
    return max(0.2, min(5.0, max(dims) * 0.6))


def joint_lines(obj):
    """Line segments (world points, pairs) that show the joint of obj in its rest frame."""
    j = obj.bii_rig
    rest = rig_props.rest_world(obj)
    origin = rest.translation
    rot = rest.to_3x3().normalized()
    try:
        axis = rig_props.world_axis(obj) if j.joint_type in ("REVOLUTE", "PRISMATIC") else None
    except RigError:
        return []
    s = _size(obj)
    lines = []
    if j.joint_type == "REVOLUTE":
        lines += [origin - axis * s, origin + axis * s]
        ref = rot @ Vector((1, 0, 0))
        if abs(ref.dot(axis)) > 0.9:
            ref = rot @ Vector((0, 1, 0))
        ref = (ref - axis * ref.dot(axis)).normalized() * s * 0.8
        lo, hi = rig_props.limits(j) or (-math.pi, math.pi)
        pts = [origin + Matrix.Rotation(lo + (hi - lo) * i / ARC_SEGMENTS, 3, axis) @ ref
               for i in range(ARC_SEGMENTS + 1)]
        for a, b in zip(pts, pts[1:]):
            lines += [a, b]
        if rig_props.limits(j):
            lines += [origin, pts[0], origin, pts[-1]]
        cur = origin + Matrix.Rotation(j.preview_angle, 3, axis) @ ref * 1.15
        lines += [origin, cur]
    elif j.joint_type == "PRISMATIC":
        lo, hi = rig_props.limits(j)
        a, b = origin + axis * lo, origin + axis * hi
        lines += [a, b]
        tick = (rot @ Vector((0, 0, 1)) if abs((rot @ Vector((0, 0, 1))).dot(axis)) < 0.9
                else rot @ Vector((1, 0, 0))) * s * 0.15
        for p in (a, b):
            lines += [p - tick, p + tick]
        cur = origin + axis * j.preview_length
        lines += [cur - tick * 2, cur + tick * 2]
    elif j.joint_type == "BALL":
        for k in range(3):
            ax = Vector([1.0 if i == k else 0.0 for i in range(3)])
            ref = Vector([1.0 if i == (k + 1) % 3 else 0.0 for i in range(3)]) * s * 0.4
            pts = [origin + rot @ (Matrix.Rotation(2 * math.pi * i / ARC_SEGMENTS, 3, ax) @ ref)
                   for i in range(ARC_SEGMENTS + 1)]
            for p, q in zip(pts, pts[1:]):
                lines += [p, q]
    return lines


_handle = None


def _draw():
    context = bpy.context
    obj = context.object
    roots = {rig_props.rig_root(o) for o in context.selected_objects} | {rig_props.rig_root(obj)}
    roots.discard(None)
    if not roots:
        return
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    region = context.region
    shader.uniform_float("viewportSize", (region.width, region.height))
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    for root in roots:
        if not root.bii_rig.show_axes:
            continue
        for o in rig_props.joints(root):
            if not o.visible_get():
                continue
            lines = joint_lines(o)
            if not lines:
                continue
            selected = o.select_get()
            shader.uniform_float("lineWidth", 3.0 if selected else 1.5)
            color = COLORS[o.bii_rig.joint_type]
            shader.uniform_float("color", color if selected else (*color[:3], 0.6))
            batch_for_shader(shader, "LINES", {"pos": [tuple(p) for p in lines]}).draw(shader)
    gpu.state.blend_set("NONE")


classes = (BII_RIG_OT_sweep, BII_RIG_OT_reset_preview)


def register():
    global _handle
    for c in classes:
        bpy.utils.register_class(c)
    if not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

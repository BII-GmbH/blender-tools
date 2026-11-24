"""IK preview mode: drag a gizmo, the rig follows as the part's IK capability would move it.

Start it with the "IK Preview" button (operator bii_rig.ik_preview) on a part with a joint above
it. A gizmo appears at the part: drag its ring to move the target freely, its arrows to move it
along the part's axes, its circles to turn it. The IK is solved from the joint values the rig had
when the mode started (the equipment's state in dProB), exactly as the generated capability does,
including the passes for following and held joints.

Solving runs in a worker thread. A new solve starts only when the last one is done, so the
preview may lag behind the gizmo but never queues up. Esc, right click or the button again ends
the mode: the gizmo disappears and the joints go back to their values from before.

IkSession is the mode without the UI (also for scripts and tests).
"""

import math
import threading
import time

import bpy
from mathutils import Matrix, Vector

from . import ik_chain, rig_props
from .rig_props import RigError


def _rigid(m):
    loc, rot, _ = m.decompose()
    return Matrix.LocRotScale(loc, rot, None)


def _np(m):
    return [list(r) for r in m]


class IkSession:
    """One IK preview: a target pose, solved in the background, shown on the rig."""

    def __init__(self, target):
        self.setup = ik_chain.Setup(target)
        self.target_obj = target
        self.root = self.setup.root
        self.saved = {o: rig_props.preview_value(o.bii_rig) for o in rig_props.joints(self.root)}
        current = self.setup.current_world()
        self.target = Matrix([list(r) for r in current])   # rigid world matrix of the goal
        self.revision = 0          # bumps on every target change
        self.solved_revision = -1
        self._job = None
        self.last = None           # (values, position error, orientation error, seconds)
        self.error = None

    # ---- target
    def set_target(self, matrix_world):
        self.target = _rigid(Matrix(matrix_world))
        self.revision += 1

    @property
    def busy(self):
        return self._job is not None and self._job.is_alive()

    # ---- solving
    def _start(self):
        import numpy as np
        dest = self.setup.destination(np.array(_np(self.target)))
        revision = self.revision
        box = {}

        def work():
            t = time.perf_counter()
            try:
                values, pos_err, rot_err = self.setup.solve(dest)
                box["result"] = (values, pos_err, rot_err, time.perf_counter() - t)
            except Exception as e:          # shown in the header, the mode goes on
                box["error"] = e

        self._job = threading.Thread(target=work, daemon=True)
        self._job.box = box
        self._job.revision = revision
        self._job.start()

    def pump(self):
        """Applies a finished result and starts the next solve if the target changed.
        Call often from the main thread; returns True when it changed the preview."""
        changed = False
        job = self._job
        if job is not None and not job.is_alive():
            self._job = None
            self.solved_revision = job.revision
            if "error" in job.box:
                self.error = str(job.box["error"])
            else:
                self.error = None
                self.last = job.box["result"]
                self._show(self.last[0])
                changed = True
        if self._job is None and self.solved_revision != self.revision:
            self._start()
        return changed

    def wait(self, timeout=30.0):
        """Solves the current target and shows it (blocking; for scripts and tests)."""
        end = time.monotonic() + timeout
        while self.solved_revision != self.revision or self.busy:
            self.pump()
            if self._job is not None:
                self._job.join(max(0.0, end - time.monotonic()))
            if time.monotonic() > end:
                raise RigError("the IK did not finish in time")
        self.pump()
        return self.last

    def _show(self, values):
        for o, v in self.setup.preview_values(values).items():
            try:
                rig_props.set_preview_value(o, v, followers=False)
            except (RigError, ReferenceError):
                pass

    def end(self):
        """Puts the joints back to their values from before the mode."""
        self._job = None
        for o, v in self.saved.items():
            try:
                rig_props.set_preview_value(o, v, followers=False)
            except (RigError, ReferenceError):
                pass
        rig_props.update_followers(self.root)

    def status(self):
        if self.error:
            return f"IK error: {self.error}"
        if self.last is None:
            return "IK: solving..."
        _, pos_err, rot_err, secs = self.last
        lag = " (solving...)" if self.busy or self.solved_revision != self.revision else ""
        return (f"IK {self.target_obj.name}: off by {pos_err * 1000:.1f} mm, {rot_err:.2f}°, "
                f"{secs * 1000:.0f} ms{lag}  |  drag the gizmo; Esc / right click ends")

    def destination(self):
        """(position, rotation x, y, z, w) of the current target as the capability gets it."""
        import numpy as np
        return ik_chain.pose_tuple(self.setup.destination(np.array(_np(self.target))))


session = None      # the active IkSession of the IK preview mode


def _redraw(context=None):
    wm = bpy.context.window_manager
    for w in wm.windows:
        for a in w.screen.areas:
            if a.type in ("VIEW_3D",):
                a.tag_redraw()


class BII_RIG_OT_ik_preview(bpy.types.Operator):
    bl_idname = "bii_rig.ik_preview"
    bl_label = "IK Preview"
    bl_description = ("Drag a gizmo and see how the part's IK capability moves the rig. "
                      "Esc, right click or the button again ends it and resets the joints")

    _timer = None

    @classmethod
    def poll(cls, context):
        if session is not None:
            return True
        o = context.object
        return o is not None and rig_props.rig_root(o) is not None and ik_chain.end_joint(o)[0] is not None

    def invoke(self, context, event):
        global session
        if session is not None:       # the button again: end the mode
            session.stop_requested = True
            return {"FINISHED"}
        try:
            session = IkSession(context.object)
        except RigError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        session.stop_requested = False
        session.workspace = context.workspace
        session.pump()
        # An app timer, not a window timer: while a gizmo is dragged, its own modal handler
        # takes the window's events (also timer events), so this operator's modal() does not
        # run; app timers do.
        bpy.app.timers.register(_tick, first_interval=TICK)
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(session.status())
        _redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if session is None or session.stop_requested or event.type in {"ESC"} or (
                event.type == "RIGHTMOUSE" and event.value == "PRESS"):
            self.finish(context)
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    def finish(self, context):
        global session
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        if session is not None:
            session.end()
        session = None
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
        _redraw()


TICK = 1 / 60


def _tick():
    """Applies finished solves and starts the next one, also while the gizmo is dragged."""
    s = session
    if s is None:
        return None
    try:
        s.target_obj.name
    except ReferenceError:
        s.stop_requested = True
        return None
    if s.pump():
        _redraw()
    try:
        s.workspace.status_text_set(s.status())
    except (ReferenceError, AttributeError, RuntimeError):
        pass
    return TICK


# ------------------------------------------------------------------ gizmo

AXES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
COLORS = ((0.9, 0.25, 0.3), (0.45, 0.8, 0.2), (0.25, 0.5, 0.95))


def _align_z(direction):
    """Rotation (3x3) whose Z axis points along direction."""
    return Vector((0, 0, 1)).rotation_difference(Vector(direction)).to_matrix()


def _retarget(matrix):
    session.set_target(matrix)
    session.pump()          # starts solving at once if nothing runs


class BII_RIG_GGT_ik_target(bpy.types.GizmoGroup):
    bl_idname = "BII_RIG_GGT_ik_target"
    bl_label = "IK Target"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"3D", "PERSISTENT", "SHOW_MODAL_ALL"}

    @classmethod
    def poll(cls, context):
        return session is not None

    def setup(self, context):
        self.base = None
        self.move_acc = Vector()
        self.arrow_acc = [0.0, 0.0, 0.0]
        self.dial_acc = [0.0, 0.0, 0.0]

        mv = self.gizmos.new("GIZMO_GT_move_3d")
        mv.draw_style = "RING_2D"
        mv.draw_options = {"FILL_SELECT", "ALIGN_VIEW"}
        mv.color, mv.alpha = (1.0, 0.8, 0.2), 0.6
        mv.color_highlight, mv.alpha_highlight = (1.0, 1.0, 0.4), 0.9
        mv.scale_basis = 0.25
        mv.target_set_handler("offset", get=self._get_move, set=self._set_move)
        self.mv = mv

        self.arrows, self.dials = [], []
        for i in range(3):
            ar = self.gizmos.new("GIZMO_GT_arrow_3d")
            ar.color, ar.alpha = COLORS[i], 0.7
            ar.color_highlight, ar.alpha_highlight = COLORS[i], 1.0
            ar.scale_basis = 1.0
            ar.target_set_handler("offset", get=self._getter(self.arrow_acc, i),
                                  set=self._arrow_setter(i))
            self.arrows.append(ar)
            dl = self.gizmos.new("GIZMO_GT_dial_3d")
            dl.draw_options = {"CLIP", "ANGLE_VALUE"}
            dl.color, dl.alpha = COLORS[i], 0.6
            dl.color_highlight, dl.alpha_highlight = COLORS[i], 1.0
            dl.scale_basis = 0.8
            dl.line_width = 2
            dl.target_set_handler("offset", get=self._getter(self.dial_acc, i), set=self._dial_setter(i))
            self.dials.append(dl)

    # ---- handlers: values relative to the pose when the drag started (self.base)
    @staticmethod
    def _getter(acc, i):
        return lambda: acc[i]

    def _get_move(self):
        return tuple(self.move_acc)

    def _ensure_base(self):
        if self.base is None and session is not None:
            self.base = session.target.copy()

    def _set_move(self, value):
        self._ensure_base()
        self.move_acc = Vector(value)
        m = self.base.copy()
        m.translation = self.base.translation + self.move_acc
        _retarget(m)

    def _arrow_setter(self, i):
        def set_(value):
            self._ensure_base()
            self.arrow_acc[i] = value
            axis = self.base.to_3x3() @ Vector(AXES[i])
            m = self.base.copy()
            m.translation = self.base.translation + axis * value
            _retarget(m)
        return set_

    def _dial_setter(self, i):
        def set_(value):
            self._ensure_base()
            self.dial_acc[i] = value
            # turn about the part's own axis i, around its origin
            _retarget(self.base @ Matrix.Rotation(-value, 4, Vector(AXES[i])))
        return set_

    def draw_prepare(self, context):
        if session is None:
            return
        dragging = any(g.is_modal for g in self.gizmos)
        if not dragging:
            # a new drag starts from the current target
            self.base = session.target.copy()
            self.move_acc = Vector()
            for k in range(3):
                self.arrow_acc[k] = 0.0
                self.dial_acc[k] = 0.0
            m = self.base
            loc, rot = m.translation, m.to_3x3()
            self.mv.matrix_basis = Matrix.Translation(loc)
            for i in range(3):
                axis = rot @ Vector(AXES[i])
                frame = _align_z(axis).to_4x4()
                frame.translation = loc
                self.arrows[i].matrix_basis = frame
                self.dials[i].matrix_basis = frame


classes = (BII_RIG_OT_ik_preview, BII_RIG_GGT_ik_target)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    global session
    if session is not None:
        try:
            session.end()
        except Exception:
            pass
        session = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

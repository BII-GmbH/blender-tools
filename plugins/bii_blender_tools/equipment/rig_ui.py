"""Sidebar panels (View3D > Sidebar > BII Equipment), operators and File > Export entry."""

import os

import bpy
from bpy_extras.io_utils import ExportHelper

from . import ik_chain, ik_mode, rig_api, rig_export, rig_props
from .rig_props import RigError

CATEGORY = "BII Equipment"


def _root(context):
    return rig_props.rig_root(context.object)


# ------------------------------------------------------------------ operators

class BII_RIG_OT_make_root(bpy.types.Operator):
    bl_idname = "bii_rig.make_root"
    bl_label = "Make Rig"
    bl_description = ("The active object and its children become a dProB equipment. "
                      "Its origin becomes the equipment's origin")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and _root(context) is None

    def execute(self, context):
        try:
            rig_api.make_rig(context.object)
        except RigError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class BII_RIG_OT_remove_root(bpy.types.Operator):
    bl_idname = "bii_rig.remove_root"
    bl_label = "Remove Rig"
    bl_description = "The object is no rig root any more. The joint settings stay on the objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def execute(self, context):
        rig_api.remove_rig(_root(context))
        return {"FINISHED"}


class BII_RIG_OT_select(bpy.types.Operator):
    bl_idname = "bii_rig.select"
    bl_label = "Select"
    bl_description = "Select this part"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.name not in context.view_layer.objects:
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class BII_RIG_OT_pivot_to_cursor(bpy.types.Operator):
    bl_idname = "bii_rig.pivot_to_cursor"
    bl_label = "Pivot to 3D Cursor"
    bl_description = ("Moves the active object's origin (the joint's pivot) to the 3D cursor. "
                      "Its geometry and children stay in place")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.mode == "OBJECT"

    def execute(self, context):
        rig_api.set_pivot(context.object, context.scene.cursor.location)
        return {"FINISHED"}


class BII_RIG_OT_copy_joint(bpy.types.Operator):
    bl_idname = "bii_rig.copy_joint"
    bl_label = "Copy Joint to Selected"
    bl_description = "Gives the selected objects the active object's joint type, axis and limits"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and len(context.selected_objects) > 1

    def execute(self, context):
        src = context.object.bii_rig
        keys = ("joint_type", "axis", "custom_axis", "use_limits", "angle_min", "angle_max",
                "length_min", "length_max")
        for o in context.selected_objects:
            if o is context.object or rig_props.rig_root(o) is None:
                continue
            for k in keys:
                setattr(o.bii_rig, k, getattr(src, k))
        return {"FINISHED"}


class BII_RIG_OT_follow_add(bpy.types.Operator):
    bl_idname = "bii_rig.follow_add"
    bl_label = "Add Leader"
    bl_description = "This joint also follows another joint (factor x its value)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.bii_rig.joint_type in ("REVOLUTE", "PRISMATIC")

    def execute(self, context):
        j = context.object.bii_rig
        t = j.drive_terms.add()
        # a selected joint of the same type is the likely leader
        for o in context.selected_objects:
            if o is not context.object and o.bii_rig.joint_type == j.joint_type:
                t.leader = o
                break
        j.drive = "FOLLOW"
        return {"FINISHED"}


class BII_RIG_OT_follow_remove(bpy.types.Operator):
    bl_idname = "bii_rig.follow_remove"
    bl_label = "Remove Leader"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        j = context.object.bii_rig
        if 0 <= self.index < len(j.drive_terms):
            j.drive_terms.remove(self.index)
            rig_props.update_followers(rig_props.rig_root(context.object))
        return {"FINISHED"}


def _export(op, root, path):
    try:
        res = rig_export.export(root, path)
    except RigError as e:
        op.report({"ERROR"}, str(e))
        return {"CANCELLED"}
    msg = f"Wrote {os.path.basename(res['path'])}: {res['elements']} parts, {len(res['joints'])} joints"
    if res["kept_capabilities"]:
        msg += ", capabilities kept"
    op.report({"INFO"}, msg)
    for w in res["warnings"]:
        op.report({"WARNING"}, w)
    return {"FINISHED"}


class BII_RIG_OT_export(bpy.types.Operator):
    bl_idname = "bii_rig.export"
    bl_label = "Export"
    bl_description = "Writes the rig to its .dequip file (asks for a file the first time)"

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def invoke(self, context, event):
        root = _root(context)
        if not root.bii_rig.export_path:
            return bpy.ops.bii_rig.export_as("INVOKE_DEFAULT")
        return self.execute(context)

    def execute(self, context):
        return _export(self, _root(context), None)


class BII_RIG_OT_export_as(bpy.types.Operator, ExportHelper):
    bl_idname = "bii_rig.export_as"
    bl_label = "Export dProB Equipment"
    bl_description = "Writes the rig of the active object as a dProB scriptable equipment (.dequip)"

    filename_ext = ".dequip"
    filter_glob: bpy.props.StringProperty(default="*.dequip", options={"HIDDEN"})
    keep_capabilities: bpy.props.BoolProperty(
        name="Keep Capabilities", default=True,
        description="When the file exists, keep its capabilities, state variables and vehicle data")

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def invoke(self, context, event):
        root = _root(context)
        r = root.bii_rig
        self.keep_capabilities = r.keep_capabilities
        self.filepath = bpy.path.abspath(r.export_path) if r.export_path else (
            os.path.join(os.path.dirname(bpy.data.filepath), bpy.path.clean_name(root.name) + ".dequip")
            if bpy.data.filepath else bpy.path.clean_name(root.name) + ".dequip")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        root = _root(context)
        root.bii_rig.keep_capabilities = self.keep_capabilities
        return _export(self, root, self.filepath)


class BII_RIG_OT_check(bpy.types.Operator):
    bl_idname = "bii_rig.check"
    bl_label = "Check File"
    bl_description = ("Compares the written .dequip with the Blender preview: random joint values, "
                      "evaluated as dProB does")

    @classmethod
    def poll(cls, context):
        root = _root(context)
        return root is not None and os.path.isfile(bpy.path.abspath(root.bii_rig.export_path))

    def execute(self, context):
        try:
            res = rig_export.check(_root(context))
        except (RigError, ValueError, KeyError) as e:
            self.report({"ERROR"}, f"Check failed: {e}")
            return {"CANCELLED"}
        if res["max_error"] < 1e-4:
            self.report({"INFO"}, f"File matches the preview (deviation {res['max_error'] * 1000:.3f} mm)")
        else:
            self.report({"WARNING"}, f"File differs from the preview by {res['max_error']:.4f} m at "
                                     f"{res['worst']}; export again")
        return {"FINISHED"}


# ------------------------------------------------------------------ panels

def _value_prop(j):
    return {"REVOLUTE": "preview_angle", "PRISMATIC": "preview_length", "BALL": "preview_ball"}.get(j.joint_type)


class VIEW3D_PT_bii_rig(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Equipment Rig"

    def draw(self, context):
        layout = self.layout
        root = _root(context)
        if root is None:
            layout.label(text="Select the top object of the equipment.", icon="INFO")
            layout.operator("bii_rig.make_root", icon="ARMATURE_DATA")
            return
        r = root.bii_rig
        row = layout.row(align=True)
        row.operator("bii_rig.select", text=root.name, icon="OUTLINER_OB_EMPTY" if root.type == "EMPTY"
                     else "OBJECT_DATA").name = root.name
        row.operator("bii_rig.remove_root", text="", icon="X")
        joints = rig_props.joints(root)
        layout.label(text=f"{len(rig_props.rig_objects(root))} parts, {len(joints)} joints")
        layout.prop(r, "forward")
        layout.prop(r, "show_axes")
        if not joints:
            layout.label(text="Give parts a joint in the Joint panel.", icon="INFO")
            return
        col = layout.column(align=True)
        for o in joints:
            j = o.bii_rig
            row = col.row(align=True)
            icon = next(i[3] for i in rig_props.JOINT_ITEMS if i[0] == j.joint_type)
            sub = row.row(align=True)
            sub.scale_x = 0.9
            sub.operator("bii_rig.select", text=o.name, icon=icon, depress=o == context.object).name = o.name
            prop = _value_prop(j)
            if j.joint_type == "BALL":
                row.prop(j, prop, text="")
            else:
                sub = row.row(align=True)
                sub.enabled = j.drive != "FOLLOW"
                sub.prop(j, prop, text="", slider=rig_props.limits(j) is not None)
                if j.drive != "FREE":
                    row.label(text="", icon="LINKED" if j.drive == "FOLLOW" else "LOCKED")
        row = layout.row(align=True)
        row.operator("bii_rig.sweep", icon="PLAY")
        row.operator("bii_rig.reset_preview", icon="LOOP_BACK")


class VIEW3D_PT_bii_rig_joint(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Joint"

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        j = obj.bii_rig
        layout.label(text=obj.name, icon="OBJECT_DATA")
        layout.prop(j, "joint_type", text="")
        if j.joint_type in ("REVOLUTE", "PRISMATIC"):
            layout.prop(j, "axis", expand=True)
            if j.axis == "CUSTOM":
                layout.prop(j, "custom_axis", text="")
        col = layout.column(align=True)
        if j.joint_type == "REVOLUTE":
            col.prop(j, "use_limits")
            if j.use_limits:
                row = col.row(align=True)
                row.prop(j, "angle_min")
                row.prop(j, "angle_max")
        elif j.joint_type == "PRISMATIC":
            row = col.row(align=True)
            row.prop(j, "length_min")
            row.prop(j, "length_max")
        prop = _value_prop(j)
        if prop:
            row = layout.row()
            row.enabled = j.drive != "FOLLOW" or j.joint_type == "BALL"
            row.prop(j, prop, text="Preview", slider=rig_props.limits(j) is not None)
        if j.joint_type in ("REVOLUTE", "PRISMATIC"):
            box = layout.box()
            box.prop(j, "drive")
            if j.drive == "FOLLOW":
                col = box.column(align=True)
                for i, t in enumerate(j.drive_terms):
                    row = col.row(align=True)
                    row.prop(t, "factor", text="")
                    row.label(text="×")
                    row.prop(t, "leader", text="")
                    row.operator("bii_rig.follow_remove", text="", icon="X").index = i
                row = box.row(align=True)
                row.operator("bii_rig.follow_add", icon="ADD")
                row.prop(j, "drive_offset_angle" if j.joint_type == "REVOLUTE" else "drive_offset_length")
                for e in rig_props.drive_problems(obj):
                    box.label(text=e, icon="ERROR")
                if not j.drive_terms:
                    box.label(text="No leader: the joint stays at the offset", icon="INFO")
            elif j.drive == "HOLD":
                box.label(text="IK capabilities do not move it", icon="INFO")
        if j.joint_type != "FIXED":
            row = layout.row(align=True)
            row.operator("bii_rig.pivot_to_cursor", icon="PIVOT_CURSOR")
            row.operator("bii_rig.copy_joint", text="", icon="COPYDOWN")
            if not (j.preview_owned or rig_props._deltas_clean(obj)):
                layout.label(text="Uses delta transforms: preview off", icon="ERROR")
        col = layout.column(heading="dProB")
        col.prop(j, "visible")
        col.prop(j, "attachment_point")


class VIEW3D_PT_bii_rig_ik(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Inverse Kinematics"

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def draw_header(self, context):
        self.layout.prop(context.object.bii_rig, "ik_target", text="")

    def draw(self, context):
        layout = self.layout
        obj = context.object
        j = obj.bii_rig
        active = ik_mode.session is not None
        if ik_chain.end_joint(obj)[0] is None:
            layout.label(text="No joint above this part moves it", icon="INFO")
            return
        col = layout.column()
        col.active = j.ik_target
        col.prop(j, "ik_name")
        col.prop(j, "ik_description")
        col.prop(j, "ik_root")
        row = col.row(align=True)
        row.prop(j, "ik_iterations")
        row.prop(j, "ik_loops")
        col.prop(j, "ik_orientation_weight")
        col.prop(j, "ik_seed_margin")
        row = layout.row()
        row.scale_y = 1.3
        row.operator("bii_rig.ik_preview", text="End IK Preview" if active else "IK Preview",
                     icon="CANCEL" if active else "CON_KINEMATIC", depress=active)
        if active:
            s = ik_mode.session
            box = layout.box()
            if s.error:
                box.label(text=s.error, icon="ERROR")
            elif s.last is not None:
                _, pos_err, rot_err, secs = s.last
                box.label(text=f"Off by {pos_err * 1000:.1f} mm, {rot_err:.2f}°  ({secs * 1000:.0f} ms)")
            (x, y, z), (qx, qy, qz, qw) = s.destination()
            box.label(text="Destination (dProB, relative to the equipment):")
            box.label(text=f"Position  {x:.3f}  {y:.3f}  {z:.3f} m")
            box.label(text=f"Rotation  {qx:.4f}  {qy:.4f}  {qz:.4f}  {qw:.4f}")


class VIEW3D_PT_bii_rig_export(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Export"

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def draw(self, context):
        layout = self.layout
        root = _root(context)
        r = root.bii_rig
        layout.prop(r, "export_path", text="")
        layout.prop(r, "keep_capabilities")
        layout.prop(r, "crane_hook", icon="CON_KINEMATIC")
        if r.crane_hook is not None:
            layout.label(text="Exports as Crane: Move Arm, Lift, Deliver", icon="INFO")
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("bii_rig.export", icon="EXPORT")
        row.operator("bii_rig.check", text="", icon="CHECKMARK")
        for severity, text in rig_export.problems(root)[:8]:
            layout.label(text=text, icon="ERROR" if severity == "ERROR" else "INFO")


def menu_export(self, context):
    self.layout.operator(BII_RIG_OT_export_as.bl_idname, text="dProB Equipment (.dequip)")


classes = (
    BII_RIG_OT_make_root, BII_RIG_OT_remove_root, BII_RIG_OT_select, BII_RIG_OT_pivot_to_cursor,
    BII_RIG_OT_copy_joint, BII_RIG_OT_follow_add, BII_RIG_OT_follow_remove,
    BII_RIG_OT_export, BII_RIG_OT_export_as, BII_RIG_OT_check,
    VIEW3D_PT_bii_rig, VIEW3D_PT_bii_rig_joint, VIEW3D_PT_bii_rig_ik, VIEW3D_PT_bii_rig_export,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

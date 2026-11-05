"""File > Import > STEP.

Converts the STEP file with step2glb.py and loads the result with Blender's
glTF importer, which keeps colors, names and the assembly tree. OCP, which
the converter needs, comes with the extension as a bundled wheel.

With "Choose Parts" on, a second dialog shows the assembly tree, where whole
instances (helpers, track sections, ...) can be left out of the import.
"""

import math
import os
import tempfile
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

# A STEP document read for the parts dialog, reused when the dialog confirms.
_read_cache = {}


def load_converter(op):
    try:
        from . import step2glb  # imports OCP, which takes a moment, so only on use
        return step2glb
    except ImportError as e:
        traceback.print_exc()
        op.report({'ERROR'}, f"OpenCascade (OCP) cannot be loaded: {e}")
        return None


class StepSettings:
    deflection: FloatProperty(
        name="Deflection",
        description="Max distance between mesh and CAD surface, as a fraction of the "
                    "model size. Larger means fewer triangles",
        default=0.002, min=0.00001, max=0.1, precision=4, step=0.01)
    angle: FloatProperty(
        name="Angle",
        description="Max angle between neighboring mesh normals on curved surfaces. "
                    "Larger means fewer triangles. Does not depend on feature size, "
                    "so small details such as embossed text keep their shape",
        default=math.radians(22.5), min=math.radians(1), max=math.radians(90),
        precision=1, subtype='ANGLE')
    up_axis: EnumProperty(
        name="Up",
        description="Up axis of the STEP model",
        items=[('Z', "Z", "Z is up (most CAD systems)"),
               ('Y', "Y", "Y is up (for example some SolidWorks models)")],
        default='Z')
    drop_small: BoolProperty(
        name="Small Parts",
        description="Remove bodies whose bounding box diagonal is below the size, "
                    "such as screws and nuts",
        default=False)
    small_size: FloatProperty(
        name="Size", default=0.05, min=0.0, subtype='DISTANCE', unit='LENGTH')
    drop_hidden: BoolProperty(
        name="Hidden Parts",
        description="Remove bodies that cannot be seen from outside the model. "
                    "Takes a while on large models",
        default=False)

    setting_names = ("deflection", "angle", "up_axis", "drop_small", "small_size", "drop_hidden")

    def draw_settings(self, layout):
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(heading="Mesh")
        col.prop(self, "deflection")
        col.prop(self, "angle")
        col.row().prop(self, "up_axis", expand=True)
        col = layout.column(heading="Remove")
        col.prop(self, "drop_small")
        sub = col.column()
        sub.active = self.drop_small
        sub.prop(self, "small_size")
        col.prop(self, "drop_hidden")

    def import_step(self, context, step2glb, doc, exclude=()):
        flt = step2glb.Filter(set(exclude), self.small_size if self.drop_small else None,
                              self.drop_hidden)
        fd, glb = tempfile.mkstemp(suffix=".glb")
        os.close(fd)
        try:
            report = step2glb.convert(doc, glb, self.deflection, math.degrees(self.angle),
                                      self.up_axis.lower(), flt)
            bpy.ops.import_scene.gltf(filepath=glb)
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"STEP import failed: {e}")
            return {'CANCELLED'}
        finally:
            os.remove(glb)

        print(f"Imported {self.filepath}")
        for line in report:
            print(f"  {line}")
        self.report({'INFO'}, f"Imported {os.path.basename(self.filepath)}: {report[0]}")
        return {'FINISHED'}


def read_step(op, context, step2glb):
    if context.window:
        context.window.cursor_set('WAIT')
    try:
        return step2glb.read_step(op.filepath)
    except Exception as e:
        traceback.print_exc()
        op.report({'ERROR'}, f"STEP import failed: {e}")
        return None
    finally:
        if context.window:
            context.window.cursor_set('DEFAULT')


class ImportStepOperator(bpy.types.Operator, ImportHelper, StepSettings):
    """Import a STEP file with colors, optionally without small, hidden or chosen parts"""
    bl_idname = "import_scene.bii_step"
    bl_label = "Import STEP"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filename_ext = ".step"
    filter_glob: StringProperty(default="*.step;*.stp", options={'HIDDEN'})

    choose_parts: BoolProperty(
        name="Choose Parts",
        description="Show the assembly tree after choosing the file, to leave out "
                    "instances such as helper geometry",
        default=False)

    def draw(self, context):
        self.draw_settings(self.layout)
        self.layout.prop(self, "choose_parts")

    def invoke(self, context, event):
        if self.filepath and os.path.isfile(self.filepath):  # dropped onto the viewport
            return context.window_manager.invoke_props_dialog(self, width=360)
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        if self.choose_parts:
            settings = {name: getattr(self, name) for name in self.setting_names}
            bpy.ops.import_scene.bii_step_parts('INVOKE_DEFAULT', filepath=self.filepath, **settings)
            return {'CANCELLED'}  # the parts dialog does the import
        step2glb = load_converter(self)
        doc = step2glb and read_step(self, context, step2glb)
        if doc is None:
            return {'CANCELLED'}
        if context.window:
            context.window.cursor_set('WAIT')
        try:
            return self.import_step(context, step2glb, doc)
        finally:
            if context.window:
                context.window.cursor_set('DEFAULT')


class StepPartItem(bpy.types.PropertyGroup):
    path: StringProperty()  # instance path, such as "0/4/2"
    label: StringProperty()
    size: FloatProperty()
    depth: IntProperty()
    parent: IntProperty(default=-1)  # index in the list, -1 for a root
    has_children: BoolProperty()
    expanded: BoolProperty(name="Expand")
    include: BoolProperty(name="Import", description="Import this instance", default=True)


class BII_UL_step_parts(bpy.types.UIList):
    """Assembly tree: indentation by depth, folding by the expand toggle"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        parts = data.parts
        included = item.include
        parent = item.parent
        while included and parent >= 0:
            included = parts[parent].include
            parent = parts[parent].parent

        row = layout.row(align=True)
        if not self.filter_name:
            for _ in range(item.depth):
                row.separator(factor=2.0)
            if item.has_children:
                row.prop(item, "expanded", text="", emboss=False,
                         icon='DISCLOSURE_TRI_DOWN' if item.expanded else 'DISCLOSURE_TRI_RIGHT')
            else:
                row.label(text="", icon='BLANK1')
        row.prop(item, "include", text="")
        sub = row.row()
        sub.active = included
        sub.label(text=item.label, icon='OUTLINER_COLLECTION' if item.has_children else 'MESH_DATA')
        size = row.row()
        size.alignment = 'RIGHT'
        size.active = included
        size.label(text=f"{item.size:.2f} m")

    def filter_items(self, context, data, propname):
        parts = getattr(data, propname)
        if self.filter_name:  # searching shows matches flat, folded or not
            pattern = self.filter_name.lower()
            shown = [pattern in p.label.lower() for p in parts]
        else:
            shown = []
            for p in parts:  # parents come before their children
                shown.append(p.parent < 0 or (shown[p.parent] and parts[p.parent].expanded))
        return [self.bitflag_filter_item if s else 0 for s in shown], []


class ImportStepPartsOperator(bpy.types.Operator, StepSettings):
    """Choose which instances of the STEP assembly to import"""
    bl_idname = "import_scene.bii_step_parts"
    bl_label = "Import STEP: Choose Parts"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    parts: CollectionProperty(type=StepPartItem, options={'HIDDEN'})
    active_parts: IntProperty(options={'HIDDEN'})

    def draw(self, context):
        layout = self.layout
        split = layout.split(factor=0.38)
        self.draw_settings(split.column())
        col = split.column()
        col.label(text="Uncheck instances to leave them out:")
        col.template_list("BII_UL_step_parts", "", self, "parts", self, "active_parts", rows=18)

    def invoke(self, context, event):
        step2glb = load_converter(self)
        doc = step2glb and read_step(self, context, step2glb)
        if doc is None:
            return {'CANCELLED'}
        _read_cache.clear()
        _read_cache[self.filepath] = doc

        self.parts.clear()
        index_of = {}
        for node in step2glb.assembly_tree(doc):
            item = self.parts.add()
            index_of[node.path] = len(self.parts) - 1
            item.path = "/".join(map(str, node.path))
            item.label = f"{node.product}  ({node.instance})" if node.instance else node.product
            item.size = node.size
            item.depth = len(node.path) - 1
            item.expanded = item.depth == 0
            item.parent = index_of.get(node.path[:-1], -1)
            if item.parent >= 0:
                self.parts[item.parent].has_children = True
        return context.window_manager.invoke_props_dialog(
            self, width=900, title=f"Import {os.path.basename(self.filepath)}", confirm_text="Import")

    def cancel(self, context):
        _read_cache.clear()

    def execute(self, context):
        step2glb = load_converter(self)
        if step2glb is None:
            return {'CANCELLED'}
        doc = _read_cache.pop(self.filepath, None) or read_step(self, context, step2glb)
        _read_cache.clear()
        if doc is None:
            return {'CANCELLED'}
        exclude = {tuple(int(i) for i in p.path.split("/")) for p in self.parts if not p.include}
        if context.window:
            context.window.cursor_set('WAIT')
        try:
            return self.import_step(context, step2glb, doc, exclude)
        finally:
            if context.window:
                context.window.cursor_set('DEFAULT')


class IO_FH_bii_step(bpy.types.FileHandler):
    """Drag and drop of STEP files into the 3D viewport"""
    bl_idname = "IO_FH_bii_step"
    bl_label = "STEP"
    bl_import_operator = ImportStepOperator.bl_idname
    bl_file_extensions = ".step;.stp"

    @classmethod
    def poll_drop(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'


classes = [StepPartItem, BII_UL_step_parts, ImportStepOperator, ImportStepPartsOperator,
           IO_FH_bii_step]


def menu_func_import(self, context):
    self.layout.operator(ImportStepOperator.bl_idname, text="STEP (.step, .stp)")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

"""File > Import > STEP.

Converts the STEP file with step2glb.py and loads the result with Blender's
glTF importer, which keeps colors, names and the assembly tree. OCP, which
the converter needs, comes with the extension as a bundled wheel.

With "Choose Parts" on, a second dialog shows the assembly tree, where whole
instances (helpers, track sections, ...) can be left out of the import.

Started from the user interface, the import runs as a modal operator: it
converts in short slices, shows the progress in the status bar between them
and stops on Esc. Reading the STEP file and loading the glTF file are single
calls, so Esc waits for them. From scripts and from the Adjust Last Operation
panel, the import runs to the end in one call.
"""

import math
import os
import tempfile
import time
import traceback
from contextlib import contextmanager

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, StringProperty)
from bpy.types import OperatorFileListElement
from bpy_extras.io_utils import ImportHelper

SLICE_SECONDS = 0.1  # work between two progress updates and Esc checks


def load_converter(op):
    try:
        from . import step2glb  # imports OCP, which takes a moment, so only on use
        return step2glb
    except ImportError as e:
        traceback.print_exc()
        op.report({'ERROR'}, f"OpenCascade (OCP) cannot be loaded: {e}")
        return None


@contextmanager
def wait_cursor(context):
    window = context.window
    if window:
        window.cursor_set('WAIT')
    try:
        yield
    finally:
        if window:
            window.cursor_set('DEFAULT')


def format_length(context, meters):
    """A length in the scene's unit system, like Blender shows distance properties."""
    units = context.scene.unit_settings
    if units.system == 'NONE':
        return f"{meters / units.scale_length:.3g}"
    return bpy.utils.units.to_string(units.system, 'LENGTH', meters, precision=3)


def labeled(steps, text, start=0.0, end=1.0):
    """Map the progress of step2glb steps to start..end and add text; return their result.

    Yields start first, so that the text is shown before the first step runs.
    """
    yield start, text
    while True:
        try:
            fraction = next(steps)
        except StopIteration as done:
            return done.value
        yield start + (end - start) * fraction, text


def reading_text(path):
    return f"Reading {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.0f} MB)"


def read_parts(step2glb, path):
    """Read a STEP file for the parts dialog. Yields (progress, text), returns (document, tree)."""
    doc = yield from labeled(step2glb.read_step_steps(path), reading_text(path), 0.0, 0.95)
    yield 0.95, "Reading the assembly tree"
    return doc, step2glb.assembly_tree(doc)


# Share of reading in the import of a file, measured on a 143 MB SolidWorks
# file: 45 s reading, then about as long for converting and loading.
READ_SHARE = 0.4


def import_steps(op, context, step2glb, files):
    """Read, convert and load STEP files. Yields (progress, text), returns report lines.

    files holds (path, document or None, excluded instance paths). A document
    from read_step saves reading the file again.
    """
    # Size is a distance property, so it is in Blender units; step2glb takes meters.
    max_size = op.small_size * context.scene.unit_settings.scale_length if op.drop_small else None
    report = []
    for i, (path, doc, exclude) in enumerate(files):
        def overall(steps, text, start, end):
            return labeled(steps, text, (i + start) / len(files), (i + end) / len(files))

        name = os.path.basename(path)
        convert_start = 0.0
        if doc is None:
            doc = yield from overall(step2glb.read_step_steps(path), reading_text(path),
                                     0.0, READ_SHARE)
            convert_start = READ_SHARE
        fd, glb = tempfile.mkstemp(suffix=".glb")
        os.close(fd)
        try:
            steps = step2glb.convert_steps(doc, glb, op.deflection, math.degrees(op.angle),
                                           op.up_axis.lower(),
                                           step2glb.Filter(set(exclude), max_size, op.drop_hidden))
            lines = yield from overall(steps, f"Converting {name}", convert_start, 0.9)
            yield (i + 0.9) / len(files), f"Loading {name} into Blender"
            # Nothing may be yielded after the last file is loaded: the modal
            # import must not be cancelled once objects are in the scene.
            bpy.ops.import_scene.gltf(filepath=glb)
            op.imported_files += 1
        finally:
            os.remove(glb)
        print(f"Imported {path}")
        for line in lines:
            print(f"  {line}")
        report.append(f"{name}: {lines[0]}")
    return report


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

    # --- Running the import ---

    def run_import(self, context, step2glb, files):
        """Import files, see import_steps, modal when started from the user interface."""
        self.imported_files = 0
        self._file_count = len(files)
        return self.run_steps(context, step2glb, import_steps(self, context, step2glb, files),
                              self._finished)

    def run_steps(self, context, step2glb, steps, done):
        """Run steps that yield (progress, text), then return done(their result).

        Modal when started from the user interface, else in one call.
        """
        if (context.window and not bpy.app.background and self.options.is_invoke
                and not (self.options.is_repeat or self.options.is_repeat_last)):
            self._steps = steps
            self._done = done
            self._text = None
            self._workspace = context.workspace
            self._show_progress(0.0, "Starting import")
            context.window.cursor_modal_set('WAIT')
            self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        with wait_cursor(context):
            try:
                result = step2glb.run(steps)
            except Exception as e:
                return self._failed(e)
        return done(result)

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._steps.close()
            self._stop_modal(context)
            if self.imported_files:  # keep what is in the scene, with an undo step
                self.report({'WARNING'}, f"STEP import cancelled, {self.imported_files} of "
                                         f"{self._file_count} files imported")
                return {'FINISHED'}
            self.report({'WARNING'}, "STEP import cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}  # the scene must not change during the import

        # A slice also ends when the text changes, so that the next step's text
        # is shown before it runs: some steps are single calls of a minute.
        end = time.monotonic() + SLICE_SECONDS
        try:
            while True:
                fraction, text = next(self._steps)
                if text != self._text or time.monotonic() >= end:
                    break
        except StopIteration as done:
            self._stop_modal(context)
            return self._done(done.value)
        except Exception as e:
            self._stop_modal(context)
            if self.imported_files:
                self._failed(e)
                return {'FINISHED'}
            return self._failed(e)
        self._text = text
        self._show_progress(fraction, text)
        return {'RUNNING_MODAL'}

    def _show_progress(self, fraction, text):
        def draw(header, context):
            row = header.layout.row(align=True)
            row.label(text="Cancel", icon='EVENT_ESC')
            row.separator(factor=2.0)
            bar = row.row()
            bar.ui_units_x = 16
            bar.progress(factor=fraction, type='BAR', text=f"{text}  {fraction:.0%}")
        self._workspace.status_text_set(draw)

    def _stop_modal(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.window.cursor_modal_restore()
        self._workspace.status_text_set(None)

    def _failed(self, error):
        traceback.print_exc()
        self.report({'ERROR'}, f"STEP import failed: {error}")
        return {'CANCELLED'}

    def _finished(self, report):
        self.report({'INFO'}, f"Imported {report[0]}" if len(report) == 1
                    else f"Imported {len(report)} STEP files")
        return {'FINISHED'}


class ImportStepOperator(bpy.types.Operator, ImportHelper, StepSettings):
    """Import a STEP file with colors, optionally without small, hidden or chosen parts"""
    bl_idname = "import_scene.bii_step"
    bl_label = "Import STEP"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filename_ext = ".step"
    filter_glob: StringProperty(default="*.step;*.stp", options={'HIDDEN'})
    # Several files, chosen in the file browser or dropped onto the viewport.
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE', 'SKIP_PRESET'})
    files: CollectionProperty(type=OperatorFileListElement,
                              options={'HIDDEN', 'SKIP_SAVE', 'SKIP_PRESET'})

    choose_parts: BoolProperty(
        name="Choose Parts",
        description="Show the assembly tree after choosing the file, to leave out "
                    "instances such as helper geometry",
        default=False)

    def draw(self, context):
        self.draw_settings(self.layout)
        self.layout.prop(self, "choose_parts")

    def invoke(self, context, event):
        # A dialog for files dropped onto the viewport, else the file browser.
        return ImportHelper.invoke_popup(self, context)

    def paths(self):
        if not self.files:
            return [self.filepath]
        directory = self.directory or os.path.dirname(self.filepath)
        return [os.path.join(directory, f.name) for f in self.files if f.name]

    def execute(self, context):
        paths = self.paths()
        if self.choose_parts and len(paths) > 1:
            self.report({'ERROR'}, "Choose Parts works with one file at a time")
            return {'CANCELLED'}
        step2glb = load_converter(self)
        if step2glb is None:
            return {'CANCELLED'}
        if self.choose_parts:
            # Read here, with the progress in the status bar; then the parts
            # dialog opens.
            return self.run_steps(context, step2glb, read_parts(step2glb, paths[0]),
                                  lambda result: self._open_parts_dialog(paths[0], *result))
        return self.run_import(context, step2glb, [(path, None, ()) for path in paths])

    def _open_parts_dialog(self, path, doc, tree):
        global _handover
        settings = {name: getattr(self, name) for name in self.setting_names}
        _handover = (path, doc, tree)
        try:
            bpy.ops.import_scene.bii_step_parts('INVOKE_DEFAULT', filepath=path, **settings)
        finally:
            _handover = None
        return {'CANCELLED'}  # the parts dialog does the import


# The STEP document and assembly tree that ImportStepOperator read, for the
# parts dialog that it opens. bpy.ops cannot pass Python objects, so they are
# handed over here, only for the duration of that one call: the dialog's
# invoke runs within it and takes them.
_handover = None


class StepPartItem(bpy.types.PropertyGroup):
    path: StringProperty()  # instance path, such as "0/4/2"
    label: StringProperty()
    size: FloatProperty()  # meters
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
        size.label(text=format_length(context, item.size))

    def filter_items(self, context, data, propname):
        # Runs on every redraw of the dialog, for every item: foreach_get reads
        # the whole list at once instead of item by item.
        parts = getattr(data, propname)
        if self.filter_name:  # searching shows matches flat, folded or not
            pattern = self.filter_name.lower()
            shown = [pattern in p.label.lower() for p in parts]
        else:
            parent = [0] * len(parts)
            parts.foreach_get("parent", parent)
            expanded = [False] * len(parts)
            parts.foreach_get("expanded", expanded)
            shown = []
            for p in parent:  # parents come before their children
                shown.append(p < 0 or (shown[p] and expanded[p]))
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
        if _handover is None or _handover[0] != self.filepath:
            self.report({'ERROR'}, "Open the parts list with File > Import > STEP and Choose Parts")
            return {'CANCELLED'}
        # The document is kept for execute, which runs on this same operator
        # instance when the dialog is confirmed, so the file is read once.
        _, self._doc, tree = _handover

        self.parts.clear()
        index_of = {}
        for node in tree:
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

    def execute(self, context):
        step2glb = load_converter(self)
        if step2glb is None:
            return {'CANCELLED'}
        # Adjust Last Operation runs a new instance, which reads the file again.
        doc = getattr(self, "_doc", None)
        self._doc = None
        exclude = {tuple(int(i) for i in p.path.split("/")) for p in self.parts if not p.include}
        return self.run_import(context, step2glb, [(self.filepath, doc, exclude)])


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

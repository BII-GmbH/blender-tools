"""File > Import > Plan: PDF, TIFF, PNG or JPEG plans as scaled reference planes.

The plan becomes a plane object. Its mesh is in paper meters (1 mm on the
sheet = 0.001), the object scale is the Maßstab, so 1:1000 is scale 1000
and the plane has its true size in the scene.

A Geometry Nodes modifier sets the UV map from the vertex positions. The
mesh outline is therefore the crop: move or cut its edges in Edit Mode and
the image stays in place. "Full Sheet" shows the whole sheet, for example to
read the Legende, without touching the crop.

Georeferencing and aligning are in plan_georef.py.
"""

import math
import os
import tempfile
import traceback

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from . import plan_geo

NODE_GROUP = "BII Plan Sheet"
MODIFIER = "BII Plan Sheet"
OPACITY_NODE = "Opacity"
BACKGROUND_NODE = "Background"
SHEET_TOLERANCE = 1e-5  # paper meters a vertex may lie outside the sheet


# ---------------------------------------------------------------- properties

def _redraw(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _crs_changed(self, context):
    # Guesses are positions in the old system: take them again in the new one.
    from . import plan_georef
    obj = self.id_data
    if obj and context and context.scene:
        try:
            plan_georef.refresh_guesses(context.scene, obj, self)
        except ValueError:
            pass
    _points_changed(self, context)


def _points_changed(self, context):
    from . import plan_georef
    obj = self.id_data
    if obj and getattr(obj, "bii_plan", None):
        plan_georef.update_residuals(obj.bii_plan)
    _redraw(self, context)


def set_point_value(point, axis, value, guessed):
    """Set the "easting" or "northing" of a PlanPoint from code.

    Typing a value in the sidebar marks it as read from the plan: the
    property's update clears its guessed flag. Code sets values through this
    function, which sets the flag after the value.
    """
    setattr(point, axis, value)
    setattr(point, axis + "_guessed", guessed)


def _easting_edited(self, context):
    # A typed value is read from the plan, not guessed.
    if self.easting_guessed:
        self.easting_guessed = False
    _points_changed(self, context)


def _northing_edited(self, context):
    if self.northing_guessed:
        self.northing_guessed = False
    _points_changed(self, context)


class PlanPoint(bpy.types.PropertyGroup):
    """A grid cross: its position on the sheet and its plan coordinates."""
    x: FloatProperty(name="Sheet X", subtype='DISTANCE', precision=5)
    y: FloatProperty(name="Sheet Y", subtype='DISTANCE', precision=5)
    # Whole meters as integers: a 32-bit float cannot hold 5437550 exactly.
    easting: IntProperty(name="Rechtswert", description="Easting printed on the plan, "
                         "with the zone digits in front (e.g. 4590250)",
                         update=_easting_edited)
    northing: IntProperty(name="Hochwert", description="Northing printed on the plan",
                          update=_northing_edited)
    easting_guessed: BoolProperty(
        name="Rechtswert Guessed", update=_points_changed,
        description="The Rechtswert is a guess, not read from the plan. Guesses only decide "
        "what the values read from the plan leave open. Typing a value clears this")
    northing_guessed: BoolProperty(
        name="Hochwert Guessed", update=_points_changed,
        description="The Hochwert is a guess, not read from the plan. Guesses only decide "
        "what the values read from the plan leave open. Typing a value clears this")
    use: BoolProperty(name="Use", default=True, description="Use this point for the fit",
                      update=_points_changed)
    residual: FloatProperty(name="Residual", description="Distance between the entered and "
                            "the fitted position, in meters", default=-1.0)


class AlignPair(bpy.types.PropertyGroup):
    """A point on the sheet and the scene point it should go to."""
    x: FloatProperty(name="Sheet X", subtype='DISTANCE', precision=5)
    y: FloatProperty(name="Sheet Y", subtype='DISTANCE', precision=5)
    target: FloatVectorProperty(name="Target", subtype='TRANSLATION', size=3,
                                description="Scene position of this point", update=_redraw)
    residual: FloatProperty(name="Residual", default=-1.0)


def _get_massstab(self):
    return self.id_data.scale[0]


def _set_massstab(self, value):
    self.id_data.scale = (value, value, value)


def _set_orientation(self, context):
    obj = self.id_data
    rot = obj.rotation_euler
    obj.rotation_euler = (math.pi / 2 if self.orientation == 'UPRIGHT' else 0.0, 0.0, rot[2])


def _set_full_sheet(self, context):
    mod = self.id_data.modifiers.get(MODIFIER)
    if mod:
        _set_modifier_input(mod, "Full Sheet", self.full_sheet)
        self.id_data.update_tag()
    _redraw(self, context)


def own_material(obj):
    """The plan's material, copied first if another plan uses it too.

    Shift+D links the material by default, so without the copy the Opacity
    and Background of one plan would change its copies as well. The Geometry
    Nodes modifier sets the material, so the copy goes there.
    """
    pg = obj.bii_plan
    mat = pg.material
    if mat is None:
        return None
    if not any(o is not obj and o.bii_plan.material == mat
               for o in bpy.data.objects if o.bii_plan.is_plan):
        return mat
    copy = mat.copy()
    pg.material = copy
    mod = obj.modifiers.get(MODIFIER)
    if mod:
        _set_modifier_input(mod, "Material", copy)
    if obj.data.users == 1:  # not a linked duplicate (Alt+D): its slots are its own
        for i, slot_mat in enumerate(obj.data.materials):
            if slot_mat == mat:
                obj.data.materials[i] = copy
    obj.update_tag()
    return copy


def _set_opacity(self, context):
    mat = own_material(self.id_data)
    if mat is None:
        return
    mat.node_tree.nodes[OPACITY_NODE].outputs[0].default_value = self.opacity
    # Solid view does not run the shader; it takes the opacity from the
    # material's viewport color.
    mat.diffuse_color = (1.0, 1.0, 1.0, self.opacity)


def _set_background(self, context):
    mat = own_material(self.id_data)
    if mat is None:
        return
    mat.node_tree.nodes[BACKGROUND_NODE].outputs[0].default_value = self.background
    update_solid_paper(self)


def update_solid_paper(pg):
    """Solid view cuts the image off at its alpha instead of blending it:
    from Background 0.5 on, it shows the image without alpha, with its paper.
    Copies of a plan share its image, so in Solid view they show the paper
    alike."""
    if pg.image is not None:
        pg.image.alpha_mode = 'NONE' if pg.background >= 0.5 else 'STRAIGHT'


class PlanSettings(bpy.types.PropertyGroup):
    is_plan: BoolProperty()
    source: StringProperty(name="Source", subtype='FILE_PATH')
    image: PointerProperty(type=bpy.types.Image)
    material: PointerProperty(type=bpy.types.Material)
    width: FloatProperty(name="Sheet Width", subtype='DISTANCE', precision=4,
                         description="Paper width of the sheet")
    height: FloatProperty(name="Sheet Height", subtype='DISTANCE', precision=4,
                          description="Paper height of the sheet")
    dpi: FloatProperty(name="Resolution", description="Pixels per inch of the sheet")
    massstab: FloatProperty(
        name="Maßstab", description="Scale of the plan, 1:x. Sets the object scale",
        get=_get_massstab, set=_set_massstab, min=1e-6, soft_min=1, soft_max=100000,
        precision=1, step=100)
    orientation: EnumProperty(
        name="Orientation",
        items=[('FLAT', "Flat", "Lageplan: lies on the ground (XY)"),
               ('UPRIGHT', "Upright", "Ansicht or Schnitt: stands upright (XZ)")],
        update=_set_orientation)
    full_sheet: BoolProperty(name="Full Sheet", description="Show the whole sheet, "
                             "for example the Legende, instead of the cropped part",
                             update=_set_full_sheet)
    opacity: FloatProperty(name="Opacity", min=0, max=1, default=1, subtype='FACTOR',
                           update=_set_opacity,
                           description="Opacity of the whole plan")
    background: FloatProperty(
        name="Background", min=0, max=1, default=0, subtype='FACTOR', update=_set_background,
        description="Opacity of the white paper. 0: the plan lies on the scene like a print "
        "on tracing paper (white vanishes, colors tint what is below). 1: the plan as "
        "printed. Solid view shows the paper from 0.5 on")
    page: IntProperty(default=1, min=1, description="PDF page of the plan")

    crs: EnumProperty(name="Coordinates", items=plan_geo.CRS_ITEMS, default='DB_REF',
                      update=_crs_changed,
                      description="Coordinate system of the grid crosses on the plan")
    utm_zone: IntProperty(name="UTM Zone", min=1, max=60, default=32, update=_crs_changed,
                          description="UTM zone, for eastings without the zone in front")
    snap: BoolProperty(name="Snap to Crosses", default=True,
                       description="Move each clicked point to the center of the "
                       "nearest grid cross")
    grid_spacing: IntProperty(name="Grid Spacing", min=0, default=0, update=_redraw,
                              description="Distance between grid crosses in meters. "
                              "0: from the entered coordinates")
    show_grid: BoolProperty(name="Show Grid", default=True, update=_redraw,
                            description="Draw the grid that follows from the points, "
                            "to check them against the crosses on the plan")
    points: CollectionProperty(type=PlanPoint)
    point_index: IntProperty()

    pairs: CollectionProperty(type=AlignPair)
    pair_index: IntProperty()
    fit_scale: BoolProperty(name="Fit Scale", default=False,
                            description="Also change the scale. Off keeps the Maßstab")


# ---------------------------------------------------------------- sheet setup

def _socket(interface, name, in_out, socket_type):
    return interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)


def _set_modifier_input(mod, name, value):
    for item in mod.node_group.interface.items_tree:
        if getattr(item, "in_out", None) == 'INPUT' and item.name == name:
            if hasattr(mod, "properties"):  # Blender 5.2+: inputs are RNA, not ID properties
                getattr(mod.properties.inputs, item.identifier).value = value
            else:
                mod[item.identifier] = value
            return


def _value_input(node):
    sockets = [s for s in node.inputs if s.name == "Value"]
    enabled = [s for s in sockets if getattr(s, "enabled", True)]
    return (enabled or sockets)[0]


def sheet_node_group():
    """Geometry Nodes group that sets the UV map from positions.

    UV = position / sheet size, so editing the mesh crops the image instead
    of moving it. "Full Sheet" replaces the mesh with the whole sheet.
    """
    ng = bpy.data.node_groups.get(NODE_GROUP)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(NODE_GROUP, 'GeometryNodeTree')
    _socket(ng.interface, "Geometry", 'INPUT', 'NodeSocketGeometry')
    _socket(ng.interface, "Width", 'INPUT', 'NodeSocketFloat')
    _socket(ng.interface, "Height", 'INPUT', 'NodeSocketFloat')
    _socket(ng.interface, "Full Sheet", 'INPUT', 'NodeSocketBool')
    _socket(ng.interface, "Material", 'INPUT', 'NodeSocketMaterial')
    _socket(ng.interface, "Geometry", 'OUTPUT', 'NodeSocketGeometry')
    nodes, links = ng.nodes, ng.links
    gin = nodes.new('NodeGroupInput')
    gout = nodes.new('NodeGroupOutput')

    grid = nodes.new('GeometryNodeMeshGrid')
    grid.inputs["Vertices X"].default_value = 2
    grid.inputs["Vertices Y"].default_value = 2
    links.new(gin.outputs["Width"], grid.inputs["Size X"])
    links.new(gin.outputs["Height"], grid.inputs["Size Y"])
    half_w = nodes.new('ShaderNodeMath')
    half_w.operation = 'MULTIPLY'
    half_w.inputs[1].default_value = 0.5
    links.new(gin.outputs["Width"], half_w.inputs[0])
    half_h = nodes.new('ShaderNodeMath')
    half_h.operation = 'MULTIPLY'
    half_h.inputs[1].default_value = 0.5
    links.new(gin.outputs["Height"], half_h.inputs[0])
    offset = nodes.new('ShaderNodeCombineXYZ')
    links.new(half_w.outputs[0], offset.inputs["X"])
    links.new(half_h.outputs[0], offset.inputs["Y"])
    move = nodes.new('GeometryNodeTransform')
    links.new(grid.outputs["Mesh"], move.inputs["Geometry"])
    links.new(offset.outputs[0], move.inputs["Translation"])

    switch = nodes.new('GeometryNodeSwitch')
    switch.input_type = 'GEOMETRY'
    links.new(gin.outputs["Full Sheet"], switch.inputs["Switch"])
    links.new(gin.outputs["Geometry"], switch.inputs["False"])
    links.new(move.outputs["Geometry"], switch.inputs["True"])

    position = nodes.new('GeometryNodeInputPosition')
    sep = nodes.new('ShaderNodeSeparateXYZ')
    links.new(position.outputs[0], sep.inputs[0])
    u = nodes.new('ShaderNodeMath')
    u.operation = 'DIVIDE'
    links.new(sep.outputs["X"], u.inputs[0])
    links.new(gin.outputs["Width"], u.inputs[1])
    v = nodes.new('ShaderNodeMath')
    v.operation = 'DIVIDE'
    links.new(sep.outputs["Y"], v.inputs[0])
    links.new(gin.outputs["Height"], v.inputs[1])
    uv = nodes.new('ShaderNodeCombineXYZ')
    links.new(u.outputs[0], uv.inputs["X"])
    links.new(v.outputs[0], uv.inputs["Y"])

    store = nodes.new('GeometryNodeStoreNamedAttribute')
    store.data_type = 'FLOAT2'
    store.domain = 'CORNER'
    store.inputs["Name"].default_value = "UVMap"
    links.new(switch.outputs[0], store.inputs["Geometry"])
    links.new(uv.outputs[0], _value_input(store))

    set_mat = nodes.new('GeometryNodeSetMaterial')
    links.new(store.outputs["Geometry"], set_mat.inputs["Geometry"])
    links.new(gin.outputs["Material"], set_mat.inputs["Material"])

    links.new(set_mat.outputs["Geometry"], gout.inputs[0])

    for i, node in enumerate((gin, grid, half_w, half_h, offset, move, switch, position,
                              sep, u, v, uv, store, set_mat, gout)):
        node.location = (i * 180 - 1200, (i % 3) * -160)
    return ng


def plan_material(name, image):
    """Unlit material that lays the plan on the scene like a print on tracing paper.

    Background 0: Transparent BSDF tinted with the plan's colors, which
    multiplies the scene behind it with the plan (white vanishes, black
    stays black, colors tint). Background 1: the plan's colors, opaque.
    Opacity fades all of it. The image's own colors are used unchanged, in
    every color space step, so nothing gets washed out. Its alpha is only for
    Solid view.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_backface_culling = False
    mat.surface_render_method = 'BLENDED'  # colored transparency needs Blended
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = image
    tex.extension = 'CLIP'
    tex.location = (-700, 0)
    background = nodes.new('ShaderNodeValue')
    background.name = background.label = BACKGROUND_NODE
    background.outputs[0].default_value = 0.0
    background.location = (-450, 250)
    opacity = nodes.new('ShaderNodeValue')
    opacity.name = opacity.label = OPACITY_NODE
    opacity.outputs[0].default_value = 1.0
    opacity.location = (-200, 350)

    # The alpha (paper, for Solid view) blacks out the color the texture
    # gives: lay the image over white again, which is the paper it was on.
    on_white = nodes.new('ShaderNodeMix')
    on_white.data_type = 'RGBA'
    on_white.location = (-450, 200)
    sockets = {sock.identifier: sock for sock in on_white.inputs}
    links.new(tex.outputs["Alpha"], sockets["Factor_Float"])
    sockets["A_Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(tex.outputs["Color"], sockets["B_Color"])
    color = next(sock for sock in on_white.outputs if sock.identifier == "Result_Color")

    tint = nodes.new('ShaderNodeBsdfTransparent')
    tint.location = (-450, 50)
    links.new(color, tint.inputs["Color"])
    emission = nodes.new('ShaderNodeEmission')
    emission.location = (-450, -100)
    links.new(color, emission.inputs["Color"])
    paper = nodes.new('ShaderNodeMixShader')
    paper.location = (-200, 100)
    links.new(background.outputs[0], paper.inputs[0])
    links.new(tint.outputs[0], paper.inputs[1])
    links.new(emission.outputs[0], paper.inputs[2])

    clear = nodes.new('ShaderNodeBsdfTransparent')
    clear.location = (-200, -100)
    fade = nodes.new('ShaderNodeMixShader')
    fade.location = (50, 100)
    links.new(opacity.outputs[0], fade.inputs[0])
    links.new(clear.outputs[0], fade.inputs[1])
    links.new(paper.outputs[0], fade.inputs[2])
    out = nodes.new('ShaderNodeOutputMaterial')
    out.location = (250, 100)
    links.new(fade.outputs[0], out.inputs["Surface"])
    nodes.active = tex  # the image Solid view shows with Color: Texture
    return mat


def load_plan_image(png_path):
    image = bpy.data.images.load(png_path)
    # The alpha marks paper for Solid view; the shader lays the image on white.
    image.alpha_mode = 'STRAIGHT'
    if bpy.data.filepath:
        try:
            image.filepath = bpy.path.relpath(png_path)
        except ValueError:  # another drive than the .blend file
            pass
    else:
        image.pack()
    return image


def set_sheet_rectangle(obj, x0, y0, x1, y1):
    """Replace the plane's mesh with a rectangle in sheet (paper) meters."""
    mesh = obj.data
    pg = obj.bii_plan
    x0, x1 = sorted((max(0.0, min(pg.width, x0)), max(0.0, min(pg.width, x1))))
    y0, y1 = sorted((max(0.0, min(pg.height, y0)), max(0.0, min(pg.height, y1))))
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        raise ValueError("The crop rectangle is empty")
    mesh.clear_geometry()
    mesh.from_pydata([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x / pg.width, co.y / pg.height)
    mesh.update()


def outline_off_sheet(obj):
    """True if vertices of the mesh lie outside the sheet.

    The UV map and the points are in the mesh's own coordinates, which are
    sheet coordinates. Set Origin and Apply Rotation or Scale move the
    vertices, and then image and points no longer match the sheet.
    """
    mesh, pg = obj.data, obj.bii_plan
    if obj.mode == 'EDIT' or not mesh.vertices:
        return False
    import numpy as np
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    t = SHEET_TOLERANCE
    return bool((co[:, 0] < -t).any() or (co[:, 0] > pg.width + t).any()
                or (co[:, 1] < -t).any() or (co[:, 1] > pg.height + t).any()
                or (np.abs(co[:, 2]) > t).any())


def plan_output_path(source, page_index, page_count):
    """Where the PNG goes: "plans" next to the .blend file, else a temp folder.

    In the temp folder case the image is packed into the .blend file.
    """
    stem = os.path.splitext(os.path.basename(source))[0]
    if page_count > 1:
        stem += f"_p{page_index + 1}"
    if bpy.data.filepath:
        folder = os.path.join(os.path.dirname(bpy.data.filepath), "plans")
    else:
        folder = os.path.join(tempfile.gettempdir(), "bii_plans")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, stem + ".png")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}_{n}.png")
        n += 1
    return path


def create_plan_object(context, sheet, png_path, source, massstab, orientation, page_index=0,
                       page_count=1, background=0.0):
    name = os.path.splitext(os.path.basename(source))[0]
    if page_count > 1:
        name += f" p{page_index + 1}"
    image = load_plan_image(png_path)

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    pg = obj.bii_plan
    pg.is_plan = True
    pg.source = source
    pg.image = image
    pg.width = sheet.width_m
    pg.height = sheet.height_m
    pg.dpi = (sheet.dpi_x + sheet.dpi_y) / 2
    pg.page = page_index + 1
    set_sheet_rectangle(obj, 0, 0, sheet.width_m, sheet.height_m)

    mat = plan_material(name, image)
    mesh.materials.append(mat)
    pg.material = mat
    pg.background = background

    mod = obj.modifiers.new(MODIFIER, 'NODES')
    mod.node_group = sheet_node_group()
    _set_modifier_input(mod, "Width", sheet.width_m)
    _set_modifier_input(mod, "Height", sheet.height_m)
    _set_modifier_input(mod, "Material", mat)
    mod.show_in_editmode = True

    pg.massstab = massstab
    pg.orientation = orientation
    _set_orientation(pg, context)
    # Center the sheet on the 3D cursor; the origin stays at its lower left corner.
    center = obj.rotation_euler.to_matrix() @ (obj.data.vertices[2].co * (massstab / 2))
    obj.location = context.scene.cursor.location - center
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


# ---------------------------------------------------------------- operators

class ImportPlanOperator(bpy.types.Operator, ImportHelper):
    """Import a plan (PDF, TIFF, PNG, JPEG) as a plane at its true size"""
    bl_idname = "import_image.bii_plan"
    bl_label = "Import Plan"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filter_glob: StringProperty(default="*.pdf;*.tif;*.tiff;*.png;*.jpg;*.jpeg;*.bmp",
                                options={'HIDDEN'})
    massstab: FloatProperty(name="Maßstab 1:", default=1000, min=1e-6, soft_min=1,
                            description="Scale printed on the plan. Can be changed later")
    orientation: EnumProperty(
        name="Orientation",
        items=[('FLAT', "Flat", "Lageplan: lies on the ground (XY)"),
               ('UPRIGHT', "Upright", "Ansicht or Schnitt: stands upright (XZ)")])
    background: FloatProperty(
        name="Background", min=0, max=1, default=0, subtype='FACTOR',
        description="Opacity of the white paper, can be changed later. 0: the plan lies on "
        "the scene like a print on tracing paper, the lines over maps and models. 1: the "
        "plan as printed")
    page: IntProperty(name="PDF Page", default=1, min=1)
    dpi: FloatProperty(name="PDF Resolution", default=0, min=0, soft_max=1200,
                       description="Pixels per inch for PDF pages. 0: the resolution "
                       "of the scan in the PDF, 300 for vector drawings")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "massstab")
        layout.prop(self, "orientation")
        layout.prop(self, "background")
        col = layout.column(heading="PDF")
        col.prop(self, "page")
        col.prop(self, "dpi")

    def execute(self, context):
        from . import plan_raster
        path = self.filepath
        try:
            page_index, page_count = 0, 1
            if os.path.splitext(path)[1].lower() in plan_raster.PDF_EXTENSIONS:
                page_index, page_count = self.page - 1, plan_raster.pdf_page_count(path)
            if context.window:
                context.window.cursor_set('WAIT')
            sheet = plan_raster.load(path, page_index, self.dpi)
            png = plan_output_path(path, page_index, page_count)
            kind = plan_raster.write_png(sheet, png)
        except ImportError as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"Pillow or pypdfium2 cannot be loaded: {e}")
            return {'CANCELLED'}
        except Exception as e:  # unreadable file, missing page, ...
            traceback.print_exc()
            self.report({'ERROR'}, f"Cannot read {os.path.basename(path)}: {e}")
            return {'CANCELLED'}
        finally:
            if context.window:
                context.window.cursor_set('DEFAULT')
        obj = create_plan_object(context, sheet, png, path, self.massstab, self.orientation,
                                 page_index, page_count, self.background)
        w, h = sheet.image.size
        text = (f"{obj.name}: {w} x {h} px, {sheet.width_m * 1000:.0f} x "
                f"{sheet.height_m * 1000:.0f} mm, {kind}")
        if sheet.note:
            text += f"; {sheet.note}"
        if max(w, h) > 16384:
            text += "; larger than most GPUs can show, the viewport shows it scaled down"
        self.report({'WARNING'} if "check" in sheet.note else {'INFO'}, text)
        return {'FINISHED'}


class PlanCropResetOperator(bpy.types.Operator):
    """Show the whole sheet again, removing the crop"""
    bl_idname = "bii_plan.crop_reset"
    bl_label = "Reset Crop"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.bii_plan.is_plan and obj.mode == 'OBJECT'

    def execute(self, context):
        pg = context.object.bii_plan
        set_sheet_rectangle(context.object, 0, 0, pg.width, pg.height)
        return {'FINISHED'}


class IO_FH_bii_plan(bpy.types.FileHandler):
    """Drop PDF plans into the 3D viewport"""
    bl_idname = "IO_FH_bii_plan"
    bl_label = "Plan"
    bl_import_operator = ImportPlanOperator.bl_idname
    bl_file_extensions = ".pdf"

    @classmethod
    def poll_drop(cls, context):
        return context.area and context.area.type == 'VIEW_3D'


def menu_func_import(self, context):
    self.layout.operator(ImportPlanOperator.bl_idname, text="Plan (.pdf, .tif, .png, .jpg)")


classes = (PlanPoint, AlignPair, PlanSettings, ImportPlanOperator, PlanCropResetOperator,
           IO_FH_bii_plan)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.bii_plan = PointerProperty(type=PlanSettings)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    del bpy.types.Object.bii_plan
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

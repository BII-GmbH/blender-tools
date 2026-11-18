"""Signal panel: configure and place railway signals by their Lageplan symbol.

Sidebar > BII Signal. The composite assets of DB_Signale.blend (one per
Bauform, see signal_spec: main and distant signals, Zs, Lf, Ne, Ls) have a
Geometry Nodes modifier "Signal" with one input per element. The panel shows each element's options as Lageplan symbols
(signal_symbols), a preview of the symbol of the whole combination, and the
aspects of the Signalbild menu that light something. It works on the active
object if that is such a signal, else on a draft that "Place Signal" puts at
the 3D cursor.
"""

import json
import math
import os
import re

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from mathutils import Vector

from . import plan_km, plan_place, signal_spec as spec, signal_symbols as symbols
from .import_plan import _redraw
from .plan_georef import wrapped_label

LIBRARY_FILE = "DB_Signale.blend"
MODIFIER = "Signal"
ICON_SIZE = 64
PREVIEW_SIZE = 256
TILE_SIZE = 128      # pixels of the option icons in the tiles and popups
TILE_SCALE = 4.0     # tile size in UI units
TILE_COLUMNS = 3
POPUP_SCALE = 5.0

_previews = None


# ---------------------------------------------------------------- icons

def _icon(elements, size=ICON_SIZE):
    """icon_id of a symbol, drawn once and kept in the preview collection."""
    key = f"{size}:{json.dumps(elements)}"
    if key in _previews:
        return _previews[key].icon_id
    p = _previews.new(key)
    p.image_size = (size, size)
    p.image_pixels_float = symbols.draw(elements, size).ravel().tolist()
    return p.icon_id


# ---------------------------------------------------------------- signal on an object

def _active(context):
    """Active object; also from scripts and timers, where the context has no active_object."""
    return context.view_layer.objects.active if context.view_layer else None


def signal_modifier(obj):
    """The "Signal" modifier of a composite signal asset, or None."""
    if obj is None:
        return None
    mod = obj.modifiers.get(MODIFIER)
    if mod and mod.type == 'NODES' and mod.node_group and bauform_of(mod):
        return mod
    return None


def bauform_of(mod):
    name = mod.node_group.name.split(".")[0]
    b = spec.BY_ASSET.get(name[len("Signal "):]) if name.startswith("Signal ") else None
    return b.key if b else None


def _inputs(mod):
    return {s.name: s for s in mod.node_group.interface.items_tree
            if getattr(s, "item_type", "SOCKET") == "SOCKET" and s.in_out == 'INPUT'}


def read_modifier(mod):
    """{input name: value}: menu item name (Blender 5.2+) or index, or bool."""
    out = {}
    props = getattr(mod, "properties", None)
    for name, sock in _inputs(mod).items():
        if sock.socket_type == 'NodeSocketGeometry':
            continue
        if props is not None and hasattr(props, "inputs"):
            out[name] = getattr(props.inputs, sock.identifier).value
        else:
            out[name] = mod[sock.identifier]
    return out


def write_modifier(mod, values):
    """Set inputs from {input name: menu index or bool}."""
    try:
        _write_modifier(mod, values)
    except TypeError:
        # Blender 5.2+: menu items exist once the node tree was evaluated (new objects)
        bpy.context.view_layer.update()
        _write_modifier(mod, values)


def _write_modifier(mod, values):
    props = getattr(mod, "properties", None)
    socks = _inputs(mod)
    for name, value in values.items():
        sock = socks.get(name)
        if sock is None:
            continue
        if props is not None and hasattr(props, "inputs"):
            item = getattr(props.inputs, sock.identifier)
            if sock.socket_type == 'NodeSocketMenu':
                item.value = _menu_label(mod, name, value)
            else:
                item.value = value
        else:
            mod[sock.identifier] = value
    mod.id_data.update_tag()


def _menu_label(mod, name, index):
    b = bauform_of(mod)
    if name == "Signalbild":
        return spec.all_aspects(b)[index]
    e = next(e for e in spec.elements(b) if e.label == name)
    return e.options[index][1]


def object_state(mod):
    """(bauform, config, aspect) of a placed signal."""
    b = bauform_of(mod)
    values = read_modifier(mod)
    cfg = spec.config_from_modifier(b, values)
    aspect = values.get("Signalbild", 0)
    if not isinstance(aspect, str):
        aspect = spec.all_aspects(b)[int(aspect)]
    return b, cfg, aspect


# ---------------------------------------------------------------- draft (for placing)

def _bauform_items(self, context):
    return [(b.key, b.label, "") for b in spec.BAUFORMEN]


def _library_items(self, context):
    items = [("AUTO", "Automatic", f"First asset library that has {LIBRARY_FILE}")]
    for lib in context.preferences.filepaths.asset_libraries:
        if os.path.isfile(os.path.join(bpy.path.abspath(lib.path), LIBRARY_FILE)):
            items.append((lib.name, lib.name, lib.path))
    return items


def _kennziffer_changed(self, context):
    cfg = self.get()
    if "kennziffer" in cfg:
        cfg["kennziffer"] = self.kennziffer
        self.set(cfg)
    _redraw(self, context)


# ---------------------------------------------------------------- big icon pickers
# template_icon_view is the only clickable widget that draws icons large: one tile per element shows
# the current option, a click opens all options as large icons. The texts are in the tooltips.

_pick_items = {}   # Blender needs the item lists of dynamic enums kept alive


def _option_icon(bauform, key, value, cfg):
    els = spec.option_symbol(bauform, key, value, cfg)
    return _icon(els, TILE_SIZE) if els else 'X'


def _pick_option_items(key):
    e = spec.ELEMENT[key]

    def items(self, context):
        b, cfg, _, _ = state(context)
        out = []
        if key in cfg:
            for n, (value, label, ok) in enumerate(spec.options(b, key, cfg)):
                if ok or cfg[key] == value:  # options that do not fit the rest are left out
                    out.append((str(value), label, f"{e.label}: {label}", _option_icon(b, key, value, cfg), n))
        _pick_items[key] = out or [("NONE", "", "", 'BLANK1', -1)]
        return _pick_items[key]

    def get(self):
        cfg = state(bpy.context)[1]
        return e.values().index(cfg[key]) if key in cfg else -1

    def set(self, n):
        b, cfg, aspect, _ = state(bpy.context)
        if key in cfg and 0 <= n < len(e.values()):
            cfg[key] = e.values()[n]
            apply(bpy.context, b, cfg, aspect)

    return EnumProperty(name=e.label, description=e.description, items=items, get=get, set=set)


def _pick_bauform_items(self, context):
    _pick_items["bauform"] = [
        (b.key, b.label, f"{b.group}: {b.label}", _icon(spec.symbol_elements(b.key, spec.config(b.key)), TILE_SIZE), n)
        for n, b in enumerate(spec.BAUFORMEN)]
    return _pick_items["bauform"]


def _pick_bauform_get(self):
    return [b.key for b in spec.BAUFORMEN].index(bpy.context.scene.bii_signal.bauform)


def _pick_bauform_set(self, n):
    choose_bauform(bpy.context, spec.BAUFORMEN[n].key)


# Scene.bii_signal.picks: one enum per element (pick_<key>) and pick_bauform
SignalPicks = type("SignalPicks", (bpy.types.PropertyGroup,), {"__annotations__": {
    **{f"pick_{e.key}": _pick_option_items(e.key) for e in spec.ELEMENTS if not e.number},
    "pick_bauform": EnumProperty(name="Bauform", description="Signal type to place", items=_pick_bauform_items,
                                 get=_pick_bauform_get, set=_pick_bauform_set),
}})


class SignalDraft(bpy.types.PropertyGroup):
    picks: bpy.props.PointerProperty(type=SignalPicks)
    bauform: EnumProperty(name="Bauform", items=_bauform_items, update=_redraw)
    config: StringProperty(name="Configuration", default="{}")
    kennziffer: IntProperty(name="Kennziffer", min=0, max=16, update=_kennziffer_changed,
                            description=spec.ELEMENT["kennziffer"].description)
    aspect: StringProperty(name="Signalbild", default="Aus")
    library: EnumProperty(name="Library", items=_library_items,
                          description=f"Asset library to take the signal from (it needs {LIBRARY_FILE})")
    align_to_track: BoolProperty(
        name="Align to Track", default=True,
        description="Turn the signal to the nearest Umgebung track axis (Gleisachse), lamps facing along it")

    def get(self):
        cfg = json.loads(self.config or "{}")
        cfg = {k: v for k, v in cfg.items() if k in spec.config(self.bauform)}
        if "kennziffer" in spec.config(self.bauform):
            cfg["kennziffer"] = self.kennziffer
        return spec.fix(self.bauform, spec.config(self.bauform, **cfg))

    def set(self, cfg):
        self.config = json.dumps(cfg)


def state(context):
    """(bauform, config, aspect, modifier or None) the panel shows."""
    mod = signal_modifier(_active(context))
    if mod:
        return (*object_state(mod), mod)
    d = context.scene.bii_signal
    return d.bauform, d.get(), d.aspect, None


def apply(context, bauform, cfg, aspect):
    """Store a configuration on the active signal or the draft."""
    cfg = spec.fix(bauform, cfg)
    if aspect not in spec.aspects(bauform, cfg):
        aspect = "Aus"
    obj = _active(context)
    mod = signal_modifier(obj)
    if mod:
        old = object_state(mod)
        values = spec.modifier_values(bauform, cfg)
        values["Signalbild"] = spec.all_aspects(bauform).index(aspect)
        write_modifier(mod, values)
        # keep the name in step, unless the user renamed the signal
        base = re.sub(r"\.\d{3}$", "", obj.name)
        if base in (spec.object_name(old[0], old[1]), spec.BY_KEY[bauform].asset):
            obj.name = spec.object_name(bauform, cfg)
    else:
        d = context.scene.bii_signal
        d.set(cfg)
        d.aspect = aspect
    _redraw(None, context)


# ---------------------------------------------------------------- operators

def _parse(element, text):
    e = spec.ELEMENT[element]
    return text == "True" if e.boolean else text


class SignalSetOptionOperator(bpy.types.Operator):
    """Choose this option for the signal"""
    bl_idname = "bii_signal.set_option"
    bl_label = "Set Option"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    element: StringProperty()
    value: StringProperty()

    @classmethod
    def description(cls, context, props):
        e = spec.ELEMENT.get(props.element)
        if not e:
            return ""
        return f"{e.label}: {e.option_label(_parse(props.element, props.value))}. {e.description}"

    def execute(self, context):
        b, cfg, aspect, _ = state(context)
        cfg[self.element] = _parse(self.element, self.value)
        apply(context, b, cfg, aspect)
        return {'FINISHED'}


class SignalSetBauformOperator(bpy.types.Operator):
    """Signal type (Bauform) to place"""
    bl_idname = "bii_signal.set_bauform"
    bl_label = "Set Bauform"
    bl_options = {'REGISTER', 'INTERNAL'}

    bauform: StringProperty()

    @classmethod
    def description(cls, context, props):
        b = spec.BY_KEY.get(props.bauform)
        return b.label if b else ""

    def execute(self, context):
        choose_bauform(context, self.bauform)
        return {'FINISHED'}


def choose_bauform(context, bauform):
    """Switch the draft to another Bauform, keeping the options both have."""
    d = context.scene.bii_signal
    old = d.get()
    d.bauform = bauform
    new = spec.config(bauform)
    for k in new:
        if k in old:
            new[k] = old[k]
    d.set(spec.fix(bauform, new))
    _redraw(None, context)


class SignalSetAspectOperator(bpy.types.Operator):
    """Light the lamps of this aspect (Signalbild)"""
    bl_idname = "bii_signal.set_aspect"
    bl_label = "Set Signalbild"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    aspect: StringProperty()

    def execute(self, context):
        b, cfg, _, _ = state(context)
        apply(context, b, cfg, self.aspect)
        return {'FINISHED'}


class SignalTakeOperator(bpy.types.Operator):
    """Use the active signal's setup for new signals (shown when no signal is selected)"""
    bl_idname = "bii_signal.take"
    bl_label = "Use for Placing"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return signal_modifier(_active(context)) is not None

    def execute(self, context):
        b, cfg, aspect = object_state(signal_modifier(_active(context)))
        d = context.scene.bii_signal
        d.bauform = b
        d.set(cfg)
        d.aspect = aspect
        if "kennziffer" in cfg:
            d.kennziffer = cfg["kennziffer"]
        return {'FINISHED'}


_asset_names = {}


def asset_names(path):
    """Names of the composite signal assets in a .blend file: objects with their node group
    "Signal <name>" (read once per file version). Older single assets of the same name do not count."""
    key = (path, os.path.getmtime(path))
    if key not in _asset_names:
        with bpy.data.libraries.load(path) as (src, _dst):
            groups = set(src.node_groups)
            _asset_names[key] = {o for o in src.objects if f"Signal {o}" in groups}
    return _asset_names[key]


def library_path(context, name="AUTO", asset=None):
    """(path of DB_Signale.blend, import method, library name) of the asset library to use,
    or (None, None, None). Automatic takes the first library whose DB_Signale.blend has the
    asset: an older library (without the Signal-Baukasten) is skipped."""
    for lib in context.preferences.filepaths.asset_libraries:
        if name not in ("AUTO", "", lib.name):
            continue
        path = os.path.join(bpy.path.abspath(lib.path), LIBRARY_FILE)
        if not os.path.isfile(path):
            continue
        if asset is None or asset in asset_names(path):
            return path, getattr(lib, "import_method", 'APPEND_REUSE'), lib.name
    return None, None, None


def missing_text(context, name, asset):
    if library_path(context, name)[0]:
        which = "this library's" if name not in ("AUTO", "") else "every"
        return (f"{asset} is not in {which} {LIBRARY_FILE}: it is an older version without the Signal-Baukasten. "
                "Choose another Library, or refresh the BII asset library.")
    return (f"No asset library has {LIBRARY_FILE}. Add the BII asset library under "
            "Preferences > File Paths > Asset Libraries.")


def load_signal_object(path, asset, method):
    """A new local object of the asset, its data imported like the Asset Browser does."""
    link = method in ('LINK', 'PACK')
    kwargs = {"link": link}
    if method == 'PACK':
        kwargs["pack"] = True
    if method == 'APPEND_REUSE':
        kwargs["reuse_local_id"] = True
    try:
        with bpy.data.libraries.load(path, **kwargs) as (src, dst):
            dst.objects = [asset]
    except TypeError:  # Blender without pack / reuse_local_id
        with bpy.data.libraries.load(path, link=link) as (src, dst):
            dst.objects = [asset]
    obj = dst.objects[0]
    if obj is None:
        raise RuntimeError(f"{asset} not found in {path}")
    if obj.library is not None:
        obj = obj.make_local()  # object local, mesh and node groups stay linked / packed
    if obj.asset_data:
        obj.asset_clear()
    return obj


def track_direction(context, point):
    """Unit XY tangent of the nearest Umgebung track axis at point, or None."""
    best = None
    for pts in plan_place.track_polylines(context.scene, ""):
        d, _q, tangent, _i, _t = plan_km.nearest_on_polyline(pts, point.x, point.y)
        if best is None or d < best[0]:
            best = (d, tangent)
    return best[1] if best else None


class SignalPlaceOperator(bpy.types.Operator):
    """Place a signal as shown above at the 3D cursor (with a signal active: another one like it)"""
    bl_idname = "bii_signal.place"
    bl_label = "Place Signal"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        d = context.scene.bii_signal
        bauform, cfg, aspect, _ = state(context)
        b = spec.BY_KEY[bauform]
        path, method, _lib = library_path(context, d.library, b.asset)
        if not path:
            self.report({'ERROR'}, missing_text(context, d.library, b.asset))
            return {'CANCELLED'}
        try:
            obj = load_signal_object(path, b.asset, method)
        except (OSError, RuntimeError, KeyError) as e:
            self.report({'ERROR'}, f"Could not load {b.asset}: {e}")
            return {'CANCELLED'}
        context.collection.objects.link(obj)
        cursor = context.scene.cursor
        obj.location = cursor.location
        obj.rotation_euler = (0.0, 0.0, cursor.rotation_euler.z)
        if d.align_to_track:
            t = track_direction(context, cursor.location)
            if t is not None:
                # local +Y along the track: the lamps (-Y) face the trains coming along -t
                obj.rotation_euler = (0.0, 0.0, math.atan2(t[1], t[0]) - math.pi / 2)
        for o in context.view_layer.objects:
            if o.select_get():
                o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        mod = signal_modifier(obj)
        if mod:
            values = spec.modifier_values(bauform, cfg)
            aspect = aspect if aspect in spec.aspects(bauform, cfg) else "Aus"
            values["Signalbild"] = spec.all_aspects(bauform).index(aspect)
            write_modifier(mod, values)
        obj.name = spec.object_name(bauform, cfg)
        return {'FINISHED'}


class SignalTurnOperator(bpy.types.Operator):
    """Turn the selected signals by 180° about the vertical axis (face the other direction)"""
    bl_idname = "bii_signal.turn"
    bl_label = "Turn 180°"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.select_get() for o in context.view_layer.objects)

    def execute(self, context):
        for o in [o for o in context.view_layer.objects if o.select_get()]:
            o.rotation_euler.z += math.pi
        return {'FINISHED'}


# ---------------------------------------------------------------- panel

class VIEW3D_PT_bii_signal(bpy.types.Panel):
    bl_label = "Signal"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BII Signal"

    def draw(self, context):
        layout = self.layout
        b, cfg, aspect, mod = state(context)
        bf = spec.BY_KEY[b]

        box = layout.box()
        row = box.row()
        row.alignment = 'CENTER'
        row.template_icon(icon_value=_icon(spec.symbol_elements(b, cfg), PREVIEW_SIZE), scale=7.0)
        if mod:
            box.label(text=f"Active: {_active(context).name}", icon='OBJECT_DATA')
        else:
            box.label(text="New signal (not placed yet)", icon='ADD')

        # one large tile per choice: the current option; a click shows all options (texts in tooltips)
        picks = context.scene.bii_signal.picks
        grid = layout.grid_flow(row_major=True, columns=TILE_COLUMNS, even_columns=True, align=False)
        if not mod:
            grid.template_icon_view(picks, "pick_bauform", show_labels=False, scale=TILE_SCALE,
                                    scale_popup=POPUP_SCALE)
        numbers = []
        for e in spec.elements(b):
            if not spec.shown(b, e.key, cfg):
                continue
            if e.number:
                numbers.append(e)
                continue
            grid.template_icon_view(picks, f"pick_{e.key}", show_labels=False, scale=TILE_SCALE,
                                    scale_popup=POPUP_SCALE)
        for e in numbers:
            draw_number(layout.column(), context, mod, e)

        lit = [a for a in spec.aspects(b, cfg) if a != "Aus"]
        col = layout.column(align=True)
        if lit:
            col.label(text="Signalbild")
        grid = col.grid_flow(columns=3, align=True, even_columns=True)
        for a in spec.aspects(b, cfg) if lit else []:
            grid.operator(SignalSetAspectOperator.bl_idname, text=a, depress=a == aspect).aspect = a

        layout.separator()
        if mod:
            row = layout.row(align=True)
            row.operator(SignalTakeOperator.bl_idname, icon='COPYDOWN')
            row.operator(SignalTurnOperator.bl_idname, icon='FILE_REFRESH')
        d = context.scene.bii_signal
        col = layout.column()
        col.use_property_split = True
        col.prop(d, "library")
        col.prop(d, "align_to_track")
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(SignalPlaceOperator.bl_idname, text="Place Another" if mod else "Place Signal",
                     icon='EMPTY_SINGLE_ARROW')
        lib = library_path(context, d.library, bf.asset)[2]
        if lib:
            col.label(text=f"From: {lib}", icon='ASSET_MANAGER')
        else:
            wrapped_label(layout, context, missing_text(context, d.library, bf.asset), 'ERROR')


def draw_number(layout, context, mod, e):
    """Number field of a number element: on the active signal's modifier, else on the draft."""
    if mod is None:
        layout.prop(context.scene.bii_signal, e.key, text=e.label)
        return
    sock = _inputs(mod).get(e.label)
    if sock is None:
        return
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        layout.prop(getattr(props.inputs, sock.identifier), "value", text=e.label)
    else:
        layout.prop(mod, f'["{sock.identifier}"]', text=e.label)


classes = (
    SignalPicks,
    SignalDraft,
    SignalSetOptionOperator,
    SignalSetBauformOperator,
    SignalSetAspectOperator,
    SignalTakeOperator,
    SignalPlaceOperator,
    SignalTurnOperator,
    VIEW3D_PT_bii_signal,
)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.bii_signal = bpy.props.PointerProperty(type=SignalDraft)


def unregister():
    global _previews
    del bpy.types.Scene.bii_signal
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None

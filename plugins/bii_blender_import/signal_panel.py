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
import time

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import plan_km, plan_place, signal_spec as spec, signal_symbols as symbols
from .import_plan import _redraw
from .plan_georef import wrapped_label

LIBRARY_FILE = "DB_Signale.blend"
MODIFIER = "Signal"
ICON_SIZE = 64
PREVIEW_SIZE = 256
TILE_SIZE = 128      # pixels of the option icons in the popups
POPUP_SCALE = 5.0    # size of the popup's symbols in UI units
ZOOM_SPLIT = 0.86    # share of a row for the option buttons

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

# ---------------------------------------------------------------- big icon pickers
# template_icon_view is the only widget that shows icons large: its button at the end of a row shows the
# chosen option, a click opens all options as large icons.

_pick_items = {}   # Blender needs the item lists of dynamic enums kept alive


def _option_icon(bauform, key, value, cfg):
    els = spec.option_symbol(bauform, key, value, cfg)
    return _icon(els, TILE_SIZE) if els else 'X'


def _pick(key):
    """Property of an element on the active signal or the draft: an enum of option icons,
    or an int for a number element."""
    e = spec.ELEMENT[key]

    def get(self):
        cfg = state(bpy.context)[1]
        if key not in cfg:
            return 0 if e.number else -1
        return cfg[key] if e.number else e.values().index(cfg[key])

    def set(self, n):
        b, cfg, aspect, _ = state(bpy.context)
        if key in cfg and (e.number or 0 <= n < len(e.values())):
            cfg[key] = n if e.number else e.values()[n]
            apply(bpy.context, b, cfg, aspect)

    if e.number:
        return IntProperty(name=e.label, description=e.description, min=e.number[0], max=e.number[1],
                           get=get, set=set)

    def items(self, context):
        b, cfg, _, _ = state(context)
        out = []
        if key in cfg:
            for n, (value, label, ok) in enumerate(spec.options(b, key, cfg)):
                if ok or cfg[key] == value:  # options that do not fit the rest are left out
                    out.append((str(value), label, f"{e.label}: {label}", _option_icon(b, key, value, cfg), n))
        _pick_items[key] = out or [("NONE", "", "", 'BLANK1', -1)]
        return _pick_items[key]

    return EnumProperty(name=e.label, description=e.description, items=items, get=get, set=set)


def _pick_bauform_items(self, context):
    _pick_items["bauform"] = [
        (b.key, b.label, f"{b.group}: {b.label}", _icon(spec.symbol_elements(b.key, spec.config(b.key)), TILE_SIZE), n)
        for n, b in enumerate(spec.BAUFORMEN)]
    return _pick_items["bauform"]


def _pick_bauform_get(self):
    return [b.key for b in spec.BAUFORMEN].index(state(bpy.context)[0])


def _pick_bauform_set(self, n):
    try:
        choose_bauform(bpy.context, spec.BAUFORMEN[n].key)
    except RuntimeError as e:  # a property setter cannot report: show it in a popup
        text = str(e)
        bpy.context.window_manager.popup_menu(lambda menu, _c: menu.label(text=text), title="BII Signal", icon='ERROR')


# Scene.bii_signal.picks: one property per element (pick_<key>) and pick_bauform
SignalPicks = type("SignalPicks", (bpy.types.PropertyGroup,), {"__annotations__": {
    **{f"pick_{e.key}": _pick(e.key) for e in spec.ELEMENTS},
    "pick_bauform": EnumProperty(name="Bauform", description="Signal type; a placed signal is replaced in place", items=_pick_bauform_items,
                                 get=_pick_bauform_get, set=_pick_bauform_set),
}})


class SignalDraft(bpy.types.PropertyGroup):
    picks: bpy.props.PointerProperty(type=SignalPicks)
    bauform: EnumProperty(name="Bauform", items=[(b.key, b.label, "") for b in spec.BAUFORMEN], update=_redraw)
    config: StringProperty(name="Configuration", default="{}")
    aspect: StringProperty(name="Signalbild", default="Aus")
    library: StringProperty(name="Library", description=f"Asset library to take the signal from (it needs "
                            f"{LIBRARY_FILE}). Empty: the first one that has the signal")
    align_to_track: BoolProperty(
        name="Align to Track", default=True,
        description="Turn the signal to the nearest Umgebung track axis (Gleisachse), lamps facing along it")

    def get(self):
        cfg = json.loads(self.config or "{}")
        cfg = {k: v for k, v in cfg.items() if k in spec.config(self.bauform)}
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


def write_signal(mod, bauform, cfg, aspect):
    """Write a configuration and aspect (Aus if it lights nothing there) to a signal's modifier."""
    values = spec.modifier_values(bauform, cfg)
    values["Signalbild"] = spec.all_aspects(bauform).index(aspect if aspect in spec.aspects(bauform, cfg) else "Aus")
    write_modifier(mod, values)


def auto_named(obj, bauform, cfg):
    """True if obj still has the name the panel gave it, i.e. the user did not rename it."""
    return re.sub(r"\.\d{3}$", "", obj.name) in (spec.object_name(bauform, cfg), spec.BY_KEY[bauform].asset)


def apply(context, bauform, cfg, aspect):
    """Store a configuration on the active signal or the draft."""
    cfg = spec.fix(bauform, cfg)
    if aspect not in spec.aspects(bauform, cfg):
        aspect = "Aus"
    obj = _active(context)
    mod = signal_modifier(obj)
    if mod:
        auto = auto_named(obj, *object_state(mod)[:2])
        write_signal(mod, bauform, cfg, aspect)
        if auto:  # keep the name in step, unless the user renamed the signal
            obj.name = spec.object_name(bauform, cfg)
    else:
        d = context.scene.bii_signal
        d.set(cfg)
        d.aspect = aspect
    _redraw(None, context)


# ---------------------------------------------------------------- operators

def _parse(element, text):
    """Option value from its str() in the operator property: "True" -> True, "2" -> 2."""
    for v in spec.ELEMENT[element].values():
        if str(v) == text:
            return v
    raise ValueError(f"{element}: unknown option {text!r}")


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
    """Signal type (Bauform); a placed signal is replaced by one of this type in the same place"""
    bl_idname = "bii_signal.set_bauform"
    bl_label = "Set Bauform"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    bauform: StringProperty()

    @classmethod
    def description(cls, context, props):
        b = spec.BY_KEY.get(props.bauform)
        return b.label if b else ""

    def execute(self, context):
        try:
            choose_bauform(context, self.bauform)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


def _carry(old_cfg, bauform):
    """Configuration of bauform with the options both Bauformen have taken from old_cfg."""
    new = spec.config(bauform)
    for k in new:
        if k in old_cfg:
            new[k] = old_cfg[k]
    return spec.fix(bauform, new)


def choose_bauform(context, bauform):
    """Switch to another Bauform, keeping the options both have: the active signal is replaced,
    else the draft changes. Raises RuntimeError if the library has no such signal."""
    obj = _active(context)
    mod = signal_modifier(obj)
    if mod:
        old_bauform, old_cfg, aspect = object_state(mod)
        if bauform != old_bauform:
            replace_signal(context, obj, old_bauform, old_cfg, aspect, bauform)
    else:
        d = context.scene.bii_signal
        cfg = _carry(d.get(), bauform)
        d.bauform = bauform
        d.set(cfg)
    _redraw(None, context)


def replace_signal(context, obj, old_bauform, old_cfg, aspect, bauform):
    """Put a signal of another Bauform in the place of obj (transform, collections, parent,
    children, name) and delete obj. Returns the new object."""
    b = spec.BY_KEY[bauform]
    d = context.scene.bii_signal
    path, method, _lib = library_path(context, d.library, b.asset)
    if not path:
        raise RuntimeError(missing_text(context, d.library, b.asset))
    try:
        new = load_signal_object(path, b.asset, method)
    except (OSError, KeyError) as e:
        raise RuntimeError(f"Could not load {b.asset}: {e}") from e
    for c in obj.users_collection:
        c.objects.link(new)
    new.parent = obj.parent
    new.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
    new.matrix_basis = obj.matrix_basis.copy()
    for child in obj.children:
        world = child.matrix_world.copy()
        child.parent = new
        child.matrix_world = world
    cfg = _carry(old_cfg, bauform)
    mod = signal_modifier(new)
    if mod:
        write_signal(mod, bauform, cfg, aspect)
    name, auto = obj.name, auto_named(obj, old_bauform, old_cfg)
    selected = obj.select_get()
    bpy.data.objects.remove(obj)
    new.name = spec.object_name(bauform, cfg) if auto else name  # a renamed signal keeps its name
    new.select_set(selected)
    context.view_layer.objects.active = new
    return new


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
    """Use the active signal's setup for new signals: Place Signal places it when no signal is active"""
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
        return {'FINISHED'}


_asset_names = {}  # path -> (time looked at, modification time or None, names or None)
RECHECK_SECONDS = 5


def asset_names(path):
    """Names of the composite signal assets in a .blend file, or None without the file: objects with
    their node group "Signal <name>". Older single assets of the same name do not count. The panel asks
    on every redraw, so the file is looked at only every few seconds and read once per version."""
    checked, mtime, names = _asset_names.get(path, (-math.inf, None, None))
    if time.monotonic() - checked >= RECHECK_SECONDS:
        new = os.path.getmtime(path) if os.path.isfile(path) else None
        if new != mtime or names is None:
            names = None
            if new is not None:
                with bpy.data.libraries.load(path) as (src, _dst):
                    groups = set(src.node_groups)
                    names = {o for o in src.objects if f"Signal {o}" in groups}
        _asset_names[path] = (time.monotonic(), new, names)
    return names


def library_path(context, name="", asset=None):
    """(path of DB_Signale.blend, import method, library name) of the asset library to use,
    or (None, None, None). Without a name, the first library whose DB_Signale.blend has the
    asset: an older library (without the Signal-Baukasten) is skipped."""
    for lib in context.preferences.filepaths.asset_libraries:
        if name not in ("", lib.name):
            continue
        path = os.path.join(bpy.path.abspath(lib.path), LIBRARY_FILE)
        names = asset_names(path)
        if names is not None and (asset is None or asset in names):
            return path, getattr(lib, "import_method", 'APPEND_REUSE'), lib.name
    return None, None, None


def missing_text(context, name, asset):
    if library_path(context, name)[0]:
        which = "this library's" if name else "every"
        return (f"{asset} is not in {which} {LIBRARY_FILE}: it is an older version without the Signal-Baukasten. "
                "Choose another Library, or refresh the BII asset library.")
    return (f"{'This' if name else 'No'} asset library has {LIBRARY_FILE}. Add the BII asset library under "
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
            write_signal(mod, bauform, cfg, aspect)
        obj.name = spec.object_name(bauform, cfg)
        return {'FINISHED'}


class SignalTurnOperator(bpy.types.Operator):
    """Turn the selected signals by 180° about the vertical axis (face the other direction)"""
    bl_idname = "bii_signal.turn"
    bl_label = "Turn 180°"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(signal_modifier(o) for o in context.selected_objects)

    def execute(self, context):
        for o in context.selected_objects:
            if signal_modifier(o):
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

        # every option as a small symbol button (names in the tooltips); the button at the end of a row
        # shows the chosen option and opens all options as large symbols
        picks = context.scene.bii_signal.picks
        # Bauform; for a placed signal, another Bauform replaces it in the same place
        col = layout.column()
        for n, group in enumerate(spec.GROUPS):
            row, zoom = _split(col, max(sum(o.group == g for o in spec.BAUFORMEN) for g in spec.GROUPS))
            for o in spec.BAUFORMEN:
                if o.group == group:
                    row.operator(SignalSetBauformOperator.bl_idname, text="",
                                 icon_value=_icon(spec.symbol_elements(o.key, spec.config(o.key))),
                                 depress=o.key == b).bauform = o.key
            _zoom(zoom, picks, "pick_bauform", visible=n == 0)
        layout.separator()

        col = layout.column()
        for e in spec.elements(b):
            if not spec.shown(b, e.key, cfg):
                continue
            if e.number:
                col.prop(picks, f"pick_{e.key}")
                continue
            row, zoom = _split(col, len(spec.ELEMENT[e.key].options))
            for value, label, ok in spec.options(b, e.key, cfg):
                els = spec.option_symbol(b, e.key, value, cfg)
                sub = row.column(align=True)
                sub.enabled = ok
                kw = {"icon_value": _icon(els)} if els else {"icon": 'X'}
                op = sub.operator(SignalSetOptionOperator.bl_idname, text="", depress=cfg[e.key] == value, **kw)
                op.element, op.value = e.key, str(value)
            _zoom(zoom, picks, f"pick_{e.key}")

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
        col.prop_search(d, "library", context.preferences.filepaths, "asset_libraries")
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


def _split(layout, columns):
    """(grid for the option buttons, cell at the end for the large-symbol button)"""
    split = layout.split(factor=ZOOM_SPLIT, align=True)
    grid = split.grid_flow(row_major=True, columns=columns, even_columns=True, align=True)
    return grid, split.row(align=True)


def _zoom(cell, picks, prop, visible=True):
    """Button at the end of a row: the chosen option; a click shows all options as large symbols."""
    if visible:
        cell.template_icon_view(picks, prop, show_labels=False, scale=1.0, scale_popup=POPUP_SCALE)
    else:
        cell.label(text="")


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

"""Erzeugt die Blender-Objekte: Kilometrierungslinie, Marken und Beschriftung.

Es wird bewusst keine Gleisgeometrie gebaut - nur die Achse als Linie sowie
die Kilometrierungswerte als Textobjekte.
"""

import math

import bpy
from mathutils import Vector

from . import railgeom

TOP_COLLECTION = "Kilometrierung"

# Nachkommastellen der exakten Kilometrierung am Anfangspunkt der Linie
START_DECIMALS = 3


# ------------------------------------------------------------- Hilfsmittel

def ensure_collection(name, parent):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if parent is not None and coll.name not in parent.children:
        try:
            parent.children.link(coll)
        except RuntimeError:
            pass
    return coll


def ensure_material(name, color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
    mat.diffuse_color = color
    if mat.use_nodes:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = color
            if "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = color
                bsdf.inputs["Emission Strength"].default_value = 0.6
    return mat


MARKER = "railway_chainage"


def clear_generated(collection):
    """Entfernt frühere Erzeugnisse dieser Erweiterung aus der Sammlung.

    Nur Objekte mit der Markierung werden gelöscht - eigene Objekte des
    Anwenders in derselben Sammlung bleiben unangetastet.
    """
    for obj in list(collection.objects):
        if not obj.get(MARKER):
            continue
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)


def unique_name(base):
    """Vorhandene Objekte gleichen Namens werden ersetzt, nicht dupliziert."""
    obj = bpy.data.objects.get(base)
    if obj is not None:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    return base


# --------------------------------------------------------------- Geometrie

def create_line(name, points, z, width, collection, material=None):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    if width > 0.0:
        curve.bevel_depth = width * 0.5
        curve.fill_mode = 'FULL'
    # Objektursprung an den Anfang der Strecke legen (statt an den Weltnullpunkt)
    ox, oy = points[0]
    spline = curve.splines.new('POLY')
    spline.points.add(len(points) - 1)
    for i, (x, y) in enumerate(points):
        spline.points[i].co = (x - ox, y - oy, 0.0, 1.0)

    obj = bpy.data.objects.new(unique_name(name), curve)
    obj.location = (ox, oy, z)
    obj[MARKER] = True
    if material:
        curve.materials.append(material)
    collection.objects.link(obj)
    return obj


def create_ticks(name, marks, z, length, length_km, collection, material=None):
    verts, edges = [], []
    for mark in marks:
        (x, y) = mark["pos"]
        tx, ty = mark["tangent"]
        nx, ny = -ty, tx
        half = (length_km if (mark["is_km"] or mark.get("is_start")) else length) * 0.5
        if half <= 0.0:
            continue
        i = len(verts)
        verts.append((x + nx * half, y + ny * half, z))
        verts.append((x - nx * half, y - ny * half, z))
        edges.append((i, i + 1))
    if not verts:
        return None

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(unique_name(name), mesh)
    obj[MARKER] = True
    if material:
        mesh.materials.append(material)
    collection.objects.link(obj)
    return obj


def _half_text_width(text, size):
    """Grobe halbe Textbreite - reicht, um Ueberlappungen zu erkennen."""
    return 0.5 * len(text) * size * 0.55


def create_labels(prefix, marks, props, z, collection, material=None):
    side = 1.0 if props.label_side == 'LEFT' else -1.0
    objects = []
    start_label = None   # (Position, halbe Breite) der Beschriftung am Linienanfang
    for index, mark in enumerate(marks):
        is_start = mark.get("is_start", False)
        if props.label_mode == 'KM_ONLY' and not mark["is_km"] and not is_start:
            continue
        if is_start:
            # exakter Kilometerwert am Anfang der Linie (metergenau)
            decimals = START_DECIMALS
        else:
            decimals = 0 if (props.label_mode == 'KM_ONLY' and props.decimals == 0) else props.decimals
        if props.label_format == 'DB':
            # Ril 883.0010, Abschnitt 3 (4): 13,4 bzw. 13,4+23,05
            text = props.label_prefix + railgeom.format_km_db(
                mark["km"], separator=props.decimal_separator)
        else:
            text = railgeom.format_km(mark["km"], decimals=decimals,
                                      separator=props.decimal_separator,
                                      prefix=props.label_prefix)
        emphasis = mark["is_km"] or is_start
        size = props.text_size * (props.km_text_factor if emphasis else 1.0)

        if is_start:
            start_label = (mark["pos"], _half_text_width(text, size))
        elif index == 1 and start_label is not None:
            # Die erste regulaere Marke liegt oft dicht hinter dem Linienanfang;
            # dann wuerde ihre Beschriftung die exakte Kilometrierung ueberdecken.
            gap = math.dist(start_label[0], mark["pos"])
            if gap < start_label[1] + _half_text_width(text, size):
                continue

        data = bpy.data.curves.new("%s %s" % (prefix, text), 'FONT')
        data.body = text
        data.size = size
        data.align_x = 'CENTER'
        data.align_y = 'CENTER'

        (x, y) = mark["pos"]
        tx, ty = mark["tangent"]
        nx, ny = -ty * side, tx * side
        offset = props.label_offset
        angle = math.atan2(ty, tx)
        if props.label_orientation == 'ACROSS':
            angle += math.pi * 0.5
        elif props.label_orientation == 'WORLD':
            angle = 0.0
        if props.label_orientation != 'WORLD':
            # Text nie auf dem Kopf stehend
            if math.cos(angle) < 0.0:
                angle += math.pi

        name = "%s %s" % (prefix, text)
        obj = bpy.data.objects.new(unique_name(name), data)
        obj[MARKER] = True
        obj.location = (x + nx * offset, y + ny * offset, z)
        obj.rotation_euler = (0.0, 0.0, angle)
        if material:
            data.materials.append(material)
        collection.objects.link(obj)
        objects.append(obj)
    return objects


# ------------------------------------------------------------------ Aufbau

def build_route(context, props, route_label, points, marks, fit, suffix="", total_length=0.0,
                axis_note="", crossings=0, interval=None):
    """Erzeugt Linie, Marken und Beschriftung einer Strecke.

    Rueckgabe: (Linienobjekt, Anzahl Beschriftungen)
    """
    scene_coll = context.scene.collection
    top = ensure_collection(TOP_COLLECTION, scene_coll)
    safe_label = route_label.replace("/", "-")
    name = "%s%s" % (safe_label, suffix)
    coll = ensure_collection("KM %s" % name, top)
    clear_generated(coll)

    material = None
    if props.create_material:
        material = ensure_material("Kilometrierung", tuple(props.color))

    z = props.z_offset
    line = create_line("KM-Linie %s" % name, points, z, props.line_width, coll, material)

    children = []
    if props.make_ticks:
        ticks = create_ticks("KM-Marken %s" % name, marks, z,
                             props.tick_length, props.tick_length_km, coll, material)
        if ticks:
            children.append(ticks)

    labels = []
    if props.make_labels:
        # Streckenname im Objektnamen, damit sich gleiche Kilometerwerte
        # verschiedener Strecken nicht gegenseitig verdrängen
        labels = create_labels("KM %s" % name, marks, props, z, coll, material)
        children.extend(labels)

    if props.parent_objects:
        # matrix_basis statt matrix_world: die Weltmatrix ist unmittelbar nach dem
        # Setzen von location noch nicht aktualisiert
        inverse = line.matrix_basis.inverted()
        for child in children:
            child.parent = line
            child.matrix_parent_inverse = inverse

    # Kilometrierungsdaten am Objekt hinterlegen (nachvollziehbar und skriptbar)
    line["chainage_source"] = getattr(fit, "source", "MANUAL")
    line["chainage_km_start"] = round(fit.km(0.0), START_DECIMALS)
    line["chainage_km_end"] = round(fit.km(total_length), START_DECIMALS)
    line["chainage_interval_m"] = props.interval if interval is None else interval
    line["chainage_axis_mode"] = props.axis_mode
    line["chainage_axis"] = axis_note
    line["chainage_track_crossings"] = crossings
    line["chainage_rule"] = "Ril 883.0010 (Orientierung; keine amtliche Vermessung)"
    line["chainage_distance_basis"] = "Planar XY along route; elevation does not recalibrate km"
    line["chainage_accuracy"] = "OSM approximation; terrain is not a surveyed rail elevation"
    line["chainage_milestones_used"] = getattr(fit, "inliers", 0)
    line["chainage_max_deviation_m"] = round(getattr(fit, "max_dev", 0.0), 2)

    return line, len(labels)


# =====================================================================
#  Geländemodell und Zuschnitt auf den Bahnkorridor
# =====================================================================

def create_terrain_segments(context, name, verts, faces, coords, collection,
                            height_offset=0.0, segment_length=0.0, radius=100.0):
    """Legt das Gelände in Abschnitten entlang der Strecke an.

    Jeder Abschnitt wird ein eigenes Objekt mit eigenen UV-Koordinaten und kann
    dadurch eine eigene, entsprechend höher aufgelöste Kartentextur tragen.
    Die Zuordnung erfolgt über die Station der Flächenmitte, sodass die
    Abschnitte lückenlos aneinandergrenzen.
    """
    if not faces:
        return []
    stations = [c[0] for c in coords] if coords else [0.0] * len(verts)
    total = max(stations) if stations else 0.0
    if segment_length and segment_length > 0.0 and total > segment_length:
        count = max(1, int(math.ceil(total / segment_length)))
    else:
        count = 1
    span = total / count if count else total

    buckets = {}
    for face in faces:
        middle = sum(stations[i] for i in face) / len(face)
        index = min(count - 1, max(0, int(middle / span))) if span else 0
        buckets.setdefault(index, []).append(face)

    objects = []
    for index in sorted(buckets):
        part_faces = buckets[index]
        used = {}
        part_verts, part_coords = [], []
        remapped = []
        for face in part_faces:
            new_face = []
            for vertex in face:
                if vertex not in used:
                    used[vertex] = len(part_verts)
                    x, y, z = verts[vertex]
                    part_verts.append((x, y, z - height_offset))
                    part_coords.append(coords[vertex] if coords else (0.0, 0.0))
                new_face.append(used[vertex])
            remapped.append(tuple(new_face))

        part_name = name if count == 1 else "%s %02d" % (name, index + 1)
        mesh = bpy.data.meshes.new(part_name)
        mesh.from_pydata(part_verts, [], remapped)
        mesh.update()
        obj = bpy.data.objects.new(unique_name(part_name), mesh)
        obj[MARKER] = True
        obj["height_offset"] = height_offset
        start = index * span
        obj["corridor_station_start"] = round(start, 2)
        obj["corridor_station_end"] = round(min(total, start + span), 2)
        collection.objects.link(obj)
        try:
            mesh.shade_smooth()
        except AttributeError:
            pass
        if coords:
            apply_corridor_uv(obj, part_coords, radius, span, station_offset=start)
        objects.append(obj)
    return objects


def create_terrain(context, name, verts, faces, collection, height_offset=0.0):
    """Legt das Geländemodell als Mesh an."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, y, z - height_offset) for x, y, z in verts], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(unique_name(name), mesh)
    obj[MARKER] = True
    obj["height_offset"] = height_offset
    collection.objects.link(obj)
    try:
        mesh.shade_smooth()
    except AttributeError:
        pass
    return obj


def _object_faces_outside(obj, corridor):
    """Löscht alle Flächen außerhalb des Korridors. Rückgabe (entfernt, verblieben)."""
    import bmesh
    mesh = obj.data
    matrix = obj.matrix_world
    bm = bmesh.new()
    bm.from_mesh(mesh)
    doomed = []
    for face in bm.faces:
        centre = matrix @ face.calc_center_median()
        if not corridor.contains(centre.x, centre.y):
            doomed.append(face)
    removed = len(doomed)
    if removed:
        bmesh.ops.delete(bm, geom=doomed, context='FACES')
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
    remaining = len(bm.faces)
    if removed:
        bm.to_mesh(mesh)
        mesh.update()
    bm.free()
    return removed, remaining


def _points_outside(obj, corridor):
    """Prüft, ob ein Objekt vollständig außerhalb des Korridors liegt."""
    matrix = obj.matrix_world
    corners = [matrix @ Vector(c) for c in obj.bound_box] if obj.bound_box else []
    if not corners:
        location = matrix.translation
        return not corridor.contains(location.x, location.y)
    return not any(corridor.contains(c.x, c.y) for c in corners)


def clip_scene(context, corridor, skip_objects=(), crop_textures=True, max_texture=4096):
    """Schneidet die Szene auf den Korridor zu.

    Mesh-Flächen außerhalb des Korridors werden gelöscht, vollständig außerhalb
    liegende Objekte entfernt. Anschließend werden die verwendeten Bildtexturen
    auf den verbleibenden Bereich zugeschnitten - sonst bliebe die Textur des
    gesamten Areals in voller Größe in der Datei.
    """
    protected = {obj.name for obj in skip_objects}
    stats = {"objekte_entfernt": 0, "flaechen_entfernt": 0, "objekte_zugeschnitten": 0,
             "texturen": []}

    for obj in list(context.scene.objects):
        if obj.name in protected or obj.get(MARKER):
            continue
        if obj.type == 'MESH' and obj.data and len(obj.data.polygons):
            removed, remaining = _object_faces_outside(obj, corridor)
            stats["flaechen_entfernt"] += removed
            if removed:
                stats["objekte_zugeschnitten"] += 1
            if not remaining:
                bpy.data.objects.remove(obj)
                stats["objekte_entfernt"] += 1
        elif obj.type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'META'}:
            if _points_outside(obj, corridor):
                bpy.data.objects.remove(obj)
                stats["objekte_entfernt"] += 1

    if crop_textures:
        stats["texturen"] = crop_scene_textures(context, protected, max_texture)
    return stats


def _images_of(obj):
    images = []
    for slot in obj.material_slots:
        material = slot.material
        if not material or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                images.append((material, node))
    return images


def crop_scene_textures(context, protected, max_texture):
    """Schneidet Bildtexturen auf den tatsächlich genutzten UV-Bereich zu."""
    import numpy as np

    usage = {}
    for obj in context.scene.objects:
        if obj.type != 'MESH' or obj.name in protected or obj.get(MARKER):
            continue
        mesh = obj.data
        if not mesh.uv_layers.active or not len(mesh.polygons):
            continue
        entries = _images_of(obj)
        if not entries:
            continue
        uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        mesh.uv_layers.active.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        bounds = (float(uvs[:, 0].min()), float(uvs[:, 1].min()),
                  float(uvs[:, 0].max()), float(uvs[:, 1].max()))
        for material, node in entries:
            record = usage.setdefault(node.image.name, {"image": node.image, "nodes": [],
                                                        "objects": [], "bounds": bounds})
            record["nodes"].append(node)
            if obj not in record["objects"]:
                record["objects"].append(obj)
            b = record["bounds"]
            record["bounds"] = (min(b[0], bounds[0]), min(b[1], bounds[1]),
                                max(b[2], bounds[2]), max(b[3], bounds[3]))

    report = []
    for name, record in usage.items():
        image = record["image"]
        width, height = image.size
        if width < 8 or height < 8:
            continue
        u0, v0, u1, v1 = record["bounds"]
        u0, v0 = max(0.0, u0), max(0.0, v0)
        u1, v1 = min(1.0, u1), min(1.0, v1)
        if u1 <= u0 or v1 <= v0:
            continue

        x0, x1 = int(u0 * width), min(width, int(math.ceil(u1 * width)) + 1)
        y0, y1 = int(v0 * height), min(height, int(math.ceil(v1 * height)) + 1)
        new_width, new_height = x1 - x0, y1 - y0
        gain = 1.0 - (new_width * new_height) / float(width * height)
        if new_width < 8 or new_height < 8 or gain < 0.05:
            continue

        buffer = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        cropped = buffer.reshape(height, width, 4)[y0:y1, x0:x1].copy()

        new_image = bpy.data.images.new("%s_Korridor" % name, new_width, new_height,
                                        alpha=True)
        new_image.colorspace_settings.name = image.colorspace_settings.name
        new_image.pixels.foreach_set(cropped.reshape(-1))
        new_image.pack()

        # UV-Koordinaten auf den neuen Ausschnitt umrechnen
        nu0, nv0 = x0 / width, y0 / height
        nu1, nv1 = x1 / width, y1 / height
        for obj in record["objects"]:
            layer = obj.data.uv_layers.active
            uvs = np.empty(len(obj.data.loops) * 2, dtype=np.float32)
            layer.data.foreach_get("uv", uvs)
            uvs = uvs.reshape(-1, 2)
            uvs[:, 0] = (uvs[:, 0] - nu0) / (nu1 - nu0)
            uvs[:, 1] = (uvs[:, 1] - nv0) / (nv1 - nv0)
            layer.data.foreach_set("uv", uvs.reshape(-1))
        for node in record["nodes"]:
            node.image = new_image

        if max_texture and max(new_width, new_height) > max_texture:
            factor = max_texture / float(max(new_width, new_height))
            new_image.scale(max(1, int(new_width * factor)), max(1, int(new_height * factor)))

        report.append("%s: %dx%d -> %dx%d (%.0f %% kleiner)" %
                      (name, width, height, new_image.size[0], new_image.size[1], gain * 100.0))
    return report


# =====================================================================
#  Kartentextur, die dem Streckenkorridor folgt
# =====================================================================

def _inverse_projection_numpy(np, projection, x, y):
    """Vektorisierte Umkehrung der Transverse-Mercator-Projektion."""
    radius = projection.radius * projection.k
    xs = x / radius
    ys = y / radius
    d = ys + projection.lat_rad
    lon = np.arctan2(np.sinh(xs), np.cos(d))
    lat = np.arcsin(np.clip(np.sin(d) / np.cosh(xs), -1.0, 1.0))
    return np.degrees(lat), projection.lon + np.degrees(lon)


def _load_tile_array(np, path, cache):
    """Kachel als uint8-Array (oben links zuerst)."""
    if path in cache:
        return cache[path]
    image = bpy.data.images.load(path, check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        width, height = image.size
        buffer = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        array = (buffer.reshape(height, width, 4)[::-1] * 255.0).astype(np.uint8)
    finally:
        bpy.data.images.remove(image)
    cache[path] = array
    return array


def corridor_latlon_grid(axis_points, radius, projection, resolution, max_edge):
    """Gitter geografischer Koordinaten entlang des Korridors.

    Rückgabe: (lat, lon, Breite, Höhe, Länge, Schrittweite in Metern)
    """
    import numpy as np

    xs = np.array([p[0] for p in axis_points], dtype=np.float64)
    ys = np.array([p[1] for p in axis_points], dtype=np.float64)
    segments = np.hypot(np.diff(xs), np.diff(ys))
    stations = np.concatenate(([0.0], np.cumsum(segments)))
    length = float(stations[-1])

    width = min(int(max_edge), max(16, int(math.ceil(length / resolution))))
    step = length / width
    # Die Querauflösung folgt der gewünschten Auflösung, nicht der Längsauflösung:
    # bei langen Strecken begrenzt die Bildbreite sonst auch die Schärfe quer zur Achse.
    height = min(int(max_edge), max(8, int(round(2.0 * radius / resolution))))

    # Punkte auf der Achse zu jeder Bildspalte
    s = (np.arange(width) + 0.5) * step
    index = np.clip(np.searchsorted(stations, s) - 1, 0, len(segments) - 1)
    local = (s - stations[index]) / np.maximum(segments[index], 1e-9)
    ax = xs[index] + (xs[index + 1] - xs[index]) * local
    ay = ys[index] + (ys[index + 1] - ys[index]) * local
    tx = (xs[index + 1] - xs[index]) / np.maximum(segments[index], 1e-9)
    ty = (ys[index + 1] - ys[index]) / np.maximum(segments[index], 1e-9)

    offsets = (np.arange(height) + 0.5) * (2.0 * radius / height) - radius
    grid_x = ax[None, :] + (-ty)[None, :] * offsets[:, None]
    grid_y = ay[None, :] + (tx)[None, :] * offsets[:, None]

    lat, lon = _inverse_projection_numpy(np, projection, grid_x, grid_y)
    return lat, lon, width, height, length, step


def corridor_tile_keys(lat, lon, zoom, tile_size=256, step=4):
    """Kacheln, die das Korridorgitter berührt.

    Das Gitter wird ausgedünnt geprüft (``step``): Eine Kachel deckt am Boden ein
    Vielfaches der Bildauflösung ab, sodass jeder vierte Bildpunkt sicher jede
    berührte Kachel trifft. Die Ränder werden immer mitgenommen.
    """
    import numpy as np
    sub_lat = np.concatenate([lat[::step, ::step].ravel(), lat[0], lat[-1],
                              lat[:, 0], lat[:, -1]])
    sub_lon = np.concatenate([lon[::step, ::step].ravel(), lon[0], lon[-1],
                              lon[:, 0], lon[:, -1]])
    n = 2 ** zoom * tile_size
    px = (sub_lon + 180.0) / 360.0 * n
    py = (1.0 - np.arcsinh(np.tan(np.radians(sub_lat))) / np.pi) / 2.0 * n
    tx = np.floor(px / tile_size).astype(np.int64)
    ty = np.floor(py / tile_size).astype(np.int64)
    # eindimensional zusammenfassen - np.unique über Spaltenpaare ist um ein
    # Vielfaches langsamer
    combined = np.unique(tx * (1 << 24) + ty)
    return [(int(v >> 24), int(v & ((1 << 24) - 1))) for v in combined]


def corridor_texture(lat, lon, width, height, step, source_key, tiles, zoom, tile_size=256,
                     name="Korridorkarte", tile_url="", cache=None):
    """Baut das Texturbild aus den geladenen Kacheln. Rückgabe Bild."""
    import numpy as np
    from . import maptexture

    n = 2 ** zoom * tile_size
    px = (lon + 180.0) / 360.0 * n
    py = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * n

    tile_x = np.floor(px / tile_size).astype(np.int64)
    tile_y = np.floor(py / tile_size).astype(np.int64)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[..., 3] = 255
    if cache is None:
        cache = {}
    for (kx, ky), layers in tiles.items():
        mask = (tile_x == kx) & (tile_y == ky)
        if not mask.any():
            continue
        cols = np.clip((px[mask] - kx * tile_size).astype(np.int64), 0, tile_size - 1)
        rows = np.clip((py[mask] - ky * tile_size).astype(np.int64), 0, tile_size - 1)
        base = _load_tile_array(np, layers["base"], cache)
        scale = base.shape[0] / float(tile_size)
        patch = base[np.clip((rows * scale).astype(np.int64), 0, base.shape[0] - 1),
                     np.clip((cols * scale).astype(np.int64), 0, base.shape[1] - 1)]
        overlay_path = layers.get("overlay")
        if overlay_path:
            over = _load_tile_array(np, overlay_path, cache)
            oscale = over.shape[0] / float(tile_size)
            opatch = over[np.clip((rows * oscale).astype(np.int64), 0, over.shape[0] - 1),
                          np.clip((cols * oscale).astype(np.int64), 0, over.shape[1] - 1)]
            alpha = opatch[:, 3:4].astype(np.float32) / 255.0
            patch = (patch.astype(np.float32) * (1.0 - alpha)
                     + opatch.astype(np.float32) * alpha).astype(np.uint8)
            patch[:, 3] = 255
        out[mask] = patch

    existing = bpy.data.images.get(name)
    if existing:
        bpy.data.images.remove(existing)
    image = bpy.data.images.new(name, width, height, alpha=True)
    image.colorspace_settings.name = 'sRGB'
    # Zeile 0 des Puffers entspricht dem seitlichen Abstand -radius und damit v = 0 -
    # genau die Zeile, die Blender zuerst erwartet. Ein zusätzliches Spiegeln würde
    # die Textur quer zur Strecke verkehrt herum auflegen.
    image.pixels.foreach_set((out.astype(np.float32) / 255.0).reshape(-1))
    image.pack()
    image["quelle"] = "eigener Kachelserver" if tile_url else maptexture.credit(source_key)
    image["zoom"] = zoom
    image["aufloesung_m_px"] = round(step, 3)
    return image


def apply_corridor_uv(obj, coords, radius, length, uv_name="Korridor", station_offset=0.0):
    """Legt die UV-Koordinaten aus (Station, seitlicher Abstand) an.

    ``station_offset`` ist der Stationsbeginn des Abschnitts; die U-Koordinate
    läuft dann innerhalb des Abschnitts von 0 bis 1.
    """
    import numpy as np
    mesh = obj.data
    layer = mesh.uv_layers.get(uv_name) or mesh.uv_layers.new(name=uv_name)
    mesh.uv_layers.active = layer

    stations = np.array([c[0] for c in coords], dtype=np.float32) - station_offset
    offsets = np.array([c[1] for c in coords], dtype=np.float32)
    u = np.clip(stations / max(length, 1e-6), 0.0, 1.0)
    v = np.clip((offsets + radius) / (2.0 * radius), 0.0, 1.0)

    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    uvs = np.empty((len(mesh.loops), 2), dtype=np.float32)
    uvs[:, 0] = u[loop_verts]
    uvs[:, 1] = v[loop_verts]
    layer.data.foreach_set("uv", uvs.reshape(-1))
    mesh.update()
    return layer


def apply_map_material(obj, image, name="Korridorkarte"):
    """Weist dem Gelände ein Material mit der Kartentextur zu."""
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    texture = None
    for node in nodes:
        if node.type == 'TEX_IMAGE':
            texture = node
            break
    if texture is None:
        texture = nodes.new("ShaderNodeTexImage")
        texture.location = (-350, 200)
    texture.image = image
    texture.extension = 'EXTEND'
    if bsdf:
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 1.0
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return material


# =====================================================================
#  Gleise und Anbindung an das Gelände
# =====================================================================

def terrain_sampler(terrain_obj):
    """Liefert eine Funktion (x, y) -> Höhe auf dem Geländeobjekt."""
    from mathutils.bvhtree import BVHTree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(terrain_obj, depsgraph)
    matrix = terrain_obj.matrix_world
    inverse = matrix.inverted()
    top = max((matrix @ Vector(corner)).z for corner in terrain_obj.bound_box) + 1000.0
    direction = (inverse.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()

    def sample(x, y):
        origin = inverse @ Vector((x, y, top))
        location, _normal, _index, _distance = tree.ray_cast(origin, direction)
        if location is None:
            return None
        return (matrix @ location).z

    return sample


def add_shrinkwrap(obj, target, offset=0.0, name="Auf Gelände"):
    """Legt einen Shrinkwrap-Modifier auf das Objekt (nicht zerstörend).

    ``offset`` ist die gewünschte Höhe **über** dem Gelände. Der Modifier zählt
    seinen Abstand in Projektionsrichtung, also nach unten - deshalb wird das
    Vorzeichen gedreht.
    """
    modifier = obj.modifiers.get(name)
    if modifier is None:
        modifier = obj.modifiers.new(name=name, type='SHRINKWRAP')
    modifier.target = target
    modifier.wrap_method = 'PROJECT'
    modifier.use_project_z = True
    modifier.use_negative_direction = True
    modifier.use_positive_direction = True
    modifier.offset = -offset
    return modifier


def remove_shrinkwrap(obj, name="Auf Gelände"):
    modifier = obj.modifiers.get(name)
    if modifier is not None:
        obj.modifiers.remove(modifier)
        return True
    return False


def _material(name, color, roughness=0.6, metallic=0.0):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
    material.diffuse_color = color
    bsdf = material.node_tree.nodes.get("Principled BSDF") if material.use_nodes else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = metallic
    return material


def _mesh_object(name, verts, faces, collection, material=None):
    if not verts or not faces:
        return None
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(unique_name(name), mesh)
    obj[MARKER] = True
    if material:
        mesh.materials.append(material)
    collection.objects.link(obj)
    return obj


def switch_collection(parent):
    """Eigene untergeordnete Sammlung; fremde gleichnamige Sammlungen bleiben frei."""
    for child in parent.children:
        if child.get("railway_switch_collection"):
            return child
    child = bpy.data.collections.new("Weichen")
    child["railway_switch_collection"] = True
    parent.children.link(child)
    return child


def organize_track_objects(context):
    """Bestehende Plugin-Gleisachsen je Import vereinigen, Weichen einsortieren.

    Blender Join erhält Splines und Weltpositionen. Nur vom Plugin markierte
    Gleisobjekte werden bearbeitet, keine Kilometrierung oder fremden Objekte.
    """
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    top = bpy.data.collections.get(TOP_COLLECTION)
    if top is None:
        return {"gleisobjekte": 0, "weichen": 0}
    result = {"gleisobjekte": 0, "weichen": 0}
    previous = context.view_layer.objects.active
    selected = list(context.selected_objects)
    for coll in list(top.children):
        tracks = [o for o in coll.objects if o.get(MARKER) and o.get("track_number")
                  and o.type == 'CURVE' and not o.get("railway_switch")]
        switches = [o for o in coll.objects if o.get(MARKER) and o.get("railway_switch")]
        if tracks:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in tracks:
                obj.select_set(True)
            active = tracks[0]
            context.view_layer.objects.active = active
            if len(tracks) > 1:
                with context.temp_override(active_object=active, object=active,
                                           selected_objects=tracks, selected_editable_objects=tracks):
                    bpy.ops.object.join()
            active.name = coll.name
            active["track_sections"] = len(active.data.splines)
            result["gleisobjekte"] += 1
        if switches:
            child = switch_collection(coll)
            for obj in switches:
                if obj.name not in child.objects:
                    child.objects.link(obj)
                coll.objects.unlink(obj)
        for child in coll.children:
            if child.get("railway_switch_collection"):
                result["weichen"] += sum(bool(o.get("railway_switch")) for o in child.objects)
    for obj in context.selected_objects:
        obj.select_set(False)
    # References to joined source objects may have been invalidated.
    for obj in selected:
        try:
            obj.select_set(True)
        except ReferenceError:
            pass
    try:
        if previous is not None:
            context.view_layer.objects.active = previous
    except ReferenceError:
        pass
    return result


def build_track_splines(context, props, label, tracks, sampler=None, terrain=None, switches=()):
    """Legt die Gleisachsen als Kurven an - ohne Schienen- oder Oberbaugeometrie.

    ``sampler`` setzt die Höhen fest aus dem Gelände, ``terrain`` hängt
    stattdessen einen Shrinkwrap-Modifier an.
    Rückgabe: Statistik als dict.
    """
    top = ensure_collection(TOP_COLLECTION, context.scene.collection)
    coll = ensure_collection("Gleise %s" % label.replace("/", "-"), top)
    clear_generated(coll)
    switch_coll = switch_collection(coll)
    clear_generated(switch_coll)

    material = _material("Gleisachse", (0.25, 0.45, 0.85, 1.0), 0.6)
    created, total_length, points_total = [], 0.0, 0
    # Gemeinsamer Ursprung verhindert unterschiedlich gerundete Anschlusskoordinaten.
    origin = tracks[0][0] if tracks else (switches[0]["points"][0] if switches else (0, 0))
    height_cache = {}
    def height(x, y):
        key = (x, y)
        if key not in height_cache:
            value = sampler(x, y) if sampler else None
            height_cache[key] = props.z_offset if value is None else value + props.z_offset
        return height_cache[key]
    valid_tracks = [points for points in tracks if len(points) >= 2]
    from .tracknetwork import track_name
    import json
    separate_tracks = getattr(props, 'rails_object_mode', 'JOINED') == 'SEPARATE'
    groups = [[points] for points in valid_tracks] if separate_tracks else ([valid_tracks] if valid_tracks else [])
    for number, group in enumerate(groups, start=1):
        name = track_name(group[0]) if separate_tracks else "Gleise %s" % label
        if separate_tracks and bpy.data.objects.get(name):
            ids = getattr(group[0], 'way_ids', ())
            name += " [OSM %s]" % (ids[0] if ids else number)
        curve = bpy.data.curves.new(name, 'CURVE')
        curve.dimensions = '3D'
        # Reine Gleisachsen, auch bei alten gespeicherten rails_width-Werten.
        curve.bevel_depth = 0.0
        curve.extrude = 0.0
        ox, oy = origin
        for points in group:
            spline = curve.splines.new('POLY')
            spline.points.add(len(points) - 1)
            for point, (x, y) in zip(spline.points, points):
                point.co = (x - ox, y - oy, height(x, y), 1.0)
            total_length += sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            points_total += len(points)
        curve.materials.append(material)
        obj = bpy.data.objects.new(name, curve)
        obj.location = (ox, oy, 0.0)
        obj[MARKER] = True
        obj["track_number"] = number  # Internal ordering, not an operational track number.
        obj["track_sections"] = len(group)
        obj["osm_track_ref"] = " / ".join(getattr(group[0], 'refs', ())) if separate_tracks else ""
        obj["osm_way_ids"] = json.dumps(sorted({wid for path in group for wid in getattr(path, 'way_ids', ())}))
        obj["track_metadata"] = json.dumps([{'spline': i, 'track_refs': list(getattr(path, 'refs', ())),
                                             'way_ids': list(getattr(path, 'way_ids', ()))}
                                            for i, path in enumerate(group)], ensure_ascii=False)
        coll.objects.link(obj)
        targets = terrain if isinstance(terrain, (list, tuple)) else ([terrain] if terrain else [])
        for count, target in enumerate(targets, start=1):
            modifier_name = "Auf Gelände" if len(targets) == 1 else "Auf Gelände %02d" % count
            add_shrinkwrap(obj, target, props.z_offset, name=modifier_name)
        created.append(obj)

    for switch in switches:
        from .tracknetwork import switch_name
        tags = switch.get("tags", {})
        kind, side = switch.get("kind", "Weiche"), switch.get("side", 0)
        name = switch_name(switch["node_id"], tags, kind, side)
        if bpy.data.objects.get(name):
            name += " [OSM %s]" % switch["node_id"]
        ox, oy = origin
        points = switch["points"]
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x-ox, y-oy, height(x,y)) for x,y in points], [(0,1),(0,2)], [])
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        obj.location = (ox, oy, 0)
        obj[MARKER] = True
        obj["track_number"] = len(tracks) + len(created) + 1
        obj["railway_switch"] = True
        obj["osm_node_id"] = str(switch["node_id"])
        obj["osm_ref"] = str(tags.get("ref") or tags.get("railway:ref") or "")
        obj["osm_name"] = str(tags.get("name") or "")
        obj["osm_url"] = "https://www.openstreetmap.org/node/%s" % switch["node_id"]
        obj["switch_geometry"] = "V: 3 vertices, 2 edges; schematic OSM geometry"
        obj["junction_kind"] = kind
        if side:
            obj["crossing_side"] = side
            obj["switch_geometry"] += "; half of a crossing (two V back to back)"
        apex, leg_a, leg_b = points
        va = (leg_a[0] - apex[0], leg_a[1] - apex[1])
        vb = (leg_b[0] - apex[0], leg_b[1] - apex[1])
        obj["switch_opening_deg"] = round(abs(math.degrees(math.atan2(
            va[0] * vb[1] - va[1] * vb[0], va[0] * vb[0] + va[1] * vb[1]))), 2)
        mesh.materials.append(material)
        switch_coll.objects.link(obj)
        targets = terrain if isinstance(terrain, (list, tuple)) else ([terrain] if terrain else [])
        for count, target in enumerate(targets, start=1):
            modifier_name = "Auf Gelände" if len(targets) == 1 else "Auf Gelände %02d" % count
            add_shrinkwrap(obj, target, props.z_offset, name=modifier_name)
        created.append(obj)
        total_length += sum(math.dist(points[0], end) for end in points[1:])
        points_total += 3

    return {"objekte": [o.name for o in created], "gleise": len(valid_tracks), "gleisobjekte": len(groups),
            "weichen": len(switches), "laenge_km": total_length / 1000.0, "punkte": points_total}


def area_latlon_grid(utm_bounds, zone, resolution, max_edge):
    """Gitter geografischer Koordinaten über ein UTM-Rechteck (für die Flächentextur)."""
    import numpy as np
    from . import dgm as dgm_mod

    east0, north0, east1, north1 = utm_bounds
    width = min(int(max_edge), max(16, int(math.ceil((east1 - east0) / resolution))))
    height = min(int(max_edge), max(16, int(math.ceil((north1 - north0) / resolution))))
    step = (east1 - east0) / width

    east = east0 + (np.arange(width) + 0.5) * (east1 - east0) / width
    north = north0 + (np.arange(height) + 0.5) * (north1 - north0) / height
    grid_e, grid_n = np.meshgrid(east, north)

    # Umkehrung der UTM-Abbildung, vektorisiert (Krüger-Reihen)
    a, f = 6378137.0, 1.0 / 298.257222101
    n_par = f / (2.0 - f)
    k0, e0 = 0.9996, 500000.0
    lon0 = math.radians(zone * 6 - 183)
    big_a = a / (1.0 + n_par) * (1.0 + n_par ** 2 / 4.0 + n_par ** 4 / 64.0)
    beta = (n_par / 2.0 - 2.0 * n_par ** 2 / 3.0 + 37.0 * n_par ** 3 / 96.0,
            n_par ** 2 / 48.0 + n_par ** 3 / 15.0,
            17.0 * n_par ** 3 / 480.0)
    delta = (2.0 * n_par - 2.0 * n_par ** 2 / 3.0 - 2.0 * n_par ** 3,
             7.0 * n_par ** 2 / 3.0 - 8.0 * n_par ** 3 / 5.0,
             56.0 * n_par ** 3 / 15.0)
    xi = grid_n / (k0 * big_a)
    eta = (grid_e - e0) / (k0 * big_a)
    xi_ = xi - sum(beta[j] * np.sin(2 * (j + 1) * xi) * np.cosh(2 * (j + 1) * eta)
                   for j in range(3))
    eta_ = eta - sum(beta[j] * np.cos(2 * (j + 1) * xi) * np.sinh(2 * (j + 1) * eta)
                     for j in range(3))
    chi = np.arcsin(np.clip(np.sin(xi_) / np.cosh(eta_), -1.0, 1.0))
    lat = chi + sum(delta[j] * np.sin(2 * (j + 1) * chi) for j in range(3))
    lon = lon0 + np.arctan(np.sinh(eta_) / np.cos(xi_))
    return np.degrees(lat), np.degrees(lon), width, height, step


def create_area_terrain(context, name, verts, faces, uvs, collection, height_offset=0.0):
    """Legt ein flächiges Geländemodell mit planaren UV-Koordinaten an."""
    import numpy as np
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, y, z - height_offset) for x, y, z in verts], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(unique_name(name), mesh)
    obj[MARKER] = True
    obj["height_offset"] = height_offset
    collection.objects.link(obj)
    try:
        mesh.shade_smooth()
    except AttributeError:
        pass

    layer = mesh.uv_layers.new(name="Gebiet")
    mesh.uv_layers.active = layer
    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    uv_array = np.asarray(uvs, dtype=np.float32)
    layer.data.foreach_set("uv", uv_array[loop_verts].reshape(-1))
    mesh.update()
    return obj


# =====================================================================
#  Gebäude
# =====================================================================

def create_buildings(context, name, gebaeude, collection, samplers=(), skirt=1.0,
                     base_height=0.0, material_name="Gebäude"):
    """Baut die Grundrisse als einen Körper je Gebäude in einem Mesh auf.

    Alle Gebäude landen in einem Objekt - bei mehreren tausend Häusern ist das
    um Größenordnungen schneller als einzelne Objekte und lässt sich in Blender
    trotzdem jederzeit trennen (``P`` > *Nach losen Teilen*).

    ``samplers`` sind Funktionen (x, y) -> Höhe, wie sie
    :func:`terrain_sampler` liefert. Getroffen wird der erste Abschnitt, der
    einen Wert liefert; ohne Treffer gilt ``base_height``. Damit ein Haus am
    Hang nicht in der Luft steht, reicht die Grundfläche um ``skirt`` unter den
    tiefsten und das Dach bis über den höchsten gemessenen Punkt.
    """
    if not gebaeude:
        return None, 0

    def hoehe_bei(x, y):
        for sampler in samplers:
            wert = sampler(x, y)
            if wert is not None:
                return wert
        return None

    verts, faces = [], []
    ohne_gelaende = 0
    for haus in gebaeude:
        punkte = haus.points
        proben = [hoehe_bei(x, y) for x, y in punkte] if samplers else []
        proben = [p for p in proben if p is not None]
        if proben:
            unten, oben = min(proben), max(proben)
        else:
            unten = oben = base_height
            if samplers:
                ohne_gelaende += 1

        sockel = unten + haus.min_height if haus.min_height else unten - skirt
        dach = oben + haus.height

        anzahl = len(punkte)
        start = len(verts)
        verts.extend((x, y, sockel) for x, y in punkte)
        verts.extend((x, y, dach) for x, y in punkte)
        for i in range(anzahl):
            j = (i + 1) % anzahl
            faces.append((start + i, start + j, start + anzahl + j, start + anzahl + i))
        faces.append(tuple(range(start + anzahl, start + 2 * anzahl)))
        faces.append(tuple(reversed(range(start, start + anzahl))))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(unique_name(name), mesh)
    obj[MARKER] = True
    obj["building_count"] = len(gebaeude)
    collection.objects.link(obj)

    material = _material(material_name, (0.62, 0.60, 0.57, 1.0), roughness=0.85)
    mesh.materials.append(material)
    return obj, ohne_gelaende


def local_latlon_grid(local_bounds, projection, resolution, max_edge):
    """Gitter geografischer Koordinaten über ein achsparalleles Szenenrechteck.

    Gegenstück zu :func:`area_latlon_grid`, aber für ein Gelände, das entlang der
    Szenenachsen aufgebaut ist. Die Umkehrung der Transverse-Mercator-Abbildung
    ist dieselbe wie in :meth:`projection.TransverseMercator.to_geographic`, nur
    vektorisiert.
    """
    import numpy as np

    x0, y0, x1, y1 = local_bounds
    width = min(int(max_edge), max(16, int(math.ceil((x1 - x0) / resolution))))
    height = min(int(max_edge), max(16, int(math.ceil((y1 - y0) / resolution))))
    step = (x1 - x0) / width

    xs = x0 + (np.arange(width) + 0.5) * (x1 - x0) / width
    ys = y0 + (np.arange(height) + 0.5) * (y1 - y0) / height
    grid_x, grid_y = np.meshgrid(xs, ys)

    massstab = projection.k * projection.radius
    grid_x = grid_x / massstab
    grid_y = grid_y / massstab
    d = grid_y + projection.lat_rad
    lon = np.degrees(np.arctan(np.sinh(grid_x) / np.cos(d))) + projection.lon
    lat = np.degrees(np.arcsin(np.clip(np.sin(d) / np.cosh(grid_x), -1.0, 1.0)))
    return lat, lon, width, height, step

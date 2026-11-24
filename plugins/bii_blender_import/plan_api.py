"""Plans for scripts and AI agents: coordinates, crop and a placement check.

Units of a point on a plan (u, v):
- 'SHEET': meters on the paper, from the lower left corner, y up (the plan mesh's own coordinates).
- 'PDF': PDF points (1/72 inch) of the page, from the upper left corner, y down.
- 'IMAGE': pixels of the plan's image in Blender (pg.image), from the upper left corner, y down.
- 'SOURCE': pixels of the original raster file (TIFF, PNG, ...), from the upper left corner, y down.

check_placement() compares a flat plan with the plans it overlaps and with the BII Umgebung track
axes: it moves the plan's ink over theirs and reports how far it should move to match best.
"""

import math
import os

import bpy
import numpy as np
from mathutils import Vector

from . import import_plan, plan_place, plan_raster

UNITS = ('SHEET', 'PDF', 'IMAGE', 'SOURCE')
INCH = 0.0254
INK_LEVEL = 160          # gray below this is ink
MAX_CELLS = 16_000_000   # grid size limit of the placement check
MIN_SCORE = 0.2          # a match scoring less is not trusted (see compare)


def plans(scene=None):
    """Plan objects of the scene (all scenes' objects without one)."""
    objs = scene.objects if scene else bpy.data.objects
    return [o for o in objs if import_plan.is_plan(o)]


# ---------------------------------------------------------------- coordinates

def _source_pixels(pg):
    path = import_plan.absolute_path(pg.source)
    if os.path.splitext(path)[1].lower() in plan_raster.PDF_EXTENSIONS:
        raise ValueError("SOURCE pixels are for raster files; this plan comes from a PDF: use 'PDF'")
    if not os.path.isfile(path):
        raise ValueError(f"The original file is missing: {path}")
    return plan_raster.pixel_size(path, long_side=math.inf)


def to_sheet(obj, u, v, unit='SHEET'):
    """(x, y) in sheet meters of a point given in unit (see the module docstring)."""
    pg = obj.bii_plan
    if unit == 'SHEET':
        return float(u), float(v)
    if unit == 'PDF':
        return u / 72 * INCH, pg.height - v / 72 * INCH
    if unit == 'IMAGE':
        if pg.image is None or not pg.image.size[0]:
            raise ValueError(f"{obj.name} has no image (relink it)")
        w, h = pg.image.size
    elif unit == 'SOURCE':
        w, h = _source_pixels(pg)
        if pg.rotation % 360:
            u, v = plan_raster.turn_point(u, v, w, h, pg.rotation)
            if pg.rotation % 180:
                w, h = h, w
    else:
        raise ValueError(f"unknown unit {unit!r}; one of {UNITS}")
    return u / w * pg.width, pg.height - v / h * pg.height


def from_sheet(obj, x, y, unit='SHEET'):
    """(u, v) in unit of a point given in sheet meters."""
    pg = obj.bii_plan
    if unit == 'SHEET':
        return float(x), float(y)
    if unit == 'PDF':
        return x / INCH * 72, (pg.height - y) / INCH * 72
    if unit == 'IMAGE':
        w, h = pg.image.size
    elif unit == 'SOURCE':
        w0, h0 = _source_pixels(pg)
        w, h = (h0, w0) if pg.rotation % 180 else (w0, h0)
        return plan_raster.unturn_point(x / pg.width * w, (pg.height - y) / pg.height * h,
                                        w0, h0, pg.rotation)
    else:
        raise ValueError(f"unknown unit {unit!r}; one of {UNITS}")
    return x / pg.width * w, (pg.height - y) / pg.height * h


def plan_to_world(obj, u, v, unit='SHEET'):
    """World position (Vector) of a point on the plan."""
    x, y = to_sheet(obj, u, v, unit)
    return obj.matrix_world @ Vector((x, y, 0.0))


def world_to_plan(obj, point, unit='SHEET'):
    """(u, v) on the plan of a world point; for a flat plan the height does not matter."""
    m = obj.matrix_world
    p = Vector(point)
    if obj.bii_plan.orientation == 'FLAT' and len(p) == 3:
        p.z = (m @ Vector((0.0, 0.0, 0.0))).z
    local = m.inverted() @ p.to_3d()
    return from_sheet(obj, local.x, local.y, unit)


def crop(obj, u0, v0, u1, v1, unit='SHEET'):
    """Crop the plan to the rectangle between two corners given in unit."""
    x0, y0 = to_sheet(obj, u0, v0, unit)
    x1, y1 = to_sheet(obj, u1, v1, unit)
    import_plan.set_sheet_rectangle(obj, x0, y0, x1, y1)


def crop_rectangle(obj):
    """(x0, y0, x1, y1) in sheet meters: the bounding box of the plan's outline (its crop)."""
    co = [v.co for v in obj.data.vertices]
    if not co:
        pg = obj.bii_plan
        return 0.0, 0.0, pg.width, pg.height
    return (min(c.x for c in co), min(c.y for c in co), max(c.x for c in co), max(c.y for c in co))


# ---------------------------------------------------------------- placing

def _run(obj, op, **kwargs):
    """Run a plan operator on obj alone (active and only selected object); returns its result."""
    context = bpy.context
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    return op(**kwargs)


def placement(obj):
    """How the plan was placed last (dict: method, details, time), or None. Written by the
    placement calls of this module."""
    import json
    text = obj.get("bii_placement")
    return json.loads(text) if text else None


def _record(obj, method, **details):
    import json
    import time
    obj["bii_placement"] = json.dumps({"method": method, "time": time.strftime("%Y-%m-%d %H:%M"),
                                       **details}, default=str)


def _guard(obj, force):
    """A plan placed by its grid is the most accurate there is: other placement calls leave it
    alone unless force=True."""
    rec = placement(obj)
    if rec and rec.get("method") == "grid" and not force:
        raise ValueError(f"{obj.name} is placed by its grid crosses; it is not moved by other "
                         "methods (pass force=True if you really mean to)")


def georeference(obj, crosses, unit='SHEET', crs=None, utm_zone=None, replace=True):
    """Place the plan by grid crosses: crosses = [(u, v, easting, northing)], the position of
    each cross on the plan in unit and the coordinates printed at it (Rechtswert, Hochwert).
    crs / utm_zone: as in the panel (Coordinates), else kept. Needs at least 2 crosses, better 4
    near the corners. Returns (Maßstab measured, RMS residual m, largest residual m, warnings)."""
    from . import plan_georef
    pg = obj.bii_plan
    if crs:
        pg.crs = crs
    if utm_zone:
        pg.utm_zone = utm_zone
    if replace:
        pg.points.clear()
    for n, (u, v, e, north) in enumerate(crosses, 1):
        x, y = to_sheet(obj, u, v, unit)
        p = pg.points.add()
        p.name = f"G{len(pg.points)}"
        p.x, p.y, p.use = x, y, True
        import_plan.set_point_value(p, 'easting', int(round(e)), 'READ')
        import_plan.set_point_value(p, 'northing', int(round(north)), 'READ')
    if _run(obj, bpy.ops.bii_plan.georeference) != {'FINISHED'}:
        raise ValueError(f"{obj.name}: Place in Scene failed (see the report)")
    bpy.context.view_layer.update()
    scale, rms, worst, warnings = plan_georef.fit_summary(pg)
    _record(obj, "grid", crosses=len(crosses), scale=round(scale, 2), rms=round(rms, 3),
            worst=round(worst.residual, 3))
    return scale, rms, worst.residual, warnings


def align_to_scene(obj, pairs, unit='SHEET', fit_scale=False, replace=True, force=False):
    """Place the plan so points on it meet scene points: pairs = [(u, v, (x, y, z))], a point on the
    plan in unit and where it belongs in the scene (e.g. the same corner on a plan already placed:
    plan_to_world(other, ...)). One pair only moves the plan, two or more also turn it; fit_scale
    measures the Maßstab too. Returns the residuals in meters, one per pair. A plan placed by its
    grid is not moved unless force=True."""
    _guard(obj, force)
    pg = obj.bii_plan
    if replace:
        pg.pairs.clear()
    for u, v, target in pairs:
        x, y = to_sheet(obj, u, v, unit)
        p = pg.pairs.add()
        p.name = f"A{len(pg.pairs)}"
        p.x, p.y = x, y
        p.target = Vector(target).to_3d()
    pg.fit_scale = fit_scale
    if _run(obj, bpy.ops.bii_plan.align) != {'FINISHED'}:
        raise ValueError(f"{obj.name}: Align failed (see the report)")
    bpy.context.view_layer.update()
    _record(obj, "align_to_scene", pairs=len(pairs))
    return [p.residual for p in pg.pairs]


def attach_to_neighbor(obj, neighbor, own, other, unit='SHEET', force=False):
    """Join a sheet to its neighbor (Anschlussblatt) at the match line: own = two points on this
    sheet, other = the same two points on the neighbor, each (u, v) in unit. The first is the point
    where the track crosses the match line. Sheets attached to obj move along. Returns warnings."""
    from . import plan_place
    _guard(obj, force)
    place = obj.bii_plan_place
    pts_own = [to_sheet(obj, u, v, unit) for u, v in own]
    pts_other = [to_sheet(neighbor, u, v, unit) for u, v in other]
    place.attach_parent = neighbor
    place.attach_own = (*pts_own[0], *pts_own[1])
    place.attach_other = (*pts_other[0], *pts_other[1])
    place.placed_by_km = False
    warnings = plan_place.attach(obj)
    bpy.context.view_layer.update()
    warnings += plan_place.place_chain(bpy.context.scene, obj)[2]
    _record(obj, "neighbor", neighbor=neighbor.name)
    return warnings


def align_by_km(obj, km_points, strecke=None, unit='SHEET', replace=True, force=False):
    """Place the plan by Kilometrierung: km_points = [(u, v, "5,3")], a point on the plan track and
    the km printed there ("" for a point that only lies on the track). Only for plans without a
    coordinate grid: the OSM Kilometrierung can be 20 m and more off along the track. Returns the
    report lines. A plan placed by its grid is not moved unless force=True. Each point must be the
    km mark on the track, not the label text beside it."""
    _guard(obj, force)
    place = obj.bii_plan_place
    if strecke is not None:
        place.strecke = strecke
    if replace:
        place.km_points.clear()
    for u, v, km in km_points:
        x, y = to_sheet(obj, u, v, unit)
        p = place.km_points.add()
        p.name = f"K{len(place.km_points)}"
        p.x, p.y = x, y
        p.km_text = km
    if _run(obj, bpy.ops.bii_plan.align_km) != {'FINISHED'}:
        raise ValueError(f"{obj.name}: Align by Kilometrierung failed: {place.report}")
    bpy.context.view_layer.update()
    return place.report.splitlines()


# ---------------------------------------------------------------- pixels

def gray(obj):
    """Gray pixels (uint8, row 0 at the top) of the plan's image."""
    image = obj.bii_plan.image
    if image is None:
        raise ValueError(f"{obj.name} has no image")
    if import_plan.texture_missing(image):
        raise ValueError(f"The image file of {obj.name} is missing: Relink it")
    if image.packed_file:
        return plan_raster.gray_pixels(bytes(image.packed_file.data))
    return plan_raster.gray_pixels(bpy.path.abspath(image.filepath))


def _block_min(a, k):
    """a reduced by k x k blocks, keeping the darkest pixel of each."""
    if k <= 1:
        return a
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:h, :w].reshape(h // k, k, w // k, k).min(axis=(1, 3))


def _rgb(obj):
    """RGB pixels (uint8, row 0 at the top) of the plan's image."""
    import io
    from PIL import Image
    image = obj.bii_plan.image
    if image is None or import_plan.texture_missing(image):
        raise ValueError(f"The image of {obj.name} is missing: Relink it")
    src = io.BytesIO(bytes(image.packed_file.data)) if image.packed_file else bpy.path.abspath(image.filepath)
    with plan_raster._large_images(), Image.open(src) as im:
        return np.asarray(im.convert("RGB"))


COLORS = {"red": lambda r, g, b: (r > 150) & (g < 120) & (b < 120) & (r - g > 60),
          "blue": lambda r, g, b: (b > 150) & (r < 120) & (b - r > 60),
          "green": lambda r, g, b: (g > 120) & (r < 120) & (b < 120) & (g - r > 40)}


def color_clusters(obj, color="red", join=0.002, min_size=0.0005):
    """Groups of colored ink on the plan (e.g. the red, new signals of a Signallageplan):
    [{"box": (u0, v0, u1, v1) IMAGE pixels, "center": world Vector, "pixels": count}], largest first.
    join: ink closer than this (sheet meters, 2 mm) belongs to one group; min_size: smaller groups
    (sheet meters) are left out. A group can hold a symbol with its label, or touching symbols:
    look at each one."""
    pg = obj.bii_plan
    rgb = _rgb(obj)
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    mask = COLORS[color](r, g, b)
    h, w = mask.shape
    k = max(1, int(join / (pg.width / w)))
    hh, ww = -(-h // k), -(-w // k)
    padded = np.zeros((hh * k, ww * k), bool)
    padded[:h, :w] = mask
    coarse = padded.reshape(hh, k, ww, k).any(axis=(1, 3))
    labels = np.zeros(coarse.shape, np.int32)
    groups = []
    for start in zip(*np.nonzero(coarse)):
        if labels[start]:
            continue
        n = len(groups) + 1
        labels[start] = n
        todo, cells = [start], []
        while todo:
            y, x = todo.pop()
            cells.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < hh and 0 <= xx < ww and coarse[yy, xx] and not labels[yy, xx]:
                        labels[yy, xx] = n
                        todo.append((yy, xx))
        ys = [c[0] for c in cells]
        xs = [c[1] for c in cells]
        box = (int(min(xs) * k), int(min(ys) * k), int(min(w, (max(xs) + 1) * k)), int(min(h, (max(ys) + 1) * k)))
        groups.append(box)
    out = []
    px = pg.width / w
    for u0, v0, u1, v1 in groups:
        if max(u1 - u0, v1 - v0) * px < min_size:
            continue
        count = int(mask[v0:v1, u0:u1].sum())
        out.append({"box": (u0, v0, u1, v1), "pixels": count,
                    "center": plan_to_world(obj, (u0 + u1) / 2, (v0 + v1) / 2, 'IMAGE')})
    out.sort(key=lambda c: -c["pixels"])
    return out


def crop_image(obj, u0, v0, u1, v1, path, unit='IMAGE', pad=0, scale=1.0, up=None):
    """Write the part of the plan's image between two corners (in unit, plus pad image pixels) as
    PNG, e.g. one symbol for a closer look; scale > 1 enlarges it. Returns path.
    up: a direction (du, dv) in the plan's image (y down), e.g. from a symbol's foot to its head:
    the crop is turned so that this direction points up, and numbers printed along the symbol read
    upright (a 6 and a 9 are told apart only that way)."""
    from PIL import Image
    a = from_sheet(obj, *to_sheet(obj, u0, v0, unit), 'IMAGE')
    b = from_sheet(obj, *to_sheet(obj, u1, v1, unit), 'IMAGE')
    rgb = _rgb(obj)
    h, w = rgb.shape[:2]
    x0, x1 = sorted((int(a[0]), int(math.ceil(b[0]))))
    y0, y1 = sorted((int(a[1]), int(math.ceil(b[1]))))
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
    im = Image.fromarray(rgb[y0:y1, x0:x1])
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    if up is not None and (up[0] or up[1]):
        # angle of up, clockwise from "up" on the screen; turning the image back by it puts it up
        angle = math.degrees(math.atan2(up[0], -up[1]))
        im = im.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    im.save(path)
    return path


# ---------------------------------------------------------------- grid crosses

def _full_gray(obj):
    """(gray pixels, dpi) of the plan's original at full resolution (PDF pages at 400 dpi)."""
    pg = obj.bii_plan
    path = import_plan.absolute_path(pg.source)
    if not os.path.isfile(path):
        raise ValueError(f"The original file is missing: {path}")
    if os.path.splitext(path)[1].lower() in plan_raster.PDF_EXTENSIONS:
        sheet = plan_raster.load_pdf(path, pg.page - 1, 400.0)
        gray = np.asarray(sheet.image.convert("L"))
    else:
        gray = plan_raster.gray_pixels(path)
        if pg.rotation % 360:
            gray = np.ascontiguousarray(np.rot90(gray, k=-(pg.rotation // 90)))
    dpi = gray.shape[1] / (pg.width / INCH)
    return gray, dpi


def grid(obj):
    """The grid found by find_grid() (dict, sheet meters), or None."""
    import json
    text = obj.get("bii_grid")
    return json.loads(text) if text else None


def _grid_to_px(obj, gray, stored):
    """The stored grid (sheet meters) in pixels of gray."""
    pg = obj.bii_plan
    h, w = gray.shape

    def px(x, y):
        return (x / pg.width * w, (pg.height - y) / pg.height * h)
    return {"origin": px(*stored["origin"]),
            "a": (stored["a"][0] / pg.width * w, -stored["a"][1] / pg.height * h),
            "b": (stored["b"][0] / pg.width * w, -stored["b"][1] / pg.height * h),
            "crosses": [dict(c, x=px(c["x"], c["y"])[0], y=px(c["x"], c["y"])[1]) for c in stored["crosses"]],
            "spacing_m": stored["spacing_m"]}


def find_grid(obj, add_points=True):
    """Find the plan's coordinate grid (Gitterkreuze) on the original file at full resolution.

    Stores it on the plan (grid()) and, with add_points, makes every cross found a georeference
    point named "G i,j" without values (i, j: its place in the grid). Then either enter Rechtswert
    and Hochwert of 2 or 3 crosses and call fill_from_grid(), or read the numbers printed on the
    plan (grid_labels()) and call place_from_grid(). Returns {"crosses", "spacing_m", "angle",
    "rms_px"}, or None when no grid was found."""
    import json
    from . import plan_grid
    pg = obj.bii_plan
    gray, dpi = _full_gray(obj)
    found = plan_grid.find_grid(gray, dpi, massstab=pg.massstab or None)
    if not found:
        if "bii_grid" in obj:
            del obj["bii_grid"]
        return None
    h, w = gray.shape

    def sheet(x, y):
        return (x / w * pg.width, pg.height - y / h * pg.height)
    stored = {"origin": sheet(*found["origin"]),
              "a": (found["a"][0] / w * pg.width, -found["a"][1] / h * pg.height),
              "b": (found["b"][0] / w * pg.width, -found["b"][1] / h * pg.height),
              "spacing_m": found["spacing_m"], "angle": found["angle"], "rms_px": found["rms_px"],
              "dpi": dpi,
              "crosses": [{"i": c["i"], "j": c["j"], "x": sheet(c["x"], c["y"])[0],
                           "y": sheet(c["x"], c["y"])[1], "score": c["score"]} for c in found["crosses"]]}
    obj["bii_grid"] = json.dumps(stored)
    if found["spacing_m"]:
        pg.grid_spacing = int(found["spacing_m"])
    if add_points:
        pg.points.clear()
        for c in stored["crosses"]:
            p = pg.points.add()
            p.name = f"G {c['i']},{c['j']}"
            p.x, p.y, p.use = c["x"], c["y"], True
        pg.point_index = 0
    if bpy.context.window_manager:
        import_plan._redraw(None, bpy.context)
    return {"crosses": len(stored["crosses"]), "spacing_m": found["spacing_m"],
            "angle": round(found["angle"], 2), "rms_px": round(found["rms_px"], 2)}


GRID_LABEL_QUESTION = (
    "Numbered boxes from a scanned plan, separated by black lines. Each box shows the same piece "
    "twice: as printed (top) and turned 180 degrees (bottom). For each box: if one of the two shows "
    "an upright coordinate number (7 digits, like 3589300 or 5546100), give that number; else NONE. "
    "Reply only with lines 'n: number' or 'n: NONE'.")


def grid_labels(obj, folder, per_image=4):
    """Images to read the grid's coordinate numbers from (after find_grid()).

    Writes PNGs into folder. Each holds up to per_image numbered boxes: a place along a grid line
    where a number may be printed, shown as printed (top) and turned by 180 degrees (bottom).
    Returns [{"image": path, "boxes": [label id, ...]}]: box n of an image is boxes[n - 1].
    Ask GRID_LABEL_QUESTION about each image, then place_from_grid(obj, [(label id, number)])."""
    from PIL import Image, ImageDraw
    from . import plan_grid
    stored = grid(obj)
    if not stored:
        raise ValueError(f"{obj.name}: no grid yet, run find_grid() first")
    gray, dpi = _full_gray(obj)
    cands = plan_grid.label_candidates(gray, _grid_to_px(obj, gray, stored), dpi)
    cands.sort(key=lambda c: not c["near_cross"])
    obj["bii_grid_labels"] = [[c["family"], c["index"]] for c in cands]
    os.makedirs(folder, exist_ok=True)
    out = []
    for k0 in range(0, len(cands), per_image):
        group = cands[k0:k0 + per_image]
        img, tops = plan_grid.contact_sheet([c["strip"] for c in group])
        im = Image.fromarray(img).convert("RGB")
        draw = ImageDraw.Draw(im)
        for n, y in enumerate(tops, 1):
            draw.text((8, y + 8), str(n), fill=(255, 0, 0))
        path = os.path.join(folder, f"{bpy.path.clean_name(obj.name)}_labels_{k0 // per_image + 1}.png")
        im.save(path)
        out.append({"image": path, "boxes": list(range(k0, k0 + len(group)))})
    return out


def _expected(obj):
    """Rough (easting, northing) of the scene origin in the plan's system, or None."""
    from . import plan_geo
    scene = bpy.context.scene
    if "lat" not in scene or "lon" not in scene:
        return None
    pg = obj.bii_plan
    try:
        zone = pg.utm_zone if pg.crs == 'ETRS89' else plan_geo.default_zone(pg.crs, scene["lon"])
        return plan_geo.wgs84_to_grid(pg.crs, scene["lat"], scene["lon"], zone)
    except Exception:  # noqa: BLE001 - only a hint
        return None


def place_from_grid(obj, readings, crs=None):
    """Place the plan from its grid (find_grid()) and coordinate numbers read on it.

    readings: [(label id, number)] with the label ids of grid_labels(), or
    [(family, index, number)] for a grid line directly. Every number must fit the grid; misreads
    are left out and reported. Needs numbers on both grid directions and two on one of them (else
    the plan could be turned by 180 degrees). Returns (Maßstab, RMS m, largest residual m,
    warnings) like georeference(); raises ValueError with the reason when it cannot decide."""
    from . import plan_grid
    stored = grid(obj)
    if not stored:
        raise ValueError(f"{obj.name}: no grid yet, run find_grid() first")
    if crs:
        obj.bii_plan.crs = crs
    labels = obj.get("bii_grid_labels") or []
    rows = []
    for r in readings:
        if len(r) == 2:
            fam, idx = labels[int(r[0])]
            rows.append((fam, int(idx), float(r[1])))
        else:
            rows.append((r[0], int(r[1]), float(r[2])))
    sol = plan_grid.solve(stored, rows, stored["spacing_m"] or obj.bii_plan.grid_spacing or 100,
                          expected=_expected(obj), y_down=False)
    if not sol["ok"]:
        raise ValueError(f"{obj.name}: {sol['message']}")
    scale, rms, worst, warnings = georeference(obj, sol["crosses"], unit='SHEET')
    _record(obj, "grid", crosses=len(sol["crosses"]), scale=round(scale, 2), rms=round(rms, 3),
            numbers=[list(r) for r in sol["used"]], misread=[list(r) for r in sol["outliers"]])
    for p, c in zip(obj.bii_plan.points, stored["crosses"]):
        p.name = f"G {c['i']},{c['j']}"
    warnings = list(warnings)
    if sol["outliers"]:
        warnings.append(f"Left out as misread: {sol['outliers']}")
    return scale, rms, worst, warnings


def fill_from_grid(obj):
    """Fill every grid point of find_grid() from the values entered at 2 or more of them, so that
    Place in Scene uses all crosses. Returns the number of points filled; raises ValueError when
    the values do not fit the grid or leave it open."""
    from . import plan_grid
    stored = grid(obj)
    if not stored:
        raise ValueError("No grid yet: Find Grid first")
    pg = obj.bii_plan
    spacing = stored["spacing_m"] or pg.grid_spacing or 100
    index = {}
    for c in stored["crosses"]:
        index[f"G {c['i']},{c['j']}"] = (c["i"], c["j"])
    known = [(index[p.name], p.easting, p.northing) for p in pg.points
             if p.name in index and p.easting_state == 'READ' and p.northing_state == 'READ']
    if len(known) < 2:
        raise ValueError("Enter Rechtswert and Hochwert of at least 2 grid crosses")
    good = []
    for e_fam in ("i", "j"):
        rows = []
        for (i, j), e, n in known:
            if e_fam == "i":
                rows += [("i", i, e), ("j", j, n)]
            else:
                rows += [("j", j, e), ("i", i, n)]
        sol = plan_grid.solve(stored, rows, spacing, y_down=False)
        if sol["ok"] and not sol["outliers"]:
            good.append(sol)
    if len(good) != 1:
        raise ValueError("The values fit the grid in two ways: enter one more cross, not in line "
                         "with the others" if good else
                         "The values do not fit the grid: check for a typo")
    values = {}
    for (x, y, e, n), c in zip(good[0]["crosses"], stored["crosses"]):
        values[f"G {c['i']},{c['j']}"] = (e, n)
    filled = 0
    for p in pg.points:
        if p.name not in values:
            continue
        e, n = values[p.name]
        if p.easting_state != 'READ':
            import_plan.set_point_value(p, "easting", int(round(e)), 'READ')
            filled += 1
        if p.northing_state != 'READ':
            import_plan.set_point_value(p, "northing", int(round(n)), 'READ')
    return filled


# ---------------------------------------------------------------- Auto Crop

def auto_crop_rectangle(obj, margin=0.005):
    """(x0, y0, x1, y1) in sheet meters around the drawing: without the empty paper and the frame
    lines of the sheet, plus margin meters of paper. The title block (Schriftfeld) and legends
    inside the frame stay: crop them away by hand if needed."""
    pg = obj.bii_plan
    g = gray(obj)
    k = max(1, max(g.shape) // 2000)
    ink = _block_min(g, k) < INK_LEVEL
    h, w = ink.shape
    rows, cols = ink.mean(axis=1), ink.mean(axis=0)
    # frame lines: rows / columns mostly ink, within the outer fifth of the sheet
    top, bottom, left, right = 0, h, 0, w
    for i in np.nonzero(rows > 0.5)[0]:
        if i < h / 5:
            top = max(top, i + 1)
        elif i > h * 4 / 5:
            bottom = min(bottom, i)
    for j in np.nonzero(cols > 0.5)[0]:
        if j < w / 5:
            left = max(left, j + 1)
        elif j > w * 4 / 5:
            right = min(right, j)
    pad = 3  # cells next to a frame line, where its anti-aliased edge lies
    inner = np.zeros_like(ink)
    inner[top + pad:bottom - pad, left + pad:right - pad] = ink[top + pad:bottom - pad, left + pad:right - pad]
    r = np.nonzero(inner.sum(axis=1) >= 2)[0]   # rows and columns with more than a speck
    c = np.nonzero(inner.sum(axis=0) >= 2)[0]
    if not len(r) or not len(c):
        raise ValueError(f"{obj.name}: no drawing found inside the frame")
    sx, sy = pg.width / w, pg.height / h
    x0, x1 = c[0] * sx - margin, (c[-1] + 1) * sx + margin
    y0, y1 = pg.height - (r[-1] + 1) * sy - margin, pg.height - r[0] * sy + margin
    return (max(0.0, x0), max(0.0, y0), min(pg.width, x1), min(pg.height, y1))


def auto_crop(obj, margin=0.005):
    """Crop the plan to auto_crop_rectangle(); returns the rectangle."""
    rect = auto_crop_rectangle(obj, margin)
    import_plan.set_sheet_rectangle(obj, *rect)
    return rect


# ---------------------------------------------------------------- placement check

def footprint(obj):
    """World XY corners of the plan's crop rectangle."""
    x0, y0, x1, y1 = crop_rectangle(obj)
    m = obj.matrix_world
    return [(m @ Vector((x, y, 0.0))).to_2d() for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def _bounds(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


class _Grid:
    """A world XY grid with cells of size cell over a box."""

    def __init__(self, box, cell):
        x0, y0, x1, y1 = box
        self.cell = cell
        self.x0, self.y0 = x0, y0
        self.nx, self.ny = max(1, int(math.ceil((x1 - x0) / cell))), max(1, int(math.ceil((y1 - y0) / cell)))

    def centers(self):
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.cell
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.cell
        return np.meshgrid(xs, ys)  # arrays of shape (ny, nx)


def _plan_ink(obj, grid):
    """(ink 0..1, valid) of a flat plan on the grid."""
    pg = obj.bii_plan
    g = gray(obj)
    h, w = g.shape
    m = obj.matrix_world
    scale = (m.to_3x3() @ Vector((1.0, 0.0, 0.0))).length          # world meters per sheet meter
    k = max(1, int(grid.cell / scale / (pg.width / w)))             # image pixels per cell
    pooled = _block_min(g, k) < INK_LEVEL
    ph, pw = pooled.shape
    inv = m.inverted()
    X, Y = grid.centers()
    z = (m @ Vector((0.0, 0.0, 0.0))).z
    sx = inv[0][0] * X + inv[0][1] * Y + inv[0][2] * z + inv[0][3]
    sy = inv[1][0] * X + inv[1][1] * Y + inv[1][2] * z + inv[1][3]
    x0, y0, x1, y1 = crop_rectangle(obj)
    valid = (sx >= x0) & (sx <= x1) & (sy >= y0) & (sy <= y1)
    col = np.clip((sx / pg.width * w / k).astype(int), 0, pw - 1)
    row = np.clip(((pg.height - sy) / pg.height * h / k).astype(int), 0, ph - 1)
    ink = pooled[row, col] & valid
    return ink.astype(np.float32), valid


def _track_ink(scene, grid, width=1.5):
    """(ink, valid) of the Umgebung track axes on the grid; valid everywhere."""
    ink = np.zeros((grid.ny, grid.nx), np.float32)
    step = grid.cell / 2
    r = max(0, int(round(width / 2 / grid.cell)))
    for pts in plan_place.track_polylines(scene, ""):
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(bx - ax, by - ay) / step))
            t = np.linspace(0.0, 1.0, n + 1)
            cx = ((ax + (bx - ax) * t - grid.x0) / grid.cell).astype(int)
            cy = ((ay + (by - ay) * t - grid.y0) / grid.cell).astype(int)
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    ok = (cx + dx >= 0) & (cx + dx < grid.nx) & (cy + dy >= 0) & (cy + dy < grid.ny)
                    ink[cy[ok] + dy, cx[ok] + dx] = 1.0
    return ink, np.ones_like(ink, dtype=bool)


def _blur(a, passes=2):
    """A small box blur, so lines of slightly different width and position still meet."""
    for _ in range(passes):
        b = a.copy()
        b[1:, :] += a[:-1, :]
        b[:-1, :] += a[1:, :]
        b[:, 1:] += a[:, :-1]
        b[:, :-1] += a[:, 1:]
        a = b / 5.0
    return a


def _correlate(a, b, max_shift_cells):
    """Normalized cross-correlation of b moved over a, for shifts up to max_shift_cells.
    Returns (surface, offset): surface[i, j] is the score for moving b by (j - offset, i - offset)."""
    a = a - a.mean()
    b = b - b.mean()
    norm = math.sqrt(float((a * a).sum()) * float((b * b).sum())) or 1.0
    ny, nx = a.shape
    fy, fx = ny + max_shift_cells, nx + max_shift_cells
    fa = np.fft.rfft2(a, (fy, fx))
    fb = np.fft.rfft2(b, (fy, fx))
    corr = np.fft.irfft2(fa * np.conj(fb), (fy, fx)) / norm   # corr[dy, dx]: a(p) vs b(p - d)
    s = max_shift_cells
    rows = np.r_[fy - s:fy, 0:s + 1]
    cols = np.r_[fx - s:fx, 0:s + 1]
    return corr[np.ix_(rows, cols)], s


def _ambiguity(surface, peak, cell):
    """(along_axis_deg or None, spread_m): where the score stays near its peak along a line (straight
    track), the shift along that line is not fixed by the drawing."""
    near = surface >= surface[peak] - 0.15 * (surface[peak] - np.median(surface))
    ys, xs = np.nonzero(near)
    if len(xs) < 3:
        return None, 0.0
    cov = np.cov(np.vstack((xs, ys)).astype(float))
    vals, vecs = np.linalg.eigh(cov)
    spread = 2 * math.sqrt(max(vals[1], 0.0)) * cell
    if vals[1] < 9 * max(vals[0], 0.25):  # not elongated: the shift is fixed in both directions
        return None, spread
    vx, vy = vecs[:, 1]
    return math.degrees(math.atan2(vy, vx)) % 180.0, spread


def compare(obj, reference, cell=1.0, max_shift=15.0, scene=None):
    """How far the flat plan obj should move in XY to match reference best.

    reference: another plan object, or "TRACKS" for the BII Umgebung track axes. Returns a dict:
    shift (dx, dy) in meters to add to the plan's location; distance; score (0..1, how well the
    ink matches after the shift) and score_now (before); overlap (m²); along_deg and spread: when
    the match is a straight band (e.g. only straight tracks overlap), along_deg is its direction
    and the shift along it is not reliable; reliable: False when the best match lies at the edge of
    the search or is hardly better than the current place, which happens when the drawings differ
    (a scanned Lageplan against a Signallageplan, plans against OSM tracks): then compare by eye
    with overlay(). Raises ValueError without overlap.
    """
    scene = scene or bpy.context.scene
    if obj.bii_plan.orientation != 'FLAT':
        raise ValueError(f"{obj.name} is not a flat plan")
    box = _bounds(footprint(obj))
    if reference != "TRACKS":
        if reference.bii_plan.orientation != 'FLAT':
            raise ValueError(f"{reference.name} is not a flat plan")
        rb = _bounds(footprint(reference))
        box = (max(box[0], rb[0]), max(box[1], rb[1]), min(box[2], rb[2]), min(box[3], rb[3]))
    pad = max_shift
    box = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    if box[2] - box[0] <= 2 * pad or box[3] - box[1] <= 2 * pad:
        raise ValueError(f"{obj.name} and {getattr(reference, 'name', reference)} do not overlap")
    while ((box[2] - box[0]) / cell) * ((box[3] - box[1]) / cell) > MAX_CELLS:
        cell *= 1.5
    grid = _Grid(box, cell)
    ink, valid = _plan_ink(obj, grid)
    if reference == "TRACKS":
        ref, ref_valid = _track_ink(scene, grid)
        if not ref.any():
            raise ValueError("No BII Umgebung track axes in the scene")
    else:
        ref, ref_valid = _plan_ink(reference, grid)
    both = valid & ref_valid
    overlap = float(both.sum()) * cell * cell
    if overlap <= 0:
        raise ValueError(f"{obj.name} and {getattr(reference, 'name', reference)} do not overlap")
    a = _blur(np.where(ref_valid, ref, 0.0))
    b = _blur(np.where(valid, ink, 0.0))
    s = int(math.ceil(max_shift / cell))
    surface, off = _correlate(a, b, s)
    peak = np.unravel_index(int(np.argmax(surface)), surface.shape)
    dy, dx = (peak[0] - off) * cell, (peak[1] - off) * cell
    along, spread = _ambiguity(surface, peak, cell)
    edge = max(abs(peak[0] - off), abs(peak[1] - off)) >= s - 1
    gain = float(surface[peak] - surface[off, off])
    # below MIN_SCORE the drawings share too little: false matches on parallel tracks and text (tried on
    # real plans: true matches scored 0.27 and more, false ones 0.14 and less)
    reliable = not edge and (gain >= 0.005 or math.hypot(dx, dy) <= 2 * cell) and float(surface[peak]) >= MIN_SCORE
    return {
        "reference": getattr(reference, "name", reference),
        "shift": (dx, dy),
        "distance": math.hypot(dx, dy),
        "score": float(surface[peak]),
        "score_now": float(surface[off, off]),
        "overlap": overlap,
        "along_deg": along,
        "spread": spread,
        "cell": cell,
        "reliable": reliable,
    }


def overlay(obj, reference, center, path, size=80.0, pixels=800, scene=None):
    """Write a PNG of a size x size m window around center (world x, y): the plan's ink red, the
    reference's ink blue (another plan, or "TRACKS" for the track axes), both together dark purple,
    a light 10 m grid. Where the plan sits right, red lies on blue. Returns path."""
    from PIL import Image, ImageDraw
    scene = scene or bpy.context.scene
    cell = size / pixels
    box = (center[0] - size / 2, center[1] - size / 2, center[0] + size / 2, center[1] + size / 2)
    grid = _Grid(box, cell)
    red, _ = _plan_ink(obj, grid)
    if reference == "TRACKS":
        blue, _ = _track_ink(scene, grid, width=0.6)
    else:
        blue, _ = _plan_ink(reference, grid)
    rgb = np.full((grid.ny, grid.nx, 3), 255, np.uint8)
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * cell
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * cell
    rgb[:, (np.abs((xs + 5) % 10 - 5) < cell / 2)] = (225, 225, 225)
    rgb[(np.abs((ys + 5) % 10 - 5) < cell / 2), :] = (225, 225, 225)
    r, b = red > 0, blue > 0
    rgb[b] = (40, 90, 255)
    rgb[r] = (235, 30, 30)
    rgb[r & b] = (90, 0, 110)
    image = Image.fromarray(rgb[::-1])  # rows from the top: north up
    draw = ImageDraw.Draw(image)
    name = "track axes" if reference == "TRACKS" else reference.name
    draw.text((6, 6), f"red: {obj.name}   blue: {name}   grid 10 m   north up", fill=(0, 0, 0))
    draw.text((6, 20), f"center {center[0]:.1f}, {center[1]:.1f}   {size:.0f} m", fill=(0, 0, 0))
    image.save(path)
    return path


def _inside(quad, p):
    """True if p lies inside the convex quad (corners in order)."""
    signs = []
    for (ax, ay), (bx, by) in zip(quad, quad[1:] + quad[:1]):
        signs.append((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) >= 0)
    return all(signs) or not any(signs)


def overlap_centers(obj, reference, count=3):
    """Up to count world (x, y) points along the middle of the plan's crop that also lie on
    reference (a plan; "TRACKS": anywhere on the plan), for overlay()."""
    x0, y0, x1, y1 = crop_rectangle(obj)
    m = obj.matrix_world
    long_x = (x1 - x0) >= (y1 - y0)
    quad = None if reference == "TRACKS" else footprint(reference)
    candidates = []
    for i in range(4 * count):
        t = (i + 0.5) / (4 * count)
        x, y = (x0 + t * (x1 - x0), (y0 + y1) / 2) if long_x else ((x0 + x1) / 2, y0 + t * (y1 - y0))
        w = (m @ Vector((x, y, 0.0))).to_2d()
        if quad is None or _inside(quad, w):
            candidates.append((w.x, w.y))
    if len(candidates) <= count:
        return candidates
    step = (len(candidates) - 1) / (count - 1) if count > 1 else 0
    return [candidates[round(i * step)] for i in range(count)] if count > 1 else [candidates[len(candidates) // 2]]


def _overlap_area(quad_a, quad_b, step=None):
    """Approximate area (m²) where two convex quads overlap, by sampling."""
    x0, y0, x1, y1 = _bounds(quad_a)
    bx0, by0, bx1, by1 = _bounds(quad_b)
    x0, y0, x1, y1 = max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    step = step or max((x1 - x0), (y1 - y0)) / 60
    n = 0
    for x in np.arange(x0 + step / 2, x1, step):
        for y in np.arange(y0 + step / 2, y1, step):
            if _inside(quad_a, (x, y)) and _inside(quad_b, (x, y)):
                n += 1
    return n * step * step


def validate(obj, scene=None, compare_plans=True):
    """Checks of a placed flat plan, one call. Returns {"ok", "problems": [...], "notes": [...],
    "placement", "checks"}: problems are findings that need a look (a wrong placement is likely),
    notes say what could not be decided. Checks:
    - Maßstab within 0.5 % of the printed one, residuals (grid placement);
    - every coordinate number used lands on its own grid line (raw, not snapped to the nearest);
    - Kilometrierung points: their residuals;
    - overlap with the other plans (a sheet whose neighbors all overlap it but which overlaps
      none is suspicious) and the placement check against plans and track axes;
    - the plan lies above the terrain."""
    from . import plan_georef
    scene = scene or bpy.context.scene
    pg = obj.bii_plan
    problems, notes, checks = [], [], {}
    rec = placement(obj)
    if pg.orientation != 'FLAT':
        return {"ok": True, "problems": [], "notes": ["upright plan: not checked"], "placement": rec,
                "checks": {}}
    if rec is None and not len(pg.points) and not len(obj.bii_plan_place.km_points):
        problems.append("no placement recorded and no points: the plan may not be placed")
    # grid placement
    summary = plan_georef.fit_summary(pg) if len(pg.points) else None
    if summary:
        scale, rms, worst, warnings = summary
        checks["massstab"] = round(scale, 2)
        checks["rms_m"] = round(rms, 3)
        if pg.massstab and abs(scale / pg.massstab - 1) > 0.005:
            problems.append(f"measured Maßstab 1:{scale:.1f}, printed 1:{pg.massstab:.0f}: a cross or "
                            "the coordinate system is wrong")
        if rms > 0.5 or worst.residual > 1.0:
            problems.append(f"residuals {rms:.2f} m RMS, largest {worst.residual:.2f} m ({worst.name})")
        problems += [f"georeference: {w}" for w in warnings]
    stored = grid(obj)
    if rec and rec.get("numbers") and stored:
        # each number read must lie on its own line at the plan's current place
        fit = plan_georef.sheet_fit(pg)
        bad = []
        for fam, idx, value in rec["numbers"]:
            o, a, b = (np.array(stored[k]) for k in ("origin", "a", "b"))
            p = o + idx * (a if fam == "i" else b) + 0.5 * (b if fam == "i" else a)
            if fit:
                from . import plan_geo
                e, n = plan_geo.apply_similarity(fit, p[0], p[1])
                got = e if abs(e - value) < abs(n - value) else n
                if abs(got - value) > 1.0:
                    bad.append((fam, idx, value, round(got, 1)))
        checks["numbers_on_their_lines"] = len(rec["numbers"]) - len(bad)
        if bad:
            problems.append(f"numbers not on their grid lines: {bad}")
    if not summary and len(obj.bii_plan_place.km_points):
        res = [p.residual for p in obj.bii_plan_place.km_points if p.residual >= 0]
        if res:
            checks["km_residual_max_m"] = round(max(res), 2)
            if max(res) > 5:
                problems.append(f"Kilometrierung residuals up to {max(res):.1f} m: is every point the "
                                "km mark on the track (not the label text)?")
        notes.append("placed by Kilometrierung: OpenStreetMap km can be 20 m and more off along the track")
    # overlaps with the other flat plans
    quad = footprint(obj)
    overlaps = {}
    for other in plans(scene):
        if other is obj or other.bii_plan.orientation != 'FLAT':
            continue
        area = _overlap_area(quad, footprint(other))
        if area > 1.0:
            overlaps[other.name] = round(area)
    checks["overlaps_m2"] = overlaps
    prefix = obj.name[:4]
    series = [o for o in plans(scene) if o is not obj and o.name[:4] == prefix]
    if series and not overlaps:
        near = min(((Vector(_bounds(footprint(o))[:2]) - Vector(_bounds(quad)[:2])).length, o.name)
                   for o in series)
        if near[0] < 3000:
            problems.append(f"overlaps no other plan, while sheets of its series lie {near[0]:.0f} m away "
                            f"({near[1]}): placed far from its neighbors?")
    # compare drawings
    if compare_plans:
        for r in check_placement(obj, scene=scene):
            name = r.get("reference")
            if r.get("reliable") and r.get("distance", 0) > (1.0 if name != "TRACKS" else 5.0):
                problems.append(f"{describe(r)}")
            elif not r.get("reliable"):
                notes.append(f"{name}: no clear match (drawings differ); use overlay() to look")
            checks.setdefault("compare", []).append({k: r.get(k) for k in ("reference", "reliable", "distance")})
    # terrain
    try:
        from . import signal_panel
        zs = []
        for x, y in quad:
            z = signal_panel.terrain_height(bpy.context, x, y)
            if z is not None:
                zs.append(z)
        if zs and obj.matrix_world.translation.z < max(zs):
            problems.append(f"lies at z {obj.matrix_world.translation.z:.1f} m, below the terrain "
                            f"(up to {max(zs):.1f} m at its corners)")
    except Exception:  # noqa: BLE001 - no terrain
        pass
    return {"ok": not problems, "problems": problems, "notes": notes, "placement": rec, "checks": checks}


def check_placement(obj, cell=1.0, max_shift=15.0, scene=None):
    """compare() of a flat plan with every other flat plan it overlaps, and with the track axes
    if the scene has any. Returns a list of the result dicts (no overlap: left out)."""
    scene = scene or bpy.context.scene
    out = []
    refs = [o for o in plans(scene) if o is not obj and o.bii_plan.orientation == 'FLAT']
    if plan_place.track_polylines(scene, ""):
        refs.append("TRACKS")
    for ref in refs:
        try:
            out.append(compare(obj, ref, cell, max_shift, scene))
        except ValueError:
            continue
    return out


def describe(result, tolerance=1.0):
    """One line of text for a compare() result."""
    dx, dy = result["shift"]
    ref = "track axes" if result["reference"] == "TRACKS" else result["reference"]
    if not result["reliable"]:
        return (f"{ref}: no clear match in the drawings (best score {result['score']:.2f}); "
                "compare by eye: plan_api.overlay() or lower the plan's Opacity")
    verdict = "fits" if result["distance"] <= tolerance else f"off by {result['distance']:.1f} m"
    text = (f"{ref}: {verdict} (move {dx:+.1f}, {dy:+.1f} m; score {result['score_now']:.2f} -> "
            f"{result['score']:.2f})")
    if result["along_deg"] is not None:
        text += f"; along {result['along_deg']:.0f}° not fixed (±{result['spread'] / 2:.0f} m)"
    return text


# ---------------------------------------------------------------- operators

class PlanAutoCropOperator(bpy.types.Operator):
    """Crop the selected plans to their drawing: without empty paper and the sheet's frame lines.
    The title block and legends inside the frame stay"""
    bl_idname = "bii_plan.auto_crop"
    bl_label = "Auto Crop"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(import_plan.is_plan(o) for o in context.selected_objects)

    def execute(self, context):
        done, failed = 0, []
        for obj in [o for o in context.selected_objects if import_plan.is_plan(o)]:
            try:
                auto_crop(obj)
                done += 1
            except ValueError as e:
                failed.append(str(e))
        if failed:
            self.report({'WARNING'}, f"Cropped {done}; " + "; ".join(failed))
        else:
            self.report({'INFO'}, f"Cropped {done} plan(s) to their drawing")
        return {'FINISHED'}


class PlanCheckPlacementOperator(bpy.types.Operator):
    """Compare the plan's drawing with the plans it overlaps and with the BII Umgebung track axes:
    how far it should move to match them best"""
    bl_idname = "bii_plan.check_placement"
    bl_label = "Check Placement"
    bl_options = {'REGISTER'}

    tolerance: bpy.props.FloatProperty(name="Tolerance", default=1.0, min=0.0, unit='LENGTH',
                                       description="Offsets up to this count as a fit")

    @classmethod
    def poll(cls, context):
        obj = context.object
        return import_plan.is_plan(obj) and obj.bii_plan.orientation == 'FLAT'

    def execute(self, context):
        try:
            results = check_placement(context.object, scene=context.scene)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if not results:
            self.report({'WARNING'}, "Nothing to compare with: no overlapping flat plan and no track axes")
            return {'CANCELLED'}
        lines = [describe(r, self.tolerance) for r in results]
        off = any(r["distance"] > self.tolerance and r["score"] >= 0.05 for r in results)
        for line in lines:
            print("Check Placement:", context.object.name, "-", line)
        self.report({'WARNING' if off else 'INFO'}, " | ".join(lines))
        return {'FINISHED'}


classes = (PlanAutoCropOperator, PlanCheckPlacementOperator)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

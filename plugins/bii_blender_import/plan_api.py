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
CONTRAST = 1.08          # fit_to_plans: the best match must beat the next one by this factor


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
    _record(obj, "km", points=len(km_points))
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


def groups_without_signal(obj, color="red", points=None, margin=0.004, scene=None):
    """color_clusters() of the plan that no placed signal stands in or next to: the red symbols
    still to account for. points: world positions of the placed signals (default: every signal
    object of the scene, see signal_panel.read_signal); margin: sheet meters around each group
    (4 mm). Each group returned is either a signal or sign not placed yet, or something else
    (label, km text, track circuit mark): say which in the inventory."""
    from . import signal_panel
    scene = scene or bpy.context.scene
    if points is None:
        points = [o.matrix_world.translation for o in scene.objects if signal_panel.read_signal(o)]
    pg = obj.bii_plan
    w = pg.image.size[0]
    pad = margin / pg.width * w
    feet = []
    for p in points:
        u, v = world_to_plan(obj, Vector(p).to_3d(), 'IMAGE')
        feet.append((u, v))
    out = []
    for g in color_clusters(obj, color):
        u0, v0, u1, v1 = g["box"]
        if not any(u0 - pad <= u <= u1 + pad and v0 - pad <= v <= v1 + pad for u, v in feet):
            out.append(g)
    return out


def _symbol_frame(mask, px_per_mm, foot, head, length_mm=26.0, half_mm=5.0, step_mm=0.1):
    """The mask sampled in the symbol's own frame: rows from the foot (row 0) towards the head and
    beyond, columns across from -half_mm to +half_mm."""
    ux, uy = head[0] - foot[0], head[1] - foot[1]
    n = math.hypot(ux, uy) or 1.0
    ux, uy = ux / n, uy / n
    nx, ny = -uy, ux
    s = np.arange(0.0, length_mm, step_mm) * px_per_mm
    t = np.arange(-half_mm, half_mm + 1e-9, step_mm) * px_per_mm
    S, T = np.meshgrid(s, t, indexing="ij")
    X = np.round(foot[0] + S * ux + T * nx).astype(int)
    Y = np.round(foot[1] + S * uy + T * ny).astype(int)
    h, w = mask.shape
    inside = (X >= 0) & (X < w) & (Y >= 0) & (Y < h)
    out = np.zeros(S.shape, bool)
    out[inside] = mask[Y[inside], X[inside]]
    return out


def _runs_of(row):
    """(start, end) index pairs of the True runs in a 1-D bool array."""
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def symbol_features(obj, foot, head, unit='IMAGE', color="red", rgb=None):
    """Measure a signal symbol on the plan instead of judging it by eye. foot: the foot bar's
    center; head: any point further along the symbol's axis, towards its far end (both in unit).
    Returns a dict:
    - "bedienung": "stellwerk" (head completely filled), "stellwerk_zug" (one half filled along its
      length), "zug" (empty) or None. Counted over the rows of the head's widest part: filled on
      both sides, on one side only, or on neither. The rows of an Sh 1 part (bars and white fields)
      are full or empty on both sides alike, so they do not look half filled;
    - "rows": those counts; "sure": True when there are 15 rows or more and 80 % of them agree;
    - "chevron": "zs1" (tip pointing away from the head), "zs7" (tip pointing towards the head) or
      None, from how the two arms of the mark at the far end move apart;
    - "length_mm": from the foot to the far end of the symbol.
    A measurement, not a verdict: compare it with what you see, and look again where they differ.
    rgb: the plan's pixels (_rgb(obj)), to measure many symbols without reading them each time."""
    pg = obj.bii_plan
    if rgb is None:
        rgb = _rgb(obj)
    mask = COLORS[color](rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int))
    w = mask.shape[1]
    px_per_mm = w / (pg.width * 1000.0)
    f = from_sheet(obj, *to_sheet(obj, *foot, unit), 'IMAGE')
    hd = from_sheet(obj, *to_sheet(obj, *head, unit), 'IMAGE')
    # a foot point a little off the drawing: move it to the nearest red pixel within 1 mm
    r = int(math.ceil(px_per_mm))
    x0, y0 = int(round(f[0])), int(round(f[1]))
    win = mask[max(0, y0 - r):y0 + r + 1, max(0, x0 - r):x0 + r + 1]
    if win.any() and not mask[min(max(y0, 0), mask.shape[0] - 1), min(max(x0, 0), mask.shape[1] - 1)]:
        ys, xs = np.nonzero(win)
        k = int(np.argmin((ys + max(0, y0 - r) - y0) ** 2 + (xs + max(0, x0 - r) - x0) ** 2))
        dx, dy = xs[k] + max(0, x0 - r) - x0, ys[k] + max(0, y0 - r) - y0
        f, hd = (f[0] + dx, f[1] + dy), (hd[0] + dx, hd[1] + dy)
    step = 0.1
    fr = _symbol_frame(mask, px_per_mm, f, hd, step_mm=step)
    mid = fr.shape[1] // 2
    near = int(2.0 / step)                                   # the axis region: 2 mm each side
    rows_with = np.nonzero(fr[:, mid - near:mid + near + 1].any(axis=1))[0]
    out = {"bedienung": None, "rows": None, "chevron": None, "length_mm": 0.0, "sure": False}
    if not len(rows_with):
        return out
    end = rows_with[0]
    for r in rows_with[1:]:                                   # stays connected: gaps up to 1.5 mm
        if r - end > int(1.5 / step):
            break
        end = r
    sym = fr[:end + 1]
    out["length_mm"] = round((end + 1) * step, 1)
    extent = np.zeros(len(sym))
    for i, row in enumerate(sym):
        runs = [(a, b) for a, b in _runs_of(row) if b > mid - near and a < mid + near]
        if runs:
            extent[i] = (max(b for a, b in runs) - min(a for a, b in runs)) * step
    if extent.max() < 1.5:
        return out
    # the head: the longest stretch of rows nearly as wide as the widest row, beyond the foot bar
    skip = int(1.5 / step)
    extent[:skip] = 0.0
    if extent.max() < 1.5:
        return out
    wide = extent >= 0.8 * extent.max()
    best, cur = (0, 0), None
    for i, v in enumerate(np.append(wide, False)):
        if v and cur is None:
            cur = i
        elif not v and cur is not None:
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    both = one = neither = 0
    rim = int(0.4 / step)                                      # the outline left out
    for row in sym[best[0]:best[1]]:
        runs = _runs_of(row)
        if not runs:
            continue
        lo, hi = min(a for a, b in runs), max(b for a, b in runs)
        inner_l = row[lo + rim:(lo + hi) // 2 - 1]
        inner_r = row[(lo + hi) // 2 + 1:hi - rim]
        if len(inner_l) < 3 or len(inner_r) < 3:
            continue
        fl, frr = float(inner_l.mean()), float(inner_r.mean())
        if fl > 0.7 and frr > 0.7:
            both += 1
        elif max(fl, frr) > 0.7 and min(fl, frr) < 0.3:
            one += 1
        elif fl < 0.3 and frr < 0.3:
            neither += 1
    out["rows"] = {"both": both, "one": one, "neither": neither}
    total = both + one + neither
    out["sure"] = total >= 15 and max(both, one, neither) >= 0.8 * total
    if total >= 5:
        if one >= max(3, 0.3 * total):
            out["bedienung"] = "stellwerk_zug"
        elif both >= max(3, 0.3 * total):
            out["bedienung"] = "stellwerk"
        else:
            out["bedienung"] = "zug"
    # the chevron beyond the head: rows with two thin strokes; their distance grows towards the
    # far end for a V opening outwards (Zs 7) and shrinks for a ^ (Zs 1)
    # (only the last part, after the last empty row: not the rounded end of the head)
    empty = [i for i in range(best[1], len(sym)) if not sym[i].any()]
    seps = []
    start = empty[-1] + 1 if empty else len(sym)
    for i, row in enumerate(sym[start:]):
        runs = [(a, b) for a, b in _runs_of(row) if b - a < int(1.5 / step)]
        if len(runs) == 2 and len(_runs_of(row)) == 2:
            seps.append((i * step, (runs[1][0] + runs[1][1] - runs[0][0] - runs[0][1]) / 2 * step))
    if len(seps) >= 6:
        slope = float(np.polyfit([a for a, _ in seps], [b for _, b in seps], 1)[0])
        if abs(slope) > 0.3:
            out["chevron"] = "zs7" if slope > 0 else "zs1"
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
    import json
    obj["bii_grid_labels"] = json.dumps([[c["family"], c["index"]] for c in cands])
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
    import json
    labels = json.loads(obj.get("bii_grid_labels") or "[]")
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


def seat_above_terrain(obj, clearance=3.0, samples=12):
    """Lift or lower a flat plan so it lies clearance meters above the highest BII Umgebung
    terrain under its visible part (terrain: meshes with the property "dem_service"). Only the
    height changes. Returns the new height, or None without terrain under the plan."""
    from . import signal_panel
    x0, y0, x1, y1 = crop_rectangle(obj)
    m = obj.matrix_world
    zs = []
    for i in range(samples + 1):
        for j in range(samples + 1):
            w = m @ Vector((x0 + (x1 - x0) * i / samples, y0 + (y1 - y0) * j / samples, 0.0))
            z = signal_panel.terrain_height(bpy.context, w.x, w.y)
            if z is not None:
                zs.append(z)
    if not zs:
        return None
    obj.location.z += max(zs) + clearance - (m @ Vector((0.0, 0.0, 0.0))).z
    bpy.context.view_layer.update()
    return obj.matrix_world.translation.z


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


# ---------------------------------------------------------------- fit to placed plans

def grid_placed(obj):
    """True if the plan is placed by its coordinate grid (find_grid/georeference, or Place in
    Scene by two or more grid crosses): the most accurate placement there is."""
    rec = placement(obj)
    if rec:
        return rec.get("method") == "grid"
    return len(obj.bii_plan.points) >= 2


def _neutral_ink(obj, cell, scale):
    """Ink of the plan pooled to about cell meters: dark, not colored pixels (red or blue
    symbols of a Signallageplan are not on the plans below). (mask, k, w, h)."""
    rgb = _rgb(obj).astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    ink = ((r + g + b) < 3 * INK_LEVEL) & (sat < 60)
    h, w = ink.shape
    k = max(1, int(cell / scale / (obj.bii_plan.width / w)))
    if k > 1:
        hh, ww = (h // k) * k, (w // k) * k
        ink = ink[:hh, :ww].reshape(hh // k, k, ww // k, k).any(axis=(1, 3))
    return ink, k, w, h


def _ink_at(obj, pooled, grid, m):
    """(ink, valid) of the plan on the grid as if its matrix were m."""
    pg = obj.bii_plan
    ink, k, w, h = pooled
    ph, pw = ink.shape
    inv = m.inverted()
    X, Y = grid.centers()
    z = (m @ Vector((0.0, 0.0, 0.0))).z
    sx = inv[0][0] * X + inv[0][1] * Y + inv[0][2] * z + inv[0][3]
    sy = inv[1][0] * X + inv[1][1] * Y + inv[1][2] * z + inv[1][3]
    x0, y0, x1, y1 = crop_rectangle(obj)
    valid = (sx >= x0) & (sx <= x1) & (sy >= y0) & (sy <= y1)
    col = np.clip((sx / pg.width * w / k).astype(int), 0, pw - 1)
    row = np.clip(((pg.height - sy) / pg.height * h / k).astype(int), 0, ph - 1)
    return (ink[row, col] & valid).astype(np.float32), valid, sx, sy


def _dilate(mask, r):
    """Square dilation of a bool mask by r cells."""
    for axis in (0, 1):
        out = mask.copy()
        for d in range(1, r + 1):
            if axis == 0:
                out[d:, :] |= mask[:-d, :]
                out[:-d, :] |= mask[d:, :]
            else:
                out[:, d:] |= mask[:, :-d]
                out[:, :-d] |= mask[:, d:]
        mask = out
    return mask


def _piece_surfaces(obj, pooled, m, grid, ref, ref_valid, length, search, blur):
    """Cut the plan into pieces of about length meters along its long side and match each piece's
    ink on the reference ink: [(center world (x, y), surface)], surface[i, j] the match score
    (normalized correlation, 0..1) for moving the piece by ((j - s) * cell, (i - s) * cell)."""
    ink, valid, sx, sy = _ink_at(obj, pooled, grid, m)
    x0, y0, x1, y1 = crop_rectangle(obj)
    scale = (m.to_3x3() @ Vector((1.0, 0.0, 0.0))).length
    long_x = (x1 - x0) >= (y1 - y0)
    lo_all, hi_all, coord = (x0, x1, sx) if long_x else (y0, y1, sy)
    n = max(1, int(round((hi_all - lo_all) * scale / length)))
    s = int(math.ceil(search / grid.cell))
    out = []
    for i in range(n):
        lo = lo_all + (hi_all - lo_all) * i / n
        hi = lo_all + (hi_all - lo_all) * (i + 1) / n
        part = valid & ref_valid & (coord >= lo) & (coord < hi)
        if part.sum() * grid.cell ** 2 < 200:
            continue
        rows, cols = np.nonzero(part)
        r0, r1 = max(0, rows.min() - s), min(grid.ny, rows.max() + s + 1)
        c0, c1 = max(0, cols.min() - s), min(grid.nx, cols.max() + s + 1)
        p = part[r0:r1, c0:c1]
        b = _blur(np.where(p, ink[r0:r1, c0:c1], 0.0), blur)
        if not b.any():
            continue
        # the reference around the piece, grown by the search radius: cut to the piece itself, every
        # shift away from 0 would lose overlap and the match would lean towards no shift
        a = ref[r0:r1, c0:c1] * _dilate(p, s)
        surface, _ = _correlate(a, b, s)
        cu = (lo + hi) / 2
        cv = float(np.mean((sy if long_x else sx)[part]))
        c = m @ Vector((cu, cv, 0.0) if long_x else (cv, cu, 0.0))
        out.append(((c.x, c.y), np.clip(surface, 0.0, None)))
    return out


def _shifted(a, dy, dx):
    """a moved by (dy, dx) cells, zeros where nothing moved in: out[i, j] = a[i + dy, j + dx]."""
    out = np.zeros_like(a)
    h, w = a.shape
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    if ys.stop > ys.start and xs.stop > xs.start:
        out[yd, xd] = a[ys, xs]
    return out


def _consensus(pieces, pivot, cell, turns):
    """For each turn (radians, about pivot) add up the pieces' match surfaces at the shift each
    piece needs for one common move and that turn. Returns (best value, turn, total surface)."""
    best = None
    for t in turns:
        total = None
        for (cx, cy), surface in pieces:
            # turning by t moves the piece's center by (-t * ry, t * rx)
            ox = int(round(-t * (cy - pivot[1]) / cell))
            oy = int(round(t * (cx - pivot[0]) / cell))
            moved = _shifted(surface, oy, ox)
            total = moved if total is None else total + moved
        value = float(total.max())
        if best is None or value > best[0]:
            best = (value, t, total)
    return best


def _subcell(total, peak):
    """Peak position refined by a parabola through its neighbors, in cells (row, col)."""
    out = []
    for axis in (0, 1):
        i = peak[axis]
        if 0 < i < total.shape[axis] - 1:
            idx = list(peak)
            idx[axis] = i - 1
            lo = total[tuple(idx)]
            idx[axis] = i + 1
            hi = total[tuple(idx)]
            mid = total[peak]
            den = lo - 2 * mid + hi
            out.append(i + (0.5 * (lo - hi) / den if den < 0 else 0.0))
        else:
            out.append(float(i))
    return out


def _turn_about(pivot, angle, shift=(0.0, 0.0)):
    from mathutils import Matrix
    t = Matrix.Translation(Vector((pivot[0], pivot[1], 0.0)))
    return Matrix.Translation(Vector((shift[0], shift[1], 0.0))) @ t @ Matrix.Rotation(angle, 4, 'Z') @ t.inverted()


def fit_to_plans(obj, references=None, apply=True, force=False, cell=0.5, max_shift=30.0, max_turn=1.5,
                 piece=80.0, corridor=12.0, tolerance=1.0, scene=None):
    """Fit a flat plan onto plans placed by their grid that lie under it (e.g. a Signallageplan
    placed roughly by Kilometrierung over scanned Lagepläne): moves and turns it, never scales.

    The plan is cut into pieces of about piece meters along its long side. Each piece's dark,
    uncolored ink (track lines, not the red or blue symbols) is matched on the ink of the
    references, within corridor meters of the BII Umgebung track axes if the scene has any, for
    every shift up to max_shift meters. Then for every turn up to max_turn degrees the pieces vote
    together: the move and turn where the sum of all pieces' scores is highest wins, so one piece
    that matches a neighboring track does not pull the plan away, and pieces at both ends of the
    sheet fix the turn. A line through the pieces' remaining offsets across the track refines the
    turn below the cell size. The whole search runs from the plan's place and from two places
    6 m and 0.4° away from it.
    - across the track: applied when the best result beats the next one more than 2 m across by
      CONTRAST, most pieces agree with it within tolerance meters, and all three starts end within
      tolerance meters of each other;
    - along the track: applied only when that direction is fixed as well (curves, switches, ends of
      tracks); else the plan keeps its place along the track (e.g. from the Kilometrierung), and
      "along_fixed" is False.
    references: plan objects; default: the flat plans placed by their grid that overlap it.
    apply=False only reports. A plan placed by its grid is not moved unless force=True.
    Returns a dict: references, applied, across_fixed, along_fixed, move (dx, dy at the plan's
    center), turn_deg, pieces (agreeing, of), residuals (per piece from one end of the sheet to the
    other, meters across the track; None where a piece has no clear match), contrast,
    contrast_along, spread_across, spread_along (of the three starts), message."""
    from mathutils import Matrix
    scene = scene or bpy.context.scene
    pg = obj.bii_plan
    if pg.orientation != 'FLAT':
        raise ValueError(f"{obj.name} is not a flat plan")
    if apply:
        _guard(obj, force)
    quad = footprint(obj)
    if references is None:
        references = [o for o in plans(scene) if o is not obj and o.bii_plan.orientation == 'FLAT'
                      and grid_placed(o) and _overlap_area(quad, footprint(o)) > 1.0]
    if not references:
        raise ValueError(f"{obj.name}: no plan placed by its grid lies under it")
    x0, y0, x1, y1 = crop_rectangle(obj)
    m0 = obj.matrix_world.copy()
    scale = (m0.to_3x3() @ Vector((1.0, 0.0, 0.0))).length
    half_len = 0.5 * max(x1 - x0, y1 - y0) * scale
    pad = max_shift + math.radians(max_turn) * half_len + 10.0
    box = _bounds(quad)
    box = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    while ((box[2] - box[0]) / cell) * ((box[3] - box[1]) / cell) > MAX_CELLS:
        cell *= 1.25
    grid = _Grid(box, cell)
    ref = np.zeros((grid.ny, grid.nx), np.float32)
    ref_valid = np.zeros((grid.ny, grid.nx), bool)
    for o in references:
        scale_o = (o.matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))).length
        ink, valid, _, _ = _ink_at(o, _neutral_ink(o, cell, scale_o), grid, o.matrix_world)
        ref = np.maximum(ref, ink)
        ref_valid |= valid
    if corridor and plan_place.track_polylines(scene, ""):
        ref_valid &= _dilate(_track_ink(scene, grid, width=0.0)[0] > 0, int(corridor / cell))
    ref = _blur(np.where(ref_valid, ref, 0.0), 2)
    pooled = _neutral_ink(obj, cell, scale)
    center = m0 @ Vector(((x0 + x1) / 2, (y0 + y1) / 2, 0.0))
    pivot = (center.x, center.y)
    long_x = (x1 - x0) >= (y1 - y0)
    axis = (m0.to_3x3() @ Vector((1.0, 0.0, 0.0) if long_x else (0.0, 1.0, 0.0))).to_2d().normalized()
    across_dir = Vector((-axis.y, axis.x))
    out = {"references": [o.name for o in references], "applied": False, "across_fixed": False,
           "along_fixed": False, "move": (0.0, 0.0), "turn_deg": 0.0, "pieces": (0, 0), "residuals": [],
           "contrast": None, "contrast_along": None, "spread_across": None,
           "spread_along": None}

    def solve(start):
        """Consensus fit from start (a matrix applied before m0): coarse over all shifts and turns,
        then twice finer around the result. Returns (matrix, stats) or (None, message)."""
        total_m = start.copy()
        stats = {}
        for search, turns in ((max_shift, np.radians(np.arange(-max_turn, max_turn + 1e-9, 0.05))),
                              (8.0, np.radians(np.arange(-0.3, 0.3 + 1e-9, 0.02))),
                              (4.0, np.radians(np.arange(-0.06, 0.06 + 1e-9, 0.01)))):
            pieces = _piece_surfaces(obj, pooled, total_m @ m0, grid, ref, ref_valid, piece, search, 2)
            if len(pieces) < 3:
                return None, (f"only {len(pieces)} piece(s) of the plan lie on the plans under it (3 needed)")
            value, turn, total = _consensus(pieces, pivot, grid.cell, turns)
            s = (total.shape[0] - 1) // 2
            peak = np.unravel_index(int(np.argmax(total)), total.shape)
            if search == max_shift:
                # the best result more than 2 m across the track from this one (e.g. one track
                # over), and the best one more than 2 m along it
                jj, ii = np.meshgrid(np.arange(total.shape[1]), np.arange(total.shape[0]))
                u = ((jj - peak[1]) * across_dir.x + (ii - peak[0]) * across_dir.y) * grid.cell
                v = ((jj - peak[1]) * axis.x + (ii - peak[0]) * axis.y) * grid.cell
                far_across = float(total[np.abs(u) > 2.0].max(initial=0.0))
                far_along = float(total[(np.abs(v) > 2.0) & (np.abs(u) <= 1.0)].max(initial=0.0))
                stats["contrast"] = value / max(far_across, 1e-6)
                stats["contrast_along"] = value / max(far_along, 1e-6)
                stats["along_deg"] = _ambiguity(total, peak, grid.cell)[0]
                stats["edge"] = max(abs(peak[0] - s), abs(peak[1] - s)) >= s - 1
            py, px = _subcell(total, peak)
            dx, dy = (px - s) * grid.cell, (py - s) * grid.cell
            total_m = _turn_about(pivot, float(turn), (dx, dy)) @ total_m
        # how each piece agrees: its own best shift near the result (to a fraction of a cell),
        # across the track; then a line through these offsets along the sheet corrects the turn and
        # the place across the track more finely than the cells allow
        def offsets(pcs):
            found, k = [], int(math.ceil(3.0 / grid.cell))
            s = (pcs[0][1].shape[0] - 1) // 2
            for (cx, cy), surface in pcs:
                window = surface[s - k:s + k + 1, s - k:s + k + 1]
                pk = np.unravel_index(int(np.argmax(window)), window.shape)
                pos = Vector((cx - pivot[0], cy - pivot[1])).dot(axis)
                if window[pk] < 0.15 or max(abs(pk[0] - k), abs(pk[1] - k)) >= k:
                    found.append((pos, None))
                    continue
                py, px = _subcell(window, pk)
                found.append((pos, Vector(((px - k) * grid.cell, (py - k) * grid.cell)).dot(across_dir)))
            return sorted(found, key=lambda o: o[0])

        meas = offsets(pieces)
        good = [(p, e) for p, e in meas if e is not None and abs(e) <= tolerance]
        if len(good) >= 3:
            slope, shift = np.polyfit(np.array([p for p, _ in good]), np.array([e for _, e in good]), 1)
            step = across_dir * float(shift)
            total_m = _turn_about(pivot, float(slope), (step.x, step.y)) @ total_m
            again = _piece_surfaces(obj, pooled, total_m @ m0, grid, ref, ref_valid, piece, 4.0, 2)
            if len(again) >= 3:
                meas = offsets(again)
        residuals = [None if e is None else round(abs(e), 2) for _, e in meas]
        agree = sum(e is not None and e <= tolerance for e in residuals)
        stats["agree"], stats["count"], stats["residuals"] = agree, len(meas), residuals
        # along the track: pieces of 40 m each vote for the shift along the plan's long side with
        # the best match (allowing 2 m across); a vote counts only when that best match clearly
        # beats every shift more than 2 m away along (switches, platform ends, signals). When at
        # least four agree within tolerance, they fix the place along the track.
        along = []
        for _, surface in _piece_surfaces(obj, pooled, total_m @ m0, grid, ref, ref_valid, 40.0, 15.0, 2):
            s2 = (surface.shape[0] - 1) // 2
            prof = []
            for a in np.arange(-14.0, 14.01, grid.cell):
                best = 0.0
                for c in np.arange(-2.0, 2.01, grid.cell):
                    d = axis * float(a) + across_dir * float(c)
                    j, i = int(round(s2 + d.x / grid.cell)), int(round(s2 + d.y / grid.cell))
                    if 0 <= i < surface.shape[0] and 0 <= j < surface.shape[1]:
                        best = max(best, float(surface[i, j]))
                prof.append(best)
            prof = np.array(prof)
            k = int(np.argmax(prof))
            r = int(round(2.0 / grid.cell))
            far = np.r_[prof[:max(0, k - r)], prof[k + r + 1:]]
            if prof[k] > 0.3 and len(far) and prof[k] > 1.05 * far.max() and 0 < k < len(prof) - 1:
                along.append(-14.0 + k * grid.cell)
        stats["along_votes"] = sorted(along)
        if len(along) >= 4:
            med = float(np.median(along))
            near = [a for a in along if abs(a - med) <= tolerance]
            if len(near) >= 4 and len(near) >= 0.6 * len(along):
                step = axis * float(np.mean(near))
                total_m = Matrix.Translation(Vector((step.x, step.y, 0.0))) @ total_m
                stats["along_fixed"] = len(near)
        return total_m, stats

    # from the plan's place and from two places moved and turned away from it: the result must not
    # depend on where the search starts
    starts = [Matrix.Identity(4),
              _turn_about(pivot, math.radians(0.4), (axis * 6.0 + across_dir * 5.0).to_tuple()),
              _turn_about(pivot, math.radians(-0.4), (-axis * 5.0 - across_dir * 6.0).to_tuple())]
    runs = [solve(st) for st in starts]
    total_m, stats = runs[0]
    if total_m is None:
        out["message"] = f"{obj.name}: not moved: {stats}"
        return out
    out["contrast"], out["contrast_along"] = stats["contrast"], stats["contrast_along"]
    edge, along_deg, agree = stats["edge"], stats["along_deg"], stats["agree"]
    pieces = [None] * stats["count"]
    out["pieces"] = (agree, stats["count"])
    out["residuals"] = stats["residuals"]
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def world_corners(t):
        return [(t @ m0 @ Vector((x, y, 0.0))).to_2d() for x, y in corners]

    c0 = world_corners(total_m)
    others = [t for t, _ in runs[1:]]
    if any(t is None for t in others):
        out["spread_across"] = out["spread_along"] = None
    else:
        out["spread_across"] = max(abs((p - q).dot(across_dir)) for t in others for p, q in zip(c0, world_corners(t)))
        out["spread_along"] = max(abs((p - q).dot(axis)) for t in others for p, q in zip(c0, world_corners(t)))
    moved = (total_m @ center).to_2d() - center.to_2d()
    turn_deg = math.degrees(total_m.to_euler().z)
    clear = out["contrast"] >= CONTRAST
    steady = out["spread_across"] is not None and out["spread_across"] <= tolerance
    out["across_fixed"] = bool(not edge and clear and steady and agree >= max(3, 0.6 * len(pieces)))
    by_features = stats.get("along_fixed", 0) >= 3
    out["along_pieces"] = stats.get("along_fixed", 0)
    out["along_votes"] = stats.get("along_votes", [])
    out["along_fixed"] = bool(out["across_fixed"] and out["spread_along"] <= tolerance
                              and (by_features or (along_deg is None and out["contrast_along"] >= CONTRAST)))
    if not out["across_fixed"]:
        why = ("the best match lies at the edge of the search" if edge else
               f"a match more than 2 m across is almost as good (contrast {out['contrast']:.2f})"
               if not clear else
               "the result depends on where the search starts" + (
                   f" ({out['spread_across']:.1f} m across the track)" if out["spread_across"] is not None else "")
               if not steady else f"only {agree} of {len(pieces)} pieces agree")
        out["message"] = f"{obj.name}: not moved: {why}"
        return out
    final = total_m
    if not out["along_fixed"]:
        # keep the place along the track: take back the move along the plan's long side
        final = Matrix.Translation(Vector((*(-axis * moved.dot(axis)), 0.0))) @ total_m
        moved = (final @ center).to_2d() - center.to_2d()
    out["move"] = (moved.x, moved.y)
    out["turn_deg"] = turn_deg
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    worst = max(((final @ m0 @ Vector((x, y, 0.0))) - (m0 @ Vector((x, y, 0.0)))).length for x, y in corners)
    out["message"] = (f"{obj.name}: moved {moved.length:.1f} m, turned {turn_deg:+.2f}° "
                      f"(corners up to {worst:.1f} m) onto {len(references)} plan(s); {agree} of "
                      f"{len(pieces)} pieces agree; "
                      + ("along the track fixed by the drawing" if out["along_fixed"] else
                         "along the track kept as it was (straight track: not fixed by the drawing)"))
    if apply:
        obj.matrix_world = final @ m0
        bpy.context.view_layer.update()
        out["applied"] = True
        _record(obj, "fit_to_plans", references=out["references"], move=[round(v, 2) for v in out["move"]],
                turn_deg=round(turn_deg, 3), along_fixed=out["along_fixed"], before=placement(obj))
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

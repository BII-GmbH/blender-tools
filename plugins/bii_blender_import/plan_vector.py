"""Signal symbols and cable troughs in the vector drawing of a PDF plan (pypdfium2, no bpy).

A Signallageplan draws its symbols as straight strokes. Two patterns are found here, in one
colour (red for the new signals):
- a symbol on a mast: a short bar (the foot) across the end of a longer line (the leader); the
  foot point is the end of the leader, the head lies along the leader;
- a Vorsignalbake (Ne 3): a narrow plate of two parallel lines about 8 mm long with diagonal
  stripes inside and a short support bar at one end.
A Kabeltiefbau plan draws new cable troughs as two parallel red lines, Gleisquerungen as red dashed
lines and Schächte as small red quads; km_marks() reads the hectometer marks to place the sheet.
Coordinates are PDF points, y down from the top of the page (unit 'PDF' of plan_api).
"""

import ctypes
import functools
import math
import os
import re

COLORS = {"red": lambda r, g, b: r > 0.6 and g < 0.45 and b < 0.45 and r - g > 0.5,
          "blue": lambda r, g, b: b > 0.6 and r < 0.45,
          "black": lambda r, g, b: max(r, g, b) < 0.25}
MM = 72 / 25.4      # PDF points per mm


def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _bezier(p0, p1, p2, p3, n=8):
    out = []
    for k in range(1, n + 1):
        t = k / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


@functools.lru_cache(maxsize=4)
def _read(path, page_index, mtime):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    def matrix(obj):
        m = raw.FS_MATRIX()
        return (m.a, m.b, m.c, m.d, m.e, m.f) if raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)) else (1, 0, 0, 1, 0, 0)

    def walk(handle, count, get, parent):
        for i in range(count(handle)):
            obj = get(handle, i)
            kind = raw.FPDFPageObj_GetType(obj)
            if kind == raw.FPDF_PAGEOBJ_FORM:
                yield from walk(obj, raw.FPDFFormObj_CountObjects, raw.FPDFFormObj_GetObject, _mul(matrix(obj), parent))
            elif kind in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_TEXT):
                yield obj, kind, parent

    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[page_index]
        w, h = page.get_width(), page.get_height()
        textpage = page.get_textpage()
        paths, texts = [], []
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        x, y = ctypes.c_float(), ctypes.c_float()
        for obj, kind, parent in walk(page.raw, raw.FPDFPage_CountObjects, raw.FPDFPage_GetObject, (1, 0, 0, 1, 0, 0)):
            if kind == raw.FPDF_PAGEOBJ_TEXT:
                n = raw.FPDFTextObj_GetText(obj, textpage.raw, None, 0)
                buf = (ctypes.c_ushort * max(n // 2, 1))()
                raw.FPDFTextObj_GetText(obj, textpage.raw, buf, n)
                text = bytes(buf)[:max(n - 2, 0)].decode("utf-16-le", "replace").strip()
                box = [ctypes.c_float() for _ in range(4)]
                if text and raw.FPDFPageObj_GetBounds(obj, *map(ctypes.byref, box)):
                    x0, y0, x1, y1 = (v.value for v in box)
                    corners = [(parent[0] * u + parent[2] * v + parent[4], h - (parent[1] * u + parent[3] * v + parent[5]))
                               for u in (x0, x1) for v in (y0, y1)]
                    texts.append((text, (min(c[0] for c in corners), min(c[1] for c in corners),
                                         max(c[0] for c in corners), max(c[1] for c in corners))))
                continue
            m = _mul(matrix(obj), parent)
            fill, stroke = ctypes.c_int(), ctypes.c_int()
            raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke))
            item = {"stroke": None, "fill": None, "segs": []}
            if stroke.value:
                raw.FPDFPageObj_GetStrokeColor(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
                item["stroke"] = (r.value / 255, g.value / 255, b.value / 255)
            if fill.value:
                raw.FPDFPageObj_GetFillColor(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
                item["fill"] = (r.value / 255, g.value / 255, b.value / 255)
            prev = start = None
            ctrl = []
            for i in range(raw.FPDFPath_CountSegments(obj)):
                seg = raw.FPDFPath_GetPathSegment(obj, i)
                raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
                p = (m[0] * x.value + m[2] * y.value + m[4], h - (m[1] * x.value + m[3] * y.value + m[5]))
                kind = raw.FPDFPathSegment_GetType(seg)
                if kind == raw.FPDF_SEGMENT_MOVETO:
                    start = prev = p
                elif kind == raw.FPDF_SEGMENT_LINETO and prev is not None:
                    item["segs"].append((prev, p, False))
                    prev = p
                elif kind == raw.FPDF_SEGMENT_BEZIERTO and prev is not None:
                    ctrl.append(p)
                    if len(ctrl) == 3:
                        for q in _bezier(prev, *ctrl):
                            item["segs"].append((prev, q, True))
                            prev = q
                        ctrl = []
                if raw.FPDFPathSegment_GetClose(seg) and start is not None and not ctrl and prev != start:
                    item["segs"].append((prev, start, kind == raw.FPDF_SEGMENT_BEZIERTO))
                    prev = start
            paths.append(item)
        return w, h, paths, texts
    finally:
        pdf.close()


def read_page(path, page_index=0):
    """(width, height, paths, texts) of a page, in PDF points with y down. paths: [{"stroke": (r, g, b) or
    None, "fill": (r, g, b) or None, "segs": [((x0, y0), (x1, y1), curved)]}], curves cut into straight
    pieces; texts: [(text, (x0, y0, x1, y1))]. Cached while the file stays the same."""
    return _read(os.path.abspath(path), page_index, os.path.getmtime(path))


def strokes(path, page_index=0, curves=False):
    """[((x0, y0), (x1, y1), (r, g, b))]: the straight stroked segments of a page (text left out), with
    curves=True also the pieces of curves"""
    return [(p, q, item["stroke"]) for item in read_page(path, page_index)[2] if item["stroke"]
            for p, q, curved in item["segs"] if curves or not curved]


def _unit(a, b):
    n = math.dist(a, b) or 1.0
    return (b[0] - a[0]) / n, (b[1] - a[1]) / n


def mast_symbols(segs, bar=(1.0, 3.2), min_leader=2.8, gap=0.3):
    """[{"foot", "axis", "leader"}] (PDF points; axis: unit vector from the foot towards the head);
    bar and leader lengths in mm"""
    segs = [(a, b) for a, b in segs if math.dist(a, b) > 0.2 * MM]
    found = []
    for i, (a, b) in enumerate(segs):
        ln = math.dist(a, b)
        if not bar[0] * MM <= ln <= bar[1] * MM:
            continue
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        ux, uy = _unit(a, b)
        for j, (c, d) in enumerate(segs):
            l2 = math.dist(c, d)
            if j == i or l2 < min_leader * MM:
                continue
            vx, vy = _unit(c, d)
            if abs(ux * vx + uy * vy) > 0.15:
                continue
            for end, other in ((c, d), (d, c)):
                if math.dist(end, mid) <= gap * MM:
                    found.append({"foot": end, "axis": _unit(end, other), "leader": l2 / MM})
    out = []
    for f in sorted(found, key=lambda f: -f["leader"]):
        if all(math.dist(f["foot"], g["foot"]) > 0.5 * MM for g in out):
            out.append(f)
    return out


def ne3_boards(segs, length=(7.0, 9.0), width=(1.6, 3.2)):
    """[{"foot", "axis", "stripes", "center"}]: Vorsignalbaken, plates of two parallel lines (length
    and width in mm) with diagonal stripes inside; the foot is the far end of the support bar"""
    longs = [(a, b) for a, b in segs if length[0] * MM <= math.dist(a, b) <= length[1] * MM]
    out = []
    for i, (a, b) in enumerate(longs):
        u = _unit(a, b)
        n = (-u[1], u[0])
        for c, d in longs[i + 1:]:
            v = _unit(c, d)
            if abs(u[0] * v[0] + u[1] * v[1]) < 0.98:
                continue
            sep = (c[0] - a[0]) * n[0] + (c[1] - a[1]) * n[1]
            if not width[0] * MM <= abs(sep) <= width[1] * MM:
                continue
            along = sorted([0.0, math.dist(a, b)])
            oc = sorted([(c[0] - a[0]) * u[0] + (c[1] - a[1]) * u[1], (d[0] - a[0]) * u[0] + (d[1] - a[1]) * u[1]])
            if min(along[1], oc[1]) - max(along[0], oc[0]) < 0.8 * math.dist(a, b):
                continue
            center = ((a[0] + b[0] + c[0] + d[0]) / 4, (a[1] + b[1] + c[1] + d[1]) / 4)
            half = math.dist(a, b) / 2

            def local(p):
                q = (p[0] - center[0], p[1] - center[1])
                return q[0] * u[0] + q[1] * u[1], q[0] * n[0] + q[1] * n[1]

            stripes, bars = 0, []
            for p, q in segs:
                lp, lq = local(p), local(q)
                if max(abs(lp[0]), abs(lq[0])) <= half + 0.3 * MM and max(abs(lp[1]), abs(lq[1])) <= abs(sep) / 2 + 0.3 * MM:
                    w = _unit(p, q)
                    cos = abs(w[0] * u[0] + w[1] * u[1])
                    if 0.4 < cos < 0.9 and math.dist(p, q) > 0.8 * abs(sep):
                        stripes += 1
                elif half + 0.3 * MM < min(abs(lp[0]), abs(lq[0])) and max(abs(lp[0]), abs(lq[0])) < half + 2.5 * MM                         and max(abs(lp[1]), abs(lq[1])) <= abs(sep) / 2 + 0.5 * MM:
                    # the foot bar across the short support beyond one end of the plate
                    w = _unit(p, q)
                    if abs(w[0] * n[0] + w[1] * n[1]) > 0.9 and 0.8 * MM <= math.dist(p, q) <= 3.0 * MM:
                        bars.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
            if stripes and all(math.dist(center, o["center"]) > 1.0 * MM for o in out):
                foot = max(bars, key=lambda s: math.dist(s, center)) if bars else center
                out.append({"foot": foot, "axis": _unit(foot, center) if bars else n, "stripes": stripes, "center": center})
    return out


def symbols(path, color="red", page_index=0):
    """mast symbols and Ne 3 boards of one colour: [{"kind": "mast" | "ne3", "foot", "axis", ...}]"""
    test = COLORS[color]
    segs = [(a, b) for a, b, col in strokes(path, page_index) if test(*col)]
    return [{"kind": "mast", **m} for m in mast_symbols(segs)] + [{"kind": "ne3", **n} for n in ne3_boards(segs)]


# ---------------------------------------------------------------- cable troughs (Kabeltiefbau plans)

def point_per_m(massstab):
    """PDF points per meter in the field"""
    return 1000.0 / massstab * MM


def drawing_right(path, page_index=0):
    """x where the drawing ends: the title block and legend on the right side are left out"""
    w, _h, _paths, texts = read_page(path, page_index)
    xs = [box[0] for text, box in texts if re.search(r"Legende|Rückbau|Plankopf|Maßstab", text) and box[0] > 0.6 * w]
    return min(xs) - 10 if xs else w


def km_marks(path, massstab=1000.0, km_range=None, page_index=0):
    """[(x, y, "55,3")]: the hectometer marks of the Strecke, the small circle on the track that belongs
    to each km label. Of several Strecken on the sheet, the longest run of labels 100 m apart counts,
    or the labels within km_range = (from, to). A circle is chosen per label so that the marks lie
    100 m apart on the sheet."""
    _w, _h, paths, texts = read_page(path, page_index)
    circles, straight = set(), {}
    for item in paths:
        segs = item["segs"]
        if segs and all(c for _p, _q, c in segs):
            xs = [p[0] for p, _q, _c in segs]
            ys = [p[1] for p, _q, _c in segs]
            cw, ch = max(xs) - min(xs), max(ys) - min(ys)
            if 2.0 <= cw <= 8.0 and abs(cw - ch) < 0.5:
                circles.add((round((max(xs) + min(xs)) / 2, 1), round((max(ys) + min(ys)) / 2, 1), round(cw, 1)))
        for p, q, curved in segs:
            if not curved and 1.5 < math.dist(p, q) < 4:
                m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                straight.setdefault((int(m[0] // 5), int(m[1] // 5)), []).append(m)
    # a circle with another one about its size around or inside it, or with short rays around it, is
    # another symbol (a full km has a circle 1.8 times as large around the mark)
    circles = {c[:2] for c in circles if c[2] <= 5.0 and not any(
        math.dist(c[:2], o[:2]) < 0.6 and abs(c[2] - o[2]) > 0.3 and o[2] < 1.65 * c[2] for o in circles) and sum(
        2.5 < math.dist(c[:2], m) < 4.5 for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        for m in straight.get((int(c[0] // 5) + dx, int(c[1] // 5) + dy), ())) < 4}
    labels = []
    for text, (x0, y0, x1, y1) in texts:
        if re.fullmatch(r"\d{1,3},\d", text):
            center = ((x0 + x1) / 2, (y0 + y1) / 2)
            near = sorted((math.dist(center, c), c) for c in circles)
            near = [n for n in near if n[0] < 100][:4]
            if near:
                labels.append((float(text.replace(",", ".")), text, near))
    if not labels:
        return []
    # runs of km about 100 m apart: the Strecken on the sheet
    kms = sorted({k for k, _t, _n in labels})
    groups = [[kms[0]]]
    for k in kms[1:]:
        if k - groups[-1][-1] <= 0.55:  # a few labels may be missing
            groups[-1].append(k)
        else:
            groups.append([k])
    # the longest run (within km_range, if given) is the sheet's own Strecke
    inside = [[k for k in g if km_range is None or km_range[0] <= k <= km_range[1]] for g in groups]
    best = max(inside, key=len)
    if not best:
        return []
    km_range = (best[0], best[-1])
    # a circle belongs to the label nearest to it, of all Strecken (one label per km)
    first = {}
    for label in sorted(labels, key=lambda label: label[2][0][0]):
        first.setdefault(label[0], label)
    strecken = {k for g in groups if len(g) >= 3 for k in g}
    owner = {}
    for k, _t, near in first.values():
        for d, c in near:
            if k in strecken:
                owner[c] = min(owner.get(c, 1e9), d)
    labels = [(k, t, [n for n in near if n[0] <= owner.get(n[1], 1e9) + 5])
              for k, t, near in first.values() if km_range[0] <= k <= km_range[1]]
    seen, runs = set(), []
    for k, t, near in sorted(labels, key=lambda label: (label[0], label[2][0][0] if label[2] else 1e9)):
        if near and k not in seen:
            seen.add(k)
            runs.append((k, t, near))
    if not runs:
        return []
    # one circle per label, so that consecutive marks lie 100 m apart (dynamic programming)
    hm = 100 * point_per_m(massstab)
    cost = [[d * 0.05 for d, _c in runs[0][2]]]
    back = [[-1] * len(runs[0][2])]
    for i in range(1, len(runs)):
        step = hm * (runs[i][0] - runs[i - 1][0]) * 10
        row, arg = [], []
        for d, c in runs[i][2]:
            e = [cost[i - 1][j] + min(abs(math.dist(c, p) - step), 60) for j, (_d, p) in enumerate(runs[i - 1][2])]
            j = min(range(len(e)), key=e.__getitem__)
            row.append(e[j] + d * 0.05)
            arg.append(j)
        cost.append(row)
        back.append(arg)
    j = min(range(len(cost[-1])), key=cost[-1].__getitem__)
    pick = [None] * len(runs)
    for i in range(len(runs) - 1, -1, -1):
        pick[i] = runs[i][2][j][1]
        j = back[i][j]
    out = []
    for i, (k, t, _near) in enumerate(runs):
        # drop marks whose spacing to both neighbors is more than 10 points off
        errs = [abs(math.dist(pick[i], pick[n]) - hm * abs(runs[n][0] - k) * 10)
                for n in (i - 1, i + 1) if 0 <= n < len(runs)]
        if not errs or min(errs) <= 10:
            out.append((pick[i][0], pick[i][1], t))
    return out


def simplify(points, tol):
    """Douglas-Peucker: the polyline [(x, y)] with fewer points, within tol"""
    points = [tuple(p) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = points[i], points[j]
        n = math.dist(a, b)
        best, k = 0.0, None
        for m in range(i + 1, j):
            p = points[m]
            d = (abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / n if n > 1e-9
                 else math.dist(p, a))
            if d > best:
                best, k = d, m
        if k is not None and best > tol:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, k in zip(points, keep) if k]


def _colour_segments(path, color, page_index):
    """numpy array (N, 4) x0 y0 x1 y1 of the stroked segments of one colour left of the title block,
    and that x"""
    import numpy as np
    test = COLORS[color]
    right = drawing_right(path, page_index)
    segs = [(p[0], p[1], q[0], q[1]) for p, q, col in strokes(path, page_index, curves=True)
            if test(*col) and max(p[0], q[0]) < right and math.dist(p, q) > 1e-3]
    return np.array(segs, float).reshape(-1, 4), right


def _pair_midpoints(S, gap, min_seg=2.0, ang_tol=4.0, step=0.5, cell=12.0):
    """samples along every segment that has a parallel partner segment gap[0] to gap[1] points away
    (a trough is two parallel lines): midpoints (M, 2) and directions (M, 2)"""
    import numpy as np
    A, D = S[:, :2], S[:, 2:] - S[:, :2]
    L = np.hypot(D[:, 0], D[:, 1])
    U = D / np.maximum(L, 1e-9)[:, None]
    idx = np.nonzero(L >= min_seg)[0]
    if not len(idx):
        return np.zeros((0, 2)), np.zeros((0, 2))
    n = np.maximum(1, (L[idx] / step).astype(int))
    si = np.repeat(idx, n)
    k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    P = A[si] + ((k + 0.5) / np.repeat(n, n))[:, None] * D[si]
    grid = {}
    lo = np.minimum(S[:, :2], S[:, 2:]) - gap[1]
    hi = np.maximum(S[:, :2], S[:, 2:]) + gap[1]
    for i in idx:
        for gx in range(int(lo[i, 0] // cell), int(hi[i, 0] // cell) + 1):
            for gy in range(int(lo[i, 1] // cell), int(hi[i, 1] // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    keys = np.floor(P / cell).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    bounds = np.nonzero(np.any(np.diff(keys[order], axis=0) != 0, axis=1))[0] + 1
    cos_tol = math.cos(math.radians(ang_tol))
    mids, dirs = [], []
    for group in np.split(order, bounds):
        J = np.array(grid.get(tuple(keys[group[0]]), ()), int)
        if len(J) < 2:
            continue
        p = P[group][:, None, :]
        t = ((p - A[J][None]) * U[J][None]).sum(2)
        q = A[J][None] + t[..., None] * U[J][None]
        d = np.sqrt(((p - q) ** 2).sum(2))
        ok = ((t >= -0.2) & (t <= L[J][None] + 0.2) & (np.abs(U[si[group]] @ U[J].T) >= cos_tol)
              & (si[group][:, None] != J[None]) & (d >= gap[0]) & (d <= gap[1]))
        d = np.where(ok, d, np.inf)
        j = d.argmin(1)
        rows = np.nonzero(np.isfinite(d[np.arange(len(group)), j]))[0]
        mids.append((P[group[rows]] + q[rows, j[rows]]) / 2)
        dirs.append(U[si[group[rows]]])
    if not mids:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.concatenate(mids), np.concatenate(dirs)


def _chain(pts, dirs, link=1.6, min_len=10.0, ang_tol=35.0):
    """midpoint samples joined into polylines, following their direction: [[(x, y)]]"""
    import numpy as np
    if not len(pts):
        return []
    _, first = np.unique(np.round(pts / 0.4).astype(np.int64), axis=0, return_index=True)
    first = np.sort(first)
    pts, dirs = pts[first], dirs[first]
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(x // link), int(y // link)), []).append(i)

    def near(i):
        gx, gy = int(pts[i][0] // link), int(pts[i][1] // link)
        return [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((gx + dx, gy + dy), ())]

    used = np.zeros(len(pts), bool)
    cos_tol = math.cos(math.radians(ang_tol))
    chains = []
    for s in np.lexsort((pts[:, 1], pts[:, 0])):
        if used[s]:
            continue
        used[s] = True
        chain = [s]
        for sign in (1, -1):
            cur, d = s, dirs[s] * sign
            while True:
                best, bd = None, link
                for c in near(cur):
                    if used[c]:
                        continue
                    v = pts[c] - pts[cur]
                    n = math.hypot(v[0], v[1])
                    if n > link:
                        continue
                    if n < 1e-6:
                        used[c] = True
                        continue
                    cos = (v[0] * d[0] + v[1] * d[1]) / n
                    if cos < cos_tol:
                        if abs(cos) < 0.5 and n < 0.8:  # beside us: from the other line of the pair
                            used[c] = True
                        continue
                    if n < bd:
                        best, bd = c, n
                if best is None:
                    break
                used[best] = True
                d = (pts[best] - pts[cur]) / bd
                cur = best
                if sign == 1:
                    chain.append(best)
                else:
                    chain.insert(0, best)
        c = pts[chain]
        if len(c) > 1 and np.sqrt((np.diff(c, axis=0) ** 2).sum(1)).sum() >= min_len:
            chains.append([(float(x), float(y)) for x, y in c])
    return chains


def _dashed(S, dash=(2.0, 16.0), max_gap=4.5, off_tol=0.35, ang_tol=3.0, min_dashes=4, min_len=20.0):
    """dashed lines drawn as separate collinear dashes (lengths in points): [[(x0, y0), (x1, y1)]]"""
    import numpy as np
    A, B = S[:, :2], S[:, 2:]
    L = np.sqrt(((B - A) ** 2).sum(1))
    idx = np.nonzero((L >= dash[0]) & (L <= dash[1]))[0]
    if not len(idx):
        return []
    mids = (A[idx] + B[idx]) / 2
    U = (B[idx] - A[idx]) / L[idx, None]
    reach = dash[1] + max_gap
    grid = {}
    for a, (x, y) in enumerate(mids):
        grid.setdefault((int(x // reach), int(y // reach)), []).append(a)
    cos_tol = math.cos(math.radians(ang_tol))
    parent = list(range(len(idx)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a in range(len(idx)):
        gx, gy = int(mids[a][0] // reach), int(mids[a][1] // reach)
        for b in [b for dx in (-1, 0, 1) for dy in (-1, 0, 1) for b in grid.get((gx + dx, gy + dy), ())]:
            if b <= a or abs(U[a] @ U[b]) < cos_tol:
                continue
            v = mids[b] - mids[a]
            if math.hypot(v[0], v[1]) > reach or abs(U[a][0] * v[1] - U[a][1] * v[0]) > off_tol:
                continue
            ia, ib = idx[a], idx[b]
            pb = ((A[ib] - A[ia]) @ U[a], (B[ib] - A[ia]) @ U[a])
            gap = max(min(pb) - L[ia], -max(pb))  # between the two dashes along the line
            if 0.3 <= gap <= max_gap:
                parent[find(a)] = find(b)
    groups = {}
    for a in range(len(idx)):
        groups.setdefault(find(a), []).append(a)
    out = []
    for g in groups.values():
        if len(g) < min_dashes:
            continue
        pts = np.concatenate([A[idx[g]], B[idx[g]]])
        t = (pts - pts[0]) @ U[g[0]]
        p0, p1 = pts[t.argmin()], pts[t.argmax()]
        ln = math.dist(p0, p1)
        # the dashes fill a good part of the line (not scattered strokes of text)
        if ln >= min_len and 0.4 <= L[idx[g]].sum() / ln <= 0.95:
            out.append([(float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))])
    return out


def trough_lines(path, color="red", gap_mm=(0.14, 0.92), page_index=0):
    """Centre lines [[(x, y)]] (PDF points) of the cable troughs of a Kabeltiefbau plan: a trough is
    drawn as two parallel lines gap_mm apart on the paper, a Gleisquerung (crossing under the tracks)
    as a dashed line. Only the drawing left of the title block counts."""
    S, _right = _colour_segments(path, color, page_index)
    if not len(S):
        return []
    mids, dirs = _pair_midpoints(S, (gap_mm[0] * MM, gap_mm[1] * MM))
    return [simplify(c, 0.2) for c in _chain(mids, dirs)] + _dashed(S)


def schacht_marks(path, massstab=1000.0, color="red", page_index=0, min_size=0.8):
    """[(x, y, (short, long))]: Schächte (cable pits) in PDF points with their size in meters. A
    Schacht is a small outlined quad (1 to 8 points a side; filled quads and outlines of a filled
    shape are other symbols). The quad is a symbol, often drawn smaller than the Schacht: each side
    is at least min_size m."""
    _w, _h, paths, _texts = read_page(path, page_index)
    right = drawing_right(path, page_index)
    test = COLORS[color]
    quads, filled = [], []
    for item in paths:
        segs = item["segs"]
        if item["fill"] and test(*item["fill"]) and segs:
            filled.append((sum(p[0] for p, _q, _c in segs) / len(segs), sum(p[1] for p, _q, _c in segs) / len(segs)))
        if item["fill"] or not item["stroke"] or not test(*item["stroke"]) or not 4 <= len(segs) <= 5                 or any(c for _p, _q, c in segs):
            continue
        corners = []
        for p, _q, _c in segs:
            if all(math.dist(p, c) > 0.01 for c in corners):
                corners.append(p)
        if len(corners) != 4:
            continue
        a, b = math.dist(corners[0], corners[1]), math.dist(corners[1], corners[2])
        c, d = math.dist(corners[2], corners[3]), math.dist(corners[3], corners[0])
        diagonals = math.dist(corners[0], corners[2]), math.dist(corners[1], corners[3])
        if abs(a - c) > 0.1 * a or abs(b - d) > 0.1 * b or abs(diagonals[0] - diagonals[1]) > 0.1 * max(diagonals):
            continue  # not a rectangle
        x, y = sum(c[0] for c in corners) / 4, sum(c[1] for c in corners) / 4
        if 1.0 <= a <= 8 and 1.0 <= b <= 8 and x < right:
            quads.append((x, y, a, b))
    out = []
    for x, y, a, b in quads:
        if all(math.dist((x, y), f) > 0.3 for f in filled) and all(math.dist((x, y), o[:2]) > 0.8 for o in out):
            out.append((x, y, tuple(sorted(max(s / point_per_m(massstab), min_size) for s in (a, b)))))
    return out

"""Signal symbols in the vector drawing of a PDF plan (pypdfium2, no bpy).

A Signallageplan draws its symbols as straight strokes. Two patterns are found here, in one
colour (red for the new signals):
- a symbol on a mast: a short bar (the foot) across the end of a longer line (the leader); the
  foot point is the end of the leader, the head lies along the leader;
- a Vorsignalbake (Ne 3): a narrow plate of two parallel lines about 8 mm long with diagonal
  stripes inside and a short support bar at one end.
Coordinates are PDF points, y down from the top of the page (unit 'PDF' of plan_api).
"""

import ctypes
import math

COLORS = {"red": lambda r, g, b: r > 0.6 and g < 0.45 and b < 0.45,
          "blue": lambda r, g, b: b > 0.6 and r < 0.45,
          "black": lambda r, g, b: max(r, g, b) < 0.25}
MM = 72 / 25.4      # PDF points per mm


def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def strokes(path, page_index=0):
    """[((x0, y0), (x1, y1), (r, g, b))]: the straight stroked segments of a page (text left out)"""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    def matrix(obj):
        m = raw.FS_MATRIX()
        return (m.a, m.b, m.c, m.d, m.e, m.f) if raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)) else (1, 0, 0, 1, 0, 0)

    def walk(handle, count, get, parent):
        for i in range(count(handle)):
            obj = get(handle, i)
            kind = raw.FPDFPageObj_GetType(obj)
            m = _mul(matrix(obj), parent)
            if kind == raw.FPDF_PAGEOBJ_FORM:
                yield from walk(obj, raw.FPDFFormObj_CountObjects, raw.FPDFFormObj_GetObject, m)
            elif kind == raw.FPDF_PAGEOBJ_PATH:
                yield obj, m

    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[page_index]
        h = page.get_height()
        out = []
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        x, y = ctypes.c_float(), ctypes.c_float()
        for obj, m in walk(page.raw, raw.FPDFPage_CountObjects, raw.FPDFPage_GetObject, (1, 0, 0, 1, 0, 0)):
            fill, stroke = ctypes.c_int(), ctypes.c_int()
            raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke))
            if not stroke.value:
                continue
            raw.FPDFPageObj_GetStrokeColor(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
            col = (r.value / 255, g.value / 255, b.value / 255)
            prev = start = None
            for i in range(raw.FPDFPath_CountSegments(obj)):
                seg = raw.FPDFPath_GetPathSegment(obj, i)
                raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
                p = (m[0] * x.value + m[2] * y.value + m[4], h - (m[1] * x.value + m[3] * y.value + m[5]))
                kind = raw.FPDFPathSegment_GetType(seg)
                if kind == raw.FPDF_SEGMENT_MOVETO:
                    start = p
                elif kind == raw.FPDF_SEGMENT_LINETO and prev is not None:
                    out.append((prev, p, col))
                if raw.FPDFPathSegment_GetClose(seg) and start is not None and kind == raw.FPDF_SEGMENT_LINETO:
                    out.append((p, start, col))
                prev = p
        return out
    finally:
        pdf.close()


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

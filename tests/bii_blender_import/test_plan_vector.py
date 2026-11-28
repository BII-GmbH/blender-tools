"""Tests for plan_vector: signal symbols in a PDF's vector drawing (pypdfium2, no bpy).

Run: python -m unittest discover tests/bii_blender_import (needs pypdfium2)
"""

import ctypes
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "bii_blender_import"))

try:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
except ImportError:
    raise unittest.SkipTest("pypdfium2 missing")

import plan_vector  # noqa: E402

MM = 72 / 25.4
H = 400.0          # page height, points


def line(page, p, q, rgb):
    """a stroked line; p, q in PDF points with y down"""
    path = raw.FPDFPageObj_CreateNewPath(p[0], H - p[1])
    raw.FPDFPath_LineTo(path, q[0], H - q[1])
    raw.FPDFPageObj_SetStrokeColor(path, *rgb, 255)
    raw.FPDFPageObj_SetStrokeWidth(path, 0.5)
    raw.FPDFPath_SetDrawMode(path, 0, True)
    raw.FPDFPage_InsertObject(page, path)


def make_pdf(path):
    doc = raw.FPDF_CreateNewDocument()
    page = raw.FPDFPage_New(doc, 0, 600.0, H)
    red, black = (230, 0, 0), (0, 0, 0)
    # a signal: foot bar at (100, 300), leader 12 mm up and to the right at 30 degrees
    a = math.radians(30)
    foot = (100.0, 300.0)
    axis = (math.sin(a), -math.cos(a))
    across = (-axis[1], axis[0])
    line(page, (foot[0] - across[0] * 3, foot[1] - across[1] * 3), (foot[0] + across[0] * 3, foot[1] + across[1] * 3), red)
    line(page, foot, (foot[0] + axis[0] * 12 * MM, foot[1] + axis[1] * 12 * MM), red)
    # the same in black: not red, not found
    line(page, (197, 300), (203, 300), black)
    line(page, (200, 300), (200, 300 - 12 * MM), black)
    # a Vorsignalbake: plate 8 x 2.3 mm along x from x=300, two stripes, stem and foot bar at its right end
    x0, x1, y0, y1 = 300.0, 300 + 8 * MM, 200.0, 200 + 2.3 * MM
    line(page, (x0, y0), (x1, y0), red)
    line(page, (x0, y1), (x1, y1), red)
    line(page, (x1, y0), (x1, y1), red)
    for sx in (x0 + 2 * MM, x0 + 4 * MM):
        line(page, (sx, y1), (sx + 2.3 * MM, y0), red)
    ym = (y0 + y1) / 2
    line(page, (x1, ym), (x1 + 1.0 * MM, ym), red)
    line(page, (x1 + 1.0 * MM, ym - 0.75 * MM), (x1 + 1.0 * MM, ym + 0.75 * MM), red)
    raw.FPDFPage_GenerateContent(page)
    pdf = pdfium.PdfDocument(doc)
    pdf.save(path)
    return foot, axis, (x1 + 1.0 * MM, ym)


class PlanVectorTest(unittest.TestCase):
    def test_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "slp.pdf")
            foot, axis, ne3_foot = make_pdf(path)
            found = plan_vector.symbols(path, "red")
            masts = [s for s in found if s["kind"] == "mast"]
            boards = [s for s in found if s["kind"] == "ne3"]
            self.assertEqual(len(masts), 1)
            self.assertLess(math.dist(masts[0]["foot"], foot), 0.5)
            self.assertGreater(masts[0]["axis"][0] * axis[0] + masts[0]["axis"][1] * axis[1], 0.99)
            self.assertEqual(len(boards), 1)
            self.assertEqual(boards[0]["stripes"], 2)
            self.assertLess(math.dist(boards[0]["foot"], ne3_foot), 0.5)
            self.assertEqual(len([s for s in plan_vector.symbols(path, "black") if s["kind"] == "mast"]), 1)

    def test_kabeltiefbau(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ktb.pdf")
            marks = make_ktb_pdf(path)
            found = plan_vector.km_marks(path, massstab=1000)
            self.assertEqual([t for _x, _y, t in found], ["5,2", "5,3", "5,4"])
            for (x, y, _t), m in zip(found, marks):
                self.assertLess(math.dist((x, y), m), 0.2)
            lines = plan_vector.trough_lines(path)
            troughs = [l for l in lines if len(l) > 2 or math.dist(l[0], l[-1]) > 100]
            # the trough with its bend: one line along the middle between the two strokes
            self.assertEqual(len(troughs), 1)
            self.assertEqual(len(lines), 2)  # and the dashed Gleisquerung
            trough = troughs[0]
            self.assertTrue(all(abs(y - (300 + TROUGH_GAP / 2)) < 0.3 for x, y in trough if x < 280))
            self.assertGreater(max(x for x, _y in trough), 395)
            dashed = [l for l in lines if l is not trough][0]
            self.assertLess(math.dist(sorted(dashed, key=lambda p: p[1])[0], (150, 250)), 0.1)
            self.assertLess(math.dist(sorted(dashed, key=lambda p: p[1])[1], (150, 308)), 0.1)
            pits = plan_vector.schacht_marks(path, massstab=1000)
            self.assertEqual(len(pits), 2)  # not the filled quad
            big, small = sorted(pits)
            self.assertLess(math.dist(big[:2], (200, 301)), 0.1)
            self.assertAlmostEqual(big[2][0], 4 / plan_vector.point_per_m(1000), places=3)
            self.assertAlmostEqual(big[2][1], 6 / plan_vector.point_per_m(1000), places=3)
            self.assertEqual(small[2], (0.8, 0.8))  # a small symbol: at least min_size

    def test_classify(self):
        import signal_spec
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "slp.pdf")
            ks, lf, bent, free = make_signals_pdf(path)
            found = {}
            for s in plan_vector.symbols(path, "red"):
                if s["kind"] in ("mast", "free"):
                    signals, reason = plan_vector.classify(path, s["foot"], s["axis"], base=s.get("base"))
                    self.assertTrue(reason)
                    if signals:
                        near = min((ks, lf, bent, free), key=lambda p: math.dist(p, s["foot"]))
                        self.assertLess(math.dist(near, s["foot"]), 0.5)
                        found[near] = s["kind"], signals
            # the head edge of the Ks signal (found as a foot too) and the long line with a wide bar: no signals
            self.assertEqual(len(found), 4)
            self.assertEqual(found[ks], ("mast", [("ks_haupt", {"bedienung": "stellwerk_zug", "kopf": "zs1",
                                                                "zs3": "ohne", "zs6": False, "sh1": False,
                                                                "licht": "oben"})]))
            self.assertEqual(found[lf], ("mast", [("lf6", {"kennziffer": 9}), ("lf7", {"kennziffer": 16})]))
            self.assertEqual(found[bent], ("mast", [("ks_vor", {"bedienung": "stellwerk_zug", "zs3v": "ohne",
                                                                "licht": "ohne"})]))
            self.assertEqual(found[free], ("free", [("ks_mehr", {"bedienung": "stellwerk_zug", "kopf": "ohne",
                                                                 "zs3": "ohne", "zs6": False, "sh1": False,
                                                                 "zs3v": "licht", "licht": "ohne"})]))
            for _kind, signals in found.values():
                for bauform, cfg in signals:
                    self.assertTrue(signal_spec.valid(bauform, signal_spec.config(bauform, **cfg)))


def make_signals_pdf(path):
    """a sheet with masts pointing up the page: a Ks-Hauptsignal (bell, half filled, Zs 1 above, light beside),
    a mast with Lf 6 and Lf 7 (numbers 9 and 16), and a long line with a wide bar (no signal)"""
    doc = raw.FPDF_CreateNewDocument()
    page = raw.FPDFPage_New(doc, 0, 600.0, H)
    red = (230, 0, 0)

    def at(foot, s, t):
        """the point s mm up the mast and t mm to the right of it"""
        return foot[0] + t * MM, foot[1] - s * MM

    def poly(foot, points, fill=False, close=False):
        raw.FPDFPage_InsertObject(page, path_of([at(foot, s, t) for s, t in points], red, fill=fill, close=close))

    def arc(foot, s, t, r, a0, a1):
        """a quarter circle around (s, t), radius r mm, from angle a0 to a1 (degrees from the mast direction)"""
        k, a0, a1 = 0.5523 * r, math.radians(a0), math.radians(a1)
        sign = 1 if a1 > a0 else -1
        p0 = (s + r * math.cos(a0), t + r * math.sin(a0))
        p3 = (s + r * math.cos(a1), t + r * math.sin(a1))
        c1 = (p0[0] - sign * k * math.sin(a0), p0[1] + sign * k * math.cos(a0))
        c2 = (p3[0] + sign * k * math.sin(a1), p3[1] - sign * k * math.cos(a1))
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = (at(foot, *p) for p in (p0, c1, c2, p3))
        obj = raw.FPDFPageObj_CreateNewPath(x0, H - y0)
        raw.FPDFPath_BezierTo(obj, x1, H - y1, x2, H - y2, x3, H - y3)
        raw.FPDFPageObj_SetStrokeColor(obj, *red, 255)
        raw.FPDFPageObj_SetStrokeWidth(obj, 0.3)
        raw.FPDFPath_SetDrawMode(obj, 0, True)
        raw.FPDFPage_InsertObject(page, obj)

    ks = (100.0, 300.0)
    poly(ks, [(0, -1), (0, 1)])                                   # foot bar
    poly(ks, [(0, 0), (7.5, 0)])                                  # mast
    poly(ks, [(7.5, -1.5), (7.5, 1.5)])                           # the head: straight end at the foot
    poly(ks, [(7.5, -1.5), (10, -1.5)])
    poly(ks, [(7.5, 1.5), (10, 1.5)])
    poly(ks, [(7.5, 0), (10, 0), (10, 1.5), (7.5, 1.5)], fill=True, close=True)  # half filled
    arc(ks, 10, 0, 1.5, -90, 0)                                   # rounded at the far end
    arc(ks, 10, 0, 1.5, 0, 90)
    poly(ks, [(12, -1.5), (14.6, 0)])                             # Zs 1: chevron, tip away from the head
    poly(ks, [(12, 1.5), (14.6, 0)])
    circle(page, *at(ks, 11, -2.4), 0.6 * MM, red)              # Zusatzlicht beside the head, near its top
    lf = (250.0, 300.0)
    poly(lf, [(0, -0.8), (0, 0.8)])
    poly(lf, [(0, 0), (4.5, 0)])
    poly(lf, [(7.1, -1.5), (4.5, 0), (7.1, 1.5), (7.1, -1.5)])   # Lf 6: open triangle towards the foot
    poly(lf, [(7.5, -1.5), (11.5, -1.5), (11.5, 1.5), (7.5, 1.5)], close=True)  # Lf 7: upright plate
    text(doc, page, "9", *at(lf, 5.0, 2.2), size=5.0)
    text(doc, page, "16", *at(lf, 8.8, -0.9), size=5.0)
    other = (400.0, 300.0)
    poly(other, [(0, -1.5), (0, 1.5)])
    poly(other, [(0, 0), (20, 0)])
    # a bent mast: foot bar, a short stub, a slanted piece, then the straight mast 2 mm to the side with a
    # Ks-Vorsignal (cup: rounded towards the foot, half filled)
    bent = (480.0, 300.0)
    poly(bent, [(0, -1), (0, 1)])
    poly(bent, [(0, 0), (0.5, 0)])
    poly(bent, [(0.5, 0), (3, 2)])
    poly(bent, [(3, 2), (8, 2)])
    arc(bent, 9.5, 2, 1.5, 90, 180)
    arc(bent, 9.5, 2, 1.5, 180, 270)
    poly(bent, [(9.5, 0.5), (11, 0.5), (11, 3.5), (9.5, 3.5)])
    poly(bent, [(9.5, 2), (11, 2), (11, 3.5), (9.5, 3.5)], fill=True, close=True)
    # a head without foot bar and mast (on a signal bridge): leader, bracket, Zs 3v light, capsule half filled
    free = (550.0, 330.0)
    poly(free, [(0, 0), (3.6, 0)])
    poly(free, [(1, -1.5), (3.6, 0), (1, 1.5)])
    poly(free, [(6.7, -1.5), (4.1, 0), (6.7, 1.5)], fill=True, close=True)
    poly(free, [(8.7, -1.5), (12.7, -1.5)])
    poly(free, [(8.7, 1.5), (12.7, 1.5)])
    poly(free, [(8.7, 0), (12.7, 0), (12.7, 1.5), (8.7, 1.5)], fill=True, close=True)
    for a0 in (90, 180):
        arc(free, 8.7, 0, 1.5, a0, a0 + 90)
    for a0 in (-90, 0):
        arc(free, 12.7, 0, 1.5, a0, a0 + 90)
    raw.FPDFPage_GenerateContent(page)
    pdfium.PdfDocument(doc).save(path)
    return ks, lf, bent, free


TROUGH_GAP = 0.5 * MM   # two parallel lines 0.5 mm apart


def path_of(points, rgb, fill=False, close=False):
    path = raw.FPDFPageObj_CreateNewPath(points[0][0], H - points[0][1])
    for x, y in points[1:]:
        raw.FPDFPath_LineTo(path, x, H - y)
    if close:
        raw.FPDFPath_Close(path)
    raw.FPDFPageObj_SetStrokeColor(path, *rgb, 255)
    raw.FPDFPageObj_SetFillColor(path, *rgb, 255)
    raw.FPDFPageObj_SetStrokeWidth(path, 0.3)
    raw.FPDFPath_SetDrawMode(path, raw.FPDF_FILLMODE_WINDING if fill else 0, True)
    return path


def circle(page, cx, cy, r, rgb):
    k = 0.5523 * r
    path = raw.FPDFPageObj_CreateNewPath(cx + r, H - cy)
    for (c1, c2, e) in (((r, k), (k, r), (0, r)), ((-k, r), (-r, k), (-r, 0)),
                        ((-r, -k), (-k, -r), (0, -r)), ((k, -r), (r, -k), (r, 0))):
        raw.FPDFPath_BezierTo(path, cx + c1[0], H - cy - c1[1], cx + c2[0], H - cy - c2[1], cx + e[0], H - cy - e[1])
    raw.FPDFPath_Close(path)
    raw.FPDFPageObj_SetStrokeColor(path, *rgb, 255)
    raw.FPDFPageObj_SetStrokeWidth(path, 0.3)
    raw.FPDFPath_SetDrawMode(path, 0, True)
    raw.FPDFPage_InsertObject(page, path)


def text(doc, page, s, x, y, size=8.0):
    obj = raw.FPDFPageObj_NewTextObj(doc, b"Helvetica", size)
    codes = list(s.encode("utf-16-le"))
    buf = (ctypes.c_ushort * (len(s) + 1))(*[codes[i] | codes[i + 1] << 8 for i in range(0, len(codes), 2)], 0)
    raw.FPDFText_SetText(obj, buf)
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, H - y)
    raw.FPDFPage_InsertObject(page, obj)


def make_ktb_pdf(path):
    """a Kabeltiefbau sheet at 1:1000: hectometer marks 100 m apart along a track, a trough (two red
    lines 0.5 mm apart with a bend), a dashed red Gleisquerung, two red Schächte and a filled red quad"""
    doc = raw.FPDF_CreateNewDocument()
    page = raw.FPDFPage_New(doc, 0, 900.0, H)
    red, gray, black = (255, 0, 0), (152, 152, 152), (0, 0, 0)
    hm = 100 * plan_vector.point_per_m(1000)
    marks = [(80 + i * hm, 200.0) for i in range(3)]
    raw.FPDFPage_InsertObject(page, path_of([(40, 200), (860, 200)], black))
    for (x, y), km in zip(marks, ("5,2", "5,3", "5,4")):
        circle(page, x, y, 2.2, gray)
        text(doc, page, km, x - 8, y - 20)
    # a circle with rays: another symbol, not a mark
    circle(page, 300, 200, 2.2, gray)
    for a in range(0, 360, 45):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        raw.FPDFPage_InsertObject(page, path_of([(300 + 2.6 * c, 200 + 2.6 * s), (300 + 4.6 * c, 200 + 4.6 * s)], gray))
    for off in (0.0, TROUGH_GAP):
        raw.FPDFPage_InsertObject(page, path_of([(60, 300 + off), (300, 300 + off), (400 - off * 0.4, 360 + off * 0.9)], red))
    # dashes of 4 points with gaps of 2 across the track
    for y in range(250, 310, 6):
        raw.FPDFPage_InsertObject(page, path_of([(150, y), (150, y + 4)], red))
    # Schächte: outlined quads, 6 x 4 and 1.3 x 1.8 points; a filled quad is another symbol
    raw.FPDFPage_InsertObject(page, path_of([(197, 299), (203, 299), (203, 303), (197, 303)], red, close=True))
    raw.FPDFPage_InsertObject(page, path_of([(250, 299), (251.3, 299), (251.3, 300.8), (250, 300.8)], red, close=True))
    raw.FPDFPage_InsertObject(page, path_of([(100, 299), (104, 299), (104, 303), (100, 303)], red, fill=True, close=True))
    raw.FPDFPage_GenerateContent(page)
    pdf = pdfium.PdfDocument(doc)
    pdf.save(path)
    return marks


if __name__ == "__main__":
    unittest.main()

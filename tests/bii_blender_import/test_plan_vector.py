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
            quads = [p for p in pits if p[2]]
            self.assertEqual(len(quads), 1)
            self.assertLess(math.dist(quads[0][:2], (200, 301)), 0.1)
            self.assertAlmostEqual(quads[0][2][0], 4 / plan_vector.point_per_m(1000), places=3)
            self.assertAlmostEqual(quads[0][2][1], 6 / plan_vector.point_per_m(1000), places=3)
            self.assertEqual(len([p for p in pits if p[2] is None]), 2)


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
    lines 0.5 mm apart with a bend), a dashed red Gleisquerung and a red Schacht"""
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
    raw.FPDFPage_InsertObject(page, path_of([(197, 299), (203, 299), (203, 303), (197, 303)], red, fill=True, close=True))
    raw.FPDFPage_GenerateContent(page)
    pdf = pdfium.PdfDocument(doc)
    pdf.save(path)
    return marks


if __name__ == "__main__":
    unittest.main()

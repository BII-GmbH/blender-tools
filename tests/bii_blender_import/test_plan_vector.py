"""Tests for plan_vector: signal symbols in a PDF's vector drawing (pypdfium2, no bpy).

Run: python -m unittest discover tests/bii_blender_import (needs pypdfium2)
"""

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


if __name__ == "__main__":
    unittest.main()

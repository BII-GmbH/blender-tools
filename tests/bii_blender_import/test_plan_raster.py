"""Tests for plan_raster: loading plans and writing their PNG.

Needs numpy, Pillow and pypdfium2, so it runs in Blender's Python with the
extension's wheels, see run_in_blender.py. Skipped without them.
"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins",
                                "bii_blender_import"))

try:
    import numpy as np
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
    from PIL import Image, ImageDraw
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs numpy, Pillow and pypdfium2: {e}")

import plan_raster  # noqa: E402

MM_PER_INCH = 25.4


class _TempDir(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, image, dpi=300):
        png = self.path("out.png")
        kind = plan_raster.write_png(plan_raster.Sheet(image, dpi, dpi), png)
        return kind, Image.open(png)


class WritePngTest(_TempDir):

    def test_transparent_background_becomes_white_paper(self):
        # A CAD export: black lines on a transparent background whose color is black.
        image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        ImageDraw.Draw(image).line((10, 50, 190, 50), fill=(0, 0, 0, 255), width=3)
        kind, png = self.write(image)
        self.assertEqual(kind, "black and white")
        gray = plan_raster.gray_pixels(self.path("out.png"))
        self.assertEqual(gray[10, 10], 255)  # paper
        self.assertEqual(gray[50, 100], 0)  # line
        rgba = png.convert("RGBA")
        self.assertEqual(rgba.getpixel((10, 10))[3], 0)  # paper transparent for Solid view
        self.assertEqual(rgba.getpixel((100, 50))[3], 255)

    def test_half_transparent_color_lies_on_white(self):
        image = Image.new("RGBA", (50, 50), (255, 0, 0, 128))
        kind, png = self.write(image)
        self.assertEqual(kind, "color")
        r, g, b, _ = png.convert("RGBA").getpixel((25, 25))
        self.assertEqual(r, 255)
        self.assertAlmostEqual(g, 127, delta=2)
        self.assertAlmostEqual(b, 127, delta=2)

    def test_color_stays_and_paper_is_transparent(self):
        image = Image.new("RGB", (100, 100), (250, 250, 250))
        ImageDraw.Draw(image).rectangle((40, 40, 60, 60), fill=(200, 30, 30))
        kind, png = self.write(image)
        self.assertEqual(kind, "color")
        rgba = png.convert("RGBA")
        self.assertEqual(rgba.getpixel((50, 50)), (200, 30, 30, 255))
        self.assertEqual(rgba.getpixel((5, 5)), (250, 250, 250, 0))

    def test_gray_rgb_is_stored_as_grayscale(self):
        image = Image.new("RGB", (100, 100), (255, 255, 255))
        ImageDraw.Draw(image).rectangle((10, 10, 90, 90), fill=(120, 120, 120))
        kind, png = self.write(image)
        self.assertEqual(kind, "grayscale")
        self.assertEqual(png.mode, "LA")
        self.assertEqual(png.getpixel((50, 50)), (120, 255))
        self.assertEqual(png.getpixel((2, 2)), (255, 0))

    def test_one_bit_scan_is_one_bit_png(self):
        image = Image.new("1", (300, 200), 1)
        ImageDraw.Draw(image).line((0, 100, 299, 100), fill=0, width=2)
        kind, png = self.write(image)
        self.assertEqual(kind, "black and white")
        self.assertEqual(png.mode, "P")
        self.assertEqual(png.info.get("transparency"), 1)  # index 1, white

    def test_palette_with_transparency(self):
        image = Image.new("P", (40, 40), 0)
        image.putpalette([0, 0, 0, 0, 0, 255] + [0] * 762)
        image.info["transparency"] = 0  # index 0 (black) is the transparent background
        ImageDraw.Draw(image).rectangle((10, 10, 30, 30), fill=1)
        kind, png = self.write(image)
        self.assertEqual(kind, "color")
        rgba = png.convert("RGBA")
        self.assertEqual(rgba.getpixel((2, 2))[:3], (255, 255, 255))
        self.assertEqual(rgba.getpixel((20, 20)), (0, 0, 255, 255))


def _gray_scan_pdf(path, stamp_color=None, scan_dpi=200, size_mm=(100, 80)):
    """A PDF page holding a gray scan, optionally with a colored rectangle drawn over it."""
    w_pt, h_pt = (s / MM_PER_INCH * 72 for s in size_mm)
    px = (round(size_mm[0] / MM_PER_INCH * scan_dpi), round(size_mm[1] / MM_PER_INCH * scan_dpi))
    scan = Image.new("L", px, 255)
    ImageDraw.Draw(scan).line((0, px[1] // 2, px[0], px[1] // 2), fill=0, width=3)
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(w_pt, h_pt)
    image = pdfium.PdfImage.new(pdf)
    image.set_bitmap(pdfium.PdfBitmap.from_pil(scan))
    image.set_matrix(pdfium.PdfMatrix().scale(w_pt, h_pt))
    page.insert_obj(image)
    if stamp_color:
        rect = pdfium_raw.FPDFPageObj_CreateNewRect(10, 10, 30, 30)
        pdfium_raw.FPDFPageObj_SetFillColor(rect, *stamp_color, 255)
        pdfium_raw.FPDFPath_SetDrawMode(rect, pdfium_raw.FPDF_FILLMODE_ALTERNATE, False)
        pdfium_raw.FPDFPage_InsertObject(page.raw, rect)
    page.gen_content()
    pdf.save(path)
    pdf.close()


class LoadRasterTest(_TempDir):

    def test_missing_resolution_is_a_warning(self):
        Image.new("L", (40, 30), 255).save(self.path("plan.png"))
        sheet = plan_raster.load(self.path("plan.png"))
        self.assertTrue(sheet.warning)
        self.assertEqual(sheet.dpi_x, 300.0)

    def test_resolution_in_file_is_no_warning(self):
        Image.new("L", (40, 30), 255).save(self.path("plan.png"), dpi=(200, 200))
        sheet = plan_raster.load(self.path("plan.png"))
        self.assertFalse(sheet.warning)
        self.assertAlmostEqual(sheet.dpi_x, 200, delta=0.01)

    def test_pillow_limit_is_restored(self):
        # The decompression bomb limit is Pillow's global setting, shared with
        # other add-ons: lifted only while a plan is opened.
        Image.new("L", (40, 30), 255).save(self.path("plan.png"), dpi=(200, 200))
        limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 100  # smaller than the plan
        try:
            plan_raster.load(self.path("plan.png"))
            plan_raster.gray_pixels(self.path("plan.png"))
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 100)
        finally:
            Image.MAX_IMAGE_PIXELS = limit


class LoadPdfTest(_TempDir):

    def test_scan_resolution(self):
        _gray_scan_pdf(self.path("scan.pdf"))
        sheet = plan_raster.load(self.path("scan.pdf"))
        self.assertAlmostEqual(sheet.dpi_x, 200, delta=0.5)
        self.assertAlmostEqual(sheet.width_m, 0.100, delta=1e-4)
        self.assertAlmostEqual(sheet.height_m, 0.080, delta=1e-4)
        self.assertIn("scanned at 200 dpi", sheet.note)

    def test_colored_stamp_on_gray_scan_keeps_its_color(self):
        _gray_scan_pdf(self.path("stamp.pdf"), stamp_color=(255, 0, 0))
        sheet = plan_raster.load(self.path("stamp.pdf"))
        # The rectangle is at 10..40 pt from the lower left corner.
        x = round(25 / 72 * sheet.dpi_x)
        y = sheet.image.height - 1 - round(25 / 72 * sheet.dpi_y)
        r, g, b = sheet.image.convert("RGB").getpixel((x, y))
        self.assertGreater(r, 200)
        self.assertLess(g, 60)
        self.assertEqual(plan_raster.write_png(sheet, self.path("stamp.png")), "color")

    def test_large_page_is_limited_to_max_side(self):
        pdf = pdfium.PdfDocument.new()
        pdf.new_page(2000 / MM_PER_INCH * 72, 30 / MM_PER_INCH * 72)  # 2 m wide, 300 dpi
        pdf.save(self.path("wide.pdf"))
        pdf.close()
        sheet = plan_raster.load(self.path("wide.pdf"))
        self.assertLessEqual(sheet.image.width, plan_raster.MAX_SIDE)
        self.assertGreater(sheet.image.width, plan_raster.MAX_SIDE - 2)
        self.assertAlmostEqual(sheet.width_m, 2.0, delta=1e-4)  # the true size stays

    def test_missing_page(self):
        _gray_scan_pdf(self.path("scan.pdf"))
        with self.assertRaises(ValueError):
            plan_raster.load(self.path("scan.pdf"), page_index=1)


class FindMarkCenterTest(unittest.TestCase):

    def test_cross_center(self):
        gray = np.full((200, 200), 255, np.uint8)
        gray[100, 80:121] = 0
        gray[80:121, 100] = 0
        cx, cy = plan_raster.find_mark_center(gray, 92.0, 107.0, 40)
        self.assertAlmostEqual(cx, 100.5, delta=0.6)
        self.assertAlmostEqual(cy, 100.5, delta=0.6)

    def test_line_through_window_is_not_a_cross(self):
        gray = np.full((200, 200), 255, np.uint8)
        gray[100, :] = 0
        self.assertIsNone(plan_raster.find_mark_center(gray, 100.0, 100.0, 20))


if __name__ == "__main__":
    unittest.main()

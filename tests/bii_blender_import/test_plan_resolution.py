"""Tests of changing a plan's resolution after import, inside Blender.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import os
import shutil
import tempfile
import unittest

try:
    import bpy
    from PIL import Image, ImageDraw
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender and Pillow: {e}")

import bii_blender_import  # noqa: E402  (run_in_blender.py puts plugins/ on sys.path)
from bii_blender_import import import_plan  # noqa: E402

DPI = 400
SIZE = (4000, 3000)


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


class ResolutionTest(unittest.TestCase):
    def setUp(self):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        self.folder = tempfile.mkdtemp()
        self.source = os.path.join(self.folder, "plan.tif")
        image = Image.new("L", SIZE, 255)
        draw = ImageDraw.Draw(image)
        for x in range(100, SIZE[0], 200):
            draw.line([(x, 0), (x, SIZE[1])], fill=0, width=3)
        image.save(self.source, dpi=(DPI, DPI))
        bpy.ops.import_image.bii_plan(filepath=self.source)
        self.obj = bpy.context.object
        pg = self.obj.bii_plan
        import_plan.set_sheet_rectangle(self.obj, 0.01, 0.01, pg.width / 2, pg.height / 2)
        p = pg.points.add()
        p.x, p.y = 0.02, 0.03
        self.obj.location = (5.0, 6.0, 0.0)
        self.verts = [tuple(v.co) for v in self.obj.data.vertices]
        self.width, self.height = pg.width, pg.height

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def lower(self, long_side):
        bpy.context.view_layer.objects.active = self.obj
        self.obj.select_set(True)
        return bpy.ops.bii_plan.set_resolution(resolution=str(long_side))

    def test_lower_keeps_sheet_crop_points_place(self):
        pg = self.obj.bii_plan
        old_name = pg.image.name
        self.assertEqual(self.lower(1024), {'FINISHED'})
        self.assertEqual(max(pg.image.size), 1024)
        self.assertNotIn(old_name, bpy.data.images)  # freed
        self.assertAlmostEqual(pg.width, self.width)
        self.assertAlmostEqual(pg.height, self.height)
        self.assertAlmostEqual(pg.dpi, DPI * 1024 / SIZE[0], places=3)
        self.assertEqual([tuple(v.co) for v in self.obj.data.vertices], self.verts)
        self.assertEqual(len(pg.points), 1)
        self.assertEqual(tuple(self.obj.location), (5.0, 6.0, 0.0))
        tex = [n for n in pg.material.node_tree.nodes if n.type == 'TEX_IMAGE'][0]
        self.assertEqual(tex.image, pg.image)

    def test_back_up_from_original(self):
        pg = self.obj.bii_plan
        self.lower(1024)
        self.assertEqual(self.lower(2048), {'FINISHED'})
        self.assertEqual(max(pg.image.size), 2048)

    def test_without_original_uses_current_image(self):
        pg = self.obj.bii_plan
        self.lower(2048)
        os.remove(self.source)
        self.assertEqual(self.lower(1024), {'FINISHED'})
        self.assertEqual(max(pg.image.size), 1024)
        self.assertAlmostEqual(pg.width, self.width)

    def test_copies_share_the_new_image(self):
        pg = self.obj.bii_plan
        copy = self.obj.copy()
        copy.data = self.obj.data.copy()
        bpy.context.collection.objects.link(copy)
        self.lower(1024)
        self.assertEqual(copy.bii_plan.image, pg.image)

    def test_import_option(self):
        bpy.ops.import_image.bii_plan(filepath=self.source, resolution='2048')
        pg = bpy.context.object.bii_plan
        self.assertEqual(max(pg.image.size), 2048)
        self.assertAlmostEqual(pg.width, self.width)


if __name__ == "__main__":
    unittest.main()

"""Tests of the georeference operators inside Blender: snapping, Place in Scene,
guesses after a change of the coordinate system, Align to Scene.

The test plan is a sheet with four grid crosses of a 50 m grid at 1:1000,
turned 30° against the sheet.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import math
import os
import tempfile
import unittest

try:
    import bpy
    from mathutils import Vector
    from PIL import Image, ImageDraw
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender and Pillow: {e}")

import bii_blender_import  # noqa: E402  (run_in_blender.py puts plugins/ on sys.path)
from bii_blender_import import import_plan, plan_geo, plan_georef  # noqa: E402

DPI = 300
SIZE = (1800, 1200)  # 152.4 x 101.6 mm
BASE = (0.04, 0.02)  # sheet meters of the cross at E0 / N0
E0, N0 = 3588400, 5520000
STEP = 50  # meters between crosses
ANGLE = math.radians(30)  # east on the plan, against the sheet's x axis


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


def cross_sheet(i, j):
    """Sheet meters of the cross at E0 + i * STEP, N0 + j * STEP."""
    d = STEP / 1000  # 1:1000
    c, s = math.cos(ANGLE), math.sin(ANGLE)
    return BASE[0] + d * (c * i - s * j), BASE[1] + d * (s * i + c * j)


CROSSES = [(i, j) for i in (0, 1) for j in (0, 1)]


class GeoreferenceOperatorTest(unittest.TestCase):

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = os.path.join(self._dir.name, "Lageplan.png")
        image = Image.new("L", SIZE, 255)
        draw = ImageDraw.Draw(image)
        height_m = SIZE[1] / DPI * 0.0254
        for i, j in CROSSES:
            x, y = cross_sheet(i, j)
            px, py = x / 0.0254 * DPI, (height_m - y) / 0.0254 * DPI
            draw.line((px - 15, py, px + 15, py), fill=0, width=3)
            draw.line((px, py - 15, px, py + 15), fill=0, width=3)
        image.save(path, dpi=(DPI, DPI))
        bpy.ops.import_image.bii_plan(filepath=path, massstab=1000)
        self.obj = bpy.context.view_layer.objects.active
        self.pg = self.obj.bii_plan

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir.cleanup()

    def add_crosses(self):
        """Points clicked 0.4 mm off each cross, snapped, with their values read."""
        for i, j in CROSSES:
            x, y = cross_sheet(i, j)
            snapped = plan_georef.snap_to_cross(self.pg, x + 0.0004, y - 0.0003)
            self.assertIsNotNone(snapped)
            p = self.pg.points.add()
            p.x, p.y = snapped
            import_plan.set_point_value(p, "easting", E0 + STEP * i, 'READ')
            import_plan.set_point_value(p, "northing", N0 + STEP * j, 'READ')

    def test_snap_finds_the_cross_center(self):
        x, y = cross_sheet(1, 0)
        sx, sy = plan_georef.snap_to_cross(self.pg, x + 0.0004, y - 0.0003)
        pixel = 0.0254 / DPI
        self.assertLess(math.hypot(sx - x, sy - y), pixel)

    def test_place_in_scene_puts_the_crosses_at_their_coordinates(self):
        self.add_crosses()
        self.assertEqual(bpy.ops.bii_plan.georeference(), {'FINISHED'})
        scene = bpy.context.scene
        self.assertIn("lat", scene)  # no origin before: the plan sets it
        proj = plan_geo.SceneProjection(scene["lat"], scene["lon"])
        bpy.context.view_layer.update()
        for i, j in CROSSES:
            want = proj.from_geographic(*plan_geo.grid_to_wgs84(
                'DB_REF', E0 + STEP * i, N0 + STEP * j))
            got = self.obj.matrix_world @ Vector((*cross_sheet(i, j), 0.0))
            self.assertLess(math.hypot(got.x - want[0], got.y - want[1]), 0.2, (i, j))
        self.assertAlmostEqual(self.pg.massstab, 1000, delta=5)

    def test_system_change_takes_guesses_again(self):
        self.add_crosses()
        bpy.ops.bii_plan.georeference()
        bpy.context.view_layer.update()
        p = self.pg.points.add()
        p.x, p.y = 0.1, 0.05
        self.assertTrue(plan_georef.estimate_from_scene(bpy.context.scene, self.obj, self.pg, p))
        self.assertEqual(p.easting_state, 'GUESSED')
        self.assertTrue(3e6 < p.easting < 4e6)  # Gauß-Krüger zone 3
        self.pg.crs = 'ETRS89'
        # No UTM easting read yet: the guess is a 6-digit easting in the zone setting.
        self.assertEqual(p.easting_state, 'GUESSED')
        self.assertTrue(1e5 < p.easting < 1e6, p.easting)
        self.assertEqual(self.pg.points[0].easting, E0)  # read values stay

    def test_align_to_scene(self):
        targets = [Vector((10.0, 20.0, 0.0)), Vector((10.0, 120.0, 0.0))]
        for (x, y), target in zip(((0.0, 0.0), (0.1, 0.0)), targets):
            pair = self.pg.pairs.add()
            pair.x, pair.y = x, y
            pair.target = target
        self.assertEqual(bpy.ops.bii_plan.align(), {'FINISHED'})
        bpy.context.view_layer.update()
        a = self.obj.matrix_world @ Vector((0.0, 0.0, 0.0))
        b = self.obj.matrix_world @ Vector((0.1, 0.0, 0.0))
        self.assertLess((a - targets[0]).xy.length, 1e-3)
        self.assertLess((b - targets[1]).xy.length, 1e-3)  # 100 m apart, Maßstab kept


if __name__ == "__main__":
    unittest.main()

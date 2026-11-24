"""Tests of the grid functions of plan_api inside Blender: Find Grid, Fill from Grid,
place_from_grid, and importing a turned scan.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import math
import os
import sys
import tempfile
import unittest

try:
    import bpy
    import numpy as np
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender and Pillow: {e}")

import bii_blender_import  # noqa: E402
from bii_blender_import import plan_api  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from test_plan_grid import DPI, sheet  # noqa: E402

E0, N0 = 3589000, 5546000      # values of the lattice node (0, 0)


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


class GridBlenderTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        img, self.a, self.b, self.o = sheet(20.0)
        self.path = os.path.join(self._dir.name, "Grid Plan.png")
        Image.fromarray(img).save(self.path, dpi=(DPI, DPI))
        self.img = img

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir.cleanup()

    def node_values(self, i, j):
        # eastings grow along a, northings against b (b points down-right in the image): not mirrored
        return E0 + 100 * i, N0 - 100 * j

    def test_find_and_fill(self):
        bpy.ops.import_image.bii_plan(filepath=self.path, massstab=1000, find_grid=True)
        plan = bpy.context.view_layer.objects.active
        pg = plan.bii_plan
        g = plan_api.grid(plan)
        self.assertIsNotNone(g)
        self.assertEqual(g["spacing_m"], 100.0)
        self.assertEqual(len(pg.points), len(g["crosses"]))
        self.assertGreaterEqual(len(pg.points), 12)
        # which lattice direction of the stored grid is our a? enter values at two crosses
        found = g["crosses"]
        M = np.column_stack([self.a, self.b])

        def truth(c):
            u, v = plan_api.from_sheet(plan, c["x"], c["y"], 'SOURCE')
            i, j = np.round(np.linalg.solve(M, np.array([u, v]) - self.o))
            return self.node_values(int(i), int(j))
        typed = [0, len(found) // 2, len(found) - 1]
        from bii_blender_import import import_plan
        for k in typed[:2]:
            e, n = truth(found[k])
            import_plan.set_point_value(pg.points[k], "easting", e, 'READ')
            import_plan.set_point_value(pg.points[k], "northing", n, 'READ')
        try:
            plan_api.fill_from_grid(plan)
        except ValueError:
            # two crosses in one grid line leave it open: a third one decides
            e, n = truth(found[typed[2]])
            import_plan.set_point_value(pg.points[typed[2]], "easting", e, 'READ')
            import_plan.set_point_value(pg.points[typed[2]], "northing", n, 'READ')
            plan_api.fill_from_grid(plan)
        for p, c in zip(pg.points, found):
            self.assertEqual((p.easting, p.northing), truth(c), p.name)
        self.assertEqual(bpy.ops.bii_plan.georeference(), {'FINISHED'})

    def test_place_from_readings(self):
        bpy.ops.import_image.bii_plan(filepath=self.path, massstab=1000)
        plan = bpy.context.view_layer.objects.active
        self.assertIsNotNone(plan_api.find_grid(plan))
        g = plan_api.grid(plan)
        M = np.column_stack([self.a, self.b])
        # the line families of the stored grid against ours: read the values of 4 crosses
        rows = []
        for c in g["crosses"]:
            u, v = plan_api.from_sheet(plan, c["x"], c["y"], 'SOURCE')
            i, j = np.round(np.linalg.solve(M, np.array([u, v]) - self.o))
            e, n = self.node_values(int(i), int(j))
            rows.append((c["i"], c["j"], e, n))
        # tell lines by the stored grid's indices: eastings are constant along one family
        by_i = {}
        for i, j, e, n in rows:
            by_i.setdefault(i, set()).add(e)
        e_on_i = all(len(v) == 1 for v in by_i.values())
        readings = []
        for i, j, e, n in rows:
            readings += [("i", i, e if e_on_i else n), ("j", j, n if e_on_i else e)]
        readings.append(("i", rows[0][0], 1234567.0))       # a misread
        scale, rms, worst, warnings = plan_api.place_from_grid(plan, readings)
        self.assertAlmostEqual(scale, 1000, delta=2)
        self.assertLess(rms, 0.2)
        self.assertTrue(any("misread" in w for w in warnings))

    def test_turned_import_keeps_source_pixels(self):
        turned = os.path.join(self._dir.name, "Turned.png")
        Image.fromarray(self.img).transpose(Image.ROTATE_90).save(turned, dpi=(DPI, DPI))  # lies on its side
        bpy.ops.import_image.bii_plan(filepath=turned, massstab=1000, rotation='90')
        plan = bpy.context.view_layer.objects.active
        pg = plan.bii_plan
        h, w = self.img.shape
        self.assertGreater(pg.width, pg.height)                # upright again
        self.assertAlmostEqual(pg.width, w / DPI * 0.0254, places=4)
        # a point of the turned file: (u, v) in the original image is (v, w - u) in the turned file
        u, v = 300.0, 200.0
        x, y = plan_api.to_sheet(plan, v, w - u, 'SOURCE')
        self.assertAlmostEqual(x, u / DPI * 0.0254, places=5)
        self.assertAlmostEqual(y, pg.height - v / DPI * 0.0254, places=5)
        back = plan_api.from_sheet(plan, x, y, 'SOURCE')
        self.assertAlmostEqual(back[0], v, places=3)
        self.assertAlmostEqual(back[1], w - u, places=3)
        g = plan_api.find_grid(plan, add_points=False)
        self.assertIsNotNone(g)
        self.assertAlmostEqual(g["angle"] % 90, 20.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()

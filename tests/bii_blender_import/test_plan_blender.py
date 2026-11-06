"""Tests of the plan import inside Blender: import, copies, outline, point values.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import os
import tempfile
import unittest

try:
    import bpy
    from PIL import Image, ImageDraw
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender and Pillow: {e}")

import bii_blender_import  # noqa: E402  (run_in_blender.py puts plugins/ on sys.path)
from bii_blender_import import import_plan  # noqa: E402


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


class PlanImportTest(unittest.TestCase):

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.png = os.path.join(self._dir.name, "Lageplan.png")
        image = Image.new("RGB", (600, 400), (255, 255, 255))
        ImageDraw.Draw(image).line((0, 200, 599, 200), fill=(0, 0, 0), width=3)
        image.save(self.png, dpi=(300, 300))
        bpy.ops.import_image.bii_plan(filepath=self.png, massstab=1000)
        self.plan = bpy.context.view_layer.objects.active

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir.cleanup()

    def test_true_size(self):
        pg = self.plan.bii_plan
        self.assertTrue(pg.is_plan)
        self.assertAlmostEqual(pg.width, 600 / 300 * 0.0254, places=6)
        self.assertAlmostEqual(pg.massstab, 1000)
        self.assertEqual(pg.background, 0.0)
        self.assertEqual(pg.image.alpha_mode, 'STRAIGHT')

    def test_modifier_sets_uv_from_positions(self):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = self.plan.evaluated_get(depsgraph).to_mesh()
        try:
            uv = mesh.attributes["UVMap"]
            corner = max(range(len(mesh.loops)), key=lambda i: sum(uv.data[i].vector))
            self.assertAlmostEqual(uv.data[corner].vector[0], 1.0, places=5)
            self.assertAlmostEqual(uv.data[corner].vector[1], 1.0, places=5)
            self.assertEqual(mesh.materials[0].original, self.plan.bii_plan.material)
        finally:
            self.plan.evaluated_get(depsgraph).to_mesh_clear()

    def test_copy_gets_its_own_material_on_change(self):
        bpy.ops.object.duplicate()
        copy = bpy.context.view_layer.objects.active
        self.assertIsNot(copy, self.plan)
        self.assertEqual(copy.bii_plan.material, self.plan.bii_plan.material)
        copy.bii_plan.opacity = 0.3
        original_mat, copy_mat = self.plan.bii_plan.material, copy.bii_plan.material
        self.assertNotEqual(copy_mat, original_mat)
        self.assertAlmostEqual(
            original_mat.node_tree.nodes[import_plan.OPACITY_NODE].outputs[0].default_value, 1.0)
        self.assertAlmostEqual(
            copy_mat.node_tree.nodes[import_plan.OPACITY_NODE].outputs[0].default_value, 0.3)
        self.assertAlmostEqual(original_mat.diffuse_color[3], 1.0)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = copy.evaluated_get(depsgraph).to_mesh()
        try:
            self.assertEqual(mesh.materials[0].original, copy_mat)
        finally:
            copy.evaluated_get(depsgraph).to_mesh_clear()

    def test_set_origin_is_detected(self):
        self.assertFalse(import_plan.outline_off_sheet(self.plan))
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
        self.assertTrue(import_plan.outline_off_sheet(self.plan))
        bpy.ops.bii_plan.crop_reset()
        self.assertFalse(import_plan.outline_off_sheet(self.plan))

    def test_crop_inside_sheet_is_fine(self):
        pg = self.plan.bii_plan
        import_plan.set_sheet_rectangle(self.plan, 0.01, 0.01, pg.width / 2, pg.height / 2)
        self.assertFalse(import_plan.outline_off_sheet(self.plan))

    def test_point_value_from_code_keeps_guess_flag(self):
        p = self.plan.bii_plan.points.add()
        import_plan.set_point_value(p, "easting", 3588400, True)
        import_plan.set_point_value(p, "northing", 5520000, False)
        self.assertTrue(p.easting_guessed)
        self.assertFalse(p.northing_guessed)
        p.easting = 3588500  # as typed in the sidebar: read from the plan
        self.assertFalse(p.easting_guessed)


if __name__ == "__main__":
    unittest.main()

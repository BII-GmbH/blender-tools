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
from bii_blender_import import import_plan, plan_geo, plan_georef  # noqa: E402


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

    def test_point_value_from_code_keeps_its_state(self):
        p = self.plan.bii_plan.points.add()
        self.assertEqual((p.easting_state, p.northing_state), ('EMPTY', 'EMPTY'))
        import_plan.set_point_value(p, "easting", 3588400, 'GUESSED')
        import_plan.set_point_value(p, "northing", 5520000, 'READ')
        self.assertTrue(p.easting_guessed)
        self.assertEqual(p.northing_state, 'READ')
        p.easting = 3588500  # as typed in the sidebar: read from the plan
        self.assertEqual(p.easting_state, 'READ')
        p.easting = 0  # cleared
        self.assertEqual(p.easting_state, 'EMPTY')

    def test_question_toggle(self):
        p = self.plan.bii_plan.points.add()
        p.easting_guessed = True  # an empty value stays empty
        self.assertEqual(p.easting_state, 'EMPTY')
        import_plan.set_point_value(p, "easting", 3588400, 'GUESSED')
        p.easting_guessed = False  # confirmed as read from the plan
        self.assertEqual(p.easting_state, 'READ')
        p.easting_guessed = True
        self.assertEqual(p.easting_state, 'GUESSED')


# A 100 m grid at 1:1000, so 0.1 m on the sheet between crosses.
def _cross(x_steps, y_steps):
    return 0.01 + 0.1 * x_steps, 0.01 + 0.1 * y_steps, 3588000 + 100 * x_steps, \
        5520000 + 100 * y_steps


class GeoreferenceTest(unittest.TestCase):
    """The point values behind Georeference, on an object without image."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_plane_add()
        self.pg = bpy.context.object.bii_plan
        self.pg.is_plan = True
        self.pg.width, self.pg.height = 0.6, 0.4

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def add(self, x_steps, y_steps, easting='READ', northing='READ'):
        x, y, e, n = _cross(x_steps, y_steps)
        p = self.pg.points.add()
        p.x, p.y = x, y
        if easting:
            import_plan.set_point_value(p, "easting", e, easting)
        if northing:
            import_plan.set_point_value(p, "northing", n, northing)
        return p

    def test_half_entered_point_does_not_pull_the_fit(self):
        # Hochwert of P3 not typed yet: its 0 must not count as a value.
        self.add(0, 0)
        self.add(5, 3)
        p3 = self.add(2, 1, northing=None)
        fit = plan_georef.sheet_fit(self.pg)
        self.assertAlmostEqual(plan_geo.similarity_scale(fit), 1000, places=3)
        self.assertLess(p3.residual, 1e-3)

    def test_prediction_is_a_guess_on_the_finer_grid(self):
        # Crosses read 500 m apart suggest a 500 m grid; the grid is 100 m.
        self.add(0, 0)
        self.add(5, 5)
        self.assertEqual(plan_georef.grid_spacing(self.pg), 500)
        x, y, e, n = _cross(2, 3)
        self.assertEqual(plan_georef.predict_values(self.pg, x, y), (e, n))

    def test_predicted_points_count_as_guesses(self):
        self.add(0, 0)
        self.add(5, 3)
        p = self.add(2, 1, easting='GUESSED', northing='GUESSED')
        self.assertEqual(p.residual, -1.0)  # guesses get no residual
        self.assertEqual(plan_georef.read_values(self.pg, "easting"), [3588000, 3588500])

    def test_rechtswerte_in_a_row_leave_rotation_to_the_guesses(self):
        self.add(0, 0, northing='GUESSED')
        self.add(5, 0, northing='GUESSED')
        warnings = plan_georef.fit_summary(self.pg)[3]
        self.assertTrue(any("do not fix" in w for w in warnings), warnings)
        self.add(2, 3, northing='GUESSED')
        warnings = plan_georef.fit_summary(self.pg)[3]
        self.assertFalse(any("do not fix" in w for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()

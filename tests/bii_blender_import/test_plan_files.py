"""Tests of the paths to a plan's original and image, Relink and the panel's plan.

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
from bii_blender_import import import_plan, plan_georef  # noqa: E402

DPI = 300
SIZE = (900, 600)


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


def write_plan(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = Image.new("RGB", SIZE, (255, 255, 255))
    ImageDraw.Draw(image).line((0, 300, 899, 300), fill=(0, 0, 0), width=3)
    image.save(path, dpi=(DPI, DPI))


class PlanFilesTest(unittest.TestCase):

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.root = tempfile.mkdtemp()
        self.project = os.path.join(self.root, "project")
        os.makedirs(self.project)
        self.blend = os.path.join(self.project, "scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=self.blend)

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def import_plan(self, path):
        bpy.ops.import_image.bii_plan(filepath=path, massstab=1000)
        return bpy.context.view_layer.objects.active

    def test_stored_path(self):
        inside = os.path.join(self.project, "pdf", "a.pdf")
        self.assertEqual(import_plan.stored_path(inside), "//pdf/a.pdf")
        outside = os.path.join(self.root, "a.pdf")
        self.assertEqual(import_plan.stored_path(outside), os.path.normpath(outside))
        self.assertEqual(import_plan.stored_path(outside, ""), os.path.normpath(outside))
        self.assertEqual(import_plan.absolute_path("//pdf/a.pdf"), os.path.normpath(inside))

    def test_source_below_blend_is_relative(self):
        path = os.path.join(self.project, "Pläne", "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        self.assertEqual(pg.source, "//Pläne/Lageplan.tif")
        self.assertEqual(import_plan.absolute_path(pg.source), os.path.normpath(path))

    def test_source_elsewhere_is_absolute(self):
        path = os.path.join(self.root, "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        self.assertEqual(pg.source, os.path.normpath(path))

    def test_typed_source_becomes_relative(self):
        path = os.path.join(self.root, "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        pg.source = os.path.join(self.project, "x", "Lageplan.tif")
        self.assertEqual(pg.source, "//x/Lageplan.tif")

    def test_save_as_elsewhere_keeps_the_file(self):
        path = os.path.join(self.project, "pdf", "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        other = os.path.join(self.root, "other", "scene.blend")
        os.makedirs(os.path.dirname(other))
        bpy.ops.wm.save_as_mainfile(filepath=other)
        self.assertEqual(pg.source, os.path.normpath(path))  # no longer below the .blend
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(self.root, "scene.blend"))
        self.assertEqual(pg.source, "//project/pdf/Lageplan.tif")

    def test_save_copy_keeps_the_open_file_paths(self):
        path = os.path.join(self.project, "pdf", "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        copy = os.path.join(self.root, "copy.blend")
        bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)
        self.assertEqual(pg.source, "//pdf/Lageplan.tif")
        bpy.ops.wm.open_mainfile(filepath=copy)
        obj = next(o for o in bpy.data.objects if o.bii_plan.is_plan)
        self.assertEqual(obj.bii_plan.source, "//project/pdf/Lageplan.tif")

    def test_first_save_makes_source_relative(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        path = os.path.join(self.project, "pdf", "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        self.assertEqual(pg.source, os.path.normpath(path))  # unsaved: absolute
        bpy.ops.wm.save_as_mainfile(filepath=self.blend)
        self.assertEqual(pg.source, "//pdf/Lageplan.tif")

    def test_relink_finds_original_and_writes_missing_image(self):
        path = os.path.join(self.root, "old", "Lageplan.tif")
        write_plan(path)
        obj = self.import_plan(path)
        pg = obj.bii_plan
        size = tuple(pg.image.size)
        png = import_plan.absolute_path(pg.image.filepath)
        bpy.ops.wm.save_mainfile()
        # The .blend file on another PC: no image, the original elsewhere.
        os.remove(png)
        moved = os.path.join(self.project, "originals", "sub", "Lageplan.tif")
        os.makedirs(os.path.dirname(moved))
        shutil.move(path, moved)
        bpy.ops.wm.open_mainfile(filepath=self.blend)
        obj = next(o for o in bpy.data.objects if o.bii_plan.is_plan)
        pg = obj.bii_plan
        self.assertTrue(import_plan.texture_missing(pg.image))
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        result = bpy.ops.bii_plan.relink(directory=os.path.join(self.project, "originals"))
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(pg.source, "//originals/sub/Lageplan.tif")
        self.assertFalse(import_plan.texture_missing(pg.image))
        self.assertTrue(os.path.isfile(png))
        self.assertEqual(tuple(pg.image.size), size)

    def test_change_resolution_with_missing_image(self):
        path = os.path.join(self.project, "Lageplan.tif")
        write_plan(path)
        obj = self.import_plan(path)
        png = import_plan.absolute_path(obj.bii_plan.image.filepath)
        bpy.ops.wm.save_mainfile()
        os.remove(png)
        bpy.ops.wm.open_mainfile(filepath=self.blend)
        obj = next(o for o in bpy.data.objects if o.bii_plan.is_plan)
        self.assertEqual(tuple(obj.bii_plan.image.size), (0, 0))
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        self.assertEqual(bpy.ops.bii_plan.set_resolution(resolution='1024'), {'FINISHED'})
        self.assertEqual(tuple(obj.bii_plan.image.size), SIZE)  # from the original

    def test_relink_without_original_fails(self):
        path = os.path.join(self.root, "Lageplan.tif")
        write_plan(path)
        obj = self.import_plan(path)
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        os.remove(path)
        obj.select_set(True)
        self.assertEqual(bpy.ops.bii_plan.relink(directory=empty), {'CANCELLED'})

    def test_new_plan_background_is_one(self):
        path = os.path.join(self.root, "Lageplan.tif")
        write_plan(path)
        pg = self.import_plan(path).bii_plan
        self.assertEqual(pg.background, 1.0)
        bpy.ops.import_image.bii_plan(filepath=path, background=0.25)
        self.assertAlmostEqual(bpy.context.object.bii_plan.background, 0.25)

    def test_unselected_active_plan_is_no_plan(self):
        path = os.path.join(self.root, "Lageplan.tif")
        write_plan(path)
        obj = self.import_plan(path)
        self.assertEqual(plan_georef._plan(bpy.context), obj)
        obj.select_set(False)
        self.assertIsNone(plan_georef._plan(bpy.context))
        self.assertTrue(plan_georef._active_unselected(bpy.context))


if __name__ == "__main__":
    unittest.main()

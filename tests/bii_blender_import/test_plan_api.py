"""Tests of plan_api inside Blender: coordinates, crop, Auto Crop, Check Placement.

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

import bii_blender_import  # noqa: E402
from bii_blender_import import plan_api  # noqa: E402

DPI = 100
W, H = 1200, 500
# the drawn track: bends, so a match fixes the position in both directions (image pixels)
TRACK = [(150, 300), (450, 300), (650, 220), (900, 220), (1050, 140)]


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


def make_png(path, frame=True):
    image = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(image)
    if frame:
        d.rectangle((10, 10, W - 11, H - 11), outline=(0, 0, 0), width=3)
    d.line(TRACK, fill=(0, 0, 0), width=3)
    for x in range(200, 1000, 160):  # labels beside the track
        d.rectangle((x, 330, x + 40, 345), fill=(0, 0, 0))
    image.save(path, dpi=(DPI, DPI))


class PlanApiTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.png = os.path.join(self._dir.name, "Plan A.png")
        make_png(self.png)
        bpy.ops.import_image.bii_plan(filepath=self.png, massstab=1000, background=1.0)
        self.plan = bpy.context.view_layer.objects.active

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir.cleanup()

    def test_units_round_trip(self):
        pg = self.plan.bii_plan
        self.assertAlmostEqual(pg.width, W / DPI * 0.0254, places=5)
        for unit, (u, v) in (('SHEET', (0.1, 0.05)), ('PDF', (72.0, 36.0)), ('IMAGE', (600, 250)),
                             ('SOURCE', (600, 250))):
            x, y = plan_api.to_sheet(self.plan, u, v, unit)
            back = plan_api.from_sheet(self.plan, x, y, unit)
            self.assertAlmostEqual(back[0], u, places=4)
            self.assertAlmostEqual(back[1], v, places=4)
        # PDF points and image pixels count from the top
        self.assertAlmostEqual(plan_api.to_sheet(self.plan, 0, 0, 'PDF')[1], pg.height)
        x, y = plan_api.to_sheet(self.plan, W, H, 'IMAGE')
        self.assertAlmostEqual(x, pg.width, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        with self.assertRaises(ValueError):
            plan_api.to_sheet(self.plan, 0, 0, 'MM')

    def test_crop_image_turned(self):
        path = os.path.join(self._dir.name, "crop.png")
        plan_api.crop_image(self.plan, 100, 100, 300, 150, path)
        w, h = Image.open(path).size
        self.assertGreater(w, h)
        # up = to the right on the plan: turned by 90 degrees, so the crop stands upright
        plan_api.crop_image(self.plan, 100, 100, 300, 150, path, up=(1, 0))
        self.assertEqual(Image.open(path).size, (h, w))
        plan_api.crop_image(self.plan, 100, 100, 300, 150, path, up=(0, 1))   # upside down
        self.assertEqual(Image.open(path).size, (w, h))

    def test_groups_without_signal(self):
        red = os.path.join(self._dir.name, "red.png")
        image = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(image)
        d.rectangle((200, 100, 230, 110), fill=(230, 0, 0))     # two red marks far apart
        d.rectangle((800, 300, 830, 310), fill=(230, 0, 0))
        image.save(red, dpi=(DPI, DPI))
        bpy.ops.import_image.bii_plan(filepath=red, massstab=1000, background=1.0)
        plan = bpy.context.view_layer.objects.active
        self.assertEqual(len(plan_api.groups_without_signal(plan, points=[])), 2)
        foot = plan_api.plan_to_world(plan, 215, 112, 'IMAGE')      # a signal just below the first
        left = plan_api.groups_without_signal(plan, points=[foot])
        self.assertEqual(len(left), 1)
        self.assertGreater(left[0]["box"][0], 700)

    def test_seat_above_terrain(self):
        self.assertIsNone(plan_api.seat_above_terrain(self.plan))      # no terrain yet
        bpy.ops.mesh.primitive_plane_add(size=2000, location=(0, 0, 10))
        ground = bpy.context.view_layer.objects.active
        ground["dem_service"] = "test"
        bpy.context.view_layer.update()
        z = plan_api.seat_above_terrain(self.plan, clearance=3.0)
        self.assertAlmostEqual(z, 13.0, places=3)

    def test_world(self):
        self.plan.location = (100.0, 50.0, 3.0)
        self.plan.rotation_euler.z = math.radians(30)
        bpy.context.view_layer.update()
        p = plan_api.plan_to_world(self.plan, 600, 250, 'IMAGE')
        u, v = plan_api.world_to_plan(self.plan, (p.x, p.y, 99.0), 'IMAGE')  # height does not matter
        self.assertAlmostEqual(u, 600, places=3)
        self.assertAlmostEqual(v, 250, places=3)
        corner = plan_api.plan_to_world(self.plan, 0, 0, 'SHEET')
        self.assertAlmostEqual((corner - Vector((100.0, 50.0, 3.0))).length, 0.0, places=4)

    def test_crop_and_auto_crop(self):
        plan_api.crop(self.plan, 100, 100, 500, 400, 'IMAGE')
        x0, y0, x1, y1 = plan_api.crop_rectangle(self.plan)
        self.assertAlmostEqual(x0, 100 / DPI * 0.0254, places=6)
        self.assertAlmostEqual(y1, (H - 100) / DPI * 0.0254, places=6)

        x0, y0, x1, y1 = plan_api.auto_crop(self.plan, margin=0.0)
        px0, py1 = plan_api.from_sheet(self.plan, x0, y0, 'IMAGE')
        px1, py0 = plan_api.from_sheet(self.plan, x1, y1, 'IMAGE')
        # the drawing: track and labels, x 150..1050, y 140..345 px; not the frame at 10 px
        self.assertAlmostEqual(px0, 148, delta=8)
        self.assertAlmostEqual(px1, 1052, delta=8)
        self.assertAlmostEqual(py0, 138, delta=8)
        self.assertAlmostEqual(py1, 347, delta=8)

    def test_check_against_plan(self):
        # plan B: the same drawing, placed 3 m east and 2 m south of where it belongs
        png_b = os.path.join(self._dir.name, "Plan B.png")
        make_png(png_b, frame=False)
        bpy.ops.import_image.bii_plan(filepath=png_b, massstab=1000, background=1.0)
        b = bpy.context.view_layer.objects.active
        b.matrix_world = self.plan.matrix_world.copy()
        b.location.x += 3.0
        b.location.y -= 2.0
        bpy.context.view_layer.update()
        r = plan_api.compare(b, self.plan, cell=0.25)
        self.assertAlmostEqual(r["shift"][0], -3.0, delta=0.5)
        self.assertAlmostEqual(r["shift"][1], 2.0, delta=0.5)
        self.assertGreater(r["score"], r["score_now"])
        self.assertIn("off by", plan_api.describe(r))

        b.location.x -= 3.0
        b.location.y += 2.0
        bpy.context.view_layer.update()
        results = plan_api.check_placement(b, cell=0.25)
        self.assertEqual([x["reference"] for x in results], [self.plan.name])
        self.assertLess(results[0]["distance"], 0.5)
        self.assertIn("fits", plan_api.describe(results[0]))

    def test_overlay(self):
        png_b = os.path.join(self._dir.name, "Plan B.png")
        make_png(png_b, frame=False)
        bpy.ops.import_image.bii_plan(filepath=png_b, massstab=1000, background=1.0)
        b = bpy.context.view_layer.objects.active
        b.matrix_world = self.plan.matrix_world.copy()
        bpy.context.view_layer.update()
        centers = plan_api.overlap_centers(b, self.plan, 3)
        self.assertEqual(len(centers), 3)
        path = plan_api.overlay(b, self.plan, centers[1], os.path.join(self._dir.name, "o.png"), size=60, pixels=300)
        with Image.open(path) as im:
            px = list(im.convert("RGB").getdata())
        self.assertIn((90, 0, 110), px)  # red on blue: the plans match

    def test_check_against_tracks(self):
        coll = bpy.data.collections.new("Gleise 5102")
        bpy.context.scene.collection.children.link(coll)
        curve = bpy.data.curves.new("Gleis", 'CURVE')
        spline = curve.splines.new('POLY')
        pts = [plan_api.plan_to_world(self.plan, u, v, 'IMAGE') for u, v in TRACK]
        spline.points.add(len(pts) - 1)
        for sp, p in zip(spline.points, pts):
            sp.co = (p.x, p.y, 0.0, 1.0)
        obj = bpy.data.objects.new("Gleis 1", curve)
        obj["track_number"] = 1
        coll.objects.link(obj)
        self.plan.location.y += 4.0  # 4 m off the tracks
        bpy.context.view_layer.update()
        r = plan_api.compare(self.plan, "TRACKS", cell=0.25)
        self.assertAlmostEqual(r["shift"][0], 0.0, delta=0.6)
        self.assertAlmostEqual(r["shift"][1], -4.0, delta=0.6)
        self.assertEqual(bpy.ops.bii_plan.check_placement(), {'FINISHED'})

    def test_align_and_attach(self):
        # plan B shows the same drawing; align it onto plan A by three features
        png_b = os.path.join(self._dir.name, "Plan B.png")
        make_png(png_b, frame=False)
        bpy.ops.import_image.bii_plan(filepath=png_b, massstab=1000, background=1.0)
        b = bpy.context.view_layer.objects.active
        b.location = (500.0, 300.0, 0.0)
        b.rotation_euler.z = 0.4
        self.plan.rotation_euler.z = -0.2
        bpy.context.view_layer.update()
        pairs = [(u, v, plan_api.plan_to_world(self.plan, u, v, 'IMAGE')) for u, v in TRACK[::2]]
        residuals = plan_api.align_to_scene(b, pairs, 'IMAGE')
        self.assertLess(max(residuals), 0.01)
        self.assertLess(plan_api.compare(b, self.plan, cell=0.25)["distance"], 0.5)

        # and as a neighbor sheet at a "match line" through two track points
        b.location = (-200.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        pts = [TRACK[1], TRACK[3]]
        plan_api.attach_to_neighbor(b, self.plan, pts, pts, 'IMAGE')
        got = plan_api.plan_to_world(b, *TRACK[1], 'IMAGE')
        want = plan_api.plan_to_world(self.plan, *TRACK[1], 'IMAGE')
        self.assertLess((got - want).xy.length, 0.01)
        self.assertEqual(b.bii_plan_place.attach_parent, self.plan)

    def _plan_b(self, track=TRACK, red=True):
        """Plan B: the drawing of plan A without its frame, with red symbols A does not have."""
        png_b = os.path.join(self._dir.name, "Plan B.png")
        image = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(image)
        d.line(track, fill=(0, 0, 0), width=3)
        for x in range(200, 1000, 160):
            d.rectangle((x, 330, x + 40, 345), fill=(0, 0, 0))
        if red:
            for x in range(260, 1000, 120):
                d.rectangle((x, 250, x + 12, 290), fill=(230, 0, 0))
        image.save(png_b, dpi=(DPI, DPI))
        bpy.ops.import_image.bii_plan(filepath=png_b, massstab=1000, background=1.0)
        return bpy.context.view_layer.objects.active

    def _corner_error(self, b, want):
        x0, y0, x1, y1 = plan_api.crop_rectangle(b)
        return max((b.matrix_world @ Vector((x, y, 0)) - want @ Vector((x, y, 0))).length
                   for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))

    def _turn(self, obj, dx, dy, deg):
        c = plan_api.plan_to_world(obj, W / 2, H / 2, 'IMAGE')
        obj.matrix_world = plan_api._turn_about((c.x, c.y), math.radians(deg), (dx, dy)) @ obj.matrix_world
        bpy.context.view_layer.update()

    def test_fit_to_plans(self):
        plan_api._record(self.plan, "grid")
        b = self._plan_b()
        b.matrix_world = self.plan.matrix_world.copy()
        want = b.matrix_world.copy()
        self._turn(b, 3.0, -2.0, 0.6)
        self.assertGreater(self._corner_error(b, want), 3.0)
        r = plan_api.fit_to_plans(b, piece=60.0)
        self.assertTrue(r["applied"], r["message"])
        self.assertTrue(r["along_fixed"])           # the track bends: both directions are fixed
        self.assertEqual(r["references"], [self.plan.name])
        self.assertLess(self._corner_error(b, want), 0.6)
        self.assertEqual(plan_api.placement(b)["method"], "fit_to_plans")
        # the grid-placed plan is not moved by it, and a plan with nothing under it is refused
        with self.assertRaises(ValueError):
            plan_api.fit_to_plans(self.plan)
        b.location.x += 5000
        bpy.context.view_layer.update()
        with self.assertRaises(ValueError):
            plan_api.fit_to_plans(b)

    def test_fit_to_plans_straight_track(self):
        # a straight track only fixes the place across it: along it the plan stays where it was
        def draw(name, red):
            png = os.path.join(self._dir.name, name)
            image = Image.new("RGB", (W, H), (255, 255, 255))
            d = ImageDraw.Draw(image)
            d.line([(100, 250), (1100, 250)], fill=(0, 0, 0), width=3)      # two tracks, 9 m apart
            d.line([(100, 285), (1100, 285)], fill=(0, 0, 0), width=3)
            for x in range(260, 1000, 120) if red else ():
                d.rectangle((x, 200, x + 12, 240), fill=(230, 0, 0))
            image.save(png, dpi=(DPI, DPI))
            bpy.ops.import_image.bii_plan(filepath=png, massstab=1000, background=1.0)
            return bpy.context.view_layer.objects.active

        a = draw("Straight A.png", False)
        plan_api._record(a, "grid")
        a.location.x += 1000          # away from plan A of setUp
        bpy.context.view_layer.update()
        b = draw("Straight B.png", True)
        b.matrix_world = a.matrix_world.copy()
        want = b.matrix_world.copy()
        self._turn(b, 4.0, 2.0, 0.0)  # 4 m along the track, 2 m across it
        r = plan_api.fit_to_plans(b, references=[a], piece=60.0)
        self.assertTrue(r["across_fixed"], r["message"])
        self.assertFalse(r["along_fixed"])
        d = b.matrix_world.translation - want.translation
        self.assertAlmostEqual(d.y, 0.0, delta=0.5)      # across: fitted
        self.assertAlmostEqual(d.x, 4.0, delta=0.5)      # along: kept

    def test_symbol_features(self):
        # a Ks head 3 x 6 mm on a 6 mm leader with a chevron beyond it, drawn upright at 400 dpi,
        # then turned by 35°; measured in every combination
        mm = 400 / 25.4
        red = (230, 0, 0)

        def symbol(fill, chevron):
            size = 900
            image = Image.new("RGB", (size, size), (255, 255, 255))
            d = ImageDraw.Draw(image)
            cx, foot_y = size / 2, size / 2 + 10 * mm
            d.line((cx - 1.5 * mm, foot_y, cx + 1.5 * mm, foot_y), fill=red, width=int(0.35 * mm))   # foot bar
            d.line((cx, foot_y, cx, foot_y - 6 * mm), fill=red, width=int(0.35 * mm))                # leader
            top, bottom = foot_y - 12 * mm, foot_y - 6 * mm
            box = (cx - 1.5 * mm, top, cx + 1.5 * mm, bottom)
            d.rounded_rectangle(box, radius=1.4 * mm, outline=red, width=int(0.35 * mm))
            if fill == "stellwerk":
                d.rounded_rectangle(box, radius=1.4 * mm, fill=red)
            elif fill == "stellwerk_zug":
                d.rectangle((cx - 1.5 * mm, top + 1.2 * mm, cx, bottom - 1.2 * mm), fill=red)
            tip, arm = top - 2.5 * mm, 1.6 * mm
            if chevron == "zs1":     # ^: tip away from the head
                d.line((cx - arm, tip + 1.6 * mm, cx, tip, cx + arm, tip + 1.6 * mm), fill=red, width=int(0.3 * mm))
            else:                    # V: tip towards the head
                d.line((cx - arm, tip, cx, tip + 1.6 * mm, cx + arm, tip), fill=red, width=int(0.3 * mm))
            image = image.rotate(35, center=(size / 2, size / 2), fillcolor=(255, 255, 255))
            path = os.path.join(self._dir.name, f"sym_{fill}_{chevron}.png")
            image.save(path, dpi=(400, 400))
            a = math.radians(-35)          # PIL turns counterclockwise on screen (y down)

            def turn(x, y):
                x, y = x - size / 2, y - size / 2
                return (size / 2 + x * math.cos(a) - y * math.sin(a), size / 2 + x * math.sin(a) + y * math.cos(a))
            return path, turn(cx, foot_y), turn(cx, foot_y - 9 * mm)

        for fill in ("stellwerk", "stellwerk_zug", "zug"):
            for chevron in ("zs1", "zs7"):
                path, foot, head = symbol(fill, chevron)
                bpy.ops.import_image.bii_plan(filepath=path, massstab=1000, background=1.0)
                plan = bpy.context.view_layer.objects.active
                r = plan_api.symbol_features(plan, foot, head, 'IMAGE')
                self.assertEqual((r["bedienung"], r["chevron"]), (fill, chevron), r)
                self.assertAlmostEqual(r["length_mm"], 14.0, delta=1.5)

    def test_operators(self):
        self.assertEqual(bpy.ops.bii_plan.auto_crop(), {'FINISHED'})
        self.assertLess(plan_api.crop_rectangle(self.plan)[2], self.plan.bii_plan.width)
        # nothing to compare with
        self.assertEqual(bpy.ops.bii_plan.check_placement(), {'CANCELLED'})


if __name__ == "__main__":
    unittest.main()

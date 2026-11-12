"""Tests of placing plans by Kilometrierung and of neighbor sheets, inside Blender.

Builds a Kilometrierung line the way BII Umgebung does (curve "KM-Linie
<Strecke> ...", km at both ends, tick marks every 100 m), imports a sheet,
and checks that km points put it back where it belongs.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import math
import os
import tempfile
import unittest

try:
    import bpy
    from mathutils import Matrix, Vector
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender and Pillow: {e}")

import bii_blender_import  # noqa: E402  (run_in_blender.py puts plugins/ on sys.path)
from bii_blender_import import plan_km, plan_place  # noqa: E402

RADIUS = 1500.0
KM_START = 5.0


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


def arc_point(s, offset=0.0):
    """Point of the test track at s meters from its start; offset to the left."""
    a = s / RADIUS
    r = RADIUS - offset
    return Vector((r * math.sin(a), RADIUS - r * math.cos(a), 0.0))


def add_curve(name, points, collection, **props):
    curve = bpy.data.curves.new(name, 'CURVE')
    spline = curve.splines.new('POLY')
    spline.points.add(len(points) - 1)
    origin = points[0]
    for i, p in enumerate(points):
        spline.points[i].co = (*(p - origin), 1.0)
    obj = bpy.data.objects.new(name, curve)
    obj.location = origin
    for key, value in props.items():
        obj[key] = value
    collection.objects.link(obj)
    return obj


def build_umgebung_line(length=1200.0, strecke="5240"):
    """A Kilometrierung line like BII Umgebung builds it, km 5,0 at its start."""
    label = f"{strecke}  Teststrecke"
    coll = bpy.data.collections.new(f"KM {label}")
    bpy.context.scene.collection.children.link(coll)
    points = [arc_point(s) for s in range(0, int(length) + 1, 20)]
    add_curve(f"KM-Linie {label}", points, coll, railway_chainage=True,
              chainage_source="OSM", chainage_km_start=KM_START,
              chainage_km_end=KM_START + length / 1000, chainage_interval_m=100.0)
    verts, edges = [], []
    for s in range(100, int(length), 100):
        p, q = arc_point(s, 3), arc_point(s, -3)
        edges.append((len(verts), len(verts) + 1))
        verts += [p, q]
    mesh = bpy.data.meshes.new(f"KM-Marken {label}")
    mesh.from_pydata(verts, edges, [])
    ticks = bpy.data.objects.new(f"KM-Marken {label}", mesh)
    ticks["railway_chainage"] = True
    coll.objects.link(ticks)
    return label


def km_point(km, offset=0.0):
    return arc_point((km - KM_START) * 1000, offset)


class PlanPlaceTest(unittest.TestCase):

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.label = build_umgebung_line()

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self._dir.cleanup()

    def sheet(self, name, size=(1200, 800), dpi=100):
        path = os.path.join(self._dir.name, name)
        Image.new("RGB", size, (255, 255, 255)).save(path, dpi=(dpi, dpi))
        return path

    def import_plan(self, name="Lageplan.png"):
        bpy.ops.import_image.bii_plan(filepath=self.sheet(name), massstab=1000)
        return bpy.context.view_layer.objects.active

    def true_matrix(self):
        # Where the sheet belongs: sheet point (0.15, 0.10) at km 5,4, track along sheet x.
        p = km_point(5.4)
        heading = math.atan2(*(reversed((km_point(5.41) - km_point(5.39)).xy)))
        return (Matrix.Translation(p) @ Matrix.Rotation(heading, 4, 'Z')
                @ Matrix.Diagonal((1000, 1000, 1000, 1)) @ Matrix.Translation((-0.15, -0.10, 0)))

    def sheet_xy(self, matrix, world):
        v = matrix.inverted() @ world
        return v.x, v.y

    def assert_placed(self, obj, matrix, tolerance=0.05):
        for x, y in ((0, 0), (0.3, 0), (0.3, 0.2), (0.15, 0.1)):
            got = obj.matrix_world @ Vector((x, y, 0))
            want = matrix @ Vector((x, y, 0))
            self.assertLess((got - want).xy.length, tolerance, f"sheet point {x}, {y}")

    def test_km_line_from_umgebung(self):
        line_obj = plan_place.km_line_objects(bpy.context.scene, "5240")[0]
        line = plan_place.km_line_from_object(line_obj)
        self.assertAlmostEqual(line.km_range[0], 5.0)
        self.assertAlmostEqual(line.km_range[1], 6.2)
        pos, _ = line.position(5.3)
        self.assertLess((Vector((*pos, 0)) - km_point(5.3)).length, 0.05)
        self.assertEqual(plan_place.scene_strecken(bpy.context.scene), ["5240"])
        self.assertEqual(plan_place.km_line_objects(bpy.context.scene, "5241"), [])

    def test_align_by_km(self):
        plan = self.import_plan()
        truth = self.true_matrix()
        place = plan.bii_plan_place
        place.strecke = "5240"
        for km in ("5,3", "5,5"):
            p = place.km_points.add()
            p.name = f"K{len(place.km_points)}"
            p.x, p.y = self.sheet_xy(truth, km_point(plan_km.parse_km(km)))
            p.km_text = km
        p = place.km_points.add()  # on the track, without km
        p.name = "K3"
        p.x, p.y = self.sheet_xy(truth, km_point(5.42))
        plan.rotation_euler.z = math.radians(170)  # roughly the wrong way round
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.bii_plan.align_km(), {'FINISHED'})
        self.assert_placed(plan, truth)
        self.assertTrue(place.placed_by_km)
        self.assertLess(max(p.residual for p in place.km_points), 0.05)
        self.assertNotIn("!", place.report)
        # A new click gets its km predicted from the line, as a guess.
        new = plan_place.add_km_point(bpy.context.scene, plan,
                                      *self.sheet_xy(truth, km_point(5.6)))
        self.assertEqual(new.km_text, "5,6")
        self.assertTrue(new.km_guessed)
        self.assertIsNone(new.km)
        new.km_text = "5,6"  # typed: read from the plan
        self.assertFalse(new.km_guessed)

    def test_landmark_label_sets_strecke_and_wins(self):
        plan = self.import_plan()
        truth = self.true_matrix()
        place = plan.bii_plan_place
        p = place.km_points.add()
        p.name = "K1"
        p.x, p.y = self.sheet_xy(truth, km_point(5.3))
        p.km_text = "EÜ (Weg) 5240/5,3/1616"
        self.assertEqual(place.strecke, "5240")
        self.assertAlmostEqual(p.km, 5.3)
        p = place.km_points.add()
        p.name = "K2"
        p.x, p.y = self.sheet_xy(truth, km_point(5.5))
        p.km_text = "5,52"  # wrong by 20 m
        pair = plan.bii_plan.pairs.add()  # the landmark at the 3D cursor is right
        pair.name = "A1"
        pair.x, pair.y = self.sheet_xy(truth, km_point(5.48))
        pair.target = km_point(5.48)
        self.assertEqual(bpy.ops.bii_plan.align_km(), {'FINISHED'})
        self.assertIn("!", place.report)  # the spacing check names the wrong km
        landmark = plan.matrix_world @ Vector((pair.x, pair.y, 0))
        self.assertLess((landmark - km_point(5.48)).xy.length, 2.0)

    def test_track_axes_pull_across(self):
        # A track 2.5 m left of the Kilometrierung line; the plan shows that track.
        coll = bpy.data.collections.new(f"Gleise {self.label}")
        bpy.context.scene.collection.children.link(coll)
        add_curve("Gleis 1", [arc_point(s, 2.5) for s in range(0, 1201, 20)], coll,
                  track_number=1)
        plan = self.import_plan()
        truth = Matrix.Translation(km_point(5.4, 2.5) - km_point(5.4)) @ self.true_matrix()
        place = plan.bii_plan_place
        for km in (5.3, 5.5):
            p = place.km_points.add()
            p.name = f"K{len(place.km_points)}"
            p.x, p.y = self.sheet_xy(truth, km_point(km, 2.5))
            p.km_text = plan_km.format_km(km)
        self.assertEqual(bpy.ops.bii_plan.align_km(), {'FINISHED'})
        self.assert_placed(plan, truth, tolerance=0.3)

    def test_no_line_for_strecke(self):
        plan = self.import_plan()
        place = plan.bii_plan_place
        place.strecke = "6100"
        p = place.km_points.add()
        p.km_text = "5,3"
        with self.assertRaises(ValueError):
            plan_place.align_by_km(bpy.context.scene, plan)

    def test_import_sheets_side_by_side(self):
        names = ["Blatt 10.png", "Blatt 2.png", "Blatt 1.png"]
        for n in names:
            self.sheet(n, (600, 400), 100)
        result = bpy.ops.import_image.bii_plan_sheets(
            directory=self._dir.name, files=[{"name": n} for n in names], massstab=500, gap=10)
        self.assertEqual(result, {'FINISHED'})
        plans = sorted((o for o in bpy.data.objects if o.bii_plan.is_plan),
                       key=lambda o: o.location.x)
        self.assertEqual([o.name for o in plans], ["Blatt 1", "Blatt 2", "Blatt 10"])
        width = 600 / 100 * 0.0254 * 500
        for a, b in zip(plans, plans[1:]):
            self.assertAlmostEqual(b.location.x - a.location.x, width + 10, places=3)
            self.assertAlmostEqual(a.bii_plan.massstab, 500)

    def test_attach_and_chain(self):
        a = self.import_plan("A.png")
        b = self.import_plan("B.png")
        c = self.import_plan("C.png")
        ma = Matrix.Translation((100, 50, 0)) @ Matrix.Rotation(0.2, 4, 'Z') @ Matrix.Diagonal(
            (1000, 1000, 1000, 1))
        # Sheet B continues A to the right, turned by 3° about the shared point.
        joint = Matrix.Translation((0.30, 0.10, 0)) @ Matrix.Rotation(math.radians(3), 4, 'Z') \
            @ Matrix.Translation((-0.005, -0.10, 0))
        mb = ma @ joint
        a.matrix_world = ma
        for child, parent in ((b, a), (c, b)):
            place = child.bii_plan_place
            place.attach_parent = parent
            back = Matrix.Rotation(math.radians(-3), 4, 'Z') @ Vector((0, -0.05, 0))
            # On the child the match line is clicked the other way: the side decides.
            place.attach_own = (0.005, 0.10, 0.005 + back.x, 0.10 + back.y)
            place.attach_other = (0.30, 0.10, 0.30, 0.15)
        bpy.context.view_layer.update()
        self.assertEqual(plan_place.attach(b), [])
        bpy.context.view_layer.update()
        self.assert_placed(b, mb, tolerance=1e-3)
        # Moving A and placing the chain moves B and C.
        ma = Matrix.Translation((-300, 20, 0)) @ Matrix.Rotation(-1.0, 4, 'Z') \
            @ Matrix.Diagonal((1000, 1000, 1000, 1))
        a.matrix_world = ma
        bpy.context.view_layer.update()
        moved, kept, warnings = plan_place.place_chain(bpy.context.scene, a)
        self.assertEqual(moved, [b.name, c.name])
        self.assert_placed(b, ma @ joint, tolerance=1e-3)
        self.assert_placed(c, ma @ joint @ joint, tolerance=1e-3)
        # A sheet placed by its own km points stays.
        c.bii_plan_place.placed_by_km = True
        a.matrix_world = Matrix.Translation((50, 0, 0)) @ ma
        bpy.context.view_layer.update()
        moved, kept, _ = plan_place.place_chain(bpy.context.scene, a)
        self.assertEqual((moved, kept), ([b.name], [c.name]))

    def attach_pair(self):
        """Sheet B attached to the right edge of sheet A."""
        a = self.import_plan("A.png")
        b = self.import_plan("B.png")
        place = b.bii_plan_place
        place.attach_parent = a
        place.attach_own = (0.0, 0.10, 0.0, 0.15)
        place.attach_other = (0.30, 0.10, 0.30, 0.15)
        bpy.context.view_layer.update()
        return a, b

    def test_upright_sheet_in_chain_is_a_warning(self):
        a, b = self.attach_pair()
        b.bii_plan.orientation = 'UPRIGHT'
        bpy.context.view_layer.update()
        moved, kept, warnings = plan_place.place_chain(bpy.context.scene, a)
        self.assertEqual(moved, [])
        self.assertTrue(any(b.name in w for w in warnings), warnings)
        # The attach button is not offered on an upright sheet.
        bpy.context.view_layer.objects.active = b
        self.assertFalse(plan_place.PlanAttachOperator.poll(bpy.context))

    def test_align_to_scene_places_attached_sheets(self):
        a, b = self.attach_pair()
        pair = a.bii_plan.pairs.add()
        pair.x, pair.y = 0.0, 0.0
        pair.target = (500.0, 300.0, 0.0)
        bpy.context.view_layer.objects.active = a
        self.assertEqual(bpy.ops.bii_plan.align(), {'FINISHED'})
        bpy.context.view_layer.update()
        joint = b.matrix_world @ Vector((0.0, 0.10, 0.0))
        edge = a.matrix_world @ Vector((0.30, 0.10, 0.0))
        self.assertLess((joint - edge).xy.length, 1e-3)
        self.assertLess((edge - Vector((800.0, 400.0, 0.0))).xy.length, 1e-3)


if __name__ == "__main__":
    unittest.main()

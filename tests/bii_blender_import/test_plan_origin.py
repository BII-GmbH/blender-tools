"""Tests of taking the scene origin from the Umgebung panel, inside Blender.

Registers a stand-in for Umgebung's scene settings (scene.railway_chainage)
with the fields the fallback reads.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import unittest

try:
    import bpy
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender: {e}")

import bii_blender_import  # noqa: E402  (run_in_blender.py puts plugins/ on sys.path)
from bii_blender_import import plan_georef  # noqa: E402


class FakeUmgebungProps(bpy.types.PropertyGroup):
    area_mode: bpy.props.EnumProperty(items=[('CENTER', "Center", ""), ('BBOX', "Box", "")])
    center_lat: bpy.props.FloatProperty()
    center_lon: bpy.props.FloatProperty()
    min_lat: bpy.props.FloatProperty()
    max_lat: bpy.props.FloatProperty()
    min_lon: bpy.props.FloatProperty()
    max_lon: bpy.props.FloatProperty()


def setUpModule():
    bii_blender_import.register()
    bpy.utils.register_class(FakeUmgebungProps)
    bpy.types.Scene.railway_chainage = bpy.props.PointerProperty(type=FakeUmgebungProps)


def tearDownModule():
    del bpy.types.Scene.railway_chainage
    bpy.utils.unregister_class(FakeUmgebungProps)
    bii_blender_import.unregister()


class OriginFromUmgebungTest(unittest.TestCase):
    def setUp(self):
        scene = bpy.context.scene
        for key in ("lat", "lon"):
            if key in scene:
                del scene[key]
        self.props = scene.railway_chainage

    def test_center_mode(self):
        self.props.area_mode = 'CENTER'
        self.props.center_lat, self.props.center_lon = 50.057, 10.232
        lat, lon = plan_georef.umgebung_origin(bpy.context.scene)
        self.assertAlmostEqual(lat, 50.057, places=5)
        self.assertAlmostEqual(lon, 10.232, places=5)

    def test_box_mode_is_its_middle(self):
        self.props.area_mode = 'BBOX'
        self.props.min_lat, self.props.max_lat = 50.0, 50.2
        self.props.min_lon, self.props.max_lon = 10.0, 10.4
        lat, lon = plan_georef.umgebung_origin(bpy.context.scene)
        self.assertAlmostEqual(lat, 50.1, places=5)
        self.assertAlmostEqual(lon, 10.2, places=5)

    def test_operator_stores_origin_once(self):
        self.props.area_mode = 'CENTER'
        self.props.center_lat, self.props.center_lon = 49.5, 11.0
        self.assertEqual(bpy.ops.bii_plan.origin_from_umgebung('EXEC_DEFAULT'), {'FINISHED'})
        scene = bpy.context.scene
        self.assertAlmostEqual(scene["lat"], 49.5, places=5)
        self.assertAlmostEqual(scene["lon"], 11.0, places=5)
        # With an origin stored, the fallback is not offered any more.
        self.assertFalse(plan_georef.PlanOriginFromUmgebungOperator.poll(bpy.context))


if __name__ == "__main__":
    unittest.main()

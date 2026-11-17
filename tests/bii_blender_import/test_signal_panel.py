"""Blender tests for the Signal panel (signal_panel.py).

Run: blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_import/run_in_blender.py

Placing needs the DB_Signale.blend asset library: set BII_SIGNAL_LIBRARY to the
folder that holds it (for example the asset library's library folder), else
those tests are skipped.
"""

import os
import unittest

try:
    import bpy
except ImportError:
    raise unittest.SkipTest("needs Blender")

import bii_blender_import
from bii_blender_import import signal_panel, signal_spec as spec

LIBRARY_DIR = os.environ.get("BII_SIGNAL_LIBRARY", "")


def setUpModule():
    bii_blender_import.register()


def tearDownModule():
    bii_blender_import.unregister()


class DraftTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def test_set_option_and_bauform(self):
        bpy.ops.bii_signal.set_bauform(bauform="ks_mehr")
        bpy.ops.bii_signal.set_option(element="zs3", value="licht")
        bpy.ops.bii_signal.set_option(element="sh1", value="True")
        d = bpy.context.scene.bii_signal
        self.assertEqual(d.get()["zs3"], "licht")
        self.assertTrue(d.get()["sh1"])
        bpy.ops.bii_signal.set_bauform(bauform="ks_vor")  # no Zs 3, no Sh 1 there
        self.assertEqual(d.bauform, "ks_vor")
        self.assertNotIn("zs3", d.get())
        bpy.ops.bii_signal.set_bauform(bauform="ks_haupt")
        self.assertEqual(d.get()["zs3"], "ohne")  # defaults for what ks_vor did not keep

    def test_aspect_falls_back_when_its_lights_go(self):
        bpy.ops.bii_signal.set_bauform(bauform="ks_haupt")
        bpy.ops.bii_signal.set_option(element="sh1", value="True")
        bpy.ops.bii_signal.set_aspect(aspect="Hp0 + Sh1")
        self.assertEqual(bpy.context.scene.bii_signal.aspect, "Hp0 + Sh1")
        bpy.ops.bii_signal.set_option(element="sh1", value="False")
        self.assertEqual(bpy.context.scene.bii_signal.aspect, "Aus")

    def test_icons(self):
        els = spec.symbol_elements("hv_mehr", spec.config("hv_mehr", zs3="licht"))
        a = signal_panel._icon(els)  # icon_id is 0 without a GPU (background mode)
        self.assertEqual(a, signal_panel._icon(els))
        key = f"{signal_panel.ICON_SIZE}:" + __import__("json").dumps(els)
        self.assertEqual(tuple(signal_panel._previews[key].image_size), (signal_panel.ICON_SIZE,) * 2)


@unittest.skipUnless(os.path.isfile(os.path.join(LIBRARY_DIR, signal_panel.LIBRARY_FILE)),
                     "BII_SIGNAL_LIBRARY not set")
class PlaceTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        libs = bpy.context.preferences.filepaths.asset_libraries
        if not any(lib.name == "test signals" for lib in libs):
            bpy.ops.preferences.asset_library_add(directory=LIBRARY_DIR)
            libs[-1].name = "test signals"
            libs[-1].import_method = 'PACK'

    def test_place_and_configure(self):
        scene = bpy.context.scene
        scene.cursor.location = (10.0, 5.0, 0.0)
        bpy.ops.bii_signal.set_bauform(bauform="ks_mehr")
        for k, v in (("bedienung", "stellwerk_zug"), ("kopf", "ohne"), ("zs3", "licht"), ("zs3v", "licht")):
            bpy.ops.bii_signal.set_option(element=k, value=v)
        bpy.ops.bii_signal.set_aspect(aspect="Ks1")
        scene.bii_signal.library = "test signals"
        self.assertEqual(bpy.ops.bii_signal.place(), {'FINISHED'})
        obj = bpy.context.active_object
        self.assertEqual(tuple(obj.location), (10.0, 5.0, 0.0))
        self.assertIsNone(obj.library)
        mod = signal_panel.signal_modifier(obj)
        b, cfg, aspect = signal_panel.object_state(mod)
        self.assertEqual(b, "ks_mehr")
        self.assertEqual(cfg, scene.bii_signal.get())
        self.assertEqual(aspect, "Ks1")

        # the panel now edits the placed signal
        full = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons)
        bpy.ops.bii_signal.set_option(element="zs3v", value="ohne")
        bpy.context.view_layer.update()
        fewer = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons)
        self.assertLess(fewer, full)
        self.assertEqual(signal_panel.object_state(mod)[1]["zs3v"], "ohne")

        # Place Another: a second signal like the active one
        bpy.context.scene.cursor.location = (20.0, 5.0, 0.0)
        self.assertEqual(bpy.ops.bii_signal.place(), {'FINISHED'})
        second = bpy.context.active_object
        self.assertIsNot(second, obj)
        self.assertEqual(signal_panel.object_state(signal_panel.signal_modifier(second))[1],
                         signal_panel.object_state(mod)[1])

        me = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
        lit = sum(1 for p in me.polygons if p.material_index < len(me.materials)
                  and me.materials[p.material_index].name == "Signal Lampe an")
        self.assertEqual(lit, 1)  # Ks1: the green light
        obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()


if __name__ == "__main__":
    unittest.main()

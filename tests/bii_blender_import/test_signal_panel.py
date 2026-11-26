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

    def test_icon_pickers(self):
        picks = bpy.context.scene.bii_signal.picks
        picks.pick_bauform = "ks_haupt"
        self.assertEqual(bpy.context.scene.bii_signal.bauform, "ks_haupt")
        picks.pick_zs3 = "tafel"
        picks.pick_sh1 = "True"
        cfg = bpy.context.scene.bii_signal.get()
        self.assertEqual((cfg["zs3"], cfg["sh1"]), ("tafel", True))
        self.assertEqual(picks.pick_zs3, "tafel")
        picks.pick_bauform = "hv_haupt"
        with self.assertRaises(TypeError):  # Hp0/Hp2 does not fit Sh 1: not offered
            picks.pick_hp = "hp02"
        picks.pick_hp = "hp01"
        self.assertEqual(bpy.context.scene.bii_signal.get()["hp"], "hp01")

    def test_number_options(self):
        # options whose values are numbers (Ne 3 stripes) come back as numbers
        bpy.context.view_layer.objects.active = None
        bpy.ops.bii_signal.set_bauform(bauform="ne3")
        self.assertEqual(bpy.ops.bii_signal.set_option(element="streifen", value="2"), {'FINISHED'})
        self.assertEqual(bpy.context.scene.bii_signal.get()["streifen"], 2)
        for b in spec.BAUFORMEN:  # every option button value parses
            for e in spec.elements(b.key):
                if not e.number:
                    for v in e.values():
                        self.assertEqual(signal_panel._parse(e.key, str(v)), v)

    def test_faces_the_way_its_symbol_is_drawn(self):
        # symbol top to the west, foot to the east: seen upright from the east, so it faces east
        import math
        east = signal_panel.symbol_facing((10.0, 0.0), (6.0, 0.5))
        self.assertGreater(east[0], 0)
        r = signal_panel.facing_rotation((1.0, 0.0))
        lamps = (math.sin(r), -math.cos(r))        # local -Y in world
        self.assertAlmostEqual(lamps[0], 1.0, places=6)
        self.assertAlmostEqual(lamps[1], 0.0, places=6)
        # along a track drawn towards -x, the symbol's way wins over the drawing direction
        coll = bpy.data.collections.new("Gleise 5102")
        bpy.context.scene.collection.children.link(coll)
        curve = bpy.data.curves.new("Gleis", 'CURVE')
        spline = curve.splines.new('POLY')
        spline.points.add(1)
        spline.points[0].co = (100.0, 0.0, 0.0, 1.0)
        spline.points[1].co = (-100.0, 0.0, 0.0, 1.0)
        obj = bpy.data.objects.new("Gleis 1", curve)
        obj["track_number"] = 1
        coll.objects.link(obj)
        for side in (-3.0, 3.0):                   # the side of the track does not matter
            r = signal_panel.track_rotation(bpy.context, (0.0, side), east)
            self.assertAlmostEqual(math.sin(r), 1.0, places=6)
            r = signal_panel.track_rotation(bpy.context, (0.0, side), (-1.0, 0.2))
            self.assertAlmostEqual(math.sin(r), -1.0, places=6)

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

        self.assertEqual(obj.name, "Mehrabschnitts-Blocksignal stellwerks-/zugbedient Hp0/Ks1/Ks2/Zs3/Zs3v")

        # the panel now edits the placed signal, and its name follows
        full = len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons)
        bpy.ops.bii_signal.set_option(element="zs3v", value="ohne")
        self.assertEqual(obj.name, "Mehrabschnitts-Blocksignal stellwerks-/zugbedient Hp0/Ks1/Ks2/Zs3")
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

    def test_change_bauform_of_placed_signal(self):
        scene = bpy.context.scene
        scene.bii_signal.library = "test signals"
        bpy.context.view_layer.objects.active = None
        bpy.ops.bii_signal.set_bauform(bauform="ks_mehr")
        bpy.ops.bii_signal.set_option(element="zs3", value="licht")
        scene.cursor.location = (3.0, 4.0, 0.0)
        bpy.ops.bii_signal.place()
        old = bpy.context.active_object
        old.rotation_euler.z = 1.0
        coll = bpy.data.collections.new("Signale")
        scene.collection.children.link(coll)
        coll.objects.link(old)
        old_name = old.name

        self.assertEqual(bpy.ops.bii_signal.set_bauform(bauform="ks_haupt"), {'FINISHED'})
        new = bpy.context.view_layer.objects.active
        self.assertNotIn(old_name, bpy.data.objects)
        self.assertEqual(tuple(new.location), (3.0, 4.0, 0.0))
        self.assertAlmostEqual(new.rotation_euler.z, 1.0, places=5)
        self.assertIn(coll, new.users_collection)
        b, cfg, _ = signal_panel.object_state(signal_panel.signal_modifier(new))
        self.assertEqual((b, cfg["zs3"]), ("ks_haupt", "licht"))  # options both have are kept
        self.assertEqual(new.name, spec.object_name("ks_haupt", cfg))

        new.name = "Mein Signal"  # a renamed signal keeps its name
        bpy.ops.bii_signal.set_bauform(bauform="hv_haupt")
        self.assertEqual(bpy.context.view_layer.objects.active.name, "Mein Signal")

    def test_older_library_is_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as old_dir:
            # an older DB_Signale.blend: an object of the same name, but no "Signal" node group
            old = bpy.data.objects.new("Ks-Vorsignal", bpy.data.meshes.new("old"))
            bpy.context.scene.collection.objects.link(old)
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(old_dir, signal_panel.LIBRARY_FILE), copy=True)
            bpy.data.objects.remove(old)
            libs = bpy.context.preferences.filepaths.asset_libraries
            bpy.ops.preferences.asset_library_add(directory=old_dir)
            libs[-1].name = "old signals"
            try:
                ctx = bpy.context
                self.assertEqual(signal_panel.library_path(ctx, "", "Ks-Vorsignal")[2], "test signals")
                self.assertIsNone(signal_panel.library_path(ctx, "old signals", "Ks-Vorsignal")[0])
                self.assertIn("older version", signal_panel.missing_text(ctx, "old signals", "Ks-Vorsignal"))
            finally:
                bpy.ops.preferences.asset_library_remove(index=len(libs) - 1)

    def test_speed_sign_with_kennziffer(self):
        scene = bpy.context.scene
        scene.bii_signal.library = "test signals"
        bpy.context.view_layer.objects.active = None
        bpy.ops.bii_signal.set_bauform(bauform="lf7")
        scene.bii_signal.picks.pick_kennziffer = 10
        self.assertEqual(scene.bii_signal.get()["kennziffer"], 10)
        self.assertEqual(bpy.ops.bii_signal.place(), {'FINISHED'})
        obj = bpy.context.active_object
        mod = signal_panel.signal_modifier(obj)
        self.assertEqual(signal_panel.object_state(mod)[1]["kennziffer"], 10)
        self.assertEqual(obj.name, "Geschwindigkeitssignal 100 km/h Lf7")
        scene.bii_signal.picks.pick_kennziffer = 6  # on the placed signal: its name follows
        self.assertEqual(obj.name, "Geschwindigkeitssignal 60 km/h Lf7")

        other = bpy.data.objects.new("not a signal", None)
        scene.collection.objects.link(other)
        other.select_set(True)
        obj.select_set(True)
        bpy.ops.bii_signal.turn()
        self.assertEqual(other.rotation_euler.z, 0.0)  # only signals turn
        self.assertNotEqual(obj.rotation_euler.z, 0.0)

        def faces():
            bpy.context.view_layer.update()
            return len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons)

        with_number = faces()
        signal_panel.write_modifier(mod, {"Kennziffer": 0})
        self.assertLess(faces(), with_number)  # the digits are gone


    def test_scripting_api(self):
        bpy.context.scene.bii_signal.library = "test signals"
        obj = signal_panel.place_signal("ks_mehr", {"zs3": "licht", "zs3v": "licht", "kopf": "ohne"},
                                        (5.0, 6.0, 1.5), rotation=0.5, aspect="Ks1",
                                        library="test signals")
        self.assertEqual(tuple(obj.location), (5.0, 6.0, 1.5))
        self.assertAlmostEqual(obj.rotation_euler.z, 0.5, places=6)
        b, cfg, aspect = signal_panel.read_signal(obj)
        self.assertEqual((b, cfg["zs3"], cfg["zs3v"], cfg["kopf"], aspect), ("ks_mehr", "licht", "licht", "ohne", "Ks1"))
        self.assertEqual(obj.name, spec.object_name(b, cfg))

        obj = signal_panel.configure_signal(obj, {"zs3": "tafel"})
        b, cfg, aspect = signal_panel.read_signal(obj)
        self.assertEqual((cfg["zs3"], cfg["zs3v"], aspect), ("tafel", "licht", "Ks1"))  # the rest stays
        self.assertEqual(obj.name, spec.object_name(b, cfg))  # the name follows

        new = signal_panel.configure_signal(obj, bauform="ks_haupt")
        self.assertEqual(signal_panel.read_signal(new)[0], "ks_haupt")
        self.assertEqual(tuple(new.location), (5.0, 6.0, 1.5))

        # head given: faces from the symbol's head towards its foot (here north: lamps to +y)
        import math
        north = signal_panel.place_signal("lf7", {}, (0.0, 0.0, 0.0), head=(0.0, -4.0), library="test signals")
        r = north.rotation_euler.z
        self.assertAlmostEqual(math.sin(r), 0.0, places=6)
        self.assertAlmostEqual(-math.cos(r), 1.0, places=6)

        with self.assertRaisesRegex(ValueError, "no element"):
            signal_panel.place_signal("ks_vor", {"sh1": True})
        with self.assertRaisesRegex(ValueError, "unknown bauform"):
            signal_panel.place_signal("ks_main")
        with self.assertRaisesRegex(ValueError, "does not fit"):
            signal_panel.place_signal("hv_haupt", {"hp": "hp02", "sh1": True})
        with self.assertRaisesRegex(ValueError, "lights nothing"):
            signal_panel.place_signal("ks_haupt", aspect="Hp0 + Sh1")
        self.assertIsNone(signal_panel.read_signal(bpy.data.objects.new("plain", None)))

    def test_on_terrain(self):
        # a tilted terrain piece of BII Umgebung: z = 10 + 0.1 x
        me = bpy.data.meshes.new("terrain")
        me.from_pydata([(-50, -50, 5), (50, -50, 15), (50, 50, 15), (-50, 50, 5)], [], [(0, 1, 2, 3)])
        ground = bpy.data.objects.new("Gelände Test", me)
        ground["dem_service"] = "test"
        bpy.context.scene.collection.objects.link(ground)
        ground.location.z = 2.0
        bpy.context.view_layer.update()
        self.assertAlmostEqual(signal_panel.terrain_height(bpy.context, 20.0, 0.0), 14.0, places=4)
        self.assertIsNone(signal_panel.terrain_height(bpy.context, 500.0, 0.0))

        obj = signal_panel.place_signal("lf7", {"kennziffer": 8}, (20.0, 0.0, 99.0), on_terrain=True,
                                        library="test signals")
        self.assertAlmostEqual(obj.location.z, 14.0, places=4)

        scene = bpy.context.scene
        scene.cursor.location = (-10.0, 0.0, 50.0)
        bpy.context.view_layer.objects.active = None
        scene.bii_signal.library = "test signals"
        scene.bii_signal.on_terrain = True
        bpy.ops.bii_signal.place()
        self.assertAlmostEqual(bpy.context.view_layer.objects.active.location.z, 11.0, places=4)
        scene.bii_signal.on_terrain = False
        bpy.context.view_layer.objects.active = None
        bpy.ops.bii_signal.place()
        self.assertAlmostEqual(bpy.context.view_layer.objects.active.location.z, 50.0, places=4)


class LegendTest(unittest.TestCase):
    def test_export_legend(self):
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = os.path.join(d, "key.png")
            self.assertEqual(bpy.ops.bii_signal.export_legend(filepath=path), {'FINISHED'})
            self.assertGreater(os.path.getsize(path), 10000)


if __name__ == "__main__":
    unittest.main()

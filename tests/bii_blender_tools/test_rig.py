"""Blender tests for the equipment rigs of BII Tools: preview, export to .dequip, the check against dProB's math.

Run: blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_tools/run_in_blender.py
"""

import json
import math
import os
import tempfile
import unittest
import zipfile

try:
    import bpy
except ImportError:
    raise unittest.SkipTest("needs Blender")

import numpy as np
from mathutils import Euler, Matrix, Vector

import bii_blender_tools
from bii_blender_tools.equipment import (dequip_format, dprob_math, fbx_binary, rig_api as rig,
                                   rig_export, rig_props)
from bii_blender_tools.equipment.rig_props import RigError

TOL = 1e-5


def setUpModule():
    bii_blender_tools.register()


def tearDownModule():
    bii_blender_tools.unregister()


def cube(name, loc, parent=None, rot=(0, 0, 0), scale=(1, 1, 1), size=1.0):
    mesh = bpy.data.meshes.new(name)
    s = size / 2
    verts = [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    o = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(o)
    o.matrix_world = Matrix.LocRotScale(Vector(loc), Euler(rot), Vector(scale))
    if parent is not None:
        bpy.context.view_layer.update()
        mw = o.matrix_world.copy()
        o.parent = parent
        o.matrix_world = mw
    bpy.context.view_layer.update()
    return o


def crane():
    """A small crane: root empty off the origin, turned; joints of every type, scales, nesting."""
    root = bpy.data.objects.new("Crane", None)
    bpy.context.scene.collection.objects.link(root)
    root.location = (5, 3, 1)
    root.rotation_euler = (0, 0, 0.3)
    bpy.context.view_layer.update()
    base = cube("Base", (5, 3, 1.5), root, scale=(2, 2, 1))
    tower = cube("Tower", (5, 3, 3), base, rot=(0, 0, 0.7))
    arm = cube("Arm", (6, 3.5, 4), tower, rot=(0.2, 0.4, 0.1), scale=(1.5, 1.5, 1.5))
    slider = cube("Slider", (7, 3.5, 4), arm, rot=(0.0, 0.1, 0.3))
    hook = cube("Hook", (7, 3.5, 3), slider, rot=(0.5, 0.0, 0.0))
    rig.make_rig(root)
    rig.set_joint(tower, "REVOLUTE", axis="Z", limits=None)
    rig.set_joint(arm, "REVOLUTE", axis=(1, 1, 0), limits=(-30, 60))
    rig.set_joint(slider, "PRISMATIC", axis="X", limits=(-0.5, 2.0))
    rig.set_joint(hook, "BALL")
    return root


class RigTestCase(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "crane.dequip")

    def tearDown(self):
        self.tmp.cleanup()


class PreviewTest(RigTestCase):
    def test_revolute_turns_about_origin_and_world_axis(self):
        root = crane()
        arm = bpy.data.objects["Arm"]
        child = bpy.data.objects["Slider"]
        pivot = arm.matrix_world.translation.copy()
        axis = rig_props.world_axis(arm)
        before = child.matrix_world.translation.copy()
        shown = rig.set_preview(arm, 40)
        bpy.context.view_layer.update()
        self.assertAlmostEqual(shown, 40, places=4)
        self.assertLess((arm.matrix_world.translation - pivot).length, TOL)
        expected = pivot + Matrix.Rotation(math.radians(40), 3, axis) @ (before - pivot)
        self.assertLess((child.matrix_world.translation - expected).length, 1e-4)
        rig.reset_preview(root)
        bpy.context.view_layer.update()
        self.assertLess((child.matrix_world.translation - before).length, TOL)
        self.assertFalse(arm.bii_rig.preview_owned)

    def test_values_are_clamped_to_limits(self):
        crane()
        self.assertAlmostEqual(rig.set_preview("Arm", 500), 60, places=4)
        self.assertAlmostEqual(rig.set_preview("Slider", -9), -0.5, places=5)
        self.assertAlmostEqual(rig.set_preview("Tower", 500), 500, places=3)   # no limits

    def test_slider_spans_the_limit_range(self):
        """The panel's slider (preview_position) covers the whole limit range: 0 % = min, 100 % = max."""
        crane()
        arm, s = bpy.data.objects["Arm"].bii_rig, bpy.data.objects["Slider"].bii_rig
        arm.preview_position = 50
        self.assertAlmostEqual(math.degrees(arm.preview_angle), 15, places=4)
        arm.preview_position = 100
        self.assertAlmostEqual(math.degrees(arm.preview_angle), 60, places=4)
        s.preview_length = 0.75
        self.assertAlmostEqual(s.preview_position, 50, places=4)
        s.preview_position = 0.0
        self.assertAlmostEqual(s.preview_length, -0.5, places=5)
        for o, prop in (("Arm", "preview_angle"), ("Slider", "preview_length"), ("Tower", "preview_angle")):
            p = bpy.data.objects[o].bii_rig.bl_rna.properties[prop]
            self.assertLess(p.soft_max, 1e3, o)       # drag sensitivity follows the soft range

    def test_prismatic_moves_along_world_axis(self):
        crane()
        s = bpy.data.objects["Slider"]
        axis = rig_props.world_axis(s)
        before = s.matrix_world.translation.copy()
        rig.set_preview(s, 1.25)
        bpy.context.view_layer.update()
        self.assertLess((s.matrix_world.translation - (before + axis * 1.25)).length, 1e-4)

    def test_foreign_deltas_block_the_preview(self):
        crane()
        arm = bpy.data.objects["Arm"]
        arm.delta_location = (0, 0, 1)
        with self.assertRaises(RigError):
            rig.set_preview(arm, 10)
        self.assertTrue(any("delta" in p for p in rig.problems("Crane")))

    def test_sweep_pose_stays_in_limits(self):
        crane()
        rig.sweep_pose("Crane", 180)
        self.assertAlmostEqual(math.degrees(bpy.data.objects["Arm"].bii_rig.preview_angle), 60, places=3)
        self.assertAlmostEqual(bpy.data.objects["Slider"].bii_rig.preview_length, 2.0, places=5)


class PivotTest(RigTestCase):
    def test_set_pivot_keeps_geometry_and_children(self):
        crane()
        arm = bpy.data.objects["Arm"]
        child = bpy.data.objects["Slider"]
        rig.set_preview(arm, 20)
        bpy.context.view_layer.update()
        rig.reset_preview("Crane")
        bpy.context.view_layer.update()
        verts = [arm.matrix_world @ v.co for v in arm.data.vertices]
        child_m = child.matrix_world.copy()
        rig.set_preview(arm, 20)
        pivot = rig.set_pivot(arm, (6.5, 3.5, 4.2))
        self.assertLess((Vector(pivot) - Vector((6.5, 3.5, 4.2))).length, 1e-5)
        self.assertAlmostEqual(math.degrees(arm.bii_rig.preview_angle), 20, places=4)
        rig.reset_preview("Crane")
        bpy.context.view_layer.update()
        for a, v in zip(verts, arm.data.vertices):
            self.assertLess((arm.matrix_world @ v.co - a).length, 1e-5)
        self.assertLess(max(abs(x) for r in (child.matrix_world - child_m) for x in r), 1e-5)


class ExportTest(RigTestCase):
    def test_file_layout_and_json(self):
        crane()
        res = rig.export("Crane", self.path)
        self.assertEqual(res["elements"], 6)
        self.assertEqual(len(res["joints"]), 4)
        with zipfile.ZipFile(self.path) as z:
            names = set(z.namelist())
            meta = json.loads(z.read("dProB_asset_metadata.json"))
            model = json.loads(z.read("model.json"))
            guid = z.read("asset_guid.txt").decode()
        self.assertEqual(meta, {"Format": "ScriptableEquipment_v2", "ProductVersion": dequip_format.PRODUCT_VERSION,
                                "Description": "Crane"})                # default: the name
        self.assertEqual(len(guid), 36)
        geo = model["GeometryModels"][0]
        self.assertEqual(geo["$case"], "Fbx")
        self.assertIn(geo["Asset"]["FbxFile"], names)
        self.assertTrue(geo["Asset"]["FbxFile"].startswith(geo["Reference"]["Id"] + "/"))
        root = model["HierarchyRoots"][0]
        self.assertEqual(root["Name"], "Crane")
        self.assertEqual(root["GeometrySource"]["$case"], "Empty")
        p = root["Pose"]["Position"]
        self.assertLess(max(abs(p["X"]), abs(p["Y"]), abs(p["Z"])), 1e-9)   # root origin = equipment origin
        by_name = {e["Name"]: e for e in dequip_format.walk(model["HierarchyRoots"])}
        self.assertEqual(by_name["Base"]["GeometrySource"]["$case"], "ImportedGeometry")
        self.assertEqual(by_name["Base"]["GeometrySource"]["GeometryData"]["$case"], "Fbx")
        self.assertIsNone(by_name["Tower"]["JointConstraint"]["Limits"])
        lim = by_name["Arm"]["JointConstraint"]["Limits"]
        self.assertAlmostEqual(lim["MinRadians"], math.radians(-30), places=5)
        self.assertEqual(by_name["Hook"]["JointConstraint"], {"$case": "Ball"})
        self.assertEqual(by_name["Base"]["JointConstraint"], {"$case": "Fixed"})
        pr = by_name["Slider"]["JointConstraint"]
        self.assertAlmostEqual(pr["MinMeters"], -0.5, places=5)
        self.assertAlmostEqual(pr["MaxMeters"], 2.0, places=5)
        for key in ("RailVehicleData", "GroundVehicleData", "BulkLoadableData", "VehicleCollisionData"):
            self.assertIn(key, model)
            self.assertIsNone(model[key])

    def test_check_matches_preview(self):
        crane()
        rig.set_preview("Tower", 30)
        rig.export("Crane", self.path)
        # the export leaves the preview as it was
        self.assertAlmostEqual(math.degrees(bpy.data.objects["Tower"].bii_rig.preview_angle), 30, places=4)
        self.assertTrue(bpy.data.objects["Tower"].bii_rig.preview_owned)
        res = rig.verify("Crane", samples=6)
        self.assertLess(res["max_error"], 1e-4, res)

    def test_axis_mapping_and_mirror(self):
        """Blender +Z is dProB +Y; a mirror flips the turning sense, so a hinge about Blender +Z
        gets the axis -Y in dProB (same angle, same visible direction). Axes in equipment space;
        in the file they are in the element's frame, which dProB's import turns."""
        root = bpy.data.objects.new("R", None)
        bpy.context.scene.collection.objects.link(root)
        door = cube("Door", (1, 0, 0), root)
        rig.make_rig(root)
        rig.set_joint(door, "REVOLUTE", axis="Z", limits=None)
        rig.set_joint(cube("Slide", (0, 2, 0), root), "PRISMATIC", axis="Y", limits=(0, 1))
        rig.export(root, self.path)
        m = dequip_format.read(self.path).model
        by = {e["Name"]: e for e in dequip_format.walk(m["HierarchyRoots"])}
        worlds = dprob_math.element_world_matrices(m["HierarchyRoots"])

        def world_axis(name):
            a = by[name]["JointConstraint"]["Axis"]
            return worlds[by[name]["Id"]][:3, :3] @ np.array([a["X"], a["Y"], a["Z"]])

        np.testing.assert_allclose(world_axis("Door"), (0, -1, 0), atol=1e-6)
        np.testing.assert_allclose(world_axis("Slide"), (0, 0, 1), atol=1e-6)   # Blender +Y is dProB forward
        np.testing.assert_allclose(worlds[by["Slide"]["Id"]][:3, 3], (0, 0, 2), atol=1e-6)
        np.testing.assert_allclose(worlds[by["Door"]["Id"]][:3, 3], (1, 0, 0), atol=1e-6)

    def test_fbx_vertices_are_object_local(self):
        """dProB draws an element's mesh as the FBX vertices times its scale/shear matrix; the
        check relies on these being the object's local vertices."""
        crane()
        arm = bpy.data.objects["Arm"]
        bevel = arm.modifiers.new("Bevel", "BEVEL")
        bevel.width = 0.05
        rig.export("Crane", self.path)
        dq = dequip_format.read(self.path)
        root = fbx_binary.parse(dq.files[dq.model["GeometryModels"][0]["Asset"]["FbxFile"]])
        models = dprob_math.read_models(root)
        arm_id = next(i for i, m in models.items() if m.name == "Arm")
        geo_id = next(c.props[1] for c in root.first("Connections").all("C")
                      if c.props[0] == "OO" and c.props[2] == arm_id and c.props[1] not in models)
        geom = next(g for g in root.first("Objects").all("Geometry") if g.props[0] == geo_id)
        fbx_verts = np.array(geom.first("Vertices").props[0]).reshape(-1, 3)
        ev = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
        local = np.array([v.co for v in ev.data.vertices])
        self.assertEqual(fbx_verts.shape, local.shape)
        self.assertLess(np.abs(np.sort(fbx_verts, axis=0) - np.sort(local, axis=0)).max(), 1e-5)
        self.assertLess(rig.verify("Crane")["max_error"], 1e-4)

    def test_hidden_parts_are_exported(self):
        crane()
        bpy.data.objects["Hook"].hide_set(True)
        bpy.data.objects["Slider"].hide_viewport = True
        res = rig.export("Crane", self.path)
        self.assertEqual(res["elements"], 6)
        self.assertTrue(bpy.data.objects["Hook"].hide_get())
        self.assertTrue(bpy.data.objects["Slider"].hide_viewport)

    def test_ids_stay_and_copies_get_new_ones(self):
        crane()
        rig.export("Crane", self.path)
        ids1 = {e["Name"]: e["Id"] for e in dequip_format.walk(dequip_format.read(self.path).model["HierarchyRoots"])}
        arm = bpy.data.objects["Arm"]
        copy = arm.copy()
        copy.data = arm.data.copy()
        bpy.context.scene.collection.objects.link(copy)
        copy.parent = arm.parent
        rig.export("Crane", self.path)
        m = dequip_format.read(self.path).model
        ids2 = {e["Name"]: e["Id"] for e in dequip_format.walk(m["HierarchyRoots"])}
        for name, i in ids1.items():
            self.assertEqual(ids2[name], i, name)
        self.assertNotEqual(ids2[copy.name], ids1["Arm"])
        self.assertEqual(len(set(ids2.values())), len(ids2))

    def test_renamed_copy_before_its_original_gets_a_new_id(self):
        """The copy is visited first (its new name sorts before the original's)."""
        crane()
        rig.export("Crane", self.path)
        arm = bpy.data.objects["Arm"]
        arm_id = arm.bii_rig.element_id
        copy = arm.copy()
        copy.data = arm.data.copy()
        bpy.context.scene.collection.objects.link(copy)
        copy.parent = arm.parent
        copy.name = "AArm"
        rig.export("Crane", self.path)
        self.assertEqual(arm.bii_rig.element_id, arm_id)
        self.assertNotEqual(copy.bii_rig.element_id, arm_id)

    def test_export_replaces_the_file(self):
        crane()
        rig.export("Crane", self.path)
        dq = dequip_format.read(self.path)
        dq.model["CustomCapabilities"] = [{"Signature": {"Name": "Swing"}, "Implementation": {}}]
        dq.model["StateVariables"] = [{"Name": "Load"}]
        entry = dq.model["GeometryModels"][0]["Asset"]["FbxFile"]
        dequip_format.write(self.path, dq.model, dq.files[entry], entry)
        equipment_id = dq.model["EquipmentId"]
        rig.export("Crane", self.path)
        m = dequip_format.read(self.path).model
        self.assertEqual(m["EquipmentId"], equipment_id)
        self.assertEqual(m["CustomCapabilities"], [])
        self.assertEqual(m["StateVariables"], [])

    def test_description(self):
        crane()
        rig.set_description("Crane", "Kran\nFähigkeiten:\n- Bodenfahrzeug")
        rig.export("Crane", self.path)
        self.assertEqual(dequip_format.read(self.path).metadata["Description"], "Kran\nFähigkeiten:\n- Bodenfahrzeug")
        bpy.data.objects["Crane"].bii_rig.asset_description = "A\\nB"       # typed in the panel
        rig.export("Crane", self.path)
        self.assertEqual(dequip_format.read(self.path).metadata["Description"], "A\nB")

    def test_errors_stop_export(self):
        crane()
        bpy.data.objects["Slider"].bii_rig.length_min = 5
        with self.assertRaises(RigError):
            rig.export("Crane", self.path)
        self.assertFalse(os.path.exists(self.path))


class TextureTest(RigTestCase):
    def image_file(self, name, fmt, ext, color, float_buffer=False):
        img = bpy.data.images.new(name + "_src", 4, 4, alpha=True, float_buffer=float_buffer)
        img.pixels.foreach_set(list(color) * 16)
        path = os.path.join(self.tmp.name, name + ext)
        img.filepath_raw = path
        img.file_format = fmt
        img.save()
        bpy.data.images.remove(img)
        return bpy.data.images.load(path)

    def material(self, obj, images):
        """Material with the images on Principled BSDF inputs (the FBX exporter takes only those)."""
        m = bpy.data.materials.new(obj.name)
        m.use_nodes = True
        tree = m.node_tree
        bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
        for img, socket in zip(images, ("Base Color", "Roughness", "Metallic")):
            node = tree.nodes.new("ShaderNodeTexImage")
            node.image = img
            tree.links.new(node.outputs["Color"], bsdf.inputs[socket])
        obj.data.materials.append(m)
        return m

    def embedded(self):
        dq = dequip_format.read(self.path)
        root = fbx_binary.parse(dq.files[dq.model["GeometryModels"][0]["Asset"]["FbxFile"]])
        objs = root.first("Objects")
        videos = {v.first("RelativeFilename").props[0]: v.first("Content").props[0] for v in objs.all("Video")}
        textures = [t.first("RelativeFilename").props[0] for t in objs.all("Texture")]
        return videos, textures

    def test_all_textures_embedded_as_png_or_jpeg(self):
        crane()
        png = self.image_file("wood", "PNG", ".png", (0.2, 0.4, 0.6, 1))
        jpg = self.image_file("steel", "JPEG", ".jpg", (0.5, 0.5, 0.5, 1))
        tga = self.image_file("paint", "TARGA", ".tga", (1, 0, 0, 1))
        exr = self.image_file("height", "OPEN_EXR", ".exr", (0.5, 0.5, 0.5, 1), float_buffer=True)
        gen = bpy.data.images.new("decal", 8, 8)
        gen.generated_color = (0, 1, 0, 1)
        self.material(bpy.data.objects["Base"], [png, jpg])
        self.material(bpy.data.objects["Arm"], [tga, gen])
        self.material(bpy.data.objects["Hook"], [exr])
        before = set(bpy.data.images)
        res = rig.export("Crane", self.path)
        self.assertEqual(set(res["textures_converted"]), {"paint.tga", "height.exr", "decal"})
        videos, textures = self.embedded()
        self.assertEqual(len(textures), 5)
        for name in textures:
            self.assertIn(os.path.splitext(name)[1].lower(), {".png", ".jpg", ".jpeg"}, name)
            data = videos[name]
            self.assertTrue(data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8"), name)
        # the materials use their own images again, the substitutes are gone
        self.assertEqual(set(bpy.data.images), before)
        nodes = [n for n in bpy.data.materials["Arm"].node_tree.nodes if n.type == "TEX_IMAGE"]
        self.assertEqual({n.image for n in nodes}, {tga, gen})
        # converted pixels keep their color (linear EXR 0.5 -> sRGB about 0.735)
        tmp = os.path.join(self.tmp.name, "check.png")
        with open(tmp, "wb") as f:
            f.write(videos[next(n for n in textures if n.replace("\\", "/").split("/")[-1].startswith("height"))])
        back = bpy.data.images.load(tmp)
        self.assertAlmostEqual(back.pixels[0], 0.735, delta=0.01)
        with open(tmp, "wb") as f:
            f.write(videos[next(n for n in textures if n.replace("\\", "/").split("/")[-1].startswith("paint"))])
        back.reload()
        self.assertEqual([round(v, 2) for v in back.pixels[:4]], [1.0, 0.0, 0.0, 1.0])

    def test_missing_texture_stops_export(self):
        crane()
        img = self.image_file("gone", "PNG", ".png", (1, 1, 1, 1))
        self.material(bpy.data.objects["Base"], [img])
        img.filepath = os.path.join(self.tmp.name, "nowhere.png")
        img.reload()
        with self.assertRaises(RigError):
            rig.export("Crane", self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(bpy.data.materials["Base"].node_tree.nodes["Image Texture"].image, img)


class ApiTest(RigTestCase):
    def test_describe_and_read(self):
        crane()
        d = rig.describe("Crane")
        self.assertEqual(d["parts"], 6)
        self.assertEqual(sorted(d["joints"]), ["Arm", "Hook", "Slider", "Tower"])
        arm = d["tree"]["children"][0]["children"][0]["children"][0]
        self.assertEqual(arm["object"], "Arm")
        self.assertEqual(arm["joint"], "REVOLUTE")
        self.assertAlmostEqual(arm["limits_deg"][1], 60, places=4)
        rig.export("Crane", self.path)
        r = rig.read_dequip(self.path)
        self.assertEqual(r["metadata"]["Format"], "ScriptableEquipment_v2")
        self.assertEqual(r["hierarchy"][0]["name"], "Crane")
        self.assertEqual(rig.rig_of("Hook"), "Crane")
        self.assertEqual(rig.rigs(), ["Crane"])

    def test_bad_input(self):
        crane()
        with self.assertRaises(ValueError):
            rig.set_joint("Arm", "HINGE")
        with self.assertRaises(ValueError):
            rig.set_joint("Arm", "REVOLUTE", axis="W")
        with self.assertRaises(ValueError):
            rig.set_joint("Slider", "PRISMATIC", limits=None)
        with self.assertRaises(RigError):
            rig.set_joint("Nope", "FIXED")
        loose = cube("Loose", (0, 0, 0))
        with self.assertRaises(RigError):
            rig.set_joint(loose, "REVOLUTE")
        with self.assertRaises(RigError):
            rig.make_rig("Arm")   # inside the crane's rig


class OperatorTest(RigTestCase):
    def test_operators(self):
        root = bpy.data.objects.new("Box", None)
        bpy.context.scene.collection.objects.link(root)
        lid = cube("Lid", (0, 0, 1), root)
        bpy.context.view_layer.objects.active = root
        self.assertEqual(bpy.ops.bii_rig.make_root(), {"FINISHED"})
        lid.bii_rig.joint_type = "REVOLUTE"
        lid.bii_rig.axis = "X"
        bpy.context.view_layer.objects.active = lid
        self.assertEqual(bpy.ops.bii_rig.export_as(filepath=self.path), {"FINISHED"})
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(bpy.ops.bii_rig.check(), {"FINISHED"})
        lid.bii_rig.preview_angle = 0.5
        self.assertEqual(bpy.ops.bii_rig.reset_preview(), {"FINISHED"})
        self.assertEqual(lid.bii_rig.preview_angle, 0.0)

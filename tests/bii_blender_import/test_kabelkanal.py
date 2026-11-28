"""Tests of plan_api.kabelkanal_from_lines inside Blender: two sheets' trough lines merged into one
edge mesh with the Schacht attributes and the Kabelkanäle modifier.

Runs only in Blender, see run_in_blender.py. Skipped elsewhere.
"""

import math
import unittest

try:
    import bpy
except ImportError as e:  # pragma: no cover
    raise unittest.SkipTest(f"needs Blender: {e}")

from bii_blender_import import plan_api  # noqa: E402

# two overlapping sheets: the same trough along x (0.3 m apart where they overlap), a branch on
# the second sheet that ends 1 m short of the trough
SHEET_A = [[(0.0, 0.0), (60.0, 0.0)]]
SHEET_B = [[(40.0, 0.3), (120.0, 0.3)], [(80.0, 30.0), (80.0, 1.3)]]
MARKS = [((20.0, 0.5), (1.0, 1.6)), ((20.4, 0.4), None), ((100.0, 0.0), None)]


def fake_node_group():
    """a stand-in for the asset library's Kabelkanäle group, with its inputs"""
    group = bpy.data.node_groups.new(plan_api.KABEL_GROUP, 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Generate Schotter", in_out='INPUT', socket_type='NodeSocketBool')
    group.interface.new_socket("Schacht Size", in_out='INPUT', socket_type='NodeSocketVector')
    group.interface.new_socket("Schacht Rotation", in_out='INPUT', socket_type='NodeSocketFloat')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    return group


class KabelkanalTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def test_merge_and_attributes(self):
        fake_node_group()
        out = plan_api.kabelkanal_from_lines(SHEET_A + SHEET_B, MARKS, z=-1.0)
        obj = out["object"]
        self.assertTrue(out["modifier"], out["note"])
        self.assertAlmostEqual(obj.location.z, -1.0)
        # 120 m along x once (not 140 with the overlap twice), plus the branch snapped onto it
        self.assertLess(abs(out["length_m"] - (120 + 30)), 2.0)
        me = obj.data
        ends = [v.co for v in me.vertices if sum(v.index in e.vertices for e in me.edges) == 1]
        self.assertFalse([c for c in ends if 70 < c.x < 90 and abs(c.y) < 2],
                         "the branch end lies on the trough, not 1 m short of it")
        size = me.attributes["Schacht"].data
        rot = me.attributes["Schacht Rotation"].data
        pits = [(me.vertices[i].co, size[i].vector, rot[i].value) for i in range(len(me.vertices))
                if size[i].vector.length > 0]
        self.assertEqual(len(pits), 2)  # the two marks at x = 20 are one Schacht
        first = min(pits, key=lambda p: p[0].x)
        self.assertLess(abs(first[0].y), 0.4)            # on the trough
        self.assertAlmostEqual(first[1].x, 1.0)          # its size from the quad
        self.assertAlmostEqual(first[1].y, 1.6)
        self.assertAlmostEqual(first[1].z, plan_api.SCHACHT_HEIGHT)
        self.assertLess(abs(math.sin(first[2])), 0.05)   # turned along the trough
        mod = obj.modifiers[0]
        self.assertEqual(mod.node_group.name, plan_api.KABEL_GROUP)
        ids = {s.name: s.identifier for s in mod.node_group.interface.items_tree if s.in_out == 'INPUT'}
        props = getattr(mod, "properties", None)
        for name, attribute in (("Schacht Size", "Schacht"), ("Schacht Rotation", "Schacht Rotation")):
            if props is not None and hasattr(props, "inputs"):  # Blender 5.2+
                item = getattr(props.inputs, ids[name])
                self.assertEqual((item.type, item.attribute_name), ('ATTRIBUTE', attribute))
            else:
                self.assertTrue(mod[ids[name] + "_use_attribute"])
                self.assertEqual(mod[ids[name] + "_attribute_name"], attribute)

    def test_without_library(self):
        out = plan_api.kabelkanal_from_lines(SHEET_A, [], name="KK")
        self.assertFalse(out["modifier"])
        self.assertIn("asset library", out["note"])
        self.assertEqual(len(out["object"].modifiers), 0)


if __name__ == "__main__":
    unittest.main()

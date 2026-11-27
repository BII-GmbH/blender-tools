"""Blender tests for joint couplings (Drive), the IK solver and the generated IK capabilities.

Run: blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_tools/run_in_blender.py
"""

import json
import math
import os
import random
import tempfile
import unittest
import uuid

try:
    import bpy
except ImportError:
    raise unittest.SkipTest("needs Blender")

import numpy as np
from mathutils import Matrix

import bii_blender_tools
from bii_blender_tools.equipment import (dequip_format, dprob_math, ik_capability, ik_chain, ik_solver,
                                   rig_api as rig, rig_props)
from bii_blender_tools.equipment.rig_props import RigError

from test_rig import cube, crane

TOL = 1e-5


def setUpModule():
    bii_blender_tools.register()


def tearDownModule():
    bii_blender_tools.unregister()


def truck_crane():
    """Boom crane like the crane test: slewing cab, luffing boom, telescope, pitch reversal at
    the boom tip (follows the boom angle) and a rope (prismatic) to the hook."""
    root = bpy.data.objects.new("Truck", None)
    bpy.context.scene.collection.objects.link(root)
    bpy.context.view_layer.update()
    cab = cube("Cab", (0, 0, 2), root)
    boom = cube("Boom", (0, 1, 3), cab, scale=(0.5, 3, 0.5))
    tele = cube("Tele", (0, 1, 3), boom)
    rev = cube("Reversal", (0, -6, 3), tele, size=0.3)
    hook = cube("Hook", (0, -6, 3), rev, size=0.5)
    point = cube("HookPoint", (0, -6, 2.6), hook, size=0.1)
    rig.make_rig(root)
    rig.set_joint(cab, "REVOLUTE", axis="Z", limits=None)
    rig.set_joint(boom, "REVOLUTE", axis="-X", limits=(0, 80))
    rig.set_joint(tele, "PRISMATIC", axis="-Y", limits=(0, 10))
    rig.set_joint(rev, "REVOLUTE", axis="X", limits=(0, 80))
    rig.set_joint(hook, "PRISMATIC", axis="-Z", limits=(0, 20))
    rig.set_joint(point, "FIXED", attachment_point=True)
    rig.set_follow(rev, [(boom, 1.0)])
    return root


class IkTestCase(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "crane.dequip")

    def tearDown(self):
        self.tmp.cleanup()


class SpaceTest(IkTestCase):
    def check_rig(self, root, samples=4):
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        space = ik_chain.Space(root)
        rest = dprob_math.element_world_matrices(model["HierarchyRoots"])
        for o in rig_props.rig_objects(root):
            self.assertLess(np.abs(space.rest_pose(o) - rest[ids[o.name]]).max(), TOL, o.name)
        # joints from Blender == joints dProB reads from the file, for random states
        joints, parents, _ = ik_solver.rig_from_model(model["HierarchyRoots"])
        rng = random.Random(3)
        jointed = rig_props.joints(root)
        for _ in range(samples):
            states = {}
            for o in jointed:
                j = o.bii_rig
                if j.joint_type == "BALL":
                    v = tuple(rng.uniform(-1, 1) for _ in range(3))
                else:
                    lim = rig_props.limits(j) or (-math.pi, math.pi)
                    v = rng.uniform(*lim)
                states[o] = space.to_dprob(o, v)
            worlds = dprob_math.element_world_matrices(
                model["HierarchyRoots"], {ids[o.name]: v for o, v in states.items()})
            for o in jointed:
                chain = ik_solver.chain_to(joints, parents, ids[o.name])
                _, ends = ik_solver.forward(chain, [states[bpy.data.objects[n]] for n in
                                                    (self.name_of(model, j.key) for j in chain)])
                self.assertLess(np.abs(ends[-1] - worlds[ids[o.name]]).max(), TOL, o.name)
        return model

    @staticmethod
    def name_of(model, element_id):
        return next(e["Name"] for e in dequip_format.walk(model["HierarchyRoots"]) if e["Id"] == element_id)

    def test_space_matches_export(self):
        self.check_rig(crane())

    def test_space_matches_export_truck(self):
        self.check_rig(truck_crane())

    def test_blender_chain_equals_file_chain(self):
        """Setup's joints (from Blender) and the file's joints give the same IK result."""
        root = truck_crane()
        model = self.check_rig(root)
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        setup = ik_chain.Setup("HookPoint")
        joints, parents, fixed = ik_solver.rig_from_model(model["HierarchyRoots"])
        chain = ik_solver.chain_to(joints, parents, ids["Hook"])
        offset, joint_id = fixed[ids["HookPoint"]]
        self.assertEqual(joint_id, ids["Hook"])
        # a Destination: the part's frame in the equipment
        target = setup.destination(np.array(Matrix.Translation((2, -4, 1)) @ Matrix.Rotation(0.3, 4, "Z")))
        # the joint's target from the file: offset turned like the part is now (at rest)
        _, rest_ends = ik_solver.forward(chain, [0.0] * len(chain))
        inv = np.linalg.inv(offset)
        turned = np.eye(4)
        turned[:3, 3] = target[:3, 3]
        turned[:3, :3] = (rest_ends[-1] @ offset)[:3, :3]
        dest = target @ inv
        dest[:3, 3] = (turned @ inv)[:3, 3]
        start = [0.0] * len(chain)
        file_values = ik_solver.solve(chain, dest, start, 100, 0.05)
        blender_values = ik_solver.solve(setup.chain, setup.joint_destination(target), start, 100, 0.05)
        np.testing.assert_allclose(file_values, blender_values, atol=1e-6)


class FollowTest(IkTestCase):
    def test_follower_tracks_leader(self):
        truck_crane()
        rig.set_preview("Boom", 30)
        self.assertAlmostEqual(rig.describe_joint("Reversal")["preview_deg"], 30, places=5)
        rig.set_follow("Reversal", [("Boom", 1.0)], offset=5)
        self.assertAlmostEqual(rig.describe_joint("Reversal")["preview_deg"], 35, places=5)
        bpy.context.view_layer.update()
        # the hook keeps its world orientation (pitch reversal) up to the 5 degree offset
        hook = bpy.data.objects["Hook"].matrix_world.to_3x3().normalized()
        self.assertAlmostEqual(math.degrees(hook.to_euler().x), 5, places=3)

    def test_constant_and_hold(self):
        truck_crane()
        rig.set_follow("Tele", [], offset=2.5)
        self.assertAlmostEqual(rig.describe_joint("Tele")["preview_m"], 2.5, places=5)
        rig.set_hold("Cab")
        self.assertEqual(rig.describe_joint("Cab")["drive"], "HOLD")
        rig.set_free("Tele")
        self.assertEqual(rig.describe_joint("Tele")["drive"], "FREE")

    def test_cycle_and_type_mismatch(self):
        truck_crane()
        with self.assertRaises(RigError):
            rig.set_follow("Boom", [("Reversal", 1.0)])
        with self.assertRaises(RigError):
            rig.set_follow("Tele", [("Boom", 1.0)])


class SolverTest(IkTestCase):
    def test_reaches_nearby_pose(self):
        """From a start near a reachable pose the solver gets there (position and orientation)."""
        crane()
        setup = ik_chain.Setup("Hook")
        axis = np.array([0.3, 0.5, 0.8]) / np.linalg.norm([0.3, 0.5, 0.8])
        rng = random.Random(5)
        for _ in range(3):
            values, start = [], []
            for j in setup.chain:
                if j.kind == "BALL":
                    a = rng.uniform(-0.5, 0.5)
                    values.append(ik_solver.axis_angle(axis, a))
                    start.append(ik_solver.axis_angle(axis, a + 0.2))
                else:
                    lo, hi = j.limits or (-1, 1)
                    v = rng.uniform(lo + 0.2, hi - 0.2)
                    values.append(v)
                    start.append(v + 0.15)
            _, ends = ik_solver.forward(setup.chain, values, setup.base())
            got = ik_solver.solve(setup.chain, ends[-1], start, 300, 0.01, setup.base())
            pos_err, rot_err = ik_solver.residual(setup.chain, ends[-1], {j.key: v for j, v in zip(setup.chain, got)},
                                                  setup.base())
            self.assertLess(pos_err, 1e-3)
            self.assertLess(rot_err, 0.1)

    def test_orientation_error_in_degrees_like_dprob(self):
        """dProB's node weights the orientation error in degrees: a pure 1 degree error with
        weight 1 is 1, not 0.0175."""
        e = ik_solver.error(np.eye(4), np.block([[ik_solver.axis_angle(np.array([0, 0, 1.0]), math.radians(1)),
                                                 np.zeros((3, 1))], [np.zeros((1, 3)), np.ones((1, 1))]]), 1.0)
        self.assertAlmostEqual(float(np.linalg.norm(e[3:])), 1.0, places=6)

    def test_pitch_reversal_keeps_hook_level(self):
        truck_crane()
        rig.set_ik_target("HookPoint", name="Move Hook", orientation_weight=0.001, loops=10, iterations=100)
        start = rig.describe_joint("HookPoint")
        res = rig.solve_ik("HookPoint", position_world=(0, -2, 9))
        self.assertLess(res["position_error_m"], 0.01)
        self.assertAlmostEqual(res["values"]["Reversal"], res["values"]["Boom"], places=4)
        bpy.context.view_layer.update()
        hp = bpy.data.objects["HookPoint"].matrix_world
        self.assertLess((hp.translation - Matrix.Translation((0, -2, 9)).translation).length, 0.01)
        self.assertLess(abs(hp.to_3x3().normalized().to_euler().x), 1e-3)
        rig.reset_preview("Truck")

    def test_hold_keeps_joint(self):
        truck_crane()
        rig.set_preview("Cab", 20)
        rig.set_hold("Cab")
        rig.set_ik_target("HookPoint")
        res = rig.solve_ik("HookPoint", position_world=(3, 2, 6))
        self.assertAlmostEqual(res["values"]["Cab"], 20, places=4)


class CapabilityTest(IkTestCase):
    def test_capability_graph(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", name="Move Hook", orientation_weight=0.001)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        caps = model["CustomCapabilities"]
        self.assertEqual([c["Signature"]["Name"] for c in caps], ["Move Hook"])
        sig, impl = caps[0]["Signature"], caps[0]["Implementation"]
        self.assertEqual([i["Name"].get("Text") or i["Name"].get("LocalizationKey") for i in sig["Inputs"]],
                         ["BII.ScriptableTemplates.Capabilities.SelfInput", "Destination", "Duration",
                          "Iterations", "Orientation Weight", "Start Away from Limits", "Position Only", "Passes"])
        defaults = {i["Name"].get("Text"): i["DefaultValue"] for i in sig["Inputs"]}
        self.assertAlmostEqual(defaults["Orientation Weight"]["Value"], 0.001, places=6)
        g = impl["GraphData"]
        node_ids = {(n.get("Node", n))["Id"]["Guid"] for n in g["Nodes"]}
        node_ids |= {g["FirstNodeId"]["Guid"]} | {e["Guid"] for e in impl["EndNodeIds"]}
        for c in g["DataConnections"]:
            self.assertIn(c["FromNode"]["Guid"], node_ids)
            self.assertIn(c["ToNode"]["Guid"], node_ids)
        for c in g["ExecutionConnections"]:
            self.assertIn(c["FromNode"]["Guid"], node_ids)
            self.assertIn(c["ToNode"]["Guid"], node_ids)
        cases = [(n.get("Node", n))["$case"] for n in g["Nodes"]]
        self.assertIn("InverseKinematicsNode", cases)
        self.assertIn(ik_capability.LOOP_RANGE_MACRO, [n.get("MacroId") for n in g["Nodes"]])  # coupling loop
        # joint getters use the element id with bit 0 flipped
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        getters = {n["CachedJointName"]: n["JointId"] for n in g["Nodes"] if n.get("$case") == "JointGetter"}
        b = bytearray(uuid.UUID(ids["Boom"]).bytes_le)
        b[0] ^= 1
        self.assertEqual(getters["Boom"], str(uuid.UUID(bytes_le=bytes(b))))
        # the capability id stays over exports
        cap_id = sig["Id"]["Guid"]
        rig.export(root, self.path)
        self.assertEqual(dequip_format.read(self.path).model["CustomCapabilities"][0]["Signature"]["Id"]["Guid"], cap_id)

    def test_no_loop_without_couplings(self):
        root = crane()
        rig.set_ik_target("Hook", name="Reach")
        rig.export(root, self.path)
        g = dequip_format.read(self.path).model["CustomCapabilities"][0]["Implementation"]["GraphData"]
        macros = [n.get("MacroId") for n in g["Nodes"]]
        self.assertNotIn(ik_capability.LOOP_RANGE_MACRO, macros)
        self.assertIn("MoveBallJointNode", [n.get("Node", {}).get("$case") for n in g["Nodes"]])

    def test_simulated_graph_equals_preview(self):
        """Runs the generated graph with a small interpreter and the ported IK node."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", name="Move Hook", orientation_weight=0.001)
        rig.set_preview("Cab", 10)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        setup = ik_chain.Setup("HookPoint")
        dest = setup.destination(np.array(Matrix.Translation((1, -3, 8))))
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        state = {ids[n]: v for n, v in setup.start_values.items()}
        moves = ik_capability.simulate(cap, model, dest, state)
        expected, _, _ = setup.solve(dest)
        for name in ("Cab", "Boom", "Tele", "Reversal", "Hook"):
            self.assertAlmostEqual(moves[ids[name]], float(expected[name]), places=5, msg=name)

    def test_disabled_target_has_no_capability(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", name="Move Hook")
        rig.export(root, self.path)
        names = [c["Signature"]["Name"] for c in dequip_format.read(self.path).model["CustomCapabilities"]]
        self.assertEqual(names, ["Move Hook"])
        rig.set_ik_target("HookPoint", enabled=False)
        rig.export(root, self.path)
        self.assertEqual(dequip_format.read(self.path).model["CustomCapabilities"], [])


class SessionTest(IkTestCase):
    def test_session_solves_and_restores(self):
        from bii_blender_tools.equipment import ik_mode
        truck_crane()
        rig.set_preview("Cab", 15)
        rig.set_ik_target("HookPoint", orientation_weight=0.001)
        s = ik_mode.IkSession(bpy.data.objects["HookPoint"])
        start = s.target.copy()
        goal = start.copy()
        goal.translation = (1, -3, 7)
        s.set_target(goal)
        values, pos_err, _, _ = s.wait()
        self.assertLess(pos_err, 0.01)
        bpy.context.view_layer.update()
        self.assertLess((bpy.data.objects["HookPoint"].matrix_world.translation - goal.translation).length, 0.01)
        self.assertIn("off by", s.status())
        # a second target while nothing runs starts at once; results only for the latest target
        goal.translation = (0, -4, 6)
        s.set_target(goal)
        s.wait()
        self.assertEqual(s.solved_revision, s.revision)
        s.end()
        self.assertAlmostEqual(rig.describe_joint("Cab")["preview_deg"], 15, places=4)
        self.assertAlmostEqual(rig.describe_joint("Boom")["preview_deg"], 0, places=6)


class _Layout:
    """Stands in for a panel layout: checks that every drawn property exists."""

    def __init__(self, test):
        self.test = test
        self.enabled = self.active = True
        self.scale_x = self.scale_y = 1.0

    def __getattr__(self, name):
        if name in ("row", "column", "box", "split"):
            return lambda *a, **k: _Layout(self.test)
        if name == "prop":
            def prop(data, prop_name, **k):
                self.test.assertTrue(hasattr(data, prop_name), prop_name)
            return prop
        if name == "operator":
            def op(idname, **k):
                mod, fn = idname.split(".")
                self.test.assertIn(fn, dir(getattr(bpy.ops, mod)), idname)
                return type("Props", (), {})()
            return op
        return lambda *a, **k: None


class PanelTest(IkTestCase):
    def test_panels_draw(self):
        from bii_blender_tools.equipment import ik_mode, rig_ui
        truck_crane()
        rig.set_ik_target("HookPoint")
        rig.set_hold("Cab")
        ctx = type("Ctx", (), {})()
        for name in ("Reversal", "Cab", "HookPoint", "Truck"):
            obj = bpy.data.objects[name]
            ctx.object = obj
            ctx.selected_objects = [obj]
            for active in (False, True):
                ik_mode.session = ik_mode.IkSession(bpy.data.objects["HookPoint"]) if active else None
                if active:
                    ik_mode.session.wait()
                for panel in (rig_ui.VIEW3D_PT_bii_rig, rig_ui.VIEW3D_PT_bii_rig_joint,
                              rig_ui.VIEW3D_PT_bii_rig_ik, rig_ui.VIEW3D_PT_bii_rig_export):
                    p = panel.__new__(panel) if False else type("P", (), {})()
                    p.layout = _Layout(self)
                    if hasattr(panel, "draw_header"):
                        panel.draw_header(p, ctx)
                    panel.draw(p, ctx)
                if active:
                    ik_mode.session.end()
                ik_mode.session = None


class SeedTest(IkTestCase):
    def test_flat_boom_rises_to_reach_closer(self):
        """A boom resting flat on its lower limit must rise to reach a point closer to the
        crane. From the limit the solver stalls; starting away from the limits it gets there."""
        truck_crane()
        target = (0, -2, 1)
        rig.set_ik_target("HookPoint", orientation_weight=0.001, seed_margin=0.0)
        stalled = rig.solve_ik("HookPoint", position_world=target)
        rig.reset_preview("Truck")
        rig.set_ik_target("HookPoint", seed_margin=0.1)
        res = rig.solve_ik("HookPoint", position_world=target)
        self.assertGreater(stalled["position_error_m"], 0.5)
        self.assertLess(res["position_error_m"], 0.01)
        self.assertGreater(res["values"]["Boom"], 30)

    def test_seeds_in_graph(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.001, seed_margin=0.1)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        macros = [n.get("MacroId") for n in cap["Implementation"]["GraphData"]["Nodes"]]
        self.assertIn(ik_capability.CLAMP_MACRO, macros)
        setup = ik_chain.Setup("HookPoint")
        dest = setup.destination(np.array(Matrix.Translation((0, -2, 1))))
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        moves = ik_capability.simulate(cap, model, dest, {ids[n]: v for n, v in setup.start_values.items()})
        expected, pos_err, _ = setup.solve(dest)
        self.assertLess(pos_err, 0.01)
        for name in ("Boom", "Tele", "Hook"):
            self.assertAlmostEqual(moves[ids[name]], float(expected[name]), places=5, msg=name)


class GraphFormTest(IkTestCase):
    def spec(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", name="Move Hook", orientation_weight=0.001)
        rig_props.ensure_ids(root)
        setup = ik_chain.Setup("HookPoint")
        return setup.capability_spec({o: o.bii_rig.element_id for o in rig_props.rig_objects(root)})

    def test_rig_values_come_from_nodes(self):
        """Limits and the attachment point's offset are read in the graph, not written as numbers."""
        cap = ik_capability.build(self.spec())
        g = cap["Implementation"]["GraphData"]
        cases = [(n.get("Node", n))["$case"] for n in g["Nodes"]]
        for case in ("GetRevoluteJointConstraintsNode", "GetPrismaticJointConstraintsNode",
                     "AttachmentPointGetLocalPoseNode", "InvertNode"):
            self.assertIn(case, cases)
        fixed_cases = {f["Value"]["$case"] for f in g["FixedInputs"]}
        self.assertNotIn("LocalPose", fixed_cases)
        # numbers left: loop settings, and the coupling factor (Reversal follows Boom x 1)
        units = [f for f in g["FixedInputs"] if f["Value"]["$case"] == "ValueWithUnit"]
        self.assertEqual(units, [])
        doubles = [f["Value"]["Value"] for f in g["FixedInputs"] if f["Value"]["$case"] == "Double"]
        self.assertEqual(doubles, [1.0])

    def test_layout_has_no_overlaps(self):
        spec = self.spec()
        for global_destination in (False, True):
            with self.subTest(global_destination=global_destination):
                self._check_layout({**spec, "global_destination": global_destination})

    def _check_layout(self, spec):
        g, cap = ik_capability.build_graph(spec)
        self.assertEqual(ik_capability.overlaps(g), [])
        meta = cap["Implementation"]["GraphData"]["EditorMetadata"]
        self.assertEqual(len(meta), len(g.nodes))
        # execution flows left to right on its row
        xs = {m["Node"]["Guid"]: m["Xpos"] for m in meta}
        for c in cap["Implementation"]["GraphData"]["ExecutionConnections"]:
            if c["SuccessionSlotId"]["Guid"] != ik_capability.LOOP_DONE:
                self.assertLess(xs[c["FromNode"]["Guid"]], xs[c["ToNode"]["Guid"]])
        # data flows left to right
        for c in cap["Implementation"]["GraphData"]["DataConnections"]:
            self.assertLess(xs[c["FromNode"]["Guid"]], xs[c["ToNode"]["Guid"]] + 1e-6)
        self.assertTrue(cap["Implementation"]["GraphData"]["Frames"])


class TypeConstraintTest(IkTestCase):
    def test_unwired_generic_ports_have_types(self):
        """dProB takes a generic port's type from its wire; a fixed value on an unwired Mul/Add
        port needs a type constraint, else the editor shows "Set Type..." and drops the value."""
        root = truck_crane()
        rig.set_follow("Reversal", [("Boom", 1.0)], offset=3)
        rig.set_follow("Tele", [], offset=1.5)        # a constant: Add with two fixed inputs
        rig.set_ik_target("HookPoint", orientation_weight=0.001)
        rig.export(root, self.path)
        g = dequip_format.read(self.path).model["CustomCapabilities"][0]["Implementation"]["GraphData"]
        cases = {(n.get("Node", n))["Id"]["Guid"]: (n.get("Node", n))["$case"] for n in g["Nodes"]}
        typed = {(c["Node"]["Guid"], c["InputSlotId"]["Guid"]): c["Type"] for c in g["InputPortTypeConstraints"]}
        generic = [f for f in g["FixedInputs"] if cases[f["Node"]["Guid"]] in ("MulNode", "AddNode")]
        self.assertTrue(generic)
        for f in generic:
            self.assertIn((f["Node"]["Guid"], f["InputSlotId"]["Guid"]), typed)
        self.assertIn({"Type": "Double", "$case": "Atomic"}, list(typed.values()))


class DestinationTest(IkTestCase):
    def test_identity_rotation_reaches_position(self):
        """A Destination with identity rotation (as dProB's gizmos and Move Arm give it) puts the
        attachment point at the position, with orientation weight 0 too. Regression: the point's
        offset from its joint was turned by the Destination's rotation instead of the part's."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.0, iterations=500)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        dest = np.eye(4)
        dest[:3, 3] = (0.5, 3.0, -4.0)           # equipment space: x, up, forward
        state = {k: (np.eye(3) if j.kind == "BALL" else 0.0)
                 for k, j in ik_solver.rig_from_model(model["HierarchyRoots"])[0].items()}
        moves = ik_capability.simulate(cap, model, dest, state)
        joints, parents, fixed = ik_solver.rig_from_model(model["HierarchyRoots"])
        offset, end = fixed[ids["HookPoint"]]
        chain = ik_solver.chain_to(joints, parents, end)
        _, ends = ik_solver.forward(chain, [moves.get(c.key, state[c.key]) for c in chain])
        point = (ends[-1] @ offset)[:3, 3]
        self.assertLess(float(np.linalg.norm(point - dest[:3, 3])), 0.01)
        # Blender agrees: the preview puts the point there
        setup = ik_chain.Setup("HookPoint")
        values, pos_err, _ = setup.solve(dest)
        self.assertLess(pos_err, 0.01)
        for name in ("Boom", "Tele", "Hook"):
            self.assertAlmostEqual(moves[ids[name]], float(values[name]), places=5, msg=name)
        rig.reset_preview(root)

    def test_moves_loop_over_dictionaries(self):
        root = truck_crane()
        rig.set_hold("Cab")
        rig.set_ik_target("HookPoint", orientation_weight=0.0)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        g = cap["Implementation"]["GraphData"]
        macros = [n.get("MacroId") for n in g["Nodes"]]
        self.assertEqual(macros.count(ik_capability.FOR_EACH_DICT_MACRO), 2)   # revolute, prismatic
        cases = [(n.get("Node", n))["$case"] for n in g["Nodes"]]
        self.assertEqual(cases.count("LocalFunction") + cases.count("MovePrismaticJointNode"), 2)
        self.assertEqual([f["Name"] for f in cap["Implementation"]["LocalFunctions"]], ["Rotate Joint"])
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        dest = np.eye(4)
        dest[:3, 3] = (0.5, 3.0, -4.0)
        moves = ik_capability.simulate(cap, model, dest, state)
        self.assertNotIn(ids["Cab"], moves)                  # held: no process
        self.assertEqual(set(moves), {ids[n] for n in ("Boom", "Tele", "Reversal", "Hook")})


class CraneTypeTest(IkTestCase):
    def test_crane_type_from_hook(self):
        from bii_blender_tools.equipment import crane_type
        root = truck_crane()
        rig.set_crane_hook(root, "HookPoint")
        res = rig.export(root, self.path)
        self.assertEqual(res["crane"]["hook"], "HookPoint")
        model = dequip_format.read(self.path).model
        types = model["EquipmentTypeImplementations"]
        self.assertEqual([t["EquipmentType"] for t in types], [crane_type.CRANE_TYPE])
        impls = {i["CapabilityId"]["Guid"]: i["Implementation"] for i in types[0]["Implementations"]}
        self.assertEqual(set(impls), {crane_type.MOVE_ARM, crane_type.LIFT, crane_type.DELIVER})
        text = json.dumps(types)
        self.assertNotIn("{{", text)
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        point = ik_capability.attachment_point_id(ids["HookPoint"])
        lift_points = {n["AttachmentPointId"] for n in impls[crane_type.LIFT]["GraphData"]["Nodes"]
                       if n.get("$case") == "AttachmentPointGetter"}
        self.assertEqual(lift_points, {point})
        # Move Arm invokes the hook's IK capability with its own inputs
        cap = model["CustomCapabilities"][0]
        call = next(n for n in impls[crane_type.MOVE_ARM]["GraphData"]["Nodes"] if n.get("$case") == "CapabilityInvocation")
        self.assertEqual(call["Collection"], {"EquipmentId": model["EquipmentId"], "$case": "Custom"})
        self.assertEqual(call["CapabilityId"]["Guid"], cap["Signature"]["Id"]["Guid"])
        cap_inputs = {i["Id"]["Guid"] for i in cap["Signature"]["Inputs"]}
        g = impls[crane_type.MOVE_ARM]["GraphData"]
        for c in g["DataConnections"]:
            if c["ToNode"]["Guid"] == call["Id"]["Guid"]:
                self.assertIn(c["InputSlotId"]["Guid"], cap_inputs)
        self.assertEqual(len(g["FixedInputs"]), 5)       # the IK settings (the capability's defaults)
        fixed = {f["InputSlotId"]["Guid"]: f["Value"]["Value"] for f in g["FixedInputs"]}
        names = {i["Id"]["Guid"]: i["Name"].get("Text") for i in cap["Signature"]["Inputs"]}
        self.assertIs({names[k]: v for k, v in fixed.items()}["Position Only"], False)
        # Lift puts the hook above the cargo turned like the cargo (not identity)
        lift = impls[crane_type.LIFT]["GraphData"]
        cases = {(n.get("Node", n))["Id"]["Guid"]: (n.get("Node", n))["$case"] for n in lift["Nodes"]}
        into_pack = [c for c in lift["DataConnections"] if cases.get(c["ToNode"]["Guid"]) == "PackPoseNode"
                     and c["InputSlotId"]["Guid"] == ik_capability.port("Rotation")]
        self.assertEqual(len(into_pack), 1)
        self.assertEqual(cases[into_pack[0]["FromNode"]["Guid"]], "ReadCurrentPoseNode")
        self.assertEqual(into_pack[0]["OutputSlotId"]["Guid"], ik_capability.port("Rotation"))
        self.assertNotIn("IdentityNode", json.dumps(into_pack))
        # the hook point has the equipment's axes
        self.assertLess(ik_chain.misalignment_deg(bpy.data.objects["HookPoint"], root), 1e-3)
        names = [v["Name"] for v in model["StateVariables"]]
        self.assertEqual(sorted(names), ["Current Cargo", "Current Cargo Hook Offset"])
        # again: nothing doubles
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        self.assertEqual(len(model["StateVariables"]), 2)
        self.assertEqual(len(model["EquipmentTypeImplementations"]), 1)

    def test_no_crane_type_after_hook_removed(self):
        root = truck_crane()
        rig.set_crane_hook(root, "HookPoint")
        rig.export(root, self.path)
        self.assertEqual(len(dequip_format.read(self.path).model["EquipmentTypeImplementations"]), 1)
        rig.set_crane_hook(root, None)
        rig.set_ik_target("HookPoint", enabled=False)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        self.assertEqual(model["EquipmentTypeImplementations"], [])
        self.assertEqual(model["StateVariables"], [])

    def test_hook_without_ik_is_an_error(self):
        root = truck_crane()
        rig.set_crane_hook(root, "HookPoint")
        rig.set_ik_target("HookPoint", enabled=False)
        self.assertTrue(any("crane hook" in p for p in rig.problems(root)))
        with self.assertRaises(RigError):
            rig.export(root, self.path)


class CraneRotationTest(IkTestCase):
    def test_hook_rotation_from_cargo_offset(self):
        """Deliver passes the hook's own frame rotation (target * inverse(cargo offset)). The crane
        must not swing to turn the hook. Regression: the rotation was taken relative to the hook
        as modeled, so the hook's own rotation counted twice and the cab turned away."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.01)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        setup = ik_chain.Setup("HookPoint")
        dest = setup.current_pose().copy()           # the hook's frame, as dProB's poses give it
        dest[:3, 3] += (0.0, 2.0, -1.5)              # up and closer
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        moves = ik_capability.simulate(cap, model, dest, state)
        self.assertLess(abs(moves[ids["Cab"]]), 0.01)
        values, pos_err, rot_err = setup.solve(dest)
        self.assertLess(pos_err, 0.01)
        self.assertAlmostEqual(moves[ids["Boom"]], float(values["Boom"]), places=5)

    def test_unlimited_joint_takes_short_way(self):
        """Revolute moves go through the sample's Rotate Joint function: an unlimited joint at
        350 degrees moves to 370, not back to 10."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.0)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        state[ids["Cab"]] = math.radians(350)
        sim = ik_capability._Sim(model, state, None)
        hook = sim.point_pose(ids["HookPoint"])
        turn = np.eye(4)
        turn[:3, :3] = ik_solver.axis_angle(np.array([0, 1.0, 0]), math.radians(-20))
        dest = turn @ hook                       # the hook swung 20 degrees about the slewing axis
        moves = ik_capability.simulate(cap, model, dest, state)
        self.assertGreater(math.degrees(moves[ids["Cab"]]), 300)


class CraneMoveArmTest(IkTestCase):
    def test_lift_identity_rotation_reaches_position(self):
        """An upright rotation the hook cannot turn to (no swivel) costs the position almost
        nothing: the hook point has the equipment's axes, so it is no tilt. Regression: Lift missed
        by meters, the rotation asked for a tilt (the point's frame was dProB's, Z up)."""
        from bii_blender_tools.equipment import crane_type
        root = truck_crane()
        rig.set_crane_hook(root, "HookPoint")
        rig.preview(root, {"Boom": 20, "Tele": 3, "Hook": 4})
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        move_arm = next(i for i in model["EquipmentTypeImplementations"][0]["Implementations"]
                        if i["CapabilityId"]["Guid"] == crane_type.MOVE_ARM)
        setup = ik_chain.Setup("HookPoint")
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        for name, v in setup.start_values.items():
            state[ids[name]] = v
        place = np.eye(4)
        place[:3, 3] = (100.0, 0.0, -20.0)          # the crane somewhere in the world
        target = place @ np.array([[1, 0, 0, 1.0], [0, 1, 0, 2.0], [0, 0, 1, -7.0], [0, 0, 0, 1]])
        target[:3, :3] = np.eye(3)                 # identity rotation, as Lift passes it
        moves = ik_capability.simulate_type_capability(
            move_arm, model, {ik_capability.port("Self"): "self", ik_capability.port("Destination"): target,
                              ik_capability.port("Duration"): 10.0}, state, place)
        sim = ik_capability._Sim(model, {**state, **moves}, place)
        hook = place @ sim.point_pose(ids["HookPoint"])
        self.assertLess(float(np.linalg.norm(hook[:3, 3] - target[:3, 3])), 0.02)


    def test_deliver_turns_cargo(self):
        """Move Arm with an upright rotation, as Deliver passes it for a turned cargo: a hook with a
        swivel reaches the position turned like that."""
        self._deliver_turns_cargo(False)

    def test_deliver_turns_cargo_global_hook(self):
        """The same with a hook capability that takes global destinations: Move Arm passes the
        Destination as it is, the capability makes it relative."""
        nodes = self._deliver_turns_cargo(True)
        self.assertNotIn("RelativeToNode", nodes)

    def _deliver_turns_cargo(self, global_destination):
        root = truck_crane()
        swivel = rig.insert_joint("Swivel", "Hook", ["HookPoint"], (0, -6, 2.8), axes_from=root)
        rig.set_joint(swivel, "REVOLUTE", axis="Z", limits=None)
        rig.set_crane_hook(root, "HookPoint")
        rig.set_ik_target("HookPoint", global_destination=global_destination)
        rig.preview(root, {"Boom": 20, "Tele": 3, "Hook": 4})
        rig.export(root, self.path)
        from bii_blender_tools.equipment import crane_type
        model = dequip_format.read(self.path).model
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        move_arm = next(i for i in model["EquipmentTypeImplementations"][0]["Implementations"]
                        if i["CapabilityId"]["Guid"] == crane_type.MOVE_ARM)
        nodes = [n.get("Node", n)["$case"] for n in move_arm["Implementation"]["GraphData"]["Nodes"]]
        setup = ik_chain.Setup("HookPoint")
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        for name, v in setup.start_values.items():
            state[ids[name]] = v
        place = np.eye(4)
        place[:3, 3] = (100.0, 0.0, -20.0)
        place[:3, :3] = ik_solver.axis_angle(np.array([0, 1.0, 0]), math.radians(30))
        for heading, pos in ((70, (4.0, 2.0, -5.0)), (-120, (-3.0, 1.0, 6.0)), (180, (5.0, 3.0, 2.0))):
            target = np.eye(4)
            target[:3, :3] = ik_solver.axis_angle(np.array([0, 1.0, 0]), math.radians(heading))
            target[:3, 3] = pos
            target = place @ target
            moves = ik_capability.simulate_type_capability(
                move_arm, model, {ik_capability.port("Self"): "self", ik_capability.port("Destination"): target,
                                  ik_capability.port("Duration"): 10.0}, state, place)
            sim = ik_capability._Sim(model, {**state, **moves}, place)
            reached = place @ sim.point_pose(ids["HookPoint"])
            self.assertLess(float(np.linalg.norm(reached[:3, 3] - target[:3, 3])), 0.005, msg=heading)
            turn = reached[:3, :3].T @ target[:3, :3]
            self.assertLess(math.degrees(math.acos(min(1.0, (np.trace(turn) - 1) / 2))), 1.0, msg=heading)
        return nodes


class AlignTest(IkTestCase):
    def test_align_point_with_equipment(self):
        root = truck_crane()
        rig.set_forward(root, "-X")
        child = cube("Shackle", (0, -6, 2.4), bpy.data.objects["HookPoint"], size=0.05)
        before = child.matrix_world.copy()
        rig.set_preview("Boom", 30)
        point = bpy.data.objects["HookPoint"]
        self.assertGreater(ik_chain.misalignment_deg(point, root), 45)
        rig.set_ik_target(point)
        self.assertTrue(any("equipment's axes" in p for p in rig.problems(root)))
        rig.align_to_equipment(point)
        self.assertLess(ik_chain.misalignment_deg(point, root), 1e-3)
        self.assertFalse(any("equipment's axes" in p for p in rig.problems(root)))
        rig.reset_preview(root)
        self.assertLess(max(abs(a - b) for ra, rb in zip(child.matrix_world, before) for a, b in zip(ra, rb)), 1e-5)
        # dProB's identity rotation: up as up, forward to the model's front (Blender -X here)
        setup = ik_chain.Setup(point)
        self.assertLess(float(np.abs(setup.current_pose()[:3, :3] - np.eye(3)).max()), 1e-5)
        name = rig.add_point("Saddle", "Hook", (0, -6, 2.3))
        self.assertLess(ik_chain.misalignment_deg(bpy.data.objects[name], root), 1e-3)
        with self.assertRaises(RigError):
            rig.align_to_equipment("Hook")

    def test_crane_hook_on_turned_joint_is_an_error(self):
        root = truck_crane()
        hook = bpy.data.objects["Hook"]
        hook.rotation_euler = (0, 0, 0.5)
        bpy.context.view_layer.update()
        with self.assertRaises(RigError):
            rig.set_crane_hook(root, hook)


class InsertJointTest(IkTestCase):
    def test_insert_pitch_reversal(self):
        """A pitch reversal added with insert_joint: the hook stays level when the boom luffs."""
        root = bpy.data.objects.new("Rig", None)
        bpy.context.scene.collection.objects.link(root)
        boom = cube("Boom2", (0, 1, 3), root)
        hook = cube("Hook2", (0, -5, 2), boom, size=0.4)
        rig.make_rig(root)
        rig.set_joint(boom, "REVOLUTE", axis="X", limits=(0, 80))
        before = {o.name: o.matrix_world.copy() for o in (boom, hook)}
        name = rig.insert_joint("Reversal2", boom, [hook], (0, -5, 3), axes_from=root)
        bpy.context.view_layer.update()
        for o in (boom, hook):
            self.assertLess(max(abs(a - b) for ra, rb in zip(o.matrix_world, before[o.name]) for a, b in zip(ra, rb)), 1e-5)
        self.assertIs(hook.parent, bpy.data.objects[name])
        rig.set_joint(name, "REVOLUTE", axis="-X", limits=(0, 80))
        rig.set_follow(name, [(boom, 1.0)])
        rig.set_preview(boom, 40)
        bpy.context.view_layer.update()
        self.assertLess(abs(hook.matrix_world.to_3x3().normalized().to_euler().x), 1e-4)
        with self.assertRaises(RigError):
            rig.insert_joint("Other", root, [hook], (0, 0, 0))


class LargeRigTest(IkTestCase):
    def test_many_parts_stay_fast(self):
        """Hundreds of parts: rig operations must not scan all objects per part. Regression: a
        679-object crane took over 13 minutes to set up (Object.children in a recursion)."""
        import time
        root = truck_crane()
        boom = bpy.data.objects["Boom"]
        for i in range(800):                      # detail parts, many of them under the boom
            e = bpy.data.objects.new(f"Detail{i:03d}", None)
            bpy.context.scene.collection.objects.link(e)
            e.parent = boom if i % 2 else root
        bpy.context.view_layer.update()
        t = time.perf_counter()
        name = rig.insert_joint("Level2", "Tele", ["Reversal"], (0, -6, 3), axes_from=root)
        rig.set_joint(name, "REVOLUTE", axis="X", limits=(-10, 10))
        for v in range(0, 60, 5):
            rig.set_preview("Boom", v)
        rig.reset_preview(root)
        rig.describe(root)
        rig.problems(root)
        ik_chain.Setup("HookPoint")
        self.assertLess(time.perf_counter() - t, 10.0)


class AimTest(IkTestCase):
    def test_target_behind_the_slewing(self):
        """A target exactly behind the boom: the slewing gets no gradient from the solver, so the
        IK starts with it turned towards the target (graph and preview alike). Regression: the
        boom only luffed up and the hook stayed far off."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.001)
        setup = ik_chain.Setup("HookPoint")
        self.assertEqual(setup.aim.name, "Cab")
        cur = setup.current_pose()
        behind = cur.copy()
        behind[:3, 3] = (cur[0, 3], cur[1, 3] + 1.0, -0.8 * cur[2, 3])  # behind the slewing axis, a bit closer
        values, pos_err, _ = setup.solve(behind, position_only=True)
        self.assertLess(pos_err, 0.001)
        self.assertAlmostEqual(abs(math.degrees(values["Cab"])), 180, delta=1)
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = model["CustomCapabilities"][0]
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        moves = ik_capability.simulate(cap, model, behind, state, inputs={"Position Only": True})
        # exactly behind, +180 and -180 degrees are the same turn
        wrap = lambda a: (a + math.pi) % (2 * math.pi) - math.pi
        self.assertAlmostEqual(abs(wrap(moves[ids["Cab"]] - float(values["Cab"]))), 0, places=4)
        for name in ("Boom", "Tele", "Hook"):
            self.assertAlmostEqual(moves[ids[name]], float(values[name]), places=4, msg=name)
        macros = [n.get("MacroId") for n in cap["Implementation"]["GraphData"]["Nodes"]]
        self.assertIn(ik_capability.SIGNED_ANGLE_MACRO, macros)


class ForwardTest(IkTestCase):
    def test_model_facing_x(self):
        """A model whose front points along Blender +X: with Forward +X the export turns it so its
        front is dProB's forward (+Z) and Blender's up is dProB's up (+Y); verify and the IK
        capability still match Blender."""
        root = truck_crane()                        # its boom points along -Y
        root.rotation_euler = (0, 0, math.radians(90))   # now along +X... and the front (+Y) along -X
        bpy.context.view_layer.update()
        root.bii_rig.forward = "NEG_X"
        rig.set_ik_target("HookPoint", orientation_weight=0.001)
        rig.export(root, self.path)
        self.assertLess(rig.verify(root)["max_error"], 1e-4)
        model = dequip_format.read(self.path).model
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        worlds = dprob_math.element_world_matrices(model["HierarchyRoots"])
        hook = worlds[ids["HookPoint"]][:3, 3]
        # the boom points backwards in the model (-Y as modeled = away from the front): dProB -Z
        self.assertLess(hook[2], -4.0)
        self.assertAlmostEqual(hook[0], 0.0, places=3)
        self.assertGreater(hook[1], 1.0)           # up stays up
        setup = ik_chain.Setup("HookPoint")
        dest = setup.current_pose().copy()
        dest[:3, 3] += (1.0, 1.0, 1.0)
        values, pos_err, _ = setup.solve(dest, position_only=True)
        self.assertLess(pos_err, 0.001)
        state = {k: 0.0 for k in ik_solver.rig_from_model(model["HierarchyRoots"])[0]}
        moves = ik_capability.simulate(model["CustomCapabilities"][0], model, dest, state,
                                       inputs={"Position Only": True})
        for name in ("Boom", "Tele", "Hook"):
            self.assertAlmostEqual(moves[ids[name]], float(values[name]), places=4, msg=name)

    def test_default_front_is_plus_y(self):
        root = truck_crane()
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        ids = {e["Name"]: e["Id"] for e in dequip_format.walk(model["HierarchyRoots"])}
        hook = dprob_math.element_world_matrices(model["HierarchyRoots"])[ids["HookPoint"]][:3, 3]
        self.assertLess(hook[2], -4.0)             # Blender -Y is dProB -Z (behind)
        self.assertGreater(hook[1], 1.0)           # Blender +Z is dProB +Y (up)


class ApiAdditionsTest(IkTestCase):
    def test_add_point_world_axis_forward_and_follower_range(self):
        root = truck_crane()
        name = rig.add_point("Saddle", "Hook", (0, -6, 2.3), axes_from=root)
        p = bpy.data.objects[name]
        self.assertTrue(p.bii_rig.attachment_point)
        self.assertEqual(p.bii_rig.joint_type, "FIXED")
        self.assertIs(p.parent, bpy.data.objects["Hook"])
        self.assertLess((p.matrix_world.translation - Matrix.Translation((0, -6, 2.3)).translation).length, 1e-6)
        # a rotated part, axis given in world space
        arm = cube("Arm2", (2, 0, 3), root, rot=(0.3, 0.2, 0.9))
        rig.set_joint(arm, "REVOLUTE", axis_world=(1, 0, 0), limits=(-10, 10))
        ax = rig.describe_joint(arm)["axis_world"]
        self.assertLess(max(abs(a - b) for a, b in zip(ax, (1, 0, 0))), 1e-5)
        rig.set_forward(root, "-X")
        self.assertEqual(rig.describe(root)["forward"], "-X")
        with self.assertRaises(ValueError):
            rig.set_forward(root, "up")
        # a follower that its leader drives beyond its limits
        self.assertFalse([t for t in rig.problems(root) if "beyond its limits" in t])
        rig.set_joint("Reversal", "REVOLUTE", limits=(0, 40))
        self.assertTrue([t for t in rig.problems(root) if "beyond its limits" in t])


class FitAimTest(IkTestCase):
    def test_cylinder_follows_boom(self):
        """A luffing cylinder between the house and the boom: fit_follow_aim makes it follow the
        boom so it keeps pointing at the rod eye."""
        root = bpy.data.objects.new("Exc", None)
        bpy.context.scene.collection.objects.link(root)
        house = cube("House", (0, 0, 1), root)
        boom = cube("Boom3", (0, 1, 2), house)           # hinge pin at (0, 1, 2)
        eye = bpy.data.objects.new("Rod Eye", None)
        bpy.context.scene.collection.objects.link(eye)
        eye.parent = boom
        eye.matrix_world = Matrix.Translation((0, 3, 2.2))
        cyl = cube("Cylinder", (0, 0.2, 1.0), house, size=0.3)  # base pin on the house
        bpy.context.view_layer.update()
        rig.make_rig(root)
        rig.set_joint(boom, "REVOLUTE", axis="-X", limits=(0, 70))
        rig.set_joint(cyl, "REVOLUTE", axis="-X", limits=None)
        res = rig.fit_follow_aim(cyl, boom, eye)
        self.assertLess(res["worst_error_deg"], 3.0)
        # in the preview, the cylinder points at the eye at a few boom angles
        for v in (10, 35, 60):
            rig.set_preview(boom, v)
            bpy.context.view_layer.update()
            m = cyl.matrix_world
            want = (eye.matrix_world.translation - m.translation).normalized()
            rest_dir = (Matrix.Translation((0, 3, 2.2)).translation - Matrix.Translation((0, 0.2, 1.0)).translation).normalized()
            now = (m.to_3x3().normalized() @ rest_dir).normalized()
            self.assertLess(math.degrees(now.angle(want)), 3.0)


class TrialHelpersTest(IkTestCase):
    def test_rotate_parts_outline_to_dprob(self):
        root = truck_crane()
        hook = bpy.data.objects["Hook"]
        before = hook.matrix_world.translation.copy()
        rig.rotate_parts(["Hook"], (0, -6, 3), (1, 0, 0), 90)
        bpy.context.view_layer.update()
        after = hook.matrix_world.translation
        self.assertLess((after - before).length, 1e-6)                 # pivot at its origin
        point = bpy.data.objects["HookPoint"].matrix_world.translation   # child turned with it
        self.assertAlmostEqual(point.y, -6 + 0.4, places=4)
        for i in range(30):
            e = bpy.data.objects.new(f"Bolt.{i:03d}", None)
            bpy.context.scene.collection.objects.link(e)
            e.parent = root
        lines = rig.outline(root, depth=2)
        self.assertTrue(any("Bolt x" in l for l in lines))
        self.assertLess(len(lines), 10)
        root.bii_rig.forward = "POS_X"
        self.assertAlmostEqual(rig.to_dprob(root, (5, 0, 1))[2], 5.0, places=5)   # front is forward
        self.assertAlmostEqual(rig.to_dprob(root, (5, 0, 1))[1], 1.0, places=5)   # up is up

    def test_verify_ik_runs_the_exported_graph(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.001)
        rig.export(root, self.path)
        res = rig.verify_ik("HookPoint", [(0, -3, 4), (0, 8, 2), (5, 0, 6)])
        for r in res:
            self.assertLess(r["position_error_m"], 0.01, r)
            self.assertLess(r["max_joint_difference"], 1e-4, r)

    def test_global_destination(self):
        """Global Destination: the Destination is a GlobalPose, made relative to the equipment's
        current pose in the graph; verify_ik runs it with the equipment turned and moved."""
        root = truck_crane()
        rig.set_ik_target("HookPoint", orientation_weight=0.001, global_destination=True)
        self.assertTrue(rig.describe_joint("HookPoint")["ik"]["global_destination"])
        rig.export(root, self.path)
        model = dequip_format.read(self.path).model
        cap = next(c for c in model["CustomCapabilities"]
                   if c["Signature"]["Id"]["Guid"] == bpy.data.objects["HookPoint"].bii_rig.ik_capability_id)
        dest = next(i for i in cap["Signature"]["Inputs"] if i["Name"].get("Text") == "Destination")
        self.assertEqual(dest["Type"], ik_capability.atomic("GlobalPose"))
        cases = [n.get("Node", n)["$case"] for n in cap["Implementation"]["GraphData"]["Nodes"]]
        self.assertEqual(cases.count("RelativeToNode"), 1)
        self.assertEqual(cases.count("ReadCurrentPoseNode"), 1)
        res = rig.verify_ik("HookPoint", [(0, -3, 4), (0, 8, 2), (5, 0, 6)])
        for r in res:
            self.assertLess(r["position_error_m"], 0.01, r)
            self.assertLess(r["max_joint_difference"], 1e-4, r)

    def test_fit_exact_at_rest(self):
        root = bpy.data.objects.new("Exc2", None)
        bpy.context.scene.collection.objects.link(root)
        house = cube("House2", (0, 0, 1), root)
        boom = cube("Boom4", (0, 1, 2), house)
        eye = bpy.data.objects.new("Eye2", None)
        bpy.context.scene.collection.objects.link(eye)
        eye.parent = boom
        eye.matrix_world = Matrix.Translation((0, 3, 2.2))
        cyl = cube("Cyl2", (0, 0.2, 1.0), house, size=0.3)
        bpy.context.view_layer.update()
        rig.make_rig(root)
        rig.set_joint(boom, "REVOLUTE", axis="-X", limits=(0, 80))
        rig.set_joint(cyl, "REVOLUTE", axis="-X", limits=None)
        rig.fit_follow_aim(cyl, boom, eye)
        rig.set_preview(boom, 0)
        self.assertAlmostEqual(rig.describe_joint(cyl)["preview_deg"], 0.0, places=3)   # exact at rest


class IkRootProblemTest(IkTestCase):
    def test_root_joint_check(self):
        root = truck_crane()
        rig.set_ik_target("HookPoint", root_joint="Boom")
        self.assertFalse([p for p in rig.problems(root) if "root joint" in p])
        bpy.data.objects["HookPoint"].bii_rig.ik_root = bpy.data.objects["Cab"]
        self.assertFalse([p for p in rig.problems(root) if "root joint" in p])

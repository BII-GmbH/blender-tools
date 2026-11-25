# Equipment rigs (BII Tools)

Part of the BII Tools extension. Turns a model into a dProB scriptable equipment: give its parts joints (revolute, prismatic, ball) with axes and limits, preview the movements in the viewport the way dProB moves them, and export a `.dequip` file that dProB imports as scriptable equipment. Its capabilities are then scripted in dProB's equipment configurator; for parts you mark, the export also writes inverse kinematics capabilities ("move this part to a pose").

Install BII Tools (see [its README](../README.md)); tested with Blender 5.2 and 5.3.

## Use

View3D > Sidebar (N) > BII Equipment.

1. **Make Rig**: select the top object of the equipment and click Make Rig. It and all its children become the equipment, one dProB part per object. Its origin is the equipment's origin (on the ground; for a crane under the slewing center). **Forward** says where the model's front points in Blender (+Y by default, or -Y, +X, -X); the export turns the equipment so the front is dProB's forward (+Z), and Blender's +Z is dProB's up (+Y). The root's own rotation is part of the model, so do not turn the root to fix the direction.
2. **Joint** panel, for each moving part:
   - Joint type: Fixed (moves only with its parent), Revolute (hinge), Prismatic (slider), Ball (free rotation).
   - Axis: X, Y, Z, their negatives, or a custom direction, in the object's own axes (its rotation, without scale).
   - Limits: angles for revolute (Limits off: turns all the way round), distances for prismatic.
   - The joint turns about, or slides from, the object's **origin**. Pivot to 3D Cursor moves the origin without moving the geometry or the children.
   - Visible (dProB shows the geometry) and Attachment Point (dProB can attach objects there).
   - Copy Joint to Selected gives the selected objects the active object's joint.
3. **Preview**: the Equipment Rig panel has a slider per joint. Play Joints moves every joint back and forth through its range (Esc stops); Rest Pose sets all joints to 0. The viewport draws each joint: an axis with the arc of its angle range (orange), the slide range (blue) or rings (ball joint, purple).
4. **Drive** (Joint panel, revolute and prismatic joints): how a joint is moved besides by hand.
   - Free: by hand or by IK.
   - Follow: value = offset + sum of factor × leader joint. A pitch reversal that keeps the hook level follows the boom with factor 1 (axes opposite) or -1 (same axis). Without leaders the joint stays at the offset (a constant). Leader and follower must be of the same type. The preview moves followers with their leaders.
   - Hold: IK capabilities keep the joint at the value it has when they start (for example the slewing, if a capability should only luff and telescope).
5. **Inverse Kinematics** panel, on the part to be moved (a joint, or a fixed part such as an attachment point under one): tick the header to export an IK capability for it.
   - Name, Description: of the capability in dProB (default "Move <part>").
   - Root Joint: the lowest joint the IK may move; empty: all joints down to the rig root.
   - Iterations: solver steps per pass. Passes: when joints of the chain follow or hold, the IK runs this many times and sets them after each pass (dProB's IK node knows nothing about them); one pass otherwise.
   - Start Away from Limits: the IK starts from the current joint values, but every limited joint at least this share of its range inside its limits (default 10 %). A joint resting at a limit often stalls the solver: a boom lying flat cannot get closer to the crane by rising, to first order, so the solver never raises it and only pulls the telescope, which is already in. Joints that are inside the margin keep their values, so small moves stay small. 0 starts from the current values as they are.
   - Orientation Weight: how much the orientation counts against the position. dProB measures the orientation error in degrees, so small values are usual: 0.001 is about position only (dProB's Crane Move Arm uses it), the default is 0.01; avoid 0 with a pitch reversal (the IK then tilts the rope and the passes converge slowly); from about 0.03 the orientation overshoots and the solver may not settle.
   - Global Destination: the capability takes the Destination in the world (a GlobalPose) and makes it relative to the equipment's current pose (Get Current Pose of Self, Relative To), as the Crane's Move Arm does. Off (default): the Destination is relative to the equipment.
   - **IK Preview**: a gizmo appears at the part. Drag the ring (free move), the arrows (along the part's axes) or the circles (turn). The rig shows the result of the capability for that destination, solved from the joint values when you started (the equipment's state). Solving runs in the background; a new solve starts when the last one is done, so the rig may lag behind the gizmo. The panel and the status bar show how far the part is off and the destination as dProB gets it. Esc, right click or End IK Preview ends it: the gizmo disappears and the joints go back.
6. **Export**: choose the file, click Export. Check File compares the written file with the preview (see below).

### What dProB gets

- The model as an FBX (modifiers applied) and the hierarchy of the parts with their joints.
- Every texture is packed into the FBX, as PNG or JPEG. Other formats (TIFF, TGA, EXR, BMP, WebP, ...) and images without a readable file (generated, edited and not saved) are converted to PNG for the export; your materials keep their images. A texture whose file is missing stops the export. Only textures the FBX exporter takes are exported: images connected to the Principled BSDF.
- A part's pose, axis and limits are computed the way dProB's configurator imports an FBX, so the file looks in dProB as if you had imported the FBX there and set the joints by hand. Angles keep their sign: a joint at 30° in Blender looks the same at 30° in dProB.
- Ids stay the same over exports: the equipment id is on the root, every part has an element id (a duplicated object gets a new one). dProB's capabilities refer to the joints by these ids.
- The export replaces an existing file completely. Capabilities you scripted in dProB on an earlier import are not carried over. Import the new file in dProB and let it replace the old asset.

### The IK capabilities

For every IK part the export writes a custom capability (inputs Self, Destination as a LocalPose of the part relative to the equipment, or with Global Destination as a GlobalPose in the world that the graph makes relative to the equipment's current pose (the part's frame, as dProB's attachment point poses are; the part's offset from its joint is turned with the part's current rotation, so the position is reached also when the rotation is not), Duration, and the IK settings Iterations, Orientation Weight, Start Away from Limits and Passes with the Blender values as defaults; output Processes). The graph reads the rig's values from the rig: joint limits with Get Revolute / Prismatic Joint Constraints, a fixed target's offset with its attachment point's Get Local Pose (so a fixed IK part must be an attachment point; the API makes it one). Only the Follow factors and offsets are numbers in the graph. The nodes are laid out in rows (execution left to right, each node's inputs as a tree to its left, getters next to their consumer, the loop's Done on its own row) with named frames around the sections. It works like dProB's built-in Crane Move Arm: the Inverse Kinematics node computes the joint values, then one process per moved joint (For Each Dictionary Entry over the result; revolute joints through the Rotate Joint function of dProB's sample crane, which takes the short way round for unlimited joints), all with the full duration so they run together. Before the first IK pass, the graph clamps the current values of the limited joints into their ranges minus the margin (dProB's Clamp macro) and passes them as the IK's initial values. With Follow or Hold joints in the chain, the IK runs in a Loop Range and the graph sets those joints in the result after each pass.

- The capability id stays the same over exports.
- Followers keep their relation during the movement too, as all joints move linearly over the same duration.
- The coupling exists only in these capabilities. Another script that moves the leader alone does not move its followers.
- The IK preview runs a port of dProB's IK node (`ik_solver.py`), on joints built from the Blender objects. Tests check that these joints equal the ones dProB reads from the exported file, and that the generated graph, run by a small interpreter, gives the preview's values.

### Crane

Set **Crane Hook** (Export panel) to an attachment point with an IK capability, and the export makes the equipment a dProB Crane:

- Move Arm turns the global Destination into a pose relative to the equipment (Relative To its current pose) and invokes the hook's IK capability with it (a hook capability with Global Destination gets the Destination as it is), with the IK settings from Blender, but orientation weight 0.001 as dProB's own crane. The hook takes the Destination's position and, as far as it can turn (hook swivel, slewing), its rotation.
- The hook's frame must have the equipment's axes at rest (dProB's up as up, forward to the model's front; Blender shows such an empty with Y up and Z to the front). Then the rotations Lift and Deliver pass, which have dProB's up as up, are ones a hanging hook can take, and turning them about up turns the hook. `set_crane_hook` turns a fixed hook point so (**Align with Equipment Axes** in the IK panel does the same); a hook frame turned otherwise is an export error. dProB takes an object's local Z as forward and local Y as up, so an empty with Blender's usual axes would ask the hook for a 90° tilt.
- Lift and Deliver are dProB's own implementations (from a sample crane, written as code in `sample_graphs.py`), on the hook's attachment point. They move the arm through Move Arm. Lift without a hook offset puts the hook above the cargo turned like the cargo (the sample used identity), so the Current Cargo Hook Offset has no turn and Deliver sets the cargo down turned as asked.
- The state variables Current Cargo and Current Cargo Hook Offset, which Lift and Deliver use, are added.

How the capability solves:

- Start values: limited joints at least "Start Away from Limits" inside their limits; the top-most free unlimited revolute joint of the chain (the slewing, when the target is at least 0.5 m from its axis) turned so that the target part points at the Destination (Get Current Effective Joint Pose of the joints above it, Signed Angle on Plane). Without that, a target exactly behind the boom gives the solver no gradient to slew at all.
- Position Only (input, default off): the part keeps its current rotation, turned with that slewing, and only the Destination's position counts.
- Follow joints are set after each pass and not clamped to their limits; give them limits that cover their leaders' range (`problems` warns).

### Notes

- The preview uses the delta transforms (Object > Transform > Delta Transform) of the moving parts, so their own location, rotation and scale stay the rest pose. A part that already uses delta transforms cannot be previewed; apply or clear them.
- Apply non-uniform scale on parts that have children: Blender and dProB handle a non-uniform parent scale differently (the export warns).
- Only meshes, empties, curves, surfaces, text and metaballs become parts. Cameras, lights and armatures are left out, with their children.
- Check File sets random joint values, evaluates the file with dProB's rig math (`dprob_math.py`, a port of dProB's FBX import and joint code) and compares every part with Blender. It shows how far they differ; less than 0.1 mm means the file matches the preview.

## Scripting and AI agents

`rig_api.py` is the public interface, for scripts and for AI agents that work in Blender through the Blender MCP (`execute_blender_code`). Angles in degrees, lengths in meters, objects as objects or names. Errors raise `RigError` or `ValueError` with a message for the user.

```python
from bl_ext.user_default.bii_blender_tools.equipment import rig_api as rig

rig.make_rig("Crane")
rig.set_joint("Tower", "REVOLUTE", axis="Z", limits=None)          # unlimited
rig.set_joint("Boom", "REVOLUTE", axis="X", limits=(-10, 80))      # degrees
rig.set_joint("Trolley", "PRISMATIC", axis="Y", limits=(0, 12))    # meters
rig.set_joint("Hook", "BALL")
rig.set_pivot("Boom", (0, 1.5, 22))          # world point; geometry and children stay
rig.preview("Crane", {"Tower": 45, "Boom": 30, "Trolley": 6})
rig.set_follow("HookRot", [("Boom", 1.0)])   # pitch reversal: keeps the hook level
rig.set_ik_target("Hook", name="Move Hook", orientation_weight=0.001)
rig.solve_ik("Hook", position_world=(4, 10, 15))  # shows the capability's result
rig.describe("Crane")                         # tree of parts with joints, problems
rig.export("Crane", r"C:\work\crane.dequip")  # summary: parts, joints, warnings
rig.verify("Crane")                           # {"max_error": ...} in meters
rig.read_dequip(r"C:\work\crane.dequip")      # what a .dequip contains
```

| Function | Does |
|---|---|
| `make_rig(obj, export_path=None, forward=None)`, `set_forward(root, "+Y"/"-Y"/"+X"/"-X")`, `remove_rig(root)`, `rigs()`, `rig_of(obj)` | Rig roots and where the model's front points |
| `set_joint(obj, type, axis=None, limits="keep", visible=None, attachment_point=None, axis_world=None)` | Joint of a part; `limits=None` makes a revolute joint unlimited; `axis_world` for an axis given in world space |
| `fit_follow_aim(part, leader, target, samples=9, value_range=None, exact_at="rest")` | Fits a Follow so a revolute part (a hydraulic cylinder) keeps pointing at a target that moves with the leader, exact at rest; returns the worst error |
| `rotate_parts(objects, pivot_world, axis_world, degrees)` | Turns parts with their children about a world axis (re-posing a hook block modeled for transport) |
| `outline(obj, depth=3)` | Compact hierarchy for a first look at a big model (same-named siblings folded) |
| `to_dprob(root, point_world)` | A world point in dProB's equipment space (+Y up, +Z forward), to check the Forward setting |
| `verify_ik(obj, targets_world, path=None, position_only=True)` | Runs the exported IK capability (its graph, with the ported IK node) for targets and compares it with the preview (Global Destination: with the equipment turned and moved in the world) |
| `add_point(name, parent, point_world, axes_from=None, attachment_point=True)` | A fixed empty at a world point, marked as attachment point: the load point of a hook or tool; with the equipment's axes unless `axes_from` |
| `align_to_equipment(obj)` | Turns a fixed part so its dProB frame has the equipment's axes at rest (children stay); returns the angle |
| `clear_joint(obj)`, `describe_joint(obj)`, `describe(root)` | Read and reset |
| `set_pivot(obj, point_world)` | Moves the origin (pivot) |
| `insert_joint(name, parent, children, pivot_world, axes_from=None)` | Adds an empty joint between a part and its children (level joints, hook spin); nothing moves |
| `set_preview(obj, value)`, `preview(root, {name: value})`, `sweep_pose(root, phase_deg)`, `reset_preview(root)` | Preview |
| `set_follow(obj, [(leader, factor)], offset=0)`, `set_hold(obj)`, `set_free(obj)` | Drive: couplings |
| `set_crane_hook(root, hook)` | Crane type: Move Arm on the hook's IK capability, Lift, Deliver |
| `set_ik_target(obj, enabled=True, name=None, root_joint="keep", iterations=None, loops=None, orientation_weight=None, seed_margin=None, global_destination=None)`, `ik_targets(root)` | IK capabilities |
| `solve_ik(obj, position_world=None, rotation_world=None, matrix_world=None, apply=True)`, `ik_destination(obj)` | Solve as the capability would (without a rotation: Position Only), show it; the destination as dProB gets it |
| `problems(root)`, `export(root, path=None)`, `verify(root, path=None, samples=3)`, `read_dequip(path)` | Export and check |

The skill `skills/bii-equipment-rig` in this repo tells an AI agent (Claude Code, Codex) how to rig an equipment with these functions; its `rigging-patterns.md` shows how to build common mechanisms (hinges, telescopes, parts that stay level, hoists, trolleys, excavator arms) from joints. Setup: [Skills for AI agents](../../../README.md#skills-for-ai-agents).

## The .dequip format

A zip archive (dProB's asset system, format `ScriptableEquipment_v2`): `asset_guid.txt`, `dProB_asset_metadata.json`, `model.json` (the `EquipmentAssetModel` of `BII.Common.Equipment`) and the FBX under `<geometry id>/<name>.fbx`. `dequip_format.py` writes and reads it; `PRODUCT_VERSION` there is the dProB version whose format it writes.

## Tests

```
blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_tools/run_in_blender.py
```

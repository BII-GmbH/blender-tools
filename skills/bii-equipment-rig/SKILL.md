---
name: bii-equipment-rig
description: Rig a model in Blender as a dProB scriptable equipment with the BII Tools plugin (equipment rigs) - set joints (revolute, prismatic, ball), axes, limits and pivots, couple joints, add inverse kinematics capabilities, make it a dProB Crane (anything that carries cargo with IK - mobile or tower crane, excavator, loader arm), preview the movements, and export a .dequip file for dProB. Use when the user asks to rig, set up joints for, make movable, or export a crane, excavator, vehicle, door or other machine as dProB equipment (.dequip), or to check or update such a file.
---

# Rig a dProB equipment (BII Tools)

Work in Blender through the Blender MCP (`execute_blender_code`; `result` must be a dict). Use
the background Blender (`blender-headless`) for the work and a Blender window
(`blender-window`) when you need viewport screenshots; both start on their own. A fresh session
opens an empty scene: open the user's file with `bpy.ops.wm.open_mainfile(filepath=...)`, and
save under a new name (`save_as_mainfile`) unless the user wants the file changed.

```python
from bl_ext.user_default.bii_blender_tools.equipment import rig_api as rig
```

Keep each MCP call short (well under a minute): a call that runs into the MCP's timeout loses its
result, and the Blender stays busy until it is done. Split long loops (IK tests over many
targets) into several calls. Before changing anything, check `bpy.data.filepath` is the file you
work on.

`equipment/README.md` of the BII Tools extension has the details. Angles are degrees, lengths
meters, objects can be names. Errors raise `RigError`/`ValueError`: show the message to the user.

[rigging-patterns.md](rigging-patterns.md) shows how to build common mechanisms from joints
(hinges, telescopes, parts that stay level, hoist and hook, trolleys, cylinders, booms with
several sections, tracked and wheeled bases). Read it before you set up anything that carries a
load.

## How a rig works (read once)

- Every object below the rig root is a part. A part's joint moves it **with all its children**
  relative to its parent: turn about (revolute), slide along (prismatic) or turn freely about
  (ball) an axis through the part's **origin**, in the part's own axes.
- Joint value 0 is the pose as modeled. Limits are relative to it.
- One object, one joint. A mechanism that needs two motions at one place (raise a hook and spin
  it) needs two objects in a chain. Empties are fine for that: `rig.insert_joint` adds one
  between a part and its children without moving anything.
- Joints do not know about each other in dProB. Where one joint must depend on another (a hook
  that stays level while the boom luffs), use Drive: Follow (value = offset + sum of factor x
  leader) or Hold. Only the generated IK capabilities apply these.
- An IK capability moves one target part to a pose by moving all joints from the target up to the
  root joint. The target is a joint part or a fixed part that is an attachment point (the exact
  point where a load hangs or a tool works).
- dProB's axes: +Y up, +Z forward. The export maps Blender +Z to up and the model's front (Forward
  setting, default Blender +Y) to forward. The root's origin is the equipment's origin.

## 1. Look at the model first

Walk the hierarchy (parents, children, types, names, origins, rotations, scales) and look at it
(screenshot from `blender-window`, or a Workbench render into a file). For big models start with
`rig.outline(top_object, depth=3)` (a full dump overflows the MCP result). The window is a
Blender of its own: save in the headless one and open that file in the window before
screenshots. Change nothing yet. Find out, and ask the user where names and shape do not tell:

- The top of the equipment and the equipment's origin: on the ground, where dProB places it (for
  a crane under the slewing center). If the top object's origin is elsewhere, add a new empty as
  root at the right point and parent the old top object to it (keep its world transform).
- Where the front points (cab side, driving direction). Not Blender +Y: `make_rig(root,
  forward="-X")` (or `set_forward`). Do not rotate the root for this; its rotation is part of the
  model.
- Every motion the machine has, and its range (data sheet or user). Do not guess joints of
  real machines; ask "Does the boom only luff, or also telescope?"
- Whether each moving part is its own object, parented to what it moves with. If parts are joined
  in one mesh or parented wrongly, tell the user and ask before separating or reparenting.
- Parts modeled in a transport or stowed pose (a hook block tilted and lashed to the bumper,
  folded outriggers): ask whether to re-pose them into the working pose. Re-pose the geometry
  (rotate the parts) rather than holding them with a constant Follow: dProB starts every joint
  at 0, so it shows the pose as modeled until a capability moves the joint.
- Where the load or tool point is (hook bottom, bucket teeth, fork tip), and whether something
  must stay level or keep its direction while other parts move.
- Scale: moving parts with children need uniform scale (warned at export). Ask before applying.
- Re-posing: `rig.rotate_parts(parts, pivot, axis, degrees)` turns parts with their children
  about a world axis. Find the angle from the geometry (for example the long axis of a plate's
  vertices), not by eye.
- Proportions: where the model does not match the real machine (short cylinders, short outrigger
  beams), real ranges make parts drift apart. Prefer ranges the model shows correctly, and tell
  the user which real values differ.

Old setups may carry hints: custom properties from other tools (for example `devision.joint.*`),
or empties named after their axis (`Drehwerk | Z-Achse`, `Teleskopschuss 01 | X`) that already
sit at the pivots. Use them as hints for joints, axes and limits, then confirm.

## 2. Joints, pivots, couplings

```python
rig.make_rig("Machine", forward="+Y")
rig.set_joint("Slew", "REVOLUTE", axis="Z", limits=None)         # unlimited
rig.set_joint("Boom", "REVOLUTE", axis="X", limits=(-5, 75))
rig.set_joint("Section1", "PRISMATIC", axis="Y", limits=(0, 10))
rig.set_joint("Cylinder", "REVOLUTE", axis_world=(1, 0, 0), limits=None)   # rotated part
rig.set_pivot("Boom", (0, 1.5, 3.2))                              # hinge pin, world point
name = rig.insert_joint("Hook Level", "Section1", ["Hook Block"], (0, -12, 4), axes_from="Machine")
rig.set_joint(name, "REVOLUTE", axis="-X", limits=(-5, 75))
rig.set_follow(name, [("Boom", 1)])                               # keeps the hook block level
point = rig.add_point("Load Point", "Hook", (0, -12, 0.9))         # equipment's axes
```

- Check every joint: `describe_joint(obj)` gives `axis_world` and `pivot_world`. The pivot must lie
  on the real axis (pin, slewing ring center): find it from the mesh (center of the bolt's
  vertices), not by eye. `set_pivot` keeps geometry and children in place.
- Check the direction of every revolute joint in the preview: a positive value must do what the
  limits assume (boom up for 0..75). If not, flip the axis (`-X`) rather than the limits.
- `visible=False` for helper parts dProB should not show (lashings, helper empties).
- Followers: preview the leader at a few values and check that the follower does what it should
  (for example the hook block's world rotation stays the same). Wrong sign: factor -1 or flip the
  follower's axis. Several leaders sum up (a level hook below two luffing sections). Give a
  follower limits that cover everything its leaders produce: the IK capabilities do not clamp
  followers, the preview does (`problems` warns).
- `rig.set_hold(obj)`: IK capabilities leave the joint as it is (slewing that a capability should
  not use). `rig.set_follow(obj, [], offset=v)`: a constant.

## 3. IK capabilities and the Crane

```python
rig.set_ik_target("Load Point", name="Move Hook", orientation_weight=0.001)
rig.solve_ik("Load Point", position_world=(4, -9, 2))     # position only; shows the result
rig.set_crane_hook("Machine", "Load Point")                # anything that carries cargo
```

- Target: the exact working point. For a hook it is an attachment point at the hook's saddle
  (`add_point`), not the hook's origin.
- `root_joint`: the lowest joint the IK may move (outriggers, tracks and the vehicle are usually
  below it or held).
- Orientation weight: 0.001 to 0.02 when only the position matters (hooks, most tools); higher
  only when the target must also turn (a gripper), and then the target needs joints to turn it.
  dProB measures the orientation error in degrees, so above about 0.03 it overshoots. Do not use
  0 with a Follow joint in the chain: the passes then converge slowly.
- `global_destination=True`: the capability takes the Destination in the world, like the Crane's
  Move Arm, and makes it relative to the equipment's current pose. Default: relative to the
  equipment. Ask the user which one scripts in dProB will pass.
- The capability first turns the top-most unlimited joint (slewing) so that the target part points
  at the destination, and starts limited joints away from their limits. Its input Position Only
  ignores the destination's rotation.
- A load point's frame should have the equipment's axes (`add_point` does that,
  `align_to_equipment` fixes an existing one): then a destination rotation with dProB's up as up
  is one a hanging hook can take. `problems` warns otherwise.
- Test targets all around: near and far, high and low, left, right, behind, and in front of the
  machine. `position_error_m` must be small for reachable targets. Off for a reachable target:
  raise `seed_margin` (start away from limits), the iterations or the passes; check the joints'
  limits and directions. Look at the results. `rig.reset_preview(root)` afterwards.
- `set_crane_hook` makes the equipment a dProB Crane: Move Arm (on the hook's IK capability),
  Lift and Deliver (attach and detach cargo at the hook's attachment point; the cargo keeps the
  rotation the user gives it if the hook can turn: give it a swivel). It aligns a fixed hook
  point with the equipment's axes.
  dProB treats anything as a crane that moves cargo with IK: mobile and tower cranes, excavators,
  loader arms, gantries. Ask the user whether they want it.

## 4. Preview and look

```python
rig.preview("Machine", {"Slew": 90, "Boom": 60, "Section1": 5})
```

Look at it (screenshot from `blender-window`, or a render): parts must turn about the right
point, in the right direction, and not fly apart. Check the extremes with `rig.sweep_pose(root,
0)` (all minimum) and `rig.sweep_pose(root, 180)` (all maximum). Then `rig.reset_preview(root)`.
`rig.describe(root)["problems"]` lists errors and warnings; fix errors, report warnings.

## 5. Export and verify

```python
res = rig.export("Machine", r"C:\path\machine.dequip")   # ask the user where, if unknown
check = rig.verify("Machine")
```

- `check["max_error"]` must be below 1e-4 (meters). Otherwise report it with `check["worst"]`.
- When the file exists, its capabilities scripted in dProB are kept (`res["kept_capabilities"]`);
  pass `keep_capabilities=False` only if the user wants a fresh equipment. Generated IK
  capabilities and the Crane type are replaced on every export.
- `res["ik_capabilities"]`, `res["crane"]` list what was generated.
- Tell the user: the file, number of parts and joints, the capabilities, warnings, what the rig
  cannot show (ropes that do not stretch, cylinders that do not extend), and that dProB should
  replace the earlier asset when they import it again.

- `rig.verify_ik(target, [points])` runs the exported IK capability (the graph in the file)
  and compares it with the preview; run it for a few of your IK test targets.
- Check the direction: `rig.to_dprob(root, point_in_front_of_the_machine)` must have a positive
  Z (forward) and the right height in Y (up).

`rig.read_dequip(path)` shows what a .dequip contains (parts, joints, capabilities).

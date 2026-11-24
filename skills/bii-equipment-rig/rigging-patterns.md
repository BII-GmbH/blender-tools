# Rigging patterns

How common mechanisms are built from the rig's joints. These are building blocks, not recipes
for one machine: combine them to match the model you have, and ask the user about motions and
ranges you cannot see. Names below are examples.

The rules behind all of them:

- A joint moves its object and all children, about or along an axis through the object's origin.
- One joint per object. Two motions at one place need two objects in a chain (use
  `rig.insert_joint` for an empty in between).
- Parenting order is the order of motion: what moves another part is its parent.
- What must stay level or keep its direction while its parents move needs a joint of its own that
  follows the joints moving it (Drive: Follow).

## Hinge (boom, arm, door, bucket, flap)

Revolute joint on the moving part, origin on the hinge pin, axis along the pin. Find the pin from
the mesh: the bolt's or the bore's vertices, not the part's bounding box. If the part's origin is
elsewhere and must stay there, `rig.insert_joint` an empty at the pin and parent the part below it.

## Slewing / turntable (upper carriage, tower crane slewing, excavator house)

Revolute about the vertical axis through the slewing ring's center, usually unlimited. Everything
that turns with it is below it. Outriggers, tracks and the chassis are not.

## Telescope (telescopic boom, extendable arm, outrigger beams)

One prismatic joint per section, each section the child of the one it slides out of, axis along
the boom. Limits per section (0 = retracted as modeled). Sections that extend together: let the
others Follow the first with factor 1 (or the ratio the machine has). Without that the IK picks
any split between them, which is fine for most uses.

## Part that stays level (hook block, platform, fork, gripper)

The part's parent turns (a luffing boom), but the part must keep its direction. Insert a joint at
the boom tip, between the boom and the part, with the same axis direction as the luffing joint
(often opposite sign) and Follow the luffing joint with factor 1 or -1:

```python
name = rig.insert_joint("Tip Level", "Boom Tip", ["Hook Block"], tip_pin_world, axes_from=root)
rig.set_joint(name, "REVOLUTE", axis="-X", limits=boom_limits)
rig.set_follow(name, [("Boom", 1)])
```

Several luffing joints in the chain (knuckle boom, luffing jib on a boom): Follow all of them,
each with its factor. Check in the preview: the part's world rotation must not change when any
leader moves.

## Hoist, rope and hook (mobile crane, tower crane, gantry)

- Rope length: a prismatic joint pointing down on the part that hangs, its origin at the rope's
  top (an empty at the boom tip or trolley that slides down, with the hook block as its child). It
  only points down when everything above keeps it level: below a luffing boom it needs the level
  joint above (previous pattern); below a trolley on a horizontal jib it is level anyway.
- Hook spin: a revolute joint about the rope axis, unlimited, so the IK can turn a load.
- Load point: `rig.add_point(name, hook, saddle_point, axes_from=root)`, a fixed empty at the exact
  point a load hangs from (the hook's saddle, not the hook's origin), marked as attachment point.
  That is the IK target and the crane hook; cargo hangs in its axes, so give it the root's axes.
- Typical chain: tip/trolley -> level joint (if luffing) -> rope (prismatic down) -> hook spin
  (revolute) -> hook block and hook -> load point (fixed, attachment point).
- Ropes cannot stretch: a rope mesh stays as long as modeled. Put rope meshes on the hook block
  (they move down with it and leave a gap at the top) or on the boom head (a gap above the block),
  hide them, or keep them short; tell the user which.
- A hook block modeled tilted or lashed to the vehicle for transport: re-pose it hanging (rotate
  its parts about the block axle), hide the lashing, after asking.

## Trolley (tower crane, gantry, overhead crane)

Prismatic along the jib or girder, below the slewing (tower crane) or the bridge (gantry). The
hoist hangs below the trolley. No level joint is needed while the jib does not luff.

## Excavator, loader, knuckle boom

A chain of hinges: boom on the house, stick (arm) on the boom, bucket or tool on the stick. The
load point is where the tool works (bucket teeth or the bucket's pivot, as the user wants), as an
attachment point on the tool. Whether the tool must keep its angle: ask. If yes, a Follow on the
tool joint (sum of the boom and stick joints, with signs) keeps it, else the IK may turn the tool.

## Hydraulic cylinders

Joints cannot keep a cylinder between two moving pins (no aim constraint in dProB). Options:

- Leave the cylinder and piston as parts of what they move (cylinder on the house, piston on the
  boom); they come apart or overlap at other angles. Simplest; tell the user.
- Revolute joints at each pin that Follow the luffing joint with a fitted factor (the cylinder's
  angle changes about linearly with the boom's). Fit the factor from the geometry at a few boom
  angles, check the worst angle and gap, and report them. It only moves in the generated IK
  capabilities (they set followers of joints in the chain), not in other scripts that move the
  boom alone. Real strokes longer than the modeled cylinder still pull the piston out.

## Vehicle base

The chassis is usually the rig root or fixed below it. Outrigger beams and jacks are prismatic
joints that IK capabilities of the arm never move (they are not above the target, or use Hold).
Wheels and tracks are not driven by the rig.

## IK target and root

- Target: the attachment point where the work happens. Root joint: the lowest joint the
  capability may move; leave it empty to move everything above the target.
- Joints the capability must not touch: Hold.
- Test many targets (near, far, high, low, all around, exactly behind). Reaching close to the
  machine often needs a joint to leave its limit first (a boom lying flat must rise); "Start Away
  from Limits" (seed margin) handles that, raise it if a reachable close target stays off. A
  target behind the boom needs the slewing to turn half round; the capability turns the top-most
  unlimited joint towards the target first, so give the slewing no limits if it has none.

## Crane (anything that carries cargo)

`rig.set_crane_hook(root, load_point)` adds dProB's Crane type on the load point's IK capability:
Move Arm, Lift, Deliver. It fits every machine that lifts and sets down cargo at one point:
mobile and tower cranes, excavators with a hook or a gripper, loader arms, gantries. Lift and
Deliver attach the cargo to the load point, so the load point's frame is the frame the cargo
hangs in.

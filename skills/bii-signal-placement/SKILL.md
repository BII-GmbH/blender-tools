---
name: bii-signal-placement
description: Place DB railway signals and signs (Ks, H/V, Zs, Lf, Ne, Ls) in a Blender project from the symbols on a placed plan (Signallageplan, Lageplan), with the BII Import plugin's Signal-Baukasten. Use when the user asks to place, configure, check or fix signals or signs from plans. The plans must be placed first (skill bii-plan-placement).
---

# Place signals from plans (BII Import)

Work through Blender's MCP (`execute_blender_code`). Do every step in order. Most mistakes in
earlier projects came from skipping steps 2, 4 and 7.

## Before you start

- **Plans placed and checked** with the skill `bii-plan-placement`. A signal is only as right as the
  plan under it.
- Functions: `from bl_ext.user_default.bii_blender_import import plan_api, signal_panel as sp,
  signal_spec as spec, signal_legend`. The extension's `README.md` (sections Signals and Scripting)
  has the details. `result` in `execute_blender_code` must be a dict.
- **Signal models:** the signals come from the BII asset library (`DB_Signale.blend`), which the
  user must have added under Preferences > File Paths > Asset Libraries. Check first:
  `sp.library_path(bpy.context, "", "Ks-Hauptsignal")[0]` is None: no library has the signals (or
  only an old one); tell the user `sp.missing_text(bpy.context, "", "Ks-Hauptsignal")` and stop.
- Place, change and read signals only with `sp.place_signal`, `sp.configure_signal` and
  `sp.read_signal`: they load the model from that library, set the modifier and check every value.
- **Terrain and track axes** of the BII Umgebung plugin: signals stand on the terrain and turn
  along the track axes. If they are missing, ask the user whether to load them (skill
  `bii-plan-placement`, step 2).

## 1. Symbol key

Write the key once into the project's work folder, per group, so each image stays small:

```python
for group in spec.GROUPS:   # Ks, H/V, Zusatzsignale, Langsamfahrsignale, Nebensignale, Rangiersignale
    signal_legend.write(rf"{folder}\key_{group.replace('/', '')}.png", bauformen=[group])
```

Every tile shows a symbol, its option in words, the value for `place_signal` (`zs3="tafel"`) and a
**red dot where the signal's foot is**. Filled triangle = Lichtsignal, empty = Tafel. Mast is not in
the symbol: a symbol drawn without a stem stands on a Signalbrücke or another mast (`mast=False`).

## 2. Inventory: every symbol, nothing left out

New signals are drawn in red on a Signallageplan (ask the user if another color is used).

**PDF plans: the plugin reads and places the symbols itself.** The vector drawing gives the
exact foot, the drawing direction and the configuration of every symbol on a mast and of every
Vorsignalbake, without reading pixels. On a checked project this was right for 47 of 49 found
signals, better than reading the symbols by eye, so place these first and spend your time on
the checks and on what it cannot read:

```python
coll = bpy.data.collections["Signale"]
for s in plan_api.vector_symbols(plan, "red"):
    # s["kind"]: "mast", "free" (head without mast, mast=False) or "ne3"; s["signals"]: [(bauform, config)]
    # (empty: not a signal, s["reason"] says why); s["foot"], s["head"] in IMAGE pixels
    for bauform, cfg in s["signals"]:
        sig = sp.place_signal(bauform, cfg, location=s["foot_world"], head=s["head_world"],
                              align_to_track=True, on_terrain=True, collection=coll)
        sig["plan_sheet"] = plan.name
```

Sheets overlap: place a symbol shown on two sheets once (same Bauform within 1 m). Then
continue with the inventory below, which now only has to find what is left: symbols drawn
without a foot bar that the vector search did not find (a signal on a Signalbrücke or on another
signal's mast, `mast=False`, its foot is the end of its leader at the track), and anything else
it missed. Plan labels and km come from the text beside the symbol. Spot-check the placed signals
in step 7 like your own.

```python
groups = plan_api.color_clusters(plan, "red")   # [{"box": IMAGE pixels, "center": world, "pixels"}]
for i, g in enumerate(groups):
    plan_api.crop_image(plan, *g["box"], rf"{folder}\red_{i:02}.png", pad=40, scale=2)
```

Make a table with one row per group: what it is (signal, sign, label/km text, note, other equipment
such as GSA/GSE track circuit marks), its plan label (e.g. 06N601) and km. A group can hold several
symbols and their labels; split it then. **Every group gets a row**, also when you read the symbols
from the PDF's vector drawing instead: counting masts or stems is not an inventory, because signs
without a stem of their own are missed that way. Before placing, check the table against the plan
once more ("list every red symbol in this crop") so nothing is missed.

Signs that were missed or mistaken before:
- **Vorsignalbake (Ne 3, `ne3`)**: a narrow plate, about 8 × 2 mm on paper, with 1 to 5 diagonal
  stripes, held by a short support bar at one end; the km is printed beside it. Usually 2 or 3 of
  them stand in a row about 75 m apart before a Vorsignal, the nearest one with 1 stripe. They are
  not cable or duct marks.
- **Schachbretttafel (Ne 4, `ne4`)**: a small square with a checkerboard (2 × 2 or more fields).
- **Lf 6 / Lf 7 plates**: a triangle (Lf 6) or an upright rectangle (Lf 7) with a printed number.

## 3. Classify each signal

Compare the symbol crop with the key image of its group, attribute by attribute, not "which signal is this".
Work on a crop turned upright (`up=`, see below): the plan draws symbols at any angle, and "above",
"below", `^` and `V` only mean something on an upright crop.
- head shape (Ks: rounded top = Hauptsignal, rounded bottom = Vorsignal, rounded both ends =
  Mehrabschnittssignal; H/V: circles / two dots);
- Bedienung, from the head alone: completely filled = `stellwerk`; one half filled along its length
  (the other half empty, split by a line from the top to the bottom of the head) =
  `stellwerk_zug`; empty = `zug`. The Sh1 part (two bars across an extra part of the head, see
  below) is not part of this: a filled head with an Sh1 part is `stellwerk`, even though the Sh1
  part shows white fields. Placing `stellwerk_zug` for a filled head was the most common mistake;
- the chevron at the far end of the symbol: its tip points away from the head = Zs1 (`kopf="zs1"`,
  `^` on an upright crop); its tip points towards the head, open side outwards = Zs7
  (`kopf="zs7"`, `V` on an upright crop). Decide by the tip relative to the head, never by how the
  chevron looks on the sheet;
- above the head: triangle (Zs3) filled or empty; below the head: triangle (Zs3v)
  filled or empty, square with diagonal (Zs6); small circle at the head and where (Zusatzlicht);
  two bars across the head, which is longer by them (Sh1; on a half filled or empty head two empty
  fields below the fill);
- a stem with a foot bar, or no stem (Signalbrücke: `mast=False`); for Lf 6/Lf 7 and Zs3/Zs3v plates
  the printed number (Kennziffer = km/h / 10). Symbols are drawn turned, so read numbers on a crop
  turned upright: `plan_api.crop_image(plan, *box, path, pad=40, scale=3, up=(hu - fu, hv - fv))`
  with foot (fu, fv) and head (hu, hv) in IMAGE pixels. On a crop that is not upright a 6 and a 9
  look the same.

Cross-check Bedienung and Zs1/Zs7 with a measurement on the plan's pixels (Ks signals):

```python
m = plan_api.symbol_features(plan, (fu, fv), (hu, hv), 'IMAGE')   # foot, a point towards the head
# m["bedienung"], m["chevron"], m["rows"], m["sure"]
```

When `m["sure"]` is True and it differs from your reading, look at an upright crop of the head again
(`scale=4`) and decide; say in the report where you went against the measurement and why. None
means it could not measure: rely on your reading.

Write down `bauform` and `config` with the values from the key, e.g.
`("ks_mehr", {"bedienung": "stellwerk_zug", "zs3": "licht", "zs3v": "licht", "kopf": "ohne", "mast": False})`.
Two triangles, one above and one below, means both `zs3` and `zs3v`. When a detail is unclear,
crop larger (`scale=3`) and ask again; when still unclear, list it as uncertain instead of guessing.

## 4. Foot point and head point

The signal belongs at the **foot of its symbol**: the end of the stem, the short bar across it (the
red dot in the key), never the middle of the symbol or of its head. Without a mast of its own
(`mast=False`, e.g. on a Signalbrücke) the symbol still has a leader line from the head towards
the track or the bridge: the foot is the end of that leader at the track, the head the other end.
Which end is which: the foot is the end nearer the track line it belongs to (on it or just beside
it); check it with the distance of both ends to the nearest track, never by which end has more red
around it. Missed twice for 06F/06FF (8 to 19 m off along the leader). Only when there is no line at all: the
lower end of the symbol. The leader is not always straight (it can bend or run crooked):
follow it to its own end and take the foot bar there, never a point found by extending the
direction of the head or of the leader's first part. Measure the pixel position of
the foot bar's center in the crop, then add the crop's offset (`box[0] - pad`, `box[1] - pad`, divided by
`scale` first) to get IMAGE pixels of the plan.

Measure the **head point** too: the center of the far end of the symbol (the top of
the topmost mark, e.g. the tip of the Zs 1 chevron or of the head). From head to foot is the
direction the signal faces: a symbol drawn with its top to the west and its foot to the east looks
upright when seen from the east, so the signal faces east. Symbols are drawn rotated in any angle;
never take the facing from the side of the track or the km direction.

## 5. Place

```python
sig = sp.place_signal("ks_haupt", {"zs3": "tafel"},
                      location=plan_api.plan_to_world(plan, u, v, 'IMAGE'),          # foot
                      head=plan_api.plan_to_world(plan, u_head, v_head, 'IMAGE'),    # top end
                      align_to_track=True, on_terrain=True,
                      collection=bpy.data.collections["Signale"])
sig["plan_label"] = "06N601"
```

- `on_terrain`: the signal stands on the BII Umgebung terrain, not on the plan sheet above it.
- `head`: the signal faces from the head towards the foot, as its symbol is drawn.
- `align_to_track`: the signal turns along the nearest track, in the track direction closer to the
  symbol's. Leave it off for a signal whose symbol is drawn across the track or far from any track.
- Keep the name `place_signal` gives ("<Name> <Tag>", e.g. "Vorsignal Ks1/Ks2/Zs3v"); put the plan
  label into the custom property `plan_label`.
- `kennziffer` exists only on the Bauformen `zs3`, `zs3v`, `lf6` and `lf7` (standalone plates).
  For the Zs3/Zs3v of a Ks or H/V signal, put the number into the custom property
  `sig["kennziffer"]` and into the report.
- A `ValueError` lists the allowed values: fix the configuration, do not work around it.
- Change a placed signal with `sp.configure_signal(sig, {"zs3v": "licht"})` (another Bauform:
  `bauform="ks_mehr"`, which returns a new object).

## 6. Signals the plan only names

Signals in a table or only as text (no symbol): place them only if the position is clear (km and
side), and say so in the report.

## 7. Check (always)

```python
rows = []
for o in bpy.data.collections["Signale"].objects:
    b, cfg, aspect = sp.read_signal(o)
    u, v = plan_api.world_to_plan(plan, o.location, 'IMAGE')
    ground = sp.terrain_height(bpy.context, o.location.x, o.location.y)
    rows.append((o.get("plan_label"), b, cfg, round(u), round(v), o.location.z - (ground or o.location.z),
                 round(math.degrees(o.rotation_euler.z))))
```

- Configuration equals the inventory table; height above ground is 0; every inventory row that is a
  signal or sign has exactly one object.
- **Every red group is accounted for** (do not skip this, also when you read the symbols from the
  PDF's vector data):

  ```python
  for i, g in enumerate(plan_api.groups_without_signal(plan)):     # red groups with no signal at them
      plan_api.crop_image(plan, *g["box"], rf"{folder}\left_{i:02}.png", pad=60, scale=3)
  ```
  Look at each crop ("is there a signal or sign symbol in it, and which?"). Each group must be a
  row of your table that says what it is (label or km text, GSA/GSE, INA magnet, note) or get its
  signal placed now. Vorsignalbaken (Ne 3) were missed twice because the inventory only counted
  symbols with a leader and foot bar; they show up here.
- **Every vector symbol is accounted for** (PDF plans): `plan_api.symbols_without_signal(plan)`
  lists the symbols on a mast and Ne 3 boards with no signal within 1 m of their foot. Place each
  one now, or name it in the report as not a signal (dimension arrow, note leader). Also: a placed
  signal more than 1 m from every vector foot (and not on a Signalbrücke) is in the wrong place;
  move it to the foot.
- **Vorsignalbaken before every Vorsignal**: a Vorsignal (`ks_vor`, `hv_vor`, also the distant
  signal part of a `ks_mehr`) usually has 1 to 3 Ne 3 boards on its approach side, about 75, 150
  and 225 m before it, the nearest with 1 stripe. For each placed Vorsignal, crop the plan along
  its track 50 to 300 m before it (`world_to_plan`, then `crop_image`, `scale=3`) and ask "is
  there a small plate with diagonal stripes, and how many stripes?". Place every board found.
  A Vorsignal without any board is possible (short distances, boards on another sheet), but say
  so in the report. Ne 3 boards were missed in four of seven runs.
- Position: mark the placed feet on the plan and compare: crop the plan around a
  group of signals, draw a small circle at each `world_to_plan` position (Pillow), ask "is each
  circle on the foot bar of its symbol?".
- Facing: for each signal compare its direction (lamps along local -Y: `(sin z, -cos z)`) with its
  symbol's head-to-foot direction on the plan; they must point the same way. Report the facing as
  km rising or falling.

## 8. Report

A table: plan label, km, Bauform and configuration, facing (km rising/falling), and anything
uncertain or not placed, with the reason. Do not decide uncertain cases silently.

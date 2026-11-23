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

New signals are drawn in red on a Signallageplan (ask the user if another color is used):

```python
groups = plan_api.color_clusters(plan, "red")   # [{"box": IMAGE pixels, "center": world, "pixels"}]
for i, g in enumerate(groups):
    plan_api.crop_image(plan, *g["box"], rf"{folder}\red_{i:02}.png", pad=40, scale=2)
```

Make a table with one row per group: what it is (signal, sign, label/km text, note, other equipment
such as GSA/GSE track circuit marks), its plan label (e.g. 06N601) and km. A group can hold several
symbols and their labels; split it then. **Every group gets a row.** Before placing, check the table
against the plan once more ("list every red symbol in this crop") so nothing is missed:
Vorsignalbaken (Ne3, plates with 1 to 5 diagonal stripes) and Lf 6/Lf 7 plates were missed before.

## 3. Classify each signal

Compare the symbol crop with the key image of its group, attribute by attribute, not "which signal is this":
- head shape (Ks: rounded top = Hauptsignal, rounded bottom = Vorsignal, rounded both ends =
  Mehrabschnittssignal; H/V: circles / two dots), filled, right half filled or empty (Bedienung);
- above the head: `^` (Zs1), `V` (Zs7), triangle (Zs3) filled or empty; below the head: triangle (Zs3v)
  filled or empty, square with diagonal (Zs6); small circle at the head and where (Zusatzlicht);
  two bars across the head, which is longer by them (Sh1; on a half filled or empty head two empty
  fields below the fill);
- a stem with a foot bar, or no stem (Signalbrücke: `mast=False`); for Lf 6/Lf 7 and Zs3/Zs3v plates
  the printed number (Kennziffer = km/h / 10).

Write down `bauform` and `config` with the values from the key, e.g.
`("ks_mehr", {"bedienung": "stellwerk_zug", "zs3": "licht", "zs3v": "licht", "kopf": "ohne", "mast": False})`.
Two triangles, one above and one below, means both `zs3` and `zs3v`. When a detail is unclear,
crop larger (`scale=3`) and ask again; when still unclear, list it as uncertain instead of guessing.

## 4. Foot point and head point

The signal belongs at the **foot of its symbol**: the end of the stem, the short bar across it (the
red dot in the key), never the middle of the symbol. Without a stem: the lower end of the symbol,
or for a signal on a bridge the point on the bridge line. Measure the pixel position of
the foot bar's center in the crop, then add the crop's offset (`box[0] - pad`, `box[1] - pad`, divided by
`scale` first) to get IMAGE pixels of the plan.

Measure the **head point** too: the center of the far end of the symbol (the top of
the topmost mark, e.g. the tip of the Zs 1 chevron or of the head). From head to foot is the
direction the signal faces: a symbol drawn with its top to the west and its foot to the east looks
upright when seen from the east, so the signal faces east. Symbols are drawn rotated in any angle;
never take the facing from the side of the track or the km direction.

## 5. Place

```python
sig = sp.place_signal("ks_haupt", {"zs3": "tafel", "kennziffer": 6},
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
- Position: mark the placed feet on the plan and compare: crop the plan around a
  group of signals, draw a small circle at each `world_to_plan` position (Pillow), ask "is each
  circle on the foot bar of its symbol?".
- Facing: for each signal compare its direction (lamps along local -Y: `(sin z, -cos z)`) with its
  symbol's head-to-foot direction on the plan; they must point the same way. Report the facing as
  km rising or falling.

## 8. Report

A table: plan label, km, Bauform and configuration, facing (km rising/falling), and anything
uncertain or not placed, with the reason. Do not decide uncertain cases silently.

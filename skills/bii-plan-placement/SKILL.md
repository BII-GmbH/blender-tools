---
name: bii-plan-placement
description: Import plans (Lagepläne, Signallagepläne, Ansichten; PDF or TIFF) into a Blender project with the BII Import plugin, place them correctly (grid crosses, existing plans, neighbor sheets, Kilometrierung), crop them, and check the placement against existing plans and tracks. Use for any "load / place / position / align / crop plans" task in Blender, in empty projects and in projects that already have plans.
---

# Place plans in Blender (BII Import)

Work through Blender's MCP (`execute_blender_code`). Do every step in order. Do not skip the
checks in step 6: a plan that is a few meters off ruins everything placed from it later.

## Rules

- **Images:** if you cannot view images yourself (heimdall-claude / edda), give every viewing task
  to the `heimdall:image-reader` subagent: one question per call, at most 2 images, and name exactly
  what to report (numbers, pixel positions, "yes/no and by how many meters"). Do what you can with
  numbers first (Python, Pillow, numpy).
- **Do not rebuild** what is in the scene. Do not move plans that are already placed unless the user
  asks; if you find one wrong, report it and ask.
- **Never** apply scale or rotation of a plan object, never Set Origin, never edit its mesh by hand:
  the mesh is in sheet meters and the scale is the Maßstab.
- Use the functions below, not your own math. They live in the installed extension:
  `from bl_ext.user_default.bii_blender_import import plan_api, import_plan`. The extension's
  `README.md` (next to its `__init__.py`) explains everything in more detail.
- `result` in `execute_blender_code` must be a dict.

## Coordinates on a plan

`plan_api` takes points in one of four units; always say which:

| unit | meaning |
|---|---|
| `'PDF'` | PDF points of the page (1/72 inch), from the top left, y down |
| `'IMAGE'` | pixels of the plan's image in Blender (`plan.bii_plan.image.size`), from the top left, y down |
| `'SOURCE'` | pixels of the original TIFF/PNG file, from the top left, y down |
| `'SHEET'` | meters on the paper from the bottom left, y up |

`plan_api.plan_to_world(plan, u, v, unit)` gives the scene position, `plan_api.world_to_plan(plan, point, unit)`
the way back. Pixel positions you measure on the original TIFF are `'SOURCE'`; on a PDF use `'PDF'`.

## 1. Look at the project

```python
import bpy
from bl_ext.user_default.bii_blender_import import plan_api, plan_place
scene = bpy.context.scene
plans = plan_api.plans(scene)
result = {
  "blend": bpy.data.filepath,                       # "" = not saved yet
  "origin": (scene.get("lat"), scene.get("lon")),   # Umgebung / georeference origin
  "plans": [(p.name, p.bii_plan.orientation, p.bii_plan.massstab, len(p.bii_plan.points),
             len(p.bii_plan_place.km_points), getattr(p.bii_plan_place.attach_parent, "name", None)) for p in plans],
  "tracks": len(plan_place.track_polylines(scene, "")),
  "terrain": [o.name for o in scene.objects if o.get("dem_service")],
}
```

- **The .blend file must be saved** in the project folder before you import. Otherwise the plan
  images go into a temporary folder or are packed. Ask the user where to save it if it is not.
- Existing plans: note how each was placed (grid points, km points, attached to a neighbor). They are
  the references for the new ones.

## 2. Import

```python
bpy.ops.import_image.bii_plan(filepath=r"...\plan.pdf", massstab=1000, orientation='FLAT')
plan = bpy.context.view_layer.objects.active
```

Several sheets of one plan: `bpy.ops.import_image.bii_plan_sheets(directory=..., files=[{"name": ...}, ...], massstab=1000)`.
Read the Maßstab from the title block (image-reader) if the user did not give it. Ansichten and
Schnitte are `orientation='UPRIGHT'`; everything below is for flat plans.

## 3. Choose how to place it (first that applies)

1. **The plan has a coordinate grid** (crosses with Rechtswert/Hochwert, Gauß-Krüger or UTM):
   Georeference. Most accurate (a few cm).
2. **It overlaps a plan already placed** and has no grid: Align to Scene with points on features both
   plans show (switch tips, platform ends, bridge edges, track crossings), or attach it as neighbor
   sheet if it continues a placed sheet at a match line.
3. **Only Kilometrierung** (hectometer labels): Align by Kilometrierung. Least accurate: the scene's
   Kilometrierung comes from OpenStreetMap and can be 20 m and more off along the track. Add
   landmarks (Align to Scene points) whenever you can.

### Georeference

Find at least 4 grid crosses near the corners (image-reader: "pixel position of the cross center and
the coordinate labels printed at it"). Then:

```python
scale, rms, worst, warnings = plan_api.georeference(
    plan, [(u, v, easting, northing), ...], unit='SOURCE', crs='DB_REF')   # crs as printed on the plan
```

`crs` from the plan's title block or legend: `'DB_REF'` (DB plans: DB_REF, DB-Referenzsystem,
Gauß-Krüger values with a 7-digit Rechtswert), `'DHDN'` (older Landesvermessung Gauß-Krüger), or
`'ETRS89'` (UTM, 8-digit easting with zone, or 6 digits; give `utm_zone=32` or `33`). When unsure,
ask the user. The measured Maßstab must be the printed one (±0.5 %); if not, the crs or a cross is
wrong.

### Align to Scene (onto a placed plan)

```python
other = bpy.data.objects["placed plan"]
pairs = [(u_new, v_new, plan_api.plan_to_world(other, u_old, v_old, 'IMAGE')), ...]  # same feature on both
residuals = plan_api.align_to_scene(plan, pairs, unit='IMAGE')   # 3+ pairs far apart
```

### Neighbor sheet

```python
plan_api.attach_to_neighbor(plan, other, own=[(u1, v1), (u2, v2)], other=[(u1, v1), (u2, v2)], unit='IMAGE')
```
First point: where the track crosses the match line; second: another point on the match line.

### Kilometrierung

```python
lines = plan_api.align_by_km(plan, [(u, v, "53,9"), (u, v, "54,2"), (u, v, "")], strecke="5102", unit='SOURCE')
```
Points on the plan's track at printed km; `""` = a point that only lies on the track.

## 4. Crop

```python
rect = plan_api.auto_crop(plan)          # removes empty paper and the sheet frame
```
Auto Crop keeps the title block and legends inside the frame. Ask image-reader whether the cropped
part still contains title block or legend, and trim with `plan_api.crop(plan, u0, v0, u1, v1, unit)`.
Keep all plan drawing, track stubs and "Anschluss Blatt" labels.

## 5. Height

Plans keep the height they were imported at. They hide terrain and objects below them in top views:
for renders that check other objects, hide the plans.

## 6. Check the placement (always)

```python
for r in plan_api.check_placement(plan):
    print(plan_api.describe(r))
```

It compares the plan with every flat plan it overlaps and with the Umgebung track axes.

- `reliable` and `distance` ≤ 1 m against a plan: fits. More: something is wrong; find out which plan.
- Against the track axes (OpenStreetMap): up to about 5 m is normal; more is suspicious.
- `along_deg` set: the position along that direction is not fixed by the drawing (straight track).
- Not reliable ("no clear match"): the drawings differ. On scanned Lagepläne and between different kinds
  of plan this is the normal case, so the overlay images below are the real check there:

```python
for i, c in enumerate(plan_api.overlap_centers(plan, other, 3)):   # other: a plan or "TRACKS"
    plan_api.overlay(plan, other, c, rf"{folder}\overlay_{i}.png", size=80)
```
Send each image to image-reader alone: "Red is plan A, blue is plan B (or the track axes), dark
purple where both lie, grid 10 m, north up. Do the red track lines lie on the blue ones? If not, by
how many meters and in which direction (right/left/up/down)?"

Also check: Georeference RMS ≤ 0.5 m and largest residual ≤ 1 m (more: a misread cross; fix it,
don't drop it silently). Neighbor sheets: their match line shows no step.

## 7. Report

For each plan: how it was placed, the numbers (Maßstab, residuals), the check results against each
plan and the tracks, and what stayed uncertain. Say plainly when a check could not decide.

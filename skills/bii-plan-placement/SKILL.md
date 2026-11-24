---
name: bii-plan-placement
description: Import plans (Lagepläne, Signallagepläne, Ansichten; PDF or TIFF) into a Blender project with the BII Import plugin, place them correctly (grid crosses, existing plans, Kilometrierung, neighbor sheets) in the scene of the BII Umgebung plugin (project coordinates, Kilometrierung, track axes, terrain; asks before loading them), crop them, and check the placement against existing plans and tracks. Use for any "load / place / position / align / crop plans" task in Blender, in empty projects and in projects that already have plans.
---

# Place plans in Blender (BII Import)

Work through Blender's MCP (`execute_blender_code`). Do every step in order. Do not skip the
checks in step 7: a plan that is a few meters off ruins everything placed from it later.

Plans are placed into the scene the **BII Umgebung** plugin builds: its project coordinates
(`scene["lat"]` / `scene["lon"]`) are what grid crosses are converted into, its Kilometrierung
lines are what Kilometrierung placement fits to, its track axes are what the checks compare with,
and its terrain is what the plans are placed over.

## Rules

- Do what you can with numbers first (Python, Pillow, numpy); look at images where numbers
  cannot decide.
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
  "umgebung": hasattr(scene, "railway_chainage"),         # BII Umgebung installed
  "kilometrierung": plan_place.scene_strecken(scene),     # Strecken with a Kilometrierung line
  "tracks": len(plan_place.track_polylines(scene, "")),   # Umgebung track axes
  "terrain": [o.name for o in scene.objects if o.get("dem_service")],
}
```

- **The .blend file must be saved** in the project folder before you import. Otherwise the plan
  images go into a temporary folder or are packed. Ask the user where to save it if it is not.
- Existing plans: note how each was placed (grid points, km points, attached to a neighbor). They are
  the references for the new ones.

## 2. Umgebung data (ask before loading)

Find out what the plans need, then ask the user before loading anything (it downloads data and
changes the scene):

- **No terrain:** ask whether to load the terrain (Umgebung: Gelände und Korridor, with a height
  model or flat).
- **No Kilometrierung line for the plan's Strecke**, and the plan has no grid and does not overlap a
  placed plan (step 4, method 3): ask whether to load the Kilometrierung of that Strecke.
- **No track axes:** ask whether to load them; Check Placement and the signal placement use them.
- **Project coordinates:**
  - Scene with an origin: everything fits to it; keep it.
  - Empty scene and a plan with grid crosses: place that plan first (Georeference sets the origin to
    the plan's center), then load the Umgebung data around it; Umgebung then uses that origin.
  - Empty scene and no plan with a grid: Umgebung needs the area first. Ask the user for the place
    (station, coordinates or Strecke and km range).
  - Umgebung objects but no origin: Georeference reports it; tell the user, don't guess.
- Umgebung is not installed: tell the user; only Align to Scene onto placed plans works without it.

If the user wants you to load it (otherwise they use the sidebar tab Umgebung), for example around
the placed plans:

```python
from mathutils import Vector
from bl_ext.user_default.bii_blender_import import plan_geo
rc = scene.railway_chainage
proj = plan_geo.SceneProjection(scene["lat"], scene["lon"])
corners = [p.matrix_world @ Vector(c) for p in plans_to_cover for c in p.bound_box]
geo = [proj.to_geographic(c.x, c.y) for c in corners]          # (lat, lon)
m = 0.005                                                       # about 500 m margin
rc.area_mode = 'BBOX'
rc.min_lat, rc.max_lat = min(g[0] for g in geo) - m, max(g[0] for g in geo) + m
rc.min_lon, rc.max_lon = min(g[1] for g in geo) - m, max(g[1] for g in geo) + m
rc.use_scene_origin = True
bpy.ops.railway_chainage.scan()                                 # Strecken in the area
for r in rc.routes:
    r.selected = r.ref == "5102"                                # the plan's Strecke
bpy.ops.railway_chainage.generate()                             # Kilometrierung lines
bpy.ops.railway_chainage.generate_rails()                       # track axes
rc.terrain_source = 'DEM'                                       # or 'FLAT'
bpy.ops.railway_chainage.import_terrain()                       # terrain along the Kilometrierung
```

Each call reports errors (no network, no access to the data server): pass them on to the user.
The Umgebung plugin's `README.de.md` explains the settings.

## 3. Import

```python
bpy.ops.import_image.bii_plan(filepath=r"...\plan.pdf", massstab=1000, orientation='FLAT')
plan = bpy.context.view_layer.objects.active
```

A scan lying on its side or upside down: import it with `rotation='90'` (clockwise), `'180'` or
`'270'` so the text reads upright. Never turn the file yourself (copies lose their resolution and
the `'SOURCE'` pixels): with `rotation`, `'SOURCE'` stays the pixels of the original file.

Several sheets of one plan: `bpy.ops.import_image.bii_plan_sheets(directory=..., files=[{"name": ...}, ...], massstab=1000)`.
Read the Maßstab from the title block if the user did not give it. Ansichten and
Schnitte are `orientation='UPRIGHT'`; everything below is for flat plans.

## 4. Choose how to place it (first that applies)

1. **The plan has a coordinate grid** (crosses with Rechtswert/Hochwert, Gauß-Krüger or UTM):
   Georeference. Most accurate (a few cm).
2. **It overlaps a plan already placed** and has no grid: Align to Scene with points on features both
   plans show (switch tips, platform ends, bridge edges, track crossings), spread over the whole
   overlap.
3. **Kilometrierung** (hectometer labels): Align by Kilometrierung. Needs the Umgebung
   Kilometrierung line of the plan's Strecke (step 2). That Kilometrierung comes from
   OpenStreetMap and can be 20 m and more off along the track. Add landmarks (Align to Scene
   points) whenever you can.
4. **Neighbor sheet** only when nothing above works: attach it to a placed sheet at their match line.
   Each attached sheet adds the error of its match line, so a chain of sheets drifts further at every
   sheet. Do not chain more than one or two sheets; place each sheet by 1 to 3 where it can be done.

A sheet with too few grid crosses, points or km of its own can combine them: e.g. Kilometrierung
plus the match line to a sheet placed by grid crosses (Align by Kilometrierung keeps the match line
with Neighbor Weight).

### Georeference

Let the plugin find the grid; never look for crosses yourself:

```python
found = plan_api.find_grid(plan)          # None: no grid; else crosses, spacing_m, angle
images = plan_api.grid_labels(plan, folder)
```

`find_grid` works on the original file at full resolution and finds every cross, also crosses that
touch other lines. Do not decide "this plan has no grid" from your own look at a downscaled
image: run `find_grid`. `grid_labels` writes images with numbered boxes, each a place along a grid
line where a coordinate number may be printed, shown as printed and turned by 180°. Ask about each
image with exactly `plan_api.GRID_LABEL_QUESTION`, collect the answers as `(box id, number)`
(`images[k]["boxes"][n - 1]` is the id of box n), then:

```python
scale, rms, worst, warnings = plan_api.place_from_grid(plan, [(box_id, 3589300), ...], crs='DB_REF')
```

It checks every number against the grid, leaves misreads out (they appear in `warnings`) and
refuses when the numbers allow more than one placement: then read more boxes. Never fill in
numbers you estimated from the plan's current position: a placement that starts from a wrong
position confirms itself. Only when the plan prints no number at all, say so in the report and
place it another way.

Points you measured yourself still work:

```python
scale, rms, worst, warnings = plan_api.georeference(
    plan, [(u, v, easting, northing), ...], unit='SOURCE', crs='DB_REF')   # crs as printed on the plan
```

Small residuals do not prove a placement: a wrong assignment of numbers to lines fits just as well.
The proof is a number printed on the plan that lands on its own grid line, and the checks of step 7.

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

### Kilometrierung

```python
lines = plan_api.align_by_km(plan, [(u, v, "53,9"), (u, v, "54,2"), (u, v, "")], strecke="5102", unit='SOURCE')
```
Each point is the **km mark on the track**: the small circle (or tick) on the track line that the
km label belongs to, never the center of the label text. The text stands beside the track, often
10 to 20 m away, and a point there turns the whole sheet. Find the mark next to each label; if you
cannot find it, do not use that label. `""` = a point that only lies on the track.

### Neighbor sheet

```python
plan_api.attach_to_neighbor(plan, other, own=[(u1, v1), (u2, v2)], other=[(u1, v1), (u2, v2)], unit='IMAGE')
```
First point: where the track crosses the match line; second: another point on the match line.

## 5. Crop

```python
rect = plan_api.auto_crop(plan)          # removes empty paper and the sheet frame
```
Auto Crop keeps the title block and legends inside the frame. Check on the image whether the cropped
part still contains title block or legend, and trim with `plan_api.crop(plan, u0, v0, u1, v1, unit)`.
Keep all plan drawing, track stubs and "Anschluss Blatt" labels.

## 6. Height

Plans keep the height they were imported at. They hide terrain and objects below them in top views:
for renders that check other objects, hide the plans.

## 7. Check the placement (always)

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
Look at each image: "Red is plan A, blue is plan B (or the track axes), dark
purple where both lie, grid 10 m, north up. Do the red track lines lie on the blue ones? If not, by
how many meters and in which direction (right/left/up/down)?"

Also check: Georeference RMS ≤ 0.5 m and largest residual ≤ 1 m (more: a misread cross; fix it,
don't drop it silently). Neighbor sheets: their match line shows no step.

## Many plans at once

- First make a list of all sheets: orientation (turned?), grid yes/no (`find_grid`), Strecke and km
  range from the title block. Place the sheets with a grid first, then the others.
- Write down for each sheet how it was placed and what the checks gave (a JSON file next to the
  blend), so nothing is lost when your context runs out.
- Never move a sheet that is already placed by its grid. Place each sheet on its own; a batch that
  places several sheets at once must leave the others alone.
- Check each sheet before the next one, and check all sheets again after any change.

## 8. Report

For each plan: how it was placed, the numbers (Maßstab, residuals), the check results against each
plan and the tracks, and what stayed uncertain. Say plainly when a check could not decide.

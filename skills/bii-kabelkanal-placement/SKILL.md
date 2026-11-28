---
name: bii-kabelkanal-placement
description: Build the cable troughs (Kabelkanäle, Kabeltiefbau) of a railway project in Blender from KTB plan sheets (PDF) with the BII Import plugin - import the sheets, place them by their hectometer marks and onto the plans placed by grid, build one edge mesh of the new troughs, Gleisquerungen and Schächte with the Kabelkanäle Geometry Nodes asset, and check it against the plans. Use when the user asks to build, model, place or check Kabelkanäle, cable troughs, Kabeltiefbau or KTB plans in Blender.
---

# Build cable troughs from Kabeltiefbau plans (BII Import)

Work through Blender's MCP (`execute_blender_code`). Do every step in order. The troughs are only as
right as the sheets under them: place and check the sheets first.

## Before you start

- Functions: `from bl_ext.user_default.bii_blender_import import plan_api, plan_vector`. The
  extension's `README.md` (sections Cable troughs and Scripting) has the details. `result` in
  `execute_blender_code` must be a dict.
- The project needs plans placed by their grid (Lagepläne, Signallagepläne) and the BII Umgebung
  Kilometrierung of the Strecke. Without them, place the plans first with the skill
  `bii-plan-placement`.
- The Kabelkanäle modifier comes from `Kabelkanäle.blend` in the BII asset library (Preferences >
  File Paths > Asset Libraries). Without it the mesh is built without the modifier; tell the user.
- What is built: the new troughs, drawn in **red** (Neubau). Troughs in **black** (Mitnutzung,
  existing troughs that are used) and **orange** (Rückbau, removed) are **not** built unless the user
  asks for them.

## 1. Import the KTB sheets

Import the sheets as plans (File > Import > Plan Sheets, or `bpy.ops.import_image.bii_plan` per file)
with Maßstab 1:1000 (read it from the title block). Name them so they can be told apart
(`KTB_01` ...). They must be PDF files: everything below reads the PDF's drawing.

## 2. Place each sheet

KTB sheets have no coordinate grid. Place them by their hectometer marks, then onto the plans placed
by grid:

```python
plan = bpy.data.objects["KTB_05"]
marks = plan_api.km_marks(plan)              # [(u, v, "55,3")], PDF points
report = plan_api.align_by_km(plan, marks, strecke="5240", unit='PDF')
fit = plan_api.fit_to_plans(plan, apply=True)
result = {"marks": [m[2] for m in marks], "km": report[:2],
          "fit": {k: fit.get(k) for k in ("applied", "across_fixed", "along_fixed", "contrast", "move", "turn_deg")}}
```

- The km values must run in steps of 0,1 without gaps or jumps and belong to the Strecke of the
  title block. A sheet that shows two Strecken may give the other one: then pass
  `km_range=(from, to)` from the title block.
- Residuals of `align_by_km` up to about 1.5 m RMS are normal (OSM Kilometrierung). Larger: a wrong
  mark; look at the sheet (`plan_api.crop_image` around the marks).
- `fit_to_plans` not applied (`applied` False): the sheet keeps the km placement, which can be off
  by several meters along the track. Say so in the report.
- Then check each sheet as in the skill `bii-plan-placement`, step 7 (`check_placement`, overlays).

## 3. Build the troughs

```python
sheets = [o for o in plan_api.plans(bpy.context.scene) if o.name.startswith("KTB_")]
out = plan_api.build_kabelkanal(sheets, name="Kabelkanal Neubau", z=-1.0)
obj = out.pop("object")
result = out          # lines, length_m, schaechte, modifier, note
```

`z` is the height of the mesh (default 1 m below the origin, as in earlier projects; ask when the
project uses another). The mesh replaces an object of the same name. If `note` is not empty, tell
the user (no asset library with the node group).

## 4. Check

**Coverage of the red lines.** Every red line of a sheet's drawing should lie within about 1 m of a
trough. Red lines far from every trough are troughs that were missed, or leader lines, dimension
lines and symbols, so a coverage well below 1 is normal; the places in `far` tell which:

```python
import numpy as np
me = obj.data
A = np.array([me.vertices[e.vertices[0]].co.xy for e in me.edges])
B = np.array([me.vertices[e.vertices[1]].co.xy for e in me.edges])

def dist(p):
    d = B - A
    t = np.clip(((p[:, None] - A) * d).sum(2) / np.maximum((d ** 2).sum(1), 1e-12), 0, 1)
    return np.sqrt(((p[:, None] - (A + t[..., None] * d)) ** 2).sum(2)).min(1)

report = {}
for plan in sheets:
    path = plan_api.pdf_path(plan)
    right = plan_vector.drawing_right(path)
    step = plan_vector.point_per_m(plan.bii_plan.massstab)      # a point every meter
    pts = []
    for p, q, col in plan_vector.strokes(path, curves=True):
        if plan_vector.COLORS["red"](*col) and max(p[0], q[0]) < right:
            n = int(np.hypot(q[0] - p[0], q[1] - p[1]) / step)
            if n < 3:
                continue  # pieces shorter than 3 m: mostly red text
            pts += [plan_api.plan_to_world(plan, p[0] + (q[0] - p[0]) * (k + 0.5) / n,
                                           p[1] + (q[1] - p[1]) * (k + 0.5) / n, 'PDF').xy for k in range(n)]
    if pts:
        pts = np.array(pts)
        d = np.concatenate([dist(pts[i:i + 2000]) for i in range(0, len(pts), 2000)])
        far = pts[d > 1.0]
        report[plan.name] = {"red_m": len(pts), "covered": round(float((d <= 1.0).mean()), 2),
                             "far": [(round(float(x), 1), round(float(y), 1)) for x, y in far[::max(1, len(far) // 10)]]}
result = report
```

Look at the places in `far`: crop the sheet there and draw the troughs on it.

```python
from PIL import Image, ImageDraw
def look(plan, center, path, size=40.0):
    corners = [plan_api.world_to_plan(plan, (center[0] + dx, center[1] + dy, 0), 'IMAGE')
               for dx in (-size, size) for dy in (-size, size)]
    u0, v0 = min(c[0] for c in corners), min(c[1] for c in corners)
    u1, v1 = max(c[0] for c in corners), max(c[1] for c in corners)
    plan_api.crop_image(plan, u0, v0, u1, v1, path)
    im = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(im)
    x0, y0 = max(0, int(u0)), max(0, int(v0))
    for a, b in zip(A, B):
        pa = plan_api.world_to_plan(plan, (*a, 0), 'IMAGE')
        pb = plan_api.world_to_plan(plan, (*b, 0), 'IMAGE')
        draw.line([(pa[0] - x0, pa[1] - y0), (pb[0] - x0, pb[1] - y0)], fill=(0, 160, 255), width=2)
    im.save(path)
```

Ask for each image: "The blue lines are the built troughs. Is every red trough (two parallel red
lines, or a red dashed line) covered by a blue line? Are there blue lines where there is no red
trough?" Also look at a few places where sheets overlap: the trough must not be doubled or broken
there.

**Schächte.** Every small red outlined rectangle on a trough should be a vertex with a `Schacht`
size (at least 0.8 m a side). Filled red rectangles are other symbols, and the ends of a Gleisquerung
get no Schacht unless the plan draws one there. Count them per sheet against the plan.

Fix what is wrong by hand in the mesh (delete, draw, move vertices), or tell the user what is
missing. Do not change the sheets' placement for it.

## 5. Report

For each sheet: km marks used, residuals, whether `fit_to_plans` moved it, and the coverage. For
the mesh: length, number of Schächte, whether the modifier is set, the places that are uncertain
(with coordinates), and that Mitnutzung (black) and Rückbau (orange) were not built.

# BII Import

Blender extension that imports STEP files with their colors, names and assembly tree, with options for low-poly meshes and for leaving out small, hidden or chosen parts. It also imports plans (Lagepläne, Ansichten) from PDF or image files as reference planes at their true size, and can place Lagepläne by their grid crosses or by their Kilometrierung, and join sheets at their match lines. Its Signal panel places and configures railway signals by their Lageplan symbol.

## Install

Needs Blender 4.2 or newer.

1. Download the zip for your system from the [releases](https://github.com/BII-GmbH/blender-tools/releases) (`bii_blender_import-<version>-windows_x64.zip` and so on).
2. In Blender: Edit > Preferences > Get Extensions > drop-down menu at the top right > Install from Disk.

A zip of this folder does not work: the release zips contain OpenCascade, Pillow and PDFium, which are not in git. They are large (about 90 to 130 MB) because they hold them for two Python versions: 3.11 (Blender 4.x) and 3.13 (Blender 5.1 and newer). Blender installs the one that fits, and refuses the install on a Blender with another Python version.

## STEP import

File > Import > STEP (.step, .stp). You can also drag STEP files into the 3D viewport. Several files can be chosen or dropped at once, except with Choose Parts.

The status bar shows the progress of the import. Esc cancels it; Blender reacts to Esc between steps, not while it reads the STEP file or loads the converted model.

| Option | Meaning |
|---|---|
| Deflection | Max distance between mesh and CAD surface, as a fraction of the model size. Larger means fewer triangles. |
| Angle | Max angle between neighboring normals on curved surfaces. Does not depend on feature size, so embossed text keeps its shape. |
| Up | Up axis of the STEP model. Some SolidWorks exports are Y-up. |
| Remove Small Parts | Drops bodies smaller than the size (bounding box diagonal), such as screws. |
| Remove Hidden Parts | Drops bodies that cannot be seen from outside. Takes a while on large models. |
| Choose Parts | After you pick the file, shows the assembly tree. Uncheck instances to leave them out, for example helper geometry. The search field finds instances by name. |

Operator presets save option sets, for example one for "hull only".

The importer uses [OpenCascade](https://dev.opencascade.org/) (LGPL-2.1 with the Open CASCADE exception) through [OCP](https://github.com/CadQuery/OCP) (Apache-2.0), from the `cadquery-ocp-novtk` package.

### Command line

`step2glb.py` converts STEP to GLB without the Blender UI. Run it in a Blender that has the extension installed, which provides OCP:

```
blender -b --python step2glb.py -- model.step --list
blender -b --python step2glb.py -- model.step model.glb --exclude 0/0 0/1 --max-size 0.05 --hidden
```

`--list` prints the assembly tree with the instance paths that `--exclude` takes.

## Plan import

File > Import > Plan (.pdf, .tif, .png, .jpg). PDF files can also be dragged into the 3D viewport.

The plan becomes a plane at its true size: the sheet size comes from the file's resolution (dpi) or the PDF page size, and the object scale is the Maßstab (scale 1000 for 1:1000). Scanned PDFs are rendered at the resolution of the scan, with at most 16384 pixels on the long side (GPUs show no more). Black-and-white plans are stored as 1-bit PNG, about as small as the original.

| Option | Meaning |
|---|---|
| Maßstab 1: | Scale printed on the plan. Can be changed later in the sidebar. |
| Orientation | Flat for Lagepläne (on the ground), Upright for Ansichten and Schnitte. |
| Background | Opacity of the white paper, see below. 0: the lines lie over maps and models. 1 (default): the plan as printed. Can be changed later in the sidebar. Edit > Preferences > Add-ons > BII Blender Import > Plan Background sets the default. |
| Resolution | Long side of the plan image, 16384 px (the most GPUs show) down to 1024 px. 8192 px by default, which keeps most plans readable at a quarter of the memory. Can be changed later. |
| PDF Page | Page of a PDF with several pages. |
| PDF Resolution | 0 uses the resolution of the scan in the PDF, or 300 dpi for vector drawings. |
| Check GPU Memory | Imports the plan only if the GPU has memory left for it, see below. On by default for every import. |

The PNG goes into a `plans` folder next to the .blend file. If the .blend file is not saved yet, the PNG is packed into it.

### Files and Relink

The panel shows where the plan's original (PDF, TIFF, ...) and its image (the PNG) are; a red icon marks a missing file. Both paths can be edited there. The path to the original is stored relative to the .blend file (`//`) if the original lies in the .blend file's folder or below it, else absolute. Saving into another folder (Save As) updates it.

To pass a project on, send the .blend file with its `plans` folder, or pack the images first (File > External Data > Pack Resources). Without them, the plans show purple. Relink (in the panel, for all selected plans) then helps: choose a folder, and it looks there and in its subfolders for each plan's original by its file name, links the plan to it and writes the missing image again from it, at the plan's resolution. Crop, points and placement stay.

The sidebar (N) > BII Plan shows the settings of the selected plan. With several plans selected, it shows only what can change for all of them at once: Maßstab, Orientation, Opacity, Background, Full Sheet, Reset Crop and Change Resolution. Fields where the plans differ say "(mixed)" and show the active plan's value; a change sets it on all selected plans. Crop, Georeference, Neighbor Sheets, Kilometrierung and Align to Scene work on one plan and show when it is selected alone.

### Resolution and memory

Blender keeps every plan image in RAM at 4 bytes per pixel: a sheet at 16384 x 4140 px takes about 270 MB, more for large sheets. The panel shows each plan's pixel size and memory. The button next to it (Change Resolution) writes the images of the selected plans again at another long side (8192 px takes a quarter of the memory of 16384). Crop, grid crosses, km points and placement stay, since the sheet keeps its size in meters; copies of a plan get the new image too.

The viewport also keeps each plan image it shows on the GPU, with about a third more for mipmaps: about 1 GB for a sheet at 16384 x 11600 px. When the GPU memory runs out, Blender (5.x with Vulkan) crashes while drawing. So the imports first check how much GPU memory is left (NVIDIA GPUs report their free memory; for other GPUs the total memory minus the plans shown and a reserve for the scene; 4 GB assumed if neither can be read). A plan that does not fit is not imported, and the message says which resolution would fit. Import Plan Sheets imports the sheets that fit and stops before the first one that does not. Change Resolution checks the same before it raises the resolution. Turn off Check GPU Memory to import anyway. Hidden plans do not count: the viewport only loads the images it shows. Preferences > Viewport > Texture Limit Size lowers the GPU memory of all images, and the check takes it into account.

The image is made from the original file the plan was imported from (PDF, TIFF, ...), so the resolution can go up again later. If that file is gone, it uses the PNG kept at the import resolution (remembered when the resolution was first lowered), and else the current image, which can then only go down. The old image is removed from the file; its PNG on disk stays.

### Crop

The mesh outline is the crop: the UV map follows the vertex positions (Geometry Nodes modifier "BII Plan Sheet"), so moving or cutting edges in Edit Mode hides parts of the sheet without moving the image. Crop Rectangle crops by two clicks on the sheet. Auto Crop (the button next to it, also for several selected plans) crops to the drawing: it removes the empty paper and the sheet's frame lines, but keeps the title block and legends inside the frame; trim those with Crop Rectangle if needed. Full Sheet shows the whole sheet again, for example to read the Legende, and keeps the crop.

The mesh coordinates are sheet coordinates: image, crop and grid crosses depend on them. Do not apply the object's scale or rotation (the scale is the Maßstab), and do not use Set Origin. The panel warns when the outline lies outside the sheet; undo the step, or use Reset Crop.

Opacity fades the whole plan, Background sets how much of its white paper shows:

- Background 0 lays the plan on the scene like a print on tracing paper: white vanishes, black stays black, colors tint what lies below (multiply). Good for line plans over maps.
- Background 1 shows the plan as printed, opaque. Good for plans with aerial images.
- Values in between mix the two in Material Preview and Rendered view. Solid view cannot mix: it hides the paper below 0.5 and shows it from 0.5 on. Solid view also shows pixels this light in every channel (230 of 255) as paper. Copies of a plan (Shift+D) share its image, so in Solid view they show the paper alike; Opacity and Background in Material Preview and Rendered view stay separate.

The plan's colors stay exactly as in the file. In Material Preview and Rendered view, Blender's default View Transform AgX makes them paler and lowers the contrast; the panel offers to switch the scene to Standard (Render Properties > Color Management), which shows them as printed.

### Check Placement

Check Placement (for a flat plan) compares the plan's drawing with the flat plans it overlaps and with the BII Umgebung track axes, and reports how far it should move to match each of them best, for example "5102CR0P: off by 3.2 m (move -3.0, +1.1 m)". Use it after placing a plan in a project that already has plans or tracks.

- It is reliable when both show the same drawing, such as overlapping copies or neighbour sheets, or a plan's tracks against the track axes. Between different kinds of drawing (a scanned Lageplan against a Signallageplan) it often finds no clear match and says so: then compare by eye, with the plan's Opacity lowered.
- Along a straight track the match does not fix the position along it; the report then names the direction ("along 34° not fixed").
- The track axes come from OpenStreetMap: a few meters off is normal there. A plan placed by its coordinate grid is more accurate than they are.

### FBX export

File > Export > FBX exports plans with their image as texture. Use Path Mode Copy and Embed Textures (the button next to Path Mode) to put the image into the FBX file. The plan's material has a second shader for this, a Principled BSDF with the image as Base Color, on a Material Output that is not the active one; Blender keeps drawing the plan's own shader. Opacity and Background are not exported. The image keeps the alpha that marks the paper, so some programs show the paper transparent. Plans imported with an older version get the export shader when the file is opened.

### Georeference

For Lagepläne with grid crosses (Gitterkreuze) and their coordinates on the sheet frame:

1. Choose the coordinate system, see the table below. The Gauß-Krüger zone comes from the first digit of the Rechtswert (3588400: zone 3). Under the list, the panel shows the zone and warns about values that do not fit the system (8 digits for Gauß-Krüger, 7 for UTM, swapped values, different zones).
2. Click + and then a grid cross in the viewport. Shift-click + to add several crosses, until Esc or right click. While points are added, the viewport has an orange frame and a banner saying what to click; clicks on the sidebar, toolbar or header go to their buttons, not to the plan. Snap to Crosses (off by default) moves each point to the center of the nearest dark mark: good for crosses that stand alone, but lines or text near a cross pull the point off.
3. Enter Rechtswert and Hochwert of the first two points in the list. From the third point on, the coordinates are predicted from the others and rounded to the grid, and marked as guesses (?). Check them against the labels on the frame, then click ? to confirm them, or type the right value. Grid Spacing is 100 m by default. Set it to the grid of the plan, or to 0 to take it from the values read: then the prediction goes to the coarsest grid line it lies near (crosses read 1000 m apart may lie on a 100 m grid).

   If the scene has an origin (`scene["lat"]` / `scene["lon"]`, for example from Umgebung), the first points get estimated values instead: where they lie in the scene now. So you can first move, turn and scale the plan roughly into place by hand, then click the crosses and round their values to the grid. This also helps when the plan labels only one axis (only Hochwert, say): correct the labeled value and round the other one. The panel shows "Lies now at" for the active point, and From Scene Position takes it over again after you moved the plan. Estimated values are guesses (? next to the value, "≈" in the list). Guesses only decide what the values read from the plan leave open: with Rechtswerte read from at least three crosses not in one line and Hochwerte guessed, the Rechtswerte set Maßstab, rotation and the east-west position, and the guesses only the north-south position. So wrong guesses do not bend the grid. With fewer values, or crosses in one line, the guesses also turn the plan; the panel warns then. Typing a value marks it as read from the plan, and so does clicking ? on a guess; clicking ? again marks it as a guess. A value left empty (0) does not count. With Grid Spacing set, the estimates are rounded to it.

   The panel warns when the values read from the plan cannot be right: when Rechtswert and Hochwert give different Maßstäbe (a value off by grid steps, the warning names both Maßstäbe, the axis matching the plan is right), when the values are mirrored (swapped, or counting the wrong way), or when the grid they give is skewed. All points with the same residual is the typical sign of such a mistake.
4. Show Grid draws the grid that follows from the points. If it runs through all crosses and matches the labels on the frame, the points are right. Residuals over 0.5 m show in red.
5. Place in Scene moves and turns the plan to the scene coordinates of the Umgebung extension (`scene["lat"]` / `scene["lon"]`). Without them, the plan sets them to its center, and Umgebung can build around it ("use scene origin"). If the scene has Umgebung objects but no stored origin (Umgebung before 1.29.1 stored it only when loading tracks), the panel offers Origin from Umgebung Panel: it takes the center of the area in the Umgebung panel, which is right only if that area has not changed since Umgebung built the scene.

#### Find Grid

Find Grid (in the Georeference panel, or the Find Grid option of the import) looks for the grid crosses on the original file at full resolution: crosses at any angle, also those touching other lines. It adds every cross it finds as a point named "G i,j" (i, j: its place in the grid) without values and sets Grid Spacing. Enter Rechtswert and Hochwert of two or three of them (not all in one line), then Fill from Grid: it gives every other cross its values from the grid, and refuses values that do not fit (a typo) or that leave the direction open. Then Place in Scene. A plan whose grid cannot be found keeps the + way above.

For scripts: `plan_api.find_grid(plan)`, then `plan_api.grid_labels(plan, folder)` writes images of the places along the grid lines where coordinate numbers are printed (each shown as printed and turned by 180°), and `plan_api.place_from_grid(plan, [(box id, number), ...])` places the plan from the numbers read there. Each number must fit the grid; misreads are left out and reported, and the call refuses when the numbers allow two placements (turned by 180°) or would mirror the plan.

#### Which coordinate system?

Look on the plan for "Lagesystem", "Lagebezug" or "Koordinatensystem". The sidebar has the same help under Georeference > Which Coordinate System?

| On the plan | Choose | Values |
|---|---|---|
| DB_REF, DB_REF2003, DB_REF2016, DB-Referenzsystem | DB_REF | Rechtswert with 7 digits, zone in front: 3588400 |
| DHDN, DHDN 90, Gauß-Krüger, GK, Bessel, Potsdam datum | DHDN | Rechtswert with 7 digits: 4590250 |
| ETRS89, UTM, EPSG:25832 / 25833 | ETRS89 / UTM | Easting with 8 digits (32588400) or 6 digits (588400, set the zone) |

DHHN92, DHHN2016, NHN and NN are height systems (Höhensysteme), never Lagesysteme. The height system does not matter here, because the plan is placed on the ground. A plan that says "Höhensystem DB_REF, Koordinatensystem DHHN92" has its labels mixed up: DB_REF is the Lagesystem, so choose DB_REF.

DB_REF and DHDN give nearly the same numbers. Mixing them up moves the plan by 0.2 to 1 m. When unsure, take DB_REF for DB plans.

Count the digits on the plan and do not add any. A 6-digit label like 582450 is a UTM easting (ETRS89 / UTM, usually zone 32), not a Gauß-Krüger Rechtswert without its zone digit. Read as Gauß-Krüger 3582450, the plan lands about 100 to 150 m off east-west and about 2 km off north-south; with only Rechtswerte read and Hochwerte guessed, the north-south part hides in the guesses. Changing the coordinate system takes all guessed values again in the new system; Update Guesses does the same after you moved the plan by hand.

The coordinates go through WGS84 with the 7-parameter transformations PROJ uses (DHDN about 1 m, DB_REF a few decimeters). Umgebung's map projection stretches north-south by about 0.3 % against east-west, which a plane with uniform scale cannot follow: at the edges of a 1 km sheet, the plan can be up to about 1 m off the Umgebung maps. The report after Place in Scene says how much.

### Align to Scene

For plans without coordinates, such as Ansichten: put the 3D cursor on a scene point (Shift + right click, with snapping), click + and then the matching point on the sheet. Repeat for a second point, then Align. Shift-click + to add several points in a row, moving the 3D cursor with Shift + right click in between. The plan turns about the vertical axis only and keeps its Maßstab, unless Fit Scale is on: then two or more points measure the Maßstab (on a flat plan from their horizontal distances only, so the terrain height at the 3D cursor does not count). One point only moves the plan.

### Place by Kilometrierung

For DB Lagepläne without grid crosses: they show the Kilometrierung of the Strecke (hectometer labels like 5,3 / 5,4 / 5,5 along the track) and landmarks with their km, such as "EÜ (Weg) 5240/5,504/1616" for a bridge on Strecke 5240 at km 5,504. The scene needs the Kilometrierung line of that Strecke from BII Umgebung (a curve "KM-Linie 5240 ..." with its tick marks). Sidebar > BII Plan > Kilometrierung:

1. Enter the Strecke (5240). Empty works when the scene has only one Strecke. The panel shows the km range of its Kilometrierung lines.
2. Move the plan roughly into place by hand (optional: two km points far apart fix it from anywhere).
3. Click + and then a point on the plan track: a hectometer mark, a landmark. Shift-click + to add several, until Esc. Type the km printed there, as on the plan: `5,3`, `5,504`, `13,4+23,05`, or the whole landmark label (`5240/5,504/1616`, which also sets the Strecke). New points get a predicted km (≈, a guess) once two km are typed or the plan is placed; click ? to confirm it, or type the right one. A point without km (or with a guessed km) only lies on the track: it pulls the plan track onto the scene track, across the track.
4. Align by Kilometrierung turns the plan about the vertical axis and moves it. The Maßstab stays, unless Fit Scale is on: then the fit measures the Maßstab from the km points and landmarks and scales the plan, as Place in Scene does with grid crosses. That needs km points or landmarks some distance apart, else the plan keeps its Maßstab and the report says so. Fit Scale is the same switch as in Align to Scene. Each km point lies at its km along the Strecke and on the track; Track sets which scene lines count as the track: the Kilometrierung line, or the track axes (Gleisachsen) BII Umgebung built for the Strecke (Auto takes the track axes when there are any).

Landmarks you can find in the scene, such as a bridge edge on the map or a model, are more accurate than the OSM Kilometrierung (which is an approximation: a few meters off along the track, in places 20 m or more; on plans with a coordinate grid, use Georeference instead): put the 3D cursor there and add an Align to Scene point (see below) on the plan. Align by Kilometrierung uses those points with Landmark Weight (10 against 1 for a km point; 0 leaves them out).

The panel shows the residual of each point and warns:

- when two km points lie farther apart on the plan than on the Strecke, or closer (a wrong km, or a wrong Maßstab; the warning names both points),
- when the residuals are large (over 2 m RMS) or one point is far off,
- when the points do not fix the rotation (all close together) or the position along the track (only points without km): the plan keeps its current rotation or position there,
- when a km is not on the Kilometrierung lines of the scene.

Two km points far apart fix the plan. One km point and a few track points far from it also do; there the track points fix the rotation and the km point the position along the track.

### Scripting

For scripts and AI agents, `plan_api` has the plan functions in one place:

```python
from bl_ext.user_default.bii_blender_import import plan_api

plan_api.plans(bpy.context.scene)                   # the plan objects
plan_api.plan_to_world(plan, 1820, 450, 'PDF')      # world Vector of a point on the plan
plan_api.world_to_plan(plan, (x, y, z), 'IMAGE')    # and back
plan_api.georeference(plan, [(u, v, easting, northing), ...], 'IMAGE', crs='DB_REF')  # grid crosses
plan_api.find_grid(plan)                            # grid crosses found on the original file
plan_api.place_from_grid(plan, [(box_id, 3589300), ...])  # numbers read on grid_labels() images
plan_api.validate(plan)                             # ok, problems, notes: all checks in one call
plan_api.vector_symbols(plan)                       # PDF: feet of red symbols on a mast and Ne 3 boards, exact
plan_api.groups_without_signal(plan)               # red symbol groups with no placed signal at them
plan_api.align_to_scene(plan, [(u, v, (x, y, z)), ...], 'PDF')  # points onto scene points
plan_api.attach_to_neighbor(plan, other, [(u1, v1), (u2, v2)], [(u1, v1), (u2, v2)], 'IMAGE')
plan_api.align_by_km(plan, [(u, v, "5,3"), (u, v, "5,4")], strecke="5240")
plan_api.fit_to_plans(plan, apply=False)            # move/turn onto the grid-placed plans under it
plan_api.crop(plan, 63.5, 318.5, 2187, 603.5, 'PDF')
plan_api.auto_crop(plan)
for r in plan_api.check_placement(plan):            # dicts: shift, distance, score, reliable, along_deg
    print(plan_api.describe(r))
plan_api.overlay(plan, other, center_xy, "check.png")  # red: plan, blue: other plan or "TRACKS"
```

A plan placed by its grid is not moved by `align_to_scene`, `attach_to_neighbor` or `align_by_km` unless they get `force=True`. `plan_api.placement(plan)` says how a plan was placed last (method, numbers, residuals). `validate()` checks the Maßstab and residuals, that every coordinate number used lies on its own grid line, the Kilometrierung residuals, the overlaps with the other plans (a sheet that overlaps no plan while its neighbors lie close is suspicious), the drawings against plans and track axes, and that the plan lies above the terrain.

`fit_to_plans(plan)` moves and turns a flat plan (never scales it) onto the plans placed by their grid that lie under it, for a plan without a grid that was placed roughly, e.g. by Kilometrierung. It cuts the plan into pieces of about 80 m and matches each piece's dark, uncolored lines (not the red or blue symbols) on the plans below, near the BII Umgebung track axes if there are any, for every shift up to 30 m. Then all pieces vote together for every turn up to 1.5°: the move and turn with the highest total wins, so a single piece that matches the next track does not pull the plan away, and the pieces at both ends fix the turn. A last step fits a line through the pieces' offsets for a finer turn. The plan is moved only when the best result is clearly better than the next one more than 2 m across the track, most pieces agree with it within 1 m, and the same result comes out when the search starts 6 m and 0.4° away. Along straight track the plan keeps its place (e.g. from the Kilometrierung); along the track it moves only where the drawing fixes that direction too. `apply=False` only reports: `across_fixed`, `along_fixed`, the move and turn, `residuals` (each piece's remaining offset across the track, from one end of the sheet to the other), `contrast` and the spread of the starts.

Import with Turn (0°, 90° clockwise, 180°, 90° counterclockwise) for scans lying on their side or upside down; `'SOURCE'` pixels stay those of the original file.

Units of a point on a plan: `'SHEET'` (meters on the paper, y up, the mesh's own coordinates), `'PDF'` (points of the PDF page, y down), `'IMAGE'` (pixels of the plan's image in Blender, y down), `'SOURCE'` (pixels of the original TIFF or PNG, y down). `overlay()` writes a PNG with the plan's ink red, the other plan's or the track axes' ink blue and both together dark purple, with a 10 m grid: where the plan sits right, red lies on blue.

### Neighbor sheets (Anschlussblatt)

Long Strecken come as several sheets that join at dashed match lines ("Anschlussblatt 2"). File > Import > Plan Sheets imports several files at once with the same Maßstab, Background and Resolution, side by side in name order (Blatt 2 before Blatt 10), centered on the 3D cursor. It imports the sheets that fit into the GPU memory left and names the others (see Resolution and memory). The sidebar has the same under Plan > Neighbor Sheets > Import Plan Sheets, right below Georeference.

1. Place one sheet, by grid crosses, by Kilometrierung or by hand.
2. Select the next sheet and click Attach to Neighbor. Click where the track crosses the match line on this sheet, then a second point on the match line; then the same crossing on the neighbor sheet, and a second point on its match line. The sheet snaps to its neighbor: the crossings meet, the match lines lie on each other, and the sheets lie on opposite sides of the line (so the order of the second clicks does not matter). With Two Shared Points, both clicks on each sheet are the same two places instead, for example two Weichen both sheets show.
3. The attachment is kept. Placing a sheet places the sheets attached to it, and the ones attached to those (Place Attached Sheets does it by hand; Snap to Neighbor snaps this sheet again).
4. Refine each sheet with its own km points (Align by Kilometrierung). The match line to its neighbor takes part in the fit with Neighbor Weight, so the sheets stay joined. A sheet placed by its own km points stays when its neighbor moves; align it again, or use Place Attached Sheets with Also Sheets Placed by km.

All sheets of a chain should have the same Maßstab; Attach warns otherwise.

### Cable troughs (Kabelkanäle)

Kabeltiefbau plans (KTB) draw the new cable troughs in red: a trough as two parallel lines about 0.5 mm apart, a Gleisquerung (crossing under the tracks) as a dashed line, a Schacht as a small rectangle. Troughs in black (Mitnutzung, existing ones that are used) and orange (Rückbau, removed) are not built. The functions read the PDF's drawing, not the image:

```python
from bl_ext.user_default.bii_blender_import import plan_api

plan_api.align_by_km(plan, plan_api.km_marks(plan), strecke="5240", unit='PDF')  # place by the hectometer marks
plan_api.fit_to_plans(plan)                         # then onto the plans placed by their grid
plan_api.kabel_lines(plan)                          # centre lines of troughs and Gleisquerungen, world
plan_api.schacht_marks(plan)                        # Schächte: (world point, (short, long) in m or None)
out = plan_api.build_kabelkanal(sheets)             # one mesh "Kabelkanal Neubau" from all placed sheets
```

- `km_marks(plan)` reads the hectometer labels ("55,3") and the small circle on the track that belongs to each, in PDF points. A sheet that shows several Strecken uses the longest run of labels; `km_range=(55.0, 58.0)` picks one.
- `build_kabelkanal(plans, name, z=-1.0, merge=0.8)` merges the lines of overlapping sheets (parts within `merge` m of a line already there are dropped, line ends within 1.5 m of another line are joined to it) and builds an edge mesh at height `z`. Every Schacht is a vertex on its trough with the point attributes `Schacht` (size x, y and a height of 2 m; 1 x 1 m at the ends of a Gleisquerung, where the plan gives no size) and `Schacht Rotation` (along the trough). The mesh gets the Geometry Nodes group `Kabelkanäle` from `Kabelkanäle.blend` of the first asset library that has it, with Generate Schotter on and Schacht Size and Schacht Rotation set to the attributes. Without such a library the mesh has no modifier and `out["note"]` says so. An object of the same name is replaced.
- `plan_vector.trough_lines(path)`, `km_marks(path)` and `schacht_marks(path)` do the same on a PDF file without Blender, in PDF points.

Check the result: red lines of labels and symbols can turn up as short troughs, and a trough drawn with another gap is missed. The skill `bii-kabelkanal-placement` describes the checks.

The plan import uses [Pillow](https://python-pillow.org/) (MIT-CMU) for images and [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (Apache-2.0 / BSD-3-Clause, PDFium BSD-3-Clause) for PDF files.

## Signals

Sidebar (N) > BII Signal places railway signals from a Lageplan and sets them up by their symbol: Ks and H/V Haupt-, Vor- and Mehrabschnittssignale, Zusatzsignale (Zs1, Zs2/Zs2v, Zs3, Zs3v, Zs6), Lf 6 and Lf 7, Ne 2, Ne 3 and Ne 4, and Lichtsperrsignale. It uses the composite signals (Signal-Baukasten) of `DB_Signale.blend` in the BII asset library: add that library under Preferences > File Paths > Asset Libraries. The signals are built from the object library of DB InfraGO AG and may be used only for projects of DB InfraGO AG.

A signal symbol in the Lageplan is a head (Bauform) with marks around it. At the top, the panel shows the symbol of the whole combination, to compare with the plan. Below it, one row per choice shows every option as a small symbol button (names in the tooltips). The button at the end of a row shows the chosen option; a click on it shows all options as large symbols to choose from:

| Row | Lageplan symbol |
|---|---|
| Bauform | The first rows (one per group: Ks, H/V, Zusatzsignale, Langsamfahrsignale, Nebensignale, Rangiersignale). Head: Ks screen (Hauptsignal rounded on top, Vorsignal rounded below, Mehrabschnittssignal rounded on both ends), H/V circles and distant-signal lights |
| Bedienung | Head filled (stellwerksbedient), right half filled (stellwerks- oder zugbedient) or empty (zugbedient); block signals get the white-yellow Mastschild |
| Hp | H/V: one or two circles, a bar beside them for Hp0/Hp2 |
| Kopf | `^` Zs1 (Ersatzsignal), `V` Zs7 (Vorsichtsignal) or nothing on top |
| Zs3 | Triangle above the head: filled = light indicator, empty = sign |
| Zs3v | Triangle below the head: filled = light indicator, empty = sign |
| Zs6 | Square with a diagonal, half filled, below the head |
| Zusatzlicht | Small circle at the head: Kennlicht or shortened braking distance (top left), Vorsignalwiederholer (bottom left, Ks distant signals) |
| Sh1 | Two bars across the head, which is longer by them (Hauptsperrsignal): at the lower end of a Hauptsignal, in the middle of a Mehrabschnittssignal. On a half filled or empty head they are two empty fields below the fill |
| Mast | Without mast for a Signalbrücke or Ausleger, or to put a Zusatzsignal on another signal's mast |
| Art, Ausführung | Zs2 or Zs2v; Zs3/Zs3v as light indicator or sign |
| Kennziffer (number field) | The number of Lf 6, Lf 7 and the Zs3/Zs3v signs (speed = number x 10 km/h; 0 leaves the sign empty) |
| Streifen | Ne 3 Vorsignalbake with 1 to 5 stripes |
| Bauart | Lichtsperrsignal high on a mast, low, or low with Rangierkennlicht |

Options that do not fit the rest are greyed out. Kennziffer is a number field. Signalbild lights the lamps of one aspect; it offers only the aspects whose lights the combination has.

- With a placed signal active, the panel changes that signal; another Bauform replaces it by a signal of that Bauform in the same place (transform, collections, parent, the options both have, and its name unless renamed). Without one, it sets up a new signal.
- Where to put the 3D cursor: at the foot of the symbol, the end of its stem (the bar across it), not its middle. A symbol without a stem sits there with its lower end.
- Place Signal puts the signal as shown at the 3D cursor, with the cursor's rotation, or, with Align to Track, turned along the nearest Umgebung track axis. A signal faces the way its symbol is drawn on the plan: a symbol drawn with its top to the west and its foot to the east looks upright when seen from the east, so the signal faces east. Check that and use Turn 180° where it faces the other way. On Terrain (on by default) puts it on the BII Umgebung terrain under the cursor, so a cursor snapped to the plan sheet above the ground still gives a signal on the ground; without terrain there, it stays at the cursor's height. The new signal is then active, so the panel changes it; Place Another places one more like it. Turn 180° turns the selected signals to face the other direction. Use for Placing keeps the active signal's setup for when no signal is selected.
- The signal is imported like the Asset Browser does it (Pack, Link or Append, from the asset library's setting). Library: left empty, it takes the first asset library whose `DB_Signale.blend` has the signal, so an older copy without the Signal-Baukasten (for example a not yet refreshed remote library) is skipped; the panel shows which library it uses.

Save Symbol Key writes all symbols as one PNG: every Bauform and each of its options, with the option in words, its value for scripts (`zs3="tafel"`) and a red dot where the signal's foot belongs. Print it as a key, or give it to an AI agent to compare with a plan.

The settings are inputs of the signal's Geometry Nodes modifier "Signal", so they also work without the panel and can be changed in the modifier. `signal_spec.py` describes the Bauformen, elements and combinations; `signal_symbols.py` draws the symbols. Both have no bpy, so they also build the asset library.

### Scripting

For scripts and AI agents (for example through the Blender MCP), `signal_panel` has functions that check their input and say what is allowed when it is not:

```python
from bl_ext.user_default.bii_blender_import import signal_panel as sp, signal_legend

obj = sp.place_signal("ks_mehr", {"zs3": "licht", "zs3v": "licht", "kopf": "ohne"},
                      location=(x, y, 0.0), aspect="Ks1", align_to_track=True, on_terrain=True)
sp.read_signal(obj)                          # ("ks_mehr", {...full configuration...}, "Ks1")
obj = sp.configure_signal(obj, {"zs3": "tafel"})   # changes only what is given
obj = sp.configure_signal(obj, bauform="ks_haupt") # a new object in the same place
sp.terrain_height(bpy.context, x, y)         # z of the BII Umgebung terrain, or None
signal_legend.write("key.png")               # the symbol key, also without Blender
```

- `place_signal(bauform, config, location, rotation=None, aspect="Aus", *, align_to_track, on_terrain, library, collection, name)`: `location` is the foot of the mast. `head` is the top end of the symbol on the plan (world x, y); the signal then faces the way its symbol is drawn, from its head towards its foot. `rotation` in radians about Z; None turns it by `head`, and with `align_to_track` along the nearest track in the direction closer to the symbol's (without `head`: against the direction the track line is drawn in). The name is "<Name> <Tag>" from the configuration.
- Configurations: `bauform` is a key of `signal_spec.BY_KEY`; `config` holds element keys and values as in the symbol key (missing ones keep their defaults). An unknown key or value, options that do not fit together, or an aspect the signal cannot show raise `ValueError` with the allowed values; nothing is changed silently. No asset library with the signal raises `RuntimeError`.
- Do not read the modifier with `mod[...]`: Blender 5.2 and later keep the inputs in `mod.properties`. `read_signal` handles both.

## Tests

The plan import and the Signal panel have tests in [tests/bii_blender_import](../../tests/bii_blender_import). They run in Blender's Python with Pillow and pypdfium2 from `wheels/` (download them first with the build script, see below):

```
blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_import/run_in_blender.py
```

`python -m unittest discover tests/bii_blender_import` runs the coordinate tests without Blender and skips the others. The signal placing tests need the asset library: set `BII_SIGNAL_LIBRARY` to the folder with `DB_Signale.blend`, else they are skipped.

## Release

1. Raise `version` in [blender_manifest.toml](blender_manifest.toml) and push to `main`.
2. On GitHub: Actions > Release BII Import > Run workflow, on `main`.

The workflow builds one zip per platform and publishes them as the release `bii_blender_import-v<version>`. To build locally instead (Python 3.11+ and Blender 4.2+):

```
python scripts/build_extension.py plugins/bii_blender_import --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
```

This downloads the wheels (OCP, Pillow, pypdfium2) into `wheels/` and writes the zips to `dist/`. Both folders are ignored by git.

# BII Import

Blender extension that imports STEP files with their colors, names and assembly tree, with options for low-poly meshes and for leaving out small, hidden or chosen parts. It also imports plans (Lagepläne, Ansichten) from PDF or image files as reference planes at their true size, and can place Lagepläne by their grid crosses.

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
| Background | Opacity of the white paper, see below. 0 (default): the lines lie over maps and models. 1: the plan as printed. Can be changed later in the sidebar. |
| PDF Page | Page of a PDF with several pages. |
| PDF Resolution | 0 uses the resolution of the scan in the PDF, or 300 dpi for vector drawings. |

The PNG goes into a `plans` folder next to the .blend file. If the .blend file is not saved yet, the PNG is packed into it.

The sidebar (N) > BII Import > Plan shows the settings of the selected plan.

### Crop

The mesh outline is the crop: the UV map follows the vertex positions (Geometry Nodes modifier "BII Plan Sheet"), so moving or cutting edges in Edit Mode hides parts of the sheet without moving the image. Crop Rectangle crops by two clicks on the sheet. Full Sheet shows the whole sheet again, for example to read the Legende, and keeps the crop.

The mesh coordinates are sheet coordinates: image, crop and grid crosses depend on them. Do not apply the object's scale or rotation (the scale is the Maßstab), and do not use Set Origin. The panel warns when the outline lies outside the sheet; undo the step, or use Reset Crop.

Opacity fades the whole plan, Background sets how much of its white paper shows:

- Background 0 lays the plan on the scene like a print on tracing paper: white vanishes, black stays black, colors tint what lies below (multiply). Good for line plans over maps.
- Background 1 shows the plan as printed, opaque. Good for plans with aerial images.
- Values in between mix the two in Material Preview and Rendered view. Solid view cannot mix: it hides the paper below 0.5 and shows it from 0.5 on. Solid view also shows pixels this light in every channel (230 of 255) as paper. Copies of a plan (Shift+D) share its image, so in Solid view they show the paper alike; Opacity and Background in Material Preview and Rendered view stay separate.

The plan's colors stay exactly as in the file. In Material Preview and Rendered view, Blender's default View Transform AgX makes them paler and lowers the contrast; the panel offers to switch the scene to Standard (Render Properties > Color Management), which shows them as printed.

### Georeference

For Lagepläne with grid crosses (Gitterkreuze) and their coordinates on the sheet frame:

1. Choose the coordinate system, see the table below. The Gauß-Krüger zone comes from the first digit of the Rechtswert (3588400: zone 3). Under the list, the panel shows the zone and warns about values that do not fit the system (8 digits for Gauß-Krüger, 7 for UTM, swapped values, different zones).
2. Click + and then grid crosses in the viewport. Snap to Crosses moves each point to the center of the nearest cross. Esc ends.
3. Enter Rechtswert and Hochwert of the first two points in the list. From the third point on, the coordinates are predicted from the others and rounded to the grid spacing.

   If the scene has an origin (`scene["lat"]` / `scene["lon"]`, for example from Umgebung), the first points get estimated values instead: where they lie in the scene now. So you can first move, turn and scale the plan roughly into place by hand, then click the crosses and round their values to the grid. This also helps when the plan labels only one axis (only Hochwert, say): correct the labeled value and round the other one. The panel shows "Lies now at" for the active point, and From Scene Position takes it over again after you moved the plan. Estimated values are guesses (? next to the value, "≈" in the list). Guesses only decide what the values read from the plan leave open: with Rechtswerte read and Hochwerte guessed, the Rechtswerte set Maßstab, rotation and the east-west position, and the guesses only the north-south position. So wrong guesses do not bend the grid. Typing a value marks it as read from the plan; the ? button marks it as a guess again. With Grid Spacing set, the estimates are rounded to it.

   The panel warns when the values read from the plan cannot be right: when Rechtswert and Hochwert give different Maßstäbe (a value off by grid steps, the warning names both Maßstäbe, the axis matching the plan is right), when the values are mirrored (swapped, or counting the wrong way), or when the grid they give is skewed. All points with the same residual is the typical sign of such a mistake.
4. Show Grid draws the grid that follows from the points. If it runs through all crosses and matches the labels on the frame, the points are right. Residuals over 0.5 m show in red.
5. Place in Scene moves and turns the plan to the scene coordinates of the Umgebung extension (`scene["lat"]` / `scene["lon"]`). Without them, the plan sets them to its center, and Umgebung can build around it ("use scene origin").

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

For plans without coordinates, such as Ansichten: put the 3D cursor on a scene point (Shift + right click, with snapping), click + and then the matching point on the sheet. Repeat for a second point, then Align. The plan turns about the vertical axis only and keeps its Maßstab, unless Fit Scale is on. One point only moves the plan.

The plan import uses [Pillow](https://python-pillow.org/) (MIT-CMU) for images and [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (Apache-2.0 / BSD-3-Clause, PDFium BSD-3-Clause) for PDF files.

## Tests

The plan import has tests in [tests/bii_blender_import](../../tests/bii_blender_import). They run in Blender's Python with Pillow and pypdfium2 from `wheels/` (download them first with the build script, see below):

```
blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_import/run_in_blender.py
```

`python -m unittest discover tests/bii_blender_import` runs the coordinate tests without Blender and skips the others.

## Release

1. Raise `version` in [blender_manifest.toml](blender_manifest.toml) and push to `main`.
2. On GitHub: Actions > Release BII Import > Run workflow, on `main`.

The workflow builds one zip per platform and publishes them as the release `bii_blender_import-v<version>`. To build locally instead (Python 3.11+ and Blender 4.2+):

```
python scripts/build_extension.py plugins/bii_blender_import --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
```

This downloads the wheels (OCP, Pillow, pypdfium2) into `wheels/` and writes the zips to `dist/`. Both folders are ignored by git.

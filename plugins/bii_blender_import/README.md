# BII Blender Import

Blender extension that imports STEP files with their colors, names and assembly tree, with options for low-poly meshes and for leaving out small, hidden or chosen parts.

## Install

Needs Blender 4.2 or newer.

1. Download the zip for your system from the [releases](https://github.com/BII-GmbH/blender-tools/releases) (`bii_blender_import-<version>-windows_x64.zip` and so on).
2. In Blender: Edit > Preferences > Get Extensions > drop-down menu at the top right > Install from Disk.

A zip of this folder does not work: the release zips contain OpenCascade, which is not in git. They are large (about 90 to 130 MB) because they hold it for two Python versions: 3.11 (Blender 4.x) and 3.13 (Blender 5.1 and newer). Blender installs the one that fits, and refuses the install on a Blender with another Python version.

## STEP import

File > Import > STEP (.step, .stp). You can also drag a STEP file into the 3D viewport.

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

## Release

1. Raise `version` in [blender_manifest.toml](blender_manifest.toml) and push.
2. On GitHub: Actions > Release BII Blender Import > Run workflow.

The workflow builds one zip per platform and publishes them as the release `bii_blender_import-v<version>`. To build locally instead (Python 3.11+ and Blender 4.2+):

```
python scripts/build_extension.py plugins/bii_blender_import --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
```

This downloads the OCP wheels into `wheels/` and writes the zips to `dist/`. Both folders are ignored by git.

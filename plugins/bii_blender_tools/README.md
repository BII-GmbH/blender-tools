# BII Tools

Blender extension with IFC cleanup tools, the dProB rail asset export and [equipment rigs](equipment/README.md): joints, couplings, a movement and IK preview and the export as dProB scriptable equipment (`.dequip`) with generated IK capabilities, also for scripts and AI agents (`equipment/rig_api.py`). The STEP importer is a separate extension: [BII Import](../bii_blender_import/README.md).

## Install

Needs Blender 4.2 or newer.

1. Zip the `bii_blender_tools` folder, or download a zip from the [releases](https://github.com/BII-GmbH/blender-tools/releases).
2. In Blender: Edit > Preferences > Get Extensions > drop-down menu at the top right > Install from Disk.

If the old "BII Blender Tools" add-on is installed (under Add-ons, before version 0.1.0), remove it first. Both register the same tools.

## Tests

```
blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_tools/run_in_blender.py
```

## Release

1. Raise `version` in [blender_manifest.toml](blender_manifest.toml) and push to `main`.
2. On GitHub: Actions > Release BII Tools > Run workflow, on `main`.

This publishes the release `bii_blender_tools-v<version>`.

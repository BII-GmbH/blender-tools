# BII Tools

Blender extension with IFC cleanup tools and the dProB rail asset export. The STEP importer is a separate extension: [BII Import](../bii_blender_import/README.md).

## Install

Needs Blender 4.2 or newer.

1. Zip the `bii_blender_tools` folder, or download a zip from the [releases](https://github.com/BII-GmbH/blender-tools/releases).
2. In Blender: Edit > Preferences > Get Extensions > drop-down menu at the top right > Install from Disk.

If the old "BII Blender Tools" add-on is installed (under Add-ons, before version 0.1.0), remove it first. Both register the same tools.

## Release

1. Raise `version` in [blender_manifest.toml](blender_manifest.toml) and push to `main`.
2. On GitHub: Actions > Release BII Tools > Run workflow, on `main`.

This publishes the release `bii_blender_tools-v<version>`.

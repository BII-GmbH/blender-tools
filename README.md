# Blender Tools for BII Gmbh

This repo contains plugins, pipelines and equipment utilizing the popular [Blender](https://www.blender.org/) 3D modelling software.

For more information on the different tools, look for the respective README.md files.

## Plugins

Plugins are directly installed into Blender and add their functionality to the software. 
They can be downloaded in the release section of the repo or by zipping them.
BII Import is the exception: it bundles OpenCascade, so it is built with `scripts/build_extension.py` or its release workflow, see [its README](plugins/bii_blender_import/README.md).

Each plugin has its own releases, tagged `<plugin>-v<version>` (for example `bii_blender_tools-v0.1.0`, `bii_blender_umgebung-v1.29.0`).
To release a plugin, raise `version` in its `blender_manifest.toml`, push to `main`, and run its workflow on `main` under Actions (Release BII Tools, Release BII Import, Release BII Umgebung). The workflows refuse other branches.
The release notes list the commits that changed that plugin's folder since its previous release.

The release [all-plugins](https://github.com/BII-GmbH/blender-tools/releases/latest) always holds the latest version of every plugin: each release workflow replaces its plugin's zips there.
It is the release marked Latest, so `/releases/latest` leads to it. Pre-releases do not go into it.

## Skills for Claude Code

`skills/` holds instructions for Claude Code agents that work in Blender through the Blender MCP:

- `bii-plan-placement`: import, place, crop and check plans (BII Import).
- `bii-signal-placement`: place configured DB signals from the plans (needs the plans placed first).

They describe the functions of the BII Import version in the same commit, so install that version. To use them, link each folder into `~/.claude/skills` (Windows: `New-Item -ItemType Junction -Path $HOME\.claude\skills\bii-plan-placement -Target <repo>\skills\bii-plan-placement`, the same for `bii-signal-placement`); they then work in `claude` and in launchers on the same configuration, such as `heimdall-claude`. They are written for agents that cannot view images themselves and use an image-reading subagent.

## Pipelines

Pipelines automate workflows that a) take time and b) are tedious.
They run Blender in the background on a separate thread.

## Equipment

Equipment are configurable geometry, that use geomtry nodes. 
They can resemble cranes, vehicles or other construction site elements, that can differ in dimensions.
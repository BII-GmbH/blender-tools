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

BII Tools also has [equipment rigs](plugins/bii_blender_tools/equipment/README.md): it rigs a model with joints (revolute, prismatic, ball), previews the movements and exports it as a dProB scriptable equipment (`.dequip`). Its `rig_api` lets scripts and AI agents do the same through a Blender MCP server; the skill `skills/bii-equipment-rig` describes how (see [Skills for AI agents](#skills-for-ai-agents)).

## Skills for AI agents

`skills/` holds skills in the [Agent Skills](https://agentskills.io) format: each folder has a `SKILL.md` that tells an agent how to do a task in Blender with the plugins. Claude Code and Codex both read them.

| Skill | Plugin | Task |
| --- | --- | --- |
| `bii-equipment-rig` | BII Tools 0.2.0 or newer | Rig a model and export it as dProB equipment (`.dequip`) |

To use them:

1. Install the plugin the skill needs from the releases, into Blender's default extension repository (Preferences > Get Extensions > Install from Disk). The skills import it as `bl_ext.user_default.<plugin>`.
2. Set up a Blender MCP server, so the agent can run Python in Blender. The skills are written for Blender Lab's [blender_mcp](https://projects.blender.org/lab/blender_mcp) (tool `execute_blender_code`). Follow its README: install its add-on in Blender and add its server to your agent (Claude Code: `claude mcp add`; Codex: an `[mcp_servers.<name>]` entry in `~/.codex/config.toml`). Start Blender with the add-on running before you give the agent a task.
3. Make the skill folder visible to your agent, as a link (it follows your checkout) or a copy (it stays at that version):

   | Agent | For all projects | For one project |
   | --- | --- | --- |
   | Claude Code | `~/.claude/skills/<skill>` | `<project>/.claude/skills/<skill>` |
   | Codex | `~/.agents/skills/<skill>` (older versions: `~/.codex/skills/<skill>`) | `<project>/.agents/skills/<skill>` |

   Windows (a junction needs no admin rights):

   ```powershell
   New-Item -ItemType Junction -Path $HOME\.claude\skills\bii-equipment-rig -Target <repo>\skills\bii-equipment-rig
   ```

   macOS and Linux:

   ```sh
   ln -s <repo>/skills/bii-equipment-rig ~/.claude/skills/bii-equipment-rig
   ```

4. Start a new agent session and ask in your own words, for example "Rig the crane in C:\models\crane.blend for dProB and export it". The agent loads the skill from its description.

The skills open, change and save files in the Blender the MCP is connected to. Give the agent a copy of a file you do not want changed, or tell it to save under a new name.

## Pipelines

Pipelines automate workflows that a) take time and b) are tedious.
They run Blender in the background on a separate thread.

## Equipment

Equipment are configurable geometry, that use geomtry nodes. 
They can resemble cranes, vehicles or other construction site elements, that can differ in dimensions.
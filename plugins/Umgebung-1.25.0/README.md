# Umgebung – railway surroundings for Blender

**Umgebung** is a Blender add-on that builds the surroundings of a railway line from open
data: a chainage line with kilometre labels, track axes with switches and crossings,
terrain from official elevation models, map textures and buildings.

The track axes are prepared for use in a rail driving simulation: every connection between
two track ends stays within a maximum diverging angle (default 10°).

> The user interface is in German. This document gives the German labels in **bold**
> with an English explanation. A detailed manual in German is in [README.de.md](README.de.md).

## Features

- **Chainage line** along a railway line, labelled every 100 m (branch lines: 500 m)
  following DB guideline Ril 883.0010. Kilometre values come from OpenStreetMap
  milestones, so they match the real kilometre posts.
- **Track axes** as curves. Simple switches become separate V-shaped meshes
  (3 vertices, 2 edges); crossings become two V-shaped switches tip to tip.
- **Terrain** from official 1 m elevation models (Baden-Württemberg, Bavaria,
  North Rhine-Westphalia), either along a corridor around the line or for a whole area.
  Flat terrain is available as well.
- **Map textures** (basemap.de, TopPlusOpen, OpenStreetMap and others) on the terrain.
- **Buildings** from OpenStreetMap footprints, placed on the terrain.
- **Area selection** on a map in the browser, with place search.

## Requirements

- Blender 4.2 or newer (tested with Blender 5.1.1)
- Internet access. Blender asks for the network permission when the add-on is installed.

## Installation

1. Download `Umgebung-<version>.zip` from the releases.
2. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ ▾ ▸ Install from Disk…** and select the zip file.
3. After an update, restart Blender.
4. Open the sidebar in the 3D Viewport (key **N**) and select the tab **Umgebung**.

## Quick start

1. **Gebiet** (area): enter a bounding box, or click **Gebiet auswählen** to draw a
   rectangle on a map in your browser and send it to Blender with
   **Nach Blender übernehmen**.
2. **Strecken Auswahl** (line selection): click **Strecken suchen** to load the railway
   data, then tick the lines and stations you need.
3. **Erzeugen** (generate): click **Kilometrierung erzeugen** for the chainage line.
4. **Gleisachsen** (track axes): click **Gleisachsen erzeugen**.
5. **Gelände und Korridor** (terrain and corridor): load terrain along the line
   (**Gelände im Korridor laden**) or for the whole area (**Gebiet als Fläche laden**),
   then add a map with **Kartentextur laden**.
6. **Gebäude** (buildings): click **Gebäude erzeugen**.

Downloads run in the background and show a progress bar; **Esc** cancels them.

## Panels

| Panel | Purpose |
|---|---|
| **Gebiet** | Area to work in: bounding box or centre and radius, map selection, import from the BLOSM add-on |
| **Strecken Auswahl** | Track types, station tracks, OpenStreetMap server (public or your own Overpass instance) |
| **Kilometrierung** | Source of the kilometre values, position of the line (track axis or line axis between tracks), label interval. Sub-panel **Darstellung**: line, ticks and labels |
| **Erzeugen** | Creates the chainage line for the selected lines |
| **Gleisachsen** | Track axes, switches and crossings (see below) |
| **Gelände und Korridor** | Terrain source and resolution, corridor width, map texture and its resolution |
| **Gebäude** | Building height defaults, minimum area, only inside the corridor, placement on the terrain |

## Track network for simulation

The simulation connects two track ends only if their directions differ by at most
`MaxDivergingAngle` (10°). The add-on prepares the track axes accordingly:

- **Switches** (**Weichen als einzelne Objekte**): each simple switch becomes a V-shaped
  mesh. Tracks connect at exactly the same coordinates as the switch legs.
- **Crossings** (**Kreuzungen als zwei V-Weichen**): an OpenStreetMap node with four
  neighbours (crossing or slip switch) becomes two V-shaped switches, tip to tip.
- **Maximum diverging angle** (**Max. Abzweigwinkel**, default 10°): limits the opening
  of each switch, the angle between the main track and each leg, and every transition at
  a crossing. Steeper legs are rotated. The following track bends back onto its original
  course within the **Übergangslänge** (transition length). OpenStreetMap nodes are never
  moved.
- **Plain crossings** (**Einfache Kreuzungen begrenzen**, on by default): also limits
  crossings without switch function. The simulation then allows turning there; switch it
  off to keep the original angle (straight on only). Crossings steeper than 45° are never
  changed.

Connections are made only through shared OpenStreetMap nodes, never through geometric
proximity; bridges and tunnels stay separate. After generating, the add-on checks all
connections and reports the largest angle. Switch objects carry the custom properties
`junction_kind`, `switch_opening_deg`, `osm_node_id` and, for crossings, `crossing_side`.

## Data sources and licences

| Data | Source | Licence |
|---|---|---|
| Railway lines, milestones, switches, buildings | OpenStreetMap via Overpass API | ODbL – © OpenStreetMap contributors |
| Terrain Baden-Württemberg | LGL, DGM1 | dl-de/by-2-0 |
| Terrain Bavaria | Bayerische Vermessungsverwaltung, DGM1 | CC BY 4.0 |
| Terrain North Rhine-Westphalia | Geobasis NRW, DGM1 | dl-de/zero-2-0 |
| Terrain elsewhere (fallback, about 30 m) | AWS Terrain Tiles | see registry.opendata.aws/terrain-tiles |
| Maps | basemap.de and TopPlusOpen (BKG) | dl-de/by-2-0 |
| Maps | OpenStreetMap, OpenRailwayMap, OpenTopoMap | CC BY-SA; volunteer-run servers, limited to 250 tiles per request |
| Place search | Nominatim | ODbL |

If you publish results, give the attribution required by these licences.

## Limitations

- Official 1 m terrain is only available for Baden-Württemberg, Bavaria and North
  Rhine-Westphalia. Elsewhere the add-on uses coarser fallback data or flat terrain.
- Chainage values are derived from OpenStreetMap milestones. They are not an official
  survey, and chainage jumps are not modelled.
- Switch geometry is schematic. Mapping errors in OpenStreetMap carry over into the result.

## Licence

GPL-3.0-or-later. Version history: `CHANGELOG_*.md` (German).

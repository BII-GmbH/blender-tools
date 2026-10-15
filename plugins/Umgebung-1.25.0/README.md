# Umgebung

This Blender add-on builds the surroundings of a railway line from open data: a chainage line with kilometre labels, track axes with switches and crossings, terrain, map textures and buildings.

## How to install

1. [Blender](https://www.blender.org/download/) 4.2 or newer has to be installed.
2. Zip this folder, then in Blender go to **Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk…** and select the zip.
3. Restart Blender. The add-on is in the 3D Viewport sidebar (key **N**), tab **Umgebung**.

## How to use

1. **Gebiet**: choose the area, e.g. with **Gebiet auswählen** on a map.
2. **Strecken Auswahl**: click **Strecken suchen** and select the railway lines.
3. Click **Kilometrierung erzeugen** (chainage) and **Gleisachsen erzeugen** (track axes).
4. Optional: **Gelände und Korridor** for terrain and map textures, **Gebäude** for buildings.

Switches and crossings are created as V-shaped meshes; connections between tracks stay within 10°, so the network can be used in a driving simulation.

## Disclaimer

The add-on needs internet access; data comes from OpenStreetMap (© OpenStreetMap contributors) and official German services. Detailed 1 m terrain is only available for Baden-Württemberg, Bavaria and North Rhine-Westphalia. The detailed manual is in German: [README.de.md](README.de.md).

# BII Umgebung

Dieses Blender-Addon baut die Umgebung einer Bahnstrecke aus offenen Daten auf: eine Kilometrierungslinie mit Kilometerangaben, Gleisachsen mit Weichen und Kreuzungen, Gelände, Kartentexturen (auch Luftbilder über den eigenen Geo-Server) und Gebäude.

## Installation

1. [Blender](https://www.blender.org/download/) 4.2 oder neuer muss installiert sein.
2. Den Ordner `bii_blender_umgebung` zippen oder eine Zip-Datei aus den [Releases](https://github.com/BII-GmbH/blender-tools/releases) laden, dann in Blender unter **Bearbeiten ▸ Einstellungen ▸ Add-ons ▸ Von Festplatte installieren…** die Zip-Datei auswählen.
3. Blender neu starten. Das Addon liegt in der Seitenleiste der 3D-Ansicht (Taste **N**), Reiter **Umgebung**.

## Benutzung

1. **Gebiet**: Gebiet festlegen, z. B. mit **Gebiet auswählen** auf einer Karte.
2. **Strecken Auswahl**: **Strecken suchen** klicken und die Strecken auswählen.
3. **Kilometrierung erzeugen** und **Gleisachsen erzeugen** klicken.
4. Optional: **Gelände und Korridor** für Gelände und Kartentexturen, **Gebäude** für Gebäude.

Weichen und Kreuzungen werden als V-förmige Meshes angelegt; Übergänge zwischen Gleisen bleiben innerhalb von 10°, damit sich das Gleisnetz in einer Fahrsimulation verwenden lässt.

## Hinweise

Das Addon braucht eine Internetverbindung; die Daten stammen aus OpenStreetMap (© OpenStreetMap-Mitwirkende) und von amtlichen deutschen Diensten. Gelände mit 1 m Auflösung gibt es nur für Baden-Württemberg, Bayern und Nordrhein-Westfalen. Luftbilder und der eigene Overpass-Server brauchen ein Service-Konto der Geo-Box: Client-ID und Secret unter **Bearbeiten ▸ Einstellungen ▸ Add-ons ▸ BII Umgebung** eintragen. Die ausführliche Anleitung steht in [README.de.md](README.de.md).

## Release

1. `version` in [blender_manifest.toml](blender_manifest.toml) erhöhen und nach `main` pushen.
2. Auf GitHub: Actions > Release BII Umgebung > Run workflow, auf `main`.

Das veröffentlicht das Release `bii_blender_umgebung-v<version>` mit der Datei `railway_chainage-<version>.zip`.

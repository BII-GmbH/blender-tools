# Umgebung 1.21.0

Weichen werden automatisch anhand von OSM ref (ersatzweise railway:ref) und name benannt. Fehlende Bezeichnungen werden durch „Weiche OSM <ID>“ ersetzt. Bei Namenskollisionen wird die OSM-ID ergänzt. Die ursprüngliche Nummer, der Name und der OSM-Link bleiben als Objekteigenschaften erhalten. OpenRailwayMap stellt dieselben OSM-Daten dar; fehlende Betriebsnummern werden nicht erfunden.

Importierte Gleisachsen haben grundsätzlich Bevel 0 und Extrusion 0, auch wenn eine alte Szene eine Linienstärke gespeichert hat. Die Gleisabschnitte bleiben in einem gemeinsamen Objekt; Weichen bleiben einzelne Meshes in „Weichen“.

## Kilometrierung und Gelände
Die Kilometerberechnung folgt der projizierten Streckenachse in XY, einschließlich ihrer Kurven. Sie ist keine Luftlinie zwischen Anfang und Ende. Bei der Geländeauflage werden Höhen ergänzt, ohne die Kilometerwerte neu zu zählen. Diese Berechnung wurde bewusst beibehalten.
Kilometrierung ist ein festgelegtes Bezugssystem. Kilometerdifferenzen müssen nicht der tatsächlich gefahrenen räumlichen Länge entsprechen, insbesondere bei historischen Fehl-/Überlängen und Kilometrierungssprüngen. Ein Neuzählen entlang einer beliebigen Geländeoberfläche wäre deshalb ungeeignet. Brücken und Tunnel folgen zudem nicht der Geländeoberfläche.
OSM-Geometrie, Tafeldaten, Projektion und Interpolation begrenzen die Genauigkeit; es handelt sich nicht um eine amtliche DB-Vermessung. Der Modus „Tafeln, stückweise“ interpoliert und modelliert ohne explizite Sprungpunkte keine echten Kilometrierungssprünge. Der bisher zu weitgehende Tooltip wurde berichtigt.

Quellen: OpenRailwayMap-Tagging https://wiki.openstreetmap.org/wiki/OpenRailwayMap/Tagging ; DB-Richtlinie 883.0010, Abschnitte 1, 3 und 4, öffentlich verfügbare ältere Kopie (kein Nachweis der aktuellen Fassung): https://www.klauserbeck.de/Kilometerstein/RiLi883/RiLi883.pdf


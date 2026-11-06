# Umgebung 1.20.0

Installiert und aktiviert in Blender 5.1.1. Die vorherige Installation und die Szene wurden vor der Änderung gesichert.

## Gleise und Weichen
- Alle normalen Gleisabschnitte eines erzeugten Gleisbilds sind Splines eines gemeinsamen Kurvenobjekts.
- Mit „Weichen als einzelne Objekte“ wird jede einfache Weiche als eigenes Mesh mit drei Vertices und zwei Kanten erzeugt, gesammelt in der untergeordneten Collection „Weichen“.
- Die Topologie erhält originale OSM-Knoten und gemeinsame Anschlusskoordinaten. Das gilt für zukünftige Importe, nicht nur für diese Testdatei. Komplexe Kreuzungen werden nicht in einfache V-Weichen umgedeutet.
- „Vorhandene Gleise zusammenfassen“ organisiert bereits erzeugte Plugin-Gleisobjekte. Für eine Neuberechnung alter Weichengeometrie muss das Gleisbild erneut erzeugt werden.


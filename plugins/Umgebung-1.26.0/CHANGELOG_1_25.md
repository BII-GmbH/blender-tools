# Umgebung 1.25.0 – Kreuzungen als V-Weichen, Abzweigwinkel höchstens 10°

Basis: Umgebung-1.24.1; alle Funktionen aus 1.24.1 bleiben enthalten.

## Änderungen

- Kreuzungen und Kreuzungsweichen (OSM-Knoten mit vier Nachbarn, je zwei zu einer
  Seite) werden als zwei V-Weichen Spitze an Spitze angelegt – zwei Meshes mit je
  3 Vertices und 2 Kanten, gemeinsame Spitze. Bisher endeten dort vier Gleise im selben
  Punkt und sahen wie zwei gekreuzte Geraden aus. Schalter: „Kreuzungen als zwei V-Weichen“.
- „Max. Abzweigwinkel“ (Standard 10°, entsprechend RailNetworkConstants.MaxDivergingAngle):
  Weichenschenkel stehen höchstens so weit auseinander und vom Stammgleis ab; an
  Kreuzungen gilt das für die Öffnung jedes V und jeden Übergang in das gegenüberliegende
  V. Steilere Schenkel werden gedreht, das Stammgleis bleibt unverändert.
- Hinter einem gedrehten Schenkel wird das Gleis auf der „Übergangslänge“ (Standard 20 m)
  wieder in die OSM-Kante eingebogen, höchstens bis zum nächsten OSM-Knoten. OSM-Knoten
  werden nie verschoben; ein gedrehter Schenkel belegt höchstens die halbe Kante
  (bei zwei Weichen auf einer Kante je 30 %).
- „Einfache Kreuzungen begrenzen“ (Standard an): auch railway=railway_crossing auf den
  Grenzwinkel bringen. Die Simulation lässt dort dann auch das Abbiegen zu; ausgeschaltet
  bleibt der Winkel erhalten. Kreuzungen steiler als 45° werden nie verbogen.
- Weichenobjekte tragen zusätzlich junction_kind, switch_opening_deg und bei Kreuzungen
  crossing_side; Kreuzungshälften heißen „Kreuzungsweiche <ref> (Seite 1/2)“.
- Nach dem Erzeugen werden alle Anschlüsse nach den Regeln der Simulation geprüft
  (Übergänge in Gegenrichtung an exakt gemeinsamen Koordinaten); die Meldung nennt die
  größte Abweichung.
- Unverändert: Verbindungen nur über gemeinsame OSM-Knoten-IDs, keine geometrischen
  Kreuzungen, gleiche Anschlusskoordinaten, Weichen weiterhin 3 Vertices/2 Kanten.

## Prüfung

Sechs Bahnhöfe, Daten vom konfigurierten internen Overpass-Server, alle Gleise und
Bahnhofsgleise gemeinsam ausgewählt, 1.24.1 gegen 1.25.0:

| Bahnhof | 1.24.1: Übergänge / Öffnungen über 10° | 1.25.0 | Kreuzungen als 2 V | größte Lageänderung |
|---|---|---|---|---|
| Wörth (Rhein) | 0 / 0 | 0 / 0 | 2 von 2 | 0,48 m |
| Karlsruhe Hbf | 4 (max. 15,2°) / 0 | 0 / 0 | 22 von 22 | 0,42 m |
| Ulm Hbf | 3 (max. 19,4°) / 1 (14,6°) | 0 / 0 | 9 von 9 | 0,94 m |
| Aulendorf | 2 (max. 13,5°) / 0 | 0 / 0 | 4 von 4 | 0,48 m |
| Biberach (Riß) | 0 / 0 | 0 / 0 | – | 0,00 m |
| Friedrichshafen | 1 (10,7°) / 0 | 0 / 0 | – | 0,15 m |

- Gesamtlänge von Gleisen und Weichenschenkeln unverändert (höchstens +0,3 m durch
  die Einbiegungen).
- Jede Weiche und jede Kreuzungshälfte hat genau 3 Vertices; alle Schenkelenden liegen
  exakt auf einem Gleisende oder einer benachbarten Weiche (wie in 1.24.1).
- In Blender 5.1.1 installiert und am Bahnhof Wörth (Rhein) erzeugt.

Nach dem Update Blender neu starten und „Strecken suchen“ erneut ausführen.

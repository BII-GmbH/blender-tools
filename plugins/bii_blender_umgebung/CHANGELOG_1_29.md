# Umgebung 1.29.0 – Große Gebiete Stück für Stück

Basis: Umgebung-1.28.0; alle Funktionen bleiben enthalten.

## Leistung

- **Stückweiser Aufbau.** „Gebiet als Fläche laden“, „Gelände im Korridor laden“,
  „Kartentextur laden“ und „Gebäude erzeugen“ laden im Hintergrund und bauen in Blender
  ein Stück nach dem anderen auf. Jedes Stück erscheint sofort in der Szene; Fehler in der
  Eingabe sieht man schon am ersten. **Esc** bricht ab, fertige Stücke bleiben erhalten
  (ein Rückgängig-Schritt).
- **Gebiet:** quadratische Stücke (neues Feld „Stückgröße“, Standard 2 km), jedes mit
  eigener Kartentextur (höchstens 4096 px Kante). Nicht mehr gebrauchte Höhenkacheln
  werden nach jedem Stück freigegeben. Sammlung „Gelände Gebiet“; ein neuer Lauf ersetzt
  den vorigen.
- **Gebäude:** Teilgebiete von etwa 5 × 3,5 km, je ein Objekt „Gebäude 001 …“. Vorab
  zählt eine kleine Overpass-Abfrage die Gebäude und zeigt die Zahl im Fortschritt. Die
  Höchstzahl entfällt.
- **Größe vorab sehen:** Das Panel zeigt für „Gebiet als Fläche laden“ Ausdehnung,
  Punktzahl, Stückzahl und Kartenkacheln. Ab 40 Mio Punkten erscheint eine Warnung mit
  dem geschätzten Speicherbedarf in Blender; gesperrt wird nichts.
- **Speicher:** Höhenraster als numpy-Arrays (float32) statt Python-Listen; Meshes direkt
  aus Arrays (`foreach_set`) statt `from_pydata`; Kartentextur in einem Durchgang statt
  einer Maske über das ganze Bild je Kachel.
- **Geo-Box-Höhen:** vier Kacheln gleichzeitig.
- **Höhenbezug („Auf Null beziehen“):** eine grobe Stichprobe über das ganze Gebiet bzw.
  die erste Linie, bevor das erste Stück entsteht – damit liegen alle Stücke auf derselben
  Null wie bisher. Ohne Geo-Box bestimmt das erste Stück die Null.
- Die Kachelgrenze („max. Kacheln“) gilt nicht mehr für die eigene Geo-Box.

## Bedienoberfläche

- Neues Panel **Status** ganz oben: Fortschritt und letzte Meldung für alle Befehle. Die
  drei bisherigen Statuskästen in einzelnen Panels entfallen.
- Panel **Erzeugen** entfällt; „Kilometrierung erzeugen“ steht im Panel Kilometrierung.
- Quellen-Zeilen („Strecken/Höhen/Karte: Geo-Box“) entfallen – das steht in Datenquelle.
- Doppelte Felder im Gelände-Panel zusammengeführt (Abschnittslänge, Auf Gelände legen).
- Erklärungstexte entfernt; die Add-on-Einstellungen verweisen nur noch auf das Panel
  Datenquelle.
- Markierung **„Geo-Box max.“** an der feinsten Stufe, für die die Geo-Box Daten hat:
  Rasterweite **5 m**, Kartenqualität **0,5 m/Px**. Feinere Stufen bleiben für öffentliche
  Dienste wählbar.

## Prüfung

Blender 5.1.2, echte Geo-Box, Würzburg:

| Fall | 1.28.0 | 1.29.0 |
|---|---|---|
| Gebiet 4 × 4 km, 5 m, Karte 1 m/Px | 81 s, +1,95 GB | 11 s, +0,63 GB |
| Gebiet 10 × 20 km, 5 m, Karte 2 m/Px | (hochgerechnet ~24 GB) | 173 s, +1,44 GB, 8 Mio Punkte, 60 Stücke |
| Gebäude 10 × 20 km | bei Grenze 20 000 abgelehnt | 53 011 Gebäude, 5–16 s, +0,12 GB |

- Ganzer Ablauf (Strecken, Kilometrierung, Gleise, Korridor, Karte, Gebiet, Gebäude)
  mit 1.28.0 und 1.29.0: Höhen aller Gelände- und Gebäudepunkte identisch (bis auf den
  Höhenbezug, Abweichung 0,1 m), gleiche Punkt-, Flächen- und Gebäudezahlen.
- Mit Fenster: stückweises Erscheinen, Fortschritt, Esc-Abbruch, Panels.
- Auflösung der Geo-Box gemessen: Höhen ~5 m (Quellen DGM1, serverseitig umgerechnet);
  ohne Landes-DGM (z. B. Niedersachsen, Thüringen) und außerhalb DACH ~30 m (GLO-30).
  Luftbilder: alle 18 Datensätze 0,5 m.

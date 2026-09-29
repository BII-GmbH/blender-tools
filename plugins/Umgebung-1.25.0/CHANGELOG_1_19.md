# Umgebung 1.19.0 – Weichen und Gleisanschlüsse

## Änderungen

- Gleisachsen werden aus einem gemeinsamen Netz der ausgewählten OSM-Ways aufgebaut. Gemeinsame Knoten bleiben erhalten, auch innerhalb eines Ways und über Strecken-/Bahnhofsgruppen hinweg.
- Neuabtastung ergänzt Punkte, statt ursprüngliche Knoten zu ersetzen. Auch kurze Gleisverbindungen bleiben erhalten.
- „Weichen als einzelne Objekte“ ist standardmäßig aktiviert. Einfache Weichen werden als Mesh mit exakt drei Vertices und zwei Kanten erzeugt. Namen enthalten die OSM-Knoten-ID; außerdem sind `railway_switch` und `osm_node_id` als Objekteigenschaften vorhanden.
- Die V-Kanten werden aus den übrigen Gleisen herausgenommen. Alle drei Anschlüsse verwenden dieselben Koordinaten. Ein gemeinsamer Objektursprung verhindert unterschiedliche Float-Rundung an den Anschlüssen.
- Maximale Weichenschenkellänge: standardmäßig 10 m, einstellbar. Ein Schenkel endet spätestens am nächsten OSM-Knoten. Bei gegenüberliegenden Weichen wird ein kurzes Verbindungsstück erhalten. Das ist eine schematische OSM-Darstellung, keine vermessene physische Weichenlänge.
- Unklare Dreifachabzweige und Knoten mit mehr als drei Nachbarn werden nicht künstlich in eine V-Weiche umgewandelt. Geometrische Kreuzungen ohne gemeinsame OSM-ID werden nicht verbunden.
- An der Bounding-Box-Grenze angeschnittene Weichen bleiben als gekürzte Gleisabschnitte erhalten.
- „Direkt auf Gelände“ berücksichtigt jetzt alle geladenen Korridor-Geländesegmente. Shrinkwrap und das spätere Auf-Gelände-Legen berücksichtigen auch Weichen-Meshes.
- Gemeinsame Auswahl mehrerer Strecken ersetzt deren ältere, vom Plugin erzeugte Einzelausgaben. Eigene unmarkierte Objekte bleiben erhalten.
- Manifest-Schema auf 1.0.0 korrigiert; Add-on-Version 1.19.0.

## Benutzung

1. ZIP über Blenders Erweiterungs-/Add-on-Einstellungen „Install from Disk“ installieren bzw. aktualisieren. Danach Blender neu starten, damit die Python-Module der alten Version nicht mehr geladen sind.
2. Gebiet setzen, „Strecken suchen“ und zusammengehörende Strecken sowie benötigte Bahnhofsgleise gemeinsam auswählen. Weichen können nur vollständig erkannt werden, wenn ihre drei Anschlussgleise in der Auswahl enthalten sind.
3. Im Bereich „Gleisachsen“: „Alle Gleise“ und „Weichen als einzelne Objekte“ aktivieren, dann „Gleisachsen erzeugen“.
4. Bei mehreren ausgewählten Gruppen entsteht die gemeinsame Sammlung „Gleise Gleisbild Auswahl“. Gleisachsen sind POLY-Kurven, Weichen reine Kanten-Meshes. Die Linienstärke gilt für Kurven; die Weichen besitzen bewusst keine zusätzlichen Profil-Vertices. Für eine rein schematische Ansicht Linienstärke 0 verwenden.
5. Der vorhandene Korridor bleibt bei 100 m **je Seite**. Gelände/Kartentexturen werden weiterhin mit den vorhandenen Korridor-Funktionen entlang der Kilometrierung erzeugt.

Bereits erzeugte Geometrie wird durch die Installation allein nicht verändert: Gleisachsen erneut erzeugen. Bei früheren Importen mit anderen Sammlungsnamen ggf. die alte Ausgabe ausblenden, damit sie nicht mit der neuen verglichen wird.

## Prüfung

Testgebiet aus dem Screenshot: Süd 50.7504, Nord 50.9052, West 7.05803, Ost 8.05573. Daten vom im Original-Plugin konfigurierten Server.

- 7 automatisierte Geometrietests bestanden, darunter interne Way-Abzweige, eng benachbarte Weichen, fehlende Knoten, Kreuzungen, doppelte Ways, geschlossene Ringe und das reale Gebiet.
- Rohdaten: 1655 Ways, 538 einfache Weichen; Gesamtkantenlänge vor/nach Separierung unverändert (510989.414803 m einschließlich über die Bounding Box hinausreichender Ways).
- Blender 5.1.1 mit Bounding-Box-Zuschnitt: 968 Gleisabschnitte und 528 separate Weichen. Die teilweise außerhalb liegenden Weichen werden als Gleisabschnitte zugeschnitten.
- Alle Anschlüsse in Blender-Weltkoordinaten exakt gleich; jede Weiche hat 3 Vertices/2 Kanten. Anschlüsse auch nach DIRECT und ausgewertetem SHRINKWRAP geprüft.
- Wiederholtes Erzeugen ohne anwachsende Objekt-/Mesh-/Kurvenzahl und Umschalten der Separierung geprüft.
- Blender-Manifest und abschließendes ZIP mit Blenders Extension-Validator geprüft.

Die beigefügte .blend-Datei enthält das neu erzeugte Gleisnetz des Beispielgebiets, auf eine Weiche gezoomt. Korridor-Einstellung: 100 m je Seite. Sie enthält kein neu heruntergeladenes Höhenmodell und keine Kartentexturen. Das PNG zeigt ein gerendertes Detail: orange die Weichen, blau die weiterführenden Gleise. Die drei orangefarbenen Kugeln markieren nur im Vorschaubild die Vertices; sie gehören nicht zur gelieferten Weichengeometrie.

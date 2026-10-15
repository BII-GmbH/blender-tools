# Umgebung – Bahnstrecken-Umgebung für Blender

Erzeugt entlang von Bahnstrecken eine **Kilometrierungslinie** und beschriftet sie
standardmäßig alle **100 m** mit dem Kilometerwert. Die Gleise selbst werden bewusst
**nicht** erzeugt – nur die Achse als Linie, die Marken und die Zahlen.

Datenquelle sind die Bahndaten von **OpenStreetMap**, also genau die Daten, die die
[OpenRailwayMap](https://www.openrailwaymap.org) darstellt. Die Kilometrierung wird aus
dem Tag `railway:position` der Kilometrierungstafeln (`railway=milestone`) abgeleitet –
die Zahlen entsprechen damit den tatsächlichen Hektometertafeln im Gelände und nicht
einer bei Null beginnenden Zählung.

## Installation

Blender 4.2 oder neuer:

1. `Bearbeiten ▸ Einstellungen ▸ Erweiterungen (Add-ons)`
2. Oben rechts `▾ ▸ Install from Disk…`
3. `railway_chainage-1.18.0.zip` auswählen – die Erweiterung wird aktiviert.

Das Panel liegt in der 3D-Ansicht in der Seitenleiste (Taste **N**) im Reiter
**Umgebung**. Jeder Abschnitt trägt ein Symbol in der Überschrift, damit sich die
Bereiche schneller auseinanderhalten lassen.

Zwei selten gebrauchte Bereiche stehen bewusst nicht im Panel, bleiben aber über die
Suche (**F3**) erreichbar: *Zwischenspeicher leeren* und *Szene auf Korridor
zuschneiden*. Die Zwischenspeicherung der Serverantworten ist dabei weiterhin
eingeschaltet.

Die Erweiterung fragt beim Aktivieren die Berechtigung *Netzwerk* ab; sie wird für
die Overpass-API (OpenStreetMap), die Höhendienste und die Kartenkacheln benötigt.
Für die Gebietsauswahl startet das Addon kurzzeitig einen Server auf `127.0.0.1`,
der nur vom eigenen Rechner aus erreichbar ist.

## Ablauf

### 1. Gebiet festlegen

Wie bei BLOSM über Koordinaten:

* **Bounding Box** – vier Eckkoordinaten, oder
* **Mittelpunkt + Radius** – ein Punkt und die halbe Kantenlänge.

Hilfsschaltflächen:

| Schaltfläche | Funktion |
|---|---|
| **Gebiet auswählen** | Öffnet eine Karte im Browser und übernimmt das Gebiet mit einem Klick direkt nach Blender – siehe unten |
| **BLOSM-Karte** | Öffnet die Auswahlkarte des BLOSM-Addons (prochitecture.com). Dort *Show selection rectangle*, Rechteck setzen, Koordinaten kopieren – anschließend hier *Aus Zwischenablage*. Braucht eine Internetverbindung |
| **Aus Zwischenablage** | Übernimmt das kopierte Gebiet. Versteht außerdem kopierte Adressen: OpenStreetMap (`#map=…` oder `?bbox=…`), Google Maps (`@lat,lon,17z`), vier Zahlen als `west,süd,ost,nord` oder ein Koordinatenpaar `48.1, 9.79` |
| **Gebiet von BLOSM** | Übernimmt das im BLOSM-Addon eingestellte Gebiet |
| **In eigener Karte ansehen** | Öffnet das Gebiet in der eigenen Kartenanwendung. Die Adresse steht im Feld darunter (Voreinstellung: die selbst gehostete Overpass-Turbo-Instanz). Enthält sie `{lat}`, `{lon}` oder `{zoom}`, werden diese ersetzt; sonst wird für Overpass Turbo automatisch `C=lat;lon;zoom` angehängt |

#### Gebiet auf der Karte auswählen

*Gebiet auswählen* öffnet eine Kartenseite im Browser. Neu seit 1.14: Die Seite
läuft auf einem winzigen Server, den das Addon nur für die Dauer der Auswahl auf
`127.0.0.1` startet (ausschließlich lokal erreichbar, er wird danach sofort wieder
beendet). Ein Klick auf **Nach Blender übernehmen** setzt das Gebiet damit
unmittelbar in den Einstellungen – der Umweg über die Zwischenablage entfällt.
Blender bleibt währenddessen bedienbar, **Esc** bricht die Auswahl ab.

Auf der Karte:

* **Suchfeld** für Orte, Bahnhöfe und Bauwerke (über Nominatim). Ein Treffer setzt
  Kartenausschnitt und Rechteck.
* **Ziehen** verschiebt die Karte, **Mausrad** zoomt zum Mauszeiger hin.
* **Umschalt+Ziehen** oder die Schaltfläche *Rechteck aufziehen* legt ein neues
  Rechteck an; die **vier Eckgriffe** ändern es, das Rechteck selbst lässt sich
  verschieben. Die Größe des Ausschnitts steht laufend in Kilometern daneben.
* **Koordinaten kopieren** bleibt als zweiter Weg erhalten (dann hier
  *Aus Zwischenablage*) – etwa wenn der Browser den lokalen Server nicht erreichen darf.

Die Seite kommt ohne Kartenbibliothek aus und funktioniert damit auch in
abgeschotteten Netzen. Sie nutzt den unter *Kartentextur* eingetragenen eigenen
Kachelserver, sonst basemap.de; ehrenamtlich betriebene Kachelserver werden hier
bewusst nicht verwendet, weil beim Herumfahren schnell hunderte Abrufe zusammenkommen.

**Nullpunkt der Szene verwenden** (empfohlen): Ist in der Szene bereits ein
Projektionsmittelpunkt hinterlegt (`scene["lat"]` / `scene["lon"]` – das setzt auch
BLOSM beim ersten Import), wird dieser benutzt. Die Kilometrierung liegt dann
deckungsgleich über einem BLOSM-Import. Andernfalls wird die Mitte des Gebiets zum
Nullpunkt und wie bei BLOSM in der Szene hinterlegt. Stammt der hinterlegte Nullpunkt
aus einem weit entfernten Gebiet, liegt die Kilometrierung entsprechend weit vom
Weltursprung entfernt – das ist beabsichtigt und hält verschiedene Importe zueinander
lagerichtig.

**Eigener OSM-Server:** Unter *Strecken* lässt sich zwischen den öffentlichen
Overpass-Servern und einer **eigenen Instanz** umschalten (Feld *Adresse*, z. B.
`http://server/api/interpreter`). Eine eigene Instanz hat kein Rate-Limit und
antwortet meist deutlich schneller; der Datenstand richtet sich nach dem dort
eingespielten Auszug.

### 2. Strecken suchen

Lädt die Daten und listet alle gefundenen Strecken auf – gruppiert nach
Streckennummer (`ref`), sonst nach Name oder Streckenrelation. Pro Eintrag werden
die Länge und die Anzahl der zugeordneten Kilometrierungstafeln angezeigt. Strecken
mit Tafeln werden automatisch vorausgewählt.

Die angezeigte Länge ist die Summe **aller** Gleisabschnitte dieser Strecke im Gebiet.
Erzeugt wird daraus eine durchgehende Achse – bei zweigleisigen Strecken ist die Linie
also etwa halb so lang wie der angezeigte Wert.

Der Abruf läuft im Hintergrund, Blender bleibt bedienbar; **Esc** bricht ab.
Antworten werden einen Tag lang lokal zwischengespeichert.

**Bahnhofsgleise:** Mit *Bahnhofsgleise aufnehmen* erscheinen zusätzlich die Gleise im
Umkreis von Bahnhöfen und Haltepunkten – je Betriebsstelle eine eigene Gruppe, in der
Liste mit einem Haussymbol gekennzeichnet. Sie sind nicht kilometriert (die
Kilometrierung gehört zur durchgehenden Strecke) und lassen sich deshalb nur über
*Gleisachsen erzeugen* anlegen; dort werden immer alle Gleise der Betriebsstelle
gebaut. Der Umkreis ist einstellbar.

### 3. Kilometrierung

| Quelle | Bedeutung |
|---|---|
| **Kilometrierungstafeln (OSM)** | Ausgleichsrechnung über alle Tafeln der Strecke: Richtung und Nullpunkt werden robust bestimmt (Ausreißer und mehrdeutige Angaben an Kreuzungen fallen heraus), die Marken haben exakt gleiche Abstände. Standard. |
| **Tafeln, stückweise** | Zwischen den Tafeln wird interpoliert. Bildet Fehlkilometer ab, die Markenabstände folgen dafür der Erfassungsgenauigkeit der Tafeln. |
| **Manuell** | Beginnt am Anfang der Linie mit dem eingestellten Start-km. |

Die erzeugte Linie wird immer in Richtung **aufsteigender** Kilometrierung orientiert.

Am **Anfangspunkt der Linie** steht immer zusätzlich die exakte Kilometrierung dieses
Punktes, auf drei Nachkommastellen (metergenau) – zum Beispiel `92,246`. Dort beginnt
die Linie ja fast nie auf einem runden Hundertmeterwert. Die Marke wird wie ein voller
Kilometer hervorgehoben und auch dann beschriftet, wenn sonst nur ganze Kilometer
beschriftet werden; fällt der Linienanfang ausnahmsweise genau auf eine reguläre Marke,
wird diese verwendet statt eine zweite zu setzen. Läge die erste reguläre Beschriftung
so dicht dahinter, dass sich beide überdecken würden, entfällt sie – ihr Querstrich
bleibt erhalten.

**Auf Gebiet zuschneiden** (Standard an): Die Linien enden exakt an der Grenze des
eingestellten Gebiets. Der Server liefert immer vollständige Gleisabschnitte, die
meist einige Kilometer über die Bounding Box hinausreichen – ohne diese Option wären
die Linien entsprechend länger. Der Zuschnitt erfolgt in geografischen Koordinaten
genau auf der angegebenen Bounding Box; die Kilometrierung wird vorher an der
gesamten Linie ausgeglichen und bleibt dadurch unverändert gültig. Am zugeschnittenen
Anfang steht wieder der exakte Kilometerwert.

Weitere Einstellungen: Lage der Linie zu den Gleisen und Markenabstand (beides nach
Ril 883.0010, siehe unten), Mindestlänge, maximaler Knickwinkel an Weichen und die
Höhe der Linie über der Nullebene.

Wird das Gebiet nach dem Suchen geändert, meldet **Kilometrierung erzeugen**, dass
erneut gesucht werden muss – nachgeladen wird dort bewusst nicht, damit Blender
während einer Serverabfrage nie blockiert.

### 4. Darstellung und Erzeugen

**Darstellung** ist ein eingeklapptes Unterpanel von *Kilometrierung* – es gehört
inhaltlich dazu und steht deshalb dort statt als eigener Abschnitt. Einstellbar sind
Linienstärke, Querstriche (mit eigener Länge für volle Kilometer), Beschriftung
(jede Marke oder nur volle Kilometer), Format (`12,3` / `12.3` / `km 12,3`),
Schriftgröße, seitlicher Abstand, Ausrichtung sowie Farbe.

Erzeugt wird je Strecke eine Sammlung unterhalb von **Kilometrierung**:

```
Kilometrierung
└── KM 1720  Hannover–Hamburg
    ├── KM-Linie 1720  Hannover–Hamburg      (Kurve, Poly)
    ├── KM-Marken 1720  Hannover–Hamburg     (Mesh mit Querstrichen)
    └── KM 92,3 … KM 100,5                   (Textobjekte)
```

Marken und Beschriftungen sind an die Linie geparentet. An der Linie hängen außerdem
benutzerdefinierte Eigenschaften zur Nachvollziehbarkeit:
`chainage_source`, `chainage_km_start`, `chainage_km_end` (beide metergenau),
`chainage_interval_m`, `chainage_milestones_used`, `chainage_max_deviation_m`.

## Gelände und Korridor

Für Streckenplanungen ist meist nur ein schmaler Streifen um das Gleis interessant.
Das Panel **Gelände und Korridor** erzeugt genau diesen Streifen – mit amtlichen
Höhendaten statt der groben weltweiten Modelle.

### Geländeart: Höhenmodell oder eben

Über **Geländeart** lässt sich das Gelände wahlweise aus amtlichen Höhendaten
aufbauen oder als **ebene Fläche ohne Höhenmodell** anlegen. Die ebene Variante
lädt nichts herunter, ist dadurch sofort da und lässt sich genauso mit der
Kartentextur belegen – als schnelle Arbeitsgrundlage, für Gebiete ohne offenes
DGM oder wenn die Höhe schlicht nicht gebraucht wird. Sie gilt für beide
Schaltflächen, also für den Korridor ebenso wie für *Gebiet als Fläche laden*.

Einstellbar sind:

* **Rasterweite** – Punktabstand der Fläche (Standard 25 m; eine Ebene braucht
  kein feines Raster, gröbere Werte halten die Szene klein),
* **Geländehöhe** – die z-Höhe der Fläche. Sie gilt genau so, wie sie eingetragen
  ist; ein Höhenbezug aus BLOSM wird hier bewusst nicht abgezogen.

Shrinkwrap, Kartentextur, Abschnittsunterteilung und die Gebäude arbeiten mit der
ebenen Fläche genauso wie mit einem Höhenmodell.

### Geländemodell im Korridor

**Gelände im Korridor laden** baut ein Geländemodell entlang der erzeugten
Kilometrierungslinien auf, begrenzt auf die eingestellte **Korridorbreite**
(Standard 100 m beiderseits). Die Höhen stammen vom Web-Coverage-Service des
Landesamtes für Geoinformation und Landentwicklung Baden-Württemberg
(**DGM1**, 1 m Raster, offene Daten). Die Rasterweite ist frei wählbar von **1 m**
bis 25 m; der Server liefert bereits in der gewählten Weite, sodass nur die
tatsächlich benötigte Datenmenge übertragen wird.

Geladen werden ausschließlich die Kacheln, die der Korridor berührt – die Länge der
Strecke spielt daher keine Rolle. Der Abruf läuft im Hintergrund, Blender bleibt
bedienbar, **Esc** bricht ab. Heruntergeladene Kacheln werden lokal gespeichert.

**Auf Gelände legen** bestimmt, wie Linie, Marken und Beschriftung mit dem Gelände
verbunden werden:

| Modus | Wirkung |
|---|---|
| **Direkt (Punkte setzen)** | Die Höhen werden fest aus den Höhendaten übernommen – unabhängig von weiteren Objekten in der Szene |
| **Shrinkwrap-Modifier** | Linie und Marken erhalten einen Shrinkwrap-Modifier (Projektion entlang Z) auf das Gelände. Nicht zerstörend: Wird das Gelände später verändert oder ausgetauscht, folgt die Linie automatisch |
| **Nicht** | Die Linie bleibt auf der eingestellten Höhe |

Der Shrinkwrap lässt sich auch nachträglich setzen – die beiden Knöpfe unter dem
Geländeabschnitt legen ihn an bzw. entfernen ihn wieder. Als Ziel dient das
Korridorgelände; ist keines vorhanden, wird ein Objekt mit „Gelände“ oder „Terrain“
im Namen verwendet (z. B. ein BLOSM-Import). Textobjekte würde ein Shrinkwrap
verformen, sie werden deshalb weiterhin über ihre Höhe gesetzt.

Der Höhenbezug richtet sich nach einem vorhandenen BLOSM-Gelände (`scene["height_offset"]`),
sonst wird der tiefste Punkt auf z = 0 gelegt – beide Modelle liegen damit lagerichtig
übereinander.

Hinterlegt sind derzeit zwei Dienste, die Auswahl erfolgt auf Wunsch **automatisch
nach Lage** des Gebiets:

| Dienst | Gebiet | Bezug | Lizenz |
|---|---|---|---|
| LGL Baden-Württemberg, DGM1 | Baden-Württemberg | WCS | dl-de/by-2-0, „Datenquelle: LGL, www.lgl-bw.de“ |
| Geobasis NRW, DGM1 | Nordrhein-Westfalen | WCS | dl-de/zero-2-0 |
| Bayerische Vermessungsverwaltung, DGM1 | Bayern | 1-km-Kacheln als Datei | CC BY 4.0, „Datenquelle: Bayerische Vermessungsverwaltung – www.geodaten.bayern.de“ |

> Bei Veröffentlichung ist die jeweilige Quellenangabe zu übernehmen. Liegt das Gebiet
> außerhalb beider Dienste, meldet das Addon dies und es lässt sich ein Dienst von Hand
> wählen. Weitere Länder lassen sich mit je einem Eintrag in `dgm.py` ergänzen –
> nötig sind Endpunkt, Coverage-Name, UTM-Zone, die Achsnamen des WCS und der
> abgedeckte Bereich.

### Geländeabschnitte

Über **Abschnittslänge** wird das Gelände in Stücke entlang der Strecke unterteilt
(Standard 5 km, 0 = nicht unterteilen). Jeder Abschnitt ist ein eigenes Objekt mit
eigenen UV-Koordinaten und trägt eine eigene Kartentextur.

Das ist der entscheidende Hebel für die Auflösung: Eine einzelne Textur ist auf
16384 Pixel Breite begrenzt (Grafikkarten können selten mehr), sodass eine 90 km
lange Strecke in einem Bild nur 5,5 m je Pixel erreicht. In 2-km-Abschnitten sind
es bis zu 0,12 m je Pixel – begrenzt dann nur noch durch die Kartenquelle.

Die Zuordnung erfolgt über die Station der Flächenmitte, sodass die Abschnitte
lückenlos aneinandergrenzen. Kilometrierungslinie und Gleisachsen bekommen je
Abschnitt einen eigenen Shrinkwrap-Modifier – Punkte, die ein Abschnitt nicht
trifft, lässt er unverändert, in Reihe ergibt das die durchgehende Auflage.

### Kartentextur auf dem Korridor

**Kartentextur laden** legt ein Kartenbild auf das Korridorgelände. Die Textur folgt
dabei der Strecke: Längsachse ist die Station, Querachse der Abstand zur
Kilometrierungslinie. Dadurch bleibt das Bild schmal – für 22 km Strecke und 100 m
Korridor entsteht eine Textur von 16384 × 149 Pixeln (1,35 m je Pixel) statt eines
riesigen Bildes über die gesamte, schräg liegende Bounding Box. Die UV-Koordinaten
dafür werden bereits beim Geländeaufbau angelegt.

Voreingestellt ist **basemap.de** des Bundesamtes für Kartographie und Geodäsie – ein
amtlicher Dienst, der ausdrücklich für die Nutzung in Anwendungen vorgesehen ist
(dl-de/by-2-0). Ebenfalls wählbar sind **TopPlusOpen** (farbig oder grau, ebenfalls BKG)
sowie **OpenStreetMap**, **OSM mit OpenRailwayMap** und **OpenTopoMap**.

> Die drei letztgenannten laufen auf **ehrenamtlich betriebenen Servern**. Massenhafte
> Abrufe verstoßen gegen deren Nutzungsbedingungen und führen zur Sperrung
> („Access blocked“). Das Addon begrenzt sie deshalb auf 250 Kacheln je Durchlauf.
> Für größere Gebiete bitte basemap.de, TopPlusOpen oder einen eigenen Kachelserver
> verwenden. Über das Feld *eigener Server* lässt sich ein eigener Kachelserver
als Vorlage eintragen (`http://server/tile/{z}/{x}/{y}.png`); er ersetzt dann die
gewählte öffentliche Karte. Die Zoomstufe ergibt sich automatisch aus der gewünschten
Auflösung; die Bildbreite ist auf 16384 Pixel begrenzt, weil Grafikkarten meist nicht
mehr darstellen. Kacheln werden lokal zwischengespeichert, der Abruf läuft im
Hintergrund.

**Kartenqualität** wird als Stufe gewählt – Übersicht (4 m/Px), Mittel (2 m), Standard
(1 m), Detail (0,5 m), Maximal (0,25 m) oder ein eigener Wert. Direkt darunter steht,
was daraus folgt: Bildgröße in Pixeln, tatsächliche Auflösung, Kartenstufe und die
geschätzte Kachelzahl – und ob das innerhalb der Grenzen liegt. Ist es zu groß,
erscheint das Feld mit Warnsymbol samt Grenzwerten; andernfalls steht dort, wie fein
es in diesem Gebiet überhaupt gehen kann (bei langen Korridoren begrenzt die
Bildbreite, bei großen Flächen das Pixelbudget).

Während Höhen- oder Kartendaten geladen werden, zeigt das Panel einen
**Fortschrittsbalken** mit der Zahl der geladenen Kacheln; dieselbe Meldung steht in
Blenders Statusleiste. Der Abruf läuft im Hintergrund und lässt sich mit Esc abbrechen.

Die Auflösung ist doppelt begrenzt: durch die Zahl der Kacheln (*max. Kacheln*) und
durch die Summe aller Texturpixel (150 Mio). Für sehr feine Auflösungen über lange
Strecken – etwa 0,2 m je Pixel über 90 km – ist ein **eigener Kachelserver** nötig;
über die öffentlichen Dienste wären das mehrere tausend Kacheln je Durchlauf.

> Die Kartendienste werden ehrenamtlich betrieben. Die Zahl der Kacheln je Abruf ist
> deshalb begrenzt (Voreinstellung 500); bitte sparsam abrufen und die Quellenangabe
> übernehmen – sie steht nach dem Laden als Eigenschaft `quelle` am Bild und in der
> Statuszeile.

### Gebiet als Fläche laden

Neben dem Korridor entlang einer Strecke lässt sich auch ein **rechteckiger Ausschnitt**
laden: *Gebiet als Fläche laden* erzeugt ein Geländemodell über die eingestellte
Bounding Box und belegt es auf Wunsch gleich mit der Karte. Das braucht weder eine
Kilometrierungslinie noch Gleisdaten und eignet sich für einzelne Bauwerke oder
Örtlichkeiten.

Die Textur wird hier nicht entlang einer Achse gestreckt, sondern liegt planar über
dem Ausschnitt; die UV-Koordinaten entstehen direkt aus den UTM-Koordinaten.

### Lage und Ausrichtung des Flächengeländes

Das Flächengelände wird **entlang der Achsen der Szene** aufgebaut, nicht entlang
der UTM-Achsen: Der Ausschnitt liegt dadurch achsparallel in Blender, mit geraden
Kanten in X und Y.

Der Unterschied ist keine Kleinigkeit. Gitternord einer UTM-Zone und Nord der
Szenenprojektion laufen um die **Meridiankonvergenz** auseinander, und die wächst
mit dem Abstand zum Mittelmeridian der Zone. Bayern führt sein gesamtes Gebiet in
Zone 32 (Mittelmeridian 9°), also auch den Bayerischen Wald jenseits von 13°: Dort
stünde ein UTM-Gitter rund **3,2°** verdreht in der Szene. Gemessen an einem
Ausschnitt bei 13,23° Ost / 49,07° Nord.

Die Höhen kommen weiterhin aus dem UTM-Raster des Dienstes – nur die Gitterpunkte
liegen jetzt auf den Szenenachsen. Weil das umschließende achsparallele Rechteck
etwas größer ist als die geografische Bounding Box, reicht das Gelände geringfügig
über den eingestellten Bereich hinaus.

### Kachelgrenzen

Die Höhenkacheln der Landesdienste stoßen aneinander, überlappen sich aber nicht:
Eine Kachel mit 1000 × 1000 Stützstellen deckt 1000 m ab, ihre letzte Stützstelle
liegt also 1 m vor der Kachelgrenze. Zwischen ihr und der ersten Stützstelle der
Nachbarkachel bleibt ein schmaler Streifen ohne eigenen Wert; zusätzlich gehört ein
Punkt genau auf der Grenze rechnerisch zur nördlich anschließenden Kachel, liegt in
deren Raster aber auf dem ausgeschlossenen Rand. Beides schnitt früher je eine
vollständige Punktreihe aus dem Gelände – im Modell als durchgehende Lücke sichtbar.
Das Addon prüft deshalb die angrenzenden Kacheln mit und übernimmt im Nahtstreifen
deren Randwert.

### Lücken im Geländemodell füllen

Die Landesdienste enden an der Landesgrenze – eine Strecke, die sie quert, bekäme
sonst ein löchriges Gelände. Mit **Lücken füllen** (Standard an) springt das Addon
dort auf weltweite Höhenkacheln um (AWS Terrain Tiles im Terrarium-Format, Datenbasis
u. a. SRTM und EU-DEM). Die Zoomstufe der Ersatzdaten ist einstellbar: 12 entspricht
etwa 24 m, 13 etwa 12 m, 14 etwa 6 m Punktabstand.

> **OpenStreetMap enthält keine flächendeckenden Höhendaten** – dort stehen nur
> vereinzelte `ele`-Angaben an Gipfeln, Bahnhöfen oder Brücken. Ein Geländemodell
> lässt sich daraus nicht ableiten, deshalb der Umweg über die Höhenkacheln.

Zwischen den Rasterpunkten wird **bikubisch interpoliert** (Catmull-Rom). Ohne das
schlagen die ganzzahligen Meterwerte des rund 30 m weiten Quellrasters als sichtbare
Geländestufen durch: In einem Testprofil über 480 m lagen vorher 20 von 60 Schritten
auf gleicher Höhe, jetzt nur noch einer.

Die Ersatzdaten sind deutlich gröber als ein DGM1: Ihre Datenbasis liegt bei rund
30 m Punktabstand, das Gelände wirkt entsprechend glatt. In einer Stichprobe über
sechs Punkte in Nordrhein-Westfalen wichen sie im Mittel −1,9 m vom amtlichen DGM1 ab
(Streuung 3,4 m, größte Abweichung 6,3 m). **An der Grenze zwischen beiden Quellen
kann daher eine Stufe von einigen Metern sichtbar sein.** Wie viele Punkte aus den
Ersatzdaten stammen, steht nach dem Erzeugen in der Statuszeile.

### Szene auf den Korridor zuschneiden

**Szene auf Korridor zuschneiden** entfernt alles außerhalb des Korridors: Flächen
werden aus den Meshes gelöscht, vollständig außerhalb liegende Objekte entfernt.
Die vom Addon erzeugten Objekte bleiben unangetastet.

Anschließend werden die verwendeten **Bildtexturen auf den verbleibenden Bereich
zugeschnitten** und die UV-Koordinaten entsprechend umgerechnet – sonst bliebe die
Textur des gesamten Areals in voller Auflösung in der Datei. Optional lässt sich eine
maximale Kantenlänge vorgeben. In einem Test schrumpfte eine 2048 × 2048-Textur eines
6 × 6 km großen Areals auf 378 × 1060 Pixel (90 % weniger).

### Gebietsgröße

Für die Streckensuche gibt es keine feste Obergrenze mehr: Gebiete über 0,25° Kantenlänge
werden automatisch in Teilabfragen zerlegt und wieder zusammengeführt. Gleise an den
Kachelgrenzen bleiben dabei vollständig erhalten. Erst jenseits von 200 Teilabfragen
lehnt das Addon ab, um den frei nutzbaren Kartendienst nicht zu überlasten.

## Gleisachsen

Das Panel **Gleisachsen** legt die Gleise der ausgewählten Strecken als **Splines**
an – je Gleis eine Poly-Kurve, ohne Schienen-, Schwellen- oder Schottergeometrie.
Einstellbar sind der Stützpunktabstand (Standard 10 m), die Linienstärke und ob alle
Gleise oder nur das längste durchgehende gebaut werden. Mit *Auf Gebiet zuschneiden*
enden die Achsen an der Gebietsgrenze.

Wie die Kilometrierungslinie lassen sich auch die Gleisachsen per Shrinkwrap auf dem
Gelände halten oder direkt auf dessen Höhe setzen.

### Weichen und Kreuzungen

Mit *Weichen als einzelne Objekte* wird jede einfache Weiche ein V-Mesh (Spitze und
zwei Schenkel, 3 Vertices, 2 Kanten), an dessen Enden die Gleise exakt anschließen.
Seit 1.25 gilt dasselbe für **Kreuzungen**: Ein OSM-Knoten mit vier Nachbarn, je zwei
zu einer Seite (Kreuzung, Kreuzungsweiche), wird zu **zwei V-Weichen, Spitze an
Spitze** (*Kreuzungen als zwei V-Weichen*). Verbindungen entstehen weiterhin nur über
gemeinsame OSM-Knoten, nie über geometrische Nähe – Brücken und Tunnel bleiben getrennt.

**Max. Abzweigwinkel** (Standard 10°, `RailNetworkConstants.MaxDivergingAngle`): Die
Schenkel einer Weiche stehen höchstens so weit auseinander und vom Stammgleis ab; an
einer Kreuzung gilt das für die Öffnung jedes V und jeden Übergang von einem V in das
gegenüberliegende.
Steilere Schenkel werden auf den Grenzwinkel gedreht, das Stammgleis bleibt, wie es ist.
Damit am Schenkelende kein neuer Knick entsteht, wird das anschließende Gleis auf der
**Übergangslänge** wieder in die OSM-Kante eingebogen – höchstens bis zum nächsten
OSM-Knoten, der selbst nie verschoben wird. Ein gedrehter Schenkel belegt dafür
höchstens die halbe Kante. 0° schaltet die Begrenzung ab.

**Einfache Kreuzungen begrenzen** (Standard an): Auch Kreuzungen ohne Weichenfunktion
(`railway=railway_crossing`) werden auf den Grenzwinkel gebracht; die Simulation lässt
dort dann auch das Abbiegen zu. Ausgeschaltet bleibt ihr Winkel erhalten – geradeaus
befahrbar, abbiegen nicht. Kreuzungen steiler als 45° werden nie verbogen.

Nach dem Erzeugen prüft das Addon alle Anschlüsse nach den Regeln der Simulation und
meldet die größte Abweichung. Die V-Meshes tragen `junction_kind` (Weiche,
Kreuzungsweiche, Kreuzung), `switch_opening_deg` und bei Kreuzungen `crossing_side`.

## Gebäude

Das Panel **Gebäude** erzeugt die Gebäudegrundrisse aus OpenStreetMap für das
eingestellte Gebiet als Baukörper – alle zusammen in einem Mesh, was bei mehreren
tausend Häusern deutlich schneller ist als einzelne Objekte. In Blender lassen sie
sich jederzeit trennen (*P* › *Nach losen Teilen*).

Die Bauhöhe kommt – in dieser Reihenfolge – aus:

1. `height` bzw. `building:height`,
2. `building:levels` × **Geschosshöhe** (zuzüglich `roof:height`, falls vorhanden),
3. der eingestellten **Vorgabehöhe**.

`min_height` und `building:min_level` werden als Unterkante berücksichtigt, etwa bei
Auskragungen. Wie viele Gebäude tatsächlich eine Höhenangabe tragen, meldet das Addon
nach dem Erzeugen.

| Einstellung | Wirkung |
|---|---|
| **Mindestfläche** | Grundrisse darunter werden übergangen (Carports, Gartenhäuser) |
| **Gebäudeteile einbeziehen** | Wertet zusätzlich `building:part` aus – genauer bei gegliederten Bauten, erzeugt aber überlappende Körper |
| **Nur im Korridor** | Übernimmt nur Gebäude, deren Schwerpunkt innerhalb der Korridorbreite um die Kilometrierungslinie liegt |
| **Auf Gelände setzen** | Stellt die Gebäude auf die Höhe des vorhandenen Geländes (Korridor- wie Flächengelände) |
| **Einbindetiefe** | So weit reicht die Grundfläche unter den tiefsten Geländepunkt, damit am Hang keine Lücke entsteht. Das Dach liegt entsprechend über dem höchsten Punkt |
| **Höchstzahl** | Obergrenze, damit ein zu großes Gebiet Blender nicht überlastet |

Der Abruf läuft im Hintergrund mit Fortschrittsanzeige, **Esc** bricht ab; die
Antworten werden wie die Bahndaten einen Tag lang zwischengespeichert. Große Gebiete
werden in Teilabfragen zerlegt (Gebäudedaten sind dichter als Gleisdaten, daher in
kleineren Kacheln als dort).

## Stützpunktabstand und Shrinkwrap

Ein Shrinkwrap verschiebt nur die **vorhandenen** Stützpunkte – zwischen ihnen bleibt
die Linie gerade. Die Rohdaten enthalten aber oft nur alle 50 bis 100 m einen Punkt,
sodass die Linie Kuppen durchschneiden und über Senken schweben würde. Deshalb wird
die Kilometrierungslinie standardmäßig mit **10 m** Punktabstand neu gestützt
(*Stützpunktabstand* im Abschnitt Kilometrierung, 0 = unverändert lassen).

Gemessen an der Südbahn, Sollhöhe 0,5 m über Gelände:

| Stützung | Höhe über Gelände (min / Median / max) |
|---|---|
| an den Stützpunkten | 0,50 / 0,50 / 0,50 m |
| 10 m (Voreinstellung), zwischen den Punkten | −0,24 / 0,50 / 0,95 m |
| 80 m (Rohdaten), zwischen den Punkten | −1,25 / 0,50 / 4,97 m |

Mit der feineren Stützung bleibt die Linie also auf gut ±0,5 m am Gelände statt auf
±4,5 m. Noch genauer wird es mit kleineren Werten – um den Preis von mehr Punkten
(22 km ergeben bei 10 m rund 2.200 Stützpunkte).

## Regelwerk: Ril 883.0010 „Bahnstrecken kilometrieren“

Die Erzeugung orientiert sich an der DB-Richtlinie 883.0010 (Gleis- und Bauvermessung,
Abschnitt „Bahnstrecken kilometrieren“). Was davon wie umgesetzt ist:

| Vorgabe | Umsetzung |
|---|---|
| **1 (1)** Jede Strecke hat eine eigene Kilometrierung in Richtung der Namensnennung | Richtung wird aus den Kilometrierungstafeln abgeleitet und die Linie stets in Richtung aufsteigender Kilometrierung orientiert. Im manuellen Modus über *Richtung umkehren* |
| **1 (2)** Streckennummer + Kilometer als eindeutige Ordnungsangabe | Gruppierung der Gleise nach Streckennummer (`ref`), je Strecke eine eigene Linie |
| **3 (1)** Linie folgt der **Gleisachse** (eingleisig), der **Streckenachse** (zweigleisig) oder **parallel zu einer Gleisachse** | Einstellung *Lage der Linie*: `Streckenachse (Ril 883)` — Standard —, `Gleisachse`, `Parallel zur Gleisachse`. Im Regelfall wandert die Linie dort in die Mitte, wo ein annähernd paralleles Nachbargleis in Reichweite liegt, und bleibt sonst auf der Gleisachse |
| **3 (1)** Keine Gleisachse darf die Kilometrierungslinie überschneiden | Wird nach dem Erzeugen geprüft und gemeldet; die Zahl steht als `chainage_track_crossings` an der Linie |
| **3 (2)** Ein seitlicher Sprung (Versatz) ist unzulässig | Der seitliche Versatz wird über die einstellbare *Übergangslänge* geglättet; es entstehen keine Sprünge |
| **3 (3)** Übergang ein-/zweigleisig etwa mittig zwischen den auseinanderstrebenden Gleisen | Dieselbe Überblendung (Standard 150 m); die Linie läuft mittig ein |
| **3 (4)** Kilometerangabe = rechtwinklige Projektion auf die Linie | Tafeln und Marken werden rechtwinklig auf die Linie projiziert (Lotfußpunkt je Segment) |
| **3 (4)** Schreibweise `13,4+23,05` | Beschriftungsformat `Ril 883`: volle Hektometer als `13,4`, Zwischenwerte als `13,4+23,05`. Negative Angaben werden wie in der Richtlinie geschrieben (`-6,1+-82,00`) |
| **4** Kilometrierungssprünge (Fehl-/Überlängen) | Quelle *Tafeln, stückweise* folgt den Tafeln abschnittsweise und bildet Sprünge damit ab. Die **Sonderschreibweise für Überlängen** (Meter über 100 hinaus ab dem nächstkleineren geraden Hektometer) ist **nicht** umgesetzt |
| **6 (1)** Hauptstrecken alle geraden Hektometer, Nebenstrecken alle 500 m | *Markenabstand: Nach Ril 883* wertet das OSM-Tag `usage` aus: `branch` → 500 m, sonst 100 m. Über *Fester Wert* frei einstellbar |

Nicht umgesetzt (außerhalb dessen, was sich aus OSM-Daten ableiten lässt): die
Bezugspunkte nach Abschnitt 2 (Mitte Empfangsgebäude, Bahnsteigmitten, Grenzpunkte
usw.) als Anfangs- und Endpunkte der Linie sowie die Dokumentation nach Abschnitt 5.
Anfang und Ende ergeben sich hier aus dem gewählten Gebiet.

Die Richtlinie ist eine Vorgabe für die Vermessung des Netzbetreibers. Dieses Addon
rekonstruiert die Kilometrierung aus frei verfügbaren OSM-Daten und erreicht damit die
Genauigkeit dieser Daten – es ersetzt keine Vermessungsunterlage.

## Genauigkeit

Die Tafeln sind in OSM typischerweise auf 10–30 m genau erfasst; diese Streuung wird
im Standardmodus ausgeglichen. Die maximale Abweichung der verwendeten Tafeln steht
nach dem Erzeugen in der Systemkonsole und als Eigenschaft an der Linie. Sind für eine
Strecke keine Tafeln vorhanden, wird – sofern aktiviert – ersatzweise beim Start-km
begonnen.

Sinnvolle Gebietsgrößen liegen bei wenigen Kilometern Kantenlänge; die Overpass-API
ist ein gemeinschaftlich genutzter Dienst und kann bei großen Abfragen zeitweise
überlastet sein (die Erweiterung wechselt dann automatisch auf einen anderen Server).

## Lizenz und Daten

Code: GPL-3.0-or-later. Die abgerufenen Geodaten – Bahndaten wie Gebäudegrundrisse –
stammen aus OpenStreetMap und stehen unter der
[ODbL](https://www.openstreetmap.org/copyright); bei einer Veröffentlichung ist
„© OpenStreetMap-Mitwirkende“ anzugeben. Die Quellenangabe der jeweils gewählten
Kartenkacheln meldet das Addon nach dem Laden der Textur.

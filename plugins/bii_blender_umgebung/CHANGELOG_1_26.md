# Umgebung 1.26.0 – Luftbilder von der eigenen Geo-Box

Basis: Umgebung-1.25.0; alle Funktionen bleiben enthalten.

## Änderungen

- Neue Kartenquelle **„Luftbilder (Geo-Box)“** unter *Kartentextur ▸ Karte*: Landes-
  Orthophotos (DE, AT, CH) von `geo.simbox.site` über `GET /osm/imagery`. Jede
  Web-Mercator-Kachel wird als WMS-Ausschnitt in EPSG:3857 abgefragt, vier gleichzeitig;
  Korridor- und Flächentextur funktionieren damit unverändert.
- Mindestens Kartenstufe 16: Die Geo-Box lehnt Ausschnitte über 4096 Quellpixel ab
  (20-cm-Orthophotos). Kacheln ohne Abdeckung (`404`) bleiben leer und werden gemeldet.
- Quellenangabe nach den Pflichten der Geo-Box: Datensätze aus `X-Imagery-Sources`,
  Namensnennung und Lizenz aus `/osm/meta/extent` – in der Meldung, am Kartenbild
  (`quelle`) und in der Szene (`kartenquelle`).
- Anmeldung per Service-Konto (Keycloak, Realm `simbox`, Client-Credentials mit
  HTTP-Basic wie in der Geo-Box-Doku). Client-ID (Vorgabe `simbox-geo-blender`) und
  Secret in den Add-on-Einstellungen, ersatzweise `SIMBOX_GEO_CLIENT_ID` /
  `SIMBOX_GEO_CLIENT_SECRET`. Token wird zwischengespeichert und vor Ablauf erneuert.
  Knopf „Verbindung testen“.
- Neue Voreinstellungen, weil der alte Server nicht mehr existiert: Overpass-Adresse
  `https://geo.simbox.site/overpass/api/interpreter` (mit Anmeldung), Kartenansicht
  `https://turbo.simbox.site/`. Die interne IP-Adresse ist aus dem Code entfernt.
- Robuster: leere oder unlesbare Kartenkacheln brechen das Zusammensetzen der Textur
  nicht mehr ab.
- Die Auswahlkarte im Browser zeigt bei Luftbildern basemap.de (der Browser hat keine
  Anmeldung) und nennt dafür nun die richtige Quelle.

## Einrichtung

1. Beim Betreiber ein Service-Konto für das Plugin anfordern (Vorbild `simbox-geo-cm`,
   Audience-Mapper `simbox-geo`).
2. *Bearbeiten ▸ Einstellungen ▸ Add-ons ▸ Umgebung*: Client-ID und Secret eintragen,
   „Verbindung testen“.
3. *Kartentextur ▸ Karte*: „Luftbilder (Geo-Box)“ wählen, „Kartentextur laden“.

## Prüfung

- Kachelgeometrie: Punkt-in-Kachel-Test für 16 Proben (DE, AT) fehlerfrei.
- Ohne Zugang: klare Meldung vor jedem Abruf (Kartentextur, Gebiet, Overpass).
- Gegen einen lokalen Ersatzserver mit derselben Schnittstelle (Token, WMS-Ausschnitt,
  `404`, `X-Imagery-Sources`, Katalog), der statt Luftbildern basemap.de-Kacheln
  lieferte: Flächentextur außerhalb der absichtlichen Lücken pixelgenau identisch mit
  der basemap.de-Textur; Korridortextur, Quellenangabe, Lückenmeldung, Overpass-Token
  und Verbindungstest geprüft.
- Nicht geprüft: Abruf echter Luftbilder – dafür fehlt noch das Service-Konto.

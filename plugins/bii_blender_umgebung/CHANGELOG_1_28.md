# Umgebung 1.28.0 – Eine Datenquelle für alles

Basis: Umgebung-1.27.1; alle Funktionen bleiben enthalten.

## Änderungen

- **Neues Panel „Datenquelle“** ganz oben im Reiter *Umgebung*. Es ist die einzige Stelle
  für Server und Zugang: Modus „Geo-Box“ oder „Öffentlich“, Server-Adresse, Anmelde-Adresse,
  Client-ID und Client-Secret. Dieselben Felder stehen auch in den Add-on-Einstellungen;
  beide zeigen dieselben Werte (gespeichert in den Blender-Einstellungen, nicht in der
  .blend-Datei).
- **Voreingestellt auf die Geo-Box**: `https://geo.simbox.site`, Anmeldung über
  `auth.bii-gmbh.com` (Realm `simbox`), Client-ID `simbox-geo-dev`. Nur das Client-Secret
  muss einmal eingetragen werden (oder Umgebungsvariable `SIMBOX_GEO_CLIENT_SECRET`).
- **Alle Abrufe folgen der Datenquelle.** Mit der Geo-Box laufen über sie:
  Streckensuche und Gebäude (Overpass), Gelände (Höhendienst `/osm/elevation/sample`:
  amtliche DGM für DE, AT, CH, sonst GLO-30), Kartentextur (Luftbilder oder die neue
  Basiskarte), Ortssuche und Karte in „Gebiet auswählen“ (über den lokalen Auswahl-Server,
  der den Token ergänzt) und „In Overpass Turbo ansehen“ (`/turbo/`).
- **Entfernt aus den einzelnen Panels**: Server-Wahl unter *Strecken Auswahl*, Feld
  „eigener Server“ unter *Kartentextur*, Adressfeld der Kartenansicht unter *Gebiet*. Die
  Panels zeigen stattdessen eine Zeile, woher die Daten kommen.
- **Zugang aus einer `.env`-Datei**: Feld „Zugangsdatei (.env)“ im Panel; leer = `.env` im
  Ordner des Addons. Gelesen werden `SIMBOX_GEO_CLIENT_SECRET`, `SIMBOX_GEO_CLIENT_ID`,
  `SIMBOX_GEO_URL`, `SIMBOX_GEO_TOKEN_URL` - die `.env.local` der simbox passt direkt.
  Werte aus der Datei haben Vorrang; die betroffenen Felder sind dann ausgegraut. Vorlage:
  `.env.example`. „Verbindung testen“ liest die Datei neu ein.
- **Overpass Turbo ohne „Unauthorized“**: „In Overpass Turbo ansehen“ öffnet Turbo der
  Geo-Box über einen lokalen Zugang in Blender (`http://127.0.0.1:8771/turbo/`). Er reicht
  Seite, Overpass-Abfragen, Basiskarte und Ortssuche mit dem Service-Konto weiter; keine
  Anmeldung im Browser. Nur lokal erreichbar, Anfragen fremder Webseiten werden abgewiesen.
  Läuft, solange Blender mit aktivem Addon offen ist.
- **BLOSM entfernt**: Schaltflächen „BLOSM-Karte“ und „Gebiet von BLOSM“ samt Funktionen.
  Für das Gebiet bleiben „Gebiet auswählen“ (Karte und Ortssuche der Geo-Box) und
  „Aus Zwischenablage“.
- **Kennung im Geo-Box-Protokoll**: Alle Abrufe an die Geo-Box tragen den User-Agent
  `Umgebung-Blender/<Version> (Blender add-on; simbox Geo-Box)` (über Turbo mit Zusatz
  „Turbo“). So sind Anfragen des Addons im Zugriffsprotokoll von anderen Nutzern des
  Kontos `simbox-geo-dev` zu unterscheiden.
- **Hinweistexte** in den Panels brechen nach der Breite der Seitenleiste um.
- **Verbindungstest** prüft jetzt Anmeldung, Luftbild-Katalog, Höhendienst, Basiskarte und
  Ortssuche; das Ergebnis steht im Panel.
- Ohne eingetragenes Secret oder außerhalb von DACH weicht die Streckensuche wie bisher auf
  die öffentlichen Overpass-Server aus.

## Prüfung

Blender 5.1.2 im Hintergrund gegen die echte Geo-Box (Würzburg Hbf, 1 × 1,4 km):
Verbindungstest, 7 Strecken, 6 Kilometrierungslinien, Gelände aus dem Geo-Box-Höhendienst,
Kartentextur mit Basiskarte und Luftbildern (Bayern DOP), 1638 Gebäude, Kachel und Ortssuche
über den Auswahl-Server; Umschalten auf „Öffentlich“ und Ändern der Server-Adresse wirken
auf alle Dienste. 26 von 26 Prüfungen bestanden.

# Umgebung 1.27.0 – Overpass über die Geo-Box

Basis: Umgebung-1.26.0; alle Funktionen bleiben enthalten. Abgeglichen mit der Anleitung
zur Overpass der simbox Geo-Box.

## Änderungen

- **Geo-Box als Standard für die Streckensuche** (neuer Modus „Geo-Box“ unter *Strecken
  Auswahl*, neben „Öffentlich“ und „Eigener Server“): `https://geo.simbox.site/overpass/
  api/interpreter`, ganz DACH. Ohne eingetragenen Zugang oder für Gebiete außerhalb von
  DACH nutzt das Addon die öffentlichen Server; die Meldung nennt die Quelle. Szenen, in
  denen ausdrücklich „Öffentlich“ oder „Eigener Server“ gewählt wurde, behalten das (feste
  Werte für die Auswahl); alle anderen nutzen ab jetzt die Geo-Box.
- **Große Gebiete als eine Abfrage**, wie vom Betreiber gewünscht – für Gleise und
  Gebäude. Öffentliche Server bekommen weiterhin Teilabfragen.
- **Token wie in der Anleitung**: Client-ID und Client-Secret im Formular
  (`grant_type=client_credentials`), Wiederverwendung bis 30 s vor Ablauf, bei `401`
  einmal ein neuer Token und eine Wiederholung.
- **`429`/`503`**: Wartezeit aus `Retry-After` (höchstens 60 s), bis zu drei Versuche – bei
  Overpass und Luftbildern.
- **Client-ID** voreingestellt auf `simbox-geo-dev`. Die Einstellungen weisen darauf hin,
  dass Service-Konto und Client-Secret gemeint sind, nicht der persönliche Login, und
  warnen, wenn die Client-ID wie eine E-Mail-Adresse aussieht.
- Overpass-Abfragen nutzen jetzt denselben TLS-Kontext wie die Kartenabrufe
  (Zertifikatsfix aus 1.24.1).
- Die Grenze von 200 Teilabfragen gilt nur noch für öffentliche Server.

## Prüfung

Gegen einen lokalen Ersatz der Geo-Box mit derselben Schnittstelle (Token nur mit
Formularfeldern, erste Overpass-Abfrage `401`, zweite `429` mit `Retry-After: 2`, echte
Overpass-Daten für Wörth):

- Streckensuche Wörth: 3 Strecken, 10 Kilometrierungstafeln, Quelle „Geo-Box“; `401` →
  neuer Token → `429` → nach 2 s erfolgreich; insgesamt 2 Token-Abrufe für alle Abfragen.
- 30 × 40 km um Würzburg (Beispiel der Anleitung): Gleise als 1 Abfrage (öffentlich 6),
  Gebäude als 1 Abfrage (öffentlich 96).
- Rückfall ohne Zugang und außerhalb von DACH: öffentliche Server mit Hinweis.
- Nicht geprüft: Abfrage gegen die echte Geo-Box – der eingetragene Zugang entspricht
  noch nicht der Anleitung (siehe Einstellungen).

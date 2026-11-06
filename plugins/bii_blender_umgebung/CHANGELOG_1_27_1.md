# Umgebung 1.27.1 – Streckensuche: klare Fehlermeldungen

Basis: Umgebung-1.27.0; alle Funktionen bleiben enthalten.

## Behoben

- **„Keine Daten erhalten“ ohne Grund**: Die Meldung unter *Strecken suchen* nennt jetzt
  die eigentliche Ursache, z. B. „Anmeldung an der Geo-Box abgelehnt (Client-ID …) -
  Client-ID und Secret prüfen“ oder den HTTP-Fehler des Servers.
- **Modus „Geo-Box“**: Lehnt die Geo-Box die Anmeldung ab, lädt das Addon die Strecken
  über die öffentlichen Overpass-Server; die Quellenangabe nennt den Grund. Im Modus
  „Eigener Server“ gibt es kein Ausweichen, die Meldung zeigt den Fehler.
- **Neu laden nach Ausweichen**: Kamen die Daten zuletzt von den öffentlichen Servern,
  lädt „Strecken suchen“ neu, statt den alten Stand wiederzuverwenden. Nach einem
  korrigierten Zugang wird also wieder die Geo-Box gefragt.
- **Hinweis bei falscher Client-ID**: Steht in den Add-on-Einstellungen eine E-Mail-Adresse
  als Client-ID, weisen die Einstellungen und *Strecken Auswahl* darauf hin; laut
  Anleitung lautet sie `simbox-geo-dev`.
